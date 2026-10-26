"""把「已消失」行的个人记录接到新文件上（ADR-0087 决策二）。

文件在盘上消失、行上带着个人记录时，资源同步只标 `disposal='vanished'`。新文件登记之后
按两条判据找它的旧行：

1. 番号：`normalise_code_key` 之后对上的已消失行只有一条，新文件也只有一个就搬；新文件有
   几个时取时长与旧行最接近的那个，时长缺或并列就不搬，留给人在孤儿记录列表里选。
2. 无番号的创作者视频：旧行的创作者是新文件路径上的一层目录、文件名主干相同、时长差在
   `DURATION_TOLERANCE` 秒之内；候选多个时同样取最接近的。

同一 `(location, path)` 的文件回来了不归这里管：扫描的 upsert 直接清掉那一行的 `disposal`。

新版本先入库、旧文件后消失时反过来找：资源同步与「同步删除」标完已消失，`reattach_vanished`
拿刚标的这几行按同一套判据在全部在库视频里找，唯一命中就当场接回，记同一种批次；候选多个
分不出的照旧留成孤儿。

搬运把五张表的行改指新行，新行已有同一键的记录时以新行为准；`asset_quality_goal` 搬到新行
后置 `wanted=0` 并记 `replaced_at` 关闭，不当作新行的待找目标。旧行自己的四列个人记录并进
新行：评分新行为空才取旧的，高潮与播放次数相加，最近播放取较晚的。搬完删旧行。每次搬运在
`record_rehome` 记一行，批次号 `<source>@<id>`（ADR-0052），存旧行整行、它在各引用表里的
全部行、改指过的键与新行四列在接回前的值，`revert` 按快照重建旧行、把搬走的改回去、新行四列
还原成接回前的值。

调用方负责事务：这里的函数都只在给定连接上写，不提交。
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from contextlib import closing
from datetime import datetime
from pathlib import PureWindowsPath

from .catalog_rules import normalise_code_key, release_code_from_filename
from .personal_records import ASSET_RECORD_COLUMNS, ASSET_REFERENCE_TABLES, VANISHED

#: 登记时自动接回记的来源。
AUTO_SOURCE = "auto:vanished-reattach"
#: 孤儿记录列表里由人接到某个文件记的来源。
MANUAL_SOURCE = "user:reattach"

#: 搬运时改指新行的五张表，各自除 `asset_id` 之外认同一条记录的键。
MOVED_TABLES: dict[str, tuple[str, ...]] = {
    "asset_preference": ("profile_id",),
    "watch_queue": ("profile_id",),
    "playlist_item": ("playlist_id",),
    "activity_event": ("id",),
    "asset_tag_preference": ("profile_id", "normalized_tag"),
}
#: 寻找更好版本的目标：搬到新行后关闭。
GOAL_TABLE, GOAL_KEYS = "asset_quality_goal", ("profile_id",)
#: `moved_json` 里记新行四列个人记录原值的那一项。
ROW_RECORDS = "asset_row"

#: 无番号的创作者视频：时长差在这几秒之内算同一部。
DURATION_TOLERANCE = 2.0

_COLUMNS = "id,location,path,name,code,creator,duration"


def _rows(connection, sql: str, params: Sequence = ()) -> list[dict]:
    """查询结果按列名转成字典，不依赖调用方连接上的 `row_factory`。"""
    cursor = connection.execute(sql, tuple(params))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def code_key(row: dict) -> str:
    """一行的番号键：有 `code` 用它，新登记的行还没有，就从文件名里解析。"""
    return normalise_code_key(row.get("code") or release_code_from_filename(row.get("name")))


def _stem(name: str | None) -> str:
    return PureWindowsPath(name or "").stem.casefold()


def _creator(connection, row: dict) -> str:
    """旧行的创作者：先看 `asset.creator`，没有再看挂着的创作者实体。"""
    name = str(row.get("creator") or "").strip()
    if name:
        return name
    found = connection.execute(
        "SELECT e.canonical_name FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
        "WHERE ae.asset_id=? AND e.kind='creator' ORDER BY e.id LIMIT 1", (row["id"],)).fetchone()
    return str(found[0]) if found else ""


def _under(path: str, creator: str) -> bool:
    """新文件路径上有一层目录就叫这个创作者名。"""
    folded = creator.casefold()
    return any(part.casefold() == folded for part in PureWindowsPath(path).parent.parts)


def _closest(old: dict, candidates: list[dict]) -> dict | None:
    """只有一个候选就是它；多个时取时长与旧行最接近的，时长缺或并列返回 None。"""
    if len(candidates) == 1:
        return candidates[0]
    duration = old.get("duration")
    if not candidates or not duration or any(not item.get("duration") for item in candidates):
        return None
    gaps = sorted((abs(float(item["duration"]) - float(duration)), item["id"]) for item in candidates)
    if gaps[0][0] == gaps[1][0]:
        return None
    return next(item for item in candidates if item["id"] == gaps[0][1])


def _creator_candidates(connection, old: dict, by_stem: dict[str, list[dict]]) -> list[dict]:
    """`by_stem` 是无番号的在库行按文件名主干分的组：在整库里找时不必逐行比一遍。"""
    same_stem = by_stem.get(_stem(old["name"]), [])
    creator, duration = (_creator(connection, old), old.get("duration")) if same_stem else ("", None)
    if not creator or not duration:
        return []
    return [item for item in same_stem
            if _under(item["path"], creator) and item.get("duration")
            and abs(float(item["duration"]) - float(duration)) <= DURATION_TOLERANCE]


def _vanished_rows(connection) -> list[dict]:
    return _rows(connection, f"SELECT {_COLUMNS} FROM asset WHERE disposal=? AND medium='video'", (VANISHED,))


def plan(connection, new_ids: Iterable[int]) -> list[tuple[dict, dict, str]]:
    """这几行新文件各自该接哪一条已消失行：`(旧行, 新行, 判据)`，一对一。"""
    wanted = sorted({int(item) for item in new_ids})
    if not wanted:
        return []
    vanished = _vanished_rows(connection)
    if not vanished:
        return []
    fresh = []
    for offset in range(0, len(wanted), 400):
        batch = wanted[offset:offset + 400]
        fresh += _rows(connection, f"SELECT {_COLUMNS} FROM asset WHERE id IN ({','.join('?' * len(batch))}) "
                       "AND disposal IS NULL AND medium='video'", batch)
    return _pairs(connection, vanished, fresh)


def plan_vanished(connection, old_ids: Iterable[int]) -> list[tuple[dict, dict, str]]:
    """刚标已消失的这几行各自该接到哪一条在库视频：新版本先入库、旧文件后消失的那种。

    全部已消失行都参与判定：同番号还有别的孤儿、两条旧行争同一个文件，都照旧留给人。
    """
    wanted = {int(item) for item in old_ids}
    vanished = _vanished_rows(connection) if wanted else []
    if not any(row["id"] in wanted for row in vanished):
        return []
    fresh = _rows(connection, f"SELECT {_COLUMNS} FROM asset WHERE disposal IS NULL AND medium='video'")
    return [pair for pair in _pairs(connection, vanished, fresh) if pair[0]["id"] in wanted]


def _pairs(connection, vanished: list[dict], fresh: list[dict]) -> list[tuple[dict, dict, str]]:
    """已消失行与在库行按两条判据配对，只留一对一的。"""
    olds_by_code: dict[str, list[dict]] = defaultdict(list)
    news_by_code: dict[str, list[dict]] = defaultdict(list)
    news_by_stem: dict[str, list[dict]] = defaultdict(list)
    for row in vanished:
        if key := code_key(row):
            olds_by_code[key].append(row)
    for row in fresh:
        if key := code_key(row):
            news_by_code[key].append(row)
        else:
            news_by_stem[_stem(row["name"])].append(row)
    pairs = []
    for key, olds in olds_by_code.items():
        # 同番号消失了几条（分卷、两个版本都没了）就分不出哪条记录归哪个文件，留给人。
        chosen = _closest(olds[0], news_by_code.get(key, [])) if len(olds) == 1 else None
        if chosen:
            pairs.append((olds[0], chosen, "code"))
    for old in vanished:
        if not code_key(old):
            chosen = _closest(old, _creator_candidates(connection, old, news_by_stem))
            if chosen:
                pairs.append((old, chosen, "creator-stem-duration"))
    # 两条旧行争同一个新文件：分不出，都留给人。
    claimed = Counter(new["id"] for _old, new, _rule in pairs)
    return [pair for pair in pairs if claimed[pair[1]["id"]] == 1]


def _move_rows(connection, table: str, keys: tuple[str, ...], old_id: int, new_id: int) -> list[dict]:
    """把旧行在这张表里的记录改指新行，新行已有同键记录的那条不搬；返回搬了的键。"""
    moved = []
    match = " AND ".join(f"{key}=?" for key in keys)
    for row in _rows(connection, f"SELECT {','.join(keys)} FROM {table} WHERE asset_id=?", (old_id,)):
        values = [row[key] for key in keys]
        if connection.execute(f"SELECT 1 FROM {table} WHERE asset_id=? AND {match}",
                              (new_id, *values)).fetchone():
            continue
        connection.execute(f"UPDATE {table} SET asset_id=? WHERE asset_id=? AND {match}",
                           (new_id, old_id, *values))
        moved.append(row)
    return moved


def _epoch(value) -> float:
    """`last_played` 多数是 epoch 秒，早年导入的行可能是 ISO 时间；认不出的当最早。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return float("-inf")


