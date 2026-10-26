"""番号发现源的契约层：拉取一轮、建壳、列表、已读与忽略（ADR-0042）。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from peach import feed_followup, feeds, web_feeds, web_settings  # noqa: E402
from peach.http import HttpResponse  # noqa: E402
from peach.web_contract import WebContract  # noqa: E402
from peach.web_router import dispatch_api_get, dispatch_api_post  # noqa: E402
from support.ledger import fresh_ledger  # noqa: E402

#: 端到端那一趟读的是临时 Peach 数据目录里的这份文件：拉取只吃已下载的字节，
#: 字节从网络来还是从盘上来与解析、去重、建壳那几步无关。
SAMPLE = """
<a href="/v/Ab2" class="box" title="x"><div class="video-title"><strong>SSIS-950</strong>
 作品标题</div><div class="meta">
 2026-09-21</div></a>
<a href="/v/Ab1" class="box" title="x"><div class="video-title"><strong>HMN-071</strong>
 另一部作品</div><div class="meta">
 2026-09-20</div></a>
<a href="/v/Ab0" class="box" title="x"><div class="video-title"><strong></strong>
 合集 30 部打包</div><div class="meta">
 2026-09-19</div></a>
"""


class FakeTransport:
    """按地址回一份事先备好的响应。记下每次请求，好核对条件请求头。"""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def __call__(self, request, timeout, max_bytes):
        self.requests.append(request)
        return self.responses[request.url]


class FeedWebFixture(unittest.TestCase):
    """临时账本、本地 feed 字节和替掉的取资料一步。不带用例，两个套件共用。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db_path = fresh_ledger(self.temporary.name)
        self.contract = WebContract(self.db_path)
        self.addCleanup(self.contract.stop_background_jobs)
        # 后继在服务里由后台线程领走，在测试里那是一个和断言赛跑的线程：它可能在
        # assert 之后才写完，也可能在临时目录删掉之后才醒。这里关掉自动领取，
        # 要跑后继的用例自己 `drain()`。
        self.contract.followups.stop()
        self.url = "https://javdb.com/actors/Smp1"
        sample = Path(self.temporary.name) / "feed-sample.html"
        sample.write_text(SAMPLE, encoding="utf-8")
        self.transport = FakeTransport({self.url: HttpResponse(
            200, {"ETag": 'W/"abc"', "Content-Type": "text/html"},
            sample.read_bytes(), self.url)})
        self._patched = web_feeds.fetch
        web_feeds.fetch = self._fetch
        self.addCleanup(lambda: setattr(web_feeds, "fetch", self._patched))
        # 建壳会派取资料后继，后继由契约自己的 runner 领走。不替掉问来源那一步的话，
        # 这个套件每跑一次就朝真实来源发一轮请求——番号是假的，配额是真的。
        self._collect = feed_followup.collect
        feed_followup.collect = lambda provider, code: (
            {"title": f"{code} 的标题", "studio": "示例厂牌",
             "release_date": "2026-09-20",
             "performers": [{"name": "深田えいみ"}]},
            "https://images.example.test/cover.jpg")
        self.addCleanup(lambda: setattr(feed_followup, "collect", self._collect))
        # 封面装进临时封面目录；取图那一步同样替掉，换成一张现画的横版封套。
        self.contract.cover_root = Path(self.temporary.name) / "covers"
        # 每一轮拉取还会给缺头像的存量女优派补头像，判缺不缺、试没试过都读这两处。
        self.contract.candidate_root = Path(self.temporary.name) / "generated"
        self.contract.avatar_root = self.contract.candidate_root / "avatars"
        self.cover_misses: set[str] = set()
        self.cover_unreachable: set[str] = set()
        self.cover_urls: dict[str, str | None] = {}
        self._fetch_cover = feed_followup.fetch_cover
        feed_followup.fetch_cover = self._fake_cover
        self.addCleanup(lambda: setattr(feed_followup, "fetch_cover", self._fetch_cover))

    def _fake_cover(self, contract, code, cover_url=None):
        self.cover_urls[code] = cover_url
        if code in self.cover_misses:
            raise RuntimeError("官方渠道没有这个番号的封面")
        if code in self.cover_unreachable:
            from peach.jav_cover_fetch import HOSTS_UNREACHABLE, CoverConnectError
            raise CoverConnectError(HOSTS_UNREACHABLE)
        from PIL import Image
        image = Image.new("RGB", (800, 538), (40, 40, 40))
        image.paste((200, 120, 150), (420, 0, 800, 538))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        return buffer.getvalue(), (800, 538), {"code": code, "source": "test"}

    def _fetch(self, transport, url, *, etag=None, last_modified=None):
        # 真实路径上的 SSRF 闸门要做 DNS，这里替换掉的正是那一层；条件请求头仍原样传下去。
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        from peach.http import HttpRequest
        return transport(HttpRequest("GET", url, headers), 30.0, 1 << 21)

    def _add(self, entity_id=None):
        with self.contract.database.write_transaction() as connection:
            return feeds.add_source(connection, url=self.url, name="示例源",
                                    entity_id=entity_id)

    def _check(self, **body):
        original = web_feeds._transport
        web_feeds._transport = lambda contract: self.transport
        try:
            return dispatch_api_post(self.contract, "/api/feeds/check", {"all": True, **body})
        finally:
            web_feeds._transport = original


