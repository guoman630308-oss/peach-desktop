"""经本机浏览器取页（ADR-0065）：对着假的 CDP 服务验证握手、帧、验证页三态、年龄门与进程生命周期。"""
import base64
import hashlib
import json
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from peach import browser_transport
from peach.browser_transport import (BrowserTransport, BrowserUnavailable, ChallengeUnsolved, PageTimeout,
                                     _WebSocket, find_browser, proxy_flags)
from peach.http import HttpRequest

PAGE_HTML = "<html><head><title>FC2-PPV-1 | FC2PPV Database</title></head><body>ok</body></html>"


def document_reply(url: str, html: str = PAGE_HTML, status: int = 200, kind: str = "text/html") -> str:
    return json.dumps({"url": url, "type": kind, "status": status, "html": html})


def state_reply(title: str, ready: str = "complete", head: str = "<html>", url: str = "https://x/") -> str:
    return json.dumps({"title": title, "ready": ready, "head": head, "url": url})


def is_state(params) -> bool:
    return "document.readyState" in params["expression"]


def is_document(params) -> bool:
    return "getEntriesByType" in params["expression"]


class FakeCdp(threading.Thread):
    """一个端口同时充当 `/json` 目标列表和页面的 WebSocket 端：回包按 `handler(method, params)`。"""

    def __init__(self, handler):
        super().__init__(daemon=True)
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(5)
        self.port = self.listener.getsockname()[1]
        self.start()

    def targets(self, port: int) -> list[dict]:
        assert port == self.port
        return [{"type": "page", "id": "T1", "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/page/T1"}]

    def run(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(connection,), daemon=True).start()

    def serve(self, connection: socket.socket):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = connection.recv(4096)
            if not chunk:
                return
            head += chunk
        request, rest = head.split(b"\r\n\r\n", 1)
        key = next(line.split(b":", 1)[1].strip() for line in request.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        buffer = rest
        while True:
            frame, buffer = self.read_frame(connection, buffer)
            if frame is None:
                connection.close()
                return
            message = json.loads(frame)
            self.calls.append((message["method"], message.get("params", {})))
            result = self.handler(message["method"], message.get("params", {}))
            reply = json.dumps({"id": message["id"], "result": result}).encode()
            length = len(reply)
            if length < 126:
                header = bytes([0x81, length])
            elif length < 65536:
                header = bytes([0x81, 126]) + struct.pack(">H", length)
            else:
                header = bytes([0x81, 127]) + struct.pack(">Q", length)
            connection.sendall(header + reply)

    @staticmethod
    def read_frame(connection, buffer):
        def need(size):
            nonlocal buffer
            while len(buffer) < size:
                chunk = connection.recv(65536)
                if not chunk:
                    raise ConnectionError
                buffer += chunk
            out, buffer = buffer[:size], buffer[size:]
            return out
        try:
            first, second = need(2)
            opcode, size = first & 0x0F, second & 0x7F
            if size == 126:
                size = struct.unpack(">H", need(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", need(8))[0]
            mask = need(4) if second & 0x80 else b"\0\0\0\0"
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(need(size)))
        except ConnectionError:
            return None, buffer
        if opcode == 0x8:
            return None, buffer
        return data.decode(), buffer

    def close(self):
        self.listener.close()


class FakeProcess:
    def __init__(self, port_file: Path, port: int):
        self.port_file, self.port, self.returncode = port_file, port, None
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class BrowserTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name) / "browser"
        self.launches: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        self.clock = Clock()

    def make(self, handler, executable="C:/fake/chrome.exe", **options) -> tuple[BrowserTransport, FakeCdp]:
        server = FakeCdp(handler)
        self.addCleanup(server.close)

        def popen(command, **_kwargs):
            self.launches.append(command)
            port_file = self.profile / "DevToolsActivePort"
            port_file.write_text(f"{server.port}\n/devtools/browser/abc\n", encoding="utf-8")
            process = FakeProcess(port_file, server.port)
            self.processes.append(process)
            return process

        transport = BrowserTransport(executable, self.profile, ("--no-proxy-server",), popen=popen,
                                     targets=server.targets, clock=self.clock, sleep=self.clock.sleep,
                                     auto_seconds=40, click_seconds=120, idle_seconds=600, **options)
        self.addCleanup(transport.close)
        return transport, server

    @staticmethod
    def generic(method, params):
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7}
        return {}

    def test_the_browser_navigates_to_the_address_and_the_document_comes_back_as_a_response(self):
        """取页就是让浏览器导航过去，DOM 解析完把最终地址、状态码与 HTML 读回来；Cookie、UA 由浏览器自己带。"""
        final = "https://javten.com/tw/video/1/id2/x"

        def handler(method, params):
            if method == "Runtime.evaluate" and is_document(params):
                return {"result": {"value": document_reply(final, PAGE_HTML, 200, "text/html")}}
            if method == "Runtime.evaluate":
                return {"result": {"value": state_reply("JAVten", "interactive", url=final)}}
            return self.generic(method, params)

        transport, server = self.make(handler)
        response = transport(HttpRequest("GET", "https://javten.com/search?kw=2",
                                         {"Cookie": "secret=1", "User-Agent": "x", "Accept-Language": "ja,en;q=0.9",
                                          "Referer": "https://javten.com/"}), 12.5, 4096)
        self.assertEqual((response.status, response.body.decode(), response.url), (200, PAGE_HTML, final))
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertNotIn("secret", json.dumps(server.calls), "Cookie 与 UA 不进浏览器，由它自己填")
        extra = [params["headers"] for method, params in server.calls if method == "Network.setExtraHTTPHeaders"]
        self.assertEqual(extra, [{"Accept-Language": "ja,en;q=0.9"}],
                         "会话上只挂语言头：JAVten 按 Accept-Language 决定回日文原页还是译文页")
        self.assertEqual([params for method, params in server.calls if method == "Page.navigate"],
                         [{"url": "https://javten.com/search?kw=2", "referrer": "https://javten.com/"}],
                         "上一跳只作主文档的 Referer：挂在会话上会让 Turnstile 勾选框的 iframe 被浏览器拦下")
        # 启动参数：调试口、关掉自动化标记与它的警告条、不同步、profile 在凭据根下、窗口在屏幕外、来源的连接方式。
        command = self.launches[0]
        for flag in ("--remote-debugging-port=0", "--disable-blink-features=AutomationControlled", "--test-type",
                     "--disable-sync", f"--user-data-dir={self.profile}", "--window-position=-32000,-32000",
                     "--no-proxy-server"):
            self.assertIn(flag, command)
        self.assertNotIn("--inprivate", command, "Chrome 不会拿系统账号登录，用持久 profile 留住 cf_clearance")
        self.assertEqual([call[0] for call in server.calls],
                         ["Page.enable", "Network.enable", "Network.setBlockedURLs", "Browser.getWindowForTarget",
                          "Network.setExtraHTTPHeaders", "Page.navigate", "Runtime.evaluate", "Runtime.evaluate"])
        blocked = server.calls[2][1]["urls"]
        self.assertTrue({"*.jpg", "*.webp", "*.woff2", "*.mp4"} <= set(blocked), "图片、字体与媒体不下载")
        transport(HttpRequest("GET", "https://fc2ppv-db.com/ja/videos/1", {}), 10, 4)
        self.assertEqual([params for method, params in server.calls if method == "Page.navigate"][1:],
                         [{"url": "https://fc2ppv-db.com/ja/videos/1"}], "每条请求各导航一次；没有上一跳就不传 referrer")
        with self.assertRaises(BrowserUnavailable):
            transport(HttpRequest("POST", "https://javten.com/", {}), 10, 4096)

    def test_an_attended_window_opens_on_screen_keeps_its_profile_and_loads_everything(self):
        """给人登录用的窗口（ADR-0093）：开在屏幕内、Edge 也不用 InPrivate，图片与字体照常加载。"""
        server = FakeCdp(self.generic)
        self.addCleanup(server.close)

        def popen(command, **_kwargs):
            self.launches.append(command)
            (self.profile / "DevToolsActivePort").write_text(f"{server.port}\n/devtools/browser/abc\n", encoding="utf-8")
            return FakeProcess(self.profile / "DevToolsActivePort", server.port)

        browser = browser_transport._Browser("C:/fake/msedge.exe", self.profile, (), popen=popen,
                                             targets=server.targets, sleep=self.clock.sleep, attended=True)
        browser.start()
        self.addCleanup(browser.close)
        command = self.launches[0]
        self.assertIn(f"--window-position={browser_transport.ONSCREEN[0]},{browser_transport.ONSCREEN[1]}", command)
        self.assertIn(f"--user-data-dir={self.profile}", command)
        self.assertNotIn("--inprivate", command, "登录要的 localStorage 必须落在 profile 里")
        self.assertNotIn("Network.setBlockedURLs", [method for method, _params in server.calls],
                         "登录页和人机验证靠图片与字体渲染")

    def test_the_body_is_cut_at_the_limit_and_a_missing_status_reads_as_ok(self):
        def handler(method, params):
            if method == "Runtime.evaluate" and is_document(params):
                return {"result": {"value": json.dumps({"url": "https://javten.com/", "type": "text/html",
                                                        "status": 0, "html": "字" * 100})}}
            if method == "Runtime.evaluate":
                return {"result": {"value": state_reply("JAVten")}}
            return self.generic(method, params)

        transport, _server = self.make(handler)
        response = transport(HttpRequest("GET", "https://javten.com/", {}), 10, 30)
        self.assertEqual((response.status, len(response.body)), (200, 31), "多读一个字节让调用方判断超限")

    def test_a_challenge_page_is_waited_out_without_touching_the_window(self):
        """导航落到验证页：每秒看一次，浏览器自己过了就读文档；自动过的验证不动窗口、不提醒。"""
        states = iter([state_reply("Just a moment...", "interactive"), state_reply("请稍候…"),
                       state_reply("FC2-PPV-1 | FC2PPV Database", url="https://fc2ppv-db.com/ja/videos/1")])

        def handler(method, params):
            if method == "Runtime.evaluate" and is_document(params):
                return {"result": {"value": document_reply("https://fc2ppv-db.com/ja/videos/1")}}
            if method == "Runtime.evaluate":
                return {"result": {"value": next(states)}}
            return self.generic(method, params)

        transport, server = self.make(handler)
        response = transport(HttpRequest("GET", "https://fc2ppv-db.com/ja/videos/1", {}), 10, 4096)
        self.assertEqual((response.status, response.body.decode()), (200, PAGE_HTML))
        self.assertEqual(len([1 for method, _ in server.calls if method == "Page.navigate"]), 1)
        self.assertFalse(any(method == "Browser.setWindowBounds" for method, _ in server.calls))
        self.assertFalse(any(method == "Network.deleteCookies" for method, _ in server.calls), "几秒就过的验证不动 cookie")
        self.assertEqual(browser_transport.attention(), [])

    def test_a_stuck_challenge_gets_the_sites_cookies_cleared_and_reloaded_before_anyone_is_asked(self):
        """验证页转了 15 秒还没过：只删这一站的 cookie、重新导航，重载后过了就照常读文档，窗口不动、不提醒。"""
        cleared_at = []

        def handler(method, params):
            if method == "Network.getCookies":
                return {"cookies": [{"name": "cf_clearance", "domain": "javten.com", "path": "/"},
                                    {"name": "cf_chl_rc_ni", "domain": "javten.com", "path": "/"}]}
            if method == "Network.deleteCookies":
                cleared_at.append(self.clock.now - started)
            if method == "Runtime.evaluate" and is_document(params):
                return {"result": {"value": document_reply("https://javten.com/search?kw=1")}}
            if method == "Runtime.evaluate":
                if cleared_at and self.clock.now - started >= cleared_at[-1] + 5:
                    return {"result": {"value": state_reply("JAVten", url="https://javten.com/search?kw=1")}}
                return {"result": {"value": state_reply("しばらくお待ちください...", head="<script src='/cdn-cgi/challenge-platform/h/b'>")}}
            return self.generic(method, params)

        transport, server = self.make(handler)
        started = self.clock.now
        response = transport(HttpRequest("GET", "https://javten.com/search?kw=1", {"Referer": "https://javten.com/"}),
                             45, 4096)
        self.assertEqual(response.status, 200)
        self.assertEqual(cleared_at, [15, 15], "转 15 秒清一次，两条 cookie 都删")
        self.assertEqual([params for method, params in server.calls if method == "Network.getCookies"],
                         [{"urls": ["https://javten.com/search?kw=1"]}], "只查这一站的 cookie")
        self.assertEqual(sorted(params["name"] for method, params in server.calls if method == "Network.deleteCookies"),
                         ["cf_chl_rc_ni", "cf_clearance"])
        self.assertEqual([params for method, params in server.calls if method == "Page.navigate"],
                         [{"url": "https://javten.com/search?kw=1", "referrer": "https://javten.com/"}] * 2,
                         "清完重新导航一次，重载照样带上一跳")
        self.assertFalse(any(method == "Browser.setWindowBounds" for method, _ in server.calls), "重载后过了就不惊动人")
        self.assertEqual(browser_transport.attention(), [])
        self.assertEqual(transport._unsolved, set())

    def test_a_page_mid_navigation_counts_as_loading(self):
        """跳转那一瞬执行环境被销毁、脚本报错：当作还在加载，下一秒再看。"""
        answers = iter([{"exceptionDetails": {"text": "Execution context was destroyed"}},
                        {"result": {"value": state_reply("JAVten", url="https://javten.com/tw/x")}}])

        def handler(method, params):
            if method == "Runtime.evaluate" and is_document(params):
                return {"result": {"value": document_reply("https://javten.com/tw/x")}}
            if method == "Runtime.evaluate":
                return next(answers)
            return self.generic(method, params)

        transport, _server = self.make(handler)
        self.assertEqual(transport(HttpRequest("GET", "https://javten.com/x", {}), 10, 4096).status, 200)

    def test_a_challenge_that_needs_a_click_shows_the_window_and_asks_for_attention(self):
        attention_seen = []

        def handler(method, params):
            if method == "Runtime.evaluate" and is_document(params):
                return {"result": {"value": document_reply("https://fc2ppv-db.com/ja/videos/1")}}
            if method == "Runtime.evaluate":
                if self.clock.now - started >= 60:
                    attention_seen.append(browser_transport.attention())
                    return {"result": {"value": state_reply("FC2PPV Database", url="https://fc2ppv-db.com/ja/videos/1")}}
                return {"result": {"value": state_reply("Just a moment...")}}
            return self.generic(method, params)

        transport, server = self.make(handler)
        started = self.clock.now
        transport(HttpRequest("GET", "https://fc2ppv-db.com/ja/videos/1", {}), 10, 4096)
        bounds = [params["bounds"] for method, params in server.calls if method == "Browser.setWindowBounds"]
        self.assertEqual(bounds, [{"windowState": "normal"}, {"left": 200, "top": 100, "width": 1000, "height": 800},
                                  {"windowState": "minimized"}], "先放回屏幕内，过了再收起")
        self.assertIn("Page.bringToFront", [method for method, _ in server.calls])
        self.assertEqual(attention_seen[0], ["fc2ppv-db.com 的人机验证需要点一下，浏览器窗口已打开"])
        self.assertEqual(browser_transport.attention(), [], "过了就撤回提醒")

    def test_an_unsolved_challenge_is_reported_and_the_window_is_put_away(self):
        def handler(method, params):
            if method == "Runtime.evaluate":
                return {"result": {"value": state_reply("Just a moment...")}}
            return self.generic(method, params)

        transport, server = self.make(handler)
        with self.assertRaises(ChallengeUnsolved) as caught:
            transport(HttpRequest("GET", "https://javten.com/search?kw=1", {}), 10, 4096)
        self.assertIn("javten.com", str(caught.exception))
        self.assertEqual(browser_transport.attention(), [])
        states = [params["bounds"].get("windowState") for method, params in server.calls
                  if method == "Browser.setWindowBounds"]
        self.assertEqual(states, ["normal", None, "minimized"])
        navigations = [params for method, params in server.calls if method == "Page.navigate"]
        self.assertEqual(navigations[-1], {"url": "about:blank"},
                         "放弃后把验证页换成空白页：留在窗口里它每 50 秒自己重载一次")
        self.assertEqual(len(navigations), 3, "首次导航、清 cookie 重载、空白页")
        self.assertIsNone(self.processes[0].returncode, "验证没过不是浏览器坏了，进程留着")

    def test_a_site_whose_window_went_unclicked_is_not_shown_again_until_a_page_loads(self):
        """同一站弹过窗口没点过去：再撞验证只等自动时限就报、不弹窗；哪次页面正常打开后再撞才重新弹。"""
        passes = {"through": False}

        def handler(method, params):
            if method == "Runtime.evaluate" and is_document(params):
                return {"result": {"value": document_reply("https://javten.com/x")}}
            if method == "Runtime.evaluate":
                if passes["through"]:
                    return {"result": {"value": state_reply("JAVten", url="https://javten.com/x")}}
                return {"result": {"value": state_reply("Just a moment...")}}
            return self.generic(method, params)

        def window_shows(server):
            return len([1 for method, params in server.calls
                        if method == "Browser.setWindowBounds" and params["bounds"].get("windowState") == "normal"])

        transport, server = self.make(handler)
        request = HttpRequest("GET", "https://javten.com/x", {})
        with self.assertRaises(ChallengeUnsolved):
            transport(request, 10, 4096)
        self.assertEqual(window_shows(server), 1)
        started = self.clock.now
        with self.assertRaises(ChallengeUnsolved) as caught:
            transport(request, 10, 4096)
        self.assertIn("不再弹", str(caught.exception))
        self.assertLessEqual(self.clock.now - started, 56, "不弹窗就只等清 cookie 那 15 秒加自动时限，不再多等点击那 120 秒")
        self.assertEqual(window_shows(server), 1, "弹过一次没点过去，这次不弹")
        self.assertEqual(browser_transport.attention(), [])
        self.assertEqual([params for method, params in server.calls if method == "Page.navigate"][-1],
                         {"url": "about:blank"}, "不弹窗直接放弃的那条路径同样把验证页换成空白页")
        passes["through"] = True
        self.assertEqual(transport(request, 10, 4096).status, 200)
        passes["through"] = False
        with self.assertRaises(ChallengeUnsolved):
            transport(request, 10, 4096)
        self.assertEqual(window_shows(server), 2, "页面正常打开过一次之后再撞验证，重新弹窗")

    def test_a_page_that_is_not_a_challenge_must_load_within_the_callers_timeout(self):
        """不是验证页的那一页按调用方的 `timeout` 截止：报 `PageTimeout`（调用方按连接失败重试），浏览器留着。"""
        def handler(method, params):
            if method == "Runtime.evaluate":
                return {"result": {"value": state_reply("", "loading")}}
            return self.generic(method, params)

        transport, server = self.make(handler)
        started = self.clock.now
        with self.assertRaises(PageTimeout) as caught:
            transport(HttpRequest("GET", "https://javten.com/x", {}), 8, 4096)
        self.assertIsInstance(caught.exception, BrowserUnavailable, "SourceTransport 按连接失败处理")
        self.assertEqual(self.clock.now - started, 8)
        self.assertFalse(any(method == "Browser.setWindowBounds" for method, _ in server.calls))
        self.assertEqual([params for method, params in server.calls if method == "Page.navigate"],
                         [{"url": "https://javten.com/x"}], "慢页面留在原地，只有放弃的验证页才换成空白页")
        self.assertIsNone(self.processes[0].returncode, "页面慢不是浏览器坏了，进程留着")
        self.assertEqual(len(self.launches), 1)

    def test_the_automatic_phase_is_cut_to_the_timeout_but_the_click_window_is_not(self):
        """撞验证页：清 cookie 在 `min(RESET_SECONDS, 自动时限)` 触发，重载后自动阶段最多等 `min(auto_seconds, timeout)`；
        弹窗之后照旧等满 `click_seconds`，不看 `timeout`。"""
        shown_at = []

        def handler(method, params):
            if method == "Browser.setWindowBounds" and params["bounds"].get("windowState") == "normal":
                shown_at.append(self.clock.now - started)
            if method == "Runtime.evaluate":
                return {"result": {"value": state_reply("Just a moment...")}}
            return self.generic(method, params)

        transport, _server = self.make(handler)
        for timeout, reset, auto in ((5, 5, 5), (100, 15, 40)):
            with self.subTest(timeout=timeout):
                shown_at.clear()
                transport._unsolved.clear()
                started = self.clock.now
                with self.assertRaises(ChallengeUnsolved):
                    transport(HttpRequest("GET", "https://javten.com/x", {}), timeout, 4096)
                self.assertEqual(shown_at, [reset + auto])
                self.assertEqual(self.clock.now - started, reset + auto + 120, "弹窗后等满 click_seconds")

    def test_a_site_left_unclicked_gives_up_at_the_shortened_automatic_limit(self):
        """同一站上次弹窗没点过去：这次清 cookie 重载后到 `min(auto_seconds, timeout)` 就报，不弹窗，不再多等。"""
        def handler(method, params):
            if method == "Runtime.evaluate":
                return {"result": {"value": state_reply("Just a moment...")}}
            return self.generic(method, params)

        transport, server = self.make(handler)
        transport._unsolved.add("javten.com")
        started = self.clock.now
        with self.assertRaises(ChallengeUnsolved):
            transport(HttpRequest("GET", "https://javten.com/x", {}), 6, 4096)
        self.assertEqual(self.clock.now - started, 12, "清 cookie 那一步与重载后的自动阶段都按 timeout 裁到 6 秒")
        self.assertFalse(any(method == "Browser.setWindowBounds" for method, _ in server.calls))

    def test_a_site_gate_is_clicked_and_the_page_it_returns_to_is_read(self):
        """FC2PPV-DB 的年龄门：落到 `/age-verify` 就点匹配的按钮，等地址离开那个路径再读文档。"""
        gate_url = "https://fc2ppv-db.com/ja/age-verify?returnTo=%2Fja%2Fvideos%2F1"
        video_url = "https://fc2ppv-db.com/ja/videos/1"
        clicked = []
        states = iter([state_reply("年齢確認", url=gate_url), state_reply("", "loading", url=gate_url),
                       state_reply("FC2-PPV-1", url=video_url)])

        def handler(method, params):
            if method == "Runtime.evaluate" and is_document(params):
                return {"result": {"value": document_reply(video_url)}}
            if method == "Runtime.evaluate" and "target.click()" in params["expression"]:
                clicked.append(params["expression"])
                return {"result": {"value": "18歳以上"}}
            if method == "Runtime.evaluate":
                return {"result": {"value": next(states)}}
            return self.generic(method, params)

        gates = {"fc2ppv-db.com": {"path": "/age-verify", "button": r"18|はい|以上"}}
        transport, server = self.make(handler, gates=gates)
        response = transport(HttpRequest("GET", video_url, {}), 10, 4096)
        self.assertEqual((response.status, response.url), (200, video_url))
        self.assertEqual(len(clicked), 1)
        self.assertIn(f"new RegExp({json.dumps(gates['fc2ppv-db.com']['button'])}, 'i')", clicked[0])
        self.assertEqual(len([1 for method, _ in server.calls if method == "Page.navigate"]), 1, "点按钮不是再导航")

    def test_a_gate_without_a_matching_button_returns_the_gate_page_itself(self):
        gate_url = "https://javten.com/age?returnTo=%2Fx"

        def handler(method, params):
            if method == "Runtime.evaluate" and is_document(params):
                return {"result": {"value": document_reply(gate_url, "<html>gate</html>")}}
            if method == "Runtime.evaluate" and "target.click()" in params["expression"]:
                return {"result": {"value": ""}}
            if method == "Runtime.evaluate":
                return {"result": {"value": state_reply("Age", url=gate_url)}}
            return self.generic(method, params)

        transport, _server = self.make(handler, gates={"javten.com": {"path": "/age", "button": "Enter"}})
        response = transport(HttpRequest("GET", "https://javten.com/x", {}), 10, 4096)
        self.assertEqual((response.url, response.body), (gate_url, b"<html>gate</html>"), "解析器自己认出门页")

    def test_a_broken_page_script_drops_the_browser_and_the_next_request_relaunches(self):
        calls = {"count": 0}

        def handler(method, params):
            if method == "Runtime.evaluate" and is_document(params):
                calls["count"] += 1
                if calls["count"] == 1:
                    return {"exceptionDetails": {"text": "TypeError: boom"}}
                return {"result": {"value": document_reply("https://javten.com/")}}
            if method == "Runtime.evaluate":
                return {"result": {"value": state_reply("JAVten")}}
            return self.generic(method, params)

        transport, _server = self.make(handler)
        with self.assertRaises(BrowserUnavailable):
            transport(HttpRequest("GET", "https://javten.com/", {}), 10, 4096)
        self.assertEqual(self.processes[0].returncode, 0, "坏了就关掉")
        response = transport(HttpRequest("GET", "https://javten.com/", {}), 10, 4096)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.launches), 2)

    def test_an_idle_browser_is_closed_and_a_dead_one_is_replaced(self):
        def handler(method, params):
            if method == "Runtime.evaluate":
                value = document_reply("https://javten.com/") if is_document(params) else state_reply("JAVten")
                return {"result": {"value": value}}
            return self.generic(method, params)

        transport, _server = self.make(handler)
        transport(HttpRequest("GET", "https://javten.com/", {}), 10, 4096)
        self.clock.now += 599
        transport._reap()
        self.assertIsNone(self.processes[0].returncode, "还没到空闲时限")
        self.clock.now += 600
        transport._reap()
        self.assertEqual(self.processes[0].returncode, 0)
        transport(HttpRequest("GET", "https://javten.com/", {}), 10, 4096)
        self.processes[1].returncode = 1
        transport(HttpRequest("GET", "https://javten.com/", {}), 10, 4096)
        self.assertEqual(len(self.launches), 3, "进程自己退了就再拉一个")

    def test_edge_is_started_in_private_mode(self):
        """Edge 会拿 Windows 账号登录新 profile 并开同步；InPrivate 不登录、不同步。"""
        def handler(method, params):
            if method == "Runtime.evaluate":
                value = document_reply("https://javten.com/") if is_document(params) else state_reply("JAVten")
                return {"result": {"value": value}}
            return self.generic(method, params)

        transport, _server = self.make(handler, executable=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        transport(HttpRequest("GET", "https://javten.com/", {}), 10, 4096)
        self.assertIn("--inprivate", self.launches[0])
        self.assertEqual(self.launches[0][-1], "about:blank")

    def test_a_browser_that_never_opens_its_port_is_reported(self):
        def popen(command, **_kwargs):
            process = FakeProcess(self.profile / "DevToolsActivePort", 0)
            process.returncode = 3
            return process

        transport = BrowserTransport("C:/fake/chrome.exe", self.profile, popen=popen, clock=self.clock,
                                     sleep=self.clock.sleep)
        self.addCleanup(transport.close)
        with self.assertRaises(BrowserUnavailable) as caught:
            transport(HttpRequest("GET", "https://javten.com/", {}), 10, 4096)
        self.assertIn("退出", str(caught.exception))


class WebSocketTests(unittest.TestCase):
    def test_frames_of_every_length_class_round_trip(self):
        server = FakeCdp(lambda method, params: {"echo": params["text"]})
        self.addCleanup(server.close)
        ws = _WebSocket(f"ws://127.0.0.1:{server.port}/devtools/page/T1")
        self.addCleanup(ws.close)
        for size in (10, 200, 70000):
            text = "字" * size
            self.assertEqual(ws.call("Echo", text=text), {"echo": text})

    def test_a_cdp_error_surfaces_as_an_exception(self):
        server = FakeCdp(lambda method, params: {})
        self.addCleanup(server.close)
        ws = _WebSocket(f"ws://127.0.0.1:{server.port}/devtools/page/T1")
        self.addCleanup(ws.close)
        with patch.object(ws, "recv", return_value=json.dumps({"id": 1, "error": {"code": -32000, "message": "no target"}})):
            with self.assertRaises(RuntimeError) as caught:
                ws.call("Page.navigate", url="about:blank")
        self.assertIn("no target", str(caught.exception))


class BrowserLookupTests(unittest.TestCase):
    def test_the_environment_override_wins_and_a_missing_file_means_none(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / "browser.exe"
            fake.write_bytes(b"")
            self.assertEqual(find_browser({"PEACH_BROWSER": str(fake)}, "Windows"), str(fake))
            self.assertIsNone(find_browser({"PEACH_BROWSER": str(fake) + ".missing"}, "Windows"))

    def test_each_platform_looks_in_its_own_places(self):
        with tempfile.TemporaryDirectory() as folder:
            edge = Path(folder) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            edge.parent.mkdir(parents=True)
            edge.write_bytes(b"")
            self.assertEqual(find_browser({"ProgramFiles(x86)": folder}, "Windows"), str(edge))
            chrome = Path(folder) / "Google" / "Chrome" / "Application" / "chrome.exe"
            chrome.parent.mkdir(parents=True)
            chrome.write_bytes(b"")
            self.assertEqual(find_browser({"ProgramFiles(x86)": folder}, "Windows"), str(chrome),
                             "两个都装了先用 Chrome：Edge 会拿系统账号登录并同步")
            self.assertEqual(find_browser({"LocalAppData": folder}, "Windows"), str(chrome), "用户目录下只找 Chrome")
            self.assertIsNone(find_browser({}, "Linux", which=lambda name: None), "Linux 没有显示器就没有浏览器")
            linux_chrome = Path(folder) / "google-chrome"
            linux_chrome.write_bytes(b"")
            self.assertEqual(find_browser({"DISPLAY": ":0"}, "Linux",
                                          which=lambda name: str(linux_chrome) if name == "google-chrome" else None),
                             str(linux_chrome))

    def test_proxy_flags_follow_the_source_connection_mode(self):
        self.assertEqual(proxy_flags(direct=True, proxy="http://x:1"), ("--no-proxy-server",))
        self.assertEqual(proxy_flags(direct=False, proxy=""), ())
        self.assertEqual(proxy_flags(direct=False, proxy="socks5://127.0.0.1:7890/"), ("--proxy-server=socks5://127.0.0.1:7890",))
        self.assertIsNone(proxy_flags(direct=False, proxy="http://user:pw@127.0.0.1:7890"), "命令行带不了代理凭据")


if __name__ == "__main__":
    unittest.main()
