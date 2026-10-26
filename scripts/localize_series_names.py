#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 r18dev 同一响应中的官方日文系列名修正 Peach 规范实体。

输入是 ``scrape_codes.py`` 生成的字段候选 CSV。脚本会回读每条候选引用的原始
快照，只接受 r18dev 响应 ``translations`` 中明确标为日文的系列名。默认只输出
审计 CSV；真实写入必须同时使用 ``--apply --backup``。证据冲突、兼容投影不一致
或缺少日文原文的实体一律跳过。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR
from peach.entities import merge_entity, normalize_entity_name
from peach.metadata import collapse_repeated_phrase
from peach.review_csv import read_rows, write_rows
from peach.scripting import add_ledger_write_args, counts_of, open_for_write, verify_after_write


SOURCE = "r18dev:series-localization"
MERGE_SOURCE = "merge:series-localization"
FIELDS = (
    "entity_id", "current_name", "target_name", "assets", "codes",
    "evidence_codes", "raw_snapshots", "action", "reason", "merge_target_id",
    "revision",
)


def _contains_latin(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]", value or ""))


def _truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _code_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _snapshot_japanese_series(path: Path) -> str:
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return ""
    if str(wrapper.get("source") or "") != "r18dev":
        return ""
    payload = wrapper.get("result")
    if not isinstance(payload, dict):
        return ""
    for translation in payload.get("translations") or []:
        if not isinstance(translation, dict):
            continue
        if not str(translation.get("language") or "").lower().startswith("ja"):
            continue
        value, _ = collapse_repeated_phrase(str(translation.get("series") or ""))
        return value.strip()
    return ""


def read_evidence(path: Path) -> dict[str, list[dict[str, str]]]:
    """回读候选及原始快照，返回按规范番号分组的可验证日文系列名。"""
    by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_rows(path):
            if row.get("field") != "series":
                continue
            query = _code_key(str(row.get("query") or row.get("code") or ""))
            try:
                candidates = json.loads(row.get("candidates_json") or "[]")
            except (TypeError, ValueError):
                continue
            for candidate in candidates if isinstance(candidates, list) else []:
                if not isinstance(candidate, dict):
                    continue
                if (candidate.get("source") != "r18dev"
                        or candidate.get("source_kind") != "official_mirror"
                        or not _truthy(candidate.get("official"))):
                    continue
                raw_value = str(candidate.get("raw_snapshot") or "").strip()
                if not raw_value:
                    continue
                snapshot = Path(raw_value)
                if not snapshot.is_absolute():
                    snapshot = path.parent / snapshot
                japanese = _snapshot_japanese_series(snapshot)
                candidate_value, _ = collapse_repeated_phrase(
                    str(candidate.get("value") or ""))
                if not japanese or japanese != candidate_value.strip():
                    continue
                by_code[query].append({
                    "target": japanese,
                    "snapshot": str(snapshot.resolve()),
                    "source_url": str(candidate.get("source_url") or ""),
                })
    return dict(by_code)


def collect(
    connection: sqlite3.Connection,
    evidence: dict[str, list[dict[str, str]]],
    revision: str,
) -> list[dict[str, object]]:
    connection.row_factory = sqlite3.Row
    entity_rows = connection.execute(
        "SELECT id,canonical_name,normalized_name FROM entity "
        "WHERE kind='series' ORDER BY id"
    ).fetchall()
    owners = {str(row["normalized_name"]): int(row["id"]) for row in entity_rows}
    rows: list[dict[str, object]] = []

    for entity in entity_rows:
        entity_id = int(entity["id"])
        assets = connection.execute(
            "SELECT DISTINCT a.id,a.code,a.series FROM asset a "
            "JOIN asset_entity ae ON ae.asset_id=a.id "
            "WHERE ae.entity_id=? AND ae.role='series' AND a.medium='video' "
            "ORDER BY a.id",
            (entity_id,),
        ).fetchall()
        codes = sorted({_code_key(str(row["code"] or "")) for row in assets
                        if str(row["code"] or "").strip()})
        mismatched_assets = [
            int(row["id"]) for row in assets
            if str(row["series"] or "").strip()
            and normalize_entity_name(str(row["series"])) != str(entity["normalized_name"])
        ]
        proofs = [proof for code in codes for proof in evidence.get(code, [])]
        targets = sorted({proof["target"] for proof in proofs}, key=str.casefold)
        base: dict[str, object] = {
            "entity_id": entity_id,
            "current_name": str(entity["canonical_name"]),
            "target_name": "",
            "assets": len(assets),
            "codes": "|".join(codes),
            "evidence_codes": "|".join(sorted(
                code for code in codes if evidence.get(code))),
            "raw_snapshots": "|".join(sorted({proof["snapshot"] for proof in proofs})),
            "action": "",
            "reason": "",
            "merge_target_id": "",
            "revision": revision,
            "_proofs": proofs,
        }
        if mismatched_assets:
            base.update(
                action="skip-projection-mismatch",
                reason="asset.series 与规范系列关系不一致：" +
                       ",".join(map(str, mismatched_assets)),
            )
        elif not _contains_latin(str(entity["canonical_name"])):
            base.update(
                target_name=str(entity["canonical_name"]),
                action="keep-localized", reason="规范名已无拉丁字母",
            )
        elif not targets:
            base.update(action="skip-no-japanese-evidence", reason="没有可验证的官方日文系列名")
        elif len(targets) != 1:
            base.update(
                action="skip-evidence-conflict",
                reason="同一系列关联番号返回多个日文名：" + " | ".join(targets),
            )
        else:
            target = targets[0]
            normalized = normalize_entity_name(target)
            base["target_name"] = target
            if normalized == str(entity["normalized_name"]):
                base.update(action="keep-source-same", reason="官方日文原文与当前规范名相同")
            elif normalized in owners and owners[normalized] != entity_id:
                base.update(
                    action="merge-into-existing", reason="日文规范实体已存在",
                    merge_target_id=owners[normalized],
                )
            else:
                base.update(action="rename", reason="官方日文翻译证据唯一")
        rows.append(base)
    return rows


