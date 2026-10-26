"""一次补完库里女优的资料：逐条跑补女优资料后继（ADR-0067），不等处理任务按名额排。

处理任务每轮只给存量 `STOCK_SHARE` 个名额，九百多位女优要一个多月才轮遍；这个脚本把
同一条后继在当前进程里挨个跑掉。取页、限流、冷却、双向核对、落库与判词 CSV 全是后继
自己那一套，这里只负责挑人和记进度：

- 挑人用后继的 `stock`：有路进得去、记号未到期的女优，作品多的在前。跑过又没变的由记号
  跳过，所以中断之后原样重跑就是接着跑。
- 写入批次是 `auto:performer-profile@manual-<时间>`，
  `scripts/revert_auto_landing.py --source auto:performer-profile` 整批撤回。
- 连续 `STOP_AFTER_UNFETCHED` 位都「未取得」多半是站点冷却或断网，停下来报告，不把剩下
  的女优全刷成「未取得」。

用法：
  python scripts/run_performer_profiles.py                      # dry-run，只报待跑人数
  python scripts/run_performer_profiles.py --apply --backup <路径> [--limit N]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import performer_profile_followup as profiles  # noqa: E402
from peach.followups import Attempts, attempts_root  # noqa: E402
from peach.migrations import sqlite_backup  # noqa: E402
from peach.scripting import (BACKUP_REQUIRED, add_ledger_write_args, open_readonly,  # noqa: E402
                             verify_after_write)

#: 连续几位「未取得」就停。站点冷却期内每一位都会立刻落成「未取得」，再跑只是刷判词。
STOP_AFTER_UNFETCHED = 5


class Handle:
    """后继要的任务句柄只用到批次号与进度，这里把进度打到终端。"""

    def __init__(self, run_id: str):
        self.run_id = run_id

    def progress(self, **_kwargs) -> None:
        return None


def pending(connection, generated: Path, limit: int | None) -> list:
    attempts = Attempts(attempts_root(generated))
    return profiles.stock(connection, attempts, limit=limit or 10**9)


def sweep(contract, items, run_id: str, *, out=sys.stdout,
          stop_after: int = STOP_AFTER_UNFETCHED) -> dict:
    """挨个跑，返回各结论的计数；连续 `stop_after` 位未取得就停。"""
    handle, tally, streak = Handle(run_id), {}, 0
    for index, item in enumerate(items, 1):
        try:
            summary = profiles.run(contract, item.key, handle)
        except Exception as error:  # 一位出错只算她自己，接着跑下一位。
            summary = {"outcome": f"出错：{type(error).__name__}: {error}"}
        outcome = str(summary.get("outcome", ""))
        head = outcome.split("；")[0] or "无结论"
        tally[head] = tally.get(head, 0) + 1
        print(f"{index}/{len(items)} {summary.get('name', item.label)}：{outcome}", file=out, flush=True)
        streak = streak + 1 if outcome == profiles.UNFETCHED else 0
        if streak >= stop_after:
            print(f"连续 {streak} 位未取得，停下：多半是站点冷却或网络断了，稍后原样重跑即可接着来",
                  file=out, flush=True)
            tally["停在"] = index
            break
    return tally


def main(argv=None) -> int:
    # 原地改编码：`sweep` 的默认输出在定义时就绑着这个对象。中文控制台是 GBK，名字里的生僻字会让打印抛错。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_ledger_write_args(parser)
    parser.add_argument("--limit", type=int, help="这一趟最多跑几位")
    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        raise SystemExit(BACKUP_REQUIRED)
    from peach.web_state import WebContract
    contract = WebContract(args.db)
    with open_readonly(args.db) as connection:
        items = pending(connection, contract.candidate_root, args.limit)
    print(f"待补资料的女优：{len(items)} 位")
    if not args.apply:
        for item in items[:10]:
            print(f"  {item.label}")
        return 0
    sqlite_backup(Path(args.db), Path(args.backup))
    print(f"备份：{args.backup}")
    run_id = f"manual-{time.strftime('%Y%m%dT%H%M%S')}"
    tally = sweep(contract, items, run_id)
    print("结论：" + "，".join(f"{key} {value}" for key, value in tally.items()))
    print(f"批次：{profiles.SOURCE}@{run_id}；判词：{contract.candidate_root / profiles.REVIEW_FILE}")
    with open_readonly(args.db) as connection:
        integrity, violations = verify_after_write(connection)
    print(f"integrity_check：{integrity}；外键违规：{violations}")
    return 0 if integrity == "ok" and not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