class FeedWebTest(FeedWebFixture):
    def test_recovery_keeps_source_selection_progress_and_discovered_codes(self):
        from peach import job_recovery
        first = self._add()
        self.url = 'https://javdb.com/actors/Smp2'
        second = self._add()
        run = self.contract.task_runs.start('feed-check', trigger='manual', total=2)
        self.contract.task_runs.suspend(run.id)
        job_recovery.save(self.db_path, 'feed-check', 'original', {'background': True}, run_id=run.id, waiting=True)
        job_recovery.checkpoint(self.db_path, 'feed-check', 'original', dict(source_ids=[first, second],
            checked=1, total=2, added=1, results=[{'source': first, 'ok': True, 'codes': ['AAA-001']}]))
        seen = []
        def poll(contract, transport, source):
            seen.append((source['id'], contract.feed_job.snapshot()['checked']))
            return {'source': source['id'], 'ok': True, 'codes': ['BBB-002']}
        with patch.object(web_feeds, 'poll_source', side_effect=poll), \
                patch.object(web_feeds, '_transport', return_value=self.transport):
            job_recovery.resume(self.db_path, {'feed-check': lambda body: web_feeds.w_feed_check(self.contract, body)})
            self.contract.feed_job.thread.join(5)
        final = self.contract.feed_job.snapshot()
        self.assertEqual(seen, [(second, 1)])
        self.assertEqual((final['job_id'], final['run_id'], final['checked'], final['added']), ('original', run.id, 2, 2))
        self.assertEqual({row['source'] for row in final['results']}, {first, second})

    def test_javdb_provenance_is_unavailable_to_cover_readers(self):
        from PIL import Image
        target = self.contract.cover_root / 'SSIS-950.jpg'
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (800, 538)).save(target)
        evidence = target.with_suffix('.scraping.json')
        evidence.write_text(json.dumps({'source_url': 'https://c0.jdbstatic.com/covers/a.jpg'}), encoding='utf-8')
        self.contract.cache_bust()
        self.assertFalse(self.contract.has_cover('SSIS-950'))
        self.assertIsNone(self.contract.cover_path('SSIS-950'))
        evidence.write_text(json.dumps({'source_url': 'https://pics.dmm.co.jp/a.jpg'}), encoding='utf-8')
        self.contract.cache_bust()
        self.assertTrue(self.contract.has_cover('SSIS-950'))
        self.assertEqual(self.contract.cover_path('SSIS-950'), target)

    def test_known_discovery_without_cover_address_has_no_javdb_image_fallback(self):
        self._add()
        self._check()
        with self.contract.database.write_transaction() as connection:
            connection.execute('UPDATE feed_discovery SET cover_url=NULL')
        rows = dispatch_api_get(self.contract, '/api/feeds/discoveries', {})['items']
        self.assertTrue(rows)
        self.assertTrue(all('jdbstatic.com' not in row['cover_url'] and not row['cover_url'].startswith('/jav-cover') for row in rows))
        with self.contract.database.read_connection() as connection:
            self.assertTrue(all(row[0] is None for row in connection.execute('SELECT cover_url FROM feed_discovery')))

    def test_newwork_cast_reads_cached_source_faces_and_local_aliases_without_fetching(self):
        from peach.jav_discovery_metadata import discovery_metadata
        self._add()
        self._check()
        self._drain(1)
        with self.contract.database.write_transaction() as connection:
            connection.execute("INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                               " VALUES('performer','规范乙','规范乙',?,?)", (feeds.stamp(), feeds.stamp()))
            eid = connection.execute("SELECT id FROM entity WHERE canonical_name='规范乙'").fetchone()[0]
            connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                               " VALUES(?,'来源乙','来源乙','test')", (eid,))
        metadata = discovery_metadata(self.contract)
        with self.contract.database.read_connection() as connection:
            codes = [row[0] for row in connection.execute('SELECT code FROM feed_discovery')]
        for code in codes:
            metadata.remember(code, {'number': code, 'actors': [
                {'id': 'A', 'name': '深田えいみ', 'gender': 0, 'avatar_url': 'https://tp.spfcas.com/A.jpg'},
                {'id': 'B', 'name': '来源乙', 'gender': 0, 'avatar_url': 'https://tp.spfcas.com/B.jpg'},
                {'id': 'C', 'name': '来源男优', 'gender': 1}]})
        with patch('peach.javdb_public.PUBLIC_API.movie') as fetch:
            items = dispatch_api_get(self.contract, '/api/feeds/discoveries', {})['items']
        fetch.assert_not_called()
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item['performers'], '深田えいみ、规范乙')
            self.assertEqual([ref['source_actor_id'] for ref in item['performer_refs']], ['A', 'B'])
            self.assertTrue(all(ref['source_avatar'] for ref in item['performer_refs']))
            self.assertEqual(item['performer_refs'][1]['id'], eid)
        with self.contract.database.read_connection() as connection:
            self.assertEqual({row[0] for row in connection.execute('SELECT performers FROM feed_discovery')}, {'深田えいみ'})

    def test_home_shape_tracks_visible_discoveries_without_an_entity(self):
        def has_feed():
            return dispatch_api_get(self.contract, "/api/entity/shapes", {})["home"]["feed"]

        self.assertFalse(has_feed())
        self._add()
        self._check()
        self.assertTrue(has_feed())
        items = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["items"]
        dispatch_api_post(self.contract, "/api/feeds/discovery",
                          {"action": "read", "ids": [item["id"] for item in items]})
        self.assertTrue(has_feed())
        dispatch_api_post(self.contract, "/api/feeds/discovery",
                          {"action": "ignore", "ids": [item["id"] for item in items]})
        self.assertFalse(has_feed())

    def test_home_shape_applies_library_and_compilation_filters(self):
        self._compilation("波多野結衣、篠田ゆう")
        with self.contract.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO asset(location,path,name,medium,code) "
                "VALUES('R','R:\\media\\hmn071.mp4','hmn071.mp4','video','HMN-071')")
        shape = lambda: dispatch_api_get(self.contract, "/api/entity/shapes", {})["home"]["feed"]
        self.assertFalse(shape())
        dispatch_api_post(self.contract, "/api/settings", {"feedHideGroupCompilations": False})
        self.assertTrue(shape())

    def test_entity_shapes_use_the_real_jav_work_predicate(self):
        people = []
        with self.contract.database.write_transaction() as connection:
            for index, region in enumerate(("jp", "kr")):
                name = f"演示艺人{index}"
                entity_id = connection.execute(
                    "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                    " VALUES('performer',?,peach_normalize(?),?,?)",
                    (name, name, feeds.stamp(), feeds.stamp())).lastrowid
                asset_id = connection.execute(
                    "INSERT INTO asset(location,path,name,medium,code,region,studio)"
                    " VALUES('local',?,?,'video','SSIS-950',?,'S1')",
                    (f"R:\\media\\shape{index}.mp4", f"shape{index}.mp4", region)).lastrowid
                connection.execute(
                    "INSERT INTO asset_entity(asset_id,entity_id,role,source)"
                    " VALUES(?,?,'performer','test')", (asset_id, entity_id))
                people.append((entity_id, name, region == "jp"))
        shapes = {item["id"]: item["parts"] for item in dispatch_api_get(
            self.contract, "/api/entity/shapes", {})["entities"]}
        for entity_id, name, jav in people:
            items = dispatch_api_get(self.contract, "/api/items", {"performer": name})["items"]
            self.assertEqual(items[0]["is_jav"], jav)
            self.assertEqual("jav" in shapes.get(entity_id, []), jav)

    def test_settings_snapshot_lists_the_source(self):
        self._add()
        snapshot = dispatch_api_get(self.contract, "/api/feeds", {})
        self.assertEqual(len(snapshot["sources"]), 1)
        self.assertEqual(snapshot["sources"][0]["name"], "示例源")
        self.assertEqual(snapshot["sources"][0]["kind_label"], "JAV 订阅")
        self.assertTrue(snapshot["sources"][0]["enabled"])
        # 没挂人物就没有资料图可画。
        self.assertFalse(snapshot["sources"][0]["has_image"])

    def test_a_source_tied_to_a_performer_is_named_by_her_canonical_name(self):
        """挂着人物的源，名字是她在账本里的统称，登记时抄下的站上名字不再顶在前面。"""
        with self.contract.database.write_transaction() as connection:
            entity_id = int(connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer',?,peach_normalize(?),?,?)",
                ("凉森玲梦", "凉森玲梦", feeds.stamp(), feeds.stamp())).lastrowid)
        self._add(entity_id)
        [source] = dispatch_api_get(self.contract, "/api/feeds", {})["sources"]
        self.assertEqual((source["name"], source["entity_id"], source["entity_name"]),
                         ("凉森玲梦", entity_id, "凉森玲梦"))
        self.assertFalse(source["has_image"])

    def test_the_source_endpoint_takes_no_address_from_the_page(self):
        # 订阅只从人物页进，地址由服务端现拼（ADR-0047）；页面送来的地址一律不收。
        with self.assertRaises(ValueError):
            dispatch_api_post(self.contract, "/api/feeds/source",
                              {"action": "add", "url": "https://example.test/rss"})
        self.assertEqual(dispatch_api_get(self.contract, "/api/feeds", {})["sources"], [])

    def test_one_round_turns_a_local_feed_into_unfiled_new_releases(self):
        self._add()
        result = self._check()
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["results"][0]["error"], None)
        self.assertEqual(result["added"], 2)
        # 后继由结果声明，调度端统一派（ADR-0040）；同一轮发现的合成一条。
        self.assertEqual([(item["task_key"], item["key"]) for item in result["followups"]],
                         [("feed-scrape", "feed-scrape:SSIS-950,HMN-071")])
        listing = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})
        self.assertEqual([item["code"] for item in listing["items"]],
                         ["SSIS-950", "HMN-071"])
        self.assertFalse(listing["items"][0]["read"])

    def test_the_second_round_uses_the_stored_validators_and_adds_nothing(self):
        self._add()
        self._check()
        self.transport.responses[self.url] = HttpResponse(304, {}, b"", self.url)
        result = self._check()
        self.assertTrue(result["results"][0]["not_modified"])
        self.assertEqual(self.transport.requests[-1].headers["If-None-Match"], 'W/"abc"')
        listing = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})
        self.assertEqual(len(listing["items"]), 2)

    def test_missing_covers_follow_their_active_scrape_job(self):
        self._add()
        self._check()
        deadline = time.monotonic() + 5
        while not self.contract.task_runs.query(status="active", task_key=feed_followup.TASK_KEY):
            self.assertLess(time.monotonic(), deadline, "取资料后继未入队")
            time.sleep(0.01)
        items = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["items"]
        self.assertTrue(all(item["cover_fetching"] for item in items))
        self.assertTrue(all(not item["has_cover"] for item in items))
        self._drain(1)
        items = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["items"]
        self.assertFalse(any(item["cover_fetching"] for item in items))
        self.assertTrue(all(item["has_cover"] for item in items))

    def test_a_failing_source_reports_on_its_own_row_and_stays_scheduled(self):
        self._add()
        self.transport.responses[self.url] = HttpResponse(503, {}, b"", self.url)
        result = self._check()
        self.assertFalse(result["results"][0]["ok"])
        row = dispatch_api_get(self.contract, "/api/feeds", {})["sources"][0]
        self.assertEqual(row["last_error"], "来源回了 HTTP 503")
        self.assertIsNotNone(row["next_fetch_at"])

    def test_an_empty_alias_page_settles_quietly_and_names_its_row(self):
        """同一个人的第二页挂在旧艺名下、一部作品都没有：不算失败，行上说出是哪个名字。"""
        with self.contract.database.write_transaction() as connection:
            entity_id = int(connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer',?,peach_normalize(?),?,?)",
                ("森日向子", "森日向子", feeds.stamp(), feeds.stamp())).lastrowid)
        self._add(entity_id)
        empty = ('<span class="actor-section-name">白石アイリ</span><br>'
                 '<span class="section-meta">0 部影片</span>'
                 '<div class="empty-message">暂无内容</div>').encode("utf-8")
        self.transport.responses[self.url] = HttpResponse(200, {}, empty, self.url)
        self.assertTrue(self._check()["results"][0]["ok"])
        [row] = dispatch_api_get(self.contract, "/api/feeds", {})["sources"]
        self.assertEqual((row["last_error"], row["name"], row["page_name"]),
                         (None, "森日向子", "白石アイリ"))

    def test_read_and_ignore_are_separate_and_idempotent(self):
        self._add()
        self._check()
        first = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["items"][0]
        dispatch_api_post(self.contract, "/api/feeds/discovery",
                          {"action": "read", "ids": [first["id"]]})
        dispatch_api_post(self.contract, "/api/feeds/discovery",
                          {"action": "read", "ids": [first["id"]]})
        listing = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})
        self.assertTrue(listing["items"][0]["read"])
        self.assertFalse(listing["items"][0]["ignored"])

        dispatch_api_post(self.contract, "/api/feeds/discovery",
                          {"action": "ignore", "ids": [first["id"]]})
        active = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})
        self.assertEqual([item["code"] for item in active["items"]], ["HMN-071"])
        ignored = dispatch_api_get(self.contract, "/api/feeds/discoveries",
                                   {"state": "ignored"})
        self.assertEqual([item["code"] for item in ignored["items"]], ["SSIS-950"])

        dispatch_api_post(self.contract, "/api/feeds/discovery",
                          {"action": "unignore", "ids": [first["id"]]})
        back = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})
        # 取消忽略只恢复可见性，已读状态不跟着变。
        self.assertEqual([item["code"] for item in back["items"]],
                         ["SSIS-950", "HMN-071"])
        self.assertTrue(back["items"][0]["read"])

    def test_a_code_that_reaches_the_library_leaves_the_list(self):
        self._add()
        self._check()
        with self.contract.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO asset(location,path,name,medium,code) "
                "VALUES('R','R:\\\\media\\\\ssis950.mp4','ssis950.mp4','video','SSIS-950')")
        listing = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})
        self.assertEqual([item["code"] for item in listing["items"]], ["HMN-071"])

    def _compilation(self, performers: str) -> None:
        self._add()
        self._check()
        self._drain(1)
        with self.contract.database.write_transaction() as connection:
            connection.execute("UPDATE feed_discovery SET title='8時間 BEST',performers=?,"
                               "scraped_at=NULL WHERE code='SSIS-950'", (performers,))

    def _listed(self) -> list[str]:
        return [item["code"] for item in
                dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["items"]]

    def _backlog(self) -> list[str]:
        hidden = web_settings.hidden_compilations(self.contract.database)
        with self.contract.database.read_connection() as connection:
            return feed_followup.backlog(connection, self.contract.cover_root, hidden=hidden)

    def test_a_group_compilation_is_neither_listed_counted_nor_rescheduled(self):
        self._compilation("波多野結衣、篠田ゆう")
        listing = dispatch_api_get(self.contract, "/api/feeds/discoveries", {"limit": 1})
        # 分页按筛过的条数算：合集排在前面，要一条也还是拿到下一部，不是空页。
        self.assertEqual([item["code"] for item in listing["items"]], ["HMN-071"])
        self.assertFalse(listing["more"])
        self.assertEqual(dispatch_api_get(self.contract, "/api/feeds", {})["unread"], 1)
        self.assertEqual(self._backlog(), [])

    def test_the_two_switches_each_decide_their_own_kind(self):
        self._compilation("深田えいみ")
        # 单人合集默认照列，也照样补资料与封面。
        self.assertCountEqual(self._listed(), ["SSIS-950", "HMN-071"])
        self.assertEqual(self._backlog(), ["SSIS-950"])
        dispatch_api_post(self.contract, "/api/settings", {"feedHideSoloCompilations": True})
        self.assertEqual(self._listed(), ["HMN-071"])
        self.assertEqual(dispatch_api_get(self.contract, "/api/feeds", {})["unread"], 1)
        self.assertEqual(self._backlog(), [])
        # 换成大合集：默认收起；关掉大合集那个开关就回来，单人那个开关不管它。
        with self.contract.database.write_transaction() as connection:
            connection.execute("UPDATE feed_discovery SET performers='波多野結衣、篠田ゆう'"
                               " WHERE code='SSIS-950'")
        self.assertEqual(self._listed(), ["HMN-071"])
        dispatch_api_post(self.contract, "/api/settings", {"feedHideGroupCompilations": False})
        settings = dispatch_api_get(self.contract, "/api/settings", {})
        self.assertEqual((settings["feedHideGroupCompilations"],
                          settings["feedHideSoloCompilations"]), (False, True))
        self.assertCountEqual(self._listed(), ["SSIS-950", "HMN-071"])
        self.assertEqual(self._backlog(), ["SSIS-950"])

    def test_a_highlight_excerpt_is_hidden_by_studio_until_its_switch_is_off(self):
        self._compilation("篠田ゆう")
        with self.contract.database.write_transaction() as connection:
            connection.execute("UPDATE feed_discovery SET title='愛する妻が…',studio='ハイライト'"
                               " WHERE code='SSIS-950'")
        self.assertEqual(self._listed(), ["HMN-071"])
        self.assertEqual(self._backlog(), [])
        dispatch_api_post(self.contract, "/api/settings", {"feedHideExcerpts": False})
        self.assertCountEqual(self._listed(), ["SSIS-950", "HMN-071"])
        self.assertEqual(self._backlog(), ["SSIS-950"])

    def test_the_entity_filter_only_returns_that_person(self):
        with self.contract.database.write_transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer','深田えいみ',peach_normalize('深田えいみ'),?,?)",
                (feeds.stamp(), feeds.stamp()))
            entity_id = int(cursor.lastrowid)
        self._add(entity_id=entity_id)
        self._check()
        mine = dispatch_api_get(self.contract, "/api/feeds/discoveries",
                                {"entity": entity_id})
        self.assertEqual(len(mine["items"]), 2)
        nobody = dispatch_api_get(self.contract, "/api/feeds/discoveries",
                                  {"entity": entity_id + 1})
        self.assertEqual(nobody["items"], [])

    def test_the_shape_list_names_whoever_still_has_a_listed_new_release(self):
        with self.contract.database.write_transaction() as connection:
            entity_id = int(connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer','深田えいみ',peach_normalize('深田えいみ'),?,?)",
                (feeds.stamp(), feeds.stamp())).lastrowid)
            connection.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                " VALUES(?,'Eimi Fukada',peach_normalize('Eimi Fukada'),'test')", (entity_id,))
        def rows():
            return [entity for entity in dispatch_api_get(
                self.contract, "/api/entity/shapes", {})["entities"] if "feed" in entity["parts"]]

        self.assertEqual(rows(), [])
        self._add(entity_id=entity_id)
        self._check()
        self.assertEqual(rows(), [{"id": entity_id, "kind": "performer",
                                   "names": ["深田えいみ", "Eimi Fukada"], "parts": ["feed"]}])
        # 那几部都忽略掉，她的页面上就没有那一行了，名单跟着不再有她。
        ids = [item["id"] for item in dispatch_api_get(
            self.contract, "/api/feeds/discoveries", {"entity": entity_id})["items"]]
        dispatch_api_post(self.contract, "/api/feeds/discovery", {"action": "ignore", "ids": ids})
        self.assertEqual(rows(), [])

    def test_removing_a_source_keeps_the_new_releases_it_found(self):
        self._add()
        self._check()
        source_id = dispatch_api_get(self.contract, "/api/feeds", {})["sources"][0]["id"]
        dispatch_api_post(self.contract, "/api/feeds/source",
                          {"action": "remove", "id": source_id})
        listing = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})
        self.assertEqual(len(listing["items"]), 2)

    def _drain(self, expected):
        """等后继排上再跑掉它们。

        `w_feed_check` 在任务体收尾时就放行调用方，而声明的后继是任务包装那一层在
        之后派的（ADR-0040）。直接 `drain()` 会赶在入队之前，跑到零条。
        """
        deadline = time.monotonic() + 5.0
        done = 0
        while done < expected and time.monotonic() < deadline:
            done += self.contract.followups.drain()
        return done

    def test_the_scrape_followup_fills_the_shell_and_links_the_performer(self):
        with self.contract.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer','深田えいみ',peach_normalize('深田えいみ'),?,?)",
                (feeds.stamp(), feeds.stamp()))
        self._add()
        self._check()
        self.assertEqual(self._drain(1), 1)
        items = {row["code"]: row for row in dispatch_api_get(
            self.contract, "/api/feeds/discoveries", {})["items"]}
        item = items["SSIS-950"]
        self.assertEqual(item["title"], "SSIS-950 的标题")
        self.assertEqual(item["studio"], "示例厂牌")
        self.assertEqual(item["performers"], "深田えいみ")
        self.assertEqual(item["performer_refs"], [{"id": 1, "name": "深田えいみ", "has_image": False}])
        # 壳只认已有的人，不新建实体；关联上了人物页那一块才看得到。
        self.assertEqual(len(dispatch_api_get(
            self.contract, "/api/feeds/discoveries", {"entity": 1})["items"]), 2)

    def test_the_card_names_the_studio_the_way_the_library_does(self):
        """来源给的是日文厂牌名，卡片上写账本里那个厂牌的规范名；壳上仍存原文。"""
        with self.contract.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('studio','Sample Studio',peach_normalize('Sample Studio'),?,?)",
                (feeds.stamp(), feeds.stamp()))
            connection.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                " VALUES(last_insert_rowid(),'示例厂牌',peach_normalize('示例厂牌'),'test')")
        self._add()
        self._check()
        self._drain(1)
        items = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["items"]
        self.assertEqual({item["studio"] for item in items}, {"Sample Studio"})
        with self.contract.database.read_connection() as connection:
            self.assertEqual({row[0] for row in connection.execute(
                "SELECT studio FROM feed_discovery")}, {"示例厂牌"})

    def test_discovery_performer_references_resolve_existing_aliases_without_writing_names(self):
        with self.contract.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer','规范姓名',peach_normalize('规范姓名'),?,?)",
                (feeds.stamp(), feeds.stamp()))
            connection.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                " VALUES(last_insert_rowid(),'深田えいみ',peach_normalize('深田えいみ'),'test')")
        self._add()
        self._check()
        self._drain(1)
        items = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["items"]
        self.assertEqual({tuple((ref["id"], ref["name"]) for ref in item["performer_refs"])
                          for item in items}, {((1, "规范姓名"),)})
        self.assertEqual({item["performers"] for item in items}, {"规范姓名"})
        with self.contract.database.read_connection() as connection:
            self.assertEqual({row[0] for row in connection.execute("SELECT performers FROM feed_discovery")}, {"深田えいみ"})

    def test_discovery_avatars_share_entity_image_version_and_focus(self):
        with self.contract.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer','深田えいみ',peach_normalize('深田えいみ'),?,?)",
                (feeds.stamp(), feeds.stamp()))
        self._add()
        self._check()
        self._drain(1)
        focus = {"axis": "y", "pct": 12}
        with patch.object(self.contract, "has_entity_image", return_value=True), \
                patch.object(self.contract, "entity_image_version", return_value="image7"), \
                patch.object(self.contract, "avatar_focus", return_value=focus):
            items = dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["items"]
        for item in items:
            self.assertEqual(item["performer_refs"], [{
                "id": 1, "name": "深田えいみ", "has_image": True,
                "image_version": "image7", "avatar_focus": focus}])

    def test_one_batch_installs_each_cover_with_the_sidecars_the_cards_frame_by(self):
        self.cover_misses.add("HMN-071")
        self._add()
        self._check()
        self.assertEqual(self._drain(1), 1)
        [run] = self.contract.task_runs.query(task_key=feed_followup.TASK_KEY)
        self.assertEqual(run.result_summary, {"total": 2, "ok": 2, "miss": 0, "covers": 1})
        covers = self.contract.cover_root
        self.assertTrue((covers / "SSIS-950.jpg").is_file())
        self.assertTrue((covers / "SSIS-950.poster.json").is_file())
        self.contract.cache_bust()
        items = {row["code"]: row for row in dispatch_api_get(
            self.contract, "/api/feeds/discoveries", {})["items"]}
        # 装上的那部带着正封框，页面按资产卡同一套取景；没装上的只有来源地址。
        self.assertTrue(items["SSIS-950"]["has_cover"])
        self.assertEqual(items["SSIS-950"]["poster_box"]["px"], [800, 538])
        self.assertFalse(items["HMN-071"]["has_cover"])
        self.assertIsNone(items["HMN-071"]["poster_box"])
        attempt = json.loads((covers.parent / 'feed-cover-attempts/HMN-071.json').read_text(encoding='utf-8'))
        self.assertFalse(attempt['ok'])
        self.assertIn('error_type', attempt)

    def test_official_miss_does_not_install_a_bound_javdb_image(self):
        from peach.jav_cover_fetch import Unavailable
        self._add()
        self._check()
        with self.contract.database.write_transaction() as connection:
            connection.execute("UPDATE feed_discovery SET link='https://javdb.com/v/Source01' WHERE code='SSIS-950'")
        with patch('peach.jav_cover_fetch.best_cover', side_effect=Unavailable('官方封面未取得')), \
                patch('peach.javdb_public.PUBLIC_API.image') as source_image, \
                patch('peach.javdb_public.PUBLIC_API.remember_movie_identity') as identity:
            with self.assertRaisesRegex(Unavailable, '官方封面未取得'):
                self._fetch_cover(self.contract, 'SSIS-950')
        identity.assert_not_called()
        source_image.assert_not_called()

    def test_vr_layout_survives_without_a_detected_face(self):
        target = self.contract.cover_root / 'MDVR-276.face.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'layout': 'still', 'face': None}), encoding='utf-8')
        self.assertEqual(self.contract._cover_focus(str(target)), {'layout': 'still'})

    def test_cover_backlog_prioritises_the_least_recent_attempt(self):
        self._add()
        self._check()
        with self.contract.database.write_transaction() as connection:
            connection.execute("UPDATE feed_discovery SET scraped_at='2026-01-01T00:00:00Z',release_date='2026-10-01' WHERE code='SSIS-950'")
            connection.execute("UPDATE feed_discovery SET scraped_at='2025-01-01T00:00:00Z',release_date='2025-01-01' WHERE code='HMN-071'")
        with self.contract.database.read_connection() as connection:
            result = feed_followup.backlog(connection, self.contract.cover_root, hidden=frozenset(), limit=1)
        self.assertEqual(result, ['HMN-071'])

    def test_covers_cut_off_at_the_connection_are_counted_for_the_new_release_row(self):
        """连不上图片主机的几部单独记数，列表把最近一轮的数带给页面去提示换线路；
        官方没图的那几部不算进去。"""
        self.assertEqual(dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["cover_network"], 0)
        self.cover_unreachable.add("SSIS-950")
        self.cover_misses.add("HMN-071")
        self._add()
        self._check()
        self.assertEqual(self._drain(1), 1)
        [run] = self.contract.task_runs.query(task_key=feed_followup.TASK_KEY)
        self.assertEqual(run.result_summary,
                         {"total": 2, "ok": 2, "miss": 0, "covers": 0, "cover_network": 1})
        self.assertEqual(dispatch_api_get(self.contract, "/api/feeds/discoveries", {})["cover_network"], 1)

    def test_the_cover_step_gets_the_address_the_scrape_just_wrote(self):
        """封面地址是同一轮取资料才写上壳的，取图那一步拿到的得是它，不是空的。"""
        self._add()
        self._check()
        self._drain(1)
        self.assertEqual(self.cover_urls, {
            "SSIS-950": "https://images.example.test/cover.jpg",
            "HMN-071": "https://images.example.test/cover.jpg"})

    def test_a_shell_still_missing_its_cover_rejoins_a_batch_after_the_retry_window(self):
        self.cover_misses.add("HMN-071")
        self._add()
        self._check()
        self._drain(1)
        self.transport.responses[self.url] = HttpResponse(304, {}, b"", self.url)
        # 定时那一轮：刚试过的不再排；已经有封面、资料也齐的那部永远不再排。
        self.assertEqual(self._check(automatic=True)["followups"], [])
        earlier = feeds.stamp(datetime.now(timezone.utc)
                              - feed_followup.RETRY_AFTER - timedelta(minutes=1))
        with self.contract.database.write_transaction() as connection:
            connection.execute("UPDATE feed_discovery SET scraped_at=?", (earlier,))
        self.assertEqual([item["key"] for item in self._check(automatic=True)["followups"]],
                         ["feed-scrape:HMN-071"])
        # 人说了不想看的，不再替它花配额。
        first = {row["code"]: row for row in dispatch_api_get(
            self.contract, "/api/feeds/discoveries", {})["items"]}["HMN-071"]
        dispatch_api_post(self.contract, "/api/feeds/discovery",
                          {"action": "ignore", "ids": [first["id"]]})
        self.assertEqual(self._check()["followups"], [])

    def test_a_manual_check_retries_a_missing_cover_without_waiting_out_the_window(self):
        """人手点的那一轮不等重试窗口：点「检查」就是要现在取。"""
        self.cover_misses.add("HMN-071")
        self._add()
        self._check()
        self._drain(1)
        self.transport.responses[self.url] = HttpResponse(304, {}, b"", self.url)
        self.assertEqual([item["key"] for item in self._check()["followups"]],
                         ["feed-scrape:HMN-071"])

    def test_every_check_carries_performers_still_missing_an_avatar(self):
        """资料处理不跑的日子，缺头像的存量女优跟着定时拉取补（ADR-0097）。试过的不再带。"""
        from peach import avatar_followup
        from peach.followups import Attempts, attempts_root

        with self.contract.database.write_transaction() as connection:
            entity_id = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer','二の宮すずか','二の宮すずか',?,?)",
                ("2026-09-22T00:00:00Z", "2026-09-22T00:00:00Z")).lastrowid
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code,size)"
                " VALUES(9001,'R:','R:\\media\\NHDTB-557.mp4','NHDTB-557.mp4','video','NHDTB-557',1)")
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source)"
                " VALUES(9001,?,'performer','test')", (entity_id,))
        key = avatar_followup.followup_key("performer", entity_id)
        self.assertIn(key, [item["key"] for item in self._check(automatic=True)["followups"]])
        with self.contract.database.read_connection() as connection:
            current = avatar_followup.fingerprint(connection, entity_id)
        Attempts(attempts_root(self.contract.candidate_root)).record(key, current, "图库 6 张认不准")
        self.assertNotIn(key, [item["key"] for item in self._check(automatic=True)["followups"]])

    def test_an_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            dispatch_api_post(self.contract, "/api/feeds/discovery",
                              {"action": "delete", "ids": [1]})


