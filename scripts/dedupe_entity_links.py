"""把同一条实体上指向同一个去处的链接合并成一条。

`entity_link` 的 `UNIQUE(entity_id,url)` 只认字面，而同一个去处有很多种写法：

    http://kawakami-yuu.livedoor.biz      http://kawakami-yuu.livedoor.biz/
    https://x.com/yu_kinao               https://x.com/yu_kinao/status/1221041995415580672
    …/talent/%e6%b6%bc%e6%a3%ae…/        …/talent/%E6%B6%BC%E6%A3%AE…/

第一组差一个斜杠，第二组是同一个账号的主页和它的一条推文，第三组只差百分号转义的
大小写。三组在资料页上都是并排两枚一模一样的图标，点下去到同一个人那里。采集器还在
跑，所以这不是一次性清理：不同来源对同一个账号的写法本来就不同，这条路要留着复用。

判据分两种，因为「同一个去处」在社媒和普通网站上不是一回事：
  * 社媒按账号算。平台加 handle 就是身份，路径后面挂什么（status、photo、媒体页）
    都还是同一个人的账号。
  * 其余按主机加路径算，路径末尾的斜杠不算差别。查询串和片段照算——`?id=3` 换一个
    值多半就是另一个页面。

路径先解回字节再比：`%E6` 和 `%e6` 是同一个字节，而日文艺人页的路径整段都是转义的
假名，两个来源各按自己的习惯写大小写。解成字节而不是文字，是因为路径不保证是 UTF-8。

同组保留 URL 最短的那条：最短的就是账号主页或站点根，也就是点进去最有用的那个。

默认 dry-run。`--apply` 必须同时给 `--backup`：这是真实账本写入。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.review_csv import write_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    add_ledger_write_args,
    counts_of,
    open_for_write,
    verify_after_write,
)
from peach.social_links import account_segment, host_of, platform   # noqa: E402

FIELDS = ("entity_id", "entity", "verdict", "link_id", "link_kind", "label", "url", "note")

EXTRA_COUNTS = {"entity_link": "SELECT count(*) FROM entity_link"}


def destination(url: str) -> str:
    """「这条链接指向哪里」的身份键。写法不同、去处相同的链接得到同一个键。"""
    name = platform(url)
    if name:
        handle = account_segment(url)
        # 平台的功能页（没有账号段）落回普通规则，否则一个域下所有功能页会挤成一条。
        if handle:
            return f"{name}\t{handle.casefold()}"
    parts = urlsplit(url.strip())
    path = unquote_to_bytes(parts.path).rstrip(b"/").decode("latin-1")
    return "\t".join((host_of(url), path, parts.query, parts.fragment))


def keep_order(item: dict) -> tuple:
    """同一组里留哪条。https 先于 http，再取最短，最后按 id 定序。

    只按最短取会把 `https://www.prestige-av.com/` 换成 `http://www.prestige-av.com/`：
    少一个字符，代价是资料页上一条本来加密的外链变成明文跳转。长度是用来挑出「账号
    主页而不是某条推文」的，不该顺带决定协议。末尾的 id 保证结果不随查询顺序变。
    """
    return (urlsplit(item["url"]).scheme != "https", len(item["url"]), item["link_id"])


def collect(connection) -> list[dict]:
    """逐实体找出指向同一个去处的多条链接，标出留哪条、删哪条。"""
    groups: dict[tuple[int, str], list[dict]] = {}
    for row in connection.execute(
            "SELECT l.id,l.entity_id,l.link_kind,l.label,l.url,e.canonical_name "
            "FROM entity_link l JOIN entity e ON e.id=l.entity_id ORDER BY l.id"):
        item = {"entity_id": row["entity_id"], "entity": row["canonical_name"],
                "link_id": row["id"], "link_kind": row["link_kind"],
                "label": row["label"], "url": row["url"]}
        groups.setdefault((row["entity_id"], destination(row["url"])), []).append(item)
    out: list[dict] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = min(members, key=keep_order)
        for item in members:
            same = item is keep
            out.append(dict(item, verdict="keep" if same else "delete",
                            note="" if same else f"与 {keep['link_id']} 同一个去处"))
    return out


def apply_rows(connection, rows) -> int:
    doomed = [row["link_id"] for row in rows if row["verdict"] == "delete"]
    for link_id in doomed:
        connection.execute("DELETE FROM entity_link WHERE id=?", (link_id,))
    return len(doomed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ledger_write_args(parser)
    parser.add_argument("--out", help="把逐条判定写成 CSV 复核产物")
    args = parser.parse_args()

    connection = open_for_write(args)
    try:
        rows = collect(connection)
        before = counts_of(connection, EXTRA_COUNTS)
        if args.out:
            write_rows(args.out, FIELDS, rows, atomic=True, fill_missing=True)
        groups = sum(1 for row in rows if row["verdict"] == "keep")
        doomed = sum(1 for row in rows if row["verdict"] == "delete")
        print(f"重复组 {groups}，多余链接 {doomed}")
        for row in rows:
            if row["verdict"] == "delete":
                print(f"  删 {row['link_id']:5} {row['entity']}  {row['url']}")
        if not args.apply:
            print("dry-run：没有写入。加 --apply --backup <路径> 才真的删。")
            return 0
        removed = apply_rows(connection, rows)
        connection.commit()
        after = counts_of(connection, EXTRA_COUNTS)
        integrity, violations = verify_after_write(connection)
        print(f"删除 {removed} 条")
        for key in sorted(before):
            print(f"  {key}: {before[key]} -> {after[key]}")
        print(f"integrity_check={integrity} foreign_key_check={violations}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
