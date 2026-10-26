"""从 minnano-av 的事务所名册补成员关系：这家有哪些人，而不只是这个人属于哪家。

账本里的 200 条归属全部来自女优页的「所属事務所」那一行——一个人一个人问出来的。
那条路只走得到已经问过的人：`harvest_performer_links.py` 默认 `--min-assets 3`，
559 位 performer 里只有 200 位被问过，剩下的归属至今空着。而事务所页那个「艺人」
名册要回答的是反向的一问，它现在只显示问出来的那一部分，看起来像这家只有三个人。

名册页正是反向那一问的答案：`actress_list.php?production=<编号>` 一页 30 位，
JSON-LD 里带这家的总人数（マインズ 249 位，2026-09-05 实测）。一次取一家，比按人
逐个问省下两个数量级的请求。

**编号只能从人身上取。** 站点没有事务所索引页，编号只出现在女优页「所属事務所」那一格的
站内链接里。所以先拿这家已知的成员去检索，落到女优页，读出编号，并核对那一格里写的
名字确实是这家——只凭一个数字装上去，装错了在复核件上看不出来。核不上就写「未取得」，
不猜。取到的编号写进 `entity_external_ref`，下一趟不必再问一遍。

**名册上的名字要对回账本。** 账本里 performer 的规范名是简体中文（`凉森玲梦`），
日文写法在 `entity_alias` 里（`涼森れむ`），所以对名字必须连别名一起对。对不上的记
「不在库」——那是这家有、我们没有的人，不是错误；同一个写法对上两个人的记「重名」，
留给人判。

**已经有归属的人不动。** `entity_membership` 主键在成员一侧，一个人只有一条现役归属。
名册说这个人属于 A、账本里写着 B，这是移籍或采错，两种都要人来定：记「另有归属」，
`--apply` 不碰它。

默认 dry-run，只出复核 CSV。`--apply` 必须同时给 `--backup`：这是真实账本写入。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.config import REVIEW_DIR, SECRETS_DIR, STATE_DIR   # noqa: E402
from peach.entities import (   # noqa: E402
    FORMER_PREFIX, agency_key, agency_rejections, name_chain, normalize_entity_name, split_name,
)
from peach.http import HttpRequest   # noqa: E402
from peach.jobs import job_main   # noqa: E402
from peach.minnano_av import (   # noqa: E402
    actress_id, production_ref, roster_page, roster_url, search_url,
)
from peach.review_csv import write_rows   # noqa: E402
from peach.scraping_access import SourceTransport   # noqa: E402
from peach.scripting import (   # noqa: E402
    USER_AGENT, RateLimiter, add_ledger_write_args, counts_of, open_for_write, verify_after_write,
)

FIELDS = ("agency", "agency_id", "production", "roster_name", "actress_id",
          "entity_id", "performer", "current_agency", "verdict", "evidence")

#: 归属的来源。女优页那一行是 `minnano-av:所属事務所`（`install_agencies.py`），
#: 名册页是另一条证据路径，两者分开记：将来某一条被推翻时，要能只撤那一条。
MEMBERSHIP_SOURCE = "minnano-av:事務所名簿"
PROVIDER, PRODUCTION = "minnano-av", "production"

NEW, KNOWN, MOVED = "新增归属", "已归属", "另有归属"
AMBIGUOUS, ABSENT, MISSING = "重名", "不在库", "未取得"
#: 名册收了这个人，而她驳回过这一家（`peach.entities.AGENCY_REJECTED`）。
REJECTED = "已驳回"
#: 会被 `--apply` 写进账本的判词。只有这一个。
INSTALLABLE = (NEW,)

EXTRA_COUNTS = {
    "entity_membership": "SELECT count(*) FROM entity_membership",
    "minnano_production": ("SELECT count(*) FROM entity_external_ref"
                           f" WHERE provider='{PROVIDER}'"
                           f" AND external_kind='{PRODUCTION}'"),
}

#: 对名字时忽略的东西。站上写 `明日花 キララ`，账本别名写 `明日花キララ`；
#: 中点、连字符和各种空白都只是排版，不是名字的一部分。
_NOISE = str.maketrans({" ": "", "　": "", "・": "", "･": "", "‐": "", "-": "",
                        "－": ""})


def roster_key(name: str) -> str:
    return normalize_entity_name(name).translate(_NOISE)


def shown_keys(shown: str) -> set[str]:
    """站上那一格写的事务所名 → 可以拿去和账本对的键。

    括号里是两种东西。`KRONE(クローネ)` 里是读音，账本把它记成别名，整串拿去对
    一个都对不上，拆开就对上了。而 `GG(旧・Prime Agency)` 里是这家吞掉的旧名，
    Prime Agency 在账本里仍是独立一家，认下这个键就把两家并成了一家——带「旧」
    「元」的那几段一律不作数。
    """
    current, aliases = split_name(shown)
    former = {FORMER_PREFIX.sub("", part).strip()
              for part in re.split(r"[（()）]", shown) if FORMER_PREFIX.match(part)}
    return {roster_key(name) for name in (current, *aliases)
            if name and name not in former} - {""}


def performer_index(connection) -> tuple[dict[str, set[int]], dict[int, str]]:
    """(名字 → performer id 集合, id → 规范名)。规范名和别名一起进索引。"""
    index: dict[str, set[int]] = {}
    names: dict[int, str] = {}
    for row in connection.execute(
            "SELECT id, canonical_name FROM entity WHERE kind='performer'"):
        names[row["id"]] = row["canonical_name"]
        index.setdefault(roster_key(row["canonical_name"]), set()).add(row["id"])
    for row in connection.execute(
            "SELECT a.entity_id, a.alias FROM entity_alias a JOIN entity e ON e.id=a.entity_id"
            " WHERE e.kind='performer'"):
        if row["entity_id"] in names:
            index.setdefault(roster_key(row["alias"]), set()).add(row["entity_id"])
    index.pop("", None)
    return index, names


def agencies(connection) -> list[dict[str, object]]:
    """要采名册的事务所：名字、别名、已知成员、已经记下的站内编号。"""
    found: list[dict[str, object]] = []
    for row in connection.execute(
            "SELECT id, canonical_name FROM entity WHERE kind='agency'"
            " ORDER BY canonical_name"):
        aliases = [item["alias"] for item in connection.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=?", (row["id"],))]
        ref = connection.execute(
            "SELECT external_id FROM entity_external_ref WHERE entity_id=?"
            " AND provider=? AND external_kind=?",
            (row["id"], PROVIDER, PRODUCTION)).fetchone()
        members = [item["member_id"] for item in connection.execute(
            "SELECT member_id FROM entity_membership WHERE agency_id=?", (row["id"],))]
        found.append({"id": row["id"], "name": row["canonical_name"], "aliases": aliases,
                      "production": ref["external_id"] if ref else "",
                      "members": members})
    return found


def memberships(connection) -> dict[int, tuple[int, str]]:
    """member_id → (现在归哪家, 那家的名字)。"""
    return {row["member_id"]: (row["agency_id"], row["canonical_name"])
            for row in connection.execute(
                "SELECT m.member_id, m.agency_id, e.canonical_name FROM entity_membership m"
                " JOIN entity e ON e.id=m.agency_id")}


class Site:
    """按固定间隔取页的取数器。返回 (最终地址, 正文)；取不到返回 (地址, "")。"""

    def __init__(self, http, timeout: float, interval: float):
        self.http = http
        self.timeout = timeout
        self.interval = interval
        self.fetched = 0
        self._limiter = RateLimiter(interval)

    def __call__(self, url: str) -> tuple[str, str]:
        self._limiter.wait()
        try:
            response = self.http(HttpRequest("GET", url, {"User-Agent": USER_AGENT}),
                                 self.timeout, 4 << 20)
        except Exception:
            return url, ""
        if response.status != 200:
            return response.url or url, ""
        self.fetched += 1
        return response.url or url, response.body.decode("utf-8", "replace")


def find_production(site: Site, agency: dict[str, object], names: dict[int, str],
                    aliases_of, probe: int) -> tuple[str, str]:
    """拿这家已知的成员去问出站内编号，返回 (编号, 说明)。

    核对站上那一格写的名字必须落在这家的名字或别名里。`マインズ` 对得上，
    而一个成员被采错时那一格写的是别家——那种情况宁可空着。
    """
    keys = {roster_key(str(agency["name"]))}
    keys.update(roster_key(alias) for alias in agency["aliases"])  # type: ignore[union-attr]
    tried: list[str] = []
    for member_id in list(agency["members"])[:probe]:  # type: ignore[arg-type]
        for candidate in name_chain(names.get(member_id, ""), aliases_of(member_id)):
            final, html = site(search_url(candidate))
            if not html or not actress_id(final):
                tried.append(f"{candidate} 未唯一命中")
                continue
            found, shown = production_ref(html)
            if not found:
                tried.append(f"{candidate} 的资料表没有事务所链接")
                continue
            if not shown_keys(shown) & keys:
                tried.append(f"{candidate} 指向「{shown}」（production={found}），不是这家")
                continue
            return found, f"由成员「{candidate}」的资料表取得，站上写作「{shown}」"
    return "", "；".join(tried) or "这家在账本里没有已知成员，无从取得编号"


def fetch_roster(site: Site, production: str, max_pages: int
                 ) -> tuple[list[tuple[str, str]], int, str]:
    """名册全部页 → ([(名字, 女优编号)], 站点声明的总人数, 说明)。

    翻页地址跟 `<link rel="next">`，但停在哪里不能听它的：站点在最后一页之后照旧
    给出 `page=6`、`page=7`，而超出范围的页返回的就是最后一页那几位
    （production=573 实测第 5 页 9 位，第 6 页起原样重复，跟到第 20 页会把这 9 位
    抄十六遍，`264 位` 比站点声明的 129 位还多一倍）。真正的终点是「这一页没有新人」。
    """
    people: list[tuple[str, str]] = []
    known: set[str] = set()
    walked: set[str] = set()
    url = roster_url(production)
    total, pages = 0, 0
    while url and url not in walked and pages < max_pages:
        walked.add(url)
        final, html = site(url)
        if not html:
            return people, total, f"第 {pages + 1} 页取不回来（{final}）"
        page, declared, following = roster_page(html, final)
        total = max(total, declared)
        pages += 1
        fresh = [(name, ident) for name, ident in page if (ident or name) not in known]
        known.update(ident or name for name, ident in fresh)
        people.extend(fresh)
        if not fresh:
            break
        url = following
    note = f"{pages} 页、{len(people)} 位"
    if total and len(people) != total:
        # 差额分两种，复核件上要能分得开：页数封顶是我们没走完，走完了还差是站点自己的
        # 名册里有重号（T-POWERS 声明 420 位，14 页去重后 410 位，2026-09-05 实测）。
        reason = "页数封顶没采全" if pages >= max_pages else "翻到最后一页去重后就这些"
        note += f"，站点声明 {total} 位，{reason}"
    return people, total, note


def plan(site: Site, connection, args) -> list[dict[str, object]]:
    index, names = performer_index(connection)
    current = memberships(connection)
    rejected = agency_rejections(connection)

    def aliases_of(entity_id: int) -> list[str]:
        return [row["alias"] for row in connection.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=?", (entity_id,))]

    rows: list[dict[str, object]] = []
    wanted = {normalize_entity_name(name) for name in args.only}
    for agency in agencies(connection):
        if wanted and normalize_entity_name(str(agency["name"])) not in wanted:
            continue
        production = str(agency["production"])
        note = "账本里已经记着这个编号"
        if not production:
            production, note = find_production(site, agency, names, aliases_of, args.probe)
        base = {"agency": agency["name"], "agency_id": agency["id"],
                "production": production}
        if not production:
            rows.append({**base, "roster_name": "", "actress_id": "", "entity_id": "",
                         "performer": "", "current_agency": "", "verdict": MISSING,
                         "evidence": note})
            print(f"{str(agency['name'])[:16]:<16} 未取得：{note}")
            continue
        people, total, roster_note = fetch_roster(site, production, args.max_pages)
        if not people:
            rows.append({**base, "roster_name": "", "actress_id": "", "entity_id": "",
                         "performer": "", "current_agency": "", "verdict": MISSING,
                         "evidence": f"{note}；名册 {roster_note}"})
            print(f"{str(agency['name'])[:16]:<16} 未取得：{roster_note}")
            continue
        absent: list[str] = []
        for person, found_id in people:
            matched = sorted(index.get(roster_key(person), ()))
            row = {**base, "roster_name": person, "actress_id": found_id,
                   "entity_id": "", "performer": "", "current_agency": "",
                   "evidence": f"{note}；名册 {roster_note}"}
            if not matched:
                # 默认收成一行
                # 计数，`--all-names` 才逐个记：复核件是给人看的，不是站点镜像。
                if args.all_names:
                    rows.append({**row, "verdict": ABSENT})
                else:
                    absent.append(person)
                continue
            if len(matched) > 1:
                rows.append({**row, "verdict": AMBIGUOUS,
                             "evidence": row["evidence"] + "；账本里这个写法对上 "
                                         + "、".join(names[item] for item in matched)})
                continue
            member_id = matched[0]
            row.update(entity_id=member_id, performer=names[member_id])
            held = current.get(member_id)
            if agency_key(str(agency["name"])) in rejected.get(member_id, ()):
                rows.append({**row, "verdict": REJECTED})
            elif held is None:
                rows.append({**row, "verdict": NEW})
            elif held[0] == agency["id"]:
                rows.append({**row, "verdict": KNOWN, "current_agency": held[1]})
            else:
                rows.append({**row, "verdict": MOVED, "current_agency": held[1]})
        if absent:
            shown = "、".join(absent[:20])
            rows.append({**base, "roster_name": "", "actress_id": "", "entity_id": "",
                         "performer": "", "current_agency": "", "verdict": ABSENT,
                         "evidence": f"名册上 {len(absent)} 位不在账本里："
                                     f"{shown}{'…' if len(absent) > 20 else ''}"})
        hit = sum(1 for row in rows if row["agency_id"] == agency["id"]
                  and row["verdict"] == NEW)
        print(f"{str(agency['name'])[:16]:<16} production={production} "
              f"{roster_note}，新增 {hit}")
    return rows


def apply_rows(connection, rows: list[dict[str, object]], stamp: str) -> dict[str, int]:
    done = {"编号": 0, "归属": 0}
    for agency_id, production in sorted({
            (int(row["agency_id"]), str(row["production"])) for row in rows
            if row["production"]}):
        connection.execute(
            "INSERT OR IGNORE INTO entity_external_ref"
            "(entity_id,provider,external_kind,external_id,metadata_json,last_synced_at)"
            " VALUES(?,?,?,?,?,?)",
            (agency_id, PROVIDER, PRODUCTION, production,
             json.dumps({"source": "harvest_agency_rosters"}, ensure_ascii=False), stamp))
        done["编号"] += connection.execute("SELECT changes()").fetchone()[0]
    for row in rows:
        if row["verdict"] not in INSTALLABLE or not row["entity_id"]:
            continue
        # 只写空着的那一位。`另有归属` 不在 `INSTALLABLE` 里，所以这里的 IGNORE
        # 挡的是同一趟里两家名册都收了同一个人的情况，不是在悄悄跳过冲突。
        connection.execute(
            "INSERT OR IGNORE INTO entity_membership"
            "(member_id,agency_id,source,confidence,checked_at) VALUES(?,?,?,1.0,?)",
            (int(row["entity_id"]), int(row["agency_id"]), MEMBERSHIP_SOURCE, stamp))
        done["归属"] += connection.execute("SELECT changes()").fetchone()[0]
    return done


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ledger_write_args(parser)
    parser.add_argument("--output", type=Path, default=REVIEW_DIR / "agency-rosters.csv")
    parser.add_argument("--only", nargs="*", default=[],
                        help="只采这几家，按 canonical_name 给")
    parser.add_argument("--all-names", action="store_true",
                        help="名册上不在账本里的人也逐个记进复核件")
    parser.add_argument("--probe", type=int, default=3,
                        help="为取站内编号最多问几位已知成员")
    parser.add_argument("--max-pages", type=int, default=20,
                        help="一家最多翻几页；30 位一页，20 页是 600 位")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".agency-rosters.lock")
    return parser


def run(args) -> int:
    connection = open_for_write(args)
    # 与补别名后继同一个入口：冷却记录共用，HTTP 客户端被拦时由本机浏览器验证（`browser_fallback`）。
    http = SourceTransport(SECRETS_DIR)
    try:
        before = counts_of(connection, EXTRA_COUNTS)
        rows = plan(Site(http, args.timeout, args.interval), connection, args)
        write_rows(args.output, FIELDS, rows, atomic=True, fill_missing=True)
        stats = {verdict: sum(1 for row in rows if row["verdict"] == verdict)
                 for verdict in (NEW, KNOWN, MOVED, AMBIGUOUS, ABSENT, MISSING)}
        print({"复核行": len(rows), **stats, "output": str(args.output)})
        if not args.apply:
            print("dry-run：没有写入。加 --apply --backup <路径> 才真的写。")
            return 0
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        done = apply_rows(connection, rows, stamp)
        connection.commit()
        after = counts_of(connection, EXTRA_COUNTS)
        integrity, violations = verify_after_write(connection)
        print("写入：" + "，".join(f"{key} {value}" for key, value in done.items()))
        for key in sorted(before):
            print(f"  {key}: {before[key]} -> {after[key]}")
        print(f"integrity_check={integrity} foreign_key_check={violations}")
        return 0
    finally:
        http.close()
        connection.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(job_main(build_parser, run))
