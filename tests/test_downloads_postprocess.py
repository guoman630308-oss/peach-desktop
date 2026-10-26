"""下载后处理在独立账本和临时媒体上的失败与重试。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from peach import download_postprocess as post
from peach.downloads import DownloadStore, DownloadTask, INGESTED, POSTPROCESS_PENDING
from peach.repository import LedgerDatabase
from support.ledger import fresh_ledger


class DownloadPostprocessTests(unittest.TestCase):
    def test_isolated_ads_record_the_junk_reason(self):
        self.contract.download_junk_candidates = lambda: [{"id": self.asset_id}]
        self.assertEqual(post._isolate_ads(self.contract, -1, [self.asset_id]), 1)
        with self.database.read_connection() as connection:
            self.assertEqual(tuple(connection.execute("SELECT disposal,disposal_reason FROM asset WHERE id=?",
                                                      (self.asset_id,)).fetchone()), ("trash", "junk"))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.database = LedgerDatabase(fresh_ledger(self.root))
        self.source = self.root / 'incoming' / 'ABC-123.mp4'
        self.target = self.root / 'organized' / self.source.name
        self.source.parent.mkdir()
        self.source.write_bytes(b'main video')
        with self.database.write_transaction() as connection:
            self.asset_id = connection.execute(
                'INSERT INTO asset(location,path,name,medium,size,code) VALUES(?,?,?,?,?,?)',
                ('115', str(self.source), self.source.name, 'video', 10, 'ABC-123')).lastrowid
        self.store = DownloadStore(self.database)
        self.task = self.store.save(DownloadTask(
            id=0, info_hash='a' * 40, provider='115', source_uri='', display_name='ABC-123',
            target='/115/incoming', state=INGESTED, code='ABC-123',
            ledger_path=str(self.source.parent), asset_id=self.asset_id,
            postprocess_state=POSTPROCESS_PENDING))
        self.contract = SimpleNamespace(
            database=self.database, db_path=self.database.db_path,
            candidate_root=self.root / 'generated', download_junk_candidates=lambda: [],
            download_organize_templates=lambda: {'115': {'file': '{code}'}}, cache_bust=lambda: None)
        self.handle = SimpleNamespace(progress=lambda **_: None)
        plan = {'rows': [{'asset_id': self.asset_id, 'action': 'move',
                         'current_path': str(self.source), 'target_path': str(self.target)}], 'reasons': {}}
        self.plan = patch.object(post.organize, 'build_plan', return_value=plan)
        self.plan.start()
        self.addCleanup(self.plan.stop)
        collect = patch.object(post, '_collect', return_value={'candidates': 0})
        collect.start()
        self.addCleanup(collect.stop)

    def run_task(self):
        return post.run(self.contract, post.followup_key(self.task.id), self.handle)

    def test_existing_unrelated_foreign_key_violation_is_reported_without_failing_the_move(self):
        with self.database.write_transaction() as connection:
            connection.execute("INSERT INTO media_binding(asset_id,backend,external_id) VALUES(99999,'test','x')")
        result = self.run_task()
        self.assertEqual((result['moved'], result['existing_foreign_keys']), (1, 1))
        self.assertEqual(self.store.get(self.task.id).postprocess_state, 'complete')
        self.assertTrue(self.target.exists())

    def test_retry_uses_the_bound_asset_after_a_move(self):
        post._organize(self.contract, self.task, self.asset_id)
        main, path, _ = post._bind_main(self.contract, self.store.get(self.task.id))
        self.assertEqual((main, path), (self.asset_id, str(self.target)))

    def test_occupied_target_is_a_retryable_failure(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b'existing file')
        with self.assertRaisesRegex(RuntimeError, '整理'):
            self.run_task()
        self.assertEqual(self.store.get(self.task.id).postprocess_state, 'failed')
        self.assertTrue(self.source.exists())
        self.assertEqual(self.target.read_bytes(), b'existing file')

    def test_new_foreign_key_violation_is_rejected(self):
        original = post.organize.apply_plan
        def corrupt(connection, rows, **options):
            result = original(connection, rows, **options)
            connection.execute("INSERT INTO media_binding(asset_id,backend,external_id) VALUES(99999,'test','x')")
            connection.commit()
            return result
        with patch.object(post.organize, 'apply_plan', side_effect=corrupt), \
                self.assertRaisesRegex(RuntimeError, '校验'):
            self.run_task()
        self.assertEqual(self.store.get(self.task.id).postprocess_state, 'failed')

    def test_a_collision_found_in_the_plan_is_a_failure(self):
        with patch.object(post.organize, 'build_plan', return_value={
                'rows': [{'action': 'skip', 'reason': post.organize.SKIP_TARGET_EXISTS}],
                'reasons': {post.organize.SKIP_TARGET_EXISTS: 1}}), \
                self.assertRaisesRegex(RuntimeError, '整理'):
            self.run_task()
        self.assertTrue(self.source.exists())
