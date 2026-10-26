import unittest

from peach import metadata_routes
from peach.catalog_rules import is_uncensored_code
from peach.metadata_policy import (
    CHAIN_COMMUNITY,
    CHAIN_FALLBACK,
    CHAIN_OFFICIAL,
    CHAIN_PREFERRED,
    CHAIN_UNKNOWN,
    FIELD_SOURCE_ORDER,
    HISTORICAL_SOURCES,
    PEACH_FIELDS,
    POLICY_VERSION,
    SOURCE_SPECS,
    chain_rank,
    field_rank,
    sort_candidates,
    source_tier,
)


class MetadataPolicyTests(unittest.TestCase):
    def test_censored_tags_prefer_javdb_while_nfo_and_other_fields_keep_their_order(self):
        candidates = [{"source": "dmm"}, {"source": "javdb"}, {"source": "mgstage"}]
        self.assertEqual(sort_candidates("tags", candidates, code="ABW-220")[0]["source"], "javdb")
        self.assertEqual(sort_candidates("tags", [*candidates, {"source": "local_nfo"}],
                                         code="ABW-220")[0]["source"], "local_nfo")
        self.assertLess(chain_rank("tags", "local_nfo", code="ABW-220"),
                        chain_rank("tags", "javdb", code="ABW-220"))
        self.assertLess(chain_rank("tags", "javdb", code="ABW-220"),
                        chain_rank("tags", "dmm", code="ABW-220"))
        for field in ("studio", "performers", "title", "release_date"):
            self.assertEqual(sort_candidates(field, candidates, code="ABW-220")[0]["source"], "dmm")
        for code in ("FC2-PPV-1234567", "259LUXU-1475", "040221-001", "AR-101"):
            self.assertEqual(sort_candidates("tags", candidates, code=code)[0]["source"], "mgstage")
        self.assertFalse(SOURCE_SPECS["javdb"].official)

    def test_every_source_the_chain_can_ask_is_registered_once(self):
        """链上每一档的成员都要在 `SOURCE_SPECS` 里有级别，否则候选算不出 official。"""
        chain_sources = {source for chain in metadata_routes.ROUTES.values() for source in chain}
        chain_sources |= set(metadata_routes.AMANE_STAGE) | set(metadata_routes.AMANE_OFFICIAL_STAGE)
        chain_sources |= {"sougouwiki", "av_neme", "av_name"}
        self.assertLessEqual(chain_sources, set(SOURCE_SPECS))
        self.assertTrue(POLICY_VERSION.startswith("metadata-source-policy-"))

    def test_historical_sources_keep_their_tier_but_are_not_on_any_chain(self):
        """账本里 `javinizer:libredmm:studio` 这类 provenance 还在，级别要认得；请求不再发。"""
        chain_sources = {source for chain in metadata_routes.ROUTES.values() for source in chain}
        chain_sources |= set(metadata_routes.AMANE_STAGE) | set(metadata_routes.AMANE_OFFICIAL_STAGE)
        for source in HISTORICAL_SOURCES:
            self.assertIn(source, SOURCE_SPECS, source)
            self.assertNotIn(source, chain_sources, source)
        self.assertFalse(SOURCE_SPECS["javlibrary"].official)

    def test_the_amane_official_sites_rank_as_official_ahead_of_the_mirror(self):
        """经桥的片商官网与 MGStage 是官方来源；片商站在每个字段上排在 dmm 与 r18.dev 之前。"""
        for source in metadata_routes.AMANE_OFFICIAL_STAGE:
            with self.subTest(source=source):
                self.assertEqual(source_tier(source), CHAIN_OFFICIAL)
        for field in ("title", "performers", "studio", "series"):
            with self.subTest(field=field):
                self.assertLess(field_rank(field, "makers"), field_rank(field, "dmm"))
                self.assertLess(field_rank(field, "prestige"), field_rank(field, "r18dev"))
        self.assertLess(field_rank("release_date", "faleno"), field_rank("release_date", "r18dev"))
        self.assertNotIn("prestige", FIELD_SOURCE_ORDER["release_date"])

    def test_field_order_covers_every_peach_field(self):
        self.assertEqual(set(FIELD_SOURCE_ORDER), set(PEACH_FIELDS))
        for field, order in FIELD_SOURCE_ORDER.items():
            self.assertEqual(len(order), len(set(order)), field)
            for source in order:
                self.assertIn(source, SOURCE_SPECS, f"{field} 排了未登记的 {source}")

    def test_field_rank_counts_from_one_and_puts_unknown_sources_last(self):
        self.assertEqual(field_rank("tags", "mgstage"), 1)
        self.assertEqual(field_rank("tags", "r18dev"), 12)
        self.assertEqual(field_rank("tags", "imaginary"), len(FIELD_SOURCE_ORDER["tags"]) + 1)

    def test_every_field_uses_policy_order_and_explicit_official_metadata(self):
        candidates = [
            {"source": "javbus", "confidence": 0.99},
            {"source": "r18dev", "confidence": 0.8},
            {"source": "dmm", "confidence": 0.7},
            {"source": "makers", "confidence": 0.6},
        ]
        for field in FIELD_SOURCE_ORDER:
            ordered = sort_candidates(field, candidates)
            self.assertEqual(ordered[0]["source"], "makers", field)
            self.assertEqual(
                [row["field_rank"] for row in ordered],
                sorted(row["field_rank"] for row in ordered),
            )
        with self.assertRaisesRegex(ValueError, "未知 Peach 元数据字段"):
            sort_candidates("runtime", candidates)

    def test_tags_prefer_mgstage_over_the_dmm_dvd_page(self):
        # ABW-220 实测：mgstage 给 8 项内容标签，dmm/libredmm/r18dev 都只给
        # 「AV女優・単体作品・サンプル動画」。厂牌与日期仍以 dmm 为准。
        candidates = [{"source": "dmm"}, {"source": "mgstage"}, {"source": "r18dev"}]
        self.assertEqual(sort_candidates("tags", candidates)[0]["source"], "mgstage")
        self.assertEqual(sort_candidates("studio", candidates)[0]["source"], "dmm")
        self.assertEqual(sort_candidates("release_date", candidates)[0]["source"], "dmm")

    def test_uncensored_codes_are_told_apart_by_shape(self):
        """番号形状就能确定发行面，不必先有元数据证明。"""
        self.assertTrue(is_uncensored_code("040221-001"))
        self.assertTrue(is_uncensored_code("HEYZO-1380"))
        self.assertFalse(is_uncensored_code("ABW-220"))
        self.assertFalse(is_uncensored_code("259LUXU-1475"))

    def test_javbus_stays_community_so_its_values_need_review(self):
        # javbus 取到的值只能进人工复核，不能走免复核写入；它还是兜底那一层。
        self.assertFalse(SOURCE_SPECS["javbus"].official)
        self.assertEqual(source_tier("javbus"), CHAIN_FALLBACK)
        self.assertEqual(source_tier("javdb"), CHAIN_PREFERRED)
        self.assertEqual(source_tier("avbase"), CHAIN_COMMUNITY)
        self.assertEqual(source_tier("caribbeancom"), CHAIN_OFFICIAL)
        self.assertEqual(source_tier("imaginary"), CHAIN_UNKNOWN)

    def test_publisher_outranks_the_overseas_reseller(self):
        """aventertainment 是转售商，不是发行方。

        实测 `071213-625`：它答 2017-12-28（自己的上架日），而番号本身就是
        发行日 2013-07-12，javbus 与番号一致；`092415-001` 差了 9 个月。
        标签同理——`040221-001` 它给的是英文页的改写版，caribbeancom 给的是
        发行方原页。
        """
        candidates = [{"source": "aventertainment"}, {"source": "caribbeancom"}]
        for field in ("tags", "release_date"):
            self.assertEqual(sort_candidates(field, candidates)[0]["source"], "caribbeancom", field)
            self.assertLess(chain_rank(field, "caribbeancom"), chain_rank(field, "aventertainment"))

    def test_the_fc2_mirror_names_the_cast_ahead_of_javdb(self):
        """FC2 的演员栏以 fc2cmadb 为准：`FC2-PPV-2629971` javdb 写 `安娜`，它写原名 `あんな`。

        提前只限演员栏，标题上 javdb 仍排在它前面。
        """
        self.assertLess(chain_rank("performers", "fc2cmadb"), chain_rank("performers", "javdb"))
        self.assertLess(chain_rank("title", "javdb"), chain_rank("title", "fc2cmadb"))


if __name__ == "__main__":
    unittest.main()
