"""采集任务的社区来源：AVBase、JavBus、javdb 与 JavLibrary 的顺序及封面互证。

官方渠道（r18.dev、DMM CDN、MGS、Prestige）落空时才问社区来源，决策见 ADR-0030 与 ADR-0032。
各家都是 `peach.sources` 契约下的一个站：
取页、解析、防封间隔、Cookie 与页面上限都在那里，`LibraryMetadataProvider.community` 与
`scripts/scrape_codes.py` 按 `SITE_SOURCES` 取站。几家的值常有出入：ABW-358 在 javdb 上发行日期是
MGS 的 5/23、标题带 MGS 附注、演员里有男优，所以社区来源的资料一律要两家一致才免复核。

封面比对用 dHash：两张图宽高比相差不超过 4%、64 位指纹相差不超过 10 位就算同一张。
对不上时再比一次特征点（`same_scene`）：javdb 给 FC2 的封面是同一张商品图另取景的
276×276 方图，JavArchive 转存的是竖版，整图指纹两边差 39 位，特征点却有近三百个对得上。
同一张图出现在两个不同的图源（DMM、DUGA、MGS、JavBus、javdb 各算一个）就算印证；官方渠道
只取到小图时，那张小图也参加比对。可用的图全出自一个图源、没有别家可比时，取其中最大的
那张，印证图源留空（ADR-0032）；两个图源各给了图却对不上，是有证据的冲突，不用。
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import httpx
from PIL import Image

from .jav_cover_fetch import (SMALL_MIN_WIDTH, Candidate, NotFound, Unavailable, _fetch, candidate_for,
                              is_cross_product_cover, is_javdb_image)
from .scraping_access import SourcePaused
from .scripting import host_under, hostname_of
from .sources import AVBASE, JAVBUS, JAVDB, JAVLIBRARY

IMAGE_LIMIT = 16 * 1024 * 1024

#: 图源按店铺算，不按主机名：`pics.dmm.co.jp` 与 `awsimgsrc.dmm.co.jp` 是同一家的两条路径。
IMAGE_ORIGINS = (("dmm", ("dmm.co.jp", "dmm.com")), ("duga", ("duga.jp",)),
                 ("mgstage", ("mgstage.com",)), (JAVBUS.name, JAVBUS.domains), (JAVDB.name, JAVDB.domains),
                 (JAVLIBRARY.name, JAVLIBRARY.domains))
#: 社区站自己的图要带站内 Referer；店铺的图按 `candidate_for` 取官方 Referer。
ORIGIN_REFERERS = {JAVBUS.name: JAVBUS.referer, JAVDB.name: JAVDB.referer, JAVLIBRARY.name: JAVLIBRARY.referer}
ASPECT_TOLERANCE = 0.04
HASH_DISTANCE = 10
#: 两张图按一个相似变换对上的特征点至少这么多才算同一张。
SCENE_INLIERS = 60
#: 算特征点前把长边缩到这么长：几百像素已经够认，原图 3000 多像素只是白算。
SCENE_SIDE = 400

#: 社区来源按顺序补齐；JavLibrary 的人工验证会话在有码链中作为备用。
COMMUNITY_SOURCES = (AVBASE.name, JAVBUS.name, JAVDB.name, JAVLIBRARY.name)


def community_sources_for(code: str, *hints: str, route=None):
    """这个番号问社区里的哪几家，顺序同这个番号的来源链。

    成员与顺序的真相在 `metadata_routes`：有码问四个目录，素人问 AVBase、JavBus 与 javdb，
    FC2 的商品号只问 javdb，韩国 MIB 一家都不问。`route` 是调用方已经算好的那一档成员
    （算它要本机路径与厂牌证据，这里手上没有），给了就直接用。

    FC2 只问 javdb 的依据：2026-09-22 清点本机攒下的 1213 份来源证据，AVBase 那 86 份、
    JavBus 那 37 份全是厂牌番号，对 FC2 番号一份都没给过，javdb 则给了 166 份。问了只是
    各撞一次空搜索，白花两家的配额，还多两次被 Cloudflare 记上的机会。

    省不出时间是预料之中的：`HostLimiter` 等的是「距上次满 3 秒」（`library_processing.SOURCE_INTERVALS`
    里 javdb 的主机间隔），这两家的往返本来就落在等 javdb 的窗口里。一轮采集的长短由 javdb 的请求数乘
    3 秒定死，这里省的是请求次数。
    """
    from .metadata_routes import community_route
    members = community_route(code, *hints) if route is None else tuple(route)
    return tuple(name for name in COMMUNITY_SOURCES if name in members)


def origin_of(url: str) -> str:
    host = hostname_of(url)
    return next((name for name, domains in IMAGE_ORIGINS if host_under(host, domains)), host)


def _candidate(url: str) -> Candidate:
    origin = origin_of(url)
    if origin in ORIGIN_REFERERS:
        return Candidate(hostname_of(url), url, ORIGIN_REFERERS[origin])
    return candidate_for(url)


@dataclass(frozen=True)
class Picture:
    candidate: Candidate
    size: tuple[int, int]
    data: bytes
    fingerprint: int

    @property
    def origin(self) -> str:
        return origin_of(self.candidate.url)

    @property
    def pixels(self) -> int:
        return self.size[0] * self.size[1]


def fingerprint(image: Image.Image) -> int:
    """dHash：缩到 9×8 灰度，逐行比较相邻像素。重压缩与缩放不改它，换一张图就变。"""
    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = bits << 1 | (pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return bits


def picture(candidate: Candidate, data: bytes) -> Picture:
    """下载到的一张图。动图不算封面：JavArchive 的图床给 FC2 存的常是 GIF 预览动画，
    落进 `.jpg` 之后卡片就一直在动。"""
    with Image.open(io.BytesIO(data)) as image:
        if getattr(image, "is_animated", False):
            raise ValueError(f"{candidate.url} 是动图")
        image.load()
        return Picture(candidate, image.size, data, fingerprint(image))


def same_picture(one: Picture, other: Picture) -> bool:
    ratio, other_ratio = one.size[0] / one.size[1], other.size[0] / other.size[1]
    if (abs(ratio - other_ratio) <= ASPECT_TOLERANCE * ratio
            and bin(one.fingerprint ^ other.fingerprint).count("1") <= HASH_DISTANCE):
        return True
    return same_scene(one.data, other.data)


def _scene_features(data: bytes):
    import cv2
    import numpy

    image = cv2.imdecode(numpy.frombuffer(data, numpy.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None or not image.size:
        return None
    scale = SCENE_SIDE / max(image.shape)
    if scale < 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.ORB_create(1000).detectAndCompute(image, None)


def same_scene(data: bytes, other: bytes) -> bool:
    """两张图是不是同一张照片的不同取景：ORB 特征点按相似变换对上 `SCENE_INLIERS` 个以上。

    整图指纹管不了取景不同：裁成方图、缩成竖图、套上水印框，dHash 就全变了。相似变换
    只许平移、等比缩放与旋转，两张不同的照片凑不出几十个同时满足它的点；同一场拍摄的
    两帧会有少量重合（背景、床单），所以门槛远高于实测的那几个。缺 vision 依赖组时退回
    只认整图指纹。
    """
    try:
        import cv2
        import numpy
    except ImportError:                         # pragma: no cover - 缺 vision 依赖组
        return False
    first, second = _scene_features(data), _scene_features(other)
    if not first or not second or first[1] is None or second[1] is None:
        return False
    (points, descriptors), (other_points, other_descriptors) = first, second
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(descriptors, other_descriptors, k=2)
    good = [best for best, runner in (pair for pair in pairs if len(pair) == 2)
            if best.distance < 0.75 * runner.distance]
    if len(good) < SCENE_INLIERS:
        return False
    source = numpy.float32([points[match.queryIdx].pt for match in good])
    target = numpy.float32([other_points[match.trainIdx].pt for match in good])
    _, mask = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC,
                                          ransacReprojThreshold=4)
    return mask is not None and int(mask.sum()) >= SCENE_INLIERS


def _download(transport, candidate: Candidate, *, deadline: float | None) -> tuple[Picture | None, bool]:
    """一张候选图：下载得到、宽度够小图门槛的静态图，再报下载到没有。"""
    try:
        data = _fetch(transport, candidate.url, referer=candidate.referer, limit=IMAGE_LIMIT, deadline=deadline)
    except (NotFound, Unavailable, SourcePaused, httpx.TransportError, OSError):
        return None, False
    try:
        found = picture(candidate, data)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None, True
    return (found if found.size[0] >= SMALL_MIN_WIDTH else None), True


def _pictures(transport, urls: dict[str, Candidate], reference, siblings: tuple[Candidate, ...], *,
              deadline: float | None) -> tuple[list[Picture], int, int]:
    """官方小图与它的同页图（有的话）加上社区来源里能用的图；再报社区来源下载到了几张、能用几张。

    下载到了却不能用（动图、太窄）和没下载到是两回事：前者再问一遍还是这几张。
    """
    pool = [picture(reference[0], reference[2])] if reference is not None else []
    seen = {pool[0].candidate.url} if pool else set()
    for candidate in siblings:
        if candidate.url not in seen and candidate.url not in urls:
            seen.add(candidate.url)
            found, _ = _download(transport, candidate, deadline=deadline)
            pool += [found] if found else []
    official = len(pool)
    fetched = 0
    for url, candidate in urls.items():
        if url in seen:
            continue
        found, downloaded = _download(transport, candidate, deadline=deadline)
        fetched += downloaded
        pool += [found] if found else []
    return pool, fetched, len(pool) - official


def _unverified(pool: list[Picture], usable: int,
                fetched: int) -> tuple[Candidate, tuple[int, int], bytes, tuple[str, ...]]:
    """没有两个图源对得上时的退路：图全出自一个图源就取最大那张，印证图源留空。

    `usable` 是池子里社区来源那几张，不算官方小图与它的同页图。"""
    if not usable:
        if fetched:
            raise NotFound("社区来源给的封面都是动图或太小")
        raise Unavailable("社区来源的封面下载失败")
    origins = sorted({one.origin for one in pool})
    if len(origins) > 1:
        # 几家都答了、图都下到了，只是互相对不上：再问还是这几张，交 `NotFound` 让采集任务记住。
        raise NotFound(f"{'、'.join(origins)} 给的封面不是同一张图，无法互相印证")
    allowed = [one for one in pool if not is_javdb_image(one.candidate.url)]
    if not allowed:
        raise NotFound('未取得无 JavDB 水印的封面')
    largest = max(allowed, key=lambda one: one.pixels)
    return largest.candidate, largest.size, largest.data, ()


def verified_cover(transport, code: str, works: list[tuple[str, dict]], *,
                   reference: tuple[Candidate, tuple[int, int], bytes] | None = None,
                   siblings: tuple[Candidate, ...] = (),
                   deadline: float | None = None) -> tuple[Candidate, tuple[int, int], bytes, tuple[str, ...]]:
    """社区来源给的封面里，至少两个图源对得上的最大那张；返回值末尾是参与印证的图源。

    `reference` 是官方渠道取到的小图，它也算一个图源：javdb 的大图和 DMM 的小图是同一张
    时，大图就有了官方印证。没有第二个图源可比时按 `_unverified` 取图，末尾为空。

    `siblings` 是官方渠道同一页上的其余几张，也下下来参加比对。官方那一档按尺寸挑出来的
    未必是封面：JavArchive 给 `FC2-PPV-3264420` 转存了 605×364 的正片截图、菱形水印框、
    GIF 预览和 510×616 的竖版商品图，最宽的是截图，和 javdb 那张方图对得上的是竖版。
    """
    urls = {url: _candidate(url) for _source, payload in works for url in payload.get("cover_urls") or []
            if not is_cross_product_cover(code, url)}
    if not urls:
        raise NotFound("社区来源没有这部片的封面")
    siblings = tuple(one for one in siblings if not is_cross_product_cover(code, one.url))
    pool, fetched, usable = _pictures(transport, urls, reference, siblings, deadline=deadline)
    best = None
    for one in pool:
        if is_javdb_image(one.candidate.url):
            continue
        origins = {other.origin for other in pool if same_picture(one, other)}
        if len(origins) >= 2 and (best is None or one.pixels > best[0].pixels):
            best = (one, tuple(sorted(origins)))
    if best is None:
        return _unverified(pool, usable, fetched)
    return best[0].candidate, best[0].size, best[0].data, best[1]
