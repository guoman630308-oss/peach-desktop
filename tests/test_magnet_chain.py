"""磁力抓取链的纯判据；不连接真实站点。"""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from peach.downloads import parse_magnet
from peach.magnet_chain import (CHAIN, _normalise, decode_base91, rank_candidates,
                                resolution_of, size_bytes)

HASH_A = "c9e15763f722f23e98a29decdfae341b98d53056"
HASH_B = "d9e15763f722f23e98a29decdfae341b98d53056"


def item(info_hash, **fields):
    return {
        "id": info_hash, "info_hash": info_hash,
        "uri": f"magnet:?xt=urn:btih:{info_hash}&dn=ABC-123",
        "name": "ABC-123", "size": 0, "size_text": "", "files": "",
        "file_count": 0, "file_list": [], "promo_files": 0,
        "resolution": 0, "codec": "", "seeders": 0, "peers": 0, "date": "",
        "chinese": False, "uncensored": False,
        "source": "test", "origins": ["test"], "source_url": "",
        "watermark_check": "pending", **fields,
    }


class MagnetChainTests(unittest.TestCase):
    def test_wishlist_preserves_comment_links_and_more_than_eight_source_versions(self):
        from peach.wants_magnets import fetch
        links = [dict(id=f'magnet:{index:040x}', protocol='magnet', info_hash=f'{index:040x}',
                      uri=f'magnet:?xt=urn:btih:{index:040x}', name='ABC-123', size='2 GB', files='',
                      date='', attributes=[], source='JavDB', source_url='https://example.test/work', origins=['JavDB'])
                 for index in range(10)]
        links.append({**links[0], 'id': 'ed2k:example', 'protocol': 'ed2k', 'info_hash': '',
                      'uri': 'ed2k://|file|ABC-123|1234|' + 'a' * 32 + '|/', 'source': 'JavDB 评论'})
        direct = _normalise({'uri': item(HASH_A)['uri'], 'size': '8 GiB'}, CHAIN[0], 'ABC-123')
        with patch('peach.magnet_chain.search', return_value={'items': [direct], 'linked_resources': links}):
            shown = fetch(Path('.'), 'ABC-123')['items']
        self.assertEqual(len(shown), 12)
        self.assertEqual(shown[-1]['protocol'], 'ed2k')
        self.assertEqual(shown[0]['source'], 'manko.fun')

    def test_direct_add_stops_at_preferred_hd_but_wishlist_collects_other_sources(self):
        from peach.magnet_chain import Fetcher, search
        from peach.wants_magnets import fetch
        preferred = item(HASH_A, source='manko.fun', origins=['manko.fun'], resolution=1080)
        alternate = item(HASH_B, source='JavDB', origins=['JavDB'], resolution=2160)
        with patch.object(Fetcher, '__init__', return_value=None), patch.object(Fetcher, 'close'), \
                patch.object(Fetcher, 'manko', side_effect=lambda _: [dict(preferred)]), \
                patch.object(Fetcher, 'javdb', side_effect=lambda _: [dict(alternate)]) as javdb, \
                patch.object(Fetcher, 'sukebei', return_value=[]) as sukebei:
            self.assertEqual(len(search(Path('.'), 'ABC-123')['items']), 1)
            javdb.assert_not_called()
            sukebei.assert_not_called()
            found = fetch(Path('.'), 'ABC-123')['items']
            self.assertEqual({row['source'] for row in found}, {'manko.fun', 'JavDB'})
            self.assertEqual(found[0]['resolution'], 2160)
            javdb.assert_called_once()
            sukebei.assert_called_once()

    def test_wishlist_candidates_have_display_and_download_fields(self):
        from peach.wants_magnets import fetch
        candidate = _normalise({'uri': item(HASH_A)['uri'], 'size': '2 GiB',
                                'attributes': ['中字']}, CHAIN[1], 'ABC-123')
        with patch('peach.magnet_chain.search', return_value={'items': [candidate]}):
            result = fetch(Path('.'), 'ABC-123')['items'][0]
        self.assertEqual(result['protocol'], 'magnet')
        self.assertEqual(result['attributes'], ['中字'])
        self.assertEqual(result['size'], '2 GiB')

    def test_unavailable_sources_without_results_use_the_failure_cooldown(self):
        from peach.magnet_chain import Fetcher, search
        from peach.wants_magnets import MagnetSearch
        now = [100.0]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with patch.object(Fetcher, '__init__', return_value=None), patch.object(Fetcher, 'close'), \
                patch.object(Fetcher, 'manko', side_effect=RuntimeError), \
                patch.object(Fetcher, 'javdb', return_value=[]), \
                patch.object(Fetcher, 'sukebei', return_value=[]):
            calls = []
            def fetcher(root, code):
                calls.append(code)
                return search(root, code)
            lookup = MagnetSearch(Path(temporary.name).resolve(), fetcher=fetcher, clock=lambda: now[0], sleeper=lambda _: None)
            self.assertEqual(lookup.query('ABC-123')['state'], 'error')
            now[0] += 120
            self.assertEqual(lookup.query('ABC-123')['state'], 'error')
        self.assertEqual(len(calls), 2)
    def test_base91_decodes_the_api_envelope_payload(self):
        # basE91 官方示例：编码后的这一段解回 ASCII "test"。
        self.assertEqual(decode_base91("fPNKd"), b"test")

    def test_resolution_and_size_are_normalised(self):
        self.assertEqual(resolution_of("[FHD] ABC-123"), 1080)
        self.assertEqual(resolution_of("ABC-123 4K"), 2160)
        self.assertEqual(size_bytes("2.5 GiB"), int(2.5 * 1024 ** 3))

    def test_manko_bypasses_watermark_gate_but_other_sources_do_not(self):
        manko = _normalise({"uri": item(HASH_A)["uri"], "name": "ABC-123 FHD"},
                           CHAIN[0], "ABC-123")
        javdb = _normalise({"uri": item(HASH_B)["uri"], "name": "ABC-123 FHD"},
                           CHAIN[1], "ABC-123")
        self.assertEqual((manko["watermark_check"], javdb["watermark_check"]),
                         ("bypassed", "pending"))

    def test_file_count_is_not_a_rejection_and_quality_wins(self):
        many = item(HASH_A, resolution=2160, file_count=8, promo_files=2, seeders=1)
        single = item(HASH_B, resolution=1080, file_count=1, seeders=20)
        ranked = rank_candidates([single, many])
        self.assertEqual([row["info_hash"] for row in ranked], [HASH_A, HASH_B])

    def test_selected_edition_goal_wins_before_resolution(self):
        chinese = item(HASH_A, resolution=1080, chinese=True)
        plain = item(HASH_B, resolution=2160)
        self.assertEqual(rank_candidates([plain, chinese], "chinese")[0]["info_hash"], HASH_A)

    def test_mismatched_release_is_rejected(self):
        candidate = _normalise({"uri": item(HASH_A)["uri"], "name": "ABC-124 FHD"},
                               CHAIN[1], "ABC-123")
        self.assertIsNone(candidate)
        self.assertEqual(parse_magnet(item(HASH_A)["uri"]).info_hash, HASH_A)


if __name__ == "__main__":
    unittest.main()
