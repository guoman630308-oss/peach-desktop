"""资源对账：把账本里的记录、来源目录和磁盘上真实存在的文件对齐。

从 `web_contract` 拆出。这一域自己持有扫描线程状态、目录遍历和孤儿缓存清理，
和浏览、复核没有共享逻辑，留在契约巨石里只是让那个文件更难读。

用户在网盘客户端里删掉一个目录，本机看到的是三样东西：账本里一批指向不存在文件的
行、盘上留下的空壳目录、这些行生成过的缓存。一次检查把三样一起报出来，一次执行
一起清掉（ADR-0036）。指向不存在文件的行分两档：带个人记录的标「已消失」留着，
其余的永久删除（ADR-0087）。标了已消失的那几行若在库里唯一对得上另一个版本，当场把
记录接过去（`record_rehome.reattach_vanished`），回执单报「已接到另一个版本」。

`source_is_online` 也在这里：判断某个来源的根挂载没挂载，只有对账要问这件事。
`w_purge_missing` 是按目录的那个入口，服务详情页上「这个目录我刚整理过」，
判缺失与整库扫描共用同一套目录枚举。
"""
from __future__ import annotations

import errno
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PureWindowsPath
from typing import Callable, Protocol, Sequence

from . import jav_poster_crop
from .catalog_rules import dir_expr, normalise_code_key, photo_set_title
from .config import LOCATION_ROOT_DECLARATIONS
from .jobs import BackgroundJob
from .media import normalized_path
from .media_sources import SCANNABLE_LOCATIONS
from .personal_records import VANISHED, mark_vanished, record_holders
from .platform import is_unmapped, root_online, translate_ledger_path, within_root
from .record_rehome import reattach_vanished

#: 「同步删除」回执里，记录已接到另一个版本、旧行已删的那一条。
REATTACHED = "reattached"


class ResourceSyncContract(Protocol):
    """对账需要契约提供的能力；比整个 WebContract 小得多。"""

    db_path: Path
    snapshot_root: Path | None
    cover_root: Path
    poster_root: Path
    avatar_root: Path
    photo_root: Path
    timeline_root: Path
    stream_root: Path
    transcode_root: Path
    resource_cleanup_enabled: bool
    resource_scan: BackgroundJob
    resource_apply_job: BackgroundJob

    def cache_bust(self) -> None: ...
    def read_connection(self): ...
    def write_transaction(self): ...


def source_is_online(location: str) -> bool:
    """这个来源整体在不在线。对账前的唯一闸门。"""
    declared = LOCATION_ROOT_DECLARATIONS.get(location)
    if not declared:
        return False
    # 几个根都在才算在线：少一块盘就对账，那块盘上的文件会被整批判成丢失。
    for root in declared:
        resolved = translate_ledger_path(root)
        if is_unmapped(resolved) or not root_online(resolved):
            return False
    return True


RESOURCE_SCAN_WORKERS = 8


def configured_resource_locations() -> tuple[str, ...]:
    """核对已声明的媒体来源，离线状态由扫描阶段报告。"""
    return tuple(location for location, roots in LOCATION_ROOT_DECLARATIONS.items()
                 if location in SCANNABLE_LOCATIONS and roots)


def _scan_resource_directory(
    item: tuple[Path, dict[str, list[int]]],
) -> tuple[list[int], int]:
    parent, expected = item
    present: set[str] = set()
    unreadable = 0
    try:
        with os.scandir(parent) as entries:
            for entry in entries:
                key = entry.name.casefold()
                if key not in expected:
                    continue
                try:
                    if entry.is_file():
                        present.add(key)
                except OSError:
                    # The name was returned but its type could not be read.  Preserve the
                    # ledger row and report it as unreadable instead of declaring deletion.
                    present.add(key)
                    unreadable += len(expected[key])
    except FileNotFoundError:
        return [asset_id for ids in expected.values() for asset_id in ids], 0
    except OSError:
        return [], sum(len(ids) for ids in expected.values())
    return (
        [asset_id for key, ids in expected.items() if key not in present for asset_id in ids],
        unreadable,
    )


def _directory_key(path: str | os.PathLike[str]) -> str:
    """同一个目录只数一次：账本枚举与目录遍历报上来的写法不一定相同。"""
    return os.path.normcase(os.fspath(path))


