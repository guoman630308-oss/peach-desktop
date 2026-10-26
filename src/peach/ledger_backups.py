"""账本备份的保留规则：`ledger.pre-<用途>-<时间戳>.db` 只留该留的，其余清退。

每个 `--apply` 脚本与 `peach migrate` 都在写入前留一份完整备份，每份和账本一样大
（一百多 MB）。备份的价值只在「这次写入被复核之前」，之后它就是一份没人会再读的旧账本。
三条规则任一成立就留：最近 `KEEP_RECENT` 份之内、还没满 `MIN_AGE`、或者比当前账本更新
（正常不会出现，出现了说明有事没弄清，先别删）。三条都不成立才删，连同 `-wal`／`-shm`
一起删。当前账本 `integrity_check` 不是 `ok` 时拒绝清退任何一份：那正是要回滚的时候。

主文件已经不在的 `-wal`／`-shm` 另外扫一遍。清退按 `.db` 列举，边车只跟着主文件走，所以
主文件删掉而边车没删掉的那一刻，这几个文件就再没有人会看它们一眼：下一轮列不到，只会
一直留在账本目录里。2026-09-13 在真实数据目录数到 20 个，最早的来自 08-21。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

BACKUP_PATTERN = re.compile(r"^ledger\.pre-.+\.db$")
SIDECAR_SUFFIXES = ("-wal", "-shm")
SIDECAR_PATTERN = re.compile(r"^ledger\.pre-.+\.db(?:" + "|".join(SIDECAR_SUFFIXES) + r")$")
KEEP_RECENT = 5
MIN_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class BackupPlan:
    keep: tuple[Path, ...]
    remove: tuple[Path, ...]
    refused: str | None = None   # 不为 None 时一份都不删，这里写原因
    removable_bytes: int = 0     # 算计划时量好，删完文件就量不到了
    orphans: tuple[Path, ...] = ()   # 主文件已不在的 `-wal`／`-shm`，和 remove 一起删


def _size_with_sidecars(path: Path) -> int:
    total = 0
    for candidate in (path, *(path.with_name(path.name + s) for s in SIDECAR_SUFFIXES)):
        try:
            total += candidate.stat().st_size
        except OSError:
            continue
    return total


def list_backups(live: Path) -> list[Path]:
    """同目录下所有备份，按修改时间从旧到新。"""
    if not live.parent.is_dir():
        return []
    found = [
        path for path in live.parent.iterdir()
        if path.is_file() and BACKUP_PATTERN.match(path.name)
    ]
    return sorted(found, key=lambda path: path.stat().st_mtime)


def orphan_sidecars(live: Path) -> list[Path]:
    """主文件已经不在的备份边车，按名字排序。"""
    if not live.parent.is_dir():
        return []
    found = []
    for path in live.parent.iterdir():
        if not path.is_file() or not SIDECAR_PATTERN.match(path.name):
            continue
        base = path.with_name(path.name.rsplit("-", 1)[0])
        if not base.is_file():
            found.append(path)
    return sorted(found)


def integrity(db: Path) -> str:
    try:
        connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return f"无法打开：{exc}"
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    except sqlite3.Error as exc:
        return f"检查失败：{exc}"
    finally:
        connection.close()


def _live_mtime(live: Path) -> float:
    """WAL 模式下已提交的改动先落在 `-wal`，主库文件的时间不一定动，取两者较新的。"""
    stamps = [live.stat().st_mtime]
    wal = live.with_name(live.name + "-wal")
    if wal.is_file():
        stamps.append(wal.stat().st_mtime)
    return max(stamps)


def plan(
    live: Path,
    *,
    keep_recent: int = KEEP_RECENT,
    min_age: timedelta = MIN_AGE,
    now: datetime | None = None,
) -> BackupPlan:
    backups = list_backups(live)
    orphans = tuple(orphan_sidecars(live))
    if not backups and not orphans:
        return BackupPlan((), ())
    if not live.is_file():
        return BackupPlan(tuple(backups), (), "当前数据库不存在，不清退任何备份")
    check = integrity(live)
    if check != "ok":
        return BackupPlan(tuple(backups), (), f"当前数据库 integrity_check 不是 ok：{check}")
    moment = now or datetime.now()
    live_mtime = _live_mtime(live)
    recent = set(backups[-keep_recent:]) if keep_recent > 0 else set()
    keep: list[Path] = []
    remove: list[Path] = []
    for backup in backups:
        mtime = backup.stat().st_mtime
        young = moment - datetime.fromtimestamp(mtime) < min_age
        if backup in recent or young or mtime > live_mtime:
            keep.append(backup)
        else:
            remove.append(backup)
    return BackupPlan(
        tuple(keep), tuple(remove), orphans=orphans,
        removable_bytes=(sum(_size_with_sidecars(path) for path in remove)
                         + sum(path.stat().st_size for path in orphans)))


def prune(
    live: Path,
    *,
    apply: bool,
    keep_recent: int = KEEP_RECENT,
    min_age: timedelta = MIN_AGE,
    now: datetime | None = None,
) -> BackupPlan:
    """算出计划；`apply` 为真且没有拒绝理由时真的删。删不掉的记日志、继续。"""
    decided = plan(live, keep_recent=keep_recent, min_age=min_age, now=now)
    if not apply or decided.refused:
        return decided
    doomed = [target for backup in decided.remove
              for target in (backup,
                             *(backup.with_name(backup.name + s) for s in SIDECAR_SUFFIXES))]
    for target in (*doomed, *decided.orphans):
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("数据库备份未能删除 %s: %s", target, exc)
    return decided
