"""label 属于片商（ADR-0049）；上级可以一级套一级，片商合计旗下每一级的作品（ADR-0051）。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach import web_contract as rm_web
from peach.entities import merge_entity
from peach.migrations import upgrade
from scripts.install_label_makers import install, plan

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


class LabelMakerTests(unittest.TestCase):
    """KMP 旗下两个 label、妄想族旗下一个，妄想族自己在账本里还没有实体。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.db = root / "ledger.db"
        upgrade(self.db, MIGRATIONS)
        self.con = sqlite3.connect(self.db)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,first_seen)"
            " VALUES(?,'local',?,?,'video',100,'2026-01-01')",
            [(1, r"R:\Media\a.mp4", "a.mp4"), (2, r"R:\Media\b.mp4", "b.mp4"),
             (3, r"R:\Media\c.mp4", "c.mp4")])
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,'studio',?,?,'t','t')",
            [(5607, "K M Produce", "k m produce"), (5569, "BAZOOKA", "bazooka"),
             (5638, "S級素人", "s級素人"), (8248, "ABC/妄想族", "abc/妄想族")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,?,'studio','test',1.0)", [(1, 5569), (2, 5607), (3, 8248)])
        self.con.commit()
        self.rows = [
            {"label": "BAZOOKA", "maker": "K M Produce", "evidence": "KMP 名录"},
            {"label": "S級素人", "maker": "K M Produce", "evidence": "KMP 名录"},
            {"label": "ABC/妄想族", "maker": "妄想族", "evidence": "妄想族名录"},
        ]
        logos = root / "logos"; logos.mkdir()
        avatars = root / "avatars"; avatars.mkdir()
        self.contract = rm_web.WebContract(self.db, avatar_root=avatars, logo_root=logos,
                                           cover_root=root / "covers")

    def apply(self, rows=None, *, create_makers=True):
        planned = plan(self.con, rows or self.rows, create_makers=create_makers)
        with self.con:
            install(self.con, planned, source="t.csv")
        return planned

    def maker_of(self, label_id):
        row = self.con.execute(
            "SELECT e.canonical_name FROM label_maker lm JOIN entity e ON e.id=lm.maker_id"
            " WHERE lm.label_id=?", (label_id,)).fetchone()
        return row[0] if row else None

    def test_a_maker_missing_from_the_ledger_is_only_created_when_asked(self):
        planned = plan(self.con, self.rows, create_makers=False)
        self.assertEqual([item["action"] for item in planned], ["insert", "insert", "skip"])
        self.assertIn("--create-makers", planned[2]["reason"])
        self.apply()
        self.assertEqual(self.maker_of(8248), "妄想族")

    def test_rerunning_the_same_table_writes_nothing(self):
        self.apply()
        self.assertEqual({item["action"] for item in self.apply()}, {"skip"})
        self.assertEqual(self.con.execute("SELECT count(*) FROM label_maker").fetchone()[0], 3)

    def test_a_label_changing_hands_is_overwritten_not_added(self):
        self.apply()
        planned = self.apply([{"label": "BAZOOKA", "maker": "妄想族", "evidence": "转手"}])
        self.assertEqual(planned[0]["action"], "update")
        self.assertEqual(self.maker_of(5569), "妄想族")

    def test_a_maker_can_itself_sit_under_another_maker(self):
        """ADR-0051：妄想族 → 素人ホイホイ → 素人ホイホイpower 这样的三层照收。"""
        self.apply()
        planned = self.apply([{"label": "K M Produce", "maker": "妄想族", "evidence": "x"},
                              {"label": "S級素人", "maker": "BAZOOKA", "evidence": "x"}])
        self.assertEqual([item["action"] for item in planned], ["insert", "update"])
        self.assertEqual(self.maker_of(5607), "妄想族")
        self.assertEqual(self.maker_of(5638), "BAZOOKA")

    def test_a_chain_that_loops_back_is_refused(self):
        self.apply()
        planned = plan(self.con, [{"label": "K M Produce", "maker": "BAZOOKA", "evidence": "x"}],
                       create_makers=True)
        self.assertEqual(planned[0]["action"], "skip")
        self.assertIn("成环", planned[0]["reason"])
        same_batch = plan(self.con, [{"label": "ABC/妄想族", "maker": "S級素人", "evidence": "x"},
                                     {"label": "S級素人", "maker": "ABC/妄想族", "evidence": "x"}],
                          create_makers=True)
        self.assertEqual([item["action"] for item in same_batch], ["skip", "skip"])

    def test_one_table_builds_a_middle_layer_missing_from_the_ledger(self):
        """中间那一级账本里还没有：同一张表里它既是上级又是 label，先建出来再挂上去。"""
        planned = self.apply([{"label": "BAZOOKA", "maker": "素人ホイホイ", "evidence": "x"},
                              {"label": "素人ホイホイ", "maker": "妄想族", "evidence": "x"}])
        self.assertEqual([item["action"] for item in planned], ["insert", "insert"])
        self.assertEqual(self.maker_of(5569), "素人ホイホイ")
        middle = self.con.execute(
            "SELECT id FROM entity WHERE canonical_name='素人ホイホイ'").fetchall()
        self.assertEqual(len(middle), 1)
        self.assertEqual(self.maker_of(middle[0][0]), "妄想族")

    def test_a_label_missing_from_the_ledger_is_not_created_unless_it_is_also_a_maker(self):
        planned = plan(self.con, [{"label": "無名", "maker": "K M Produce", "evidence": "x"}],
                       create_makers=True)
        self.assertEqual(planned[0]["action"], "skip")

    def test_each_page_names_only_the_neighbouring_layers(self):
        """资料页只链直接上级和直接下级；作品详情列整条链，从近到远。"""
        self.apply(self.rows + [{"label": "K M Produce", "maker": "妄想族", "evidence": "x"}])
        middle = rm_web.q_entity(self.contract, {"kind": "studio", "name": "K M Produce"})
        self.assertEqual(middle["maker"]["name"], "妄想族")
        self.assertEqual([label["name"] for label in middle["labels"]], ["BAZOOKA", "S級素人"])
        bottom = rm_web.q_entity(self.contract, {"kind": "studio", "name": "BAZOOKA"})
        self.assertEqual(bottom["maker"]["name"], "K M Produce")
        studio = rm_web.q_item(self.contract, 1)["entity_refs"]["studio"]
        self.assertEqual([maker["name"] for maker in studio[0]["makers"]], ["K M Produce", "妄想族"])

    def test_the_label_page_points_at_its_maker_and_keeps_its_own_works(self):
        self.apply()
        page = rm_web.q_entity(self.contract, {"kind": "studio", "name": "BAZOOKA"})
        self.assertEqual(page["maker"]["name"], "K M Produce")
        self.assertEqual(page["asset_count"], 1)
        self.assertEqual(page["labels"], [])

    def test_the_maker_page_counts_its_labels_works_as_its_own(self):
        """ADR-0051 修订：旗下 label 出的片都归片商，名册每格的数是那个 label 的合计。"""
        self.apply()
        page = rm_web.q_entity(self.contract, {"kind": "studio", "name": "K M Produce"})
        self.assertEqual([(label["k"], label["n"]) for label in page["labels"]],
                         [("BAZOOKA", 1), ("S級素人", 0)])
        self.assertEqual(page["asset_count"], 2)
        self.assertIsNone(page["maker"])
        items = rm_web.q_items(self.contract, {"studio": "K M Produce"})
        self.assertEqual(sorted(item["id"] for item in items["items"]), [1, 2])

    def test_vanished_works_drop_out_of_the_counts_and_trashed_ones_stay(self):
        """已消失的作品文件不在盘上，不进资料页和名册的作品数；回收站里的照旧算。"""
        self.apply()
        self.con.execute("UPDATE asset SET disposal='vanished' WHERE id=1")
        self.con.execute("UPDATE asset SET disposal='trash' WHERE id=2")
        self.con.commit()
        page = rm_web.q_entity(self.contract, {"kind": "studio", "name": "K M Produce"})
        self.assertEqual([(label["k"], label["n"]) for label in page["labels"]],
                         [("BAZOOKA", 0), ("S級素人", 0)])
        self.assertEqual(page["asset_count"], 1)

    def test_a_maker_with_no_works_of_its_own_counts_every_layer_below(self):
        """妄想族 → K M Produce → BAZOOKA：妄想族自己一部片都没挂，合计是三层的并集。"""
        self.apply(self.rows + [{"label": "K M Produce", "maker": "妄想族", "evidence": "x"}])
        top = rm_web.q_entity(self.contract, {"kind": "studio", "name": "妄想族"})
        self.assertEqual(top["asset_count"], 3)
        self.assertEqual([(label["k"], label["n"]) for label in top["labels"]],
                         [("K M Produce", 2), ("ABC/妄想族", 1)])

    def test_the_maker_representative_can_be_a_labels_work(self):
        """BAZOOKA 那部最大、归 K M Produce；ABC/妄想族 那部更大，但不归它。"""
        self.apply()
        self.con.executemany("UPDATE asset SET snapshot_path='s.jpg',size=? WHERE id=?",
                             [(500, 1), (200, 2), (900, 3)])
        self.con.commit()
        page = rm_web.q_entity(self.contract, {"kind": "studio", "name": "K M Produce"})
        self.assertEqual(page["representative_asset_id"], 1)

    def test_the_maker_photos_and_sample_sets_include_its_labels(self):
        """图集、总数、分页和番号样张集都连同旗下 label 一起算，ABC/妄想族 的不算。"""
        self.apply()
        for asset_id, studio in ((21, 5569), (22, 5607), (23, 8248)):
            path = rf"R:\Media\stills\{asset_id}.jpg"
            self.con.execute(
                "INSERT INTO asset(id,location,path,name,medium,size,first_seen)"
                " VALUES(?,'local',?,?,'image',10,'2026-01-01')", (asset_id, path, f"{asset_id}.jpg"))
            self.con.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
                " VALUES(?,?,'studio','test',1.0)", (asset_id, studio))
        for asset_id, code in ((1, "SSIS-057"), (2, "SSIS-058"), (3, "IPX-001")):
            self.con.execute("UPDATE asset SET code=? WHERE id=?", (code, asset_id))
            self.con.execute(
                "INSERT INTO code_sample_image(code,position,url,site,source,fetched_at)"
                " VALUES(?,1,'https://pics.dmm.co.jp/x.jpg','dmm','test','2026-09-25T00:00:00Z')",
                (code,))
        self.con.commit()
        photos = rm_web.q_entity_photos(self.contract, {"kind": "studio", "name": "K M Produce"})
        self.assertEqual([(item["kind"], item["id"], item["n"]) for item in photos["sets"]],
                         [("code", "code:SSIS-057", 1), ("code", "code:SSIS-058", 1),
                          ("dir", 21, 2)])
        self.assertEqual((photos["total"], photos["sample_total"]), (2, 2))
        self.assertEqual([item["id"] for item in photos["items"]], [21, 22])

    def test_the_index_lists_makers_with_their_totals_and_folds_labels_in(self):
        """索引页只列最上层，数的是合计；搜名字时 label 照常搜得到。"""
        self.apply()
        index = rm_web.q_index(self.contract, "studios")
        self.assertEqual([(row["k"], row["n"]) for row in index["items"]],
                         [("K M Produce", 2), ("妄想族", 1)])
        found = rm_web.q_index(self.contract, "studios", q="BAZOOKA")
        self.assertEqual([(row["k"], row["n"]) for row in found["items"]], [("BAZOOKA", 1)])

    def test_the_index_picks_each_makers_representative_from_every_layer(self):
        """K M Produce 那格取 BAZOOKA 那部；妄想族自己没挂片，取 ABC/妄想族 那部。

        K M Produce 名下最大的那部没有截图，不当代表作。搜名字时 label 那格取它自己的。
        """
        self.apply()
        self.con.executemany("UPDATE asset SET snapshot_path='s.jpg',size=? WHERE id=?",
                             [(500, 1), (200, 2), (900, 3)])
        self.con.execute(
            "INSERT INTO asset(id,location,path,name,medium,size,first_seen)"
            " VALUES(4,'local',?,'d.mp4','video',999,'2026-01-01')", (r"R:\Media\d.mp4",))
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(4,5607,'studio','test',1.0)")
        self.con.commit()
        index = rm_web.q_index(self.contract, "studios")
        self.assertEqual([(row["k"], row["rep"]) for row in index["items"]],
                         [("K M Produce", 1), ("妄想族", 3)])
        found = rm_web.q_index(self.contract, "studios", q="妄想族")
        self.assertEqual([(row["k"], row["rep"]) for row in found["items"]],
                         [("ABC/妄想族", 3), ("妄想族", 3)])

    def test_the_detail_carries_the_maker_beside_the_label(self):
        self.apply()
        studio = rm_web.q_item(self.contract, 3)["entity_refs"]["studio"]
        self.assertEqual([ref["name"] for ref in studio], ["ABC/妄想族"])
        self.assertEqual([maker["name"] for maker in studio[0]["makers"]], ["妄想族"])
        self.assertNotIn("makers", rm_web.q_item(self.contract, 2)["entity_refs"]["studio"][0])

    def test_merging_a_label_carries_its_maker_over(self):
        self.apply()
        self.con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(9000,'studio','BAZOOKA!','bazooka!','t','t')")
        merge_entity(self.con, target_id=9000, source_id=5569, source_name="BAZOOKA", alias_source="test")
        self.assertEqual(self.maker_of(9000), "K M Produce")
        self.assertIsNone(self.maker_of(5569))

    def test_merging_a_maker_moves_its_labels(self):
        self.apply()
        self.con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(9001,'studio','KMP','kmp','t','t')")
        merge_entity(self.con, target_id=9001, source_id=5607, source_name="K M Produce", alias_source="test")
        self.assertEqual({self.maker_of(5569), self.maker_of(5638)}, {"KMP"})

    def test_merging_a_label_into_its_own_maker_drops_the_self_link(self):
        self.apply()
        merge_entity(self.con, target_id=5607, source_id=5569, source_name="BAZOOKA", alias_source="test")
        self.assertIsNone(self.maker_of(5607))
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM label_maker WHERE label_id=maker_id").fetchone()[0], 0)

    def test_merging_the_top_of_a_chain_into_its_bottom_breaks_the_loop(self):
        """BAZOOKA → K M Produce → 妄想族，把妄想族并进 BAZOOKA：K M Produce 改挂
        BAZOOKA，BAZOOKA 自己那一行上级会绕回它自己，删掉。"""
        self.apply(self.rows + [{"label": "K M Produce", "maker": "妄想族", "evidence": "x"}])
        top = self.con.execute("SELECT id FROM entity WHERE canonical_name='妄想族'").fetchone()[0]
        merge_entity(self.con, target_id=5569, source_id=top, source_name="妄想族", alias_source="test")
        self.assertIsNone(self.maker_of(5569))
        self.assertEqual(self.maker_of(5607), "BAZOOKA")


if __name__ == "__main__":
    unittest.main()
