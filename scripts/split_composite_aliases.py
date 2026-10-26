#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""把一格装了多个艺名的别名拆成一人多名。

r18.dev 的罗马字字段本身就是 `现用名 (曾用名, 曾用名)` 这个渲染格式，导入时一个字段
写一行，于是 `Ako Momona (Kou Akemi, Mari Koizumi)` 整串成了一条别名。它做别名是死的：
按任何一段都搜不到，选成统称更不成立。同一条实体的假名和汉字写法本来就各自成行，缺的
只是这几个罗马字，所以拆开是补信息，不是丢信息。

**只拆罗马字人名这一种形状**（判据见 `peach.entities.COMPOSITE_PERSON_NAME`）。账本里
「名字后面跟一对括号」的还有四类完全不同的东西，一律不动，只写进复核 CSV 交人工：

- `AV DEBUT（本物人妻）`：括号里是厂牌，用来把三个同名系列分开。拆了就撞成一个。
- `アスナ(SAO)`：角色标签的出处消歧，账本里有五百多条。
- `快慢扳机（接稿中）`、`るかぴあ（X：復帰しました）`：括号里是接稿状态或公告。
- `kitty(1)`、`Mana(23)`：去重后缀或数字。

形状分不开的那些交人工：在复核 CSV 里把 `verdict` 改成 `split`（别名拆成几条）或
`strip`（规范名换成 `target`，旧名留作别名），填好 `target`，再用 `--from-review` 跑。

拆分不可逆。默认只产出复核 CSV，`--apply` 才写 ledger 且必须给 `--backup`。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR   # noqa: E402
from peach.entities import (   # noqa: E402
    normalize_entity_name, rewrite_flat_projection, split_composite_person_name,
)
from peach.review_csv import read_rows, write_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write,
)

#: 只有人名实体才可能出现「一个人的多个艺名」。系列、厂牌和标签的括号是别的意思。
PERSON_KINDS = ("performer", "creator")

FIELDS = ("verdict", "kind", "entity_id", "canonical_name", "field",
          "value", "source", "parts", "target", "note")

#: 人工判定改写规范名时，换下来的旧名记在这个来源下。它不是哪个站给的，是用户定的。
CLEANUP_SOURCE = "user:name-cleanup"

EXTRA_COUNTS = {
    "composite_alias": (
        "SELECT count(*) FROM entity_alias WHERE alias LIKE '%(%' OR alias LIKE '%（%'"),
}


def _collision(connection: sqlite3.Connection, entity_id: int, part: str) -> str:
    """这一段是不是别的实体的规范名。是的话整行不拆，交人工。

    拆出来的名字撞上另一条实体的规范名，意味着两条实体可能是同一个人——那是合并要
    回答的问题，不是拆别名顺手能定的。悄悄插进去只会让两边都持有对方的名字。
    """
    row = connection.execute(
        "SELECT id, kind, canonical_name FROM entity "
        "WHERE normalized_name=? AND id<>?",
        (normalize_entity_name(part), entity_id)).fetchone()
    return "" if row is None else f"撞 entity {row[0]}（{row[1]}）{row[2]}"


def collect(connection: sqlite3.Connection) -> list[dict[str, object]]:
    """账本里所有带括号的实体名与别名，标好判定。"""
    rows: list[dict[str, object]] = []
    scans = (
        ("alias", "SELECT a.entity_id, e.kind, e.canonical_name, a.alias, a.source "
                  "FROM entity_alias a JOIN entity e ON e.id=a.entity_id "
                  "WHERE a.alias LIKE '%(%' OR a.alias LIKE '%（%'"),
        ("canonical", "SELECT id, kind, canonical_name, canonical_name, '' FROM entity "
                      "WHERE canonical_name LIKE '%(%' OR canonical_name LIKE '%（%'"),
    )
    for field, sql in scans:
        for entity_id, kind, canonical, value, source in connection.execute(sql):
            parts = split_composite_person_name(value)
            note, verdict = "", "review"
            # 判据是「签名匹配上了」，不是「拆出几段」。`Rei Mizuna (Rei Mizuna)` 去重后
            # 只剩一段，但它照样是那个打包字段，照样得换成干净的一行。
            if parts == [value.strip()]:
                note = "不是罗马字复合人名"
            elif kind not in PERSON_KINDS:
                note = f"{kind} 的括号不是艺名"
            elif field == "canonical":
                note = "规范名要人工定留哪一个"
            else:
                collisions = [c for c in (_collision(connection, int(entity_id), part)
                                          for part in parts) if c]
                if collisions:
                    note = "；".join(collisions)
                else:
                    verdict = "auto"
            rows.append({
                "verdict": verdict, "kind": kind, "entity_id": int(entity_id),
                "canonical_name": canonical, "field": field, "value": value,
                "source": source or "", "parts": " | ".join(parts),
                "target": " | ".join(parts) if verdict == "auto" else "", "note": note,
            })
    rows.sort(key=lambda row: (row["verdict"] != "auto", str(row["kind"]),
                               int(row["entity_id"])))
    return rows


def apply_rows(connection: sqlite3.Connection,
               rows: list[dict[str, object]]) -> dict[str, int]:
    """把 `auto` 行拆开：先补上各段，再删掉那条复合别名。

    每一段都记在提供它的那个来源名下。它们确实是它给的，只是被打包在一个字段里；改写成
    「本脚本」会让下一个人查不到这几个罗马字是谁提供的。
    """
    added = removed = 0
    for row in rows:
        if row["verdict"] != "auto":
            continue
        entity_id, source = int(row["entity_id"]), str(row["source"])
        for part in str(row["parts"]).split(" | "):
            connection.execute(
                "INSERT OR IGNORE INTO entity_alias"
                "(entity_id,alias,normalized_alias,source) VALUES(?,?,?,?)",
                (entity_id, part, normalize_entity_name(part), source))
            added += connection.execute("SELECT changes()").fetchone()[0]
        connection.execute(
            "DELETE FROM entity_alias WHERE entity_id=? AND alias=? AND source=?",
            (entity_id, row["value"], source))
        removed += connection.execute("SELECT changes()").fetchone()[0]
    return {"added": added, "removed": removed}


