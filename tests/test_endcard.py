import csv
import hashlib
import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.endcard import detect_endcard


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "audit_video_endcards.py"
    spec = importlib.util.spec_from_file_location("audit_video_endcards", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EndcardDetectionTests(unittest.TestCase):
    def test_full_version_endcard_is_an_incomplete_candidate(self):
        detection = detect_endcard(
            "Full version available on:\nfansly.com/smuzililpussy"
        )
        self.assertEqual(detection.verdict, "incomplete_candidate")
        self.assertTrue(detection.full_version)
        self.assertEqual(detection.urls, ("fansly.com/smuzililpussy",))
        self.assertEqual(detection.confidence, 0.98)

    def test_watermark_without_full_version_is_source_evidence(self):
        detection = detect_endcard("mirror.example.com @source_account")
        self.assertEqual(detection.verdict, "source_evidence")
        self.assertEqual(detection.handles, ("source_account",))

    def test_ordinary_subtitle_is_not_a_candidate(self):
        self.assertEqual(detect_endcard("More, I'm cumming.").verdict, "none")


class FakeOcr:
    def __init__(self):
        self.calls = 0

    def recognize(self, paths):
        self.calls += 1
        return {
            str(path.resolve()): {
                "path": str(path.resolve()),
                "text": (
                    "Full version available on: fansly.com/smuzililpussy"
                    if path.name.startswith("tail-") else "More, I'm cumming."
                ),
                "lines": [], "error": "",
            }
            for path in paths
        }


class EndcardAuditScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.module = load_script()
        self.db = self.tmp / "ledger.db"
        connection = sqlite3.connect(self.db)
        connection.execute(
            "CREATE TABLE asset(id INTEGER PRIMARY KEY,location TEXT,path TEXT,name TEXT,"
            "code TEXT,duration REAL,size INTEGER,medium TEXT,disposal TEXT)"
        )
        connection.execute(
            "INSERT INTO asset VALUES(1,'115',?,'sample.mp4','',507.6071,1000,'video',NULL)",
            (str(self.tmp / "sample.mp4"),),
        )
        connection.commit(); connection.close()
        self.evidence = self.tmp / "evidence"
        self.out = self.tmp / "video-endcard-candidate-test.csv"
        self.health = self.tmp / "video-endcard-health-test.csv"
        self.lock = self.tmp / "endcard.lock"

    def args(self, *extra):
        return self.module.build_parser().parse_args([
            "--db", str(self.db), "--asset", "1", "--min-free", "0",
            "--head-offsets", "0.5", "--tail-offsets", "2",
            "--evidence-root", str(self.evidence), "--out", str(self.out),
            "--health", str(self.health), "--lock", str(self.lock), *extra,
        ])

    @staticmethod
    def capture(_ffmpeg, _source, _timestamp, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"P" * 2048)
        return True

    def test_candidate_frames_and_ocr_are_cached_without_database_writes(self):
        ocr = FakeOcr()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = self.module.run(
            self.args(), capture=self.capture, ocr=ocr, ffmpeg_path="fake-ffmpeg",
        )
        self.assertEqual(result, 0)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        with self.out.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset_id"], "1")
        self.assertEqual(rows[0]["verdict"], "incomplete_candidate")
        self.assertEqual(rows[0]["detected_urls"], "fansly.com/smuzililpussy")
        self.assertEqual(rows[0]["sample_kind"], "tail")
        self.assertFalse(Path(rows[0]["frame_key"]).is_absolute())
        self.assertEqual(ocr.calls, 1)

        class NoOcr:
            def recognize(self, _paths):
                raise AssertionError("OCR cache should be reused")

        def no_capture(*_args):
            raise AssertionError("frame cache should be reused")

        result = self.module.run(
            self.args(), capture=no_capture, ocr=NoOcr(), ffmpeg_path="fake-ffmpeg",
        )
        self.assertEqual(result, 0)
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["frame_cache_reused"], "2")
        self.assertEqual(health["ocr_cache_reused"], "2")
        self.assertEqual(health["capture_errors"], "0")
        self.assertEqual(health["ocr_errors"], "0")

    def test_no_unbounded_default_batch(self):
        args = self.module.build_parser().parse_args([
            "--db", str(self.db), "--min-free", "0", "--out", str(self.out),
            "--health", str(self.health), "--evidence-root", str(self.evidence),
        ])
        result = self.module.run(args, ocr=FakeOcr(), ffmpeg_path="fake-ffmpeg")
        self.assertEqual(result, 2)
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["last_error_kind"], "ValueError")

    def test_sample_points_cover_head_and_tail_without_duplicates(self):
        points = self.module.sample_points(10, (0.5, 2), (0.5, 2, 20))
        self.assertEqual(points, [
            ("head", 0.5), ("head", 2), ("tail", 9.5), ("tail", 8),
        ])


if __name__ == "__main__":
    unittest.main()
