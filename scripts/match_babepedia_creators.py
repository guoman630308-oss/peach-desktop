#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""把 ledger 里的拉丁名出演者与网黄博主回配到 babepedia，产出候选 CSV。

判据只看 `<title>`，三条实测教训都写死在代码里：

1. **HTTP 状态码没有区分力**：档案页、搜索结果页、垃圾串查询全都返回 200。
2. **标题后缀有两种**，写死一种会稳定漏掉一整类真实档案：

       Tania Shinaryen  - Free nude pics, galleries & more at Babepedia
       Brianna Marchant - Free pics,      galleries & more at Babepedia

3. **页面上的 `/babe/` 链接全是 `thumbshot` 相关推荐**，不是搜索结果。裸抓链接会
   拿到与查询毫无关系的人（实测查 `SexySaffron` 抓出 `Brianna_Marchant`），绝不采信。

限流必须单独成一类，永远不能折叠进「无档案」。Cloudflare 返回 429 加
`Just a moment...` 挑战页，标题匹配不上——若按「匹配不上即否定」处理，限流期间
的查询会被静默记成确认无档案。r18 那次 556 位里 203 位假阴性就是这么来的。

名字写法三方不一致，所以按可解释的规则生成变体，命中即停：

    Shinaryen    -> Tania Shinaryen    原名直接解析
    SexySaffron  -> Sexy Saffron       驼峰拆词后解析到别名 Saffron Bacchus
    ruth_lee     -> ruth lee -> Ruth Lee
    MattieDoll   -> MattieDoll         站上就这么写，不能拆

别名解析会把查询带到另一个名字上，这是能力也是风险。因此记录命中用的变体、
返回的档案名和两者的词元重合度；重合度为 0 的一律标 `需人工确认`，不当作已确认。

