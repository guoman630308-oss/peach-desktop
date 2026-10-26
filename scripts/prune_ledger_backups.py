"""清退旧的账本备份 `ledger.pre-*.db`。默认只列计划，`--apply` 才删。

保留规则在 `peach.ledger_backups`：最近几份、还没满 24 小时、比当前账本更新的都留；
当前账本 `integrity_check` 不是 ok 时拒绝清退。主文件已经不在的 `-wal`／`-shm` 一并扫掉。
托盘每次启动按同一规则自动跑一遍，这个脚本给手动与排障用。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import ledger_backups  # noqa: E402
from peach.config import DATABASE_PATH  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DATABASE_PATH, help="当前账本路径")
    parser.add_argument("--keep", type=int, default=ledger_backups.KEEP_RECENT,
                        help="无论多旧都保留的最近份数")
    parser.add_argument("--min-age-hours", type=float,
                        default=ledger_backups.MIN_AGE.total_seconds() / 3600,
                        help="不满这个小时数的备份一律保留")
    parser.add_argument("--apply", action="store_true", help="真的删除；缺省只打印计划")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    decided = ledger_backups.prune(
        args.db.resolve(), apply=args.apply, keep_recent=args.keep,
        min_age=timedelta(hours=args.min_age_hours),
    )
    if args.json:
        print(json.dumps({
            "db": str(args.db.resolve()),
            "applied": bool(args.apply and not decided.refused),
            "refused": decided.refused,
            "keep": [path.name for path in decided.keep],
            "remove": [path.name for path in decided.remove],
            "orphans": [path.name for path in decided.orphans],
            "removable_bytes": decided.removable_bytes,
        }, ensure_ascii=False, indent=2))
    else:
        verb = "已删除" if args.apply and not decided.refused else "将删除"
        for path in decided.keep:
            print(f"保留  {path.name}")
        for path in decided.remove:
            print(f"{verb}  {path.name}")
        for path in decided.orphans:
            print(f"{verb}  {path.name}（主文件已不在）")
        print(f"{verb} {len(decided.remove)} 份加 {len(decided.orphans)} 个无主副文件，"
              f"约 {decided.removable_bytes / 1_048_576:.0f} MB")
        if decided.refused:
            print(f"拒绝清退：{decided.refused}")
    return 2 if decided.refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
