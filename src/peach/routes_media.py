"""媒体字节的出口：播放、分片、字幕、缩图、封面、头像与外链圆标。

这一层只做取路径与拼响应头，不做媒体判断——能不能播、走 Range 还是 HLS、要不要
转码，全在 `media`／`transcodes`／`segments` 里。三个媒体异常（`MediaNotFound`、
`MediaOffline`、`MediaUnavailable`）由 `api.py` 的异常处理器统一收口，路由里不要
再手抄同一组 try/except。

有一条贯穿整层的规矩：**上游地址一律不外露**。`/follow-stream`、`/follow-cover`、
`/link-mark` 都只接受账本里的 id，自己去查地址；接受前端递过来的 URL 就等于开了一个
任意地址抓取的口子。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from functools import partial
from dataclasses import replace
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import (
    FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
)
from starlette.staticfiles import StaticFiles

from . import (
    avatar_face, avatar_online, avatar_picker, avatar_provider, follow_assets, follow_providers, images,
    jav_poster_crop, link_marks, link_status, scraping_access, site_icons, social_links, subtitles,
    taste_history, timeline_sheets, web_follow,
)
from .config import GENERATED_DIR, PROJECT_ROOT
from .entities import resolve_entity_id
from .follow import FollowSourceError
from .follow_avatar import profile_avatar_tiers, profile_identities, resolve_official_avatar
from .follow_covers import (
    PLACEHOLDER_CONTENT_TYPE, PLACEHOLDER_IMAGE, FollowCoverUnavailable,
)
from .follow_store import FollowStore
from .follow_stream import (
    FollowMediaUnavailable, FollowProxyError, ResolvedFollowMedia, open_upstream,
)
from .media import MediaUnavailable, normalized_path
from .platform import within_root
from .previews import ENTITY_THUMB_TYPE, PreviewUnavailable, entity_image_key
from .remote_media_cache import AVATAR_REFRESH_SECONDS, FAVICON_REFRESH_SECONDS, cache_for
from .preview_cache import PreviewCache
from .routes_auth import require_auth, require_loopback_client
from .segments import SegmentCancelled, SegmentUnavailable, build_hls_playlist
from .streaming import BufferedFileResponse, CancellableFileResponse, SplicedMp4Response
from .transcodes import TranscodeCancelled, TranscodeUnavailable

router = APIRouter()

#: 生成物的缓存时长。这些端点按 asset / follow_item id 取图：内容换了 id 也就换了
#: （封面重生成写的是新文件），所以一年也不嫌长。
#: 不加 immutable——那会让浏览器连刷新都不再回源，真要换图就只能干等过期。
MEDIA_CACHE_SECONDS = 365 * 24 * 3600

#: 头像单独短一档：id 不变但人会换头像，作者换得还挺勤。
AVATAR_CACHE_SECONDS = AVATAR_REFRESH_SECONDS


def _asset_root(state) -> Path:
    return Path(state.settings.candidate_root) / follow_assets.ROOT_NAME


def _asset_response(request: Request, path: Path | None):
    """这些端点只出现在 `<img src>` 里，所以取不到时回的是占位图而不是 JSON：

    错误正文对 `<img>` 毫无意义，只会留下一个碎图。状态码仍然是 404，
    前端据此把 `<img>` 摘掉、换成站名或首字母。
    """
    if path is None:
        return Response(PLACEHOLDER_IMAGE, status_code=404,
                        media_type=PLACEHOLDER_CONTENT_TYPE,
                        headers={"cache-control": "no-store"})
    return _image_response(request, path, media_type=follow_assets.content_type(path))

#: 取图标时报浏览器 UA。CDN 上的图标资产（p-smith、static.cdninstagram）对
#: 机器人 UA 会直接 403，而这只是一次公开静态文件请求，没有伪装成用户的意思。
from .user_agent import USER_AGENT

#: 文件名里不能出现的字符，按 Windows 的最严口径取——落盘的那台多半是它。
_UNSAFE_FILENAME = re.compile('[\\/:*?"<>|]+|[\x00-\x1f]+')

#: FANBOX 官方身份的两种写法：pixiv 数字 user id，或 FANBOX 创作者 id。
_FANBOX_IDENTITY_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")

LOGGER = logging.getLogger(__name__)


def _image_response(request: Request, path: Path, media_type: str | None = None):
    """固定图片 URL 使用私有缓存，并按文件 ETag 复验。"""
    response = FileResponse(path, media_type=media_type, stat_result=path.stat())
    response.headers["Cache-Control"] = "private, no-cache"
    if StaticFiles().is_not_modified(response.headers, request.headers):
        return Response(status_code=304, headers={
            "ETag": response.headers["etag"],
            "Cache-Control": response.headers["cache-control"],
        })
    return response


def _attachment_disposition(title: str, url: str) -> str:
    """按条目标题构造下载文件名，扩展名沿用上游地址的后缀。

    不回传上游地址：主机名和签名同样是不该外露的东西，和 `/follow-stream`
    整体的边界一致。`filename*` 用 RFC 5987 编码，标题里的中文和日文才落得下来。
    """
    stem = _UNSAFE_FILENAME.sub(" ", str(title or "")).strip() or "peach-media"
    stem = " ".join(stem.split())[:120]
    suffix = Path(urlsplit(str(url or "")).path).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{2,5}", suffix):
        suffix = ".mp4"
    name = f"{stem}{suffix}"
    # 标题整条都是中日文时，ascii 回退会只剩空格和标点，浏览器落下来是个没名字的
    # 文件。回退名必须自己站得住，不能是「把非 ASCII 删掉之后剩下的」。
    ascii_stem = "".join(ch for ch in stem if ch.isascii() and (ch.isalnum() or ch in " -_[]().")).strip()
    ascii_name = f"{ascii_stem}{suffix}" if ascii_stem else f"peach-media{suffix}"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def _repaired_header(state, asset_id: int, source: Path):
    """已经修好的 MP4 头；没有就是 None。只查不算，算是 `_needs_conversion` 的事。"""
    store = getattr(state, "header_repairs", None)
    return None if store is None else store.lookup(asset_id, source)


def _needs_conversion(state, asset_id: int, source: Path, session: str) -> bool:
    """起播时要不要转码。只有 `/api/stream-plan` 问这一句。

    缺 ctts 的片子只是时间戳错乱，重建一份头就能直接按 Range 播，不必重编码。头还没
    算出来时先照旧走 HLS，同时在后台补上；补好之后同一部片下次起播就走 Range。

    HLS 那两个端点不问：一次播放的分片计划要在整段会话里保持同一个形状，边车中途落地
    会让固定 6 秒的计划换成按关键帧切的，剩下的分片索引全对不上。
    """
    if not state.transcode_service.requires_conversion(
            source, session=session, registry=state.stream_sessions):
        return False
    store = getattr(state, "header_repairs", None)
    if store is None or not state.transcode_service.decode_order_timestamps(
            source, session=session, registry=state.stream_sessions):
        return True
    if store.lookup(asset_id, source) is not None:
        return False
    store.request(asset_id, source)
    return True


def _hls_plan(state, asset_id: int, session: str = "", conversion: bool | None = None):
    """解析 HLS 的片源路径与分片计划；任何一步不成立就返回 None 走 Range。

    要重编码的片源按固定 6 秒切，时长先看账本，账本没记或记成负数就用 ffprobe 报的；
    探测也拿不到才放弃，否则浏览器只能退回 `/stream`，那条路要先把整部片转完才出第一个字节。
    原样封装的 HLS 仍要账本时长与关键帧表。

    `conversion` 是起播时已经得出的结论。给不出时按片源本身判：播放列表和分片端点走的
    就是这条，它们不看重建好的 MP4 头，理由见 `_needs_conversion`。
    """
    asset = state.media_engine.asset(asset_id)
    # 播放列表和分片端点本身就是 HLS 路径，按 ADR-0016 显式要计划，不受默认值影响。
    choice = state.media_engine.stream_plan(asset_id, mode="hls")
    source = state.media_engine.filesystem.file_for(asset, thumbnail=False)
    duration = asset.duration if asset.duration and asset.duration > 0 else 0.0
    if conversion if conversion is not None else state.transcode_service.requires_conversion(
            source, session=session, registry=state.stream_sessions):
        duration = duration or state.transcode_service.media_duration(
            source, session=session, registry=state.stream_sessions)
        plan = state.hls_service.conversion_plan(duration) if duration > 0 else []
        return (asset, source, plan, True) if plan else None
    if choice.protocol != "hls" or duration <= 0:
        return None
    plan = state.hls_service.plan(source, duration)
    return None if not plan else (asset, source, plan, False)


@router.post("/api/external-playback")
def external_playback(request: Request, id: int, args: dict[str, str] = Depends(require_auth)):
    from .external_playback import TTL_SECONDS, issue
    from .routes_auth import _named_origin, _origin_key, _tunnel_origin
    from fastapi import HTTPException
    state = request.app.state
    asset = state.media_engine.asset(id)
    state.media_engine.filesystem.file_for(asset, thumbnail=False)
    supplied = request.headers.get('origin')
    raw = supplied or str(request.base_url)
    parsed = urlsplit(raw)
    if any(ord(char) <= 32 or ord(char) == 127 for char in raw) or parsed.username or parsed.password:
        raise HTTPException(400, '播放地址无效')
    key = _origin_key(raw) if supplied else (_tunnel_origin(request) or _named_origin(request) or _origin_key(raw))
    if key is None:
        raise HTTPException(400, '播放地址无效')
    scheme, host, port = key
    host = f'[{host}]' if ':' in host else host
    origin = f'{scheme}://{host}' + (f':{port}' if port is not None else '')
    ticket = issue(state.external_playback_key, id, origin=origin)
    query = f"ticket={ticket}&origin={quote(origin, safe='')}"
    path = f"/external-stream/{id}?{query}"
    playlist = f"/external-playlist/{id}.m3u?{query}"
    return JSONResponse({"url": path, "playlist": playlist, "expires_in": TTL_SECONDS},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/local-player/play")
def launch_local_player(request: Request, body: dict = Body(default_factory=dict),
                        _args=Depends(require_auth)):
    from . import local_player, strm
    from .external_playback import issue
    require_loopback_client(request)
    state = request.app.state
    kind, item_id, media = body.get("kind"), body.get("id"), body.get("media")
    if not isinstance(kind, str) or kind not in {"item", "follow"} or type(item_id) is not int or item_id < 1:
        raise HTTPException(400, "播放条目无效")
    if media is not None and (type(media) is not int or media < 0):
        raise HTTPException(400, "媒体序号无效")
    try:
        preference = local_player.read(state.settings.follow_state_root)
        if not preference["available"]:
            raise HTTPException(501, "本机播放器启动仅适用于 Windows")
        if kind == "item":
            asset = state.media_engine.asset(item_id)
            if asset.location == "online":
                with state.database.read_connection() as connection:
                    follow = connection.execute("SELECT id FROM follow_item WHERE asset_id=? ORDER BY id LIMIT 1",
                                                (item_id,)).fetchone()
                if not follow:
                    raise HTTPException(404, "这条作品没有可播放的视频")
                kind, item_id = "follow", int(follow[0])
            else:
                source = state.media_engine.filesystem.file_for(asset)
                with state.database.read_connection() as connection:
                    row = connection.execute("SELECT medium FROM asset WHERE id=?", (item_id,)).fetchone()
                if not row or row[0] != "video":
                    raise HTTPException(400, "这条作品没有可播放的视频")
                if strm.is_pointer(source):
                    links = json.loads(external_playback(request, item_id, {}).body)
                    source = str(request.base_url).rstrip("/") + links["url"]
        if kind == "follow":
            with state.database.read_connection() as connection:
                item = FollowStore(lambda: connection).item(item_id)
            if item is None:
                raise HTTPException(404, "关注条目不存在")
            subject = f"follow:{item_id}:{media}"
            ticket = issue(state.external_playback_key, subject)
            source = (str(request.base_url).rstrip("/") + f"/external-follow-stream/{item_id}"
                      + f"?ticket={ticket}" + (f"&media={media}" if media is not None else ""))
        local_player.launch(preference, source)
        return {"opened": True, "player": preference["label"]}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.api_route("/external-follow-stream/{id}", methods=["GET", "HEAD"])
def external_follow_stream(request: Request, id: int, ticket: str, media: int | None = None):
    from .external_playback import valid
    if not valid(request.app.state.external_playback_key, f"follow:{id}:{media}", ticket):
        raise HTTPException(401, "播放链接已失效，请重新打开外部播放器")
    return follow_stream(request, id, media=media, args={})


def _external_asset(request: Request, asset_id: int, ticket: str, origin: str):
    from .external_playback import valid
    from fastapi import HTTPException
    state = request.app.state
    if not origin or not valid(state.external_playback_key, asset_id, ticket, origin=origin):
        raise HTTPException(401, "播放链接已失效，请在作品详情重新打开")
    asset = state.media_engine.asset(asset_id)
    return state.media_engine.filesystem.file_for(asset, thumbnail=False)


@router.api_route("/external-stream/{id}", methods=["GET", "HEAD"])
def external_stream(request: Request, id: int, ticket: str, origin: str = ''):
    path = _external_asset(request, id, ticket, origin)
    from . import strm, strm_stream
    if strm.is_pointer(path):
        return strm_stream.proxy(request, id, path)
    return BufferedFileResponse(path, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/external-playlist/{id}.m3u")
def external_playlist(request: Request, id: int, ticket: str, origin: str = ''):
    _external_asset(request, id, ticket, origin)
    url = origin + f"/external-stream/{id}?ticket={ticket}&origin={quote(origin, safe='')}"
    return PlainTextResponse(f"#EXTM3U\n#EXTINF:-1,Peach {id}\n{url}\n", media_type="audio/x-mpegurl",
                             headers={"Content-Disposition": f'attachment; filename="peach-{id}.m3u"',
                                      "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.api_route("/stream", methods=["GET", "HEAD"])
def stream(request: Request, id: int, session: str = "", args: dict[str, str] = Depends(require_auth)):
    state = request.app.state
    asset = state.media_engine.asset(id)
    path = state.media_engine.filesystem.file_for(asset, thumbnail=False)
    from . import strm, strm_stream
    if strm.is_pointer(path):
        return strm_stream.proxy(request, id, path, session)
    if path.suffix.lower() in strm.DISC_SUFFIXES:
        return JSONResponse({'error': 'ISO 原盘请使用作品详情中的外部播放器打开'}, status_code=415)
    header = _repaired_header(state, id, path)
    if header is not None:
        return SplicedMp4Response(
            path, header, session=session, registry=state.stream_sessions,
        )
    try:
        path, transcoded = state.transcode_service.browser_path(
            id, path, session=session, registry=state.stream_sessions,
        )
    except TranscodeCancelled:
        return Response(status_code=410, headers={"Cache-Control": "no-store"})
    except TranscodeUnavailable:
        LOGGER.exception("browser transcode failed for asset %s", id)
        return JSONResponse({"error": "transcode unavailable"}, status_code=503)
    media_type = "video/mp4" if transcoded else None
    response = (
        CancellableFileResponse(
            path, session=session, registry=state.stream_sessions, media_type=media_type,
        )
        if session else BufferedFileResponse(path, media_type=media_type)
    )
    if transcoded:
        response.headers["X-Peach-Transcoded"] = "1"
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/stream-plan")
def stream_plan(request: Request, id: int, session: str = "", mode: str = "", args: dict[str, str] = Depends(require_auth)):
    state = request.app.state
    if session and len(session) > 128:
        return JSONResponse({"error": "invalid session"}, status_code=400)
    if session and state.stream_sessions.is_cancelled(session):
        return JSONResponse({"error": "stream cancelled"}, status_code=410)
    asset = state.media_engine.asset(id)
    plan = state.media_engine.stream_plan(id, mode=mode or "auto")
    # 只有真的能读出关键帧才宣告 HLS，否则客户端会拿到一个必然 404 的播放列表。
    source_path = state.media_engine.filesystem.file_for(asset, thumbnail=False)
    from . import strm, strm_stream
    if strm.is_pointer(source_path):
        try:
            target = strm.read_target(source_path)
        except ValueError as error:
            return JSONResponse({'error': str(error)}, status_code=400)
        mime = strm_stream.mime_type(target)
        return {'id': id, 'protocol': 'hls' if mime in strm_stream.PLAYLIST_TYPES else 'range',
                'mime_type': mime, 'duration': asset.duration,
                'src': f'/stream?id={id}&session={quote(session, safe="")}', 'reason': 'strm-pointer'}
    if source_path.suffix.lower() in strm.DISC_SUFFIXES:
        return JSONResponse({'error': 'ISO 原盘请使用作品详情中的外部播放器打开'}, status_code=415)
    conversion = _needs_conversion(state, id, source_path, session)
    resolved = (_hls_plan(state, id, session, conversion)
                if conversion or plan.protocol == "hls" else None)
    if resolved and session:
        return {
            "id": id,
            "protocol": "hls",
            "mime_type": "application/vnd.apple.mpegurl",
            "duration": round(sum(length for _, length in resolved[2]), 3),
            "segment_seconds": plan.segment_seconds,
            "segments": len(resolved[2]),
            "src": f"/stream/hls/{id}/index.m3u8?session={quote(session, safe='')}",
            "reason": plan.reason,
        }
    source = f"/stream?id={id}"
    if session:
        source += f"&session={quote(session, safe='')}"
    return {
        "id": id,
        "protocol": "range",
        "mime_type": plan.mime_type,
        "duration": asset.duration,
        "src": source,
        "reason": plan.reason,
    }


@router.api_route('/strm-resource/{id}/{key}', methods=['GET', 'HEAD'])
def strm_resource(request: Request, id: int, key: str, session: str = '',
                  ticket: str = '', origin: str = ''):
    from . import strm, strm_stream
    if ticket:
        path = _external_asset(request, id, ticket, origin)
    else:
        require_auth(request)
        path = request.app.state.media_engine.file_for(id)
    if not strm.is_pointer(path):
        return Response(status_code=404)
    return strm_stream.proxy(request, id, path, session, key)


@router.get("/stream/hls/{id}/index.m3u8")
def hls_playlist(request: Request, id: int, session: str = "", args: dict[str, str] = Depends(require_auth)):
    state = request.app.state
    # 分片必须带 session 才能被取消，播放列表这层就要求它；
    # 否则会生成一份每个分片都必然 400 的目录。
    if not session or len(session) > 128:
        return PlainTextResponse("session required", status_code=400)
    if state.stream_sessions.is_cancelled(session):
        return PlainTextResponse("stream cancelled", status_code=410)
    try:
        resolved = _hls_plan(state, id, session)
    except (MediaUnavailable, TranscodeUnavailable):
        return PlainTextResponse("hls unavailable", status_code=404)
    if resolved is None:
        return PlainTextResponse("hls unavailable", status_code=404)
    query = f"?session={quote(session, safe='')}"
    playlist = build_hls_playlist(
        resolved[2], lambda index: f"/stream/hls/{id}/{index}.ts{query}",
    )
    response = PlainTextResponse(playlist, media_type="application/vnd.apple.mpegurl")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/stream/hls/{id}/{index}.ts")
async def hls_segment(request: Request, id: int, index: int, session: str = "", args: dict[str, str] = Depends(require_auth)):
    state = request.app.state
    if not session or len(session) > 128:
        return JSONResponse({"error": "invalid session"}, status_code=400)
    if state.stream_sessions.is_cancelled(session):
        return Response(status_code=410, headers={"Cache-Control": "no-store"})
    try:
        loop = asyncio.get_running_loop()
        resolved = await loop.run_in_executor(
            state.hls_plan_executor, partial(_hls_plan, state, id, session),
        )
        if resolved is None:
            return JSONResponse({"error": "hls unavailable"}, status_code=404)
        _, source, plan, conversion = resolved
        if index < 0 or index >= len(plan):
            return JSONResponse({"error": "invalid segment"}, status_code=416)
        start, duration = plan[index]
        path = await state.hls_service.generate(
            source, start, duration, asset_id=id, index=index,
            session=session, registry=state.stream_sessions,
            **({"transcode": True} if conversion else {}),
        )
    except SegmentCancelled:
        return Response(status_code=410, headers={"Cache-Control": "no-store"})
    except TranscodeCancelled:
        return Response(status_code=410, headers={"Cache-Control": "no-store"})
    except TranscodeUnavailable:
        LOGGER.exception("browser transcode failed for asset %s", id)
        return JSONResponse({"error": "transcode unavailable"}, status_code=503)
    except SegmentUnavailable:
        LOGGER.exception("HLS segment failed for asset %s", id)
        return JSONResponse({"error": "segment unavailable"}, status_code=503)
    # 片段现在留在缓存里而不是随响应删除：回放、重连和多设备都会重复请求同一段，
    # 每次重跑 FFmpeg 等于让 CloudDrive 再预取一次块。
    response = FileResponse(path, media_type="video/mp2t")
    response.headers["Cache-Control"] = f"private, max-age={MEDIA_CACHE_SECONDS}"
    response.headers["X-Peach-HLS-Segment"] = "1"
    return response


@router.post("/api/stream-cancel")
async def stream_cancel(request: Request, session: str, args: dict[str, str] = Depends(require_auth)):
    if not session or len(session) > 128:
        return JSONResponse({"error": "invalid session"}, status_code=400)
    cancelled = request.app.state.stream_sessions.cancel(session)
    return JSONResponse({"ok": True, "cancelled": cancelled})


#: 单份字幕的读取上限。两小时的片子字幕撑死几百 KB；比这更大的多半不是字幕，
#: 整份读进内存的代价不该由播放器那一次请求承担。
SUBTITLE_BYTE_LIMIT = 4 * 1024 * 1024


def _subtitle_rows(state, asset_id: int):
    """资产本身加它的字幕行。资产不存在时抛 `MediaNotFound`，由 `api.py` 收口。"""
    asset = state.media_engine.asset(asset_id)
    with state.database.read_connection() as connection:
        return asset, subtitles.subtitles_for(connection, asset_id)


def _subtitle_path(state, asset, row) -> Path | None:
    """字幕文件在本机的位置；逃出正片所在目录或授权根一律拒绝。

    路径来自账本而不是前端，这一层仍然要查：`asset_subtitle.path` 由扫描写入，
    而扫描根本身可以被改设置改掉，判据不能寄托在「写进去的那一刻是对的」。
    """
    if not asset.path:
        return None
    directory = normalized_path(asset.path).parent
    path = normalized_path(row["path"])
    if str(path.parent).casefold() != str(directory).casefold():
        return None
    if not any(within_root(path, root)
               for root in state.media_engine.filesystem.allowed_roots):
        return None
    return path


def _subtitle_bytes(state, asset, row) -> tuple[bytes | None, str]:
    """字幕文件的内容，以及取不到时的原因。原因直接进列表给人看。"""
    path = _subtitle_path(state, asset, row)
    if path is None:
        return None, "不在正片所在目录"
    try:
        if path.stat().st_size > SUBTITLE_BYTE_LIMIT:
            return None, "文件过大"
        return path.read_bytes(), ""
    except OSError:
        return None, "文件未找到"


def _subtitle_note(state, asset, row) -> str:
    """这一条为什么不能播；能播返回空串。"""
    if not subtitles.is_playable_format(row["format"]):
        return "图形字幕，播放器里不显示"
    data, note = _subtitle_bytes(state, asset, row)
    if data is None:
        return note
    try:
        subtitles.decode(data)
    except subtitles.SubtitleUndecodable:
        return "编码未识别"
    return ""


@router.get("/api/assets/{id}/subtitles")
def asset_subtitles(request: Request, id: int, args: dict[str, str] = Depends(require_auth)):
    """这个资产有哪些字幕轨，以及每一条能不能播。

    序号就是这张表里的位置，`/api/assets/{id}/subtitles/{n}` 按同一个顺序取。
    不能播的也列出来并说明原因：界面上「有三条字幕、只挂上两条」要能解释得清。
    """
    state = request.app.state
    asset, rows = _subtitle_rows(state, id)
    tracks = []
    for index, row in enumerate(rows):
        note = _subtitle_note(state, asset, row)
        tracks.append({
            "index": index,
            "name": row["name"],
            "format": row["format"],
            "language": row["language"],
            "label": subtitles.track_label(row["name"], row["language"], asset.name),
            "pairing": row["pairing"],
            "note": note,
            "playable": not note,
            "src": f"/api/assets/{id}/subtitles/{index}",
        })
    return {"id": id, "subtitles": tracks}


@router.api_route("/api/assets/{id}/subtitles/{index}", methods=["GET", "HEAD"])
def asset_subtitle_track(request: Request, id: int, index: int,
                         args: dict[str, str] = Depends(require_auth)):
    """一条字幕的 WebVTT。浏览器的 text track 只认这一种格式。"""
    state = request.app.state
    asset, rows = _subtitle_rows(state, id)
    if index < 0 or index >= len(rows):
        return JSONResponse({"error": "no such subtitle"}, status_code=404)
    row = rows[index]
    data, _note = _subtitle_bytes(state, asset, row)
    if data is None:
        return JSONResponse({"error": "subtitle unavailable"}, status_code=404)
    try:
        text = subtitles.to_webvtt(data, row["format"])
    except subtitles.SubtitleUndecodable as error:
        return JSONResponse({"error": str(error)}, status_code=415)
    response = PlainTextResponse(text, media_type="text/vtt; charset=utf-8")
    response.headers["Cache-Control"] = "private, no-cache"
    return response


@router.api_route("/thumb", methods=["GET", "HEAD"])
def thumbnail(request: Request, id: int, args: dict[str, str] = Depends(require_auth)):
    path = request.app.state.media_engine.file_for(id, thumbnail=True)
    return _image_response(request, path)


@router.api_route("/photo", methods=["GET", "HEAD"])
def photo(request: Request, id: int, args: dict[str, str] = Depends(require_auth)):
    """图片资产原图。灯箱看大图用这条，图片墙一律走 `/photo-thumb`。"""
    path = request.app.state.media_engine.file_for(id)
    return _image_response(request, path)


@router.api_route("/photo-thumb", methods=["GET", "HEAD"])
def photo_thumb(request: Request, id: int, args: dict[str, str] = Depends(require_auth)):
    """图片墙的缩略图。缓存命中就此返回，不去解析原图。

    解析原图要在挂载的网盘上确认它还在，实测一次 0.1–0.4 秒；一屏几十张缩略图全部命中
    缓存，也要为这几十次往返等上好几秒。缩略图缩好之后原图在不在都不改变这次的响应。
    """
    state = request.app.state
    path = state.photo_service.cached(id)
    if path is None:
        try:
            path = state.photo_service.thumbnail(id, state.media_engine.file_for(id))
        except PreviewUnavailable:
            return JSONResponse({"error": "unavailable"}, status_code=404)
    return _image_response(request, path, media_type="image/jpeg")


def _sample_response(request: Request, code: str, n: int, *, thumb: bool):
    """番号样张的一张图（ADR-0068）。先问缓存；没有才按账本里那条地址下载一次。

    只接受番号与序号，地址由服务端从账本查：页面递不进上游地址。取不到一律 404，页面
    显示占位；下载失败在缓存里记一个标记，一天内不再为这一张出网。
    """
    from . import sample_images

    state = request.app.state
    cache = state.sample_cache
    path = cache.cached(code, n, thumb=thumb)
    if path is None:
        contract = state.web_contract
        with contract.read_connection() as connection:
            url = sample_images.sample_url(connection, code, n)
        if url is not None and not cache.failed_recently(code, n):
            path = cache.fetch(code, n, url, sample_images.downloader(contract.follow_secrets_root),
                               thumb=thumb)
    if path is None:
        return JSONResponse({"error": "unavailable"}, status_code=404)
    return _image_response(request, path, media_type="image/jpeg")


@router.api_route("/sample-thumb", methods=["GET", "HEAD"])
def sample_thumb(request: Request, code: str = "", n: int = 0,
                 args: dict[str, str] = Depends(require_auth)):
    """照片墙上那一档样张缩略图，宽度与 `/photo-thumb` 相同。"""
    return _sample_response(request, code, n, thumb=True)


@router.api_route("/sample-image", methods=["GET", "HEAD"])
def sample_image(request: Request, code: str = "", n: int = 0,
                 args: dict[str, str] = Depends(require_auth)):
    """灯箱里看的样张原图。"""
    return _sample_response(request, code, n, thumb=False)


@router.api_route("/poster", methods=["GET", "HEAD"])
def poster(request: Request, id: int, c: int = 4, args: dict[str, str] = Depends(require_auth)):
    try:
        path = request.app.state.preview_service.poster(id, c)
    except PreviewUnavailable:
        return JSONResponse({"error": "unavailable"}, status_code=404)
    return _image_response(request, path, media_type="image/jpeg")


@router.api_route("/timeline", methods=["GET", "HEAD"])
def timeline(request: Request, id: int, s: int = 0, args: dict[str, str] = Depends(require_auth)):
    """时间轴预览的一张接触印相。只发已经铺好的，这里不现抽。

    `/poster` 那条取不到会当场抽一帧，因为九宫格只要九张。这里一张图是一百帧，现抽要
    几分钟，悬停的人早走了；没铺到就回 404，页面退回九宫格。
    """
    contract = request.app.state.web_contract
    path = timeline_sheets.sheet_path(contract.timeline_root, id, s)
    if id <= 0 or s < 0 or not path.is_file():
        return JSONResponse({"error": "unavailable"}, status_code=404)
    return _image_response(request, path, media_type="image/jpeg")


@router.api_route("/cover", methods=["GET", "HEAD"])
def cover(request: Request, code: str = "", thumb: int = 0,
          args: dict[str, str] = Depends(require_auth)):
    """官方封套原图。存原图不裁：4:3 与 16:9 两种版式在界面上按比例取景。

    `thumb=1` 要的是卡片网格那一档派生件（`COVER_THUMB_EDGE`），详情与裁切取原件。
    开关是布尔而不是像素数，理由同 `/entity-image`。
    """
    path = request.app.state.web_contract.cover_path(code)
    if path is None:
        return JSONResponse({"error": "no cover"}, status_code=404)
    if thumb:
        derived = request.app.state.cover_thumb_service.thumbnail(path.stem, path)
        if derived is not None:
            return _image_response(request, derived, media_type=ENTITY_THUMB_TYPE)
    return _image_response(request, path, media_type="image/jpeg")


@router.post("/api/cover-crop")
async def cover_crop(request: Request, args: dict[str, str] = Depends(require_auth)):
    """人自己框的正封那一块，或者把它撤掉换回算出来的那个。

    写的是封面旁边的 `.poster.json`，不生成第二张图片：正封本来就不是一张新图，
    而是一组坐标（`jav_poster_crop` 的约定）。所以封面原图一个字节都不动，撤掉
    手工框就是删掉这份 sidecar，下一次取景重新按折痕判据算。

    `box` 是源图像素的 `{x0,y0,x1,y1}`，右下开区间；`box` 为 null 表示恢复默认。
    """
    state = request.app.state.web_contract
    sent = await request.json()
    payload = sent if isinstance(sent, dict) else {}
    code = str(payload.get("code") or "")
    path = state.cover_path(code)
    if path is None:
        return JSONResponse({"error": "这个番号没有封面"}, status_code=404)
    size = images.measure_image_size(path.read_bytes())
    if size is None:
        return JSONResponse({"error": "这张封面读不出来"}, status_code=422)
    if payload.get("box") is None:
        # 恢复默认是当场按折痕判据重算一遍，不是删掉 sidecar：删掉之后没有人会
        # 再来算，页面拿到的是「这张图不该裁」，正封从此再也回不来。
        def read_evidence(suffix):
            try:
                value = json.loads(path.with_suffix(suffix).read_text(encoding='utf-8'))
                return value if isinstance(value, dict) else {}
            except (OSError, ValueError):
                return {}
        face = read_evidence('.face.json')
        current_face = face.get('px') == list(size)
        title = (jav_poster_crop.read_sidecar(path) or {}).get('title') or read_evidence('.scraping.json').get('title', '')
        record = jav_poster_crop.crop_record(
            code, size[0], size[1], jav_poster_crop.file_gradient(path), title=title,
            face=face.get('face') if current_face else None)
        if current_face and face.get('layout') == 'still':
            record['box'] = dict(x0=0, y0=0, x1=size[0], y1=size[1], method=jav_poster_crop.NONE)
    else:
        record = jav_poster_crop.manual_record(size[0], size[1], payload.get("box"))
    if record is None:
        return JSONResponse({"error": "框选的区域不成立"}, status_code=400)
    jav_poster_crop.write_sidecar(path, record)
    state.cache_bust()
    return JSONResponse({"ok": True, "code": code,
                         "poster_box": jav_poster_crop.projection(record)})


@router.api_route("/endcard-frame", methods=["GET", "HEAD"])
def endcard_frame(request: Request, id: int, name: str, args: dict[str, str] = Depends(require_auth)):
    """Serve only generated OCR evidence frames, never a client-provided path."""
    if (id <= 0 or not name.endswith(".png") or "/" in name or "\\" in name
            or name.startswith(".")):
        return JSONResponse({"error": "invalid frame"}, status_code=400)
    root = (request.app.state.settings.candidate_root
            / "endcard-evidence" / str(id)).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        return JSONResponse({"error": "no frame"}, status_code=404)
    response = FileResponse(path, media_type="image/png")
    response.headers["Cache-Control"] = f"private, max-age={MEDIA_CACHE_SECONDS}"
    return response


@router.api_route("/avatar", methods=["GET", "HEAD"])
def avatar(request: Request, id: int, args: dict[str, str] = Depends(require_auth)):
    try:
        path = request.app.state.preview_service.avatar(id)
    except PreviewUnavailable:
        return JSONResponse({"error": "unavailable"}, status_code=404)
    return _image_response(request, path, media_type="image/jpeg")


@router.api_route("/follow-avatar", methods=["GET", "HEAD"])
def follow_avatar(request: Request, service: str = "", id: str = "",
                  provider: str = "", ref: str = "",
                  args: dict[str, str] = Depends(require_auth)):
    """作者头像：官方资料页的那张（`service`+`id`）或归档站的那张（`provider`+`ref`）。

    两种都由服务端取回存在本机再交给页面，浏览器不直接碰对方站点。地址只从固定主机
    拼：官方那条由 `resolve_official_avatar` 认 pixiv.pximg.net 一个主机、
    `profile_avatar_tiers` 认 X、Patreon 与 FANBOX 各自的图床，归档那条由
    `follow_assets.mirror_avatar_url` 按 provider 查表；前端递不进任何 URL。
    查官方资料与取图共用应用那一条 client，不每次请求另开连接池。
    """
    state = request.app.state
    transport = state.http_transport
    client = transport.client
    if provider:
        target = follow_assets.mirror_avatar_url(provider, ref)
        if target is None:
            return _asset_response(request, None)
        key = f"mirror:{provider}:{ref}"

        def fetch():
            return follow_assets.fetch_image(client, target)
    elif service == "profile":
        # 名片上的 X、Patreon 与 pixiv：每家取到能用的最大一档，几家之间留像素最多的那张。
        identities = profile_identities(id)
        if not identities:
            return _asset_response(request, None)
        key = f"official:profile:{id}"

        def fetch():
            tier_lists = []
            for name, handle in identities:
                try:
                    tier_lists.append(profile_avatar_tiers(name, handle, transport=transport))
                except (OSError, FollowSourceError):
                    continue
            return follow_assets.largest_image(client, tier_lists)
    else:
        # 官方身份有两种写法：归档 ref 带的 pixiv 数字 id，和论坛名片链接里的
        # FANBOX 创作者 id（`jul3dnsfw.fanbox.cc`）。两种的合法形状都由
        # `resolve_official_avatar` 把关，这里只挡住明显不是身份的串。
        if service != "fanbox" or not _FANBOX_IDENTITY_RE.fullmatch(id):
            return _asset_response(request, None)
        key = f"official:{service}:{id}"

        def fetch():
            try:
                resolved = resolve_official_avatar(service, id, transport=transport)
            except (OSError, FollowSourceError):
                return None
            return follow_assets.fetch_image(client, resolved)
    path = follow_assets.cached_image(_asset_root(state), "avatars", key,
                                      AVATAR_REFRESH_SECONDS, fetch)
    return _asset_response(request, path)


@router.api_route("/source-icon", methods=["GET", "HEAD"])
def source_icon(request: Request, provider: str = "", args: dict[str, str] = Depends(require_auth)):
    """来源的站点图标。地址只认 `follow_assets.SOURCE_ICON_URLS` 那张表，没登记的 404。"""
    state = request.app.state
    target = follow_assets.SOURCE_ICON_URLS.get(provider)
    if target is None:
        return _asset_response(request, None)
    client = state.http_transport.client
    path = follow_assets.cached_image(
        _asset_root(state), "icons", provider, FAVICON_REFRESH_SECONDS,
        lambda: follow_assets.fetch_image(client, target))
    return _asset_response(request, path)


#: 人脸探针，题材头像与换头像按番号取的封面共用。YuNet 的模型是个 232 KB 的 ONNX，
#: 首次用到时才去取；取不到就一直回 None，那时圆标退回样式表里的默认取景、封面退回
#: 正封取景——没网不该等于这一排一张图都没有。
_WORK_FACE_PROBE = avatar_face.FaceProbe()


#: 脸框至少要占画面长边这么多，才算这张图里看得见是谁。
#
#: 数是从落盘那一档反推的：`icon_side()` 要求落盘的图里脸框有 `FACE_PX_IN_STORE` 个
#: 像素，而默认存 `ICON_SIDE` 那么大——脸占到长边这个比例，正好一条线。低于它的图不
#: 是「存小了」，是脸在画面里本来就只有那么点，存多大都改不了圆标里露出的是别的东西。检出分数挡不住这一类——那个后脑勺是 0.83。
_WORK_ICON_FACE_SHARE = follow_assets.FACE_PX_IN_STORE / follow_assets.ICON_SIDE


def _usable_face(record: dict | None) -> bool:
    """这张脸够不够撑起一枚圆标：构图上占得住，像素上放得大。

    两条线问的是两件事，缺一条就漏一类。占比（`_WORK_ICON_FACE_SHARE`）问的是圆里落
    下的到底是不是脸；像素（`FACE_PX_IN_STORE`）问的是这张脸放得大放不大——页面按脸
    框放大时不许上采样，源图里那张脸有多少像素就是放大的天花板。`FACE_PX_IN_STORE` 正是「脸要填
    满圆标那六成得有多少像素」，所以这条线不是新定的数，是那一档的定义本身。

    两关合起来也保证了落盘那份够用：占比过关意味着 `icon_side()` 算出来就是
    `ICON_SIDE`，缩完脸仍有 `ICON_SIDE × 占比` ≥ `FACE_PX_IN_STORE` 个像素；源图比
    `ICON_SIDE` 还小的时候根本不缩，像素这一关直接就是落盘那份的读数。
    """
    return (avatar_face.face_share(record) >= _WORK_ICON_FACE_SHARE
            and avatar_face.face_px_width(record) >= follow_assets.FACE_PX_IN_STORE)


def _work_icon_targets(state, local: list[str], tag: str):
    """这个题材可以拿来当代表图的地址，本库那几张在前。

    后半截是生成器，本库这几张里挑得出脸就一个字节都不出网：`_pick_work_icon` 一找到
    够大的脸就返回，站点那一趟根本不会被求值。走到那里的只有「本库这几张全都看不清」
    ——那多半是这个题材在库里只有一两条更新，而站上同一个标签下有成千上万帖。
    """
    yield from local
    seen = set(local)
    for url in web_follow.work_icon_search_urls(state.web_contract, tag,
                                                transport=state.http_transport):
        if url not in seen:
            seen.add(url)
            yield url


def _pick_work_icon(client, targets: Iterable[str]) -> tuple[bytes | None, dict | None]:
    """按热度顺着候选找第一张看得清脸的图，返回落盘用的字节和人脸记录。

    只取最热那一张的话，圆标里有一半是身体特写——最热的帖子常常就是特写。

    「检出了脸」这一关太松，收下的常常不是脸：YuNet 在一张 3072×4096 的远景图上会给
    出一个占长边百分之五、分数 0.69 的框，罩在肩背的纹身上，而圆标正是按这个框取景放
    大的，于是圆里是一小块皮肤。判据因此是 `_usable_face`：脸得在画面里占得住，还得
    有足够多的像素撑到放大到头，两条都过才停，否则继续看下一张候选。

    一张都过不了这两关的题材按「没有头」处理：退回第一张取得到的图，不写人脸记录，
    页面于是按样式表里的默认取景显示整张封面。这类题材多半真的给不出正脸——顶着头发
    的背影、非人形的主角，或者站上那个标签下本来就只有远景。与其把画面里最大的那块
    皮肤放大成一枚认不出的圆，不如老实摆一张全身：它至少还认得出是哪部作品。

    检脸看的是站点那张高清封面，落盘的是缩过的那份：两件事要的尺寸不是一个数——
    250px 的缩略图里一张脸只剩十几个像素，而显示出来只有 28px。

    黑边在这三步之前就裁掉。站点上的 3D 封面常把 21:9 的画面压进 16:9 的帧里，上下
    各留一道纯黑；那两道黑边跟着进圆标，圆里直接露出黑条，还把画面撑高、让 cover 把
    脸缩得更小。裁完再检脸，坐标才落在这张图自己的坐标系里。
    """
    first: bytes | None = None
    for target in targets:
        body = follow_assets.fetch_image(client, target)
        if not body:
            continue
        body = follow_assets.trim_letterbox(body)
        record = _WORK_FACE_PROBE.on_bytes(body)
        if _usable_face(record):
            return _stored_icon(body, record)
        if first is None:
            first = body
    return _stored_icon(first, None) if first is not None else (None, None)


def _stored_icon(body: bytes, record: dict | None) -> tuple[bytes, dict | None]:
    """缩到圆标那一档，并把记录里的源图像素换成落盘那张的。

    存多大由 `icon_side` 按这张脸占画面多少来定，不是一个固定值：页面能放大到多少，
    上限之一就是落盘那张里脸框有几个像素。

    记录必须描述图旁边那个文件：页面拿 `naturalWidth` 核对脸框说的是不是同一张图，
    对不上就退回几何居中。脸框本身是归一化的，比例缩放不动它；「这张脸有多少像素」
    问的则是浏览器手里那张图，答案也只能是缩完之后的那个数。
    """
    small = follow_assets.shrink_image(body, follow_assets.icon_side(record))
    size = follow_assets.image_size(small) if record else None
    if size:
        record = {**record, "px": [size[0], size[1]]}
    return small, record


@router.api_route("/work-icon", methods=["GET", "HEAD"])
def work_icon(request: Request, work: str = "",
              args: dict[str, str] = Depends(require_auth)):
    """题材的代表图。

    `work` 只是题材的身份，不是地址：服务端按它在账本里排出按热度排好的几个候选，
    再核对图床主机是不是 `web_follow` 登记的那个。排这一步在缓存回调里做，本机那份
    还新鲜时一行账本都不读；取回的字节照样要先能认成图片才落盘。

    候选顺着往下取，停在第一张看得清脸的，判据见 `_pick_work_icon`。人脸记录跟着落盘
    的那张图写在旁边，页面据它把取景挪到脸上、按脸框放大：圆标只有 28px，按几何中心
    裁一张全身图出来常常只剩一截身子，而挪到哪、放多大都要看这张图里脸在哪、有多少
    像素，猜不出来。
    """
    state = request.app.state
    root = web_follow.work_root(work)
    if not root:
        return _asset_response(request, None)
    client = state.http_transport.client
    cache_root = _asset_root(state)
    cached = follow_assets.cache_path(cache_root, "works", root)

    picked: dict | None = None
    replaced = False

    def fetch():
        nonlocal picked, replaced
        with state.database.read_connection() as connection:
            store = FollowStore(lambda: connection)
            targets = web_follow.work_icon_urls(store, root)
            tag = web_follow.work_icon_tag(store, root)
        body, picked = _pick_work_icon(client, _work_icon_targets(state, targets, tag))
        replaced = bool(body)
        return body

    path = follow_assets.cached_image(cache_root, "works", root, None, fetch)
    if replaced:
        # 换了图，旧的脸记录必须一起换：留着它页面会拿上一张的脸心给这一张取景，
        # 而那在界面上和「这张图本来就该这么摆」看不出区别。
        avatar_face.drop_sidecar(cached)
        if path is not None and picked is not None:
            avatar_face.write_sidecar(path, picked)
    return _asset_response(request, path)


def _follow_media_size(state, item_id: int, target: ResolvedFollowMedia) -> int | None:
    """正片的字节数，取不到就是 None。

    只能问上游：来源列表里没有这一项，账本里也只有落盘资源才有 size。一次 HEAD 就够，
    正文一个字节都不取，而且走的是播放同一条 `open_upstream`——逐跳白名单和非 2xx 拒收
    都在那里，这里不另开一条出网路径。

    上游不给 content-length（分块传输、签名 URL 过期、限流挡住）时返回 None，
    播放器那边退回按秒的读数；不拿估算的字节数冒充实测。
    """
    try:
        response = open_upstream(state.http_transport.client, "HEAD", target, incoming={})
    except (FollowProxyError, OSError, httpx.HTTPError) as error:
        LOGGER.warning("follow media size unavailable for item %s: %s", item_id, error)
        return None
    try:
        size = int(response.headers.get("content-length") or 0)
    except ValueError:
        size = 0
    finally:
        response.close()
    return size or None


@router.api_route("/follow-qualities", methods=["GET"])
def follow_qualities(request: Request, id: int, args: dict[str, str] = Depends(require_auth)):
    """这条关注视频有哪些清晰度可选，以及默认档有多少字节。

    单独一个端点而不是塞进 /api/follow：解析要抓一次来源详情页，列表一次几百条，
    逐条解析等于几百个外部请求。这里只在用户展开某个条目、播放器要画菜单时问一次，
    解析结果本身在 resolver 里有缓存。

    只有 rule34video 给多档（video_url 加 video_alt_url{,2,3}）；其余来源返回空表，
    播放器据此只显示「原画」。取不到不是错误——签名 URL 会过期、来源也可能改版，
    那时照常播默认档就行。

    `size` 跟着一起回：渐进下载在浏览器里量不到字节速率（media 请求的
    `encodedBodySize` 是 0），只有拿文件大小和时长换出平均码率，才能把缓冲前沿推进的
    秒数折成 MB/s。这里已经解析完媒体地址，多一次 HEAD 就能拿到，比让浏览器再发一趟便宜。
    """
    state = request.app.state
    with state.database.read_connection() as connection:
        item = FollowStore(lambda: connection).item(id)
    if item is None:
        return JSONResponse({"error": "no such follow item"}, status_code=404)
    try:
        resolved = state.follow_media_resolver.resolve(item)
    except FollowMediaUnavailable:
        return {"qualities": [], "size": None}
    # 档位数量不写死：正则匹配 video_url 与任意编号的 video_alt_urlN，
    # 站点给几档就是几档（实测同一作者下有 4 档也有 5 档的条目）。
    # 2160 按站点自己的写法叫 4K，不叫 2160p。
    return {"qualities": [
        {"height": height, "label": "4K" if height >= 2160 else f"{height}p"}
        for height, _ in resolved.qualities if height
    ], "size": _follow_media_size(state, id, resolved)}


@router.api_route("/follow-stream", methods=["GET", "HEAD"])
def follow_stream(request: Request, id: int, media: int | None = None,
                  quality: int | None = None, download: int = 0,
                  args: dict[str, str] = Depends(require_auth)):
    """Play a remote follow candidate through Peach without exposing its upstream URL."""
    state = request.app.state
    with state.database.read_connection() as connection:
        item = FollowStore(lambda: connection).item(id)
    if item is None:
        return JSONResponse({"error": "no such follow item"}, status_code=404)
    try:
        key = f"follow:{item.id}:media:{media}:quality:{quality}"
        cache = cache_for(state.web_contract)
        extra = {"content-disposition": _attachment_disposition(item.title, item.media_url or item.url)} if download else None
        cached = cache.response(key, request, headers=extra)
        if cached is not None:
            return cached
        target = state.follow_media_resolver.resolve(item, media, quality)
        # 跟重定向、逐跳过白名单、非 2xx 一律不转发，都在 follow_stream 里；
        # 这里只负责把 Peach 的请求接到那个流上。
        extra = {"content-disposition": _attachment_disposition(item.title, target.url)} if download else None
        return cache.stream(key, request,
            lambda: open_upstream(state.http_transport.client, request.method, target, incoming=request.headers),
            headers=extra)
    except FollowMediaUnavailable as error:
        return JSONResponse({"error": str(error)}, status_code=404)
    except FollowProxyError as error:
        # 上游的状态码与正文不转发：403 页面、限流提示、错误 JSON 交给播放器
        # 没有用处，还会把上游主机和提示语抄给浏览器。对外统一是 502。
        LOGGER.warning("follow media proxy refused for item %s: %s", id, error)
        return JSONResponse({"error": "follow media unavailable"}, status_code=502)
    except (OSError, httpx.HTTPError):
        LOGGER.exception("follow media proxy failed for item %s", id)
        return JSONResponse({"error": "follow media unavailable"}, status_code=502)

@router.api_route("/follow-image", methods=["GET", "HEAD"])
def follow_image(request: Request, id: int, media: int | None = None,
                 args: dict[str, str] = Depends(require_auth)):
    state = request.app.state
    with state.database.read_connection() as connection:
        item = FollowStore(lambda: connection).item(id)
    if item is None:
        return Response(status_code=404)
    cache = cache_for(state.web_contract)
    try:
        if media is None:
            url = web_follow._remote_thumb_url(item)
        else:
            rows = web_follow._raw_media_items(item)
            if media < 0 or media >= len(rows):
                raise ValueError("媒体序号不存在")
            url = rows[media].get("thumb_url") or rows[media].get("url")
        from .http import public_https_url
        if not public_https_url(url or ""):
            raise ValueError("图片来源不可用")
        key = f"image:{url}"
        hit = cache.response(key, request)
        if hit is not None:
            return hit
        target = ResolvedFollowMedia(url, item.url,
            allowed_hosts=follow_providers.hosts().get(item.provider, ()), public_hosts=True)
        if media is not None:
            resolved = state.follow_media_resolver.resolve(item, media)
            if resolved.headers:
                target = replace(resolved, url=url)
        return cache.stream(key, request, lambda: open_upstream(
            state.http_transport.client, request.method, target, incoming=request.headers))
    except (ValueError, FollowMediaUnavailable, FollowProxyError, OSError, httpx.HTTPError):
        return Response(status_code=404, headers={"cache-control": "no-store"})


def _jav_preview_target(url: str) -> ResolvedFollowMedia:
    """已验证的预览来源使用固定主机范围，其他 CDN 逐跳核对公网解析。"""
    return ResolvedFollowMedia(url, allowed_hosts=("www.prestige-av.com", "jdforrepam.com", "cc3001.dmm.co.jp"), public_hosts=True)


@router.api_route("/jav-preview/{code}/{ref}", methods=["GET", "HEAD"])
def jav_preview(request: Request, code: str, ref: str, args: dict[str, str] = Depends(require_auth)):
    from .web_jav_detail import detail_code
    from .preview_transport import open_preview_upstream
    cache = cache_for(request.app.state.web_contract)
    previews = PreviewCache(cache)
    try:
        code = detail_code({"code": code})
        source = previews.source(code, ref)
        url = source["url"]
        target = _jav_preview_target(url)
        client = request.app.state.http_transport.client
        def upstream(method, target, incoming):
            return open_preview_upstream(client, method, target, incoming=incoming,
                secrets_root=request.app.state.web_contract.follow_secrets_root)
        if source["playlist"]:
            def fetch(url):
                response = upstream("GET", _jav_preview_target(url), {})
                try:
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 1024 * 1024:
                            raise ValueError("预览清单过大")
                        chunks.append(chunk)
                    return b"".join(chunks)
                finally:
                    response.close()
            body = previews.playlist(code, ref, fetch)
            return Response(b"" if request.method == "HEAD" else body, media_type="application/vnd.apple.mpegurl",
                            headers={"cache-control": "private, no-cache", "content-length": str(len(body))})
        def media():
            response = upstream(request.method, target, request.headers)
            if source.get('media_type') == 'video/mp4':
                kind = response.headers.get('content-type', '').split(';')[0].lower()
                if kind not in {'video/mp4', 'application/octet-stream', 'binary/octet-stream'}:
                    response.close()
                    raise FollowProxyError('视频预览响应未取得')
                response.headers['content-type'] = 'video/mp4'
            return response
        return cache.stream(f"preview:{code}:{ref}", request, media)
    except (ValueError, OSError, FollowProxyError, httpx.HTTPError):
        return Response(status_code=404, headers={"cache-control": "no-store"})


@router.api_route("/follow-cover", methods=["GET", "HEAD"])
def follow_cover(request: Request, id: int, media: int | None = None,
                 args: dict[str, str] = Depends(require_auth)):
    """Return a cached clear still for a follow video; keep its URL server-side.

    `media` 与 `/follow-stream` 同义，按原始媒体清单的序号点名帖子里的某个视频。
    """
    state = request.app.state
    with state.database.read_connection() as connection:
        item = FollowStore(lambda: connection).item(id)
    if item is None:
        return JSONResponse({"error": "no such follow item"}, status_code=404)
    try:
        if item.provider == "rule34video":
            # 同作品别的版本只在磁盘缓存未命中时才去查：命中的请求不该先算一遍整库分组。
            path = state.follow_cover_service.cover(
                item, media, alternatives=lambda: web_follow.cover_alternatives(state.web_contract, item.id))
        else:
            path = state.follow_cover_service.cover(item, media)
    except FollowCoverUnavailable:
        # 失败响应不包含可解码图像，图片组件以 error 状态显示占位并允许重试。
        return Response(status_code=404, headers={"cache-control": "no-store"})
    return _image_response(request, path, media_type="image/jpeg")


@router.api_route("/logo", methods=["GET", "HEAD"])
def logo(request: Request, studio: str = "", variant: str = "",
         args: dict[str, str] = Depends(require_auth)):
    try:
        path, content_type = request.app.state.preview_service.logo(studio, variant)
    except PreviewUnavailable:
        return JSONResponse({"error": "unavailable"}, status_code=404)
    response = FileResponse(path, media_type=content_type)
    # Logo 可以缓存，但文件会在人工批准或重新归一后原地替换。固定 URL 若缓存
    # 一整天，浏览器会继续显示旧图；no-cache 会复用本地副本并用 ETag 重验。
    response.headers["Cache-Control"] = "public, no-cache"
    return response


def _site_mark_response(state, url: str, root: Path):
    """站点圆标的取图、挑图、合成与缓存；调用方负责先把地址查出来。

    地址一律由服务端自己从某张表或账本解析，函数本身不判断来路——但它是唯一会
    真的去连对方站点的地方，所以每个调用方都得先说清自己的地址从哪来。
    """
    # 存档链接按快照保存的原站找图标，缓存也按原站存：缓存键只看主机，按存档地址存，
    # 所有存档链接就共用 web.archive.org 那一枚。
    page, locate = social_links.archive_view(url)
    cached = link_marks.cached_path(root, page)
    if cached is None:
        return JSONResponse({"error": "unavailable"}, status_code=404)
    if not link_marks.is_fresh(cached, ttl=FAVICON_REFRESH_SECONDS):
        def fetch(target: str):
            try:
                upstream = state.http_transport.client.get(
                    locate(target), headers={"User-Agent": USER_AGENT},
                    timeout=8, follow_redirects=True)
            except (OSError, httpx.HTTPError):
                return None
            if upstream.status_code != 200 or not upstream.content:
                return None
            return upstream.content, upstream.headers.get("content-type", "")

        # 两条通道都不适用时退回原样缩图：糊一点也好过露出地球图标。存档链接例外：原站
        # 给不出合格的圆标就用存档站自己的图标，点过去本来就是存档。原站那张不合格的图
        # 原样缩进来是变形的：ONE'S DOUBLE 只留下 7:1 的横排字标。
        archived = page != url
        made = site_icons.best_mark(page, fetch, link_marks.render_mark,
                                    fallback=None if archived else link_marks.plain_mark)
        if made is None and archived:
            made = site_icons.best_mark(url, fetch, link_marks.render_mark,
                                        fallback=link_marks.plain_mark)
        if made:
            root.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(made)
        elif not cached.exists():
            # 取不到就让前端露出下面的地球图标，不要留一个空白圆。
            return JSONResponse({"error": "unavailable"}, status_code=404)
    result = FileResponse(cached, media_type="image/png")
    result.headers["Cache-Control"] = "public, no-cache"
    return result


@router.api_route("/link-mark", methods=["GET", "HEAD"])
def link_mark(request: Request, id: int = 0, args: dict[str, str] = Depends(require_auth)):
    """资料页外链的圆标：站点自己最好的那份图标资产，能上色就上色。

    地址只从账本按链接 id 解析，绝不接受前端递过来的 URL——和 `/follow-stream`
    同一条规矩，否则这就是一个任意地址抓取的口子。取哪一份交给 `site_icons`：
    先读首页声明的 apple-touch-icon / SVG / manifest，都没有才落到 favicon.ico。

    标记为失效的链接不取：那个域名多半已被停放，首页是一段跳转脚本，抓它只会让
    杀毒软件把 Peach 报成在访问跳转页。
    """
    state = request.app.state
    with state.database.read_connection() as connection:
        row = connection.execute(
            "SELECT url, metadata_json FROM entity_link WHERE id=?", (id,)).fetchone()
    if row is None or link_status.gone_mark(row["metadata_json"]) is not None:
        return JSONResponse({"error": "no such link"}, status_code=404)
    return _site_mark_response(state, row["url"], GENERATED_DIR / "link-marks")


@router.api_route("/site-mark", methods=["GET", "HEAD"])
def site_mark(request: Request, source: str = "", domain: str = "",
              args: dict[str, str] = Depends(require_auth)):
    """采集来源的已核对 favicon 与口味排行的站点圆标。

    两个参数都不是地址，是键：`source` 查 `scraping_access.SOURCES`，`domain` 查
    `taste_history.TASTE_DOMAIN_SUFFIXES`，都不在表里就是 404。和 `/link-mark`
    同一条规矩——服务端只取自己已经知道的地址，绝不取前端递来的任意 URL。

    图标由服务端取有三样浏览器拿不到的东西：`site_icons` 会问站点自己声明的
    apple-touch-icon、SVG 和 manifest，而直连只够拿一枚 16px 的 `/favicon.ico`；
    要代理才通的站点在这里照样有图；浏览器不必向对方站点发请求，也就不必按站
    报出「在看哪些站」。

    `domain` 只认白名单里那条后缀本身，不认它的子域：子域是浏览历史里带进来的
    任意值，放行等于按前端给的主机名去连——正是上面那条规矩要挡的。
    """
    if source:
        spec = scraping_access.SOURCES.get(source)
        if spec is None:
            return JSONResponse({"error": "no such source"}, status_code=404)
        # 来源身份使用已核对的原图，保留站点 favicon 的颜色和画布。
        icon = PROJECT_ROOT / "resources" / "site-marks" / f"{source}.png"
        if icon.is_file():
            result = FileResponse(icon, media_type="image/png")
            result.headers["Cache-Control"] = "public, no-cache"
            return result
        target = spec["login"]
    else:
        host = domain.casefold().strip().removeprefix("www.").rstrip(".")
        if host not in taste_history.TASTE_DOMAIN_SUFFIXES:
            return JSONResponse({"error": "no such site"}, status_code=404)
        target = f"https://{host}/"
    return _site_mark_response(request.app.state, target, GENERATED_DIR / "site-marks")


@router.api_route("/entity-image", methods=["GET", "HEAD"])
def entity_image(request: Request, kind: str, id: int, thumb: int = 0,
                 args: dict[str, str] = Depends(require_auth)):
    """实体图。`thumb=1` 要的是索引页那一档派生件，不带就是资料页用的原件。

    开关是布尔而不是像素数：尺寸由服务端一处定死，页面递多少像素进来就等于让每个
    调用点各存一份尺寸，改一次得追七处，而缓存目录里会长出一堆只差几十像素的派生件。
    """
    state = request.app.state
    try:
        path, content_type = state.preview_service.entity_image(kind, id)
    except PreviewUnavailable:
        # 并入别人的实体没有自己的图：按墓碑跳到现在那一条。只在取不到时查库，
        # 索引页一屏几百张图的常规路径不多一次查询。
        target = current_entity_id(state.web_contract, id)
        if target != id:
            # 只换 id，其余参数（缩略图开关、口令）原样带上；写相对地址，不依赖反代给的主机名。
            moved = request.url.include_query_params(id=target)
            return RedirectResponse(f"{moved.path}?{moved.query}", status_code=307)
        return JSONResponse({"error": "unavailable"}, status_code=404)
    if thumb:
        derived = state.entity_thumb_service.thumbnail(entity_image_key(kind, id), path)
        if derived is not None:
            path, content_type = derived, ENTITY_THUMB_TYPE
    return _image_response(request, path, media_type=content_type)


def current_entity_id(contract, entity_id: int) -> int:
    """页面递来的实体 id 按合并墓碑换成现在的实体；解析不到就原样返回，交给接口自己的判据。"""
    if entity_id <= 0:
        return entity_id
    with contract.read_connection() as connection:
        return resolve_entity_id(connection, entity_id) or entity_id


def _picker_roots(state) -> tuple[Path, Path]:
    """(候选缓存按来源分的根, 装头像的目录)。"""
    return (state.candidate_root / "provider-cache" / "performer-avatars",
            state.avatar_root)


def _picker_kind(kind: str) -> str:
    return kind if kind in {"performer", "creator"} else "performer"


def _picker_artwork(request: Request) -> avatar_picker.ArtworkSource:
    """作品画面的取图口。九宫格没铺过就现抽一格，和 `/poster` 走同一条路。"""
    state = request.app.state

    def frame(asset_id: int, cell: int):
        try:
            return state.preview_service.poster(asset_id, cell)
        except PreviewUnavailable:
            return None

    return avatar_picker.ArtworkSource(
        cover_root=state.web_contract.cover_root, frame=frame)


def _remember_library(providers_root: Path, body: bytes, origin: dict,
                      inspected: avatar_provider.InspectedAvatar) -> None:
    """图库图取过一次就按地址留进缓存，翻第二遍、框它、装它都不再出网。"""
    if str(origin.get("provider")) == "gfriends":
        cache = avatar_provider.AvatarCandidateCache(
            providers_root / avatar_picker.GFRIENDS_CACHE)
        cache.store(str(origin.get("upstream_url") or ""), body, inspected)


def _picker_online_projection(item) -> avatar_online.MediaChoices:
    """候选与关注卡共用可见媒体和封面策略，HTTP 地址由 web 层决定。"""
    indexes = tuple(media["index"] for media in web_follow._media_items(item)
                    if media["media_kind"] == "image")
    if (not web_follow._raw_media_items(item) and not item.hidden_media
            and web_follow._media_kind(item) == "image" and item.media_url):
        indexes = (None,)
    return avatar_online.MediaChoices(cover=web_follow._thumb_url(item), images=indexes)


def _picker_online(request: Request, connection, entity_id: int):
    """复用关注代理的主机、凭据与有界封面判据，按候选缓存原图。"""
    state = request.app.state
    providers_root, _ = _picker_roots(state.web_contract)
    cache = avatar_provider.AvatarCandidateCache(providers_root / "follow-content")

    def media(item, index):
        target = state.follow_media_resolver.resolve(item, index)
        upstream = open_upstream(state.http_transport.client, "GET", target, incoming={})
        try:
            chunks, length = [], 0
            deadline = time.monotonic() + avatar_picker.FETCH_TIMEOUT
            for chunk in upstream.iter_bytes():
                if time.monotonic() > deadline:
                    raise avatar_picker.PickerError("这张图未取得：请求超时")
                length += len(chunk)
                if length > avatar_picker.MAX_IMAGE_BYTES:
                    raise avatar_picker.PickerError("图太大了")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            upstream.close()

    def image(item, url):
        if url.startswith("/follow-cover"):
            return state.follow_cover_service.cover(item).read_bytes()
        if url.startswith("/follow-stream"):
            return media(item, None)
        if not avatar_picker.allowed_source(url):
            raise avatar_picker.PickerError("封面地址不可用")
        return avatar_picker.fetch_image(state.http_transport, url)

    def avatar(params):
        response = follow_avatar(request, args={}, **params)
        return Path(response.path).read_bytes() if isinstance(response, FileResponse) else None

    def resolve(ref):
        def remembered(fetch):
            def wrapped(*args):
                found = cache.lookup(f"peach:online/{entity_id}/{ref}")
                return found if found is not None else fetch(*args)
            return wrapped
        try:
            body, origin = avatar_online.resolve(ref, connection, entity_id,
                project=_picker_online_projection,
                image=remembered(image), avatar=remembered(avatar), media=remembered(media))
        except (OSError, httpx.HTTPError, FollowMediaUnavailable, FollowProxyError, FollowCoverUnavailable) as error:
            raise avatar_picker.PickerError("在线图片未取得") from error
        cache.store(f"peach:online/{entity_id}/{ref}", body, avatar_picker.accept_image(body))
        return body, origin
    return resolve


@router.get("/api/avatar-choices")
def avatar_choices(request: Request, kind: str = "performer", id: int = 0,
                   args: dict[str, str] = Depends(require_auth)):
    """这个人还能换成哪些图。只读，不联网，不写盘。"""
    state = request.app.state.web_contract
    providers_root, avatar_root = _picker_roots(state)
    id = current_entity_id(state, id)
    with state.read_connection() as connection:
        listing = avatar_picker.choices(
            connection, providers_root, avatar_root, _picker_kind(kind), id,
            cover_root=state.cover_root)
        if kind == "creator":
            listing["choices"] += [choice.as_dict() for choice in avatar_online.choices(
                connection, id, project=_picker_online_projection)]
        return JSONResponse(listing)


@router.api_route("/avatar-choice", methods=["GET", "HEAD"])
def avatar_choice(request: Request, kind: str = "performer", id: int = 0,
                  ref: str = "", args: dict[str, str] = Depends(require_auth)):
    """候选的预览图。

    页面只递 `ref`，地址由服务端按索引拼——这一层的规矩在文件开头：接受前端递过来的
    URL 就等于开了一个任意地址抓取的口子。取过一次就进内容寻址缓存，翻第二遍不出网。
    """
    state = request.app.state.web_contract
    providers_root, _ = _picker_roots(state)
    id = current_entity_id(state, id)
    with state.read_connection() as connection:
        try:
            body, origin = avatar_picker.resolve(
                ref, connection, providers_root, id,
                request.app.state.http_transport, _picker_artwork(request),
                online=_picker_online(request, connection, id))
        except avatar_picker.PickerError as error:
            return JSONResponse({"error": str(error)}, status_code=404)
    inspected = avatar_provider.inspect_avatar(body)
    if inspected is None:
        return JSONResponse({"error": "这不是一张能识别的图片"}, status_code=415)
    _remember_library(providers_root, body, origin, inspected)
    result = Response(b"" if request.method == "HEAD" else body,
                      media_type=inspected.mime_type)
    # 封面会原地替换，可以缓存这么久是因为它的地址带着 `v`（`Choice.version`）：封面一换，
    # 页面要的就是另一个地址。
    result.headers["Cache-Control"] = f"private, max-age={AVATAR_CACHE_SECONDS}"
    return result


async def _picker_request(request: Request) -> tuple[dict, bytes]:
    """换头像那几个端点的请求：JSON 一份参数，或者请求体是图片字节、参数走查询串。"""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    if content_type == "application/json":
        sent = await request.json()
        return (sent if isinstance(sent, dict) else {}), b""
    return dict(request.query_params), await request.body()


def _picker_entity(state, payload: dict) -> int:
    """请求里的实体 id，换算成现在那一条；缺了或不成数是 0。"""
    try:
        entity_id = int(payload.get("id") or 0)
    except (TypeError, ValueError):
        return 0
    # 页面开着时实体被并入别人，换上的图要落到现在那一条名下，不留成孤立文件。
    return current_entity_id(state, entity_id) if entity_id > 0 else 0


def _picker_crop(raw: object) -> object:
    """请求里的框。JSON 给的是一个对象；本机文件那一路走查询串，写成 `x0,y0,x1,y1`。"""
    if isinstance(raw, str) and raw.count(",") == 3:
        return dict(zip(("x0", "y0", "x1", "y1"), raw.split(",")))
    return raw


@router.post("/api/avatar-frame")
async def avatar_frame(request: Request, args: dict[str, str] = Depends(require_auth)):
    """一张图进框选之前：量出转正后的尺寸，检出脸给默认框。不装。

    - `ref`：候选网格里的那一格。图库图还没取过就像预览那样取一次、留进缓存
    - `url`：手填的地址。过公网判据后取一次、按地址留进缓存，交回一格 `url:` 候选，
      框选、确认都读这一份
    - 请求体直接是图片字节：本机选的文件。只量不存，确认时浏览器再发一次原字节
    """
    state = request.app.state.web_contract
    providers_root, _ = _picker_roots(state)
    payload, body = await _picker_request(request)
    ref, url = str(payload.get("ref") or ""), str(payload.get("url") or "")
    probe = _WORK_FACE_PROBE.on_bytes
    transport = request.app.state.http_transport

    def library() -> avatar_picker.Choice:
        entity_id = _picker_entity(state, payload)
        with state.read_connection() as connection:
            found, origin = avatar_picker.resolve(
                ref, connection, providers_root, entity_id, transport,
                _picker_artwork(request), online=_picker_online(request, connection, entity_id))
            listed = avatar_online.choices(connection, entity_id, project=_picker_online_projection) if ref.startswith("follow:") else []
        _remember_library(providers_root, found, origin, avatar_picker.accept_image(found))
        choice = avatar_picker.framed(ref, found, probe, source="", label="")
        if ref.startswith("follow:"):
            original = next((one for one in listed if ref in one.bases), None)
            if original:
                choice = replace(choice, bases=original.bases)
        return choice

    try:
        if ref:
            choice = await asyncio.to_thread(library)
        elif url:
            if not avatar_picker.allowed_source(url):
                return JSONResponse(
                    {"error": "只接受指向公网的 https 地址"}, status_code=400)
            choice = await asyncio.to_thread(
                avatar_picker.typed_address, url, providers_root,
                partial(avatar_picker.fetch_image, transport), probe)
        elif body:
            choice = await asyncio.to_thread(
                avatar_picker.framed, "", body, probe,
                source="upload", label=str(payload.get("name") or ""))
        else:
            return JSONResponse({"error": "没有可用的图片"}, status_code=400)
    except avatar_picker.PickerError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(choice.as_dict())


@router.post("/api/avatar-pick")
async def avatar_pick(request: Request, args: dict[str, str] = Depends(require_auth)):
    """换头像。几种来源共用这一个出口，区别只在字节从哪来。

    - `ref`：服务端自己列出来的候选（图库同名图、这个人取过的图、他作品里的画面），
      或经 `/api/avatar-frame` 取过、留在缓存里的番号封面与手填地址
    - 请求体直接是图片字节：用户从本机选的文件，浏览器原样发过来

    `crop` 是各路共用的一道可选工序：给了框就先按源图像素切一块再装。作品画面
    那一路必须给框——横图整张装进圆框只剩一块背景。被切的原图一个字节都不动。

    被顶下来的那张不删也不搬：它按内容哈希躺在候选缓存里，下次出现在候选列表的
    「用过的」那一组，换回去只是再装一次。
    """
    state = request.app.state.web_contract
    providers_root, avatar_root = _picker_roots(state)
    payload, body = await _picker_request(request)
    entity_id = _picker_entity(state, payload)
    if entity_id <= 0:
        return JSONResponse({"error": "缺少实体 id"}, status_code=400)
    kind = _picker_kind(str(payload.get("kind") or "performer"))
    ref = str(payload.get("ref") or "")
    version = payload.get("version")
    box = _picker_crop(payload.get("crop"))
    try:
        if ref:
            with state.read_connection() as connection:
                body, origin = avatar_picker.resolve(
                    ref, connection, providers_root, entity_id,
                    request.app.state.http_transport, _picker_artwork(request),
                    version=version if isinstance(version, str) else None,
                    online=_picker_online(request, connection, entity_id))
            _remember_library(providers_root, body, origin, avatar_picker.accept_image(body))
        elif body:
            origin = {"source": "avatar picker", "provider": "upload",
                      "external_id": str(payload.get("name") or "")}
        else:
            return JSONResponse({"error": "没有可用的图片"}, status_code=400)
        if box not in (None, ""):
            body, cropped = avatar_picker.crop(body, box)
            origin = {**origin, **cropped}
        origin = {**origin, "user_selected": True}
        result = avatar_picker.install(providers_root, avatar_root, kind,
                                       entity_id, body, origin)
    except avatar_picker.PickerError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    # 页面按这份索引决定「出 `<img>` 还是首字母垫底」，换完不失效就看不到新图。
    state.cache_bust()
    return JSONResponse({"ok": True, "kind": kind, "id": entity_id, **result})


#: 按番号去官方渠道取一张封面的预算。人在弹层里等着，挑不出就报原因，不能一直转。
CODE_COVER_SECONDS = 60


def _official_cover(contract, key: str) -> bytes:
    """官方渠道里这个番号最大的那张封面，和重探封面走同一个 `best_cover`。"""
    from .catalog_rules import is_korean_mib_code
    from .jav_cover_fetch import (
        MIB_NOT_JAV, DeadlineExceeded, HostLimitedTransport, NotFound, Unavailable, best_cover,
    )
    from .scraping_access import SourcePaused, SourceTransport

    if is_korean_mib_code(key):
        raise avatar_picker.PickerError(MIB_NOT_JAV)
    raw = SourceTransport(contract.follow_secrets_root, max_requests=40,
                          max_bytes=32 * 1024 * 1024, max_seconds=CODE_COVER_SECONDS)
    transport = HostLimitedTransport(raw, 1.0)
    try:
        _candidate, _size, data = best_cover(
            transport, key, 0,
            metadata_root=contract.follow_sources_root / "metadata" / "javinizer-go",
            deadline=time.monotonic() + CODE_COVER_SECONDS)
    except NotFound as error:
        raise avatar_picker.PickerError("官方渠道没有这个番号的封面") from error
    except Unavailable as error:
        raise avatar_picker.PickerError(f"取不到这个番号的封面：{error}") from error
    except DeadlineExceeded as error:
        raise avatar_picker.PickerError(
            f"{CODE_COVER_SECONDS} 秒内没取到这个番号的封面，稍后再试") from error
    except SourcePaused as error:
        raise avatar_picker.PickerError(str(error)) from error
    except httpx.TransportError as error:
        raise avatar_picker.PickerError("来源连接失败，请检查来源连接设置") from error
    finally:
        transport.close()
    return data


@router.post("/api/avatar-code-cover")
async def avatar_code_cover(request: Request, args: dict[str, str] = Depends(require_auth)):
    """按番号拿一张封面给换头像框选，番号不必在馆藏里。

    本机有就读本机，没有才去官方渠道取，取到的留在候选缓存里；这里只取图，装不装由
    框选之后的 `/api/avatar-pick` 决定。
    """
    from .metadata import validate_provider_code

    state = request.app.state.web_contract
    providers_root, _ = _picker_roots(state)
    sent = await request.json()
    payload = sent if isinstance(sent, dict) else {}
    try:
        code = validate_provider_code(str(payload.get("code") or ""))
    except ValueError:
        return JSONResponse({"error": "认不出这个番号"}, status_code=400)
    try:
        choice = await asyncio.to_thread(
            avatar_picker.code_cover, code, state.cover_root, providers_root,
            partial(_official_cover, state), _WORK_FACE_PROBE.on_bytes)
    except avatar_picker.PickerError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(choice.as_dict())
