import asyncio
import csv
import importlib.util
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from collections import OrderedDict
from contextlib import asynccontextmanager, closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

from peach import __version__
from support.ledger import fresh_ledger
from support.mp4 import minimal_mp4


ROOT = Path(__file__).resolve().parents[1]


HAS_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
if HAS_DEPS:
    import httpx
    from fastapi.routing import APIRoute
    from starlette.routing import Mount
    from peach import api as api_module
    from peach import routes_auth
    from peach import routes_jav_browser
    from peach.api import create_app
    from peach.follow_covers import PLACEHOLDER_CONTENT_TYPE
    from peach.follow_secrets import credential_store_for
    from peach.config import PeachSettings


BASE_SCHEMA = """
CREATE TABLE asset(
  id INTEGER PRIMARY KEY, location TEXT NOT NULL, path TEXT NOT NULL, name TEXT,
  medium TEXT, size INTEGER, creator TEXT, studio TEXT, series TEXT, code TEXT, release_date TEXT,
  catalog_title TEXT, original_title TEXT,
  duration REAL, width INTEGER, height INTEGER, ctx_length TEXT, ctx_orient TEXT,
  ctx_quality TEXT, play_count INTEGER DEFAULT 0, last_played TEXT, rating INTEGER,
  o_count INTEGER, watch_ratio REAL, stash_scene_id INTEGER, snapshot_path TEXT, first_seen TEXT,
  feedback TEXT, disposal TEXT, disposal_reason TEXT, leave_ratio REAL, play_seconds REAL,
  feedback_at REAL, seek_count INTEGER, max_reached REAL,
  field_owners TEXT, mutation_revision INTEGER NOT NULL DEFAULT 0, region TEXT,
  UNIQUE(location,path));
CREATE TABLE asset_tag(asset_id INTEGER,tag TEXT,confidence REAL DEFAULT 1.0,source TEXT,
                       UNIQUE(asset_id,tag));
CREATE TABLE entity(
  id INTEGER PRIMARY KEY,kind TEXT,canonical_name TEXT,normalized_name TEXT,
  metadata_json TEXT DEFAULT '{}',created_at TEXT,updated_at TEXT,region TEXT,
  UNIQUE(kind,normalized_name));
CREATE TABLE entity_redirect(old_id INTEGER PRIMARY KEY,target_id INTEGER,source TEXT,merged_at TEXT);
CREATE TABLE entity_alias(
  entity_id INTEGER,alias TEXT,normalized_alias TEXT,source TEXT,confidence REAL DEFAULT 1.0);
CREATE TABLE entity_external_ref(
  entity_id INTEGER,provider TEXT,external_kind TEXT,external_id TEXT,
  metadata_json TEXT DEFAULT '{}',last_synced_at TEXT);
CREATE TABLE entity_link(
  id INTEGER PRIMARY KEY,entity_id INTEGER,link_kind TEXT,label TEXT,url TEXT,
  hostname TEXT,is_sensitive INTEGER DEFAULT 0,metadata_json TEXT DEFAULT '{}',
  created_at TEXT,updated_at TEXT);
CREATE TABLE entity_search_term(
  entity_id INTEGER,term TEXT,purpose TEXT,source TEXT,created_at TEXT);
CREATE TABLE asset_entity(
  asset_id INTEGER,entity_id INTEGER,role TEXT,source TEXT,confidence REAL,
  metadata_json TEXT DEFAULT '{}',first_seen_at TEXT,last_seen_at TEXT,
  UNIQUE(asset_id,entity_id,role,source));
CREATE TABLE entity_membership(
  member_id INTEGER PRIMARY KEY,agency_id INTEGER,source TEXT,
  confidence REAL DEFAULT 1.0,checked_at TEXT);
CREATE TABLE label_maker(
  label_id INTEGER PRIMARY KEY,maker_id INTEGER,source TEXT,
  confidence REAL DEFAULT 1.0,checked_at TEXT);
CREATE TABLE performer_profile(entity_id INTEGER PRIMARY KEY,source TEXT);
CREATE TABLE watch_queue(profile_id TEXT,asset_id INTEGER,added_at TEXT,source TEXT,
  PRIMARY KEY(profile_id,asset_id));
CREATE TABLE playlist(
  id INTEGER PRIMARY KEY,profile_id TEXT,name TEXT,source_kind TEXT,
  source_seed_asset_id INTEGER,current_asset_id INTEGER,created_at TEXT,updated_at TEXT);
CREATE TABLE playlist_item(
  playlist_id INTEGER,asset_id INTEGER,position INTEGER,added_at TEXT,
  PRIMARY KEY(playlist_id,asset_id),UNIQUE(playlist_id,position));
CREATE TABLE asset_preference(profile_id TEXT,asset_id INTEGER,liked INTEGER,reason TEXT,
  source TEXT,updated_at TEXT,PRIMARY KEY(profile_id,asset_id));
CREATE TABLE asset_quality_goal(profile_id TEXT,asset_id INTEGER,wanted INTEGER,reason TEXT,
  updated_at TEXT,PRIMARY KEY(profile_id,asset_id));
CREATE TABLE asset_tag_preference(profile_id TEXT,asset_id INTEGER,normalized_tag TEXT,
  hidden INTEGER,updated_at TEXT,PRIMARY KEY(profile_id,asset_id,normalized_tag));
CREATE TABLE media_binding(
  asset_id INTEGER,backend TEXT,external_id TEXT,metadata_json TEXT,last_synced_at TEXT,
  PRIMARY KEY(asset_id,backend),UNIQUE(backend,external_id));
CREATE TABLE activity_event(id INTEGER PRIMARY KEY,asset_id INTEGER,kind TEXT,created_at TEXT);
CREATE TABLE asset_subtitle(
  id INTEGER PRIMARY KEY,asset_id INTEGER,location TEXT NOT NULL,path TEXT NOT NULL,
  name TEXT NOT NULL,language TEXT NOT NULL DEFAULT '',format TEXT NOT NULL,
  size INTEGER,mtime TEXT,pairing TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,
  UNIQUE(location,path));
CREATE TABLE search_history(
  query TEXT PRIMARY KEY, used_count INTEGER NOT NULL DEFAULT 1, last_used_at TEXT NOT NULL);
CREATE TABLE review_decision(
  category TEXT NOT NULL,item_key TEXT NOT NULL,status TEXT NOT NULL,
  reviewer TEXT NOT NULL DEFAULT 'local-default',note TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
  PRIMARY KEY(category,item_key));
CREATE TABLE genre_decision(
  source_genre TEXT PRIMARY KEY, raw_genre TEXT NOT NULL, peach_tag TEXT, decided_at TEXT NOT NULL,
  CHECK(peach_tag IS NULL OR length(trim(peach_tag))>0));
CREATE TABLE profile(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0,
  settings_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO profile(id,user_id,name,is_default,settings_json,created_at,updated_at)
VALUES('local-default','local','Default',1,'{}','2026-08-14T12:17:23Z','2026-08-14T12:17:23Z');
"""


def tiny_jpeg(tone: int, width: int = 64, height: int = 48) -> bytes:
    """一张真的能解开的小图。

    题材圆标那条路径会把取回的封面解开缩一次，喂假字节只能让 OpenCV 在测试输出里
    刷一屏解码错误——那时验的也不再是这段代码。
    """
    import cv2
    import numpy

    canvas = numpy.full((height, width, 3), tone, dtype=numpy.uint8)
    return bytes(cv2.imencode(".jpg", canvas)[1])


class MediaCacheHeaderTests(unittest.TestCase):
    """生成物的缓存时长只许在一处写死。

    这条断言扫的是媒体路由真正所在的模块。钉死扫 `api.py` 的话，媒体路由拆到
    `routes_media.py` 之后它会永远绿——那里已经一条媒体路由都没有了。门槛跟着
    实现走，不跟着文件名走。
    """

    def test_no_media_endpoint_hardcodes_a_day(self):
        source = (ROOT / "src" / "peach" / "routes_media.py").read_text(encoding="utf-8")
        self.assertIn("MEDIA_CACHE_SECONDS", source, "媒体路由不在这个文件里了，门槛已空转")
        self.assertNotIn("max-age=86400", source, "媒体端点不该再写死一天")
        # `immutable` 只许出现在 /vendor/ 那条中间件里，它留在 api.py。这里不查
        # 字面量：常量上方那段注释正是在解释「为什么不加 immutable」。
        self.assertNotIn('"public, max-age=31536000, immutable"', source)


