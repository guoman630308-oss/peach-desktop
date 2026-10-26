"""索引器搜索用合成 XML、假 HTTP 与临时凭据；不连接真实账号。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

from peach.follow_secrets import CredentialStore
from peach.resource_search import (CREDENTIAL, MAX_BYTES, MAX_TORRENTS, Filters, Indexers, Search, endpoint,
                                   qualities)

HASH = "c9e15763f722f23e98a29decdfae341b98d53056"
CAPS = '<caps><limits max="25"/><searching><search available="yes" supportedParams="q"/></searching></caps>'
ROW = {"key": "one", "name": "来源一", "url": "http://indexer.test/api",
       "api_key": "private-test-key", "enabled": True}


def item(title="ABC-123 4K HEVC 中字", info_hash=HASH, size=1024 ** 3, seeders=3,
         magnet_field="magneturl"):
    magnet = f"magnet:?xt=urn:btih:{info_hash}&tr=https://tracker.test/?passkey=secret"
    link = (f'<torznab:attr name="magneturl" value="{escape(magnet, {chr(34): "&quot;"})}"/>'
            if magnet_field == "magneturl" else f'<{magnet_field}>{escape(magnet)}</{magnet_field}>')
    return (f'<item><title>{escape(title)}</title><size>{size}</size>{link}'
            f'<torznab:attr name="seeders" value="{seeders}"/>'
            '<torznab:attr name="peers" value="7"/></item>')


#: 键按字节序排列的最小种子；infohash 是 info 字典原文的 SHA-1。
INFO = b"d6:lengthi1e4:name7:ABC-123e"
TORRENT = b"d8:announce16:http://t.test/an4:info" + INFO + b"e"
TORRENT_HASH = hashlib.sha1(INFO).hexdigest()
DOWNLOAD = "http://indexer.test/1/download?apikey=private-test-key&amp;link=abc"


def torrent_item(title="ABC-123 FHD", link=DOWNLOAD, seeders=3):
    """OneJAV 一类条目：没有磁力与 infohash，只有索引器下载代理的种子地址。"""
    return (f'<item><title>{escape(title)}</title><size>{1024 ** 3}</size><link>{link}</link>'
            f'<torznab:attr name="seeders" value="{seeders}"/></item>')


def feed(*items):
    return '<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>' + ''.join(items) + '</channel></rss>'


class SearchTests(unittest.TestCase):
    def test_edition_badges_follow_the_catalog_suffix_rules(self):
        self.assertTrue(qualities("ABC-123-CH", [], "ABC-123")["chinese"])
        self.assertTrue(qualities("ABC-123-UC", [], "ABC-123")["uncensored"])
        self.assertFalse(qualities("ABC-123", [], "ABC-123")["chinese"])

    def test_fhd_and_uhd_labels_count_as_resolutions(self):
        self.assertEqual(qualities("[FHD] ABC-123", [], "ABC-123")["resolution"], 1080)
        self.assertEqual(qualities("ABC-123 UHD", [], "ABC-123")["resolution"], 2160)
        self.assertEqual(qualities("[HD Uncensored] ABC-123", [], "ABC-123")["resolution"], 0)

    def search(self, content, *, rows=None, code="ABC-123", filters=None, blocked=()):
        self.requests = []

        def respond(request):
            self.requests.append(request)
            if callable(content):
                return content(request)
            return httpx.Response(200, text=CAPS if request.url.params["t"] == "caps" else content)

        search = Search(transport=httpx.MockTransport(respond))
        return search.run(rows or [ROW], code, filters or Filters(), blocked)

    def test_caps_then_search_and_private_tracker_is_not_returned(self):
        result = self.search(feed(item()))
        self.assertTrue(result["ok"])
        self.assertEqual([r.url.params["t"] for r in self.requests], ["caps", "search"])
        params = self.requests[1].url.params
        self.assertEqual((params["q"], params["cat"], params["limit"]), ("ABC-123", "6000", "25"))
        row = result["items"][0]
        self.assertEqual((row["info_hash"], row["resolution"], row["codec"], row["chinese"]),
                         (HASH, 2160, "HEVC", True))
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn(ROW["api_key"], json.dumps(result))

    def test_magnet_link_and_guid_are_accepted_by_content(self):
        for field in ("magneturl", "link", "guid"):
            with self.subTest(field=field):
                self.assertEqual(self.search(feed(item(magnet_field=field)))["items"][0]["id"], HASH)

    def torrent_search(self, *items, torrent=TORRENT, status=200, headers=None):
        def respond(request):
            if request.url.path.startswith("/1/download"):
                return httpx.Response(status, content=torrent, headers=headers)
            return httpx.Response(200, text=CAPS if request.url.params["t"] == "caps" else feed(*items))
        return self.search(respond)

    def test_torrent_only_item_becomes_a_magnet_from_its_info_dictionary(self):
        result = self.torrent_search(torrent_item())
        row = result["items"][0]
        self.assertEqual((row["info_hash"], row["uri"], row["resolution"]),
                         (TORRENT_HASH, f"magnet:?xt=urn:btih:{TORRENT_HASH}", 1080))
        self.assertEqual([r.url.path for r in self.requests], ["/api", "/api", "/1/download"])
        self.assertNotIn(ROW["api_key"], json.dumps(result))

    def test_proxy_redirect_to_a_magnet_is_read_from_location(self):
        result = self.torrent_search(torrent_item(), status=301,
                                     headers={"location": f"magnet:?xt=urn:btih:{HASH}"})
        self.assertEqual(result["items"][0]["id"], HASH)

    def test_torrent_link_off_the_indexer_origin_is_not_requested(self):
        result = self.torrent_search(torrent_item(link="http://tracker.test/1/download/x.torrent"))
        self.assertEqual(result["items"], [])
        self.assertEqual({r.url.host for r in self.requests}, {"indexer.test"})

    def test_unreadable_torrent_drops_only_that_item(self):
        unsorted = b"d4:infod4:name1:a6:lengthi1eee"
        for torrent in (b"<!DOCTYPE html>", unsorted, b"d8:announce1:xe"):
            with self.subTest(torrent=torrent[:16]):
                result = self.torrent_search(torrent_item(), item(), torrent=torrent)
                self.assertTrue(result["ok"])
                self.assertEqual([row["id"] for row in result["items"]], [HASH])
                self.assertEqual(result["warnings"], [])

    def test_torrent_fetches_skip_dead_items_and_stop_at_the_per_indexer_cap(self):
        rows = [torrent_item(), torrent_item(seeders=0)] + [torrent_item() for _ in range(MAX_TORRENTS + 2)]
        self.torrent_search(*rows)
        self.assertEqual(sum(r.url.path.startswith("/1/download") for r in self.requests), MAX_TORRENTS)

    def test_utf8_xml_with_bom_is_accepted(self):
        def respond(request):
            xml = CAPS if request.url.params["t"] == "caps" else feed(item())
            return httpx.Response(200, content=("\ufeff" + xml).encode("utf-8"))
        self.assertEqual(self.search(respond)["items"][0]["id"], HASH)

    def test_fc2_query_uses_digits_and_keeps_exact_identity(self):
        result = self.search(feed(item(title="FC2-PPV-1234567 1080p")), code="FC2-PPV-1234567")
        self.assertEqual(self.requests[1].url.params["q"], "1234567")
        self.assertEqual(len(result["items"]), 1)

    def test_filters_reject_different_code_zero_seeders_size_and_blocked_hash(self):
        result = self.search(feed(item(title="ABC-124"), item(seeders=0), item(size=1), item()),
                             filters=Filters(min_size=100), blocked=[HASH])
        self.assertEqual(result["items"], [])

    def test_quality_goal_precedes_size_and_only_five_candidates_are_returned(self):
        rows = [item(title=f"ABC-123 1080p {'中字' if at == 6 else ''}",
                     info_hash=f"{at:040x}", size=(at + 1) * 1024 ** 3) for at in range(7)]
        result = self.search(feed(*rows), filters=Filters(goal="chinese"))
        self.assertEqual(len(result["items"]), 5)
        self.assertEqual(result["items"][0]["id"], f"{6:040x}")

    def test_partial_failure_preserves_results_and_hides_remote_error(self):
        def respond(request):
            if request.url.host == "bad.test":
                raise httpx.ConnectError(str(request.url), request=request)
            return httpx.Response(200, text=CAPS if request.url.params["t"] == "caps" else feed(item()))
        result = self.search(respond, rows=[ROW, {**ROW, "key": "bad", "url": "http://bad.test/api"}])
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertNotIn(ROW["api_key"], json.dumps(result))

    def test_repeated_hash_merges_origins(self):
        result = self.search(feed(item()), rows=[ROW, {**ROW, "key": "two", "name": "来源二"}])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(set(result["items"][0]["origins"]), {"来源一", "来源二"})

    def test_unavailable_search_does_not_send_search_request(self):
        result = self.search(lambda request: httpx.Response(200, text='<caps><searching/></caps>'))
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.requests), 1)

    def test_bad_xml_entities_large_response_and_html_fail_without_results(self):
        for content in ('<!DOCTYPE rss [<!ENTITY x "x">]><rss/>', '<html/>', '<rss>', 'x' * (MAX_BYTES + 1)):
            with self.subTest(content=content[:30]):
                result = self.search(content)
                self.assertFalse(result["ok"])
                self.assertEqual(result["items"], [])

    def test_redirect_is_not_followed(self):
        result = self.search(lambda request: httpx.Response(302, headers={"location": "http://other.test/"}))
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.requests), 1)

    def test_cache_rechecks_blacklist_and_expires(self):
        calls = []
        clock = [0]
        def respond(request):
            calls.append(request)
            return httpx.Response(200, text=CAPS if request.url.params["t"] == "caps" else feed(item()))
        search = Search(transport=httpx.MockTransport(respond), clock=lambda: clock[0])
        self.assertEqual(len(search.run([ROW], "ABC-123", Filters())["items"]), 1)
        self.assertEqual(search.run([ROW], "ABC-123", Filters(), [HASH])["items"], [])
        self.assertEqual(len(calls), 2)
        clock[0] = 61
        search.run([ROW], "ABC-123", Filters())
        self.assertEqual(len(calls), 4)

    def test_disabled_and_busy_search_do_not_contact_indexer(self):
        search = Search(transport=httpx.MockTransport(lambda request: self.fail("unexpected request")))
        self.assertEqual(search.run([{**ROW, "enabled": False}], "ABC-123", Filters())["state"], "unavailable")
        with search.lock:
            self.assertEqual(search.run([ROW], "ABC-123", Filters())["state"], "busy")

    def test_invalid_code_and_size_range_are_rejected(self):
        for code in ("", "not-a-code", "x" * 90):
            with self.assertRaises(ValueError):
                Search().run([ROW], code, Filters())
        for values in ({"min_size": -1}, {"min_size": 10, "max_size": 2}, {"goal": "unknown"}):
            with self.assertRaises(ValueError):
                Filters(**values)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = CredentialStore(Path(temporary.name))
        self.settings = Indexers(self.store)

    def test_save_preserve_clear_disable_and_remove_are_reversible(self):
        public = self.settings.save({"indexers": [ROW]})
        self.assertNotIn(ROW["api_key"], json.dumps(public))
        self.assertTrue(public["indexers"][0]["api_key_set"])
        row = {**ROW, "api_key": "", "enabled": False}
        self.settings.save({"indexers": [row]})
        self.assertEqual(self.settings.load()[0]["api_key"], ROW["api_key"])
        self.assertFalse(self.settings.load()[0]["enabled"])
        self.settings.save({"indexers": [{**row, "clear_api_key": True}]})
        self.assertFalse(self.settings.public()["indexers"][0]["api_key_set"])
        self.settings.save({"indexers": []})
        self.assertEqual(self.settings.load(), [])

    def test_changing_address_does_not_forward_stored_secret(self):
        self.settings.save({"indexers": [ROW]})
        self.settings.save({"indexers": [{**ROW, "url": "http://other.test/api", "api_key": ""}]})
        self.assertEqual(self.settings.load()[0]["api_key"], "")

    def test_validation_is_atomic_and_rejects_duplicate_ids_and_secret_urls(self):
        self.settings.save({"indexers": [ROW]})
        for rows in ([ROW, ROW], [{**ROW, "url": "http://indexer.test/api?apikey=secret"}], [ROW] * 5):
            with self.assertRaises(ValueError):
                self.settings.save({"indexers": rows})
            self.assertEqual(self.settings.load(), [ROW])
        for value in ("file:///tmp/x", "http://user:pass@host/api", "https://host/api#secret"):
            with self.assertRaises(ValueError):
                endpoint(value)

    def test_corrupt_configuration_does_not_disclose_file_contents(self):
        self.store.save(CREDENTIAL, {"indexers": 'private-test-key'})
        with self.assertRaisesRegex(ValueError, "配置读不出来") as error:
            self.settings.public()
        self.assertNotIn(ROW["api_key"], str(error.exception))
