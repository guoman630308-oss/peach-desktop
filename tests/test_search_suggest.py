"""搜索栏补全：补出来的每一项都点得开，口径与 `/api/items` 同源。

这一域最容易出的不是报错，是两侧漂开：补全按一套判据取词、搜索按另一套判据找片，
于是下拉里给出的名字搜出零条，或者搜得到的标签补不出来。所以这里的断言分两层——
一层钉具体行为，一层遍历每个补全项拿它去真的搜一次。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach import web_contract as rm_web
from peach.catalog_rules import normalise_code_key
from peach.web_entity import SUGGEST_GROUPS, suggest_kinds

from support.ledger import fresh_ledger

STAMP = "2026-09-07T00:00:00Z"


class LedgerFixture(unittest.TestCase):
    """一份小账本：几部片挂着一位有别名的女优，另有厂牌、系列、标签和事务所。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.db = fresh_ledger(root)
        self.con = sqlite3.connect(self.db)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,first_seen,code,"
            "catalog_title,disposal) VALUES(?,'local',?,?,'video',100,'2026-01-01',?,?,?)",
            [(1, r"R:\Media\one.mp4", "one.mp4", None, None, None),
             (2, r"R:\Media\two.mp4", "two.mp4", None, None, None),
             (3, r"R:\Media\three.mp4", "three.mp4", None, None, None),
             (4, r"R:\Media\ABW-123.mp4", "ABW-123.mp4", "ABW-123", "潜入取材の一日", None),
             (5, r"R:\Media\ABW-999.mp4", "ABW-999.mp4", "ABW-999", "已删除的一部", "trash"),
             # 既无番号也无发行标题，只有一个相机文件名。
             (6, r"R:\Media\IMG_2757_682.MOV", "IMG_2757_682.MOV", None, None, None),
             # 文件名里有别人的番号，它自己没有番号。
             (7, r"R:\Media\old-ABW-clip.mp4", "old-ABW-clip.mp4", None, None, None)],
        )
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'t','t')",
            [(11, "performer", "涼森れむ", "涼森れむ"),
             (12, "performer", "神雪", "神雪"),
             (13, "studio", "ラグジュTV", "ラグジュtv"),
             (14, "series", "ドキュメント", "ドキュメント"),
             (15, "tag", "足交", "足交"),
             # 与女优同名的标签是另一个身份的冒充，标签组不该收它。
             (16, "tag", "神雪", "神雪"),
             # 时长标签是筛选控件，不是可搜的词。
             (17, "tag", "短片-2分内", "短片-2分内"),
             (18, "agency", "Capsule Agency", "capsule agency"),
             # 名下没有任何作品的人和事务所：账本里有身份，按名字搜是空的。
             (19, "performer", "涼森みつ", "涼森みつ"),
             (20, "agency", "Capsule Reserve", "capsule reserve"),
             # 只出现在回收站那条上的标签。
             (21, "tag", "足底", "足底"),
             # 三个字的标签落在搜索的 FTS 分支上，两个字的落在 LIKE 分支上。
             (22, "tag", "巨乳系", "巨乳系"),
             # 同一个字头的一批标签，用来看每组的条数上限。
             (23, "tag", "足元", "足元"), (24, "tag", "足指", "足指"),
             (25, "tag", "足首", "足首"), (26, "tag", "足裏", "足裏"),
             (27, "tag", "足音", "足音"),
             # 「MO」是 MOODYZ 的词首、SO MODEL AGENT 里一个词的词首，
             # 而在 kemonokai 中间。两个字母的拉丁输入只该命中前两个。
             (28, "studio", "MOODYZ", "moodyz"),
             (29, "creator", "kemonokai", "kemonokai"),
             (30, "agency", "SO MODEL AGENT", "so model agent")],
        )
        self.con.executemany(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
            " VALUES(?,?,?,'test')",
            [(11, "凉森", "凉森"), (18, "カプセル", "カプセル")],
        )
        self.con.execute(
            "INSERT INTO entity_search_term(entity_id,term,purpose,source,created_at)"
            " VALUES(11,'Remu','discovery','test',?)", (STAMP,))
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,?,?,'test',1.0)",
            [(1, 11, "performer"), (2, 11, "performer"), (3, 11, "performer"),
             (1, 12, "performer"), (1, 13, "studio"), (2, 13, "studio"),
             (2, 14, "series"), (1, 15, "tag"), (2, 15, "tag"), (3, 16, "tag"),
             (3, 17, "tag"), (5, 21, "tag"), (1, 22, "tag"), (2, 22, "tag"),
             (1, 23, "tag"), (1, 24, "tag"), (1, 25, "tag"), (1, 26, "tag"),
             (1, 27, "tag"), (3, 28, "studio"), (3, 29, "creator")],
        )
        self.con.executemany(
            "INSERT INTO entity_membership(member_id,agency_id,source,checked_at)"
            " VALUES(?,?,'test',?)",
            [(11, 18, STAMP), (12, 18, STAMP), (19, 20, STAMP), (29, 30, STAMP)],
        )
        self.con.commit()
        self.contract = rm_web.WebContract(self.db)

    def suggest(self, query, limit=5):
        return rm_web.q_suggest(self.contract, query, limit)

    def group(self, query, kind, limit=5):
        for group in self.suggest(query, limit)["groups"]:
            if group["kind"] == kind:
                return group["items"]
        return []

    def values(self, query, kind, limit=5):
        return [item["value"] for item in self.group(query, kind, limit)]

    def total(self, query):
        return rm_web.q_items(self.contract, {"q": query, "limit": "10"})["total"]


