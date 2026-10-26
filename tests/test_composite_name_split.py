"""把一格装了多个艺名的别名拆成一人多名，以及不该被拆的四类括号。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.entities import split_composite_person_name
from peach.metadata import normalized_performers, split_former_name
from peach.metadata_auto_apply import _apply_performer_candidate
from peach.migrations import upgrade
from scripts.split_composite_aliases import (
    CLEANUP_SOURCE, apply_review, apply_rows, collect,
)

ROOT = Path(__file__).resolve().parents[1]


class CompositePersonNameTests(unittest.TestCase):
    def test_a_packed_romaji_field_becomes_one_name_per_person(self):
        self.assertEqual(
            split_composite_person_name("Ako Momona (Kou Akemi, Mari Koizumi)"),
            ["Ako Momona", "Kou Akemi", "Mari Koizumi"])
        self.assertEqual(split_composite_person_name("Nao (Hitomi Koike)"),
                         ["Nao", "Hitomi Koike"])

    def test_a_bracket_repeating_the_name_collapses_to_one(self):
        # 上游把括号无条件填满，`Rei Mizuna (Rei Mizuna)` 是模板产物不是两个人。
        self.assertEqual(split_composite_person_name("Rei Mizuna (Rei Mizuna)"),
                         ["Rei Mizuna"])

    def test_names_carrying_dots_apostrophes_and_hyphens_still_split(self):
        self.assertEqual(split_composite_person_name("A.J. O'Neil (Mary-Ann Doe)"),
                         ["A.J. O'Neil", "Mary-Ann Doe"])

    def test_the_other_four_kinds_of_bracket_are_left_whole(self):
        # 括号里是厂牌消歧、角色出处、接稿状态、去重后缀——都不是这个人的另一个名字。
        for name in ("AV DEBUT（本物人妻）", "アスナ(SAO)", "快慢扳机（接稿中）",
                     "kitty(1)", "Mana(23)", "プレステージプレミアム(PRESTIGEPREMIUM)",
                     "Egami(えがみ)", "Z(指定)ERO"):
            self.assertEqual(split_composite_person_name(name), [name])

    def test_a_plain_name_comes_back_as_itself(self):
        self.assertEqual(split_composite_person_name("  白石亚子 "), ["白石亚子"])
        self.assertEqual(split_composite_person_name(""), [])


class CompositeAliasSplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'t','t')",
            [(10, "performer", "藤木美夏", "藤木美夏"),
             (11, "performer", "里中静香", "里中静香"),
             (12, "series", "AV DEBUT（本物人妻）", "av debut（本物人妻）"),
             (13, "tag", "アスナ(SAO)", "アスナ(sao)"),
             (14, "performer", "Kou Akemi", "kou akemi"),
             (15, "performer", "新井爱丽", "新井爱丽")])
        self.con.executemany(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(?,?,?,?,1.0)",
            [(10, "Ako Momona (Kou Akemi, Mari Koizumi)",
              "ako momona (kou akemi, mari koizumi)", "r18:performer"),
             (11, "Rei Mizuna (Rei Mizuna)", "rei mizuna (rei mizuna)", "r18:performer"),
             (12, "AV DEBUT (Real Married Woman)", "av debut (real married woman)",
              "r18dev:series-localization"),
             (15, "Elly Akira (Elly Arai)", "elly akira (elly arai)",
              "merge:duplicate-identity")])
        self.con.commit()

    def _rows(self):
        return {str(row["value"]): row for row in collect(self.con)}

    def test_only_the_romaji_person_shape_is_marked_auto(self):
        rows = self._rows()
        self.assertEqual(rows["Elly Akira (Elly Arai)"]["verdict"], "auto")
        self.assertEqual(rows["Rei Mizuna (Rei Mizuna)"]["verdict"], "auto")
        # 系列别名的括号里是厂牌，标签的括号里是作品出处。
        self.assertEqual(rows["AV DEBUT (Real Married Woman)"]["verdict"], "review")
        self.assertEqual(rows["AV DEBUT（本物人妻）"]["verdict"], "review")
        self.assertEqual(rows["アスナ(SAO)"]["verdict"], "review")

    def test_a_part_that_is_another_entitys_canonical_name_goes_to_review(self):
        # `Kou Akemi` 已经自己占了一条实体：那意味着可能要合并，不是拆别名顺手能定的。
        row = self._rows()["Ako Momona (Kou Akemi, Mari Koizumi)"]
        self.assertEqual(row["verdict"], "review")
        self.assertIn("撞 entity 14", str(row["note"]))

    def test_applying_replaces_the_packed_row_with_one_row_per_name(self):
        moved = apply_rows(self.con, collect(self.con))
        self.assertEqual(moved, {"added": 3, "removed": 2})
        self.assertEqual(
            self.con.execute("SELECT alias,source FROM entity_alias WHERE entity_id=15"
                             " ORDER BY alias").fetchall(),
            [("Elly Akira", "merge:duplicate-identity"),
             ("Elly Arai", "merge:duplicate-identity")])
        # 每一段都记在提供它的那个来源名下：这几个罗马字确实是它给的，只是被打包在一个字段里。
        self.assertEqual(
            self.con.execute("SELECT alias,source FROM entity_alias WHERE entity_id=11")
            .fetchall(), [("Rei Mizuna", "r18:performer")])

    def test_applying_leaves_every_review_row_untouched(self):
        apply_rows(self.con, collect(self.con))
        for entity_id, alias in ((10, "Ako Momona (Kou Akemi, Mari Koizumi)"),
                                 (12, "AV DEBUT (Real Married Woman)")):
            self.assertEqual(
                self.con.execute("SELECT count(*) FROM entity_alias"
                                 " WHERE entity_id=? AND alias=?",
                                 (entity_id, alias)).fetchone()[0], 1)
        self.assertEqual(
            self.con.execute("SELECT canonical_name FROM entity WHERE id=13")
            .fetchone()[0], "アスナ(SAO)")


class ReviewedNameCleanupTests(unittest.TestCase):
    """形状分不开、只有人能判的那些：复核 CSV 填 `verdict` 与 `target` 再执行。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'t','t')",
            [(20, "creator", "幼月月@一日目(土)東オ54a", "幼月月@一日目(土)東オ54a"),
             (21, "studio", "プレステージプレミアム", "プレステージプレミアム"),
             (22, "performer", "白石亚子", "白石亚子"),
             (23, "creator", "みどりふう", "みどりふう")])
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(21,'プレステージプレミアム(PRESTIGEPREMIUM)',"
            "'プレステージプレミアム(prestigepremium)','javdb:studio',1.0)")
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,creator)"
            " VALUES(?,'online',?,?,'image',?)",
            [(1, "pixiv/1", "a", "幼月月@一日目(土)東オ54a"),
             (2, "pixiv/2", "b", "幼月月@一日目(土)東オ54a"),
             (3, "pixiv/3", "c", "みどりふう")])
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(1,20,'creator','pixiv',1.0)")
        self.con.commit()

    def _row(self, verdict, entity_id, value, target, kind="creator", source=""):
        return {"verdict": verdict, "kind": kind, "entity_id": str(entity_id),
                "value": value, "target": target, "source": source}

    def test_stripping_a_booth_suffix_renames_the_entity_and_its_assets(self):
        done = apply_review(self.con, [
            self._row("strip", 20, "幼月月@一日目(土)東オ54a", "幼月月")])
        self.assertEqual((done["strip"], done["flat"]), (1, 2))
        self.assertEqual(
            self.con.execute("SELECT canonical_name,normalized_name FROM entity"
                             " WHERE id=20").fetchone(), ("幼月月", "幼月月"))
        # 扁平投影跟着改，否则卡片还写着旧名、按旧名照样搜得到。
        self.assertEqual(
            sorted(name for (name,) in self.con.execute(
                "SELECT creator FROM asset WHERE creator IS NOT NULL")),
            ["みどりふう", "幼月月", "幼月月"])

    def test_the_stripped_name_stays_as_an_alias_under_the_cleanup_source(self):
        apply_review(self.con, [self._row("strip", 20, "幼月月@一日目(土)東オ54a", "幼月月")])
        self.assertEqual(
            self.con.execute("SELECT alias,source FROM entity_alias WHERE entity_id=20")
            .fetchall(), [("幼月月@一日目(土)東オ54a", CLEANUP_SOURCE)])

    def test_splitting_a_studio_alias_keeps_the_source_that_gave_it(self):
        done = apply_review(self.con, [
            self._row("split", 21, "プレステージプレミアム(PRESTIGEPREMIUM)",
                      "プレステージプレミアム | PRESTIGEPREMIUM",
                      kind="studio", source="javdb:studio")])
        self.assertEqual(done["split"], 1)
        self.assertEqual(
            sorted(alias for (alias,) in self.con.execute(
                "SELECT alias FROM entity_alias WHERE entity_id=21")),
            ["PRESTIGEPREMIUM", "プレステージプレミアム"])

    def test_a_target_taken_by_another_entity_is_refused_not_written(self):
        # 撞上另一条实体的规范名意味着两条可能该合并，那是合并要回答的问题。
        done = apply_review(self.con, [
            self._row("strip", 20, "幼月月@一日目(土)東オ54a", "みどりふう")])
        self.assertEqual(done["strip"], 0)
        self.assertEqual(len(done["blocked"]), 1)
        self.assertIn("撞 entity 23", done["blocked"][0])
        self.assertEqual(
            self.con.execute("SELECT canonical_name FROM entity WHERE id=20").fetchone()[0],
            "幼月月@一日目(土)東オ54a")

    def test_rows_left_at_review_do_nothing(self):
        done = apply_review(self.con, [
            self._row("review", 20, "幼月月@一日目(土)東オ54a", "幼月月"),
            self._row("strip", 20, "幼月月@一日目(土)東オ54a", "")])
        self.assertEqual((done["strip"], done["split"], done["flat"]), (0, 0, 0))
        self.assertEqual(
            self.con.execute("SELECT canonical_name FROM entity WHERE id=20").fetchone()[0],
            "幼月月@一日目(土)東オ54a")

    def test_a_performer_strip_rewrites_the_flat_tag(self):
        self.con.execute(
            "INSERT INTO asset(id,location,path,name,medium)"
            " VALUES(4,'online','pixiv/4','d','image')")
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(4,22,'performer','javdb',1.0)")
        self.con.execute(
            "INSERT INTO asset_tag(asset_id,tag,confidence,source)"
            " VALUES(4,'演员:白石亚子',1.0,'javdb')")
        done = apply_review(self.con, [
            self._row("strip", 22, "白石亚子", "白石 あこ", kind="performer")])
        self.assertEqual((done["strip"], done["flat"]), (1, 1))
        self.assertEqual(
            [tag for (tag,) in self.con.execute(
                "SELECT tag FROM asset_tag WHERE asset_id=4")], ["演员:白石 あこ"])


