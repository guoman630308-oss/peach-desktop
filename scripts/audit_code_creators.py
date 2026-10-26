#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""番号目录被投影成创作者的清理器。

判定与清理在 `peach.code_creators`，复核页按同一套判据现算队列。本脚本是写这一步的
唯一入口：有文件级证据的那批由 `--apply` 带备份清掉，另外产出可复核、可重放的 CSV。

默认只产出复核 CSV，`--apply` 才写 ledger 且必须给 `--backup`。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.code_creators import FIELDS, apply_rows, collect
from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.migrations import sqlite_backup
from peach.review_csv import write_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="清理被投影成创作者的番号目录名")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--review-csv", type=Path,
                        default=GENERATED_DIR / "code-creator-review.csv")
    parser.add_argument("--apply", action="store_true", help="写 ledger；默认只出 CSV")
    parser.add_argument("--backup", type=Path, help="--apply 必需：写库前的 SQLite 备份路径")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.apply and not args.backup:
        raise SystemExit("--apply 必须同时给 --backup")
    connection = sqlite3.connect(args.db)
    rows = collect(connection)
    print(f"命中番号形态的创作者 {len(rows)} 个")
    print("  判定分布：", dict(Counter(str(row["verdict"]) for row in rows)))

    if args.apply:
        sqlite_backup(args.db, args.backup)
        print(f"已备份 → {args.backup}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            counts = apply_rows(connection, rows)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        print(f"已写入：删创作者关系 {counts['links']} 条、删实体 {counts['entities']} 个、"
              f"补 code {counts['codes']} 条、清扁平字段 {counts['flat']} 条")
    connection.close()

    write_rows(args.review_csv, FIELDS, rows)
    print(f"复核 CSV → {args.review_csv}")
    if not args.apply:
        print("这是预览：未写 ledger。确认后再加 --apply --backup。")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
