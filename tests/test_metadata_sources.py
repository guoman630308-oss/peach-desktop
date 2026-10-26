"""站点解析器契约：取页与解析分开、配置注入、`query` 的异常翻译、失败原因表，以及配置与各张表的一致。"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from peach import scraping_access
from peach.http import HttpResponse
from peach.jav_cover_fetch import DeadlineExceeded, HostLimitedTransport, NotFound, Unavailable
from peach.library_processing import PROVIDER_NAMES, SOURCE_INTERVALS, SOURCE_LABELS
from peach.metadata import MetadataProviderError
from peach.metadata_policy import SOURCE_SPECS
from peach.metadata_routes import FC2_STAGE
from peach.scraping_access import FIRST_BLOCKED_PAUSE, SourcePaused, SourceTransport, cooldown_state
from peach.sources import (COOLDOWN_ACTIONS, PERMANENT_REASONS, REASON_KINDS, SEESAA, SITE_SOURCES, FailureReason,
                           Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure, http_failure)
from peach.sources.base import challenge_page
from peach.sources.seesaa import WIKI_SOURCES, WikiPages

DEMO = SiteConfig(name="demo", label="Demo", provider="demo-page", base_url="https://demo.test",
                  domains=("demo.test",), stage="community")


class JavDBResourceTests(unittest.TestCase):
    def test_categories_keep_raw_words_and_deduplicate_links_after_identity_check(self):
        from peach.sources.javdb import JavDBSource
        from peach.metadata import extract_peach_fields
        html = '''<div class="panel-block"><strong>番號:</strong><span class="value">ABW-220</span></div>
        <div class="panel-block"><strong>類別:</strong><span class="value">
        <a href="/tags?c=1">高跟鞋</a><a href="/tags?c=2">絲襪、過膝襪</a>
        <a href="/tags?c=3"><b>尚未收錄的分類</b></a><a href="/tags?c=1">高跟鞋</a>
        <a href="/tags?c=4">單體作品</a></span></div>'''
        page = Page("https://javdb.com/v/demo", html.encode())
        record = JavDBSource().parse(page, "ABW-220")
        self.assertEqual(record.tags, ("高跟鞋", "絲襪、過膝襪", "尚未收錄的分類", "單體作品"))
        self.assertEqual(record.payload()["genres"], ["高跟鞋", "絲襪、過膝襪", "尚未收錄的分類", "單體作品"])
        fields = extract_peach_fields(record.payload())
        self.assertEqual(fields["tags"]["value"], ["高跟", "丝袜"])
        self.assertEqual(fields["tags"]["unmapped_genres"], ["尚未收錄的分類"])
        with self.assertRaises(SourceFailure):
            JavDBSource().parse(page, "ABW-221")

    def test_resource_area_and_comment_links_share_identity(self):
        from peach.sources.javdb import parse_magnets, parse_comment_links, merge_resources
        digest = 'a' * 40
        file_hash = 'b' * 32
        html = f'''<div id="magnets-content"><div class="item">
          <a href="magnet:?xt=urn:btih:{digest}&amp;dn=demo"><span class="name">演示</span></a>
          <span class="meta">4 GB, 2 個文件</span><span class="tag">字幕</span><span class="time">2026-10-01</span>
          </div></div><dl class="review-items"><dt class="review-item" id="review-item-1">
          <a href="https://ignored.test/profile">作者</a><span class="time">2026-10-02</span>
          <div class="content"><a href="magnet:?xt=urn:btih:{digest.upper()}">另一条写法</a>
          ed2k://|file|demo%20file.mkv|123456|{file_hash}|/
          <a href="ed2k://|file|another-name|123456|{file_hash.upper()}|/">重复</a>
          <a href="https://EXAMPLE.test/file?q=1">补充</a> https://example.test/file?q=1
          <a href="javascript:alert(1)">忽略</a></div></dt></dl>'''
        page = Page('https://javdb.com/v/demo', html.encode())
        items = merge_resources([*parse_magnets(page), *parse_comment_links(page)])
        self.assertEqual([item['protocol'] for item in items], ['magnet', 'ed2k', 'url'])
        self.assertEqual(items[0]['size'], '4 GB')
        self.assertEqual(items[0]['attributes'], ['字幕'])
        self.assertEqual(len(items[0]['origins']), 2)
        self.assertEqual(items[1]['name'], 'another-name')
        self.assertEqual(items[1]['size'], '123,456 B')
        self.assertEqual(items[2]['uri'], 'https://example.test/file?q=1')

    def test_missing_magnet_region_is_not_an_empty_result(self):
        from peach.sources.javdb import parse_magnets
        with self.assertRaises(SourceFailure):
            parse_magnets(Page('https://javdb.com/v/demo', b'<html>login</html>'))
        self.assertEqual(parse_magnets(Page('https://javdb.com/v/demo', b'<div id="magnets-content"></div>')), [])

    def test_comment_failure_keeps_magnets_and_reports_partial_results(self):
        from peach.sources.javdb import JavDBSource
        source = JavDBSource()
        page = Page('https://javdb.com/v/demo', b'<div id="magnets-content"></div>'
                    b'<a class="review-tab" data-url="/v/demo/reviews/lastest"></a>')
        with patch.object(source, 'fetch', return_value=page), patch.object(source, 'parse'), \
                patch.object(source, '_page', side_effect=SourceFailure(FailureReason.AUTH_REQUIRED, 'login')):
            result = source.resources('DEMO-001', session=Session(lambda *args: None))
        self.assertEqual(result['items'], [])
        self.assertEqual(result['source_url'], 'https://javdb.com/v/demo')
        self.assertTrue(result['warnings'])

    def test_comment_urls_cannot_fetch_other_hosts(self):
        from peach.sources.javdb import JavDBSource
        source = JavDBSource()
        page = Page('https://javdb.com/v/demo', b'<div id="magnets-content"></div>'
                    b'<a class="review-tab" data-url="https://example.test/private"></a>')
        with patch.object(source, 'fetch', return_value=page), patch.object(source, 'parse'), \
                patch.object(source, '_page') as fetch_page:
            result = source.resources('DEMO-001', session=Session(lambda *args: None))
        fetch_page.assert_not_called()
        self.assertTrue(result['warnings'])


class DemoSource(SiteSource):
    """最小的一站：作品页是 `/<番号>`，页面正文就是标题。"""

    DEFAULT = DEMO

    def fetch(self, code, *, session):
        return session.get(f"{self.config.base_url}/{code}", config=self.config)

    def parse(self, page, code):
        if page.text == "gate":
            raise SourceFailure(FailureReason.AUTH_REQUIRED, "Demo 要求登录")
        return SiteRecord(source=self.config.name, provenance=self.config.provider, code=code,
                          source_url=page.url, title=page.text)


def serve(pages):
    calls = []

    def call(request, timeout, limit):
        calls.append((request.url, request.headers.get("Referer"), limit))
        body = pages.get(request.url)
        if isinstance(body, Exception):
            raise body
        return HttpResponse(200 if body is not None else 404, {}, body or b"", request.url)

    call.calls = calls
    return call


class ContractTests(unittest.TestCase):
    def test_parse_reads_a_page_without_any_transport(self):
        record = DemoSource().parse(Page("https://demo.test/ABC-001", "標題".encode()), "ABC-001")
        self.assertEqual((record.source, record.provenance, record.title, record.source_url),
                         ("demo", "demo-page", "標題", "https://demo.test/ABC-001"))

    def test_fetch_uses_the_config_for_referer_and_page_limit_and_query_chains_both_steps(self):
        transport = serve({"https://demo.test/ABC-001": "標題".encode()})
        record = DemoSource().query("ABC-001", session=Session(transport))
        self.assertEqual(record.title, "標題")
        self.assertEqual(transport.calls, [("https://demo.test/ABC-001", "https://demo.test/", DEMO.page_limit)])

    def test_an_injected_config_replaces_the_default_one(self):
        mirror = SiteConfig(name="demo", label="Demo", provider="demo-page", base_url="https://mirror.test",
                            domains=("mirror.test",), stage="community", page_limit=1024)
        transport = serve({"https://mirror.test/ABC-001": b"x"})
        DemoSource(mirror).query("ABC-001", session=Session(transport))
        self.assertEqual(transport.calls, [("https://mirror.test/ABC-001", "https://mirror.test/", 1024)])
        self.assertIs(DemoSource().config, DEMO)

    def test_one_request_can_name_its_own_referer_and_extra_headers(self):
        seen = []

        def transport(request, timeout, limit):
            seen.append((request.headers.get("Referer"), request.headers.get("X-Inertia")))
            return HttpResponse(200, {}, b"x", request.url)

        session = Session(transport)
        session.get("https://demo.test/ABC-001", config=DEMO)
        session.get("https://demo.test/ABC-001", config=DEMO, referer="https://demo.test/ABC-001",
                    headers={"X-Inertia": "true"})
        self.assertEqual(seen, [("https://demo.test/", None), ("https://demo.test/ABC-001", "true")])

    def test_post_sends_the_body_with_the_same_referer_limit_and_extra_headers(self):
        """只收 POST 的接口（DMM 的 GraphQL）走 `post`：方法与请求体进 `HttpRequest`，其余与 `get` 同一条路。"""
        seen = []

        def transport(request, timeout, limit):
            seen.append((request.method, request.url, request.body, request.headers.get("Referer"),
                         request.headers.get("Content-Type"), limit))
            return HttpResponse(200, {}, b'{"data": {}}', request.url)

        page = Session(transport).post("https://demo.test/graphql", config=DEMO, body=b'{"query": "x"}',
                                       headers={"Content-Type": "application/json"})
        self.assertEqual(seen, [("POST", "https://demo.test/graphql", b'{"query": "x"}', "https://demo.test/",
                                 "application/json", DEMO.page_limit)])
        self.assertEqual((page.url, page.body), ("https://demo.test/graphql", b'{"data": {}}'))
        Session(transport).get("https://demo.test/ABC-001", config=DEMO)
        self.assertEqual(seen[-1][:3], ("GET", "https://demo.test/ABC-001", None))

    def test_records_default_to_the_single_query_result(self):
        transport = serve({"https://demo.test/ABC-001": "標題".encode()})
        found = DemoSource().records("ABC-001", session=Session(transport))
        self.assertEqual([record.title for record in found], ["標題"])
        with self.assertRaises(SourceFailure):
            DemoSource().records("ABC-002", session=Session(transport))

    def test_query_translates_http_tiers_into_reasons_and_keeps_the_wording(self):
        with self.assertRaises(SourceFailure) as caught:
            DemoSource().query("ABC-001", session=Session(serve({})))
        self.assertEqual((caught.exception.reason, caught.exception.status_code, str(caught.exception)),
                         (FailureReason.NOT_FOUND, 404, "HTTP 404"))
        for status, reason in ((401, FailureReason.AUTH_REQUIRED), (403, FailureReason.AUTH_REQUIRED),
                               (410, FailureReason.GONE), (429, FailureReason.RATE_LIMITED),
                               (500, FailureReason.SERVER_ERROR), (503, FailureReason.SERVER_ERROR),
                               (418, FailureReason.NETWORK)):
            with self.subTest(status=status):
                failure = http_failure(Unavailable(f"HTTP {status}"))
                self.assertEqual((failure.reason, failure.status_code, failure.message), (reason, status, f"HTTP {status}"))
        self.assertEqual(http_failure(NotFound("来源给的是占位图")).reason, FailureReason.NOT_FOUND)

    def test_query_passes_parse_failures_through_and_leaves_transport_signals_alone(self):
        """冷却、预算与连接失败由传输层抛、由调用方按原语义处理，契约不改写它们。"""
        with self.assertRaises(SourceFailure) as caught:
            DemoSource().query("ABC-001", session=Session(serve({"https://demo.test/ABC-001": b"gate"})))
        self.assertEqual(caught.exception.reason, FailureReason.AUTH_REQUIRED)
        for signal in (SourcePaused("来源正在冷却"), DeadlineExceeded("动作预算已用尽"),
                       httpx.TransportError("来源连接未取得")):
            with self.subTest(signal=type(signal).__name__), \
                    patch("peach.jav_cover_fetch.NETWORK_RETRY_DELAYS", ()), \
                    self.assertRaises(type(signal)):
                DemoSource().query("ABC-001", session=Session(serve({"https://demo.test/ABC-001": signal})))

    def test_the_record_projects_into_the_snapshot_shaped_payload(self):
        record = SiteRecord(source="demo", provenance="demo-page", code="ABC-001", source_url="https://demo.test/ABC-001",
                            title="標題", performers=({"japanese_name": "女優"},), studio="片商", runtime=90,
                            cover_urls=("https://demo.test/a.jpg", "https://demo.test/b.jpg"))
        self.assertEqual(record.cover_url, "https://demo.test/a.jpg")
        self.assertEqual(record.payload(), {
            "id": "ABC-001", "source_url": "https://demo.test/ABC-001", "title": "標題",
            "actresses": [{"japanese_name": "女優"}], "maker": "片商", "label": "", "series": "", "director": "",
            "release_date": "", "runtime": 90, "cover_urls": ["https://demo.test/a.jpg", "https://demo.test/b.jpg"],
            "cover_url": "https://demo.test/a.jpg"})
        tagged = SiteRecord(source="demo", provenance="demo-page", code="ABC-001", source_url="", tags=(),
                            extra={"plot": "剧情"})
        payload = tagged.payload()
        self.assertEqual((payload["genres"], payload["plot"], payload["cover_urls"], payload["cover_url"]),
                         ([], "剧情", [], ""))
        self.assertNotIn("genres", record.payload(), "不给标签的站不带 genres 键")


class FailureReasonTests(unittest.TestCase):
    def test_the_table_has_twelve_reasons_each_with_a_kind(self):
        self.assertEqual({reason.value for reason in FailureReason}, {
            "cloudflare_challenge", "ip_banned", "geo_restricted", "auth_required", "not_found", "gone",
            "parse_error", "no_usable_metadata", "rate_limited", "timeout", "server_error", "network"})
        self.assertEqual(set(REASON_KINDS), set(FailureReason))
        self.assertEqual(set(REASON_KINDS.values()), {"auth", "unavailable", "not_found"})
        self.assertEqual({reason for reason, kind in REASON_KINDS.items() if kind == "auth"},
                         {FailureReason.CLOUDFLARE_CHALLENGE, FailureReason.IP_BANNED,
                          FailureReason.GEO_RESTRICTED, FailureReason.AUTH_REQUIRED})
        self.assertEqual({reason for reason, kind in REASON_KINDS.items() if kind == "not_found"},
                         {FailureReason.NOT_FOUND, FailureReason.GONE, FailureReason.NO_USABLE_METADATA})

    def test_provider_errors_keep_the_three_tier_semantics(self):
        auth = SourceFailure(FailureReason.CLOUDFLARE_CHALLENGE, "撞上 Cloudflare 挑战页", status_code=403)
        error = auth.provider_error("AVSOX")
        self.assertIsInstance(error, MetadataProviderError)
        self.assertEqual((error.kind, error.status_code, error.retryable, error.temporary, error.detail),
                         ("auth", 403, False, True, "cloudflare_challenge"))
        self.assertEqual(str(error), "AVSOX 需要登录或已被拒绝：撞上 Cloudflare 挑战页")
        missing = SourceFailure(FailureReason.NOT_FOUND, "javdb 没有这个番号").provider_error("javdb")
        self.assertEqual((missing.kind, str(missing)), ("not_found", "javdb 没有这个番号"))
        parse = SourceFailure(FailureReason.PARSE_ERROR, "结构对不上").provider_error("javdb")
        self.assertEqual((parse.kind, parse.retryable, parse.temporary), ("unavailable", False, False))
        timeout = SourceFailure(FailureReason.TIMEOUT, "超时", detail="curl 28").provider_error("javdb")
        self.assertEqual((timeout.kind, timeout.retryable, timeout.temporary, timeout.detail),
                         ("unavailable", True, True, "curl 28"))
        self.assertEqual(PERMANENT_REASONS, {FailureReason.PARSE_ERROR})

    def test_only_bans_and_rate_limits_pause_a_whole_site(self):
        self.assertEqual(COOLDOWN_ACTIONS, {FailureReason.CLOUDFLARE_CHALLENGE: "blocked",
                                            FailureReason.IP_BANNED: "blocked",
                                            FailureReason.GEO_RESTRICTED: "blocked",
                                            FailureReason.RATE_LIMITED: "rate_limited"})
        for reason in FailureReason:
            with self.subTest(reason=reason):
                self.assertEqual(SourceFailure(reason, "").cooldown_action, COOLDOWN_ACTIONS.get(reason, ""))
        self.assertEqual(set(COOLDOWN_ACTIONS.values()), {"blocked", "rate_limited"},
                         "两档就是 scraping_access.pause_source 的 refused=True / retry_after 两档")


CHALLENGE_HTML = (b"<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
                  b"<body><script>window._cf_chl_opt={}</script></body></html>")


class SiteCooldownTests(unittest.TestCase):
    """自写站报出 `COOLDOWN_ACTIONS` 里的细档时整站进冷却，与 amane 桥写同一份记录，同一轮里不再一部片撞一次。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        finder = patch("peach.browser_transport.find_browser", return_value=None)
        finder.start()
        self.addCleanup(finder.stop)

    def session(self, source, respond):
        """真的 `SourceTransport` 套在 `HostLimitedTransport` 里（与 `LibraryMetadataProvider` 同一个形状），底下是假传输。"""
        calls = []

        def fake(request, timeout, limit):
            calls.append(request.url)
            return respond(request)

        raw = SourceTransport(self.root)
        raw.transports[source] = fake
        return Session(HostLimitedTransport(raw, 0.0)), calls

    def test_an_avbase_challenge_served_with_200_pauses_the_whole_site(self):
        session, calls = self.session("avbase", lambda request: HttpResponse(200, {}, CHALLENGE_HTML, request.url))
        with self.assertRaises(SourceFailure) as caught:
            SITE_SOURCES["avbase"]().query("ABW-358", session=session)
        self.assertEqual(caught.exception.reason, FailureReason.CLOUDFLARE_CHALLENGE)
        until, blocks = cooldown_state(self.root, "avbase")
        self.assertEqual((round(until - time.time()), blocks), (FIRST_BLOCKED_PAUSE, 1))
        with self.assertRaises(SourcePaused):
            SITE_SOURCES["avbase"]().query("MIDE-594", session=session)
        self.assertEqual(len(calls), 1, "冷却期内第二部片一次都不问")

    def test_the_challenge_title_is_recognised_in_its_other_wordings(self):
        for title in ("Just a moment...", "请稍候…", "Attention Required! | Cloudflare"):
            with self.subTest(title=title):
                self.assertTrue(challenge_page(f"<html><head><title> {title} </title>".encode()))
        self.assertFalse(challenge_page(b"<html><head><title>ABW-358 - AVBase</title>"
                                        b"<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'>"),
                         "正常页里 Cloudflare 注入的脚本不算验证页")

    def test_each_cooldown_reason_goes_to_its_own_tier_and_other_reasons_leave_no_record(self):
        class Failing(DemoSource):
            reason = FailureReason.NOT_FOUND

            def parse(self, page, code):
                raise SourceFailure(self.reason, "demo")

        config = SiteConfig(name="javbus", label="JavBus", provider="javbus-page", base_url="https://www.javbus.com",
                            domains=("javbus.com",), stage="community")
        for reason in FailureReason:
            with self.subTest(reason=reason):
                scraping_access.cooldown_path(self.root, "javbus").unlink(missing_ok=True)
                session, _calls = self.session("javbus", lambda request: HttpResponse(200, {}, b"page", request.url))
                Failing.reason = reason
                with self.assertRaises(SourceFailure):
                    Failing(config).query("ABC-001", session=session)
                until, blocks = cooldown_state(self.root, "javbus")
                action = COOLDOWN_ACTIONS.get(reason, "")
                if action == "blocked":
                    self.assertEqual((round(until - time.time()), blocks), (FIRST_BLOCKED_PAUSE, 1))
                elif action == "rate_limited":
                    self.assertEqual((round(until - time.time()), blocks), (900, 0))
                else:
                    self.assertEqual((until, blocks), (0.0, 0), "不是整站的问题就不停整站")

    def test_a_nested_query_counts_one_failure_once(self):
        """子类的 `query` 包着基类的 `query`（`r18dev`），同一个失败只翻一次倍。"""
        class Nested(DemoSource):
            def parse(self, page, code):
                raise SourceFailure(FailureReason.IP_BANNED, "demo")

            def query(self, code, *, session):
                with self.holding(session):
                    return super().query(code, session=session)

        config = SiteConfig(name="javdb", label="javdb", provider="javdb-page", base_url="https://javdb.com",
                            domains=("javdb.com",), stage="community")
        session, _calls = self.session("javdb", lambda request: HttpResponse(200, {}, b"page", request.url))
        with self.assertRaises(SourceFailure):
            Nested(config).query("ABC-001", session=session)
        self.assertEqual(cooldown_state(self.root, "javdb")[1], 1)

    def test_a_javbus_refusal_pauses_the_site_like_the_other_community_sources(self):
        session, calls = self.session("javbus", lambda request: HttpResponse(403, {}, b"", request.url))
        for code in ("MIDE-594", "ABW-358"):
            with self.assertRaises(SourcePaused):
                SITE_SOURCES["javbus"]().query(code, session=session)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cooldown_state(self.root, "javbus")[1], 1)


