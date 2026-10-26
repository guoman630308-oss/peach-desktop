r"""人脸检出：OpenCV 的 YuNet，取代已经跑不起来的 Haar 级联。

**先说清一件常被混淆的事：YuNet 不是 OpenCV 的替代品，它就是 OpenCV 的一部分。**
`cv2.FaceDetectorYN` 是 OpenCV 的 API，YuNet 是它跑的那个模型。解码、色彩转换、
缩放仍然要 OpenCV。这里换掉的只是「检出器」这一层：

- Haar 级联：2001 年的算法，模型是随 wheel 分发的 XML。
- YuNet：2021 年的小型 CNN，模型是一个 232 KB 的 ONNX，走 OpenCV 的 DNN 模块。

换的直接原因是旧路径已经跑不起来。`pyproject.toml` 钉的是
`opencv-python-headless==5.0.0.93`，而 OpenCV 5 把 Haar 级联移出了 Python wheel：
`cv2.CascadeClassifier` 不存在，`cv2/data/` 只剩 `__init__.py`。两个取景脚本因此
直接抛 `AttributeError`，954 张封面一个 sidecar 都没有，「人脸取景」从来没生效过。

换的第二个原因是召回。`docs/REUSE.md` 记过一次实测：512 张头像 Haar 检出 313；
封面脚本自己的注释也写着 46 张检出 24。也就是漏掉三分之一以上，还得靠额外规则
丢掉假阳性（`278GYAN-017` 检到画面最左的剧照拼贴，`KAVR-428` 一张图检出 7 个框）。
YuNet 给的是框 + 5 个关键点 + 置信度，可以按分数卡，不必靠位置猜。

模型不进 Git：它是二进制资产，和 ffmpeg 一样放 `peach-data/tools/`。首次运行按
固定 URL 取一次并校验 sha256——版本要能复现，不能「取到什么算什么」。
"""
from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

from .config import TOOLS_DIR

#: opencv_zoo 的 YuNet 定版模型。走 media. 域名是因为仓库用 Git LFS，
#: raw. 域名只会回一个 131 字节的指针文件。
MODEL_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
MODEL_PATH = TOOLS_DIR / "yunet" / "face_detection_yunet_2023mar.onnx"

#: 低于这个分数的检出不算数。YuNet 会给出置信度，不必再靠「落在左半边就是假阳性」
#: 这类位置规则去猜——那条规则只在长封套上成立，换个版式就不成立。
DEFAULT_SCORE = 0.6
#: NMS 阈值沿用 opencv_zoo 示例的默认值。
DEFAULT_NMS = 0.3
#: 送进网络的几个长边，坐标按各自的缩放比还原回原图。
#:
#: 只检一个尺度不够：YuNet 是定尺寸输入，同一张脸在不同尺度上的分数能差出一倍还多。
#: 实测题材 `xenoblade` 那张竖图里的正脸——长边 320 得 0.63、640 得 0.70，到 1280 只
#: 剩 0.27；同一张图上罩在躯干的误检反过来，320 上根本没有，1280 上涨到 0.65。于是
#: 「只在最大那一档检一次」把这张图判成了「脸在躯干上」。分数在这里不是脸的属性，
#: 是「这张脸在这个尺度上有多像脸」的一次读数，几次读数合起来才是它的分（见 `detect`）。
#:
#: 只往下缩不往上放：放大不会凭空多出细节，却会让暗部噪点和布料褶皱变成够大的「脸」。
DETECT_SIDES = (320, 640, 1280)
#: 两个框重叠到这个程度，就当成同一张脸在两个尺度上的两次读数，而不是两张脸。
SAME_FACE_IOU = 0.35
#: 单次读数低于这个分数不算「在这个尺度上认得出」。收读数的门比留脸的门低一截：
#: 一张脸在三个尺度上读 0.9、0.85、0.3，中位数是 0.85，那个 0.3 也是一次读数。
READ_FLOOR = 0.2
#: 挑主角时，分数落后最好那张这么多的框不参与「取最大」。
SCORE_MARGIN = 0.1
#: 送检前的最小长边。图标常常只有 32×32、64×64，YuNet 在这个尺寸上几乎检不出东西——
#: 它的输入是定尺寸的，脸在原图里只占十几个像素时进网络已经糊成一团。放大到这个数
#: 再检，问的才是「这张图里是不是一张脸」，而不是「这张图够不够大」。
MIN_DETECT_SIDE = 320


class FaceModelUnavailable(RuntimeError):
    """模型取不到。调用方据此决定是跳过还是整轮停下，不要静默当成「没有脸」。"""