#: JavDB 演员页里两部作品，形状照 `test_feeds.JAVDB_ACTOR` 那份抓回来的 HTML。
JAVDB_PAGE = """
<a href="/v/5nr8mp" class="box" title="x"><div class="video-title"><strong>PBD-528</strong>
 気高きお姉さん達</div><div class="meta">
 2026-10-20</div></a>
<a href="/v/RkPb5z" class="box" title="x"><div class="video-title"><strong>BBSS-106</strong>
 別の作品</div><div class="meta">
 2026-10-13</div></a>
"""

#: 隐退的人：主页最近只剩大合集，「單體作品」页才翻得到她自己的正片。
RETIRED_PAGE = """
<a href="/v/Rt0001" class="box" title="x"><div class="video-title"><strong>JKSR-736</strong>
 デカパイ奥様 16人4時間</div><div class="meta">
 2026-05-23</div></a>
"""
SOLO_PAGE = """
<a href="/v/Rt0001" class="box" title="x"><div class="video-title"><strong>JKSR-736</strong>
 デカパイ奥様 16人4時間</div><div class="meta">
 2026-05-23</div></a>
<a href="/v/Rt0002" class="box" title="x"><div class="video-title"><strong>MCSR-191</strong>
 欲情不倫妻</div><div class="meta">
 2025-07-19</div></a>
<a href="/v/Rt0003" class="box" title="x"><div class="video-title"><strong>CLO-350</strong>
 今晩、何回シテくれるか</div><div class="meta">
 2025-06-06</div></a>
"""


