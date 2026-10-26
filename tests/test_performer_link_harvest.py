"""minnano-av 女优链接采集：只采信重定向，只留站外链接。"""
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_module():
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(
        "harvest_performer_links", REPO / "scripts" / "harvest_performer_links.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 按 2026-09-01 实测的 actress494354 资料表复刻：站内链接是相对路径，站外是绝对 URL。
PROFILE = """
<table>
 <tr><td><span>血液型</span><p><a href="actress_list.php?blood_type=A">A型</a></p></td></tr>
 <tr><td><span>出身地</span><p><a href="actress_list.php?place=13">東京都</a></p></td></tr>
 <tr><td><span>所属事務所</span><p><a href="actress_list.php?production=360">New Actor eXperience</a></p></td></tr>
 <tr><td><span>ブログ</span><p><a href="https://twitter.com/alice_710_">http://twitter.com/alice_710_</a></p></td></tr>
 <tr><td><span>公式サイト</span><p><a href="http://official.nax-pro.com/model/">nax-pro</a></p></td></tr>
 <tr><td><span>タグ</span><p><a href="actress_list.php?tag_a_id=76">長身</a></p></td></tr>
</table>
"""


class Response:
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body.encode("utf-8"), url


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_only_external_links_survive_the_profile_table(self):
        """站内链接是检索入口，不是这个人的链接。

        不筛的话每位女优都会挂上「A型」「東京都」「長身」三条站内跳转，
        资料页上的「相关链接」就变成了噪音。
        """
        fields = self.module.profile_fields(PROFILE)
        self.assertEqual(set(fields), {"ブログ", "公式サイト"})
        self.assertEqual(fields["ブログ"], ["https://twitter.com/alice_710_"])

    def test_the_agency_is_read_as_text_because_its_link_is_internal(self):
        """事务所名值得留下，但它的链接指向站内检索，不能当外链装进去。"""
        self.assertEqual(self.module.profile_text(PROFILE, "所属事務所"),
                         "New Actor eXperience")

    def test_a_blog_field_holding_twitter_is_classified_as_social(self):
        """minnano-av 把 Twitter 放在「ブログ」栏里。

        按栏目名分类会把它归成博客；按 host 分类才对得上它实际是什么。
        """
        self.assertEqual(self.module.classify("https://twitter.com/alice_710_"),
                         ("social", "X @alice_710_"))
        self.assertEqual(self.module.classify("https://x.com/foo"), ("social", "X @foo"))
        self.assertEqual(self.module.classify("http://official.nax-pro.com/model/"),
                         ("official", "官方网站"))

    def test_an_agency_page_is_labelled_with_the_agency_name(self):
        """域名归属未知时才退回事务所名：那时它至少没有和已知事实矛盾。

        退回通用的「官方网站」等于把取到的信息扔掉——资料页上一排链接全叫同一个名字，
        点之前看不出哪个是事务所。
        """
        self.assertEqual(
            self.module.classify("http://www.t-powers.co.jp/official/talent/x", "T-POWERS"),
            ("official", "T-POWERS"))

    def test_the_label_follows_the_domain_not_the_agency_field(self):
        """白石亚子的「所属事務所」是 T-POWERS，「公式サイト」却是 Prestige 的宣传页。

        这是资料表里两行不同的事实。把事务所名贴到这条链接上，一个控件就同时替两家公司
        说话：文字写着 T-POWERS，图标和落点都是 Prestige。
        """
        # 归属表的键和 `host_of` 用同一条归一规则，都不带 `www.`。
        owners = {"prestige-av.com": "Prestige"}
        self.assertEqual(
            self.module.classify("http://www.prestige-av.com/special/shiraishi_ako.php",
                                 "T-POWERS", owners.get),
            ("official", "Prestige"))
        # 域名确实归事务所时，两条规则给出同一个答案。
        self.assertEqual(
            self.module.classify("http://www.t-powers.co.jp/official/talent/x", "T-POWERS",
                                 {"t-powers.co.jp": "T-POWERS"}.get),
            ("official", "T-POWERS"))

    def test_a_personal_platform_account_is_not_an_agency_page(self):
        """Facebook 和 Linktree 此前不在平台表里，走了 official 分支、被贴上事务所名。"""
        self.assertEqual(
            self.module.classify("https://www.facebook.com/miho.tanaka.334491", "T-POWERS"),
            ("social", "Facebook @miho.tanaka.334491"))
        self.assertEqual(
            self.module.classify("http://linktr.ee/sawa_sasaki", "GG"),
            ("social", "Linktree @sawa_sasaki"))
        self.assertEqual(
            self.module.classify("http://kawakami-yuu.livedoor.biz/", "Ys Promotion"),
            ("social", "博客"))

    def test_a_blog_is_social_not_an_official_site(self):
        """`blog.livedoor.jp/kaede_fuyutuki/` 是本人的博客，不是公式站。

        实测冬月枫的「公式サイト」栏给的就是它；照栏目名当官网会让标签说谎。
        """
        self.assertEqual(
            self.module.classify("http://blog.livedoor.jp/kaede_fuyutuki/", "T-POWERS"),
            ("social", "博客"))

    def test_a_lookalike_host_is_not_taken_for_the_real_platform(self):
        """`notx.com` 不是 x.com。后缀匹配必须落在点边界上。"""
        kind, _ = self.module.classify("https://notx.com/foo")
        self.assertEqual(kind, "official")


class NameChainTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_the_japanese_stage_name_is_tried_before_the_simplified_chinese_one(self):
        """账本里 performer 的规范名是简体中文，日文站按它一个都搜不到。

        实测：拿 `凉森玲梦` 直接搜，12 位里只有 1 位命中；能用的写法 `涼森れむ`
        就在别名里。名字链的次序不是风格问题，它决定了这个脚本有没有用。
        """
        chain = self.module.name_chain("凉森玲梦", ["涼森れむ", "Remu Suzumori", "すずもりれむ"])
        self.assertEqual(chain[0], "涼森れむ")
        self.assertEqual(chain, ["涼森れむ", "すずもりれむ", "凉森玲梦"])

    def test_romaji_never_enters_the_chain(self):
        """罗马字对日文站基本无效。

        留着不只是白跑一次往返：它落空后混进未取得，看起来像「这个人查不到」，
        而真相是「我们从没用她的日文名查过」。
        """
        self.assertNotIn("Alice Shaku",
                         self.module.name_chain("释爱丽丝", ["Alice Shaku", "釈アリス"]))

    def test_duplicates_collapse_and_a_kanji_only_name_still_qualifies(self):
        """`美谷朱音` 既是账本规范名也是有效日文写法，实测能直接命中。"""
        self.assertEqual(self.module.name_chain("美谷朱音", ["美谷朱音", "美谷朱里"]),
                         ["美谷朱音", "美谷朱里"])

    def test_a_performer_with_only_latin_names_yields_an_empty_chain(self):
        self.assertEqual(self.module.name_chain("Cola", ["Cola Sauce"]), [])


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_a_unique_match_is_taken_from_the_final_url(self):
        page = f"<html>{PROFILE}</html>"
        rows, found, agency, note = self.module.scan(
            lambda *a: Response(200, page, "https://www.minnano-av.com/actress494354.html"), "釈アリス", 5)
        self.assertEqual(found, "494354")
        self.assertEqual(agency, "New Actor eXperience")
        self.assertEqual({row["url"] for row in rows},
                         {"https://twitter.com/alice_710_", "http://official.nax-pro.com/model/"})
        self.assertIn("actress494354", rows[0]["evidence"])
        self.assertIn("2 条外链", note)

    def test_a_search_result_page_is_refused_even_though_it_is_full_of_actress_links(self):
        """这是本脚本最危险的失败模式。

        检索页正文里同样有一堆 `actressNNN.html`——那是「相关女优」。按正文解析会把
        别人的社媒安到这个人头上，而且看起来完全正常。信号只在最终地址里，
        所以停在 search_result.php 就必须判未取得，哪怕正文看起来很有料。
        """
        body = ('<a href="/actress100069.html">A</a><a href="/actress102284.html">B</a>'
                + PROFILE)
        rows, found, _, note = self.module.scan(
            lambda *a: Response(200, body,
                                "https://www.minnano-av.com/search_result.php?search_word=x"),
            "さくら", 5)
        self.assertEqual((rows, found), ([], ""))
        self.assertIn("未唯一命中", note)

    def test_a_non_200_is_reported_as_未取得(self):
        rows, _, _, note = self.module.scan(
            lambda *a: Response(503, "", "https://www.minnano-av.com/"), "誰か", 5)
        self.assertEqual(rows, [])
        self.assertIn("503", note)

    def test_the_search_url_encodes_japanese_as_utf8(self):
        self.assertTrue(self.module.search_url("釈アリス").endswith(
            "%E9%87%88%E3%82%A2%E3%83%AA%E3%82%B9"))


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())

    def ledger(self, rows, aliases=()):
        db = self.tmp / "ledger.db"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT)")
        connection.execute("CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT, confidence REAL DEFAULT 1)")
        connection.execute("CREATE TABLE asset(id INTEGER PRIMARY KEY, medium TEXT, code TEXT)")
        connection.execute("CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER)")
        connection.executemany("INSERT INTO entity_alias(entity_id,alias) VALUES(?,?)", aliases)
        asset_id = 0
        for entity_id, name, codes in rows:
            connection.execute("INSERT INTO entity VALUES(?,?,?)", (entity_id, "performer", name))
            for code in codes:
                asset_id += 1
                connection.execute("INSERT INTO asset VALUES(?,?,?)", (asset_id, "video", code))
                connection.execute("INSERT INTO asset_entity VALUES(?,?)", (asset_id, entity_id))
        connection.commit()
        connection.close()
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    def test_a_performer_with_no_jav_code_is_skipped(self):
        """minnano-av 是 JAV 资料库；拿中文素人创作者去查必然落空。

        更糟的是落空会混进未取得，把真正查不到的 JAV 女优盖住。
        """
        connection = self.ledger([(1, "立花美涼", ["ABW-001", "ABW-002", "ABW-003"]),
                                  (2, "Cola酱", ["", "", ""])])
        try:
            self.assertEqual([r["name"] for r in self.module.load_performers(connection, 1)],
                             ["立花美涼"])
        finally:
            connection.close()

    def test_the_selected_performer_carries_its_japanese_name_chain(self):
        """选人和取名字必须一起出来。

        分两步做的话，调用方很容易只拿到 canonical_name 就去查——那就退回到
        「拿简体中文搜日文站」的原样，而且不会有任何报错。
        """
        connection = self.ledger([(1, "凉森玲梦", ["ABW-001"])],
                                 aliases=[(1, "涼森れむ"), (1, "Remu Suzumori")])
        try:
            selected = self.module.load_performers(connection, 1)
            self.assertEqual(selected[0]["chain"], ["涼森れむ", "凉森玲梦"])
        finally:
            connection.close()

    def test_a_performer_below_the_minimum_is_not_queried(self):
        connection = self.ledger([(1, "多作", ["ABW-001", "ABW-002"]), (2, "少作", ["ABW-003"])])
        try:
            self.assertEqual([r["name"] for r in self.module.load_performers(connection, 2)],
                             ["多作"])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
