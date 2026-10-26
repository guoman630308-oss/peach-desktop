"""候选图片的实测、取舍与方形归一。

界面把厂牌 Logo 和女优头像都渲染成方框（厂牌方图、资料页 160×160 圆头像）。
候选按实际像素比例处理：接近正方形的直接用，长条形的补背景填成正方形，
只有小到缩进方框会糊的才拒绝。

厂牌标识另有一层：页面三处取图位（品牌小圆片、身份格、厂牌页大位）都用
`object-fit: cover` 铺满方框，所以文件本身必须是不透明方图。`bake_square` 是
位图这条规则的唯一入口，`classify_plate` 给出它据以分流的判定，`refit_plate` 再把
方图摆到圆形图位里看得全的位置。矢量标识走 `bake_square_vector`：同样的边距，但
方底用外层 SVG 包出来，不栅格化。底色两条路同一条规则（`_plate_color`），按内容
明暗判，白笔画配深底。
女优头像等照片不走这条路径，只走 `classify` 与 `pad_to_square`。
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ElementTree
from collections import Counter
from math import ceil, hypot

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat

# 长边/短边在这个值以内视为「已经够方」，直接用原图。
MAX_ASPECT = 1.35
# 缩到方框里仍然清晰的最小短边。低于这个值补白也救不回来。
MIN_SHORT_EDGE = 128
# 整张图的色彩起伏低于这个值就当它只有一个颜色。不取 0 是给 JPEG 的压缩噪声留余量；
# 取 2.0 不会误伤人像——最平的那张真实候选三个通道也在 56 以上。
FLAT_STDDEV = 2.0

SQUARE = "square"
PAD = "pad"
REJECT = "reject"

# 独立图标：带透明像素，主体之外没有属于它自己的底，烤方图时配白底。
MARK = "mark"
# 整块底图：完全不透明，底色是设计的一部分。照片也归这一类。
TILE = "tile"

# 烤白底时内容占方图边长的比例，四周各留约 12% 边距。
PLATE_CONTENT_RATIO = 0.76
PLATE_BACKGROUND = (255, 255, 255, 255)
# 给浅色标识配的深底。这些站点的字标本来就是给深色页面画的，配白底等于把它抹掉。
PLATE_DARK_BACKGROUND = (17, 17, 17, 255)
# 内容里在白底上还看得见的比例低于这个数就改配深底。四张真实矢量标识实测：
# DarkRoomVR 0.20、TeamSkeetXReislin 0.52 会被白底吞掉，TeenFidelity 0.82、
# VirtualTaboo 1.00 不受影响，阈值落在中间两侧都有余量。
PLATE_VISIBLE_RATIO = 0.7
# 内容框里的不透明覆盖到这个比例就算自带整块底，底色只当画框，一律白。真实标识
# 实测：稀疏笔画的 HEYZO 0.52、TeamSkeetXReislin 0.56 要判底色，自带底的 Fitch
# 0.98、Hunter 1.00 不必判，阈值落在中间。
PLATE_SOLID_COVER = 0.9
# 判底色用的探针尺寸。只用来数像素，产物仍是原矢量。
PLATE_PROBE_SIZE = 256
# 亮度离白多远才算「在白底上看得见」。
PLATE_INK_CONTRAST = 40
# 离底色多远才算内容。方图的底色是设计的一部分，压不到这个差的算同一块底。
PLATE_GROUND_TOLERANCE = 24
# 内容外接框长边占方图边长低于这个数，就裁掉多余留白。真实目录实测：
# Flower 0.24、いんすた 0.30、Planet_Plus 0.31、まんまんランド 0.55、EST 0.56 都是
# 源站 favicon 自带的大留白，铺进 32 px 圆片后内容小得认不出；下一档 FC2-PPV 0.62
# 起看着正常，阈值落在这个断点上。
PLATE_MIN_SPAN = 0.6
# 内容落在内切圆之外的比例超过这个数，就把方图补大到内容的外接圆。小圆片
# （`.brandpill .mk`）是圆的，方图四角在圆外，那部分内容直接看不见。实测断点：
# T-POWERS 0.019 与 TEPPAN 0.034 之间；圆形图标（Wanz Factory 0.008）不受影响。
PLATE_CIRCLE_LOSS = 0.025
# 一行（或一列）里的内容像素不多于内容总量这个比例就算杂点。有损压缩会在纯色区
# 留下极淡的斑点，逐像素的外接框被它们撑满整张画布：EST 的字样只占纵向 249 行，
# 杂点却让框横跨 685 行。下限挡住内容本来就很少的小图。
PLATE_NOISE_RATIO = 0.0005
PLATE_NOISE_FLOOR = 3
# 方图短边不到这个数，就用自己的底色补到这个数。小圆片（`.brandpill .mk`）是 32 CSS px，
# 2 倍屏要 64 实像素，短边不够时浏览器只能放大——放大糊掉的是整张图，而补出来的边是
# 这张图自己的底色，笔画一个像素都不缩放。2026-09-08 实测受影响 7 张：DorcelClub 57、
# Flower 三张 55、LINX 63、HEYZO 39、Prestige 42（前面的数是裁完留白之后的短边，
# 一半的图是先裁小才不够用的）。补到这里为止：再往外撑会把内容占宽压到 `PLATE_MIN_SPAN` 以下，
# 那正是上一条要裁掉的形状，两条规则会在同一张图上来回拉锯，重跑一遍就不再幂等。
# 上限用截断而不是四舍五入，就为了让占宽落在 0.6 这一侧。
PLATE_MIN_SIDE = 64

# 手工裁出来的头像重编时用的 JPEG 质量。头像框最大也就 512 CSS px，95 在这个尺寸
# 上和无损肉眼无差，而下一档 85 会在脸部平滑区留下可见的块。
CROP_JPEG_QUALITY = 95

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
# 方底是外层 SVG 包出来的，标记记在它的根元素上：矢量没有像素可读，再跑一遍时
# 只能靠这个标记认出「已经补过白」，否则每跑一次就多套一层白框。
VECTOR_PLATE_MARK = "data-peach-plate"
# 标识文件实测都在几十 KB 以内。上限挡住构造出来的深嵌套 XML，解析前就拒收。
VECTOR_MAX_BYTES = 1 << 20
# 无单位和 px 都直接是用户坐标；pt、em、% 这些换算基准不在文件里，套进外层
# 会缩错，宁可原样留着。
_SVG_LENGTH = re.compile(r"^([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)(px)?$")

ElementTree.register_namespace("", SVG_NS)
ElementTree.register_namespace("xlink", XLINK_NS)


def measure_image_size(payload: bytes) -> tuple[int, int] | None:
    """只读图片头部拿尺寸；解析失败返回 None，不抛给调用方。"""
    try:
        with Image.open(io.BytesIO(payload)) as image:
            return image.size
    except Exception:
        return None


def measure_image_file(path) -> tuple[int, int] | None:
    """盘上那张图的像素尺寸；没有或读不出就是 None。只读图片头，不解码整张。"""
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def clamp_box(box: object, width: int, height: int) -> dict | None:
    """人递过来的框 → 落在源图里的整数框；形状不成立返回 None。

    四边取整后夹回源图，再要求切出来的宽高都至少 1 像素。页面量的是显示像素、换算
    回源图会带小数，所以取整在这里做一次，落盘和裁图用的就是同一组整数——两边各取
    各的整，框会差出一个像素，而那一个像素在页面上看不出来，只在事后对不上账。
    """
    width, height = int(width or 0), int(height or 0)
    if width <= 0 or height <= 0 or not isinstance(box, dict):
        return None
    try:
        edges = {name: int(round(float(box[name]))) for name in ("x0", "y0", "x1", "y1")}
    except (KeyError, TypeError, ValueError):
        return None
    for name, limit in (("x0", width), ("y0", height), ("x1", width), ("y1", height)):
        edges[name] = max(0, min(edges[name], limit))
    if edges["x1"] - edges["x0"] < 1 or edges["y1"] - edges["y0"] < 1:
        return None
    return edges


def crop_to_box(payload: bytes, box: tuple[int, int, int, int]) -> bytes | None:
    """按源图像素框切出一张新图；框不成立或图读不出来返回 None。

    切出来的字节按源图的格式重编：头像那一路后面还要过 `inspect_avatar` 的格式门槛，
    在这里换成第三种格式只会让那一关多一条分支。PNG 无损原样写，其余一律 JPEG——
    带 alpha 的图先铺白底，JPEG 装不下透明通道，不铺的话透明处会落成黑块。

    原图一个字节都不动：这个函数只产出新的字节，落盘是调用方的事。
    """
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            width, height = opened.size
            left, top, right, bottom = (int(value) for value in box)
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                return None
            return _encode_like(opened.crop((left, top, right, bottom)), opened.format)
    except Exception:
        return None


def _encode_like(image: Image.Image, source_format: str | None) -> bytes:
    """按源图的格式重编：PNG 无损原样写，其余一律 JPEG，带 alpha 的先铺白底。"""
    buffer = io.BytesIO()
    if source_format == "PNG":
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    if image.mode in ("RGBA", "LA", "P"):
        flat = Image.new("RGB", image.size, (255, 255, 255))
        converted = image.convert("RGBA")
        flat.paste(converted, mask=converted.split()[-1])
        image = flat
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.save(buffer, format="JPEG", quality=CROP_JPEG_QUALITY)
    return buffer.getvalue()


#: EXIF 里记方向的那个标签号。1 是正向，2–8 是镜像与各个角度的旋转。
EXIF_ORIENTATION = 0x0112


def upright(payload: bytes) -> bytes:
    """按 EXIF 方向转正后的图；没有方向标签或本来就是正向的原样返回。

    手机照片常把像素按传感器方向存、再用一个标签说「显示时转 90°」。浏览器和 OpenCV
    解码时都照这个标签转正，PIL 的尺寸和裁切却按原始像素走——人在页面上框的那一块、
    人脸探针给的脸心，落到没转正的像素上是另一块区域。框选这一路的字节先过这一道，
    三边看到的就是同一张图。读不出来的原样返回，交给后面的格式门槛去拒。
    """
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            if opened.getexif().get(EXIF_ORIENTATION, 1) in (1, None):
                return payload
            turned = ImageOps.exif_transpose(opened)
            return _encode_like(turned, opened.format)
    except Exception:
        return payload


def is_flat(payload: bytes) -> bool:
    """整张图只有一个颜色——装进圆框就是一块底色，不是一张人像。

    取不到人像时回一块占位底色的来源不止一家：X 给的那张是 143×143 纯白，尺寸过得了
    `MIN_SHORT_EDGE`、格式也是正经 PNG，只有看像素才分得出来。先缩到 64×64 再统计：
    判「有没有内容」用不着原分辨率，而候选列表一次要过几十张图。
    解析不了的按「不是纯色」放行——这个函数只负责挑出确定没有内容的那些，
    「这堆字节是不是一张图」由 `inspect_avatar` 回答。
    """
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            image = opened.convert("RGB")
            image.thumbnail((64, 64))
            spread = ImageStat.Stat(image).stddev
    except Exception:
        return False
    return all(value < FLAT_STDDEV for value in spread)


def classify(width: int, height: int) -> tuple[str, float, str]:
    """返回（判定, 长宽比, 说明）。判定为 square / pad / reject。"""
    if not width or not height:
        return REJECT, 0.0, "尺寸未知"
    aspect = max(width, height) / min(width, height)
    if min(width, height) < MIN_SHORT_EDGE:
        return REJECT, aspect, f"短边 {min(width, height)} < {MIN_SHORT_EDGE}"
    if aspect <= MAX_ASPECT:
        return SQUARE, aspect, "接近正方形"
    return PAD, aspect, f"长宽比 {aspect:.2f}，补背景填成正方形"


def _has_transparency(image: Image.Image) -> bool:
    return image.getchannel("A").getextrema()[0] < 255


def _open_rgba(payload: bytes) -> Image.Image | None:
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            return opened.convert("RGBA")
    except Exception:
        return None


def classify_plate(payload: bytes) -> str | None:
    """这张图是独立图标（`MARK`）还是整块底图（`TILE`）；解析失败返回 None。

    判据是有没有透明像素。带透明的（PREMIUM 那种全透明底蓝色字标）主体之外
    没有属于它的底，铺进方框前得先配一块；完全不透明的（M's Video Group 的
    黑底方块、Natural High 的红底）自带底色，那块底就是设计的一部分。
    """
    image = _open_rgba(payload)
    if image is None:
        return None
    return MARK if _has_transparency(image) else TILE


def bake_square(payload: bytes) -> bytes | None:
    """把厂牌标识烤成不透明方图，返回 PNG 字节；解析失败返回 None。

    独立图标裁掉透明边后居中放到方底上，内容占边长 `PLATE_CONTENT_RATIO`，底色
    按内容明暗判。整块底图接近方形就沿用它自己的底，长条按边缘主色补方。两条路
    最后都过一遍 `refit_plate`：内容在画布里的占比也是取图位看得见的东西，
    只看长宽比会让 0.24 和 0.95 一起原样通过。原始像素一律不缩放。
    """
    image = _open_rgba(payload)
    if image is None:
        return None
    width, height = image.size
    if not width or not height:
        return None
    if not _has_transparency(image):
        if max(width, height) / min(width, height) <= MAX_ASPECT:
            squared = payload
        else:
            squared = pad_to_square(payload)
        if squared is None:
            return None
        return refit_plate(squared) or squared
    box = image.getchannel("A").getbbox()
    if box is None:
        return None
    content = image.crop(box)
    side = max(max(content.size), round(max(content.size) / PLATE_CONTENT_RATIO))
    canvas = Image.new("RGBA", (side, side), _plate_color(image))
    canvas.paste(content, ((side - content.width) // 2, (side - content.height) // 2),
                 content)
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, "PNG")
    baked = buffer.getvalue()
    return refit_plate(baked) or baked


def _ink_mask(image: Image.Image, ground: tuple[int, int, int, int]) -> Image.Image:
    """离底色够远的那些像素。返回只有 0 / 255 的单通道图。"""
    plate = Image.new("RGB", image.size, ground[:3])
    return ImageChops.difference(image.convert("RGB"), plate).convert("L").point(
        lambda value: 255 if value > PLATE_GROUND_TOLERANCE else 0)


def _ink_extent(mask: Image.Image, floor: int) -> tuple[int, int] | None:
    """内容像素多于 `floor` 的那些行的首末位置，末位是开区间。"""
    width = mask.width
    data = mask.tobytes()
    rows = [index for index in range(mask.height)
            if data[index * width:(index + 1) * width].count(255) > floor]
    if not rows:
        return None
    return rows[0], rows[-1] + 1


def _content_box(mask: Image.Image) -> tuple[int, int, int, int] | None:
    """内容外接框，只有零星几个像素的行列不算内容。"""
    total = mask.histogram()[255]
    if not total:
        return None
    floor = max(PLATE_NOISE_FLOOR, round(total * PLATE_NOISE_RATIO))
    rows = _ink_extent(mask, floor)
    columns = _ink_extent(mask.transpose(Image.Transpose.TRANSPOSE), floor)
    if rows is None or columns is None:
        return None
    return columns[0], rows[0], columns[1], rows[1]


def _content_radius(mask: Image.Image, box: tuple[int, int, int, int]) -> float:
    """内容相对自己外接框中心的最大距离，也就是它的外接圆半径。

    只看每一行最左、最右那两个内容像素：同一行里离中心最远的必然是这两个之一，
    所以逐行取一次 `getbbox()` 就够，不必遍历上百万像素。
    """
    center_x = (box[0] + box[2] - 1) / 2
    center_y = (box[1] + box[3] - 1) / 2
    width = mask.width
    radius = 0.0
    for y in range(box[1], box[3]):
        row = mask.crop((0, y, width, y + 1)).getbbox()
        if row is None:
            continue
        for x in (row[0], row[2] - 1):
            radius = max(radius, hypot(x - center_x, y - center_y))
    return radius


def refit_plate(payload: bytes) -> bytes | None:
    """把不透明方图重新摆一遍：内容既不小到发空，也不大到被圆片切掉。

    两件事都是取图位真的看得见的：小圆片（`.brandpill .mk`）是 32 px 圆、`cover`
    铺满，所以铺满的是整张画布，不是内容——源站 favicon 常自带大留白（Flower 的
    金环只占 0.24），铺进去就小得认不出；反过来顶到边的实心方标（MARRION 0.95）
    四角落在圆外，金框和字样直接看不见。

    动手只有三种：内容太小就裁掉多余留白，会被圆切就把画布补到内容的外接圆，这一趟
    的产物小于圆片要的实像素（`PLATE_MIN_SIDE`）就用自己的底色补到那个数——裁和补在
    同一趟里定完，产物才是不动点。像素一律不缩放，所以裁出来的图更小但更清晰，
    补出来的图更大而清晰度不变。
    三者都不适用时返回原字节；`None` 只表示解析不了。
    """
    image = _open_rgba(payload)
    if image is None:
        return None
    width, height = image.size
    if not width or not height:
        return payload
    if max(width, height) / min(width, height) > MAX_ASPECT:
        # 横幅字标本来就不是方的，摆进圆片这件事由 `pad_to_square` 负责。
        return payload
    if _has_transparency(image):
        # 带透明的还没配底，`_background_color` 会给回透明，补出来的边会变成黑块。
        # 这一步只收 `bake_square` 已经配好底的产物和目录里那些不透明方图。
        return payload
    ground = _background_color(image)
    box = _content_box(_ink_mask(image, ground))
    if box is None:
        # 整张一个色（占位底板那类），没有内容框可言。
        return payload
    # 框外的杂点不参与后面的圆外损失和外接圆，否则一个斑点就能把画布撑到两倍。
    mask = Image.new("L", image.size, 0)
    mask.paste(_ink_mask(image.crop(box), ground), box[:2])
    span = max(box[2] - box[0], box[3] - box[1])
    # 圆片是 `cover`：长边被切掉，露出来的是居中那个短边见方的区域的内切圆。
    short = min(width, height)
    left = (width - short) // 2
    top = (height - short) // 2
    circle = Image.new("L", image.size, 0)
    ImageDraw.Draw(circle).ellipse(
        (left, top, left + short - 1, top + short - 1), fill=255)
    total = mask.histogram()[255]
    inside = ImageChops.multiply(mask, circle).histogram()[255]
    lost = 1 - inside / total if total else 0.0
    side = 0
    if span / short < PLATE_MIN_SPAN:
        side = round(span / PLATE_CONTENT_RATIO)
    if lost > PLATE_CIRCLE_LOSS:
        side = max(side, ceil(_content_radius(mask, box) * 2))
    if (side or short) < PLATE_MIN_SIDE:
        # 判的是这一趟的产物，不是原图。裁掉大留白之后才不够圆片用的最多（Flower
        # 从 180 裁到 57），按原图短边判就永远轮不到它，得再跑一趟才补上——那也
        # 就是说产物不是不动点，而归一脚本承诺幂等。
        # 上限是内容占宽的下限：补过头就成了上面那条要裁的大留白。
        side = max(side, min(PLATE_MIN_SIDE, int(span / PLATE_MIN_SPAN)))
    if side <= 0 or (side == width and side == height):
        return payload
    content = image.crop(box)
    canvas = Image.new("RGBA", (side, side), ground)
    canvas.paste(content, ((side - content.width) // 2, (side - content.height) // 2))
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


def _svg_number(value: float) -> str:
    """用户坐标写成十进制；整数不留 `.0`，小数留够位数不改变比例。"""
    return f"{value:.10g}"


def _plate_color(image: Image.Image) -> tuple[int, int, int, int]:
    """这张标识该配白底还是深底。

    只有笔画直接挨着底色的稀疏标识才会被底色吞掉：HEYZO 的「HEY」、DarkRoomVR 的
    「DARK ROOM」都是白笔画摆在透明底上，白底可见率 0.44 与 0.20，配白底等于把那
    部分抹掉，深底才是它们的本相。自带整块底的（Fitch 的白卡片、Hunter 的迷彩方块，
    内容框里的不透明覆盖 0.98 与 1.00）另说：它们的边界是自己画的，外面那圈底色
    只是画框，配深底反而让那块卡片浮在黑里。透明像素不算内容。
    """
    ink = image.getchannel("A").point(lambda value: 255 if value >= 128 else 0)
    lit = image.convert("L").point(
        lambda value: 255 if value < 255 - PLATE_INK_CONTRAST else 0)
    box = ink.getbbox()
    total = ink.histogram()[255]
    if not total or box is None:
        return PLATE_BACKGROUND
    if total / ((box[2] - box[0]) * (box[3] - box[1])) >= PLATE_SOLID_COVER:
        return PLATE_BACKGROUND
    visible = ImageChops.multiply(ink, lit).histogram()[255]
    if visible / total >= PLATE_VISIBLE_RATIO:
        return PLATE_BACKGROUND
    return PLATE_DARK_BACKGROUND


def _vector_plate_color(payload: bytes) -> tuple[int, int, int, int]:
    """矢量标识的底色。栅格化只用来数像素，产物仍是矢量。

    渲染不出来（`resvg_py` 缺席或图有问题）按白底走，和判不出内容时一致。
    """
    from .link_marks import rasterize_svg

    rendered = rasterize_svg(payload, PLATE_PROBE_SIZE)
    image = _open_rgba(rendered) if rendered else None
    return PLATE_BACKGROUND if image is None else _plate_color(image)


def _svg_length(value: str | None) -> float | None:
    matched = _SVG_LENGTH.match((value or "").strip())
    if matched is None:
        return None
    number = float(matched.group(1))
    return number if number > 0 else None


def _svg_content_box(root: ElementTree.Element) -> tuple[float, float] | None:
    """标识内容在自己坐标系里的宽高；定不出来返回 None。

    `viewBox` 是第一判据：它是作者声明的内容框，而 `width`／`height` 常被下游改成
    展示尺寸。两个都没有（`<svg/>` 这种空壳）就没有可依据的比例，方框边长无从算起。
    """
    box = (root.get("viewBox") or "").replace(",", " ").split()
    if len(box) == 4:
        try:
            width, height = float(box[2]), float(box[3])
        except ValueError:
            return None
        if width > 0 and height > 0:
            return width, height
        return None
    width = _svg_length(root.get("width"))
    height = _svg_length(root.get("height"))
    if width is None or height is None:
        return None
    return width, height


def _parse_svg(payload: bytes) -> ElementTree.Element | None:
    """SVG 根元素；不是 SVG、解析不了或过大都返回 None。"""
    if len(payload) > VECTOR_MAX_BYTES:
        return None
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return None
    return root if root.tag == f"{{{SVG_NS}}}svg" else None


def vector_image_size(payload: bytes) -> tuple[float, float] | None:
    """矢量标识的内容宽高，单位是它自己的用户坐标；量不出来返回 None。

    位图的 `measure_image_size` 给的是像素，这里给的是比例基准：矢量没有固有像素，
    方框边长只能从这个框推。
    """
    root = _parse_svg(payload)
    return None if root is None else _svg_content_box(root)


def bake_square_vector(payload: bytes) -> bytes | None:
    """把矢量标识包进白色方底，返回 SVG 字节；不是可归一的 SVG 时返回 None。

    位图那条路要先栅格化才能取外接框，矢量不必：原文档整个塞进外层 SVG 的一个
    嵌套 `<svg>`，长边占方框 `PLATE_CONTENT_RATIO`，居中，四周是白底矩形。原文档
    一个节点都不改写，放多大仍然清晰。已经包过的原样返回，重复跑不会越套越多。

    内容框直接取 `viewBox`，不像位图那样按 alpha 裁一遍：真实目录里 4 张矢量标识
    渲染后实测，决定方框边长的那条长边都是 tight 的（横向占满 0.97～1.00），
    纵向留白只影响居中，肉眼看不出。底色由 `_vector_plate_color` 判，白字标配深底。
    """
    inner = _parse_svg(payload)
    if inner is None:
        return None
    if inner.get(VECTOR_PLATE_MARK) is not None:
        return payload
    box = _svg_content_box(inner)
    if box is None:
        return None
    width, height = box
    side = max(width, height) / PLATE_CONTENT_RATIO
    # 嵌套 `<svg>` 的 x／y／width／height 说的是它在外层坐标里占哪一块，viewBox 说的
    # 是自己的坐标怎么映射进去。原文档缺 viewBox 时按它的 width／height 补一个，
    # 否则嵌套之后内容会按外层坐标重新量，比例就散了。
    if not (inner.get("viewBox") or "").strip():
        inner.set("viewBox", f"0 0 {_svg_number(width)} {_svg_number(height)}")
    inner.set("x", _svg_number((side - width) / 2))
    inner.set("y", _svg_number((side - height) / 2))
    inner.set("width", _svg_number(width))
    inner.set("height", _svg_number(height))
    edge = _svg_number(side)
    plate = ElementTree.Element(f"{{{SVG_NS}}}svg", {
        "width": edge, "height": edge, "viewBox": f"0 0 {edge} {edge}",
        VECTOR_PLATE_MARK: "1",
    })
    red, green, blue = _vector_plate_color(payload)[:3]
    ElementTree.SubElement(plate, f"{{{SVG_NS}}}rect", {
        "x": "0", "y": "0", "width": edge, "height": edge,
        "fill": f"#{red:02x}{green:02x}{blue:02x}",
    })
    plate.append(inner)
    return ElementTree.tostring(plate, encoding="utf-8", xml_declaration=True)


def _background_color(image: Image.Image) -> tuple[int, int, int, int]:
    """取边缘主色当不透明底色；图片带透明像素时继续保持透明。

    Logo 字样可能贴到四角，不能把某个角上的文字颜色误当成背景。只要原图有
    透明像素，补边就保持透明；完全不透明时再从整圈边缘取出现最多的颜色。
    """
    width, height = image.size
    if _has_transparency(image):
        return (0, 0, 0, 0)
    border = [image.getpixel((x, 0)) for x in range(width)]
    border.extend(image.getpixel((x, height - 1)) for x in range(width))
    border.extend(image.getpixel((0, y)) for y in range(1, height - 1))
    border.extend(image.getpixel((width - 1, y)) for y in range(1, height - 1))
    return Counter(border).most_common(1)[0][0]


def pad_to_square(payload: bytes) -> bytes | None:
    """把长条形图片居中放到正方形画布上，返回 PNG 字节；失败返回 None。"""
    image = _open_rgba(payload)
    if image is None:
        return None
    width, height = image.size
    side = max(width, height)
    canvas = Image.new("RGBA", (side, side), _background_color(image))
    canvas.paste(image, ((side - width) // 2, (side - height) // 2), image)
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()