def _fold_row_records(connection, old_id: int, new_id: int) -> dict:
    """把旧行上四列个人记录并进新行，返回新行这四列在接回前的值，供撤回还原。

    评分新行为空才取旧行的；高潮与播放次数相加；最近播放取两者中较晚的。
    """
    columns = ",".join(ASSET_RECORD_COLUMNS)
    old = _rows(connection, f"SELECT {columns} FROM asset WHERE id=?", (old_id,))[0]
    new = _rows(connection, f"SELECT {columns} FROM asset WHERE id=?", (new_id,))[0]
    merged = {"rating": new["rating"] if new["rating"] is not None else old["rating"]}
    for column in ("o_count", "play_count"):
        both = (old[column], new[column])
        merged[column] = None if both == (None, None) else sum(int(value or 0) for value in both)
    played = [value for value in (old["last_played"], new["last_played"]) if value is not None]
    merged["last_played"] = max(played, key=_epoch) if played else None
    connection.execute(f"UPDATE asset SET {','.join(f'{column}=?' for column in ASSET_RECORD_COLUMNS)} "
                       "WHERE id=?", (*[merged[column] for column in ASSET_RECORD_COLUMNS], new_id))
    return new


def _snapshot(connection, old_id: int) -> dict:
    return {
        "asset": _rows(connection, "SELECT * FROM asset WHERE id=?", (old_id,))[0],
        "tables": {table: _rows(connection, f"SELECT * FROM {table} WHERE asset_id=?", (old_id,))
                   for table in ASSET_REFERENCE_TABLES},
        "playlists": _rows(connection, "SELECT id,current_asset_id,source_seed_asset_id FROM playlist "
                           "WHERE current_asset_id=? OR source_seed_asset_id=?", (old_id, old_id)),
    }


