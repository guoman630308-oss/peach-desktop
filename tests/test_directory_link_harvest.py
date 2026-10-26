"""目录型社媒采集：整站翻一遍、按名字对回账本、X 账号验活；以及抽出来共用的社媒判据。"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from peach import social_links   # noqa: E402


def load_module():
    spec = importlib.util.spec_from_file_location(
        "harvest_directory_links", REPO / "scripts" / "harvest_directory_links.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 按 2026-09-02 实测的 laoshi.ink actor-001 复刻：标题带三种写法，ld+json 有 sameAs，
# 「基本资料」是 <span>标签</span><strong>值</strong>，别名可能是占位词「待补充」。
SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://laoshi.ink/actresses/actor-075.html</loc></url>
<url><loc>https://laoshi.ink/actresses/actor-001.html</loc></url>
<url><loc>https://laoshi.ink/korean/actor-003.html</loc></url>
<url><loc>https://laoshi.ink/actresses/actor-001.html</loc></url>
</urlset>"""

LAOSHI_PAGE = """<html><head>
<title>波多野结衣（波多野結衣 / Yui Hatano）｜日本女优资料与职业履历 - 老师图鉴</title>
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [{"@type": "Person",
 "name": "波多野结衣", "sameAs": ["https://x.com/hatano_yui"]}]}</script>
</head><body>
<a href="/actresses/index.html">返回</a>
<a href="https://cdn.jsdelivr.net/npm/x.js">脚本</a>
<div class="spot-fact-table">
 <div><span>中文名</span><strong>波多野结衣</strong></div>
 <div><span>日文名</span><strong>波多野結衣</strong></div>
 <div><span>罗马音</span><strong>Yui Hatano</strong></div>
 <div><span>别名</span><strong>待补充</strong></div>
 <div><span>公开主页</span><strong>X / Twitter</strong></div>
</div>
<a href="https://x.com/hatano_yui" target="_blank">X</a>
<a href="https://x.com/intent/follow?screen_name=hatano_yui">关注</a>
</body></html>"""

LAOSHI_TWO_NAMES = """<html><head><title>西田カリナ（Karina Nishida）｜资料 - 老师图鉴</title></head>
<body><div><span>别名</span><strong>西田卡莉娜、にしだカリナ</strong></div></body></html>"""

# 按 2026-09-02 实测的 bstar-pro.com 复刻：列表页要先过年龄门；页脚有写坏的相对路径。
BSTAR_GATE = '<form method="post"><input type="radio" name="age_check" value="yes"></form>'
BSTAR_LIST = """<ul>
 <li><a href="model.html?mid=355" class="model_img_hover"><img alt="瀬戸環奈">
   <span class="infoDate">瀬戸環奈</span><span>Kanna Seto</span></a></li>
 <li><a href="model.html?mid=360"><img alt="架空同名"><span>架空同名</span></a></li>
 <li><a href="model.html?mid=355">重复</a></li>
</ul>
<a href="https://x.com/bstar_pro/">X</a><a href="https://www.instagram.com/bstar_pro_">IG</a>"""
BSTAR_MODEL = """<ul class="sns_list">
 <li><a href="https://x.com/kanna_seto0510"></a></li>
 <li><a href="https://x.com/setokan_sub"></a></li>
 <li><a href="https://www.instagram.com/setokanna_/"></a></li>
 <li><a href="https://www.youtube.com/@setokan.channel"></a></li>
</ul>
<a href="https://s1s1s1.com/actress/detail/849776">S1</a>
<a href="https://x.com/bstar_pro/">事务所</a>
<a href="/https://www.instagram.com/bstar_pro_">写坏的</a>"""

# 按 2026-09-04 实测的 jae.tokyo 三届名录复刻。三处陷阱都留在样本里：2014 那届开头是
# 一条填好数据的模板 `<li>`（在注释里），2015 那届资料表里有片商的商品页，2017 那届每页
# 都是从上一位那页复制来的，上一位的账号整段留在注释里。
JAE_2014_PAGE = """<section class="sec_list_performers"><ul>
 <!--
 <li id="AyamiShunka"><h2>あやみ</h2><p class="alignright">Shunka</p>
  <img src="images/nophoto.jpg" alt="">
  <div class="clearfix"><p><span><span class="rc3">リンク</span></span>
   <span><a href="http://example.com/template">公式</a></span></p></div></li>
 -->
 <li id="YuiHatano"><h2>波多野結衣</h2><p class="alignright">Yui Hatano</p>
  <img src="images/Hatano_Yui.jpg" alt="波多野結衣">
  <div class="clearfix">
   <p><span><span class="rc3">所属</span></span><span>MAXING</span></p>
   <p><span><span class="rc3">リンク</span></span><span>
     <a href="http://blog.livedoor.jp/hatano_yui/">公式ブログ</a>
     <a href="https://twitter.com/hatano_yui">ツイッター</a></span></p></div></li>
 <li id="Nameless"><h2>架空同名</h2><p class="alignright">Nameless</p>
  <img src="images/nophoto.jpg" alt="架空同名"><div class="clearfix"></div></li>
</ul></section>"""

JAE_2015_PAGE = """<div id="joyu001" class="off">
 <img src="images/actress/sod/seto_l.jpg" class="textLeft">
 <div class="textRightOrange"><div class="nameBackOrange">
  <h3 class="bigName">瀬戸環奈</h3><h4 class="smallName">Kanna Seto</h4></div>
  <table><tr><th>おすすめの<br>作品タイトル</th>
   <td><a href="http://ec.sod.co.jp/detail/index/-_-/iid/STAR-621" target="_blank">作品</a></td>
  </tr></table>
  <div class="actLinkOrange">
   <a href="http://kanna-seto.ameblo.jp/" target="_blank">オフィシャルブログ<span></span></a>
   <a href="https://twitter.com/kanna_seto0510" target="_blank">ツイッター<span></span></a>
  </div></div></div>
<div id="joyu002" class="off">
 <img src="images/actress/sod/nameless_l.jpg" class="textLeft">
 <h3 class="bigName">架空同名</h3><h4 class="smallName">Nameless</h4></div>"""

JAE_2017_LIST_PAGE = """<ul>
 <li><a href="actress/04.html"><img src="images/actress/girls/04.jpg" alt="波多野結衣"></a></li>
 <li><a href="actress/sonoda_mion.html"><img src="images/actress/girls/03.jpg"></a></li>
 <li><a href="actress/04.html">重复</a></li>
 <li><a href="../jae2015/actress.html">上一届</a></li></ul>"""

