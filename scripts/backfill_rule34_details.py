#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""给已入库的 rule34.xxx 追更条目补上只有帖子页和原文件才有的细节。

posts DAPI 只回扁平标签和最后修改时间，三样东西要另外问：

- 标签类型（general／artist／character／copyright／metadata）在帖子页 `#tag-sidebar`。
- 上传时间在帖子页 `#stats` 的 Posted。DAPI 的 `change` 是最后一次修改：17361475
  五月上传、九月被改过标签，入库时记成了九月，卡片就排到了新内容里。
- 视频时长只在原文件头的 `mvhd` 里，接口和帖子页都不给。

常规「检查更新」只看第一页，而且已经补齐过类型的条目不再进第二阶段，存量行靠
它永远改不过来，所以要这一趟。判据不重写，直接复用连接器的 `_detail` 与
`_video_seconds`：判据只能有一处。

默认 dry-run，只写复核 CSV。`--apply` 必须同时给 `--backup`，与本仓库其它真实写入
脚本一致。`--resume` 跳过同一份 CSV 里已经写入的条目，中断后用原命令加它接着跑。

`--duration-only` 只补 mp4 缺的时长：只挑 `duration` 为空的 mp4 条目，不问帖子页，
只读原文件头，也只写 `duration`。时长在视频 CDN 上，不受帖子页每秒一次的限流牵连，
授权只覆盖时长时用它，别的列一格不动。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR
from peach.follow_sources import FollowSourceError, build_connector
from peach.review_csv import read_rows, write_rows
from peach.scripting import (
    BACKUP_REQUIRED, add_ledger_write_args, open_for_write, open_readonly,
)

FIELDS = ("item_id", "external_id", "result", "published_before", "published_after",
          "duration", "tag_types_added", "written", "note")

#: 帖子页的最小请求间隔。rule34.xxx 自己公布的口径是每 60 秒 60 次，也就是
#: 每次至少 1 秒；留一点余量。首轮按 0.35 秒跑过一次：400 条里 372 条「帖子页
#: 没有解析出类型」——不是这些帖子没有类型，是被限流挡了，`_detail`
#: 把非 200 吞成了空字典。速度换来的全是白跑的请求。视频文件头在另一个主机上，
#: 在这段间隔里发，不另占时间。
DEFAULT_DELAY = 1.1
#: 连续这么多条取不到就认定是被限流，而不是这一段恰好都取不到。
MISS_STREAK_LIMIT = 8
#: 限流后的静默时长，按站方公布的窗口取满一格。
COOLDOWN_SECONDS = 60.0
#: 每这么多条落一次库、写一次 CSV。
FLUSH_EVERY = 50


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="补齐 rule34.xxx 追更条目的标签类型、上传时间与视频时长")
    add_ledger_write_args(parser)
    parser.add_argument("--out", type=Path,
                        default=GENERATED_DIR / "rule34-detail-backfill.csv")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条，0 为全部")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--resume", action="store_true",
                        help="跳过 --out 里已经写入的条目，接着上一趟跑")
    parser.add_argument("--duration-only", action="store_true",
                        help="只补 mp4 缺的时长：不问帖子页，只读原文件头，只写 duration")
    return parser


def written_ids(path: Path) -> set[int]:
    """上一趟已经写进账本的条目。只认 `written=是`：dry-run 与未取得的都要再问。"""
    return {int(row["item_id"]) for row in read_rows(path, missing_ok=True)
            if row.get("written") == "是" and str(row.get("item_id") or "").isdigit()}


def pending_rows(connection: sqlite3.Connection, limit: int,
                 done: set[int] = frozenset(), *, duration_only: bool = False) -> list[dict]:
    """待补条目：rule34xxx 来源的全部条目，扣掉上一趟已经写入的。

    上传时间错没错，不问帖子页就看不出来——被改过的帖子 `change` 晚于上传，没改过的
    两者逐秒相同，账本里长得一样。所以每条都要问一遍，续跑靠 CSV 而不是账本里的值。
    只补时长时缺不缺看账本就知道，只挑 `duration` 为空的 mp4。
    """
    rows = []
    query = (
        "SELECT i.id, i.external_id, i.media_url, i.published_at, i.duration,"
        " i.metadata_json FROM follow_item i JOIN follow_source s ON s.id=i.source_id"
        " WHERE s.provider='rule34xxx'"
        + (" AND i.duration IS NULL" if duration_only else "")
        + " ORDER BY i.id"
    )
    for row in connection.execute(query):
        if row["id"] in done or (duration_only and not _is_mp4(row["media_url"])):
            continue
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except ValueError:
            metadata = {}
        rows.append({"id": row["id"], "external_id": str(row["external_id"] or ""),
                     "media_url": row["media_url"], "published_at": row["published_at"],
                     "duration": row["duration"],
                     "metadata": metadata if isinstance(metadata, dict) else {}})
        if limit and len(rows) >= limit:
            break
    return rows


