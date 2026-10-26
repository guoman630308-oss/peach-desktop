from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from peach import tray_lifecycle as lifecycle

ROOT = Path(__file__).resolve().parents[1]


class TrayLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "session.json"
        self.log = self.root / "lifecycle.log"
        self.current = self.root / "current"
        self.current.write_text("one", encoding="utf-8")
        self.record = dict(session="one", pid=123, created=456, log=str(self.log),
                           data_root=str(self.root), cwd=str(self.root),
                           argv=[sys.executable, "-c", "pass"], history="[]", current=str(self.current))
        self.save()
        self.process = Mock(created=456)
        self.process.wait.return_value = 99
        self.child = Mock(pid=789)
        self.child.poll.return_value = 0
        self.spawn = Mock(return_value=self.child)

    def save(self):
        self.path.write_text(json.dumps(self.record), encoding="utf-8")

    def events(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def watch(self):
        return lifecycle.watch(self.path, open_process=lambda _: self.process,
                               popen=self.spawn, sleep=lambda _: None, now=lambda: 1000)

    def test_abnormal_exit_preserves_data_root_and_records_unknown_actor(self):
        self.assertEqual(self.watch(), 0)
        fields = self.spawn.call_args.kwargs
        self.assertEqual(fields["env"]["PEACH_DATA_ROOT"], str(self.root))
        self.assertEqual(json.loads(fields["env"][lifecycle.HISTORY_ENV]), [1000])
        failure = next(row for row in self.events() if row["event"] == "unexpected_exit")
        self.assertEqual((failure["exit_code"], failure["actor"]), (99, "unknown"))
        self.process.close.assert_called_once()
        self.assertFalse(self.path.exists())

    def test_explicit_stop_disarms_without_recovery(self):
        self.path.with_suffix(".stop").write_text("menu-exit")
        self.assertEqual(self.watch(), 0)
        self.spawn.assert_not_called()
        self.assertEqual(self.events()[-1]["event"], "watchdog_disarmed")

    def test_zero_exit_without_intent_is_also_recovered(self):
        self.process.wait.return_value = 0
        self.watch()
        self.spawn.assert_called_once()

    def test_reused_pid_is_rejected(self):
        self.process.created = 457
        self.assertEqual(self.watch(), 1)
        self.spawn.assert_not_called()
        self.assertEqual(self.events()[-1]["event"], "watchdog_error")

    def test_manual_start_supersedes_pending_recovery(self):
        self.current.write_text("manual-session")
        self.assertEqual(self.watch(), 0)
        self.spawn.assert_not_called()
        self.assertEqual(self.events()[-1]["event"], "recovery_superseded")

    def test_manual_start_during_delay_supersedes_recovery(self):
        self.assertEqual(lifecycle.watch(
            self.path, open_process=lambda _: self.process, popen=self.spawn,
            sleep=lambda _: self.current.write_text("manual-session"), now=lambda: 1000), 0)
        self.spawn.assert_not_called()

    def test_record_cleanup_failure_does_not_interrupt_explicit_quit(self):
        item = lifecycle.Lifecycle(self.root, self.root, self.root, ROOT)
        item.guard = Mock()
        with patch.object(Path, "unlink", side_effect=PermissionError("busy")), \
                self.assertLogs("peach.tray_lifecycle", level="WARNING"):
            item.stop("menu-exit")
        self.assertEqual(item.reason, "menu-exit")
        item.guard.wait.assert_called_once_with(timeout=5)

    def test_repeated_launch_failure_stops_at_three_attempts(self):
        self.spawn.side_effect = OSError("entry missing")
        self.assertEqual(self.watch(), 1)
        self.assertEqual(self.spawn.call_count, 3)
        self.assertEqual(self.events()[-1]["event"], "recovery_limit")

    def test_import_failures_share_the_attempt_budget(self):
        self.child.poll.return_value = 1
        self.record["history"] = "[990,995]"
        self.save()
        self.assertEqual(self.watch(), 1)
        self.spawn.assert_called_once()

    def test_new_watchdog_acknowledges_handoff(self):
        def spawn(*args, **kwargs):
            Path(kwargs["env"][lifecycle.ACK_ENV]).write_text("222")
            return self.child
        self.spawn.side_effect = spawn
        self.assertEqual(self.watch(), 0)
        self.assertEqual(self.events()[-1]["event"], "recovery_handoff")

    def test_attempts_expire_and_malformed_history_is_ignored(self):
        self.assertEqual(lifecycle.recovery_history('[1,900,"bad",true,1100]', 1000), [900])
        self.assertEqual(lifecycle.recovery_history("{}", 1000), [])
        self.assertEqual(lifecycle.recovery_history("not json", 1000), [])

    def test_stop_message_records_sender_and_session_end_respects_cancellation(self):
        item = lifecycle.Lifecycle(self.root, self.root, self.root, ROOT)
        item.guard = Mock()
        original = Mock(return_value=42)
        icon = Mock(_message_handlers={lifecycle.WM_STOP: original})
        item.attach(icon)
        icon._message_handlers[lifecycle.WM_ENDSESSION](0, 0)
        self.assertIsNone(item.reason)
        self.assertEqual(icon._message_handlers[lifecycle.WM_STOP](987, 0), 42)
        rows = [json.loads(line) for line in (self.root / "tray-lifecycle.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((rows[0]["actor_pid"], rows[0]["reason"]), (987, "external-stop"))
        item.guard.wait.assert_called_once_with(timeout=5)

    def test_menu_exit_disarms_before_stopping_services(self):
        from peach.tray import PeachTray
        tray = PeachTray.__new__(PeachTray)
        events = []
        tray.lifecycle = Mock()
        tray.lifecycle.stop.side_effect = lambda *args: events.append("intent")
        tray._stop_event = Mock()
        tray.manager = Mock()
        tray.manager.stop_owned.side_effect = lambda: events.append("services")
        tray.icon = Mock()
        tray.icon.stop.side_effect = lambda: events.append("icon")
        tray.exit()
        self.assertEqual(events, ["intent", "services", "icon"])
        tray.lifecycle.stop.assert_called_once_with("menu-exit", os.getpid())


@unittest.skipUnless(os.name == "nt", "Windows 内核进程句柄")
class WindowsWatchdogTests(TrayLifecycleTests):
    def test_native_lifecycle_arms_base_interpreter_and_disarms(self):
        item = lifecycle.Lifecycle(self.root, self.root, self.root, ROOT)
        try:
            item.start()
            self.assertIsNone(item.guard.poll())
            self.assertTrue(item.path.with_suffix(".ready").exists())
        finally:
            item.stop("menu-exit")
        self.assertEqual(item.guard.poll(), 0)
        rows = [json.loads(line) for line in (self.root / "tray-lifecycle.log").read_text(encoding="utf-8").splitlines()]
        self.assertIn("watchdog_ready", [row["event"] for row in rows])

    def native_case(self, intentional):
        target = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        guard = None
        handle = lifecycle.ProcessHandle(target.pid)
        try:
            marker = self.root / "recovered.txt"
            self.record.update(pid=target.pid, created=handle.created,
                               argv=[sys.executable, "-c",
                                     "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('ok')"])
            self.save()
            environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            guard = subprocess.Popen([sys.executable, "-m", "peach.tray_lifecycle", str(self.path)],
                                     env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 10
            while not self.path.with_suffix(".ready").exists():
                self.assertIsNone(guard.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.05)
            if intentional:
                self.path.with_suffix(".stop").write_text("menu-exit")
                self.assertEqual(guard.wait(timeout=10), 0)
            target.terminate()
            target.wait(timeout=5)
            self.assertEqual(guard.wait(timeout=15), 0)
            self.assertEqual(marker.exists(), not intentional)
            if not intentional:
                self.assertTrue(any(row["event"] == "unexpected_exit" for row in self.events()))
        finally:
            handle.close()
            for process in (guard, target):
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)

    def test_native_process_death_recovers_a_temporary_command(self):
        self.native_case(False)

    def test_native_intent_keeps_the_process_stopped(self):
        self.native_case(True)


if __name__ == "__main__":
    unittest.main()
