"""JavDB Web UI 的匿名只读协议；作品身份核对、请求预算与有界缓存。"""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime
from urllib.parse import urlencode, urlsplit

from PIL import Image

from .catalog_rules import normalise_code_key
from .http import HttpRequest, HttpxTransport, response_text
from .scripting import HostLimiter
from .jav_cache import DAY, PublicSnapshots, cache_lifetime

WEB = "https://catembylegacy.fastcdn.dpdns.org"
API = "https://jdforrepam.com/api"
PAGE_SIZE = 10
MAX_REVIEW_PAGES = 8
_CACHE_ROOT = ContextVar('jav_public_cache_root', default=None)
_CACHE_READS = ContextVar('jav_public_cache_reads', default=None)


@contextmanager
def public_cache_scope(root):
    """来源快照跟随调用者的数据目录，读取时间与失败状态属于本次请求。"""
    root_token = _CACHE_ROOT.set(root)
    reads = []
    reads_token = _CACHE_READS.set(reads)
    try:
        yield reads
    finally:
        _CACHE_READS.reset(reads_token)
        _CACHE_ROOT.reset(root_token)


def cache_checked_at(prefix, *, exact=False):
    matches = [read['saved'] for read in (_CACHE_READS.get() or [])
               if (read['key'] == prefix if exact else read['key'].startswith(prefix))]
    return min(matches) if matches else None


def cache_error(key):
    return next((read['error'] for read in reversed(_CACHE_READS.get() or [])
                 if read['key'] == key and read['error']), '')


def _record_read(key, snapshot, error=''):
    reads = _CACHE_READS.get()
    if reads is not None and snapshot is not None:
        reads.append({'key': key, 'saved': snapshot.saved, 'error': error})


def public_image_url(value: object) -> bool:
    """公开图片仅取已验证来源主机的 HTTPS 地址。"""
    try:
        parts = urlsplit(str(value or ""))
        return (parts.scheme == "https" and parts.hostname == "tp.spfcas.com"
                and not parts.username and not parts.password and parts.port in (None, 443))
    except ValueError:
        return False


def preview_video_url(movie: dict) -> str:
    """只投影来源作品的公开 HLS 预览入口，不替来源断言播放成功。"""
    value = str(movie.get("preview_video_url") or "")
    try:
        parts = urlsplit(value)
        allowed = (parts.scheme == "https" and parts.hostname == "jdforrepam.com"
                   and not parts.username and not parts.password and parts.port in (None, 443)
                   and not parts.fragment and not any(char.isspace() for char in value)
                   and re.fullmatch(r"/api/v1/movies/ttm3u8/preview/[A-Za-z0-9/_-]+\.m3u8", parts.path))
    except ValueError:
        return ""
    return value if allowed else ""


class PublicSourceUnavailable(ValueError):
    """公开来源当前未取得。"""


class PublicBudgetExceeded(PublicSourceUnavailable):
    """本轮匿名资料请求预算已用完。"""


@dataclass
class PublicRequestBudget:
    max_requests: int = 202
    max_bytes: int = 16 * 1024 * 1024
    max_seconds: int = 900
    requests: int = 0
    bytes: int = 0
    started: float = 0
    stop_reason: str = ""

    def check(self):
        for reason, exhausted in (
                ("request_budget", self.requests >= self.max_requests),
                ("byte_budget", self.bytes >= self.max_bytes),
                ("time_budget", time.monotonic() - self.started >= self.max_seconds)):
            if exhausted:
                self.stop_reason = reason
                raise PublicBudgetExceeded("本轮作品资料预算已用完")


def actor_gender(actor: dict) -> str:
    """公开协议的明确性别字段；缺失和未知值保留未知。"""
    value = actor.get("gender")
    if type(value) is int:
        return {0: "female", 1: "male"}.get(value, "unknown")
    return value if value in ("female", "male") else "unknown"