class ConfigConsistencyTests(unittest.TestCase):
    """配置是数据：`SOURCE_SPECS`、`SOURCE_LABELS`、`PROVIDER_NAMES`、`scraping_access.SOURCES` 与
    `SOURCE_INTERVALS` 里这几站的那几行，与站的配置逐项一致。"""

    def test_every_registered_site_agrees_with_the_existing_tables(self):
        for name, site in SITE_SOURCES.items():
            config = site.DEFAULT
            with self.subTest(site=name):
                self.assertIs(site().config, config)
                self.assertEqual(config.name, name)
                self.assertEqual(config.stage, SOURCE_SPECS[name].kind)
                self.assertEqual(config.label, SOURCE_LABELS[name])
                self.assertEqual(config.provider, PROVIDER_NAMES[name])
                if name in WIKI_SOURCES:
                    continue
                access = scraping_access.SOURCES[name]
                self.assertEqual(set(config.domains), set(access["domains"]))
                self.assertEqual(config.cookie, bool(access.get("cookie")))
                self.assertTrue(config.base_url.startswith("https://"))
                self.assertEqual(scraping_access.source_for(config.base_url), name)
                for host, interval in SOURCE_INTERVALS.items():
                    if scraping_access.source_for("https://" + host + "/") == name:
                        self.assertEqual(interval, config.interval, f"{host} 的主机间隔与配置不一致")

    def test_every_bridge_site_agrees_with_the_same_tables(self):
        """经 amane 桥的站同样逐项一致；每个站只有一个归属，桥站与自写站不重名（ADR-0048）。"""
        from peach import metadata_amane
        self.assertEqual(set(metadata_amane.SITE_CONFIGS) & set(SITE_SOURCES), set())
        for name, config in metadata_amane.SITE_CONFIGS.items():
            with self.subTest(site=name):
                self.assertEqual(config.name, name)
                self.assertEqual(SOURCE_SPECS[name].kind, "community" if config.stage == "amane" else config.stage)
                self.assertEqual(config.label, SOURCE_LABELS[name])
                self.assertEqual(config.provider, PROVIDER_NAMES[name])
                # Prestige 与 MGStage 的冷却记录和封面那一路同键：同一个出口对同一站只有一份冷却。
                if name in scraping_access.SOURCES:
                    self.assertEqual(scraping_access.SOURCES[name]["label"], config.label)
        self.assertEqual({name for name, config in metadata_amane.SITE_CONFIGS.items() if config.stage == "official"},
                         {"makers", "prestige", "faleno", "dahlia", "mgstage"})

    def test_seesaa_brings_its_own_transport_and_keeps_its_source_identity(self):
        """Seesaa 作品表不经 `SourceTransport`：不收 Cookie、采集设置页没有它的卡片，冷却只在本批内，
        取页、缓存与限额在 `WikiPages`，间隔与页面上限取自配置；provenance 是账本里已有的 `sougouwiki`。"""
        self.assertEqual(set(WIKI_SOURCES), {"sougouwiki", "av_neme", "av_name"})
        for name, site in WIKI_SOURCES.items():
            config = site.DEFAULT
            with self.subTest(site=name):
                self.assertNotIn(name, scraping_access.SOURCES)
                self.assertIsNone(scraping_access.source_for(config.base_url))
                self.assertEqual((config.provider, config.cookie, config.interval, config.page_limit),
                                 (name, False, 2.0, 4 * 1024 * 1024))
                self.assertTrue(site.root.startswith(config.base_url + "/"))
        with patch("peach.sources.seesaa.HostLimiter") as limiter:
            pages = WikiPages("unused", transport=object())
        limiter.assert_called_once_with({}, default_interval=SEESAA.interval)
        self.assertIs(pages.config, SEESAA)

    def test_browser_catalogs_keep_their_interval_and_the_other_sites_the_default(self):
        self.assertEqual(SITE_SOURCES["javdb"].DEFAULT.interval, 3.0)
        self.assertEqual(SITE_SOURCES["javlibrary"].DEFAULT.interval, 3.0)
        for name in ("javbus", "avbase", "r18dev", "1pondo", "fc2", "fc2cmadb", "fc2ppvdb", "javten", "javarchive",
                     *WIKI_SOURCES):
            self.assertEqual(SITE_SOURCES[name].DEFAULT.interval, 2.0, name)
        self.assertEqual({scraping_access.source_for("https://" + host + "/") for host in SOURCE_INTERVALS},
                         {"javdb", "javlibrary"})

    def test_registered_sites_have_their_stage_cookie_and_page_limit(self):
        shape = {name: (site.DEFAULT.stage, site.DEFAULT.cookie, site.DEFAULT.page_limit)
                 for name, site in SITE_SOURCES.items()}
        self.assertEqual(shape, {"r18dev": ("official_mirror", False, 2 * 1024 * 1024),
                                 "dmm": ("official", False, 1024 * 1024),
                                 "1pondo": ("official", False, 1024 * 1024),
                                 "fc2": ("official", False, 2 * 1024 * 1024),
                                 "fc2cmadb": ("community", True, 2 * 1024 * 1024),
                                 "fc2ppvdb": ("community", True, 2 * 1024 * 1024),
                                 "javten": ("community", True, 2 * 1024 * 1024),
                                 "javarchive": ("community", False, 2 * 1024 * 1024),
                                 "avbase": ("community", False, 4 * 1024 * 1024),
                                 "javbus": ("community", True, 4 * 1024 * 1024),
                                 "javdb": ("community", True, 4 * 1024 * 1024),
                                 "javlibrary": ("community", True, 4 * 1024 * 1024),
                                 "sougouwiki": ("community", False, 4 * 1024 * 1024),
                                 "av_neme": ("community", False, 4 * 1024 * 1024),
                                 "av_name": ("community", False, 4 * 1024 * 1024)})
        self.assertEqual({name for name in SITE_SOURCES if SOURCE_SPECS[name].official},
                         {"r18dev", "dmm", "1pondo", "fc2"})
        self.assertEqual(tuple(name for name in SITE_SOURCES if name in FC2_STAGE), FC2_STAGE,
                         "FC2 五站按链上先后登记")
        # 在 Cloudflare 验证后面的两站：公开采集带用户贴的 Cookie，403 时整站冷却。
        for name in ("fc2ppvdb", "javten", "javlibrary"):
            self.assertEqual({key: scraping_access.SOURCES[name].get(key) for key in ("cookie", "session")},
                             {"cookie": True, "session": True}, name)
            self.assertTrue(scraping_access.SOURCES[name]["blocked_pause"], name)


if __name__ == "__main__":
    unittest.main()
