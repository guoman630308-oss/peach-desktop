"""经本机浏览器取页：Cloudflare 的验证由浏览器自己过，Peach 只让它打开地址、读回文档（ADR-0065）。

FC2PPV-DB 与 JAVten 两站在 Cloudflare 的 JS 验证后面，任何不是浏览器的客户端都回 403。这里拉起
用户机器上已装的 Chrome 或 Edge（带远程调试口、独立 profile），让它导航到要取的地址，验证页由它
自己过掉，过了就把文档的最终地址、状态码与序列化后的 HTML 读回来。Cookie、User-Agent 与出口三者
天然一致，不再要用户去贴 Cookie。不用页面内 `fetch()`：JAVten 搜索那一跳的 Location 是 `http://`，
`fetch()` 跟到明文地址会被浏览器按混合内容拦下，导航则照常走。

实测（2026-09-25，Edge 154）两站的验证多数在 3～25 秒内自动过完，窗口放在屏幕外也一样；只有验证页
真的要人点一下时，才把窗口顶到前面，同时登记到 `attention()`，由 `/healthz` 带出去供排查。

不加依赖：与浏览器说话的是这里几十行的 RFC 6455 客户端，只跑本机回环、只发文本帧、不处理分片
（CDP 的回包不分片）。浏览器进程由第一次请求拉起、空闲一段时间后关掉；一个进程只开一页，请求串行。
macOS 是读者不采集，Linux 无显示器时 `find_browser` 返回空，来源退回带用户 Cookie 的直连路径。
"""
from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import platform
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from .http import HttpRequest, HttpResponse

LOGGER = logging.getLogger(__name__)

#: 验证页转了多久还没自动过，就清掉该站 cookie 重载一次。Cloudflare 见到过期或判无效的 `cf_clearance`
#: 会把浏览器扔进不给勾选框、也不放行的重试循环（页上只有转圈，`cf_chl_rc_ni` 计数涨），等多久、点哪里
#: 都没用，删掉那两条 cookie 重载 5 秒就过（2026-09-25 用生产 profile 实测）；正常自动放行都在 10 秒内。
RESET_SECONDS = 15.0
#: 清 cookie 重载之后再等多久没过，就把窗口顶到前面让人点，调用方的 `timeout` 更短时按 `timeout`（见 `_load`）。
#: 三次实测里最慢的一次是 25 秒（JAVten）。
AUTO_SECONDS = 40.0
#: 窗口顶到前面后再等多久。到点就报 `ChallengeUnsolved`，来源按拒绝访问那一档冷却。
CLICK_SECONDS = 120.0
#: 多久没有请求就关掉浏览器进程。`cf_clearance` 存在 profile 里，下次拉起接着用。
IDLE_SECONDS = 600.0
#: 站点的年龄门之类点一下就过的页：点完之后最多等多久跳回去。
GATE_SECONDS = 15.0
#: 启动时窗口放的位置：屏幕外（系统会把它钳到 -16384）。自动能过的验证在那里照样过。
OFFSCREEN = (-32000, -32000)
#: 要人点时窗口放回屏幕内的位置与大小。
ONSCREEN = (200, 100)
WINDOW_SIZE = (1000, 800)
#: 验证页的标记，前两条是 Cloudflare 的标题（英文与中文界面），后面是它脚本与文案里的固定字串。
CHALLENGE_MARKS = ("Just a moment", "请稍候", "cf-chl-", "challenge-platform",
                   "Verifying you are human", "Attention Required")
#: 导航时不下载的资源：图片、字体与媒体。要的是 HTML，验证页也不靠这些。
BLOCKED_RESOURCES = ("*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", "*.avif", "*.ico",
                     "*.woff", "*.woff2", "*.ttf", "*.mp4", "*.webm", "*.m3u8")