只产出候选 CSV，不写 ledger、不下载图片。
"""
from __future__ import annotations

import argparse
import re
import time
import urllib.parse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.http import HttpRequest, HttpTransport, HttpxTransport
from peach.review_csv import read_rows, write_rows
from peach.scripting import USER_AGENT, open_readonly

BASE = "https://www.babepedia.com/babe/"

#: 档案页标题。`nude` 可有可无——两种写法都是真实档案。
PROFILE_TITLE = re.compile(
    r"<title>\s*(.*?)\s*-\s*Free (?:nude )?pics, galleries & more at Babepedia",
    re.S | re.I)
#: 搜索结果页标题，代表这个写法查无此人（但不代表别的写法也查无此人）。
SEARCH_TITLE = re.compile(r"<title>\s*Babepedia - Search results for", re.S | re.I)
#: Cloudflare 挑战页。必须先于其它判断，否则限流会被记成否定结论。
CHALLENGE = re.compile(r"Just a moment|cf-browser-verification|Checking your browser", re.I)

LATIN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 ._'-]*$")

VERDICT_HIT = "命中"
VERDICT_REVIEW = "需人工确认"
VERDICT_NONE = "确认无档案"
VERDICT_BLOCKED = "未取得:限流"


#: 截断后短于这个长度的写法不再查询。`G3104` 削成 `G`、`N1032` 削成 `N` 之后
#: 已经不指向任何人，只会撞上无关档案并招来限流——实测三个「限流未取得」全是它们。
MIN_TRUNCATED = 4


def name_variants(name: str) -> list[tuple[str, bool]]:
    """返回 (写法, 是否有损)，保持顺序、去重。

    前三种只改分隔与空格，字母数字一个不少，属于无损改写；去尾部数字丢掉了识别
    信息，属于有损。有损写法查出来的结果不能单独构成确认结论：`fantia-3760310`
    被削成 `fantia` 后会撞上艺名里含 `Fantia` 的人，重合度还够高，足以骗过闸门。
    """
    text = (name or "").strip()
    spaced = re.sub(r"[._-]+", " ", text)
    camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    truncated = re.sub(r"[\s._-]*\d+$", "", spaced).strip()
    candidates: list[tuple[str, bool]] = [
        (text, False), (spaced, False), (camel, False),
        (re.sub(r"[._-]+", " ", camel), False),
    ]
    if len(truncated.replace(" ", "")) >= MIN_TRUNCATED:
        candidates.append((truncated, True))

    seen: set[str] = set()
    result: list[tuple[str, bool]] = []
    for candidate, lossy in candidates:
        value = " ".join(candidate.split())
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            result.append((value, lossy))
    return result


def tokens(value: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (value or "").casefold()) if len(t) > 2}


class RateLimited(RuntimeError):
    pass


def fetch_title(transport: HttpTransport, query: str, timeout: int = 30) -> str | None:
    """返回档案名；查无此人返回 None；被限流抛 RateLimited。"""
    url = BASE + urllib.parse.quote(query)
    response = transport(
        HttpRequest("GET", url, {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}),
        timeout, 4 * 1024 * 1024,
    )
    body = response.body.decode("utf-8", "ignore")
    if response.status == 429 or CHALLENGE.search(body[:4000]):
        raise RateLimited(query)
    match = PROFILE_TITLE.search(body)
    if match:
        return match.group(1).strip()
    if SEARCH_TITLE.search(body):
        return None
    # 既不是档案页也不是搜索页：形态未知，按未取得处理而不是否定。
    raise RateLimited(f"{query}: 未知页面形态 HTTP {response.status}")


def resolve(transport: HttpTransport, name: str, delay: float,
            retries: int = 3) -> tuple[str, str, str, float]:
    """返回 (判定, 命中变体, 档案名, 词元重合度)。"""
    for variant, lossy in name_variants(name):
        # 重合度按实际命中的变体算：`SexySaffron` 不含分隔符，只切得出一个词元，
        # 拿它去比 `Saffron Bacchus` 会得 0，把真命中误判成需人工确认。
        query_tokens = tokens(name) | tokens(variant)
        backoff = delay
        for attempt in range(retries):
            try:
                found = fetch_title(transport, variant)
            except RateLimited:
                if attempt == retries - 1:
                    return VERDICT_BLOCKED, variant, "", 0.0
                time.sleep(backoff)
                backoff *= 3
                continue
            time.sleep(delay)
            if found is None:
                break
            overlap = (len(query_tokens & tokens(found)) / len(query_tokens)
                       if query_tokens else 0.0)
            # 有损写法即使词元对得上也只是线索：削掉数字后剩下的可能是个通用词。
            verdict = (VERDICT_REVIEW if lossy or overlap == 0 else VERDICT_HIT)
            return verdict, variant, found, round(overlap, 2)
    return VERDICT_NONE, "", "", 0.0


def candidates(database: Path) -> list[tuple[int, str, int]]:
    connection = open_readonly(database)
    try:
        rows = connection.execute(
            """SELECT e.id, e.canonical_name,
                      (SELECT COUNT(*) FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id
                       WHERE ae.entity_id=e.id AND a.medium='video')
               FROM entity e WHERE e.kind IN ('creator','performer') ORDER BY 3 DESC"""
        ).fetchall()
    finally:
        connection.close()
    return [(int(i), str(n), int(c)) for i, n, c in rows
            if LATIN_NAME.match((n or "").strip()) and c > 0]


FIELDS = ("entity_id", "creator", "videos", "verdict", "matched_variant",
          "babepedia_name", "token_overlap", "portrait_url", "profile_url")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把拉丁名出演者与网黄博主回配到 babepedia")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--out", type=Path,
                        default=GENERATED_DIR / "babepedia-candidates.csv")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="请求间隔秒；实测 1.8 秒并发其它探测时会被 Cloudflare 打到 429")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true",
                        help="跳过 CSV 里已有确定结论的行；限流行会重试")
    return parser


def load_done(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    return {row["creator"]: row for row in read_rows(path)
            if row.get("verdict") != VERDICT_BLOCKED}


def run(args: argparse.Namespace) -> int:
    todo = candidates(args.db)
    done = load_done(args.out) if args.resume else {}
    if args.limit:
        todo = todo[:args.limit]
    print(f"拉丁名人物候选 {len(todo)} 个（已完成 {len(done)} 个），间隔 {args.delay}s")

    transport = HttpxTransport()
    rows: list[dict] = []
    counts: dict[str, int] = {}
    try:
        for index, (entity_id, name, videos) in enumerate(todo, 1):
            if name in done:
                rows.append({**done[name], 'entity_id': entity_id, 'videos': videos})
                continue
            verdict, variant, found, overlap = resolve(transport, name, args.delay)
            counts[verdict] = counts.get(verdict, 0) + 1
            slug = urllib.parse.quote(found.replace(" ", "_")) if found else ""
            rows.append({
                "entity_id": entity_id, "creator": name, "videos": videos,
                "verdict": verdict, "matched_variant": variant,
                "babepedia_name": found, "token_overlap": overlap,
                "portrait_url": (f"https://www.babepedia.com/pics/"
                                 f"{urllib.parse.quote(found)}.jpg" if found else ""),
                "profile_url": f"https://www.babepedia.com/babe/{slug}" if slug else "",
            })
            if verdict in (VERDICT_HIT, VERDICT_REVIEW):
                print(f"[{index}/{len(todo)}] {verdict} {name} -> {found} "
                      f"（写法 {variant}，重合 {overlap}，{videos} 部）", flush=True)
            elif verdict == VERDICT_BLOCKED:
                print(f"[{index}/{len(todo)}] 限流未取得 {name}", flush=True)
            _write(args.out, rows)
    finally:
        transport.close()
        _write(args.out, rows)

    print("\n判定分布：", counts)
    print(f"候选 CSV → {args.out}")
    print("这是候选，不是真相：命中项仍需人工确认后才能写 ledger 或下载头像。")
    return 0


def _write(path: Path, rows: list[dict]) -> None:
    write_rows(path, FIELDS, rows)


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