class FormerNameTests(unittest.TestCase):
    """女优栏里的「现名（旧名）」：拆成一个人的两个名字，挂到账本里已有的那一位。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,'performer',?,?,'t','t')",
            [(20, "美谷朱音", "美谷朱音"), (21, "姬川优奈", "姬川优奈")])
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(21,'月城らん','月城らん','test',1.0)")
        self.con.execute(
            "INSERT INTO asset(id,location,path,name,medium,code,size)"
            " VALUES(1,'R:','R:\\media\\MIZD-997.mp4','MIZD-997.mp4','video','MIZD-997',1)")

    def land(self, raw_name: str, external_id: str = "") -> int:
        performers, _warnings = normalized_performers(
            [{"japanese_name": raw_name, "dmm_id": external_id, "profile_source": "r18dev"}])
        _apply_performer_candidate(self.con, [1], {"value": performers}, source="r18dev",
                                   confidence=0.9, metadata={}, now="t")
        return self.con.execute(
            "SELECT entity_id FROM asset_entity WHERE asset_id=1 AND role='performer'"
        ).fetchone()[0]

    def test_current_and_former_names_split_apart(self):
        self.assertEqual(split_former_name("美谷朱音（美谷朱里）"), ("美谷朱音", "美谷朱里"))
        self.assertEqual(split_former_name("七海ひな(旧・七瀬ひな)"), ("七海ひな", "七瀬ひな"))
        performers, _warnings = normalized_performers([{"japanese_name": "美谷朱音（美谷朱里）"}])
        self.assertEqual((performers[0]["name"], performers[0]["aliases"],
                          performers[0]["former_names"]),
                         ("美谷朱音", ["美谷朱里"], ["美谷朱里"]))

    def test_brackets_that_are_not_a_former_name_stay_whole(self):
        # 年龄、描述、罗马字、几个旧名挤在一格：都不是「一个现名配一个旧名」。
        for name in ("Mana(23)", "みく（素人）", "Rei Mizuna (Rei Mizuna)",
                     "美谷朱音（美谷朱里、朱里）", "ゆい（20歳）"):
            self.assertEqual(split_former_name(name), (name, ""))

    def test_the_current_name_lands_on_the_entity_already_there(self):
        self.assertEqual(self.land("美谷朱音（美谷朱里）", "1039982"), 20)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM entity WHERE kind='performer'").fetchone()[0], 2)
        self.assertEqual([row[0] for row in self.con.execute(
            "SELECT tag FROM asset_tag WHERE asset_id=1")], ["演员:美谷朱音"])

    def test_a_new_current_name_lands_on_whoever_holds_the_former_one(self):
        self.assertEqual(self.land("姫川ゆうな（月城らん）"), 21)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM entity WHERE kind='performer'").fetchone()[0], 2)
        self.assertEqual(self.con.execute(
            "SELECT source FROM entity_alias WHERE entity_id=21 AND alias='姫川ゆうな'"
        ).fetchone()[0], "javinizer:current-name")


if __name__ == "__main__":
    unittest.main()
