#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""给已落盘的封面算人脸位置，供卡片取景用。

写死的锚点在多数封套上够用，但人物在画面里的位置差别很大。`object-fit:cover`
一次只裁一个轴，所以两个轴都要算，用哪个交给页面按版式挑：

- 双页封套在 16:9 容器里被纵向裁，纵向锚点决定会不会切掉下巴或留出大片空白；
  横向仍由几何规则定死（正封贴着封套右边缘），不交给检出结果。
- 16:9 官方剧照在大图容器里被横向裁，整幅都是画面，没有「正封那一块」可推。
  横向锚点不跟着人走的话，偏在一侧的人物会整个落到可见窗口外面。

取景复用 `cover_artwork.face_record`：先检查整幅画面的主脸，再判断是否存在可信书脊。
传统封套使用右侧正封人脸，整幅大封使用主脸。记录包含算法版本和源图尺寸，过期缓存自动重算。

结果写成每张封面一个 sidecar（`<番号>.face.json`），不写单一索引文件：抓取器
可能正在并发写同目录，单文件会互相覆盖。脚本可反复运行，当前版本且尺寸匹配的记录默认跳过。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import COVER_DIR
from peach.cover_artwork import FACE_ALGORITHM_VERSION, face_record
from peach.face_detect import FaceDetector, FaceModelUnavailable
from peach.jav_poster_crop import read_sidecar


def detect(path: Path, detector: FaceDetector) -> dict | None:
    import cv2
    image = cv2.imread(str(path))
    return face_record(image, detector, code=path.stem, title=(read_sidecar(path) or {}).get('title', ''))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="给封面算人脸位置，供取景用")
    parser.add_argument("--covers", type=Path, default=COVER_DIR)
    parser.add_argument("--redo", action="store_true", help="重算已有的 sidecar")
    parser.add_argument("--limit", type=int, default=0)
    return parser


def current_face(path):
    from peach.images import measure_image_file
    try:
        record = json.loads(path.with_suffix('.face.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (isinstance(record, dict) and record.get('version') == FACE_ALGORITHM_VERSION
            and record.get('px') == list(measure_image_file(path) or ()))


def run(args: argparse.Namespace) -> int:
    try:
        detector = FaceDetector()
    except FaceModelUnavailable as error:
        raise SystemExit(str(error))

    covers = sorted(args.covers.glob("*.jpg"))
    todo = [p for p in covers
            if args.redo or not current_face(p)]
    if args.limit:
        todo = todo[:args.limit]
    print(f"封面 {len(covers)} 张，待处理 {len(todo)} 张")

    stats = {"face": 0, "none": 0, "unreadable": 0}
    for index, path in enumerate(todo, 1):
        result = detect(path, detector)
        if result is None:
            stats["unreadable"] += 1
            print(f"[{index}/{len(todo)}] 读图失败 {path.name}", flush=True)
            continue
        stats["face" if result["face"] else "none"] += 1
        path.with_suffix(".face.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"\n检出 {stats['face']}，未检出 {stats['none']}，读图失败 {stats['unreadable']}")
    print("未检出的会退回固定取景，不影响显示。")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