JAE_2017_PAGE = """<div id="girls_area" class="other_area">
 <div class="girls_btn"><a href="sonoda_mion.html" class="btn_left pop">◀◀</a></div>
 <div class="girls_area_left">
  <a class="group pop" href="../images/actress/girls/04l.jpg" title="波多野結衣">
   <img src="../images/actress/girls/04m.jpg" alt="波多野結衣"></a></div>
 <div class="girls_area_right">
  <div class="name_area"><h2>波多野結衣</h2><p>Yui Hatano</p></div>
  <ul><li><a href="https://twitter.com/hatano_yui" target="_blank" class="pop">
    <img src="../images/actress/icon01.png" alt="twitter"></a></li>
   <!-- <li><a href="http://blog.livedoor.jp/sonoda_mion/" target="_blank" class="pop"></a></li>
   <li><a href="https://www.instagram.com/kamisaki_shiori/" class="pop"></a></li> -->
  </ul>
  <table><tr><th>おすすめの作品タイトル</th>
   <td><a href="http://www.max-a.co.jp/item.php?item_id=ef33" class="pop">作品</a></td>
  </tr></table></div></div>"""

# 按 2026-09-04 实测的 javdb.com 复刻。搜索结果卡片的 `title` 一栏列出这个人在站上的
# 全部写法；资料页的社媒按钮在 `section-addition` 那一块，别处的站外链接每页都一样。
JAVDB_SEARCH_PAGE = """<div id="actors" class="actors">
 <div class="box actor-box"><a href="/actors/Kn01" title="瀬戸環奈, 瀨戶環奈, 濑户环奈">
  <figure class="image"><img class="avatar" src="https://c0.jdbstatic.com/avatars/kn/Kn01.jpg" />
  </figure><strong>瀬戸環奈</strong></a></div>
 <div class="box actor-box"><a href="/actors/Kn02" title="瀬戸環奈">
  <strong>瀬戸環奈</strong></a></div>
 <div class="box actor-box"><a href="/actors/Zz99" title="別人さん">
  <strong>別人さん</strong></a></div>
</div>"""

JAVDB_EMPTY_SEARCH = """<div id="actors" class="actors"></div>"""

JAVDB_ACTOR_PAGE = """<html><head><title> 瀬戸環奈 | JavDB 成人影片數據庫 </title></head><body>
<div class="columns is-desktop section-columns">
 <div class="column actor-avatar"><div class="image">
  <span class="avatar" style="background-image: url(https://c0.jdbstatic.com/avatars/kn/Kn01.jpg)">
  </span></div></div>
 <div class="column section-title"><h2 class="title is-4">
  <span class="actor-section-name">瀬戸環奈, 瀨戶環奈</span><br />
  <span class="section-meta">水沢のの</span><br>
  <span class="section-meta">42 部影片</span></h2></div></div>
<div class="columns"><div class="column section-addition"><div class="field has-addons">
 <p class="control"><a class="button is-info" href="/actors/Kn01/collect">收藏</a></p>
 <p class="control"><a class="button is-info" title="訪問ta的Twitter主頁"
  href="https://twitter.com/kanna_seto0510"><span>Twitter</span></a></p>
 <p class="control"><a class="button is-info" title="訪問ta的Instagrm主頁"
  href="https://instagram.com/kanna_seto0510"><span>Instagram</span></a></p>
</div></div></div>
<div class="toolbar"><a href="https://jav.app/">姊妹站</a>
 <a href="https://www.rtalabel.org">RTA</a></div></body></html>"""

JAVDB_LOGIN_PAGE = """<html><head><title> 登入 | JavDB 成人影片數據庫 </title></head>
<body><form action="/user_sessions"></form></body></html>"""

X_ALIVE = """<html><head><meta content="瀬戸環奈 (@kanna_seto0510) / X" property="og:title">
<meta property="og:image" content="https://pbs.twimg.com/profile_images/1/abc_200x200.jpg"></head></html>"""
X_SUSPENDED = """<html><head><meta property="og:title" content="X"><meta property="og:image"
 content="https://abs.twimg.com/responsive-web/client-web/icon-ios.png"></head></html>"""
X_SHELL = "<html><head><title>X</title></head><body><div id='react-root'></div></body></html>"
#: 照 2026-09-04 实测的 `@yu_shinoda` 复刻：活着的账号，显示名自己写明是应援号。
X_FAN = """<html><head><meta content="篠田ゆう様💝応援アカウント (@yu_shinoda) on X" property="og:title">
<meta property="og:image" content="https://pbs.twimg.com/profile_images/9/fan_200x200.jpg"></head></html>"""


class FakeSite:
    """按 URL 查字典的取页器；模仿年龄门：POST 过之前 bstar 页面一律给门。"""

    def __init__(self, pages, gated=False, HttpStatusError=RuntimeError):
        self.pages, self.gated, self.opened = pages, gated, False
        self.posts = []
        self.fetched = self.cached = 0
        self.error = HttpStatusError

    def get(self, url, refresh=False):
        if self.gated and "bstar-pro.com" in url and not self.opened:
            return BSTAR_GATE
        if url not in self.pages:
            raise self.error(404)
        return self.pages[url]

    def request(self, method, url, body=None, headers=None):
        self.posts.append((method, url, body))
        if body and b"age_check=yes" in body:
            self.opened = True
        return ""

    def close(self):
        pass


PERFORMERS = [
    {"entity_id": 1, "name": "波多野结衣", "chain": ["波多野結衣", "波多野结衣"]},
    {"entity_id": 2, "name": "濑户环奈", "chain": ["瀬戸環奈", "濑户环奈"]},
    {"entity_id": 3, "name": "架空同名", "chain": ["架空同名"]},
    {"entity_id": 4, "name": "另一位同名", "chain": ["架空同名", "另一位同名"]},
]


