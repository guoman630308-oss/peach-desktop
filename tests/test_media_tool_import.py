import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")


@unittest.skipUnless(os.name == "nt" and PWSH, "Windows PowerShell tool import")
class MediaToolImportTests(unittest.TestCase):
    def test_imports_installed_tools_companion_dlls_and_preserves_existing_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            data = root / "Peach data"
            data.mkdir()
            installed = root / "WinGet/Packages/Gyan.FFmpeg/version/bin"
            installed.mkdir(parents=True)
            for name in ("ffmpeg.exe", "ffprobe.exe", "avcodec.dll"):
                (installed / name).write_bytes(b"MZ" + name.encode())
            repair = root / "WinGet/Packages/untrunc"
            repair.mkdir()
            (repair / "untrunc.exe").write_bytes(b"MZrepair")
            target = data / "tools/ffmpeg/bin"
            target.mkdir(parents=True)
            (target / "ffmpeg.exe").write_bytes(b"MZold")
            result = subprocess.run([PWSH, "-NoProfile", "-File",
                str(ROOT / "scripts/import_windows_media_tools.ps1"),
                "-DataRoot", str(data), "-SearchRoots", str(root / "WinGet")],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name in ("ffmpeg.exe", "ffprobe.exe", "avcodec.dll"):
                self.assertEqual((target / name).read_bytes(), (installed / name).read_bytes())
            self.assertEqual((data / "tools/untrunc/untrunc.exe").read_bytes(), b"MZrepair")
            backups = list((data / "tools").glob("import-backup-*/ffmpeg.exe"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"MZold")

    def test_missing_download_does_not_claim_ready_or_touch_other_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "ledger.db").write_bytes(b"kept")
            result = subprocess.run([PWSH, "-NoProfile", "-File",
                str(ROOT / "scripts/import_windows_media_tools.ps1"),
                "-DataRoot", str(root), "-SearchRoots", str(root)],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("NOT FOUND", result.stdout)
            self.assertEqual((root / "ledger.db").read_bytes(), b"kept")
            self.assertFalse((root / "tools").exists())
