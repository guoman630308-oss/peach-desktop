"""推送发现：文件一落地就进账本，不必等下一轮全量扫描。

两条通道、一个队列、一个出口：

- 本地来源（`local`）由 watchdog 订阅文件系统事件。网盘挂载不订阅——遍历 `A:`／`B:`
  就是走网络，把它挂上递归监视等于让 CloudDrive 替我们把整棵树拉一遍。
- 网盘来源由 CloudDrive2 的通知送进来（`POST /api/inbox/clouddrive`）。

两条通道都只做一件事：把「哪个来源的哪条账本路径」放进去抖队列。队列到期后调
`scan.ingest_path`，也就是全量扫描登记一个文件时走的那段代码；这里不新建第二条
入库逻辑，账本里也就不会出现第二种「新文件怎么变成一行」的写法。

**漏发由全量扫描兜底。** 事件通道没有送达保证：服务没起来的那段时间、CloudDrive 重启、
watchdog 的缓冲区溢出，都会静默丢事件。所以定期全量扫描一条不删，推送只是把「多久发现」
从一轮扫描缩短到几秒。

**云端路径不由本机 pathlib 解析。** CloudDrive2 给的是它自己挂载树里的 POSIX 路径
（`/115/影视/xxx.mp4`），本机这一侧是 Windows 盘符。`Path` 在 Windows 上会把前导 `/`
当成当前盘的根、把 `\\` 也当分隔符，在 macOS 上又会当成真实的根目录路径——两种都能拼出
一条看着像样、指向别处的路径。映射只用一张前缀表加纯字符串切分：前缀命中之后剩下的层级
按 `/` 拆开，逐段拒掉空串、`.`、`..` 和带分隔符或控制字符的段，再用 `PureWindowsPath`
与数据库路径前缀拼接。

**写完了没有，按大小稳定判。** 事件在第一个字节写下时就来了，那时去 stat 只会把一个
0 字节的半成品登记进账本。判据是：最后一次事件之后静置 `DEBOUNCE_SECONDS`，再隔
`SETTLE_SECONDS` 复测一次大小，两次相同才算写完；一直在长大就一直等，超过
`SETTLE_TIMEOUT_SECONDS` 放弃这一条，交回给全量扫描。
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Callable

from .fsutil import atomic_write_text

LOGGER = logging.getLogger(__name__)

#: 最后一次事件之后至少静置这么久才第一次探测。
DEBOUNCE_SECONDS = 5.0
#: 两次大小探测之间隔多久。相等才算写完。
SETTLE_SECONDS = 4.0
#: 一条路径最多在队列里待这么久。一直在长大的大文件由全量扫描接手。
SETTLE_TIMEOUT_SECONDS = 1800.0
#: 队列里最多存这么多条待办。事件风暴（整目录移动、批量下载完成）会一次送来几万条，
#: 超出之后新键直接丢弃并计数——丢掉的那些正是全量扫描存在的理由。
MAX_PENDING = 4096
#: 登记的并发上限：一个工作线程，串行调 `ingest_path`。账本是单写者，放开并发只会
#: 把 SQLite 的写锁冲突搬到这里来。
MAX_WORKERS = 1

#: 下载中、转码中或编辑器留下的半成品。这些名字不该进账本，等它改成最终名字再说。
IGNORED_SUFFIXES = frozenset({
    ".tmp", ".temp", ".part", ".partial", ".crdownload", ".download", ".downloading",
    ".!qb", ".!ut", ".aria2", ".bak", ".swp",
})
#: 系统与同步工具自己的落脚文件。
IGNORED_NAMES = frozenset({
    "desktop.ini", "thumbs.db", ".ds_store", ".localized",
})

#: CloudDrive2 那一侧要填的地址路径。页面上照着显示，两处不能各写一份。
WEBHOOK_PATH = "/api/inbox/clouddrive"

#: 设置与密钥的落点。开关与前缀表是本机偏好，密钥是凭据，两者分开放。
SETTINGS_FILENAME = "push-discovery.json"
SECRET_FILENAME = "push-discovery.json"
SECRET_BYTES = 32
#: 密钥只走请求头。CloudDrive2 的 `[file_system_watcher.headers]` 本来就能填自定义头，
#: 放进查询串只会让它进访问日志。
SECRET_HEADER = "X-Peach-Secret"

#: CloudDrive2 通知里算「有个文件在这里出现了」的动作。`delete` 不在里面：账本不因为
#: 一次事件删行，去留仍由资源同步对账决定。
CLOUD_ACTIONS = frozenset({"create", "rename"})
#: `is_dir` 会被 CloudDrive2 的模板渲染成字符串，所以两种形态都要认。
_TRUE_WORDS = frozenset({"true", "1", "yes"})


class CloudPathError(ValueError):
    """云端路径映射不出账本路径。消息可以直接给人看。"""


def ignored(name: str) -> bool:
    """这个文件名要不要直接丢掉。"""
    lowered = name.casefold()
    if lowered in IGNORED_NAMES or lowered.startswith("."):
        return True
    return os.path.splitext(lowered)[1] in IGNORED_SUFFIXES


# ---------------------------------------------------------------- 云端路径映射


def _clean_segments(tail: str) -> tuple[str, ...]:
    """把云端路径里前缀之后那一段拆成层级，可疑的一律拒绝。

    纯字符串切分，不碰 `Path`：判定必须与本机是哪个平台无关，否则同一条请求在
    Windows 和 macOS 上会拼出两条不同的账本路径。
    """
    parts: list[str] = []
    for segment in tail.split("/"):
        if not segment or segment == ".":
            continue
        if (segment == ".." or "\\" in segment or ":" in segment
                or any(character < " " for character in segment)):
            raise CloudPathError(f"✗ 云端路径里有不能接受的层级：{segment!r}")
        parts.append(segment)
    if not parts:
        raise CloudPathError("✗ 云端路径在前缀之后没有内容")
    return tuple(parts)


def _truthy(raw) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().casefold() in _TRUE_WORDS


def parse_notification(body) -> list[str]:
    """从 CloudDrive2 的通知里取出要登记的云端路径。

    载荷形状见 `docs/reference-snapshots/amane-watcher.md`：顶层一个 `data` 数组，每项带
    `action`、`is_dir`、`source_file`、`destination_file`。顶层与条目里的其余字段
    （`device_name`、`event_name`、`send_time` 一类）一律忽略——多一个字段不是错误。

    目录事件不进队列：一个目录里有几个文件只有遍历才知道，而遍历网盘挂载正是推送要
    避开的那件事。整目录搬进来由下一轮全量扫描收，那条路本来就没删。
    """
    items = body.get("data") if isinstance(body, dict) else None
    found: list[str] = []
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        if str(item.get("action") or "").strip().casefold() not in CLOUD_ACTIONS:
            continue
        if _truthy(item.get("is_dir")):
            continue
        # 改名报的是同一件事的两端，只有新名字对应着此刻磁盘上的文件。
        path = str(item.get("destination_file") or "").strip() or str(
            item.get("source_file") or "").strip()
        if path:
            found.append(path)
    return found


#: CloudDrive2「设置 → Webhooks」里一条 webhook 的整段 TOML。形状照它自带的默认模板，
#: 三处按本机填：地址、端点路径、共享密钥。`{base_url}`、`{action}` 这些花括号是
#: CloudDrive2 自己的占位符，Python 这边不碰，所以模板用 `%s` 代入。
#:
#: 两个 watcher 在它那边是同一个结构（`WebHookConfigItem`，5 个字段），五个字段一个
#: 都不能省：关掉的那个只写 `enabled = false` 的话，整份配置在它的列表里标「无效」。
#: Webhook 是它的会员功能，非会员时配置照样显示有效，却一条也不发。
_CLOUDDRIVE_CONFIG = """[global_params]
base_url = "%s"
enabled = true

