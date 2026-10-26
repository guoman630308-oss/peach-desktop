#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""给已落盘的实体图缩出索引页那一档派生件。`/entity-image?thumb=1` 取的就是这里缩好的一份，实测 105 张首屏从 19.2 MB
降到 2.83 MB。

服务端本来就会在第一次请求时现缩，这个脚本只是把那一次挪到没人等的时候：现缩一张
实测 68 ms，一屏一百来张全没缩过的话，第一个打开索引页的人要替后来所有人等这一遍。

缩法与判据都在 `peach.previews.DerivedImageService`，这里只是批量入口；可反复
运行，已经缩好且不比原件旧的默认跳过。派生件缺了不影响显示，页面退回原件。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR
from peach.previews import DerivedImageService, entity_thumb_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="缩出索引页那一档实体图派生件")
    parser.add_argument("--avatars", type=Path, default=GENERATED_DIR / "avatars",
                        help="实体图目录（默认 generated/avatars）")
    parser.add_argument("--limit", type=int, default=0)
    return parser


def run(args: argparse.Namespace) -> int:
    # 派生件目录跟着实体图目录走，和服务端同一条推导，不在这里另写一个默认值。
    service = DerivedImageService(entity_thumb_root(args.avatars))
    images = sorted(args.avatars.glob("*.img"))
    if args.limit:
        images = images[:args.limit]
    print(f"实体图 {len(images)} 张")

    source_total = derived_total = 0
    skipped = 0
    for index, path in enumerate(images, 1):
        source_bytes = path.stat().st_size
        derived = service.thumbnail(path.stem, path)
        if derived is None:
            # 矢量实体图缩不出来，页面照旧取原件。
            skipped += 1
            continue
        source_total += source_bytes
        derived_total += derived.stat().st_size
        if index % 100 == 0:
            print(f"[{index}/{len(images)}]", flush=True)
    if source_total:
        print(f"\n原件 {source_total/1048576:.0f} MB，派生件 {derived_total/1048576:.1f} MB，"
              f"省 {100 - derived_total/source_total*100:.0f}%")
    if skipped:
        print(f"{skipped} 张缩不出来，那几格取原件。")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