@dataclass(frozen=True)
class Face:
    """一张脸在原图里的归一化位置。`score` 是模型给的置信度。

    `landmarks` 是 YuNet 给的五个关键点（右眼、左眼、鼻尖、右嘴角、左嘴角），按
    `(x, y)` 交替、同样归一化。比对要先按这五个点把脸摆正（`face_match`），取景用不上它。
    """

    cx: float
    cy: float
    width: float
    height: float
    score: float
    landmarks: tuple[float, ...] = ()

    @property
    def area(self) -> float:
        return self.width * self.height


def main_face(faces: list[Face], margin: float = SCORE_MARGIN) -> Face | None:
    """主角那张脸：先按分数筛掉明显不如最好那张的框，再在剩下的里取最大。

    只按面积挑会被「大而勉强」的误检抢走。实测 `performer-8218`（jae 名录的
    600×1000 人像）：YuNet 给了两个框，脸是 0.202×0.170 分 0.928，另一个大一倍多、
    罩在胸口，分 0.798——圆头像于是取景在胸口。只按分数挑则会被背景里那张小而清晰的
    脸抢走。两个都用一次，各挡住对方的失败例。

    这道 margin 挡不住「大而勉强」里分数够近的那一类：封面 `200GANA-2156` 上罩在胯
    下的框 0.80、脸 0.89，差不到 0.1，面积大七成。那一类由 `detect` 的中位数挡——
    它们在别的尺度上读数塌下来，根本进不到这里。
    """
    if not faces:
        return None
    best_score = max(face.score for face in faces)
    strong = [face for face in faces if face.score >= best_score - margin]
    return max(strong, key=lambda face: face.area)


def decode(payload: bytes):
    """图片字节 → OpenCV 的 BGR 数组；解不开返回 None。

    调用方手里常常只有字节（刚从站上取回来的一枚图标），不必先绕一趟 PIL 再转格式。
    """
    import cv2
    import numpy

    if not payload:
        return None
    image = cv2.imdecode(numpy.frombuffer(payload, numpy.uint8), cv2.IMREAD_COLOR)
    if image is None or not image.size:
        return None
    return image


def shows_a_face(payload: bytes, detector: "FaceDetector") -> Face | None:
    """这几个字节画的是一张脸吗；是就返回那张脸，用来当「这不是标识」的证据。

    小图先按长边放大到 `MIN_DETECT_SIDE`：一枚 64×64 的头像照原样送进去检不出任何东西，
    于是「没检到脸」会同时意味着「这是标识」和「这张图太小」，两件事分不开。
    """
    image = decode(payload)
    if image is None:
        return None
    return main_face(detector.detect(_upscale(image)))


def _upscale(image, side: int = MIN_DETECT_SIDE):
    import cv2

    height, width = image.shape[:2]
    longest = max(height, width)
    if not longest or longest >= side:
        return image
    scale = side / longest
    return cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                      interpolation=cv2.INTER_CUBIC)


def ensure_model(path: Path | None = None, *, allow_download: bool = True) -> Path:
    """返回可用的模型路径；本地没有且允许联网时取一次。

    校验 sha256 而不是只看文件在不在：半个下载同样留下一个文件，而它会让
    `FaceDetectorYN_create` 抛一个和网络毫无关系的错。
    """
    target = Path(path) if path is not None else MODEL_PATH
    return fetch_model(target, MODEL_URL, MODEL_SHA256,
                       allow_download=allow_download, label="人脸模型")


