"""站点自己挂在页面上的标识资产：header 的 `<img>` 与页面上的 X 账号。

样本按 bambi.ne.jp 2026-09-05 的实测结构写：只声明一枚 16×16 的 `favicon.ico`，
header 里挂着 `images/_/header_logo.png`，页脚一排社媒里有两个 X 账号。
"""
from __future__ import annotations

import unittest

from peach import site_logos


PAGE = """
<!doctype html><html><head>
<link rel="icon" href="/favicon.ico">
<meta property="og:image" content="https://bambi.ne.jp/images/1/image.jpg">
</head><body>
<header><a href="/"><img src="images/_/header_logo.png" alt="Bambi Promotion"></a></header>
<img src="images/1/image.jpg" alt="">
<footer>
  <a href="https://x.com/BambiPromotion"><img src="images/_/icon/x.png" alt="X"></a>
  <a href="https://twitter.com/Bambi__MG">MG</a>
  <a href="https://www.instagram.com/bambi.hajimero/">Instagram</a>
  <a href="https://x.com/intent/tweet?url=https://bambi.ne.jp/">分享</a>
</footer></body></html>
"""


class LogoImageTests(unittest.TestCase):
    def test_the_header_mark_is_found_and_made_absolute(self):
        self.assertEqual(site_logos.logo_images(PAGE, "https://bambi.ne.jp/"),
                         ["https://bambi.ne.jp/images/_/header_logo.png"])

    def test_the_hero_photograph_is_not_a_mark(self):
        """首屏那张大图没有任何一处说自己是标识。按位置猜「第一张图」会取到模特照。"""
        self.assertNotIn("https://bambi.ne.jp/images/1/image.jpg",
                         site_logos.logo_images(PAGE, "https://bambi.ne.jp/"))

    def test_a_social_button_is_not_a_mark(self):
        """`images/_/icon/x.png` 的路径里带 x，取回来是 X 自己的鸟，不是这家公司的标识。"""
        self.assertEqual(site_logos.logo_images(
            '<img src="/assets/icon/logo-x.png">', "https://bambi.ne.jp/"), [])

    def test_the_class_and_the_alt_also_declare_a_mark(self):
        html = ('<img src="/assets/a1.png" class="site-logo">'
                '<img src="/assets/a2.png" alt="Company logo">'
                '<img src="/assets/a3.png" id="brand">')
        self.assertEqual(site_logos.logo_images(html, "https://x.jp/"),
                         ["https://x.jp/assets/a1.png", "https://x.jp/assets/a2.png",
                          "https://x.jp/assets/a3.png"])

    def test_the_same_mark_twice_is_one_candidate(self):
        html = '<img src="/logo.png"><img src="https://x.jp/logo.png">'
        self.assertEqual(site_logos.logo_images(html, "https://x.jp/"),
                         ["https://x.jp/logo.png"])

    def test_a_lazy_loaded_mark_still_counts(self):
        html = '<img data-src="/logo.png" src="">'
        self.assertEqual(site_logos.logo_images(html, "https://x.jp/"),
                         ["https://x.jp/logo.png"])

    def test_an_inline_placeholder_is_not_a_candidate(self):
        """懒加载占位图顶着标识那张 `<img>` 的 class，词形闸门拦不住它。

        取字节那一步打不开 `data:`，白等两轮重试再记一次「取不回来」；eltra.jp
        2026-09-05 那一页就有两张。
        """
        html = ('<img class="logo" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">'
                '<img class="logo" src="/logo.png">')
        self.assertEqual(site_logos.logo_images(html, "https://x.jp/"),
                         ["https://x.jp/logo.png"])

    def test_an_empty_page_yields_nothing(self):
        self.assertEqual(site_logos.logo_images("", "https://x.jp/"), [])


class ProfileTests(unittest.TestCase):
    def test_both_accounts_are_found_in_page_order(self):
        self.assertEqual(site_logos.x_profiles(PAGE, "https://bambi.ne.jp/"),
                         ["https://x.com/BambiPromotion", "https://x.com/Bambi__MG"])

    def test_a_share_button_is_not_an_account(self):
        """`x.com/intent/tweet` 的 og:image 是 X 自己的卡片图，每家取回来都一样。"""
        self.assertFalse(site_logos.is_x_profile("https://x.com/intent/tweet?url=a"))
        self.assertFalse(site_logos.is_x_profile("https://x.com/hashtag/av"))
        self.assertFalse(site_logos.is_x_profile("https://x.com/"))
        self.assertFalse(site_logos.is_x_profile("https://instagram.com/bambi"))

    def test_a_status_link_is_not_an_account_page(self):
        self.assertFalse(site_logos.is_x_profile("https://x.com/BambiPromotion/status/1"))

    def test_the_old_host_is_the_same_account(self):
        self.assertTrue(site_logos.is_x_profile("https://twitter.com/BambiPromotion"))
        self.assertEqual(site_logos.x_profiles(
            '<a href="https://twitter.com/A"></a><a href="https://x.com/A"></a>',
            "https://x.jp/"), ["https://x.com/A"])


class AvatarTests(unittest.TestCase):
    def test_the_profile_image_falls_back_through_the_tiers(self):
        html = ('<meta property="og:image" content="https://pbs.twimg.com/'
                'profile_images/1/7N42mRCO_400x400.jpg">')
        tiers = site_logos.avatar_tiers(html)
        self.assertEqual(tiers[0],
                         "https://pbs.twimg.com/profile_images/1/7N42mRCO.jpg")
        self.assertIn("_400x400", tiers[1])

    def test_a_banner_is_not_an_avatar(self):
        """登出页有时把横幅放进 og:image。那不是头像，退回空手比装错强。"""
        html = ('<meta property="og:image" '
                'content="https://pbs.twimg.com/profile_banners/1/2">')
        self.assertEqual(site_logos.avatar_tiers(html), [])

    def test_a_page_without_the_tag_yields_nothing(self):
        """Instagram 登出页就是这样：`og:image` 一条都没有（2026-09-05 实测）。"""
        self.assertEqual(site_logos.avatar_tiers("<html></html>"), [])


if __name__ == "__main__":
    unittest.main()
