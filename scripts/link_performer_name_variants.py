#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""同一位出演者的另一个艺名：从复核候选里认出来，登记进别名表。

两家来源在同一部片上给出不同的出演者名，多数时候不是谁记错了，是这位在两边挂着
不同艺名。账本的别名表本来就是记这件事的地方，只是这几个写法还没登记：`n0762`
javbus 写 `藤原遼子`、javdb 写 `森沢かな`，后者指向实体 7901（规范名 `森泽佳奈`，
别名里已经有简体的 `藤原辽子`），前者账本完全不认识。没登记的后果是自动落库把这
一行读成「来源有分歧」而扣在人工队列——要判的那个问题账本其实已经答过了。

判据只用账本自己答得出的部分：

1. 同一部片、两家来源，各自给出的人数相同；
2. 除一个名字外其余逐字相同。只有一处不同，那一处才谈得上是「同一个人的另一个
   写法」；整组换掉说明两家说的是两拨人，那是真分歧，不在这里处理；
3. 有且只有一侧解析得到账本实体。两侧都认识说明账本已经当成两个人，那要不要并成
   一条是 `merge_entity` 的事；两侧都不认识就没有锚点，谁当规范名都是猜。

临时艺名不进别名表（用户 2026-09-16 定的口径）。素人企划的商品页给的是这部片给她
起的一次性称呼——`259LUXU-1468` 的 `EMILY`、`390JAC-062` 的 `上野さん 21歳 …`——那
不是她的艺名，记进去等于把一次性称呼变成她的身份。素人企划的番号整行跳过——判据
不只是那三位数字前缀，同一个企划既有 `259LUXU-811` 也有 `LUXU-688`，所以字母段在
账本里出现过带前缀的写法就整段算进去。剪不出艺名边界的写法（`_stage_name`）另外排除。

同一个写法被两条实体同时认领时两边都不写，交人工：别名一旦落库就参与身份解析，
指错人比没指更难发现。

默认只产出复核 CSV，`--apply` 才写 ledger 且必须给 `--backup`。
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.catalog_rules import code_letter_stem, is_amateur_code   # noqa: E402
from peach.config import GENERATED_DIR   # noqa: E402
from peach.entities import normalize_entity_name, resolve_entity   # noqa: E402
from peach.metadata_auto_apply import _split_multi, _stage_name   # noqa: E402
from peach.review_csv import read_candidates, write_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write,
)

ALIAS_SOURCE = "link:performer-name-variant"
FIELDS = ("code", "keep_id", "keep_name", "alias", "alias_source", "keep_source", "evidence")
EXTRA_COUNTS = {
    "performer 实体": "SELECT count(*) FROM entity WHERE kind='performer'",
    "performer 别名": "SELECT count(*) FROM entity_alias ea JOIN entity e ON e.id=ea.entity_id"
                     " WHERE e.kind='performer'",
}


def _names(candidate: dict) -> list[str]:
    """一条候选给出的出演者名。剪不出艺名边界的整条作废——那是企划文案。"""
    names = [_stage_name(name) for name in _split_multi(str(candidate.get("display_value") or ""))]
    return [] if not names or None in names else [name for name in names if name]


