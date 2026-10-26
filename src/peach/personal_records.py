"""个人记录：用户自己的行为记在哪里，文件消失时据此决定删行还是标「已消失」（ADR-0087）。

个人记录在两处：六张表，对 `asset` 一律 `ON DELETE CASCADE`；`asset` 行自己的四列
`rating`、`o_count`、`play_count`、`last_played`。盘上文件消失多半是换了
版本或整理了目录，作品还在馆藏里，所以带着这些记录的行不删，标 `disposal='vanished'`：
列表、搜索、统计与推荐都当它不在库，记录留着，等新文件登记时接回，或在孤儿记录列表里由人
接到某个文件、彻底删除。

`disposal` 只有三个值：`NULL` 在库，`trash` 用户丢进回收站，`vanished` 文件已不在盘上但
带着个人记录。判「在库」一律写 `disposal IS NULL`。
"""
from __future__ import annotations

from collections.abc import Iterable

#: 构成个人记录的六张表。任一张有指向这一行的记录，或行上四列记过，这一行就算「带个人记录」。
PERSONAL_RECORD_TABLES = (
    "asset_preference", "watch_queue", "playlist_item",
    "asset_quality_goal", "activity_event", "asset_tag_preference",
)

#: `asset` 行自己的个人记录列：评分、高潮次数、播放次数、最近播放时间。
ASSET_RECORD_COLUMNS = ("rating", "o_count", "play_count", "last_played")

#: 这一行自己带着个人记录：打过分、记过高潮、播放过，任一成立即算。
ASSET_RECORD_PREDICATE = ("(rating IS NOT NULL OR COALESCE(o_count,0)>0 "
                          "OR COALESCE(play_count,0)>0 OR last_played IS NOT NULL)")

#: 一行 `asset` 被物理删除时要一并清掉的引用表，删除与搬运记录的边界只写在这一处。
#: `asset_search` 不在其中：0004 的 `asset_search_asset_delete` 触发器已经负责 FTS 行，
#: 这里再删一遍只会重复，还会诱使测试库伪造一张同名普通表，把 has_fts() 骗成 True。
ASSET_REFERENCE_TABLES = (
    "asset_tag", "media_binding", "activity_event", "asset_entity",
    "watch_queue", "asset_preference", "asset_tag_preference", "asset_quality_goal",
    "playlist_item", "asset_subtitle",
)

#: 文件已不在盘上、带着个人记录的那一档。
VANISHED = "vanished"

#: 一次 `IN (...)` 最多带多少个参数，留在 SQLite 默认上限之内。
_CHUNK = 400


def _chunks(ids: list[int]) -> Iterable[list[int]]:
    for offset in range(0, len(ids), _CHUNK):
        yield ids[offset:offset + _CHUNK]


def record_holders(connection, asset_ids: Iterable[int]) -> set[int]:
    """这些行里带个人记录的那几个 id：六张表里有记录，或行上四列记过。"""
    wanted = sorted({int(asset_id) for asset_id in asset_ids})
    holders: set[int] = set()
    for batch in _chunks(wanted):
        marks = ",".join("?" * len(batch))
        holders.update(int(row[0]) for row in connection.execute(
            f"SELECT id FROM asset WHERE id IN ({marks}) AND {ASSET_RECORD_PREDICATE}", batch))
        for table in PERSONAL_RECORD_TABLES:
            holders.update(int(row[0]) for row in connection.execute(
                f"SELECT DISTINCT asset_id FROM {table} WHERE asset_id IN ({marks})", batch))
    return holders


def record_counts(connection, asset_ids: Iterable[int]) -> dict[int, dict[str, int]]:
    """每一行在六张表里各有几条记录，加上行上四列的读数；没有记录的不出现在那一行的字典里。

    `rating` 是分值，`o_count` 与 `play_count` 是次数，`last_played` 只要记过就是 1。
    """
    wanted = sorted({int(asset_id) for asset_id in asset_ids})
    counts: dict[int, dict[str, int]] = {asset_id: {} for asset_id in wanted}
    for batch in _chunks(wanted):
        marks = ",".join("?" * len(batch))
        for asset_id, *values in connection.execute(
                f"SELECT id,{','.join(ASSET_RECORD_COLUMNS)} FROM asset WHERE id IN ({marks})", batch):
            for column, value in zip(ASSET_RECORD_COLUMNS, values):
                if column == "last_played":
                    value = 1 if value is not None else 0
                if value:
                    counts[int(asset_id)][column] = int(value)
        for table in PERSONAL_RECORD_TABLES:
            for asset_id, n in connection.execute(
                    f"SELECT asset_id,count(*) FROM {table} WHERE asset_id IN ({marks}) "
                    "GROUP BY asset_id", batch):
                counts[int(asset_id)][table] = int(n)
    return counts


def mark_vanished(connection, asset_ids: Iterable[int], stamp: float) -> list[int]:
    """把在库的这些行标「已消失」，回收站里的不动；返回真的改了的 id。

    `feedback_at` 记下标的时刻，和移入回收站同一列：孤儿记录列表按它排新旧。
    """
    wanted = sorted({int(asset_id) for asset_id in asset_ids})
    changed: list[int] = []
    for batch in _chunks(wanted):
        marks = ",".join("?" * len(batch))
        changed.extend(int(row[0]) for row in connection.execute(
            f"SELECT id FROM asset WHERE id IN ({marks}) AND disposal IS NULL", batch))
        connection.execute(
            f"UPDATE asset SET disposal=?,feedback_at=? WHERE id IN ({marks}) AND disposal IS NULL",
            [VANISHED, stamp, *batch])
    return changed
