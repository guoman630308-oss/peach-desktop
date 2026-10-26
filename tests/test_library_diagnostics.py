"""库健康只读投影的计数、失败口径、来源证据和脱敏回归。"""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from peach import library_diagnostics as diagnostics
from peach.config import PeachSettings
from peach.migrations import upgrade
from peach.settings_file import PeachConfig


class LibraryDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db = self.root / 'ledger.db'
        upgrade(self.db, Path(__file__).resolve().parents[1] / 'migrations')
        self.config = PeachConfig(self.root, self.root / 'config.toml')
        self.settings = PeachSettings(db_path=self.db, cover_root=self.root / 'covers', poster_root=self.root / 'posters')
        for name in ('state', 'covers', 'posters'):
            (self.root / name).mkdir()
        with closing(sqlite3.connect(self.db)) as connection, connection:
            for asset_id, code, duration, disposal in ((1, 'ABC-123', 120, None), (2, None, 0, None),
                                                      (3, 'DEF-456', None, None), (4, None, None, 'vanished')):
                connection.execute('INSERT INTO asset(id,location,path,name,medium,code,duration,disposal) '
                                   "VALUES(?,'local',?,'private-name','video',?,?,?)",
                                   (asset_id, f'R:/private/{asset_id}', code, duration, disposal))

    def log(self, records):
        (self.root / 'state/library-processing.json').write_text(json.dumps(
            {'job_id': 'safe-job', 'issue_count': len(records), 'started_at': 1_700_000_000}), encoding='utf-8')
        path = self.root / 'state/library-processing-safe-job.issues.jsonl'
        path.write_text('\n'.join(json.dumps(row) for row in records), encoding='utf-8')
        return path

    def test_cover_duration_and_identification_counts_preserve_uncoded_creator_works(self):
        (self.root / 'covers/abc-123.jpg').write_bytes(b'image')
        (self.root / 'posters/2_4.jpg').write_bytes(b'image')
        self.log([{'asset_id': 1, 'severity': 'error', 'failed_action': 'reading_local', 'message': 'NFO parse error'},
                  {'asset_id': 2, 'severity': 'error', 'failed_action': 'reading_local', 'message': '媒体文件不可访问'},
                  {'asset_id': 3, 'severity': 'error', 'failed_action': 'querying_metadata', 'message': 'private-cookie'},
                  {'asset_id': 1, 'severity': 'paused', 'failed_action': 'querying_metadata'},
                  {'asset_id': 2, 'severity': 'info', 'failed_action': 'querying_metadata'}])
        before = self.db.read_bytes()
        result = diagnostics.library(self.settings, self.config)
        groups = result['groups']
        self.assertEqual({key: value['count'] for key, value in groups.items()},
                         {'identification': 1, 'metadata': 1, 'cover': 1, 'duration': 2})
        self.assertEqual(groups['duration']['items'][0], {
            'id': 2, 'code': None, 'name': 'private-name', 'title': 'private-name',
            'location': 'local', 'path': 'R:/private/2', 'duration': 0, 'size': None})
        self.assertEqual([row['id'] for row in groups['cover']['items']], [3])
        self.assertEqual(groups['cover']['items'][0]['code'], 'DEF-456')
        self.assertIsNotNone(result['issue_at'])
        self.assertNotIn('private-cookie', json.dumps(result))
        self.assertEqual(before, self.db.read_bytes())

    def test_library_items_expose_local_identity_and_keep_title_and_filename_distinct(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("UPDATE asset SET catalog_title='作品标题',name='未识别文件.mp4',"
                               "path='R:\\Media\\未识别文件.mp4',size=1048576 WHERE id=2")
        before = self.db.read_bytes()
        row = diagnostics.library(self.settings, self.config)['groups']['duration']['items'][0]
        self.assertEqual(row['title'], '作品标题')
        self.assertEqual(row['name'], '未识别文件.mp4')
        self.assertEqual(row['path'], 'R:\\Media\\未识别文件.mp4')
        self.assertIsNone(row['code'])
        self.assertEqual(row['size'], 1048576)
        self.assertEqual(before, self.db.read_bytes())

    def test_missing_database_and_broken_or_oversized_logs_are_unknown(self):
        self.db.unlink()
        self.assertTrue(all(row['count'] is None for row in diagnostics.library(self.settings, self.config)['groups'].values()))
        self.assertFalse(self.db.exists())
        path = self.log([])
        path.write_text('{unfinished', encoding='utf-8')
        self.assertIsNone(diagnostics.failures(self.config)[0])
        with patch.object(diagnostics, 'MAX_LOG_BYTES', 1):
            self.assertIsNone(diagnostics.failures(self.config)[0])

    def test_job_paths_are_validated_and_missing_problem_logs_are_unknown(self):
        path = self.log([{'asset_id': 1}])
        path.unlink()
        self.assertIsNone(diagnostics.failures(self.config)[0])
        (self.root / 'state/library-processing.json').write_text('{"job_id":"../secret"}', encoding='utf-8')
        self.assertIsNone(diagnostics.failures(self.config)[0])

    def test_large_lists_are_bounded_and_keep_the_total(self):
        result = diagnostics.group('缺时长', [{'id': i, 'code': None} for i in range(1, 601)])
        self.assertEqual(result['count'], 600)
        self.assertEqual(len(result['items']), 500)
        self.assertTrue(result['truncated'])

    def test_sources_require_parsed_profile_provenance_and_show_cooldown(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO performer_profile(entity_id,source,source_url,fetched_at) "
                               "VALUES(1,'auto:performer-profile@42','https://www.minnano-av.com/actress1.html','2023-11-14T22:13:20Z')")
            connection.execute("INSERT INTO entity_external_ref VALUES(1,'javdb','performer','a',?,?)",
                               ('{"source":"manual"}', '2023-11-14T22:13:20Z'))
        with patch.object(diagnostics, 'cooldown_state', return_value=(0, 0)):
            result = diagnostics.sources(self.settings, self.config, now=1_700_000_100)
        self.assertEqual(result[0]['status'], 'ok')
        self.assertEqual(result[1]['status'], 'unchecked')
        self.assertIsNone(result[1]['last_success_at'])
        with patch.object(diagnostics, 'cooldown_state', return_value=(1_700_000_200, 1)):
            cooling = diagnostics.sources(self.settings, self.config, now=1_700_000_100)
        self.assertEqual(cooling[0]['status'], 'warning')
        self.assertIsNotNone(cooling[0]['cooldown_until'])
        self.assertNotIn('actress1', json.dumps(cooling))

    def test_cover_inventory_permission_errors_are_unknown(self):
        with patch.object(diagnostics.os, 'scandir', side_effect=PermissionError):
            self.assertIsNone(diagnostics.inventory(self.root))

    def test_source_history_and_absent_checks_have_explicit_states(self):
        from peach.scraping_access import record_source_check
        root = self.config.directory('secrets')
        record_source_check(root, 'airav', 'metadata', {
            'ok': True, 'kind': 'ok', 'reason': 'parsed', 'checked_at': 1_700_000_000})
        rows = {row['source']: row for row in diagnostics.sources(self.settings, self.config, now=1_700_172_800)}
        self.assertEqual(rows['airav']['status'], 'stale')
        self.assertIn('解析成功', rows['airav']['reason'])
        self.assertIsNotNone(rows['airav']['last_success_at'])
        self.assertEqual(rows['avsox']['status'], 'unchecked')
        self.assertIn('尚无', rows['avsox']['reason'])
        self.assertNotIn('fc2club', rows)
        self.assertFalse({'makers', 'prestige', 'faleno', 'dahlia'} & rows.keys())
        self.assertTrue({'mgstage', 'dmm', 'fc2'} <= rows.keys())

    def test_connection_and_parsing_evidence_remain_distinct_for_all_bridge_sources(self):
        from peach.scraping_access import record_source_check
        root = self.config.directory('secrets')
        record_source_check(root, 'airav', 'connection', {
            'ok': True, 'kind': 'ok', 'reason': 'ok', 'checked_at': 1_700_000_000, 'elapsed_s': 1.2})
        record_source_check(root, 'fc2club', 'metadata', {
            'ok': True, 'kind': 'ok', 'reason': 'parsed', 'checked_at': 1_700_000_000, 'elapsed_s': 2.1})
        rows = {row['source']: row for row in diagnostics.sources(self.settings, self.config, now=1_700_000_100)}
        self.assertEqual(rows['airav']['status'], 'unknown')
        self.assertIn('解析状态未取得', rows['airav']['reason'])
        self.assertEqual(rows['fc2club']['status'], 'ok')
        self.assertIsNotNone(rows['fc2club']['last_success_at'])
        self.assertEqual(rows['airav']['elapsed_s'], 1.2)
        record_source_check(root, 'fc2club', 'metadata', {
            'ok': False, 'kind': 'auth', 'reason': 'geo_restricted', 'checked_at': 1_700_000_100})
        rows = {row['source']: row for row in diagnostics.sources(self.settings, self.config, now=1_700_000_101)}
        self.assertEqual(rows['fc2club']['status'], 'warning')
        self.assertIn('地区', rows['fc2club']['reason'])
        self.assertIsNotNone(rows['fc2club']['last_success_at'])

    def test_source_times_are_normalized_and_corrupt_fields_are_ignored(self):
        root = self.config.directory('secrets')
        root.mkdir(parents=True, exist_ok=True)
        (root / 'source-check-airav.json').write_text(json.dumps({
            'metadata': {'ok': True, 'reason': 'parsed', 'checked_at': '2023-11-14T22:13:20Z',
                         'last_success_at': '2023-11-14T22:13:20Z', 'elapsed_s': '1.2'},
            'connection': {'ok': False, 'reason': {}, 'checked_at': 1_700_000_050, 'elapsed_s': 'broken'},
        }), encoding='utf-8')
        rows = {row['source']: row for row in diagnostics.sources(self.settings, self.config, now=1_700_000_100)}
        self.assertEqual(rows['airav']['status'], 'warning')
        self.assertIsNotNone(rows['airav']['last_success_at'])
        self.assertIsNone(rows['airav']['elapsed_s'])
