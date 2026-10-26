"""从 MP4 头部直接读关键帧时间表。

HLS 分片必须切在关键帧上，所以要先知道关键帧在哪。用
`ffprobe -skip_frame nokey` 也能拿到，但那会把整个文件解复用一遍——对挂载网盘来说
等于把整部片拉一遍，正好抵消了分片省流量的目的。

MP4 把关键帧表（`stss`）和时长表（`stts`）都放在 `moov` 里，通常位于文件开头，
读几 MB 就够。解析不出来就返回 None，由调用方回退到标准 Range，绝不猜。

同一份 `moov` 还回答另一个问题：视频轨有没有 `ctts`（显示时间偏移表）。有 B 帧的片子
靠它才能把解码顺序还原成显示顺序，缺了它容器声明的显示时刻就是解码顺序。
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

# moov 通常在文件头部；非 faststart 的文件会把它放在结尾，另行处理。
HEADER_PROBE_BYTES = 32 << 20


def _iter_boxes(payload: bytes, start: int, end: int):
    """逐个产出 (类型, 内容起点, 内容终点)。遇到坏长度就停，不抛异常。"""
    offset = start
    while offset + 8 <= end:
        size = int.from_bytes(payload[offset:offset + 4], "big")
        kind = payload[offset + 4:offset + 8]
        body = offset + 8
        if size == 1:                       # 64 位长度
            if body + 8 > end:
                return
            size = int.from_bytes(payload[body:body + 8], "big")
            body += 8
        elif size == 0:                     # 延伸到容器末尾
            size = end - offset
        if size < 8 or offset + size > end:
            return
        yield kind, body, offset + size
        offset += size


def _find(payload: bytes, start: int, end: int, kind: bytes):
    for box, body, stop in _iter_boxes(payload, start, end):
        if box == kind:
            return body, stop
    return None


def _video_stbl(payload: bytes, start: int, end: int):
    """返回视频轨的 (timescale, stbl 区间)。"""
    for box, body, stop in _iter_boxes(payload, start, end):
        if box != b"trak":
            continue
        mdia = _find(payload, body, stop, b"mdia")
        if not mdia:
            continue
        hdlr = _find(payload, *mdia, b"hdlr")
        if not hdlr or payload[hdlr[0] + 8:hdlr[0] + 12] != b"vide":
            continue
        mdhd = _find(payload, *mdia, b"mdhd")
        minf = _find(payload, *mdia, b"minf")
        if not mdhd or not minf:
            continue
        stbl = _find(payload, *minf, b"stbl")
        if not stbl:
            continue
        version = payload[mdhd[0]]
        timescale = struct.unpack_from(">I", payload, mdhd[0] + (20 if version == 1 else 12))[0]
        if timescale:
            return timescale, stbl
    return None


def _video_sample_table(payload: bytes, start: int, end: int):
    """返回视频轨的 (timescale, stts 区间, stss 区间)。"""
    found = _video_stbl(payload, start, end)
    if found is None:
        return None
    timescale, stbl = found
    stts = _find(payload, *stbl, b"stts")
    stss = _find(payload, *stbl, b"stss")
    return (timescale, stts, stss) if stts and stss else None


def _sync_sample_seconds(payload: bytes, timescale: int, stts, stss) -> list[float]:
    body, stop = stts
    count = struct.unpack_from(">I", payload, body + 4)[0]
    entries = []
    cursor = body + 8
    for _ in range(count):
        if cursor + 8 > stop:
            return []
        entries.append(struct.unpack_from(">II", payload, cursor))
        cursor += 8

    body, stop = stss
    sync_count = struct.unpack_from(">I", payload, body + 4)[0]
    cursor = body + 8
    wanted = []
    for _ in range(sync_count):
        if cursor + 4 > stop:
            return []
        wanted.append(struct.unpack_from(">I", payload, cursor)[0])
        cursor += 4
    if not wanted:
        return []

    # stts 是游程编码的「多少个样本，每个多长」，按需展开到目标样本号即可。
    times: list[float] = []
    target = iter(sorted(wanted))
    following = next(target, None)
    sample = 1
    elapsed = 0
    for run_length, delta in entries:
        for _ in range(run_length):
            while following is not None and following == sample:
                times.append(elapsed / timescale)
                following = next(target, None)
            if following is None:
                return times
            elapsed += delta
            sample += 1
    return times


def _read_moov(handle, limit: int) -> bytes | None:
    """按顶层 box 头逐个跳过，只把 moov 那一段读进内存。

    `moov` 不一定在文件开头——非 faststart 的文件会把它放在结尾。顺序读到它可能要拉
    几个 GB，而 box 头只有 8/16 字节，seek 过去几乎不产生流量。
    """
    offset = 0
    while True:
        handle.seek(offset)
        header = handle.read(16)
        if len(header) < 8:
            return None
        size = int.from_bytes(header[0:4], "big")
        kind = header[4:8]
        body = offset + 8
        if size == 1:
            if len(header) < 16:
                return None
            size = int.from_bytes(header[8:16], "big")
            body = offset + 16
        elif size == 0:
            size = max(0, handle.seek(0, 2) - offset)
        if size < 8:
            return None
        if kind == b"moov":
            if size > limit:
                return None
            handle.seek(body)
            return handle.read(offset + size - body)
        offset += size


def _moov_of(path: Path | str, probe_bytes: int) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return _read_moov(handle, probe_bytes)
    except OSError:
        return None


def movie_seconds(head: bytes) -> float | None:
    """文件开头这一段里 `mvhd` 声明的整片时长（秒），读不出就是 None。

    给只取得到文件头的远端视频用：`mvhd` 是 `moov` 的第一个子盒，faststart 的文件
    开头几百字节就有它，`moov` 其余部分被截在这一段之外也不影响。`moov` 在文件结尾
    的读不出，交给调用方记作未取得。
    """
    moov = _read_moov(io.BytesIO(head), HEADER_PROBE_BYTES)
    found = _find(moov, 0, len(moov), b"mvhd") if moov else None
    if not found:
        return None
    box = moov[found[0]:found[1]]
    if box[:1] == b"\x01":
        scale, length = int.from_bytes(box[20:24], "big"), int.from_bytes(box[24:32], "big")
    else:
        scale, length = int.from_bytes(box[12:16], "big"), int.from_bytes(box[16:20], "big")
    return round(length / scale, 3) if scale and length else None


def composition_offsets_present(
    path: Path | str, probe_bytes: int = HEADER_PROBE_BYTES,
) -> bool | None:
    """视频轨里有没有 `ctts`。读不出 `moov` 或没有视频轨返回 None。

    回答的是「有没有这张表」，不是「该不该有」：没有 B 帧的片子本来就不需要 ctts。
    调用方要把它和帧重排深度（ffprobe 的 `has_b_frames`）放在一起看才有结论。
    """
    moov = _moov_of(path, probe_bytes)
    if not moov:
        return None
    found = _video_stbl(moov, 0, len(moov))
    if found is None:
        return None
    return _find(moov, *found[1], b"ctts") is not None


def keyframe_seconds(path: Path | str, probe_bytes: int = HEADER_PROBE_BYTES) -> list[float] | None:
    """返回 MP4 的关键帧时间（秒）。拿不到就返回 None，由调用方回退到标准 Range。"""
    moov = _moov_of(path, probe_bytes)
    if not moov:
        return None
    table = _video_sample_table(moov, 0, len(moov))
    if table is None:
        return None
    times = _sync_sample_seconds(moov, *table)
    return sorted(set(times)) or None


def segment_plan(keyframes: list[float], duration: float,
                 target_seconds: float) -> list[tuple[float, float]]:
    """把关键帧合并成不短于 target 的片段，返回 [(起点, 时长)]。

    边界一律落在真实关键帧上，`-c copy` 才切得准，播放列表报的时长也才是真的。
    """
    if duration <= 0 or target_seconds <= 0:
        return []
    starts = [value for value in keyframes if 0 <= value < duration]
    if not starts:
        return []
    if starts[0] > 0:
        starts.insert(0, 0.0)
    plan: list[tuple[float, float]] = []
    begin = starts[0]
    for value in starts[1:]:
        if value - begin >= target_seconds:
            plan.append((begin, value - begin))
            begin = value
    plan.append((begin, duration - begin))
    return [(start, length) for start, length in plan if length > 0]