#: 请求头里跟着进浏览器的只有两个。Accept-Language 挂在会话上，这一页之后每条请求都带（JAVten 按它决定
#: 回日文原页还是译文页）；Referer 是解析器给的上一跳，只经 `Page.navigate` 的 `referrer` 落在主文档上：
#: 挂在会话上的 Referer 会跟进子框架的导航，Chrome 把那种导航按 `ERR_BLOCKED_BY_CLIENT` 拦下，Turnstile
#: 勾选框那个 iframe 就出不来，验证页只剩转圈、`cf_chl_rc_ni` 反复涨（2026-09-25 生产进程内对照实测）。
#: Cookie、User-Agent 之类由浏览器自己填，其余的不带。
SESSION_HEADERS = ("accept-language",)
REFERRER_HEADER = "referer"

_WINDOWS_CANDIDATES = (
    # Chrome 排前面：Edge 会拿 Windows 账号把新 profile 隐式登录并开同步，采集用的浏览记录会跟着
    # 进用户的微软账号；Chrome 不会。只有 Edge 的机器用 InPrivate 拉起，见 `_Browser.start`。
    # 尾段写正斜杠：`Path` 在哪个平台都按它拆段，反斜杠在 macOS 上是文件名的一部分，测试找不到文件。
    ("ProgramFiles", "Google/Chrome/Application/chrome.exe"),
    ("ProgramFiles(x86)", "Google/Chrome/Application/chrome.exe"),
    ("LocalAppData", "Google/Chrome/Application/chrome.exe"),
    ("ProgramFiles(x86)", "Microsoft/Edge/Application/msedge.exe"),
    ("ProgramFiles", "Microsoft/Edge/Application/msedge.exe"),
)
_MAC_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
_LINUX_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                "microsoft-edge", "microsoft-edge-stable")


class BrowserUnavailable(RuntimeError):
    """浏览器起不来或半路断了。调用方按连接失败重试，下一次会重新拉起。"""


class ChallengeUnsolved(RuntimeError):
    """验证页在限时内没过，人也没点。"""


class PageTimeout(BrowserUnavailable):
    """不是验证页的那一页在调用方给的 `timeout` 内没解析完。调用方按连接失败重试；浏览器没坏，进程留着。"""


def find_browser(environ: dict[str, str] | None = None, system: str | None = None,
                 which: Callable[[str], str | None] = shutil.which) -> str | None:
    """这台机器上能驱动的 Chromium 系浏览器；没有就返回 None，来源退回 Cookie 那条路。

    `PEACH_BROWSER` 指定的可执行文件优先。Linux 上没有显示器（无 `DISPLAY` 也无 `WAYLAND_DISPLAY`）
    时视为没有：验证页要真的渲染，无头模式会被 Turnstile 认出来。
    """
    environ = os.environ if environ is None else environ
    system = platform.system() if system is None else system
    chosen = environ.get("PEACH_BROWSER")
    if chosen:
        return chosen if Path(chosen).is_file() else None
    if system == "Windows":
        candidates = [Path(environ[key]) / tail for key, tail in _WINDOWS_CANDIDATES if environ.get(key)]
    elif system == "Darwin":
        candidates = [Path(item) for item in _MAC_CANDIDATES]
    else:
        if not (environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY")):
            return None
        candidates = [Path(found) for found in map(which, _LINUX_NAMES) if found]
    return next((str(path) for path in candidates if path.is_file()), None)


def proxy_flags(*, direct: bool, proxy: str) -> tuple[str, ...] | None:
    """来源的连接方式换成浏览器的启动参数。

    直连是 `--no-proxy-server`；Peach 代理有地址就 `--proxy-server=`；跟环境走的什么都不加，浏览器
    用系统代理。Chromium 不收命令行里的代理凭据（会弹登录框），带用户名的地址返回 None，来源退回
    httpx 那条路。
    """
    if direct:
        return ("--no-proxy-server",)
    if not proxy:
        return ()
    parsed = urlsplit(proxy)
    if parsed.username or parsed.password:
        return None
    return (f"--proxy-server={urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))}",)


class _WebSocket:
    """最小的 RFC 6455 客户端：本机回环、文本帧、客户端加掩码、不处理分片。"""

    def __init__(self, url: str, timeout: float = 30.0):
        _scheme, rest = url.split("://", 1)
        hostport, path = rest.split("/", 1)
        host, port = hostport.rsplit(":", 1)
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("浏览器在握手时关闭了连接")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("浏览器拒绝了调试连接")
        self.buffer = head.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _read(self, size: int) -> bytes:
        while len(self.buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("浏览器关闭了调试连接")
            self.buffer += chunk
        out, self.buffer = self.buffer[:size], self.buffer[size:]
        return out

    def send(self, text: str) -> None:
        payload = text.encode()
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            head = bytes([0x81, 0x80 | size])
        elif size < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", size)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", size)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def recv(self) -> str:
        while True:
            first, second = self._read(2)
            opcode, size = first & 0x0F, second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            if second & 0x80:
                self._read(4)
            data = self._read(size)
            if opcode == 0x1:
                return data.decode()
            if opcode == 0x8:
                raise ConnectionError("浏览器关闭了调试连接")

    def call(self, method: str, timeout: float = 30.0, **params):
        """发一条 CDP 命令，等它的回包；中途到的事件跳过。"""
        self.next_id += 1
        wanted = self.next_id
        self.sock.settimeout(timeout)
        self.send(json.dumps({"id": wanted, "method": method, "params": params}))
        while True:
            message = json.loads(self.recv())
            if message.get("id") == wanted:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error'].get('message', message['error'])}")
                return message.get("result", {})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10) as response:
        return json.load(response)


