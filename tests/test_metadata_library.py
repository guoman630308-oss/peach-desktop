"""既有库边车识别和统一处理的隔离回归。"""
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, Mock, patch
from contextlib import closing

from filelock import FileLock
from PIL import Image

from peach.field_owners import auto_owner, owner_of, review_owner
from peach.genre_taxonomy import map_genres
from peach.jav_cover_fetch import NotFound
from peach.http import HttpResponse
from peach.library_nfo import read_nfo, sidecars, local_art
from peach.library_processing import (STALL_AFTER_SECONDS, _candidate_identity, decorate,
                                      issues_path, _merge_local_performer_profiles,
                                      process_library, snapshot, state_path, _fields)
from peach.metadata_auto_apply import _apply_metadata_candidate
from peach.repository import LedgerDatabase
from peach.review_csv import read_rows
from peach.settings_file import PeachConfig
from support.conditions import windows_ledger_roots
from support.ledger import fresh_ledger


def stub_provider():
    """只关心 r18.dev 那一档的用例用它：DMM 兜底与综合索引那两档按「没有」桩掉。

    来源链在 r18.dev 没给全必填标量时会接着问 DMM，再问综合索引（`peach.metadata_routes`）。
    桩不给答复，那几问拿到的是 Mock 的自动属性，报出来的错和被测行为无关。
    `provider.query.return_value` 与 `side_effect` 仍由各用例设，只对 r18.dev 那一问生效。
    """
    provider = Mock()

    def dmm_has_nothing(code, source='r18dev', **_):
        if source in ('dmm', 'javdb'):
            raise NotFound('HTTP 404')
        return DEFAULT

    provider.query.side_effect = dmm_has_nothing
    provider.community.side_effect = NotFound('社区来源都没有这个番号')
    provider.amane.side_effect = NotFound('amane 桥问的几站都没有这个番号')
    return provider


def _mirror_page(image='https://storage92000.contents.fc2.com/file/1.jpg', video=3189161):
    """fc2cmadb 的作品页：Inertia 把整棵 props 树连同握手版本号放在一个 script 里。

    和真的 `_fetch` 一样交字节。
    """
    page = {'component': 'Articles/Show', 'version': 'fcb3b524d4c7f8f3d2c38e437b35b7a9',
            'url': f'/articles/{video}',
            'props': {'article': {'video_id': video, 'title': '【無】コスプレシリーズ',
                                  'release_date': '2023-02-19', 'duration': '46:06',
                                  'image_url': image,
                                  'writer': {'slug': 'rina_vlog', 'name': '梨奈'},
                                  'tags': [{'name': 'ハメ撮り'}]}}}
    return (f'<script data-page="app" type="application/json">'
            f'{json.dumps(page, ensure_ascii=False)}</script><div id="app"></div>').encode()


#: JavArchive 的作品地址把商品号夹在标题里，站上真的用空格分隔（用户 2026-09-22 给的
#: `FC2PPV%203232110%20…`）；搜索结果那一步要 unquote 之后才认得出这个号。
_ARCHIVE_LINK = '/859881-FC2PPV%203232110%20%E3%81%BF%E3%81%8A-pn.html'
_ARCHIVE_COVER = 'https://img.javstore.net/images/2023/12/26/3232110pl.jpg'
#: 同一页上的第二个图位（schema.org 的 `image`）。转存者存的图会失效，排头那张 404 时
#: 只有这张还能用——2026-09-22 实测 `FC2-PPV-1021177` 那页排头的图就已经是 404。
_ARCHIVE_COVER_SECOND = 'https://img.javstore.net/images/2023/12/26/FC2PPV-3232110-2.jpg'


def _archive_pages(url):
    """JavArchive 那一档的两跳：先搜出站内地址，作品页上才有封面和正文那块资料。和真的 `_fetch` 一样交字节。"""
    if 'search' in url:
        return f'<div class="post"><a href="{_ARCHIVE_LINK}">FC2PPV 3232110</a></div>'.encode()
    return ('<h1><a href="' + _ARCHIVE_LINK + '">FC2PPV 3232110 みおちゃんが素人さん</a></h1>'
            f'<div class="fisrst_sc"><img src="{_ARCHIVE_COVER}" alt="x" /></div>'
            f'<img itemprop="image" src="{_ARCHIVE_COVER_SECOND}" alt="x" />'
            '<div class="news">标签：素人 <br />日期：2023/03/23 <br />时长：45:12 <br /></div>').encode()


