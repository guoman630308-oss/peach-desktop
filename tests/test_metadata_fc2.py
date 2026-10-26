"""FC2 五站：发行方商品页、fc2cmadb 镜像页、FC2PPV-DB、JAVten 与 JavArchive 转载页的取页与解析。"""
import json
import unittest
import urllib.parse

from peach.http import HttpResponse
from peach.sources import FailureReason, Page, Session, SourceFailure
from peach.sources.fc2 import FC2, STUDIO, UNRECOGNISED, Fc2Source, canonical_code, runtime_minutes, video_id
from peach.sources.fc2cmadb import COMPONENT as MIRROR_COMPONENT
from peach.sources.fc2cmadb import FC2CMADB, Fc2cmadbSource, parse_actresses, partial_headers
from peach.sources.fc2ppvdb import FC2PPVDB, Fc2ppvdbSource, japanese_date
from peach.sources.javarchive import JAVARCHIVE, JavArchiveSource, links
from peach.sources import javten as javten_module
from peach.sources.javten import JAVTEN, JavtenSource

CHALLENGE = ('<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title></head>'
             '<body><div id="challenge-body-text">Verifying you are human.</div></body></html>')

#: 站上那份前端资源的指纹，2026-09-22 实测形态。
MIRROR_VERSION = "fcb3b524d4c7f8f3d2c38e437b35b7a9"

COVER = "https://storage92000.contents.fc2.com/file/261/26076760/1711813737.76.jpg"
TITLE = "みお(19)可愛い巨乳JDの初アナル貫通動画"


def shop_page(video="4364209", title=TITLE, image=COVER, slug="otonakamenz",
              seller="大人仮面Z", sold="2024/03/31", runtime="41:50",
              tags=("おっぱい", "お尻", "素人")):
    """商品页里解析用得上的那几块，标记与站上一致。"""
    product = {"@type": "Product", "@id": f"article:{video}", "sku": video, "name": title,
               "description": "本編と別アングルの2本立て。",
               "image": {"url": image, "@type": "ImageObject"},
               "brand": {"name": None, "url": f"https://adult.contents.fc2.com/users/{slug}/",
                         "@type": "Brand"}}
    links = "".join(f'<a class="tag tagTag" href="/search/?tag=x">{tag}</a>' for tag in tags)
    return (
        '<script type="application/ld+json">{"@type": "BreadcrumbList"}</script>'
        f'<script type="application/ld+json">{json.dumps(product, ensure_ascii=False)}</script>'
        f'<div class="items_article_headerInfo"><h3>{title}</h3>'
        f'<a href="https://adult.contents.fc2.com/users/{slug}/">{seller}</a></div>'
        f'<p class="items_article_info">{runtime}</p>'
        f'<div class="items_article_Releasedate"><p>販売日 : {sold}</p></div>'
        f'<section class="items_article_TagArea"><h3>商品タグ</h3>{links}</section>'
    )


def gone_page():
    """下架后站上仍回 200，只是正文里那份 Product 不见了。"""
    return ('<script type="application/ld+json">{"@type": "BreadcrumbList"}</script>'
            '<div class="items_notfound"><h3>見つかりませんでした</h3></div>')


ARCHIVE_LINK = "/926949-FC2-PPV-4137487-Gカップ・脅威-マジ凄いです！脅威のパイパーGカップグラマラス!-pn.html"
ARCHIVE_TITLE = "Gカップ・脅威 マジ凄いです！脅威のパイパーGカップグラマラス!"
ARCHIVE_PICTURES = "https://img.javstore.net/images/2023/12/26/"


def search_page(video="4137487", picture=True, reposted=False):
    """搜索结果。侧栏的本周热门与归档菜单用的是同一种地址形状，所以它们也在这一页里。

    第二条那部没有图，只有标题链接——站上实测一半的结果是这样（`1863914`、`2110084`）。
    `reposted` 是同一个商品的第二条：不同转存者各发一次，文章号、番号写法和标题都不同。
    """
    heading = f"FC2-PPV-{video} {ARCHIVE_TITLE}"
    thumb = (f'<img src="{ARCHIVE_PICTURES}{video}ps.jpg" alt="{heading}" loading="lazy">'
             if picture else "")
    repost = (f'<li><a href="/800025-fc2ppv-{video}-離婚の後遺症で性欲が止まらない変態女-pn.html" '
              f'title="FC2PPV {video} 離婚の後遺症">转存的那一条</a></li>' if reposted else "")
    return (
        '<ul><li><a href="/1000-featured-articles-cn.html">Featured</a></li>'
        '<li><a href="/4-av-censored-cn.html">AV Censored</a></li></ul>'
        '<div class="news_1n"><ul><li>'
        f'<a href="/926949-FC2-PPV-{video}-Gカップ・脅威-マジ凄いです！脅威のパイパーGカップグラマラス!-pn.html" '
        f'title="{heading}">{thumb}</a>'
        '</li><li>'
        '<a href="/735702-fc2-ppv-1863914-10代美少女、圧倒的透明感のしほちゃん。-pn.html" '
        'title="FC2 PPV 1863914 10代美少女、圧倒的透明感のしほちゃん。">另一部</a>'
        f"</li>{repost}</ul></div>"
    )