def _insert_alias(
    connection: sqlite3.Connection, entity_id: int, alias: str, revision: str,
) -> int:
    normalized = normalize_entity_name(alias)
    canonical = connection.execute(
        "SELECT normalized_name FROM entity WHERE id=?", (entity_id,)
    ).fetchone()[0]
    if not normalized or normalized == canonical:
        return 0
    connection.execute(
        "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence) "
        "VALUES(?,?,?,?,1.0)",
        (entity_id, alias, normalized, f"{SOURCE}@{revision[:12]}"),
    )
    return int(connection.execute("SELECT changes()").fetchone()[0])


def _write_provenance(
    connection: sqlite3.Connection, entity_id: int, row: dict[str, object], revision: str,
) -> None:
    raw = connection.execute(
        "SELECT metadata_json FROM entity WHERE id=?", (entity_id,)
    ).fetchone()[0]
    try:
        metadata = json.loads(raw or "{}")
    except (TypeError, ValueError):
        metadata = {}
    metadata["series_name_localization"] = {
        "source": "r18dev",
        "source_kind": "official_mirror",
        "revision": revision,
        "previous_name": str(row["current_name"]),
        "japanese_name": str(row["target_name"]),
        "evidence_codes": str(row["evidence_codes"]).split("|")
        if row["evidence_codes"] else [],
        "raw_snapshots": str(row["raw_snapshots"]).split("|")
        if row["raw_snapshots"] else [],
    }
    connection.execute(
        "UPDATE entity SET metadata_json=? WHERE id=?",
        (json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), entity_id),
    )


def _sync_projection(connection: sqlite3.Connection, entity_id: int, target: str) -> int:
    connection.execute(
        "UPDATE asset SET series=? WHERE id IN ("
        "SELECT asset_id FROM asset_entity WHERE entity_id=? AND role='series'"
        ") AND COALESCE(series,'')<>?",
        (target, entity_id, target),
    )
    return int(connection.execute("SELECT changes()").fetchone()[0])


def apply_rows(
    connection: sqlite3.Connection, rows: list[dict[str, object]], revision: str,
) -> dict[str, int]:
    counts = Counter()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in rows:
        action = str(row["action"])
        if action not in {"rename", "merge-into-existing"}:
            continue
        entity_id = int(row["entity_id"])
        target = str(row["target_name"])
        if action == "merge-into-existing":
            target_id = int(row["merge_target_id"])
            moved = merge_entity(
                connection, target_id=target_id, source_id=entity_id,
                source_name=str(row["current_name"]), alias_source=MERGE_SOURCE,
            )
            counts["merged"] += 1
            counts["relations_moved"] += moved["assets"]
            counts["dropped_refs"] += moved["dropped_refs"]
            counts["aliases"] += _insert_alias(
                connection, target_id, str(row["current_name"]), revision)
            counts["projections"] += _sync_projection(connection, target_id, target)
            _write_provenance(connection, target_id, row, revision)
            counts["provenance"] += 1
            continue

        connection.execute(
            "UPDATE entity SET canonical_name=?,normalized_name=?,updated_at=? WHERE id=?",
            (target, normalize_entity_name(target), stamp, entity_id),
        )
        counts["renamed"] += int(connection.execute("SELECT changes()").fetchone()[0])
        counts["aliases"] += _insert_alias(
            connection, entity_id, str(row["current_name"]), revision)
        counts["projections"] += _sync_projection(connection, entity_id, target)
        _write_provenance(connection, entity_id, row, revision)
        counts["provenance"] += 1
    return dict(counts)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    write_rows(path, FIELDS, rows, fill_missing=True)


#: 本脚本自己关心的口径；基础计数由 `scripting.counts_of` 给。
EXTRA_COUNTS = {
    "series": "SELECT count(*) FROM entity WHERE kind='series'",
    "latin_series": "SELECT count(*) FROM entity WHERE kind='series' "
                    "AND canonical_name GLOB '*[A-Za-z]*'",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="用官方日文证据本地化系列规范名")
    add_ledger_write_args(parser)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument(
        "--audit-csv", type=Path,
        default=GENERATED_DIR / "series-name-localization.csv",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    evidence = read_evidence(args.candidates)
    connection = open_for_write(args)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        rows = collect(connection, evidence, args.revision)
        write_csv(args.audit_csv, rows)
        print(f"已审计 series {len(rows)} 个；审计 CSV：{args.audit_csv}")
        print("  动作分布：", dict(Counter(str(row["action"]) for row in rows)))
        if not args.apply:
            print("  未写 ledger（加 --apply --backup 才写）")
            return 0

        print(f"  已备份到 {args.backup}")
        before = counts_of(connection, EXTRA_COUNTS)
        with connection:
            changed = apply_rows(connection, rows, args.revision)
        after = counts_of(connection, EXTRA_COUNTS)
        integrity, foreign_keys = verify_after_write(connection)
        print("  写入结果：", changed)
        for key in before:
            print(f"    {key}: {before[key]} -> {after[key]}")
        print(f"  integrity_check={integrity}；foreign_key_check={foreign_keys}")
        return 1 if integrity != "ok" or foreign_keys else 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
