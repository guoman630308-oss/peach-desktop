"""从厂牌官网找出其社交账号，穿过 18+ 年龄门。

只采信**厂牌自有域名页面上**的社交链接。维基外链里混着引用来源的新闻站，
直接抓会把新闻站自己的账号当成厂牌的——实测 `妄想族` 条目就抓出了
`@news_postseven` 与 `@taishurxjp`。

AV 厂牌官网普遍先给一个年龄确认页：肯定链接写「はい（入室する）」指向站内，
否定链接指向 dmm.com。不穿过它只能拿到约 10 KB 的空壳页。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.http import HttpRequest, HttpxTransport, body_text   # noqa: E402
from peach.review_csv import read_rows, write_rows
from peach.scripting import USER_AGENT
from peach.social_links import NON_HANDLES, handle

SOCIAL = re.compile(r'https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})/?(?:[?#].*)?$', re.I)
AFFIRMATIVE = re.compile(r"はい|入室|入場|入る|同意|年齢認証|(?<![a-z])(?:ENTER|YES)(?![a-z])"
                         r"|18\s*歳以上|over\s*18", re.I)
NEGATIVE = re.compile(r"いいえ|退出|戻る|(?<![a-z])NO\b|18\s*歳未満", re.I)
MAX_HOPS = 2
SCAN_FIELDS = ("studio", "site", "final_url", "handles", "note")
REVIEW_FIELDS = ("entity_id", "kind", "name", "link_kind", "label", "url", "evidence", "context", "review")


def affirmative_link(html: str, base: str) -> str | None:
    """返回年龄确认页的「进入」链接；不是年龄门就返回 None。

    判据是锚文本而不是 URL：否定链接通常指向站外（dmm.com），
    肯定链接指向站内，两者的 href 本身看不出区别。
    """
    host = urlsplit(base).hostname or ""
    for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
        text = anchor.get_text(" ", strip=True) + " ".join(
            str(img.get("alt", "")) for img in anchor.select("img"))
        text = re.sub(r"は\s+い", "はい", text)
        if not AFFIRMATIVE.search(text) or NEGATIVE.search(text):
            continue
        target = urljoin(base, str(anchor["href"]))
        if urlsplit(target).scheme in {"http", "https"} and (urlsplit(target).hostname or "") == host:
            return target
    return None


def handles_in(html: str) -> set[str]:
    return {item["handle"] for item in accounts_in(html)}


def accounts_in(html: str) -> list[dict[str, str]]:
    """提取账号主页锚点及原文；帖子、分享组件与脚本内容不算账号入口。"""
    found = {}
    for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
        url = str(anchor.get("href", "")).strip()
        if url.startswith("//"):
            url = "https:" + url
        match = SOCIAL.fullmatch(url)
        if not match or match[1].casefold() in NON_HANDLES:
            continue
        shown = match[1]
        text = anchor.get_text(" ", strip=True)
        alt = " ".join(str(img.get("alt", "")) for img in anchor.select("img"))
        item = {"handle": shown, "anchor": (text or alt).strip(),
                "context": anchor.parent.get_text(" ", strip=True)[:600]}
        previous = found.get(shown.casefold())
        if previous is None or (not previous["anchor"] and item["anchor"]):
            found[shown.casefold()] = item
    return list(found.values())


def scan(http, site: str, timeout: float, *, evidence: list | None = None) -> tuple[set[str], str, str]:
    """返回（找到的 handle, 最终 URL, 说明）。"""
    current, note = site, ""
    found: set[str] = set()
    visited = set()
    host = (urlsplit(site).hostname or "").removeprefix("www.")
    for hop in range(MAX_HOPS + 1):
        visited.add(current)
        response = http(HttpRequest("GET", current, {"User-Agent": USER_AGENT}), timeout, 8 << 20)
        current = response.url or current
        if response.status != 200:
            return found, current, f"未取得：HTTP {response.status}"
        if (urlsplit(current).hostname or "").removeprefix("www.") != host:
            return found, current, "未取得：重定向离开官网域名"
        html = body_text(response.body, response.headers)
        accounts = accounts_in(html)
        found.update(item["handle"] for item in accounts)
        if evidence is not None:
            for item in accounts:
                evidence.append({**item, "page": current,
                                 "sha256": hashlib.sha256(response.body).hexdigest()})
        following = affirmative_link(html, current)
        if following is None or following in visited or hop == MAX_HOPS:
            return found, current, note or ("官网页面直接给出社交链接" if found else "页面无社交链接")
        note = f"穿过 {hop + 1} 层年龄门"
        current = following
    return found, current, note


def ledger_sites(connection: sqlite3.Connection) -> list[dict]:
    """读取厂牌官网及已收录的 X 账号；有社媒的厂牌同样核查多账号遗漏。"""
    rows = []
    for eid, name, site in connection.execute(
            "SELECT e.id,e.canonical_name,l.url FROM entity e JOIN entity_link l ON l.entity_id=e.id "
            "WHERE e.kind='studio' AND l.link_kind='official' ORDER BY e.id,l.id"):
        known = {handle(url) for (url,) in connection.execute(
            "SELECT url FROM entity_link WHERE entity_id=? AND link_kind='social'", (eid,))
                 if (urlsplit(url).hostname or "").removeprefix("www.") in {"x.com", "twitter.com"}}
        rows.append({"entity_id": eid, "studio": name, "site": site, "known": known})
    return rows


def review_rows(row: dict, evidence: list[dict]) -> list[dict]:
    """生成安装器可读的待复核表；归属须结合锚文本确认，不能整页自动采信。"""
    known = set(row.get("known", ()))
    result = []
    for item in evidence:
        if item["handle"].casefold() in known:
            continue
        known.add(item["handle"].casefold())
        result.append({"entity_id": row.get("entity_id", ""), "kind": "studio",
                       "name": row["studio"], "link_kind": "social",
                       "label": f"X @{item['handle']}", "url": f"https://x.com/{item['handle']}",
                       "evidence": f"{item['page']} ; sha256={item['sha256']} ; {item['anchor']}",
                       "context": item["context"], "review": "待复核账号归属"})
    return result


def parse_args():
    """校验采集输入及进度文件，避免覆盖来源。"""
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="含 studio,site 两列的 CSV")
    source.add_argument("--db", type=Path, help="只读账本里的厂牌官网")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-output", type=Path, help="待复核链接表，确认归属后交 install_entity_links.py")
    parser.add_argument("--resume", action="store_true", help="复用有账号证据的行；空结果与失败重新检查")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()
    source_path = (args.db or args.input).resolve()
    if any(path and path.resolve() == source_path for path in (args.output, args.review_output)):
        parser.error("输出文件不能覆盖输入文件或账本")
    if args.review_output and args.output.resolve() == args.review_output.resolve():
        parser.error("扫描表与复核表必须使用不同路径")
    if args.resume and args.output.exists() and args.review_output and not args.review_output.exists():
        parser.error("续跑缺少原复核表；去掉 --resume 可重新采集")
    return args


def input_rows(args) -> list[dict]:
    """从只读账本或官网清单取得本轮范围。"""
    if args.db:
        with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            return ledger_sites(connection)
    return [row for row in read_rows(args.input) if (row.get("site") or "").strip()]


def main() -> int:
    args = parse_args()
    rows = input_rows(args)
    results = read_rows(args.output) if args.resume and args.output.exists() else []
    reviews = read_rows(args.review_output) if args.resume and args.review_output and args.review_output.exists() else []
    settled = {(r["studio"], r["site"]) for r in results
               if r["handles"] and not r["note"].startswith("未取得")}
    http = HttpxTransport()
    last = 0.0
    try:
        for row in rows:
            studio, site = (row.get("studio") or "").strip(), (row["site"]).strip()
            if (studio, site) in settled:
                continue
            wait = args.interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            evidence: list[dict] = []
            try:
                found, final, note = scan(http, site, args.timeout, evidence=evidence)
            except Exception as exc:
                found, final, note = set(), "", f"未取得：{type(exc).__name__}"
            results = [r for r in results if (r["studio"], r["site"]) != (studio, site)]
            results.append({"studio": studio, "site": site, "final_url": final,
                            "handles": "|".join(sorted(found)), "note": note})
            seen = {(str(r["entity_id"]), r["name"], r["url"].casefold()) for r in reviews}
            reviews.extend(r for r in review_rows(row, evidence)
                           if (str(r["entity_id"]), r["name"], r["url"].casefold()) not in seen)
            write_rows(args.output, SCAN_FIELDS, results)
            if args.review_output:
                write_rows(args.review_output, REVIEW_FIELDS, reviews)
            print(f"{studio:<20} {'|'.join(sorted(found)) or '未取得':<28} {note}")
    finally:
        http.close()

    write_rows(args.output, SCAN_FIELDS, results)
    if args.review_output:
        write_rows(args.review_output, REVIEW_FIELDS, reviews)
    print({"total": len(results), "with_handles": sum(1 for r in results if r["handles"]),
           "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