class SharedJudgementTests(unittest.TestCase):
    """`peach.social_links` 是三个采集器共用的判据，行为定在这里。"""

    def test_handle_folds_case_and_skips_platform_prefixes(self):
        """`@Yua_Mikami` 和 `@yua_mikami` 是同一个人；YouTube 的 channel/ 只是路径前缀。"""
        self.assertEqual(social_links.handle("https://x.com/Yua_Mikami"), "yua_mikami")
        self.assertEqual(social_links.handle("https://www.youtube.com/channel/UCabc"), "ucabc")
        self.assertEqual(social_links.handle("https://www.youtube.com/@setokan.channel"),
                         "setokan.channel")

    def test_platform_function_pages_are_not_accounts(self):
        """`x.com/intent/follow?…` 和 `instagram.com/p/…` 谁都能挂，不是任何人的账号。"""
        self.assertEqual(social_links.handle("https://x.com/intent/follow?screen_name=a"), "")
        self.assertEqual(social_links.handle("https://www.instagram.com/p/Cxyz/"), "")

    def test_classify_labels_youtube_by_channel_name_not_by_prefix(self):
        """`@名字` 进标签；`channel/UC…` 那串是频道 ID，写进标签只是一行乱码。"""
        self.assertEqual(social_links.classify("https://www.youtube.com/@setokan.channel"),
                         ("social", "YouTube @setokan.channel"))
        self.assertEqual(social_links.classify("https://www.youtube.com/channel/UC78MKlRFJzLXiWlXcP7o1BQ"),
                         ("social", "YouTube"))
        self.assertEqual(social_links.classify("https://twitter.com/alice_710_"),
                         ("social", "X @alice_710_"))

    def test_canonical_url_only_renames_the_same_site(self):
        """twitter.com 是 x.com 的旧名，路径原样搬；别的站原样返回。"""
        self.assertEqual(social_links.canonical_url("http://twitter.com/A_b?x=1"),
                         "https://x.com/A_b?x=1")
        self.assertEqual(social_links.canonical_url("https://www.instagram.com/a/"),
                         "https://www.instagram.com/a/")

    def test_canonical_url_lowercases_the_host_but_not_the_handle(self):
        """jae 的页面上就写着 `https://Instagram.com/sakuramanatee/`。"""
        self.assertEqual(social_links.canonical_url("https://Instagram.com/sakuramanatee/"),
                         "https://instagram.com/sakuramanatee/")
        self.assertEqual(social_links.canonical_url("https://X.com/A_b"), "https://x.com/A_b")

    def test_name_key_ignores_width_spacing_and_case(self):
        self.assertEqual(social_links.name_key("Yua　Mikami"), social_links.name_key("yua mikami"))
        self.assertEqual(social_links.name_key("三上 悠亜"), social_links.name_key("三上悠亜"))

    def test_twimg_tiers_puts_the_upload_original_first(self):
        """unavatar 一律给 `_200x200`，而原图常常更大——`セレブの友` 实测 242×242。

        `_400x400` 不是「有这么大」的证据：原图更小时它返回的仍是原图，所以档位表
        必须从无后缀的原图开始往下退，不能拿后缀当尺寸。
        """
        self.assertEqual(
            social_links.twimg_tiers(
                "https://pbs.twimg.com/profile_images/562462783950700545/PL-RGriK_200x200.jpeg"),
            ["https://pbs.twimg.com/profile_images/562462783950700545/PL-RGriK.jpeg",
             "https://pbs.twimg.com/profile_images/562462783950700545/PL-RGriK_400x400.jpeg",
             "https://pbs.twimg.com/profile_images/562462783950700545/PL-RGriK_200x200.jpeg"])

    def test_twimg_tiers_strips_every_named_tier_too(self):
        self.assertEqual(
            social_links.twimg_tiers("https://pbs.twimg.com/profile_images/1/a_normal.jpg")[0],
            "https://pbs.twimg.com/profile_images/1/a.jpg")

    def test_other_hosts_are_returned_untouched(self):
        """别的 CDN 没有这套档位约定，`logo_200x200.png` 就是文件名本身。"""
        self.assertEqual(social_links.twimg_tiers("https://cdn.example.com/logo_200x200.png"),
                         ["https://cdn.example.com/logo_200x200.png"])

    def test_x_state_needs_a_profile_image_to_count_as_alive(self):
        """封停页也有 og 标签，只是 og:image 是 X 的图标不是 profile_images。

        什么 og 都没有的是 JS 壳或限流页，看不出死活，不能把限流当成封停。
        """
        self.assertEqual(social_links.x_profile_state(X_ALIVE),
                         ("alive", "瀬戸環奈 (@kanna_seto0510) / X"))
        self.assertEqual(social_links.x_profile_state(X_SUSPENDED)[0], "gone")
        self.assertEqual(social_links.x_profile_state(X_SHELL)[0], "unknown")
        self.assertEqual(social_links.x_profile_state("<p>Account suspended</p>")[0], "gone")


class LaoshiParseTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_listing_keeps_only_actress_pages_deduped_and_sorted(self):
        self.assertEqual(self.module.laoshi_listing(SITEMAP),
                         ["https://laoshi.ink/actresses/actor-001.html",
                          "https://laoshi.ink/actresses/actor-075.html"])

    def test_names_come_from_title_and_fact_table_without_placeholders(self):
        """「待补充」不是名字；标题括号里的日文名和罗马字都要拆出来。"""
        self.assertEqual(self.module.laoshi_names(LAOSHI_PAGE),
                         ["波多野结衣", "波多野結衣", "Yui Hatano"])
        self.assertEqual(self.module.laoshi_names(LAOSHI_TWO_NAMES),
                         ["西田カリナ", "Karina Nishida", "西田卡莉娜", "にしだカリナ"])

    def test_links_merge_same_as_with_anchors_and_drop_site_internal(self):
        """sameAs 与正文锚点指向同一账号时只留一条；站内与 CDN 链接不是这个人的。"""
        links = self.module.laoshi_links(LAOSHI_PAGE)
        self.assertIn("https://x.com/hatano_yui", links)
        self.assertEqual(links.count("https://x.com/hatano_yui"), 1)
        self.assertNotIn("/actresses/index.html", links)
        self.assertTrue(all("laoshi.ink" not in url for url in links))


class BstarCollectTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.pages = {self.module.BSTAR_MODELS: BSTAR_LIST,
                      "https://bstar-pro.com/model.html?mid=355": BSTAR_MODEL}

    def test_listing_pairs_each_model_page_with_its_names_once(self):
        self.assertEqual(self.module.bstar_listing(BSTAR_LIST)[0],
                         ("https://bstar-pro.com/model.html?mid=355", ["瀬戸環奈", "Kanna Seto"]))
        self.assertEqual(len(self.module.bstar_listing(BSTAR_LIST)), 2)

    def test_the_age_gate_is_passed_once_and_the_page_refetched(self):
        """models.html 先给年龄门；POST age_check=yes 之后同一会话才给列表。"""
        site = FakeSite(self.pages, gated=True, HttpStatusError=self.module.HttpStatusError)
        site_links, pages = self.module.collect_bstar(site)
        self.assertEqual([post[0] for post in site.posts], ["POST"])
        self.assertEqual({url for url in site_links},
                         {"https://x.com/bstar_pro/", "https://www.instagram.com/bstar_pro_"})
        self.assertEqual(pages[0]["names"], ["瀬戸環奈", "Kanna Seto"])
        self.assertEqual(pages[0]["official"], ("https://bstar-pro.com/model.html?mid=355", "Bstar"))

    def test_a_missing_model_page_becomes_未取得_not_a_crash(self):
        site = FakeSite(self.pages, HttpStatusError=self.module.HttpStatusError)
        _, pages = self.module.collect_bstar(site)
        self.assertTrue(pages[1]["note"].startswith("未取得：HttpStatusError: HTTP 404"), pages[1]["note"])
        self.assertEqual(pages[1]["links"], [])

    def test_the_broken_relative_footer_link_lands_on_the_own_host_and_is_dropped(self):
        links = self.module.external_links(BSTAR_MODEL, ("bstar-pro.com",), "https://bstar-pro.com/")
        self.assertNotIn("https://bstar-pro.com/https://www.instagram.com/bstar_pro_", links)
        self.assertIn("https://s1s1s1.com/actress/detail/849776", links)


class JaeCollectTests(unittest.TestCase):
    """展会名录三届：结构各不相同，注释里的东西一律不算这一页的内容。"""

    def setUp(self):
        self.module = load_module()
        self.list_2017 = self.module.JAE + self.module.JAE_2017_LIST
        # 2014 那届按五十音分页，样本只给「あ行」有人，其余页留空。
        self.pages = {self.module.JAE + path: "<ul></ul>"
                      for path in self.module.JAE_2014_LISTS}
        self.pages.update({
            self.module.JAE + self.module.JAE_2014_LISTS[0]: JAE_2014_PAGE,
            self.module.JAE + self.module.JAE_2015_LIST: JAE_2015_PAGE,
            self.list_2017: JAE_2017_LIST_PAGE,
            "http://www.jae.tokyo/jae2017/actress/04.html": JAE_2017_PAGE,
            "http://www.jae.tokyo/jae2017/actress/sonoda_mion.html": "<div></div>"})

    def entries_2014(self):
        return self.module.jae_2014_entries(
            JAE_2014_PAGE, self.module.JAE + self.module.JAE_2014_LISTS[0])

    def test_the_commented_out_template_entry_is_not_a_person(self):
        """2014 那届列表开头是一条填好数据的模板 `<li>`，整段在注释里。"""
        entries = self.entries_2014()
        self.assertEqual([entry["anchor"] for entry in entries], ["YuiHatano", "Nameless"])
        self.assertTrue(all("example.com/template" not in url
                            for entry in entries for url, _ in entry["links"]))

    def test_an_entry_pairs_both_name_spellings_with_its_own_links_and_portrait(self):
        entry = self.entries_2014()[0]
        self.assertEqual(entry["names"], ["波多野結衣", "Yui Hatano"])
        self.assertEqual(entry["links"], [("http://blog.livedoor.jp/hatano_yui/", "公式ブログ"),
                                          ("https://twitter.com/hatano_yui", "ツイッター")])
        self.assertEqual(entry["portrait"],
                         "http://www.jae.tokyo/jae2014/performer/images/Hatano_Yui.jpg")

    def test_the_placeholder_photo_is_not_a_portrait(self):
        """没交照片的位置放的是 `nophoto.jpg`，那不是这个人的像。"""
        self.assertEqual(self.entries_2014()[1]["portrait"], "")

    def test_2015_takes_links_from_the_link_row_and_not_from_the_product_table(self):
        """同一层的资料表里还有片商的商品页，那不是本人的链接。"""
        entries = self.module.jae_2015_entries(
            JAE_2015_PAGE, self.module.JAE + self.module.JAE_2015_LIST)
        self.assertEqual([entry["anchor"] for entry in entries], ["joyu001", "joyu002"])
        self.assertEqual(entries[0]["names"], ["瀬戸環奈", "Kanna Seto"])
        self.assertEqual(entries[0]["links"],
                         [("http://kanna-seto.ameblo.jp/", "オフィシャルブログ"),
                          ("https://twitter.com/kanna_seto0510", "ツイッター")])
        self.assertEqual(entries[1]["links"], [])

    def test_2017_listing_names_each_detail_page_once_and_stays_in_its_own_year(self):
        pages = self.module.jae_2017_pages(JAE_2017_LIST_PAGE, self.list_2017)
        self.assertEqual(pages, ["http://www.jae.tokyo/jae2017/actress/04.html",
                                 "http://www.jae.tokyo/jae2017/actress/sonoda_mion.html"])

    def test_2017_detail_does_not_inherit_the_neighbours_accounts_from_the_comments(self):
        """每页都是从上一位那页复制来的，上一位的账号整段留在注释里。"""
        entry = self.module.jae_2017_entry(
            JAE_2017_PAGE, "http://www.jae.tokyo/jae2017/actress/04.html")
        self.assertEqual(entry["names"], ["波多野結衣", "Yui Hatano"])
        self.assertEqual(entry["links"], [("https://twitter.com/hatano_yui", "")])
        self.assertEqual(entry["portrait"],
                         "http://www.jae.tokyo/jae2017/images/actress/girls/04l.jpg")

    def test_platform_accounts_and_own_sites_leave_the_collector_on_separate_tracks(self):
        """平台账号照常走 `links`（要按 handle 判冲突）；博客与本人官网走 `owned`。"""
        site = FakeSite(self.pages, HttpStatusError=self.module.HttpStatusError)
        site_links, pages = self.module.collect_jae(site)
        self.assertEqual(site_links, [])
        got = {page["page"]: page for page in pages if not page["note"]}
        entry = got["http://www.jae.tokyo/jae2014/performer/#YuiHatano"]
        self.assertEqual(entry["links"], ["http://blog.livedoor.jp/hatano_yui/",
                                          "https://twitter.com/hatano_yui"])
        self.assertEqual(entry["owned"], [("http://blog.livedoor.jp/hatano_yui/", "公式ブログ")])
        self.assertEqual(entry["portrait"],
                         "http://www.jae.tokyo/jae2014/performer/images/Hatano_Yui.jpg")

    def test_a_missing_year_only_leaves_未取得_and_the_others_still_run(self):
        pages = dict(self.pages)
        del pages[self.module.JAE + self.module.JAE_2015_LIST]
        site = FakeSite(pages, HttpStatusError=self.module.HttpStatusError)
        _, collected = self.module.collect_jae(site)
        failures = [page for page in collected if page["note"]]
        self.assertEqual([page["page"] for page in failures],
                         [self.module.JAE + self.module.JAE_2015_LIST])
        self.assertTrue(failures[0]["note"].startswith("未取得：HttpStatusError: HTTP 404"))
        self.assertTrue(any(page["page"].endswith("#YuiHatano") for page in collected))