class PerformerFeedSwitchTest(FeedWebFixture):
    """人物页「订阅新作」开关：地址由服务端按 JavDB 演员页拼，页面只送开和关。"""

    def setUp(self):
        super().setUp()
        self.page = "https://javdb.com/actors/pRMq"
        self.transport.responses[self.page] = HttpResponse(
            200, {"Content-Type": "text/html"}, JAVDB_PAGE.encode("utf-8"), self.page)
        original = web_feeds._transport
        web_feeds._transport = lambda contract: self.transport
        self.addCleanup(lambda: setattr(web_feeds, "_transport", original))
        self.performer = self._performer("凉森れむ", ["pRMq"])

    def _performer(self, name, javdb_ids):
        with self.contract.database.write_transaction() as connection:
            entity_id = int(connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer',?,peach_normalize(?),?,?)",
                (name, name, feeds.stamp(), feeds.stamp())).lastrowid)
            for external_id in javdb_ids:
                connection.execute(
                    "INSERT INTO entity_external_ref(entity_id,provider,external_kind,"
                    "external_id) VALUES(?,'javdb','performer',?)", (entity_id, external_id))
        return entity_id

    def _switch(self, enabled, entity_id=None):
        return dispatch_api_post(self.contract, "/api/feeds/source", {
            "action": "follow", "entity_id": entity_id or self.performer,
            "enabled": enabled})

    def _settled(self):
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            state = self.contract.feed_job.snapshot() or {}
            if state.get("status") not in ("running", None):
                return state
            time.sleep(0.02)
        self.fail("订阅拉取没有在 5 秒内跑完")

    def _sources(self):
        return dispatch_api_get(self.contract, "/api/feeds", {})["sources"]

    def test_switching_on_subscribes_her_javdb_page_and_fetches_it_now(self):
        result = self._switch(True)
        self.assertTrue(result["following"])
        [source] = self._sources()
        self.assertEqual((source["kind"], source["url"], source["entity_id"]),
                         (feeds.KIND_JAVDB_ACTOR, self.page, self.performer))
        # 名字不另存，跟着人物走。
        self.assertEqual(source["name"], "凉森れむ")
        self.assertEqual(self._settled()["added"], 2)
        mine = dispatch_api_get(self.contract, "/api/feeds/discoveries",
                                {"entity": self.performer})
        self.assertEqual([item["code"] for item in mine["items"]], ["PBD-528", "BBSS-106"])

    def test_a_page_of_only_compilations_is_backfilled_from_her_solo_works(self):
        retired = self._performer("篠田ゆう", ["WE4e"])
        page = "https://javdb.com/actors/WE4e"
        self.transport.responses[page] = HttpResponse(
            200, {"Content-Type": "text/html"}, RETIRED_PAGE.encode("utf-8"), page)
        self.transport.responses[page + "?t=s"] = HttpResponse(
            200, {"Content-Type": "text/html"}, SOLO_PAGE.encode("utf-8"), page + "?t=s")
        self._switch(True, retired)
        state = self._settled()
        [report] = state["results"]
        self.assertTrue(report["ok"])
        self.assertEqual(report["backfilled"], 2)
        mine = dispatch_api_get(self.contract, "/api/feeds/discoveries", {"entity": retired})
        self.assertEqual([item["code"] for item in mine["items"]], ["MCSR-191", "CLO-350"])

    def test_a_missing_solo_page_leaves_the_round_itself_successful(self):
        self._switch(True)
        [report] = self._settled()["results"]
        self.assertTrue(report["ok"])
        self.assertIn("backfill_error", report)
        self.assertIsNone(report["error"])

    def test_switching_off_pauses_and_switching_on_again_reuses_the_source(self):
        self._switch(True)
        self._settled()
        self._switch(False)
        [paused] = self._sources()
        self.assertFalse(paused["enabled"])
        self._switch(True)
        self._settled()
        [again] = self._sources()
        self.assertEqual(again["id"], paused["id"])
        self.assertTrue(again["enabled"])

    def test_a_listed_page_without_a_person_is_adopted_instead_of_duplicated(self):
        with self.contract.database.write_transaction() as connection:
            feeds.add_source(connection, url=self.page)
        self._switch(True)
        self._settled()
        [source] = self._sources()
        self.assertEqual(source["entity_id"], self.performer)

    def test_every_javdb_page_of_hers_gets_its_own_source(self):
        twice = self._performer("释爱丽丝", ["d45k9", "ZX5z7"])
        result = self._switch(True, twice)
        self._settled()
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(sorted(source["url"] for source in self._sources()),
                         ["https://javdb.com/actors/ZX5z7", "https://javdb.com/actors/d45k9"])

    def test_the_profile_carries_the_switch_only_when_she_has_a_javdb_page(self):
        profile = dispatch_api_get(self.contract, "/api/entity",
                                   {"kind": "performer", "name": "凉森れむ"})
        self.assertEqual(profile["feed"], {"following": False})
        self._switch(True)
        self._settled()
        profile = dispatch_api_get(self.contract, "/api/entity",
                                   {"kind": "performer", "name": "凉森れむ"})
        self.assertEqual(profile["feed"], {"following": True})
        self._performer("無名の人", [])
        bare = dispatch_api_get(self.contract, "/api/entity",
                                {"kind": "performer", "name": "無名の人"})
        self.assertNotIn("feed", bare)

    def test_a_performer_without_a_javdb_page_is_refused(self):
        bare = self._performer("無名の人", [])
        with self.assertRaises(ValueError):
            self._switch(True, bare)
        self.assertEqual(self._sources(), [])


