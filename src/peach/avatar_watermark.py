r"""头像上的站点水印：检出、裁掉或修掉。

非 gfriends 来源抓回来的头像常带片商或图库的水印——底部一条 `PRIVATE.com`、
`TEAMSKEET.COM`，角落一枚 `DogFart (SM)` 标，侧边一串半透明 `NUBILES.NET`。
页面按脸取景后这些多半落在取景框外看不见，但看不见不等于不在：字节里还带着别人
的水印，导出、换取景规则或改用整图的地方就会带出来。要的是数据本身干净。

**检出用 PP-OCRv3 的 DB 检测头，不是自己写的启发式。** 先试过 MSER 加几何与
明暗一致性过滤：干净图上的假阳性能压到零，但那 14 张里总共抓到 1 处水印，半透明
的一个字符都抓不到——头发与织物纹理产出的字符状连通块和真文字在单张图上无从分辨。
换成 DB 模型后，这 14 张里 6 处实心水印全中、零假阳性（`peach-data/tools/ppocr/`，
2.4 MB ONNX，和 YuNet 同一个来源与同一套校验）。

**半透明水印它抓不到，所以这里不是全自动的。** `NUBILES.NET`、`MATTIEDOLL`
这类叠在皮肤上的低对比度水印，DB 给不出框。检出结果是候选，人看标注图确认，
漏掉的自己补框——判据与流程在 `scripts/scrub_avatar_watermarks.py`。

移除分两段，顺序固定：

- **能裁就裁。** 水印贴着某条边时裁掉那一条，像素一个都不伪造。约束是不许裁进
  人脸框，也不许把图裁得只剩 `MIN_KEEP`。竖构图全身写真的底部条带正好属于这一类。
- **裁不动才修。** 角落标、画面中部的水印只能 `cv2.inpaint`。mask 取框内的笔画
  而不是整个矩形：填一个纯色方块比水印本身更显眼。
"""
from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import TOOLS_DIR

#: opencv_zoo 的 PP-OCRv3 定版检测模型。走 media. 域名的原因和 YuNet 一样：
#: 仓库用 Git LFS，raw. 域名只会回一个指针文件。
MODEL_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/text_detection_ppocr/text_detection_en_ppocrv3_2023may.onnx"
)
MODEL_SHA256 = "03f550c6b406fda8bf54bd8327815f6c7e2edd98cea02348c93d879254366587"
MODEL_PATH = TOOLS_DIR / "ppocr" / "text_detection_en_ppocrv3_2023may.onnx"

#: 送进网络的方形边长。DB 要 32 的倍数，736 是 opencv_zoo 示例的取值。
INPUT_SIDE = 736
#: DB 自己的两个阈值，沿用 opencv_zoo 示例。
BINARY_THRESHOLD = 0.3
POLYGON_THRESHOLD = 0.5
MAX_CANDIDATES = 200
UNCLIP_RATIO = 2.0
#: 归一化与均值也沿用示例，换了这几个数检出会整体垮掉。
INPUT_SCALE = 1 / 255.0
INPUT_MEAN = (122.67891434, 116.66876762, 104.00698793)

#: 低于这个分数的文字框不当候选。实测 620 张头像上，真水印的分数几乎都在 0.97
#: 以上（`PRIVATE.com` 0.994、`TEAMSKEET.COM` 0.986、角落的 `DogFart` 0.98），
#: 而落在衣服花纹上的假阳性是 0.71 到 0.81。门槛卡在这两群中间。
MIN_SCORE = 0.9
#: 框宽至少要占图宽这一比例。水印是一串字，有横向长度；花纹上那些假阳性是二三十
#: 像素的碎块，尺寸这一条比分数更早把它们挡在外面。
MIN_MARK_SHARE = 0.04
#: 水印的位置先验：框要整体落在距某条边这一比例的带内。画面正中的文字是画面的一
#: 部分（衣服印字、背景招牌），不是水印；实测这一条把裙子花纹和脸上的框全砍掉了。
EDGE_MARGIN = 0.15
#: 裁切后至少要剩下这么多面积。剩得比这还少说明水印不在边上，该走 inpaint。
MIN_KEEP = 0.7
#: 裁切线与人脸框之间留出的余量，按脸高的比例算。
FACE_CLEARANCE = 0.15
#: inpaint 的邻域半径与笔画 mask 的膨胀量，单位像素。
INPAINT_RADIUS = 3
MASK_DILATE = 3
#: 一张图上超过这么多处文字就不当水印看。实测 620 张头像时，13 张检出 8 到 32 处
#: ——它们不是带水印的头像，是作品封面被当成头像装了进去，满屏的宣传文字全被检出。
#: 水印这东西一张图上最多三四处；真到几十处，要换掉的是这张图本身，不是它的像素。
MAX_MARKS = 4


