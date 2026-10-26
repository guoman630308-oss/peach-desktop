"""「想要」清单：登记「这部我想要」，入库时自动对账（ADR-0090）。

关注一位女优只是持续知道她出了什么，「想要」说的是要这一部。能登记三种东西：Feed 发现的番号壳、
关注条目、用户直接输入的库外番号。番号一律按 `normalise_code_key` 归一，关注条目没有番号，按
`follow_item.id` 认。

**与屏蔽互斥。** Peach 的「屏蔽」是 Feed 卡上的「不想看」（`feed_discovery.ignored_at`）与关注条目的
「忽略」（`follow_item.status='ignored'`）。登记想要时清掉对应的屏蔽；屏蔽时撤掉对应的想要
（`drop_codes`、`drop_follow_items`）。两边的写入口各调这里一个函数，SQL 只写在这一处。

**入库自动对账。** 扫描登记新文件（`scan.scan_location` 与 `scan.ingest_path` 两条路径）和关注条目
保存进账本时调 `reconcile`：新行的番号键命中一条想要，就标「已入库」。番号键的判据是代码给的，
同样输入同样输出，按 ADR-0052 直接落库，记归属串 `AUTO_SOURCE` 与批次 `<source>@<登记时刻>`，
`revert` 按它们整批撤回成待找。

**查找状态。** 真正的查资源归第 41–43 条的下载模块；这里留状态与计数，给它一个记账入口
`record_search`。照 SakuraMedia 的口径：发售 90 天内的新片查不到也不计数，一直待找；老片
（发行日缺失也按老片算）查满 `STALE_ATTEMPT_LIMIT` 次无果标「暂时放弃」，`reset_search` 手动重置。
还没发售的不催：`phase` 按发行日现算出「未发售」，`pending_targets` 不交出它。

调用方负责事务：这里的函数只在给定连接上写，不提交。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .catalog_rules import normalise_code_key, release_code_from_text
from .record_rehome import code_key as asset_code_key

#: 入库自动对账写下的归属串。
AUTO_SOURCE = "auto:want-acquired"

#: 发售多少天之内算新片：查不到也不计数，持续待找。
FRESH_DAYS = 90
#: 老片查满几次无果就暂时放弃。
STALE_ATTEMPT_LIMIT = 3

#: 三种状态，含义见迁移 0042。
WANTED, GIVEN_UP, ACQUIRED = "wanted", "given_up", "acquired"
#: 清单上的四段：待找、未发售、暂时放弃、已入库。未发售是按发行日现算的，不是存下的状态。
SEARCHING, UNRELEASED = "searching", "unreleased"
PHASES = (SEARCHING, UNRELEASED, GIVEN_UP, ACQUIRED)

#: 一次查找的三种结果。`error` 是网络或索引器失败，不算这一部查过。
SEARCH_OUTCOMES = ("found", "none", "error")

#: 一次 `IN (...)` 最多带多少个参数，留在 SQLite 默认上限之内。
_CHUNK = 400

_COLUMNS = ("id,code,code_key,follow_item_id,origin,title,link,cover_url,release_date,studio,"
            "performers,scraped_at,scrape_error,state,search_count,last_search_at,"
            "last_search_outcome,last_search_note,given_up_at,acquired_asset_id,acquired_at,"
            "acquired_source,acquired_batch,created_at,updated_at")


def stamp(moment: datetime | None = None) -> str:
    """ISO-8601 UTC 文本，与 `feed_discovery`、`task_run` 同一种写法。"""
    return (moment or datetime.now(timezone.utc)).astimezone(
        timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _rows(connection, sql: str, params: Sequence = ()) -> list[dict]:
    """查询结果按列名转成字典，不依赖调用方连接上的 `row_factory`。"""
    cursor = connection.execute(sql, tuple(params))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _chunks(values: list) -> Iterable[list]:
    for offset in range(0, len(values), _CHUNK):
        yield values[offset:offset + _CHUNK]


def _register(connection) -> None:
    """扫描那条连接是裸 `sqlite3.connect`，没有挂 `normalise_code_key`；补上同一份实现。"""
    connection.create_function("normalise_code_key", 1, normalise_code_key, deterministic=True)


def parse_code(text: str | None) -> tuple[str, str]:
    """一段文字里的番号与它的键；认不出就抛，消息直接给人看。"""
    code = release_code_from_text(str(text or "").strip())
    key = normalise_code_key(code) if code else ""
    if not key:
        raise ValueError(f"认不出番号：{str(text or '').strip() or '（空）'}")
    return code, key


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def is_unreleased(release_date: str | None, today: date) -> bool:
    released = _day(release_date)
    return released is not None and released > today


def is_fresh(release_date: str | None, today: date) -> bool:
    """发售 `FRESH_DAYS` 天之内（含还没发售的）。发行日缺失按老片算：不知道多新就不当新片。"""
    released = _day(release_date)
    return released is not None and released > today - timedelta(days=FRESH_DAYS)


def phase(row: dict, today: date | None = None) -> str:
    """清单上的那一段：已入库、未发售、暂时放弃、待找。"""
    if row["state"] == ACQUIRED:
        return ACQUIRED
    if is_unreleased(row.get("release_date"), today or date.today()):
        return UNRELEASED
    if row["state"] == GIVEN_UP:
        return GIVEN_UP
    return SEARCHING


def get(connection, want_id: int) -> dict | None:
    found = _rows(connection, f"SELECT {_COLUMNS} FROM want_item WHERE id=?", (int(want_id),))
    return found[0] if found else None


def by_code_key(connection, key: str) -> dict | None:
    found = _rows(connection, f"SELECT {_COLUMNS} FROM want_item WHERE code_key=?", (key,))
    return found[0] if found else None


def wanted_keys(connection, keys: Iterable[str]) -> set[str]:
    """这些番号键里登记了想要、还没入库的那几个。Feed 卡上「想要」键的按下状态按它给。"""
    wanted = sorted({key for key in keys if key})
    found: set[str] = set()
    for batch in _chunks(wanted):
        found.update(str(row[0]) for row in connection.execute(
            f"SELECT code_key FROM want_item WHERE state<>'acquired' AND code_key IN "
            f"({','.join('?' * len(batch))})", batch))
    return found


def in_library(connection, key: str) -> bool:
    """这个番号键在库里有没有一部：与 `feeds.in_library` 同一口径，已消失的不算，回收站里的算。"""
    _register(connection)
    return connection.execute(
        "SELECT 1 FROM asset WHERE code IS NOT NULL AND COALESCE(disposal,'')<>'vanished'"
        " AND normalise_code_key(code)=? LIMIT 1", (key,)).fetchone() is not None


def _shell(connection, key: str) -> dict | None:
    """同一个番号的 Feed 壳：登记时把它取到的资料抄一份快照。"""
    _register(connection)
    found = _rows(connection, "SELECT id,code,title,link,cover_url,release_date,studio,performers,"
                  "scraped_at,scrape_error FROM feed_discovery WHERE normalise_code_key(code)=?"
                  " ORDER BY id LIMIT 1", (key,))
    return found[0] if found else None


def add_code(connection, text: str, *, origin: str = "code", now: str | None = None) -> dict:
    """按番号登记想要，返回那一行（带 `created`）。已登记的原样返回，幂等。

    已经在库里的拒绝：那部已经到手了，要换更好的版本走「寻找更好版本」。同一个番号的 Feed 壳
    要是被「不想看」收起了，这里取消那次忽略（想要与屏蔽互斥），并把壳上的资料抄进快照。
    """
    if origin not in ("code", "feed"):
        raise ValueError(f"unknown want origin: {origin}")
    code, key = parse_code(text)
    if in_library(connection, key):
        raise ValueError(f"{code} 已经在库里")
    moment = now or stamp()
    shell = _shell(connection, key)
    if shell is not None:
        connection.execute("UPDATE feed_discovery SET ignored_at=NULL WHERE id=?", (shell["id"],))
    existing = by_code_key(connection, key)
    if existing is not None:
        if existing["state"] == ACQUIRED:
            # 对上的那部又不在库里了（已消失或被删）：重新算待找。
            connection.execute(
                "UPDATE want_item SET state='wanted',acquired_asset_id=NULL,acquired_at=NULL,"
                "acquired_source=NULL,acquired_batch=NULL,updated_at=? WHERE id=?",
                (moment, existing["id"]))
        if shell is not None:
            _fill_snapshot(connection, existing["id"], shell, moment)
        return {**get(connection, existing["id"]), "created": False}
    snapshot = shell or {}
    want_id = int(connection.execute(
        "INSERT INTO want_item(code,code_key,origin,title,link,cover_url,release_date,studio,"
        "performers,scraped_at,scrape_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, key, origin, snapshot.get("title"), snapshot.get("link"), snapshot.get("cover_url"),
         snapshot.get("release_date"), snapshot.get("studio"), snapshot.get("performers"),
         snapshot.get("scraped_at"), snapshot.get("scrape_error"), moment, moment)).lastrowid)
    return {**get(connection, want_id), "created": True}


def _fill_snapshot(connection, want_id: int, values: dict, moment: str) -> None:
    """快照只补空格：用户登记之后壳上才取到的资料补进来，已经有的不覆盖。"""
    connection.execute(
        "UPDATE want_item SET title=COALESCE(title,?),link=COALESCE(link,?),"
        "cover_url=COALESCE(cover_url,?),release_date=COALESCE(release_date,?),"
        "studio=COALESCE(studio,?),performers=COALESCE(performers,?),updated_at=? WHERE id=?",
        (values.get("title"), values.get("link"), values.get("cover_url"),
         values.get("release_date"), values.get("studio"), values.get("performers"),
         moment, int(want_id)))


def add_feed(connection, discovery_id: int, *, now: str | None = None) -> dict:
    """Feed 卡上的「想要」：按壳的番号登记。"""
    found = connection.execute(
        "SELECT code FROM feed_discovery WHERE id=?", (int(discovery_id),)).fetchone()
    if found is None:
        raise ValueError("找不到这条新作")
    return add_code(connection, found[0], origin="feed", now=now)


def add_follow(connection, item_id: int, *, now: str | None = None) -> dict:
    """关注条目的「想要」。条目被忽略过的拉回已看（想要与屏蔽互斥）。"""
    found = _rows(connection, "SELECT id,title,url,published_at,status FROM follow_item WHERE id=?",
                  (int(item_id),))
    if not found:
        raise ValueError("找不到这条关注条目")
    item = found[0]
    if item["status"] == "ignored":
        connection.execute("UPDATE follow_item SET status='seen' WHERE id=?", (item["id"],))
    existing = _rows(connection, "SELECT id FROM want_item WHERE follow_item_id=?", (item["id"],))
    if existing:
        return {**get(connection, existing[0]["id"]), "created": False}
    moment = now or stamp()
    want_id = int(connection.execute(
        "INSERT INTO want_item(follow_item_id,origin,title,link,release_date,created_at,updated_at)"
        " VALUES(?,'follow',?,?,?,?,?)",
        (item["id"], item["title"], item["url"], (item["published_at"] or "")[:10] or None,
         moment, moment)).lastrowid)
    return {**get(connection, want_id), "created": True}


def remove(connection, want_ids: Iterable[int]) -> int:
    """用户在清单上移除。已入库的也能移除：那只是这份清单不再列它。"""
    ids = sorted({int(value) for value in want_ids})
    removed = 0
    for batch in _chunks(ids):
        removed += connection.execute(
            f"DELETE FROM want_item WHERE id IN ({','.join('?' * len(batch))})", batch).rowcount or 0
    return removed


def drop_codes(connection, codes: Iterable[str]) -> int:
    """屏蔽了这几个番号（Feed 卡上「不想看」）：撤掉还没入库的想要。"""
    keys = sorted({normalise_code_key(code) for code in codes if code} - {""})
    dropped = 0
    for batch in _chunks(keys):
        dropped += connection.execute(
            f"DELETE FROM want_item WHERE state<>'acquired' AND code_key IN "
            f"({','.join('?' * len(batch))})", batch).rowcount or 0
    return dropped


def drop_follow_items(connection, item_ids: Iterable[int]) -> int:
    """忽略了这几条关注条目：撤掉还没入库的想要。"""
    ids = sorted({int(value) for value in item_ids})
    dropped = 0
    for batch in _chunks(ids):
        dropped += connection.execute(
            f"DELETE FROM want_item WHERE state<>'acquired' AND follow_item_id IN "
            f"({','.join('?' * len(batch))})", batch).rowcount or 0
    return dropped


def reconcile(connection, asset_ids: Iterable[int], *, now: str | None = None) -> list[int]:
    """新登记的这几行命中了哪些想要，标「已入库」并返回它们的 id。

    番号键用 `record_rehome.code_key`：刚登记的行 `code` 还空着，从文件名解析。判在库与
    `feeds.in_library` 同一口径，只看 `disposal` 为空的行。关注条目按 `follow_item.asset_id`
    认：保存进账本的那条就是它。同一个批次号记这一轮的时刻，整批撤回按它认。
    """
    ids = sorted({int(value) for value in asset_ids})
    if not ids or connection.execute(
            "SELECT 1 FROM want_item WHERE state<>'acquired' LIMIT 1").fetchone() is None:
        return []
    moment = now or stamp()
    batch_id = f"{AUTO_SOURCE}@{moment}"
    by_key: dict[str, int] = {}
    by_follow: dict[int, int] = {}
    for batch in _chunks(ids):
        marks = ",".join("?" * len(batch))
        for row in _rows(connection, f"SELECT id,code,name FROM asset WHERE id IN ({marks})"
                         " AND disposal IS NULL", batch):
            key = asset_code_key(row)
            if key:
                by_key.setdefault(key, int(row["id"]))
        for item_id, asset_id in connection.execute(
                f"SELECT id,asset_id FROM follow_item WHERE asset_id IN ({marks})", batch):
            by_follow.setdefault(int(item_id), int(asset_id))
    hits: list[tuple[int, int]] = []
    for key_batch in _chunks(sorted(by_key)):
        hits += [(int(want_id), by_key[key]) for want_id, key in connection.execute(
            f"SELECT id,code_key FROM want_item WHERE state<>'acquired' AND code_key IN "
            f"({','.join('?' * len(key_batch))})", key_batch)]
    for item_batch in _chunks(sorted(by_follow)):
        hits += [(int(want_id), by_follow[int(item_id)]) for want_id, item_id in connection.execute(
            f"SELECT id,follow_item_id FROM want_item WHERE state<>'acquired' AND follow_item_id IN "
            f"({','.join('?' * len(item_batch))})", item_batch)]
    for want_id, asset_id in hits:
        connection.execute(
            "UPDATE want_item SET state='acquired',acquired_asset_id=?,acquired_at=?,"
            "acquired_source=?,acquired_batch=?,updated_at=? WHERE id=?",
            (asset_id, moment, AUTO_SOURCE, batch_id, moment, want_id))
    return sorted(want_id for want_id, _asset in hits)


def planned_revert(connection, source: str, batch: str = "") -> list[dict]:
    """这个来源（或其中一批）对账写下的「已入库」。"""
    clause, values = (" AND acquired_batch=?", (batch,)) if batch else ("", ())
    return _rows(connection, "SELECT id,code,title,acquired_asset_id,acquired_batch FROM want_item"
                 f" WHERE state='acquired' AND acquired_source=?{clause} ORDER BY id",
                 (source, *values))


def revert(connection, source: str, batch: str = "") -> int:
    """撤回对账：标「已入库」的那几条回到待找，查找计数原样留着。返回撤了几条。"""
    planned = planned_revert(connection, source, batch)
    moment = stamp()
    for item in planned:
        connection.execute(
            "UPDATE want_item SET state=CASE WHEN given_up_at IS NULL THEN 'wanted' ELSE 'given_up' END,"
            "acquired_asset_id=NULL,acquired_at=NULL,acquired_source=NULL,acquired_batch=NULL,"
            "updated_at=? WHERE id=?", (moment, item["id"]))
    return len(planned)


def record_search(connection, want_id: int, outcome: str, *, note: str = "",
                  now: str | None = None, today: date | None = None) -> str:
    """下载模块查了一次资源，记一笔，返回这一条现在的段（`phase`）。

    `found` 清零计数、取消暂时放弃；`error` 只记下这次失败，不算查过；`none` 只给老片计数，
    满 `STALE_ATTEMPT_LIMIT` 次标暂时放弃。已入库的不再记。
    """
    if outcome not in SEARCH_OUTCOMES:
        raise ValueError(f"unknown search outcome: {outcome}")
    row = get(connection, want_id)
    if row is None:
        raise ValueError("找不到这条想要")
    day = today or date.today()
    if row["state"] == ACQUIRED:
        return ACQUIRED
    moment = now or stamp()
    count, state, given_up = int(row["search_count"] or 0), row["state"], row["given_up_at"]
    if outcome == "found":
        count, state, given_up = 0, WANTED, None
    elif outcome == "none" and not is_fresh(row["release_date"], day):
        count += 1
        if count >= STALE_ATTEMPT_LIMIT and state != GIVEN_UP:
            state, given_up = GIVEN_UP, moment
    connection.execute(
        "UPDATE want_item SET search_count=?,state=?,given_up_at=?,last_search_at=?,"
        "last_search_outcome=?,last_search_note=?,updated_at=? WHERE id=?",
        (count, state, given_up, moment, outcome, note[:500] or None, moment, int(want_id)))
    return phase({**row, "state": state}, day)


def reset_search(connection, want_ids: Iterable[int], *, now: str | None = None) -> int:
    """手动重置：暂时放弃的回到待找，计数清零。已入库的不动。"""
    ids = sorted({int(value) for value in want_ids})
    moment = now or stamp()
    changed = 0
    for batch in _chunks(ids):
        changed += connection.execute(
            "UPDATE want_item SET state='wanted',search_count=0,given_up_at=NULL,updated_at=?"
            f" WHERE state<>'acquired' AND id IN ({','.join('?' * len(batch))})",
            (moment, *batch)).rowcount or 0
    return changed


@dataclass(frozen=True)
class WantTarget:
    """交给下载模块的一条待找：拿什么去搜、去哪个关注条目取。"""

    id: int
    #: 番号与它的键；关注条目没有番号时两项都是 None。
    code: str | None
    code_key: str | None
    title: str | None
    release_date: str | None
    studio: str | None
    performers: str | None
    #: 登记时来源给的作品页或帖子地址。
    link: str | None
    #: 关注条目的 id 与帖子地址；按番号登记的为 None。
    follow_item_id: int | None
    follow_url: str | None
    #: 发售 `FRESH_DAYS` 天之内：查不到不计数。
    fresh: bool
    search_count: int
    last_search_at: str | None


def pending_targets(connection, *, today: date | None = None) -> list[WantTarget]:
    """云下载（ADR-0089）与本地下载（待办第 42 条）读的入口：全部「待找」的条目，新片在前。

    不交出未发售的、暂时放弃的、已入库的；番号已经在库里、只是对账没走到的（文件名解析不出
    番号、刮削后才补上 `asset.code`）也不交出，免得把已有的再下一遍。
    """
    day = today or date.today()
    _register(connection)
    rows = _rows(
        connection,
        "SELECT w.id,w.code,w.code_key,w.title,w.release_date,w.studio,w.performers,w.link,"
        "w.follow_item_id,f.url AS follow_url,w.search_count,w.last_search_at,w.state"
        " FROM want_item w LEFT JOIN follow_item f ON f.id=w.follow_item_id"
        " WHERE w.state='wanted' AND (w.code_key IS NULL OR NOT EXISTS"
        " (SELECT 1 FROM asset a WHERE a.code IS NOT NULL AND COALESCE(a.disposal,'')<>'vanished'"
        " AND normalise_code_key(a.code)=w.code_key))"
        " ORDER BY w.created_at, w.id")
    targets = [WantTarget(
        id=int(row["id"]), code=row["code"], code_key=row["code_key"], title=row["title"],
        release_date=row["release_date"], studio=row["studio"], performers=row["performers"],
        link=row["link"], follow_item_id=row["follow_item_id"], follow_url=row["follow_url"],
        fresh=is_fresh(row["release_date"], day), search_count=int(row["search_count"] or 0),
        last_search_at=row["last_search_at"])
        for row in rows if phase(row, day) == SEARCHING]
    return sorted(targets, key=lambda target: not target.fresh)


def list_rows(connection) -> list[dict]:
    """清单页的全部行，带关注条目的来源与状态。"""
    return _rows(connection, f"SELECT {', '.join('w.' + name for name in _COLUMNS.split(','))},"
                 " s.provider AS follow_provider, f.status AS follow_status"
                 " FROM want_item w LEFT JOIN follow_item f ON f.id=w.follow_item_id"
                 " LEFT JOIN follow_source s ON s.id=f.source_id"
                 " ORDER BY w.created_at DESC, w.id DESC")
