"""Seesaa 上的两个认人 Wiki（av_neme、av_name）：作品条目的精确番号出演候选。

夹具是 2026-09-24 实测页面裁出的骨架（小节、列表、键值表、搜索结果的标签与类名照原样），
人名与作品名换成虚构的。
"""
import argparse
import csv
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from peach.http import HttpResponse
from peach.metadata import MetadataProviderError
from peach.sources import Page, Session
from peach.sources.seesaa import AvNameSource, AvNemeSource, WikiPages

NAME = 'https://seesaawiki.jp/av_name/'
NEME = 'https://seesaawiki.jp/av_neme/'
EDIT = '<a title="部分編集" href="{root}e/edit?id=1&amp;part=content_1" rel="nofollow" class="part-edit"><img src="x.gif" /></a>'


def page(inner):
    return f'<div id="page-body"><div class="user-area">{inner}</div></div>'.encode('euc_jp')


def section(heading, body, root=NAME):
    return ('<div class="wiki-section-3"><div class="title-3"><h5 id="content_1_1_1">'
            f'{heading} {EDIT.format(root=root)}</h5></div><div class="wiki-section-body-3">{body}</div></div>')


def code_page(code='ABC-001', cast=None):
    """av_name 的单品番页：一张「品番／配信品番／素人名義／出演女優／…」键值表。"""
    cast = cast if cast is not None else (
        f'<span class="fsize"><b><a href="{NAME}d/A">架空花子（架空はなこ）</a></b></span><br/>'
        f'<span class="fsize"><b><a href="{NAME}d/B">架空春子</a></b></span>（かくう はるこ）')
    return page(f'{code}に出てるAV女優の名前は、<a href="{NAME}d/A">架空花子</a>。'
                '<div class="wiki-section-1"><div class="title-1"><h3 id="content_1">基本情報</h3></div>'
                '<div class="wiki-section-body-1"><table id="content_block_2"><tbody>'
                f'<tr><th>品番</th><td>{code}</td></tr><tr><th>配信品番</th><td>abc001</td></tr>'
                '<tr><th>素人名義</th><td>はなちゃん</td></tr>'
                f'<tr><th>出演女優</th><td style="background-color:#d4edda;">{cast}</td></tr>'
                '<tr><th>配信開始日</th><td>2026-09-24</td></tr>'
                f'<tr><th>レーベル</th><td><a href="{NAME}d/L">架空レーベル</a></td></tr>'
                '</tbody></table></div></div>')


def name_entry(code, cast):
    """av_name 厂牌页上的一部作品：`h5` 品番，列表里「出演女優／タイトル／配信日」。"""
    return section(code, '<a href="https://image.example/pf.jpg"><img src="https://image.example/pf.jpg"/></a>'
                         f'<ul class="list-1"><li> 出演女優: {cast}</li>'
                         f'<li> タイトル: <a class="outlink" href="https://www.mgstage.com/product/product_detail/{code}/">'
                         '架空の作品…</a></li><li> 配信日: 2021-03-01</li></ul>')


def neme_entry(code, cast, tail=''):
    """av_neme 系列页、月份页上的一部作品：`h5` 是「品番| 系列名」，正文一行「名前(女優名)：」。"""
    return section(f'{code}| 架空シリーズ',
                   '<a href="https://image.example/pf.jpg"><img src="https://image.example/pf.jpg"/></a><br />'
                   f'<span class="fsize" style="font-size:18px;"><b>名前(女優名)</b>：{cast}</span>{tail}<br />'
                   '<a class="outlink" href="https://www.mgstage.com/product/product_detail/x/">架空の作品</a>'
                   f'(<a href="{NEME}d/Series">レーベル一覧</a>)<br />別名：はなちゃん 25歳 会社員<br />', NEME)