def _missing_resource_ids(
    rows: Sequence, unreadable_dirs: set[str] | None = None,
) -> tuple[list[int], int]:
    """Compare ledger paths one directory listing at a time.

    Cloud mounts make one ``stat`` request per path painfully slow.  ``scandir`` reuses the
    directory enumeration that the filesystem already returns, while retaining the same
    case-insensitive Windows path semantics.  An unreadable directory is skipped rather than
    being mistaken for a directory that the user deleted; ``unreadable_dirs`` collects it.
    """
    directories: dict[Path, dict[str, list[int]]] = {}
    for row in rows:
        path = translate_ledger_path(row["path"])
        expected = directories.setdefault(path.parent, {})
        expected.setdefault(path.name.casefold(), []).append(int(row["id"]))

    directory_items = list(directories.items())
    if len(directory_items) <= 1:
        results = [_scan_resource_directory(item) for item in directory_items]
    else:
        # CloudDrive directory reads are latency-bound metadata requests.  Keep concurrency
        # deliberately small: enough to hide round trips without turning a scan into a media
        # download or overwhelming either mounted provider.
        with ThreadPoolExecutor(
            max_workers=min(RESOURCE_SCAN_WORKERS, len(directory_items)),
            thread_name_prefix="PeachResourceScan",
        ) as executor:
            results = list(executor.map(_scan_resource_directory, directory_items))
    missing = [asset_id for ids, _unreadable in results for asset_id in ids]
    unreadable = sum(count for _ids, count in results)
    if unreadable_dirs is not None:
        unreadable_dirs.update(_directory_key(parent)
                               for (parent, _expected), (_ids, count) in zip(directory_items, results)
                               if count)
    return missing, unreadable


def vanished_asset_rows(
    contract: ResourceSyncContract, location: str, *,
    unreadable_dirs: set[str] | None = None,
) -> list:
    """这个来源上文件已经不在的行，回收站里的也算，已标「已消失」的不算。

    问的是「账本这一行指的东西还在不在」，与这一行是否在回收站无关：文件已经在网盘那边
    删掉了，回收站里那一行同样指不到任何东西。已消失的行早就知道文件不在，它们
    留着是为了个人记录，归孤儿记录列表处理，不再进每一轮的候选。

    调用方必须先确认这个来源在线（`source_is_online`）：盘没挂上时目录读不到，
    每一条都会被判成文件没了。

    目录枚举出来的名单还要逐条 `stat` 复核一遍才作数，理由见 `_confirm_vanished`。
    """
    with contract.read_connection() as connection:
        rows = connection.execute(
            "SELECT id,path,snapshot_path,disposal FROM asset "
            "WHERE path IS NOT NULL AND location=? AND COALESCE(disposal,'')<>? ORDER BY id",
            (location, VANISHED),
        ).fetchall()
    missing, _unreadable = _missing_resource_ids(rows, unreadable_dirs)
    gone = set(missing)
    return _confirm_vanished([row for row in rows if int(row["id"]) in gone])


def _confirm_vanished(rows: Sequence) -> list:
    """逐条单独问一次「这个文件在不在」，只留还是答不在的。

    目录枚举在网盘挂载上会静默漏报。
    `scandir` 那一趟成功返回、不抛错，只是少给了几个名字，所以 `unreadable` 也数不到
    它。115 那边稳得多，506 条复核完仍有 475 条——真删掉的确实删了。

    单条 `stat` 是另一条路：问的是「这个名字在不在」，不必把整个目录列全。名单已经被
    枚举筛到几百条量级，这一趟的成本远小于它挡住的损失——照枚举的结果删，一次就能删掉
    几百行文件其实还在的资产。
    """
    if not rows:
        return []

    def present(row) -> bool:
        try:
            return translate_ledger_path(row["path"]).exists()
        except OSError:
            # 读不出来不等于不存在；答不上来的一律留着。
            return True

    with ThreadPoolExecutor(
        max_workers=min(RESOURCE_SCAN_WORKERS, len(rows)),
        thread_name_prefix="PeachVanishedCheck",
    ) as executor:
        verdicts = list(executor.map(present, rows))
    return [row for row, here in zip(rows, verdicts) if not here]


def _source_roots(location: str) -> list[Path]:
    return [translate_ledger_path(root) for root in LOCATION_ROOT_DECLARATIONS.get(location, ())]


def _hollow_directory(path: Path) -> bool:
    """CloudDrive 上的空目录：`scandir` 报找不到，`listdir` 却说目录在、里面是空的。

    挂载层给空目录列不出 `.` 与 `..`，`FindFirstFileW` 于是回 `ERROR_FILE_NOT_FOUND`；
    `os.listdir` 把它当成空目录，`os.scandir`（`os.walk` 走的就是它）当成错误。2026-09-27
    """
    try:
        return path.is_dir() and not path.is_symlink() and not os.listdir(path)
    except OSError:
        return False


