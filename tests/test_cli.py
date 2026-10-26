"""`peach` 顶层 CLI 的可达性与只读保证。

这里管的是 `serve`/`migrate`/`status`/`ledger-sync`
这一层，以及打包后的 EXE 到底能不能调到它们——判据见 `build_app_entry.py`。
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from unittest import mock

from peach import cli, onboarding, settings_file
from peach.migrations import plan, upgrade

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

HAS_HTTP_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
NATIVE_WINDOWS = os.name == "nt"


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StatusCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "ledger.db"
        self.shared = self.root / "shared" / "ledger.db"
        self.state = self.root / "state"
        upgrade(self.db, MIGRATIONS)
        connection = sqlite3.connect(self.db)
        connection.executemany(
            "INSERT INTO asset(location,path,name,medium,size,duration,snapshot_path,"
            "creator,ctx_length) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ("local", r"R:\media\a.mp4", "a.mp4", "video", 2 << 30, 1800.0,
                 r"R:\snap\a.jpg", "Someone", "中"),
                ("local", r"R:\media\b.mp4", "b.mp4", "video", 1 << 30, None, None,
                 None, None),
                ("online", "https://example.test/x", "x", "account", None, None, None,
                 "Handle", None),
            ])
        connection.commit()
        connection.close()

    def _run(self, argv):
        output = io.StringIO()
        with mock.patch.object(cli, "STATE_DIR", self.state), redirect_stdout(output):
            code = cli.main(argv)
        return code, output.getvalue()

    def test_status_reports_ledger_migration_and_sync_state(self):
        code, output = self._run(["status", "--db", str(self.db),
                                  "--shared-db", str(self.shared)])
        self.assertEqual(code, 0)
        self.assertIn("合计", output)
        self.assertIn("加工进度（本机视频 2 条）", output)
        # 在线资产不进「加工进度」的分母，否则百分比永远到不了 100。
        self.assertIn("有时长 (ffprobe)", output)
        self.assertIn("1 / 2", output)
        self.assertIn("待应用 0 个", output)
        self.assertIn("同步：", output)

    def test_status_never_writes_the_ledger_or_invents_a_device_id(self):
        before = self.db.stat().st_mtime_ns, self.db.stat().st_size
        code, _ = self._run(["status", "--db", str(self.db),
                             "--shared-db", str(self.shared)])
        self.assertEqual(code, 0)
        self.assertEqual((self.db.stat().st_mtime_ns, self.db.stat().st_size), before)
        self.assertFalse((self.state / "device-id").exists(),
                         "状态命令不该顺手生成一个从没写过库的写入端标识")

    def test_missing_ledger_is_reported_rather_than_created(self):
        missing = self.root / "nope.db"
        code, output = self._run(["status", "--db", str(missing),
                                  "--shared-db", str(self.shared)])
        self.assertEqual(code, 4)
        self.assertIn("数据库不存在", output)
        self.assertFalse(missing.exists())


class InitCommandTests(unittest.TestCase):
    """`peach init`：一台全新机器从零到能跑的唯一入口。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "peach-data"
        # 证书生成要调 openssl；那是 `test_certs.py` 的事，这里只验初始化流程。
        patcher = mock.patch.object(cli.onboarding.certs, "bootstrap_certificates")
        self.certs = patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(argv)
        return code, output.getvalue()

    def _init(self, *extra):
        return self._run(["init", "--data-root", str(self.root), *extra])

    def test_fresh_init_builds_a_runnable_data_root(self):
        code, output = self._init()
        self.assertEqual(code, 0)
        for key in settings_file.DIRECTORY_KEYS:
            self.assertTrue((self.root / key).is_dir(), key)
        database = self.root / "database" / "ledger.db"
        self.assertTrue(database.is_file())
        # 建完就必须是最新 schema，否则第一次 serve 就撞上待应用迁移。
        self.assertEqual(plan(database, MIGRATIONS)[1], [])
        self.assertIn("下一步", output)
        self.certs.assert_called_once()

    def test_fresh_init_output_is_readable_by_the_settings_layer(self):
        self._init("--mdns-name", "peach-two", "--port", "9443",
                   "--mount", "local=/mnt/media")
        loaded = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
        self.assertTrue(loaded.present)
        self.assertTrue(loaded.configured)
        self.assertEqual(loaded.server.mdns_name, "peach-two")
        self.assertEqual(loaded.server.port, 9443)
        self.assertEqual(loaded.mounts, {"local": ("/mnt/media",)})
        # 全新机器不开复制：单机用户不该凭空多出一条同步路径。
        self.assertFalse(loaded.replication.enabled)

    def test_mount_for_an_undeclared_location_is_refused(self):
        """打错来源 ID 静默收下，那个来源就会安静地进脱盘模式。"""
        with self.assertRaises(SystemExit) as caught:
            self._init("--mount", "locla=/mnt/media")
        self.assertIn("locla", str(caught.exception))
        self.assertIn("local", str(caught.exception))

    def test_mount_without_an_equals_sign_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self._init("--mount", "/mnt/media")
        self.assertIn("来源ID=路径", str(caught.exception))

    def test_mount_ids_keep_their_case(self):
        """来源 ID 不是盘符，`local` 不能被改写成 `LOCAL`。"""
        self.assertEqual(
            cli._parse_mounts(["local=/mnt/res"], {"local": (r"R:\media",)}),
            {"local": ("/mnt/res",)})
        # 同一来源的多个媒体目录，按顺序对应数据库路径前缀。
        self.assertEqual(
            cli._parse_mounts(["local=/mnt/a", "local=/mnt/b"], {"local": (r"R:\media", r"R:\media2")}),
            {"local": ("/mnt/a", "/mnt/b")})

    def test_the_new_ledger_is_readable_by_status(self):
        self._init()
        code, output = self._run(
            ["status", "--db", str(self.root / "database" / "ledger.db"),
             "--shared-db", str(self.root / "nope.db")])
        self.assertEqual(code, 0)
        self.assertIn("待应用 0 个", output)

    def test_existing_settings_file_is_not_overwritten_without_force(self):
        self._init()
        stamp = (self.root / "config.toml").read_text(encoding="utf-8")
        code, output = self._init("--mdns-name", "changed")
        self.assertEqual(code, 3)
        self.assertIn("已存在", output)
        self.assertEqual((self.root / "config.toml").read_text(encoding="utf-8"), stamp)
        self.assertEqual(self._init("--mdns-name", "changed", "--force")[0], 0)
        self.assertNotEqual(
            (self.root / "config.toml").read_text(encoding="utf-8"), stamp)

    def test_from_existing_only_writes_the_settings_file(self):
        code, _ = self._init("--from-existing", "--smb-host", "peach-writer.local",
                             "--smb-user", "someone")
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "database").exists())
        self.assertFalse((self.root / "generated").exists())
        self.certs.assert_not_called()
        loaded = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
        # 现有部署本来就在复制；写一份设置文件不该把它悄悄关掉。
        self.assertTrue(loaded.replication.enabled)
        self.assertEqual(loaded.replication.smb_host, "peach-writer.local")
        self.assertEqual(loaded.replication.smb_user, "someone")

    def test_from_existing_says_so_when_the_old_file_is_unreadable(self):
        """升级路径上最贵的一次静默：第一阶段的 `[media] R = ...` 现在是硬错误。

        `--from-existing` 声称落盘的是「当前生效的配置」，而旧文件读不出来时那份配置
        其实是内建默认。不说这一句，重写出来的文件会安静地丢掉 mDNS 名和 SMB 坐标。
        """
        self.root.mkdir(parents=True)
        (self.root / "config.toml").write_text(
            "[media]\nR = '/Volumes/Media'\n", encoding="utf-8")
        code, output = self._init("--from-existing", "--force")
        self.assertEqual(code, 0)
        self.assertIn("media.mounts", output)      # 原始错误里的升级提示
        self.assertIn("没有**继承", output)
        loaded = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
        self.assertEqual(loaded.mounts, {})

    def test_blank_coordinates_are_listed_instead_of_guessed(self):
        _, output = self._init("--from-existing")
        self.assertIn("replication.smb_host", output)
        self.assertIn("--smb-host", output)

    def test_an_existing_ledger_is_never_rebuilt_by_a_fresh_init(self):
        database = self.root / "database" / "ledger.db"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"not really a database")
        code, output = self._init()
        self.assertEqual(code, 0)
        self.assertEqual(database.read_bytes(), b"not really a database")
        self.assertIn("--from-existing", output)


