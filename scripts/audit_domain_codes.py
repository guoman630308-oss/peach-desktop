#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""转载站水印顶替番号的排查器（只读，只出复核 CSV）。

搬运站把自己的域名压成水印写进文件名和目录名。域名剥掉 TLD 之后就是「字母+数字」，
和番号同形，于是导入器把水印当成了作品标识。实测样例 asset 31048：

    B:\番号\_未知厂牌\HHD800\hhd800.com@ABW-132.mp4\ABW-132.mp4
    code=HHD800   ← 真番号 ABW-132 就在文件名里

形态上分不开——真番号 `IPX219C`、`MEYD911` 也是字母紧贴数字。所以这里不猜形态，
只按路径里的域名实证判定，分三档：

- `E1` `code` 本身命中 `catalog_rules.REPOST_SITE_LABELS`，或整串就是个域名。
- `E2` 这条资产自己的 path 里出现 `<code>.<tld>` 或 `<code>@`——水印链的原样。
- `E3` 同 `code` 的其他资产 path 里有 E2 证据，本条没有。同一批搬运包里只有部分
  文件保留了水印，靠自己那条 path 判不出来。

拿不到直接证据、但同 `code` 的 path 里存在别的域名水印时判 `疑似搬运包标识`，只报
存疑不给提案：论坛整合包（`WX17`）的标识确实不是番号，但也不一定是域名，得人工看。

默认只读，只出复核 CSV。`code` 是真相字段，改它要按
`.claude/skills/peach-ledger-write/SKILL.md` 单独授权：`--apply` 必须同时给 `--backup`，
写入的内容取自**复核过的那份 CSV**而不是当场重新推断，人工在表里改过什么就写什么。
`存疑` 档一律不自动写。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path, PureWindowsPath

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.catalog_rules import (
    RELEASE_EVIDENCE_KINDS,
    compact_label,
    is_jav_code,
    is_repost_site_label,
    normalise_code_key,
    release_code_from_filename,
    release_code_from_text,
)
from peach.config import GENERATED_DIR
from peach.review_csv import read_rows, write_rows
from peach.scripting import (
    add_ledger_write_args,
    counts_of,
    open_for_write,
    open_readonly,
    verify_after_write,
)

#: 搬运站常用的顶级域，够宽就行——这里只用来确认「这是个域名」，不做归属判断。
_TLD = (r"com|net|org|la|cc|club|tv|me|xyz|top|vip|info|co|us|pw|ws|to|io"
        r"|app|site|link|biz|online|cn|gg|one|art|fun|shop|sex|xx")
#: 任意域名水印：`hhd800.com`、`www.98t.la`，或搬运链里的 `bei88@`。
_ANY_WATERMARK = re.compile(
    rf"(?<![0-9a-z])((?:www\.)?[0-9a-z][0-9a-z-]{{1,30}}(?:\.(?:{_TLD}))+"
    rf"|[0-9a-z][0-9a-z-]{{1,30}}(?=@))", re.I)
#: 出事的形态：字母紧贴数字、原串里没有分隔符，`normalise_code_key` 会替它补出连字符。
#: `TZ-105` 自带分隔符，就算它的文件来自 thzu.cc 也不必怀疑 code 本身。
_FABRICATED_SEPARATOR = re.compile(r"[A-Za-z]{2,8}\d{2,5}$")

VERDICT_CODED = "域名顶替-有番号"
VERDICT_BLANK = "域名顶替-无番号"
VERDICT_PACK = "疑似搬运包标识"

#: 允许自动写入的证据档。`存疑` 不在其中：论坛整合包的标识确实不是番号，但没有任何
#: 证据指向某个具体番号，替它下手等于把「人工看过」这一步跳过去。
APPLICABLE_TIERS = frozenset({"E1", "E2", "E3"})

APPLIED_WRITTEN = "已写入"
APPLIED_STALE = "跳过-原值已变"
APPLIED_REJECTED = "跳过-提案不是番号"

FIELDS = ("asset_id", "location", "dir", "name", "current_code", "proposed_code",
          "proposal_from", "verdict", "confidence", "tier", "evidence",
          "code_assets", "code_proposals", "meta_evidence")


