"""数据面板：库存统计、存储卷、口味窗口、搜索历史与画质目标。

这一域的输出全是聚合数，没有一条是真相字段：它们要么来自 `COUNT`/`SUM`，要么来自
浏览历史分析产出的 JSON。因此这里的函数都可以走 `contract.cached()`，也都必须能在
缓存被 `cache_bust()` 清掉时安全丢弃结果——口径见 `web_state.WebContract.cached`。

存储卷一节读的是真实挂载点：不在线的来源要显示成「未挂载」而不是 0 字节，
否则面板会把一整块外置盘报告成「空的」。
"""
from __future__ import annotations

import json
import re
import shutil
import time

from datetime import UTC, datetime, timedelta

from .catalog_rules import tag_cat
from . import media_libraries, settings_file
from .config import LOCATION_ROOT_DECLARATIONS
from .platform import is_unmapped, root_online, system_volume, translate_ledger_path
from .taste_history import (
    analyze_history,
    build_taste_dashboard,
    refresh_history,
    remove_history_source,
    store_version,
    write_manifest,
)
from .web_activity import DEFAULT_PROFILE_ID
from .web_catalog import attach_avatar_availability, tag_is_not_a_performer_name
from .media_sources import cost_for
from .web_state import WebContract, path_version
from .taste_browsers import browser_sources, configure_browser, enabled_history_sources, enabled_bookmark_sources
from .taste_bookmarks import refresh_bookmarks


def q_search_history(contract: WebContract, limit: int = 10):
    with contract.read_connection() as connection:
        rows = connection.execute(
            "SELECT query FROM search_history ORDER BY last_used_at DESC, query LIMIT ?",
            (max(1, min(limit, 50)),),
        ).fetchall()
    return {"items": [row["query"] for row in rows]}


TASTE_WINDOWS = {"all": None, "7d": 7, "30d": 30, "90d": 90, "365d": 365}


def _taste_since(window: str) -> str | None:
    if window not in TASTE_WINDOWS:
        raise ValueError("invalid taste window")
    days = TASTE_WINDOWS[window]
    return (datetime.now(UTC) - timedelta(days=days)).isoformat() if days else None


def taste_inputs(contract: WebContract, window: str) -> tuple:
    """`q_taste` 读到的账本之外的东西的版本，给 `cached_until_changed` 当键。

    浏览历史库、分析清单与导入目录由 `scripts/taste_history.py` 在进程外写；榜单上的
    圆头像读头像目录。有时间窗的视图起点每天滑一格，按日期重算一次。
    """
    return (store_version(contract.taste_history_store),
            path_version(contract.taste_history_manifest),
            path_version(contract.taste_history_import_root),
            path_version(contract.avatar_root),
            (_taste_since(window) or "")[:10])


#: 口味榜里会出圆头像的两排，以及页面上读得到它们的那几个榜单键。标签榜和网站榜
#: 不出脸，域名那排出的是站点 favicon，都不在这里。
TASTE_FACE_RANKINGS = {
    "creator": ("creators", "browser_creators", "peach_creators"),
    "performer": ("performers", "browser_performers", "peach_performers"),
}


def _attach_taste_face_availability(contract: WebContract, rankings: dict) -> None:
    """给口味榜的人像行标上「实体图和代表作头像取不取得到」。

    榜行和别处的身份引用形状不一样（`entity_id` 与 `representative_asset_id` 是行
    自己的列，不是嵌在 `entity_ref` 里），但判据是同一对：`has_entity_image()` 加
    `has_avatar()`。缺了标志这两排就只能无条件出图、等 404 再把图摘掉。

    同一个 dict 会同时出现在多个榜单里（`peach_creators` 是 `creators` 过滤出来的
    子集，不是副本），所以按对象身份去重再标一次——否则代表作那次批量查询要按榜单
    数量重复跑。
    """
    for kind, keys in TASTE_FACE_RANKINGS.items():
        rows = list({id(row): row for key in keys
                     for row in rankings.get(key) or ()}.values())
        if not rows:
            continue
        for row in rows:
            row["has_image"] = contract.has_entity_image(kind, row.get("entity_id"))
            row["image_version"] = contract.entity_image_version(kind, row.get("entity_id"))
            #: 榜上那颗 30 px 的圆头像和别处同一份 sidecar、同一个换算。框越小，几何
            #: 居中切掉脸的代价越大——这一格除了脸没有别的东西能认人。
            row["avatar_focus"] = contract.avatar_focus(kind, row.get("entity_id"))
        attach_avatar_availability(contract, rows, key="representative_asset_id")


