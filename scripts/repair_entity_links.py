"""全库审计 `entity_link`，并把能确证的那几类修法落成计划。

用户在一位女优的资料页上点开 official 链接，落到了事务所的全站模特列表。那条链接
200、有图、也确实是那家事务所——从后台看不出任何异常。他的要求是「检查所有的并修复」，
所以这里不挑单条，而是把全库按形状过一遍。

## 问题分四类，处置各不相同

- **目录页**：链接落在「有哪些人」那一层。修法是在站点索引里按名字找回本人页。
- **检索页**：把「这个人在那边是谁」交给站内检索去猜。只有账本里存着那个站的 id
  才换成直达页；没有 id 就标 `保留检索页` 交给用户，**不按名字拼一个直达地址**——
  拼出来的地址打开是 404 还算好的，打开是别人才麻烦。
- **明文 http**：站点已提供 https 时升级。
- **HTML 实体未还原**：`model.php?alias=x&amp;id=176` 是从页面源码里抄地址时把 `&amp;`
  一起抄了进来，服务端收到的参数名成了 `amp;id`，`id` 因此丢失。

同一实体同一站点有多条链接只报不改：那可能是同一个人的两个账号，也可能有一条是别人
的，两种都要人看过才知道，脚本删哪一条都是替用户做决定。

## 换地址前必须过的那一关

新地址要回 200，**并且**页面标题自述这个人。标题这一关不能省：列表页同样回 200，
少了它，一批人会被改写成同一个列表地址，而且每一条都「能打开」。

纯粹换协议或还原实体时，新旧是同一页，标题里未必有人名（博客的标题常是栏目名）。
这时改判「新地址的标题和旧地址完全一致」——那是比人名更强的同一页证据。两条都不
成立就写 `未取得`，不进计划。

限流按 `peach-batch-jobs`：同一域名两次请求至少隔 `--interval` 秒，整轮有
`--max-requests` 上限，取回的页面在本轮内缓存（几十条链接共用同一张索引页）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import ssl
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.entities import name_chain   # noqa: E402
from peach.http import HttpRequest, HttpxTransport, response_text   # noqa: E402
from peach.link_repair import (   # noqa: E402
    anchors_naming, bare_root, confirms, directory_reason, empty_path_segment,
    html_entity_leak, index_candidates, is_search_page, page_title, same_document_path,
    same_site, upgraded_scheme, without_entities,
)
from peach.review_csv import write_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    BACKUP_REQUIRED, HostLimiter, USER_AGENT, add_ledger_write_args, counts_of,
    open_for_write, open_readonly, verify_after_write,
)

DIRECTORY = "目录页"
SEARCH = "检索页"
PLAIN_HTTP = "明文 http"
ENTITY_LEAK = "HTML 实体未还原"
EMPTY_SEGMENT = "路径含空段"
DUPLICATE = "同实体同站点多条"

FIX_REDISCOVER = "站内索引按名字重探直达页"
FIX_DIRECT_ID = "换成账本 id 拼的直达页"
FIX_KEEP_SEARCH = "保留检索页"
FIX_HTTPS = "升级到 https"
FIX_ENTITY = "还原 HTML 实体"
FIX_REPORT_ONLY = "只报，等用户决定"

#: 一条链接可能同时犯几条。修法按这个顺序挑主修法：重探出来的地址本来就是站点现在
#: 给的形态，协议和实体的问题跟着一起没了，反过来不成立。
PRIORITY = (DIRECTORY, SEARCH, ENTITY_LEAK, PLAIN_HTTP)
#: 只报不改的那几类。
REPORT_ONLY = (EMPTY_SEGMENT, DUPLICATE)

#: 检索页换直达页要靠账本里存着的站内 id。表里只放**已经确证过模板形态**的站；
#: 一个站没进这张表，它的检索页就标 `保留检索页`，不拿名字去拼。
DIRECT_PAGES: dict[str, tuple[str, str]] = {
    "video.dmm.co.jp": ("dmm", "https://video.dmm.co.jp/av/list/?actress={id}"),
}

FIELDS = ("link_id", "entity_id", "kind", "name", "link_kind", "label", "url",
          "problem", "fix", "evidence", "new_url", "verdict", "check")
MAX_BYTES = 4 << 20
#: 一条链接最多试这么多个同站候选。索引页上同名的锚点通常只有一两个。
MAX_CANDIDATES = 20


def title_names(name: str, aliases: list[str]) -> list[str]:
    """拿去和页面标题比对的名字，可用程度高的在前。

    `name_chain` 把罗马字排除在外，那是给日文站检索定的规矩：拿罗马字去搜只会白跑一趟。
    标题比对不受它约束，而事务所和厂牌的规范名恰恰常常就是罗马字（`T-POWERS`、
    `LIGHT promotion`）——照搬那条规矩，这些实体一个名字都拿不到，它们的官网首页会全部
    落进「没有可用于日文站的名字」，看起来像是查不到，实际是我们从没拿它们的名字比过。

    兜底只收三个字符以上的写法：两三个字母在任何一条标题里都撞得上。
    """
    chain = name_chain(name, aliases)
    return chain or [text for text in [name, *aliases] if len(text.strip()) >= 3]


def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trust_context() -> ssl.SSLContext:
    """按本机系统信任库做严格校验。

    httpx 默认只信 certifi，而 `www.crusegroup.net` 这类站少发中间证书，certifi 一侧
    验不过——浏览器靠自己缓存的中间证书补上，所以在浏览器里看不出问题。用系统库是把
    判据对齐到这台机器平时的信任面，**不是**关掉校验：校验一律严格，验不过就写
    `未取得` 并把原始错误留在复核表里，绝不改用 http 的成功去声称 https 通了。
    """
    return ssl.create_default_context()


class Fetcher:
    """按域名限速、带本轮缓存和总量上限的取页器。

    缓存不是优化：四十多条目录页链接里有一大半指向同一张索引页，不缓存就是对同一个
    地址发几十次请求，而限速让这件事既慢又显眼。
    """

    def __init__(self, context: ssl.SSLContext, interval: float, timeout: float, budget: int):
        self._context = context
        self._limiter = HostLimiter({}, default_interval=interval)
        self._timeout = timeout
        self._budget = budget
        self._cache: dict[str, tuple[int, str, str]] = {}
        self.used = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self._budget

    def get(self, url: str) -> tuple[int, str, str]:
        """(status, 正文, 最终地址)。取不到时 status 为 0，正文为空。"""
        if url in self._cache:
            return self._cache[url]
        if self.exhausted:
            return 0, "", url
        self._limiter.wait(url)
        self.used += 1
        try:
            # 每个请求一个 client。共用一个池时，连不上的那些主机会把池槽漏掉，几十条
            # 之后所有请求一律 `PoolTimeout`——那看起来像整批站点集体挂了，而实际上
            # 只是没有空槽可用了。这一批本来就按域名限速在跑，握手的开销无关紧要。
            transport = HttpxTransport(httpx.Client(
                verify=self._context, headers={"User-Agent": USER_AGENT},
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=0)),
                owns_client=True)
            try:
                response = transport(
                    HttpRequest("GET", url, {"User-Agent": USER_AGENT}),
                    self._timeout, MAX_BYTES)
            finally:
                transport.close()
            result = (response.status, response_text(response), response.url or url)
        except Exception as exc:
            # 原始错误要留在复核表里。TLS 失败尤其不能只写「取不到」：证书链不完整、
            # 被中间人换掉、和本机到不了这个站是三件不同的事，处置也不同。
            result = (0, "", f"{type(exc).__name__}: {exc}"[:160])
        self._cache[url] = result
        return result


def load_links(connection: sqlite3.Connection) -> list[dict]:
    aliases: dict[int, list[str]] = {}
    for entity_id, alias in connection.execute(
            "SELECT entity_id, alias FROM entity_alias ORDER BY confidence DESC, alias"):
        aliases.setdefault(entity_id, []).append(alias)
    refs: dict[tuple[int, str], str] = {}
    for entity_id, provider, external_id in connection.execute(
            "SELECT entity_id, provider, external_id FROM entity_external_ref"):
        refs[(entity_id, provider)] = external_id
    rows = []
    for link_id, entity_id, kind, name, link_kind, label, url, metadata in connection.execute(
            "SELECT l.id, e.id, e.kind, e.canonical_name, l.link_kind, l.label, l.url,"
            " l.metadata_json FROM entity_link l JOIN entity e ON e.id=l.entity_id"
            " ORDER BY l.id"):
        rows.append({"link_id": link_id, "entity_id": entity_id, "kind": kind, "name": name,
                     "link_kind": link_kind, "label": label, "url": url,
                     "metadata_json": metadata,
                     "chain": title_names(name, aliases.get(entity_id, [])),
                     "refs": {provider: value for (owner, provider), value in refs.items()
                              if owner == entity_id}})
    return rows


def review(links: list[dict]) -> list[dict]:
    """给每条链接挂上它犯的问题。返回的就是传给计划那一步的记录。"""
    deeper: dict[str, set[str]] = defaultdict(set)
    for row in links:
        if row["link_kind"] == "official":
            deeper[urlsplit(row["url"]).hostname or ""].add(urlsplit(row["url"]).path)
    shared: dict[str, int] = defaultdict(int)
    for row in links:
        if row["link_kind"] == "official":
            shared[row["url"]] += 1
    by_site: dict[tuple[int, str], list[int]] = defaultdict(list)
    for row in links:
        by_site[(row["entity_id"], urlsplit(row["url"]).hostname or "")].append(row["link_id"])

    for row in links:
        url = row["url"]
        host = urlsplit(url).hostname or ""
        problems: list[tuple[str, str, str]] = []
        if row["link_kind"] == "official":
            reason = directory_reason(url, deeper[host])
            if shared[url] > 1:
                reason = f"{shared[url]} 个实体共用这一个地址"
            if reason:
                problems.append((DIRECTORY, FIX_REDISCOVER, reason))
        if is_search_page(url):
            provider = DIRECT_PAGES.get(host, ("", ""))[0]
            has_id = bool(provider and row["refs"].get(provider))
            problems.append((SEARCH, FIX_DIRECT_ID if has_id else FIX_KEEP_SEARCH,
                             f"账本里有 {provider} id" if has_id
                             else "账本里没有这个站的直达 id"))
        if html_entity_leak(url):
            problems.append((ENTITY_LEAK, FIX_ENTITY,
                             "服务端收到的参数名带 amp; 前缀，真正的参数因此丢失"))
        if urlsplit(url).scheme == "http":
            problems.append((PLAIN_HTTP, FIX_HTTPS, "账本里存的是明文 http"))
        if empty_path_segment(url):
            problems.append((EMPTY_SEGMENT, FIX_REPORT_ONLY, "路径里有空段"))
        peers = by_site[(row["entity_id"], host)]
        if len(peers) > 1:
            problems.append((DUPLICATE, FIX_REPORT_ONLY,
                             f"该实体在这个站上有 {len(peers)} 条链接："
                             f"{'、'.join(str(peer) for peer in peers)}"))
        row["problems"] = problems
    return [row for row in links if row["problems"]]


def audit_rows(findings: list[dict]) -> list[dict]:
    """审计表：一条链接犯几条就写几行。"""
    out = []
    for row in findings:
        for problem, fix, evidence in row["problems"]:
            out.append({field: "" for field in FIELDS} | {
                "link_id": row["link_id"], "entity_id": row["entity_id"], "kind": row["kind"],
                "name": row["name"], "link_kind": row["link_kind"], "label": row["label"],
                "url": row["url"], "problem": problem, "fix": fix, "evidence": evidence})
    return out


def primary(row: dict) -> tuple[str, str, str] | None:
    """这条链接的主修法；只有该只报的问题时返回 None。"""
    for name in PRIORITY:
        for problem in row["problems"]:
            if problem[0] == name:
                return problem
    return None


def confirm_replacement(fetcher: Fetcher, old_url: str, new_url: str,
                        names: list[str]) -> tuple[str, str]:
    """(确认过的新地址, 说明)。确认不了时新地址为空。"""
    status, html, final = fetcher.get(new_url)
    if status != 200:
        return "", f"新地址回 HTTP {status}" if status else f"新地址取不到（{final}）"
    hit = confirms(html, names)
    if hit:
        return final, f"新地址 200，标题自述「{hit}」"
    shape = "站点根" if bare_root(final) else directory_reason(final)
    if shape:
        # 站点把整段路径重定向到首页时，新旧两头的标题完全一致，「标题相同」那一关
        # 于是变成了「都落在首页」的证明。换成首页比留着原地址更糟。
        return "", f"新地址落在{shape}：{final}"
    if not same_document_path(final, new_url):
        return "", f"新地址被重定向到另一条路径：{final}"
    title = page_title(html)
    old_status, old_html, _ = fetcher.get(old_url)
    if old_status == 200 and title and page_title(old_html) == title:
        return final, f"新地址 200，标题与原地址同为「{title[:60]}」"
    return "", "新地址 200，但标题既不含该人名字、也与原地址不一致"


def rediscover(fetcher: Fetcher, row: dict) -> tuple[str, str]:
    """在站点索引里把本人页找回来。(新地址, 说明)。"""
    names = row["chain"]
    if not names:
        return "", "没有可用于标题比对的名字"
    url = row["url"]
    # 先问这一页自己。`official` 里有一批指向站点根，其中一部分是这个人自己的官网——
    # 那种地址的根就是她的页面，按目录页去改会把对的改成别的。标题自述该人就收工。
    status, html, final = fetcher.get(url)
    if status == 200 and confirms(html, names):
        return final, f"该地址的标题自述「{confirms(html, names)}」，本身就是本人页"
    seen: set[str] = set()
    for index in index_candidates(url, include_self=True):
        status, html, final = fetcher.get(index)
        if status != 200 or not html:
            continue
        for target, matched in anchors_naming(html, final, names):
            if target in seen or target.rstrip("/") == url.rstrip("/"):
                continue
            # 修复的定义是「在同一个站点上找到新地址」，不是「找到关于这个人的另一个
            # 链接」：索引页里那一条可能指向她的 X 账号，人是对的，但那不是这一栏的东西。
            if not same_site(target, url):
                continue
            seen.add(target)
            hit_status, hit_html, hit_final = fetcher.get(target)
            if hit_status != 200:
                continue
            title = confirms(hit_html, names)
            if title:
                return hit_final, (f"站点索引 {index} 里「{matched}」指向该页，"
                                   f"标题自述「{title}」")
        if len(seen) >= MAX_CANDIDATES:
            break
    return "", f"在 {len(seen)} 个同站候选里没有标题自述该人的页面"


def plan_one(fetcher: Fetcher, row: dict) -> dict:
    """一条链接的计划行。"""
    problem, fix, evidence = primary(row)
    plan = {field: "" for field in FIELDS} | {
        "link_id": row["link_id"], "entity_id": row["entity_id"], "kind": row["kind"],
        "name": row["name"], "link_kind": row["link_kind"], "label": row["label"],
        "url": row["url"], "fix": fix, "evidence": evidence,
        "problem": "；".join(item[0] for item in row["problems"])}
    if fix == FIX_KEEP_SEARCH:
        plan.update(verdict=FIX_KEEP_SEARCH, check="没有直达 id，不按名字拼地址")
        return plan
    if fetcher.exhausted:
        plan.update(verdict="未取得", check="本轮请求上限已用完")
        return plan

    if fix == FIX_REDISCOVER:
        new_url, note = rediscover(fetcher, row)
    elif fix == FIX_DIRECT_ID:
        host = urlsplit(row["url"]).hostname or ""
        provider, template = DIRECT_PAGES[host]
        candidate = template.format(id=row["refs"][provider])
        new_url, note = confirm_replacement(fetcher, row["url"], candidate, row["chain"])
    else:
        candidate = row["url"]
        if html_entity_leak(candidate):
            candidate = without_entities(candidate)
        candidate = upgraded_scheme(candidate) or candidate
        new_url, note = confirm_replacement(fetcher, row["url"], candidate, row["chain"])

    if not new_url:
        plan.update(verdict="未取得", check=note)
    elif new_url == row["url"]:
        plan.update(new_url=new_url, verdict="已是目标", check=note)
    else:
        plan.update(new_url=new_url, verdict="ok", check=note)
    return plan


def interleaved(rows: list[dict]) -> list[dict]:
    """按主机轮转排队。

    顺着账本 id 跑的话，同一个站的几十条会连成一串，而同域名两次请求之间要等满
    `--interval`——那段等待是白等的，换个站先发就不必等。轮转既保住了每个域名的
    节拍，也不让整轮时间等于「条数 × 间隔」。
    """
    queues: dict[str, deque] = defaultdict(deque)
    for row in rows:
        queues[urlsplit(row["url"]).hostname or ""].append(row)
    out: list[dict] = []
    while queues:
        for host in list(queues):
            out.append(queues[host].popleft())
            if not queues[host]:
                del queues[host]
    return out


def plan_all(todo: list[dict], fetcher: Fetcher) -> list[dict]:
    plans = []
    for index, row in enumerate(interleaved(todo), 1):
        plan = plan_one(fetcher, row)
        plans.append(plan)
        print(f"{index:>4}/{len(todo)} {plan['name'][:10]:<10} {plan['fix']:<14} "
              f"{plan['verdict']:<8} {plan['new_url'][:60]}")
    return sorted(plans, key=lambda plan: plan["link_id"])


def write_plan(connection: sqlite3.Connection, plans: list[dict], now: str) -> dict:
    """把 verdict 为 ok 的行写进 `entity_link`。返回这一轮的处置计数。"""
    tally = {"updated": 0, "skipped_unique": 0}
    for plan in plans:
        if plan["verdict"] != "ok":
            continue
        link_id, entity_id, new_url = int(plan["link_id"]), int(plan["entity_id"]), plan["new_url"]
        clash = connection.execute(
            "SELECT id FROM entity_link WHERE entity_id=? AND url=? AND id<>?",
            (entity_id, new_url, link_id)).fetchone()
        if clash is not None:
            plan["verdict"] = "撞上唯一约束，整行跳过"
            plan["check"] += f"；该实体已有 id={clash[0]} 指向这个地址"
            tally["skipped_unique"] += 1
            continue
        stored = connection.execute(
            "SELECT metadata_json FROM entity_link WHERE id=?", (link_id,)).fetchone()
        try:
            metadata = json.loads(stored[0] or "{}")
        except ValueError:
            metadata = {}
        metadata["repaired_from"] = {"url": plan["url"], "problem": plan["problem"],
                                     "evidence": plan["check"], "at": now}
        connection.execute(
            "UPDATE entity_link SET url=?, hostname=?, metadata_json=?, updated_at=?"
            " WHERE id=?",
            (new_url, urlsplit(new_url).hostname or "",
             json.dumps(metadata, ensure_ascii=False), now, link_id))
        tally["updated"] += 1
    return tally


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ledger_write_args(parser)
    parser.add_argument("--audit-output", type=Path, required=True,
                        help="全库审计表：一条链接犯几条问题就写几行")
    parser.add_argument("--output", type=Path, required=True, help="修复计划表")
    parser.add_argument("--offline", action="store_true",
                        help="只做审计，不发任何请求")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="同一域名两次请求之间的最小间隔（秒）")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-requests", type=int, default=600,
                        help="整轮的请求总量上限")
    parser.add_argument("--limit", type=int, default=0, help="只处理前若干条待修链接")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and not args.backup:
        raise SystemExit(BACKUP_REQUIRED)

    connection = open_readonly(args.db)
    try:
        findings = review(load_links(connection))
    finally:
        connection.close()
    audit = audit_rows(findings)
    write_rows(args.audit_output, FIELDS, audit)
    counted: dict[str, int] = defaultdict(int)
    for row in audit:
        counted[row["problem"]] += 1
    print({"链接": len(findings), "问题行": len(audit), **counted,
           "audit": str(args.audit_output)})

    if args.offline:
        write_rows(args.output, FIELDS, [])
        print("只做审计，没有发请求")
        return 0

    todo = [row for row in findings if primary(row)]
    if args.limit:
        todo = todo[:args.limit]
    fetcher = Fetcher(trust_context(), args.interval, args.timeout, args.max_requests)
    plans = plan_all(todo, fetcher)

    now = now_text()
    if args.apply:
        writable = open_for_write(args)
        try:
            integrity = writable.execute("PRAGMA integrity_check").fetchone()[0]
            if str(integrity) != "ok":
                raise SystemExit(f"写入前 integrity_check 不是 ok：{integrity}")
            before = counts_of(writable, {"entity_link": "SELECT count(*) FROM entity_link"})
            tally = write_plan(writable, plans, now)
            writable.commit()
            integrity, violations = verify_after_write(writable)
            after = counts_of(writable, {"entity_link": "SELECT count(*) FROM entity_link"})
        finally:
            writable.close()
        print({"写入": tally, "前": before, "后": after,
               "integrity_check": integrity, "foreign_key_check 违规": violations})

    write_rows(args.output, FIELDS, plans)
    verdicts: dict[str, int] = defaultdict(int)
    for plan in plans:
        verdicts[plan["verdict"]] += 1
    print({"待修": len(plans), **verdicts, "请求数": fetcher.used,
           "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