def search(root, *names):
    """站内搜索结果页：一条结果一个 `.body`，`h3` 链到页面。"""
    bodies = ''.join(f'<div class="body"><h3 class="keyword"><a href="{root}d/{name}">{name}</a></h3>'
                     f'<p class="url"><a href="{root}d/{name}">{root}d/{name}</a></p></div>' for name in names)
    return f'<div class="result-box"><p class="title">全文検索</p>{bodies}</div>'.encode('euc_jp')


def payloads(site, body, url):
    return [row.payload() for row in site().rows(Page(url, body))]


def serve(routes):
    """按地址回页的假传输；没登记的地址当测试失败。"""
    def transport(request, timeout, limit):
        if request.url not in routes:
            raise AssertionError('unexpected request ' + request.url)
        return HttpResponse(200, {}, routes[request.url])
    return Mock(side_effect=transport)


class AvNameTests(unittest.TestCase):
    def test_code_page_reads_linked_cast_without_readings_and_keeps_details_as_evidence(self):
        row = payloads(AvNameSource, code_page(), NAME+'d/ABC%2d001')[0]
        self.assertEqual((row['id'], row['source_url']), ('ABC-001', NAME+'d/ABC%2d001'))
        self.assertEqual([a['japanese_name'] for a in row['actresses']], ['架空花子', '架空春子'])
        evidence = row['wiki_evidence']
        self.assertTrue(evidence['performers_complete'])
        self.assertEqual(evidence['performer_links'][0], {'name': '架空花子（架空はなこ）', 'source_url': NAME+'d/A'})
        self.assertEqual(evidence['details']['素人名義'], 'はなちゃん')
        # 只产出演候选：标题与日期只在证据里。
        self.assertEqual((row['title'], row['release_date'], row['cover_urls']), ('', '', []))

    def test_label_page_rows_split_complete_unidentified_and_pageless_names(self):
        body = page(name_entry('300ABC-010', f'<span class="fsize"><a href="{NAME}d/A">架空花子</a></span>')
                    + name_entry('300ABC-011', '--')
                    + name_entry('300ABC-012', f'<span class="fsize"><a href="{NAME}d/A">架空花子</a></span>・'
                                 '<span class="fsize"><span style="color:gray">架空夏子</span><small>'
                                 f'<a rel="nofollow" href="{NAME}e/add?pagename=%b2">?</a></small></span>'))
        rows = {row['id']: row for row in payloads(AvNameSource, body, NAME+'d/Label')}
        self.assertEqual(set(rows), {'300ABC-010', '300ABC-011', '300ABC-012'})
        self.assertEqual(rows['300ABC-010']['actresses'], [{'japanese_name': '架空花子'}])
        for code, text in (('300ABC-011', '--'), ('300ABC-012', '架空花子 ・ 架空夏子 ?')):
            with self.subTest(code=code):
                self.assertEqual(rows[code]['actresses'], [])
                self.assertFalse(rows[code]['wiki_evidence']['performers_complete'])
                self.assertEqual(rows[code]['wiki_evidence']['actress_text'], text)
                self.assertEqual(len(rows[code]['source_warnings']), 2)

    def test_person_page_history_is_not_a_record(self):
        body = page(section('300ABC-010', '<ul class="list-1"><li> タイトル: 架空の作品</li>'
                                          f'<li> メーカー: <a href="{NAME}d/M">架空メーカー</a></li></ul>'))
        self.assertEqual(payloads(AvNameSource, body, NAME+'d/Person'), [])

    def test_search_reads_the_code_page_first_and_rejects_substring_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            routes = {NAME+'search?keywords=ABC-001': search(NAME, 'Person', 'ABC%2d001'),
                      NAME+'d/ABC%2d001': code_page(),
                      NAME+'search?keywords=BC-001': search(NAME, 'ABC%2d001')}
            transport = serve(routes)
            source, session = AvNameSource(), Session(WikiPages(Path(tmp), config=AvNameSource.DEFAULT,
                                                                transport=transport, limiter=Mock()))
            record = source.query('ABC-001', session=session)
            self.assertEqual((record.source, record.provenance, record.code), ('av_name', 'av_name', 'ABC-001'))
            # 人物页排在单品番页之后，找到就停，一次没问。
            self.assertEqual([call.args[0].url for call in transport.call_args_list],
                             [NAME+'search?keywords=ABC-001', NAME+'d/ABC%2d001'])
            # 全文检索把 `ABC-001` 当成 `BC-001` 的命中交回来：读过的页里没有精确番号就不算。
            with self.assertRaises(MetadataProviderError) as caught:
                source.query('BC-001', session=session)
            self.assertEqual((caught.exception.kind, caught.exception.retryable), ('incomplete_search', True))


