"""把 `entity.metadata_json.agency` 里的事务所名变成实体和成员关系。

事务所此前只是女优实体元数据里的一个字符串。字符串搜不到、点不开，也回答不了反向的
那一问——「Capsule Agency 有哪些人」。它和厂牌、系列一样是有名字、有官网、有成员的
规范身份，缺的只是一种 `kind`；0025 补上了 `agency` 和 `entity_membership`，这个脚本
把已经采到的 200 条归属搬进去。

采到的原文留在 `metadata.agency` 不动：那是证据（哪个站、什么时候采的），实体是结论。
两者都在，结论错了才有得回溯。

名字里的括号按同一条规则拆：括号外是现用名，括号里和括号后跟着的是读音或旧名，
一律留作别名。`GG(旧・Prime Agency)` 的现用名是 `GG`，而 `Prime Agency` 另有一家
仍在营业——旧名撞上别家的现用名时不写别名，只报出来等人判。

默认 dry-run。`--apply` 必须同时给 `--backup`：这是真实账本写入。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 括号写法的拆分是实体名字的规则，不是本脚本的：名册采集器按同一条核对站上写的名字。
from peach.entities import (   # noqa: E402,F401
    FORMER_PREFIX, agency_key, normalize_entity_name, rejected_agencies, split_name,
)
from peach.review_csv import write_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    add_ledger_write_args,
    counts_of,
    open_for_write,
    verify_after_write,
)
from peach.social_links import host_owners, owner_key   # noqa: E402

FIELDS = ("agency", "aliases", "members", "site", "note")

#: 成员关系与别名的来源。归属是 minnano-av 资料表「所属事務所」那一行给的，
#: 拆名字这一步是本脚本做的判断，两件事分开记。
MEMBERSHIP_SOURCE = "minnano-av:所属事務所"
ALIAS_SOURCE = "minnano-av:事務所旧称"

EXTRA_COUNTS = {
    "agency": "SELECT count(*) FROM entity WHERE kind='agency'",
    "entity_membership": "SELECT count(*) FROM entity_membership",
    "agency_link": ("SELECT count(*) FROM entity_link l JOIN entity e ON e.id=l.entity_id"
                    " WHERE e.kind='agency'"),
}

def agency_sites(connection) -> dict[str, str]:
    """事务所名 → 它的官网。

    证据是成员链接：`repair_link_labels.py` 已经把每条 official 链接的标签改成按域名
    归属写，所以标签等于事务所名的那些链接，它们的域名就是这家的站。取根地址而不是
    某位女优的个人页——那是她的页面，不是这家公司的首页。
    """
    sites: dict[str, str] = {}
    for host, owner in host_owners(connection).items():
        sites.setdefault(owner, f"https://{host}/")
    return sites


def collect(connection) -> list[dict]:
    """按事务所归拢：现用名、别名、成员 id、官网。"""
    found: dict[str, dict] = {}
    for row in connection.execute(
            "SELECT id,metadata_json FROM entity WHERE kind='performer'"
            " AND json_extract(metadata_json,'$.agency.name') IS NOT NULL"
            " ORDER BY id"):
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            continue
        agency = metadata.get("agency") or {}
        raw = str(agency.get("name") or "").strip()
        if not raw or agency_key(raw) in rejected_agencies(metadata):
            continue
        canonical, aliases = split_name(raw)
        item = found.setdefault(canonical, {
            "agency": canonical, "aliases": [], "member_ids": [], "site": "",
            "raw_names": [], "checked_at": str(agency.get("checked_at") or "")})
        if raw not in item["raw_names"]:
            item["raw_names"].append(raw)
        for alias in aliases:
            if alias not in item["aliases"]:
                item["aliases"].append(alias)
        item["member_ids"].append(int(row["id"]))
        # 同一家的两条记录采集时间不同就取新的：用户定的口径是保留最新那一次。
        item["checked_at"] = max(item["checked_at"], str(agency.get("checked_at") or ""))
    sites = agency_sites(connection)
    taken = {name for name in found}
    for item in found.values():
        # 链接标签写的是采到的原文，括号还在。现用名查不到就按原文再查一次，否则
        # `ACT(アクト)` 这几家明明有站，只因为拆过名字就查不到自己的官网。
        item["site"] = next((sites[name] for name in [item["agency"], *item["raw_names"]]
                             if name in sites), "")
        blocked = [alias for alias in item["aliases"] if alias in taken]
        item["aliases"] = [alias for alias in item["aliases"] if alias not in taken]
        item["note"] = (f"旧称 {'、'.join(blocked)} 是另一家现用名，不写别名"
                        if blocked else "")
    return sorted(found.values(), key=lambda item: (-len(item["member_ids"]), item["agency"]))


def apply_rows(connection, rows, stamp: str) -> dict:
    done = {"事务所": 0, "别名": 0, "归属": 0, "官网": 0}
    for item in rows:
        name = item["agency"]
        key = normalize_entity_name(name)
        connection.execute(
            "INSERT OR IGNORE INTO entity(kind,canonical_name,normalized_name,"
            "metadata_json,created_at,updated_at) VALUES('agency',?,?,'{}',?,?)",
            (name, key, stamp, stamp))
        done["事务所"] += connection.execute("SELECT changes()").fetchone()[0]
        agency_id = connection.execute(
            "SELECT id FROM entity WHERE kind='agency' AND normalized_name=?", (key,)
        ).fetchone()[0]
        for alias in item["aliases"]:
            connection.execute(
                "INSERT OR IGNORE INTO entity_alias"
                "(entity_id,alias,normalized_alias,source,confidence) VALUES(?,?,?,?,1.0)",
                (agency_id, alias, normalize_entity_name(alias), ALIAS_SOURCE))
            done["别名"] += connection.execute("SELECT changes()").fetchone()[0]
        for member_id in item["member_ids"]:
            # 主键在成员一侧，REPLACE 就是「移籍以最新一次采集为准」。
            connection.execute(
                "INSERT OR REPLACE INTO entity_membership"
                "(member_id,agency_id,source,confidence,checked_at) VALUES(?,?,?,1.0,?)",
                (member_id, agency_id, MEMBERSHIP_SOURCE, item["checked_at"] or stamp))
            done["归属"] += 1
        if item["site"]:
            connection.execute(
                "INSERT OR IGNORE INTO entity_link(entity_id,link_kind,label,url,hostname,"
                "is_sensitive,metadata_json,created_at,updated_at)"
                " VALUES(?,'official',?,?,?,0,?,?,?)",
                (agency_id, name, item["site"], owner_key(item["site"].split("/")[2]),
                 json.dumps({"source": "install_agencies", "installed_at": stamp},
                            ensure_ascii=False), stamp, stamp))
            done["官网"] += connection.execute("SELECT changes()").fetchone()[0]
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ledger_write_args(parser)
    parser.add_argument("--out", help="把逐家事务所的计划写成 CSV 复核产物")
    args = parser.parse_args()

    connection = open_for_write(args)
    try:
        rows = collect(connection)
        before = counts_of(connection, EXTRA_COUNTS)
        report = [{"agency": item["agency"], "aliases": " | ".join(item["aliases"]),
                   "members": len(item["member_ids"]), "site": item["site"],
                   "note": item["note"]} for item in rows]
        if args.out:
            write_rows(args.out, FIELDS, report, atomic=True, fill_missing=True)
        members = sum(len(item["member_ids"]) for item in rows)
        sited = sum(1 for item in rows if item["site"])
        print(f"事务所 {len(rows)} 家，成员 {members} 位，有官网 {sited} 家")
        for item in report:
            if item["note"]:
                print(f"  ! {item['agency']}：{item['note']}")
        if not args.apply:
            print("dry-run：没有写入。加 --apply --backup <路径> 才真的写。")
            return 0
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        done = apply_rows(connection, rows, stamp)
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
