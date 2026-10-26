"""从 javdatabase 采女优的日文名与别名候选，用账本自己的番号做入口。

用户 2026-09-02 要求把 javdatabase 加进候选。它不是社媒来源（每个 idol 页的外链只有
站方自己的 `@JAVDatabase`），能给的是名字：idol 页上一行 `JP: 凰華りん - Alt: Rin Natsuki`
把罗马字艺名、日文原名和旧名摆在一起，正是账本里最缺的那一类别名——`夏木铃` 已有
`Rin Natsuki／なつきりん／凰華りん／夏木りん`，但没有 `凰華りん` 的罗马字 `Rin Oka`。

**入口必须是账本里的番号，不能猜 slug。** 实测 `/idols/rin-natsuki/` 打开的是
`Rin Oka` 的页面：javdatabase 一个艺名一页，slug 与人不是一对一，按名字拼 slug 会
稳稳取到另一个人的资料。站内搜索也不给 idol 页，只回作品列表。所以链路是
番号 → `/movies/<code>/` → 页面上的 idol 页 → 名字，每一步都由上一步的页面给出。

匹配要求 idol 页的名字里至少有一个已经在账本这个人的名字链上，否则只当页面没对上人：
一部作品可以挂好几位女优，不能因为番号对上就把整页名字都装到这个人身上。
一个 idol 页对上账本多个人时记「需人工消歧」，不猜。

只产出复核 CSV，不写账本：写别名是另一次授权的事。
"""
from __future__ import annotations

import argparse
import html as html_lib
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.catalog_rules import is_jav_code   # noqa: E402
from peach.config import STATE_DIR   # noqa: E402
from peach.jobs import job_main   # noqa: E402
from peach.page_cache import HttpStatusError, Site   # noqa: E402
from peach.review_csv import write_rows   # noqa: E402
from peach.social_links import load_performers, name_key   # noqa: E402

FIELDS = ("entity_id", "name", "assets", "code", "movie_url", "idol_url", "idol_name",
          "field", "candidate", "verdict", "evidence")

MOVIE = "https://www.javdatabase.com/movies/{}/"
IDOL_LINK = re.compile(r'href="(https://www\.javdatabase\.com/idols/[a-z0-9-]+/)"')
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
#: 资料行的真实写法是 `<b>JP:</b> 涼森れむ <br>` 与
#: `<b>Alt:</b> Iwatani Shiki, Arina Hashimoto, Mana Kaminogi<br>`（两段都可能缺）。
#: 标签名必须跳过：按 `JP:\s*([^<-]+)` 写的话，紧跟的 `</b>` 让它一条都匹配不上，
#: 全站只回罗马字，而正是日文名和旧艺名才是这个来源的价值。
JP_FIELD = re.compile(r"JP:\s*(?:</[a-z]+>)?\s*([^<]{1,60})")
ALT_FIELD = re.compile(r"Alt:\s*(?:</[a-z]+>)?\s*([^<]{1,160})")
TAG = re.compile(r"<[^>]+>")

OK, HELD, AMBIGUOUS, MISSING = "ok", "已有", "需人工消歧", "未取得"


# ---------------------------------------------------------------- 解析

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(TAG.sub(" ", text))).strip()


def split_names(text: str) -> list[str]:
    """`Alt:` 一栏可能列好几个名字。不按空格拆——罗马字里本来就有空格。

    两栏之间还有个连字符（`新ありな  - <b>Alt:</b> …`），落在 `JP:` 的捕获尾巴上，
    所以两端的连字符要削掉；名字本身不会以连字符开头或结尾。
    """
    parts = (part.strip().strip("-–—").strip() for part in re.split(r"[、,，/／|｜;；]", text))
    return [part for part in parts if part]


def idol_links(html: str) -> list[str]:
    return list(dict.fromkeys(IDOL_LINK.findall(html)))


