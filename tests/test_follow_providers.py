import inspect
import unittest
from pathlib import Path

from peach import follow_discovery, follow_providers, web_follow
from peach.follow_sources import CONNECTORS, ParsedSource, _BaseConnector
from peach.follow_stream import _PROVIDER_HOSTS
from peach.follow_variants import PROVIDER_PRIORITY
from peach.follow_secrets import (
    CREDENTIAL_GUIDE,
    SYNCABLE_FIELDS,
    credential_store_for,
)
from peach.follow_store import _OFFICIAL_IDENTITY_PROVIDERS
from peach.web_follow import PROVIDER_LABELS, _BACKFILL_PROVIDERS


class ProviderRegistryTests(unittest.TestCase):
    def test_every_connector_is_registered_and_every_source_has_a_connector(self):
        """新增站点漏登记不会自己报错，只会在某个页面上少一行——所以这里挡住。

        有 `source_url` 的 spec 就是追更来源，必须有连接器；反过来每个连接器也必须
        有 spec，否则显示名、优先级、主机白名单都会静默走默认值。"""
        registered = {key for key, spec in follow_providers.PROVIDERS.items() if spec.source_url}
        self.assertEqual(
            registered, set(CONNECTORS),
            "追更来源与连接器必须一一对应；新增站点要同时写连接器和 follow_providers 登记",
        )

    def test_gofile_is_a_media_source_not_a_follow_source(self):
        """Gofile 只作为媒体来源出现在界面上，没有连接器也不该有作品页 URL。"""
        self.assertIn("gofile", PROVIDER_LABELS)
        self.assertNotIn("gofile", CONNECTORS)
        self.assertIsNone(follow_providers.PROVIDERS["gofile"].source_url)

    def test_semantics_is_declared_only_in_the_registry(self):
        """连接器类与解析结果都从登记表读语义，没有第二份可以改漏的清单。

        改了登记表却忘了连接器时，分组语义会和优先级排序对不上，而且不会抛错；
        所以连接器不得自己声明 `semantics`，解析结果也只能派生。"""
        self.assertIsInstance(vars(_BaseConnector)["semantics"], property)
        for key, factory in CONNECTORS.items():
            self.assertNotIn("semantics", vars(factory), f"{key} 不得自己声明语义")
        self.assertNotIn("semantics", ParsedSource.__dataclass_fields__)
        self.assertEqual(
            ParsedSource("f95zone", "1", "https://f95zone.to/threads/1/", "x").semantics,
            "release")

    def test_a_source_url_without_ref_is_refused_at_declaration_time(self):
        with self.assertRaises(ValueError):
            follow_providers.ProviderSpec(key="x", label="X", source_url="https://x/",
                                          url_hosts=("x",))

    def test_priority_is_unique_so_primary_choice_is_deterministic(self):
        values = list(PROVIDER_PRIORITY.values())
        self.assertEqual(len(values), len(set(values)), "优先级重复会让选主条目结果不确定")

    def test_official_identity_providers_do_not_proxy_media(self):
        """官方三家走各自详情接口取媒体，不经本地代理，所以不该有主机白名单。"""
        for key in _OFFICIAL_IDENTITY_PROVIDERS:
            self.assertEqual(follow_providers.PROVIDERS[key].hosts, ())
            self.assertNotIn(key, _PROVIDER_HOSTS)

    def test_backfill_providers_are_sites_with_real_paging(self):
        """「抓更早一页」只给历史页走得完的来源：归档站按偏移翻页，fanbox 走
        站点自己的游标清单（`post.paginateCreator`）。官方身份与可回填是两回事：
        fanbox 两者都是，subscribestar、patreon 没有公开分页就不进这一档。"""
        self.assertTrue(_BACKFILL_PROVIDERS <= set(CONNECTORS))
        self.assertIn("fanbox", _BACKFILL_PROVIDERS)
        self.assertTrue(_BACKFILL_PROVIDERS.isdisjoint({"subscribestar", "patreon"}))

    def test_registry_carries_no_credential_policy(self):
        """凭据同步策略必须留在 CREDENTIAL_GUIDE 里逐字段声明，不进这张通用表。

        收进来会让它更容易被顺手改错：今天 `api_key` 能同步、`cookie` 不能，
        明天新增 `session_token` 就会落到错误的一侧。新增来源时作者必须单独表态，
        这个摩擦是故意留的。"""
        spec_fields = set(vars(follow_providers.PROVIDERS["fanbox"]))
        for banned in ("syncable", "credential", "cookie", "token", "secret"):
            self.assertNotIn(
                banned, spec_fields, f"ProviderSpec 不得携带凭据相关字段：{banned}",
            )
        self.assertEqual(
            SYNCABLE_FIELDS,
            {provider: tuple(guide.get("syncable", ())) for provider, guide in CREDENTIAL_GUIDE.items()},
            "SYNCABLE_FIELDS 只能从 CREDENTIAL_GUIDE 派生",
        )

    def test_every_layer_builds_its_credential_store_through_one_factory(self):
        """Web 与发现必须拿到同一套共享根与可同步字段声明。

        分头 `CredentialStore(...)` 时只有 Web 那份带上了共享回填，同一份凭据
        在网页里在、在发现那层「未配置」。这里挡住往回退化。
        """
        sources = {
            name: (Path(inspect.getsourcefile(module)).read_text(encoding="utf-8"))
            for name, module in (("web_follow", web_follow),
                                 ("follow_discovery", follow_discovery))
        }
        for name, text in sources.items():
            with self.subTest(module=name):
                self.assertNotIn("CredentialStore(", text.replace(
                    "-> CredentialStore", ""),
                    f"{name} 必须走 credential_store_for，不要自己构造")
                self.assertIn("credential_store_for(", text)

    def test_the_factory_carries_the_syncable_declaration(self):
        store = credential_store_for(Path("secrets"), shared_root=Path("shared"))
        self.assertEqual(store.syncable_fields, SYNCABLE_FIELDS)
        self.assertEqual(store.syncable("rule34xxx"), ("user_id", "api_key"))
        self.assertEqual(store.syncable("f95zone"), ())
        self.assertEqual(store.shared_root, Path("shared") / "secrets" / "follow")

    def test_semantics_rejects_unknown_values(self):
        with self.assertRaises(ValueError):
            follow_providers.ProviderSpec(key="x", label="X", semantics="whatever")


