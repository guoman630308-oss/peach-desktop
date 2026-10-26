"""从裸 id 或名字反查来源。全部注入 transport，测试不联网。"""
import json
import os
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

from peach import follow_discovery
from peach.follow import FollowSourceError
from peach.follow_discovery import (
    CREATOR_INDEX_TTL_SECONDS, DEFAULT_PROVIDERS, MAX_TERM_LENGTH, CreatorIndex,
    archive_suggestions, discover, discovery_plan, forum_queries, identity_key,
    rule34video_slugs, search_variants, spelling_variants, suggest_term, tag_suggestions,
)
from peach.follow_secrets import Credential
from peach.http import HttpResponse
from support.backoff import no_real_backoff


CREATORS = json.dumps([
    {"id": "30917150", "name": "LazyProcrastinator", "service": "fanbox"},
    {"id": "109730638", "name": "EcchiWaffle", "service": "patreon"},
    {"id": "25775753", "name": "LazyMazer", "service": "patreon"},
    {"id": "suzutaro3d", "name": "鈴太郎3D", "service": "patreon"},
]).encode()

F95_HIT = json.dumps({"status": "ok", "msg": {"data": [
    {"thread_id": 50685, "title": "Lazy Procrastinator Collection",
     "version": "2026-06-28"}]}}).encode()
F95_MISS = json.dumps({"status": "ok", "msg": {"data": []}}).encode()
F95_SEARCH_FORM = b"""<html><body><input type="hidden" name="_xfToken" value="1,a" /></body></html>"""
F95_SEARCH_RESULTS = b"""<html><body><h3 class="contentRow-title">
<a href="/threads/ria-collection-2026-08-03-ria_neearts.146348/"><span class="label">Collection</span>Ria Collection [2026-08-03] [<em class="textHighlight">Ria_neearts</em>]</a>
</h3></body></html>"""
F95_SEARCH_NOTHING = b"""<html><body><div class="blockMessage">No results found.</div></body></html>"""
SIMPCITY_SEARCH_FORM = b"""<html><body><input type="hidden" name="_xfToken" value="1,s" /></body></html>"""
# 2026-09-08 实测 `solazola`：资源线程和讨论帖各一条，标题前的版块标签是分辨两者的依据。
SIMPCITY_SEARCH_RESULTS = b"""<html><body>
<h3 class="contentRow-title"><a href="/threads/solazola-baby_sue.17401/"><span class="label">OnlyFans</span><em class="textHighlight">Solazola</em> / baby_sue</a></h3>
<h3 class="contentRow-title"><a href="/threads/solazola-discussion.392510/"><span class="label">Simp Chat</span><em class="textHighlight">solazola</em> discussion</a></h3>
</body></html>"""
# 2026-09-01 实测：站上的写法是 `ria-neearts`，补全按字面前缀匹配，所以
# `Ria_neearts` 查出来是空的，`Ria-neearts` 才命中。
R34_AUTOCOMPLETE_HIT = json.dumps(
    [{"label": "ria-neearts (248)", "value": "ria-neearts"},
     {"label": "riahri (156)", "value": "riahri"}]).encode()
R34_CEEEEEKC = json.dumps([{"label": "ceeeeekc (40)", "value": "ceeeeekc"}]).encode()
# 首楼名片：一个与检索词不同的手柄、一个同名手柄、一个 pixiv 数字 id。
F95_PROFILE_PAGE = b"""<html><body><article class="message"><div class="message-userContent">
<a href="https://twitter.com/Ceeeeekc">X</a><a href="https://www.patreon.com/cekc">Patreon</a>
<a href="https://www.pixiv.net/users/123456">pixiv</a></div></article></body></html>"""
FANBOX_PROFILE = ("<html><meta name='metadata' content='"
                  + json.dumps({"urlContext": {"host": {"creatorId": "lazyprocrast"}}})
                    .replace("'", "&#39;")
                  + "'></html>").encode()
FANBOX_CREATOR = json.dumps({"body": {"user": {
    "userId": "30917150", "name": "LazyProcrastinator",
    "iconUrl": "https://pixiv.pximg.net/icon.jpeg",
}}}).encode()


def _router(routes, calls=None):
    """按 URL 片段派发的假 transport。没有匹配就回 404。"""
    def call(request, timeout, max_bytes):
        if calls is not None:
            calls.append(request.url)
        for fragment, response in routes.items():
            if fragment in request.url:
                return response
        return HttpResponse(404, {}, b"not found")
    return call


class _DiscoveryCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.secrets = self.root / "secrets"
        self.state = self.root / "state"

    def _write_credential(self, provider, values):
        path = self.secrets / "follow" / f"{provider}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values), encoding="utf-8")

    def _discover(self, term, routes, providers=None, calls=None):
        return discover(term, secrets_root=self.secrets, state_root=self.state,
                        transport=_router(routes, calls), providers=providers)


class SearchVariantTests(unittest.TestCase):
    def test_camel_case_is_split_because_full_text_search_matches_words(self):
        # f95 搜 `lazyprocrastinator` 搜不到，搜 `lazy procrastinator` 能。
        self.assertEqual(search_variants("LazyProcrastinator"),
                         ("LazyProcrastinator", "Lazy Procrastinator"))

    def test_an_all_lowercase_run_has_no_reliable_split(self):
        # 全小写连写没有切分信号，就只用原词，不去猜词典。
        self.assertEqual(search_variants("lazyprocrastinator"), ("lazyprocrastinator",))

    def test_an_underscored_name_is_also_tried_as_words(self):
        self.assertEqual(search_variants("initial_a"), ("initial_a", "initial a"))

    def test_a_handle_is_also_tried_with_the_other_separators(self):
        # 手柄写作 `Ria_neearts`，rule34.xxx 上的标签是 `ria-neearts`。
        self.assertEqual(spelling_variants("Ria_neearts"),
                         ("Ria_neearts", "Ria-neearts", "Rianeearts", "Ria neearts"))

    def test_the_forum_search_ends_with_a_prefix_wildcard(self):
        # 2026-09-12 实测：`strauz` 站内命中 0 条，`strauz*` 与全名一样命中 3 条。
        self.assertEqual(forum_queries("strauz"), ("strauz", "strauz*"))

    def test_a_short_term_is_not_widened(self):
        # 三个字母加通配等于把半个站搜回来，命中一屏也认不出是哪个作者。
        self.assertEqual(forum_queries("ria"), ("ria",))

    def test_a_term_that_already_has_a_wildcard_is_left_alone(self):
        self.assertEqual(forum_queries("strauz*"), ("strauz*",))


