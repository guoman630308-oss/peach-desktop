"""连接器基类共享骨架的隔离测试。

「条件请求 → 304 短路 → 状态检查」由 `_BaseConnector._request()` 一处实现，
八个连接器的 fetch 开头不各写一份，所以这些规则也只需要在这里守住；
各站的解析差异仍由 `test_follow_sources.py` 覆盖。测试不联网，transport 全部注入。
"""
import unittest
import httpx

from peach.follow import FollowSourceError
from peach.follow_sources import (
    CONNECTORS, KemonoConnector, _BaseConnector, display_thumb_url,
    is_history_end_error, official_profile_handle,
)
from peach.follow_store import _OFFICIAL_IDENTITY_PROVIDERS
from peach.http import HttpResponse


def _transport(status=200, headers=None, body=b"", record=None):
    def call(request, timeout, max_bytes):
        if record is not None:
            record.append(request)
        return HttpResponse(status, headers or {}, body)
    return call


class _Probe(_BaseConnector):
    provider = "probe"


class SharedRequestSkeletonTests(unittest.TestCase):
    def test_handshake_timeout_recovers_with_bounded_backoff(self):
        calls, waits, progress = [], [], []
        def transport(request, timeout, max_bytes):
            calls.append(request)
            if len(calls) < 5:
                raise httpx.ConnectTimeout('_ssl.c:1064: The handshake operation timed out')
            return HttpResponse(200, {}, b'{}')
        connector = _Probe(transport=transport, sleeper=waits.append)
        connector.progress = lambda **state: progress.append(state)
        self.assertEqual(connector._get('https://example.test').status, 200)
        self.assertEqual(waits, [1, 2, 4, 8])
        self.assertEqual(progress[-1]['attempt'], 5)

    def test_retries_exhaust_and_post_is_not_replayed(self):
        for method, expected in [('GET', 5), ('POST', 1)]:
            calls = []
            def transport(*args, calls=calls):
                calls.append(1)
                raise httpx.ReadTimeout('temporary')
            connector = _Probe(transport=transport, sleeper=lambda _: None)
            with self.assertRaises(FollowSourceError):
                connector._send(method, 'https://example.test', None, headers={}, base={})
            self.assertEqual(len(calls), expected)

    def test_access_denied_is_not_retried(self):
        calls, waits = [], []
        connector = _Probe(transport=_transport(status=403, record=calls), sleeper=waits.append)
        with self.assertRaises(FollowSourceError):
            connector._request('https://example.test', ref='r')
        self.assertEqual(len(calls), 1)
        self.assertEqual(waits, [])

    def test_first_page_sends_conditional_headers(self):
        seen = []
        connector = _Probe(transport=_transport(body=b"{}", record=seen))
        common, response = connector._request(
            "https://example.test/a", ref="r", etag='"v0"',
            last_modified="Wed, 13 Aug 2026 08:00:00 GMT")
        self.assertIsNotNone(response)
        self.assertEqual(seen[0].headers.get("If-None-Match"), '"v0"')
        self.assertEqual(seen[0].headers.get("If-Modified-Since"),
                         "Wed, 13 Aug 2026 08:00:00 GMT")
        self.assertEqual(common["ref"], "r")
        self.assertEqual(common["request_url"], "https://example.test/a")

    def test_paging_back_drops_conditional_headers(self):
        """往回翻页不能带条件请求头。

        `If-None-Match` 存的是第一页的 etag，拿它去问第二页，站点很可能回 304，
        表现就是「点了没反应」。这条规则过去在每个连接器里各写一遍，现在只有一处。"""
        seen = []
        connector = _Probe(transport=_transport(body=b"{}", record=seen))
        connector._request("https://example.test/a?o=50", ref="r", etag='"v0"',
                           last_modified="Wed, 13 Aug 2026 08:00:00 GMT", page=1)
        self.assertNotIn("If-None-Match", seen[0].headers)
        self.assertNotIn("If-Modified-Since", seen[0].headers)

    def test_not_modified_short_circuits_with_usable_common(self):
        connector = _Probe(transport=_transport(status=304, headers={"ETag": '"v1"'}))
        common, response = connector._request("https://example.test/a", ref="r")
        self.assertIsNone(response, "304 必须短路，调用方据此直接返回 not_modified")
        self.assertEqual(common["etag"], '"v1"')
        self.assertEqual(common["provider"], "probe")

    def test_request_url_can_be_masked_so_credentials_never_reach_evidence(self):
        """带凭据的真实 URL 绝不能落进证据档案。

        rule34xxx 的 `api_key` 在查询串里，必须传脱敏版本；`_request` 请求用真实 URL，
        `common` 里放脱敏 URL。"""
        seen = []
        connector = _Probe(transport=_transport(body=b"{}", record=seen))
        common, _ = connector._request(
            "https://example.test/a?api_key=secret", ref="r",
            request_url="https://example.test/a")
        self.assertIn("api_key=secret", seen[0].url, "请求本身仍要带真实凭据")
        self.assertNotIn("secret", str(common["request_url"]))

    def test_non_200_raises_before_the_caller_parses_anything(self):
        waits = []
        connector = _Probe(transport=_transport(status=500), sleeper=waits.append)
        with self.assertRaises(FollowSourceError):
            connector._request("https://example.test/a", ref="r")
        self.assertEqual(waits, [1, 2, 4, 8])


