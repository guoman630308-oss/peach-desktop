#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
创作者级视觉标签 —— 把人工读风格板得出的结论写进 ledger。

标签来自 scripts/creator_boards.py 生成的 3x3 风格板，每张板横跨该创作者 9 个
不同视频，所以结论描述的是"这位创作者的稳定风格"，不是某一条视频的确证内容。
因此写入用 source='vision_creator'、confidence=0.6，与逐条确证的 vision(0.9)、
番号刮削的 r18(0.9) 区分开，下游可以按置信度取舍。

只给该创作者名下当前完全没有标签的视频补，不覆盖任何已有标注。
标签一律取自 ledger 现有词表，不新造词。

用法:
    python scripts/creator_tags.py --export-review
    # Claude/人工先写 candidate；审核后才把行改为 approved。
    python scripts/creator_tags.py --apply-review --backup <backup.db>
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.scripting import open_readonly
from peach.classification import is_probable_mainstream_release, is_structural_creator
from peach.entities import upsert_asset_entity
from peach.migrations import sqlite_backup
from peach.review_csv import read_rows, write_rows

DB = DATABASE_PATH
BOARD_DIR = GENERATED_DIR / "boards"
REVIEW_CSV = GENERATED_DIR / "creator-tags-review.csv"
SOURCE = "vision_creator"
CONFIDENCE = 0.6
REVIEW_FIELDS = ["board", "creator", "video_count", "status", "tags", "reason"]

# 创作者 -> 标签。读风格板得出，只记反复出现的稳定特征。
BOARDS: dict[str, list[str]] = {
    "Retsu_dao":    ["素人", "制服", "情趣内衣", "口交", "酒店", "多人"],
    "luckydog22":   ["美臀", "后入", "主观视角", "酒店", "素人", "骑乘"],
    "捅主任":        ["酒店", "素人", "制服", "丝袜", "探花", "角色扮演", "高跟"],
    "gattouz0":     ["素人", "无码", "口交", "骑乘", "主观视角", "美臀"],
    "SexySaffron":  ["眼镜", "自慰", "网红主播", "无码", "露脸", "丝袜", "手交"],
    "ruth_lee":     ["口交", "主观视角", "美臀", "骑乘", "自慰", "无码", "露脸"],
    "pandor_a":     ["自慰", "无码", "网红主播", "情趣内衣", "素人"],
    "luckydog11":   ["丝袜", "酒店", "后入", "美臀", "素人", "主观视角"],
    "oscarkim123":  ["足交", "美腿"],
    "Shinaryen":    ["素人", "无码", "主观视角", "骑乘", "美臀", "口交", "苗条"],
    "rina_vlog":    ["口交", "主观视角", "角色扮演", "情趣内衣", "丝袜", "乳交", "手交"],
    "chocoletmilkk": ["酒店", "美臀", "多人", "素人", "骑乘"],
    "阿曼达":        ["素人", "酒店", "情趣内衣", "丝袜", "骑乘"],
    "LegsJapan":    ["足交", "美腿", "丝袜", "高跟", "无码"],
    "MattieDoll - pornhub.com": ["自慰", "无码", "网红主播", "素人", "苗条", "丝袜"],
    "roselip":      ["无码", "熟女", "女仆", "丝袜", "口交"],
    "emailprotected": ["丝袜", "美腿", "素人", "口交", "制服"],
    "OBOKOZU":      ["口交", "主观视角", "眼镜", "美臀", "无码", "素人"],
    "铃木美咲":      ["制服", "学生", "丝袜", "美腿", "自慰"],
    "秀妍baby":      ["网红主播", "露脸", "素人", "丝袜", "自慰"],
    "임상병리학":     ["素人", "自慰", "露脸", "网红主播", "无码"],
    "Bewyx 2509":   ["角色扮演", "主观视角", "骑乘", "口交"],
    "羊羊子":        ["口交", "酒店", "露脸", "高颜值", "素人"],
}

# 读了板但故意不打标的，理由记在这里，免得下次重读一遍得出同样结论。
SKIPPED = {
    "BNST033":    "全部 34 条是 H 游戏推广广告（hgame1/2/3.xyz + 二维码），不是内容",
    "G3104":      "是某人的私人相机胶卷（宠物、写作业、自拍、夜景），基本不含成人内容",
    "tuki_1154":  "可用样张仅 5 张且过暗过糊，无法确证任何标签",
}

def _safe_name(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]", "_", value)[:40]


def _board_identity(path: Path) -> tuple[str, int] | None:
    match = re.match(r"^(?:v)?\d+_(.+)_(\d+)$", path.stem)
    return (match.group(1), int(match.group(2))) if match else None


