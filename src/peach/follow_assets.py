"""作者头像与来源图标的本地缓存。

头像和站点图标是元数据：一张几 KB 到十几 KB，跟着来源走，不像视频和图片那样大到
不能存。保存在本机有三个好处：页面不再让浏览器直接向对方站点要图（不泄露正在看
谁的关注）；站点抽风、限流或改版时旧图还在，不会满屏碎图；每次开页也不必再打一遍
别人的服务器。

地址永远由服务端从固定表或固定主机拼出来，不接受前端递过来的 URL——否则这里就是
一个任意地址抓取的口子。取回的字节要先能按图片认出来才落盘：站点回的机器人质询页、
错误页和整页 HTML 都不算图。

头像保鲜期为 90 天，favicon 为 365 天；到期后下次显示时重取。
重取失败继续用已有图片，并在 `RETRY_SECONDS` 内不再反复去试。
"""
from __future__ import annotations

import hashlib
import math
import os
import time
from pathlib import Path
from typing import Callable

import httpx
from filelock import FileLock

from .follow_sources import KemonoConnector
from .images import measure_image_size
from .fsutil import atomic_path
from .user_agent import USER_AGENT

#: 落在 `generated/` 下的目录名。头像与来源图标各占一个子目录，方便按类清理。
ROOT_NAME = "follow-assets"
#: 元数据图片的体积上限。头像 160×160 的 webp 十几 KB、favicon 几 KB；超过这个数的
#: 不是头像，多半是站点回了整页 HTML 或一张海报。
MAX_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT = 8.0
#: 一次重取失败后隔多久再试。关注页一屏几十张头像，站点挂掉时不能每次重绘都打几十枪。
RETRY_SECONDS = 3600

#: 各来源自己声明的站点图标。地址只在服务端；页面拿到的是 `/source-icon?provider=`。
#: simpcity 的 `/favicon.ico` 是 404，站点声明的图标在 `/data/assets/logo/` 下。
SOURCE_ICON_URLS: dict[str, str] = {
    "fanbox": "https://www.fanbox.cc/favicon.ico",
    "patreon": "https://www.patreon.com/favicon.ico",
    "subscribestar": "https://assets.subscribestar.com/assets/public/images/favicons/"
                     "favicon-32x32-b9aa1e7e5bab6cb1b28b5161e16f9d42.png",
    "kemono": "https://kemono.cr/assets/favicon-CPB6l7kH.ico",
    "coomer": "https://coomer.st/assets/favicon-CPB6l7kH.ico",
    "pawchive": "https://pawchive.pw/static/favicon.png",
    "rule34video": "https://rule34video.com/favicon-32x32.png",
    "rule34xxx": "https://rule34.xxx/favicon.ico",
    "rule34paheal": "https://rule34.paheal.net/favicon.ico",
    "gofile": "https://gofile.io/favicon.ico",
    "f95zone": "https://f95zone.to/assets/favicon-32x32.png",
    "simpcity": "https://simpcity.cr/data/assets/logo/favicon.png",
}

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"BM", "image/bmp"),
)


def sniff(data: bytes) -> str | None:
    """字节 → 图片 MIME；认不出就是 None。"""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    head = data[:2048].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "image/svg+xml"
    return None


def content_type(path: Path) -> str:
    """已落盘文件的 MIME。落盘前已经 `sniff` 过，这里认不出只可能是文件被人改了。"""
    with path.open("rb") as handle:
        return sniff(handle.read(2048)) or "application/octet-stream"


def mirror_avatar_url(provider: str, ref: str) -> str | None:
    """归档站上的作者头像地址。**只有实测拿得到的来源才给，取不到就是 `None`。**

    2026-08-27 实测（`curl`，不带凭据）：
    `https://kemono.cr/icons/fanbox/30917150` → 302 → `img.kemono.cr`，
    200 `image/webp` 160×160；`pawchive.pw` 同路径 200、14,534 字节。
    coomer.st 对这个创作者回 404，但那只说明他不在 coomer 上，
    不能据此断定 coomer 没有这个端点——所以 coomer 照样按同一规则给 URL，
    取不到时页面退回作者首字母。

    rule34video / rule34.xxx **未取得**：没有可用的作者页样本可测，不猜一个路径。
    """
    if provider not in KemonoConnector.HOSTS:
        return None
    service, _, user = str(ref or "").partition("/")
    if not service or not user:
        return None
    return f"https://{KemonoConnector.HOSTS[provider]}/icons/{service}/{user}"