@unittest.skipUnless(HAS_DEPS, "FastAPI/httpx 尚未安装")
class RobotsTests(unittest.IsolatedAsyncioTestCase):
    """整站不进搜索引擎。

    响应头覆盖每一个响应，`/robots.txt` 与两页 `<meta>` 覆盖爬虫会主动去读的位置。
    公网入口一开，这个站就在互联网上，收录了就再也收不回来，所以不按部署开关。
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name).resolve()
        # 账本要真的建出来：`/api/items` 走只读连接（`mode=ro`），文件不存在就打不开，
        # 响应变成 500 并往日志里吐一整段回溯，用例却因为响应头照样在而通过。
        cls.app = create_app(PeachSettings(
            configured=True, db_path=fresh_ledger(root), follow_state_root=root / "state"))

    @classmethod
    def tearDownClass(cls):
        cls.app.state.http_transport.close()
        cls.tmp.cleanup()

    def client(self):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")

    async def test_robots_txt_is_readable_without_signing_in(self):
        async with self.client() as client:
            response = await client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "User-agent: *\nDisallow: /\n")

    async def test_every_response_carries_the_robots_header(self):
        async with self.client() as client:
            for path in ("/robots.txt", "/healthz", "/login", "/favicon.ico", "/api/items"):
                with self.subTest(path=path):
                    response = await client.get(path)
                    self.assertLess(response.status_code, 500,
                                    "夹具坏了也带着响应头，5xx 说明测的不是正常响应")
                    self.assertEqual(
                        response.headers["x-robots-tag"], "noindex, nofollow, noarchive")

    def test_the_login_page_and_the_app_shell_declare_noindex(self):
        # 这份服务没有口令，`/login` 会直接跳回首页；声明本身在页面源里。
        self.assertIn('<meta name="robots" content="noindex, nofollow">',
                      routes_auth.login_html("/"))
        shell = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', shell)


@unittest.skipUnless(HAS_DEPS, "FastAPI/httpx 尚未安装")
class FastApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.object(routes_auth, "_ATTEMPTS", OrderedDict()))
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "ledger.db"
        self.media_root = self.root / "media"
        self.snapshot_root = self.root / "snapshots"
        self.poster_root = self.root / "posters"
        self.avatar_root = self.root / "avatars"
        self.logo_root = self.root / "logos"
        self.cover_root = self.root / "covers"
        self.transcode_root = self.root / "transcodes"
        self.stream_root = self.root / "stream-segments"
        self.photo_root = self.root / "photo-thumbs"
        self.vendor_root = self.root / "vendor"
        self.taste_store = self.root / "sources" / "taste-history" / "history.sqlite"
        self.taste_import_root = self.root / "sources" / "taste-history" / "imports"
        self.taste_output_root = self.root / "review" / "taste-history"
        self.taste_manifest = self.root / "state" / "taste-history" / "manifest.json"
        # 复核候选必须来自临时目录，不去读真实的 R:\peach-data\generated。
        self.candidate_root = self.root / "generated"
        self.candidate_root.mkdir()
        self.endcard_frame = (
            self.candidate_root / "endcard-evidence" / "1" / "tail-000098000.png"
        )
        self.endcard_frame.parent.mkdir(parents=True)
        self.endcard_frame.write_bytes(b"endcard")
        for path in (self.media_root, self.snapshot_root, self.poster_root,
                     self.avatar_root, self.logo_root, self.cover_root,
                     self.vendor_root):
            path.mkdir()
        (self.vendor_root / "player.js").write_text("window.vendorReady=true;", encoding="utf-8")
        self.media_file = self.media_root / "one.mp4"
        self.media_file.write_bytes(b"0123456789")
        # HLS 分片按真实关键帧切，所以它的测试媒体必须带可解析的 moov/stss；
        # Range 契约测试仍用上面那个 10 字节文件，两者不要互相牵连。
        self.hls_file = self.media_root / "segmented.mp4"
        self.hls_file.write_bytes(minimal_mp4(timescale=1000, sample_delta=40,
                                              samples=338, keyframe_every=25))
        self.snapshot_file = self.snapshot_root / "cloud" / "local" / "one.jpg"
        self.snapshot_file.parent.mkdir(parents=True)
        self.snapshot_file.write_bytes(b"snapshot")
        (self.poster_root / "1_4.jpg").write_bytes(b"poster")
        (self.avatar_root / "1.jpg").write_bytes(b"avatar")
        (self.logo_root / "Studio_A.img").write_bytes(b"logo")
        (self.logo_root / "Studio_A.img.ct").write_text("image/png", encoding="utf-8")
        self.page = self.root / "index.html"
        self.page.write_text("<!doctype html><title>Peach test</title><main>ready</main>", encoding="utf-8")
        # 隔离页面根包含退场路径的文件，用于验证它们不会被提供。
        (self.root / "app.js").write_text("export const retired = true;", encoding="utf-8")
        (self.root / "js").mkdir()
        for name in ("core", "tags", "jav-title", "ui-sounds", "middle-truncate", "ui-components",
                     "filter-scroll", "search-morph"):
            (self.root / "js" / f"{name}.js").write_text("export const ok = 1;", encoding="utf-8")
        # 前端产物（ADR-0022）：构建结果提交进 Git，运行时由 `/dist/{name}` 提供。
        (self.root / "dist").mkdir()
        for name in ("peach-app.js", "peach-pages.js", "peach-ui.js", "peach-react.js", "peach-entry.js",
                     "other.js"):
            (self.root / "dist" / name).write_text("export const ok = 1;", encoding="utf-8")
        for name in ("peach-app.css", "peach-pages.css", "peach-ui.css", "peach-react.css", "peach-entry.css"):
            (self.root / "dist" / name).write_text(".peach-react{}", encoding="utf-8")
        con = sqlite3.connect(self.db)
        con.executescript(BASE_SCHEMA)
        for migration in ("0018_online_follow.sql", "0034_feed_discovery.sql"):
            con.executescript((ROOT / "migrations" / migration).read_text(encoding="utf-8"))
        con.execute(
            """INSERT INTO asset(id,location,path,name,medium,size,creator,studio,duration,
                                  width,height,ctx_orient,snapshot_path,first_seen)
               VALUES(1,'local',?,'one.mp4','video',100,
                      'Alice','Studio A',100,1920,1080,'横屏',?,'2026-08-14')""",
            (str(self.media_file), str(self.snapshot_file)),
        )
        con.execute("INSERT INTO asset_tag(asset_id,tag,source) VALUES(1,'Tag A','test')")
        con.execute(
            "INSERT INTO asset_tag(asset_id,tag,source) "
            "VALUES(1,'官方标签','javinizer:r18dev:tag')"
        )
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name) "
            "VALUES(1,'tag','Tag A','tag a')"
        )
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(1,1,'tag','test',1.0)"
        )
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name) "
            "VALUES(2002,'tag','官方标签','官方标签')"
        )
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(1,2002,'tag','javinizer:r18dev:tag',0.9)"
        )
        con.commit()
        con.close()
        self.settings = PeachSettings(
            db_path=self.db, configured=True, token="secret", page_path=self.page, vendor_path=self.vendor_root,
            agentation_path=self.root / "build" / "peach-agentation.js",
            allowed_media_roots=(self.media_root,), snapshot_root=self.snapshot_root,
            poster_root=self.poster_root, avatar_root=self.avatar_root, logo_root=self.logo_root,
            cover_root=self.cover_root, stream_root=self.stream_root,
            ffmpeg_root=self.root / "ffmpeg", transcode_root=self.transcode_root,
            candidate_root=self.candidate_root, photo_root=self.photo_root,
            taste_history_store=self.taste_store,
            taste_history_import_root=self.taste_import_root,
            taste_history_output_root=self.taste_output_root,
            taste_history_manifest=self.taste_manifest,
            entry_links_root=self.root / "state",
        )
        self.app = create_app(self.settings)
        # 字节与时间表夹具不含可解码画面；编码判定由媒体域的真实样本覆盖。
        from peach.transcodes import _MediaProfile
        self.profile_patch = patch.object(
            self.app.state.transcode_service, "_probe",
            return_value=_MediaProfile("h264", "yuv420p", "aac"),
        )
        self.profile_patch.start()
        self.addCleanup(self.profile_patch.stop)
        # 凭据仓库落在临时目录：夹具不该读到这台机器上真实的 api_key，结论也不该
        # 因为某个 provider 在本机配没配过而变。要用凭据的用例自己往这里写一份。
        self.secrets_root = self.root / "secrets"
        self.credentials = credential_store_for(self.secrets_root, shared_root=None)
        credentials_patch = patch("peach.web_follow._credential_store",
                                  return_value=self.credentials)
        credentials_patch.start()
        self.addCleanup(credentials_patch.stop)
        self.assertIs(
            self.app.state.web_contract.database,
            self.app.state.repository.database,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.tmp.cleanup()

    async def test_health_is_side_effect_free(self):
        response = await self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], __version__)
        # 采集浏览器等人点验证的站列在这里，供排查（ADR-0065）；平时是空表。
        self.assertEqual(response.json()["attention"], [])
        checks = response.json()["checks"]
        self.assertEqual(set(checks), {"database", "schema", "configured", "ffmpeg", "media_mounts", "security"})
        self.assertTrue(all(set(check) == {"status", "reason"} for check in checks.values()))
        self.assertNotIn(str(self.media_root), json.dumps(checks))

    async def test_diagnostics_is_authenticated_local_and_uses_the_shared_report(self):
        from peach import diagnostics
        report = {"version": __version__, "status": "warning", "checks": {}}
        with patch.object(diagnostics, "report", return_value=report) as shared:
            denied = await self.client.get("/api/diagnostics")
            self.assertEqual(denied.status_code, 401)
            accepted = await self.client.get("/api/diagnostics?t=secret")
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json(), report)
            shared.assert_called_once_with(self.settings, self.app.state.mount_reachability.summary())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("192.0.2.10", 1234)),
                                         base_url="http://test") as remote:
                denied = await remote.get("/api/diagnostics?t=secret")
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(shared.call_count, 1)

    async def test_health_reports_the_build_commit_the_deploy_check_needs(self):
        """换生产托盘的脚本按这个字段确认跑起来的正是它刚打出的包。源码运行时是 null。"""
        payload = (await self.client.get("/healthz")).json()
        self.assertIn("build_commit", payload)
        self.assertIsNone(payload["build_commit"])
        from peach.buildinfo import BuildInfo
        with patch.object(api_module, "BUILD",
                          BuildInfo("c0ffee1234", __version__, "2026-09-07T00:00:00")):
            served = (await self.client.get("/healthz")).json()
        self.assertEqual(served["build_commit"], "c0ffee1234")

    async def test_sources_and_public_health_read_unknown_mount_snapshot_without_io(self):
        from peach.mount_reachability import MountReachability, MountRoot
        monitor = MountReachability([MountRoot("local", "本地磁盘", "R:/private", self.media_root)],
                                    probe=lambda path: self.fail("读取快照不得触发目录探测"))
        self.addCleanup(monitor.stop)
        self.app.state.mount_reachability = monitor
        sources = await self.client.get("/api/sources?t=secret")
        self.assertEqual(sources.status_code, 200)
        self.assertEqual(sources.json()["offline"], [])
        self.assertIsNone(sources.json()["sources"][0]["online"])
        health = (await self.client.get("/healthz")).json()["media_mounts"]
        self.assertEqual(health["state"], "checking")
        self.assertNotIn("R:/private", json.dumps(health))
        self.assertNotIn(str(self.media_root), json.dumps(health))

        monitor.probe = lambda path: "permission_denied"
        monitor.retry_delay = 0
        for _ in range(100):
            monitor.tick()
            if monitor.sources()[0]["online"] is False:
                break
            await asyncio.sleep(0.005)
        sources = (await self.client.get("/api/sources?t=secret")).json()
        self.assertEqual(sources["offline"], ["local"])
        self.assertEqual(sources["sources"][0]["message"], "本地磁盘：没有权限读取")
        payload = (await self.client.get("/healthz")).json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["media_mounts"]["warnings"], ["本地磁盘：没有权限读取"])

    async def test_reader_role_keeps_gets_available_and_rejects_posts(self):
        class ReaderSync:
            status = "reader"
            detail = "写入端是 mac"
            read_only = True
            read_only_message = "数据库由 mac 负责写入，本机当前只能浏览。"

            def observe(self):
                return None

            def start(self):
                return None

            def stop(self):
                return None

        class ReaderMirror:
            def resolve(self, payload):
                result = dict(payload)
                result["mirror"] = {"state": "live", "read_only": True}
                return result

        app = create_app(self.settings, ReaderSync(), ReaderMirror())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            health = await client.get("/healthz")
            self.assertEqual(health.json()["ledger_sync"], "reader")
            self.assertTrue(health.json()["ledger_read_only"])
            self.assertIn("只能浏览", health.json()["ledger_read_only_message"])
            reviewed = await client.get("/api/review?t=secret")
            self.assertEqual(reviewed.status_code, 200)
            self.assertEqual(reviewed.json()["mirror"]["state"], "live")
            listed = await client.get("/api/items?t=secret")
            self.assertEqual(listed.status_code, 200)
            denied = await client.post(
                "/api/feedback?t=secret", json={"id": 1, "kind": "dispose"},
            )
            self.assertEqual(denied.status_code, 409)
            body = denied.json()
            self.assertEqual(body["error"], "ledger read-only")
            # `detail` 是诊断串，界面要展示的是 `message`；缺了它前端只能显示内部原话。
            self.assertEqual(body["detail"], "写入端是 mac")
            self.assertIn("只能浏览", body["message"])

    async def test_replication_off_runs_as_a_standalone_writer(self):
        """`replication.enabled = false` 时没有 sync 对象（ADR-0023 第 3 阶段）。

        `ledger_sync` 必须是明确的 `disabled`，不能冒充 `writer`：复核镜像那一侧
        正是按这个字段判断对面是不是写入端的。写接口照常开——没有第二台机器就没有
        「读者」，只读闸门本来就不该生效。
        """
        app = create_app(self.settings, None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            health = (await client.get("/healthz")).json()
            self.assertEqual(health["ledger_sync"], "disabled")
            self.assertFalse(health["ledger_read_only"])
            allowed = await client.post(
                "/api/feedback?t=secret", json={"id": 1, "kind": "dispose"},
            )
            self.assertNotEqual(allowed.status_code, 409)

    async def test_peach_logo_is_served_as_png(self):
        response = await self.client.get("/peach-logo.png")
        self.assertEqual(response.status_code, 200)
        # 类型声明逐字相等：图片是字节流，`asset_response` 不许给它挂 charset。
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(response.headers["cache-control"], "no-cache")

    async def test_the_favicon_is_served_without_a_session_and_revalidates(self):
        """浏览器取图标时手里没有会话，这条路径因此不设防，也不跳登录页。

        发的是 `resources/peach.ico`：里面装着 16 到 256 七档尺寸，浏览器挑一档就够，
        不必为一枚 16px 的角标下载 1024×1024 的 PNG。缓存走 ETag 复验——图标几个版本
        才动一次，每次开页重下没有道理，而复验又让换了图的那一次立刻生效。
        """
        response = await self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/x-icon")
        # ICO 的固定文件头：保留位 0、类型 1。
        self.assertTrue(response.content.startswith(b"\x00\x00\x01\x00"))
        self.assertEqual(response.headers["cache-control"], "no-cache")
        again = await self.client.get(
            "/favicon.ico", headers={"If-None-Match": response.headers["etag"]},
        )
        self.assertEqual(again.status_code, 304)

    async def test_retired_assets_are_not_served_even_when_files_exist(self):
        paths = ("/app.js", *(f"/js/{name}.js" for name in (
            "core", "tags", "jav-title", "ui-sounds", "middle-truncate", "ui-components",
            "filter-scroll", "search-morph")), *(f"/dist/{name}" for name in (
                "peach-ui.js", "peach-ui.css", "peach-react.js", "peach-react.css", "peach-entry.js",
                "peach-entry.css")))
        for path in paths:
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    response = await self.client.request(method, path, params={"t": "secret"})
                    self.assertEqual(response.status_code, 404)

    async def test_main_assets_need_the_same_token_as_the_page(self):
        for path in ("/dist/peach-app.js", "/dist/peach-app.css", "/board.css", "/app.css"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    response = await self.client.request(method, path)
                    self.assertEqual(response.status_code, 401)
                    self.assertTrue(response.headers["content-type"].startswith("text/plain"))
                    self.assertNotIn("location", response.headers)
                    self.assertEqual(response.text, "需要 ?t=口令" if method == "GET" else "")

    async def test_main_bundle_has_authenticated_get_head_mime_and_revalidation(self):
        for name, media in (("peach-app.js", "text/javascript"), ("peach-app.css", "text/css")):
            path = f"/dist/{name}"
            served = await self.client.get(path, params={"t": "secret"})
            self.assertEqual(served.status_code, 200, name)
            self.assertEqual(served.content, (self.root / "dist" / name).read_bytes())
            self.assertEqual(served.headers["content-type"], f"{media}; charset=utf-8", name)
            self.assertEqual(served.headers["cache-control"], "no-cache", name)
            etag = served.headers["etag"]
            head = await self.client.head(path, params={"t": "secret"})
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.content, b"")
            self.assertEqual(head.headers["content-type"], served.headers["content-type"])
            self.assertEqual(head.headers["etag"], etag)
            for method in ("GET", "HEAD"):
                cached = await self.client.request(method, path, params={"t": "secret"},
                                                   headers={"If-None-Match": etag})
                self.assertEqual(cached.status_code, 304)
                self.assertEqual(cached.content, b"")
                self.assertEqual(cached.headers["etag"], etag)

        for escape in ("..%2f..%2fapp.js", "..%5c..%5csecrets.json", "sub%2fpeach-app.js",
                       "..%2fapp.js", "peach-app.js.map", "peach-app.mjs", "Peach-App.js", "other.js"):
            denied = await self.client.get(f"/dist/{escape}?t=secret")
            self.assertEqual(denied.status_code, 404, f"{escape} 不该被提供")

    async def test_page_bundle_is_public_with_only_its_literal_names(self):
        for name, media in (("peach-pages.js", "text/javascript"), ("peach-pages.css", "text/css")):
            path = f"/dist/{name}"
            served = await self.client.get(path)
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served.content, (self.root / "dist" / name).read_bytes())
            self.assertEqual(served.headers["content-type"], f"{media}; charset=utf-8")
            head = await self.client.head(path)
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.content, b"")
            self.assertEqual(head.headers["etag"], served.headers["etag"])
            cached = await self.client.get(path, headers={"If-None-Match": served.headers["etag"]})
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(cached.content, b"")
        self.assertEqual((await self.client.get("/dist/peach-pages.js.map")).status_code, 401)

    async def test_annotation_tool_is_served_only_where_it_was_built(self):
        """`/dev/agentation.js` 只在本机构建过的检出里有东西，其余部署一律 404。"""
        missing = await self.client.get("/dev/agentation.js?t=secret")
        self.assertEqual(missing.status_code, 404)

        bundle = self.settings.agentation_path
        bundle.parent.mkdir(parents=True)
        bundle.write_text("export {};\n", encoding="utf-8")
        served = await self.client.get("/dev/agentation.js?t=secret")
        self.assertEqual(served.status_code, 200)
        self.assertTrue(served.headers["content-type"].startswith("text/javascript"))
        self.assertEqual(served.headers["cache-control"], "no-cache")

        unauthorized = await self.client.get("/dev/agentation.js")
        self.assertEqual(unauthorized.status_code, 401)

    def _swap_http_client(self, upstream):
        fake = httpx.Client(transport=httpx.MockTransport(upstream), follow_redirects=True)
        original = self.app.state.http_transport.client
        self.app.state.http_transport.client = fake

        def restore():
            self.app.state.http_transport.client = original
            fake.close()
        self.addCleanup(restore)
        return fake

    async def test_follow_avatar_is_fetched_once_and_then_served_from_disk(self):
        """头像是元数据，落在本机：浏览器不直接碰 pixiv，第二次显示也不再问上游。"""
        denied = await self.client.get("/follow-avatar?service=fanbox&id=30917150")
        self.assertEqual(denied.status_code, 401)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
        hits = []

        def upstream(request):
            hits.append(str(request.url))
            return httpx.Response(200, content=png, request=request,
                                  headers={"content-type": "image/png"})
        self._swap_http_client(upstream)
        # patch 打在真正 import 它的模块上。`/follow-avatar` 住在 routes_media，
        # 打在 `peach.api` 上会静默失效——那个名字已经不在那里了。
        with patch("peach.routes_media.resolve_official_avatar",
                   return_value="https://pixiv.pximg.net/icon.jpeg") as resolver:
            response = await self.client.get(
                "/follow-avatar?t=secret&service=fanbox&id=30917150")
            again = await self.client.get(
                "/follow-avatar?t=secret&service=fanbox&id=30917150")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.content, png)
        self.assertEqual(hits, ["https://pixiv.pximg.net/icon.jpeg"], "第二次不再出网")
        resolver.assert_called_once_with("fanbox", "30917150",
                                         transport=self.app.state.http_transport)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.content, png)
        cached = list((self.candidate_root / "follow-assets" / "avatars").glob("*.img"))
        self.assertEqual(len(cached), 1, "头像落在 generated/follow-assets/avatars 下")

    async def test_mirror_avatars_come_from_the_fixed_archive_host(self):
        webp = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16
        hits = []

        def upstream(request):
            hits.append(str(request.url))
            return httpx.Response(200, content=webp, request=request,
                                  headers={"content-type": "image/webp"})
        self._swap_http_client(upstream)
        response = await self.client.get(
            "/follow-avatar?t=secret&provider=kemono&ref=fanbox/30917150")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/webp")
        self.assertEqual(hits, ["https://kemono.cr/icons/fanbox/30917150"])
        # 没有实测过头像端点的来源、缺 ref 或身份形状不对的请求都不出网，直接给占位图。
        for query in ("provider=rule34video&ref=1", "provider=kemono&ref=noslash",
                      "service=fanbox&id=not.a.creator", "service=patreon&id=1",
                      "service=profile&id=twitter:../x", "service=profile&id=fanbox:1"):
            missing = await self.client.get(f"/follow-avatar?t=secret&{query}")
            self.assertEqual(missing.status_code, 404, query)
            self.assertEqual(missing.headers["content-type"], PLACEHOLDER_CONTENT_TYPE, query)
        self.assertEqual(len(hits), 1)
        # 字母加连字符是合法的 FANBOX 创作者 id：去官方查一次，走的是应用那条 client；
        # 回来的不是资料 JSON，就给占位图，不再往别处取。
        unknown = await self.client.get("/follow-avatar?t=secret&service=fanbox&id=not-a-number")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(hits[1:], ["https://api.fanbox.cc/creator.get?creatorId=not-a-number"])

    async def test_profile_avatars_keep_the_sharpest_of_x_and_patreon(self):
        """名片上的 X 与 Patreon 各退到能用的最大一档，两家之间按实际像素留大的那张。"""
        import io
        from PIL import Image

        def png(side):
            buffer = io.BytesIO()
            Image.new("RGB", (side, side)).save(buffer, "PNG")
            return buffer.getvalue()
        bodies = {
            "https://pbs.twimg.com/profile_images/1/a_400x400.jpg": png(400),
            "https://c10.patreonusercontent.com/original.png": png(256),
        }
        hits = []

        def upstream(request):
            hits.append(str(request.url))
            body = bodies.get(str(request.url))
            if body is None:
                return httpx.Response(404, request=request)
            return httpx.Response(200, content=body, request=request,
                                  headers={"content-type": "image/png"})
        self._swap_http_client(upstream)
        tiers = {
            "twitter": ["https://pbs.twimg.com/profile_images/1/a.jpg",
                        "https://pbs.twimg.com/profile_images/1/a_400x400.jpg"],
            "patreon": ["https://c10.patreonusercontent.com/original.png"],
        }
        with patch("peach.routes_media.profile_avatar_tiers",
                   side_effect=lambda service, handle, transport: tiers[service]):
            response = await self.client.get(
                "/follow-avatar?t=secret&service=profile&id=twitter:Rekin3D,patreon:sharkarts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, bodies[
            "https://pbs.twimg.com/profile_images/1/a_400x400.jpg"])
        self.assertEqual(hits, [*tiers["twitter"], *tiers["patreon"]])

    async def test_the_work_icon_walks_the_candidates_until_one_shows_a_clear_face(self):
        """题材圆标顺着候选往下取，停在第一张脸够大的那里，取景写在图旁边。

        只取最热那一张的话，圆标里有一半是身体特写——最热的帖子常常就是特写。停下来
        之后不再往下取：后面那几张既不该出网，也不该留在缓存目录里。
        """
        from peach import follow_assets, routes_media

        covers = {f"https://api-cdn.rule34.xxx/samples/{n}/c.jpg": tiny_jpeg(n * 40)
                  for n in (1, 2, 3)}
        hits = []

        def upstream(request):
            hits.append(str(request.url))
            return httpx.Response(200, content=covers[str(request.url)], request=request,
                                  headers={"content-type": "image/jpeg"})
        self._swap_http_client(upstream)
        second = covers["https://api-cdn.rule34.xxx/samples/2/c.jpg"]
        self.assertNotEqual(second, covers["https://api-cdn.rule34.xxx/samples/1/c.jpg"])
        seen = {second: {"ratio": 0.563, "px": [1080, 1920],
                         "face": {"cx": 0.5, "cy": 0.18, "w": 0.3, "h": 0.17,
                                  "score": 0.93},
                         "focus": {"axis": "y", "pct": 18}}}
        denied = await self.client.get("/work-icon?work=stellar+blade")
        self.assertEqual(denied.status_code, 401)
        with patch("peach.routes_media.web_follow.work_icon_urls",
                   return_value=list(covers)) as urls:
            with patch("peach.routes_media.web_follow.work_icon_search_urls") as search:
                with patch.object(routes_media._WORK_FACE_PROBE, "on_bytes",
                                  side_effect=seen.get):
                    response = await self.client.get(
                        "/work-icon?t=secret&work=stellar+blade")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, second, "服务的是看得清脸的那一张")
        self.assertEqual(hits, list(covers)[:2], "脸够大就停，后面的候选不再出网")
        search.assert_not_called()
        urls.assert_called_once()
        self.assertEqual(urls.call_args.args[1], "stellar blade")
        cached = follow_assets.cache_path(
            self.candidate_root / follow_assets.ROOT_NAME, "works", "stellar blade")
        self.assertEqual(cached.read_bytes(), second)
        written = json.loads(cached.with_suffix(".face.json").read_text(encoding="utf-8"))
        self.assertEqual(written["focus"], {"axis": "y", "pct": 18})
        # 记录说的必须是落盘那张图：页面拿 naturalWidth 核对，对不上就退回几何居中。
        self.assertEqual(written["px"], [64, 48])
        self.assertEqual(len(list(cached.parent.glob("*.img"))), 1,
                         "落选的候选一个文件都不留")

    async def test_a_face_too_small_to_see_keeps_looking_and_then_shows_the_whole_frame(self):
        """检出了脸不算数，脸得在画面里占到看得清的那一档，不够就继续看下一张。

        YuNet 在远景图上会给出一个占长边百分之二、分数照样过线的框，罩在肩背的纹身
        上；圆标正是按这个框取景放大的，于是圆里是一小块皮肤。一张都过不了这一关的
        题材按「没有头」处理：整张封面摆出来，不写人脸记录。把画面里最大的那块皮肤
        放大成一枚认不出的圆，还不如一张认得出是哪部作品的全身。
        """
        from peach import follow_assets, routes_media

        covers = {f"https://api-cdn.rule34.xxx/samples/{n}/c.jpg": tiny_jpeg(n * 30)
                  for n in (4, 5, 6)}
        hits = []

        def upstream(request):
            hits.append(str(request.url))
            return httpx.Response(200, content=covers[str(request.url)], request=request,
                                  headers={"content-type": "image/jpeg"})
        self._swap_http_client(upstream)
        first, _, third = (covers[url] for url in covers)

        def probe(payload):
            share = {first: 0.02, third: 0.05}.get(payload)
            if share is None:
                return None
            return {"ratio": 1.0, "px": [1000, 1000],
                    "face": {"cx": 0.4, "cy": 0.3, "w": share, "h": share,
                             "score": 0.82}}
        with patch("peach.routes_media.web_follow.work_icon_urls",
                   return_value=list(covers)):
            with patch.object(routes_media._WORK_FACE_PROBE, "on_bytes",
                              side_effect=probe):
                response = await self.client.get("/work-icon?t=secret&work=miside")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(hits, list(covers), "一张都不够大时候选要走完")
        self.assertEqual(response.content, first, "退回第一张取得到的，摆整张")
        cached = follow_assets.cache_path(
            self.candidate_root / follow_assets.ROOT_NAME, "works", "miside")
        self.assertFalse(cached.with_suffix(".face.json").exists(),
                         "没有一张脸够格，就不该留下一份让页面照着放大的记录")

    async def test_a_face_with_too_few_pixels_is_passed_over_for_a_sharper_one(self):
        """脸在画面里占得住还不够，它得有足够多的像素撑到放大到头。

        实测本库五枚圆标栽在这里：那几条没有封面，候选只能退回站点那层 250px 的缩略
        图，脸在里面只剩十几二十个像素。占比这一关它们全过，可页面按脸放大时不许上采
        样，于是脸最多只能占到圆的三成，剩下七成是身上和背景——看起来就是一张糊图。
        """
        from peach import follow_assets, routes_media

        covers = {f"https://api-cdn.rule34.xxx/samples/{n}/c.jpg": tiny_jpeg(n * 20)
                  for n in (1, 2)}
        hits = []

        def upstream(request):
            hits.append(str(request.url))
            return httpx.Response(200, content=covers[str(request.url)], request=request,
                                  headers={"content-type": "image/jpeg"})
        self._swap_http_client(upstream)
        blurry, sharp = (covers[url] for url in covers)
        # 两张的脸占比一样，差别只在源图有多大：250px 那张的脸只有 24 个像素。
        sizes = {blurry: [250, 141], sharp: [1920, 1080]}

        def probe(payload):
            return {"ratio": 0.5625, "px": sizes[payload],
                    "face": {"cx": 0.5, "cy": 0.3, "w": 0.096, "h": 0.17,
                             "score": 0.88}}
        with patch("peach.routes_media.web_follow.work_icon_urls",
                   return_value=list(covers)):
            with patch.object(routes_media._WORK_FACE_PROBE, "on_bytes",
                              side_effect=probe):
                response = await self.client.get("/work-icon?t=secret&work=miside")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(hits, list(covers), "糊的那张不算数，得往下看")
        self.assertEqual(response.content, sharp)
        cached = follow_assets.cache_path(
            self.candidate_root / follow_assets.ROOT_NAME, "works", "miside")
        written = json.loads(cached.with_suffix(".face.json").read_text(encoding="utf-8"))
        self.assertEqual(written["face"]["w"], 0.096)

    async def test_when_nothing_here_shows_a_face_the_icon_comes_from_the_site(self):
        """本库那几张都看不清脸时，圆标取站上这个题材最热的那几张。

        库里存的是用户关注的那几位作者发的东西，一个题材常常只有一两条，那一两条未必
        有正脸；站上同一个标签下有成千上万帖。站点那一趟排在本库之后，而且用的是本库
        记下的标签写法——照归一化后的题材身份拼出来的标签在站上是零命中。
        """
        from peach import follow_assets, routes_media

        local = "https://api-cdn.rule34.xxx/samples/9/c.jpg"
        remote = "https://api-cdn.rule34.xxx/images/9/d.jpg"
        covers = {local: tiny_jpeg(20), remote: tiny_jpeg(25)}
        hits = []

        def upstream(request):
            hits.append(str(request.url))
            return httpx.Response(200, content=covers[str(request.url)], request=request,
                                  headers={"content-type": "image/jpeg"})
        self._swap_http_client(upstream)

        def probe(payload):
            if payload != covers[remote]:
                return None
            return {"ratio": 1.0, "px": [1000, 1000],
                    "face": {"cx": 0.5, "cy": 0.3, "w": 0.3, "h": 0.3, "score": 0.9}}

        with patch("peach.routes_media.web_follow.work_icon_urls",
                   return_value=[local]):
            with patch("peach.routes_media.web_follow.work_icon_tag",
                       return_value="the_witcher_(series)"):
                with patch("peach.routes_media.web_follow.work_icon_search_urls",
                           return_value=[remote]) as search:
                    with patch.object(routes_media._WORK_FACE_PROBE, "on_bytes",
                                      side_effect=probe):
                        response = await self.client.get(
                            "/work-icon?t=secret&work=the+witcher")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, covers[remote])
        self.assertEqual(hits, [local, remote], "本库那张先取，站点那一趟排在它后面")
        self.assertEqual(search.call_args.args[1], "the_witcher_(series)")
        cached = follow_assets.cache_path(
            self.candidate_root / follow_assets.ROOT_NAME, "works", "the witcher")
        self.assertEqual(cached.read_bytes(), covers[remote])

    async def test_a_work_with_no_face_anywhere_still_gets_a_cover(self):
        """一张都检不出脸时用第一张取得到的：没有脸的代表图仍然好过一个空圆。

        那时不写 sidecar，页面退回样式表里的默认取景——留一份旧记录的话，圆标会拿
        上一张图的脸心给这一张取景，而这在界面上看不出和「本来就该这么摆」的区别。
        """
        from peach import follow_assets, routes_media

        covers = {f"https://api-cdn.rule34.xxx/samples/{n}/c.jpg": tiny_jpeg(n * 30)
                  for n in (7, 8)}

        def upstream(request):
            return httpx.Response(200, content=covers[str(request.url)], request=request,
                                  headers={"content-type": "image/jpeg"})
        self._swap_http_client(upstream)
        cache_root = self.candidate_root / follow_assets.ROOT_NAME
        cached = follow_assets.cache_path(cache_root, "works", "miside")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.with_suffix(".face.json").write_text('{"focus":{"axis":"x","pct":90}}',
                                                    encoding="utf-8")
        with patch("peach.routes_media.web_follow.work_icon_urls",
                   return_value=list(covers)):
            with patch.object(routes_media._WORK_FACE_PROBE, "on_bytes",
                              return_value=None):
                response = await self.client.get("/work-icon?t=secret&work=miside")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, covers[list(covers)[0]])
        self.assertFalse(cached.with_suffix(".face.json").exists(),
                         "换了图就不能留着上一张的脸记录")

    async def test_source_icons_are_served_from_disk_and_only_from_the_table(self):
        """来源图标同样经 Peach 落盘；地址只认 follow_assets.SOURCE_ICON_URLS 那张表。"""
        from peach import follow_assets

        ico = b"\x00\x00\x01\x00\x01\x00" + b"\x00" * 24
        hits = []

        def upstream(request):
            hits.append(str(request.url))
            if "simpcity" in request.url.host:
                # 机器人质询页：content-type 说是图也不算，认不出字节就不落盘。
                return httpx.Response(200, content=b"<html>Just a moment...</html>",
                                      request=request, headers={"content-type": "image/png"})
            return httpx.Response(200, content=ico, request=request,
                                  headers={"content-type": "image/x-icon"})
        self._swap_http_client(upstream)
        denied = await self.client.get("/source-icon?provider=kemono")
        self.assertEqual(denied.status_code, 401)
        response = await self.client.get("/source-icon?t=secret&provider=kemono")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/x-icon")
        self.assertEqual(response.content, ico)
        again = await self.client.get("/source-icon?t=secret&provider=kemono")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(hits, [follow_assets.SOURCE_ICON_URLS["kemono"]])
        blocked = await self.client.get("/source-icon?t=secret&provider=simpcity")
        self.assertEqual(blocked.status_code, 404)
        self.assertEqual(blocked.headers["content-type"], PLACEHOLDER_CONTENT_TYPE)
        self.assertEqual(hits[-1], follow_assets.SOURCE_ICON_URLS["simpcity"])
        unknown = await self.client.get("/source-icon?t=secret&provider=evil.example")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(len(hits), 2, "没登记的来源不出网")
        root = self.candidate_root / follow_assets.ROOT_NAME
        kemono = follow_assets.cache_path(root, "icons", "kemono")
        simpcity = follow_assets.cache_path(root, "icons", "simpcity")
        required = {kemono.name, simpcity.with_suffix(".failed").name}
        optional_locks = {kemono.with_suffix(".lock").name, simpcity.with_suffix(".lock").name}
        entries = list((root / "icons").iterdir())
        self.assertTrue(all(path.is_file() and not path.is_symlink() for path in entries))
        self.assertEqual({path.name for path in entries} - optional_locks, required)

    async def test_site_marks_only_ever_resolve_a_key_the_server_already_knows(self):
        """站点圆标的地址由服务端查表得到，前端递的是键。

        判据是「这个键在不在表里」，不是「这个地址看着像不像那个站」——收地址就是开一个
        任意地址抓取的口子。白名单里那条后缀的子域同样不认：子域是浏览历史带进来的
        任意值，`sub.kemono.cr` 和 `kemono.cr` 在这里是两件事。
        """
        from peach import routes_media

        self.addCleanup(setattr, routes_media, "GENERATED_DIR", routes_media.GENERATED_DIR)
        routes_media.GENERATED_DIR = self.root / "site-marks"
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"IHDR" + b"\x00" * 8)
        hosts = []

        def upstream(request):
            hosts.append(request.url.host)
            return httpx.Response(200, content=png, request=request,
                                  headers={"content-type": "image/png"})
        self._swap_http_client(upstream)

        denied = await self.client.get("/site-mark?source=airav")
        self.assertEqual(denied.status_code, 401)
        await self.client.get("/site-mark?t=secret&source=airav")
        self.assertTrue(hosts, "表里的采集来源要真的去问对方站点")
        self.assertTrue(all(host.endswith("airav.io") for host in hosts), hosts)

        reached = len(hosts)
        for query in ("source=evil.example", "domain=evil.example",
                      "domain=sub.kemono.cr", "domain="):
            refused = await self.client.get(f"/site-mark?t=secret&{query}")
            self.assertEqual(refused.status_code, 404, query)
        self.assertEqual(len(hosts), reached, "表外的键一次网都不出")

        await self.client.get("/site-mark?t=secret&domain=kemono.cr")
        self.assertTrue(any(host.endswith("kemono.cr") for host in hosts[reached:]), hosts)

    async def test_source_favicons_keep_verified_pixels_without_upstream_discovery(self):
        from peach.config import PROJECT_ROOT

        reached = []
        def upstream(request):
            reached.append(str(request.url))
            return httpx.Response(502, request=request)
        self._swap_http_client(upstream)
        for name in ('avsox', 'manko', 'sukebei', 'mgstage', 'dmm', 'fc2'):
            response = await self.client.get(f'/site-mark?source={name}&t=secret')
            self.assertEqual(response.status_code, 200, name)
            self.assertEqual(response.content, (PROJECT_ROOT / 'resources/site-marks' / f'{name}.png').read_bytes())
            self.assertEqual(response.headers['Cache-Control'], 'public, no-cache')
        self.assertEqual(reached, [])

    async def test_copy_editor_is_source_only_and_rejects_cross_origin_saves(self):
        from unittest.mock import patch
        from peach import routes_pages
        with patch.object(routes_pages.sys, "frozen", True, create=True):
            refused = await self.client.get('/dev/copy-editor.js?t=secret')
            self.assertEqual(refused.status_code, 404)
            page = await self.client.get('/?edit&t=secret', follow_redirects=True)
            self.assertNotIn('data-peach-copy-mode', page.text)
        refused = await self.client.post('/dev/copy-save?t=secret', json={},
                                         headers={'Origin': 'https://another.example'})
        self.assertEqual(refused.status_code, 403)

    async def test_bundled_site_icons_require_auth_and_validate_names(self):
        from peach.config import PROJECT_ROOT
        self.assertEqual((await self.client.get('/site-icon/github.png')).status_code, 401)
        for name in ('javten', 'fc2ppvdb', 'avwikidb', 'minnano-av', 'github'):
            response = await self.client.get(f'/site-icon/{name}.png?t=secret')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['content-type'], 'image/png')
            self.assertEqual(response.content, (PROJECT_ROOT / 'resources/site-marks' / f'{name}.png').read_bytes())
            cached = await self.client.get(f'/site-icon/{name}.png?t=secret', headers={'If-None-Match': response.headers['etag']})
            self.assertEqual(cached.status_code, 304)
        for name in ('unknown.png', 'github.svg', '..%2Fgithub.png'):
            self.assertEqual((await self.client.get(f'/site-icon/{name}?t=secret')).status_code, 404)

    def _archive_links(self, colour_of):
        """两条存档链接；上游按 `colour_of(url)` 给一枚圆形图标，给 None 就 404。返回请求过的地址。"""
        from PIL import Image, ImageDraw

        from peach import routes_media

        self.addCleanup(setattr, routes_media, "GENERATED_DIR", routes_media.GENERATED_DIR)
        routes_media.GENERATED_DIR = self.root / "generated"
        asked = []

        def upstream(request):
            asked.append(str(request.url))
            colour = colour_of(str(request.url)) if request.url.path.endswith(".png") else None
            if colour is None:
                return httpx.Response(404, request=request)
            buffer = io.BytesIO()
            image = Image.new("RGBA", (180, 180), (255, 255, 255, 0))
            ImageDraw.Draw(image).ellipse((20, 20, 160, 160), fill=colour)
            image.save(buffer, format="PNG")
            return httpx.Response(200, content=buffer.getvalue(), request=request,
                                  headers={"content-type": "image/png"})
        self._swap_http_client(upstream)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.executemany(
                "INSERT INTO entity_link(id,entity_id,link_kind,label,url,hostname)"
                " VALUES(?,1,'official','官网存档（2022-05）',?,'web.archive.org')",
                [(901, "https://web.archive.org/web/20220512171500/https://ones-double.com/people/a/"),
                 (902, "https://web.archive.org/web/20221013093626/https://all-p.jp/talent/")])
        return asked

    async def test_archived_links_of_two_sites_get_two_marks(self):
        """存档链接的圆标按快照保存的原站缓存，两家关了门的公司不共用 web.archive.org 那一枚。"""
        asked = self._archive_links(lambda url: "red" if "ones-double.com" in url else "blue")
        answers = [await self.client.get(f"/link-mark?t=secret&id={link_id}")
                   for link_id in (901, 902)]
        self.assertEqual([answer.status_code for answer in answers], [200, 200])
        self.assertNotEqual(answers[0].content, answers[1].content)
        self.assertEqual(len(list((self.root / "generated" / "link-marks").iterdir())), 2)
        self.assertTrue(all(url.startswith("https://web.archive.org/") for url in asked), asked)

    async def test_a_link_marked_gone_gets_no_mark_and_no_upstream_request(self):
        """已失效的链接不再去取图标：停放域名的一次请求就够杀毒软件报警。"""
        asked = self._archive_links(lambda url: "red")
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "UPDATE entity_link SET metadata_json=? WHERE id=901",
                ('{"gone":{"at":"2026-09-26","note":"HTTP 404"}}',))
        answer = await self.client.get("/link-mark?t=secret&id=901")
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(asked, [])

    async def test_an_archived_site_without_a_usable_icon_shows_the_archive_one(self):
        """原站在快照里给不出合格的圆标，就用存档站自己的：点过去本来就是存档。"""
        from PIL import Image

        self._archive_links(lambda url: None if "id_/" in url else "green")
        answer = await self.client.get("/link-mark?t=secret&id=901")
        self.assertEqual(answer.status_code, 200)
        # 单色透明的图标做成品牌色圆底白字，圆底那一圈就是存档站给的绿。
        red, green, blue, _alpha = Image.open(io.BytesIO(answer.content)).convert("RGBA").getpixel((64, 6))
        self.assertGreater(green, max(red, blue))

    async def test_unauthorized_keeps_three_shapes_grouped_by_route_class(self):
        """401 三种形态按路由类分组，收敛到 Depends 之后也不许并成一种。

        页面路由跳登录页、页面资产回 PlainText 提示、API 与媒体路由回 JSON。
        统一 Depends 后每种形态仍必须出现在正确的路由类上。
        """
        page = await self.client.get("/")
        self.assertEqual(page.status_code, 303)
        self.assertEqual(page.headers["location"], "/login?next=/")

        asset = await self.client.get("/dist/peach-app.js")
        self.assertEqual(asset.status_code, 401)
        self.assertEqual(asset.text, "需要 ?t=口令")
        self.assertTrue(asset.headers["content-type"].startswith("text/plain"))

        api = await self.client.get("/api/items")
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.json(), {"error": "unauthorized"})

        media = await self.client.get("/thumb?id=1")
        self.assertEqual(media.status_code, 401)
        self.assertEqual(media.json(), {"error": "unauthorized"})

    async def test_emptying_the_recycle_bin_is_actually_wired_and_deletes_media(self):
        """接线本身要有测试：此前 dispatch 接上了 `/api/trash/empty`，函数却根本没写。

        单测直接调用函数抓不到这类错误，只有走一遍 HTTP 才会暴露。
        """
        disposed = await self.client.post("/api/feedback?t=secret",
                                          json={"id": 1, "kind": "dispose"})
        self.assertEqual(disposed.json()["disposal"], "trash")
        self.assertTrue(self.media_file.exists())

        emptied = await self.client.post("/api/trash/empty?t=secret")
        self.assertEqual(emptied.status_code, 200)
        self.assertEqual(emptied.json()["purged"], 1)
        self.assertEqual(emptied.json()["blocked"], [])
        self.assertFalse(self.media_file.exists(), "清空回收站必须真的删掉媒体文件")

        listed = await self.client.get("/api/items?t=secret")
        self.assertEqual([item["id"] for item in listed.json()["items"]], [])

    async def test_recycle_bin_route_and_batch_delete_are_reachable(self):
        legacy = await self.client.get("/trash?t=secret")
        self.assertEqual(legacy.status_code, 303)
        self.assertEqual(legacy.headers["location"], "/trash")
        page = await self.client.get("/trash")
        self.assertEqual(page.status_code, 200)
        refused = await self.client.post("/api/batch?t=secret",
                                         json={"ids": [1], "operation": "delete"})
        self.assertEqual(refused.status_code, 400, "不在回收站的资产不允许彻底删除")

        await self.client.post("/api/feedback?t=secret", json={"id": 1, "kind": "dispose"})
        deleted = await self.client.post("/api/batch?t=secret",
                                         json={"ids": [1], "operation": "delete"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["purged"], 1)
        self.assertFalse(self.media_file.exists())

    async def test_ads_queue_batch_disposal_removes_the_candidate(self):
        connection = sqlite3.connect(self.db)
        connection.execute(
            "UPDATE asset SET name='扫码加入福利群.mp4',size=?,duration=60 WHERE id=1",
            (20 * 1024 * 1024,),
        )
        connection.commit(); connection.close()

        before = await self.client.get("/api/ads?t=secret")
        self.assertEqual([item["id"] for item in before.json()["items"]], [1])

        disposed = await self.client.post(
            "/api/batch?t=secret", json={"ids": [1], "operation": "dispose"},
        )
        self.assertEqual(disposed.status_code, 200)
        self.assertEqual(disposed.json()["changed"], 1)

        after = await self.client.get("/api/ads?t=secret")
        self.assertEqual(after.json()["items"], [])
        recycle_bin = await self.client.get("/api/items?t=secret&state=trash")
        self.assertEqual([item["id"] for item in recycle_bin.json()["items"]], [1])

    async def test_ads_queue_can_filter_and_reverse_not_junk_decisions(self):
        connection = sqlite3.connect(self.db)
        connection.execute(
            "UPDATE asset SET name='Mib19.com.zip',medium='archive',size=? WHERE id=1",
            (14 * 1024**3,),
        )
        connection.commit(); connection.close()

        archive = await self.client.get("/api/ads?t=secret&kind=archive")
        self.assertEqual([item["id"] for item in archive.json()["items"]], [1])
        self.assertEqual(archive.json()["counts"]["archive"], 1)

        dismissed = await self.client.post(
            "/api/batch?t=secret", json={"ids": [1], "operation": "dismiss-junk"},
        )
        self.assertEqual(dismissed.status_code, 200)
        self.assertEqual((await self.client.get("/api/ads?t=secret")).json()["items"], [])
        excluded = (await self.client.get("/api/ads?t=secret&status=dismissed")).json()
        self.assertEqual([item["id"] for item in excluded["items"]], [1])

        reconsidered = await self.client.post(
            "/api/batch?t=secret", json={"ids": [1], "operation": "reconsider-junk"},
        )
        self.assertEqual(reconsidered.status_code, 200)
        self.assertEqual(
            [item["id"] for item in (await self.client.get("/api/ads?t=secret")).json()["items"]],
            [1],
        )

    async def test_search_history_is_shared_through_the_api(self):
        saved = await self.client.post("/api/search-history?t=secret", json={"query": "ABW"})
        self.assertEqual(saved.status_code, 200)
        history = await self.client.get("/api/search-history?t=secret")
        self.assertEqual(history.json()["items"], ["ABW"])
        removed = await self.client.post("/api/search-history?t=secret", json={"operation": "remove", "query": "ABW"})
        self.assertEqual(removed.status_code, 200)

    async def test_taste_bookmark_upload_count_reimport_and_source_removal(self):
        payload = b'<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DT><A HREF="https://rule34.xxx/?tags=feet">private bookmark</A></DL>'
        for _ in range(2):
            imported = await self.client.post("/api/taste/import?t=secret", content=payload,
                headers={"Content-Type": "application/octet-stream", "X-Peach-Filename": "bookmarks.html"})
            self.assertEqual(imported.status_code, 200, imported.text)
            dashboard = imported.json()["dashboard"]
            self.assertEqual(dashboard["summary"]["history_visits"], 0)
            self.assertEqual(dashboard["summary"]["bookmarks"], 1)
            self.assertNotIn("private bookmark", imported.text)
            self.assertNotIn("rule34.xxx/?tags", imported.text)
        listed = await self.client.get("/api/taste?t=secret&window=all")
        self.assertEqual(listed.json()["summary"]["bookmarks"], 1)
        removed = await self.client.post("/api/taste/source?t=secret", json={
            "operation": "remove", "source_key": dashboard["sources"][0]["source_key"], "window": "all"})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["removed"], 1)
        self.assertEqual(removed.json()["dashboard"]["summary"]["bookmarks"], 0)
        self.assertEqual(len(list(self.taste_import_root.iterdir())), 1)

    async def test_taste_refresh_reads_enabled_local_bookmarks(self):
        profile = self.root / "browser" / "Default"
        profile.mkdir(parents=True)
        (profile / "Bookmarks").write_text(json.dumps({"roots": {"bookmark_bar": {"children": [
            {"type": "url", "url": "https://rule34.xxx/?tags=feet", "name": "private local bookmark"}]}}}), encoding="utf-8")
        with patch("peach.taste_history.discover_history_sources", return_value=[]):
            configured = await self.client.post("/api/taste/source?t=secret", json={
                "operation": "add", "name": "测试 Chrome", "path": str(profile)})
            self.assertEqual(configured.status_code, 200, configured.text)
            refreshed = await self.client.post("/api/taste/refresh?t=secret", json={"window": "all"})
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json()["dashboard"]["summary"]["bookmarks"], 1)
        self.assertEqual(refreshed.json()["dashboard"]["summary"]["history_visits"], 0)
        self.assertEqual(refreshed.json()["warnings"], [])
        self.assertNotIn("private local bookmark", refreshed.text)
        with patch("peach.taste_history.discover_history_sources", return_value=[]):
            browser = refreshed.json()["dashboard"]["browsers"][0]
            self.assertEqual(len(browser["source_keys"]), 1)
            removed = await self.client.post("/api/taste/source?t=secret", json={"operation": "delete_browser", "id": browser["id"]})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["dashboard"]["summary"]["bookmarks"], 0)
        self.assertEqual(removed.json()["dashboard"]["browsers"], [])
        self.assertTrue((profile / "Bookmarks").is_file())

    async def test_taste_import_combines_private_history_with_peach_behavior(self):
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                "UPDATE asset SET play_count=2,play_seconds=600,last_played=? WHERE id=1",
                (1_700_000_200,),
            )
            connection.commit()
        payload = json.dumps([{
            "url": "https://rule34.xxx/index.php?page=post&s=list&tags=tag+a",
            "dt": 1_700_000_000,
            "metadata": None,
        }]).encode()
        imported = await self.client.post(
            "/api/taste/import?t=secret",
            content=payload,
            headers={"Content-Type": "application/octet-stream",
                     "X-Peach-Filename": "history.json"},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        dashboard = imported.json()["dashboard"]
        self.assertEqual(dashboard["summary"]["history_visits"], 1)
        self.assertEqual(dashboard["summary"]["peach_items"], 1)
        self.assertEqual(dashboard["rankings"]["tags"][0]["name"], "Tag A")
        self.assertNotIn("rule34.xxx/index.php", imported.text)
        self.assertTrue(self.taste_store.is_file())
        self.assertTrue(self.taste_manifest.is_file())

        listed = await self.client.get("/api/taste?t=secret&window=all")
        self.assertEqual(listed.status_code, 200)
        source_key = listed.json()["sources"][0]["source_key"]
        removed = await self.client.post(
            "/api/taste/source?t=secret",
            json={"operation": "remove", "source_key": source_key, "window": "all"},
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json()["removed"], 1)
        self.assertEqual(removed.json()["dashboard"]["summary"]["history_visits"], 0)

    async def test_entity_focus_uses_the_apps_configured_avatar_root(self):
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name) "
            "VALUES(2,'performer','Alice','alice')"
        )
        connection.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(1,2,'performer','test',1.0)"
        )
        connection.commit(); connection.close()
        (self.avatar_root / "performer-2.face.json").write_text(
            '{"focus":{"axis":"y","pct":23}}', encoding="utf-8")

        response = await self.client.get(
            "/api/entity?t=secret&kind=performer&name=Alice"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["avatar_focus"], {"axis": "y", "pct": 23})

    async def test_entity_gives_direct_entries_only_for_the_sites_it_has_an_id_for(self):
        """外部入口随资料一起下发；账本里没有那个站点 id 的，那一枚就不在列表里。"""
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name) "
            "VALUES(3,'performer','七沢みあ','七沢みあ')"
        )
        connection.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(1,3,'performer','test',1.0)"
        )
        connection.execute(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id) "
            "VALUES(3,'javdb','performer','NPD3')"
        )
        connection.commit(); connection.close()

        response = await self.client.get(
            "/api/entity?t=secret&kind=performer&name=" + quote("七沢みあ")
        )
        self.assertEqual(response.status_code, 200, response.text)
        entries = response.json()["entry_links"]
        self.assertEqual([row["site"] for row in entries], ["javdb", "missav"])
        self.assertEqual([row["slot"] for row in entries], ["mark", "mark"])
        self.assertEqual(entries[0]["url"], "https://javdb.com/actors/NPD3")
        self.assertTrue(entries[1]["url"].startswith("https://missav.ws/dm3/cn/actresses/%"))

    async def test_entity_entries_use_paired_current_han_stage_name(self):
        connection = sqlite3.connect(self.db)
        metadata = {"name_localization": {"source": "avdb-actor-mapping",
                                          "jp": "水谷心音", "zh_cn": "水谷心音"}}
        connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,metadata_json) "
                           "VALUES(3,'performer','水谷心音','水谷心音',?)",
                           (json.dumps(metadata, ensure_ascii=False),))
        connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                           "VALUES(1,3,'performer','test',1)")
        connection.execute("INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id) "
                           "VALUES(3,'javdb','performer','sample')")
        connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence) "
                           "VALUES(3,'藤崎りお','藤崎りお','r18:performer',1)")
        connection.commit()
        connection.close()
        response = await self.client.get("/api/entity?t=secret&kind=performer&name=" + quote("水谷心音"))
        self.assertEqual(response.status_code, 200, response.text)
        missav = next(row for row in response.json()["entry_links"] if row["site"] == "missav")
        self.assertEqual(missav["url"], "https://missav.ws/dm3/cn/actresses/" + quote("水谷心音"))

    async def test_review_queue_is_readable_and_decisions_are_persisted(self):
        response = await self.client.get("/api/review?t=secret")
        self.assertEqual(response.status_code, 200)
        self.assertIn("metadata_fields", response.json()["sections"])
        self.assertIn("creator_tags", response.json()["sections"])
        decided = await self.client.post(
            "/api/review/decision?t=secret",
            json={"category": "video_endcards", "item_key": "12510", "status": "skipped"},
        )
        self.assertEqual(decided.status_code, 200)

    async def test_quality_goal_queue_is_readable_from_management(self):
        saved = await self.client.post(
            "/api/quality-goal?t=secret", json={"id": 1, "wanted": True},
        )
        self.assertEqual(saved.status_code, 200)
        response = await self.client.get("/api/quality-goals?t=secret&limit=20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["id"], 1)

    async def test_metadata_field_candidate_is_approved_through_the_http_contract(self):
        connection = sqlite3.connect(self.db)
        connection.execute("UPDATE asset SET code='ABC-001' WHERE id=1")
        connection.commit(); connection.close()
        candidate = {
            "candidate_key": "ABC-001:studio:r18dev:abc", "source": "r18dev",
            "source_url": "https://r18.dev/example", "confidence": 0.9,
            "provider_id": "ABC-001",
            "value": "Studio B", "display_value": "Studio B", "warnings": [],
            "raw_snapshot": "/evidence.json",
        }
        fields = ["item_key", "code", "query", "field", "field_label", "current_value",
                  "candidates_json", "source_count", "status", "size_gb", "videos", "fetched_at"]
        path = self.candidate_root / "metadata-field-candidates-20260822.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerow({
                "item_key": "ABC-001:studio", "code": "ABC-001", "query": "ABC-001",
                "field": "studio", "field_label": "厂牌", "current_value": "Studio A",
                "candidates_json": json.dumps([candidate]), "source_count": "1",
                "status": "candidate", "size_gb": "1", "videos": "1", "fetched_at": "now",
            })
        approved = await self.client.post("/api/review/decision?t=secret", json={
            "category": "metadata_fields", "item_key": "ABC-001:studio",
            "candidate_key": candidate["candidate_key"], "status": "approved",
        })
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["applied_assets"], 1)
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute("SELECT studio FROM asset WHERE id=1").fetchone()[0], "Studio B")
        self.assertEqual(connection.execute(
            "SELECT e.canonical_name FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "WHERE ae.asset_id=1 AND ae.source='javinizer:r18dev:studio'"
        ).fetchall(), [("Studio B",)])
        connection.close()

    async def test_approving_against_a_stale_revision_answers_409_with_the_current_one(self):
        connection = sqlite3.connect(self.db)
        connection.execute("UPDATE asset SET code='ABC-001' WHERE id=1")
        connection.commit(); connection.close()
        candidate = {
            "candidate_key": "ABC-001:studio:r18dev:abc", "source": "r18dev",
            "source_url": "https://r18.dev/example", "confidence": 0.9,
            "provider_id": "ABC-001",
            "value": "Studio B", "display_value": "Studio B", "warnings": [],
            "raw_snapshot": "/evidence.json",
        }
        fields = ["item_key", "code", "query", "field", "field_label", "current_value",
                  "candidates_json", "source_count", "status", "size_gb", "videos", "fetched_at"]
        path = self.candidate_root / "metadata-field-candidates-20260822.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerow({
                "item_key": "ABC-001:studio", "code": "ABC-001", "query": "ABC-001",
                "field": "studio", "field_label": "厂牌", "current_value": "Studio A",
                "candidates_json": json.dumps([candidate]), "source_count": "1",
                "status": "candidate", "size_gb": "1", "videos": "1", "fetched_at": "now",
            })
        body = {
            "category": "metadata_fields", "item_key": "ABC-001:studio",
            "candidate_key": candidate["candidate_key"], "status": "approved",
        }
        stale = await self.client.post("/api/review/decision?t=secret",
                                       json={**body, "expected_revision": 7})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["expected_revision"], 7)
        self.assertEqual(list(stale.json()["revisions"].values()), [0])
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute(
            "SELECT studio FROM asset WHERE id=1").fetchone()[0], "Studio A")
        connection.close()
        fresh = await self.client.post("/api/review/decision?t=secret",
                                       json={**body, "expected_revision": 0})
        self.assertEqual(fresh.status_code, 200, fresh.text)
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute(
            "SELECT studio,mutation_revision FROM asset WHERE id=1").fetchone(), ("Studio B", 1))
        connection.close()

    async def test_metadata_tag_approval_promotes_an_existing_tag_to_official_source(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("UPDATE asset SET code='ABC-001' WHERE id=1")
            connection.execute(
                "INSERT INTO asset_tag(asset_id,tag,confidence,source) "
                "VALUES(1,'美乳',0.4,'filename')"
            )
        candidate = {
            "candidate_key": "ABC-001:tags:r18dev:abc", "source": "r18dev",
            "source_url": "https://r18.dev/example", "confidence": 0.9,
            "provider_id": "ABC-001",
            "value": ["美乳", "颜射"], "display_value": "美乳、颜射", "warnings": [],
        }
        fields = ["item_key", "code", "query", "field", "field_label", "current_value",
                  "candidates_json", "source_count", "status", "size_gb", "videos", "fetched_at"]
        path = self.candidate_root / "metadata-field-candidates-20260822.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerow({
                "item_key": "ABC-001:tags", "code": "ABC-001", "query": "ABC-001",
                "field": "tags", "field_label": "标签", "current_value": "美乳",
                "candidates_json": json.dumps([candidate], ensure_ascii=False), "source_count": "1",
                "status": "candidate", "size_gb": "1", "videos": "1", "fetched_at": "now",
            })
        approved = await self.client.post("/api/review/decision?t=secret", json={
            "category": "metadata_fields", "item_key": "ABC-001:tags",
            "candidate_key": candidate["candidate_key"], "status": "approved",
        })
        self.assertEqual(approved.status_code, 200, approved.text)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute(
                "SELECT tag,confidence,source FROM asset_tag WHERE asset_id=1 "
                "AND tag IN ('美乳','颜射') ORDER BY tag"
            ).fetchall(), [
                ("美乳", 0.9, "javinizer:r18dev:tag"),
                ("颜射", 0.9, "javinizer:r18dev:tag"),
            ])

    async def _tags_candidate_with_unmapped_genres(self, candidate: dict) -> None:
        fields = ["item_key", "code", "query", "field", "field_label", "current_value",
                  "candidates_json", "source_count", "status", "size_gb", "videos", "fetched_at"]
        path = self.candidate_root / "metadata-field-candidates-20260911.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerow({
                "item_key": "CARIB-001:tags", "code": "CARIB-001", "query": "CARIB-001",
                "field": "tags", "field_label": "标签", "current_value": "",
                "candidates_json": json.dumps([candidate], ensure_ascii=False), "source_count": "1",
                "status": "candidate", "size_gb": "1", "videos": "1", "fetched_at": "now",
            })

    async def _review_tags_candidate(self) -> dict:
        queue = await self.client.get("/api/review?t=secret")
        self.assertEqual(queue.status_code, 200, queue.text)
        row = next(row for row in queue.json()["sections"]["metadata_fields"]
                   if row["item_key"] == "CARIB-001:tags")
        return row["candidates"][0]

    async def test_a_recorded_genre_joins_the_candidate_and_is_written_on_approval(self):
        """收录一个 genre 之后，队列里那条候选当场就多出这个标签，批准写下去的也是它。"""
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE asset SET code='CARIB-001' WHERE id=1")
            connection.commit()
        await self._tags_candidate_with_unmapped_genres({
            "candidate_key": "CARIB-001:tags:caribbeancom:abc", "source": "caribbeancom",
            "source_url": "https://example.invalid/carib", "confidence": 0.9,
            "provider_id": "CARIB-001", "value": ["中出内射"], "display_value": "中出内射",
            "unmapped_genres": ["まだ決めていない分類", "まだ知らない分類"],
            "warnings": ["来源还有 2 个未收录 genre：まだ決めていない分類、まだ知らない分類"],
        })
        before = await self._review_tags_candidate()
        self.assertEqual(before["unmapped_genres"], ["まだ決めていない分類", "まだ知らない分類"])
        self.assertEqual(before["warnings"], [], "未决的词挪进结构化字段，不再只是一句话")

        # 素材必须是占位词，不能拿真实来源词：词表一收那个词，这条路径就没有未决的
        # 词可测，而扩词表的人跑不到 web 域。2026-09-21 先后拿 `シャワー` 和 `温泉`
        # 当素材，两次都是这样把测试跑红的。
        recorded = await self.client.post("/api/review/genre?t=secret",
                                          json={"genre": "まだ知らない分類", "tag": "浴室"})
        self.assertEqual(recorded.status_code, 200, recorded.text)
        excluded = await self.client.post("/api/review/genre?t=secret",
                                          json={"genre": "まだ決めていない分類", "tag": ""})
        self.assertEqual(excluded.status_code, 200, excluded.text)

        after = await self._review_tags_candidate()
        self.assertEqual(after["value"], ["中出内射", "浴室"])
        self.assertEqual(after["display_value"], "中出内射、浴室")
        self.assertEqual(after["unmapped_genres"], [], "两个都有了结论，不该再回来问")

        approved = await self.client.post("/api/review/decision?t=secret", json={
            "category": "metadata_fields", "item_key": "CARIB-001:tags",
            "candidate_key": "CARIB-001:tags:caribbeancom:abc", "status": "approved",
        })
        self.assertEqual(approved.status_code, 200, approved.text)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual([row[0] for row in connection.execute(
                "SELECT tag FROM asset_tag WHERE asset_id=1 AND tag IN ('中出内射','浴室') "
                "ORDER BY tag")],
                ["中出内射", "浴室"], "页面上多出来的那个标签必须真的落库")

    async def test_an_old_candidate_carries_its_unmapped_genres_in_the_warning_only(self):
        """2026-09-11 之前写下的候选文件没有结构化那一份，收录按钮同样要能出现。"""
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE asset SET code='CARIB-001' WHERE id=1")
            connection.commit()
        await self._tags_candidate_with_unmapped_genres({
            "candidate_key": "CARIB-001:tags:caribbeancom:old", "source": "caribbeancom",
            "source_url": "https://example.invalid/carib", "confidence": 0.9,
            "provider_id": "CARIB-001", "value": ["中出内射"], "display_value": "中出内射",
            "warnings": ["来源还有 1 个未收录 genre：まだ知らない分類"],
        })
        self.assertEqual((await self._review_tags_candidate())["unmapped_genres"],
                         ["まだ知らない分類"])

    async def test_the_genre_tag_answers_to_the_same_rules_as_any_other_tag(self):
        """这里收录的名字和作品页加的标签进同一套词表，判据不能各写一套。"""
        performer = await self.client.post("/api/review/genre?t=secret",
                                           json={"genre": "シャワー", "tag": "演员:桃子"})
        self.assertEqual(performer.status_code, 400, performer.text)
        long_name = await self.client.post("/api/review/genre?t=secret",
                                           json={"genre": "シャワー", "tag": "浴" * 81})
        self.assertEqual(long_name.status_code, 400, long_name.text)
        blank = await self.client.post("/api/review/genre?t=secret", json={"genre": "  ", "tag": "x"})
        self.assertEqual(blank.status_code, 400, blank.text)
        # 两头的空格是笔误，不是另一个标签：收下来并按去掉空格的写法存。
        trimmed = await self.client.post("/api/review/genre?t=secret",
                                         json={"genre": "シャワー", "tag": " 浴室 "})
        self.assertEqual(trimmed.status_code, 200, trimmed.text)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute(
                "SELECT peach_tag FROM genre_decision WHERE source_genre='シャワー'").fetchone(),
                ("浴室",))

    async def test_catalog_keeps_missing_performers_separate_from_release_code(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(
                "INSERT INTO asset(id,location,path,name,medium,size,code,studio,first_seen) "
                "VALUES(29999,'local',?,'JBS-023.mp4','video',10,'JBS-023','Prestige','2026-09-05')",
                (str((self.media_root / 'JBS-023.mp4').resolve()),),
            )
            con.commit()
        response = await self.client.get('/api/items?t=secret&loc=local')
        self.assertEqual(response.status_code, 200)
        item = next(row for row in response.json()['items'] if row['id'] == 29999)
        self.assertEqual(item['code'], 'JBS-023')
        self.assertFalse(item.get('creator'))
        self.assertEqual(item['performers'], [])
        self.assertEqual(item['performer_entities'], [])

    async def test_auth_and_items_contract(self):
        denied = await self.client.get("/api/items")
        self.assertEqual(denied.status_code, 401)
        wrong_cookie = await self.client.get(
            "/api/items", headers={"Cookie": "notok=secret"}
        )
        self.assertEqual(wrong_cookie.status_code, 401)
        cookie = await self.client.get(
            "/api/items?loc=local&limit=10", headers={"Cookie": "tok=secret"}
        )
        self.assertEqual(cookie.status_code, 200)
        response = await self.client.get("/api/items?t=secret&loc=local&limit=10")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 1)
        self.assertTrue(data["items"][0]["has_thumb"])
        self.assertNotIn("path", data["items"][0])
        self.assertEqual(response.headers["cache-control"], "no-store")

        scoped = await self.client.get("/api/facets?t=secret&id=1")
        self.assertEqual(scoped.status_code, 200)
        self.assertEqual(scoped.json()["locations"], [
            {"k": "local", "n": 1, "played": 0},
        ])
        self.assertEqual(scoped.json()["orientations"], [
            {"k": "横屏", "n": 1},
        ])

    async def test_global_facets_supply_recommendations_for_an_untagged_detail(self):
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,first_seen) "
            "VALUES(2,'online','https://example.test/untagged','Untagged','video','2026-08-31')"
        )
        connection.commit(); connection.close()

        scoped = await self.client.get("/api/facets?t=secret&id=2")
        recommended = await self.client.get("/api/facets?t=secret")
        self.assertEqual(scoped.status_code, 200)
        self.assertEqual(scoped.json()["tags"], [])
        self.assertEqual(recommended.status_code, 200)
        self.assertIn({"k": "Tag A", "n": 1, "cat": "general"}, recommended.json()["tags"])

    async def test_saved_online_asset_survives_the_default_thumbnail_filter(self):
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,first_seen) "
            "VALUES(2,'online','https://example.test/post/2','Saved online post','video','2026-08-30')"
        )
        connection.commit(); connection.close()

        listed = await self.client.get(
            "/api/items?t=secret&loc=online&thumb=1&limit=10"
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)
        self.assertEqual(listed.json()["items"][0]["location"], "online")
        self.assertFalse(listed.json()["items"][0]["has_thumb"])

    async def test_multipart_api_keeps_assets_separate_but_returns_an_ordered_queue(self):
        connection = sqlite3.connect(self.db)
        connection.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,studio,code,duration,"
            "width,height,first_seen) VALUES(?,'local',?,?,'video',?,'S1','OJIE-325',?,"
            "1920,1080,'2026-08-28')",
            [
                (4, str(self.media_root / "OJIE-325-B.mp4"), "OJIE-325-B.mp4", 10_000, 14349),
                (5, str(self.media_root / "OJIE-325-A.mp4"), "OJIE-325-A.mp4", 20_000, 14281),
            ],
        )
        connection.commit(); connection.close()

        listed = (await self.client.get("/api/items?t=secret&q=OJIE-325&limit=10")).json()
        self.assertEqual(listed["total"], 2)
        self.assertTrue(all(item["part_group"]["count"] == 2 for item in listed["items"]))
        queue = (await self.client.get("/api/parts?t=secret&id=4")).json()
        self.assertEqual([item["part_label"] for item in queue["items"]], ["A", "B"])
        self.assertTrue(all("path" not in item for item in queue["items"]))

    async def test_unexpected_contract_errors_are_logged_but_not_exposed(self):
        with patch(
            "peach.routes_api.web_contract.dispatch_api_get",
            side_effect=RuntimeError("private C:\\ledger.db detail"),
        ):
            with self.assertLogs("peach.routes_api", level="ERROR") as captured:
                response = await self.client.get("/api/items?t=secret")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": "internal server error"})
        self.assertNotIn("ledger.db", response.text)
        self.assertIn("private C:\\ledger.db detail", "\n".join(captured.output))

    async def test_preference_is_an_independent_authenticated_write(self):
        denied = await self.client.post(
            "/api/preference", json={"id": 1, "liked": True, "reason": "镜头自然"},
        )
        self.assertEqual(denied.status_code, 401)
        saved = await self.client.post(
            "/api/preference?t=secret",
            json={"id": 1, "liked": True, "reason": "镜头自然"},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), {
            "ok": True, "liked": True, "like_reason": "镜头自然",
        })
        detail = await self.client.get("/api/item?t=secret&id=1")
        self.assertTrue(detail.json()["liked"])
        self.assertEqual(detail.json()["like_reason"], "镜头自然")

    async def test_item_detail_exposes_official_title_and_official_tag_marker(self):
        connection = sqlite3.connect(self.db)
        connection.execute(
            "UPDATE asset SET catalog_title='正式作品标题',original_title='原标题',"
            "studio='Prestige',code='ABW-232' WHERE id=1"
        )
        connection.commit(); connection.close()
        detail = (await self.client.get("/api/item?t=secret&id=1")).json()
        self.assertEqual(detail["catalog_title"], "正式作品标题")
        self.assertEqual(detail["original_title"], "原标题")
        self.assertEqual(detail["display_code"], "ABW-232")
        self.assertIn("edition_badges", detail)
        official = next(tag for tag in detail["tags"] if tag["k"] == "官方标签")
        self.assertTrue(official["official"])

    async def test_provider_health_is_authenticated_and_secret_free(self):
        denied = await self.client.get("/api/providers")
        self.assertEqual(denied.status_code, 401)
        response = await self.client.get("/api/providers?t=secret")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["providers"]), 3)
        self.assertNotIn("secret_ref", response.text)

    async def test_item_tags_are_authenticated_and_source_evidence_is_preserved(self):
        denied = await self.client.post(
            "/api/item-tag", json={"id": 1, "operation": "remove", "tag": "Tag A"},
        )
        self.assertEqual(denied.status_code, 401)
        removed = await self.client.post(
            "/api/item-tag?t=secret",
            json={"id": 1, "operation": "remove", "tag": "Tag A"},
        )
        self.assertEqual(removed.status_code, 200)
        self.assertNotIn("Tag A", removed.json()["tags"])
        added = await self.client.post(
            "/api/item-tag?t=secret",
            json={"id": 1, "operation": "add", "tag": "手动标签"},
        )
        self.assertEqual(added.status_code, 200)
        self.assertIn("手动标签", added.json()["tags"])

    async def test_opencode_models_endpoint_is_explicit_and_normalized(self):
        denied = await self.client.get("/api/providers/opencode-go/models")
        self.assertEqual(denied.status_code, 401)
        with patch("peach.api.OpenCodeGoClient.list_models", return_value=[{
            "id": "kimi-k3", "object": "model", "owned_by": "opencode",
        }]) as discover:
            response = await self.client.get(
                "/api/providers/opencode-go/models?t=secret",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["models"][0]["id"], "kimi-k3")
        discover.assert_called_once_with()

    async def test_post_writes_only_test_database(self):
        response = await self.client.post(
            "/api/activity", headers={"X-Token": "secret"},
            json={"id": 1, "position": 50, "duration": 100, "delta": 7, "seeks": 1},
        )
        self.assertEqual(response.status_code, 200)
        con = sqlite3.connect(self.db)
        row = con.execute("SELECT play_seconds,seek_count,max_reached FROM asset WHERE id=1").fetchone()
        con.close()
        self.assertEqual(row, (7.0, 1, 0.5))

    async def test_home_sets_exact_http_only_cookie(self):
        denied = await self.client.get("/")
        self.assertEqual(denied.status_code, 303)
        self.assertEqual(denied.headers["location"], "/login?next=/")
        legacy = await self.client.get("/?t=secret")
        self.assertEqual(legacy.status_code, 303)
        self.assertEqual(legacy.headers["location"], "/")
        cookie = legacy.headers["set-cookie"]
        self.assertIn("peach_session=", cookie)
        self.assertNotIn("tok=secret", cookie)
        self.assertIn("HttpOnly", cookie)
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ready", response.text)

    async def test_playback_time_requires_a_session_and_survives_page_navigation(self):
        denied = await self.client.get("/assets/1?t=42")
        self.assertEqual(denied.status_code, 303)
        self.assertTrue(denied.headers["location"].startswith("/login?"))
        await self.client.get("/?t=secret")
        for path in ("/assets/1", "/posts/1", "/works/ABP-968"):
            response = await self.client.get(path + "?t=42&tag=a%20b&tag=c")
            self.assertEqual(response.status_code, 200, path)
            self.assertNotIn("location", response.headers)
        legacy = await self.client.get("/item/1?t=42&mix=7")
        self.assertEqual(legacy.headers["location"], "/assets/1?t=42&mix=7")
        page = await self.client.get(legacy.headers["location"])
        self.assertEqual(page.status_code, 200)

    async def test_query_credentials_are_removed_without_losing_page_parameters(self):
        response = await self.client.get(
            "/assets/1?tag=a%20b&t=secret&t=42&tag=c&t=se%63ret&playlist=7")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"],
                         "/assets/1?tag=a%20b&t=42&tag=c&playlist=7")
        self.assertIn("peach_session=", response.headers["set-cookie"])
        page = await self.client.get(response.headers["location"])
        self.assertEqual(page.status_code, 200)

    async def test_login_posts_the_token_without_putting_it_in_the_url(self):
        page = await self.client.get("/login?next=/stats")
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-page="login" data-next="/stats"', page.text)
        refused = await self.client.post(
            "/login", content="token=wrong&next=%2Fstats",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(refused.status_code, 401)
        accepted = await self.client.post(
            "/login", content="token=secret&next=%2Fstats",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(accepted.status_code, 303)
        self.assertEqual(accepted.headers["location"], "/stats")
        self.assertNotIn("secret", accepted.headers["location"])

    async def test_the_login_page_declares_the_icon_like_every_other_page(self):
        """书签地址是 `/`，没有会话时浏览器实际停在这一页，图标要在这里就声明。

        缺声明的后果不止是这一页少个角标：浏览器改去要 `/favicon.ico`，并把那一次的
        结果当成整个站的图标记进书签。声明和兜底路径都在，才不依赖谁先谁后。
        """
        page = await self.client.get("/login?next=/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('<link rel="icon" href="/favicon.ico" type="image/x-icon">', page.text)

    async def test_the_login_page_follows_the_chosen_theme(self):
        """登录页在拿到 cookie 之前出图，深浅色跟着同一个选择走，在第一次绘制前定下来。

        手动选的那一档存在页面自己的 localStorage 里，主题预读读它，排在页面包的样式之前；
        默认浅色、跟随系统的深色、手动那一档压过系统这三支的色值由截图验收。
        """
        page = await self.client.get("/login?next=/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('<meta name="color-scheme" content="light dark">', page.text)
        self.assertIn('<link rel="stylesheet" href="/dist/peach-pages.css">', page.text)
        self.assertIn('localStorage.getItem("peach.settings.v1")', page.text)
        self.assertLess(page.text.index('localStorage.getItem("peach.settings.v1")'),
                        page.text.index('<link rel="stylesheet"'), "主题预读要排在任何样式之前")

    async def test_refused_browser_logins_come_back_to_the_page_and_others_get_json(self):
        """浏览器表单提交（`Accept` 含 `text/html`）撞上限流或保持登录时间不合法时回登录页并原位报错，
        状态码照旧；其余调用方回 JSON。"""
        routes_auth._ATTEMPTS.clear()
        self.addCleanup(routes_auth._ATTEMPTS.clear)
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        page = {**form, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        body = "token=secret&next=%2Fstats&days=999"
        refused = await self.client.post("/login", content=body, headers=page)
        self.assertEqual(refused.status_code, 400)
        self.assertTrue(refused.headers["content-type"].startswith("text/html"))
        self.assertIn('data-page="login" data-next="/stats" data-error="请选择有效的保持登录时间">', refused.text)
        self.assertNotIn("set-cookie", refused.headers)
        refused = await self.client.post("/login", content=body, headers=form)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json(), {"error": "请选择有效的保持登录时间"})
        routes_auth._ATTEMPTS.clear()
        for _ in range(10):
            wrong = await self.client.post("/login", content="token=wrong&next=%2Fstats", headers=form)
            self.assertEqual(wrong.status_code, 401)
        limited = await self.client.post("/login", content="token=wrong&next=%2Fstats", headers=page)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["retry-after"], "60")
        self.assertTrue(limited.headers["content-type"].startswith("text/html"))
        self.assertIn('data-page="login" data-next="/stats" data-error="尝试次数较多，请一分钟后重试">',
                      limited.text)
        limited = await self.client.post("/login", content="token=secret&next=%2Fstats", headers=form)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json(),{"error": "尝试次数较多，请一分钟后重试"})

    async def test_index_directory_urls_keep_query_and_use_canonical_path(self):
        await self.client.post('/login', content='token=secret&next=%2F',
                               headers={'Content-Type': 'application/x-www-form-urlencoded'})
        for index in ('performers', 'creators', 'studios', 'agencies', 'tags'):
            for method in ('GET', 'HEAD'):
                response = await self.client.request(
                    method, f'/{index}/?q=A%2FB&view=cloud', follow_redirects=False)
                self.assertEqual(response.status_code, 307, (index, method))
                self.assertEqual(response.headers['location'], f'/{index}?q=A%2FB&view=cloud')

    async def test_entity_name_urls_redirect_to_ids_and_keep_query(self):
        await self.client.post('/login', content='token=secret&next=%2F',
                               headers={'Content-Type': 'application/x-www-form-urlencoded'})
        kinds = [('performer', 'performers'), ('creator', 'creators'), ('studio', 'studios'),
                 ('series', 'series'), ('agency', 'agencies'), ('tag', 'tags')]
        canonical = 'Canonical / 名%? #&'
        with closing(sqlite3.connect(self.db)) as connection, connection:
            for entity_id, (kind, prefix) in enumerate(kinds, 300):
                connection.execute('INSERT INTO entity(id,kind,canonical_name,normalized_name) VALUES(?,?,?,?)',
                                   (entity_id, kind, canonical, canonical.casefold()))
                connection.execute("INSERT INTO entity_alias VALUES(?,?,?,'test',1)",
                                   (entity_id, f'old-{kind}', f'old-{kind}'))
        query = '?tag=a%2Fb&tag=second&media=photos&set=9&offset=48&sort=release&dir=asc'
        for entity_id, (kind, prefix) in enumerate(kinds, 300):
            names = [canonical, f'old-{kind}'] + ([canonical.upper()] if kind == 'studio' else [])
            target = f'/{prefix}/{entity_id}' + query
            for name in names:
                for method in ('GET', 'HEAD'):
                    response = await self.client.request(method, f'/{prefix}/{quote(name, safe="")}' + query,
                                                         follow_redirects=False)
                    self.assertEqual(response.status_code, 307, (kind, name, method))
                    self.assertEqual(response.headers['location'], target)
                    self.assertEqual(response.headers['cache-control'], 'no-store')
            response = await self.client.get(target, follow_redirects=False)
            self.assertEqual(response.status_code, 200, kind)
            if kind != 'series':
                response = await self.client.get(f'/{prefix}?name=old-{kind}', follow_redirects=False)
                self.assertEqual(response.status_code, 200, kind)
                self.assertNotIn('location', response.headers)

    async def test_unknown_or_ambiguous_entity_urls_keep_the_page_shell(self):
        await self.client.post('/login', content='token=secret&next=%2F',
                               headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with closing(sqlite3.connect(self.db)) as connection, connection:
            for entity_id in (300, 301):
                connection.execute('INSERT INTO entity(id,kind,canonical_name,normalized_name) VALUES(?,?,?,?)',
                                   (entity_id, 'studio', f'Studio {entity_id}', f'studio {entity_id}'))
                connection.execute("INSERT INTO entity_alias VALUES(?,'Shared','shared','test',1)", (entity_id,))
        for name in ('Missing', 'Shared'):
            response = await self.client.get('/studios/' + name, follow_redirects=False)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('location', response.headers)

    async def test_client_routes_serve_the_single_page_surface(self):
        await self.client.post(
            "/login", content="token=secret&next=%2F",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        for path in ("/assets/1", "/performers/Alice", "/studios/Prestige",
                     "/creators/luckydog11", "/series/Example", "/performers",
                     "/creators", "/tags", "/stats", "/taste", "/immerse", "/wall", "/trash", "/review",
                     # 前端路由写好了不等于能直接打开：SPA 路径是逐条登记的，
                     # 漏登记时源码断言照样全绿，只有真的请求一次才会露出 404。
                     "/unseen", "/watch-later", "/flagged", "/junk-files",
                     "/data-cleanup", "/duplicates", "/quality-goals", "/tasks",
                     "/assets/2?mix=1", "/assets/2?parts=1", "/assets/2?editions=1", "/playlists",
                     "/resource-sync", "/settings/diagnostics",
                     "/assets/1?playlist=1", "/follow", "/subscriptions",
                     "/posts/190", "/sources", "/settings", "/works/ABC-001"):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("Peach test", response.text)

        diagnostic = await self.client.get('/diagnostics?tab=x', follow_redirects=False)
        self.assertEqual(diagnostic.status_code, 307)
        self.assertEqual(diagnostic.headers['location'], '/settings/diagnostics?tab=x')
        removed = await self.client.get("/entity/performer/Alice")
        self.assertEqual(removed.status_code, 404)

    async def test_entity_deep_link_carries_the_api_shape_in_its_document(self):
        await self.client.post('/login', content='token=secret&next=%2F',
                               headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.executemany(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name) VALUES(?,'performer',?,?)",
                [(901, '演示艺人', '演示艺人'), (902, '普通艺人', '普通艺人'), (903, '同台艺人', '同台艺人')])
            connection.execute("UPDATE asset SET code='SSIS-950',region='jp' WHERE id=1")
            connection.executemany(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source) VALUES(1,?,'performer','test')",
                [(901,), (903,)])
            connection.execute("INSERT INTO performer_profile(entity_id,source) VALUES(901,'test')")
            connection.execute(
                "INSERT INTO entity_alias VALUES(901,'</script><script>演示','alias','test',1)")
        shapes = (await self.client.get('/api/entity/shapes')).json()['entities']
        by_id = {shape['id']: shape for shape in shapes}
        for entity_id in (901, 902):
            for method in ('GET', 'HEAD'):
                response = await self.client.request(method, f'/performers/{entity_id}')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                if method == 'HEAD':
                    self.assertEqual(response.content, b'')
                    continue
                marker = '<script type="application/json" id="peach-entity-shape">'
                payload = response.text.split(marker, 1)[1].split('</script>', 1)[0]
                shape = json.loads(payload)
                self.assertEqual(shape['parts'], by_id.get(entity_id, {}).get('parts', []))
                self.assertEqual(shape['id'], entity_id)
                if entity_id == 901:
                    self.assertEqual(shape['parts'], ['costars', 'facts', 'jav'])
                    self.assertIn('</script><script>演示', shape['names'])
                    self.assertNotIn('<script>', payload)

    async def test_canonical_page_urls_require_authentication(self):
        for path in ('/assets/1', '/posts/1', '/works/ABC-001', '/performers/1',
                     '/tags/1', '/tasks', '/sources', '/subscriptions', '/settings/library',
                     '/item/1', '/mix/1/2', '/jav/movie/ABC-001', '/follow/item/1'):
            response = await self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 303, path)
            self.assertTrue(response.headers['location'].startswith('/login?next='), path)

    async def test_compatibility_page_urls_redirect_with_query_and_context(self):
        await self.client.post('/login', content='token=secret&next=%2F',
                               headers={'Content-Type': 'application/x-www-form-urlencoded'})
        routes = {'/item/42': '/assets/42', '/follow/item/42': '/posts/42',
                  '/jav/movie/abc001': '/works/ABC-001', '/works/abc001': '/works/ABC-001',
                  '/activity': '/tasks', '/scraping': '/sources',
                  '/configuration': '/settings/library', '/follow-manage': '/subscriptions'}
        query = '?tag=a%2Fb&tag=second&sort=release'
        for source, target in routes.items():
            for method in ('GET', 'HEAD'):
                response = await self.client.request(method, source + query, follow_redirects=False)
                self.assertEqual(response.status_code, 307, (source, method))
                self.assertEqual(response.headers['location'], target + query)
        for prefix, key in (('mix', 'mix'), ('parts', 'parts'), ('editions', 'editions'),
                            ('playlists', 'playlist')):
            for method in ('GET', 'HEAD'):
                response = await self.client.request(
                    method, f'/{prefix}/7/42' + query + f'&{key}=999&{key}=888', follow_redirects=False)
                self.assertEqual(response.status_code, 307)
                location = urlsplit(response.headers['location'])
                self.assertEqual(location.path, '/assets/42')
                self.assertEqual(parse_qs(location.query),
                                 {'tag': ['a/b', 'second'], 'sort': ['release'], key: ['7']})

    async def test_settings_sections_serve_the_page_and_unknown_sections_are_missing(self):
        await self.client.post('/login', content='token=secret&next=%2F',
                               headers={'Content-Type': 'application/x-www-form-urlencoded'})
        for section in ('appearance', 'playback', 'jav', 'search', 'content', 'library',
                        'subscriptions', 'downloads', 'network', 'maintenance'):
            for method in ('GET', 'HEAD'):
                response = await self.client.request(method, f'/settings/{section}')
                self.assertEqual(response.status_code, 200, (section, method))
        self.assertEqual((await self.client.get('/settings/missing')).status_code, 404)

    async def test_entity_id_pages_validate_kind_and_follow_merge_ids(self):
        await self.client.post('/login', content='token=secret&next=%2F',
                               headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO entity_redirect(old_id,target_id,source,merged_at) VALUES(399,1,'test','now')")
        for method in ('GET', 'HEAD'):
            response = await self.client.request(method, '/tags/399?tag=a%2Fb&tag=c', follow_redirects=False)
            self.assertEqual(response.status_code, 307)
            self.assertEqual(response.headers['location'], '/tags/1?tag=a%2Fb&tag=c')
            self.assertEqual((await self.client.request(method, '/tags/1')).status_code, 200)
            self.assertEqual((await self.client.request(method, '/performers/1')).status_code, 404)
            self.assertEqual((await self.client.request(method, '/tags/999999')).status_code, 404)

    async def test_jav_section_deep_links_serve_the_authenticated_application_shell(self):
        paths = ("/jav", "/jav?tab=discover", "/jav?tab=wants")
        for path in paths:
            response = await self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 303, path)
            self.assertTrue(response.headers["location"].startswith("/login?next="))
        await self.client.post(
            "/login", content="token=secret&next=%2F",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        for path in paths:
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Peach test", response.text)
                head = await self.client.head(path)
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.content, b"")
                self.assertEqual(head.headers["content-type"], response.headers["content-type"])

    async def test_playlist_api_saves_and_reads_a_mix_without_exposing_paths(self):
        await self.client.post(
            "/login", content="token=secret&next=%2F",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        created = await self.client.post("/api/playlist", json={
            "action": "create", "name": "测试 Mix", "asset_ids": [1],
            "source_kind": "mix", "source_seed_asset_id": 1,
        })
        self.assertEqual(created.status_code, 200)
        playlist_id = created.json()["playlist"]["id"]
        listing = (await self.client.get("/api/playlists")).json()["items"]
        self.assertEqual(listing[0]["item_count"], 1)
        detail = (await self.client.get(f"/api/playlist?id={playlist_id}")).json()
        self.assertEqual(detail["items"][0]["id"], 1)
        self.assertNotIn("path", detail["items"][0])

    async def test_pinned_frontend_vendor_assets_are_self_hosted(self):
        response = await self.client.get("/vendor/player.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "window.vendorReady=true;")
        self.assertEqual(response.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual((await self.client.get("/vendor/missing.js")).status_code, 404)

    async def test_standard_range_and_head_contract(self):
        headers = {"X-Token": "secret"}
        full = await self.client.get("/stream?id=1", headers=headers)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.content, b"0123456789")
        self.assertIn("etag", full.headers)
        self.assertIn("last-modified", full.headers)

        partial = await self.client.get(
            "/stream?id=1", headers={**headers, "Range": "bytes=2-5"}
        )
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"2345")
        self.assertEqual(partial.headers["content-range"], "bytes 2-5/10")

        suffix = await self.client.get(
            "/stream?id=1", headers={**headers, "Range": "bytes=-4"}
        )
        self.assertEqual(suffix.status_code, 206)
        self.assertEqual(suffix.content, b"6789")

        invalid = await self.client.get(
            "/stream?id=1", headers={**headers, "Range": "bytes=99-"}
        )
        self.assertEqual(invalid.status_code, 416)
        self.assertEqual(invalid.headers["content-range"], "bytes */10")

        head = await self.client.head("/stream?id=1", headers=headers)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head.headers["content-length"], "10")

    async def test_follow_qualities_lists_every_tier_the_source_offers(self):
        """档位数量由来源决定，不由我们写死。

        取证时那条给四档，实测线上另有五档（多一个 2160）的条目——所以解析用的是
        `video_alt_url` 加任意编号，而不是固定认三个 alt 字段。这里就用五档兜住：
        少认一档等于用户白白播低清。2160 按站点自己的写法叫 4K，不叫 2160p。
        """
        connection = sqlite3.connect(self.db)
        connection.executescript((ROOT / "migrations" / "0018_online_follow.sql").read_text(
            encoding="utf-8"))
        connection.execute(
            "INSERT INTO follow_source(id,provider,ref,label,url,semantics,created_at,updated_at)"
            " VALUES(2,'rule34video','r34/1','Creator','https://rule34video.com/u','work','x','x')"
        )
        connection.execute(
            "INSERT INTO follow_item(id,source_id,external_id,title,url,media_url,release_key,"
            "first_seen_at,last_seen_at) VALUES(9,2,'9','Remote',"
            "'https://rule34video.com/video/9/x/','https://rule34video.com/get_file/9.mp4/',"
            "'remote','x','x')"
        )
        connection.commit()
        connection.close()

        page = ("<script>"
                "video_url: 'https://rule34video.com/get_file/9_360.mp4/?v=t0';"
                "video_alt_url: 'https://rule34video.com/get_file/9_480p.mp4/?v=t1';"
                "video_alt_url2: 'https://rule34video.com/get_file/9_720p.mp4/?v=t2';"
                "video_alt_url3: 'https://rule34video.com/get_file/9_1080p.mp4/?v=t3';"
                "video_alt_url4: 'https://rule34video.com/get_file/9_2160p.mp4/?v=t4';"
                "</script>").encode("utf-8")

        def upstream(request):
            # 正片的字节数只有上游知道，端点解析完地址后顺手 HEAD 一次拿回来。
            if request.method == "HEAD":
                return httpx.Response(200, request=request, headers={
                    "content-type": "video/mp4", "content-length": "11458972"})
            return httpx.Response(200, stream=httpx.ByteStream(page), request=request,
                                  headers={"content-type": "text/html"})

        original = self.app.state.http_transport.client
        fake = httpx.Client(transport=httpx.MockTransport(upstream), follow_redirects=True)
        self.app.state.http_transport.client = fake
        try:
            response = await self.client.get("/follow-qualities?id=9&t=secret")
        finally:
            self.app.state.http_transport.client = original
            fake.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["qualities"], [
            {"height": 2160, "label": "4K"},
            {"height": 1080, "label": "1080p"},
            {"height": 720, "label": "720p"},
            {"height": 480, "label": "480p"},
            {"height": 360, "label": "360p"},
        ])
        self.assertEqual(response.json()["size"], 11458972)

    async def test_follow_qualities_reports_no_size_when_upstream_refuses_the_head(self):
        """字节数取不到不是错误：清晰度照给，`size` 给 None。

        浏览器量不到渐进下载的字节速率，播放器要靠文件大小和时长换出平均码率才能报
        MB/s。取不到时它退回按秒的读数——所以这里绝不能让一次 HEAD 失败把整张档位表
        也带走，也不能编一个字节数出来。
        """
        connection = sqlite3.connect(self.db)
        connection.executescript((ROOT / "migrations" / "0018_online_follow.sql").read_text(
            encoding="utf-8"))
        connection.execute(
            "INSERT INTO follow_source(id,provider,ref,label,url,semantics,created_at,updated_at)"
            " VALUES(2,'rule34video','r34/1','Creator','https://rule34video.com/u','work','x','x')"
        )
        connection.execute(
            "INSERT INTO follow_item(id,source_id,external_id,title,url,media_url,release_key,"
            "first_seen_at,last_seen_at) VALUES(9,2,'9','Remote',"
            "'https://rule34video.com/video/9/x/','https://rule34video.com/get_file/9.mp4/',"
            "'remote','x','x')"
        )
        connection.commit()
        connection.close()

        page = ("<script>"
                "video_url: 'https://rule34video.com/get_file/9_720p.mp4/?v=t0';"
                "</script>").encode("utf-8")

        def upstream(request):
            if request.method == "HEAD":
                return httpx.Response(403, request=request)
            return httpx.Response(200, stream=httpx.ByteStream(page), request=request,
                                  headers={"content-type": "text/html"})

        original = self.app.state.http_transport.client
        fake = httpx.Client(transport=httpx.MockTransport(upstream), follow_redirects=True)
        self.app.state.http_transport.client = fake
        try:
            response = await self.client.get("/follow-qualities?id=9&t=secret")
        finally:
            self.app.state.http_transport.client = original
            fake.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["qualities"], [{"height": 720, "label": "720p"}])
        self.assertIsNone(response.json()["size"])

    async def test_follow_stream_proxies_range_without_exposing_the_upstream_url(self):
        connection = sqlite3.connect(self.db)
        connection.executescript((ROOT / "migrations" / "0018_online_follow.sql").read_text(
            encoding="utf-8"))
        connection.execute(
            "INSERT INTO follow_source(id,provider,ref,label,url,semantics,created_at,updated_at)"
            " VALUES(1,'kemono','fanbox/1','Creator','https://kemono.cr/u','work','x','x')"
        )
        connection.execute(
            "INSERT INTO follow_item(id,source_id,external_id,title,url,media_url,release_key,"
            "first_seen_at,last_seen_at) VALUES(7,1,'7','Remote','https://kemono.cr/p/7',"
            "'https://img.kemono.cr/data/7.mp4','remote','x','x')"
        )
        connection.commit()
        connection.close()

        def upstream(request):
            self.assertEqual(request.headers.get("range"), "bytes=2-5")
            return httpx.Response(
                206, stream=httpx.ByteStream(b"2345"), request=request,
                headers={"content-type": "video/mp4", "content-range": "bytes 2-5/10",
                         "accept-ranges": "bytes", "content-length": "4"},
            )

        original = self.app.state.http_transport.client
        fake = httpx.Client(transport=httpx.MockTransport(upstream), follow_redirects=True)
        self.app.state.http_transport.client = fake
        try:
            denied = await self.client.get("/follow-stream?id=7")
            response = await self.client.get(
                "/follow-stream?id=7&t=secret", headers={"Range": "bytes=2-5"})
            with patch.object(self.app.state.follow_media_resolver, "resolve", side_effect=AssertionError("缓存命中不解析来源")):
                replay = await self.client.get("/follow-stream?id=7&t=secret", headers={"Range": "bytes=2-5"})
            self.assertEqual(replay.content, b"2345")
        finally:
            self.app.state.http_transport.client = original
            fake.close()
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        self.assertEqual(response.headers["content-range"], "bytes 2-5/10")
        self.assertNotIn("img.kemono.cr", response.text)

    async def test_follow_stream_only_offers_a_download_when_asked(self):
        """下载是显式动作，不是播放的副作用。

        文件名从条目标题来，不回传上游地址——上游主机名和签名同样不该外露，
        这和 `/follow-stream` 整体的边界是同一条。
        """
        connection = sqlite3.connect(self.db)
        connection.executescript((ROOT / "migrations" / "0018_online_follow.sql").read_text(
            encoding="utf-8"))
        connection.execute(
            "INSERT INTO follow_source(id,provider,ref,label,url,semantics,created_at,updated_at)"
            " VALUES(1,'kemono','fanbox/1','Creator','https://kemono.cr/u','work','x','x')"
        )
        connection.execute(
            "INSERT INTO follow_item(id,source_id,external_id,title,url,media_url,release_key,"
            "first_seen_at,last_seen_at) VALUES(8,1,'8','2B Camp [4K]','https://kemono.cr/p/8',"
            "'https://img.kemono.cr/data/8.webm','remote','x','x')"
        )
        connection.commit()
        connection.close()

        def upstream(request):
            return httpx.Response(
                200, stream=httpx.ByteStream(b"0123456789"), request=request,
                headers={"content-type": "video/webm", "content-length": "10"},
            )

        original = self.app.state.http_transport.client
        fake = httpx.Client(transport=httpx.MockTransport(upstream), follow_redirects=True)
        self.app.state.http_transport.client = fake
        try:
            played = await self.client.get("/follow-stream?id=8&t=secret")
            with patch.object(self.app.state.follow_media_resolver, "resolve", side_effect=AssertionError("完整缓存无需来源")):
                saved = await self.client.get("/follow-stream?id=8&t=secret&download=1")
        finally:
            self.app.state.http_transport.client = original
            fake.close()
        self.assertNotIn("content-disposition", played.headers)
        disposition = saved.headers["content-disposition"]
        self.assertIn("attachment;", disposition)
        self.assertIn("2B Camp [4K].webm", disposition)
        self.assertNotIn("img.kemono.cr", disposition)

    async def test_follow_image_and_preview_segments_replay_from_disk(self):
        from peach.preview_cache import PreviewCache
        from peach.remote_media_cache import cache_for
        item = SimpleNamespace(id=27, provider="simpcity", url="https://simpcity.cr/post/27",
            thumb_url="https://cdn.example/image.png", media_url="https://cdn.example/image.png", metadata={}, hidden_media=())
        cache = cache_for(self.app.state.web_contract)
        ref = PreviewCache(cache).register("EXM-001", "https://cdn.example/root.m3u8", identity="root", playlist=True)
        calls = []
        def upstream(request):
            calls.append(request.url.path)
            body, kind = ((b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\npart.ts\n#EXT-X-ENDLIST\n", "application/vnd.apple.mpegurl")
                          if request.url.path.endswith(".m3u8") else (b"media", "video/mp2t"))
            return httpx.Response(200, headers={"content-type": kind, "content-length": str(len(body))},
                                  stream=httpx.ByteStream(body), request=request)
        original = self.app.state.http_transport.client
        fake = httpx.Client(transport=httpx.MockTransport(upstream))
        self.app.state.http_transport.client = fake
        try:
            with patch("peach.routes_media.FollowStore.item", return_value=item), \
                 patch("peach.follow_stream.resolves_publicly", return_value=True):
                image = await self.client.get("/follow-image?id=27&t=secret")
                replay = await self.client.get("/follow-image?id=27&t=secret")
                self.assertEqual(image.content, replay.content)
                self.assertEqual(calls, ["/image.png"])
                playlist = await self.client.get(f"/jav-preview/EXM-001/{ref}?t=secret")
                self.assertEqual(playlist.status_code, 200)
                self.assertNotIn("cdn.example", playlist.text)
                segment = next(line for line in playlist.text.splitlines() if line.startswith("/jav-preview/"))
                for _ in range(2):
                    self.assertEqual((await self.client.get(segment + "?t=secret")).content, b"media")
                await self.client.get(f"/jav-preview/EXM-001/{ref}?t=secret")
                self.assertEqual(calls, ["/image.png", "/root.m3u8", "/part.ts"])
        finally:
            self.app.state.http_transport.client = original
            fake.close()

    async def test_official_mp4_preview_preserves_type_range_and_local_replay(self):
        from peach.preview_cache import PreviewCache
        from peach.remote_media_cache import cache_for
        cache = cache_for(self.app.state.web_contract)
        ref = PreviewCache(cache).register('EXM-001', 'https://www.prestige-av.com/api/media/public.mp4', playlist=False, media_type='video/mp4')
        body = b'\0\0\0\x18ftypisom' + b'\0' * 64
        calls = []
        def upstream(request):
            calls.append(request)
            if request.url.path.endswith('failure.mp4'):
                return httpx.Response(200, headers={'content-type': 'text/html'},
                                      stream=httpx.ByteStream(b'<html>unavailable</html>'), request=request)
            return httpx.Response(200, headers={'content-type': 'application/octet-stream', 'content-length': str(len(body))},
                                  stream=httpx.ByteStream(body), request=request)
        original = self.app.state.http_transport.client
        fake = httpx.Client(transport=httpx.MockTransport(upstream))
        self.app.state.http_transport.client = fake
        try:
            with patch('peach.follow_stream.resolves_publicly', return_value=False):
                response = await self.client.get(f'/jav-preview/EXM-001/{ref}?t=secret')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['content-type'], 'video/mp4')
                self.assertEqual(response.content, body)
                replay = await self.client.get(f'/jav-preview/EXM-001/{ref}?t=secret', headers={'Range': 'bytes=0-7'})
                self.assertEqual(replay.status_code, 206)
                self.assertEqual(replay.content, body[:8])
                self.assertEqual(replay.headers['content-range'], f'bytes 0-7/{len(body)}')
                self.assertEqual(len(calls), 1)
                invalid = PreviewCache(cache).register('EXM-002', 'https://www.prestige-av.com/api/media/failure.mp4',
                    playlist=False, media_type='video/mp4')
                failed = await self.client.get(f'/jav-preview/EXM-002/{invalid}?t=secret')
                self.assertEqual(failed.status_code, 404)
                self.assertEqual(failed.headers['cache-control'], 'no-store')
        finally:
            self.app.state.http_transport.client = original
            fake.close()

    async def test_preview_fixed_sources_and_hls_keep_unknown_hosts_guarded(self):
        from peach.preview_cache import PreviewCache
        from peach.remote_media_cache import cache_for
        cache = cache_for(self.app.state.web_contract)
        previews = PreviewCache(cache)
        calls = []
        def upstream(request):
            calls.append(str(request.url))
            if request.url.path.endswith('redirect.mp4'):
                return httpx.Response(302, headers={'location': 'https://private.example/video.mp4'}, request=request)
            body = b'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\npart.ts\n#EXT-X-ENDLIST\n' if request.url.path.endswith('.m3u8') else b'media'
            kind = 'application/vnd.apple.mpegurl' if request.url.path.endswith('.m3u8') else 'video/mp2t'
            return httpx.Response(200, headers={'content-type': kind, 'content-length': str(len(body))},
                                  stream=httpx.ByteStream(body), request=request)
        original = self.app.state.http_transport.client
        fake = httpx.Client(transport=httpx.MockTransport(upstream))
        self.app.state.http_transport.client = fake
        try:
            with patch('peach.follow_stream.resolves_publicly', return_value=False):
                ref = previews.register('EXM-010', 'https://jdforrepam.com/api/preview/root.m3u8', playlist=True)
                playlist = await self.client.get(f'/jav-preview/EXM-010/{ref}?t=secret')
                self.assertEqual(playlist.status_code, 200)
                segment = next(line for line in playlist.text.splitlines() if line.startswith('/jav-preview/'))
                self.assertEqual((await self.client.get(segment + '?t=secret')).content, b'media')
                self.assertEqual(len(calls), 2)
                for index, url in enumerate(('https://private.example/video.mp4',
                                             'https://www.prestige-av.com/api/media/redirect.mp4')):
                    code = f'EXM-{20 + index:03d}'
                    ref = previews.register(code, url, playlist=False)
                    failed = await self.client.get(f'/jav-preview/{code}/{ref}?t=secret')
                    self.assertEqual(failed.status_code, 404)
                    self.assertEqual(failed.headers['cache-control'], 'no-store')
                self.assertEqual(len(calls), 3)
                self.assertTrue(all('private.example' not in url for url in calls))
        finally:
            self.app.state.http_transport.client = original
            fake.close()

    async def test_follow_image_failure_has_no_image_body_and_can_be_retried(self):
        from types import SimpleNamespace
        item = SimpleNamespace(id=28, provider='simpcity', url='https://simpcity.cr/post/28',
            thumb_url='https://cdn.example/retry.png', media_url='https://cdn.example/retry.png', metadata={}, hidden_media=())
        calls = []
        def upstream(request):
            calls.append(request.url.path)
            return httpx.Response(404 if len(calls) == 1 else 200, headers={'content-type': 'image/png'},
                stream=httpx.ByteStream(b'' if len(calls) == 1 else b'restored-image'), request=request)
        original = self.app.state.http_transport.client
        fake = httpx.Client(transport=httpx.MockTransport(upstream))
        self.app.state.http_transport.client = fake
        try:
            with patch('peach.routes_media.FollowStore.item', return_value=item), \
                    patch('peach.follow_stream.resolves_publicly', return_value=True):
                failed = await self.client.get('/follow-image?id=28&t=secret')
                self.assertEqual(failed.status_code, 404)
                self.assertEqual(failed.content, b'')
                self.assertEqual(failed.headers['cache-control'], 'no-store')
                recovered = await self.client.get('/follow-image?id=28&t=secret')
                self.assertEqual(recovered.status_code, 200)
                self.assertEqual(recovered.content, b'restored-image')
                self.assertEqual((await self.client.get('/follow-image?id=28&t=secret')).content, recovered.content)
                self.assertEqual(calls, ['/retry.png', '/retry.png'])
        finally:
            self.app.state.http_transport.client = original
            fake.close()

    async def test_follow_image_uses_registered_media_hosts_and_checks_third_party_redirects(self):
        item = SimpleNamespace(id=29, provider='rule34xxx', url='https://rule34.xxx/post/29',
            thumb_url='https://api-cdn.rule34.xxx/poster.jpg', media_url=None, metadata={}, hidden_media=())
        calls = []
        def upstream(request):
            calls.append(request.url.path)
            headers = ({'location': 'https://cdn.example/blocked.jpg'} if request.url.path == '/redirect.jpg'
                       else {'content-type': 'image/jpeg'})
            return httpx.Response(302 if request.url.path == '/redirect.jpg' else 200,
                headers=headers, stream=httpx.ByteStream(b'poster'), request=request)
        original = self.app.state.http_transport.client
        fake = httpx.Client(transport=httpx.MockTransport(upstream))
        self.app.state.http_transport.client = fake
        try:
            with patch('peach.routes_media.FollowStore.item', return_value=item), \
                    patch('peach.follow_stream.resolves_publicly', return_value=False):
                known = await self.client.get('/follow-image?id=29&t=secret')
                self.assertEqual(known.status_code, 200)
                self.assertEqual(known.content, b'poster')
                item.thumb_url = 'https://cdn.example/blocked.jpg'
                self.assertEqual((await self.client.get('/follow-image?id=29&t=secret')).status_code, 404)
                item.thumb_url = 'https://api-cdn.rule34.xxx/redirect.jpg'
                self.assertEqual((await self.client.get('/follow-image?id=29&t=secret')).status_code, 404)
                self.assertEqual(calls, ['/poster.jpg', '/redirect.jpg'])
        finally:
            self.app.state.http_transport.client = original
            fake.close()

    async def test_follow_cover_serves_cached_frame_and_reports_retryable_failure(self):
        connection = sqlite3.connect(self.db)
        connection.executescript((ROOT / "migrations" / "0018_online_follow.sql").read_text(
            encoding="utf-8"))
        connection.execute(
            "INSERT INTO follow_source(id,provider,ref,label,url,semantics,created_at,updated_at)"
            " VALUES(1,'rule34paheal','artist','Artist','https://rule34.paheal.net/u',"
            "'work','x','x')"
        )
        connection.execute(
            "INSERT INTO follow_item(id,source_id,external_id,title,url,media_url,thumb_url,"
            "release_key,metadata_json,first_seen_at,last_seen_at) VALUES(7,1,'7','Remote',"
            "'https://rule34.paheal.net/post/view/7',"
            "'https://r34i.paheal-cdn.net/ab/cd/video',"
            "'https://r34t.paheal.net/ab/cd/video','remote',"
            "'{\"media_kind\":\"video\"}','x','x')"
        )
        connection.commit()
        connection.close()
        frame = self.root / "clear.jpg"
        frame.write_bytes(b"clear-jpeg")

        class Cover:
            def __init__(self):
                self.fail = False

            def cover(self, _item, _media=None):
                if self.fail:
                    from peach.follow_covers import FollowCoverUnavailable
                    raise FollowCoverUnavailable("test")
                return frame

        cover = Cover()
        self.app.state.follow_cover_service = cover
        denied = await self.client.get("/follow-cover?id=7")
        response = await self.client.get("/follow-cover?id=7&t=secret")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"clear-jpeg")
        self.assertEqual(response.headers["cache-control"],
                         "private, no-cache")
        cover.fail = True
        fallback = await self.client.get(
            "/follow-cover?id=7&t=secret", follow_redirects=False)
        # 生成失败回占位图，不再 302 到上游缩略图：这个端点存在的理由就是
        # 不把上游主机和地址交回浏览器。
        self.assertEqual(fallback.status_code, 404)
        self.assertNotIn("location", fallback.headers)
        self.assertNotIn("paheal", fallback.text)
        self.assertEqual(fallback.content, b"")
        self.assertEqual(fallback.headers['cache-control'], 'no-store')

    async def test_rule34video_cover_uses_only_posters_from_its_work(self):
        from peach.follow_covers import FollowCoverService
        from peach.follow_store import FollowStore
        from peach.follow_sources import FollowCandidate, SourceFetch
        from peach.follow_stream import FollowMediaResolver
        from peach.http import HttpResponse
        from PIL import Image

        connection = sqlite3.connect(self.db)
        connection.row_factory = sqlite3.Row
        connection.executescript((ROOT / "migrations" / "0021_follow_author_alias.sql").read_text(encoding="utf-8"))
        store = FollowStore(lambda: connection)
        source = store.register(provider="rule34video", ref="artist", label="Artist",
                                url="https://rule34video.com/models/artist/")
        store.record(source, SourceFetch(provider="rule34video", ref="artist",
            request_url="https://rule34video.com/models/artist/", semantics="work", candidates=(
                FollowCandidate(provider="rule34video", external_id="1", title="Evening Movie (60fps)",
                                url="https://rule34video.com/video/1/movie/", thumb_url="https://rule34video.com/missing.jpg"),
                FollowCandidate(provider="rule34video", external_id="2", title="Evening Movie (4K60fps)",
                                thumb_url="https://rule34video.com/available.jpg"),
                FollowCandidate(provider="rule34video", external_id="3", title="Another Movie",
                                thumb_url="https://rule34video.com/other.jpg"),
            )))
        item = next(row for row in store.items() if row.external_id == "1")
        connection.commit()
        connection.close()
        output = io.BytesIO()
        Image.new("RGB", (96, 54), "red").save(output, "PNG")
        requests = []

        def transport(request, timeout, limit):
            requests.append(request.url)
            return HttpResponse(404 if request.url.endswith("missing.jpg") else 200,
                                {"content-type": "image/png"}, output.getvalue())

        self.app.state.follow_cover_service = FollowCoverService(
            self.app.state.follow_cover_service.resolver, FollowMediaResolver(transport), self.root / "follow-covers")
        response = await self.client.get(f"/follow-cover?id={item.id}&t=secret")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(requests, ["https://rule34video.com/missing.jpg", "https://rule34video.com/available.jpg"])
        with Image.open(io.BytesIO(response.content)) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (96, 54)))
        self.assertEqual((await self.client.get(f"/follow-cover?id={item.id}&t=secret")).status_code, 200)
        self.assertEqual(len(requests), 2)

    async def test_custom_metadata_api_keeps_preview_read_only_and_requires_auth(self):
        root = self.root.resolve() / 'custom-secrets'
        body = {'name': '测试来源', 'endpoint': 'https://metadata.example.com/{code}',
                'fields': {'code': 'id', 'title': 'title'}, 'content_types': ['censored'], 'enabled': False}
        with patch.object(self.app.state.web_contract, 'follow_secrets_root', root):
            self.assertEqual((await self.client.get('/api/custom-metadata')).status_code, 401)
            with patch('peach.custom_metadata.fetch_json', return_value={'id': 'ABW-001', 'title': '标题'}):
                preview = await self.client.post('/api/custom-metadata', json=dict(body, action='preview', code='ABW-001'),
                                                 headers={'X-Token': 'secret'})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()['record']['title'], '标题')
            self.assertFalse((root / 'custom-metadata.json').exists())
            saved = await self.client.post('/api/custom-metadata', json=body, headers={'X-Token': 'secret'})
            self.assertEqual(saved.status_code, 200, saved.text)
            read = await self.client.get('/api/custom-metadata', headers={'X-Token': 'secret'})
            self.assertEqual(read.json(), saved.json())
            removed = await self.client.post('/api/custom-metadata', json={'action': 'delete', 'id': saved.json()['items'][0]['id']},
                                             headers={'X-Token': 'secret'})
            self.assertEqual(removed.json(), {'items': []})

    async def test_external_player_links_authorize_only_one_asset_and_support_ranges(self):
        denied = await self.client.post('/api/external-playback?id=1', json={})
        self.assertEqual(denied.status_code, 401)
        issued = await self.client.post('/api/external-playback?id=1', json={}, headers={'X-Token': 'secret'})
        self.assertEqual(issued.status_code, 200, issued.text)
        links = issued.json()
        self.assertNotIn('secret', json.dumps(links))
        self.assertNotIn(str(self.media_root), json.dumps(links))
        media = await self.client.get(links['url'], headers={'Range': 'bytes=2-5'})
        self.assertEqual(media.status_code, 206)
        self.assertEqual(media.content, b'2345')
        playlist = await self.client.get(links['playlist'])
        self.assertEqual(playlist.status_code, 200)
        self.assertIn('#EXTM3U', playlist.text)
        self.assertIn(links['url'], playlist.text)
        self.assertNotIn(str(self.media_root), playlist.text)
        self.assertEqual((await self.client.get(links['url'].replace('/1?', '/2?'))).status_code, 401)
        cross_site = await self.client.post('/api/external-playback?id=1', json={},
            headers={'X-Token': 'secret', 'Origin': 'https://attacker.example'})
        self.assertEqual(cross_site.status_code, 403)

    async def test_local_player_opens_only_the_resolved_video_without_watching_writes(self):
        from peach import local_player
        self.client.base_url = 'http://127.0.0.1'
        self.app.state.settings = replace(self.app.state.settings, follow_state_root=self.root / "state")
        executable = self.root / "PotPlayerMini64.exe"
        executable.write_bytes(b"MZfixture")
        headers = {"X-Token": "secret"}
        before = (await self.client.get('/api/item?id=1', headers=headers)).json()
        self.assertEqual(before['medium'], 'video')
        with patch.object(local_player, "platform", "win32"), patch.object(local_player, "launch") as launch:
            saved = await self.client.post('/api/configuration/local-player', headers=headers,
                                           json={'mode': 'external', 'executable': str(executable)})
            self.assertEqual(saved.status_code, 200, saved.text)
            read = await self.client.get('/api/configuration/local-player', headers=headers)
            self.assertEqual(read.json(), saved.json())
            opened = await self.client.post('/api/local-player/play', headers=headers,
                                            json={'kind': 'item', 'id': 1, 'path': 'untrusted.exe'})
            self.assertEqual(opened.json(), {'opened': True, 'player': 'PotPlayer'})
            self.assertEqual(launch.call_args.args[1], self.media_file)
        after = (await self.client.get('/api/item?id=1', headers=headers)).json()
        for key in ('play_count', 'play_seconds', 'leave_ratio', 'max_reached'):
            self.assertEqual(after.get(key), before.get(key))
        self.assertFalse(any(self.transcode_root.glob('*.mp4')))

    async def test_local_player_rejects_remote_cross_origin_and_invalid_requests(self):
        self.client.base_url = 'http://127.0.0.1'
        headers = {'X-Token': 'secret'}
        with patch('peach.local_player.launch') as launch:
            self.assertEqual((await self.client.post('/api/local-player/play', json={'kind': 'item', 'id': 1})).status_code, 401)
            cross = await self.client.post('/api/local-player/play', json={'kind': 'item', 'id': 1},
                                          headers={**headers, 'Origin': 'https://attacker.example'})
            self.assertEqual(cross.status_code, 403)
            for extra in ({'Host': 'attacker.example'}, {'X-Forwarded-For': '192.0.2.10'}):
                blocked = await self.client.post('/api/local-player/play', headers={**headers, **extra},
                                                 json={'kind': 'item', 'id': 1})
                self.assertEqual(blocked.status_code, 403)
                blocked = await self.client.get('/api/configuration/local-player', headers={**headers, **extra})
                self.assertEqual(blocked.status_code, 403)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('192.0.2.10', 1234)),
                                         base_url='http://test') as remote:
                self.assertEqual((await remote.post('/api/local-player/play', headers=headers,
                                  json={'kind': 'item', 'id': 1})).status_code, 403)
            for body in ({'kind': [], 'id': 1}, {'kind': 'item', 'id': True}, {'kind': 'item', 'id': 1, 'media': -1}):
                response = await self.client.post('/api/local-player/play', headers=headers, json=body)
                self.assertEqual(response.status_code, 400)
            launch.assert_not_called()

    async def test_external_follow_stream_ticket_is_scoped_to_media_and_expiry(self):
        from peach.external_playback import issue
        from fastapi.responses import Response
        key = self.app.state.external_playback_key
        ticket = issue(key, 'follow:19:2')
        with patch('peach.routes_media.follow_stream', return_value=Response(b'original-stream')) as stream:
            response = await self.client.get(f'/external-follow-stream/19?media=2&ticket={ticket}')
            self.assertEqual(response.content, b'original-stream')
            self.assertEqual(stream.call_args.kwargs, {'media': 2, 'args': {}})
            self.assertEqual((await self.client.get(f'/external-follow-stream/19?media=3&ticket={ticket}')).status_code, 401)
            self.assertEqual((await self.client.get(f'/external-follow-stream/20?media=2&ticket={ticket}')).status_code, 401)

    async def test_external_playlist_binds_public_origin_behind_tunnel(self):
        from types import SimpleNamespace
        from urllib.parse import urlsplit, parse_qs, urlencode
        self.app.state.tunnel = SimpleNamespace(snapshot=lambda: SimpleNamespace(url='https://public.example'))
        for headers in ({'X-Token': 'secret', 'Origin': 'https://public.example'}, {'X-Token': 'secret'}):
            issued = await self.client.post('/api/external-playback?id=1', json={}, headers=headers)
            self.assertEqual(issued.status_code, 200, issued.text)
            links = issued.json()
            playlist = await self.client.get(links['playlist'], headers={'Host': 'different.example'})
            self.assertEqual(playlist.status_code, 200)
            self.assertEqual(playlist.text.splitlines()[-1], 'https://public.example' + links['url'])
            parsed = urlsplit(links['playlist'])
            query = parse_qs(parsed.query)
            query['origin'] = ['https://attacker.example\n#EXTINF:0,injected']
            changed = parsed.path + '?' + urlencode(query, doseq=True)
            self.assertEqual((await self.client.get(changed)).status_code, 401)
            changed_stream = changed.replace('/external-playlist/1.m3u', '/external-stream/1')
            self.assertEqual((await self.client.get(changed_stream)).status_code, 401)
        for origin in ('https://attacker.example', 'https://user:password@public.example'):
            denied = await self.client.post('/api/external-playback?id=1', json={},
                headers={'X-Token': 'secret', 'Origin': origin})
            self.assertIn(denied.status_code, (400, 403))

    async def test_stream_session_cancel_is_authenticated_and_tombstoned(self):
        denied = await self.client.post("/api/stream-cancel?session=detail-1")
        self.assertEqual(denied.status_code, 401)

        cancelled = await self.client.post(
            "/api/stream-cancel?t=secret&session=detail-1"
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json(), {"ok": True, "cancelled": 0})

        stale = await self.client.get(
            "/stream?id=1&session=detail-1", headers={"X-Token": "secret"}
        )
        self.assertEqual(stale.status_code, 410)

    async def test_remote_session_preserves_open_ended_range(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE asset SET location='115' WHERE id=1")
        con.commit()
        con.close()
        response = await self.client.get(
            "/stream?id=1&session=remote-standard",
            headers={"X-Token": "secret", "Range": "bytes=0-"},
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"0123456789")
        self.assertEqual(response.headers["content-range"], "bytes 0-9/10")
        self.assertEqual(response.headers["content-length"], "10")

    async def test_remote_stream_plan_and_hls_playlist_are_segmented(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE asset SET location='115', duration=13.5, path=? WHERE id=1",
                    (str(self.hls_file),))
        con.commit()
        con.close()

        # 默认计划是标准 Range：HLS 的 TS 分片装不下浏览器能解的 HEVC，见 ADR-0016。
        default_plan = await self.client.get(
            "/api/stream-plan?id=1&session=hls-test&t=secret",
        )
        self.assertEqual(default_plan.status_code, 200)
        self.assertEqual(default_plan.json()["protocol"], "range")
        self.assertIn("/stream?id=1", default_plan.json()["src"])

        plan = await self.client.get(
            "/api/stream-plan?id=1&session=hls-test&mode=hls&t=secret",
        )
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(plan.json()["protocol"], "hls")
        self.assertEqual(plan.json()["segment_seconds"], 6)
        self.assertIn("/stream/hls/1/index.m3u8?session=hls-test", plan.json()["src"])

        playlist = await self.client.get(
            "/stream/hls/1/index.m3u8?session=hls-test&t=secret",
        )
        self.assertEqual(playlist.status_code, 200)
        self.assertEqual(playlist.headers["content-type"], "application/vnd.apple.mpegurl")
        self.assertEqual(playlist.text.count("#EXTINF:"), 3)

    async def test_incompatible_video_uses_time_slices_without_whole_movie_conversion(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE asset SET duration=7632.28 WHERE id=1")
        con.commit()
        con.close()
        with patch.object(self.app.state.transcode_service, 'requires_conversion', return_value=True), \
                patch.object(self.app.state.transcode_service, 'browser_path',
                             side_effect=AssertionError('whole movie conversion')):
            response = await self.client.get('/api/stream-plan?id=1&session=s&t=secret')
            self.assertEqual(response.json()['protocol'], 'hls')
            playlist = await self.client.get('/stream/hls/1/index.m3u8?session=s&t=secret')
            self.assertEqual(playlist.status_code, 200)
            self.assertIn('#EXTINF:0.280', playlist.text)

    async def test_unknown_ledger_duration_still_slices_by_the_probed_duration(self):
        """账本没记时长（或记成 -1）的 HEVC MP4 也按片切：时长取 ffprobe 报的，不走整片转码。"""
        from peach.transcodes import _MediaProfile
        con = sqlite3.connect(self.db)
        con.execute("UPDATE asset SET duration=-1 WHERE id=1")
        con.commit()
        con.close()
        # 探测结果由 `_probe` 给，但“走不走探测”由 `resolver.ffprobe()` 定：它返回 None 时判据
        # 在探测之前就退回 Range。两个都接手，这条用例才在装与不装 ffmpeg 的机器上讲同一件事。
        from peach.ffmpeg import BinaryChoice
        with patch.object(self.app.state.transcode_service.resolver, "ffprobe",
                          return_value=BinaryChoice(Path("ffprobe"), "test")), \
                patch.object(self.app.state.transcode_service, "_probe",
                          return_value=_MediaProfile("hevc", "yuv420p", "mp3", duration=13.5)), \
                patch.object(self.app.state.transcode_service, "browser_path",
                             side_effect=AssertionError("whole movie conversion")):
            plan = (await self.client.get("/api/stream-plan?id=1&session=s&t=secret")).json()
            self.assertEqual((plan["protocol"], plan["segments"], plan["duration"]), ("hls", 3, 13.5))
            playlist = await self.client.get("/stream/hls/1/index.m3u8?session=s&t=secret")
        self.assertEqual(playlist.status_code, 200)
        self.assertEqual(playlist.text.count("#EXTINF:"), 3)

    async def test_unprobeable_file_without_duration_gets_range_and_a_404_playlist(self):
        """moov 缺失的文件 ffprobe 读不出、账本也没时长：计划回 Range，播放列表回 404，不是 500。"""
        con = sqlite3.connect(self.db)
        con.execute("UPDATE asset SET duration=-1 WHERE id=1")
        con.commit()
        con.close()
        with patch.object(self.app.state.transcode_service, "_probe", return_value=None):
            plan = (await self.client.get("/api/stream-plan?id=1&session=s&t=secret")).json()
            playlist = await self.client.get("/stream/hls/1/index.m3u8?session=s&t=secret")
        self.assertEqual(plan["protocol"], "range")
        self.assertEqual(playlist.status_code, 404)

    async def test_hls_segment_is_generated_for_one_requested_time_slice(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE asset SET location='115', duration=13.5, path=? WHERE id=1",
                    (str(self.hls_file),))
        con.commit()
        con.close()
        segment = self.root / "segment.ts"
        segment.write_bytes(b"one segment")

        async def generate(source, start, duration, *, asset_id, index, session, registry):
            # 关键帧每秒一个，6 秒目标 → 第 1 段从 6.0 开始，长 6.0。
            self.assertEqual((start, duration), (6.0, 6.0))
            self.assertEqual((asset_id, index, session), (1, 1, "hls-test"))
            return segment

        with patch.object(self.app.state.hls_service, "generate", new=generate):
            with patch.object(
                self.app.state.hls_plan_executor,
                "submit",
                wraps=self.app.state.hls_plan_executor.submit,
            ) as submit:
                response = await self.client.get(
                    "/stream/hls/1/1.ts?session=hls-test&t=secret",
                )
        submit.assert_called_once()
        self.assertEqual(self.app.state.hls_plan_executor._max_workers, 2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"one segment")
        self.assertEqual(response.headers["content-type"], "video/mp2t")
        self.assertTrue(segment.exists(), "片段要留在缓存里，重复请求不该再跑一次 FFmpeg")

    async def test_a_repaired_header_takes_a_broken_timestamp_file_off_the_transcode_path(self):
        """时间戳错乱的片子一旦有了修好的头，就该按 Range 无损播，不再实时转码。"""
        from peach.mp4repair import RepairedHeader

        source = self.media_root / "decode-order.mp4"
        source.write_bytes(b"OLDHEAD" + bytes(range(64)))
        header = RepairedHeader(prefix=b"NEWHEADER", payload_start=7, payload_end=71)
        con = sqlite3.connect(self.db)
        con.execute("UPDATE asset SET path=?, duration=13.5 WHERE id=1", (str(source),))
        con.commit()
        con.close()
        service = self.app.state.transcode_service

        with patch.object(service, "requires_conversion", return_value=True), \
                patch.object(service, "decode_order_timestamps", return_value=True), \
                patch.object(service, "browser_path",
                             side_effect=AssertionError("whole movie conversion")), \
                patch.object(self.app.state.header_repairs, "lookup", return_value=header), \
                patch.object(self.app.state.header_repairs, "request",
                             side_effect=AssertionError("已经有头了还要再算一遍")):
            plan = (await self.client.get("/api/stream-plan?id=1&session=s&t=secret")).json()
            response = await self.client.get(
                "/stream?id=1", headers={"X-Token": "secret", "Range": "bytes=5-12"})

        self.assertEqual(plan["protocol"], "range")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, (b"NEWHEADER" + bytes(range(64)))[5:13])
        self.assertEqual(response.headers["content-type"], "video/mp4")

    async def test_a_broken_timestamp_file_without_a_header_still_slices_and_asks_for_one(self):
        """头还没算出来时照旧走 HLS，同时在后台补一份，下次播就换成 Range。"""
        con = sqlite3.connect(self.db)
        con.execute("UPDATE asset SET path=?, duration=13.5 WHERE id=1", (str(self.hls_file),))
        con.commit()
        con.close()
        service = self.app.state.transcode_service
        asked: list[int] = []

        with patch.object(service, "requires_conversion", return_value=True), \
                patch.object(service, "decode_order_timestamps", return_value=True), \
                patch.object(self.app.state.header_repairs, "lookup", return_value=None), \
                patch.object(self.app.state.header_repairs, "request",
                             new=lambda asset_id, source: asked.append(asset_id)):
            plan = (await self.client.get("/api/stream-plan?id=1&session=s&t=secret")).json()

        self.assertEqual(plan["protocol"], "hls")
        self.assertEqual(asked, [1])

    async def test_transcoded_stream_has_browser_mime_and_marker(self):
        avi = self.media_root / "two.avi"
        avi.write_bytes(b"avi-source")
        cached = self.transcode_root / "two.mp4"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"mp4-cache")
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT INTO asset(id,location,path,name,medium,size,first_seen) "
            "VALUES(2,'local',?,'two.avi','video',10,'2026-08-15')",
            (str(avi),),
        )
        con.commit()
        con.close()

        with patch.object(
            self.app.state.transcode_service, "browser_path",
            return_value=(cached, True),
        ):
            response = await self.client.get(
                "/stream?id=2", headers={"X-Token": "secret", "Range": "bytes=0-2"},
            )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"mp4")
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertEqual(response.headers["x-peach-transcoded"], "1")

    async def test_cached_visual_assets_and_thumbnail(self):
        headers = {"X-Token": "secret"}
        poster = await self.client.get("/poster?id=1&c=4", headers=headers)
        avatar = await self.client.get("/avatar?id=1", headers=headers)
        logo = await self.client.get("/logo?studio=Studio_A", headers=headers)
        thumb = await self.client.get("/thumb?id=1", headers=headers)
        self.assertEqual((poster.content, avatar.content, logo.content, thumb.content),
                         (b"poster", b"avatar", b"logo", b"snapshot"))
        self.assertEqual(logo.headers["content-type"], "image/png")
        self.assertEqual(logo.headers["cache-control"], "public, no-cache")
        self.assertEqual(poster.headers["cache-control"],
                         "private, no-cache")
        fresh = await self.client.get("/poster?id=1&c=4", headers={
            **headers, "If-None-Match": poster.headers["etag"],
        })
        self.assertEqual(fresh.status_code, 304)
        self.assertEqual(fresh.content, b"")
        denied = await self.client.get("/poster?id=1&c=4", headers={
            "If-None-Match": poster.headers["etag"],
        })
        self.assertEqual(denied.status_code, 401)

    async def test_endcard_frame_is_authenticated_and_confined_to_evidence_root(self):
        denied = await self.client.get(
            "/endcard-frame?id=1&name=tail-000098000.png"
        )
        self.assertEqual(denied.status_code, 401)
        response = await self.client.get(
            "/endcard-frame?id=1&name=tail-000098000.png",
            headers={"X-Token": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"endcard")
        self.assertEqual(response.headers["content-type"], "image/png")
        traversal = await self.client.get(
            "/endcard-frame?id=1&name=../index.html",
            headers={"X-Token": "secret"},
        )
        self.assertEqual(traversal.status_code, 400)

    def _seed_photo(self):
        """一张真实 JPEG：缩略图端点要真的解码，假字节测不出这条路径。"""
        from PIL import Image

        source = self.media_root / "shoot" / "001.jpg"
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1600, 2400), (200, 120, 90)).save(source, "JPEG")
        source2 = self.media_root / "shoot-2" / "002.jpg"
        source2.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2400, 1600), (90, 120, 200)).save(source2, "JPEG")
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT INTO asset(id,location,path,name,medium,size,first_seen) "
            "VALUES(9,'pikpak',?,'001.jpg','image',?,'2026-08-24')",
            (str(source), source.stat().st_size),
        )
        con.execute(
            "INSERT INTO asset(id,location,path,name,medium,size,first_seen) "
            "VALUES(10,'pikpak',?,'002.jpg','image',?,'2026-08-24')",
            (str(source2), source2.stat().st_size),
        )
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name) "
            "VALUES(9,'creator','Alice','alice')")
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(9,9,'creator','legacy:asset',1.0)")
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(10,9,'creator','legacy:asset',1.0)")
        con.commit()
        con.close()
        return source

    async def test_photo_sets_expose_directories_without_leaking_paths(self):
        self._seed_photo()
        headers = {"X-Token": "secret"}
        listing = await self.client.get(
            "/api/photos?kind=creator&name=Alice&limit=1", headers=headers)
        self.assertEqual(listing.status_code, 200)
        payload = listing.json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["sets"][0]["id"], 9)
        self.assertEqual(payload["sets"][0]["title"], "shoot")
        self.assertEqual([item["id"] for item in payload["items"]], [9])
        self.assertEqual(payload["items"][0]["location"], "pikpak")
        self.assertTrue(payload["has_more"])
        self.assertNotIn(str(self.media_root), json.dumps(payload),
                         "只发目录名和图集 id，不发真实路径")
        next_page = await self.client.get(
            "/api/photos?kind=creator&name=Alice&limit=1&offset=1", headers=headers)
        self.assertEqual([item["id"] for item in next_page.json()["items"]], [10])
        self.assertFalse(next_page.json()["has_more"])
        detail = await self.client.get("/api/photo-set?id=9", headers=headers)
        self.assertEqual([item["id"] for item in detail.json()["items"]], [9])
        self.assertEqual(detail.json()["items"][0]["location"], "pikpak")
        self.assertNotIn(str(self.media_root), json.dumps(detail.json()),
                         "图片详情也只发安全元数据，真实路径留在服务端")

    async def test_photo_detail_reveal_resolves_the_asset_id_on_the_server(self):
        source = self._seed_photo()
        headers = {"X-Token": "secret"}
        with patch("peach.routes_api.reveal_path", return_value=True) as reveal:
            response = await self.client.post(
                "/api/reveal", headers=headers,
                json={"id": 9, "path": "C:/client-must-not-control-this"},
            )
        self.assertEqual(response.status_code, 200)
        reveal.assert_called_once_with(source)

    async def test_reveal_by_path_stays_inside_peach_own_data_root(self):
        """页面上印着全路径的那几处（凭据文件、问题日志、要删的数据目录）按 `path` 定位。

        递回来的那一串只是候选：服务端拿同一份设置把 Peach 数据目录重算一遍再比对，所以往上爬
        出去的、Peach 数据目录外面的一律不开。媒体文件不走这条——它的路径压根没发给过前端。
        """
        from peach import settings_file

        data_root = self.root / "peach-data"
        log = data_root / "state" / "library-processing-one.issues.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text("", encoding="utf-8")
        outside = self.root / "elsewhere.txt"
        outside.write_text("", encoding="utf-8")
        config = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(data_root)})
        headers = {"X-Token": "secret"}
        with patch("peach.settings_file.active", return_value=config), \
                patch("peach.routes_api.reveal_path", return_value=True) as reveal:
            opened = await self.client.post("/api/reveal", headers=headers, json={"path": str(log)})
            refused = await self.client.post("/api/reveal", headers=headers, json={"path": str(outside)})
            climbing = await self.client.post(
                "/api/reveal", headers=headers,
                json={"path": str(log.parent / ".." / ".." / "elsewhere.txt")})
            gone = await self.client.post(
                "/api/reveal", headers=headers, json={"path": str(log.parent / "gone.jsonl")})
        self.assertEqual(opened.status_code, 200)
        reveal.assert_called_once_with(log.resolve())
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(climbing.status_code, 403, "`..` 爬出 Peach 数据目录的一样不开")
        self.assertEqual(gone.status_code, 410)
        # 410 与 403 在页面那层的通用错误映射里都没有说法，原因得由这条自己写成中文。
        self.assertEqual(gone.json()["message"], "这个位置已经不在了")
        self.assertIn("不归 Peach 管", refused.json()["message"])

    async def test_photo_thumbnail_is_generated_once_and_cached(self):
        source = self._seed_photo()
        headers = {"X-Token": "secret"}
        first = await self.client.get("/photo-thumb?id=9", headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["content-type"], "image/jpeg")
        cached = self.photo_root / "9.jpg"
        self.assertTrue(cached.is_file(), "缩略图要落盘，计费来源不能每次回源")
        self.assertLess(cached.stat().st_size, source.stat().st_size)
        stamp = cached.stat().st_mtime_ns
        again = await self.client.get("/photo-thumb?id=9", headers=headers)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(cached.stat().st_mtime_ns, stamp, "第二次只读缓存")
        full = await self.client.get("/photo?id=9", headers=headers)
        self.assertEqual(full.content, source.read_bytes())

    async def test_the_card_grade_cover_is_a_smaller_webp_beside_the_cover_directory(self):
        """卡片网格取 `/cover?thumb=1`，详情与裁切取原件。

        派生件不进封面目录：`cover_index` 逐次扫它判「有没有封面」，混进派生件就多判一倍。
        """
        from PIL import Image

        self.cover_root.mkdir(parents=True, exist_ok=True)
        source = self.cover_root / "ABC-123.jpg"
        image = Image.new("RGB", (2400, 1620), (200, 120, 90))
        # 纯色图压出来只有几百字节，比不出「缩了没有」。噪声让体积跟着像素量走。
        for y in range(0, 1620, 4):
            for x in range(0, 2400, 4):
                image.putpixel((x, y), ((x * 7) % 256, (y * 13) % 256, (x + y) % 256))
        image.save(source, "JPEG", quality=95)
        headers = {"X-Token": "secret"}
        card = await self.client.get("/cover?code=ABC-123&thumb=1", headers=headers)
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.headers["content-type"], "image/webp")
        self.assertLess(len(card.content), source.stat().st_size)
        with Image.open(io.BytesIO(card.content)) as derived:
            self.assertEqual(derived.size, (1600, 1080))
        self.assertEqual(sorted(path.name for path in self.cover_root.iterdir()), ["ABC-123.jpg"])
        self.assertTrue(any((self.cover_root.parent / "cover-thumbs").iterdir()))
        full = await self.client.get("/cover?code=ABC-123", headers=headers)
        self.assertEqual(full.content, source.read_bytes())

    async def test_a_warm_photo_thumbnail_answers_without_resolving_the_original(self):
        """缩略图已经躺在本地时，这条路径不再去解析原图。

        解析要在挂载的网盘上确认原文件还在，实测一次 137–402 毫秒；一屏几十张缩略图
        全部命中缓存，也要为这几十次往返一张张等下去，人看到的是图片墙加载半天。缩略
        图缩好之后，原图还在不在都不改变这次的响应。

        判据用「把原图删掉」而不是数调用次数：先问缓存的答得出来，先解析原图的答不
        出来，顺序换回去这条就红。原图那条路仍然照常认它不在了——省掉的只是缩略图这
        一条上的那次确认。
        """
        source = self._seed_photo()
        headers = {"X-Token": "secret"}
        warm = await self.client.get("/photo-thumb?id=9", headers=headers)
        self.assertEqual(warm.status_code, 200)
        source.unlink()
        again = await self.client.get("/photo-thumb?id=9", headers=headers)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.content, warm.content)
        full = await self.client.get("/photo?id=9", headers=headers)
        self.assertNotEqual(full.status_code, 200)

    async def test_photo_endpoints_require_the_token(self):
        self._seed_photo()
        for path in ("/photo?id=9", "/photo-thumb?id=9", "/sample-thumb?code=SSIS-057&n=1",
                     "/sample-image?code=SSIS-057&n=1"):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 401, path)

    def _seed_samples(self, count: int = 3) -> None:
        """Alice 名下一部有码片和它的官方样张。夹具的表结构不含样张表，这里按迁移原文建。"""
        self._seed_photo()
        migration = Path(__file__).resolve().parents[1] / "migrations" / "0037_code_sample_image.sql"
        with closing(sqlite3.connect(self.db)) as con:
            con.executescript(migration.read_text(encoding="utf-8"))
            con.execute("INSERT INTO asset(id,location,path,name,medium,code,catalog_title,release_date)"
                        " VALUES(11,'local',?,'SSIS-057.mp4','video','SSIS-057','雨の日','2021-05-18')",
                        (str(self.media_root / "SSIS-057.mp4"),))
            con.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
                        " VALUES(11,9,'creator','legacy:asset',1.0)")
            con.executemany(
                "INSERT INTO code_sample_image(code,position,url,site,source,fetched_at)"
                " VALUES('SSIS-057',?,?,'dmm','auto:sample-images@7','2026-09-25T00:00:00+00:00')",
                [(n, f"https://pics.dmm.co.jp/digital/video/ssis00057/ssis00057jp-{n}.jpg")
                 for n in range(1, count + 1)])
            con.commit()

    async def test_photos_list_one_code_set_per_work_that_has_samples(self):
        self._seed_samples()
        payload = (await self.client.get(
            "/api/photos?kind=creator&name=Alice", headers={"X-Token": "secret"})).json()
        code_sets = [item for item in payload["sets"] if item["kind"] == "code"]
        self.assertEqual(len(code_sets), 1)
        self.assertEqual(code_sets[0]["id"], "code:SSIS-057")
        self.assertEqual(code_sets[0]["n"], 3)
        self.assertEqual(code_sets[0]["title"], "SSIS-057 雨の日")
        self.assertEqual(code_sets[0]["site"], "dmm")
        self.assertEqual(payload["sample_total"], 3)
        self.assertEqual(payload["total"], 2, "`total` 只数本地图片")
        self.assertEqual([item["kind"] for item in payload["sets"]], ["code", "dir", "dir"])
        self.assertNotIn("pics.dmm.co.jp", json.dumps(payload), "样张地址留在服务端")

    async def test_item_samples_are_ordered_local_positions_without_source_requests(self):
        self._seed_samples(40)
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("DELETE FROM code_sample_image WHERE position IN (2,4)")
            connection.execute("UPDATE asset SET code='ssis-057' WHERE id=11")
            connection.commit()
        with patch("peach.sample_images.downloader") as download, patch("peach.javdb_public.PUBLIC_API.movie") as movie:
            response = await self.client.get("/api/item?id=11", headers={"X-Token": "secret"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        expected = [{"n": n} for n in range(1, 33) if n not in (2, 4)]
        self.assertEqual(payload["sample_images"], expected)
        self.assertNotIn("pics.dmm.co.jp", response.text)
        download.assert_not_called()
        movie.assert_not_called()

    async def test_item_without_sample_table_has_an_empty_sample_list(self):
        payload = (await self.client.get("/api/item?id=1", headers={"X-Token": "secret"})).json()
        self.assertEqual(payload["sample_images"], [])

    async def test_item_without_matching_samples_has_an_empty_sample_list(self):
        self._seed_samples()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE asset SET code='UNKNOWN-001' WHERE id=11")
            connection.commit()
        payload = (await self.client.get("/api/item?id=11", headers={"X-Token": "secret"})).json()
        self.assertEqual(payload["sample_images"], [])

    async def test_a_sample_is_downloaded_on_first_view_and_then_served_from_cache(self):
        from PIL import Image

        self._seed_samples()
        picture = io.BytesIO()
        Image.new("RGB", (800, 534), (120, 80, 60)).save(picture, "JPEG")
        asked = []

        def factory(_secrets_root):
            def download(url):
                asked.append(url)
                return picture.getvalue()
            return download

        headers = {"X-Token": "secret"}
        with patch("peach.sample_images.downloader", side_effect=factory):
            first = await self.client.get("/sample-thumb?code=SSIS-057&n=2", headers=headers)
            again = await self.client.get("/sample-thumb?code=ssis-057&n=2", headers=headers)
            full = await self.client.get("/sample-image?code=SSIS-057&n=2", headers=headers)
            unknown = await self.client.get("/sample-thumb?code=SSIS-057&n=9", headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(again.content, first.content)
        self.assertEqual(full.content, picture.getvalue())
        self.assertEqual(asked, ["https://pics.dmm.co.jp/digital/video/ssis00057/ssis00057jp-2.jpg"],
                         "同一张只下载一次，数据库里没有的序号不出网")
        self.assertEqual(unknown.status_code, 404)
        folder = self.root / "sample-cache" / "SSIS-057"
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ["2.jpg", "2.thumb.jpg"])
        with Image.open(folder / "2.thumb.jpg") as thumb:
            self.assertEqual(thumb.width, 640)

    async def test_a_failed_sample_download_answers_404_and_is_not_retried_at_once(self):
        self._seed_samples()
        asked = []

        def factory(_secrets_root):
            def download(url):
                asked.append(url)
                raise OSError("来源连接未取得")
            return download

        headers = {"X-Token": "secret"}
        with patch("peach.sample_images.downloader", side_effect=factory):
            first = await self.client.get("/sample-thumb?code=SSIS-057&n=1", headers=headers)
            again = await self.client.get("/sample-thumb?code=SSIS-057&n=1", headers=headers)
        self.assertEqual((first.status_code, again.status_code), (404, 404))
        self.assertEqual(len(asked), 1, "失败标记一天内挡住重试")
        self.assertTrue((self.root / "sample-cache" / "SSIS-057" / "1.miss").is_file())


@unittest.skipUnless(HAS_DEPS, "fastapi/httpx not installed")
class UnconfiguredMachineTests(unittest.IsolatedAsyncioTestCase):
    """还没跑过 `peach init` 的机器：服务照常起，页面告诉人下一步做什么。

    「未配置」是显式状态，不是崩溃：数据目录不存在、数据库不存在、什么来源都没挂，
    这些都不该让 `serve` 起不来——起不来的服务连提示都没法给。
    """

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        missing = Path(self.tmp.name).resolve() / "nothing-here"
        self.settings = PeachSettings(
            configured=False,
            db_path=missing / "database" / "ledger.db",
            page_path=missing / "web" / "index.html",
            vendor_path=missing / "web" / "vendor",
            allowed_media_roots=(),
            snapshot_root=missing / "snapshots",
            poster_root=missing / "posters", avatar_root=missing / "avatars",
            logo_root=missing / "logos", cover_root=missing / "covers",
            photo_root=missing / "photos", stream_root=missing / "stream",
            ffmpeg_root=missing / "ffmpeg", transcode_root=missing / "transcodes",
            candidate_root=missing, review_mirror_cache=missing / "review.json",
            taste_history_store=missing / "history.sqlite",
            taste_history_import_root=missing / "imports",
            taste_history_output_root=missing / "taste",
            taste_history_manifest=missing / "manifest.json",
            follow_state_root=missing / "state",
        )
        self.app = create_app(self.settings)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_health_reports_the_unconfigured_state_instead_of_failing(self):
        response = await self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["configured"])
        self.assertEqual(body["db"], "missing")

    async def test_setup_lifespan_keeps_ledger_workers_stopped(self):
        async with self.app.router.lifespan_context(self.app):
            self.assertIsNone(self.app.state.web_contract.downloads._thread)
            self.assertFalse(self.app.state.follow_scheduler._started)
            self.assertFalse(self.app.state.web_contract.feed_scheduler._started)
            response = await self.client.get("/healthz")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["configured"])
            self.assertFalse(self.settings.db_path.exists())

    @asynccontextmanager
    async def ledger_client(self, database):
        from peach import settings_file
        config = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(database.parent.parent)})
        config = replace(config, locations={}, mounts={})
        settings = replace(self.settings, configured=True, db_path=database,
                           page_path=ROOT / "web/index.html", secrets_root=database.parent / "secrets",
                           tunnel_state_root=database.parent / "tunnel-state",
                           tunnel_log_root=database.parent / "logs")
        with patch("peach.settings_file.load_config", return_value=config), \
                patch("peach.routes_configuration.managed_configuration", return_value=False), \
                self.assertNoLogs("peach", level="ERROR"):
            app = create_app(settings)
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                yield app, client

    async def test_unready_ledgers_keep_background_workers_stopped_without_creating_or_migrating_data(self):
        parent = self.settings.db_path.parent
        parent.mkdir(parents=True)
        for state in ("missing", "empty", "corrupt", "pending", "mismatch"):
            with self.subTest(state=state):
                database = parent / f"{state}.db"
                if state == "empty":
                    with closing(sqlite3.connect(database)):
                        pass
                elif state == "corrupt":
                    database.write_bytes(b"not a sqlite database")
                elif state in {"pending", "mismatch"}:
                    fresh_ledger(parent, database.name)
                    with closing(sqlite3.connect(database)) as connection, connection:
                        if state == "pending":
                            connection.execute("DELETE FROM schema_migration WHERE version=(SELECT MAX(version) FROM schema_migration)")
                        else:
                            connection.execute("UPDATE schema_migration SET checksum='wrong' WHERE version=(SELECT MAX(version) FROM schema_migration)")
                original = database.read_bytes() if database.exists() else None
                with self.assertLogs("peach.api", level="WARNING") as captured:
                    async with self.ledger_client(database) as (app, client):
                        self.assertIsNone(app.state.web_contract.downloads._thread)
                        self.assertFalse(app.state.follow_scheduler._started)
                        self.assertFalse(app.state.web_contract.feed_scheduler._started)
                        self.assertEqual((await client.get("/healthz")).status_code, 200)
                        ready = await client.get("/healthz?ready=1")
                        self.assertEqual(ready.status_code, 503)
                        self.assertFalse(ready.json()["ready"])
                self.assertEqual([record.levelname for record in captured.records], ["WARNING"])
                self.assertIn("数据库准备完成后重启服务", captured.output[0])
                self.assertNotIn(str(database), captured.output[0])
                self.assertEqual(database.read_bytes() if database.exists() else None, original)

    async def test_current_ledger_starts_background_workers_and_stops_them_with_the_service(self):
        parent = self.settings.db_path.parent
        parent.mkdir(parents=True)
        database = fresh_ledger(parent)
        async with self.ledger_client(database) as (app, client):
            self.assertIsNotNone(app.state.web_contract.downloads._thread)
            self.assertTrue(app.state.follow_scheduler._started)
            self.assertTrue(app.state.web_contract.feed_scheduler._started)
            ready = await client.get("/healthz?ready=1")
            self.assertEqual(ready.status_code, 200)
            self.assertTrue(ready.json()["ready"])
        self.assertIsNone(app.state.web_contract.downloads._thread)
        self.assertFalse(app.state.follow_scheduler._started)
        self.assertFalse(app.state.web_contract.feed_scheduler._started)

    async def test_service_startup_replays_an_interrupted_library_request_with_its_scope(self):
        from peach import job_recovery, settings_file
        parent = self.settings.db_path.parent
        parent.mkdir(parents=True)
        database = fresh_ledger(parent)
        job_recovery.save(database, 'library-processing', 'interrupted', {'stage': 'previews'})
        path = parent.parent / 'state/job-recovery/library-processing.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record['pid'] = -1
        path.write_text(json.dumps(record), encoding='utf-8')
        media = parent.parent / 'media'
        media.mkdir()
        config = replace(settings_file.load_config(environ={'PEACH_DATA_ROOT': str(parent.parent)}),
                         present=True, locations={'local': (str(media),)})
        invoked = threading.Event()
        def process(*_args, **kwargs):
            self.assertEqual(kwargs['stage'], 'previews')
            invoked.set()
            return {'status': 'complete'}
        with patch('peach.settings_file.active', return_value=config), patch(
                'peach.web_library_processing.process_library', side_effect=process):
            async with self.ledger_client(database) as (app, _client):
                self.assertTrue(await asyncio.to_thread(invoked.wait, 5))
                thread = app.state.web_contract.library_processing_job.thread
                await asyncio.to_thread(thread.join, 5)
                self.assertEqual(job_recovery.read(database, 'library-processing')['status'], 'complete')

    async def test_cancelled_installation_resumes_the_paused_task_in_the_existing_service(self):
        from peach import settings_file, update_tasks
        parent = self.settings.db_path.parent
        parent.mkdir(parents=True)
        database = fresh_ledger(parent)
        media = parent.parent / 'media'
        media.mkdir()
        config = replace(settings_file.load_config(environ={'PEACH_DATA_ROOT': str(parent.parent)}),
                         present=True, locations={'local': (str(media),)})
        entered, checkpoint, completed = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def process(*_args, **kwargs):
            calls.append(kwargs['stage'])
            entered.set()
            checkpoint.wait(5)
            kwargs['report']({'stage': '生成预览图'})
            completed.set()
            return {'status': 'complete'}
        with patch('peach.settings_file.active', return_value=config), patch(
                'peach.web_library_processing.process_library', side_effect=process):
            async with self.ledger_client(database) as (app, _client):
                from peach.web_library_processing import w_library_processing
                job = app.state.web_contract.library_processing_job
                w_library_processing(app.state.web_contract, {'stage': 'previews'})
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                run_id = job.snapshot()['run_id']
                token = update_tasks.reserve(database)
                try:
                    checkpoint.set()
                    await asyncio.to_thread(job.thread.join, 5)
                    self.assertTrue(job.snapshot()['waiting_for_update'])
                    self.assertEqual(app.state.web_contract.task_runs.get(run_id).status, 'pending')
                finally:
                    update_tasks.release(database, token)
                    checkpoint.set()
                self.assertTrue(await asyncio.to_thread(completed.wait, 5))
                await asyncio.to_thread(job.thread.join, 5)
                self.assertEqual(calls, ['previews', 'previews'])
                self.assertEqual(job.snapshot()['run_id'], run_id)

    async def test_preparing_the_ledger_requires_a_service_restart_to_resume_background_work(self):
        parent = self.settings.db_path.parent
        parent.mkdir(parents=True)
        database = parent / "ledger.db"
        async with self.ledger_client(database) as (app, client):
            self.assertIsNone(app.state.web_contract.downloads._thread)
            fresh_ledger(parent)
            response = await client.get("/healthz?ready=1")
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json()["checks"]["background"])
        async with self.ledger_client(database) as (app, client):
            response = await client.get("/healthz?ready=1")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["checks"]["background"])
            self.assertIsNotNone(app.state.web_contract.downloads._thread)

    async def test_shutdown_joins_the_active_aggregate_before_releasing_the_ledger(self):
        parent = self.settings.db_path.parent
        parent.mkdir(parents=True)
        database = fresh_ledger(parent)
        opened, release, closed = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def query(contract, path, args):
            calls.append(path)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("SELECT id FROM asset LIMIT 1").fetchone()
                opened.set()
                release.wait(5)
            closed.set()

        context = self.ledger_client(database)
        shutdown = None
        with patch("peach.api.web_contract.dispatch_api_get", side_effect=query):
            try:
                app, _client = await context.__aenter__()
                self.assertTrue(await asyncio.to_thread(opened.wait, 5))
                shutdown = asyncio.create_task(context.__aexit__(None, None, None))
                while app.state.ledger_background_ready and not shutdown.done():
                    await asyncio.sleep(0)
                self.assertFalse(shutdown.done(), "服务退出须等待预热查询释放数据库")
                release.set()
                await shutdown
                self.assertTrue(closed.is_set())
                self.assertEqual(calls, ["/api/taste"])
            finally:
                release.set()
                if shutdown is not None:
                    await shutdown
                else:
                    await context.__aexit__(None, None, None)
                await asyncio.to_thread(closed.wait, 5)

    async def test_the_page_serves_the_first_run_shell(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn('<div id="peach-page" data-page="setup">', response.text)
        self.assertIn('src="/dist/peach-pages.js"', response.text)

    async def test_deep_links_land_on_the_same_shell(self):
        # 前端路由全部落到 `index`，未配置时不该只有首页能看。
        response = await self.client.get("/tags")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-page="setup"', response.text)

    async def test_a_configured_machine_still_reports_true(self):
        app = create_app(PeachSettings(configured=True, db_path=self.settings.db_path))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            self.assertTrue((await client.get("/healthz")).json()["configured"])


#: 不挂鉴权依赖的路由，以及每一条为什么必须公开。每一项都逐条核实过，新加的路由
#: 默认要带鉴权：漏挂时在这里红，而不是等有人从局域网外面发现它。
#: 键是 `(方法集合, 路径)`，方法也要对上——同一条路径的 GET 公开不等于 POST 也公开。
PUBLIC_ROUTES = {
    # 健康检查。托盘与 uptime 工具要在没有口令时探到服务活着，返回里只有本机
    # 运行态，没有任何媒体内容或馆藏数据。
    ("GET,HEAD", "/healthz"),
    # 登录页自己。要口令才能打开输入口令的那一页，就没有人能登录了。
    ("GET", "/login"),
    ("POST", "/login"),
    # 首次运行页的读题与提交。这台机器那时还没有口令可验；两条共用
    # `routes_pages._setup_guard`：已配置过就 404、非回环调用方 403、独立包的非回环
    # 主机名 403；提交再看 Origin（403）与设置文件已存在（409）。配置完成后读题也是 404，
    # 平台、打包形态与挂载软件这些运行事实不外露。
    ("GET", "/api/setup/questions"),
    ("POST", "/api/setup"),
    ("POST", "/api/setup/restore"),
    # 首启与登录页的浏览器尚无会话；此接口仅向本机同源开放，限流、限长后只写脱敏错误日志。
    ("POST", "/api/diagnostics/client-error"),
    # 登录页在拿到会话之前就要出图，书签与历史记录也从这三个固定路径取图标。
    # 它们发的是随仓库分发的品牌资源，不读账本。
    ("GET,HEAD", "/favicon.ico"),
    ("GET,HEAD", "/favicon.svg"),
    ("GET,HEAD", "/peach-logo.png"),
    # 入口页的页面包（ADR-0094）。登录页在拿到会话之前就要画出来，首启、登录与错误三页都由它画。
    # 只放这两个文件：它们提交进 Git、随仓库分发，不读账本、不含配置与凭据，内容不随登录与否
    # 变化，也不 import 别的产物；其余 `/dist/*` 仍走要会话的 `/dist/{name}`。
    ("GET,HEAD", "/dist/peach-pages.js"),
    ("GET,HEAD", "/dist/peach-pages.css"),
    # 用户脚本安装产物只含随仓库分发的代码与站点规则，不含配对令牌、网盘账号或账本内容。
    ("GET", "/integrations/peach-jav.user.js"),
    # 拒绝收录的声明。爬虫没有会话，被 401 挡住等于这份声明根本没被读到。
    ("GET,HEAD", "/robots.txt"),
    # CloudDrive2 的文件通知。推送方是另一个进程，没有也不该有 Peach 的会话，所以它带的
    # 是独立共享密钥（ADR-0041）。这一条因此不挂 require_*，但自己有三道门：通道没开、
    # 来源不在回环与私网、密钥对不上，任意一条不成立都回 404，见 `test_push_discovery`。
    ("POST", "/api/inbox/clouddrive"),
    # 外部播放器没有浏览器会话；两条路由各自验证进程密钥签发的限时 HMAC，
    # 绑定单个资产与公开 Origin，验证后仍逐次检查媒体根目录。
    ("GET,HEAD", "/external-stream/{id}"),
    ("GET", "/external-playlist/{id}.m3u"),
    # 关注外部播放使用进程密钥签发的限时 HMAC，绑定条目和媒体序号，不接受上游地址。
    ("GET,HEAD", "/external-follow-stream/{id}"),
    # STRM 的 HLS 子资源供浏览器和外部播放器共用：处理函数验证浏览器会话，
    # 或绑定资产与 Origin 的限时 HMAC；两条分支都检查媒体根目录及资源归属。
    ("GET,HEAD", "/strm-resource/{id}/{key}"),
}


def _api_routes(routes):
    """铺平 FastAPI 的路由表。

    FastAPI 0.141 起 `include_router` 往 `app.routes` 里放的是一个包装对象，
    真正的路由在它的 `original_router` 上；只遍历 `app.routes` 会看见一条
    `/healthz` 就以为全站只有一个端点。
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _api_routes(route.original_router.routes)
        elif hasattr(route, "routes"):
            yield from _api_routes(route.routes)


