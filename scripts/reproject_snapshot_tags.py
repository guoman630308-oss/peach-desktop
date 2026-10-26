#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""按每组标签记着的来源快照，把已落库的 javinizer 标签跟上现在的映射。

标签候选在抓取那一刻就映射成了中文，已落库的行不会随映射表自己变。这里从每条标签行记着
的 `raw_snapshot` 读回来源原词，按现在的映射重算，有两种口径：

- `--since <版本>`：映射表改了去向（`巨尻` 从「美臀」分出「巨臀」、`可愛い` 从「高颜值」
  分出「可爱」）。用那一版和现在各算一遍整套，只把两者的差加减到账本上：按整套比，同一部
  片 `巨尻` 与 `美尻` 都在时「美臀」留下；账本与快照之间和这次改动无关的出入原样不动。
- 不给 `--since`：补齐。批准时的映射认得的词少（09-01 那批用的还是早期英文表，NHDTB-455
  的 `Big Asses`、`Anal Sex` 都没映射上），账本比快照现在能映射出的少。只补缺的，一个不删；
  账本那一侧看这部片挂着的全部标签，不分来源。页面上删掉的标签只是记成隐藏，行还在，
  补齐不会把它加回来。

写入按差来：去掉的只删这一组来源下的那几行；加上的走 ADR-0082 的并集补标签
（`_extend_approved_tags`），归属是 `auto:metadata-tags@<时间>`，能用
`revert_auto_landing.py --source auto:metadata-tags --batch <批次号>` 整批撤回，下一次整套
批准也会连它们一起换掉。不整套重写：那会一并清掉这部片已有的并集补标签。不登记
review_decision，这不是新的批准。快照缺失或读不了、没有 genre 字段、套完一个标签都不剩
的，原样不动并报数。

默认只产出复核 CSV，`--apply` 才写 ledger 且必须给 `--backup`。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR
from peach.entities import normalize_entity_name
from peach.genre_decisions import load_genre_decisions
from peach.genre_taxonomy import map_genres
from peach.metadata_auto_apply import UNION_TAGS_SOURCE, _extend_approved_tags
from peach.review_csv import write_rows
from peach.scripting import add_ledger_write_args, counts_of, open_for_write, verify_after_write

FIELDS = ("asset_id", "code", "source", "status", "removed", "added", "snapshot")
#: 每组的去向；只有 `REWRITE` 会写库。
REWRITE, SAME, NO_SNAPSHOT, NO_GENRES, EMPTY = "重写", "不受影响", "快照缺失", "无 genre", "套完为空"
TAXONOMY = "src/peach/genre_taxonomy.py"


def taxonomy_at(revision: str) -> types.ModuleType:
    """`revision` 那一版的映射模块。它只依赖标准库，取出源码直接执行就是当时的行为。"""
    source = subprocess.run(["git", "show", f"{revision}:{TAXONOMY}"], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, encoding="utf-8", check=True).stdout
    module = types.ModuleType(f"genre_taxonomy_at_{revision}")
    exec(compile(source, f"{revision}:{TAXONOMY}", "exec"), module.__dict__)
    return module


def mapped_by(module: types.ModuleType, genres: list, decisions: dict) -> list[str]:
    """`module` 那一版映射出的标签。早于 `genre_decision` 的版本只收一个参数。"""
    try:
        return module.map_genres(genres, decisions)[0]
    except TypeError:
        return module.map_genres(genres)[0]


def snapshot_genres(path: str) -> list | None:
    """快照里的来源原词。javinizer-go 的快照外面包一层 `result`，资料快照直接就是正文。"""
    try:
        wrapper = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(wrapper, dict):
        return None
    payload = wrapper.get("result") if isinstance(wrapper.get("result"), dict) else wrapper
    genres = payload.get("genres")
    return genres if isinstance(genres, list) else None


def ledger_groups(connection: sqlite3.Connection) -> list[dict]:
    """每条资产在每个 javinizer 来源下的现有标签，连同那批标签落库时记下的出处。

    `held` 是这部片挂着的全部标签（规范化名，不分来源），补的时候不重复补。
    """
    held: dict[int, set[str]] = {}
    for asset_id, tag in connection.execute("SELECT asset_id,tag FROM asset_tag"):
        held.setdefault(asset_id, set()).add(normalize_entity_name(tag))
    groups: dict[tuple[int, str], dict] = {}
    for asset_id, code, path, source, confidence, tag in connection.execute(
            "SELECT t.asset_id,a.code,a.path,t.source,t.confidence,t.tag FROM asset_tag t "
            "JOIN asset a ON a.id=t.asset_id WHERE t.source LIKE 'javinizer:%:tag' "
            "ORDER BY t.asset_id,t.source"):
        group = groups.setdefault((asset_id, source), {
            "asset_id": asset_id, "code": code or "", "path": path, "source": source,
            "confidence": confidence, "tags": set(), "held": held[asset_id], "metadata": {}})
        group["tags"].add(tag)
    for asset_id, source, metadata_json in connection.execute(
            "SELECT asset_id,source,metadata_json FROM asset_entity "
            "WHERE role='tag' AND source LIKE 'javinizer:%:tag'"):
        group = groups.get((asset_id, source))
        if group is not None and not group["metadata"]:
            group["metadata"] = json.loads(metadata_json or "{}")
    return list(groups.values())