def q_taste(contract: WebContract, args=None):
    args = args or {}
    window = str(args.get("window") or "all")
    with contract.read_connection() as connection:
        payload = build_taste_dashboard(
            contract.taste_history_store,
            connection,
            since=_taste_since(window),
        )
    # 取图可用性在库连接之外判：它读的是头像目录的索引，不是账本。
    _attach_taste_face_availability(contract, payload.get("rankings") or {})
    updated_at = None
    try:
        manifest = json.loads(contract.taste_history_manifest.read_text(encoding="utf-8"))
        updated_at = manifest.get("updated_at")
    except (OSError, ValueError, TypeError):
        pass
    export_count = export_bytes = 0
    try:
        for path in contract.taste_history_import_root.iterdir():
            if path.is_file() and not path.name.endswith(".part"):
                export_count += 1
                export_bytes += path.stat().st_size
    except OSError:
        pass
    payload.update({
        "window": window,
        "updated_at": updated_at,
        "storage": {"exports": export_count, "bytes": export_bytes},
        "browsers": browser_sources(contract.taste_history_store),
    })
    return payload


def w_taste_refresh(contract: WebContract, body, *, progress=None, checkpoint=None, resume_state=None):
    if body.get("background"):
        job = contract.taste_refresh_job
        def work(job_id):
            result = w_taste_refresh(contract, {**body, "background": False},
                progress=lambda **fields: job.update(job_id, **fields),
                checkpoint=lambda **fields: job.checkpoint(job_id, **fields), resume_state=job.snapshot())
            job.update(job_id, **result, status="complete", message="部分浏览记录未取得" if result["warnings"] else "口味分析已更新", completed_at=time.time())
        return job.start(work, restart=True, initial={
            "stage": "discovering", "message": "正在查找 Peach 主机上的浏览器资料"},
            recovery={'background': True, 'window': body.get('window') or 'all'})
    window = str(body.get("window") or "all")
    sources = enabled_history_sources(contract.taste_history_store)
    results = list((resume_state or {}).get('refresh_results') or [])
    completed = set((resume_state or {}).get('completed_profiles') or [])
    plans = (
            ('history', sources, refresh_history),
            ('bookmarks', enabled_bookmark_sources(contract.taste_history_store), refresh_bookmarks))
    profile_keys = (resume_state or {}).get('profile_keys')
    if profile_keys is None:
        profile_keys = [f'{kind}:{source.browser}:{source.profile}:{source.path}'
                        for kind, selected, _refresh in plans for source in selected]
        if checkpoint and not checkpoint(profile_keys=profile_keys):
            raise InterruptedError('口味读取已停止')
    def profile_progress(**fields):
        if progress:
            progress(**{**fields, 'checked': len(completed), 'total': len(profile_keys)})
    for kind, selected, refresh in plans:
        for source in selected:
            key = f'{kind}:{source.browser}:{source.profile}:{source.path}'
            if key in completed or key not in profile_keys:
                continue
            rows = refresh([source], contract.taste_history_store, progress=profile_progress)
            results.extend(rows)
            if rows and all(row.get('status') != 'failed' for row in rows):
                completed.add(key)
            if checkpoint and not checkpoint(refresh_results=results.copy(), completed_profiles=sorted(completed),
                                              checked=len(completed), total=len(profile_keys)):
                raise InterruptedError('口味读取已停止')
    if contract.taste_history_store.is_file():
        analysis = analyze_history(contract.taste_history_store, contract.taste_history_root, progress=progress)
        write_manifest(contract.taste_history_manifest, results, analysis)
    contract.cache_bust()
    if progress:
        progress(stage="dashboard", checked=0, total=None, message="正在汇总口味分布、标签与创作者排名")
    return {"refresh": results, "warnings": [f"{row['browser']} · {row['profile']}：{row['error']}"
            for row in results if row.get("status") == "failed"], "dashboard": q_taste(contract, {"window": window})}


