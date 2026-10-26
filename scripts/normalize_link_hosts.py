"""把改过名的站点在 `entity_link` 里收成一个主机，不留两套写法。重复不只是难看——资料页会并排出现两枚一样的链接，`/link-mark`
按主机缓存图标就得为同一个站存两份，日后任何按主机做的判断（图标覆盖表、敏感来源、
死链清理）都要写两遍。

处理方式按行分两种，**不是**一律改写：

    entity 只有旧写法      → 改写 url 与 hostname，id、label、metadata 全部保留
    entity 两种写法都有    → 删掉旧写法那一行（`UNIQUE(entity_id,url)` 不允许并存，
                             而新写法那行已经承载同一个 handle）

删除的那几行不是丢信息：同一个实体上留下的 x.com 行指向同一个 handle。这一点在计划表
里逐条写明保留的是哪一行，不要只报一个数字。

默认 dry-run，只打印计划。`--apply` 必须同时给 `--backup`：这是真实账本写入。
写入是幂等的——跑完一次就没有旧写法了，重跑得到空计划。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# 别名表和改写规则与采集器共用（采集到 twitter.com 也要收成 x.com），定义在 peach.social_links。
from peach.social_links import HOST_ALIASES, canonical_url   # noqa: E402


def target_url(url: str) -> str:
    """旧主机 → 新主机后的地址；不涉及别名表就返回空串。"""
    new_url = canonical_url(url)
    return new_url if new_url != url else ""


def plan(connection: sqlite3.Connection) -> list[dict]:
    """逐行给出 rewrite 或 drop，附上判断依据。"""
    rows = connection.execute(
        "SELECT l.id, l.entity_id, l.label, l.url, e.canonical_name "
        "FROM entity_link l JOIN entity e ON e.id=l.entity_id ORDER BY l.id").fetchall()
    taken = {(row["entity_id"], row["url"]): row["id"] for row in rows}

    planned: list[dict] = []
    for row in rows:
        new_url = target_url(row["url"])
        if not new_url or new_url == row["url"]:
            continue
        keeper = taken.get((row["entity_id"], new_url))
        item = {"id": row["id"], "entity_id": row["entity_id"],
                "entity": row["canonical_name"], "label": row["label"],
                "url": row["url"], "new_url": new_url}
        if keeper is not None:
            item.update(action="drop", reason=f"同实体已有 #{keeper} 指向 {new_url}")
        else:
            item.update(action="rewrite", reason="")
            # 本轮改写会占掉这个地址，后面同实体的旧写法必须看得见这一点。
            taken[(row["entity_id"], new_url)] = row["id"]
        planned.append(item)
    return planned


def apply_plan(connection: sqlite3.Connection, planned: list[dict]) -> tuple[int, int]:
    """执行计划，返回 (改写行数, 删除行数)。"""
    now = datetime.now(timezone.utc).isoformat()
    rewritten = dropped = 0
    for item in planned:
        if item["action"] == "rewrite":
            connection.execute(
                "UPDATE entity_link SET url=?, hostname=?, updated_at=? WHERE id=?",
                (item["new_url"], urlsplit(item["new_url"]).hostname or "", now, item["id"]))
            rewritten += connection.execute("SELECT changes()").fetchone()[0]
        else:
            connection.execute("DELETE FROM entity_link WHERE id=?", (item["id"],))
            dropped += connection.execute("SELECT changes()").fetchone()[0]
    return rewritten, dropped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="真正写入；必须同时给 --backup")
    parser.add_argument("--backup", type=Path, help="写入前的 SQLite 备份落点")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and not args.backup:
        print("[stop] --apply 是真实账本写入，必须同时给 --backup")
        return 2

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        before = connection.execute("SELECT count(*) FROM entity_link").fetchone()[0]
        planned = plan(connection)
        for item in planned:
            mark = "~" if item["action"] == "rewrite" else "-"
            print(f" {mark} {str(item['entity'])[:14]:<14} {item['url'][:46]:<46} "
                  f"{item['new_url'][:40]:<40} {item['reason']}")
        rewrites = [i for i in planned if i["action"] == "rewrite"]
        drops = [i for i in planned if i["action"] == "drop"]
        print({"库内链接": before, "将改写": len(rewrites), "将删除（同实体已有新写法）": len(drops)})
        if not args.apply:
            print("dry-run；确认无误后加 --apply --backup <路径>")
            return 0

        args.backup.parent.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(args.backup)
        with destination:
            connection.backup(destination)
        destination.close()
        print(f"已备份：{args.backup}")

        with connection:
            rewritten, dropped = apply_plan(connection, planned)
        after = connection.execute("SELECT count(*) FROM entity_link").fetchone()[0]
        left = connection.execute(
            "SELECT count(*) FROM entity_link WHERE hostname IN (%s)"
            % ",".join("?" * len(HOST_ALIASES)), tuple(HOST_ALIASES)).fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        orphans = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        print({"改写": rewritten, "删除": dropped, "写入后 entity_link": after,
               "差值": before - after, "残留旧主机": left,
               "integrity_check": integrity, "foreign_key_check": orphans})
        if (before - after != dropped or rewritten != len(rewrites) or left
                or integrity != "ok" or orphans):
            print("[warn] 前后差值、残留、完整性或外键与预期不符，请人工核对")
            return 1
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    # Windows 重定向到文件时 stdout 是 GBK，实体名里的零宽字符会让打印计划表直接炸掉。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
