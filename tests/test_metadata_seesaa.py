import csv
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from peach.http import HttpResponse
from peach.metadata import MetadataProviderError
from peach.sources import SITE_SOURCES, Page, Session
from peach.sources.seesaa import ROOT, SEESAA, SeesaaSource, WikiPages, page_url


def fixture(code='ABC-007', actors=None):
    actors = actors if actors is not None else '<a href="/w/sougouwiki/d/OtherName">架空花子</a>／<a href="/w/sougouwiki/d/B">架空春子</a>'
    return (f'<div id="page-body"><div class="user-area"><table id="table_edit_1">'
            '<tr><th>NO</th><th>PHOTO</th><th>TITLE</th><th>ACTRESS</th><th>RELEASE</th><th>NOTE</th></tr>'
            f'<tr><td>{code}</td><td><a href="https://example.org/cover.jpg">画像</a></td>'
            f'<td>架空の作品</td><td>{actors}</td><td>2024/11/20</td><td>別版 ABC-008</td></tr>'
            '</table></div></div><aside>ABC-008 別の出演者</aside>').encode('euc_jp')


def parse_page(body, url):
    """一页作品表的每一行，投影成来源快照那份 dict。"""
    return [row.payload() for row in SeesaaSource().rows(Page(url, body))]


class SeesaaMetadataTests(unittest.TestCase):
    def source(self, root, transport, **kwargs):
        pages = kwargs.pop('pages', ())
        return SeesaaSource(pages=pages), Session(WikiPages(root, transport=transport, limiter=Mock(), **kwargs))

    def test_table_scopes_code_cast_date_and_evidence(self):
        row = parse_page(fixture(), ROOT+'d/Label')[0]
        self.assertEqual(row['id'], 'ABC-007')
        self.assertEqual(row['release_date'], '2024-11-20')
        self.assertEqual([a['japanese_name'] for a in row['actresses']], ['架空花子', '架空春子'])
        self.assertEqual(row['wiki_evidence']['performer_links'][0]['source_url'], ROOT+'d/OtherName')
        self.assertEqual(row['wiki_evidence']['notes'], '別版 ABC-008')
        self.assertEqual(row['cover_url'], 'https://example.org/cover.jpg')

    def test_uncertain_or_unlinked_cast_is_evidence_only(self):
        for tail in ('／？', '／3人目', '／未確認', '／未リンク花子'):
            row = parse_page(fixture(actors='<a href="/w/sougouwiki/d/A">架空花子</a>'+tail), ROOT+'d/Label')[0]
            self.assertEqual(row['actresses'], [])
            self.assertFalse(row['wiki_evidence']['performers_complete'])

    def test_edit_comments_and_unscoped_tables_are_not_records(self):
        self.assertEqual(parse_page(fixture().replace(b'user-area', b'comments'), ROOT+'d/Label'), [])
        for url in ('https://evil.test/w/sougouwiki/d/X', ROOT+'e/edit', ROOT+'d/X?edit=1'):
            with self.assertRaises(ValueError):
                page_url(url)

    def test_search_discovers_label_without_using_person_as_entire_cast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            search = b'<div class="result-box"><div class="body"><h3><a href="/w/sougouwiki/d/Person">Person</a></h3></div></div>'
            person = '<div id="page-body"><div class="user-area"><a href="/w/sougouwiki/d/Label">(レーベル一覧)</a></div></div>'.encode('euc_jp')
            transport = Mock(side_effect=[HttpResponse(200, {}, search), HttpResponse(200, {}, person), HttpResponse(200, {}, fixture())])
            source, session = self.source(root, transport)
            self.assertEqual(len(source.query('ABC-007', session=session).payload()['actresses']), 2)
            self.assertEqual(transport.call_count, 3)
            self.assertIsNone(source.match('123ABC-007'))
            self.assertEqual(transport.call_count, 3)

    def test_success_cache_resumes_and_failures_remain_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            url = ROOT+'d/Label'
            first, session = self.source(root, Mock(return_value=HttpResponse(200, {}, fixture())), pages=[url])
            first.query('ABC-007', session=session)
            transport = Mock(side_effect=AssertionError('unexpected network'))
            resumed, session = self.source(root, transport, pages=[url])
            self.assertEqual(resumed.query('ABC-007', session=session).payload()['id'], 'ABC-007')
            transport.assert_not_called()

    def test_block_budget_timeout_and_large_responses_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for response in (HttpResponse(403, {}, b''), HttpResponse(429, {}, b''),
                             HttpResponse(200, {}, b'Just a moment')):
                transport = Mock(return_value=response)
                source, session = self.source(root, transport)
                for code in ('ABC-007', 'ABC-008'):
                    with self.assertRaises(MetadataProviderError):
                        source.query(code, session=session)
                self.assertEqual(transport.call_count, 1)
            source, session = self.source(root, Mock(side_effect=httpx.ReadTimeout('timeout')))
            with self.assertRaises(MetadataProviderError) as caught:
                source.query('ABC-007', session=session)
            self.assertTrue(caught.exception.retryable)
            source, session = self.source(root, Mock(), max_requests=0)
            with self.assertRaises(MetadataProviderError):
                source.query('ABC-007', session=session)
            session.transport.transport.assert_not_called()
            source, session = self.source(root, Mock(return_value=HttpResponse(200, {}, b'x'*(4*1024*1024+1))))
            with self.assertRaises(MetadataProviderError) as caught:
                source.query('ABC-007', session=session)
            self.assertEqual(caught.exception.kind, 'size_limit')

    def test_authentication_walls_are_told_apart_from_missing_pages(self):
        """401、403 与跳到登录页都是 auth；404 仍然是「这次没取到」，不许混成一档。

        混成一档的代价是批处理按网络抖动退让几百秒后回来重试，而凭据没换，
        重试多少次都是同一个结果。
        """
        page = ROOT+'d/Label'
        cases = ((HttpResponse(401, {}, b''), 'auth', 401),
                 (HttpResponse(403, {}, b''), 'auth', 403),
                 (HttpResponse(200, {}, b'<html>login</html>',
                               'https://seesaawiki.jp/auth/login'), 'auth', 200),
                 (HttpResponse(404, {}, b''), 'unavailable', 404))
        for response, kind, status in cases:
            with self.subTest(status=response.status, url=response.url), \
                    tempfile.TemporaryDirectory() as tmp:
                source, session = self.source(Path(tmp).resolve(), Mock(return_value=response))
                with self.assertRaises(MetadataProviderError) as caught:
                    source.load(page, session=session)
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.retryable, kind != 'auth')
                # auth 是「换凭据就能继续」，不是定论，所以不会被冻进错误快照复用。
                self.assertEqual(caught.exception.temporary, kind == 'auth')

    def test_conflicting_rows_and_neighbor_codes_are_not_selected(self):
        source = SeesaaSource()
        source.tables['one'] = source.rows(Page(ROOT+'d/One', fixture()))
        self.assertIsNone(source.match('ABC-008'))
        source.tables['two'] = source.rows(Page(ROOT+'d/Two', fixture(actors='<a href="/w/sougouwiki/d/C">架空夏子</a>')))
        with self.assertRaisesRegex(MetadataProviderError, '冲突'):
            source.match('ABC-007')

    def test_mgs_and_dvd_rows_keep_separate_release_identities(self):
        source = SeesaaSource()
        source.tables['mgs'] = source.rows(Page(ROOT+'d/Jackson', fixture('390JAC-040')))
        source.tables['dvd'] = source.rows(Page(ROOT+'d/Jackson', fixture('JAC-040', actors='未詳')))
        self.assertEqual(source.match('390JAC-040').code, '390JAC-040')
        self.assertEqual(source.match('JAC-040').code, 'JAC-040')

    def test_cli_outputs_community_candidates_without_the_chain_provider_or_ledger_changes(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/scrape_codes.py'
        spec = importlib.util.spec_from_file_location('seesaa_scrape_test', path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            db = root/'ledger.db'
            with sqlite3.connect(db) as c:
                c.executescript("CREATE TABLE asset(id INTEGER, medium TEXT, code TEXT, size INTEGER, path TEXT, name TEXT, catalog_title TEXT, original_title TEXT, studio TEXT, series TEXT, release_date TEXT); CREATE TABLE entity(id INTEGER, kind TEXT, canonical_name TEXT); CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER, role TEXT); CREATE TABLE genre_decision(source_genre TEXT PRIMARY KEY, raw_genre TEXT NOT NULL, peach_tag TEXT, decided_at TEXT NOT NULL); INSERT INTO asset VALUES(1,'video','ABC-007',1,'one.mp4','one.mp4',NULL,NULL,NULL,NULL,NULL);")
            c.close()
            before = db.read_bytes()
            pages = root/'pages.txt'
            pages.write_text(ROOT+'d/Label', encoding='utf8')
            output = root/'metadata-field-candidates-test.csv'
            # 只问 Seesaa 时正式链的 provider 一次都不该建：它会读凭据根、起 amane 桥。
            with patch('peach.sources.seesaa.HttpxTransport', return_value=Mock(return_value=HttpResponse(200, {}, fixture()))), patch('peach.library_processing.LibraryMetadataProvider', side_effect=AssertionError('not required')):
                self.assertEqual(script.main(['--db', str(db), '--out', str(output), '--raw-dir', str(root/'raw'), '--log-dir', str(root/'logs'), '--profile', 'seesaa', '--wiki-pages-file', str(pages), '--delay', '0', '--min-free', '0']), 0)
            with output.open(encoding='utf-8-sig') as handle:
                rows = list(csv.DictReader(handle))
            candidate = json.loads(next(r for r in rows if r['field']=='performers')['candidates_json'])[0]
            self.assertEqual(candidate['source_kind'], 'community')
            self.assertFalse(candidate['official'])
            self.assertTrue(candidate['wiki_evidence']['performers_complete'])
            self.assertTrue(candidate['warnings'])
            self.assertEqual(before, db.read_bytes())
            self.assertEqual(script._chain_for(('ABC-007', 1.0, 1, None, None, None), profile='seesaa', sources=None),
                             ('sougouwiki',))
            # 快照与候选记的来源身份就是 `sougouwiki`，账本里已有的来源值据此对得上。
            snapshot = json.loads((root/'raw'/'ABC-007'/'sougouwiki.json').read_text(encoding='utf-8'))
            self.assertEqual((snapshot['source'], snapshot['provider'], candidate['source'], candidate['provider']),
                             ('sougouwiki',) * 4)


#: 作品表一行的来源快照里 Seesaa 自己的那几键，逐键固定：候选、复核与既有快照读的就是它们。
ROW = {
    'id': 'ABC-007', 'source_url': ROOT+'d/Label', 'title': '架空の作品', 'release_date': '2024-11-20',
    'actresses': [{'japanese_name': '架空花子'}, {'japanese_name': '架空春子'}],
    'cover_url': 'https://example.org/cover.jpg',
    'source_warnings': ['社区 Wiki 的出演断言需要人工复核；人物页链接不自动合并艺名'],
    'wiki_evidence': {'table': 'table_edit_1', 'row': 1,
                      'performer_links': [{'name': '架空花子', 'source_url': ROOT+'d/OtherName'},
                                          {'name': '架空春子', 'source_url': ROOT+'d/B'}],
                      'performers_complete': True, 'actress_text': '架空花子 ／ 架空春子', 'notes': '別版 ABC-008'},
}
#: 契约统一带的键：Seesaa 不给厂牌、系列、导演、时长与标签，留空；`cover_urls` 是 `cover_url` 那一张。
CONTRACT_KEYS = {'maker': '', 'label': '', 'series': '', 'director': '', 'runtime': None,
                 'cover_urls': ['https://example.org/cover.jpg']}
#: 每种失败的 `(kind, 措辞, status_code, retryable, temporary)`，`scrape_codes` 的错误表与健康表记的就是这几项。
FAILURES = {
    'http403': ('auth', 'sougouwiki 需要登录或已被拒绝：来源返回 403：可能是凭据失效，也可能是出口 IP 被封，'
                '先看站点是否要求登录再决定换凭据还是等', 403, False, True),
    'http401': ('auth', 'sougouwiki 需要登录或已被拒绝：来源返回 401，换一份凭据后重跑', 401, False, True),
    'login': ('auth', 'sougouwiki 需要登录或已被拒绝：重定向终点落在 /auth/login，换一份凭据后重跑', 200, False, True),
    'http429': ('unavailable', 'Wiki HTTP 请求未取得', 429, True, False),
    'http404': ('unavailable', 'Wiki HTTP 请求未取得', 404, True, False),
    'http500': ('unavailable', 'Wiki HTTP 请求未取得', 500, True, False),
    'challenge': ('blocked', 'Wiki 机器人验证未取得', 403, False, False),
    'redirect': ('redirect', 'Wiki 重定向页面未取得身份验证', 0, False, False),
    'size': ('size_limit', 'Wiki 页面超过读取上限', 0, False, False),
    'timeout': ('unavailable', 'Wiki 网络请求未取得', 0, True, False),
    'budget': ('budget', 'Wiki 本批请求已停止或达到限额', 0, True, False),
}
FAILURE_RESPONSES = {
    'http403': HttpResponse(403, {}, b''), 'http401': HttpResponse(401, {}, b''),
    'login': HttpResponse(200, {}, b'<html>login</html>', 'https://seesaawiki.jp/auth/login'),
    'http429': HttpResponse(429, {}, b''), 'http404': HttpResponse(404, {}, b''), 'http500': HttpResponse(500, {}, b''),
    'challenge': HttpResponse(200, {}, b'Just a moment'), 'redirect': HttpResponse(200, {}, b'x', ROOT+'d/Other'),
    'size': HttpResponse(200, {}, b'x'*(4*1024*1024+1)),
}


def described(error):
    return (error.kind, str(error), error.status_code, error.retryable, error.temporary)


class SeesaaContractTests(unittest.TestCase):
    """契约下的这一站：快照逐键、失败逐档、请求形状与页缓存键都是固定的。"""

    def test_payload_keeps_every_table_key_and_only_adds_the_empty_contract_keys(self):
        payload = parse_page(fixture(), ROOT+'d/Label')[0]
        self.assertEqual({key: payload[key] for key in ROW}, ROW)
        self.assertEqual({key: payload[key] for key in set(payload) - set(ROW)}, CONTRACT_KEYS)
        self.assertNotIn('genres', payload)

    def test_incomplete_cast_and_missing_photo_rows_are_fixed_key_by_key(self):
        dvd = parse_page(fixture('JAC-040', actors='未詳'), ROOT+'d/Jackson')[0]
        self.assertEqual({key: dvd[key] for key in ('id', 'actresses', 'source_warnings')},
                         {'id': 'JAC-040', 'actresses': [],
                          'source_warnings': ['社区 Wiki 的出演断言需要人工复核；人物页链接不自动合并艺名',
                                              '出演栏含未识别或不确定内容，完整出演名单未取得']})
        self.assertEqual(dvd['wiki_evidence'], {'table': 'table_edit_1', 'row': 1, 'performer_links': [],
                                                'performers_complete': False, 'actress_text': '未詳',
                                                'notes': '別版 ABC-008'})
        no_photo = fixture().replace(b'<th>PHOTO</th>', b'').replace(
            '<td><a href="https://example.org/cover.jpg">画像</a></td>'.encode('euc_jp'), b'')
        row = parse_page(no_photo, ROOT+'d/Label')[0]
        self.assertEqual((row['cover_url'], row['cover_urls']), ('', []))
        self.assertEqual({key: row[key] for key in ROW if key != 'cover_url'},
                         {key: value for key, value in ROW.items() if key != 'cover_url'})

    def test_every_failure_keeps_its_kind_wording_status_and_retry_flags(self):
        for name, expected in FAILURES.items():
            with self.subTest(failure=name), tempfile.TemporaryDirectory() as tmp:
                if name == 'timeout':
                    transport = Mock(side_effect=httpx.ReadTimeout('timeout'))
                else:
                    transport = Mock(return_value=FAILURE_RESPONSES.get(name))
                pages = WikiPages(Path(tmp), transport=transport, limiter=Mock(),
                                  max_requests=0 if name == 'budget' else 80)
                with self.assertRaises(MetadataProviderError) as caught:
                    SeesaaSource().load(ROOT+'d/Label', session=Session(pages))
                self.assertEqual(described(caught.exception), expected)
        source = SeesaaSource()
        source.tables['one'] = source.rows(Page(ROOT+'d/One', fixture()))
        source.tables['two'] = source.rows(Page(ROOT+'d/Two', fixture(actors='<a href="/w/sougouwiki/d/C">架空夏子</a>')))
        with self.assertRaises(MetadataProviderError) as caught:
            source.match('ABC-007')
        self.assertEqual(described(caught.exception), ('ambiguous', 'Wiki 精确番号存在冲突行，需复核原页', 0, False, False))

    def test_search_requests_cache_keys_and_the_unfound_code_are_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            search = b'<div class="result-box"><div class="body"><h3><a href="/w/sougouwiki/d/Person">Person</a></h3></div></div>'
            person = '<div id="page-body"><div class="user-area"><a href="/w/sougouwiki/d/Label">(レーベル一覧)</a></div></div>'.encode('euc_jp')
            transport = Mock(side_effect=[HttpResponse(200, {}, body) for body in (search, person, fixture(), search)])
            source, session = SeesaaSource(), Session(WikiPages(root, transport=transport, limiter=Mock()))
            self.assertEqual(source.query('ABC-007', session=session).payload()['id'], 'ABC-007')
            self.assertEqual([(call.args[0].url, call.args[0].headers, call.args[1:]) for call in transport.call_args_list],
                             [(ROOT+'search?keywords=ABC-007', {}, (20, 4194304)),
                              (ROOT+'d/Person', {}, (20, 4194304)),
                              (ROOT+'d/Label', {}, (20, 4194304))])
            # 缓存键是地址的 SHA-256：既有的 `seesaa-pages` 目录照样命中，续跑不重取。
            self.assertEqual(sorted(path.name for path in root.glob('*.json')),
                             ['2bf377d9cc422ec28b32727eafbcc82ab7d98d885e20132849bf32cc24fe3588.json',
                              'd90190c81f8e207b0a0a966078831d7466573a88a283696f606ada2fc50e16bd.json',
                              'eb4cfc7f914dd31b2f956c46c29ec1b2b94fc36addacb884685bb17e9d729f9f.json'])
            with self.assertRaises(MetadataProviderError) as caught:
                source.query('ABC-009', session=session)
            self.assertEqual(described(caught.exception),
                             ('incomplete_search', 'Wiki 搜索范围内未取得精确作品表格行', 0, True, False))

    def test_parse_reads_one_page_without_any_transport(self):
        record = SeesaaSource().parse(Page(ROOT+'d/Label', fixture()), 'abc-007')
        self.assertEqual((record.source, record.provenance, record.code, record.source_url),
                         ('sougouwiki', 'sougouwiki', 'ABC-007', ROOT+'d/Label'))
        with self.assertRaises(MetadataProviderError) as caught:
            SeesaaSource().parse(Page(ROOT+'d/Label', fixture()), 'ABC-008')
        self.assertEqual(caught.exception.kind, 'incomplete_search')

    def test_registered_under_the_existing_source_identity(self):
        self.assertIs(SITE_SOURCES['sougouwiki'], SeesaaSource)
        self.assertEqual((SEESAA.name, SEESAA.provider), ('sougouwiki', 'sougouwiki'))


if __name__ == '__main__':
    unittest.main()
