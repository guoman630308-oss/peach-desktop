"""死链复查：搬走了和没了要分开，而且「搬走了」必须还在同一个站。"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_module():
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(
        "rediscover_entity_links", REPO / "scripts" / "rediscover_entity_links.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SameSiteTests(unittest.TestCase):
    def test_private_suffix_tenants_are_independent(self):
        module = load_module()
        self.assertFalse(module.same_site("https://a.github.io/x", "https://b.github.io/y"))
        self.assertFalse(module.same_site("https://a.blogspot.com/x", "https://b.blogspot.com/y"))
        self.assertFalse(module.same_site("https://co.jp/", "https://co.jp/"))

    def setUp(self):
        self.module = load_module()

    def test_a_link_to_another_site_is_not_a_repair(self):
        """实测：KRONE 的索引页里「上川星空」那一条指向她的 X 账号。

        人是对的——`天馬ゆい` 就在账本别名里，那确实是她。但那不是事务所页面。
        照单收下会留下一条标着「KRONE(クローネ)」、点开却是 Twitter 的 official 链接，
        比一条死链更糟：它看起来是对的。修复的定义是「在同一个站点上找到新地址」，
        不是「找到关于这个人的另一个链接」。
        """
        self.assertFalse(self.module.same_site(
            "https://x.com/tenma_yui2", "http://krone-web.jp/model/info.php?5493"))

    def test_www_and_scheme_changes_are_still_the_same_site(self):
        """T-POWERS 的真实修复就是这个形态：http 裸域 → https 带 www，路径换了一层。"""
        self.assertTrue(self.module.same_site(
            "https://www.t-powers.co.jp/talent/%e6%b6%bc%e6%a3%ae/",
            "http://t-powers.co.jp/official/talent/%E6%B6%BC%E6%A3%AE"))

    def test_a_lookalike_domain_is_not_the_same_site(self):
        """`evil-t-powers.co.jp` 不是 `t-powers.co.jp`。判据要落在点边界上。"""
        self.assertFalse(self.module.same_site(
            "https://evil-t-powers.co.jp/x", "http://t-powers.co.jp/y"))

    def test_a_japanese_second_level_suffix_keeps_three_labels(self):
        """`co.jp` 下取末两段会把所有日本公司判成同一家。"""
        self.assertEqual(self.module.registrable("www.t-powers.co.jp"), "t-powers.co.jp")
        self.assertEqual(self.module.registrable("x.com"), "x.com")
        self.assertFalse(self.module.same_site("https://a.co.jp/", "https://b.co.jp/"))


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_a_list_page_does_not_confirm_an_individual(self):
        """列表页同样回 200。

        少了标题这一关，`/talent/` 本身会被当成每个人的新地址——四百个人被改写成
        同一个列表链接，而且每一条都「能打开」。
        """
        self.assertEqual(
            self.module.confirms("<title>所属タレント一覧</title>", ["涼森れむ"]), "")

    def test_a_title_that_spaces_the_family_name_still_confirms(self):
        """站点常在姓与名之间加空格或全角空格；逐字比会把真页面判成不匹配。"""
        self.assertIn("涼森", self.module.confirms(
            "<title>涼森 れむ｜AVプロダクション【ティーパワーズ】公式サイト</title>", ["涼森れむ"]))
        self.assertIn("天海", self.module.confirms(
            "<title>天海　つばさ｜ティーパワーズ</title>", ["天海つばさ"]))

    def test_an_encoded_name_in_the_href_is_matched(self):
        """日文站的艺人页地址常常就是 URL 编码后的名字，而锚文本只是一张图。"""
        html = ('<a href="/talent/%E6%B6%BC%E6%A3%AE%E3%82%8C%E3%82%80/"><img></a>'
                '<a href="/other">別の人</a>')
        found = self.module.anchors_naming(html, "https://www.t-powers.co.jp/", ["涼森れむ"])
        self.assertEqual([url for url, _ in found],
                         ["https://www.t-powers.co.jp/talent/%E6%B6%BC%E6%A3%AE%E3%82%8C%E3%82%80/"])


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_index_pages_walk_up_from_the_dead_url(self):
        """艺人页几乎总挂在某个列表下面，而改版通常只动其中一层。"""
        self.assertEqual(
            self.module.index_candidates("http://www.t-powers.co.jp/official/talent/x"),
            ["http://www.t-powers.co.jp/official/talent/",
             "http://www.t-powers.co.jp/official/",
             "http://www.t-powers.co.jp/"])


if __name__ == "__main__":
    unittest.main()
