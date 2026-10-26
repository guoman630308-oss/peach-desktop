"""匿名协议、身份核对、图片包装与只读详情投影。"""
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from peach.http import HttpResponse
from peach.javdb_public import PublicAPI, PublicSourceUnavailable, decode_image, preview_video_url
from peach.javdb_public import PublicBudgetExceeded, PublicRequestBudget, actor_gender
from peach.web_jav_detail import q_jav_detail, q_jav_image, q_jav_reviews, q_jav_resources
from peach.web_state import WebContract
from peach.web_router import dispatch_api_get
from support.ledger import fresh_ledger


class PublicProtocolTests(unittest.TestCase):
    def test_api_and_image_cooldowns_have_independent_scope(self):
        requests = []
        def transport(request, *_):
            requests.append(request.url)
            return HttpResponse(429 if request.url.endswith('blocked') else 200, {}, b'pixels')
        api = PublicAPI(transport, clock=lambda: 100, sleeper=lambda _: None)
        with self.assertRaises(PublicSourceUnavailable):
            api._get('https://api.example.test/blocked')
        self.assertEqual(api._get('https://tp.spfcas.com/actor.jpg', image=True).status, 200)
        api.paused_until = 0
        with self.assertRaises(PublicSourceUnavailable):
            api._get('https://tp.spfcas.com/blocked', image=True)
        self.assertEqual(api._get('https://api.example.test/ready').status, 200)
        count = len(requests)
        with self.assertRaises(PublicSourceUnavailable):
            api._get('https://tp.spfcas.com/actor.jpg', image=True)
        self.assertEqual(len(requests), count)

    def test_pending_image_keeps_metadata_readable_and_merges_same_key(self):
        api = PublicAPI()
        started, release = threading.Event(), threading.Event()
        calls = []
        def fetch():
            calls.append(1)
            started.set()
            self.assertTrue(release.wait(3))
            return 'pixels'
        api._cached('movie:EXM-001', lambda: {'id': 'Exact'})
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(api._cached, 'image:Exact:0', fetch)
            self.assertTrue(started.wait(1))
            same = pool.submit(api._cached, 'image:Exact:0', fetch)
            metadata = pool.submit(api.peek_movie, 'EXM-001')
            try:
                self.assertEqual(metadata.result(timeout=1), {'id': 'Exact'})
            finally:
                release.set()
            self.assertEqual(first.result(timeout=1), 'pixels')
            self.assertEqual(same.result(timeout=1), 'pixels')
        self.assertEqual(calls, [1])
        self.assertEqual(api.pending, {})

    def test_images_use_three_slots_and_leave_api_interval_intact(self):
        api = PublicAPI(lambda *_: HttpResponse(200, {}, b'pixels'))
        api._cached('movie:EXM-001', lambda: {'id': 'Exact', 'number': 'EXM-001',
                    'preview_images': [f'https://tp.spfcas.com/{index}.jpg' for index in range(6)]})
        started, release = threading.Event(), threading.Event()
        lock = threading.Lock()
        active = peak = 0
        def transport(*_):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 3:
                    started.set()
            self.assertTrue(release.wait(3))
            with lock:
                active -= 1
            return HttpResponse(200, {}, b'pixels')
        api.transport = transport
        with patch.object(api.limiter, 'wait') as limiter, patch('peach.javdb_public.decode_image', return_value=b'jpeg'):
            with ThreadPoolExecutor(max_workers=6) as pool:
                images = [pool.submit(api.image, 'EXM-001', str(index)) for index in range(6)]
                try:
                    self.assertTrue(started.wait(1))
                finally:
                    release.set()
                for image in images:
                    self.assertTrue(image.result(timeout=2).startswith('data:image/jpeg;base64,'))
            self.assertEqual(peak, 3)
            limiter.assert_not_called()
            api._get('https://javdb.com/example')
            limiter.assert_called_once()

    def test_gender_uses_only_explicit_supported_source_values(self):
        for value, expected in ((0, "female"), (1, "male"), (None, "unknown"),
                                (False, "unknown"), (2, "unknown"), ("male", "male"), ("0", "unknown")):
            self.assertEqual(actor_gender({"gender": value}), expected)
        self.assertEqual(actor_gender({"name": "示例姓名"}), "unknown")

    def test_request_budget_stops_before_exceeding_transport_count(self):
        api = self.api()
        budget = PublicRequestBudget(max_requests=2)
        with api.request_budget(budget):
            with self.assertRaises(PublicBudgetExceeded):
                api.movie("EXM-001")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(budget.requests, 2)
        self.assertEqual(budget.stop_reason, "request_budget")

    def test_known_movie_identity_skips_search_and_keeps_full_detail_validation(self):
        api = self.api()
        api.remember_movie_identity('EXM-001', {'id': 'Exact', 'number': 'EXM-001'})
        self.assertEqual(api.movie('EXM-001')['id'], 'Exact')
        self.assertFalse(any('/v2/search' in request.url for request in self.requests))
        wrong = self.api(wrong_code=True)
        wrong.remember_movie_identity('EXM-001', {'id': 'Exact', 'number': 'EXM-001'})
        with self.assertRaises(PublicSourceUnavailable):
            wrong.movie('EXM-001')

    def test_identity_hint_requires_matching_number_and_safe_source_identifier(self):
        api = self.api()
        api.remember_movie_identity('EXM-001', {'id': '../Other', 'number': 'EXM-001'})
        api.remember_movie_identity('EXM-001', {'id': 123, 'number': 'EXM-001'})
        api.remember_movie_identity('EXM-001', {'id': 'Other', 'number': 'EXM-002'})
        self.assertEqual(api.movie('EXM-001')['id'], 'Exact')
        self.assertTrue(any('/v2/search' in request.url for request in self.requests))

    def api(self, *, wrong_code=False, blocked=False):
        self.requests = []

        def transport(request, timeout, max_bytes):
            self.requests.append(request)
            if request.url.endswith('/'):
                body = b'<script src="/assets/index-test.js"></script>'
            elif '/assets/' in request.url:
                body = b'const S1="test-middle",E1="test-suffix";'
            else:
                if blocked:
                    return HttpResponse(429, {}, b'')
                if '/search' in request.url:
                    data = {'movies': [{'id': 'Exact', 'number': 'EXM-001'}, {'id': 'Other', 'number': 'EXM-002'}]}
                elif '/reviews' in request.url:
                    data = {'reviews': [{'id': 'second' if 'page=2' in request.url else 'first', 'content': '示例评论'}]}
                else:
                    data = {'movie': {'id': 'Exact', 'number': 'EXM-002' if wrong_code else 'EXM-001', 'title': '示例作品'}}
                body = json.dumps({'success': 1, 'data': data}).encode()
            return HttpResponse(200, {}, body)

        return PublicAPI(transport, sleeper=lambda _: None)

    def test_exact_work_is_cached_and_signed_without_credentials(self):
        api = self.api()
        self.assertEqual(api.movie('EXM001')['number'], 'EXM-001')
        self.assertEqual(api.movie('EXM-001')['id'], 'Exact')
        self.assertEqual(len(self.requests), 4)
        for request in self.requests[2:]:
            self.assertIn('jdsignature', request.headers)
            self.assertNotIn('Authorization', request.headers)
            self.assertNotIn('Cookie', request.headers)
        self.assertEqual(api.peek_movie('exm001')['id'], 'Exact')
        self.assertIsNone(api.peek_movie('EXM-999'))

    def test_mismatched_detail_is_rejected(self):
        with self.assertRaisesRegex(PublicSourceUnavailable, '身份不匹配'):
            self.api(wrong_code=True).movie('EXM-001')

    def test_rate_limit_stops_retrying(self):
        api = self.api(blocked=True)
        with self.assertRaises(PublicSourceUnavailable):
            api.movie('EXM-001')
        count = len(self.requests)
        with self.assertRaises(PublicSourceUnavailable):
            api.movie('EXM-001')
        self.assertEqual(len(self.requests), count)

    def test_local_retry_does_not_extend_source_cooldown(self):
        api = self.api(blocked=True)
        stamp = [100.0]
        api.clock = lambda: stamp[0]
        with self.assertRaises(PublicSourceUnavailable):
            api.movie('EXM-001')
        deadline = api.paused_until
        stamp[0] += 890
        with self.assertRaises(PublicSourceUnavailable):
            api.movie('EXM-001')
        self.assertEqual(api.paused_until, deadline)

    def test_missing_work_does_not_block_another_work(self):
        api = self.api()
        with self.assertRaises(PublicSourceUnavailable):
            api.movie('EXM-999')
        self.assertEqual(api.movie('EXM-001')['id'], 'Exact')

    def test_transport_failure_cools_source_without_repeating_io(self):
        api = self.api()
        api._signature()
        with patch.object(api, 'transport', side_effect=OSError('连接未取得')) as transport:
            with self.assertRaises(PublicSourceUnavailable):
                api.movie('EXM-001')
            self.assertGreater(api.paused_until, api.clock())
            with self.assertRaises(PublicSourceUnavailable):
                api.movie('EXM-001')
            self.assertEqual(transport.call_count, 1)

    def test_reviews_request_page_and_enforce_budget(self):
        api = self.api()
        self.assertEqual(api.reviews('EXM-001', 2)[0]['id'], 'second')
        with self.assertRaises(ValueError):
            api.reviews('EXM-001', 9)

    def test_image_wrappers_decode_to_real_pixels(self):
        original = io.BytesIO()
        Image.new('RGB', (40, 20), (40, 100, 160)).save(original, 'PNG')
        raw = original.getvalue()
        for wrapped in (raw, bytes([31]) + bytes(b ^ 31 for b in raw), bytes(b ^ 127 for b in raw)):
            with self.subTest(length=len(wrapped)):
                with Image.open(io.BytesIO(decode_image(wrapped))) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.size, (40, 20))
        with self.assertRaises(PublicSourceUnavailable):
            decode_image(b'<html>unavailable</html>')

    def test_original_cover_keeps_jpeg_bytes_and_dimensions(self):
        output = io.BytesIO()
        Image.new('RGB', (2000, 1100), (40, 100, 160)).save(output, 'JPEG', quality=94)
        raw = output.getvalue()
        for body in (raw, bytes([31]) + bytes(value ^ 31 for value in raw)):
            self.assertEqual(decode_image(body, original=True), raw)
        with Image.open(io.BytesIO(decode_image(raw))) as preview:
            self.assertEqual(preview.size, (1200, 660))

    def test_original_image_cache_is_separate_from_preview(self):
        api = self.api()
        output = io.BytesIO()
        Image.new('RGB', (2000, 1100), (40, 100, 160)).save(output, 'JPEG')
        raw = output.getvalue()
        with patch.object(api, '_get', return_value=HttpResponse(200, {}, raw)) as request:
            preview = api.image_from_url('https://tp.spfcas.com/image.jpg')
            original = api.image_from_url('https://tp.spfcas.com/image.jpg', original=True)
            self.assertNotEqual(preview, original)
            self.assertEqual(api.image_from_url('https://tp.spfcas.com/image.jpg', original=True), original)
        self.assertEqual(request.call_count, 2)

    def test_preview_entry_accepts_only_the_public_hls_origin_and_path(self):
        url = 'https://jdforrepam.com/api/v1/movies/ttm3u8/preview/123/0/720p.m3u8?token=sample'
        self.assertEqual(preview_video_url({'preview_video_url': url}), url)
        for value in (url.replace('https:', 'http:'), url.replace('jdforrepam.com', 'jdforrepam.com.evil.example'),
                      url.replace('https://', 'https://user:pass@'), url.replace('/preview/', '/private/'),
                      url.replace('.com/', '.com:8443/'), url + '#fragment', 'javascript:alert(1)',
                      'https://jdforrepam.com:wrong/preview.m3u8'):
            with self.subTest(url=value):
                self.assertEqual(preview_video_url({'preview_video_url': value}), '')

    def test_actor_images_are_bound_to_current_work_and_cached(self):
        api = self.api()
        original = io.BytesIO()
        Image.new('RGB', (40, 60)).save(original, 'PNG')
        movie = {'id': 'Exact', 'actors': [{'id': 'Person', 'avatar_url': 'https://tp.spfcas.com/avatars/person.jpg'}]}
        with patch.object(api, 'movie', return_value=movie), \
                patch.object(api, '_get', return_value=HttpResponse(200, {}, original.getvalue())) as get:
            first = api.image('EXM-001', 'actor:Person')
            self.assertTrue(first.startswith('data:image/jpeg;base64,'))
            self.assertEqual(api.image('EXM-001', 'actor:Person'), first)
            get.assert_called_once()
            with self.assertRaises(ValueError):
                api.image('EXM-001', 'actor:OtherPerson')
            movie['actors'][0]['avatar_url'] = 'https://private.example/person.jpg'
            with self.assertRaises(PublicSourceUnavailable):
                api.image('EXM-001', 'actor:Person')
            get.assert_called_once()


