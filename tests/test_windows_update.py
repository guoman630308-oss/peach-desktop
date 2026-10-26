import importlib.util
import os
import subprocess
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import Mock

from peach.windows_update import sweep_onefile_extractions

from peach.windows_update import (
    BACKUP_GLOB,
    WindowsUpdateInstaller,
    swap_tray_binary,
    windows_tray_rebuild_required,
)


ROOT = Path(__file__).resolve().parents[1]


def load_replacer():
    spec = importlib.util.spec_from_file_location(
        "test_replace_windows_tray", ROOT / "scripts" / "replace_windows_tray.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsUpdateInstallerTests(unittest.TestCase):
    def make_tree(self, root: Path) -> tuple[Path, Path, Path]:
        (root / "scripts").mkdir()
        (root / "scripts" / "test.ps1").write_text("test", encoding="ascii")
        (root / "scripts" / "build_windows.ps1").write_text("build", encoding="ascii")
        (root / "scripts" / "replace_windows_tray.py").write_text("helper", encoding="ascii")
        (root / ".venv" / "Scripts").mkdir(parents=True)
        (root / ".venv" / "Scripts" / "pythonw.exe").write_bytes(b"python")
        target_dir = root / "dist" / "Peach"
        target_dir.mkdir(parents=True)
        target = target_dir / "Peach.exe"
        target.write_bytes(b"old")
        return target, root / "state", root / "logs"

    def test_rebuild_scope_includes_every_frozen_input(self):
        self.assertFalse(windows_tray_rebuild_required(("docs/STATUS.md",)))
        for path in (
            "src/peach/web.py", "web/index.html", "migrations/0021.sql",
            "resources/peach.ico", "scripts/build_windows.ps1", "pyproject.toml",
        ):
            self.assertTrue(windows_tray_rebuild_required((path,)), path)

    def test_pending_update_round_trips_and_clears(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, state, logs = self.make_tree(root)
            installer = WindowsUpdateInstaller(
                root, state_dir=state, log_dir=logs,
                current_executable=target, powershell="pwsh", frozen=False,
            )
            installer.mark_pending("abc12345", ("src/peach/tray.py",))
            pending = installer.pending()
            self.assertIsNotNone(pending)
            self.assertEqual(pending.commit, "abc12345")
            self.assertEqual(pending.changed_paths, ("src/peach/tray.py",))
            installer.clear_pending()
            self.assertIsNone(installer.pending())

    def test_service_only_update_runs_the_formal_suite_without_building(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, state, logs = self.make_tree(root)
            runner = Mock(return_value=subprocess.CompletedProcess([], 0))
            installer = WindowsUpdateInstaller(
                root, state_dir=state, log_dir=logs, current_executable=target,
                run=runner, powershell="pwsh", frozen=True,
            )
            result = installer.prepare("abc12345", ("docs/STATUS.md",))
            self.assertEqual(result.state, "services")
            self.assertEqual(runner.call_count, 1)
            self.assertIn("test.ps1", " ".join(runner.call_args.args[0]))
            self.assertEqual(runner.call_args.args[0][-3:], ["-Scope", "full", "-Fresh"])

    def test_tray_update_builds_validates_backs_up_and_launches_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, state, logs = self.make_tree(root)

            def run(command, **_kwargs):
                if "build_windows.ps1" in " ".join(command):
                    output = Path(command[command.index("-OutputDirectory") + 1])
                    output.mkdir(parents=True, exist_ok=True)
                    (output / "Peach.exe").write_bytes(b"new")
                return subprocess.CompletedProcess(command, 0)

            popen = Mock(return_value=Mock())
            installer = WindowsUpdateInstaller(
                root, state_dir=state, log_dir=logs, current_executable=target,
                run=run, popen=popen, powershell="pwsh", frozen=True, process_id=123,
            )
            installer.mark_pending("abc12345", ("src/peach/tray.py",))
            result = installer.prepare("abc12345", ("src/peach/tray.py",))
            self.assertEqual(result.state, "replace")
            backups = list(target.parent.glob("Peach.pre-source-sync-*.exe"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"old")
            helper_command = popen.call_args.args[0]
            self.assertIn("replace_windows_tray.py", helper_command[1])
            self.assertIn("--wait-pid", helper_command)
            self.assertEqual(target.read_bytes(), b"old", "running EXE is replaced only by helper")

    def test_failed_formal_suite_never_builds_or_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, state, logs = self.make_tree(root)
            popen = Mock()
            installer = WindowsUpdateInstaller(
                root, state_dir=state, log_dir=logs, current_executable=target,
                run=Mock(return_value=subprocess.CompletedProcess([], 1)), popen=popen,
                powershell="pwsh", frozen=True,
            )
            result = installer.prepare("abc12345", ("src/peach/tray.py",))
            self.assertEqual(result.state, "failed")
            installer._run.assert_called_once()
            popen.assert_not_called()
            self.assertEqual(target.read_bytes(), b"old")

    def test_invalid_verification_retries_and_requires_a_valid_record(self):
        for exits, expected in (([4, 0], "services"), ([4, 4, 4], "failed"), ([4, 1], "failed")):
            with self.subTest(exits=exits), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target, state, logs = self.make_tree(root)
                runner = Mock(side_effect=[subprocess.CompletedProcess([], code) for code in exits])
                popen = Mock()
                installer = WindowsUpdateInstaller(
                    root, state_dir=state, log_dir=logs, current_executable=target,
                    run=runner, popen=popen, powershell="pwsh", frozen=True,
                )
                result = installer.prepare("abc12345", ("docs/STATUS.md",))
                self.assertEqual(result.state, expected)
                self.assertEqual(runner.call_count, len(exits))
                for call in runner.call_args_list:
                    self.assertIn("test.ps1", " ".join(call.args[0]))
                popen.assert_not_called()
                self.assertEqual(target.read_bytes(), b"old")

    def test_sweep_keeps_recent_backups_and_pending_staging_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target, state, logs = self.make_tree(root)
            for stamp in ("20260901-090000", "20260902-090000", "20260903-090000"):
                (target.parent / f"Peach.pre-source-sync-{stamp}.exe").write_bytes(b"old")
            manual = target.parent / "Peach.pre-0.7.4-20260828-160731.exe"
            manual.write_bytes(b"manual")
            staging = state / "source-sync-build"
            for commit in ("aaaa1111", "bbbb2222"):
                (staging / commit).mkdir(parents=True)
                (staging / commit / "Peach.exe").write_bytes(b"staged")
            (staging / "windows-source-sync.log").write_text("log", encoding="ascii")
            installer = WindowsUpdateInstaller(
                root, state_dir=state, log_dir=logs,
                current_executable=target, powershell="pwsh", frozen=True,
            )
            installer.mark_pending("bbbb2222", ("src/peach/tray.py",))

            removed = installer.sweep_artifacts()

            self.assertEqual(
                sorted(path.name for path in removed),
                ["Peach.pre-source-sync-20260901-090000.exe", "aaaa1111"],
            )
            self.assertEqual(
                sorted(path.name for path in target.parent.glob("Peach.pre-source-sync-*.exe")),
                ["Peach.pre-source-sync-20260902-090000.exe",
                 "Peach.pre-source-sync-20260903-090000.exe"],
            )
            self.assertEqual(target.read_bytes(), b"old", "运行中的 EXE 本体不在清退边界内")
            self.assertTrue(manual.exists(), "只认更新器自己的命名，手工放的文件不碰")
            self.assertTrue((staging / "bbbb2222" / "Peach.exe").exists(), "待应用的暂存构建保留")
            self.assertTrue((staging / "windows-source-sync.log").exists(), "只删目录，不删文件")

            installer.clear_pending()
            removed = installer.sweep_artifacts()
            self.assertEqual([path.name for path in removed], ["bbbb2222"])
            self.assertEqual(
                sorted(path.name for path in staging.iterdir()), ["windows-source-sync.log"],
            )

    def test_sweep_is_a_no_op_without_update_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target, state, logs = self.make_tree(root)
            installer = WindowsUpdateInstaller(
                root, state_dir=state, log_dir=logs,
                current_executable=root / "missing" / "python.exe",
                powershell="pwsh", frozen=False,
            )
            self.assertEqual(installer.sweep_artifacts(), ())
            self.assertFalse(state.exists(), "清退不创建目录")


class OnefileExtractionSweepTests(unittest.TestCase):
    def test_removes_stale_sibling_extractions_only(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw).resolve()
            own = temp / "_MEI000001"
            stale = temp / "_MEI000002"
            fresh = temp / "_MEI000003"
            unrelated = temp / "tmpabc123"
            for directory in (own, stale, fresh, unrelated):
                directory.mkdir()
            (stale / "python314.dll").write_bytes(b"x")
            old = time.time() - 3 * 86400
            os.utime(own, (old, old))
            os.utime(stale, (old, old))
            os.utime(unrelated, (old, old))

            removed = sweep_onefile_extractions(str(own))

            self.assertEqual(removed, (stale,))
            # 自己的、刚起来的、以及别的程序的临时目录一律不碰。
            self.assertTrue(own.is_dir())
            self.assertTrue(fresh.is_dir())
            self.assertTrue(unrelated.is_dir())

    def test_source_tree_run_has_nothing_to_sweep(self):
        self.assertEqual(sweep_onefile_extractions(None), ())


class WindowsBuildScriptTests(unittest.TestCase):
    def test_build_script_removes_pyinstaller_work_directory(self):
        script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("--clean", script, "工作目录不复用，所以删掉它不损失任何东西")
        self.assertIn("--workpath $WorkPath", script)
        self.assertIn("Remove-Item -LiteralPath $WorkPath -Recurse -Force", script)


class WindowsTrayReplacerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.replacer = load_replacer()

    def paths(self, root: Path):
        target = root / "Peach.exe"
        staged = root / "staged.exe"
        backup = root / "Peach.pre-source-sync.exe"
        pending = root / "pending.json"
        log = root / "sync.log"
        target.write_bytes(b"old")
        backup.write_bytes(b"old")
        staged.write_bytes(b"new")
        pending.write_text("pending", encoding="ascii")
        return target, staged, backup, pending, log

    def test_successful_replacement_starts_new_tray_and_clears_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.paths(Path(directory))
            running = Mock()
            running.poll.return_value = None
            start = Mock(return_value=running)
            result = self.replacer.install_and_restart(
                wait_pid=123, staged=paths[1], target=paths[0], backup=paths[2],
                pending=paths[3], log=paths[4], alive=lambda _pid: False,
                start=start, sleep=lambda _delay: None,
            )
            self.assertEqual(result, 0)
            self.assertEqual(paths[0].read_bytes(), b"new")
            self.assertFalse(paths[3].exists())
            start.assert_called_once()

    def test_failed_new_tray_rolls_back_and_keeps_pending_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.paths(Path(directory))
            failed = Mock(returncode=7)
            failed.poll.return_value = 7
            restored = Mock()
            start = Mock(side_effect=[failed, restored])
            result = self.replacer.install_and_restart(
                wait_pid=123, staged=paths[1], target=paths[0], backup=paths[2],
                pending=paths[3], log=paths[4], alive=lambda _pid: False,
                start=start, sleep=lambda _delay: None,
            )
            self.assertEqual(result, 1)
            self.assertEqual(paths[0].read_bytes(), b"old")
            self.assertTrue(paths[3].exists())
            self.assertEqual(start.call_count, 2)


class SwapTrayBinaryTests(unittest.TestCase):
    def paths(self, root: Path) -> tuple[Path, Path]:
        target = root / "Peach.exe"
        staged = root / "staged" / "Peach.exe"
        staged.parent.mkdir()
        target.write_bytes(b"old")
        staged.write_bytes(b"new")
        return staged, target

    def test_the_old_binary_is_kept_under_the_name_the_sweeper_recognises(self):
        with tempfile.TemporaryDirectory() as directory:
            staged, target = self.paths(Path(directory).resolve())
            backup = swap_tray_binary(staged, target, sleep=lambda _delay: None)
            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual(backup.read_bytes(), b"old")
            self.assertEqual([backup], list(target.parent.glob(BACKUP_GLOB)))
            self.assertFalse(staged.exists(), "暂存包被原子移到生产入口")

    def test_a_target_that_is_not_the_production_entry_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            staged, target = self.paths(root)
            with self.assertRaises(ValueError):
                swap_tray_binary(staged, root / "Other.exe")
            with self.assertRaises(FileNotFoundError):
                swap_tray_binary(root / "missing.exe", target)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(target.parent.glob(BACKUP_GLOB)), [])

    def test_a_replacement_that_never_lands_leaves_no_orphan_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            staged, target = self.paths(Path(directory).resolve())
            with unittest.mock.patch("peach.windows_update.os.replace",
                                     side_effect=PermissionError("held")):
                with self.assertRaises(PermissionError):
                    swap_tray_binary(staged, target, timeout=0.0,
                                     sleep=lambda _delay: None)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(target.parent.glob(BACKUP_GLOB)), [],
                             "换不上就没有可回滚的东西，备份留着只会被当成一次成功的更新")