class LibraryNfoTests(unittest.TestCase):
    def test_task_sidecar_index_does_not_walk_the_folder_for_each_video(self):
        from peach.library_nfo import SidecarIndex
        class CountedFiles(dict):
            walks = 0
            def values(self):
                self.walks += 1
                return super().values()
        files = CountedFiles()
        for index in range(100):
            for suffix in ('.mp4', '.jpg'):
                name = f'合集-{index}{suffix}'
                files[name.casefold()] = self.root / name
        listing = SidecarIndex(files)
        for index in range(100):
            nfo, posters = listing.for_video(self.root / f'合集-{index}.mp4')
            self.assertIsNone(nfo)
            self.assertEqual(posters, [self.root / f'合集-{index}.jpg'])
        self.assertEqual(files.walks, 1)

    @windows_ledger_roots
    def test_retry_assets_with_one_code_share_a_fresh_source_answer(self):
        media = self.root / 'media'
        media.mkdir()
        for name in ('ABW-101-CD1.mp4', 'ABW-101-CD2.mp4'):
            (media / name).write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        process_library(config, db, self.root / 'generated', self.root / 'covers', stage='scan')
        provider = stub_provider()
        provider.query.return_value = {'id': 'ABW-101', 'title': '同一作品', 'maker': '厂牌',
            'release_date': '2026-01-01', 'actresses': [{'japanese_name': '测试演员'}]}
        provider.cover.side_effect = NotFound('没有封面')
        result = process_library(config, db, self.root / 'generated', self.root / 'covers', stage='metadata',
            retry_ids=[1, 2], provider_factory=lambda: provider)
        r18 = [call for call in provider.query.call_args_list if call.args[1] == 'r18dev']
        self.assertEqual(len(r18), 1)
        self.assertEqual(provider.cover.call_count, 1)
        candidates = read_rows(self.root / 'generated/library-metadata-field-candidates.csv')
        titles = [row for row in candidates if row['field'] == 'title']
        self.assertEqual({row['asset_id'] for row in titles}, {'1', '2'})
        self.assertEqual(result['checked'], 2)

    @windows_ledger_roots
    def test_a_later_source_requires_review_to_replace_a_field_filled_during_the_run(self):
        media = self.root / 'media'
        media.mkdir()
        (media / 'SSIS-057.mp4').write_bytes(b'video')
        (media / 'SSIS-057.nfo').write_text(
            '<movie><id>SSIS-057</id><title>本地标题</title></movie>', encoding='utf-8')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        database = LedgerDatabase(db)
        provider = stub_provider()
        provider.cover.return_value = False

        def tags():
            with database.read_connection() as connection:
                return {row[0] for row in connection.execute(
                    "SELECT tag FROM asset_tag WHERE asset_id=1 AND tag NOT LIKE '演员:%'")}

        def query(code, source='r18dev', **kwargs):
            if source == 'javdb':
                self.assertIn('巨乳', tags())
                return {'id': code, 'genres': ['高跟鞋', '絲襪']}
            if source == 'dmm':
                raise NotFound('没有')
            return {'id': code, 'title': '本地标题', 'maker': 'Prestige', 'release_date': '2023-05-26',
                    'actresses': [{'japanese_name': '涼森れむ'}], 'genres': ['巨乳']}

        provider.query.side_effect = query
        with patch('peach.library_processing._enrich_finished_performers',
                   return_value={'aliases': 0, 'avatars': 0, 'conflicts': 0, 'failed': 0}):
            process_library(config, db, self.root / 'generated', self.root / 'covers',
                            provider_factory=lambda: provider, database=database,
                            route_overrides='censored=r18dev,javdb')
        self.assertEqual(tags(), {'巨乳'})
        group = next(row for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')
                     if row['field'] == 'tags')
        choice = next(item for item in json.loads(group['candidates_json']) if item['source'] == 'javdb')
        self.assertTrue(choice['requires_review'])
        from peach.web_contract import WebContract
        from peach.web_review import q_review, w_review_decision
        contract = WebContract(db, candidate_root=self.root / 'generated', database=database)
        pending = q_review(contract)['sections']['metadata_fields']
        decision = next(item for item in pending if item['field'] == 'tags')
        w_review_decision(contract, {'category': 'metadata_fields', 'item_key': decision['item_key'],
            'status': 'approved', 'candidate_key': choice['candidate_key'],
            'expected_revision': decision['asset_mutation_revision']})
        self.assertEqual(tags(), {'高跟', '丝袜'})

    @windows_ledger_roots
    def test_a_confirmed_site_is_visible_before_another_site_reaches_its_deadline(self):
        from peach.library_processing import LibraryMetadataProvider
        from peach.jav_cover_fetch import DeadlineExceeded
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-358.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("INSERT INTO asset(path,name,location,medium,code) VALUES(?,?,'local','video','ABW-358')",
                               (str(media / 'ABW-358.mp4'), 'ABW-358.mp4'))
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.cover = Mock(return_value=False)
        provider.transport = Mock()
        seen = []

        def site(source, code, **kwargs):
            if source == 'avbase':
                return {'id': code, 'title': '已确认来源标题'}
            if source == 'javbus':
                with closing(sqlite3.connect(db)) as connection:
                    seen.extend(connection.execute('SELECT catalog_title FROM asset').fetchone())
                self.assertTrue((config.directory('sources') / 'library-metadata/ABW-358-avbase.json').is_file())
                raise DeadlineExceeded('等待另一站超时')
            raise NotFound('没有')

        provider.site = site
        with patch('peach.library_processing._enrich_finished_performers',
                   return_value={'aliases': 0, 'avatars': 0, 'conflicts': 0, 'failed': 0}):
            process_library(config, db, self.root / 'generated', self.root / 'covers', stage='metadata',
                            provider_factory=lambda: provider, database=LedgerDatabase(db),
                            route_overrides='censored=avbase,javbus,javdb')
        self.assertEqual(seen, ['已确认来源标题'])
        candidates = read_rows(self.root / 'generated/library-metadata-field-candidates.csv')
        self.assertEqual(json.loads(candidates[0]['candidates_json'])[0]['source'], 'avbase')

    @windows_ledger_roots
    def test_metadata_checkpoint_is_saved_when_evidence_was_flushed_before_cover_finished(self):
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-358.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("INSERT INTO asset(path,name,location,medium,code) VALUES(?,?,'local','video','ABW-358')",
                               (str(media / 'ABW-358.mp4'), 'ABW-358.mp4'))
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'id': 'ABW-358', 'title': '已保存资料'}
        provider.cover.return_value = False
        with patch('peach.library_processing.CANDIDATE_FLUSH_SECONDS', 0):
            result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                     stage='metadata', provider_factory=lambda: provider)
        self.assertEqual(len(result['completed_metadata']), 1)
        provider.query.reset_mock()
        process_library(config, db, self.root / 'generated', self.root / 'covers', stage='metadata',
                        provider_factory=lambda: provider, resume_state=result)
        provider.query.assert_not_called()

    @windows_ledger_roots
    def test_user_pause_keeps_published_fields_and_resumes_the_same_collection(self):
        import threading
        from peach.processing_control import control_for
        media = self.root / 'media'
        media.mkdir()
        video = media / 'ABW-358.mp4'
        video.write_bytes(b'video')
        db = fresh_ledger(self.root)
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("INSERT INTO asset(path,name,location,medium,code) VALUES(?,?,'local','video','ABW-358')",
                               (str(video), video.name))
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'id': 'ABW-358', 'title': '已确认资料'}
        provider.cover.return_value = False
        control = control_for(config)
        paused, requested = threading.Event(), threading.Event()
        results, errors = [], []
        def report(state):
            if state['status'] == 'paused':
                paused.set()
            elif state.get('auto_applied', 0) and not requested.is_set():
                requested.set()
                control.request('pause', state['job_id'])
        def run():
            try:
                results.append(process_library(config, db, self.root / 'generated', self.root / 'covers',
                    stage='metadata', speed_mode='high', job_id='one', report=report,
                    provider_factory=lambda: provider, database=LedgerDatabase(db)))
            except BaseException as exc:
                errors.append(exc)
        with patch('peach.library_processing.CANDIDATE_FLUSH_SECONDS', 0), \
                patch('peach.library_processing._enrich_finished_performers',
                      return_value={'aliases': 0, 'avatars': 0, 'conflicts': 0, 'failed': 0}):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(paused.wait(5), errors)
                with closing(sqlite3.connect(db)) as connection:
                    self.assertEqual(connection.execute('SELECT catalog_title FROM asset').fetchone()[0], '已确认资料')
                self.assertTrue(read_rows(self.root / 'generated/library-metadata-field-candidates.csv'))
                self.assertEqual(snapshot(config)['status'], 'paused')
            finally:
                control.request('resume', 'one')
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual((results[0]['job_id'], results[0]['status']), ('one', 'complete'))
        self.assertGreater(results[0]['auto_applied'], 0)

    @windows_ledger_roots
    def test_all_local_titles_are_committed_before_the_first_online_request(self):
        media = self.root / 'media'
        media.mkdir()
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        for code in ('ABW-358', 'ABW-359'):
            (media / f'{code}.mp4').write_bytes(b'video')
            (media / f'{code}.nfo').write_text(
                f'<movie><id>{code}</id><title>本地 {code}</title></movie>', encoding='utf-8')
        seen = []

        def query(*args, **kwargs):
            with closing(sqlite3.connect(db)) as connection:
                seen.append(connection.execute('SELECT catalog_title FROM asset ORDER BY id').fetchall())
            raise KeyboardInterrupt()

        provider = stub_provider()
        provider.query.side_effect = query
        def previews(*args, **kwargs):
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute('SELECT catalog_title FROM asset ORDER BY id').fetchall(),
                                 [('本地 ABW-358',), ('本地 ABW-359',)])

        with self.assertRaises(KeyboardInterrupt), patch('peach.library_processing._generate_previews', side_effect=previews):
            process_library(config, db, self.root / 'generated', self.root / 'covers',
                            provider_factory=lambda: provider, database=LedgerDatabase(db))
        self.assertEqual(seen, [[('本地 ABW-358',), ('本地 ABW-359',)]])
        self.assertTrue(read_rows(self.root / 'generated/library-metadata-field-candidates.csv'))

    @windows_ledger_roots
    def test_confirmed_online_fields_are_committed_while_cover_fetch_is_pending(self):
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-358.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'id': 'ABW-358', 'title': '已确认标题', 'maker': 'Prestige',
            'release_date': '2023-05-26', 'actresses': [{'japanese_name': '涼森れむ'}]}
        visible = []

        def cover(*args, **kwargs):
            with closing(sqlite3.connect(db)) as connection:
                visible.extend(connection.execute('SELECT catalog_title FROM asset').fetchone())
            raise KeyboardInterrupt()

        provider.cover.side_effect = cover
        with self.assertRaises(KeyboardInterrupt):
            process_library(config, db, self.root / 'generated', self.root / 'covers',
                            provider_factory=lambda: provider, database=LedgerDatabase(db))
        self.assertEqual(visible, ['已确认标题'])

    @windows_ledger_roots
    def test_targeted_collection_keeps_global_progress_and_unrelated_candidates(self):
        media = self.root / 'media'
        media.mkdir()
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        db = fresh_ledger(self.root)
        database = LedgerDatabase(db)
        with database.write_transaction() as connection:
            for aid, code in ((1, 'ABW-358'), (2, 'ABW-359')):
                video = media / f'{code}.mp4'
                video.write_bytes(b'video')
                video.with_suffix('.nfo').write_text(
                    f'<movie><title>作品 {aid}</title><sorttitle>{code}</sorttitle></movie>', encoding='utf-8')
                connection.execute('INSERT INTO asset(id,location,path,name,medium,code) VALUES(?,?,?,?,?,?)',
                                   (aid, 'local', str(video), video.name, 'video', code))
        provider = stub_provider()
        provider.query.return_value = {}
        generated = self.root / 'generated'
        covers = self.root / 'covers'
        with patch('peach.library_processing._enrich_finished_performers',
                   return_value={'aliases': 0, 'avatars': 0, 'conflicts': 0, 'failed': 0}):
            process_library(config, db, generated, covers, provider_factory=lambda: provider)
            prior = state_path(config).read_bytes()
            output = self.root / 'state/download-processing/1.json'
            result = process_library(config, db, generated, covers, retry_ids=[1], stage='collect',
                                     state_file=output, provider_factory=lambda: provider, database=database)
        self.assertEqual(state_path(config).read_bytes(), prior)
        self.assertTrue(output.exists())
        self.assertEqual(result['total'], 1)
        candidates = read_rows(generated / 'library-metadata-field-candidates.csv')
        self.assertEqual(result['candidates'], sum(row['asset_id'] == '1' for row in candidates))
        with database.read_connection() as connection:
            self.assertIsNotNone(connection.execute('SELECT catalog_title FROM asset WHERE id=1').fetchone()[0])
            self.assertIsNone(connection.execute('SELECT catalog_title FROM asset WHERE id=2').fetchone()[0])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_javboss_fields_and_original_tags(self):
        path = self.root / 'sample.nfo'
        path.write_text('<movie><generator>JavBoss</generator><uniqueid type="javboss">ABW-358</uniqueid>'
            '<title>作品</title><originaltitle>原題</originaltitle><set><name>系列</name></set>'
            '<premiered>2023-05-26</premiered><runtime>210</runtime><actor><name>涼森れむ</name></actor>'
            '<genre>自定义标签</genre><tag>有码</tag><tag>主觀視角</tag><tag>苗條</tag>'
            '<tag>單體作品</tag><tag>MGSだけのおまけ映像付き</tag>'
            '<tag>フルハイビジョン(FHD)</tag></movie>', encoding='utf-8')
        payload, raw = read_nfo(path)
        self.assertEqual(payload['id'], 'ABW-358')
        self.assertEqual(payload['source_generator'], 'JavBoss')
        tags = _fields(payload)['tags']['value']
        self.assertEqual(tags, ['自定义标签', '有码', '主观视角', '苗条'])
        self.assertEqual(payload['runtime'], '210')
        self.assertEqual(raw, path.read_bytes())

    def test_movie_sidecar_only_when_unambiguous_and_named_file_preferred(self):
        video = self.root / 'one.mp4'
        video.touch()
        common = self.root / 'movie.nfo'
        common.touch()
        self.assertEqual(sidecars(video)[0], common)
        (self.root / 'two.mkv').touch()
        self.assertIsNone(sidecars(video)[0])
        named = self.root / 'one.NFO'
        named.touch()
        self.assertEqual(sidecars(video)[0], named)

    def test_numbered_image_set_beside_the_video_is_not_its_poster(self):
        gallery = self.root / 'gallery'
        gallery.mkdir()
        video = gallery / 'TG@BOT- (1).mp4'
        for name in ('TG@BOT- (1).mp4', 'TG@BOT- (1).jpg', 'TG@BOT- (2).jpg', 'TG@BOT- (10).jpg'):
            (gallery / name).touch()
        self.assertEqual(sidecars(video)[1], [])
        (gallery / 'cover.jpg').touch()
        self.assertEqual(sidecars(video)[1], [gallery / 'cover.jpg'])

    def test_release_sidecar_is_shared_by_parts_and_versions(self):
        video = self.root / 'ABC-123-CD1.mp4'
        for name in (video.name, 'ABC-123-CD2.mp4', 'ABC-123-4K.mkv', 'ABC-124.mp4'):
            (self.root / name).touch()
        shared = self.root / 'ABC-123.nfo'
        shared.write_text('<movie><id>ABC-123</id><title>本地标题</title></movie>', encoding='utf-8')
        for name in (video.name, 'ABC-123-CD2.mp4', 'ABC-123-4K.mkv'):
            self.assertEqual(sidecars(self.root / name)[0], shared)
        self.assertIsNone(sidecars(self.root / 'ABC-124.mp4')[0])
        named = video.with_suffix('.nfo')
        named.touch()
        self.assertEqual(sidecars(video)[0], named)
        (self.root / 'ABC-123-4K.nfo').touch()
        self.assertIsNone(sidecars(self.root / 'ABC-123-CD2.mp4')[0])

    def test_movie_sidecar_is_shared_only_by_one_release(self):
        video = self.root / 'ABC-123-CD1.mp4'
        for name in (video.name, 'ABC-123-CD2.mp4'):
            (self.root / name).touch()
        common = self.root / 'movie.nfo'
        common.touch()
        self.assertEqual(sidecars(video)[0], common)
        (self.root / 'ABC-124.mp4').touch()
        self.assertIsNone(sidecars(video)[0])

    @windows_ledger_roots
    def test_shared_nfo_fills_parts_and_verifies_cached_remote_metadata(self):
        media = self.root / 'media'
        media.mkdir()
        for part in (1, 2):
            (media / f'ABW-358-CD{part}.mp4').write_bytes(b'video')
        (media / 'ABW-358.nfo').write_text(
            '<movie><id>ABW-358</id><title>本地标题</title><studio>本地厂牌</studio>'
            '<premiered>2023-05-26</premiered><actor><name>本地演员</name></actor>'
            '<tag>主观视角</tag></movie>', encoding='utf-8')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {
            'id': 'ABW-358', 'title': '本地标题', 'maker': '本地厂牌',
            'release_date': '2023-05-26', 'actresses': [{'japanese_name': '本地演员'}],
            'genres': ['主觀視角'],
        }
        factory = Mock(return_value=provider)
        with patch('peach.library_processing._asks_cover', return_value=False), \
                patch('peach.library_processing._generate_previews', return_value=0), \
                patch('peach.library_processing._enrich_finished_performers',
                      return_value={'aliases': 0, 'avatars': 0, 'conflicts': 0, 'failed': 0}):
            result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                     provider_factory=factory, database=LedgerDatabase(db),
                                     route_overrides='censored=r18dev')
        self.assertEqual(result['status'], 'complete')
        provider.query.assert_called_once()
        with closing(sqlite3.connect(db)) as connection:
            self.assertEqual(connection.execute(
                'SELECT code,catalog_title,studio FROM asset ORDER BY name').fetchall(),
                [('ABW-358', '本地标题', '本地厂牌')] * 2)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM review_decision WHERE status='approved'").fetchone()[0], 10)

    @windows_ledger_roots
    def test_nfo_errors_allow_remote_fill_and_existing_values_require_review(self):
        from peach.web_contract import WebContract
        from peach.web_review import q_review, w_review_decision
        documents = (
            '<movie><title>未闭合',
            '<metadata><title>其他格式</title></metadata>',
            '<movie><id>ABW-359</id><title>其他作品</title></movie>',
            '<movie><id>ABW-358</id><title>本地标题</title><premiered>2023-99-99</premiered></movie>',
        )
        for index, document in enumerate(documents):
            with self.subTest(document=index):
                root = self.root / str(index)
                root.mkdir()
                media = root / 'media'
                media.mkdir()
                (media / 'ABW-358.mp4').write_bytes(b'video')
                (media / 'ABW-358.nfo').write_text(document, encoding='utf-8')
                db = fresh_ledger(root)
                config = PeachConfig(root, root / 'config.toml', present=True,
                                     locations={'local': (str(media),)})
                provider = stub_provider()
                provider.query.return_value = {
                    'id': 'ABW-358', 'title': '在线标题', 'maker': '片商', 'release_date': '2023-05-26',
                    'actresses': [{'japanese_name': '测试演员'}], 'genres': ['主觀視角'],
                }
                generated = root / 'generated'
                covers = root / 'covers'
                with patch('peach.library_processing._asks_cover', return_value=False), \
                        patch('peach.library_processing._generate_previews', return_value=0), \
                        patch('peach.library_processing._enrich_finished_performers',
                              return_value={'aliases': 0, 'avatars': 0, 'conflicts': 0, 'failed': 0}):
                    for _ in range(2):
                        process_library(config, db, generated, covers, provider_factory=Mock(return_value=provider),
                                        database=LedgerDatabase(db), route_overrides='censored=r18dev')
                provider.query.assert_called_once()
                with closing(sqlite3.connect(db)) as connection:
                    title, date = connection.execute('SELECT catalog_title,release_date FROM asset').fetchone()
                self.assertEqual(date, '2023-05-26')
                self.assertEqual(title, '本地标题' if index == 3 else '在线标题')
                contract = WebContract(db, candidate_root=generated, cover_root=covers)
                queue = q_review(contract)['sections']['metadata_fields']
                self.assertEqual([row['field'] for row in queue], ['title'] if index == 3 else [])
                if queue:
                    row = queue[0]
                    choice = next(candidate for candidate in row['candidates'] if candidate['source'] == 'r18dev')
                    w_review_decision(contract, {
                        'category': 'metadata_fields', 'item_key': row['item_key'], 'status': 'approved',
                        'candidate_key': choice['candidate_key'],
                        'expected_revision': row['asset_mutation_revision'],
                    })
                    self.assertEqual(q_review(contract)['sections']['metadata_fields'], [])
                    with closing(sqlite3.connect(db)) as connection:
                        self.assertEqual(connection.execute('SELECT catalog_title FROM asset').fetchone()[0], '在线标题')
                    with patch('peach.library_processing._asks_cover', return_value=False), \
                            patch('peach.library_processing._generate_previews', return_value=0), \
                            patch('peach.library_processing._enrich_finished_performers',
                                  return_value={'aliases': 0, 'avatars': 0, 'conflicts': 0, 'failed': 0}):
                        process_library(config, db, generated, covers, provider_factory=Mock(return_value=provider),
                                        database=LedgerDatabase(db), route_overrides='censored=r18dev')
                    self.assertEqual(q_review(contract)['sections']['metadata_fields'], [])

    def test_releases_sharing_a_folder_keep_their_same_name_posters(self):
        for code in ('ABC-123', 'ABC-124'):
            (self.root / f'{code}.mp4').touch()
            (self.root / f'{code}.jpg').touch()
        (self.root / 'ABC-123-fanart.jpg').touch()
        self.assertEqual(sidecars(self.root / 'ABC-123.mp4')[1], [self.root / 'ABC-123.jpg'])

    def test_episode_and_plain_set_without_jav_identity(self):
        path = self.root / 'episode.nfo'
        path.write_text('<episodedetails><title>Episode</title><uniqueid type="tmdb">123</uniqueid>'
            '<set>Collection</set><aired>2024-01-02</aired></episodedetails>', encoding='utf-8')
        payload, _ = read_nfo(path)
        self.assertEqual(payload['id'], '')
        self.assertEqual(payload['series'], 'Collection')
        self.assertEqual(payload['release_date'], '2024-01-02')

    def test_jav_id_and_imdb_id_have_distinct_meanings(self):
        path = self.root / 'movie.nfo'
        path.write_text('<movie><id>ABW-358</id></movie>', encoding='utf-8')
        self.assertEqual(read_nfo(path)[0]['id'], 'ABW-358')
        path.write_text('<movie><id>tt1234567</id></movie>', encoding='utf-8')
        self.assertEqual(read_nfo(path)[0]['id'], '')

    def test_entity_declarations_rejected_in_utf16(self):
        path = self.root / 'unsafe.nfo'
        path.write_bytes('<!DOCTYPE movie [<!ENTITY x "test">]><movie><title>&x;</title></movie>'.encode('utf-16'))
        with self.assertRaises(ValueError):
            read_nfo(path)

    def test_art_cannot_escape_media_directory(self):
        folder = self.root / 'film'
        folder.mkdir()
        (self.root / 'outside.jpg').touch()
        self.assertIsNone(local_art(folder / 'film.mp4', {'local_art': '../outside.jpg'}))
        self.assertIsNone(local_art(folder / 'film.mp4', {'local_art': 'https://example.com/poster.jpg'}))

    @windows_ledger_roots
    def test_scan_import_and_explicit_review_use_exact_asset(self):
        media = self.root / 'media'
        media.mkdir()
        video = media / 'film.mp4'
        video.write_bytes(b'video')
        (media / 'movie.nfo').write_text('<movie><title>Local title</title><tag>自定义标签</tag></movie>', encoding='utf-8')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        factory = Mock()
        result = process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        self.assertEqual(result['status'], 'complete')
        factory.assert_not_called()
        groups = read_rows(self.root / 'generated/library-metadata-field-candidates.csv')
        title = next(row for row in groups if row['field'] == 'title')
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.row_factory = sqlite3.Row
            self.assertIsNone(connection.execute('SELECT catalog_title FROM asset WHERE medium="video"').fetchone()[0])
            candidate = json.loads(title['candidates_json'])[0]
            owner = review_owner('local_nfo')
            self.assertEqual(
                _apply_metadata_candidate(connection, title, candidate, '2026-09-06', owner), 1)
            written = connection.execute(
                'SELECT catalog_title,field_owners FROM asset WHERE medium="video"').fetchone()
            self.assertEqual(written['catalog_title'], 'Local title')
            # 批准写下的值要答得出是谁写的，否则下一轮自动落库分不清该不该覆盖它。
            self.assertEqual(owner_of(written['field_owners'], 'catalog_title'), owner)
            title['asset_path'] = 'moved.mp4'
            with self.assertRaises(ValueError):
                _apply_metadata_candidate(connection, title, candidate, '2026-09-06', owner)
        self.assertEqual(snapshot(config)['status'], 'complete')

    def test_status_reads_do_not_start_processing(self):
        from peach.web_library_processing import q_library_processing
        from unittest.mock import patch
        contract = Mock()
        contract.db_path = self.root / 'database/ledger.db'
        contract.library_processing_job.snapshot.return_value = {'status': 'running', 'job_id': 'one'}
        with patch('peach.web_library_processing.process_library') as worker:
            self.assertEqual(q_library_processing(contract, {})['job_id'], 'one')
            worker.assert_not_called()

    @windows_ledger_roots
    def test_processing_auto_applies_safe_nfo_fields_before_it_finishes(self):
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-358.mp4').write_bytes(b'video')
        (media / 'ABW-358.nfo').write_text(
            '<movie><title>涼森れむ流</title><sorttitle>ABW-358</sorttitle></movie>',
            encoding='utf-8')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'id': 'ABW-358'}

        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=Mock(return_value=provider),
                                 database=LedgerDatabase(db))

        self.assertGreaterEqual(result['auto_applied'], 1)
        self.assertEqual(result['auto_rejected'], 0)
        with closing(sqlite3.connect(db)) as connection:
            title, owners = connection.execute(
                "SELECT catalog_title,field_owners FROM asset WHERE code='ABW-358'").fetchone()
            decision = connection.execute(
                "SELECT status,note FROM review_decision WHERE category='metadata_fields' "
                "AND item_key='asset:1:title'").fetchone()
        self.assertEqual(title, '涼森れむ流')
        self.assertEqual(owner_of(owners, 'catalog_title'), auto_owner('local_nfo'))
        self.assertEqual(decision[0], 'approved')
        self.assertEqual(json.loads(decision[1])['rule'], 'adr-0029-empty-field-local-nfo')

    @windows_ledger_roots
    def test_stop_preserves_committed_fields_and_prevents_followup_writes(self):
        """停止保留已提交资料，禁止后续写入与头像下载。"""
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-358.mp4').write_bytes(b'video')
        (media / 'ABW-358.nfo').write_text(
            '<movie><title>涼森れむ流</title><sorttitle>ABW-358</sorttitle>'
            '<actor><name>涼森れむ</name></actor></movie>', encoding='utf-8')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        transport = Mock()
        provider = stub_provider()
        provider.transport = transport
        provider.query.return_value = {'id': 'ABW-358'}
        # 采集跑完、收尾还没开始的那一刻按下停止。
        alive = {'value': True}
        commits = []
        database = LedgerDatabase(db)
        database.after_commit = lambda: commits.append(alive['value'])

        def report(state):
            if state.get('stage') == '保存待确认资料':
                alive['value'] = False

        with self.assertRaises(InterruptedError):
            process_library(config, db, self.root / 'generated', self.root / 'covers',
                            provider_factory=Mock(return_value=provider),
                            database=database, report=report,
                            active=lambda: alive['value'])

        with closing(sqlite3.connect(db)) as connection:
            decisions = connection.execute(
                "SELECT count(*) FROM review_decision").fetchone()[0]
            title = connection.execute(
                "SELECT catalog_title FROM asset WHERE code='ABW-358'").fetchone()[0]
        self.assertEqual(decisions, 2)
        self.assertEqual(title, '涼森れむ流')
        self.assertTrue(commits)
        self.assertTrue(all(commits))
        transport.assert_not_called()
        self.assertFalse((self.root / 'generated' / 'avatars').exists())

    @windows_ledger_roots
    def test_profile_evidence_gives_the_candidate_a_new_identity(self):
        """把资料页证据并进 NFO 那条候选之后，`candidate_key` 换成新取值的那一个。

        键是「用户批准的是哪一版」的唯一凭据。补了 DMM 编号和官方头像却留着旧键，
        事后按键回溯拿到的是没有这些证据的那一版，两边对不上。
        """
        groups = {}
        key = 'asset:1:performers'
        people = [{'name': '涼森れむ'}]
        groups[key] = {'item_key': key, 'field': 'performers',
                       'candidates_json': json.dumps(
                           [{'candidate_key': _candidate_identity('local_nfo', people),
                             'source': 'local_nfo', 'value': people}], ensure_ascii=False)}
        before = json.loads(groups[key]['candidates_json'])[0]['candidate_key']

        _merge_local_performer_profiles(groups, key, {'value': [{
            'name': '涼森れむ', 'external_id': '1051912',
            'thumb_url': 'https://pics.dmm.co.jp/mono/actjpgs/suzumori_remu.jpg',
            'profile_source': 'r18dev'}]}, 'r18dev')

        candidate = json.loads(groups[key]['candidates_json'])[0]
        self.assertNotEqual(candidate['candidate_key'], before)
        self.assertEqual(candidate['candidate_key'],
                         _candidate_identity('local_nfo', candidate['value']))

    @windows_ledger_roots
    def test_new_import_normalizes_nfo_tags_and_enriches_the_same_performer(self):
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-358.mp4').write_bytes(b'video')
        (media / 'ABW-358.nfo').write_text(
            '<movie><title>涼森れむ流</title><sorttitle>ABW-358</sorttitle>'
            '<actor><name>涼森れむ</name></actor><tag>主觀視角</tag><tag>苗條</tag>'
            '<tag>單體作品</tag><tag>フルハイビジョン(FHD)</tag></movie>',
            encoding='utf-8')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        image = io.BytesIO()
        Image.new('RGB', (125, 125), '#795548').save(image, format='JPEG')
        transport = Mock(return_value=HttpResponse(
            200, {'content-type': 'image/jpeg'}, image.getvalue(),
            'https://pics.dmm.co.jp/mono/actjpgs/suzumori_remu.jpg'))
        provider = stub_provider()
        provider.transport = transport
        provider.query.return_value = {
            'id': 'ABW-358',
            'actresses': [{
                'dmm_id': 1051912, 'japanese_name': '涼森れむ',
                'name_kana': 'すずもりれむ', 'name_romaji': 'Remu Suzumori',
                'thumb_url': 'https://pics.dmm.co.jp/mono/actjpgs/suzumori_remu.jpg',
                'profile_source': 'r18dev',
            }],
        }
        provider.cover.return_value = False

        with patch('peach.avatar_provider.FaceProbe') as face, \
                patch('peach.library_processing._generate_previews', return_value=0):
            face.return_value.return_value = None
            result = process_library(
                config, db, self.root / 'generated', self.root / 'covers',
                provider_factory=Mock(return_value=provider),
                database=LedgerDatabase(db))

        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['performer_aliases'], 2)
        self.assertEqual(result['performer_avatars'], 1)
        with closing(sqlite3.connect(db)) as connection:
            entity_id = connection.execute(
                "SELECT id FROM entity WHERE kind='performer' AND canonical_name='涼森れむ'").fetchone()[0]
            aliases = {row[0] for row in connection.execute(
                'SELECT alias FROM entity_alias WHERE entity_id=?', (entity_id,))}
            tags = {row[0] for row in connection.execute(
                "SELECT tag FROM asset_tag WHERE tag NOT LIKE '演员:%'")}
        self.assertEqual(aliases, {'すずもりれむ', 'Remu Suzumori'})
        self.assertEqual(tags, {'主观视角', '苗条'})
        self.assertTrue((self.root / 'generated' / 'avatars'
                         / f'performer-{entity_id}.img').is_file())
        transport.assert_called_once()

    @windows_ledger_roots
    def test_no_code_video_pairs_with_its_sibling_image_without_nfo(self):
        """无番号、无 NFO 的视频也拿同目录图片当海报，落在 `{id}_4.jpg`。"""
        from PIL import Image
        media = self.root / 'media'
        media.mkdir()
        (media / '梓怡-背著老公.mp4').write_bytes(b'video')
        Image.new('RGB', (24, 36), (180, 120, 90)).save(media / '梓怡-背著老公.png')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        result = process_library(config, db, self.root / 'generated',
                                 self.root / 'covers', provider_factory=Mock())
        with closing(sqlite3.connect(db)) as connection:
            asset_id = connection.execute(
                "SELECT id FROM asset WHERE name='梓怡-背著老公.mp4'").fetchone()[0]
        self.assertEqual(result['covers'], 1)
        self.assertTrue(
            (self.root / 'generated' / 'posters' / f'{asset_id}_4.jpg').is_file())

    @windows_ledger_roots
    def test_slow_sibling_cover_is_retryable_and_the_next_file_is_processed(self):
        import subprocess
        from peach.local_poster import read_jpeg
        media = self.root / 'media'
        media.mkdir()
        for name in ('合集一', '合集二'):
            (media / f'{name}.mp4').write_bytes(b'video')
            Image.new('RGB', (24, 36), 'teal').save(media / f'{name}.png')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        def read(source, **options):
            if source.stem == '合集一':
                raise subprocess.TimeoutExpired('local-image', 20)
            return read_jpeg(source, **options)
        reports = []
        with patch('peach.local_poster.read_jpeg', side_effect=read):
            result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                     report=lambda state: reports.append(dict(state)))
        self.assertEqual((result['checked'], result['total'], result['covers']), (2, 2, 1))
        self.assertEqual(result['issue_count'], 1)
        self.assertEqual(result['issue_preview'][0]['message'], '读取配套图片超时，可稍后重试')
        self.assertEqual(len(result['retryable_asset_ids']), 1)
        reading = next(state for state in reports if state['current_action'] == 'reading_local_cover')
        self.assertAlmostEqual(reading['current_deadline_at'] - reading['current_started_at'], 20, places=2)

    def test_reader_cannot_scan_or_create_candidates(self):
        from peach.settings_file import ReplicationSettings
        config = PeachConfig(self.root, self.root / 'config.toml', replication=ReplicationSettings(enabled=True))
        with self.assertRaisesRegex(ValueError, '只读端'):
            process_library(config, self.root / 'ledger.db', self.root / 'generated', self.root / 'covers')
        self.assertFalse((self.root / 'generated').exists())
        self.assertFalse((self.root / 'ledger.db').exists())

    @windows_ledger_roots
    def test_local_metadata_remote_completion_and_repeat_are_one_pipeline(self):
        from PIL import Image
        media = self.root / 'media'
        media.mkdir()
        video = media / 'ABW-358.mp4'
        video.write_bytes(b'video')
        (media / 'ABW-358.nfo').write_text('<movie><title>Local title</title><sorttitle>ABW-358</sorttitle>'
            '<premiered>2023-05-26</premiered><actor><name>涼森れむ</name></actor><tag>自定义标签</tag></movie>', encoding='utf-8')
        # 本地海报按长边判断可用尺寸。
        Image.new('RGB', (600, 900), 'blue').save(media / 'ABW-358-poster.jpg')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'maker': 'Studio', 'id': 'ABW-358', 'director': 'Director', 'runtime': 210}
        factory = Mock(return_value=provider)
        result = process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['identified'], 1)
        self.assertEqual(result['covers'], 1)
        provider.cover.assert_not_called()
        self.assertEqual([call.args for call in provider.query.call_args_list],
                         [('ABW-358', 'r18dev'), ('ABW-358', 'dmm')])
        self.assertIn('deadline', provider.query.call_args.kwargs)
        groups = read_rows(self.root / 'generated/library-metadata-field-candidates.csv')
        studio = next(row for row in groups if row['field'] == 'studio')
        self.assertEqual(json.loads(studio['candidates_json'])[0]['catalog_evidence']['runtime']['value'], 210)
        # 成功快照和未取得记录均在有效期内，重复核查直接复用。
        second = process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        self.assertEqual(second['status'], 'complete')
        self.assertEqual(second['candidates'], result['candidates'])
        self.assertEqual([call.args for call in provider.query.call_args_list],
                         [('ABW-358', 'r18dev'), ('ABW-358', 'dmm')])
        asked = (provider.query.call_count, provider.community.call_count, provider.amane.call_count)
        process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        self.assertEqual((provider.query.call_count, provider.community.call_count, provider.amane.call_count),
                         asked)

    @windows_ledger_roots
    def test_local_nfo_fields_keep_remote_differences_for_review(self):
        """本地字段优先补空，在线不同值保留为覆盖复核候选。"""
        from PIL import Image
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-358.mp4').write_bytes(b'video')
        (media / 'ABW-358.nfo').write_text('<movie><title>涼森れむ流</title><sorttitle>ABW-358</sorttitle>'
            '<actor><name>涼森れむ</name></actor></movie>', encoding='utf-8')
        Image.new('RGB', (40, 60), 'blue').save(media / 'ABW-358-poster.jpg')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'id': 'ABW-358', 'title': 'Remu Style', 'maker': 'Prestige',
                                       'actresses': [{'japanese_name': 'Remu Suzumori'}]}
        process_library(config, db, self.root / 'generated', self.root / 'covers',
                        provider_factory=Mock(return_value=provider))
        groups = {row['field']: [entry['source'] for entry in json.loads(row['candidates_json'])]
                  for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')}
        self.assertEqual(groups['title'], ['local_nfo', 'r18dev'])
        self.assertEqual(groups['performers'], ['local_nfo', 'r18dev'])
        self.assertEqual(groups['studio'], ['r18dev'])

    @windows_ledger_roots
    def test_online_sources_only_offer_fields_the_ledger_has_no_value_for(self):
        """账本有日文原题，r18 的英文机翻不是分歧，只是写法。"""
        media = self.root / 'media'
        media.mkdir()
        (media / 'DASS-468.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'id': 'DASS-468', 'title': 'I Was Spoiled To Death', 'maker': 'Das',
                                       'series': 'Spoiled To Death', 'release_date': '2024-09-10',
                                       'actresses': [{'japanese_name': '胡桃さくら'}]}
        factory = Mock(return_value=provider)
        candidates = self.root / 'generated/library-metadata-field-candidates.csv'
        process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        self.assertEqual({row['field'] for row in read_rows(candidates)},
                         {'title', 'studio', 'series', 'release_date', 'performers'})
        with closing(sqlite3.connect(db)) as connection, connection:
            asset_id = connection.execute("SELECT id FROM asset WHERE code='DASS-468'").fetchone()[0]
            connection.execute("UPDATE asset SET catalog_title=?, studio=?, series=?, release_date=? WHERE id=?",
                               ('ふわとろ巨乳の年下義母 胡桃さくら', 'Das', 'ふわとろ巨乳の年下義母',
                                '2024-09-06', asset_id))
            connection.execute("INSERT INTO asset_tag(asset_id, tag, confidence, source) VALUES(?,?,?,?)",
                               (asset_id, '巨乳', 1.0, 'user'))
            connection.execute("INSERT INTO entity(kind, canonical_name, normalized_name, created_at, updated_at) "
                               "VALUES(?,?,?,?,?)",
                               ('performer', '胡桃樱花', 'hutaoyinghua', '2026-09-15', '2026-09-15'))
            entity_id = connection.execute("SELECT id FROM entity WHERE canonical_name='胡桃樱花'").fetchone()[0]
            connection.execute("INSERT INTO asset_entity(asset_id, entity_id, role, source, confidence) "
                               "VALUES(?,?,?,?,?)", (asset_id, entity_id, 'performer', 'user', 1.0))
        candidates.unlink()
        process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        self.assertEqual(list(read_rows(candidates, missing_ok=True)), [])
        self.assertEqual([call.args[1] for call in provider.query.call_args_list], ['r18dev', 'javdb'])

    @windows_ledger_roots
    def test_a_performer_marker_is_not_a_content_tag_the_ledger_already_has(self):
        """`演员:` 是出演者在 `asset_tag` 上的扁平投影，算成标签就再也采不回 genre。
        """
        media = self.root / 'media'
        media.mkdir()
        (media / 'MIDE-612.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'id': 'MIDE-612', 'title': '痴女秘書', 'maker': 'MOODYZ',
                                       'release_date': '2018-01-01', 'genres': ['淫語'],
                                       'actresses': [{'japanese_name': '本田岬'}]}
        factory = Mock(return_value=provider)
        candidates = self.root / 'generated/library-metadata-field-candidates.csv'
        process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        with closing(sqlite3.connect(db)) as connection, connection:
            asset_id = connection.execute("SELECT id FROM asset WHERE code='MIDE-612'").fetchone()[0]
            connection.execute("UPDATE asset SET catalog_title=?, studio=?, release_date=? WHERE id=?",
                               ('痴女秘書', 'MOODYZ', '2018-01-01', asset_id))
            connection.execute("INSERT INTO asset_tag(asset_id, tag, confidence, source) VALUES(?,?,?,?)",
                               (asset_id, '演员:本田岬', 1.0, 'user'))
        candidates.unlink()
        process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        self.assertIn('tags', {row['field'] for row in read_rows(candidates, missing_ok=True)})

    def _collect_with_maker(self, code, maker, payload, r18dev=None, javdb=None):
        """一部有码片走采集，官方档由经桥的片商站 `maker` 答 `payload`；返回 (provider, 候选行)。"""
        media = self.root / 'media'
        media.mkdir()
        (media / f'{code}.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.amane.side_effect = None
        provider.amane.return_value = [(maker, {'id': code, 'content_id': code, 'source': maker, **payload})]
        provider.query.return_value = r18dev or {'id': code}
        if javdb is not None:
            other_query = provider.query.side_effect
            provider.query.side_effect = lambda query, source='r18dev', **kwargs: (
                javdb if source == 'javdb' else other_query(query, source, **kwargs))
        process_library(config, db, self.root / 'generated', self.root / 'covers',
                        provider_factory=Mock(return_value=provider))
        return provider, read_rows(self.root / 'generated/library-metadata-field-candidates.csv')

    @windows_ledger_roots
    def test_javdb_tags_are_collected_with_their_community_identity_after_maker_scalars(self):
        provider, rows = self._collect_with_maker('SSIS-057', 'makers', {
            'title': '標題', 'maker': 'エスワン ナンバーワンスタイル', 'release_date': '2021-05-07',
            'genres': ['巨乳'], 'actresses': [{'japanese_name': '葵つかさ'}]},
            javdb={'id': 'SSIS-057', 'genres': ['高跟鞋', '絲襪', '待收錄分類']})
        self.assertEqual([call.args[1] for call in provider.query.call_args_list], ['javdb'])
        provider.community.assert_not_called()
        tags = next(row for row in rows if row['field'] == 'tags')
        candidate = next(c for c in json.loads(tags['candidates_json']) if c['source'] == 'javdb')
        self.assertEqual(candidate['value'], ['高跟', '丝袜'])
        self.assertEqual(candidate['unmapped_genres'], ['待收錄分類'])
        self.assertEqual((candidate['source_kind'], candidate['official']), ('community', False))

    @windows_ledger_roots
    def test_a_maker_site_answering_every_scalar_settles_the_chain_before_r18dev(self):
        """片商官网给齐标量后，只额外请求 JavDB 内容类别。"""
        provider, rows = self._collect_with_maker('SSIS-057', 'makers', {
            'title': '標題', 'maker': 'エスワン ナンバーワンスタイル', 'release_date': '2021-05-07',
            'genres': ['巨乳'], 'actresses': [{'japanese_name': '葵つかさ'}]})
        self.assertEqual(provider.amane.call_args.kwargs['route'], ('makers',))
        self.assertEqual([call.args[1] for call in provider.query.call_args_list], ['javdb'])
        provider.community.assert_not_called()
        title = next(row for row in rows if row['field'] == 'title')
        self.assertEqual([candidate['source'] for candidate in json.loads(title['candidates_json'])], ['makers'])

    @windows_ledger_roots
    def test_a_maker_site_without_tags_leaves_them_to_r18dev_but_not_to_the_indexes(self):
        """FALENO 官网不给类别：继续请求 r18.dev 与 JavDB。"""
        provider, rows = self._collect_with_maker('FSDSS-437', 'faleno', {
            'title': '標題', 'maker': 'FALENO', 'release_date': '2022-07-07',
            'actresses': [{'japanese_name': '女優'}]},
            r18dev={'id': 'FSDSS-437', 'genres': ['巨乳']})
        self.assertEqual(provider.amane.call_args.kwargs['route'], ('faleno',))
        self.assertEqual([call.args[1] for call in provider.query.call_args_list], ['r18dev', 'javdb'])
        provider.community.assert_not_called()
        tags = next(row for row in rows if row['field'] == 'tags')
        self.assertEqual({candidate['source'] for candidate in json.loads(tags['candidates_json'])}, {'r18dev'})

    def test_the_collector_carries_the_cookies_saved_in_scraping_settings(self):
        """采集设置里贴的 JavBus Cookie 要真的跟着采集走。

        凭据根多给一层 `follow` 不会报错，只会让每个来源都读成「没贴过 Cookie」，
        表现是 JavBus 一直回年龄确认页。
        """
        from peach import scraping_access
        from peach.library_processing import _RemoteSession
        config = PeachConfig(self.root, self.root / 'config.toml', present=True)
        scraping_access.save(config.directory('secrets'), 'javbus', {'cookie': 'existmag=all; age=verified'})
        session = _RemoteSession(config, None, None, retrying=False)
        root = session.provider().transport.inner.root
        self.assertTrue(list(scraping_access.cookie_jar(scraping_access.values_for(root, 'javbus'), 'javbus')))

    @windows_ledger_roots
    def test_a_candidate_repeating_the_current_value_is_not_a_question(self):
        """连本地 NFO 也一样：值和账本里那个字一模一样时没有什么可判断的。"""
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-358.mp4').write_bytes(b'video')
        (media / 'ABW-358.nfo').write_text('<movie><title>涼森れむ流</title><sorttitle>ABW-358</sorttitle>'
            '<studio>Prestige</studio></movie>', encoding='utf-8')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'id': 'ABW-358'}
        factory = Mock(return_value=provider)
        candidates = self.root / 'generated/library-metadata-field-candidates.csv'
        process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        self.assertIn('title', {row['field'] for row in read_rows(candidates)})
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("UPDATE asset SET catalog_title=? WHERE code='ABW-358'", ('涼森れむ流',))
        candidates.unlink()
        process_library(config, db, self.root / 'generated', self.root / 'covers', provider_factory=factory)
        from peach.web_contract import WebContract
        from peach.web_review import q_review
        queue = q_review(WebContract(db, candidate_root=candidates.parent))['sections']['metadata_fields']
        self.assertNotIn('title', {row['field'] for row in queue})

    def test_r18_metadata_takes_japanese_title_series_and_names(self):
        from peach.library_processing import LibraryMetadataProvider
        detail = {'content_id': '118abw358', 'title': 'Remu Style', 'maker': {'name': 'Prestige'},
                  'series': {'name': 'HOW TO SEX'}, 'actresses': [{'name': 'Remu Suzumori'}]}
        combined = {'content_id': '118abw358', 'title_ja': '涼森れむ流', 'series_name_ja': '保健室の先生',
                    'label_name_ja': 'ABSOLUTELY WONDERFUL', 'maker_name_ja': 'プレステージ',
                    'actresses': [{'id': 1051912, 'image_url': 'suzumori_remu.jpg',
                                   'name_kanji': '涼森れむ', 'name_kana': 'すずもりれむ',
                                   'name_romaji': 'Remu Suzumori'}],
                    'directors': [{'name_kanji': 'チャーリー中田'}]}
        pages = lambda transport, url, **kwargs: json.dumps(combined if 'combined=' in url else detail)
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages) as fetch:
            payload = provider.query('ABW-358')
        self.assertIn('combined=118abw358', fetch.call_args_list[1].args[1])
        fields = _fields(payload)
        self.assertEqual(fields['title']['value'], '涼森れむ流')
        self.assertEqual(fields['series']['value'], '保健室の先生')
        self.assertEqual(fields['performers']['display_value'], '涼森れむ')
        self.assertEqual(fields['performers']['value'][0]['external_id'], '1051912')
        self.assertEqual(fields['performers']['value'][0]['aliases'],
                         ['すずもりれむ', 'Remu Suzumori'])
        self.assertEqual(fields['performers']['value'][0]['thumb_url'],
                         'https://pics.dmm.co.jp/mono/actjpgs/suzumori_remu.jpg')
        # 账本厂牌实体用品牌名，日文写法会另起一个实体。
        self.assertEqual(fields['studio']['value'], 'Prestige')

    def test_r18_genres_are_taken_in_japanese_not_in_r18s_english(self):
        """英文是 r18 在 DMM 那套词上再译一层，取日文原词才不丢信息。

        丢的是词根：`その他フェチ` 一眼看得出是「フェチ」那一格的兜底，从
        `Other Fetishes` 反推不回去。英文写法要另外逐条登记才追得平。
        """
        from peach.library_processing import LibraryMetadataProvider
        detail = {'content_id': '118miad573', 'title': 'x',
                  'categories': [{'name': 'Variety'}, {'name': 'Other Fetishes'},
                                 {'name': 'Slender'}]}
        combined = {'content_id': '118miad573',
                    'categories': [{'name_en': 'Variety', 'name_ja': '企画'},
                                   {'name_en': 'Other Fetishes', 'name_ja': 'その他フェチ'},
                                   {'name_en': 'Slender', 'name_ja': 'スレンダー'}]}
        pages = lambda transport, url, **kwargs: json.dumps(combined if 'combined=' in url else detail)
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            payload = provider.query('MIAD-573')
        self.assertEqual(payload['genres'], ['企画', 'その他フェチ', 'スレンダー'])
        tags, unmapped = map_genres(payload['genres'])
        self.assertEqual(tags, ['苗条'])
        self.assertEqual(unmapped, [], '`企画` 是发行企划、`その他フェチ` 是兜底格，都按非内容排除')

    def test_without_the_japanese_page_r18_gives_no_english_title_or_romaji_cast(self):
        from peach.jav_cover_fetch import Unavailable
        from peach.library_processing import LibraryMetadataProvider
        detail = {'content_id': '118abw358', 'title': 'Remu Style', 'actresses': [{'name': 'Remu Suzumori'}],
                  'categories': [{'name': 'Slender'}]}
        def pages(transport, url, **kwargs):
            if 'combined=' in url:
                raise Unavailable('HTTP 503')
            return json.dumps(detail)
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            payload = provider.query('ABW-358')
        fields = _fields(payload)
        self.assertNotIn('title', fields, '英文页的标题是 r18 的译文，留空让来源链去问下一家')
        self.assertNotIn('performers', fields, '罗马字演员会建出罗马字的女优实体')
        self.assertEqual(payload['genres'], ['Slender'], '日文页没取到时，genre 仍然能投影')
        self.assertNotIn('translations', payload)

    def test_fc2cmadb_asks_a_second_time_for_the_women_that_page_holds_back(self):
        """女优是那一页的延迟 prop：第一跳拿到的 HTML 里一个人也没有。"""
        from peach.library_processing import LibraryMetadataProvider
        asked = []
        def pages(transport, url, **kwargs):
            asked.append((url, kwargs.get('extra_headers') or {}))
            if 'fc2cmadb.com' not in url:
                raise NotFound('官方那一页已空')
            if kwargs.get('extra_headers'):
                return json.dumps({'props': {'actresses': [{'name': '野々宮すず'}]}}).encode()
            return _mirror_page()
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            found = provider.fc2('FC2-PPV-3189161', route=('fc2', 'fc2cmadb'))
        self.assertEqual(found, [('fc2cmadb', found[0][1])])
        self.assertEqual(found[0][1]['actresses'], [{'japanese_name': '野々宮すず'}])
        second = [headers for url, headers in asked if headers]
        self.assertEqual([headers['X-Inertia-Partial-Data'] for headers in second], ['actresses'])
        self.assertEqual(_fields(found[0][1])['performers']['display_value'], '野々宮すず',
                         '点名问来的女优进演员候选')

    def test_a_silent_second_ask_costs_the_women_and_nothing_else(self):
        """那一跳撞上限流是常事，其余字段是站上最全的一份，不跟着一起丢。"""
        from peach.jav_cover_fetch import Unavailable
        from peach.library_processing import LibraryMetadataProvider
        def pages(transport, url, **kwargs):
            if 'fc2cmadb.com' not in url:
                raise NotFound('官方那一页已空')
            if kwargs.get('extra_headers'):
                raise Unavailable('HTTP 429')
            return _mirror_page()
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            found = provider.fc2('FC2-PPV-3189161', route=('fc2', 'fc2cmadb'))
        self.assertEqual(found[0][1]['actresses'], [])
        self.assertEqual(found[0][1]['title'], '【無】コスプレシリーズ')

    def _fc2_provider(self, asked, *, image='https://storage92000.contents.fc2.com/file/1.jpg'):
        """三档都摆好的 FC2 链：官方页已空，镜像在，JavArchive 上另有一张转存封面。"""
        from peach.library_processing import LibraryMetadataProvider
        def pages(transport, url, **kwargs):
            if 'fc2cmadb.com' in url:
                if kwargs.get('extra_headers'):
                    # 女优那一栏点名再问一次，问的还是这一页，不算又问了一档。
                    return json.dumps({'props': {'actresses': []}}).encode()
                asked.append(url)
                return _mirror_page(image=image, video=3232110)
            asked.append(url)
            if 'javarchive.com' in url:
                return _archive_pages(url)
            raise NotFound('官方那一页已空')
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        return provider, pages

    def test_the_metadata_step_stops_at_the_source_that_answers(self):
        """标量字段够了就不必再问一档：那一趟只多一批标签，一份流量却是实打实的。"""
        asked = []
        provider, pages = self._fc2_provider(asked)
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            found = provider.fc2('FC2-PPV-3232110')
        self.assertEqual([source for source, _ in found], ['fc2cmadb'])
        self.assertFalse([url for url in asked if 'javarchive.com' in url])

    def _live_fc2_provider(self, asked, *, mirror=True):
        """官方商品页还在：它答上了标题、卖家和发行日，却没有演员栏。"""
        from peach.library_processing import LibraryMetadataProvider
        product = {'@type': 'Product', '@id': 'article:3232110', 'sku': '3232110', 'name': '在售的那一部',
                   'image': {'url': 'https://storage92000.contents.fc2.com/file/1.jpg'},
                   'brand': {'url': 'https://adult.contents.fc2.com/users/rina_vlog/'}}
        shop = (f'<script type="application/ld+json">{json.dumps(product, ensure_ascii=False)}</script>'
                '<div class="items_article_headerInfo"><h3>在售的那一部</h3>'
                '<a href="https://adult.contents.fc2.com/users/rina_vlog/">梨奈</a></div>'
                '<div class="items_article_Releasedate"><p>販売日 : 2022/04/28</p></div>')
        def pages(transport, url, **kwargs):
            if 'fc2cmadb.com' in url:
                if not mirror:
                    asked.append(url)
                    raise NotFound('镜像站上没有这一部')
                if kwargs.get('extra_headers'):
                    return json.dumps({'props': {'actresses': [{'name': '梨奈'}]}}).encode()
                asked.append(url)
                return _mirror_page(video=3232110)
            asked.append(url)
            if 'javarchive.com' in url:
                return _archive_pages(url)
            return shop.encode()
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        return provider, pages

    def test_a_live_product_without_women_still_asks_the_mirror_for_them(self):
        """发行方商品页没有演员栏，镜像站那一栏是 FC2 链上唯一对得上人的地方。"""
        asked = []
        provider, pages = self._live_fc2_provider(asked)
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            found = provider.fc2('FC2-PPV-3232110', required=('title', 'performers'))
        self.assertEqual([source for source, _ in found], ['fc2', 'fc2cmadb'])
        self.assertEqual(found[1][1]['actresses'], [{'japanese_name': '梨奈'}])
        self.assertFalse([url for url in asked if 'javarchive.com' in url], 'JavArchive 不给演员，不为演员去问它')

    def test_the_database_is_asked_for_the_women_the_mirror_does_not_name_and_javten_is_not(self):
        """镜像那一栏空着时接着问 FC2PPV-DB，它也有女优栏；JAVten 不给演员，不为演员去问它。"""
        from peach.library_processing import LibraryMetadataProvider
        asked = []
        database = ('<html><body><main><h1>FC2-PPV-3232110 在售的那一部</h1>'
                    '<div><div><p>出演女優</p></div><div><a href="/ja/actresses/x"><span><img alt="梨奈"></span>'
                    '<span>梨奈</span></a></div></div></main></body></html>')

        def pages(transport, url, **kwargs):
            asked.append(url)
            if 'fc2cmadb.com' in url:
                if kwargs.get('extra_headers'):
                    return json.dumps({'props': {'actresses': []}}).encode()
                return _mirror_page(video=3232110)
            if 'fc2ppv-db.com' in url:
                return database.encode()
            raise NotFound('别处都没有')

        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            found = provider.fc2('FC2-PPV-3232110', required=('title', 'performers'))
        self.assertEqual([source for source, _ in found], ['fc2cmadb', 'fc2ppvdb'])
        self.assertEqual(found[1][1]['actresses'], [{'japanese_name': '梨奈'}])
        self.assertFalse([url for url in asked if 'javten.com' in url or 'javarchive.com' in url])

    def test_a_row_that_already_has_its_cast_stops_at_the_product_page(self):
        asked = []
        provider, pages = self._live_fc2_provider(asked)
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            found = provider.fc2('FC2-PPV-3232110', required=('title',))
        self.assertEqual([source for source, _ in found], ['fc2'])
        self.assertFalse([url for url in asked if 'fc2cmadb.com' in url])

    def test_a_cached_product_page_counts_as_answered(self):
        """缓存里已有官方那页：只为演员去问镜像，镜像也没有就交空，不把整档记成「没有」。"""
        known = [{'id': 'FC2-PPV-3232110', 'title': '在售的那一部', 'actresses': []}]
        for mirror, sources in ((True, ['fc2cmadb']), (False, [])):
            asked = []
            provider, pages = self._live_fc2_provider(asked, mirror=mirror)
            with self.subTest(mirror=mirror), patch('peach.jav_cover_fetch._fetch', side_effect=pages):
                found = provider.fc2('FC2-PPV-3232110', required=('performers',),
                                     route=('fc2cmadb', 'javarchive'), known=known)
                self.assertEqual([source for source, _ in found], sources)
                self.assertFalse([url for url in asked if 'fc2cmadb.com' not in url], '官方页与 JavArchive 都不再问')

    def test_the_cover_step_asks_the_rest_of_the_chain_for_more_image_sources(self):
        """答上的那一档给的地址下不下得来图，这一层判不出来，所以图源要凑齐再挑。"""
        asked = []
        provider, pages = self._fc2_provider(asked)
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            provider.fc2('FC2-PPV-3232110')
            found = provider.fc2('FC2-PPV-3232110', covers=True)
        self.assertEqual([source for source, _ in found], ['fc2cmadb', 'javarchive'])
        self.assertEqual(found[1][1]['cover_url'], _ARCHIVE_COVER)
        self.assertEqual([url for url in asked if 'fc2cmadb.com' in url and 'articles' in url].count(
            'https://fc2cmadb.com/articles/3232110'), 1, '资料那步问过的档不再问第二遍')

    def test_the_mirrors_placeholder_leaves_the_cover_to_the_next_source(self):
        """镜像站标着没有商品图时，它那一栏挂的是占位件，不是这部片的封面。"""
        asked = []
        provider, pages = self._fc2_provider(asked, image='/storage/images/article/no-image.jpg')
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            found = provider.fc2('FC2-PPV-3232110', covers=True)
        self.assertEqual(found[0][1]['cover_url'], '')
        self.assertEqual(found[0][1]['title'], '【無】コスプレシリーズ', '资料照旧带着走')
        self.assertEqual(found[1][1]['cover_url'], _ARCHIVE_COVER)

    def test_every_image_slot_on_the_page_becomes_a_candidate(self):
        """一页上收了几个图位就交几个：排头那张失效时，另一张还得有人量过。"""
        asked = []
        provider, pages = self._fc2_provider(asked)
        with patch('peach.jav_cover_fetch._fetch', side_effect=pages):
            candidates = provider._official_candidates('FC2-PPV-3232110')
        self.assertEqual([candidate.url for candidate in candidates],
                         ['https://storage92000.contents.fc2.com/file/1.jpg',
                          _ARCHIVE_COVER, _ARCHIVE_COVER_SECOND])

    def test_javdb_keeps_its_own_host_interval_on_both_of_its_hosts(self):
        """这一档的节奏由用户定，改动要连图床一起改：页面与图分别落在两个主机上。"""
        from peach.library_processing import SOURCE_INTERVALS
        self.assertEqual(SOURCE_INTERVALS, {'javdb.com': 3.0, 'jdbstatic.com': 3.0, 'javlibrary.com': 3.0})

    def test_community_sources_are_asked_once_per_code_and_say_why_they_failed(self):
        """资料和封面两步都要社区来源的结果，javdb 的配额经不起同一部片问两遍。"""
        from peach.jav_cover_fetch import NotFound, Unavailable
        from peach.library_processing import LibraryMetadataProvider
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        avbase = Mock(side_effect=NotFound('AVBase 没有这个番号'))
        javdb = Mock(return_value={'id': 'ORETD-615'})
        asks = {'avbase': avbase, 'javdb': javdb}
        provider.site = lambda source, code, deadline=None: asks[source](provider.transport, code, deadline=deadline)
        with patch('peach.community_catalog.COMMUNITY_SOURCES', ('avbase', 'javdb')):
            self.assertEqual(provider.community('ORETD-615'), [('javdb', {'id': 'ORETD-615'})])
            provider.community('ORETD-615')
            javdb.side_effect = TimeoutError()
            with self.assertRaisesRegex(Unavailable, r'^javdb：处理出错（TimeoutError）$'):
                provider.community('ORETD-616')
            with self.assertRaisesRegex(Unavailable, 'javdb：'):
                provider.community('ORETD-616')
            javdb.side_effect = Unavailable('javdb 要求登录')
            with self.assertRaisesRegex(Unavailable, '^javdb 要求登录$'):
                provider.community('ORETD-617')
            javdb.side_effect = NotFound('javdb 没有这个番号')
            with self.assertRaises(NotFound):
                provider.community('ORETD-618')
        self.assertEqual(javdb.call_count, 4)

    def test_a_small_official_cover_stays_unless_a_bigger_one_is_confirmed_by_another_origin(self):
        """小封面比没有封面强；社区来源的大图要另一个图源对得上才换上（ADR-0030）。"""
        from peach.jav_cover_fetch import Candidate, NotFound, Unavailable
        from peach.library_processing import LibraryMetadataProvider
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        provider.community = Mock(return_value=[('javdb', {'cover_urls': ['https://c0.jdbstatic.com/covers/x.jpg']})])
        small = (Candidate('image.mgstage.com', 'https://image.mgstage.com/pf_o1.jpg'), (300, 200), b'small')
        large = (Candidate('img.javbus.com', 'https://img.javbus.com/covers/x.jpg'), (1200, 800), b'large',
                 ('javdb', 'mgstage'))
        covers = self.root / 'covers'
        with patch('peach.jav_cover_fetch.best_cover', return_value=small) as official, \
                patch('peach.community_catalog.verified_cover', return_value=large) as verified:
            self.assertTrue(provider.cover('ORETD-615', covers))
        self.assertEqual(official.call_args.kwargs['minimum_width'], 240)
        self.assertEqual(verified.call_args.kwargs['reference'], small)
        self.assertEqual((covers / 'ORETD-615.jpg').read_bytes(), b'large')
        evidence = json.loads((covers / 'ORETD-615.scraping.json').read_text(encoding='utf-8'))
        self.assertEqual((evidence['width'], evidence['verified_by']), (1200, ['javdb', 'mgstage']))

        with patch('peach.jav_cover_fetch.best_cover', return_value=small), \
                patch('peach.community_catalog.verified_cover', side_effect=Unavailable('javdb、mgstage 给的封面不是同一张图，无法互相印证')):
            self.assertTrue(provider.cover('ORETD-616', covers))
        self.assertEqual((covers / 'ORETD-616.jpg').read_bytes(), b'small')

        lone = (Candidate('img.javbus.com', 'https://img.javbus.com/covers/y.jpg'), (800, 538), b'lone', ())
        with patch('peach.jav_cover_fetch.best_cover', side_effect=NotFound('所有渠道都没有候选')), \
                patch('peach.community_catalog.verified_cover', return_value=lone):
            self.assertTrue(provider.cover('IPX-060', covers))
        evidence = json.loads((covers / 'IPX-060.scraping.json').read_text(encoding='utf-8'))
        self.assertEqual(((covers / 'IPX-060.jpg').read_bytes(), evidence['verified_by']), (b'lone', []),
                         '只有一个图源的封面照样装上，verified_by 为空即未经印证')

        with patch('peach.jav_cover_fetch.best_cover', side_effect=Unavailable('官方封面只有缩略图或占位图')), \
                patch('peach.community_catalog.verified_cover', side_effect=Unavailable('社区来源的封面下载失败')), \
                self.assertRaisesRegex(Unavailable, '^官方封面只有缩略图或占位图；社区来源的封面下载失败$'):
            provider.cover('ORETD-617', covers)
        provider.community.side_effect = NotFound('社区来源都没有这个番号')
        with patch('peach.jav_cover_fetch.best_cover', side_effect=NotFound('所有渠道都没有候选')), \
                self.assertRaises(NotFound):
            provider.cover('ORETD-618', covers)

    def test_a_thumbnail_on_disk_is_asked_again_and_only_a_wider_cover_replaces_it(self):
        """缩略图不算有了封面：发行方那里常常还留着原图，问来的更宽才换。"""
        from peach.jav_cover_fetch import Candidate
        from peach.library_processing import CoverKept, LibraryMetadataProvider
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        provider._official_candidates = Mock(return_value=())
        covers = self.root / 'covers'
        covers.mkdir()

        def picture(path, size):
            buffer = io.BytesIO()
            Image.new('RGB', size, 'gray').save(buffer, format='JPEG')
            path.write_bytes(buffer.getvalue())

        picture(covers / 'FC2-PPV-1.jpg', (800, 450))
        with patch('peach.jav_cover_fetch.best_cover') as official:
            self.assertFalse(provider.cover('FC2-PPV-1', covers))
        official.assert_not_called()

        picture(covers / 'FC2-PPV-2.jpg', (276, 154))
        big = (Candidate('storage.contents.fc2.com', 'https://storage.contents.fc2.com/2.jpg'),
               (1180, 2100), b'big')
        with patch('peach.jav_cover_fetch.best_cover', return_value=big):
            self.assertTrue(provider.cover('FC2-PPV-2', covers))
        self.assertEqual((covers / 'FC2-PPV-2.jpg').read_bytes(), b'big')

        picture(covers / 'FC2-PPV-3.jpg', (276, 154))
        before = (covers / 'FC2-PPV-3.jpg').read_bytes()
        # 同一张缩略图转存到别处常差一行像素：面积大一点不算更清楚。
        same = (Candidate('storage.contents.fc2.com', 'https://storage.contents.fc2.com/3.jpg'),
                (276, 155), b'same')
        provider.community = Mock(side_effect=NotFound('社区来源都没有这个番号'))
        with patch('peach.jav_cover_fetch.best_cover', return_value=same), \
                self.assertRaises(CoverKept):
            provider.cover('FC2-PPV-3', covers)
        self.assertEqual((covers / 'FC2-PPV-3.jpg').read_bytes(), before)

        # 官方这一趟没问成时，镜像给的缩略图不能让这部片一周都不再问。
        from peach.jav_cover_fetch import Unavailable
        mirror = (Candidate('img.javstore.net', 'https://img.javstore.net/3.jpg'), (276, 155), b'mirror')
        provider.community = Mock(return_value=[])
        with patch('peach.jav_cover_fetch.best_cover', side_effect=Unavailable('FC2：来源正在冷却')), \
                patch('peach.community_catalog.verified_cover', return_value=(*mirror, ())), \
                self.assertRaises(Unavailable) as raised:
            provider.cover('FC2-PPV-3', covers)
        self.assertNotIsInstance(raised.exception, CoverKept)
        self.assertEqual((covers / 'FC2-PPV-3.jpg').read_bytes(), before)

        # 官方图全回 404（卖家删了图）是确定答复：镜像那张不比本机宽，就是保留，一周不再问。
        with patch('peach.jav_cover_fetch.best_cover', side_effect=NotFound('官方封面地址都没有取到图片')), \
                patch('peach.community_catalog.verified_cover', return_value=(*mirror, ())), \
                self.assertRaises(CoverKept):
            provider.cover('FC2-PPV-3', covers)
        self.assertEqual((covers / 'FC2-PPV-3.jpg').read_bytes(), before)

    def test_a_verified_cover_replaces_a_wider_unverified_picture_of_something_else(self):
        """官方那一档按尺寸挑中的是正片截图：同页竖版商品图经 javdb 印证后，窄一点也换上。

        FC2 官方存储上那张是卖家自己传的商品图，没印证也不当错图换；已经印证过的也不换。
        """
        from peach.jav_cover_fetch import Candidate
        from peach.library_processing import CoverKept, LibraryMetadataProvider
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        portrait_url = 'https://img.javstore.net/images/FC2PPV-1PS.jpg'
        siblings = (Candidate('img.javstore.net', portrait_url),)
        provider._official_candidates = Mock(return_value=siblings)
        provider.community = Mock(return_value=[('javdb', {'cover_urls': ['https://c0.jdbstatic.com/covers/x.jpg']})])
        covers = self.root / 'covers'
        covers.mkdir()

        def gradient(size, rising):
            image = Image.new('L', size)
            image.putdata([int(255 * (x if rising else size[0] - 1 - x) / (size[0] - 1))
                           for x in range(size[0])] * size[1])
            buffer = io.BytesIO()
            image.convert('RGB').save(buffer, format='JPEG')
            return buffer.getvalue()

        screenshot = (Candidate('img.javstore.net', 'https://img.javstore.net/images/FC2PPV-1.jpg'),
                      (605, 364), gradient((605, 364), True))
        portrait = (siblings[0], (510, 616), gradient((510, 616), False), ('img.javstore.net', 'javdb'))

        def kept(code, **evidence):
            (covers / f'{code}.jpg').write_bytes(screenshot[2])
            (covers / f'{code}.scraping.json').write_text(json.dumps(
                {'source_url': screenshot[0].url, 'verified_by': [], **evidence}), encoding='utf-8')

        kept('FC2-PPV-1')
        with patch('peach.jav_cover_fetch.best_cover', return_value=screenshot), \
                patch('peach.community_catalog.verified_cover', return_value=portrait) as verified:
            self.assertTrue(provider.cover('FC2-PPV-1', covers))
        self.assertEqual(verified.call_args.kwargs['siblings'], siblings)
        self.assertEqual((covers / 'FC2-PPV-1.jpg').read_bytes(), portrait[2])
        evidence = json.loads((covers / 'FC2-PPV-1.scraping.json').read_text(encoding='utf-8'))
        self.assertEqual((evidence['width'], evidence['verified_by']), (510, ['img.javstore.net', 'javdb']))

        kept('FC2-PPV-2', source_url='https://storage71000.contents.fc2.com/file/1.jpg')
        kept('FC2-PPV-3', verified_by=['dmm', 'javdb'])
        for code in ('FC2-PPV-2', 'FC2-PPV-3'):
            with patch('peach.jav_cover_fetch.best_cover', return_value=screenshot), \
                    patch('peach.community_catalog.verified_cover', return_value=portrait), \
                    self.assertRaises(CoverKept):
                provider.cover(code, covers)
            self.assertEqual((covers / f'{code}.jpg').read_bytes(), screenshot[2])

    @windows_ledger_roots
    def test_codes_r18_does_not_know_are_collected_from_the_community_sources(self):
        """r18.dev 没有的番号问 AVBase 与 javdb，两家各留一条候选；免不免复核由落库那道闸按几家一致判。"""
        from peach.jav_cover_fetch import NotFound
        media = self.root / 'media'
        media.mkdir()
        (media / 'ORETD-615.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = Mock()
        provider.amane.side_effect = NotFound('片商官网没有这个番号')
        provider.query.side_effect = NotFound('HTTP 404')
        provider.community.return_value = [
            ('avbase', {'id': 'ORETD-615', 'title': 'たまき', 'release_date': '2024-01-05',
                        'source_url': 'https://www.avbase.net/works/orenoshirouto:ORETD-615'}),
            ('javdb', {'id': 'ORETD-615', 'title': 'たまき', 'release_date': '2024-01-04',
                       'source_url': 'https://javdb.com/v/abc'})]
        provider.cover.return_value = False
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=Mock(return_value=provider))
        self.assertEqual((result['status'], result['issue_count']), ('complete', 0))
        rows = {row['field']: row for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')}
        title = json.loads(rows['title']['candidates_json'])
        self.assertEqual(sorted((c['source'], c['provider'], c['source_kind'], c['official'], c['provider_id'])
                                for c in title),
                         [('avbase', 'avbase-search', 'community', False, 'ORETD-615'),
                          ('javdb', 'javdb-page', 'community', False, 'ORETD-615')])
        self.assertEqual(rows['title']['source_profile'], 'library')
        self.assertEqual(len(json.loads(rows['release_date']['candidates_json'])), 2)

    @windows_ledger_roots
    def test_korean_mib_codes_ask_no_jav_source_for_metadata_or_cover(self):
        """`HA-101` 是 MIB 的编号，也是一部日本片的番号：问了就取回那部日本片。"""
        media = self.root / 'media'
        media.mkdir()
        for name in ('HA-101.mp4', 'ABW-001.mp4'):
            (media / name).write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'maker': 'Studio', 'id': 'ABW-001', 'source_url': ''}
        provider.cover.return_value = False
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=Mock(return_value=provider))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['identified'], 2)
        # r18.dev 只答了厂牌，链接着问 DMM 兜底；两问都只为 ABW-001 发。
        self.assertEqual([item.args[:2] for item in provider.query.call_args_list],
                         [('ABW-001', 'r18dev'), ('ABW-001', 'dmm')])
        self.assertEqual([item.args[0] for item in provider.cover.call_args_list], ['ABW-001'])

    @windows_ledger_roots
    def test_codes_of_other_systems_ask_nobody_and_misread_ones_ask_only_the_indexes(self):
        """国产的 `兔子先生TZ-105` 形状和有码一样，按有码链问是每一家都白问一遍、封面还可能取回同号日本片。

        文件名被读错了号的（`UWFr85dczsVeysGg` 读成 `UWFR-085`）只问综合索引一遍。
        """
        media = self.root / 'media'
        (media / '兔子先生TZ-105').mkdir(parents=True)
        (media / '兔子先生TZ-105' / '兔子先生TZ-105K杯.mp4').write_bytes(b'video')
        (media / 'UWFr85dczsVeysGg.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.cover.return_value = False
        process_library(config, db, self.root / 'generated', self.root / 'covers', stage='scan',
                        provider_factory=lambda: provider)
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("UPDATE asset SET code='TZ-105' WHERE name LIKE '%TZ-105%'")
            connection.execute("UPDATE asset SET code='UWFR-085' WHERE name LIKE 'UWF%'")
        process_library(config, db, self.root / 'generated', self.root / 'covers', stage='collect',
                        provider_factory=lambda: provider)
        provider.query.assert_not_called()
        provider.amane.assert_not_called()
        self.assertEqual([item.args[0] for item in provider.community.call_args_list], ['UWFR-085'])
        self.assertEqual([item.args[0] for item in provider.cover.call_args_list], ['UWFR-085'])

    @windows_ledger_roots
    def test_fc2_codes_ask_the_shop_itself_and_never_r18(self):
        """r18.dev 没有 FC2，问一次就是白等一次主机间隔；发行方自己那一页才有这批番号。"""
        media = self.root / 'media'
        media.mkdir()
        for name in ('FC2-PPV-1239052.mp4', 'ABW-001.mp4'):
            (media / name).write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'maker': 'Studio', 'id': 'ABW-001', 'source_url': ''}
        provider.fc2.return_value = [('fc2', {
            'id': 'FC2-PPV-1239052', 'content_id': '1239052', 'maker': 'FC2-PPV',
            'title': 'みお(19)の動画', 'label': '大人仮面Z', 'genres': ['おっぱい'],
            'release_date': '2024-03-31', 'actresses': [],
            'source_url': 'https://adult.contents.fc2.com/article/1239052/'})]
        provider.cover.return_value = False
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=Mock(return_value=provider))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([item.args[:2] for item in provider.query.call_args_list],
                         [('ABW-001', 'r18dev'), ('ABW-001', 'dmm')])
        self.assertEqual([item.args[0] for item in provider.fc2.call_args_list], ['FC2-PPV-1239052'])
        self.assertEqual(sorted(item.args[0] for item in provider.cover.call_args_list),
                         ['ABW-001', 'FC2-PPV-1239052'])
        self.assertEqual(result['issue_count'], 0)
        rows = {(row['code'], row['field']): row
                for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')}
        title = json.loads(rows[('FC2-PPV-1239052', 'title')]['candidates_json'])
        self.assertEqual([(c['source'], c['provider'], c['source_kind'], c['official']) for c in title],
                         [('fc2', 'fc2-article', 'official', True)])
        self.assertEqual(json.loads(rows[('FC2-PPV-1239052', 'studio')]['candidates_json'])[0]['value'],
                         'FC2-PPV')

    @windows_ledger_roots
    def test_a_dated_code_asks_1pondo_only_when_this_file_says_it_is_theirs(self):
        """一本道与カリビアンコム 的番号同形：问错那家，答回来的是同一天发行的另一部片。

        指不着一本道的那条落到综合索引，不问 r18.dev：无码番号在它上面没有
        （`peach.metadata_routes.ROUTES['uncensored']`）。
        """
        media = self.root / 'media'
        (media / '1pon').mkdir(parents=True)
        (media / 'Carib-040221-001-FHD').mkdir()
        (media / '1pon' / '112312_478-1pon-whole1_hd.mp4').write_bytes(b'video')
        (media / 'Carib-040221-001-FHD' / '040221-001-carib-1080p.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.one_pondo.return_value = [('1pondo', {
            'id': '112312_478', 'content_id': '112312_478', 'maker': '一本道',
            'title': '裸演奏 〜第5回演奏会・ホルン〜', 'series': '裸演奏',
            'release_date': '2012-11-23', 'genres': ['スレンダー'],
            'actresses': [{'japanese_name': '飯岡かなこ', 'name_romaji': 'Kanako Iioka'}],
            'source_url': 'https://www.1pondo.tv/movies/112312_478/'})]
        provider.community.side_effect = None
        provider.community.return_value = [('javdb', {
            'id': '040221-001', 'maker': 'カリビアンコム', 'title': '未熟な僕と年上の彼女',
            'release_date': '2021-04-02', 'actresses': [{'japanese_name': '倉本すみれ'}],
            'source_url': 'https://javdb.com/v/carib'})]
        provider.cover.return_value = False
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=Mock(return_value=provider))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([item.args[0] for item in provider.one_pondo.call_args_list], ['112312_478'])
        self.assertEqual([item.args[0] for item in provider.community.call_args_list], ['040221-001'])
        provider.query.assert_not_called()
        rows = {(row['code'], row['field']): row
                for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')}
        performers = json.loads(rows[('112312_478', 'performers')]['candidates_json'])
        self.assertEqual([(c['source'], c['provider'], c['official']) for c in performers],
                         [('1pondo', '1pondo-json', True)])
        self.assertEqual(performers[0]['display_value'], '飯岡かなこ')
        self.assertEqual(result['issue_count'], 0)

    @windows_ledger_roots
    def test_files_without_a_code_are_registered_but_not_reported(self):
        """账本里两万多行创作者作品本来就没有番号，逐行报问题只会淹掉真正要处理的几十条。"""
        media = self.root / 'media'
        media.mkdir()
        (media / '某创作者的作品.mp4').write_bytes(b'video')
        from PIL import Image
        Image.new('RGB', (4, 6), 'teal').save(media / '某创作者的作品.png')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        factory = Mock()
        reports = []
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=factory, report=lambda state: reports.append(dict(state)))
        factory.assert_not_called()
        self.assertEqual((result['status'], result['issue_count'], result['checked']), ('complete', 0, 1))
        self.assertTrue((self.root / 'generated' / 'posters' / '1_4.jpg').is_file(), '本地海报照常登记')
        local = next(state for state in reports if state.get('current_action') == 'reading_local')
        self.assertEqual(local['stage'], '检查配套文件')
        self.assertEqual(local['eta_scope'], '整趟任务')
        prepared = next(state for state in reports if state.get('estimate_pending', {}).get('metadata') == 0)
        self.assertNotIn('local', prepared['estimate_pending'])

    @windows_ledger_roots
    def test_a_directory_name_sitting_in_the_code_column_counts_as_no_code(self):
        """`asset.code` 里的创作者自编号不是发行番号：问不到来源，也不该报成格式无效。"""
        media = self.root / 'media'
        media.mkdir()
        (media / 'DTW003-放课后.mp4').write_bytes(b'video')
        from PIL import Image
        Image.new('RGB', (4, 6), 'teal').save(media / 'DTW003-放课后.png')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.return_value = {'id': 'DTW003', 'maker': 'Studio', 'source_url': ''}
        provider.cover.return_value = False
        process_library(config, db, self.root / 'generated', self.root / 'covers',
                        stage='scan', provider_factory=lambda: provider)
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("UPDATE asset SET code='DTW003'")
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 stage='collect', provider_factory=lambda: provider)
        provider.query.assert_not_called()
        self.assertEqual((result['status'], result['issue_count'], result['checked']), ('complete', 0, 1))
        self.assertTrue((self.root / 'generated' / 'posters' / '1_4.jpg').is_file(), '本地海报照常登记')

    @windows_ledger_roots
    def test_a_tokyo_hot_code_written_with_the_site_name_is_asked_for(self):
        """账本里按目录名落的 `TOKYO-HOT-N0762` 是真番号，要按规范写法去问来源。

        Tokyo-Hot 的编号是无码那条链，问的是综合索引而不是 r18.dev。
        """
        media = self.root / 'media'
        media.mkdir()
        (media / 'n0762.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.community.side_effect = None
        provider.community.return_value = [('javdb', {
            'id': 'n0762', 'maker': 'Tokyo-Hot', 'title': '東京熱 n0762',
            'release_date': '2011-09-30', 'actresses': [{'japanese_name': '倉本すみれ'}],
            'source_url': 'https://javdb.com/v/n0762'})]
        provider.cover.return_value = False
        process_library(config, db, self.root / 'generated', self.root / 'covers',
                        stage='scan', provider_factory=lambda: provider)
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("UPDATE asset SET code='TOKYO-HOT-N0762'")
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 stage='collect', provider_factory=lambda: provider)
        self.assertEqual([item.args[0] for item in provider.community.call_args_list], ['n0762'])
        provider.query.assert_not_called()
        self.assertEqual(result['issue_count'], 0)

    @windows_ledger_roots
    def test_a_throttled_source_reads_as_waiting_not_as_a_thing_that_went_wrong(self):
        """限流和取不到分开报：一个等一会儿就有，一个再点多少次都是同一句。"""
        from peach.jav_cover_fetch import NotFound
        from peach.scraping_access import SourcePaused
        media = self.root / 'media'
        media.mkdir()
        (media / 'STP-26232.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.side_effect = NotFound('HTTP 404')
        provider.community.side_effect = SourcePaused('来源正在冷却，请稍后重试；已有图片保留')
        provider.cover.side_effect = SourcePaused('本趟采集次数已用完，再跑一次接着采')
        state = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                provider_factory=Mock(return_value=provider))
        self.assertEqual([row['message'] for row in state['issue_preview']],
                         ['外部资料本趟没轮到：来源正在冷却，请稍后重试；已有图片保留',
                          '封面本趟没轮到：本趟采集次数已用完，再跑一次接着采'])
        self.assertEqual({row['severity'] for row in state['issue_preview']}, {'paused'})
        self.assertEqual((state['issue_count'], state['paused_count']), (2, 2))
        self.assertEqual(state['error'], '2 项都卡在来源限流上，等一会儿再跑一次。')
        self.assertEqual(state['retryable_asset_ids'], [1], '等来源放开之后重试仍然有意义')

    def test_the_two_kinds_are_counted_apart_in_the_line_that_sums_them_up(self):
        from peach.library_processing import issue_summary
        self.assertEqual(issue_summary(0), '')
        self.assertEqual(issue_summary(3), '3 项需要处理，可重试未完成的部分。')
        self.assertEqual(issue_summary(3, 1),
                         '3 项需要处理，其中 1 项是来源限流，等一会儿再跑；其余可重试未完成的部分。')
        self.assertEqual(issue_summary(3, 3), '3 项都卡在来源限流上，等一会儿再跑一次。')

    @windows_ledger_roots
    def test_a_source_that_said_no_is_not_asked_again_for_a_week(self):
        """r18.dev 不认识的番号每轮都重问、每条卡一次 2 秒的主机间隔，答案永远一样。

        只记来源明确说「没有」的（`NotFound`）；超时与网络故障照旧下次再问。
        「重试未完成项」按上一任务的失败集合强制重试，不看这份记忆。
        """
        from peach.jav_cover_fetch import NotFound, Unavailable
        from peach.library_processing import _MissCache, misses_path
        media = self.root / 'media'
        media.mkdir()
        (media / 'STP-26232.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True, locations={'local': (str(media),)})
        provider = stub_provider()
        provider.query.side_effect = NotFound('HTTP 404')
        provider.community.side_effect = NotFound('社区来源都没有这个番号')
        provider.cover.side_effect = Unavailable('HTTP 503')
        run = lambda **extra: process_library(config, db, self.root / 'generated', self.root / 'covers',
                                              provider_factory=Mock(return_value=provider), **extra)
        first = run()
        self.assertEqual((provider.query.call_count, provider.community.call_count), (1, 1))
        self.assertEqual([row['message'] for row in first['issue_preview']],
                         ['封面未取得：来源返回 HTTP 503'], '来源说没有只报一个数，不占问题清单')
        self.assertEqual(first['notes'], {'querying_metadata': 1})
        recorded = json.loads(misses_path(config).read_text(encoding='utf-8'))
        self.assertEqual(list(recorded['misses']), ['mgstage', 'avbase', 'javbus', 'javdb', 'r18dev'],
                         '按站记，不按档记')
        self.assertEqual(provider.amane.call_args.kwargs['route'], ('mgstage',))
        self.assertEqual(list(recorded['misses']['r18dev']), ['STP-26232'])

        provider.cover.side_effect = NotFound('所有渠道都没有候选')
        second = run()
        self.assertEqual((provider.query.call_count, provider.community.call_count), (1, 1), '资料 7 天内不再问')
        self.assertEqual(provider.cover.call_count, 2, '封面上次是来源故障，这次照问')
        self.assertEqual(second['issue_preview'], [])
        self.assertEqual(second['notes'], {'querying_metadata': 1, 'fetching_cover': 1})
        self.assertEqual(second['retryable_asset_ids'], [])
        self.assertEqual((second['status'], second['issue_count']), ('complete', 0))
        self.assertEqual((first['status'], first['issue_count']), ('failed', 1))
        # 状态文件里没有 `notes` 的任务，读出来要按日志把告知项和问题分开重算。
        stored = dict(second, status='failed', error='2 项需要处理，可重试未完成的部分。', issue_count=2,
                      issue_preview=[{'asset_id': 1, 'title': 'STP-26232.mp4', 'path': '',
                                      'message': message, 'severity': 'error'}
                                     for message in ('外部来源没有这部片的资料，7 天内不再问',
                                                     '外部来源没有这部片的封面，7 天内不再问')])
        stored.pop('notes')
        state_path(config).write_text(json.dumps(stored), encoding='utf-8')
        projected = snapshot(config)
        self.assertEqual((projected['status'], projected['issue_count'], projected['issue_preview']),
                         ('complete', 0, []))
        self.assertEqual(projected['notes'], {'querying_metadata': 1, 'fetching_cover': 1})
        self.assertEqual(json.loads(state_path(config).read_text(encoding='utf-8'))['status'], 'failed')

        third = run()
        self.assertEqual((provider.query.call_count, provider.cover.call_count), (1, 2))
        self.assertEqual(third['issue_count'], 0)
        self.assertEqual((third['status'], third['retryable_asset_ids']), ('complete', []))

        run(retry_ids=[1])
        self.assertEqual((provider.query.call_count, provider.cover.call_count), (2, 3), '重试未完成项不看记忆')

        cache = _MissCache(misses_path(config), now=lambda: time.time() + 8 * 24 * 3600)
        self.assertFalse(cache.fresh('r18dev', 'STP-26232'), '7 天后再问一次')
        self.assertFalse(_MissCache(self.root / 'missing.json').fresh('r18dev', 'STP-26232'))

    @windows_ledger_roots
    def test_a_source_that_already_answered_within_the_week_is_not_asked_again(self):
        """上一趟存下的原始快照还新鲜就直接用，不发请求。

        有效期与「说过没有」的记忆同一个（7 天）。给过待批候选的来源不重问；
        重试忽略过期/缺失记忆，只向尚未给过候选的来源请求新答复。
        """
        media = self.root / 'media'
        media.mkdir()
        (media / 'DASS-468.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        snapshot_path = config.directory('sources') / 'library-metadata' / 'DASS-468-r18dev.json'
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps({
            'id': 'DASS-468', 'title': '甘やかされて', 'maker': 'Das', 'release_date': '2024-09-10',
            'actresses': [{'japanese_name': '胡桃さくら'}], 'source_url': 'https://r18.dev/'}),
            encoding='utf-8')
        provider = stub_provider()
        provider.query.return_value = json.loads(snapshot_path.read_text(encoding='utf-8'))
        provider.cover.return_value = False
        run = lambda **extra: process_library(config, db, self.root / 'generated', self.root / 'covers',
                                              provider_factory=Mock(return_value=provider), **extra)
        result = run()
        self.assertEqual([call.args[1] for call in provider.query.call_args_list], ['javdb'])
        provider.community.assert_not_called()
        self.assertEqual((result['status'], result['issue_count']), ('complete', 0))
        rows = {row['field']: json.loads(row['candidates_json'])
                for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')}
        self.assertEqual([entry['source'] for entry in rows['title']], ['r18dev'])

        os.utime(snapshot_path, (time.time() - 8 * 24 * 3600,) * 2)
        run(retry_ids=[1])
        # r18dev 的整份答复已在待批候选表；重试不重复该来源。DMM 尚未给过候选，
        # 社区档在重试时可以再查此前只说过「没有」的 JavDB。
        retry_sources = [call.args[1] for call in provider.query.call_args_list[1:]]
        self.assertEqual(retry_sources, ['dmm'])
        provider.community.assert_called_once()
        self.assertIn('javdb', provider.community.call_args.kwargs['route'])
        rows = {row['field']: json.loads(row['candidates_json'])
                for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')}
        self.assertEqual([entry['source'] for entry in rows['title']], ['r18dev'])
        self.assertEqual(rows['title'][0]['value'], '甘やかされて')

    @windows_ledger_roots
    def test_fresh_official_and_javdb_snapshots_supply_scalars_and_tags_without_queries(self):
        media = self.root / 'media'
        media.mkdir()
        (media / 'DASS-468.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        directory = config.directory('sources') / 'library-metadata'
        directory.mkdir(parents=True)
        documents = {
            'r18dev': {'id': 'DASS-468', 'title': '甘やかされて', 'maker': 'Das',
                      'release_date': '2024-09-10', 'actresses': [{'japanese_name': '胡桃さくら'}]},
            'javdb': {'id': 'DASS-468', 'genres': ['高跟鞋', 'Anal Play'],
                      'source_url': 'https://javdb.com/v/example'},
        }
        for source, payload in documents.items():
            (directory / f'DASS-468-{source}.json').write_text(
                json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        provider = stub_provider()
        provider.cover.return_value = False
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=Mock(return_value=provider))
        provider.query.assert_not_called()
        provider.community.assert_not_called()
        self.assertEqual((result['status'], result['issue_count']), ('complete', 0))
        rows = {row['field']: json.loads(row['candidates_json'])
                for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')}
        self.assertEqual([entry['source'] for entry in rows['title']], ['r18dev'])
        self.assertEqual([(entry['source'], entry['value']) for entry in rows['tags']],
                         [('javdb', ['高跟', '屁眼'])])
        self.assertEqual(Path(rows['tags'][0]['raw_snapshot']), directory / 'DASS-468-javdb.json')

    def test_expired_javdb_snapshot_is_requested_once_and_answered_sources_are_skipped(self):
        from peach.library_processing import _MissCache, _RemoteSession, misses_path
        config = PeachConfig(self.root, self.root / 'config.toml', present=True)
        path = config.directory('sources') / 'library-metadata' / 'DASS-468-javdb.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'id': 'DASS-468', 'genres': ['口交']}), encoding='utf-8')
        os.utime(path, (time.time() - 8 * 24 * 3600,) * 2)
        provider = Mock()
        provider.query.return_value = {'id': 'DASS-468', 'genres': ['高跟鞋']}
        session = _RemoteSession(config, Mock(return_value=provider),
                                 _MissCache(misses_path(config)), retrying=False)
        options = dict(update=Mock(), issue=Mock(), deadline=time.monotonic() + 60)
        try:
            entries = session._preferred_tags({}, 'DASS-468', ['tags'], ('r18dev', 'javdb'),
                                              [], set(), **options)
            self.assertEqual([(source, payload['genres']) for source, payload, _ in entries],
                             [('javdb', ['高跟鞋'])])
            provider.query.assert_called_once()
            self.assertEqual(provider.query.call_args.args, ('DASS-468', 'javdb'))
            # 同一趟已取到的来源、已有待批候选的来源均不重问；新鲜快照直接复用。
            self.assertEqual(session._preferred_tags({}, 'DASS-468', ['tags'], ('r18dev', 'javdb'),
                                                      entries, set(), **options), [])
            self.assertEqual(session._preferred_tags({}, 'DASS-468', ['tags'], ('r18dev', 'javdb'),
                                                      [], {'javdb'}, **options), [])
            cached = session._preferred_tags({}, 'DASS-468', ['tags'], ('r18dev', 'javdb'),
                                             [], set(), **options)
            self.assertEqual(cached, entries)
            provider.query.assert_called_once()
        finally:
            session.close()

    @windows_ledger_roots
    def test_a_cached_product_page_still_sends_the_row_to_the_mirror_for_its_cast(self):
        """缓存里只有官方商品页、那一页没有演员栏：这一行缺的演员照样去镜像站要，官方页不再问。"""
        media = self.root / 'media'
        media.mkdir()
        (media / 'FC2-PPV-2851534.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        product = {'id': 'FC2-PPV-2851534', 'title': 'キュートなバニーメイド', 'maker': 'rina_vlog',
                   'release_date': '2022-04-28', 'actresses': [], 'source_url': 'https://adult.contents.fc2.com/'}
        snapshot_path = config.directory('sources') / 'library-metadata' / 'FC2-PPV-2851534-fc2.json'
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(product, ensure_ascii=False), encoding='utf-8')
        provider = stub_provider()
        provider.fc2.return_value = [('fc2cmadb', {'id': 'FC2-PPV-2851534', 'actresses': [{'japanese_name': '梨奈'}],
                                                   'source_url': 'https://fc2cmadb.com/articles/2851534'})]
        provider.cover.return_value = False
        process_library(config, db, self.root / 'generated', self.root / 'covers',
                        provider_factory=Mock(return_value=provider))
        provider.fc2.assert_called_once()
        kwargs = provider.fc2.call_args.kwargs
        self.assertNotIn('fc2', kwargs['route'])
        self.assertEqual(kwargs['known'], [product])
        rows = {row['field']: json.loads(row['candidates_json'])
                for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')}
        self.assertEqual([(entry['source'], entry['display_value']) for entry in rows['performers']],
                         [('fc2cmadb', '梨奈')])

    @windows_ledger_roots
    def test_a_pending_candidate_skips_only_the_source_that_gave_it(self):
        """演员栏只有 fc2cmadb 一条待批候选：重跑时照样去问演员，只是不再问 fc2cmadb。

        待批候选不是账本的值，免复核要两家一致；把它当成「已有着落」，FC2PPV-DB 就一次都轮不到。
        """
        media = self.root / 'media'
        media.mkdir()
        (media / 'FC2-PPV-2851534.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = stub_provider()
        provider.fc2.return_value = [
            ('fc2', {'id': 'FC2-PPV-2851534', 'title': 'キュートなバニーメイド', 'maker': 'rina_vlog',
                     'release_date': '2022-04-28', 'actresses': [], 'source_url': 'https://adult.contents.fc2.com/'}),
            ('fc2cmadb', {'id': 'FC2-PPV-2851534', 'actresses': [{'japanese_name': '梨奈'}],
                          'source_url': 'https://fc2cmadb.com/articles/2851534'})]
        provider.cover.return_value = False
        run = lambda: process_library(config, db, self.root / 'generated', self.root / 'covers',
                                      provider_factory=Mock(return_value=provider))
        run()
        provider.fc2.return_value = [('fc2ppvdb', {'id': 'FC2-PPV-2851534', 'actresses': [{'japanese_name': '梨奈'}],
                                                   'source_url': 'https://fc2ppvdb.com/articles/2851534'})]
        run()
        kwargs = provider.fc2.call_args.kwargs
        self.assertIn('performers', kwargs['required'])
        self.assertEqual(kwargs['route'], ('fc2ppvdb', 'javten', 'javarchive'))
        rows = {row['field']: json.loads(row['candidates_json'])
                for row in read_rows(self.root / 'generated/library-metadata-field-candidates.csv')}
        self.assertEqual([entry['source'] for entry in rows['performers']], ['fc2cmadb', 'fc2ppvdb'])
        self.assertEqual([entry['source'] for entry in rows['title']], ['fc2'], '同一家不重复产候选')

    def test_the_cover_step_reuses_the_r18_answer_the_metadata_step_just_fetched(self):
        """资料那一步刚取回 r18.dev 的作品 JSON，封面那一步拿里面的原图地址直接量，不再问 r18.dev。

        处理链里 `best_cover` 手上没有缓存目录，每个非 FC2 番号都要再问一遍 r18.dev；
        快照交下去之后，这一问省掉了，装上的是同一张图。
        """
        from peach.library_processing import LibraryMetadataProvider
        buffer = io.BytesIO()
        Image.new('RGB', (800, 538), 'gray').save(buffer, format='JPEG')
        picture = buffer.getvalue()
        raw = {'content_id': 'ipx00060', 'images': {'jacket_image': {
            'large': 'https://pics.dmm.co.jp/digital/video/ipx00060/ipx00060pl.jpg'}}}
        asked = {}
        for label, snapshots in (('without', ()), ('with', [('r18dev', {'id': 'IPX-060', 'raw': raw})])):
            hits = asked.setdefault(label, [])

            def transport(request, timeout, limit, hits=hits):
                hits.append(request.url)
                if 'r18.dev' in request.url:
                    return HttpResponse(200, {}, json.dumps(raw).encode(), request.url)
                return HttpResponse(200, {}, picture, request.url)

            provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
            provider.transport = transport
            provider.community = Mock(side_effect=NotFound('社区来源都没有这个番号'))
            covers = self.root / label
            self.assertTrue(provider.cover('IPX-060', covers, snapshots=snapshots))
            with Image.open(covers / 'IPX-060.jpg') as installed:
                self.assertEqual(installed.size, (800, 538))
        self.assertEqual(sum('r18.dev' in url for url in asked['without']), 1)
        self.assertEqual(sum('r18.dev' in url for url in asked['with']), 0)
        self.assertLess(len(asked['with']), len(asked['without']))

    def test_the_cover_step_skips_r18_where_the_route_never_asks_it(self):
        """r18.dev 不在这个番号的链上（无码、判不准的番号），或一周内说过没有：封面那一步也不问它。"""
        from peach.library_processing import _official_evidence
        self.assertIsNone(_official_evidence([]))
        self.assertEqual(_official_evidence([], {'r18dev'}).sources, frozenset({'r18dev'}))
        self.assertIsNone(_official_evidence([('r18dev', {'id': 'X-1'})]), '没有作品 JSON 也没有图址的快照不算证据')

    def test_maker_snapshot_supplies_its_own_cover_candidate(self):
        from peach.library_processing import _official_evidence
        url = 'https://cdn.up-timely.com/image/30/content/68732/cover.jpg'
        evidence = _official_evidence([('makers', {
            'source_url': 'https://moodyz.com/works/detail/midv377',
            'cover_urls': [url], 'title': '作品', 'genres': ['Blu-ray（ブルーレイ）'],
        }), ('javdb', {'cover_urls': ['https://c0.jdbstatic.com/covers/x.jpg']})])
        self.assertIsNotNone(evidence)
        self.assertEqual([candidate.url for candidate in evidence.candidates], [url])

    def test_fc2_snapshots_supply_only_product_storage_originals(self):
        from peach.library_processing import _official_evidence
        original = 'https://storage12000.contents.fc2.com/file/342/34141418/1554539118.7.jpg'
        evidence = _official_evidence([('javten', {'cover_urls': [original]}),
            ('javarchive', {'cover_urls': ['https://img.javstore.net/a.jpg']}),
            ('javdb', {'cover_urls': ['https://c0.jdbstatic.com/a.jpg']})])
        self.assertEqual([candidate.url for candidate in evidence.candidates], [original])

    def test_community_cover_is_eligible_for_official_replacement(self):
        from peach.library_processing import cover_settled
        target = self.root / 'MIDV-377.jpg'
        Image.new('RGB', (800, 438)).save(target)
        target.with_suffix('.scraping.json').write_text(json.dumps({
            'source': 'c0.jdbstatic.com', 'source_url': 'https://c0.jdbstatic.com/covers/x.jpg',
        }), encoding='utf-8')
        self.assertFalse(cover_settled(target))

    def test_official_cover_can_replace_a_larger_community_copy(self):
        from peach.jav_cover_fetch import Candidate
        from peach.library_processing import LibraryMetadataProvider
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        provider._official_candidates = Mock(return_value=())
        covers = self.root / 'covers'
        covers.mkdir()
        target = covers / 'MIDV-377.jpg'
        Image.new('RGB', (2000, 1100), 'gray').save(target)
        target.with_suffix('.scraping.json').write_text(json.dumps({
            'source_url': 'https://c0.jdbstatic.com/covers/x.jpg',
        }), encoding='utf-8')
        data = io.BytesIO()
        Image.new('RGB', (1600, 1200), 'blue').save(data, 'JPEG')
        official = (Candidate('awsimgsrc.dmm.com', 'https://awsimgsrc.dmm.com/dig/cover.jpg'),
                    (1600, 1200), data.getvalue())
        with patch('peach.jav_cover_fetch.best_cover', return_value=official):
            self.assertTrue(provider.cover('MIDV-377', covers, snapshots=[('makers', {
                'title': '作品', 'genres': ['Blu-ray（ブルーレイ）'],
            })]))
        installed = json.loads(target.with_suffix('.scraping.json').read_text(encoding='utf-8'))
        self.assertEqual(installed['source'], 'awsimgsrc.dmm.com')
        self.assertEqual(installed['title'], '作品 Blu-ray')

    def test_a_source_held_by_its_cooldown_is_reported_as_not_yet_reached(self):
        """冷却里的来源说的是「本趟没轮到」，不是「没取到」：社区那一档与封面那一步都按它分档。

        自写来源撞上验证页或限流时，来源层已经把那一站写进冷却（`SourceFailure.cooldown_action`），
        与传输层的 `SourcePaused` 同一档。
        """
        from peach.jav_cover_fetch import Unavailable
        from peach.library_processing import LibraryMetadataProvider
        from peach.scraping_access import SourcePaused
        from peach.sources.base import FailureReason, SourceFailure
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        answers = {'avbase': SourcePaused('来源正在冷却，请稍后重试'), 'javbus': NotFound('没有'),
                   'javdb': SourceFailure(FailureReason.CLOUDFLARE_CHALLENGE, 'javdb 撞上了验证页')}

        def site(source, code, **_):
            raise answers[source]

        provider.site = site
        route = ('avbase', 'javbus', 'javdb')
        with self.assertRaises(SourcePaused):
            provider.community('ORETD-701', route=route)
        answers['javbus'] = Unavailable('HTTP 503')
        with self.assertRaises(Unavailable):
            provider.community('ORETD-702', route=route)

        provider._official_candidates = Mock(return_value=())
        provider.community = Mock(side_effect=SourcePaused('javdb：来源正在冷却'))
        with patch('peach.jav_cover_fetch.best_cover', side_effect=SourcePaused('本趟采集次数已用完')), \
                self.assertRaisesRegex(SourcePaused, '^本趟采集次数已用完；javdb：来源正在冷却$'):
            provider.cover('ORETD-703', self.root / 'covers')
        with patch('peach.jav_cover_fetch.best_cover', side_effect=Unavailable('HTTP 503')), \
                self.assertRaises(Unavailable):
            provider.cover('ORETD-704', self.root / 'covers')

    @windows_ledger_roots
    def test_a_challenge_on_a_source_of_its_own_is_listed_as_not_yet_reached(self):
        """r18.dev 撞上验证页、其余几档说没有：这一行在问题清单里是「本趟没轮到」，不是失败。"""
        from peach.sources.base import FailureReason, SourceFailure
        media = self.root / 'media'
        media.mkdir()
        (media / 'DASS-468.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = stub_provider()

        def answer(code, source='r18dev', **_):
            if source == 'dmm':
                raise NotFound('HTTP 404')
            raise SourceFailure(FailureReason.CLOUDFLARE_CHALLENGE, '撞上了验证页')

        provider.query.side_effect = answer
        provider.cover.return_value = False
        state = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                provider_factory=Mock(return_value=provider))
        self.assertEqual([(row['message'], row['severity']) for row in state['issue_preview']],
                         [('外部资料本趟没轮到：r18.dev：撞上了验证页', 'paused')])
        self.assertEqual(state['paused_count'], 1)

    def test_waiting_for_someone_to_click_a_verification_is_not_charged_to_the_title(self):
        """浏览器来源第一次弹验证窗口要等两分多钟，那段时间不算进这部片的预算：下一站照样有预算可问。

        ADR-0065 第二条：弹窗那一段从弹出起算、不受请求时限约束，一部片 90 秒的资料预算装不下它。
        """
        from peach.library_processing import LibraryMetadataProvider
        from peach.sources.base import FailureReason, SourceFailure
        clock = [100.0]
        seen = {}

        class Challenged:
            def records(self, code, *, session):
                seen['fc2ppvdb'] = session.deadline
                clock[0] += 45 + 120
                raise SourceFailure(FailureReason.NOT_FOUND, '没有')

        class Next:
            def records(self, code, *, session):
                seen['javten'] = session.deadline
                return [SimpleNamespace(payload=lambda: {'id': code, 'title': '日本語タイトル'})]

        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        with patch.dict('peach.sources.SITE_SOURCES', {'fc2ppvdb': Challenged, 'javten': Next}), \
                patch('peach.library_processing.time', SimpleNamespace(monotonic=lambda: clock[0])):
            found = provider.fc2('FC2-PPV-1234567', deadline=190.0, route=('fc2ppvdb', 'javten'), covers=True)
        self.assertEqual([name for name, _ in found], ['javten'])
        self.assertEqual((seen['fc2ppvdb'], seen['javten']), (190.0, 310.0))
        self.assertEqual(provider.excused, 120.0)

    def test_a_browser_source_gets_the_whole_automatic_verification_window(self):
        """浏览器来源一条请求给 45 秒，不按剩余预算往下裁：自动过验证最多要 40 秒。"""
        from peach.jav_cover_fetch import BROWSER_REQUEST_TIMEOUT, request_timeout
        self.assertEqual(request_timeout('https://fc2ppv-db.com/ja/articles/1', 10.0), BROWSER_REQUEST_TIMEOUT)
        self.assertEqual(request_timeout('https://javten.com/video/1', None), BROWSER_REQUEST_TIMEOUT)
        self.assertEqual(request_timeout('https://r18.dev/videos/1', 10.0), 10.0)
        self.assertEqual(request_timeout('https://r18.dev/videos/1', None), 30.0)

    @windows_ledger_roots
    def test_a_route_override_decides_who_gets_asked(self):
        """用户把有码那条链换成只问综合索引，r18.dev 就一次都不问。"""
        media = self.root / 'media'
        media.mkdir()
        (media / 'DASS-468.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        provider = stub_provider()
        provider.community.side_effect = None
        provider.community.return_value = [('javdb', {
            'id': 'DASS-468', 'title': '甘やかされて', 'maker': 'Das', 'release_date': '2024-09-10',
            'actresses': [{'japanese_name': '胡桃さくら'}], 'source_url': 'https://javdb.com/v/x'})]
        provider.cover.return_value = False
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=Mock(return_value=provider),
                                 route_overrides='censored=javdb')
        provider.query.assert_not_called()
        self.assertEqual(provider.community.call_args.kwargs['route'], ('javdb',))
        self.assertEqual((result['status'], result['issue_count']), ('complete', 0))

    def test_a_new_site_clears_only_the_memories_whose_lineup_it_joined(self):
        """接上一站只让链上有它的那几条「封面没有」失效，别的站说过的「没有」照旧作数。

        2026-09-21 接上 FC2 商品页与 fc2cmadb 时，430 个 FC2 番号手上压着一条
        「封面没有」：答案没变，能问的人变了。有码番号的链上没有这两站，它们的记忆不该跟着作废，
        javdb 对某个番号说过的「没有」也还是 javdb 的答复。
        """
        from peach.library_processing import _MissCache
        path = self.root / 'misses.json'
        clock = [1000.0]
        now = lambda: clock[0]
        before = _MissCache(path, now=now, sites=('fc2', 'javdb', 'r18dev'))
        before.record('cover', 'FC2-PPV-3189161')
        before.record('cover', 'DASS-468')
        before.record('javdb', 'FC2-PPV-3189161')

        clock[0] = 2000.0
        widened = _MissCache(path, now=now, sites=('fc2', 'fc2cmadb', 'javdb', 'r18dev'))
        self.assertFalse(widened.fresh('cover', 'FC2-PPV-3189161', ('fc2', 'fc2cmadb', 'javdb')))
        self.assertTrue(widened.fresh('cover', 'DASS-468', ('r18dev', 'javdb')))
        self.assertTrue(widened.fresh('javdb', 'FC2-PPV-3189161'))

        clock[0] = 3000.0
        widened.record('r18dev', 'DASS-468')
        again = _MissCache(path, now=now, sites=('fc2', 'fc2cmadb', 'javdb', 'r18dev'))
        self.assertFalse(again.fresh('cover', 'FC2-PPV-3189161', ('fc2', 'fc2cmadb', 'javdb')),
                         '接入时刻落了盘，重开之后那条旧记忆仍不作数')
        clock[0] = 4000.0
        again.record('cover', 'FC2-PPV-3189161')
        self.assertTrue(again.fresh('cover', 'FC2-PPV-3189161', ('fc2', 'fc2cmadb', 'javdb')),
                        '新站接入之后记下的「没有」作数')

    def test_a_memory_kept_per_stage_trusts_only_the_single_site_stages(self):
        """按档记的文件：单站成档的键就是那一站的答复，合档的键说不清是哪一站，丢掉；
        不带来源目录指纹的更早写法无从判断，一律重问。"""
        from peach.library_processing import _legacy_fingerprint, _MissCache
        path = self.root / 'legacy.json'
        stamp = time.time()
        path.write_text(json.dumps({'sources': _legacy_fingerprint(), 'misses': {
            'r18dev': {'STP-26232': stamp}, 'community': {'STP-26232': stamp},
            'cover': {'STP-26232': stamp}, 'fc2cmadb-cast': {'FC2-PPV-1': stamp}}}), encoding='utf-8')
        cache = _MissCache(path)
        self.assertTrue(cache.fresh('r18dev', 'STP-26232'))
        self.assertTrue(cache.fresh('cover', 'STP-26232', ('mgstage', 'javdb', 'r18dev')))
        self.assertTrue(cache.fresh('fc2cmadb-cast', 'FC2-PPV-1'), 'FC2 演员复核脚本记的键照旧作数')
        self.assertFalse(cache.fresh('community', 'STP-26232'))

        path.write_text(json.dumps({'r18dev': {'STP-26232': stamp}}), encoding='utf-8')
        self.assertFalse(_MissCache(path).fresh('r18dev', 'STP-26232'))

    @windows_ledger_roots
    def test_a_stage_asks_only_the_sites_that_have_not_said_no(self):
        """已明确无结果的来源不再请求，尚未查询的 JavDB 与 JavLibrary 保留在链上。"""
        from peach.library_processing import _MissCache, misses_path
        media = self.root / 'media'
        media.mkdir()
        (media / 'DASS-468.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': (str(media),)})
        misses = _MissCache(misses_path(config))
        for site in ('avbase', 'javbus'):
            misses.record(site, 'DASS-468')
        provider = stub_provider()
        provider.query.side_effect = NotFound('HTTP 404')
        provider.cover.return_value = False
        process_library(config, db, self.root / 'generated', self.root / 'covers',
                        provider_factory=Mock(return_value=provider))
        self.assertEqual(provider.community.call_args.kwargs['route'], ('javdb', 'javlibrary'))
        self.assertTrue(_MissCache(misses_path(config)).fresh('javdb', 'DASS-468'))

    def test_management_controls_keep_credentials_and_empty_sections_visible(self):
        root = Path(__file__).resolve().parents[1]
        # 关注管理那一屏在 React 里：能加来源的站才进候选，缺必填凭据的那一行默认展开，
        # 一个站都没配也照样把整块列出来（空清单不等于这块不存在）。
        follow = root / 'frontend/src/react/follow-manage'
        add = (follow / 'add-source.tsx').read_text(encoding='utf-8')
        creds = (follow / 'credentials.tsx').read_text(encoding='utf-8')
        self.assertIn('const rows = (credentials.providers || []).filter((row) => row.followable);', add)
        self.assertIn("defaultOpen={row.requirement === 'required' && !credentialDone(row)}>", creds)
        self.assertIn('const rows = data.providers || [];', creds)
        configuration = (root / 'frontend/src/react/settings/configuration-page.tsx').read_text(encoding='utf-8')
        self.assertNotIn("'/api/library-processing'", configuration)
        page = (root / 'frontend/src/react/data-cleanup/data-cleanup-page.tsx').read_text(encoding='utf-8')
        self.assertIn('<LibraryProcessingCard toast={notify} monitor onComplete={refreshReadings} />', page)


class LibraryWatchdogTests(unittest.TestCase):
    """扫描与采集任务的进度心跳、卡住提示、预算与失败项重试。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def _config(self, media):
        return PeachConfig(self.root, self.root / 'config.toml', present=True,
                           locations={'local': (str(media),)})

    def _provider(self):
        provider = stub_provider()
        provider.query.return_value = {'id': 'code', 'maker': 'Studio', 'source_url': ''}
        provider.cover.return_value = False
        return provider

    @windows_ledger_roots
    def test_reports_carry_current_asset_action_and_rising_sequence(self):
        media = self.root / 'media'
        media.mkdir()
        for name in ('ABW-001.mp4', 'ABW-002.mp4'):
            (media / name).write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = self._config(media)
        reports = []
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=lambda: self._provider(), report=reports.append)
        actions = [row['current_action'] for row in reports]
        names = {row['current_asset_name'] for row in reports if row['current_asset_name']}
        self.assertEqual(names, {'ABW-001.mp4', 'ABW-002.mp4'})
        self.assertIn('reading_local', actions)
        self.assertIn('querying_metadata', actions)
        self.assertIn('fetching_cover', actions)
        sequences = [row['progress_seq'] for row in reports]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(set(sequences)), len(sequences))
        self.assertEqual(result['status'], 'complete')
        self.assertIsNone(result['current_asset_id'])
        self.assertEqual(result['current_action'], '')

    @windows_ledger_roots
    def test_issue_preview_is_capped_while_the_log_keeps_every_row(self):
        media = self.root / 'media'
        media.mkdir()
        for index in range(25):
            (media / f'样品{index:02d}.mp4').write_bytes(b'video')
            (media / f'样品{index:02d}.nfo').write_text('<movie><title>没闭合', encoding='utf-8')
        db = fresh_ledger(self.root)
        config = self._config(media)
        result = process_library(config, db, self.root / 'generated', self.root / 'covers')
        self.assertEqual(result['issue_count'], 25)
        self.assertEqual(len(result['issue_preview']), 20)
        self.assertTrue(result['issues_truncated'])
        log = issues_path(config, result['job_id'])
        self.assertEqual(len([line for line in log.read_text(encoding='utf-8').splitlines() if line]), 25)
        from peach.web_library_processing import q_library_processing_issues
        contract = Mock()
        contract.library_processing_job.snapshot.return_value = {'status': 'running', 'job_id': result['job_id']}
        with patch('peach.web_library_processing.settings_file.active', return_value=config):
            page = q_library_processing_issues(contract, {'job_id': result['job_id'], 'offset': 20, 'limit': 5})
        self.assertEqual(page['total'], 25)
        self.assertEqual(len(page['rows']), 5)
        logged = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines() if line]
        self.assertEqual(page['rows'], logged[20:25])

    def test_status_read_recovers_the_log_path_left_by_an_earlier_job(self):
        """上一趟任务的状态文件里没有这个字段，它那份完整清单却还在磁盘上。"""
        from peach.web_library_processing import q_library_processing
        config = self._config(self.root / 'media')
        log = issues_path(config, 'old')
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({'asset_id': 1, 'message': '未识别到番号'}) + '\n', encoding='utf-8')
        state_path(config).write_text(json.dumps({'status': 'failed', 'job_id': 'old', 'issue_count': 1}),
                                      encoding='utf-8')
        contract = Mock()
        contract.db_path = self.root / 'database/ledger.db'
        contract.library_processing_job.snapshot.return_value = None
        with patch('peach.web_library_processing.settings_file.active', return_value=config):
            self.assertEqual(q_library_processing(contract, {})['issues_log'], str(log))

    @windows_ledger_roots
    def test_each_issue_names_the_item_its_path_and_where_the_full_log_is(self):
        """一句「NFO 无法解析」加一个链接，是哪个文件得逐个点开才知道；改名或去磁盘上
        确认时要用的是路径。完整清单的地址跟着状态一起给出，不让人按 job_id 自己去拼。
        """
        media = self.root / 'media'
        media.mkdir()
        (media / '样品.mp4').write_bytes(b'video')
        (media / '样品.nfo').write_text('<movie><title>没闭合', encoding='utf-8')
        db = fresh_ledger(self.root)
        config = self._config(media)
        result = process_library(config, db, self.root / 'generated', self.root / 'covers')
        preview = result['issue_preview'][0]
        self.assertEqual(preview['title'], '样品.mp4')
        self.assertTrue(preview['path'].endswith('样品.mp4'), preview['path'])
        self.assertEqual(result['issues_log'], str(issues_path(config, result['job_id'])))
        log = issues_path(config, result['job_id'])
        logged = json.loads(log.read_text(encoding='utf-8').splitlines()[0])
        self.assertEqual(logged['title'], '样品.mp4')
        self.assertEqual(logged['path'], preview['path'])

    def test_stalled_warning_never_flips_a_live_task_to_failed(self):
        config = self._config(self.root / 'media')
        path = state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        old = time.time() - (STALL_AFTER_SECONDS + 30)
        path.write_text(json.dumps({'status': 'running', 'job_id': 'one', 'started_at': old,
                                    'last_progress_at': old, 'current_started_at': old,
                                    'current_action': 'reading_local'}), encoding='utf-8')
        with FileLock(str(path) + '.lock', timeout=0):
            live = snapshot(config)
        self.assertEqual(live['status'], 'running')
        self.assertTrue(live['stalled'])
        dead = snapshot(config)
        self.assertEqual(dead['status'], 'failed')
        self.assertIn('中断', dead['error'])
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['status'], 'failed')
        # 另一个读取者正好占着锁时读到的也是同一个结论，不会跳回「运行中」。
        with FileLock(str(path) + '.lock', timeout=0):
            again = snapshot(config)
        self.assertEqual(again['status'], 'failed')
        self.assertNotIn('stalled', again)

    def test_deadline_within_budget_is_running_and_expired_is_stalled(self):
        now = time.time()
        within = decorate({'status': 'running', 'last_progress_at': now - 300,
                           'current_deadline_at': now + 30}, now=now)
        expired = decorate({'status': 'running', 'last_progress_at': now - 300,
                            'current_deadline_at': now - 1}, now=now)
        self.assertFalse(within['stalled'])
        self.assertTrue(expired['stalled'])

    def test_stalled_read_hides_stale_eta_without_mutating_worker_state(self):
        state = dict(status='running', last_progress_at=100, current_started_at=100,
                     eta_seconds_min=63, eta_seconds_max=94, eta_kind='measured')
        waiting = decorate(state, now=100 + STALL_AFTER_SECONDS + 1)
        self.assertTrue(waiting['stalled'])
        self.assertIsNone(waiting['eta_seconds_min'])
        self.assertIsNone(waiting['eta_seconds_max'])
        self.assertEqual(state['eta_seconds_max'], 94)
        resumed = decorate({**state, 'last_progress_at': 1000}, now=1001)
        self.assertFalse(resumed['stalled'])
        self.assertEqual(resumed['eta_seconds_max'], 94)

    def test_starting_a_job_keeps_the_previous_issue_log(self):
        from peach.library_processing import _processing_issue_log
        config = self._config(self.root / 'media')
        path = state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = issues_path(config, 'previous')
        previous.write_text('previous evidence\n', encoding='utf-8')
        current = _processing_issue_log(config, path, 'current', False)
        self.assertEqual(current, issues_path(config, 'current'))
        self.assertEqual(previous.read_text(encoding='utf-8'), 'previous evidence\n')

    def test_interleaved_folders_share_the_same_task_directory_index(self):
        from peach.library_processing import _DirectoryIndex
        folders = [self.root / str(index) for index in range(10)]
        listing = _DirectoryIndex()
        with patch('peach.library_processing.directory_files', side_effect=lambda path: {'video.mp4': path / 'video.mp4'}) as read:
            for folder in folders + folders:
                self.assertIn('video.mp4', listing.files(folder))
        self.assertEqual(read.call_count, 10)

    def test_legacy_issue_list_projects_into_count_and_preview(self):
        legacy = [{'asset_id': index, 'message': '未识别到番号'} for index in range(1, 26)]
        state = decorate({'status': 'failed', 'job_id': 'old', 'issues': legacy})
        self.assertNotIn('issues', state)
        self.assertEqual(state['issue_count'], 25)
        self.assertEqual(len(state['issue_preview']), 20)
        self.assertEqual(state['issue_preview'][0],
                         {'asset_id': 1, 'title': '', 'path': '', 'message': '未识别到番号'})
        self.assertTrue(state['issues_truncated'])

    @windows_ledger_roots
    def test_deadline_skips_one_asset_and_keeps_processing(self):
        from peach.jav_cover_fetch import DeadlineExceeded
        media = self.root / 'media'
        media.mkdir()
        for name in ('ABW-101.mp4', 'ABW-102.mp4'):
            (media / name).write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = self._config(media)
        provider = self._provider()
        # ABW-101 在 r18.dev 那一问就用尽预算；ABW-102 的 r18.dev 只答厂牌，接着问 DMM 兜底。
        provider.query.side_effect = [DeadlineExceeded('预算'), provider.query.return_value, NotFound('HTTP 404')]
        result = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                 provider_factory=lambda: provider)
        with closing(sqlite3.connect(db)) as connection:
            ids = {name: row_id for name, row_id in connection.execute('SELECT name, id FROM asset')}
        self.assertEqual(result['retryable_asset_ids'], [ids['ABW-101.mp4']])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([item.args for item in provider.query.call_args_list],
                         [('ABW-101', 'r18dev'), ('ABW-102', 'r18dev'), ('ABW-102', 'dmm')])
        self.assertEqual(provider.reset.call_count, 1)

    @windows_ledger_roots
    def test_retry_skips_rescan_and_touches_only_listed_assets(self):
        media = self.root / 'media'
        media.mkdir()
        for name in ('ABW-201.mp4', 'ABW-202.mp4'):
            (media / name).write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = self._config(media)
        provider = self._provider()
        first = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                provider_factory=lambda: provider)
        self.assertEqual(first['total'], 2)
        with closing(sqlite3.connect(db)) as connection:
            ids = {name: row_id for name, row_id in connection.execute('SELECT name, id FROM asset')}
        provider.reset_mock()
        with patch('peach.library_processing.scan_location') as scan:
            retried = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                      retry_ids=[ids['ABW-202.mp4']],
                                      provider_factory=lambda: provider)
        scan.assert_not_called()
        self.assertEqual(retried['total'], 1)
        self.assertEqual(retried['checked'], 1)
        self.assertEqual(retried['status'], 'complete')

    @windows_ledger_roots
    def test_retry_ignores_an_unrelated_offline_source(self):
        missing = self.root / 'missing-local'
        cloud = self.root / 'cloud'
        cloud.mkdir()
        (cloud / 'ABW-203.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(
            self.root, self.root / 'config.toml', present=True,
            locations={'local': (str(missing),), '115': (str(cloud),)})
        first = process_library(
            config, db, self.root / 'generated', self.root / 'covers', stage='scan',
            provider_factory=lambda: self._provider())
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(first['issue_preview'][0]['message'], 'local 来源离线')
        with closing(sqlite3.connect(db)) as connection:
            asset_id = connection.execute(
                "SELECT id FROM asset WHERE name='ABW-203.mp4'").fetchone()[0]
        retried = process_library(
            config, db, self.root / 'generated', self.root / 'covers',
            retry_ids=[asset_id], stage='collect', provider_factory=lambda: self._provider())
        self.assertEqual(
            (retried['status'], retried['total'], retried['checked'], retried['issue_count']),
            ('complete', 1, 1, 0))

    @windows_ledger_roots
    def test_retry_still_reports_the_target_source_offline(self):
        cloud = self.root / 'cloud'
        cloud.mkdir()
        (cloud / 'ABW-204.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = PeachConfig(
            self.root, self.root / 'config.toml', present=True,
            locations={'115': (str(cloud),)})
        process_library(
            config, db, self.root / 'generated', self.root / 'covers', stage='scan',
            provider_factory=lambda: self._provider())
        with closing(sqlite3.connect(db)) as connection:
            asset_id = connection.execute(
                "SELECT id FROM asset WHERE name='ABW-204.mp4'").fetchone()[0]
        cloud.rename(self.root / 'offline-cloud')
        retried = process_library(
            config, db, self.root / 'generated', self.root / 'covers',
            retry_ids=[asset_id], stage='collect', provider_factory=lambda: self._provider())
        self.assertEqual((retried['status'], retried['issue_count']), ('failed', 1))
        self.assertEqual(retried['issue_preview'][0]['message'], '115 来源离线')

    @windows_ledger_roots
    def test_scanning_alone_registers_the_files_and_asks_no_source(self):
        """只扫描那一段登记完文件就收工，不读本地资料也不联网。

        新盘刚接上时要的就是这个：几万个文件进了馆藏就能用，采集可以留到夜里。
        """
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-203.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        provider = self._provider()
        state = process_library(self._config(media), db, self.root / 'generated',
                                self.root / 'covers', stage='scan',
                                provider_factory=lambda: provider)
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(state['scanned'], 1)
        self.assertEqual(state['checked'], 0)
        self.assertEqual(state['candidates'], 0)
        provider.query.assert_not_called()
        with closing(sqlite3.connect(db)) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM asset').fetchone()[0], 1,
                             "文件要进馆藏，只是没往下走采集")

    @windows_ledger_roots
    def test_collecting_alone_walks_the_library_without_touching_the_disk_again(self):
        """只采集那一段不再扫一遍来源目录，处理的仍是整个馆藏。

        采集被网络拖住时重跑的就是它：几万个文件的目录遍历没有必要再走一趟。
        """
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-204.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = self._config(media)
        provider = self._provider()
        process_library(config, db, self.root / 'generated', self.root / 'covers',
                        stage='scan', provider_factory=lambda: provider)
        with patch('peach.library_processing.scan_location') as scan:
            state = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                    stage='collect', provider_factory=lambda: provider)
        scan.assert_not_called()
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(state['total'], 1)
        self.assertEqual(state['checked'], 1)

    @windows_ledger_roots
    def test_media_is_checked_for_local_metadata_when_sources_are_settled(self):
        """目录列表用于核对媒体与 NFO；媒体消失时记录问题，在线来源缓存仍然有效。"""
        from peach import metadata_routes
        from peach.library_processing import FIELDS, _MissCache, misses_path
        from peach.review_csv import write_rows
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-205.mp4').write_bytes(b'video')
        db = fresh_ledger(self.root)
        config = self._config(media)
        provider = self._provider()
        process_library(config, db, self.root / 'generated', self.root / 'covers',
                        stage='scan', provider_factory=lambda: provider)
        with closing(sqlite3.connect(db)) as connection, connection:
            asset_id = connection.execute("UPDATE asset SET code='ABW-205', catalog_title='t', studio='s', "
                                          "release_date='2024-01-01' RETURNING id").fetchone()[0]
        (self.root / 'covers').mkdir()
        Image.new('RGB', (800, 538), 'gray').save(self.root / 'covers' / 'ABW-205.jpg', format='JPEG')
        blank = {field: '' for field in FIELDS}
        write_rows(self.root / 'generated' / 'library-metadata-field-candidates.csv', FIELDS,
                   [dict(blank, item_key=f'asset:{asset_id}:{field}') for field in ('performers', 'tags')])
        misses = _MissCache(misses_path(config))
        for site in metadata_routes.route_for_code('ABW-205'):
            misses.record(site, 'ABW-205')
        (media / 'ABW-205.mp4').unlink()
        with patch('peach.library_processing.sidecars') as listing:
            state = process_library(config, db, self.root / 'generated', self.root / 'covers',
                                    stage='collect', provider_factory=lambda: provider)
        listing.assert_not_called()
        provider.query.assert_not_called()
        self.assertEqual((state['status'], state['checked'], state['issue_count']), ('failed', 1, 1))

    @windows_ledger_roots
    def test_one_directory_is_listed_once_for_all_the_videos_in_it(self):
        """同一个文件夹里的片子共用一次目录列表，找 NFO 和找海报也不各列一遍。"""
        from peach import library_nfo
        media = self.root / 'media'
        media.mkdir()
        for name in ('ABW-206.mp4', 'ABW-207.mp4', 'ABW-208.mp4'):
            (media / name).write_bytes(b'video')
        db = fresh_ledger(self.root)
        provider = self._provider()
        with patch('peach.library_processing.directory_files', wraps=library_nfo.directory_files) as listed:
            state = process_library(self._config(media), db, self.root / 'generated', self.root / 'covers',
                                    provider_factory=lambda: provider)
        self.assertEqual(state['checked'], 3)
        self.assertEqual(listed.call_count, 1)

    @windows_ledger_roots
    def test_candidates_are_written_once_for_a_short_batch_not_once_per_asset(self):
        """候选 CSV 按时间节流落盘，结束时写全；三条资产不该重写三遍整份文件。"""
        from peach import review_csv
        media = self.root / 'media'
        media.mkdir()
        for name in ('ABW-209.mp4', 'ABW-210.mp4', 'ABW-211.mp4'):
            (media / name).write_bytes(b'video')
        db = fresh_ledger(self.root)
        provider = self._provider()
        with patch('peach.library_processing.write_rows', wraps=review_csv.write_rows) as written:
            state = process_library(self._config(media), db, self.root / 'generated', self.root / 'covers',
                                    provider_factory=lambda: provider)
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(written.call_count, 1)
        rows = read_rows(self.root / 'generated' / 'library-metadata-field-candidates.csv')
        self.assertEqual(len({row['asset_id'] for row in rows}), 3)

    def test_the_stage_asked_for_is_the_stage_that_runs(self):
        """页面点哪一段就跑哪一段，不认识的段数拒绝掉。

        段名要一路传到管线里。只拿它换一句提示文字的话，按钮看着分了工、跑起来
        全是同一件事，而且页面上看不出区别。
        """
        from peach.web_library_processing import w_library_processing
        contract = SimpleNamespace(
            db_path=Path(self.root / 'database' / 'ledger.db'),
            candidate_root=self.root / 'generated', cover_root=self.root / 'covers',
            cache_bust=lambda: None, database=Mock(),
            library_processing_job=SimpleNamespace(
                snapshot=lambda: None,
                start=lambda work, restart, initial, recovery: (work('job'), initial)[1],
                update=lambda job_id, **values: None))
        media = self.root / 'media'
        media.mkdir()
        (self.root / 'database').mkdir(exist_ok=True)
        fresh_ledger(self.root / 'database')
        config = self._config(media)
        with patch('peach.web_library_processing.settings_file.active', return_value=config), \
             patch('peach.web_library_processing.process_library') as run:
            run.return_value = {'job_id': 'job', 'status': 'complete'}
            initial = w_library_processing(contract, {'stage': 'collect'})
            self.assertEqual(run.call_args.kwargs['stage'], 'collect')
            self.assertEqual(initial['requested_stage'], 'collect')
            self.assertEqual(initial['stage'], '准备采集资料')
            w_library_processing(contract, {})
            self.assertEqual(run.call_args.kwargs['stage'], 'all')
            with self.assertRaises(ValueError):
                w_library_processing(contract, {'stage': 'thumbnails'})

    def test_retry_request_accepts_only_the_previous_failure_set(self):
        from peach.web_library_processing import _retry_ids
        previous = {'job_id': 'one', 'retryable_asset_ids': [130, 131]}
        self.assertIsNone(_retry_ids(previous, {}))
        self.assertEqual(_retry_ids(previous, {'job_id': 'one', 'retry': [130]}), [130])
        self.assertEqual(_retry_ids(previous, {'job_id': 'one', 'retry': []}), [130, 131])
        with self.assertRaises(ValueError):
            _retry_ids(previous, {'job_id': 'old', 'retry': [130]})
        with self.assertRaises(ValueError):
            _retry_ids(previous, {'job_id': 'one', 'retry': [999]})
        with self.assertRaises(ValueError):
            _retry_ids(previous, {'job_id': 'one', 'retry': 'all'})

    def test_fetch_refuses_to_start_after_its_budget_is_gone(self):
        from peach.jav_cover_fetch import DeadlineExceeded, _fetch
        transport = Mock()
        with self.assertRaises(DeadlineExceeded):
            _fetch(transport, 'https://example.com/a.jpg', referer='https://example.com/',
                   limit=100, deadline=time.monotonic() - 1)
        transport.assert_not_called()

    def test_fetch_clamps_each_attempt_to_the_remaining_budget(self):
        from peach.jav_cover_fetch import _fetch
        transport = Mock()
        transport.return_value = Mock(status=200, body=b'x')
        _fetch(transport, 'https://example.com/a.jpg', referer='https://example.com/',
               limit=100, deadline=time.monotonic() + 5)
        timeout = transport.call_args.args[1]
        self.assertGreater(timeout, 4)
        self.assertLessEqual(timeout, 5)


class SingleFileImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.media = self.root / 'media'
        self.media.mkdir()
        declared = str(self.media) if os.name == 'nt' else r'R:\media'
        self.config = PeachConfig(self.root, self.root / 'config.toml', present=True,
            locations={'local': (declared,)}, mounts={'local': (str(self.media),)})
        directory = self.root / 'database'
        directory.mkdir()
        self.db = fresh_ledger(directory)
        self.database = LedgerDatabase(self.db)
        self.selected = self.media / 'ABC-101.mp4'
        self.selected.write_bytes(b'selected video')
        (self.media / 'ABC-102.mp4').write_bytes(b'other video')
        self.contract = SimpleNamespace(db_path=self.db, database=self.database,
            library_processing_job=SimpleNamespace(snapshot=lambda: {'status': 'idle'}), cache_bust=Mock())

    def import_file(self, path, **kwargs):
        from peach.web_library_processing import w_library_import_file
        with patch('peach.web_library_processing.settings_file.active', return_value=self.config), \
                patch('peach.web_library_processing.w_library_processing', return_value={'status': 'running'}) as run:
            result = w_library_import_file(self.contract, {'path': path, **kwargs})
            return result, run.call_args

    def test_only_the_selected_file_is_registered_and_scoped_for_collection(self):
        with patch('peach.library_processing.scan_location') as scan:
            result, call = self.import_file(str(self.selected))
        self.assertEqual(result['status'], 'running')
        scan.assert_not_called()
        with self.database.read_connection() as connection:
            rows = connection.execute('SELECT id,name FROM asset').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], self.selected.name)
        self.assertEqual(call.args[1]['asset_ids'], [rows[0]['id']])
        self.assertEqual(call.args[1]['stage'], 'collect')
        _, repeated = self.import_file(str(self.selected))
        self.assertEqual(repeated.args[1]['asset_ids'], [rows[0]['id']])
        with self.database.read_connection() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM asset').fetchone()[0], 1)

    def test_invalid_inputs_do_not_register_any_file(self):
        outside = self.root / 'outside.mp4'
        outside.touch()
        nonvideo = self.media / 'readme.txt'
        nonvideo.touch()
        for path in (None, '', 'relative.mp4', str(self.media), str(outside), str(nonvideo),
                     str(self.media / 'missing.mp4')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.import_file(path)
        with self.assertRaises(ValueError):
            self.import_file(str(self.selected), speed_mode='invalid')
        self.contract.library_processing_job.snapshot = lambda: {'status': 'running'}
        with self.assertRaises(ValueError):
            self.import_file(str(self.selected))
        with self.database.read_connection() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM asset').fetchone()[0], 0)


class LibraryPreviewCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.media = self.root / 'media'
        self.media.mkdir()
        self.config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                                  locations={'local': (str(self.media),),
                                             'pikpak': (str(self.root / 'unmounted-cloud'),)})
        directory = self.root / 'database'
        directory.mkdir()
        self.db = fresh_ledger(directory)
        self.database = LedgerDatabase(self.db)
        self.resolver = Mock()
        self.resolver.ffmpeg.return_value = SimpleNamespace(path=Path('test-ffmpeg'))
        with self.database.write_transaction() as connection:
            for aid, location, medium, disposal in ((1, 'local', 'video', None),
                                                    (2, 'pikpak', 'video', None),
                                                    (3, 'local', 'photo', None),
                                                    (4, 'local', 'video', 'trash')):
                video = self.media / f'clip-{aid}.mp4'
                video.write_bytes(b'video')
                connection.execute(
                    'INSERT INTO asset(id,location,path,name,medium,duration,disposal) VALUES(?,?,?,?,?,?,?)',
                    (aid, location, str(video), video.name, medium, 30, disposal))

    def run_processing(self, **options):
        return process_library(self.config, self.db, self.root / 'candidates', self.root / 'covers',
                               database=self.database, **options)

    def test_selected_collection_rebuilds_stale_path_candidates_and_keeps_other_rows(self):
        from peach.library_processing import FIELDS, _merge_candidates
        from peach.review_csv import write_rows
        with self.database.write_transaction() as connection:
            connection.execute("UPDATE asset SET code='ABC-101' WHERE id=1")
        groups = {}
        document = {'id': 'ABC-101', 'title': '资料标题'}
        for asset_id in (1, 2):
            _merge_candidates(groups, {'id': asset_id, 'name': 'ABC-101.mp4', 'path': r'G:\old\ABC-101.mp4',
                'size': 123}, 'ABC-101', 'r18dev', document, self.root / 'evidence.json', {})
        untouched = dict(groups['asset:2:title'])
        candidates = self.root / 'candidates' / 'library-metadata-field-candidates.csv'
        candidates.parent.mkdir()
        write_rows(candidates, FIELDS, groups.values())
        covers = self.root / 'covers'
        covers.mkdir()
        Image.new('RGB', (2000, 1000)).save(covers / 'ABC-101.jpg')
        provider = stub_provider()
        provider.query.return_value = document
        result = self.run_processing(stage='metadata', asset_ids=[1], provider_factory=lambda: provider,
                                     route_overrides={'censored': ('r18dev',)})
        provider.query.assert_called_once()
        self.assertEqual(result['status'], 'complete')
        rebuilt = {row['item_key']: row for row in read_rows(candidates)}
        self.assertEqual(rebuilt['asset:1:title']['asset_path'], str(self.media / 'clip-1.mp4'))
        self.assertEqual(rebuilt['asset:2:title'], {key: str(value) for key, value in untouched.items()})

    @staticmethod
    def make_tile(_ffmpeg, _pattern, _columns, _rows, destination):
        Image.new('RGB', (1440, 810), 'green').save(destination, format='JPEG')
        return True

    def test_selected_completion_generates_a_snapshot_without_scanning_or_cloud_reads(self):
        reports = []
        with patch('peach.library_processing.scan_location') as scan, \
                patch('peach.library_processing.FFmpegResolver', return_value=self.resolver), \
                patch('peach.frame_capture.capture_with_retry', return_value=True) as capture, \
                patch('peach.frame_capture.tile_frames', side_effect=self.make_tile), \
                patch('peach.library_processing._finish_collection', return_value={}), \
                patch('peach.library_processing.root_online', return_value=True) as online:
            result = self.run_processing(asset_ids=[1], report=reports.append)
        scan.assert_not_called()
        self.assertEqual(capture.call_count, 9)
        online.assert_called_once_with(self.media)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['previews'], 1)
        self.assertEqual(result['requested_stage'], 'all')
        self.assertEqual(result['asset_ids'], [1])
        self.assertTrue(any(row.get('current_action') == 'generating_preview' for row in reports))
        with self.database.read_connection() as connection:
            snapshot = connection.execute('SELECT snapshot_path FROM asset WHERE id=1').fetchone()[0]
            self.assertIsNone(connection.execute('SELECT snapshot_path FROM asset WHERE id=2').fetchone()[0])
        self.assertTrue(Path(snapshot).is_relative_to(self.root / 'generated' / 'snapshots'))
        self.assertTrue(Path(snapshot).is_file())
        with patch('peach.frame_capture.capture_with_retry') as capture:
            second = self.run_processing(stage='previews')
        self.assertEqual(second['previews'], 0)
        self.assertEqual(second['status'], 'complete')
        capture.assert_not_called()

    def test_preview_only_leaves_candidates_and_sources_untouched(self):
        factory = Mock(side_effect=AssertionError('外部资料不属于预览任务'))
        with patch('peach.library_processing.FFmpegResolver', return_value=self.resolver), \
                patch('peach.frame_capture.capture_with_retry', return_value=True), \
                patch('peach.frame_capture.tile_frames', side_effect=self.make_tile), \
                patch('peach.library_processing._finish_collection') as finish:
            result = self.run_processing(stage='previews', provider_factory=factory)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['previews'], 1)
        self.assertFalse((self.root / 'candidates').exists())
        finish.assert_not_called()
        factory.assert_not_called()
        self.assertEqual([(step['id'], step['status']) for step in result['steps']],
                         [('sources', 'complete'), ('media', 'complete')])

    def test_missing_ffmpeg_is_retryable_and_collection_can_finish_its_work(self):
        self.resolver.ffmpeg.return_value = None
        with patch('peach.library_processing.FFmpegResolver', return_value=self.resolver), \
                patch('peach.library_processing._finish_collection', return_value={'auto_applied': 2}) as finish:
            result = self.run_processing(stage='collect', asset_ids=[1])
        finish.assert_called_once()
        self.assertEqual(result['auto_applied'], 2)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['retryable_asset_ids'], [1])
        issue = json.loads(Path(result['issues_log']).read_text(encoding='utf-8').splitlines()[0])
        self.assertEqual(issue['failed_action'], 'generating_preview')
        self.assertIn('FFmpeg', result['issue_preview'][0]['message'])

    def test_metadata_only_never_extracts_frames(self):
        with patch('peach.library_processing._generate_previews') as generate, \
                patch('peach.library_processing._finish_collection', return_value={}):
            result = self.run_processing(stage='metadata', asset_ids=[1])
        generate.assert_not_called()
        self.assertEqual(result['previews'], 0)
        self.assertEqual(result['status'], 'complete')

    def test_management_mode_keeps_collection_and_suppresses_automatic_previews(self):
        from peach.media_processing_policy import save
        save(self.config.directory('state'), True)
        with patch('peach.library_processing._generate_previews', side_effect=AssertionError('不应抽帧')), \
                patch('peach.library_processing._finish_collection', return_value={}) as finish:
            result = self.run_processing(stage='collect', asset_ids=[1])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['previews'], 0)
        finish.assert_called_once()
        with self.assertRaisesRegex(ValueError, '视频预览生成已关闭'):
            self.run_processing(stage='previews', asset_ids=[1])

    def test_local_preview_is_available_when_online_collection_starts(self):
        with self.database.write_transaction() as connection:
            connection.execute("UPDATE asset SET code='ABW-358' WHERE id=1")
        def collect(*_args, **_kwargs):
            with self.database.read_connection() as connection:
                path = connection.execute('SELECT snapshot_path FROM asset WHERE id=1').fetchone()[0]
            self.assertTrue(Path(path).is_file())
            return [], 0
        with patch('peach.library_processing.FFmpegResolver', return_value=self.resolver), \
                patch('peach.frame_capture.capture_with_retry', return_value=True), \
                patch('peach.frame_capture.tile_frames', side_effect=self.make_tile), \
                patch('peach.library_processing._RemoteSession.collect', side_effect=collect) as online, \
                patch('peach.library_processing._finish_collection', return_value={}):
            result = self.run_processing(stage='collect', asset_ids=[1])
        online.assert_called_once()
        self.assertEqual(result['previews'], 1)
        with self.database.read_connection() as connection:
            path = connection.execute('SELECT snapshot_path FROM asset WHERE id=1').fetchone()[0]
        self.assertTrue(Path(path).is_file())

    def test_failed_tiling_leaves_no_registered_or_partial_snapshot(self):
        import subprocess
        with patch('peach.library_processing.FFmpegResolver', return_value=self.resolver), \
                patch('peach.frame_capture.capture_with_retry', return_value=True), \
                patch('peach.frame_capture.tile_frames', side_effect=subprocess.TimeoutExpired('ffmpeg', 60)):
            result = self.run_processing(stage='previews')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['retryable_asset_ids'], [1])
        self.assertIn('超时', result['issue_preview'][0]['message'])
        self.assertFalse(list((self.root / 'generated').rglob('*.jpg')))
        with self.database.read_connection() as connection:
            self.assertIsNone(connection.execute('SELECT snapshot_path FROM asset WHERE id=1').fetchone()[0])

    def test_frame_failure_evidence_is_saved_and_included_in_the_support_zip(self):
        import io
        import zipfile
        from peach import support_bundle

        def fail(_ffmpeg, video, _timestamp, _destination, *, diagnostics):
            diagnostics.update(reason='ffmpeg_error', returncode=69, timestamp_seconds=2.4667,
                               timeout_seconds=45, attempts=[],
                               stderr=f'Error decoding {video}\nInvalid data token=private-token')
            return False

        with patch('peach.library_processing.FFmpegResolver', return_value=self.resolver), \
                patch('peach.frame_capture.capture_with_retry', side_effect=fail):
            result = self.run_processing(stage='previews', asset_ids=[1])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['retryable_asset_ids'], [1])
        issue = json.loads(Path(result['issues_log']).read_text(encoding='utf-8').splitlines()[0])
        detail = issue['diagnostics']
        self.assertEqual(detail['returncode'], 69)
        self.assertEqual(detail['frame_index'], 0)
        self.assertEqual(detail['duration_seconds'], 30)
        self.assertIn('Invalid data', detail['stderr'])
        self.assertNotIn(str(self.media), detail['stderr'])
        self.assertNotIn('private-token', detail['stderr'])
        self.assertIn('导出诊断报告', issue['message'])
        with patch.object(support_bundle.error_reporting, 'log_directories', return_value=[]):
            payload = support_bundle.bundle_bytes(config=self.config)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            evidence = json.loads(archive.read('processing/' + Path(result['issues_log']).name))
            state = json.loads(archive.read('processing/library-processing.json'))
        self.assertEqual(evidence['diagnostics'], detail)
        self.assertEqual(evidence['asset_id'], 1)
        self.assertEqual(evidence['title'], '<media>')
        self.assertNotIn(str(self.media), evidence['path'])
        self.assertEqual(state['retryable_asset_ids'], [1])
        with patch('peach.library_processing.FFmpegResolver', return_value=self.resolver), \
                patch('peach.frame_capture.capture_with_retry', return_value=True), \
                patch('peach.frame_capture.tile_frames', side_effect=self.make_tile):
            retried = self.run_processing(stage='previews', asset_ids=[1])
        self.assertEqual(retried['status'], 'complete')
        self.assertEqual(retried['issue_count'], 0)
        self.assertEqual(retried['retryable_asset_ids'], [])

    def test_selected_assets_require_active_videos(self):
        for selection in ([], [True], [0], [-1], ['1'], '1', [99], [3], [4]):
            with self.subTest(selection=selection), self.assertRaises(ValueError), \
                    patch('peach.library_processing.scan_location') as scan:
                self.run_processing(asset_ids=selection)
            scan.assert_not_called()
        with self.assertRaises(ValueError):
            self.run_processing(asset_ids=[1], retry_ids=[1])

    def test_stop_during_extraction_registers_no_snapshot(self):
        alive = [True]
        def capture(*_args, **_kwargs):
            alive[0] = False
            return True
        with patch('peach.library_processing.FFmpegResolver', return_value=self.resolver), \
                patch('peach.frame_capture.capture_with_retry', side_effect=capture), \
                self.assertRaises(InterruptedError):
            self.run_processing(stage='previews', active=lambda: alive[0])
        with self.database.read_connection() as connection:
            self.assertIsNone(connection.execute('SELECT snapshot_path FROM asset WHERE id=1').fetchone()[0])
        self.assertFalse(list((self.root / 'generated').rglob('*.jpg')))

    def test_web_request_preserves_preview_scope_when_retrying(self):
        from peach.web_library_processing import w_library_processing
        previous = {'job_id': 'old', 'status': 'failed', 'requested_stage': 'previews',
                    'retryable_asset_ids': [1]}
        job = SimpleNamespace(snapshot=lambda: None, update=lambda *_args, **_kwargs: None,
                              start=lambda work, restart, initial, recovery: (work('new'), initial)[1])
        contract = SimpleNamespace(db_path=self.db, database=self.database,
                                   candidate_root=self.root / 'candidates', cover_root=self.root / 'covers',
                                   library_processing_job=job, cache_bust=lambda: None)
        with patch('peach.web_library_processing.settings_file.active', return_value=self.config), \
                patch('peach.web_library_processing.snapshot', return_value=previous), \
                patch('peach.web_library_processing.process_library', return_value={'status': 'complete'}) as run:
            result = w_library_processing(contract, {'job_id': 'old', 'retry': []})
            self.assertEqual(result['requested_stage'], 'previews')
            self.assertEqual(run.call_args.kwargs['stage'], 'previews')
            self.assertEqual(run.call_args.kwargs['retry_ids'], [1])
            w_library_processing(contract, {'stage': 'collect', 'asset_ids': [1, 1]})
            self.assertEqual(run.call_args.kwargs['asset_ids'], [1])
            for selection in ([], None, [99], [True]):
                with self.subTest(selection=selection), self.assertRaises(ValueError):
                    w_library_processing(contract, {'asset_ids': selection})

    def test_preview_checkpoint_restarts_only_unfinished_movies(self):
        from peach import library_processing
        with self.database.write_transaction() as connection:
            video = self.media / 'clip-5.mp4'
            video.write_bytes(b'video')
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,duration) VALUES(5,'local',?,?, 'video',30)",
                (str(video), video.name))
        seen = []
        def capture(_config, row, _database, _resolver, *, progress):
            seen.append(row['id'])
            progress()
            if row['id'] == 5:
                raise InterruptedError('模拟服务退出')
            return True
        with patch.object(library_processing, '_generate_preview', side_effect=capture):
            with self.assertRaises(InterruptedError):
                self.run_processing(stage='previews', asset_ids=[1, 5])
        saved = snapshot(self.config)
        self.assertEqual(saved['completed_previews'], [f'1:{self.media / "clip-1.mp4"}'])
        seen.clear()
        with patch.object(library_processing, '_generate_preview',
                          side_effect=lambda _c, row, *_a, **_k: seen.append(row['id']) or True):
            result = self.run_processing(stage='previews', asset_ids=[1, 5], resume_state=saved)
        self.assertEqual(seen, [5])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['previews'], 2)

    def test_real_local_video_produces_a_snapshot_and_card_poster(self):
        import subprocess
        from peach.ffmpeg import FFmpegResolver
        from peach.previews import PreviewService
        from peach.repository import LedgerRepository
        resolver = FFmpegResolver(self.config.directory('tools') / 'ffmpeg')
        choice = resolver.ffmpeg()
        if choice is None or resolver.ffprobe() is None:
            self.skipTest('需要 FFmpeg 和 ffprobe')
        video = self.media / 'clip-1.mp4'
        subprocess.run([str(choice.path), '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc2=size=320x180:rate=10', '-t', '3', '-an',
                        '-c:v', 'mpeg4', str(video)], capture_output=True, check=True, timeout=30)
        with self.database.write_transaction() as connection:
            connection.execute('UPDATE asset SET duration=NULL WHERE id=1')
        result = self.run_processing(stage='previews')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['previews'], 1)
        with self.database.read_connection() as connection:
            row = connection.execute('SELECT snapshot_path,duration FROM asset WHERE id=1').fetchone()
        self.assertGreater(row['duration'], 0)
        with Image.open(row['snapshot_path']) as image:
            self.assertEqual(image.size, (1440, 810))
        generated = self.root / 'generated'
        service = PreviewService(LedgerRepository(self.database), resolver, generated / 'snapshots',
                                 generated / 'posters', generated / 'avatars', generated / 'logos')
        with Image.open(service.poster(1)) as image:
            self.assertEqual(image.size, (480, 270))
