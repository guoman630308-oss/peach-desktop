#!/usr/bin/env python3
"""在真实账本上只读抽样相关推荐，只输出聚合指标和资产 ID。"""
from __future__ import annotations

import argparse
import json
import time
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH
from peach.scripting import open_readonly
from peach.web_contract import WebContract, q_related


def seed_ids(database: Path, count: int) -> list[int]:
    connection = open_readonly(database)
    try:
        return [row[0] for row in connection.execute(
            "SELECT a.id FROM asset a JOIN asset_entity ae ON ae.asset_id=a.id "
            "JOIN entity e ON e.id=ae.entity_id WHERE a.medium='video' "
            "AND a.disposal IS NULL AND e.kind='tag' GROUP BY a.id "
            "HAVING count(DISTINCT ae.entity_id)>=2 "
            "ORDER BY ((a.id * 1103515245 + 12345) % 2147483647),a.id LIMIT ?", (count,),
        )]
    finally:
        connection.close()


def run(database: Path, count: int, limit: int) -> dict:
    contract = WebContract(database)
    seeds = seed_ids(database, count)
    started = time.perf_counter()
    outputs = {seed: q_related(contract, seed, limit)["items"] for seed in seeds}
    repeatable = all(
        [row["id"] for row in rows]
        == [row["id"] for row in q_related(contract, seed, limit)["items"]]
        for seed, rows in outputs.items()
    )
    reasons: dict[str, int] = {}
    for rows in outputs.values():
        for row in rows:
            reasons[row["why"]] = reasons.get(row["why"], 0) + 1
    return {
        "database_mode": "ro",
        "seed_ids": seeds,
        "seed_count": len(seeds),
        "recommendation_count": sum(len(rows) for rows in outputs.values()),
        "average_per_seed": round(
            sum(len(rows) for rows in outputs.values()) / max(1, len(seeds)), 2
        ),
        "repeatable": repeatable,
        "reason_counts": dict(sorted(reasons.items())),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.db, args.seeds, args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
