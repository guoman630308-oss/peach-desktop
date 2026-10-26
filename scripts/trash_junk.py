#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把广告候选移进回收站，或按逐文件复核清单永久删除。

候选来自 `peach.web_batch.q_ads`，与垃圾复核页是同一套评分与判据；这里只按分数
与来源过滤，然后写 `disposal='trash'`——文件不动、账本行保留，清空回收站才真正
删除，误判可以随时从回收站恢复。默认 dry-run 只出清单，`--apply` 必须同时给
`--backup`。

`--purge --review-csv` 只处理清单里 decision=delete 的确认项。每项须带路径、体积、
证据与至少 0.95 的确认置信度；执行时核对文件未变化、来源在线且路径在根内，保护字幕与 NFO。
以下几类整条跳过、不删，并在结果的 `skipped` 里逐条回报：已不在垃圾复核队列里的行、
用户点过「不是垃圾」的行、带个人记录（播放、评分、偏好、稍后看、播放列表等，判据同
`personal_records.record_holders`）的行，以及登记了字幕或盘上有同名字幕、NFO 的行。
删除只走 `purge_assets()`。

用法:
    python scripts/trash_junk.py --min-score 60
    python scripts/trash_junk.py --min-score 60 --apply --backup <落点>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, GENERATED_DIR, LOCATION_ROOT_DECLARATIONS
from peach.media_sources import SCANNABLE_LOCATIONS
from peach.personal_records import record_holders
from peach.platform import root_online, translate_ledger_path, within_root
from peach.review_csv import read_rows, write_rows
from peach.scripting import (
    add_ledger_write_args, counts_of, open_for_write, open_readonly, verify_after_write,
)
from peach.web_batch import (
    AUTO_TRASH_SCORE, MEDIA_SIDECAR_SUFFIXES, q_ads, purge_assets, _finish_purge,
    _restore_staged_media,
)
from peach.web_contract import WebContract

FIELDS = ["id", "score", "location", "medium", "size_mb", "name", "why", "path"]
TRASHABLE_LOCATIONS = SCANNABLE_LOCATIONS


def load_review(path: Path) -> list[dict]:
    """只接受逐文件确认的删除清单，评分不构成永久删除依据。"""
    rows = read_rows(path)
    selected = [row for row in rows if row.get("decision") == "delete"]
    seen = set()
    for row in selected:
        asset_id = int(row["id"])
        if asset_id in seen or not row.get("path") or not row.get("why"):
            raise ValueError("删除清单缺少路径、证据或包含重复资产")
        confidence = float(row.get("confidence") or 0)
        if not math.isfinite(confidence) or not .95 <= confidence <= 1:
            raise ValueError("删除清单的确认置信度不足 0.95")
        if int(row["size"]) < 0:
            raise ValueError("删除清单的文件体积不能为负")
        seen.add(asset_id)
    return selected


def candidate_ids(db_path: Path) -> set[int]:
    """当前仍在垃圾复核队列（未被标「不是垃圾」）里的资产 id，只读。"""
    result = q_ads(WebContract(Path(db_path)), limit=1_000_000)
    return {int(item["id"]) for item in result["items"]}


def sidecar_files(path: Path) -> list[str]:
    """盘上与该文件同名的字幕与 NFO（`片名.srt`、`片名.zh.ass`、`片名.nfo` 等）。"""
    prefix = path.stem.casefold() + "."
    return sorted(entry.name for entry in path.parent.iterdir()
                  if entry != path and entry.name.casefold().startswith(prefix)
                  and entry.suffix.casefold() in MEDIA_SIDECAR_SUFFIXES)


