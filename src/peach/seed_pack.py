"""实体事实的私有种子包：只给已有实体填空（ADR-0073、ADR-0075、ADR-0111）。

导出的是女优、厂牌与事务所名下**能在公开站点上重新查到**的文字事实：别名、站上编号、
官网与社媒链接、女优资料表、所属事务所、label 归哪家片商。不导任何图像字节、本机路径、
刮削残片（`entity.metadata_json`、资料的 `raw_json`、链接的 `evidence`）与 Stash 的编号——
那些要么是本机的，要么是别的用户账本里不可能对上的。

导入按 ADR-0024 第五条：不造实体。包里的一位女优只有在本机账本已经登记了她（规范名、
别名或站上编号对得上）时才补，补的每一格都是空格（ADR-0052 第三条），归属串 `auto:seed`、
批次 `auto:seed@<版本>`，`scripts/revert_auto_landing.py --source auto:seed` 整批撤回。

会变的事实（所属事务所、label 的片商、资料表）里由种子自己写下的行，新版种子可以整行换掉。
格式 2 通过独立身份锚点、累计显式撤销和持久版本历史传递精确纠错；人或本机后继写的行不动。
一条对上本机两位实体的记进 `duplicates`：合并不可逆，不由种子动手。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import link_status
from . import seed_corrections, seed_validation
from .entities import _ref_is_free, normalize_entity_name
from .minnano_av import PROFILE_COLUMNS
from .performer_profiles import read_profile, write_profile
from .config import STATE_DIR
from .social_links import canonical_url

FORMAT = 1
SOURCE = "auto:seed"
DEFAULT_PACK = STATE_DIR / "seed" / "entities.json"
#: 导出的实体种类。创作者、系列与标签是本机馆藏的投影，不在这里。
KINDS = ("performer", "studio", "agency")
#: 不导的站上编号：Stash 的 id 是那台机器上另一份数据库的行号。
EXCLUDED_PROVIDERS = frozenset({"stash"})
#: 导出的链接类型：目录页与来源引用是采集残迹，不是这个人或这家公司自己的地址。
LINK_KINDS = ("official", "social")
COUNT_KEYS = ("entities", "aliases", "refs", "links", "profiles", "memberships", "makers")
#: 一对一指向的两张表：包里的键 → (表, 自己那列, 对面那列, 对面的实体种类, 报告里的计数键)。
#: 所属事务所与 label 的片商都是「主键在自己一侧，转手是覆盖」的形态（ADR-0049），导入共用一段逻辑。
POINTERS = {
    "agency": ("entity_membership", "member_id", "agency_id", "agency", "memberships"),
    "maker": ("label_maker", "label_id", "maker_id", "studio", "makers"),
}


def batch_of(version: str) -> str:
    return f"{SOURCE}@{version}"


def _ours(source: object) -> bool:
    text = str(source or "")
    return text == SOURCE or text.startswith(SOURCE + "@")


def _metadata_ours(raw: object) -> bool:
    try:
        metadata = json.loads(str(raw or "{}"))
    except ValueError:
        return False
    return isinstance(metadata, dict) and _ours(metadata.get("source"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _name(connection: sqlite3.Connection, entity_id: int) -> str:
    row = connection.execute("SELECT canonical_name FROM entity WHERE id=?", (entity_id,)).fetchone()
    return str(row[0]) if row else str(entity_id)


# -- 导出 ----------------------------------------------------------------------


def _aliases(connection: sqlite3.Connection, entity_id: int, kind: str, *, source_confidence: bool = False) -> list[dict]:
    """名下的别名写法，同一写法只留排在前面的那个来源；种子自己写的不再导出。

    女优的写法先过 ADR-0055 的判据：罗马字与短单名导入时本来就判「不收」，留在包里只会让
    匹配认出别人（`绫乃梓` 的 `Nozomi` 与另一位 `NOZOMI` 同键，两位都对上就整条跳过）。"""
    from .performer_alias_followup import rejection
    found: dict[str, dict] = {}
    for alias, source, confidence in connection.execute(
            "SELECT alias,source,confidence FROM entity_alias WHERE entity_id=? ORDER BY normalized_alias,source",
            (entity_id,)):
        if _ours(source) or not str(alias or "").strip():
            continue
        if kind == "performer" and rejection(str(alias)):
            continue
        if source_confidence and str(source).casefold().split(":", 1)[0] == "stash":
            continue
        fact = {"alias": str(alias), "source": str(source)}
        if source_confidence:
            fact["confidence"] = float(confidence)
        found.setdefault(normalize_entity_name(str(alias)), fact)
    return list(found.values())


def _refs(connection: sqlite3.Connection, entity_id: int) -> list[dict]:
    return [{"provider": str(provider), "kind": str(kind), "id": str(external_id)}
            for provider, kind, external_id, metadata in connection.execute(
                "SELECT provider,external_kind,external_id,metadata_json FROM entity_external_ref"
                " WHERE entity_id=? ORDER BY provider,external_kind,external_id", (entity_id,))
            if provider not in EXCLUDED_PROVIDERS and not _metadata_ours(metadata)]


def _normalised(url: str) -> str:
    """补 scheme、主机小写、改过名的站换现主机：导出与导入用同一个形态，本机存的
    `prime-Recruit.com` 才不会在导回时多出一条只差大小写的链接。"""
    url = str(url or "").strip()
    return canonical_url(url if urlsplit(url).scheme else "https://" + url) if url else ""


def _links(connection: sqlite3.Connection, entity_id: int) -> list[dict]:
    found = {}
    for kind, label, url, metadata in connection.execute(
            f"SELECT link_kind,label,url,metadata_json FROM entity_link WHERE entity_id=?"
            f" AND is_sensitive=0 AND {link_status.live_clause('metadata_json')}"
            f" AND link_kind IN ({','.join('?' * len(LINK_KINDS))}) ORDER BY url",
            (entity_id, *LINK_KINDS)):
        if not _metadata_ours(metadata) and _normalised(url):
            found.setdefault(_normalised(url), {"kind": str(kind), "label": str(label), "url": _normalised(url)})
    return [found[url] for url in sorted(found)]


def _profile(connection: sqlite3.Connection, entity_id: int) -> dict | None:
    """资料表按列导出，`raw` 是刮削残片不导；种子自己写的那一行不再导出。"""
    found = read_profile(connection, entity_id)
    if found is None or _ours(found.get("source")):
        return None
    fields = {column: found[column] for column in PROFILE_COLUMNS if found.get(column) is not None}
    fields["tags"] = list(found.get("tags") or [])
    return {"fields": fields, "source": str(found["source"]), "source_url": str(found["source_url"]),
            "fetched_at": str(found["fetched_at"])}


def _pointer(connection: sqlite3.Connection, entity_id: int, field: str) -> dict | None:
    """这位指向的那一个（所属事务所、label 的片商）：对面的规范名与这条关系的来源；种子写的不导。"""
    table, own, other, _kind, _count = POINTERS[field]
    row = connection.execute(
        f"SELECT t.canonical_name,p.source FROM {table} p JOIN entity t ON t.id=p.{other} WHERE p.{own}=?",
        (entity_id,)).fetchone()
    if row is None or _ours(row[1]):
        return None
    return {"name": str(row[0]), "source": str(row[1])}


def export_pack(connection: sqlite3.Connection, *, version: str, revision: int | None = None,
                withdrawals: list[dict] | None = None, previous: dict | None = None) -> dict:
    """账本里三类实体的公开事实。只有名字、没有任何可补事实的实体不进包：导入不造实体，
    光一个名字对别的账本没有用处。输出按种类与归一名排序，同一账本两次导出逐字节一致。"""
    entities, counts = [], dict.fromkeys(COUNT_KEYS, 0)
    for entity_id, kind, name in connection.execute(
            f"SELECT id,kind,canonical_name FROM entity WHERE kind IN ({','.join('?' * len(KINDS))})"
            " ORDER BY kind,normalized_name,id", KINDS):
        item = {"kind": str(kind), "name": str(name), "aliases": _aliases(connection, entity_id, str(kind), source_confidence=revision is not None),
                "refs": _refs(connection, entity_id), "links": _links(connection, entity_id)}
        if kind == "performer":
            item.update({key: value for key, value in (("profile", _profile(connection, entity_id)),
                                                       ("agency", _pointer(connection, entity_id, "agency")))
                         if value})
        elif kind == "studio":
            item.update({key: value for key, value in (("maker", _pointer(connection, entity_id, "maker")),)
                         if value})
        if not (item["aliases"] or item["refs"] or item["links"] or {"profile", "agency", "maker"} & set(item)):
            continue
        entities.append(item)
        counts["entities"] += 1
        counts["aliases"] += len(item["aliases"])
        counts["refs"] += len(item["refs"])
        counts["links"] += len(item["links"])
        counts["profiles"] += "profile" in item
        counts["memberships"] += "agency" in item
        counts["makers"] += "maker" in item
    pack = {"format": FORMAT, "version": version, "source": SOURCE, "counts": counts, "entities": entities}
    if revision is not None:
        inherited = list((previous or {}).get("withdrawals", []))
        if previous is not None:
            seed_validation.validate(previous)
            if revision <= previous.get("revision", 0) or version <= previous["version"]:
                raise ValueError("导出需要更新的 version 与 revision")
        by_id = {item["id"]: item for item in inherited}
        for item in withdrawals or []:
            if item["id"] in by_id and item != by_id[item["id"]]:
                raise ValueError("累计撤销 id 不得改写")
            by_id[item["id"]] = item
        for item in entities:
            item["identity"] = {"kind": item["kind"], "name": item["name"], "refs": item["refs"]}
        pack.update(format=2, revision=revision, withdrawals=[by_id[key] for key in sorted(by_id)])
        pack["content_sha256"] = seed_validation.digest(pack)
        seed_validation.validate(pack)
    elif withdrawals or previous:
        raise ValueError("累计撤销需要格式 2 的 revision")
    return pack


def dump(pack: dict) -> str:
    """落盘形态：键有序、一层缩进，改一位女优只动她那几行。"""
    return json.dumps(pack, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def load(path) -> dict:
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"种子 JSON 字段重复：{key}")
            result[key] = value
        return result

    pack = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_fields)
    return seed_validation.validate(pack)


# -- 导入 ----------------------------------------------------------------------


def matches(connection: sqlite3.Connection, item: dict) -> set[int]:
    """包里这一条按规范名、每个写法、每个站上编号各自去认，认出来的本机实体 id 全部返回。"""
    kind = item["kind"]
    keys = [normalize_entity_name(name) for name in
            (item["name"], *(alias["alias"] for alias in item.get("aliases") or []))]
    marks = ",".join("?" * len(keys))
    found = {int(row[0]) for row in connection.execute(
        f"SELECT id FROM entity WHERE kind=? AND normalized_name IN ({marks})"
        f" UNION SELECT e.id FROM entity e JOIN entity_alias a ON a.entity_id=e.id"
        f" WHERE e.kind=? AND a.normalized_alias IN ({marks})", (kind, *keys, kind, *keys))}
    refs = [ref for ref in item.get("refs") or [] if ref["provider"] not in EXCLUDED_PROVIDERS]
    if refs:
        clause = " OR ".join("(r.provider=? AND r.external_kind=? AND r.external_id=?)" for _ in refs)
        values = [value for ref in refs for value in (ref["provider"], ref["kind"], ref["id"])]
        found |= {int(row[0]) for row in connection.execute(
            f"SELECT e.id FROM entity_external_ref r JOIN entity e ON e.id=r.entity_id"
            f" WHERE e.kind=? AND ({clause})", (kind, *values))}
    return found


def match_entity(connection: sqlite3.Connection, item: dict) -> int | None:
    """包里这一条对应本机哪个实体：认出来的必须是同一位。

    认出两位就返回 None，哪怕其中一位是规范名逐字相同：名字对上 A、编号却挂在 B 名下，说明
    本机账本里这两条里至少有一条是错的，指错人比漏补糟得多，这样的留给人看。"""
    found = matches(connection, item)
    return found.pop() if len(found) == 1 else None


def _land_aliases(connection, entity_id: int, item: dict, version: str, batch: str, report: dict) -> int:
    """女优别名走补别名后继的判据（ADR-0055：不收的写法、已有、被占用）；厂牌与事务所
    只查同类实体里有没有人用着这个写法。"""
    names = [item["name"], *(alias["alias"] for alias in item.get("aliases") or [])]
    if item["kind"] == "performer":
        from .performer_alias_followup import WRITE, land
        canonical = connection.execute("SELECT canonical_name FROM entity WHERE id=?", (entity_id,)).fetchone()
        rows = land(connection, entity_id, str(canonical[0]), "seed", f"{SOURCE}:{version}", names, batch)
        _preserve_alias_confidence(connection, entity_id, item, batch, report,
                                   {row["alias"] for row in rows if row["action"] == WRITE})
        return sum(row["action"] == WRITE for row in rows)
    written, created = 0, set()
    for name in dict.fromkeys(names):
        key = normalize_entity_name(name)
        taken = connection.execute(
            "SELECT 1 FROM entity WHERE kind=? AND normalized_name=?"
            " UNION SELECT 1 FROM entity_alias a JOIN entity e ON e.id=a.entity_id"
            " WHERE e.kind=? AND a.normalized_alias=?", (item["kind"], key, item["kind"], key)).fetchone()
        if taken is None and key:
            connection.execute(
                "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
                " VALUES(?,?,?,?,1.0)", (entity_id, name, key, batch))
            changed = connection.execute("SELECT changes()").fetchone()[0]
            written += changed
            if changed:
                created.add(name)
    _preserve_alias_confidence(connection, entity_id, item, batch, report, created)
    return written


def _preserve_alias_confidence(connection, entity_id: int, item: dict, batch: str, report: dict,
                               created: set[str]) -> None:
    """来源置信度与用户复核状态独立，只维护种子自己拥有的行。"""
    for alias in item.get("aliases", []):
        if alias.get("confidence") is None:
            continue
        rows = connection.execute("SELECT alias,source,confidence FROM entity_alias WHERE entity_id=? AND normalized_alias=?",
                                  (entity_id, normalize_entity_name(alias["alias"]))).fetchall()
        for spelling, source, current in rows:
            if not _ours(source) or current == alias["confidence"]:
                continue
            connection.execute("UPDATE entity_alias SET confidence=?,source=? WHERE entity_id=? AND alias=? AND source=?",
                               (alias["confidence"], batch, entity_id, spelling, source))
            if spelling not in created:
                report["refreshed"] += connection.execute("SELECT changes()").fetchone()[0]


def _land_refs(connection, entity_id: int, item: dict, batch: str, stamp: str) -> int:
    """站上编号只在这个编号没人登记、这位在这家来源下也还没有编号时写：现成的
    `ON CONFLICT … DO UPDATE` 会把别人的编号改指到她，这里不用它。"""
    written = 0
    for ref in item.get("refs") or []:
        if ref["provider"] in EXCLUDED_PROVIDERS:
            continue
        held = connection.execute(
            "SELECT 1 FROM entity_external_ref WHERE provider=? AND external_kind=? AND external_id=?",
            (ref["provider"], ref["kind"], ref["id"])).fetchone()
        if held is not None or not _ref_is_free(connection, entity_id, ref["provider"], ref["kind"], ref["id"]):
            continue
        connection.execute(
            "INSERT OR IGNORE INTO entity_external_ref(entity_id,provider,external_kind,external_id,"
            "metadata_json,last_synced_at) VALUES(?,?,?,?,?,?)",
            (entity_id, ref["provider"], ref["kind"], ref["id"],
             json.dumps({"source": SOURCE, "batch": batch}, ensure_ascii=False), stamp))
        written += connection.execute("SELECT changes()").fetchone()[0]
    return written


def _land_links(connection, entity_id: int, item: dict, batch: str, stamp: str) -> int:
    """已有的链接按同一套归一规则比：`UNIQUE(entity_id,url)` 认字面，本机存着
    `prime-Recruit.com` 时只靠 `INSERT OR IGNORE` 会多出一条只差大小写的。"""
    have = {_normalised(url) for (url,) in connection.execute(
        "SELECT url FROM entity_link WHERE entity_id=?", (entity_id,))}
    written = 0
    for link in item.get("links") or []:
        url = _normalised(link.get("url"))
        if not url or url in have or link.get("kind") not in LINK_KINDS:
            continue
        have.add(url)
        connection.execute(
            "INSERT OR IGNORE INTO entity_link(entity_id,link_kind,label,url,hostname,is_sensitive,"
            "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?,?)",
            (entity_id, link["kind"], str(link.get("label") or urlsplit(url).hostname or url), url,
             urlsplit(url).hostname or "", json.dumps({"source": SOURCE, "batch": batch}, ensure_ascii=False),
             stamp, stamp))
        written += connection.execute("SELECT changes()").fetchone()[0]
    return written


def _land_profile(connection, entity_id: int, item: dict, batch: str, report: dict, *, revision: bool = False) -> None:
    """资料只补空行与种子自己的行。格式 1 按取回时间换新，格式 2 由包修订与内容决定纠错；
    本机后继与人工资料由自己的写入者维护。"""
    profile = item.get("profile")
    if not profile or item["kind"] != "performer":
        return
    current = read_profile(connection, entity_id)
    if current is not None:
        if not _ours(current.get("source")):
            return
        same = (all(current.get(key) == profile["fields"].get(key) for key in PROFILE_COLUMNS)
                and current.get("tags") == profile["fields"].get("tags", [])
                and current.get("source_url") == profile.get("source_url")
                and current.get("fetched_at") == profile.get("fetched_at"))
        if same or (not revision and str(profile.get("fetched_at") or "") <= str(current.get("fetched_at") or "")):
            return
    fields = dict(profile.get("fields") or {})
    fields["raw"] = {}
    write_profile(connection, entity_id, fields, source=batch,
                  source_url=str(profile.get("source_url") or ""),
                  fetched_at=str(profile.get("fetched_at") or "") or None)
    report["refreshed" if current is not None else "profiles"] += 1


def _loops(connection, table: str, own: str, other: str, start: int, target: int, limit: int = 32) -> bool:
    """从对面那位沿同一张表往上走会不会绕回自己：label 的片商可以再有片商（ADR-0051），成环不写。"""
    current, depth = target, 0
    while current is not None and depth < limit:
        if current == start:
            return True
        row = connection.execute(f"SELECT {other} FROM {table} WHERE {own}=?", (current,)).fetchone()
        current, depth = (int(row[0]) if row else None), depth + 1
    return False


def _land_pointer(connection, entity_id: int, item: dict, field: str, batch: str, stamp: str,
                  report: dict) -> None:
    """所属事务所与 label 的片商：本机没有就写，种子自己写的指向变了就换成新批次，人写的
    与包里不一致就记进 `conflicts`。不造对面那个实体，对面对不上就什么都不做。"""
    fact = item.get(field)
    if not fact:
        return
    table, own, other, kind, count = POINTERS[field]
    target = match_entity(connection, {"kind": kind, "name": fact["name"]})
    if target is None or target == entity_id or _loops(connection, table, own, other, entity_id, target):
        return
    row = connection.execute(
        f"SELECT p.{other},p.source,t.canonical_name FROM {table} p JOIN entity t ON t.id=p.{other}"
        f" WHERE p.{own}=?", (entity_id,)).fetchone()
    if row is None:
        connection.execute(f"INSERT INTO {table}({own},{other},source,confidence,checked_at) VALUES(?,?,?,1.0,?)",
                           (entity_id, target, batch, stamp))
        report[count] += 1
    elif int(row[0]) == target:
        return
    elif _ours(row[1]):
        connection.execute(f"UPDATE {table} SET {other}=?,source=?,checked_at=? WHERE {own}=?",
                           (target, batch, stamp, entity_id))
        report["refreshed"] += 1
    else:
        report["conflicts"].append({"kind": item["kind"], "entity": _name(connection, entity_id), "field": field,
                                    "ours": str(row[2]), "source": str(row[1]), "theirs": str(fact["name"])})


def _land_one(connection, entity_id: int, item: dict, version: str, batch: str, stamp: str, report: dict, *, revision: bool = False) -> None:
    report["aliases"] += _land_aliases(connection, entity_id, item, version, batch, report)
    report["refs"] += _land_refs(connection, entity_id, item, batch, stamp)
    report["links"] += _land_links(connection, entity_id, item, batch, stamp)
    _land_profile(connection, entity_id, item, batch, report, revision=revision)
    for field in POINTERS:
        _land_pointer(connection, entity_id, item, field, batch, stamp, report)


def written(report: dict) -> int:
    """这次导入动了多少行：补上的加换掉的。"""
    return sum(int(report.get(key, 0)) for key in (*COUNT_KEYS[1:], "refreshed", "withdrawn"))


def land(connection: sqlite3.Connection, pack: dict) -> dict:
    """把包里的事实补进这本账，返回逐类计数与两张明细（`conflicts`、`duplicates`）。
    调用方负责事务与备份；dry-run 走 `plan`。同一包再跑一遍不会再动任何一行。"""
    seed_validation.validate(pack)
    if not connection.in_transaction:
        connection.execute("BEGIN")
    connection.execute("SAVEPOINT seed_landing")
    try:
        seed_corrections.check_history(connection, pack)
        report = _land_pack(connection, pack)
        seed_corrections.remember(connection, pack, _now())
        connection.execute("RELEASE seed_landing")
        return report
    except BaseException:
        connection.execute("ROLLBACK TO seed_landing")
        connection.execute("RELEASE seed_landing")
        raise


def _land_pack(connection: sqlite3.Connection, pack: dict) -> dict:
    version, batch, stamp = str(pack["version"]), batch_of(str(pack["version"])), _now()
    report = {"version": version, "batch": batch, "matched": 0, "unmatched": 0,
              **dict.fromkeys(COUNT_KEYS[1:], 0), "refreshed": 0, "conflicts": [], "duplicates": []}
    revision = pack["format"] == 2
    if revision:
        report.update(withdrawn=0, withdrawal_conflicts=[])
        seed_corrections.withdraw(connection, pack, report)
    for item in pack.get("entities") or []:
        found = matches(connection, item["identity"] if revision else item)
        if len(found) > 1:
            report["duplicates"].append({"kind": item["kind"], "name": item["name"],
                                         "entities": sorted(_name(connection, entity_id) for entity_id in found)})
            continue
        if not found:
            report["unmatched"] += 1
            continue
        report["matched"] += 1
        _land_one(connection, found.pop(), item, version, batch, stamp, report, revision=revision)
    return report


def plan(connection: sqlite3.Connection, pack: dict) -> dict:
    """dry-run：在只读账本的内存副本上跑一遍 `land`，交出同样的报告，本机账本一字不动。"""
    scratch = sqlite3.connect(":memory:")
    try:
        connection.backup(scratch)
        scratch.execute("PRAGMA foreign_keys=ON")
        return land(scratch, pack)
    finally:
        scratch.close()
