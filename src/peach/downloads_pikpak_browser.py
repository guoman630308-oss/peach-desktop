"""PikPak 用浏览器登录：人在 Peach 拉起的浏览器窗口里登录一次，Peach 从网页的 localStorage 读出 token（ADR-0093）。

做法和 Greasy Fork 上的 PikPak Assistant 用户脚本一样，只是换了宿主：脚本跑在用户自己的浏览器页面里，
Peach 是外部进程，读不到用户主浏览器的 localStorage，也不去读它磁盘上的 LevelDB。这里复用
`browser_transport._Browser` 的 CDP 通道，开一个 Peach 专用的持久 profile（`<凭据根>/browser-pikpak`，
和采集用的 `browser/` 分开），打开 mypikpak.com 顶到前面；人机验证由这台真浏览器自然通过。登录完成后
网页 SDK 把凭据写进 `credentials_<client_id>`，这里每秒用 `Runtime.evaluate` 看一次，读到就存进
`CredentialStore`（`download-pikpak`），然后关掉浏览器。

**刷新只归 Peach。** PikPak 每次刷新都作废旧的 refresh token，两个刷新者会互相打死对方。所以读到 token
后先把页面里的 `credentials_*` 删掉再关浏览器，这个 profile 之后不再持有 Peach 也持有的那一份；下次
拉起登录窗时，`_FORGET_ON_FIRST_DOCUMENT` 在网页脚本运行之前再删一次，网页从未登录开始，不会拿旧
token 去刷新，Peach 也不会把旧 token 当成新登录读回来。只有 Peach 刷新失败、要重新登录时，才重新
拉起这个窗口。设备号（`deviceid`）留着，同一台设备不必每次都过一遍设备验证。

**client 绑定。** client_id 从键名后缀读（`credentials_<client_id>`，多账号模式是
`credentials_<client_id>@<sub>`），不写死；`downloads_pikpak.profile_for` 按它选网页端常量。设备号按网页
自己的取法归一：`deviceid`（或 `_deviceid`）按 `.` 拆开取最后一段的前 32 个字符。

**等待与取消。** 登录在后台线程里等，接口只回状态，页面轮询。最多等 `WAIT_SECONDS`；用户关掉窗口时
调试连接断开或进程退出，记为「已取消」。token 只进凭据文件，不进日志、状态和响应体。
"""
from __future__ import annotations

import atexit
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from .browser_transport import BrowserUnavailable, _Browser, find_browser
from .downloads_pikpak import SESSION_LOCK, new_device_id

LOGGER = logging.getLogger(__name__)

PROFILE_DIRNAME = "browser-pikpak"
LOGIN_URL = "https://mypikpak.com/drive/"
#: 等用户登录的上限。到点关窗口，记为超时。
WAIT_SECONDS = 600.0
POLL_SECONDS = 1.0
CREDENTIALS_PREFIX = "credentials_"

IDLE, WAITING, DONE, CANCELLED, TIMEOUT, FAILED = "idle", "waiting", "done", "cancelled", "timeout", "failed"

#: 每个新文档里先于网页脚本运行。同一个标签页里只在 mypikpak.com 系的第一个文档删一次 `credentials_*`：
#: 标记放在 sessionStorage，登录后跳回来的文档不再删，刚登录写下的凭据才留得住。
_FORGET_ON_FIRST_DOCUMENT = """(() => {
  try {
    if (!/(^|\\.)mypikpak\\.com$/.test(location.hostname) || sessionStorage.getItem('peach-forgot')) return;
    sessionStorage.setItem('peach-forgot', '1');
    Object.keys(localStorage).filter((key) => key.startsWith('credentials_'))
      .forEach((key) => localStorage.removeItem(key));
  } catch (error) {}
})()"""
#: 读凭据。还没登录、不在 mypikpak.com 系的页面上（登录中可能跳到 user.mypikpak.com）都回 null。
_READ_SCRIPT = """(() => {
  if (!/(^|\\.)mypikpak\\.com$/.test(location.hostname)) return null;
  for (const key of Object.keys(localStorage)) {
    if (!key.startsWith('credentials_')) continue;
    let value = null;
    try { value = JSON.parse(localStorage.getItem(key) || 'null'); } catch (error) { continue; }
    if (!value || !value.refresh_token || !value.access_token) continue;
    const device = localStorage.getItem('deviceid') || localStorage.getItem('_deviceid') || '';
    return JSON.stringify({key, access_token: value.access_token, refresh_token: value.refresh_token,
      expires_at: value.expires_at || '', sub: value.sub || '', device: (device.split('.').pop() || '').substring(0, 32)});
  }
  return null;
})()"""
#: 读到之后从页面里删掉凭据，profile 里不留 Peach 也持有的 refresh token。
_FORGET_SCRIPT = """(() => {
  Object.keys(localStorage).filter((key) => key.startsWith('credentials_'))
    .forEach((key) => localStorage.removeItem(key));
  return true;
})()"""


