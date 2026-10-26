"""安装包与 Peach 自身更新、卸载机制之间的约定。"""
from pathlib import Path
import ctypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from peach import desktop_installer
from peach.windows_restart import TrayWindow

ISS = Path(__file__).resolve().parents[1] / "scripts" / "installer" / "peach.iss"


def iss_value(pattern: str) -> str:
    match = re.search(pattern, ISS.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise AssertionError(f"peach.iss 里没有 {pattern}")
    return match.group(1)


class InstallerContractTests(unittest.TestCase):
    def test_installer_registers_under_the_key_peach_cleans_up(self):
        self.assertEqual(iss_value(r"^AppId=\{\{([0-9A-F-]+)\}$"), desktop_installer.APP_ID)

    def test_start_menu_entry_is_the_shortcut_peach_removes(self):
        name = iss_value(r'^Name: "\{userprograms\}\\([^"]+)"; Filename: "\{app\}\\Peach\.exe"')
        self.assertEqual(name + ".lnk", desktop_installer.MENU_SHORTCUT)

    def test_directory_page_and_upgrade_directory_are_enabled(self):
        self.assertEqual(iss_value(r"^DisableDirPage=(.+)$"), "no")
        self.assertEqual(iss_value(r"^UsePreviousAppDir=(.+)$"), "yes")


@unittest.skipUnless(sys.platform == "win32", "安装器目录回归需要 Windows")
class InstallerDirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates = [os.environ.get("PEACH_ISCC"), shutil.which("ISCC.exe")]
        for base in (os.environ.get("ProgramFiles(x86)"),
                     str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs")):
            if base:
                candidates.append(str(Path(base) / "Inno Setup 6/ISCC.exe"))
        cls.compiler = next((p for p in candidates if p and Path(p).is_file()), None)
        if not cls.compiler:
            raise unittest.SkipTest("未取得 Inno Setup 编译器")
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.build = Path(temporary.name).resolve()
        fixture = ISS.parent / "directory-contract-test.iss"
        cls.compile(fixture, f"/O{cls.build}")

    @classmethod
    def compile(cls, script, *options):
        compiled = subprocess.run([cls.compiler, "/Q", *options, str(script)],
                                  capture_output=True, timeout=120)
        if compiled.returncode:
            raise AssertionError(compiled.stdout.decode(errors="replace") +
                                 compiled.stderr.decode(errors="replace"))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data = self.root / "data"
        self.data.mkdir()

    def check_directory(self, program, expected, *, previous="", data=None):
        result = self.root / "result.txt"
        result.unlink(missing_ok=True)
        subprocess.run([str(self.build / "directory-contract.exe"), "/VERYSILENT",
                        "/SUPPRESSMSGBOXES", "/NORESTART", f"/program={program}",
                        f"/data={data or self.data}", f"/previous={previous}",
                        f"/result={result}"], capture_output=True, timeout=30)
        self.assertEqual(result.read_text(), expected)

    def test_custom_directory_with_spaces_and_unicode(self):
        program = self.root / "自选 目录/Peach"
        self.check_directory(program, "ok")
        self.assertFalse(program.exists())

    def test_empty_existing_directory(self):
        program = self.root / "Peach"
        program.mkdir()
        self.check_directory(program, "ok")
        self.assertEqual(list(program.iterdir()), [])

    def test_data_directory_and_ancestors_and_children_are_rejected(self):
        for program in (self.data, self.root, self.data / "Peach",
                        self.data / "child/../Peach", str(self.data).upper()):
            with self.subTest(program=program):
                self.check_directory(program, "DirectoryOverlapsData")

    def test_sibling_with_the_same_prefix_is_independent(self):
        self.check_directory(self.root / "data-program", "ok")

    def test_short_path_alias_of_data_is_rejected(self):
        data = self.root / "Peach data directory"
        data.mkdir()
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.windll.kernel32.GetShortPathNameW(str(data), buffer, len(buffer))
        self.assertGreater(size, 0)
        self.check_directory(Path(buffer.value) / "Peach", "DirectoryOverlapsData", data=data)

    def test_directory_and_update_parent_must_be_writable(self):
        parent = self.root / "programs"
        program = parent / "Peach"
        program.mkdir(parents=True)
        for denied in (program, parent):
            with self.subTest(denied=denied):
                changed = subprocess.run(["icacls", str(denied), "/deny", "*S-1-1-0:(W)"],
                                         capture_output=True, timeout=10)
                try:
                    self.assertEqual(changed.returncode, 0, changed.stderr)
                    self.check_directory(program, "DirectoryNotWritable")
                finally:
                    restored = subprocess.run(["icacls", str(denied), "/remove:d", "*S-1-1-0"],
                                              capture_output=True, timeout=10)
                    self.assertEqual(restored.returncode, 0, restored.stderr)

    def test_unrelated_nonempty_directory_is_rejected(self):
        program = self.root / "media"
        program.mkdir()
        original = program / "personal.txt"
        original.write_text("keep")
        self.check_directory(program, "DirectoryNotDedicated")
        self.assertEqual(original.read_text(), "keep")

    def test_existing_installation_can_be_upgraded_in_place(self):
        program = self.root / "Peach"
        program.mkdir()
        for name in ("Peach.exe", "unins000.exe"):
            (program / name).touch()
        self.check_directory(program, "ok", previous=program)
        self.check_directory(self.root / "elsewhere", "DirectoryMove", previous=program)

    def test_directory_junction_is_rejected(self):
        alias = self.root / "alias"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.data)],
                              capture_output=True, timeout=10)
        self.assertEqual(made.returncode, 0, made.stderr)
        self.addCleanup(alias.rmdir)
        self.check_directory(alias / "Peach", "DirectoryLink")
        self.check_directory(self.root / "Peach", "DirectoryLink", data=alias)

    def test_production_installer_compiles_with_directory_validation(self):
        payload = self.root / "payload"
        payload.mkdir()
        (payload / "Peach.exe").write_bytes(b"compile fixture")
        self.compile(ISS, "/DAppVersion=0.0.0", f"/DSourceDir={payload}",
                     f"/DOutputDir={self.root}")
        self.assertTrue((self.root / "Peach-0.0.0-windows-x64-setup.exe").is_file())

    def test_lite_installer_compiles_from_the_application_update_package(self):
        from peach.update_packages import TOOL_PATHS
        from scripts.package_windows_release import package, build_lite_installer
        payload = self.root / 'payload'
        (payload / '_internal').mkdir(parents=True)
        (payload / 'Peach.exe').write_bytes(b'compile fixture')
        (payload / '_internal/standalone.txt').touch()
        for prefix, required in TOOL_PATHS.values():
            for name in required:
                target = payload / prefix / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'fixture')
        output = self.root / 'release'
        package(payload, output, '0.0.0')
        installer = build_lite_installer(output, '0.0.0', Path(self.compiler))
        self.assertTrue(installer.is_file())
        self.assertEqual(installer.name, 'Peach-0.0.0-windows-x64-lite-setup.exe')


class InstallerHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_update_carries_only_the_uninstaller_into_the_new_directory(self):
        old, new = self.root / "old", self.root / "new"
        old.mkdir(); new.mkdir()
        for name in ("unins000.exe", "unins000.dat", "unins000.msg", "Peach.exe", "unins.txt"):
            (old / name).write_text(name)
        desktop_installer.carry_uninstaller(old, new)
        self.assertEqual(sorted(path.name for path in new.iterdir()),
                         ["unins000.dat", "unins000.exe", "unins000.msg"])
        self.assertTrue(desktop_installer.installed(new))
        self.assertFalse(desktop_installer.installed(self.root))

    def test_entry_refuses_outside_a_standalone_windows_package(self):
        with patch.object(desktop_installer.distribution, "standalone", return_value=False):
            self.assertEqual(desktop_installer.main("--installer-stop"), 2)

    def test_stop_waits_for_the_tray_to_exit(self):
        windows = [(TrayWindow(10, 20),), (TrayWindow(10, 20),), ()]
        with patch("peach.windows_restart.find_tray_windows", side_effect=lambda _: windows.pop(0)), \
                patch("peach.windows_restart.post_stop") as post, \
                patch.object(desktop_installer, "_running_from", return_value=[]), \
                patch.object(desktop_installer.time, "sleep"):
            self.assertTrue(desktop_installer.stop(self.root))
        post.assert_called_once_with(20)

    def test_the_uninstaller_that_called_us_is_not_a_stray(self):
        program = self.root / "Peach"
        paths = {1: str(program / "unins000.exe"), 2: str(self.root / "temp" / "_unins.tmp"),
                 3: str(program / "Peach.exe"), 4: str(program / "_internal" / "ffmpeg.exe")}
        parents = {2: 1, 3: 2, 4: 99}
        with patch("peach.windows_restart._process_paths_and_parents", return_value=(paths, parents)), \
                patch.object(desktop_installer.os, "getpid", return_value=3):
            self.assertEqual(desktop_installer._running_from(program), [4])

    def test_stop_reports_a_tray_that_never_exits(self):
        with patch("peach.windows_restart.find_tray_windows", return_value=(TrayWindow(10, 20),)), \
                patch("peach.windows_restart.post_stop"), \
                patch.object(desktop_installer.time, "sleep"), \
                patch.object(desktop_installer.time, "monotonic", side_effect=[0, 0, 100]):
            self.assertFalse(desktop_installer.stop(self.root, timeout=1))

    def test_uninstall_also_withdraws_startup_and_desktop_shortcuts(self):
        with patch.object(desktop_installer.distribution, "standalone", return_value=True), \
                patch.object(desktop_installer.sys, "platform", "win32"), \
                patch.object(desktop_installer, "stop", return_value=True), \
                patch.object(desktop_installer, "remove_shortcuts") as remove:
            self.assertEqual(desktop_installer.main("--installer-stop"), 0)
            remove.assert_not_called()
            self.assertEqual(desktop_installer.main("--installer-uninstall"), 0)
            remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
