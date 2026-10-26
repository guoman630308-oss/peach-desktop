"""事务所是实体：有资料页、有成员、搜得到。"""
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach import web_contract as rm_web
from peach.entities import merge_entity
from peach.migrations import needs_foreign_keys_off, upgrade
from peach.web_entity import PROFILE_KINDS, scope_predicate
from scripts.dedupe_entity_links import collect as dedupe_plan
from scripts.dedupe_entity_links import destination
from scripts.install_agencies import collect as agency_plan
from scripts.install_agencies import apply_rows as install_agencies
from scripts.install_agencies import split_name

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
STAMP = "2026-09-05T00:00:00Z"


class ForeignKeysOffMarkerTests(unittest.TestCase):
    """重建父表的迁移要能关掉外键，而且只有它能。"""

    def test_the_marker_is_read_from_the_leading_comment_block(self):
        self.assertTrue(needs_foreign_keys_off(
            "-- peach:foreign_keys=off\n-- 说明\nCREATE TABLE t(id INTEGER);\n"))

    def test_a_migration_without_the_marker_keeps_foreign_keys_on(self):
        self.assertFalse(needs_foreign_keys_off("-- 说明\nCREATE TABLE t(id INTEGER);\n"))

    def test_the_marker_after_the_first_statement_does_not_count(self):
        """pragma 要在整个文件开跑之前决定，写在语句之间等于没写。"""
        self.assertFalse(needs_foreign_keys_off(
            "CREATE TABLE t(id INTEGER);\n-- peach:foreign_keys=off\n"))

    def test_rebuilding_entity_keeps_every_child_row(self):
        """外键开着时 DROP TABLE entity 会 CASCADE 删空子表，这条迁移不能那样。"""
        with tempfile.TemporaryDirectory() as tmp:
            older = Path(tmp).resolve() / "migrations"
            older.mkdir()
            for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
                if path.name < "0025":
                    shutil.copy2(path, older / path.name)
            db = Path(tmp).resolve() / "ledger.db"
            upgrade(db, older)
            con = sqlite3.connect(db)
            con.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,"
                        "created_at,updated_at) VALUES(7,'performer','阿花','阿花','t','t')")
            con.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                        " VALUES(7,'Hana','hana','test')")
            con.execute("INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,"
                        "created_at,updated_at) VALUES(7,'social','X','https://x.com/h','x.com','t','t')")
            con.commit()
            con.close()

            upgrade(db, MIGRATIONS)

            con = sqlite3.connect(db)
            try:
                counts = (con.execute("SELECT count(*) FROM entity_alias").fetchone()[0],
                          con.execute("SELECT count(*) FROM entity_link").fetchone()[0])
                name = con.execute("SELECT canonical_name FROM entity WHERE id=7").fetchone()[0]
                broken = len(con.execute("PRAGMA foreign_key_check").fetchall())
            finally:
                # Windows 上临时目录删不掉带着打开句柄的库文件，断言失败也要先关。
                con.close()
        self.assertEqual(counts, (1, 1))
        self.assertEqual(name, "阿花")
        self.assertEqual(broken, 0)

    def test_the_search_index_trigger_survives_the_rebuild(self):
        """DROP TABLE 把挂在 entity 上的触发器一起带走，迁移必须补回来。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp).resolve() / "ledger.db"
            upgrade(db, MIGRATIONS)
            con = sqlite3.connect(db)
            try:
                triggers = con.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='trigger'"
                    " AND name='asset_search_entity_update'").fetchone()[0]
            finally:
                con.close()
        self.assertEqual(triggers, 1)

    def test_agency_is_an_accepted_entity_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp).resolve() / "ledger.db"
            upgrade(db, MIGRATIONS)
            con = sqlite3.connect(db)
            try:
                con.execute(
                    "INSERT INTO entity(kind,canonical_name,normalized_name,"
                    "created_at,updated_at) VALUES('agency','Capsule','capsule','t','t')")
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO entity(kind,canonical_name,normalized_name,"
                        "created_at,updated_at) VALUES('label','X','x','t','t')")
            finally:
                con.close()


class AgencyNameTests(unittest.TestCase):
    """括号里是读音或旧名，括号外才是现用名。"""

    def test_a_plain_name_has_no_aliases(self):
        self.assertEqual(split_name("Capsule Agency"), ("Capsule Agency", []))

    def test_the_reading_in_parentheses_becomes_an_alias(self):
        self.assertEqual(split_name("ACT(アクト)"), ("ACT", ["アクト"]))

    def test_a_former_name_loses_its_prefix(self):
        self.assertEqual(split_name("Wish(元・GIRFY)"), ("Wish", ["GIRFY"]))

    def test_a_former_name_after_the_closing_bracket_is_kept(self):
        self.assertEqual(
            split_name("SO MODEL AGENT(ソウ モデルエージェント)旧・Eightman Production"),
            ("SO MODEL AGENT", ["ソウ モデルエージェント", "Eightman Production"]))


class LinkDestinationTests(unittest.TestCase):
    """写法不同、去处相同的链接是一条，不是两条。"""

    def test_a_trailing_slash_is_the_same_destination(self):
        self.assertEqual(destination("http://kawakami-yuu.livedoor.biz"),
                         destination("http://kawakami-yuu.livedoor.biz/"))

    def test_a_post_belongs_to_the_same_account_as_its_profile(self):
        self.assertEqual(destination("https://x.com/yu_kinao"),
                         destination("https://x.com/yu_kinao/status/1221041995415580672"))

    def test_two_accounts_on_one_platform_stay_apart(self):
        self.assertNotEqual(destination("https://x.com/one"), destination("https://x.com/two"))

    def test_a_query_string_still_distinguishes_pages(self):
        self.assertNotEqual(destination("https://a.jp/t?id=3"), destination("https://a.jp/t?id=4"))

    def test_percent_escapes_are_compared_by_byte_not_by_case(self):
        """凉森玲梦的官网被两个来源各按自己的大小写写了一遍，指向同一个页面。"""
        self.assertEqual(
            destination("https://www.t-powers.co.jp/talent/%e6%b6%bc%e6%a3%ae%e3%82%8c%e3%82%80/"),
            destination("https://www.t-powers.co.jp/talent/%E6%B6%BC%E6%A3%AE%E3%82%8C%E3%82%80/"))

    def test_two_escaped_paths_that_differ_stay_apart(self):
        self.assertNotEqual(destination("https://a.jp/t/%E6%B6%BC"),
                            destination("https://a.jp/t/%E6%A3%AE"))


class AgencyLedgerTests(unittest.TestCase):
    """按真实形状建一份小账本：两家事务所、三位女优、几条链接。"""

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
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,metadata_json,"
            "created_at,updated_at) VALUES(?,?,?,?,?,'t','t')",
            [(11, "performer", "七泽美亚", "七泽美亚",
              '{"agency":{"name":"Capsule Agency","checked_at":"2026-09-04T00:00:00Z"}}'),
             (12, "performer", "神雪", "神雪",
              '{"agency":{"name":"Capsule Agency","checked_at":"2026-09-04T00:00:00Z"}}'),
             (13, "performer", "白石亚子", "白石亚子",
              '{"agency":{"name":"ACT(アクト)","checked_at":"2026-09-04T00:00:00Z"}}'),
             (20, "tag", "足交", "足交", "{}")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,?,?,'test',1.0)",
            [(1, 11, "performer"), (2, 12, "performer"), (3, 13, "performer"),
             (1, 20, "tag")])
        # 归属域名的共识：三条同标签的链接指向同一个主机。
        self.con.executemany(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,"
            "created_at,updated_at) VALUES(?,'official','ACT(アクト)',?,"
            "'actentertainment.jp','t','t')",
            [(11, "https://actentertainment.jp/a"), (12, "https://actentertainment.jp/b"),
             (13, "https://actentertainment.jp/c")])
        self.con.commit()
        rows = agency_plan(self.con)
        install_agencies(self.con, rows, STAMP)
        self.con.commit()
        self.logos = root / "logos"; self.logos.mkdir()
        self.avatars = root / "avatars"; self.avatars.mkdir()
        self.contract = rm_web.WebContract(
            self.db, avatar_root=self.avatars, logo_root=self.logos, cover_root=root / "covers")

    def page(self, kind, name):
        return rm_web.q_entity(self.contract, {"kind": kind, "name": name})

    def photos(self, name, kind="agency"):
        return rm_web.q_entity_photos(self.contract, {"kind": kind, "name": name})

    def counted_once(self, photos):
        """总数、分页和图集数的是同一张图：各一次，图集字节数也只算一份。"""
        self.assertEqual(
            (photos["total"], [item["id"] for item in photos["items"]],
             [(item["kind"], item["n"], item["bytes"]) for item in photos["sets"]]),
            (1, [21], [("dir", 1, 10)]))

    def add_pictures(self, rows):
        """(id, 路径, 挂在哪几位名下, disposal)。"""
        for asset_id, path, owners, disposal in rows:
            self.con.execute(
                "INSERT INTO asset(id,location,path,name,medium,size,first_seen,disposal)"
                " VALUES(?,'local',?,?,'image',10,'2026-01-01',?)",
                (asset_id, path, path.rsplit("\\", 1)[-1], disposal))
            self.con.executemany(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
                " VALUES(?,?,'performer','test',1.0)", [(asset_id, owner) for owner in owners])
        self.con.commit()

    def add_samples(self, codes):
        """作品 id → 番号，每个番号配一张官方样张。"""
        for asset_id, code in codes.items():
            self.con.execute("UPDATE asset SET code=? WHERE id=?", (code, asset_id))
            self.con.execute(
                "INSERT INTO code_sample_image(code,position,url,site,source,fetched_at)"
                " VALUES(?,1,'https://pics.dmm.co.jp/x.jpg','dmm','test',?)", (code, STAMP))
        self.con.commit()

    def test_every_performer_with_an_agency_gets_exactly_one_membership(self):
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM entity_membership").fetchone()[0], 3)

    def test_the_agency_profile_counts_the_works_of_its_members(self):
        page = self.page("agency", "Capsule Agency")
        self.assertEqual(page["asset_count"], 2)
        self.assertEqual(page["member_count"], 2)

    def test_the_agency_profile_lists_its_roster_not_co_stars(self):
        names = [person["k"] for person in self.page("agency", "Capsule Agency")
                 ["related_performers"]]
        self.assertEqual(sorted(names), ["七泽美亚", "神雪"])

    def test_the_agency_profile_carries_the_tags_of_its_members_works(self):
        self.assertIn("足交", [tag["k"] for tag in
                              self.page("agency", "Capsule Agency")["tags"]])

    def test_the_agency_representative_is_picked_from_its_members_works(self):
        """ACT 那位的片更大，但不是这家的。"""
        self.con.executemany("UPDATE asset SET snapshot_path='s.jpg',size=? WHERE id=?",
                             [(200, 1), (300, 2), (900, 3)])
        self.con.commit()
        self.assertEqual(self.page("agency", "Capsule Agency")["representative_asset_id"], 2)

    def test_the_agency_photos_and_sample_sets_are_those_of_its_members(self):
        """事务所自己不挂图：图集、总数、分页和番号样张集都从成员名下取。

        ACT 那位的图和片不算，回收站里的图也不算。
        """
        self.add_pictures([
            (21, r"R:\Media\summer\01.jpg", [11], None),
            (22, r"R:\Media\summer\02.jpg", [12], None),
            (23, r"R:\Media\winter\01.jpg", [12], None),
            (24, r"R:\Media\summer\03.jpg", [11], "trash"),
            (25, r"R:\Media\act\01.jpg", [13], None)])
        self.add_samples({1: "SSIS-057", 2: "SSIS-058", 3: "IPX-001"})
        photos = self.photos("Capsule Agency")
        self.assertEqual([(item["kind"], item["id"], item["n"]) for item in photos["sets"]],
                         [("code", "code:SSIS-057", 1), ("code", "code:SSIS-058", 1),
                          ("dir", 21, 2), ("dir", 23, 1)])
        self.assertEqual((photos["total"], photos["sample_total"]), (3, 2))
        self.assertEqual([item["id"] for item in photos["items"]], [21, 22, 23])

    def test_a_picture_of_two_members_is_counted_and_listed_once(self):
        self.add_pictures([(21, r"R:\Media\duo\01.jpg", [11, 12], None)])
        self.counted_once(self.photos("Capsule Agency"))

    def test_a_picture_filed_twice_under_one_entity_is_counted_once(self):
        """主键含 role 与 source：同一位名下，复核再写一行也还是同一张图。"""
        self.add_pictures([(21, r"R:\Media\solo\01.jpg", [11], None)])
        self.con.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
                         " VALUES(21,11,'performer','review',1.0)")
        self.con.commit()
        self.counted_once(self.photos("七泽美亚", "performer"))

    def test_a_performer_profile_points_at_her_agency_entity(self):
        home = self.page("performer", "七泽美亚")["agency"]
        self.assertEqual(home["canonical_name"], "Capsule Agency")
        self.assertEqual(home["checked_at"], "2026-09-04T00:00:00Z")

    def test_an_agency_profile_has_no_agency_of_its_own(self):
        self.assertIsNone(self.page("agency", "Capsule Agency")["agency"])

    def test_the_agency_is_reachable_by_its_former_name(self):
        """`ACT(アクト)` 的读音留成别名，按它也要能打开这一页。"""
        self.assertEqual(self.page("agency", "アクト")["canonical_name"], "ACT")

    def test_the_agency_keeps_its_own_official_site(self):
        page = self.page("agency", "ACT")
        self.assertEqual([link["url"] for link in page["links"]],
                         ["https://actentertainment.jp/"])

    def test_agency_is_a_profile_kind(self):
        self.assertIn("agency", PROFILE_KINDS)

    def test_an_unknown_kind_is_still_refused(self):
        self.assertEqual(rm_web.q_entity(self.contract, {"kind": "label", "name": "x"}),
                         {"error": "invalid entity"})

    def test_the_catalog_can_be_filtered_by_agency(self):
        items = rm_web.q_items(self.contract, {"agency": "Capsule Agency", "limit": "10"})
        self.assertEqual(sorted(item["id"] for item in items["items"]), [1, 2])

    def test_searching_an_agency_name_finds_the_works_of_its_members(self):
        """用户搜「Capsule」时，账本里确实有它，只是此前没有一条路把它接上作品。"""
        items = rm_web.q_items(self.contract, {"q": "Capsule", "limit": "10"})
        self.assertEqual(sorted(item["id"] for item in items["items"]), [1, 2])

    def test_a_short_query_reaches_the_agency_too(self):
        """短查询走 LIKE 分支，两条分支要覆盖同样的身份写法。"""
        items = rm_web.q_items(self.contract, {"q": "AC", "limit": "10"})
        self.assertEqual([item["id"] for item in items["items"]], [3])

    def test_agency_facets_come_from_the_same_rows_as_its_items(self):
        facets = rm_web.q_facets(self.contract, scope_kind="agency",
                                 scope_name="Capsule Agency")
        self.assertEqual(sum(row["n"] for row in facets["locations"]), 2)

    def index(self, kind, q=""):
        return {row["k"]: row for row in
                rm_web.q_index(self.contract, kind, q=q, limit=50)["items"]}

    def test_the_agency_index_lists_every_agency_by_headcount(self):
        """事务所自己不挂作品，一家有多少艺人才是它的规模。"""
        rows = self.index("agencies")
        self.assertEqual(sorted(rows), ["ACT", "Capsule Agency"])
        self.assertEqual(rows["Capsule Agency"]["members"], 2)
        self.assertEqual(rows["ACT"]["members"], 1)

    def test_the_agency_index_counts_works_the_same_way_the_profile_does(self):
        """判据只有 `scope_predicate` 一份，两个页面数出来的才是同一个数。"""
        rows = self.index("agencies")
        for name in rows:
            with self.subTest(agency=name):
                self.assertEqual(rows[name]["n"], self.page("agency", name)["asset_count"])

    def test_the_agency_index_hands_out_its_own_mark_not_a_members_frame(self):
        """索引页那格图取的是官网圆标：作品截图是某位成员某部片的画面。"""
        rows = self.index("agencies")
        self.assertIsNotNone(rows["ACT"]["mark"])
        self.assertFalse(rows["ACT"]["has_avatar"])

    def test_the_agency_index_can_be_filtered_by_name(self):
        self.assertEqual(sorted(self.index("agencies", q="Capsule")), ["Capsule Agency"])

    def test_both_halves_of_the_maker_switch_have_an_index(self):
        """开关的两半都得有索引页，否则那个开关有一边是死的。"""
        for kind in ("studios", "agencies"):
            with self.subTest(kind=kind):
                self.assertEqual(rm_web.q_index(self.contract, kind, limit=5)["kind"], kind)

    def test_the_agency_asks_whether_its_logo_is_installed(self):
        """标识按名字落盘，厂牌和事务所同一个仓：两边都得先问过再出图。"""
        self.assertIn("has_logo", self.page("agency", "ACT"))
        self.assertIn("has_logo", self.index("agencies")["ACT"])
        (self.logos / "ACT.img").write_bytes(b"x")
        self.contract = rm_web.WebContract(
            self.db, avatar_root=self.avatars, logo_root=self.logos)
        self.assertTrue(self.page("agency", "ACT")["has_logo"])

    def test_the_agency_profile_hands_out_the_link_id_for_its_mark(self):
        page = self.page("agency", "ACT")
        self.assertEqual(page["mark_link_id"],
                         next(link["link_id"] for link in page["links"]))

    def test_an_archived_official_site_is_listed_but_not_used_as_the_mark(self):
        """关门事务所的官网只剩存档快照：链接照列，门面不取存档站的圆标。"""
        agency_id = self.con.execute(
            "SELECT id FROM entity WHERE kind='agency' AND canonical_name='Capsule Agency'"
        ).fetchone()[0]
        self.con.execute("DELETE FROM entity_link WHERE entity_id=?", (agency_id,))
        self.con.execute(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,created_at,updated_at)"
            " VALUES(?,'official','官网存档（2020-01）',"
            "'https://web.archive.org/web/20200101000000/https://capsule.bz/','web.archive.org',"
            "'t','t')", (agency_id,))
        self.con.commit()
        page = self.page("agency", "Capsule Agency")
        self.assertEqual([link["hostname"] for link in page["links"]], ["web.archive.org"])
        self.assertIsNone(page["mark_link_id"])
        self.assertIsNone(self.index("agencies")["Capsule Agency"]["mark"])

    def test_the_agency_roster_carries_the_focus_the_big_layout_needs(self):
        """名册摆的是竖幅大图，几何居中会切掉脸，取景要随资料一起下发。"""
        roster = self.page("agency", "Capsule Agency")["related_performers"]
        self.assertTrue(roster)
        for person in roster:
            with self.subTest(person=person["k"]):
                self.assertIn("avatar_focus", person)

    def test_the_scope_predicate_takes_one_placeholder_either_way(self):
        self.assertEqual(scope_predicate("performer", "ae.entity_id").count("?"), 1)
        self.assertEqual(scope_predicate("agency", "ae.entity_id").count("?"), 1)

    def test_the_index_scope_predicate_reads_the_agency_from_each_row(self):
        """索引页一句 SQL 数完所有事务所，判据那一侧填的是列名不是占位符。"""
        self.assertIn("agency_id=e.id", scope_predicate("agency", "ae.entity_id", "e.id"))

    def test_merging_a_performer_carries_her_membership_over(self):
        self.con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(99,'performer','Mia Nanasawa','mia nanasawa','t','t')")
        moved = merge_entity(self.con, target_id=99, source_id=11,
                             source_name="七泽美亚", alias_source="merge:test")
        # 资料页读的是另一条只读连接，不提交就还看得见合并前的账本。
        self.con.commit()
        self.assertEqual(moved["memberships"], 1)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM entity_membership WHERE member_id=11").fetchone()[0], 0)
        self.assertEqual(self.page("agency", "Capsule Agency")["member_count"], 2)

    def test_merging_two_agencies_moves_the_roster(self):
        target = self.con.execute(
            "SELECT id FROM entity WHERE kind='agency' AND canonical_name='ACT'").fetchone()[0]
        source = self.con.execute(
            "SELECT id FROM entity WHERE kind='agency' AND canonical_name='Capsule Agency'"
        ).fetchone()[0]
        moved = merge_entity(self.con, target_id=target, source_id=source,
                             source_name="Capsule Agency", alias_source="merge:test")
        self.con.commit()
        self.assertEqual(moved["members"], 2)
        self.assertEqual(self.page("agency", "ACT")["member_count"], 3)

    def test_duplicate_links_on_one_entity_collapse_to_the_https_one(self):
        self.con.executemany(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,"
            "created_at,updated_at) VALUES(11,'social','X',?,'x.com','t','t')",
            [("https://x.com/mia",), ("https://x.com/mia/status/1",)])
        self.con.execute(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,"
            "created_at,updated_at) VALUES(12,'official','ACT',"
            "'http://actentertainment.jp/b','actentertainment.jp','t','t')")
        self.con.commit()
        verdicts = {row["url"]: row["verdict"] for row in dedupe_plan(self.con)}
        self.assertEqual(verdicts["https://x.com/mia"], "keep")
        self.assertEqual(verdicts["https://x.com/mia/status/1"], "delete")
        self.assertEqual(verdicts["https://actentertainment.jp/b"], "keep")
        self.assertEqual(verdicts["http://actentertainment.jp/b"], "delete")


class DeepLinkRouteTests(unittest.TestCase):
    """资料页的地址得能直接打开，而不只是在应用里点得到。"""

    def test_every_entity_route_is_served_as_a_page(self):
        """前端认得的每一种实体路由，服务端都要发页面。

        少一条不会报错，只会在直接打开地址时回 404：应用内点进去照常，刷新一下就没了。
        所以判据取自前端 core 的路由表本身，加一种实体就自动多一条要求。
        """
        routes = re.search(r"const ENTITY_ROUTES(?::[^=]*)?=\{([^}]*)\}",
                           (ROOT / "frontend" / "src" / "core" / "index.ts").read_text(encoding="utf-8"))
        self.assertIsNotNone(routes, "frontend/src/core/index.ts 里没有 ENTITY_ROUTES")
        wanted = re.findall(r"[:,]\s*'([a-z]+)'", "," + routes.group(1))
        self.assertIn("agencies", wanted, "路由表没解析出来")
        pages = (ROOT / "src" / "peach" / "routes_pages.py").read_text(encoding="utf-8")
        for route in wanted:
            with self.subTest(route=route):
                self.assertIn(f'"/{route}/{{name:path}}"', pages)


if __name__ == "__main__":
    unittest.main()