class AvNemeTests(unittest.TestCase):
    def test_series_entry_reads_the_code_before_the_bar(self):
        body = page(neme_entry('300ABC-010', f'<a href="{NEME}d/A">架空花子</a>'))
        row = payloads(AvNemeSource, body, NEME+'d/Series')[0]
        self.assertEqual((row['id'], row['actresses']), ('300ABC-010', [{'japanese_name': '架空花子'}]))
        self.assertIn('別名：はなちゃん', row['wiki_evidence']['entry_text'])

    def test_uncertain_marks_and_broken_links_keep_only_the_raw_text(self):
        """站方说明：名字旁带「？」或「▲」是不确定；标记写在名字那一段外面也算。"""
        cases = {'300ABC-011': (f'<a href="{NEME}d/A">架空花子</a>', '？'),
                 '300ABC-012': (f'<a href="{NEME}d/A">架空花子</a>▲', ''),
                 '300ABC-013': ('<span style="color:gray">&gt;</span><small>'
                                f'<a rel="nofollow" href="{NEME}e/add?pagename=%3e">?</a></small>', '')}
        body = page(''.join(neme_entry(code, cast, tail) for code, (cast, tail) in cases.items()))
        rows = payloads(AvNemeSource, body, NEME+'d/2021%c7%af4%b7%ee')
        self.assertEqual([row['id'] for row in rows], list(cases))
        for row in rows:
            with self.subTest(code=row['id']):
                self.assertEqual(row['actresses'], [])
                self.assertFalse(row['wiki_evidence']['performers_complete'])

    def test_search_does_not_follow_person_page_label_links(self):
        """全文检索的结果本身就有载着这个番号的系列页与月份页，人物页上的「レーベル一覧」不再跟读。"""
        person = page(section('架空シリーズ', f'(<a href="{NEME}d/Series">レーベル一覧</a>)', NEME))
        month = page(neme_entry('300ABC-010', f'<a href="{NEME}d/A">架空花子</a>')
                     + neme_entry('300ABC-020', f'<a href="{NEME}d/B">架空春子</a>'))
        with tempfile.TemporaryDirectory() as tmp:
            transport = serve({NEME+'search?keywords=300ABC-010': search(NEME, 'Person', 'Month'),
                               NEME+'d/Person': person, NEME+'d/Month': month})
            source, session = AvNemeSource(), Session(WikiPages(Path(tmp), config=AvNemeSource.DEFAULT,
                                                                transport=transport, limiter=Mock()))
            self.assertEqual(source.query('300ABC-010', session=session).performers, ({'japanese_name': '架空花子'},))
            # 同一张月份页上的别的作品直接从读过的表里取，不再联网。
            self.assertEqual(source.query('300ABC-020', session=session).performers, ({'japanese_name': '架空春子'},))
            self.assertEqual(transport.call_count, 3)


