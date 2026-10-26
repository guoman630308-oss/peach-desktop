"""`peach.onboarding`：首次运行问答的题目、校验与落盘，纯逻辑、可注入。

CLI 与将来的托盘设置页共用这一层，所以这里只用脚本化答案驱动，不碰 stdin；平台由
`windows=` 注入。只有一处例外：媒体目录题要收一个真实存在的目录，而那个目录是不是盘符
路径由测试机决定，所以走到这道题的用例按 `NATIVE_WINDOWS` 用本机形态。
"""
from __future__ import annotations

import importlib.util
import io
import ntpath
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from peach import distribution, onboarding, settings_file

NATIVE_WINDOWS = os.name == "nt"
HAS_HTTP_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))


def scripted(*answers: str):
    """按顺序吐答案，并把每一题的题目与默认值记下来。"""
    queue = list(answers)
    seen: list[tuple[str, str]] = []

    def ask(prompt: str, default: str) -> str:
        seen.append((prompt, default))
        return queue.pop(0)

    ask.seen = seen  # type: ignore[attr-defined]
    return ask


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.media = self.root / "media"
        self.media.mkdir()
        self.config = settings_file.load_config(
            project_root=self.root / "app", environ={"PEACH_DATA_ROOT": str(self.root / "peach-data")})


class QuestionTests(_Case):
    def test_questions_come_in_a_fixed_order_with_defaults_from_the_config(self):
        asked = onboarding.questions(self.config, windows=True, home=self.home)
        self.assertEqual([q.key for q in asked],
                         ["data_root", "media_dir", "host", "port", "mdns_name"])
        by_key = {q.key: q for q in asked}
        self.assertEqual(by_key["data_root"].default, str(self.root / "peach-data"))
        self.assertEqual(by_key["host"].default, "2")
        self.assertEqual(by_key["port"].default, "8900")
        self.assertEqual(by_key["mdns_name"].default, "peach")

    def test_media_default_is_the_system_video_folder_only_when_it_exists(self):
        self.assertIsNone(onboarding.default_media_dir(self.home, windows=True))
        (self.home / "Videos").mkdir()
        self.assertEqual(onboarding.default_media_dir(self.home, windows=True),
                         self.home / "Videos")
        self.assertIsNone(onboarding.default_media_dir(self.home, windows=False))
        (self.home / "Movies").mkdir()
        self.assertEqual(onboarding.default_media_dir(self.home, windows=False),
                         self.home / "Movies")
        asked = {q.key: q for q in onboarding.questions(self.config, windows=False, home=self.home)}
        self.assertEqual(asked["media_dir"].default, str(self.home / "Movies"))


class ValidatorTests(_Case):
    def test_windows_mapped_directory_keeps_its_drive_path(self):
        mapped = r"Z:\CloudDrive2\115open\云下载"
        redirected = Path(r"\\NAS\CloudDrive2\115open\云下载")
        windows_abspath = ntpath.abspath
        with mock.patch.object(Path, "is_dir", return_value=True), \
                mock.patch.object(Path, "resolve", return_value=redirected), \
                mock.patch("peach.onboarding.os.path.abspath", side_effect=windows_abspath):
            validate = onboarding.media_dir_validator(windows=True)
            for raw in (mapped, mapped + r"\子目录\.."):
                with self.subTest(raw=raw):
                    self.assertEqual(validate(raw), Path(mapped))

    def test_windows_unc_directory_requires_a_drive_mapping(self):
        shared = r"\\NAS\媒体库"
        windows_abspath = ntpath.abspath
        with mock.patch.object(Path, "is_dir", return_value=True), \
                mock.patch.object(Path, "resolve", return_value=Path(shared)), \
                mock.patch("peach.onboarding.os.path.abspath", side_effect=windows_abspath):
            with self.assertRaisesRegex(ValueError, "不是盘符路径"):
                onboarding.media_dir_validator(windows=True)(shared)

    def test_posix_media_directory_uses_its_filesystem_target(self):
        with mock.patch.object(Path, "resolve", return_value=self.media):
            self.assertEqual(onboarding.media_dir_validator(windows=False)(str(self.home)), self.media)

    def test_media_dir_must_already_exist_and_is_never_created(self):
        validate = onboarding.media_dir_validator(windows=False)
        missing = self.root / "nope"
        with self.assertRaises(ValueError) as caught:
            validate(str(missing))
        self.assertIn("不存在", str(caught.exception))
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            validate("")
        self.assertEqual(validate(str(self.media)), self.media)

    def test_host_accepts_the_two_choices_in_several_spellings(self):
        for raw in ("1", "本机", "127.0.0.1"):
            self.assertEqual(onboarding.validate_host(raw), "127.0.0.1")
        for raw in ("2", "局域网", "0.0.0.0"):
            self.assertEqual(onboarding.validate_host(raw), "0.0.0.0")
        with self.assertRaises(ValueError):
            onboarding.validate_host("3")

    def test_port_is_an_integer_in_range(self):
        self.assertEqual(onboarding.validate_port(" 8900 "), 8900)
        for raw in ("0", "65536", "abc", ""):
            with self.assertRaises(ValueError):
                onboarding.validate_port(raw)

    def test_mdns_name_is_a_dns_label(self):
        self.assertEqual(onboarding.validate_mdns_name("peach-two"), "peach-two")
        for raw in ("", "-peach", "peach.local", "pea ch"):
            with self.assertRaises(ValueError):
                onboarding.validate_mdns_name(raw)

    def test_yes_no_defaults_to_yes(self):
        for raw in ("", "y", "Yes", "是"):
            self.assertTrue(onboarding.validate_yes_no(raw))
        for raw in ("n", "NO", "否"):
            self.assertFalse(onboarding.validate_yes_no(raw))
        with self.assertRaises(ValueError):
            onboarding.validate_yes_no("maybe")

    def test_data_root_may_not_exist_yet_but_may_not_be_a_file(self):
        self.assertEqual(onboarding.validate_data_root(str(self.root / "new")), self.root / "new")
        (self.root / "file").write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            onboarding.validate_data_root(str(self.root / "file"))


class AskUntilValidTests(_Case):
    def test_empty_input_takes_the_default(self):
        question = onboarding.Question("port", "服务端口", "8900", onboarding.validate_port)
        self.assertEqual(onboarding.ask_until_valid(question, scripted(""), report=lambda _: None), 8900)

    def test_invalid_answers_are_reported_and_reasked_up_to_the_limit(self):
        question = onboarding.Question("port", "服务端口", "8900", onboarding.validate_port)
        reported: list[str] = []
        value = onboarding.ask_until_valid(question, scripted("x", "y", "9000"), report=reported.append)
        self.assertEqual(value, 9000)
        self.assertEqual(len(reported), 2)
        self.assertIn("还可以再试 2 次", reported[0])
        with self.assertRaises(onboarding.OnboardingAborted) as caught:
            onboarding.ask_until_valid(question, scripted("x", "y", "z"), report=lambda _: None)
        self.assertIn("服务端口", str(caught.exception))
        self.assertIn("3 次", str(caught.exception))


