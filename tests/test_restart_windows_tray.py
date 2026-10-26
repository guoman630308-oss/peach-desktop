from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from peach import runtime_prepare, windows_restart


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "restart_windows_tray.py"


def load_entry():
    """命令行入口只做参数解析和打印，实现在 `peach.windows_restart`。"""
    spec = importlib.util.spec_from_file_location("restart_windows_tray_entry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RestartWindowsTrayTests(unittest.TestCase):
    def test_process_probe_reuses_read_only_pid_check(self):
        with mock.patch("peach.jobs.PidFileLock._running", return_value=True) as running, mock.patch("os.kill") as kill:
            self.assertTrue(windows_restart.process_alive(123))
            running.assert_called_once_with(123)
            kill.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # restart_tray 第一件事就是 target.resolve()，并由它推出 .venv 里的服务入口。
        # CI runner 的临时目录是别名（macOS /var 软链到 /private/var，Windows 的
        # RUNNER~1 短名展开成 runneradmin），不先 resolve 推出来的服务入口就对不上
        # self.service，services() 返回空、循环空转，最后把 find_windows 的假迭代器
        # 耗尽成 StopIteration。
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / "dist" / "Peach" / "Peach.exe"
        self.service = self.root / ".venv" / "Scripts" / "peach.exe"
        self.target.parent.mkdir(parents=True)
        self.service.parent.mkdir(parents=True)
        self.target.write_bytes(b"tray")
        self.service.write_bytes(b"service")

    def tearDown(self):
        self.temp.cleanup()

    def test_refuses_when_the_exact_tray_window_is_not_unique(self):
        start = mock.Mock()
        result = windows_restart.restart_tray(
            self.target, find_windows=lambda _target: (), start=start,
        )
        self.assertFalse(result.ok)
        self.assertIn("唯一", result.message)
        start.assert_not_called()

    BOOTSTRAP = ("import os,runpy;os.environ['PEACH_DATA_ROOT']=r'X'"
                 ";runpy.run_module('peach.tray',run_name='__main__')")

    def test_the_source_search_runs_its_own_win32_declarations(self):
        """两步筛的第一步是一串 ctypes 声明，名字取错要到调用那一刻才炸。

        EnumWindows 是替换不掉的，那就真枚举一遍：非 Windows 上函数在开头就返回空，
        Windows 上把整串声明走完。命令行那步替掉，免得为一条判据起 PowerShell。
        """
        with mock.patch.object(windows_restart, "_command_lines", return_value={}):
            self.assertEqual(windows_restart.find_source_tray_windows(), ())

    def test_windows_command_line_splits_the_source_bootstrap_intact(self):
        line = f'"{self.service.parent / "pythonw.exe"}" -c "{self.BOOTSTRAP}" --show'
        argv = windows_restart.parse_windows_command_line(line)
        self.assertEqual(argv, [f"{self.service.parent / 'pythonw.exe'}",
                                "-c", self.BOOTSTRAP, "--show"])

    def test_the_command_line_rules_that_only_show_up_in_odd_arguments(self):
        """三条 Win32 规则各要一条判据：反斜杠、引号里的引号、真的空参数。

        照抄命令行重启，argv 差一格起的就是另一个东西，所以这里比对的是逐项相等，
        不是「大致长这样」。
        """
        cases = {
            r'a "b c" d': ["a", "b c", "d"],
            # 2n 个反斜杠后跟引号：反斜杠减半，引号收界。
            r'"C:\dir\\" next': ["C:\\dir\\", "next"],
            # 2n+1 个：反斜杠减半，引号变字面量。
            r'"say \"hi\"" done': ['say "hi"', "done"],
            # 引号里的 "" 是一个字面引号，不收界。
            r'"a""b"': ['a"b'],
            # 空参数是真参数，不能因为没攒到字符就丢掉。
            'first "" last': ["first", "", "last"],
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(windows_restart.parse_windows_command_line(line), expected)

    def test_source_restart_reruns_the_recorded_command_line(self):
        pythonw = self.service.parent / "pythonw.exe"
        pythonw.write_bytes(b"shim")
        argv = [str(pythonw), "-c", self.BOOTSTRAP, "--show"]
        windows = iter((
            (windows_restart.TrayWindow(10, 20),),
            (windows_restart.TrayWindow(30, 40),),
        ))
        launched = mock.Mock()
        launched.poll.return_value = None
        started_with = []
        result = windows_restart.restart_source_tray(
            find_windows=lambda: next(windows),
            stop_window=lambda _handle: True,
            alive=lambda _pid: False,
            command_lines=lambda: {10: subprocess.list2cmdline(argv)},
            start=lambda argv: started_with.append(list(argv)) or launched,
            services=lambda tray_pid, executable: (51, 52)
            if tray_pid == 30 and executable == self.service else (),
            sleep=lambda _seconds: None,
        )
        self.assertTrue(result.ok, result.message)
        self.assertEqual(started_with, [argv])
        self.assertEqual(result.new_tray_pid, 30)
        self.assertEqual(result.service_pids, (51, 52))

    def test_source_restart_refuses_when_the_window_command_has_no_tray_marker(self):
        windows = iter(((windows_restart.TrayWindow(10, 20),),))
        start = mock.Mock()
        result = windows_restart.restart_source_tray(
            find_windows=lambda: next(windows),
            command_lines=lambda: {10: "notepad.exe"},
            start=start,
        )
        self.assertFalse(result.ok)
        self.assertIn("peach.tray", result.message)
        start.assert_not_called()

    def test_timeout_never_force_kills_or_starts_a_second_tray(self):
        start = mock.Mock()
        result = windows_restart.restart_tray(
            self.target, timeout=0.001,
            find_windows=lambda _target: (windows_restart.TrayWindow(10, 20),),
            stop_window=lambda _handle: True, alive=lambda _pid: True,
            start=start, sleep=lambda _seconds: None,
        )
        self.assertFalse(result.ok)
        self.assertIn("未强杀、未另启", result.message)
        start.assert_not_called()

    def test_normal_exit_restarts_and_requires_tray_owned_services(self):
        windows = iter((
            (windows_restart.TrayWindow(10, 20),),
            (windows_restart.TrayWindow(30, 40),),
        ))
        launched = mock.Mock()
        launched.poll.return_value = None
        result = windows_restart.restart_tray(
            self.target,
            find_windows=lambda _target: next(windows),
            stop_window=lambda handle: handle == 20,
            alive=lambda _pid: False,
            start=lambda _target: launched,
            services=lambda tray_pid, executable: (51, 52)
            if tray_pid == 30 and executable == self.service else (),
            sleep=lambda _seconds: None,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.old_tray_pid, 10)
        self.assertEqual(result.new_tray_pid, 30)
        self.assertEqual(result.service_pids, (51, 52))

    def source_restart(self, **overrides):
        pythonw = self.service.parent / "pythonw.exe"
        pythonw.write_bytes(b"shim")
        argv = [str(pythonw), "-c", self.BOOTSTRAP]
        windows = iter(((windows_restart.TrayWindow(10, 20),),))
        options = dict(
            find_windows=lambda: next(windows, (windows_restart.TrayWindow(30, 40),)),
            stop_window=lambda _handle: True,
            alive=lambda _pid: False,
            command_lines=lambda: {10: subprocess.list2cmdline(argv)},
            services=lambda tray_pid, _executable: (51, 52) if tray_pid in (10, 30) else (),
            strays=lambda _tray_pid, _executable: (),
            sleep=lambda _seconds: None,
        )
        options.update(overrides)
        return windows_restart.restart_source_tray(**options)

    @staticmethod
    def running_tray():
        launched = mock.Mock()
        launched.poll.return_value = None
        return launched

    def test_orphaned_services_on_the_ports_refuse_the_restart_before_anything_stops(self):
        """被强杀过的托盘留下的服务不归任何人管，重启托盘换不掉它们。

        2026-09-25 就是这样：旧托盘名下一个子服务都没有，新托盘见端口健康也不拉自己的，
        脚本空等到超时，报的却是「未在期限内就绪」。
        """
        stop_window, start = mock.Mock(), mock.Mock()
        result = self.source_restart(
            services=lambda _tray_pid, _executable: (),
            strays=lambda _tray_pid, _executable: (5428, 39272),
            stop_window=stop_window, start=start,
        )
        self.assertFalse(result.ok)
        self.assertIn("5428、39272", result.message)
        self.assertEqual(result.service_pids, (5428, 39272))
        stop_window.assert_not_called()
        start.assert_not_called()

    def test_a_tray_whose_services_simply_died_is_still_restarted(self):
        """名下缺服务、旁边也没有别的 serve：重启正是修它的办法。"""
        launched = self.running_tray()
        result = self.source_restart(
            services=lambda tray_pid, _executable: (51, 52) if tray_pid == 30 else (),
            start=lambda _argv: launched,
        )
        self.assertTrue(result.ok, result.message)

    def test_the_new_tray_starts_only_after_the_old_services_are_gone(self):
        still_running = {51: 3, 52: 1}
        seen_at_start = []

        def alive(pid):
            if still_running.get(pid, 0) == 0:
                return False
            still_running[pid] -= 1
            return True

        launched = self.running_tray()
        result = self.source_restart(
            alive=alive,
            start=lambda _argv: seen_at_start.append(dict(still_running)) or launched,
        )
        self.assertTrue(result.ok, result.message)
        self.assertEqual(seen_at_start, [{51: 0, 52: 0}])

    def test_old_services_that_outlive_their_tray_block_the_new_one(self):
        start = mock.Mock()
        result = self.source_restart(
            timeout=0.001, alive=lambda pid: pid in (51, 52), start=start,
        )
        self.assertFalse(result.ok)
        self.assertIn("子服务未在期限内退出", result.message)
        self.assertEqual(result.service_pids, (51, 52))
        start.assert_not_called()

    def test_a_new_tray_without_its_own_services_is_named_in_the_failure(self):
        launched = self.running_tray()
        result = self.source_restart(
            timeout=0.05,
            services=lambda tray_pid, _executable: (51, 52) if tray_pid == 10 else (),
            start=lambda _argv: launched,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.new_tray_pid, 30)
        self.assertIn("PID 30", result.message)
        self.assertIn("名下只有 0 个子服务", result.message)

    @unittest.skipUnless(sys.platform == "win32", "进程快照只在 Windows 上有")
    def test_strays_are_serve_launchers_outside_the_tray(self):
        exe = str(self.service)
        paths = {100: exe, 101: exe, 102: exe, 103: exe, 7: "C:/tray/pythonw.exe"}
        parents = {100: 7, 101: 66, 102: 66, 103: 66, 7: 1}
        commands = {101: f'"{exe}" serve --port 443', 102: f'"{exe}" scrape ABW-001',
                    103: f'"{exe}" serve --port 80'}
        with (
            mock.patch.object(windows_restart, "_process_paths_and_parents",
                              return_value=(paths, parents)),
            mock.patch.object(windows_restart, "_command_lines",
                              side_effect=lambda pids: {pid: commands.get(pid, "") for pid in pids}),
        ):
            self.assertEqual(windows_restart.stray_service_pids(7, self.service), (101, 103))
            self.assertEqual(windows_restart.owned_service_pids(7, self.service), (100,))

    def stage(self) -> Path:
        staged = self.root / "staging" / "Peach.exe"
        staged.parent.mkdir()
        staged.write_bytes(b"new")
        return staged

    def test_the_binary_is_swapped_after_the_old_tray_exits_and_before_the_new_starts(self):
        staged = self.stage()
        launched = mock.Mock()
        launched.poll.return_value = None
        bytes_at_start = []
        windows = iter((
            (windows_restart.TrayWindow(10, 20),),
            (windows_restart.TrayWindow(30, 40),),
        ))
        result = windows_restart.restart_tray(
            self.target, swap_from=staged,
            find_windows=lambda _target: next(windows),
            stop_window=lambda _handle: True,
            alive=lambda _pid: False,
            start=lambda target: bytes_at_start.append(target.read_bytes()) or launched,
            services=lambda _pid, _executable: (51, 52),
            sleep=lambda _seconds: None,
        )
        self.assertTrue(result.ok, result.message)
        self.assertEqual(bytes_at_start, [b"new"], "新托盘启动时读到的已经是新二进制")
        self.assertEqual(self.target.read_bytes(), b"new")
        self.assertEqual(Path(result.backup).read_bytes(), b"tray")
        self.assertEqual(result.swapped_from, str(staged))

    def test_a_new_tray_that_never_owns_its_services_is_rolled_back(self):
        staged = self.stage()
        launched = mock.Mock()
        launched.poll.return_value = None
        start = mock.Mock(return_value=launched)
        result = windows_restart.restart_tray(
            self.target, timeout=0.001, swap_from=staged,
            find_windows=lambda _target: (windows_restart.TrayWindow(10, 20),),
            stop_window=lambda _handle: True,
            alive=lambda _pid: False,
            start=start,
            services=lambda _pid, _executable: (),
            sleep=lambda _seconds: None,
        )
        self.assertFalse(result.ok)
        self.assertIn("已回滚到备份并重开旧托盘", result.message)
        self.assertEqual(self.target.read_bytes(), b"tray")
        self.assertEqual(Path(result.backup).read_bytes(), b"tray",
                         "备份留在原地，下一次失败还有得退")
        self.assertEqual(start.call_count, 2)


class Preparation:
    """记下准备步骤与停、起托盘的先后。"""

    def __init__(self, events, *, refusal=None, ready=True, note="已按 uv.lock 同步依赖"):
        self.events, self.refusal, self.ready, self.note = events, refusal, ready, note

    def before_stop(self, tray_pid):
        self.events.append(("check", tray_pid))
        return self.refusal

    def while_stopped(self):
        self.events.append("sync")
        return self.ready, self.note


class RestartPreparationTests(unittest.TestCase):
    BOOTSTRAP = RestartWindowsTrayTests.BOOTSTRAP

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
        self.service = self.pythonw.with_name("peach.exe")
        self.pythonw.parent.mkdir(parents=True)
        self.pythonw.write_bytes(b"shim")
        self.service.write_bytes(b"service")
        self.argv = [str(self.pythonw), "-c", self.BOOTSTRAP, "--silent"]

    def tearDown(self):
        self.temp.cleanup()

    def restart(self, preparation, events):
        windows = iter((
            (windows_restart.TrayWindow(10, 20),),
            (windows_restart.TrayWindow(30, 40),),
        ))
        launched = mock.Mock()
        launched.poll.return_value = None
        return windows_restart.restart_source_tray(
            find_windows=lambda: next(windows),
            stop_window=lambda _handle: events.append("stop") or True,
            alive=lambda _pid: False,
            command_lines=lambda: {10: subprocess.list2cmdline(self.argv)},
            start=lambda argv: events.append("start") or launched,
            services=lambda tray_pid, _executable: (51, 52) if tray_pid == 30 else (),
            sleep=lambda _seconds: None,
            prepare=preparation,
        )

    def test_dependencies_are_synced_after_the_old_tray_stops_and_before_the_new_starts(self):
        events = []
        result = self.restart(Preparation(events), events)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(events, [("check", 10), "stop", "sync", "start"])
        self.assertEqual(result.preparation, "已按 uv.lock 同步依赖")

    def test_a_refusal_stops_nothing(self):
        events = []
        result = self.restart(Preparation(events, refusal="拒绝重启：PID 48420 还在用项目 venv"),
                              events)
        self.assertFalse(result.ok)
        self.assertIn("48420", result.message)
        self.assertEqual(events, [("check", 10)])

    def test_a_failed_sync_still_brings_the_tray_back_and_says_so(self):
        events = []
        result = self.restart(Preparation(events, ready=False, note="uv sync 失败"), events)
        self.assertEqual(events[-1], "start")
        self.assertFalse(result.ok)
        self.assertEqual(result.new_tray_pid, 30)
        self.assertIn("uv sync 失败", result.message)


class VenvHolderTests(unittest.TestCase):
    def test_only_venv_processes_outside_the_tray_and_this_script_hold_the_venv(self):
        venv = Path(tempfile.gettempdir()).resolve() / "peach-app" / ".venv"
        scripts = venv / "Scripts"
        base = str(Path(tempfile.gettempdir()).resolve() / "python" / "python.exe")
        paths = {
            1: str(scripts / "python.exe"), 2: base,           # 本脚本：启动器与解释器
            10: str(scripts / "pythonw.exe"), 11: base,        # 旧托盘：启动器与解释器
            12: str(scripts / "peach.exe"), 13: base,          # 旧托盘的子服务
            20: str(scripts / "python.exe"), 21: base,         # 别人起的调试服务
            30: base,                                          # 别的 venv 外进程
            40: str(venv.parent / ".venv-other" / "python.exe"),
        }
        parents = {2: 1, 1: 11, 11: 10, 10: 5, 12: 11, 13: 12, 21: 20, 20: 6}
        holders = windows_restart.venv_holders(
            venv, (11,), own_pid=2, snapshot=lambda: (paths, parents))
        self.assertEqual(holders, (20,))


class RestartEntryTests(unittest.TestCase):
    def setUp(self):
        # 入口默认读真实账本的任务表与 Peach 数据目录下的启动记录；这里换成空闸门与给定记录。
        # 入口用 `from … import` 绑定名字，所以补丁打在来源模块上、并且先于 `load_entry()`。
        self.gate = runtime_prepare.TaskGate()
        self.startup = None
        for name, replacement in (
            ("task_gate", lambda *_args, **_kwargs: self.gate),
            ("read_record", lambda *_args, **_kwargs: self.startup),
        ):
            patcher = mock.patch.object(runtime_prepare, name, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_entry(self, argv, result):
        entry = load_entry()
        with (
            mock.patch.object(entry, "find_tray_windows", return_value=()),
            mock.patch.object(entry, "restart_source_tray", return_value=result) as restart,
            contextlib.redirect_stdout(io.StringIO()) as printed,
        ):
            code = entry.main(argv)
        return code, json.loads(printed.getvalue()), restart

    def test_tasks_that_cannot_resume_refuse_the_restart(self):
        blocking = runtime_prepare.ActiveRun(3, "library-processing", "manual", "running", 100, None)
        self.gate = runtime_prepare.TaskGate(blocking=(blocking,))
        code, printed, restart = self.run_entry([], windows_restart.RestartResult(True, "ok"))
        self.assertEqual(code, 1)
        restart.assert_not_called()
        self.assertFalse(printed["ok"])
        self.assertEqual(printed["tasks"]["blocking"][0]["task_key"], "library-processing")

    def test_force_restarts_past_the_gate_and_still_lists_the_tasks(self):
        blocking = runtime_prepare.ActiveRun(3, "media-repair", "manual", "running", 100, None)
        self.gate = runtime_prepare.TaskGate(blocking=(blocking,))
        code, printed, restart = self.run_entry(
            ["--force"], windows_restart.RestartResult(True, "源码托盘已重启"))
        self.assertEqual(code, 0)
        restart.assert_called_once()
        self.assertIsNotNone(restart.call_args.kwargs["prepare"])
        self.assertEqual(printed["tasks"]["blocking"][0]["task_key"], "media-repair")

    def test_a_new_tray_whose_startup_preparation_failed_fails_the_restart(self):
        self.startup = {"pid": 30, "ok": False, "steps": [
            {"name": "migrations", "state": "failed", "message": "migrate status 失败：locked"}]}
        code, printed, _restart = self.run_entry(
            [], windows_restart.RestartResult(True, "源码托盘已重启", new_tray_pid=30))
        self.assertEqual(code, 1)
        self.assertFalse(printed["ok"])
        self.assertIn("locked", printed["message"])

    def test_a_missing_startup_record_is_named_not_assumed(self):
        code, printed, _restart = self.run_entry(
            [], windows_restart.RestartResult(True, "源码托盘已重启", new_tray_pid=30))
        self.assertEqual(code, 0)
        self.assertIn("未取得新托盘的启动准备记录", printed["message"])

    def test_the_entry_hands_the_package_to_swap_and_prints_the_outcome(self):
        entry = load_entry()
        finished = windows_restart.RestartResult(True, "重启完成", backup="C:/dist/backup.exe")
        with (
            mock.patch.object(entry, "restart_tray", return_value=finished) as restart,
            mock.patch.object(entry, "find_tray_windows",
                              return_value=(windows_restart.TrayWindow(10, 20),)),
            contextlib.redirect_stdout(io.StringIO()) as printed,
        ):
            self.assertEqual(entry.main(["--swap-from", "staged.exe"]), 0)
        self.assertEqual(restart.call_args.kwargs["swap_from"], Path("staged.exe"))
        self.assertEqual(json.loads(printed.getvalue())["backup"], "C:/dist/backup.exe")

    def test_without_a_packaged_tray_the_entry_falls_back_to_the_source_tray(self):
        entry = load_entry()
        source = windows_restart.RestartResult(True, "源码托盘已重启")
        with (
            mock.patch.object(entry, "find_tray_windows", return_value=()),
            mock.patch.object(entry, "restart_source_tray", return_value=source) as srcs,
            contextlib.redirect_stdout(io.StringIO()) as printed,
        ):
            self.assertEqual(entry.main(["--swap-from", "staged.exe"]), 0)
        srcs.assert_called_once()
        self.assertEqual(json.loads(printed.getvalue())["message"], "源码托盘已重启")

    def test_source_requests_bypass_the_packaged_tray(self):
        entry = load_entry()
        source = windows_restart.RestartResult(True, "源码托盘已重启")
        with (
            mock.patch.object(entry, "find_tray_windows", return_value=()),
            mock.patch.object(entry, "restart_source_tray", return_value=source),
            mock.patch.object(entry, "restart_tray") as ghost,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(entry.main(["--source"]), 0)
        ghost.assert_not_called()

    def test_asking_to_swap_a_package_into_the_source_tray_is_refused(self):
        """源码托盘没有生产入口可换，这两个开关同用是笔误。

        放行的话命令照样退出 0、照样打印重启成功，而那个暂存包一动没动——
        下一次开机跑起来的还是旧代码，而部署这一步看上去是成功的。
        """
        entry = load_entry()
        with (
            mock.patch.object(entry, "restart_source_tray") as source,
            mock.patch.object(entry, "restart_tray") as packaged,
            contextlib.redirect_stderr(io.StringIO()) as complaint,
        ):
            with self.assertRaises(SystemExit):
                entry.main(["--source", "--swap-from", "staged.exe"])
        source.assert_not_called()
        packaged.assert_not_called()
        self.assertIn("--source 不能与 --swap-from 同用", complaint.getvalue())


if __name__ == "__main__":
    unittest.main()
