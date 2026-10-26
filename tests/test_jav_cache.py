"""公开快照的发行边界、磁盘复用和并发原子读取。"""
from datetime import date, datetime, timezone
import tempfile
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch
from types import SimpleNamespace

from peach.jav_cache import DAY, PublicSnapshots, cache_fresh, cache_lifetime, stable_after
from peach.javdb_public import PublicAPI, PublicSourceUnavailable, public_cache_scope, cache_checked_at


def stamp(day):
    return datetime.fromisoformat(day + 'T12:00:00').replace(tzinfo=timezone.utc).timestamp()


class PublicationCacheTests(unittest.TestCase):
    def api(self, root, now):
        return PublicAPI(cache_root=root, wallclock=lambda: now[0], clock=lambda: now[0], sleeper=lambda _: None)

    def test_public_api_reuses_movie_comments_and_resources_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            now = [stamp('2026-10-13')]
            api = self.api(root, now)
            movie = {'id': 'Exact', 'number': 'EXM-001', 'release_date': '2020-01-01'}
            api._cached('movie:EXM-001', lambda: movie)
            api._rows = Mock(side_effect=[[{'id': 'review'}], [{'hash': 'resource'}]])
            self.assertEqual(api.reviews('EXM-001', 1), [{'id': 'review'}])
            self.assertEqual(api.magnets('EXM-001'), [{'hash': 'resource'}])
            now[0] = stamp('2030-01-01')
            reader = self.api(root, now)
            reader._rows = Mock(side_effect=AssertionError('缓存读取不得触发来源'))
            with public_cache_scope(root):
                self.assertEqual(reader.movie('EXM-001'), movie)
                self.assertEqual(reader.reviews('EXM-001', 1), [{'id': 'review'}])
                self.assertEqual(cache_checked_at('reviews:'), stamp('2026-10-13'))
                self.assertEqual(reader.magnets('EXM-001'), [{'hash': 'resource'}])
            reader._rows.assert_not_called()

    def test_daily_refresh_and_failed_manual_refresh_keep_good_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            now = [stamp('2026-10-13')]
            api = self.api(root, now)
            fetch = Mock(return_value=['first'])
            api._cached('reviews:Exact:1', fetch, released='2026-10-01')
            now[0] += DAY - 1
            api._cached('reviews:Exact:1', fetch, released='2026-10-01')
            self.assertEqual(fetch.call_count, 1)
            now[0] += 1
            fetch.return_value = ['second']
            api._cached('reviews:Exact:1', fetch, released='2026-10-01')
            self.assertEqual(fetch.call_count, 2)
            path = api._store().path('reviews:Exact:1')
            before = path.read_bytes()
            restarted = self.api(root, now)
            unavailable = Mock(side_effect=PublicSourceUnavailable('来源未取得'))
            with public_cache_scope(root) as reads:
                self.assertEqual(restarted._cached('reviews:Exact:1', unavailable, refresh=True), ['second'])
                self.assertEqual(restarted._cached('reviews:Exact:1', unavailable), ['second'])
                self.assertTrue(all(read['error'] for read in reads))
            self.assertEqual(unavailable.call_count, 1)
            self.assertEqual(path.read_bytes(), before)

    def test_independent_api_instances_merge_source_fetch_via_disk_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            now = [stamp('2026-10-13')]
            entered, release = Event(), Event()
            def fetch():
                entered.set()
                self.assertTrue(release.wait(3))
                return [{'id': 'review'}]
            other = Mock(side_effect=AssertionError('磁盘锁应合并来源抓取'))
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.api(root, now)._cached, 'reviews:Exact:1', fetch, released='2020-01-01')
                self.assertTrue(entered.wait(1))
                second = pool.submit(self.api(root, now)._cached, 'reviews:Exact:1', other, released='2020-01-01')
                release.set()
                self.assertEqual(first.result(timeout=3), second.result(timeout=3))
            other.assert_not_called()

    def test_uncached_media_renews_temporary_urls_and_keeps_static_metadata_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            now = [stamp('2026-10-13')]
            api = self.api(root, now)
            movie = {'id': 'Exact', 'number': 'EXM-001', 'release_date': '2020-01-01',
                     'cover_url': 'https://tp.spfcas.com/cover.jpg?token=temporary'}
            api._cached('movie:EXM-001', lambda: movie)
            api._rows = Mock(return_value=[{'id': 'Exact', 'number': 'EXM-001'}])
            api._json = Mock(return_value={'movie': {**movie, 'cover_url': 'https://tp.spfcas.com/current.jpg'}})
            now[0] += DAY
            self.assertEqual(api.movie('EXM-001'), movie)
            api._json.assert_not_called()
            self.assertEqual(api.media_movie('EXM-001')['cover_url'], 'https://tp.spfcas.com/current.jpg')
            self.assertEqual(api._json.call_count, 1)
            now[0] += 10 * DAY
            self.assertEqual(api.media_movie('EXM-001')['cover_url'], 'https://tp.spfcas.com/current.jpg')
            self.assertEqual(api._json.call_count, 1)

    def test_discovery_rebuilds_a_missing_projection_from_the_fixed_source_timestamp(self):
        from peach.jav_discovery_metadata import DiscoveryMetadata
        from peach.web_jav_discovery import _cached_movie
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            now = [stamp('2026-10-13')]
            movie = {'number': 'EXM-001', 'release_date': '2020-01-01', 'tags': [], 'score': 4, 'duration': 120}
            self.api(root / 'jav-public', now)._cached('movie:EXM-001', lambda: movie)
            now[0] += 10 * DAY
            reader = self.api(root / 'jav-public', now)
            metadata = DiscoveryMetadata(root / 'metadata.json', api=reader, clock=lambda: now[0])
            contract = SimpleNamespace(cover_root=root / 'covers')
            with patch('peach.javdb_public.PUBLIC_API', reader), patch('peach.web_jav_discovery.discovery_metadata', return_value=metadata):
                self.assertEqual(_cached_movie('EXM-001', contract), movie)
            self.assertEqual(metadata.peek('EXM-001')[1]['metadata_checked_at'], stamp('2026-10-13'))

    def test_month_end_and_leap_year_boundaries(self):
        self.assertEqual(stable_after('2024-01-31'), date(2024, 2, 29))
        self.assertEqual(stable_after('2026-01-31'), date(2026, 2, 28))
        self.assertEqual(stable_after('2026-12-31'), date(2027, 1, 31))
        self.assertEqual(cache_lifetime('2026-09-13', date(2026, 10, 13)), DAY)
        self.assertIsNone(cache_lifetime('2026-09-12', date(2026, 10, 13)))

    def test_new_and_unknown_release_recheck_after_one_day(self):
        saved = stamp('2026-10-12')
        for released in ('2026-10-01', '2026-11-01', None, '', 'unknown'):
            self.assertTrue(cache_fresh(saved, released, saved + DAY - 1))
            self.assertFalse(cache_fresh(saved, released, saved + DAY))

    def test_mature_work_requires_a_snapshot_after_the_release_month(self):
        self.assertFalse(cache_fresh(stamp('2026-09-20'), '2026-09-01', stamp('2026-10-13')))
        self.assertTrue(cache_fresh(stamp('2026-10-02'), '2026-09-01', stamp('2036-10-13')))
        self.assertFalse(cache_fresh(float('nan'), '2020-01-01', stamp('2026-10-13')))
        self.assertFalse(cache_fresh(stamp('2026-10-14'), '2020-01-01', stamp('2026-10-13')))

    def test_disk_snapshot_survives_reconstruction_and_keeps_its_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            writer = PublicSnapshots(root, clock=lambda: stamp('2026-10-13'))
            saved = writer.save('reviews:Exact:1', [{'id': '1', 'content': '来源评论'}], '2020-01-01')
            reader = PublicSnapshots(root, clock=lambda: stamp('2030-01-01'))
            restored = reader.load('reviews:Exact:1')
            self.assertEqual(restored, saved)
            self.assertTrue(restored.fresh(reader.clock()))
            self.assertEqual(reader.load('reviews:Other:1'), None)

    def test_corrupt_or_future_snapshots_are_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PublicSnapshots(Path(directory).resolve(), clock=lambda: 100)
            store.save('movie:EXM-001', {'id': 'Exact'}, None)
            store.path('movie:EXM-001').write_text('{', encoding='utf-8')
            self.assertIsNone(store.load('movie:EXM-001'))
            future = PublicSnapshots(Path(directory).resolve(), clock=lambda: 200)
            future.save('movie:EXM-001', {'id': 'Exact'}, None)
            self.assertIsNone(store.load('movie:EXM-001'))

    def test_unavailable_source_with_empty_message_still_obeys_failure_cooldown(self):
        api = PublicAPI()
        fetch = Mock(side_effect=PublicSourceUnavailable())
        for _ in range(2):
            with self.assertRaises(PublicSourceUnavailable):
                api._cached('reviews:Exact:1', fetch)
        self.assertEqual(fetch.call_count, 1)
