#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""同一家厂牌被记成两条实体的去重：写法变体、日文名／罗马字名与并写名三类。

`entity` 的唯一约束是 `(kind, normalized_name)`，所以 `AVS collector's` 与
`AVS collector’s` 可以并存，`Prestige`（425 部）与 `プレステージ`（9 部）也可以。
后果不是报错而是每一处都少一半：资产数被劈开，标识各装一份，筛选和资料页各算各的。

三类判据都不靠转写。罗马音回日文没有唯一解，反过来也一样，所以这里一条都不推：

**写法变体**（class A）——NFKC 之后把弯撇号、星号与全角符号折成同一个形，两个名字
逐字相等才算一对。`シロウトTV` 与 `ラグジュTV` 不会撞上，因为这个折叠一个假名都不丢；
只留 ASCII 的折法会把它们双双折成 `tv`，那是错的。

**日文名／罗马字名**（class B）——用番号前缀作证据。前缀属于厂牌，两条实体共用同一个
前缀就是同一家：`ムーディーズ` 的 MIDA／MIDV／MIRD 三个全部也出现在 `MOODYZ` 名下。
唯一命中才当候选，撞上两家以上交人工。

**并写名**（class C）——来源把两种写法并在一串里：`プレステージプレミアム(PRESTIGE
PREMIUM)`。整串匹配不到已有实体，落库那一刻就自己立了一条；括号两边各自却都是
`Prestige` 名下登记过的写法。证据是账本自己的别名表。素人系的番号前缀以数字开头
（`300MIUM`），class B 在这里一个前缀都取不到，所以它接不住这一类。

保留哪一边：写法变体保留纯 ASCII 的那一个（用户 2026-09-04 定的口径「统一为英文、
罗马音」），两边都是或都不是 ASCII 时保留作品多的一侧；日文名／罗马字名看日文侧：
含汉字或平假名的保留日文侧（`セレブの友` 顶掉 `Celeb no Tomo`：罗马音是番号站的转写，
不算英文，用户 2026-09-23 定的口径），纯片假名的保留罗马字侧（`MOODYZ` 不让给
`ムーディーズ`，那是英文品牌的外来语写法）；
并写名保留被括号两边指到的那一条，也就是已经规范化过的那一侧。
被丢弃的名字降为别名，扁平 `asset.studio` 一并改写成保留名（ADR-0005：兼容投影跟着
规范关系走）。只改实体不改投影的话，下一次刮削会照着投影里的旧名把实体再建一遍。

合并不可逆。默认只产出复核 CSV，`--apply` 才写 ledger 且必须给 `--backup`。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR   # noqa: E402
from peach.entities import merge_entity, studio_name_key   # noqa: E402
from peach.field_owners import write_owned_fields   # noqa: E402
from peach.review_csv import read_rows, write_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write,
)

MERGE_ALIAS_SOURCE = "merge:studio-name-variant"

#: 假名、汉字与全角片假名。判「这个名字是日文写法」只看有没有这些字符。
JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
#: 汉字或平假名。日文名含它们，对面的拉丁名就是罗马音转写；只有片假名的是外来语写法。
NATIVE_JAPANESE = re.compile(r"[぀-ゟ㐀-䶿一-鿿]")
#: 番号前缀：连字符前那一段字母数字。`336KBI-042` 的前缀是 `336KBI`。
CODE_PREFIX = re.compile(r"^([A-Za-z][A-Za-z0-9]*)-\d")
# AV OPEN 参赛作品跨厂牌共用这一前缀，不承载厂牌身份。
SHARED_EVENT_PREFIXES = frozenset({'AVOP'})
#: 两种写法并在一串里：`プレステージプレミアム(PRESTIGE PREMIUM)`。括号里不许再套括号，
#: 否则 `AVS collector's (2)` 这类带序号的名字会被当成并写。
BRACKETED = re.compile(r"^(.+?)\s*[（(]\s*([^（()）]+?)\s*[)）]\s*$")

