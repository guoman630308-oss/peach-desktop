"""实体链接的「已失效」标记：确证没了的链接是删掉，还是留成一枚不可点的记录。

已隐退女优的事务所页没了是常态：退所后页面下架，公司注销后域名被停放。删掉的话资料页
那一排外链就空了，而「她当年属于哪家」本身仍是有用的信息。所以已隐退的人，确证没了的
链接留在账本里，`metadata_json.gone` 记下判定时间和依据；资料页照旧列出这一枚，只是
不可点、不取站点图标，悬停写隐退年份。还在活动的人照旧删除：她的页面多半只是搬了家，
留一枚死标记只会挡住找回新地址。

隐退的判据是 `performer_profile.active_until` 有值：minnano-av 的出演期间写了结束年份
（`2019年〜2021年`），还在活动的写成 `2017年 -`，结束年份为空。
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone

GONE_KEY = "gone"


def live_clause(column: str = "l.metadata_json") -> str:
    """SQL 条件：这条链接没有失效标记。`json_valid` 在前，坏 JSON 不会让整句查询报错。"""
    return f"NOT (json_valid({column}) AND json_type({column},'$.{GONE_KEY}') IS NOT NULL)"


def gone_mark(metadata: dict | str | None) -> dict | None:
    """失效标记本身；没有标记返回 None。"""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except ValueError:
            return None
    mark = (metadata or {}).get(GONE_KEY) if isinstance(metadata, dict) else None
    return mark if isinstance(mark, dict) else None


def retired_year(connection: sqlite3.Connection, entity_id: int) -> int | None:
    """女优资料里出演期间的结束年份；还在活动、没有资料或不是女优都返回 None。"""
    try:
        row = connection.execute(
            "SELECT active_until FROM performer_profile WHERE entity_id=?", (entity_id,)).fetchone()
    except sqlite3.OperationalError:   # 资料表所在的迁移还没应用
        return None
    try:
        return int(row[0]) if row and row[0] is not None else None
    except (TypeError, ValueError):
        return None


def settle_gone(connection: sqlite3.Connection, items: Iterable[dict],
                now: str | None = None) -> tuple[int, int]:
    """处置确证没了的链接，返回（删掉几条, 标记几条）。`items` 每项要有 `id` 与 `note`。

    调用方负责事务：这里只发语句，要么和调用方的其余写入一起提交，要么一起回滚。
    """
    now = now or datetime.now(timezone.utc).isoformat()
    removed = marked = 0
    for item in items:
        row = connection.execute(
            "SELECT entity_id, metadata_json FROM entity_link WHERE id=?", (item["id"],)).fetchone()
        if row is None:
            continue
        entity_id, metadata_json = row[0], row[1]
        if retired_year(connection, entity_id) is None:
            removed += connection.execute(
                "DELETE FROM entity_link WHERE id=?", (item["id"],)).rowcount
            continue
        try:
            metadata = json.loads(metadata_json or "{}")
        except ValueError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata[GONE_KEY] = {"at": now, "note": str(item.get("note") or "")}
        marked += connection.execute(
            "UPDATE entity_link SET metadata_json=?, updated_at=? WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False), now, item["id"])).rowcount
    return removed, marked
