"""视频的时长、分辨率与编码：ffprobe 读一次文件头，写回 `asset`。

入库时顺手探一次，新文件一登记就带着时长和分辨率：全量扫描扫完一个根就探这个根下
还没探过的视频，推送发现登记一个视频就探这一个。`scripts/probe.py` 只剩补历史空缺、
重探失败和按 id 点名三件事。

计费来源（PikPak）入库时一律不探。入库是自动发生的，没有人在场确认流量预算；要探就
显式跑那个脚本并加 `--allow-metered`。

拿不到时长写 -1，不写 0，也不留 NULL。0 会同时躲过 `duration IS NULL` 的待办和抽帧的
`duration>2` 门槛，永久卡住；留 NULL 的话，每一轮入库都会把同一个坏文件再探一遍。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing
from pathlib import PureWindowsPath

from .jobs import ACTIVE_ASSET_SQL
from .media import resolve_case_insensitive
from .media_sources import cost_for
from .strm import skip_background_read
from .processing_control import CURRENT, checkpoint

#: 一次探测最多等多久。ffprobe 只读文件头，网盘上实测一两秒。
TIMEOUT_SECONDS = 20.0
#: 入库时同时探几个。网盘挂载上这是延迟受限的元数据读取，开多了只会把 CloudDrive 压住。
WORKERS = 4

UPDATE = (
    "UPDATE asset SET duration=?,width=?,height=?,vcodec=?,fps=?,has_audio=?,"
    "ctx_length=?,ctx_orient=?,ctx_quality=? WHERE id=?"
)
_UNMEASURED = f"medium='video' AND duration IS NULL AND location=? AND {ACTIVE_ASSET_SQL}"


def context_fields(width: int, height: int, duration: float) -> tuple[str | None, str | None, str | None]:
    orientation = quality = length = None
    if width and height:
        orientation = "竖屏" if height > width else "横屏"
        longest = max(width, height)
        quality = (
            "4K" if longest >= 3000 else "2K" if longest >= 1900
            else "1080P" if longest >= 1300 else "720P" if longest >= 900
            else "低画质"
        )
    if duration and duration > 0:
        length = "速食" if duration < 300 else "短" if duration < 900 else "中" if duration < 2400 else "长"
    return length, orientation, quality


def probe_file(ffprobe: str, path: str, timeout: float = TIMEOUT_SECONDS) -> tuple:
    if skip_background_read(path):
        raise ValueError('STRM 和 ISO 不执行视频探测')
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-rw_timeout", "8000000",
            "-select_streams", "v:0", "-show_entries",
            "format=duration:stream=width,height,codec_name,avg_frame_rate",
            "-of", "json", path,
        ],
        capture_output=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, 'ffprobe', stderr=result.stderr)
    payload = json.loads(result.stdout or b"{}")
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        duration = -1.0
    stream = (payload.get("streams") or [{}])[0]
    fps = 0.0
    try:
        numerator, denominator = (stream.get("avg_frame_rate") or "0/1").split("/")
        fps = float(numerator) / float(denominator) if float(denominator) else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return (
        duration,
        int(stream.get("width") or 0),
        int(stream.get("height") or 0),
        stream.get("codec_name"),
        fps,
        None,
    )


def measure(ffprobe: str, asset_id: int, path: str, timeout: float = TIMEOUT_SECONDS) -> tuple:
    """这一行的 `UPDATE` 参数。探不出来的一律是 -1 那一行，不抛。"""
    try:
        duration, width, height, codec, fps, audio = probe_file(
            ffprobe, resolve_case_insensitive(path), timeout)
    except Exception as error:  # 超时、文件读不到、输出不是 JSON：对这一行都是「没拿到」
        from .error_reporting import subprocess_failure
        subprocess_failure('media-probe', tool=ffprobe, error=error, asset_id=asset_id,
                           returncode=getattr(error, 'returncode', None), timeout_seconds=timeout)
        return (-1, 0, 0, None, 0, 0, None, None, None, asset_id)
    if duration <= 0:
        from .error_reporting import event
        event('media-probe', asset_id=asset_id, reason='duration_unavailable')
    return (duration, width, height, codec, fps, audio,
            *context_fields(width, height, duration), asset_id)


def probe_unmeasured(
    db_path, ffprobe: str | None, location: str, root: str, *,
    report: Callable[[int, int], None] = lambda done, total: None,
    asset_ids: Sequence[int] | None = None,
    config=None,
) -> int:
    """`root`（账本口径）下还没探过的视频逐个探一遍，返回探了几个。"""
    if not ffprobe:
        return 0
    prefix = str(PureWindowsPath(root)).rstrip("\\") + "\\"
    selected = ''
    parameters = (location, len(prefix), prefix)
    if asset_ids is not None:
        if not asset_ids:
            return 0
        selected = ' AND id IN (' + ','.join('?' * len(asset_ids)) + ')'
        parameters += tuple(asset_ids)
    with closing(sqlite3.connect(db_path, timeout=30)) as connection:
        rows = connection.execute(
            f"SELECT id,path FROM asset WHERE {_UNMEASURED} AND substr(path,1,?)=?{selected} ORDER BY id",
            parameters).fetchall()
    rows = [row for row in rows if cost_for(location, row[1], config) == 'free']
    return _record(db_path, ffprobe, rows, report)


def probe_path(db_path, ffprobe: str | None, location: str, path: str, *, config=None) -> int:
    """推送发现刚登记的这一个文件；不是视频、已经探过或来源计费都回 0。"""
    if (not ffprobe or cost_for(location, path, config) == 'metered'
            or skip_background_read(path)):
        return 0
    with closing(sqlite3.connect(db_path, timeout=30)) as connection:
        rows = connection.execute(
            f"SELECT id,path FROM asset WHERE {_UNMEASURED} AND path=?",
            (location, str(PureWindowsPath(path)))).fetchall()
    return _record(db_path, ffprobe, rows, lambda done, total: None)


def _record(db_path, ffprobe: str, rows: Sequence, report: Callable[[int, int], None]) -> int:
    """探完一条写一条。`report` 抛出来（任务被停）时，还没开始的那些不再探。"""
    rows = [row for row in rows if not skip_background_read(row[1])]
    if not rows:
        return 0
    done = 0
    with closing(sqlite3.connect(db_path, timeout=120)) as connection:
        report(0, len(rows))
        for measured in _measure_batches(ffprobe, rows):
            connection.execute(UPDATE, measured)
            connection.commit()
            done += 1
            report(done, len(rows))
    return done


def _measure_batches(ffprobe, rows):
    """安全停点按档位领取探测，空闲后补位；退出时等待在途进程返回。"""
    start = 0
    futures = set()
    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='PeachProbe') as executor:
        while start < len(rows) or futures:
            checkpoint('probe')
            control = CURRENT.get()
            workers = {'low': 1, 'standard': 2, 'high': WORKERS}[control.mode] if control else WORKERS
            while start < len(rows) and len(futures) < workers:
                row = rows[start]
                futures.add(executor.submit(measure, ffprobe, row[0], row[1]))
                start += 1
            completed, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in completed:
                yield future.result()
