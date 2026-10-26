"""群体研究的同意、只读投影、周期限制和独立传输回归。"""
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from peach import library_sharing as sharing
from peach.repository import LedgerDatabase
from peach.settings_file import PeachConfig
from support.ledger import fresh_ledger

DSN = 'https://public@o123.ingest.us.sentry.io/789'


class LibrarySharingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch('peach.remote_reporting._disabled', False))
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        self.config = PeachConfig(self.root, self.root / 'config.toml')
        self.database = LedgerDatabase(fresh_ledger(self.root))
        self.contract = SimpleNamespace(read_connection=self.database.read_connection,
                                        taste_history_store=self.root / 'taste/history.sqlite')
        self.service = sharing.Service(self.config, self.contract)
        self.addCleanup(self.service.stop)
        patcher = mock.patch.dict(os.environ, {'PEACH_SENTRY_DSN': '', 'PEACH_LIBRARY_SENTRY_DSN': DSN})
        patcher.start()
        self.addCleanup(patcher.stop)

    def enable(self):
        preview = self.service.preview()
        return self.service.save({'enabled': True, 'schema': sharing.SCHEMA,
                                  'consent': True, 'preview_token': preview['token']})

    def test_disabled_and_missing_endpoint_do_not_read_library_or_construct_sdk(self):
        with mock.patch.object(sharing, 'collect') as collect, \
                mock.patch('peach.remote_reporting._new_client') as client:
            self.service.attempt()
            collect.assert_not_called()
            client.assert_not_called()
        self.enable()
        with mock.patch.object(sharing, 'target', return_value=''), mock.patch.object(sharing, 'collect') as collect:
            self.service.attempt()
            collect.assert_not_called()

    def test_enable_requires_preview_explicit_consent_and_current_schema(self):
        for body in ({'enabled': True}, {'enabled': 'true'}, {'enabled': True, 'consent': True, 'schema': sharing.SCHEMA}):
            with self.assertRaises(ValueError):
                self.service.save(body)
        preview = self.service.preview()
        with self.assertRaises(ValueError):
            self.service.save({'enabled': True, 'schema': sharing.SCHEMA, 'preview_token': preview['token']})
        self.assertTrue(self.enable()['enabled'])
        self.assertFalse(self.service.save({'enabled': False})['enabled'])
        with mock.patch.object(sharing, 'SCHEMA', sharing.SCHEMA + 1):
            self.assertFalse(sharing.snapshot(self.config)['enabled'])

    def test_preview_expires_and_cannot_be_reused(self):
        preview = self.service.preview()
        body = {'enabled': True, 'consent': True, 'schema': sharing.SCHEMA, 'preview_token': preview['token']}
        with mock.patch.object(sharing.time, 'monotonic', return_value=10**20), self.assertRaises(ValueError):
            self.service.save(body)
        self.service.save(body)
        with self.assertRaises(ValueError):
            self.service.save(body)

    def test_work_signals_are_deduplicated_and_collection_is_read_only(self):
        with self.database.write_transaction() as connection:
            for code, path in (('EXM-001', 'R:/private-person/movie-a.mp4'), ('EXM-001', 'R:/private-person/movie-b.mp4')):
                connection.execute("INSERT INTO asset(location,path,name,code,medium,rating,play_count,watch_ratio,duration) "
                                   "VALUES('local',?,'private-title',?,'video',80,2,.5,600)", (path, code))
            connection.execute("INSERT INTO asset_tag(asset_id,tag,source) VALUES(1,'手动标签','web-user')")
            connection.execute("INSERT INTO asset_tag(asset_id,tag,source) VALUES(2,'720P','metadata')")
            connection.execute("INSERT INTO asset_tag(asset_id,tag,source) VALUES(1,'已隐藏标签','web-user')")
            connection.execute("INSERT INTO asset_tag_preference(profile_id,asset_id,normalized_tag,hidden,updated_at) "
                               "VALUES('local-default',1,'已隐藏标签',1,'now')")
            connection.execute("INSERT INTO asset_preference(profile_id,asset_id,liked,updated_at) VALUES('local-default',1,1,'2026-10-18')")
        before = self.database.db_path.read_bytes()
        payload, data = sharing.collect(self.contract)
        self.assertEqual(self.database.db_path.read_bytes(), before)
        self.assertFalse((self.contract.taste_history_store.parent / 'preference-cache.json').exists())
        self.assertFalse((self.contract.taste_history_store.parent / 'browser-preference-cache.json').exists())
        self.assertEqual(set(payload), {'schema', 'peach', 'period', 'library', 'preferences', 'coverage'})
        self.assertEqual(len(payload['library']), 1)
        work = payload['library'][0]
        self.assertEqual(work['code'], 'EXM-001')
        self.assertEqual(work['manual_tags'], ['手动标签'])
        self.assertEqual(work['rating'], 80)
        self.assertTrue(work['liked'])
        self.assertEqual(work['play_count'], 4)
        self.assertEqual(work['watch_ratio'], .5)
        self.assertEqual(payload['coverage'], {'active_files': 2, 'coded_files': 2, 'coded_works': 1, 'uncoded_items': 0})
        self.assertEqual(payload['preferences']['tag'][0]['works']['collected'], 1)
        self.assertEqual(payload['preferences']['tag'][0]['works']['ratings'], {'80.0': 1})
        self.assertTrue(sharing.valid_payload(payload))
        for private in ('private-person', 'private-title', 'movie-a', 'movie-b', 'asset_id', 'visited_at', 'last_played',
                        'profile_id', 'fingerprint', 'R:/', '720P', '已隐藏标签', 'duration_seconds', 'entities'):
            self.assertNotIn(private, data.decode())

    def test_entity_distributions_include_uncoded_signals_and_explicit_favorites(self):
        from peach.entities import upsert_asset_entity
        with self.database.write_transaction() as connection:
            for code in ('EXM-001', 'EXM-001', None):
                connection.execute("INSERT INTO asset(location,path,name,code,medium,rating,play_count,duration,release_date) "
                                   "VALUES('local',?,'title',?,'video',60,1,2400,'2021-01-01')", (str(code) + str(connection.total_changes), code))
            for aid in (1, 2, 3):
                for kind in ('performer', 'creator', 'studio', 'series'):
                    upsert_asset_entity(connection, asset_id=aid, kind=kind, role=kind, name='名称-' + kind, source='test', confidence=1)
            eid = connection.execute("SELECT id FROM entity WHERE kind='performer'").fetchone()[0]
            connection.execute("UPDATE profile SET settings_json=? WHERE id='local-default'",
                               (json.dumps({'libraryShortcuts': {'favorites': [{'entity_id': eid}]}}),))
            connection.execute("UPDATE asset SET feedback='dislike',feedback_at=5,rating=100 WHERE id=2")
            connection.execute("UPDATE asset SET disposal='trash' WHERE id=3")
            connection.execute("INSERT INTO asset(location,path,name,medium,rating) VALUES('local','uncoded','title','image',20)")
            upsert_asset_entity(connection, asset_id=4, kind='performer', role='performer', name='名称-performer', source='test', confidence=1)
            connection.execute("INSERT INTO watch_queue(profile_id,asset_id,added_at,source) VALUES('local-default',1,'now','web')")
        payload, _ = sharing.collect(self.contract)
        self.assertEqual(payload['coverage'], {'active_files': 3, 'coded_files': 2, 'coded_works': 1, 'uncoded_items': 1})
        self.assertEqual(payload['library'], [{'code': 'EXM-001', 'rating': 80, 'watch_later': True,
                                              'play_count': 2, 'feedback': 'dislike'}])
        performer = payload['preferences']['performer'][0]
        self.assertEqual(performer, {'name': '名称-performer',
            'works': {'collected': 1, 'liked': 0, 'disliked': 1, 'watched': 1, 'ratings': {'80.0': 1}},
            'uncoded': {'collected': 1, 'liked': 0, 'disliked': 0, 'watched': 0, 'ratings': {'20.0': 1}}, 'favorite': True})
        self.assertEqual(payload['preferences']['era'][0]['name'], '2020')
        self.assertEqual(payload['preferences']['length'][0]['name'], '1')
        self.assertEqual(payload['preferences']['tag'], [])
        self.assertTrue(sharing.valid_payload(payload))

    def test_large_population_projection_uses_lossless_compression(self):
        count = 12000
        with self.database.write_transaction() as connection:
            connection.executemany("INSERT INTO asset(location,path,name,code) VALUES('local',?,'title',?)",
                                   ((str(i), f'EXM-{i:05}') for i in range(count)))
        payload, data = sharing.collect(self.contract)
        wire = sharing.compressed(data)
        self.assertLess(len(wire), sharing.MAX_BYTES)
        self.assertEqual(gzip.decompress(wire), data)
        self.assertEqual(len(json.loads(gzip.decompress(wire))['library']), count)
        self.assertTrue(sharing.valid_payload(payload))
        self.assertEqual(wire[3:8], bytes(5), 'gzip 不含文件名或生成时间')

    def test_research_project_must_be_separate_from_diagnostics(self):
        with mock.patch.dict(os.environ, {'PEACH_SENTRY_DSN': DSN}):
            self.assertEqual(sharing.target(), '')

    def test_schema_three_consent_does_not_authorize_population_projection(self):
        self.enable()
        path = self.config.directory('state') / 'library-sharing.json'
        state = json.loads(path.read_text())
        path.write_text(json.dumps({**state, 'schema': 3}))
        with mock.patch.object(sharing, 'collect') as collect:
            self.service.attempt()
            collect.assert_not_called()
        self.assertFalse(sharing.snapshot(self.config)['enabled'])

    def test_transport_rejects_invalid_signal_and_distribution_values(self):
        from copy import deepcopy
        with self.database.write_transaction() as connection:
            connection.execute("INSERT INTO asset(location,path,name,code,rating) VALUES('local','a','title','EXM-001',80)")
        payload, _ = sharing.collect(self.contract)
        self.assertTrue(sharing.valid_payload(payload))
        for key, invalid in [('rating', float('nan')), ('rating', 101), ('rating', True),
                             ('liked', 'yes'), ('manual_tags', ['R:/private']), ('play_count', -1),
                             ('watch_ratio', 1.1), ('entities', {'performer': ['name']}), ('tags', ['720P'])]:
            altered = deepcopy(payload)
            altered['library'][0][key] = invalid
            self.assertFalse(sharing.valid_payload(altered), (key, invalid))
        altered = deepcopy(payload)
        altered['preferences']['tag'] = [{'name': 'tag', 'works': {'collected': 1, 'liked': 0, 'disliked': 0,
                                         'watched': 0, 'ratings': {'80': 2}}}]
        self.assertFalse(sharing.valid_payload(altered))

    def test_installer_saves_explicit_selection_without_reading_or_sending(self):
        import runpy
        entry = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/build_app_entry.py'))
        with mock.patch('peach.settings_file.active', return_value=self.config), \
                mock.patch.object(sharing, 'collect') as collect, \
                mock.patch('peach.remote_reporting._new_client') as client, \
                mock.patch('peach.remote_reporting._disabled', False):
            for argument in ('1', '0'):
                self.assertEqual(entry['main'](['Peach.exe', '--installer-library-sharing', argument]), 0)
                self.assertEqual(bool(sharing.consent(self.config)), argument == '1')
            self.assertEqual(entry['main'](['Peach.exe', '--installer-library-sharing', 'true']), 2)
            collect.assert_not_called()
            client.assert_not_called()

    def test_real_sdk_sends_only_research_event_and_reviewed_attachment_once_per_week(self):
        from sentry_sdk.transport import HttpTransport
        self.enable()
        sent = []
        with mock.patch.object(HttpTransport, '_send_envelope', side_effect=lambda envelope: sent.append(envelope)):
            self.service.attempt()
            sharing.Service(self.config, self.contract).attempt()
        self.assertEqual(len(sent), 1)
        envelope = sent[0]
        self.assertEqual([item.type for item in envelope.items], ['event', 'attachment'])
        event = envelope.items[0].get_event()
        self.assertNotIn('extra', event)
        self.assertNotIn('server_name', event)
        self.assertNotIn('user', event)
        self.assertNotIn('release', event)
        self.assertEqual(event['tags']['purpose'], 'population-research')
        attachment = json.loads(gzip.decompress(envelope.items[1].payload.get_bytes()))
        self.assertEqual(attachment['schema'], sharing.SCHEMA)
        self.assertEqual(envelope.items[1].headers['filename'], sharing.FILENAME)
        self.assertIn('library', attachment)
        self.assertIn('preferences', attachment)

    def test_disable_invalidates_queued_attachment(self):
        from sentry_sdk.envelope import Envelope
        from sentry_sdk.attachments import Attachment
        self.enable()
        previous = sharing.consent(self.config)['revision']
        envelope = Envelope()
        envelope.headers['trace'] = {'release': 'private-build', 'user_segment': 'private-user'}
        envelope.add_event({'message': 'Peach research', 'tags': {'private': 'name'}})
        envelope.add_item(Attachment(bytes=sharing.compressed(sharing.collect(self.contract)[1]), filename=sharing.FILENAME).to_envelope_item())
        self.assertTrue(self.service._gate(previous, envelope))
        self.assertNotIn('trace', envelope.headers)
        self.assertNotIn('private', envelope.items[0].get_event()['tags'])
        self.service.save({'enabled': False})
        self.assertFalse(self.service._gate(previous, envelope))

    def test_transport_rejects_unreviewed_fields_even_with_correct_filename(self):
        from sentry_sdk.envelope import Envelope
        from sentry_sdk.attachments import Attachment
        self.enable()
        revision = sharing.consent(self.config)['revision']
        payload, _ = sharing.collect(self.contract)
        for invalid in ({**payload, 'library': [{'code': 'EXM-001', 'path': 'R:/private'}]},
                        {**payload, 'user_id': 'private'},
                        {**payload, 'preferences': {'private': []}},
                        {'schema': sharing.SCHEMA, 'summary': []}):
            envelope = Envelope()
            envelope.add_event({'message': 'private'})
            envelope.add_item(Attachment(bytes=gzip.compress(json.dumps(invalid).encode()), filename=sharing.FILENAME).to_envelope_item())
            self.assertFalse(self.service._gate(revision, envelope))

    def test_transport_rejects_corrupt_and_oversized_compressed_data(self):
        from sentry_sdk.envelope import Envelope
        from sentry_sdk.attachments import Attachment
        self.enable()
        revision = sharing.consent(self.config)['revision']
        for raw in (b'not gzip', gzip.compress(b'x' * 4096), gzip.compress(b'{}')[:-3]):
            envelope = Envelope()
            envelope.add_event({'message': 'private'})
            envelope.add_item(Attachment(bytes=raw, filename=sharing.FILENAME).to_envelope_item())
            with mock.patch.object(sharing, 'MAX_RAW_BYTES', 2048):
                self.assertFalse(self.service._gate(revision, envelope))

    def test_oversized_snapshot_is_rejected_without_partial_upload(self):
        with mock.patch.object(sharing, 'MAX_RAW_BYTES', 1), self.assertRaises(ValueError):
            self.service.preview()
        self.assertFalse(sharing.snapshot(self.config)['enabled'])
        self.enable()
        with mock.patch.object(sharing, 'MAX_BYTES', 1), mock.patch('peach.remote_reporting._new_client') as client:
            with self.assertRaises(ValueError):
                self.service.attempt()
            client.assert_not_called()

    def test_preview_and_consent_api_are_local_authenticated_and_same_origin(self):
        import asyncio
        import httpx
        from fastapi import FastAPI
        from peach.routes_configuration import router, require_auth
        app = FastAPI()
        app.include_router(router)
        app.state.library_sharing = self.service
        app.dependency_overrides[require_auth] = lambda: {}

        async def request(method, path, peer='127.0.0.1', origin='http://test', body=None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
                                         base_url='http://test') as client:
                return await client.request(method, path, json=body, headers={'Origin': origin})
        endpoint = '/api/configuration/library-sharing'
        response = asyncio.run(request('GET', endpoint + '/preview'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        token = response.json()['token']
        body = {'enabled': True, 'schema': sharing.SCHEMA, 'consent': True, 'preview_token': token}
        for peer, origin in (('198.51.100.7', 'http://test'), ('127.0.0.1', 'https://external.invalid')):
            self.assertEqual(asyncio.run(request('GET', endpoint + '/preview', peer, origin)).status_code, 403)
            self.assertEqual(asyncio.run(request('POST', endpoint, peer, origin, body)).status_code, 403)
        self.assertFalse(sharing.snapshot(self.config)['enabled'])
        self.assertEqual(asyncio.run(request('POST', endpoint, body=body)).status_code, 200)
