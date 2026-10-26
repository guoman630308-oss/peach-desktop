"""重问所属事务所：选人是并集，判词按站上写的那一串，写入只碰移籍的那几行。"""
import contextlib
import io
import json
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

from peach.entities import normalize_entity_name
from peach.migrations import upgrade
from scripts.resync_performer_agency import (
    GONE, MISSING, MOVED, REJECTED, SAME, apply_rows, ask, plan, search_url, targets,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
STAMP = "2026-09-05T00:00:00Z"


def profile_html(shown: str) -> str:
    return ('<table><tr><td><span>所属事務所</span><p>'
            f'<a href="actress_list.php?production=1251">{shown}</a>'
            '</p></td></tr></table>')


class Response:
    def __init__(self, status: int, url: str, body: str = ""):
        self.status = status
        self.url = url
        self.body = body.encode("utf-8")


class Http:
    """按检索地址发页的取数器替身。没登记的名字停在检索页，等同未唯一命中。"""

    def __init__(self, pages: dict[str, Response]):
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, request, timeout, limit):
        self.asked.append(request.url)
        return self.pages.get(
            request.url, Response(200, "https://www.minnano-av.com/search_result.php"))

    def close(self):
        pass


class AskTests(unittest.TestCase):
    """站点那一问只有三种结果：命中、没这一格、取不到。"""

    def page(self, shown: str) -> Http:
        return Http({search_url("松本一香"): Response(
            200, "https://www.minnano-av.com/actress21661.html", profile_html(shown))})

    def test_the_shown_name_comes_back_whole(self):
        """括号里那一段是拆名字那一步要用的，采集这一步不动它。"""
        shown, found, note = ask(self.page("ELTRA(エルトラ)旧・LIGHT"), "松本一香", 1.0)
        self.assertEqual(shown, "ELTRA(エルトラ)旧・LIGHT")
        self.assertEqual(found, "21661")
        self.assertIn("actress21661", note)

    def test_a_search_page_that_did_not_redirect_is_not_read(self):
        """检索页正文里那一堆女优是「相关女优」，信号只在最终地址里。"""
        shown, found, note = ask(self.page("ELTRA"), "查无此人", 1.0)
        self.assertEqual((shown, found), ("", ""))
        self.assertIn("未取得", note)

    def test_a_transport_failure_is_a_row_not_a_crash(self):
        class Broken:
            def __call__(self, *args):
                raise TimeoutError("connect")

        shown, _, note = ask(Broken(), "松本一香", 1.0, tries=1)
        self.assertEqual(shown, "")
        self.assertIn("未取得", note)

    def test_a_blip_on_the_first_try_does_not_become_a_verdict(self):
        """一次 SSL EOF 写出来的行，和「站点查不到这个人」长得一模一样。"""
        page = self.page("EST(エスト)旧・LIGHT")

        class Flaky:
            def __init__(self):
                self.calls = 0

            def __call__(self, request, timeout, limit):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("SSL: UNEXPECTED_EOF_WHILE_READING")
                return page(request, timeout, limit)

        flaky = Flaky()
        shown, _, note = ask(flaky, "松本一香", 1.0, pause=0.0)
        self.assertEqual(shown, "EST(エスト)旧・LIGHT")
        self.assertEqual(flaky.calls, 2)

    def test_a_search_page_that_never_redirects_is_not_retried(self):
        """再问一遍还是同一个答案，重试只是多敲别人的门。"""
        http = self.page("EST")
        ask(http, "查无此人", 1.0, pause=0.0)
        self.assertEqual(len(http.asked), 1)


