"""库外 JAV 作品详情的只读投影，来源资料不构成 ledger 断言。"""
from __future__ import annotations

import re
import json
import base64
from datetime import datetime, timezone
from functools import wraps

from . import entities, wants, web_catalog
from .catalog_rules import normalise_code_key, tag_cat
from .genre_decisions import load_genre_decisions
from .genre_taxonomy import resolve_genre
from .javdb_public import (MAX_REVIEW_PAGES, PAGE_SIZE, PUBLIC_API, PublicSourceUnavailable,
                          actor_gender, preview_video_url, public_image_url, public_cache_scope, cache_checked_at, cache_error)
from .jav_discovery_metadata import discovery_metadata
from .sources.javdb import maker_writing
from .remote_media_cache import AVATAR_REFRESH_SECONDS, RemoteMediaUnavailable, cache_for
from .preview_cache import PreviewCache


def detail_code(args) -> str:
    raw = str(args.get("code") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,64}", raw):
        raise ValueError("需要有效的作品番号")
    return normalise_code_key(raw)


def discovery_cover_url(item, source) -> str:
    """卡片只显示合格图源的封面地址。"""
    from .jav_cover_fetch import is_javdb_image
    for value in (item.get('cover_url'), source.get('cover_url')):
        if value and not is_javdb_image(value):
            return value
    return ''


def _refs(contract, connection, kind, names):
    refs = []
    for name in dict.fromkeys(str(name or "").strip() for name in names):
        if not name:
            continue
        lookup = maker_writing(name) if kind == "studio" else name
        entity = entities.resolve_entity(connection, kind, lookup)
        if entity is None and kind == "studio":
            matches = connection.execute(
                "SELECT DISTINCT e.* FROM entity e LEFT JOIN entity_alias a ON a.entity_id=e.id "
                "WHERE e.kind='studio' AND (e.canonical_name=? COLLATE NOCASE OR a.alias=? COLLATE NOCASE) "
                "ORDER BY e.id LIMIT 2", (lookup, lookup)).fetchall()
            entity = matches[0] if len(matches) == 1 else None
        refs.append(web_catalog.entity_ref(contract, kind, entity["id"], entity["canonical_name"])
                    if entity else {"id": None, "name": name, "has_image": False})
    return refs


def _source_cache(handler):
    @wraps(handler)
    def read(contract, args):
        with public_cache_scope(contract.cover_root.parent / 'jav-public') as reads:
            payload = handler(contract, args)
            if handler.__name__ == 'q_jav_image':
                return payload
            prefix = {'q_jav_reviews': ('reviews:',), 'q_jav_resources': ('reviews:', 'magnets:')}.get(
                handler.__name__, ('movie:',))
            selected = [row for row in reads if row['key'].startswith(prefix)]
            if selected:
                payload['checked_at'] = datetime.fromtimestamp(min(row['saved'] for row in selected), timezone.utc).isoformat()
            errors = list(dict.fromkeys(row['error'] for row in reads if row['error']))
            if errors:
                payload['cache_stale'] = True
                payload['message'] = '；'.join(errors)
                if 'warnings' in payload:
                    payload['warnings'].extend(errors)
            return payload
    return read


@_source_cache
def q_jav_detail(contract, args) -> dict:
    code = detail_code(args)
    with contract.database.read_connection() as connection:
        row = connection.execute("SELECT * FROM feed_discovery WHERE code=? COLLATE NOCASE LIMIT 1", (code,)).fetchone()
        item = dict(row) if row else {}
    source = None
    message = ""
    try:
        stored, _ = discovery_metadata(contract).peek(code)
        PUBLIC_API.remember_movie_identity(code, stored or {})
        source = PUBLIC_API.movie(code, refresh=True) if args.get('refresh') == '1' else PUBLIC_API.movie(code)
        discovery_metadata(contract).remember(code, source, checked_at=cache_checked_at('movie:'))
        if cache_error('movie:' + code):
            discovery_metadata(contract).remember(code, error=cache_error('movie:' + code))
    except PublicSourceUnavailable as error:
        message = str(error)
        discovery_metadata(contract).remember(code, error=message)
    if source:
        item.update(title=source.get("title") or source.get("origin_title") or item.get("title"),
                    release_date=source.get("release_date") or item.get("release_date"),
                    studio=source.get("maker_name") or item.get("studio"))
    payload = {"ok": True, "state": "ready" if source else "partial", "message": message,
               "code": code, "title": item.get("title") or code,
               "release_date": item.get("release_date"), "studio": item.get("studio")}
    with contract.database.read_connection() as connection:
        decisions = load_genre_decisions(connection)
    payload.update(_source_fields(source or {}, decisions))
    payload.update(_artwork_fields(contract, code, item, source or {}, refresh=args.get('refresh') == '1'))
    if payload.get('preview_message'):
        payload['message'] = '；'.join(filter(None, (payload['message'], payload['preview_message'])))
    payload.update(_identity_fields(contract, code, item, source or {}))
    return payload