def export_review(db_path: Path, board_dir: Path, output: Path) -> tuple[int, int]:
    connection = open_readonly(db_path)
    creators = [row[0] for row in connection.execute(
        "SELECT DISTINCT creator FROM asset WHERE medium='video' "
        "AND creator IS NOT NULL AND creator<>''"
    )]
    connection.close()
    by_safe_name: dict[str, str] = {}
    for creator in creators:
        key = _safe_name(creator)
        if key in by_safe_name and by_safe_name[key] != creator:
            raise RuntimeError(f"board filename collision: {creator} / {by_safe_name[key]}")
        by_safe_name[key] = creator

    previous: dict[str, dict] = {}
    if output.is_file():
        previous = {row["board"]: row for row in read_rows(output)}

    rows: list[dict] = []
    pending = 0
    for board in sorted(board_dir.glob("*.jpg")):
        identity = _board_identity(board)
        if identity is None:
            continue
        safe_creator, count = identity
        creator = by_safe_name.get(safe_creator)
        if not creator:
            raise RuntimeError(f"creator not found for board: {board.name}")
        old = previous.get(board.name)
        if is_structural_creator(creator):
            row = {"board": board.name, "creator": creator, "video_count": count,
                   "status": "skip", "tags": "",
                   "reason": "已核验为结构/集合目录，不是创作者身份"}
        elif old:
            row = {field: old.get(field, "") for field in REVIEW_FIELDS}
            row.update({"board": board.name, "creator": creator, "video_count": count})
        elif creator in BOARDS:
            row = {"board": board.name, "creator": creator, "video_count": count,
                   "status": "applied", "tags": "|".join(BOARDS[creator]), "reason": ""}
        elif creator in SKIPPED:
            row = {"board": board.name, "creator": creator, "video_count": count,
                   "status": "skip", "tags": "", "reason": SKIPPED[creator]}
        else:
            row = {"board": board.name, "creator": creator, "video_count": count,
                   "status": "pending", "tags": "", "reason": ""}
        pending += row["status"] == "pending"
        rows.append(row)

    write_rows(output, REVIEW_FIELDS, rows, atomic=True)
    return len(rows), pending


def apply_review(db_path: Path, review_path: Path, backup: Path) -> tuple[int, int]:
    approved = [row for row in read_rows(review_path) if row.get("status") == "approved"]
    sqlite_backup(db_path, backup)
    connection = sqlite3.connect(db_path, timeout=60)
    connection.execute("PRAGMA busy_timeout=60000")
    known = {row[0] for row in connection.execute(
        "SELECT DISTINCT tag FROM asset_tag WHERE source IN ('name','r18','vision')"
    )}
    total_assets = total_rows = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for row in approved:
            if is_structural_creator(row["creator"]):
                raise ValueError(f"{row['creator']} is a structural creator label")
            tags = list(dict.fromkeys(tag.strip() for tag in row["tags"].split("|") if tag.strip()))
            unknown = set(tags) - known
            if unknown:
                raise ValueError(f"{row['creator']} has unknown tags: {sorted(unknown)}")
            candidates = connection.execute(
                "SELECT id,name,path FROM asset WHERE medium='video' AND creator=? "
                "AND id NOT IN (SELECT asset_id FROM asset_tag)", (row["creator"],)
            ).fetchall()
            ids = [asset_id for asset_id, name, path in candidates
                   if not is_probable_mainstream_release(name, path)]
            connection.executemany(
                "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,?,?,?)",
                [(asset_id, tag, CONFIDENCE, SOURCE) for asset_id in ids for tag in tags],
            )
            for asset_id in ids:
                for tag in tags:
                    upsert_asset_entity(
                        connection, kind="tag", name=tag, asset_id=asset_id, role="tag",
                        source=SOURCE, confidence=CONFIDENCE,
                        metadata={"evidence_board": row["board"]},
                    )
            total_assets += len(ids)
            total_rows += len(ids) * len(tags)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return total_assets, total_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="review-first creator board tagging")
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--board-dir", type=Path, default=BOARD_DIR)
    parser.add_argument("--review", type=Path, default=REVIEW_CSV)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--export-review", action="store_true")
    action.add_argument("--apply-review", action="store_true")
    parser.add_argument("--backup", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.export_review:
        total, pending = export_review(args.db, args.board_dir, args.review)
        print(f"review queue: {total} boards, {pending} pending -> {args.review}")
        return 0
    if not args.backup:
        raise SystemExit("--apply-review 必须同时给 --backup")
    assets, rows = apply_review(args.db, args.review, args.backup)
    print(f"applied: {assets} assets, {rows} tag rows; backup={args.backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