class InteractiveInitTests(unittest.TestCase):
    """`peach init` 不带参数、stdin 是终端时的问答流。

    答案一律脚本化。两种形态各有一条专属用例，其余与形态无关的用例按 `NATIVE_WINDOWS`
    走本机形态：媒体目录题只收真实存在的目录，而目录是不是盘符路径由测试机决定，
    在 POSIX 上硬填 `windows=True` 会被媒体目录校验直接拒掉。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.data_root = self.root / "peach-data"
        self.home = self.root / "home"
        self.home.mkdir()
        self.media = self.root / "media"
        (self.media / "sub").mkdir(parents=True)
        (self.media / "sub" / "a.mp4").write_bytes(b"0" * 10)
        (self.media / "b.jpg").write_bytes(b"1")
        patcher = mock.patch.object(cli.onboarding.certs, "bootstrap_certificates")
        self.certs = patcher.start()
        self.addCleanup(patcher.stop)
        # 第一题的默认值要落在临时目录里，回车接受默认才不会碰真实 Peach 数据目录。
        env = mock.patch.dict(os.environ, {"PEACH_DATA_ROOT": str(self.data_root)})
        env.start()
        self.addCleanup(env.stop)

    def _run(self, answers, *, windows):
        queue = list(answers)
        prompts: list[str] = []

        def ask(prompt: str, default: str) -> str:
            prompts.append(prompt)
            return queue.pop(0)

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli._init_interactive(ask, windows=windows, home=self.home)
        return code, output.getvalue(), prompts

    def _ledger_paths(self):
        connection = sqlite3.connect(self.data_root / "database" / "ledger.db")
        try:
            return sorted(row[0] for row in connection.execute("SELECT path FROM asset"))
        finally:
            connection.close()

    def _loaded(self):
        return settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.data_root)})

    @unittest.skipUnless(NATIVE_WINDOWS, "本用例需要真实存在的 Windows 盘符目录")
    def test_windows_flow_declares_the_directory_and_scans_it(self):
        second = self.root / "more"
        second.mkdir()
        (second / "c.mp4").write_bytes(b"2")
        code, output, prompts = self._run(
            ["", str(self.media), str(second), "", "", "", "", ""], windows=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(prompts), 8)
        self.assertEqual(prompts[2], prompts[3], "追加目录问到回车为止")
        self.assertTrue(prompts[-1].startswith("现在扫描 这 2 个文件夹"))
        loaded = self._loaded()
        self.assertTrue(loaded.present)
        self.assertEqual(loaded.locations, {"local": (str(self.media), str(second))})
        self.assertEqual(loaded.mounts, {})
        self.assertEqual((loaded.server.host, loaded.server.port, loaded.server.mdns_name),
                         ("0.0.0.0", 8900, "peach"))
        self.assertFalse(loaded.replication.enabled)
        for key in settings_file.DIRECTORY_KEYS:
            self.assertTrue((self.data_root / key).is_dir(), key)
        self.certs.assert_called_once()
        # 账本里的路径就是本机路径，每一行都指向真实文件。
        self.assertEqual(self._ledger_paths(),
                         sorted([str(self.media / "b.jpg"), str(self.media / "sub" / "a.mp4"),
                                 str(second / "c.mp4")]))
        self.assertIn("✓ local: 2 文件", output)
        self.assertIn("✓ local: 1 文件", output)
        self.assertIn("下一步", output)
        self.assertEqual(output.count("扫描结果："), 2, "每个目录各报一行")
        self.assertNotIn("115", loaded.locations)

    def test_posix_flow_keeps_the_ledger_shape_and_mounts_the_directory(self):
        code, output, _ = self._run(["", str(self.media), "", "2", "9443", "peach-two", ""],
                                    windows=False)
        self.assertEqual(code, 0)
        loaded = self._loaded()
        self.assertEqual(loaded.locations, {"local": (r"R:\media",)})
        self.assertEqual(loaded.mounts, {"local": (str(self.media),)})
        self.assertEqual((loaded.server.host, loaded.server.port, loaded.server.mdns_name),
                         ("0.0.0.0", 9443, "peach-two"))
        self.assertIn("媒体目录", output)
        paths = self._ledger_paths()
        self.assertEqual(paths, [r"R:\media\b.jpg", r"R:\media\sub\a.mp4"])
        # 读取文件时，将数据库路径前缀之后的层级拼到本机媒体目录。
        for path in paths:
            tail = PureWindowsPath(path).relative_to(PureWindowsPath(r"R:\media")).parts
            self.assertTrue(Path(loaded.mounts["local"][0]).joinpath(*tail).is_file(), path)

    def test_an_invalid_media_directory_is_asked_again(self):
        missing = self.root / "nope"
        code, output, prompts = self._run(
            ["", str(missing), str(self.media), "", "", "", "", "n"], windows=NATIVE_WINDOWS)
        self.assertEqual(code, 0)
        self.assertEqual(prompts.count("媒体目录（必须已经存在，可以在外置硬盘上）"), 2)
        self.assertIn("目录不存在", output)
        self.assertFalse(missing.exists(), "问答不替人建媒体目录")

    def test_three_bad_answers_abort_before_anything_is_written(self):
        with self.assertRaises(SystemExit) as caught:
            self._run(["", "/nope1", "/nope2", "/nope3"], windows=NATIVE_WINDOWS)
        self.assertIn("3 次", str(caught.exception))
        self.assertIn("没有写任何文件", str(caught.exception))
        self.assertFalse(self.data_root.exists())

    def test_the_scan_can_be_declined(self):
        code, output, _ = self._run(["", str(self.media), "", "", "", "", "n"], windows=NATIVE_WINDOWS)
        self.assertEqual(code, 0)
        self.assertEqual(self._ledger_paths(), [])
        self.assertNotIn("扫描结果", output)
        self.assertIn("下一步", output)

    def test_an_existing_settings_file_is_not_overwritten(self):
        self.data_root.mkdir()
        (self.data_root / "config.toml").write_text("[server]\nport = 9100\n", encoding="utf-8")
        code, output, _ = self._run(["", str(self.media), "", "", ""], windows=NATIVE_WINDOWS)
        self.assertEqual(code, 3)
        self.assertIn("已存在", output)
        self.assertEqual((self.data_root / "config.toml").read_text(encoding="utf-8"),
                         "[server]\nport = 9100\n")

    @unittest.skipUnless(HAS_HTTP_DEPS, "需要 fastapi 与 httpx")
    def test_a_config_declaring_only_local_serves_health_items_and_sources(self):
        """只声明 `local` 的设置文件要能直接起服务：没有 115/pikpak 不是错误。"""
        import asyncio

        import httpx

        from peach import web_resource_sync
        from peach.api import create_app
        from peach.config import PeachSettings
        from peach.platform import translate_roots

        self._run(["", str(self.media), "", "", "", "", ""], windows=NATIVE_WINDOWS)
        loaded = self._loaded()
        # POSIX 系统也用 `R:\media` 作为数据库路径前缀，按 [media.mounts] 转换为本机目录；
        # 模块级 `active()` 指的是这台机器自己的设置，临时目录不在里面。
        active = mock.patch.object(settings_file, "active", lambda: loaded)
        active.start()
        self.addCleanup(active.stop)
        settings = PeachSettings(
            db_path=loaded.directory("database") / "ledger.db", configured=True, token="secret",
            allowed_media_roots=translate_roots(tuple(root for roots in loaded.locations.values() for root in roots)),
        )

        async def probe():
            app = create_app(settings)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                health = await client.get("/healthz")
                items = await client.get("/api/items?limit=10", headers={"Cookie": "tok=secret"})
                sources = await client.get("/api/sources", headers={"Cookie": "tok=secret"})
                return health, items, sources

        with mock.patch("peach.config.LOCATION_ROOT_DECLARATIONS", dict(loaded.locations)), \
                mock.patch.object(web_resource_sync, "LOCATION_ROOT_DECLARATIONS",
                                  dict(loaded.locations)):
            health, items, sources = asyncio.run(probe())
            # 两个闸门断言必须留在 patch 里：出了 with 就换回模块默认声明，
            # 那份声明带着 115/pikpak，在真挂上它们的机器上 source_is_online 会返回 True。
            self.assertTrue(web_resource_sync.source_is_online("local"))
            self.assertFalse(web_resource_sync.source_is_online("115"))
        self.assertEqual(health.status_code, 200)
        self.assertEqual(items.status_code, 200)
        self.assertGreaterEqual(items.json()["total"], 1, "扫进去的本地资产要能列出来")
        self.assertEqual(sources.status_code, 200)
        listed = [row["location"] for row in sources.json()["sources"]]
        self.assertIn("local", listed)
        self.assertNotIn("115", listed)
        self.assertNotIn("pikpak", listed)


class InitDispatchTests(unittest.TestCase):
    """问答只在「不带任何参数且 stdin 是终端」时进入；其余一律走非交互路径。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "peach-data"
        patcher = mock.patch.object(cli, "_init_interactive", return_value=0)
        self.interactive = patcher.start()
        self.addCleanup(patcher.stop)
        certs = mock.patch.object(cli.onboarding.certs, "bootstrap_certificates")
        certs.start()
        self.addCleanup(certs.stop)

    def _run(self, argv, *, tty):
        with mock.patch.object(onboarding, "is_interactive", return_value=tty), \
                redirect_stdout(io.StringIO()):
            return cli.main(argv)

    def test_bare_init_on_a_terminal_enters_the_interview(self):
        self.assertEqual(self._run(["init"], tty=True), 0)
        self.interactive.assert_called_once_with()

    def test_bare_init_without_a_terminal_stays_non_interactive(self):
        with mock.patch.dict(os.environ, {"PEACH_DATA_ROOT": str(self.root)}):
            self.assertEqual(self._run(["init"], tty=False), 0)
        self.interactive.assert_not_called()
        self.assertTrue((self.root / "config.toml").is_file())

    def test_any_argument_or_no_input_stays_non_interactive(self):
        self.assertEqual(self._run(["init", "--data-root", str(self.root)], tty=True), 0)
        self.assertEqual(
            self._run(["init", "--data-root", str(self.root), "--no-input", "--force"], tty=True), 0)
        self.interactive.assert_not_called()
        loaded = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
        # 非交互设置写出三个来源的默认数据库路径前缀。
        self.assertEqual(set(loaded.locations), {"local", "115", "pikpak"})


