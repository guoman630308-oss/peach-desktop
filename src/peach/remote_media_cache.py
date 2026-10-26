"""远程媒体的本机持久缓存；只安装完整响应，Range 保留自己的范围。"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from filelock import FileLock
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, Response, StreamingResponse

from .fsutil import atomic_path, atomic_write_text

AVATAR_REFRESH_SECONDS = 90 * 86400
FAVICON_REFRESH_SECONDS = 365 * 86400
MEDIA_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges", "content-encoding")


class RemoteMediaUnavailable(ValueError):
    pass


def cache_for(contract):
    return RemoteMediaCache(Path(contract.candidate_root) / "remote-media")


class RemoteMediaCache:
    """身份键不含签名 URL；磁盘命中无需重新解析来源。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    def folder(self, key: str) -> Path:
        return self.root / hashlib.sha256(key.encode()).hexdigest()

    def _slot(self, key: str, byte_range: str = "") -> Path:
        name = hashlib.sha256(byte_range.encode()).hexdigest() if byte_range else "full"
        return self.folder(key) / name

    def stored(self, key: str, byte_range: str = ""):
        for slot in dict.fromkeys((self._slot(key), self._slot(key, byte_range))):
            try:
                metadata = json.loads(slot.with_suffix(".json").read_text(encoding="utf-8"))
                path = slot.with_suffix(".bin")
                if path.is_file() and path.stat().st_size == metadata["size"]:
                    return path, metadata
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def response(self, key: str, request, *, headers=None):
        found = self.stored(key, request.headers.get("range", ""))
        if not found:
            return None
        path, metadata = found
        response_headers = {**metadata["headers"], "cache-control": "private, max-age=31536000", **(headers or {})}
        if metadata["status"] == 200:
            response_headers.pop("content-length", None)
            return FileResponse(path, media_type=response_headers.pop("content-type", None), headers=response_headers)
        if request.method == "HEAD":
            return Response(status_code=206, headers=response_headers)
        def body():
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    yield chunk
        return StreamingResponse(body(), status_code=206, headers=response_headers)

    def stream(self, key: str, request, fetch, *, headers=None):
        """流式写入不等待整片下载；中断、长度不符与错误页不成为缓存命中。"""
        hit = self.response(key, request, headers=headers)
        if hit is not None:
            return hit
        folder = self.folder(key)
        folder.mkdir(parents=True, exist_ok=True)
        lock = FileLock(folder / "fetch.lock", thread_local=False)
        lock.acquire(timeout=120)
        upstream = None
        def close():
            if upstream is not None:
                upstream.close()
            lock.release()
        try:
            hit = self.response(key, request, headers=headers)
            if hit is not None:
                close()
                return hit
            upstream = fetch()
            forwarded = {name: upstream.headers[name] for name in MEDIA_HEADERS if name in upstream.headers}
            forwarded.update({"cache-control": "private, max-age=31536000", **(headers or {})})
            kind = upstream.headers.get("content-type", "").lower()
            accepted = (upstream.status_code in (200, 206) and
                        (kind.startswith(("image/", "video/", "audio/")) or
                         kind.split(";")[0] in ("application/octet-stream", "binary/octet-stream")))
            content_range = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", forwarded.get("content-range", ""))
            if upstream.status_code == 206 and not content_range:
                accepted = False
            byte_range = request.headers.get("range", "") if upstream.status_code == 206 else ""
            complete_range = bool(content_range and int(content_range[1]) == 0
                                  and int(content_range[2]) + 1 == int(content_range[3]))
            if complete_range:
                byte_range = ""
            elif upstream.status_code == 206 and not byte_range:
                accepted = False
            if not accepted:
                forwarded["cache-control"] = "no-store"
            if request.method == "HEAD":
                response = Response(status_code=upstream.status_code, headers=forwarded)
                close()
                return response
            slot = self._slot(key, byte_range)
            def body():
                try:
                    yield from self._capture_response(slot, upstream, forwarded, accepted=accepted,
                        content_range=content_range, complete_range=complete_range)
                finally:
                    close()
            return StreamingResponse(body(), status_code=upstream.status_code, headers=forwarded,
                                     background=BackgroundTask(close))
        except BaseException:
            close()
            raise

    def _capture_response(self, slot, upstream, headers, *, accepted, content_range, complete_range):
        """安装一次完整传输的字节与范围信息，未完成的临时文件由原子写入清除。"""
        if not accepted:
            yield from upstream.iter_raw()
            return
        with atomic_path(slot.with_suffix(".bin")) as temporary:
            size = 0
            with temporary.open("wb") as handle:
                for chunk in upstream.iter_raw():
                    handle.write(chunk)
                    size += len(chunk)
                    yield chunk
            expected = int(headers.get("content-length", size))
            if not size or size != expected or (content_range and
                    size != int(content_range[2]) - int(content_range[1]) + 1):
                raise OSError("媒体响应长度不完整")
        atomic_write_text(slot.with_suffix(".json"), json.dumps({
            "status": 200 if complete_range else upstream.status_code, "size": size, "headers": {
                name: value for name, value in headers.items() if name in MEDIA_HEADERS
                and not (complete_range and name == "content-range")},
        }), mode=0o600)

    def bytes(self, key: str, fetch, *, ttl: int | None = None) -> bytes:
        """图片与清单复用原子文件及跨进程锁；刷新失败保留可用副本。"""
        path = self._slot(key).with_suffix(".data")
        failed = path.with_suffix(".failed")
        def fresh():
            return path.is_file() and path.stat().st_size > 0 and (ttl is None or time.time() - path.stat().st_mtime < ttl)
        if fresh():
            return path.read_bytes()
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(path.with_suffix(".lock")):
            if fresh():
                return path.read_bytes()
            if failed.is_file() and time.time() - failed.stat().st_mtime < 3600:
                if path.is_file():
                    return path.read_bytes()
                raise RemoteMediaUnavailable("媒体未取得")
            try:
                data = fetch()
                if not data:
                    raise RemoteMediaUnavailable("媒体未取得")
                with atomic_path(path) as temporary:
                    temporary.write_bytes(data)
                failed.unlink(missing_ok=True)
                return data
            except Exception:
                failed.write_bytes(b"")
                if path.is_file():
                    return path.read_bytes()
                raise