#: JavDB 演员搜索页：同一位的有碼与無碼两张卡，加一位同名的别人。写法照
#: `test_directory_link_harvest.JAVDB_SEARCH_PAGE`，多了無碼那枚 `info` 标记。
SEARCH_PAGE = """<div id="actors" class="actors">
 <div class="box actor-box"><a href="/actors/Kn01" title="瀬戸環奈, 瀨戶環奈, 濑户环奈">
  <figure class="image"><img class="avatar" src="https://c0.jdbstatic.com/avatars/kn/Kn01.jpg" />
  </figure><strong>瀬戸環奈</strong></a></div>
 <div class="box actor-box"><a href="/actors/Kn02" title="瀬戸環奈">
  <figure class="image"><span class="info">無碼</span></figure><strong>瀬戸環奈</strong></a></div>
 <div class="box actor-box"><a href="/actors/Zz99" title="別人さん">
  <strong>別人さん</strong></a></div>
</div>"""

#: 站上真有两位同名：有碼那种记录出现了两张。
TWINS_PAGE = """<div id="actors" class="actors">
 <div class="box actor-box"><a href="/actors/Tw01" title="瀬戸環奈"><strong>瀬戸環奈</strong></a></div>
 <div class="box actor-box"><a href="/actors/Tw02" title="瀬戸環奈"><strong>瀬戸環奈</strong></a></div>
</div>"""

