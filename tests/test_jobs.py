import os
import sqlite3
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from peach.jobs import (
    BackgroundJob,
    DiskGuard,
    DiskSpaceDenied,
    JobAlreadyRunning,
    MeteredSourceDenied,
    PidFileLock,
    SourceAccessPolicy,
    job_main,
    require_free_space,
)


class JobPolicyTests(unittest.TestCase):
    def test_metered_source_requires_explicit_authorization(self):
        policy = SourceAccessPolicy()
        with self.assertRaises(MeteredSourceDenied):
            policy.sql_filter("pikpak", allow_metered=False)

    def test_unmetered_query_uses_parameters_and_excludes_metered_sources(self):
        sql, parameters = SourceAccessPolicy().sql_filter("115", allow_metered=False)
        with sqlite3.connect(':memory:') as connection:
            connection.execute('CREATE TABLE asset(location TEXT,path TEXT)')
            connection.executemany('INSERT INTO asset VALUES(?,?)',
                                   [('115', r'B:\movie.mp4'), ('online', ''), ('pikpak', r'A:\movie.mp4')])
            self.assertEqual(connection.execute('SELECT location FROM asset WHERE 1=1' + sql,
                                                parameters).fetchall(), [('115',)])

    def test_root_choices_allow_pikpak_and_protect_a_different_root_of_the_same_source(self):
        config = SimpleNamespace(locations={'pikpak': (r'A:\Normal', r'A:\Proxy', r'A:\Proxy\Free')},
                                 media_costs={r'A:\Normal': 'free', r'A:\Proxy': 'metered',
                                              r'A:\Proxy\Free': 'free'})
        with patch('peach.settings_file.active', return_value=config):
            sql, parameters = SourceAccessPolicy().sql_filter('pikpak', False, 'a.location')
        with sqlite3.connect(':memory:') as connection:
            connection.execute('CREATE TABLE asset(location TEXT,path TEXT)')
            connection.executemany('INSERT INTO asset VALUES(?,?)', [('pikpak', path) for path in
                (r'A:\Normal\one.mp4', r'A:\Proxy\two.mp4', r'A:\Proxy\Free\three.mp4', r'A:\NormalExtra\four.mp4')])
            self.assertEqual(connection.execute('SELECT path FROM asset a WHERE 1=1' + sql,
                                                parameters).fetchall(),
                             [(r'A:\Normal\one.mp4',), (r'A:\Proxy\Free\three.mp4',)])

    def test_normal_source_can_require_budget_when_its_root_is_protected(self):
        config = SimpleNamespace(locations={'115': (r'B:\Media',)}, media_costs={r'B:\Media': 'metered'})
        with patch('peach.settings_file.active', return_value=config):
            with self.assertRaises(MeteredSourceDenied):
                SourceAccessPolicy().sql_filter('115', False)

    def test_authorized_metered_query_is_scoped_to_requested_source(self):
        sql, parameters = SourceAccessPolicy().sql_filter("pikpak", allow_metered=True)
        self.assertEqual(sql, " AND location=?")
        self.assertEqual(parameters, ("pikpak",))

    def test_disk_guard_fails_closed_when_usage_is_unavailable(self):
        with patch("peach.jobs.shutil.disk_usage", side_effect=OSError("offline")):
            with self.assertRaises(DiskSpaceDenied):
                require_free_space(Path("C:/"), 40)

    def test_disk_guard_accepts_real_temporary_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertGreaterEqual(require_free_space(tmp, 0), 0)

    def test_runtime_guard_stops_a_job_that_started_with_room(self):
        """2026-08-15 事故：起跑时 C: 有几百 GB，跑到 0 字节也没人再看一眼。"""
        guard = DiskGuard(Path("C:/"), minimum_gb=40, interval_secs=0)
        free = 500 * 1024**3
        with patch("peach.jobs.shutil.disk_usage", return_value=type("U", (), {"free": free})):
            self.assertAlmostEqual(guard.check(force=True), 500, places=3)
        # 同一次运行中盘被第三方缓存吃光，闸门必须在运行期触发，而不是等下次启动。
        with patch("peach.jobs.shutil.disk_usage", return_value=type("U", (), {"free": 1024**3})):
            with self.assertRaises(DiskSpaceDenied):
                guard.check(force=True)

    def test_runtime_guard_throttles_and_fails_closed(self):
        guard = DiskGuard(Path("C:/"), minimum_gb=40, interval_secs=3600)
        with patch("peach.jobs.shutil.disk_usage", return_value=type("U", (), {"free": 1024**3})):
            with self.assertRaises(DiskSpaceDenied):
                guard.check(force=True)
            # 节流窗口内不重复读盘，返回 None 表示本轮未检查——不能被误当成"通过"。
            self.assertIsNone(guard.check())
        with patch("peach.jobs.shutil.disk_usage", side_effect=OSError("offline")):
            with self.assertRaises(DiskSpaceDenied):
                guard.check(force=True)

    def test_stale_lock_from_a_killed_job_does_not_crash_the_next_run(self):
        """被强杀留下的锁必须被清理后继续，而不是把后续所有运行直接崩掉。

        2026-08-15 实际发生过。取一个几乎不可能存在的 PID 走真实的存活探测。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.lock"
            path.write_text("4294967294", encoding="ascii")
            with PidFileLock(path):
                self.assertEqual(path.read_text(encoding="ascii").strip(), str(os.getpid()))
            self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == "nt", "只在 Windows 上成立")
    def test_liveness_probe_never_signals_its_own_console(self):
        """Windows 上 `signal.CTRL_C_EVENT == 0`。

        于是 Unix 探测存活的经典写法 `os.kill(pid, 0)` 实际会调用
        `GenerateConsoleCtrlEvent(CTRL_C_EVENT, ...)`，把 Ctrl+C 发给整个控制台进程组。
        锁里写的又是自己的 PID，「检查锁是否还活着」就会当场把自己打断——在真实控制台里
        跑批处理或测试时表现为毫无征兆的 KeyboardInterrupt，而重定向/无控制台的环境
        不复现，因为控制台事件无处投递。这条守住「探测存活不得发信号」。
        """
        self.assertEqual(int(signal.CTRL_C_EVENT), 0, "前提：CTRL_C_EVENT 就是 0")
        with patch("peach.jobs.os.kill", side_effect=AssertionError("存活探测不得调用 os.kill")):
            self.assertTrue(PidFileLock._running(os.getpid()))
            self.assertFalse(PidFileLock._running(4294967294))

    def test_pid_lock_rejects_live_owner_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.lock"
            with PidFileLock(path):
                self.assertTrue(path.is_file())
                with self.assertRaises(JobAlreadyRunning):
                    PidFileLock(path).acquire()
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()


class JobMainTests(unittest.TestCase):
    """长跑批处理的统一入口收尾。

    这段此前在四个脚本里各写了一份。前三份逐字相同；第四份只把异常变量从 `exc`
    改名成 `error`——一个标识符的差别就能让逐字扫描漏掉它，所以指望「看一眼就发现
    重复」是不成立的。
    """

    def _parser(self, lock: Path):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--lock", type=Path, default=lock)
        return parser

    def test_returns_the_policy_exit_code_instead_of_raising(self):
        """批处理是无人值守跑的，被读的是退出码，不是栈。"""
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "job.lock"

            def run(_args):
                raise DiskSpaceDenied("盘满了")

            with patch("builtins.print") as printed:
                code = job_main(lambda: self._parser(lock), run, [])
            self.assertEqual(code, DiskSpaceDenied.exit_code)
            self.assertIn("[stop]", " ".join(str(c) for c in printed.call_args_list))

    def test_already_running_is_not_a_failure(self):
        """`JobAlreadyRunning.exit_code` 是 0：另一份还在跑不算这次失败。

        散成四份手抄时，谁把它当异常向上抛或改成非零，都不会有测试拦住。
        """
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "job.lock"
            with PidFileLock(lock):
                with patch("builtins.print"):
                    code = job_main(lambda: self._parser(lock),
                                    lambda _args: self.fail("锁没拿住就不该跑 run"), [])
            self.assertEqual(code, 0)

    def test_the_lock_wraps_the_whole_run(self):
        """pid 锁必须包住整个 run，而不是只包住入口。"""
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "job.lock"
            seen = []

            def run(_args):
                seen.append(lock.exists())
                return 7

            self.assertEqual(job_main(lambda: self._parser(lock), run, []), 7)
            self.assertEqual(seen, [True], "run 执行期间锁文件必须还在")
            self.assertFalse(lock.exists(), "退出后锁要释放")


class BackgroundJobTests(unittest.TestCase):
    def test_operation_errors_are_queryable_terminal_receipts(self):
        job = BackgroundJob('test-operation')
        started = job.start_result(lambda: {'ok': False, 'error': 'expired'})
        job.thread.join(2)
        state = job.snapshot()
        self.assertEqual(state['job_id'], started['job_id'])
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error'], 'expired')

    def test_operation_does_not_replay_while_running(self):
        job = BackgroundJob('test-operation')
        entered, release = threading.Event(), threading.Event()
        calls = []
        def work():
            calls.append(1)
            entered.set()
            release.wait(3)
            return {'ok': True, 'removed': 2}
        first = job.start_result(work)
        try:
            self.assertTrue(entered.wait(2))
            second = job.start_result(work)
            self.assertEqual(first['job_id'], second['job_id'])
        finally:
            release.set()
            job.thread.join(3)
        self.assertEqual(calls, [1])
        self.assertEqual(job.snapshot()['removed'], 2)

    """服务里的后台任务状态机。

    死链检查和资源对账共用这一份状态机。共用的不只是形状，还有几条容易漏的约定：
    重复点击不许把在跑的那轮丢掉、被顶掉的线程不许再写状态、后台异常必须变成
    可轮询的 failed。这些散在两处的话，下一个照抄的人漏哪条都不会有人发现。
    """

    def _job(self) -> BackgroundJob:
        return BackgroundJob("PeachTestJob", id_key="check_id")

    def test_a_fresh_job_has_no_state_at_all(self):
        """没跑过就是 None，而不是一个假装跑过的空状态。"""
        self.assertIsNone(self._job().snapshot())

    def test_repeated_clicks_do_not_replace_the_round_that_is_running(self):
        job = self._job()
        release = threading.Event()
        started = threading.Event()

        def work(_job_id: str) -> None:
            started.set()
            release.wait(5)

        first = job.start(work)
        started.wait(5)
        try:
            again = job.start(work, restart=True)
            self.assertEqual(again["check_id"], first["check_id"])
            self.assertEqual(again["status"], "running")
        finally:
            release.set()
        job.stop()

    def test_restart_is_required_to_replace_a_finished_round(self):
        job = self._job()
        job.start(lambda job_id: job.update(job_id, status="complete"))
        for _ in range(500):
            if job.snapshot()["status"] == "complete":
                break
            time.sleep(0.01)
        done = job.snapshot()
        self.assertEqual(done["status"], "complete")
        # 不带 restart 只是查现状：一份跑完的结果不该被一次误点清掉。
        self.assertEqual(job.start(lambda _id: None)["check_id"], done["check_id"])
        self.assertNotEqual(
            job.start(lambda _id: None, restart=True)["check_id"], done["check_id"])

    def test_a_superseded_worker_cannot_touch_the_state(self):
        """被顶掉的线程还继续写，前端就会看到新一轮的 id 配旧一轮的进度。"""
        job = self._job()
        stale = job.start(lambda _id: None, initial={"checked": 0})["check_id"]
        job.state = {"check_id": "fresh", "status": "running", "checked": 0}
        self.assertFalse(job.update(stale, checked=99))
        with job.editing(stale) as state:
            self.assertIsNone(state)
        self.assertEqual(job.snapshot()["checked"], 0)

    def test_a_raising_worker_becomes_a_pollable_failure(self):
        job = self._job()

        def work(_job_id: str) -> None:
            raise RuntimeError("上游没了")

        job.start(work)
        for _ in range(500):
            if job.snapshot()["status"] != "running":
                break
            time.sleep(0.01)
        state = job.snapshot()
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error"], "RuntimeError: 上游没了")
        self.assertIn("completed_at", state)

    def test_the_snapshot_is_deep_enough_to_project_outside_the_lock(self):
        """公开投影在锁外算，所以快照不能和 worker 共享同一个列表对象。"""
        job = self._job()
        job.state = {"check_id": "x", "status": "running", "gone": [{"id": 1}]}
        snapshot = job.snapshot()
        snapshot["gone"].append({"id": 2})
        snapshot["gone"][0]["id"] = 9
        self.assertEqual(job.state["gone"], [{"id": 1}])

    def test_stop_drops_the_state_and_waits_for_the_worker(self):
        job = self._job()
        release = threading.Event()
        finished = threading.Event()

        def work(_job_id: str) -> None:
            release.wait(5)
            finished.set()

        job.start(work)
        release.set()
        job.stop(timeout=5)
        self.assertTrue(finished.is_set())
        self.assertIsNone(job.snapshot())