class JavdbCollectTests(unittest.TestCase):
    """入口是账本里的名字：搜索卡片的 `title` 决定要不要点进去。"""

    def setUp(self):
        self.module = load_module()
        self.kanna = PERFORMERS[1]
        self.yui = PERFORMERS[0]
        self.pages = {
            self.search("瀬戸環奈"): JAVDB_SEARCH_PAGE,
            "https://javdb.com/actors/Kn01": JAVDB_ACTOR_PAGE,
            "https://javdb.com/actors/Kn02": JAVDB_ACTOR_PAGE,
        }

    def search(self, name):
        return self.module.JAVDB_SEARCH.format(quote(name))

    def collect(self, pages, performers):
        site = FakeSite(pages, HttpStatusError=self.module.HttpStatusError)
        return self.module.collect_javdb(site, 0, performers)[1]

    def test_only_the_cards_whose_names_match_the_ledger_are_opened(self):
        """搜「瀬戸環奈」也会回别人。卡片的 title 就够判，对不上的不点进去。"""
        collected = self.collect(self.pages, [self.kanna])
        self.assertEqual([page["page"] for page in collected],
                         ["https://javdb.com/actors/Kn01", "https://javdb.com/actors/Kn02"])

    def test_the_actor_page_gives_every_name_the_two_buttons_and_the_avatar(self):
        page = self.collect(self.pages, [self.kanna])[0]
        self.assertEqual(page["names"], ["瀬戸環奈", "瀨戶環奈", "水沢のの"],
                         "「42 部影片」和名字同一个 class，它不是名字")
        self.assertEqual(page["links"], ["https://twitter.com/kanna_seto0510",
                                         "https://instagram.com/kanna_seto0510"],
                         "收藏按钮是站内相对路径，姊妹站与 RTA 在这一块之外")
        self.assertEqual(page["portrait"], "https://c0.jdbstatic.com/avatars/kn/Kn01.jpg")

    def test_the_login_wall_is_未取得_and_still_says_who_it_was_about(self):
        """同一位女优在站上常有两条记录，其中一条要登录才给。不注册账号。"""
        pages = dict(self.pages, **{"https://javdb.com/actors/Kn02": JAVDB_LOGIN_PAGE})
        walled = self.collect(pages, [self.kanna])[1]
        self.assertEqual(walled["note"], "javdb 这一页要登录才给，不注册账号")
        self.assertEqual(walled["names"], self.kanna["chain"],
                         "登录页上没有名字，是谁得由发起搜索的那条名字链带过来")

    def test_the_whole_name_chain_is_tried_before_giving_up(self):
        """账本规范名多是中文，站上是日文原名；哪个写法能搜到得试出来。"""
        pages = {self.search("波多野結衣"): JAVDB_EMPTY_SEARCH,
                 self.search("波多野结衣"): JAVDB_SEARCH_PAGE.replace(
                     "瀬戸環奈, 瀨戶環奈, 濑户环奈", "波多野结衣"),
                 "https://javdb.com/actors/Kn01": JAVDB_ACTOR_PAGE}
        collected = self.collect(pages, [self.yui])
        self.assertEqual([page["page"] for page in collected],
                         ["https://javdb.com/actors/Kn01"])

    def test_searched_and_absent_is_a_row_of_its_own(self):
        """「搜过、站上没有这个人」和「没搜」是两件事，不写下来下一轮还得再搜一遍。"""
        pages = {self.search(name): JAVDB_EMPTY_SEARCH for name in self.yui["chain"]}
        collected = self.collect(pages, [self.yui])
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["note"],
                         "搜过名字链的 2 个写法，javdb 上没有这个人")
        self.assertEqual(collected[0]["page"], self.search("波多野结衣"),
                         "记最后搜的那一次，别记一个没搜过的地址")

    def test_a_search_that_fails_does_not_become_absence(self):
        collected = self.collect({}, [self.yui])
        self.assertEqual(len(collected), 1)
        self.assertTrue(collected[0]["note"].startswith("未取得：HttpStatusError: HTTP 404"))
        self.assertEqual(collected[0]["names"], [])

    def limited(self, pages, status):
        """除 `pages` 里的地址外，其它一律回限速状态码。"""
        module = self.module

        class Limited(FakeSite):
            def get(self, url, refresh=False):
                if url in self.pages:
                    return self.pages[url]
                self.asked.append(url)
                raise module.HttpStatusError(status)

        site = Limited(pages, HttpStatusError=module.HttpStatusError)
        site.asked = []
        return site

    def test_a_rate_limit_ends_the_source_instead_of_grinding_on(self):
        """403 之后接着翻只会一位撞一次：`Site.request()` 对状态码不重试，那几分钟只出空行。

        2026-09-04 实测就是这样：封了之后那一轮又跑了好几分钟，一条有用的都没产出。
        """
        site = self.limited({}, 403)
        collected = self.module.collect_javdb(site, 0, PERFORMERS)[1]
        self.assertEqual(len(collected), 1, "只留撞上限速的那一行，后面几位不再发请求")
        self.assertEqual(site.asked, [self.search("波多野結衣")],
                         "同一位的其它名字写法也不试了，限速不是这个写法的问题")
        self.assertTrue(collected[0]["note"].startswith("未取得：HttpStatusError: HTTP 403"))

    def test_429_and_503_end_it_the_same_way(self):
        for status in (429, 503):
            with self.subTest(status=status):
                site = self.limited({}, status)
                self.assertEqual(
                    len(self.module.collect_javdb(site, 0, PERFORMERS)[1]), 1)

    def test_what_was_already_collected_survives_the_abort(self):
        """收工不是丢掉这一轮：封之前取到的页照样进判定，否则等于白跑。"""
        site = self.limited(self.pages, 403)
        collected = self.module.collect_javdb(site, 0, [self.kanna, self.yui])[1]
        self.assertEqual([page["page"] for page in collected[:2]],
                         ["https://javdb.com/actors/Kn01", "https://javdb.com/actors/Kn02"])
        self.assertEqual(len(collected), 3)

    def test_javdb_runs_at_a_quarter_of_the_rate_that_got_us_banned(self):
        """限速规律写进了常量：封之前峰值每分钟 50 页，自己按四分之一跑。"""
        floor = self.module.SOURCE_INTERVAL["javdb"]
        self.assertLessEqual(60 / floor, 50 / 4)
        self.assertGreater(floor, self.module.build_parser().get_default("interval"),
                           "`--interval` 的默认值压不过来源自己的下限")


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def page(self, source, url, names, links, official=None, note=""):
        return self.module.page_record(source, url, names, links, official=official, note=note)

    def judge(self, pages, site_links=(), existing=None, urls=None):
        return self.module.judge(pages, list(site_links), PERFORMERS, existing or {}, urls or {})

    def test_a_unique_match_yields_ok_rows_for_every_new_account_on_the_page(self):
        """同一页两个 X 账号是主号加副号，都装；事务所自己的账号减掉；非社交外链不算。"""
        page = self.page("bstar", "https://bstar-pro.com/model.html?mid=355", ["瀬戸環奈", "Kanna Seto"],
                         ["https://x.com/kanna_seto0510", "https://x.com/setokan_sub",
                          "https://www.instagram.com/setokanna_/", "https://x.com/bstar_pro/",
                          "https://s1s1s1.com/actress/detail/849776"],
                         official=("https://bstar-pro.com/model.html?mid=355", "Bstar"))
        rows, stats = self.judge([page], site_links=["https://x.com/bstar_pro/"])
        self.assertEqual([row["verdict"] for row in rows], ["ok"] * 4)
        self.assertEqual({row["label"] for row in rows},
                         {"X @kanna_seto0510", "X @setokan_sub", "Instagram @setokanna_", "Bstar"})
        self.assertTrue(all(row["entity_id"] == 2 and row["name"] == "濑户环奈" for row in rows))
        self.assertIn("对上数据库「濑户环奈」", rows[0]["evidence"])
        self.assertEqual(stats["ok"], 4)

    def test_the_same_account_in_the_old_host_shape_is_已有_not_a_duplicate(self):
        """账本存 `twitter.com/Hatano_Yui`，页面给 `x.com/hatano_yui`：同一个账号。"""
        existing = {1: {("X", "hatano_yui"): "https://twitter.com/Hatano_Yui"}}
        page = self.page("laoshi", "https://laoshi.ink/actresses/actor-001.html",
                         ["波多野结衣", "波多野結衣"], ["https://x.com/hatano_yui"])
        rows, _ = self.judge([page], existing=existing)
        self.assertEqual([row["verdict"] for row in rows], ["已有"])
        self.assertIn("twitter.com/Hatano_Yui", rows[0]["evidence"])

    def test_a_different_account_on_a_platform_the_ledger_already_has_is_a_conflict(self):
        """这是用户要的「交叉对比」：两处资料说的不是同一个账号，只报不装。"""
        existing = {1: {("X", "old_hatano"): "https://x.com/old_hatano"}}
        page = self.page("laoshi", "https://laoshi.ink/actresses/actor-001.html",
                         ["波多野結衣"], ["https://x.com/hatano_yui"])
        rows, stats = self.judge([page], existing=existing)
        self.assertEqual(rows[0]["verdict"], "conflict")
        self.assertIn("另有 https://x.com/old_hatano", rows[0]["evidence"])
        self.assertEqual(stats["conflict"], 1)

    def test_an_ok_from_one_source_makes_the_next_source_report_已有_or_conflict(self):
        """两个来源先后给同一个人：第二个来源看到第一个来源装进去的账号。"""
        existing = {}
        first = self.page("laoshi", "https://laoshi.ink/actresses/actor-001.html",
                          ["波多野結衣"], ["https://x.com/hatano_yui"])
        second = self.page("bstar", "https://bstar-pro.com/model.html?mid=1",
                           ["波多野結衣"], ["https://x.com/HATANO_YUI", "https://x.com/other"])
        rows, _ = self.judge([first, second], existing=existing)
        self.assertEqual([row["verdict"] for row in rows], ["ok", "已有", "conflict"])

    def test_a_name_matching_several_performers_is_需人工消歧_with_no_entity(self):
        page = self.page("laoshi", "https://laoshi.ink/actresses/actor-9.html",
                         ["架空同名"], ["https://x.com/someone"])
        rows, _ = self.judge([page])
        self.assertEqual(rows[0]["verdict"], "需人工消歧")
        self.assertEqual(rows[0]["entity_id"], "")
        self.assertIn("架空同名、另一位同名", rows[0]["evidence"])

    def test_pages_not_in_the_ledger_are_counted_but_not_written(self):
        page = self.page("laoshi", "https://laoshi.ink/actresses/actor-8.html",
                         ["不在库里的人"], ["https://x.com/nobody"])
        rows, stats = self.judge([page])
        self.assertEqual(rows, [])
        self.assertEqual(stats["页面不在账本"], 1)

    def test_a_matched_page_without_social_links_and_a_failed_page_both_leave_a_trace(self):
        """命中却没链接要写下来——否则看不出这个人在来源里是有页没号，还是根本没页。"""
        empty = self.page("laoshi", "https://laoshi.ink/actresses/actor-001.html", ["波多野結衣"], [])
        broken = self.page("laoshi", "https://laoshi.ink/actresses/actor-002.html", [], [],
                           note="未取得：HttpStatusError: HTTP 503")
        rows, stats = self.judge([empty, broken])
        self.assertEqual([(row["verdict"], row["entity_id"]) for row in rows],
                         [("未取得", 1), ("未取得", "")])
        self.assertEqual((stats["命中但无社媒"], stats["页面未取得"]), (1, 1))

    def test_a_note_that_carries_a_name_chain_says_who_it_was_about(self):
        """按名字进的来源搜不到人也是结论；只写一个 URL 的话看不出这一行是谁的。"""
        absent = self.page("javdb", "https://javdb.com/search?f=actor&q=x",
                           ["波多野結衣", "波多野结衣"], [],
                           note="搜过名字链的 2 个写法，javdb 上没有这个人")
        ambiguous = self.page("javdb", "https://javdb.com/search?f=actor&q=y", ["架空同名"], [],
                              note="搜过名字链的 1 个写法，javdb 上没有这个人")
        rows, _ = self.judge([absent, ambiguous])
        self.assertEqual([(row["verdict"], row["entity_id"], row["name"]) for row in rows],
                         [("未取得", 1, "波多野结衣"), ("未取得", "", "")],
                         "名字对上不止一位时不认领，和链接一样的判据")

    def test_the_official_page_is_已有_when_the_ledger_already_holds_it(self):
        url = "https://bstar-pro.com/model.html?mid=355"
        page = self.page("bstar", url, ["瀬戸環奈"], [], official=(url, "Bstar"))
        rows, _ = self.judge([page], urls={2: {url}})
        self.assertEqual([(row["link_kind"], row["verdict"]) for row in rows], [("official", "已有")])

    def test_an_own_site_is_judged_by_url_and_labelled_like_the_rest_of_the_ledger(self):
        """博客与本人官网没有 handle 可比，只按 URL 判重；标签统一成账本里那批写法。"""
        blog = "http://blog.livedoor.jp/hatano_yui/"
        home = "http://hatano-yui.jp/"
        page = self.module.page_record(
            "jae", "http://www.jae.tokyo/jae2014/performer/#YuiHatano", ["波多野結衣"], [],
            owned=[(blog, "公式ブログ"), (home, "")])
        rows, stats = self.judge([page])
        self.assertEqual([(row["link_kind"], row["label"], row["verdict"]) for row in rows],
                         [("social", "博客", "ok"), ("official", "官方网站", "ok")])
        self.assertIn("资料页写明的本人链接「公式ブログ」", rows[0]["evidence"])
        self.assertEqual(stats["ok"], 2)

    def test_an_own_site_the_ledger_already_holds_is_已有(self):
        blog = "http://blog.livedoor.jp/hatano_yui/"
        page = self.module.page_record("jae", "p", ["波多野結衣"], [], owned=[(blog, "公式ブログ")])
        rows, _ = self.judge([page], urls={1: {blog}})
        self.assertEqual([row["verdict"] for row in rows], ["已有"])