def _empty_directories(roots: Sequence[Path], unreadable_dirs: set[str]) -> list[Path]:
    """自底向上找出来源根下的空目录；删掉子目录后会变空的父目录也算，来源根本身不算。

    判空只用 `os.walk` 已经列出来的名字，不再逐个目录多列一次：网盘挂载上每列一次
    就是一次网络往返。目录链接不跟进，也不算空。读不了的目录记进 `unreadable_dirs`。
    """
    empty: set[str] = set()
    found: list[Path] = []
    for root in roots:
        # 自底向上时子目录的 onerror 先于父目录产出，空目录在这里记下，父目录照常判空。
        def on_walk_error(error: OSError, root: Path = root) -> None:
            path = Path(error.filename) if isinstance(error, FileNotFoundError) and error.filename else None
            if path is not None and path != root and _hollow_directory(path):
                empty.add(_directory_key(path))
                found.append(path)
            else:
                unreadable_dirs.add(_directory_key(error.filename or root))

        for directory, subdirectories, files in os.walk(
                root, topdown=False, onerror=on_walk_error, followlinks=False):
            candidate = Path(directory)
            if files or candidate == root or candidate.is_symlink():
                continue
            if all(_directory_key(os.path.join(directory, name)) in empty for name in subdirectories):
                empty.add(_directory_key(candidate))
                found.append(candidate)
    return found


def _keeps_records(contract: ResourceSyncContract, rows: Sequence) -> set[int]:
    """文件已不在盘上的这些行里，要标「已消失」而不删的：在库、且带个人记录。

    回收站里的行是用户自己丢的，带不带记录都照旧永久删除（ADR-0087 第四条）。
    """
    in_library = [int(row["id"]) for row in rows if row["disposal"] is None]
    if not in_library:
        return set()
    with contract.read_connection() as connection:
        return record_holders(connection, in_library)


def _scan_sources(
    contract: ResourceSyncContract,
    progress: Callable[[dict], None] | None = None,
    *, resume_state=None, checkpoint=None,
) -> dict:
    """只读核对每个在线来源：文件已不在盘上的行（分删除与标已消失两档）、空文件夹、读不了的目录。"""
    with contract.read_connection() as connection:
        totals = {row[0]: int(row[1]) for row in connection.execute(
            "SELECT location,count(*) FROM asset WHERE path IS NOT NULL "
            "AND COALESCE(disposal,'')<>? GROUP BY location", (VANISHED,))}
    previous = (resume_state or {}).get('scan_checkpoint') or {}
    parts = dict(previous.get('parts') or {})
    sources = [part['source'] for part in parts.values()]
    completed = {row['location'] for row in sources if row['online'] and not row['unreadable']}
    for location in configured_resource_locations():
        if location in completed:
            continue
        online = source_is_online(location)
        source = {"location": location, "online": online, "total": totals.get(location, 0),
                  "missing": 0, "vanish": 0, "empty": 0, "unreadable": 0}
        part = dict(source=source, vanished_ids=[], keep_ids=[], empty_dirs=[])
        if online:
            unreadable: set[str] = set()
            gone = vanished_asset_rows(contract, location, unreadable_dirs=unreadable)
            empties = _empty_directories(_source_roots(location), unreadable)
            part['vanished_ids'] = [int(row['id']) for row in gone]
            keep = _keeps_records(contract, gone)
            part['keep_ids'] = sorted(keep)
            part['empty_dirs'] = [os.fspath(path) for path in empties]
            source.update(missing=len(gone), vanish=len(keep), empty=len(empties),
                          unreadable=len(unreadable))
        sources = [row for row in sources if row['location'] != location] + [source]
        parts[location] = part
        if progress is not None:
            progress(source)
        if checkpoint and not checkpoint(scan_checkpoint={'parts': parts}, sources=sources):
            raise InterruptedError('资源检查已停止')
    sources = [part['source'] for part in parts.values()]
    vanished_ids = sorted({value for part in parts.values() for value in part['vanished_ids']})
    keep_ids = {value for part in parts.values() for value in part['keep_ids']}
    empty_dirs = {location: part['empty_dirs'] for location, part in parts.items() if part['source']['online']}
    return {"sources": sources, "vanished_ids": vanished_ids, "keep_ids": keep_ids,
            "empty_dirs": empty_dirs}


def _cache_file(path: Path, kind: str, output: list[tuple[str, Path, int]]) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    output.append((kind, path, size))


def _managed_cache_root(contract: ResourceSyncContract, root: Path | None) -> bool:
    """Only let a ledger clean caches inside its own runtime data directory.

    Production stores ``ledger.db`` under ``peach-data/database`` and generated files under
    the sibling ``peach-data/generated`` directory. Isolated tests commonly put the database
    directly in a temporary root. In both shapes the database identifies the only directory
    tree cleanup may enter. This prevents a temporary database paired with a forgotten default
    cache root from treating the real generated files as orphans.
    """
    if root is None:
        return False
    database_parent = Path(contract.db_path).resolve().parent
    data_root = (database_parent.parent if database_parent.name.casefold() == "database"
                 else database_parent)
    resolved = Path(root).resolve()
    return resolved != data_root and resolved.is_relative_to(data_root)


