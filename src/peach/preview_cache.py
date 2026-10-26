"""预览 HLS 的持久清单与媒体分片，浏览器仅取得本机对象 ID。"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urljoin, urlsplit

import m3u8

from .fsutil import atomic_write_text
from .http import public_https_url

PLAYLIST_REFRESH_SECONDS = 300


def rewrite_hls(body: bytes, url: str, register) -> bytes:
    """用已声明的 m3u8 模型投影全部媒体引用，地址登记策略由消费者提供。"""
    text = body.decode('utf-8-sig')
    if not text.lstrip().startswith('#EXTM3U'):
        raise ValueError('播放列表未取得')
    playlist = m3u8.loads(text, uri=url)
    if playlist.content_steering:
        raise ValueError('暂不支持 Content Steering 播放列表')
    nested = [*playlist.playlists, *playlist.iframe_playlists, *playlist.image_playlists, *playlist.media]
    objects = [*playlist.segments, *nested, *playlist.keys, *playlist.session_keys,
               *playlist.session_data, *playlist.segment_map, *playlist.rendition_reports,
               playlist.preload_hint]
    objects += [segment.init_section for segment in playlist.segments if segment.init_section]
    objects += [part for segment in playlist.segments for part in segment.parts]
    seen = set()
    for obj in objects:
        if obj is not None and id(obj) not in seen and getattr(obj, 'uri', None):
            seen.add(id(obj))
            obj.uri = register(urljoin(url, obj.uri), any(obj is row for row in nested))
    return playlist.dumps().encode('utf-8')


class PreviewCache:
    def __init__(self, cache):
        self.cache = cache

    def register(self, code: str, url: str, *, identity: str = "", playlist: bool | None = None,
                 media_type: str = "") -> str:
        if not public_https_url(url):
            raise ValueError("预览来源不可用")
        ref = hashlib.sha256((identity or url).encode()).hexdigest()
        path = self.cache.folder("preview:" + code) / (ref + ".source.json")
        value = {"url": url, "playlist": playlist if playlist is not None else urlsplit(url).path.lower().endswith(".m3u8")}
        if media_type == 'video/mp4':
            value['media_type'] = media_type
        try:
            current = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            current = None
        if current != value:
            atomic_write_text(path, json.dumps(value), mode=0o600)
        return ref

    def source(self, code: str, ref: str) -> dict:
        ref = ref.removesuffix(".m3u8")
        if len(ref) != 64 or any(char not in "0123456789abcdef" for char in ref):
            raise ValueError("预览对象不存在")
        path = self.cache.folder("preview:" + code) / (ref + ".source.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def playlist(self, code: str, ref: str, fetch) -> bytes:
        url = self.source(code, ref)["url"]
        def download():
            body = fetch(url)
            if not body.decode("utf-8").removeprefix(chr(0xFEFF)).lstrip().startswith("#EXTM3U"):
                raise ValueError("预览清单未取得")
            return body
        generation = hashlib.sha256(url.encode()).hexdigest()
        body = self.cache.bytes(f"preview:{code}:{ref}:playlist:{generation}", download,
                                ttl=PLAYLIST_REFRESH_SECONDS)
        def register(source, nested):
            item = self.register(code, source, playlist=nested)
            return f"/jav-preview/{code}/{item}{'.m3u8' if nested else ''}"
        return rewrite_hls(body, url, register)