class SharedSendContractTests(unittest.TestCase):
    """`_get` 与 `_post` 只在动词和请求体上不同，其余规则必须是同一份实现。

    两份复制品已经开始分岔：`connector_headers=False`（跳到第三方主机时不带来源站
    Cookie）当初只加进了 GET 那一份。那是安全语义，不该取决于用的是哪个动词。
    """

    class _Blocked(_BaseConnector):
        provider = "blocked"
        blocked_reason = "站点有机器人验证"

    def test_both_verbs_refuse_a_blocked_site(self):
        connector = self._Blocked(transport=_transport())
        for call in (lambda: connector._get("https://example.test/a"),
                     lambda: connector._post("https://example.test/a", b"x")):
            with self.assertRaises(FollowSourceError):
                call()

    def test_both_verbs_reject_a_body_over_the_size_bound(self):
        connector = _Probe(transport=_transport(body=b"x" * 40), max_bytes=8)
        for call in (lambda: connector._get("https://example.test/a"),
                     lambda: connector._post("https://example.test/a", b"x")):
            with self.assertRaises(FollowSourceError):
                call()

    def test_both_verbs_can_drop_the_source_site_headers(self):
        """跨站请求不带来源站的 Cookie，POST 也一样。"""
        class _WithCookie(_BaseConnector):
            provider = "withcookie"

            def _headers(self):
                return {**super()._headers(), "Cookie": "session=secret"}

        seen = []
        connector = _WithCookie(transport=_transport(body=b"{}", record=seen))
        connector._get("https://third.test/a", connector_headers=False)
        connector._post("https://third.test/a", b"x", connector_headers=False)
        for request in seen:
            self.assertNotIn("Cookie", request.headers)
        connector._post("https://own.test/a", b"x")
        self.assertEqual(seen[-1].headers.get("Cookie"), "session=secret")

    def test_post_carries_the_body_and_get_does_not(self):
        seen = []
        connector = _Probe(transport=_transport(body=b"{}", record=seen))
        connector._get("https://example.test/a")
        connector._post("https://example.test/a", b"payload")
        self.assertEqual([(row.method, row.body) for row in seen],
                         [("GET", None), ("POST", b"payload")])

    def test_the_conditional_header_still_overrides_an_explicit_one(self):
        """`etag` 盖掉调用方显式给的同名头，合并顺序与抽取前一致。"""
        seen = []
        connector = _Probe(transport=_transport(body=b"{}", record=seen))
        connector._get("https://example.test/a", headers={"If-None-Match": '"old"'},
                       etag='"new"')
        self.assertEqual(seen[0].headers.get("If-None-Match"), '"new"')