@unittest.skipUnless(HAS_DEPS, "fastapi/httpx not installed")
class RouteAuthContractTests(unittest.TestCase):
    """每条路由都要挂上鉴权依赖，例外只能出自上面那张逐条核实过的白名单。

    Peach 的闸门是依赖注入（`routes_auth` 的三个 `require_*`），不是中间件：新加
    一条路由时忘记写 `Depends(require_auth)`，服务照常起、测试照常绿，那条路径就
    此对局域网敞开。这是「只改了自己测试的那条路径」在鉴权面上的形态。
    """

    GUARD_NAMES = ("require_auth", "require_page_auth", "require_asset_auth")

    def test_single_file_import_requires_local_same_origin_and_a_writable_ledger(self):
        from fastapi import HTTPException
        from starlette.requests import Request
        from peach.routes_api import api_post
        def request(peer='127.0.0.1', headers=(), sync=None):
            return Request({'type': 'http', 'method': 'POST', 'scheme': 'http',
                'path': '/api/library-processing/import-file', 'query_string': b'',
                'client': (peer, 12345), 'server': ('127.0.0.1', 8900),
                'headers': [(b'host', b'127.0.0.1:8900'), *headers],
                'app': SimpleNamespace(state=SimpleNamespace(sync=sync, web_contract=object()))})
        with patch('peach.routes_api.web_contract.dispatch_api_post', return_value={'status': 'running'}) as dispatch:
            for req in (request(peer='192.0.2.20'), request(headers=[(b'x-forwarded-for', b'127.0.0.1')]),
                        request(headers=[(b'origin', b'https://example.invalid')])):
                with self.subTest(request=req), self.assertRaises(HTTPException) as error:
                    api_post(req, 'library-processing/import-file', {'path': r'E:\Media\one.mp4'})
                self.assertEqual(error.exception.status_code, 403)
            dispatch.assert_not_called()
            sync = SimpleNamespace(read_only=True, detail='reader', read_only_message='只读')
            self.assertEqual(api_post(request(sync=sync), 'library-processing/import-file', {}).status_code, 409)
            dispatch.assert_not_called()
            self.assertEqual(api_post(request(), 'library-processing/import-file', {'path': r'E:\Media\one.mp4'}),
                             {'status': 'running'})

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name).resolve()
        cls.app = create_app(PeachSettings(
            configured=True, db_path=root / "ledger.db",
            follow_state_root=root / "state"))
        cls.guards = {getattr(routes_auth, name) for name in cls.GUARD_NAMES}
        cls.special_guards = {
            ("GET", "/api/jav-browser"): routes_jav_browser.browser_client,
            ("POST", "/api/jav-browser"): routes_jav_browser.browser_client,
        }
        cls.routes = {(",".join(sorted(route.methods)), route.path):
                      {dependency.call for dependency in route.dependant.dependencies}
                      for route in _api_routes(cls.app.routes)}

    @classmethod
    def tearDownClass(cls):
        cls.app.state.http_transport.close()
        cls.tmp.cleanup()

    def test_the_route_table_is_actually_visible_to_this_test(self):
        """铺平失败时这个契约会静默变成空断言，所以先钉住「看得见路由」。"""
        self.assertGreater(len(self.routes), 60, "没有遍历到路由表，下面的断言全是空的")
        self.assertIn(("GET,HEAD", "/healthz"), self.routes)

    def test_every_route_is_gated_unless_it_is_on_the_public_list(self):
        unguarded = sorted(key for key, dependencies in self.routes.items()
                           if not dependencies & self.guards and key not in PUBLIC_ROUTES
                           and self.special_guards.get(key) not in dependencies)
        self.assertEqual(unguarded, [],
                         "新路由没挂 routes_auth 的 require_* 依赖；确实该公开就写进 "
                         "PUBLIC_ROUTES 并注明理由")

    def test_browser_addition_requires_its_scoped_bearer_dependency(self):
        for key, guard in self.special_guards.items():
            with self.subTest(route=key):
                self.assertIn(guard, self.routes[key])

    def test_the_public_list_has_no_entry_that_stopped_existing(self):
        """路径改名或删掉时白名单要跟着收，否则它会一直替一条不存在的路由背书。"""
        stale = sorted(key for key in PUBLIC_ROUTES if key not in self.routes)
        self.assertEqual(stale, [])
        still_gated = sorted(key for key in PUBLIC_ROUTES
                             if self.routes.get(key, set()) & self.guards)
        self.assertEqual(still_gated, [], "这些路由已经挂上鉴权了，从白名单里删掉")

    def test_the_only_unguarded_mount_is_the_vendored_frontend(self):
        """挂载不是路由，遍历看不到它；再挂一个就必须有人重新判断它该不该公开。"""
        self.assertEqual(
            sorted(route.path for route in self.app.routes if isinstance(route, Mount)),
            ["/vendor"])


