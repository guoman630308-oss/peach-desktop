#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名净化 —— 去掉盗版站塞进文件名的推广域名与残留噪声。

只做可逆、无歧义的改写。番号使用 Peach 的统一规范：大写、标准连字符、片商番号
至少三位数字；FC2 统一为 ``FC2-PPV-<数字>``。
  * 不动 "xxx.mp4.jpg"：那是该 mp4 的缩略图，不是双后缀（真的双后缀只有 2 条）。
  * 不重排标题/不套命名模板：只替换 ledger 已确认的番号片段。

先出 CSV 供人工核对，加 --apply 才落盘；改名同时同步 ledger 的 path/name。

用法:
    python scripts/clean_names.py [--location 来源ID] [--apply]
"""
from __future__ import annotations

import argparse
import sys
import ntpath
import os
import posixpath
import re
import sqlite3
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.review_csv import write_rows
from peach.catalog_rules import is_jav_code, normalise_code_key
from peach.config import DATABASE_PATH, GENERATED_DIR, LOG_DIR
from peach.media_sources import SCANNABLE_LOCATIONS
from peach.scripting import (
    add_ledger_write_args, open_for_write, open_readonly, verify_after_write,
)

DEFAULT_DB = DATABASE_PATH
DEFAULT_OUT = GENERATED_DIR / "name-clean.csv"
DEFAULT_LOG_DIR = LOG_DIR
_logf = None


def configure_log(log_dir: str | Path) -> None:
    global _logf
    root = Path(log_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / time.strftime("clean-names-%Y%m%d-%H%M%S.log")
    _logf = path.open("w", encoding="utf-8", buffering=1)


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _logf is not None:
        _logf.write(line + "\n")


def close_log() -> None:
    global _logf
    if _logf is not None:
        _logf.close()
        _logf = None


TLD = r"(?:com|net|la|xyz|cc|me|top|vip|club|info|org|tv|app|co|pw|gg|cn)"

# 站点前缀：域名后面常跟 @ - _ 空格，或直接连着广告语
RE_PREFIX = re.compile(
    rf"^\s*[\[\【@]?\s*(?:www\.)?[0-9A-Za-z][-0-9A-Za-z]{{1,30}}\.{TLD}"
    rf"(?:\d{{6,}})?\s*[@\-_\]\】、,，]?\s*", re.I)
# 尾部域名：紧贴扩展名之前
RE_SUFFIX = re.compile(
    rf"[-_@\s]+(?:www\.)?[0-9A-Za-z][-0-9A-Za-z]{{2,25}}\.{TLD}\s*(?=\.[A-Za-z0-9]{{2,4}}$)", re.I)
# 番号后直接黏着的方括号域名：`CJOD-158[fuckbe.com].mp4` 没有分隔符，
# 旧 `RE_SUFFIX` 刻意要求空格／横线，因而漏掉。只删完整括号域名，不碰普通标题括号。
RE_BRACKET_DOMAIN = re.compile(
    rf"[\[【(（]\s*(?:www\.)?[0-9A-Za-z][-0-9A-Za-z]{{1,30}}\.{TLD}\s*[\]】)）]", re.I)
# 中文推广尾巴（域名已被上面吃掉后剩下的宣传语）
RE_ADCOPY = re.compile(
    r"[-_\s]*(?:全网最火爆成人游戏|成人手游|最新地址|开车地址|每日更新|无码破解|"
    r"免费看片|请记住本站|更多资源|中文成人网站?)[^.]*(?=\.[A-Za-z0-9]{2,4}$)")


def _normalise_code_in_name(name: str, code: str | None,
                            allow_compact_code: bool = False) -> str:
    """只替换 ledger 已确认的番号片段，不从文件名重新猜番号。"""
    canonical = normalise_code_key(code)
    if not is_jav_code(code) and not (
        allow_compact_code and is_jav_code(canonical)
    ):
        return name
    if canonical.startswith("FC2-PPV-"):
        digits = canonical.rsplit("-", 1)[-1]
        pattern = re.compile(
            rf"(?<![A-Z0-9])FC2(?:[-_ ]?PPV)?[-_ ]*0*{re.escape(digits)}(?!\d)", re.I,
        )
    else:
        amateur = re.fullmatch(r"(\d{3})?([A-Z]+)-(\d+)", canonical)
        dated = re.fullmatch(r"(\d{6})([-_])(\d{2,4})", canonical)
        if amateur:
            prefix, letters, digits = amateur.groups()
            number = str(int(digits))
            pattern = re.compile(
                rf"(?<![A-Z0-9]){re.escape(prefix or '')}{re.escape(letters)}"
                rf"[-_ ]?0*{re.escape(number)}(?!\d)", re.I,
            )
        elif dated:
            left, separator, right = dated.groups()
            # 只补齐缺分隔符或用空格分隔的写法。日期式番号的 `-` 与 `_` 是两个片商
            # （`catalog_rules._CODE_DATE`），把文件名里的另一种改写成 ledger 这一种，
            # 就是拿一部片的名字盖掉另一部的证据。
            pattern = re.compile(
                rf"(?<!\d){re.escape(left)}(?:{re.escape(separator)}| )?"
                rf"{re.escape(right)}(?!\d)",
            )
        else:
            return name
    return pattern.sub(canonical, name, count=1)


def propose(name: str, code: str | None = None,
            allow_compact_code: bool = False) -> str:
    """返回净化后的文件名；无需改动则原样返回。"""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:           # 没有扩展名、或形如 ".mp4" 的空主干，都不碰
        return name

    new = RE_PREFIX.sub("", name, count=1)
    new, bracket_domains = RE_BRACKET_DOMAIN.subn("", new)
    new = RE_SUFFIX.sub("", new)
    new = RE_ADCOPY.sub("", new)
    new = _normalise_code_in_name(new, code, allow_compact_code)

    # 同类双后缀 .mp4.mp4（.mp4.jpg 这种缩略图命名不算）
    dbl = re.search(r"\.([A-Za-z0-9]{2,4})\.([A-Za-z0-9]{2,4})$", new)
    if dbl and dbl.group(1).lower() == dbl.group(2).lower():
        new = new[: dbl.start()] + "." + dbl.group(2)

    # 删除方括号域名后会留下双分隔符或尾分隔符：
    # `标题 - [site.com] - 年份` 应变成 `标题 - 年份`，而不是 `标题 -  - 年份`。
    if bracket_domains:
        new = re.sub(r"[ \t]{2,}", " ", new)
        new = re.sub(r"(?:\s*-\s*){2,}", " - ", new)

    # 首尾空白 / 扩展名前的空格、点和分隔符
    new = new.strip()
    if new != name:
        new = re.sub(r"[\s._\-—]+(?=\.[A-Za-z0-9]{2,4}$)", "", new)
    new = re.sub(r"^[\s._\-—]+", "", new)

    # 净化到只剩扩展名、或只剩 "(1)" 这种序号，说明规则吃过头了，放弃这条
    if not new or new.startswith(".") or new == name:
        return name
    body = os.path.splitext(new)[0]
    if not re.sub(r"[\s()\[\]【】.\-_—]", "", body):
        return name
    # 只剩 "(2)" 这种括号序号，等于把名字删没了
    if re.fullmatch(r"[\s\-_.]*\(\d+\)[\s\-_.]*", body):
        return name
    if os.path.splitext(new)[1].lower() != os.path.splitext(name)[1].lower():
        return name
    return new


FIELDS = ["id", "location", "dir", "old", "new", "old_code", "new_code", "status"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="preview or apply conservative filename cleanup")
    add_ledger_write_args(parser, db_default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--location", choices=SCANNABLE_LOCATIONS)
    return parser


def write_plan(path: str | Path, plan: list[dict]) -> None:
    target = Path(path)
    write_rows(target, FIELDS, plan)


def verify_backup(backup: Path) -> None:
    """备份是唯一的回退路径，所以要当场校验一遍。

    坏掉的备份比没有备份更糟：它看起来是有的，等到需要它的那天才发现打不开。
    备份本身由 `open_for_write` 用 SQLite 备份 API 落盘（WAL 安全），这里只做验收。
    """
    connection = open_readonly(backup)
    try:
        integrity, foreign_keys = verify_after_write(connection)
    finally:
        connection.close()
    if integrity != "ok" or foreign_keys:
        raise RuntimeError(
            f"备份校验失败：integrity={integrity} foreign_keys={foreign_keys}"
        )


def _numbered_name(name: str, occupied: set[str]) -> tuple[str, bool]:
    """目标撞名时保留两份媒体，用稳定的 ``(n)`` 后缀消歧。"""
    if name.lower() not in occupied:
        return name, False
    stem, ext = os.path.splitext(name)
    index = 2
    while True:
        candidate = f"{stem} ({index}){ext}"
        if candidate.lower() not in occupied:
            return candidate, True
        index += 1


def _path_ops(path: str):
    """按 ledger 路径本身选择 Windows／POSIX 语义，而不是按运行主机选择。"""
    return ntpath if ntpath.splitdrive(path)[0] or "\\" in path else posixpath


def _dirname(path: str) -> str:
    return _path_ops(path).dirname(path)


def _join(directory: str, name: str) -> str:
    return _path_ops(directory).join(directory, name)


def build_plan(rows: list[tuple]) -> list[dict]:
    taken: dict[str, set[str]] = {}
    for _aid, _location, path, name, _code, *_evidence in rows:
        taken.setdefault(_dirname(path).lower(), set()).add(str(name).lower())

    plan: list[dict] = []
    for aid, location, path, name, code, *evidence in rows:
        allow_compact = bool(evidence and evidence[0])
        normalized = normalise_code_key(code)
        canonical = (
            normalized if is_jav_code(code)
            or (allow_compact and is_jav_code(normalized)) else str(code or "")
        )
        proposed = propose(str(name), str(code or ""), allow_compact)
        if proposed == name and canonical == str(code or ""):
            continue
        directory = _dirname(path)
        occupied = taken[directory.lower()]
        occupied.discard(str(name).lower())
        target, suffixed = _numbered_name(proposed, occupied)
        occupied.add(target.lower())
        plan.append({
            "id": aid, "location": location, "dir": directory,
            "old": name, "new": target, "old_code": code or "",
            "new_code": canonical, "status": "ready-suffixed" if suffixed else "ready",
        })
    return plan


def _rename_path(source: str, destination: str) -> None:
    """Windows 只改大小写时通过同目录临时名，避免把源误判成目标撞名。"""
    if source != destination and source.lower() == destination.lower():
        staging = source + f".peach-rename-{uuid.uuid4().hex}.tmp"
        os.rename(source, staging)
        try:
            os.rename(staging, destination)
        except Exception:
            os.rename(staging, source)
            raise
    else:
        os.rename(source, destination)


def _database_health(connection: sqlite3.Connection) -> tuple[str, int, int]:
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    assets = int(connection.execute("SELECT count(*) FROM asset").fetchone()[0])
    return integrity, foreign_keys, assets


def run(args_ns: argparse.Namespace) -> int:
    conn = open_for_write(args_ns)
    where = "WHERE name IS NOT NULL"
    sql_args: list = []
    if args_ns.location:
        where += " AND location = ?"
        sql_args.append(args_ns.location)
    asset_columns = {row[1] for row in conn.execute("PRAGMA table_info(asset)")}
    evidence_terms = []
    if "studio" in asset_columns:
        evidence_terms.append("COALESCE(trim(studio),'')<>''")
    if "release_date" in asset_columns:
        evidence_terms.append("COALESCE(trim(release_date),'')<>''")
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if {"asset_entity", "entity"}.issubset(tables):
        evidence_terms.append(
            "EXISTS(SELECT 1 FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "WHERE ae.asset_id=asset.id AND e.kind IN ('performer','studio','series'))"
        )
    release_evidence = " OR ".join(evidence_terms) or "0"
    rows = conn.execute(
        f"SELECT id,location,path,name,code,CASE WHEN {release_evidence} "
        f"THEN 1 ELSE 0 END FROM asset {where}", sql_args,
    ).fetchall()
    log(f"扫描 {len(rows)} 条" + (f"（location={args_ns.location}）" if args_ns.location else ""))

    plan = build_plan(rows)
    ready = [p for p in plan if p["status"].startswith("ready")]
    suffixed = sum(p["status"] == "ready-suffixed" for p in plan)
    log(f"待规范 {len(plan)} 条，其中可执行 {len(ready)}，撞名保留双份 {suffixed}")
    write_plan(args_ns.out, plan)

    if args_ns.apply:
        before = _database_health(conn)
        if before[0] != "ok" or before[1]:
            raise RuntimeError(f"写入前 ledger 校验失败：integrity={before[0]} foreign_keys={before[1]}")
        verify_backup(args_ns.backup)
        log(f"数据库备份 → {args_ns.backup}")
        ok = fail = gone = 0
        for item in ready:
            src = _join(item["dir"], item["old"])
            dst = _join(item["dir"], item["new"])
            rename_needed = src != dst
            if rename_needed and not os.path.exists(src):
                item["status"] = "missing"
                gone += 1
                continue
            case_only = src.lower() == dst.lower()
            if rename_needed and os.path.exists(dst) and not case_only:
                item["status"] = "collide"
                fail += 1
                continue
            try:
                if rename_needed:
                    _rename_path(src, dst)
            except Exception as exc:
                item["status"] = f"error:{type(exc).__name__}"
                log(f"  失败 {src} -> {item['new']} : {exc}")
                fail += 1
                continue
            try:
                conn.execute(
                    "UPDATE asset SET path=?, name=?, code=? WHERE id=?",
                    (dst, item["new"], item["new_code"], item["id"]),
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                try:
                    if rename_needed:
                        _rename_path(dst, src)
                    item["status"] = f"db-error-rolled-back:{type(exc).__name__}"
                except Exception:
                    item["status"] = f"db-error-manual-repair:{type(exc).__name__}"
                log(f"  数据库失败 {src} -> {item['new']} : {exc}")
                fail += 1
                continue
            item["status"] = "done"
            ok += 1
        after = _database_health(conn)
        if after[0] != "ok" or after[1] or after[2] != before[2]:
            raise RuntimeError(
                f"写入后 ledger 校验失败：integrity={after[0]} foreign_keys={after[1]} "
                f"assets={before[2]}->{after[2]}"
            )
        log(f"改名完成 {ok}，失败 {fail}，源已不存在 {gone}")
    conn.close()

    write_plan(args_ns.out, plan)
    log(f"→ {args_ns.out}")
    if not args_ns.apply:
        log("未加 --apply，只出清单未改名。")
    return 2 if args_ns.apply and any(
        not str(item["status"]).startswith("done") for item in plan
    ) else 0


def main(argv: list[str] | None = None) -> int:
    args_ns = build_parser().parse_args(argv)
    configure_log(args_ns.log_dir)
    try:
        return run(args_ns)
    finally:
        close_log()


if __name__ == "__main__":
    raise SystemExit(main())
