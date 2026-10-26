"""自动更新的持久设置、跨服务互斥和安装边界。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from filelock import FileLock
from peach.automatic_updates import AutomaticUpdates


class AutomaticUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.updates = AutomaticUpdates(self.root, available=True)

    def test_settings_roundtrip_disable_and_validate_installation(self):
        self.assertEqual(self.updates.snapshot()["mode"], "off")
        self.updates.save({"mode": "check", "interval_hours": 6})
        self.assertEqual(AutomaticUpdates(self.root, available=True).read()["mode"], "check")
        for body in ({"mode": [], "interval_hours": 24}, {"mode": "check", "interval_hours": True},
                     {"mode": "check", "interval_hours": 1}):
            with self.assertRaises(ValueError):
                self.updates.save(body)
        with patch("peach.distribution.standalone", return_value=False), self.assertRaises(ValueError):
            self.updates.save({"mode": "download", "interval_hours": 24})
        self.updates.save({"mode": "off", "interval_hours": 24})
        with patch("peach.release_updates.check") as check:
            self.updates.tick()
            check.assert_not_called()

    def test_two_services_share_due_time_and_respect_file_lock(self):
        self.updates.save({"mode": "check", "interval_hours": 24})
        other = AutomaticUpdates(self.root, available=True)
        with patch("peach.release_updates.check", return_value={"state": "current"}) as check, patch(
                "peach.automatic_updates.time.time", return_value=200000):
            with FileLock(str(self.updates.path) + ".lock", timeout=0):
                other.tick()
                check.assert_not_called()
            self.updates.tick()
            other.tick()
            check.assert_called_once()
        with patch("peach.release_updates.check", return_value={"state": "error"}) as check, patch(
                "peach.automatic_updates.time.time", return_value=300000):
            other.tick()
            self.updates.tick()
            check.assert_called_once()
        self.assertEqual(self.updates.read()["result"]["state"], "error")

    def test_download_prepares_once_and_never_requests_restart(self):
        with patch("peach.distribution.standalone", return_value=True), patch(
                "peach.release_updates.check", return_value={"state": "available"}), patch(
                "peach.standalone_update.public", return_value={"state": "idle"}), patch(
                "peach.standalone_update.start") as start, patch("peach.standalone_update.request_restart") as restart:
            self.updates.save({"mode": "download", "interval_hours": 24})
            self.updates.tick()
            self.updates.tick()
            start.assert_called_once()
            restart.assert_not_called()
        for state in ("ready", "downloading", "installing"):
            with patch("peach.distribution.standalone", return_value=True), patch(
                    "peach.release_updates.check", return_value={"state": "available"}), patch(
                    "peach.standalone_update.public", return_value={"state": state}), patch(
                    "peach.standalone_update.start") as start:
                self.updates.write({"mode": "download", "interval_hours": 24})
                self.updates.tick()
                start.assert_not_called()

    def test_unmanaged_and_corrupt_settings_fail_closed(self):
        other = AutomaticUpdates(self.root, available=False)
        with self.assertRaises(ValueError):
            other.save({"mode": "check", "interval_hours": 24})
        self.updates.path.write_text('[]', encoding="utf-8")
        self.assertIn("error", self.updates.snapshot())
        with patch("peach.release_updates.check") as check:
            self.updates.tick()
            other.tick()
            check.assert_not_called()