class SuggestTests(LedgerFixture):
    """补全按输入给出馆藏里的身份与作品。"""

    def test_a_canonical_name_completes_from_its_first_characters(self):
        self.assertEqual(self.values("涼森", "performer"), ["涼森れむ"])

    def test_the_suggestion_carries_how_many_works_are_behind_it(self):
        self.assertEqual(self.group("涼森", "performer")[0]["n"], 3)

    def test_an_alias_completes_to_the_canonical_name(self):
        """输入的写法是别名，补出来的必须是这个人在馆藏里的统称。"""
        self.assertEqual(self.values("凉森", "performer"), ["涼森れむ"])

    def test_a_hit_on_an_alias_says_which_writing_matched(self):
        """不说明凭什么，用户会以为补错了人。"""
        self.assertEqual(self.group("凉森", "performer")[0]["matched"], "凉森")

    def test_a_hit_on_the_canonical_name_says_nothing_extra(self):
        self.assertEqual(self.group("涼森", "performer")[0]["matched"], "")

    def test_a_search_term_reaches_the_same_person(self):
        self.assertEqual(self.values("Remu", "performer"), ["涼森れむ"])

    def test_a_person_with_no_works_never_shows_up(self):
        """`涼森みつ` 的名字也以「涼森」开头，但她名下一部片都没有。"""
        self.assertNotIn("涼森みつ", self.values("涼森", "performer"))

    def test_studios_and_series_have_their_own_groups(self):
        self.assertEqual(self.values("ラグジュ", "studio"), ["ラグジュTV"])
        self.assertEqual(self.values("ドキュ", "series"), ["ドキュメント"])

    def test_an_agency_counts_the_works_of_its_members(self):
        self.assertEqual(self.group("Capsule", "agency")[0]["n"], 3)

    def test_an_agency_reachable_only_by_its_former_name_still_completes(self):
        self.assertEqual(self.values("カプセル", "agency"), ["Capsule Agency"])

    def test_an_agency_whose_roster_has_no_works_never_shows_up(self):
        self.assertNotIn("Capsule Reserve", self.values("Capsule", "agency"))

    def test_a_tag_completes_like_any_other_identity(self):
        self.assertIn("足交", self.values("足交", "tag"))

    def test_a_tag_that_is_really_a_performer_name_stays_out(self):
        self.assertEqual(self.values("神雪", "tag"), [])
        self.assertEqual(self.values("神雪", "performer"), ["神雪"])

    def test_a_length_tag_is_a_filter_control_not_a_word(self):
        self.assertEqual(self.values("短片", "tag"), [])

    def test_a_tag_only_seen_in_the_trash_stays_out(self):
        self.assertEqual(self.values("足底", "tag"), [])

    def test_a_code_completes_to_a_work_that_can_be_opened(self):
        items = self.group("ABW", "asset")
        self.assertEqual(items[0]["value"], "ABW-123")
        self.assertEqual(items[0]["id"], 4)

    def test_a_work_in_the_trash_is_not_offered(self):
        self.assertNotIn("ABW-999", self.values("ABW", "asset"))

    def test_a_work_completes_by_its_release_title_too(self):
        self.assertEqual(self.values("潜入", "asset"), ["ABW-123"])

    def test_a_hit_in_the_file_name_ranks_last(self):
        """`old-ABW-clip.mp4` 里有别人的番号。它排在真的叫这个番号的那条之后。"""
        self.assertEqual(self.values("ABW", "asset"), ["ABW-123", "old-ABW-clip"])

    def test_a_file_name_loses_its_extension(self):
        """`.MOV` 是存储事实，不是这部片叫什么。"""
        self.assertEqual(self.values("IMG", "asset"), ["IMG_2757_682"])

    def test_an_empty_query_offers_nothing(self):
        """空输入下该出现的是搜索记录和推荐，不是补全。"""
        self.assertEqual(self.suggest("")["groups"], [])

    def test_groups_come_back_in_the_declared_order(self):
        order = [kind for kind, _ in SUGGEST_GROUPS]
        kinds = [group["kind"] for group in self.suggest("涼")["groups"]]
        self.assertEqual(kinds, sorted(kinds, key=order.index))

    def test_each_group_carries_its_own_label(self):
        labels = {group["kind"]: group["label"] for group in self.suggest("涼森")["groups"]}
        self.assertEqual(labels["performer"], "女优")

    def test_a_group_never_exceeds_the_asked_for_size(self):
        """六个标签以「足」开头，一组只给得下五个。"""
        self.assertEqual(len(self.group("足", "tag", limit=5)), 5)
        self.assertEqual(len(self.group("足", "tag", limit=2)), 2)

    def test_a_prefix_hit_outranks_a_hit_in_the_middle(self):
        """「足交」是前缀命中，其余几个是同一个字打头的同辈。"""
        self.assertEqual(self.values("足交", "tag")[0], "足交")

    def test_every_suggestion_finds_something_when_actually_searched(self):
        """补全与搜索同源的唯一验收方式：拿补出来的词去真的搜一次。

        逐条搜是这条测试的成本，也是它的意义——两侧判据漂开时，别的断言都还绿着。
        """
        for query in ("涼森", "凉森", "Remu", "ラグジュ", "ドキュ", "Capsule",
                      "カプセル", "足", "足交", "巨乳", "ABW", "潜入",
                      "MO", "mon", "IMG"):
            for group in self.suggest(query)["groups"]:
                for item in group["items"]:
                    with self.subTest(query=query, kind=group["kind"], value=item["value"]):
                        self.assertGreater(
                            self.total(item["value"]), 0,
                            f"补全给出 {item['value']}，搜它却是空的")

    def test_a_suggested_work_opens_the_row_it_points_at(self):
        for item in self.group("ABW", "asset"):
            self.assertEqual(rm_web.q_item(self.contract, item["id"])["id"], item["id"])


