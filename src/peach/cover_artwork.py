"""原子安装 JAV 封面及与当前图片匹配的取景 sidecar。"""
from __future__ import annotations

import json
import os
import threading
import uuid
from functools import lru_cache
from pathlib import Path

from filelock import FileLock

from . import jav_poster_crop
from .face_detect import FaceDetector, FaceModelUnavailable, decode, main_face
from .jav_cover_fetch import Unavailable, is_javdb_image

FRONT_START = 0.468
MIN_FACE_Y, MAX_FACE_Y = 0.05, 0.60
FACE_ALGORITHM_VERSION = 'cover-face-v4'
PROMOTIONAL_ASPECTS = (4 / 3, 2 ** .5)
_INSTALL_LOCK = threading.RLock()


def has_javdb_source(path: Path) -> bool:
    """封面旁的来源证据是否指向 JavDB 转存图。"""
    try:
        evidence = json.loads(Path(path).with_suffix('.scraping.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(evidence, dict) and is_javdb_image(evidence.get('source_url'), evidence.get('source'))


@lru_cache(maxsize=1)
def _default_detector() -> FaceDetector:
    return FaceDetector(allow_download=False)


def metadata_snapshots(root: Path, code: str) -> list[tuple[str, dict]]:
    """当前番号已保存的来源快照；字段仍由各自的来源契约解释。"""
    found = []
    for path in sorted(Path(root).glob(code + '-*.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            found.append((payload.get('source') or path.stem[len(code) + 1:], payload))
    return found


def _dominant_left_face(image, largest, faces, *, code, title):
    """没有可辨书脊、左侧主脸明显大于右侧拼图时，保留整幅宣传画面。"""
    height, width = image.shape[:2]
    if not (jav_poster_crop.SLEEVE_RATIO_MIN <= width / height < jav_poster_crop.SLEEVE_RATIO_MAX):
        return False
    spine = 1 - jav_poster_crop.PANEL_ASPECT * height / width
    if largest is None or largest.cx >= spine or largest.score < .8 or largest.width < .075 or largest.height < .12:
        return False
    front = main_face([face for face in faces if face.cx >= spine])
    if front and largest.width * largest.height < 2 * front.width * front.height:
        return False
    box = jav_poster_crop.front_panel_box(width, height, lambda: jav_poster_crop.column_gradient(image), code, title)
    return box['method'] == jav_poster_crop.RATIO


def _promotional_canvas(image, *, code, title):
    """4:3 与纸张比例的画布没有可辨书脊时保留整幅。"""
    height, width = image.shape[:2]
    if (min(abs(width / height - ratio) for ratio in PROMOTIONAL_ASPECTS) >= .015
            or not jav_poster_crop.crops_to_portrait(code, title)):
        return False
    box = jav_poster_crop.front_panel_box(width, height, lambda: jav_poster_crop.column_gradient(image), code, title)
    return box['method'] == jav_poster_crop.RATIO


def _full_picture_face(faces):
    """宣传大封允许明显占据主体的闭眼或侧脸，微弱的大框仍由强脸优先。"""
    strong = main_face(faces)
    if strong is None:
        return None
    largest = max(faces, key=lambda face: face.area)
    return largest if largest.score >= .7 and largest.area >= 4 * strong.area else strong


def face_record(image, detector, *, code: str, title: str = '') -> dict | None:
    """已解码的封面 → 页面取景记录；无法解码时不生成记录。

    脸框宽高与源图尺寸 `px` 也记下，形状与头像边车（`avatar_face.face_record_of`）
    一致：卡片取景只用脸心，换头像要按脸宽在封面上框出一块方图
    （`avatar_cover_face.face_square`），而 `px` 对不上当前这张图时那一块不作数。
    """
    if image is None or not getattr(image, "size", 0):
        return None
    height, width = image.shape[:2]
    ratio = width / height if height else 0
    faces = detector.detect(image)
    portraits = [face for face in faces if MIN_FACE_Y <= face.cy <= MAX_FACE_Y]
    largest = main_face(portraits)
    full_cover = jav_poster_crop.face_crosses_spine(width, height, {
        'cx': largest.cx, 'w': largest.width, 'h': largest.height,
        'score': largest.score} if largest else None) or _dominant_left_face(
            image, largest, portraits, code=code, title=title) or _promotional_canvas(image, code=code, title=title)
    if full_cover:
        panel = jav_poster_crop.front_panel_box(width, height, lambda: jav_poster_crop.column_gradient(image), code, title)
        full_cover = panel['method'] != jav_poster_crop.CENTER
    if (not full_cover and jav_poster_crop.crops_to_portrait(code, title)
            and (jav_poster_crop.SLEEVE_RATIO_MIN <= ratio < jav_poster_crop.SLEEVE_RATIO_MAX
                 or (jav_poster_crop.is_wide_sleeve_label(code, title)
                     and jav_poster_crop.WIDE_SLEEVE_RATIO_MIN <= ratio < jav_poster_crop.WIDE_SLEEVE_RATIO_MAX))):
        faces = [face for face in faces if face.cx >= FRONT_START]
    low, high = (0.0, 1.0) if full_cover or jav_poster_crop.is_scene_cover(code, title) else (MIN_FACE_Y, MAX_FACE_Y)
    faces = [face for face in faces if low <= face.cy <= high]
    best = _full_picture_face(faces) if full_cover else main_face(faces)
    return {"version": FACE_ALGORITHM_VERSION, "ratio": round(ratio, 3), "px": [int(width), int(height)],
            **({'layout': 'still'} if full_cover or jav_poster_crop.is_scene_cover(code, title) else {}), "face": (
        {"cx": best.cx, "cy": best.cy, "w": best.width, "h": best.height,
         "score": best.score} if best else None)}


def _sidecars(code: str, payload: bytes, size: tuple[int, int], detector=None, evidence=None) -> dict[str, bytes]:
    title = str((evidence or {}).get('title') or '')
    image = None
    try:
        image = decode(payload)
    except ImportError:
        pass
    try:
        active = detector if detector is not None else _default_detector()
        face = face_record(image, active, code=code, title=title)
    except (FaceModelUnavailable, ImportError):
        face = None
    poster = jav_poster_crop.crop_record(
        code, size[0], size[1],
        (lambda: jav_poster_crop.column_gradient(image)) if image is not None else None,
        title=title, face=(face or {}).get('face'), layout=(face or {}).get('layout', ''),
    )
    if (face is None and min(abs(size[0] / size[1] - ratio) for ratio in PROMOTIONAL_ASPECTS) < .015
            and poster['box']['method'] == jav_poster_crop.RATIO):
        face = {'px': list(size), 'layout': 'still', 'face': None}
        poster = jav_poster_crop.crop_record(code, *size, title=title, layout='still')
    records = {jav_poster_crop.SIDECAR_SUFFIX: poster}
    if face is not None:
        records[".face.json"] = face
    return {suffix: json.dumps(record, ensure_ascii=False).encode("utf-8")
            for suffix, record in records.items()}


def install_cover(target: Path, code: str, payload: bytes, size: tuple[int, int], *,
                  evidence: dict | None = None, detector=None) -> None:
    """准备完成后安装整组文件；失败时还原旧组，避免图片与取景跨版本。"""
    if evidence and is_javdb_image(evidence.get('source_url'), evidence.get('source')):
        raise Unavailable('未取得无 JavDB 水印的封面')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    prepared = _sidecars(code, payload, size, detector, evidence)
    if evidence is not None:
        prepared[".scraping.json"] = json.dumps(evidence, ensure_ascii=False).encode("utf-8")
    token = uuid.uuid4().hex
    managed = [target, *(target.with_suffix(suffix) for suffix in (
        ".face.json", jav_poster_crop.SIDECAR_SUFFIX, ".scraping.json"))]
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    try:
        image_temp = target.with_name(f".{target.name}.{token}.tmp")
        image_temp.write_bytes(payload)
        staged[target] = image_temp
        for suffix, data in prepared.items():
            temporary = target.with_name(f".{target.stem}{suffix}.{token}.tmp")
            temporary.write_bytes(data)
            staged[target.with_suffix(suffix)] = temporary
        with _INSTALL_LOCK, FileLock(
                str(target.parent / ".cover-install.lock"), thread_local=False):
            try:
                for current in managed:
                    if current.exists():
                        backup = current.with_name(f".{current.name}.{token}.bak")
                        os.replace(current, backup)
                        backups[current] = backup
                for current, temporary in staged.items():
                    os.replace(temporary, current)
            except Exception:
                for current in managed:
                    current.unlink(missing_ok=True)
                for current, backup in backups.items():
                    os.replace(backup, current)
                raise
            for backup in backups.values():
                backup.unlink(missing_ok=True)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