def visible_actors(connection, source, excluded=None):
    """来源演员资料保持完整，使用时排除明确登记的男优。"""
    excluded = entities.male_performer_ids(connection) if excluded is None else excluded
    actors = []
    for actor in source.get('actors') or []:
        if not isinstance(actor, dict) or not actor.get('name') or actor_gender(actor) == 'male':
            continue
        entity = entities.resolve_entity(connection, 'performer', actor['name'])
        if entity is None or entity['id'] not in excluded:
            actors.append(actor)
    return actors


def _identity_fields(contract, code, item, source):
    with contract.database.read_connection() as connection:
        actors = visible_actors(connection, source)
        if 'actors' not in source:
            actors = [{'name': name} for name in re.split(r"[、,，]", str(item.get('performers') or ''))]
        performer_refs = source_performer_refs(contract, connection, actors)
        studio_refs = _refs(contract, connection, "studio", [item.get("studio")])
        for ref in studio_refs:
            ref["has_logo"] = contract.has_logo(ref["name"])
            ref["logo_version"] = contract.logo_version(ref["name"])
        series_refs = _refs(contract, connection, "series", [(source or {}).get("series_name")])
        wanted = code in wants.wanted_keys(connection, [code])
        want = connection.execute("SELECT id FROM want_item WHERE code_key=? AND state<>'acquired' LIMIT 1", (code,)).fetchone()
    return {"performers": "、".join(ref["name"] for ref in performer_refs),
            "performer_refs": performer_refs, "studio_refs": studio_refs, "series_refs": series_refs,
            "wanted": wanted, "want_id": int(want[0]) if want else None}


def source_performer_refs(contract, connection, actors):
    """详情与发现共用来源身份；头像是否可取不保存签名地址。"""
    refs = []
    for actor in actors:
        matched = _refs(contract, connection, 'performer', [actor.get('name')])
        if not matched:
            continue
        ref = matched[0]
        gender = actor_gender(actor)
        if gender == 'unknown' and ref.get('id'):
            local = connection.execute('SELECT metadata_json FROM entity WHERE id=?', (ref['id'],)).fetchone()
            try:
                metadata = json.loads(local[0] or '{}') if local else {}
                gender = actor_gender(metadata) if isinstance(metadata, dict) else 'unknown'
            except (ValueError, TypeError):
                pass
        if gender == 'male' or any(person['name'] == ref['name'] for person in refs):
            continue
        ref['gender'] = gender
        actor_id = str(actor.get('id') or '')
        if re.fullmatch(r'[A-Za-z0-9]{1,64}', actor_id):
            ref['source_actor_id'] = actor_id
            ref['source_avatar'] = (bool(actor['has_avatar']) if 'has_avatar' in actor
                                    else public_image_url(actor.get('avatar_url')) if 'avatar_url' in actor else True)
        refs.append(ref)
    return refs


def _artwork_fields(contract, code, item, source, *, refresh=False):
    from .jav_cover_fetch import is_javdb_image
    source_cover = (source or {}).get('cover_url') or (source or {}).get('thumb_url')
    return {"has_cover": contract.has_cover(code), "cover_version": contract.cover_version(code),
            "cover_url": discovery_cover_url(item, source or {}),
            "source_image": bool(source_cover and not is_javdb_image(source_cover)),
            "preview_count": min(len(source.get("preview_images") or []), 30),
            **_preview_fields(contract, code, source, refresh=refresh),
            "source_url": "https://javdb.com/v/" + str(source["id"]) if source else item.get("link") or ""}