class SuggestCardTests(LedgerFixture):
    """下拉栏画得出每一行：人的脸、事务所与近作，作品的小图与署名，每类的总数。"""

    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name)
        covers = root / "covers"
        covers.mkdir()
        (covers / "ABW-123.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        snapshot = root / "two.jpg"
        snapshot.write_bytes(b"\xff\xd8\xff\xd9")
        # ABW-123 有官方封面，two.mp4 只有抽帧，one/three 两样都没有。
        self.con.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
                         " VALUES(4,11,'performer','test',1.0)")
        self.con.execute("UPDATE asset SET snapshot_path=? WHERE id=2", (str(snapshot),))
        self.con.commit()
        self.contract = rm_web.WebContract(self.db, cover_root=covers)

    def test_a_person_row_names_her_agency(self):
        self.assertEqual(self.group("涼森", "performer")[0]["agency"], "Capsule Agency")

    def test_a_person_row_brings_only_works_that_have_a_picture(self):
        works = self.group("涼森", "performer")[0]["works"]
        self.assertEqual({work["id"] for work in works}, {2, 4})
        cover = next(work for work in works if work["id"] == 4)
        self.assertTrue(cover["has_cover"])
        self.assertEqual(cover["code"], "ABW-123")

    def test_a_face_that_cannot_be_drawn_is_not_offered(self):
        """没装实体图、代表作也没有抽帧：页面直接画首字母，不先出一张等 404 的图。"""
        item = self.group("神雪", "performer")[0]
        self.assertFalse(item["has_image"])
        self.assertIsNone(item["rep"])

    def test_a_suggested_work_carries_its_credit_and_picture(self):
        item = self.group("ABW", "asset")[0]
        self.assertEqual(item["who"], "涼森れむ")
        self.assertTrue(item["card"]["has_cover"])

    def test_a_suggested_work_without_a_picture_has_no_card(self):
        self.assertIsNone(self.group("IMG", "asset")[0]["card"])

    def test_each_group_says_how_many_it_matched_in_all(self):
        """六个标签以「足」开头，一组只列五个，总数仍是六。"""
        tags = next(group for group in self.suggest("足")["groups"] if group["kind"] == "tag")
        self.assertEqual((len(tags["items"]), tags["total"]), (5, 6))

    def test_the_work_total_counts_every_hit(self):
        works = next(group for group in self.suggest("ABW", limit=1)["groups"]
                     if group["kind"] == "asset")
        self.assertEqual((len(works["items"]), works["total"]), (1, 2))

    def test_asking_for_one_kind_returns_only_that_kind(self):
        groups = rm_web.q_suggest(self.contract, "涼", 20, ("performer",))["groups"]
        self.assertEqual([group["kind"] for group in groups], ["performer"])

    def test_an_unknown_kind_is_dropped(self):
        self.assertEqual(suggest_kinds("asset,bogus,performer"), ("performer", "asset"))

    def test_an_agency_row_carries_its_site_mark_slot(self):
        """事务所没有标识文件，门面是官网圆标；这份夹具里没有官网，那一格就是空的。"""
        self.assertIsNone(self.group("Capsule", "agency")[0]["mark"])