def w_taste_source(contract: WebContract, body):
    if body.get("operation") in ("add", "disable", "enable", "delete_browser"):
        configure_browser(contract.taste_history_store, body["operation"], body)
        if body["operation"] == "delete_browser":
            analysis = analyze_history(contract.taste_history_store, contract.taste_history_root)
            write_manifest(contract.taste_history_manifest, [], analysis)
        contract.cache_bust()
        return {"dashboard": q_taste(contract, {"window": str(body.get("window") or "all")})}
    if body.get("operation") != "remove":
        raise ValueError("invalid taste source operation")
    source_key = str(body.get("source_key") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", source_key):
        raise ValueError("invalid taste source key")
    removed = remove_history_source(contract.taste_history_store, source_key)
    analysis = analyze_history(contract.taste_history_store, contract.taste_history_root)
    write_manifest(contract.taste_history_manifest, [{"removed": removed}], analysis)
    contract.cache_bust()
    return {
        "removed": removed,
        "dashboard": q_taste(contract, {"window": str(body.get("window") or "all")}),
    }


def w_search_history(contract: WebContract, body):
    operation = body.get("operation", "remember")
    query = str(body.get("query", "")).strip()
    if operation == "remove":
        if not query:
            raise ValueError("query is required")
        with contract.write_transaction() as connection:
            connection.execute("DELETE FROM search_history WHERE query=?", (query,))
        return {"ok": True, "operation": operation}
    if operation != "remember" or not query or len(query) > 200:
        raise ValueError("invalid search history request")
    with contract.write_transaction() as connection:
        connection.execute(
            "INSERT INTO search_history(query,used_count,last_used_at) VALUES(?,1,datetime('now')) "
            "ON CONFLICT(query) DO UPDATE SET used_count=used_count+1,last_used_at=excluded.last_used_at",
            (query,),
        )
    return {"ok": True, "operation": operation}

_STORAGE_LABELS = {
    "system": "系统盘",
    "local": "资源盘",
    "115": "115 网盘",
    "pikpak": "PikPak 网盘",
}


def _storage_volumes() -> list[dict[str, object]]:
    """返回当前主机可验证的系统卷、资源盘和网盘容量。"""
    declarations = [("system", _STORAGE_LABELS["system"], system_volume(), True)] + [
        # 一个来源有几个根就几行；同名来源用序号区分，单根不加。
        (location,
         _STORAGE_LABELS.get(location, location) + (f" {index + 1}" if len(roots) > 1 else ""),
         translate_ledger_path(root), False)
        for location, roots in LOCATION_ROOT_DECLARATIONS.items()
        for index, root in enumerate(roots)
    ]
    volumes: list[dict[str, object]] = []
    for kind, label, root, is_system in declarations:
        mounted = not is_unmapped(root) and (is_system or root_online(root))
        row: dict[str, object] = {
            "kind": kind,
            "label": label,
            "root": str(root) if not is_unmapped(root) else None,
            "online": mounted,
            "free": None,
            "used": None,
            "total": None,
        }
        if mounted:
            try:
                usage = shutil.disk_usage(root)
                row.update(
                    free=usage.free,
                    used=max(usage.total - usage.free, 0),
                    total=usage.total,
                )
            except OSError:
                pass
        volumes.append(row)
    return volumes


#: 库存一节只数盘上还在的：标「已消失」的行留着只为个人记录（ADR-0087），不算库存。
#: 回收站里的照旧算进来。播放与消费一节是个人记录本身，不套这一条。
PRESENT = "COALESCE(disposal,'')<>'vanished'"
PRESENT_A = "COALESCE(a.disposal,'')<>'vanished'"


#: 视频的时长与画质分档，顺序同 `media_probe.context_fields` 的判据：由短到长、由高到低。
LENGTH_BANDS = ("速食", "短", "中", "长")
QUALITY_BANDS = ("4K", "2K", "1080P", "720P", "低画质")


def _bands(c, column: str, order: tuple[str, ...]) -> list[dict[str, object]]:
    """一列分档的视频数。判据里的档位按顺序全列出来，没有视频的读 0；账本里多出的档排在后面。"""
    counts = {row[0]: row[1] for row in c.execute(
        f"SELECT {column},count(*) FROM asset WHERE medium='video' AND {column} IS NOT NULL "
        f"AND {PRESENT} GROUP BY {column}")}
    extra = sorted(key for key in counts if key not in order)
    return [{"k": key, "n": counts.get(key, 0)} for key in (*order, *extra)]


#: 在线追更的一条播放不算进来的条件：它关联的馆藏视频自己已经有这一项。和 `online_played`
#: 同一个去重，一个作品在两边都播过只算一次。
_ONLINE_ONLY = ("(fi.asset_id IS NULL OR NOT EXISTS(SELECT 1 FROM asset a "
                "WHERE a.id=fi.asset_id AND {}))")


def _utc_offset(moment: datetime) -> str:
    offset = moment.strftime("%z") or "+0000"
    return f"UTC{offset[:3]}:{offset[3:]}"


def _play_activity(c, has_follow: bool) -> dict[str, object]:
    """每个作品最近一次播放落在哪一天、星期几的哪个钟点，按本机时区算。

    形状同口味页的 `activity`（`days`、`hours`、`timezone`），前端两处共用一张热力卡。
    账本每个作品只记最近一次播放的时间，所以一个作品只占一格。"""
    stamps = [row[0] for row in c.execute(
        "SELECT CAST(last_played AS REAL) FROM asset WHERE last_played IS NOT NULL")]
    if has_follow:
        stamps += [row[0] for row in c.execute(
            "SELECT CAST(fp.last_played AS REAL) FROM follow_playback fp "
            "JOIN follow_item fi ON fi.id=fp.follow_item_id WHERE fp.last_played IS NOT NULL AND "
            + _ONLINE_ONLY.format("a.last_played IS NOT NULL"))]
    days: dict[str, int] = {}
    hours: dict[tuple[int, int], int] = {}
    for stamp in stamps:
        if not stamp or stamp <= 0:
            continue
        try:
            moment = datetime.fromtimestamp(stamp).astimezone()
        except (OverflowError, OSError, ValueError):
            continue
        day = moment.date().isoformat()
        days[day] = days.get(day, 0) + 1
        slot = (moment.weekday(), moment.hour)
        hours[slot] = hours.get(slot, 0) + 1
    return {
        "timezone": _utc_offset(datetime.now().astimezone()),
        "days": [{"date": day, "count": count} for day, count in sorted(days.items())],
        "hours": [{"weekday": weekday, "hour": hour, "count": count}
                  for (weekday, hour), count in sorted(hours.items())],
    }


def _replays(c, has_follow: bool) -> list[dict[str, int]]:
    """播放次数的分布：播过 k 次的作品有几个。合计与 `consumption.played` 相同。"""
    counts: dict[int, int] = {}
    rows = list(c.execute("SELECT play_count,count(*) FROM asset WHERE play_count>0 GROUP BY play_count"))
    if has_follow:
        rows += c.execute(
            "SELECT fp.play_count,count(*) FROM follow_playback fp JOIN follow_item fi "
            "ON fi.id=fp.follow_item_id WHERE fp.play_count>0 AND "
            + _ONLINE_ONLY.format("a.play_count>0") + " GROUP BY fp.play_count").fetchall()
    for plays, n in rows:
        counts[int(plays)] = counts.get(int(plays), 0) + n
    return [{"k": plays, "n": n} for plays, n in sorted(counts.items())]


def q_stats(contract: WebContract):
    """统计页：库存 / 归属 / 标签 / 本地与在线播放 / 各存储卷。"""
    with contract.read_connection() as c:
        out = {}
        out["by_loc"] = [dict(r) for r in c.execute(
            "SELECT location k, count(*) n, COALESCE(sum(size),0) bytes, "
            "SUM(CASE WHEN medium='video' THEN 1 ELSE 0 END) videos "
            f"FROM asset WHERE {PRESENT} GROUP BY location ORDER BY bytes DESC")]
        out["by_medium"] = [dict(r) for r in c.execute(
            "SELECT medium k, count(*) n, COALESCE(sum(size),0) bytes "
            f"FROM asset WHERE {PRESENT} GROUP BY medium ORDER BY bytes DESC")]
        out["by_length"] = _bands(c, "ctx_length", LENGTH_BANDS)
        out["by_quality"] = _bands(c, "ctx_quality", QUALITY_BANDS)
        config = settings_file.active()
        out["by_library"] = []
        for library in media_libraries.libraries(config):
            clause, params = media_libraries.predicate(config, library["id"])
            counts = c.execute(
                "SELECT count(*) videos,COALESCE(sum(a.size),0) bytes FROM asset a "
                f"WHERE a.medium='video' AND {PRESENT_A} AND " + clause, params,
            ).fetchone()
            out["by_library"].append({"k": library["id"], "name": library["name"],
                                      "icon": library["icon"], **dict(counts)})
        v = c.execute(f"SELECT count(*) FROM asset WHERE medium='video' AND {PRESENT}").fetchone()[0]
        def one(sql, *a):
            r = c.execute(sql, a).fetchone()
            return r[0] if r else 0
        out["attribution"] = {
            "videos": v,
            "creator": one("SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae "
                           "JOIN entity e ON e.id=ae.entity_id JOIN asset a ON a.id=ae.asset_id "
                           f"WHERE a.medium='video' AND {PRESENT_A} AND e.kind='creator'"),
            "code": one(f"SELECT count(*) FROM asset WHERE medium='video' AND {PRESENT} "
                        "AND code IS NOT NULL AND code<>''"),
            "studio": one("SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae "
                          "JOIN entity e ON e.id=ae.entity_id JOIN asset a ON a.id=ae.asset_id "
                          f"WHERE a.medium='video' AND {PRESENT_A} AND e.kind='studio'"),
            "thumb": one(f"SELECT count(*) FROM asset WHERE medium='video' AND {PRESENT} "
                         "AND snapshot_path IS NOT NULL"),
            "duration": one(f"SELECT count(*) FROM asset WHERE medium='video' AND {PRESENT} "
                            "AND duration IS NOT NULL"),
        }
        out["tag_source"] = [dict(r) for r in c.execute(
            "SELECT source k, count(*) n, count(DISTINCT asset_id) assets "
            "FROM asset_tag GROUP BY source ORDER BY n DESC")]
        out["tag_cov"] = one("SELECT count(DISTINCT asset_id) FROM asset_tag "
                             f"WHERE asset_id IN (SELECT id FROM asset WHERE {PRESENT}) "
                             "AND source IN ('name','r18','vision','vision-creator',"
                             "'vision_creator','vision_creator_review')")
        out["top_tags"] = [dict(r, cat=tag_cat(r["k"])) for r in c.execute(
            "SELECT t.tag k, count(*) n FROM asset_tag t JOIN asset a ON a.id=t.asset_id "
            f"WHERE a.medium='video' AND {PRESENT_A} AND t.source IN ('name','r18','vision','vision-creator',"
            "'vision_creator','vision_creator_review') "
            "AND " + tag_is_not_a_performer_name("peach_normalize(t.tag)") + " "
            "GROUP BY t.tag ORDER BY n DESC LIMIT 30")]
        tables = {row[0] for row in c.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )}
        library_played = one("SELECT count(*) FROM asset WHERE play_count>0")
        library_seconds = one("SELECT COALESCE(sum(play_seconds),0) FROM asset")
        online_played = 0
        online_seconds = 0
        has_follow = "follow_playback" in tables
        if has_follow:
            online_played = one(
                "SELECT count(*) FROM follow_playback fp JOIN follow_item fi "
                "ON fi.id=fp.follow_item_id WHERE fp.play_count>0 AND "
                + _ONLINE_ONLY.format("a.play_count>0")
            )
            online_seconds = one(
                "SELECT COALESCE(sum(play_seconds),0) FROM follow_playback"
            )
        out["consumption"] = {
            "played": library_played + online_played,
            "library_played": library_played,
            "online_played": online_played,
            "play_seconds": library_seconds + online_seconds,
            "library_play_seconds": library_seconds,
            "online_play_seconds": online_seconds,
            "o_total": one("SELECT COALESCE(sum(o_count),0) FROM asset"),
            "liked": one("SELECT count(*) FROM asset_preference WHERE profile_id=? AND liked=1",
                         DEFAULT_PROFILE_ID) if "asset_preference" in tables else 0,
            "dislike": one("SELECT count(*) FROM asset WHERE feedback='dislike'"),
            "seen": one("SELECT count(*) FROM asset WHERE feedback='seen'"),
            "trash": one("SELECT count(*) FROM asset WHERE disposal='trash'"),
            "skimmed": one("SELECT count(*) FROM asset WHERE duration>0 AND play_seconds>0 "
                           "AND max_reached>0.6 AND play_seconds/duration < max_reached-0.25"),
        }
        recent = [dict(r, kind="library") for r in c.execute(
            "SELECT id,name,creator,play_seconds,duration,max_reached,leave_ratio,o_count,"
            "CAST(last_played AS REAL) last_played FROM asset WHERE last_played IS NOT NULL "
            "ORDER BY CAST(last_played AS REAL) DESC LIMIT 12")]
        if "follow_playback" in tables:
            recent.extend(dict(r, kind="online", leave_ratio=None, o_count=0) for r in c.execute(
                "SELECT fi.id,fi.title name,fs.label creator,fp.play_seconds,fi.duration,"
                "fp.max_reached,fp.last_played FROM follow_playback fp "
                "JOIN follow_item fi ON fi.id=fp.follow_item_id "
                "JOIN follow_source fs ON fs.id=fi.source_id WHERE fp.last_played IS NOT NULL "
                "ORDER BY fp.last_played DESC LIMIT 12"
            ))
        out["recent"] = sorted(
            recent, key=lambda row: float(row.get("last_played") or 0), reverse=True,
        )[:12]
        out["play_activity"] = _play_activity(c, has_follow)
        out["replays"] = _replays(c, has_follow)
    out["storage_volumes"] = _storage_volumes()
    measured = [row for row in out["storage_volumes"] if row["total"] is not None]
    out["storage_summary"] = {
        "volumes": len(out["storage_volumes"]),
        "online": sum(1 for row in out["storage_volumes"] if row["online"]),
        "measured": len(measured),
        "free": sum(int(row["free"]) for row in measured),
        "used": sum(int(row["used"]) for row in measured),
        "total": sum(int(row["total"]) for row in measured),
    }
    system = next((row for row in out["storage_volumes"] if row["kind"] == "system"), None)
    out["system_disk"] = (
        {"root": system["root"], "free": system["free"], "total": system["total"]}
        if system and system["total"] is not None else None
    )
    out["disk_c"] = out["system_disk"]  # 0.6.x 客户端兼容别名
    return out


