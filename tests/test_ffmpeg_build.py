"""FFmpeg 对应源码归档与发行清单的行为验证。"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fetch_ffmpeg_sources.py"
SPEC = importlib.util.spec_from_file_location("ffmpeg_source_build", SCRIPT)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class FFmpegSourceBuildTests(unittest.TestCase):
    def test_source_lock_pins_all_linked_external_dependencies(self):
        lock = builder.load_sources()
        self.assertEqual(lock["version"], "9.0.2")
        self.assertEqual(lock["license"], "GPL-3.0-or-later")
        self.assertEqual(len(lock["sources"]), 4)
        self.assertIn('dav1d', {source['name'] for source in lock['sources']})

    def make_sources(self, output):
        lock = builder.load_sources()
        directory = output / "sources"
        directory.mkdir()
        for source in lock["sources"]:
            archive = directory / source["archive"]
            with tarfile.open(archive, "w:gz") as package:
                data = b"upstream source and license\n"
                entry = tarfile.TarInfo(f"{source['name']}-revision/README")
                entry.size = len(data)
                package.addfile(entry, io.BytesIO(data))
            source["sha256"] = builder.digest(archive)
        return lock

    def test_prepare_verifies_archives_before_extracting(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve()
            lock = self.make_sources(output)
            builder.prepare(output, lock)
            for source in lock["sources"]:
                self.assertTrue((output / "work" / source["name"] / "README").is_file())
            with self.assertRaises(FileExistsError):
                builder.prepare(output, lock)

    def test_prepare_rejects_source_archive_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve()
            lock = self.make_sources(output)
            (output / "sources" / lock["sources"][0]["archive"]).write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                builder.prepare(output, lock)
            self.assertFalse((output / "work/ffmpeg").exists())

    def test_finish_includes_sources_scripts_licenses_and_matching_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve()
            lock = self.make_sources(output)
            builder.prepare(output, lock)
            for path in ("ffmpeg/COPYING.GPLv3", "ffmpeg/COPYING.LGPLv2.1",
                         "ffmpeg/COPYING.LGPLv3", "x264/COPYING", "dav1d/COPYING"):
                (output / "work" / path).write_text("license", encoding="utf-8")
            headers = output / "work/nv-codec-headers/include/ffnvcodec"
            headers.mkdir(parents=True)
            (headers / "test.h").write_text("/* MIT license */\ncode", encoding="utf-8")
            (output / "ffmpeg/bin").mkdir(parents=True)
            for name in ("ffmpeg", "ffprobe"):
                (output / f"ffmpeg/bin/{name}.exe").write_bytes(b"MZ fixture")
            (output / "buildconf.mak").write_text("CONFIG_GPL=yes\n", encoding="utf-8")
            manifest = builder.finish(output, lock)
            source_zip = output / manifest["source_archive"]["name"]
            self.assertEqual(builder.digest(source_zip), manifest["source_archive"]["sha256"])
            for path, expected in manifest["files"].items():
                self.assertEqual(builder.digest(output / "ffmpeg" / path), expected)
            with zipfile.ZipFile(source_zip) as package:
                names = set(package.namelist())
                self.assertTrue({f"sources/{s['archive']}" for s in lock["sources"]} <= names)
                self.assertTrue({"scripts/build_ffmpeg_windows.sh", "scripts/fetch_ffmpeg_sources.py",
                                 "scripts/ffmpeg-sources.json", "README.md", "buildconf.txt"} <= names)
                self.assertFalse(any("manifest.json" in name for name in names))
            stored = json.loads((output / "ffmpeg/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, manifest)


if __name__ == "__main__":
    unittest.main()
