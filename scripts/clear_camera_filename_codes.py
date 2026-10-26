#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""清掉账本里由相机文件名派生出来的伪番号（`VIDEO-2022`、`IMG-1734` 这类）。

`video_2022-06-08_11-40-56.mp4`、`IMG_1734 (2).mp4` 是手机与相机的默认命名，
早期的文件名解析把它们认成了番号。现在 `release_code_from_filename` 已经把
`IMG`／`VID`／`VIDEO` 列为停用词，这些值只剩在旧行上：采集任务把它们当番号去
问 r18.dev，页面上按番号分组也把几十个不相干的视频拢到一起。

只动同时满足三条的行：`medium='video'`、番号形如 `IMG-数字`／`VID-数字`／`VIDEO-数字`、
且今天的解析器对同一个文件名给不出番号。番号归属是用户或复核写下的一律不碰。
写入走 `write_owned_fields`，归属记为 `script:clear-camera-filename-codes`。

默认 dry-run。`--apply` 必须同时给 `--backup`，与本仓库其它真实写入脚本一致。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.catalog_rules import release_code_from_filename  # noqa: E402
from peach.field_owners import is_protected, owner_of, script_owner, write_owned_fields  # noqa: E402
from peach.scripting import add_ledger_write_args, counts_of, open_for_write, verify_after_write  # noqa: E402

OWNER = script_owner("clear-camera-filename-codes")
CAMERA_CODE = re.compile(r"^(IMG|VID|VIDEO)-\d+$")


def bogus_rows(connection: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """(要清的行, 因归属受保护而跳过的行)。"""
    clear, protected = [], []
    query = ("SELECT id, name, code, field_owners FROM asset "
             "WHERE medium='video' AND code IS NOT NULL AND code<>'' ORDER BY id")
    for row in connection.execute(query):
        code = str(row["code"])
        if not CAMERA_CODE.match(code) or release_code_from_filename(str(row["name"])) is not None:
            continue
        item = {"id": int(row["id"]), "name": row["name"], "code": code,
                "owner": owner_of(row["field_owners"], "code")}
        (protected if is_protected(item["owner"]) else clear).append(item)
    return clear, protected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_ledger_write_args(parser)
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    connection = open_for_write(args)
    extra = {"camera_codes": "SELECT count(*) FROM asset WHERE medium='video' AND "
                             "(code GLOB 'IMG-[0-9]*' OR code GLOB 'VID-[0-9]*' OR code GLOB 'VIDEO-[0-9]*')"}
    before = counts_of(connection, extra)
    clear, protected = bogus_rows(connection)
    report = {"db": str(Path(args.db).resolve()), "applied": bool(args.apply),
              "before": before, "clear": clear, "protected": protected}
    if args.apply and clear:
        with connection:
            result = write_owned_fields(connection, [item["id"] for item in clear], {"code": None}, OWNER)
        report["written_fields"] = list(result.written)
        report["after"] = counts_of(connection, extra)
        report["integrity"], report["foreign_key_violations"] = verify_after_write(connection)
    connection.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        verb = "已清除" if args.apply else "将清除"
        for item in clear:
            print(f"{verb}  #{item['id']}  {item['code']:<12} {item['name']}")
        for item in protected:
            print(f"保留    #{item['id']}  {item['code']:<12} {item['name']}（归属 {item['owner']}）")
        print(f"{verb} {len(clear)} 行，保留 {len(protected)} 行；"
              f"相机式番号 {before['camera_codes']} → {report.get('after', before)['camera_codes']}")
        if "integrity" in report:
            print(f"integrity_check={report['integrity']} foreign_key_violations={report['foreign_key_violations']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