class _Browser:
    """一个浏览器进程与它唯一的那一页。

    `attended` 为真时这是给人操作的窗口（PikPak 浏览器登录，ADR-0093）：开在屏幕内、不挡图片与字体
    （登录页和人机验证要靠它们渲染），Edge 也不用 InPrivate，因为登录要的 localStorage 必须落在 profile 里。
    """

    def __init__(self, executable: str, profile: Path, flags: tuple[str, ...], *,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 targets: Callable[[int], list[dict]] = _targets, sleep: Callable[[float], None] = time.sleep,
                 attended: bool = False):
        self.executable, self.profile, self.flags = executable, Path(profile), tuple(flags)
        self._popen, self._targets, self._sleep = popen, targets, sleep
        self.attended = attended
        self.process: subprocess.Popen | None = None
        self.socket: _WebSocket | None = None
        self.window_id = None

    def start(self) -> None:
        self.profile.mkdir(parents=True, exist_ok=True)
        port_file = self.profile / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        position = ONSCREEN if self.attended else OFFSCREEN
        command = [self.executable, f"--user-data-dir={self.profile}", "--remote-debugging-port=0",
                   # 带远程调试口时 Chromium 会把 `navigator.webdriver` 置真，Turnstile 据此拒绝连人工点击；关掉这个标记。
                   # 它是「不受支持的标志」，浏览器会在每个窗口顶上挂一条警告；`--test-type` 让它不挂。
                   "--disable-blink-features=AutomationControlled", "--test-type",
                   "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-sync",
                   f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}",
                   f"--window-position={position[0]},{position[1]}", *self.flags]
        if "msedge" in Path(self.executable).name.lower() and not self.attended:
            # Edge 会拿 Windows 账号把新 profile 隐式登录并开同步；InPrivate 窗口不登录、不同步。
            # 代价是 Cookie 只活到进程退出，下次拉起再过一次验证（几秒）。
            command.append("--inprivate")
        command.append("about:blank")
        try:
            self.process = self._popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        except OSError as error:
            raise BrowserUnavailable(f"浏览器没有启动：{error}") from None
        port = self._wait_port(port_file)
        try:
            page = next(target for target in self._targets(port) if target.get("type") == "page")
            self.socket = _WebSocket(page["webSocketDebuggerUrl"])
            self.socket.call("Page.enable")
            self.socket.call("Network.enable")
            if not self.attended:
                self.socket.call("Network.setBlockedURLs", urls=list(BLOCKED_RESOURCES))
            self.window_id = self.socket.call("Browser.getWindowForTarget", targetId=page.get("id", "")).get("windowId")
        except (OSError, StopIteration, KeyError, ValueError, RuntimeError) as error:
            self.close()
            raise BrowserUnavailable(f"浏览器的调试口没有接上：{error}") from None

    def _wait_port(self, port_file: Path) -> int:
        for _ in range(300):
            if self.process is not None and self.process.poll() is not None:
                raise BrowserUnavailable(f"浏览器启动后立即退出（{self.process.returncode}）")
            if port_file.is_file():
                lines = port_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if len(lines) >= 2 and lines[0].strip().isdigit():
                    return int(lines[0])
            self._sleep(0.1)
        self.close()
        raise BrowserUnavailable("浏览器没有写出调试口")

    def alive(self) -> bool:
        return self.socket is not None and self.process is not None and self.process.poll() is None

    def evaluate(self, expression: str, *, timeout: float = 30.0):
        assert self.socket is not None
        result = self.socket.call("Runtime.evaluate", timeout, expression=expression, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("text") or "页面脚本出错")
        return result.get("result", {}).get("value")

    def navigate(self, url: str, *, referrer: str | None = None) -> None:
        """导航到 `url`；`referrer` 只作这一次主文档的 Referer，没有就不传这个键。"""
        assert self.socket is not None
        params = {"url": url, "referrer": referrer} if referrer else {"url": url}
        self.socket.call("Page.navigate", **params)

    def extra_headers(self, headers: Mapping[str, str]) -> None:
        """这一页之后每条请求都附带的头；传空字典就是清掉。"""
        assert self.socket is not None
        self.socket.call("Network.setExtraHTTPHeaders", headers=dict(headers))

    def clear_cookies(self, url: str) -> int:
        """删掉会随 `url` 发出去的全部 cookie（含 `cf_clearance`），返回条数。只动这一站，别的站的放行凭据留着。"""
        assert self.socket is not None
        cookies = self.socket.call("Network.getCookies", urls=[url]).get("cookies", [])
        for cookie in cookies:
            self.socket.call("Network.deleteCookies", name=cookie["name"], domain=cookie.get("domain", ""),
                             path=cookie.get("path", "/"))
        return len(cookies)

    def place(self, *, visible: bool) -> None:
        """要人点时放回屏幕内并顶到前面，点完最小化。

        位置与状态得分两步给：一条命令里同时带 `windowState` 与坐标，Chromium 只改状态不动位置
        （2026-09-25 实测，窗口停在屏幕外原地）。收起来用最小化而不是再挪回屏幕外：负坐标会被系统
        钳到边上，最小化则确定不占屏幕。
        """
        if self.socket is None or self.window_id is None:
            return
        if visible:
            self.socket.call("Browser.setWindowBounds", windowId=self.window_id, bounds={"windowState": "normal"})
            self.socket.call("Browser.setWindowBounds", windowId=self.window_id,
                             bounds={"left": ONSCREEN[0], "top": ONSCREEN[1],
                                     "width": WINDOW_SIZE[0], "height": WINDOW_SIZE[1]})
            self.socket.call("Page.bringToFront")
        else:
            self.socket.call("Browser.setWindowBounds", windowId=self.window_id, bounds={"windowState": "minimized"})

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.call("Browser.close", 5.0)
            except (OSError, RuntimeError, ValueError):
                pass
            self.socket.close()
            self.socket = None
        if self.process is not None:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