class DiscoveryPlanTests(unittest.TestCase):
    def test_a_handle_includes_the_name_searching_sources(self):
        tasks = discovery_plan("suzutaro3d")
        self.assertEqual(tasks, DEFAULT_PROVIDERS)

    def test_f95zone_is_asked_first_so_its_profile_handles_reach_the_rest(self):
        self.assertEqual(discovery_plan("cekc")[0], "f95zone")
        self.assertEqual(discovery_plan("Lazy", providers=("kemono", "f95zone")),
                         ("f95zone", "kemono"))

    def test_a_numeric_word_skips_the_name_searching_sources(self):
        tasks = discovery_plan("30917150")
        for skipped in ("rule34video", "rule34xxx", "simpcity"):
            self.assertNotIn(skipped, tasks)

    def test_a_restricted_provider_paid_respects_the_selection(self):
        self.assertEqual(discovery_plan("Lazy", providers=("kemono",)),
                         ("kemono",))

    def test_a_handle_without_separators_has_only_itself(self):
        self.assertEqual(spelling_variants("lazyprocrastinator"), ("lazyprocrastinator",))

    def test_the_identity_key_ignores_separators_and_case(self):
        self.assertEqual(identity_key("Ria_neearts"), identity_key("ria-neearts"))
        self.assertNotEqual(identity_key("Ria_neearts"), identity_key("riahri"))

    def test_the_identity_key_keeps_names_written_in_cjk(self):
        # 中日文名不能被抹成空串：空键会让 rule34.xxx 那一路直接认定无从比对。
        self.assertEqual(identity_key("鈴太郎_3D"), "鈴太郎3d")
        self.assertEqual(identity_key("鈴太郎_3D"), identity_key("鈴太郎-3d"))
        self.assertEqual(identity_key("うるしばら"), "うるしばら")

    def test_a_spaced_term_is_left_alone(self):
        self.assertEqual(search_variants("Lazy Procrastinator"), ("Lazy Procrastinator",))


class Rule34VideoSlugTests(unittest.TestCase):
    def test_word_breaks_become_hyphens_before_they_are_dropped(self):
        # KVS 的 slug 是 `bulging-senpai`；连写的 `bulgingsenpai` 回 404。
        self.assertEqual(rule34video_slugs("Bulging Senpai"), ("bulging-senpai", "bulgingsenpai"))

    def test_a_joined_name_tries_itself_first_then_its_camel_split(self):
        self.assertEqual(rule34video_slugs("BulgingSenpai"), ("bulgingsenpai", "bulging-senpai"))
        self.assertEqual(rule34video_slugs("lazyprocrastinator"), ("lazyprocrastinator",))

    def test_a_name_without_latin_letters_has_no_slug(self):
        self.assertEqual(rule34video_slugs("鈴太郎"), ())


class CreatorIndexTests(_DiscoveryCase):
    def _index(self, calls=None):
        return CreatorIndex(self.state, transport=_router(
            {"/api/v1/creators": HttpResponse(200, {}, CREATORS)}, calls))

    def test_the_index_is_downloaded_once_and_then_cached(self):
        calls = []
        index = self._index(calls)
        self.assertEqual(len(index.load("kemono")), 4)
        self.assertEqual(len(index.load("kemono")), 4)
        self.assertEqual(len(calls), 1, "第二次不该再下载")

    def test_a_stale_cache_is_refreshed(self):
        calls = []
        index = self._index(calls)
        index.load("kemono")
        # `now` 是墙钟时间，不是相对量：要比文件 mtime 晚过一个 TTL 才算过期。
        index.load("kemono", now=time.time() + CREATOR_INDEX_TTL_SECONDS * 2)
        self.assertEqual(len(calls), 2)

    def test_a_broken_payload_is_an_error_not_an_empty_index(self):
        index = CreatorIndex(self.state, transport=_router(
            {"/api/v1/creators": HttpResponse(200, {}, b'{"nope": 1}')}))
        with self.assertRaises(FollowSourceError):
            index.load("kemono")


