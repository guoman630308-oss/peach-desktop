#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""给已入库的关注图片补上固有宽高。

图片墙靠 `<img width height>` 在图落地前占好比例；没有这两个数的卡片按 1:1
占位，图一到就整墙重排。只有图片视图摆成瀑布流，所以只补图片卡面那一张。尺寸只有
fanbox 一家一直在记（imageMap 自带），其余来源的存量行都是空的。这一趟分两路补：

- **归档**：rule34.xxx 的 dapi 响应本来就带 `width`/`height`，每次检查更新的
  原始响应都归档在 `sources/follow/rule34xxx/` 下。不用再发一个请求，把归档里
  每个帖子的尺寸按帖子号对回条目即可。
- **探测**：归档站（kemono、coomer、pawchive）、paheal、论坛附件这些来源不报
  尺寸，只能问文件本身——但只问文件头：`Range: bytes=0-65535`，PNG/GIF/WebP/
  AVIF 几十到几百字节就够，JPEG 的 SOF 段在 EXIF 之后，64 KiB 覆盖绝大多数。
  先问卡片实际显示的公开缩略图（归档站的原文件主机对脚本直接 403），再按界面
  同一条 `FollowMediaResolver` 拿媒体地址与请求头，凭据与 Referer 同一口径。
  每个主机各自限速。

判据只有 `peach.follow_image_dims` 一份；落库走 `FollowStore.set_image_dims`，
只补空缺，条数不增不减。默认 dry-run。`--apply` 必须同时给 `--backup`，与本仓库
其它真实写入脚本一致。可反复运行：已经有尺寸的条目直接跳过，中断后接着跑就是。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR, SECRETS_DIR, SHARED_CREDENTIAL_ROOT, SOURCES_DIR
from peach.follow_image_dims import ImageDimsUnavailable, positive_dims, probe_image_dims
from peach.follow_secrets import credential_store_for
from peach.follow_sources import display_thumb_url, f95_attachment_media_items
from peach.follow_store import FollowItemRow, FollowStore
from peach.follow_stream import FollowMediaResolver, FollowMediaUnavailable
from peach.http import HttpTransport, HttpxTransport
from peach.review_csv import write_rows
from peach.scripting import (
    BACKUP_REQUIRED, HostLimiter, add_ledger_write_args, open_for_write, open_readonly,
)
# 「这条是不是图片」与「卡面是清单里哪一张」都与界面同一判据：卡片按卡面那张图预留
# 比例，这里就按同一张去问尺寸。
from peach.web_follow import _media_items as visible_media_of, _media_kind as media_kind_of

FIELDS = ("item_id", "provider", "external_id", "media", "mode", "result",
          "width", "height", "note")

#: 同一主机两次探测的最小间隔。归档站对匿名请求限流很紧，一秒两次已经算快。
DEFAULT_DELAY = 0.5
#: 单次探测的超时。只取文件头，等得久多半是被挂起，不值得等。
PROBE_TIMEOUT = 15.0
#: 每写这么多条提交一次并落一次 CSV，中断也不白跑。
FLUSH_EVERY = 50


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="补齐关注图片的固有宽高")
    add_ledger_write_args(parser)
    parser.add_argument("--out", type=Path,
                        default=GENERATED_DIR / "follow-image-dims-backfill.csv")
    parser.add_argument("--archives", type=Path, default=SOURCES_DIR / "follow",
                        help="原始响应归档根目录（各来源一个子目录）")
    parser.add_argument("--mode", choices=("all", "archives", "probe"), default="all",
                        help="archives 只对归档、probe 只发请求，默认两路都走")
    parser.add_argument("--provider", action="append", default=[],
                        help="只处理这些来源，可重复给")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 张，0 为全部")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    return parser


