"""链接管理契约：取不到不等于没了，删除前必须重验。"""
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import contextmanager
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import link_status, web_links   # noqa: E402
from peach.jobs import BackgroundJob   # noqa: E402

from support.ledger import fresh_ledger   # noqa: E402


class FakeContract:
    """只提供 `LinkContract` 声明的那几项能力，且读写分开。

    把 `read_connection` 和 `write_connection` 指向同一个可写函数的话，
    `w_links_prune` 调了真契约上根本不存在的 `write_connection` 也照样绿——真的
    `WebContract` 只有 `read_connection` 与 `write_transaction`，线上删链接直接 500。
    读连接因此按真实实现开成 SQLite 的 `mode=ro`：拿读连接写库会当场报错，而不是
    悄悄成功。
    """

    def __init__(self, db: Path):
        self.db_path = db
        self.link_check = BackgroundJob("PeachLinkCheckJob", id_key="check_id")

    @contextmanager
    def read_connection(self):
        connection = sqlite3.connect(
            self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write_transaction(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()


class VerdictTests(unittest.TestCase):
    def test_prune_checks_retain_definitive_results_across_interruption(self):
        planned = [{'id': index, 'url': f'https://example.invalid/{index}'} for index in (1, 2, 3)]
        saved = {}
        def remember(**fields):
            saved.update(fields)
            if fields['checked'] == 2:
                raise InterruptedError('停止于检查点')
            return True
        with mock.patch.object(web_links, '_probe', side_effect=[(404, ''), (503, '')]):
            with self.assertRaises(InterruptedError):
                web_links._recheck_prune_links(planned, None, remember, {})
        with mock.patch.object(web_links, '_probe', side_effect=[(200, ''), (404, '')]) as probe:
            confirmed, recovered = web_links._recheck_prune_links(planned, None, None, saved)
        self.assertEqual([call.args[0] for call in probe.call_args_list],
                         ['https://example.invalid/2', 'https://example.invalid/3'])
        self.assertEqual([row['id'] for row in confirmed], [1, 3])
        self.assertEqual([row['id'] for row in recovered], [2])

    def test_only_a_gone_status_is_deletable(self):
        """取不到不等于没了，这两件事只有 404／410 能划清。

        实测反例：`linktr.ee` 回 403（Linktree 挡爬虫，浏览器里能正常打开）、
        `facebook.com` 回 400、`x.com` 回 500（临时错误，账号还在）、连接失败与超时。
        把它们并进 gone，删除时就会连好链接一起删。
        """
        self.assertEqual(web_links.link_verdict(200, ""), "ok")
        for status in (404, 410):
            self.assertEqual(web_links.link_verdict(status, ""), "gone", status)
        for status in (400, 403, 429, 500, 502, 503):
            self.assertEqual(web_links.link_verdict(status, ""), "unclear", status)
        self.assertEqual(web_links.link_verdict(0, "ConnectError"), "unclear")

    def test_a_parked_domain_answering_200_is_gone(self):
        """事务所注销、域名被停放平台接走之后，页面照样 200，内容已不是这家公司。"""
        page = (b'<title>Redirecting...</title>'
                b'<script>fetch("https://router.parklogic.com/model/221")</script>')
        client = httpx.Client

        def mocked(**kwargs):
            return client(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=page)), **kwargs)

        web_links.httpx.Client = mocked
        try:
            status, note = web_links._probe("https://www.crusegroup.net/model/221")
        finally:
            web_links.httpx.Client = client
        self.assertEqual(status, 200)
        self.assertIn("parklogic.com", note)
        self.assertEqual(web_links.link_verdict(status, note), "gone")
        self.assertIn("parklogic.com", web_links._note(status, note),
                      "面板上要看得出为什么 200 也判了没了")


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        db = fresh_ledger(self.tmp)
        connection = sqlite3.connect(db)
        connection.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,?,?,?,'2026-01-01','2026-01-01')",
            [(1, "performer", "凉森玲梦", "凉森玲梦"), (2, "studio", "MOODYZ", "moodyz")])
        connection.executemany(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,"
            "created_at,updated_at) VALUES(?,?,?,?,?,'2026-01-01','2026-01-01')",
            [(1, "social", "X @a", "https://x.com/a", "x.com"),
             (1, "official", "T-POWERS", "https://www.t-powers.co.jp/talent/x/", "www.t-powers.co.jp"),
             (2, "official", "官方网站", "https://moodyz.com/", "moodyz.com")])
        connection.commit()
        connection.close()
        self.contract = FakeContract(db)

    def test_the_summary_counts_links_entities_and_hosts(self):
        """面板一打开就要能回答「库里现在有什么」，所以这一条纯读库、不联网。"""
        info = web_links.w_links(self.contract)
        self.assertEqual(info["total"], 3)
        self.assertEqual(info["entities"], 2)
        self.assertEqual(info["by_kind"], {"social": 1, "official": 2})
        self.assertEqual(info["by_entity_kind"], {"performer": 2, "studio": 1})
        self.assertIn(("moodyz.com", 1), info["top_hosts"])

    def test_prune_refuses_without_a_finished_check(self):
        """没有检查结果就删除，等于凭空删。"""
        self.assertFalse(web_links.w_links_prune(self.contract, {"confirm": True})["ok"])

    def test_prune_refuses_a_stale_check_id(self):
        """拿一份放了半天的清单去删，删的可能是早已改好的链接。"""
        self.contract.link_check.state = {
            "check_id": "fresh", "status": "complete", "checked": 3, "total": 3,
            "gone": [{"id": 1, "entity": "凉森玲梦", "link_kind": "social",
                      "label": "X @a", "url": "https://x.com/a", "note": "HTTP 404"}],
            "unclear": [], "error": "",
        }
        out = web_links.w_links_prune(self.contract, {"confirm": True, "check_id": "old"})
        self.assertFalse(out["ok"])
        self.assertIn("过期", out["error"])
        self.assertEqual(
            sqlite3.connect(self.contract.db_path).execute(
                "SELECT count(*) FROM entity_link").fetchone()[0], 3)

    def test_prune_reverifies_and_keeps_links_that_came_back(self):
        """删除前逐条重验，这几秒钟换的是「不会因为一次网络抖动删掉好链接」。

        第一条重验仍是 404，删；第二条重验回了 200，保留并计入 recovered。
        """
        self.contract.link_check.state = {
            "check_id": "fresh", "status": "complete", "checked": 3, "total": 3,
            "gone": [
                {"id": 1, "entity": "凉森玲梦", "link_kind": "social", "label": "X @a",
                 "url": "https://x.com/a", "note": "HTTP 404"},
                {"id": 3, "entity": "MOODYZ", "link_kind": "official", "label": "官方网站",
                 "url": "https://moodyz.com/", "note": "HTTP 404"},
            ],
            "unclear": [], "error": "",
        }
        original = web_links._probe
        web_links._probe = lambda url, timeout=0: (404, "") if "x.com" in url else (200, "")
        try:
            out = web_links.w_links_prune(
                self.contract, {"confirm": True, "check_id": "fresh"})
        finally:
            web_links._probe = original
        self.assertEqual((out["removed"], out["recovered"]), (1, 1))
        remaining = {row[0] for row in sqlite3.connect(self.contract.db_path).execute(
            "SELECT url FROM entity_link")}
        self.assertNotIn("https://x.com/a", remaining)
        self.assertIn("https://moodyz.com/", remaining)

    def test_prune_requires_confirm(self):
        """不可逆动作不接受默认参数。"""
        self.assertFalse(web_links.w_links_prune(self.contract, {})["ok"])

    def test_prune_selected_ids_only_reverifies_and_removes_the_chosen_links(self):
        self._finished_check()
        state = self.contract.link_check.state
        state["gone"].append({"id": 3, "entity": "MOODYZ", "link_kind": "official",
                              "label": "官方网站", "url": "https://moodyz.com/", "note": "HTTP 404"})
        probed = []
        original = web_links._probe
        self.addCleanup(setattr, web_links, "_probe", original)
        web_links._probe = lambda url, timeout=0: probed.append(url) or (404, "")
        out = web_links.w_links_prune(self.contract, {"confirm": True, "check_id": "fresh", "ids": [3]})
        self.assertEqual(out["removed"], 1)
        self.assertEqual(probed, ["https://moodyz.com/"])
        self.assertEqual([item["id"] for item in self.contract.link_check.snapshot()["gone"]], [1])
        with self.contract.read_connection() as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT id FROM entity_link ORDER BY id")], [1, 2])

    def test_prune_selected_ids_rejects_empty_invalid_and_outside_selection(self):
        self._finished_check()
        for ids in ([], [2], [999], [True], ["1"], None):
            with self.subTest(ids=ids):
                out = web_links.w_links_prune(self.contract, {"confirm": True, "check_id": "fresh", "ids": ids})
                self.assertFalse(out["ok"])
        with self.contract.read_connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM entity_link").fetchone()[0], 3)

    def _finished_check(self):
        self.contract.link_check.state = {
            "check_id": "fresh", "status": "complete", "checked": 3, "total": 3,
            "scope": "all", "error": "",
            "gone": [{"id": 1, "entity": "凉森玲梦", "link_kind": "social",
                      "label": "X @a", "url": "https://x.com/a", "note": "HTTP 404"}],
            "unclear": [
                {"id": 2, "entity": "凉森玲梦", "link_kind": "official",
                 "label": "T-POWERS", "url": "https://www.t-powers.co.jp/talent/x/",
                 "note": "HTTP 403"},
                {"id": 3, "entity": "MOODYZ", "link_kind": "official",
                 "label": "官方网站", "url": "https://moodyz.com/", "note": "HTTP 500"},
            ],
        }

    def _retry(self, body):
        original_probe, original_interval = web_links._probe, web_links.CHECK_INTERVAL
        web_links._probe = lambda url, timeout=0: (
            (200, "") if "t-powers" in url else (500, ""))
        web_links.CHECK_INTERVAL = 0
        try:
            out = web_links.w_links_check(self.contract, body)
            thread = self.contract.link_check.thread
            if thread is not None:
                thread.join(5)
        finally:
            web_links._probe, web_links.CHECK_INTERVAL = original_probe, original_interval
        return out

    def test_retrying_the_picked_links_keeps_every_other_verdict(self):
        """重问几条不该让另外几百条的结论一起清零。

        整批重跑要好几分钟；「取不到」多半是站点挡爬虫或一次抖动，值得单独再问一次。
        如果重试把状态清空，删除失效链接就得再等一整轮才能按。
        """
        self._finished_check()
        started = self._retry({"retry": [2], "check_id": "fresh"})
        self.assertEqual((started["scope"], started["total"]), ("retry", 1))
        state = self.contract.link_check.snapshot()
        self.assertEqual(state["status"], "complete")
        self.assertEqual([item["id"] for item in state["gone"]], [1])
        self.assertEqual([item["id"] for item in state["unclear"]], [3])

    def test_a_retried_link_that_is_still_broken_comes_back(self):
        """重验没通过的要留在表里，否则一次重试就等于把它判成好的。"""
        self._finished_check()
        self._retry({"retry": [2, 3], "check_id": "fresh"})
        state = self.contract.link_check.snapshot()
        self.assertEqual([item["id"] for item in state["unclear"]], [3])
        self.assertEqual(state["unclear"][0]["note"], "HTTP 500")

    def test_retry_refuses_a_stale_result_or_an_unknown_link(self):
        """重试要改的是这一次结果里的行，别的 id 不该被一个请求标成已通过。"""
        self.assertFalse(web_links.w_links_check(
            self.contract, {"retry": [2]})["ok"])
        self._finished_check()
        stale = web_links.w_links_check(self.contract, {"retry": [2], "check_id": "old"})
        self.assertFalse(stale["ok"])
        self.assertIn("过期", stale["error"])
        unknown = web_links.w_links_check(
            self.contract, {"retry": [999], "check_id": "fresh"})
        self.assertFalse(unknown["ok"])
        self.assertEqual(self.contract.link_check.snapshot()["check_id"], "fresh")


