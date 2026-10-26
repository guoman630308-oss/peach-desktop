"""creator / performer 跨类重复身份审计与兼容投影回归。"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.migrations import upgrade
from peach.review_csv import read_rows
from scripts.merge_duplicate_identities import (
    apply_repeated_projections,
    apply_rows,
    build_parser,
    collect,
    collect_repeated_projections,
    named_pairs,
    run,
)

ROOT = Path(__file__).resolve().parents[1]


class DuplicateIdentityMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,creator) "
            "VALUES(?,'local',?,?,'video',?)",
            [(1, "/x/1.mp4", "1.mp4", "小千 Qian0791"),
             (2, "/x/2.mp4", "2.mp4", "小千 Qian0791"),
             (3, "/x/3.mp4", "3.mp4", "NOZOMI NOZOMI"),
             (4, "/x/4.mp4", "4.mp4", "NOZOMI NOZOMI")])
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,?,?,?, 't','t')",
            [(10, "creator", "小千 Qian0791", "小千 qian0791"),
             (11, "performer", "小千", "小千"),
             (12, "creator", "NOZOMI NOZOMI", "nozomi nozomi"),
             (13, "performer", "NOZOMI", "nozomi"),
             (14, "creator", "无关目录", "无关目录")])
        self.con.executemany(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence) "
            "VALUES(?,?,?,?,1.0)",
            [(11, "Qian0791", "qian0791", "stash:performer"),
             (13, "NOZOMI NOZOMI", "nozomi nozomi", "javbus:performer")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(?,?,?,?,1.0)",
            [(1, 10, "creator", "legacy:asset"), (2, 10, "creator", "legacy:asset"),
             (1, 11, "performer", "performer"), (2, 11, "performer", "performer"),
             (3, 12, "creator", "legacy:asset"),
             (3, 13, "performer", "javbus:performer"),
             (4, 13, "performer", "javbus:performer"),
             (1, 14, "creator", "legacy:asset"), (2, 14, "creator", "legacy:asset")])
        self.con.executemany(
            "INSERT INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,?,1.0,?)",
            [(1, "演员:小千", "performer"), (2, "演员:小千", "performer"),
             (3, "演员:NOZOMI NOZOMI", "javbus:performer")])
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_collect_uses_same_assets_aliases_and_release_provenance(self):
        rows = collect(self.con)
        self.assertEqual(len(rows), 2)
        by_drop = {row["drop_name"]: row for row in rows}
        self.assertEqual(by_drop["小千"]["keep_kind"], "creator")
        self.assertIn("账号别名", by_drop["小千"]["match_evidence"])
        self.assertEqual(by_drop["NOZOMI NOZOMI"]["keep_kind"], "performer")
        self.assertEqual(by_drop["NOZOMI NOZOMI"]["evidence"], "发行元数据")
        self.assertNotIn("无关目录", {row["keep_name"] for row in rows})

    def test_apply_syncs_flat_creator_and_actor_tag_projections(self):
        counts = apply_rows(self.con, collect(self.con))
        self.con.commit()

        self.assertIsNone(self.con.execute(
            "SELECT 1 FROM entity WHERE id=11").fetchone())
        self.assertIsNone(self.con.execute(
            "SELECT 1 FROM entity WHERE id=12").fetchone())
        self.assertEqual(self.con.execute(
            "SELECT creator FROM asset WHERE id=3").fetchone()[0], None)
        self.assertEqual(self.con.execute(
            "SELECT creator FROM asset WHERE id=4").fetchone()[0], None,
            "兼容字段要按坏名字全量清，不能只清旧 creator 关系覆盖的资产")
        tags = dict(self.con.execute(
            "SELECT asset_id,tag FROM asset_tag ORDER BY asset_id"))
        self.assertNotIn(1, tags)
        self.assertNotIn(2, tags)
        self.assertEqual(tags[3], "演员:NOZOMI")
        self.assertEqual(counts["actor_tags_removed"], 2)
        self.assertEqual(counts["actor_tags_rewritten"], 1)
        self.assertEqual(self.con.execute(
            "PRAGMA foreign_key_check").fetchall(), [])

    def _seed_same_kind_projection(self):
        """`小桃` / `小桃 shixiaotaone` 这类同 kind 目录名投影。

        20 号是规范实体：带两个账号别名，作品集合是 {20,21,22}。21 号是
        `R:\\Media\\<本名 + 账号名>` 投影出来的第二条 creator，作品 {20,21} 全落在 20 号里。
        22 号名字同样由 20 号的本名和别名拼成，但作品 {22,23} 越出了 20 号的集合。
        """
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,creator) "
            "VALUES(?,'local',?,?,'video',?)",
            [(20, "/x/20.mp4", "20.mp4", "小桃 shixiaotaone"),
             (21, "/x/21.mp4", "21.mp4", "小桃 shixiaotaone"),
             (22, "/x/22.mp4", "22.mp4", "小桃 taomi"),
             (23, "/x/23.mp4", "23.mp4", "小桃 taomi")])
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,?,?,?, 't','t')",
            [(20, "creator", "小桃", "小桃"),
             (21, "creator", "小桃 shixiaotaone", "小桃 shixiaotaone"),
             (22, "creator", "小桃 taomi", "小桃 taomi")])
        self.con.executemany(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence) "
            "VALUES(?,?,?,'stash:performer',0.9)",
            [(20, "shixiaotaone", "shixiaotaone"), (20, "taomi", "taomi")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(?,?,?,?,1.0)",
            [(20, 20, "performer", "performer"), (21, 20, "performer", "performer"),
             (22, 20, "performer", "performer"),
             (20, 21, "creator", "legacy:asset"), (21, 21, "creator", "legacy:asset"),
             (22, 22, "creator", "legacy:asset"), (23, 22, "creator", "legacy:asset")])
        self.con.commit()

    def test_collect_merges_same_kind_directory_projection(self):
        self._seed_same_kind_projection()
        rows = [row for row in collect(self.con) if row["drop_id"] == 21]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["keep_id"], 20)
        self.assertEqual(rows[0]["keep_kind"], "creator")
        self.assertEqual(rows[0]["drop_kind"], "creator")
        self.assertIn("同 kind", rows[0]["match_evidence"])

    def test_collect_skips_same_kind_without_asset_or_provenance_evidence(self):
        self._seed_same_kind_projection()
        # 21 号一旦有了自己的外部引用就不再是纯目录投影，留给人工。
        self.con.execute(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id) "
            "VALUES(21,'stash','performer','777')")
        self.con.commit()
        drops = {row["drop_id"] for row in collect(self.con)}
        self.assertNotIn(21, drops, "带外部引用的实体不是目录投影")
        self.assertNotIn(22, drops, "作品集合越出保留方时，拼名不足以判同人")

    def test_apply_rewrites_flat_creator_for_same_kind_merge(self):
        self._seed_same_kind_projection()
        rows = [row for row in collect(self.con) if row["drop_id"] == 21]
        counts = apply_rows(self.con, rows)
        self.con.commit()

        self.assertIsNone(self.con.execute("SELECT 1 FROM entity WHERE id=21").fetchone())
        self.assertEqual([row[0] for row in self.con.execute(
            "SELECT creator FROM asset WHERE id IN (20,21) ORDER BY id")], ["小桃", "小桃"])
        self.assertEqual(counts["flat_rewritten"], 2)
        self.assertEqual(self.con.execute(
            "SELECT creator FROM asset WHERE id=22").fetchone()[0], "小桃 taomi",
            "只改写被丢弃的那个名字，不动别的目录名")
        self.assertIn("小桃 shixiaotaone", [row[0] for row in self.con.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=20")])
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_projection_audit_repairs_orphan_flat_fields_tags_and_aliases(self):
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,creator) "
            "VALUES(?,'local',?,?,'video',?)",
            [(5, "/x/5.mp4", "5.mp4", "瀬奈まお 瀬奈まお"),
             (6, "/x/6.mp4", "6.mp4", "画像を拡大する 画像を拡大する")],
        )
        self.con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(15,'performer','瀬奈まお','瀬奈まお','t','t')"
        )
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence) "
            "VALUES(15,'瀬奈まお 瀬奈まお','瀬奈まお 瀬奈まお','user:merge',1.0)"
        )
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(5,15,'performer','javbus:performer',1.0)"
        )
        self.con.executemany(
            "INSERT INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,?,1.0,'javbus:performer')",
            [(5, "演员:瀬奈まお 瀬奈まお"),
             (6, "演员:画像を拡大する 画像を拡大する")],
        )
        rows = collect_repeated_projections(self.con)
        by_name = {row["bad_name"]: row for row in rows}
        self.assertEqual(by_name["瀬奈まお 瀬奈まお"]["action"], "use-performer")
        self.assertEqual(by_name["画像を拡大する 画像を拡大する"]["action"], "remove-invalid")
        counts = apply_repeated_projections(self.con, rows)
        self.con.commit()
        self.assertEqual(self.con.execute(
            "SELECT creator FROM asset WHERE id=5").fetchone()[0], None)
        self.assertEqual(self.con.execute(
            "SELECT creator FROM asset WHERE id=6").fetchone()[0], None)
        self.assertEqual([row[0] for row in self.con.execute(
            "SELECT tag FROM asset_tag WHERE asset_id=5")], ["演员:瀬奈まお"])
        self.assertEqual([row[0] for row in self.con.execute(
            "SELECT tag FROM asset_tag WHERE asset_id=6")], [])
        self.assertEqual([row[0] for row in self.con.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=15")], [])
        self.assertGreaterEqual(counts["flat_cleared"], 2)


class ContainedAssetSetMergeTests(unittest.TestCase):
    """跨 kind 判据放宽到「真子集」后的回归。

    只认作品集合完全相同的话，`哆米`(7 部) 与目录投影 `哆米 Dolmi24`(6 部) 这种
    一侧多出一两部的就永远落在判据外——多出的那几部只被 Stash 认成 performer、
    没有对应本地目录，是常态而不是反证。放宽的部分由名字那一重证据兜底。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,creator) "
            "VALUES(?,'local',?,?,'video',?)",
            [(1, "/x/1.mp4", "1.mp4", "哆米 Dolmi24"),
             (2, "/x/2.mp4", "2.mp4", "哆米 Dolmi24"),
             (3, "/x/3.mp4", "3.mp4", None),
             (4, "/x/4.mp4", "4.mp4", "桉X合集")])
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,?,?,?, 't','t')",
            [(20, "creator", "哆米 Dolmi24", "哆米 dolmi24"),
             (21, "performer", "哆米", "哆米"),
             (22, "creator", "桉X合集", "桉x合集"),
             (23, "performer", "桉X", "桉x")])
        self.con.executemany(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence) "
            "VALUES(?,?,?,?,1.0)",
            [(21, "Dolmi24", "dolmi24", "stash:performer"),
             (23, "lananh9496", "lananh9496", "stash:performer")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(?,?,?,?,1.0)",
            # creator 只挂 1、2；performer 多出第 3 部——真子集，不是相等。
            [(1, 20, "creator", "legacy:asset"), (2, 20, "creator", "legacy:asset"),
             (1, 21, "performer", "performer"), (2, 21, "performer", "performer"),
             (3, 21, "performer", "performer"),
             (4, 22, "creator", "legacy:asset"),
             (4, 23, "performer", "performer")])
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_collect_merges_when_creator_assets_are_a_strict_subset(self):
        rows = collect(self.con)
        by_drop = {row["drop_name"]: row for row in rows}
        self.assertIn("哆米", by_drop)
        merged = by_drop["哆米"]
        # performer 侧只有 Stash 扁平断言，保留带真实目录的 creator。
        self.assertEqual(merged["keep_kind"], "creator")
        self.assertEqual(merged["keep_name"], "哆米 Dolmi24")
        self.assertIn("账号别名", merged["match_evidence"])
        self.assertIn("真包含", merged["match_evidence"])
        self.assertIn("2/3 部", merged["match_evidence"])

    def test_collect_still_refuses_a_name_that_is_not_alias_backed(self):
        """`桉X合集` 的多出词「合集」不是 `桉X` 的已登记别名，仍然留给人工。

        目录名里的「合集」可能是同人的合集，也可能是多人合辑；判子集只说明
        文件重叠，说明不了那一条目录就是这个人。
        """
        self.assertNotIn(
            "桉X", {row["drop_name"] for row in collect(self.con)})