class _Item:
    """已入库的一行里，读取时投影用得到的那几个字段。"""

    def __init__(self, provider, *, thumb_url=None, media_url=None, item_id=1, metadata=None):
        self.provider = provider
        self.thumb_url = thumb_url
        self.media_url = media_url
        self.id = item_id
        self.metadata = metadata or {}


class DisplayThumbUrlTests(unittest.TestCase):
    """「这个站的缩略图 URL 长什么样」属于连接器，不属于 Web 层。

    rule34.xxx 的 preview→sample 改写、归档站的 `img.` 子域、归档站旧行的缩略图
    推导，都归连接器。站点行为变了要改的是站点知识，而 Web 层根本不该知道
    `api-cdn.rule34.xxx` 这种主机名。
    """

    def test_rule34xxx_images_use_the_source_file_and_extension(self):
        """图片使用来源声明的原文件，视频使用来源缩略图。"""
        thumb = ("https://api-cdn.rule34.xxx/thumbnails/1234/"
                 "thumbnail_" + "a" * 32 + ".jpg")
        image = "https://api-cdn.rule34.xxx/images/1234/" + "a" * 32 + ".png"
        self.assertEqual(display_thumb_url(_Item("rule34xxx", thumb_url=thumb,
            media_url=image)), image)
        self.assertEqual(display_thumb_url(_Item("rule34xxx", thumb_url=thumb,
            media_url='https://api-cdn.rule34.xxx/images/128/' + 'c' * 32 + '.mp4')), thumb)

    def test_a_rule34xxx_thumb_in_another_shape_is_left_alone(self):
        """认不出的形状原样返回，不猜一个 bucket 出来。"""
        other = "https://api-cdn.rule34.xxx/images/1/x.jpg"
        self.assertEqual(display_thumb_url(_Item("rule34xxx", thumb_url=other)), other)

    def test_archive_thumbnails_use_the_img_subdomain(self):
        """2026-08-30 实测：主域 kemono.cr 回 302、pawchive.pw 回 404，img. 都回 200。"""
        for provider, host in (("kemono", "kemono.cr"), ("pawchive", "pawchive.pw"),
                               ("coomer", "coomer.st")):
            recorded = f"https://{host}/thumbnail/data/a/b/c.jpg"
            fixed = f"https://img.{host}/thumbnail/data/a/b/c.jpg"
            self.assertEqual(display_thumb_url(_Item(provider, thumb_url=recorded)),
                             fixed)
            # 已经是 img. 的不再叠加
            self.assertEqual(display_thumb_url(_Item(provider, thumb_url=fixed)), fixed)

    def test_an_archive_thumb_on_a_foreign_host_is_not_rewritten(self):
        self.assertEqual(
            display_thumb_url(_Item("kemono", thumb_url="https://example.test/a.jpg")),
            "https://example.test/a.jpg")

    def test_an_archive_row_without_a_thumb_derives_one_from_the_image_media(self):
        """封面修复前入库的旧行 thumb_url 为空，但 media_url 就是图片本身。

        库里两种 `media_url` 形状都有：那次媒体主机修复之前拼的没有 `/data` 前缀，
        之后拼的有。`/thumbnail/data` 里已经带了一个 `data`，所以拼之前要剥掉，
        否则新形状的行会得到 `/thumbnail/data/data/...` 这种必然 404 的地址。
        """
        for media in ("https://pawchive.pw/a/b/c.jpg",
                      "https://file.pawchive.pw/data/a/b/c.jpg"):
            self.assertEqual(
                display_thumb_url(_Item("pawchive", media_url=media)),
                "https://img.pawchive.pw/thumbnail/data/a/b/c.jpg", media)

    def test_a_video_only_archive_row_gets_no_derived_thumb(self):
        """视频没有可推导的缩略图，给了也是 404——卡片宁可显示干净的占位。"""
        self.assertIsNone(display_thumb_url(_Item(
            "kemono", media_url="https://kemono.cr/data/a/b/c.mp4")))

    def test_a_provider_without_a_rule_uses_the_recorded_thumb(self):
        for provider in ("rule34video", "f95zone", "nyaa"):
            self.assertEqual(
                display_thumb_url(_Item(provider, thumb_url="https://x.test/a.jpg")),
                "https://x.test/a.jpg")
        self.assertIsNone(display_thumb_url(_Item("rule34video")))