LOGIN_PAGE = """<html><head><title> 登入 | JavDB 成人影片數據庫 </title></head>
<body><form action="/user_sessions"></form></body></html>"""


class FeedFollowByNameTest(PerformerFeedSwitchTest):
    """订阅源页签「添加 JAV 订阅」：按名字搜演员卡，勾选后登记到她名下并订上（ADR-0083）。"""

    def setUp(self):
        super().setUp()
        self.search = "https://javdb.com/search?f=actor&q=%E7%80%AC%E6%88%B8%E7%92%B0%E5%A5%88"
        self._serve(self.search, SEARCH_PAGE)
        for actor_id in ("Kn01", "Kn02", "Tw01", "Tw02"):
            self._serve(f"https://javdb.com/actors/{actor_id}", JAVDB_PAGE)

    def _serve(self, url, html):
        self.transport.responses[url] = HttpResponse(
            200, {"Content-Type": "text/html"}, html.encode("utf-8"), url)

    def _lookup(self, name="瀬戸環奈"):
        return dispatch_api_get(self.contract, "/api/feeds/lookup", {"q": name})

    def _follow(self, ids, name="瀬戸環奈"):
        return dispatch_api_post(self.contract, "/api/feeds/source",
                                 {"action": "follow-name", "name": name, "ids": ids})

    def _entity(self, entity_id):
        with self.contract.database.read_connection() as connection:
            row = connection.execute(
                "SELECT canonical_name,metadata_json FROM entity WHERE id=?", (entity_id,)).fetchone()
            refs = connection.execute(
                "SELECT external_id,metadata_json FROM entity_external_ref WHERE entity_id=?"
                " AND provider='javdb' ORDER BY external_id", (entity_id,)).fetchall()
        return row, refs

    def test_lookup_lists_every_card_and_only_pre_picks_the_one_person_matching_the_name(self):
        result = self._lookup()
        self.assertEqual(result["url"], self.search)
        self.assertEqual([card["id"] for card in result["candidates"]], ["Kn01", "Kn02", "Zz99"])
        first = result["candidates"][0]
        self.assertEqual(first["names"], ["瀬戸環奈", "瀨戶環奈", "濑户环奈"])
        self.assertEqual(first["url"], "https://javdb.com/actors/Kn01")
        self.assertEqual(result["candidates"][1]["record"], "無碼")
        # 有碼与無碼各一张，是同一个人；别人那张不在预勾里。
        self.assertEqual(result["suggested"], ["Kn01", "Kn02"])
        # 账本里还没有她：既没人持有这些 id，名字也对不上谁。
        self.assertIsNone(first["held_by"])
        self.assertEqual(first["matched"], [])
        self.assertIsNone(result["known"])
        self.assertEqual(self.transport.requests[-1].url, self.search)

    def test_lookup_tells_who_already_holds_the_card_and_whose_name_it_matches(self):
        held = self._performer("瀬戸環奈", ["Kn01"])
        with self.contract.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                " VALUES(?,'濑户环奈',peach_normalize('濑户环奈'),'test')", (held,))
        result = self._lookup()
        first, second = result["candidates"][:2]
        self.assertEqual(result["known"], {"id": held, "name": "瀬戸環奈"})
        self.assertEqual(first["held_by"], {"id": held, "name": "瀬戸環奈"})
        self.assertIsNone(second["held_by"])
        # 无码那张卡上只有正名，也对得上她；别名撞上的写法只报一次。
        self.assertEqual(first["matched"], [{"id": held, "name": "瀬戸環奈"}])
        self.assertEqual(second["matched"], [{"id": held, "name": "瀬戸環奈"}])

    def test_lookup_does_not_pre_pick_when_the_site_has_two_people_with_that_name(self):
        self._serve(self.search, TWINS_PAGE)
        result = self._lookup()
        self.assertEqual([card["id"] for card in result["candidates"]], ["Tw01", "Tw02"])
        self.assertEqual(result["suggested"], [])

    def test_lookup_refuses_a_login_page_and_an_empty_name(self):
        self._serve(self.search, LOGIN_PAGE)
        with self.assertRaisesRegex(ValueError, "登入页"):
            self._lookup()
        with self.assertRaises(ValueError):
            self._lookup("  ")

    def test_following_an_unknown_name_creates_her_binds_the_cards_and_subscribes_now(self):
        result = self._follow(["Kn01", "Kn02"])
        self.assertTrue(result["created"])
        self.assertEqual(result["entity_name"], "瀬戸環奈")
        row, refs = self._entity(result["entity_id"])
        self.assertEqual(row["canonical_name"], "瀬戸環奈")
        self.assertEqual(json.loads(row["metadata_json"])["source"], web_feeds.FOLLOW_SOURCE)
        self.assertEqual([ref["external_id"] for ref in refs], ["Kn01", "Kn02"])
        self.assertEqual(json.loads(refs[0]["metadata_json"])["source"], web_feeds.FOLLOW_SOURCE)
        # 两个演员页各一条源，全部启用、挂在她名下，当场拉了一趟。
        self.assertEqual(sorted(source["url"] for source in self._sources()),
                         ["https://javdb.com/actors/Kn01", "https://javdb.com/actors/Kn02"])
        self.assertTrue(all(source["entity_id"] == result["entity_id"] for source in self._sources()))
        self.assertEqual(self._settled()["added"], 2)
        # 新建的是零作品实体：她的资料页打得开，但作品索引里还没有她。
        profile = dispatch_api_get(self.contract, "/api/entity",
                                   {"kind": "performer", "name": "瀬戸環奈"})
        self.assertEqual(profile["feed"], {"following": True})

    def test_following_a_known_name_binds_to_her_instead_of_making_a_second_person(self):
        known = self._performer("瀬戸環奈", [])
        result = self._follow(["Kn01"])
        self.assertFalse(result["created"])
        self.assertEqual(result["entity_id"], known)
        _row, refs = self._entity(known)
        self.assertEqual([ref["external_id"] for ref in refs], ["Kn01"])
        self._settled()

    def test_a_card_already_held_wins_over_the_typed_name(self):
        holder = self._performer("瀬戸かんな", ["Kn01"])
        result = self._follow(["Kn01", "Kn02"], name="瀬戸環奈")
        self.assertEqual((result["entity_id"], result["entity_name"], result["created"]),
                         (holder, "瀬戸かんな", False))
        _row, refs = self._entity(holder)
        self.assertEqual([ref["external_id"] for ref in refs], ["Kn01", "Kn02"])
        self._settled()

    def test_cards_held_by_two_different_people_are_refused_by_name(self):
        self._performer("甲", ["Kn01"])
        self._performer("乙", ["Kn02"])
        with self.assertRaisesRegex(ValueError, "甲、乙"):
            self._follow(["Kn01", "Kn02"])
        self.assertEqual(self._sources(), [])

    def test_following_needs_a_name_and_well_formed_card_ids(self):
        with self.assertRaises(ValueError):
            self._follow(["Kn01"], name="")
        with self.assertRaises(ValueError):
            self._follow([])
        with self.assertRaises(ValueError):
            self._follow(["../Kn01"])
        self.assertEqual(self._sources(), [])