class StageNameDuplicateTests(unittest.TestCase):
    """艺名已登记为别名、那个名字却还自己占一条实体。三道闸都拿实测假阳性做样本。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,code) "
            "VALUES(?,'local',?,?,'video',?)",
            [(1, "/x/1.mp4", "1.mp4", "CEAD-275"), (2, "/x/2.mp4", "2.mp4", "SKY-250"),
             (3, "/x/3.mp4", "3.mp4", "MIZD-998"), (4, "/x/4.mp4", "4.mp4", "ABP-951"),
             (5, "/x/5.mp4", "5.mp4", "DPMX-016"),
             (6, "/x/6.mp4", "6.mp4", "300MIUM-1219")])
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'t','t')",
            [(20, "performer", "森泽佳奈", "森泽佳奈"),
             (21, "performer", "飯岡かなこ", "飯岡かなこ"),
             (22, "performer", "白石亚子", "白石亚子"),
             (23, "performer", "Ako Shiraishi", "ako shiraishi"),
             (24, "performer", "绫乃梓", "绫乃梓"),
             (25, "performer", "NOZOMI", "nozomi")])
        self.con.executemany(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(?,?,?,?,0.9)",
            [(20, "飯岡かなこ", "飯岡かなこ", "r18:performer"),
             (22, "Ako Shiraishi", "ako shiraishi", "r18:performer"),
             (23, "Suzu Hirasawa", "suzu hirasawa", "r18:performer"),
             (23, "平沢すず", "平沢すず", "avdb-actor-mapping@8e2d5b7a"),
             (24, "Nozomi", "nozomi", "r18:performer")])
        self.con.executemany(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
            " VALUES(?,?,'performer',?)",
            [(23, "r18", "平沢すず"), (24, "stash", "538"), (25, "stash", "610")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,?,'performer',?,0.9)",
            [(1, 20, "r18:performer"), (2, 21, "javbus:performer"),
             (3, 23, "r18:performer"), (4, 22, "r18:performer"),
             (5, 24, "r18:performer"), (6, 25, "javbus:performer")])
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def drops(self):
        return {str(row["drop_name"]): row for row in collect(self.con)}

    def test_a_stage_name_registered_as_another_entity_alias_is_a_duplicate(self):
        """r18 早已把 `飯岡かなこ` 记成 `森泽佳奈` 的别名，那个名字却还自己占一条实体。"""
        row = self.drops()["飯岡かなこ"]
        self.assertEqual(row["keep_id"], 20)
        self.assertEqual(row["keep_name"], "森泽佳奈")
        self.assertIn("飯岡かなこ", str(row["match_evidence"]))
        self.assertIn("r18:performer", str(row["match_evidence"]))

    def test_an_entity_whose_own_aliases_name_someone_else_is_left_to_a_human(self):
        """`Ako Shiraishi` 的每个别名和 r18 引用都指向 平沢すず——规范名写错了人。

        并进 `白石亚子` 就是把两位女优搅在一起，不可逆。这条要改的是规范名，不是合并。
        """
        self.assertNotIn("Ako Shiraishi", self.drops())

    def test_two_entities_with_different_ids_at_one_provider_are_two_people(self):
        """`NOZOMI` 是素人企划里的通名，撞上的是 `绫乃梓` 罗马字读音那条别名。

        两条各有自己的 stash performer id，那就是站方也认为的两个人。
        """
        self.assertNotIn("NOZOMI", self.drops())

    def test_a_localization_alias_is_not_evidence_of_a_second_stage_name(self):
        """简繁与本地化别名说的是同一个名字的另一种写法，证不了这是那个人的另一个艺名。"""
        self.con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(30,'performer','綾乃梓','綾乃梓','t','t')")
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(24,'綾乃梓','綾乃梓','kanji-simplification',1.0)")
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(1,30,'performer','legacy:asset',0.8)")
        self.con.commit()
        self.assertNotIn("綾乃梓", self.drops())

    def test_the_merge_keeps_the_alias_holder_and_moves_its_assets(self):
        counts = apply_rows(self.con, collect(self.con))
        self.con.commit()
        self.assertEqual(counts["merged"], 1)
        self.assertIsNone(self.con.execute("SELECT 1 FROM entity WHERE id=21").fetchone())
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM asset_entity WHERE entity_id=20").fetchone()[0], 2)
        self.assertIn("飯岡かなこ", [row[0] for row in self.con.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=20")])


class DirectoryIdentityTests(unittest.TestCase):
    """名录页号是身份，比名字和别名都强：换过艺名的人靠它才认得出来。"""

    LINK = "http://www.prestige-av.com/special/shiraishi_ako.php"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,code)"
            " VALUES(?,'local',?,?,'video',?)",
            [(1, "/x/1.mp4", "1.mp4", "ABP-951"), (2, "/x/2.mp4", "2.mp4", "MIZD-998")])
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,'performer',?,?,'t','t')",
            [(40, "白石亚子", "白石亚子"), (41, "Ako Shiraishi", "ako shiraishi")])
        self.con.executemany(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(?,?,?,'r18:performer',0.9)",
            [(40, "白石あこ", "白石あこ"), (41, "平沢すず", "平沢すず")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,?,'performer','r18:performer',0.9)",
            [(1, 40), (2, 41)])
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def install(self, entity_id, evidence, url=None):
        self.con.execute(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,is_sensitive,"
            "metadata_json,created_at,updated_at)"
            " VALUES(?,'official','T-POWERS',?,'www.prestige-av.com',0,?,'t','t')",
            (entity_id, url or f"{self.LINK}?{entity_id}",
             json.dumps({"evidence": evidence}, ensure_ascii=False)))
        self.con.commit()

    def test_two_entities_on_one_directory_page_are_one_person(self):
        """艺名换过一次，账本各存了一条；两侧各按自己的名字检索，落到同一页。"""
        self.install(40, "minnano-av actress37250 资料表「公式サイト」；经「白石あこ」检索命中")
        self.install(41, "minnano-av actress37250 资料表「公式サイト」；经「平沢すず」检索命中")
        rows = collect(self.con)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["keep_name"], "白石亚子")
        self.assertEqual(rows[0]["drop_name"], "Ako Shiraishi")
        self.assertEqual(rows[0]["evidence"], "名录页号")
        self.assertIn("actress37250", str(rows[0]["match_evidence"]))

    def test_the_localized_name_is_the_one_that_survives(self):
        """用户 2026-09-04 的口径：用更知名的名字，其余叫法留在别名里。"""
        self.install(40, "minnano-av actress37250 经「白石あこ」检索命中")
        self.install(41, "minnano-av actress37250 经「平沢すず」检索命中")
        apply_rows(self.con, collect(self.con))
        self.con.commit()
        self.assertIsNone(self.con.execute("SELECT 1 FROM entity WHERE id=41").fetchone())
        aliases = [row[0] for row in self.con.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=40")]
        self.assertIn("Ako Shiraishi", aliases)
        self.assertIn("平沢すず", aliases)

    def test_two_different_pages_are_two_people(self):
        """同一家事务所的两位女优也会共用一条官网 URL——URL 不是身份，页号才是。"""
        self.install(40, "minnano-av actress37250 经「白石あこ」检索命中", url=self.LINK)
        self.install(41, "minnano-av actress99999 经「平沢すず」检索命中", url=self.LINK)
        self.assertEqual(collect(self.con), [])

    def test_a_link_without_a_page_number_is_not_evidence(self):
        self.install(40, "X @shiraishi 简介外链")
        self.install(41, "X @suzu 简介外链")
        self.assertEqual(collect(self.con), [])

    def test_three_entities_on_one_page_are_left_to_a_human(self):
        """三条要先决定并的顺序，那不是脚本该替人做的。"""
        self.con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(42,'performer','平泽铃','平泽铃','t','t')")
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(1,42,'performer','r18:performer',0.9)")
        for entity_id in (40, 41, 42):
            self.install(entity_id, f"minnano-av actress37250 经「{entity_id}」检索命中")
        self.assertEqual(collect(self.con), [])




class NamedPairTests(unittest.TestCase):
    """命令行点名的一对：证据在站外时，唯一说得清来由的就是那段话。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'t','t')",
            [(1, "performer", "五十岚星兰", "五十岚星兰"),
             (2, "performer", "五十嵐星蘭", "五十嵐星蘭"),
             (3, "studio", "别的种类", "别的种类"),
             (6, "performer", "加山優衣", "加山優衣")])
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def pairs(self, *values, engaged=None):
        return named_pairs(self.con, list(values), engaged)

    def test_the_evidence_is_carried_through_to_the_review_file(self):
        row = self.pairs("1:2:javdb 资料页把五个艺名列在一起")[0]
        self.assertEqual(row["match_evidence"], "javdb 资料页把五个艺名列在一起")
        self.assertEqual((row["keep_id"], row["drop_id"]), (1, 2))
        self.assertEqual(row["drop_name"], "五十嵐星蘭")

    def test_a_pair_without_evidence_is_refused(self):
        """合并不可逆。没有证据的一对说不清来由，比不合更糟。"""
        with self.assertRaises(SystemExit):
            self.pairs("1:2:")
        with self.assertRaises(SystemExit):
            self.pairs("1:2")

    def test_two_different_kinds_are_never_the_same_identity(self):
        with self.assertRaises(SystemExit):
            self.pairs("1:3:随便什么理由")

    def test_an_entity_already_planned_by_a_detector_is_refused(self):
        """自动判据先合会把 id 删掉，第二趟再引用同一个 id 就是合进一条不存在的实体。"""
        with self.assertRaises(SystemExit):
            self.pairs("1:2:证据", engaged={2})

    def test_a_missing_entity_is_refused(self):
        with self.assertRaises(SystemExit):
            self.pairs("1:99:证据")

    def test_one_entity_on_both_sides_is_refused(self):
        with self.assertRaises(SystemExit):
            self.pairs("1:1:证据")

    def test_one_survivor_can_take_in_several_old_stage_names(self):
        """一个人有几个旧艺名就有几条要并进来，保留侧当然会重复出现。"""
        rows = self.pairs("1:2:javdb 同一页", "1:6:javdb 同一页")
        self.assertEqual([(row["keep_id"], row["drop_id"]) for row in rows],
                         [(1, 2), (1, 6)])

    def test_an_entity_dropped_earlier_cannot_come_back_as_a_survivor(self):
        """前一对已经把它删了，再拿它当保留侧就是往一条不存在的实体里搬东西。"""
        with self.assertRaises(SystemExit):
            self.pairs("1:2:证据", "2:6:另一段证据")

    def test_an_entity_dropped_earlier_cannot_be_dropped_twice(self):
        with self.assertRaises(SystemExit):
            self.pairs("1:2:证据", "6:2:另一段证据")

    def test_a_survivor_cannot_turn_into_a_drop_later(self):
        """顺序写反：先说留它，后面又要丢它，两条不可能同时成立。"""
        with self.assertRaises(SystemExit):
            self.pairs("1:2:证据", "6:1:另一段证据")

    def test_merging_series_rewrites_the_flat_column(self):
        """只搬关系不改 `asset.series`，卡片上还写着被丢弃的系列名。"""
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,'series',?,?,'t','t')",
            [(4, "Night Safari", "night safari"), (5, "旧写法", "旧写法")])
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,code,series)"
            " VALUES(?,'local',?,?,'video',?,?)",
            [(1, "/x/1.mp4", "1.mp4", "AKA-022", "旧写法"),
             (2, "/x/2.mp4", "2.mp4", "AKA-024", "Night Safari")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source)"
            " VALUES(?,?,'series','r18:series')",
            [(1, 5), (2, 4)])
        self.con.commit()
        with self.con:
            counts = apply_rows(self.con, self.pairs("4:5:外部资料页说这两部同属一个系列"))
        self.assertEqual(counts["merged"], 1)
        self.assertEqual(
            [row[0] for row in self.con.execute("SELECT series FROM asset ORDER BY id")],
            ["Night Safari", "Night Safari"])