FIELDS = ("keep_id", "keep_name", "keep_assets", "drop_id", "drop_name", "drop_assets",
          "klass", "evidence")


def variant_key(name: str) -> str:
    """写法变体的比较键：折掉符号写法与大小写，一个字符都不丢。

    不能退化成「只留 ASCII 字母数字」。那样纯日文名全折成空串，`シロウトTV` 与
    `ラグジュTV` 都折成 `tv`，129 个厂牌会凭空长出一堆假的重复对。
    """
    return studio_name_key(name)


def studios(connection: sqlite3.Connection) -> dict[int, str]:
    return {int(row[0]): str(row[1]) for row in connection.execute(
        "SELECT id,canonical_name FROM entity WHERE kind='studio'")}


def asset_facts(connection: sqlite3.Connection
                ) -> tuple[dict[int, int], dict[int, set[str]]]:
    """每个厂牌实体挂了多少作品、这些作品用过哪些番号前缀。"""
    counts: dict[int, int] = defaultdict(int)
    prefixes: dict[int, set[str]] = defaultdict(set)
    for entity_id, _asset_id, code in connection.execute(
            "SELECT DISTINCT ae.entity_id,a.id,a.code FROM asset_entity ae"
            " JOIN asset a ON a.id=ae.asset_id"
            " JOIN entity e ON e.id=ae.entity_id WHERE e.kind='studio'"):
        counts[int(entity_id)] += 1
        shape = CODE_PREFIX.match(str(code or ""))
        if shape:
            prefixes[int(entity_id)].add(shape.group(1).upper())
    return counts, prefixes


def _pair(keep: int, drop: int, names: dict[int, str], counts: dict[int, int],
          klass: str, evidence: str) -> dict[str, object]:
    return {"keep_id": keep, "keep_name": names[keep], "keep_assets": counts.get(keep, 0),
            "drop_id": drop, "drop_name": names[drop], "drop_assets": counts.get(drop, 0),
            "klass": klass, "evidence": evidence}


def spelling_variants(names: dict[int, str], counts: dict[int, int]) -> list[dict]:
    """同一个名字的两种写法。保留纯 ASCII 的那一个。"""
    grouped: dict[str, list[int]] = defaultdict(list)
    for entity_id, name in names.items():
        grouped[variant_key(name)].append(entity_id)
    rows = []
    for key, ids in sorted(grouped.items()):
        if len(ids) != 2:
            continue
        ascii_ids = [i for i in ids if names[i].isascii()]
        if len(ascii_ids) == 1:
            keep = ascii_ids[0]
            why = "同名不同写法，保留纯 ASCII 的一侧"
        else:
            keep = max(ids, key=lambda i: (counts.get(i, 0), -i))
            why = "同名不同写法，两侧同为 ASCII 或同为非 ASCII，保留作品多的一侧"
        drop = next(i for i in ids if i != keep)
        rows.append(_pair(keep, drop, names, counts, "写法变体", why))
    return rows


def script_variants(names: dict[int, str], counts: dict[int, int],
                    prefixes: dict[int, set[str]], taken: set[int]) -> list[dict]:
    """日文名与罗马字名各存一条。证据是共用的番号前缀，不是转写。"""
    latin = [i for i, name in names.items()
             if not JAPANESE.search(name) and i not in taken]
    rows = []
    for entity_id, name in sorted(names.items(), key=lambda item: item[1]):
        if not JAPANESE.search(name) or entity_id in taken or not prefixes.get(entity_id):
            continue
        shared = [(other, sorted((prefixes[entity_id] & prefixes.get(other, set()))
                                 - SHARED_EVENT_PREFIXES))
                  for other in latin]
        hits = [item for item in shared if item[1]]
        if len(hits) != 1:
            # 一个前缀都不共用说明证据不足；共用到两家以上说明前缀本身分不开，
            # 两种都交人工——这条捷径唯一的身份保证就是前缀，它不成立就没有别的。
            continue
        other, common = hits[0]
        evidence = f"共用番号前缀 {'、'.join(common)}"
        if NATIVE_JAPANESE.search(name):
            rows.append(_pair(entity_id, other, names, counts, "日文名／罗马字名",
                              evidence + "；日文名含汉字或平假名，罗马字名是转写"))
        else:
            rows.append(_pair(other, entity_id, names, counts, "日文名／罗马字名",
                              evidence + "；日文名是纯片假名，保留英文品牌名"))
    return rows