class JavDiscoveryTest(FeedWebFixture):
    def test_discovery_cast_projects_canonical_names_and_preserves_unknown_names(self):
        self._add()
        self._check()
        with self.contract.database.write_transaction() as connection:
            eid = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer','筱田优','筱田优',?,?)", (feeds.stamp(), feeds.stamp())).lastrowid
            connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES(?,?,?,'test')",
                               (eid, '篠田ゆう', '篠田ゆう'))
            connection.execute("UPDATE feed_discovery SET performers='篠田ゆう、筱田优、未知人物',studio='示例厂牌'")
        rankings = {'peach_studios': [{'name': '示例厂牌', 'score': 4}]}
        for metadata in ({}, {'actors': [{'name': '篠田ゆう'}, {'name': '筱田优'}, {'name': '未知人物'}]}):
            with self.subTest(source=bool(metadata)):
                rows = self._discover(rankings=rankings, metadata=metadata)['items']
                self.assertEqual(len(rows), 2)
                for row in rows:
                    self.assertEqual(row['performers'], '筱田优、未知人物')
                    self.assertEqual([ref['name'] for ref in row['performer_refs']], ['筱田优', '未知人物'])
                    self.assertEqual(row['performer_refs'][0]['id'], eid)
        with self.contract.database.read_connection() as connection:
            self.assertEqual({row[0] for row in connection.execute('SELECT performers FROM feed_discovery')},
                             {'篠田ゆう、筱田优、未知人物'})

    def test_source_cast_uses_female_and_unknown_references_without_male_features(self):
        self._add()
        self._check()
        result = self._discover(metadata={'actors': [
            {'id': 'Actor1', 'name': '示例女优', 'gender': 0, 'has_avatar': True},
            {'id': 'Actor2', 'name': '示例男优', 'gender': 1},
            {'id': 'Actor3', 'name': '未分类人物'}]})
        for item in result['items']:
            self.assertEqual(item['performers'], '示例女优、未分类人物')
            self.assertEqual([ref['name'] for ref in item['performer_refs']], ['示例女优', '未分类人物'])
            self.assertTrue(item['performer_refs'][0]['source_avatar'])
            self.assertFalse(any('示例男优' in reason for reason in item['reasons']))

    def test_persistent_metadata_supplies_canonical_filters_and_sort_values_without_detail(self):
        from peach import web_jav_discovery
        from peach.jav_discovery_metadata import discovery_metadata
        self._add()
        self._check()
        metadata = discovery_metadata(self.contract)
        with self.contract.database.read_connection() as connection:
            codes = [row[0] for row in connection.execute("SELECT code FROM feed_discovery ORDER BY id")]
        for code, score in zip(codes, (None, "4.23")):
            metadata.remember(code, {"number": code, "tags": [{"name": "中出"}], "score": score, "duration": 180})
        with patch.object(web_jav_discovery, "preference_profile", return_value=self._preference_fixture(
                {"browser_tags": [{"name": "中出内射", "score": 4}]})), \
                patch("peach.javdb_public.PUBLIC_API.movie") as movie:
            result = web_jav_discovery.q_jav_discoveries(self.contract, {"sort": "source_score"})
            metadata.job.thread.join(5)
        movie.assert_not_called()
        self.assertEqual([row["source_score"] for row in result["items"]], [4.23, None])
        self.assertTrue(all(row["tags"] == ["中出内射"] for row in result["items"]))
        self.assertTrue(all(row["duration"] == 10800 for row in result["items"]))
        self.assertEqual(result["ranking_method"], "field-sort")
        self.assertEqual([row["rank"] for row in result["items"]], [1, 2])
        self.assertTrue(all(row["taste_score"] == row["score"] for row in result["items"]))

    def _discover(self, scope="external", rankings=None, metadata=None, args=None):
        from peach import web_jav_discovery
        with patch.object(web_jav_discovery, "preference_profile", return_value=self._preference_fixture(rankings or {})), \
                patch.object(web_jav_discovery, "_cached_movie", return_value=metadata or {}), \
                patch("peach.jav_discovery_metadata.DiscoveryMetadata.ensure", return_value=None):
            return web_jav_discovery.q_jav_discoveries(self.contract, {"scope": scope, **(args or {})})

    def _preference_fixture(self, rankings):
        from peach import web_jav_discovery
        from peach.taste_preferences import WEIGHTS
        strengths, names, evidence = ({kind: {} for kind in WEIGHTS} for _ in range(3))
        with self.contract.database.read_connection() as connection:
            for key, kind in (("peach_studios", "studio"), ("peach_tags", "tag"),
                              ("browser_tags", "tag"), ("peach_series", "series")):
                for row in rankings.get(key, ()):
                    eid = row.get("entity_id") or web_jav_discovery._identity(connection, kind, row["name"])
                    strengths[kind][eid] = .7
                    names[kind][eid] = row["name"]
                    evidence[kind][eid] = ["浏览记录" if key.startswith("browser") else "观看与反馈"]
        return strengths, names, evidence

    def test_taste_ascending_preserves_the_selection_and_scores(self):
        self._add()
        self._check()
        with self.contract.database.write_transaction() as connection:
            connection.execute("UPDATE feed_discovery SET studio='示例厂牌'")
        rankings = {"peach_studios": [{"name": "示例厂牌", "score": 4}]}
        descending = self._discover(rankings=rankings, args={"direction": "desc"})
        ascending = self._discover(rankings=rankings, args={"direction": "asc"})
        self.assertEqual(ascending["direction"], "asc")
        self.assertEqual(ascending["ranking_method"], "mmr")
        self.assertEqual([row["id"] for row in ascending["items"]],
                         [row["id"] for row in reversed(descending["items"])])
        self.assertEqual([row["rank"] for row in ascending["items"]], [1, 2])
        def score_fields(rows):
            return {row["id"]: (row["score"], row["taste_score"], row["ranking_score"]) for row in rows}
        self.assertEqual(score_fields(ascending["items"]), score_fields(descending["items"]))

    def test_external_discovery_includes_older_known_works_and_studio_matches(self):
        self._add()
        self._check()
        with self.contract.database.write_transaction() as connection:
            connection.execute("UPDATE feed_discovery SET release_date='2010-01-01',studio='示例厂牌'")
        result = self._discover(rankings={"peach_studios": [{"name": "示例厂牌", "score": 4}]})
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(all(row["release_date"] == "2010-01-01" for row in result["items"]))
        self.assertTrue(all(row["reasons"] == ["厂牌偏好：示例厂牌（观看与反馈）"] for row in result["items"]))
        self.assertEqual(self._discover()["items"], [])

    def test_external_discovery_projects_obtained_metadata_and_canonical_tags(self):
        self._add()
        self._check()
        rankings = {"browser_tags": [{"name": "恋足", "score": 4}],
                    "peach_series": [{"name": "示例系列", "score": 2}]}
        self.assertEqual(self._discover(rankings=rankings)["items"], [])
        rows = self._discover(rankings=rankings, metadata={"tags": [{"name": "恋足"}], "series_name": "示例系列"})["items"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tags"], ["恋足"])
        self.assertEqual(rows[0]["reasons"], ["标签偏好：恋足（浏览记录）", "系列偏好：示例系列（观看与反馈）"])

    def test_library_exploration_excludes_completed_disliked_and_frequently_watched(self):
        with self.contract.database.write_transaction() as connection:
            entity_id = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('tag','恋足','恋足','2026-10-01','2026-10-01')").lastrowid
            for aid, plays, reached, feedback, disposal in (
                    (1, 0, 0, None, None), (2, 1, 10, None, None), (3, 1, 95, None, None),
                    (4, 2, 10, None, None), (5, 0, 0, 'dislike', None), (6, 0, 0, None, 'vanished')):
                connection.execute("INSERT INTO asset(id,location,path,name,medium,code,studio,duration,play_count,max_reached,feedback,disposal)"
                    " VALUES(?,'local',?,'示例作品','video',?,'示例厂牌',100,?,?,?,?)",
                    (aid, f"R:\\media\\SSIS-{aid:03d}.mp4", f"SSIS-{aid:03d}", plays, reached, feedback, disposal))
                connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source) VALUES(?,?,'tag','test')", (aid, entity_id))
        result = self._discover("library", {"peach_tags": [{"name": "恋足", "entity_id": entity_id, "score": 4}]})
        self.assertEqual({row["asset_id"] for row in result["items"]}, {1, 2})
        self.assertTrue(all(row["tags"] == ["恋足"] for row in result["items"]))
        self.assertTrue(all(row["reasons"] == ["标签偏好：恋足（观看与反馈）"] for row in result["items"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