class HistoryEndRecognitionTests(unittest.TestCase):
    """哪些状态码代表「往回翻到尽头」只声明一次，翻页判定和旧行识别共用。

    `record_history_end` 之前的版本把翻到尽头记成了 `error`，正文就是
    `_check_status` 那句话。旧的识别代码是在 Web 层照站点名硬编码中文串比较，
    新增一个可回填来源时没人会想到还要改那一处。
    """

    def test_a_declared_status_is_recognised_and_others_are_not(self):
        self.assertTrue(is_history_end_error("kemono", "kemono 返回 HTTP 400"))
        self.assertTrue(is_history_end_error("coomer", "coomer 返回 HTTP 404"))
        self.assertTrue(is_history_end_error("rule34paheal",
                                             "rule34paheal 返回 HTTP 404"))
        self.assertFalse(is_history_end_error("kemono", "kemono 返回 HTTP 500"))
        self.assertFalse(is_history_end_error("rule34video",
                                             "rule34video 返回 HTTP 400"))

    def test_a_provider_that_never_pages_back_recognises_nothing(self):
        for message in ("f95zone 返回 HTTP 404", "f95zone 返回 HTTP 400"):
            self.assertFalse(is_history_end_error("f95zone", message))
        self.assertFalse(is_history_end_error("nyaa", "nyaa 返回 HTTP 404"))

    def test_it_matches_the_message_check_status_actually_writes(self):
        """判据必须对上真正落盘的那句话。

        识别的是 `_check_status` 生成的文本。这两处一旦分家，回填到底的来源会
        永远显示成红色错误行，而且不会有任何测试变红——所以这里现场生成一次。
        """
        connector = KemonoConnector(provider="kemono",
                                    transport=_transport(status=400))
        with self.assertRaises(FollowSourceError) as raised:
            connector._check_status(HttpResponse(400, {}, b""))
        self.assertTrue(is_history_end_error("kemono", str(raised.exception)),
                        f"没认出来：{raised.exception}")

    def test_an_unrelated_error_is_not_swallowed(self):
        for message in ("kemono 请求失败：connect timeout",
                        "kemono 的帖子列表格式不符", ""):
            self.assertFalse(is_history_end_error("kemono", message))


class PublicProbeApiTests(unittest.TestCase):
    """`follow_discovery` 与 `web_follow` 要的是探测能力，不该去摸私有方法。"""

    def test_probe_returns_the_response_without_checking_status(self):
        """发现流程按状态码判断「作者页在不在」，404 是答案不是故障。"""
        connector = _Probe(transport=_transport(status=404))
        self.assertEqual(connector.probe("https://example.test/a").status, 404)

    def test_fetch_json_parses_and_rejects_non_200(self):
        connector = _Probe(transport=_transport(body=b'{"name": "x"}'))
        self.assertEqual(connector.fetch_json("https://example.test/a"), {"name": "x"})
        waits = []
        failing = _Probe(transport=_transport(status=503, body=b"{}"), sleeper=waits.append)
        with self.assertRaises(FollowSourceError):
            failing.fetch_json("https://example.test/a")
        self.assertEqual(waits, [1, 2, 4, 8])

    def test_parse_json_rejects_a_non_json_body(self):
        connector = _Probe(transport=_transport(body=b"<html/>"))
        with self.assertRaises(FollowSourceError):
            connector.parse_json(connector.probe("https://example.test/a"))

    def test_every_connector_inherits_the_public_probe_api(self):
        for provider, factory in CONNECTORS.items():
            for name in ("probe", "fetch_json", "parse_json"):
                self.assertTrue(
                    hasattr(factory, name) or hasattr(KemonoConnector, name),
                    f"{provider} 缺少公开的 {name}",
                )


