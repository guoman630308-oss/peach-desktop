# -*- coding: utf-8 -*-
r"""横版封套里正封那一块在哪。

JAV 的官方封套是「背面 | 书脊 | 正面」拼成的一整张横图，正面在右侧。Peach 原样
保存这张图（`jav_cover_fetch` 的约定），所以正封不是一张新图片，而是一组坐标：
算出来的框写进 sidecar，页面按它取景。原图一个字节都不动。

框就是正封本身——从折痕一路到右缘、满高。页面要的正是这一块：按折痕切掉封底，
正封整块摆进卡片。框里不另取固定形状的子区域，因为卡片的容器比例是页面的事，
源图这一侧无从知道；在这里先按某个形状切一刀，只会把正封两侧各削掉一圈。

五种取景方式，写在框的 `method` 里：

- `fold`：Sobel 找到了书脊折痕那道竖直峭壁，正面从折痕右侧开始。
- `ratio`：没找到折痕，按正封宽高比的先验从右缘量回去。
- `center`：拼图或模糊补边正中的正封，框在两条拼接缝之间。
- `none`：这张图不该裁。整图框原样返回，页面维持现有的封面取景。
- `manual`：人在详情页自己框的。它压过上面四条，也不受算法版本号管辖——
  框后面没有算法，改判据不构成重算它的理由。作废的唯一条件是封面换了张图
  （`px` 对不上），那时框描述的是另一张图。
折痕按正封形状判定，只在「切出来的正封落在 `PANEL_ASPECT_MIN`～`PANEL_ASPECT_MAX` 之间」的那几十
列里找峭壁，窗里够强的边不止一条时按形状定夺，不按谁更强。

同一套找法还有一档形状不同的宽封套（`WIDE_SLEEVE_LABELS`）：Blu-ray 模板的正封更宽，
整张封套的比例和 16:9 剧照几乎一样，只能由厂牌开门、再由比例决定走不走这一档。

外部实现的实测值登记在 `docs/REUSE.md`，这里留作来路：

- NeoAVDC `c7a430c64013c97a0213cd8a57e2ff5696793a86`（MIT）量 DMM/JavBus 封套，
  折痕落在全宽约 52.5%。这个数和本机实测对得上，但它是结果不是判据——拿它反过来
  卡位置，封套一宽一窄就落空。
- sakuramediabe `7e40ef87c7518c1dc2b6c8c299170ad7395fec6d`（GPL-3.0，只作算法边界
  参考，不引入代码与运行时）用列向 Sobel 梯度找切点，接受条件是「左右两个峰关于
  中线对称」或「右峰离中线约 20 像素」。这两条都不采用：对称那一条在本机实测里会
  误收，KBI-036 的背面有一条竖直分栏线，与正封内部的一道强边恰好关于中线对称，按
  它切会切到正封里面 52 像素处，把大标题削掉一截；宽高比窗口把这张定在 421 列，
  正封宽高比 0.703。

OpenCV 不在（`vision` 是可选依赖组）或图读不出来时直接走 `ratio`：取景退化成先验
几何，仍然给得出框，不会让整批停下。
"""
from __future__ import annotations

import json
import re
import statistics
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from . import images
from .catalog_rules import (
    is_jav_code,
    is_korean_mib_code,
    is_uncensored_code,
    normalise_code_key,
)

#: 封套的宽高比下限。低于它按竖版正封处理：整张就是正封，没有可裁的横向余量。
#: 这个数和 `SLEEVE_RATIO_MAX` 是 `scripts/detect_cover_faces.py` 与 `web/app.js`
#: 的 `coverAnchor` 共用的同一对判据，三处必须是同一对值，由
#: `tests/test_web_ui.py` 的 `CoverSleeveThresholdTests` 守住。
SLEEVE_RATIO_MIN = 1.2
#: 上限之外是 16:9 官方剧照。整幅都是画面，没有「正面那一块」可推，按先验切会裁出
#: 半张背景，所以这一档只认一种版式（`center_panel`），别的不给框。NeoAVDC 对
#: HEYZO、FC2 与欧美片标 `posterNoCrop` 是同一条判据的另一面：那些封面本身就是
#: 成品横版剧照。
SLEEVE_RATIO_MAX = 1.65

