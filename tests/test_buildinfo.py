import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from peach.buildinfo import BUILD_INFO_NAME, frozen_build, read_build_info

ROOT = Path(__file__).resolve().parents[1]


class BuildInfoTests(unittest.TestCase):
    def write(self, root: Path, payload) -> None:
        (root / BUILD_INFO_NAME).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_a_packaged_build_carries_the_commit_it_was_made_from(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.write(root, {
                "commit": "1a2b3c4d5e6f7081920a1b2c3d4e5f6071829304",
                "version": "0.7.14",
                "built_at": "2026-09-05T10:00:00+08:00",
            })
            info = read_build_info(root)
            self.assertIsNotNone(info)
            self.assertEqual(info.commit, "1a2b3c4d5e6f7081920a1b2c3d4e5f6071829304")
            self.assertEqual(info.version, "0.7.14")
            self.assertEqual(info.built_at, "2026-09-05T10:00:00+08:00")

    def test_a_build_made_without_git_reports_no_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.write(root, {
                "commit": None, "version": "0.7.14", "built_at": "2026-09-05T10:00:00+08:00",
            })
            info = read_build_info(root)
            self.assertIsNotNone(info)
            self.assertIsNone(info.commit)

    def test_a_missing_or_damaged_file_never_stops_the_tray(self):
        """构建身份是产物不是配置：缺了只说明这个包比机制旧，托盘照样要起得来。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.assertIsNone(read_build_info(root))

            (root / BUILD_INFO_NAME).write_text("{截断", encoding="utf-8")
            self.assertIsNone(read_build_info(root))

            self.write(root, {"version": "0.7.14"})
            self.assertIsNone(read_build_info(root))

            self.write(root, ["0.7.14"])
            self.assertIsNone(read_build_info(root))

            self.write(root, {"commit": 12345, "version": "0.7.14", "built_at": "x"})
            self.assertIsNone(read_build_info(root))

    def test_source_checkouts_have_no_second_version_to_report(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(frozen_build())

    def test_a_frozen_process_reads_the_identity_from_its_own_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.write(root, {
                "commit": "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
                "version": "0.7.14",
                "built_at": "2026-09-05T10:00:00+08:00",
            })
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "_MEIPASS", str(root), create=True):
                info = frozen_build()
            self.assertIsNotNone(info)
            self.assertEqual(info.commit, "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432")

    def test_the_build_script_writes_the_identity_and_packs_it_at_the_bundle_root(self):
        """两种打包模式共用同一行 `--add-data`，所以本机托盘和独立测试包都带身份。"""
        script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("$BuildInfoPath = Join-Path $BuildPath 'build-info.json'", script)
        self.assertIn('--add-data "${BuildInfoPath};."', script)
        self.assertIn("rev-parse HEAD", script)
        self.assertLess(script.index("Set-Content -LiteralPath $BuildInfoPath"),
                        script.index("-m PyInstaller --noconfirm"))


if __name__ == "__main__":
    unittest.main()
