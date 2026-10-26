"""人物资料页的外部入口：地址怎么拼、摆在哪个位置、什么时候不出。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from peach import entry_links


def ref(provider: str, external_id: str, kind: str = "performer") -> dict:
    return {"provider": provider, "external_kind": kind, "external_id": external_id}


def alias(written: str, source: str = "r18:performer") -> dict:
    return {"alias": written, "source": source}


class EntryLinkAddressTests(unittest.TestCase):
    def test_each_site_lands_in_the_slot_it_belongs_to(self):
        rows = entry_links.build(entry_links.defaults(), "河合あすな",
                                 [ref("javdb", "QDvG"), ref("minnano-av", "155084")])
        self.assertEqual([row["site"] for row in rows],
                         ["minnano-av", "javdb", "missav"])
        # minnano-av 是一份资料页，排进上面那排链接；看片那两枚自己一行。
        self.assertEqual([row["slot"] for row in rows], ["pill", "mark", "mark"])
        self.assertEqual(rows[0]["url"], "https://www.minnano-av.com/actress155084.html")
        self.assertEqual(rows[1]["url"], "https://javdb.com/actors/QDvG")
        # 药丸上写站点自己的名字，取自 minnano-av 官网的 title。
        self.assertEqual([row["label"] for row in rows],
                         ["みんなのAV", "JavDB", "MISSAV"])
        # MISSAV 没有可用的图形标识，那一枚由前端按它站上的排版规则排字。
        self.assertEqual([row["mark"] for row in rows],
                         ["brand-minnano", "mark-javdb", ""])

    def test_two_pages_on_the_same_site_each_get_an_entry(self):
        """javdb 上同一位女优有两个演员页，两边挂的作品不同，都要能点进去。"""
        rows = entry_links.build(entry_links.defaults(), "释爱丽丝",
                                 [ref("javdb", "d45k9"), ref("javdb", "ZX5z7")],
                                 [alias("釈アリス", "avdb-actor-mapping@8e2d")])
        pages = [row for row in rows if row["site"] == "javdb"]
        self.assertEqual([row["label"] for row in pages], ["JavDB", "JavDB ②"])
        self.assertEqual([row["ordinal"] for row in pages], ["", "②"])
        self.assertEqual([row["url"] for row in pages],
                         ["https://javdb.com/actors/d45k9",
                          "https://javdb.com/actors/ZX5z7"])

    def test_a_site_without_an_id_is_absent_instead_of_a_search_address(self):
        rows = entry_links.build(entry_links.defaults(), "深田えいみ",
                                 [ref("r18dev", "1041521")])
        self.assertEqual([row["site"] for row in rows], ["missav"])
        self.assertNotIn("search", rows[0]["url"])

    def test_a_performer_outside_the_jav_directories_gets_no_watch_row(self):
        """`145cm色白お嬢様` 这类 FC2 个人摄创作者，账本 kind 也是 performer。

        JavDB 与 MISSAV 只收商业 AV 女优；本机 Stash 的 id 与 r18 的名字映射每个人都有，
        它们不是「别处收录过她」的证据，所以不能拿来开这一行。
        """
        rows = entry_links.build(entry_links.defaults(), "145cm色白お嬢様",
                                 [ref("stash", "912"),
                                  ref("r18", "145cm", kind="performer_name")])
        self.assertEqual(rows, [])

    def test_an_agency_roster_id_does_not_open_the_watch_row(self):
        """K-MIB 那类事务所站收的是所属艺人，不是 JAV 的作品目录。

        名册上有她，说明这家公司签了她；她拍的是不是商业 AV、JavDB 与 MISSAV 上有没有
        她的页面，这条引用一个字都没说。
        """
        rows = entry_links.build(entry_links.defaults(), "神木ほのか",
                                 [ref("kmib", "218")])
        self.assertEqual(rows, [])

    def test_missav_uses_the_japanese_stage_name(self):
        """规范名是中文译名时，MISSAV 那条按别名里的日文艺名拼。

        中文译名与纯平假名读音不作为混合文字艺名的入口。
        """
        rows = entry_links.build(entry_links.defaults(), "释爱丽丝",
                                 [ref("r18dev", "1096544")], [
            alias("Alice Shaku", "avdb-actor-mapping@8e2d"),
            alias("しゃくありす", "r18:performer"),
            alias("釈アリス", "avdb-actor-mapping@8e2d"),
        ])
        self.assertEqual(
            rows[0]["url"],
            "https://missav.ws/dm3/cn/actresses/%E9%87%88%E3%82%A2%E3%83%AA%E3%82%B9")

    def test_current_japanese_name_wins_over_a_former_name_alias(self):
        rows = entry_links.build(entry_links.defaults(), "逢沢みゆ",
                                 [ref("javdb", "zK98J")], [
            alias("あいざわみゆ", "r18dev:performer-profile"),
            alias("坂本マリア", "auto:performer-alias@2048"),
        ])
        self.assertEqual(next(row["url"] for row in rows if row["site"] == "missav"),
                         "https://missav.ws/dm3/cn/actresses/"
                         "%E9%80%A2%E6%B3%BD%E3%81%BF%E3%82%86")

    def test_plain_hiragana_canonical_reading_uses_a_stage_name_alias(self):
        self.assertEqual(entry_links.japanese_name("あいざわみゆ", [alias("逢沢みゆ")]),
                         "逢沢みゆ")

    def test_glyph_equivalent_alias_preserves_the_japanese_spelling(self):
        self.assertEqual(entry_links.japanese_name("蓝川みれい", [
            alias("藍川みれい", "avdb-actor-mapping"), alias("坂本マリア"),
        ]), "藍川みれい")

    def test_plain_hiragana_alias_is_not_a_han_stage_name(self):
        for name, reading in (("三宅美香", "みやけみか"), ("音海里奈", "おとみりな")):
            with self.subTest(name=name):
                self.assertEqual(entry_links.japanese_name(name, [alias(reading)]), name)

    def test_a_published_hiragana_stage_name_is_distinct_from_a_profile_reading(self):
        aliases = [alias("なぎさ", "javdb-actor-page")]
        self.assertEqual(entry_links.japanese_name("渚向日葵", aliases), "なぎさ")
        self.assertEqual(entry_links.japanese_name("渚向日葵", aliases,
                         {"source": "javdb-actor-page", "jp": "なぎさ", "zh_cn": "渚向日葵"}),
                         "なぎさ")

    def test_glyph_equivalent_chinese_alias_does_not_hide_a_published_kana_stage_name(self):
        for name, jp, chinese in (("逢泽美优", "逢沢みゆ", "逢澤美優"),
                                  ("优梨舞奈", "優梨まいな", "優梨舞奈"),
                                  ("新井爱丽", "新井エリー", "新井愛麗")):
            with self.subTest(name=name):
                aliases = [alias(chinese, "review:performer-localization"),
                           alias(jp, "review:performer-localization")]
                self.assertEqual(entry_links.japanese_name(name, aliases,
                                 {"source": "review:performer-localization", "jp": jp, "zh_cn": name}), jp)

    def test_equivalent_han_name_preserves_original_glyphs_and_iteration_mark(self):
        for name, jp in (("北条麻妃", "北條麻妃"), ("乃乃果花", "乃々果花"),
                         ("梨梨衣", "梨々衣")):
            with self.subTest(name=name):
                self.assertEqual(entry_links.japanese_name(name, [alias(jp)]), jp)

    def test_missav_preserves_source_stage_name_glyphs(self):
        from urllib.parse import unquote, urlsplit

        for name, jp in (("七泽美亚", "七沢みあ"), ("逢泽美优", "逢沢みゆ"),
                         ("北条麻妃", "北條麻妃"), ("乃乃果花", "乃々果花"),
                         ("优梨舞奈", "優梨まいな")):
            with self.subTest(name=name):
                directory = entry_links.directory_index({"source": entry_links.DIRECTORY_URL,
                    "entries": [{"name": name, "url": "https://missav.ws/dm3/cn/actresses/" + quote(jp)}]})
                rows = entry_links.build(entry_links.defaults(), name,
                                         [ref("javdb", "sample")], [alias(jp)], directory=directory)
                address = next(row["url"] for row in rows if row["site"] == "missav")
                self.assertEqual(unquote(urlsplit(address).path), f"/dm3/cn/actresses/{jp}")

    def test_paired_current_han_name_wins_over_an_unreviewed_former_name(self):
        localized = {"source": "avdb-actor-mapping", "jp": "水谷心音", "zh_cn": "水谷心音"}
        rows = entry_links.build(entry_links.defaults(), "水谷心音", [ref("javdb", "sample")],
                                 [alias("藤崎りお"), alias("みずたにここね")], localized)
        self.assertEqual(next(row["url"] for row in rows if row["site"] == "missav"),
                         "https://missav.ws/dm3/cn/actresses/"
                         "%E6%B0%B4%E8%B0%B7%E5%BF%83%E9%9F%B3")

    def test_localization_conflicts_do_not_replace_the_current_identity(self):
        self.assertEqual(entry_links.japanese_name("霞理沙", [alias("かすみりさ")],
                         {"source": "avdb-actor-mapping", "jp": "松冈仁美", "zh_cn": "霞理沙"}),
                         "霞理沙")
        self.assertEqual(entry_links.japanese_name("泽村玲子", [
            alias("澤村レイコ", "avdb-actor-mapping")],
            {"source": "avdb-actor-mapping", "jp": "泽村玲子", "zh_cn": "泽村玲子"}),
                         "澤村レイコ")
        self.assertEqual(entry_links.japanese_name("鹫尾芽衣", [
            alias("筧ジュン", "avdb-actor-mapping"), alias("鷲尾めい", "avdb-actor-mapping")],
            {"source": "avdb-actor-mapping", "jp": "鷲尾めい", "zh_cn": "鹫尾芽衣"}),
                         "筧ジュン")

    def test_paired_han_name_requires_the_same_current_chinese_name(self):
        self.assertEqual(entry_links.japanese_name("宇野刊菜", [alias("あべかんな")],
                         {"source": "avdb-actor-mapping", "jp": "宇野栞菜", "zh_cn": "宇野刊菜"}),
                         "宇野栞菜")
        self.assertEqual(entry_links.japanese_name("宇野刊菜", [alias("あべかんな")],
                         {"source": "avdb-actor-mapping", "jp": "宇野栞菜", "zh_cn": "别的写法"}),
                         "宇野刊菜")

    def test_a_reading_in_plain_hiragana_loses_to_the_written_stage_name(self):
        rows = entry_links.build(entry_links.defaults(), "天川空",
                                 [ref("r18dev", "1091104")], [
            alias("あまかわそら", "r18:performer"),
            alias("天川そら", "avdb-actor-mapping@8e2d"),
        ])
        self.assertTrue(rows[0]["url"].endswith("/%E5%A4%A9%E5%B7%9D%E3%81%9D%E3%82%89"))

    def test_a_stage_name_source_wins_among_writings_of_the_same_shape(self):
        rows = entry_links.build(entry_links.defaults(), "佐佐木纱希",
                                 [ref("r18dev", "1075533")], [
            alias("佐々木サキ", "stash:performer"),
            alias("佐々木さき", "avdb-actor-mapping@8e2d"),
        ])
        self.assertTrue(rows[0]["url"].endswith(
            "/%E4%BD%90%E3%80%85%E6%9C%A8%E3%81%95%E3%81%8D"))

    def test_the_canonical_name_stands_in_when_no_alias_reads_as_japanese(self):
        rows = entry_links.build(entry_links.defaults(), "楪 カレン",
                                 [ref("r18dev", "1058712")],
                                 [alias("Karen Yuzuriha", "r18:performer")])
        self.assertEqual(
            rows[0]["url"],
            "https://missav.ws/dm3/cn/actresses/%E6%A5%AA%20%E3%82%AB%E3%83%AC%E3%83%B3")

    def test_ids_from_another_entity_kind_do_not_count(self):
        # 事务所那 55 条 minnano-av 引用记的是 `production`，不是这个人在站上的号；
        # 它既拼不出 minnano 的资料页，也不足以说明这个人是 JAV 女优。
        rows = entry_links.build(entry_links.defaults(), "七沢みあ",
                                 [ref("minnano-av", "110", kind="production")])
        self.assertEqual(rows, [])

    def test_a_mirror_domain_replaces_only_the_host(self):
        settings = entry_links.defaults()
        settings["javdb"]["host"] = "javdb521.com"
        rows = entry_links.build(settings, "七沢みあ", [ref("javdb", "NPD3")])
        self.assertEqual(rows[0]["url"], "https://javdb521.com/actors/NPD3")

    def test_the_feed_address_stays_on_javdb_whatever_the_mirror(self):
        """订阅地址按主机计限流预算，跟着镜像走就绕开了 `javdb.com` 那份预算。"""
        self.assertEqual(entry_links.javdb_actor_pages("七沢みあ", [ref("javdb", "NPD3")]),
                         ["https://javdb.com/actors/NPD3"])
        self.assertEqual(entry_links.javdb_actor_pages(
            "145cm色白お嬢様", [ref("stash", "12")]), [])

    def test_a_broken_domain_falls_back_to_the_default_site(self):
        """手改坏的域名拼出来是个不存在的主机名，那比退回默认站更难看出问题。"""
        settings = entry_links.defaults()
        settings["javdb"]["host"] = "javdb.com/actors"
        rows = entry_links.build(settings, "七沢みあ", [ref("javdb", "NPD3")])
        self.assertEqual(rows[0]["url"], "https://javdb.com/actors/NPD3")

    def test_an_empty_domain_means_the_default_one(self):
        """配置页那个框默认就是空的：留空是「没换过」，不是「没有地址」。"""
        rows = entry_links.build({"javdb": {"host": ""}}, "七沢みあ", [ref("javdb", "NPD3")])
        self.assertEqual(rows[0]["url"], "https://javdb.com/actors/NPD3")


class EntryLinkSettingsFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def body(self, **changed: dict) -> dict:
        """配置页提交的形状：能换域名的那几站各一行，值就是框里写的那串。"""
        sites = {site.key: {"host": ""} for site in entry_links.SITES if site.mirrored}
        sites.update(changed)
        return {"sites": sites}

    def test_an_absent_file_reads_as_no_domain_changed(self):
        """配置页只管能换域名的那两站；みんなのAV 只此一家，不在这份设置里。"""
        saved = entry_links.read(self.root)
        self.assertEqual(sorted(saved), ["javdb", "missav"])
        self.assertTrue(all(row["host"] == "" for row in saved.values()))

    def test_saving_round_trips_through_the_snapshot(self):
        entry_links.save(self.root, self.body(javdb={"host": "javdb521.com"}))
        rows = {row["key"]: row for row in entry_links.snapshot(self.root)["sites"]}
        self.assertEqual(sorted(rows), ["javdb", "missav"])
        self.assertEqual(rows["javdb"]["host"], "javdb521.com")
        self.assertEqual(rows["javdb"]["default_host"], "javdb.com")
        self.assertEqual(rows["javdb"]["label"], "JavDB")
        # 没动过的那一站留空：框里显示的是占位符，不是一串要人去认的默认值。
        self.assertEqual(rows["missav"]["host"], "")
        self.assertEqual(rows["missav"]["default_host"], "missav.ws")

    def test_directory_file_supplies_the_published_missav_path(self):
        path = self.root / entry_links.DIRECTORY_FILENAME
        first = "https://missav.ws/dm102/cn/actresses/" + quote("七沢みあ")
        path.write_text(json.dumps({"source": entry_links.DIRECTORY_URL,
                                  "entries": [{"name": "七泽美亚", "url": first}]}), encoding="utf-8")
        rows = entry_links.entry_links(self.root, "七泽美亚", [ref("javdb", "NPD3")],
                                       [alias("七沢みあ")])
        self.assertEqual(next(row["url"] for row in rows if row["site"] == "missav"), first)
        second = first.replace("dm102", "dm1002")
        path.write_text(json.dumps({"source": entry_links.DIRECTORY_URL,
                                  "entries": [{"name": "七泽美亚", "url": second}]}), encoding="utf-8")
        rows = entry_links.entry_links(self.root, "七泽美亚", [ref("javdb", "NPD3")],
                                       [alias("七沢みあ")])
        self.assertEqual(next(row["url"] for row in rows if row["site"] == "missav"), second)


    def test_a_pasted_address_is_trimmed_down_to_its_domain(self):
        """镜像地址多半是整条复制过来的，前缀与末尾的斜杠不值得弹一条错误。"""
        entry_links.save(self.root, self.body(missav={"host": "https://missav.ai/"}))
        self.assertEqual(entry_links.read(self.root)["missav"]["host"], "missav.ai")

    def test_an_address_with_a_path_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            entry_links.save(self.root, self.body(
                javdb={"host": "javdb.com/actors/{javdb_id}"}))
        self.assertIn("只写域名本身", str(caught.exception))

    def test_clearing_the_box_goes_back_to_the_default_domain(self):
        entry_links.save(self.root, self.body(missav={"host": "missav.ai"}))
        entry_links.save(self.root, self.body(missav={"host": "  "}))
        self.assertEqual(entry_links.read(self.root)["missav"]["host"], "")

    def test_a_hand_broken_file_falls_back_instead_of_emptying_the_row(self):
        (self.root / entry_links.FILENAME).write_text(
            json.dumps({"sites": {"javdb": {"host": "not a domain"}}}), encoding="utf-8")
        self.assertEqual(entry_links.read(self.root)["javdb"]["host"], "")


class MissavDirectoryTests(unittest.TestCase):
    def directory(self, rows, *, complete=False):
        return entry_links.directory_index({"source": entry_links.DIRECTORY_URL,
                                            "entries": rows, "complete": complete})

    def test_failed_source_page_has_no_template_fallback(self):
        address = "https://missav.ws/dm31/cn/actresses/Rina"
        for complete in (False, True):
            with self.subTest(complete=complete):
                directory = self.directory([{"name": "Rina", "url": address, "verified": False}],
                                           complete=complete)
                rows = entry_links.build(entry_links.defaults(), "RINA", [ref("javdb", "actor")],
                                         directory=directory)
                self.assertNotIn("missav", [row["site"] for row in rows])

    def test_conflicting_page_evidence_requires_a_verified_source(self):
        failed = {"name": "Rina", "url": "https://missav.ws/dm31/cn/actresses/Rina", "verified": False}
        alternate = {"name": "Rina", "url": "https://missav.ws/dm83/cn/actresses/Rina"}
        self.assertEqual(self.directory([failed, alternate]).path("Rina", "RINA"), "")
        restored = self.directory([{**failed, "verified": True}])
        self.assertEqual(restored.path("Rina", "RINA"), "/dm31/cn/actresses/Rina")

    def test_site_glyphs_and_compound_stage_names_use_the_published_href(self):
        for canonical, jp, display, slug in (
                ("北条麻妃", "北條麻妃", "北条麻妃", "北条麻妃"),
                ("森泽佳奈", "飯岡かなこ", "森泽佳奈 (饭冈佳奈子)", "森沢かな（飯岡かなこ）")):
            with self.subTest(canonical=canonical):
                address = "https://missav.ws/dm193/cn/actresses/" + quote(slug)
                directory = self.directory([{"name": display, "url": address}])
                rows = entry_links.build(entry_links.defaults(), canonical, [ref("javdb", "actor")],
                                         [alias(jp)], directory=directory)
                self.assertEqual(next(row["url"] for row in rows if row["site"] == "missav"), address)

    def test_ambiguous_directory_names_have_no_missav_entry(self):
        directory = self.directory([
            {"name": "同名艺人", "url": "https://missav.ws/dm1/cn/actresses/A"},
            {"name": "同名艺人", "url": "https://missav.ws/dm2/cn/actresses/B"},
        ])
        rows = entry_links.build(entry_links.defaults(), "同名艺人", [ref("javdb", "actor")],
                                 directory=directory)
        self.assertEqual([row["site"] for row in rows], ["javdb"])

    def test_native_name_whitespace_preserves_the_published_url(self):
        for written, published in (("ローレン 花恋", "ローレン花恋"),
                                   ("日向\u3000なつ", "日向なつ")):
            with self.subTest(name=written):
                address = "https://missav.ws/dm193/cn/actresses/" + quote(published)
                directory = self.directory([{"name": published, "url": address}])
                rows = entry_links.build(entry_links.defaults(), written, [ref("javdb", "actor")],
                                         directory=directory)
                self.assertEqual(next(row["url"] for row in rows if row["site"] == "missav"), address)

    def test_latin_name_whitespace_keeps_names_distinct(self):
        directory = self.directory([{"name": "Rina", "url": "https://missav.ws/dm1/cn/actresses/Rina"}],
                                   complete=True)
        rows = entry_links.build(entry_links.defaults(), "Rin A", [ref("javdb", "actor")],
                                 directory=directory)
        self.assertEqual([row["site"] for row in rows], ["javdb"])

    def test_distinct_published_slugs_remain_ambiguous_with_native_whitespace(self):
        directory = self.directory([
            {"name": "日向なつ", "url": "https://missav.ws/dm1/cn/actresses/" + quote("日向なつ")},
            {"name": "日向 なつ", "url": "https://missav.ws/dm2/cn/actresses/" + quote("日向 なつ")},
        ])
        self.assertEqual(directory.path("日向なつ", "日向なつ"), "")

    def test_prefix_variants_of_the_same_published_slug_share_one_identity(self):
        directory = self.directory([
            {"name": "A", "url": "https://missav.ws/dm1/cn/actresses/A"},
            {"name": "A", "url": "https://missav.ws/dm2/cn/actresses/A"},
        ])
        self.assertEqual(directory.path("A", "A"), "/dm1/cn/actresses/A")

    def test_an_incomplete_directory_does_not_assert_an_unlisted_name_is_absent(self):
        directory = self.directory([])
        self.assertIsNone(directory.path("七沢みあ", "七泽美亚"))
        rows = entry_links.build(entry_links.defaults(), "七泽美亚", [ref("javdb", "actor")],
                                 [alias("七沢みあ")], directory=directory)
        self.assertTrue(any(row["site"] == "missav" for row in rows))

    def test_a_complete_directory_has_no_entry_for_an_unmatched_name(self):
        rows = entry_links.build(entry_links.defaults(), "同名艺人", [ref("javdb", "actor")],
                                 directory=self.directory([], complete=True))
        self.assertEqual([row["site"] for row in rows], ["javdb"])

    def test_directory_entries_are_scoped_to_the_public_actress_origin(self):
        invalid = ("http://missav.ws/cn/actresses/A", "https://missav.ws.evil.test/cn/actresses/A",
                   "https://name@missav.ws/cn/actresses/A", "https://missav.ws/cn/videos/A",
                   "https://missav.ws/cn/actresses/A/extra")
        directory = self.directory([{"name": "A", "url": value} for value in invalid])
        self.assertEqual(directory.paths, {})

    def test_a_mirror_preserves_the_directory_path(self):
        settings = entry_links.defaults()
        settings["missav"]["host"] = "missav.ai"
        directory = self.directory([{"name": "A", "url": "https://missav.ws/dm102/cn/actresses/A"}])
        rows = entry_links.build(settings, "A", [ref("javdb", "actor")], directory=directory)
        self.assertEqual(next(row["url"] for row in rows if row["site"] == "missav"),
                         "https://missav.ai/dm102/cn/actresses/A")

if __name__ == "__main__":
    unittest.main()
