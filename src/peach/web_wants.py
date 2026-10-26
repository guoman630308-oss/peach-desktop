"""「想要」清单的读写契约与取资料任务（ADR-0090）。

数据层与对账在 `wants`；这里是 `/api/wants` 的读写，以及给按番号登记的想要取一份资料和封面。

**取资料复用 Feed 那一套。** 问来源链是 `feed_followup.collect`，取图与落盘是
`feed_followup.ensure_cover`：封面按番号装进本机封面目录，Feed 卡、想要清单和文件入库后的
资产卡用的是同一张。Feed 壳已经取到的资料在登记时抄进快照，这里只补库外番号和壳上还缺的。
关注条目没有番号，缩略图沿用关注卡那份投影（`web_follow.item_thumbs`）。

**两处起跑。** 登记一条缺资料或缺封面的想要，当场起一轮 `want_job`；Feed 每拉一轮订阅，
跑完顺带再起一轮，作为取不到时的重试。两处读同一份 `backlog`，隔 `RETRY_AFTER` 才重试，
取不到的原因写在行上的 `scrape_error`。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from . import feed_followup, wants, web_follow
from .jobs import TaskRunConflict

#: 取不到资料或封面的想要，隔多久再试一次；与 Feed 壳同一个间隔。
RETRY_AFTER = feed_followup.RETRY_AFTER

#: 一轮最多取几部。按番号登记是一部一部点的，一轮撞上这么多已经是积压。
MAX_PER_RUN = 48


def backlog(connection, cover_root, *, exclude=(), now: datetime | None = None,
            retry_after: timedelta = RETRY_AFTER) -> list[int]:
    """有番号、还没入库、缺资料或缺本机封面、上次尝试已经过了 `retry_after` 的想要。"""
    cutoff = wants.stamp((now or datetime.now(timezone.utc)) - retry_after)
    skip = set(exclude)
    found: list[int] = []
    for want_id, code, scraped_at, scrape_error in connection.execute(
            "SELECT id,code,scraped_at,scrape_error FROM want_item WHERE state<>'acquired'"
            " AND code IS NOT NULL AND (scraped_at IS NULL OR scraped_at<?) ORDER BY created_at, id",
            (cutoff,)):
        if int(want_id) in skip:
            continue
        needs_fields = scraped_at is None or scrape_error is not None
        if needs_fields or not feed_followup.has_local_cover(cover_root, code):
            found.append(int(want_id))
    return found


def _scrape_one(contract, provider_factory, want_id: int) -> tuple[bool, bool]:
    """取一部：缺资料先问来源链，再装封面。返回 (取到资料, 现在有封面)。"""
    with contract.database.read_connection() as connection:
        row = wants.get(connection, want_id)
    if row is None or row["state"] == wants.ACQUIRED or not row["code"]:
        return False, False
    fetched = False
    if row["scraped_at"] is None or row["scrape_error"] is not None:
        try:
            fields, cover_url = feed_followup.collect(provider_factory(), row["code"])
        except Exception as error:  # noqa: BLE001 - 原因写到行上给人看
            with contract.database.write_transaction() as connection:
                connection.execute("UPDATE want_item SET scrape_error=?,scraped_at=?,updated_at=?"
                                   " WHERE id=?", (str(error)[:500], wants.stamp(), wants.stamp(),
                                                   want_id))
        else:
            names = feed_followup.performer_names(fields.get("performers"))
            with contract.database.write_transaction() as connection:
                connection.execute(
                    "UPDATE want_item SET title=COALESCE(title,?),cover_url=COALESCE(cover_url,?),"
                    "studio=COALESCE(studio,?),release_date=COALESCE(release_date,?),"
                    "performers=COALESCE(performers,?),scrape_error=NULL,scraped_at=?,updated_at=?"
                    " WHERE id=?",
                    (fields.get("title") or None, cover_url, fields.get("studio") or None,
                     fields.get("release_date") or None,
                     "、".join(name for name in names if name) or None,
                     wants.stamp(), wants.stamp(), want_id))
            fetched = True
    with contract.database.read_connection() as connection:
        cover_url = (wants.get(connection, want_id) or {}).get("cover_url")
    try:
        covered = feed_followup.ensure_cover(contract, row["code"], cover_url)
    except Exception:  # noqa: BLE001 - 取不到封面这一部照样有资料，隔一阵再试
        covered = False
    with contract.database.write_transaction() as connection:
        connection.execute("UPDATE want_item SET scraped_at=? WHERE id=?", (wants.stamp(), want_id))
    return fetched, covered


def _provider(contract):
    holder: list = []

    def get():
        if not holder:
            from .library_processing import LibraryMetadataProvider
            holder.append(LibraryMetadataProvider(contract.follow_secrets_root))
        return holder[0]
    return get


def scrape(contract, job_id: str | None = None) -> dict:
    """把积压的想要取一遍。一轮里新登记的也会接着取到：每取完一批再读一次积压。"""
    previous = (contract.want_job.snapshot() or {}) if job_id else {}
    done = set(previous.get('completed_wants') or [])
    completed = set(done)
    fetched, covers = previous.get('fetched', 0), previous.get('covers', 0)
    provider = _provider(contract)
    while len(done) < MAX_PER_RUN:
        with contract.database.read_connection() as connection:
            batch = backlog(connection, contract.cover_root, exclude=done)[:MAX_PER_RUN - len(done)]
        if not batch:
            break
        for want_id in batch:
            if job_id:
                if not contract.want_job.update(job_id, checked=len(done), total=len(done) + len(batch),
                                                current=want_id):
                    raise InterruptedError('想要资料任务已停止')
            got, covered = _scrape_one(contract, provider, want_id)
            fetched += got
            covers += covered
            done.add(want_id)
            with contract.database.read_connection() as connection:
                saved = wants.get(connection, want_id)
            if covered and saved and saved['scraped_at'] is not None and saved['scrape_error'] is None:
                completed.add(want_id)
            if job_id and not contract.want_job.checkpoint(job_id, completed_wants=sorted(completed),
                    checked=len(done), fetched=fetched, covers=covers):
                raise InterruptedError('想要资料任务已停止')
    contract.cache_bust()
    return {"ok": True, "total": len(done), "fetched": fetched, "covers": covers}


def start_scrape(contract, *, trigger: str = "manual") -> dict | None:
    """有积压就起一轮 `want_job`；没有就不起，任务中心里不留空跑的一行。"""
    with contract.database.read_connection() as connection:
        from .job_recovery import current_job_id
        if not current_job_id.get() and not backlog(connection, contract.cover_root):
            return None

    def work(job_id):
        result = scrape(contract, job_id)
        contract.want_job.update(job_id, **result, status="complete", current=None,
                                 completed_at=time.time())
    try:
        return contract.want_job.start(work, restart=True, trigger=trigger, recovery={},
                                       initial={"ok": True, "checked": 0, "total": 0, "current": None})
    except TaskRunConflict:
        # 另一个进程占着这把互斥键：登记已经落库，资料留给下一轮。
        return None


def _cover(contract, row: dict, thumbs: dict) -> tuple[str | None, bool]:
    """(封面地址, 是不是远程图)。本机封面优先，其次关注卡的缩略图，最后是来源给的地址。"""
    code = row["code"]
    if code and contract.has_cover(code):
        return f"/cover?code={quote(code)}&thumb=1", False
    thumb = thumbs.get(row["follow_item_id"]) if row["follow_item_id"] else None
    if thumb:
        return thumb, False
    return row["cover_url"], bool(row["cover_url"])


def _payload(contract, row: dict, today: date, thumbs: dict) -> dict:
    from .wants_magnets import search_for
    cover, remote = _cover(contract, row, thumbs)
    return {
        "id": int(row["id"]), "code": row["code"], "origin": row["origin"],
        "has_magnets": search_for(contract.follow_secrets_root).known_magnets(row['code'], row['release_date']) if row['code'] else None,
        "title": row["title"], "link": row["link"], "release_date": row["release_date"],
        "studio": row["studio"], "performers": row["performers"],
        "phase": wants.phase(row, today), "fresh": wants.is_fresh(row["release_date"], today),
        "search_count": int(row["search_count"] or 0), "last_search_at": row["last_search_at"],
        "last_search_outcome": row["last_search_outcome"], "given_up_at": row["given_up_at"],
        "acquired_asset_id": row["acquired_asset_id"], "acquired_at": row["acquired_at"],
        "follow_item_id": row["follow_item_id"], "follow_provider": row.get("follow_provider"),
        "cover": cover, "remote_cover": remote,
        "scraped": row["scraped_at"] is not None, "scrape_error": row["scrape_error"],
        "created_at": row["created_at"],
    }


def q_wants(contract, args) -> dict:
    """清单页的全部行与四段计数；带 `follow=<条目 id>` 只回这一条关注条目的想要。"""
    today = date.today()
    follow = str(args.get("follow") or "")
    with contract.database.read_connection() as connection:
        rows = wants.list_rows(connection)
        if follow.isdigit():
            rows = [row for row in rows if row["follow_item_id"] == int(follow)]
        thumbs = web_follow.item_thumbs(
            contract, connection, [row["follow_item_id"] for row in rows if row["follow_item_id"]])
    items = [_payload(contract, row, today, thumbs) for row in rows]
    if follow.isdigit():
        return {"ok": True, "want": items[0] if items else None}
    counts = {name: 0 for name in wants.PHASES}
    for item in items:
        counts[item["phase"]] += 1
    snapshot = contract.want_job.snapshot() or {}
    return {"ok": True, "items": items, "counts": counts,
            "scraping": snapshot.get("status") == "running"}


def _ids(body) -> list[int]:
    raw = body.get("ids")
    ids = [value for value in (raw if isinstance(raw, list) else [raw]) if type(value) is int]
    if not ids:
        raise ValueError("ids must be a nonempty list of want ids")
    return ids


def q_want_magnets(contract, args) -> dict:
    """只读资源查询；未发售、已入库与没有番号的条目不请求来源。"""
    from .wants_magnets import search_for

    raw = str(args.get("id") or "")
    if not raw.isdigit():
        raise ValueError("缺少想要条目编号")
    with contract.database.read_connection() as connection:
        row = wants.get(connection, int(raw))
    if row is None:
        raise KeyError("想要条目不存在")
    if not row["code"] or wants.phase(row, date.today()) in {"unreleased", "acquired"}:
        return {"ok": True, "state": "unavailable", "items": [], "error": "", "checked_at": None}
    return search_for(contract.follow_secrets_root).query(
        row["code"], refresh=str(args.get('refresh') or '') == '1', released=row['release_date'])


def _add(connection, body) -> dict:
    if type(body.get("feed")) is int:
        return wants.add_feed(connection, body["feed"])
    if type(body.get("follow")) is int:
        return wants.add_follow(connection, body["follow"])
    return wants.add_code(connection, str(body.get("code") or ""))


def w_wants(contract, body) -> dict:
    """登记（`add`：`code`、`feed` 或 `follow` 三选一）、移除、重置查找（`remove`/`reset` 带 `ids`）。"""
    action = str(body.get("action") or "")
    if action == "add":
        with contract.database.write_transaction() as connection:
            row = _add(connection, body)
        contract.cache_bust()
        start_scrape(contract)
        with contract.database.read_connection() as connection:
            listed = [item for item in wants.list_rows(connection) if item["id"] == row["id"]]
            thumbs = web_follow.item_thumbs(contract, connection,
                                            [row["follow_item_id"]] if row["follow_item_id"] else [])
        return {"ok": True, "created": row["created"],
                "want": _payload(contract, listed[0], date.today(), thumbs)}
    if action not in ("remove", "reset"):
        raise ValueError(f"unknown want action: {action}")
    ids = _ids(body)
    with contract.database.write_transaction() as connection:
        changed = (wants.remove if action == "remove" else wants.reset_search)(connection, ids)
    contract.cache_bust()
    return {"ok": True, "action": action, "affected": changed}
