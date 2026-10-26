"""性能路径的契约：响应压缩、页面资产复验、related 缓存与封面索引。

这四项都是「结果不变、只是更快」的改动，所以它们的门槛全是负向的：压缩不许碰
媒体流，复验不许改更新语义，缓存不许在账本写入后继续端旧结果，封面索引不许丢掉
`is_file()` 那层大小写容错。少了任何一条，回归都不会表现成报错，只会表现成用户
看到旧数据或者播放坏掉。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from support.ledger import fresh_ledger

HAS_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
if HAS_DEPS:
    import httpx
    from fastapi import FastAPI
    from fastapi.responses import Response, StreamingResponse
    from starlette.middleware.gzip import GZipMiddleware
    from peach.api import COMPRESSION_EXCLUDED_TYPES, create_app
    from peach.config import PeachSettings
    from peach.web_catalog import q_related
    from peach.web_state import WebContract


def _settings(root: Path, page: Path, **overrides) -> "PeachSettings":
    """把每个受管目录都指到临时目录下。

    默认值指向本机真实的 `peach-data/generated`，漏一个就是测试去读真实数据。
    """
    return PeachSettings(
        db_path=root / "ledger.db", token="", page_path=page,
        vendor_path=root / "vendor", allowed_media_roots=(root / "media",),
        snapshot_root=root / "snapshots",
        cover_root=root / "covers", poster_root=root / "posters",
        avatar_root=root / "avatars", logo_root=root / "logos",
        transcode_root=root / "transcodes", stream_root=root / "stream",
        photo_root=root / "photos", candidate_root=root / "generated",
        ffmpeg_root=root / "ffmpeg", follow_state_root=root / "state",
        taste_history_store=root / "taste.sqlite",
        taste_history_import_root=root / "taste-imports",
        taste_history_output_root=root / "taste-output",
        taste_history_manifest=root / "taste-manifest.json",
        **overrides,
    )


@unittest.skipUnless(HAS_DEPS, "FastAPI/httpx 尚未安装")
class CompressionGateTests(unittest.IsolatedAsyncioTestCase):
    """压缩按 Content-Type 决定，不按路由名单，所以每个闸门都要有一条对例。

    这里测的是 Peach 实际装上去的那份配置（`COMPRESSION_EXCLUDED_TYPES`），不是
    Starlette 的默认值：默认名单排掉了 `video/*` 和已压缩的图片，但没排
    `application/octet-stream` 和 `text/plain`，而 `FileResponse` 猜不出扩展名时
    正是回落到 `text/plain`——`/stream`、`/photo` 都不传 media_type。
    """

    async def asyncSetUp(self):
        app = FastAPI()

        @app.get("/big-json")
        def big_json():
            return {"text": "桃" * 2000}

        @app.get("/small-json")
        def small_json():
            return {"ok": 1}

        @app.get("/video")
        def video():
            return Response(b"\x00" * 10000, media_type="video/mp4")

        @app.get("/guessed-binary")
        def guessed_binary():
            # FileResponse 对没登记过的扩展名就是这个头，内容其实是媒体字节。
            return Response(b"\x00" * 10000, media_type="text/plain")

        @app.get("/octet")
        def octet():
            return Response(b"\x00" * 10000, media_type="application/octet-stream")

        @app.get("/partial")
        def partial():
            return Response(b"abc" * 1000, status_code=206,
                            headers={"content-range": "bytes 0-2999/9000"})

        @app.get("/pre-encoded")
        def pre_encoded():
            return Response(b"already" * 500, headers={"content-encoding": "identity"})

        @app.get("/icon")
        def icon():
            return Response("<svg>" + "x" * 5000, media_type="image/svg+xml")

        @app.get("/playlist")
        def playlist():
            return Response("#EXTM3U\n" + "#EXTINF\n" * 800,
                            media_type="application/vnd.apple.mpegurl")

        @app.get("/chunked")
        async def chunked():
            async def generate():
                yield "a" * 2000
                yield "b" * 2000
            # 用 text/csv 而不是 text/plain：后者在 Peach 的排除名单里（见上面
            # 那条「猜错成文本的媒体」），拿它测流式压缩会测成「被排除了」。
            return StreamingResponse(generate(), media_type="text/csv; charset=utf-8")

        app.add_middleware(
            GZipMiddleware, exclude_content_types=COMPRESSION_EXCLUDED_TYPES)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_large_json_is_compressed_and_the_body_survives_the_round_trip(self):
        response = await self.client.get(
            "/big-json", headers={"accept-encoding": "gzip, deflate"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertIn("Accept-Encoding", response.headers.get("vary", ""))
        self.assertEqual(response.json(), {"text": "桃" * 2000})

    async def test_a_client_that_does_not_accept_gzip_still_gets_the_plain_body(self):
        # httpx 默认自带 `accept-encoding: gzip, deflate`，要显式换成 identity
        # 才测得到「客户端不接受」这条闸门。
        response = await self.client.get(
            "/big-json", headers={"accept-encoding": "identity"})
        self.assertNotIn("content-encoding", response.headers)
        self.assertEqual(response.json(), {"text": "桃" * 2000})

    async def test_small_bodies_are_left_alone(self):
        response = await self.client.get("/small-json", headers={"accept-encoding": "gzip"})
        self.assertNotIn("content-encoding", response.headers)

    async def test_media_and_byte_streams_are_never_compressed(self):
        """三种都是「压了纯烧 CPU」：声明的媒体、通用字节流、猜错成文本的媒体。"""
        for path in ("/video", "/octet", "/guessed-binary"):
            response = await self.client.get(path, headers={"accept-encoding": "gzip"})
            self.assertNotIn("content-encoding", response.headers, path)
            self.assertEqual(response.content, b"\x00" * 10000, path)

    async def test_range_slices_and_already_encoded_bodies_pass_through(self):
        """206 被压掉会让 Content-Range 和实际字节数对不上，播放器直接放弃。"""
        partial = await self.client.get("/partial", headers={"accept-encoding": "gzip"})
        self.assertEqual(partial.status_code, 206)
        self.assertNotIn("content-encoding", partial.headers)

        encoded = await self.client.get("/pre-encoded", headers={"accept-encoding": "gzip"})
        self.assertEqual(encoded.headers.get("content-encoding"), "identity")

    async def test_text_shaped_payloads_are_compressed_even_when_the_type_is_unusual(self):
        """图标和 HLS 播放列表都是大段文本，被排除名单误伤就白丢一次收益。"""
        for path, opening in (("/icon", "<svg>"), ("/playlist", "#EXTM3U")):
            response = await self.client.get(path, headers={"accept-encoding": "gzip"})
            self.assertEqual(response.headers.get("content-encoding"), "gzip", path)
            self.assertTrue(response.text.startswith(opening), path)

    async def test_streaming_text_is_compressed_chunk_by_chunk(self):
        response = await self.client.get("/chunked", headers={"accept-encoding": "gzip"})
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertNotIn("content-length", response.headers)
        self.assertEqual(response.text, "a" * 2000 + "b" * 2000)


@unittest.skipUnless(HAS_DEPS, "FastAPI/httpx 尚未安装")
class PageAssetDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """真实 app 上的资产交付：ETag 复验 + gzip，`index.html` 仍是 no-store。

    压缩在这里再验一遍，是因为上面那组用的是自己搭的 app：中间件那一行从
    `create_app` 里掉了也不会让它变红。
    """

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        (root / "dist").mkdir()
        self.app_js = root / "dist" / "peach-app.js"
        # 要大于 gzip 的 minimum_size，否则压缩那条断言测的是「太小所以没压」。
        self.app_js.write_text(
            "const peach=1;export default peach;\n" + "// filler\n" * 200,
            encoding="utf-8")
        # `/app.css` 拼的是 `web/css/` 下的分区而不是单个文件，所以这里也铺两份：
        # 一份足够大让 gzip 生效，第二份用来验「按文件名顺序拼、改哪份都换 ETag」。
        (root / "css").mkdir()
        self.css_parts = [root / "css" / "01-base.css", root / "css" / "02-cards.css"]
        self.css_parts[0].write_text("body{margin:0}\n" + "/* filler */\n" * 200,
                                     encoding="utf-8")
        self.css_parts[1].write_text(".card{display:grid}\n", encoding="utf-8")
        (root / "board.css").write_text(".ptoggle{width:42px}\n", encoding="utf-8")
        page = root / "index.html"
        page.write_text("<!doctype html><title>Peach test</title>", encoding="utf-8")
        (root / "dist" / "peach-app.css").write_text(
            ".peach-react{display:block}\n" + "/* filler */\n" * 200, encoding="utf-8")
        (root / "dist" / "peach-pages.js").write_text("export const ok=1;", encoding="utf-8")
        (root / "dist" / "peach-pages.css").write_text(".page{}", encoding="utf-8")
        fresh_ledger(root)
        self.app = create_app(_settings(root, page))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_main_script_revalidates_instead_of_being_refetched(self):
        first = await self.client.get("/dist/peach-app.js")
        self.assertEqual(first.status_code, 200)
        self.assertIn("export default peach", first.text)
        # no-cache 不是「不缓存」：它是「每次回源问一次」，与 no-store 的更新语义
        # 相同，区别只在没变时不必重传。
        self.assertEqual(first.headers["cache-control"], "no-cache")
        etag = first.headers["etag"]

        cached = await self.client.get("/dist/peach-app.js", headers={"if-none-match": etag})
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.content, b"")
        self.assertEqual(cached.headers["etag"], etag)

        # 文件一变，同一个 ETag 必须立刻失效——这是复验能替掉 no-store 的全部前提。
        self.app_js.write_text("const peach=2;export default peach;", encoding="utf-8")
        updated = await self.client.get("/dist/peach-app.js", headers={"if-none-match": etag})
        self.assertEqual(updated.status_code, 200)
        self.assertNotEqual(updated.headers["etag"], etag)
        self.assertIn("const peach=2", updated.text)

    async def test_every_asset_route_shares_the_same_revalidation_contract(self):
        for path in ("/app.css", "/board.css", "/dist/peach-app.js", "/dist/peach-app.css",
                     "/dist/peach-pages.js", "/dist/peach-pages.css"):
            first = await self.client.get(path)
            self.assertEqual(first.status_code, 200, path)
            self.assertEqual(first.headers["cache-control"], "no-cache", path)
            again = await self.client.get(
                path, headers={"if-none-match": first.headers["etag"]})
            self.assertEqual(again.status_code, 304, path)
            self.assertEqual(again.content, b"", path)

    async def test_app_css_is_its_partitions_joined_in_cascade_order(self):
        """`/app.css` 是 `web/css/` 分区的顺序拼接，改任何一份都立刻失效。

        拆分只减少改动撞车，交付的字节必须逐字不变；顺序也必须是文件名顺序，
        层叠顺序错了样式表照样「加载成功」，只是显示是错的。
        ETag 也必须覆盖到全部分区：只看第一份的 mtime，改后面的分区会命中旧缓存。
        """
        first = await self.client.get("/app.css")
        self.assertEqual(first.status_code, 200)
        # 按字节比：拼接发生在字节层面，`read_text` 会把 Windows 的 CRLF 归一成 LF，
        # 拿解码后的文本比等于把「交付的字节是否逐字相同」这件事绕过去。
        self.assertEqual(
            first.content,
            b"".join(part.read_bytes() for part in self.css_parts))
        self.assertLess(first.text.index("body{margin:0}"),
                        first.text.index(".card{display:grid}"))
        etag = first.headers["etag"]

        # 改的是最后一份分区，并且换了字节数：这一条要能在只看单个文件的实现上变红。
        self.css_parts[-1].write_text(".card{display:flex;gap:2px}\n", encoding="utf-8")
        updated = await self.client.get("/app.css", headers={"if-none-match": etag})
        self.assertEqual(updated.status_code, 200)
        self.assertNotEqual(updated.headers["etag"], etag)
        self.assertIn(".card{display:flex;gap:2px}", updated.text)

    async def test_the_page_itself_stays_no_store(self):
        """资产 URL 全部由 index.html 给出，它自己被缓存住就没人看得到新资产。"""
        response = await self.client.get("/")
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_the_real_app_compresses_its_scripts_and_styles(self):
        for path in ("/dist/peach-app.js", "/dist/peach-app.css", "/app.css"):
            response = await self.client.get(path, headers={"accept-encoding": "gzip"})
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.headers.get("content-encoding"), "gzip", path)


class KeyedCacheTests(unittest.TestCase):
    """`cached_lru`：TTL 命中、LRU 驱逐、代次失效，三条语义各自一个门槛。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.contract = WebContract(Path(tmp.name).resolve() / "ledger.db")

    def test_the_same_key_is_only_computed_once_inside_the_ttl(self):
        calls = []

        def compute():
            calls.append(1)
            return {"value": len(calls)}

        self.assertEqual(self.contract.cached_lru("k", compute), {"value": 1})
        self.assertEqual(self.contract.cached_lru("k", compute), {"value": 1})
        self.assertEqual(len(calls), 1)

    def test_the_least_recently_used_key_is_evicted_past_maxsize(self):
        """限界是这个缓存存在的理由：键跟着浏览过的资产走，不封闭。"""
        calls: dict[str, int] = {}

        def compute_for(key):
            def run():
                calls[key] = calls.get(key, 0) + 1
                return key
            return run

        lru = self.contract.cached_lru
        lru("a", compute_for("a"), maxsize=2)
        lru("b", compute_for("b"), maxsize=2)
        lru("c", compute_for("c"), maxsize=2)      # 最旧的 a 被逐出
        lru("b", compute_for("b"), maxsize=2)      # b 仍在，命中
        lru("a", compute_for("a"), maxsize=2)      # a 已被逐出，必须重算
        self.assertEqual(calls, {"a": 2, "b": 1, "c": 1})

    def test_cache_bust_clears_the_keyed_cache_too(self):
        calls = []
        self.contract.cached_lru("k", lambda: calls.append(1))
        self.contract.cache_bust()
        self.contract.cached_lru("k", lambda: calls.append(1))
        self.assertEqual(len(calls), 2)

    def test_a_generation_bump_during_the_call_discards_the_stale_result(self):
        """算的时候账本被改了，这次结果就是失效前的快照，不能写回缓存。"""
        calls = []

        def compute_and_bust():
            calls.append(1)
            self.contract.cache_bust()
            return len(calls)

        self.contract.cached_lru("k", compute_and_bust)
        self.assertEqual(self.contract.keyed_cache, {})


