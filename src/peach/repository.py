from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .catalog_rules import is_jav_code, normalise_code_key
from .entities import normalize_entity_name
from .regions import infer_region


class LedgerDatabase:
    """Shared SQLite connection and transaction boundary for one Peach app."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.write_lock = threading.Lock()
        self.after_commit = lambda: None

    def connect(self, *, write: bool = False, timeout: float = 30) -> sqlite3.Connection:
        target = (
            str(self.db_path)
            if write
            else self.db_path.resolve().as_uri() + "?mode=ro"
        )
        connection = sqlite3.connect(
            target, timeout=timeout, check_same_thread=False, uri=not write,
        )
        connection.row_factory = sqlite3.Row
        connection.create_function("is_jav_code", 1, is_jav_code, deterministic=True)
        connection.create_function(
            "normalise_code_key", 1, normalise_code_key, deterministic=True)
        # 产地推断要和 Python 侧同一份实现。SQL 里重写一遍前缀名单的话，账本筛出来的
        # 那批片和页面上标着的产地会从某一次改名单开始悄悄分家。
        connection.create_function(
            "infer_region", 2, infer_region, deterministic=True)
        # 标签归一化必须两边同一份。SQLite 自带的 lower() 只认 ASCII：西里尔、
        # 罗马数字 Ⅱ 这类字符它原样放过，而写入时用的是 Python 的 casefold，
        # 于是「隐藏这个标签」写进去的值和查询时算出的值对不上，隐藏静默失效。
        connection.create_function(
            "peach_normalize", 1, normalize_entity_name, deterministic=True)
        return connection

    @contextmanager
    def read_connection(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write_transaction(self, *, notify: bool = True, timeout: float | None = None):
        """写事务。`notify=False` 的写入不触发 `after_commit`。

        `after_commit` 在服务里是清聚合缓存。任务中心的心跳与进度每两秒写一行
        `task_run`，它和馆藏数据没有任何关系；跟着清一次缓存，等于让首页、统计和
        复核页在每个长任务运行期间全程失去缓存。

        `timeout` 给可以放弃的写入用：进程内的写锁与 SQLite 的写锁各最多等这么久，
        等不到抛 `sqlite3.OperationalError`，与 SQLite 自己等超时是同一种异常。不传则
        进程内一直排队、SQLite 等 30 秒。
        """
        if not self.write_lock.acquire(timeout=-1 if timeout is None else timeout):
            raise sqlite3.OperationalError("database is locked")
        try:
            connection = (self.connect(write=True) if timeout is None
                          else self.connect(write=True, timeout=timeout))
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
                if notify:
                    self.after_commit()
            finally:
                connection.close()
        finally:
            self.write_lock.release()


@dataclass(frozen=True)
class MediaAsset:
    id: int
    path: str | None
    snapshot_path: str | None
    location: str | None = None
    name: str | None = None
    duration: float | None = None
    size: int | None = None


class LedgerRepository:
    def __init__(self, database: Path | LedgerDatabase):
        self.database = (
            database if isinstance(database, LedgerDatabase) else LedgerDatabase(database)
        )
        self.db_path = self.database.db_path

    def media_asset(self, asset_id: int) -> MediaAsset | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT id,path,snapshot_path,location,name,duration,size "
                "FROM asset WHERE id=?",
                (asset_id,),
            ).fetchone()
        if row is None:
            return None
        return MediaAsset(
            row["id"],
            row["path"],
            row["snapshot_path"],
            row["location"],
            row["name"],
            row["duration"],
            row["size"],
        )
