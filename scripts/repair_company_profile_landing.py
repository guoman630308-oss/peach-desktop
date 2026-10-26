"""存量修正：公司资料批次里没有代码判据的结论（ADR-0052 第一条）。

公司资料落库只认 `company_profiles` 的通用判据。这里按采集清单记下的原始页面缓存重放一遍
判据，把指定批次写下的内容改到判据下的样子：

- 公司资料格：重放得出同一个值的保持 observed，原文换成重放得出的那一句；得不出的降为
  `candidate`，记下原因，不进入显示契约，等人复核。
- 别名与链接：采集器不产出公司别名和链接，这一批写下的全部删掉，原值记进回执。

默认只列计划（只读打开账本）；`--apply` 必须同时给 `--backup`。

    repair_company_profile_landing.py --batch auto:company-profile@20261007-company \
        --plan <复核目录>/company-profile-observed-plan.json --output <回执.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import company_profiles  # noqa: E402
from peach.config import REVIEW_DIR  # noqa: E402
from peach.scripting import (  # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write)

SOURCE = "auto:company-profile"
REASON = "采集清单的原始页面按通用判据重放得不出这个值"


def plan(connection, pages_by_entity: dict[int, list[dict]], batch: str) -> dict:
    """逐格判定保留、改原文还是降为候选；别名与链接整批列出。"""
    fields = []
    for entity_id, name, raw in connection.execute(
            "SELECT id,canonical_name,metadata_json FROM entity WHERE kind IN ('studio','agency') ORDER BY id"):
        profile = json.loads(raw or "{}").get(company_profiles.KEY)
        if not isinstance(profile, dict):
            continue
        ours = {key: fact for key, fact in profile.items() if isinstance(fact, dict)
                and fact.get("source") == SOURCE and fact.get("batch") == batch
                and fact.get("status") != "candidate"}
        if not ours:
            continue
        names = company_profiles.entity_names(connection, entity_id) or [name]
        hosts = company_profiles.official_hosts(connection, entity_id, skip_batch=batch)
        replayed = company_profiles.replay(pages_by_entity.get(entity_id, []), names, hosts)["facts"]
        for key, fact in sorted(ours.items()):
            again = replayed.get(key)
            if again and again["value"] == fact.get("value"):
                action = "keep" if again["evidence"] == fact.get("evidence") else "evidence"
                fields.append({"entity_id": entity_id, "entity": name, "field": key, "action": action,
                               "value": fact.get("value"), "evidence": again["evidence"],
                               "source_url": again["source_url"]})
            else:
                fields.append({"entity_id": entity_id, "entity": name, "field": key, "action": "candidate",
                               "value": fact.get("value"), "evidence": fact.get("evidence")})
    aliases = [dict(zip(("entity_id", "entity", "alias"), row)) for row in connection.execute(
        "SELECT a.entity_id,e.canonical_name,a.alias FROM entity_alias a JOIN entity e ON e.id=a.entity_id"
        " WHERE a.source=? ORDER BY a.entity_id,a.alias", (batch,))]
    links = []
    for link_id, entity_id, name, url, raw in connection.execute(
            "SELECT l.id,l.entity_id,e.canonical_name,l.url,l.metadata_json FROM entity_link l"
            " JOIN entity e ON e.id=l.entity_id WHERE l.metadata_json LIKE ? ORDER BY l.id", (f"%{batch}%",)):
        metadata = json.loads(raw or "{}")
        if metadata.get("source") == SOURCE and metadata.get("batch") == batch:
            links.append({"id": link_id, "entity_id": entity_id, "entity": name, "url": url})
    return {"fields": fields, "aliases": aliases, "links": links}


def apply(connection, planned: dict) -> dict:
    """按计划改写；调用方负责事务。"""
    by_entity: dict[int, list[dict]] = {}
    for item in planned["fields"]:
        by_entity.setdefault(item["entity_id"], []).append(item)
    for entity_id, items in by_entity.items():
        metadata = json.loads(connection.execute("SELECT metadata_json FROM entity WHERE id=?",
                                                 (entity_id,)).fetchone()[0] or "{}")
        profile = metadata[company_profiles.KEY]
        for item in items:
            fact = profile[item["field"]]
            if item["action"] == "candidate":
                fact.update(status="candidate", candidate_reason=REASON)
            elif item["action"] == "evidence":
                fact.update(evidence=item["evidence"], source_url=item["source_url"])
        connection.execute("UPDATE entity SET metadata_json=? WHERE id=?",
                           (json.dumps(metadata, ensure_ascii=False), entity_id))
    for alias in planned["aliases"]:
        connection.execute("DELETE FROM entity_alias WHERE entity_id=? AND alias=?",
                           (alias["entity_id"], alias["alias"]))
    for link in planned["links"]:
        connection.execute("DELETE FROM entity_link WHERE id=?", (link["id"],))
    return {action: sum(item["action"] == action for item in planned["fields"])
            for action in ("keep", "evidence", "candidate")} | {
        "aliases": len(planned["aliases"]), "links": len(planned["links"])}


def build_parser() -> argparse.ArgumentParser:
    parser = add_ledger_write_args(argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--batch", required=True)
    parser.add_argument("--plan", type=Path, default=REVIEW_DIR / "company-profile-observed-plan.json")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.batch.startswith(SOURCE + "@"):
        raise SystemExit(f"--batch 必须以 {SOURCE}@ 开头")
    rows = json.loads(args.plan.read_text(encoding="utf-8"))["entities"]
    pages = {int(row["entity_id"]): row.get("pages", []) for row in rows}
    connection = open_for_write(args)
    try:
        before = counts_of(connection)
        planned = plan(connection, pages, args.batch)
        summary = {action: sum(item["action"] == action for item in planned["fields"])
                   for action in ("keep", "evidence", "candidate")} | {
            "aliases": len(planned["aliases"]), "links": len(planned["links"])}
        receipt = {"apply": args.apply, "batch": args.batch, "before": before, "plan": planned}
        if args.apply:
            summary = apply(connection, planned)
            connection.commit()
            integrity, violations = verify_after_write(connection)
            receipt.update(after=counts_of(connection), integrity=integrity, foreign_keys=violations)
        receipt["summary"] = summary
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        for item in planned["fields"]:
            if item["action"] != "keep":
                print(f" - {item['action']:<9} {item['entity']} {item['field']} = {item['value']}")
        for alias in planned["aliases"]:
            print(f" - 删别名   {alias['entity']} {alias['alias']}")
        for link in planned["links"]:
            print(f" - 删链接   {link['entity']} {link['url']}")
        print(json.dumps(summary, ensure_ascii=False))
        if args.apply:
            return 0 if receipt["integrity"] == "ok" else 1
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
