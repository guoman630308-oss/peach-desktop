#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无番号视频的联网识别清单与候选合并。

`worklist` 按文件名生成可搜索的查询并标出同目录配套图片；智能体或人联网核对后，
按 `asset_id,field,value,source_url,confidence,note` 填回一份 CSV，再用 `ingest`
合并进 `/review` 读的 `library-metadata-field-candidates.csv`。脚本本身不写账本，
候选仍要人工批准。

用法:
    python scripts/identify_resources.py worklist --root "B:\\xxr\\0208 (23)"
    python scripts/identify_resources.py ingest --results identification-results.csv
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.media_sources import SCANNABLE_LOCATIONS
from peach.resource_identification import (
    CANDIDATE_FILENAME,
    build_worklist,
    ingest_results,
)
from peach.review_csv import read_rows, write_rows
from peach.scripting import open_readonly

WORKLIST_FIELDS = [
    "asset_id", "location", "path", "name", "size_gb", "duration", "metadata_hits",
    "performer_guess", "cover_path", "existing_creator", "existing_title",
    "query_primary", "query_variants",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="无番号视频的检索清单与候选合并")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    worklist = sub.add_parser("worklist", help="生成联网识别清单")
    worklist.add_argument("--location", action="append", choices=SCANNABLE_LOCATIONS)
    worklist.add_argument("--root", help="账本口径的目录前缀，例如 B:\\xxr\\0208 (23)")
    worklist.add_argument("--paired-only", action="store_true", help="只列有同目录配套图片的")
    worklist.add_argument("--sparse-only", action="store_true",
                          help="只列元信息最少的（无创作者／标题／厂牌／系列／演员）")
    worklist.add_argument("--limit", type=int, default=0)
    worklist.add_argument("--out", type=Path)

    ingest = sub.add_parser("ingest", help="把核对结果合并成字段候选")
    ingest.add_argument("--results", type=Path, required=True)
    ingest.add_argument("--candidates", type=Path,
                        default=GENERATED_DIR / CANDIDATE_FILENAME)
    return parser


def cmd_worklist(args: argparse.Namespace) -> int:
    connection = open_readonly(args.db)
    try:
        rows = build_worklist(
            connection,
            locations=tuple(args.location or SCANNABLE_LOCATIONS),
            prefix=args.root, paired_only=args.paired_only,
            sparse_only=args.sparse_only, limit=args.limit,
        )
    finally:
        connection.close()
    output = args.out or GENERATED_DIR / time.strftime(
        "resource-identification-%Y%m%d-%H%M%S.csv")
    write_rows(output, WORKLIST_FIELDS, rows, atomic=True)
    print(f"待识别 {len(rows)} 条 → {output}")
    for row in rows[:15]:
        cover = "有图" if row["cover_path"] else "无图"
        guess = f" 演出者线索={row['performer_guess']}" if row["performer_guess"] else ""
        print(f"  {row['asset_id']:>7} 元信息{row['metadata_hits']} {cover} "
              f"{row['size_gb']:>8.2f}GB {str(row['name'])[:56]!r}{guess}")
    if len(rows) > 15:
        print(f"  …… 其余 {len(rows) - 15} 条见 CSV")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    results = read_rows(args.results)
    connection = open_readonly(args.db)
    try:
        stats = ingest_results(connection, args.candidates, results)
    finally:
        connection.close()
    print(f"合并候选 {stats['applied']} 条，跳过 {stats['skipped']} 条，"
          f"候选组 {stats['groups']} 个 → {args.candidates}")
    print("在 /review 的「资料字段」里逐条核对；批准才写真相字段。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "worklist":
        return cmd_worklist(args)
    return cmd_ingest(args)


if __name__ == "__main__":
    raise SystemExit(main())