def _source_fields(source, decisions):
    tags = [str(tag.get("name") or "") if isinstance(tag, dict) else str(tag)
            for tag in source.get("tags", [])]
    tag_refs = []
    for raw in tags:
        label = resolve_genre(raw, decisions)
        if label is None:
            continue
        label = label or raw
        if label and all(ref["name"] != label for ref in tag_refs):
            tag_refs.append({"name": label, "category": tag_cat(label), "source_name": raw})
    return {"duration": source.get("duration"), "score": source.get("score"),
            "summary": source.get("summary") or "", "director": source.get("director_name") or "",
            "publisher": source.get("publisher_name") or "", "tags": [tag for tag in tags if tag],
            "tag_refs": tag_refs,
            "comments_count": int(source.get("comments_count") or 0)}


@_source_cache
def q_jav_reviews(contract, args) -> dict:
    code = detail_code(args)
    page = int(args.get("page") or 1)
    try:
        if args.get('refresh') == '1':
            PUBLIC_API.movie(code, refresh=True)
        rows = PUBLIC_API.reviews(code, page, refresh=True) if args.get('refresh') == '1' else PUBLIC_API.reviews(code, page)
    except PublicSourceUnavailable as error:
        return {"ok": False, "items": [], "more": False, "message": str(error)}
    items = [{"id": str(row.get("id")), "username": str(row.get("username") or "匿名用户"),
              "content": str(row.get("content") or ""), "score": row.get("score"),
              "created_at": str(row.get("created_at") or "")} for row in rows[:PAGE_SIZE] if isinstance(row, dict)]
    return {"ok": True, "items": items, "page": page,
            "more": len(items) == PAGE_SIZE and page < MAX_REVIEW_PAGES,
            "message": ""}


@_source_cache
def q_jav_image(contract, args) -> dict:
    code = detail_code(args)
    index = str(args.get("image") or "cover")
    if (index != "cover" and not re.fullmatch(r"actor:[A-Za-z0-9]{1,64}", index)
            and (not index.isdigit() or not 0 <= int(index) < 30)):
        raise ValueError("预览图不存在")
    try:
        key = f"jav:actor:{index.removeprefix('actor:')}" if index.startswith("actor:") else f"jav:{code}:image:{index}"
        body = cache_for(contract).bytes(key,
            lambda: base64.b64decode(_fetch_image(contract, code, index).partition(',')[2], validate=True),
            ttl=AVATAR_REFRESH_SECONDS if index.startswith("actor:") else None)
        image = "data:image/jpeg;base64," + base64.b64encode(body).decode("ascii")
    except (PublicSourceUnavailable, RemoteMediaUnavailable) as error:
        return {"ok": False, "image": "", "message": str(error)}
    return {"ok": True, "image": image}


def _fetch_image(contract, code, index):
    stored, _ = discovery_metadata(contract).peek(code)
    PUBLIC_API.remember_movie_identity(code, stored or {})
    if index.startswith('actor:'):
        actor = next((person for person in (stored or {}).get('actors', [])
                      if isinstance(person, dict) and str(person.get('id')) == index.removeprefix('actor:')), {})
        if public_image_url(actor.get('avatar_image')):
            return PUBLIC_API.image_from_url(actor['avatar_image'])
    image = PUBLIC_API.image(code, index)
    if index.startswith('actor:'):
        movie = PUBLIC_API.peek_movie(code)
        if movie:
            discovery_metadata(contract).remember(code, movie, checked_at=cache_checked_at('movie:' + code, exact=True))
    return image