def idol_names(html: str) -> list[tuple[str, str]]:
    """idol 页 → [(字段, 名字)]，字段是 `romaji`／`jp`／`alt`，按页面出现顺序。"""
    found: list[tuple[str, str]] = []
    title = H1.search(html)
    if title:
        # 标题写成 `Rin Oka - JAV Profile`，后半截是栏目名不是名字。
        head = clean(title.group(1)).split(" - ")[0]
        if head:
            found.append(("romaji", head))
    japanese = JP_FIELD.search(html)
    if japanese:
        found += [("jp", value) for value in split_names(clean(japanese.group(1)))]
    alternate = ALT_FIELD.search(html)
    if alternate:
        found += [("alt", value) for value in split_names(clean(alternate.group(1)))]
    return [(field, value) for field, value in dict.fromkeys(found) if value]


# ---------------------------------------------------------------- 取数

def codes_of(connection: sqlite3.Connection, entity_id: int, wanted: int) -> list[str]:
    """这个人的番号，按作品数排。同一部片的多个文件只算一个番号。"""
    rows = connection.execute(
        "SELECT a.code, count(*) n FROM asset a JOIN asset_entity ae ON ae.asset_id = a.id"
        " WHERE ae.entity_id = ? AND a.code IS NOT NULL AND a.code <> ''"
        " GROUP BY a.code ORDER BY n DESC, a.code",
        (entity_id,),
    ).fetchall()
    codes = [str(row[0]).strip().upper() for row in rows if is_jav_code(str(row[0]).strip())]
    return list(dict.fromkeys(codes))[:wanted]


def held_names(connection: sqlite3.Connection) -> dict[int, list[str]]:
    """每位 performer 账本里已有的全部名字：规范名加全部别名。

    这里不能用 `load_performers` 给的 `chain`——它按设计把罗马字剔掉了（拿罗马字去日文
    站查是白跑）。但判断「这个名字账本有没有」必须连罗马字一起看，否则 `Rin Natsuki`
    明明已在 `entity_alias` 里，还会被当成新别名报一遍。
    """
    names: dict[int, list[str]] = {}
    for entity_id, name in connection.execute(
            "SELECT id, canonical_name FROM entity WHERE kind = 'performer'"):
        names.setdefault(int(entity_id), []).append(str(name))
    for entity_id, alias in connection.execute(
            "SELECT entity_id, alias FROM entity_alias ORDER BY entity_id, alias"):
        names.setdefault(int(entity_id), []).append(str(alias))
    return names


def index_names(names: dict[int, list[str]]) -> dict[str, set[int]]:
    index: dict[str, set[int]] = {}
    for entity_id, values in names.items():
        for value in values:
            key = name_key(value)
            if key:
                index.setdefault(key, set()).add(entity_id)
    return index


def fetch(site: Site, url: str) -> tuple[str, str]:
    """返回 (页面, 失败说明)。番号在 javdatabase 没收录是常态，不是异常。"""
    try:
        return site.get(url), ""
    except HttpStatusError as exc:
        return "", f"HTTP {exc.status}"
    except Exception as exc:   # 单页失败只记未取得，不拖垮整批
        return "", f"{type(exc).__name__}: {exc}"[:160]


# ---------------------------------------------------------------- 判定

def row(**values) -> dict:
    record = {field: "" for field in FIELDS}
    record.update(values)
    return record