def archive_page(video="4137487", title=None, large="{root}{video}pl.jpg",
                 small="{root}{video}ps.jpg", tags="ハメ撮り｜素人｜爆乳", release="2023/11/21",
                 runtime="50:03"):
    """作品页。封面在两个图位上，标签、日期、时长在正文那块资料里，转存者可填可不填。

    `<head>` 里那条 `description` 照站上的样子写：它把同一段话截断成 `…｜S級...` 塞进
    `content`，整页搜「标签：」会先命中它。余下几段是转载来的网盘链接，一概不取。
    """
    heading = title if title is not None else f"FC2-PPV-{video} {ARCHIVE_TITLE}"
    link = ARCHIVE_LINK if video == "4137487" else f"/926949-FC2-PPV-{video}-x-pn.html"
    slots = []
    if large:
        slots.append('<div class="fisrst_sc">'
                     f'<img src="{large.format(root=ARCHIVE_PICTURES, video=video)}" '
                     f'alt="{heading}" /></div>')
    if small:
        slots.append(f'<img itemprop="image" src="{small.format(root=ARCHIVE_PICTURES, video=video)}"'
                     f' alt="{heading}">')
    rows = "".join(f"{label}：{value} <br class='a5555' />" for label, value in
                   (("标签", tags), ("日期", release), ("时长", runtime)) if value)
    return (
        '<head><meta name="description" content="商品名称：FC2-PPV-'
        f'{video} {ARCHIVE_TITLE} 标签：ハメ撮り｜素人｜S級..." /></head>'
        f'<div class="menudd"><h1><a href="{link}" title="{heading}">{heading}</a></h1></div>'
        f'<div class="news"><div class="first_des">{heading}</div>'
        + "".join(slots)
        + f"商品名称：{heading} <br class='a5555' />{rows}"
        # 多帧拼成的长条预览，正文里标着 Preview，不是封面。
        f'Original version: <a href="{ARCHIVE_PICTURES}ARCHIVE-FC2PPV-{video}_s.jpg">CLICK HERE!</a>'
        f'<img src="{ARCHIVE_PICTURES}fc2ppv-{video}_s.jpg" alt="{heading}" />'
        '<div class="downloads">https://rapidgator.net/file/deadbeef/x.mp4.html</div></div>'
    )


def mirror_page(video="3189161", title="【無】コスプレシリーズ", image=COVER,
                slug="rina_vlog", seller="梨奈の射精動画＠個人撮影",
                release="2023-02-19", duration="46:06", tags=("ハメ撮り", "フェラ"),
                component=MIRROR_COMPONENT, version=MIRROR_VERSION):
    """fc2cmadb 是 Laravel + Inertia，整棵 props 树放在一个 script 里，正文是空壳。"""
    page = {"component": component, "version": version, "url": f"/articles/{video}",
            "props": {"appName": "FC2CMADB", "auth": {"user": None},
                      "article": {"id": 364910, "video_id": int(video), "title": title,
                                  "release_date": release, "duration": duration,
                                  "image_url": image,
                                  "writer": {"id": 1818, "slug": slug, "name": seller},
                                  "tags": [{"name": tag} for tag in tags]}}}
    return (f'<script data-page="app" type="application/json">'
            f'{json.dumps(page, ensure_ascii=False)}</script><div id="app"></div>')


def mirror_actresses(*named):
    """点名 `actresses` 那一跳回来的东西：整份 Inertia 响应，props 里只剩要的那一栏。"""
    listed = [{"id": 3667 + at, "name": name, "description": None,
               "alias_name": "ののみやすず 逢坂りの きたのあや", "redirect_url": None,
               "pivot": {"article_id": 521612, "actress_id": 3667 + at}}
              for at, name in enumerate(named)]
    return json.dumps({"component": MIRROR_COMPONENT, "version": MIRROR_VERSION,
                       "url": "/articles/3189161",
                       "props": {"errors": {}, "actresses": listed}}, ensure_ascii=False)


def page(html, url="https://example.test/"):
    """站上回的是字节，解析器按 UTF-8 读。"""
    return Page(url, html.encode() if isinstance(html, str) else html)


def article(html, code):
    return Fc2Source().parse(page(html), code).payload()


def mirror(html, code):
    return Fc2cmadbSource().parse(page(html), code).payload()


def archive(html, code):
    return JavArchiveSource().parse(page(html), code).payload()


def serve(pages):
    """按地址回页面的假传输。键是解码后的地址；值可以是按请求头挑页面的函数。缺的回 404。"""
    calls = []

    def call(request, timeout, limit):
        url = urllib.parse.unquote(request.url)
        calls.append((url, request.headers.get("Referer"), limit, request.headers.get("X-Inertia-Partial-Data")))
        body = pages.get(url)
        if callable(body):
            body = body(request.headers)
        if isinstance(body, str):
            body = body.encode()
        return HttpResponse(200 if body is not None else 404, {}, body or b"", request.url)

    call.calls = calls
    return call


class Fc2CodeTests(unittest.TestCase):
    def test_a_ledger_code_becomes_the_shop_number_in_every_writing_it_wears(self):
        for code in ("FC2-PPV-4364209", "FC2PPV-4364209", "fc2_ppv_4364209",
                     "FC2 PPV 4364209", "FC2-4364209"):
            self.assertEqual(video_id(code), "4364209", code)
        self.assertEqual(canonical_code("4364209"), "FC2-PPV-4364209")
        self.assertEqual(Fc2Source().article_url("FC2PPV-4364209"),
                         "https://adult.contents.fc2.com/article/4364209/")
        self.assertEqual(Fc2cmadbSource().article_url("FC2PPV-4364209"), "https://fc2cmadb.com/articles/4364209")

    def test_a_collection_part_asks_nowhere(self):
        # `FC2-PPV-3312576-1` 是 21 段合集里的一段，两个站都只有整份合集那一页。
        # 认出商品号就意味着把合集封面套给每一段，屏幕上是 21 个内容顶着同一张图。
        for code in ("FC2-PPV-3312576-1", "FC2-PPV-3312576_4", "", "ABW-358"):
            self.assertEqual(video_id(code), "", code)
            self.assertEqual(Fc2Source().article_url(code), "", code)
            self.assertEqual(Fc2cmadbSource().article_url(code), "", code)
            self.assertEqual(JavArchiveSource().search_url(code), "", code)

    def test_a_code_without_a_shop_number_fails_every_site_before_any_request(self):
        for site in (Fc2Source(), Fc2cmadbSource(), JavArchiveSource()):
            transport = serve({})
            with self.subTest(site=site.config.name), self.assertRaises(SourceFailure) as caught:
                site.records("FC2-PPV-3312576-1", session=Session(transport))
            self.assertEqual((caught.exception.reason, str(caught.exception)), (FailureReason.NOT_FOUND, UNRECOGNISED))
            self.assertEqual(transport.calls, [])

    def test_both_runtime_writings_read_as_minutes(self):
        self.assertEqual(runtime_minutes("41:50"), 41.83)
        self.assertEqual(runtime_minutes("1:23:45"), 83.75)
        self.assertIsNone(runtime_minutes(""))
        self.assertIsNone(runtime_minutes("収録時間 41:50"))


