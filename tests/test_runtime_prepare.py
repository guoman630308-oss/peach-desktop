from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from peach import runtime_prepare
from peach.runtime_prepare import (
    UV_SYNC, Dependencies, Migrations, RestartPreparation, Step, find_uv, ledger_writer,
    read_record, record, task_gate,
)
from peach.sync import marker_path


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


OUTDATED = (
    "Would use project environment at: .venv\n"
    "Would install 1 package\n"
    " + grpcio==1.84.0\n"
    "The environment is outdated; run `uv sync` to update the environment\n"
)


class FakeRun:
    """按调用顺序回放结果，同时记下每次的命令与关键字参数。"""

    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class ProjectTree:
    """临时项目根：`uv.lock` 与 `.venv/pyvenv.cfg` 都只是占位文件。"""

    def __init__(self, directory: Path, *, lock: bool = True, venv: bool = True):
        self.root = directory / "peach-app"
        self.root.mkdir()
        if lock:
            (self.root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        if venv:
            (self.root / ".venv").mkdir()
            (self.root / ".venv" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.directory.name)
        self.tree = ProjectTree(self.temp)

    def tearDown(self):
        self.directory.cleanup()

    def dependencies(self, run, **kwargs):
        options = {"which": lambda _name: "C:/tools/uv.exe",
                   "environ": {"VIRTUAL_ENV": "C:/elsewhere/.venv", "PATH": ""},
                   "frozen": False, "clock": iter((10.0, 12.5)).__next__}
        options.update(kwargs)
        return Dependencies(self.tree.root, run=run, **options)

    def test_a_venv_that_matches_the_lock_is_left_alone(self):
        run = FakeRun(completed(0, "Would make no changes\n"))
        step = self.dependencies(run).sync()
        self.assertEqual(step.state, "current")
        self.assertEqual(len(run.calls), 1)
        command, kwargs = run.calls[0]
        self.assertEqual(command[1:1 + len(UV_SYNC)], list(UV_SYNC))
        self.assertIn("--check", command)
        self.assertEqual(command[-2:], ["--project", str(self.tree.root)])
        # 调用方自己的 venv 不能顶替项目的 `.venv`。
        self.assertNotIn("VIRTUAL_ENV", kwargs["env"])

    def test_an_outdated_venv_is_synced_without_touching_the_editable_project(self):
        run = FakeRun(completed(1, OUTDATED), completed(0, "Installed 1 package\n"))
        step = self.dependencies(run).sync()
        self.assertEqual(step.state, "synced")
        self.assertIn("grpcio==1.84.0", step.message)
        self.assertIn("2.5 秒", step.message)
        sync_command = run.calls[1][0]
        self.assertNotIn("--check", sync_command)
        self.assertIn("--no-install-project", sync_command)
        self.assertIn("--inexact", sync_command)

    def test_check_alone_reports_stale_and_never_installs(self):
        run = FakeRun(completed(1, OUTDATED))
        step = self.dependencies(run).check()
        self.assertEqual(step.state, "stale")
        self.assertTrue(step.problem)
        self.assertEqual(len(run.calls), 1)

    def test_a_failed_sync_is_reported_with_the_tail_of_its_output(self):
        run = FakeRun(completed(1, OUTDATED),
                      completed(2, "", "error: failed to remove file numpy\\_core.pyd"))
        step = self.dependencies(run).sync()
        self.assertTrue(step.failed)
        self.assertIn("_core.pyd", step.message)

    def test_a_check_that_errors_for_another_reason_is_a_failure_not_stale(self):
        run = FakeRun(completed(2, "", "error: The lockfile needs to be updated"))
        step = self.dependencies(run).check()
        self.assertTrue(step.failed)
        self.assertIn("lockfile", step.message)

    def test_without_uv_nothing_runs(self):
        run = FakeRun()
        step = self.dependencies(run, which=lambda _name: None,
                                 environ={"LOCALAPPDATA": str(self.temp)}).sync()
        self.assertEqual(step.state, "skipped")
        self.assertIn("未取得 uv", step.message)
        self.assertEqual(run.calls, [])

    def test_uv_is_found_in_the_winget_package_directory_when_path_lacks_it(self):
        package = self.temp / "Microsoft" / "WinGet" / "Packages" / "astral-sh.uv_Microsoft.Winget.Source_x"
        package.mkdir(parents=True)
        (package / "uv.exe").write_bytes(b"")
        found = find_uv(which=lambda _name: None, environ={"LOCALAPPDATA": str(self.temp)})
        self.assertEqual(found, package / "uv.exe")

    def test_frozen_processes_and_trees_without_a_lock_or_venv_skip(self):
        run = FakeRun()
        self.assertEqual(self.dependencies(run, frozen=True).check().state, "skipped")
        (self.temp / "bare").mkdir()
        bare = ProjectTree(self.temp / "bare", lock=False)
        self.assertEqual(Dependencies(bare.root, run=run, frozen=False,
                                      which=lambda _name: "uv").check().state, "skipped")
        self.assertEqual(run.calls, [])


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.db = root / "local" / "ledger.db"
        self.shared = root / "shared" / "ledger.db"
        self.state = root / "state"
        for path in (self.db.parent, self.shared.parent, self.state):
            path.mkdir(parents=True)

    def tearDown(self):
        self.directory.cleanup()

    def mark(self, db: Path, device: str, generation: int = 1):
        marker_path(db).write_text(json.dumps({"generation": generation, "device": device}),
                                   encoding="utf-8")

    def writer(self, enabled=True):
        return ledger_writer(replication_enabled=enabled, db=self.db, shared_db=self.shared,
                             state_dir=self.state)

    def test_a_single_machine_is_its_own_writer(self):
        self.assertTrue(self.writer(enabled=False)[0])

    def test_a_reader_never_counts_as_writer(self):
        (self.state / "device-id").write_text("mac-1", encoding="utf-8")
        self.mark(self.shared, "win-1")
        writer, why = self.writer()
        self.assertFalse(writer)
        self.assertIn("win-1", why)

    def test_the_marked_device_is_the_writer(self):
        (self.state / "device-id").write_text("win-1", encoding="utf-8")
        self.mark(self.shared, "win-1")
        self.assertTrue(self.writer()[0])

    def test_no_device_id_means_undecided_and_none_is_written(self):
        self.mark(self.shared, "win-1")
        self.assertFalse(self.writer()[0])
        self.assertFalse((self.state / "device-id").exists())


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Path(self.directory.name) / "ledger.db"
        self.db.write_bytes(b"")

    def tearDown(self):
        self.directory.cleanup()

    def migrations(self, run, *, writer=(True, "本机是写入端")):
        return Migrations(self.db, peach=lambda: Path("C:/peach/.venv/Scripts/peach.exe"),
                          writer=lambda: writer, run=run,
                          environ=lambda: {"PEACH_DATA_ROOT": "C:/peach-data"})

    def test_a_reader_never_migrates(self):
        run = FakeRun()
        step = self.migrations(run, writer=(False, "写入端是 win-1，本机是只读端")).apply()
        self.assertEqual(step.state, "skipped")
        self.assertIn("只读端", step.message)
        self.assertEqual(run.calls, [])

    def test_a_missing_ledger_is_skipped(self):
        self.db.unlink()
        run = FakeRun()
        self.assertEqual(self.migrations(run).apply().state, "skipped")
        self.assertEqual(run.calls, [])

    def test_nothing_pending_runs_only_the_status(self):
        run = FakeRun(completed(0, f"database: {self.db}\nmigrations: 43, pending: 0\n"))
        step = self.migrations(run).apply()
        self.assertEqual(step.state, "current")
        self.assertEqual(len(run.calls), 1)
        command, kwargs = run.calls[0]
        self.assertEqual(command[1:], ["migrate", "status", "--db", str(self.db)])
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(kwargs["env"]["PEACH_DATA_ROOT"], "C:/peach-data")

    def test_pending_migrations_are_applied_with_the_backup_reported(self):
        backup = self.db.with_name("ledger.pre-migrate-20261002-101500.db")
        run = FakeRun(
            completed(0, "migrations: 43, pending: 2\nPENDING 0042 want\nPENDING 0043 x\n"),
            completed(0, f"database: {self.db}\nbackup: {backup}\napplied: 0042, 0043\n"),
        )
        step = self.migrations(run).apply()
        self.assertEqual(step.state, "applied")
        self.assertIn("0042, 0043", step.message)
        self.assertIn(str(backup), step.message)
        self.assertEqual(run.calls[1][0][1:],
                         ["migrate", "upgrade", "--yes", "--db", str(self.db)])

    def test_a_failed_upgrade_is_reported_not_swallowed(self):
        run = FakeRun(completed(0, "migrations: 43, pending: 1\n"),
                      completed(1, "", "sqlite3.OperationalError: database is locked"))
        step = self.migrations(run).apply()
        self.assertTrue(step.failed)
        self.assertIn("database is locked", step.message)
        self.assertIn("备份", step.message)

    def test_a_status_that_cannot_run_is_a_failure(self):
        step = self.migrations(FakeRun(OSError("peach.exe missing"))).apply()
        self.assertTrue(step.failed)
        self.assertIn("peach.exe missing", step.message)


class RecordTests(unittest.TestCase):
    def test_the_record_is_read_back_only_for_the_tray_that_wrote_it(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            written = record(state, [Step("migrations", "applied", "已执行迁移 0042"),
                                     Step("readiness", "current", "服务已就绪")], pid=4242)
            self.assertTrue(written["ok"])
            self.assertEqual(read_record(state, pid=4242)["steps"][0]["state"], "applied")
            self.assertIsNone(read_record(state, pid=1))

    def test_stale_dependencies_make_the_record_not_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            written = record(Path(directory), [Step("dependencies", "stale", "不一致")], pid=1)
            self.assertFalse(written["ok"])


class TaskGateTests(unittest.TestCase):
    HOST = "peach-win"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Path(self.directory.name) / "ledger.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "CREATE TABLE task_run(id INTEGER PRIMARY KEY, task_key TEXT, trigger TEXT, "
                "status TEXT, pid INTEGER, host TEXT, followup_key TEXT)")
            connection.executemany(
                "INSERT INTO task_run(id, task_key, trigger, status, pid, host, followup_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "entity-avatar", "manual", "pending", None, self.HOST, "avatar:7"),
                    (2, "follow-check", "scheduled", "running", 100, self.HOST, None),
                    (3, "library-processing", "manual", "running", 100, self.HOST, None),
                    (4, "scrape-codes", "cli", "running", 200, self.HOST, None),
                    (5, "media-repair", "manual", "running", 300, "peach-mac", None),
                    (6, "taste-refresh", "manual", "succeeded", 100, self.HOST, None),
                ])
        connection.close()

    def tearDown(self):
        self.directory.cleanup()

    def test_runs_are_split_into_resumable_blocking_and_stale(self):
        gate = task_gate(self.db, host=self.HOST, alive=lambda pid: pid == 100)
        self.assertEqual([run.id for run in gate.resumable], [1, 2])
        self.assertEqual([run.id for run in gate.blocking], [3])
        # CLI 那条的进程已经不在：重启伤不到它，不该拦。
        self.assertEqual([run.id for run in gate.stale], [4])
        self.assertTrue(gate.refused)
        self.assertIn("library-processing", gate.refusal())
        self.assertIn("--force", gate.refusal())

    def test_a_live_cli_run_blocks_too(self):
        gate = task_gate(self.db, host=self.HOST, alive=lambda _pid: True)
        self.assertEqual([run.task_key for run in gate.blocking],
                         ["library-processing", "scrape-codes"])

    def test_only_resumable_work_lets_the_restart_through(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("DELETE FROM task_run WHERE id IN (3, 4)")
        connection.close()
        gate = task_gate(self.db, host=self.HOST, alive=lambda _pid: True)
        self.assertFalse(gate.refused)
        self.assertEqual(len(gate.as_dict()["resumable"]), 2)

    def test_a_ledger_without_the_table_or_without_a_file_does_not_block(self):
        self.assertFalse(task_gate(self.db.with_name("none.db"), host=self.HOST,
                                   alive=lambda _pid: True).refused)
        empty = self.db.with_name("empty.db")
        sqlite3.connect(empty).close()
        gate = task_gate(empty, host=self.HOST, alive=lambda _pid: True)
        self.assertFalse(gate.refused)
        self.assertIn("task_run", gate.note)


class FakeDependencies:
    def __init__(self, checked: Step, synced: Step | None = None):
        self.checked, self.synced_step = checked, synced
        self.synced = 0

    def check(self):
        return self.checked

    def sync(self):
        self.synced += 1
        return self.synced_step


class RestartPreparationTests(unittest.TestCase):
    STALE = Step("dependencies", "stale", "项目 venv 与 uv.lock 不一致，要装 1 个包：grpcio==1.84.0")

    def test_a_current_venv_neither_refuses_nor_syncs(self):
        dependencies = FakeDependencies(Step("dependencies", "current", "一致"))
        holders = []
        preparation = RestartPreparation(dependencies, holders=lambda pid: holders.append(pid) or ())
        self.assertIsNone(preparation.before_stop(10))
        self.assertEqual(preparation.while_stopped(), (True, "一致"))
        self.assertEqual(dependencies.synced, 0)
        self.assertEqual(holders, [])

    def test_other_venv_users_refuse_the_restart_before_anything_stops(self):
        asked = []
        preparation = RestartPreparation(FakeDependencies(self.STALE),
                                         holders=lambda pid: asked.append(pid) or (48420,))
        refusal = preparation.before_stop(10)
        self.assertIn("48420", refusal)
        self.assertEqual(asked, [10])

    def test_a_stale_venv_is_synced_while_the_tray_is_stopped(self):
        dependencies = FakeDependencies(self.STALE, Step("dependencies", "synced", "已同步"))
        preparation = RestartPreparation(dependencies, holders=lambda _pid: ())
        self.assertIsNone(preparation.before_stop(10))
        self.assertEqual(dependencies.synced, 0)
        self.assertEqual(preparation.while_stopped(), (True, "已同步"))
        self.assertEqual(dependencies.synced, 1)

    def test_a_failed_sync_is_reported_as_not_ready(self):
        dependencies = FakeDependencies(self.STALE, Step("dependencies", "failed", "uv sync 失败"))
        preparation = RestartPreparation(dependencies, holders=lambda _pid: ())
        preparation.before_stop(10)
        self.assertEqual(preparation.while_stopped(), (False, "uv sync 失败"))


class ImportBoundaryTests(unittest.TestCase):
    def test_the_restart_path_loads_no_third_party_module(self):
        """重启脚本要在活着时替换 venv 里的包，被加载的 `.pyd` 在 Windows 上换不掉。"""
        probe = (
            "import sys; import peach.runtime_prepare, peach.windows_restart, peach.jobs, "
            "peach.config\n"
            "names = sorted(name for name, module in sys.modules.items()\n"
            "    if 'site-packages' in (getattr(module, '__file__', '') or '')\n"
            "    and not name.startswith(('_distutils_hack', '_virtualenv')))\n"
            "print(','.join(names))"
        )
        source_root = str(Path(runtime_prepare.__file__).parents[1])
        output = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], check=True,
                                capture_output=True, text=True, encoding="utf-8",
                                env={**os.environ, "PYTHONPATH": source_root})
        self.assertEqual(output.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
