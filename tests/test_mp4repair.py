"""缺 `ctts` 的 MP4 重建头。

用例不跑解码器：显示顺序那一步换成已知的置换，剩下的全是头里的算术，可以逐字节断言。
"""
from __future__ import annotations

import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from peach import mp4repair
from peach.ffmpeg import BinaryChoice
from peach.mp4index import _find, _iter_boxes, _video_stbl
from peach.mp4repair import (
    HeaderRepairStore, RepairUnavailable, RepairedHeader, read_sidecar,
    repaired_header, write_sidecar,
)

from support.mp4 import SAMPLE_SIZE, minimal_mp4, sample_bytes

#: 解码序 I P B B P B B …，显示序 I B B P B B P …；每组三帧里 P 要等两帧才显示。
DECODE_TO_DISPLAY = (0, 3, 1, 2, 6, 4, 5, 9, 7, 8, 12, 10, 11)


def moov_of(blob: bytes) -> bytes:
    for kind, body, stop in _iter_boxes(blob, 0, len(blob)):
        if kind == b"moov":
            return blob[body:stop]
    raise AssertionError("没有 moov")


def table(moov: bytes, kind: bytes) -> bytes | None:
    found = _video_stbl(moov, 0, len(moov))
    assert found is not None
    span = _find(moov, *found[1], kind)
    return None if span is None else moov[span[0]:span[1]]


def entries(payload: bytes, layout: str) -> list[tuple[int, ...]]:
    width = struct.calcsize(layout)
    count = struct.unpack_from(">I", payload, 4)[0]
    return [struct.unpack_from(layout, payload, 8 + index * width) for index in range(count)]


def composition_times(moov: bytes, sample_delta: int) -> list[int]:
    """每个样本的显示时刻：解码时刻加上 `ctts` 里的偏移。"""
    offsets: list[int] = []
    for run, offset in entries(table(moov, b"ctts"), ">Ii"):
        offsets.extend([offset] * run)
    return [index * sample_delta + offset for index, offset in enumerate(offsets)]


