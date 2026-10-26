"""外链图标：把站点自己最好的那份图标资产做成资料页上能看的一枚圆标。

用户指出资料页上的 favicon 不好看，并给了 beeg 的做法作参照——
`cdn.../tag_full/34.svg` 是一块品牌色实心底 + 白色 logo 路径。那是 beeg 逐个平台
手工做的 SVG；我们有八十多个不同主机，只能自动近似。

## 两条通道

站点给出的东西分两类，各走各的路，别混：

**成品图标**（apple-touch-icon、manifest icon、`type=image/svg+xml` 的 icon）本身就是
设计好的方形标识——threads 那枚黑底圆角白字就是。这种**原样用**，只补成方图。CSS 的
`.entitylinkicon` 已经是 `border-radius:50%` + `overflow:hidden`，圆是浏览器裁的，
服务端再画一次圆只会把人家设计好的底色换掉。

**字形 favicon**（透明背景 + 单色字形，T-POWERS、FANZA 的「F」都是）不能原样用：透明
背景会露出容器的灰底。这种才做「品牌色圆底 + 白色主体」。

判据仍是「单色字形 + 有真透明」。实测三种形态：

    T-POWERS   48×48，带透明通道，不透明像素只有一种绿   → alpha 就是现成遮罩，效果好
    HEYZO      32×32，全不透明，白底 + 品牌粉的多色字形   → 抠图会反转，出来一团糟
    MOODYZ     favicon 根本不是有效图片                  → 什么都做不了

## 锯齿是怎么来的，又是怎么没的

2026-09-02 用户指出 `/link-mark?id=753`（FANZA）的「F」边缘全是台阶。原因有三层，
少修一层都还是毛的：

1. 遮罩是 `alpha >= 128` 的**二值**图。源图 alpha 本来带抗锯齿的中间值，这一刀全砍掉了。
2. 这张二值图在源分辨率（48×48）上生成，再拉到 64——把台阶一起放大。
3. 字形没有被圆裁，白色像素直接溢到圆外，圆边上多一圈啃出来的缺口。

现在：alpha 原样当连续遮罩用（`getbbox()` 定位内容，不再二值化），整套合成在 8 倍
超采样的画布上做完再一次性缩下来，字形先与圆做 `composite` 再贴。另外把成品尺寸从
64 提到 128——容器是 32 px，3x 屏要 96 px，64 在高分屏上本来就不够。

服务端做还有两个附带好处：浏览器不再直接向对方站点发请求（不泄露正在看谁的资料页），
以及结果可以按主机缓存，不必每次开页都去取一遍。
"""
from __future__ import annotations

import colorsys
import hashlib
import io
import time
from pathlib import Path
from urllib.parse import urlsplit

#: 容器 32 px，3x 屏要 96 px。64 是这次之前的值，在高分屏上本来就不够。
MARK_SIZE = 128
#: 合成时的超采样倍数。圆边和字形边都靠它拿到中间灰阶。
SUPERSAMPLE = 8
#: 字形在圆内占的比例。圆的内接正方形是 0.707D，留出呼吸空间后取 0.60。
GLYPH_INSET = 0.60
#: 主体像素的色相要多集中才算「单色字形」。实测 T-POWERS 是 1 簇，HEYZO 是 4 簇。
MAX_HUE_BUCKETS = 2
#: 透明像素要占到这么多，alpha 才算真的是遮罩而不是压缩噪点。
MIN_TRANSPARENT = 0.15
#: 内容外接框的长宽比超过这个就不是标识而是字标，塞进 32 px 圆里必然糊。
#: FANZA 的 `apple-touch-icon/fanza.png` 是 200×200 画布装一条约 4:1 的「FANZA」，
#: 正是这道闸门要挡的东西——它属于厂牌页那个大 logo 位，不属于小圆标。
MAX_CONTENT_ASPECT = 2.2
#: 位图小于这个边长就不算成品图标，只能当字形或原样回落。
MIN_DESIGNED_SIZE = 96
#: SVG 光栅化的边长。取够大，缩到 128 时还有余量。
VECTOR_RASTER_SIZE = 512
CACHE_TTL = 30 * 24 * 3600


#: 渲染版本。改了取图规则或合成方式就加一。
#:
#: 缓存保鲜期是 30 天，不换键的话即使代码换了，用户看到的仍是旧那一张——2026-09-02
#: 这一轮正好是把糊的换成清楚的，靠等 30 天过期说不通。1 是候选发现之前的老口径。
#: 3：`site_icons.HOST_OVERRIDES` 加了 `fc2.com`，FC2 的取图规则变了。键里没有主机
#: 覆盖表的内容，只能整体换版本；代价是所有主机各重取一次，比让 FC2 挂着旧的 16×16
#: 再显示一个月划算。
#: 4：用户否决了 App Store 那枚 FC2 图标（背景带胶片图案），`fc2.com` 换成用户指定的
#: 400×400 纯独角兽，取图规则又变了一次。
#: 5：透明底的纯黑或纯白字形也做成圆底白字（`single_tone`），此前原样放行，深色页面上看不见。
RENDER_VERSION = 5