class OwnedLinkTests(unittest.TestCase):
    """资料页写明的本人链接：类型与标签。"""

    def setUp(self):
        self.module = load_module()

    def test_the_words_on_the_page_override_the_host_for_blogs(self):
        """实测三例：`alicejapan.co.jp` 的子域、`plaza.rakuten.co.jp`、`takasyo.blog.jp`
        都是女优博客，主机名却都不在 BLOG_HOSTS 里。页面上写着「公式ブログ」。"""
        for url in ("http://www.me-nana.alicejapan.co.jp/",
                    "http://plaza.rakuten.co.jp/rinsakuragi/",
                    "http://takasyo.blog.jp/"):
            self.assertEqual(self.module.owned_link(url, "公式ブログ"), ("social", "博客"))

    def test_a_generic_line_falls_back_to_the_ledger_wording(self):
        for shown in ("公式ブログ", "オフィシャルブログ", "ブログ", "Official Blog"):
            self.assertEqual(self.module.owned_link("http://ameblo.jp/x/", shown)[1], "博客")

    def test_a_blog_that_has_a_name_keeps_it(self):
        self.assertEqual(self.module.owned_link("http://a.jp/", "公式ブログ「旬の果実」"),
                         ("social", "博客 旬の果実"))
        self.assertEqual(self.module.owned_link("http://a.jp/", "ブログ 彩りの散歩道"),
                         ("social", "博客 彩りの散歩道"))

    def test_a_site_that_is_not_a_blog_keeps_classify_result(self):
        self.assertEqual(self.module.owned_link("https://eikawa-noa.com/", ""),
                         ("official", "官方网站"))