def move(connection, old_id: int, new_id: int, *, source: str, rule: str,
         now: str | None = None) -> int:
    """把一条已消失行的个人记录搬到新行，删掉旧行；返回这一批的 `record_rehome.id`。"""
    stamp = now or time.strftime("%Y-%m-%d %H:%M:%S")
    old = connection.execute("SELECT disposal FROM asset WHERE id=?", (old_id,)).fetchone()
    target = connection.execute("SELECT disposal FROM asset WHERE id=?", (new_id,)).fetchone()
    if old is None or old[0] != VANISHED:
        raise ValueError("只能接回已消失的行")
    if target is None or target[0] is not None or old_id == new_id:
        raise ValueError("只能接到在库的另一行")
    snapshot = _snapshot(connection, old_id)
    moved: dict = {table: _move_rows(connection, table, keys, old_id, new_id)
                   for table, keys in MOVED_TABLES.items()}
    moved[ROW_RECORDS] = _fold_row_records(connection, old_id, new_id)
    moved[GOAL_TABLE] = _move_rows(connection, GOAL_TABLE, GOAL_KEYS, old_id, new_id)
    connection.executemany(
        f"UPDATE {GOAL_TABLE} SET wanted=0,replaced_at=? WHERE asset_id=? AND profile_id=?",
        [(stamp, new_id, row["profile_id"]) for row in moved[GOAL_TABLE]])
    for column in ("current_asset_id", "source_seed_asset_id"):
        connection.execute(f"UPDATE playlist SET {column}=? WHERE {column}=?", (new_id, old_id))
    for table in ASSET_REFERENCE_TABLES:
        connection.execute(f"DELETE FROM {table} WHERE asset_id=?", (old_id,))
    connection.execute("DELETE FROM asset WHERE id=?", (old_id,))
    cursor = connection.execute(
        "INSERT INTO record_rehome(source,rule,old_asset_id,new_asset_id,snapshot_json,moved_json,"
        "created_at) VALUES(?,?,?,?,?,?,?)",
        (source, rule, old_id, new_id, json.dumps(snapshot, ensure_ascii=False),
         json.dumps(moved, ensure_ascii=False), stamp))
    return int(cursor.lastrowid)


def reattach(connection, new_ids: Iterable[int], *, now: str | None = None) -> list[int]:
    """登记完一批新文件后接回记录；返回这一轮记下的批次 id。调用方提交。"""
    return [move(connection, old["id"], new["id"], source=AUTO_SOURCE, rule=rule, now=now)
            for old, new, rule in plan(connection, new_ids)]


