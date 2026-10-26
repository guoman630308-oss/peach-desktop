#!/usr/bin/env python3
"""只读审计全部带 code 的视频，核对 JAV 番号、标题清洁与版本徽章投影。"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.catalog_rules import (
    is_jav_asset,
    is_jav_code,
    jav_display_metadata,
)
from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.scripting import open_readonly
from peach.review_csv import write_rows


FIELDS = (
    "asset_id", "name", "raw_code", "display_code", "display_title",
    "edition_badges", "is_jav", "issues",
)
DOMAIN = re.compile(
    r"(?:www\.)?[a-z0-9][a-z0-9-]{1,30}\."
    r"(?:com|net|la|xyz|cc|me|top|vip|club|info|org|tv|app|co|pw|gg|cn)",
    re.I,
)


def audit(database: Path) -> list[dict[str, object]]:
    connection = open_readonly(database)
    try:
        rows = connection.execute(
            "SELECT a.id,a.name,a.code,a.studio,a.release_date,"
            "COALESCE((SELECT group_concat(DISTINCT e.kind) FROM asset_entity ae "
            "JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id=a.id),'') entity_kinds,"
            "COALESCE((SELECT group_concat(value,'|') FROM ("
            "SELECT DISTINCT t.tag value FROM asset_tag t WHERE t.asset_id=a.id UNION "
            "SELECT DISTINCT e.canonical_name value FROM asset_entity ae "
            "JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id=a.id AND e.kind='tag'"
            ")),'') tags FROM asset a WHERE a.medium='video' "
            "AND a.code IS NOT NULL AND trim(a.code)<>'' ORDER BY a.id"
        ).fetchall()
    finally:
        connection.close()

    output = []
    for row in rows:
        kinds = tuple(filter(None, str(row["entity_kinds"] or "").split(",")))
        tags = tuple(filter(None, str(row["tags"] or "").split("|")))
        jav = is_jav_asset(
            row["code"], row["studio"], row["release_date"], kinds,
        )
        display = jav_display_metadata(row["name"], row["code"], tags)
        issues = []
        if jav and not is_jav_code(row["code"]):
            issues.append("缺连字符但有发行证据")
        if jav and str(row["code"] or "").strip().upper() != display["display_code"]:
            issues.append("番号需规范显示")
        if DOMAIN.search(str(row["name"] or "")):
            issues.append("推广域名残留")
        if display["edition_badges"]:
            issues.append("版本语义改徽章")
        if not jav:
            issues.append("非JAV或缺发行证据")
        output.append({
            "asset_id": row["id"],
            "name": row["name"],
            "raw_code": row["code"],
            "display_code": display["display_code"] if jav else "",
            "display_title": display["display_title"] if jav else "",
            "edition_badges": " ".join(display["edition_badges"]) if jav else "",
            "is_jav": int(jav),
            "issues": "；".join(issues) if issues else "正常",
        })
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--out", type=Path, default=GENERATED_DIR / "jav-display-audit.csv",
    )
    args = parser.parse_args(argv)
    rows = audit(args.db)
    write_rows(args.out, FIELDS, rows)
    issue_count = sum(row["issues"] != "正常" for row in rows)
    jav_count = sum(int(row["is_jav"]) for row in rows)
    print({
        "database_mode": "ro", "rows": len(rows), "jav": jav_count,
        "needs_attention": issue_count, "output": str(args.out),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
