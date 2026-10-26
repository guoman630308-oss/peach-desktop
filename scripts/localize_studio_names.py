"""用作品番号回查 javbus 的製作商，把罗马音厂牌名换回日文原名。

用户 2026-09-02 指出 `Celeb no Tomo` 其实是 `セレブの友`。这类罗马音是番号站的转写，
不是厂牌自己的写法，账本不该留着它。判据也由用户当场定下：

- 製作商含汉字或平假名 → 采用日文原名（`セレブの友`、`痴女ヘブン`、`アロマ企画`）。
- 製作商是纯片假名 → 默认当英文品牌的外来语写法，保留账本里的英文原名。
  `ムーディーズ` 不该顶掉 `MOODYZ`，`プレステージ` 不该顶掉 `Prestige`。这只是默认值：
  拉丁名若只是这串片假名的罗马音（`Akinori` 对 `アキノリ`），厂牌自己并不这么写，
  复核时按官网或官方 X 的自称改判（用户 2026-09-23：`PREMIUM` 用英文，`Celeb no Tomo` 用日文）。
- 账本现名本来就含日文 → 不动。

**日文名是查出来的，不是转写出来的。** 罗马音回日文没有唯一解（`Hon Naka` 可以是
`本中` 也可以是`ほんなか`），所以这里不做音译：拿厂牌自己作品的番号去 javbus 读
`製作商` 字段。一个厂牌尽量取两个不同前缀的番号，两页说法一致才敢提改名，
不一致就标出来交给人看——同一个账本厂牌名底下混了两家片商，本身就是要人处理的问题。

javbus 有年龄门，带 `age=verified` cookie 才给内容页，否则只回一张 21 KB 确认页。
番号页走代理。

只产出复核 CSV，不写账本：改厂牌名要同时动 `entity.canonical_name` 与 `asset.studio`，
那是另一次授权的事。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.config import STATE_DIR   # noqa: E402
from peach.jobs import job_main   # noqa: E402
from peach.page_cache import HttpStatusError, Site   # noqa: E402
from peach.review_csv import write_rows   # noqa: E402

FIELDS = ("entity_id", "studio", "assets", "codes", "source_url", "maker",
          "shape", "verdict", "proposed", "evidence")

JAVBUS = "https://www.javbus.com/{}"
JAVBUS_COOKIES = {"age": "verified", "dv": "1", "existmag": "all"}
#: 番号页上「製作商」标签之后的第一个链接文本就是片商名。
MAKER = re.compile(r"製作商.{0,200}?<a[^>]*>([^<]{1,60})</a>", re.S)
JAV_CODE = re.compile(r"^([A-Za-z]{2,6})-(\d{2,5})$")

KANA_KANJI = re.compile(r"[぀-ゟ一-鿿]")   # 平假名或汉字
KATAKANA = re.compile(r"[゠-ヿ]")

RENAME, KEEP_KATAKANA, KEEP_LATIN, KEEP_JP, SPLIT, UNKNOWN, SKIP = (
    "改名", "保留（片假名外来语）", "保留（来源也是拉丁）", "保留（已是日文）",
    "不一致", "未取得", "不适用（非番号体系）")


# ---------------------------------------------------------------- 判定

def shape(name: str) -> str:
    """按用户的判据给名字分类：只有「汉字假名」这一类才值得顶掉英文写法。"""
    if KANA_KANJI.search(name):
        return "汉字假名"
    if KATAKANA.search(name):
        return "片假名"
    return "拉丁"


def decide(current: str, makers: list[str]) -> tuple[str, str, str]:
    """返回 (判词, 建议名, 证据说明)。makers 是各番号页读到的製作商，按出现顺序。"""
    if shape(current) != "拉丁":
        return KEEP_JP, "", "账本现名已含日文"
    found = [m for m in dict.fromkeys(makers) if m]
    if not found:
        return UNKNOWN, "", "番号页没读到製作商"
    if len(found) > 1:
        return SPLIT, "", "多个番号给出不同製作商：" + "／".join(found)
    maker = found[0]
    corroboration = f"{len(makers)} 个番号一致" if len(makers) > 1 else "只有 1 个番号可查"
    if shape(maker) == "片假名":
        return KEEP_KATAKANA, "", f"製作商 {maker} 是纯片假名，按规则保留英文原名（{corroboration}）"
    if maker == current:
        return KEEP_JP, "", f"製作商与现名相同（{corroboration}）"
    if shape(maker) == "拉丁":
        # 来源自己也写拉丁，两边差的只是空格或符号，那不是「去罗马音」，别拿它改名。
        return KEEP_LATIN, "", f"製作商 {maker} 也是拉丁写法，与现名只差写法（{corroboration}）"
    return RENAME, maker, f"製作商 {maker}（{corroboration}）"


# ---------------------------------------------------------------- 取数

def studios(connection: sqlite3.Connection, minimum: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM (
            SELECT e.id AS id, e.canonical_name AS canonical_name,
                   (SELECT COUNT(*) FROM asset a WHERE a.studio = e.canonical_name) AS assets
            FROM entity e WHERE e.kind = 'studio'
        ) WHERE assets >= ? ORDER BY assets DESC, canonical_name
        """,
        (minimum,),
    ).fetchall()


