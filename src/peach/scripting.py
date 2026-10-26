"""`scripts/` 下各脚本共用的账本访问与取证约定。

这些脚本本来各自实现同一件事：只读连接写过三种 URI 形态，`--apply` 的备份要求有
`SystemExit`、`print`+`return 2`、`ValueError` 三种拒绝方式，User-Agent 有五个版本号
不同的副本，按主机限速有两份逐字重复的 `HostLimiter`。同一条判据有多份实现时，
修好一份不等于修好这件事——`HostLimiter` 的主机匹配就是活例子：一份落在点边界上，
另一份是裸子串，`"x.com" in "netflix.com"` 为真，于是无关主机被按 X 的节拍限速。

本模块只收口这些约定，不放任何业务逻辑；业务留在各自脚本里。

它住在 `src/peach/` 而不是 `scripts/`：脚本通过可编辑安装或测试入口强制的
`PYTHONPATH=<当前树>/src` 导入 `peach`，所以不需要（也不该）用 `sys.path.insert`
把 `scripts/` 变成一个包——那个写法在工作树里还会把 `peach` 解析到主检出。
"""
from __future__ import annotations

import argparse
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path

from .config import DATABASE_PATH
from .migrations import sqlite_backup

from .user_agent import USER_AGENT as USER_AGENT

#: `--apply` 缺 `--backup` 时的唯一拒绝话术。
BACKUP_REQUIRED = "--apply 必须同时给 --backup"


def readonly_uri(db_path: Path | str) -> str:
    """`mode=ro` 的 URI。路径先百分号转义，含 `#`/`?` 的目录名才不会被当查询串截断。"""
    return "file:" + urllib.parse.quote(Path(db_path).resolve().as_posix()) + "?mode=ro"


def open_readonly(db_path: Path | str) -> sqlite3.Connection:
    """打开只读连接。

    `mode=ro` 让「这一趟绝不写库」成为数据库层的硬保证，而不只是「代码里没写
    INSERT」这种靠读代码维持的约定。审计与 dry-run 一律走这里。
    """
    connection = sqlite3.connect(readonly_uri(db_path), uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def add_ledger_write_args(parser: argparse.ArgumentParser, *,
                          db_default: Path = DATABASE_PATH) -> argparse.ArgumentParser:
    """挂上真实写入脚本的三个标准参数：`--db`、`--apply`、`--backup`。

    参数名只有这一套，不收 `--database`、`--backup-dir` 这类同义写法：名字不同
    的同义参数会让「上次那条命令」在另一个脚本上直接报错，而报错信息只说缺参数。
    """
    parser.add_argument("--db", type=Path, default=db_default, help="账本路径")
    parser.add_argument("--apply", action="store_true",
                        help="真正写 ledger；默认只做 dry-run")
    parser.add_argument("--backup", type=Path,
                        help=f"{BACKUP_REQUIRED}：写库前的 SQLite 备份路径")
    return parser


def open_for_write(args: argparse.Namespace) -> sqlite3.Connection:
    """按 `--apply` 决定连接可写还是只读，可写前先落一份 SQLite 备份。

    三件事在这里一次做完，因为分开做过就漏过：dry-run 拿到的是 `mode=ro` 连接，写
    不进去而不是「碰巧没写」；`--apply` 缺 `--backup` 直接拒绝；备份用
    `Connection.backup()` 而不是文件复制——账本是 WAL 模式，已提交但未 checkpoint 的
    事务还在 `-wal` 里，只复制主库会得到一份少了最近改动、看起来却完全正常的账本。

    备份在任何写入之前完成，所以「备份到底是这次写之前还是之后的状态」不需要推断。
    """
    if not args.apply:
        return open_readonly(args.db)
    if not getattr(args, "backup", None):
        raise SystemExit(BACKUP_REQUIRED)
    sqlite_backup(Path(args.db), Path(args.backup))
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    return connection


#: 写入前后都要对一遍的基础计数。真实写入的验收口径是「前后计数 + integrity_check
#: + foreign_key_check」，这几张表是所有身份类脚本共同的分母。
_BASE_COUNTS = {
    "asset": "SELECT count(*) FROM asset",
    "entity": "SELECT count(*) FROM entity",
    "asset_entity": "SELECT count(*) FROM asset_entity",
    "entity_alias": "SELECT count(*) FROM entity_alias",
}


def counts_of(connection: sqlite3.Connection,
              extra: dict[str, str] | None = None) -> dict[str, int]:
    """基础计数，`extra` 追加本脚本自己关心的口径（键 -> 取一个整数的 SQL）。"""
    queries = dict(_BASE_COUNTS)
    queries.update(extra or {})
    return {key: connection.execute(sql).fetchone()[0] for key, sql in queries.items()}


def verify_after_write(connection: sqlite3.Connection) -> tuple[str, int]:
    """写完之后的两道自检：`integrity_check` 与 `foreign_key_check` 违规条数。"""
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    return str(integrity), len(violations)


def host_under(host: str, domains: tuple[str, ...]) -> bool:
    """`host` 是否就是这些域名之一或它们的子域。

    后缀匹配必须落在点边界上。`endswith` 会把 `notx.com` 判成 x.com 的子域，裸子串
    更宽：`"x.com" in "netflix.com"` 为真。
    """
    host = (host or "").casefold()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def hostname_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").casefold().removeprefix("www.")


class RateLimiter:
    """单一节拍的限速器：两次 `wait()` 之间至少隔 `interval` 秒。

    用它而不是在循环里写 `time.sleep(interval)`，是因为 sleep 版本睡的是「无条件
    interval」而不是「距离上次请求还差多少」：本地处理耗时会被白白叠加上去，一批
    几千条的抓取因此比设定的频率慢一大截，而看日志只觉得是网络慢。
    """

    def __init__(self, interval: float, *, clock=time.monotonic, sleeper=time.sleep):
        self._interval = max(0.0, float(interval))
        self._clock = clock
        self._sleeper = sleeper
        self._next = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        now = self._clock()
        delay = self._next - now
        if delay > 0:
            self._sleeper(delay)
            now = self._clock()
        self._next = now + self._interval


class HostLimiter:
    """按主机分别限速：每个主机一把锁、一个下次可发时刻。

    键按点边界匹配（见 `host_under`）；没有对上任何键的主机不限速。
    """

    def __init__(self, intervals: dict[str, float], *,
                 clock=time.monotonic, sleeper=time.sleep, default_interval: float = 0):
        self._intervals = dict(intervals)
        self._default_interval = max(0.0, default_interval)
        self._registry_lock = threading.Lock()
        self._locks = {host: threading.Lock() for host in self._intervals}
        self._next = {host: 0.0 for host in self._intervals}
        self._clock = clock
        self._sleeper = sleeper

    def _key(self, url: str) -> str | None:
        host = hostname_of(url)
        with self._registry_lock:
            key = next((h for h in self._intervals if host_under(host, (h,))), None)
            if key is None and self._default_interval > 0:
                key = host
                self._intervals[key] = self._default_interval
                self._locks[key] = threading.Lock()
                self._next[key] = 0.0
            return key

    def wait(self, url: str) -> None:
        key = self._key(url)
        if key is None:
            return
        with self._locks[key]:
            now = self._clock()
            delay = self._next[key] - now
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
            self._next[key] = now + self._intervals[key]