class ConfigureTests(_Case):
    def _answers(self, **overrides):
        base = dict(data_root=self.root / "peach-data", media_dirs=(self.media,),
                    host="127.0.0.1", port=8900, mdns_name="peach")
        base.update(overrides)
        return onboarding.Answers(**base)

    def test_windows_declares_the_directory_itself_and_leaves_mounts_empty(self):
        prepared = onboarding.configure(self.config, self._answers(), windows=True)
        self.assertEqual(prepared.locations, {"local": (str(self.media),)})
        self.assertEqual(prepared.mounts, {})
        self.assertEqual(onboarding.scan_roots(prepared), (str(self.media),))

    def test_posix_keeps_the_ledger_shape_and_mounts_the_directory(self):
        prepared = onboarding.configure(self.config, self._answers(), windows=False)
        self.assertEqual(prepared.locations, {"local": (r"R:\media",)})
        self.assertEqual(prepared.mounts, {"local": (str(self.media),)})
        self.assertEqual(onboarding.scan_roots(prepared), (r"R:\media",))
        self.assertIn(str(self.media), onboarding.mounts_explanation(self.media))

    def test_several_directories_all_belong_to_the_local_source(self):
        """终端设置的两个本地目录：Windows 直接使用目录路径，macOS 按序号生成数据库路径前缀。"""
        second = self.root / "more"
        second.mkdir()
        answers = self._answers(media_dirs=(self.media, second))
        windows = onboarding.configure(self.config, answers, windows=True)
        self.assertEqual(windows.locations, {"local": (str(self.media), str(second))})
        self.assertEqual(windows.mounts, {})
        posix = onboarding.configure(self.config, answers, windows=False)
        self.assertEqual(posix.locations, {"local": (r"R:\media", r"R:\media2")})
        self.assertEqual(posix.mounts, {"local": (str(self.media), str(second))})
        self.assertEqual(onboarding.scan_roots(posix), (r"R:\media", r"R:\media2"))
        explanation = onboarding.mounts_explanation((self.media, second))
        self.assertIn(r"R:\media2", explanation)
        self.assertIn(str(second), explanation)

    def test_directories_must_not_repeat_or_nest(self):
        nested = self.media / "inner"
        nested.mkdir()
        other = self.root / "other"
        other.mkdir()
        self.assertEqual(onboarding.check_media_dirs((self.media, other)), {})
        self.assertEqual(list(onboarding.check_media_dirs((self.media, self.media))), [1])
        self.assertIn("重复", onboarding.check_media_dirs((self.media, self.media))[1])
        problems = onboarding.check_media_dirs((self.media, nested))
        self.assertEqual(list(problems), [1])
        self.assertIn(str(self.media), problems[1])
        # 父目录排在后面时，多余的仍是子目录那一行：用户后来给的父目录已经把它包住了。
        self.assertEqual(list(onboarding.check_media_dirs((nested, self.media))), [0])

    def test_form_rows_are_read_line_by_line_with_errors_kept_in_place(self):
        validate = onboarding.media_dir_validator(windows=NATIVE_WINDOWS)
        other = self.root / "other"
        other.mkdir()
        # 第一行留空取默认值，中间的空行当没填。
        paths, problems = onboarding.read_media_dirs(
            ["", "", str(other)], validate=validate, default=str(self.media))
        self.assertEqual(paths, [self.media, other])
        self.assertEqual(problems, [])
        # 错误与提交的行一一对应：第三行不存在，第二行是空行也占一个位置。
        _paths, problems = onboarding.read_media_dirs(
            [str(self.media), "", str(self.root / "nope")], validate=validate)
        self.assertEqual(problems[:2], ["", ""])
        self.assertIn("目录不存在", problems[2])
        (self.media / "inner").mkdir()
        _paths, problems = onboarding.read_media_dirs(
            [str(self.media), str(self.media / "inner")], validate=validate)
        self.assertEqual(problems[0], "")
        self.assertIn("已经在", problems[1])

    def test_only_the_asked_settings_change_and_replication_stays_off(self):
        prepared = onboarding.configure(
            self.config, self._answers(host="0.0.0.0", port=9443, mdns_name="peach-two"), windows=True)
        self.assertEqual((prepared.server.host, prepared.server.port, prepared.server.mdns_name),
                         ("0.0.0.0", 9443, "peach-two"))
        self.assertFalse(prepared.replication.enabled)
        self.assertEqual(prepared.server.review_writer_origin, "")
        self.assertEqual(prepared.replication.smb_host, "")
        self.assertEqual(set(prepared.directories), set(settings_file.DIRECTORY_KEYS))

    def test_the_written_file_reads_back_with_only_the_declared_source(self):
        """`[media.locations]` 里只有 local：内建默认的示例盘符不得从旁边混进来。"""
        prepared = onboarding.configure(self.config, self._answers(), windows=True)
        settings_file.write(prepared)
        loaded = settings_file.load_config(
            project_root=self.root / "app", environ={"PEACH_DATA_ROOT": str(self.root / "peach-data")})
        self.assertTrue(loaded.present)
        self.assertEqual(loaded.locations, {"local": (str(self.media),)})
        self.assertEqual(loaded.mounts, {})
        self.assertFalse(loaded.replication.enabled)


class InterviewTests(_Case):
    def test_scripted_answers_drive_the_whole_interview(self):
        second = self.root / "more"
        second.mkdir()
        ask = scripted("", str(self.media), str(second), "", "2", "", "peach-two")
        answers = onboarding.interview(self.config, ask, windows=NATIVE_WINDOWS, home=self.home,
                                       report=lambda _: None)
        self.assertEqual(answers, onboarding.Answers(
            data_root=self.root / "peach-data", media_dirs=(self.media, second), host="0.0.0.0",
            port=8900, mdns_name="peach-two"))
        # 媒体目录问完一个就问「再加一个」，回车结束；追加的目录和第一个走同一套校验。
        self.assertEqual([prompt for prompt, _ in ask.seen], [
            "数据目录（Peach 数据库、缓存和设置文件都放在这里）",
            "媒体目录（必须已经存在，可以在外置硬盘上）",
            "再加一个媒体目录（不加就直接回车）",
            "再加一个媒体目录（不加就直接回车）",
            "谁可以访问：1 = 只有这台电脑，2 = 同一局域网的设备",
            "端口",
            "局域网访问地址（<名字>.local，只在允许局域网访问时发布）",
        ])

    def test_an_extra_directory_inside_the_first_is_asked_again(self):
        nested = self.media / "inner"
        nested.mkdir()
        reports: list[str] = []
        ask = scripted("", str(self.media), str(nested), "", "1", "", "")
        answers = onboarding.interview(self.config, ask, windows=NATIVE_WINDOWS, home=self.home,
                                       report=reports.append)
        self.assertEqual(answers.media_dirs, (self.media,))
        self.assertTrue(any("已经在" in line for line in reports), reports)

    def test_console_ask_shows_the_default_and_treats_eof_as_abort(self):
        with mock.patch("builtins.input", return_value="x") as prompt:
            self.assertEqual(onboarding.console_ask("服务端口", "8900"), "x")
        prompt.assert_called_once_with("服务端口 [8900]: ")
        with mock.patch("builtins.input", return_value="") as prompt:
            onboarding.console_ask("本地媒体目录", "")
        prompt.assert_called_once_with("本地媒体目录（必填）: ")
        with mock.patch("builtins.input", side_effect=EOFError):
            with self.assertRaises(onboarding.OnboardingAborted):
                onboarding.console_ask("服务端口", "8900")

    def test_only_a_real_terminal_counts_as_interactive(self):
        self.assertFalse(onboarding.is_interactive(io.StringIO()))
        self.assertFalse(onboarding.is_interactive(None))
        self.assertTrue(onboarding.is_interactive(mock.Mock(isatty=lambda: True)))