def cache_path(root: Path, kind: str, key: str) -> Path:
    digest = hashlib.sha256(f"{kind}:{key}".encode("utf-8")).hexdigest()[:32]
    return root / kind / f"{digest}.img"


def is_fresh(path: Path, ttl: int | None, now: float | None = None) -> bool:
    """`ttl` 为 None 表示从不重取：文件在就算新鲜。"""
    try:
        age = (now if now is not None else time.time()) - path.stat().st_mtime
    except OSError:
        return False
    return ttl is None or 0 <= age < ttl


def largest_image(client: httpx.Client, tier_lists: list[list[str]]) -> bytes | None:
    """几份头像候选里留像素最多的那张。

    每一份按档位从大到小排好，取到第一张能用的就停；几份之间比实际像素，不比档位名
    ——`_400x400` 那档在原图更小时回的仍是原图。一张都取不到回 None。
    """
    best, best_area = None, 0
    for tiers in tier_lists:
        for url in tiers:
            body = fetch_image(client, url)
            if body is None:
                continue
            size = measure_image_size(body)
            if size and size[0] * size[1] > best_area:
                best, best_area = body, size[0] * size[1]
            break
    return best


def fetch_image(client: httpx.Client, url: str) -> bytes | None:
    """取一张图。不是 200、不是图、太大、网络出错都返回 None。不带任何凭据。"""
    try:
        response = client.get(
            url, headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.5"},
            timeout=FETCH_TIMEOUT, follow_redirects=True)
    except (OSError, httpx.HTTPError):
        return None
    body = response.content
    if response.status_code != 200 or not body or len(body) > MAX_BYTES:
        return None
    return body if sniff(body) else None


#: 圆标落盘时的长边。定这个数的不是圆标有多大，是圆标里那张脸能放大到多少：页面按
#: 人脸取景放大，上限之一是「源图里那张脸有多少像素」，越过去就是上采样。实测本库的
#: 代表图，脸框在 256px 那份里只剩 19～30px，而 28px 的圆在双倍屏上要 26px——正好
#: 卡在天花板上，放大被这一条压住。翻一倍就都过线了，整排也不过一两兆。
ICON_SIDE = 512

#: 落盘那张图里脸框该有的最少像素。28px 的圆 × 双倍屏 × 脸最多占六成 = 33.6，取整。
#: 到了这个数，页面那条「不许上采样」的线就不再是瓶颈，放大由构图上限说了算。
FACE_PX_IN_STORE = 34

#: 落盘长边的上限。远景全身图里一张脸只占画面百分之三四，按上面那条算要两千像素——
#: 那是一枚两百 KB 的圆标。到这里就停，剩下的交给「放不大就不放」。
MAX_ICON_SIDE = 1024

#: 判定黑边的两条线：这一行的平均亮度和最亮的那个像素。只有两条都低才算黑边。
#: 只看平均值会把夜景整片暗部当黑边裁掉，只看最大值则会被一颗噪点挡住。
_LETTERBOX_MEAN, _LETTERBOX_PEAK = 6, 24

#: 裁到只剩这么少就不裁了。真的是黑边的话裁不掉多少；裁掉一大半只说明判据认错了东西。
_LETTERBOX_KEEP = 0.4


def icon_side(record: object) -> int:
    """按人脸记录算这张图该存多大。没有脸就走 `ICON_SIDE` 那一档。

    判据只有一条：落盘那张里脸框得有 `FACE_PX_IN_STORE` 个像素。脸在画面里占得越
    小，要留住同样多的脸像素就得存得越大——一张 16:9 远景图里脸只占宽的 4.5%，存成
    512px 时脸框只剩 23px，页面想放大到看得清就撞上采样那条线了。
    """
    if not isinstance(record, dict):
        return ICON_SIDE
    face = record.get("face")
    size = record.get("px")
    if not isinstance(face, dict) or not isinstance(size, (list, tuple)) or len(size) != 2:
        return ICON_SIDE
    try:
        width = float(face.get("w") or 0)
        image_width, image_height = int(size[0]), int(size[1])
    except (TypeError, ValueError):
        return ICON_SIDE
    face_pixels = width * image_width
    if not (face_pixels > 0 and image_width > 0 and image_height > 0):
        return ICON_SIDE
    # `side` 说的是长边，脸框宽却是按宽度归一化的：竖图要先把两者换算到同一根轴上。
    longest = max(image_width, image_height)
    need = FACE_PX_IN_STORE * longest / face_pixels
    return int(min(MAX_ICON_SIDE, max(ICON_SIDE, math.ceil(need))))