class Fc2ShopPageTests(unittest.TestCase):
    def test_the_shop_page_gives_every_field_the_review_card_shows(self):
        found = article(shop_page(), "FC2-PPV-4364209")
        self.assertEqual(found["id"], "FC2-PPV-4364209")
        self.assertEqual(found["content_id"], "4364209")
        self.assertEqual(found["source_url"], "https://adult.contents.fc2.com/article/4364209/")
        self.assertEqual(found["title"], TITLE)
        self.assertEqual(found["release_date"], "2024-03-31")
        self.assertEqual(found["runtime"], 41.83)
        self.assertEqual(found["maker"], STUDIO)
        self.assertEqual(found["label"], "大人仮面Z")
        self.assertEqual(found["seller_url"], "https://adult.contents.fc2.com/users/otonakamenz/")
        self.assertEqual(found["genres"], ["おっぱい", "お尻", "素人"])
        self.assertEqual(found["cover_url"], COVER)
        self.assertEqual(found["cover_urls"], [COVER])

    def test_the_whole_payload_matches_the_snapshot_shape_key_by_key(self):
        self.assertEqual(article(shop_page(), "FC2-PPV-4364209"), {
            "id": "FC2-PPV-4364209", "content_id": "4364209",
            "source_url": "https://adult.contents.fc2.com/article/4364209/", "title": TITLE,
            "description": "本編と別アングルの2本立て。", "actresses": [], "maker": "FC2-PPV",
            "label": "大人仮面Z", "seller_url": "https://adult.contents.fc2.com/users/otonakamenz/",
            "series": "", "director": "", "release_date": "2024-03-31", "runtime": 41.83,
            "genres": ["おっぱい", "お尻", "素人"], "cover_urls": [COVER], "cover_url": COVER})

    def test_the_shop_page_names_no_performers(self):
        # 标题里那个 `みお(19)` 是卖家自己写的宣传语，没有第二处可以印证；FC2 的演员
        # 线索在 fc2cmadb 的评论区，走 `scripts/fetch_fc2_metadata.py`。
        self.assertEqual(article(shop_page(), "FC2-PPV-4364209")["actresses"], [])

    def test_another_products_page_is_not_this_ones_data(self):
        # 站上的商品号会被复用给别的投稿，不核 sku 就把另一部片的资料写到这个番号头上。
        with self.assertRaises(SourceFailure) as caught:
            article(shop_page(video="4470851"), "FC2-PPV-4364209")
        self.assertEqual((caught.exception.reason, str(caught.exception)),
                         (FailureReason.NOT_FOUND, "FC2 上没有这个商品"))

    def test_a_delisted_product_reads_as_absent_not_as_a_broken_page(self):
        with self.assertRaises(SourceFailure) as caught:
            article(gone_page(), "FC2-PPV-4364209")
        self.assertEqual((caught.exception.kind, str(caught.exception)), ("not_found", "FC2 上没有这个商品"))

    def test_a_seller_without_a_name_keeps_the_page_that_identifies_it(self):
        html = shop_page(seller="").replace(
            '<a href="https://adult.contents.fc2.com/users/otonakamenz/"></a>', "")
        found = article(html, "FC2-PPV-4364209")
        self.assertEqual(found["label"], "https://adult.contents.fc2.com/users/otonakamenz/")

    def test_the_shop_is_asked_once_on_its_own_host_with_the_fc2_page_limit(self):
        transport = serve({"https://adult.contents.fc2.com/article/4364209/": shop_page()})
        found = Fc2Source().records("FC2-PPV-4364209", session=Session(transport))
        self.assertEqual([record.title for record in found], [TITLE])
        self.assertEqual(transport.calls, [("https://adult.contents.fc2.com/article/4364209/",
                                            "https://adult.contents.fc2.com/", 2 * 1024 * 1024, None)])
        self.assertEqual(FC2.page_limit, 2 * 1024 * 1024)