class NoPrivateReachIntoConnectorsTests(unittest.TestCase):
    def test_other_modules_never_call_connector_private_methods(self):
        """连接器之外的模块只能用公开 API。

        缺少「探测」这类公开入口时，调用方会直接摸 `_get`/`_check_status`/`_json`，
        于是基类的任何改动都可能悄悄弄坏发现流程。这条门槛把它挡在源码层面。"""
        import pathlib

        import peach

        root = pathlib.Path(peach.__file__).parent
        offenders = []
        for path in sorted(root.glob("*.py")):
            if path.name == "follow_sources.py":
                continue  # 基类自己的实现文件
            text = path.read_text(encoding="utf-8")
            for private in ("connector._get(", "connector._check_status(",
                            "connector._json(", "connector._post("):
                if private in text:
                    offenders.append(f"{path.name}: {private}")
        self.assertEqual(
            offenders, [],
            "改用 probe()／fetch_json()／parse_json()；缺能力就在基类补公开方法",
        )


class ProfileHandleTests(unittest.TestCase):
    """ref 里哪一截是作者本人的手柄，由各站自己的连接器说。

    这些规则不写在 `web_follow` 的一串 if/elif 里：「ref 长什么形状」正是连接器
    已经在解析、在校验的东西，同一份知识分在两层，改一处就会漂移。
    """

    def test_an_official_channel_ref_is_the_author_handle(self):
        cases = {
            "fanbox": ("ffxivinitiala", "ffxivinitiala"),
            "subscribestar": ("subscribestar.adult/initiala", "initiala"),
            "patreon": ("initiala", "initiala"),
        }
        for provider, (ref, expected) in cases.items():
            with self.subTest(provider=provider):
                self.assertEqual(official_profile_handle(provider, ref), expected)

    def test_a_numeric_patreon_user_page_has_no_handle(self):
        """`user/12345` 是内部 id，不是名字；学成别名会造出一个假作者。"""
        for ref in ("user/12345", "12345", "/user/12345/"):
            self.assertEqual(official_profile_handle("patreon", ref), "")

    def test_archive_and_tag_sites_offer_no_handle_at_all(self):
        cases = (("kemono", "fanbox/30917150"), ("coomer", "onlyfans/x"),
                 ("pawchive", "fanbox/1"), ("rule34xxx", "lazyprocrastinator"),
                 ("rule34video", "lazyprocrastinator"), ("f95zone", "50685"),
                 ("rule34paheal", "tag"), ("simpcity", "thread"))
        for provider, ref in cases:
            with self.subTest(provider=provider):
                self.assertEqual(official_profile_handle(provider, ref), "")

    def test_every_connector_answers_this_question(self):
        """基类给了默认值，所以新增站点不会因为漏实现而炸在检查更新的中途。"""
        for provider, factory in CONNECTORS.items():
            with self.subTest(provider=provider):
                self.assertIsInstance(factory.profile_handle("anything"), str)
        self.assertEqual(official_profile_handle("not-registered", "x"), "")

    def test_only_the_official_identity_providers_actually_return_one(self):
        """能返回手柄的站必须正好是 `_OFFICIAL_IDENTITY_PROVIDERS` 那一组。

        这两处一旦不一致，就会出现「明明解析出了手柄却永远不学」或者反过来
        「学了一个不该信的名字」，而两种都不会报错。
        """
        answering = {provider for provider in CONNECTORS
                     if official_profile_handle(provider, "some-name")}
        self.assertEqual(answering, set(_OFFICIAL_IDENTITY_PROVIDERS))


if __name__ == "__main__":
    unittest.main()