#: 正封宽高比的可信区间，折痕只在切出这个形状的那几十列里找。
PANEL_ASPECT_MIN = 0.68
PANEL_ASPECT_MAX = 0.76
#: 峰值要达到全图最强列梯度的这个比例才算一道峭壁。挡住的是平缓横图：那种图在窗里
#: 照样有一个最大值，但它只是噪声的最高点，按它切会把画面拦腰截断。
FOLD_MIN_STRENGTH = 0.35
#: 强度达到窗内最强边这个比例的列都算候选折痕。书脊有左右两条边，窗口装得下一整条
#: 书脊时两条都在候选里，而左边那条常常更强：它挨着封底的留白，右边那条挨着正封的
#: 画面。谁更强不说明哪条是折痕，所以候选之间按「切出来的正封形状」定夺。
FOLD_RIVAL_RATIO = 0.7
#: 突出度（这一列减去左右各这个比例源图宽内的梯度中位数）达到窗内最高突出度
#: `FOLD_RIVAL_RATIO` 倍的列也算候选。折痕是一条一两列宽的线，书脊上的竖排片名是
#: 二十来列宽的一片高地：DDK-023 书脊里片名那片高地最高 0.50，折痕 419 列只有 0.34，
#: 按强度进不了候选，切在高地的下坡 409 列，正封左缘留下 11 列书脊；按突出度折痕
#: 是窗里最高的那条（0.23 对高地的 0.16）。
FOLD_PROMINENCE_REACH = 0.01
#: 按突出度进来的候选还得大体从上贯到下：满高覆盖（见 `SEAM_STEP`）达到这个数。正封里
#: 紧挨折痕的大字也是一两列宽的尖峰，突出度照样高，但只占几行（IDBD-610 的 433 列 0.22、
#: MIMK-009 的 423 列 0.35）；DDK-023 的折痕 0.68。
FOLD_SHARP_MIN_COVERAGE = 0.6
#: 满高不够的窄尖峰还有一条路：切点自己不满高、它右边窗内有一根孤立的尖峰时，折痕是
#: 那根尖峰（`_sharp_beyond`）。孤立是「这一列达到左右各 `FOLD_PROMINENCE_REACH` 内梯度
#: 中位数的这个倍数」。书脊和正封共用底色的封套，折痕只在书脊内容所在的行里看得见：
#: IPZ-602 书脊 379～421 列，折痕 420 列梯度 0.24、满高覆盖 0.20，进不了上面那一关，
#: 切在书脊内照片与文字形成的高地下坡 411 列，正封左缘留下 10 列书脊；同形态的
#: EBOD-531、GUN-851、IPX-150、PGD-526、CLUB-622、IPZZ-477 各留 7～19 列。这七张的
#: 折痕是周围中位数的 3.7～9.2 倍；正封里紧挨折痕的大字周围还有别的笔画，MIMK-009
#: 的 423 列只有 2.2 倍、IDBD-610 的 433 列 2.0 倍。ABP-627 的折痕 441 列挨着正封的
#: JPEG 软边也要达到 2.5 倍，并满足左忙右静的方向约束。
FOLD_ISOLATION = 2.5
#: 候选按相邻归并成边：一道边在梯度上响应好几列，相距不超过源图宽这个比例的算一条。
FOLD_EDGE_SPAN = 0.005
#: 折痕是一道有宽度的斜坡，梯度的峰落在斜坡最陡处，也就是斜坡当中；书脊的最后一两
#: 列还在峰的右边。切点从峰往右走到梯度落回窗内中位数为止，最多走源图宽的这个比例。
FOLD_SETTLE_LIMIT = 0.01
#: 没找到折痕时按这个宽高比从右缘量回去，也是候选之间定夺用的形状。
PANEL_ASPECT = 0.704

#: 宽封套与剧照比例接近，按厂牌与比例共同判定：厂牌在名单里且比例落在这个区间才按宽封套切；同一
#: 厂牌换成 DVD 比例的封面照旧走上面那一档，比例更宽的照旧当剧照。别的 `*BD` 厂牌
#: （IDBD、PBD、REBD）的封面是普通 DVD 比例，不在名单里。
WIDE_SLEEVE_LABELS = frozenset({"CWPBD", "SMBD"})
WIDE_SLEEVE_RATIO_MIN = 1.75
WIDE_SLEEVE_RATIO_MAX = 1.85
#: 宽封套正封的宽高比先验与折痕窗。本机六张实测书脊右缘都切出 0.848～0.852：这是一家
#: 模板的固定几何，不像 DVD 封套的书脊厚度随碟数变，所以窗只留 JPEG 糊边与缩放的余量。
#: 窗一放宽就会认错：书脊上竖排的片名文字边比书脊右缘强得多（CWPBD-126 是 0.81 对
#: 0.32），0.80～0.90 的窗会按它切在 387 列、SMBD-110 切在 388 列，正封左缘各留下
#: 六七列书脊；窄窗里那道文字边不在窗内，右缘又不够峭壁，落到 `ratio` 的 394 列。
WIDE_PANEL_ASPECT = 0.85
WIDE_PANEL_ASPECT_MIN = 0.83
WIDE_PANEL_ASPECT_MAX = 0.87


