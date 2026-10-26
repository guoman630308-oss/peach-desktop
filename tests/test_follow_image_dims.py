import struct
import unittest

from peach.follow_image_dims import (
    HEADER_BUDGET, ImageDimsUnavailable, dims_from_header, positive_dims, probe_image_dims,
)
from peach.http import HttpResponse


def png(width, height):
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")


def gif(width, height):
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\xf7\x00\x00"


def webp_vp8(width, height):
    payload = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height)
    return b"RIFF" + struct.pack("<I", 4 + 8 + len(payload)) + b"WEBP" + b"VP8 " \
        + struct.pack("<I", len(payload)) + payload


def webp_vp8l(width, height):
    bits = (width - 1) | ((height - 1) << 14)
    payload = b"\x2f" + struct.pack("<I", bits)
    return b"RIFF" + struct.pack("<I", 4 + 8 + len(payload)) + b"WEBP" + b"VP8L" \
        + struct.pack("<I", len(payload)) + payload


def webp_vp8x(width, height):
    payload = b"\x10\x00\x00\x00" + (width - 1).to_bytes(3, "little") \
        + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + 8 + len(payload)) + b"WEBP" + b"VP8X" \
        + struct.pack("<I", len(payload)) + payload


def box(kind, payload, *, full=False):
    body = (b"\x00\x00\x00\x00" if full else b"") + payload
    return struct.pack(">I", 8 + len(body)) + kind + body


def avif(width, height):
    """ftyp，然后 meta（FullBox）里 iprp → ipco → ispe；主图之外还有一枚透明层的 ispe。"""
    ispe = box(b"ispe", struct.pack(">II", width, height), full=True)
    alpha = box(b"ispe", struct.pack(">II", 8, 8), full=True)
    ipco = box(b"ipco", box(b"colr", b"nclx\x00\x01\x00\x0d\x00\x06\x80") + ispe + alpha)
    meta = box(b"meta", box(b"hdlr", b"\x00" * 20) + box(b"iprp", ipco), full=True)
    return box(b"ftyp", b"avif\x00\x00\x00\x00avifmif1miaf") + meta + box(b"mdat", b"\x00" * 16)


def jpeg(width, height, *, exif_bytes=0, sof=0xC0):
    """SOI，一段 APP1（EXIF 占位），一段 DQT，然后才是 SOF。"""
    data = b"\xff\xd8"
    if exif_bytes:
        exif = b"Exif\x00\x00" + b"\x00" * exif_bytes
        data += b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    dqt = b"\x00" + b"\x01" * 64
    data += b"\xff\xdb" + struct.pack(">H", len(dqt) + 2) + dqt
    frame = b"\x08" + struct.pack(">HH", height, width) + b"\x03"
    data += bytes([0xFF, sof]) + struct.pack(">H", len(frame) + 2) + frame
    return data + b"\xff\xda\x00\x08" + b"\x00" * 64


class HeaderDimsTests(unittest.TestCase):
    def test_each_format_reads_its_size_from_the_first_bytes(self):
        self.assertEqual(dims_from_header(png(1920, 1080)), (1920, 1080))
        self.assertEqual(dims_from_header(gif(320, 240)), (320, 240))
        self.assertEqual(dims_from_header(webp_vp8(1024, 768)), (1024, 768))
        self.assertEqual(dims_from_header(webp_vp8l(2048, 1536)), (2048, 1536))
        self.assertEqual(dims_from_header(webp_vp8x(4000, 3000)), (4000, 3000))
        self.assertEqual(dims_from_header(jpeg(1280, 720)), (1280, 720))
        # 论坛把 AVIF 附件照旧起 `.png` 的名字，认字节不认后缀。
        self.assertEqual(dims_from_header(avif(2560, 1440)), (2560, 1440))
        self.assertIsNone(dims_from_header(avif(2560, 1440)[:40]))

    def test_jpeg_walks_past_exif_and_reads_progressive_frames_too(self):
        self.assertEqual(dims_from_header(jpeg(3000, 2000, exif_bytes=20_000)), (3000, 2000))
        self.assertEqual(dims_from_header(jpeg(640, 480, sof=0xC2)), (640, 480))

    def test_a_jpeg_huffman_table_is_not_mistaken_for_a_frame(self):
        # C4 是 DHT，长度字段后面是表，不是帧头；按 SOF 读会得到一对胡乱的数。
        data = b"\xff\xd8" + b"\xff\xc4" + struct.pack(">H", 8) + b"\x00\x01\x02\x03\x04\x05" \
            + b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", 480, 640) + b"\x03\x00\x00\x00\x00"
        self.assertEqual(dims_from_header(data), (640, 480))

    def test_truncated_or_unknown_bytes_give_nothing_rather_than_a_guess(self):
        self.assertIsNone(dims_from_header(png(1, 1)[:20]))
        self.assertIsNone(dims_from_header(jpeg(1280, 720)[:6]))
        self.assertIsNone(dims_from_header(b"<html><body>404</body></html>"))
        self.assertIsNone(dims_from_header(b""))
        self.assertIsNone(dims_from_header(b"RIFF\x00\x00\x00\x00WAVEfmt "))

    def test_only_a_pair_of_positive_bounded_integers_counts(self):
        self.assertEqual(positive_dims("1280", 720.0), (1280, 720))
        self.assertIsNone(positive_dims(0, 720))
        self.assertIsNone(positive_dims(-1, 720))
        self.assertIsNone(positive_dims(None, 720))
        self.assertIsNone(positive_dims("wide", 720))
        self.assertIsNone(positive_dims(70_000, 720))


class ProbeTests(unittest.TestCase):
    def _transport(self, status, body, seen):
        def call(request, timeout, max_bytes):
            seen.append((request, timeout, max_bytes))
            return HttpResponse(status, {}, body)
        return call

    def test_the_probe_asks_for_the_header_range_only_and_keeps_source_headers(self):
        seen = []
        dims = probe_image_dims(self._transport(206, png(800, 600), seen),
                                "https://img.example/a.png",
                                {"Referer": "https://example/post/1", "Cookie": "s=1"})
        self.assertEqual(dims, (800, 600))
        request, _, max_bytes = seen[0]
        self.assertEqual(request.headers["Range"], f"bytes=0-{HEADER_BUDGET - 1}")
        self.assertEqual(request.headers["Referer"], "https://example/post/1")
        self.assertEqual(request.headers["Cookie"], "s=1")
        self.assertIn("User-Agent", request.headers)
        self.assertEqual(max_bytes, HEADER_BUDGET)

    def test_a_full_response_is_fine_but_a_refusal_or_a_non_image_is_not(self):
        self.assertEqual(
            probe_image_dims(self._transport(200, jpeg(300, 200), []), "https://img.example/a.jpg"),
            (300, 200))
        with self.assertRaises(ImageDimsUnavailable):
            probe_image_dims(self._transport(404, b"", []), "https://img.example/a.jpg")
        with self.assertRaises(ImageDimsUnavailable):
            probe_image_dims(self._transport(200, b"<html>login</html>", []),
                             "https://img.example/a.jpg")


if __name__ == "__main__":
    unittest.main()