def cache_key(url: str) -> str:
    """按主机缓存，不按完整地址——同一个站的每条链接共用一枚图标。"""
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    if not host:
        return ""
    return hashlib.sha256(f"{RENDER_VERSION}:{host}".encode("utf-8")).hexdigest()[:32]


def rasterize_svg(data: bytes, size: int = VECTOR_RASTER_SIZE) -> bytes | None:
    """SVG → PNG。`resvg_py` 缺席或图有问题时返回 None，调用方换下一个候选。

    用 resvg 而不是 cairosvg：后者在 Windows 上要另装 cairo 原生库，前者是 abi3 轮子，
    win_amd64 / macosx_arm64 / macosx_x86_64 都有官方预编译（见 docs/REUSE.md）。
    """
    try:
        import resvg_py
    except ImportError:
        return None
    try:
        return bytes(resvg_py.svg_to_bytes(
            svg_string=data.decode("utf-8", "replace"), width=size))
    except Exception:
        return None


def _looks_like_svg(data: bytes, content_type: str = "") -> bool:
    if "svg" in (content_type or "").lower():
        return True
    head = data[:512].lstrip()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048])


def decode(data: bytes, content_type: str = ""):
    """任意图标字节 → RGBA 图像；解不开返回 None。"""
    from PIL import Image

    if _looks_like_svg(data, content_type):
        rendered = rasterize_svg(data)
        if rendered is None:
            return None
        data = rendered
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return None
    return image.convert("RGBA")


def _body_pixels(image):
    """返回 (主体坐标, 是否靠 alpha 判定的)。"""
    width, height = image.size
    pixels = image.load()
    transparent = sum(1 for y in range(height) for x in range(width)
                      if pixels[x, y][3] < 128)
    if transparent > MIN_TRANSPARENT * width * height:
        return [(x, y) for y in range(height) for x in range(width)
                if pixels[x, y][3] >= 128], True
    # 全不透明时只能拿四角当底色去猜主体。实测这条路在多色 logo 上会反转，
    # 所以它的结果不参与「有把握」的判定，只用来兜底。
    corners = [pixels[0, 0], pixels[width - 1, 0],
               pixels[0, height - 1], pixels[width - 1, height - 1]]
    background = tuple(sum(corner[i] for corner in corners) // 4 for i in range(3))
    return [(x, y) for y in range(height) for x in range(width)
            if sum(abs(pixels[x, y][i] - background[i]) for i in range(3)) > 110], False


def content_aspect(image) -> float:
    """内容外接框的长宽比（≥1）。空图返回 0。

    看的是内容不是画布：字标和字形的画布可以一样方，内容框差着三四倍。
    """
    body, _ = _body_pixels(image)
    if not body:
        return 0.0
    xs = [x for x, _ in body]
    ys = [y for _, y in body]
    width = max(xs) - min(xs) + 1
    height = max(ys) - min(ys) + 1
    return max(width, height) / min(width, height)


def brand_colour(image, body) -> tuple[tuple[int, int, int], int]:
    """主体里最常见的那个饱和色，以及色相簇的个数。

    簇数就是「这是不是单色字形」的度量：纯黑白 logo 没有可用色，多色 logo 簇数会散开。
    """
    pixels = image.load()
    buckets: dict[tuple[int, int, int], int] = {}
    for x, y in body:
        red, green, blue = pixels[x, y][:3]
        _hue, light, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
        if saturation > 0.25 and 0.15 < light < 0.85:
            key = (red // 32 * 32, green // 32 * 32, blue // 32 * 32)
            buckets[key] = buckets.get(key, 0) + 1
    if not buckets:
        return (60, 60, 64), 0
    return max(buckets, key=buckets.get), len(buckets)


#: 黑白字形没有品牌色可取，圆底用这个中性深灰（与 `brand_colour` 取不到色时给的同一个）。
NEUTRAL_DISC = (60, 60, 64)
#: 主体像素有这么多落在同一头（都黑或都白），才算单一深浅的字形。
SINGLE_TONE_SHARE = 0.95


def single_tone(image, body) -> bool:
    """主体是不是一种深浅：全黑或全白。

    ONE'S DOUBLE 页脚那枚字标是透明底上的纯黑字，原样贴在深色页面上看不见。黑白两色都有的
    （threads 那种黑底白字）不算：那是设计好的图标，归 `designed_mark`。
    """
    pixels = image.load()
    shades = [sum(weight * channel for weight, channel in zip((299, 587, 114), pixels[x, y][:3]))
              / 255000 for x, y in body]
    share = SINGLE_TONE_SHARE * len(shades)
    return (sum(shade < 0.25 for shade in shades) >= share
            or sum(shade > 0.75 for shade in shades) >= share)


def _circle(side: int):
    from PIL import Image, ImageDraw

    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, side - 1, side - 1), fill=255)
    return mask