def skip_reason(connection, row, path: Path, candidates: set[int], holders: set[int]) -> str:
    """不能永久删除的原因；空串表示可以删。"""
    if connection.execute(
            "SELECT 1 FROM review_decision WHERE category='junk_file' AND status='rejected' "
            "AND item_key=?", (str(row["id"]),)).fetchone():
        return "用户已标为不是垃圾"
    if int(row["id"]) not in candidates:
        return "已不在垃圾复核队列里"
    if int(row["id"]) in holders:
        return "带个人记录"
    if connection.execute("SELECT 1 FROM asset_subtitle WHERE asset_id=?", (row["id"],)).fetchone():
        return "登记了字幕"
    sidecars = sidecar_files(path)
    if sidecars:
        return "盘上有同名字幕或资料：" + "、".join(sidecars)
    return ""


def purge_reviewed(connection, reviews: list[dict], candidates: set[int]) -> dict:
    """核对清单与文件后，复用批量永久删除的隔离、提交与清退流程。

    `candidates` 是删除前只读算出的垃圾复核队列 id；不在其中的行跳过。
    """
    baseline = {tuple(row) for row in connection.execute("PRAGMA foreign_key_check")}
    outcome = None
    skipped = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        holders = record_holders(connection, [int(review["id"]) for review in reviews])
        rows = []
        for review in reviews:
            row = connection.execute("SELECT * FROM asset WHERE id=?", (int(review["id"]),)).fetchone()
            if row is None or row["location"] not in TRASHABLE_LOCATIONS or row["path"] != review["path"]:
                raise ValueError("删除清单已失效：资产来源或路径不一致")
            path = translate_ledger_path(row["path"])
            roots = [translate_ledger_path(root) for root in LOCATION_ROOT_DECLARATIONS.get(row["location"], ())]
            if not any(root_online(root) and within_root(path, root) for root in roots):
                raise ValueError("删除路径不在已挂载的来源根内")
            if path.suffix.casefold() in MEDIA_SIDECAR_SUFFIXES:
                raise ValueError("媒体资料与字幕不能进入垃圾永久删除")
            if path.is_symlink() or not path.is_file() or path.stat().st_size != int(review["size"]):
                raise ValueError("删除清单已失效：文件不存在、类型或体积变化")
            reason = skip_reason(connection, row, path, candidates, holders)
            if reason:
                skipped.append({"id": row["id"], "path": row["path"], "reason": reason})
                continue
            rows.append(row)
        outcome = purge_assets(connection, rows)
        integrity, _ = verify_after_write(connection)
        violations = {tuple(row) for row in connection.execute("PRAGMA foreign_key_check")}
        if integrity != "ok" or violations - baseline:
            raise RuntimeError("删除后账本校验失败")
        connection.commit()
    except BaseException:
        connection.rollback()
        if outcome is not None:
            _restore_staged_media(outcome["_staged"])
        raise
    return {**_finish_purge(outcome), "skipped": skipped, "existing_foreign_keys": len(baseline)}


def select_candidates(db_path: Path, *, min_score: int, locations: tuple[str, ...] = (),
                      kind: str = "") -> list[dict]:
    """只读跑一遍垃圾判据，按分数与来源过滤，并补上路径供人核对。"""
    contract = WebContract(Path(db_path))
    result = q_ads(contract, limit=100000, kind=kind)
    items = [item for item in result["items"] if int(item["score"]) >= min_score]
    if locations:
        items = [item for item in items if item["location"] in locations]
    connection = open_readonly(db_path)
    try:
        paths = {row["id"]: row["path"] for row in connection.execute("SELECT id,path FROM asset")}
    finally:
        connection.close()
    for item in items:
        item["path"] = paths.get(item["id"], "")
    return items