_STATE_SCRIPT = ("JSON.stringify({title: document.title, ready: document.readyState, url: location.href, "
                 "head: document.documentElement.outerHTML.slice(0, 4000)})")
#: 读回文档：状态码来自 Navigation Timing（Chromium 109 起有 `responseStatus`），HTML 是序列化后的 DOM。
_DOCUMENT_SCRIPT = ("JSON.stringify({url: location.href, type: document.contentType, "
                    "status: ((performance.getEntriesByType('navigation')[0] || {}).responseStatus || 0), "
                    "html: '<!DOCTYPE html>\\n' + document.documentElement.outerHTML})")
#: 点年龄门那颗按钮：文字匹配 `pattern`、没被禁用的第一颗。返回点了哪颗，没有就返回空串。
_CLICK_SCRIPT = """(() => {
  const pattern = new RegExp(%(pattern)s, 'i');
  const target = [...document.querySelectorAll('button, a')].find((node) => !node.disabled && pattern.test(node.textContent));
  if (!target) return '';
  target.click();
  return target.textContent.trim();
})()"""

_ATTENTION: dict[str, str] = {}
_ATTENTION_LOCK = threading.Lock()


def attention() -> list[str]:
    """眼下哪些站的验证页等着人点。`/healthz` 带出去供排查；提醒用户的是顶到前面的那个窗口本身。"""
    with _ATTENTION_LOCK:
        return list(_ATTENTION.values())