class Fc2MirrorPageTests(unittest.TestCase):
    def test_the_mirror_carries_what_the_shop_has_dropped(self):
        found = mirror(mirror_page(), "FC2-PPV-3189161")
        self.assertEqual(found["id"], "FC2-PPV-3189161")
        self.assertEqual(found["source_url"], "https://fc2cmadb.com/articles/3189161")
        self.assertEqual(found["title"], "【無】コスプレシリーズ")
        self.assertEqual(found["release_date"], "2023-02-19")
        self.assertEqual(found["runtime"], 46.1)
        self.assertEqual(found["maker"], STUDIO)
        self.assertEqual(found["label"], "梨奈の射精動画＠個人撮影")
        self.assertEqual(found["seller_url"], "https://adult.contents.fc2.com/users/rina_vlog/")
        self.assertEqual(found["genres"], ["ハメ撮り", "フェラ"])

    def test_the_whole_payload_matches_the_snapshot_shape_key_by_key(self):
        self.assertEqual(mirror(mirror_page(), "FC2-PPV-3189161"), {
            "id": "FC2-PPV-3189161", "content_id": "3189161",
            "source_url": "https://fc2cmadb.com/articles/3189161", "title": "【無】コスプレシリーズ",
            "description": "", "actresses": [], "maker": "FC2-PPV", "label": "梨奈の射精動画＠個人撮影",
            "seller_url": "https://adult.contents.fc2.com/users/rina_vlog/", "series": "", "director": "",
            "release_date": "2023-02-19", "runtime": 46.1, "genres": ["ハメ撮り", "フェラ"],
            "cover_urls": [COVER], "cover_url": COVER})

    def test_the_named_women_come_from_the_second_ask_not_from_this_page(self):
        # 女优是这一页的延迟 prop：首屏那份 HTML 里一个人也没有，点名要过才有。
        self.assertEqual(mirror(mirror_page(), "FC2-PPV-3189161")["actresses"], [])
        url = "https://fc2cmadb.com/articles/3189161"
        transport = serve({url: lambda headers: (mirror_actresses("野々宮すず") if headers.get("X-Inertia")
                                                 else mirror_page())})
        found = Fc2cmadbSource().query("FC2-PPV-3189161", session=Session(transport))
        self.assertEqual(found.payload()["actresses"], [{
            "japanese_name": "野々宮すず", "external_id": "3667",
            "alias_names": ["ののみやすず", "逢坂りの", "きたのあや"]}])
        self.assertEqual(transport.calls, [(url, "https://fc2cmadb.com/", 2 * 1024 * 1024, None),
                                           (url, url, 2 * 1024 * 1024, "actresses")])

    def test_a_failed_second_ask_keeps_everything_else_the_mirror_gave(self):
        # 那一跳撞上限流是常事，其余字段是站上最全的一份，不跟着丢。
        url = "https://fc2cmadb.com/articles/3189161"
        transport = serve({url: lambda headers: None if headers.get("X-Inertia") else mirror_page()})
        found = Fc2cmadbSource().query("FC2-PPV-3189161", session=Session(transport))
        self.assertEqual((found.title, found.performers), ("【無】コスプレシリーズ", ()))
        self.assertEqual(len(transport.calls), 2)

    def test_the_second_ask_names_this_page_and_only_the_column_it_wants(self):
        headers = partial_headers(mirror_page())
        self.assertEqual(headers["X-Inertia-Version"], MIRROR_VERSION)
        self.assertEqual(headers["X-Inertia-Partial-Component"], MIRROR_COMPONENT)
        self.assertEqual(headers["X-Inertia-Partial-Data"], "actresses")

    def test_a_page_that_is_not_a_product_page_is_never_asked_a_second_time(self):
        # 站上没有的商品回的是错误页，它照样带着版本号，问下去只会白花一趟配额。
        self.assertEqual(partial_headers(mirror_page(component="Error")), {})
        self.assertEqual(partial_headers(mirror_page(version="")), {})
        self.assertEqual(partial_headers("<div id='app'></div>"), {})
        transport = serve({"https://fc2cmadb.com/articles/3189161": mirror_page(video="4364209")})
        with self.assertRaises(SourceFailure):
            Fc2cmadbSource().query("FC2-PPV-3189161", session=Session(transport))
        self.assertEqual(len(transport.calls), 1)

    def test_the_stage_names_a_woman_has_worn_are_not_more_women(self):
        # `alias_name` 那一串是同一个人的曾用名，一位女优挂着十几个。
        found = parse_actresses(mirror_actresses("野々宮すず", "ゆうか"))
        self.assertEqual([one["japanese_name"] for one in found], ["野々宮すず", "ゆうか"])
        self.assertEqual([one["external_id"] for one in found], ["3667", "3668"])
        # 放在 `alias_names` 而不是 `aliases`：后者会跟着资料页证据被直接登记成别名。
        self.assertNotIn("aliases", found[0])
        self.assertEqual(found[0]["alias_names"], ["ののみやすず", "逢坂りの", "きたのあや"])
        self.assertEqual(parse_actresses(mirror_actresses()), [])
        self.assertEqual(parse_actresses("<html>429</html>"), [])

    def test_the_mirror_cover_points_at_the_file_itself(self):
        # 镜像有时给的是缩放服务的地址。w276 只有 276 像素宽，连封面的最低宽度都过不了，
        # 而它后半截就是原件地址（实测同一个文件 2350×2352）。
        wrapped = "https://contents-thumbnail2.fc2.com/w276/" + COVER.removeprefix("https://")
        found = mirror(mirror_page(image=wrapped), "FC2-PPV-3189161")
        self.assertEqual(found["cover_url"], COVER)
        self.assertEqual(found["cover_urls"], [COVER])

    def test_the_mirrors_own_placeholder_is_not_a_cover(self):
        # 站上没有商品图的条目挂的是镜像自己那张占位件，还是个站内相对地址。当封面交
        # 下去，抓取那侧连主机都拼不出来，报回来的是一句「来源连接未取得」。
        found = mirror(mirror_page(image="/storage/images/article/no-image.jpg"), "FC2-PPV-3189161")
        self.assertEqual(found["cover_url"], "")
        self.assertEqual(found["cover_urls"], [])
        self.assertEqual(found["title"], "【無】コスプレシリーズ")

    def test_another_products_mirror_page_is_not_this_ones_data(self):
        for html in (mirror_page(video="4364209"), "<div id='app'></div>"):
            with self.subTest(html=html[:40]), self.assertRaises(SourceFailure) as caught:
                mirror(html, "FC2-PPV-3189161")
            self.assertEqual((caught.exception.reason, str(caught.exception)),
                             (FailureReason.NOT_FOUND, "FC2CMADB 上没有这个商品"))

    def test_the_two_pages_hand_back_the_same_shape(self):
        # 两处的资料落进同一条候选流水线，字段少一个就是复核卡上少一格。
        self.assertEqual(sorted(article(shop_page(), "FC2-PPV-4364209")),
                         sorted(mirror(mirror_page(), "FC2-PPV-3189161")))
        self.assertEqual((FC2.name, FC2CMADB.name), ("fc2", "fc2cmadb"))