[global_params.default_headers]
content-type = "application/json"
user-agent = "clouddrive2/{version}"

[file_system_watcher]
url = "{base_url}%s"
method = "POST"
enabled = true
body = '''
{
  "data": [
    {
      "action": "{action}",
      "is_dir": "{is_dir}",
      "source_file": "{source_file}",
      "destination_file": "{destination_file}"
    }
  ]
}
'''

[file_system_watcher.headers]
%s = "%s"

[mount_point_watcher]
url = "{base_url}%s"
method = "POST"
enabled = false
body = '{"data": []}'

[mount_point_watcher.headers]
"""


def clouddrive_config(origin: str, secret: str) -> str:
    """整段抄进 CloudDrive2 的配置文本。两样缺一样就给空串，页面据此说还不能抄。

    地址只能是 HTTPS。80 口上那条服务对 `POST` 回的是 426 而不是重定向——跨 origin
    的 307 会让 CloudDrive2 把密钥和正文原样再发一遍到另一个地址上。

    正文只留 Peach 要读的那四个字段，默认模板里的设备名、事件时间那些一概不要：
    `parse_notification` 本来就忽略它们，少发一样东西就少一处能对不上的地方。
    挂载点通知关掉——它送的是挂载状态，一条路径都没有，收下来只是空转一次队列。
    """
    origin, secret = str(origin or "").strip().rstrip("/"), str(secret or "").strip()
    if not origin or not secret:
        return ""
    return _CLOUDDRIVE_CONFIG % (origin, WEBHOOK_PATH, SECRET_HEADER, secret, WEBHOOK_PATH)


def normalise_prefix(raw: str) -> str:
    """前缀统一成 `/foo` 形态：前导一个斜杠、尾部不留斜杠。"""
    text = str(raw or "").strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    text = "/" + text.strip("/")
    return text


@dataclass(frozen=True)
class CloudPrefix:
    """一条「CloudDrive2 挂载目录前缀 → 数据库路径前缀」的对应关系。"""
    prefix: str
    root: str


def map_cloud_path(cloud_path: str, prefixes: tuple[CloudPrefix, ...]) -> str:
    """把 CloudDrive2 给的云端路径换成账本口径的 Windows 路径。

    前缀重叠时取最长的那条：`/115` 与 `/115/影视` 同时登记时，后者说了算。
    """
    text = str(cloud_path or "").strip().replace("\\", "/")
    if not text:
        raise CloudPathError("✗ 请求里没有云端路径")
    while "//" in text:
        text = text.replace("//", "/")
    if not text.startswith("/"):
        text = "/" + text
    best: tuple[int, CloudPrefix, str] | None = None
    for entry in prefixes:
        prefix = normalise_prefix(entry.prefix)
        if text == prefix:
            raise CloudPathError("✗ 云端路径就是前缀本身，没有文件")
        if not text.startswith(prefix + "/"):
            continue
        depth = len(prefix)
        if best is None or depth > best[0]:
            best = (depth, entry, text[len(prefix):])
    if best is None:
        known = "、".join(normalise_prefix(entry.prefix) for entry in prefixes) or "（一条都没配）"
        raise CloudPathError(f"✗ {text} 不在任何已配置的前缀下；已知：{known}")
    return str(PureWindowsPath(best[1].root).joinpath(*_clean_segments(best[2])))


# ---------------------------------------------------------------- 去抖队列


@dataclass
class _Pending:
    location: str
    path: str
    #: 最早可以探测的时刻。每来一次同路径的事件就往后推。
    due: float
    #: 上一次探测到的大小；`None` 表示还没探测过。
    size: int | None = None
    first_seen: float = 0.0


@dataclass
class QueueCounters:
    submitted: int = 0
    ingested: int = 0
    missing: int = 0
    dropped: int = 0
    timed_out: int = 0
    failed: int = 0


class DiscoveryQueue:
    """两条通道共用的去抖队列。

    `tick` 是纯粹的推进一步：调用方给当前时刻，它把到期的那些探测一遍、该登记的登记。
    工作线程只是按 `tick` 报回的下一次到期时间去睡，所以整套判定在测试里可以用假时钟
    一格一格走完，不必真的等几秒。
    """

    def __init__(
        self, ingest: Callable[[str, str], bool], probe: Callable[[str, str], int | None], *,
        debounce: float = DEBOUNCE_SECONDS, settle: float = SETTLE_SECONDS,
        timeout: float = SETTLE_TIMEOUT_SECONDS, max_pending: int = MAX_PENDING,
    ):
        self.ingest = ingest
        self.probe = probe
        self.debounce = debounce
        self.settle = settle
        self.timeout = timeout
        self.max_pending = max_pending
        self.counters = QueueCounters()
        self._pending: dict[tuple[str, str], _Pending] = {}
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._stopping = False
        self.last_path = ""
        self.last_ingested_at: float | None = None
        self.last_error = ""

    # -- 入口

    def submit(self, location: str, path: str, now: float) -> bool:
        """放一条待办进来；同一条路径再来只是把到期时间往后推。"""
        key = (location, path)
        with self._wake:
            entry = self._pending.get(key)
            if entry is None:
                if len(self._pending) >= self.max_pending:
                    self.counters.dropped += 1
                    return False
                self._pending[key] = _Pending(location, path, now + self.debounce,
                                              first_seen=now)
            else:
                # 又写了一次：静置窗口重新开始，大小也重新测。
                entry.due = now + self.debounce
                entry.size = None
            self.counters.submitted += 1
            self._wake.notify()
        return True

    # -- 推进

    def due_at(self) -> float | None:
        with self._lock:
            return min((entry.due for entry in self._pending.values()), default=None)

    def tick(self, now: float) -> int:
        """把到期的待办推进一步，返回这一轮实际登记的条数。"""
        with self._lock:
            ready = [entry for entry in self._pending.values() if entry.due <= now]
        done = 0
        for entry in ready:
            done += self._advance(entry, now)
        return done

    def _advance(self, entry: _Pending, now: float) -> int:
        if now - entry.first_seen > self.timeout:
            self._forget(entry)
            self.counters.timed_out += 1
            return 0
        size = self.probe(entry.location, entry.path)
        if size is None:
            self._forget(entry)
            self.counters.missing += 1
            return 0
        if entry.size != size:
            # 还在写：记下这次的大小，隔一个静置窗口再测。
            with self._lock:
                entry.size = size
                entry.due = now + self.settle
            return 0
        self._forget(entry)
        return self._ingest(entry)

    def _ingest(self, entry: _Pending) -> int:
        try:
            wrote = self.ingest(entry.location, entry.path)
        except Exception as error:  # 一条路径失败不该带走整条队列
            LOGGER.exception("推送发现登记失败：%s", entry.path)
            self.counters.failed += 1
            self.last_error = str(error)
            return 0
        if not wrote:
            self.counters.missing += 1
            return 0
        self.counters.ingested += 1
        self.last_path = entry.path
        self.last_ingested_at = time.time()
        self.last_error = ""
        return 1

    def _forget(self, entry: _Pending) -> None:
        with self._lock:
            self._pending.pop((entry.location, entry.path), None)

    # -- 工作线程

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping = False
        self._thread = threading.Thread(
            target=self._loop, name="PeachPushDiscovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        with self._wake:
            self._stopping = True
            self._wake.notify_all()
        thread.join(timeout=5)
        self._thread = None

    def _loop(self) -> None:
        while True:
            with self._wake:
                if self._stopping:
                    return
                due = min((entry.due for entry in self._pending.values()), default=None)
                delay = 1.0 if due is None else max(0.0, due - time.monotonic())
                if delay > 0:
                    self._wake.wait(min(delay, 1.0))
                if self._stopping:
                    return
            self.tick(time.monotonic())

    def snapshot(self) -> dict:
        with self._lock:
            pending = len(self._pending)
        return {
            "pending": pending,
            "submitted": self.counters.submitted,
            "ingested": self.counters.ingested,
            "missing": self.counters.missing,
            "dropped": self.counters.dropped,
            "timed_out": self.counters.timed_out,
            "failed": self.counters.failed,
            "last_path": self.last_path,
            "last_ingested_at": self.last_ingested_at,
            "last_error": self.last_error,
        }


# ---------------------------------------------------------------- 设置与密钥


@dataclass(frozen=True)
class PushDiscoveryConfig:
    #: 总开关。关掉就回到纯扫描：不起 watchdog，webhook 一律 404。
    enabled: bool = False
    #: 本地来源的文件系统事件通道。
    watch_local: bool = True
    #: CloudDrive2 通知通道。
    cloud: bool = True
    prefixes: tuple[CloudPrefix, ...] = ()

    def payload(self) -> dict:
        return {
            "enabled": self.enabled, "watch_local": self.watch_local, "cloud": self.cloud,
            "prefixes": [{"prefix": entry.prefix, "root": entry.root}
                         for entry in self.prefixes],
        }


def _prefix_rows(raw, declared_roots) -> tuple[CloudPrefix, ...]:
    """统一前缀写法，并核对数据库路径前缀是否已配置在 `[media.locations]` 中。

    用户写错盘符会使推送来的路径无法转换为本机路径，也无法通过目录访问权限检查。
    在保存时拒绝这种配置，避免直到打开媒体记录时才发现文件无法读取。
    """
    known = {str(root).casefold(): str(root)
             for roots in declared_roots.values() for root in roots}
    rows: list[CloudPrefix] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue
        prefix = normalise_prefix(item.get("prefix", ""))
        root = str(item.get("root") or "").strip()
        if prefix == "/" or not root:
            continue
        settled = known.get(root.casefold())
        if settled is None:
            shown = "、".join(sorted(known.values())) or "（设置文件里一个都没有）"
            raise ValueError(f"{root} 不是已声明的媒体根；已知：{shown}")
        if prefix in seen:
            raise ValueError(f"前缀 {prefix} 登记了不止一次")
        seen.add(prefix)
        rows.append(CloudPrefix(prefix, settled))
    return tuple(rows)


class PushDiscoverySettings:
    """开关与前缀表的落点。它是本机偏好，不是账本真相。"""

    def __init__(self, state_root: Path):
        self.path = Path(state_root) / SETTINGS_FILENAME

    def load(self, declared_roots) -> PushDiscoveryConfig:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return PushDiscoveryConfig()
        except (OSError, ValueError):
            LOGGER.warning("推送发现设置读不出来，按默认值跑：%s", self.path)
            return PushDiscoveryConfig()
        if not isinstance(payload, dict):
            return PushDiscoveryConfig()
        try:
            prefixes = _prefix_rows(payload.get("prefixes"), declared_roots)
        except ValueError:
            # 存进去的时候核对过；此刻对不上多半是媒体根改了。宁可让云端通道空转，
            # 也不要拿一张过期的表往账本里写路径。
            LOGGER.warning("推送发现的前缀表与当前媒体根对不上，云端通道按未配置处理")
            prefixes = ()
        return PushDiscoveryConfig(
            enabled=payload.get("enabled") is True,
            watch_local=payload.get("watch_local") is not False,
            cloud=payload.get("cloud") is not False,
            prefixes=prefixes,
        )

    def save(self, body: dict, declared_roots) -> PushDiscoveryConfig:
        if not isinstance(body, dict):
            raise ValueError("设置必须是一个对象")
        config = PushDiscoveryConfig(
            enabled=body.get("enabled") is True,
            watch_local=body.get("watch_local") is not False,
            cloud=body.get("cloud") is not False,
            prefixes=_prefix_rows(body.get("prefixes"), declared_roots),
        )
        atomic_write_text(
            self.path,
            json.dumps(config.payload(), ensure_ascii=False, indent=2) + "\n")
        return config


class WebhookSecret:
    """CloudDrive2 那一侧要填的共享密钥。

    它和 Peach 的访问口令是两件事：口令是人登录用的，这一份只给一台本机服务往一个
    端点推路径。分开存也就能单独轮换，轮换不会把所有已登录的浏览器踢下线。
    """

    def __init__(self, secrets_root: Path):
        self.path = Path(secrets_root) / SECRET_FILENAME

    def read(self) -> str:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        return str(payload.get("secret") or "") if isinstance(payload, dict) else ""

    def ensure(self) -> str:
        return self.read() or self.rotate()

    def rotate(self) -> str:
        value = secrets.token_urlsafe(SECRET_BYTES)
        atomic_write_text(self.path, json.dumps({"secret": value}) + "\n", mode=0o600)
        return value

    def matches(self, supplied: str) -> bool:
        stored = self.read()
        if not stored or not supplied:
            return False
        return hmac.compare_digest(str(supplied).encode(), stored.encode())


def allowed_source(host: str) -> bool:
    """只收回环与局域网来的请求。

    CloudDrive2 和 Peach 在同一台机器或同一个局域网里；公网来的请求没有任何理由
    出现在这个端点上，密钥对不对都先不看。
    """
    try:
        address = ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)


# ---------------------------------------------------------------- 服务


@dataclass
class ChannelState:
    """一条通道此刻在不在，以及为什么不在。"""
    running: bool = False
    message: str = ""
    roots: tuple[str, ...] = field(default_factory=tuple)


class PushDiscoveryService:
    """把设置、队列、本地监视与云端入口装配成一件东西。

    `start()` 自己判断开关：调用方（`api.lifespan`）无条件调用它，开关关着就什么也不做。
    """

    def __init__(
        self, *, state_root: Path, secrets_root: Path, db_path: Path,
        declared_roots, mounts, available: bool = True,
        debounce: float = DEBOUNCE_SECONDS, settle: float = SETTLE_SECONDS,
        ffprobe: Callable[[], object] | None = None,
        after_ingest: Callable[[], None] | None = None,
    ):
        self.settings = PushDiscoverySettings(state_root)
        #: 取 ffprobe 的回调，登记完视频当场探时长用；每次现取，装上 FFmpeg 不必重启。
        self.ffprobe = ffprobe
        #: 登记落库之后调一次。入库走 `scan` 自己的连接，不经过 `write_transaction`，
        #: 服务里的聚合缓存靠它作废，新文件才会立刻出现在统计与复核里。
        self.after_ingest = after_ingest
        self.secret = WebhookSecret(secrets_root)
        self.db_path = Path(db_path)
        self.declared_roots = {key: tuple(value) for key, value in declared_roots.items()}
        self.mounts = {key: tuple(value) for key, value in mounts.items()}
        self.available = bool(available)
        self.config = self.settings.load(self.declared_roots)
        self.queue = DiscoveryQueue(
            self._ingest, self._probe, debounce=debounce, settle=settle)
        self.local_channel = ChannelState()
        self._observer = None
        self._lock = threading.Lock()

    # -- 出口：两条通道最后都走这里

    def _local_path(self, location: str, path: str) -> Path:
        from .scan import walk_root_for
        ledger_dir = str(PureWindowsPath(path).parent)
        directory = walk_root_for(
            location, ledger_dir, declared_roots=self.declared_roots, mounts=self.mounts)
        return directory / PureWindowsPath(path).name

    def _probe(self, location: str, path: str) -> int | None:
        try:
            return self._local_path(location, path).stat().st_size
        except (OSError, ValueError):
            return None

    def _ingest(self, location: str, path: str) -> bool:
        from .record_rehome import reattach_in
        from .scan import ingest_path
        result = ingest_path(self.db_path, location, path,
                             declared_roots=self.declared_roots, mounts=self.mounts)
        # 附属文件按「处理完了」计：文件在，只是按规则不登记，不是「文件不在」。
        if result.found and not result.sidecar:
            self._measure(location, path)
            # 登记时已按番号接回过一轮；探到时长之后，要靠时长分辨的那几条再判一次。
            reattach_in(self.db_path, [result.asset_id] if result.new else [])
        if result.found and self.after_ingest:
            self.after_ingest()
        return result.found

    def _measure(self, location: str, path: str) -> None:
        """登记完顺手探时长与分辨率。探不成不影响这一条已经入库，全量扫描还会再补。"""
        from .media_probe import probe_path
        choice = self.ffprobe() if self.ffprobe else None
        try:
            probe_path(self.db_path, str(choice.path) if choice else None, location, path)
        except sqlite3.Error:
            LOGGER.exception("推送发现探测时长失败：%s", path)

    def submit(self, location: str, path: str) -> bool:
        if ignored(PureWindowsPath(path).name):
            return False
        return self.queue.submit(location, path, time.monotonic())

    def ingest_now(self, location: str, path: str) -> bool:
        """定向登记一条路径，不经去抖队列，开关关着也照样登记。

        云下载对账时用：远端已经完成、文件就在挂载目录里，推送发现那一侧却没送来通知
        （没开、没配会员，或通知丢了）。文件不在返回假。
        """
        if ignored(PureWindowsPath(path).name):
            return False
        return self._ingest(location, path)

    def submit_cloud(self, cloud_path: str) -> str:
        """webhook 收到一条云端路径：映射成账本路径再进队列，返回那条账本路径。"""
        if not self.config.cloud:
            raise CloudPathError("✗ 云端通道没有打开")
        path = map_cloud_path(cloud_path, self.config.prefixes)
        from .platform import resolve_location
        location = resolve_location(path, self.declared_roots)[0]
        if location is None:
            raise CloudPathError(f"✗ {path} 不在任何媒体来源已配置的目录下")
        self.submit(location, path)
        return path

    # -- 本地通道

    def _local_roots(self) -> list[tuple[str, str, Path]]:
        """列出本地来源的数据库路径前缀及其本机目录；不包含网盘来源。"""
        from .scan import walk_root_for
        found: list[tuple[str, str, Path]] = []
        for root in self.declared_roots.get("local", ()):
            try:
                directory = walk_root_for(
                    "local", root, declared_roots=self.declared_roots, mounts=self.mounts)
            except ValueError:
                continue
            if directory.is_dir():
                found.append(("local", root, directory))
        return found

    def start(self) -> None:
        if not (self.available and self.config.enabled):
            return
        # 设置文件可能是从别处拷来的，或者密钥文件被删过：通道开着却没有密钥，webhook 就
        # 永远收不下任何请求，而页面上只写「还没有生成」，没人看得出该点哪里。开机补一份。
        self._ensure_secret()
        self.queue.start()
        self._start_local()

    def _ensure_secret(self) -> None:
        if self.config.cloud:
            self.secret.ensure()

    def _start_local(self) -> None:
        if not self.config.watch_local:
            self.local_channel = ChannelState(False, "本地通道没有打开")
            return
        roots = self._local_roots()
        if not roots:
            self.local_channel = ChannelState(False, "本地来源在这台机器上没有可监视的目录")
            return
        try:
            from watchdog.observers import Observer
        except ImportError as error:  # 打包漏装时不该让整条服务起不来
            self.local_channel = ChannelState(False, f"文件监视不可用：{error}")
            return
        handler = _LocalEventHandler(self)
        observer = Observer()
        for location, root, directory in roots:
            handler.register(location, root, directory)
            observer.schedule(handler, str(directory), recursive=True)
        try:
            observer.start()
        except OSError as error:
            self.local_channel = ChannelState(False, f"文件监视启动失败：{error}")
            return
        self._observer = observer
        self.local_channel = ChannelState(
            True, "", tuple(root for _location, root, _ in roots))

    def stop(self) -> None:
        observer, self._observer = self._observer, None
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)
        self.local_channel = ChannelState()
        self.queue.stop()

    # -- 设置

    def save(self, body: dict) -> dict:
        if not self.available:
            raise ValueError("推送发现只在允许修改数据库的设备上可用")
        with self._lock:
            self.config = self.settings.save(body, self.declared_roots)
            # 密钥由 `start` 补：云端通道一打开，CloudDrive2 那一侧马上要填，页面得当场给出来。
            self.stop()
            self.start()
        return self.snapshot(reveal=True)

    def rotate_secret(self) -> dict:
        if not self.available:
            raise ValueError("推送发现只在允许修改数据库的设备上可用")
        self.secret.rotate()
        return self.snapshot(reveal=True)

    def snapshot(self, *, reveal: bool = False) -> dict:
        """状态。`reveal` 只给配置页那条本机路由：密钥要能抄进 CloudDrive2。"""
        payload = self.config.payload()
        payload.update({
            "available": self.available,
            "secret": self.secret.read() if reveal else "",
            "secret_set": bool(self.secret.read()),
            "endpoint": WEBHOOK_PATH,
            "local_running": self.local_channel.running,
            "local_message": self.local_channel.message,
            "local_roots": list(self.local_channel.roots),
            "queue": self.queue.snapshot(),
            "media_roots": [root for roots in self.declared_roots.values() for root in roots],
        })
        return payload


class _LocalEventHandler:
    """watchdog 的事件回调。

    只认「有个文件在这里出现了」这一类：新建、改完、移动过来。删除不进队列——账本
    不因为一次事件删行，这是扫描层早就定下的边界（本次没扫到只会让 `last_seen`
    落后，去留由资源同步对账决定）。
    """

    def __init__(self, service: PushDiscoveryService):
        self.service = service
        self.roots: list[tuple[str, PureWindowsPath, Path]] = []

    def register(self, location: str, declared_root: str, directory: Path) -> None:
        self.roots.append((location, PureWindowsPath(declared_root), directory))

    def dispatch(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        for attribute in ("dest_path", "src_path"):
            raw = getattr(event, attribute, "")
            if raw and getattr(event, "event_type", "") in _LOCAL_EVENTS:
                self._offer(raw)
                return

    def _offer(self, raw: str) -> None:
        local = Path(os.fsdecode(raw))
        for location, declared_root, directory in self.roots:
            try:
                tail = local.relative_to(directory).parts
            except ValueError:
                continue
            if not tail:
                return
            self.service.submit(location, str(declared_root.joinpath(*tail)))
            return


#: 只有这几类事件算「有个文件在这里出现了」。`deleted` 不在里面。
_LOCAL_EVENTS = frozenset({"created", "modified", "moved", "closed"})