class RetiredLinkTests(unittest.TestCase):
    """已隐退女优的失效链接留成标记，还在活动的照旧删除。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        db = fresh_ledger(self.tmp)
        connection = sqlite3.connect(db)
        connection.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,'performer',?,?,'2026-01-01','2026-01-01')",
            [(1, "东条苍", "东条苍"), (2, "神宫寺", "神宫寺")])
        connection.execute(
            "INSERT INTO performer_profile(entity_id,active_until,source,source_url,fetched_at) "
            "VALUES(1,2021,'minnano-av','https://www.minnano-av.com/','2026-01-01')")
        connection.executemany(
            "INSERT INTO entity_link(id,entity_id,link_kind,label,url,hostname,"
            "created_at,updated_at) VALUES(?,?,'official','Cruse Group',?,'crusegroup.net',"
            "'2026-01-01','2026-01-01')",
            [(1, 1, "https://crusegroup.net/model/316"),
             (2, 2, "https://www.crusegroup.net/model/295")])
        connection.commit()
        connection.close()
        self.contract = FakeContract(db)
        self.original_probe, self.original_interval = web_links._probe, web_links.CHECK_INTERVAL
        self.addCleanup(setattr, web_links, "_probe", self.original_probe)
        self.addCleanup(setattr, web_links, "CHECK_INTERVAL", self.original_interval)
        web_links.CHECK_INTERVAL = 0
        self.probed = []
        web_links._probe = lambda url, timeout=0: self.probed.append(url) or (404, "")

    def _prune(self):
        self.contract.link_check.state = {
            "check_id": "fresh", "status": "complete", "checked": 2, "total": 2,
            "scope": "all", "error": "", "unclear": [],
            "gone": [{"id": link_id, "entity": name, "link_kind": "official",
                      "label": "Cruse Group", "url": url, "note": "HTTP 404"}
                     for link_id, name, url in ((1, "东条苍", "https://crusegroup.net/model/316"),
                                                (2, "神宫寺", "https://www.crusegroup.net/model/295"))],
        }
        return web_links.w_links_prune(self.contract, {"confirm": True, "check_id": "fresh"})

    def test_prune_keeps_a_retired_performers_link_as_a_mark(self):
        out = self._prune()
        self.assertEqual((out["removed"], out["marked"]), (1, 1))
        rows = sqlite3.connect(self.contract.db_path).execute(
            "SELECT id, metadata_json FROM entity_link").fetchall()
        self.assertEqual([row[0] for row in rows], [1])
        self.assertEqual(link_status.gone_mark(rows[0][1])["note"], "HTTP 404")

    def test_a_full_check_does_not_probe_a_marked_link(self):
        """结论已经落账；每次都去敲一个停放域名只会招来杀毒软件告警。"""
        self._prune()
        self.probed.clear()
        web_links.w_links_check(self.contract, {"restart": True})
        thread = self.contract.link_check.thread
        if thread is not None:
            thread.join(5)
        state = self.contract.link_check.snapshot()
        self.assertEqual((state["status"], state["total"]), ("complete", 0))
        self.assertEqual(self.probed, [])


if __name__ == "__main__":
    unittest.main()