def _sources(row: dict) -> dict[str, list[str]]:
    """这一行每家来源各给了哪几个人。同一家有多条候选时只认第一条。"""
    try:
        candidates = json.loads(str(row.get("candidates_json") or "[]"))
    except (TypeError, ValueError):
        return {}
    by_source: dict[str, list[str]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        source = str(candidate.get("source") or "").strip()
        names = _names(candidate)
        if source and names:
            by_source.setdefault(source, names)
    return by_source


def amateur_stems(connection: sqlite3.Connection) -> set[str]:
    """账本里出现过三位前缀的那些字母段：`259LUXU-811` 记下 `luxu`。

    素人企划的番号不总带那三位数字——同一个 `ラグジュTV` 既有 `259LUXU-811` 也有
    `LUXU-688`，`is_amateur_code` 只认得前者。字母段属于企划本身，账本里只要有一条
    带前缀的同段番号，这一段就整段按素人企划对待。宁可漏掉一条真别名，也不把一次性
    称呼写成某个人的身份。
    """
    stems = set()
    for (code,) in connection.execute(
            "SELECT DISTINCT code FROM asset WHERE code IS NOT NULL AND code<>''"):
        if is_amateur_code(code) and (stem := code_letter_stem(code)):
            stems.add(stem)
    return stems


def _claimed_by(connection: sqlite3.Connection, name: str) -> set[int]:
    """账本里有哪几条 performer 实体认领了这个写法。"""
    normalized = normalize_entity_name(name)
    return {int(row[0]) for row in connection.execute(
        "SELECT e.id FROM entity e WHERE e.kind='performer' AND e.normalized_name=?"
        " UNION SELECT e.id FROM entity e JOIN entity_alias a ON a.entity_id=e.id"
        " WHERE e.kind='performer' AND a.normalized_alias=?", (normalized, normalized))}


def _pair(connection: sqlite3.Connection, code: str, left: tuple[str, str],
          right: tuple[str, str]) -> dict | None:
    """两个写法里恰好一个有账本锚点时，另一个就是这条实体的又一个写法。"""
    anchored = [side for side in (left, right) if _claimed_by(connection, side[1])]
    if len(anchored) != 1:
        return None
    (keep_source, keep_name), = anchored
    alias_source, alias = left if anchored[0] is right else right
    entity = resolve_entity(connection, "performer", keep_name)
    if entity is None:                    # 撞名：两条实体都认领 keep_name，指向不唯一
        return None
    return {"code": code, "keep_id": int(entity["id"]),
            "keep_name": str(entity["canonical_name"]), "alias": alias,
            "alias_source": alias_source, "keep_source": keep_source,
            "evidence": f"{code} 上 {keep_source} 与 {alias_source} 给的是同一组人，"
                        f"只有这一个写法不同"}


def collect(connection: sqlite3.Connection, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """（可登记的别名, 被两条实体同时认领而交人工的）。"""
    proposals: dict[tuple[int, str], dict] = {}
    by_alias: dict[str, set[int]] = defaultdict(set)
    amateur = amateur_stems(connection)
    for row in rows:
        if str(row.get("field") or "").strip() != "performers":
            continue
        code = str(row.get("code") or row.get("query") or "").strip()
        if not code or is_amateur_code(code) or code_letter_stem(code) in amateur:
            continue
        by_source = _sources(row)
        for left, right in itertools.combinations(sorted(by_source), 2):
            here, there = by_source[left], by_source[right]
            if len(here) != len(there):
                continue
            only_here = [name for name in here if name not in there]
            only_there = [name for name in there if name not in here]
            if len(only_here) != 1 or len(only_there) != 1:
                continue
            found = _pair(connection, code, (left, only_here[0]), (right, only_there[0]))
            if found is None:
                continue
            by_alias[normalize_entity_name(found["alias"])].add(found["keep_id"])
            proposals.setdefault((found["keep_id"], normalize_entity_name(found["alias"])), found)
    contested = [row for key, row in proposals.items() if len(by_alias[key[1]]) > 1]
    keep = [row for key, row in proposals.items() if len(by_alias[key[1]]) == 1]
    return sorted(keep, key=lambda row: (row["keep_name"], row["alias"])), contested


def apply_rows(connection: sqlite3.Connection, rows: list[dict]) -> int:
    written = 0
    for row in rows:
        connection.execute(
            "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(?,?,?,?,1.0)",
            (row["keep_id"], row["alias"], normalize_entity_name(str(row["alias"])), ALIAS_SOURCE))
        written += int(connection.execute("SELECT changes()").fetchone()[0])
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把复核候选里同一位出演者的另一个艺名登记成别名")
    add_ledger_write_args(parser)
    parser.add_argument("--review-csv", type=Path,
                        default=GENERATED_DIR / "performer-name-variant-link.csv")
    parser.add_argument("--candidate-root", type=Path,
                        help="复核候选目录；缺省用本机默认位置")
    return parser


def run(args: argparse.Namespace) -> int:
    connection = open_for_write(args)
    try:
        candidates = read_candidates("metadata_fields", args.candidate_root)[0]
        rows, contested = collect(connection, candidates)
        if rows:
            write_rows(args.review_csv, FIELDS, rows)
        print(f"可登记的别名 {len(rows)} 条，复核 CSV：{args.review_csv}")
        for row in rows:
            print(f"    {row['alias']}（{row['alias_source']}）"
                  f" -> 实体 {row['keep_id']} {row['keep_name']}（{row['keep_source']}）"
                  f"  {row['evidence']}")
        for row in contested:
            print(f"  交人工：{row['alias']} 被多条实体认领，一条都不写")
        if not args.apply:
            print("  未写 ledger（加 --apply --backup 才写）")
            return 0

        print(f"  已备份到 {args.backup}")
        before = counts_of(connection, EXTRA_COUNTS)
        with connection:
            written = apply_rows(connection, rows)
        after = counts_of(connection, EXTRA_COUNTS)
        _integrity, violations = verify_after_write(connection)
        print(f"  新登记别名 {written} 条")
        for key in before:
            mark = "" if before[key] == after[key] else "  <-- 变化"
            print(f"    {key}: {before[key]} -> {after[key]}{mark}")
        print(f"  foreign_key_check 违规 {violations} 条")
        return 1 if violations else 0
    finally:
        connection.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
