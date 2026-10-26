"""个人实体收藏的规范身份与只读快照。"""
from .entities import resolve_entity, resolve_entity_id
from .profiles import read_settings

KEY = "libraryShortcuts"
KINDS = frozenset({"performer", "creator", "studio", "series", "agency", "tag"})


def favorite_entry(connection, entry):
    """将实体或标签选择解析为当前规范实体。"""
    if not isinstance(entry, dict):
        return None
    if entry.get("kind") == "tag" and not entry.get("entity_id"):
        tag = entry.get("name")
        if isinstance(tag, str) and 0 < len(tag.strip()) <= 120:
            entity = resolve_entity(connection, "tag", tag.strip())
            if entity:
                return favorite_entry(connection, {"entity_id": entity["id"]})
        return None
    value = entry.get("entity_id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    resolved = resolve_entity_id(connection, value)
    row = connection.execute("SELECT id,kind,canonical_name FROM entity WHERE id=?", (resolved,)).fetchone() if resolved else None
    if row is None or row[1] not in KINDS:
        return None
    return {"key": "entity:" + str(row[0]), "entity_id": row[0], "kind": row[1], "name": row[2]}


def favorite_entities(connection, settings=None):
    """读取有界收藏清单并折叠规范身份相同的条目。"""
    settings = read_settings(connection) if settings is None else settings
    stored = settings.get(KEY)
    stored = stored if isinstance(stored, dict) else {}
    raw = stored.get("favorites", [])
    favorites = {}
    for item in (raw if isinstance(raw, list) else [])[:1000]:
        current = favorite_entry(connection, item)
        if current:
            favorites[current["key"]] = current
    return list(favorites.values())