class ScrapeCodesTests(unittest.TestCase):
    def script(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/scrape_codes.py'
        spec = importlib.util.spec_from_file_location('seesaa_name_wikis_scrape_test', path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        return script

    def test_named_wikis_share_one_host_interval_and_their_own_prefetched_pages(self):
        script = self.script()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = root/'pages.txt'
            pages.write_text(NEME+'d/Month\n' + NAME+'d/Label\n', encoding='utf8')
            args = argparse.Namespace(wiki_pages_file=pages, raw_dir=root, refresh=False, wiki_max_requests=5,
                                      secrets_root=root, tools_root=root)
            adapter = script._build_adapter(args, ('av_neme', 'av_name'))
            (neme, neme_session), (name, name_session) = adapter.wikis['av_neme'], adapter.wikis['av_name']
            self.assertIs(neme_session.transport.limiter, name_session.transport.limiter)
            self.assertEqual((neme.pages, name.pages), ((NEME+'d/Month',), (NAME+'d/Label',)))
            self.assertEqual((neme_session.transport.config.name, name_session.transport.max_requests), ('av_neme', 5))
            with self.assertRaisesRegex(ValueError, '预取页不属于'):
                script._build_adapter(args, ('av_neme',))

    def test_search_misses_do_not_cool_the_wiki_down_but_transport_failures_do(self):
        """样本批里 FC2 排在前面、两站都没收录：连着几个「搜索范围内没有」不该把整站冷却掉。"""
        script = self.script()
        throttle = script._Throttle(script._health_rows(('av_name',), 'custom'))
        miss = MetadataProviderError('miss', kind='incomplete_search', retryable=True)
        for _ in range(script.COOLDOWN_AFTER_FAILURES + 2):
            throttle.record_failure('av_name', miss)
        self.assertEqual(throttle.open_members(('av_name',)), ('av_name',))
        down = MetadataProviderError('down', kind='unavailable', retryable=True)
        for _ in range(script.COOLDOWN_AFTER_FAILURES):
            throttle.record_failure('av_name', down)
        self.assertEqual(throttle.open_members(('av_name',)), ())

    def test_cli_writes_review_candidates_per_wiki_without_touching_the_ledger(self):
        script = self.script()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            db = root/'ledger.db'
            with sqlite3.connect(db) as c:
                c.executescript("CREATE TABLE asset(id INTEGER, medium TEXT, code TEXT, size INTEGER, path TEXT, name TEXT, catalog_title TEXT, original_title TEXT, studio TEXT, series TEXT, release_date TEXT); CREATE TABLE entity(id INTEGER, kind TEXT, canonical_name TEXT); CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER, role TEXT); CREATE TABLE genre_decision(source_genre TEXT PRIMARY KEY, raw_genre TEXT NOT NULL, peach_tag TEXT, decided_at TEXT NOT NULL); INSERT INTO asset VALUES(1,'video','ABC-001',1,'one.mp4','one.mp4',NULL,NULL,NULL,NULL,NULL);")
            c.close()
            before = db.read_bytes()
            output = root/'metadata-field-candidates-test.csv'
            transport = serve({NAME+'search?keywords=ABC-001': search(NAME, 'ABC%2d001'),
                               NAME+'d/ABC%2d001': code_page(),
                               NEME+'search?keywords=ABC-001': search(NEME)})
            with patch('peach.sources.seesaa.HttpxTransport', return_value=transport), \
                    patch('peach.library_processing.LibraryMetadataProvider', side_effect=AssertionError('not required')):
                self.assertEqual(script.main(['--db', str(db), '--out', str(output), '--raw-dir', str(root/'raw'),
                                              '--log-dir', str(root/'logs'), '--sources', 'av_neme,av_name',
                                              '--delay', '0', '--min-free', '0']), 0)
            with output.open(encoding='utf-8-sig') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row['field'] for row in rows], ['performers'])
            candidate, = json.loads(rows[0]['candidates_json'])
            self.assertEqual((candidate['source'], candidate['provider'], candidate['source_kind'], candidate['official']),
                             ('av_name', 'av_name', 'community', False))
            self.assertEqual([item['name'] for item in candidate['value']], ['架空花子', '架空春子'])
            with (root/'metadata-source-errors-test.csv').open(encoding='utf-8-sig') as handle:
                errors = list(csv.DictReader(handle))
            self.assertEqual([(row['source'], row['kind']) for row in errors], [('av_neme', 'incomplete_search')])
            self.assertEqual(before, db.read_bytes())


if __name__ == '__main__':
    unittest.main()