def judge(group: dict, before: types.ModuleType | None, decisions: dict) -> dict:
    """一组标签的去向与增减。`before` 为空是补齐：只加，不删。"""
    snapshot = str(group["metadata"].get("raw_snapshot") or "")
    genres = snapshot_genres(snapshot) if snapshot else None
    now = map_genres(genres, decisions)[0] if genres else []
    old = set(mapped_by(before, genres, decisions)) if genres and before else set()
    removed = (old - set(now)) & group["tags"]
    added = [tag for tag in now if tag not in old and normalize_entity_name(tag) not in group["held"]]
    if not snapshot or not Path(snapshot).is_file():
        status = NO_SNAPSHOT
    elif genres is None:
        status = NO_GENRES
    elif not (removed or added):
        status = SAME
    else:
        status = REWRITE if (group["tags"] - removed) or added else EMPTY
    changed = status == REWRITE
    return {**group, "status": status, "snapshot": snapshot,
            "removed": "、".join(sorted(removed)) if changed else "",
            "added": "、".join(added) if changed else ""}


def rewrite(connection: sqlite3.Connection, row: dict, batch: str, now: str) -> None:
    """把一组的差写进账本：加上的按并集补标签落，去掉的只删这一组来源下的行。

    先补后删：补的那一步过闸失败就整组不动，不留下只删了一半的组。
    """
    added = [tag for tag in row["added"].split("、") if tag]
    if added:
        metadata = row["metadata"]
        site = row["source"].split(":")[1]
        group = {"field": "tags", "code": row["code"], "query": row["code"],
                 "asset_id": row["asset_id"], "asset_path": row["path"],
                 "item_key": metadata.get("review_item") or f"{row['code']}:tags"}
        candidate = {key: metadata.get(key) for key in (
            "provider", "source_url", "provider_id", "content_id", "raw_snapshot", "candidate_key")}
        candidate.update(source=site, confidence=row["confidence"], value=added,
                         candidate_key=metadata.get("candidate_key") or f"reproject:{row['asset_id']}:{site}")
        if _extend_approved_tags(connection, group, candidate, batch, now) is None:
            raise ValueError("落库的闸没过（资产已不在或来源身份对不上）")
    for tag in filter(None, row["removed"].split("、")):
        connection.execute("DELETE FROM asset_tag WHERE asset_id=? AND tag=? AND source=?",
                           (row["asset_id"], tag, row["source"]))
        connection.execute(
            "DELETE FROM asset_entity WHERE asset_id=? AND role='tag' AND source=? AND entity_id IN "
            "(SELECT id FROM entity WHERE kind='tag' AND normalized_name=?)",
            (row["asset_id"], row["source"], normalize_entity_name(tag)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按来源快照把已落库的 javinizer 标签跟上现在的映射")
    add_ledger_write_args(parser)
    parser.add_argument("--since",
                        help="映射改动之前的那一版（git 提交或分支），只套它与现在之间的差；"
                             "不给就是补齐：快照现在映射得出、账本还没有的补上，一个不删")
    parser.add_argument("--review-csv", type=Path,
                        default=GENERATED_DIR / "snapshot-tag-reprojection.csv")
    return parser


def run(args: argparse.Namespace) -> int:
    before = taxonomy_at(args.since) if args.since else None
    connection = open_for_write(args)
    connection.row_factory = sqlite3.Row
    try:
        decisions = load_genre_decisions(connection)
        rows = [judge(group, before, decisions) for group in ledger_groups(connection)]
        tally = Counter(row["status"] for row in rows)
        print("资产×来源 {} 组：".format(len(rows))
              + "，".join(f"{status} {count}" for status, count in tally.most_common()))
        changes = Counter((kind, tag) for row in rows for kind in ("removed", "added")
                          for tag in row[kind].split("、") if tag)
        for (kind, tag), count in changes.most_common():
            print(f"  {'去掉' if kind == 'removed' else '加上'} {tag}：{count} 组")
        if args.apply:
            apply_rows(connection, [row for row in rows if row["status"] == REWRITE])
    finally:
        connection.close()
    write_rows(args.review_csv, FIELDS, [{field: row[field] for field in FIELDS}
                                         for row in rows if row["status"] != SAME])
    print(f"复核 CSV → {args.review_csv}")
    if not args.apply:
        print("这是预览：未写 ledger。确认后再加 --apply --backup。")
    return 0


def apply_rows(connection: sqlite3.Connection, rows: list[dict]) -> None:
    extra = {"tag": "SELECT count(*) FROM asset_tag"}
    before = counts_of(connection, extra)
    now = datetime.now(timezone.utc).isoformat()
    batch = f"{UNION_TAGS_SOURCE}@{time.strftime('%Y%m%dT%H%M%S')}"
    failures: list[str] = []
    with connection:
        for row in rows:
            try:
                rewrite(connection, row, batch, now)
            except ValueError as error:
                failures.append(f"{row['code'] or row['asset_id']} {row['source']}：{error}")
    after = counts_of(connection, extra)
    integrity, violations = verify_after_write(connection)
    print(f"已改 {len(rows) - len(failures)} 组；标签行 {before['tag']} → {after['tag']}；"
          f"补上的归属 {batch}")
    print(f"  自检：integrity_check={integrity}，外键违规 {violations} 条")
    for line in failures:
        print(f"  跳过 {line}")


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