def _blocked(connection: sqlite3.Connection, entity_id: int,
             names: list[str]) -> str:
    """人工判定也过一遍碰撞：撞上另一条实体的规范名要走合并，不是改个名了事。"""
    return "；".join(hit for hit in (_collision(connection, entity_id, name)
                                     for name in names) if hit)


def apply_review(connection: sqlite3.Connection,
                 rows: list[dict[str, str]]) -> dict[str, object]:
    """按人工填好的 `verdict` 与 `target` 改名字。

    `split`：把一条别名换成 `target` 里那几条，来源不变。
    `strip`：把规范名换成 `target` 的第一段，旧名留作别名，扁平投影跟着改。
    """
    done = {"split": 0, "strip": 0, "flat": 0, "blocked": []}
    for row in rows:
        verdict = str(row.get("verdict", "")).strip()
        targets = [part.strip() for part in str(row.get("target", "")).split("|")
                   if part.strip()]
        if verdict not in {"split", "strip"} or not targets:
            continue
        entity_id, value = int(row["entity_id"]), str(row["value"])
        blocked = _blocked(connection, entity_id, targets)
        if blocked:
            done["blocked"].append(f"{value}：{blocked}")
            continue
        if verdict == "split":
            source = str(row.get("source", ""))
            for target in targets:
                connection.execute(
                    "INSERT OR IGNORE INTO entity_alias"
                    "(entity_id,alias,normalized_alias,source) VALUES(?,?,?,?)",
                    (entity_id, target, normalize_entity_name(target), source))
            connection.execute(
                "DELETE FROM entity_alias WHERE entity_id=? AND alias=? AND source=?",
                (entity_id, value, source))
            done["split"] += 1
            continue
        target = targets[0]
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        connection.execute(
            "UPDATE entity SET canonical_name=?,normalized_name=?,updated_at=? WHERE id=?",
            (target, normalize_entity_name(target), stamp, entity_id))
        # 旧名留作别名：它是这条实体真的用过的写法，也是回退的入口。
        connection.execute(
            "INSERT OR IGNORE INTO entity_alias"
            "(entity_id,alias,normalized_alias,source,confidence) VALUES(?,?,?,?,1.0)",
            (entity_id, value, normalize_entity_name(value), CLEANUP_SOURCE))
        done["flat"] += rewrite_flat_projection(
            connection, str(row["kind"]), entity_id, value, target)
        done["strip"] += 1
    return done


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_ledger_write_args(parser)
    parser.add_argument("--review-csv", type=Path,
                        default=GENERATED_DIR / "review" / "composite-names.csv",
                        help="复核 CSV 输出路径")
    parser.add_argument("--from-review", type=Path,
                        help="按这份人工判定过的 CSV 执行 split / strip，不再重新扫描")
    return parser


def _report(before: dict[str, int], after: dict[str, int],
            connection: sqlite3.Connection) -> int:
    _integrity, violations = verify_after_write(connection)
    for key in before:
        mark = "" if before[key] == after[key] else "  <-- 变化"
        print(f"    {key}: {before[key]} -> {after[key]}{mark}")
    print(f"  foreign_key_check 违规 {violations} 条")
    return 1 if violations else 0


def run_review(args: argparse.Namespace) -> int:
    connection = open_for_write(args)
    try:
        rows = read_rows(args.from_review)
        acting = [row for row in rows
                  if str(row.get("verdict", "")).strip() in {"split", "strip"}]
        print(f"人工判定 {len(acting)} 行：{args.from_review}")
        for row in acting:
            print(f"    {row['verdict']} [{row['entity_id']}] {row['value']}"
                  f" -> {row['target']}")
        if not args.apply:
            print("  未写 ledger（加 --apply --backup 才写）")
            return 0
        print(f"  已备份到 {args.backup}")
        before = counts_of(connection, EXTRA_COUNTS)
        with connection:
            done = apply_review(connection, acting)
        after = counts_of(connection, EXTRA_COUNTS)
        print("  结果：", done)
        return _report(before, after, connection)
    finally:
        connection.close()


def run(args: argparse.Namespace) -> int:
    if args.from_review:
        return run_review(args)
    connection = open_for_write(args)
    try:
        rows = collect(connection)
        write_rows(args.review_csv, FIELDS, rows)
        verdicts = Counter(str(row["verdict"]) for row in rows)
        print(f"带括号的名字 {len(rows)} 条，复核 CSV：{args.review_csv}")
        print("  判定分布：", dict(verdicts))
        for row in rows:
            if row["verdict"] == "auto":
                print(f"    拆 [{row['entity_id']}] {row['canonical_name']}："
                      f"{row['value']} -> {row['parts']}")
        by_kind = Counter(f"{row['kind']}/{row['field']}" for row in rows
                          if row["verdict"] != "auto")
        print("  交人工：", dict(by_kind))
        if not args.apply:
            print("  未写 ledger（加 --apply --backup 才写）")
            return 0

        print(f"  已备份到 {args.backup}")
        before = counts_of(connection, EXTRA_COUNTS)
        with connection:
            moved = apply_rows(connection, rows)
        after = counts_of(connection, EXTRA_COUNTS)
        print("  拆分结果：", moved)
        return _report(before, after, connection)
    finally:
        connection.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
