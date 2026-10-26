"""驳回某位女优名下的一家事务所：记进她的元数据，撤掉这条归属，这家没人了就连实体一起删。

minnano-av 的「所属事務所」只记现在签在谁名下。退役后转进个人经纪公司的人，那一格写的
是她自己的公司（三上悠亜 → 株式会社Miss），照它建出来的是一家只有一个人、却不是 AV 事务所
的「事务所」。只删实体不够：`repair_link_labels.py`、`resync_performer_agency.py` 会把站上
那一格重新写进 `metadata.agency`，`install_agencies.py` 再照它把实体建回来。所以驳回记在
人身上（`peach.entities.AGENCY_REJECTED`），这几条路径读到它就跳过。

输入是复核 CSV，一行一对：`performer,agency,reason[,replacement]`，名字按 canonical_name 给。
每一行：

1. 她的 `metadata.agency_rejected` 追加 `{name, reason, source, decided_at}`，同名不重复记；
2. `metadata.agency` 写的正是这一家就移除——那是证据，但它已经被人判定为不成立；
3. 她的归属指向这一家就删掉；
4. 这家再没有成员就删实体，别名、链接、外部编号跟着外键级联删；
5. 给了 `replacement`（她 AV 时期真正的事务所，写法同站上那一格，如 `ONE'S DOUBLE(ワンズダブル)`）
   就改挂过去：按 `split_name` 拆出现用名与别名，缺实体就建，`metadata.agency` 也写成它，
   这样 `install_agencies.py` 重跑得出的是同一个结论。

默认 dry-run。`--apply` 必须同时给 `--backup`：这是真实账本写入。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.entities import (   # noqa: E402
    AGENCY_REJECTED, agency_key, normalize_entity_name, rejected_agencies, split_name,
)
from peach.review_csv import read_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write,
)
from peach.social_links import classify, host_owners   # noqa: E402

#: 驳回是人的判断，写入者按 `field_owners` 的归属串署名。
SOURCE = "user:manual"

EXTRA_COUNTS = {
    "agency": "SELECT count(*) FROM entity WHERE kind='agency'",
    "entity_membership": "SELECT count(*) FROM entity_membership",
    "entity_link": "SELECT count(*) FROM entity_link",
    "entity_external_ref": "SELECT count(*) FROM entity_external_ref",
}


def plan(connection, rows: list[dict[str, str]]) -> list[dict[str, object]]:
    """每一行对回账本：她是谁、这家是哪个实体、她现在归哪家、这家还剩几个人。"""
    found: list[dict[str, object]] = []
    for raw in rows:
        row = {key: (value or "").strip() for key, value in raw.items()}
        person = connection.execute(
            "SELECT id, metadata_json FROM entity WHERE kind='performer' AND canonical_name=?",
            (row["performer"],)).fetchone()
        wanted = agency_key(row["agency"])
        agency = next((item for item in connection.execute(
            "SELECT id, canonical_name FROM entity WHERE kind='agency'")
            if agency_key(item["canonical_name"]) == wanted), None)
        item = {**row, "entity_id": person["id"] if person else None,
                "agency_id": agency["id"] if agency else None, "note": ""}
        if not person or not wanted:
            item["note"] = "未取得：账本里没有这位 performer" if not person else "未取得：事务所名为空"
            found.append(item)
            continue
        held = connection.execute(
            "SELECT agency_id FROM entity_membership WHERE member_id=?", (person["id"],)).fetchone()
        item["holds"] = bool(agency and held and held["agency_id"] == agency["id"])
        others = connection.execute(
            "SELECT count(*) FROM entity_membership WHERE agency_id=? AND member_id<>?",
            (item["agency_id"], person["id"])).fetchone()[0] if agency else 0
        item["drop_entity"] = bool(agency) and others == 0
        item["note"] = (f"撤归属{'，删实体' if item['drop_entity'] else f'，这家还有 {others} 人'}"
                        if item["holds"] else "她不在这家名下，只记驳回")
        if row.get("replacement"):
            item["note"] += f"，改挂 {row['replacement']}"
        found.append(item)
    return found


def attach(connection, entity_id: int, raw: str, stamp: str) -> int:
    """把她挂到 AV 时期的事务所：缺实体就建，别名按 `split_name` 补上。返回新建的实体数。"""
    canonical, aliases = split_name(raw)
    key = normalize_entity_name(canonical)
    connection.execute(
        "INSERT OR IGNORE INTO entity(kind,canonical_name,normalized_name,"
        "metadata_json,created_at,updated_at) VALUES('agency',?,?,'{}',?,?)",
        (canonical, key, stamp, stamp))
    created = connection.execute("SELECT changes()").fetchone()[0]
    agency_id = connection.execute(
        "SELECT id FROM entity WHERE kind='agency' AND normalized_name=?", (key,)).fetchone()[0]
    for alias in aliases:
        connection.execute(
            "INSERT OR IGNORE INTO entity_alias"
            "(entity_id,alias,normalized_alias,source,confidence) VALUES(?,?,?,?,1.0)",
            (agency_id, alias, normalize_entity_name(alias), SOURCE))
    connection.execute(
        "INSERT OR REPLACE INTO entity_membership"
        "(member_id,agency_id,source,confidence,checked_at) VALUES(?,?,?,1.0,?)",
        (entity_id, agency_id, SOURCE, stamp))
    return created


def relabel_links(connection, entity_id: int, rejected: str, replacement: str, stamp: str) -> int:
    """她名下标签写着这家事务所的官方链接，按域名归属重写标签。返回改了几条。

    采集时 minnano-av 的「所属事務所」被贴在同一页每条 official 链接上：三上悠亜那条
    ONE'S DOUBLE 的个人页写着「株式会社Miss」。事务所驳回了，标签还替它说话。重写走
    `classify`，与 `repair_link_labels.py` 同一套：域名归谁写谁，不知道就写她 AV 时期的
    事务所，再不知道写「官方网站」。
    """
    owners = host_owners(connection)
    agency = split_name(replacement)[0] if replacement else ""
    changed = 0
    for link_id, label, url in connection.execute(
            "SELECT id,label,url FROM entity_link WHERE entity_id=? AND link_kind='official'",
            (entity_id,)).fetchall():
        if agency_key(label) != agency_key(rejected):
            continue
        connection.execute("UPDATE entity_link SET label=?,updated_at=? WHERE id=?",
                           (classify(url, agency, owners.get)[1], stamp, link_id))
        changed += 1
    return changed


def apply_rows(connection, rows: list[dict[str, object]], stamp: str) -> dict[str, int]:
    done = {"驳回": 0, "归属": 0, "事务所": 0, "改挂": 0, "新建事务所": 0, "链接标签": 0}
    # 外键默认关着，删实体时要它把别名、链接、外部编号一并级联删掉；PRAGMA 只能在事务外设。
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")
    for row in rows:
        if row["entity_id"] is None or not row["agency"]:
            continue
        entity_id = int(row["entity_id"])
        current = connection.execute(
            "SELECT metadata_json FROM entity WHERE id=?", (entity_id,)).fetchone()
        metadata = json.loads(current[0] or "{}")
        key = agency_key(str(row["agency"]))
        if key not in rejected_agencies(metadata):
            metadata.setdefault(AGENCY_REJECTED, []).append(
                {"name": row["agency"], "reason": row.get("reason", ""),
                 "source": SOURCE, "decided_at": stamp})
            done["驳回"] += 1
        if agency_key(str((metadata.get("agency") or {}).get("name") or "")) == key:
            metadata.pop("agency")
        replacement = str(row.get("replacement") or "")
        if replacement:
            metadata["agency"] = {"name": replacement, "source": f"{SOURCE}：{row.get('reason', '')}",
                                  "checked_at": stamp}
        connection.execute("UPDATE entity SET metadata_json=?,updated_at=? WHERE id=?",
                           (json.dumps(metadata, ensure_ascii=False), stamp, entity_id))
        done["链接标签"] += relabel_links(connection, entity_id, str(row["agency"]), replacement, stamp)
        if row.get("holds"):
            connection.execute("DELETE FROM entity_membership WHERE member_id=? AND agency_id=?",
                               (entity_id, int(row["agency_id"])))
            done["归属"] += connection.execute("SELECT changes()").fetchone()[0]
        if row.get("drop_entity"):
            connection.execute(
                "DELETE FROM entity WHERE id=? AND kind='agency' AND NOT EXISTS"
                " (SELECT 1 FROM entity_membership WHERE agency_id=?)",
                (int(row["agency_id"]), int(row["agency_id"])))
            done["事务所"] += connection.execute("SELECT changes()").fetchone()[0]
        if replacement:
            done["新建事务所"] += attach(connection, entity_id, replacement, stamp)
            done["改挂"] += 1
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ledger_write_args(parser)
    parser.add_argument("--input", type=Path, required=True,
                        help="复核 CSV：performer,agency,reason[,replacement]")
    args = parser.parse_args()

    connection = open_for_write(args)
    try:
        rows = plan(connection, read_rows(args.input))
        for row in rows:
            print(f"{row['performer']} × {row['agency']}：{row['note']}")
        if not args.apply:
            print("dry-run：没有写入。加 --apply --backup <路径> 才真的写。")
            return 0
        before = counts_of(connection, EXTRA_COUNTS)
        done = apply_rows(connection, rows, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        connection.commit()
        after = counts_of(connection, EXTRA_COUNTS)
        integrity, violations = verify_after_write(connection)
        print("写入：" + "，".join(f"{key} {value}" for key, value in done.items()))
        for key in sorted(before):
            print(f"  {key}: {before[key]} -> {after[key]}")
        print(f"integrity_check={integrity} foreign_key_check={violations}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