class Mp4HeaderRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def broken_mp4(self, path: Path, **options) -> Path:
        path.write_bytes(minimal_mp4(
            timescale=1000, sample_delta=400, samples=len(DECODE_TO_DISPLAY),
            keyframe_every=6, **options))
        return path

    def repair(self, path: Path, order=DECODE_TO_DISPLAY) -> RepairedHeader:
        """把「解一遍片子问显示顺序」换成已知答案，只验头里的算术。"""
        with patch.object(mp4repair, "_display_order",
                          lambda ffprobe, source, times: list(order)):
            return repaired_header(path, Path("ffprobe"))

    def test_a_rebuilt_header_puts_every_frame_back_in_display_order(self):
        source = self.broken_mp4(self.root / "broken.mp4")
        header = self.repair(source)
        times = composition_times(moov_of(header.prefix), 400)

        self.assertEqual(sorted(times), sorted(set(times)), "两帧显示在同一时刻")
        self.assertEqual([index for index, _ in sorted(enumerate(times), key=lambda pair: pair[1])],
                         [DECODE_TO_DISPLAY.index(rank) for rank in range(len(DECODE_TO_DISPLAY))])
        self.assertTrue(all(time >= index * 400 for index, time in enumerate(times)),
                        "有帧的显示时刻早于它的解码时刻")

    def test_the_spliced_file_keeps_the_original_media_bytes(self):
        source = self.broken_mp4(self.root / "broken.mp4")
        header = self.repair(source)
        blob = source.read_bytes()

        self.assertEqual(blob[header.payload_start:header.payload_end],
                         sample_bytes(len(DECODE_TO_DISPLAY)))
        self.assertEqual(header.size, len(header.prefix) + len(sample_bytes(len(DECODE_TO_DISPLAY))))

    def test_chunk_offsets_move_with_the_longer_header(self):
        source = self.broken_mp4(self.root / "broken.mp4")
        header = self.repair(source)
        delta = len(header.prefix) - header.payload_start
        before = entries(table(moov_of(source.read_bytes()), b"stco"), ">I")
        after = entries(table(moov_of(header.prefix), b"stco"), ">I")

        self.assertGreater(delta, 0)
        self.assertEqual([value + delta for (value,) in before], [value for (value,) in after])
        self.assertEqual(after[0][0], len(header.prefix))
        self.assertEqual(after[-1][0],
                         header.size - SAMPLE_SIZE, "最后一个块没落在文件末尾那一段上")

    def test_an_edit_list_that_already_compensates_the_shift_is_left_alone(self):
        """封装器为 ctts 补过的那一刀要原样留着，再补一次会真的切掉开头那两帧。"""
        source = self.broken_mp4(self.root / "broken.mp4", edit_list=(5200, 400))
        header = self.repair(source)

        self.assertEqual(entries(self.edit_list(header), ">IiI"), [(5200, 400, 1 << 16)])

    def test_a_real_trim_keeps_its_own_start_and_takes_the_shift_on_top(self):
        source = self.broken_mp4(self.root / "broken.mp4", edit_list=(4000, 2000))
        header = self.repair(source)

        self.assertEqual(entries(self.edit_list(header), ">IiI"), [(4000, 2400, 1 << 16)])

    def test_a_file_without_an_edit_list_gets_one_that_starts_at_the_shift(self):
        source = self.broken_mp4(self.root / "broken.mp4")
        header = self.repair(source)

        self.assertEqual(entries(self.edit_list(header), ">IiI"), [(5200, 400, 1 << 16)])

    def edit_list(self, header: RepairedHeader) -> bytes:
        moov = moov_of(header.prefix)
        trak = _find(moov, 0, len(moov), b"trak")
        assert trak is not None
        edts = _find(moov, *trak, b"edts")
        assert edts is not None
        elst = _find(moov, *edts, b"elst")
        assert elst is not None
        return moov[elst[0]:elst[1]]

    def test_a_file_that_already_has_composition_offsets_is_refused(self):
        source = self.broken_mp4(self.root / "fine.mp4", composition_offsets=True)

        with self.assertRaises(RepairUnavailable):
            self.repair(source)

    def test_a_display_order_equal_to_the_decode_order_is_refused(self):
        source = self.broken_mp4(self.root / "fine.mp4")

        with self.assertRaises(RepairUnavailable):
            self.repair(source, order=range(len(DECODE_TO_DISPLAY)))

    def test_the_display_order_is_the_order_the_decoder_hands_frames_back(self):
        """解码器按显示顺序出帧，每帧的时间戳原样来自它那个样本，所以置换是读出来的。"""
        with patch.object(mp4repair.subprocess, "run",
                          lambda *args, **options: _Probe(b"0\n800\n1200\n400\n")):
            order = mp4repair._display_order(Path("ffprobe"), Path("movie.mp4"),
                                             [0, 400, 800, 1200])

        self.assertEqual(order, [0, 3, 1, 2])

    def test_a_decode_that_loses_frames_is_refused_instead_of_guessed(self):
        """少一帧就说明这份表和码流对不上，宁可不修，也不能写一张错的 ctts。"""
        with patch.object(mp4repair.subprocess, "run",
                          lambda *args, **options: _Probe(b"0\n800\n")):
            with self.assertRaises(RepairUnavailable):
                mp4repair._display_order(Path("ffprobe"), Path("movie.mp4"), [0, 400, 800])


class SidecarTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def test_a_sidecar_reads_back_exactly_what_was_written(self):
        header = RepairedHeader(prefix=b"head", payload_start=7, payload_end=99, suffix=b"tail")
        path = self.root / "9-1-2.mp4hdr"
        write_sidecar(path, header)

        self.assertEqual(read_sidecar(path), header)

    def test_a_truncated_sidecar_reads_as_missing(self):
        path = self.root / "9-1-2.mp4hdr"
        write_sidecar(path, RepairedHeader(prefix=b"head", payload_start=7, payload_end=99))
        path.write_bytes(path.read_bytes()[:-1])

        self.assertIsNone(read_sidecar(path))

    def test_a_missing_sidecar_reads_as_missing(self):
        self.assertIsNone(read_sidecar(self.root / "nothing.mp4hdr"))


class HeaderRepairStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.source = self.root / "movie.mp4"
        self.source.write_bytes(b"x" * 64)
        self.store = HeaderRepairStore(self.root / "cache", _Resolver())
        self.header = RepairedHeader(prefix=b"head", payload_start=8, payload_end=64)

    def sidecar(self) -> Path:
        stat = self.source.stat()
        return self.store.root / f"6297-{stat.st_size}-{stat.st_mtime_ns}.mp4hdr"

    def test_a_stored_header_is_found_again_for_the_same_file(self):
        write_sidecar(self.sidecar(), self.header)

        self.assertEqual(self.store.lookup(6297, self.source), self.header)

    def test_a_changed_source_file_invalidates_the_stored_header(self):
        write_sidecar(self.sidecar(), self.header)
        self.source.write_bytes(b"y" * 65)

        self.assertIsNone(self.store.lookup(6297, self.source))

    def test_a_missing_source_file_answers_none_instead_of_raising(self):
        self.assertIsNone(self.store.lookup(6297, self.root / "gone.mp4"))

    def test_a_looked_up_header_is_kept_for_the_next_range_request(self):
        """一份头有几 MB，拖动一次进度条就是十几个 Range，不能每个都回去读文件。"""
        sidecar = self.sidecar()
        write_sidecar(sidecar, self.header)
        self.assertEqual(self.store.lookup(6297, self.source), self.header)
        sidecar.unlink()

        self.assertEqual(self.store.lookup(6297, self.source), self.header)

    def test_the_probe_that_gets_run_is_the_one_the_resolver_points_at(self):
        """定位器答的是 `BinaryChoice`，解码那一步要的是它里面那条路径。"""
        seen: list[Path] = []

        def build(source: Path, ffprobe: Path):
            seen.append(ffprobe)
            return self.header

        with patch.object(mp4repair, "repaired_header", build):
            self.assertTrue(self.store.repair_now(6297, self.source))

        self.assertEqual(seen, [Path("ffprobe")])

    def test_a_source_that_cannot_be_repaired_is_not_tried_again(self):
        """修不了的片子每次播放都重解一遍，就是把一分多钟的整片读白扔一次。"""
        attempts: list[Path] = []

        def build(source: Path, ffprobe: Path):
            attempts.append(source)
            raise RepairUnavailable("测试用：这个片源修不了")

        with patch.object(mp4repair, "repaired_header", build):
            outcomes = [self.store.repair_now(6297, self.source) for _ in range(3)]

        self.assertEqual(outcomes, [False, False, False])
        self.assertEqual(len(attempts), 1)
        self.assertIsNone(self.store.lookup(6297, self.source))

    def test_a_stored_header_is_not_computed_a_second_time(self):
        write_sidecar(self.sidecar(), self.header)

        with patch.object(mp4repair, "repaired_header", _never_called):
            self.assertFalse(self.store.repair_now(6297, self.source))

    def test_a_fresh_header_replaces_the_one_left_by_an_older_copy(self):
        stale = self.store.root / "6297-1-1.mp4hdr"
        write_sidecar(stale, self.header)

        with patch.object(mp4repair, "repaired_header", lambda source, ffprobe: self.header):
            self.assertTrue(self.store.repair_now(6297, self.source))

        self.assertFalse(stale.exists())
        self.assertEqual(self.store.lookup(6297, self.source), self.header)


def _never_called(source: Path, ffprobe: Path):
    raise AssertionError("不该再算一遍")


class _Probe:
    """`subprocess.run` 的返回值里，`_display_order` 只看这两样。"""

    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class _Resolver:
    """够 `HeaderRepairStore` 用的 FFmpeg 定位器：答案和生产里一样是 `BinaryChoice`。"""

    def ffprobe(self) -> BinaryChoice:
        return BinaryChoice(Path("ffprobe"), "test")


if __name__ == "__main__":
    unittest.main()