class JavArchiveTests(unittest.TestCase):
    def test_the_search_page_gives_up_the_link_that_carries_this_shop_number(self):
        self.assertEqual(links(search_page(), "FC2-PPV-4137487"), [ARCHIVE_LINK])
        self.assertEqual(links(search_page().encode(), "fc2ppv4137487"), [ARCHIVE_LINK])

    def test_a_longer_number_that_merely_starts_the_same_is_not_this_one(self):
        # 站上 `4137487` 与 `41374870` 都有；按子串比会把后者的标题和封面安到前者头上。
        self.assertEqual(links(search_page(video="41374870"), "FC2-PPV-4137487"), [])
        self.assertEqual(links(search_page(), "FC2-PPV-9999999"), [])
        self.assertEqual(links(search_page(), "ORETD-615"), [])

    def test_one_search_page_holds_several_works_and_each_code_finds_its_own(self):
        # 一页里既有别的作品，也有侧栏的热门与归档，挑的必须是这个商品号那一条。
        self.assertTrue(links(search_page(), "FC2-PPV-1863914")[0].startswith("/735702-"))
        self.assertTrue(links(search_page(), "FC2-PPV-4137487")[0].startswith("/926949-"))

    def test_every_repost_of_the_same_work_is_handed_over(self):
        # 不同转存者各发一次，图也各存各的：`1436028` 的 `/641159-` 那条是 404，
        # `/800025-` 那条有 1280×720。先后没有质量含义，所以两条都要。
        found = links(search_page(reposted=True), "FC2-PPV-4137487")
        self.assertEqual([link.split("-")[0] for link in found], ["/926949", "/800025"])

    def test_every_repost_becomes_its_own_record_and_a_dead_one_is_skipped(self):
        search = "https://javarchive.com/search?q=FC2-PPV-4137487"
        repost = "https://javarchive.com/800025-fc2ppv-4137487-離婚の後遺症で性欲が止まらない変態女-pn.html"
        both = serve({search: search_page(reposted=True), "https://javarchive.com" + ARCHIVE_LINK: archive_page(),
                      repost: archive_page(large="{root}FC2PPV-{video}.jpg", small="")})
        found = JavArchiveSource().records("FC2-PPV-4137487", session=Session(both))
        self.assertEqual([record.cover_urls for record in found],
                         [(ARCHIVE_PICTURES + "4137487pl.jpg", ARCHIVE_PICTURES + "4137487ps.jpg"),
                          (ARCHIVE_PICTURES + "FC2PPV-4137487.jpg",)])
        self.assertEqual([call[:3] for call in both.calls],
                         [(search, "https://javarchive.com/", 2 * 1024 * 1024),
                          ("https://javarchive.com" + ARCHIVE_LINK, "https://javarchive.com/", 2 * 1024 * 1024),
                          (repost, "https://javarchive.com/", 2 * 1024 * 1024)])
        # 第一条 404、第二条对不上番号时，只剩还在的那一条。
        one = serve({search: search_page(reposted=True), repost: archive_page()})
        self.assertEqual(len(JavArchiveSource().records("FC2-PPV-4137487", session=Session(one))), 1)
        stray = serve({search: search_page(reposted=True), repost: archive_page(video="4364209")})
        with self.assertRaises(SourceFailure) as caught:
            JavArchiveSource().records("FC2-PPV-4137487", session=Session(stray))
        self.assertEqual((caught.exception.reason, str(caught.exception)),
                         (FailureReason.NOT_FOUND, "JavArchive 上没有这个商品"))

    def test_a_search_without_this_shop_number_is_absent_and_a_refusal_keeps_its_tier(self):
        search = "https://javarchive.com/search?q=FC2-PPV-4137487"
        with self.assertRaises(SourceFailure) as caught:
            JavArchiveSource().records("FC2-PPV-4137487",
                                       session=Session(serve({search: search_page(video="41374870")})))
        self.assertEqual(str(caught.exception), "JavArchive 上没有这个商品")

        def refused(request, timeout, limit):
            return HttpResponse(403, {}, b"", request.url)

        with self.assertRaises(SourceFailure) as caught:
            JavArchiveSource().records("FC2-PPV-4137487", session=Session(refused))
        self.assertEqual((caught.exception.reason, str(caught.exception)),
                         (FailureReason.AUTH_REQUIRED, "HTTP 403"))

    def test_the_archive_page_gives_the_title_without_the_code_in_front_of_it(self):
        found = archive(archive_page(), "FC2-PPV-4137487")
        self.assertEqual(found["title"], "Gカップ・脅威 マジ凄いです！脅威のパイパーGカップグラマラス!")
        self.assertEqual((found["id"], found["content_id"], found["maker"]),
                         ("FC2-PPV-4137487", "4137487", STUDIO))
        self.assertEqual(found["source_url"], "https://javarchive.com" + ARCHIVE_LINK)
        self.assertEqual(archive(archive_page(title="FC2PPV 4137487 素顔"), "FC2-PPV-4137487")["title"],
                         "素顔")

    def test_the_whole_payload_matches_the_snapshot_shape_key_by_key(self):
        self.assertEqual(archive(archive_page(), "FC2-PPV-4137487"), {
            "id": "FC2-PPV-4137487", "content_id": "4137487", "source_url": "https://javarchive.com" + ARCHIVE_LINK,
            "title": ARCHIVE_TITLE, "description": "", "actresses": [], "maker": "FC2-PPV", "label": "",
            "seller_url": "", "series": "", "director": "", "release_date": "2023-11-21", "runtime": 50.05,
            "genres": ["ハメ撮り", "素人", "爆乳"],
            "cover_urls": [ARCHIVE_PICTURES + "4137487pl.jpg", ARCHIVE_PICTURES + "4137487ps.jpg"],
            "cover_url": ARCHIVE_PICTURES + "4137487pl.jpg"})

    def test_the_cover_is_read_from_its_slot_whatever_the_transferrer_named_the_file(self):
        """封面认位置不认文件名：站上三种命名都有，按名字认的话两种一张都取不到。"""
        found = archive(archive_page(), "FC2-PPV-4137487")
        self.assertEqual(found["cover_url"], ARCHIVE_PICTURES + "4137487pl.jpg")
        self.assertEqual(found["cover_urls"], [ARCHIVE_PICTURES + "4137487pl.jpg",
                                               ARCHIVE_PICTURES + "4137487ps.jpg"])
        # 转存者自己起的名字：`FC2PPV-4030617.jpg`、`FC2PPV835964-2.jpg`，都不带 `pl`/`ps`。
        named = archive(archive_page(large="{root}FC2PPV-{video}.jpg",
                                     small="{root}FC2PPV{video}-2.jpg"), "FC2-PPV-4137487")
        self.assertEqual(named["cover_urls"], [ARCHIVE_PICTURES + "FC2PPV-4137487.jpg",
                                               ARCHIVE_PICTURES + "FC2PPV4137487-2.jpg"])
        # 只有 schema.org 那个图位时它就是封面（实测 `835964` 的页面就少了前一个）。
        self.assertEqual(archive(archive_page(large=""), "FC2-PPV-4137487")["cover_url"],
                         ARCHIVE_PICTURES + "4137487ps.jpg")
        self.assertEqual(archive(archive_page(large="", small=""), "FC2-PPV-4137487")["cover_urls"], [])

    def test_the_stitched_preview_is_never_a_cover(self):
        # `_s.jpg` 是把多帧拼成的长条（实测 1024×2000），装上去就是一格拉长的马赛克。
        found = archive(archive_page(large="", small=""), "FC2-PPV-4137487")
        self.assertEqual(found["cover_urls"], [])
        self.assertNotIn("_s.jpg", str(archive(archive_page(), "FC2-PPV-4137487")["cover_urls"]))

    def test_the_body_block_gives_up_the_tags_the_date_and_the_runtime(self):
        """转存者填了就取。2026-09-22 实测 4 部里只有 `4030617` 这块是齐的。"""
        found = archive(archive_page(), "FC2-PPV-4137487")
        self.assertEqual(found["genres"], ["ハメ撮り", "素人", "爆乳"])
        self.assertEqual(found["release_date"], "2023-11-21")
        self.assertEqual(found["runtime"], 50.05)

    def test_an_empty_block_stays_empty_instead_of_taking_the_truncated_meta_line(self):
        """`<head>` 那条 description 里有同一段话的截断版，取回来就是半截标签加一串属性。"""
        bare = archive(archive_page(tags="", release="", runtime=""), "FC2-PPV-4137487")
        self.assertEqual((bare["genres"], bare["release_date"], bare["runtime"]), ([], "", None))
        # 站上没填标签时那一行写成 `--`，当成一个标签就入了库。
        self.assertEqual(archive(archive_page(tags="--"), "FC2-PPV-4137487")["genres"], [])

    def test_another_products_archive_page_is_not_this_ones_data(self):
        for html, code, wording in ((archive_page(video="4364209"), "FC2-PPV-4137487", "JavArchive 上没有这个商品"),
                                    ("<div class='news'></div>", "FC2-PPV-4137487", "JavArchive 上没有这个商品"),
                                    (archive_page(), "ORETD-615", UNRECOGNISED)):
            with self.subTest(code=code, html=html[:40]), self.assertRaises(SourceFailure) as caught:
                archive(html, code)
            self.assertEqual((caught.exception.reason, str(caught.exception)), (FailureReason.NOT_FOUND, wording))

    def test_the_third_page_hands_back_the_same_shape_as_the_first_two(self):
        self.assertEqual(sorted(archive(archive_page(), "FC2-PPV-4137487")),
                         sorted(mirror(mirror_page(), "FC2-PPV-3189161")))
        self.assertEqual(JAVARCHIVE.name, "javarchive")
        self.assertEqual(JavArchiveSource().search_url("FC2-PPV-4137487"),
                         "https://javarchive.com/search?q=FC2-PPV-4137487")
        self.assertEqual(JavArchiveSource().search_url("ORETD-615"), "")