class DetailProjectionTests(unittest.TestCase):
    def test_official_thumbnail_does_not_enable_a_javdb_cover(self):
        from peach.web_jav_detail import _artwork_fields
        for url in ('https://c0.jdbstatic.com/a.jpg', 'https://tp.spfcas.com/a.jpg?expires=100'):
            with self.subTest(url=url):
                source = {'id': 'Exact', 'cover_url': url, 'thumb_url': 'https://pics.dmm.co.jp/a.jpg'}
                with patch('peach.web_jav_detail._preview_fields', return_value={}):
                    result = _artwork_fields(self.contract, 'MIDA-085', {}, source)
                self.assertFalse(result['source_image'])
                self.assertEqual(result['cover_url'], '')

    def test_explicit_source_evidence_reuses_detail_image_bytes(self):
        import base64
        original = io.BytesIO()
        Image.new('RGB', (800, 438), '#777').save(original, 'JPEG')
        data_url = 'data:image/jpeg;base64,' + base64.b64encode(original.getvalue()).decode('ascii')
        with patch('peach.web_jav_detail.PUBLIC_API.image', return_value=data_url) as fetch:
            detail = q_jav_image(self.contract, {'code': 'MIDA-085'})
        with patch('peach.web_jav_detail.PUBLIC_API.image', side_effect=AssertionError('缓存命中不请求来源')):
            self.assertEqual(q_jav_image(self.contract, {'code': 'MIDA-085'})['image'], data_url)
        self.assertTrue(detail['ok'])
        fetch.assert_called_once()

    def test_source_cover_url_projects_known_work_without_changing_source_fields(self):
        from peach.web_jav_detail import discovery_cover_url
        item = {'code': 'MIDA-085', 'cover_url': None, 'link': 'https://javdb.com/v/Exact'}
        self.assertEqual(discovery_cover_url(item, {}), '')
        self.assertIsNone(item['cover_url'])
        self.assertEqual(discovery_cover_url({**item, 'cover_url': 'https://c0.jdbstatic.com/a.jpg'}, {}), '')
        self.assertEqual(discovery_cover_url({**item, 'link': ''}, {'id': 'Exact'}), '')
        self.assertEqual(discovery_cover_url({**item, 'cover_url': 'https://pics.dmm.co.jp/a.jpg'}, {}), 'https://pics.dmm.co.jp/a.jpg')
        self.assertEqual(discovery_cover_url({**item, 'link': 'https://private.invalid/'}, {}), '')

    def test_saved_identity_fetches_actor_portrait_without_code_search(self):
        from peach.jav_discovery_metadata import discovery_metadata
        metadata = discovery_metadata(self.contract)
        metadata.remember('EXM-001', {'id': 'Exact', 'number': 'EXM-001', 'actors': [
            {'id': 'Person', 'name': '甲', 'gender': 0,
             'avatar_url': 'https://tp.spfcas.com/actor.jpg?signature=expired'}]})
        original = io.BytesIO()
        Image.new('RGB', (40, 60)).save(original, 'PNG')
        requests = []
        def transport(request, *_):
            requests.append(request.url)
            if '/search' in request.url:
                raise AssertionError('已有作品身份，无需番号搜索')
            if request.url.startswith('https://tp.spfcas.com/'):
                body = original.getvalue()
            elif request.url.endswith('/'):
                body = b'<script src="/assets/index-test.js"></script>'
            elif '/assets/' in request.url:
                body = b'const S1="test-middle",E1="test-suffix";'
            else:
                body = json.dumps({'success': 1, 'data': {'movie': {'id': 'Exact', 'number': 'EXM-001',
                    'actors': [{'id': 'Person', 'name': '甲', 'gender': 0,
                                'avatar_url': 'https://tp.spfcas.com/actor.jpg'}]}}}).encode()
            return HttpResponse(200, {}, body)
        api = PublicAPI(transport, sleeper=lambda _: None)
        with patch('peach.web_jav_detail.PUBLIC_API', api):
            first = q_jav_image(self.contract, {'code': 'EXM-001', 'image': 'actor:Person'})
            second = q_jav_image(self.contract, {'code': 'EXM-001', 'image': 'actor:Person'})
        self.assertTrue(first['ok'])
        self.assertTrue(first['image'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(second, first)
        self.assertEqual(sum('/movies/Exact' in url for url in requests), 1)
        self.assertEqual(sum(url.startswith('https://tp.spfcas.com/') for url in requests), 1)

    def test_saved_static_actor_portrait_does_not_fetch_movie_details(self):
        from peach.jav_discovery_metadata import discovery_metadata
        metadata = discovery_metadata(self.contract)
        metadata.remember('EXM-001', {'actors': [{'id': 'Actor1', 'name': '甲', 'gender': 0,
                                               'avatar_url': 'https://tp.spfcas.com/actor.jpg'}]})
        with patch('peach.web_jav_detail.PUBLIC_API.image_from_url', return_value='data:image/jpeg;base64,c2FtcGxl') as image, \
                patch('peach.web_jav_detail.PUBLIC_API.movie') as movie:
            result = q_jav_image(self.contract, {'code': 'EXM-001', 'image': 'actor:Actor1'})
        self.assertTrue(result['ok'])
        movie.assert_not_called()
        image.assert_called_once_with('https://tp.spfcas.com/actor.jpg')

    def setUp(self):
        for name in ('dmm_preview', 'javdb_preview'):
            patcher = patch('peach.jav_official_preview.' + name, return_value='')
            patcher.start()
            self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.contract = WebContract(fresh_ledger(root), cover_root=root / 'covers', candidate_root=root / 'generated')
        self.addCleanup(self.contract.stop_background_jobs)
        self.contract.followups.stop()

    def test_details_project_source_metadata_without_writing_ledger(self):
        movie = {'id': 'Exact', 'number': 'EXM-001', 'title': '示例作品', 'actors': [{'name': '示例人物'}],
                 'maker_name': '示例厂牌', 'series_name': '示例系列', 'tags': [{'name': '示例标签'}],
                 'score': 4.2, 'duration': 120, 'comments_count': 23, 'preview_images': []}
        with self.contract.database.read_connection() as connection:
            before = connection.execute('SELECT COUNT(*) FROM feed_discovery').fetchone()[0]
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value=movie):
            result = dispatch_api_get(self.contract, '/api/jav/detail', {'code': 'exm001'})
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['performer_refs'][0]['name'], '示例人物')
        self.assertIsNone(result['performer_refs'][0]['id'])
        self.assertEqual(result['tags'], ['示例标签'])
        self.assertEqual(result['source_url'], 'https://javdb.com/v/Exact')
        with self.contract.database.read_connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM feed_discovery').fetchone()[0], before)

    def test_male_source_records_are_retained_without_display_references(self):
        movie = {"id": "Exact", "number": "EXM-001", "actors": [
            {"name": "甲", "gender": 0}, {"name": "乙", "gender": 1}, {"name": "丙"}]}
        with patch("peach.web_jav_detail.PUBLIC_API.movie", return_value=movie):
            rows = q_jav_detail(self.contract, {"code": "EXM-001"})["performer_refs"]
        self.assertEqual([row["gender"] for row in rows], ["female", "unknown"])
        from peach.jav_discovery_metadata import discovery_metadata
        recorded, _ = discovery_metadata(self.contract).peek('EXM-001')
        self.assertEqual([{key: value for key, value in actor.items() if key != 'has_avatar'}
                          for actor in recorded['actors']], movie['actors'])

    def test_source_studio_letter_case_uses_a_unique_local_identity(self):
        with self.contract.database.write_transaction() as connection:
            connection.executemany("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) VALUES (?,'studio',?,?,'2026-10-12','2026-10-12')",
                                   [(7, 'Idea Pocket', 'idea pocket'), (8, 'IDEAPOCKET', 'ideapocket')])
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value={'id': 'Exact', 'maker_name': 'IDEA POCKET'}):
            reference = q_jav_detail(self.contract, {'code': 'EXM-001'})['studio_refs'][0]
        self.assertEqual((reference['id'], reference['name']), (7, 'Idea Pocket'))
        with self.contract.database.read_connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM entity').fetchone()[0], 2)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM entity_alias').fetchone()[0], 0)

    def test_source_studio_case_collision_keeps_the_source_name(self):
        with self.contract.database.write_transaction() as connection:
            connection.executemany("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) VALUES (?,'studio',?,?,'2026-10-12','2026-10-12')",
                                   [(7, '厂牌甲', '厂牌甲'), (8, '厂牌乙', '厂牌乙')])
            connection.executemany("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES (?,?,?,'test')",
                                   [(7, 'IDEA POCKET', 'idea pocket'), (8, 'idea pocket', 'idea pocket')])
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value={'id': 'Exact', 'maker_name': 'Idea Pocket'}):
            reference = q_jav_detail(self.contract, {'code': 'EXM-001'})['studio_refs'][0]
        self.assertIsNone(reference['id'])
        self.assertEqual(reference['name'], 'Idea Pocket')

    def test_combined_source_studio_writings_use_the_existing_identity_and_logo(self):
        with self.contract.database.write_transaction() as connection:
            connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) VALUES (7,'studio','Prestige','prestige','2026-10-12','2026-10-12')")
            connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES (7,'プレステージ','プレステージ','test')")
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value={'id': 'Exact', 'maker_name': 'PRESTIGE,プレステージ'}), \
                patch.object(self.contract, 'has_logo', side_effect=lambda name: name == 'Prestige') as logo:
            reference = q_jav_detail(self.contract, {'code': 'EXM-001'})['studio_refs'][0]
        self.assertEqual((reference['id'], reference['name'], reference['has_logo']), (7, 'Prestige', True))
        logo.assert_called_once_with('Prestige')
        with self.contract.database.read_connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM entity').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM entity_alias').fetchone()[0], 1)


    def test_source_failure_keeps_usable_detail_and_reviews_failure_is_explicit(self):
        with patch('peach.web_jav_detail.PUBLIC_API.movie', side_effect=PublicSourceUnavailable('资料未取得')):
            result = q_jav_detail(self.contract, {'code': 'EXM-001'})
        self.assertEqual(result['code'], 'EXM-001')
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['message'], '资料未取得')
        with patch('peach.web_jav_detail.PUBLIC_API.reviews', side_effect=PublicSourceUnavailable('评论未取得')):
            self.assertFalse(q_jav_reviews(self.contract, {'code': 'EXM-001'})['ok'])

    def test_invalid_identity_and_image_are_rejected_before_network(self):
        with patch('peach.web_jav_detail.PUBLIC_API.movie') as movie:
            with self.assertRaises(ValueError):
                q_jav_detail(self.contract, {'code': '../secret'})
            with self.assertRaises(ValueError):
                q_jav_image(self.contract, {'code': 'EXM-001', 'image': '-1'})
            movie.assert_not_called()

    def test_resources_normalize_and_deduplicate_valid_magnets(self):
        fingerprint = 'a' * 40
        rows = [{'hash': fingerprint, 'name': '示例资源', 'cnsub': True, 'hd': True,
                 'size': '3 GB', 'files_count': 2, 'created_at': '2026-10-12'},
                {'hash': fingerprint}, {'hash': 'invalid'}]
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value={'id': 'Exact'}), \
                patch('peach.web_jav_detail.PUBLIC_API.magnets', return_value=rows) as magnets, \
                patch('peach.web_jav_detail.PUBLIC_API.reviews', return_value=[]):
            result = q_jav_resources(self.contract, {'code': 'EXM-001', 'refresh': '1'})
        magnets.assert_called_once_with('EXM-001', refresh=True)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['info_hash'], fingerprint)
        self.assertTrue(result['items'][0]['uri'].startswith('magnet:?'))
        self.assertEqual(result['items'][0]['attributes'], ['中字', '高清'])
        self.assertEqual(result['source_url'], 'https://javdb.com/v/Exact')

    def test_resources_include_comment_ed2k_magnets_and_urls_with_origins(self):
        digest = 'a' * 40
        ed2k = 'ed2k://|file|www.98T.la@MIKR-096-U_1_chf3_prob4.mp4|19735225787|0936618B7ED1E5A0235FC6FBE27E104A|h=U5OZKZ4TELDTNANYLRNGQX5LCTDKAM52|/'
        first = [{'id': n, 'content': '正文'} for n in range(9)] + [{'id': 31, 'content': '4K60:' + ed2k, 'created_at': '2026-05-15'}]
        second = [{'id': 32, 'content': ed2k + '\nmagnet:?xt=urn:btih:' + digest + '\nhttps://example.test/download'}]
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value={'id': 'Exact'}), \
                patch('peach.web_jav_detail.PUBLIC_API.magnets', return_value=[{'hash': digest, 'size': '3 GB'}]), \
                patch('peach.web_jav_detail.PUBLIC_API.reviews', side_effect=[first, second]) as reviews:
            result = q_jav_resources(self.contract, {'code': 'MIKR-096', 'refresh': '1'})
        self.assertEqual([item['protocol'] for item in result['items']], ['magnet', 'ed2k', 'url'])
        link = result['items'][1]
        self.assertEqual(link['uri'], ed2k)
        self.assertEqual(link['source'], 'JavDB 评论')
        self.assertEqual(link['size'], '19,735,225,787 B')
        self.assertEqual(link['origins'], ['https://javdb.com/v/Exact#review-item-31', 'https://javdb.com/v/Exact#review-item-32'])
        self.assertEqual(reviews.call_count, 2)
        self.assertEqual(result['warnings'], [])

    def test_comment_failure_keeps_magnet_resources_and_reports_missing_comments(self):
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value={'id': 'Exact'}), \
                patch('peach.web_jav_detail.PUBLIC_API.magnets', return_value=[{'hash': 'a' * 40}]), \
                patch('peach.web_jav_detail.PUBLIC_API.reviews', side_effect=PublicSourceUnavailable('限流')):
            result = q_jav_resources(self.contract, {'code': 'EXM-001'})
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['warnings'], ['评论资源未取得：限流'])

    def test_comment_resource_scan_has_a_page_limit(self):
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value={'id': 'Exact'}), \
                patch('peach.web_jav_detail.PUBLIC_API.magnets', return_value=[]), \
                patch('peach.web_jav_detail.PUBLIC_API.reviews', return_value=[{'content': '正文'}] * 10) as reviews:
            result = q_jav_resources(self.contract, {'code': 'EXM-001'})
        self.assertEqual(reviews.call_count, 8)
        self.assertEqual(result['warnings'], ['已查询前 8 页评论资源。'])

    def test_magnet_failure_keeps_comment_resources(self):
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value={'id': 'Exact'}), \
                patch('peach.web_jav_detail.PUBLIC_API.magnets', side_effect=PublicSourceUnavailable('未取得')), \
                patch('peach.web_jav_detail.PUBLIC_API.reviews', return_value=[{
                    'id': 5, 'content': 'ed2k://|file|demo.mp4|123|' + 'b' * 32 + '|/'}]):
            result = q_jav_resources(self.contract, {'code': 'EXM-001'})
        self.assertEqual(result['state'], 'ready')
        self.assertEqual([item['protocol'] for item in result['items']], ['ed2k'])
        self.assertEqual(result['warnings'], ['磁链区资源未取得：未取得'])

    def test_source_avatars_and_studio_logos_preserve_each_identity(self):
        movie = {'id': 'Exact', 'actors': [
            {'id': 'Known', 'name': '甲', 'avatar_url': 'https://tp.spfcas.com/a.jpg'},
            {'id': 'Known', 'name': '甲', 'avatar_url': 'https://tp.spfcas.com/a.jpg'},
            {'id': 'Unknown', 'name': '乙', 'avatar_url': 'https://tp.spfcas.com/b.jpg'}], 'maker_name': '示例厂牌'}
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value=movie), \
                patch.object(self.contract, 'has_logo', return_value=True), \
                patch.object(self.contract, 'logo_version', return_value='logo-version'):
            result = q_jav_detail(self.contract, {'code': 'EXM-001'})
        self.assertEqual([(ref['name'], ref['source_actor_id']) for ref in result['performer_refs']],
                         [('甲', 'Known'), ('乙', 'Unknown')])
        self.assertTrue(all(ref['source_avatar'] and ref['id'] is None for ref in result['performer_refs']))
        self.assertEqual(result['studio_refs'][0]['has_logo'], True)
        self.assertEqual(result['studio_refs'][0]['logo_version'], 'logo-version')
        with patch('peach.web_jav_detail.PUBLIC_API.image_from_url', return_value='data:image/jpeg;base64,c2FtcGxl') as image:
            self.assertTrue(q_jav_image(self.contract, {'code': 'EXM-001', 'image': 'actor:Unknown'})['ok'])
        image.assert_called_once_with('https://tp.spfcas.com/b.jpg')

    def test_image_bytes_are_reused_without_querying_source_again(self):
        with patch('peach.web_jav_detail.PUBLIC_API.image', return_value='data:image/jpeg;base64,c2FtcGxl') as image:
            first = q_jav_image(self.contract, {'code': 'EXM-001'})
        image.assert_called_once()
        with patch('peach.web_jav_detail.PUBLIC_API.image', side_effect=PublicSourceUnavailable('offline')) as offline:
            replay = q_jav_image(self.contract, {'code': 'EXM-001'})
        self.assertEqual(replay, first)
        offline.assert_not_called()

    def test_local_performer_reference_keeps_canonical_identity_and_face_crop(self):
        movie = {'id': 'Exact', 'actors': [
            {'id': 'Known', 'name': '来源别名', 'avatar_url': 'https://tp.spfcas.com/a.jpg'},
            {'id': 'Unknown', 'name': '未登记人物', 'avatar_url': 'https://tp.spfcas.com/b.jpg'}]}
        def resolve(connection, kind, name):
            return {'id': 7, 'canonical_name': '规范人物'} if kind == 'performer' and name == '来源别名' else None
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value=movie), \
                patch('peach.web_jav_detail.entities.resolve_entity', side_effect=resolve), \
                patch.object(self.contract, 'has_entity_image', return_value=True), \
                patch.object(self.contract, 'entity_image_version', return_value='face-version'), \
                patch.object(self.contract, 'avatar_focus', return_value={'axis': 'y', 'pct': 7}):
            result = q_jav_detail(self.contract, {'code': 'EXM-001'})
        first, second = result['performer_refs']
        self.assertEqual((first['id'], first['name'], first['source_actor_id']), (7, '规范人物', 'Known'))
        self.assertEqual(first['avatar_focus'], {'axis': 'y', 'pct': 7})
        self.assertEqual(first['image_version'], 'face-version')
        self.assertIsNone(second['id'])
        self.assertFalse(second['has_image'])
        self.assertEqual(second['source_actor_id'], 'Unknown')

    def test_tag_projection_uses_taxonomy_and_user_decisions_without_losing_source(self):
        movie = {'id': 'Exact', 'tags': [{'name': name} for name in
                 ('荡妇', '戀腿癖', '恋腿癖', '中出', '足交', 'Creampie', '4小時以上作品', '未知类别')]}
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value=movie):
            result = q_jav_detail(self.contract, {'code': 'EXM-001'})
        self.assertEqual(result['tag_refs'], [
            {'name': '痴女', 'category': 'role', 'source_name': '荡妇'},
            {'name': '恋腿', 'category': 'story', 'source_name': '戀腿癖'},
            {'name': '中出内射', 'category': 'position', 'source_name': '中出'},
            {'name': '足交', 'category': 'position', 'source_name': '足交'},
            {'name': '未知类别', 'category': 'general', 'source_name': '未知类别'}])
        self.assertEqual(result['tags'], [tag['name'] for tag in movie['tags']])
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value=movie), \
                patch('peach.web_jav_detail.load_genre_decisions', return_value={'荡妇': None, '未知类别': '护士'}):
            result = q_jav_detail(self.contract, {'code': 'EXM-001'})
        self.assertNotIn('痴女', [ref['name'] for ref in result['tag_refs']])
        self.assertEqual(result['tag_refs'][-1], {'name': '护士', 'category': 'role', 'source_name': '未知类别'})

    def test_simplified_source_tags_use_local_names_and_exclude_cast_format(self):
        names = ['单体作品', '淫乱真实', '白天出轨', '戏剧', '深喉', '巨大阴茎']
        movie = {'id': 'Exact', 'tags': [{'name': name} for name in names]}
        with patch('peach.web_jav_detail.PUBLIC_API.movie', return_value=movie):
            result = q_jav_detail(self.contract, {'code': 'SNOS-399'})
        self.assertEqual(result['tags'], names)
        self.assertEqual([(ref['name'], ref['source_name']) for ref in result['tag_refs']],
                         [('淫乱真实', '淫乱真实'), ('出轨', '白天出轨'), ('剧情', '戏剧'),
                          ('深喉', '深喉'), ('巨根', '巨大阴茎')])

    def test_resource_source_failure_is_explicit(self):
        with patch('peach.web_jav_detail.PUBLIC_API.movie', side_effect=PublicSourceUnavailable('资料未取得')):
            result = q_jav_resources(self.contract, {'code': 'EXM-001'})
        self.assertEqual(result['state'], 'error')
        self.assertEqual(result['items'], [])
        self.assertEqual(result['error'], '资料未取得')
