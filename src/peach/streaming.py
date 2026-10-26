from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import MutableSet
from pathlib import Path
from time import monotonic
from typing import Any

from starlette.responses import FileResponse, Response
from starlette.types import Receive, Scope, Send

RANGE_PREFIX = "bytes="


class StreamSessionRegistry:
    """Track addressable browser stream requests so the UI can cancel them."""

    def __init__(self, *, tombstone_seconds: float = 300.0) -> None:
        self._active: dict[str, MutableSet[asyncio.Task[object]]] = {}
        self._processes: dict[str, MutableSet[Any]] = {}
        self._cancelled: dict[str, float] = {}
        self._tombstone_seconds = tombstone_seconds
        self._lock = threading.RLock()

    def _prune(self, now: float) -> None:
        expired = [
            session for session, cancelled_at in self._cancelled.items()
            if now - cancelled_at >= self._tombstone_seconds
        ]
        for session in expired:
            self._cancelled.pop(session, None)

    def register(self, session: str, task: asyncio.Task[object]) -> bool:
        with self._lock:
            self._prune(monotonic())
            if session in self._cancelled:
                return False
            self._active.setdefault(session, set()).add(task)
            return True

    def register_process(self, session: str, process: Any) -> bool:
        """把生成 HLS 片段的子进程纳入同一个可取消会话。"""
        with self._lock:
            self._prune(monotonic())
            if session in self._cancelled:
                return False
            self._processes.setdefault(session, set()).add(process)
            return True

    def unregister_process(self, session: str, process: Any) -> None:
        with self._lock:
            processes = self._processes.get(session)
            if not processes:
                return
            processes.discard(process)
            if not processes:
                self._processes.pop(session, None)

    def is_cancelled(self, session: str) -> bool:
        with self._lock:
            self._prune(monotonic())
            return session in self._cancelled

    def unregister(self, session: str, task: asyncio.Task[object]) -> None:
        with self._lock:
            tasks = self._active.get(session)
            if not tasks:
                return
            tasks.discard(task)
            if not tasks:
                self._active.pop(session, None)

    def cancel(self, session: str) -> int:
        with self._lock:
            self._prune(monotonic())
            self._cancelled[session] = monotonic()
            tasks = tuple(self._active.pop(session, ()))
            processes = tuple(self._processes.pop(session, ()))
        for task in tasks:
            task.cancel()
        for process in processes:
            try:
                if process.returncode is None:
                    process.kill()
            except (OSError, ProcessLookupError):
                pass
        return len(tasks) + len(processes)

    def active_count(self, session: str) -> int:
        with self._lock:
            return len(self._active.get(session, ()))


async def _wait_for_disconnect(receive: Receive) -> None:
    """等到客户端断开。请求体读完后 uvicorn 的 receive() 只会在断开时再返回一次。"""
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return