DB_COVER = "https://d39jz7pbpqkw9s.cloudfront.net/thumbnails/48/4898837.webp"


def db_actress(at: int) -> str:
    """站内女优页的编号：标准写法的 uuid，实测 `5fb206c2-f8a0-4447-8e6e-c33869f54ea3`。"""
    return f"5fb206c2-f8a0-4447-8e6e-{at:012d}"


def db_page(video="4898837", title="ふたりの巨乳美少女と3P。", seller="ぷにぷに製作所", slug="punipuni",
            sold="2026年5月10日", runtime="55:00", performers=("川北すずね",), leaked="流出なし",
            tags=("巨乳", "3P")):
    """fc2ppv-db.com 的作品页：Next.js 服务端渲染，「動画詳細情報」那块是嵌套的 div 与 p。

    PR 位与関連動画也链到女优页，放进来看解析只认出演女優那一块。
    """
    cast = ("".join(f'<a href="/ja/actresses/{db_actress(at)}"><span><img alt="{name}" src="/a.webp"></span>'
                    f"<span>{name}</span></a>" for at, name in enumerate(performers))
            if performers else "<p>情報がありません</p>")
    marks = "".join(f'<a href="/ja/videos?tags={tag}">{tag}</a>' for tag in tags)
    return (
        '<html><head><meta name="description" content="'
        f'FC2-PPV-{video} {title} - 販売者: {seller} / 公開日: 2026年5月9日 / 再生時間: {runtime}">'
        f'<meta property="og:image" content="{DB_COVER}"><meta property="og:image:width" content="800"></head>'
        '<body><nav><a href="/ja/actresses/pr-uuid">今日の女優</a></nav><main>'
        f"<h1>FC2-PPV-{video} {title}</h1>"
        f'<img src="{DB_COVER}" alt="">'
        "<section><h2>動画詳細情報</h2>"
        f"<div><div><p>動画ID</p></div><div><p>{video}</p></div></div>"
        f"<div><p>販売日</p><p>{sold}</p></div>"
        f"<div><span>{leaked}</span><span>モザイクあり</span></div>"
        f"<div><div><p>出演女優</p></div><div>{cast}</div></div>"
        f'<div><a href="/ja/sellers/{slug}"><span><img alt="{seller}" src="/s.webp"></span>'
        f"<span><span>販売者</span><span>{seller}</span></span></a></div>"
        f"<div><div><span>タグ</span></div><div>{marks}</div></div>"
        "</section></main>"
        '<aside><h2>関連動画</h2><a href="/ja/actresses/other-uuid"><span>別の人</span></a></aside>'
        "</body></html>"
    )


def db_missing_page():
    """站上没有的商品回的也是 200，正文是它自己的 404 页。"""
    return "<html><head><title>404</title></head><body><main><h1>404</h1><p>ページが見つかりません</p></main></body></html>"


def database(html, code):
    return Fc2ppvdbSource().parse(page(html, "https://fc2ppv-db.com/ja/videos/4898837"), code).payload()