class AgencyGroupTests(LedgerFixture):
    """事务所这一组的排序与计数。

    事务所自己不挂作品，名下作品数是跨成员的一次去重计数；排序先看整串前缀命中，
    再看作品数，最后按名字。单个字母在真实账本上能命中十几家事务所，这一组的
    先后和总数由这里钉住。
    """

    def setUp(self):
        super().setUp()
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'t','t')",
            [(40, "agency", "Alpha Talent", "alpha talent"), (41, "performer", "星空", "星空"),
             (42, "agency", "Blue Agent", "blue agent"), (43, "performer", "月野", "月野"),
             # 唯一的成员只在回收站那条上出现过：名下没有可见作品。
             (44, "agency", "Amber Empty", "amber empty"), (45, "performer", "雨宮", "雨宮")],
        )
        self.link(4, 41)
        self.link(2, 43)
        self.link(5, 45)
        self.con.executemany(
            "INSERT INTO entity_membership(member_id,agency_id,source,checked_at)"
            " VALUES(?,?,'test',?)",
            [(41, 40, STAMP), (43, 42, STAMP), (45, 44, STAMP)],
        )
        self.con.commit()

    def link(self, asset_id, entity_id, source="test"):
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,?,'performer',?,1.0)", (asset_id, entity_id, source))
        self.con.commit()

    def capsule_works(self):
        return self.group("Capsule", "agency")[0]["n"]

    def test_agencies_rank_by_prefix_hit_then_works_then_name(self):
        """单个字母比词首：`Alpha Talent` 整串以它开头，其余三家是某个词的词首。

        前缀命中压过作品更多的 `Capsule Agency`；同为词首命中的按作品数，作品数
        相同的按名字。
        """
        self.assertEqual(
            [(item["value"], item["n"]) for item in self.group("a", "agency")],
            [("Alpha Talent", 1), ("Capsule Agency", 3), ("Blue Agent", 1),
             ("SO MODEL AGENT", 1)])

    def test_the_total_counts_every_agency_with_works(self):
        """五家命中「a」。`Amber Empty` 名下没有可见作品，不进列表，也不进总数。"""
        agencies = next(group for group in self.suggest("a", limit=1)["groups"]
                        if group["kind"] == "agency")
        self.assertEqual((len(agencies["items"]), agencies["total"]), (1, 4))

    def test_the_same_work_registered_by_another_source_counts_once(self):
        self.link(1, 11, source="scrape")
        self.assertEqual(self.capsule_works(), 3)

    def test_a_member_work_in_the_trash_is_not_counted(self):
        self.link(5, 12)
        self.assertEqual(self.capsule_works(), 3)

    def test_a_member_picture_is_not_counted(self):
        self.con.execute(
            "INSERT INTO asset(id,location,path,name,medium,size,first_seen)"
            " VALUES(8,'local',?,'still.jpg','image',100,'2026-01-01')",
            (r"R:\Media\still.jpg",))
        self.link(8, 12)
        self.assertEqual(self.capsule_works(), 3)