class PanelShape(NamedTuple):
    """一档封套的正封形状：折痕只在切出 `low`～`high` 宽高比的列里找，窗里的候选按贴近
    `prior` 定夺，找不到折痕就按 `prior` 从右缘量回去。"""

    low: float
    prior: float
    high: float


DVD_PANEL = PanelShape(PANEL_ASPECT_MIN, PANEL_ASPECT, PANEL_ASPECT_MAX)
WIDE_PANEL = PanelShape(WIDE_PANEL_ASPECT_MIN, WIDE_PANEL_ASPECT, WIDE_PANEL_ASPECT_MAX)

#: 16:9 里的居中正封：「剧照 | 正封 | 剧照」拼成一张，正封宽 `PANEL_ASPECT` 倍高、
#: 正好居中（mgstage 给 Prestige PASN 的 `pake-03_`／`pb_e_`，MOON FORCE 的素人封套）。
#: 认它靠拼接缝：缝是一条从上贯到下的直线，画面里的边再强也很少在同一列满高。
#: 一行算「有落差」要求这一列左右两侧的灰度差过这个数。
SEAM_STEP = 12
#: 缝要覆盖这么多行才算。只要一侧成立：PASN-031
#: 右侧剧照压暗后和正封边缘几乎同色，那一侧只有 0.08。
SEAM_MIN_COVERAGE = 0.75
#: 缝只在居中正封的两个边位附近找，左右各容这个比例的源图宽。PASN 两张实测偏差 1 列。
SEAM_TOLERANCE = 0.005

FOLD = "fold"
RATIO = "ratio"
CENTER = "center"
NONE = "none"
MANUAL = "manual"

#: 手工框的来路，落在 sidecar 的 `source` 上。写死一个串是为了事后能一眼分清
#: 「这张图的取景是算出来的还是人定的」：算出来的那几档没有 `source`。
MANUAL_SOURCE = "user:crop"

#: 算法版本号。sidecar 记着它，落后的重算。改动判据、常数或框的形状都要进位。
ALGORITHM_VERSION = "poster-crop-v13"
#: sidecar 与封面同名换后缀：`ABW-232.jpg` → `ABW-232.poster.json`。人脸取景是
#: `.face.json`，两者同目录、同命名风格，各描述一件事：一个是脸在哪，一个是正封在哪。
SIDECAR_SUFFIX = ".poster.json"

#: 列梯度的来源：一条长度等于图宽的序列，或一个返回它的可调用对象，或 None。
#: 收可调用对象是为了惰性：番号形态或宽高比就能定下 `none` 的图不必解码。
GradientSource = Sequence[float] | Callable[[], Sequence[float] | None] | None


SCENE_COVER_LABELS = frozenset({'IENEE', 'IENFE', 'SDHS'})


def is_vr_title(title: str | None) -> bool:
    """来源标题明确标记为 VR 作品。"""
    return bool(re.search(r'(?:【|\[|\(|（)\s*(?:[48]K\s*)?VR\s*(?:】|\]|\)|）)', str(title or ''), re.I))


def is_scene_cover(code: str | None, title: str = '') -> bool:
    """VR 与数字发行厂牌的整幅宣传画面。"""
    key = normalise_code_key(code)
    return is_vr_title(title) or bool(key and key.split('-', 1)[0] in SCENE_COVER_LABELS)


def face_crosses_spine(width: int, height: int, face: dict | None) -> bool:
    """占据主体的大脸跨过 DVD 正封先验边界时，整张图按宣传大封取景。"""
    if not face or not height or not SLEEVE_RATIO_MIN <= width / height < SLEEVE_RATIO_MAX:
        return False
    cx, fw, fh = (float(face.get(key) or 0) for key in ('cx', 'w', 'h'))
    spine = 1 - PANEL_ASPECT * height / width
    return (float(face.get('score') or 0) >= .8 and fw >= .075 and fh >= .12
            and cx - fw / 2 < spine < cx + fw / 2)