class Fc2ppvdbTests(unittest.TestCase):
    def test_the_database_page_gives_the_women_the_seller_the_date_and_the_leak_mark(self):
        found = database(db_page(), "FC2-PPV-4898837")
        self.assertEqual(found["title"], "ふたりの巨乳美少女と3P。")
        self.assertEqual(found["actresses"], [{"japanese_name": "川北すずね", "external_id": db_actress(0)}])
        self.assertEqual((found["label"], found["seller_url"]),
                         ("ぷにぷに製作所", "https://adult.contents.fc2.com/users/punipuni/"))
        self.assertEqual((found["release_date"], found["runtime"]), ("2026-05-10", 55.0))
        self.assertEqual(found["genres"], ["巨乳", "3P"])
        self.assertEqual(found["leaked"], False)
        self.assertEqual(database(db_page(leaked="流出あり"), "FC2-PPV-4898837")["leaked"], True)
        self.assertEqual((found["id"], found["content_id"], found["maker"], found["source_url"]),
                         ("FC2-PPV-4898837", "4898837", STUDIO, "https://fc2ppv-db.com/ja/videos/4898837"))

    def test_the_thumbnail_is_not_handed_over_as_a_cover(self):
        # 站上那张是 360×360 的 CloudFront 缩略图（`og:image:width` 写 800，实测 360），封面留给存储原件那几档。
        found = database(db_page(), "FC2-PPV-4898837")
        self.assertEqual((found["cover_urls"], found["cover_url"]), ([], ""))

    def test_only_the_cast_block_names_the_women_of_this_film(self):
        # PR 位与関連動画也链到女优页，那些不是这部片的人；站上没有女优时那一栏写「情報がありません」。
        self.assertEqual(database(db_page(performers=("A", "B")), "FC2-PPV-4898837")["actresses"],
                         [{"japanese_name": "A", "external_id": db_actress(0)},
                          {"japanese_name": "B", "external_id": db_actress(1)}])
        self.assertEqual(database(db_page(performers=()), "FC2-PPV-4898837")["actresses"], [])

    def test_each_woman_carries_her_actress_page_id_once(self):
        # 同一位在栏里列两次只留一个；链接不是站内女优页的 uuid 时只交名字，不拿半截地址当编号。
        doubled = database(db_page(performers=("A", "A")), "FC2-PPV-4898837")["actresses"]
        self.assertEqual(doubled, [{"japanese_name": "A", "external_id": db_actress(0)}])
        odd = db_page(performers=("A",)).replace(f"/ja/actresses/{db_actress(0)}", "/ja/actresses/a-page")
        self.assertEqual(database(odd, "FC2-PPV-4898837")["actresses"], [{"japanese_name": "A"}])

    def test_the_sale_date_falls_back_to_the_meta_line_and_japanese_dates_read_as_iso(self):
        self.assertEqual(japanese_date("2026年9月21日"), "2026-09-21")
        self.assertEqual(japanese_date("いつか"), "")
        missing = db_page().replace("<div><p>販売日</p><p>2026年5月10日</p></div>", "")
        self.assertEqual(database(missing, "FC2-PPV-4898837")["release_date"], "2026-05-09")

    def test_a_missing_product_and_a_challenge_page_each_keep_their_tier(self):
        for html, code, reason, wording in (
                (db_missing_page(), "FC2-PPV-99999999", FailureReason.NOT_FOUND, "FC2PPV-DB 上没有这个商品"),
                (db_page(video="48988370"), "FC2-PPV-4898837", FailureReason.NOT_FOUND, "FC2PPV-DB 上没有这个商品"),
                (db_page(), "ORETD-615", FailureReason.NOT_FOUND, UNRECOGNISED),
                (CHALLENGE, "FC2-PPV-4898837", FailureReason.CLOUDFLARE_CHALLENGE,
                 "FC2PPV-DB 要求 Cloudflare 验证，请在采集设置里更新 Cookie")):
            with self.subTest(code=code, html=html[:40]), self.assertRaises(SourceFailure) as caught:
                database(html, code)
            self.assertEqual((caught.exception.reason, str(caught.exception)), (reason, wording))
        gate = page("<html><head><title>年齢確認 | FC2PPV Database</title></head><body><h1>年齢確認</h1></body></html>",
                    "https://fc2ppv-db.com/ja/age-verify?returnTo=%2Fja%2Fvideos%2F4898837")
        with self.assertRaises(SourceFailure) as caught:
            Fc2ppvdbSource().parse(gate, "FC2-PPV-4898837")
        self.assertEqual((caught.exception.reason, str(caught.exception)),
                         (FailureReason.AUTH_REQUIRED, "FC2PPV-DB 送到了年齢確認页，浏览器没有点过去"),
                         "年龄门是会话状态不是这部片没有，不落 not_found")

    def test_the_database_is_asked_once_on_its_own_host_and_hands_back_the_same_shape(self):
        url = "https://fc2ppv-db.com/ja/videos/4898837"
        pages = serve({url: db_page()})
        found = Fc2ppvdbSource().query("FC2-PPV-4898837", session=Session(pages))
        self.assertEqual(found.title, "ふたりの巨乳美少女と3P。")
        self.assertEqual([call[:3] for call in pages.calls], [(url, "https://fc2ppv-db.com/", 2 * 1024 * 1024)])
        self.assertEqual(FC2PPVDB.name, "fc2ppvdb")
        self.assertEqual(sorted(set(database(db_page(), "FC2-PPV-4898837")) - {"leaked"}),
                         sorted(mirror(mirror_page(), "FC2-PPV-3189161")))
        self.assertEqual(Fc2ppvdbSource().video_url("ORETD-615"), "")


#: 假传输按解码后的地址找页，夹具地址就写原字。
TEN_ORIGINAL = "https://javten.com/video/1234567/id4898837/ふたり"
TEN_STORAGE = "https://storage200000.contents.fc2.com/file/393/39269295/1778420440.09.png"
TEN_GALLERY = "//contents-thumbnail2.fc2.com/w1280/storage200000.contents.fc2.com/file/393/39269295/1778420440.53.png"


def ten_page(video="4898837", title="ふたりの巨乳美少女と3P。", seller="ぷにぷに製作所", runtime="55:00",
             published="2026-05-10T22:02:04+07:00", tags=("巨乳", "3P"), url=TEN_ORIGINAL, gallery=TEN_GALLERY):
    """javten.com 的作品页：番号在 `h1.fc2-id`，卖家与时长只在 description 里。"""
    marks = "".join(f'<a class="badge badge-primary" href="https://javten.com/tag/{at}/{tag}/newest">{tag}</a>'
                    for at, tag in enumerate(tags))
    picture = (f'<a data-fancybox="gallery" href="{gallery}"><img data-src="{gallery.replace("w1280", "w500")}"></a>'
               if gallery else "")
    return (
        f'<html><head><meta property="og:title" content="[FC2-PPV-{video}]{title}">'
        f'<meta name="description" content="[FC2-PPV-{video}] | {title} | By {seller} | {runtime} | Free Sample Video">'
        f'<meta property="og:url" content="{url}"><link rel="canonical" href="{url}">'
        f'<meta property="og:image" content="{TEN_STORAGE}">'
        f'<meta property="videos:published_time" content="{published}"></head>'
        f'<body><h1 class="card-title fc2-id">FC2-PPV-{video}</h1><h2 class="card-title">{title}</h2>'
        f"{picture}{marks}"
        f'<a href="https://javten.com/seller/99/{seller}">この売り手からのすべてのビデオ</a>'
        '<a href="https://javten.com/tw/video/1234567/id4898837/x">繁體</a>'
        "</body></html>"
    )