class UrlHostTests(unittest.TestCase):
    """粘一条链接时「这个主机属于哪个站」的登记与查表。"""

    def test_every_follow_source_declares_at_least_one_url_host(self):
        """没有 url_hosts 的追更来源永远无法从链接登记，而且不会报错。"""
        for key, spec in follow_providers.PROVIDERS.items():
            if spec.source_url:
                self.assertTrue(spec.url_hosts, f"{key} 缺 url_hosts")

    def test_a_source_url_without_url_hosts_is_refused_at_declaration_time(self):
        with self.assertRaises(ValueError):
            follow_providers.ProviderSpec(key="x", label="X",
                                          source_url="https://x/{ref}")

    def test_no_host_is_claimed_by_two_sources(self):
        declared = [host for spec in follow_providers.PROVIDERS.values()
                    for host in spec.url_hosts]
        self.assertEqual(len(declared), len(set(declared)),
                         "同一个主机登记两次，解析结果取决于字典顺序")

    def test_url_hosts_are_not_the_media_proxy_allowlist(self):
        """两张表名字像、含义不同：paheal 的站点主机与媒体主机根本不一样。

        「复用 hosts 就行」不成立：`hosts` 是媒体代理白名单，放宽它等于放宽能被
        代理取回的地址；`url_hosts` 只决定一条链接归谁解析。
        """
        paheal = follow_providers.PROVIDERS["rule34paheal"]
        self.assertEqual(paheal.url_hosts, ("rule34.paheal.net",))
        self.assertIn("paheal-cdn.net", paheal.hosts)
        self.assertNotIn("rule34.paheal.net", paheal.hosts)

    def test_rule34video_media_is_allowed_to_land_on_its_cdn(self):
        """正片不在站内：`/get_file/…` 会 302 到 `*.boomio-cdn.com`。

        2026-09-04 实测四条候选分别落在 eu-cdn05／06／08／11-prem，最终一跳才回
        206 `video/mp4`。少了这个后缀，媒体代理在跳出白名单那一步拒收，整站的
        rule34video 视频一条都放不出来。站点主机仍然要留着——签名地址是从详情页
        读出来的，那一步按 `hosts` 校验。
        """
        video = follow_providers.PROVIDERS["rule34video"]
        self.assertEqual(video.url_hosts, ("rule34video.com",))
        self.assertEqual(video.hosts, ("rule34video.com", "boomio-cdn.com"))

    def test_subdomains_and_www_resolve_to_the_registered_source(self):
        for host, expected in (
            ("fanbox.cc", "fanbox"),
            ("www.fanbox.cc", "fanbox"),
            ("ffxivinitiala.fanbox.cc", "fanbox"),
            ("api.rule34.xxx", "rule34xxx"),
            ("kemono.cr", "kemono"),
            ("coomer.st", "coomer"),
            ("SubscribeStar.adult", "subscribestar"),
        ):
            self.assertEqual(follow_providers.provider_for_host(host), expected, host)

    def test_the_longest_registered_suffix_wins(self):
        """`rule34.paheal.net` 比将来可能出现的 `paheal.net` 更具体，必须赢。"""
        self.assertEqual(follow_providers.provider_for_host("rule34.paheal.net"),
                         "rule34paheal")
        self.assertEqual(
            follow_providers.provider_for_host("cdn.rule34.paheal.net"),
            "rule34paheal")

    def test_an_unregistered_host_is_empty_not_a_guess(self):
        for host in ("nyaa.si", "", "notfanbox.cc", "fanbox.cc.evil.example"):
            self.assertEqual(follow_providers.provider_for_host(host), "", host)


