"""刮削登记了新创作者之后，按页名去日本资料站查它是不是厂牌或系列（ADR-0098）。

文件名里的「创作者」常常不是人：`COSH こすっち` 是スコッチ的一个系列，每部出演者都不同。
通用搜索不收 sougouwiki、av_neme 这类站，命令行那一趟（`scripts/research_creator_identity_wikis.py`）
要人跑、人看、再交给 `apply_entity_identity_research.py` 写。这条后继把判据确定的那一半接在刮削
后面，判据一个字都没新加，全在 `entity_identity_research.wiki_finding`：

1. 页名候选是规范名、去掉番号前缀的名字和文件名里的发行前缀（`lookup_keys`），两站各按
   EUC-JP 直取，至多三个名字。
2. 同名页是三位以上出演者的作品一览，就记 `identity=release` 的 observed；人物页、出演者不足
   的页和不存在的页都不写，只把这一轮问过什么留在活动页那一行上。
3. 不改实体类型。改成厂牌或系列是人复核的那一步；observed 已经让它离开博主名册
   （`entity_classification.index_scope`）。

每条断言的来源都是批次号 `script:creator-identity-wiki@<任务行 id>`，判错了由
`scripts/revert_auto_landing.py --source script:creator-identity-wiki` 整批撤回。

身份面上已有可信断言（任何来源、任何值）的创作者一律不查，人复核过的不会被另一个来源冲掉。
幂等：第一件事就看这一条，写入按 `(entity_id,facet,value,source)` 唯一。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from . import entity_identity_research as research
from . import performer_alias_followup as alias
from .entity_classification import write_claim
from .followups import Attempts, Followup, FollowupType, attempts_root, register

#: 这类后继在任务中心的身份，也是活动页上那一行的名字来源。
TASK_KEY = "creator-identity"
TASK_LABEL = "查创作者身份"

#: 这条后继写下的断言来源都以它开头，后面接 `@<任务行 id>`；撤回按它认。
SOURCE = "script:creator-identity-wiki"

#: 每轮处理任务给存量创作者的名额，在女优头像的存量之前取。
STOCK_SHARE = 8
#: 结论是「未取得」时记号只保这么久：那一轮取决于站那天让不让进，不取决于名字。
RETRY_UNFETCHED = 24 * 3600
#: 入口判据的版本，进指纹。换了页名候选或作品一览判据就加一，跑过的创作者各再问一次。
ENTRY_RULE = 1


def followup_key(entity_id: int) -> str:
    return f"{TASK_KEY}:{int(entity_id)}"


def parse_key(key: str) -> int:
    """把后继 key 拆回创作者 id。形状不对就抛——那说明排队的行不是这个版本写的。"""
    prefix, _, raw = str(key).partition(":")
    if prefix != TASK_KEY or not raw.isdigit():
        raise ValueError(f"认不出这条查创作者身份后继：{key}")
    return int(raw)


def _label(name: str) -> str:
    return f"{TASK_LABEL}：{name}" if name else TASK_LABEL


def _open(connection: sqlite3.Connection, where: str, params: tuple) -> list[tuple[int, str]]:
    """身份还没有可信断言、名字能拿去查的创作者，作品多的在前。"""
    rows = connection.execute(
        "SELECT e.id,e.canonical_name,count(DISTINCT ae.asset_id) AS assets FROM entity e"
        " LEFT JOIN asset_entity ae ON ae.entity_id=e.id"
        " WHERE e.kind='creator' AND " + where + " AND NOT " + research.settled_sql()
        + " GROUP BY e.id ORDER BY assets DESC, e.id", params).fetchall()
    return [(int(row[0]), str(row[1] or "")) for row in rows if research.researchable(row[1])]


def plan(connection: sqlite3.Connection, *, since_entity_id: int) -> list[Followup]:
    """这一轮新登记的创作者，一个一条后继。「新登记」按实体 id 的水位判，与补头像那条同一个水位。"""
    return [Followup(key=followup_key(entity_id), task_key=TASK_KEY, label=_label(name))
            for entity_id, name in _open(connection, "e.id>?", (int(since_entity_id),))]


def fingerprint(connection: sqlite3.Connection, entity_id: int) -> str:
    """会让这条后继结论变的量：名字、别名数、作品数（文件名前缀跟着变）与入口判据版本。"""
    name = connection.execute("SELECT canonical_name FROM entity WHERE id=?", (int(entity_id),)).fetchone()
    aliases = connection.execute("SELECT count(*) FROM entity_alias WHERE entity_id=?",
                                 (int(entity_id),)).fetchone()[0]
    assets = connection.execute("SELECT count(DISTINCT asset_id) FROM asset_entity WHERE entity_id=?",
                                (int(entity_id),)).fetchone()[0]
    return f"{ENTRY_RULE}/{name[0] if name else ''}/{aliases}/{assets}"


def stock(connection: sqlite3.Connection, attempts, *, limit: int, skip=()) -> list[Followup]:
    """库里早就登记、身份至今没有可信断言的创作者，最多 `limit` 条（ADR-0053）。

    跑过一次、指纹没变的不再派（`attempts`）。
    """
    if limit <= 0:
        return []
    skip = set(skip)
    found = []
    for entity_id, name in _open(connection, "1", ()):
        key = followup_key(entity_id)
        if key in skip or attempts.settled(key, fingerprint(connection, entity_id)):
            continue
        found.append(Followup(key=key, task_key=TASK_KEY, label=_label(name)))
        if len(found) >= limit:
            break
    return found


def open_sites(contract) -> dict:
    """两站的取页器，页缓存与冷却和补别名后继同一份。测试把这一步整个换掉，网络就一次都不出。"""
    from .sources.seesaa import AV_NEME, SEESAA

    cache = Path(contract.candidate_root) / "provider-cache" / "seesaa-pages"
    cooldown = alias._cooldown_root(contract)
    return {config.name: alias.WikiSitePages(cache, cooldown, config=config)
            for config in (SEESAA, AV_NEME)}


def run(contract, key: str, handle) -> dict:
    """跑一条查创作者身份后继，再把它当时的指纹记进 `Attempts`，存量补派按它判。"""
    summary = _run(contract, key, handle)
    with contract.database.read_connection() as connection:
        current = fingerprint(connection, parse_key(key))
    outcome = str(summary.get("outcome", ""))
    Attempts(attempts_root(contract.candidate_root)).record(
        key, current, outcome, retry_after=RETRY_UNFETCHED if outcome == "未取得" else None)
    return summary


def _current(connection, entity_id: int):
    """(规范名, 还要不要查)；实体不在了交 None。"""
    row = connection.execute("SELECT canonical_name FROM entity WHERE id=? AND kind='creator'",
                             (entity_id,)).fetchone()
    if row is None:
        return None
    settled = connection.execute("SELECT " + research.settled_sql("?"), (entity_id,)).fetchone()[0]
    return str(row[0] or ""), not settled and research.researchable(row[0])


def _run(contract, key: str, handle) -> dict:
    entity_id = parse_key(key)
    batch = f"{SOURCE}@{getattr(handle, 'run_id', None) or time.strftime('%Y%m%dT%H%M%S')}"
    with contract.database.read_connection() as connection:
        current = _current(connection, entity_id)
        if current is None:
            # 实体被合并、删掉或改成厂牌了。这不是失败：那件事已经不存在了。
            return {"outcome": "档案已不存在"}
        name, open_ = current
        if not open_:
            return {"name": name, "outcome": "已有身份"}
        keys = research.lookup_keys(name, research.filename_prefixes(connection, [entity_id])[entity_id])
    if handle is not None:
        handle.progress(label=_label(name), throttle=0)
    sites = open_sites(contract)
    try:
        finding = research.wiki_finding(entity_id, name, keys, sites)
    finally:
        for pages in sites.values():
            pages.close()
    if finding is None:
        return {"name": name, "outcome": "未取得", "keys": keys}
    claims = [claim for claim in finding["claims"] if claim["status"] == "observed"]
    if not claims:
        return {"name": name, "outcome": "两站都没有作品一览", "asked": finding["claims"][0]["evidence"]}
    with contract.database.write_transaction() as connection:
        if _current(connection, entity_id) != (name, True):
            return {"name": name, "outcome": "已变"}
        for claim in claims:
            write_claim(connection, entity_id=entity_id, source=batch, **claim)
    contract.cache_bust()
    return {"name": name, "outcome": "是厂牌或系列", "page": claims[0]["source_url"],
            "evidence": claims[0]["evidence"]}


#: 写账本：断言进 `entity_classification`。取页在事务外做，写的那一下很短，
#: 但照样走写账本那一条串行通道（ADR-0040 第二条）。
TYPE = register(FollowupType(task_key=TASK_KEY, label=TASK_LABEL, writes_ledger=True, run=run))
