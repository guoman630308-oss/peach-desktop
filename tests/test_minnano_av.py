"""minnano-av 的页面解析：名册从 JSON-LD 读，翻页地址按属性原样还原。"""
import unittest

from peach.minnano_av import (
    actress_id, production_ref, profile_text, roster_page, roster_url, search_url,
)

# 按 2026-09-05 实测的 actress_list.php?production=134 复刻：名册在 JSON-LD 里，
# 正文那个 `<a>` 的文字是「名字 + 女優情報」，下一页地址在属性里带着 `&amp;`。
ROSTER = """
<html><head>
<link rel="canonical" href="https://www.minnano-av.com/actress_list.php?production=134">
<link rel="next" href="/actress_list.php?production=134&amp;page=2">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"CollectionPage",
 "mainEntity":{"@type":"ItemList","numberOfItems":249,
  "itemListElement":[
   {"@type":"ListItem","position":1,
    "item":{"@type":"Person","name":"鈴北梨乃",
            "url":"https://www.minnano-av.com/actress231277.html"}},
   {"@type":"ListItem","position":2,
    "item":{"@type":"Person","name":"明日花 キララ",
            "url":"https://www.minnano-av.com/actress3145.html"}}]}}
</script>
</head><body>
<a href="/actress231277.html">鈴北梨乃女優情報</a>
<a href="/actress3145.html">明日花 キララ女優情報</a>
</body></html>
"""

PROFILE = """
<table>
<tr><td><span>血液型</span><p><a href="actress_list.php?blood_type=A">A型</a></p></td></tr>
<tr><td><span>所属事務所</span><p>
  <a href="actress_list.php?production=573">KRONE(クローネ)</a></p></td></tr>
</table>
"""

LAST_PAGE = """
<html><head>
<script type="application/ld+json">
{"@type":"CollectionPage","mainEntity":{"@type":"ItemList","numberOfItems":249,
 "itemListElement":[{"item":{"name":"篠田ゆう",
  "url":"https://www.minnano-av.com/actress9999.html"}}]}}
</script>
</head><body></body></html>
"""


class RosterPageTests(unittest.TestCase):
    def test_the_names_come_from_the_structured_data_not_the_anchor_text(self):
        """正文那个链接的文字是「鈴北梨乃女優情報」。

        把它当名字拿去和账本对，249 位一个都对不上，而看起来只像是「库里没有这些人」。
        """
        people, _, _ = roster_page(ROSTER)
        self.assertEqual(people, [("鈴北梨乃", "231277"), ("明日花 キララ", "3145")])

    def test_the_declared_headcount_is_read_back_for_reconciliation(self):
        """站点自己声明这家有多少人，翻完对不上就说明断在了中间。"""
        self.assertEqual(roster_page(ROSTER)[1], 249)

    def test_the_next_page_address_is_unescaped_before_it_is_used(self):
        """属性里的 `&amp;` 不还原，服务端认不出 `amp;page`，照样回第一页。"""
        following = roster_page(ROSTER, "https://www.minnano-av.com/actress_list.php")[2]
        self.assertEqual(
            following, "https://www.minnano-av.com/actress_list.php?production=134&page=2")

    def test_a_page_without_a_next_link_ends_the_walk(self):
        people, total, following = roster_page(LAST_PAGE)
        self.assertEqual([name for name, _ in people], ["篠田ゆう"])
        self.assertEqual((total, following), (249, ""))

    def test_a_page_of_junk_yields_nothing_instead_of_raising(self):
        self.assertEqual(roster_page("<html><script type=\"application/ld+json\">{</script>"),
                         ([], 0, ""))


class ProductionRefTests(unittest.TestCase):
    def test_the_internal_link_gives_both_the_number_and_the_name_on_the_site(self):
        """编号是名册唯一的入口，名字用来核对这个编号确实是那一家。"""
        self.assertEqual(production_ref(PROFILE), ("573", "KRONE(クローネ)"))

    def test_the_agency_cell_is_still_read_as_plain_text(self):
        self.assertEqual(profile_text(PROFILE, "所属事務所"), "KRONE(クローネ)")

    def test_a_profile_without_an_agency_link_yields_nothing(self):
        self.assertEqual(production_ref("<td><span>所属事務所</span><p>フリー</p></td>"),
                         ("", ""))


class AddressTests(unittest.TestCase):
    def test_the_first_roster_page_carries_no_page_parameter(self):
        """和站点自己的 canonical 保持一致，免得同一页收成两个地址。"""
        self.assertEqual(roster_url("134"),
                         "https://www.minnano-av.com/actress_list.php?production=134")
        self.assertEqual(roster_url(134, 2),
                         "https://www.minnano-av.com/actress_list.php?production=134&page=2")

    def test_the_actress_number_only_counts_when_the_redirect_landed_on_a_profile(self):
        self.assertEqual(actress_id("https://www.minnano-av.com/actress3145.html"), "3145")
        self.assertEqual(actress_id("https://www.minnano-av.com/search_result.php?x=1"), "")

    def test_the_search_word_is_percent_encoded_as_utf8(self):
        self.assertTrue(search_url("篠田ゆう").endswith("%E7%AF%A0%E7%94%B0%E3%82%86%E3%81%86"))


if __name__ == "__main__":
    unittest.main()
