"""PikPak 云下载：直连 PikPak 的非官方 API 提交与跟踪离线任务。

CloudDrive2 v0.7.13 起去掉了 PikPak 离线，只剩挂载，所以这一路不经 CloudDrive2。协议照
PyPI `PikPakAPI` 0.1.11（GPL-3.0-only，Quan666/PikPakAPI）：请求地址、客户端常量、验证码
签名盐都是那份实现里抄来的协议事实。不直接依赖它，原因有三：它只有 async 接口，而对账在
线程里跑；登录要人机验证时它把验证页地址吞掉、只抛一句 `captcha_token get failed`；设备
号默认取账号加密码的 md5。

**接口是非官方的，随时可能失效。** 回包形状对不上时报「配置与认证」并说明接口可能变了，
页面上每条任务都有「复制磁力」可以退回手动。

**凭据。** refresh token、access token、设备号存本机 `CredentialStore`（`download-pikpak`）。
有两种来路（ADR-0093）：设置页「用浏览器登录」从 PikPak 网页的 localStorage 读出网页端签发的
token（`downloads_pikpak_browser`）；账号密码登录用安卓客户端常量换 token，密码只在用户勾选
「保存密码」时一并存，用来在 refresh token 失效后自动重登。PikPak 每次刷新都发一个新的 refresh
token，旧的随即作废，所以每次刷新后立刻写回，同一份 token 也只能有 Peach 一个刷新者。

**client 绑定。** token 绑定在签发它的 client 上，刷新、`captcha/init` 与签名都要用同一个 client
的常量。凭据里的 `client_id` 记着是哪一个，`profile_for` 按它选 `ANDROID` 或 `WEB`；没有这个字段
的旧凭据是安卓客户端登录的。

**人机验证。** `captcha/init` 回包里带 `url` 时，PikPak 要求在浏览器里完成验证。Peach 不
自动过验证：把这个地址交给设置页，用户打开完成后再点一次登录，用同一个设备号重试。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

import httpx

from .downloads import (
    DONE, ERROR, MISSING, RUNNING, DownloadError, DownloadTask, Magnet, RemoteStatus,
    magnet_info_hash,
)
from .user_agent import USER_AGENT

USER_HOST = "https://user.mypikpak.com"
API_HOST = "https://api-drive.mypikpak.com"
CLIENT_ID = "YNxT9w7GMdWvEOKa"
CLIENT_SECRET = "dbw2OtmVEeuUvIptb1Coyg"
CLIENT_VERSION = "1.47.1"
PACKAGE_NAME = "com.pikcloud.pikpak"
#: `captcha_sign` 的盐，逐个做 md5。来自 PikPak 网页端脚本，经 PikPakAPI 转抄。
SALTS = (
    "Gez0T9ijiI9WCeTsKSg3SMlx", "zQdbalsolyb1R/", "ftOjr52zt51JD68C3s",
    "yeOBMH0JkbQdEFNNwQ0RI9T3wU/v", "BRJrQZiTQ65WtMvwO", "je8fqxKPdQVJiy1DM6Bc9Nb1", "niV",
    "9hFCW2R1", "sHKHpe2i96", "p7c5E6AcXQ/IJUuAEC9W6", "", "aRv9hjc9P+Pbn+u3krN6",
    "BzStcgE8qVdqjEH16l4", "SqgeZvL5j9zoHP95xWHt", "zVof5yaJkPe3VFpadPof",
)


@dataclass(frozen=True)
class ClientProfile:
    """一个 PikPak client 的协议常量。`client_secret` 为 None 时请求体不带这个键。

    `sign_timestamp` 是网页端写死在脚本里的签名时间戳；为 None 时按当前毫秒数签（安卓客户端的做法）。
    `refresh_headers` 是刷新时额外带的头。`known` 为假表示这个 client_id 是从网页 localStorage 键名里
    读到的、Peach 没有它的签名常量：刷新照样能试，要 `captcha/init` 签名的操作直接报错。
    """

    client_id: str
    client_secret: str | None
    client_version: str
    package_name: str
    salts: tuple[str, ...]
    sign_timestamp: str | None = None
    refresh_headers: tuple[tuple[str, str], ...] = ()
    known: bool = True


ANDROID = ClientProfile(CLIENT_ID, CLIENT_SECRET, CLIENT_VERSION, PACKAGE_NAME, SALTS)
#: 网页端（mypikpak.com/drive）的常量，取自 2026-10-02 的网页脚本，来源与哈希见
#: `docs/reference-snapshots/pikpak-web-session-measured.md`：`5990.326e2dc8.js` 的签名配置
#: （clientId、clientVersion、packageName、固定 timestamp 与 15 条 md5 盐），`7991.17876548.js` 登录 SDK
#: 的刷新请求（`x-client-id`、`x-sdk-version`、`x-protocol-version` 三个头）。网页端的登录配置里没有
#: clientSecret，SDK 的刷新请求体因此不带 `client_secret`；服务端接不接受这个形态要在真实账号上验证。
#: PikPak 重新发布网页时盐与时间戳可能换，那时 `captcha/init` 会拒绝签名，错误里会说明。
WEB = ClientProfile(
    "YUMx5nI8ZU8Ap8pm", None, "2.0.0", "mypikpak.com",
    ("C9qPpZLN8ucRTaTiUMWYS9cQvWOE", "+r6CQVxjzJV6LCV", "F", "pFJRC", "9WXYIDGrwTCz2OiVlgZa90qpECPD6olt",
     "/750aCr4lm/Sly/c", "RB+DT/gZCrbV", "", "CyLsf7hdkIRxRm215hl", "7xHvLi2tOYP0Y92b",
     "ZGTXXxu8E/MIWaEDB+Sm/", "1UI3", "E7fP5Pfijd+7K+t6Tg/NhuLq0eEUVChpJSkrKxpO", "ihtqpG6FMt65+Xk+tWUH2",
     "NhXXU9rg4XXdzo7u5o"),
    sign_timestamp="1790733736477",
    refresh_headers=(("X-Sdk-Version", "8.1.4"), ("X-Protocol-Version", "301")),
)
CLIENTS = {ANDROID.client_id: ANDROID, WEB.client_id: WEB}


def profile_for(client_id: str | None) -> ClientProfile:
    """凭据里记的 client_id 对应的常量。没记就是安卓客户端；认不出的只知道 id，不知道签名常量。"""
    if not client_id:
        return ANDROID
    return CLIENTS.get(client_id) or ClientProfile(client_id, None, "", "", (), known=False)


#: 对外请求共用的那一份桌面 Chrome UA（`user_agent.py`）。
BROWSER_AGENT = USER_AGENT
TIMEOUT = 20.0
PHASES = "PHASE_TYPE_RUNNING,PHASE_TYPE_ERROR,PHASE_TYPE_COMPLETE,PHASE_TYPE_PENDING"
#: 过期前这么多秒就提前刷新。access token 有效期 7200 秒。
REFRESH_MARGIN = 300
#: 目录与任务列表最多翻这么多页，防止接口变形时在分页里打转。
MAX_PAGES = 20
#: 一个部署只有一个 PikPak 账号。提交与后台对账都可能刷新 token，刷新会作废旧的
#: refresh token，所以同一时间只让一条线程拿着凭据。
SESSION_LOCK = threading.Lock()


class CaptchaRequired(DownloadError):
    """PikPak 要求在浏览器里完成人机验证。`url` 交给设置页。"""

    def __init__(self, url: str):
        super().__init__("config", "PikPak 要求先完成人机验证：打开验证页完成后，回到设置页再点一次登录")
        self.url = url


def captcha_sign(device_id: str, timestamp: str, profile: ClientProfile = ANDROID) -> str:
    """client_id、版本、包名、设备号、时间戳串起来，逐条加盐做 md5。网页端与安卓客户端同一个算法。"""
    sign = profile.client_id + profile.client_version + profile.package_name + device_id + timestamp
    for salt in profile.salts:
        sign = hashlib.md5((sign + salt).encode()).hexdigest()
    return f"1.{sign}"


def new_device_id() -> str:
    return uuid.uuid4().hex


def _account_meta(username: str) -> dict:
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", username):
        return {"email": username}
    if re.fullmatch(r"\+?\d{11,18}", username):
        return {"phone_number": username}
    return {"username": username}


def _changed(what: str) -> DownloadError:
    return DownloadError(
        "config", f"PikPak 接口回包形状不对（{what}），接口可能已经变了。可以先复制磁力手动添加")


class PikPakClient:
    """同步的最小客户端：登录、刷新、离线提交、任务列表、取消、按路径找目录。

    `values` 是凭据字典（`refresh_token`、`access_token`、`expires_at`、`user_id`、
    `device_id`、可选的 `client_id` 与 `username`/`password`）。每次换到新 token 都调 `persist(values)`。
    """

    def __init__(self, values: dict[str, str], *, persist: Callable[[dict], None],
                 transport: httpx.BaseTransport | None = None, clock: Callable[[], float] = time.time):
        self.values = dict(values)
        self.values.setdefault("device_id", new_device_id())
        self.profile = profile_for(self.values.get("client_id"))
        self.persist = persist
        self.clock = clock
        self.http = httpx.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "PikPakClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @property
    def device_id(self) -> str:
        return self.values["device_id"]

    # -- 传输

    def _send(self, method: str, url: str, *, body=None, params=None, headers=None) -> dict:
        try:
            response = self.http.request(method, url, json=body, params=params, headers=headers)
        except httpx.HTTPError as error:
            raise DownloadError("network", f"连不上 PikPak：{type(error).__name__}") from None
        if response.status_code >= 500:
            raise DownloadError("network", f"PikPak 暂时不可用（HTTP {response.status_code}）")
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            raise _changed(f"HTTP {response.status_code} 不是 JSON") from None
        if not isinstance(payload, dict):
            raise _changed("回包不是对象")
        if payload.get("error") or (response.status_code >= 400 and payload):
            raise PikPakApiError(payload, response.status_code)
        if response.status_code >= 400:
            raise _changed(f"HTTP {response.status_code}")
        return payload

    def _captcha(self, action: str, meta: dict) -> str:
        payload = self._send("POST", f"{USER_HOST}/v1/shield/captcha/init", body={
            "client_id": self.profile.client_id, "action": action, "device_id": self.device_id, "meta": meta})
        if payload.get("url"):
            raise CaptchaRequired(str(payload["url"]))
        token = payload.get("captcha_token")
        if not token:
            raise _changed("没有 captcha_token")
        return str(token)

    def _drive_captcha(self, action: str) -> str:
        profile = self.profile
        if not profile.known:
            raise DownloadError("config", f"PikPak 网页端的客户端标识是 {profile.client_id}，Peach 没有它的签名常量，"
                                          "提交不了离线任务。可以先复制磁力手动添加")
        stamp = profile.sign_timestamp or str(int(self.clock() * 1000))
        try:
            return self._captcha(action, {
                "captcha_sign": captcha_sign(self.device_id, stamp, profile),
                "client_version": profile.client_version, "package_name": profile.package_name,
                "user_id": self.values.get("user_id", ""), "timestamp": stamp})
        except PikPakApiError as error:
            reason = f"{error.description}（{error.code or error.status}）"
            if profile is WEB:
                raise DownloadError("config", f"PikPak 拒绝了网页端的请求签名：{reason}。网页端的签名常量可能已经更新，"
                                              "可以先复制磁力手动添加") from None
            raise error.as_download_error() from None

    # -- 登录与刷新

    def login(self, username: str, password: str) -> None:
        """账号密码登录走安卓客户端：换到的 token 绑定安卓 client，凭据里记下它的 client_id。"""
        self.profile = ANDROID
        signin = f"{USER_HOST}/v1/auth/signin"
        captcha = self._captcha(f"POST:{signin}", _account_meta(username))
        payload = self._send("POST", signin, body={
            "client_id": ANDROID.client_id, "client_secret": ANDROID.client_secret, "username": username,
            "password": password, "captcha_token": captcha},
            headers={"X-Device-Id": self.device_id, "User-Agent": BROWSER_AGENT})
        self.values["username"] = username
        self.values["client_id"] = ANDROID.client_id
        self._take_tokens(payload)

    def refresh(self) -> None:
        """按签发 token 的那个 client 刷新。安卓与网页端的刷新请求体都不带 `client_secret`。"""
        token = self.values.get("refresh_token")
        if not token:
            raise DownloadError("config", "还没有登录 PikPak")
        profile = self.profile
        body = {"client_id": profile.client_id, "refresh_token": token, "grant_type": "refresh_token"}
        headers = {"X-Device-Id": self.device_id, "User-Agent": BROWSER_AGENT}
        if profile is not ANDROID:
            headers.update({"X-Client-Id": profile.client_id, **dict(profile.refresh_headers)})
        payload = self._send("POST", f"{USER_HOST}/v1/auth/token", body=body, headers=headers)
        self._take_tokens(payload)

    def _take_tokens(self, payload: dict) -> None:
        access, refresh = payload.get("access_token"), payload.get("refresh_token")
        if not access or not refresh:
            raise _changed("登录回包里没有 token")
        expires = int(payload.get("expires_in") or 7200)
        self.values.update({
            "access_token": str(access), "refresh_token": str(refresh),
            "user_id": str(payload.get("sub") or self.values.get("user_id", "")),
            "expires_at": str(int(self.clock()) + expires)})
        self.persist(dict(self.values))

    def _ensure_access(self) -> None:
        expires = int(self.values.get("expires_at") or 0)
        if not self.values.get("access_token") or self.clock() > expires - REFRESH_MARGIN:
            self._renew()

    def _renew(self) -> None:
        """刷新；被拒时有保存的密码就重登，没有就报原因。浏览器登录的凭据没有密码，只能回设置页再登一次。"""
        try:
            self.refresh()
        except PikPakApiError as error:
            username, password = self.values.get("username"), self.values.get("password")
            if not (username and password):
                reason = f"{error.description}（{error.code or error.status}）"
                if self.profile is ANDROID:
                    raise DownloadError("config", f"PikPak 登录已过期，请在设置页重新登录：{reason}") from None
                raise DownloadError("config", f"PikPak 拒绝刷新网页登录的令牌：{reason}。"
                                              "请在设置页点「用浏览器登录」重新登录") from None
            self.login(username, password)

    def _api(self, method: str, path: str, *, body=None, params=None, captcha: str | None = None) -> dict:
        self._ensure_access()
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self.values['access_token']}",
                       "X-Device-Id": self.device_id, "User-Agent": BROWSER_AGENT}
            if captcha:
                headers["X-Captcha-Token"] = captcha
            try:
                return self._send(method, API_HOST + path, body=body, params=params, headers=headers)
            except PikPakApiError as error:
                if attempt == 0 and error.unauthenticated:
                    self._renew()
                    continue
                raise error.as_download_error() from None
        raise _changed("刷新 token 之后仍未通过认证")

    # -- 网盘

    def folder_id(self, path: str) -> str:
        """`/a/b` → 文件夹 id。目录必须已经存在；不替用户建目录。"""
        parent = ""
        for name in [part for part in path.split("/") if part]:
            match = None
            for item in self._children(parent):
                if item.get("name") == name and "folder" in str(item.get("kind", "")):
                    match = item
                    break
            if match is None:
                raise DownloadError("config", f"PikPak 里没有 {path} 这个文件夹")
            parent = str(match.get("id") or "")
        if not parent:
            raise DownloadError("config", "目标目录要写成 PikPak 里的一个具体文件夹")
        return parent

    def _children(self, parent_id: str) -> list[dict]:
        found: list[dict] = []
        token = ""
        for _page in range(MAX_PAGES):
            payload = self._api("GET", "/drive/v1/files", params={
                "parent_id": parent_id, "limit": 200, "page_token": token,
                "filters": json.dumps({"trashed": {"eq": False}})})
            files = payload.get("files")
            if not isinstance(files, list):
                raise _changed("文件列表")
            found.extend(item for item in files if isinstance(item, dict))
            token = str(payload.get("next_page_token") or "")
            if not token:
                break
        return found

    def has_child(self, parent_id: str, name: str) -> bool:
        return any(item.get("name") == name for item in self._children(parent_id))

    def add_offline(self, uri: str, parent_id: str) -> dict:
        captcha = self._drive_captcha("POST:/drive/v1/files")
        payload = self._api("POST", "/drive/v1/files", captcha=captcha, body={
            "kind": "drive#file", "upload_type": "UPLOAD_TYPE_URL", "url": {"url": uri},
            "folder_type": "", "parent_id": parent_id})
        task = payload.get("task")
        if not isinstance(task, dict) or not task.get("id"):
            raise _changed("提交回包里没有 task")
        return task

    def tasks(self) -> list[dict]:
        found: list[dict] = []
        token = ""
        for _page in range(MAX_PAGES):
            payload = self._api("GET", "/drive/v1/tasks", params={
                "type": "offline", "limit": 100, "page_token": token,
                "filters": json.dumps({"phase": {"in": PHASES}})})
            tasks = payload.get("tasks")
            if not isinstance(tasks, list):
                raise _changed("任务列表")
            found.extend(item for item in tasks if isinstance(item, dict))
            token = str(payload.get("next_page_token") or "")
            if not token:
                break
        return found

    def delete_task(self, task_id: str) -> None:
        self._api("DELETE", "/drive/v1/tasks", params={"task_ids": task_id, "delete_files": "false"})


class PikPakApiError(Exception):
    """PikPak 回的错误 JSON：`error`、`error_code`、`error_description`。"""

    def __init__(self, payload: dict, status: int):
        super().__init__(str(payload.get("error") or status))
        self.code = str(payload.get("error") or "")
        self.number = payload.get("error_code")
        self.description = str(payload.get("error_description") or payload.get("error") or "")
        self.status = status

    @property
    def unauthenticated(self) -> bool:
        return self.number == 16 or self.status == 401 or self.code == "unauthenticated"

    def as_download_error(self) -> DownloadError:
        text = f"{self.description}（{self.code or self.status}）"
        lowered = f"{self.code} {self.description}".casefold()
        if self.unauthenticated or "invalid_grant" in lowered:
            return DownloadError("config", f"PikPak 登录已失效，请在设置页重新登录：{text}")
        if "captcha" in lowered:
            return DownloadError("config", f"PikPak 要求人机验证，请在设置页重新登录：{text}")
        if any(word in lowered for word in ("limit", "quota", "space", "exceed")):
            return DownloadError("quota", f"PikPak 离线次数或空间不够：{text}")
        if any(word in lowered for word in ("url", "invalid_argument", "magnet")):
            return DownloadError("invalid_link", f"PikPak 不认这个链接：{text}")
        return DownloadError("rejected", f"PikPak 拒绝了这个任务：{text}")


def match_task(tasks: list[dict], task: DownloadTask) -> dict | None:
    for item in tasks:
        if task.remote_id and str(item.get("id")) == task.remote_id:
            return item
    for item in tasks:
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        if magnet_info_hash(str(params.get("url") or "")) == task.info_hash:
            return item
    return None


def task_status(item: dict | None) -> RemoteStatus:
    if item is None:
        return RemoteStatus(MISSING)
    phase = str(item.get("phase") or "")
    name = str(item.get("file_name") or item.get("name") or "")
    try:
        progress = max(0.0, min(float(item.get("progress") or 0) / 100.0, 1.0))
    except (TypeError, ValueError):
        progress = None
    remote_id = str(item.get("id") or "")
    if phase == "PHASE_TYPE_COMPLETE":
        return RemoteStatus(DONE, remote_id, name, 1.0)
    if phase == "PHASE_TYPE_ERROR":
        return RemoteStatus(ERROR, remote_id, name, progress,
                            str(item.get("message") or "PikPak 报告离线失败"))
    if phase in {"PHASE_TYPE_RUNNING", "PHASE_TYPE_PENDING"}:
        return RemoteStatus(RUNNING, remote_id, name, progress)
    raise _changed(f"任务状态 {phase or '为空'}")


class PikPakProvider:
    """`downloads.OfflineProvider` 的 PikPak 实现。"""

    key = "pikpak"

    def __init__(self, load: Callable[[], dict], *, persist: Callable[[dict], None],
                 transport: httpx.BaseTransport | None = None, clock: Callable[[], float] = time.time):
        self.load = load
        self.persist = persist
        self.transport = transport
        self.clock = clock

    @contextmanager
    def client(self) -> Iterator[PikPakClient]:
        """在会话锁里现读凭据。锁外读到的 refresh token 可能刚被另一条线程轮换作废。"""
        with SESSION_LOCK:
            values = self.load() or {}
            if not values.get("refresh_token"):
                raise DownloadError("config", "还没有登录 PikPak")
            with PikPakClient(values, persist=self.persist, transport=self.transport,
                              clock=self.clock) as client:
                yield client

    def submit(self, magnet: Magnet, target: str) -> RemoteStatus:
        with self.client() as client:
            parent = client.folder_id(target)
            probe = DownloadTask(0, magnet.info_hash, self.key, magnet.uri, magnet.name, target, "")
            existing = match_task(client.tasks(), probe)
            if existing is not None:
                status = task_status(existing)
                return RemoteStatus(status.state, status.remote_id, status.name, status.progress,
                                    status.message, adopted=True)
            created = client.add_offline(magnet.uri, parent)
        return RemoteStatus(RUNNING, str(created.get("id")), str(created.get("file_name") or
                                                                    created.get("name") or magnet.name))

    def status(self, task: DownloadTask) -> RemoteStatus:
        with self.client() as client:
            return task_status(match_task(client.tasks(), task))

    def landed(self, task: DownloadTask) -> bool:
        names = [name for name in (task.remote_name, task.display_name) if name]
        if not names:
            return False
        with self.client() as client:
            parent = client.folder_id(task.target)
            return any(client.has_child(parent, name) for name in names)

    def cancel(self, task: DownloadTask) -> None:
        if not task.remote_id:
            raise DownloadError("rejected", "这个任务还没有 PikPak 任务号，无法取消")
        with self.client() as client:
            client.delete_task(task.remote_id)


def login(load: Callable[[], dict], username: str, password: str, *, remember: bool,
          persist: Callable[[dict], None], transport: httpx.BaseTransport | None = None) -> dict:
    """设置页的登录。成功后凭据整份写回，密码只在 `remember` 时保留。

    要人机验证时抛 `CaptchaRequired`，设备号已经写回，用户完成验证后用同一个设备号重试。
    """
    username, password = str(username or "").strip(), str(password or "")
    if not username or not password:
        raise ValueError("请填写 PikPak 账号和密码")
    saved: dict = {}

    def keep(update: dict) -> None:
        saved.update(update)

    with SESSION_LOCK:
        start = {"device_id": (load() or {}).get("device_id") or new_device_id()}
        with PikPakClient(dict(start), persist=keep, transport=transport) as client:
            try:
                client.login(username, password)
            except CaptchaRequired:
                persist(start)
                raise
            except PikPakApiError as error:
                raise DownloadError("config", f"PikPak 登录失败：{error.description}"
                                              f"（{error.code or error.status}）") from None
        final = {key: value for key, value in saved.items() if key != "password"}
        if remember:
            final["password"] = password
        persist(final)
    return {"username": username, "remember": bool(remember)}


def account(values: dict | None) -> dict:
    """设置页显示用：登没登录、账号名、有没有存密码、怎么登的。不回 token 与密码本身。

    `method` 是 `browser`（网页端签发的 token）或 `password`（安卓客户端，账号密码换的）。
    """
    values = values or {}
    method = "password" if profile_for(values.get("client_id")) is ANDROID else "browser"
    return {"logged_in": bool(values.get("refresh_token")), "username": values.get("username", ""),
            "remember": bool(values.get("password")), "method": method}