class ScanCommandTests(unittest.TestCase):
    """`peach scan <来源ID> [根目录]`：目录省略时取配置中的数据库路径前缀。

    `cli._scan` 不带 `windows=`，走的就是测试机的形态，所以设置也按本机形态搭：Windows 上
    数据库路径前缀就是真实目录、挂载表为空；POSIX 系统使用 `R:\\media`，挂载表指向真实目录。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "ledger.db"
        upgrade(self.db, MIGRATIONS)
        self.media = self.root / "media"
        (self.media / "sub").mkdir(parents=True)
        (self.media / "sub" / "a.mp4").write_bytes(b"0")
        (self.media / "b.jpg").write_bytes(b"1")
        config = settings_file.load_config(environ={}, strict=False)
        declared = str(self.media) if NATIVE_WINDOWS else r"R:\media"
        mounts = {} if NATIVE_WINDOWS else {"local": (Path(self.media),)}
        fixed = replace(config, locations={"local": (declared,)},
                        mounts={key: tuple(str(item) for item in value)
                                for key, value in mounts.items()})
        for target in (mock.patch.object(settings_file, "active", lambda: fixed),
                       mock.patch.object(cli, "location_mounts", lambda: mounts),
                       mock.patch.object(cli, "SETTINGS_ERROR", None)):
            target.start()
            self.addCleanup(target.stop)

    def _run(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(argv)
        return code, output.getvalue()

    def _count(self):
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute("SELECT COUNT(*) FROM asset").fetchone()[0]
        finally:
            connection.close()

    def test_root_defaults_to_the_declared_root(self):
        code, output = self._run(["scan", "local", "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertIn("✓ local: 2 文件", output)
        self.assertEqual(self._count(), 2)

    def test_configured_scan_uses_each_online_source_id(self):
        fixed = replace(settings_file.active(), locations={'115': ('B:/',), 'pikpak': ('A:/',)})
        with mock.patch.object(settings_file, 'active', return_value=fixed), \
             mock.patch('peach.platform.root_online', side_effect=[False, True]), \
             mock.patch.object(cli.scan, 'scan_location') as scanner:
            code, output = self._run(['scan', 'configured', '--db', str(self.db)])
        self.assertEqual(code, 0)
        self.assertIn('115', output)
        self.assertEqual(scanner.call_count, 1)
        self.assertEqual(scanner.call_args.args[1:3], ('pikpak', 'A:/'))

    def test_an_explicit_subdirectory_is_scanned_alone(self):
        code, _ = self._run(["scan", "local", str(self.media / "sub"), "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertEqual(self._count(), 1)

    def test_an_undeclared_source_is_refused_and_lists_the_known_ones(self):
        with self.assertRaises(SystemExit) as caught:
            self._run(["scan", "nas", "--db", str(self.db)])
        self.assertIn("nas", str(caught.exception))
        self.assertIn("local", str(caught.exception))
        self.assertEqual(self._count(), 0)

    def test_a_missing_ledger_points_at_init(self):
        code, output = self._run(["scan", "local", "--db", str(self.root / "nope.db")])
        self.assertEqual(code, 4)
        self.assertIn("peach init", output)


class ProcessCommandTests(unittest.TestCase):
    """`peach process` 和网页那一轮同一个处理函数，结算也要一样：声明的后继进任务中心。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, MIGRATIONS)
        config = settings_file.load_config(environ={}, strict=False)
        for target in (mock.patch.object(settings_file, "active", lambda: config),
                       mock.patch.object(cli, "SETTINGS_ERROR", None)):
            target.start()
            self.addCleanup(target.stop)

    def _run(self, result):
        output = io.StringIO()
        with mock.patch("peach.library_processing.process_library", return_value=result), \
                redirect_stdout(output):
            code = cli.main(["process", "--db", str(self.db)])
        connection = sqlite3.connect(self.db)
        try:
            rows = connection.execute(
                "SELECT id,task_key,trigger,status,parent_run_id,followup_key FROM task_run "
                "ORDER BY id").fetchall()
        finally:
            connection.close()
        return code, output.getvalue(), rows

    def test_the_followups_a_run_declares_are_queued_under_it(self):
        declared = [{"key": "performer-alias:9378", "task_key": "performer-alias",
                     "label": "补女优别名：白川麻衣"}]
        code, output, rows = self._run({"status": "complete", "followups": declared})
        self.assertEqual(code, 0)
        parent, child = rows
        self.assertEqual(parent[1:4], ("library-processing", "cli", "succeeded"))
        self.assertEqual(child[1:], ("performer-alias", "cli", "pending", parent[0],
                                     "performer-alias:9378"))
        self.assertIn("入队 1 条", output)

    def test_an_unfinished_run_is_recorded_as_failed_and_still_queues_its_followups(self):
        declared = [{"key": "performer-alias:1", "task_key": "performer-alias", "label": ""}]
        code, _, rows = self._run({"status": "failed", "error": "网盘离线",
                                   "followups": declared})
        self.assertEqual(code, 1)
        self.assertEqual(rows[0][3], "failed")
        self.assertEqual(rows[1][3], "pending")


