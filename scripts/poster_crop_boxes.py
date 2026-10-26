#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""给已落盘的封面算正封的取景框，写进边车。

用法（默认只看不写）：

    python scripts/poster_crop_boxes.py
    python scripts/poster_crop_boxes.py --apply
    python scripts/poster_crop_boxes.py --apply --redo

判据全在 `peach.jav_poster_crop`：番号形态决定该不该裁，Sobel 列梯度决定书脊折痕
在哪，找不到折痕就按正封宽高比的先验从右缘量回去。这里只负责遍历、统计和落盘。

**原图一个字节都不动。** 产物是 `<番号>.poster.json`，和人脸取景的
`<番号>.face.json` 同目录、同命名风格，各描述一件事。

可重入：默认只处理边车缺失、算法版本落后或源图尺寸对不上的封面，所以中断之后再跑
一遍就是续跑。封面被更大的那张替换过（`fetch_jav_covers.py --upgrade-existing`
只升不降）时尺寸对不上，同样会重算。`--redo` 无条件全部重算。

失败不写成正常值：图读不出来只计进 `unreadable` 并跳过，不留一个按零尺寸算出来的
框——那种框在页面上和真框看不出区别。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import jav_poster_crop  # noqa: E402
from peach.config import COVER_DIR  # noqa: E402

#: 每处理这么多张报一次进度。封面目录是千张量级，一张一行会把日志淹掉。
PROGRESS_EVERY = 100
#: 每一档在报告里带几个样例路径，供人抽查。
SAMPLES = 3


def image_size(path: Path) -> tuple[int, int] | None:
    """封面的像素尺寸；读不出来返回 None。只读图头，不解全图。"""
    try:
        from PIL import Image
    except ImportError:                         # pragma: no cover - 基础依赖
        return None
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None


def pending(covers: list[Path], redo: bool) -> list[tuple[Path, tuple[int, int]]]:
    """待处理的封面加它们的尺寸。读不出尺寸的留给主循环去计数。"""
    todo: list[tuple[Path, tuple[int, int]]] = []
    for path in covers:
        size = image_size(path)
        if size is None:
            todo.append((path, (0, 0)))
            continue
        if redo or not jav_poster_crop.is_current(
                jav_poster_crop.read_sidecar(path), *size):
            todo.append((path, size))
    return todo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="给封面算正封取景框")
    parser.add_argument("--covers", type=Path, default=COVER_DIR)
    parser.add_argument("--redo", action="store_true",
                        help="无条件重算，不看边车是否还作数")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true", help="真的写边车")
    return parser


def run(args: argparse.Namespace) -> int:
    covers = sorted(args.covers.glob("*.jpg")) if args.covers.is_dir() else []
    todo = pending(covers, args.redo)
    if args.limit:
        todo = todo[:args.limit]
    print(f"封面 {len(covers)} 张，待处理 {len(todo)} 张"
          f"（{'写入边车' if args.apply else '只看不写'}）", flush=True)

    methods = (jav_poster_crop.FOLD, jav_poster_crop.RATIO, jav_poster_crop.CENTER,
               jav_poster_crop.NONE)
    counts = {**dict.fromkeys(methods, 0), "unreadable": 0}
    samples: dict[str, list[str]] = {}
    for index, (path, size) in enumerate(todo, 1):
        width, height = size
        if not width or not height:
            counts["unreadable"] += 1
            print(f"[{index}/{len(todo)}] 读图失败 {path.name}", flush=True)
            continue
        try:
            face = json.loads(path.with_suffix('.face.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            face = {}
        record = jav_poster_crop.crop_record(
            path.stem, width, height, jav_poster_crop.file_gradient(path),
            title=(jav_poster_crop.read_sidecar(path) or {}).get('title', ''),
            face=face.get('face') if face.get('px') == [width, height] else None,
            layout=face.get('layout', '') if face.get('px') == [width, height] else '')
        method = record["box"]["method"]
        counts[method] += 1
        bucket = samples.setdefault(method, [])
        if len(bucket) < SAMPLES:
            bucket.append(f"{path.name} {width}×{height} → "
                          + " ".join(f"{key}={record['box'][key]}"
                                     for key in ("x0", "y0", "x1", "y1")))
        if args.apply:
            jav_poster_crop.write_sidecar(path, record)
        if index % PROGRESS_EVERY == 0:
            print(f"[{index}/{len(todo)}] "
                  + "，".join(f"{key} {value}" for key, value in counts.items()),
                  flush=True)

    print("\n折痕 {fold}，右半居中 {ratio}，居中正封 {center}，不裁 {none}，"
          "读图失败 {unreadable}".format(
              fold=counts[jav_poster_crop.FOLD], ratio=counts[jav_poster_crop.RATIO],
              center=counts[jav_poster_crop.CENTER], none=counts[jav_poster_crop.NONE],
              unreadable=counts["unreadable"]))
    for method in methods:
        for line in samples.get(method, []):
            print(f"  {method}: {line}")
    if not args.apply:
        print("只看不写。加 --apply 才写边车；原图任何一档都不改。")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
