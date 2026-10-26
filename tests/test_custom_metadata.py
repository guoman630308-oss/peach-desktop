"""自定义来源的配置、网络边界与正式采集入口。"""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from peach import custom_metadata as custom
from peach.library_processing import _RemoteSession
from peach.metadata_policy import CHAIN_UNKNOWN, source_tier
from peach.metadata_auto_apply import _evidence_candidates


def definition(**changes):
    return dict(name='测试来源', endpoint='https://metadata.example.com/movies/{code}',
                fields={'code': 'data.id', 'title': 'data.title', 'performers': 'data.cast'},
                content_types=['censored'], enabled=True, **changes)


class CustomMetadataTests(unittest.TestCase):
    def test_save_toggle_delete_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            row = custom.save(root, definition())[0]
            self.assertEqual(custom.load(root), [row])
            custom.save(root, dict(row, enabled=False))
            self.assertFalse(custom.load(root)[0]['enabled'])
            self.assertEqual(custom.save(root, {'id': row['id'], 'action': 'delete'}), [])

    def test_endpoints_and_paths_are_restricted(self):
        for endpoint in ('http://public.example.com/{code}', 'https://127.0.0.1/{code}',
                         'https://user:pass@example.com/{code}', 'https://{code}.example.com/',
                         'https://例子.com/{code}', 'https://example.com./{code}',
                         'https://example.com:8443/{code}', 'https://example.com/{code}/{other}'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                custom.validate(dict(definition(), endpoint=endpoint))
        with self.assertRaises(ValueError):
            custom.validate(dict(definition(), fields={'code': 'eval()'}))

    def test_response_identity_must_come_from_response(self):
        with self.assertRaisesRegex(ValueError, '番号'):
            custom.parse(definition(), 'ABW-001', {'data': {'id': 'ABW-002', 'title': '错片'}})
        record = custom.parse(definition(), 'ABW-001', {'data': {'id': 'ABW-001', 'title': '标题', 'cast': ['演员']}})
        self.assertEqual(record.payload()['actresses'], [{'japanese_name': '演员'}])
        self.assertEqual(record.confidence, 0.5)
        self.assertEqual(source_tier(record.source), CHAIN_UNKNOWN)
        self.assertEqual(_evidence_candidates({'candidates': [{'source': record.source, 'value': '标题'}]}, 'title'), [])

    def test_private_dns_is_refused_before_connecting(self):
        with patch.object(custom, 'host_addresses', return_value=('127.0.0.1',)), patch.object(custom.requests, 'Session') as session:
            with self.assertRaisesRegex(ValueError, '公网'):
                custom.fetch_json('https://metadata.example.com/ABW-001', 2)
            session.assert_not_called()

    def test_http_uses_the_budget_left_by_proxy_dns_and_session_preparation(self):
        clock = [100.0]
        response = Mock(status_code=200)
        response.iter_content.return_value = [b'{"data":{"id":"ABW-001","title":"example","cast":[]}}']
        session = MagicMock()
        client = session.__enter__.return_value
        client.stream.return_value.__enter__.return_value = response
        def proxy_options(_root):
            clock[0] += 2
            return {'proxy': 'http://127.0.0.1:7890', 'trust_env': False}
        def addresses(_host):
            clock[0] += 6
            return ('1.1.1.1',)
        def prepare_session(**_options):
            clock[0] += 1
            return session
        with patch.object(custom.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch('peach.peach_proxy.client_options', side_effect=proxy_options), \
                patch.object(custom, 'host_addresses', side_effect=addresses), \
                patch.object(custom.requests, 'Session', side_effect=prepare_session):
            record = custom.query(definition(network='peach_proxy'), 'ABW-001', secrets_root=Path('.'))
        self.assertEqual(record.title, 'example')
        self.assertEqual(client.stream.call_args.kwargs['timeout'], 6)

    def test_exhausted_dns_budget_does_not_start_http(self):
        def exercise(dns_elapsed):
            clock = [100.0]
            response = Mock(status_code=200)
            response.iter_content.return_value = [b'{"ok":true}']
            def addresses(_host):
                clock[0] += dns_elapsed
                return ('1.1.1.1',)
            with patch.object(custom.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(custom, 'host_addresses', side_effect=addresses), \
                    patch.object(custom.requests, 'Session') as session:
                session.return_value.__enter__.return_value.stream.return_value.__enter__.return_value = response
                with self.assertRaisesRegex(ValueError, '超时'):
                    custom.fetch_json('https://metadata.example.com/ABW-001', 15)
                session.assert_not_called()
        for dns_elapsed in (15, 16):
            with self.subTest(dns_elapsed=dns_elapsed):
                exercise(dns_elapsed)

    def test_formal_pipeline_consumes_enabled_source_after_builtin_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = custom.save(root / 'secrets', definition())[0]
            key = custom.source_key(source)
            config = SimpleNamespace(directory=lambda name: root / name)
            remote = _RemoteSession(config, Mock, Mock(), retrying=True)
            self.assertEqual(remote._chain('ABW-001')[-1], key)
            with patch.object(custom, 'fetch_json', return_value={'data': {'id': 'ABW-001', 'title': '标题', 'cast': []}}):
                answer = remote._ask(key, 'ABW-001', (key,), [], required=['title'],
                                     deadline=time.monotonic() + 10, evidence=())
            self.assertEqual(answer[0][0], key)
            self.assertEqual(answer[0][1]['title'], '标题')
            remote = _RemoteSession(config, Mock, Mock(), retrying=True, routes={'censored': ()})
            with patch.object(custom, 'fetch_json', return_value={'data': {'id': 'ABW-001', 'title': '标题', 'cast': []}}):
                entries, _covers = remote.collect({'id': 1, 'code': 'ABW-001', 'path': 'R:/ABW-001.mp4',
                    'name': 'ABW-001.mp4'}, 'ABW-001', ['title'], root / 'covers', update=Mock(), issue=Mock())
            self.assertEqual(entries[0][0], key)
            self.assertEqual(entries[0][1]['title'], '标题')
            self.assertTrue(entries[0][2].is_file())
            custom.save(root / 'secrets', dict(source, enabled=False))
            self.assertNotIn(key, remote._chain('ABW-001'))
            changed = custom.save(root / 'secrets', dict(source, fields={'code': 'data.id', 'title': 'data.name'}))[0]
            self.assertNotEqual(custom.source_key(changed), key)

    def test_mapping_rejects_unbounded_or_wrong_shapes(self):
        with self.assertRaises(ValueError):
            custom.parse(definition(), 'ABW-001', {'data': {'id': 'ABW-001', 'cast': '演员'}})
        with self.assertRaises(ValueError):
            custom.parse(definition(), 'ABW-001', {'data': {'id': 'ABW-001', 'title': 'x' * 4097}})

    def test_public_connection_is_pinned_with_proxy_and_redirects_refused(self):
        response = Mock(status_code=200)
        response.iter_content.return_value = [b'{"ok":true}']
        with patch.object(custom, 'host_addresses', return_value=('1.1.1.1',)), patch.object(custom.requests, 'Session') as session:
            session.return_value.__enter__.return_value.stream.return_value.__enter__.return_value = response
            self.assertEqual(custom.fetch_json('https://metadata.example.com/ABW-001', 3,
                client_options={'proxy': 'http://127.0.0.1:7890', 'trust_env': False}), {'ok': True})
            options = session.call_args.kwargs
            target = options['curl_options'][custom.CurlOpt.CONNECT_TO]
            self.assertEqual(custom.ffi.string(target.data), b'metadata.example.com:443:1.1.1.1:443')
            stream = session.return_value.__enter__.return_value.stream
            self.assertFalse(stream.call_args.kwargs['allow_redirects'])
            self.assertNotIn('verify', stream.call_args.kwargs)
            response.status_code = 302
            with self.assertRaisesRegex(ValueError, 'HTTP 302'):
                custom.fetch_json('https://metadata.example.com/ABW-001', 3)

    def test_response_limit_and_code_format(self):
        response = Mock(status_code=200)
        response.iter_content.return_value = [b'x' * (custom.MAX_BYTES + 1)]
        with patch.object(custom, 'host_addresses', return_value=('1.1.1.1',)), patch.object(custom.requests, 'Session') as session:
            session.return_value.__enter__.return_value.stream.return_value.__enter__.return_value = response
            with self.assertRaisesRegex(ValueError, '1 MiB'):
                custom.fetch_json('https://metadata.example.com/ABW-001', 3)
        self.assertEqual(custom.endpoint(dict(definition(), code_format='underscore'), '112312-478'),
                         'https://metadata.example.com/movies/112312_478')