class WatermarkModelUnavailable(RuntimeError):
    """模型取不到。调用方据此决定跳过还是整轮停下，不要静默当成「没有水印」。"""


@dataclass(frozen=True)
class Mark:
    """一处水印的位置与检出分数。人工补的框分数记 1.0。"""

    x: int
    y: int
    w: int
    h: int
    score: float = 1.0

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_model(path: Path | None = None, *, allow_download: bool = True) -> Path:
    """本地那份校验通过就用，否则按固定 URL 取一次再校验。"""
    target = Path(path) if path is not None else MODEL_PATH
    if target.is_file() and _digest(target) == MODEL_SHA256:
        return target
    if not allow_download:
        raise WatermarkModelUnavailable(f"文字检测模型不在 {target}，且不允许下载")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
            partial.write_bytes(response.read())
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise WatermarkModelUnavailable(f"取文字检测模型失败：{error}") from error
    digest = _digest(partial)
    if digest != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        raise WatermarkModelUnavailable(
            f"文字检测模型校验不符：期望 {MODEL_SHA256}，实际 {digest}")
    partial.replace(target)
    return target


class MarkDetector:
    """DB 文字检测的薄封装。一次建好反复用，别每张图重建。"""

    def __init__(self, model: Path | None = None, *, allow_download: bool = True,
                 min_score: float = MIN_SCORE) -> None:
        import cv2

        self.model_path = ensure_model(model, allow_download=allow_download)
        self.min_score = float(min_score)
        net = cv2.dnn.TextDetectionModel_DB(str(self.model_path))
        net.setBinaryThreshold(BINARY_THRESHOLD)
        net.setPolygonThreshold(POLYGON_THRESHOLD)
        net.setMaxCandidates(MAX_CANDIDATES)
        net.setUnclipRatio(UNCLIP_RATIO)
        net.setInputParams(INPUT_SCALE, (INPUT_SIDE, INPUT_SIDE), INPUT_MEAN, True)
        self._net = net

    def detect(self, image) -> tuple[Mark, ...]:
        """图上所有够分的文字框，坐标按原图。这一步不问位置，筛位置是 `on_edge`。"""
        import cv2
        import numpy as np

        height, width = image.shape[:2]
        # DB 对非方形输入会按各边独立缩放，长短边比例一大文字就被压扁。先补成方的。
        side = max(height, width)
        square = np.zeros((side, side, 3), np.uint8)
        square[:height, :width] = image
        quads, scores = self._net.detect(square)
        found = []
        for quad, score in zip(quads, scores):
            if float(score) < self.min_score:
                continue
            x, y, w, h = cv2.boundingRect(np.array(quad))
            x, y = max(0, x), max(0, y)
            w, h = min(w, width - x), min(h, height - y)
            if w >= max(1, int(width * MIN_MARK_SHARE)) and h > 0:
                found.append(Mark(x, y, w, h, round(float(score), 3)))
        return tuple(found)


def on_edge(mark: Mark, width: int, height: int,
            margin: float = EDGE_MARGIN) -> bool:
    """框整体落在某条边的带内才算水印位置。"""
    return (mark.y + mark.h <= height * margin
            or mark.y >= height * (1 - margin)
            or mark.x + mark.w <= width * margin
            or mark.x >= width * (1 - margin))


