"""按媒体库批量修 MP4 这一轮任务。

判定、重建头和重建索引都用替身：这里要验的是选谁、按什么顺序、哪些不必再问一遍、
修好之后文件和账本落成什么样。重建头的算术在 `test_mp4repair` 里，重建索引的判据在
`test_mp4recover` 里。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest import mock

from peach import web_contract as rm_web
from peach import web_media_repair as rm_repair
from peach.mp4recover import Recovery
from peach.mp4repair import RepairUnavailable

from support.ledger import fresh_ledger

SEP = chr(92)
#: 顶层只有 `ftyp` 和写到文件末尾的 `mdat`：下载没写完、缺整个索引的那一类。
CUT_SHORT = b"\x00\x00\x00\x10ftypisom\x00\x00\x02\x00" + b"\x00\x00\x00\x00mdat" + b"\x21" * 32


class _Store:
    """`HeaderRepairStore` 的替身：记下被要求修的是谁。"""

    def __init__(self, existing=(), failing=()):
        self.existing = set(existing)
        self.failing = set(failing)
        self.repaired: list[int] = []
        self.resolver = SimpleNamespace(ffmpeg=lambda: SimpleNamespace(path=Path("ffmpeg")),
                                        ffprobe=lambda: SimpleNamespace(path=Path("ffprobe")))

    def lookup(self, asset_id, source):
        return "头" if asset_id in self.existing else None

    def repair_now(self, asset_id, source):
        self.repaired.append(asset_id)
        if asset_id in self.failing:
            return False
        self.existing.add(asset_id)
        return True


class _Transcodes:
    """`TranscodeService` 的替身：只回答「是不是缺 ctts 的那一类」。"""

    def __init__(self, affected=()):
        self.affected = set(affected)
        self.asked: list[str] = []

    def decode_order_timestamps(self, source):
        self.asked.append(Path(source).name)
        return Path(source).name in self.affected


class MediaRepairRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.media = self.root / "media"
        self.media.mkdir()
        self.db_path = str(fresh_ledger(self.root))
        rows = [
            (1, "115", "B:" + SEP + "seldom.mp4", "seldom.mp4", 0, 60),
            (2, "115", "B:" + SEP + "favourite.mp4", "favourite.mp4", 9, 60),
            (3, "115", "B:" + SEP + "fine.mp4", "fine.mp4", 1, 60),
            (4, "pikpak", "A:" + SEP + "metered.mp4", "metered.mp4", 5, None),
            (5, "115", "B:" + SEP + "photo.jpg", "photo.jpg", 3, None),
            (6, "115", "B:" + SEP + "cut.mp4", "cut.mp4", 2, -1),
        ]
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,play_count,duration,hash_kind,hash) "
            "VALUES(?,?,?,?,'video',10,?,?,'sha1','old')", rows)
        connection.commit()
        connection.close()
        for *_, name, _, _ in rows:
            (self.media / name).write_bytes(CUT_SHORT if name == "cut.mp4" else b"x" * 16)
        self.contract = rm_web.WebContract(Path(self.db_path), transcode_root=self.root / "cache",
                                           tools_root=self.root / "tools")
        self.store = _Store()
        self.transcodes = _Transcodes({"favourite.mp4", "seldom.mp4"})
        self.contract.header_repairs = self.store
        self.contract.transcode_service = self.transcodes
        config = SimpleNamespace(locations={"115": ("B:" + SEP,), "pikpak": ("A:" + SEP,)},
                                 library_names={"B:" + SEP: "网盘", "A:" + SEP: "PikPak"})
        for patch in (
            mock.patch.object(rm_repair, "translate_ledger_path",
                              lambda raw: self.media / PureWindowsPath(str(raw)).name),
            mock.patch.object(rm_repair.settings_file, "active", lambda: config),
            mock.patch.object(rm_repair.mp4recover, "untrunc_path", lambda root: Path("untrunc")),
            mock.patch.object(rm_repair.media_probe, "measure",
                              lambda ffprobe, asset_id, path: (95.0, 720, 406, "h264", 29.97, None,
                                                               "短", "横屏", "低画质", asset_id)),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.recovered: list[tuple[str, list[str]]] = []

    def recover(self, source, references, *, work, **tools):
        """untrunc 的替身：记下拿哪些参照修了谁，产出一份「修好的」文件。"""
        self.recovered.append((Path(source).name, [Path(path).name for path in references]))
        work.mkdir(parents=True, exist_ok=True)
        output = work / "recovered-0.mp4"
        output.write_bytes(b"repaired")
        return Recovery(output, references[0], 95.0, 1.0)

    def run_once(self, library="网盘", **body) -> dict:
        """跑完一轮再返回状态：后台任务在测试里没有别的事要抢。"""
        with mock.patch.object(rm_repair.mp4recover, "recover", self.recover):
            started = rm_repair.w_media_repair(self.contract, {"restart": True, "library": library, **body})
            thread = self.contract.media_repair_job.thread
            if thread is not None:
                thread.join(timeout=30)
        return rm_repair.q_media_repair(self.contract) or started

    def ledger_row(self, asset_id: int):
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                "SELECT size,duration,width,hash,hash_kind FROM asset WHERE id=?", (asset_id,)).fetchone()
        finally:
            connection.close()

    def test_only_the_files_that_are_actually_broken_get_repaired(self):
        state = self.run_once()

        self.assertEqual(self.store.repaired, [2, 1])
        self.assertEqual([name for name, _ in self.recovered], ["cut.mp4"])
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["library"], "网盘")
        self.assertEqual((state["found"], state["repaired"], state["failed"]), (3, 3, 0))

    def test_the_most_played_ones_are_repaired_first(self):
        """一轮要跑几个钟头，中途停下也该已经换来体感：常看的先修好。"""
        self.run_once()

        self.assertEqual(self.store.repaired[0], 2, "放得最多的那部没排在最前面")

    def test_mounted_cloud_sources_replace_recovered_files(self):
        for location in ('guangya', 'onedrive', 'mounted'):
            with self.subTest(location=location):
                source = self.media / f'{location}.mp4'
                source.write_bytes(CUT_SHORT)
                raw = f'B:\\{source.name}'
                with self.contract.write_transaction() as connection:
                    connection.execute("UPDATE asset SET location=? WHERE id IN (1,2,3,6)", (location,))
                    connection.execute("UPDATE asset SET path=?,name=? WHERE id=6", (raw, source.name))
                with mock.patch.object(rm_repair.mp4recover, 'recover', self.recover):
                    self.assertTrue(rm_repair._recover(self.contract, 6, raw,
                        (Path('untrunc'), Path('ffmpeg'), Path('ffprobe'))))
                self.assertEqual(source.read_bytes(), b'repaired')

    def test_a_library_has_to_be_named(self):
        with self.assertRaisesRegex(ValueError, "先选一个媒体库"):
            rm_repair.w_media_repair(self.contract, {})
        with self.assertRaisesRegex(ValueError, "没有这个媒体库"):
            rm_repair.w_media_repair(self.contract, {"library": "不存在"})

    def test_other_libraries_are_left_alone(self):
        """PikPak 上一部片要整个拉下来才修得了，不点名那个库就不能碰。"""
        self.run_once()

        self.assertNotIn("metered.mp4", self.transcodes.asked)

    def test_naming_the_metered_library_reaches_it_and_nothing_else(self):
        self.transcodes.affected.add("metered.mp4")
        self.transcodes.asked.clear()
        self.run_once("PikPak")

        self.assertEqual(self.transcodes.asked, ["metered.mp4"])
        self.assertEqual(self.store.repaired, [4])

    def test_a_file_missing_its_index_is_rebuilt_from_its_neighbours_and_swapped_in(self):
        self.run_once()

        name, references = self.recovered[0]
        self.assertEqual(set(references), {"seldom.mp4", "favourite.mp4", "fine.mp4"})
        self.assertEqual((self.media / "cut.mp4").read_bytes(), b"repaired")
        self.assertEqual((self.media / ".cut.mp4.peach-original").read_bytes(), CUT_SHORT)

    def test_the_ledger_follows_the_swapped_file(self):
        """时长和画面重新探，大小照扫描的口径刷新；115 的 SHA1 算的是旧内容，清掉。"""
        self.run_once()

        self.assertEqual(self.ledger_row(6), (len(b"repaired"), 95.0, 720, None, None))
        self.assertEqual(self.ledger_row(3)[3], "old")

    def test_without_untrunc_the_file_waits_for_it_instead_of_being_written_off(self):
        with mock.patch.object(rm_repair.mp4recover, "untrunc_path", lambda root: None):
            first = self.run_once()
        second = self.run_once()

        self.assertEqual((first["failed"], first["missing_tool"]), (1, 1))
        self.assertEqual((self.media / "cut.mp4").read_bytes(), b"repaired")
        self.assertEqual(second["repaired"], 1)

    def test_a_file_no_reference_can_rebuild_is_not_tried_again(self):
        def hopeless(source, references, **kwargs):
            self.recovered.append((Path(source).name, []))
            raise RepairUnavailable("没有一个参照切得出能解码的片子")

        self.recover = hopeless
        first = self.run_once()
        self.run_once()

        self.assertEqual((first["failed"], first["missing_tool"]), (1, 0))
        self.assertEqual(len(self.recovered), 1)
        self.assertEqual((self.media / "cut.mp4").read_bytes(), CUT_SHORT)

    def test_a_file_that_already_has_a_header_is_not_probed_again(self):
        self.store.existing.add(2)
        self.run_once()

        self.assertNotIn("favourite.mp4", self.transcodes.asked)
        self.assertEqual(self.store.repaired, [1])

    def test_a_clean_verdict_is_remembered_for_the_next_run(self):
        """判一遍要把整个库的头读一遍，115 上那是几千次网络往返，不能每轮重来。"""
        self.run_once()
        self.transcodes.asked.clear()
        self.run_once()

        self.assertNotIn("fine.mp4", self.transcodes.asked)
        self.assertTrue((self.root / "cache" / rm_repair.SCAN_CACHE_NAME).exists())

    def test_a_file_that_cannot_be_repaired_is_not_tried_again(self):
        self.store.failing.add(2)
        first = self.run_once()
        self.run_once()

        self.assertEqual((first["repaired"], first["failed"]), (2, 1))
        self.assertEqual(self.store.repaired.count(2), 1)

    def test_a_changed_file_is_judged_again(self):
        """结论跟着文件的大小和改动时间走：换了内容，上一轮的结论就不作数。"""
        self.run_once()
        (self.media / "fine.mp4").write_bytes(b"y" * 32)
        self.transcodes.asked.clear()
        self.run_once()

        self.assertIn("fine.mp4", self.transcodes.asked)

    def test_images_are_never_in_the_list(self):
        self.run_once()

        self.assertNotIn("photo.jpg", self.transcodes.asked)

    def test_an_instance_without_the_playback_components_says_so(self):
        self.contract.header_repairs = None

        with self.assertRaises(ValueError):
            rm_repair.w_media_repair(self.contract, {"library": "网盘"})

    def test_an_idle_instance_reports_idle_instead_of_nothing(self):
        state = rm_repair.q_media_repair(self.contract)

        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["total"], 0)


if __name__ == "__main__":
    unittest.main()