@unittest.skipUnless(HAS_DEPS, "FastAPI/httpx 尚未安装")
class RelatedEndpointCacheTests(unittest.IsolatedAsyncioTestCase):
    """`/api/related` 必须缓存，而写路径的 `cache_bust()` 必须让它作废。

    后半句才是真正的门槛：推荐列表端旧数据的表现是「标了标签之后接着看没变」，
    不报错，只是看起来像功能没生效。
    """

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        db = fresh_ledger(root)
        with closing(sqlite3.connect(db)) as con:
            con.execute(
                "INSERT INTO asset(id,location,path,name,medium,size,duration,"
                "width,height,first_seen) "
                "VALUES(1,'local',?,'one.mp4','video',100,90.0,1920,1080,'2026-08-14')",
                (str(root / "media" / "one.mp4"),),
            )
            con.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,"
                "created_at,updated_at) "
                "VALUES(1,'performer','Alice','alice','2026-08-14','2026-08-14')")
            con.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                "VALUES(1,1,'performer','test',1.0)")
            con.commit()
        page = root / "index.html"
        page.write_text("<!doctype html><title>Peach test</title>", encoding="utf-8")
        (root / "covers").mkdir()
        self.app = create_app(_settings(root, page))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_repeated_requests_only_rank_once_until_the_ledger_changes(self):
        # patch 打在 `web_router` 上，不是 `web_contract`：那个 shim 只是再导出，
        # 处理器读的是自己模块里的名字。
        with patch("peach.web_router.q_related", side_effect=q_related) as spy:
            first = await self.client.get("/api/related?id=1")
            second = await self.client.get("/api/related?id=1")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(spy.call_count, 1)

        self.app.state.web_contract.cache_bust()
        with patch("peach.web_router.q_related", side_effect=q_related) as spy:
            await self.client.get("/api/related?id=1")
        self.assertEqual(spy.call_count, 1, "cache_bust 之后必须重算")

    async def test_a_different_limit_is_a_different_result(self):
        """限流后的 limit 进了缓存键；不进的话第二次请求会拿到条数不对的列表。"""
        with patch("peach.web_router.q_related", side_effect=q_related) as spy:
            await self.client.get("/api/related?id=1&limit=24")
            await self.client.get("/api/related?id=1&limit=48")
        self.assertEqual(spy.call_count, 2)