def nearest_edge(mark: Mark, width: int, height: int,
                 margin: float = EDGE_MARGIN) -> str | None:
    """这处水印贴着哪条边，贴不着任何一条就是 `None`。

    看的是框离那条边有多远，不是框本身有多大。这个区别要紧：人工补的框常常是
    整条边——`performer-7911` 左边那一列水印要用 62×508 的框整条框住，而那张图宽
    360，拿框宽去比 `EDGE_MARGIN` 的 54 像素就成了「不在边上」，明明能无损裁掉
    却要去 inpaint；按距离算，它离左边是 0。

    先按框的长轴定候选边，再在候选里比距离。跨方向直接比像素会判错：底部一条
    300×40 的水印离左边 10 像素、离底边 24 像素，比距离就成了「贴左边」，于是
    去裁左边 310 像素——横着的一条字贴的是水平边，这跟它离侧边多远没有关系。
    """
    gaps = {"left": mark.x, "right": width - (mark.x + mark.w),
            "top": mark.y, "bottom": height - (mark.y + mark.h)}
    if mark.w >= mark.h * 1.5:
        gaps.pop("left"), gaps.pop("right")
    elif mark.h >= mark.w * 1.5:
        gaps.pop("top"), gaps.pop("bottom")
    span = {"left": width, "right": width, "top": height, "bottom": height}
    edge = min(gaps, key=lambda name: gaps[name] / span[name])
    return edge if gaps[edge] <= span[edge] * margin else None


def looks_like_content(marks: tuple[Mark, ...] | list[Mark],
                       limit: int = MAX_MARKS) -> bool:
    """这些框是画面本身的文字而不是水印。成立就整张放过，一处都不要动。

    判据只有一条：数量。带水印的头像顶多三四处框，成片的文字只出在封面、海报和
    带字幕的截图上，而那些图的问题是「不该当头像」，去水印解决不了，涂一遍只会
    把一张错图变成一张糊掉的错图。
    """
    return len(marks) > limit


def _keeps_the_face(crop: tuple[int, int, int, int],
                    face: tuple[int, int, int, int] | None) -> bool:
    """裁切框有没有把脸连同它的留白整个留下。没有脸框就没有这项约束。"""
    if not face:
        return True
    cx, cy, cw, ch = crop
    fx, fy, fw, fh = face
    pad = int(round(fh * FACE_CLEARANCE))
    return (cy <= fy - pad and cy + ch >= fy + fh + pad
            and cx <= fx - pad and cx + cw >= fx + fw + pad)


def crop_box(marks: tuple[Mark, ...] | list[Mark], width: int, height: int,
             face: tuple[int, int, int, int] | None = None
             ) -> tuple[int, int, int, int] | None:
    """裁掉贴边水印后剩下的区域，`None` 表示这批水印一条边都裁不掉。

    **逐边独立判定，一条边裁不动不影响别的边。** 先按每处水印贴哪条边算出各边要
    让出多少，再从代价最小的边开始逐条试：加上这一条后，裁切线不能越过人脸留白，
    剩余面积不能低于 `MIN_KEEP`，两条都过才采纳。裁不掉的边上那些水印留给 inpaint。

    一开始写的是全有或全无——任一条边过不了就放弃整个裁切。实测吃了亏：
    `performer-7911` 左边缘一列名字水印本可无损裁掉，却因为左上角那枚小标落在顶部
    带内、裁顶部会切进脸，连左边一起被否，三处水印全送进 inpaint，结果是一张抹得
    半干不净的图。
    """
    if not marks:
        return None
    wants = {"top": 0, "bottom": height, "left": 0, "right": width}
    for mark in marks:
        edge = nearest_edge(mark, width, height)
        if edge == "top":
            wants["top"] = max(wants["top"], mark.y + mark.h)
        elif edge == "bottom":
            wants["bottom"] = min(wants["bottom"], mark.y)
        elif edge == "left":
            wants["left"] = max(wants["left"], mark.x + mark.w)
        elif edge == "right":
            wants["right"] = min(wants["right"], mark.x)
    costs = {"top": wants["top"] * width,
             "bottom": (height - wants["bottom"]) * width,
             "left": wants["left"] * height,
             "right": (width - wants["right"]) * height}
    taken = {"top": 0, "bottom": height, "left": 0, "right": width}
    for edge in sorted(costs, key=lambda name: costs[name]):
        if not costs[edge]:
            continue
        trial = dict(taken)
        trial[edge] = wants[edge]
        crop = (trial["left"], trial["top"],
                trial["right"] - trial["left"], trial["bottom"] - trial["top"])
        if crop[2] <= 0 or crop[3] <= 0:
            continue
        if crop[2] * crop[3] < width * height * MIN_KEEP:
            continue
        if not _keeps_the_face(crop, face):
            continue
        taken = trial
    crop = (taken["left"], taken["top"],
            taken["right"] - taken["left"], taken["bottom"] - taken["top"])
    return None if crop == (0, 0, width, height) else crop