def harvest(site: Site, connection: sqlite3.Connection, performers: list[dict],
            codes_per_performer: int) -> tuple[list[dict], Counter]:
    ledger_names = held_names(connection)
    index = index_names(ledger_names)
    rows: list[dict] = []
    stats: Counter = Counter()
    seen_idol: dict[str, list[tuple[str, str]]] = {}
    for record in performers:
        entity_id = record["entity_id"]
        base = {"entity_id": entity_id, "name": record["name"], "assets": record["assets"]}
        held = {name_key(name) for name in ledger_names.get(entity_id, ()) if name_key(name)}
        codes = codes_of(connection, entity_id, codes_per_performer)
        if not codes:
            rows.append(row(**base, verdict=MISSING, evidence="账本里没有 JAV 番号可做入口"))
            stats["无番号入口"] += 1
            continue
        notes: list[str] = []
        matched = False
        for code in codes:
            movie_url = MOVIE.format(code.lower())
            page, failure = fetch(site, movie_url)
            if failure:
                notes.append(f"{code}: {failure}")
                continue
            links = idol_links(page)
            if not links:
                notes.append(f"{code}: 作品页没有 idol 链接")
                continue
            for idol_url in links:
                if idol_url not in seen_idol:
                    idol_page, idol_failure = fetch(site, idol_url)
                    if idol_failure:
                        notes.append(f"{idol_url}: {idol_failure}")
                        continue
                    seen_idol[idol_url] = idol_names(idol_page)
                names = seen_idol[idol_url]
                keys = {name_key(value) for _, value in names}
                if not keys & held:
                    # 一部片挂多位女优，对不上名字的那几页不属于这个人。
                    continue
                owners = set().union(*(index.get(key, set()) for key in keys)) or {entity_id}
                display = next((value for field, value in names if field == "romaji"), "")
                where = f"{movie_url} → {idol_url}"
                if len(owners) > 1:
                    others = "、".join(ledger_names[other][0] for other in sorted(owners)
                                      if ledger_names.get(other))
                    rows.append(row(**base, code=code, movie_url=movie_url, idol_url=idol_url,
                                    idol_name=display, verdict=AMBIGUOUS,
                                    evidence=f"{where}；页面名字同时对上：{others}"))
                    stats[AMBIGUOUS] += 1
                    matched = True
                    continue
                matched = True
                for field, value in names:
                    verdict = HELD if name_key(value) in held else OK
                    rows.append(row(**base, code=code, movie_url=movie_url, idol_url=idol_url,
                                    idol_name=display, field=field, candidate=value,
                                    verdict=verdict, evidence=where))
                    stats[verdict] += 1
                    if verdict == OK:
                        # 同一批里别把同一个新名字重复报两遍。
                        held.add(name_key(value))
            if matched:
                break
        if not matched:
            rows.append(row(**base, code=" ".join(codes), verdict=MISSING,
                            evidence="；".join(notes) or "作品页上的 idol 都对不上账本这个人"))
            stats["未对上"] += 1
    return rows, stats


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-assets", type=int, default=1)
    parser.add_argument("--codes", type=int, default=2, help="每人最多用几个番号做入口")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", help="只查这些 entity_id（逗号分隔），用来先跑小批")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重抓")
    parser.add_argument("--cache-dir", type=Path, default=STATE_DIR / "javdatabase-names")
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".javdatabase-names.lock")
    return parser


def run(args: argparse.Namespace, site: Site | None = None) -> int:
    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    owned = site is None
    if owned:
        site = Site(args.cache_dir, args.interval, args.timeout,
                    refresh=args.refresh, via_proxy=True)
    try:
        performers = load_performers(connection, args.min_assets)
        if args.only:
            wanted = {int(part) for part in args.only.split(",") if part.strip()}
            performers = [record for record in performers if record["entity_id"] in wanted]
        if args.limit:
            performers = performers[:args.limit]
        rows, stats = harvest(site, connection, performers, args.codes)
    finally:
        if owned:
            site.close()
        connection.close()

    order = {OK: 0, AMBIGUOUS: 1, MISSING: 2, HELD: 3}
    rows.sort(key=lambda item: (order.get(item["verdict"], 9), -int(item["assets"]),
                                str(item["entity_id"])))
    write_rows(args.output, FIELDS, rows)
    print({"复核行": len(rows), **stats, "取页": site.fetched, "命中缓存": site.cached,
           "重试": site.retried, "output": str(args.output)})
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(job_main(build_parser, run))
