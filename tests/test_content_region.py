"""内容产地：韩国与国产的番号不能再算进 JAV。

这一组用例盯的是同一个口径在三处成立：`regions.infer_region` 的推断本身、
`is_jav_asset` 的短路、以及 SQL 那份 `EFFECTIVE_REGION_SQL`。分开写是因为出事的
正是它们分家的那一刻——`is_korean_mib_code` 早就认得出 MIB，可分类口径从来没读过它。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach import web_batch
from peach import web_contract as rm_web
from peach.catalog_rules import is_jav_asset
from peach.field_owners import USER_MANUAL, owner_of
from peach.regions import (
    REGIONS,
    infer_region,
    is_region,
    normalize_region,
    region_label,
)
from support.ledger import fresh_ledger


class RegionValueTests(unittest.TestCase):
    def test_every_region_has_a_label(self):
        self.assertEqual(sorted(REGIONS), sorted(r for r in REGIONS if region_label(r)))

    def test_unknown_is_not_other(self):
        """「还没判过」和「判过，不属于那四类」必须分得开，否则待办数永远算不出。"""
        self.assertTrue(is_region(""))
        self.assertEqual(normalize_region(""), "")
        self.assertEqual(normalize_region("other"), "other")
        self.assertNotEqual(normalize_region("other"), normalize_region(""))

    def test_an_unrecognised_value_reads_as_undecided(self):
        """认不出的写法退回未判定而不是抛错：它会从查询参数和候选两个方向进来。"""
        self.assertEqual(normalize_region("jpn"), "")
        self.assertEqual(normalize_region(None), "")
        self.assertFalse(is_region("jpn"))

    def test_case_and_padding_do_not_make_a_new_region(self):
        self.assertEqual(normalize_region(" JP "), "jp")


class RegionInferenceTests(unittest.TestCase):
    def test_korean_mib_codes_are_korean(self):
        for code in ("CA-103", "DB-205", "CHU-201", "ERI-102"):
            with self.subTest(code=code):
                self.assertEqual(infer_region(code), "kr")

    def test_mainland_studio_codes_are_domestic(self):
        for code in ("MDX-0123", "MD-0140", "TM-0087", "JD-0031", "SWAG-1020"):
            with self.subTest(code=code):
                self.assertEqual(infer_region(code), "cn")

    def test_japanese_codes_stay_undecided(self):
        """日本不靠推断落下来。把「剩下的都算日本」写成规则，等于让每个没收录的
        国产厂牌自动变回 JAV——那正是这次要修的毛病。"""
        for code in ("MEYD-911", "ABP-762", "SSIS-001", "FC2-PPV-1234567"):
            with self.subTest(code=code):
                self.assertEqual(infer_region(code), "")

    def test_a_real_jav_studio_sharing_a_two_letter_shape_is_not_korean(self):
        """`BF-366` 在 `B:\\番号\\BeFree\\` 下，BeFree 是真实 JAV 厂牌。"""
        self.assertEqual(infer_region("BF-366"), "")

    def test_western_release_shapes_are_western(self):
        self.assertEqual(infer_region(None, "Vixen.26.05.07.Ella.Rose.mp4"), "west")
        self.assertEqual(infer_region("", "DorcelClub.2024.12.02.Christy.White.mp4"), "west")

    def test_amateur_numeric_prefixes_keep_their_release_system(self):
        """`300MIUM-1239` 的字母段撞得上名单，但它的发行体系本来就是日本素人系。"""
        self.assertEqual(infer_region("300MIUM-1239"), "")


class JavPredicateTests(unittest.TestCase):
    def test_a_settled_non_japanese_region_is_never_jav(self):
        for region in ("kr", "cn", "west", "other"):
            with self.subTest(region=region):
                self.assertFalse(is_jav_asset(
                    "CA-103", studio="MIB", release_date="2022-10-28", region=region))

    def test_an_undecided_region_keeps_the_old_rule(self):
        """这一列绝大多数行还是空的，拿「没判过」当「不是日本」会清空整个 JAV 页。"""
        self.assertTrue(is_jav_asset("MEYD-911", studio="MOODYZ", region=""))
        self.assertTrue(is_jav_asset("MEYD-911", studio="MOODYZ"))

    def test_japan_still_needs_release_evidence(self):
        """产地是日本也救不了没有发行证据的 creator clip。"""
        self.assertFalse(is_jav_asset("JI-103", region="jp"))
        self.assertTrue(is_jav_asset("JI-103", studio="Some Studio", region="jp"))

    def test_fc2_stays_jav_under_japan(self):
        self.assertTrue(is_jav_asset("FC2-PPV-1234567", region="jp"))


class RegionLedgerTests(unittest.TestCase):
    """SQL 那一份口径。产地不写进账本就得每次查询时算，那就只能有一处算法。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(fresh_ledger(self.tmp.name))
        con = sqlite3.connect(self.db_path)
        con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,studio,code,"
            "release_date,region,first_seen) VALUES(?,?,?,?,'video',10,?,?,?,?,'2026-09-13')",
            [
                # 韩国 MIB：官网刮回来的厂牌和发行日让它满足了 JAV 的全部旧判据。
                (1, "115", r"B:\MVP\MIB\CA-103.mp4", "CA-103 Chaeah.mp4",
                 "MIB", "CA-103", "2022-10-28", None),
                (2, "115", r"B:\番号\MEYD-911.mp4", "MEYD-911.mp4",
                 "MOODYZ", "MEYD-911", "2024-01-01", None),
                (3, "115", r"B:\国产\MDX-0123.mp4", "MDX-0123.mp4",
                 "麻豆传媒", "MDX-0123", "2023-05-05", None),
                # 番号推不出产地，靠创作者实体投影。
                (4, "115", r"B:\创作者\sunwall\clip.mp4", "clip.mp4", None, None, None, None),
                # 用户判定压过番号推断：这条番号形如韩国 MIB，但用户说是日本。
                (5, "115", r"B:\番号\AR-101.mp4", "AR-101.mp4",
                 "Some Studio", "AR-101", "2021-01-01", "jp"),
            ],
        )
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,region,"
            "created_at,updated_at) VALUES(50,'creator','sunwall','sunwall','kr','t','t')")
        con.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source) "
                    "VALUES(4,50,'creator','test')")
        con.commit()
        con.close()
        self.contract = rm_web.WebContract(Path(self.db_path))

    def _regions(self):
        rows = rm_web.q_items(self.contract, {"count": "0", "limit": "50"})["items"]
        return {row["id"]: row["region"] for row in rows}

    def _jav_ids(self):
        rows = rm_web.q_items(self.contract, {"jav": "1", "count": "0", "limit": "50"})["items"]
        return sorted(row["id"] for row in rows)

    def test_korean_and_domestic_codes_leave_the_jav_page(self):
        self.assertEqual(self._jav_ids(), [2, 5])

    def test_each_layer_wins_over_the_one_below_it(self):
        self.assertEqual(self._regions(), {
            1: "kr",    # 番号推断
            2: "",      # 日本不靠推断，等人判
            3: "cn",    # 番号推断
            4: "kr",    # 创作者实体投影
            5: "jp",    # 用户判定压过推断
        })

    def test_the_settled_flag_separates_a_decision_from_a_guess(self):
        rows = {row["id"]: row for row in
                rm_web.q_items(self.contract, {"count": "0", "limit": "50"})["items"]}
        self.assertTrue(rows[5]["region_settled"])
        self.assertFalse(rows[1]["region_settled"])

    def test_the_detail_page_agrees_with_the_list(self):
        """详情页那条查询没给 asset 起别名，产地表达式必须跟着换写法。"""
        detail = rm_web.q_item(self.contract, 1)
        self.assertEqual(detail["region"], "kr")
        self.assertFalse(detail["is_jav"])

    def test_filtering_by_region(self):
        rows = rm_web.q_items(self.contract, {"region": "kr", "count": "0", "limit": "50"})
        self.assertEqual(sorted(row["id"] for row in rows["items"]), [1, 4])

    def test_filtering_several_regions_at_once(self):
        rows = rm_web.q_items(self.contract, {"region": "kr,cn", "count": "0", "limit": "50"})
        self.assertEqual(sorted(row["id"] for row in rows["items"]), [1, 3, 4])

    def test_filtering_the_undecided_pile(self):
        """判产地是人一条条过的活，看不到还剩多少就没人会去过。"""
        rows = rm_web.q_items(self.contract, {"region": "none", "count": "0", "limit": "50"})
        self.assertEqual(sorted(row["id"] for row in rows["items"]), [2])

    def test_the_facet_counts_each_bucket(self):
        facets = {row["k"]: row for row in rm_web.q_facets(self.contract)["regions"]}
        self.assertEqual({key: row["n"] for key, row in facets.items()},
                         {"kr": 2, "cn": 1, "jp": 1, "none": 1})
        self.assertEqual(facets["none"]["label"], "未判定")
        self.assertEqual(facets["kr"]["label"], "韩国")

    def test_the_facet_follows_the_other_filters(self):
        """筛选项必须和作品列表同源，否则右栏会给出点进去是 0 条的项。"""
        facets = rm_web.q_facets(self.contract, filters={"region": "kr"})["regions"]
        self.assertEqual({row["k"] for row in facets}, {"kr"})


class RegionBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(fresh_ledger(self.tmp.name))
        con = sqlite3.connect(self.db_path)
        con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,code,first_seen) "
            "VALUES(?,?,?,?,'video',10,?,'2026-09-13')",
            [(1, "115", r"B:\a.mp4", "a.mp4", "AAA-001"),
             (2, "115", r"B:\b.mp4", "b.mp4", "BBB-002")],
        )
        con.commit()
        con.close()
        self.contract = rm_web.WebContract(Path(self.db_path))

    def _stored(self):
        con = sqlite3.connect(self.db_path)
        try:
            return {row[0]: (row[1], owner_of(row[2], "region")) for row in
                    con.execute("SELECT id,region,field_owners FROM asset ORDER BY id")}
        finally:
            con.close()

    def test_a_batch_decision_is_owned_by_the_user(self):
        web_batch.w_batch(self.contract, {"ids": [1, 2], "operation": "region", "region": "cn"})
        self.assertEqual(self._stored(), {1: ("cn", USER_MANUAL), 2: ("cn", USER_MANUAL)})

    def test_none_withdraws_the_decision(self):
        web_batch.w_batch(self.contract, {"ids": [1], "operation": "region", "region": "kr"})
        web_batch.w_batch(self.contract, {"ids": [1], "operation": "region", "region": "none"})
        self.assertEqual(self._stored()[1], (None, USER_MANUAL))

    def test_a_misspelt_region_is_refused_rather_than_clearing_the_column(self):
        """`normalize_region` 把认不出的写法当未判定，批量这一路不能跟着它——
        一次拼错就成了一次静默的清空。"""
        web_batch.w_batch(self.contract, {"ids": [1], "operation": "region", "region": "kr"})
        with self.assertRaises(ValueError):
            web_batch.w_batch(self.contract, {"ids": [1], "operation": "region", "region": "jpn"})
        self.assertEqual(self._stored()[1], ("kr", USER_MANUAL))


if __name__ == "__main__":
    unittest.main()