def trash_assets(connection, asset_ids: list[int]) -> int:
    """与复核页「移入回收站」同义：只改 `disposal`，不动文件与引用关系。"""
    ids = list(dict.fromkeys(int(value) for value in asset_ids))
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    sources = ",".join("?" * len(TRASHABLE_LOCATIONS))
    cursor = connection.execute(
        f"UPDATE asset SET disposal='trash', disposal_reason='junk', feedback_at=? WHERE id IN ({marks}) "
        f"AND disposal IS NULL AND location IN ({sources})",
        [time.time(), *ids, *TRASHABLE_LOCATIONS],
    )
    connection.commit()
    return cursor.rowcount


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把高置信广告残留移入回收站")
    add_ledger_write_args(parser, db_default=DATABASE_PATH)
    parser.add_argument("--min-score", type=int, default=AUTO_TRASH_SCORE,
                        help=f"垃圾评分下限；默认 {AUTO_TRASH_SCORE}（整个名字都是推广语一档，"
                             "固定命名与未探测时长视频的目录证据单独到不了）")
    parser.add_argument("--location", action="append", choices=TRASHABLE_LOCATIONS)
    parser.add_argument("--kind", choices=("video", "image", "audio", "archive", "url", "other"),
                        default="")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--review-csv", type=Path, help="含 id、path、size、decision、confidence、why 的逐文件复核清单")
    parser.add_argument("--purge", action="store_true", help="永久删除清单中确认的垃圾，必须给 --review-csv；默认仍只出计划")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.purge:
        if not args.review_csv:
            raise SystemExit("--purge 必须同时给 --review-csv")
        reviewed = load_review(args.review_csv)
        if not args.apply:
            print(f"清单确认删除 {len(reviewed)} 条；未加 --apply，只核对清单。")
            return 0
        candidates = candidate_ids(args.db)
        connection = open_for_write(args)
        try:
            result = purge_reviewed(connection, reviewed, candidates)
        finally:
            connection.close()
        report = args.out or args.review_csv.with_suffix(".result.json")
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["blocked"] or result["cleanup_pending"] or result["skipped"] else 0
    locations = tuple(args.location or ())
    selected = select_candidates(args.db, min_score=args.min_score,
                                 locations=locations, kind=args.kind)
    output = args.out or GENERATED_DIR / time.strftime("junk-trash-%Y%m%d-%H%M%S.csv")
    rows = [{field: item.get(field, "") for field in FIELDS} for item in selected]
    for row, item in zip(rows, selected):
        row["size_mb"] = round((item.get("size") or 0) / 1048576, 1)
    write_rows(output, FIELDS, rows, atomic=True)

    total_gb = sum((item.get("size") or 0) for item in selected) / 1024 ** 3
    counts: dict[str, int] = {}
    for item in selected:
        counts[item["location"]] = counts.get(item["location"], 0) + 1
    print(f"评分≥{args.min_score} 的候选 {len(selected)} 条 / {total_gb:.2f} GB，"
          f"按来源 {counts or '无'} → {output}")
    for item in selected[:15]:
        print(f"  {item['score']:>3} {item['location']:<6} {item['medium']:<6} "
              f"{(item.get('size') or 0)/1048576:>9.1f}MB {str(item.get('name'))[:44]!r}"
              f" | {item.get('why')}")
    if len(selected) > 15:
        print(f"  …… 其余 {len(selected) - 15} 条见 CSV")

    if not args.apply:
        print("未加 --apply，只出清单；文件仍在原处。")
        return 0

    connection = open_for_write(args)
    try:
        before = counts_of(connection, {
            "trash": "SELECT count(*) FROM asset WHERE disposal='trash'",
        })
        changed = trash_assets(connection, [int(item["id"]) for item in selected])
        after = counts_of(connection, {
            "trash": "SELECT count(*) FROM asset WHERE disposal='trash'",
        })
        integrity, foreign_keys = verify_after_write(connection)
    finally:
        connection.close()
    if integrity != "ok" or foreign_keys:
        raise RuntimeError(f"写入后 ledger 校验失败：integrity={integrity} foreign_keys={foreign_keys}")
    print(f"已移入回收站 {changed} 条；回收站 {before['trash']} → {after['trash']}。"
          f"备份 {args.backup}；文件未删除，清空回收站才会真正删除。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
