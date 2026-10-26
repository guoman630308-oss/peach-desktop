#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把女优官方链接的标签改成按域名归属写，并把事务所名移到它自己的位置。

采集时 `harvest_performer_links.py` 从 minnano-av 资料表读「所属事務所」，然后把这个名字
贴到同一页找到的每一条 official 链接上。资料表里「所属事務所」和「公式サイト」是两行不同
的事实：白石亚子的事务所是 T-POWERS，公式サイト填的却是 Prestige 的専属宣传页。于是资料页
上出现一个同时替两家公司说话的控件——文字写着 T-POWERS，图标和落点都是 Prestige。

这里做两件事，一次做完：

1. **标签改成按域名归属写**（`peach.social_links.host_owners`）。域名归谁就写谁的名字；
   账本不知道它归谁才保留原标签。Facebook 和 Linktree 此前不在平台表里、走了 official
   分支，顺带归位成 social。
2. **事务所名写进 `entity.metadata_json.agency`**，带来源和采集时间。它是有用的信息，
   只是不该住在链接标签里——标签的语义是「点过去会看到什么」，装不下「她签在谁名下」。
   女优会移籍，所以这里带时间戳、按最新覆盖。

改标签可逆（原值写进复核 CSV 就能改回去），但仍按真实写入对待：默认只产出复核 CSV，
`--apply` 才写 ledger 且必须给 `--backup`。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR   # noqa: E402
from peach.entities import agency_key, rejected_agencies   # noqa: E402
from peach.review_csv import write_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write,
)
from peach.social_links import classify, host_owners, owner_key   # noqa: E402

FIELDS = ("verdict", "entity_id", "performer", "link_id", "hostname",
          "label", "new_label", "link_kind", "new_link_kind", "agency", "note")

#: 不承诺点过去会见到谁的兜底标签。它没有说错话，所以不算需要修的。
GENERIC_LABEL = "官方网站"

EXTRA_COUNTS = {
    "official_link": "SELECT count(*) FROM entity_link WHERE link_kind='official'",
    "social_link": "SELECT count(*) FROM entity_link WHERE link_kind='social'",
    "有事务所的女优": (
        "SELECT count(*) FROM entity WHERE kind='performer'"
        " AND json_extract(metadata_json,'$.agency.name') IS NOT NULL"),
}


def studio_names(connection: sqlite3.Connection) -> set[str]:
    """厂牌的规范名。标签等于其中之一时它说的是片商，不是事务所。"""
    return {row[0] for row in connection.execute(
        "SELECT canonical_name FROM entity WHERE kind='studio'")}


def agency_from_label(label: str, hostname: str, studios: set[str]) -> str:
    """这条标签里装着的事务所名，没装就返回空。

    事务所名眼下只存在于标签里，别处没有第二份。所以改标签之前先把它取出来——直接覆盖
    等于把这个事实删掉，而它正是用户要的那个信息。
    """
    label = label.strip()
    if not label or label == GENERIC_LABEL or label in studios:
        return ""
    # 标签就是域名本身（`tma.co.jp`）时它是采集兜底，不是谁的名字。
    return "" if label.strip("/") in {hostname, f"www.{hostname}"} else label


def _metadata(text) -> dict:
    try:
        found = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def collect(connection: sqlite3.Connection) -> list[dict[str, object]]:
    """每条女优 official 链接的重判结果。"""
    owners = host_owners(connection)
    studios = studio_names(connection)
    rows: list[dict[str, object]] = []
    for link_id, entity_id, performer, kind, label, url, hostname, metadata in connection.execute(
        "SELECT l.id,l.entity_id,e.canonical_name,l.link_kind,l.label,l.url,l.hostname,e.metadata_json"
        " FROM entity_link l JOIN entity e ON e.id=l.entity_id"
        " WHERE e.kind='performer' AND l.link_kind='official' ORDER BY l.id"
    ):
        agency = agency_from_label(label, hostname, studios)
        # 人判过不成立的事务所（`reject_agency.py`）不再当标签的依据：三上悠亜那条
        # ONE'S DOUBLE 官网链接上写着的「株式会社Miss」就是这么一直留下来的。
        rejected = bool(agency) and agency_key(agency) in rejected_agencies(_metadata(metadata))
        agency = "" if rejected else agency
        new_kind, new_label = classify(url, agency, owners.get)
        owner = owners.get(owner_key(hostname), "")
        if (new_kind, new_label) == (kind, label):
            verdict, note = "keep", "标签与域名归属一致"
        elif new_kind != kind:
            verdict, note = "auto", f"{hostname} 是平台账号，不是 official"
        elif owner or rejected:
            verdict, note = "auto", (f"{hostname} 归 {owner}" if owner else f"「{label}」是已驳回的事务所")
        else:
            # 归属未知时 classify 会把原标签原样还回来，走不到这里；留着兜底。
            verdict, note = "review", f"{hostname} 归属未取得"
        rows.append({
            "verdict": verdict, "entity_id": entity_id, "performer": performer,
            "link_id": link_id, "hostname": hostname, "label": label,
            "new_label": new_label, "link_kind": kind, "new_link_kind": new_kind,
            "agency": agency, "note": note})
    # 域名归属未取得、标签却写着一个名字的：孤证，没有第二条链接能佐证它。
    for row in rows:
        if (row["verdict"] == "keep" and row["agency"]
                and not owners.get(owner_key(str(row["hostname"])))):
            row["verdict"] = "review"
            row["note"] = f"{row['hostname']} 归属未取得，标签只有这一条孤证"
    rows.sort(key=lambda row: ({"auto": 0, "review": 1, "keep": 2}[str(row["verdict"])],
                               int(row["link_id"])))
    return rows