def q_quality_goals(contract: WebContract, args):
    """List explicit better-version targets for the management surface."""
    limit = min(max(int(args.get("limit", "60")), 1), 200)
    offset = max(int(args.get("offset", "0")), 0)
    with contract.read_connection() as connection:
        total = connection.execute(
            "SELECT count(*) FROM asset_quality_goal WHERE profile_id=? AND wanted=1",
            (DEFAULT_PROFILE_ID,),
        ).fetchone()[0]
        rows = [dict(row) for row in connection.execute(
            "SELECT a.id,a.name,a.code,a.location,a.path,a.size,a.duration,a.snapshot_path,"
            "g.reason,g.updated_at FROM asset_quality_goal g "
            "JOIN asset a ON a.id=g.asset_id "
            "WHERE g.profile_id=? AND g.wanted=1 "
            "ORDER BY g.updated_at DESC,a.id DESC LIMIT ? OFFSET ?",
            (DEFAULT_PROFILE_ID, limit, offset),
        )]
    for row in rows:
        row["cost"] = cost_for(row['location'], row.pop('path', None))
        row["has_thumb"] = contract.has_snapshot(row["snapshot_path"])
        row["has_cover"] = contract.has_cover(row.get("code"))
        row["cover_version"] = contract.cover_version(row.get("code"))
        row.pop("snapshot_path", None)
    return {"total": total, "items": rows, "offset": offset,
            "has_more": offset + len(rows) < total}
