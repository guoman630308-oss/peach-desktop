"""存量修正：西方资料页写下的罗马字单名别名（`performer_alias_followup.latin_rejection`）。

`Lena`、`Mara`、`Kitten` 这种单个词不是艺名，同名的人很多；按别名唯一命中归并出演者时
会把别人挂到她名下。这里把指定来源写下、按现判据不收的别名删掉。页面主名本身是单个词的
不删：给 `--plan` 时从采集清单读每位的 `matched_name`。

默认只列计划（只读打开账本）；`--apply` 必须同时给 `--backup`。

    repair_western_single_aliases.py --source auto:babepedia-profile --output <回执.json> \
        --plan <复核目录>/babepedia-person-profiles-plan-1.json --plan <复核目录>/western-profiles-plan.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.performer_alias_followup import latin_rejection  # noqa: E402
from peach.entities import name_rank  # noqa: E402
from peach.scripting import (  # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write)


def primaries(paths: list[Path]) -> dict[int, str]:
    """采集清单里每位的页面主名。"""
    found: dict[int, str] = {}
    for path in paths:
        for record in json.loads(path.read_text(encoding="utf-8")):
            if record.get("entity_id") and record.get("matched_name"):
                found[int(record["entity_id"])] = str(record["matched_name"])
    return found


def plan(connection, source: str, primary: dict[int, str]) -> list[dict]:
    """这个来源写下、按现判据不收的罗马字别名。"""
    rows = []
    for entity_id, name, alias, batch in connection.execute(
            "SELECT a.entity_id,e.canonical_name,a.alias,a.source FROM entity_alias a"
            " JOIN entity e ON e.id=a.entity_id WHERE substr(a.source,1,?)=?"
            " ORDER BY a.entity_id,a.alias", (len(source) + 1, source + "@")):
        if name_rank(alias) < 3:
            continue
        reason = latin_rejection(alias, primary.get(int(entity_id), ""))
        if reason:
            rows.append({"entity_id": entity_id, "entity": name, "alias": alias,
                         "source": batch, "reason": reason})
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = add_ledger_write_args(argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--source", default="auto:babepedia-profile")
    parser.add_argument("--plan", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection = open_for_write(args)
    try:
        before = counts_of(connection)
        rows = plan(connection, args.source, primaries(args.plan))
        receipt = {"apply": args.apply, "source": args.source, "before": before, "aliases": rows}
        if args.apply:
            for row in rows:
                connection.execute("DELETE FROM entity_alias WHERE entity_id=? AND alias=? AND source=?",
                                   (row["entity_id"], row["alias"], row["source"]))
            connection.commit()
            integrity, violations = verify_after_write(connection)
            receipt.update(after=counts_of(connection), integrity=integrity, foreign_keys=violations)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        for row in rows:
            print(f" - {row['entity']}（{row['entity_id']}）{row['alias']}：{row['reason']}")
        print(json.dumps({"aliases": len(rows), "apply": args.apply}, ensure_ascii=False))
        return 0 if not args.apply or receipt["integrity"] == "ok" else 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