class CoverIndexTests(unittest.TestCase):
    """封面索引：一次 scandir 顶掉逐行 stat，且保住 `is_file()` 的大小写容错。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.covers = self.root / "covers"
        self.covers.mkdir()
        self.contract = WebContract(self.root / "ledger.db", cover_root=self.covers)

    def test_presence_and_focus_both_come_out_of_one_scan(self):
        (self.covers / "ABC-001.jpg").write_bytes(b"jpg")
        (self.covers / "ABC-001.face.json").write_text(
            json.dumps({"face": {"cy": 0.42}}), encoding="utf-8")
        (self.covers / "DEF-002.jpg").write_bytes(b"jpg")

        self.assertTrue(self.contract.has_cover("ABC-001"))
        # `is_file()` 在 Windows 与 macOS 的默认文件系统上大小写不敏感，
        # 改走索引不能顺手把这层容错丢掉。
        self.assertTrue(self.contract.has_cover("abc-001"))
        self.assertFalse(self.contract.has_cover("ZZZ-009"))
        self.assertFalse(self.contract.has_cover(None))
        self.assertEqual(self.contract.cover_frame("ABC-001"), {"cy": 0.42})
        self.assertIsNone(self.contract.cover_frame("DEF-002"), "没有 sidecar 就没有取景")
        self.assertIsNone(self.contract.cover_frame(None))

    def test_a_broken_sidecar_only_costs_the_focus_hint(self):
        """取景是纯装饰，读不出就退回固定取景；封面本身还在，不能连它一起判丢。"""
        (self.covers / "ABC-003.jpg").write_bytes(b"jpg")
        (self.covers / "ABC-003.face.json").write_text("{broken", encoding="utf-8")
        self.assertTrue(self.contract.has_cover("ABC-003"))
        self.assertIsNone(self.contract.cover_frame("ABC-003"))

    def test_a_missing_cover_directory_is_an_empty_index_not_an_error(self):
        contract = WebContract(self.root / "ledger.db", cover_root=self.root / "absent")
        self.assertFalse(contract.has_cover("ABC-001"))
        self.assertIsNone(contract.cover_frame("ABC-001"))

    def test_a_freshly_written_cover_appears_without_waiting_for_the_cache(self):
        (self.covers / "ABC-004.jpg").write_bytes(b"jpg")
        self.assertTrue(self.contract.has_cover("ABC-004"))
        scans = []
        original = self.contract._scan_cover_root

        def counted():
            scans.append(1)
            return original()

        with patch.object(self.contract, "_scan_cover_root", side_effect=counted):
            # 目录没变就不重扫，这正是索引省下 stat 的来源。
            self.assertTrue(self.contract.has_cover("ABC-004"))
            self.assertEqual(scans, [])
            # 新文件改了目录的修改时间，索引按目录版本当场重扫。
            (self.covers / "GHI-005.jpg").write_bytes(b"jpg")
            os.utime(self.covers, ns=(0, self.covers.stat().st_mtime_ns + 1_000_000))
            self.assertTrue(self.contract.has_cover("GHI-005"))
            self.assertEqual(scans, [1])


if __name__ == "__main__":
    unittest.main()
