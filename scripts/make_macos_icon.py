#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把品牌图做成符合 macOS 版式的 .icns。

macOS 的应用图标不是「一张铺满画布的方图」：内容画在一个圆角矩形里，四周留白约
10%，圆角半径约内容边长的 22.5%。直接把 1024×1024 的方形 PNG 当图标用，在 Dock 和
访达里会比周围所有图标都大一圈、四角还是直的。

`iconutil` 需要一个 .iconset 目录，里面是固定命名的 1x/2x 两套位图。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import PROJECT_ROOT


#: macOS 图标网格：内容占画布的比例，以及圆角半径占内容边长的比例。
CONTENT_SCALE = 0.80
CORNER_RATIO = 0.225
#: iconutil 认的尺寸集合（点数，2x 由同名 @2x 提供）。
SIZES = (16, 32, 128, 256, 512)


def squircle(source: Image.Image, edge: int) -> Image.Image:
    """把源图放进带圆角和留白的画布。"""
    canvas = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    inner = max(1, int(edge * CONTENT_SCALE))
    offset = (edge - inner) // 2

    art = source.convert("RGBA").resize((inner, inner), Image.Resampling.LANCZOS)
    rounded = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(rounded).rounded_rectangle(
        (0, 0, inner - 1, inner - 1), radius=int(inner * CORNER_RATIO), fill=255)
    # 源图自己的透明区域也要保留：圆角蒙版和原始 alpha 取交集，否则透明背景会被填成实心。
    art.putalpha(Image.composite(rounded, Image.new("L", rounded.size, 0), art.getchannel("A")))
    canvas.paste(art, (offset, offset), art)
    return canvas


def build(source: Image.Image, destination: Path) -> Path:
    iconset = destination.with_suffix(".iconset")
    if iconset.exists():
        for item in iconset.iterdir():
            item.unlink()
    else:
        iconset.mkdir(parents=True)
    for size in SIZES:
        squircle(source, size).save(iconset / f"icon_{size}x{size}.png")
        squircle(source, size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(
        ["iconutil", "--convert", "icns", str(iconset), "--output", str(destination)],
        check=True)
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 macOS 风格的 .icns 图标")
    parser.add_argument("--source", type=Path,
                        default=PROJECT_ROOT / "resources" / "peach-logo.png")
    parser.add_argument("--output", type=Path,
                        default=PROJECT_ROOT / "resources" / "peach.icns")
    return parser


def run(args: argparse.Namespace) -> int:
    if not args.source.is_file():
        raise SystemExit(f"找不到源图：{args.source}")
    output = build(Image.open(args.source), args.output)
    print(f"已生成 {output}（{output.stat().st_size // 1024} KB）")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