def _cache_owners(
    contract: ResourceSyncContract, excluded_ids: Sequence[int] = (),
    retired_ids: Sequence[int] = (),
) -> tuple[set[int], set[str], set[Path]]:
    """缓存还有主人的三把键：按 id 认的、按番号认的、快照路径。

    `excluded_ids` 是这一轮要永久删除的行，它们的缓存全都不算有主。`retired_ids` 是这一轮
    要标「已消失」的行，已经标过的也一样：快照与封面留给孤儿记录列表认片（ADR-0087），
    按 id 生成的转码、分片、时间轴与海报帧对一个读不到的文件没有用处，照孤儿清掉。
    """
    with contract.read_connection() as connection:
        rows = connection.execute(
            "SELECT id,code,snapshot_path,disposal FROM asset WHERE COALESCE(disposal,'')!='trash'",
        ).fetchall()
    excluded = {int(item) for item in excluded_ids}
    retired = {int(item) for item in retired_ids}
    rows = [row for row in rows if int(row["id"]) not in excluded]
    active_ids = {int(row["id"]) for row in rows
                  if row["disposal"] is None and int(row["id"]) not in retired}
    active_codes = {normalise_code_key(row["code"]) for row in rows if row["code"]}
    active_codes.discard("")
    translate = contract.snapshot_root is not None
    active_snapshots = {normalized_path(row["snapshot_path"]) if translate else Path(row["snapshot_path"])
                        for row in rows if row["snapshot_path"]}
    return active_ids, active_codes, active_snapshots