def bracketed_variants(connection: sqlite3.Connection, names: dict[int, str],
                       counts: dict[int, int], taken: set[int]) -> list[dict]:
    """日英并写的一串另存了一条。证据是账本自己登记的别名，不是转写。

    mgstage 与 libredmm 把厂牌写成 `プレステージプレミアム(PRESTIGE PREMIUM)` 这样
    一串。整串匹配不到已有实体，落库那一刻就自己立了一条新的；括号两边各自却都是
    `Prestige` 名下登记过的写法。番号前缀在这里作不了证：素人系的前缀以数字开头
    （`300MIUM`），`CODE_PREFIX` 一个都取不到。

    括号两边都得是账本登记过的写法，并且指向同一条实体。少了「两边都登记过」这一半，
    `Faleno (2)` 这种带序号的名字就会被当成并写——括号里那个 `2` 说明不了任何事，
    剩下的等于只按「名字开头一样」合并。指到两家去的同样交人工：合并不可逆。
    """
    registered: dict[str, set[int]] = defaultdict(set)
    for entity_id, name in names.items():
        registered[variant_key(name)].add(entity_id)
    for entity_id, alias in connection.execute(
            "SELECT ea.entity_id,ea.alias FROM entity_alias ea"
            " JOIN entity e ON e.id=ea.entity_id WHERE e.kind='studio'"):
        registered[variant_key(str(alias or ""))].add(int(entity_id))
    rows = []
    for entity_id, name in sorted(names.items(), key=lambda item: item[1]):
        shape = BRACKETED.match(name)
        if entity_id in taken or not shape:
            continue
        sides = [registered[variant_key(part)] - {entity_id} for part in shape.groups()]
        if any(len(side) != 1 for side in sides) or sides[0] != sides[1]:
            continue
        keep = next(iter(sides[0]))
        if keep in taken:
            continue
        rows.append(_pair(keep, entity_id, names, counts, "并写名",
                          f"括号两边都是 {names[keep]} 的登记写法"))
        taken.update({keep, entity_id})
    return rows


def collect(connection: sqlite3.Connection) -> list[dict]:
    names = studios(connection)
    counts, prefixes = asset_facts(connection)
    rows = spelling_variants(names, counts)
    taken = {int(row["keep_id"]) for row in rows} | {int(row["drop_id"]) for row in rows}
    rows += script_variants(names, counts, prefixes, taken)
    taken |= {int(row["keep_id"]) for row in rows} | {int(row["drop_id"]) for row in rows}
    return rows + bracketed_variants(connection, names, counts, taken)


def apply_rows(connection: sqlite3.Connection, rows: list[dict]) -> dict[str, int]:
    """逐对合并，并把扁平 `asset.studio` 改写成保留名。"""
    counts = Counter()
    for row in rows:
        moved = merge_entity(
            connection, target_id=int(row["keep_id"]), source_id=int(row["drop_id"]),
            source_name=str(row["drop_name"]), alias_source=MERGE_ALIAS_SOURCE)
        counts["merged"] += 1
        for key in ("assets", "aliases", "links", "terms", "dropped_refs"):
            counts[key] += moved[key]
        assets = [int(asset[0]) for asset in connection.execute(
            "SELECT id FROM asset WHERE studio=?", (row["drop_name"],))]
        result = write_owned_fields(connection, assets, {'studio': row['keep_name']},
                                    'review:studio-name-variant')
        counts["flat_rewritten"] += result.assets
    return dict(counts)


