from __future__ import annotations

import asyncio
import unittest

from peach.streaming import CancellableFileResponse, StreamSessionRegistry
from peach.mp4index import segment_plan
from peach.segments import build_hls_playlist


async def _collect(response, requested: bytes | None, method: str = "GET"):
    """把一个 ASGI 响应跑完，返回 (状态码, 响应头, 响应体)。"""
    messages: list[dict] = []

    async def receive() -> dict:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        messages.append(message)

    await response({
        "type": "http", "method": method, "path": "/stream",
        "headers": [(b"range", requested)] if requested else [],
    }, receive, send)
    start = messages[0]
    return (
        start["status"],
        {key.decode(): value.decode() for key, value in start["headers"]},
        b"".join(message.get("body", b"") for message in messages[1:]),
    )


class StreamSessionRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_stops_every_active_request_in_the_session(self):
        registry = StreamSessionRegistry()
        started = asyncio.Event()

        async def wait_for_cancel() -> None:
            task = asyncio.current_task()
            assert task is not None
            self.assertTrue(registry.register("detail-1", task))
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                registry.unregister("detail-1", task)

        task = asyncio.create_task(wait_for_cancel())
        await started.wait()
        self.assertEqual(registry.active_count("detail-1"), 1)
        self.assertEqual(registry.cancel("detail-1"), 1)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(registry.active_count("detail-1"), 0)

    async def test_cancelled_session_rejects_late_range_requests(self):
        registry = StreamSessionRegistry()
        registry.cancel("detail-1")
        task = asyncio.current_task()
        assert task is not None
        self.assertFalse(registry.register("detail-1", task))

    async def test_open_ended_range_keeps_standard_http_semantics(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote.mp4"
            path.write_bytes(bytes(range(64)))
            registry = StreamSessionRegistry()
            response = CancellableFileResponse(
                path, session="detail-2", registry=registry,
            )
            messages: list[dict] = []

            async def receive() -> dict:
                # 服务器的 receive() 在请求体读完后就一直挂着，直到客户端断开。
                await asyncio.Event().wait()
                return {"type": "http.disconnect"}

            async def send(message: dict) -> None:
                messages.append(message)

            scope = {
                "type": "http", "method": "GET", "path": "/stream",
                "headers": [(b"range", b"bytes=0-")],
            }
            await response(scope, receive, send)

        start = messages[0]
        headers = {key.decode(): value.decode() for key, value in start["headers"]}
        body = b"".join(message.get("body", b"") for message in messages[1:])
        self.assertEqual(start["status"], 206)
        self.assertEqual(headers["content-range"], "bytes 0-63/64")
        self.assertEqual(headers["content-length"], "64")
        self.assertEqual(body, bytes(range(64)))
        self.assertEqual(registry.active_count("detail-2"), 0)

    async def test_client_disconnect_stops_reading_the_rest_of_the_file(self):
        """拖动进度条掐掉旧 Range 请求后，服务端不能把文件剩下的部分读到末尾。

        uvicorn 断开后 send() 静默返回，所以这里的 send 也照单全收；能证明停下来的
        只有「断开后发出的块数」：8 块的文件，断在第 1 块之后，读到的块数必须远少于 8。
        """
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote.mp4"
            chunk = 1024
            path.write_bytes(bytes(range(256)) * 4 * 8)
            registry = StreamSessionRegistry()
            response = CancellableFileResponse(
                path, session="detail-3", registry=registry,
            )
            response.chunk_size = chunk
            bodies: list[bytes] = []
            disconnected = asyncio.Event()

            async def receive() -> dict:
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def send(message: dict) -> None:
                if message["type"] != "http.response.body":
                    return
                bodies.append(message["body"])
                if len(bodies) == 1:
                    disconnected.set()
                    # 让监听断开的任务先跑起来，模拟浏览器在这一块发出后关闭连接。
                    await asyncio.sleep(0)

            scope = {
                "type": "http", "method": "GET", "path": "/stream",
                "headers": [(b"range", b"bytes=0-")],
            }
            await response(scope, receive, send)

        self.assertGreaterEqual(len(bodies), 1)
        self.assertLess(len(bodies), 4, "断开后仍把文件读到了末尾")
        self.assertEqual(bodies[0], bytes(range(256)) * 4)
        self.assertEqual(registry.active_count("detail-3"), 0)

    def test_range_bodies_are_read_one_mebibyte_at_a_time(self):
        """Starlette 默认 64 KiB 一读，浏览器缓冲一段 Range 要跑几十趟 CloudDrive 挂载层。"""
        from peach.streaming import BufferedFileResponse

        self.assertEqual(BufferedFileResponse.chunk_size, 1 << 20)
        self.assertEqual(CancellableFileResponse.chunk_size, 1 << 20)

    async def test_a_spliced_file_serves_ranges_across_the_seam(self):
        """头在边车里、内容在原文件中间，跨接缝的 Range 必须拼出连续的字节。"""
        import tempfile
        from pathlib import Path

        from peach.mp4repair import RepairedHeader
        from peach.streaming import SplicedMp4Response

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote.mp4"
            path.write_bytes(b"OLDHEAD" + bytes(range(64)) + b"TAIL")
            header = RepairedHeader(
                prefix=b"NEWHEADER", payload_start=7, payload_end=71, suffix=b"END")
            whole = b"NEWHEADER" + bytes(range(64)) + b"END"

            for label, requested, status, expected in (
                ("没有 Range 头", None, 200, whole),
                ("整份也按 Range 答", b"bytes=0-", 206, whole),
                ("跨头与内容的接缝", b"bytes=5-12", 206, whole[5:13]),
                ("只要内容", b"bytes=20-30", 206, whole[20:31]),
                ("开区间到尾", b"bytes=70-", 206, whole[70:]),
                ("末尾若干字节", b"bytes=-4", 206, whole[-4:]),
            ):
                with self.subTest(label):
                    status_code, headers, body = await _collect(
                        SplicedMp4Response(path, header), requested)
                    self.assertEqual(status_code, status)
                    self.assertEqual(body, expected)
                    self.assertEqual(headers["content-length"], str(len(expected)))
                    self.assertEqual(headers["accept-ranges"], "bytes")

            status_code, headers, body = await _collect(
                SplicedMp4Response(path, header), b"bytes=999-")
            self.assertEqual(status_code, 416)
            self.assertEqual(headers["content-range"], f"bytes */{len(whole)}")

    async def test_a_spliced_head_request_reports_the_size_without_a_body(self):
        import tempfile
        from pathlib import Path

        from peach.mp4repair import RepairedHeader
        from peach.streaming import SplicedMp4Response

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote.mp4"
            path.write_bytes(b"OLDHEAD" + bytes(range(64)))
            header = RepairedHeader(prefix=b"NEWHEADER", payload_start=7, payload_end=71)
            status_code, headers, body = await _collect(
                SplicedMp4Response(path, header), None, method="HEAD")

        self.assertEqual(status_code, 200)
        self.assertEqual(headers["content-length"], "73")
        self.assertEqual(headers["content-type"], "video/mp4")
        self.assertEqual(body, b"")

    def test_hls_playlist_is_time_addressable_without_full_file_ranges(self):
        # 分片边界由真实关键帧决定，不再按固定秒数等分（见 tests/test_segments.py）。
        plan = segment_plan([0.0, 6.0, 12.0], 13.5, 6)
        self.assertEqual(plan, [(0.0, 6.0), (6.0, 6.0), (12.0, 1.5)])
        playlist = build_hls_playlist(plan, lambda index: f"/seg/{index}.ts")
        self.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", playlist)
        self.assertIn("/seg/0.ts", playlist)
        self.assertIn("/seg/2.ts", playlist)
        self.assertEqual(playlist.count("#EXTINF:"), 3)