def apply_rows(connection: sqlite3.Connection,
               rows: list[dict[str, object]], stamp: str) -> dict[str, int]:
    """改标签、归位 link_kind，并把事务所写进实体元数据。

    事务所对每位女优只留一条：她现在签在谁名下。移籍是常态，所以按最新覆盖，
    同时记下这一条是什么时候、从哪里取到的——没有时间戳就分不清哪一份更新。
    """
    relabelled = moved = agencies = 0
    for row in rows:
        if row["agency"]:
            entity_id = int(row["entity_id"])
            current = connection.execute(
                "SELECT metadata_json FROM entity WHERE id=?", (entity_id,)).fetchone()
            try:
                metadata = json.loads(current[0] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            if agency_key(str(row["agency"])) not in rejected_agencies(metadata):
                metadata["agency"] = {"name": str(row["agency"]),
                                      "source": "minnano-av 资料表「所属事務所」",
                                      "checked_at": stamp}
                connection.execute(
                    "UPDATE entity SET metadata_json=?,updated_at=? WHERE id=?",
                    (json.dumps(metadata, ensure_ascii=False), stamp, entity_id))
                agencies += 1
        if row["verdict"] not in {"auto"}:
            continue
        connection.execute(
            "UPDATE entity_link SET label=?,link_kind=?,updated_at=? WHERE id=?",
            (row["new_label"], row["new_link_kind"], stamp, int(row["link_id"])))
        relabelled += 1
        if row["new_link_kind"] != row["link_kind"]:
            moved += 1
    return {"改了标签": relabelled, "其中归位成 social": moved, "写下事务所": agencies}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_ledger_write_args(parser)
    parser.add_argument("--review-csv", type=Path,
                        default=GENERATED_DIR / "review" / "link-labels.csv",
                        help="复核 CSV 输出路径")
    return parser


def run(args: argparse.Namespace) -> int:
    connection = open_for_write(args)
    try:
        rows = collect(connection)
        args.review_csv.parent.mkdir(parents=True, exist_ok=True)
        write_rows(args.review_csv, FIELDS, rows)
        tally: dict[str, int] = {}
        for row in rows:
            tally[str(row["verdict"])] = tally.get(str(row["verdict"]), 0) + 1
        print(f"{len(rows)} 条女优官方链接 -> {args.review_csv}")
        print("  ", tally)
        for row in rows:
            if row["verdict"] == "auto":
                print(f"    [{row['entity_id']}] {row['performer']}："
                      f"{row['label']} -> {row['new_label']}（{row['note']}）")
        if not args.apply:
            print("  未写 ledger（加 --apply --backup 才写）")
            return 0
        print(f"  已备份到 {args.backup}")
        before = counts_of(connection, EXTRA_COUNTS)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with connection:
            done = apply_rows(connection, rows, stamp)
        after = counts_of(connection, EXTRA_COUNTS)
        _integrity, violations = verify_after_write(connection)
        print("  结果：", done)
        for key in before:
            mark = "" if before[key] == after[key] else "  <-- 变化"
            print(f"    {key}: {before[key]} -> {after[key]}{mark}")
        print(f"  foreign_key_check 违规 {violations} 条")
        return 1 if violations else 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
