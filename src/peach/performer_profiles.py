"""女优资料表 `performer_profile` 的读写（ADR-0067）。

一位女优一行：出生日期、身高三围、血型、出身地、出道与活动年份这些按列存，站上标签与
资料表原文各存一份 JSON。列与 `minnano_av.PROFILE_COLUMNS` 一一对应，解析端交回什么这里
就写什么，读不出的列是 NULL。

和 `metadata_performer_profiles` 不是一回事：那一个在处理任务结束时补新导入人物的别名与
官方头像，不碰这张表；这张表只由补女优资料后继（`performer_profile_followup`）写。

只填自动来源的行：`source` 不以 `auto:` 开头的一行是人写的，自动写入者不覆盖。撤回就是
删除这一行（`scripts/revert_auto_landing.py`），因为自动写下的就是这一行的全部。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .minnano_av import PROFILE_COLUMNS

TABLE = "performer_profile"
AUTO_PREFIX = "auto:"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_profile(connection: sqlite3.Connection, entity_id: int, profile: dict, *,
                  source: str, source_url: str, fetched_at: str | None = None) -> bool:
    """写入或整行替换这位的资料；这一行是人写的就不动，返回 False。调用方负责事务。"""
    held = connection.execute(f"SELECT source FROM {TABLE} WHERE entity_id=?",
                              (int(entity_id),)).fetchone()
    if held is not None and not str(held[0]).startswith(AUTO_PREFIX):
        return False
    values = [profile.get(column) for column in PROFILE_COLUMNS]
    columns = ("entity_id", *PROFILE_COLUMNS, "tags_json", "raw_json", "source", "source_url",
               "fetched_at")
    row = (int(entity_id), *values,
           json.dumps(list(profile.get("tags") or []), ensure_ascii=False),
           json.dumps(dict(profile.get("raw") or {}), ensure_ascii=False),
           source, source_url, fetched_at or _stamp())
    updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
    connection.execute(
        f"INSERT INTO {TABLE}({','.join(columns)}) VALUES({','.join('?' * len(columns))})"
        f" ON CONFLICT(entity_id) DO UPDATE SET {updates}", row)
    return True


def read_profile(connection: sqlite3.Connection, entity_id: int) -> dict | None:
    """这位的资料；没有返回 None。`tags` 与 `raw` 已解成列表与字典。"""
    cursor = connection.execute(f"SELECT * FROM {TABLE} WHERE entity_id=?", (int(entity_id),))
    row = cursor.fetchone()
    if row is None:
        return None
    found = dict(zip((item[0] for item in cursor.description), row))
    found["tags"] = json.loads(found.pop("tags_json") or "[]")
    found["raw"] = json.loads(found.pop("raw_json") or "{}")
    return found


def fetched_at(connection: sqlite3.Connection, entity_id: int) -> datetime | None:
    """这位的资料是什么时候取回的；没有这一行或时间读不出都返回 None。"""
    row = connection.execute(f"SELECT fetched_at FROM {TABLE} WHERE entity_id=?",
                             (int(entity_id),)).fetchone()
    if row is None:
        return None
    try:
        stamp = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