def remaining(marks: tuple[Mark, ...] | list[Mark],
              crop: tuple[int, int, int, int] | None) -> tuple[Mark, ...]:
    """裁完还留在图里的水印，坐标已换算到裁后的图。"""
    if crop is None:
        return tuple(marks)
    cx, cy, cw, ch = crop
    left = []
    for mark in marks:
        x0, y0 = max(mark.x, cx), max(mark.y, cy)
        x1 = min(mark.x + mark.w, cx + cw)
        y1 = min(mark.y + mark.h, cy + ch)
        if x1 > x0 and y1 > y0:
            left.append(Mark(x0 - cx, y0 - cy, x1 - x0, y1 - y0, mark.score))
    return tuple(left)


def stroke_mask(image, marks: tuple[Mark, ...] | list[Mark]):
    """框内的笔画像素。填整个矩形会留下一块比水印更扎眼的色斑。

    水印字在框内是「一片里最亮或最暗的那些像素」，所以按框内自己的 Otsu 阈值二值化，
    再按哪一侧离框内均值更远选极性，最后膨胀几个像素盖住抗锯齿的边。
    """
    import cv2
    import numpy as np

    height, width = image.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    for mark in marks:
        x0, y0 = max(0, mark.x), max(0, mark.y)
        x1, y1 = min(width, mark.x + mark.w), min(height, mark.y + mark.h)
        patch = gray[y0:y1, x0:x1]
        if patch.size == 0:
            continue
        _, bright = cv2.threshold(patch, 0, 255,
                                  cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # 笔画是少数派：占比小的那一侧才是字，多数派是水印底下的画面。
        share = float(bright.mean()) / 255.0
        strokes = bright if share <= 0.5 else cv2.bitwise_not(bright)
        mask[y0:y1, x0:x1] = strokes
    if MASK_DILATE > 0:
        kernel = np.ones((MASK_DILATE, MASK_DILATE), np.uint8)
        mask = cv2.dilate(mask, kernel)
    return mask


def scrub(image, marks: tuple[Mark, ...] | list[Mark],
          face: tuple[int, int, int, int] | None = None):
    """去掉这些水印，返回处理后的图和一份说明做了什么的报告。"""
    import cv2

    height, width = image.shape[:2]
    report: dict = {"marks": len(marks), "crop": None, "inpainted": 0,
                    "px": [width, height]}
    if not marks:
        return image, report
    crop = crop_box(marks, width, height, face)
    result = image
    if crop is not None:
        cx, cy, cw, ch = crop
        result = image[cy:cy + ch, cx:cx + cw].copy()
        report["crop"] = list(crop)
        report["px"] = [cw, ch]
    left = remaining(marks, crop)
    if left:
        result = cv2.inpaint(result, stroke_mask(result, left),
                             INPAINT_RADIUS, cv2.INPAINT_TELEA)
        report["inpainted"] = len(left)
    return result, report