def crops_to_portrait(code: str | None, title: str = '') -> bool:
    """这个番号的封面是横版封套吗。

    判据全部走 `catalog_rules` 现有函数，不在这里另立一套番号形态：

    - 不是 JAV 番号的（欧美片目录名、转载站水印）没有封套版式可言；
    - FC2 是个人投稿，封面是投稿者自选的一张横图；
    - HEYZO 与六位日期式的无码番号（`is_uncensored_code`）走成品横版剧照；
    - 韩国 MIB 不是 JAV，发行物的封面版式另算。
    """
    key = normalise_code_key(code)
    if not key or not is_jav_code(key):
        return False
    if key.startswith("FC2") or is_scene_cover(code, title):
        return False
    return not (is_uncensored_code(key) or is_korean_mib_code(key))


def is_wide_sleeve_label(code: str | None, title: str = '') -> bool:
    """厂牌或来源标题是否标明宽封套版次。

    比例那一关在 `front_panel_box`：版次只负责开门，同一厂牌哪一期
    换了封面比例，仍按图本身的比例走对应的那一档。
    """
    key = normalise_code_key(code)
    return bool(key) and not key.startswith('FC2') and (
        key.split("-", 1)[0] in WIDE_SLEEVE_LABELS
        or bool(re.search(r'ブルーレイ|blu[\s-]?ray', str(title or ''), re.I)))


class ColumnProfile(list):
    """按列的横向梯度（列表本身），外加每列满高接缝的覆盖率 `seams`。

    两样出自同一次解码：折痕看梯度的列和，居中正封看一条缝贯穿了多少行。列和分不开
    「一条满高的缝」和「几段强边碰巧落在同一列」，覆盖率分得开。只给梯度的调用方
    没有 `seams`，16:9 那一档照旧不给框。
    """

    def __init__(self, gradient: Sequence[float], seams: Sequence[float] | None = None):
        super().__init__(gradient)
        self.seams = list(seams) if seams is not None else None


def column_gradient(image) -> ColumnProfile | None:
    """一张 BGR 图按列求和的横向 Sobel 梯度，归一化到 0～1；量不出来返回 None。

    竖直的折痕在横向梯度上是一整列的强响应，按列求和之后它是一根尖峰；横向的构图
    边界（地平线、色带）在横向梯度上几乎没有响应，不会混进来。
    """
    try:
        import cv2
        import numpy
    except ImportError:                         # pragma: no cover - 缺 vision 依赖组
        return None
    if image is None or not getattr(image, "size", 0):
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    columns = numpy.abs(cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)).sum(axis=0)
    peak = float(columns.max()) if columns.size else 0.0
    if peak <= 0:
        return None
    # 第 c 列的落差取 c-1 与 c+1 两列之差：缝在 JPEG 里常糊成两列宽，隔一列比才量得到。
    signed = gray.astype(numpy.int16)
    crossing = (numpy.abs(signed[:, 2:] - signed[:, :-2]) > SEAM_STEP).mean(axis=0)
    seams = [0.0, *crossing.tolist(), 0.0] if gray.shape[1] >= 3 else [0.0] * gray.shape[1]
    return ColumnProfile((columns / peak).tolist(), seams)


def file_gradient(path: Path | str) -> Callable[[], ColumnProfile | None]:
    """把一张封面包成惰性的列梯度来源：真要用到时才解码。"""
    def source() -> ColumnProfile | None:
        try:
            import cv2
        except ImportError:                     # pragma: no cover - 缺 vision 依赖组
            return None
        return column_gradient(cv2.imread(str(path)))
    return source


def front_panel_box(width: int, height: int,
                    gradient_source: GradientSource = None,
                    code: str | None = None, title: str = '') -> dict:
    """源图尺寸加列梯度 → 正封那一块的取景框。纯函数，不碰文件也不碰 OpenCV。

    返回 `{"x0", "y0", "x1", "y1", "method"}`，坐标是源图像素，右下开区间。
    封套的框右下角就是源图的右下角，`x0` 就是折痕所在的列——正封是「折痕右边的全部」，
    不是它里面某个形状的子区域。居中正封左右两条缝之间就是框。`method` 为
    `none` 时框是整张图：没有可裁的形状，调用方照原图处理。

    `code` 只用来认宽封套的厂牌：不给就没有这一档，别的判据全由尺寸和梯度决定。
    """
    width, height = int(width or 0), int(height or 0)
    if width <= 0 or height <= 0:
        return {"x0": 0, "y0": 0, "x1": 0, "y1": 0, "method": NONE}
    if is_scene_cover(code, title):
        return _whole(width, height)
    ratio = width / height
    if (is_wide_sleeve_label(code, title)
            and WIDE_SLEEVE_RATIO_MIN <= ratio < WIDE_SLEEVE_RATIO_MAX):
        return _sleeve(width, height, gradient_source, WIDE_PANEL)
    if ratio >= SLEEVE_RATIO_MAX:
        profile = gradient_source() if callable(gradient_source) else gradient_source
        return (center_panel(width, height, getattr(profile, "seams", None))
                or _whole(width, height))
    if ratio < SLEEVE_RATIO_MIN:
        return _whole(width, height)
    profile = gradient_source() if callable(gradient_source) else gradient_source
    return padded_center_panel(width, height, profile) or _sleeve(width, height, profile, DVD_PANEL)


