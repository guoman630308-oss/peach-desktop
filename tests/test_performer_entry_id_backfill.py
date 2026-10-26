"""外部入口 id 回填：一位对上几个 id 就登记几条，已登记的跳过，撞号的点名等人判。"""
import argparse
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.entry_links import EXTERNAL_KIND
from peach.migrations import upgrade
from scripts.backfill_performer_entry_ids import (
    CONFLICT, FC2_ONLY, HAVE, JAVDB, NAMESAKE, OK, TAKEN, apply_rows, plan,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


class EntryIdBackfillTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = self.root / "ledger.db"
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)
        self.connection = sqlite3.connect(self.db)
        self.addCleanup(self.connection.close)
        for entity_id, name in ((10, "释爱丽丝"), (11, "天川空")):
            self.connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,'performer',?,?,'t','t')", (entity_id, name, name))
        self.connection.commit()
        self.cache = self.root / "javdb"
        self.cache.mkdir()

    def page(self, name: str, actor: str) -> None:
        """一页 javdb 资料页：名字在标题里，演员 id 在收藏那条链接里。"""
        (self.cache / f"{actor}.html").write_text(
            f'<html><body><span class="actor-section-name">{name}</span>'
            f'<a href="/actors/{actor}/collect"></a></body></html>',
            encoding="utf-8")

    def search_page(self, stem: str, *cards: tuple[str, str, str]) -> None:
        """一张 javdb 搜索结果页：每张卡 (id, 标题一栏, 记录类型)。"""
        boxes = "".join(
            f'<div class="box actor-box"><a href="/actors/{actor}" title="{title}">'
            f'<figure class="image">'
            + (f'<span class="info">{record}</span>' if record else "")
            + f"</figure><strong>{title}</strong></a></div>"
            for actor, title, record in cards)
        (self.cache / f"{stem}.html").write_text(
            f'<html><body><div class="actors">{boxes}</div></body></html>', encoding="utf-8")

    def rows(self) -> list[dict]:
        args = argparse.Namespace(javdb_cache=self.cache, csv=[])
        return plan(self.connection, args)

    def saved(self) -> list[tuple]:
        return self.connection.execute(
            "SELECT entity_id,external_id FROM entity_external_ref"
            " WHERE provider=? AND external_kind=? ORDER BY external_id",
            (JAVDB, EXTERNAL_KIND)).fetchall()

    def test_one_performer_with_two_pages_gets_both_registered(self):
        self.page("释爱丽丝", "d45k9")
        self.page("释爱丽丝", "ZX5z7")
        rows = self.rows()
        self.assertEqual([row["verdict"] for row in rows], [OK, OK])
        self.assertEqual(apply_rows(self.connection, rows), (2, 2))
        self.assertEqual(self.saved(), [(10, "ZX5z7"), (10, "d45k9")])

    def test_a_row_already_in_the_ledger_is_skipped_and_the_rest_still_land(self):
        """同一批证据可以重复跑：已登记的那条不报错、不重写、也不算冲突。"""
        self.page("释爱丽丝", "d45k9")
        apply_rows(self.connection, self.rows())
        self.page("释爱丽丝", "ZX5z7")
        rows = self.rows()
        self.assertEqual([row["verdict"] for row in rows], [OK, HAVE])
        self.assertEqual(apply_rows(self.connection, rows), (1, 1))
        self.assertEqual(self.saved(), [(10, "ZX5z7"), (10, "d45k9")])

    def test_an_id_held_by_another_performer_is_named_not_swallowed(self):
        """主键决定一个 id 只属于一位，所以这一条要写出占有者等人判。"""
        self.connection.execute(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
            " VALUES(11,?,?,'d45k9')", (JAVDB, EXTERNAL_KIND))
        self.connection.commit()
        self.page("释爱丽丝", "d45k9")
        rows = self.rows()
        self.assertEqual([row["verdict"] for row in rows], [TAKEN])
        self.assertIn("11", rows[0]["evidence"])
        self.assertEqual(apply_rows(self.connection, rows), (0, 0))

    def test_two_performers_claiming_one_page_are_left_to_a_person(self):
        self.connection.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(11,'释爱丽丝','释爱丽丝','test',1.0)")
        self.connection.commit()
        self.page("释爱丽丝", "d45k9")
        rows = self.rows()
        self.assertEqual([row["verdict"] for row in rows], [CONFLICT, CONFLICT])
        self.assertEqual(apply_rows(self.connection, rows), (0, 0))

    def test_japanese_glyphs_on_the_page_match_the_simplified_name(self):
        self.connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(13,'performer','泽村玲子','泽村玲子','t','t')")
        self.connection.commit()
        self.page("澤村玲子", "d45k9")
        self.assertEqual([(row["entity_id"], row["verdict"]) for row in self.rows()], [(13, OK)])

    def test_search_cards_give_both_records_of_one_performer(self):
        """资料页没取回时，搜索页的卡上就有 id；有碼、無碼各一张是同一个人。"""
        self.search_page("s1", ("d45k9", "释爱丽丝, 釈アリス", ""), ("ZX5z7", "释爱丽丝", "無碼"),
                         ("Qq11", "天川そら", ""))
        rows = self.rows()
        self.assertEqual([(row["external_id"], row["verdict"]) for row in rows],
                         [("ZX5z7", OK), ("d45k9", OK)])

    def test_two_cards_of_the_same_record_kind_are_namesakes(self):
        self.search_page("s1", ("d45k9", "释爱丽丝", ""))
        self.search_page("s2", ("ZX5z7", "释爱丽丝", ""), ("d45k9", "释爱丽丝", ""))
        rows = self.rows()
        self.assertEqual([(row["verdict"], row["external_id"]) for row in rows], [(NAMESAKE, "")])
        self.assertIn("ZX5z7、d45k9", rows[0]["evidence"])
        self.assertEqual(apply_rows(self.connection, rows), (0, 0))

    def test_a_short_single_name_on_a_card_is_not_an_anchor(self):
        self.connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(12,'performer','葵','葵','t','t')")
        self.connection.commit()
        self.search_page("s1", ("Aa01", "葵", ""))
        self.assertEqual(self.rows(), [])

    def test_a_short_single_name_on_a_profile_page_is_not_an_anchor(self):
        self.connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(12,'performer','葵','葵','t','t')")
        self.connection.commit()
        self.page("葵", "Aa01")
        self.assertEqual(self.rows(), [])

    def works(self, entity_id: int, *codes: str) -> None:
        for code in codes:
            asset = self.connection.execute(
                "INSERT INTO asset(location,path,name,medium,code) VALUES('local',?,?,'video',?)",
                (rf"R:\media\{code}.mp4", f"{code}.mp4", code)).lastrowid
            self.connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
                " VALUES(?,?,'performer','test',1.0)", (asset, entity_id))
        self.connection.commit()

    def test_an_fc2_creator_without_a_jav_directory_id_is_not_bound(self):
        """FC2 个人摄创作者不在 javdb 的人物收录里，名字对上的演员页是同名的另一个人。"""
        self.works(10, "FC2-PPV-1812235", "FC2-PPV-3202758")
        self.page("释爱丽丝", "d45k9")
        rows = self.rows()
        self.assertEqual([row["verdict"] for row in rows], [FC2_ONLY])
        self.assertIn("2 部", rows[0]["evidence"])
        self.assertEqual(apply_rows(self.connection, rows), (0, 0))

    def test_fc2_works_do_not_hold_back_a_performer_known_to_a_jav_directory(self):
        self.works(10, "FC2-PPV-1812235")
        self.connection.execute(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
            " VALUES(10,'minnano-av',?,'12345')", (EXTERNAL_KIND,))
        self.connection.commit()
        self.page("释爱丽丝", "d45k9")
        self.assertEqual([row["verdict"] for row in self.rows()], [OK])

    def test_one_studio_release_among_fc2_works_keeps_the_binding(self):
        self.works(10, "FC2-PPV-1812235", "SSIS-001")
        self.page("释爱丽丝", "d45k9")
        self.assertEqual([row["verdict"] for row in self.rows()], [OK])


if __name__ == "__main__":
    unittest.main()
