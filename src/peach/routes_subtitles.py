"""播放器字幕文件转换与中文在线搜索。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import subtitle_search, subtitles
from .routes_auth import require_auth

router = APIRouter(dependencies=[Depends(require_auth)])


def _error(error: Exception):
    return JSONResponse({"error": str(error)}, status_code=400)


def _track(text: str):
    return PlainTextResponse(text, media_type="text/vtt", headers={"Cache-Control": "no-store"})


@router.post("/api/subtitles/convert")
async def convert_subtitle(request: Request, name: str = ""):
    """上传文件只转换为本次播放所需的 WebVTT。"""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > subtitle_search.MAX_BYTES:
            return JSONResponse({"error": "字幕文件超过 4 MiB"}, status_code=413)
    try:
        return _track(subtitle_search.convert(bytes(data), subtitles.subtitle_format(name)))
    except (subtitle_search.SubtitleSearchError, subtitles.SubtitleUndecodable) as error:
        return _error(error)


@router.get("/api/subtitles/search")
def search_subtitles(q: str = "", offset: int = 0):
    try:
        return subtitle_search.search(q, offset)
    except subtitle_search.SubtitleSearchError as error:
        return _error(error)


@router.get("/api/subtitles/subtitlecat/{subtitle_id}")
def subtitle_files(subtitle_id: str):
    try:
        return subtitle_search.files(subtitle_id)
    except subtitle_search.SubtitleSearchError as error:
        return _error(error)


@router.get("/api/subtitles/subtitlecat/{subtitle_id}/{index}")
def online_subtitle(subtitle_id: str, index: int):
    try:
        return _track(subtitle_search.load(subtitle_id, index))
    except (subtitle_search.SubtitleSearchError, subtitles.SubtitleUndecodable) as error:
        return _error(error)