def label_evidence(label: str, path: str) -> str:
    """在 path 里找 `<label>` 作为域名或搬运链前缀出现的原样片段；没有返回空串。"""
    if not label or not path:
        return ""
    escaped = re.escape(label)
    for pattern in (rf"(?<![0-9a-z]){escaped}(?:\.(?:{_TLD}))+",
                    rf"(?<![0-9a-z]){escaped}(?=@)"):
        found = re.search(pattern, path, re.I)
        if found:
            return found.group(0)
    return ""


def other_watermarks(label: str, path: str) -> list[str]:
    """path 里除 `label` 自己以外的域名水印，用于判「搬运包但标识不是域名」。"""
    seen: list[str] = []
    for found in _ANY_WATERMARK.finditer(path or ""):
        text = found.group(1)
        if compact_label(text.split(".")[0].removeprefix("www.")) == label:
            continue
        if text.lower() not in seen:
            seen.append(text.lower())
    return seen


def proposed_code(label: str, path: str, name: str) -> tuple[str, str]:
    r"""解析真番号，返回 (番号, 来源)；解析不出返回两个空串。

    目录名要跳过和 `code` 同压缩形的那一层——那层就是被水印顶替出来的目录（`…\WX17\`、
    `…\HHD800\`），再解析一遍只会把同一个水印当成提案交给复核的人。
    """
    from_file = release_code_from_filename(name)
    if from_file:
        return from_file, "文件名"
    for part in reversed(PureWindowsPath(path or "").parts[:-1]):
        if compact_label(part) == label:
            continue
        found = release_code_from_text(part) or release_code_from_filename(part)
        if found and compact_label(found) != label:
            return found, "目录名"
    return "", ""


def fill_from_siblings(rows: dict[int, tuple[str, str]],
                       dirs: dict[int, str]) -> None:
    r"""同目录内只解析出一个番号时，把它补给同目录里解析不出的条目。

    发行目录里混着广告图和被站点改过名的分卷（`hjd2048.com-0112mide612-h264\` 下的
    `最新成人AV.gif` 和 `0112mide612-h264.mp4`）。逐个文件名判会把它们报成「无番号，
    建议清空」，而同目录的 `mide612-5.mp4`、`mide612.jpg` 已经指明了这是哪一部。
    只在整个目录**唯一**指向一个番号时才补，来源写明是兄弟条目，不是这条自己的名字。
    """
    by_dir: dict[str, set[str]] = defaultdict(set)
    for asset_id, (code, _) in rows.items():
        if code:
            by_dir[dirs[asset_id]].add(code)
    for asset_id, (code, _) in list(rows.items()):
        if code:
            continue
        siblings = by_dir.get(dirs[asset_id], set())
        if len(siblings) == 1:
            rows[asset_id] = (next(iter(siblings)), "同目录兄弟")


