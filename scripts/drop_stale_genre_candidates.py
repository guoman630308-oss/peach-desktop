#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""把 genre 还停在英文那一层的资料候选摘掉，让下一趟「只采集」重抓它们。

判据在 `peach.stale_candidates`。摘掉之后那几条在复核页上暂时消失，采集回来的
是同一批片子的日文原词。不碰 ledger，只改复核候选文件，并先留一份备份。

默认只报数，`--apply` 才改文件。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR
from peach.library_processing import FIELDS
from peach.review_csv import read_rows, write_rows
from peach.stale_candidates import is_english, stale_genre_rows, without

CANDIDATES = GENERATED_DIR / "library-metadata-field-candidates.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="摘掉 genre 还停在英文的资料候选")
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--apply", action="store_true", help="真的改文件；默认只报数")
    return parser


def run(args: argparse.Namespace) -> int:
    rows = read_rows(args.candidates)
    stale = stale_genre_rows(rows)
    words = sorted({word for row in stale for word in _words(row)})
    print(f"候选 {len(rows)} 行，其中 genre 停在英文的 {len(stale)} 行")
    print(f"涉及资产 {len({row['asset_id'] for row in stale})} 个、英文词 {len(words)} 个")
    print("  " + "、".join(words[:20]) + ("…" if len(words) > 20 else ""))

    if args.apply:
        backup = args.candidates.with_suffix(".pre-japanese-genres.csv")
        shutil.copy2(args.candidates, backup)
        write_rows(args.candidates, FIELDS, without(rows, stale), atomic=True)
        print(f"已备份 → {backup}")
        print("已摘掉。下一趟「只采集」会重抓这批片子的 genre。")
    else:
        print("这是预览：候选文件未改。确认后再加 --apply。")
    return 0


def _words(row: dict) -> set[str]:
    found = set()
    for candidate in json.loads(row.get("candidates_json") or "[]"):
        found |= {word for word in candidate.get("unmapped_genres") or [] if is_english(word)}
    return found


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
