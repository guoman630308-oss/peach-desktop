"""缺 `moov` 的 MP4：认得出、挑得对参照、验得住结果、换得回原路径。

untrunc 与 ffmpeg 本身不在这里跑：要验的是围着它们的判据和文件操作。
"""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from peach import mp4recover
from peach.mp4repair import RepairUnavailable


def box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body) + 8) + kind + body


class MissingIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name: str, payload: bytes) -> Path:
        path = self.root / name
        path.write_bytes(payload)
        return path

    def test_a_download_that_stopped_before_the_index_is_recognised(self):
        """实测的坏片：`ftyp`、`free`，然后一个写到文件末尾的 `mdat`。"""
        path = self.write("cut.mp4", box(b"ftyp", b"isom" * 4) + box(b"free", b"")
                          + struct.pack(">I", 0) + b"mdat" + b"\x21" * 64)

        self.assertTrue(mp4recover.missing_index(path))

    def test_a_complete_file_is_left_alone(self):
        path = self.write("whole.mp4", box(b"ftyp", b"isom" * 4) + box(b"moov", b"\0" * 16)
                          + box(b"mdat", b"\x21" * 64))

        self.assertFalse(mp4recover.missing_index(path))

    def test_something_that_is_not_an_mp4_at_all_is_not_claimed(self):
        """改了扩展名的压缩包没有 `mdat`，不归这里修。"""
        path = self.write("fake.mp4", b"PK\x03\x04" + b"\0" * 64)

        self.assertFalse(mp4recover.missing_index(path))


class ReferenceOrderTests(unittest.TestCase):
    def test_neighbours_in_the_same_folder_are_tried_first(self):
        """同一批下载的文件名挨在一起：隔壁那部的编码参数最可能一样。"""
        broken = "B:\\作者\\甲\\2021-01-03_b.mp4"
        candidates = [
            "B:\\作者\\乙\\2021-01-03_a.mp4",
            "B:\\作者\\甲\\2020-11-30_a.mp4",
            "B:\\作者\\甲\\2021-01-03_c.mp4",
            "B:\\作者\\甲\\2021-02-07_d.mp4",
        ]

        picked = mp4recover.pick_references(broken, candidates)

        self.assertEqual(set(picked[:2]), {"B:\\作者\\甲\\2020-11-30_a.mp4", "B:\\作者\\甲\\2021-01-03_c.mp4"})
        self.assertEqual(picked[-1], "B:\\作者\\乙\\2021-01-03_a.mp4")

    def test_the_broken_file_never_serves_as_its_own_reference(self):
        broken = "B:\\x\\a.mp4"

        self.assertEqual(mp4recover.pick_references(broken, [broken, "B:\\x\\b.mp4"]), ["B:\\x\\b.mp4"])

    def test_only_a_handful_are_tried(self):
        """每试一个都要把坏片整个读一遍。"""
        candidates = [f"B:\\x\\{index:03}.mp4" for index in range(20)]

        self.assertEqual(len(mp4recover.pick_references("B:\\x\\010.mp4", candidates)),
                         mp4recover.REFERENCE_LIMIT)


class AcceptanceTests(unittest.TestCase):
    def test_a_clean_decode_with_matching_tracks_is_kept(self):
        self.assertTrue(mp4recover.acceptable({"video": 282.48, "audio": 282.52}, 1))

    def test_decode_errors_throughout_mean_the_reference_was_wrong(self):
        """参照分辨率不对时每一帧都报宏块错误，实测 1855 行。"""
        self.assertFalse(mp4recover.acceptable({"video": 75.28, "audio": 75.29}, 1855))

    def test_tracks_that_disagree_on_length_mean_the_timeline_is_wrong(self):
        """参照的采样率对不上时，音频只切出 11 秒、视频却有 80 秒。"""
        self.assertFalse(mp4recover.acceptable({"video": 80.08, "audio": 11.69}, 0))

    def test_nothing_playable_is_not_a_repair(self):
        self.assertFalse(mp4recover.acceptable({"audio": 12.0}, 0))


class ReplaceOriginalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "library" / "片子.mp4"
        self.source.parent.mkdir()
        self.source.write_bytes(b"broken")
        self.repaired = self.root / "work" / "recovered.mp4"
        self.repaired.parent.mkdir()
        self.repaired.write_bytes(b"repaired!")

    def test_the_repaired_file_takes_the_original_path_and_the_original_is_kept_beside_it(self):
        kept = mp4recover.replace_original(self.source, self.repaired)

        self.assertEqual(self.source.read_bytes(), b"repaired!")
        self.assertEqual(kept, self.source.with_name(".片子.mp4.peach-original"))
        self.assertEqual(kept.read_bytes(), b"broken")
        self.assertFalse(self.repaired.exists())
        self.assertEqual(sorted(path.name for path in self.source.parent.iterdir()),
                         [".片子.mp4.peach-original", "片子.mp4"])

    def test_an_earlier_kept_original_is_never_overwritten(self):
        earlier = self.source.with_name(".片子.mp4.peach-original")
        earlier.write_bytes(b"first")

        with self.assertRaises(RepairUnavailable):
            mp4recover.replace_original(self.source, self.repaired)

        self.assertEqual((self.source.read_bytes(), earlier.read_bytes()), (b"broken", b"first"))

    def test_a_failed_swap_puts_the_original_back(self):
        real = os.replace
        calls = []

        def flaky(source, target):
            calls.append(Path(target).name)
            if len(calls) == 2:
                raise OSError("网盘断开")
            return real(source, target)

        with mock.patch.object(mp4recover.os, "replace", flaky), self.assertRaises(OSError):
            mp4recover.replace_original(self.source, self.repaired)

        self.assertEqual(self.source.read_bytes(), b"broken")
        self.assertEqual([path.name for path in self.source.parent.iterdir()], ["片子.mp4"])


class ToolLookupTests(unittest.TestCase):
    def test_the_frozen_bundle_precedes_the_data_tools_and_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            executable = root / 'Peach.exe'
            bundled = root / 'tools/untrunc' / f"untrunc{'.exe' if os.name == 'nt' else ''}"
            bundled.parent.mkdir(parents=True)
            bundled.touch()
            tools = root / 'data/tools'
            managed = tools / 'untrunc' / bundled.name
            managed.parent.mkdir(parents=True)
            managed.touch()
            with mock.patch.dict(os.environ, {mp4recover.UNTRUNC_ENV: ''}), \
                    mock.patch.object(mp4recover.sys, 'frozen', True, create=True), \
                    mock.patch.object(mp4recover.sys, 'executable', str(executable)), \
                    mock.patch.object(mp4recover.shutil, 'which', return_value=None) as which:
                self.assertEqual(mp4recover.untrunc_path(tools), bundled)
            which.assert_not_called()

    def test_an_explicit_binary_precedes_the_frozen_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            custom = root / 'custom-untrunc.exe'
            custom.touch()
            with mock.patch.dict(os.environ, {mp4recover.UNTRUNC_ENV: str(custom)}), \
                    mock.patch.object(mp4recover.sys, 'frozen', True, create=True):
                self.assertEqual(mp4recover.untrunc_path(root / 'data/tools'), custom)

    def test_a_missing_bundled_binary_uses_data_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            tools = root / 'data/tools'
            managed = tools / 'untrunc' / f"untrunc{'.exe' if os.name == 'nt' else ''}"
            managed.parent.mkdir(parents=True)
            managed.touch()
            with mock.patch.dict(os.environ, {mp4recover.UNTRUNC_ENV: ''}), \
                    mock.patch.object(mp4recover.sys, 'frozen', True, create=True), \
                    mock.patch.object(mp4recover.sys, 'executable', str(root / 'Peach.exe')):
                self.assertEqual(mp4recover.untrunc_path(tools), managed)

    def test_the_managed_copy_under_tools_is_found(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            binary = tools / "untrunc" / f"untrunc{'.exe' if os.name == 'nt' else ''}"
            binary.parent.mkdir()
            binary.write_bytes(b"")
            with mock.patch.dict(os.environ, {mp4recover.UNTRUNC_ENV: ""}), \
                    mock.patch.object(mp4recover.shutil, "which", return_value=None):
                self.assertEqual(mp4recover.untrunc_path(tools), binary.resolve())

    def test_nothing_installed_reads_as_none(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {mp4recover.UNTRUNC_ENV: ""}), \
                mock.patch.object(mp4recover.shutil, "which", return_value=None):
            self.assertIsNone(mp4recover.untrunc_path(Path(directory)))


if __name__ == "__main__":
    unittest.main()