class LedgerFixture(unittest.TestCase):
    """一家事务所、三位成员，外加一位没有归属的人。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, MIGRATIONS)
        self.con = sqlite3.connect(self.db)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        held = json.dumps({"agency": {"name": "LIGHT", "source": "t", "checked_at": STAMP}},
                          ensure_ascii=False)
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,metadata_json,"
            "created_at,updated_at) VALUES(?,?,?,?,?,'t','t')",
            [(11, "performer", "松本一香", normalize_entity_name("松本一香"), held),
             (12, "performer", "宫西光", normalize_entity_name("宫西光"), held),
             (13, "performer", "小泉日向", normalize_entity_name("小泉日向"), held),
             (14, "performer", "北野未奈", normalize_entity_name("北野未奈"), "{}"),
             (30, "agency", "LIGHT", normalize_entity_name("LIGHT"), "{}")])
        self.con.executemany(
            "INSERT INTO entity_membership(member_id,agency_id,source,confidence,checked_at)"
            " VALUES(?,30,'minnano-av:所属事務所',1.0,?)",
            [(11, STAMP), (12, STAMP), (13, STAMP)])
        self.con.commit()

    def agency_of(self, entity_id: int) -> dict:
        row = self.con.execute(
            "SELECT metadata_json FROM entity WHERE id=?", (entity_id,)).fetchone()
        return json.loads(row[0] or "{}").get("agency") or {}


class TargetTests(LedgerFixture):

    def test_a_whole_agency_can_be_asked_again(self):
        found = targets(self.con, ["LIGHT"], [])
        self.assertEqual([row["performer"] for row in found],
                         ["松本一香", "宫西光", "小泉日向"])
        self.assertEqual(found[0]["current_agency"], "LIGHT")

    def test_a_named_outsider_joins_the_batch(self):
        """移籍走了的人不在这一家名下，两个条件是并集才问得到她。"""
        found = targets(self.con, ["LIGHT"], ["北野未奈"])
        self.assertEqual(len(found), 4)
        self.assertEqual(found[-1]["current_agency"], "")

    def test_the_same_person_named_twice_is_asked_once(self):
        self.assertEqual(len(targets(self.con, ["LIGHT"], ["松本一香"])), 3)

    def test_the_japanese_alias_leads_the_search_chain(self):
        """站点只认日文写法，规范名是简体中文；不带别名去问就查不到这个人。"""
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
            " VALUES(11,'松本いちか',?,'test')", (normalize_entity_name("松本いちか"),))
        found = targets(self.con, [], ["松本一香"])
        self.assertEqual(found[0]["chain"], ["松本いちか", "松本一香"])


class PlanTests(LedgerFixture):

    def args(self, **overrides):
        base = {"agency": ["LIGHT"], "only": [], "interval": 0.0, "timeout": 1.0}
        return types.SimpleNamespace(**{**base, **overrides})

    def http(self, answers: dict[str, str]) -> Http:
        return Http({search_url(name): Response(
            200, f"https://www.minnano-av.com/actress{2000 + index}.html",
            profile_html(shown))
            for index, (name, shown) in enumerate(answers.items())})

    def run_plan(self, answers: dict[str, str]):
        """进度是打给人看的，测试把它接走：控制台是 GBK，中点写不进去。"""
        with contextlib.redirect_stdout(io.StringIO()):
            return plan(self.con, self.http(answers), self.args())

    def test_each_person_lands_on_exactly_one_verdict(self):
        rows = self.run_plan({"松本一香": "ELTRA(エルトラ)旧・LIGHT", "宫西光": "LIGHT",
                              "小泉日向": ""})
        self.assertEqual({str(row["performer"]): str(row["verdict"]) for row in rows},
                         {"松本一香": MOVED, "宫西光": SAME, "小泉日向": GONE})

    def test_the_agency_she_rejected_is_not_a_move(self):
        """站上那一格写的是她驳回过的那家（个人经纪公司），不当移籍写回元数据。"""
        self.con.execute("UPDATE entity SET metadata_json=? WHERE id=11", (json.dumps(
            {"agency_rejected": [{"name": "株式会社Miss"}]}, ensure_ascii=False),))
        rows = self.run_plan({"松本一香": "株式会社Miss", "宫西光": "LIGHT", "小泉日向": ""})
        self.assertEqual(str(rows[0]["verdict"]), REJECTED)
        apply_rows(self.con, rows, "later")
        self.assertEqual(self.agency_of(11), {})

    def test_a_page_that_never_arrived_is_not_a_departure(self):
        """取不到和「这一格是空的」必须分得开，否则重跑一次就把人清空了。"""
        rows = self.run_plan({})
        self.assertEqual({str(row["verdict"]) for row in rows}, {MISSING})

    def with_alias(self):
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
            " VALUES(11,'松本いちか',?,'test')", (normalize_entity_name("松本いちか"),))

    def test_the_chain_stops_at_the_first_name_that_lands(self):
        """日文名排在链首，它命中了就不该再为简体名多敲一次门。"""
        self.with_alias()
        http = self.http({"松本いちか": "EST(エスト)旧・LIGHT"})
        with contextlib.redirect_stdout(io.StringIO()):
            rows = plan(self.con, http, self.args(agency=[], only=["松本一香"]))
        self.assertEqual(str(rows[0]["verdict"]), MOVED)
        self.assertEqual(http.asked, [search_url("松本いちか")])

    def test_a_miss_on_the_first_name_tries_the_next(self):
        self.with_alias()
        http = self.http({"松本一香": "EST(エスト)旧・LIGHT"})
        with contextlib.redirect_stdout(io.StringIO()):
            rows = plan(self.con, http, self.args(agency=[], only=["松本一香"]))
        self.assertEqual(str(rows[0]["verdict"]), MOVED)
        self.assertEqual(len(http.asked), 2)


class ApplyTests(LedgerFixture):

    def rows(self, **overrides):
        base = {"entity_id": 11, "performer": "松本一香", "current_agency": "LIGHT",
                "site_agency": "ELTRA(エルトラ)旧・LIGHT", "verdict": MOVED,
                "evidence": "t"}
        return [{**base, **overrides}]

    def test_a_move_overwrites_the_name_with_a_fresh_stamp(self):
        apply_rows(self.con, self.rows(), "2026-09-06T00:00:00Z")
        found = self.agency_of(11)
        self.assertEqual(found["name"], "ELTRA(エルトラ)旧・LIGHT")
        self.assertEqual(found["checked_at"], "2026-09-06T00:00:00Z")

    def test_an_unchanged_row_is_left_alone(self):
        apply_rows(self.con, self.rows(site_agency="LIGHT", verdict=SAME), "later")
        self.assertEqual(self.agency_of(11)["checked_at"], STAMP)

    def test_an_empty_cell_never_clears_the_ledger(self):
        """站点某天不显示这一格，和这个人真的解约了，在页面上长得一模一样。"""
        apply_rows(self.con, self.rows(site_agency="", verdict=GONE), "later")
        self.assertEqual(self.agency_of(11)["name"], "LIGHT")

    def test_membership_is_left_to_the_installer(self):
        """归属表主键在成员一侧，移籍要走 REPLACE，那是 install_agencies 的事。"""
        apply_rows(self.con, self.rows(), STAMP)
        self.assertEqual(self.con.execute(
            "SELECT agency_id FROM entity_membership WHERE member_id=11").fetchone()[0], 30)

    def test_other_metadata_survives_the_rewrite(self):
        self.con.execute(
            "UPDATE entity SET metadata_json=? WHERE id=11",
            (json.dumps({"agency": {"name": "LIGHT"}, "note": "留着"}, ensure_ascii=False),))
        apply_rows(self.con, self.rows(), STAMP)
        row = self.con.execute("SELECT metadata_json FROM entity WHERE id=11").fetchone()
        self.assertEqual(json.loads(row[0])["note"], "留着")


if __name__ == "__main__":
    unittest.main()