def padded_center_panel(width: int, height: int, profile) -> dict | None:
    """两侧均为低纹理补边、两条满高接缝夹出的居中正封。"""
    seams = getattr(profile, 'seams', None)
    panel = center_panel(width, height, seams)
    if panel is None:
        return None
    left, right = panel['x0'], panel['x1']
    if min(seams[left - 1], seams[right]) < SEAM_MIN_COVERAGE:
        return None
    middle = statistics.median(profile[left:right])
    if middle <= 0 or any(statistics.median(side) >= middle * .25
                          for side in (profile[:left], profile[right:])):
        return None
    return panel


def _sleeve(width: int, height: int, gradient_source: GradientSource,
            shape: PanelShape) -> dict:
    """一张封套的正封框：找到折痕从折痕起，找不到按这一档的先验从右缘量回去。"""
    fold = fold_column(width, height, gradient_source, shape)
    start = fold if fold is not None else round(width - shape.prior * height)
    return _panel(width, height, start, FOLD if fold is not None else RATIO)


def fold_column(width: int, height: int,
                gradient_source: GradientSource = None,
                shape: PanelShape = DVD_PANEL) -> int | None:
    """列梯度里那道书脊折痕所在的列；不成立返回 None。

    只在「折痕右边那块的宽高比落在 `shape.low`～`shape.high`」的那几十列里找。窗口由
    源图高度定，所以高清图和低清图用的是同一条判据；窗口之外再强的边也不看——那是
    画面内容，按它切会切进正面或带出封底。

    窗里够强的边不止一条时，取切出来的正封最贴近 `shape.prior` 的那条，不是最强的
    那条：书脊厚到两条边都落进窗里时，左边那条往往更强，按它切整条书脊都在框里。
    强度不够、却是窄而大体满高的尖峰也算候选（`_sharp_edges`）：书脊上的竖排片名是
    一片宽的高地，强度压过折痕，突出度压不过。

    选中的那一列是斜坡最陡处，不是斜坡尽头，所以还要往右走到梯度落回基线：基线取窗
    内梯度的中位数，每张图各算各的，画面忙的封套门槛自然就高。

    走完斜坡的切点自己不是满高缝、右边窗内还有一条满高缝时，折痕是那条缝
    （`_seam_beyond`）：书脊里的文字边只有梯度，折痕还从上贯到下。缝也没有、右边却有
    一根孤立的尖峰、且尖峰左边比右边忙时，折痕是那根尖峰（`_sharp_beyond`）：书脊和
    正封同底色的封套，折痕只在书脊内容所在的行里存在，满高不了，却是内容到留白的边。
    """
    profile = gradient_source() if callable(gradient_source) else gradient_source
    if profile is None:
        return None
    seams = getattr(profile, "seams", None)
    profile = list(profile)
    if len(profile) != int(width) or not profile:
        return None
    peak = max(profile)
    if peak <= 0:
        return None
    width, height = int(width), int(height)
    low = max(0, round(width - shape.high * height))
    high = min(width - 1, round(width - shape.low * height))
    window = range(low, high + 1)
    if not window:
        return None
    top = max(window, key=profile.__getitem__)
    if profile[top] < peak * FOLD_MIN_STRENGTH:
        return None
    span = round(width * FOLD_EDGE_SPAN)
    reach = round(width * FOLD_PROMINENCE_REACH)
    edges = _rival_edges(profile, window, profile[top], span)
    if seams is not None and len(seams) == width:
        edges += [column for column in _sharp_edges(profile, window, reach, span)
                  if seams[column] >= FOLD_SHARP_MIN_COVERAGE
                  and all(abs(column - other) > span for other in edges)]
    found = min(edges, key=lambda column: abs((width - column) / height - shape.prior))
    baseline = statistics.median(profile[low:high + 1])
    limit = round(width * FOLD_SETTLE_LIMIT)
    cut = _settled(profile, found, baseline, limit, span)
    seam = _seam_beyond(profile, seams, found, cut, window, peak)
    if seam is not None:
        return seam + 1
    sharp = _sharp_beyond(profile, seams, found, cut, window, reach, span)
    if sharp is None:
        return cut
    right_baseline = statistics.median(profile[sharp + span + 1:sharp + reach + 1] or [baseline])
    return _settled(profile, sharp, min(baseline, right_baseline), limit, span)


