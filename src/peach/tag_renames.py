"""把账本里的退役标签名改成规范名，没有接替者的整条删掉。

名单是 `catalog_rules.RETIRED_TAGS`（一件事只留一个名字）与 `catalog_rules.DROPPED_TAGS`
（撤掉的粗桶）。写这一步的入口是 `scripts/rename_retired_tags.py`，本模块只提供判定与写入。
"""
from __future__ import annotations

from datetime import datetime, timezone
from sqlite3 import Connection

from .catalog_rules import DROPPED_TAGS, RETIRED_TAGS
from .entities import merge_entity, normalize_entity_name

#: 原样记录来源怎么说的命名空间，不参与改名。
#
#: `pixiv_tag` 是作者自己打的标签，和策展词表不是一回事：把它改成 Peach 的规范名，
#: 等于事后修改来源的原话，以后再也分不清哪些词是 pixiv 上真有的。
RAW_TAG_SOURCES = frozenset({"pixiv_tag"})

FIELDS = ("old", "new", "assets", "merged", "raw_kept", "entity")


def _placeholders(values) -> str:
    return ",".join("?" * len(values))


def collect(connection: Connection) -> list[dict]:
    """每个退役名在账本里的实际存量，按改动量从多到少。

    `merged` 是那一侧资产上两个名字都有的条数：改名会撞上 `UNIQUE(asset_id, tag)`，
    这种行只能删掉旧的，不然一次改名就要报一次冲突。
    """
    raw = sorted(RAW_TAG_SOURCES)
    holes = _placeholders(raw)
    rows = []
    for old, new in sorted(RETIRED_TAGS.items()):
        curated = connection.execute(
            f"SELECT count(*) FROM asset_tag WHERE tag=? AND source NOT IN ({holes})",
            (old, *raw)).fetchone()[0]
        merged = connection.execute(
            "SELECT count(*) FROM asset_tag a WHERE a.tag=? "
            f"AND a.source NOT IN ({holes}) "
            "AND EXISTS(SELECT 1 FROM asset_tag b WHERE b.asset_id=a.asset_id AND b.tag=?)",
            (old, *raw, new)).fetchone()[0]
        kept = connection.execute(
            f"SELECT count(*) FROM asset_tag WHERE tag=? AND source IN ({holes})",
            (old, *raw)).fetchone()[0]
        source_id = _entity_id(connection, old)
        entity = ""
        if source_id is not None:
            entity = "合并" if _entity_id(connection, new) is not None else "改名"
        rows.append({"old": old, "new": new, "assets": curated - merged, "merged": merged,
                     "raw_kept": kept, "entity": entity})
    for old in sorted(DROPPED_TAGS):
        curated = connection.execute(
            f"SELECT count(*) FROM asset_tag WHERE tag=? AND source NOT IN ({holes})",
            (old, *raw)).fetchone()[0]
        kept = connection.execute(
            f"SELECT count(*) FROM asset_tag WHERE tag=? AND source IN ({holes})",
            (old, *raw)).fetchone()[0]
        entity = "删除" if _entity_id(connection, old) is not None else ""
        rows.append({"old": old, "new": "", "assets": curated, "merged": 0,
                     "raw_kept": kept, "entity": entity})
    rows.sort(key=lambda row: (-(row["assets"] + row["merged"]), row["old"]))
    # 两个退役名指向同一个标签时，先改的那个把实体造出来，后改的那个就只能合并。
    # 预览按 `apply_rows` 的顺序推一遍，报出来的才是真会发生的事。
    claimed: set[str] = set()
    for row in rows:
        if row["entity"] == "改名" and row["new"] in claimed:
            row["entity"] = "合并"
        if row["entity"]:
            claimed.add(row["new"])
    return rows


def _entity_id(connection: Connection, name: str) -> int | None:
    found = connection.execute(
        "SELECT id FROM entity WHERE kind='tag' AND normalized_name=?",
        (normalize_entity_name(name),)).fetchone()
    return int(found[0]) if found else None


def apply_rows(connection: Connection, rows: list[dict], now: str | None = None) -> dict:
    """按 `collect` 的结果改名。调用方负责事务与备份。"""
    stamp = now or datetime.now(timezone.utc).isoformat()
    raw = sorted(RAW_TAG_SOURCES)
    holes = _placeholders(raw)
    counts = {"tags": 0, "dropped": 0, "entities": 0}
    for row in rows:
        old, new = row["old"], row["new"]
        if not new:
            connection.execute(
                f"DELETE FROM asset_tag WHERE tag=? AND source NOT IN ({holes})", (old, *raw))
            counts["dropped"] += connection.execute("SELECT changes()").fetchone()[0]
            counts["entities"] += _drop_entity(connection, old)
            continue
        connection.execute(
            f"UPDATE OR IGNORE asset_tag SET tag=? WHERE tag=? AND source NOT IN ({holes})",
            (new, old, *raw))
        counts["tags"] += connection.execute("SELECT changes()").fetchone()[0]
        # 改不动的就是两个名字都有的那一侧，旧的这条已经没有信息了。
        connection.execute(
            f"DELETE FROM asset_tag WHERE tag=? AND source NOT IN ({holes})", (old, *raw))
        counts["dropped"] += connection.execute("SELECT changes()").fetchone()[0]
        counts["entities"] += _rename_entity(connection, old, new, stamp)
    return counts


#: 挂在一条标签实体下面的行。连接默认 `foreign_keys=OFF`，子表不删就成孤儿。
_ENTITY_CHILDREN = ("asset_entity", "entity_alias", "entity_external_ref", "entity_link",
                    "entity_search_term")


def _drop_entity(connection: Connection, name: str) -> int:
    """撤掉的标签连实体一起删；有人关注着的不动，留给人看。"""
    entity_id = _entity_id(connection, name)
    if entity_id is None:
        return 0
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("follow_source", "feed_source", "feed_discovery_entity"):
        if table in tables and connection.execute(
                f"SELECT 1 FROM {table} WHERE entity_id=?", (entity_id,)).fetchone():
            return 0
    for table in _ENTITY_CHILDREN:
        connection.execute(f"DELETE FROM {table} WHERE entity_id=?", (entity_id,))
    connection.execute("DELETE FROM entity WHERE id=?", (entity_id,))
    return 1


def _rename_entity(connection: Connection, old: str, new: str, stamp: str) -> int:
    """标签实体跟着改：目标已存在就并过去，否则原地换名。"""
    source_id = _entity_id(connection, old)
    if source_id is None:
        return 0
    target_id = _entity_id(connection, new)
    if target_id is None:
        connection.execute(
            "UPDATE entity SET canonical_name=?,normalized_name=?,updated_at=? WHERE id=?",
            (new, normalize_entity_name(new), stamp, source_id))
        return 1
    merge_entity(connection, target_id=target_id, source_id=source_id,
                 source_name=old, alias_source="tag-rename", now=stamp)
    return 1
