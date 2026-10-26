"""只读 JAV 发现：库外已知作品与少看的馆藏共用口味和展示契约。"""
from __future__ import annotations

import re

from . import entities, feeds, web_catalog, web_feeds, web_settings, wants
from .related import rank_related
from .taste_preferences import preference_profile, preference_score
from .genre_decisions import load_genre_decisions
from .jav_discovery_metadata import discovery_metadata, source_number
from .web_jav_detail import _source_fields, discovery_cover_url, visible_actors, source_performer_refs

def _identity(connection, kind, name):
    found = entities.resolve_entity(connection, kind, name)
    return int(found["id"]) if found else f"name:{entities.normalize_entity_name(name)}"


def _cached_movie(code, contract):
    from .javdb_public import PUBLIC_API, public_cache_scope, cache_checked_at
    metadata = discovery_metadata(contract)
    stored, _ = metadata.peek(code)
    if stored:
        return stored
    with public_cache_scope(contract.cover_root.parent / 'jav-public'):
        source = PUBLIC_API.peek_movie(code)
        if source:
            metadata.remember(code, source, checked_at=cache_checked_at('movie:' + code, exact=True))
    return source or {}


def _external(connection, contract):
    feeds.register_functions(connection, web_settings.hidden_compilations(contract.database))
    rows = [dict(row) for row in connection.execute(
        "SELECT d.* FROM feed_discovery d WHERE d.ignored_at IS NULL AND "
        + " AND ".join(web_feeds.LISTED) + " ORDER BY d.id")]
    identities = {}
    excluded = entities.male_performer_ids(connection)
    decisions = load_genre_decisions(connection)
    for row in rows:
        detail = _cached_movie(row["code"], contract)
        row['cover_url'] = discovery_cover_url(row, detail)
        projection = _source_fields(detail, decisions)
        tags = [tag["name"] for tag in projection["tag_refs"]]
        row["source_tags"] = projection["tags"]
        row["tag_refs"] = projection["tag_refs"]
        row["source_score"] = source_number(detail.get("score"))
        _, metadata_fields = discovery_metadata(contract).peek(row["code"])
        row.update(metadata_fields)
        series = detail.get("series_name") or ""
        if detail.get("maker_name"):
            row["studio"] = detail["maker_name"]
        source_actors = visible_actors(connection, detail, excluded)
        actors = [actor['name'] for actor in source_actors]
        row['_source_actors'] = source_actors
        if 'actors' in detail:
            row["performers"] = "、".join(actors)
        minutes = source_number(detail.get("duration"))
        row["duration"] = minutes * 60 if minutes else None
        features = {}
        for kind, values in (("performer", re.split(r"[,，、;/]", row["performers"] or "")),
                             ("studio", [row["studio"] or ""]), ("tag", tags), ("series", [series])):
            features[kind] = set()
            for name in values:
                name = name.strip()
                if not name:
                    continue
                key = (kind, name)
                if key not in identities:
                    identities[key] = _identity(connection, kind, name)
                features[kind].add(identities[key])
        row["entities"] = features
        features["creator"] = {_identity(connection, "creator", name) for name in actors
                               if entities.resolve_entity(connection, "creator", name)}
        row["tags"] = tags
        row["asset_id"] = None
    return rows


def _library(connection):
    # 完播作品和反复观看作品不属于「少看」；未知时长只按已有观看次数判断。
    rows = [dict(row) for row in connection.execute(
        "SELECT a.id,a.location,a.name,COALESCE(NULLIF(a.catalog_title,''),NULLIF(a.original_title,''),a.name) AS title,a.creator,a.studio,a.code,"
        "a.release_date,a.duration,a.snapshot_path,a.play_count,a.max_reached,a.region,"
        + web_catalog.EFFECTIVE_REGION_SQL + " AS effective_region FROM asset a WHERE "
        + web_catalog.JAV_ASSET_PREDICATE
        + " AND a.medium='video' AND a.disposal IS NULL AND COALESCE(a.feedback,'') NOT IN ('dislike','seen')"
        " AND COALESCE(a.play_count,0)<=1 AND (COALESCE(a.duration,0)<=0"
        " OR COALESCE(a.max_reached,0)<a.duration*0.5) ORDER BY a.id")]
    by_id = {row["id"]: row for row in rows}
    excluded = entities.male_performer_ids(connection)
    for row in rows:
        row.update({"entities": {}, "tags": [], "asset_id": row["id"], "cover_url": "", "link": "",
                    "source_score": None, "metadata_state": "ready", "metadata_fields": {}})
    for offset in range(0, len(rows), 800):
        ids = [row["id"] for row in rows[offset:offset + 800]]
        marks = ",".join("?" * len(ids))
        for aid, identity, kind, name in connection.execute(
            "SELECT ae.asset_id,e.id,e.kind,e.canonical_name FROM asset_entity ae"
            " JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id IN (" + marks + ")"
            " AND e.kind IN ('performer','creator','tag','studio','series') AND (e.kind<>'tag' OR "
            + web_catalog.tag_not_hidden("ae.asset_id", "e.normalized_name") + ") ORDER BY e.canonical_name", ids):
            if identity in excluded:
                continue
            row = by_id[aid]
            row["entities"].setdefault(kind, set()).add(identity)
            if kind == "tag" and web_catalog.tag_cat(name) in (
                    "general", "relationship", "role", "appearance", "scene", "story", "position"):
                row["tags"].append(name)
        # 兼容投影仍须同一身份解析，不以名称子串匹配。
        for row in rows[offset:offset + 800]:
            if not row["entities"].get("studio") and row.get("studio"):
                row["entities"]["studio"] = {_identity(connection, "studio", row["studio"])}
            if not row["entities"].get("creator") and row.get("creator"):
                row["entities"]["creator"] = {_identity(connection, "creator", row["creator"])}
    return rows