def plan_update(row: dict, detail: dict, duration: float | None) -> dict | None:
    """这一条要改哪几列。没有可写的就是 None。

    只补不抹：帖子页没给上传时间就不动 `published_at`；已有时长不覆盖；已有非空
    类型不覆盖。取不到的留待下一趟，不写空值冒充已补。
    """
    update: dict = {}
    posted = detail.get("published_at")
    if posted and posted != row["published_at"]:
        update["published_at"] = posted
    if duration and row["duration"] is None:
        update["duration"] = duration
    tag_types = detail.get("tag_types")
    if tag_types and not row["metadata"].get("tag_types"):
        update["metadata_json"] = json.dumps(
            {**row["metadata"], "tag_types": dict(tag_types)}, ensure_ascii=False)
    return update or None


def inspect_row(connector, row: dict) -> tuple[dict, dict | None]:
    """问一条：帖子页，缺时长的视频再问一次文件头。返回 CSV 行和要写的列。

    帖子页没解析出来时 `result` 是「未取得」：限流时整页都是空的，不能当成「这条
    没有」写进去，留给下一趟再问。
    """
    post_id = row["external_id"]
    entry = {"item_id": row["id"], "external_id": post_id, "result": "",
             "published_before": row["published_at"] or "", "published_after": "",
             "duration": row["duration"] if row["duration"] is not None else "",
             "tag_types_added": "", "written": "否", "note": ""}
    if not post_id.isdigit():
        entry.update(result="跳过", note="external_id 不是帖子号")
        return entry, None
    try:
        detail = connector._detail(post_id)
    except (FollowSourceError, OSError) as error:
        detail, entry["note"] = {}, f"{type(error).__name__}: {error}"[:120]
    if not detail.get("tag_types"):
        entry.update(result="未取得", note=entry["note"] or "帖子页没有解析出细节")
        return entry, None
    duration = (connector._video_seconds(row["media_url"])
                if row["duration"] is None else None)
    update = plan_update(row, detail, duration) or {}
    entry.update(result="取得" if update else "无需改动",
                 published_after=detail.get("published_at") or "",
                 duration=update.get("duration", entry["duration"]),
                 tag_types_added="是" if "metadata_json" in update else "")
    if not detail.get("published_at"):
        entry["note"] = "帖子页没有 Posted"
    elif _is_mp4(row["media_url"]) and row["duration"] is None and not duration:
        entry["note"] = "时长未取得"
    return entry, update or None


def inspect_duration(connector, row: dict) -> tuple[dict, dict | None]:
    """只补时长：读一次原文件头，读到就只写 `duration`。

    读不到记「未取得」而不是写空：`moov` 在文件结尾、CDN 一时不通都会这样，
    留给下一趟再问，不冒充测过。
    """
    entry = {"item_id": row["id"], "external_id": row["external_id"], "result": "",
             "published_before": row["published_at"] or "", "published_after": "",
             "duration": "", "tag_types_added": "", "written": "否", "note": ""}
    seconds = connector._video_seconds(row["media_url"])
    if not seconds:
        entry.update(result="未取得", note="时长未取得")
        return entry, None
    entry.update(result="取得", duration=seconds)
    return entry, {"duration": seconds}


def _is_mp4(url: str | None) -> bool:
    return str(url or "").lower().split("?")[0].endswith(".mp4")