class ApplyTests(_Case):
    """`apply()` 是 CLI 问答与设置页共用的落盘入口。"""

    def setUp(self):
        super().setUp()
        (self.media / "sub").mkdir()
        (self.media / "sub" / "a.mp4").write_bytes(b"0" * 10)
        patcher = mock.patch.object(onboarding.certs, "bootstrap_certificates")
        self.certs = patcher.start()
        self.certs.return_value.ca_cert = self.root / "peach-data" / "secrets" / "tls" / "ca.crt"
        self.addCleanup(patcher.stop)

    def _answers(self, **overrides):
        base = dict(data_root=self.root / "peach-data", media_dirs=(self.media,),
                    host="127.0.0.1", port=8900, mdns_name="peach")
        base.update(overrides)
        return onboarding.Answers(**base)

    def test_apply_builds_the_whole_tree_and_writes_the_settings_file(self):
        applied = onboarding.apply(self.config, self._answers(), windows=NATIVE_WINDOWS)
        data_root = self.root / "peach-data"
        for key in settings_file.DIRECTORY_KEYS:
            self.assertTrue((data_root / key).is_dir(), key)
        self.assertTrue((data_root / "secrets" / "tls").is_dir())
        self.assertEqual(applied.settings_path, data_root / "config.toml")
        self.assertTrue(applied.settings_path.is_file())
        self.assertFalse(applied.tree.ledger_existed)
        self.assertGreater(applied.tree.migrations, 0)
        self.assertTrue(applied.tree.database.is_file())
        self.assertTrue(applied.tree.token_path.is_file())
        self.assertTrue(applied.tree.token_created)
        self.certs.assert_called_once()
        # 账本真的被迁到最新：`asset` 表在，且是空的（apply 不扫描）。
        connection = sqlite3.connect(applied.tree.database)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM asset").fetchone()[0], 0)
        finally:
            connection.close()

    def test_an_existing_ledger_is_never_migrated_or_overwritten(self):
        first = onboarding.apply(self.config, self._answers(), windows=NATIVE_WINDOWS)
        again = onboarding.create_data_tree(first.config)
        self.assertTrue(again.ledger_existed)
        self.assertEqual(again.migrations, 0)
        self.assertFalse(again.token_created, "口令沿用已有的那份，不重新生成")

    def test_a_missing_openssl_is_reported_instead_of_aborting_the_setup(self):
        self.certs.side_effect = RuntimeError("openssl 不在 PATH 上")
        applied = onboarding.apply(self.config, self._answers(), windows=NATIVE_WINDOWS)
        self.assertIsNone(applied.tree.ca_cert)
        self.assertIn("openssl", applied.tree.ca_error)
        self.assertTrue(applied.settings_path.is_file(), "没有 CA 也要把设置文件写出来")

    def test_writing_over_an_existing_settings_file_needs_force(self):
        onboarding.apply(self.config, self._answers(), windows=NATIVE_WINDOWS)
        with self.assertRaises(settings_file.SettingsFileError):
            onboarding.apply(self.config, self._answers(), windows=NATIVE_WINDOWS)
        onboarding.apply(self.config, self._answers(port=9100),
                         windows=NATIVE_WINDOWS, force=True)
        loaded, _ = onboarding.resolve_config(self.root / "peach-data", environ={})
        self.assertEqual(loaded.server.port, 9100)

    def test_the_first_scan_marker_is_consumed_exactly_once(self):
        applied = onboarding.apply(self.config, self._answers(), windows=NATIVE_WINDOWS)
        config = applied.config
        self.assertIsNone(onboarding.take_first_scan_request(config))
        path = onboarding.request_first_scan(config)
        self.assertEqual(path, config.directory("state") / onboarding.SCAN_REQUEST_NAME)
        self.assertEqual(onboarding.take_first_scan_request(config), "local")
        self.assertFalse(path.exists())
        self.assertIsNone(onboarding.take_first_scan_request(config))

    def test_resolve_config_follows_the_given_data_root(self):
        elsewhere = self.root / "somewhere-else"
        config, broken = onboarding.resolve_config(elsewhere, environ={})
        self.assertIsNone(broken)
        self.assertEqual(config.data_root, elsewhere)
        self.assertEqual(config.path, elsewhere / "config.toml")

    def test_a_broken_settings_file_falls_back_and_hands_over_the_error(self):
        data_root = self.root / "peach-data"
        data_root.mkdir()
        (data_root / "config.toml").write_text("[server\nport = ", encoding="utf-8")
        config, broken = onboarding.resolve_config(data_root, environ={})
        self.assertIsNotNone(broken)
        self.assertEqual(config.server.port, 8900, "坏文件退回内建默认")


class _SetupHttpCase(_Case):
    """首启服务的 HTTP 夹具：临时 Peach 数据目录、证书生成打桩、没有口令的应用。"""

    def setUp(self):
        super().setUp()
        (self.media / "a.mp4").write_bytes(b"0" * 10)
        patcher = mock.patch.object(onboarding.certs, "bootstrap_certificates")
        self.certs = patcher.start()
        self.addCleanup(patcher.stop)
        active = mock.patch.object(settings_file, "active", lambda: self.config)
        active.start()
        self.addCleanup(active.stop)
        self.data_root = self.root / "peach-data"

    def _client(self, *, configured: bool = False, client=("127.0.0.1", 12345), base_url="http://test"):
        import httpx

        from peach.api import create_app
        from peach.config import PeachSettings

        app = create_app(PeachSettings(
            db_path=self.data_root / "database" / "ledger.db",
            configured=configured, token="",
        ))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=client),
            base_url=base_url)

    def _send(self, method, path, *, headers=None, data=None, json=None, **kwargs):
        import asyncio

        async def run():
            async with self._client(**kwargs) as client:
                return await client.request(method, path, headers=headers, data=data, json=json)

        return asyncio.run(run())

    def _get(self, path, **kwargs):
        import asyncio

        async def run():
            async with self._client(**kwargs) as client:
                return await client.get(path)

        return asyncio.run(run())

    def _loaded(self):
        return settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.data_root)})


