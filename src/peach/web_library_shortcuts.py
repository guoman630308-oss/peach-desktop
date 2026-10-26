"""收藏与命名筛选；偏好保存在当前 profile，同一事务内按动作修改。"""
from __future__ import annotations

import json
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit

from .catalog_rules import solo_performer_clause
from .library_favorites import KEY, favorite_entry, favorite_entities
from .profiles import DEFAULT_PROFILE_ID, read_settings
from .web_catalog import VISIBLE_CATALOG_ASSET, entity_ref

QUERY_KEYS = frozenset({"loc", "creator", "studio", "owner", "tag", "tag_match", "len",
                        "dur_min", "dur_max", "orient", "region", "sort", "dir", "q", "jav", "thumb", "state",
                        "browse", "folder", "folder_recursive"})
PATHS = frozenset({"/", "/unseen", "/watch-later", "/flagged"})


def _snapshot(c, settings):
    stored = settings.get(KEY)
    stored = stored if isinstance(stored, dict) else {}
    favorites = favorite_entities(c, settings)
    raw_filters = stored.get("filters", [])
    filters = []
    for item in (raw_filters if isinstance(raw_filters, list) else [])[:100]:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("id", "name", "href")):
            continue
        try:
            href = clean_href(item["href"])
        except ValueError:
            continue
        filters.append({"id": item["id"][:80], "name": item["name"][:80], "href": href})
    return {"favorites": favorites, "filters": filters}


def clean_href(raw):
    if not isinstance(raw, str) or len(raw) > 4096:
        raise ValueError("筛选地址过长或格式不正确")
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path not in PATHS:
        raise ValueError("只能保存馆藏筛选")
    pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=40)
    if any(key not in QUERY_KEYS or len(value) > 1000 for key, value in pairs):
        raise ValueError("筛选包含不支持的条件")
    query = urlencode(dict(pairs))
    return parsed.path + ("?" + query if query else "")


def q_library_shortcuts(contract, _args=None):
    with contract.read_connection() as c:
        data = _snapshot(c, read_settings(c))
        _attach_art(contract, c, data["favorites"])
        return data


def _attach_art(contract, connection, rows):
    """收藏复用实体图、厂牌标识与可取得的代表作头像。"""
    if not rows:
        return
    ids = [row["entity_id"] for row in rows]
    marks = ','.join('?' for _ in ids)
    representatives = {row[0]: (row[1], row[2]) for row in connection.execute(
        "SELECT entity_id,id,snapshot_path FROM (SELECT ae.entity_id,a.id,a.snapshot_path,"
        "ROW_NUMBER() OVER (PARTITION BY ae.entity_id ORDER BY a.size DESC,a.id) rank "
        "FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id JOIN entity e ON e.id=ae.entity_id "
        f"WHERE ae.entity_id IN ({marks}) AND {VISIBLE_CATALOG_ASSET} "
        "AND a.snapshot_path IS NOT NULL AND (e.kind<>'performer' OR (" +
        solo_performer_clause('a.id', 'ae.entity_id') + ")) ) WHERE rank=1", ids)}
    for row in rows:
        _favorite_art(contract, row, representatives.get(row['entity_id']))


def _favorite_art(contract, row, representative):
    ref = entity_ref(contract, row['kind'], row['entity_id'], row['name'])
    row['image_url'], row['image_label'] = '', row['name']
    if row['kind'] == 'studio' and contract.has_logo(row['name']):
        row['image_url'] = '/logo?' + urlencode({'studio': row['name'], 'variant': 'icon', 'v': contract.logo_version(row['name'])})
    elif ref['has_image']:
        row['image_url'] = '/entity-image?' + urlencode({'kind': row['kind'], 'id': row['entity_id'], 'thumb': 1, 'v': ref.get('image_version', '')})
    elif ref.get('source_avatar_url'):
        row['image_url'] = ref['source_avatar_url']
        row['image_fallbacks'] = ref.get('source_avatar_fallbacks', [])
    elif row['kind'] not in {'studio', 'agency'} and representative and contract.has_avatar(*representative):
        row['image_url'] = f'/avatar?id={representative[0]}'
        row['image_label'] = row['name'] + ' · 代表作'


def favorite_ids(connection):
    return {row["entity_id"] for row in favorite_entities(connection)}


def w_library_shortcuts(contract, body):
    action = body.get("action")
    with contract.write_transaction() as c:
        settings = read_settings(c)
        data = _snapshot(c, settings)
        if action == "favorite":
            item = favorite_entry(c, body)
            if item is None or not isinstance(body.get("on"), bool):
                raise ValueError("收藏对象不存在或状态不正确")
            data["favorites"] = [row for row in data["favorites"] if row["key"] != item["key"]]
            if body["on"]:
                if len(data["favorites"]) >= 1000:
                    raise ValueError("最多收藏 1000 项")
                data["favorites"].append(item)
        elif action == "save_filter":
            name = body.get("name")
            if not isinstance(name, str) or not 0 < len(name.strip()) <= 80:
                raise ValueError("筛选名称须为 1 到 80 个字符")
            if len(data["filters"]) >= 100:
                raise ValueError("最多保存 100 个筛选")
            data["filters"].append({"id": uuid.uuid4().hex, "name": name.strip(), "href": clean_href(body.get("href"))})
        elif action == "delete_filter":
            data["filters"] = [row for row in data["filters"] if row["id"] != body.get("id")]
        else:
            raise ValueError("不支持的收藏操作")
        settings[KEY] = data
        updated = c.execute("UPDATE profile SET settings_json=?,updated_at=? WHERE id=?",
                            (json.dumps(settings, ensure_ascii=False), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), DEFAULT_PROFILE_ID)).rowcount
        if not updated:
            raise ValueError("当前用户设置不存在")
    return {"ok": True, **q_library_shortcuts(contract)}