def archived_dims(root: Path) -> dict[str, tuple[int, int]]:
    """rule34.xxx 归档响应里每个帖子的宽高，按帖子号索引。

    同一帖子在多次响应里都出现时任取一份：尺寸是文件属性，不随时间变。认不出的
    文件（非 JSON、不是列表）跳过，不报错——归档目录里还有 `.json` 元数据。
    """
    dims: dict[str, tuple[int, int]] = {}
    directory = root / "rule34xxx"
    if not directory.is_dir():
        return dims
    for path in sorted(directory.rglob("*.raw")):
        try:
            posts = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if not isinstance(posts, list):
            continue
        for post in posts:
            if not isinstance(post, dict) or post.get("id") is None:
                continue
            pair = positive_dims(post.get("width"), post.get("height"))
            if pair is not None:
                dims.setdefault(str(post["id"]), pair)
    return dims


def pending_targets(items: tuple[FollowItemRow, ...],
                    providers: set[str]) -> list[tuple[FollowItemRow, int | None, dict | None]]:
    """待补的（条目，媒体序号，媒体）。序号 None 是条目级那张直链图。

    瀑布流只按卡面那张占位，一个条目最多出一条：有媒体清单的条目问可见清单里的第一张
    图（界面卡面取的就是它，隐藏的不算），其余几张只在详情里翻，不需要比例；F95 的
    附件清单是读时合成的，不在 metadata 里，尺寸没有地方落，跳过。没有清单的条目
    只问图片，视频不进图片墙。
    """
    targets: list[tuple[FollowItemRow, int | None, dict | None]] = []
    for item in items:
        if providers and item.provider not in providers:
            continue
        media_items = item.metadata.get("media_items")
        if isinstance(media_items, list) and media_items:
            index = next((media["index"] for media in visible_media_of(item)
                          if media["media_kind"] == "image"), None)
            if index is not None and positive_dims(
                    media_items[index].get("width"), media_items[index].get("height")) is None:
                targets.append((item, index, media_items[index]))
            continue
        if item.provider == "f95zone" and f95_attachment_media_items(item.metadata):
            continue
        if media_kind_of(item) != "image":
            continue
        if positive_dims(item.metadata.get("width"), item.metadata.get("height")) is None:
            targets.append((item, None, None))
    return targets


def probe_attempts(resolver: FollowMediaResolver, item: FollowItemRow, index: int | None,
                   media: dict | None) -> tuple[list[tuple[str, dict, str]], list[str]]:
    """一张图按顺序可以去问的地址：（地址，请求头，来路），以及排不上的原因。

    先问卡片实际显示的那张——公开缩略图，不带请求头，浏览器也是这么取的，它的
    比例才是排版要的那一个；归档站的原文件主机对脚本请求直接 403（pawchive 回的
    是一句「stop before I block」），缩略图主机照常给。缩略图不行再按界面同一条
    解析器拿媒体地址与请求头（凭据、Referer 都在里面）。
    """
    attempts: list[tuple[str, dict, str]] = []
    notes: list[str] = []
    thumb = str((media or {}).get("thumb_url") or "") if media is not None \
        else str(display_thumb_url(item) or "")
    if thumb.startswith("https://"):
        attempts.append((thumb, {}, "缩略图"))
    try:
        resolved = resolver.resolve(item, index)
    except FollowMediaUnavailable as error:
        notes.append(f"解析器拒收：{error}")
    else:
        headers = dict(resolved.headers or {})
        if resolved.referer:
            headers.setdefault("Referer", resolved.referer)
        if resolved.url != thumb:
            attempts.append((resolved.url, headers, "媒体地址"))
    return attempts, notes


def probe_target(resolver: FollowMediaResolver, transport: HttpTransport,
                 limiter: HostLimiter, item: FollowItemRow, index: int | None,
                 media: dict | None) -> tuple[tuple[int, int] | None, str]:
    """问一张图的尺寸。返回（尺寸或 None，说明）；每条地址各限速一次。"""
    attempts, notes = probe_attempts(resolver, item, index, media)
    for url, headers, via in attempts:
        limiter.wait(url)
        try:
            return probe_image_dims(transport, url, headers, timeout=PROBE_TIMEOUT), via
        except (ImageDimsUnavailable, OSError, httpx.HTTPError) as error:
            notes.append(f"{via}：{type(error).__name__}: {error}"[:120])
    return None, "；".join(notes) or "没有可问的地址"