def _set_attention(host: str, message: str | None) -> None:
    with _ATTENTION_LOCK:
        if message:
            _ATTENTION[host] = message
        else:
            _ATTENTION.pop(host, None)


class BrowserTransport:
    """`HttpTransport`：让浏览器导航到地址，验证页由它过，过了读回文档。

    一个实例管一个浏览器进程，请求串行，只发 GET。导航后每秒看一次标题、`readyState` 与页头：不是
    验证页且 DOM 解析完就读文档；验证转了 `RESET_SECONDS` 没过，清掉该站 cookie 重载一次；重载后再等
    自动时限没过，窗口顶到前面并登记 `attention`；再等 `click_seconds` 还没过就报 `ChallengeUnsolved`。同一站弹过窗口没点过去，之后再撞验证到自动时限
    就报，不再弹窗，直到哪次页面正常打开为止：验证转圈时页上常常没有可点的框，一直弹只会打扰。
    各段时限怎么跟调用方的 `timeout` 配合见 `_load`。`gates` 是按主机登记的「点一下就过」的页（年龄门）：
    落到那个路径就点匹配的按钮，等它跳回去。
    """

    def __init__(self, executable: str, profile: Path, flags: tuple[str, ...] = (), *,
                 gates: Mapping[str, Mapping[str, str]] | None = None,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 targets: Callable[[int], list[dict]] = _targets,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 auto_seconds: float = AUTO_SECONDS, click_seconds: float = CLICK_SECONDS,
                 idle_seconds: float = IDLE_SECONDS):
        self.executable, self.profile, self.flags = executable, Path(profile), tuple(flags)
        self.gates = dict(gates or {})
        self._popen, self._targets, self._clock, self._sleep = popen, targets, clock, sleep
        self.auto_seconds, self.click_seconds, self.idle_seconds = auto_seconds, click_seconds, idle_seconds
        self._lock = threading.RLock()
        self._browser: _Browser | None = None
        self._timer: threading.Timer | None = None
        self._last_used = 0.0
        #: 弹过窗口却没点过去的站。再撞验证只等自动时限、不再弹窗，直到哪次页面正常打开。
        self._unsolved: set[str] = set()
        atexit.register(self.close)

    def __call__(self, request: HttpRequest, timeout: float, max_bytes: int) -> HttpResponse:
        if request.method.upper() != "GET":
            raise BrowserUnavailable("浏览器传输只发 GET")
        with self._lock:
            browser = self._ensure()
            try:
                state = self._load(browser, request.url, request.headers, timeout)
                state = self._gate(browser, state)
                document = json.loads(browser.evaluate(_DOCUMENT_SCRIPT, timeout=timeout + 10))
            except (OSError, RuntimeError, ValueError, KeyError) as error:
                if isinstance(error, (ChallengeUnsolved, PageTimeout)):
                    raise
                # 页面脚本、调试连接或进程任一处坏了都按断连处理：关掉，下一次请求重新拉起。
                self._drop()
                raise BrowserUnavailable(f"浏览器请求未取得：{error}") from None
            finally:
                self._touch()
        body = document["html"].encode("utf-8")[:max_bytes + 1]
        content_type = (document.get("type") or "text/html") + "; charset=utf-8"
        return HttpResponse(int(document.get("status") or 200), {"content-type": content_type}, body,
                            document.get("url") or state.get("url") or request.url)

    def _ensure(self) -> _Browser:
        if self._browser is None or not self._browser.alive():
            self._drop()
            browser = _Browser(self.executable, self.profile, self.flags, popen=self._popen,
                               targets=self._targets, sleep=self._sleep)
            browser.start()
            self._browser = browser
        return self._browser

    def _state(self, browser: _Browser) -> dict:
        try:
            return json.loads(browser.evaluate(_STATE_SCRIPT))
        except RuntimeError:
            # 页面跳转的那一瞬执行环境被销毁，脚本会报错；这不是坏了，是还在加载。
            return {"title": "", "ready": "loading", "url": "", "head": ""}

    def _load(self, browser: _Browser, url: str, headers: Mapping[str, str], timeout: float) -> dict:
        """导航到 `url`，等浏览器过完验证、DOM 解析完，返回那一刻的页面状态。

        时限从导航那一刻起算，`timeout` 是调用方给这一条请求的预算（`SourceTransport` 已按本趟截止时间裁过）：

        1. 一直没见到验证页：`timeout` 秒内要解析完，否则报 `PageTimeout`，调用方按连接失败处理。
        2. 见到验证页：转了 `min(RESET_SECONDS, 自动时限)` 秒还没过，清掉该站 cookie 重新导航一次——过期的
           `cf_clearance` 会让 Cloudflare 只转圈不放行也不给勾选框，清掉才出得来。重载后再等自动时限
           `min(auto_seconds, timeout)` 秒，浏览器自己过了就照常返回。
        3. 重载后仍没过：这一站在 `_unsolved` 里就立刻报 `ChallengeUnsolved`，不弹窗；不在就把窗口顶到前面，
           从弹出那一刻起再等 `click_seconds`，这一段不受 `timeout` 约束——人点验证要的时间不归一条请求的
           预算管。还没过就记进 `_unsolved` 并报 `ChallengeUnsolved`，同一站之后不再弹，直到哪次页面正常打开，
           所以一个站最多让整趟多等一次 `click_seconds`。
        """
        host = urlsplit(url).hostname or url
        referrer = next((value for key, value in headers.items() if key.lower() == REFERRER_HEADER), None)
        browser.extra_headers({key: value for key, value in headers.items() if key.lower() in SESSION_HEADERS})
        browser.navigate(url, referrer=referrer)
        started = self._clock()
        phase_started = started
        auto_limit = min(self.auto_seconds, timeout)
        reset_limit = min(RESET_SECONDS, auto_limit)
        challenged_seen = False
        cleared = False
        shown_at: float | None = None
        try:
            while True:
                state = self._state(browser)
                challenged = any(mark in state["head"] or mark in state["title"] for mark in CHALLENGE_MARKS)
                # DOM 解析完（`interactive`）就够：验证页的标记在页头里，此时已经看得到；不等外部
                # 资源全加载完——JAVten 作品页挂着统计脚本，到 `complete` 要 25 秒以上。
                if state["ready"] in ("interactive", "complete") and not challenged and state["title"]:
                    self._unsolved.discard(host)
                    return state
                challenged_seen = challenged_seen or challenged
                now = self._clock()
                elapsed = now - started
                if shown_at is not None:
                    if now - shown_at >= self.click_seconds:
                        self._unsolved.add(host)
                        raise ChallengeUnsolved(f"{host} 的人机验证在 {int(elapsed)} 秒内没有通过")
                elif challenged_seen:
                    waited = now - phase_started
                    if not cleared and waited >= reset_limit:
                        cleared = True
                        count = browser.clear_cookies(url)
                        LOGGER.info("%s 的验证页 %d 秒没有自动通过，清掉该站 %d 条 cookie 重载", host, int(elapsed), count)
                        browser.navigate(url, referrer=referrer)
                        phase_started = now
                    elif cleared and waited >= auto_limit:
                        if host in self._unsolved:
                            raise ChallengeUnsolved(f"{host} 的人机验证在 {int(elapsed)} 秒内没有自动通过；"
                                                    "窗口上次弹出后没点过去，这次不再弹")
                        shown_at = now
                        browser.place(visible=True)
                        _set_attention(host, f"{host} 的人机验证需要点一下，浏览器窗口已打开")
                        LOGGER.warning("%s 的验证页没有自动通过，等待用户点击", host)
                elif elapsed >= timeout:
                    raise PageTimeout(f"{host} 的页面在 {int(elapsed)} 秒内没有打开")
                self._sleep(1.0)
        except ChallengeUnsolved:
            # 放弃的验证页不能留在窗口里：它每 50 秒自己重载一次，`cf_chl_rc_ni` 跟着涨，在没人看的窗口里
            # 一直重试（2026-09-25 生产进程内观察十分钟）。导航到空白页就停了；空白页没有上一跳。
            browser.navigate("about:blank")
            raise
        finally:
            if shown_at is not None:
                _set_attention(host, None)
                browser.place(visible=False)

    def _gate(self, browser: _Browser, state: dict) -> dict:
        """落到站点的年龄门就替用户点那颗按钮，等它跳回要看的那一页。"""
        parts = urlsplit(state.get("url") or "")
        gate = self.gates.get(parts.hostname or "")
        if not gate or gate["path"] not in parts.path:
            return state
        clicked = browser.evaluate(_CLICK_SCRIPT % {"pattern": json.dumps(gate["button"])})
        if not clicked:
            LOGGER.warning("%s 的年龄门上没有找到能点的按钮", parts.hostname)
            return state
        started = self._clock()
        while self._clock() - started < GATE_SECONDS:
            self._sleep(1.0)
            state = self._state(browser)
            if state["ready"] in ("interactive", "complete") and state["url"] and gate["path"] not in urlsplit(state["url"]).path:
                return state
        LOGGER.warning("%s 的年龄门点了「%s」但没有跳回去", parts.hostname, clicked)
        return state

    def _touch(self) -> None:
        self._last_used = self._clock()
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.idle_seconds, self._reap)
        self._timer.daemon = True
        self._timer.start()

    def _reap(self) -> None:
        with self._lock:
            if self._browser is not None and self._clock() - self._last_used >= self.idle_seconds:
                self._drop()

    def _drop(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._drop()


_SHARED: dict[tuple, BrowserTransport] = {}
_SHARED_LOCK = threading.Lock()


def shared(profile: Path, *, direct: bool, proxy: str,
           gates: Mapping[str, Mapping[str, str]] | None = None) -> BrowserTransport | None:
    """同一进程里按（profile，连接方式）复用一个浏览器；没有可用的浏览器或代理带凭据时返回 None。"""
    executable = find_browser()
    flags = proxy_flags(direct=direct, proxy=proxy)
    if executable is None or flags is None:
        return None
    key = (str(profile), flags)
    with _SHARED_LOCK:
        if key not in _SHARED:
            _SHARED[key] = BrowserTransport(executable, profile, flags, gates=gates)
        else:
            _SHARED[key].gates.update(gates or {})
        return _SHARED[key]