@unittest.skipUnless(HAS_DEPS, "fastapi/httpx not installed")
class LoginPageWithoutSessionTests(unittest.IsolatedAsyncioTestCase):
    """设了访问密码的实例上，没有会话也画得出登录页：它要的资源都能取，别的产物照旧要会话。"""

    PAGE_ASSETS = ("/dist/peach-pages.css", "/dist/peach-pages.js", "/vendor/inter/5.3.0/index.css",
                   "/peach-logo.png", "/favicon.ico")
    SESSION_ASSETS = ("/dist/peach-app.js", "/dist/peach-app.css", "/app.css", "/board.css")

    async def asyncSetUp(self):
        from peach import access
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        access_path = root / "access.json"
        access.save(access_path, "entry-password")
        app = create_app(PeachSettings(configured=True, token="entry-token", access_path=access_path,
                                       db_path=fresh_ledger(root), follow_state_root=root / "state"))
        self.addCleanup(app.state.http_transport.close)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        self.addAsyncCleanup(self.client.aclose)
        routes_auth._ATTEMPTS.clear()

    async def test_the_login_page_and_everything_it_loads_need_no_session(self):
        home = await self.client.get("/")
        self.assertEqual(home.status_code, 303)
        self.assertEqual(home.headers["location"], "/login?next=/")
        page = await self.client.get("/login")
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-page="login"', page.text)
        for path in self.PAGE_ASSETS:
            with self.subTest(path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 200)
        for path in self.PAGE_ASSETS[:2]:
            with self.subTest(path):
                response = await self.client.head(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-cache")
                again = await self.client.get(path, headers={"If-None-Match": response.headers["etag"]})
                self.assertEqual(again.status_code, 304)

    async def test_the_other_bundles_and_modules_still_need_a_session(self):
        for path in self.SESSION_ASSETS:
            with self.subTest(path):
                self.assertEqual((await self.client.get(path)).status_code, 401)
        login = await self.client.post("/login", data={"token": "entry-password", "next": "/"})
        self.assertEqual(login.status_code, 303)
        for path in self.SESSION_ASSETS + self.PAGE_ASSETS:
            with self.subTest(path):
                self.assertEqual((await self.client.get(path)).status_code, 200)

    async def test_a_missing_bundle_opened_in_the_browser_is_the_error_page(self):
        login = await self.client.post("/login", data={"token": "entry-password", "next": "/"})
        self.assertEqual(login.status_code, 303)
        page = await self.client.get("/dist/no-such.js", headers={"Accept": "text/html"})
        self.assertEqual(page.status_code, 404)
        self.assertIn('data-page="error" data-status="404" data-detail="这个地址下没有页面。"', page.text)
        script = await self.client.get("/dist/no-such.js")
        self.assertEqual(script.status_code, 404)
        self.assertEqual(script.json(), {"error": "这个地址下没有页面。"})


if __name__ == "__main__":
    unittest.main()
