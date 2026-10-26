"""官方预告的精确作品身份、地址边界、固定成功缓存和短期缺失缓存。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from peach.http import HttpResponse
from peach.jav_official_preview import dmm_preview, dmm_preview_url, javdb_preview, prestige_preview
from peach.javdb_public import PublicAPI, PublicSourceUnavailable
from peach.preview_cache import PreviewCache
from peach.remote_media_cache import cache_for
from peach.web_jav_detail import q_jav_detail
from peach.web_state import WebContract
from support.ledger import fresh_ledger

UUID = '69fa4c03-103c-482e-aec5-14f971ba4d7d'
VIDEO = 'https://www.prestige-av.com/api/media/6/9/' + UUID + '.mp4'
DMM_VIDEO = 'https://cc3001.dmm.co.jp/pv/sample/1rctd007644k.mp4'


class OfficialPreviewTests(unittest.TestCase):
    def setUp(self):
        for name in ('dmm_preview', 'javdb_preview'):
            patcher = patch('peach.jav_official_preview.' + name, return_value='')
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_dmm_search_reuses_exact_identity_and_declared_preview(self):
        requests = []
        def transport(request, *_):
            body = json.loads(request.body)
            requests.append(body)
            if 'queryWord' in body['variables']:
                data = {'legacySearchPPV': {'result': {'contents': [{'id': '1rctd00765'}, {'id': '1rctd00764'}]}}}
            elif body['variables']['id'] == 'rctd00764':
                data = {'ppvContent': None}
            else:
                data = {'ppvContent': {'id': '1rctd00764', 'makerContentId': 'RCTD-764',
                                      'sample2DMovie': {'highestMovieUrl': DMM_VIDEO}}}
            return HttpResponse(200, {}, json.dumps({'data': data}).encode())
        self.assertEqual(dmm_preview('RCTD-764', Path('unused'), transport=transport), DMM_VIDEO)
        self.assertEqual([row['variables'].get('id') for row in requests], ['rctd00764', None, '1rctd00764'])
        self.assertIn('sample2DMovie', requests[-1]['query'])

    def test_dmm_rejects_identity_mismatch_and_unrecognised_preview_fields(self):
        item = {'id': 'rctd00764', 'makerContentId': 'RCTD-764', 'sample2DMovie': {'highestMovieUrl': DMM_VIDEO}}
        missing = dict(item)
        del missing['sample2DMovie']
        for value in [missing, {**item, 'id': 'another'}, {**item, 'makerContentId': 'RCTD-765'},
                      {**item, 'sample2DMovie': {'highestMovieUrl': 'https://private.invalid/sample.mp4'}}]:
            with self.subTest(value=value), self.assertRaises(PublicSourceUnavailable):
                dmm_preview('RCTD-764', Path('unused'), transport=Mock(return_value=HttpResponse(
                    200, {}, json.dumps({'data': {'ppvContent': value}}).encode())))
        self.assertEqual(dmm_preview('RCTD-764', Path('unused'), transport=Mock(return_value=HttpResponse(
            200, {}, json.dumps({'data': {'ppvContent': {**item, 'sample2DMovie': None}}}).encode()))), '')

    def test_dmm_media_boundary_rejects_credentials_fragments_and_other_hosts(self):
        self.assertEqual(dmm_preview_url(DMM_VIDEO), DMM_VIDEO)
        for url in [DMM_VIDEO.replace('https:', 'http:'), DMM_VIDEO + '#fragment',
                    DMM_VIDEO.replace('cc3001.', 'cc3001.evil.'), DMM_VIDEO.replace('https://', 'https://user:pass@'),
                    DMM_VIDEO.replace('/pv/', '/private/'), DMM_VIDEO.replace('.mp4', '.jpg')]:
            with self.subTest(url=url):
                self.assertEqual(dmm_preview_url(url), '')

    def test_dmm_response_without_product_field_is_unavailable(self):
        with self.assertRaises(PublicSourceUnavailable):
            dmm_preview('RCTD-764', Path('unused'), transport=Mock(return_value=HttpResponse(200, {}, b'{"data": {}}')))

    def test_javdb_reads_only_the_matching_work_declared_dmm_video(self):
        html = ('<div class="panel-block"><strong>番號:</strong><span class="value">RCTD-764</span></div>'
                f'<video><source src="{DMM_VIDEO}"></video>')
        transport = Mock(return_value=HttpResponse(200, {}, html.encode()))
        self.assertEqual(javdb_preview('RCTD-764', 'ZN9Ad6', Path('unused'), transport=transport), DMM_VIDEO)
        self.assertEqual(transport.call_count, 1)
        with self.assertRaises(PublicSourceUnavailable):
            javdb_preview('RCTD-765', 'ZN9Ad6', Path('unused'), transport=transport)

    def test_javdb_can_identify_the_work_without_webui_identity(self):
        search = '<a href="/v/ZN9Ad6" class="box" title="来源作品"><strong>RCTD-764</strong></a>'
        detail = ('<div class="panel-block"><strong>番號:</strong><span class="value">RCTD-764</span></div>'
                  f'<video><source src="{DMM_VIDEO}"></video>')
        transport = Mock(side_effect=[HttpResponse(200, {}, search.encode()), HttpResponse(200, {}, detail.encode())])
        self.assertEqual(javdb_preview('RCTD-764', None, Path('unused'), transport=transport), DMM_VIDEO)
        self.assertEqual(transport.call_count, 2)

    def test_preview_source_order_and_webui_fallback(self):
        mirror = 'https://jdforrepam.com/api/v1/movies/ttm3u8/preview/sample/720p.m3u8'
        for dmm, javdb, expected in [(DMM_VIDEO, '', DMM_VIDEO), ('', DMM_VIDEO, DMM_VIDEO), ('', '', mirror),
                                     (PublicSourceUnavailable('DMM 未取得'), '', mirror)]:
            with self.subTest(dmm=dmm, javdb=javdb), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                contract = WebContract(fresh_ledger(root), cover_root=root / 'covers')
                self.addCleanup(contract.stop_background_jobs)
                api = PublicAPI()
                api.movie = Mock(return_value={'number': 'RCTD-764', 'id': 'ZN9Ad6', 'preview_video_url': mirror})
                with patch('peach.web_jav_detail.PUBLIC_API', api), \
                        patch('peach.jav_official_preview.dmm_preview', side_effect=dmm if isinstance(dmm, Exception) else None,
                              return_value=dmm) as official, \
                        patch('peach.jav_official_preview.javdb_preview', return_value=javdb) as page:
                    payload = q_jav_detail(contract, {'code': 'RCTD-764'})
                ref = payload['preview_video_url'].rsplit('/', 1)[-1]
                self.assertEqual(PreviewCache(cache_for(contract)).source('RCTD-764', ref)['url'], expected)
                official.assert_called_once()
                self.assertEqual(page.call_count, 0 if dmm == DMM_VIDEO else 1)

    def test_dmm_preview_cache_is_separate_and_rechecks_daily(self):
        now = [1791936000.0]
        api = PublicAPI(clock=lambda: now[0], wallclock=lambda: now[0])
        fetch = Mock(return_value=DMM_VIDEO)
        for _ in range(2):
            self.assertEqual(api.official_preview('RCTD-764', fetch, provider='dmm', released='2020-01-01')['url'], DMM_VIDEO)
        self.assertEqual(fetch.call_count, 1)
        now[0] += 86400
        api.official_preview('RCTD-764', fetch, provider='dmm', released='2020-01-01')
        self.assertEqual(fetch.call_count, 2)
        other = Mock(return_value=VIDEO)
        self.assertEqual(api.official_preview('RCTD-764', other, provider='prestige')['url'], VIDEO)
        other.assert_called_once()

    def test_official_preview_is_available_when_webui_metadata_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            contract = WebContract(fresh_ledger(root), cover_root=root / 'covers')
            self.addCleanup(contract.stop_background_jobs)
            api = PublicAPI()
            api.movie = Mock(side_effect=PublicSourceUnavailable('WebUI 未取得'))
            with patch('peach.web_jav_detail.PUBLIC_API', api), \
                    patch('peach.jav_official_preview.dmm_preview', return_value=DMM_VIDEO):
                payload = q_jav_detail(contract, {'code': 'RCTD-764'})
            self.assertEqual(payload['state'], 'partial')
            self.assertEqual(payload['preview_state'], 'ready')
            self.assertTrue(payload['preview_video_url'].startswith('/jav-preview/RCTD-764/'))

    def test_dmm_preview_precedes_webui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            contract = WebContract(fresh_ledger(root), cover_root=root / 'covers')
            self.addCleanup(contract.stop_background_jobs)
            api = PublicAPI()
            api.movie = Mock(return_value={'number': 'RCTD-764', 'id': 'ZN9Ad6', 'maker_name': 'ROCKET',
                'preview_video_url': 'https://jdforrepam.com/api/v1/movies/ttm3u8/preview/sample/720p.m3u8'})
            with patch('peach.web_jav_detail.PUBLIC_API', api), \
                    patch.object(api, 'official_preview', return_value={'url': DMM_VIDEO}) as official:
                payload = q_jav_detail(contract, {'code': 'RCTD-764'})
            ref = payload['preview_video_url'].rsplit('/', 1)[-1]
            self.assertEqual(PreviewCache(cache_for(contract)).source('RCTD-764', ref)['url'], DMM_VIDEO)
            self.assertEqual(official.call_args.kwargs['provider'], 'dmm')

    def transport(self, product):
        return Mock(side_effect=[HttpResponse(200, {}, json.dumps({'parentProduct': {'uuid': UUID}}).encode()),
                                 HttpResponse(200, {}, json.dumps(product).encode())])

    def product(self, **values):
        return {'uuid': UUID, 'sku': [{'deliveryItemId': 'EXM-001'}],
                'movie': {'path': '6/9/' + UUID + '.mp4', 'mimeType': 'video/mp4', 'isLimited': 0}, **values}

    def test_exact_sku_provides_declared_public_mp4_without_media_download(self):
        transport = self.transport(self.product())
        self.assertEqual(prestige_preview('EXM-001', Path('unused'), transport=transport), VIDEO)
        self.assertEqual(transport.call_count, 2)
        self.assertTrue(transport.call_args_list[0].args[0].url.endswith('/api/sku/item/EXM-001'))
        self.assertTrue(transport.call_args_list[1].args[0].url.endswith('/api/product/' + UUID))

    def test_undeclared_fields_wrong_identity_and_private_movies_are_unavailable(self):
        missing = self.product()
        del missing['movie']
        for product in [missing, self.product(uuid='another'), self.product(sku=[{'deliveryItemId': 'EXM-002'}]),
                        self.product(movie={'path': '../private.mp4', 'mimeType': 'video/mp4', 'isLimited': 0}),
                        self.product(movie={'path': '6/9/' + UUID + '.mp4', 'mimeType': 'video/mp4', 'isLimited': 1})]:
            with self.subTest(product=product), self.assertRaises(PublicSourceUnavailable):
                prestige_preview('EXM-001', Path('unused'), transport=self.transport(product))

    def test_source_absence_is_distinct_from_transport_failure(self):
        self.assertEqual(prestige_preview('EXM-001', Path('unused'), transport=self.transport(self.product(movie=None))), '')
        with self.assertRaises(PublicSourceUnavailable):
            prestige_preview('EXM-001', Path('unused'), transport=Mock(side_effect=httpx.ConnectError('offline')))

    def test_empty_preview_rechecks_daily_and_good_old_preview_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1791936000.0]
            root = Path(directory).resolve()
            api = PublicAPI(cache_root=root, clock=lambda: now[0], wallclock=lambda: now[0])
            fetch = Mock(return_value='')
            self.assertEqual(api.official_preview('EXM-001', fetch, released='2020-01-01')['url'], '')
            api.official_preview('EXM-001', fetch, released='2020-01-01')
            self.assertEqual(fetch.call_count, 1)
            now[0] += 86400
            fetch.return_value = VIDEO
            self.assertEqual(api.official_preview('EXM-001', fetch, released='2020-01-01')['url'], VIDEO)
            self.assertEqual(fetch.call_count, 2)
            now[0] += 86400 * 400
            restarted = PublicAPI(cache_root=root, clock=lambda: now[0], wallclock=lambda: now[0])
            forbidden = Mock(side_effect=AssertionError('来源请求不可执行'))
            self.assertEqual(restarted.official_preview('EXM-001', forbidden)['url'], VIDEO)
            forbidden.assert_not_called()

    def test_detail_projects_official_mp4_and_preserves_movie_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            contract = WebContract(fresh_ledger(root), cover_root=root / 'covers')
            self.addCleanup(contract.stop_background_jobs)
            movie = {'number': 'EXM-001', 'id': 'Exact', 'maker_name': 'PRESTIGE,プレステージ',
                     'release_date': '2020-01-01', 'preview_video_url': None}
            api = PublicAPI()
            api.movie = Mock(return_value=movie)
            with patch('peach.web_jav_detail.PUBLIC_API', api), \
                    patch('peach.jav_official_preview.prestige_preview', return_value=VIDEO) as official:
                payload = q_jav_detail(contract, {'code': 'EXM-001'})
                again = q_jav_detail(contract, {'code': 'EXM-001'})
            self.assertEqual(payload['preview_state'], 'ready')
            self.assertEqual(payload['preview_video_url'], again['preview_video_url'])
            self.assertFalse(payload['preview_video_url'].endswith('.m3u8'))
            ref = payload['preview_video_url'].rsplit('/', 1)[-1]
            self.assertEqual(PreviewCache(cache_for(contract)).source('EXM-001', ref), {'url': VIDEO, 'playlist': False, 'media_type': 'video/mp4'})
            official.assert_called_once()
            self.assertEqual(movie['preview_video_url'], None)

    def test_failed_official_preview_keeps_detail_and_reports_retryable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            contract = WebContract(fresh_ledger(root), cover_root=root / 'covers')
            self.addCleanup(contract.stop_background_jobs)
            api = PublicAPI()
            api.movie = Mock(return_value={'number': 'EXM-001', 'id': 'Exact', 'maker_name': 'Prestige'})
            with patch('peach.web_jav_detail.PUBLIC_API', api), \
                    patch('peach.jav_official_preview.prestige_preview', side_effect=PublicSourceUnavailable('预告连接未取得')):
                payload = q_jav_detail(contract, {'code': 'EXM-001'})
            self.assertEqual(payload['state'], 'ready')
            self.assertEqual(payload['preview_state'], 'unavailable')
            self.assertIn('预告连接未取得', payload['message'])