def _resource_orphan_plan(
    contract: ResourceSyncContract, excluded_ids: Sequence[int] = (),
    retired_ids: Sequence[int] = (),
) -> dict:
    """Find only reproducible generated files that no active asset still owns.

    Review CSVs, provider evidence, logos and entity portraits are deliberately outside this
    boundary: they are provenance or shared identity assets, not disposable per-asset caches.
    """
    if not contract.resource_cleanup_enabled:
        return {"files": [], "dirs": set(), "summary": {},
                "total_files": 0, "total_bytes": 0}
    active_ids, active_codes, active_snapshots = _cache_owners(contract, excluded_ids, retired_ids)

    files: list[tuple[str, Path, int]] = []
    cleanup_dirs: set[Path] = set()
    if (_managed_cache_root(contract, contract.snapshot_root)
            and contract.snapshot_root.is_dir()):
        for path in contract.snapshot_root.rglob("*"):
            if path.is_file() and path not in active_snapshots:
                _cache_file(path, "snapshots", files)

    patterns = (
        (contract.poster_root, "posters", re.compile(r"^(\d+)_\d+\.jpg$")),
        (contract.photo_root, "photo-thumbs", re.compile(r"^(\d+)\.jpg$")),
        # 重建的 MP4 头（`.mp4hdr`）和整片转码缓存住在一起，判据和回收也共用一套。
        (contract.transcode_root, "transcodes", re.compile(r"^(\d+)-.+\.(?:mp4|mp4hdr)$")),
    )
    for root, kind, pattern in patterns:
        if not _managed_cache_root(contract, root) or not root.is_dir():
            continue
        for path in root.iterdir():
            match = pattern.match(path.name)
            if match and int(match.group(1)) not in active_ids and path.is_file():
                _cache_file(path, kind, files)

    if (_managed_cache_root(contract, contract.stream_root)
            and contract.stream_root.is_dir()):
        for directory in contract.stream_root.iterdir():
            if not directory.is_dir() or not directory.name.isdigit():
                continue
            if int(directory.name) in active_ids:
                continue
            cleanup_dirs.add(directory)
            for path in directory.rglob("*"):
                if path.is_file():
                    _cache_file(path, "stream-segments", files)

    # 时间轴预览按 `<id 末两位>/<id>/` 分桶，一部片子一个目录、里面几十张图。判据和
    # 分片一样是目录名，只是外面多一层桶：一部三小时的片子按 10 秒一帧就是 1080 张，
    # 片子没了而这些还留着，是本机产物里单部占得最多的一类。
    if (_managed_cache_root(contract, contract.timeline_root)
            and contract.timeline_root.is_dir()):
        for bucket in contract.timeline_root.iterdir():
            if not bucket.is_dir():
                continue
            for directory in bucket.iterdir():
                if not directory.is_dir() or not directory.name.isdigit():
                    continue
                if int(directory.name) in active_ids:
                    continue
                cleanup_dirs.add(directory)
                for path in directory.rglob("*"):
                    if path.is_file():
                        _cache_file(path, "timeline", files)

    if (_managed_cache_root(contract, contract.avatar_root)
            and contract.avatar_root.is_dir()):
        for path in contract.avatar_root.iterdir():
            match = re.match(r"^(\d+)\.jpg$", path.name)
            if match and int(match.group(1)) not in active_ids and path.is_file():
                _cache_file(path, "asset-avatars", files)

    if (_managed_cache_root(contract, contract.cover_root)
            and contract.cover_root.is_dir()):
        for path in contract.cover_root.iterdir():
            key = ""
            # 边车跟着它描述的那张封面一起走，否则封面被清掉之后目录里会留下一批
            # 指向不存在的图的框和脸。
            for suffix in (".face.json", jav_poster_crop.SIDECAR_SUFFIX):
                if path.name.endswith(suffix):
                    key = path.name[:-len(suffix)]
                    break
            if not key and path.suffix.lower() == ".jpg":
                key = path.stem
            if key and normalise_code_key(key) not in active_codes and path.is_file():
                _cache_file(path, "covers", files)

    summary: dict[str, dict[str, int]] = {}
    for kind, _path, size in files:
        bucket = summary.setdefault(kind, {"files": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["bytes"] += size
    return {
        "files": files, "dirs": cleanup_dirs, "summary": summary,
        "total_files": len(files), "total_bytes": sum(item[2] for item in files),
    }


def clean_resource_orphans(contract: ResourceSyncContract, *, progress=None, checkpoint=None, resume_state=None) -> dict:
    if progress:
        progress(checked=0, total=None, message="正在核对可重建缓存清单")
    plan = _resource_orphan_plan(contract)
    previous = (resume_state or {}).get('cache_checkpoint') or {}
    removed = previous.get('cache_removed', 0)
    reclaimed = previous.get('bytes_reclaimed', 0)
    blocked = list(previous.get('cache_blocked') or [])
    for index, (kind, path, size) in enumerate(plan["files"]):
        if progress:
            progress(checked=index, total=len(plan["files"]), message=f"清理缓存：已处理 {index} / {len(plan['files'])} 个")
        try:
            path.unlink(missing_ok=True)
            removed += 1
            reclaimed += size
        except OSError as error:
            blocked.append({"kind": kind, "name": path.name,
                            "reason": error.strerror or str(error)})
        if checkpoint and not checkpoint(cache_checkpoint=dict(cache_removed=removed, bytes_reclaimed=reclaimed,
                                                                cache_blocked=blocked)):
            raise InterruptedError('缓存清理已停止')
    if progress:
        progress(checked=0, total=None, message="正在整理缓存目录并汇总释放空间")
    for directory in sorted(plan["dirs"], key=lambda item: len(item.parts), reverse=True):
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            pass
        except OSError:
            # Individual file failures above already carry useful detail; a non-empty cache
            # directory is harmless and will be reconsidered on the next scan.
            pass
    return {"cache_removed": removed, "bytes_reclaimed": reclaimed,
            "cache_blocked": blocked}


def _resource_scan_public(state: dict) -> dict:
    if state["status"] == "complete":
        return {**state["result"], "status": "complete", "scan_id": state["scan_id"],
                "applied": bool(state.get("applied_at"))}
    return {
        "ok": state["status"] != "failed",
        "status": state["status"],
        "scan_id": state["scan_id"],
        "sources": [dict(source) for source in state["sources"]],
        "completed_sources": len(state["sources"]),
        "total_sources": len(configured_resource_locations()),
        **({"error": state["error"]} if state["status"] == "failed" else {}),
    }


def _run_resource_scan(contract: ResourceSyncContract, scan_id: str) -> None:
    """Scan every mounted source.  ``BackgroundJob`` turns a failure into a pollable state."""
    job = contract.resource_scan

    def progress(source: dict) -> None:
        with job.editing(scan_id) as state:
            if state is not None:
                state['sources'] = [row for row in state['sources'] if row['location'] != source['location']]
                state["sources"].append(dict(source))

    scan = _scan_sources(contract, progress, resume_state=job.snapshot(),
                         checkpoint=lambda **fields: job.checkpoint(scan_id, **fields))
    # 候选名单和空目录路径只留在任务状态里，不进 `result`：公开投影整个下发 `result`，
    # 物理路径不出服务端。
    job.update(scan_id, status="complete", result=_scan_result(contract, scan),
               vanished_ids=list(scan["vanished_ids"]), empty_dirs=scan["empty_dirs"],
               completed_at=time.time())


def _scan_result(contract: ResourceSyncContract, scan: dict) -> dict:
    keep = scan["keep_ids"]
    caches = _resource_orphan_plan(
        contract, [asset_id for asset_id in scan["vanished_ids"] if asset_id not in keep], keep)
    sources = scan["sources"]
    missing = len(scan["vanished_ids"])
    return {
        "ok": True, "sources": sources,
        "missing": missing,
        # `missing` 拆成两档：执行时永久删除的，和带个人记录、只标「已消失」的。
        "purge": missing - len(keep), "vanish": len(keep),
        "empty": sum(source["empty"] for source in sources),
        "unreadable": sum(source["unreadable"] for source in sources),
        "cache": {"files": caches["total_files"], "bytes": caches["total_bytes"],
                  "by_kind": caches["summary"]},
    }


def _background_resource_scan(contract: ResourceSyncContract, restart: bool = False) -> dict:
    return _resource_scan_public(contract.resource_scan.start(
        lambda scan_id: _run_resource_scan(contract, scan_id),
        initial={"sources": [], "result": None, "vanished_ids": [], "empty_dirs": {}},
        restart=restart,
        recovery={'restart': True},
    ))


def w_resource_sync_scan(contract: ResourceSyncContract, body=None):
    body = body or {}
    if body.get("background") is True:
        if body.get("status_only") is True:
            state = contract.resource_scan.snapshot()
            if state is None:
                return {
                    "ok": True, "status": "idle", "scan_id": "", "sources": [],
                    "completed_sources": 0,
                    "total_sources": len(configured_resource_locations()),
                }
            return _resource_scan_public(state)
        if not configured_resource_locations():
            raise ValueError("请先在配置页添加媒体目录")
        return _background_resource_scan(contract, restart=body.get("restart") is True)
    if not configured_resource_locations():
        raise ValueError("请先在配置页添加媒体目录")
    return _scan_result(contract, _scan_sources(contract))


def _completed_scan(contract: ResourceSyncContract, scan_id: str) -> dict:
    """执行只认这一轮检查：别的一轮、还没跑完的一轮、顶掉了的一轮、已经清过的一轮都不算。"""
    state = contract.resource_scan.snapshot()
    if (not scan_id or state is None or state["scan_id"] != scan_id
            or state["status"] != "complete" or state.get("applied_at")):
        raise ValueError("resource scan expired; scan again")
    return state


def _rows_by_id(contract: ResourceSyncContract, asset_ids: Sequence[int]) -> list:
    rows = []
    with contract.read_connection() as connection:
        for offset in range(0, len(asset_ids), 400):
            batch = list(asset_ids[offset:offset + 400])
            marks = ",".join("?" for _item in batch)
            rows.extend(connection.execute(
                "SELECT id,location,path,snapshot_path,disposal FROM asset "
                f"WHERE id IN ({marks}) AND path IS NOT NULL", batch,
            ).fetchall())
    return rows


def _recheck_vanished(contract: ResourceSyncContract, asset_ids: Sequence[int]) -> list:
    """检查给的候选逐条重新 `stat`，只留仍在线来源上、这一次仍答不在的行。

    不重跑目录枚举：枚举在网盘上会静默少给名字（`_confirm_vanished`），而这一步之后
    就是永久删除。复核前后各判一次在线：复核途中掉线的来源，那一趟 `stat` 全都答不在。
    """
    if not asset_ids:
        return []
    locations = configured_resource_locations()
    online = {location for location in locations if source_is_online(location)}
    rows = [row for row in _rows_by_id(contract, asset_ids) if row["location"] in online]
    confirmed = _confirm_vanished(rows)
    still_online = {location for location in online if source_is_online(location)}
    return [row for row in confirmed if row["location"] in still_online]


def _remove_empty_directories(paths: Sequence[Path], roots: Sequence[Path]) -> tuple[int, int]:
    """按深度从深到浅删检查报出的空目录；来源根和根外的路径一律不碰。

    `rmdir` 只删得掉空目录：检查之后又放进东西的目录照样留着，不算失败。
    """
    removed = errors = 0
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        if (path in roots or path.is_symlink()
                or not any(within_root(path, root) for root in roots)):
            continue
        try:
            path.rmdir()
        except FileNotFoundError:
            # CloudDrive 会在最后一个子项消失时自己收掉空的一层；删文件那一步也会顺手清空父目录。
            continue
        except OSError as error:
            if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                errors += 1
            continue
        removed += 1
    return removed, errors


def _mark_record_holders(contract: ResourceSyncContract,
                         rows: Sequence) -> tuple[list[int], list[int], list]:
    """复核过的失效行里，在库且带个人记录的标「已消失」，唯一对得上另一个版本的当场接回。

    返回标了的 id、其中接回了的 id 与剩下要删的行。有没有记录在执行这一刻重新问：检查
    之后用户可能刚给某一部点了喜欢。
    """
    in_library = [int(row["id"]) for row in rows if row["disposal"] is None]
    if not in_library:
        return [], [], list(rows)
    contract.cache_bust()
    with contract.write_transaction() as connection:
        marked = mark_vanished(connection, record_holders(connection, in_library), time.time())
        reattached = reattach_vanished(connection, marked)
    kept = set(marked)
    return marked, reattached, [row for row in rows if int(row["id"]) not in kept]


def _apply_missing_records(contract, state, report, checkpoint, resume_state):
    """逐批复核失效记录，保存完成集合与累计回执。"""
    saved = (resume_state or {}).get('apply_checkpoint') or {}
    processed = set(saved.get('processed') or [])
    report(checked=len(processed), total=len(state['vanished_ids']), message="正在逐条复核失效记录")
    marked, reattached = list(saved.get('marked') or []), list(saved.get('reattached') or [])
    purge = dict(saved.get('purge') or {'purged': 0, 'blocked': [], 'empty_dirs_removed': 0})
    pending = [value for value in state['vanished_ids'] if value not in processed]
    for offset in range(0, len(pending), 100):
        selected = pending[offset:offset + 100]
        added, attached, confirmed = _mark_record_holders(contract, _recheck_vanished(contract, selected))
        marked.extend(added)
        reattached.extend(attached)
        if confirmed:
            from .web_batch import purge_vanished_rows
            result = purge_vanished_rows(contract, confirmed)
            for field in ('purged', 'empty_dirs_removed'):
                purge[field] += int(result.get(field) or 0)
            purge['blocked'].extend(result.get('blocked') or [])
        processed.update(selected)
        report(checked=len(processed), total=len(state['vanished_ids']), message='正在处理失效记录')
        if checkpoint and not checkpoint(apply_checkpoint=dict(processed=sorted(processed), marked=marked,
                                                                reattached=reattached, purge=purge)):
            raise InterruptedError('资源清理已停止')
    return marked, reattached, purge


def w_resource_sync_apply(contract: ResourceSyncContract, body, *, progress=None, checkpoint=None, resume_state=None):
    """处理这一轮检查报出的失效记录与空文件夹，再清孤儿缓存。

    文件已不在盘上的行不进回收站：在库且带个人记录的标「已消失」，记录留着等接回
    （ADR-0087），库里唯一对得上另一个版本的当场接过去、报在 `reattached`；其余的连回收站
    里的一起直接删（ADR-0080）。删之前逐条复核，删不掉的进
    `blocked`；删除这一档不可撤销，所以只认检查给的候选集合。
    """
    if not configured_resource_locations():
        raise ValueError("请先在配置页添加媒体目录")
    if body.get("confirm") is not True:
        raise ValueError("resource sync requires confirmation")
    scan_id = str(body.get("scan_id") or "")
    from .job_recovery import current_job_id, read
    recovering = (read(contract.db_path, 'resource-apply').get('checkpoint') or {}) if current_job_id.get() else {}
    if not recovering.get('apply_receipt'):
        _completed_scan(contract, scan_id)
    if body.get("background"):
        job = contract.resource_apply_job
        def work(job_id):
            result = w_resource_sync_apply(contract, {**body, "background": False},
                progress=lambda **fields: job.update(job_id, **fields),
                checkpoint=lambda **fields: job.checkpoint(job_id, **fields), resume_state=job.snapshot())
            job.update(job_id, **result, status="complete", completed_at=time.time())
        return job.start(work, restart=True, initial={"message": "正在逐条复核失效记录"}, recovery=body)
    if (resume_state or {}).get('apply_receipt'):
        return resume_state['apply_receipt']
    state = _completed_scan(contract, scan_id)
    report = progress or (lambda **_fields: None)
    marked, reattached, purge = _apply_missing_records(
        contract, state, report, checkpoint, resume_state)
    report(checked=0, total=None, message="正在删除空文件夹")
    dirs_removed, dir_errors = 0, 0
    for location, paths in state["empty_dirs"].items():
        if location not in configured_resource_locations() or not source_is_online(location):
            continue
        removed, errors = _remove_empty_directories([Path(path) for path in paths],
                                                    _source_roots(location))
        dirs_removed += removed
        dir_errors += errors
    contract.cache_bust()
    cleanup = clean_resource_orphans(contract, progress=progress, checkpoint=checkpoint,
                                    resume_state=resume_state) if body.get("clean_cache", True) else {
        "cache_removed": 0, "bytes_reclaimed": 0, "cache_blocked": [],
    }
    blocked = [{"id": item["id"], "name": PureWindowsPath(item["path"]).name,
                "reason": item["reason"]} for item in purge["blocked"]]
    # 这一轮的清单已经照着清过：读数不再是盘上的现状，结果区换成清理回执。
    receipt = {
        "ok": True, "sources": state["result"]["sources"],
        "purged": int(purge["purged"]), "vanished": len(marked) - len(reattached),
        "reattached": len(reattached),
        "blocked": blocked, "blocked_count": len(blocked),
        "dirs_removed": dirs_removed + int(purge.get("empty_dirs_removed") or 0),
        "dir_errors": dir_errors, "unreadable": int(state["result"].get("unreadable") or 0),
        **cleanup,
    }
    if checkpoint:
        checkpoint(apply_receipt=receipt)
    contract.resource_scan.update(scan_id, applied_at=time.time())
    return receipt


def w_purge_missing(contract: ResourceSyncContract, body):
    """按目录对账：文件已经在磁盘上删掉的在库行，带个人记录的标「已消失」，其余移入回收站。

    这条路径服务的是「我在资源管理器里整理网盘目录」——删掉的就是不要的，所以
    不进复核；两档都撤得回来：回执里的 8 秒撤销走 `/api/batch` 的 `restore`，
    它对回收站与已消失的行都清掉 `disposal`。已经在回收站或已消失的行不再碰，
    撤销因此只还原这一趟改的行。标了已消失、库里唯一对得上另一个版本的，记录当场接过去，
    `items` 里那一条标 `reattached`，记的是 `auto:vanished-reattach` 批次。

    真正危险的不是删得太干净，而是把「盘没挂上」误判成「文件没了」：R: 掉线时
    整条来源 2,552 行都会看起来像被删。所以先做来源级在线判定，整源不在线就
    一行都不碰。CloudDrive 掉线后挂载点目录仍然存在，`root_online` 因此判的是
    「能否列出一个条目」而不是「目录在不在」。

    判缺失和全量扫描共用 `_missing_resource_ids`：整个目录一次列举出结果。逐条
    `is_file()` 在云挂载上每条都是一次元数据往返——已删除的路径尤其贵，CloudDrive
    对「不存在」没有负缓存；目录暂时读不了时保留账本行，不当成已删。
    """
    asset_id = int(body["id"])
    with contract.read_connection() as connection:
        anchor = connection.execute(
            "SELECT id,location,path,name FROM asset WHERE id=?", (asset_id,),
        ).fetchone()
        if not anchor:
            return {"error": "not found"}
        location, path, name = anchor["location"], anchor["path"], anchor["name"]
        if not path or not name:
            return {"error": "asset has no path"}
        if not source_is_online(location):
            # 不是失败，是拒绝：盘不在时无法区分「文件删了」和「盘没挂上」。
            return {"ok": False, "error": "source offline", "location": location}
        directory = path[: len(path) - len(name) - 1]
        rows = connection.execute(
            f"SELECT id,path,name FROM asset WHERE location=? "
            f"AND {dir_expr('')}=? AND disposal IS NULL",
            (location, directory),
        ).fetchall()

    missing_ids, unreadable = _missing_resource_ids(rows)
    gone = set(missing_ids)
    missing = [
        {"id": row["id"], "name": row["name"]}
        for row in rows
        if int(row["id"]) in gone
    ]
    if not missing:
        return {"ok": True, "directory": photo_set_title(directory),
                "checked": len(rows), "removed": 0, "trashed": 0, "vanished": 0,
                "reattached": 0, "unreadable": unreadable, "items": []}

    contract.cache_bust()
    ids = [item["id"] for item in missing]
    with contract.write_transaction() as connection:
        stamp = time.time()
        kept = set(mark_vanished(connection, record_holders(connection, ids), stamp))
        connection.executemany(
            "UPDATE asset SET disposal='trash',disposal_reason='missing',feedback_at=? WHERE id=? AND disposal IS NULL",
            [(stamp, asset_id) for asset_id in ids if asset_id not in kept],
        )
        reattached = set(reattach_vanished(connection, kept))
    contract.cache_bust()
    for item in missing:
        # 接回了的那一行已经删掉、记录在另一个版本上，撤销不还原它（`restore` 认不到这一行）；
        # 要撤回这一次接回，按批次号走 `revert_auto_landing.py`。
        item["disposal"] = (REATTACHED if item["id"] in reattached
                            else VANISHED if item["id"] in kept else "trash")
    return {
        "ok": True, "directory": photo_set_title(directory),
        "checked": len(rows), "removed": len(missing),
        "trashed": len(missing) - len(kept), "vanished": len(kept) - len(reattached),
        "reattached": len(reattached), "unreadable": unreadable, "items": missing,
    }