def _apply(writer: sqlite3.Connection, updates: list[tuple[int, dict]]) -> None:
    for item_id, update in updates:
        if "published_at" in update:
            writer.execute("UPDATE follow_item SET published_at=?, published_precision='exact'"
                           " WHERE id=?", (update["published_at"], item_id))
        if "duration" in update:
            writer.execute("UPDATE follow_item SET duration=? WHERE id=? AND duration IS NULL",
                           (update["duration"], item_id))
        if "metadata_json" in update:
            writer.execute("UPDATE follow_item SET metadata_json=? WHERE id=?",
                           (update["metadata_json"], item_id))
    writer.commit()


def run(args: argparse.Namespace) -> int:
    if args.apply and not args.backup:
        print(BACKUP_REQUIRED, file=sys.stderr)
        return 2
    done = written_ids(args.out) if args.resume else set()
    previous = [row for row in read_rows(args.out, missing_ok=True)
                if row.get("written") == "是"] if args.resume else []
    with open_readonly(args.db) as reader:
        rows = pending_rows(reader, args.limit, done, duration_only=args.duration_only)
        total = reader.execute(
            "SELECT COUNT(*) FROM follow_item i JOIN follow_source s ON s.id=i.source_id"
            " WHERE s.provider='rule34xxx'").fetchone()[0]
    print(f"rule34xxx 共 {total} 条，已写入 {len(done)} 条，本趟待问 {len(rows)} 条", flush=True)
    if not rows:
        return 0

    writer: sqlite3.Connection | None = None
    before = 0
    if args.apply:
        # 备份在任何写入之前由 `open_for_write` 落；分批提交，中断一次不至于整趟白跑。
        writer = open_for_write(args)
        # 服务的关注检查同时在写 follow_item；它的事务很短，等它提交而不是 5 秒就报锁。
        writer.execute("PRAGMA busy_timeout=60000")
        print(f"备份：{args.backup}", flush=True)
        before = writer.execute("SELECT COUNT(*) FROM follow_item").fetchone()[0]

    connector = build_connector("rule34xxx")
    log: list[dict] = []
    staged: list[tuple[int, dict]] = []
    staged_logs: list[dict] = []
    counts = {"published_at": 0, "duration": 0, "metadata_json": 0, "未取得": 0}
    miss_streak = 0
    next_detail = 0.0

    def flush() -> None:
        if writer is not None and staged:
            _apply(writer, staged)
            for entry in staged_logs:
                entry["written"] = "是"
        staged.clear()
        staged_logs.clear()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        write_rows(args.out, FIELDS, previous + log, atomic=True)

    for index, row in enumerate(rows, 1):
        # 间隔按帖子页请求的起点算：文件头在另一个主机上，问它的时间落在这段间隔里。
        time.sleep(max(0.0, next_detail - time.monotonic()))
        next_detail = time.monotonic() + args.delay
        entry, update = (inspect_duration if args.duration_only else inspect_row)(connector, row)
        log.append(entry)
        if entry["result"] == "未取得":
            counts["未取得"] += 1
            miss_streak += 1
            if miss_streak >= MISS_STREAK_LIMIT:
                print(f"连续 {miss_streak} 条取不到，按限流处理，静默 "
                      f"{COOLDOWN_SECONDS:.0f} 秒", flush=True)
                flush()
                time.sleep(COOLDOWN_SECONDS)
                miss_streak = 0
            continue
        miss_streak = 0
        if update:
            for key in update:
                counts[key] += 1
            staged.append((row["id"], update))
            staged_logs.append(entry)
        if index % FLUSH_EVERY == 0:
            flush()
            print(f"{index}/{len(rows)}：上传时间 {counts['published_at']}，时长 "
                  f"{counts['duration']}，类型 {counts['metadata_json']}，未取得 "
                  f"{counts['未取得']}", flush=True)
    flush()
    summary = (f"上传时间改正 {counts['published_at']}，补时长 {counts['duration']}，"
               f"补类型 {counts['metadata_json']}，未取得 {counts['未取得']} → {args.out}")
    if writer is None:
        print(f"dry-run：{summary}；加 --apply --backup 才真正写入", flush=True)
        return 0
    after = writer.execute("SELECT COUNT(*) FROM follow_item").fetchone()[0]
    writer.close()
    # 条数必须不变：这一趟只补已有条目的列，不新增也不删除条目。
    print(f"已写入：{summary}；follow_item {before} → {after}", flush=True)
    return 0 if before == after else 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
