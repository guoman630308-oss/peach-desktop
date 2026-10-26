"""用户对来源 genre 下的决定：收录成哪个中文标签，或者判它不是内容。

`genre_taxonomy` 是随代码发版的静态策略，不碰数据库；这里是它的可变那一半，
只做 `genre_decision`（迁移 0026）的读写，映射逻辑仍然全在那边。分成两个文件是为了
让策略层保持可以单独测、不需要数据库。
"""
from __future__ import annotations

from .genre_taxonomy import normalise_genre


def load_genre_decisions(connection) -> dict[str, str | None]:
    """规范化写法 -> 中文标签；值为 `None` 表示用户判它不是内容标签。"""
    return {row[0]: row[1] for row in
            connection.execute("SELECT source_genre,peach_tag FROM genre_decision")}


def record_genre_decision(connection, raw_genre: str, peach_tag: str | None, now: str) -> str:
    """记下一条决定并返回规范化后的键。同一个词再收录一次是覆盖，不是新增一行。"""
    key = normalise_genre(raw_genre)
    if not key:
        raise ValueError("要收录的 genre 是空的")
    tag = (peach_tag or "").strip() or None
    connection.execute(
        "INSERT INTO genre_decision(source_genre,raw_genre,peach_tag,decided_at) VALUES(?,?,?,?) "
        "ON CONFLICT(source_genre) DO UPDATE SET raw_genre=excluded.raw_genre,"
        "peach_tag=excluded.peach_tag,decided_at=excluded.decided_at",
        (key, " ".join(str(raw_genre).split()), tag, now),
    )
    return key