class ReplicationAssemblyTests(unittest.TestCase):
    """ADR-0023 第 3 阶段：整条复制链路按 `replication.enabled` 装配或完全不装配。"""

    def _build(self, enabled):
        args = argparse.Namespace(shared_db=Path("/nonexistent/shared.db"))
        settings = mock.Mock(db_path=Path("/nonexistent/local.db"))
        output = io.StringIO()
        with mock.patch.object(cli, "REPLICATION_ENABLED", enabled), \
                mock.patch.object(cli, "LedgerSync") as ledger_sync, \
                redirect_stdout(output):
            sync = cli._build_sync(args, settings)
        return sync, ledger_sync, output.getvalue()

    def test_replication_off_builds_no_observer_and_probes_nothing(self):
        """没有第二台机器就没有「读者」：服务按独立写者跑，写接口照常开。"""
        sync, ledger_sync, output = self._build(False)
        self.assertIsNone(sync)
        ledger_sync.assert_not_called()
        self.assertIn("disabled", output)

    def test_replication_on_keeps_todays_behaviour(self):
        sync, ledger_sync, _ = self._build(True)
        ledger_sync.assert_called_once()
        ledger_sync.return_value.observe.assert_called_once()
        self.assertIs(sync, ledger_sync.return_value)


class BrokenSettingsTests(unittest.TestCase):
    """设置文件读不出来时，命令必须响亮地失败，而不是拿默认值跑出个假状态。"""

    def test_commands_refuse_to_run_and_name_the_file(self):
        broken = settings_file.SettingsFileError(
            "设置文件读不出来：/tmp/peach-data/config.toml：TOML 语法错误")
        with mock.patch.object(cli, "SETTINGS_ERROR", broken):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["status", "--db", "/tmp/nope.db"])
        message = str(caught.exception)
        self.assertIn("config.toml", message)
        self.assertIn("peach init --force", message)


