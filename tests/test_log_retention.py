"""`peach.log_retention`：日志目录统一保留半年，按月切段，大小不设限。"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from peach import log_retention

NOW = datetime(2026, 9, 5, 12, 0, 0)


def touch(path: Path, when: datetime, content: bytes = b"line\n") -> None:
    path.write_bytes(content)
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name).resolve() / "logs"
        self.logs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_file_nobody_wrote_to_for_half_a_year_is_deleted_whole(self):
        touch(self.logs / "tray-http.out.log", NOW - timedelta(days=184))
        touch(self.logs / "tray-http.out.until-20260131.log", NOW - timedelta(days=200))
        actions = log_retention.sweep(self.logs, now=NOW)
        self.assertEqual(sorted(p.name for p in self.logs.iterdir()), [])
        self.assertEqual(sorted(actions), ["deleted tray-http.out.log",
                                           "deleted tray-http.out.until-20260131.log"])

    def test_a_file_last_written_in_an_earlier_month_becomes_a_dated_segment(self):
        touch(self.logs / "tray-https.err.log", datetime(2026, 8, 30, 23, 0), b"august\n")
        actions = log_retention.sweep(self.logs, now=NOW)
        self.assertEqual(actions, ["rotated tray-https.err.log -> tray-https.err.until-20260830.log"])
        self.assertFalse((self.logs / "tray-https.err.log").exists(), "下一次写入从空文件开始")
        self.assertEqual((self.logs / "tray-https.err.until-20260830.log").read_bytes(), b"august\n")

    def test_a_file_written_this_month_stays_put(self):
        touch(self.logs / "windows-source-sync.log", NOW - timedelta(days=3))
        self.assertEqual(log_retention.sweep(self.logs, now=NOW), [])
        self.assertTrue((self.logs / "windows-source-sync.log").exists())

    def test_segments_are_never_cut_again_until_they_expire(self):
        segment = self.logs / "tray-scan.out.until-20260731.log"
        touch(segment, datetime(2026, 7, 31, 8, 0))
        self.assertEqual(log_retention.sweep(self.logs, now=NOW), [])
        self.assertTrue(segment.exists())
        self.assertEqual(log_retention.sweep(self.logs, now=datetime(2027, 2, 1)),
                         ["deleted tray-scan.out.until-20260731.log"])

    def test_a_second_cut_on_the_same_day_appends_instead_of_overwriting(self):
        touch(self.logs / "tray-http.out.until-20260830.log", datetime(2026, 8, 30, 9, 0), b"first\n")
        touch(self.logs / "tray-http.out.log", datetime(2026, 8, 30, 22, 0), b"second\n")
        log_retention.sweep(self.logs, now=NOW)
        self.assertEqual((self.logs / "tray-http.out.until-20260830.log").read_bytes(), b"first\nsecond\n")
        self.assertFalse((self.logs / "tray-http.out.log").exists())

    def test_only_log_files_are_touched_and_a_missing_directory_is_fine(self):
        touch(self.logs / "peach-tray.lock", NOW - timedelta(days=400))
        touch(self.logs / "notes.txt", NOW - timedelta(days=400))
        self.assertEqual(log_retention.sweep(self.logs, now=NOW), [])
        self.assertEqual(log_retention.sweep(self.logs / "absent", now=NOW), [])

    def test_the_retention_is_half_a_year(self):
        self.assertEqual(log_retention.RETENTION, timedelta(days=183))


if __name__ == "__main__":
    unittest.main()
