"""STRM 入库、按需播放与媒体读取模式的行为回归。"""
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import replace
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from peach import media_processing_policy as policy, settings_file, strm
from peach.api import create_app
from peach.config import PeachSettings
from peach.media_probe import probe_unmeasured
from peach.scan import medium_of, scan_location
from peach.strm_stream import StrmResources, rewrite_playlist
from support.ledger import fresh_ledger
from support.web import route_client


class StrmTextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'ABC-123.strm'

    def test_signed_address_preserves_encoding_comments_and_bom(self):
        url = 'http://127.0.0.1:5244/d/%E5%AA%92%E4%BD%93/a.mp4?sign=a%2Bb&x=2'
        self.path.write_text('\ufeff# a comment\r\n\r\n' + url + '\r\n', encoding='utf-8')
        self.assertEqual(strm.read_target(self.path), url)

    def test_invalid_or_ambiguous_pointers_are_refused_without_the_target_in_the_error(self):
        for text in ('', 'file:///private.mp4', 'C:\\private.mp4', 'plugin://addon/play',
                     'https://user:secret@example.com/a.mp4', 'https://example.com/a b.mp4',
                     'https://one.test/a\nhttps://two.test/b', 'http://host:99999/a', 'x' * (strm.MAX_BYTES + 1)):
            with self.subTest(text=text[:20]):
                self.path.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError) as caught:
                    strm.read_target(self.path)
                self.assertNotIn('secret', str(caught.exception))

    def test_strm_and_iso_are_catalogued_without_background_probe(self):
        db = fresh_ledger(self.root)
        with closing(sqlite3.connect(db)) as connection, connection:
            for index, name in enumerate(('one.STRM', 'two.ISO', 'three.mp4'), 1):
                self.assertEqual(medium_of(name), 'video')
                connection.execute('INSERT INTO asset(id,location,path,name,medium) VALUES(?,?,?,?,?)',
                                   (index, 'local', 'R:\\media\\' + name, name, 'video'))
        measured = (10, 1280, 720, 'h264', 24, None, '速食', '横屏', '720P', 3)
        with patch('peach.media_probe.measure', return_value=measured) as measure:
            policy.save(self.root, True)
            self.assertEqual(probe_unmeasured(db, 'ffprobe', 'local', 'R:\\media'), 1)
        self.assertEqual(measure.call_args.args[1:3], (3, 'R:\\media\\three.mp4'))
        with closing(sqlite3.connect(db)) as connection:
            self.assertEqual(connection.execute('SELECT duration FROM asset WHERE id<3').fetchall(), [(None,), (None,)])

    def test_preview_mode_survives_restart_and_can_be_disabled(self):
        self.assertFalse(policy.metadata_only(self.root))
        policy.save(self.root, True)
        self.assertTrue(policy.metadata_only(self.root))
        policy.save(self.root, False)
        self.assertFalse(policy.metadata_only(self.root))
        (self.root / policy.FILENAME).write_text('{', encoding='utf-8')
        self.assertTrue(policy.metadata_only(self.root))

    def test_scan_recognizes_pointers_and_disc_images_and_preserves_sidecars(self):
        media = self.root / 'media'
        media.mkdir()
        (media / 'ABC-123.strm').write_text('https://example.com/movie.mp4', encoding='utf-8')
        (media / 'ABC-124.iso').write_bytes(b'iso')
        (media / 'ABC-123.nfo').write_text('<movie/>', encoding='utf-8')
        (media / 'ABC-123-poster.jpg').write_bytes(b'poster')
        db = fresh_ledger(self.root)
        def scan():
            return scan_location(db, 'local', 'R:\\media', declared_roots={'local': ('R:\\media',)},
                                 mounts={'local': (str(media),)}, windows=False)
        scan()
        with closing(sqlite3.connect(db)) as connection, connection:
            self.assertEqual(connection.execute('SELECT name,medium FROM asset ORDER BY name').fetchall(),
                             [('ABC-123.strm', 'video'), ('ABC-124.iso', 'video')])
            connection.execute("UPDATE asset SET medium='other' WHERE name='ABC-123.strm'")
        scan()
        with closing(sqlite3.connect(db)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM asset').fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT medium FROM asset WHERE name='ABC-123.strm'").fetchone()[0], 'video')

    def test_hls_resource_map_is_bounded_and_bound_to_the_pointer(self):
        resources = StrmResources(capacity=2)
        root = 'https://example.com/master.m3u8'
        first = resources.add(1, root, 'https://example.com/first.ts')
        second = resources.add(1, root, 'https://example.com/second.ts')
        resources.add(1, root, 'https://example.com/third.ts')
        with self.assertRaises(ValueError):
            resources.get(first, 1, root)
        self.assertEqual(resources.get(second, 1, root), 'https://example.com/second.ts')
        for asset_id, pointer in ((2, root), (1, 'https://example.com/other.m3u8')):
            with self.assertRaises(ValueError):
                resources.get(second, asset_id, pointer)


class StrmPlaybackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'ABC-123.strm'
        self.path.write_text('http://127.0.0.1:5244/d/movie.mp4?sign=private', encoding='utf-8')
        self.db = fresh_ledger(self.root)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute('INSERT INTO asset(id,location,path,name,medium,size) VALUES(1,?,?,?,?,?)',
                               ('local', str(self.path), self.path.name, 'video', self.path.stat().st_size))
        self.app = create_app(PeachSettings(configured=True, db_path=self.db,
            follow_state_root=self.root / 'state', allowed_media_roots=(self.root,)))
        self.addCleanup(self.app.state.http_transport.close)
        self.requests = []
        self.upstream = httpx.Client(transport=httpx.MockTransport(self.serve))
        self.addCleanup(self.upstream.close)
        self.app.state.http_transport.client.close()
        self.app.state.http_transport.client = self.upstream

    def serve(self, request):
        self.requests.append(request)
        return httpx.Response(206, content=b'video', headers={
            'Content-Type': 'video/mp4', 'Content-Range': 'bytes 0-4/100', 'Accept-Ranges': 'bytes'})

    def test_browsing_and_stream_plan_do_not_access_remote_video(self):
        with route_client(self.app) as client:
            result = client.get('/api/stream-plan?id=1').json()
            item = client.get('/api/item?id=1').json()
        self.assertEqual(result['reason'], 'strm-pointer')
        self.assertNotIn('private', json.dumps(result))
        self.assertIsNone(item['size'])
        self.assertTrue(item['is_strm'])
        self.assertEqual(self.requests, [])

    def test_mount_choice_matches_catalog_detail_playlist_and_library_without_exposing_paths(self):
        root = str(self.root)
        config = replace(settings_file.PeachConfig(self.root, self.root / 'config.toml'),
                         locations={'local': (root,)}, library_names={root: '指针测试'},
                         media_costs={root: 'metered'})
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO playlist(id,profile_id,name,source_kind,created_at,updated_at) "
                               "VALUES(1,'local-default','测试','manual','now','now')")
            connection.execute("INSERT INTO playlist_item(playlist_id,asset_id,position,added_at) VALUES(1,1,0,'now')")
        with patch('peach.settings_file.active', return_value=config), route_client(self.app) as client:
            catalog = client.get('/api/items?thumb=0').json()['items']
            detail = client.get('/api/item?id=1').json()
            playlist = client.get('/api/playlist?id=1').json()['items']
            library = client.get('/api/libraries').json()['libraries'][0]
        for row in (catalog[0], detail, playlist[0]):
            self.assertEqual(row['cost'], 'metered')
            self.assertNotIn('path', row)
        self.assertTrue(library['metered'])
        config = replace(config, media_costs={root: 'free'})
        with patch('peach.settings_file.active', return_value=config), route_client(self.app) as client:
            self.assertEqual(client.get('/api/item?id=1').json()['cost'], 'free')
            self.assertFalse(client.get('/api/libraries').json()['libraries'][0]['metered'])
        self.assertEqual(self.requests, [])

    def test_range_is_forwarded_without_browser_credentials_or_upstream_address(self):
        self.upstream.cookies.set('service-secret', 'private', domain='127.0.0.1')
        with route_client(self.app) as client:
            response = client.get('/stream?id=1&session=test', headers={'Range': 'bytes=0-4', 'Cookie': 'private=cookie'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b'video')
        self.assertEqual(response.headers['content-range'], 'bytes 0-4/100')
        self.assertEqual(self.requests[0].headers['range'], 'bytes=0-4')
        self.assertNotIn('cookie', self.requests[0].headers)
        self.assertNotIn('location', response.headers)

    def test_real_http_range_flow_reads_only_the_requested_bytes(self):
        seen = []
        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get('Range'))
                self.send_response(206)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', '4')
                self.send_header('Content-Range', 'bytes 4-7/100')
                self.end_headers()
                self.wfile.write(b'part')

            def log_message(self, _format, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Source)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.path.write_text(f'http://127.0.0.1:{server.server_port}/movie.mp4', encoding='utf-8')
            with httpx.Client(trust_env=False) as remote, route_client(self.app) as client:
                self.app.state.http_transport.client = remote
                result = client.get('/stream?id=1&session=real', headers={'Range': 'bytes=4-7'})
            self.assertEqual((result.status_code, result.content), (206, b'part'))
            self.assertEqual(seen, ['bytes=4-7'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_hls_external_ticket_authorizes_nested_resources_without_browser_cookie(self):
        from peach.external_playback import issue
        self.app.state.settings = replace(self.app.state.settings, token='password')
        self.path.write_text('http://127.0.0.1:5244/movie.m3u8', encoding='utf-8')
        def serve(request):
            if request.url.path.endswith('.m3u8'):
                return httpx.Response(200, text='#EXTM3U\n#EXTINF:6,\npart.ts\n#EXT-X-ENDLIST\n',
                                      headers={'content-type': 'application/vnd.apple.mpegurl'})
            return httpx.Response(200, content=b'part')
        self.upstream._transport = httpx.MockTransport(serve)
        origin = 'http://testserver'
        ticket = issue(self.app.state.external_playback_key, 1, origin=origin)
        with route_client(self.app) as client:
            response = client.get('/external-stream/1', params={'ticket': ticket, 'origin': origin})
            self.assertEqual(response.status_code, 200)
            line = next(line for line in response.text.splitlines() if line.startswith('/strm-resource/'))
            self.assertEqual(client.get(line).content, b'part')
            self.assertEqual(client.get(line.split('?')[0]).status_code, 401)

    def test_iso_catalogue_entry_streams_through_external_player_only(self):
        iso = self.root / 'ABC-123.iso'
        iso.write_bytes(b'iso')
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute('UPDATE asset SET path=?,name=? WHERE id=1', (str(iso), iso.name))
        with route_client(self.app) as client:
            self.assertEqual(client.get('/api/stream-plan?id=1').status_code, 415)
            self.assertEqual(client.get('/stream?id=1').status_code, 415)
        self.assertEqual(self.requests, [])

    def test_redirect_is_resolved_at_playback_and_signed_query_is_preserved(self):
        def serve(request):
            self.requests.append(request)
            if request.url.path == '/d/movie.mp4':
                return httpx.Response(302, headers={'Location': '/actual.mp4?sign=a%2Bb'})
            return httpx.Response(200, content=b'ok')
        self.upstream._transport = httpx.MockTransport(serve)
        with route_client(self.app) as client:
            self.assertEqual(client.get('/stream?id=1').content, b'ok')
        self.assertEqual(str(self.requests[-1].url), 'http://127.0.0.1:5244/actual.mp4?sign=a%2Bb')

    def test_hls_keeps_keys_maps_and_segments_behind_authenticated_local_urls(self):
        self.path.write_text('http://127.0.0.1:5244/movie.m3u8', encoding='utf-8')
        playlist = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-KEY:METHOD=AES-128,URI="key?sign=s"\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6,\nsegment.ts?sign=s\n#EXT-X-ENDLIST\n'
        def serve(request):
            self.requests.append(request)
            if request.url.path.endswith('.m3u8'):
                return httpx.Response(200, text=playlist, headers={'content-type': 'application/vnd.apple.mpegurl'})
            return httpx.Response(200, content=b'segment')
        self.upstream._transport = httpx.MockTransport(serve)
        with route_client(self.app) as client:
            plan = client.get('/api/stream-plan?id=1').json()
            self.assertEqual(plan['protocol'], 'hls')
            result = client.get(plan['src'])
            self.assertNotIn('sign=s', result.text)
            self.assertNotIn('127.0.0.1', result.text)
            line = next(line for line in result.text.splitlines() if line.startswith('/strm-resource/'))
            self.assertEqual(client.get(line).content, b'segment')
            self.assertEqual(client.get('/strm-resource/1/unknown').status_code, 400)

    def test_cancelled_session_and_invalid_pointer_do_not_fetch(self):
        self.app.state.stream_sessions.cancel('cancelled')
        with route_client(self.app) as client:
            self.assertEqual(client.get('/stream?id=1&session=cancelled').status_code, 410)
            self.path.write_text('file:///secret', encoding='utf-8')
            self.assertEqual(client.get('/stream?id=1').status_code, 400)
        self.assertEqual(self.requests, [])

    def test_policy_endpoint_stops_timeline_without_touching_ledger(self):
        with route_client(self.app) as client:
            response = client.post('/api/media-processing', json={'metadata_only': True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(client.get('/api/media-processing').json()['metadata_only'])
            self.assertEqual(client.post('/api/thumbnail-jobs', json={'mode': 'precise'}).status_code, 400)
            self.assertEqual(client.post('/api/media-processing', json={'metadata_only': 'yes'}).status_code, 400)
            self.assertFalse(client.post('/api/media-processing', json={'metadata_only': False}).json()['metadata_only'])
        self.assertEqual(json.loads((self.root / 'state' / 'timeline-thumbnails.json').read_text())['mode'], 'off')


class StrmPlaylistTests(unittest.TestCase):
    def test_master_playlist_rewrites_renditions_and_refuses_private_cross_origin(self):
        state = SimpleNamespace(strm_resources=StrmResources())
        root = 'http://127.0.0.1:5244/master.m3u8'
        master = b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="audio",URI="audio.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=1000,AUDIO="a"\nvideo.m3u8\n'
        result = rewrite_playlist(state, master, root, 1, root, '')
        self.assertEqual(result.count('/strm-resource/'), 2)
        with self.assertRaises(ValueError):
            rewrite_playlist(state, b'#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:9999/admin\n', root, 1, root, '')
        self.assertLessEqual(len(state.strm_resources.entries), 4096)