def profile_dir(secrets_root: Path) -> Path:
    """浏览器 profile 放在凭据根下：里面有 PikPak 的设备号与登录会话，和凭据文件一个性质。"""
    return Path(secrets_root) / PROFILE_DIRNAME


def _epoch(raw) -> int:
    """网页 SDK 把 `expires_at` 存成 ISO 时间（已提前留了余量）；读不出就记 0，第一次用时先刷新。"""
    try:
        return int(datetime.fromisoformat(str(raw)).timestamp())
    except (TypeError, ValueError):
        return 0


def parse_capture(payload: dict) -> dict[str, str] | None:
    """`_READ_SCRIPT` 的回包换成凭据字典。缺 token 或键名里没有 client_id 时返回 None。"""
    key = str(payload.get("key") or "")
    client_id = key[len(CREDENTIALS_PREFIX):].split("@", 1)[0] if key.startswith(CREDENTIALS_PREFIX) else ""
    access, refresh = str(payload.get("access_token") or ""), str(payload.get("refresh_token") or "")
    if not (client_id and access and refresh):
        return None
    device = str(payload.get("device") or "")
    if not device:
        LOGGER.warning("PikPak 网页的 localStorage 里没有 deviceid，换一个新的设备号")
        device = new_device_id()
    return {"refresh_token": refresh, "access_token": access, "expires_at": str(_epoch(payload.get("expires_at"))),
            "user_id": str(payload.get("sub") or ""), "device_id": device, "client_id": client_id}


def _attended_browser(executable: str, profile: Path) -> _Browser:
    return _Browser(executable, profile, (), attended=True)


