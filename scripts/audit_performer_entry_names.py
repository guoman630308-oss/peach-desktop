#!/usr/bin/env python3
"""只读审计全库艺人的 MISSAV 入口与中文名证据缺口。"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from peach import entry_links  # noqa: E402
from peach.config import DATABASE_PATH, DATA_ROOT, STATE_DIR  # noqa: E402
from peach.review_csv import write_rows  # noqa: E402
from peach.scripting import open_readonly  # noqa: E402
from localize_performer_names import RELEASE_SOURCES  # noqa: E402

FIELDS = ("entity_id", "current_name", "assets", "jav_directory", "previous_name",
          "entry_name", "previous_url", "url", "name_changed", "cn_status", "cn_aliases",
          "aliases", "directory_status", "link_evidence")
KANA = re.compile(r"[\u3040-\u30ff]")
HAN = re.compile(r"[\u3400-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")


def _name_localization(raw: str):
    try:
        metadata = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return None
    return metadata.get("name_localization") if isinstance(metadata, dict) else None


def _directory_evidence(jav: bool, directory, selected: str, name: str) -> tuple[str, str]:
    """名录匹配与目标页面请求是两个独立的证据状态。"""
    if not jav:
        return "不适用", "不适用"
    path = directory.path(selected, name) if directory else None
    if path is None:
        return "未取得", "未取得：未取得名录条目，目标未逐页请求"
    if not path:
        return "需复核", "未取得：名录无唯一匹配"
    return "已匹配", "已取得：名录 href，目标未逐页请求"


def collect(connection: sqlite3.Connection, directory=None) -> list[dict]:
    """每位 performer 一行；入口生成正确与远端页面可访问分别留证。"""
    connection.row_factory = sqlite3.Row
    aliases: dict[int, list[dict]] = defaultdict(list)
    refs: dict[int, list[dict]] = defaultdict(list)
    sources: dict[int, set[str]] = defaultdict(set)
    assets: dict[int, set[int]] = defaultdict(set)
    for row in connection.execute("SELECT entity_id,alias,source FROM entity_alias ORDER BY entity_id,alias"):
        aliases[int(row["entity_id"])].append(dict(row))
    for row in connection.execute("SELECT entity_id,provider,external_kind,external_id FROM entity_external_ref"):
        refs[int(row["entity_id"])].append(dict(row))
    for row in connection.execute("SELECT entity_id,asset_id,source FROM asset_entity"):
        sources[int(row["entity_id"])].add(str(row["source"]))
        assets[int(row["entity_id"])].add(int(row["asset_id"]))
    out = []
    for entity in connection.execute("SELECT id,canonical_name,metadata_json FROM entity WHERE kind='performer' ORDER BY id"):
        entity_id = int(entity["id"])
        name = str(entity["canonical_name"])
        chain = aliases[entity_id]
        jav = entry_links.is_jav_performer(refs[entity_id])
        previous = entry_links.japanese_name("", chain) or name
        selected = entry_links.japanese_name(name, chain, _name_localization(entity["metadata_json"]))
        directory_status, link_evidence = _directory_evidence(jav, directory, selected, name)
        chinese = sorted({str(row["alias"]) for row in chain
                          if HAN.search(str(row["alias"])) and not KANA.search(str(row["alias"]))
                          and not LATIN.search(str(row["alias"]))})
        release = bool(RELEASE_SOURCES.intersection(sources[entity_id])) or jav
        status = ("非发行艺人，不判中译" if not release else
                  "假名规范名，有汉字别名待复核" if KANA.search(name) and chinese else
                  "假名规范名，中文来源未取得" if KANA.search(name) else
                  "拉丁规范名，需区分艺名与转写" if LATIN.search(name) else
                  "无假名规范名，通用中译覆盖未验证")
        out.append({"entity_id": entity_id, "current_name": name,
                    "assets": len(assets[entity_id]), "jav_directory": int(jav),
                    "previous_name": previous if jav else "", "entry_name": selected if jav else "",
                    "previous_url": f"https://missav.ws/cn/actresses/{quote(previous, safe='')}" if jav else "",
                    "url": next((row["url"] for row in entry_links.build(
                        entry_links.defaults(), name, refs[entity_id], chain,
                        _name_localization(entity["metadata_json"]), directory) if row["site"] == "missav"), ""),
                    "name_changed": int(jav and previous != selected), "cn_status": status,
                    "cn_aliases": "|".join(chinese), "aliases": "|".join(row["alias"] for row in chain),
                    "directory_status": directory_status,
                    "link_evidence": link_evidence})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--out", type=Path, default=DATA_ROOT / "review" / "performer-entry-name-audit.csv")
    parser.add_argument("--missav-directory", type=Path, default=STATE_DIR / entry_links.DIRECTORY_FILENAME)
    args = parser.parse_args(argv)
    directory = None
    if args.missav_directory.is_file():
        directory = entry_links.directory_index(json.loads(
            args.missav_directory.read_text(encoding="utf-8")))
    with open_readonly(args.db) as connection:
        rows = collect(connection, directory)
    write_rows(args.out, FIELDS, rows)
    print(json.dumps({"performers": len(rows), "missav_entries": sum(row["jav_directory"] for row in rows),
                      "entry_names_changed": sum(row["name_changed"] for row in rows),
                      "directory_status": dict(Counter(row["directory_status"] for row in rows)),
                      "cn_status": dict(Counter(row["cn_status"] for row in rows)),
                      "review_csv": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