def fetch_model(target: Path, url: str, sha256: str, *, allow_download: bool,
                label: str, timeout: float = 60) -> Path:
    """按固定地址与 sha256 取一个 opencv_zoo 模型，检出与比对两个模型共用这一条。

    本地那份哈希对得上就直接用；对不上先删掉，再按 `allow_download` 决定取不取。
    取不到、校验不符一律抛 `FaceModelUnavailable`，由调用方决定跳过还是停下。
    """
    target = Path(target)
    if target.is_file() and _digest(target) == sha256:
        return target
    if target.is_file():
        target.unlink()
    if not allow_download:
        raise FaceModelUnavailable(f"缺少{label}：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read()
    except OSError as error:
        raise FaceModelUnavailable(f"取{label}失败：{error}") from error
    digest = hashlib.sha256(payload).hexdigest()
    if digest != sha256:
        raise FaceModelUnavailable(
            f"{label}校验不符：期望 {sha256}，实际 {digest}")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return target


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FaceDetector:
    """YuNet 检出器。一次构造反复使用，输入尺寸每张图现设。"""

    def __init__(self, model: Path | None = None, *, score: float = DEFAULT_SCORE,
                 nms: float = DEFAULT_NMS, allow_download: bool = True) -> None:
        import cv2

        self._cv2 = cv2
        self.score = score
        self.model_path = ensure_model(model, allow_download=allow_download)
        create = getattr(cv2, "FaceDetectorYN_create", None)
        if create is None:                       # pragma: no cover - 依赖版本兜底
            raise FaceModelUnavailable(
                "当前 OpenCV 没有 FaceDetectorYN；需要 4.5.4 以上")
        # 模型那道门只管「算不算一次读数」，留不留这张脸由 `detect` 按中位数判。
        self._detector = create(str(self.model_path), "", (320, 320),
                                min(score, READ_FLOOR), nms, 5000)

    def detect(self, image) -> list[Face]:
        """返回归一化坐标的人脸，按面积从大到小。

        `DETECT_SIDES` 里的每一档各检一次，重叠到 `SAME_FACE_IOU` 的框算同一张脸在
        几个尺度上的几次读数。框取读数最高的那一次——坐标不去平均，平均出来的框哪一
        次都不是。分数取几次读数的中位数，某一档上认不出就记 0，所以一张脸要被算数，
        得在过半的尺度上都认得出。

        取中位数而不是最高分：误检的框往往只在一个尺度上高。实测封面 `SRN-104`，
        罩住整个身体的那个框在长边 320 上得 0.85，到 640、1280 只剩 0.31 和 0.49；
        同一张图上真正的脸是 0.66、0.89、0.88。取最高分两个框打平，取中位数差出一倍。

        同一张图在几个档上可能缩成同一个尺寸（原图比某一档还小时按原样送检），
        那种重复只检一次，缺的那次不算漏检——补 0 会把小图上的脸全判掉。
        """
        height, width = image.shape[:2]
        if not height or not width:
            return []
        groups: list[list[Face]] = []
        seen: set[tuple[int, int]] = set()
        for side in DETECT_SIDES:
            for face in self._detect_at(image, side, seen):
                for group in groups:
                    if _overlap(group[0], face) >= SAME_FACE_IOU:
                        group.append(face)
                        break
                else:
                    groups.append([face])
        faces = []
        for group in groups:
            best = max(group, key=lambda face: face.score)
            reads = sorted((face.score for face in group), reverse=True)
            reads += [0.0] * (len(seen) - len(reads))
            score = reads[len(reads) // 2]
            if score >= self.score:
                faces.append(replace(best, score=round(score, 3)))
        faces.sort(key=lambda face: face.area, reverse=True)
        return faces

    def _detect_at(self, image, side: int, seen: set[tuple[int, int]]) -> list[Face]:
        """把图缩到这一档的长边再检一次；坐标按缩放比还原回原图。

        归一化之后缩放比自然抵消，所以只要保证宽高用的是同一张图的。只往下缩：
        原图比这一档还小时按原样送检。
        """
        cv2 = self._cv2
        height, width = image.shape[:2]
        scale = min(1.0, side / max(height, width))
        source = (cv2.resize(image, (max(1, int(width * scale)),
                                     max(1, int(height * scale))))
                  if scale < 1.0 else image)
        rows, cols = source.shape[:2]
        if (cols, rows) in seen:
            return []
        seen.add((cols, rows))
        self._detector.setInputSize((cols, rows))
        _, raw = self._detector.detect(source)
        if raw is None:
            return []
        faces = []
        for row in raw:
            x, y, w, h, *_rest = row.tolist()
            confidence = float(row[-1])
            if confidence < min(self.score, READ_FLOOR) or w <= 0 or h <= 0:
                continue
            points = _rest[:10]
            faces.append(Face(
                cx=round(min(1.0, max(0.0, (x + w / 2) / cols)), 3),
                cy=round(min(1.0, max(0.0, (y + h / 2) / rows)), 3),
                width=round(min(1.0, w / cols), 3),
                height=round(min(1.0, h / rows), 3),
                score=round(confidence, 3),
                landmarks=tuple(round(value / (cols if index % 2 == 0 else rows), 4)
                                for index, value in enumerate(points))
                if len(points) == 10 else (),
            ))
        return faces


def _overlap(one: Face, other: Face) -> float:
    """两个框的交并比。判的是「这是不是同一张脸的两次读数」。"""
    left = max(one.cx - one.width / 2, other.cx - other.width / 2)
    right = min(one.cx + one.width / 2, other.cx + other.width / 2)
    top = max(one.cy - one.height / 2, other.cy - other.height / 2)
    bottom = min(one.cy + one.height / 2, other.cy + other.height / 2)
    inner = max(0.0, right - left) * max(0.0, bottom - top)
    union = one.area + other.area - inner
    return inner / union if union > 0 else 0.0
