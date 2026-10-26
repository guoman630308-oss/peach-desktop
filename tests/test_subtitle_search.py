"""中文字幕筛选、转换与下载边界；媒体均为临时夹具。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from peach import subtitle_search as source

SEARCH = '<table class="sub-table"><tr><td><a href="subs/42/ABP-758.html">ABP-758</a></td></tr><tr><td><a href="subs/43/English.html">English</a></td></tr></table>'
DETAIL = '<a href="/subs/42/ABP-758-zh-TW.srt">繁体</a><a href="/subs/42/ABP-758-zh-CN.srt">简体</a><a href="/subs/42/ABP-758-en.srt">English</a><a href="https://evil.example/evil-zh-CN.srt">bad</a>'
SRT = '1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n'


class SubtitleSearchTests(unittest.TestCase):
    def client(self, response):
        return mock.patch.object(source, '_client', return_value=httpx.Client(transport=httpx.MockTransport(response)))

    def test_search_lists_only_available_chinese_and_deduplicates_languages(self):
        def respond(request):
            if request.url.path == '/index.php':
                self.assertEqual(request.url.params['search'], 'ABP-758')
                return httpx.Response(200, text=SEARCH)
            return httpx.Response(200, text=DETAIL if '/42/' in request.url.path else '<a href="/subs/43/English-en.srt">English</a>')
        with self.client(respond):
            payload = source.search('ABP-758')
        self.assertEqual(payload['source'], 'SubtitleCat')
        self.assertFalse(payload['more'])
        self.assertEqual([(row['title'], row['language']) for row in payload['results']], [('ABP-758', '简体中文 / 繁体中文')])
        self.assertEqual(source._detail(payload['results'][0]['id']), source.SITE + '/subs/42/ABP-758.html')

    def test_files_expose_language_choices_without_download_urls(self):
        with self.client(lambda request: httpx.Response(200, text=DETAIL)):
            rows = source.files(source._id('/subs/42/ABP-758.html'))['files']
        self.assertEqual([row['language'] for row in rows], ['简体中文', '繁体中文'])
        self.assertEqual([row['index'] for row in rows], [0, 1])
        self.assertTrue(all(row['playable'] and 'url' not in row for row in rows))

    def test_release_search_filters_other_identities_before_pagination(self):
        titles = ['ABP-7580', 'XABP-758', 'ABP-757', 'ABP-00758', 'abp758 中文',
                  'ABP-758-kor-ja', 'ABP_758 中文字幕', 'ABP-758中文']
        html = '<table class="sub-table">' + ''.join(
            f'<tr><td><a href="/subs/{index}/track.html">{title}</a></td></tr>'
            for index, title in enumerate(titles)) + '</table>'
        details = []
        def respond(request):
            if request.url.path == '/index.php':
                self.assertEqual(request.url.params['search'], 'ABP-758')
                return httpx.Response(200, text=html)
            details.append(request.url.path)
            return httpx.Response(200, text=DETAIL)
        with self.client(respond):
            result = source.search('abp00758')
        self.assertEqual([row['title'] for row in result['results']], titles[3:])
        self.assertFalse(result['more'])
        self.assertEqual(len(details), 5)

    def test_keyword_search_keeps_the_query_and_language_filter(self):
        def respond(request):
            if request.url.path == '/index.php':
                self.assertEqual(request.url.params['search'], '电影片名')
                return httpx.Response(200, text=SEARCH)
            return httpx.Response(200, text=DETAIL)
        with self.client(respond):
            self.assertEqual(len(source.search('电影片名')['results']), 2)

    def test_matching_preserves_distribution_prefixes_and_date_separators(self):
        for title, code, expected in (
            ('390JAC-040 中文', 'JAC-040', False),
            ('259LUXU-1475 中文', 'LUXU-1475', True),
            ('092415_001 中文', '092415-001', False),
            ('FC2-PPV-1812235-zh', 'FC2-PPV-1812235', True),
            ('SSIS-291_(extract_from_Chinese)-en', 'SSIS-291', True),
        ):
            with self.subTest(title=title):
                self.assertEqual(bool(source._matches(title, code)), expected)

    def test_download_uses_selected_chinese_and_reuses_encoding_conversion(self):
        requested = []
        def respond(request):
            requested.append(request.url.path)
            return httpx.Response(200, text=DETAIL) if request.url.path.endswith('.html') else httpx.Response(200, content=SRT.encode('gbk'))
        with self.client(respond):
            text = source.load(source._id('/subs/42/ABP-758.html'), 1)
        self.assertIn('00:00:00.000 --> 00:00:02.000', text)
        self.assertIn('测试字幕', text)
        self.assertEqual(requested[-1], '/subs/42/ABP-758-zh-TW.srt')

    def test_missing_chinese_does_not_fall_back_to_other_languages(self):
        with self.client(lambda request: httpx.Response(200, text='<a href="/subs/42/test-en.srt">English</a>')):
            with self.assertRaisesRegex(source.SubtitleSearchError, '不存在'):
                source.load(source._id('/subs/42/ABP-758.html'), 0)

    def test_provider_errors_and_changed_pages_are_actionable(self):
        for response, message in ((httpx.Response(403), '无法连接'), (httpx.Response(200, text='captcha'), '页面未取得')):
            with self.subTest(message=message), self.client(lambda request, response=response: response):
                with self.assertRaisesRegex(source.SubtitleSearchError, message):
                    source.search('ABP-758')
        for query, offset in (('ab', 0), ('ABP-758', -1), ('ABP-758', 1), ('ABP-758', 60)):
            with self.subTest(offset=offset), self.assertRaises(source.SubtitleSearchError):
                source.search(query, offset)

    def test_untrusted_hosts_paths_and_redirects_are_refused(self):
        for url in ('https://127.0.0.1/subs/42/a.html', 'https://www.subtitlecat.com.evil.example/subs/42/a.html',
                    'file:///subs/42/a.html', 'https://www.subtitlecat.com:invalid/subs/42/a.html',
                    '/subs/42/%2fprivate.html', '/subs/42/a.html?redirect=evil', 'https://[invalid'):
            with self.subTest(url=url), self.assertRaises(source.SubtitleSearchError):
                source._url(url, '.html')
        with self.client(lambda request: httpx.Response(302, headers={'Location': 'https://127.0.0.1/private'})):
            with self.assertRaisesRegex(source.SubtitleSearchError, '地址不受支持'):
                source.files(source._id('/subs/42/a.html'))
        with self.assertRaisesRegex(source.SubtitleSearchError, '编号无效'):
            source.files('invalid!')

    def test_conversion_rejects_invalid_timeline_graphics_and_oversized_input(self):
        for data, fmt in ((b'garbage', 'srt'), (b'binary', 'sub'), (b'x' * (source.MAX_BYTES + 1), 'vtt')):
            with self.subTest(fmt=fmt), self.assertRaises(source.SubtitleSearchError):
                source.convert(data, fmt)
        with self.client(lambda request: httpx.Response(200, content=b'x' * (source.MAX_BYTES + 1))):
            with self.assertRaisesRegex(source.SubtitleSearchError, '4 MiB'):
                source.files(source._id('/subs/42/a.html'))


class SubtitleUploadApiTests(unittest.TestCase):
    def setUp(self):
        from peach.api import create_app
        from peach.config import PeachSettings
        from tests.support.ledger import fresh_ledger
        from tests.support.web import route_client
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.app = create_app(PeachSettings(db_path=fresh_ledger(self.root), configured=True, token=''))
        self.client = route_client(self.app)

    def test_local_file_is_converted_without_registering_a_subtitle(self):
        with self.client as client:
            response = client.post('/api/subtitles/convert?name=test.srt', content=SRT.encode('utf-16'))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers['content-type'].startswith('text/vtt'))
            self.assertIn('测试字幕', response.text)
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_upload_limits_and_provider_api_contract(self):
        with self.client as client:
            self.assertEqual(client.post('/api/subtitles/convert?name=test.sub', content=b'binary').status_code, 400)
            self.assertEqual(client.post('/api/subtitles/convert?name=test.vtt', content=b'x' * (source.MAX_BYTES + 1)).status_code, 413)
            with mock.patch.object(source, 'search', return_value={'results': [], 'more': False, 'source': 'SubtitleCat'}):
                self.assertEqual(client.get('/api/subtitles/search?q=ABP-758').json()['source'], 'SubtitleCat')
            self.assertEqual(client.get('/api/subtitles/subtitlecat/invalid!').status_code, 400)

    def test_subtitle_routes_require_the_application_password(self):
        from dataclasses import replace
        self.app.state.settings = replace(self.app.state.settings, token='secret')
        with self.client as client:
            self.assertEqual(client.post('/api/subtitles/convert?name=test.srt', content=b'test').status_code, 401)
            self.assertEqual(client.get('/api/subtitles/search?q=ABP-758').status_code, 401)
            self.assertEqual(client.get('/api/subtitles/subtitlecat/invalid').status_code, 401)
