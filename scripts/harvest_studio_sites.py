"""为厂牌找出官网的命令行入口，判据全在 `peach.studio_sites`。

出一份复核 CSV，`ok` 与 `weak` 都留给人看，装进账本走 `install_entity_links.py`。
新登记的厂牌不必跑这个：处理任务的补厂牌后继按同一份判据把 `ok` 直接登记（ADR-0052）。
这个入口留给补历史，以及 `--seeds` 喂人工查到的地址。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import studio_sites as sites   # noqa: E402
from peach.config import STATE_DIR   # noqa: E402
from peach.jobs import job_main   # noqa: E402
from peach.review_csv import read_rows, write_rows   # noqa: E402
from peach.scripting import open_readonly   # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="账本路径")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=Path, help="人工查到的 studio,site 表，优先于推导域名")
    parser.add_argument("--min-assets", type=int, default=3)
    parser.add_argument("--only", nargs="*", default=[],
                        help="只处理这几个厂牌，按 canonical_name 给；给了就不看作品数")
    parser.add_argument("--interval", type=float, default=1.2)
    # httpx 把这个标量同时用作 connect 与 read 超时，死域名因此最多吃两份。首轮用 20
    # 秒的代价是整批三个多小时；真站实测都在 2 秒内响应，8 秒已经宽裕得多。
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--limit", type=int, default=0)
    # `job_main` 直接读 args.lock，没有这一行会在拿锁时才 AttributeError。
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".studio-sites.lock")
    return parser


def run(args) -> int:
    connection = open_readonly(args.db)
    try:
        studios = (sites.load_named_studios(connection, args.only) if args.only
                   else sites.load_studios(connection, args.min_assets))
        if args.limit:
            studios = studios[:args.limit]
        aliases = sites.load_aliases(connection, [record["entity_id"] for record in studios])
    finally:
        connection.close()

    seeds: dict[str, list[str]] = {}
    if args.seeds:
        for row in read_rows(args.seeds):
            site = (row.get("site") or "").strip()
            if site:
                seeds.setdefault((row.get("studio") or "").strip(), []).append(site)

    results: list[dict[str, object]] = []
    last = 0.0
    for record in studios:
        name = record["studio"]
        # 规范名自己也可能登记在别名表里；留着它，判词就会写「页面写作『X』」而 X 正是
        # 规范名，读的人会以为命中的是另一个写法。
        other_names = tuple(a for a in aliases.get(record["entity_id"], ()) if a != name)
        row, last = sites.discover(record, other_names, seeds.get(name, []),
                                   interval=args.interval, timeout=args.timeout, last=last)
        results.append(row)
        print(f"{name[:22]:<22} {row['verdict']:<6} {str(row['final_url'])[:38]:<38} "
              f"{str(row['title'])[:34]}")

    write_rows(args.output, sites.FIELDS, results)
    counts: dict[str, int] = {}
    for row in results:
        counts[str(row["verdict"])] = counts.get(str(row["verdict"]), 0) + 1
    print({"total": len(results), **counts, "output": str(args.output)})
    return 0


if __name__ == "__main__":
    # 进度行里有日文标题，`ソフト・オン・デマンド` 的 `・` 在 GBK 控制台上编不出来，
    # 一个 print 就能把整批跑掀掉。证据在 CSV（UTF-8）里，进度行糊掉无所谓。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(job_main(build_parser, run))
