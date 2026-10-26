"""补女优资料：minnano-av 的资料表整张落进 `performer_profile`，再绑 avwikidb 的女优编号（ADR-0067）
与 javdb 的演员 id（ADR-0081）。

判据是确定性的，按 ADR-0052 直接落库，不产生候选：

1. **minnano-av 的入口与补别名后继同一条**（`performer_alias_followup.minnano_profile_pages`）：
   账本里有她的 minnano-av 编号就直接进资料页，没有就按名字链检索，唯一命中才算。
2. **双向核对**：资料页的主名或別名栏里得有账本里她的某个名字（本身收得下的写法）。
   检索进来的页核对通过时，顺手把这个编号登记成她的 minnano-av 外部编号，下一轮直接进。
3. **整行写入**：资料按 `minnano_av.profile` 规整后写 `performer_profile`，读不出的列留空。
   这一行是人写的（`source` 不以 `auto:` 开头）就不动。
4. **avwikidb 只从她自己的作品进**：取她非 FC2 作品的番号（出演人数少的在前，至多
   `MAX_WORKS` 部），读作品页的出演表；名字与账本里她的某个名字折叠后相等，或罗马字与
   minnano-av 上她的罗马字按词相等，恰好一位对得上才算。对不上、对上两位都不绑，写「未命中」，
   不按名字猜编号。这个编号已经属于另一条实体时只记「占用」。
5. **交叉核对只记不改**：avwikidb 女优页的出生日期与身高和 `performer_profile` 不一致时，
   资料表不动，两边的值记进这条外部编号的 `metadata_json.conflicts`，摘要里给条数。
6. **javdb 只给 JAV 女优、只绑 id**：前两站跑完、她在 JAV 目录站里有身份
   （`entry_links.is_jav_performer`）又还没有 javdb id 时，拿名字链里收得下的写法搜
   `search?f=actor`，至多 `alias.MAX_KEYS` 个写法、搜到就停。卡片标题一栏里有账本里她的名字才算，
   对上的几张卡还得是同一个人（`javdb.one_person`：有碼、無碼各至多一张），是就把几个 id 都绑上；
   同一种记录对上两张是站上同名，写「未命中」。资料页不进，JavDB 入口只要 id。

每条写入都带批次号 `auto:performer-profile@<任务行 id>`：资料行记在 `performer_profile.source`，
外部编号记在 `entity_external_ref.metadata_json` 的 `source` 与 `batch`。
`scripts/revert_auto_landing.py --source auto:performer-profile` 整批撤回。判词逐条写进
`generated/performer-profile-landing.csv`。

取页复用补别名后继的 `MinnanoPages`：成功页落盘缓存，minnano-av 与 avwikidb 按 3 秒间隔，撞上
429、403 或机器人验证就记进 `scraping_access` 的冷却，本轮与之后在冷却期内都不再问，结论写「未取得」。
三站的请求都走 `SourceTransport`，连接方式跟采集设置里那一站的设置走；minnano-av 的 HTTP 客户端被拦时
先由本机浏览器验证一页，取到就固定走浏览器（`scraping_access.SOURCES` 的 `browser_fallback`）。javdb 按出口 IP
计配额、一封 3～7 天，这一站单独 5 秒间隔（与目录采集同一档），冷却记录与作品采集共用 `javdb` 那一份。

多久再派一次由 `Attempts` 的记号决定：写成了的保 `REFRESH`（30 天），到期再派一次，资料页
按取回时间过了 30 天才重取；有一站「未取得」的只保 `RETRY_UNFETCHED`；哪一站都没对上的等名字链、
编号或作品变了再派。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from urllib.parse import quote

from . import avwikidb, entry_links, javdb, minnano_av
from . import performer_alias_followup as alias
from .followups import Attempts, Followup, FollowupType, attempts_root, register
from .performer_profiles import fetched_at as profile_fetched_at, read_profile, write_profile
from .scripting import HostLimiter

TASK_KEY = "performer-profile"
TASK_LABEL = "补女优资料"
SOURCE = "auto:performer-profile"
MINNANO, AVWIKIDB, JAVDB = alias.MINNANO, "avwikidb", "javdb"
#: javdb 的主机间隔，与 `harvest_directory_links.SOURCE_INTERVAL` 同一档（docs/SOURCING.md）。
JAVDB_INTERVAL = 5.0
_JAVDB_LIMITER = HostLimiter({"javdb.com": JAVDB_INTERVAL})

#: 每轮处理任务给存量的名额。一条最多问两站五六次、每次隔 3 秒，给多了会把头像与厂牌挤到很后面；
#: 九百多位女优按这个名额要一个多月的处理轮次才轮遍，存量本来就不赶。
STOCK_SHARE = 16
#: 资料多久算旧：过了这么久再派一次，资料页与 avwikidb 女优页都重取。
REFRESH = 30 * 86400
RETRY_UNFETCHED = alias.RETRY_UNFETCHED
#: 入口判据的版本，进指纹。换了判据就加一，跑过的女优按新判据各再问一次。
#: 2 是 javdb 那一站（ADR-0081）。
RULE = 2
#: avwikidb 最多翻她几部作品的出演表。
MAX_WORKS = 3

WRITE, FRESH, MISS, UNFETCHED, TAKEN, BIND, CONFLICT = (
    "写入", "未过期", "未命中", "未取得", "占用", "绑定", "冲突")
REVIEW_FILE = "performer-profile-landing.csv"
FIELDS = ("entity_id", "canonical_name", "site", "page", "action", "detail", "batch")
#: 两站交叉核对的列：(performer_profile 的列, avwikidb 那边的键)。
CROSS_CHECKED = (("birth_date", "birth_date"), ("height_cm", "height_cm"))


# -- 账本 --------------------------------------------------------------------


def followup_key(entity_id: int) -> str:
    return f"{TASK_KEY}:{int(entity_id)}"


def parse_key(key: str) -> int:
    prefix, _, raw = str(key).partition(":")
    if prefix != TASK_KEY or not raw.isdigit():
        raise ValueError(f"认不出这条补女优资料后继：{key}")
    return int(raw)


def _refs(connection: sqlite3.Connection, entity_id: int, provider: str) -> list[str]:
    return [str(row[0]) for row in connection.execute(
        "SELECT external_id FROM entity_external_ref WHERE entity_id=? AND provider=?"
        " AND external_kind='performer' ORDER BY external_id", (int(entity_id), provider))]


def work_codes(connection: sqlite3.Connection, entity_id: int) -> list[str]:
    """她出演的非 FC2 作品番号，出演人数少的在前，至多 `MAX_WORKS` 部：avwikidb 的入口。"""
    return [str(row[0]) for row in connection.execute(
        "SELECT a.code, (SELECT count(*) FROM asset_entity x WHERE x.asset_id=a.id"
        " AND x.role='performer') AS cast_size"
        " FROM asset a JOIN asset_entity ae ON ae.asset_id=a.id"
        " WHERE ae.entity_id=? AND ae.role='performer' AND a.code IS NOT NULL AND a.code<>''"
        " AND a.code NOT LIKE 'FC2%' GROUP BY a.code ORDER BY cast_size, a.code LIMIT ?",
        (int(entity_id), MAX_WORKS))]


def has_entry(connection: sqlite3.Connection, entity_id: int) -> bool:
    """有路进得去：有 minnano-av 编号、名字链里有能检索的写法，或有能进 avwikidb 作品页的番号。"""
    names = alias._names(connection, entity_id)
    return names is not None and bool(_refs(connection, entity_id, MINNANO)
                                      or alias.search_keys(*names)
                                      or work_codes(connection, entity_id))


def fingerprint(connection: sqlite3.Connection, entity_id: int) -> str:
    """会让结论变的量：名字链、三站编号、avwikidb 的入口番号与判据版本。"""
    names = alias._names(connection, entity_id) or ("", [])
    keys = sorted({alias.match_key(name) for name in [names[0], *names[1]] if name})
    parts = [keys, _refs(connection, entity_id, MINNANO), _refs(connection, entity_id, AVWIKIDB),
             work_codes(connection, entity_id), _refs(connection, entity_id, JAVDB), f"r{RULE}"]
    raw = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _label(name: str) -> str:
    return f"{TASK_LABEL}：{name}" if name else TASK_LABEL


def plan(connection: sqlite3.Connection, *, since_entity_id: int) -> list[Followup]:
    """这一轮新登记、又有路进得去的女优，一人一条，作品多的在前。"""
    rows = connection.execute(
        "SELECT e.id,e.canonical_name,"
        " (SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae WHERE ae.entity_id=e.id)"
        " FROM entity e WHERE e.id>? AND e.kind='performer' ORDER BY e.id",
        (int(since_entity_id),)).fetchall()
    found = sorted(((int(row[2] or 0), int(row[0]), str(row[1] or "")) for row in rows
                    if has_entry(connection, int(row[0]))), key=lambda item: (-item[0], item[1]))
    return [Followup(key=followup_key(entity_id), task_key=TASK_KEY, label=_label(name))
            for _works, entity_id, name in found]


def stock(connection: sqlite3.Connection, attempts, *, limit: int, skip=()) -> list[Followup]:
    """库里早就登记的女优，作品多的在前，最多 `limit` 条；记号未到期的不派（ADR-0053）。

    只读实体与外部编号，不读 `performer_profile`：资料多久算旧由记号的有效期管，
    排单这一步因此不依赖这张表。
    """
    if limit <= 0:
        return []
    skip, found = set(skip), []
    for row in connection.execute(
            "SELECT e.id,e.canonical_name,count(DISTINCT ae.asset_id) AS assets"
            " FROM entity e JOIN asset_entity ae ON ae.entity_id=e.id"
            " WHERE e.kind='performer' GROUP BY e.id ORDER BY assets DESC, e.id"):
        entity_id, key = int(row[0]), followup_key(int(row[0]))
        if key in skip or not has_entry(connection, entity_id):
            continue
        if attempts.settled(key, fingerprint(connection, entity_id)):
            continue
        found.append(Followup(key=key, task_key=TASK_KEY, label=_label(str(row[1] or ""))))
        if len(found) >= limit:
            break
    return found


# -- 取页 --------------------------------------------------------------------


def open_sites(contract) -> dict:
    """三站的取页器。缓存目录与补别名后继共用，同一页不问第二次。测试把这一步整个换掉。"""
    from .scraping_access import SourceTransport

    cache = Path(contract.candidate_root) / "provider-cache"
    cooldown = alias._cooldown_root(contract)
    return {MINNANO: alias.MinnanoPages(cache / "minnano-av-pages", cooldown, max_age=REFRESH),
            AVWIKIDB: avwikidb_pages(contract),
            JAVDB: alias.MinnanoPages(cache / "javdb-pages", cooldown, SourceTransport(cooldown),
                                      limiter=_JAVDB_LIMITER, max_requests=alias.MAX_KEYS,
                                      source=JAVDB, max_age=REFRESH)}


def avwikidb_pages(contract):
    """avwikidb 的取页器。补头像后继读单人作品页也用它：同一份缓存、同一套冷却。"""
    from .scraping_access import SourceTransport

    cooldown = alias._cooldown_root(contract)
    return alias.MinnanoPages(Path(contract.candidate_root) / "provider-cache" / "avwikidb-pages",
                              cooldown, SourceTransport(cooldown), source=AVWIKIDB,
                              max_age=REFRESH)


# -- 落库 --------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fresh(stamp: datetime | None) -> bool:
    return stamp is not None and (datetime.now(timezone.utc) - stamp).total_seconds() < REFRESH


def _ours(metadata: dict) -> bool:
    source = str(metadata.get("source") or "")
    return source == SOURCE or source.startswith(SOURCE + "@")


def bind(connection: sqlite3.Connection, entity_id: int, provider: str, external_id: str,
         metadata: dict) -> str:
    """把站上编号登记到她名下，返回判词：`绑定`，或编号已属另一条实体时的 `占用`。

    已经在她名下、却不是这条后继登记的，只换资料那几项，`source` 留原样：撤回只删自己登记的。
    """
    held = connection.execute(
        "SELECT entity_id,metadata_json FROM entity_external_ref WHERE provider=?"
        " AND external_kind='performer' AND external_id=?", (provider, str(external_id))).fetchone()
    if held is not None and int(held[0]) != int(entity_id):
        return TAKEN
    if held is not None:
        try:
            before = json.loads(held[1] or "{}")
        except ValueError:
            before = {}
        if not _ours(before):
            metadata = {**metadata, "source": before.get("source"), "batch": before.get("batch")}
            metadata = {key: value for key, value in metadata.items() if value is not None}
    connection.execute(
        "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id,"
        "metadata_json,last_synced_at) VALUES(?,?,'performer',?,?,?)"
        " ON CONFLICT(provider,external_kind,external_id) DO UPDATE SET"
        " metadata_json=excluded.metadata_json,last_synced_at=excluded.last_synced_at",
        (int(entity_id), provider, str(external_id), json.dumps(metadata, ensure_ascii=False),
         _now()))
    return BIND


def conflicts(profile: dict | None, actor: dict) -> list[dict]:
    """两站都有值却不一致的列：[{"field", "minnano-av", "avwikidb"}]。任一边没有值不算冲突。"""
    found = []
    for column, key in CROSS_CHECKED:
        mine, theirs = (profile or {}).get(column), actor.get(key)
        if mine is not None and theirs is not None and mine != theirs:
            found.append({"field": column, MINNANO: mine, AVWIKIDB: theirs})
    return found


def _alive(connection: sqlite3.Connection, entity_id: int, canonical: str) -> bool:
    current = alias._names(connection, entity_id)
    return current is not None and current[0] == canonical


def _record_review(root: Path, entity_id: int, rows: list[dict]) -> None:
    """复核产物里这位的那几行换成这一轮的判词；别人的行原样留着。"""
    from .review_csv import read_rows, write_rows

    path = Path(root) / REVIEW_FILE
    kept = [row for row in read_rows(path, missing_ok=True)
            if str(row.get("entity_id")) != str(entity_id)]
    write_rows(path, FIELDS, kept + rows, atomic=True, fill_missing=True)


# -- 执行 --------------------------------------------------------------------


def run(contract, key: str, handle) -> dict:
    """跑一条补女优资料后继，再按结论记下多久之后再派。"""
    summary = _run(contract, key, handle)
    with contract.database.read_connection() as connection:
        current = fingerprint(connection, parse_key(key))
    outcome = str(summary.get("outcome", ""))
    reports = summary.get("sites") or {}
    if any(str(report).startswith(UNFETCHED) for report in reports.values()):
        retry = RETRY_UNFETCHED
    elif summary.get("landed"):
        retry = REFRESH
    else:
        retry = None
    Attempts(attempts_root(contract.candidate_root)).record(key, current, outcome,
                                                            retry_after=retry)
    return summary


def _run(contract, key: str, handle) -> dict:
    entity_id = parse_key(key)
    batch = f"{SOURCE}@{getattr(handle, 'run_id', None) or time.strftime('%Y%m%dT%H%M%S')}"
    with contract.database.read_connection() as connection:
        names = alias._names(connection, entity_id)
        state = {"minnano": _refs(connection, entity_id, MINNANO),
                 "avwikidb": _refs(connection, entity_id, AVWIKIDB),
                 "codes": work_codes(connection, entity_id),
                 "fetched": profile_fetched_at(connection, entity_id)}
    if names is None:
        return {"outcome": "档案已不存在"}
    canonical = names[0]
    if handle is not None:
        handle.progress(label=_label(canonical), throttle=0)
    base = {"entity_id": entity_id, "canonical_name": canonical, "batch": ""}
    sites = open_sites(contract)
    reports: dict[str, str] = {}
    rows: list[dict] = []
    try:
        _visit_minnano(contract, sites[MINNANO], entity_id, names, state, batch, base, reports, rows)
        conflict_count = _visit_avwikidb(contract, sites[AVWIKIDB], entity_id, names, state, batch,
                                         base, reports, rows)
        _visit_javdb(contract, sites[JAVDB], entity_id, names, batch, base, reports, rows)
    finally:
        for pages in sites.values():
            pages.close()
    if any(row["action"] in (WRITE, BIND) for row in rows):
        contract.cache_bust()
    if rows:
        _record_review(contract.candidate_root, entity_id, rows)
    landed = [row for row in rows if row["action"] in (WRITE, BIND, FRESH)]
    done = [f"{row['site']} {row['action']}" for row in rows if row["action"] in (WRITE, BIND)]
    if done:
        outcome = "；".join(done)
    elif landed:
        outcome = "资料未过期"
    elif any(report.startswith(UNFETCHED) for report in reports.values()):
        outcome = UNFETCHED
    else:
        outcome = "哪一站都没对上她"
    summary = {"name": canonical, "outcome": outcome, "sites": reports, "landed": bool(landed)}
    if conflict_count:
        summary["conflicts"] = conflict_count
    return summary


def _visit_minnano(contract, pages, entity_id: int, names, state: dict, batch: str, base: dict,
                   reports: dict, rows: list) -> None:
    """minnano-av：进她的资料页，核对名字，整行写 `performer_profile`。"""
    canonical, aliases = names
    if _fresh(state["fetched"]):
        reports[MINNANO] = "资料未过期"
        rows.append({**base, "site": MINNANO, "page": "", "action": FRESH,
                     "detail": "资料取回不到 30 天"})
        return
    keys, refs = alias.search_keys(canonical, aliases), state["minnano"]
    if not keys and not refs:
        reports[MINNANO] = "未命中：没有能拿去检索的名字"
        return
    mine = {alias.match_key(name) for name in [canonical, *aliases] if not alias.rejection(name)}
    try:
        found, note = alias.minnano_profile_pages(pages, keys[:alias.MAX_KEYS], refs)
    except (alias.Blocked, alias.Unavailable) as error:
        reports[MINNANO] = f"{UNFETCHED}：{error}"
        return
    anchored = []
    for url, html in found:
        parsed = minnano_av.profile(html)
        listed = minnano_av.profile_names(html)
        written = [alias.clean(listed[0]), *(alias.clean(name) for name in listed[1])]
        if parsed is not None and alias._anchored(written, mine):
            anchored.append((url, parsed))
    if len(anchored) != 1:
        detail = (f"{len(anchored)} 张资料页都列着她" if anchored
                  else note or "资料页上没有数据库里她的名字")
        reports[MINNANO] = f"{MISS}：{detail}"
        rows.append({**base, "site": MINNANO, "page": "", "action": MISS, "detail": detail})
        return
    url, parsed = anchored[0]
    with contract.database.write_transaction() as connection:
        if not _alive(connection, entity_id, canonical):
            reports[MINNANO] = f"{MISS}：数据库里这条实体已经变了"
            return
        wrote = write_profile(connection, entity_id, parsed, source=batch, source_url=url)
        bound = ""
        if not refs:
            bound = bind(connection, entity_id, MINNANO, parsed["actress_id"],
                         {"source": SOURCE, "batch": batch})
    state["profile"] = parsed
    reports[MINNANO] = f"命中 {url}"
    rows.append({**base, "site": MINNANO, "page": url, "action": WRITE if wrote else MISS,
                 "batch": batch if wrote else "",
                 "detail": "资料页列着数据库里她的名字" if wrote else "这一行是人写的，不覆盖"})
    if bound:
        rows.append({**base, "site": MINNANO, "page": url, "action": bound,
                     "batch": batch if bound == BIND else "",
                     "detail": f"minnano-av 编号 {parsed['actress_id']}"
                               + ("" if bound == BIND else " 已属另一条实体")})


def _visit_avwikidb(contract, pages, entity_id: int, names, state: dict, batch: str, base: dict,
                    reports: dict, rows: list) -> int:
    """avwikidb：从她的作品页定编号，再读女优页做交叉核对。返回冲突条数。"""
    canonical = names[0]
    refs = state["avwikidb"]
    profile, fresh = _avwikidb_state(contract, entity_id, refs)
    if fresh:
        reports[AVWIKIDB] = "资料未过期"
        rows.append({**base, "site": AVWIKIDB, "page": avwikidb.ACTOR_PAGE.format(id=refs[0]),
                     "action": FRESH, "detail": "编号已绑，资料取回不到 30 天"})
        return 0
    if refs:
        actor_ref, work = refs[0], ""
    else:
        located = _locate_actor(pages, names, state, profile, base, reports, rows)
        if located is None:
            return 0
        actor_ref, work = located
    url = avwikidb.ACTOR_PAGE.format(id=actor_ref)
    try:
        actor = avwikidb.actor_profile(pages.get(url)[1]) or {}
    except (alias.Blocked, alias.Unavailable) as error:
        if refs:
            # 已绑的编号只为重取资料而来：没取到就留着上一次的资料，不拿一份空的顶掉。
            reports[AVWIKIDB] = f"{UNFETCHED}：{error}"
            return 0
        actor = {}
    if actor and actor.get("id") != actor_ref:
        actor = {}
    found = conflicts(state.get("profile") or profile, actor)
    with contract.database.write_transaction() as connection:
        if not _alive(connection, entity_id, canonical):
            reports[AVWIKIDB] = f"{MISS}：数据库里这条实体已经变了"
            return 0
        verdict = bind(connection, entity_id, AVWIKIDB, actor_ref,
                       _actor_metadata(actor, work, batch, found))
    return _record_bind(url, verdict, work, actor, found, batch, base, reports, rows)


def _avwikidb_state(contract, entity_id: int, refs: list[str]) -> tuple[dict | None, bool]:
    """(minnano-av 资料, 已绑编号的资料是否未过期)。"""
    with contract.database.read_connection() as connection:
        profile = read_profile(connection, entity_id)
        synced = connection.execute(
            "SELECT max(last_synced_at) FROM entity_external_ref WHERE entity_id=? AND provider=?",
            (entity_id, AVWIKIDB)).fetchone()[0] if refs else None
    return profile, bool(refs) and _fresh(_parse_stamp(synced))


def _locate_actor(pages, names, state: dict, profile: dict | None, base: dict, reports: dict,
                  rows: list) -> tuple[str, str] | None:
    """(女优编号, 番号)：从她作品的出演表定编号；定不下来写好判词，返回 None。"""
    canonical, aliases = names
    mine = {alias.match_key(name) for name in [canonical, *aliases] if name}
    romaji = avwikidb.romaji_key((state.get("profile") or profile or {}).get("romaji") or "")
    try:
        actor_ref, work, note = _find_in_cast(pages, state["codes"], mine, romaji)
    except (alias.Blocked, alias.Unavailable) as error:
        reports[AVWIKIDB] = f"{UNFETCHED}：{error}"
        return None
    if not actor_ref:
        reports[AVWIKIDB] = f"{MISS}：{note}"
        rows.append({**base, "site": AVWIKIDB, "page": "", "action": MISS, "detail": note})
        return None
    return actor_ref, work


def _actor_metadata(actor: dict, work: str, batch: str, found: list[dict]) -> dict:
    """外部编号的 `metadata_json`：带 source/batch 以便撤回，空值不写。"""
    metadata = {"source": SOURCE, "batch": batch, "work": work or None,
                "name": actor.get("name"), "kana": actor.get("kana"), "romaji": actor.get("romaji"),
                "image": actor.get("image"), "height": actor.get("height_cm"),
                "birthDate": actor.get("birth_date"), "conflicts": found or None}
    return {key: value for key, value in metadata.items() if value is not None}


def _record_bind(url: str, verdict: str, work: str, actor: dict, found: list[dict], batch: str,
                 base: dict, reports: dict, rows: list) -> int:
    """写绑定与冲突的判词行，返回记下的冲突条数。"""
    reports[AVWIKIDB] = (f"命中 {url}" if verdict == BIND else f"{TAKEN}：{url} 已属另一条实体")
    detail = f"{work} 的出演表里对得上她" if work else "编号已绑，重取女优页"
    if not actor:
        detail += "；女优页未取得，资料留空"
    rows.append({**base, "site": AVWIKIDB, "page": url, "action": verdict,
                 "batch": batch if verdict == BIND else "",
                 "detail": detail if verdict == BIND else "编号已属另一条实体"})
    if verdict != BIND:
        return 0
    for item in found:
        rows.append({**base, "site": AVWIKIDB, "page": url, "action": CONFLICT, "batch": batch,
                     "detail": f"{item['field']}：minnano-av {item[MINNANO]}，"
                               f"avwikidb {item[AVWIKIDB]}；资料表不改"})
    return len(found)


def _visit_javdb(contract, pages, entity_id: int, names, batch: str, base: dict, reports: dict,
                 rows: list) -> None:
    """javdb：按名字搜演员卡，卡上的 id 是她的就绑上。只给 JAV 女优，已有 id 的不再搜。"""
    canonical, aliases = names
    with contract.database.read_connection() as connection:
        refs = [{"provider": row[0], "external_kind": row[1], "external_id": row[2]}
                for row in connection.execute(
                    "SELECT provider,external_kind,external_id FROM entity_external_ref"
                    " WHERE entity_id=?", (int(entity_id),))]
    if not entry_links.is_jav_performer(refs):
        reports[JAVDB] = "不查：JAV 目录站里没有她"
        return
    if entry_links.provider_ids(refs).get(JAVDB):
        reports[JAVDB] = "编号已绑"
        return
    keys = alias.search_keys(canonical, aliases)[:alias.MAX_KEYS]
    if not keys:
        reports[JAVDB] = f"{MISS}：没有能拿去检索的名字"
        return
    mine = {alias.match_key(name) for name in [canonical, *aliases] if not alias.rejection(name)}
    cards, notes, searched = [], [], 0
    for key in keys:
        url = javdb.SEARCH.format(quote(key))
        try:
            _final, html = pages.get(url)
        except alias.Blocked as error:
            reports[JAVDB] = f"{UNFETCHED}：{error}"
            return
        except alias.Unavailable as error:
            notes.append(f"{key} {error}")
            continue
        if javdb.LOGIN.search(html):
            # 回 200 的登入页不是「搜过、站上没有她」：丢掉缓存，按未取得等下一轮。
            pages.forget(url)
            notes.append(f"{key} 回的是登入页")
            continue
        searched += 1
        cards = [card for card in javdb.search_cards(html)
                 if any(alias.match_key(name) in mine for name in card["names"])]
        if cards:
            break
        notes.append(f"{key} 搜不到她")
    if not cards:
        detail = "；".join(notes)
        if not searched:
            # 一个写法都没搜成：站那天没让进，不是她不在站上，不结算指纹。
            reports[JAVDB] = f"{UNFETCHED}：{detail}"
            return
        reports[JAVDB] = f"{MISS}：{detail}"
        rows.append({**base, "site": JAVDB, "page": "", "action": MISS, "detail": detail})
        return
    ids = [card["id"] for card in cards]
    if not javdb.one_person(cards):
        detail = f"站上同名不止一位：{'、'.join(ids)}"
        reports[JAVDB] = f"{MISS}：{detail}"
        rows.append({**base, "site": JAVDB, "page": url, "action": MISS, "detail": detail})
        return
    with contract.database.write_transaction() as connection:
        if not _alive(connection, entity_id, canonical):
            reports[JAVDB] = f"{MISS}：数据库里这条实体已经变了"
            return
        verdicts = [(card, bind(connection, entity_id, JAVDB, card["id"],
                                {"source": SOURCE, "batch": batch})) for card in cards]
    reports[JAVDB] = "；".join(f"{'命中' if verdict == BIND else TAKEN} {card['id']}"
                              for card, verdict in verdicts)
    for card, verdict in verdicts:
        record = f"（{card['record']}）" if card["record"] else ""
        rows.append({**base, "site": JAVDB, "page": url, "action": verdict,
                     "batch": batch if verdict == BIND else "",
                     "detail": f"演员卡 {card['id']}{record} 列着她的名字" if verdict == BIND
                               else f"演员 id {card['id']} 已属另一条实体"})


def _parse_stamp(value) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _find_in_cast(pages, codes: list[str], mine: set[str], romaji: str) -> tuple[str, str, str]:
    """(女优编号, 番号, 说明)：她作品出演表里恰好一位对得上的那位；对不上编号为空。

    对得上：站上的名字与账本里她的某个名字折叠后相等，或站上的罗马字与 minnano-av 上她的
    罗马字按词相等。一部里对上两位就停下判歧义，不再翻别的作品。站上没有这部（404）接着翻下一部；
    站拒绝访问照样抛 `Blocked`。
    """
    if not codes:
        return "", "", "没有能进作品页的番号"
    notes = []
    for code in codes:
        try:
            _final, html = pages.get(avwikidb.WORK_PAGE.format(code=code))
        except alias.Unavailable as error:
            notes.append(f"{code} {error}")
            continue
        cast = avwikidb.work_cast(html)
        matched = {person["id"]: person for person in cast
                   if alias.match_key(person["name"]) in mine
                   or (romaji and avwikidb.romaji_key(person["romaji"]) == romaji)}
        if len(matched) == 1:
            return next(iter(matched)), code, ""
        if matched:
            return "", "", f"{code} 的出演表里不止一位对得上：" + "、".join(
                person["name"] for person in matched.values())
        shown = "、".join(person["name"] for person in cast)
        notes.append(f"{code} 的出演表{'是 ' + shown if shown else '是空的'}")
    return "", "", "；".join(notes)


#: 写账本：资料进 `performer_profile`，编号进 `entity_external_ref`。取页在事务外做，
#: 写的那一下很短，照样走写账本那一条串行通道（ADR-0040 第二条）。
TYPE = register(FollowupType(task_key=TASK_KEY, label=TASK_LABEL, writes_ledger=True, run=run))
