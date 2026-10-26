"""系列日文规范名迁移：官方证据、冲突跳过、别名与兼容投影。"""
import argparse
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.migrations import upgrade
from scripts.localize_series_names import apply_rows, collect, read_evidence, run


ROOT = Path(__file__).resolve().parents[1]


class SeriesLocalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,code,series) "
            "VALUES(?,'local',?,?,'video',?,?)",
            [(1, "1.mp4", "1.mp4", "ABC-001", "Raw Creampies"),
             (2, "2.mp4", "2.mp4", "DEF-002", "Japanese Existing"),
             (3, "3.mp4", "3.mp4", "GHI-003", "wrong-flat-value"),
             (4, "4.mp4", "4.mp4", "JKL-004", "Conflicting Series"),
             (5, "5.mp4", "5.mp4", "MNO-005", "Conflicting Series"),
             (6, "6.mp4", "6.mp4", "PQR-006", "Prestige English")],
        )
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,'series',?,?, 't','t')",
            [(10, "Raw Creampies", "raw creampies"),
             (11, "なまなかだし", "なまなかだし"),
             (12, "Japanese Existing", "japanese existing"),
             (13, "Projection Mismatch", "projection mismatch"),
             (14, "Conflicting Series", "conflicting series"),
             (15, "Prestige English", "prestige english")],
        )
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(?,?,'series','r18:series',1.0)",
            [(1, 10), (2, 12), (3, 13), (4, 14), (5, 14), (6, 15)],
        )
        self.con.commit()
        self.candidates = root / "candidates.csv"
        self.raw = root / "raw"
        self._write_candidate("ABC-001", "なまなかだし")
        self._write_candidate("DEF-002", "なまなかだし")
        self._write_candidate("GHI-003", "投影")
        self._write_candidate("JKL-004", "企画一")
        self._write_candidate("MNO-005", "企画二", append=True)
        self._write_candidate("PQR-006", "【プレステージ企画】", append=True)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _write_candidate(self, code, japanese, append=False):
        snapshot = self.raw / code / "r18dev.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps({
            "source": "r18dev",
            "result": {"translations": [{"language": "ja-JP", "series": japanese}]},
        }, ensure_ascii=False), encoding="utf-8")
        fields = ["code", "query", "field", "candidates_json"]
        mode = "a" if append or self.candidates.exists() else "w"
        with self.candidates.open(mode, encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if mode == "w":
                writer.writeheader()
            writer.writerow({
                "code": code, "query": code, "field": "series",
                "candidates_json": json.dumps([{
                    "source": "r18dev", "source_kind": "official_mirror",
                    "official": True, "value": japanese,
                    "raw_snapshot": str(snapshot),
                    "source_url": f"https://r18.dev/videos/vod/movies/detail/-/id={code}/",
                }], ensure_ascii=False),
            })

    def plan(self):
        return collect(self.con, read_evidence(self.candidates), "r18-test")

    def test_collect_accepts_only_unique_official_japanese_and_skips_mismatch(self):
        rows = {int(row["entity_id"]): row for row in self.plan()}
        self.assertEqual(rows[10]["action"], "merge-into-existing")
        self.assertEqual(rows[10]["merge_target_id"], 11)
        self.assertEqual(rows[12]["action"], "merge-into-existing")
        self.assertEqual(rows[12]["merge_target_id"], 11)
        self.assertEqual(rows[13]["action"], "skip-projection-mismatch")
        self.assertEqual(rows[14]["action"], "skip-evidence-conflict")
        self.assertEqual(rows[15]["action"], "rename")

    def test_apply_merges_preserves_old_names_and_syncs_flat_projection(self):
        counts = apply_rows(self.con, self.plan(), "r18-test")
        self.con.commit()
        self.assertIsNone(self.con.execute("SELECT id FROM entity WHERE id=10").fetchone())
        self.assertIsNone(self.con.execute("SELECT id FROM entity WHERE id=12").fetchone())
        aliases = {row[0] for row in self.con.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=11")}
        self.assertTrue({"Raw Creampies", "Japanese Existing"} <= aliases)
        projections = dict(self.con.execute(
            "SELECT id,series FROM asset WHERE id IN (1,2) ORDER BY id"))
        self.assertEqual(projections, {1: "なまなかだし", 2: "なまなかだし"})
        metadata = json.loads(self.con.execute(
            "SELECT metadata_json FROM entity WHERE id=11").fetchone()[0])
        self.assertEqual(metadata["series_name_localization"]["source"], "r18dev")
        renamed = self.con.execute(
            "SELECT canonical_name FROM entity WHERE id=15").fetchone()[0]
        self.assertEqual(renamed, "【プレステージ企画】")
        self.assertEqual(self.con.execute(
            "SELECT series FROM asset WHERE id=6").fetchone()[0], "【プレステージ企画】")
        self.assertEqual(self.con.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=15").fetchone()[0],
                         "Prestige English")
        self.assertEqual(counts["merged"], 2)
        self.assertEqual(counts["renamed"], 1)
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_raw_snapshot_without_japanese_translation_is_rejected(self):
        snapshot = self.raw / "NOJ-006" / "r18dev.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(json.dumps({
            "source": "r18dev", "result": {"series": "English fallback"},
        }), encoding="utf-8")
        extra = Path(self.tmp.name) / "no-japanese.csv"
        with extra.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["code", "query", "field", "candidates_json"])
            writer.writeheader()
            writer.writerow({
                "code": "NOJ-006", "query": "NOJ-006", "field": "series",
                "candidates_json": json.dumps([{
                    "source": "r18dev", "source_kind": "official_mirror",
                    "official": True, "value": "English fallback",
                    "raw_snapshot": str(snapshot),
                }]),
            })
        self.assertEqual(read_evidence(extra), {})

    def test_apply_requires_backup(self):
        args = argparse.Namespace(
            db=self.db, candidates=self.candidates, revision="r18-test",
            audit_csv=Path(self.tmp.name) / "audit.csv", apply=True, backup=None,
        )
        with self.assertRaises(SystemExit):
            run(args)


if __name__ == "__main__":
    unittest.main()