def collect(connection: sqlite3.Connection) -> list[dict[str, object]]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    assets = cursor.execute(
        """SELECT a.id,a.location,a.path,a.name,a.code,a.studio,a.release_date,
                  COALESCE((SELECT group_concat(DISTINCT e.kind) FROM asset_entity ae
                            JOIN entity e ON e.id=ae.entity_id
                            WHERE ae.asset_id=a.id),'') AS kinds
           FROM asset a WHERE COALESCE(a.code,'')<>'' ORDER BY a.id"""
    ).fetchall()

    by_label: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for asset in assets:
        by_label[compact_label(asset["code"])].append(asset)

    rows: list[dict[str, object]] = []
    for label, group in sorted(by_label.items()):
        listed = is_repost_site_label(str(group[0]["code"]))
        direct = {int(a["id"]): label_evidence(label, str(a["path"] or ""))
                  for a in group}
        # 同一 code 下任何一条 path 带水印，就说明这个 code 来自搬运包，
        # 不能因为某条文件被改名过就把它判成干净的番号。
        shared = next((text for text in direct.values() if text), "")
        pack = ""
        if not listed and not shared:
            if not _FABRICATED_SEPARATOR.fullmatch(str(group[0]["code"]).strip()):
                continue
            foreign = sorted({text for a in group
                              for text in other_watermarks(label, str(a["path"] or ""))})
            if not foreign or _has_release_evidence(group):
                continue
            pack = "、".join(foreign[:3])

        proposals = {int(a["id"]): proposed_code(
            label, str(a["path"] or ""), str(a["name"] or "")) for a in group}
        fill_from_siblings(proposals, {
            int(a["id"]): str(PureWindowsPath(str(a["path"] or "")).parent)
            for a in group})
        distinct = sorted({code for code, _ in proposals.values() if code})
        for asset in group:
            asset_id = int(asset["id"])
            proposal, proposal_from = proposals[asset_id]
            if pack:
                # 标识本身不是域名，只是同一批包里出现了域名水印。给提案就等于让复核
                # 的人照着按，而这里没有任何证据支持某个具体番号。
                row_verdict, row_confidence, row_tier = VERDICT_PACK, "低", "存疑"
                proposal, proposal_from = "", ""
                evidence = pack
            else:
                row_verdict = VERDICT_CODED if proposal else VERDICT_BLANK
                if listed:
                    row_confidence, row_tier = "高", "E1"
                elif direct[asset_id]:
                    row_confidence, row_tier = "高", "E2"
                else:
                    row_confidence, row_tier = "中", "E3"
                evidence = direct[asset_id] or shared or f"名单命中 {label}"
            meta = ",".join(part for part in (
                f"studio={asset['studio']}" if asset["studio"] else "",
                f"release_date={asset['release_date']}" if asset["release_date"] else "",
                f"entity={asset['kinds']}" if asset["kinds"] else "",
            ) if part)
            path = PureWindowsPath(str(asset["path"] or ""))
            rows.append({
                "asset_id": asset_id,
                "location": str(asset["location"] or ""),
                "dir": str(path.parent) if path.parts else "",
                "name": str(asset["name"] or ""),
                "current_code": str(asset["code"] or ""),
                "proposed_code": proposal,
                "proposal_from": proposal_from,
                "verdict": row_verdict,
                "confidence": row_confidence,
                "tier": row_tier,
                "evidence": evidence,
                "code_assets": len(group),
                "code_proposals": "、".join(distinct[:6]),
                "meta_evidence": meta,
            })
    return rows


def _has_release_evidence(group: list[sqlite3.Row]) -> bool:
    """有发行证据（厂牌、发行日、出演者／片商／系列实体）就不算水印嫌疑。

    口味 `tag` 不算证据——`WX17` 的 269 条里大半挂着标签，按「有实体就放过」判会把
    整个论坛整合包从复核表里漏掉。判据和 `is_jav_asset` 共用同一个集合。
    """
    return any(
        asset["studio"] or asset["release_date"]
        or RELEASE_EVIDENCE_KINDS.intersection(str(asset["kinds"] or "").split(","))
        for asset in group)