def relink_logos(rows: list[dict], logo_root: Path) -> dict[str, list[str]]:
    """把被丢弃那一侧的标识文件挪到保留名下。

    合并只改账本，标识却按 `logo_key(canonical_name)` 落盘，所以 `プレステージ.logo.img`
    在合并那一刻起就没人认领了——而它正是 jae.tokyo 那套 320×320 方标，比保留方手上的
    那张好。`Prestige.logo.img` 缺着，页面大位就回落到补白字标。

    只补保留方缺的变体，已有的一个字节都不动（和 `studio_icons.install` 同一条
    口径）。挪不动的留在原地并列出来：孤儿文件不会被读到，删它需要单独的授权。
    """
    from peach.previews import relink_logo_files

    moved: list[str] = []
    left: list[str] = []
    for row in rows:
        done, stayed = relink_logo_files(str(row["drop_name"]), str(row["keep_name"]), logo_root)
        moved += done
        left += stayed
    return {"moved": moved, "left": left}


EXTRA_COUNTS = {
    "studio_entities": "SELECT count(*) FROM entity WHERE kind='studio'",
    "studio_aliases": "SELECT count(*) FROM entity_alias a JOIN entity e"
                      " ON e.id=a.entity_id WHERE e.kind='studio'",
    "studio_asset_links": "SELECT count(*) FROM asset_entity ae JOIN entity e"
                          " ON e.id=ae.entity_id WHERE e.kind='studio'",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合并写法变体与日文名／罗马字名重复的厂牌实体")
    add_ledger_write_args(parser)
    parser.add_argument("--review-csv", type=Path,
                        default=GENERATED_DIR / "studio-name-variant-merge.csv")
    parser.add_argument("--logo-root", type=Path,
                        help="移动标识文件：把被丢弃名下的挪到保留名下，保留方缺哪个补哪个")
    return parser


def _recorded_pairs(path: Path) -> list[dict]:
    """从复核件读回已经合并过的那些对。

    标识按 canonical_name 落盘，只能在账本改完之后再动；那时 `collect` 已经找不到
    这些对了——它们在账本里已经是一条。复核件是可重放的中间结果，这一步靠它。
    """
    return read_rows(path, missing_ok=True)


def run(args: argparse.Namespace) -> int:
    connection = open_for_write(args)
    try:
        rows = collect(connection)
        recorded = _recorded_pairs(args.review_csv) if args.logo_root else []
        if rows:
            write_rows(args.review_csv, FIELDS, rows)
        print(f"重复厂牌 {len(rows)} 组，复核 CSV：{args.review_csv}")
        print("  判据分布：", dict(Counter(str(row["klass"]) for row in rows)))
        for row in rows:
            print(f"    {row['drop_name']}（{row['drop_assets']} 部）"
                  f" -> {row['keep_name']}（{row['keep_assets']} 部）  {row['evidence']}")
        if args.logo_root:
            relinked = relink_logos(rows or recorded, args.logo_root)
            print(f"  标识改挂 {len(relinked['moved'])} 个文件"
                  f"（保留方已有、留在原地的 {len(relinked['left'])} 个）")
            for line in relinked["moved"]:
                print(f"    {line}")
        if not args.apply:
            print("  未写 ledger（加 --apply --backup 才写）")
            return 0

        print(f"  已备份到 {args.backup}")
        before = counts_of(connection, EXTRA_COUNTS)
        with connection:
            moved = apply_rows(connection, rows)
        after = counts_of(connection, EXTRA_COUNTS)
        integrity, violations = verify_after_write(connection)
        print("  合并结果：", moved)
        for key in before:
            mark = "" if before[key] == after[key] else "  <-- 变化"
            print(f"    {key}: {before[key]} -> {after[key]}{mark}")
        print(f"  foreign_key_check 违规 {violations} 条")
        print(f"  integrity_check: {integrity}")
        return 1 if violations or integrity != 'ok' else 0
    finally:
        connection.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