class DiscoverTests(_DiscoveryCase):
    ROUTES = {
        "/api/v1/creators": HttpResponse(200, {}, CREATORS),
        "/fanbox/creator/30917150": HttpResponse(200, {}, FANBOX_PROFILE),
        "creator.get?creatorId=lazyprocrast": HttpResponse(200, {}, FANBOX_CREATOR),
        "rule34video.com/models/lazyprocrastinator/": HttpResponse(200, {}, b"<html/>"),
        "cat=animations": HttpResponse(200, {}, F95_HIT),
        "latest_data.php": HttpResponse(200, {}, F95_MISS),
    }

    def test_a_name_is_found_across_every_source_that_has_it(self):
        found = self._discover("LazyProcrastinator", self.ROUTES,
                               providers=("kemono", "rule34video", "f95zone"))
        by_provider = {c.provider: c for c in found.candidates}
        self.assertEqual(by_provider["kemono"].ref, "fanbox/30917150")
        self.assertEqual(by_provider["rule34video"].ref, "lazyprocrastinator")
        self.assertEqual(by_provider["f95zone"].ref, "50685")

    def test_a_spaced_name_finds_the_hyphenated_rule34video_page(self):
        calls = []
        found = self._discover("Bulging Senpai", {
            "rule34video.com/models/bulging-senpai/": HttpResponse(200, {}, b"<html/>"),
        }, providers=("rule34video",), calls=calls)
        self.assertEqual([(c.ref, c.url) for c in found.candidates],
                         [("bulging-senpai", "https://rule34video.com/models/bulging-senpai/")])
        # 第一个写法就命中，后面的不再探。
        self.assertEqual(calls, ["https://rule34video.com/models/bulging-senpai/"])

    def test_each_source_reports_itself_before_it_runs(self):
        notes = []
        found = discover("LazyProcrastinator", secrets_root=self.secrets,
                         state_root=self.state, transport=_router(self.ROUTES),
                         providers=("kemono", "fanbox"),
                         on_progress=lambda *args: notes.append(args))
        self.assertEqual(notes, [("kemono", 0, 2), ("fanbox", 1, 2)])
        self.assertTrue(found.candidates)

    def test_every_candidate_says_why_it_matched(self):
        found = self._discover("LazyProcrastinator", self.ROUTES,
                               providers=("kemono", "rule34video", "f95zone"))
        self.assertTrue(all(c.evidence for c in found.candidates))
        kemono = next(c for c in found.candidates if c.provider == "kemono")
        self.assertEqual(kemono.evidence, "创作者名精确匹配")

    def test_archive_identity_resolves_to_the_official_fanbox_page(self):
        found = self._discover("LazyProcrastinator", self.ROUTES,
                               providers=("kemono", "fanbox"))
        official = next(c for c in found.candidates if c.provider == "fanbox")
        self.assertEqual(official.ref, "lazyprocrast")
        self.assertEqual(official.url, "https://lazyprocrast.fanbox.cc/")
        self.assertEqual(official.label, "LazyProcrastinator")
        self.assertIn("官方资料", official.evidence)

    def test_a_numeric_term_matches_the_site_id_and_skips_name_only_sources(self):
        calls = []
        found = self._discover("30917150", self.ROUTES, calls=calls)
        self.assertEqual([c.ref for c in found.candidates if c.provider == "kemono"],
                         ["fanbox/30917150"])
        # 纯数字不去 rule34video / rule34.xxx 碰运气——那两个都是按名字/标签查的。
        self.assertFalse(any("/models/" in url for url in calls))

    def test_a_handle_may_be_the_creator_id_itself(self):
        # patreon 归档的 id 就是创作者手柄：`suzutaro3d` 按 id 命中，
        # 不需要它的日文名也写进索引。
        found = self._discover("suzutaro3d", self.ROUTES, providers=("kemono",))
        self.assertEqual([c.ref for c in found.candidates],
                         ["patreon/suzutaro3d"])
        self.assertEqual(found.candidates[0].evidence, "站内 id 精确匹配")

    def test_a_handle_id_matches_whatever_case_it_was_copied_in(self):
        # 手柄的大小写只是显示形态：从主页标题复制来的 `SuzuTaro3D` 和索引里的
        # `suzutaro3d` 是同一个创作者。按名字比对已经这么判，按 id 也要一样。
        found = self._discover("SuzuTaro3D", self.ROUTES, providers=("kemono",))
        self.assertEqual([c.ref for c in found.candidates],
                         ["patreon/suzutaro3d"])
        self.assertEqual(found.candidates[0].evidence, "站内 id 精确匹配")

    def test_a_numeric_term_probes_the_thread_id(self):
        routes = {**self.ROUTES, "f95zone.to/threads/50685/": HttpResponse(200, {}, b"<html/>")}
        found = self._discover("50685", routes, providers=("f95zone",))
        self.assertEqual([c.ref for c in found.candidates], ["50685"])
        self.assertEqual(found.candidates[0].evidence, "线程存在")

    def test_a_missing_thread_yields_nothing_rather_than_a_guessed_link(self):
        found = self._discover("99999999", self.ROUTES, providers=("f95zone",))
        self.assertEqual(found.candidates, ())

    def test_an_f95_miss_offers_google_without_guessing_a_thread(self):
        found = self._discover(
            "initial_a", {"latest_data.php": HttpResponse(200, {}, F95_MISS)},
            providers=("f95zone",))
        self.assertEqual(found.candidates, ())
        self.assertEqual(len(found.external_searches), 1)
        search = found.external_searches[0]
        self.assertEqual(search.provider, "f95zone")
        self.assertEqual(search.query, "initial_a f95zone")
        self.assertEqual(search.url,
                         "https://www.google.com/search?q=initial_a+f95zone")
        self.assertIn("真实线程链接", search.evidence)

    def test_an_f95_hit_does_not_add_a_redundant_google_link(self):
        found = self._discover("LazyProcrastinator", self.ROUTES,
                               providers=("f95zone",))
        self.assertTrue(found.candidates)
        self.assertEqual(found.external_searches, ())

    def test_a_partial_name_match_is_reported_as_partial(self):
        found = self._discover("Lazy", self.ROUTES, providers=("kemono",))
        evidence = {c.label: c.evidence for c in found.candidates}
        self.assertIn("LazyProcrastinator · fanbox", evidence)
        self.assertEqual(evidence["LazyProcrastinator · fanbox"], "创作者名以该词开头")

    def test_a_name_that_only_contains_the_term_says_so(self):
        found = self._discover("Mazer", self.ROUTES, providers=("kemono",))
        self.assertEqual([(c.label, c.evidence) for c in found.candidates],
                         [("LazyMazer · patreon", "创作者名包含该词")])

    def test_one_source_failing_does_not_lose_the_others(self):
        routes = {**self.ROUTES}
        del routes["/api/v1/creators"]
        found = self._discover("LazyProcrastinator", routes,
                               providers=("kemono", "rule34video"))
        self.assertEqual([c.provider for c in found.candidates], ["rule34video"])
        self.assertIn("kemono", found.failures)

    def test_a_handle_finds_the_tag_spelled_differently_on_the_site(self):
        # 站上是 `ria-neearts`，手边的手柄是 `Ria_neearts`。按手柄逐字查是零命中，
        # 补全把两种写法对上，登记的是站上的那个写法。
        routes = {"autocomplete.php?q=Ria_neearts": HttpResponse(200, {}, b"[]"),
                  "autocomplete.php?q=Ria-neearts": HttpResponse(200, {}, R34_AUTOCOMPLETE_HIT)}
        found = self._discover("Ria_neearts", routes, providers=("rule34xxx",))
        self.assertEqual([c.ref for c in found.candidates], ["ria-neearts"])
        self.assertIn("248", found.candidates[0].evidence)
        self.assertIn("tags=ria-neearts", found.candidates[0].url)

    def test_the_tag_search_needs_no_credentials_of_its_own(self):
        routes = {"autocomplete.php": HttpResponse(200, {}, R34_AUTOCOMPLETE_HIT)}
        found = self._discover("ria-neearts", routes, providers=("rule34xxx",))
        self.assertEqual([c.ref for c in found.candidates], ["ria-neearts"])
        self.assertEqual(found.failures, {})

    def test_an_exact_hit_leads_its_prefix_neighbors(self):
        # 精确写法排最前；下拉里同时给过的相似标签照列在后，由人分辨是不是同一个人。
        routes = {"autocomplete.php": HttpResponse(200, {}, b"["
                  b"{\"label\": \"ria-neearts (248)\", \"value\": \"ria-neearts\"},"
                  b"{\"label\": \"riahri (156)\", \"value\": \"riahri\"},"
                  b"{\"label\": \"ria (12)\", \"value\": \"ria\"}]")}
        found = self._discover("ria", routes, providers=("rule34xxx",))
        self.assertEqual([c.ref for c in found.candidates], ["ria", "ria-neearts", "riahri"])
        self.assertEqual(found.candidates[0].evidence, "站内标签 ria 下有 12 件作品")
        self.assertEqual(found.candidates[1].evidence,
                         "站内标签以该词开头：ria-neearts 下有 248 件作品")

    def test_a_prefix_hit_is_a_candidate_when_no_spelling_matched_exactly(self):
        # 敲名字的开头、站上的标签比它长时，精确判据下这条也找不到。前缀命中的
        # 写法仍是站上真实存在的标签，照列出来、证据写明以该词开头，由人分辨。
        # 没有凭据就不去 dapi 确认精确写法，只问补全。
        routes = {"autocomplete.php": HttpResponse(200, {}, R34_AUTOCOMPLETE_HIT)}
        calls = []
        found = self._discover("ria", routes, providers=("rule34xxx",), calls=calls)
        self.assertFalse(any("index.php" in url for url in calls))
        self.assertEqual([c.ref for c in found.candidates], ["ria-neearts", "riahri"])
        self.assertEqual(found.candidates[0].evidence,
                         "站内标签以该词开头：ria-neearts 下有 248 件作品")
        self.assertEqual(found.candidates[1].evidence,
                         "站内标签以该词开头：riahri 下有 156 件作品")
        self.assertIn("tags=ria-neearts", found.candidates[0].url)

    def test_a_prefix_hit_without_a_count_still_states_itself(self):
        routes = {"autocomplete.php": HttpResponse(200, {}, b"["
                  b"{\"label\": \"forceball_fx\", \"value\": \"forceball_fx\"}]")}
        found = self._discover("forceball", routes, providers=("rule34xxx",))
        self.assertEqual([c.ref for c in found.candidates], ["forceball_fx"])
        self.assertEqual(found.candidates[0].evidence,
                         "站内存在以该词开头的标签 forceball_fx")

    def test_prefix_neighbors_do_not_shadow_the_variant_that_matched_exactly(self):
        # 补全按字面前缀返回：`Ria_neearts` 查不出 `ria-neearts`，要换分隔符写法
        # 再问一次。前一种写法带回来的相似标签不能盖住后一种写法的精确命中。
        routes = {"autocomplete.php?q=Ria_neearts": HttpResponse(200, {}, R34_AUTOCOMPLETE_HIT),
                  "autocomplete.php?q=Ria-neearts": HttpResponse(200, {}, R34_AUTOCOMPLETE_HIT)}
        found = self._discover("Ria_neearts", routes, providers=("rule34xxx",))
        self.assertEqual([c.ref for c in found.candidates], ["ria-neearts"])

    def _typed_r34(self):
        self._write_credential("rule34xxx", {"user_id": "1", "api_key": "k"})
        # 分类缓存是模块级的，留到下一个用例里就会让它凭空少打几次请求。
        follow_discovery._TAG_TYPES.clear()
        self.addCleanup(follow_discovery._TAG_TYPES.clear)

    def test_the_exact_tag_pushed_out_of_the_autocomplete_still_leads(self):
        # 补全一次只回十条，热门前缀会把完整写法挤出去。有凭据时按原样问一次 dapi，
        # 标签下有帖子就是精确命中，排在前缀命中之前；前缀命中照列。
        self._typed_r34()
        post = json.dumps([{"id": 1, "image": "a.jpg", "tags": "ria",
                            "file_url": "https://api-cdn.rule34.xxx/images/1/a.jpg"}])
        routes = {"autocomplete.php": HttpResponse(200, {}, R34_AUTOCOMPLETE_HIT),
                  "s=post": HttpResponse(200, {}, post.encode()),
                  "s=tag": HttpResponse(200, {}, b'<tags type="array"></tags>')}
        found = self._discover("ria", routes, providers=("rule34xxx",))
        self.assertEqual([c.ref for c in found.candidates], ["ria", "ria-neearts", "riahri"])
        self.assertEqual(found.candidates[0].evidence, "站内标签 ria 下有作品")
        self.assertEqual(found.failures, {})

    def test_a_rejected_credential_keeps_the_prefix_hits(self):
        # 确认精确写法那一问只是锦上添花；凭据被拒时补全给的前缀命中照列。
        self._typed_r34()
        rejected = HttpResponse(200, {}, b'"Missing authentication. Go to api.rule34.xxx"')
        routes = {"autocomplete.php": HttpResponse(200, {}, R34_AUTOCOMPLETE_HIT),
                  "s=post": rejected, "s=tag": rejected}
        found = self._discover("ria", routes, providers=("rule34xxx",))
        self.assertEqual([c.ref for c in found.candidates], ["ria-neearts", "riahri"])
        self.assertEqual(found.failures, {})

    def test_an_f95_collection_thread_is_found_by_the_forum_search(self):
        # `latest_data.php` 只索引 Latest Updates，艺术家的 Collection 帖不在里面。
        self._write_credential("f95zone", {"cookie": "xf_user=1"})
        routes = {"latest_data.php": HttpResponse(200, {}, F95_MISS),
                  "/search/search": HttpResponse(200, {}, F95_SEARCH_RESULTS),
                  "f95zone.to/search/": HttpResponse(200, {}, F95_SEARCH_FORM)}
        found = self._discover("Ria_neearts", routes, providers=("f95zone",))
        self.assertEqual([c.ref for c in found.candidates], ["146348"])
        self.assertEqual(found.candidates[0].label,
                         "Ria Collection [2026-08-03] [Ria_neearts]")
        self.assertEqual(found.external_searches, ())

    def test_half_a_name_is_retried_as_a_prefix(self):
        """站内搜索按整词匹配，半个名字要靠尾部通配才找得到。

        2026-09-12 实测 `strauz` 命中 0 条，`strauz*` 命中三条，其中就有
        `Strauzek Collection [2026-09-04] [Mr_Strauz]`。
        """
        self._write_credential("f95zone", {"cookie": "xf_user=1"})
        queries = []

        def call(request, timeout, max_bytes):
            if "latest_data.php" in request.url:
                return HttpResponse(200, {}, F95_MISS)
            if "/search/search" not in request.url:
                return HttpResponse(200, {}, F95_SEARCH_FORM)
            body = urllib.parse.parse_qs((request.body or b"").decode("utf-8"))
            query = body.get("keywords", [""])[0]
            queries.append(query)
            return HttpResponse(200, {}, F95_SEARCH_RESULTS if query.endswith("*")
                                else F95_SEARCH_NOTHING)

        found = discover("strauz", secrets_root=self.secrets, state_root=self.state,
                         transport=call, providers=("f95zone",))
        self.assertEqual(queries, ["strauz", "strauz*"])
        self.assertEqual([c.ref for c in found.candidates], ["146348"])
        self.assertIn("按标题开头命中「strauz*」", found.candidates[0].evidence)

    def test_an_exact_hit_never_reaches_the_wildcard_round(self):
        self._write_credential("f95zone", {"cookie": "xf_user=1"})
        queries = []

        def call(request, timeout, max_bytes):
            if "latest_data.php" in request.url:
                return HttpResponse(200, {}, F95_MISS)
            if "/search/search" not in request.url:
                return HttpResponse(200, {}, F95_SEARCH_FORM)
            queries.append(urllib.parse.parse_qs(
                (request.body or b"").decode("utf-8")).get("keywords", [""])[0])
            return HttpResponse(200, {}, F95_SEARCH_RESULTS)

        discover("strauzek", secrets_root=self.secrets, state_root=self.state,
                 transport=call, providers=("f95zone",))
        self.assertEqual(queries, ["strauzek"])

    def test_f95_profile_handles_are_searched_on_the_other_sources(self):
        """搜 `cekc` 在 rule34.xxx 上碰不到 `Ceeeeekc`；F95 首楼名片上写着这个手柄。"""
        self._write_credential("f95zone", {"cookie": "xf_user=1"})
        routes = {"latest_data.php": HttpResponse(200, {}, F95_MISS),
                  "/search/search": HttpResponse(200, {}, F95_SEARCH_RESULTS),
                  "f95zone.to/search/": HttpResponse(200, {}, F95_SEARCH_FORM),
                  "f95zone.to/threads/146348/": HttpResponse(200, {}, F95_PROFILE_PAGE),
                  "autocomplete.php?q=cekc": HttpResponse(200, {}, b"[]"),
                  "autocomplete.php?q=Ceeeeekc": HttpResponse(200, {}, R34_CEEEEEKC)}
        found = self._discover("cekc", routes, providers=("rule34xxx", "f95zone"))
        self.assertEqual([(c.provider, c.ref) for c in found.candidates],
                         [("f95zone", "146348"), ("rule34xxx", "ceeeeekc")])
        f95, tag = found.candidates
        self.assertEqual(f95.aliases, ("Ceeeeekc",), "与检索词同名的 patreon 手柄和 pixiv 数字 id 不算别名")
        self.assertEqual(tag.aliases, ("Ceeeeekc",))
        self.assertTrue(tag.evidence.startswith("F95 首楼名片上的手柄 Ceeeeekc："))

    def test_without_a_cookie_the_forum_search_is_skipped_not_guessed(self):
        calls = []
        found = self._discover("Ria_neearts", {"latest_data.php": HttpResponse(200, {}, F95_MISS)},
                               providers=("f95zone",), calls=calls)
        self.assertEqual(found.candidates, ())
        self.assertEqual(found.failures, {})
        self.assertEqual([search.provider for search in found.external_searches], ["f95zone"])
        self.assertFalse([url for url in calls if "/search/" in url])

    SIMPCITY_ROUTES = {
        "simpcity.cr/search/search": HttpResponse(200, {}, SIMPCITY_SEARCH_RESULTS),
        "simpcity.cr/search/": HttpResponse(200, {}, SIMPCITY_SEARCH_FORM),
    }

    def test_a_simpcity_thread_is_found_by_the_forum_search_with_its_forum_label(self):
        self._write_credential("simpcity", {"cookie": "yMziCv8BrCZz1o7_user=u"})
        found = self._discover("solazola", self.SIMPCITY_ROUTES, providers=("simpcity",))
        self.assertEqual([(c.provider, c.ref) for c in found.candidates],
                         [("simpcity", "17401"), ("simpcity", "392510")])
        self.assertEqual(found.candidates[0].url, "https://simpcity.cr/threads/17401/")
        self.assertEqual(found.candidates[0].label, "Solazola / baby_sue")
        self.assertEqual(found.candidates[0].evidence, "站内搜索按标题命中「solazola」，版块标签 OnlyFans")
        self.assertIn("Simp Chat", found.candidates[1].evidence)
        self.assertEqual(found.failures, {})

    def test_simpcity_is_asked_by_default_when_a_name_is_looked_up(self):
        self._write_credential("simpcity", {"cookie": "yMziCv8BrCZz1o7_user=u"})
        found = self._discover("solazola", {**self.ROUTES, **self.SIMPCITY_ROUTES})
        self.assertIn("simpcity", {c.provider for c in found.candidates})

    def test_without_a_simpcity_cookie_the_site_is_skipped_silently(self):
        # 来源筛选菜单里已经标着「需要配置凭据」，每查一个名字都再报一次只是噪音。
        calls = []
        found = self._discover("solazola", self.SIMPCITY_ROUTES, providers=("simpcity",), calls=calls)
        self.assertEqual(found.candidates, ())
        self.assertEqual(found.failures, {})
        self.assertEqual(calls, [])

    def test_a_bare_number_is_not_probed_on_simpcity(self):
        # 登录后任何数字的线程页都可能存在，「存在」不构成「就是他」的证据。
        self._write_credential("simpcity", {"cookie": "yMziCv8BrCZz1o7_user=u"})
        calls = []
        found = self._discover("17401", self.SIMPCITY_ROUTES, providers=("simpcity",), calls=calls)
        self.assertEqual(found.candidates, ())
        self.assertEqual(calls, [])

    def test_a_stale_simpcity_cookie_is_reported_as_a_failure_not_an_empty_result(self):
        self._write_credential("simpcity", {"cookie": "stale"})
        found = self._discover("solazola", {"simpcity.cr/search/": HttpResponse(403, {}, b"Forbidden")},
                               providers=("simpcity",))
        self.assertEqual(found.candidates, ())
        self.assertIn("cookie", found.failures["simpcity"])

    def test_junk_terms_are_refused_before_any_request(self):
        calls = []
        for term in ("", "   ", "../etc", "a" * 200, "<script>", "a?b", "a&b"):
            with self.assertRaises(FollowSourceError):
                self._discover(term, self.ROUTES, calls=calls)
        self.assertEqual(calls, [])

    def test_japanese_and_chinese_creator_names_are_accepted(self):
        # kemono 上大量创作者是日文或中文名，ASCII 白名单会把他们整批挡在门外。
        creators = json.dumps([
            {"id": "1", "name": "うるしばら", "service": "fanbox"},
            {"id": "2", "name": "冰鲜鱼子酱二代目", "service": "fanbox"},
        ]).encode()
        routes = {"/api/v1/creators": HttpResponse(200, {}, creators)}
        for term, expected in (("うるしばら", "1"), ("冰鲜鱼子酱二代目", "2")):
            found = self._discover(term, routes, providers=("kemono",))
            self.assertEqual([c.ref for c in found.candidates], [f"fanbox/{expected}"], term)

    def test_results_are_capped_per_source(self):
        many = json.dumps([{"id": str(i), "name": f"Lazy{i}", "service": "fanbox"}
                           for i in range(50)]).encode()
        found = self._discover("Lazy", {"/api/v1/creators": HttpResponse(200, {}, many)},
                               providers=("kemono",))
        self.assertEqual(len(found.candidates), 8)


