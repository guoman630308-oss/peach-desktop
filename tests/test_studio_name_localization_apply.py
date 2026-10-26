"""厂牌名换回日文原名：四处一起改，复核件过期的行与撞名的行只报不改。"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from peach.review_csv import read_rows, write_rows   # noqa: E402

from support.ledger import fresh_ledger   # noqa: E402


def load_script():
    path = ROOT / "scripts" / "apply_studio_name_localization.py"
    spec = importlib.util.spec_from_file_location("apply_studio_name_localization", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_script()
REVIEW_FIELDS = ("entity_id", "studio", "verdict", "proposed")


class LocalizationApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()
        self.db = fresh_ledger(self.dir)
        self.logos = self.dir / "logos"
        self.logos.mkdir()
        self.review = self.dir / "review.csv"
        self.audit = self.dir / "audit.csv"
        self.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(1,'studio','Celeb no Tomo','celeb no tomo','t','t'),"
            " (2,'studio','Hyoko','hyoko','t','t'),"
            " (3,'studio','ひよこ','ひよこ','t','t'),"
            " (4,'studio','Toyohiko','toyohiko','t','t'),"
            " (5,'studio','豊彦企画','豊彦企画','t','t')")
        self.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(5,'豊彦','豊彦','test',1.0)")
        self.execute(
            "INSERT INTO asset(id,location,path,name,medium,studio) VALUES"
            " (10,'R','R:\\a.mp4','a.mp4','video','Celeb no Tomo'),"
            " (11,'R','R:\\b.mp4','b.mp4','video','Celeb no Tomo'),"
            " (12,'R','R:\\c.mp4','c.mp4','video','Hyoko')")

    def tearDown(self):
        self.tmp.cleanup()

    def execute(self, sql, params=()):
        connection = sqlite3.connect(self.db)
        with connection:
            connection.execute(sql, params)
        connection.close()

    def query(self, sql, params=()):
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def plan(self, *rows):
        write_rows(self.review, REVIEW_FIELDS, [dict(zip(REVIEW_FIELDS, row)) for row in rows])

    def run_script(self, *extra):
        args = MODULE.build_parser().parse_args(
            ["--db", str(self.db), "--plan", str(self.review),
             "--audit-csv", str(self.audit), *extra])
        with contextlib.redirect_stdout(io.StringIO()):
            return MODULE.run(args)

    def apply(self):
        return self.run_script("--apply", "--backup", str(self.dir / "backup.db"),
                               "--logo-root", str(self.logos))

    def audit_rows(self):
        return {row["current_name"]: row for row in read_rows(self.audit)}

    def test_rename_moves_the_name_the_alias_and_the_flat_projection_together(self):
        self.plan((1, "Celeb no Tomo", "改名", "セレブの友"))
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.query("SELECT canonical_name,normalized_name FROM entity WHERE id=1"),
                         [("セレブの友", "セレブの友")])
        self.assertEqual(self.query("SELECT alias,source FROM entity_alias WHERE entity_id=1"),
                         [("Celeb no Tomo", MODULE.ALIAS_SOURCE)])
        self.assertEqual(self.query("SELECT id FROM asset WHERE studio='セレブの友' ORDER BY id"),
                         [(10,), (11,)])
        self.assertEqual(self.query("SELECT studio FROM asset WHERE id=12"), [("Hyoko",)])

    def test_a_review_table_from_another_round_records_its_own_source_and_evidence(self):
        """官方名录那一轮的复核表不是 javbus 出的：别名来源和审计原因都照实记。"""
        write_rows(self.review, (*REVIEW_FIELDS, "evidence"),
                   [{"entity_id": 1, "studio": "Celeb no Tomo", "verdict": "改名",
                     "proposed": "セレブの友", "evidence": "官网名录写作セレブの友"}])
        self.assertEqual(self.run_script("--apply", "--backup", str(self.dir / "backup.db"),
                                         "--alias-source", "review:names.csv"), 0)
        self.assertEqual(self.query("SELECT source FROM entity_alias WHERE entity_id=1"),
                         [("review:names.csv",)])
        self.assertEqual(self.audit_rows()["Celeb no Tomo"]["reason"], "官网名录写作セレブの友")

    def test_dry_run_writes_the_audit_but_not_the_ledger(self):
        self.plan((1, "Celeb no Tomo", "改名", "セレブの友"))
        self.assertEqual(self.run_script(), 0)
        self.assertEqual(self.audit_rows()["Celeb no Tomo"]["action"], MODULE.RENAME)
        self.assertEqual(self.query("SELECT canonical_name FROM entity WHERE id=1"),
                         [("Celeb no Tomo",)])
        self.assertEqual(self.query("SELECT count(*) FROM entity_alias WHERE entity_id=1"), [(0,)])

    def test_only_rows_judged_rename_are_planned(self):
        self.plan((1, "Celeb no Tomo", "改名", "セレブの友"),
                  (4, "Toyohiko", "保留（片假名外来语）", ""))
        self.run_script()
        self.assertEqual(set(self.audit_rows()), {"Celeb no Tomo"})

    def test_a_stale_review_row_is_skipped(self):
        """复核件出得早，账本此后可能改过名或合并掉了那条实体。"""
        self.execute("UPDATE entity SET canonical_name='Celeb-no-Tomo' WHERE id=1")
        self.plan((1, "Celeb no Tomo", "改名", "セレブの友"),
                  (99, "Gone", "改名", "消えた"))
        self.assertEqual(self.apply(), 0)
        rows = self.audit_rows()
        self.assertEqual(rows["Celeb no Tomo"]["action"], MODULE.SKIP)
        self.assertIn("Celeb-no-Tomo", rows["Celeb no Tomo"]["reason"])
        self.assertEqual(rows["Gone"]["action"], MODULE.SKIP)
        self.assertEqual(self.query("SELECT canonical_name FROM entity WHERE id=1"),
                         [("Celeb-no-Tomo",)])

    def test_a_japanese_name_owned_by_another_studio_is_reported_not_merged(self):
        """撞上别家的规范名或别名，可能该合并，也可能是同名的两家，都得人来判。"""
        self.plan((2, "Hyoko", "改名", "ひよこ"), (4, "Toyohiko", "改名", "豊彦"))
        self.assertEqual(self.apply(), 0)
        rows = self.audit_rows()
        self.assertEqual(rows["Hyoko"]["action"], MODULE.SKIP)
        self.assertIn("#3", rows["Hyoko"]["reason"])
        self.assertEqual(rows["Toyohiko"]["action"], MODULE.SKIP)
        self.assertIn("#5", rows["Toyohiko"]["reason"])
        self.assertEqual(self.query("SELECT canonical_name FROM entity WHERE id IN (2,4) ORDER BY id"),
                         [("Hyoko",), ("Toyohiko",)])

    def test_logo_files_follow_the_new_name(self):
        """标识按 canonical_name 落盘，名字换了不改挂，旧名下的图就没人认领。"""
        for name, body in (("Celeb_no_Tomo.logo.img", b"plate"),
                           ("Celeb_no_Tomo.logo.img.ct", b"image/png"),
                           ("Celeb_no_Tomo.logo.img.provenance.json", b"{}")):
            (self.logos / name).write_bytes(body)
        self.plan((1, "Celeb no Tomo", "改名", "セレブの友"))
        self.assertEqual(self.apply(), 0)
        self.assertEqual((self.logos / "セレブの友.logo.img").read_bytes(), b"plate")
        self.assertEqual((self.logos / "セレブの友.logo.img.ct").read_bytes(), b"image/png")
        self.assertTrue((self.logos / "セレブの友.logo.img.provenance.json").is_file())
        self.assertFalse((self.logos / "Celeb_no_Tomo.logo.img").exists())

    def test_apply_without_backup_is_refused(self):
        self.plan((1, "Celeb no Tomo", "改名", "セレブの友"))
        with self.assertRaises(SystemExit):
            self.run_script("--apply")
        self.assertEqual(self.query("SELECT canonical_name FROM entity WHERE id=1"),
                         [("Celeb no Tomo",)])


if __name__ == "__main__":
    unittest.main()
