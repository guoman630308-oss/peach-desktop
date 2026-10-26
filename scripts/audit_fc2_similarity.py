#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""把现有 FC2 评论收获与只读 ledger 媒体事实合成跨号相似候选。

不联网、不调用第三方 organizer，不依赖 FC2-Leak-Detector/JavSP。输入只复用 Peach 已有的
fc2-comment-harvest、fc2-candidate-log 和 ledger；输出候选与健康 CSV，不改番号、不合并资产、
不写 review_decision。
"""
from __future__ import annotations

import argparse
import sqlite3
import time
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.scripting import open_readonly
from peach.fc2_similarity import POLICY_VERSION, build_candidates, fc2_video_id
from peach.review_csv import read_rows, write_rows


CANDIDATE_FIELDS = (
    "pair_key", "code", "left_video_id", "right_video_id", "left_code",
    "right_code", "left_owned", "right_owned", "evidence_kinds",
    "comment_seen_on", "comment_assertions", "shared_performers",
    "left_asset_id", "right_asset_id", "exact_hash", "duration_close",
    "duration_delta_seconds", "size_close", "size_delta_percent",
    "resolution_match", "part_conflict", "confidence", "warnings", "reason",
    "policy_version", "status", "inferred",
)
HEALTH_FIELDS = (
    "source", "policy_version", "harvest_rows", "metadata_rows", "fc2_assets",
    "owned_video_ids", "evidence_pairs", "deferred_external_pairs",
    "comment_candidates", "exact_hash_candidates", "inferred_candidates",
    "candidates", "elapsed_ms", "errors",
    "last_error_kind", "last_error_message",
)


def build_parser() -> argparse.ArgumentParser:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--harvest", type=Path, default=GENERATED_DIR / "fc2-comment-harvest.csv",
    )
    parser.add_argument(
        "--metadata", type=Path, default=GENERATED_DIR / "fc2-candidate-log.csv",
    )
    parser.add_argument(
        "--out", type=Path,
        default=GENERATED_DIR / f"fc2-similarity-candidate-{stamp}.csv",
    )
    parser.add_argument(
        "--evidence", type=Path,
        default=GENERATED_DIR / f"fc2-similarity-evidence-{stamp}.csv",
        help="所有跨号证据；库外 counterpart 留在这里，不占 /review",
    )
    parser.add_argument(
        "--health", type=Path,
        default=GENERATED_DIR / f"fc2-similarity-health-{stamp}.csv",
    )
    return parser


def read_csv(path: Path) -> list[dict]:
    return read_rows(path)


def harvest_index(rows: list[dict]) -> dict[str, dict]:
    return {
        video_id: row for row in rows
        if (video_id := fc2_video_id(row.get("video_id")))
    }


def collection_ids(rows: list[dict]) -> set[str]:
    return {
        video_id for row in rows
        if str(row.get("is_collection") or "").strip()
        and (video_id := fc2_video_id(row.get("video_id") or row.get("code")))
    }


def load_assets(database: Path) -> list[dict]:
    connection = open_readonly(database)
    try:
        return [dict(row) for row in connection.execute(
            "SELECT id,code,name,size,duration,width,height,hash FROM asset "
            "WHERE upper(code) LIKE 'FC2%' AND medium='video' "
            "AND (disposal IS NULL OR disposal<>'trash') ORDER BY id"
        )]
    finally:
        connection.close()


def atomic_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    write_rows(path, fields, rows, atomic=True, fill_missing=True)


def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    health = {
        "source": "fc2_comment_harvest+ledger", "policy_version": POLICY_VERSION,
        "harvest_rows": 0, "metadata_rows": 0, "fc2_assets": 0,
        "owned_video_ids": 0, "comment_candidates": 0,
        "evidence_pairs": 0, "deferred_external_pairs": 0,
        "exact_hash_candidates": 0, "inferred_candidates": 0, "candidates": 0,
        "elapsed_ms": 0, "errors": 0, "last_error_kind": "",
        "last_error_message": "",
    }
    try:
        harvest_rows = read_csv(args.harvest)
        metadata_rows = read_csv(args.metadata) if args.metadata.is_file() else []
        assets = load_assets(args.db)
        harvest = harvest_index(harvest_rows)
        evidence = build_candidates(assets, harvest, collection_ids(metadata_rows))
        # 只有两边都有本地资产，用户才有原视频可核。库外 counterpart 保存在
        # evidence/harvest 等待以后入库，不拿不可执行项淹没 /review。
        candidates = [row for row in evidence if row["left_owned"] and row["right_owned"]]
        health.update({
            "harvest_rows": len(harvest_rows), "metadata_rows": len(metadata_rows),
            "fc2_assets": len(assets),
            "owned_video_ids": len({fc2_video_id(row.get('code')) for row in assets}),
            "evidence_pairs": len(evidence),
            "deferred_external_pairs": len(evidence) - len(candidates),
            "comment_candidates": sum(
                "comment_equivalent" in row["evidence_kinds"] for row in candidates
            ),
            "exact_hash_candidates": sum(
                "exact_hash" in row["evidence_kinds"] for row in candidates
            ),
            "inferred_candidates": sum(bool(row["inferred"]) for row in candidates),
            "candidates": len(candidates),
        })
        atomic_csv(args.evidence, CANDIDATE_FIELDS, evidence)
        atomic_csv(args.out, CANDIDATE_FIELDS, candidates)
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as error:
        health["errors"] = 1
        health["last_error_kind"] = type(error).__name__
        health["last_error_message"] = str(error)[:500]
        health["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        atomic_csv(args.health, HEALTH_FIELDS, [health])
        print(f"FC2 相似候选失败：{error}")
        print(f"健康报告 → {args.health}")
        return 2
    health["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    atomic_csv(args.health, HEALTH_FIELDS, [health])
    print(f"FC2 跨号候选 {health['candidates']} 条 → {args.out}")
    print(f"全部跨号证据 {health['evidence_pairs']} 条 → {args.evidence}")
    print(f"来源健康 → {args.health}")
    print("只生成候选：未修改 ledger、番号、资产关系或复核决定。")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
