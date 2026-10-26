import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from peach.follow_scheduler import (
    MAX_BACKOFF_FACTOR,
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    FollowScheduleConfig,
    FollowScheduleStore,
    FollowUpdateScheduler,
)


class FollowScheduleStoreTests(unittest.TestCase):
    def test_defaults_are_enabled_hourly_and_updates_are_atomic(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FollowScheduleStore(Path(temporary))
            self.assertEqual(store.load(), FollowScheduleConfig(True, 60))
            store.save(FollowScheduleConfig(False, 180))
            self.assertEqual(store.load(), FollowScheduleConfig(False, 180))

    def test_unbounded_intervals_are_rejected(self):
        with self.assertRaises(ValueError):
            FollowScheduleStore.validate(True, 14)
        with self.assertRaises(ValueError):
            FollowScheduleStore.validate(True, 10081)
        with self.assertRaises(ValueError):
            FollowScheduleStore.validate("false", 60)


class FollowUpdateSchedulerTests(unittest.TestCase):
    def test_installation_reservation_skips_the_tick_without_recording_a_failure(self):
        from peach.update_tasks import UpdateBusy
        def blocked():
            raise UpdateBusy('正在安装')
        with tempfile.TemporaryDirectory() as temporary:
            scheduler = FollowUpdateScheduler(Path(temporary), blocked, available=True)
            with self.assertNoLogs('peach', level='ERROR'):
                scheduler._run()
            status = scheduler.status()
            self.assertEqual(status['consecutive_failures'], 0)
            self.assertIsNone(status['last_error'])
            self.assertIsNotNone(status['last_skipped_at'])

    def test_interval_job_waits_one_interval_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            scheduler = FollowUpdateScheduler(Path(temporary), lambda: {"ok": True}, available=True)
            self.addCleanup(scheduler.stop)
            scheduler.start()
            status = scheduler.status()
            self.assertTrue(status["enabled"])
            self.assertIsNotNone(status["next_run_at"])
            status = scheduler.update(enabled=False, interval_minutes=60)
            self.assertFalse(status["enabled"])
            self.assertIsNone(status["next_run_at"])

    def test_run_status_reports_checked_sources_and_additions(self):
        result = {"ok": True, "checked": 2, "results": [
            {"ok": True, "added": 3}, {"ok": False, "added": 0},
        ]}
        with tempfile.TemporaryDirectory() as temporary:
            scheduler = FollowUpdateScheduler(Path(temporary), lambda: result, available=True)
            scheduler._run()
            status = scheduler.status()
            self.assertEqual(status["last_checked"], 2)
            self.assertEqual(status["last_added"], 3)
            self.assertEqual(status["last_error"], "1 个来源检查失败")
            self.assertIsNotNone(status["last_finished_at"])

    def test_a_busy_lock_is_a_skip_not_a_failure(self):
        """互斥不是故障：手动检查正在跑的时候，自动这一轮什么也没做。

        这里抛异常的话，界面会把它显示成「上次失败」，还连带触发退避——真正的
        失败反而被这种噪声盖住。"""
        with tempfile.TemporaryDirectory() as temporary:
            scheduler = FollowUpdateScheduler(
                Path(temporary), lambda: {"ok": False, "busy": True,
                                          "checked": 0, "results": []},
                available=True)
            scheduler._run()
            status = scheduler.status()
            self.assertIsNone(status["last_error"])
            self.assertIsNotNone(status["last_skipped_at"])
            self.assertEqual(status["consecutive_failures"], 0)
            # 这一轮既没检查也没完成，所以不留「上次运行」的时间戳：
            # 留了界面就会把一次没发生的运行显示成「上次完成」。
            self.assertIsNone(status["last_started_at"])
            self.assertIsNone(status["last_finished_at"])
            self.assertEqual(status["last_checked"], 0)

    def test_consecutive_failures_back_off_and_a_good_run_resets_them(self):
        outcomes = [
            {"ok": True, "checked": 1, "results": [{"ok": False, "added": 0}]},
            RuntimeError("上游断了"),
            {"ok": True, "checked": 1, "results": [{"ok": True, "added": 2}]},
        ]

        def run_check():
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with tempfile.TemporaryDirectory() as temporary:
            scheduler = FollowUpdateScheduler(Path(temporary), run_check,
                                              available=True)
            interval = scheduler.config.interval_minutes
            # 第一次失败照原间隔重试：多数失败只是一次抖动。
            self.assertEqual(scheduler.backoff_minutes(1), interval)
            self.assertEqual(scheduler.backoff_minutes(2), interval * 2)
            self.assertEqual(scheduler.backoff_minutes(3), interval * 4)
            self.assertEqual(scheduler.backoff_minutes(99),
                             min(interval * MAX_BACKOFF_FACTOR,
                                 MAX_INTERVAL_MINUTES))
            scheduler._run()
            self.assertEqual(scheduler.status()["consecutive_failures"], 1)
            with self.assertLogs("peach.follow_scheduler", level="ERROR") as captured:
                scheduler._run()
            self.assertIn("上游断了", "\n".join(captured.output))
            status = scheduler.status()
            self.assertEqual(status["consecutive_failures"], 2)
            self.assertEqual(status["last_error"], "上游断了")
            scheduler._run()
            status = scheduler.status()
            self.assertEqual(status["consecutive_failures"], 0)
            self.assertIsNone(status["last_error"])
            self.assertEqual(status["last_added"], 2)

    def test_backoff_pushes_the_next_run_past_the_plain_interval(self):
        with tempfile.TemporaryDirectory() as temporary:
            scheduler = FollowUpdateScheduler(
                Path(temporary),
                lambda: {"ok": True, "checked": 1,
                         "results": [{"ok": False, "added": 0}]},
                available=True)
            self.addCleanup(scheduler.stop)
            scheduler.update(enabled=True, interval_minutes=MIN_INTERVAL_MINUTES)
            scheduler.start()
            planned = datetime.now(timezone.utc) + timedelta(
                minutes=MIN_INTERVAL_MINUTES)
            scheduler._run()
            scheduler._run()
            next_run = scheduler.status()["next_run_at"]
            self.assertIsNotNone(next_run)
            self.assertGreater(datetime.fromisoformat(next_run), planned,
                               "连续失败后应当推后，而不是照原间隔继续敲上游")

    def test_reader_never_starts_or_accepts_schedule_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            scheduler = FollowUpdateScheduler(Path(temporary), lambda: {}, available=False)
            scheduler.start()
            self.assertFalse(scheduler.status()["available"])
            with self.assertRaises(ValueError):
                scheduler.update(enabled=True, interval_minutes=60)