class ShortLatinQueryTests(LedgerFixture):
    """两个字母的拉丁输入比词首，不比子串。

    子串比出来的多半是别的词中间那两个字母：真实账本上「MO」会捞出 `kemonokai`、
    「Pr」会捞出 `chf3_prob4`，而用户在打的是 MOODYZ 和 Prestige。汉字和假名没有
    分词空格，那一侧仍按子串，否则「凉森」这种两字输入连自己都补不出来。
    """

    def test_a_two_letter_query_completes_a_name_that_starts_with_it(self):
        self.assertEqual(self.values("MO", "studio"), ["MOODYZ"])

    def test_a_two_letter_query_reaches_the_head_of_an_inner_word(self):
        self.assertEqual(self.values("MO", "agency"), ["SO MODEL AGENT"])

    def test_a_two_letter_query_skips_the_middle_of_a_word(self):
        self.assertEqual(self.values("MO", "creator"), [])

    def test_a_third_letter_brings_the_substring_match_back(self):
        self.assertEqual(self.values("mon", "creator"), ["kemonokai"])

    def test_an_extension_in_a_file_name_is_not_a_word_head(self):
        """`IMG_2757_682.MOV` 里的 `.MOV` 不该让这条片出现在「MO」的补全里。"""
        self.assertEqual(self.values("MO", "asset"), [])

    def test_two_han_characters_still_match_inside_a_name(self):
        self.assertEqual(self.values("乳系", "tag"), ["巨乳系"])

    def test_two_kana_still_match_inside_a_name(self):
        self.assertEqual(self.values("ュメ", "series"), ["ドキュメント"])


class ShortQueryReachesEveryKindTests(LedgerFixture):
    """搜索的两条分支必须覆盖同样的 kind。

    三字以上走 FTS，它索引的 `entities` 聚合作品名下的全部实体、不挑 kind；两字以内
    走 LIKE。两边认的 kind 不一样时，同一个标签写三个字搜得到、写两个字搜不到，
    用户看到的是搜索时灵时不灵，而补全会照样把那个词补出来。
    """

    def test_a_two_character_tag_query_finds_its_works(self):
        self.assertEqual(self.total("足交"), 2)

    def test_a_three_character_tag_query_finds_its_works(self):
        self.assertEqual(self.total("巨乳系"), 2)

    def test_both_branches_agree_on_the_same_tag(self):
        """`巨乳系` 三个字走 FTS，`巨乳` 两个字走 LIKE，命中的是同一批作品。"""
        self.assertEqual(self.total("巨乳"), self.total("巨乳系"))

    def test_a_two_character_series_query_finds_its_works(self):
        self.assertEqual(self.total("ドキ"), 1)

    def test_a_two_character_performer_query_still_works(self):
        self.assertEqual(self.total("凉森"), 3)