def _preview_fields(contract, code, source, *, refresh=False):
    from .jav_official_preview import dmm_preview, javdb_preview, prestige_preview

    prestige = ('prestige' in str(source.get('maker_name') or '').casefold()
                or 'プレステージ' in str(source.get('maker_name') or ''))
    provider = 'prestige' if prestige else 'dmm'
    fetch = (lambda: prestige_preview(code, contract.follow_secrets_root)) if prestige else (
        lambda: dmm_preview(code, contract.follow_secrets_root))
    sources = [(provider, fetch), ('javdb', lambda: javdb_preview(code, source.get('id'), contract.follow_secrets_root))]
    url, errors = '', []
    for provider, fetch in sources:
        try:
            result = PUBLIC_API.official_preview(code, fetch, released=source.get('release_date'),
                                                 refresh=refresh, provider=provider)
            url = result['url']
            failure = cache_error(f'preview:{provider}:' + code)
            if failure and not url:
                raise PublicSourceUnavailable(failure)
        except PublicSourceUnavailable as error:
            errors.append(str(error))
        if url:
            break
    if not url:
        url = preview_video_url(source)
        if url and '?' in url:
            try:
                url = preview_video_url(PUBLIC_API.media_movie(code)) or url
            except PublicSourceUnavailable as error:
                errors.append(str(error))
    if not url:
        return {'preview_video_url': '', 'preview_state': 'unavailable' if errors else 'missing',
                **({'preview_message': '；'.join(dict.fromkeys(errors))} if errors else {})}
    playlist = url.endswith('.m3u8') or '.m3u8?' in url
    ref = PreviewCache(cache_for(contract)).register(code, url, playlist=playlist,
                                                     media_type='' if playlist else 'video/mp4')
    return {'preview_video_url': f"/jav-preview/{code}/{ref}{'.m3u8' if playlist else ''}", 'preview_state': 'ready'}


@_source_cache
def q_jav_resources(contract, args) -> dict:
    """公开资源列表；只返回候选，下载提交由既有入口接受用户选择。"""
    import time
    from .downloads import parse_magnet
    from .sources.javdb import merge_resources, parse_comment_text

    code = detail_code(args)
    try:
        movie = PUBLIC_API.movie(code, refresh=True) if args.get('refresh') == '1' else PUBLIC_API.movie(code)
    except PublicSourceUnavailable as error:
        return {"state": "error", "items": [], "error": str(error), "checked_at": None}
    warnings = []
    magnet_failed = False
    try:
        rows = PUBLIC_API.magnets(code, refresh=args.get('refresh') == '1')
    except PublicSourceUnavailable as error:
        rows = []
        magnet_failed = True
        warnings.append("磁链区资源未取得：" + str(error))
    items = {}
    source_url = "https://javdb.com/v/" + movie["id"]
    for row in rows:
        try:
            magnet = parse_magnet(str(row.get("hash") or ""))
        except ValueError:
            continue
        if magnet.info_hash in items:
            continue
        attributes = [label for key, label in (("cnsub", "中字"), ("hd", "高清")) if row.get(key)]
        items[magnet.info_hash] = {"id": "magnet:" + magnet.info_hash, "protocol": "magnet",
            "info_hash": magnet.info_hash, "uri": magnet.uri, "name": row.get("name") or code,
            "size": str(row.get("size") or ""), "files": str(row.get("files_count") or ""),
            "date": str(row.get("created_at") or ""), "attributes": attributes,
            "source": "JavDB", "source_url": source_url, "origins": ["JavDB"]}
    comments = []
    comments_failed = False
    try:
        for page in range(1, MAX_REVIEW_PAGES + 1):
            reviews = PUBLIC_API.reviews(code, page, refresh=args.get('refresh') == '1')
            for review in reviews:
                origin = source_url + "#review-item-" + str(review.get("id") or "")
                comments.extend(parse_comment_text(str(review.get("content") or ""), origin,
                                                   str(review.get("created_at") or "")))
            if len(reviews) < PAGE_SIZE:
                break
        else:
            warnings.append("已查询前 8 页评论资源。")
    except PublicSourceUnavailable as error:
        comments_failed = True
        warnings.append("评论资源未取得：" + str(error))
    merged = merge_resources([*items.values(), *comments])
    failed = magnet_failed and comments_failed and not merged
    return {"state": "error" if failed else "ready", "items": merged,
            "error": "；".join(warnings) if failed else "", "source_url": source_url,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "warnings": warnings,
            "cache_stale": magnet_failed or comments_failed, "complete": not (magnet_failed or comments_failed)}