def _metadata_response(contract, args, candidates, picked, limit, scope):
    if scope != "external":
        return {}
    cursor = max(0, min(int(args.get("metadata_cursor") or 0), len(candidates)))
    cohort = picked if picked and not cursor else sorted(candidates, key=lambda row: row["id"])[cursor:cursor + limit]
    metadata = discovery_metadata(contract)
    if args.get("metadata_cancel") == "1":
        job = metadata.cancel(args.get("metadata_job_id"))
    else:
        job = metadata.ensure([row["code"] for row in cohort], refresh=args.get("metadata_refresh") == "1",
                              job_id=args.get("metadata_job_id"))
    if not job:
        return {}
    public_job = {key: value for key, value in job.items() if key not in ("codes", "error")}
    public_job["message"] = _metadata_message(job)
    public_job["error"] = public_job["message"] if job["status"] == "failed" else ""
    response = {"metadata_job": public_job,
                "metadata_coverage": sum(row["metadata_state"] in ("ready", "partial", "stale") for row in picked),
                "metadata_next_cursor": cursor + limit if not picked and cursor + limit < len(candidates) else None}
    if job["status"] == "running":
        response["poll_after_ms"] = 3000
    return response


def _metadata_message(job):
    messages = {"running": "正在读取作品资料", "complete": "作品资料读取完成", "failed": "作品资料未取得"}
    reasons = {"request_budget": "本轮请求预算已用完", "byte_budget": "本轮流量预算已用完",
               "time_budget": "本轮时间预算已用完", "source_cooldown": "来源暂时不可用，已停止读取",
               "cancelled": "已停止读取作品资料"}
    return reasons.get(job.get("stop_reason"), messages.get(job["status"], "作品资料未取得"))


def q_jav_discoveries(contract, args):
    """口味采用 IDF/MMR；资料补齐只处理一次请求确定的有限集合。"""
    scope = "library" if args.get("scope") == "library" else "external"
    limit = max(1, min(int(args.get("limit") or 60), 100))
    with contract.read_connection() as connection:
        strengths, names, evidence = preference_profile(connection, contract.taste_history_store)
        profile = {"id": 0, "entities": {kind: set(values) for kind, values in strengths.items()}}
        candidates = _library(connection) if scope == "library" else _external(connection, contract)
        unique = {}
        for row in candidates:
            key = web_catalog.normalise_code_key(row["code"]) or row["id"]
            unique.setdefault(key, row)
        candidates = list(unique.values())
        # 已知候选按匹配维度排序后再限定计算量，不以发行时间裁掉旧作。
        for row in candidates:
            row["preference_score"], row["reasons"] = preference_score(row, strengths, names, evidence)
        candidates.sort(key=lambda row: (-row["preference_score"], row["id"]))
        bounded = candidates[:4000]
        sort = args.get("sort") if args.get("sort") in ("taste", "release_date", "source_score", "duration", "code") else "taste"
        picked = rank_related(profile, bounded, limit, seed=f"jav:{scope}",
                              order_by=None if sort == "taste" else sort, descending=args.get("direction") != "asc",
                              relevance_for=lambda row: row["preference_score"])
        wanted = wants.wanted_keys(connection, (web_catalog.normalise_code_key(row["code"]) for row in picked))
        refs = web_feeds._discovery_performers(connection, contract, picked) if scope == "external" else {}
        for row in picked:
            actors = row.pop('_source_actors', [])
            if actors:
                refs[row['id']] = source_performer_refs(contract, connection, actors)
        for row in picked:
            row["score"] = row.pop("related_score")
            row["taste_score"] = row["score"]
            row.pop("preference_score", None)
            row["wanted"] = web_catalog.normalise_code_key(row["code"]) in wanted
            row.pop("why", None)
            row.pop("entities", None)
        if sort == "taste":
            picked.sort(key=lambda row: (row["taste_score"], row["id"]), reverse=args.get("direction") != "asc")
        for rank, row in enumerate(picked, 1):
            row["rank"] = rank
    if scope == "library":
        web_catalog.attach_card_performers(contract, picked)
        for row in picked:
            row["performer_refs"] = row.pop("performer_entities", [])
            row["performers"] = "、".join(row["performers"])
            web_catalog.attach_artwork_fields(contract, row)
            row["has_thumb"] = contract.has_snapshot(row.pop("snapshot_path", None))
    else:
        for row in picked:
            row["performer_refs"] = refs[row["id"]]
            row["performers"] = "、".join(ref["name"] for ref in row["performer_refs"])
            row["has_cover"] = contract.has_cover(row["code"])
            row["cover_frame"] = contract.cover_frame(row["code"])
            row["cover_version"] = contract.cover_version(row["code"])
            row["poster_box"] = contract.poster_box(row["code"])
    response = {"scope": scope, "items": picked, "candidate_total": len(candidates), "sort": sort,
                "ranking_method": "mmr" if sort == "taste" else "field-sort",
                "direction": "asc" if args.get("direction") == "asc" else "desc",
                "limited": len(candidates) > 4000, "source": "library" if scope == "library" else "known-works"}
    response.update(_metadata_response(contract, args, bounded, picked, limit, scope))
    return response