class OneWorkOneSlotTests(LedgerFixture):
    """同一部作品在下拉栏里只占一格。

    真实账本上一部片常有几条来源挂着同一位女优（`r18` 与 `javinizer` 各记一条），同一个
    番号也常存着几个文件。逐行取的话，一个人的四张近作里会有两张是同一张封面，视频组
    也会并排摆出三张一样的图。
    """

    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name)
        covers = root / "covers"
        covers.mkdir()
        for code in ("ABW-123", "ABW-124"):
            (covers / f"{code}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        frame = root / "ssis-001-b.jpg"
        frame.write_bytes(b"\xff\xd8\xff\xd9")
        self.con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,first_seen,code,"
            "catalog_title,snapshot_path) VALUES(?,'local',?,?,'video',100,'2026-01-01',?,?,?)",
            [# ABW-123 的另外两个文件，一个写法规范，一个是下划线小写。
             (8, r"R:\Media\ABW-123-B.mp4", "ABW-123-B.mp4", "ABW-123", "潜入取材の一日", None),
             (9, r"R:\Media\abw_123.mp4", "abw_123.mp4", "abw_123", None, None),
             (10, r"R:\Media\ABW-124.mp4", "ABW-124.mp4", "ABW-124", "二日目", None),
             # SSIS-001 没有封面，两个文件里只有后入库的那个抽过帧。
             (11, r"R:\Media\SSIS-001.mp4", "SSIS-001.mp4", "SSIS-001", None, None),
             (12, r"R:\Media\SSIS-001-B.mp4", "SSIS-001-B.mp4", "SSIS-001", None, str(frame))],
        )
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,11,'performer',?,1.0)",
            [(4, "test"), (8, "test"), (9, "test"), (10, "test"), (10, "r18"),
             (11, "test"), (12, "test")],
        )
        self.con.commit()
        self.contract = rm_web.WebContract(self.db, cover_root=covers)

    def work_keys(self):
        works = self.group("涼森", "performer")[0]["works"]
        return [normalise_code_key(work["code"]) for work in works]

    def test_a_work_credited_by_two_sources_takes_one_slot(self):
        self.assertEqual(self.work_keys().count("ABW-124"), 1)

    def test_every_file_of_one_code_takes_one_slot(self):
        """ABW-123 存着三个文件，其中一个番号写成 `abw_123`，画出来都是同一张封面。"""
        self.assertEqual(self.work_keys().count("ABW-123"), 1)

    def test_a_work_shows_up_through_the_file_that_has_a_frame(self):
        """SSIS-001 没有封面，出面的必须是抽过帧的那个文件，否则整部作品掉出近作。"""
        works = self.group("涼森", "performer")[0]["works"]
        self.assertEqual([work["id"] for work in works], [12, 10, 4])
        self.assertTrue(works[0]["has_thumb"])

    def test_the_video_group_lists_each_work_once(self):
        self.assertEqual(self.values("ABW", "asset"), ["ABW-124", "ABW-123", "old-ABW-clip"])

    def test_the_video_group_opens_the_file_written_the_canonical_way(self):
        item = next(item for item in self.group("ABW", "asset") if item["value"] == "ABW-123")
        self.assertEqual(item["id"], 4)

    def test_the_video_total_counts_works_not_files(self):
        works = next(group for group in self.suggest("ABW", limit=1)["groups"]
                     if group["kind"] == "asset")
        self.assertEqual((len(works["items"]), works["total"]), (1, 3))


if __name__ == "__main__":
    unittest.main()