@unittest.skipUnless(HAS_HTTP_DEPS, "需要 fastapi 与 httpx")
class SetupJsonContractTests(_SetupHttpCase):
    """首启的 JSON 契约：`GET /api/setup/questions` 与 `POST /api/setup`。

    首次运行页的题目、默认值与文案全部来自读题端点，逐字段校验与落盘全部在提交端点；
    这一层和 `peach init` 的问答共用 `peach.onboarding`，所以断言的是「端点给出的字段和
    `questions()` 同名同序」，而不是另抄一份字段清单去比对。页面怎么画见 vitest 与 e2e。
    """

    QUESTIONS = "/api/setup/questions"
    SUBMIT = "/api/setup"

    def _rows(self, *paths, location="local"):
        return [{"path": str(path), "location": location,
                 "root": "" if NATIVE_WINDOWS else "R:\\media" + (str(index + 1) if index else "")}
                for index, path in enumerate(paths)]

    def _body(self, **overrides):
        body = {"data_root": str(self.data_root), "media_dir": self._rows(self.media),
                "host": "1", "port": "8900", "mdns_name": "peach", "scan_now": True}
        body.update(overrides)
        return {key: value for key, value in body.items() if value is not None}

    def _errors(self, body, **kwargs):
        """提交一份填错的答卷，返回按题目 key 收集的 errors。"""
        response = self._send("POST", self.SUBMIT, json=body, **kwargs)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"], "有几项需要修改")
        self.assertFalse((self.data_root / "config.toml").exists(), "校验失败不写设置")
        return response.json()["errors"]

    def _submit(self, **overrides):
        response = self._send("POST", self.SUBMIT, json=self._body(**overrides))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_remote_reporting_defaults_on_and_opt_out_is_saved_in_selected_data_root(self):
        from peach import remote_reporting
        response = self._send("GET", self.QUESTIONS)
        self.assertTrue(response.json()["remote_reporting"]["usage"])
        self.assertTrue(response.json()["remote_reporting"]["errors"])
        self._submit(remote_reporting={"usage": False, "errors": False})
        config = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.data_root)})
        self.assertFalse(remote_reporting.consent(config)["usage"])
        self.assertFalse(remote_reporting.consent(config)["errors"])

    def test_remote_reporting_rejects_non_boolean_consent_before_creating_settings(self):
        for value in ([], {"usage": "true"}, {"errors": 1}, {"usage": False, "errors": True}):
            response = self._send("POST", self.SUBMIT, json=self._body(remote_reporting=value))
            self.assertEqual(response.status_code, 400)
            self.assertFalse((self.data_root / "config.toml").exists())

    def test_the_first_run_page_is_a_shell_around_the_standalone_page_bundle(self):
        """首次运行页由独立页面包画：薄壳只带主题预读、样式、脚本、借来的字形与挂载点。"""
        response = self._get("/")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("<title>Peach · 首次运行</title>", body)
        self.assertIn('<link rel="icon" href="/favicon.ico" type="image/x-icon">', body)
        self.assertLess(body.index('localStorage.getItem("peach.settings.v1")'), body.index("/dist/peach-pages.css"))
        for symbol in ("folder-search", "hard-drive", "database"):
            self.assertIn(f'<symbol id="i-{symbol}"', body)
        self.assertNotIn("/dist/peach-entry.js", body)
        self.assertNotIn("<form", body)
        # 首启服务没有口令，页面包、Inter 与站标得放行。
        for path in ("/dist/peach-pages.js", "/dist/peach-pages.css", "/vendor/inter/5.3.0/index.css",
                     "/peach-logo.png"):
            with self.subTest(path):
                self.assertEqual(self._get(path).status_code, 200)

    def test_invalid_values_are_reported_without_creating_anything(self):
        missing = self.root / "nope"
        errors = self._errors(self._body(media_dir=self._rows(missing), port="0"))
        self.assertIn("目录不存在", errors["media_dir"][0])
        self.assertIn("端口要是 1 到 65535 之间的整数", errors["port"])
        self.assertFalse(missing.exists(), "校验不替人建目录")
        self.assertFalse(self.data_root.exists(), "校验失败不落任何文件")

    def test_a_valid_submission_builds_the_tree(self):
        self._submit()
        for key in settings_file.DIRECTORY_KEYS:
            self.assertTrue((self.data_root / key).is_dir(), key)
        self.assertTrue((self.data_root / "database" / "ledger.db").is_file())
        self.assertTrue((self.data_root / "secrets" / "tls").is_dir())
        self.assertTrue((self.data_root / "config.toml").is_file())
        self.certs.assert_called_once()
        loaded = self._loaded()
        self.assertTrue(loaded.present)
        self.assertEqual((loaded.server.host, loaded.server.port), ("127.0.0.1", 8900))
        self.assertFalse(loaded.replication.enabled)

    def test_a_custom_data_root_is_loaded_by_the_tray_and_a_fresh_launch(self):
        import json
        import socket
        import subprocess
        import sys
        from urllib.request import ProxyHandler, build_opener
        from peach.tray import AlreadyRunning, ServiceManager, SetupGate, SingleInstance, _peach_executable

        selected = self.root / "自选数据目录"
        executable = _peach_executable()
        environment = {"PEACH_DATA_ROOT": str(self.config.data_root)}

        def launch_service(command, **kwargs):
            python = sys.executable
            if os.name == "nt":
                python = sys._base_executable
                kwargs["env"]["__PYVENV_LAUNCHER__"] = sys.executable
            return subprocess.Popen([python, "-m", "peach", *command[1:]], **kwargs)

        manager = ServiceManager((), log_dir=self.config.directory("logs"), popen=launch_service)
        self.addCleanup(manager.stop_owned)
        instance = SingleInstance(self.config.directory("state") / "peach-tray.lock")
        instance.acquire()
        self.addCleanup(instance.close)
        gate = SetupGate(manager, self.config, waiting=True,
                         instance=instance,
                         load=lambda: settings_file.load_config(environ=environment))
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            port = holder.getsockname()[1]
        with mock.patch.object(distribution, "standalone", return_value=True), \
                mock.patch("peach.tray._peach_executable", return_value=executable):
            response = self._send("POST", self.SUBMIT,
                                  json=self._body(data_root=str(selected), port=str(port), scan_now=False),
                                  base_url="http://127.0.0.1:8900")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(gate.poll())
            self.assertFalse(gate.waiting)
            self.assertEqual(gate.config.data_root, selected)
            self.assertTrue(manager.wait_until_ready(), "切换后的服务必须能访问")
            opener = build_opener(ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=5) as ready:
                self.assertTrue(json.load(ready)["configured"])
            with opener.open(f"http://127.0.0.1:{port}/", timeout=5) as page:
                self.assertNotIn('data-page="setup"', page.read().decode("utf-8"))
            with mock.patch.object(distribution, "user_data_root", return_value=self.config.data_root):
                fresh = settings_file.load_config(environ={})
            self.assertTrue(fresh.present)
            self.assertEqual(fresh.data_root, selected)
            self.assertEqual(instance.path.parent, fresh.directory("state"))
            with self.assertRaises(AlreadyRunning):
                SingleInstance(instance.path).acquire()
        self.assertTrue((selected / "database" / "ledger.db").is_file())
        self.assertFalse((self.config.data_root / "database" / "ledger.db").exists())
        self.assertEqual(manager.child_environment()["PEACH_DATA_ROOT"], str(selected))

    def test_two_directories_are_declared_together(self):
        second = self.root / "more"
        second.mkdir()
        self._submit(media_dir=self._rows(self.media, second))
        loaded = self._loaded()
        if NATIVE_WINDOWS:
            self.assertEqual(loaded.locations, {"local": (str(self.media), str(second))})
        else:
            self.assertEqual(loaded.locations, {"local": (r"R:\media", r"R:\media2")})
            self.assertEqual(loaded.mounts, {"local": (str(self.media), str(second))})

    def test_an_omitted_lan_address_falls_back_to_the_default_name(self):
        """选「只有这台电脑」后地址项隐藏且不提交；服务端按默认值补上。"""
        self._submit(host="1", mdns_name=None)
        loaded = self._loaded()
        self.assertEqual((loaded.server.host, loaded.server.mdns_name), ("127.0.0.1", "peach"))

    def test_password_fields_are_ignored_while_the_switch_is_off(self):
        from peach import access
        self._submit(access_enabled=False, access_password="should-not-be-used",
                     access_confirm="should-not-be-used")
        self.assertEqual(access.load(self.data_root / "secrets" / "access.json")["mode"], "open")

    def test_the_setup_service_opens_the_folder_dialog_for_each_row(self):
        with mock.patch("peach.folder_picker.pick_folder", return_value=str(self.media)) as picker:
            response = self._send("POST", "/api/pick-folder", json={"initial": "E:/old"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"path": str(self.media)})
        picker.assert_called_once_with("E:/old")

    def test_the_questions_cover_every_field_in_order_with_defaults_and_copy(self):
        response = self._send("GET", self.QUESTIONS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()
        asked = onboarding.questions(self.config, windows=NATIVE_WINDOWS)
        questions = data["questions"]
        self.assertEqual([item["key"] for item in questions], [question.key for question in asked])
        self.assertEqual([item["default"] for item in questions], [question.default for question in asked])
        self.assertEqual([item["label"] for item in questions], ["数据目录", "媒体库", "谁可以访问", "端口", "局域网访问地址"])
        by_key = {item["key"]: item for item in questions}
        self.assertEqual([key for key, item in by_key.items() if not item["required"]], ["host"])
        self.assertEqual([key for key, item in by_key.items() if not item["advanced"]], ["media_dir"])
        self.assertEqual(by_key["host"]["options"], [{"value": "2", "label": "同一局域网的设备"},
                                                     {"value": "1", "label": "只有这台电脑"}])
        self.assertEqual((by_key["port"]["input"], by_key["port"]["prefix"]), ("number", "localhost:"))
        self.assertEqual((by_key["mdns_name"]["prefix"], by_key["mdns_name"]["suffix"]), ("https://", ".local"))
        self.assertEqual(by_key["mdns_name"]["visible_when"], {"host": "2"})
        self.assertEqual(by_key["media_dir"]["input"], "folders")
        self.assertEqual(data["windows"], NATIVE_WINDOWS)
        self.assertFalse(data["standalone"])
        self.assertEqual([row["value"] for row in data["media_sources"]],
                         ["local", "115", "pikpak", "guangya", "onedrive", "mounted"])
        self.assertEqual(data["media_source_default"], "local")
        if NATIVE_WINDOWS:
            self.assertIsNone(data["media_root"])
            self.assertEqual(len(by_key["media_dir"]["help"]), 1)
        else:
            self.assertIsNone(data["media_root"])
            self.assertIn("本机文件夹是这台电脑读取媒体的位置", by_key["media_dir"]["help"][-1])
        self.assertEqual(data["cloud"]["link"]["url"], "https://www.clouddrive2.com/help.html")
        # 默认对局域网开放，所以访问密码开关第一次出现就开着；扫描默认勾上，导入历史默认不勾。
        self.assertEqual((data["access_enabled"], data["scan_now"], data["history_guide"]), (True, True, False))

    def test_setup_preserves_explicit_proxy_protection_for_a_mounted_source(self):
        rows = self._rows(self.media, location="onedrive")
        rows[0]["cost"] = "free"
        self._submit(media_dir=rows, scan_now=False)
        loaded = self._loaded()
        self.assertEqual(set(loaded.locations), {"onedrive"})
        self.assertEqual(set(loaded.media_costs.values()), {"free"})
        self.assertEqual(loaded.media_costs[next(iter(loaded.locations["onedrive"]))], "free")

    def test_the_questions_list_missing_mount_software_with_download_links(self):
        rows = [{"name": "CloudDrive", "available": True, "download_url": "https://www.clouddrive2.com/download.html"},
                {"name": "WinFsp", "available": False, "download_url": "https://winfsp.dev/rel/"}]
        with mock.patch("peach.media_configuration.mount_dependencies", return_value=rows):
            data = self._send("GET", self.QUESTIONS).json()
        self.assertEqual(data["cloud"]["dependencies"], [{
            "name": "WinFsp", "message": "未检测到 WinFsp。",
            "download_url": "https://winfsp.dev/rel/", "download_label": "下载 WinFsp"}])

    def test_the_standalone_package_gets_its_own_address_form_in_the_questions(self):
        with mock.patch.object(distribution, "standalone", return_value=True):
            data = self._send("GET", self.QUESTIONS, base_url="http://127.0.0.1:8900").json()
        self.assertTrue(data["standalone"])
        mdns = next(item for item in data["questions"] if item["key"] == "mdns_name")
        self.assertEqual(mdns["prefix"], "http://")
        self.assertIn("http://peach.local:8900", mdns["help"][0])

    def test_the_questions_are_guarded_like_the_submission_but_ignore_the_origin(self):
        cases = (
            ("已配置", {"configured": True}, 404, "not found"),
            ("非回环调用方", {"client": ("198.51.100.7", 51000)}, 403, "setup is loopback-only"),
        )
        for name, kwargs, status, message in cases:
            with self.subTest(name):
                response = self._send("GET", self.QUESTIONS, headers={"Accept": "text/html"}, **kwargs)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json(), {"error": message}, "/api/ 下一律回 JSON")
        with self.subTest("独立包的非回环主机名"), mock.patch.object(distribution, "standalone", return_value=True):
            response = self._send("GET", self.QUESTIONS, base_url="http://peach.local:8900")
            self.assertEqual((response.status_code, response.json()), (403, {"error": "请使用本机地址打开设置"}))
        with self.subTest("读题不看 Origin"):
            response = self._send("GET", self.QUESTIONS, headers={"Origin": "http://elsewhere.example"})
            self.assertEqual(response.status_code, 200)

    def test_the_submission_is_loopback_only_and_same_origin(self):
        cases = (
            ("已配置", {"configured": True}, 404, "not found"),
            ("非回环调用方", {"client": ("198.51.100.7", 51000)}, 403, "setup is loopback-only"),
            ("别的页面发来", {"headers": {"Origin": "http://elsewhere.example"}}, 403, "请从 Peach 设置页提交"),
        )
        for name, kwargs, status, message in cases:
            with self.subTest(name):
                response = self._send("POST", self.SUBMIT, json=self._body(), **kwargs)
                self.assertEqual((response.status_code, response.json()), (status, {"error": message}))
        with self.subTest("独立包的非回环主机名"), mock.patch.object(distribution, "standalone", return_value=True):
            response = self._send("POST", self.SUBMIT, json=self._body(), base_url="http://peach.local:8900")
            self.assertEqual((response.status_code, response.json()), (403, {"error": "请使用本机地址打开设置"}))
        self.assertFalse(self.data_root.exists(), "被守卫挡下的提交不落任何文件")
        with self.subTest("不是 JSON 对象"):
            response = self._send("POST", self.SUBMIT, json=["not", "an", "object"])
            self.assertEqual((response.status_code, response.json()), (400, {"error": "请求正文要是 JSON 对象"}))

    def test_a_second_json_submission_refuses_to_overwrite_the_settings_file(self):
        first = self._send("POST", self.SUBMIT, json=self._body(), headers={"Origin": "http://test"})
        self.assertEqual(first.status_code, 200, first.text)
        again = self._send("POST", self.SUBMIT, json=self._body(port="9100"))
        self.assertEqual((again.status_code, again.json()), (409, {"error": "settings file already exists"}))
        self.assertEqual(self._loaded().server.port, 8900)

    def test_restore_adopts_the_selected_existing_configuration_without_rewriting_data(self):
        selected = self.root / "existing-data"
        self._submit(data_root=str(selected), scan_now=False)
        locator = self.config.data_root / "state" / settings_file.DATA_ROOT_LOCATION
        locator.unlink()
        before = {path: path.read_bytes() for path in selected.rglob("*") if path.is_file()}
        response = self._send("POST", "/api/setup/restore", json={"data_root": str(selected)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["restored"])
        self.assertFalse(response.json()["scan_requested"])
        self.assertEqual(self._loaded().data_root, selected)
        self.assertEqual(before, {path: path.read_bytes() for path in selected.rglob("*") if path.is_file()})

    def test_restore_requires_an_existing_configuration_and_ledger_and_keeps_guards(self):
        for value in (None, "relative", str(self.root / "missing")):
            with self.subTest(value=value):
                response = self._send("POST", "/api/setup/restore", json={"data_root": value})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("data_root", response.json()["errors"])
        self.assertFalse((self.config.data_root / "state" / settings_file.DATA_ROOT_LOCATION).exists())
        for kwargs, status in (({"configured": True}, 404), ({"client": ("198.51.100.7", 1)}, 403),
                               ({"headers": {"Origin": "http://elsewhere.example"}}, 403)):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self._send("POST", "/api/setup/restore", json={}, **kwargs).status_code, status)
        selected = self.root / "existing-data"
        self._submit(data_root=str(selected), scan_now=False)
        (self.config.data_root / "state" / settings_file.DATA_ROOT_LOCATION).unlink()
        (selected / "database" / "ledger.db").unlink()
        response = self._send("POST", "/api/setup/restore", json={"data_root": str(selected)})
        self.assertEqual(response.status_code, 400, response.text)

    def test_each_wrong_answer_comes_back_under_its_own_key(self):
        a_file = self.root / "not-a-folder"
        a_file.write_text("x", encoding="utf-8")
        nested = self.media / "inner"
        nested.mkdir()
        cases = {
            "目录不存在与端口越界": self._body(media_dir=self._rows(self.root / "nope"), port="0"),
            "超过 100 行": self._body(media_dir=self._rows(*[self.media] * 101)),
            "第二行嵌在第一行里": self._body(media_dir=self._rows(self.media, nested)),
            "云盘行不是绝对路径": self._body(media_dir=self._rows(self.media) + [
                {"path": "relative", "location": "115", "root": "" if NATIVE_WINDOWS else "R:\\cloud"}]),
            "开关开着没填密码": self._body(access_enabled=True),
            "两次密码不一致": self._body(access_enabled=True, access_password="first-password",
                                   access_confirm="other-password"),
            "密码太短": self._body(access_enabled=True, access_password="short", access_confirm="short"),
            "高级设置的端口越界": self._body(port="99999", history_guide=True),
            "局域网地址不合规": self._body(host="2", mdns_name="-bad-"),
            "谁可以访问不认识": self._body(host="9"),
            "数据目录是文件": self._body(data_root=str(a_file)),
        }
        expected_keys = {
            "目录不存在与端口越界": {"media_dir", "port"}, "超过 100 行": {"media_dir"},
            "第二行嵌在第一行里": {"media_dir"}, "云盘行不是绝对路径": {"media_dir"},
            "开关开着没填密码": {"access_password"}, "两次密码不一致": {"access_password"},
            "密码太短": {"access_password"}, "高级设置的端口越界": {"port"},
            "局域网地址不合规": {"mdns_name"}, "谁可以访问不认识": {"host"}, "数据目录是文件": {"data_root"},
        }
        for name, body in cases.items():
            with self.subTest(name):
                self.assertEqual(set(self._errors(body)), expected_keys[name])
        # 媒体库的错误按行对齐；整表不成立时只有一句，长度与行数不等。
        rows = self._errors(cases["第二行嵌在第一行里"])
        self.assertEqual(len(rows["media_dir"]), 2)
        self.assertEqual(rows["media_dir"][0], "")
        self.assertTrue(rows["media_dir"][1])
        table = self._errors(cases["超过 100 行"])
        self.assertEqual(table["media_dir"], ["请添加 1 到 100 个媒体目录"])

    def test_an_occupied_port_on_the_standalone_package_is_a_port_error(self):
        import socket
        with socket.socket() as holder, mock.patch.object(distribution, "standalone", return_value=True):
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            port = holder.getsockname()[1]
            data = self._errors(self._body(port=str(port)), base_url="http://127.0.0.1:8900")
        self.assertEqual(data, {"port": "这个端口已被占用，请换一个端口"})

    def test_a_failed_apply_is_reported_on_the_data_root(self):
        with mock.patch.object(onboarding, "apply", side_effect=OSError("数据目录写不进去")):
            data = self._errors(self._body())
        self.assertEqual(data, {"data_root": "数据目录写不进去"})

    def test_a_valid_json_submission_returns_the_entry_address_and_runtime_facts(self):
        from peach import access
        from peach.web_entry import runtime_fact_entries
        response = self._send("POST", self.SUBMIT, json=self._body(
            access_enabled=True, access_password="correct-password", access_confirm="correct-password"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()
        self.assertEqual({key: data[key] for key in ("url", "scan_requested", "history_guide", "standalone", "redirect")},
                         {"url": "http://127.0.0.1:8900/?onboarding=1", "scan_requested": True,
                          "history_guide": False, "standalone": False, "redirect": None})
        self.assertEqual([row["term"] for row in data["facts"]],
                         [row["term"] for row in runtime_fact_entries(self._loaded())])
        self.assertIn(str(self.data_root), [row["value"] for row in data["facts"]])
        self.assertTrue((self.data_root / "state" / onboarding.SCAN_REQUEST_NAME).is_file())
        policy = access.load(self.data_root / "secrets" / "access.json")
        self.assertEqual(policy["mode"], "password")
        self.assertNotIn("correct-password", response.text, "回话里不带密码")

    def test_the_history_guide_without_a_scan_leads_to_the_taste_page(self):
        with mock.patch('peach.web_stats.w_taste_refresh') as refresh:
            response = self._send("POST", self.SUBMIT, json=self._body(scan_now=False, history_guide=True))
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual((data["url"], data["scan_requested"], data["history_guide"], data["redirect"]),
                         ("http://127.0.0.1:8900/taste?onboarding=1", False, True, None))
        self.assertFalse((self.data_root / "state" / onboarding.SCAN_REQUEST_NAME).exists())
        refresh.assert_not_called()

    def test_the_standalone_package_is_told_to_redirect_to_the_entry_address(self):
        with mock.patch.object(distribution, "standalone", return_value=True):
            response = self._send("POST", self.SUBMIT, json=self._body(), base_url="http://127.0.0.1:8900")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual((data["standalone"], data["scan_requested"], data["history_guide"]), (True, True, False))
        self.assertEqual(data["redirect"], data["url"])
        self.assertEqual(data["url"], "http://127.0.0.1:8900/?onboarding=1")


class StandaloneConfigurationTests(_Case):
    def test_automatic_update_settings_roundtrip_and_access_boundaries(self):
        from peach.automatic_updates import AutomaticUpdates
        path = "/api/configuration/automatic-updates"
        headers = {"X-Token": "test-token"}
        with self.client() as client:
            client.app.state.automatic_updates = AutomaticUpdates(self.root, available=True)
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.post(path, headers=dict(headers, Origin="https://evil.example"),
                                         json={"mode": "check", "interval_hours": 24}).status_code, 403)
            saved = client.post(path, headers=headers, json={"mode": "download", "interval_hours": 6})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(client.get(path, headers=headers).json()["mode"], "download")
            self.assertEqual(client.get("/api/configuration", headers=headers).json()["automatic_updates"]["interval_hours"], 6)
            self.assertEqual(client.post(path, headers=headers, json={"mode": "off", "interval_hours": 24}).status_code, 200)
        with self.client(address="192.0.2.9") as client:
            self.assertEqual(client.get(path, headers=headers).status_code, 403)
            self.assertEqual(client.post(path, headers=headers, json={}).status_code, 403)

    def test_release_update_endpoints_require_local_authenticated_access(self):
        with self.client() as client, mock.patch("peach.release_updates.check", return_value={"state": "current"}) as check:
            self.assertEqual(client.get("/api/configuration/updates").status_code, 401)
            response = client.get("/api/configuration/updates", headers={"X-Token": "test-token"})
            self.assertEqual(response.json(), {"state": "current"})
            check.assert_called_once()
            self.assertEqual(client.post("/api/configuration/update", headers={"X-Token":"test-token", "Origin":"https://evil.example"}).status_code,403)
        with self.client(address="192.0.2.9") as client:
            for path in ("updates", "update-status"):
                self.assertEqual(client.get(f"/api/configuration/{path}",headers={"X-Token":"test-token"}).status_code,403)

    def test_missing_media_tools_have_download_links_in_the_runtime_facts(self):
        from peach.web_entry import runtime_fact_entries
        for missing_ffmpeg, missing_probe in ((True, True), (False, True), (False, False)):
            with self.subTest(ffmpeg=missing_ffmpeg, probe=missing_probe), mock.patch(
                    "peach.ffmpeg.FFmpegResolver.ffmpeg", return_value=None if missing_ffmpeg else object()), mock.patch(
                    "peach.ffmpeg.FFmpegResolver.ffprobe", return_value=None if missing_probe else object()):
                # 两样都在时 FFmpeg 不占一行，取不到就按没有下载入口算。
                fact = next((row for row in runtime_fact_entries(self.config) if row['term'] == 'FFmpeg'), {})
                self.assertEqual(fact.get('download_url') == 'https://ffmpeg.org/download.html',
                                 missing_ffmpeg or missing_probe)

    def test_mount_downloads_follow_os_and_detected_installation(self):
        from peach.media_configuration import mount_dependencies
        from peach.routes_pages import missing_mount_dependencies

        def names(*, windows):
            return [row['download_label'] for row in missing_mount_dependencies(windows=windows)]
        with mock.patch("peach.media_configuration._windows_installed_names", return_value=[]), mock.patch(
                "peach.media_configuration.shutil.which", return_value=None):
            windows = mount_dependencies(system='win32')
            self.assertEqual([row['name'] for row in windows], ['CloudDrive', 'WinFsp'])
            self.assertTrue(all(not row['available'] for row in windows))
            self.assertIn('下载 WinFsp', names(windows=True))
            self.assertNotIn('下载 macFUSE', names(windows=True))
        with mock.patch("peach.media_configuration._windows_installed_names", return_value=['clouddrive', 'winfsp 2026']):
            self.assertTrue(all(row['available'] for row in mount_dependencies(system='win32')))
            self.assertEqual(names(windows=True), [])
        with mock.patch("peach.media_configuration.Path.is_dir", return_value=False), mock.patch(
                "peach.media_configuration.shutil.which", return_value=None):
            self.assertEqual([row['name'] for row in mount_dependencies(system='darwin')], ['CloudDrive', 'macFUSE'])
            self.assertIn('下载 macFUSE', names(windows=False))
            self.assertNotIn('下载 WinFsp', names(windows=False))

    def setUp(self):
        super().setUp()
        from dataclasses import replace
        self.config = replace(self.config, locations={"local": (str(self.media),)},
                              mounts={}, server=settings_file.ServerSettings(port=9123))
        settings_file.write(self.config)
        self.config = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.config.data_root)})
        self.addCleanup(mock.patch.stopall)
        mock.patch("peach.distribution.standalone", return_value=True).start()
        mock.patch("peach.settings_file.load_config", side_effect=lambda: settings_file._merge(
            self.config.data_root, self.config.path, True, True,
            settings_file._read_document(self.config.path), {})).start()

    @contextmanager
    def client(self, *, token="test-token", address="127.0.0.1", base_url="http://localhost", server=None):
        from fastapi.testclient import TestClient
        from peach.api import create_app
        from peach.config import PeachSettings
        app = create_app(PeachSettings(configured=True, token=token, mdns_name="peach-writer",
                         follow_state_root=self.root / "state",
                         db_path=self.config.data_root / "database" / "ledger.db"))
        if server:
            @app.middleware("http")
            async def connection_address(request, call_next):
                request.scope["server"] = (server, 443)
                return await call_next(request)
        with self.assertNoLogs("peach", level="ERROR"), \
                TestClient(app, client=(address, 12345), base_url=base_url) as client:
            yield client

    def test_local_mdns_connection_shares_configuration_access_for_read_save_and_picker(self):
        headers = {"X-Token": "test-token"}
        with self.client(address="192.0.2.10", server="192.0.2.10",
                         base_url="https://peach-writer.local") as client:
            self.assertTrue(client.get("/healthz").json()["configurable"])
            self.assertEqual(client.get("/api/configuration").status_code, 401)
            response = client.get("/api/configuration", headers=headers)
            self.assertEqual(response.status_code, 200)
            with mock.patch("peach.folder_picker.pick_folder", return_value=str(self.media)):
                picked = client.post("/api/pick-folder", headers=headers, json={})
            self.assertEqual(picked.status_code, 200, picked.text)
            saved = client.post("/api/configuration", headers=headers, json={
                "revision": response.json()["revision"], "media_dirs": [str(self.media)], "port": "9123"})
            self.assertEqual(saved.status_code, 200, saved.text)

    def test_configuration_connection_gate_rejects_remote_and_untrusted_hosts(self):
        cases = [
            ("192.0.2.11", "192.0.2.10", "peach-writer.local", False),
            ("192.0.2.10", "192.0.2.10", "example.org", False),
            ("192.0.2.10", "192.0.2.10", "192.0.2.10", True),
            ("127.0.0.1", "127.0.0.1", "peach-writer.local", True),
            ("::1", "::1", "localhost", True),
            ("0.0.0.0", "0.0.0.0", "peach-writer.local", False),
        ]
        for peer, server, host, allowed in cases:
            with self.subTest(peer=peer, host=host), self.client(
                    address=peer, server=server, base_url=f"https://{host}") as client:
                headers = {"X-Token": "test-token", "X-Forwarded-For": server}
                self.assertEqual(client.get("/healthz", headers=headers).json()["configurable"], allowed)
                self.assertEqual(client.get("/api/configuration", headers=headers).status_code,
                                 200 if allowed else 403)
                if not allowed:
                    for path in ("/api/configuration", "/api/pick-folder"):
                        self.assertEqual(client.post(path, headers=headers, json={}).status_code, 403)

    def test_tray_managed_source_configuration_keeps_the_https_origin_and_managed_port(self):
        from peach.routes_configuration import RELOAD_NAME
        with mock.patch("peach.distribution.standalone", return_value=False), mock.patch.dict(
                os.environ, {"PEACH_TRAY_MANAGED": "1"}), self.client(
                address="192.0.2.10", server="192.0.2.10", base_url="https://peach-writer.local") as client:
            headers = {"X-Token": "test-token"}
            self.assertTrue(client.get("/healthz").json()["configurable"])
            snapshot = client.get("/api/configuration", headers=headers).json()
            self.assertTrue(snapshot["editable"])
            self.assertFalse(snapshot["port_editable"])
            body = {"revision": snapshot["revision"], "media_dirs": [str(self.media)], "port": "9124"}
            self.assertEqual(client.post("/api/configuration", headers=headers, json=body).status_code, 400)
            with mock.patch("peach.onboarding.check_available_port") as check:
                saved = client.post("/api/configuration", headers=headers, json={**body, "port": "9123"})
            check.assert_not_called()
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["url"], "https://peach-writer.local/")
            self.assertTrue((self.config.directory("state") / RELOAD_NAME).is_file())
        with mock.patch("peach.distribution.standalone", return_value=False), mock.patch.dict(
                os.environ, {"PEACH_TRAY_MANAGED": ""}), self.client() as client:
            self.assertFalse(client.get("/healthz").json()["configurable"])
            self.assertFalse(client.get("/api/configuration", headers=headers).json()["editable"])
            self.assertEqual(client.post("/api/configuration", headers=headers, json=body).status_code, 409)

    def test_a_dev_serve_counts_as_configurable_without_a_tray(self):
        """裸 serve 调试（--no-auth 置 PEACH_DEV=1）免掉「先去托管主机」的前提。"""
        with mock.patch.dict(os.environ, {"PEACH_DEV": "1", "PEACH_TRAY_MANAGED": ""}), \
                mock.patch("peach.distribution.standalone", return_value=False), \
                self.client() as client:
            self.assertTrue(client.get("/healthz").json()["configurable"])
            headers = {"X-Token": "test-token"}
            self.assertEqual(client.get("/api/configuration", headers=headers).status_code, 200)

    def test_the_configuration_page_is_a_screen_of_the_app_and_its_data_a_json_contract(self):
        """`/configuration` 是主站外壳里的一屏，表单由 island 画；真相只在 `/api/configuration`。"""
        from peach.routes_configuration import RELOAD_NAME
        with self.client() as client, mock.patch("peach.onboarding.check_available_port") as available:
            headers = {"X-Token": "test-token"}
            page = client.get("/configuration", headers=headers)
            self.assertEqual(page.status_code, 200)
            self.assertIn('data-manage-header', page.text, "配置页由 SPA 外壳承载，不是独立页面")
            self.assertTrue(client.get("/healthz").json()["configurable"])
            snapshot = client.get("/api/configuration", headers=headers).json()
            self.assertTrue(snapshot["editable"])
            self.assertEqual(snapshot["notice"], "")
            self.assertFalse(snapshot["ledger_read_only"])
            self.assertEqual(snapshot["media_dirs"], [str(self.media)])
            self.assertEqual(snapshot["port"], 9123)
            # 运行信息和首启完成页共用同一份 `runtime_facts`，不再各写一份。
            self.assertIn("版本", [fact["term"] for fact in snapshot["facts"]])
            self.config.directory("state").mkdir(parents=True, exist_ok=True)
            response = client.post("/api/configuration", headers=headers, json={
                "revision": snapshot["revision"], "media_dirs": [str(self.media)], "port": "9124"})
            self.assertEqual(response.status_code, 200, response.text)
            available.assert_called_once_with(9124, 9123)
            self.assertEqual(response.json()["url"], "http://127.0.0.1:9124/")
            self.assertEqual(settings_file.load_config().server.port, 9124)
            self.assertTrue(self.config.path.with_suffix(".previous.toml").is_file())
            self.assertTrue((self.config.directory("state") / RELOAD_NAME).is_file())
            self.assertFalse((self.config.directory("database") / "ledger.db").exists())

    def test_configuration_reports_the_ledger_role_independently_of_local_editability(self):
        from types import SimpleNamespace
        with self.client() as client:
            with mock.patch.object(client.app.state, "sync", SimpleNamespace(read_only=True), create=True):
                response = client.get("/api/configuration", headers={"X-Token": "test-token"})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["editable"])
                self.assertTrue(response.json()["ledger_read_only"])

    def test_the_snapshot_counts_libraries_the_way_the_library_switcher_groups_them(self):
        """摘要卡的媒体库数量由服务端给出：库名相同的目录算一个库，跨来源也一样。"""
        from dataclasses import replace
        from peach import media_libraries
        first, second, third = (str(self.media / name) for name in ("a", "b", "c"))
        self.config = replace(self.config, locations={"local": (first, second), "115": (third,)},
                              library_names={first: "电影", third: "电影"})
        settings_file.write(self.config, force=True)
        with self.client() as client:
            snapshot = client.get("/api/configuration", headers={"X-Token": "test-token"}).json()
        self.assertEqual(len(snapshot["media_sources"]), 3)
        self.assertEqual(snapshot["library_count"], 2)
        self.assertEqual(snapshot["library_count"], len(media_libraries.libraries(self.config)))

    def test_field_errors_come_back_per_row_and_nothing_is_written(self):
        from peach.routes_configuration import revision
        before = self.config.path.read_bytes()
        with self.client() as client:
            response = client.post("/api/configuration", headers={"X-Token": "test-token"}, json={
                "revision": revision(self.config), "media_dirs": [str(self.media), str(self.media)],
                "port": "not-a-port"})
            self.assertEqual(response.status_code, 400)
            body = response.json()
            self.assertEqual(body["error"], "有几项需要修改")
            self.assertEqual(body["errors"]["media_dirs"][0], "", "没问题的行给空串，页面据此不画红字")
            self.assertTrue(body["errors"]["media_dirs"][1], "重复的那一行要点名")
            self.assertTrue(body["errors"]["port"])
        self.assertEqual(before, self.config.path.read_bytes())

    def test_cloud_configuration_saves_all_sources_and_requests_configured_scan(self):
        from peach.routes_configuration import revision
        with self.client() as client:
            response = client.post('/api/configuration', headers={'X-Token': 'test-token'}, json={
                'revision': revision(self.config), 'port': 9123, 'scan_now': True,
                'media_sources': [
                    {'location': '115', 'path': str(self.media), 'root': 'B:/'},
                ]})
            self.assertEqual(response.status_code, 200, response.text)
            saved = settings_file.load_config()
            self.assertEqual(set(saved.locations), {'115'})
            self.assertEqual(onboarding.take_first_scan_request(saved), 'configured')
            snapshot = client.get('/api/configuration', headers={'X-Token': 'test-token'}).json()
            self.assertEqual(snapshot['media_sources'][0]['location'], '115')
            self.assertTrue(snapshot['media_sources'][0]['online'])
            self.assertFalse((saved.directory('database') / 'ledger.db').exists())

    def test_cloud_configuration_roundtrips_per_root_proxy_protection(self):
        from peach.routes_configuration import revision
        with self.client() as client:
            for location, cost in (("onedrive", "free"), ("mounted", "metered"), ("guangya", "free")):
                current = settings_file.load_config()
                response = client.post('/api/configuration', headers={'X-Token': 'test-token'}, json={
                    'revision': revision(current), 'port': 9123,
                    'media_sources': [{'location': location, 'path': str(self.media), 'root': 'O:/', 'cost': cost}]})
                self.assertEqual(response.status_code, 200, response.text)
                saved = settings_file.load_config()
                self.assertIn(cost, saved.media_costs.values())
                snapshot = client.get('/api/configuration', headers={'X-Token': 'test-token'}).json()
                self.assertEqual(snapshot['media_sources'][0]['cost'], cost)
                self.assertFalse((saved.directory('database') / 'ledger.db').exists())

    def test_settings_reject_unauthenticated_remote_cross_origin_and_stale_forms(self):
        from peach.routes_configuration import revision
        data = {"revision": revision(self.config), "media_dirs": [str(self.media)], "port": "9124"}
        before = self.config.path.read_bytes()
        with self.client() as client:
            self.assertEqual(client.get("/configuration", follow_redirects=False).status_code, 303)
            self.assertEqual(client.get("/api/configuration").status_code, 401)
            response = client.post("/api/configuration", headers={"X-Token": "test-token", "Origin": "https://example.org"}, json=data)
            self.assertEqual(response.status_code, 403)
            response = client.post("/api/configuration", headers={"X-Token": "test-token"}, json={**data, "revision": "stale"})
            self.assertEqual(response.status_code, 409)
        with self.client(address="192.0.2.1") as client:
            headers = {"X-Token": "test-token"}
            # 外壳照常打开，拒绝的原因由页面里的 Note 说；菜单里根本不会列出这一项。
            self.assertEqual(client.get("/configuration", headers=headers).status_code, 200)
            self.assertFalse(client.get("/healthz").json()["configurable"])
            response = client.get("/api/configuration", headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {"error": "请在运行 Peach 的电脑上打开配置"})
            response = client.post("/api/configuration", headers=headers, json=data)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(before, self.config.path.read_bytes())

    def test_browser_navigations_get_an_error_page_instead_of_raw_json(self):
        """地址栏里直接打开一个出错的路径，看到的是 Peach 的页面，不是一行 JSON。

        错误页由页面包画：壳里只有状态码与已换成中文的说明，标题与说明的显隐归前端。
        `/api/` 下的路径和不要 HTML 的调用方仍拿 JSON——页面脚本按 `error` 字段取原因。
        """
        from fastapi import HTTPException
        from fastapi.testclient import TestClient
        from peach.api import create_app
        from peach.config import PeachSettings
        app = create_app(PeachSettings(configured=True, token="test-token",
                                       db_path=self.config.data_root / "database" / "ledger.db"))

        def refuse(status: int):
            raise HTTPException(status, {403: "请在运行 Peach 的电脑上打开配置", 409: "请先完成首次设置"}
                                .get(status, "服务出错"))
        # 页面 catch-all 排在最后会吃掉一切路径，测试路由要插到它前面。
        app.add_api_route("/refuse/{status}", refuse, methods=["GET"])
        app.router.routes.insert(0, app.router.routes.pop())
        html = {"Accept": "text/html,application/xhtml+xml"}
        with TestClient(app, base_url="http://localhost") as client:
            for status, detail in ((403, "请在运行 Peach 的电脑上打开配置"), (409, "请先完成首次设置"),
                                   (400, "服务出错")):
                with self.subTest(status):
                    page = client.get(f"/refuse/{status}", headers=html)
                    self.assertEqual(page.status_code, status)
                    self.assertTrue(page.headers["content-type"].startswith("text/html"))
                    self.assertIn(f'<div id="peach-page" data-page="error" data-status="{status}" '
                                  f'data-detail="{detail}"></div>', page.text)
                    self.assertIn('<script type="module" src="/dist/peach-pages.js"></script>', page.text)
                    self.assertNotIn("/dist/peach-entry.js", page.text)
                    self.assertNotIn("<style", page.text)
            missing = client.get("/no-such-page", headers={"Accept": "text/html"})
            self.assertEqual(missing.status_code, 404)
            self.assertIn('data-page="error" data-status="404" data-detail="这个地址下没有页面。"', missing.text)
            data = client.get("/refuse/409", headers={"Accept": "application/json"})
            self.assertEqual(data.status_code, 409)
            self.assertEqual(data.json(), {"error": "请先完成首次设置"})
            api = client.get("/api/configuration", headers={"Accept": "text/html"})
            self.assertEqual(api.status_code, 401)
            self.assertEqual(api.headers["content-type"], "application/json")

    def test_the_folder_dialog_is_opened_by_this_machine_for_loopback_callers_only(self):
        """对话框弹在运行 Peach 的电脑上；局域网、跨站与没登录的请求都不能让它弹。"""
        from peach import folder_picker
        headers = {"X-Token": "test-token"}
        with mock.patch("peach.folder_picker.pick_folder", return_value=str(self.media)) as picker:
            with self.client() as client:
                response = client.post("/api/pick-folder", headers=headers, json={"initial": ""})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"path": str(self.media)})
                picker.assert_called_once_with(None)
                crossed = client.post("/api/pick-folder", json={},
                                      headers={**headers, "Origin": "https://example.org"})
                self.assertEqual(crossed.status_code, 403)
                self.assertEqual(client.post("/api/pick-folder", json={}).status_code, 401)
            with self.client(address="192.0.2.1") as client:
                self.assertEqual(client.post("/api/pick-folder", headers=headers, json={}).status_code, 403)
            self.assertEqual(picker.call_count, 1)
        with mock.patch("peach.folder_picker.pick_folder",
                        side_effect=folder_picker.PickerUnavailable("这个系统上没有可用的文件夹对话框")):
            with self.client() as client:
                response = client.post("/api/pick-folder", headers=headers, json={})
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["error"], "这个系统上没有可用的文件夹对话框")
        busy = folder_picker.PickerBusy("已经有一个选择文件夹的窗口开着")
        with mock.patch("peach.folder_picker.pick_folder", side_effect=busy):
            with self.client() as client:
                self.assertEqual(client.post("/api/pick-folder", headers=headers, json={}).status_code, 409)

    def test_standalone_tray_uses_its_own_binary_and_configured_loopback_port(self):
        import sys
        from peach.tray import _peach_executable, configured_service_specs, normal_url
        self.assertEqual(_peach_executable(), Path(sys.executable).resolve())
        spec, = configured_service_specs(self.config)
        self.assertEqual(spec.health_url, "http://127.0.0.1:9123/healthz")
        self.assertIn("9123", spec.command)
        self.assertIn("--no-mdns", spec.command)
        self.assertEqual(normal_url(self.config), "http://127.0.0.1:9123/")

    def test_standalone_lan_binds_all_interfaces_and_publishes_mdns(self):
        from dataclasses import replace
        from peach.tray import configured_service_specs, normal_url
        lan = replace(self.config, server=replace(
            self.config.server, host="0.0.0.0", port=9123, mdns_name="peach-test"))
        spec, = configured_service_specs(lan)
        self.assertEqual(spec.health_url, "http://127.0.0.1:9123/healthz")
        self.assertEqual(spec.command[spec.command.index("--host") + 1], "0.0.0.0")
        self.assertNotIn("--no-mdns", spec.command)
        self.assertIn("--no-ledger-sync", spec.command)
        self.assertEqual(normal_url(lan), "http://127.0.0.1:9123/")

    def test_standalone_version_inspection_does_not_execute_git(self):
        from peach.versioning import VersionManager
        execute = mock.Mock(side_effect=AssertionError("Git must not run"))
        manager = VersionManager(execute=execute)
        self.assertEqual(manager.inspect().branch, "测试包")
        with mock.patch("peach.release_updates.check", return_value={"state":"available", "latest_version":"1.0.0", "message":"有新版本可下载。"}):
            self.assertEqual(manager.check().state, "available")
            self.assertEqual(manager.update().state, "available")
        self.assertIn("GitHub", manager.inspect().channel_label)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
