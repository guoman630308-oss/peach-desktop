"""测试用的最小 MP4：时间表是真的，样本数据是可预测的填充字节。

`peach.mp4index` 只读 `moov`，关键帧表、时长表和显示时间偏移表都能这样造出来，
用例因此不必依赖 FFmpeg 或真实片源。`peach.mp4repair` 还要改写块偏移和编辑列表，
所以 `mvhd`、`tkhd`、`stsz`、`stco` 也一并造齐，`mdat` 里放真实长度的样本字节。
"""
from __future__ import annotations

import struct

SAMPLE_SIZE = 16
#: 影片时间刻度。编辑列表的段长用它，媒体时间刻度用 `timescale`。
MOVIE_TIMESCALE = 1000


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def sample_bytes(samples: int) -> bytes:
    """每个字节都能推算出来，拼接出的文件因此可以逐字节断言。"""
    return bytes((index * 7 + 11) % 251 for index in range(samples * SAMPLE_SIZE))


def minimal_mp4(
    *,
    timescale: int,
    sample_delta: int,
    samples: int,
    keyframe_every: int,
    composition_offsets: bool = False,
    edit_list: tuple[int, int] | None = None,
) -> bytes:
    """够 `peach.mp4index` 解析关键帧、够 `peach.mp4repair` 重建头的最小 MP4。

    `composition_offsets` 决定视频轨里有没有 `ctts`。有 B 帧的片源缺了这张表，容器
    声明的显示时刻就是解码顺序，浏览器会把倒着走的帧全丢掉。
    `edit_list` 是 `(段长, media_time)`，给不出就不写 `edts`。
    """
    duration = samples * sample_delta
    mvhd = box(b"mvhd", struct.pack(">IIIII", 0, 0, 0, MOVIE_TIMESCALE, duration)
               + bytes(80))
    tkhd = box(b"tkhd", struct.pack(">IIIIII", 0, 0, 0, 1, 0, duration) + bytes(60))
    edts = box(b"edts", box(b"elst", struct.pack(">IIIiI", 0, 1, *edit_list, 1 << 16))
               ) if edit_list else b""
    mdhd = box(b"mdhd", struct.pack(">4sIIII HH", bytes(4), 0, 0, timescale,
                                    duration, 0, 0))
    hdlr = box(b"hdlr", struct.pack(">4sI4s", bytes(4), 0, b"vide") + bytes(12))
    stts = box(b"stts", struct.pack(">IIII", 0, 1, samples, sample_delta))
    sync = [n for n in range(1, samples + 1) if (n - 1) % keyframe_every == 0]
    stss = box(b"stss", struct.pack(">II", 0, len(sync))
               + b"".join(struct.pack(">I", n) for n in sync))
    ctts = (box(b"ctts", struct.pack(">IIII", 0, 1, samples, sample_delta))
            if composition_offsets else b"")
    stsc = box(b"stsc", struct.pack(">IIIII", 0, 1, 1, 1, 1))
    stsz = box(b"stsz", struct.pack(">III", 0, SAMPLE_SIZE, samples))

    def head(chunk_start: int) -> bytes:
        stco = box(b"stco", struct.pack(">II", 0, samples) + b"".join(
            struct.pack(">I", chunk_start + index * SAMPLE_SIZE) for index in range(samples)))
        stbl = box(b"stbl", stts + stss + ctts + stsc + stsz + stco)
        mdia = box(b"mdia", mdhd + hdlr + box(b"minf", stbl))
        return (box(b"ftyp", b"isom" + bytes(8))
                + box(b"moov", mvhd + box(b"trak", tkhd + edts + mdia))
                + struct.pack(">I", samples * SAMPLE_SIZE + 8) + b"mdat")

    # 块偏移是文件内的绝对位置，得先知道头有多长；条目数固定，两遍长度必然相同。
    return head(len(head(0))) + sample_bytes(samples)