def _sharp_beyond(profile: list[float], seams: Sequence[float] | None, found: int,
                  cut: int, window: range, reach: int, span: int) -> int | None:
    """切点右边、窗内最靠近切点的那根孤立尖峰；切点自己已经满高，或没有缝数据，返回 None。

    书脊和正封底色相同的封套，折痕在留白的行里根本不存在，只在书脊内容所在的行里
    是一道边：满高覆盖过不了 `FOLD_SHARP_MIN_COVERAGE`，梯度也过不了峭壁那一关，
    于是候选只剩书脊内照片与文字的边，切点落在书脊里（IPZ-602 切在 411，折痕在
    420）。这种折痕在剖面上是一根孤立的尖峰：达到窗内最高突出度 `FOLD_RIVAL_RATIO`
    倍，且是左右各 `reach` 列梯度中位数的 `FOLD_ISOLATION` 倍。正封里紧挨折痕的大字
    也是尖峰，可周围还有别的笔画，中位数压不下去。

    孤立还不够，尖峰得是「书脊内容到正封留白」那个方向的（`_falls_off_to_the_right`）：
    左边是书脊里的照片和文字，右边是正封留白，左侧中位数压过右侧，七张实测 2.3～15 倍。
    正封内容的起笔正相反，左边是折痕到起笔之间的留白、右边是内容本身：PPT-018 的金色
    竖排片名 431 列 0.13 倍、MISM-236 正封里一条细竖线 1162 列 0.66 倍、IPOK-010 正封
    照片的边 1172 列 0.22 倍，三张的切点本来就在折痕上（满高 0.40～0.62，差一点够不到
    `SEAM_MIN_COVERAGE`），不看方向会各切进正封 9～24 列。

    与 `_seam_beyond` 共用两条边界：只往右找（书脊内容的边都在折痕左边），切点自身
    满高就不动（那已经是折痕）。取最靠近切点的那根：折痕右边再有孤立尖峰是正封的
    内容，越过它就切进正面。尖峰还是斜坡最陡处，切点照旧往右走完斜坡。
    """
    if seams is None or len(seams) != len(profile):
        return None
    if max(seams[found:cut + 1]) >= SEAM_MIN_COVERAGE:
        return None
    lifted = _prominence(profile, window, reach)
    if not lifted:
        return None
    best = max(lifted.values())
    if best <= 0:
        return None
    return min((column for column in window if column > cut
                and lifted[column] >= best * FOLD_RIVAL_RATIO
                and profile[column] >= FOLD_ISOLATION * (profile[column] - lifted[column])
                and _falls_off_to_the_right(profile, column, span, reach)),
               default=None)


def _falls_off_to_the_right(profile: list[float], column: int, span: int, reach: int) -> bool:
    """这根尖峰左边比右边忙吗：两侧各取边自身 `span` 之外、`reach` 之内的梯度中位数。

    边自身那几列不算：JPEG 里一道边糊成两三列（GUN-851 的折痕 422、423 两列一样高），
    算进右侧会把右侧抬起来。两侧都取不到列（尖峰贴着源图边缘）就不认。
    """
    left = profile[max(0, column - reach):max(0, column - span)]
    right = profile[column + span + 1:column + reach + 1]
    if not left or not right:
        return False
    return statistics.median(left) > statistics.median(right)


def _seam_beyond(profile: list[float], seams: Sequence[float] | None, found: int,
                 cut: int, window: range, peak: float) -> int | None:
    """切点右边、窗内最靠右的那条满高缝；切点自己已经是满高缝，或没有缝数据，返回 None。

    厚书脊里印着竖排文字，文字的边在梯度上和折痕一样陡，按形状定夺也可能选中它
    （ABW-147：书脊 1922～1978 列，窗内最强的边 1939 是文字边，切在 1941 整条书脊
    留在框里）。文字边不满高，折痕满高：折痕是书脊底色到正封画面的一条直线，从上贯
    到下。所以切点自身不满高、右边窗内还有满高缝时，正封从那条缝的下一列起：缝是
    一列过渡线，不是斜坡，不再往右走（390JAC-077 正封紧挨缝的画面很忙，走会多切 11 列）。缝也得过峭壁
    那一关，免得被一条淡淡的印刷分隔线牵走。
    """
    if seams is None or len(seams) != len(profile):
        return None
    if max(seams[found:cut + 1]) >= SEAM_MIN_COVERAGE:
        return None
    floor = peak * FOLD_MIN_STRENGTH
    return max((column for column in window if column > cut
                and seams[column] >= SEAM_MIN_COVERAGE and profile[column] >= floor),
               default=None)


