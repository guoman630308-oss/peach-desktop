#!/usr/bin/env python3
"""把已经确认厂牌的番号目录移出 ``_未知厂牌``，并去掉番号下的多余层级。

映射来自人工复核 CSV；脚本不猜厂牌。默认只写计划，``--apply`` 时先备份
SQLite，再逐文件移动，最后在一个事务内更新路径与厂牌实体关系。任何重名、
缺失文件、同目录多厂牌或账本校验失败都会拒绝执行。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.scripting import open_readonly
from peach.entities import upsert_asset_entity
from peach.migrations import sqlite_backup
from peach.review_csv import ENCODING, write_rows


PLAN_FIELDS = (
    "code", "studio", "asset_id", "tracked", "old_ledger_path", "new_ledger_path",
    "old_physical_path", "new_physical_path", "source", "confidence", "evidence_url",
    "note", "status",
)
INVALID_WINDOWS_NAME = set('<>:"/\\|?*')


class Mapping(NamedTuple):
    code: str
    studio: str
    source: str
    confidence: float
    evidence_url: str = ""
    note: str = ""


class Move(NamedTuple):
    code: str
    studio: str
    source: Path
    target: Path
    mapping: Mapping


class AssetUpdate(NamedTuple):
    asset_id: int
    code: str
    studio: str
    old_path: str
    new_path: str
    old_studio: str
    mapping: Mapping


class Plan:
    def __init__(self) -> None:
        self.moves: list[Move] = []
        self.updates: list[AssetUpdate] = []
        self.rows: list[dict[str, object]] = []
        self.errors: list[str] = []


def read_mappings(path: Path) -> list[Mapping]:
    with path.open("r", encoding=ENCODING, newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"code", "studio", "source", "confidence"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"映射 CSV 缺列：{sorted(missing)}")
        mappings: list[Mapping] = []
        seen: set[str] = set()
        for number, row in enumerate(reader, 2):
            code = str(row.get("code") or "").strip().upper()
            studio = str(row.get("studio") or "").strip()
            source = str(row.get("source") or "").strip()
            if not code or not studio or not source:
                raise ValueError(f"映射 CSV 第 {number} 行有空值")
            if any(char in INVALID_WINDOWS_NAME for char in studio) or studio in {".", ".."}:
                raise ValueError(f"厂牌不能作为 Windows 目录名：{studio}")
            if code in seen:
                raise ValueError(f"映射 CSV 重复番号：{code}")
            seen.add(code)
            try:
                confidence = float(row.get("confidence") or "")
            except ValueError as exc:
                raise ValueError(f"映射 CSV 第 {number} 行置信度无效") from exc
            if not 0 <= confidence <= 1:
                raise ValueError(f"映射 CSV 第 {number} 行置信度越界")
            mappings.append(Mapping(
                code=code, studio=studio, source=source, confidence=confidence,
                evidence_url=str(row.get("evidence_url") or "").strip(),
                note=str(row.get("note") or "").strip(),
            ))
    return mappings


def _ledger_child(root: PureWindowsPath, *parts: str) -> str:
    return str(PureWindowsPath(root, *parts))


def _path_key(value: str | Path) -> str:
    return os.path.normcase(str(value)).casefold()


def _direct_children(path: Path) -> dict[str, Path]:
    try:
        return {child.name.casefold(): child for child in path.iterdir()} if path.is_dir() else {}
    except FileNotFoundError:
        # CloudDrive 对空目录的清理由另一进程异步完成，is_dir 与
        # iterdir 之间消失等价于一个空目标，不是计划冲突。
        return {}


def build_plan(
    connection: sqlite3.Connection, *, mappings: list[Mapping],
    physical_unknown_root: Path, ledger_unknown_root: str,
) -> Plan:
    connection.row_factory = sqlite3.Row
    plan = Plan()
    ledger_unknown = PureWindowsPath(ledger_unknown_root)
    ledger_studio_root = ledger_unknown.parent
    all_assets = connection.execute(
        "SELECT id,path,name,code,coalesce(studio,'') AS studio FROM asset ORDER BY id"
    ).fetchall()

    for mapping in mappings:
        source_dir = physical_unknown_root / mapping.code
        target_dir = physical_unknown_root.parent / mapping.studio / mapping.code
        if not source_dir.is_dir():
            plan.errors.append(f"来源目录不存在：{source_dir}")
            continue
        files = sorted((path for path in source_dir.rglob("*") if path.is_file()),
                       key=lambda item: _path_key(item))
        if not files:
            plan.errors.append(f"番号目录没有文件：{source_dir}")
            continue
        by_leaf: dict[str, Path] = {}
        for source in files:
            key = source.name.casefold()
            if key in by_leaf:
                plan.errors.append(
                    f"扁平化后文件名冲突：{by_leaf[key]} / {source}"
                )
                continue
            by_leaf[key] = source
        existing = _direct_children(target_dir)
        for key, source in by_leaf.items():
            target = target_dir / source.name
            if key in existing and _path_key(existing[key]) != _path_key(source):
                plan.errors.append(f"目标文件已存在：{existing[key]}")
                continue
            plan.moves.append(Move(
                code=mapping.code, studio=mapping.studio,
                source=source, target=target, mapping=mapping,
            ))

        old_prefix = (_ledger_child(ledger_unknown, mapping.code) + "\\").casefold()
        rows = [row for row in all_assets
                if str(row["path"] or "").casefold().startswith(old_prefix)]
        for asset in rows:
            leaf = PureWindowsPath(str(asset["path"])).name
            physical = by_leaf.get(leaf.casefold())
            old_studio = str(asset["studio"] or "").strip()
            if physical is None:
                plan.errors.append(
                    f"账本文件在目录中未找到：asset {asset['id']} {asset['path']}"
                )
                continue
            if old_studio and old_studio != mapping.studio:
                plan.errors.append(
                    f"厂牌冲突：asset {asset['id']} 是 {old_studio}，映射要求 {mapping.studio}"
                )
                continue
            new_path = _ledger_child(ledger_studio_root, mapping.studio, mapping.code,
                                     physical.name)
            plan.updates.append(AssetUpdate(
                asset_id=int(asset["id"]), code=mapping.code, studio=mapping.studio,
                old_path=str(asset["path"]), new_path=new_path,
                old_studio=old_studio, mapping=mapping,
            ))

        updates_by_leaf = {
            PureWindowsPath(update.old_path).name.casefold(): update
            for update in plan.updates if update.code == mapping.code
        }
        for move in (item for item in plan.moves if item.code == mapping.code):
            update = updates_by_leaf.get(move.source.name.casefold())
            plan.rows.append({
                "code": mapping.code, "studio": mapping.studio,
                "asset_id": update.asset_id if update else "",
                "tracked": "yes" if update else "no",
                "old_ledger_path": update.old_path if update else "",
                "new_ledger_path": update.new_path if update else "",
                "old_physical_path": str(move.source),
                "new_physical_path": str(move.target),
                "source": mapping.source, "confidence": mapping.confidence,
                "evidence_url": mapping.evidence_url, "note": mapping.note,
                "status": "blocked" if plan.errors else "ready",
            })
    if plan.errors:
        for row in plan.rows:
            row["status"] = "blocked"
    return plan


def _check_database(connection: sqlite3.Connection) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"ledger integrity_check 失败：{integrity}")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise RuntimeError(f"ledger 外键违规：{len(foreign_keys)}")


def _rollback_moves(done: list[Move]) -> None:
    for move in reversed(done):
        if not move.target.exists():
            continue
        move.source.parent.mkdir(parents=True, exist_ok=True)
        move.target.rename(move.source)


def _remove_empty_tree(root: Path) -> None:
    if not root.is_dir():
        return
    directories = sorted((path for path in root.rglob("*") if path.is_dir()),
                         key=lambda path: len(path.parts), reverse=True)
    for directory in directories:
        try:
            if not any(directory.iterdir()):
                directory.rmdir()
        except FileNotFoundError:
            # CloudDrive 会在最后一个文件移走后自行收掉某些空目录；
            # 计划中的旧句柄随即失效，这等价于已完成清理。
            continue
    try:
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    except FileNotFoundError:
        pass


def apply_plan(
    connection: sqlite3.Connection, *, plan: Plan, backup: Path,
    physical_unknown_root: Path,
) -> dict[str, int]:
    if plan.errors:
        raise ValueError("计划含阻塞项，拒绝执行")
    if backup.exists():
        raise FileExistsError(f"备份已存在，拒绝覆盖：{backup}")
    _check_database(connection)
    before_assets = connection.execute("SELECT count(*) FROM asset").fetchone()[0]
    sqlite_backup(Path(connection.execute("PRAGMA database_list").fetchone()[2]), backup)
    saved = open_readonly(backup)
    try:
        _check_database(saved)
        if saved.execute("SELECT count(*) FROM asset").fetchone()[0] != before_assets:
            raise RuntimeError("备份资产计数不一致")
    finally:
        saved.close()

    done: list[Move] = []
    try:
        for move in plan.moves:
            move.target.parent.mkdir(parents=True, exist_ok=True)
            move.source.rename(move.target)
            done.append(move)
        # 空层只含已经移动走的文件；先删除它们。后续账本事务失败时，
        # rollback 会按原路径重建父目录并把文件移回去。
        for mapping in {move.mapping for move in plan.moves}:
            _remove_empty_tree(physical_unknown_root / mapping.code)
        now = datetime.now(timezone.utc).isoformat()
        with connection:
            for update in plan.updates:
                changed = connection.execute(
                    "UPDATE asset SET path=?,name=?,studio=? WHERE id=? AND path=?",
                    (update.new_path, PureWindowsPath(update.new_path).name,
                     update.studio, update.asset_id, update.old_path),
                ).rowcount
                if changed != 1:
                    raise RuntimeError(f"asset {update.asset_id} 路径并发变化，拒绝提交")
                if not update.old_studio:
                    upsert_asset_entity(
                        connection, kind="studio", name=update.studio,
                        asset_id=update.asset_id, role="studio",
                        source=update.mapping.source,
                        confidence=update.mapping.confidence,
                        metadata={
                            "evidence_url": update.mapping.evidence_url,
                            "note": update.mapping.note,
                            "confirmed_code": update.code,
                        }, now=now,
                    )
            _check_database(connection)
            after_assets = connection.execute("SELECT count(*) FROM asset").fetchone()[0]
            if after_assets != before_assets:
                raise RuntimeError(f"资产计数变化：{before_assets} -> {after_assets}")
            for update in plan.updates:
                row = connection.execute(
                    "SELECT path,studio FROM asset WHERE id=?", (update.asset_id,)
                ).fetchone()
                if row is None or row[0] != update.new_path or row[1] != update.studio:
                    raise RuntimeError(f"asset {update.asset_id} 更新后校验失败")
        return {
            "files_moved": len(done), "assets_updated": len(plan.updates),
            "studio_fields_filled": sum(not update.old_studio for update in plan.updates),
        }
    except Exception:
        _rollback_moves(done)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按人工确认映射移出 _未知厂牌，并扁平化番号内目录"
    )
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--mappings", type=Path, required=True)
    parser.add_argument("--physical-unknown-root", type=Path, required=True)
    parser.add_argument("--ledger-unknown-root", required=True,
                        help="未知厂牌目录的账本路径，使用 Windows 绝对路径")
    parser.add_argument("--plan", type=Path,
                        default=GENERATED_DIR / "rehome-unknown-jav-plan.csv")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and args.backup is None:
        raise SystemExit("--apply 必须同时给 --backup")
    mappings = read_mappings(args.mappings)
    connection = sqlite3.connect(args.db)
    try:
        plan = build_plan(
            connection, mappings=mappings,
            physical_unknown_root=args.physical_unknown_root,
            ledger_unknown_root=args.ledger_unknown_root,
        )
        write_rows(args.plan, PLAN_FIELDS, plan.rows, atomic=True)
        print(
            f"映射 {len(mappings)} 个番号；文件 {len(plan.moves)} 个；"
            f"账本路径 {len(plan.updates)} 条；计划：{args.plan}"
        )
        if plan.errors:
            for error in plan.errors:
                print(f"  阻塞：{error}")
            return 1
        if not args.apply:
            print("  dry-run：未移动文件，未写 ledger")
            return 0
        result = apply_plan(
            connection, plan=plan, backup=args.backup,
            physical_unknown_root=args.physical_unknown_root,
        )
        for row in plan.rows:
            row["status"] = "done"
        write_rows(args.plan, PLAN_FIELDS, plan.rows, atomic=True)
        print(json.dumps(result, ensure_ascii=False))
        print(f"  备份：{args.backup}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