class PortraitTests(unittest.TestCase):
    """名录页带的人像走头像候选表，判据与链接同一套。"""

    def setUp(self):
        self.module = load_module()

    def rows(self, pages):
        return self.module.portrait_rows(pages, PERFORMERS)

    def test_a_unique_match_becomes_a_candidate_row(self):
        page = self.module.page_record("jae", "p#YuiHatano", ["波多野結衣", "Yui Hatano"], [],
                                       portrait="http://www.jae.tokyo/x.jpg")
        self.assertEqual(self.rows([page]), [{
            "entity_id": "1", "kind": "performer", "name": "波多野结衣",
            "matched_name": "波多野結衣", "portrait_url": "http://www.jae.tokyo/x.jpg",
            "source": "jae", "page": "p#YuiHatano", "verdict": "命中"}])

    def test_a_name_matching_several_performers_yields_no_portrait(self):
        """头像装错人和链接装错人是同一个错误，判据不能两套。"""
        page = self.module.page_record("jae", "p", ["架空同名"], [],
                                       portrait="http://www.jae.tokyo/x.jpg")
        self.assertEqual(self.rows([page]), [])

    def test_a_page_without_a_portrait_or_a_failed_page_yields_nothing(self):
        blank = self.module.page_record("jae", "p", ["波多野結衣"], [])
        broken = self.module.page_record("jae", "q", [], [], portrait="http://x/y.jpg",
                                         note="未取得：HTTP 404")
        self.assertEqual(self.rows([blank, broken]), [])


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def rows(self):
        return [self.module.row(entity_id=2, link_kind="social", url="https://x.com/kanna_seto0510",
                                verdict="ok"),
                self.module.row(entity_id=2, link_kind="social", url="https://x.com/gone_one",
                                verdict="ok"),
                self.module.row(entity_id=2, link_kind="social", url="https://x.com/missing",
                                verdict="conflict", evidence="e"),
                self.module.row(entity_id=2, link_kind="social",
                                url="https://www.instagram.com/setokanna_/", verdict="ok"),
                self.module.row(entity_id=2, link_kind="official", url="https://bstar-pro.com/m",
                                verdict="ok"),
                self.module.row(entity_id=1, link_kind="social", url="https://x.com/x", verdict="已有")]

    def test_only_live_or_unverifiable_ok_rows_enter_the_install_queue(self):
        """来源可能是旧数据：封停的 X 账号不能装；Instagram 登出页看不出死活，标未验照装。

        实测不存在的账号登出页是 200 的 JS 壳，没有任何 og；只有对照账号同一时刻有 og，
        才能说它是真没了。
        """
        site = FakeSite({"https://x.com/kanna_seto0510": X_ALIVE, "https://x.com/gone_one": X_SHELL,
                         "https://x.com/old_hatano": X_SUSPENDED, "https://x.com/X": X_ALIVE},
                        HttpStatusError=self.module.HttpStatusError)
        rows = self.rows()
        existing = {2: {("X", "old_hatano"): "https://x.com/old_hatano"}}
        stats = self.module.probe_rows(rows, existing, site)
        self.assertEqual([row["alive"] for row in rows], ["活", "疑似失效", "疑似失效", "未验", "", ""])
        self.assertIn("X 显示名「瀬戸環奈 (@kanna_seto0510) / X」", rows[0]["evidence"])
        self.assertIn("X 验活：登出页无资料，对照账号正常", rows[1]["evidence"])
        self.assertIn("数据库旧号 https://x.com/old_hatano：疑似失效", rows[2]["evidence"])
        self.assertEqual(dict(stats), {"活": 1, "疑似失效": 2})
        queue = self.module.installable(rows)
        self.assertEqual([row["url"] for row in queue],
                         ["https://x.com/kanna_seto0510", "https://www.instagram.com/setokanna_/",
                          "https://bstar-pro.com/m"])

    def test_a_shell_page_is_未取得_when_the_control_account_is_also_a_shell(self):
        """对照账号也没有 og 说明是我们被限流了，不能把限流记成账号封停。"""
        site = FakeSite({"https://x.com/gone_one": X_SHELL, "https://x.com/X": X_SHELL},
                        HttpStatusError=self.module.HttpStatusError)
        rows = [self.module.row(link_kind="social", url="https://x.com/gone_one", verdict="ok")]
        self.module.probe_rows(rows, {}, site)
        self.assertEqual(rows[0]["alive"], "未取得")
        self.assertIn("限流", rows[0]["evidence"])
        self.assertEqual(len(self.module.installable(rows)), 1)

    def test_a_404_is_gone_and_a_transport_error_is_unknown(self):
        """404 是账号不存在；连不上是我们的问题，不能记在账号头上。"""
        class Boom(FakeSite):
            def get(self, url, refresh=False):
                if "boom" in url:
                    raise OSError("reset")
                return super().get(url, refresh)
        site = Boom({}, HttpStatusError=self.module.HttpStatusError)
        rows = [self.module.row(link_kind="social", url="https://x.com/missing", verdict="ok"),
                self.module.row(link_kind="social", url="https://x.com/boom", verdict="ok")]
        self.module.probe_rows(rows, {}, site)
        self.assertEqual([row["alive"] for row in rows], ["疑似失效", "未取得"])
        self.assertEqual(len(self.module.installable(rows)), 1)

    def test_a_support_account_is_alive_but_not_this_person(self):
        """`応援垢`／`応援アカウント` 是粉丝开的号，名录页把它当本人账号挂着。

        验活会说「活」——这个账号确实活着，只是不是这个人的。证据里标一句不够：
        `installable()` 只看 verdict 和 alive，标注留在证据里照样会装进账本。实测
        jae 8 条、javdb 3 条这样的链接被当本人账号装了进去。
        """
        site = FakeSite({"https://x.com/yu_shinoda": X_FAN, "https://x.com/X": X_ALIVE},
                        HttpStatusError=self.module.HttpStatusError)
        rows = [self.module.row(entity_id=2, link_kind="social",
                                url="https://x.com/yu_shinoda", verdict="ok")]
        stats = self.module.probe_rows(rows, {}, site)
        self.assertEqual(rows[0]["verdict"], "应援账号")
        self.assertEqual(rows[0]["alive"], "活")
        self.assertIn("応援アカウント", rows[0]["evidence"])
        self.assertEqual(dict(stats), {"活": 1, "应援账号": 1})
        self.assertEqual(self.module.installable(rows), [])

    def test_without_a_probe_site_every_social_row_is_marked_unverified(self):
        rows = self.rows()
        self.module.probe_rows(rows, {}, None)
        self.assertEqual([row["alive"] for row in rows], ["未验", "未验", "未验", "未验", "", ""])


