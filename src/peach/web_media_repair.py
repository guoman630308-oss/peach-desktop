"""批量修一个媒体库里播不了或播不顺的 MP4。两类毛病，一个入口：

- **缺 `ctts`**：有 B 帧却没有时间戳表，浏览器按容器时间戳丢帧，只能退回 HLS 实时转码。
  `mp4repair` 重建一份头存成边车，原文件一个字节不动。
- **缺 `moov`**：下载或录制没写完，整个索引都没有，在哪都打不开。`mp4recover` 借同来源
  的完好片子重建，修好的文件换回原路径，坏原件改名留在同目录。

两段走，因为两段的瓶颈不是一回事：

1. **筛**：探不出时长的片子先看顶层有没有 `moov`，其余读头判断缺不缺 `ctts`。一次判定
   只读头，但 115 上就是一次网络往返。判完的结论按「资产 + 大小 + 改动时间」落盘，下次
   再点就不必把整个库重读一遍。
2. **修**：两类都要把整片读一遍，CPU 和读盘都吃满，所以串行做，并且按播放次数排序——
   常看的先修好，中途停下也已经换来了体感。

一轮只修一个库，由用户点名。选中计费来源（PikPak）上的库就是同意走流量：一部片要完整
拉一遍才修得了。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path, PureWindowsPath

from . import media_libraries, media_probe, mp4recover, settings_file
from .mp4repair import RepairUnavailable
from .platform import translate_ledger_path

#: 判定结论的落盘位置，放在边车自己的目录里：它们同生同灭，清缓存时一起走。
SCAN_CACHE_NAME = "mp4-repair-scan.json"

#: 文件身份对应的判定缓存；修复产物由边车或新文件核验。
CLEAN = "clean"
UNAVAILABLE = "unavailable"

#: 缺时间戳表，修的是边车。
HEADER = "header"
#: 缺整个索引，修的是文件本身。
INDEX = "index"

_MP4 = "(lower({0}path) LIKE '%.mp4' OR lower({0}path) LIKE '%.m4v')"


def _cache_path(contract) -> Path:
    return Path(contract.transcode_root) / SCAN_CACHE_NAME


def _load_cache(contract) -> dict[str, str]:
    try:
        payload = json.loads(_cache_path(contract).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return {str(key): str(value) for key, value in entries.items()} if isinstance(entries, dict) else {}


def _save_cache(contract, entries: dict[str, str]) -> None:
    path = _cache_path(contract)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # 结论缓存丢了只是下次重扫一遍，不该让整轮任务失败。
        pass


def _key(asset_id: int, source: Path) -> str:
    """和边车同一套身份：文件换了内容，旧结论就不再作数。"""
    stat = source.stat()
    return f"{asset_id}-{stat.st_size}-{stat.st_mtime_ns}"


def _library_filter(library: str) -> tuple[str, list]:
    condition, parameters = media_libraries.predicate(settings_file.active(), library)
    if condition == "0":
        raise ValueError("没有这个媒体库")
    return condition, parameters


def _candidates(contract, scope: tuple[str, list]) -> list[tuple[int, str, float | None]]:
    """这个库里所有还在的 MP4，常看的排前面。"""
    condition, parameters = scope
    with contract.read_connection() as connection:
        rows = connection.execute(
            "SELECT a.id,a.path,a.duration FROM asset a "
            "WHERE a.path IS NOT NULL AND a.disposal IS NULL "
            f"AND {_MP4.format('a.')} AND {condition} "
            "ORDER BY a.play_count DESC, COALESCE(a.last_played,0) DESC, a.id",
            parameters).fetchall()
    return [(row["id"], row["path"], row["duration"]) for row in rows]


def _diagnose(contract, source: Path, duration: float | None) -> str | None:
    """这部片缺的是什么；什么都不缺回 None。"""
    if (duration is None or duration < 0) and mp4recover.missing_index(source):
        return INDEX
    return HEADER if contract.transcode_service.decode_order_timestamps(source) else None


def _survey(contract, job, job_id: str, rows, cache: dict[str, str]) -> list[tuple[int, str, str]]:
    """筛出需要修的那些。结论边判边进缓存，中途停下也不白读。"""
    store = contract.header_repairs
    found: list[tuple[int, str, str]] = []
    for index, (asset_id, raw, duration) in enumerate(rows, start=1):
        if job.snapshot() is None:
            break
        source = translate_ledger_path(raw)
        try:
            key = _key(asset_id, source)
        except OSError:
            continue
        if store.lookup(asset_id, source) is not None or cache.get(key) in (CLEAN, UNAVAILABLE):
            _advance(job, job_id, index, skipped=True)
            continue
        kind = cache.get(key)
        if kind not in (HEADER, INDEX):
            try:
                kind = _diagnose(contract, source, duration)
            except OSError:
                continue
        if kind:
            found.append((asset_id, raw, kind))
        cache[key] = kind or CLEAN
        _save_cache(contract, cache)
        if not job.checkpoint(job_id, survey_checked=index, survey_cache=dict(cache)):
            raise InterruptedError('媒体检查已停止')
        _advance(job, job_id, index, skipped=False)
    return found


def _advance(job, job_id: str, checked: int, *, skipped: bool) -> None:
    with job.editing(job_id) as state:
        if state is None:
            return
        state["checked"] = max(state.get('checked', 0), checked)
        if skipped and checked > state.get('survey_checked', 0):
            state["skipped"] = state.get("skipped", 0) + 1
        state['survey_checked'] = max(state.get('survey_checked', 0), checked)
        saved = {key: state.get(key, 0) for key in ('checked', 'survey_checked', 'skipped')}
    if not job.checkpoint(job_id, **saved):
        raise InterruptedError('媒体检查已停止')


def _reference_candidates(contract, asset_id: int, raw: str) -> list[str]:
    """能当参照的：同一来源、上一级目录之下、探得出时长的 MP4。"""
    parent = PureWindowsPath(raw).parent
    scope = parent.parent if parent.parent != parent else parent
    prefix = str(scope).rstrip("\\") + "\\"
    with contract.read_connection() as connection:
        rows = connection.execute(
            "SELECT path FROM asset WHERE location=(SELECT location FROM asset WHERE id=?) "
            f"AND duration>0 AND disposal IS NULL AND {_MP4.format('')} "
            "AND substr(path,1,?)=? COLLATE NOCASE",
            (asset_id, len(prefix), prefix)).fetchall()
    return [row["path"] for row in rows]


def _record_replacement(contract, asset_id: int, source: Path, ffprobe: Path) -> None:
    """文件换了，账本跟着换：重新探时长与画面，大小和时间照扫描的口径刷新。

    115 的 SHA1 是按旧内容算的，留着会让查重把它和别的文件认成同一份，所以清掉等下次同步。
    """
    stat = source.stat()
    measured = media_probe.measure(str(ffprobe), asset_id, str(source))
    with contract.write_transaction() as connection:
        connection.execute(media_probe.UPDATE, measured)
        connection.execute(
            "UPDATE asset SET size=?,mtime=?,hash=NULL,hash_kind=NULL WHERE id=?",
            (stat.st_size, time.strftime("%Y-%m-%d", time.localtime(stat.st_mtime)), asset_id))
    contract.cache_bust()


def _recover(contract, asset_id: int, raw: str, tools: tuple[Path, Path, Path]) -> bool:
    with contract.read_connection() as connection:
        row = connection.execute("SELECT location FROM asset WHERE id=?", (asset_id,)).fetchone()
    if row is None:
        return False
    untrunc, ffmpeg, ffprobe = tools
    source = translate_ledger_path(raw)
    references = [translate_ledger_path(path) for path in
                  mp4recover.pick_references(raw, _reference_candidates(contract, asset_id, raw))]
    work = Path(contract.transcode_root) / "recover" / str(asset_id)
    try:
        recovery = mp4recover.recover(source, references, untrunc=untrunc, ffmpeg=ffmpeg,
                                      ffprobe=ffprobe, work=work)
        mp4recover.replace_original(source, recovery.path)
    except (RepairUnavailable, OSError, subprocess.SubprocessError):
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _record_replacement(contract, asset_id, source, ffprobe)
    return True


def _recovery_tools(contract) -> tuple[Path, Path, Path] | None:
    untrunc = mp4recover.untrunc_path(contract.tools_root)
    resolver = contract.header_repairs.resolver
    ffmpeg, ffprobe = resolver.ffmpeg(), resolver.ffprobe()
    if untrunc is None or ffmpeg is None or ffprobe is None:
        return None
    return untrunc, ffmpeg.path, ffprobe.path


def _repair(contract, job, job_id: str, found, cache: dict[str, str]) -> None:
    """逐个修。一部片要整片读一遍，所以这里刻意不并发。"""
    store = contract.header_repairs
    tools = _recovery_tools(contract) if any(kind == INDEX for *_, kind in found) else None
    previous = job.snapshot() or {}
    job.update(job_id, stage="修复", checked=previous.get('repaired', 0),
               total=len(found) + previous.get('repaired', 0))
    for index, (asset_id, raw, kind) in enumerate(found, start=1):
        if job.snapshot() is None:
            return
        source = translate_ledger_path(raw)
        if kind == HEADER:
            made = store.repair_now(asset_id, source)
        elif tools is not None:
            made = _recover(contract, asset_id, raw, tools)
        else:
            # 缺的是工具不是片子：装上 untrunc 之后这部还该再试，所以不记结论。
            made = None
        if made is False:
            try:
                cache[_key(asset_id, source)] = UNAVAILABLE
            except OSError:
                pass
        with job.editing(job_id) as state:
            if state is None:
                return
            state["checked"] = previous.get('repaired', 0) + index
            state["repaired"] = state.get("repaired", 0) + (1 if made else 0)
            state["failed"] = state.get("failed", 0) + (0 if made else 1)
            state["missing_tool"] = state.get("missing_tool", 0) + (1 if made is None else 0)
            state["message"] = source.name[:80]
        _save_cache(contract, cache)
        state = job.snapshot() or {}
        if not job.checkpoint(job_id, **{key: state.get(key, 0) for key in
                              ('checked', 'total', 'repaired', 'failed', 'missing_tool')}):
            raise InterruptedError('媒体修复已停止')


def run_media_repair(contract, job_id: str, scope: tuple[str, list]) -> None:
    """一轮完整的批量修复。异常由 `BackgroundJob` 变成可轮询的失败状态。"""
    job = contract.media_repair_job
    cache = _load_cache(contract)
    cache.update((job.snapshot() or {}).get('survey_cache') or {})
    rows = _candidates(contract, scope)
    previous = job.snapshot() or {}
    job.update(job_id, stage="扫描", total=len(rows), checked=previous.get('survey_checked', 0))
    found = _survey(contract, job, job_id, rows, cache)
    _save_cache(contract, cache)
    if job.snapshot() is None:
        return
    job.update(job_id, found=len(found))
    _repair(contract, job, job_id, found, cache)
    _save_cache(contract, cache)
    job.update(job_id, stage="完成", status="complete", completed_at=time.time())


def _public(state: dict | None) -> dict:
    if state is None:
        return {"ok": True, "status": "idle", "job_id": "", "library": "", "stage": "",
                "checked": 0, "total": 0, "found": 0, "repaired": 0, "failed": 0, "missing_tool": 0}
    return {
        "ok": state["status"] != "failed",
        "status": state["status"],
        "job_id": state["job_id"],
        "library": state.get("library", ""),
        "stage": state.get("stage", ""),
        "checked": int(state.get("checked", 0)),
        "total": int(state.get("total", 0)),
        "found": int(state.get("found", 0)),
        "repaired": int(state.get("repaired", 0)),
        "failed": int(state.get("failed", 0)),
        "missing_tool": int(state.get("missing_tool", 0)),
        "skipped": int(state.get("skipped", 0)),
        "message": state.get("message", ""),
        **({"error": state["error"]} if state["status"] == "failed" else {}),
    }


def q_media_repair(contract, args=None):
    return _public(contract.media_repair_job.snapshot())


def w_media_repair(contract, body=None):
    """启动一轮，或按 `stop` 停下正在跑的那一轮。"""
    body = body or {}
    job = contract.media_repair_job
    if body.get("stop") is True:
        job.stop(resume=False)
        return _public(None)
    if body.get("status_only") is True:
        return _public(job.snapshot())
    if contract.header_repairs is None or contract.transcode_service is None:
        raise ValueError("这个服务实例没有接上转码与修复组件")
    library = str(body.get("library") or "")
    if not library:
        raise ValueError("先选一个媒体库")
    scope = _library_filter(library)
    return _public(job.start(
        lambda job_id: run_media_repair(contract, job_id, scope),
        initial={"library": library, "stage": "扫描", "total": 0, "checked": 0, "found": 0,
                 "repaired": 0, "failed": 0, "missing_tool": 0, "skipped": 0, "message": ""},
        restart=body.get("restart") is True,
        recovery={'library': library, 'restart': True},
    ))