class SuggestTermTests(unittest.TestCase):
    def test_one_letter_is_too_little_to_suggest_from(self):
        # 一个字母在十万条清单里命中几千个名字，排在最前的那几条和谁都没关系。
        self.assertEqual(suggest_term("l"), "")

    def test_a_pasted_link_is_not_a_name_to_guess_from(self):
        # 地址里必有 `/`，那时该走的是解析链接。
        self.assertEqual(suggest_term("https://kemono.cr/fanbox/user/123"), "")

    def test_a_name_comes_back_trimmed(self):
        self.assertEqual(suggest_term("  lewdgazer "), "lewdgazer")

    def test_a_term_longer_than_a_name_is_refused(self):
        self.assertEqual(suggest_term("l" * (MAX_TERM_LENGTH + 1)), "")


class ArchiveSuggestionTests(unittest.TestCase):
    """本机清单的前缀建议。清单是 `discover` 下过之后留在硬盘上的那一份。"""

    CREATORS = [
        {"id": "1", "name": "lewdgazer", "service": "fanbox"},
        {"id": "2", "name": "Lewdgatta", "service": "patreon"},
        {"id": "3", "name": "LewdGamesDev", "service": "fanbox"},
        {"id": "4", "name": "notlewdgazer", "service": "fanbox"},
    ]

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "state"

    def _write(self, provider, rows):
        path = self.state / "follow" / f"creators-{provider}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows), encoding="utf-8")
        return path

    def test_half_a_name_finds_the_creators_that_start_with_it(self):
        """打 `lewdga` 要出 `lewdgazer`——这是建议存在的全部理由。

        前缀一样时短的排前面：多出来的字越少，越可能就是这个名字本身。
        """
        self._write("kemono", self.CREATORS)
        found = archive_suggestions("lewdga", state_root=self.state,
                                    providers=("kemono",))
        self.assertEqual([row.value for row in found],
                         ["Lewdgatta", "lewdgazer", "LewdGamesDev"])

    def test_the_middle_of_a_name_is_not_a_prefix(self):
        # 子串匹配要全扫十万条乘三个站，跟不上手速；`notlewdgazer` 因此不在结果里。
        self._write("kemono", self.CREATORS)
        self.assertEqual(archive_suggestions("gazer", state_root=self.state,
                                             providers=("kemono",)), ())

    def test_the_same_person_on_two_sites_is_one_suggestion(self):
        # 大小写只是拼写形态，留先问到的那个站的写法。
        self._write("kemono", [{"id": "1", "name": "lewdgazer", "service": "fanbox"}])
        self._write("pawchive", [{"id": "9", "name": "LewdGazer", "service": "fanbox"}])
        found = archive_suggestions("lewdga", state_root=self.state,
                                    providers=("kemono", "pawchive"))
        self.assertEqual([(row.value, row.matched) for row in found],
                         [("lewdgazer", "kemono")])

    def test_an_exact_name_comes_first(self):
        self._write("kemono", self.CREATORS)
        found = archive_suggestions("lewdgazer", state_root=self.state,
                                    providers=("kemono",))
        self.assertEqual([row.value for row in found], ["lewdgazer"])

    def test_a_missing_index_is_simply_no_suggestions(self):
        """清单没下过就没有这一组，**不为建议下载整站清单**。

        清单是几 MB 的东西，为一次敲键现下它会让输入框卡住十几秒。
        """
        self.assertEqual(archive_suggestions("lewdga", state_root=self.state), ())

    def test_a_refreshed_index_is_picked_up(self):
        # 清单一天刷一次；内存里那份表按文件 mtime 认新旧。
        path = self._write("kemono",
                           [{"id": "1", "name": "lewdgazer", "service": "fanbox"}])
        self.assertEqual(len(archive_suggestions("lewdga", state_root=self.state,
                                                 providers=("kemono",))), 1)
        self._write("kemono", self.CREATORS)
        later = time.time() + 10
        os.utime(path, (later, later))
        self.assertEqual(len(archive_suggestions("lewdga", state_root=self.state,
                                                 providers=("kemono",))), 3)