def center_panel(width: int, height: int, seams: Sequence[float] | None) -> dict | None:
    """画布正中那块正封的框；不是这种版式返回 None。

    缝只在「宽 `PANEL_ASPECT` 倍高、正好居中」的两个边位附近找，一侧够满高就成立，
    另一侧按居中对称补上。缝那一列本身是过渡带，留在框外。
    """
    if seams is None or len(seams) != int(width) or width < 3:
        return None
    width, height = int(width), int(height)
    half = PANEL_ASPECT * height / 2
    reach = max(1, round(width * SEAM_TOLERANCE))

    def seam_near(edge: float) -> int | None:
        span = range(max(1, round(edge) - reach), min(width - 1, round(edge) + reach + 1))
        found = max(span, key=seams.__getitem__, default=None)
        return found if found is not None and seams[found] >= SEAM_MIN_COVERAGE else None

    left, right = seam_near(width / 2 - half), seam_near(width / 2 + half)
    if left is None and right is None:
        return None
    x0 = left + 1 if left is not None else width - right
    x1 = right if right is not None else width - (left + 1)
    if x1 <= x0:
        return None
    return {"x0": x0, "y0": 0, "x1": x1, "y1": height, "method": CENTER}


def _rival_edges(profile: list[float], window: range, top: float,
                 span: int) -> list[int]:
    """窗里强度和最强边相当的那几条边，一条边只留最强的那一列。"""
    ranked = sorted((column for column in window
                     if profile[column] >= top * FOLD_RIVAL_RATIO),
                    key=profile.__getitem__, reverse=True)
    kept: list[int] = []
    for column in ranked:
        if all(abs(column - other) > span for other in kept):
            kept.append(column)
    return kept


def _prominence(profile: list[float], window: range, reach: int) -> dict[int, float]:
    """窗里每一列的突出度：比左右各 `reach` 列的梯度中位数高出多少。`reach` 不足一列为空。"""
    if reach < 1:
        return {}

    def prominence(column: int) -> float:
        around = profile[max(0, column - reach):column] + profile[column + 1:column + reach + 1]
        return profile[column] - statistics.median(around)

    return {column: prominence(column) for column in window}


def _sharp_edges(profile: list[float], window: range, reach: int, span: int) -> list[int]:
    """窗里突出度和最突出那列相当的边，一条边只留一列。"""
    lifted = _prominence(profile, window, reach)
    if not lifted:
        return []
    best = max(lifted.values())
    if best <= 0:
        return []
    ranked = sorted((column for column in window if lifted[column] >= best * FOLD_RIVAL_RATIO),
                    key=lifted.__getitem__, reverse=True)
    kept: list[int] = []
    for column in ranked:
        if all(abs(column - other) > span for other in kept):
            kept.append(column)
    return kept


def _settled(profile: list[float], found: int, baseline: float, limit: int,
             band: int = 0) -> int:
    """从斜坡最陡的那一列往右走到梯度落回基线，书脊的最后几列留在框外。

    折痕常常是一条带：书脊边、几列灰色的折线阴影、再一道边才到正封。两道边之间梯度
    会短暂落回基线，停在那里就把阴影带进了框，卡片左缘留一道细线（ABF-328：1139 与
    1148 两道边，中间 7 列灰）。所以先找 `band` 列内最后一道和折痕相当的边，从它再往右
    走。阴影带只有几列宽；放到整个走程，正封里紧挨折痕的标题字与人物边缘也会被当成
    带的另一侧（DVDMS-996 会切进画面 20 列）。另一侧那道边自己也得过峭壁那一关
    （`FOLD_MIN_STRENGTH`）：折痕本身偏弱的封套，正封里一片花哨的画面处处都和它相当。
    """
    stop = min(found + limit, len(profile) - 1)
    closing = max(profile[found] * FOLD_RIVAL_RATIO, max(profile) * FOLD_MIN_STRENGTH)
    edge = max((column for column in range(found + 1, min(found + band, stop) + 1)
                if profile[column] >= closing), default=found)
    for column in range(edge + 1, stop + 1):
        if profile[column] <= baseline:
            return column
    return stop


