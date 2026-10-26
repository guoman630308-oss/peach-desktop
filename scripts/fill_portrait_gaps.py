#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""给缺头像的女优补上图库里那一张，认不准的摆成对照表。

没装头像的人，资料页那张圆图是从她某部片里抽的一帧（`/entity-image` 取不到就退
`/avatar`），而作品页的出镜者那一格连这条都没有，直接是首字母。同一个人在两处长着
两副样子，两副都不是她的脸。

装图这一步一直是手工的：`audit_performer_portraits.py` 只产候选与实测证据，资料页的
挑图弹层一次换一个人。缺的是中间那一段——把**认得准的**批量装上，把认不准的摆到
一起让人一眼挑。本脚本就是那一段。

**认得准 = 图库里按她整条名字链只找出一张。** 名字链带别名，因为大陆简体与日文字体在
图库里是两个不同的键。

认不准的那些产出一份对照表（`--sheet`）：候选图并排摆着，旁边是她在这个库里的作品
链接。对着片子认人是唯一靠得住的判据，而这件事机器做不了。

装图复用挑图弹层那三步（`peach.avatar_picker` 的 `choices`／`resolve`／`install`），
所以证据、缓存与取景 sidecar 的口径和手工换图完全一致。默认 dry-run，`--apply` 才真装。
不碰已经有头像的人，也不写 ledger。
"""
from __future__ import annotations

import argparse
import html
import sqlite3
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach import avatar_picker   # noqa: E402
from peach.avatar_provider import (   # noqa: E402
    MIN_LONG_SIDE, MIN_SHORT_SIDE, acceptable_avatar)
from peach.config import (DATABASE_PATH, GENERATED_DIR, SERVE_PORT,   # noqa: E402
                          STATE_DIR)
from peach.jobs import job_main   # noqa: E402
from peach.previews import entity_image_key   # noqa: E402
from peach.review_csv import write_rows   # noqa: E402
from peach.scripting import open_readonly   # noqa: E402

INSTALL, MANY, NONE, SMALL, FAILED = "装上", "多张（认不准）", "无候选", "图太小", "取图失败"

FIELDS = ("entity_id", "name", "assets", "candidates", "action", "file", "size",
          "profile_url", "detail")


def targets(connection: sqlite3.Connection, avatar_root: Path) -> list[dict]:
    """没装头像的 performer，按作品多的在前。

    作品数决定这一位值不值得先看：对照表要人一个个认，排在前面的该是她在这个库里
    真的出现过好几次的那些。
    """
    connection.row_factory = sqlite3.Row
    out = []
    for row in connection.execute(
        "SELECT e.id,e.canonical_name,"
        " (SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae WHERE ae.entity_id=e.id) n"
        " FROM entity e WHERE e.kind='performer' ORDER BY e.id"
    ):
        entity_id = int(row["id"])
        if (avatar_root / f"{entity_image_key('performer', entity_id)}.img").exists():
            continue
        out.append({"entity_id": entity_id, "name": str(row["canonical_name"]),
                    "assets": int(row["n"])})
    out.sort(key=lambda record: (-record["assets"], record["entity_id"]))
    return out


def gallery_choices(connection: sqlite3.Connection, providers_root: Path,
                    avatar_root: Path, entity_id: int) -> list[dict]:
    """这个人在图库里的候选。取过的历史图不算——这一轮补的是从没装过图的人。"""
    listing = avatar_picker.choices(connection, providers_root, avatar_root,
                                    "performer", entity_id)
    return [choice for choice in listing["choices"] if choice["source"] == "gfriends"]


def works(connection: sqlite3.Connection, entity_id: int, limit: int = 12) -> list[dict]:
    """她在这个库里的作品，新的在前。对照表上只给链接，认人要点开看。"""
    rows = connection.execute(
        "SELECT a.id,a.catalog_title,a.code,a.release_date FROM asset a"
        " JOIN asset_entity ae ON ae.asset_id=a.id WHERE ae.entity_id=?"
        " GROUP BY a.id ORDER BY COALESCE(a.release_date,'') DESC, a.id DESC LIMIT ?",
        (int(entity_id), int(limit))).fetchall()
    return [{"id": int(row["id"]),
             "label": str(row["code"] or row["catalog_title"] or f"作品 {row['id']}"),
             "date": str(row["release_date"] or "")} for row in rows]


def profile_url(base_url: str, name: str) -> str:
    return f"{base_url.rstrip('/')}/performers/{urllib.parse.quote(name, safe='')}"


def item_url(base_url: str, asset_id: int) -> str:
    return f"{base_url.rstrip('/')}/assets/{int(asset_id)}"


def sheet_html(rows: list[dict], base_url: str) -> str:
    """对照表。一个人一段：候选图并排，旁边是她的作品链接。

    图用相对路径引本地文件，不引图库地址——这一页是离线看的，而且图库那边的地址
    随索引改版会变，存下来的证据不该跟着失效。
    """
    blocks = []
    for row in rows:
        shots = "".join(
            f'<figure><img src="{html.escape(item["file"])}" alt="" loading="lazy">'
            f'<figcaption>{html.escape(item["label"])}'
            f'<span>{item["width"]}×{item["height"]}</span></figcaption></figure>'
            for item in row["candidates"])
        links = "".join(
            f'<li><a href="{html.escape(item_url(base_url, work["id"]))}">'
            f'{html.escape(work["label"])}</a>'
            + (f' <span>{html.escape(work["date"])}</span>' if work["date"] else "")
            + "</li>"
            for work in row["works"])
        blocks.append(
            f'<section><h2><a href="{html.escape(profile_url(base_url, row["name"]))}">'
            f'{html.escape(row["name"])}</a>'
            f'<small>{row["assets"]} 部 · {len(row["candidates"])} 张候选</small></h2>'
            f'<div class="shots">{shots}</div><ol class="works">{links}</ol></section>')
    return ("<!doctype html><meta charset=\"utf-8\"><title>头像候选对照</title>"
            "<style>body{font:15px/1.6 system-ui,sans-serif;margin:24px;max-width:1100px}"
            "h1{font-size:20px}section{border-top:1px solid #ddd;padding:16px 0}"
            "h2{font-size:17px;display:flex;gap:10px;align-items:baseline}"
            "h2 small{color:#666;font-weight:400}"
            ".shots{display:flex;flex-wrap:wrap;gap:12px}"
            "figure{margin:0;width:150px}img{width:150px;height:150px;object-fit:cover;"
            "border-radius:8px;background:#f2f2f2}"
            "figcaption{font-size:12px;color:#555;display:flex;justify-content:space-between;"
            "gap:6px}.works{margin:10px 0 0;padding-left:20px;columns:2;font-size:13px}"
            ".works span{color:#888}</style>"
            f"<h1>头像候选对照 · {len(blocks)} 人</h1>"
            "<p>图库按名字存图，同名的是不同的人。对着作品认出是谁，再去资料页换图。</p>"
            + "".join(blocks))


def run(args: argparse.Namespace) -> int:
    from peach.http import HttpxTransport

    providers_root = args.providers_root
    avatar_root = args.avatar_root
    sheet_dir = args.sheet
    shots_dir = sheet_dir / "candidates"
    transport = HttpxTransport()
    # 这一趟只往盘上写头像文件，账本一个字不改：连接按只读开，让它成为数据库层的保证。
    connection = open_readonly(args.db)
    rows: list[dict] = []
    sheets: list[dict] = []
    try:
        records = targets(connection, avatar_root)
        for record in records[:args.limit] if args.limit else records:
            entity_id, name = record["entity_id"], record["name"]
            found = gallery_choices(connection, providers_root, avatar_root, entity_id)
            base = {"entity_id": entity_id, "name": name, "assets": record["assets"],
                    "candidates": len(found),
                    "profile_url": profile_url(args.base_url, name)}
            if not found:
                rows.append({**base, "action": NONE, "detail": "图库里没有这个名字"})
                continue
            if len(found) == 1:
                rows.append(install_one(connection, transport, providers_root,
                                        avatar_root, base, found[0], args))
                continue
            shots = []
            for choice in found:
                shot = save_candidate(connection, transport, providers_root,
                                      shots_dir, entity_id, choice)
                if shot:
                    shots.append(shot)
            sheets.append({**base, "candidates": shots,
                           "works": works(connection, entity_id)})
            rows.append({**base, "action": MANY,
                         "detail": "；".join(choice["detail"] for choice in found)})
    finally:
        connection.close()
        close = getattr(transport, "close", None)
        if close:
            close()
    write_rows(args.out, FIELDS, rows, fill_missing=True)
    if sheets:
        sheet_dir.mkdir(parents=True, exist_ok=True)
        (sheet_dir / "index.html").write_text(sheet_html(sheets, args.base_url),
                                              encoding="utf-8")
    counts = Counter(str(row["action"]) for row in rows)
    print(f"缺头像 {len(rows)} 人；复核 CSV：{args.out}")
    print("  动作分布：", dict(counts))
    if sheets:
        print(f"  对照表：{sheet_dir / 'index.html'}（{len(sheets)} 人）")
    if not args.apply:
        print("  未装图（加 --apply 才装）")
    return 0


def install_one(connection, transport, providers_root: Path, avatar_root: Path,
                base: dict, choice: dict, args: argparse.Namespace) -> dict:
    """图库里只找出这一张，装上去。"""
    try:
        body, origin = avatar_picker.resolve(choice["ref"], connection, providers_root,
                                             base["entity_id"], transport)
        inspected = avatar_picker.accept_image(body)
    except avatar_picker.PickerError as error:
        return {**base, "action": FAILED, "file": choice["detail"], "detail": str(error)}
    size = f"{inspected.width}×{inspected.height}"
    if not acceptable_avatar(inspected, args.min_long_side, args.min_short_side):
        return {**base, "action": SMALL, "file": choice["detail"], "size": size,
                "detail": f"低于 {args.min_long_side}×{args.min_short_side} 那一档"}
    if args.apply:
        avatar_picker.install(providers_root, avatar_root, "performer",
                              base["entity_id"], body, origin)
    return {**base, "action": INSTALL, "file": choice["detail"], "size": size,
            "detail": f"图库 {choice['label']}" + ("" if args.apply else "（未装）")}


def save_candidate(connection, transport, providers_root: Path, shots_dir: Path,
                   entity_id: int, choice: dict) -> dict | None:
    """把一张候选存到对照表旁边。取不到就这一格不出现，不中断整批。"""
    try:
        body, _ = avatar_picker.resolve(choice["ref"], connection, providers_root,
                                        entity_id, transport)
        inspected = avatar_picker.accept_image(body)
    except avatar_picker.PickerError:
        return None
    shots_dir.mkdir(parents=True, exist_ok=True)
    name = f"{entity_id}-{inspected.sha256[:12]}{inspected.extension}"
    (shots_dir / name).write_bytes(body)
    return {"file": f"candidates/{name}", "label": choice["detail"],
            "width": inspected.width, "height": inspected.height}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="补图库里认得准的女优头像，其余产对照表")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--avatar-root", type=Path, default=GENERATED_DIR / "avatars")
    parser.add_argument("--providers-root", type=Path,
                        default=GENERATED_DIR / "provider-cache" / "performer-avatars")
    parser.add_argument("--out", type=Path,
                        default=GENERATED_DIR / "portrait-gaps.csv")
    parser.add_argument("--sheet", type=Path, required=True,
                        help="对照表目录，候选图存在它下面的 candidates/")
    parser.add_argument("--base-url", default=f"http://localhost:{SERVE_PORT}",
                        help="对照表里链接指向的实例")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-long-side", type=int, default=MIN_LONG_SIDE)
    parser.add_argument("--min-short-side", type=int, default=MIN_SHORT_SIDE)
    parser.add_argument("--apply", action="store_true", help="真的装图；默认只看不装")
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".portrait-gaps.lock")
    return parser


if __name__ == "__main__":
    raise SystemExit(job_main(build_parser, run))