class PackagedEntryTests(unittest.TestCase):
    """打包后的 EXE 必须能调到 `peach.cli` 的每一个子命令。

    判据硬编码成 `{"serve", "migrate"}` 的话，`ledger-sync` 和后来加的
    `status` 在 EXE 里全部不可达——参数被当成托盘参数吞掉，既不报错也不执行。
    """

    @classmethod
    def setUpClass(cls):
        cls.entry = load_script("build_app_entry")

    def setUp(self):
        self.enterContext(mock.patch("peach.error_reporting.install"))
        self.enterContext(mock.patch("peach.error_reporting.event"))
        self.enterContext(mock.patch("peach.error_reporting.console_ready"))

    def test_every_cli_subcommand_is_reachable_from_the_packaged_entry(self):
        subcommands = set()
        for action in cli.build_parser()._subparsers._group_actions:
            subcommands.update(action.choices)
        self.assertIn("status", subcommands)
        self.assertEqual(subcommands, self.entry.cli_commands())

    def test_windows_redirected_console_accepts_chinese_startup_messages(self):
        output = io.BytesIO()
        stream = io.TextIOWrapper(output, encoding="cp1252", newline="\n")
        with mock.patch.object(self.entry.os, "name", "nt"), mock.patch.object(
            self.entry.sys, "stdout", stream
        ), mock.patch.object(self.entry.sys, "stderr", stream):
            self.entry._prepare_console()
            print("服务已启动", flush=True)
        self.assertEqual(output.getvalue(), "服务已启动\n".encode("utf-8"))

    def test_tray_still_starts_when_the_first_argument_is_not_a_subcommand(self):
        self.assertFalse(self.entry.wants_cli(["peach.exe"]))
        self.assertFalse(self.entry.wants_cli(["peach.exe", "--tray-only"]))
        self.assertTrue(self.entry.wants_cli(["peach.exe", "status"]))
        self.assertTrue(self.entry.wants_cli(["peach.exe", "ledger-sync"]))

    def test_installer_hooks_reach_the_installer_handoff_not_the_tray(self):
        with mock.patch("peach.desktop_installer.main", return_value=0) as handoff, \
                mock.patch.object(self.entry, "tray_main") as tray:
            self.assertEqual(self.entry.main(["peach.exe", "--installer-uninstall"]), 0)
        handoff.assert_called_once_with("--installer-uninstall")
        tray.assert_not_called()

    def test_packaged_command_failure_returns_without_a_native_error_dialog(self):
        output = io.StringIO()
        with mock.patch.object(self.entry, "cli_main", side_effect=RuntimeError("migration failed")), mock.patch.object(
            self.entry, "_prepare_console"
        ), mock.patch.object(self.entry.sys, "stderr", output):
            self.assertEqual(self.entry.main(["peach.exe", "migrate", "upgrade", "--yes"]), 1)
        self.assertIn("migration failed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
