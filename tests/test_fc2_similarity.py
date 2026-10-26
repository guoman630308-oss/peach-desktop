import csv
import hashlib
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.fc2_similarity import build_candidates


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "audit_fc2_similarity.py"
    spec = importlib.util.spec_from_file_location("audit_fc2_similarity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def asset(asset_id: int, video_id: str, **over) -> dict:
    row = {
        "id": asset_id, "code": f"FC2-PPV-{video_id}",
        "name": f"FC2-PPV-{video_id}.mp4", "size": 1_000_000_000,
        "duration": 1000.0, "width": 1920, "height": 1080, "hash": "",
    }
    row.update(over)
    return row


class Fc2SimilarityPolicyTests(unittest.TestCase):
    def test_comment_equivalent_keeps_external_counterpart_as_review_evidence(self):
        rows = build_candidates(
            [asset(1, "1083921")],
            {
                "1083921": {"equivalents": "1384193", "seen_on": "1083921"},
                "1384193": {"equivalents": "1083921", "seen_on": "1083921"},
            },
            {"1083921"},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pair_key"], "1083921|1384193")
        self.assertEqual(rows[0]["evidence_kinds"], "comment_equivalent")
        self.assertEqual(rows[0]["left_owned"], "1")
        self.assertEqual(rows[0]["right_owned"], "")
        self.assertIn("合集", rows[0]["warnings"])
        self.assertIn("尚不在本地", rows[0]["warnings"])

    def test_exact_hash_across_numbers_is_inferred_but_never_merged(self):
        rows = build_candidates([
            asset(1, "1111111", hash="sha1"),
            asset(2, "2222222", hash="sha1"),
        ], {})
        self.assertEqual(len(rows), 1)
        self.assertIn("exact_hash", rows[0]["evidence_kinds"])
        self.assertEqual(rows[0]["confidence"], 1.0)
        self.assertEqual(rows[0]["status"], "candidate")
        self.assertEqual(rows[0]["inferred"], "1")

    def test_media_similarity_requires_independent_performer_overlap(self):
        assets = [
            asset(1, "1111111"),
            asset(2, "2222222", duration=1001.0, size=1_010_000_000),
        ]
        harvest = {
            "1111111": {"performers": "真夏"},
            "2222222": {"performers": "真夏"},
        }
        rows = build_candidates(assets, harvest)
        self.assertEqual(len(rows), 1)
        self.assertIn("media_similarity", rows[0]["evidence_kinds"])
        self.assertEqual(rows[0]["shared_performers"], "真夏")
        self.assertEqual(build_candidates(assets, {}), [])

    def test_conflicting_part_markers_block_mechanical_inference(self):
        assets = [
            asset(1, "1111111", name="FC2-PPV-1111111-1.mp4"),
            asset(2, "2222222", name="FC2-PPV-2222222-2.mp4"),
        ]
        harvest = {
            "1111111": {"performers": "真夏"},
            "2222222": {"performers": "真夏"},
        }
        self.assertEqual(build_candidates(assets, harvest), [])


class Fc2SimilarityScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.module = load_script()
        self.db = self.tmp / "ledger.db"
        connection = sqlite3.connect(self.db)
        connection.execute(
            "CREATE TABLE asset(id INTEGER PRIMARY KEY,code TEXT,name TEXT,size INTEGER,"
            "duration REAL,width INTEGER,height INTEGER,hash TEXT,medium TEXT,disposal TEXT)"
        )
        connection.executemany(
            "INSERT INTO asset VALUES(?,?,?,?,?,?,?,?,?,NULL)",
            [
                (1, "FC2-PPV-1083921", "a.mp4", 1000, 100.0, 1920, 1080, "", "video"),
                (2, "FC2-PPV-1384193", "b.mp4", 1000, 100.0, 1920, 1080, "", "video"),
            ],
        )
        connection.commit(); connection.close()
        self.harvest = self.tmp / "fc2-comment-harvest.csv"
        self.metadata = self.tmp / "fc2-candidate-log.csv"
        self.out = self.tmp / "fc2-similarity-candidate-test.csv"
        self.evidence = self.tmp / "fc2-similarity-evidence-test.csv"
        self.health = self.tmp / "fc2-similarity-health-test.csv"
        self._write(self.harvest,
                    ["video_id", "owned", "performers", "performer_votes",
                     "equivalents", "seen_on"],
                    [
                        {"video_id": "1083921", "owned": "1", "performers": "真夏",
                         "equivalents": "1384193", "seen_on": "1083921"},
                        {"video_id": "1384193", "owned": "1", "performers": "真夏",
                         "equivalents": "1083921", "seen_on": "1083921"},
                    ])
        self._write(self.metadata, ["code", "video_id", "is_collection"], [])

    @staticmethod
    def _write(path: Path, fields: list[str], rows: list[dict]):
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)

    def args(self):
        return self.module.build_parser().parse_args([
            "--db", str(self.db), "--harvest", str(self.harvest),
            "--metadata", str(self.metadata), "--out", str(self.out),
            "--evidence", str(self.evidence), "--health", str(self.health),
        ])

    def test_script_writes_candidates_and_health_without_touching_database(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(self.module.run(self.args()), 0)
        after = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(after, before)
        with self.out.open(encoding="utf-8-sig", newline="") as handle:
            candidates = list(csv.DictReader(handle))
        self.assertEqual([row["pair_key"] for row in candidates], ["1083921|1384193"])
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["candidates"], "1")
        self.assertEqual(health["evidence_pairs"], "1")
        self.assertEqual(health["deferred_external_pairs"], "0")
        self.assertEqual(health["errors"], "0")

    def test_missing_harvest_fails_with_health_report(self):
        self.harvest.unlink()
        self.assertEqual(self.module.run(self.args()), 2)
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["errors"], "1")
        self.assertEqual(health["last_error_kind"], "FileNotFoundError")

    def test_external_counterpart_stays_in_evidence_not_review(self):
        connection = sqlite3.connect(self.db)
        connection.execute("DELETE FROM asset WHERE id=2")
        connection.commit(); connection.close()
        self.assertEqual(self.module.run(self.args()), 0)
        with self.out.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])
        with self.evidence.open(encoding="utf-8-sig", newline="") as handle:
            evidence = list(csv.DictReader(handle))
        self.assertEqual([row["pair_key"] for row in evidence], ["1083921|1384193"])
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["deferred_external_pairs"], "1")


if __name__ == "__main__":
    unittest.main()
