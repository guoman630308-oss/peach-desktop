#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把字段候选 CSV 里的标签直接写进 ledger，不经 `/review` 排队。

这条路是用户当轮明确授权的批量写入，不是默认流程。默认流程仍然是 `/review`：
候选带出处和置信度，由人一条条批。这里只是把同一份写入映射批量跑一遍。

写入映射复用 `peach.metadata_auto_apply._apply_metadata_candidate`——就是 `/review` 批准时
走的那一份。它管着删旧的 `javinizer:%` 标签行、规范化标签名、按现词表换掉退役名与撤掉的粗桶，
以及 `asset_tag` 与 `asset_entity` 两处一起写。抄一份出来只会漂。

默认只统计，`--apply` 才写库且必须给 `--backup`。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# 仓库既有约定（job_status.py 等 8 个脚本同样写法）：脚本直接跑时把 src 挂上，
# 免得用户必须先设 PYTHONPATH。2026-09-02 就是漏了这段，交出去的命令直接
# ModuleNotFoundError。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH
from peach.field_owners import script_owner
from peach.metadata_auto_apply import _apply_metadata_candidate
from peach.migrations import sqlite_backup
from peach.review_csv import read_rows

#: 这一批写入在账本里的署名。用户当轮授权的批量写入仍然是脚本写的，记成
#: `review:<来源>` 会让日后回溯以为有人在 `/review` 上逐条点过。
OWNER = script_owner("apply_metadata_tags")


def select_candidate(row: dict, source: str) -> dict | None:
    """取这一组里指定来源的候选；没有就返回 None。"""
    try:
        candidates = json.loads(str(row.get("candidates_json") or "[]"))
    except (TypeError, ValueError):
        return None
    for candidate in candidates:
        if isinstance(candidate, dict) and str(candidate.get("source") or "") == source:
            return candidate
    return None


def plan(rows: list[dict], source: str, field: str,
         skip_codes: frozenset[str] = frozenset()) -> list[tuple[dict, dict]]:
    """选出要写的组。`skip_codes` 里的番号原样留在 CSV 里，只是不写库。

    批量放行总有几条明显不对的，比如 javbus 在 `MY-*` 系列的标题栏放的是
    「演员名+序号」。这种既不该混进批量写入，也不该从复核产物里删掉——
    删了就没人再看见它。所以是跳过，不是过滤 CSV。
    """
    skip = {code.strip().upper() for code in skip_codes if code.strip()}
    selected: list[tuple[dict, dict]] = []
    for row in rows:
        if str(row.get("field") or "").strip() != field:
            continue
        if str(row.get("status") or "").strip() != "candidate":
            continue
        if str(row.get("code") or "").strip().upper() in skip:
            continue
        candidate = select_candidate(row, source)
        if candidate is not None:
            selected.append((row, candidate))
    return selected


def _record_decision(connection, group: dict, candidate: dict, note: str, now: str) -> None:
    """把写入登记成 approved，留痕形状与 `/review` 手工批准完全一致。

    不登记的后果实测过：2026-09-02 那 119 组标签写完仍原样挂在 `/review` 里，
    看不出已经处理过。留痕里必须带 `candidate_key`——`metadata_decision_is_stale`
    正是靠它判断「这条旧批准指向的候选还在不在」，缺了它以后新来源就压不进来。
    """
    connection.execute(
        "INSERT INTO review_decision(category,item_key,status,note,updated_at) "
        "VALUES('metadata_fields',?,'approved',?,?) "
        "ON CONFLICT(category,item_key) DO UPDATE SET status=excluded.status,"
        "note=excluded.note,updated_at=excluded.updated_at",
        (str(group.get("item_key") or ""), json.dumps({
            "candidate_key": candidate.get("candidate_key"),
            "source": candidate.get("source"),
            "user_note": note,
        }, ensure_ascii=False, separators=(",", ":")), now),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把字段候选里的标签直接写进 ledger")
    parser.add_argument("candidates", type=Path, help="scrape_codes 产出的字段候选 CSV")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--source", default="javbus", help="只写这个来源的候选值")
    parser.add_argument("--field", default="tags", help="只写这个字段")
    parser.add_argument("--apply", action="store_true", help="写库；默认只统计")
    parser.add_argument("--backup", type=Path, help="--apply 必需：写库前的 SQLite 备份路径")
    parser.add_argument("--skip-codes", default="",
                        help="逗号分隔的番号，留在 CSV 里但不写库")
    parser.add_argument("--note", default="批量直接写入（用户当轮授权）",
                        help="写进 review_decision 留痕的说明")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.apply and not args.backup:
        raise SystemExit("--apply 必须同时给 --backup")
    rows = list(read_rows(args.candidates))
    skip = frozenset(str(args.skip_codes or "").split(","))
    selected = plan(rows, args.source, args.field, skip)
    print(f"候选 {len(rows)} 组，来源 {args.source} 的 {args.field} 候选 {len(selected)} 组"
          + (f"，跳过番号 {args.skip_codes}" if args.skip_codes else ""))
    if not selected:
        return 0
    if not args.apply:
        print("  未写库（加 --apply --backup 才执行）")
        return 0

    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        before = connection.execute(
            "SELECT count(*) FROM asset_tag WHERE source LIKE 'javinizer:%:tag'"
        ).fetchone()[0]
        connection.close()
        sqlite_backup(args.db, args.backup)
        print(f"  已备份到 {args.backup}")
        connection = sqlite3.connect(args.db)
        connection.row_factory = sqlite3.Row

        now = datetime.now(timezone.utc).isoformat()
        applied = assets = 0
        failures: list[str] = []
        with connection:
            for group, candidate in selected:
                try:
                    assets += _apply_metadata_candidate(
                        connection, group, candidate, now, OWNER)
                except ValueError as error:
                    # 逐组失败不该拖垮整批：番号已清库、候选值不规范都是常态。
                    failures.append(f"{group.get('item_key')}: {error}")
                    continue
                applied += 1
                _record_decision(connection, group, candidate, args.note, now)
        after = connection.execute(
            "SELECT count(*) FROM asset_tag WHERE source LIKE 'javinizer:%:tag'"
        ).fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tag_delta = (f"javinizer 标签行 {before} -> {after}；"
                     if args.field == "tags" else "")
        print(f"  写入 {applied} 组、覆盖资产 {assets} 条；"
              f"{tag_delta}integrity_check {integrity}")
        if failures:
            print(f"  跳过 {len(failures)} 组：")
            for line in failures[:10]:
                print(f"    {line}")
        return 0 if integrity == "ok" else 1
    finally:
        connection.close()


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
