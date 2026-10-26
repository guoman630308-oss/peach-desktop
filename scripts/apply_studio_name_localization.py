#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `localize_studio_names.py` 复核件里判「改名」的厂牌换回日文原名。

复核件只说「javbus 的製作商写的是 `セレブの友`」；真正改名要同时动四处，缺一处都是
各说各话：

- `entity.canonical_name` 与 `normalized_name`；
- 旧的罗马音降为别名。它是番号站真用过的写法，按它还得查得到这条实体；
- 扁平 `asset.studio`（ADR-0005：兼容投影跟着规范关系走）。只改实体不改投影的话，
  下一次刮削会照着投影里的旧名把实体再建一遍；
- `--logo-root` 下按 `logo_key` 落盘的标识文件。名字一换，旧名下的图就没人认领。

复核件是 2026-09-02 出的，账本此后合并过厂牌，所以逐行核对：实体还在、现名还是
复核件里那个名字，才改。日文名已经是另一条厂牌的规范名或别名时只报冲突不合并——
那要么是两条该合并，要么是同名的两家，都得人来判。

默认只写审计 CSV；`--apply` 才写 ledger，且必须同时给 `--backup`。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR   # noqa: E402
from peach.entities import normalize_entity_name, rewrite_flat_projection   # noqa: E402
from peach.previews import relink_logo_files   # noqa: E402
from peach.review_csv import read_rows, write_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write,
)

#: 复核件的判词，与 `localize_studio_names.RENAME` 同一个字面值。
RENAME_VERDICT = "改名"
ALIAS_SOURCE = "javbus:studio-localization"
FIELDS = ("entity_id", "current_name", "target_name", "action", "reason")

RENAME, SKIP = "rename", "skip"


def plan(connection: sqlite3.Connection, review_rows: list[dict]) -> list[dict]:
    """逐行核对复核件与账本现状，给每条判「改」或「跳过」并写明原因。"""
    rows = []
    for review in review_rows:
        if str(review.get("verdict") or "").strip() != RENAME_VERDICT:
            continue
        entity_id = int(review["entity_id"])
        listed = str(review.get("studio") or "").strip()
        target = str(review.get("proposed") or "").strip()
        row = {"entity_id": entity_id, "current_name": listed, "target_name": target,
               "action": SKIP, "reason": ""}
        rows.append(row)
        found = connection.execute(
            "SELECT kind,canonical_name FROM entity WHERE id=?", (entity_id,)).fetchone()
        if not target:
            row["reason"] = "复核件没有建议名"
        elif found is None or found[0] != "studio":
            row["reason"] = "实体已不在（多半已被合并）"
        elif found[1] != listed:
            row["reason"] = f"现名已变为 {found[1]}"
        else:
            row["reason"] = _clash(connection, entity_id, target)
            if not row["reason"]:
                row["action"] = RENAME
                row["reason"] = str(review.get("evidence") or "").strip() or "javbus 製作商为日文原名"
    return rows


def _clash(connection: sqlite3.Connection, entity_id: int, target: str) -> str:
    key = normalize_entity_name(target)
    taken = connection.execute(
        "SELECT id FROM entity WHERE kind='studio' AND normalized_name=? AND id<>?",
        (key, entity_id)).fetchone()
    if taken:
        return f"日文名已是厂牌 #{taken[0]} 的规范名，需人工判断是否合并"
    alias = connection.execute(
        "SELECT a.entity_id FROM entity_alias a JOIN entity e ON e.id=a.entity_id"
        " WHERE e.kind='studio' AND a.normalized_alias=? AND a.entity_id<>?",
        (key, entity_id)).fetchone()
    if alias:
        return f"日文名已是厂牌 #{alias[0]} 的别名，需人工判断是否合并"
    return ""


def apply_rows(connection: sqlite3.Connection, rows: list[dict], *,
               alias_source: str = ALIAS_SOURCE) -> Counter:
    counts: Counter = Counter()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in rows:
        if row["action"] != RENAME:
            continue
        entity_id, old, new = int(row["entity_id"]), row["current_name"], row["target_name"]
        connection.execute(
            "UPDATE entity SET canonical_name=?,normalized_name=?,updated_at=? WHERE id=?",
            (new, normalize_entity_name(new), stamp, entity_id))
        counts["renamed"] += connection.execute("SELECT changes()").fetchone()[0]
        connection.execute(
            "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(?,?,?,?,1.0)",
            (entity_id, old, normalize_entity_name(old), alias_source))
        counts["aliases"] += connection.execute("SELECT changes()").fetchone()[0]
        counts["flat_rewritten"] += rewrite_flat_projection(
            connection, "studio", entity_id, old, new)
    return counts


EXTRA_COUNTS = {
    "studio_entities": "SELECT count(*) FROM entity WHERE kind='studio'",
    "studio_aliases": "SELECT count(*) FROM entity_alias a JOIN entity e"
                      " ON e.id=a.entity_id WHERE e.kind='studio'",
    "latin_studios": "SELECT count(*) FROM entity WHERE kind='studio'"
                     " AND canonical_name NOT GLOB '*[^ -~]*'",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把复核件判「改名」的厂牌换回日文原名")
    add_ledger_write_args(parser)
    parser.add_argument("--plan", type=Path, required=True,
                        help="localize_studio_names.py 产出的复核 CSV")
    parser.add_argument("--audit-csv", type=Path,
                        default=GENERATED_DIR / "studio-name-localization-apply.csv")
    parser.add_argument("--logo-root", type=Path,
                        help="随 --apply 一起把旧名下的标识文件挪到新名下")
    # 复核件不是 javbus 那一轮出的时候（官方名录、人工复核），旧名降为别名要记真实来源。
    parser.add_argument("--alias-source", default=ALIAS_SOURCE,
                        help="旧名降为别名时记的来源")
    return parser


def run(args: argparse.Namespace) -> int:
    connection = open_for_write(args)
    try:
        rows = plan(connection, read_rows(args.plan))
        write_rows(args.audit_csv, FIELDS, rows)
        print(f"复核件判改名 {len(rows)} 行，审计 CSV：{args.audit_csv}")
        print("  动作分布：", dict(Counter(row["action"] for row in rows)))
        for row in rows:
            print(f"    [{row['action']}] {row['current_name']} -> {row['target_name']}"
                  f"  {row['reason']}")
        if not args.apply:
            print("  未写 ledger（加 --apply --backup 才写）")
            return 0

        print(f"  已备份到 {args.backup}")
        before = counts_of(connection, EXTRA_COUNTS)
        with connection:
            changed = apply_rows(connection, rows, alias_source=args.alias_source)
        after = counts_of(connection, EXTRA_COUNTS)
        integrity, violations = verify_after_write(connection)
        print("  写入结果：", dict(changed))
        for key in before:
            print(f"    {key}: {before[key]} -> {after[key]}")
        print(f"  integrity_check={integrity}；foreign_key_check={violations}")
        if args.logo_root:
            moved, left = [], []
            for row in rows:
                if row["action"] == RENAME:
                    done, stayed = relink_logo_files(
                        row["current_name"], row["target_name"], args.logo_root)
                    moved += done
                    left += stayed
            print(f"  标识改挂 {len(moved)} 个文件（新名下已有、留在原地的 {len(left)} 个）")
            for line in [*moved, *(f"留在原地：{name}" for name in left)]:
                print(f"    {line}")
        return 1 if integrity != "ok" or violations else 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
