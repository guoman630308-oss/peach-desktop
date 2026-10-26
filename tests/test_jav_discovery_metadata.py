"""发现资料边车、固定批次、预算和失败状态的行为契约。"""
import tempfile
import json
import unittest
import threading
from pathlib import Path
from unittest.mock import patch

from peach.javdb_public import PublicAPI, PublicSourceUnavailable
from peach.jav_discovery_metadata import DiscoveryMetadata
from peach.web_jav_discovery import _metadata_response


class DiscoveryMetadataTests(unittest.TestCase):
    def test_restart_retains_batch_identity_budget_and_completed_codes(self):
        self.cache.remember('EXM-001', {'number': 'EXM-001', 'tags': [], 'score': 4, 'duration': 120})
        self.path.with_suffix('.job.json').write_text(json.dumps(dict(
            job_id='original', status='running', codes=['EXM-001', 'EXM-002'], refresh=True,
            completed=1, completed_codes=['EXM-001'], attempted_codes=['EXM-001'], requests=3, bytes=120,
            active_seconds=10)), encoding='utf-8')
        with patch.object(self.api, 'movie', return_value={'number': 'EXM-002', 'tags': [], 'score': 4, 'duration': 120}) as movie:
            restarted = DiscoveryMetadata(self.path, api=self.api, clock=lambda: self.now[0])
            self.addCleanup(restarted.stop)
            restarted.job.thread.join(5)
        final = restarted.job.snapshot()
        movie.assert_called_once_with('EXM-002', refresh=True)
        self.assertEqual((final['job_id'], final['completed'], final['requests'], final['bytes']), ('original', 2, 3, 120))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name).resolve() / "metadata.json"
        self.now = [1000.0]
        self.api = PublicAPI(sleeper=lambda _: None)
        self.cache = DiscoveryMetadata(self.path, api=self.api, clock=lambda: self.now[0])
        self.addCleanup(self.cache.stop)

    def test_repeated_restart_keeps_checkpoint_before_the_next_item_finishes(self):
        self.path.with_suffix('.job.json').write_text(json.dumps(dict(
            job_id='original', status='running', codes=['EXM-001', 'EXM-002'], refresh=True,
            started_at=100, completed=1, completed_codes=['EXM-001'], attempted_codes=['EXM-001'],
            failed_codes=[], requests=3, bytes=120, active_seconds=10)), encoding='utf-8')
        with patch.object(DiscoveryMetadata, '_run_batch'):
            first = DiscoveryMetadata(self.path, api=self.api, clock=lambda: self.now[0])
            first.job.thread.join(5)
            first.stop()
        saved = json.loads(self.path.with_suffix('.job.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['completed_codes'], ['EXM-001'])
        self.assertEqual(saved['started_at'], 100)
        with patch.object(self.api, 'movie', return_value={'number': 'EXM-002', 'tags': [], 'score': 4, 'duration': 120}) as movie:
            second = DiscoveryMetadata(self.path, api=self.api, clock=lambda: self.now[0])
            self.addCleanup(second.stop)
            second.job.thread.join(5)
        movie.assert_called_once_with('EXM-002', refresh=True)
        self.assertEqual(second.job.snapshot()['completed'], 2)

    def test_sidecar_survives_restart_and_distinguishes_empty_unknown_and_stale(self):
        movie = {"number": "EXM-001", "tags": [], "score": "4.23", "duration": 180,
                 "preview_video_url": "https://example.invalid/secret",
                 "actors": [{"name": "甲", "gender": 0, "avatar_url": "secret"}]}
        self.cache.remember("EXM-001", movie)
        restarted = DiscoveryMetadata(self.path, clock=lambda: self.now[0])
        source, state = restarted.peek("EXM001")
        self.assertEqual(state["metadata_state"], "ready")
        self.assertEqual(state["metadata_fields"]["tags"], "empty")
        self.assertNotIn("preview_video_url", source)
        self.assertNotIn("avatar_url", source["actors"][0])
        self.assertEqual(restarted.peek("EXM-002")[1]["metadata_fields"]["tags"], "unavailable")
        self.now[0] += 86400
        self.assertEqual(restarted.peek("EXM-001")[1]["metadata_state"], "stale")
        self.cache.remember("EXM-002", error="资料未取得")
        self.assertEqual(self.cache.peek("EXM-002")[1]["metadata_state"], "unavailable")

    def test_fixed_batch_finishes_without_detail_open_or_polling_new_batch(self):
        def movie_for(code, **_):
            return {"number": code, "tags": [{"name": "制服"}], "score": 4, "duration": 120}
        with patch.object(self.api, "movie", side_effect=movie_for) as movie:
            self.cache.ensure([f"EXM-{index:03d}" for index in range(1, 105)])
            self.cache.job.thread.join(5)
            state = self.cache.job.snapshot()
            self.assertEqual((state["status"], state["completed"], state["total"]), ("complete", 100, 100))
            self.cache.ensure(["EXM-999"])
            self.assertEqual(movie.call_count, 100)
            self.assertEqual(self.cache.peek("EXM-001")[1]["metadata_state"], "ready")
            self.cache.ensure(["EXM-001", "EXM-999"], refresh=True, job_id=state["job_id"])
            self.cache.job.thread.join(5)
            self.assertEqual(movie.call_count, 102)

    def test_static_portraits_survive_restart_without_signed_addresses(self):
        self.cache.remember('EXM-001', {'actors': [
            {'id': 'Static', 'name': '甲', 'avatar_url': 'https://tp.spfcas.com/actor.jpg'},
            {'id': 'Signed', 'name': '乙', 'avatar_url': 'https://tp.spfcas.com/actor.jpg?signature=example'},
            {'id': 'Private', 'name': '丙', 'avatar_url': 'http://127.0.0.1/private'}]})
        source, _ = DiscoveryMetadata(self.path).peek('EXM-001')
        actors = source['actors']
        self.assertEqual(actors[0]['avatar_image'], 'https://tp.spfcas.com/actor.jpg')
        self.assertNotIn('avatar_image', actors[1])
        self.assertTrue(actors[1]['has_avatar'])
        self.assertFalse(actors[2]['has_avatar'])
        self.assertNotIn('signature', self.path.read_text(encoding='utf-8'))

    def test_source_cooldown_stops_batch_and_preserves_failure(self):
        def unavailable(code):
            self.api.paused_until = self.api.clock() + 900
            raise PublicSourceUnavailable("资料未取得")
        with patch.object(self.api, "movie", side_effect=unavailable) as movie:
            self.cache.ensure(["EXM-001", "EXM-002"])
            self.cache.job.thread.join(5)
            state = self.cache.job.snapshot()
            self.assertEqual(state["status"], "stopped")
            self.assertEqual(state["stop_reason"], "source_cooldown")
            self.assertEqual(movie.call_count, 1)
            self.assertEqual(self.cache.peek("EXM-001")[1]["metadata_state"], "unavailable")

    def test_cancel_preserves_completed_records_and_requires_explicit_resume(self):
        entered, release = threading.Event(), threading.Event()
        def movie_for(code, **_):
            entered.set()
            release.wait(5)
            return {"number": code, "tags": [], "score": 4, "duration": 120}
        with patch.object(self.api, "movie", side_effect=movie_for) as movie:
            self.cache.ensure(["EXM-001", "EXM-002"])
            self.assertTrue(entered.wait(5))
            thread = self.cache.job.thread
            stopped = self.cache.cancel()
            release.set()
            thread.join(5)
            self.assertEqual(stopped["stop_reason"], "cancelled")
            self.assertEqual(self.cache.peek("EXM-001")[1]["metadata_state"], "ready")
            self.cache.ensure(["EXM-001", "EXM-002"])
            self.assertEqual(movie.call_count, 1)
            self.cache.ensure(["EXM-001", "EXM-002"], refresh=True)
            self.cache.job.thread.join(5)
            self.assertEqual(movie.call_count, 3)

    def test_finished_collection_rechecks_expired_new_work_and_keeps_mature_work(self):
        self.now[0] = 1791892800.0
        def movie_for(code):
            return {'number': code, 'release_date': '2026-10-01' if code == 'EXM-001' else '2020-01-01',
                    'tags': [], 'score': 4, 'duration': 120}
        with patch.object(self.api, 'movie', side_effect=movie_for) as movie:
            self.cache.ensure(['EXM-001', 'EXM-002'])
            self.cache.job.thread.join(5)
            saved = self.cache.peek('EXM-002')[1]['metadata_checked_at']
            self.cache.ensure(['EXM-999'])
            self.assertEqual(movie.call_count, 2)
            self.now[0] += 86400
            self.cache.ensure(['EXM-999'])
            self.cache.job.thread.join(5)
            self.assertEqual(movie.call_count, 3)
            self.assertEqual(movie.call_args.args, ('EXM-001',))
            self.assertEqual(self.cache.peek('EXM-002')[1]['metadata_checked_at'], saved)

    def test_source_cache_read_and_failed_refresh_do_not_renew_source_timestamp(self):
        source = {'number': 'EXM-001', 'release_date': '2020-01-01', 'tags': [], 'score': 4, 'duration': 120}
        self.cache.remember('EXM-001', source, checked_at=100)
        self.now[0] = 2000
        self.cache.remember('EXM-001', source, checked_at=100)
        self.cache.remember('EXM-001', error='来源未取得')
        self.assertEqual(self.cache.peek('EXM-001')[1]['metadata_checked_at'], 100)

    def test_public_job_status_excludes_internal_errors_and_private_codes(self):
        failure = {"job_id": "sample", "status": "failed", "error": "RuntimeError: https://example.invalid/?private=sample",
                   "codes": ["EXM-001"], "completed": 0, "total": 1, "stop_reason": ""}
        with patch("peach.web_jav_discovery.discovery_metadata") as metadata:
            metadata.return_value.ensure.return_value = failure
            response = _metadata_response(None, {}, [], [], 60, "external")
        self.assertEqual(response["metadata_job"]["message"], "作品资料未取得")
        self.assertEqual(response["metadata_job"]["error"], "作品资料未取得")
        self.assertNotIn("codes", response["metadata_job"])
        self.assertNotIn("poll_after_ms", response)
