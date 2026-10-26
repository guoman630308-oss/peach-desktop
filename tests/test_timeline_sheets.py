"""时间轴预览的接触印相：格子几何、整套生成、队列取舍与整库跑法。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from peach import timeline_sheets
from peach.jobs import DiskSpaceDenied
from support.ledger import fresh_ledger


class SheetGeometryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="timeline-geometry-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_sheets_of_one_video_live_under_one_bucket(self):
        """产物按 id 末两位分桶。本机三千部一层平铺就是三千个目录挤在一处。"""
        self.assertEqual(timeline_sheets.sheet_dir(self.root, 1234),
                         self.root / "34" / "1234")
        self.assertEqual(timeline_sheets.sheet_path(self.root, 1234, 7).name, "007.jpg")

    def test_a_video_too_short_to_scan_gets_no_sheet(self):
        """两帧以下没有可扫的东西，一张图也不该出。"""
        self.assertEqual(timeline_sheets.frame_count(15, 10), 1)
        self.assertEqual(timeline_sheets.frame_count(0, 10), 0)
        self.assertEqual(timeline_sheets.frame_count(3600, 10), 360)

    def test_a_missing_sheet_file_sends_the_video_back_through(self):
        """只认 meta 不看图的话，手工清过产物目录之后整批被当成做好的跳过，
        页面上则是一片取不到的图。"""
        directory = timeline_sheets.sheet_dir(self.root, 5)
        directory.mkdir(parents=True)
        (directory / "meta.json").write_text(
            json.dumps({"interval": 10, "frames": 120, "sheets": 2}), encoding="utf-8")
        (directory / "000.jpg").write_bytes(b"x")
        self.assertFalse(timeline_sheets.is_current(self.root, 5, 10))
        (directory / "001.jpg").write_bytes(b"x")
        self.assertTrue(timeline_sheets.is_current(self.root, 5, 10))
        self.assertFalse(timeline_sheets.is_current(self.root, 5, 30))

    def test_a_frame_that_would_not_decode_keeps_its_own_slot(self):
        """抽失败的格子用邻近那一帧补上。少一帧让后面全体前移一格，图还是满的，
        位置整体错开，而错开多少取决于哪几帧解不出来。"""
        for index in (0, 3):
            (self.root / f"s{index:03d}.jpg").write_bytes(bytes([index]))
        self.assertEqual(timeline_sheets._fill_gaps(self.root, 4), 4)
        self.assertEqual((self.root / "s001.jpg").read_bytes(), bytes([0]))
        self.assertEqual((self.root / "s002.jpg").read_bytes(), bytes([3]))

    def test_nothing_decoded_means_no_sheet_at_all(self):
        self.assertEqual(timeline_sheets._fill_gaps(self.root, 3), 0)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="timeline-build-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.stamps: list[float] = []

    def _capture(self, _ffmpeg, _path, timestamp, destination, **_kw):
        self.stamps.append(timestamp)
        destination.write_bytes(b"frame")
        return True

    def _tile(self, _ffmpeg, _pattern, _columns, _rows, destination, **_kw):
        destination.write_bytes(b"sheet")
        return True

    def _build(self, duration, interval, **kwargs):
        with patch.object(timeline_sheets, "capture_with_retry", self._capture), \
             patch.object(timeline_sheets, "tile_frames", self._tile):
            return timeline_sheets.build("ffmpeg", "movie.mp4", duration, self.root,
                                         9, interval, **kwargs)

    def test_the_frame_number_times_the_interval_is_where_it_came_from(self):
        """前端按同一条式子从时间反算格子。采样点只被 `EDGE_MARGIN` 整体推进一点，
        不改这条对应关系的斜率。"""
        meta = self._build(100, 10)
        self.assertEqual(meta["frames"], 10)
        self.assertEqual(meta["interval"], 10)
        self.assertAlmostEqual(self.stamps[0], 1.0)
        self.assertAlmostEqual(self.stamps[3] - self.stamps[2], 10.0)

    def test_a_long_video_is_split_into_full_sheets_plus_a_remainder(self):
        meta = self._build(10 * 1000, 10)
        self.assertEqual(meta["sheets"], 10)
        self.assertEqual(meta["frames"], 990)
        self.assertTrue(timeline_sheets.sheet_path(self.root, 9, 9).is_file())

    def test_stopping_midway_leaves_no_index_behind(self):
        """半路停下不写 meta。留一份「说有 700 帧、实际只有 300 帧」的索引，
        下一趟会把它当成做好的跳过。"""
        calls = {"n": 0}

        def active():
            calls["n"] += 1
            return calls["n"] < 5

        self.assertEqual(self._build(1000, 10, active=active), {})
        self.assertIsNone(timeline_sheets.read_meta(self.root, 9))

    def test_switching_density_does_not_leave_the_old_sheets_behind(self):
        """换档位是整套重做，新的一套通常比旧的少几张。多出来的那几张 `is_current`
        又不看，只覆盖不清理就永远没人删。"""
        self._build(10 * 1000, 10)
        self._build(400, 30)
        self.assertFalse(timeline_sheets.sheet_path(self.root, 9, 1).exists())
        self.assertTrue(timeline_sheets.sheet_path(self.root, 9, 0).is_file())

    def test_a_sheet_that_will_not_tile_produces_nothing(self):
        with patch.object(timeline_sheets, "capture_with_retry", self._capture), \
             patch.object(timeline_sheets, "tile_frames", lambda *a, **k: False):
            self.assertEqual(timeline_sheets.build("ffmpeg", "movie.mp4", 100, self.root,
                                                   9, 10), {})
        self.assertFalse(timeline_sheets.sheet_path(self.root, 9, 0).exists())


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="timeline-queue-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.db = fresh_ledger(self.root)
        rows = [
            (1, "local", r"R:\a.mp4", 3600, "2026-09-01", None),
            (2, "pikpak", r"R:\b.mp4", 3600, "2026-09-02", None),
            (3, "local", r"R:\c.mp4", 3600, None, None),
            (4, "local", r"R:\d.mp4", 5, "2026-09-03", None),
            (5, "local", r"R:\e.mp4", 3600, "2026-09-04", "trash"),
            (6, "local", r"R:\f.mp4", 3600, "2026-09-05", None),
        ]
        connection = sqlite3.connect(self.db)
        with connection:
            connection.executemany(
                "INSERT INTO asset(id,medium,location,path,duration,last_played,disposal) "
                "VALUES(?,'video',?,?,?,?,?)", rows)
        connection.close()

    def test_the_queue_is_local_only_and_starts_with_what_was_played_last(self):
        """这条链随时会被磁盘闸门或用户按停，跑到哪算哪，所以先做的必须是最可能被
        扫到的那些。网盘上的每抽一帧都要回源拉一次，不进队列。"""
        self.assertEqual([row[0] for row in timeline_sheets.pending(self.db, 10)],
                         [6, 1, 3])

    def test_the_queue_can_be_cut_short(self):
        self.assertEqual([row[0] for row in timeline_sheets.pending(self.db, 10, limit=1)],
                         [6])


class GenerateLibraryTests(unittest.TestCase):
    def test_resume_keeps_counters_and_does_not_build_committed_assets(self):
        saved = dict(completed_assets=[1], done=1, made=1, skipped=0, failed=0)
        built = []
        with patch.object(timeline_sheets, 'is_current', side_effect=lambda root, asset_id, interval: asset_id == 1), \
                patch.object(timeline_sheets, 'build', side_effect=lambda ffmpeg, path, duration, root, asset_id, interval, **kwargs:
                             built.append(asset_id) or True):
            state = self._run(resume_state=saved)
        self.assertEqual(built, [2])
        self.assertEqual((state['made'], state['done'], state['total']), (2, 3, 3))

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="timeline-run-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.db = fresh_ledger(self.root)
        self.media = self.root / "media"
        self.media.mkdir()
        connection = sqlite3.connect(self.db)
        with connection:
            for index in (1, 2, 3):
                path = self.media / f"{index}.mp4"
                if index != 3:
                    path.write_bytes(b"movie")
                connection.execute(
                    "INSERT INTO asset(id,medium,location,path,duration,last_played) "
                    "VALUES(?,'video','local',?,3600,?)",
                    (index, str(path), f"2026-09-0{4 - index}"))
        connection.close()
        self.sheets = self.root / "sheets"

    def _run(self, **kwargs):
        with patch.object(timeline_sheets, "resolve_case_insensitive", lambda path: path):
            return timeline_sheets.generate_library(
                self.db, self.sheets, 10, ffmpeg="ffmpeg", **kwargs)

    def test_a_video_that_will_not_open_does_not_take_the_queue_down_with_it(self):
        """源坏了、时长记错、编码器不认，都是它自己的事；队列后面还有几千部。"""
        with patch.object(timeline_sheets, "build", lambda *a, **k: {"frames": 1}):
            state = self._run()
        self.assertEqual((state["total"], state["made"], state["failed"]), (3, 2, 1))
        self.assertEqual(state["stopped"], "")

    def test_a_video_already_done_is_counted_as_skipped(self):
        with patch.object(timeline_sheets, "is_current", lambda *a: True):
            state = self._run()
        self.assertEqual((state["skipped"], state["made"]), (3, 0))

    def test_a_full_disk_stops_the_run_and_says_so(self):
        """磁盘触线说明整台机器写不下了，立刻停手并把原因带出去，不能跑成「完成」。"""
        guard = MagicMock()
        guard.check.side_effect = DiskSpaceDenied("系统盘只剩 3 GB")
        state = self._run(guard=guard)
        self.assertEqual(state["stopped"], "系统盘只剩 3 GB")
        self.assertEqual(state["done"], 0)

    def test_pressing_stop_is_recorded_as_the_user_stopping_it(self):
        state = self._run(active=lambda: False)
        self.assertEqual(state["stopped"], timeline_sheets.STOPPED_BY_USER)


if __name__ == "__main__":
    unittest.main()
