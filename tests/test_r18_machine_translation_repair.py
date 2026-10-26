"""早期 r18.dev 英文机翻修复：资产字段只改同源、未被用户接管的；实体只按快照日文那一侧改名。"""
import argparse
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.entities import upsert_asset_entity
from peach.migrations import upgrade
from scripts.repair_r18_machine_translations import (apply_entity_rows, apply_rows, collect,
                                                     collect_entities, run)


ROOT = Path(__file__).resolve().parents[1]


class LedgerFixture(unittest.TestCase):
    """临时账本与快照目录；两组用例共用，本身不带用例。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "ledger.db"
        self.snapshots = root / "snapshots"
        self.snapshots.mkdir()
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.row_factory = sqlite3.Row

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def add_asset(self, asset_id: int, code: str, title: str, series: str, owners: dict[str, str]):
        self.con.execute(
            "INSERT INTO asset(id,location,path,name,medium,code,catalog_title,series,field_owners) "
            "VALUES(?,'local',?,?,'video',?,?,?,?)",
            (asset_id, f"{asset_id}.mp4", f"{asset_id}.mp4", code, title, series,
             json.dumps(owners)),
        )
        self.con.commit()

    def link_series(self, asset_id: int, name: str) -> int:
        """按自动落库那条路（`metadata_auto_apply`）的来源写法建系列实体。"""
        entity_id = upsert_asset_entity(self.con, kind="series", name=name, asset_id=asset_id, role="series",
                                        source="javinizer:r18dev:series")
        self.con.commit()
        return entity_id

    def snapshot(self, code: str, *, title_machine=True, series_machine=True):
        path = self.snapshots / f"{code}-r18dev.json"
        path.write_text(json.dumps({
            "id": code,
            "combined": {
                "dvd_id": code,
                "title_en": "Machine title", "title_en_is_machine_translation": title_machine,
                "title_ja": "日本語タイトル",
                "series_name_en": "Machine series",
                "series_name_en_is_machine_translation": series_machine,
                "series_name_ja": "日本語シリーズ",
            },
            "translations": [{"language": "ja", "title": "日本語タイトル", "series": "日本語シリーズ"}],
        }, ensure_ascii=False), encoding="utf-8")


class R18MachineTranslationRepairTests(LedgerFixture):
    def test_collect_and_apply_replaces_both_verified_fields(self):
        self.add_asset(1, "ABC-001", "Machine title", "Machine series", {
            "catalog_title": "auto:r18dev", "series": "auto:r18dev",
        })
        self.snapshot("ABC-001")
        rows = collect(self.con, self.snapshots)
        self.assertEqual([row["action"] for row in rows], ["replace", "replace"])
        self.assertEqual(apply_rows(self.con, rows), {"assets": 1, "fields": 2})
        self.con.commit()
        asset = self.con.execute(
            "SELECT catalog_title,series,field_owners,mutation_revision FROM asset WHERE id=1").fetchone()
        self.assertEqual((asset["catalog_title"], asset["series"]),
                         ("日本語タイトル", "日本語シリーズ"))
        self.assertEqual(json.loads(asset["field_owners"]), {
            "catalog_title": "auto:r18dev", "series": "auto:r18dev",
        })
        self.assertEqual(asset["mutation_revision"], 1)

    def test_changed_or_protected_values_are_not_candidates(self):
        self.add_asset(2, "ABC-002", "User title", "Changed series", {
            "catalog_title": "user:manual", "series": "auto:r18dev",
        })
        self.snapshot("ABC-002")
        rows = collect(self.con, self.snapshots)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field"], "series")
        self.assertEqual(rows[0]["action"], "skip")
        self.assertEqual(rows[0]["reason"], "current-no-longer-matches-snapshot-english")

    def test_bilingual_snapshot_is_enough_when_r18_did_not_mark_the_english_as_machine_translation(self):
        self.add_asset(3, "ABC-003", "Machine title", "Machine series", {
            "catalog_title": "auto:r18dev", "series": "auto:r18dev",
        })
        self.snapshot("ABC-003", title_machine=False, series_machine=False)
        rows = collect(self.con, self.snapshots)
        self.assertEqual({row["action"] for row in rows}, {"replace"})
        self.assertEqual({row["machine_translation"] for row in rows}, {"no"})

    def test_a_latin_only_spacing_variant_is_not_mistaken_for_japanese(self):
        self.add_asset(5, "ABC-005", "Machine title", "1 VS 1", {
            "catalog_title": "auto:r18dev", "series": "auto:r18dev",
        })
        self.snapshot("ABC-005")
        payload = json.loads((self.snapshots / "ABC-005-r18dev.json").read_text(encoding="utf-8"))
        payload["combined"]["series_name_en"] = "1 VS 1"
        payload["combined"]["series_name_ja"] = "1VS1"
        (self.snapshots / "ABC-005-r18dev.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        rows = collect(self.con, self.snapshots)
        series = next(row for row in rows if row["field"] == "series")
        self.assertEqual(series["action"], "skip")
        self.assertEqual(series["reason"], "target-has-no-japanese-script")

    def test_dry_run_writes_audit_but_not_ledger_and_apply_requires_backup(self):
        self.add_asset(4, "ABC-004", "Machine title", "Machine series", {
            "catalog_title": "auto:r18dev", "series": "auto:r18dev",
        })
        self.snapshot("ABC-004")
        self.link_series(4, "Machine series")
        audit = Path(self.tmp.name) / "audit.csv"
        entity_audit = Path(self.tmp.name) / "entities.csv"
        args = argparse.Namespace(
            db=self.db, apply=False, backup=None, snapshots=self.snapshots, audit_csv=audit,
            javinizer_snapshots=Path(self.tmp.name) / "javinizer", entity_audit_csv=entity_audit,
        )
        self.assertEqual(run(args), 0)
        self.assertTrue(audit.is_file())
        with entity_audit.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual([(row["current"], row["action"]) for row in csv.DictReader(handle)],
                             [("Machine series", "rename")])
        self.assertEqual(self.con.execute(
            "SELECT catalog_title FROM asset WHERE id=4").fetchone()[0], "Machine title")
        self.assertEqual(self.con.execute(
            "SELECT canonical_name FROM entity WHERE kind='series'").fetchone()[0], "Machine series")
        args.apply = True
        with self.assertRaisesRegex(SystemExit, "--backup"):
            run(args)


class R18EntityNameRepairTests(LedgerFixture):
    """实体那一遍：r18 英文写法建出的系列与女优，按快照日文那一侧改名；证据不足或撞名只报不改。"""

    def rows(self):
        return collect_entities(self.con, self.snapshots, Path(self.tmp.name) / "javinizer")

    def test_a_translated_series_is_renamed_and_keeps_the_english_as_an_alias(self):
        self.add_asset(10, "ABC-010", "日本語タイトル", "Machine series", {"series": "auto:r18dev"})
        self.snapshot("ABC-010")
        entity_id = self.link_series(10, "Machine series")
        rows = self.rows()
        self.assertEqual([(row["entity_id"], row["action"], row["target"], row["reason"]) for row in rows],
                         [(entity_id, "rename", "日本語シリーズ", "verified-r18dev-japanese")])
        counts = apply_entity_rows(self.con, rows, "2026-09-28T00:00:00+00:00")
        self.assertEqual((counts["renamed"], counts["aliases"], counts["flat_rewritten"]), (1, 1, 1))
        self.assertEqual(self.con.execute(
            "SELECT canonical_name FROM entity WHERE id=?", (entity_id,)).fetchone()[0], "日本語シリーズ")
        self.assertEqual([tuple(row) for row in self.con.execute(
            "SELECT alias,source FROM entity_alias WHERE entity_id=?", (entity_id,))],
            [("Machine series", "script:repair_r18_machine_translations")])
        self.assertEqual(self.con.execute("SELECT series FROM asset WHERE id=10").fetchone()[0], "日本語シリーズ")

    def test_a_name_the_japanese_side_writes_the_same_way_is_the_original(self):
        """DMM 登记的艺名本来就写拉丁字母（``RARA``）；日文那一侧同名就是原名，不改。"""
        self.add_asset(11, "ABC-011", "日本語タイトル", "", {})
        (self.snapshots / "ABC-011-r18dev.json").write_text(json.dumps({
            "id": "ABC-011", "combined": {"dvd_id": "ABC-011", "actresses": [
                {"name_romaji": "RARA", "name_kanji": "RARA"}]}}), encoding="utf-8")
        upsert_asset_entity(self.con, kind="performer", name="RARA", asset_id=11, role="performer",
                            source="javinizer:r18dev:performer")
        rows = self.rows()
        self.assertEqual([(row["action"], row["reason"]) for row in rows], [("skip", "already-original")])

    def test_a_japanese_name_owned_by_another_entity_is_reported_for_merging(self):
        self.add_asset(12, "ABC-012", "日本語タイトル", "Machine series", {"series": "auto:r18dev"})
        self.snapshot("ABC-012")
        entity_id = self.link_series(12, "Machine series")
        self.add_asset(13, "ABC-013", "日本語タイトル", "日本語シリーズ", {})
        other = upsert_asset_entity(self.con, kind="series", name="日本語シリーズ", asset_id=13, role="series",
                                    source="manual:series")
        rows = self.rows()
        self.assertEqual([(row["entity_id"], row["action"], row["conflict_entity_id"]) for row in rows],
                         [(entity_id, "merge-needed", other)])
        self.assertEqual(apply_entity_rows(self.con, rows, "2026-09-28T00:00:00+00:00")["renamed"], 0)
        self.assertEqual(self.con.execute(
            "SELECT canonical_name FROM entity WHERE id=?", (entity_id,)).fetchone()[0], "Machine series")

    def test_without_a_snapshot_the_entity_is_only_reported(self):
        self.add_asset(14, "ABC-014", "日本語タイトル", "Machine series", {"series": "auto:r18dev"})
        self.link_series(14, "Machine series")
        self.assertEqual([(row["action"], row["reason"]) for row in self.rows()], [("skip", "missing-snapshot")])


if __name__ == "__main__":
    unittest.main()
