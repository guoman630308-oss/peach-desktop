import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FETCH = ROOT / "scripts" / "fetch_cloudflared.ps1"
PWSH = shutil.which("pwsh")


class CloudflaredPackagingTests(unittest.TestCase):
    def test_the_pinned_windows_asset_has_a_sha256_and_official_source(self):
        manifest = json.loads((ROOT / "scripts" / "cloudflared-windows.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["asset"], "cloudflared-windows-amd64.exe")
        self.assertRegex(manifest["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(manifest["url"].startswith("https://github.com/cloudflare/cloudflared/"))
        self.assertEqual(manifest["license"], "Apache-2.0")

    def test_only_the_standalone_build_copies_the_verified_sidecar(self):
        build = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8-sig")
        guarded = re.search(
            r"if \(\$Standalone\) \{(?:(?!\n\}).)*Copy-Item -LiteralPath \$CloudflaredSource",
            build, re.DOTALL,
        )
        self.assertIsNotNone(guarded, "sidecar 的复制必须留在 -Standalone 分支里")
        self.assertIn("Get-FileHash -LiteralPath $CloudflaredSource", build)


@unittest.skipIf(PWSH is None, "本机没有 pwsh，跳过实际执行 fetch_cloudflared.ps1")
class CloudflaredFetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "downloaded.exe"
        self.source.write_bytes(b"pretend cloudflared")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.destination = self.root / "vendor" / "cloudflared.exe"

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self, sha256: str) -> Path:
        path = self.root / "manifest.json"
        path.write_text(json.dumps({
            "asset": "cloudflared-windows-amd64.exe",
            "url": "https://github.com/cloudflare/cloudflared/releases/download/test/x.exe",
            "sha256": sha256,
            "version": "test",
            "license": "Apache-2.0",
        }), encoding="utf-8")
        return path

    def fetch(self, sha256: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PWSH, "-NoProfile", "-File", str(FETCH),
             "-OutputPath", str(self.destination),
             "-SourcePath", str(self.source),
             "-ManifestPath", str(self.manifest(sha256))],
            capture_output=True, text=True, timeout=120, check=False,
            # pwsh 的报错正文按系统区域编码输出，这里只判断脚本自己抛的那句 ASCII。
            encoding="utf-8", errors="replace",
        )

    def test_a_matching_hash_lands_the_file(self):
        result = self.fetch(self.digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.destination.read_bytes(), b"pretend cloudflared")

    def test_a_mismatched_hash_fails_and_leaves_nothing_behind(self):
        result = self.fetch("0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.glob("*")), [])


if __name__ == "__main__":
    unittest.main()
