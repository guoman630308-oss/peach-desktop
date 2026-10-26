"""Generate Peach's square raster logo and Windows icon from the supplied artwork."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def generate(source: Path, output_dir: Path) -> None:
    image = Image.open(source).convert("RGBA")
    alpha = image.getchannel("A")
    visible = alpha.point(lambda value: 255 if value > 20 else 0)
    bbox = visible.getbbox()
    if bbox is None:
        raise ValueError("source image has no visible artwork")

    left, top, right, bottom = bbox
    content_size = max(right - left, bottom - top)
    padding = max(1, round(content_size * 0.05))
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    side = content_size + padding * 2
    crop_left = max(0, center_x - side // 2)
    crop_top = max(0, center_y - side // 2)
    crop_left = min(crop_left, image.width - side)
    crop_top = min(crop_top, image.height - side)
    square = image.crop((crop_left, crop_top, crop_left + side, crop_top + side))
    square.putalpha(square.getchannel("A").point(lambda value: value if value > 20 else 0))
    logo = square.resize((1024, 1024), Image.Resampling.LANCZOS)

    output_dir.mkdir(parents=True, exist_ok=True)
    logo.save(output_dir / "peach-logo.png", optimize=True)
    logo.save(
        output_dir / "peach.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("resources"))
    args = parser.parse_args()
    generate(args.source, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
