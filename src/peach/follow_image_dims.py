"""关注图片的固有宽高：从文件头读，不下载整张图。

图片墙按卡片高度分列，图落地前就得知道比例，否则每张加载完都把整墙重排一遍。
来源接口给尺寸的（fanbox 的 imageMap、rule34.xxx 的 dapi）在连接器里直接记下；
不给的（归档站、论坛附件）只能问文件本身——而尺寸都写在文件开头：PNG 的 IHDR、
GIF 的逻辑屏幕、WebP 的 VP8/VP8L/VP8X 块都在前几十字节，AVIF 的 `ispe` 盒在
`meta` 里、`mdat` 之前，JPEG 的 SOF 段通常在前几 KB，EXIF 缩略图大的也不过几十
KB。所以这里只发一个 `Range: bytes=0-N` 的请求，读到 `HEADER_BUDGET` 就停，一张
几 MB 的原图只花几十 KB。

判据只有这一份：连接器、回填脚本和界面回写落库前都经 `positive_dims` 归一。
"""
from __future__ import annotations

import struct
from collections.abc import Mapping

from .http import HttpRequest, HttpTransport
from .user_agent import USER_AGENT

#: 一次探测最多读多少字节。PNG/GIF/WebP 几十字节就够；JPEG 的 SOF 段在 EXIF 之后，
#: 相机原图的 EXIF 缩略图可以到几十 KB，64 KiB 覆盖绝大多数；再大的按未取得记。
HEADER_BUDGET = 65536

#: 尺寸的合理上限。超过它的多半是解析错位读到了别的字节，不是一张真图。
MAX_SIDE = 65535


class ImageDimsUnavailable(RuntimeError):
    """这一次没拿到尺寸：状态码不对、格式认不出或文件头被截在预算之外。"""


def positive_dims(width, height) -> tuple[int, int] | None:
    """两边都是正整数且不超过 `MAX_SIDE` 才算一对尺寸，否则 None。"""
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        return None
    if 0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE:
        return w, h
    return None


def dims_from_header(data: bytes) -> tuple[int, int] | None:
    """从文件开头的字节读宽高。认不出或字节不够时返回 None。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24 or data[12:16] != b"IHDR":
            return None
        return positive_dims(*struct.unpack(">II", data[16:24]))
    if data[:6] in (b"GIF87a", b"GIF89a"):
        if len(data) < 10:
            return None
        return positive_dims(*struct.unpack("<HH", data[6:10]))
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_dims(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg_dims(data)
    if data[4:8] == b"ftyp":
        # AVIF／HEIF 都是 ISO BMFF 盒结构；论坛把 AVIF 附件照旧起 `.png` 的名字，
        # 所以只看字节不看后缀。
        return _bmff_dims(data)
    return None


def _bmff_boxes(data: bytes, start: int, end: int):
    """顺着列出 [start, end) 里的盒：(类型, 内容起点, 内容终点)。"""
    offset = start
    while offset + 8 <= end:
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > end:
                return
            size = struct.unpack(">Q", data[offset + 8:offset + 16])[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header:
            return
        yield kind, offset + header, min(offset + size, end)
        offset += size


def _bmff_find(data: bytes, kind: bytes, start: int, end: int, *,
               full: bool = False) -> tuple[int, int] | None:
    for found, body_start, body_end in _bmff_boxes(data, start, end):
        if found == kind:
            # FullBox 在内容前多 4 字节的版本与标志。
            return (body_start + 4 if full else body_start), body_end
    return None


def _bmff_dims(data: bytes) -> tuple[int, int] | None:
    """AVIF／HEIF：`meta` → `iprp` → `ipco` 里第一枚 `ispe`（主图的空间尺寸）。"""
    meta = _bmff_find(data, b"meta", 0, len(data), full=True)
    if meta is None:
        return None
    iprp = _bmff_find(data, b"iprp", *meta)
    if iprp is None:
        return None
    ipco = _bmff_find(data, b"ipco", *iprp)
    if ipco is None:
        return None
    for kind, start, end in _bmff_boxes(data, *ipco):
        if kind == b"ispe" and end - start >= 12:
            return positive_dims(*struct.unpack(">II", data[start + 4:start + 12]))
    return None


def _webp_dims(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8 ":
        # 帧标签 3 字节、起始码 9d 01 2a 3 字节，然后各 14 位的宽高。
        if len(data) < 30 or data[23:26] != b"\x9d\x01\x2a":
            return None
        width, height = struct.unpack("<HH", data[26:30])
        return positive_dims(width & 0x3FFF, height & 0x3FFF)
    if chunk == b"VP8L":
        if len(data) < 25 or data[20] != 0x2F:
            return None
        bits = struct.unpack("<I", data[21:25])[0]
        return positive_dims((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8X":
        # 画布尺寸减一，各 24 位小端。
        if len(data) < 30:
            return None
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return positive_dims(width, height)
    return None


#: 带帧尺寸的 SOF 标记。C4（DHT）、C8（JPG 扩展）、CC（DAC）长得像 SOF，但不是。
_JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
#: 无长度字段的独立标记：RSTn 与 TEM。
_JPEG_STANDALONE = frozenset(range(0xD0, 0xD8)) | {0x01}


def _jpeg_dims(data: bytes) -> tuple[int, int] | None:
    offset = 2
    length = len(data)
    while offset + 4 <= length:
        if data[offset] != 0xFF:
            return None
        marker = data[offset + 1]
        if marker == 0xFF:
            # 填充字节，往后挪一个再看。
            offset += 1
            continue
        if marker in _JPEG_STANDALONE or marker == 0xD8:
            offset += 2
            continue
        if marker == 0xD9 or marker == 0xDA:
            # 文件结束或已进入扫描数据，尺寸不会再出现。
            return None
        segment = struct.unpack(">H", data[offset + 2:offset + 4])[0]
        if marker in _JPEG_SOF:
            if offset + 9 > length:
                return None
            height, width = struct.unpack(">HH", data[offset + 5:offset + 9])
            return positive_dims(width, height)
        offset += 2 + segment
    return None


def probe_image_dims(transport: HttpTransport, url: str, headers: Mapping[str, str] | None = None,
                     *, timeout: float = 15.0, budget: int = HEADER_BUDGET) -> tuple[int, int]:
    """只取文件头问一张图的宽高。

    `headers` 是来源需要的 Referer／Cookie 一类，与媒体代理同一套；这里只再加
    User-Agent 与 Range。上游不认 Range 也无妨：传输层读到 `budget` 就停。
    """
    request_headers = {"User-Agent": USER_AGENT, "Accept": "image/*",
                       "Range": f"bytes=0-{budget - 1}"}
    request_headers.update(headers or {})
    response = transport(HttpRequest("GET", url, request_headers), timeout, budget)
    if response.status not in (200, 206):
        raise ImageDimsUnavailable(f"HTTP {response.status}")
    dims = dims_from_header(response.body[:budget])
    if dims is None:
        raise ImageDimsUnavailable("文件头里没有认出尺寸")
    return dims
