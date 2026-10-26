"""站点图标发现：把候选找齐，再按「合适」而不是只按「大」来排。

这些用例的形态全部来自 2026-09-02 对真实站点的实测，注释里写的是哪一个站逼出了这一条。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import site_icons, social_links   # noqa: E402


def urls(candidates):
    return [c.url for c in candidates]


class LinkParsingTests(unittest.TestCase):
    def test_sizes_and_relative_hrefs_are_resolved(self):
        html = ('<link rel="apple-touch-icon" sizes="180x180" href="/a/touch.png">'
                '<link rel="shortcut icon" href="favicon.ico">')
        found = site_icons.link_candidates(html, "https://example.com/")
        self.assertEqual(urls(found), ["https://example.com/a/touch.png",
                                       "https://example.com/favicon.ico"])
        self.assertEqual([c.size for c in found], [180, site_icons.DEFAULT_SIZE])

    def test_a_multi_valued_sizes_attribute_takes_the_largest(self):
        """`sizes="180x180 152x152 120x120"` 是一个文件里装多份，取最大的那份记分。"""
        html = '<link rel="apple-touch-icon" sizes="120x120 180x180" href="/t.png">'
        self.assertEqual(site_icons.link_candidates(html, "https://e.com/")[0].size, 180)

    def test_an_svg_outranks_every_bitmap_regardless_of_rel(self):
        """threads.com 的实测形态，也是这次改动的起点。

        它把 512 viewBox 的成品 SVG 声明成 `rel="icon"`，同时根目录还躺着一个
        180×180 的 `apple-touch-icon.png`。按 `rel` 分层会让位图赢，那就又回到了
        「抓到一个就不找别的了」。
        """
        html = ('<link rel="apple-touch-icon" sizes="180x180" href="/touch.png">'
                '<link rel="icon" type="image/svg+xml" href="/mark.svg">')
        ranked = site_icons.rank(site_icons.link_candidates(html, "https://t.com/"))
        self.assertEqual(ranked[0].url, "https://t.com/mark.svg")
        self.assertTrue(ranked[0].vector)

    def test_a_mask_icon_is_kept_but_ranked_last(self):
        """`rel="mask-icon"` 按规范是纯黑剪影。

        当成品图标用会得到一枚全黑方块，所以不能排前面；但它矢量、无背景、边缘干净，
        是做「品牌色圆底 + 白色字形」最好的输入，所以也不能丢。
        """
        html = ('<link rel="mask-icon" href="/pinned.svg" color="#0a0">'
                '<link rel="icon" sizes="32x32" href="/small.png">')
        ranked = site_icons.rank(site_icons.link_candidates(html, "https://e.com/"))
        self.assertEqual(urls(ranked), ["https://e.com/small.png", "https://e.com/pinned.svg"])

    def test_a_windows_tile_image_counts_as_a_designed_icon(self):
        html = '<meta name="msapplication-TileImage" content="/tile.png">'
        found = site_icons.link_candidates(html, "https://e.com/")
        self.assertEqual(urls(found), ["https://e.com/tile.png"])
        self.assertGreater(found[0].size, site_icons.MIN_DESIGNED_SIZE)

    def test_tags_without_a_recognised_rel_or_href_are_ignored(self):
        html = ('<link rel="stylesheet" href="/app.css">'
                '<link rel="icon">'
                '<link rel="preconnect" href="https://cdn.example.com">')
        self.assertEqual(site_icons.link_candidates(html, "https://e.com/"), [])


class ManifestTests(unittest.TestCase):
    def test_the_manifest_link_is_resolved(self):
        html = '<link rel="manifest" href="/site.webmanifest">'
        self.assertEqual(site_icons.manifest_url(html, "https://e.com/"),
                         "https://e.com/site.webmanifest")
        self.assertEqual(site_icons.manifest_url("<html></html>", "https://e.com/"), "")

    def test_manifest_icons_bring_in_sizes_the_html_never_declared(self):
        """PWA 站常把最大的一份只写进 manifest，HTML 里只留 apple-touch-icon。

        不读 manifest 就会白白少掉最清晰的那一枚。
        """
        payload = ('{"icons":[{"src":"/i/192.png","sizes":"192x192"},'
                   '{"src":"/i/512.png","sizes":"512x512"}]}')
        ranked = site_icons.rank(
            site_icons.manifest_candidates(payload, "https://e.com/site.webmanifest"))
        self.assertEqual(urls(ranked), ["https://e.com/i/512.png", "https://e.com/i/192.png"])

    def test_a_broken_manifest_yields_nothing_instead_of_raising(self):
        self.assertEqual(site_icons.manifest_candidates(b"<html>404</html>", "https://e.com/m"), [])
        self.assertEqual(site_icons.manifest_candidates('{"icons":"nope"}', "https://e.com/m"), [])


class RankingTests(unittest.TestCase):
    def test_a_declared_icon_beats_the_root_path_guess_at_the_same_size(self):
        """T-POWERS 是这条的证据，也是这次唯一一处回归。

        它根目录的 `/apple-touch-icon.png` 是一条带「T-POWERS」文字的横向锁定图，
        而 `<link>` 里声明的 `/assets/images/common/apple-touch-icon.png` 是紧凑标识。
        两个都是 180；按大小并列时，路径更短的字标会排到前面去。站点自己指了哪一个就听
        哪一个，根路径只是我们的猜测。
        """
        declared = site_icons.link_candidates(
            '<link rel="apple-touch-icon" sizes="180x180" '
            'href="/assets/images/common/apple-touch-icon.png">', "https://t.co/")
        ranked = site_icons.rank(declared + site_icons.conventional_candidates("https://t.co/"))
        self.assertEqual(ranked[0].url, "https://t.co/assets/images/common/apple-touch-icon.png")

    def test_the_same_url_declared_twice_keeps_the_larger_score(self):
        same = "https://e.com/icon.png"
        ranked = site_icons.rank([site_icons.Candidate(same, 16, "icon"),
                                  site_icons.Candidate(same, 180, "apple-touch")])
        self.assertEqual([(c.url, c.size) for c in ranked], [(same, 180)])

    def test_an_override_outranks_everything_the_site_declares(self):
        """FANZA：资产托在 p-smith.com，video.dmm.co.jp 自己的 `<link>` 指不到那里。"""
        ranked = site_icons.rank(
            site_icons.overrides_for("https://video.dmm.co.jp/av/content/?id=x")
            + site_icons.link_candidates(
                '<link rel="icon" type="image/svg+xml" href="/dmm.svg">', "https://video.dmm.co.jp/"))
        self.assertEqual(ranked[0].url, "https://p-smith.com/pinned/favicon_r18.ico")

    def test_fc2_takes_the_confirmed_off_site_icon_on_every_subdomain(self):
        """FC2 站上只有 16×16 的 favicon，够大的那份是横向字标——不是没找对，是没有。

        用户 2026-09-03 直接指定了这一枚（400×400、纯独角兽、没有文字）。作品链接落在
        `adult.contents.fc2.com`、`contents.fc2.com`，覆盖必须跟着子域走，否则等于没配。
        """
        for url in ["https://fc2.com/en/", "https://adult.contents.fc2.com/article/1/",
                    "https://video.fc2.com/"]:
            got = site_icons.overrides_for(url)
            self.assertTrue(got, url)
            self.assertIn("storage.googleapis.com/datanyze-data", got[0].url)
            self.assertTrue(got[0].url.endswith(
                "8ef39cbce34aece41d279b6e8e7dbb77aea3086e.png"), got[0].url)
        ranked = site_icons.rank(
            site_icons.overrides_for("https://adult.contents.fc2.com/")
            + site_icons.link_candidates(
                '<link rel="shortcut icon" href="//static.fc2.com/share/image/favicon.ico">',
                "https://adult.contents.fc2.com/"))
        self.assertIn("datanyze-data", ranked[0].url)

    def test_the_app_store_icon_is_not_offered(self):
        """用户否决了那一枚：背景多了胶片图案。留着会在下一次改动里被当成现状抄走。"""
        for entries in site_icons.HOST_OVERRIDES.values():
            for url in entries:
                self.assertNotIn("mzstatic.com", url)

    def test_overrides_match_subdomains_of_the_listed_host(self):
        self.assertTrue(site_icons.overrides_for("https://www.av-event.jp/actress/1"))
        self.assertEqual(site_icons.overrides_for("https://example.com/"), [])

    def test_a_path_keyed_override_only_matches_that_path(self):
        """BangBros 的三个频道共用一个主机。键必须带路径，否则三个频道同一枚图标。"""
        bus = site_icons.overrides_for("https://bangbros.com/websites/BangBus")
        eighteen = site_icons.overrides_for("https://bangbros.com/websites/BangBros18")
        self.assertTrue(bus and eighteen)
        self.assertNotEqual(bus[0].url, eighteen[0].url)
        for got in (bus, eighteen):
            self.assertIn("images-assets-ht.project1content.com", got[0].url)
        # 页面配置里没有 MonstersOfCock 的 logo 资产，未取得就是未取得，不编一个。
        self.assertEqual(
            site_icons.overrides_for("https://bangbros.com/websites/MonstersOfCock"), [])
        self.assertEqual(site_icons.overrides_for("https://bangbros.com/"), [])

    def test_a_trailing_slash_and_deeper_path_still_match(self):
        for url in ["https://bangbros.com/websites/BangBus/",
                    "https://www.bangbros.com/websites/BangBus?page=2",
                    "https://bangbros.com/websites/BangBus/videos/1"]:
            with self.subTest(url=url):
                self.assertTrue(site_icons.overrides_for(url), url)

    def test_the_longest_matching_prefix_wins(self):
        """主机级条目不许把路径级条目盖掉：那正是同主机坍缩的来源。"""
        table = dict(site_icons.HOST_OVERRIDES)
        table["bangbros.com"] = ("https://bangbros.com/favicon.ico",)
        table["bangbros.com/websites"] = ("https://e.com/generic.png",)
        original = site_icons.HOST_OVERRIDES
        site_icons.HOST_OVERRIDES = table
        try:
            got = site_icons.overrides_for("https://bangbros.com/websites/BangBus")
            self.assertIn("6480a83863bd03", got[0].url)
            self.assertEqual(
                site_icons.overrides_for("https://bangbros.com/websites/Other")[0].url,
                "https://e.com/generic.png")
            self.assertEqual(site_icons.overrides_for("https://bangbros.com/")[0].url,
                             "https://bangbros.com/favicon.ico")
        finally:
            site_icons.HOST_OVERRIDES = original

    def test_a_path_keyed_override_does_not_leak_to_subdomains(self):
        """带路径的键管的是一条路径，不是一族主机。"""
        self.assertEqual(
            site_icons.overrides_for("https://cdn.bangbros.com/websites/BangBus"), [])

    def test_the_host_key_ignores_www_and_case(self):
        self.assertEqual(site_icons.host_key("https://WWW.X.com/a"), "x.com")
        self.assertEqual(site_icons.host_key("not a url"), "")


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.asked: list[str] = []

    def fetcher(self, pages):
        def fetch(url):
            self.asked.append(url)
            got = pages.get(url)
            return None if got is None else (got, "")
        return fetch

    def test_discovery_walks_homepage_then_manifest_then_conventional(self):
        fetch = self.fetcher({
            "https://e.com": b'<link rel="manifest" href="/m.json">',
            "https://e.com/m.json": b'{"icons":[{"src":"/big.png","sizes":"512x512"}]}',
        })
        found = site_icons.discover("https://e.com/talent/a", fetch)
        self.assertEqual(urls(found), ["https://e.com/big.png",
                                       "https://e.com/apple-touch-icon.png",
                                       "https://e.com/favicon.ico"])

    def test_a_homepage_that_cannot_be_fetched_still_leaves_the_old_conventions(self):
        """首页取不到不算失败：老规矩位置照样值得一试。"""
        found = site_icons.discover("https://e.com/a", self.fetcher({}))
        self.assertEqual(urls(found), ["https://e.com/apple-touch-icon.png",
                                       "https://e.com/favicon.ico"])

    def test_a_url_without_an_origin_asks_nothing(self):
        self.assertEqual(site_icons.discover("not a url", self.fetcher({})), [])
        self.assertEqual(self.asked, [])

    def test_everything_discovery_finds_is_only_host_scoped(self):
        """发现流程从 `origin(url)` 出发，路径一丢，结果只能代表主机。

        `bangbros.com/websites/{BangBus,BangBros18,MonstersOfCock}` 三个频道因此拿到
        逐字相同的候选表——这就是三份结果 sha256 完全一样的来源。
        """
        fetch = self.fetcher({
            "https://network.example": b'<link rel="icon" sizes="16x16" href="/fav16.png">'})
        found = [site_icons.discover(f"https://network.example/websites/{name}", fetch)
                 for name in ("ChannelA", "ChannelB", "ChannelC")]
        self.assertEqual(urls(found[0]), urls(found[1]))
        self.assertEqual(urls(found[1]), urls(found[2]))
        self.assertEqual({c.scope for c in found[0]}, {site_icons.HOST_SCOPE})

    def test_an_override_candidate_is_entity_scoped(self):
        got = site_icons.overrides_for("https://bangbros.com/websites/BangBus")
        self.assertEqual([c.scope for c in got], [site_icons.ENTITY_SCOPE])


class ArchiveViewTests(unittest.TestCase):
    """存档链接的圆标按原站找：图标是那家公司的，取图走同一份快照。"""

    SNAPSHOT = ("https://web.archive.org/web/20220512171503/"
                "https://ones-double.com/people/mikami-yua/")

    def test_a_snapshot_is_looked_up_as_the_site_it_preserved(self):
        page, locate = social_links.archive_view(self.SNAPSHOT)
        self.assertEqual(page, "https://ones-double.com/people/mikami-yua/")
        self.assertEqual(locate("https://ones-double.com/favicon.ico"),
                         "https://web.archive.org/web/20220512171503id_/"
                         "https://ones-double.com/favicon.ico")

    def test_an_address_already_inside_the_archive_is_fetched_as_is(self):
        _, locate = social_links.archive_view(self.SNAPSHOT)
        pinned = "https://web.archive.org/web/20220130000436id_/https://ones-double.com/logo.svg"
        self.assertEqual(locate(pinned), pinned)

    def test_a_live_link_is_left_alone(self):
        page, locate = social_links.archive_view("https://faleno.jp/top/")
        self.assertEqual((page, locate("https://faleno.jp/favicon.ico")),
                         ("https://faleno.jp/top/", "https://faleno.jp/favicon.ico"))

    def test_the_archive_front_page_is_not_a_snapshot(self):
        page, _ = social_links.archive_view("https://web.archive.org/")
        self.assertEqual(page, "https://web.archive.org/")


def reject_host_scope(candidate, data, content_type=""):
    """`harvest_studio_icons` 挂的那道守卫，这里用最小实现代表它。"""
    return candidate.scope == site_icons.ENTITY_SCOPE


class SharedHostGuardTests(unittest.TestCase):
    """同主机的几个频道不能共用一枚 favicon，即使那一枚过了所有闸门。"""

    def fetcher(self, extra=None):
        pages = {
            # 频道页只声明 16×16，被 harvest 的 MIN_SHORT_EDGE 退回。
            "https://bangbros.com": b'<link rel="icon" sizes="16x16" href="/fav16.png">',
            "https://bangbros.com/fav16.png": b"SMALL",
            # Aylo 站点模板的通用图标：64×64、内容比 1.00，两道闸门都过。
            "https://bangbros.com/apple-touch-icon.png": b"TEMPLATE-1",
            "https://bangbros.com/favicon.ico": b"TEMPLATE-1",
        }
        pages.update(extra or {})
        return lambda url: (pages[url], "") if url in pages else None

    def test_without_the_guard_the_platform_favicon_wins(self):
        """这是修之前的行为，留着当对照：闸门全过，图也不糊，只是不是这个频道的。"""
        made = site_icons.best_mark("https://bangbros.com/websites/MonstersOfCock",
                                    self.fetcher(), lambda data, content_type="": data)
        self.assertEqual(made, b"SMALL")

    def test_the_guard_refuses_every_host_level_candidate(self):
        made = site_icons.best_mark("https://bangbros.com/websites/MonstersOfCock",
                                    self.fetcher(), lambda data, content_type="": data,
                                    accept=reject_host_scope)
        self.assertIsNone(made)

    def test_the_guard_never_lets_a_refused_candidate_reach_the_fallback(self):
        """回落也不许用：那会把整站通用的 favicon 装成这个实体的标识。"""
        used = []
        made = site_icons.best_mark(
            "https://bangbros.com/websites/MonstersOfCock", self.fetcher(),
            lambda data, content_type="": None,
            fallback=lambda data, content_type="": used.append(data) or b"PLAIN",
            accept=reject_host_scope)
        self.assertIsNone(made)
        self.assertEqual(used, [])

    def test_an_override_gets_through_the_guard(self):
        """加了这个频道自己的来源，同一道守卫就该放行。"""
        override = site_icons.overrides_for("https://bangbros.com/websites/BangBus")[0].url
        made = site_icons.best_mark(
            "https://bangbros.com/websites/BangBus",
            self.fetcher({override: b"CHANNEL-LOGO"}),
            lambda data, content_type="": data, accept=reject_host_scope)
        self.assertEqual(made, b"CHANNEL-LOGO")

    def test_the_two_channels_with_overrides_get_different_bytes(self):
        """根因就是三份结果 sha256 完全相同。两个频道必须拿到各自那一份。"""
        made = []
        for name in ("BangBus", "BangBros18"):
            url = f"https://bangbros.com/websites/{name}"
            override = site_icons.overrides_for(url)[0].url
            made.append(site_icons.best_mark(
                url, self.fetcher({override: f"BYTES:{name}".encode()}),
                lambda data, content_type="": data, accept=reject_host_scope))
        self.assertEqual(made, [b"BYTES:BangBus", b"BYTES:BangBros18"])


class BestMarkTests(unittest.TestCase):
    def test_missav_uses_the_confirmed_icon_when_the_homepage_is_unavailable(self):
        icon = "https://missav.ws/img/favicon.png"
        for host in ("missav.com", "missav.ws"):
            with self.subTest(host=host):
                fetched = []

                def fetch(url, fetched=fetched):
                    fetched.append(url)
                    return (b"MISSAV-ICON", "image/png") if url == icon else None

                made = site_icons.best_mark(
                    f"https://{host}/", fetch,
                    lambda data, content_type="": data if content_type == "image/png" else None)
                self.assertEqual(made, b"MISSAV-ICON")
                self.assertIn(icon, fetched)

    def test_the_first_candidate_that_renders_wins(self):
        pages = {"https://e.com": b'<link rel="icon" sizes="256x256" href="/a.png">',
                 "https://e.com/a.png": b"A",
                 "https://e.com/apple-touch-icon.png": b"B"}
        rendered = []

        def render(data, content_type=""):
            rendered.append(data)
            return None if data == b"A" else b"MARK"

        made = site_icons.best_mark("https://e.com/x", lambda u: (pages[u], "") if u in pages else None, render)
        self.assertEqual(made, b"MARK")
        self.assertEqual(rendered, [b"A", b"B"])

    def test_the_fallback_reuses_bytes_already_fetched(self):
        """一个都没做成时才回落，且用循环里存下的第一份，不重新发现一遍。

        重新发现要把首页和 manifest 再取一次；为一张回落图打两轮请求不值得。
        """
        pages = {"https://e.com": b'<link rel="icon" sizes="256x256" href="/a.png">',
                 "https://e.com/a.png": b"A"}
        asked = []

        def fetch(url):
            asked.append(url)
            return (pages[url], "") if url in pages else None

        made = site_icons.best_mark("https://e.com/x", fetch,
                                    lambda *a, **k: None,
                                    fallback=lambda data, content_type="": b"PLAIN:" + data)
        self.assertEqual(made, b"PLAIN:A")
        self.assertEqual(asked.count("https://e.com"), 1, "首页只该取一次")

    def test_a_candidate_that_cannot_be_fetched_does_not_burn_an_attempt(self):
        """404 不算「试过一个站点资产」；预算是留给真的取回来了却不合格的那些。"""
        asked = []

        def fetch(url):
            asked.append(url)
            return (b"", "") if url == "https://e.com" else None

        self.assertIsNone(site_icons.best_mark("https://e.com/x", fetch, lambda *a, **k: b"M"))
        self.assertEqual(asked, ["https://e.com", "https://e.com/apple-touch-icon.png",
                                 "https://e.com/favicon.ico"])

    def test_the_fetch_budget_stops_a_site_with_dozens_of_declared_icons(self):
        """mgstage 声明了五个 apple-touch-icon。排序已经把最可能的放前面了，
        试到第五个还不成多半是这个站没有能用的图标，继续只是白打人家的服务器。
        """
        declared = "".join(f'<link rel="icon" sizes="{n}x{n}" href="/i{n}.png">'
                           for n in (512, 256, 192, 180, 152, 144, 128))
        tried = []

        def fetch(url):
            tried.append(url)
            return (declared.encode(), "") if url == "https://e.com" else (b"x", "")

        self.assertIsNone(site_icons.best_mark("https://e.com/a", fetch, lambda *a, **k: None))
        self.assertEqual(len(tried) - 1, site_icons.MAX_FETCH)


if __name__ == "__main__":
    unittest.main()
