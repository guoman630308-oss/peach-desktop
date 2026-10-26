"""数据管理页的「孤儿记录」：文件已不在盘上、带着个人记录的行（ADR-0087 决策三）。

列表列出全部 `disposal='vanished'` 的行、它们在六张表里各有几条记录与行上的评分和次数，
以及在库里可能是它新版本的文件：番号对得上的，或无番号时文件名主干相同的。人从里面挑一个接过去，搬运与
登记时自动接回走同一段 `record_rehome.move`，记一个 `user:reattach` 批次，可整批撤回。

彻底删除不在这里：页面走 `/api/batch` 的 `delete`，那一支对已消失的行只删账本行
（`purge_assets(missing_only=True)`），文件又回到盘上的那几条留着不删。
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PureWindowsPath

from .personal_records import VANISHED, record_counts
from .record_rehome import MANUAL_SOURCE, code_key, move
from .web_state import WebContract

#: 每一条最多给几个候选文件。
CANDIDATE_LIMIT = 5

_COLUMNS = "id,location,name,code,duration,size,snapshot_path,feedback_at"


def _stem(name: str | None) -> str:
    return PureWindowsPath(name or "").stem.casefold()


def _candidates(connection, orphans: list[dict]) -> dict[int, list[dict]]:
    """在库视频里番号对得上、或无番号时文件名主干相同的那几个，按时长与旧行接近排。"""
    if not orphans:
        return {}
    keys ={row["id"]: code_key(row) for row in orphans}
    stems = {row["id"]: _stem(row["name"]) for row in orphans if not keys[row["id"]]}
    by_key: dict[str, list[dict]] = defaultdict(list)
    by_stem: dict[str, list[dict]] = defaultdict(list)
    for row in connection.execute(
            "SELECT id,location,name,code,duration,size FROM asset "
            "WHERE disposal IS NULL AND medium='video'"):
        item = dict(row)
        if key := code_key(item):
            by_key[key].append(item)
        else:
            by_stem[_stem(item["name"])].append(item)
    found = {}
    for orphan in orphans:
        pool = by_key.get(keys[orphan["id"]], []) if keys[orphan["id"]] else by_stem.get(stems[orphan["id"]], [])
        duration = float(orphan["duration"] or 0)
        pool = sorted(pool, key=lambda item: (abs(float(item["duration"] or 0) - duration), item["id"]))
        found[orphan["id"]] = [{key: item[key] for key in ("id", "location", "name", "duration", "size")}
                               for item in pool[:CANDIDATE_LIMIT]]
    return found


def q_orphan_records(contract: WebContract, args=None):
    """全部已消失的行，新标的在前；每条带六张表的记录数与候选文件。"""
    with contract.read_connection() as connection:
        orphans = [dict(row) for row in connection.execute(
            f"SELECT {_COLUMNS} FROM asset WHERE disposal=? ORDER BY feedback_at DESC,id DESC", (VANISHED,))]
        counts = record_counts(connection, [row["id"] for row in orphans])
        candidates = _candidates(connection, orphans)
    return {"total": len(orphans), "items": [
        {"id": row["id"], "name": row["name"], "code": row["code"] or "", "location": row["location"],
         "duration": row["duration"], "size": row["size"], "vanished_at": row["feedback_at"],
         "has_thumb": bool(row["snapshot_path"]), "records": counts.get(row["id"], {}),
         "candidates": candidates.get(row["id"], [])}
        for row in orphans]}


def w_orphan_attach(contract: WebContract, body):
    """把一条已消失行的记录接到指定的在库文件上，删掉旧行；返回批次号。"""
    orphan, target = int(body["id"]), int(body["target"])
    contract.cache_bust()
    with contract.write_transaction() as connection:
        batch = move(connection, orphan, target, source=MANUAL_SOURCE, rule="manual")
    contract.cache_bust()
    return {"ok": True, "id": orphan, "target": target, "batch": f"{MANUAL_SOURCE}@{batch}"}
