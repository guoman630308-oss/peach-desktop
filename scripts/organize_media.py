#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按模板整理文件名与目录（ADR-0039）。

和数据管理页上那一块是同一份实现（`peach.organize`）：模板解析、计划生成、执行与回滚
只有一处，命令行和界面看到的结果因此必然一致。

默认只出计划 CSV。`--apply` 走 `peach.scripting` 的标准门槛：必须同时给 `--backup`，
备份用 SQLite 备份 API 落盘，写完跑 `integrity_check` 与 `foreign_key_check`。

用法:
    python scripts/organize_media.py --location 115 --file-template '{number}[ {title}]'
    python scripts/organize_media.py --location 115 --file-template '…' --apply --backup <落点>
    python scripts/organize_media.py --rollback-last --apply --backup <落点>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach import organize
from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.media_sources import SCANNABLE_LOCATIONS
from peach.organize_templates import PLACEHOLDERS, TemplateError
from peach.scripting import add_ledger_write_args, open_for_write, verify_after_write


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按模板整理文件名与目录；默认只出计划",
        epilog="占位符：" + "、".join(f"{{{key}}}={label}" for key, label in PLACEHOLDERS.items()))
    add_ledger_write_args(parser, db_default=DATABASE_PATH)
    parser.add_argument("--location", choices=SCANNABLE_LOCATIONS, default="local")
    parser.add_argument("--file-template", default="", help="文件名模板，不含扩展名")
    parser.add_argument("--dir-template", default="",
                        help="目录模板，相对来源根；留空表示原地改名")
    parser.add_argument("--generated-root", type=Path, default=GENERATED_DIR,
                        help="计划 CSV 与批次日志的落点")
    parser.add_argument("--rollback-last", action="store_true",
                        help="把最近一批整理原样退回去")
    return parser


def _report_plan(plan: dict) -> None:
    counts = plan["counts"]
    print(f"扫描 {counts['total']} 条：会改 {counts['change']}，"
          f"已经就是目标名字 {counts['unchanged']}，跳过 {counts['skip']}")
    for reason, count in sorted(plan["reasons"].items(), key=lambda item: -item[1]):
        print(f"  {reason}：{count} 条")


def run(args: argparse.Namespace) -> int:
    connection = open_for_write(args)
    try:
        if args.rollback_last:
            latest = organize.latest_batch(args.generated_root)
            if latest is None:
                print("没有可以回滚的批次")
                return 0
            print(f"上一批：{latest}")
            if not args.apply:
                print("未加 --apply，什么都没退回。")
                return 0
            result = organize.rollback_batch(connection, latest)
            print(f"退回 {result['restored']} 条，失败 {result['failed']}")
            for failure in result["failures"]:
                print(f"  {failure['path']}：{failure['reason']}")
        else:
            plan = organize.build_plan(
                connection, location=args.location,
                file_template=args.file_template, dir_template=args.dir_template)
            path = organize.plan_path(args.generated_root, args.location)
            organize.write_plan(path, plan["rows"])
            _report_plan(plan)
            print(f"→ {path}")
            if not args.apply:
                print("未加 --apply，只出计划未动文件。")
                return 0
            result = organize.apply_plan(connection, plan["rows"],
                                         generated_root=args.generated_root)
            print(f"整理 {result['moved']} 条，失败 {result['failed']}")
            for failure in result["failures"]:
                print(f"  {failure['path']}：{failure['reason']}")
            print(f"批次日志：{result['log_path']}")
        integrity, violations = verify_after_write(connection)
        if integrity != "ok" or violations:
            print(f"写入后 ledger 校验失败：integrity={integrity} foreign_keys={violations}")
            return 2
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (TemplateError, organize.OrganizeError) as error:
        print(f"[stop] {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