class CacheTests(unittest.TestCase):
    def test_a_cached_page_is_served_without_touching_the_network(self):
        """规则改一行就得重比 686 页；没有缓存就是一次完整重抓。"""
        module = load_module()
        calls = []

        class Transport:
            def __call__(self, request, timeout, max_bytes):
                calls.append(request.url)
                return type("R", (), {"status": 200, "body": b"<p>hi</p>", "url": request.url})()

            def close(self):
                pass

        root = Path(tempfile.mkdtemp()).resolve()
        site = module.Site(root, 0, 5, transport=Transport())
        self.assertEqual(site.get("https://laoshi.ink/a"), "<p>hi</p>")
        self.assertEqual(site.get("https://laoshi.ink/a"), "<p>hi</p>")
        self.assertEqual((len(calls), site.fetched, site.cached), (1, 1, 1))
        site.get("https://laoshi.ink/a", refresh=True)
        self.assertEqual(len(calls), 2)

    def flaky_site(self, failures, status=200):
        """前 `failures` 次抛传输错误，之后成功。"""
        module = load_module()
        calls = []

        class Transport:
            def __call__(self, request, timeout, max_bytes):
                calls.append(request.url)
                if len(calls) <= failures:
                    raise OSError("[SSL: UNEXPECTED_EOF_WHILE_READING]")
                return type("R", (), {"status": status, "body": b"<p>hi</p>",
                                      "url": request.url})()

            def close(self):
                pass

        root = Path(tempfile.mkdtemp()).resolve()
        return module, calls, module.Site(root, 0, 5, transport=Transport(), backoff=0)

    def test_a_transport_blip_is_retried_instead_of_killing_the_batch(self):
        """经代理取 javdatabase 约三次里有一次 TLS `UNEXPECTED_EOF`，重试即成。"""
        _, calls, site = self.flaky_site(1)
        self.assertEqual(site.get("https://www.javdatabase.com/movies/cemd-517/"), "<p>hi</p>")
        self.assertEqual((len(calls), site.retried, site.fetched), (2, 1, 1))

    def test_retries_are_bounded_and_the_last_error_surfaces(self):
        _, calls, site = self.flaky_site(9)
        with self.assertRaises(OSError):
            site.get("https://www.javdatabase.com/movies/cemd-517/")
        self.assertEqual((len(calls), site.retried), (3, 2))

    def test_an_http_status_is_not_retried(self):
        """404 重试三次仍是 404，白花三倍流量。"""
        module, calls, site = self.flaky_site(0, status=404)
        with self.assertRaises(module.HttpStatusError):
            site.get("https://www.javdatabase.com/movies/300mium-1198/")
        self.assertEqual((len(calls), site.retried), (1, 0))


if __name__ == "__main__":
    unittest.main()
