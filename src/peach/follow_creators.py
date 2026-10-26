"""关注作者建档：关注来源里的作者落成 `creator` 实体（ADR-0096）。

关注页把同一个人在几个站上的来源归成一组（`follow_identity.author_key`），但那一组只活在
关注页里：没有资料页，名册上点开只能回关注页筛选。这条后继把每一组落成一条创作者实体，
绑上它名下的来源，再把来源数据里已经有的正版发布渠道与社媒账号登记成实体链接。

判据全是确定的，不联网：

1. **建谁**：没绑实体、作者键是名字（`name:`）的每一组。名字取关注页那一份
   （`follow_identity.author_name`），过 `canonicalize_entity_name('creator')` 那道平台名、
   结构目录与转载站的闸，被挡的整组跳过。
2. **不建、等人**：账本里已有同名或同别名的创作者，或两组归一成同一个名字。姓名相同不能
   自动建立关联（ADR-0095），这几组原样留着，摘要里列出来。
3. **并进已建的**：新来源与某个已建档实体名下的来源，在绑定前本来就归同一个名字键——
   那是关注页自己的分组，不是新的同名推断，直接绑过去。
4. **链接**：官方来源自己的地址；归档站上的原始账号（FANBOX 的数字 id 就是 pixiv 用户 id，
   Patreon 用户 id 拼 `user?u=`，OnlyFans 用户名）；名片里可信的那几条
   （`follow_identity.trusted_profile_links`）。归档站与聚合站本身不登记，`follow_source`
   的绑定就是它们的记录。

链接只在绑定那一刻写：之后人删掉的一条不会在下一轮又长回来。每条写入都带归属串
`auto:follow-creator` 与这一轮的批次号，`scripts/revert_auto_landing.py` 按它们整批撤回：
解绑来源、删链接与别名，实体本身只在没有别的引用时删。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .entities import canonicalize_entity_name, normalize_entity_name
from .follow_identity import author_display_name, author_name, name_key, trusted_profile_links
from .follow_sources import KemonoConnector
from .follow_store import FollowStore
from .followups import FollowupType, register

TASK_KEY = "follow-creator"
TASK_LABEL = "关注作者建档"

#: 这条后继写下的一切都带这个归属串，撤回按它认。
SOURCE = "auto:follow-creator"

#: 名片服务 → 链接种类与站名。正版发布渠道记 `official`，社媒记 `social`；
#: 站名与账本里已有的那批同写法（`X`、`Instagram`、`OnlyFans`）。
CHANNELS = {
    "fanbox": ("official", "FANBOX"),
    "patreon": ("official", "Patreon"),
    "subscribestar": ("official", "SubscribeStar"),
    "gumroad": ("official", "Gumroad"),
    "fantia": ("official", "Fantia"),
    "itchio": ("official", "itch.io"),
    "kofi": ("official", "Ko-fi"),
    "onlyfans": ("official", "OnlyFans"),
    "twitter": ("social", "X"),
    "pixiv": ("social", "pixiv"),
    "deviantart": ("social", "DeviantArt"),
    "instagram": ("social", "Instagram"),
    "bsky": ("social", "Bluesky"),
}

#: 名片手柄 → 规范地址。论坛正文里贴的地址带跟踪参数、`http`、`/posts` 之类，
#: 同一个账号三种写法；按手柄重拼，同一个人的同一个账号只登记一条。
_PROFILE_URLS = {
    "fanbox": "https://{}.fanbox.cc/",
    "patreon": "https://www.patreon.com/{}",
    "gumroad": "https://{}.gumroad.com/",
    "fantia": "https://fantia.jp/fanclubs/{}",
    "itchio": "https://{}.itch.io/",
    "kofi": "https://ko-fi.com/{}",
    "twitter": "https://x.com/{}",
    "pixiv": "https://www.pixiv.net/users/{}",
    "deviantart": "https://www.deviantart.com/{}",
    "instagram": "https://www.instagram.com/{}",
    "bsky": "https://bsky.app/profile/{}",
}

#: 自己就是正版渠道的关注来源：来源地址原样就是那个账号的主页。
_OFFICIAL_PROVIDERS = ("fanbox", "subscribestar", "patreon")

#: 归档站上 `服务/用户` 还原成原站账号。只收核实过形状的：FANBOX 归档的数字 id 是
#: pixiv 用户 id（`follow_identity.official_avatar_url` 靠它取官方头像），Patreon 的是用户 id，
#: OnlyFans 的是用户名。Gumroad、Fantia 的归档 id 形状未核实，不还原。
_ARCHIVE_ACCOUNTS = {
    "fanbox": ("pixiv", "https://www.pixiv.net/users/{}", str.isdigit),
    "patreon": ("patreon", "https://www.patreon.com/user?u={}", str.isdigit),
    "onlyfans": ("onlyfans", "https://onlyfans.com/{}",
                 lambda value: value.replace("_", "").replace(".", "").isalnum()),
}


@dataclass
class Landing:
    """一位关注作者这一轮怎么处理。`action` 是 `create`、`join`、`hold` 或 `skip`。"""
    key: str
    name: str
    action: str
    source_ids: tuple[int, ...]
    links: tuple[dict, ...] = ()
    aliases: tuple[str, ...] = ()
    entity_id: int | None = None
    reason: str = ""
    providers: tuple[str, ...] = ()


def source_links(row) -> list[dict]:
    """一条关注来源能证明的作者账号：`service`、`url` 与证据说明。"""
    provider = str(row["provider"] or "")
    found: list[dict] = []
    if provider in _OFFICIAL_PROVIDERS and row["url"]:
        found.append({"service": provider, "url": str(row["url"]),
                      "evidence": f"{provider} 关注来源本身"})
    if provider in KemonoConnector.HOSTS:
        service, _, user = str(row["ref"] or "").partition("/")
        mapped = _ARCHIVE_ACCOUNTS.get(service)
        if mapped and user and mapped[2](user):
            found.append({"service": mapped[0], "url": mapped[1].format(user),
                          "evidence": f"{provider} 归档的原站账号 {service}/{user}"})
    for link in trusted_profile_links(row):
        service = str(link.get("service") or "")
        handle = str(link.get("handle") or "").strip()
        if service not in CHANNELS or not handle:
            continue
        template = _PROFILE_URLS.get(service)
        if template:
            url = template.format(handle)
        else:
            # SubscribeStar 有 `.adult` 与 `.com` 两个主机，按名片上那个拼。
            host = (urlsplit(str(link.get("url") or "")).hostname or "").removeprefix("www.")
            if not host:
                continue
            url = f"https://{host}/{handle}"
        found.append({"service": service, "url": url,
                      "evidence": f"{provider} 名片 {service}/{handle}"})
    return found


def _group_links(rows) -> tuple[dict, ...]:
    """一组来源的链接去重。同一个 Patreon 账号既有手柄地址又有 `user?u=` 时只留手柄那条。"""
    found: dict[str, dict] = {}
    for row in rows:
        for link in source_links(row):
            found.setdefault(link["url"].casefold().rstrip("/"), link)
    by_handle = {link["service"] for link in found.values() if "user?u=" not in link["url"]}
    return tuple(link for link in found.values()
                 if not ("user?u=" in link["url"] and link["service"] in by_handle))


def _name_held(connection: sqlite3.Connection, normalized: str) -> int | None:
    """账本里已经叫这个名字（正名或别名）的创作者。"""
    row = connection.execute(
        "SELECT id FROM entity WHERE kind='creator' AND (normalized_name=? OR id IN"
        " (SELECT entity_id FROM entity_alias WHERE normalized_alias=?)) ORDER BY id LIMIT 1",
        (normalized, normalized)).fetchone()
    return int(row[0]) if row else None


def plan(connection: sqlite3.Connection) -> list[Landing]:
    """这一轮每一位还没建档的关注作者怎么处理。只读，同样的账本给同样的结论。"""
    store = FollowStore(lambda: connection)
    rows = store.sources()
    alias_map, alias_groups = store.author_aliases()
    canonical = {f"name:{group['canonical_key']}": str(group["canonical_name"] or "")
                 for group in alias_groups}
    alias_names = {f"name:{group['canonical_key']}": [alias["name"] for alias in group["aliases"]]
                   for group in alias_groups}
    joined: dict[str, set[int]] = {}
    pending: dict[str, list] = {}
    for row in rows:
        key = name_key(row, alias_map)
        if row["entity_id"]:
            joined.setdefault(key, set()).add(int(row["entity_id"]))
        elif key.startswith("name:"):
            pending.setdefault(key, []).append(row)
    landings: list[Landing] = []
    for key, group in sorted(pending.items()):
        ids = tuple(int(row["id"]) for row in group)
        providers = tuple(dict.fromkeys(str(row["provider"]) for row in group))
        links = _group_links(group)
        owners = joined.get(key, set())
        if len(owners) == 1:
            entity_id = next(iter(owners))
            landings.append(Landing(key, "", "join", ids, links, entity_id=entity_id,
                                    providers=providers,
                                    reason="同一名字键的来源已建档"))
            continue
        name = canonicalize_entity_name("creator", author_name(group, key, canonical))
        if len(owners) > 1:
            landings.append(Landing(key, name, "hold", ids, providers=providers,
                                    reason="同一名字键的来源分属几个实体"))
            continue
        if not name:
            landings.append(Landing(key, author_name(group, key, canonical), "skip", ids,
                                    providers=providers, reason="名字是平台、目录或转载站"))
            continue
        held = _name_held(connection, normalize_entity_name(name))
        if held is not None:
            landings.append(Landing(key, name, "hold", ids, links, entity_id=held,
                                    providers=providers, reason="数据库里已有同名创作者"))
            continue
        normalized = normalize_entity_name(name)
        spellings = [author_display_name(row) for row in group] + alias_names.get(key, [])
        aliases = tuple(dict.fromkeys(
            spelling for spelling in spellings
            if spelling and normalize_entity_name(spelling) not in ("", normalized)
            and _name_held(connection, normalize_entity_name(spelling)) is None))
        landings.append(Landing(key, name, "create", ids, links, aliases,
                                providers=providers))
    # 两组归一成同一个名字：谁先谁后都是猜，两组都留给人。
    counts: dict[str, int] = {}
    for landing in landings:
        if landing.action == "create":
            counts[normalize_entity_name(landing.name)] = counts.get(
                normalize_entity_name(landing.name), 0) + 1
    for landing in landings:
        if landing.action == "create" and counts[normalize_entity_name(landing.name)] > 1:
            landing.action, landing.links, landing.aliases = "hold", (), ()
            landing.reason = "几组关注作者归一成同一个名字"
    return landings


def apply(connection: sqlite3.Connection, landings: list[Landing], *, batch: str,
          source: str = SOURCE, now: str | None = None) -> dict:
    """把 `create` 与 `join` 两类写进账本。调用方给写事务；重跑不会多出行。"""
    stamp = now or datetime.now(timezone.utc).isoformat()
    created = bound = linked = aliased = 0
    for landing in landings:
        if landing.action not in ("create", "join"):
            continue
        entity_id = landing.entity_id
        if landing.action == "create":
            normalized = normalize_entity_name(landing.name)
            if _name_held(connection, normalized) is not None:
                continue
            entity_id = int(connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,metadata_json,"
                "created_at,updated_at) VALUES('creator',?,?,?,?,?)",
                (landing.name, normalized,
                 json.dumps({"source": source, "batch": batch, "author_key": landing.key},
                            ensure_ascii=False), stamp, stamp)).lastrowid)
            created += 1
        bound += connection.execute(
            f"UPDATE follow_source SET entity_id=?,updated_at=? WHERE entity_id IS NULL"
            f" AND id IN ({','.join('?' * len(landing.source_ids))})",
            (entity_id, stamp, *landing.source_ids)).rowcount
        for link in landing.links:
            kind, label = CHANNELS[link["service"]]
            metadata = {"source": source, "batch": batch, "verdict": "关注来源",
                        "evidence": link["evidence"], "installed_at": stamp}
            linked += connection.execute(
                "INSERT OR IGNORE INTO entity_link(entity_id,link_kind,label,url,hostname,"
                "is_sensitive,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?,?)",
                (entity_id, kind, label, link["url"], urlsplit(link["url"]).hostname or "",
                 json.dumps(metadata, ensure_ascii=False), stamp, stamp)).rowcount
        for alias in landing.aliases:
            aliased += connection.execute(
                "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,"
                "confidence) VALUES(?,?,?,?,1.0)",
                (entity_id, alias, normalize_entity_name(alias), batch)).rowcount
    return {"created": created, "bound": bound, "links": linked, "aliases": aliased}


#: 人在资料页上点「是同一个人」写下的归属。人的判断不进自动撤回。
CONFIRM_SOURCE = "user:follow-creator"


def held_for(connection: sqlite3.Connection, entity_id: int) -> list[Landing]:
    """因为和这位创作者同名而等人的关注作者。"""
    return [landing for landing in plan(connection)
            if landing.action == "hold" and landing.entity_id == int(entity_id)]


def confirm(connection: sqlite3.Connection, entity_id: int, key: str) -> dict:
    """人确认「关注里那位就是这位创作者」：绑上那一组来源，补上它们证明的账号。

    只认此刻仍在等这位的那一组：页面停在旧状态时点下去，那组可能已经绑走或改了名。
    """
    landing = next((item for item in held_for(connection, entity_id) if item.key == key), None)
    if landing is None:
        raise ValueError("这组关注作者已经不在等确认了，刷新后再看")
    landing.action = "join"
    stamp = datetime.now(timezone.utc).isoformat()
    return apply(connection, [landing], batch=f"{CONFIRM_SOURCE}@{stamp}",
                 source=CONFIRM_SOURCE, now=stamp)


def summarize(landings: list[Landing]) -> dict:
    """活动页上那一行：建了几位、并进几组，等人的那几位写名字和原因。"""
    held = [f"{landing.name}（{landing.reason}）" for landing in landings
            if landing.action == "hold"]
    return {"create": sum(landing.action == "create" for landing in landings),
            "join": sum(landing.action == "join" for landing in landings),
            "hold": held[:12], "held": len(held),
            "skip": sum(landing.action == "skip" for landing in landings)}


def declare(connection: sqlite3.Connection) -> list[dict]:
    """关注检查结算时声明的后继：有要建档或并入的作者就派一条，没有就不派。

    一轮只派一条、键固定：后继自己把整张表看一遍，排着的那条没跑完时再声明也只是去重。
    """
    if not any(landing.action in ("create", "join") for landing in plan(connection)):
        return []
    return [{"key": f"{TASK_KEY}:all", "task_key": TASK_KEY, "label": TASK_LABEL}]


def run(contract, key: str, handle) -> dict:
    """跑一轮建档。幂等：已绑的来源不再出现在计划里，重跑只是一张空计划。"""
    if key != f"{TASK_KEY}:all":
        raise ValueError(f"认不出这条关注作者建档后继：{key}")
    batch = f"{SOURCE}@{getattr(handle, 'run_id', None) or time.strftime('%Y%m%dT%H%M%S')}"
    with contract.database.write_transaction() as connection:
        landings = plan(connection)
        written = apply(connection, landings, batch=batch)
    if written["created"] or written["bound"]:
        contract.cache_bust()
    return {**summarize(landings), **written, "batch": batch}


def _referenced(connection: sqlite3.Connection, entity_id: int) -> bool:
    """还有没有别的行指着这条实体。外键清单从 schema 读，新表不用回来改这里。"""
    for (table,) in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'"):
        for foreign in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
            if foreign[2] == "entity" and connection.execute(
                    f'SELECT 1 FROM "{table}" WHERE "{foreign[3]}"=? LIMIT 1',
                    (entity_id,)).fetchone():
                return True
    return False


def planned_revert(connection: sqlite3.Connection, source: str, batch: str) -> list[dict]:
    """这条后继建下的实体。链接与别名由撤回脚本按 `source`／`batch` 另认。"""
    if source != SOURCE:
        return []
    found = []
    for row in connection.execute(
            "SELECT id,canonical_name,metadata_json FROM entity WHERE kind='creator'"
            " AND metadata_json LIKE ?", (f"%{SOURCE}%",)):
        try:
            metadata = json.loads(row[2] or "{}")
        except ValueError:
            continue
        if metadata.get("source") == SOURCE and (not batch or metadata.get("batch") == batch):
            sources = connection.execute(
                "SELECT count(*) FROM follow_source WHERE entity_id=?", (row[0],)).fetchone()[0]
            found.append({"entity_id": int(row[0]), "name": str(row[1]),
                          "batch": str(metadata.get("batch") or ""), "sources": int(sources)})
    return found


def revert(connection: sqlite3.Connection, rows: list[dict]) -> dict:
    """解绑来源，再删没有别的引用的实体。链接与别名要先由调用方删掉。

    人后来给这位挂上的作品、资料或链接都算引用：那条实体就留着，只解绑来源。
    """
    unbound = removed = 0
    for row in rows:
        unbound += connection.execute(
            "UPDATE follow_source SET entity_id=NULL WHERE entity_id=?",
            (row["entity_id"],)).rowcount
        if not _referenced(connection, row["entity_id"]):
            removed += connection.execute(
                "DELETE FROM entity WHERE id=?", (row["entity_id"],)).rowcount
    return {"unbound": unbound, "removed": removed}


#: 写账本：建实体、绑来源、登记链接都在一个写事务里，走写账本那一条串行通道。
TYPE = register(FollowupType(task_key=TASK_KEY, label=TASK_LABEL,
                             writes_ledger=True, run=run))