class TagSuggestionTests(unittest.TestCase):
    ROWS = json.dumps([{"label": "lewdgatta (380)", "value": "lewdgatta"},
                       {"label": "lewdgazer (237)", "value": "lewdgazer"}]).encode()
    #: dapi 的 tag 接口只回 XML，`json=1` 实测被忽略。1 是 artist、0 是 general。
    TYPES = {
        "lewdgatta": b'<?xml version="1.0"?><tags type="array">'
                     b'<tag type="1" count="380" name="lewdgatta" id="1"/></tags>',
        "lewdgazer": b'<?xml version="1.0"?><tags type="array">'
                     b'<tag type="0" count="237" name="lewdgazer" id="2"/></tags>',
    }

    def setUp(self):
        # 分类缓存是模块级的，留到下一个用例里就会让它凭空少打几次请求。
        follow_discovery._TAG_TYPES.clear()
        self.addCleanup(follow_discovery._TAG_TYPES.clear)
        self.credential = Credential("rule34xxx", {"user_id": "1", "api_key": "k"})

    def _transport(self, seen=None):
        def call(request, _timeout, _max_bytes):
            if seen is not None:
                seen.append(request.url)
            if "autocomplete.php" in request.url:
                return HttpResponse(200, {"content-type": "text/html"}, self.ROWS)
            name = urllib.parse.parse_qs(
                urllib.parse.urlsplit(request.url).query)["name"][0]
            return HttpResponse(200, {"content-type": "text/xml"}, self.TYPES[name])
        return call

    def test_the_site_taxonomy_decides_who_is_an_author(self):
        """谁是作者由站方的分类说了算，不由名字的样子说了算。

        实测 `lewdchuu_(artist)` 名字里带 `artist` 只是巧合，`lewdtuber` 是
        metadata，而 `lewd`（8548 帖）是普通标签——按词形猜会三条全错。
        """
        found = tag_suggestions("lewdga", transport=self._transport(),
                                credential=self.credential)
        self.assertEqual([(row.value, row.tag_type, row.matched) for row in found],
                         [("lewdgatta", "artist", "作者"),
                          ("lewdgazer", "general", "标签")])

    def test_without_a_credential_the_class_is_left_unknown(self):
        """分类接口要凭据，没有就只报名字——空的 `tag_type` 是「没问出来」。

        它和「问出来是普通标签」不是一回事，所以不能拿去分组。
        """
        seen = []
        found = tag_suggestions("lewdga", transport=self._transport(seen))
        self.assertEqual([(row.value, row.tag_type) for row in found],
                         [("lewdgatta", ""), ("lewdgazer", "")])
        self.assertEqual(seen, ["https://api.rule34.xxx/autocomplete.php?q=lewdga"])

    def test_a_class_asked_once_is_not_asked_again(self):
        """前缀越敲越长，后几下要问的名字前一下刚问过。

        站方额度是每 60 秒 60 次，一组八条就用掉八次；不记住的话光敲一个词就能
        把额度打满。
        """
        seen = []
        transport = self._transport(seen)
        tag_suggestions("lewdga", transport=transport, credential=self.credential)
        types = [url for url in seen if "s=tag" in url]
        seen.clear()
        tag_suggestions("lewdga", transport=transport, credential=self.credential)
        self.assertEqual(len(types), 2)
        self.assertEqual([url for url in seen if "s=tag" in url], [])

    def test_one_tag_that_will_not_answer_keeps_the_rest(self):
        # 一条问不出来只是它自己没有分类，别的条目照常分组。
        def call(request, _timeout, _max_bytes):
            if "autocomplete.php" in request.url:
                return HttpResponse(200, {}, self.ROWS)
            if "lewdgazer" in request.url:
                raise OSError("connection reset")
            return HttpResponse(200, {}, self.TYPES["lewdgatta"])

        with no_real_backoff():
            found = tag_suggestions("lewdga", transport=call, credential=self.credential)
        self.assertEqual([(row.value, row.tag_type) for row in found],
                         [("lewdgatta", "artist"), ("lewdgazer", "")])

    def test_the_public_autocomplete_answers_without_a_credential(self):
        """2026-09-12 实测 `lewdga` 回 `lewdgatta`(380)、`lewdgazer`(237)、`lewdgala`(3)。

        走的是 `api.rule34.xxx`：主站上那个同名地址被 Cloudflare 挡着，实测 403。
        """
        seen = []

        def call(request, _timeout, _max_bytes):
            seen.append(request.url)
            return HttpResponse(200, {"content-type": "text/html"}, self.ROWS)

        found = tag_suggestions("lewdga", transport=call)
        self.assertEqual([(row.value, row.count) for row in found],
                         [("lewdgatta", 380), ("lewdgazer", 237)])
        self.assertEqual(seen, ["https://api.rule34.xxx/autocomplete.php?q=lewdga"])

    def test_a_site_that_is_down_only_costs_its_own_group(self):
        """建议是锦上添花：站点挂了不该让本机那两组跟着消失，也不该让人等退避。

        连接器对 GET 失败的退避累计 15 秒，那是抓取任务的节奏；敲一下字问一次的路径
        站点一挂就该立刻回空。这里把真实的 sleep 换成一炸就红的桩，退避一旦回来
        就在这条上露出来。
        """
        def call(_request, _timeout, _max_bytes):
            raise OSError("connection reset")

        with no_real_backoff():
            self.assertEqual(tag_suggestions("lewdga", transport=call), ())

    def test_a_term_too_short_never_reaches_the_site(self):
        calls = []

        def call(request, _timeout, _max_bytes):
            calls.append(request.url)
            return HttpResponse(200, {}, self.ROWS)

        self.assertEqual(tag_suggestions("l", transport=call), ())
        self.assertEqual(calls, [])