class BrowserLogin:
    """一次浏览器登录的状态与后台线程。同一时间只开一个登录窗口。

    `status()` 只回 `state` 与 `message`：`idle`、`waiting`、`done`、`cancelled`、`timeout`、`failed`。
    状态带本轮 id，被取消顶掉的线程不再改状态。不用 `jobs.BackgroundJob`：那一套带任务中心与活动页的
    记账，登录等待不是馆藏任务。
    """

    def __init__(self, profile: Path, persist: Callable[[dict], None], *,
                 find: Callable[[], str | None] = find_browser,
                 browser: Callable[[str, Path], _Browser] = _attended_browser,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 wait_seconds: float = WAIT_SECONDS, on_done: Callable[[], None] | None = None):
        self.profile = Path(profile)
        self.persist = persist
        self._find, self._browser = find, browser
        self._clock, self._sleep = clock, sleep
        self.wait_seconds = wait_seconds
        self.on_done = on_done
        self._lock = threading.Lock()
        self._state = {"id": "", "state": IDLE, "message": ""}
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        atexit.register(self.close)

    def available(self) -> bool:
        return self._find() is not None

    def status(self) -> dict:
        with self._lock:
            return {"state": self._state["state"], "message": self._state["message"]}

    def start(self) -> dict:
        """拉起登录窗口。已经在等就回当前状态，不开第二个。没有浏览器时抛 ValueError。"""
        executable = self._find()
        if executable is None:
            raise ValueError("这台电脑上没有找到 Chrome 或 Edge，用不了浏览器登录。可以展开下方用账号密码登录")
        with self._lock:
            if self._state["state"] == WAITING:
                return self._snapshot()
            run_id = uuid.uuid4().hex
            self._state = {"id": run_id, "state": WAITING, "message": "浏览器窗口已打开，在里面登录 PikPak"}
            self._cancel = threading.Event()
            cancel = self._cancel
            self._thread = threading.Thread(target=self._run, args=(run_id, executable, cancel),
                                            name="PeachPikPakLogin", daemon=True)
            thread = self._thread
            result = self._snapshot()
        thread.start()
        return result

    def _snapshot(self) -> dict:
        return {"state": self._state["state"], "message": self._state["message"]}

    def cancel(self) -> dict:
        """关掉在等的登录窗口。没有在等就原样回状态。"""
        with self._lock:
            if self._state["state"] == WAITING:
                self._cancel.set()
                self._state = {"id": self._state["id"], "state": CANCELLED, "message": "已取消浏览器登录"}
            return self._snapshot()

    def close(self) -> None:
        self.cancel()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10)

    def _finish(self, run_id: str, state: str, message: str) -> None:
        with self._lock:
            if self._state["id"] == run_id and self._state["state"] == WAITING:
                self._state = {"id": run_id, "state": state, "message": message}

    def _run(self, run_id: str, executable: str, cancel: threading.Event) -> None:
        browser = self._browser(executable, self.profile)
        try:
            browser.start()
            assert browser.socket is not None
            browser.socket.call("Page.addScriptToEvaluateOnNewDocument", source=_FORGET_ON_FIRST_DOCUMENT)
            browser.navigate(LOGIN_URL)
            browser.place(visible=True)
            state, message = self._wait(browser, cancel)
            self._finish(run_id, state, message)
        except BrowserUnavailable as error:
            self._finish(run_id, FAILED, f"浏览器没有打开：{error}")
        except Exception as error:  # noqa: BLE001 —— 线程里的任何异常都要变成可轮询的状态，否则页面一直等
            LOGGER.error("PikPak 浏览器登录出错：%s", type(error).__name__)
            self._finish(run_id, FAILED, f"浏览器登录出错：{type(error).__name__}")
        finally:
            browser.close()
        if self.status()["state"] == DONE and self.on_done is not None:
            self.on_done()

    def _wait(self, browser: _Browser, cancel: threading.Event) -> tuple[str, str]:
        deadline = self._clock() + self.wait_seconds
        while True:
            if cancel.is_set():
                return CANCELLED, "已取消浏览器登录"
            if not browser.alive():
                return CANCELLED, "浏览器窗口已关闭，登录没有完成"
            try:
                raw = browser.evaluate(_READ_SCRIPT, timeout=10)
            except TimeoutError:
                raw = None
            except RuntimeError:
                # 页面跳转的那一瞬执行环境被销毁，脚本会报错；这是还在加载，不是窗口关了。
                raw = None
            except (OSError, ValueError):
                return CANCELLED, "浏览器窗口已关闭，登录没有完成"
            if raw:
                values = parse_capture(json.loads(raw))
                if values is not None:
                    self._take(browser, values)
                    return DONE, "已用浏览器登录 PikPak"
            if self._clock() >= deadline:
                minutes = int(self.wait_seconds // 60)
                return TIMEOUT, f"{minutes} 分钟内没有完成登录，窗口已关闭。可以再点一次「用浏览器登录」"
            self._sleep(POLL_SECONDS)

    def _take(self, browser: _Browser, values: dict) -> None:
        """先存，再从页面里删掉凭据。删失败只记键名级别的日志：浏览器随后就关，页面不会再刷新它。"""
        with SESSION_LOCK:
            self.persist(values)
        try:
            browser.evaluate(_FORGET_SCRIPT, timeout=10)
        except (OSError, RuntimeError, ValueError):
            LOGGER.warning("PikPak 登录窗口里的 credentials_* 没有删掉，浏览器照常关闭")
