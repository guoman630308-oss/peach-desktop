#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
广告文件识别 —— 找出盗版组塞进资源包里的推广视频，出清单供人工处置。

不删任何东西，只出 CSV。判据来自实际样本 B:\创作者\BNST033\aaxv.xyz-BNST033\：
正片 BNST033.mp4 有 3.2 GB，旁边躺着一堆 10.9 MB / 37.40 秒**逐字节等长等时**的
"催眠(1..N).mp4"，外加 論壇文宣\ 和 1024\ 两个子目录。

五条判据，各自独立记分，命中越多越可信：
  A 目录名自曝：文宣 / 宣傳 / 廣告 / 1024 / 論壇 等
  B 同目录内体积与时长完全相同的一组小短片（正片不会出现这种整齐的重复）
  C 文件名里的手游推广词与推广站域名
  D 小且短的文件名带任意站点域名（存疑，交人工）
  E 目录名是「域名+番号」的推广打包（bbsxv.xyz-DOCP-324 形态）

B 是主力：它不依赖任何关键词，纯靠"广告是同一个文件复制多份"的结构特征。

用法:
    python scripts/find_ads.py [--min-group 3]
输出:
    R:\peach-data\generated\ad-candidates.csv
"""
from __future__ import annotations

import collections
import argparse
import re
import sys
from pathlib import Path, PureWindowsPath

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.review_csv import write_rows
from peach.scripting import open_readonly
from peach.config import DATABASE_PATH, GENERATED_DIR

DEFAULT_OUTPUT = GENERATED_DIR / "ad-candidates.csv"

# A：目录名自曝。整段路径分量匹配，不做子串 ——
# "1024" 当子串会吃掉 FC2-PPV-1381024 这种正片番号（实测误伤 970 MB 一条）。
RE_ADDIR = re.compile(r"(論壇文宣|论坛文宣|文宣|宣傳|宣传|廣告|广告|推廣|推广|"
                      r"發佈頁|发布页|禮包碼|礼包码|地址发布|免费预览)", re.I)
RE_ADDIR_EXACT = re.compile(r"^(1024|論壇|论坛|廣告|广告|文宣)$", re.I)

# C：只认推广站域名。"成人游戏""加微信"这类词单独出现不算 ——
# 实测正片标题里就有（"兄妹成人游戏" 9.98 GB、"花了三萬加微信約出來的女主播"），
# 拿它当判据会把正片当广告删掉。
RE_ADNAME = re.compile(
    r"([0-9a-z]{1,8}\.(?:18my|gm\d?|gg\d?|hgame\d?)\.(?:cc|xyz|com|net|app)|"
    r"\b(?:18my|hgame\d?|[0-9a-z]{2,6}(?:gm|gg))\.(?:cc|xyz|com|net|app)|"
    r"(?:全網最火爆|全网最火爆|開後宮|开后宫)[^.]{0,12}(?:遊戲|游戏|手遊|手游))", re.I)

# D：文件名里带任意站点域名。真广告全是又小又短的，所以必须叠加体积时长门槛。
RE_ANYDOMAIN = re.compile(
    r"\b[0-9a-z][-0-9a-z]{1,20}\.(?:cc|xyz|com|net|la|me|top|vip|club|app|cn|pw|tv|gg)\b", re.I)
# 域名旁边有没有推广文案，是"广告"和"正片被打了站点水印"的分界线。
# 实测 jitumi.pw(1).avi 抽帧是体操服正片，光有裸域名不能判广告。
RE_ADCOPY = re.compile(
    r"(手遊|手游|遊戲大全|游戏大全|在線觀看|在线观看|在線視頻|在线视频|賭場|赌场|"
    r"約炮|约炮|女神檔案|女神档案|精彩直播|大秀直播|禮包碼|礼包码|"
    r"最新地址|開車地址|开车地址|徵信|征信|借貸|借贷)", re.I)

# E：目录名是「域名+番号」的推广打包，如 B:\云下载\bbsxv.xyz-DOCP-324\。
# 实测该包内两条小视频（28 秒 / 91 秒）文件名干净、无等长重复，只有目录暴露身份。
# 注意裸域名水印目录（www.98T.la@账号、huachishe.com@系列）是转载来源标注，
# 不是广告；所以必须「域名紧贴番号」才命中，不能只看有域名。
RE_DIRPACK = re.compile(
    r"[0-9a-z][-0-9a-z]{1,20}\.[a-z]{2,10}[ \-_]+\[?[A-Za-z]{2,6}-?\d{2,5}", re.I)

def ledger_dir(path: str) -> str:
    """取账本路径的目录部分。

    账本里的路径永远是 Windows 口径（`B:\\云下载\\...`），而 `os.path.dirname` 在 macOS 上
    不认反斜杠，整条路径会被当成单个文件名、目录返回空串。后果不只是判据 A/E 失效：
    判据 B 用「同目录 + 同体积 + 同时长」分组，目录恒为空就等于跨整个库比对。
    `PureWindowsPath` 两种分隔符都认，两个平台结果一致。
    """
    return str(PureWindowsPath(path).parent)


# B 的边界：只有"小且短"的整齐重复才算广告，避免误伤分集正片
MAX_AD_MB = 80
MAX_AD_SEC = 180
# D 的边界：正片再短也很少低于这个量级
DOMAIN_AD_SEC = 150
DOMAIN_AD_MB = 220

FIELDS = ["id", "location", "dir", "name", "size_mb", "duration",
          "confidence", "score", "hits", "group_n"]


def find_candidates(db_path: Path | str, min_group: int = 3) -> tuple[list[dict], int]:
    """只读 ledger 并返回复核候选；不写数据库或文件。"""
    db_path = Path(db_path)
    conn = open_readonly(db_path)
    rows = conn.execute(
        "SELECT id, location, path, name, size, duration FROM asset "
        "WHERE medium='video'").fetchall()
    conn.close()
    # B：同目录内 (size, duration) 完全相同的成组小短片
    buckets: dict[tuple[str, int, str], list] = collections.defaultdict(list)
    for aid, loc, path, name, size, duration in rows:
        if not size or not duration:
            continue
        if size > MAX_AD_MB * 1048576 or duration > MAX_AD_SEC:
            continue
        key = (ledger_dir(path).lower(), size, f"{duration:.3f}")
        buckets[key].append((aid, loc, path, name, size, duration))
    grouped = {}
    for items in buckets.values():
        if len(items) >= min_group:
            for it in items:
                grouped[it[0]] = len(items)

    plan = []
    for aid, loc, path, name, size, duration in rows:
        directory = ledger_dir(path)
        parts = PureWindowsPath(path).parent.parts
        hits = []
        if RE_ADDIR.search(directory) or any(RE_ADDIR_EXACT.match(p) for p in parts):
            hits.append("目录名")
        if RE_ADNAME.search(name) or RE_ADNAME.search(directory):
            hits.append("推广词")
        if aid in grouped:
            hits.append(f"等长重复x{grouped[aid]}")
        small = (size or 0) <= DOMAIN_AD_MB * 1048576
        short = duration is not None and 0 < duration <= DOMAIN_AD_SEC
        if RE_ANYDOMAIN.search(name) and small and short:
            hits.append("域名+文案" if RE_ADCOPY.search(name) else "裸域名")
        elif RE_DIRPACK.search(directory) and small and short:
            hits.append("推广目录")
        if not hits:
            continue
        # 只有裸域名、没有任何其它佐证的，交人工看一眼再定
        solid = [h for h in hits if h != "裸域名"]
        plan.append({
            "id": aid, "location": loc, "dir": directory, "name": name,
            "size_mb": round((size or 0) / 1048576, 1),
            "duration": round(duration, 1) if duration else "",
            "confidence": "确认" if solid else "存疑",
            "score": len(hits), "hits": "+".join(hits),
            "group_n": grouped.get(aid, ""),
        })

    plan.sort(key=lambda r: (r["confidence"] != "确认", -r["score"], r["dir"]))
    return plan, len(rows)


def write_candidates(output: Path | str, plan: list[dict]) -> None:
    output = Path(output)
    write_rows(output, FIELDS, plan)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成广告文件人工复核清单")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-group", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.min_group < 2:
        raise SystemExit("--min-group 必须至少为 2")
    plan, scanned = find_candidates(args.db, args.min_group)
    print(f"扫描 {scanned} 条视频")
    write_candidates(args.output, plan)

    print(f"命中 {len(plan)} 条，合计 {sum(r['size_mb'] for r in plan)/1024:.1f} GB")
    for level in ("确认", "存疑"):
        sub = [r for r in plan if r["confidence"] == level]
        print(f"  {level}: {len(sub):>4} 条  {sum(r['size_mb'] for r in sub)/1024:>6.2f} GB")
    print(f"\n判据分布: {collections.Counter(r['hits'] for r in plan).most_common(6)}")
    print(f"→ {args.output}（仅清单，未删任何文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
