"""Generate cached high-resolution covers for remote follow videos.

Paheal exposes the original video and a 150/250px thumbnail, but no larger still.
The card therefore extracts the first non-black decodable frame once and serves the
cached JPEG.
"""
from __future__ import annotations
from .user_agent import USER_AGENT

import hashlib
import io
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from .ffmpeg import FFmpegResolver
from .follow_store import FollowItemRow
from .follow_stream import FollowMediaResolver, FollowMediaUnavailable, proxyable
from .http import HttpRequest


# FFmpeg's blackframe filter exports ``lavfi.blackframe.pblack`` only when the
# configured amount is reached.  Setting amount=0 makes that percentage available
# on every frame; metadata/select can then drop frames that are at least 98% black.
# Scan at most the opening 30 seconds so an all-black/broken video still reaches the
# existing low-resolution fallback promptly.
FOLLOW_COVER_SCAN_SECONDS = 30
FOLLOW_COVER_FILTER = (
    "blackframe=amount=0:threshold=32,"
    "metadata=select:key=lavfi.blackframe.pblack:value=98:function=less,"
    "scale='min(1280,iw)':-2"
)
_CACHE_VERSION = "nonblack-v1"

#: 取不到图时回的占位图。这里不 302 到上游缩略图：那等于把上游主机和地址交回
#: 浏览器，而这个端点存在的全部理由就是不让它外露。占位图内联成
#: SVG：不占磁盘、不用打包资源，也不会因为文件缺失再失败一次。
#: 图里不写字：这一层不知道界面语言，画一个中性的播放三角就够。
PLACEHOLDER_CONTENT_TYPE = "image/svg+xml"
PLACEHOLDER_IMAGE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90">'
    '<rect width="160" height="90" fill="#1f1f22"/>'
    '<path d="M68 33 100 45 68 57 Z" fill="#4c4c52"/>'
    '</svg>'
).encode("utf-8")


class FollowCoverUnavailable(RuntimeError):
    pass


def fanbox_video_indexes(item: FollowItemRow) -> list[int]:
    """fanbox 帖子里站内托管的视频在媒体清单中的序号，能由 FFmpeg 抽帧的就是这些。"""
    return [index for index, media in enumerate(item.metadata.get("media_items") or [])
            if isinstance(media, dict) and media.get("media_kind") == "video"
            and media.get("resource_provider") == "fanbox"]


def _cache_slot(name: str, item_id: int) -> str:
    """缓存文件名里视频那一格：第一个视频是空串，点名的其他视频是 `m<序号>-`。"""
    rest = name[len(f"{item_id}-"):]
    head, dash, _ = rest.partition("-")
    return f"{head}-" if dash and head.startswith("m") else ""


