"""诊断报告的只读库健康与来源解析证据。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .catalog_rules import normalise_code_key, release_code_from_text
from .metadata_policy import MAKER_SOURCES
from .metadata_routes import AMANE_STAGE, ROUTES
from .scraping_access import SOURCES, cooldown_state, source_checks

LIMIT = 500
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 50_000


def timestamp(value):
    try:
        moment = (datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float))
                  else datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def inventory(root):
    try:
        with os.scandir(root) as entries:
            return {entry.name[:-4].casefold() for entry in entries if entry.name.endswith(".jpg")}
    except FileNotFoundError:
        return set()
    except OSError:
        return None


def group(label, rows=None):
    if rows is None:
        return {"label": label, "status": "unknown", "count": None, "items": [], "truncated": False}
    ordered = sorted(rows, key=lambda row: row["id"])
    return {"label": label, "status": "ok", "count": len(ordered), "items": ordered[:LIMIT],
            "truncated": len(ordered) > LIMIT}


def failures(config):
    state_path = config.directory("state") / "library-processing.json"
    try:
        if state_path.stat().st_size > 1024 * 1024:
            return None, None
        state = json.loads(state_path.read_text(encoding="utf-8"))
        job = state.get("job_id")
        if not isinstance(job, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", job):
            return None, None
        path = state_path.parent / f"library-processing-{job}.issues.jsonl"
        if not path.exists():
            return ({"identification": set(), "metadata": set()}, timestamp(state.get("finished_at"))) if not state.get("issue_count") else (None, None)
        if path.stat().st_size > MAX_LOG_BYTES:
            return None, None
        result = {"identification": set(), "metadata": set()}
        with path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= MAX_RECORDS:
                    return None, None
                row = json.loads(line)
                asset_id = row.get("asset_id")
                if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id <= 0:
                    continue
                if row.get("severity") not in {"error", "warning"}:
                    continue
                action = row.get("failed_action")
                if action == "querying_metadata":
                    result["metadata"].add(asset_id)
                # reading_local 的其余资产问题由 NFO 解析与番号冲突分支产生。
                elif action == "reading_local" and row.get("message") not in {"媒体文件不可访问", "本地封面无法读取"}:
                    result["identification"].add(asset_id)
        return result, timestamp(state.get("finished_at") or state.get("started_at"))
    except FileNotFoundError:
        return {"identification": set(), "metadata": set()}, None
    except (OSError, ValueError, TypeError, AttributeError):
        return None, None


def _library_item(row):
    asset_id, code, duration, name, title, original, location, path, size = row
    candidate = str(code or "")
    safe_code = release_code_from_text(candidate) if len(candidate) <= 80 else None
    filename = str(name or str(path or "").replace("\\", "/").rsplit("/", 1)[-1])
    return {"id": asset_id, "code": safe_code or None, "name": filename,
            "title": str(title or original or filename), "location": location,
            "path": str(path or ""), "duration": duration, "size": size}


def library(settings, config):
    groups = {key: group(label) for key, label in (("identification", "识别失败"),
              ("metadata", "补资料失败"), ("cover", "缺封面"), ("duration", "缺时长"))}
    try:
        with closing(sqlite3.connect(settings.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute("SELECT id,code,duration,name,catalog_title,original_title,location,path,size "
                                      "FROM asset WHERE medium='video' "
                                      "AND COALESCE(disposal,'') NOT IN ('vanished','trash')").fetchall()
    except (sqlite3.Error, OSError):
        return {"groups": groups, "issue_at": None}
    assets = {row[0]: _library_item(row) for row in rows}
    groups["duration"] = group("缺时长", [assets[row[0]] for row in rows if not row[2] or row[2] <= 0])
    covers, posters = inventory(settings.cover_root), inventory(settings.poster_root)
    if covers is not None and posters is not None:
        missing = [assets[row[0]] for row in rows
                   if (normalise_code_key(row[1]).casefold() not in covers if row[1]
                       else f"{row[0]}_4" not in posters)]
        groups["cover"] = group("缺封面", missing)
    failed, issue_at = failures(config)
    if failed is not None:
        for key, label in (("identification", "识别失败"), ("metadata", "补资料失败")):
            groups[key] = group(label, [assets[value] for value in failed[key] if value in assets])
    return {"groups": groups, "issue_at": issue_at}


SOURCE_CHECK_REASONS = {
    "proxy": "代理连接失败", "timeout": "来源连接超时", "network": "来源连接未取得",
    "auth_required": "来源要求登录或拒绝当前出口", "cloudflare_challenge": "来源要求人机验证",
    "cloudflare_blocked": "来源拦截当前出口", "ip_banned": "来源拦截当前出口",
    "geo_restricted": "来源限制当前地区", "rate_limited": "来源正在限流",
    "server_error": "来源服务异常", "parse_error": "资料解析失败",
    "not_found": "上次查询未命中", "cooldown": "来源正在冷却",
}


def _source_health(cooling, failed, fresh, recent, connection_ok, reason_key, *, has_success, has_check):
    """来源诊断优先报告冷却与失败，再报告解析和连接证据。"""
    if cooling:
        return "warning", "来源正在冷却"
    if failed:
        return "warning", SOURCE_CHECK_REASONS.get(reason_key, "来源未取得资料")
    if fresh:
        return "ok", "资料解析通过"
    if has_success:
        return "stale", "曾解析成功，近期解析状态待复查"
    if recent and connection_ok:
        return "unknown", "连接检查通过；解析状态未取得"
    if has_check:
        return "stale", "已有检查记录，解析状态待复查"
    return "unchecked", "尚无检查或解析记录，可在来源和凭证中检查连接"


def _profile_success(settings):
    """从女优资料的来源证据读取最近解析成功时间。"""
    success = {"minnano-av": None, "javdb": None}
    try:
        with closing(sqlite3.connect(settings.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            connection.execute("PRAGMA query_only=ON")
            for url, stamp in connection.execute("SELECT source_url,fetched_at FROM performer_profile "
                                                "WHERE source LIKE 'auto:performer-profile@%' ORDER BY fetched_at DESC"):
                if urlsplit(str(url)).hostname in {"minnano-av.com", "www.minnano-av.com"} and timestamp(stamp):
                    success["minnano-av"] = timestamp(stamp)
                    break
            for raw, stamp in connection.execute("SELECT metadata_json,last_synced_at FROM entity_external_ref "
                                                "WHERE provider='javdb' AND external_kind='performer' ORDER BY last_synced_at DESC"):
                try:
                    source = str(json.loads(raw or "{}").get("source", ""))
                except (ValueError, AttributeError):
                    continue
                if (source == "auto:performer-profile" or source.startswith("auto:performer-profile@")) and timestamp(stamp):
                    success["javdb"] = timestamp(stamp)
                    break
    except (sqlite3.Error, OSError, ValueError):
        pass
    return success


def sources(settings, config, *, now=None):
    now = time.time() if now is None else now
    success = _profile_success(settings)
    result = []
    optional = set(AMANE_STAGE) - {source for chain in ROUTES.values() for source in chain}
    for key in dict.fromkeys([*success, *SOURCES]):
        if key == "instagram" or key in MAKER_SOURCES:
            continue
        until, _ = cooldown_state(config.directory("secrets"), key)
        cooling = now < until < now + 366 * 86400
        checks = source_checks(config.directory("secrets"), key)
        if key in optional and not cooling and not checks:
            continue
        parsed = checks.get("metadata", {})
        connection = checks.get("connection", {})
        candidate = timestamp(parsed.get("last_success_at"))
        if candidate and (not success.get(key) or candidate > success[key]):
            success[key] = candidate
        stamp = success.get(key)
        fresh = bool(stamp) and 0 <= now - datetime.fromisoformat(stamp).timestamp() <= 86400
        last = max((check for check in (parsed, connection) if timestamp(check.get("checked_at"))),
                   key=lambda check: check["checked_at"], default={})
        recent = 0 <= now - float(last.get("checked_at", 0)) <= 86400
        reason_key = last.get("reason", "") if recent else ""
        failed = recent and not last.get("ok") and reason_key != "not_found"
        status, reason = _source_health(cooling, failed, fresh, recent, connection.get("ok"), reason_key,
                                        has_success=bool(stamp), has_check=bool(last))
        result.append({"source": key, "label": SOURCES[key]["label"],
                       "status": status,
                       "reason": reason, "last_check_at": timestamp(last.get("checked_at")),
                       "elapsed_s": last.get("elapsed_s"), "last_success_at": stamp,
                       "cooldown_until": datetime.fromtimestamp(until, timezone.utc).isoformat() if cooling else None})
    return result
