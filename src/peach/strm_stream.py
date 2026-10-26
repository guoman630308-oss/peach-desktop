"""STRM 的按需播放代理；Range 与 HLS 均保留 Peach 鉴权和上游地址隔离。"""
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import OrderedDict
from urllib.parse import quote, urlsplit

import httpx
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse

from .follow_stream import FollowProxyError, ResolvedFollowMedia, open_upstream
from .http import public_https_url, resolves_publicly
from .remote_media_cache import MEDIA_HEADERS
from .preview_cache import rewrite_hls
from .strm import read_target, validate_url

PLAYLIST_TYPES = frozenset({'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/mpegurl'})
PLAYLIST_LIMIT = 2 * 1024 * 1024


def mime_type(url: str) -> str:
    suffix = urlsplit(url).path.lower()
    return ('application/vnd.apple.mpegurl' if suffix.endswith('.m3u8')
            else 'video/webm' if suffix.endswith('.webm') else 'video/mp4')


def allowed_hop(root: str, url: str) -> bool:
    try:
        validate_url(url)
        parsed, original = urlsplit(url), urlsplit(root)
    except ValueError:
        return False
    if (parsed.scheme, parsed.hostname, parsed.port) == (original.scheme, original.hostname, original.port):
        return True
    return public_https_url(url) and resolves_publicly(parsed.hostname or '')


class StrmResources:
    """HLS 子资源只发不含上游地址的短键，映射保存在本机有界内存。"""

    def __init__(self, capacity: int = 65536):
        self.entries = OrderedDict()
        self.lock = threading.Lock()
        self.capacity = capacity

    def add(self, asset_id: int, root: str, url: str) -> str:
        key = hashlib.sha256(f'{asset_id}\n{root}\n{url}'.encode()).hexdigest()
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = (time.monotonic() + 6 * 3600, asset_id, root, url)
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
        return key

    def get(self, key: str, asset_id: int, root: str) -> str:
        with self.lock:
            row = self.entries.get(key)
        if row is None or row[0] <= time.monotonic() or row[1:3] != (asset_id, root):
            raise ValueError('STRM 播放资源已失效，请重新打开作品')
        return row[3]


def resources(state):
    return state.strm_resources


def rewrite_playlist(state, body: bytes, base: str, asset_id: int, root: str, session: str,
                     external_query: str = '') -> str:
    def link(target, _nested):
        if not allowed_hop(root, target):
            raise ValueError('STRM 播放列表包含不可访问的媒体地址')
        key = resources(state).add(asset_id, root, target)
        return f'/strm-resource/{asset_id}/{key}?session={quote(session, safe="")}{external_query}'

    return rewrite_hls(body, base, link).decode('utf-8')


def proxy(request, asset_id: int, path, session: str = '', resource: str = ''):
    state = request.app.state
    if len(session) > 128:
        return JSONResponse({'error': 'invalid session'}, status_code=400)
    if session and state.stream_sessions.is_cancelled(session):
        return Response(status_code=410)
    upstream = None
    try:
        root = read_target(path)
        target = resources(state).get(resource, asset_id, root) if resource else root
        upstream = open_upstream(
            state.http_transport.client, request.method, ResolvedFollowMedia(target, ''),
            incoming=request.headers, allowed=lambda url: allowed_hop(root, url),
            accepted_statuses=(416,), strip_credentials=True)
        headers = {name: upstream.headers[name] for name in MEDIA_HEADERS if name in upstream.headers}
        headers.update({'cache-control': 'no-store', 'referrer-policy': 'no-referrer'})
        if upstream.status_code == 416:
            upstream.close()
            return Response(status_code=416, headers={name: value for name, value in headers.items()
                            if name not in {'content-type', 'content-length', 'content-encoding'}})
        content_type = upstream.headers.get('content-type', '').split(';')[0].lower()
        playlist = content_type in PLAYLIST_TYPES or urlsplit(str(upstream.url)).path.lower().endswith('.m3u8')
        if request.method == 'HEAD':
            upstream.close()
            return Response(status_code=upstream.status_code, headers=headers)
        if playlist:
            try:
                content = bytearray()
                for chunk in upstream.iter_bytes():
                    content.extend(chunk)
                    if len(content) > PLAYLIST_LIMIT:
                        raise ValueError('STRM 播放列表超过 2 MiB')
            finally:
                upstream.close()
            external_query = ''
            if request.query_params.get('ticket'):
                external_query = ('&ticket=' + quote(request.query_params['ticket'], safe='')
                                  + '&origin=' + quote(request.query_params.get('origin', ''), safe=''))
            text = rewrite_playlist(state, bytes(content), str(upstream.url), asset_id, root, session,
                                    external_query)
            return Response(text, media_type='application/vnd.apple.mpegurl',
                            headers={'cache-control': 'no-store', 'referrer-policy': 'no-referrer'})

        async def body():
            task = asyncio.current_task()
            registered = not session or state.stream_sessions.register(session, task)
            try:
                if not registered:
                    return
                iterator = iter((upstream.content,)) if upstream.is_stream_consumed else upstream.iter_raw()
                while True:
                    chunk = await asyncio.to_thread(next, iterator, None)
                    if chunk is None:
                        break
                    yield chunk
            finally:
                upstream.close()
                if session and registered:
                    state.stream_sessions.unregister(session, task)
        return StreamingResponse(body(), status_code=upstream.status_code,
                                 headers=headers, background=BackgroundTask(upstream.close))
    except ValueError:
        if upstream is not None:
            upstream.close()
        return JSONResponse({'error': 'STRM 内容无法解析，请检查播放地址和列表格式'}, status_code=400)
    except (FollowProxyError, OSError, httpx.HTTPError):
        if upstream is not None:
            upstream.close()
        return JSONResponse({'error': 'STRM 媒体未取得，请检查播放地址或在生成工具中更新 STRM'}, status_code=502)