def ten_results(*videos, lang=""):
    """搜索结果页：同一部片的日文原页与几个译文版都列着，还有标题里带数字的别的片。"""
    rows = "".join(f'<a href="https://javten.com/{lang}video/{1000 + at}/id{video}/title-{video}">FC2-PPV-{video}</a>'
                   f'<a href="https://javten.com/en/video/{1000 + at}/id{video}/title-{video}">EN</a>'
                   for at, video in enumerate(videos))
    return f"<html><body>{rows}<a href='https://javten.com/video/7/id7777777/about-4898837'>别的片</a></body></html>"


def ten(html, code):
    return JavtenSource().parse(page(html, TEN_ORIGINAL), code).payload()


class JavtenTests(unittest.TestCase):
    def test_the_work_page_gives_the_japanese_title_the_seller_the_tags_and_the_storage_originals(self):
        found = ten(ten_page(), "FC2-PPV-4898837")
        self.assertEqual(found["title"], "ふたりの巨乳美少女と3P。")
        self.assertEqual((found["label"], found["seller_url"]), ("ぷにぷに製作所", ""))
        self.assertEqual((found["release_date"], found["runtime"]), ("2026-05-10", 55.0))
        self.assertEqual(found["genres"], ["巨乳", "3P"])
        self.assertEqual(found["cover_urls"], [
            TEN_STORAGE, "https://storage200000.contents.fc2.com/file/393/39269295/1778420440.53.png"])
        self.assertEqual((found["id"], found["content_id"], found["maker"], found["source_url"], found["actresses"]),
                         ("FC2-PPV-4898837", "4898837", STUDIO, TEN_ORIGINAL, []))
        self.assertEqual(ten(ten_page(gallery=""), "FC2-PPV-4898837")["cover_urls"], [TEN_STORAGE])

    def test_the_search_page_gives_up_only_this_shop_numbers_japanese_page(self):
        found = javten_module.links(ten_results("4898837", "1111111"), "FC2-PPV-4898837")
        self.assertEqual(found, ["https://javten.com/video/1000/id4898837/title-4898837"])
        # 标题里带着这个号的别的片、以及 `id48988370` 都不是它。
        self.assertEqual(javten_module.links(ten_results("48988370"), "FC2-PPV-4898837"), [])
        self.assertEqual(javten_module.links(ten_results("4898837"), "ORETD-615"), [])

    def test_a_single_hit_lands_on_the_work_page_and_a_translated_landing_fetches_the_original(self):
        search = "https://javten.com/search?kw=4898837"
        direct = serve({search: ten_page()})
        found = JavtenSource().query("FC2-PPV-4898837", session=Session(direct))
        self.assertEqual((found.title, found.source_url), ("ふたりの巨乳美少女と3P。", TEN_ORIGINAL))
        self.assertEqual([call[0] for call in direct.calls], [search])
        translated = "https://javten.com/tw/video/1234567/id4898837/ふたり"
        via_tw = serve({search: ten_page(url=translated), TEN_ORIGINAL: ten_page()})
        JavtenSource().query("FC2-PPV-4898837", session=Session(via_tw))
        self.assertEqual([call[:2] for call in via_tw.calls], [(search, "https://javten.com/"), (TEN_ORIGINAL, search)])
        listed = serve({search: ten_results("4898837"), "https://javten.com/video/1000/id4898837/title-4898837": ten_page()})
        JavtenSource().query("FC2-PPV-4898837", session=Session(listed))
        self.assertEqual(len(listed.calls), 2)

    def test_a_translated_page_is_never_read_as_the_title(self):
        # 站上的中文是机器翻译，只收日文原页。
        with self.assertRaises(SourceFailure) as caught:
            ten(ten_page(url="https://javten.com/tw/video/1234567/id4898837/x", title="兩個巨乳美少女的3P"), "FC2-PPV-4898837")
        self.assertEqual((caught.exception.reason, str(caught.exception)),
                         (FailureReason.PARSE_ERROR, "JAVten 回的是译文页，只收日文原页"))

    def test_a_missing_product_and_a_challenge_page_each_keep_their_tier(self):
        search = "https://javten.com/search?kw=4898837"
        with self.assertRaises(SourceFailure) as caught:
            JavtenSource().query("FC2-PPV-4898837", session=Session(serve({search: ten_results("1111111")})))
        self.assertEqual((caught.exception.reason, str(caught.exception)), (FailureReason.NOT_FOUND, "JAVten 上没有这个商品"))
        with self.assertRaises(SourceFailure) as caught:
            JavtenSource().query("FC2-PPV-4898837", session=Session(serve({search: CHALLENGE})))
        self.assertEqual((caught.exception.reason, caught.exception.status_code),
                         (FailureReason.CLOUDFLARE_CHALLENGE, 403))
        for html, code, wording in ((ten_page(video="48988370"), "FC2-PPV-4898837", "JAVten 上没有这个商品"),
                                    (ten_page(), "ORETD-615", UNRECOGNISED)):
            with self.subTest(code=code), self.assertRaises(SourceFailure) as caught:
                ten(html, code)
            self.assertEqual((caught.exception.reason, str(caught.exception)), (FailureReason.NOT_FOUND, wording))

    def test_the_fifth_page_hands_back_the_same_shape_as_the_others(self):
        self.assertEqual(sorted(ten(ten_page(), "FC2-PPV-4898837")), sorted(mirror(mirror_page(), "FC2-PPV-3189161")))
        self.assertEqual(JAVTEN.name, "javten")
        self.assertEqual(JavtenSource().search_url("FC2-PPV-4898837"), "https://javten.com/search?kw=4898837")
        self.assertEqual(JavtenSource().search_url("ORETD-615"), "")


if __name__ == "__main__":
    unittest.main()