class ReleaseKeyPerPostTests(unittest.TestCase):
    def test_the_rule_is_declared_in_the_registry_not_named_in_the_data_layer(self):
        """论坛线程每层各自成组，这是来源语义，不是 `follow_store` 里的站点点名。"""
        self.assertEqual(follow_providers.release_key_per_post(),
                         frozenset({"f95zone", "simpcity"}))

    def test_it_only_applies_to_release_semantics(self):
        """每条各自成组只对「同一作品的历次发布」有意义；work 语义靠标题合并。"""
        for key in follow_providers.release_key_per_post():
            self.assertEqual(follow_providers.PROVIDERS[key].semantics, "release")


class PublicMediaHostsTests(unittest.TestCase):
    def test_public_image_hosts_are_declared_in_the_registry(self):
        """论坛帖子里的图挂在几十家图站上，放行任意公网主机是来源语义，登记在这张表上。"""
        self.assertEqual(follow_providers.public_media_hosts(), frozenset({"simpcity"}))

    def test_public_mode_replaces_the_whitelist_instead_of_sitting_beside_it(self):
        """两个都写会让读者以为白名单还在起作用。"""
        for key in follow_providers.public_media_hosts():
            self.assertEqual(follow_providers.PROVIDERS[key].hosts, ())
            self.assertNotIn(key, _PROVIDER_HOSTS)
        with self.assertRaises(ValueError):
            follow_providers.ProviderSpec("x", "X", hosts=("x.example",), public_media_hosts=True)


class ExcludedItemTests(unittest.TestCase):
    def test_hidden_items_are_declared_in_the_registry_not_in_the_web_layer(self):
        """用户点名要隐藏的既有条目登记在这张表上。

        它不是 Web 层的一个裸常量：「这个站有哪些条目要藏」是站点数据，不该跟
        标签清理、缩略图这些展示逻辑混在同一个文件里。
        """
        excluded = follow_providers.excluded_external_ids()
        self.assertEqual(excluded, {"rule34video": frozenset({"4533145"})})

if __name__ == "__main__":
    unittest.main()