def apply_rows(connection: sqlite3.Connection,
               plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """按复核过的 CSV 改写 `asset.code`，返回逐条的处置结果。

    两道闸门都不能省：

    - `WHERE COALESCE(code,'')=?` 守住 CSV 里记的原值。出表和写库之间隔着一次人工
      阅读，期间别的脚本可能已经动过同一行；改不动的整条跳过并报出来，不静默覆盖。
    - 提案要自己过一遍 `is_jav_code`。人在表里手填是被允许的，但填回一个水印域名就
      等于绕开了这次修复的全部意义。
    """
    outcome: list[dict[str, str]] = []
    for row in plan:
        target = str(row.get("proposed_code") or "").strip() or None
        if target is not None and not is_jav_code(normalise_code_key(target)):
            outcome.append({**row, "applied": APPLIED_REJECTED})
            continue
        connection.execute(
            "UPDATE asset SET code=? WHERE id=? AND COALESCE(code,'')=?",
            (target, int(row["asset_id"]), str(row.get("current_code") or "")))
        changed = connection.execute("SELECT changes()").fetchone()[0]
        outcome.append({**row,
                        "applied": APPLIED_WRITTEN if changed else APPLIED_STALE})
    return outcome


#: 本脚本自己关心的计数口径，接在 `scripting.counts_of` 的基础计数后面。
_CODE_COUNTS = {
    "有 code": "SELECT COUNT(*) FROM asset WHERE COALESCE(code,'')<>''",
    "不同 code": "SELECT COUNT(DISTINCT code) FROM asset WHERE COALESCE(code,'')<>''",
    "asset_search": "SELECT COUNT(*) FROM asset_search",
}


def verify(connection: sqlite3.Connection, asset_ids: list[int]) -> dict[str, str]:
    """写完立刻自检：共用的两道 PRAGMA，加上本次改动特有的 FTS 核对。

    `asset_search.code` 由 `0004`/`0023` 的 `AFTER UPDATE OF name,code` 触发器重建，
    这里不手动补 FTS，而是核对触发器真的跑过了——搜索索引留着旧的水印，界面上就还能
    按 `HHD800` 搜出来，那等于没改。
    """
    placeholders = ",".join("?" * len(asset_ids)) or "NULL"
    stale = connection.execute(
        f"SELECT COUNT(*) FROM asset a LEFT JOIN asset_search s ON s.asset_id=a.id "
        f"WHERE a.id IN ({placeholders}) "
        f"AND COALESCE(s.code,'')<>COALESCE(a.code,'')", asset_ids).fetchone()[0]
    integrity, violations = verify_after_write(connection)
    return {
        "integrity_check": integrity,
        "foreign_key_check": str(violations),
        "FTS 未同步": str(stale),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = add_ledger_write_args(argparse.ArgumentParser(
        description="排查被转载站水印域名顶替的 asset.code；默认只读"))
    parser.add_argument("--review-csv", type=Path,
                        default=GENERATED_DIR / "domain-code-review.csv")
    return parser


def survey(args: argparse.Namespace) -> int:
    # `mode=ro` 连接：这份排查的产物是 CSV，任何写入都要走单独授权的写库流程。
    connection = open_readonly(args.db)
    try:
        rows = collect(connection)
    finally:
        connection.close()

    print(f"命中 {len(rows)} 条资产，涉及 code {len({r['current_code'] for r in rows})} 个")
    print("  判定分布：", dict(Counter(str(row["verdict"]) for row in rows)))
    print("  证据档位：", dict(Counter(str(row["tier"]) for row in rows)))
    for code, count in Counter(str(row["current_code"]) for row in rows).most_common():
        sample = next(row for row in rows if row["current_code"] == code)
        print(f"    {code:12} {count:4} 条  {sample['verdict']}  "
              f"证据 {sample['evidence']!r}  提案 {sample['code_proposals']!r}")

    write_rows(args.review_csv, FIELDS, rows)
    print(f"复核 CSV → {args.review_csv}")
    return 0


def apply_reviewed(args: argparse.Namespace) -> int:
    plan = [row for row in read_rows(args.review_csv)
            if str(row.get("tier") or "") in APPLICABLE_TIERS]
    if not plan:
        raise SystemExit(f"{args.review_csv} 里没有可写入的行；存疑档不自动写。")

    # 备份、`--backup` 缺失的拒绝和可写连接都在 `open_for_write` 里一次做完，
    # 备份必定早于任何写入。
    connection = open_for_write(args)
    print(f"已备份 → {args.backup}")
    try:
        before = counts_of(connection, _CODE_COUNTS)
        connection.execute("BEGIN IMMEDIATE")
        try:
            outcome = apply_rows(connection, plan)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        after = counts_of(connection, _CODE_COUNTS)
        checks = verify(connection, [int(row["asset_id"]) for row in plan])
    finally:
        connection.close()

    # 应用过的那份计划单独留档：`--review-csv` 随后会被写成库的新状态，而「这次改了
    # 哪些行」是事后唯一能对着备份回放的东西。
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    applied_csv = Path(args.review_csv).with_name(
        f"{Path(args.review_csv).stem}.applied-{stamp}.csv")
    write_rows(applied_csv, (*FIELDS, "applied"), outcome)

    print("  处置分布：", dict(Counter(str(row["applied"]) for row in outcome)))
    for key in before:
        arrow = "" if before[key] == after[key] else f"  ← 变了 {after[key] - before[key]:+d}"
        print(f"    {key:12} {before[key]} → {after[key]}{arrow}")
    print("  写后自检：", checks)
    print(f"应用记录 → {applied_csv}")
    return survey(args)


def run(args: argparse.Namespace) -> int:
    if args.apply:
        return apply_reviewed(args)
    result = survey(args)
    print("这是只读排查：未写 ledger。确认 CSV 后再加 --apply --backup。")
    return result


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