def glyph_mark(image, size: int = MARK_SIZE) -> bytes | None:
    """透明单色字形 →「品牌色圆底 + 白色字形」；没把握就返回 None。

    全程在 `SUPERSAMPLE` 倍画布上合成再缩回，圆边和字形边都因此拿到中间灰阶。
    """
    from PIL import Image

    body, from_alpha = _body_pixels(image)
    if not body or not from_alpha:
        return None
    colour, clusters = brand_colour(image, body)
    if clusters == 0 and single_tone(image, body):
        colour = NEUTRAL_DISC
    elif clusters == 0 or clusters > MAX_HUE_BUCKETS:
        return None

    alpha = image.getchannel("A")
    box = alpha.getbbox()
    if box is None:
        return None
    big = size * SUPERSAMPLE
    glyph = alpha.crop(box)
    side = max(1, int(big * GLYPH_INSET))
    scale = side / max(glyph.size)
    glyph = glyph.resize((max(1, round(glyph.size[0] * scale)),
                          max(1, round(glyph.size[1] * scale))), Image.LANCZOS)

    mask = Image.new("L", (big, big), 0)
    mask.paste(glyph, ((big - glyph.size[0]) // 2, (big - glyph.size[1]) // 2))
    circle = _circle(big)
    # 字形不许溢出圆：旧版少了这一步，圆边上会被白像素啃出缺口。
    mask = Image.composite(mask, Image.new("L", (big, big), 0), circle)

    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (big, big), colour + (255,)), (0, 0), circle)
    out.paste(Image.new("RGBA", (big, big), (255, 255, 255, 255)), (0, 0), mask)
    return _encode(out.resize((size, size), Image.LANCZOS))


def designed_mark(image, size: int = MARK_SIZE) -> bytes | None:
    """成品方形图标原样用：只补成方图并缩到 `size`，不改颜色、不抠图。

    非方图按短边居中裁——容器是 `object-fit:cover`，服务端先裁能省掉一次浏览器缩放，
    也让缓存里的那张就是最终那张。
    """
    from PIL import Image

    if min(image.size) < MIN_DESIGNED_SIZE:
        return None
    width, height = image.size
    if width != height:
        side = min(width, height)
        left, top = (width - side) // 2, (height - side) // 2
        image = image.crop((left, top, left + side, top + side))
    return _encode(image.resize((size, size), Image.LANCZOS))


def plain_mark(data: bytes, size: int = MARK_SIZE, content_type: str = "") -> bytes | None:
    """两条通道都不适用时的回落：把图原样缩成 PNG。"""
    from PIL import Image

    image = decode(data, content_type)
    return None if image is None else _encode(image.resize((size, size), Image.LANCZOS))


def render_mark(data: bytes, size: int = MARK_SIZE,
                content_type: str = "") -> bytes | None:
    """一份图标字节 → 一枚圆标；这份不合格就返回 None，调用方换下一个候选。

    顺序是有意的：字形通道先判。高分辨率的透明单色字形也该做成品牌色圆底，
    原样贴进容器只会露出灰底。
    """
    image = decode(data, content_type)
    if image is None:
        return None
    aspect = content_aspect(image)
    if aspect == 0.0 or aspect > MAX_CONTENT_ASPECT:
        # 空图或宽扁字标。后者不是坏图，只是不属于这个位置——留给 `/logo`。
        return None
    return glyph_mark(image, size) or designed_mark(image, size)


def _encode(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def cached_path(root: Path, url: str) -> Path | None:
    key = cache_key(url)
    return (root / f"{key}.png") if key else None


def is_fresh(path: Path, now: float | None = None, ttl: int | None = CACHE_TTL) -> bool:
    """缓存还新鲜吗。

    图标会随站点改版变，但不必每次开页都重取；一个月是「站点改了版早晚会跟上」和
    「不要每开一页就打一遍别人的服务器」之间的折中。用户在设置里选的「头像与站点
    图标刷新」通过 `ttl` 传进来；None 表示从不重取，文件在就算新鲜。
    """
    try:
        age = (now if now is not None else time.time()) - path.stat().st_mtime
    except OSError:
        return False
    return ttl is None or 0 <= age < ttl
