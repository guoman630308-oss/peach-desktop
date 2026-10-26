import json
from pathlib import Path
import tempfile
import unittest

from scripts import media_tool_bundle as ffmpeg_bundle


class FFmpegBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "build" / "ffmpeg"
        self.source.mkdir(parents=True)
        self.manifest = {"version": "9.0.2", "license": "GPL-3.0-or-later", "files": {}}
        for name in ffmpeg_bundle.REQUIRED_FILES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            self.manifest["files"][name] = ffmpeg_bundle.digest(path)
        self.manifest["sources"] = [
            {"name": name, "revision": "fixed", "url": f"https://example.org/{name}.tar.gz", "sha256": "1" * 64}
            for name in ("ffmpeg", "x264", "nv-codec-headers", "dav1d")]
        archive = self.source.parent / "ffmpeg-corresponding-source.zip"
        archive.write_bytes(b"source fixture")
        self.manifest["source_archive"] = {"name": archive.name, "sha256": ffmpeg_bundle.digest(archive)}
        self.destination = self.root / "dist" / "Peach" / "tools" / "ffmpeg"
        self.write_manifest()

    def write_manifest(self):
        (self.source / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_packages_verified_tools_not_ffplay_and_keeps_source_beside_application(self):
        result = ffmpeg_bundle.package(self.source, self.destination)
        self.assertEqual(result["version"], "9.0.2")
        self.assertEqual((self.destination / "bin/ffmpeg.exe").read_bytes(), b"bin/ffmpeg.exe")
        self.assertTrue((self.destination / "bin/ffprobe.exe").is_file())
        self.assertFalse((self.destination / "bin/ffplay.exe").exists())
        self.assertEqual((self.root / "dist/ffmpeg-corresponding-source.zip").read_bytes(), b"source fixture")

    def test_rejects_corrupted_binary_without_copying(self):
        (self.source / "bin/ffprobe.exe").write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            ffmpeg_bundle.package(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_rejects_corrupted_or_missing_corresponding_source(self):
        archive = self.source.parent / self.manifest["source_archive"]["name"]
        for content in (b"corrupt", None):
            if content is None:
                archive.unlink()
            else:
                archive.write_bytes(content)
            with self.assertRaises(ValueError):
                ffmpeg_bundle.package(self.source, self.destination)
            self.assertFalse(self.destination.exists())

    def test_rejects_incomplete_sources_and_unlisted_files(self):
        self.manifest["sources"].pop()
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "源码来源"):
            ffmpeg_bundle.validate(self.source)
        (self.source / "bin/ffplay.exe").write_bytes(b"unverified")
        with self.assertRaisesRegex(ValueError, "目录与清单"):
            ffmpeg_bundle.validate(self.source)

    def test_rejects_source_archive_path_escape(self):
        outside = self.root / "outside.zip"
        outside.write_bytes(b"source fixture")
        self.manifest["source_archive"]["name"] = "../outside.zip"
        self.write_manifest()
        with self.assertRaises(ValueError):
            ffmpeg_bundle.validate(self.source)

    def test_rejects_stale_destination(self):
        self.destination.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "必须不存在"):
            ffmpeg_bundle.package(self.source, self.destination)

    def test_untrunc_uses_the_same_integrity_gate_with_its_own_source_and_license(self):
        self.manifest["license"] = "GPL-2.0-or-later"
        (self.source / "bin/ffprobe.exe").unlink()
        (self.source / "bin/ffmpeg.exe").unlink()
        self.manifest["files"].pop("bin/ffmpeg.exe")
        self.manifest["files"].pop("bin/ffprobe.exe")
        (self.source / "untrunc.exe").write_bytes(b"untrunc")
        self.manifest["files"]["untrunc.exe"] = ffmpeg_bundle.digest(self.source / "untrunc.exe")
        self.manifest["sources"] = [s for s in self.manifest["sources"] if s["name"] in ('ffmpeg', 'nv-codec-headers')]
        self.manifest["sources"][1]["name"] = "untrunc"
        archive = self.source.parent / "ffmpeg-corresponding-source.zip"
        archive.rename(self.source.parent / "untrunc-corresponding-source.zip")
        self.manifest["source_archive"]["name"] = "untrunc-corresponding-source.zip"
        self.write_manifest()
        result = ffmpeg_bundle.package(self.source, self.destination, "untrunc")
        self.assertEqual(result["license"], "GPL-2.0-or-later")
        self.assertEqual((self.destination / "untrunc.exe").read_bytes(), b"untrunc")