def _whole(width: int, height: int) -> dict:
    return {"x0": 0, "y0": 0, "x1": width, "y1": height, "method": NONE}


def _panel(width: int, height: int, start: int, method: str) -> dict:
    """折痕右边的全部就是正封。切不出宽度的按整图处理。"""
    start = max(0, min(int(start), width))
    if width - start <= 0:
        return _whole(width, height)
    return {"x0": start, "y0": 0, "x1": width, "y1": height, "method": method}


def crop_record(code: str | None, width: int, height: int,
                gradient_source: GradientSource = None, *, title: str = '', face: dict | None = None,
                layout: str = '') -> dict:
    """可直接落盘的 sidecar 内容：算法版本、源图尺寸、框。

    番号形态先判：不该裁的图连梯度都不算，`gradient_source` 是惰性的就一次都不解码。
    """
    width, height = int(width or 0), int(height or 0)
    box = (front_panel_box(width, height, gradient_source, code, title)
           if layout != 'still' and crops_to_portrait(code, title) else _whole(width, height))
    if box['method'] != CENTER and face_crosses_spine(width, height, face):
        box = _whole(width, height)
    return {"version": ALGORITHM_VERSION, "px": [width, height], "box": box,
            **({'title': title} if title else {})}


def manual_record(width: int, height: int, box: object) -> dict | None:
    """人手框的那一块 → 可直接落盘的 sidecar；框不成立返回 None。

    形状与算出来的那几档一模一样，只是 `method` 是 `manual` 且多一个 `source`。
    同一份文件装得下两种来路，页面那一侧就不必分辨「读哪一个框」。

    校验借 `images.clamp_box`：写 sidecar 的和读 sidecar 的必须认同一套形状，
    `projection` 拒掉的框要是能写进去，页面会拿到一个永远读不出来的取景。
    """
    edges = images.clamp_box(box, width, height)
    if edges is None:
        return None
    return {"version": ALGORITHM_VERSION, "px": [int(width), int(height)],
            "box": {**edges, "method": MANUAL}, "source": MANUAL_SOURCE}


def is_manual(record: dict | None) -> bool:
    """这份 sidecar 里的框是人定的吗。"""
    if not isinstance(record, dict):
        return False
    box = record.get("box")
    return isinstance(box, dict) and box.get("method") == MANUAL


def sidecar_path(image_path: Path | str) -> Path:
    return Path(image_path).with_suffix(SIDECAR_SUFFIX)


def read_sidecar(image_path: Path | str) -> dict | None:
    try:
        data = json.loads(sidecar_path(image_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_sidecar(image_path: Path | str, record: dict) -> Path:
    path = sidecar_path(image_path)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def is_current(record: dict | None, width: int, height: int) -> bool:
    """这份 sidecar 还作数吗：版本要对得上，源图尺寸也要对得上。

    尺寸也要对，是因为封面会被更大的那张原子替换（`jav_cover_fetch` 只升不降）。
    版本没变而图换了的话，框仍然是按旧尺寸算的，落在新图上是一块错位的区域——
    而错位在页面上和「这张图本来就该这么取景」看不出区别。

    手工框只看尺寸：它后面没有算法，改判据不构成重算它的理由。
    """
    if not isinstance(record, dict):
        return False
    if not is_manual(record) and record.get("version") != ALGORITHM_VERSION:
        return False
    return list(record.get("px") or []) == [int(width), int(height)]


def projection(record: dict | None) -> dict | None:
    """sidecar → API 的 `poster_box` 字段；不该裁、算不出、读不出都是 None。

    给出源图像素坐标加源图尺寸 `px`，消费方据此自己换算。算出来的框永远满高，封套
    那两档还贴右缘、只有 `x0` 是活的；居中正封与手工框的左右两边都可能动，页面一律按
    整个框取景。
    """
    if not isinstance(record, dict):
        return None
    if not is_manual(record) and record.get("version") != ALGORITHM_VERSION:
        return None
    box = record.get("box")
    size = record.get("px")
    if not isinstance(box, dict) or box.get("method") in (None, NONE):
        return None
    try:
        edges = {name: int(box[name]) for name in ("x0", "y0", "x1", "y1")}
        width, height = int(size[0]), int(size[1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if edges["x1"] <= edges["x0"] or edges["y1"] <= edges["y0"]:
        return None
    if edges["x1"] > width or edges["y1"] > height:
        return None
    return {**edges, "method": str(box["method"]), "px": [width, height]}