class PairsOnlyTests(unittest.TestCase):
    """`--pairs-only` 只执行人工点名那批，自动判据整趟不跑。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,creator)"
            " VALUES(?,'local',?,?,'video',?)",
            [(1, "/x/1.mp4", "1.mp4", "小桃"), (2, "/x/2.mp4", "2.mp4", "小桃")])
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'t','t')",
            [(10, "creator", "小桃", "小桃"),
             (11, "performer", "小桃", "小桃"),
             (20, "performer", "五十岚星兰", "五十岚星兰"),
             (21, "performer", "五十嵐星蘭", "五十嵐星蘭")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,?,?,?,1.0)",
            [(1, 10, "creator", "legacy:asset"), (2, 10, "creator", "legacy:asset"),
             (1, 11, "performer", "performer"),
             (1, 21, "performer", "performer")])
        self.con.commit()
        self.con.close()
        self.review = self.root / "merge.csv"
        self.projections = self.root / "projections.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, *extra):
        args = build_parser().parse_args([
            "--db", str(self.db),
            "--review-csv", str(self.review),
            "--projection-review-csv", str(self.projections),
            *extra,
        ])
        return run(args)

    def entity_ids(self):
        connection = sqlite3.connect(self.db)
        try:
            return [row[0] for row in connection.execute(
                "SELECT id FROM entity ORDER BY id")]
        finally:
            connection.close()

    def test_the_detectors_still_run_without_the_switch(self):
        """对照：不给开关时，同名那组照旧进复核 CSV。"""
        self.assertEqual(self.invoke(), 0)
        self.assertIn("小桃", {row["drop_name"] for row in read_rows(self.review)})

    def test_only_the_named_pair_reaches_the_review_file(self):
        self.assertEqual(self.invoke("--pairs-only", "--pair", "20:21:javdb 同一页"), 0)
        rows = read_rows(self.review)
        self.assertEqual([row["drop_id"] for row in rows], ["21"])
        self.assertEqual(rows[0]["evidence"], "人工指定")
        self.assertEqual(read_rows(self.projections), [])

    def test_the_detected_group_is_not_written_to_the_ledger(self):
        self.assertEqual(self.invoke(
            "--pairs-only", "--pair", "20:21:javdb 同一页",
            "--apply", "--backup", str(self.root / "backup.db")), 0)
        self.assertEqual(self.entity_ids(), [10, 11, 20])

    def test_the_switch_without_a_pair_is_refused(self):
        """只处理人工那批却一对也没给，跑下去就是一趟什么都不做的空写入。"""
        with self.assertRaises(SystemExit) as caught:
            self.invoke("--pairs-only")
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(self.entity_ids(), [10, 11, 20, 21])


class ScrapedPerformerProvenanceTests(unittest.TestCase):
    """刮削链新建的 performer 也是发行元数据，保留它而不是目录名投影出来的 creator。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.con.execute(
            "INSERT INTO asset(id,location,path,name,medium,creator)"
            " VALUES(1,'local','/x/1.mp4','1.mp4','video','三上悠亜')")
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,'三上悠亜','三上悠亜','t','t')",
            [(1, "creator"), (2, "performer")])
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def link(self, source):
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(1,1,'creator','legacy:asset',1.0)")
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(1,2,'performer',?,1.0)", (source,))
        self.con.commit()
        return collect(self.con)[0]

    def test_a_javinizer_source_keeps_the_performer(self):
        for source in ("javinizer:javdb:performer", "javinizer:r18dev:performer",
                       "javinizer:dmm:performer"):
            with self.subTest(source=source):
                self.con.execute("DELETE FROM asset_entity")
                row = self.link(source)
                self.assertEqual((row["keep_kind"], row["keep_id"]), ("performer", 2))
                self.assertEqual(row["evidence"], "发行元数据")

    def test_the_stash_flat_source_still_keeps_the_creator(self):
        row = self.link("performer")
        self.assertEqual((row["keep_kind"], row["keep_id"]), ("creator", 1))


if __name__ == "__main__":
    unittest.main()
