"""Normalize public pixivFANBOX post bodies into Peach media and links.

The supported body shapes follow PixivUtil2 v20251112 (commit ``e537e96``),
BSD-2-Clause.  Peach keeps its own small DTO because the upstream project is a
complete downloader rather than an embeddable parser library.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping

from bs4 import BeautifulSoup


class FanboxContentError(ValueError):
    """A public post used an invalid or unsupported FANBOX body shape."""


@dataclass(frozen=True)
class FanboxContent:
    post_type: str
    summary: str
    links: tuple[str, ...]
    media_items: tuple[dict[str, object], ...]
    image_count: int
    video_count: int
    file_count: int


_SUPPORTED_TYPES = frozenset({"image", "text", "file", "article", "video", "entry"})
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
_IMAGE_SUFFIXES = frozenset({".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"})
#: 封面地址里的裁切尺寸：`.../c/1200x630_90_a2_g5/...`。
_COVER_DIMS_RE = re.compile(r"/c/(\d+)x(\d+)")
_VIDEO_SUFFIXES = frozenset({".m4v", ".mkv", ".mov", ".mp4", ".webm"})


def _https_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return text


def _kind(node: Mapping[str, object], url: str) -> str | None:
    mime = str(node.get("mimeType") or node.get("mimetype") or "").casefold()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    suffix = PurePosixPath(urllib.parse.urlsplit(url).path).suffix.casefold()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _VIDEO_SUFFIXES:
        return "video"
    return None


def _nested_urls(value: object) -> tuple[str, ...]:
    found: list[str] = []

    def add(candidate: object) -> None:
        if isinstance(candidate, Mapping):
            for child in candidate.values():
                add(child)
            return
        if isinstance(candidate, (list, tuple)):
            for child in candidate:
                add(child)
            return
        if not isinstance(candidate, str):
            return
        for matched in _URL_RE.findall(candidate):
            url = _https_url(matched.rstrip(".,;"))
            if url and url not in found:
                found.append(url)

    add(value)
    return tuple(found)


def _embed_url(node: Mapping[str, object]) -> str | None:
    provider = str(node.get("serviceProvider") or "").casefold()
    content_id = str(
        node.get("videoId") or node.get("contentId") or node.get("video_id") or ""
    ).strip()
    if not content_id:
        return None
    if provider == "youtube":
        return f"https://www.youtube.com/watch?v={urllib.parse.quote(content_id)}"
    if provider == "vimeo" and content_id.isdigit():
        return f"https://vimeo.com/{content_id}"
    return None


def _add_cover(post: Mapping[str, object], media: list[dict],
               media_urls: set[str]) -> None:
    """封面追加到媒体清单末尾。

    封面也是这篇的图，收进媒体清单；排在正文之后，卡片缩略图仍然落在正文首图上。
    站点给的是 1200x630 的展示裁切版，不是原画质；裁切尺寸写在地址里，解析出来
    供界面占位。
    """
    cover = _https_url(post.get("coverImageUrl"))
    if not cover or cover in media_urls:
        return
    media_urls.add(cover)
    matched = _COVER_DIMS_RE.search(cover)
    media.append({
        "id": "cover",
        "name": "封面",
        "url": cover,
        "thumb_url": cover,
        "media_kind": "image",
        "size": None,
        "width": int(matched.group(1)) if matched else None,
        "height": int(matched.group(2)) if matched else None,
        "resource_provider": "fanbox",
    })


def normalize_fanbox_post(post: Mapping[str, object]) -> FanboxContent:
    """Return ordered text, links and playable media from one public post."""
    post_type = str(post.get("type") or "image").casefold()
    if post_type not in _SUPPORTED_TYPES:
        raise FanboxContentError(f"FANBOX 正文类型不受支持：{post_type or '空'}")
    body = post.get("body")
    if not isinstance(body, Mapping):
        raise FanboxContentError("FANBOX 公开正文缺少 body")

    text_parts: list[str] = []
    links: list[str] = []
    media: list[dict[str, object]] = []
    media_urls: set[str] = set()
    referenced_images: set[str] = set()
    referenced_files: set[str] = set()
    file_count = 0

    def add_link(value: object) -> None:
        for url in _nested_urls(value):
            if url not in links:
                links.append(url)

    def add_text(value: object) -> None:
        if isinstance(value, str) and value.strip():
            text_parts.append(value.strip())
            add_link(value)

    def add_media(node: object, *, item_id: str, fallback_name: str,
                  force_kind: str | None = None) -> None:
        if not isinstance(node, Mapping):
            return
        url = _https_url(node.get("originalUrl") or node.get("url"))
        if not url or url in media_urls:
            return
        kind = force_kind or _kind(node, url)
        if kind not in {"image", "video"}:
            return
        thumb = _https_url(node.get("thumbnailUrl") or node.get("thumbnail"))
        media_urls.add(url)
        media.append({
            "id": str(node.get("id") or item_id or url),
            "name": str(node.get("name") or fallback_name),
            "url": url,
            "thumb_url": (thumb or url) if kind == "image" else thumb,
            "media_kind": kind,
            "size": node.get("size"),
            # imageMap 自带的固有宽高跟着走：界面靠它在图片落地前占出正确的比例。
            "width": node.get("width"),
            "height": node.get("height"),
            "resource_provider": "fanbox",
        })

    raw_text = body.get("text")
    if isinstance(raw_text, str):
        add_text(raw_text)
    raw_html = body.get("html")
    if isinstance(raw_html, str) and raw_html.strip():
        parsed = BeautifulSoup(raw_html, "html.parser")
        add_text(parsed.get_text("\n", strip=True))
        for anchor in parsed.select("a[href]"):
            add_link(anchor.get("href"))
        for index, image in enumerate(parsed.select("img"), start=1):
            source = image.get("data-src-original") or image.get("src")
            add_media({"originalUrl": source}, item_id=f"html-{index}",
                      fallback_name=f"图片 {len(media) + 1}", force_kind="image")

    image_map = body.get("imageMap") if isinstance(body.get("imageMap"), Mapping) else {}
    file_map = body.get("fileMap") if isinstance(body.get("fileMap"), Mapping) else {}
    embed_map = body.get("embedMap") if isinstance(body.get("embedMap"), Mapping) else {}
    url_embed_map = (
        body.get("urlEmbedMap") if isinstance(body.get("urlEmbedMap"), Mapping) else {}
    )
    blocks = body.get("blocks") if isinstance(body.get("blocks"), list) else []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        block_type = str(block.get("type") or "")
        if block_type in {"p", "header"}:
            add_text(block.get("text"))
            add_link(block.get("links"))
        elif block_type == "image":
            image_id = str(block.get("imageId") or "")
            referenced_images.add(image_id)
            add_media(image_map.get(image_id), item_id=image_id,
                      fallback_name=f"图片 {len(media) + 1}", force_kind="image")
        elif block_type == "file":
            file_id = str(block.get("fileId") or "")
            referenced_files.add(file_id)
            node = file_map.get(file_id)
            if isinstance(node, Mapping):
                file_count += 1
                add_link(node.get("url"))
                add_media(node, item_id=file_id,
                          fallback_name=f"文件 {file_count}")
        elif block_type == "embed":
            node = embed_map.get(str(block.get("embedId") or ""))
            add_link(node)
            if isinstance(node, Mapping):
                add_link(_embed_url(node))
        elif block_type == "url_embed":
            add_link(url_embed_map.get(str(block.get("urlEmbedId") or "")))

    for image_id, node in image_map.items():
        if str(image_id) not in referenced_images:
            add_media(node, item_id=str(image_id),
                      fallback_name=f"图片 {len(media) + 1}", force_kind="image")
    for file_id, node in file_map.items():
        if str(file_id) in referenced_files or not isinstance(node, Mapping):
            continue
        file_count += 1
        add_link(node.get("url"))
        add_media(node, item_id=str(file_id), fallback_name=f"文件 {file_count}")

    body_images = body.get("images") if isinstance(body.get("images"), list) else []
    for index, node in enumerate(body_images, start=1):
        add_media(node, item_id=f"image-{index}",
                  fallback_name=f"图片 {len(media) + 1}", force_kind="image")
    body_files = body.get("files") if isinstance(body.get("files"), list) else []
    for index, node in enumerate(body_files, start=1):
        if not isinstance(node, Mapping):
            continue
        file_count += 1
        add_link(node.get("url"))
        add_media(node, item_id=f"file-{index}", fallback_name=f"文件 {file_count}")

    _add_cover(post, media, media_urls)

    video = body.get("video")
    add_link(video)
    if isinstance(video, Mapping):
        add_link(_embed_url(video))
    add_link(embed_map)
    add_link(url_embed_map)

    image_count = sum(item["media_kind"] == "image" for item in media)
    video_count = sum(item["media_kind"] == "video" for item in media)
    return FanboxContent(
        post_type=post_type,
        summary="\n".join(text_parts),
        links=tuple(links),
        media_items=tuple(media),
        image_count=image_count,
        video_count=video_count,
        file_count=file_count,
    )