def reattach_vanished(connection, old_ids: Iterable[int], *, now: str | None = None) -> list[int]:
    """刚标已消失的行唯一命中在库视频时当场接回；返回接走了的旧行 id。调用方提交。"""
    pairs = plan_vanished(connection, old_ids)
    for old, new, rule in pairs:
        move(connection, old["id"], new["id"], source=AUTO_SOURCE, rule=rule, now=now)
    return [old["id"] for old, _new, _rule in pairs]


def reattach_in(db_path, new_ids: Sequence[int]) -> int:
    """探完时长之后再判一遍：登记那一刻新文件还没有时长，多个候选与无番号的那条判据要等它。"""
    if not new_ids:
        return 0
    with closing(sqlite3.connect(db_path, timeout=30)) as connection:
        batches = reattach(connection, new_ids)
        connection.commit()
    return len(batches)


def _batch_clause(source: str, batch: str) -> tuple[str, tuple]:
    """批次号是 `<source>@<id>`；前半段要与 `--source` 一致，别的来源的批次号一条都不认。"""
    if batch:
        head, _, tail = batch.rpartition("@")
        return "source=? AND source=? AND id=?", (source, head, int(tail) if tail.isdigit() else -1)
    return "source=?", (source,)


def planned_revert(connection, source: str, batch: str = "") -> list[dict]:
    """这个来源（或其中一批）还没撤回的搬运，新的在前。"""
    clause, values = _batch_clause(source, batch)
    return [{"id": row["id"], "batch": f"{row['source']}@{row['id']}", "rule": row["rule"],
             "old_asset_id": row["old_asset_id"], "new_asset_id": row["new_asset_id"],
             "name": json.loads(row["snapshot_json"])["asset"].get("name") or ""}
            for row in _rows(connection, "SELECT id,source,rule,old_asset_id,new_asset_id,snapshot_json "
                             f"FROM record_rehome WHERE reverted_at IS NULL AND {clause} ORDER BY id DESC",
                             values)]


def _insert(connection, table: str, row: dict) -> None:
    present = {item[1] for item in connection.execute(f"PRAGMA table_info({table})")}
    columns = [column for column in row if column in present]
    connection.execute(
        f"INSERT OR IGNORE INTO {table}({','.join(columns)}) VALUES({','.join('?' * len(columns))})",
        [row[column] for column in columns])


def revert_one(connection, rehome_id: int, *, now: str | None = None) -> None:
    """按快照重建旧行，把搬到新行的记录改回旧行。旧行的 id 或路径已被占用时拒绝。"""
    row = _rows(connection, "SELECT * FROM record_rehome WHERE id=? AND reverted_at IS NULL", (rehome_id,))
    if not row:
        raise ValueError(f"没有可撤回的批次 {rehome_id}")
    snapshot, moved, new_id = (json.loads(row[0]["snapshot_json"]), json.loads(row[0]["moved_json"]),
                               int(row[0]["new_asset_id"]))
    old = snapshot["asset"]
    if connection.execute("SELECT 1 FROM asset WHERE id=? OR (location=? AND path=?)",
                          (old["id"], old["location"], old["path"])).fetchone():
        raise ValueError(f"旧行 {old['id']} 的 id 或路径已被占用，不能撤回")
    _insert(connection, "asset", old)
    for table, keys in (*MOVED_TABLES.items(), (GOAL_TABLE, GOAL_KEYS)):
        match = " AND ".join(f"{key}=?" for key in keys)
        connection.executemany(f"DELETE FROM {table} WHERE asset_id=? AND {match}",
                               [(new_id, *[item[key] for key in keys]) for item in moved.get(table, [])])
    for table, rows in snapshot["tables"].items():
        for item in rows:
            _insert(connection, table, item)
    for playlist in snapshot["playlists"]:
        for column in ("current_asset_id", "source_seed_asset_id"):
            if playlist[column] == old["id"]:
                connection.execute(f"UPDATE playlist SET {column}=? WHERE id=? AND {column}=?",
                                   (old["id"], playlist["id"], new_id))
    if before := moved.get(ROW_RECORDS):
        connection.execute(
            f"UPDATE asset SET {','.join(f'{column}=?' for column in ASSET_RECORD_COLUMNS)} WHERE id=?",
            (*[before[column] for column in ASSET_RECORD_COLUMNS], new_id))
    connection.execute("UPDATE record_rehome SET reverted_at=? WHERE id=?",
                       (now or time.strftime("%Y-%m-%d %H:%M:%S"), rehome_id))


def revert(connection, source: str, batch: str = "") -> int:
    """撤回这个来源（或其中一批）的搬运，新的先撤；返回撤了几批。调用方负责事务与备份。"""
    planned = planned_revert(connection, source, batch)
    for item in planned:
        revert_one(connection, item["id"])
    return len(planned)
