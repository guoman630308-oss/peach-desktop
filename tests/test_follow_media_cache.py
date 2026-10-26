import asyncio
import json
import gzip
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from peach.preview_cache import PreviewCache
from peach.remote_media_cache import AVATAR_REFRESH_SECONDS, FAVICON_REFRESH_SECONDS, RemoteMediaCache


class RemoteMediaCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cache = RemoteMediaCache(self.root)
        self.calls = 0
        self.status = 200
        self.headers = {"content-type": "video/mp4", "content-length": "10", "set-cookie": "origin=private", "server": "origin"}
        self.payload = b"0123456789"
        def fetch():
            self.calls += 1
            return httpx.Response(self.status, headers=self.headers, stream=httpx.ByteStream(self.payload))
        def endpoint(request):
            return self.cache.stream("follow:1:media:0", request, fetch)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=[Route("/media", endpoint)])),
                                       base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def test_complete_media_survives_restart_and_serves_range_without_upstream(self):
        first = await self.client.get("/media")
        self.assertEqual(first.content, self.payload)
        self.assertNotIn("set-cookie", first.headers)
        self.assertNotIn("server", first.headers)
        self.cache = RemoteMediaCache(self.root)
        replay = await self.client.get("/media", headers={"Range": "bytes=2-5"})
        self.assertEqual(replay.status_code, 206)
        self.assertEqual(replay.content, b"2345")
        self.assertEqual(replay.headers["content-range"], "bytes 2-5/10")
        head = await self.client.head("/media")
        self.assertEqual(head.content, b"")
        self.assertEqual(self.calls, 1)

    async def test_partial_response_is_reused_only_for_its_range(self):
        self.status, self.payload = 206, b"2345"
        self.headers.update({"content-range": "bytes 2-5/10", "content-length": "4"})
        for _ in range(2):
            response = await self.client.get("/media", headers={"Range": "bytes=2-5"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b"2345")
        self.assertEqual(self.calls, 1)
        self.assertIsNone(self.cache.stored("follow:1:media:0", "bytes=6-9"))
        self.assertIsNone(self.cache.stored("follow:1:media:0"))

    async def test_content_encoding_is_preserved_for_disk_replay(self):
        self.payload = gzip.compress(b"0123456789")
        self.headers.update({"content-encoding": "gzip", "content-length": str(len(self.payload))})
        for _ in range(2):
            response = await self.client.get("/media")
            self.assertEqual(response.content, b"0123456789")
        self.assertEqual(self.calls, 1)

    async def test_concurrent_cold_requests_download_once(self):
        responses = await asyncio.gather(self.client.get("/media"), self.client.get("/media"))
        self.assertEqual([response.content for response in responses], [self.payload] * 2)
        self.assertEqual(self.calls, 1)

    async def test_a_complete_range_can_serve_other_ranges(self):
        self.status = 206
        self.headers["content-range"] = "bytes 0-9/10"
        await self.client.get("/media", headers={"Range": "bytes=0-"})
        replay = await self.client.get("/media", headers={"Range": "bytes=2-5"})
        self.assertEqual(replay.content, b"2345")
        self.assertEqual(self.calls, 1)

    async def test_html_and_failed_status_are_not_installed(self):
        self.headers = {"content-type": "text/html", "content-length": "5"}
        self.payload = b"error"
        response = await self.client.get("/media")
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIsNone(self.cache.stored("follow:1:media:0"))
        self.headers["content-type"] = "video/mp4"
        self.status = 403
        response = await self.client.get("/media")
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIsNone(self.cache.stored("follow:1:media:0"))

    async def test_invalid_media_head_is_not_cacheable_and_valid_retry_is_persistent(self):
        self.headers['content-type'] = 'text/html'
        response = await self.client.head('/media')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIsNone(self.cache.stored('follow:1:media:0'))
        self.headers['content-type'] = 'video/mp4'
        response = await self.client.get('/media')
        self.assertEqual(response.content, self.payload)
        self.assertIn('max-age=31536000', response.headers['cache-control'])
        await self.client.get('/media')
        self.assertEqual(self.calls, 2)

    async def test_truncated_response_leaves_no_cache_or_temporary_bytes(self):
        self.payload = b"short"
        with self.assertRaises(OSError):
            await self.client.get("/media")
        self.assertIsNone(self.cache.stored("follow:1:media:0"))
        self.assertFalse(list(self.root.rglob("*.tmp*")))

    def test_permanent_bytes_and_refresh_periods(self):
        fetch = mock.Mock(return_value=b"image")
        self.assertEqual(self.cache.bytes("image", fetch), b"image")
        for file in self.root.rglob("*.data"):
            os.utime(file, (0, 0))
        self.assertEqual(RemoteMediaCache(self.root).bytes("image", fetch), b"image")
        fetch.assert_called_once()
        self.assertEqual(AVATAR_REFRESH_SECONDS, 90 * 86400)
        self.assertEqual(FAVICON_REFRESH_SECONDS, 365 * 86400)
        self.assertEqual(self.cache.bytes("image", fetch, ttl=AVATAR_REFRESH_SECONDS), b"image")
        self.assertEqual(fetch.call_count, 2)

    def test_refresh_failure_preserves_existing_bytes(self):
        self.cache.bytes("image", lambda: b"image")
        for file in self.root.rglob("*.data"):
            os.utime(file, (0, 0))
        def fail():
            raise OSError("offline")
        fetch = mock.Mock(side_effect=fail)
        self.assertEqual(self.cache.bytes("image", fetch, ttl=90), b"image")
        self.assertEqual(self.cache.bytes("image", fetch, ttl=90), b"image")
        fetch.assert_called_once()


class PreviewCacheTests(unittest.TestCase):
    def test_nested_playlists_keys_and_init_segments_are_local_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = RemoteMediaCache(Path(directory).resolve())
            previews = PreviewCache(cache)
            root = previews.register("ABP-123", "https://cdn.example/root.m3u8", identity="root", playlist=True)
            fetch = mock.Mock(return_value=b'\xef\xbb\xbf#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=10\nvideo/list\n')
            body = previews.playlist("ABP-123", root, fetch).decode()
            self.assertNotIn("https://", body)
            child = body.splitlines()[-1].rsplit("/", 1)[-1]
            self.assertTrue(previews.source("ABP-123", child)["playlist"])
            self.assertEqual(previews.source("ABP-123", child)["url"], "https://cdn.example/video/list")
            nested = mock.Mock(return_value=b'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4,\npart.ts\n#EXT-X-ENDLIST\n')
            result = previews.playlist("ABP-123", child, nested).decode()
            self.assertNotIn('URI="key.bin"', result)
            self.assertNotIn('URI="init.mp4"', result)
            self.assertNotIn("\npart.ts", result)
            replay = PreviewCache(RemoteMediaCache(Path(directory))).playlist("ABP-123", child, nested)
            self.assertEqual(replay.decode(), result)
            nested.assert_called_once()
            self.assertEqual(previews.register("ABP-123", "https://cdn.example/changed.m3u8", identity="root"), root)
            self.assertEqual(previews.source("ABP-123", root)["url"], "https://cdn.example/changed.m3u8")
            with mock.patch.object(cache, 'bytes', wraps=cache.bytes) as saved:
                self.assertEqual(previews.playlist('ABP-123', root, fetch).decode(), body)
            self.assertEqual(saved.call_count, 1)
            self.assertEqual(fetch.call_count, 2)
            with self.assertRaises(ValueError):
                previews.source("ABP-123", "../private")
            with self.assertRaises(ValueError):
                previews.register("ABP-123", "https://127.0.0.1/a")
            for path in cache.root.rglob("*.source.json"):
                self.assertIn("url", json.loads(path.read_text()))

    def test_signed_playlist_refreshes_and_registers_current_segment_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = RemoteMediaCache(Path(directory).resolve())
            previews = PreviewCache(cache)
            root = previews.register('EXM-001', 'https://cdn.example/root.m3u8', identity='root')
            def playlist(signature):
                return f'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\npart.ts?sign={signature}\n#EXT-X-ENDLIST\n'.encode()
            fetch = mock.Mock(side_effect=[playlist('first'), playlist('current')])
            first = previews.playlist('EXM-001', root, fetch)
            for path in cache.root.rglob('*.data'):
                os.utime(path, (0, 0))
            current = previews.playlist('EXM-001', root, fetch)
            self.assertNotEqual(first, current)
            segment = next(line for line in current.decode().splitlines() if line.startswith('/jav-preview/'))
            self.assertTrue(previews.source('EXM-001', segment.rsplit('/', 1)[-1])['url'].endswith('sign=current'))
            self.assertEqual(previews.playlist('EXM-001', root, fetch), current)
            self.assertEqual(fetch.call_count, 2)
