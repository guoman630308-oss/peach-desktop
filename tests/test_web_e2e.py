# -*- coding: utf-8 -*-
"""真浏览器冒烟：临时 Peach 数据目录上的合成演示库，交给 `frontend/e2e` 的 playwright-core 用例。

页面源断言守不住布局之后才成立的事实（横向溢出、等待态卡住、控制台报错、失败请求），
靠手动开浏览器逐轮重测的话，既费时又不稳定。这里把它们固定成确定性的用例：Python 这一侧准备
数据与服务，Node 那一侧只认 `PEACH_E2E_ORIGIN`。

数据全在临时目录：`scripts/demo_dataset.py` 生成 SFW 合成库，账本来自迁移模板，
设置文件把 `local` 的数据库路径前缀写成演示库目录。Windows 上 `[media.mounts]` 不参与
路径转换，若使用程序默认的前缀，测试进程会读到这台机器的真实媒体目录。

短片由 ffmpeg 编码成真能解码的 2 秒片段：详情页的播放器会预加载源，占位字节只会让
`/stream` 返回 503。缺 Node、`playwright-core`、ffmpeg 或本机 Chrome 时，本机显式跳过，
CI（`GITHUB_ACTIONS=true`）判失败，与 vitest 同一口径；浏览器不另外下载，
`PEACH_E2E_CHROME` 可以指定可执行文件。CI 的 `web-e2e` job 负责装齐这四样。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest import mock

from peach import follow_assets, link_marks, scraping_access, settings_file
from peach import platform as peach_platform
from peach.config import FFMPEG_DIR
from peach.ffmpeg import FFmpegResolver
from peach.library_processing import process_library
from support.conditions import missing_prerequisite, windows_ledger_roots
from support.ledger import fresh_ledger
from support.demo import seed_metadata_cache

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BROWSER_LOGS = ROOT / "build" / "agent-verification" / "browser"
SERVER_START_SECONDS = 60
E2E_SECONDS = 600
E2E_BATCH_FILES = 12
SETUP_SUITE = "e2e/setup.test.ts"


def chrome_executable() -> str | None:
    """本机 Chrome 的可执行文件；找不到返回 None。"""
    explicit = os.environ.get("PEACH_E2E_CHROME", "").strip()
    if explicit:
        return explicit if Path(explicit).is_file() else None
    candidates: list[Path] = []
    if os.name == "nt":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    else:
        for name in ("google-chrome", "google-chrome-stable"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    return next((str(path) for path in candidates if path.is_file()), None)


def load_demo_script():
    path = ROOT / "scripts" / "demo_dataset.py"
    spec = importlib.util.spec_from_file_location("peach_script_demo_dataset_e2e", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def refuse_network(*args, **kwargs):
    raise AssertionError("演示库的 process 不应向任何外部来源发请求")


#: 测量动画中间帧、密度切换和等待态替换的文件。独立测量这些时序，避免其他浏览器
#: 的渲染与共享设置请求影响取值时刻；其余文件保留并行。
TIMING_SENSITIVE_SUITES = frozenset({
    "e2e/sidebar-motion.test.ts",  # 开合过渡 300ms 里的帧数
    "e2e/sidebar.test.ts",         # 当前项那块玻璃 900ms 内要经过中间位置
    "e2e/design-cards.test.ts",     # 紧凑密度切换的即时样式
    "e2e/design-follow-alignment.test.ts",  # 等待标题与内容标题的替换
})


def e2e_concurrency(override: str = "") -> int:
    """浏览器用例文件的并发数：`PEACH_E2E_CONCURRENCY` 优先，CI 为 1，本机按 CPU 数推导、上限 4。

    每个文件启动独立的 Chrome，使用 Playwright 的默认浏览器参数；
    上限 4 与 vitest 的 `maxWorkers` 同一份进程预算。
    """
    if override:
        if not override.isdecimal() or int(override) < 1:
            raise AssertionError("PEACH_E2E_CONCURRENCY 必须是正整数")
        return int(override)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return 1
    return max(1, min(4, (os.cpu_count() or 1) // 4))


def e2e_command(node: str, concurrency: int, files: tuple[str, ...]) -> list[str]:
    """直接启动 Node，省掉资源守卫内的一层 npm 进程。"""
    return [node, "--test", f"--test-concurrency={concurrency}", "--test-reporter=tap", *files]


def e2e_batches(frontend: Path, concurrency: int, selected: tuple[str, ...] = ()) -> tuple[tuple[tuple[str, ...], int], ...]:
    """把已配置服务的 `*.test.ts`（含子目录）分批，每批带自己的并发数，限时 600 秒。

    并发批最多 12 个文件，串行批每批一个文件。并发批之后运行时序敏感的串行批；并发为 1 时，
    按设计决定、交互回归与路由冒烟分组。首启用例由 `SetupE2ETests` 在专用服务执行。
    """
    files = tuple(sorted(path.relative_to(frontend).as_posix()
                         for path in (frontend / "e2e").rglob("*.test.ts")))
    if not files:
        raise AssertionError("frontend/e2e 没有浏览器用例")
    if selected:
        missing = set(selected) - set(files)
        if missing:
            raise AssertionError(f"浏览器用例不存在：{sorted(missing)}")
        files = tuple(path for path in files if path in selected)
    files = tuple(path for path in files if path != SETUP_SUITE)
    if concurrency == 1:
        design = tuple(path for path in files if path.startswith("e2e/design-"))
        routes = tuple(path for path in files if path == "e2e/smoke.test.ts")
        interactions = tuple(path for path in files if path not in design + routes)
        groups = ((design, 1), (interactions, 1), (routes, 1))
    else:
        sensitive = tuple(path for path in files if path in TIMING_SENSITIVE_SUITES)
        shared = tuple(path for path in files if path not in sensitive)
        groups = ((shared, concurrency), (sensitive, 1))
    batches = []
    for group, lanes in groups:
        size = E2E_BATCH_FILES if lanes > 1 else 1
        batches.extend((group[start:start + size], lanes)
                       for start in range(0, len(group), size))
    return tuple(batches)


@windows_ledger_roots
class WebE2ESmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if node is None:
            missing_prerequisite("跳过 e2e：本机没有 Node。装 Node 24+ 后 `-Scope web` 会带上它")
        if not (FRONTEND / "node_modules" / "playwright-core").is_dir():
            missing_prerequisite("跳过 e2e：frontend/node_modules 还没装，先 `npm --prefix frontend ci`")
        ffmpeg = FFmpegResolver(FFMPEG_DIR).ffmpeg()
        if ffmpeg is None:
            missing_prerequisite("跳过 e2e：没找到 ffmpeg，演示库的短片编码不出来")
        chrome = chrome_executable()
        if chrome is None:
            missing_prerequisite("跳过 e2e：没找到 Chrome；装 Google Chrome 或用 PEACH_E2E_CHROME 指定")
        cls.node, cls.ffmpeg, cls.chrome = node, str(ffmpeg.path), chrome
        cls.root = Path(tempfile.mkdtemp(prefix="peach-e2e-")).resolve()
        cls.server = None
        try:
            cls._build_library()
            cls._start_server()
            cls._warm_stream()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        server = getattr(cls, "server", None)
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        if getattr(cls, "log", None) is not None:
            cls.log.close()
            # 访问日志留在 TAP 旁边：哪条用例向服务端发了写请求，用例跑完之后还查得到。
            BROWSER_LOGS.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cls.root / "serve.log", BROWSER_LOGS / "serve.log")
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _build_library(cls):
        media = cls.root / "media"
        load_demo_script().generate(media, count=12, seed=7, video="ffmpeg", duration=2,
                                    ffmpeg=cls.ffmpeg)
        data = cls.root / "peach-data"
        (data / "database").mkdir(parents=True)
        cls.db = fresh_ledger(data / "database")
        cls.port = free_port()
        config = settings_file.PeachConfig(
            data, data / settings_file.SETTINGS_FILENAME, present=True, data_root_found=True,
            locations={"local": (str(media),)},
            server=settings_file.ServerSettings(port=cls.port),
        )
        settings_file.write(config)
        generated = config.directory("generated")
        # 路由冒烟只验页面与站标端点的衔接；取图算法另有 mock 契约，不让这轮布局测试依赖外网。
        # 站标与来源图标两处缓存都预先填上同一张图：缓存为空时 `/site-mark` 与 `/source-icon`
        # 会在请求时去各站拉图，站点或代理一抖就 404，用例把 404 记成问题，整轮就红。
        mark_root = generated / "site-marks"
        mark_root.mkdir(parents=True)
        mark = (ROOT / "resources" / "peach-logo.png").read_bytes()
        for spec in scraping_access.SOURCES.values():
            # 聚合来源没有固定站点，页面使用本地字形，不请求站标。
            if not spec["login"]:
                continue
            cached = link_marks.cached_path(mark_root, spec["login"])
            if cached is None:
                raise AssertionError(f"采集来源没有可缓存的主机：{spec['login']}")
            cached.write_bytes(mark)
        icon_root = generated / follow_assets.ROOT_NAME
        for provider in follow_assets.SOURCE_ICON_URLS:
            cached = follow_assets.cache_path(icon_root, "icons", provider)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(mark)
        seed_metadata_cache(media, config.directory('sources'))
        result = process_library(config, cls.db, generated, generated / "covers",
                                 provider_factory=refuse_network)
        if result["status"] != "complete":
            raise AssertionError(f"演示库 process 没有完成：{result}")
        with closing(sqlite3.connect(cls.db)) as connection:
            row = connection.execute(
                "SELECT id FROM asset WHERE medium='video' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            raise AssertionError("演示库 process 之后数据库里没有视频")
        cls.data, cls.item = data, row[0]

    @classmethod
    def _start_server(cls):
        env = dict(os.environ, PEACH_DATA_ROOT=str(cls.data), PYTHONIOENCODING="utf-8")
        cls.log = (cls.root / "serve.log").open("w", encoding="utf-8")
        cls.server = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "peach", "serve", "--host", "127.0.0.1",
             "--port", str(cls.port), "--db", str(cls.db),
             "--no-auth", "--no-mdns", "--no-ledger-sync"],
            cwd=str(ROOT), env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        cls.origin = f"http://127.0.0.1:{cls.port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + SERVER_START_SECONDS
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise AssertionError(f"服务提前退出：{cls._server_log()}")
            try:
                with opener.open(f"{cls.origin}/healthz", timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        raise AssertionError(f"{SERVER_START_SECONDS} 秒内 /healthz 没有就绪：{cls._server_log()}")

    @classmethod
    def _server_log(cls) -> str:
        cls.log.flush()
        return (cls.root / "serve.log").read_text(encoding="utf-8", errors="replace")[-4000:]

    @classmethod
    def _warm_stream(cls):
        """在 Chrome 启动前预热演示短片的实际播放链。"""
        with closing(sqlite3.connect(cls.db)) as connection:
            items = connection.execute("SELECT id FROM asset WHERE medium='video' ORDER BY id LIMIT 33").fetchall()
        if len(items) > 32:
            raise AssertionError("演示短片超过预热上限")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for (item,) in items:
            cls._warm_clip(opener, item)

    @classmethod
    def _warm_clip(cls, opener, item):
        session = f"e2e-prewarm-{os.getpid()}-{item}"
        query = urllib.parse.urlencode({"id": item, "session": session})
        try:
            with opener.open(f"{cls.origin}/api/stream-plan?{query}", timeout=60) as response:
                plan = json.load(response)
            source = cls.origin + plan['src']
            with opener.open(source, timeout=60) as response:
                payload = response.read()
            if plan['protocol'] == 'hls':
                segments = [line for line in payload.decode().splitlines() if line and not line.startswith('#')]
                if not segments or len(segments) > 8:
                    raise AssertionError("演示短片分片数量不符合预热预算")
                for segment in segments:
                    with opener.open(urllib.parse.urljoin(source, segment), timeout=60) as response:
                        response.read()
        except (urllib.error.URLError, OSError) as error:
            raise AssertionError(f"演示短片预热失败：{error}\n{cls._server_log()}") from error
        finally:
            cancel = urllib.request.Request(
                f"{cls.origin}/api/stream-cancel?{urllib.parse.urlencode({'session': session})}",
                method="POST", headers={"Origin": cls.origin})
            try:
                with opener.open(cancel, timeout=5) as response:
                    if response.status != 200:
                        raise AssertionError(f"演示短片取消失败：HTTP {response.status}")
            except (urllib.error.URLError, OSError) as error:
                raise AssertionError(f"演示短片取消失败：{error}") from error

    def test_every_route_holds_the_layout_and_runtime_invariants(self):
        env = dict(os.environ, PEACH_E2E_ORIGIN=self.origin, PEACH_E2E_ITEM=str(self.item), PEACH_E2E_FFMPEG=self.ffmpeg,
                   PEACH_E2E_CHROME=self.chrome, PEACH_E2E_NET_LOG_DIR=str(BROWSER_LOGS / "network"))
        BROWSER_LOGS.mkdir(parents=True, exist_ok=True)
        for stale in BROWSER_LOGS.glob("batch-*.tap"):
            stale.unlink()
        concurrency = e2e_concurrency(os.environ.get("PEACH_E2E_CONCURRENCY", "").strip())
        selected = tuple(json.loads(os.environ.get("PEACH_E2E_FILES", "[]")))
        batches = e2e_batches(FRONTEND, concurrency, selected)
        for index, (batch, lanes) in enumerate(batches, 1):
            print(f"浏览器批次 {index}/{len(batches)}：{', '.join(batch)}", flush=True)
            log_path = BROWSER_LOGS / f"batch-{index}.tap"
            try:
                completed = subprocess.run(
                    e2e_command(self.node, lanes, batch),
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=str(FRONTEND), env=env, timeout=E2E_SECONDS, check=False)
            except subprocess.TimeoutExpired as expired:
                partial = expired.stdout or ""
                if isinstance(partial, bytes):
                    partial = partial.decode("utf-8", errors="replace")
                log_path.write_text(partial, encoding="utf-8")
                self.fail(f"本批 {E2E_SECONDS} 秒内没跑完，完整日志：{log_path}\n{partial[-4000:]}")
            output = f"{completed.stdout}\n{completed.stderr}"
            log_path.write_text(output, encoding="utf-8")
            self.assertEqual(completed.returncode, 0, f"{output}\n--- serve.log ---\n{self._server_log()}")
            self.assertRegex(output, r"# pass [1-9]\d*", output)
            self.assertRegex(output, r"# fail 0\b", output)


class SetupE2ETests(unittest.TestCase):
    """首次运行页跑在 `serve --setup` 上：临时 Peach 数据目录里还没有设置文件，提交成功后才有。

    托盘拉起引导服务用的就是这组参数（`tray.build_setup_service_specs`）。页面那一侧是
    `frontend/e2e/setup.test.ts`，只由本组在首启服务执行。
    """

    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if node is None:
            missing_prerequisite("跳过首启 e2e：本机没有 Node")
        if not (FRONTEND / "node_modules" / "playwright-core").is_dir():
            missing_prerequisite("跳过首启 e2e：frontend/node_modules 还没装，先 `npm --prefix frontend ci`")
        chrome = chrome_executable()
        if chrome is None:
            missing_prerequisite("跳过首启 e2e：没找到 Chrome；装 Google Chrome 或用 PEACH_E2E_CHROME 指定")
        cls.node, cls.chrome = node, chrome
        cls.root = Path(tempfile.mkdtemp(prefix="peach-e2e-setup-")).resolve()
        cls.data = cls.root / "peach-data"
        cls.media = cls.root / "media"
        cls.media.mkdir()
        cls.ledger_root = str(cls.media) if os.name == "nt" else "R:\\media"
        cls.port = free_port()
        cls.origin = f"http://127.0.0.1:{cls.port}"
        env = dict(os.environ, PEACH_DATA_ROOT=str(cls.data), PYTHONIOENCODING="utf-8")
        cls.log = (cls.root / "serve.log").open("w", encoding="utf-8")
        cls.server = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "peach", "serve", "--setup", "--host", "127.0.0.1",
             "--port", str(cls.port), "--no-mdns", "--no-ledger-sync"],
            cwd=str(ROOT), env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        try:
            cls._wait_ready()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _wait_ready(cls):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + SERVER_START_SECONDS
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise AssertionError(f"首启服务提前退出：{cls._server_log()}")
            try:
                with opener.open(f"{cls.origin}/healthz", timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        raise AssertionError(f"{SERVER_START_SECONDS} 秒内首启服务没有就绪：{cls._server_log()}")

    @classmethod
    def tearDownClass(cls):
        server = getattr(cls, "server", None)
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        if getattr(cls, "log", None) is not None:
            cls.log.close()
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _server_log(cls) -> str:
        cls.log.flush()
        return (cls.root / "serve.log").read_text(encoding="utf-8", errors="replace")[-4000:]

    def test_the_first_run_page_writes_errors_back_and_finishes(self):
        self.assertFalse((self.data / settings_file.SETTINGS_FILENAME).exists())
        env = dict(os.environ, PEACH_E2E_SETUP_ORIGIN=self.origin, PEACH_E2E_SETUP_MEDIA=str(self.media),
                   PEACH_E2E_SETUP_DATA=str(self.data), PEACH_E2E_SETUP_LEDGER_ROOT=self.ledger_root,
                   PEACH_E2E_CHROME=self.chrome, PEACH_E2E_NET_LOG_DIR=str(BROWSER_LOGS / "network"))
        BROWSER_LOGS.mkdir(parents=True, exist_ok=True)
        log_path = BROWSER_LOGS / "setup.tap"
        try:
            completed = subprocess.run(
                e2e_command(self.node, 1, (SETUP_SUITE,)),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(FRONTEND), env=env, timeout=E2E_SECONDS, check=False)
        except subprocess.TimeoutExpired as expired:
            partial = expired.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", errors="replace")
            log_path.write_text(partial, encoding="utf-8")
            self.fail(f"首启 e2e {E2E_SECONDS} 秒内没跑完，完整日志：{log_path}\n{partial[-4000:]}")
        output = f"{completed.stdout}\n{completed.stderr}"
        log_path.write_text(output, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, f"{output}\n--- serve.log ---\n{self._server_log()}")
        self.assertRegex(output, r"# pass 1\b", output)
        self.assertRegex(output, r"# skipped 0\b", output)
        # 改对的那一次提交在临时 Peach 数据目录里落了设置文件，媒体库就是传进去的那个目录。
        written = settings_file.load_config(environ={settings_file.DATA_ROOT_ENV: str(self.data)})
        self.assertTrue(written.present)
        if os.name == "nt":
            self.assertEqual(list(written.locations.get("local", ())), [self.ledger_root])
        else:
            self.assertEqual(len(written.locations["local"]), 1)
            self.assertTrue(peach_platform.is_windows_path(written.locations["local"][0]))
            self.assertEqual([Path(path) for path in written.mounts.get("local", ())], [self.media])


class MissingPrerequisiteTests(unittest.TestCase):
    """CI 里浏览器用例只能执行或失败，不能静默跳过；工作流那一半由 `test_frontend_build.py` 守。"""

    def test_demo_prewarm_fetches_each_clips_hls_segments_and_cancels_sessions(self):
        from io import BytesIO
        fetched, cancelled = [], []
        opener = mock.Mock()

        def open_response(request, **kwargs):
            if isinstance(request, urllib.request.Request):
                cancelled.append(urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)['session'][0])
                payload = b''
            else:
                parsed = urllib.parse.urlsplit(request)
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path == '/api/stream-plan':
                    item, session = query['id'][0], query['session'][0]
                    payload = json.dumps({'protocol':'hls','src':f'/stream/hls/{item}/index.m3u8?session={session}'}).encode()
                elif parsed.path.endswith('index.m3u8'):
                    payload = f"#EXTM3U\n0.ts?session={query['session'][0]}\n".encode()
                else:
                    fetched.append(parsed.path)
                    payload = b'segment'
            response = BytesIO(payload)
            response.status = 200
            return response

        opener.open.side_effect = open_response
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder).resolve() / 'demo.db'
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('CREATE TABLE asset(id INTEGER,medium TEXT)')
                connection.executemany('INSERT INTO asset VALUES(?,?)',[(1,'video'),(2,'photo'),(3,'video')])
            with mock.patch.multiple(WebE2ESmokeTests,db=db,origin='http://demo.test',create=True), mock.patch('urllib.request.build_opener',return_value=opener):
                WebE2ESmokeTests._warm_stream()
        self.assertEqual(fetched,['/stream/hls/1/0.ts','/stream/hls/3/0.ts'])
        self.assertEqual(len(cancelled),2)
        self.assertEqual(len(set(cancelled)),2)

    PATHS = ("e2e/design-cards.test.ts", "e2e/design-detail.test.ts", "e2e/smoke.test.ts",
             "e2e/nested/feature.test.ts", "e2e/sidebar-motion.test.ts")

    def batches_for(self, concurrency: int, paths=None):
        with tempfile.TemporaryDirectory() as folder:
            frontend = Path(folder).resolve()
            for name in (*(self.PATHS if paths is None else paths), "e2e/harness.ts"):
                path = frontend / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            return e2e_batches(frontend, concurrency)

    def assertEveryFileOnce(self, batches):
        flattened = tuple(path for files, _ in batches for path in files)
        self.assertCountEqual(flattened, self.PATHS)

    def test_concurrent_run_puts_timing_sensitive_suites_in_a_trailing_serial_batch(self):
        batches = self.batches_for(4)
        self.assertEveryFileOnce(batches)
        (shared, lanes), *serial = batches
        self.assertEqual(lanes, 4)
        self.assertEqual(serial, [(("e2e/design-cards.test.ts",), 1),
                                  (("e2e/sidebar-motion.test.ts",), 1)])
        self.assertIn("e2e/nested/feature.test.ts", shared)

    def test_serial_run_splits_design_interactions_and_routes_to_stay_within_the_time_limit(self):
        batches = self.batches_for(1)
        self.assertEveryFileOnce(batches)
        self.assertEqual([files for files, _ in batches], [
            ("e2e/design-cards.test.ts",), ("e2e/design-detail.test.ts",),
            ("e2e/nested/feature.test.ts",), ("e2e/sidebar-motion.test.ts",),
            ("e2e/smoke.test.ts",),
        ])
        self.assertEqual({lanes for _, lanes in batches}, {1})

    def test_serial_batches_bound_large_groups_and_run_each_file_once(self):
        paths = (tuple(f"e2e/design-{index:02}.test.ts" for index in range(13))
                 + tuple(f"e2e/feature-{index:02}.test.ts" for index in range(25))
                 + ("e2e/smoke.test.ts",))
        batches = self.batches_for(1, paths=paths)
        self.assertCountEqual(tuple(path for files, _ in batches for path in files), paths)
        self.assertEqual([len(files) for files, _ in batches], [1] * len(paths))
        self.assertEqual({lanes for _, lanes in batches}, {1})

    def test_concurrent_batches_cover_large_groups_once_with_the_requested_concurrency(self):
        paths = (tuple(f"e2e/feature-{index:02}.test.ts" for index in range(25))
                 + ("e2e/smoke.test.ts", "e2e/design-cards.test.ts", "e2e/sidebar-motion.test.ts"))
        batches = self.batches_for(4, paths=paths)
        self.assertCountEqual(tuple(path for files, _ in batches for path in files), paths)
        self.assertEqual([len(files) for files, _ in batches], [12, 12, 2, 1, 1])
        self.assertEqual([lanes for _, lanes in batches], [4, 4, 4, 1, 1])

    def test_browser_batches_require_at_least_one_suite(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(AssertionError, "没有浏览器用例"):
                e2e_batches(Path(folder).resolve(), 4)

    def test_setup_suite_is_owned_by_the_first_run_service(self):
        paths = (*self.PATHS, SETUP_SUITE)
        for concurrency in (1, 4):
            with self.subTest(concurrency=concurrency):
                self.assertEveryFileOnce(self.batches_for(concurrency, paths=paths))
        with tempfile.TemporaryDirectory() as folder:
            frontend = Path(folder).resolve()
            for name in paths:
                path = frontend / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual(e2e_batches(frontend, 1, (SETUP_SUITE,)), ())
            self.assertEqual(e2e_batches(frontend, 1, (SETUP_SUITE, self.PATHS[0])),
                             (((self.PATHS[0],), 1),))

    def test_focused_batches_only_collect_named_browser_files(self):
        with tempfile.TemporaryDirectory() as folder:
            frontend = Path(folder).resolve()
            for name in self.PATHS:
                path = frontend / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            chosen = ("e2e/nested/feature.test.ts",)
            self.assertEqual(e2e_batches(frontend, 1, chosen), ((chosen, 1),))
            with self.assertRaisesRegex(AssertionError, "不存在"):
                e2e_batches(frontend, 1, ("e2e/missing.test.ts",))

    def test_failed_browser_batch_stops_following_batches_and_keeps_its_log(self):
        with tempfile.TemporaryDirectory() as folder:
            log_root = Path(folder).resolve()
            case = WebE2ESmokeTests("test_every_route_holds_the_layout_and_runtime_invariants")
            case.origin, case.item, case.chrome, case.node = "http://fixture", 1, "chrome", "node"
            case.ffmpeg = "ffmpeg"
            failed = subprocess.CompletedProcess([], 1, "# pass 1\n# fail 1\n", "failure")
            batches = ((("e2e/first.test.ts",), 1), (("e2e/second.test.ts",), 1))
            with mock.patch.dict(os.environ, {"PEACH_E2E_FILES": "[]"}), \
                    mock.patch(__name__ + ".BROWSER_LOGS", log_root), \
                    mock.patch(__name__ + ".e2e_batches", return_value=batches), \
                    mock.patch.object(case, "_server_log", return_value="serve fixture"), \
                    mock.patch("subprocess.run", return_value=failed) as run:
                with self.assertRaises(AssertionError):
                    case.test_every_route_holds_the_layout_and_runtime_invariants()
            self.assertEqual(run.call_count, 1)
            self.assertIn("# fail 1", (log_root / "batch-1.tap").read_text(encoding="utf-8"))
            self.assertFalse((log_root / "batch-2.tap").exists())

    def test_concurrency_is_serial_on_ci_bounded_locally_and_overridable(self):
        local = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
        with mock.patch.dict(os.environ, local, clear=True):
            for cpus, expected in ((None, 1), (2, 1), (8, 2), (16, 4), (64, 4)):
                with self.subTest(cpus=cpus), mock.patch("os.cpu_count", return_value=cpus):
                    self.assertEqual(e2e_concurrency(), expected)
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), mock.patch("os.cpu_count", return_value=16):
            self.assertEqual(e2e_concurrency(), 1)
            self.assertEqual(e2e_concurrency("3"), 3)
        for invalid in ("0", "-1", "two"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(AssertionError, "必须是正整数"):
                e2e_concurrency(invalid)

    def test_missing_prerequisites_skip_locally_and_fail_on_ci(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaisesRegex(AssertionError, "没有 npm"):
                missing_prerequisite("没有 npm")
        local = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
        with mock.patch.dict(os.environ, local, clear=True):
            with self.assertRaises(unittest.SkipTest):
                missing_prerequisite("没有 npm")


if __name__ == "__main__":
    unittest.main()