def code_groups(connection: sqlite3.Connection, studio: str, wanted: int,
                depth: int = 3) -> list[list[str]]:
    """给一个厂牌挑几组番号：一个前缀一组，最多 `wanted` 组，组内最多 `depth` 个。

    只有跨前缀才算互相印证——同前缀的两页必然出自同一家，证明不了什么。组内的第二、
    第三个番号不参与印证，只用来顶替 404：番号页不存在是那一页的事，不代表这家查不到。
    """
    rows = connection.execute(
        "SELECT code, COUNT(*) n FROM asset WHERE studio = ? AND code IS NOT NULL AND code <> ''"
        " GROUP BY code ORDER BY n DESC, code",
        (studio,),
    ).fetchall()
    groups: dict[str, list[str]] = {}
    for row in rows:
        match = JAV_CODE.match(str(row["code"]).strip())
        if not match:
            continue
        prefix = match.group(1).upper()
        if prefix not in groups and len(groups) >= wanted:
            continue
        bucket = groups.setdefault(prefix, [])
        if len(bucket) < depth:
            bucket.append(match.group(0).upper())
    return list(groups.values())


def maker_of(page: str) -> str:
    match = MAKER.search(page)
    return match.group(1).strip() if match else ""


def inspect(site: Site, groups: list[list[str]]) -> tuple[list[str], list[str], list[str], list[str]]:
    """每组取到一个製作商就停。返回 (製作商, 证据页 URL, 实际打过的番号, 失败说明)。"""
    makers, urls, tried, notes = [], [], [], []
    for group in groups:
        for code in group:
            url = JAVBUS.format(code)
            tried.append(code)
            try:
                page = site.get(url)
            except (HttpStatusError, OSError, ValueError) as exc:
                notes.append(f"{code}: {type(exc).__name__}: {exc}")
                continue
            maker = maker_of(page)
            if not maker:
                notes.append(f"{code}: 页面无製作商字段")
                continue
            urls.append(url)
            makers.append(maker)
            break
    return makers, urls, tried, notes


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-assets", type=int, default=1)
    parser.add_argument("--codes", type=int, default=2, help="每个厂牌最多查几个不同前缀的番号")
    parser.add_argument("--depth", type=int, default=3, help="同前缀最多试几个番号（只为顶替 404）")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重抓")
    parser.add_argument("--cache-dir", type=Path, default=STATE_DIR / "studio-names")
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".studio-names.lock")
    return parser


def run(args: argparse.Namespace, site: Site | None = None) -> int:
    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    owned = site is None
    if owned:
        site = Site(args.cache_dir, args.interval, args.timeout,
                    refresh=args.refresh, via_proxy=True, cookies=JAVBUS_COOKIES)
    rows, stats = [], Counter()
    try:
        targets = studios(connection, args.min_assets)
        if args.limit:
            targets = targets[:args.limit]
        for entity in targets:
            name = entity["canonical_name"]
            if shape(name) != "拉丁":
                stats[KEEP_JP] += 1
                continue
            groups = code_groups(connection, name, args.codes, args.depth)
            if not groups:
                # Blacked、Tushy、FC2-PPV 这些压根不走番号体系，回查无从谈起。
                # 写「未取得」等于把「不适用」伪装成取证失败。
                makers, urls, tried, notes = [], [], [], []
                verdict, proposed, evidence = SKIP, "", "账本里没有 JAV 番号，不走番号体系"
            else:
                makers, urls, tried, notes = inspect(site, groups)
                verdict, proposed, evidence = decide(name, makers)
            stats[verdict] += 1
            rows.append({
                "entity_id": entity["id"], "studio": name, "assets": entity["assets"],
                "codes": " ".join(tried), "source_url": " ".join(urls),
                "maker": "／".join(dict.fromkeys(makers)), "shape": shape(name),
                "verdict": verdict, "proposed": proposed,
                "evidence": "；".join([evidence, *notes]),
            })
    finally:
        if owned:
            site.close()
        connection.close()

    order = {RENAME: 0, SPLIT: 1, UNKNOWN: 2, KEEP_KATAKANA: 3, KEEP_LATIN: 4,
             KEEP_JP: 5, SKIP: 6}
    rows.sort(key=lambda item: (order.get(item["verdict"], 9), -int(item["assets"]), item["studio"]))
    write_rows(args.output, FIELDS, rows)
    print({"复核行": len(rows), **stats, "取页": site.fetched, "命中缓存": site.cached,
           "output": str(args.output)})
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(job_main(build_parser, run))