async def _send_until_disconnect(inner, scope: Scope, receive: Receive, send: Send) -> None:
    """跑 `inner` 发响应，客户端一断开就把它掐掉。"""
    respond = asyncio.ensure_future(inner(scope, receive, send))
    watch = asyncio.ensure_future(_wait_for_disconnect(receive))
    try:
        await asyncio.wait({respond, watch}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        respond.cancel()
        watch.cancel()
        await asyncio.gather(respond, watch, return_exceptions=True)
    if not respond.cancelled():
        respond.result()


class BufferedFileResponse(FileResponse):
    """按 1 MiB 读文件再发，客户端一断开就停止读。

    Starlette 默认每次读 64 KiB，浏览器缓冲一段 Range 要向 CloudDrive 发几十次小读；
    读大一点只是少跑几趟挂载层，Range 语义不变。

    浏览器拖动进度条时会掐掉旧的开区间 Range 请求。uvicorn 在连接断开后 send() 只是静默
    返回，Starlette 的 FileResponse 又不监听断开事件，读文件的循环会一直跑到文件末尾：
    一部 5 GB 的片子每拖一次就多一个幽灵读者，通过挂载把剩下的几 GB 全拉一遍，新位置那
    一块只能排在它们后面（2026-09-13 实测，断开后 15 秒继续下行 221 MB 直到文件结束）。
    所以这里自己盯 http.disconnect，收到就取消发送任务，最多多读一块。
    """

    chunk_size = 1 << 20

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return
        await _send_until_disconnect(super().__call__, scope, receive, send)


class CancellableFileResponse(BufferedFileResponse):
    """能按会话取消正在发送任务的 FileResponse。"""

    def __init__(
        self,
        path: str | Path,
        *,
        session: str,
        registry: StreamSessionRegistry,
        media_type: str | None = None,
    ) -> None:
        super().__init__(path, media_type=media_type)
        self.session = session
        self.registry = registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        task = asyncio.current_task()
        if task is None or not self.registry.register(self.session, task):
            await Response(status_code=410, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        try:
            await super().__call__(scope, receive, send)
        except asyncio.CancelledError:
            logging.getLogger(__name__).info("stream session cancelled: %s", self.session)
            raise
        finally:
            self.registry.unregister(self.session, task)


def _overlap(blob: bytes, base: int, start: int, end: int):
    """`blob` 铺在虚拟文件的 `base` 处，取它和闭区间 [start, end] 相交的那一段。"""
    begin = max(start, base)
    stop = min(end + 1, base + len(blob))
    if begin < stop:
        yield blob[begin - base:stop - base]


def _requested_range(scope: Scope, total: int) -> tuple[int, int, bool] | None:
    """解析单段 Range，返回 (起, 止, 是不是按 Range 答的)；要不起的区间返回 None。

    末位的布尔值决定回 206 还是 200：整份内容也要回 206，有播放器把 200 读成「这个
    服务端不支持 Range」，接着就把拖动关掉。
    多段 Range 当没有 Range 处理：浏览器播视频从不这么请求，为它写一套 multipart
    没有真实需求。
    """
    raw = ""
    for key, value in scope.get("headers", ()):
        if key.lower() == b"range":
            raw = value.decode("latin-1").strip()
            break
    if not raw.startswith(RANGE_PREFIX) or "," in raw:
        return 0, total - 1, False
    first, _, last = raw[len(RANGE_PREFIX):].partition("-")
    try:
        if not first:
            length = int(last)
            if length <= 0:
                return 0, total - 1, False
            return total - min(length, total), total - 1, True
        start = int(first)
        end = int(last) if last else total - 1
    except ValueError:
        return 0, total - 1, False
    end = min(end, total - 1)
    return None if start > end or start >= total else (start, end, True)


class SplicedMp4Response(Response):
    """把「修好的头 + 原文件里那一段 mdat」当成一个虚拟文件发出去。

    Starlette 的 `FileResponse` 按磁盘上那个文件的大小和偏移切 Range，而这里要发的字节
    序列在磁盘上并不存在：头在边车里，内容是原文件中间的一段。所以 Range 自己算。
    断开就停读，理由和 `BufferedFileResponse` 一样。
    """

    chunk_size = 1 << 20

    def __init__(
        self,
        path: str | Path,
        header,
        *,
        session: str = "",
        registry: StreamSessionRegistry | None = None,
    ) -> None:
        super().__init__(b"", media_type="video/mp4")
        self.path = Path(path)
        self.header = header
        self.session = session
        self.registry = registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return
        task = asyncio.current_task()
        tracked = bool(self.session and self.registry is not None)
        if tracked and (task is None or not self.registry.register(self.session, task)):
            await Response(status_code=410, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        try:
            await _send_until_disconnect(self._respond, scope, receive, send)
        except asyncio.CancelledError:
            logging.getLogger(__name__).info("stream session cancelled: %s", self.session)
            raise
        finally:
            if tracked and task is not None:
                self.registry.unregister(self.session, task)

    async def _respond(self, scope: Scope, receive: Receive, send: Send) -> None:
        total = self.header.size
        window = _requested_range(scope, total)
        if window is None:
            await Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{total}", "Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        start, end, ranged = window
        headers = [
            (b"content-type", b"video/mp4"),
            (b"accept-ranges", b"bytes"),
            (b"cache-control", b"no-store"),
            (b"content-length", str(end - start + 1).encode("latin-1")),
        ]
        if ranged:
            headers.append((b"content-range", f"bytes {start}-{end}/{total}".encode("latin-1")))
        await send({
            "type": "http.response.start",
            "status": 206 if ranged else 200,
            "headers": headers,
        })
        if scope.get("method", "GET").upper() == "HEAD":
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        stream = self._chunks(start, end)
        try:
            async for chunk in stream:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        finally:
            await stream.aclose()
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _chunks(self, start: int, end: int):
        """按虚拟文件的坐标取字节：先头、再原文件那一段、最后尾。"""
        body_start = len(self.header.prefix)
        body_end = body_start + (self.header.payload_end - self.header.payload_start)
        for chunk in _overlap(self.header.prefix, 0, start, end):
            yield chunk
        if end >= body_start and start < body_end:
            async for chunk in self._source_chunks(
                max(start, body_start) - body_start, min(end + 1, body_end) - body_start,
            ):
                yield chunk
        for chunk in _overlap(self.header.suffix, body_end, start, end):
            yield chunk

    async def _source_chunks(self, begin: int, stop: int):
        """整段共用一个句柄：挂载盘上每开一次文件都要多走一趟握手。"""
        handle = await asyncio.to_thread(open, self.path, "rb")
        try:
            await asyncio.to_thread(handle.seek, self.header.payload_start + begin)
            remaining = stop - begin
            while remaining > 0:
                chunk = await asyncio.to_thread(handle.read, min(self.chunk_size, remaining))
                if not chunk:
                    return
                yield chunk
                remaining -= len(chunk)
        finally:
            handle.close()
