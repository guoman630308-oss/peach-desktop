"""JavLibrary 的作品身份、取页边界与人工验证预算。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from peach.http import HttpResponse
from peach.library_processing import LibraryMetadataProvider
from peach.processing_control import ActiveDeadline
from peach.sources import FailureReason, Page, Session, SiteRecord, SourceFailure
from peach.sources.javlibrary import JavLibrarySource

DETAIL = '''<html><h3 class="post-title"><a href="?v=javtest123">ABF-395 测试作品</a></h3>
<div id="video_id"><td class="text">ABF-395</td></div>
<div id="video_date"><td class="text">2026-01-02</td></div>
<div id="video_length"><span class="text">120</span></div>
<div id="video_maker"><td class="text"><a>测试厂牌</a></td></div>
<div id="video_label"><td class="text">测试发行商</td></div>
<div id="video_director"><td class="text">测试导演</td></div>
<div id="video_cast"><span class="star"><a>测试演员</a></span><span class="star"><a>测试演员</a></span></div>
<div id="video_genres"><span class="genre"><a>测试标签</a></span></div>
<img id="video_jacket_img" src="//pics.dmm.co.jp/mono/movie/adult/118abf00395/118abf00395pl.jpg"></html>'''
SEARCH = '''<div class="videothumblist">
<div class="video"><a href="?v=javwrong12"><div class="id">ABF-3950</div></a></div>
<div class="video"><a href="?v=javtest123"><div class="id">ABF-395</div></a></div></div>'''


class JavLibraryTests(unittest.TestCase):
    def setUp(self):
        self.source = JavLibrarySource()
        self.search = 'https://www.javlibrary.com/cn/vl_searchbyid.php?keyword=ABF-395'
        self.detail = 'https://www.javlibrary.com/cn/?v=javtest123'

    def test_fields_and_cover_are_read_from_the_current_work_only(self):
        result = self.source.parse(Page(self.detail, DETAIL.encode()), 'ABF-395')
        self.assertEqual((result.code, result.title, result.studio), ('ABF-395', '测试作品', '测试厂牌'))
        self.assertEqual((result.label, result.director, result.release_date, result.runtime),
                         ('测试发行商', '测试导演', '2026-01-02', 120))
        self.assertEqual(result.performers, ({'japanese_name': '测试演员'},))
        self.assertEqual(result.tags, ('测试标签',))
        self.assertTrue(result.cover_url.startswith('https://pics.dmm.co.jp/'))

    def test_search_follows_only_the_exact_release_code(self):
        calls = []
        def transport(request, _timeout, _limit):
            calls.append(request.url)
            return HttpResponse(200, {}, (SEARCH if len(calls) == 1 else DETAIL).encode())
        result = self.source.query('ABF-395', session=Session(transport))
        self.assertEqual(calls, [self.search, self.detail])
        self.assertEqual(result.source_url, self.detail)

    def test_a_search_redirect_body_can_already_be_the_detail_page(self):
        calls = []
        def transport(request, _timeout, _limit):
            calls.append(request.url)
            return HttpResponse(200, {}, DETAIL.encode())
        result = self.source.query('ABF-395', session=Session(transport))
        self.assertEqual(calls, [self.search])
        self.assertEqual(result.source_url, self.detail)

    def test_challenges_login_walls_and_changed_layouts_are_not_missing_movies(self):
        for body, reason in (
            ('<title>Just a moment...</title>', FailureReason.CLOUDFLARE_CHALLENGE),
            ('<input type="password">', FailureReason.AUTH_REQUIRED),
            ('<html>unexpected layout</html>', FailureReason.PARSE_ERROR),
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(SourceFailure) as failure:
                    self.source.parse(Page(self.detail, body.encode()), 'ABF-395')
                self.assertEqual(failure.exception.reason, reason)

    def test_mismatched_detail_identity_is_rejected(self):
        with self.assertRaises(SourceFailure) as failure:
            self.source.parse(Page(self.detail, DETAIL.replace('ABF-395', 'ABF-3950').encode()), 'ABF-395')
        self.assertEqual(failure.exception.reason, FailureReason.NOT_FOUND)

    def test_untrusted_result_links_are_not_fetched(self):
        calls = []
        def transport(request, _timeout, _limit):
            calls.append(request.url)
            return HttpResponse(200, {}, SEARCH.replace('?v=javtest123', 'https://other.example/?v=javtest123').encode())
        with self.assertRaises(SourceFailure):
            self.source.query('ABF-395', session=Session(transport))
        self.assertEqual(calls, [self.search])

    def test_an_empty_result_list_is_a_definitive_miss(self):
        with patch.object(Session, 'get', return_value=Page(self.search, b'<div class="videothumblist"></div>')):
            with self.assertRaises(SourceFailure) as failure:
                self.source.fetch('ABF-395', session=Session(None))
        self.assertEqual(failure.exception.reason, FailureReason.NOT_FOUND)

    def test_private_and_non_web_cover_addresses_are_ignored(self):
        for address in ('javascript:alert(1)', 'http://127.0.0.1/image.jpg', ''):
            with self.subTest(address=address):
                result = self.source.parse(Page(self.detail, DETAIL.replace(
                    '//pics.dmm.co.jp/mono/movie/adult/118abf00395/118abf00395pl.jpg', address).encode()), 'ABF-395')
                self.assertEqual(result.cover_urls, ())

    def test_human_verification_extends_the_detail_budget_and_preserves_pause_tracking(self):
        deadlines = []
        def get(session, url, **_options):
            deadlines.append(session.deadline)
            return Page(url, (SEARCH if len(deadlines) == 1 else DETAIL).encode())
        deadline = ActiveDeadline(100, SimpleNamespace(paused_seconds=0))
        with patch.object(Session, 'get', autospec=True, side_effect=get), \
                patch('peach.sources.javlibrary.monotonic', side_effect=[0, 75]):
            self.source.fetch('ABF-395', session=Session(None, deadline))
        self.assertIsInstance(deadlines[1], ActiveDeadline)
        self.assertEqual(float(deadlines[1]), 130)

    def test_provider_excludes_the_manual_wait_from_later_source_budgets(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LibraryMetadataProvider(Path(temporary))
            try:
                with patch('peach.sources.javlibrary.JavLibrarySource.query',
                           return_value=SiteRecord('javlibrary', 'javlibrary-page', 'ABF-395', self.detail)), \
                        patch('peach.library_processing.time.monotonic', side_effect=[100, 180]):
                    provider.site('javlibrary', 'ABF-395')
                self.assertEqual(provider.excused, 35)
            finally:
                provider.close()

    def test_a_following_community_source_keeps_its_budget_after_manual_verification(self):
        deadlines = []
        with tempfile.TemporaryDirectory() as temporary:
            provider = LibraryMetadataProvider(Path(temporary))
            def query(source, code, *, deadline):
                deadlines.append(deadline)
                if source == 'javlibrary':
                    provider.excused += 70
                return {'id': code}
            try:
                with patch.object(provider, 'site', side_effect=query):
                    provider._ask_community('ABF-395', ('javlibrary', 'javbus'), 100, lambda _: None)
                self.assertEqual(deadlines, [100, 170])
            finally:
                provider.close()
