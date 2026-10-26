"""任务中心服务层：互斥、CAS、租约恢复与保留条数。"""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from peach.migrations import upgrade
from peach.repository import LedgerDatabase
from peach.task_runs import (
    TaskRunConflict,
    TaskRunStore,
    stamp,
    task_label,
)


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class TaskRunStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)
        self.database = LedgerDatabase(self.db)
        self.store = TaskRunStore(self.database, host="test-host")

    def test_a_started_run_is_running_and_readable(self):
        run = self.store.start("demo", trigger="manual", total=10, label="准备")
        self.assertIsNotNone(run)
        self.assertEqual(run.status, "running")
        self.assertEqual(run.trigger, "manual")
        self.assertEqual(run.progress_total, 10)
        self.assertEqual(run.pid, os.getpid())
        self.assertEqual(self.store.get(run.id).progress_label, "准备")

    def test_missing_empty_or_corrupt_ledgers_have_no_available_task_center(self):
        self.assertTrue(self.store.available())
        for state, content in (("missing", None), ("empty", b""), ("corrupt", b"not a sqlite database")):
            with self.subTest(state=state):
                path = self.db.parent / f"{state}.db"
                if content is not None:
                    path.write_bytes(content)
                store = TaskRunStore(LedgerDatabase(path), host="test-host")
                self.assertFalse(store.available())
                self.assertEqual(path.read_bytes() if path.exists() else None, content)

    def test_scheduled_conflict_is_skipped_and_says_who_blocked_it(self):
        first = self.store.start("demo", trigger="manual", mutex_key="demo")
        self.assertIsNone(self.store.start(
            "demo", trigger="scheduled", mutex_key="demo", conflict="skip"))
        skipped = self.store.query(status="cancelled")[0]
        self.assertEqual(skipped.trigger, "scheduled")
        self.assertEqual(skipped.result_summary["blocked_by"], first.id)
        self.assertIn(str(first.id), skipped.error)
        # 跳过的那一轮从没开跑，所以没有开始时刻可报。
        self.assertIsNone(skipped.started_at)

    def test_manual_conflict_raises_with_the_blocking_run_id(self):
        first = self.store.start("demo", trigger="scheduled", mutex_key="demo")
        with self.assertRaises(TaskRunConflict) as caught:
            self.store.start("demo", trigger="manual", mutex_key="demo",
                             conflict="raise")
        self.assertEqual(caught.exception.blocking_run_id, first.id)
        # 抛出的那次不留记录：用户当场看到了 409，再记一条只会把历史撑肿。
        self.assertEqual(self.store.query(status="cancelled"), [])

    def test_a_finished_run_releases_the_mutex_key(self):
        first = self.store.start("demo", trigger="manual", mutex_key="demo")
        self.store.finish(first.id, "succeeded")
        second = self.store.start("demo", trigger="manual", mutex_key="demo")
        self.assertIsNotNone(second)
        self.assertNotEqual(second.id, first.id)

    def test_runs_without_a_mutex_key_do_not_block_each_other(self):
        self.assertIsNotNone(self.store.start("demo", trigger="manual"))
        self.assertIsNotNone(self.store.start("demo", trigger="manual"))

    def test_finishing_twice_only_lands_once(self):
        run = self.store.start("demo", trigger="manual")
        self.assertTrue(self.store.finish(run.id, "succeeded", summary={"changed": 3}))
        self.assertFalse(self.store.finish(run.id, "failed", error="迟到的结算"))
        settled = self.store.get(run.id)
        self.assertEqual(settled.status, "succeeded")
        self.assertEqual(settled.result_summary, {"changed": 3})
        self.assertEqual(settled.error, "")

    def test_progress_stops_at_the_terminal_state(self):
        run = self.store.start("demo", trigger="manual")
        self.assertTrue(self.store.progress(run.id, current=4, total=9, throttle=0))
        self.store.finish(run.id, "succeeded")
        self.assertFalse(self.store.progress(run.id, current=9, throttle=0))
        self.assertEqual(self.store.get(run.id).progress_current, 4)

    def test_progress_is_throttled_by_wall_clock(self):
        run = self.store.start("demo", trigger="manual")
        self.assertTrue(self.store.progress(run.id, current=1, throttle=0))
        self.assertFalse(self.store.progress(run.id, current=2, throttle=60))
        self.assertEqual(self.store.get(run.id).progress_current, 1)

    def hold_write_lock(self) -> sqlite3.Connection:
        """另一个写入者占住账本的写锁，直到调用方 ROLLBACK。"""
        blocker = sqlite3.connect(self.db, isolation_level=None)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN IMMEDIATE")
        return blocker

    def test_progress_gives_up_quietly_while_another_writer_holds_the_ledger(self):
        run = self.store.start("demo", trigger="manual")
        blocker = self.hold_write_lock()
        with mock.patch("peach.task_runs.PROGRESS_LOCK_TIMEOUT", 0.05), \
                self.assertLogs("peach.task_runs", "WARNING") as logs:
            self.assertFalse(self.store.progress(run.id, current=3, throttle=0))
            self.assertFalse(self.store.heartbeat(run.id, throttle=0))
        # 一段连续失败只记一条，不让每两秒一次的进度把错误日志刷满。
        self.assertEqual(len(logs.records), 1)
        blocker.execute("ROLLBACK")
        self.assertTrue(self.store.progress(run.id, current=4, throttle=0))
        self.assertEqual(self.store.get(run.id).progress_current, 4)

    def test_progress_does_not_queue_behind_a_writer_in_the_same_process(self):
        run = self.store.start("demo", trigger="manual")
        with mock.patch("peach.task_runs.PROGRESS_LOCK_TIMEOUT", 0.05), \
                self.assertLogs("peach.task_runs", "WARNING"), self.database.write_lock:
            self.assertFalse(self.store.progress(run.id, current=3, throttle=0))
        self.assertTrue(self.store.progress(run.id, current=3, throttle=0))

    def test_finish_retries_a_locked_ledger_and_then_lands(self):
        run = self.store.start("demo", trigger="manual", mutex_key="demo")
        real = self.store._update_active
        attempts = []

        def locked_once(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(*args, **kwargs)

        self.store.finish_retry_delays = (0,)
        with mock.patch.object(self.store, "_update_active", locked_once), \
                self.assertLogs("peach.task_runs", "WARNING"):
            self.assertTrue(self.store.finish(run.id, "succeeded"))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.store.get(run.id).status, "succeeded")
        # 结算落地了，互斥键也就放开了，下一轮开得起来。
        self.assertIsNotNone(self.store.start("demo", trigger="manual", mutex_key="demo"))

    def test_finish_raises_once_its_retries_are_spent(self):
        run = self.store.start("demo", trigger="manual")
        self.store.finish_retry_delays = (0, 0)
        locked = mock.Mock(side_effect=sqlite3.OperationalError("database is locked"))
        with mock.patch.object(self.store, "_update_active", locked), \
                self.assertLogs("peach.task_runs", "WARNING"), \
                self.assertRaises(sqlite3.OperationalError):
            self.store.finish(run.id, "succeeded")
        self.assertEqual(locked.call_count, 3)
        self.assertEqual(self.store.get(run.id).status, "running")

    def test_an_unknown_status_or_trigger_is_refused_before_any_write(self):
        with self.assertRaises(ValueError):
            self.store.start("demo", trigger="webhook")
        with self.assertRaises(ValueError):
            self.store.finish(1, "done")
        self.assertEqual(self.store.query(), [])

    def test_recovery_marks_runs_of_a_dead_process_as_interrupted(self):
        run = self.store.start("demo", trigger="manual")
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("UPDATE task_run SET pid=? WHERE id=?", (4294967294, run.id))
        self.assertEqual(self.store.recover_interrupted(), [run.id])
        recovered = self.store.get(run.id)
        self.assertEqual(recovered.status, "interrupted")
        self.assertIsNotNone(recovered.finished_at)
        # 打断不是失败：任务没有得出坏结果，只是没跑完。
        self.assertNotEqual(recovered.status, "failed")

    def test_recovery_leaves_a_run_of_a_live_process_alone(self):
        run = self.store.start("demo", trigger="manual")
        with mock.patch("peach.task_runs.process_alive", return_value=True):
            with self.database.write_transaction(notify=False) as connection:
                connection.execute("UPDATE task_run SET pid=? WHERE id=?",
                                   (os.getpid() + 1, run.id))
            self.assertEqual(self.store.recover_interrupted(), [])
        self.assertEqual(self.store.get(run.id).status, "running")

    def test_recovery_of_another_machine_waits_for_the_lease_to_expire(self):
        run = self.store.start("demo", trigger="manual")
        stale = stamp(datetime.now(timezone.utc) - timedelta(seconds=30))
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("UPDATE task_run SET host='other',heartbeat_at=? WHERE id=?",
                               (stale, run.id))
        self.assertEqual(self.store.recover_interrupted(stale_after=300), [])
        self.assertEqual(self.store.recover_interrupted(stale_after=10), [run.id])

    def test_prune_keeps_the_most_recent_finished_runs_per_task(self):
        for _ in range(5):
            run = self.store.start("demo", trigger="manual")
            self.store.finish(run.id, "succeeded")
        other = self.store.start("other", trigger="cli")
        self.store.finish(other.id, "failed", error="坏了")
        live = self.store.start("demo", trigger="manual")
        self.assertEqual(self.store.prune("demo", keep=2), 3)
        remaining = [row.id for row in self.store.query(task_key="demo")]
        self.assertIn(live.id, remaining)
        self.assertEqual(len(remaining), 3)
        self.assertEqual(len(self.store.query(task_key="other")), 1)

    def test_query_filters_by_status_group_and_task(self):
        first = self.store.start("demo", trigger="manual")
        second = self.store.start("other", trigger="cli")
        self.store.finish(second.id, "failed", error="坏了")
        self.assertEqual([row.id for row in self.store.query(status="active")], [first.id])
        self.assertEqual([row.id for row in self.store.query(status="finished")], [second.id])
        self.assertEqual([row.id for row in self.store.query(task_key="other")], [second.id])
        with self.assertRaises(ValueError):
            self.store.query(status="done")

    def test_a_read_only_end_records_nothing_but_still_reads(self):
        existing = self.store.start("demo", trigger="manual")
        reader = TaskRunStore(self.database, enabled=False, host="reader")
        self.assertIsNone(reader.start("demo", trigger="manual"))
        self.assertFalse(reader.finish(existing.id, "succeeded"))
        self.assertEqual(reader.recover_interrupted(), [])
        self.assertEqual(reader.prune("demo", keep=0), 0)
        self.assertEqual([row.id for row in reader.query()], [existing.id])
        self.assertEqual(self.store.get(existing.id).status, "running")

    def test_track_settles_on_success_and_on_failure(self):
        with self.store.track("demo", trigger="cli") as handle:
            handle.progress(2, 4, "一半", throttle=0)
        finished = self.store.get(handle.run_id)
        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(finished.progress_current, 2)
        with self.assertRaises(RuntimeError):
            with self.store.track("demo", trigger="cli") as failing:
                raise RuntimeError("来源离线")
        broken = self.store.get(failing.run_id)
        self.assertEqual(broken.status, "failed")
        self.assertIn("来源离线", broken.error)

    def test_track_hands_back_an_inert_handle_when_the_mutex_is_taken(self):
        self.store.start("demo", trigger="manual", mutex_key="demo")
        with self.store.track("demo", trigger="scheduled", mutex_key="demo") as handle:
            self.assertIsNone(handle.run_id)
            handle.progress(1, 2, throttle=0)
        self.assertEqual(len(self.store.query(status="running")), 1)

    def test_payload_reports_elapsed_time_and_a_readable_task_name(self):
        run = self.store.start("follow-check", trigger="scheduled")
        payload = self.store.get(run.id).payload()
        self.assertEqual(payload["task_label"], "追更检查")
        self.assertGreaterEqual(payload["elapsed_seconds"], 0)
        self.assertEqual(task_label("nothing-registered"), "nothing-registered")


if __name__ == "__main__":
    unittest.main()
