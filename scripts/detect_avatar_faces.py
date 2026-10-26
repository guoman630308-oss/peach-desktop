#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""给已落盘的实体图算人脸位置，供圆形头像取景用。

实体图多为竖版人像（当前 512 张里 458 张竖版），圆框按几何中心裁切会切掉头顶、
把脸压到下半圈。这里用与封面同一套 YuNet 检出最大的人脸，把归一化中心和换算
好的 object-position 写进 sidecar（`<kind>-<id>.face.json`）；取景公式只有一份，
在 `peach.web_contract.face_focus`，契约层读 sidecar 原样下发。

脸框尺寸和源图像素一并落盘。取景不止要挪，还要按「这张脸有多少像素」决定能放大
几倍才不掉清晰度，而那件事没有原图尺寸算不出来：归一化值给得出比例，给不出像素。
倍数本身由页面按框的实际尺寸和设备像素比算，判据在 `frontend/src/card-art/face-frame.ts`。

结果写成每张图一个 sidecar，不写单一索引文件，理由与封面相同：导入任务可能并发
写同目录。脚本可反复运行，已算过的默认跳过。检测与 sidecar 形状都在
`peach.avatar_face`，采集脚本装头像时就地写同一份，这里是补齐用的批量入口。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.avatar_face import face_record, sidecar_path, write_sidecar
from peach.config import GENERATED_DIR
from peach.face_detect import FaceDetector, FaceModelUnavailable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="给实体图算人脸位置，供圆头像取景")
    parser.add_argument("--avatars", type=Path, default=GENERATED_DIR / "avatars",
                        help="实体图目录（默认 generated/avatars）")
    parser.add_argument("--redo", action="store_true", help="重算已有的 sidecar")
    parser.add_argument("--limit", type=int, default=0)
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        detector = FaceDetector()
    except FaceModelUnavailable as error:
        raise SystemExit(str(error))

    images = sorted(args.avatars.glob("*.img"))
    todo = [p for p in images if args.redo or not sidecar_path(p).is_file()]
    if args.limit:
        todo = todo[:args.limit]
    print(f"实体图 {len(images)} 张，待处理 {len(todo)} 张")

    stats = {"focus": 0, "face": 0, "none": 0, "unreadable": 0}
    for index, path in enumerate(todo, 1):
        result = face_record(path, detector)
        if result is None:
            stats["unreadable"] += 1
            print(f"[{index}/{len(todo)}] 读图失败 {path.name}", flush=True)
            continue
        stats["face" if result["face"] else "none"] += 1
        if result.get("focus"):
            stats["focus"] += 1
        write_sidecar(path, result)
    print(f"\n检出 {stats['face']}（其中可取景 {stats['focus']}），"
          f"未检出 {stats['none']}，读图失败 {stats['unreadable']}")
    print("未检出的维持几何居中，不影响显示。")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
