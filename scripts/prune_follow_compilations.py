"""清退已入库的跨作者合集条目。默认只列清单，`--apply` 才删。

抓取那一侧按画面作者数拦截新候选（`Rule34VideoConnector.MAX_COLLECTION_MODELS`），
判据收紧之前入库的行还在库里，这个脚本按同一个判据扫一遍存量。删除不可逆，
`--apply` 先把账本备份成 `ledger.pre-follow-compilations-<时间戳>.db`，
清单同时落盘成 CSV 复核产物。已经保存成 asset 的条目一律不动。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.config import DATABASE_PATH, GENERATED_DIR  # noqa: E402
from peach.follow_store import FollowStore  # noqa: E402
from peach.migrations import sqlite_backup  # noqa: E402
from peach.review_csv import write_rows  # noqa: E402

FIELDS = ("item_id", "external_id", "source_ref", "status", "credited", "visual", "title")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DATABASE_PATH, help="当前账本路径")
    parser.add_argument("--review", type=Path, help="复核 CSV 的落盘位置")
    parser.add_argument("--apply", action="store_true", help="真的删除；缺省只列清单")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    db = args.db.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    connection = sqlite3.connect(db)
    try:
        rows = FollowStore(lambda: connection).collected_compilations()
        review = args.review or GENERATED_DIR / f"follow-compilations-{stamp}.csv"
        write_rows(review, FIELDS,
                   [{field: getattr(row, field) for field in FIELDS} for row in rows])
        backup = None
        if args.apply and rows:
            connection.close()
            backup = db.with_name(f"{db.stem}.pre-follow-compilations-{stamp}{db.suffix}")
            sqlite_backup(db, backup)
            connection = sqlite3.connect(db)
            store = FollowStore(lambda: connection)
            with connection:
                removed = store.purge_compilations(rows, confirm=True)
        else:
            removed = 0
    finally:
        connection.close()

    if args.json:
        print(json.dumps({
            "db": str(db), "found": len(rows), "removed": removed,
            "backup": str(backup) if backup else None, "review": str(review),
        }, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row.visual:>2} 位画面作者（名单 {row.credited:>2} 位）"
                  f"  {row.source_ref}  {row.title[:52]}")
        verb = "已删除" if args.apply else "将删除"
        print(f"{verb} {len(rows)} 条，复核清单 {review}")
        if backup:
            print(f"账本备份 {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