class PublicAPI:
    """匿名资料按发行日期复用；来源请求限速，同键请求合并。"""

    def __init__(self, transport=None, *, clock=time.monotonic, sleeper=time.sleep,
                 cache_root=None, wallclock=time.time):
        self.transport = transport
        self.clock = clock
        self.limiter = HostLimiter({}, default_interval=3.0, clock=clock, sleeper=sleeper)
        self.lock = threading.RLock()
        self.cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self.cache_root, self.wallclock = cache_root, wallclock
        self.snapshots = {}
        self.failures = {}
        self.pending: dict[str, Future] = {}
        self.image_slots = threading.BoundedSemaphore(3)
        self.source_lock = threading.RLock()
        self.signature_parts: tuple[str, str] | None = None
        self.signature_until = 0.0
        self.paused_until = 0.0
        self.image_paused_until = 0.0
        self.request_context = ContextVar("jav_public_request_budget", default=None)

    @contextmanager
    def request_budget(self, budget: PublicRequestBudget):
        budget.started = time.monotonic()
        token = self.request_context.set(budget)
        try:
            yield budget
        finally:
            self.request_context.reset(token)

    def _get(self, url: str, *, signed=False, limit=2 * 1024 * 1024, image=False):
        paused = self.image_paused_until if image else self.paused_until
        if self.clock() < paused:
            raise PublicSourceUnavailable("图片来源暂时不可用，请稍后重试" if image else "作品资料来源暂时不可用，请稍后重试")
        if not image:
            self.limiter.wait(url)
        budget = self.request_context.get()
        if budget:
            budget.check()
            limit = min(limit, budget.max_bytes - budget.bytes)
        headers = {"Accept": "application/json" if signed else "*/*"}
        if signed:
            stamp = str(int(time.time()))
            middle, suffix = self._signature()
            digest = hashlib.md5((stamp + suffix).encode(), usedforsecurity=False).hexdigest()
            headers["jdsignature"] = f"{stamp}.{middle}.{digest}"
        if self.transport is None:
            self.transport = HttpxTransport()
        try:
            if budget:
                budget.requests += 1
            response = self.transport(HttpRequest("GET", url, headers), 15, limit)
        except Exception as error:
            if image:
                self.image_paused_until = self.clock() + 60
            else:
                self.paused_until = self.clock() + 60
            raise PublicSourceUnavailable("作品资料未取得，请稍后重试") from error
        if budget:
            budget.bytes += len(response.body)
        if response.status != 200:
            delay = 900 if response.status in (403, 429) else 60
            if image:
                self.image_paused_until = self.clock() + delay
            else:
                self.paused_until = self.clock() + delay
            raise PublicSourceUnavailable("作品资料来源暂时不可用，请稍后重试")
        return response

    def _signature(self) -> tuple[str, str]:
        if self.signature_parts and self.clock() < self.signature_until:
            return self.signature_parts
        html = response_text(self._get(WEB + "/"))
        entry = re.search(r'src="(/assets/index-[A-Za-z0-9_-]+\.js)"', html)
        if not entry:
            self.paused_until = self.clock() + 60
            raise PublicSourceUnavailable("作品资料来源格式未识别")
        js = response_text(self._get(WEB + entry[1]))
        middle = re.search(r'\bS1="([^"\r\n]{1,256})"', js)
        suffix = re.search(r'\bE1="([^"\r\n]{1,256})"', js)
        if not middle or not suffix:
            self.paused_until = self.clock() + 60
            raise PublicSourceUnavailable("作品资料来源格式未识别")
        self.signature_parts = middle[1], suffix[1]
        self.signature_until = self.clock() + 24 * 3600
        return self.signature_parts

    def _json(self, path: str, query=None) -> dict:
        # 签名材料取齐后再领取 API 主机间隔，首个签名请求不占用两次间隔。
        self._signature()
        response = self._get(API + path + ("?" + urlencode(query) if query else ""), signed=True)
        try:
            payload = json.loads(response.body)
        except (ValueError, UnicodeError) as error:
            self.paused_until = self.clock() + 60
            raise PublicSourceUnavailable("作品资料来源格式未识别") from error
        if not isinstance(payload, dict) or payload.get("success") != 1:
            self.paused_until = self.clock() + 60
            raise PublicSourceUnavailable("作品资料未取得，请稍后重试")
        data = payload.get("data")
        if not isinstance(data, dict):
            self.paused_until = self.clock() + 60
            raise PublicSourceUnavailable("作品资料来源格式未识别")
        return data

    def _store(self):
        root = self.cache_root() if callable(self.cache_root) else self.cache_root
        return PublicSnapshots(root, clock=self.wallclock)

    def _cache_key(self, key):
        root = self._store().root
        return f'{root.resolve()}\0{key}' if root is not None else key

    def _memory(self, cache_key, *, image, refresh):
        """持有缓存锁时读取内存命中或失败冷却，返回内容及实际来源状态。"""
        hit = self.cache.get(cache_key)
        snapshot = self.snapshots.get(cache_key)
        failed = self.failures.get(cache_key)
        cooling = failed is not None and failed[0] > self.clock()
        error = str(failed[1]) if cooling else ''
        if (not refresh and hit and hit[0] > self.clock()
                and (image or (snapshot is not None and snapshot.fresh(self.wallclock())))):
            self.cache.move_to_end(cache_key)
            return hit[1], snapshot, error
        if cooling:
            if snapshot is not None:
                return snapshot.value, snapshot, error
            raise failed[1]
        return None

    def _cached(self, key, fetch, *, released=None, refresh=False):
        cache_key = self._cache_key(key)
        image = key.startswith('image:')
        store = self._store()
        with self.lock:
            memory = self._memory(cache_key, image=image, refresh=refresh)
            if memory is not None:
                result, snapshot, error = memory
                _record_read(key, snapshot, error)
                return result
            snapshot = self.snapshots.get(cache_key)
            future = self.pending.get(cache_key)
            owner = future is None
            if owner:
                future = self.pending[cache_key] = Future()
        if not owner:
            result = future.result()
            failed = self.failures.get(cache_key)
            _record_read(key, self.snapshots.get(cache_key), str(failed[1]) if failed else '')
            return result
        previous = snapshot if snapshot is not None else None if image else store.load(key)
        try:
            with nullcontext() if image else store.acquire(key):
                disk = None if image else store.load(key)
                if disk is not None and disk.fresh(self.wallclock()) and (
                        not refresh or (previous is not None and disk.revision != previous.revision)):
                    snapshot = self._remember(key, disk.value, snapshot=disk)
                    result = disk.value
                else:
                    previous = disk or previous
                    with nullcontext() if image else self.source_lock:
                        result = fetch()
                    snapshot = self._remember(key, result, released=released)
            future.set_result(result)
            _record_read(key, snapshot)
            return result
        except PublicSourceUnavailable as error:
            if isinstance(error, PublicBudgetExceeded):
                future.set_exception(error)
                raise
            if previous is not None:
                self._remember(key, previous.value, snapshot=previous)
            with self.lock:
                self.failures[cache_key] = (self.clock() + 900, error)
            if previous is not None:
                future.set_result(previous.value)
                _record_read(key, previous, str(error))
                return previous.value
            future.set_exception(error)
            raise
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.pending.pop(cache_key, None)

    def _remember(self, key, result, *, released=None, snapshot=None):
        image = key.startswith('image:')
        cache_key = self._cache_key(key)
        if not image and snapshot is None:
            if isinstance(result, dict):
                released = result.get('release_date') or released
            released = released if isinstance(released, str) else None
            snapshot = self._store().save(key, result, released)
        lifetime = 6 * 3600 if image else cache_lifetime(snapshot.released, datetime.fromtimestamp(self.wallclock()).date())
        remaining = lifetime if image else None if lifetime is None else max(0, snapshot.saved + lifetime - self.wallclock())
        with self.lock:
            self.cache[cache_key] = (float('inf') if remaining is None else self.clock() + remaining, result)
            if snapshot is not None:
                self.snapshots[cache_key] = snapshot
            self.failures.pop(cache_key, None)
            self.cache.move_to_end(cache_key)
            while len(self.cache) > 128:
                removed, _ = self.cache.popitem(last=False)
                self.snapshots.pop(removed, None)
                self.failures.pop(removed, None)
        return snapshot

    def movie(self, code: str, *, refresh=False) -> dict:
        canonical = normalise_code_key(code)

        def identify():
            rows = self._rows("/v2/search", "movies", {"q": canonical, "type": "movie", "page": 1})
            match = next((row for row in rows if isinstance(row, dict)
                          and normalise_code_key(row.get("number")) == canonical), None)
            if not match or not isinstance(match.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9]+", match["id"]):
                raise PublicSourceUnavailable("未取得这个番号的作品资料")
            return match["id"]

        def fetch():
            movie_id = self._cached("movie-id:" + canonical, identify, refresh=refresh)
            movie = self._json("/v4/movies/" + movie_id).get("movie")
            if (not isinstance(movie, dict) or normalise_code_key(movie.get("number")) != canonical
                    or movie.get("id") != movie_id):
                raise PublicSourceUnavailable("来源作品身份不匹配")
            return movie

        return self._cached("movie:" + canonical, fetch, refresh=refresh)

    def remember_movie_identity(self, code: str, source: dict):
        """已取得的编目身份可复用，完整资料仍须通过来源的身份核对。"""
        canonical = normalise_code_key(code)
        identity = source.get("id")
        if not isinstance(identity, str) or normalise_code_key(source.get("number")) != canonical or not re.fullmatch(r"[A-Za-z0-9]+", identity):
            return
        key = 'movie-id:' + canonical
        stored = self._store().load(key)
        if stored and stored.value == identity:
            self._remember(key, identity, snapshot=stored)
        else:
            self._remember(key, identity)

    def _rows(self, path, key, query=None):
        rows = self._json(path, query).get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            self.paused_until = self.clock() + 60
            raise PublicSourceUnavailable("作品资料来源格式未识别")
        return [row for row in rows if isinstance(row, dict)]

    def peek_movie(self, code: str) -> dict | None:
        """只读已取得的资料，不触发网络；供发现排序投影。"""
        with self.lock:
            key = 'movie:' + normalise_code_key(code)
            cache_key = self._cache_key(key)
            hit = self.cache.get(cache_key)
            snapshot = self.snapshots.get(cache_key) or self._store().load(key)
            if snapshot and snapshot.fresh(self.wallclock()):
                _record_read(key, snapshot)
                return dict(snapshot.value) if isinstance(snapshot.value, dict) else None
            return dict(hit[1]) if hit and hit[0] > self.clock() and snapshot is None else None

    def reviews(self, code: str, page: int, *, refresh=False) -> list[dict]:
        if not 1 <= page <= MAX_REVIEW_PAGES:
            raise ValueError("评论页码必须在 1 至 8 之间")
        movie = self.movie(code)
        return self._cached(f"reviews:{movie['id']}:{page}", lambda:
                            self._rows(f"/v1/movies/{movie['id']}/reviews", "reviews", {"page": page}),
                            released=movie.get('release_date'), refresh=refresh)

    def media_movie(self, code: str) -> dict:
        """未缓存媒体可补齐带查询参数的临时来源地址，公开编目仍按发行日期复用。"""
        movie = self.movie(code)
        key = 'movie:' + normalise_code_key(code)
        snapshot = self.snapshots.get(self._cache_key(key)) or self._store().load(key)
        urls = [movie.get(name) for name in ('cover_url', 'thumb_url', 'preview_video_url')]
        urls.extend(actor.get('avatar_url') for actor in movie.get('actors') or [] if isinstance(actor, dict))
        urls.extend((image.get('large_url') or image.get('url')) if isinstance(image, dict) else image
                    for image in movie.get('preview_images') or [])
        if snapshot and self.wallclock() - snapshot.saved >= DAY and any(
                isinstance(url, str) and '?' in url for url in urls):
            return self.movie(code, refresh=True)
        return movie

    def magnets(self, code: str, *, refresh=False) -> list[dict]:
        movie = self.movie(code)
        return self._cached("magnets:" + movie['id'], lambda:
                            self._rows(f"/v1/movies/{movie['id']}/magnets", "magnets"),
                            released=movie.get('release_date'), refresh=refresh)

    def official_preview(self, code, fetch, *, released=None, refresh=False, provider='prestige'):
        """预告按来源独立保存；DMM、JavDB 和未提供的预告每天可再检查。"""
        def read():
            url = fetch()
            return {'url': url, **({'release_date': released} if url and provider == 'prestige' else {})}
        return self._cached(f'preview:{provider}:' + normalise_code_key(code), read, refresh=refresh)

    def image(self, code: str, index: str, *, original=False) -> str:
        movie = self.media_movie(code)
        if index == "cover":
            url = movie.get("cover_url") or movie.get("thumb_url")
        elif index.startswith("actor:"):
            actor_id = index.removeprefix("actor:")
            actor = next((actor for actor in movie.get("actors") or []
                          if isinstance(actor, dict) and str(actor.get("id")) == actor_id), None)
            if not actor:
                raise ValueError("出演者头像不存在")
            url = actor.get("avatar_url")
        else:
            previews = movie.get("preview_images") or []
            try:
                url = previews[int(index)]
            except (ValueError, IndexError, TypeError):
                raise ValueError("预览图不存在") from None
            if isinstance(url, dict):
                url = url.get("large_url") or url.get("url")
        return self.image_from_url(url, original=True) if original else self.image_from_url(url)

    def image_from_url(self, url, *, original=False) -> str:
        """静态头像与作品图片共用校验、并发下载、解码和请求缓存。"""
        if not public_image_url(url):
            raise PublicSourceUnavailable("图片未取得")

        def fetch():
            import base64
            with self.image_slots:
                body = self._get(str(url), limit=4 * 1024 * 1024, image=True).body
                decoded = decode_image(body, original=True) if original else decode_image(body)
            return "data:image/jpeg;base64," + base64.b64encode(decoded).decode("ascii")

        key = ('image:original:' if original else 'image:') + hashlib.sha256(str(url).encode()).hexdigest()
        return self._cached(key, fetch)


def decode_image(body: bytes, *, original=False) -> bytes:
    """标准图片与公开前端 XOR 包装，只接受 Pillow 完整解码的图像。"""
    candidates = [body]
    if body:
        candidates.append(bytes(value ^ body[0] for value in body[1:]))
        candidates.extend(bytes(value ^ 0x7f for value in body[skip:]) for skip in (0, 1, 2))
    for raw in candidates:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 20_000_000:
                    continue
                image.load()
                if original and image.format == 'JPEG':
                    return raw
                if not original:
                    image.thumbnail((1200, 1200))
                output = io.BytesIO()
                image.convert("RGB").save(output, "JPEG", quality=95 if original else 85)
                return output.getvalue()
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
    raise PublicSourceUnavailable("图片未取得")


PUBLIC_API = PublicAPI(cache_root=lambda: _CACHE_ROOT.get())