class SearchKeepsWhatWasSuggestedTests(_DiscoveryCase):
    """添加框下拉里给过的名字，按回车查找之后必须还在结果里。"""

    def setUp(self):
        super().setUp()
        follow_discovery._TAG_TYPES.clear()
        self.addCleanup(follow_discovery._TAG_TYPES.clear)

    def test_archive_names_offered_for_a_prefix_survive_the_search(self):
        # 名字中间带这个词的九位排在清单前面；按清单原序截八条就轮不到下拉里那两位。
        rows = [{"id": str(n), "name": f"x{n}lewd", "service": "fanbox"} for n in range(9)]
        rows += [{"id": "90", "name": "lewdgazer", "service": "fanbox"},
                 {"id": "91", "name": "Lewdgatta", "service": "patreon"}]
        path = self.state / "follow" / "creators-kemono.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows), encoding="utf-8")
        offered = [row.value for row in archive_suggestions(
            "lewd", state_root=self.state, providers=("kemono",))]
        found = self._discover("lewd", {}, providers=("kemono",))
        self.assertEqual(offered, ["Lewdgatta", "lewdgazer"])
        self.assertEqual([c.label.split(" · ")[0] for c in found.candidates[:2]], offered)
        self.assertEqual(found.candidates[0].evidence, "创作者名以该词开头")

    def test_site_tags_offered_for_a_prefix_survive_the_search(self):
        """`lewd` 本身是标签，下拉里的作者 `lewdrex` 和角色 `lewd_dorky` 也得在结果里。

        排法跟下拉一致：精确写法最前，其余作者在前、别的分类在后，证据写明站上的分类。
        """
        self._write_credential("rule34xxx", {"user_id": "1", "api_key": "k"})
        rows = json.dumps([{"label": "lewd (8548)", "value": "lewd"},
                           {"label": "lewd_dorky (401)", "value": "lewd_dorky"},
                           {"label": "lewdrex (57)", "value": "lewdrex"}]).encode()
        #: dapi 的分类代号：0 是 general、1 是 artist、4 是 character。
        types = {"lewd": 0, "lewd_dorky": 4, "lewdrex": 1}
        calls = []

        def call(request, _timeout, _max_bytes):
            calls.append(request.url)
            if "autocomplete.php" in request.url:
                return HttpResponse(200, {}, rows)
            name = urllib.parse.parse_qs(urllib.parse.urlsplit(request.url).query)["name"][0]
            return HttpResponse(200, {}, (f'<tags type="array"><tag type="{types[name]}"'
                                          f' count="1" name="{name}"/></tags>').encode())

        offered = tag_suggestions("lewd", transport=call, credential=Credential(
            "rule34xxx", {"user_id": "1", "api_key": "k"}))
        found = discover("lewd", secrets_root=self.secrets, state_root=self.state,
                         transport=call, providers=("rule34xxx",))
        self.assertEqual({row.value for row in offered}, {c.ref for c in found.candidates})
        self.assertEqual([c.ref for c in found.candidates], ["lewd", "lewdrex", "lewd_dorky"])
        self.assertEqual([c.evidence for c in found.candidates],
                         ["站内标签 lewd 下有 8548 件作品",
                          "站内标签以该词开头：lewdrex 下有 57 件作品，站上归为作者",
                          "站内标签以该词开头：lewd_dorky 下有 401 件作品，站上归为角色"])
        # 补全里已经有精确写法，就不花一次 dapi 请求去确认它。
        self.assertFalse(any("s=post" in url for url in calls))


if __name__ == "__main__":
    unittest.main()
