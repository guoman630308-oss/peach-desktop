"""显式种子撤销与持久版本历史；不修改其他写入者的断言。"""
from __future__ import annotations

import json
import sqlite3

from . import seed_validation as validation
from .performer_profiles import read_profile


def check_history(connection: sqlite3.Connection, pack: dict) -> None:
    """同版绑定内容，旧格式与较低修订不得穿过已导入的水位。"""
    revision = pack.get("revision", 0)
    content_digest = validation.digest(pack)
    rows = connection.execute("SELECT version,revision,content_sha256,withdrawals_json FROM seed_pack_history").fetchall()
    incoming = {row["id"]: validation.canonical(row) for row in pack.get("withdrawals", [])}
    for version, previous_revision, digest, withdrawals in rows:
        if version == pack["version"] and digest != content_digest:
            raise ValueError("同版本种子内容与已导入历史不同")
        if previous_revision > revision or (revision and previous_revision == revision and version != pack["version"]):
            raise ValueError("种子 revision 不得回退或复用")
        if not revision and not previous_revision and version > pack["version"]:
            raise ValueError("旧格式种子版本不得回退")
        for old in json.loads(withdrawals):
            if incoming.get(old["id"]) != validation.canonical(old):
                raise ValueError("种子必须原样继承全部历史撤销")


def remember(connection: sqlite3.Connection, pack: dict, stamp: str) -> None:
    connection.execute("INSERT OR IGNORE INTO seed_pack_history(version,revision,content_sha256,withdrawals_json,imported_at)"
                       " VALUES(?,?,?,?,?)", (pack["version"], pack.get("revision", 0), validation.digest(pack),
                                              validation.canonical(pack.get("withdrawals", [])), stamp))


def _conflict(report: dict, withdrawal: dict, *, entity: str, detail: str, source: str = "") -> None:
    report["withdrawal_conflicts"].append({"id": withdrawal["id"], "kind": withdrawal["identity"]["kind"],
                                         "entity": entity, "field": withdrawal["field"], "source": source,
                                         "reason": withdrawal["reason"], "evidence": withdrawal["evidence"],
                                         "detail": detail})


def withdraw(connection: sqlite3.Connection, pack: dict, report: dict) -> None:
    from . import seed_pack

    for withdrawal in pack.get("withdrawals", []):
        identity = withdrawal["identity"]
        found = seed_pack.matches(connection, identity)
        if len(found) != 1:
            _conflict(report, withdrawal, entity=identity["name"],
                      detail="身份锚点未唯一命中，保留断言" if found else "身份锚点未命中，未取得可撤销实体")
            continue
        entity_id = found.pop()
        field, expected = withdrawal["field"], withdrawal["value"]
        if field == "alias":
            rows = connection.execute("SELECT rowid,alias,source FROM entity_alias WHERE entity_id=?", (entity_id,))
            candidates = [(rowid, source) for rowid, value, source in rows if validation.fact_key(field, value) == validation.fact_key(field, expected)]
            table = "entity_alias"
        elif field == "ref":
            rows = connection.execute("SELECT rowid,provider,external_kind,external_id,metadata_json FROM entity_external_ref WHERE entity_id=?", (entity_id,))
            candidates = [(rowid, "auto:seed" if seed_pack._metadata_ours(metadata) else _metadata_source(metadata))
                          for rowid, provider, kind, external_id, metadata in rows
                          if (provider, kind, external_id) == validation.ref_key(expected)]
            table = "entity_external_ref"
        elif field == "link":
            rows = connection.execute("SELECT rowid,link_kind,url,metadata_json FROM entity_link WHERE entity_id=?", (entity_id,))
            candidates = [(rowid, "auto:seed" if seed_pack._metadata_ours(metadata) else _metadata_source(metadata))
                          for rowid, kind, url, metadata in rows
                          if kind == expected["kind"] and seed_pack._normalised(url) == validation.url_key(expected["url"])]
            table = "entity_link"
        elif field == "profile":
            profile = read_profile(connection, entity_id)
            if profile is None:
                continue
            actual = {"fields": {key: profile[key] for key in expected["fields"]},
                      "source_url": profile["source_url"], "fetched_at": profile["fetched_at"]}
            # 非空列也必须完整匹配；部分资料不能充当整行删除的凭据。
            from .minnano_av import PROFILE_COLUMNS
            actual["fields"].update({key: profile[key] for key in PROFILE_COLUMNS if profile.get(key) is not None})
            actual["fields"]["tags"] = profile["tags"]
            wanted = {**expected, "fields": {**expected["fields"], "tags": expected["fields"].get("tags", [])}}
            if validation.fact_key(field, actual) != validation.fact_key(field, wanted):
                _conflict(report, withdrawal, entity=identity["name"], detail="现有资料与精确撤销值不一致，保留断言", source=profile["source"])
                continue
            candidates, table = [(entity_id, profile["source"])], "performer_profile"
        else:
            table, own, other, _kind, _count = seed_pack.POINTERS[field]
            row = connection.execute(f"SELECT p.rowid,e.canonical_name,p.source FROM {table} p JOIN entity e ON e.id=p.{other} WHERE p.{own}=?", (entity_id,)).fetchone()
            if row is None:
                continue
            if validation.fact_key(field, {"name": row[1]}) != validation.fact_key(field, expected):
                _conflict(report, withdrawal, entity=identity["name"], detail="现有归属与精确撤销值不一致，保留断言", source=row[2])
                continue
            candidates = [(row[0], row[2])]
        for rowid, source in candidates:
            if not seed_pack._ours(source):
                _conflict(report, withdrawal, entity=identity["name"], detail="断言归其他写入者所有，保留断言", source=source)
                continue
            connection.execute(f"DELETE FROM {table} WHERE rowid=?", (rowid,))
            report["withdrawn"] += connection.execute("SELECT changes()").fetchone()[0]


def _metadata_source(raw: str) -> str:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return ""
    return str(parsed.get("source", "")) if isinstance(parsed, dict) else ""
