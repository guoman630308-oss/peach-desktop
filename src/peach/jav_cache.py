"""公开作品资料的发行日期缓存策略与可撤回磁盘快照。"""
from __future__ import annotations

from calendar import monthrange
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from filelock import FileLock

from .fsutil import atomic_write_text

DAY = 86400
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


def stable_after(released: str | None) -> date | None:
    """发行满一个日历月的日期；月底按目标月份的最后一天计算。"""
    try:
        issued = date.fromisoformat((released or '')[:10])
        year, month = divmod(issued.year * 12 + issued.month, 12)
        return date(year, month + 1, min(issued.day, monthrange(year, month + 1)[1]))
    except (ValueError, TypeError, OverflowError):
        return None


def cache_lifetime(released: str | None, today: date | None = None) -> int | None:
    """新作及日期未知的资料保存一天，超过一个月的成功快照固定保存。"""
    cutoff = stable_after(released)
    return None if cutoff and (today or date.today()) > cutoff else DAY


def cache_fresh(saved: float, released: str | None, now: float | None = None) -> bool:
    """固定快照须在发行满一个月后取得；发行期内的快照可再补齐一次。"""
    now = time.time() if now is None else now
    if type(saved) not in (int, float) or not math.isfinite(saved) or saved < 0 or saved > now:
        return False
    try:
        today, checked = datetime.fromtimestamp(now).date(), datetime.fromtimestamp(saved).date()
    except (ValueError, OverflowError, OSError):
        return False
    lifetime = cache_lifetime(released, today)
    if lifetime is None:
        return checked > stable_after(released)
    return now - saved < lifetime


@dataclass(frozen=True)
class Snapshot:
    value: object
    saved: float
    released: str | None
    revision: str

    def fresh(self, now: float) -> bool:
        return cache_fresh(self.saved, self.released, now)


class PublicSnapshots:
    """每个公开资料键独立保存；进程间锁合并读取，原子写入保留完整快照。"""

    def __init__(self, root: Path | None, *, clock=time.time):
        self.root = Path(root) if root is not None else None
        self.clock = clock

    def path(self, key: str) -> Path | None:
        digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
        return self.root / (digest + '.json') if self.root is not None else None

    def load(self, key: str) -> Snapshot | None:
        path = self.path(key)
        if path is None:
            return None
        try:
            if path.stat().st_size > MAX_SNAPSHOT_BYTES:
                return None
            row = json.loads(path.read_text(encoding='utf-8'))
            saved = row.get('saved')
            if (row.get('version') != 1 or row.get('key') != key
                    or type(saved) not in (int, float) or not math.isfinite(saved)
                    or saved < 0 or saved > self.clock() or not isinstance(row.get('revision'), str)
                    or (row.get('released') is not None and not isinstance(row['released'], str))):
                return None
            return Snapshot(row['value'], saved, row.get('released'), row['revision'])
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            return None

    def save(self, key: str, value: object, released: str | None) -> Snapshot:
        snapshot = Snapshot(value, self.clock(), released, uuid4().hex)
        path = self.path(key)
        if path is not None:
            encoded = json.dumps({'version': 1, 'key': key, 'saved': snapshot.saved,
                                  'released': released, 'revision': snapshot.revision, 'value': value},
                                 ensure_ascii=False, allow_nan=False)
            if len(encoded.encode('utf-8')) > MAX_SNAPSHOT_BYTES:
                raise ValueError('公开资料快照超过读取范围')
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, encoded, mode=0o600)
        return snapshot

    def acquire(self, key: str):
        path = self.path(key)
        if path is None:
            return nullcontext()
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(path) + '.lock', timeout=120)