def trim_letterbox(payload: bytes) -> bytes:
    """裁掉图自己带的黑边；没有黑边、裁不动或解不开就原样返回。

    站点上的 3D 作品封面常是 21:9 的画面压进 16:9 的帧里，上下各留一道纯黑。那两道
    黑边跟着进圆标是两重损失：圆里直接露出黑条，而且它们把画面撑高、让 cover 把脸缩
    得更小。裁在检脸之前——脸的坐标是归一化的，裁完再检才落在这张图自己的坐标系里。
    """
    try:
        import numpy

        from .face_detect import decode

        image = decode(payload)
        if image is None:
            return payload
        height, width = image.shape[:2]
        grey = image if image.ndim == 2 else image.max(axis=2)
        rows = _content_span(grey, height, axis=1)
        columns = _content_span(grey, width, axis=0)
        if rows is None or columns is None:
            return payload
        top, bottom = rows
        left, right = columns
        if (bottom - top) == height and (right - left) == width:
            return payload
        if (bottom - top) < height * _LETTERBOX_KEEP or (right - left) < width * _LETTERBOX_KEEP:
            return payload
        import cv2

        ok, buffer = cv2.imencode(".jpg", numpy.ascontiguousarray(image[top:bottom, left:right]),
                                  [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        return bytes(buffer) if ok else payload
    except Exception:               # 缺 OpenCV／numpy、解不开的图：原样存总好过不存
        return payload


def _content_span(grey, length: int, axis: int) -> tuple[int, int] | None:
    """沿一根轴找出「不是黑边」的那一段，返回半开区间；整幅都黑就是 None。"""
    means = grey.mean(axis=axis)
    peaks = grey.max(axis=axis)
    lit = (means > _LETTERBOX_MEAN) | (peaks > _LETTERBOX_PEAK)
    found = [index for index, value in enumerate(lit) if value]
    if not found:
        return None
    return found[0], min(length, found[-1] + 1)


def image_size(payload: bytes) -> tuple[int, int] | None:
    """一串图片字节的宽高；解不开就是 None。"""
    try:
        from .face_detect import decode

        image = decode(payload)
        if image is None:
            return None
        height, width = image.shape[:2]
        return int(width), int(height)
    except Exception:               # 缺 OpenCV、解不开的图
        return None


def shrink_image(payload: bytes, side: int = ICON_SIDE) -> bytes:
    """把一张图缩到长边不超过 `side` 的 JPEG；已经够小或缩不动就原样返回。

    题材圆标的代表图取的是站点的高清封面——250px 的缩略图里一张脸只剩十几个像素、
    检不出来。检脸看到的始终是那张高清的；归一化的取景跟着比例走，缩放不动它。
    落盘这一份按页面放大到头时需要多少像素定（`ICON_SIDE`），不是按圆标那 28px 定。
    """
    try:
        import cv2

        from .face_detect import decode

        image = decode(payload)
        if image is None:
            return payload
        height, width = image.shape[:2]
        longest = max(height, width)
        if not longest or longest <= side:
            return payload
        scale = side / longest
        small = cv2.resize(image, (max(1, round(width * scale)),
                                   max(1, round(height * scale))),
                           interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        return bytes(buffer) if ok else payload
    except Exception:               # 缺 OpenCV、解不开的图：存原图总好过不存
        return payload


def cached_image(root: Path, kind: str, key: str, ttl: int | None,
                 fetch: Callable[[], bytes | None], now: float | None = None) -> Path | None:
    """本地那份能用就用；到期了先重取，取不到继续用旧的；从没取到过才是 None。

    重取失败留一个 `.failed` 标记，`RETRY_SECONDS` 内不再出网——等站点恢复的这段时间里
    页面照常显示旧图，只是不再每次重绘都去碰它。
    """
    path = cache_path(root, kind, key)
    if is_fresh(path, ttl, now):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(".lock")):
        if is_fresh(path, ttl, now):
            return path
        marker = path.with_suffix(".failed")
        if is_fresh(marker, RETRY_SECONDS, now):
            return path if path.exists() else None
        body = fetch()
        if body:
            with atomic_path(path) as partial:
                partial.write_bytes(body)
            _stamp(path, now)
            marker.unlink(missing_ok=True)
            return path
        marker.write_bytes(b"")
        _stamp(marker, now)
        return path if path.exists() else None


def _stamp(path: Path, now: float | None) -> None:
    """新鲜度按 mtime 算，所以调用方给了「现在」就把 mtime 也定在那一刻，两边用同一个钟。"""
    if now is not None:
        os.utime(path, (now, now))
