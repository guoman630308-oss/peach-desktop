"""创作者头像候选：已绑定来源的社媒头像、作品封面与正文图片。"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from .avatar_picker import Choice, MAX_ASSET_CHOICES, MAX_IMAGE_BYTES, PickerError, accept_image
from .avatar_provider import inspect_avatar
from .follow_avatar import profile_identities
from .follow_identity import official_avatar_url
from .follow_store import FollowItemRow, FollowStore
from . import images


@dataclass(frozen=True)
class MediaChoices:
    """调用方确认可见的封面与图片序号；None 序号表示单图原图。"""
    cover: str | None
    images: tuple[int | None, ...]


MediaProjection = Callable[[FollowItemRow], MediaChoices]


def _profile(url: str) -> dict:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").removeprefix("www.")
    path = parsed.path.strip("/").split("/")
    service = {"x.com": "twitter", "twitter.com": "twitter", "patreon.com": "patreon",
               "pixiv.net": "pixiv"}.get(host)
    handle = path[-1] if service == "pixiv" and path[0] == "users" else path[0]
    identity = f"{service}:{handle}"
    if service and profile_identities(identity):
        return {"service": "profile", "id": identity}
    if host.endswith(".fanbox.cc"):
        return {"service": "fanbox", "id": host.removesuffix(".fanbox.cc")}
    return {}


def choices(connection: sqlite3.Connection, entity_id: int, *, project: MediaProjection) -> list[Choice]:
    """列举只读本地元数据；图片仅在候选可见或用户进入框选时取。"""
    store = FollowStore(lambda: connection)
    result: list[Choice] = []
    seen: set[str] = set()
    for link_id, url, label in connection.execute(
            "SELECT id,url,label FROM entity_link WHERE entity_id=? AND link_kind IN ('social','official')",
            (entity_id,)):
        if _profile(url):
            result.append(Choice(ref=f"follow-link:{link_id}", source="social",
                                 label=f"{label or urlsplit(url).hostname} · 社媒头像"))
    for source in store.sources():
        if source["entity_id"] != entity_id:
            continue
        avatar = official_avatar_url(source)
        if avatar and avatar not in seen:
            seen.add(avatar)
            result.append(Choice(ref=f"follow-avatar:{source['id']}", source="social",
                                 label=f"{source['label']} · 社媒头像"))
    item_ids = connection.execute(
        "SELECT i.id FROM follow_item i JOIN follow_source s ON s.id=i.source_id"
        " WHERE s.entity_id=? ORDER BY coalesce(i.published_at,i.first_seen_at) DESC,i.id DESC LIMIT ?",
        (entity_id, MAX_ASSET_CHOICES)).fetchall()
    for (item_id,) in item_ids:
        item = store.item(item_id)
        if item is None:
            continue
        projected = project(item)
        bases = []
        if projected.cover:
            bases.append(f"follow:{item.id}:cover")
        bases += [f"follow:{item.id}:image{index if index is not None else ''}"
                  for index in projected.images[:9]]
        if bases:
            result.append(Choice(ref=bases[0], source="online", label=item.title,
                                 crop=True, bases=tuple(bases)))
    return result


def resolve(ref: str, connection: sqlite3.Connection, entity_id: int, *, project: MediaProjection,
            image, avatar, media) -> tuple[bytes, dict]:
    """每次取图重新核对归属与可见媒体，不能借别人的条目或隐藏附件换头像。"""
    listed = choices(connection, entity_id, project=project)
    allowed = {base for choice in listed for base in (choice.bases or (choice.ref,))}
    if ref not in allowed:
        raise PickerError("这张备选图片不属于当前创作者")
    if ref.startswith("follow-link:"):
        row = connection.execute("SELECT url FROM entity_link WHERE id=? AND entity_id=?",
                                 (int(ref.split(":")[1]), entity_id)).fetchone()
        body = avatar(_profile(row[0]))
        provider = "social-web"
    elif ref.startswith("follow-avatar:"):
        source_id = int(ref.split(":")[1])
        source = next(row for row in FollowStore(lambda: connection).sources() if row["id"] == source_id)
        params = {key: values[0] for key, values in parse_qs(urlsplit(official_avatar_url(source)).query).items()}
        body = avatar(params)
        provider = "social-web"
    else:
        _, item_id, base = ref.split(":")
        item = FollowStore(lambda: connection).item(int(item_id))
        if base == "cover":
            body = image(item, project(item).cover)
        else:
            index = int(base[5:]) if base[5:] else None
            body = media(item, index)
        provider = "follow-content"
    if not body or len(body) > MAX_IMAGE_BYTES:
        raise PickerError("这张图未取得或太大了")
    if not inspect_avatar(body):
        size = images.measure_image_size(body)
        body = images.crop_to_box(body, (0, 0, *size)) if size else None
    if not body:
        raise PickerError("这不是一张能识别的图片")
    accept_image(body)
    return body, {"source": "avatar picker", "provider": provider, "external_id": ref}
