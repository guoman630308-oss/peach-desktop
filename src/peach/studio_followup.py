"""刮削登记了新厂牌之后，把认得准的官网和标识补上（ADR-0052）。

新厂牌刚建出来时没有链接也没有图：厂牌页的大位退成首字母，筛选片上是一枚空圆。
补这两样的判据早就有，住在命令行那两趟里（`scripts/harvest_studio_sites.py`、
`scripts/harvest_studio_icons.py`），每一步都要人跑、人看、再跑一遍安装。这条后继让
证据已经确定的那部分不再等人，判据一个字都没新加，只是把它们接在刮削后面：

1. **官网**只收 `studio_sites.site_verdict` 判 `ok` 的一个地址：页面自述厂牌名且是成人站，
   或域名由厂牌名推出且是成人站。`weak`（同名的无关公司到这一步和真站分不开）照旧等人，
   这条后继不写它。已经有官网或平台链接的不再找。
2. **标识**走 `studio_icons.land_one`：取图链、方标／字标闸、共享主机守卫、「只换更大的」
   与命令行那一趟逐字相同，另外每一张要落盘的都过一次人像闸。人像闸本身不可用时一张不装。

每条写入都带归属串 `auto:studio-mark` 和这一轮的批次号：官网写在 `entity_link.metadata_json`
的 `source` 与 `batch`，标识写在边车 `.provenance.json` 的同名两项。判错了由
`scripts/revert_auto_landing.py` 按它们整批撤回。

幂等（ADR-0040 第六条）：第一件事看盘上有没有这家的图，有就当场返回；官网按
`UNIQUE(entity_id,url)` 写，重跑不会多出一行。
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import studio_icons, studio_sites
from .followups import Attempts, Followup, FollowupType, attempts_root, register

#: 这类后继在任务中心的身份，也是活动页上那一行的名字来源。
TASK_KEY = "studio-mark"
TASK_LABEL = "补厂牌官网与标识"

#: 这条后继写下的一切都带这个归属串，撤回按它认。
SOURCE = "auto:studio-mark"

#: 官网登记成哪一种链接、链接上写什么字，与人工复核装进去的那批相同。
OFFICIAL, OFFICIAL_LABEL = "official", "官方网站"

#: 取候选地址的间隔与超时，与命令行那一趟的缺省相同。
SITE_INTERVAL, SITE_TIMEOUT = 1.2, 8.0
ICON_INTERVAL, ICON_TIMEOUT = 1.5, 20.0

#: 每轮处理任务给存量厂牌的名额，在女优头像的存量之前取，用不满的留给头像。排在头像后面
#: 「补满」的话，没头像的女优一轮就把余下的名额用完：本机 2026-09-25 实测补厂牌一条都没
#: 派出去过，`変態紳士倶楽部` 官网 09-23 登记后一直没图。没图的厂牌只有二三十家，几轮就轮遍。
STOCK_SHARE = 8


def followup_key(entity_id: int) -> str:
    return f"{TASK_KEY}:{int(entity_id)}"


def parse_key(key: str) -> int:
    """把后继 key 拆回厂牌 id。形状不对就抛——那说明排队的行不是这个版本写的。"""
    prefix, _, raw = str(key).partition(":")
    if prefix != TASK_KEY or not raw.isdigit():
        raise ValueError(f"认不出这条补厂牌后继：{key}")
    return int(raw)


def has_mark(logo_root, name: str) -> bool:
    """盘上有没有这家的图。任何一个变体在就算有：缺的那一位由命令行那一趟补。"""
    safe = studio_icons.safe_name(name)
    return any((logo_root / f"{safe}{suffix}").exists()
               for suffix in (".img", ".icon.img", ".logo.img"))


def plan(connection: sqlite3.Connection, logo_root, *, since_entity_id: int) -> list[Followup]:
    """这一轮新登记、盘上又没有图的厂牌，一个一条后继，作品多的在前。

    「新登记」按实体 id 的水位判，与补头像那条同一个水位（`avatar_followup.plan`）。
    """
    rows = connection.execute(
        "SELECT e.id,e.canonical_name,"
        " (SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae"
        "  WHERE ae.entity_id=e.id) AS assets"
        " FROM entity e WHERE e.id>? AND e.kind='studio' ORDER BY e.id",
        (int(since_entity_id),)).fetchall()
    found = [(int(row[2] or 0), int(row[0]), str(row[1] or "")) for row in rows
             if not has_mark(logo_root, str(row[1] or ""))]
    found.sort(key=lambda item: (-item[0], item[1]))
    return [Followup(key=followup_key(entity_id), task_key=TASK_KEY,
                     label=f"{TASK_LABEL}：{name}" if name else TASK_LABEL)
            for _assets, entity_id, name in found]


def fingerprint(connection: sqlite3.Connection, entity_id: int) -> str:
    """会让这条后继结论变的量：作品数与官网、目录链接数。人补上一条链接就该再试。"""
    assets = connection.execute(
        "SELECT count(DISTINCT asset_id) FROM asset_entity WHERE entity_id=?",
        (int(entity_id),)).fetchone()[0]
    links = connection.execute(
        "SELECT count(*) FROM entity_link WHERE entity_id=? AND link_kind IN (?,?)",
        (int(entity_id), *studio_icons.ICON_LINK_KINDS)).fetchone()[0]
    return f"{assets}/{links}"


def stock(connection: sqlite3.Connection, logo_root, attempts, *, limit: int,
          skip=()) -> list[Followup]:
    """库里早就登记、盘上至今没有图的厂牌，作品多的在前，最多 `limit` 条（ADR-0053）。

    跑过一次、作品数与链接数都没变的不再派（`attempts`）。
    """
    if limit <= 0:
        return []
    skip = set(skip)
    found = []
    for row in connection.execute(
            "SELECT e.id,e.canonical_name,count(DISTINCT ae.asset_id) AS assets"
            " FROM entity e JOIN asset_entity ae ON ae.entity_id=e.id"
            " WHERE e.kind='studio' GROUP BY e.id ORDER BY assets DESC, e.id"):
        entity_id, name = int(row[0]), str(row[1] or "")
        key = followup_key(entity_id)
        if key in skip or has_mark(logo_root, name):
            continue
        if attempts.settled(key, fingerprint(connection, entity_id)):
            continue
        found.append(Followup(key=key, task_key=TASK_KEY,
                              label=f"{TASK_LABEL}：{name}" if name else TASK_LABEL))
        if len(found) >= limit:
            break
    return found


def run(contract, key: str, handle) -> dict:
    """跑一条补厂牌后继，再把厂牌当时的指纹记进 `Attempts`，存量补派按它判。"""
    summary = _run(contract, key, handle)
    with contract.database.read_connection() as connection:
        current = fingerprint(connection, parse_key(key))
    Attempts(attempts_root(contract.candidate_root)).record(
        key, current, str(summary.get("outcome", "")))
    return summary


def _run(contract, key: str, handle) -> dict:
    """跑一条补厂牌后继。返回的摘要就是活动页上那一行。"""
    entity_id = parse_key(key)
    logo_root = contract.logo_root
    batch = f"{SOURCE}@{getattr(handle, 'run_id', None) or time.strftime('%Y%m%dT%H%M%S')}"
    with contract.database.read_connection() as connection:
        row = connection.execute("SELECT canonical_name FROM entity WHERE id=? AND kind='studio'",
                                 (entity_id,)).fetchone()
        if row is None:
            # 实体被合并或删掉了。这不是失败：那件事已经不存在了。
            return {"outcome": "档案已不存在"}
        name = str(row[0] or "")
        if has_mark(logo_root, name):
            return {"name": name, "outcome": "已有标识"}
        linked = connection.execute(
            "SELECT count(*) FROM entity_link WHERE entity_id=? AND link_kind IN (?,?)",
            (entity_id, *studio_icons.ICON_LINK_KINDS)).fetchone()[0]
        aliases = studio_sites.load_aliases(connection, [entity_id]).get(entity_id, ())
    if handle is not None:
        handle.progress(label=f"{TASK_LABEL}：{name}", throttle=0)
    summary: dict[str, object] = {"name": name}
    if not linked:
        site = _land_site(contract, entity_id, name, aliases, batch)
        summary["site"] = site
        if site["outcome"] != "已登记":
            return {**summary, "outcome": f"官网{site['outcome']}"}
    return {**summary, **_land_mark(contract, entity_id, name, batch)}


def _land_site(contract, entity_id: int, name: str, aliases, batch: str) -> dict:
    """试推导出的候选地址，判 `ok` 的那一个直接登记成官网。"""
    record = {"entity_id": entity_id, "studio": name}
    other_names = tuple(alias for alias in aliases if alias != name)
    row, _last = studio_sites.discover(record, other_names, (), interval=SITE_INTERVAL,
                                       timeout=SITE_TIMEOUT)
    verdict = str(row["verdict"])
    if verdict != "ok":
        # `weak` 与 `未取得` 都不写：前者要人看，后者是没有证据。
        return {"outcome": "待人确认" if verdict == "weak" else "未取得",
                "verdict": verdict, "note": str(row["note"])[:200]}
    url = str(row["final_url"] or row["candidate_url"])
    now = datetime.now(timezone.utc).isoformat()
    metadata = {"source": SOURCE, "batch": batch, "verdict": verdict, "installed_at": now,
                "evidence": f"{row['note']}；标题「{row['title']}」", "sha256": row["sha256"]}
    with contract.database.write_transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO entity_link"
            "(entity_id,link_kind,label,url,hostname,is_sensitive,metadata_json,"
            " created_at,updated_at) VALUES(?,?,?,?,?,0,?,?,?)",
            (entity_id, OFFICIAL, OFFICIAL_LABEL, url, urlsplit(url).hostname or "",
             json.dumps(metadata, ensure_ascii=False), now, now))
    contract.cache_bust()
    return {"outcome": "已登记", "url": url}


def _land_mark(contract, entity_id: int, name: str, batch: str) -> dict:
    """按账本里这家的链接取图，可装的直接落盘。"""
    import httpx

    with contract.database.read_connection() as connection:
        entries = studio_icons.entity_links(connection, entity_id, name)
    candidate_dir = contract.candidate_root / "provider-cache" / "studio-icons"
    client = httpx.Client(trust_env=True, follow_redirects=True)
    try:
        landed = studio_icons.land_one(
            name, entries, contract.logo_root, candidate_dir,
            studio_icons.Fetcher(client, ICON_TIMEOUT, ICON_INTERVAL),
            studio_icons.FaceGate(), source=SOURCE, batch=batch)
    finally:
        client.close()
    if landed.get("installed"):
        contract.cache_bust()
    return {key: value for key, value in landed.items() if key != "rows"}


#: 写账本：官网登记在 `entity_link` 里。取图与试地址都在事务外做，写的那一下很短，
#: 但照样走写账本那一条串行通道（ADR-0040 第二条）。
TYPE = register(FollowupType(task_key=TASK_KEY, label=TASK_LABEL,
                             writes_ledger=True, run=run))