class FollowCoverService:
    """Create a bounded, cached still without exposing the upstream media URL."""

    def __init__(self, resolver: FFmpegResolver, media_resolver: FollowMediaResolver,
                 root: Path, *, timeout: float = 30.0):
        self.resolver = resolver
        self.media_resolver = media_resolver
        self.root = Path(root).resolve()
        self.timeout = timeout
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        # A visible grid can ask for several lazy images together. Two workers keep the
        # first screen responsive without turning the source CDN into a batch job.
        self._slots = threading.BoundedSemaphore(2)
        # poster 只是几张静态小图的 GET，另占一组槽：上游删了片、8 个地址挨个超时的
        # 那几张卡，不该把 fanbox 和 paheal 的抽帧也堵在后面。
        self._poster_slots = threading.BoundedSemaphore(4)
        self._poster_misses: dict[str, float] = {}

    def cover(self, item: FollowItemRow, media: int | None = None, *,
              alternatives: Callable[[], Iterable[str]] | Iterable[str] = ()) -> Path:
        """条目的视频封面；`media` 点名 fanbox 帖子里的某一个视频，缺省是第一个。

        卡面要的是第一个视频，详情里的多媒体清单每个视频各要一张。第一个视频不论
        是否点名都落在同一份缓存上，卡面和清单里那一格共用一次抽帧。

        `alternatives` 是同一作品其他版本的 poster 地址，只有 rule34video 用；给可调用
        对象时只在磁盘缓存未命中后才调用，命中的请求不碰账本。
        """
        if item.provider == "rule34video" and media is None:
            return self._poster(item, alternatives)
        media_index = None
        slot = ""
        if item.provider == "fanbox":
            videos = fanbox_video_indexes(item)
            media_index = videos[0] if videos else None
            if media is not None and media != media_index:
                if media not in videos:
                    raise FollowCoverUnavailable("点名的媒体不是这篇帖子里的视频")
                media_index, slot = media, f"m{media}-"
        elif media is not None:
            raise FollowCoverUnavailable("只有 fanbox 帖子能按媒体取视频封面")
        if not (item.provider == "fanbox" and media_index is not None) and (
                item.provider != "rule34paheal"
                or str(item.metadata.get("media_kind") or "") != "video"):
            raise FollowCoverUnavailable("该条目不需要生成视频封面")
        try:
            target = (self.media_resolver.resolve(item, media_index)
                      if media_index is not None else self.media_resolver.resolve(item))
        except FollowMediaUnavailable as exc:
            raise FollowCoverUnavailable(str(exc)) from exc
        choice = self.resolver.ffmpeg()
        if choice is None:
            raise FollowCoverUnavailable("ffmpeg 不可用")

        fingerprint = hashlib.sha256(
            f"{_CACHE_VERSION}\0{target.url}".encode("utf-8")
        ).hexdigest()[:16]
        # 点名的其他视频在文件名里带 `m<序号>-`；指纹是十六进制，不会以 m 开头，
        # 所以下面按名字清旧帧时，各个视频只清自己那一格。
        destination = self.root / f"{item.id}-{slot}{fingerprint}.jpg"
        if destination.is_file():
            return destination
        # 抽帧失败回的是不缓存的占位图，下一次渲染就会再来；不记住失败，每次都要
        # 重跑一遍 FFmpeg 去读上游正片。
        if self._recent_miss(destination.name):
            raise FollowCoverUnavailable("视频封面生成失败")

        lock = self._lock_for(destination.name)
        with lock:
            if destination.is_file():
                return destination
            if self._recent_miss(destination.name):
                raise FollowCoverUnavailable("视频封面生成失败")
            self._extract_frame(choice, target, destination)
            # URL 变化时留下旧帧没有价值；只清理同一条目、同一个视频的旧缓存。
            for stale in self.root.glob(f"{item.id}-*.jpg"):
                if stale != destination and _cache_slot(stale.name, item.id) == slot:
                    stale.unlink(missing_ok=True)
            return destination

    def _extract_frame(self, choice, target, destination: Path) -> None:
        """用 FFmpeg 从上游正片抽一帧写到 `destination`；失败记一次，限时内不再重跑。"""
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f"{destination.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
        command = [
            str(choice.path), "-y", "-v", "error",
            "-threads", "2", "-filter_threads", "1",
            "-rw_timeout", "15000000", "-user_agent", USER_AGENT,
        ]
        if target.referer:
            command.extend(("-referer", target.referer))
        command.extend((
            "-t", str(FOLLOW_COVER_SCAN_SECONDS), "-i", target.url,
            "-frames:v", "1", "-threads", "2", "-vf", FOLLOW_COVER_FILTER, "-update", "1",
            "-q:v", "4", str(temporary),
        ))
        try:
            with self._slots:
                result = subprocess.run(
                    command, capture_output=True, timeout=self.timeout, check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if (result.returncode != 0 or not temporary.is_file()
                    or temporary.stat().st_size == 0):
                self._remember_miss(destination.name)
                raise FollowCoverUnavailable("视频封面生成失败")
            os.replace(temporary, destination)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._remember_miss(destination.name)
            raise FollowCoverUnavailable("视频封面生成失败") from exc
        finally:
            temporary.unlink(missing_ok=True)

    #: 一组 poster 地址全部取不到、或一次抽帧失败后，多久内不再重试。上游删片是长期
    #: 状态，这里只挡住同一屏每次渲染都把 8 个地址挨个请求一遍、或把 FFmpeg 再跑一遍。
    POSTER_MISS_TTL = 300.0

    def _poster(self, item: FollowItemRow,
                alternatives: Callable[[], Iterable[str]] | Iterable[str]) -> Path:
        """缓存来源 poster；失效时取同一作品其他版本的封面，只请求静态图片。

        缓存文件按条目自己的 poster 地址命名：同作品别的版本增减不改名，命中时不用
        先去账本里找同组版本。
        """
        poster_key = hashlib.sha256(
            f"poster\0{item.thumb_url or ''}".encode("utf-8")).hexdigest()[:16]
        destination = self.root / f"{item.id}-poster-{poster_key}.jpg"
        if destination.is_file():
            return destination
        others = alternatives() if callable(alternatives) else alternatives
        urls = tuple(url for url in dict.fromkeys((item.thumb_url or "", *others))
                     if proxyable("rule34video", url))[:8]
        if not urls:
            raise FollowCoverUnavailable("该作品没有可用的封面地址")
        miss_key = hashlib.sha256("\0".join(urls).encode("utf-8")).hexdigest()
        if self._recent_miss(miss_key) or self.media_resolver.cooling(item.provider):
            raise FollowCoverUnavailable("该作品的封面未取得")
        with self._lock_for(destination.name):
            if destination.is_file():
                return destination
            if self._recent_miss(miss_key) or self.media_resolver.cooling(item.provider):
                raise FollowCoverUnavailable("该作品的封面未取得")
            with self._poster_slots:
                fetched = self._fetch_poster(item, urls, destination)
            if fetched is None:
                # 来源在冷却：不是这张图的问题，冷却结束后照常再取，不记失败。
                raise FollowCoverUnavailable("来源限流冷却中")
            if not fetched:
                self._remember_miss(miss_key)
                raise FollowCoverUnavailable("该作品的封面未取得")
            # 自己的 poster 地址变了才会换名；同一条目只留当前这一份。
            for stale in self.root.glob(f"{item.id}-poster-*.jpg"):
                if stale != destination and not stale.name.endswith(".tmp.jpg"):
                    stale.unlink(missing_ok=True)
            return destination

    def _recent_miss(self, key: str) -> bool:
        with self._guard:
            missed = self._poster_misses.get(key)
            if missed is None:
                return False
            if time.monotonic() - missed < self.POSTER_MISS_TTL:
                return True
            del self._poster_misses[key]
            return False

    def _remember_miss(self, key: str) -> None:
        with self._guard:
            if len(self._poster_misses) >= self.MAX_TRACKED_LOCKS:
                now = time.monotonic()
                for name, missed in list(self._poster_misses.items()):
                    if now - missed >= self.POSTER_MISS_TTL:
                        del self._poster_misses[name]
                while len(self._poster_misses) >= self.MAX_TRACKED_LOCKS:
                    del self._poster_misses[next(iter(self._poster_misses))]
            self._poster_misses[key] = time.monotonic()

    def _fetch_poster(self, item: FollowItemRow, urls: tuple[str, ...],
                      destination: Path) -> bool | None:
        """按次序取第一张能解码的 poster，转成 JPEG 原子落到 `destination`。

        请求经解析器的来源闸门发出：来源一回 403 / 429 就整站冷却，剩下的地址不再
        挨个去试，返回 None。
        """
        deadline = time.monotonic() + self.timeout
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f"{destination.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
        try:
            for url in urls:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    response = self.media_resolver.fetch(item.provider, HttpRequest("GET", url, {
                        "User-Agent": USER_AGENT, "Accept": "image/*", "Referer": item.url or "",
                    }), min(8.0, remaining), 4_000_000)
                    if self.media_resolver.cooling(item.provider):
                        return None
                    if response.status != 200 or len(response.body) > 4_000_000:
                        continue
                    if response.url and not proxyable("rule34video", response.url):
                        continue
                    with Image.open(io.BytesIO(response.body)) as image:
                        if image.width * image.height > 16_000_000:
                            continue
                        image.seek(0)
                        image.thumbnail((1280, 1280))
                        image.convert("RGB").save(temporary, "JPEG", quality=90)
                    os.replace(temporary, destination)
                    return True
                except FollowMediaUnavailable:
                    return None
                except (OSError, ValueError, httpx.HTTPError, UnidentifiedImageError, Image.DecompressionBombError):
                    continue
        finally:
            temporary.unlink(missing_ok=True)
        return False

    #: 同时追踪多少把生成锁。键是带指纹的缓存文件名，条目一多、URL 一变就再加一条，
    #: 只增不减的话，进程活多久它就长多久。超过上限就丢掉当前没人持有的键——丢锁最
    #: 坏只是让两次并发生成各跑一遍 ffmpeg，`os.replace` 仍是原子的，不会出错图。
    MAX_TRACKED_LOCKS = 256

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                if len(self._locks) >= self.MAX_TRACKED_LOCKS:
                    for name, tracked in list(self._locks.items()):
                        if not tracked.locked():
                            del self._locks[name]
                lock = self._locks[key] = threading.Lock()
            return lock