def run(args: argparse.Namespace, transport: HttpTransport | None = None) -> int:
    if args.apply and not args.backup:
        print(BACKUP_REQUIRED, file=sys.stderr)
        return 2
    providers = set(args.provider)
    reader = open_readonly(args.db)
    try:
        items = FollowStore(lambda: reader).items(limit=10_000_000)
    finally:
        reader.close()
    targets = pending_targets(items, providers)
    if args.limit:
        targets = targets[:args.limit]
    print(f"关注条目 {len(items)} 条，待补尺寸 {len(targets)} 张", flush=True)
    if not targets:
        return 0

    archive = archived_dims(args.archives) if args.mode in ("all", "archives") else {}
    if args.mode in ("all", "archives"):
        print(f"归档里有尺寸的 rule34.xxx 帖子 {len(archive)} 个", flush=True)

    writer: sqlite3.Connection | None = None
    store: FollowStore | None = None
    before = after = 0
    if args.apply:
        writer = open_for_write(args)
        print(f"备份：{args.backup}", flush=True)
        before = writer.execute("SELECT COUNT(*) FROM follow_item").fetchone()[0]
        store = FollowStore(lambda: writer)

    resolver: FollowMediaResolver | None = None
    limiter = HostLimiter({}, default_interval=args.delay)
    if args.mode in ("all", "probe"):
        transport = transport or HttpxTransport()
        resolver = FollowMediaResolver(transport).with_credential_loader(
            lambda provider: credential_store_for(
                SECRETS_DIR, shared_root=SHARED_CREDENTIAL_ROOT).load(provider))

    log: list[dict] = []
    found = written = 0
    since_flush = 0

    def flush() -> None:
        nonlocal since_flush
        if writer is not None:
            writer.commit()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        write_rows(args.out, FIELDS, log, atomic=True)
        since_flush = 0

    try:
        for position, (item, index, media) in enumerate(targets, 1):
            row = {"item_id": item.id, "provider": item.provider,
                   "external_id": item.external_id,
                   "media": "" if index is None else index,
                   "mode": "", "result": "未取得", "width": "", "height": "", "note": ""}
            dims: tuple[int, int] | None = None
            if index is None and item.provider == "rule34xxx" and archive:
                dims = archive.get(str(item.external_id))
                row["mode"] = "归档"
                if dims is None:
                    row["note"] = "归档里没有这个帖子"
            if dims is None and resolver is not None:
                dims, note = probe_target(resolver, transport, limiter, item, index, media)
                row["mode"] = "探测"
                row["note"] = note if dims is None else note
            if dims is not None:
                found += 1
                row["result"] = "取得"
                row["width"], row["height"] = dims
                if store is not None:
                    if store.set_image_dims(item.id, *dims, media_index=index):
                        written += 1
                        since_flush += 1
                    else:
                        row["note"] = "已有尺寸，未改动"
            log.append(row)
            if since_flush >= FLUSH_EVERY or position % 200 == 0:
                flush()
                print(f"{position}/{len(targets)}：已取得 {found}，已写入 {written}", flush=True)
    finally:
        flush()

    print(f"取得 {found}/{len(targets)} 张 → {args.out}", flush=True)
    if writer is None:
        print(f"dry-run：可写入 {found} 张；加 --apply --backup 才真正写入")
        return 0
    after = writer.execute("SELECT COUNT(*) FROM follow_item").fetchone()[0]
    writer.close()
    # 条数必须不变：这一趟只补 metadata 里的两个数，不新增也不删除条目。
    print(f"已写入 {written} 张；follow_item {before} → {after}")
    return 0 if before == after else 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
