from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest import mock

from peach import tray_detached, windows_restart
from peach.process_job import assign_to_job, close_job, create_kill_on_close_job

ROOT = Path(__file__).resolve().parents[1]


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [("times", ctypes.c_longlong * 2), ("flags", wintypes.DWORD),
                ("working_set", ctypes.c_size_t * 2), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class _JobLimits(ctypes.Structure):
    _fields_ = [("basic", _JobBasicLimits), ("io", ctypes.c_ulonglong * 6),
                ("memory", ctypes.c_size_t * 4)]


class DetachedLaunchTests(unittest.TestCase):
    def setUp(self):
        events = mock.patch("peach.error_reporting.event")
        self.events = events.start()
        self.addCleanup(events.stop)

    def test_source_sync_restarts_for_detachment_and_lifecycle_changes(self):
        from peach.tray import tray_restart_required
        for path in ("src/peach/tray_detached.py", "src/peach/tray_lifecycle.py"):
            with self.subTest(path=path):
                self.assertTrue(tray_restart_required((path,)))

    def test_tray_handoff_leaves_the_single_instance_lock_for_the_child(self):
        from peach.tray import main
        with mock.patch("peach.tray.handoff_tray", return_value=True) as handoff, \
                mock.patch("peach.tray.SingleInstance") as instance, \
                mock.patch("peach.tray.settings_file.load_config"), \
                mock.patch("peach.tray.error_reporting.install"), \
                mock.patch("peach.tray.needs_setup", return_value=False):
            instance.return_value.is_running.return_value = False
            self.assertEqual(main(["--silent"]), 0)
        self.assertEqual(handoff.call_args.args[0], ["--silent"])
        instance.return_value.acquire.assert_not_called()

    def test_existing_tray_opens_its_page_without_attempting_handoff(self):
        from peach.tray import main
        for waiting, silent in ((False, False), (True, False), (False, True)):
            with self.subTest(waiting=waiting, silent=silent), \
                    mock.patch("peach.tray.handoff_tray", side_effect=OSError("desktop denied")) as handoff, \
                    mock.patch("peach.tray.SingleInstance") as instance, \
                    mock.patch("peach.tray.settings_file.load_config") as config, \
                    mock.patch("peach.tray.error_reporting.install"), \
                    mock.patch("peach.tray.error_reporting.failure_details", return_value="desktop denied"), \
                    mock.patch("peach.tray.show_message"), \
                    mock.patch("peach.tray.needs_setup", return_value=waiting), \
                    mock.patch("peach.tray.normal_url", return_value="https://peach.invalid") as normal, \
                    mock.patch("peach.tray.setup_url", return_value="http://peach.invalid/setup") as setup, \
                    mock.patch("peach.tray.webbrowser.open") as browser:
                instance.return_value.is_running.return_value = True
                self.assertEqual(main(["--silent"] if silent else ["--show"]), 0)
            handoff.assert_not_called()
            instance.return_value.acquire.assert_not_called()
            if silent:
                browser.assert_not_called()
            else:
                browser.assert_called_once_with("http://peach.invalid/setup" if waiting else "https://peach.invalid")
                (setup if waiting else normal).assert_called_once_with(config.return_value)

    def test_failed_handoff_stops_before_services_or_state_are_created(self):
        from peach.tray import main
        with mock.patch("peach.tray.handoff_tray", side_effect=OSError("desktop unavailable")), \
                mock.patch("peach.tray.error_reporting.failure_details", return_value="desktop unavailable"), \
                mock.patch("peach.tray.show_message") as message, \
                mock.patch("peach.tray.SingleInstance") as instance, \
                mock.patch("peach.tray.settings_file.load_config"), \
                mock.patch("peach.tray.error_reporting.install"), \
                mock.patch("peach.tray.needs_setup", return_value=False):
            instance.return_value.is_running.return_value = False
            self.assertEqual(main(["--silent"]), 1)
        self.assertIn("desktop unavailable", message.call_args.args[1])
        instance.return_value.acquire.assert_not_called()

    def test_handoff_marker_is_consumed_once_even_in_a_system_job(self):
        with mock.patch.dict(os.environ, {"PEACH_TRAY_DESKTOP_HANDOFF": "1"}), \
                mock.patch.object(tray_detached, "in_job", return_value=True), \
                mock.patch.object(tray_detached, "launch_detached") as launch:
            self.assertFalse(tray_detached.handoff_tray([], ROOT))
            self.assertNotIn("PEACH_TRAY_DESKTOP_HANDOFF", os.environ)
            launch.assert_not_called()
            self.assertTrue(tray_detached.handoff_tray([], ROOT))
            launch.assert_called_once()

    def test_instance_probe_is_read_only_and_does_not_hold_the_lock(self):
        from peach.tray import SingleInstance
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "state" / "tray.lock"
            first, second = SingleInstance(path), SingleInstance(path)
            self.assertFalse(second.is_running())
            self.assertFalse(path.parent.exists())
            first.acquire()
            try:
                self.assertTrue(second.is_running())
            finally:
                first.close()
            content = path.read_bytes()
            self.assertFalse(second.is_running())
            self.assertEqual(path.read_bytes(), content)
            second.acquire()
            second.close()

    def test_job_flag_names_follow_windows_limit_values(self):
        kernel = mock.Mock()

        def query(_job, _kind, output, _size, _returned):
            ctypes.cast(output, ctypes.POINTER(tray_detached._JobBasicLimits)).contents.flags = 0x3800
            return 1

        kernel.QueryInformationJobObject.side_effect = query
        with mock.patch.object(tray_detached, "_in_job", return_value=True):
            facts = tray_detached._job_facts(kernel, -1, current=True)
        self.assertEqual(facts["immediate_limit_flags"], "0x3800")
        self.assertEqual(facts["immediate_limit_names"],
                         ["BREAKAWAY_OK", "SILENT_BREAKAWAY_OK", "KILL_ON_JOB_CLOSE"])

    @unittest.skipUnless(os.name == "nt", "Windows 挂起创建与线程恢复")
    def test_resume_failure_reaps_the_child_and_keeps_diagnostic_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            marker = root / "executed"
            kernel = tray_detached._kernel()
            failing = mock.Mock(wraps=kernel)

            def resume(_thread):
                ctypes.set_last_error(31)
                return 0xFFFFFFFF

            failing.ResumeThread.side_effect = resume
            with mock.patch.object(tray_detached, "_kernel", return_value=failing):
                with self.assertRaises(OSError) as raised:
                    tray_detached.launch_detached([sys._base_executable, '-c',
                        'from pathlib import Path; import sys; Path(sys.argv[1]).touch()', str(marker)], cwd=root)
            self.assertEqual(raised.exception.peach_operation, "ResumeThread")
            self.assertEqual(raised.exception.winerror, 31)
            self.assertIn("desktop", raised.exception.peach_context)
            self.assertFalse(marker.exists())
            pid = raised.exception.peach_context["child"]["pid"]
            handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
            if handle:
                process = tray_detached.DetachedProcess(handle, pid)
                try:
                    self.assertEqual(process.wait(timeout=5), 1)
                finally:
                    process.close()

    def test_source_handoff_preserves_arguments_and_environment(self):
        child = mock.Mock()
        with mock.patch.object(tray_detached, "in_job", return_value=True), \
                mock.patch.object(tray_detached, "launch_detached", return_value=child) as launch, \
                mock.patch.dict(os.environ, {"PEACH_DATA_ROOT": "C:/测试/peach-data"}), \
                mock.patch.object(sys, "frozen", False, create=True):
            self.assertTrue(tray_detached.handoff_tray(["--silent"], ROOT))
        self.assertEqual(launch.call_args.args[0], [sys.executable, "-m", "peach.tray", "--silent"])
        self.assertEqual(launch.call_args.kwargs["env"]["PEACH_DATA_ROOT"], "C:/测试/peach-data")
        self.assertEqual(launch.call_args.kwargs["env"]["PYTHONPATH"], str(ROOT / "src"))
        child.close.assert_called_once()

    def test_desktop_launch_needs_no_handoff(self):
        with mock.patch.object(tray_detached, "in_job", return_value=False), \
                mock.patch.object(tray_detached, "launch_detached") as launch:
            self.assertFalse(tray_detached.handoff_tray([], ROOT))
        launch.assert_not_called()

    def test_packaged_handoff_keeps_the_executable_and_flags(self):
        with mock.patch.object(tray_detached, "in_job", return_value=True), \
                mock.patch.object(tray_detached, "launch_detached") as launch, \
                mock.patch.object(sys, "frozen", True, create=True):
            self.assertTrue(tray_detached.handoff_tray(["--show"], ROOT))
        self.assertEqual(launch.call_args.args[0], [sys.executable, "--show"])
        self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_packaged_restart_uses_the_desktop_launcher(self):
        with mock.patch.object(windows_restart, "launch_detached") as launch:
            windows_restart.start_tray(ROOT / "Peach.exe")
        self.assertEqual(launch.call_args.args[0], [str(ROOT / "Peach.exe")])
        self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    @unittest.skipUnless(os.name == "nt", "Windows Job 与桌面进程")
    def test_desktop_child_survives_launcher_job_shutdown(self):
        # gate 确保 worker 纳入 Job 后才创建两种子进程；同一关闭动作给出存活与退出证据。
        worker_code = """
import json, os, subprocess, sys, time
from pathlib import Path
from peach.tray_detached import in_job, launch_detached
root = Path(sys.argv[1])
deadline = time.monotonic() + 15
while not (root / 'go').exists():
    if time.monotonic() > deadline:
        raise TimeoutError('gate')
    time.sleep(.05)
assert in_job()
control = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                           creationflags=subprocess.DETACHED_PROCESS)
env = {**os.environ, 'PEACH_DETACHED_TEST': '测试 env'}
code = 'import json, os, sys, time; from pathlib import Path; from peach.tray_detached import in_job; Path("child.json").write_text(json.dumps([os.getcwd(), os.environ["PEACH_DETACHED_TEST"], sys.argv[1], sys.executable, sys.prefix, in_job()]), encoding="utf-8"); time.sleep(60)'
child = launch_detached([sys.argv[3], '-c', code, sys.argv[2]], cwd=root, env=env)
(root / 'pids.json').write_text(json.dumps([control.pid, child.pid]))
time.sleep(60)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            job = create_kill_on_close_job()
            self.assertIsNotNone(job)
            worker = None
            handles = []
            try:
                worker = subprocess.Popen([sys._base_executable, "-c", worker_code, str(root),
                                           '引号 " 和 \\ 路径', sys.executable],
                    env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PEACH_DATA_ROOT": str(root)},
                    creationflags=subprocess.CREATE_NO_WINDOW)
                assign_to_job(job, worker)
                (root / "go").touch()
                deadline = time.monotonic() + 15
                while True:
                    try:
                        pids = json.loads((root / "pids.json").read_text())
                        payload = json.loads((root / "child.json").read_text(encoding="utf-8"))
                        break
                    except (FileNotFoundError, json.JSONDecodeError):
                        self.assertIsNone(worker.poll(), "worker 启动失败")
                        self.assertLess(time.monotonic(), deadline, "独立启动未就绪")
                        time.sleep(.05)
                kernel = tray_detached._kernel()
                for pid in pids:
                    handle = kernel.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, pid)
                    self.assertTrue(handle)
                    handles.append(tray_detached.DetachedProcess(handle, pid))
                control, child = handles
                self.assertTrue(tray_detached._in_job(kernel, control._handle, job))
                self.assertFalse(tray_detached._in_job(kernel, child._handle, job))
                with self.assertRaises(subprocess.TimeoutExpired):
                    child.wait(timeout=.01)
                close_job(job)
                job = None
                worker.wait(timeout=10)
                control.wait(timeout=10)
                self.assertIsNone(child.poll(), "独立托盘子进程必须存活")
                self.assertEqual(payload[:2], [str(root), "测试 env"])
                self.assertEqual(payload[2], '引号 " 和 \\ 路径')
                self.assertEqual(payload[3:5], [sys.executable, sys.prefix])
                child.terminate()
                self.assertEqual(child.wait(timeout=10), 1)
            finally:
                close_job(job)
                if worker is not None and worker.poll() is None:
                    worker.terminate()
                    worker.wait(timeout=10)
                for process in handles:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)
                    process.close()

    @unittest.skipUnless(os.name == "nt", "Windows 桌面 Job 的实际继承与脱离")
    def test_children_can_share_desktop_jobs_with_various_breakaway_policies(self):
        for flags in (0, 0x1000, 0x0800):
            with self.subTest(flags=hex(flags)):
                self._desktop_job_launch(flags)

    def _job(self, flags):
        job = create_kill_on_close_job()
        self.assertIsNotNone(job)
        kernel = tray_detached._kernel()
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   wintypes.LPVOID, wintypes.DWORD]
        limits = _JobLimits()
        limits.basic.flags = flags
        self.assertTrue(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        return job

    @staticmethod
    def _desktop_dll(desktop):
        user = mock.Mock()
        user.GetShellWindow.return_value = 1

        def desktop_pid(_window, output):
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = desktop.pid
            return 1

        user.GetWindowThreadProcessId.side_effect = desktop_pid
        native_dll = ctypes.WinDLL
        return lambda name, *args, **kwargs: user if name == "user32" else native_dll(name, *args, **kwargs)

    def _desktop_job_launch(self, flags):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            job = self._job(0x2000 | flags)
            kernel = tray_detached._kernel()
            desktop = child = None
            try:
                desktop = tray_detached.launch_detached(
                    [sys._base_executable, "-c", "import time; time.sleep(60)"], cwd=root)
                assign_to_job(job, desktop)
                self.assertTrue(tray_detached._in_job(kernel, desktop._handle))
                marker = root / "executed"
                code = "from pathlib import Path; import sys, time; Path(sys.argv[1]).touch(); time.sleep(60)"
                with mock.patch.object(ctypes, "WinDLL", side_effect=self._desktop_dll(desktop)):
                    child = tray_detached.launch_detached(
                        [sys._base_executable, "-c", code, str(marker)], cwd=root)
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    self.assertIsNone(child.poll())
                    time.sleep(.05)
                self.assertTrue(marker.exists(), "桌面 Job 内的子进程应执行")
                self.assertEqual(tray_detached._in_job(kernel, child._handle, job), not bool(flags & 0x1000))
                close_job(job)
                job = None
                desktop.wait(timeout=10)
                if flags & 0x1000:
                    self.assertIsNone(child.poll())
                else:
                    child.wait(timeout=10)
            finally:
                close_job(job)
                for process in (child, desktop):
                    if process is not None:
                        if process is desktop and job is not None:
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                pass
                        if process.poll() is None:
                            process.terminate()
                        process.wait(timeout=10)
                        process.close()


NESTED_WORKER = """
import ctypes, json, os, subprocess, sys, time
from ctypes import wintypes
from pathlib import Path
from unittest.mock import Mock, patch
from peach.tray_detached import launch_detached
root = Path(sys.argv[1])
os.environ.pop('PEACH_TRAY_DESKTOP_HANDOFF', None)
deadline = time.monotonic() + 20
while not (root / 'go').exists():
    if time.monotonic() > deadline:
        raise TimeoutError('gate')
    time.sleep(.05)
control = subprocess.Popen([sys._base_executable, '-c', 'import time; time.sleep(60)'],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW)
user = Mock()
user.GetShellWindow.return_value = 1
def desktop_pid(window, output):
    ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = int(sys.argv[2])
    return 1
user.GetWindowThreadProcessId.side_effect = desktop_pid
native_dll = ctypes.WinDLL
def dll(name, *args, **kwargs):
    return user if name == 'user32' else native_dll(name, *args, **kwargs)
with patch.object(ctypes, 'WinDLL', side_effect=dll):
    child = launch_detached([sys.executable, '-c', sys.argv[3], str(root)], cwd=root)
(root / 'pids.json').write_text(json.dumps([control.pid, child.pid]))
time.sleep(60)
"""

NESTED_CHILD = """
import json, os, sys, time
from pathlib import Path
from unittest.mock import patch
from peach.tray_detached import handoff_tray, in_job
root = Path(sys.argv[1])
with patch('peach.tray_detached.launch_detached', side_effect=RuntimeError('repeated handoff')):
    handed_off = handoff_tray([], root)
(root / 'child.json').write_text(json.dumps(dict(in_job=in_job(), handed_off=handed_off,
    marker_consumed='PEACH_TRAY_DESKTOP_HANDOFF' not in os.environ)))
time.sleep(60)
"""


@unittest.skipUnless(os.name == "nt", "Windows 多层 Job 的实际继承与关闭")
class NestedJobTests(unittest.TestCase):
    _job = DetachedLaunchTests._job
    setUp = DetachedLaunchTests.setUp

    def test_child_shares_system_jobs_and_survives_its_launcher_job_closing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            jobs = [self._job(flags) for flags in (0x2000, 0x2000, 0x3800, 0x2000)]
            processes = []
            try:
                desktop = tray_detached.launch_detached(
                    [sys._base_executable, '-c', 'import time; time.sleep(60)'], cwd=root)
                processes.append(desktop)
                for job in jobs[:3]:
                    assign_to_job(job, desktop)
                worker = tray_detached.launch_detached(
                    [sys._base_executable, '-c', NESTED_WORKER, str(root), str(desktop.pid), NESTED_CHILD],
                    cwd=root, env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PEACH_DATA_ROOT": str(root)})
                processes.append(worker)
                for job in (jobs[0], jobs[1], jobs[3]):
                    assign_to_job(job, worker)
                kernel = tray_detached._kernel()
                self.assertTrue(tray_detached._in_job(kernel, worker._handle, jobs[3]))
                (root / 'go').touch()
                self._wait_for_nested_child(root, worker)
                for pid in json.loads((root / 'pids.json').read_text()):
                    handle = kernel.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, pid)
                    self.assertTrue(handle)
                    processes.append(tray_detached.DetachedProcess(handle, pid))
                control, child = processes[-2:]
                self.assertTrue(tray_detached._in_job(kernel, child._handle, jobs[1]))
                self.assertTrue(tray_detached._in_job(kernel, control._handle, jobs[3]))
                self.assertFalse(tray_detached._in_job(kernel, child._handle, jobs[3]))
                close_job(jobs.pop())
                worker.wait(timeout=10)
                control.wait(timeout=10)
                self.assertIsNone(child.poll(), "退出启动器的私有 Job 后托盘必须存活")
                self.assertEqual(json.loads((root / 'child.json').read_text()),
                                 dict(in_job=True, handed_off=False, marker_consumed=True))
                records = [json.loads(line) for path in (root / 'logs').glob('errors-*.log')
                           for line in path.read_text(encoding='utf-8').splitlines()]
                context = next(row['context'] for row in records if row['stage'] == 'tray-detached')
                self.assertEqual(context['caller']['immediate_limit_flags'], '0x2000')
                self.assertEqual(context['caller']['immediate_limit_names'], ['KILL_ON_JOB_CLOSE'])
                self.assertTrue(context['desktop']['in_job'])
                self.assertTrue(context['child']['in_job'])
            finally:
                for job in reversed(jobs):
                    close_job(job)
                for process in processes:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=10)
                    finally:
                        process.close()

    def _wait_for_nested_child(self, root, worker):
        deadline = time.monotonic() + 20
        while not (root / 'child.json').exists() or not (root / 'pids.json').exists():
            self.assertIsNone(worker.poll(), "多层 Job 启动器失败")
            self.assertLess(time.monotonic(), deadline, "多层 Job 托盘未就绪")
            time.sleep(.05)



if __name__ == "__main__":
    unittest.main()
