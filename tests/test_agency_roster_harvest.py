"""事务所名册采集：编号要核对到家，名册要翻到底，归属只补空着的那一位。"""
import json
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

from peach.entities import normalize_entity_name
from peach.migrations import upgrade
from scripts.harvest_agency_rosters import (
    ABSENT, AMBIGUOUS, INSTALLABLE, KNOWN, MISSING, MOVED, NEW, PRODUCTION, PROVIDER, REJECTED,
    apply_rows, fetch_roster, find_production, plan, roster_key, roster_url, search_url,
    shown_keys,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
STAMP = "2026-09-05T00:00:00Z"


def roster_html(names: list[tuple[str, str]], total: int, following: str = "") -> str:
    """按名册页的形状造一页：人在 JSON-LD 里，下一页在 `<link rel="next">` 里。"""
    listing = {"@type": "CollectionPage", "mainEntity": {
        "@type": "ItemList", "numberOfItems": total,
        "itemListElement": [
            {"item": {"name": name,
                      "url": f"https://www.minnano-av.com/actress{found}.html"}}
            for name, found in names]}}
    head = f'<link rel="next" href="{following}">' if following else ""
    return ('<html><head>' + head
            + '<script type="application/ld+json">'
            + json.dumps(listing, ensure_ascii=False)
            + '</script></head><body></body></html>')


def profile_html(shown: str, production: str) -> str:
    return ('<table><tr><td><span>所属事務所</span><p>'
            f'<a href="actress_list.php?production={production}">{shown}</a>'
            '</p></td></tr></table>')


class Site:
    """按地址发页的取数器替身；没登记的地址返回空正文，等同取不回来。"""

    def __init__(self, pages: dict[str, tuple[str, str]]):
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, url: str) -> tuple[str, str]:
        self.asked.append(url)
        return self.pages.get(url, (url, ""))


class ShownNameTests(unittest.TestCase):
    """站上那一格写的名字，哪几段可以拿去和账本对。"""

    def test_the_reading_in_brackets_is_matched_as_an_alias(self):
        """账本里是 `KRONE` 加别名 `クローネ`，站上写作 `KRONE(クローネ)`。

        整串拿去对，一个都对不上；拆开才落在这家名下，名册也才走得下去。
        """
        self.assertEqual(shown_keys("KRONE(クローネ)"),
                         {roster_key("KRONE"), roster_key("クローネ")})

    def test_a_former_name_in_brackets_does_not_claim_the_agency_that_still_exists(self):
        """`GG(旧・Prime Agency)` 里的旧名不作数。

        认下它，GG 的名册就整份挂到 Prime Agency 头上，而 Prime Agency 在账本里
        仍是独立一家。
        """
        keys = shown_keys("GG(旧・Prime Agency)")
        self.assertEqual(keys, {roster_key("GG")})

    def test_a_former_name_after_the_bracket_is_dropped_too(self):
        keys = shown_keys("SO MODEL AGENT(ソウ モデルエージェント)旧・Eightman Production")
        self.assertIn(roster_key("SO MODEL AGENT"), keys)
        self.assertNotIn(roster_key("Eightman Production"), keys)

    def test_typographic_noise_never_decides_a_match(self):
        """站上写 `明日花 キララ`，账本别名写 `明日花キララ`：空格不是名字的一部分。"""
        self.assertEqual(shown_keys("明日花 キララ"), shown_keys("明日花キララ"))


class ProductionLookupTests(unittest.TestCase):
    """编号只能从人身上取，取到还要核对到家。"""

    def setUp(self):
        self.agency = {"id": 30, "name": "KRONE", "aliases": ["クローネ"], "members": [11]}
        self.names = {11: "凉森玲梦"}
        self.aliases = {11: ["涼森れむ"]}

    def probe_site(self, shown: str, production: str) -> Site:
        return Site({search_url("涼森れむ"):
                     ("https://www.minnano-av.com/actress231277.html",
                      profile_html(shown, production))})

    def test_the_number_is_taken_from_a_known_member_and_checked_against_the_name(self):
        found, note = find_production(self.probe_site("KRONE(クローネ)", "573"),
                                      self.agency, self.names, self.aliases.get, 3)
        self.assertEqual(found, "573")
        self.assertIn("涼森れむ", note)

    def test_a_member_pointing_at_another_agency_is_refused(self):
        """采错的成员会把别家的编号装到这家头上，而复核件上看不出来。"""
        found, note = find_production(self.probe_site("T-POWERS", "110"),
                                      self.agency, self.names, self.aliases.get, 3)
        self.assertEqual(found, "")
        self.assertIn("不是这家", note)

    def test_an_agency_without_known_members_says_so(self):
        found, note = find_production(Site({}), {**self.agency, "members": []},
                                      self.names, self.aliases.get, 3)
        self.assertEqual(found, "")
        self.assertIn("没有已知成员", note)


class RosterWalkTests(unittest.TestCase):
    """站点在最后一页之后照旧给 `rel=next`，超范围的页原样重复最后一页。"""

    def pages(self, mapping: dict[str, str]) -> Site:
        return Site({url: (url, html) for url, html in mapping.items()})

    def test_the_walk_ends_when_a_page_brings_nobody_new(self):
        first, second = roster_url("573"), roster_url("573", 2)
        site = self.pages({
            first: roster_html([("篠田ゆう", "1"), ("鈴北梨乃", "2")], 2, second),
            second: roster_html([("篠田ゆう", "1"), ("鈴北梨乃", "2")], 2,
                                roster_url("573", 3)),
        })
        people, total, note = fetch_roster(site, "573", 20)
        self.assertEqual([name for name, _ in people], ["篠田ゆう", "鈴北梨乃"])
        self.assertEqual(total, 2)
        self.assertEqual(len(site.asked), 2)
        self.assertNotIn("没采全", note)

    def test_hitting_the_page_cap_is_reported_as_our_own_shortfall(self):
        """两种差额要分得开：这一种再翻还有人，另一种是站点自己的名册有重号。"""
        first, second = roster_url("110"), roster_url("110", 2)
        site = self.pages({
            first: roster_html([("A", "1")], 100, second),
            second: roster_html([("B", "2")], 100, roster_url("110", 3)),
        })
        _, _, note = fetch_roster(site, "110", 2)
        self.assertIn("页数封顶没采全", note)

    def test_a_page_that_cannot_be_fetched_stops_the_walk_and_says_which(self):
        _, _, note = fetch_roster(Site({}), "573", 20)
        self.assertIn("第 1 页取不回来", note)


class LedgerFixture(unittest.TestCase):
    """按真实形状建一份小账本：两家事务所、五位女优，一份五个人的名册。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, MIGRATIONS)
        self.con = sqlite3.connect(self.db)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'t','t')",
            [(11, "performer", "凉森玲梦", normalize_entity_name("凉森玲梦")),
             (12, "performer", "筱田优", normalize_entity_name("筱田优")),
             (13, "performer", "神雪", normalize_entity_name("神雪")),
             (14, "performer", "明日花绮罗罗", normalize_entity_name("明日花绮罗罗")),
             (15, "performer", "明日花キララ", normalize_entity_name("明日花キララ")),
             (30, "agency", "KRONE", normalize_entity_name("KRONE")),
             (31, "agency", "T-POWERS", normalize_entity_name("T-POWERS"))])
        self.con.executemany(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
            " VALUES(?,?,?,'test')",
            [(11, "涼森れむ", normalize_entity_name("涼森れむ")),
             (12, "篠田ゆう", normalize_entity_name("篠田ゆう")),
             (13, "神ユキ", normalize_entity_name("神ユキ")),
             (14, "明日花キララ", normalize_entity_name("明日花キララ")),
             (30, "クローネ", normalize_entity_name("クローネ"))])
        self.con.executemany(
            "INSERT INTO entity_membership(member_id,agency_id,source,confidence,checked_at)"
            " VALUES(?,?,'minnano-av:所属事務所',1.0,?)",
            [(11, 30, STAMP), (13, 31, STAMP)])
        self.con.execute(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id,"
            "metadata_json,last_synced_at) VALUES(?,?,?,'573','{}',?)",
            (30, PROVIDER, PRODUCTION, STAMP))
        self.con.commit()
        self.site = Site({roster_url("573"): (roster_url("573"), roster_html(
            [("涼森れむ", "231277"), ("篠田ゆう", "3145"), ("神ユキ", "9001"),
             ("明日花キララ", "3146"), ("誰も知らない", "9002")], 5))})

    def args(self, **overrides):
        base = {"only": ["KRONE"], "probe": 3, "max_pages": 20, "all_names": False}
        return types.SimpleNamespace(**{**base, **overrides})

    def verdicts(self, rows) -> dict[str, str]:
        return {str(row["roster_name"]): str(row["verdict"]) for row in rows}


class RosterPlanTests(LedgerFixture):
    """名册上的每个名字落到哪个判词。"""

    def test_the_known_number_spares_the_lookup_entirely(self):
        """编号记在账本里的那一家，不该再为它去搜一位成员。"""
        plan(self.site, self.con, self.args())
        self.assertEqual(self.site.asked, [roster_url("573")])

    def test_each_roster_name_lands_on_exactly_one_verdict(self):
        rows = plan(self.site, self.con, self.args())
        found = self.verdicts(rows)
        self.assertEqual(found["涼森れむ"], KNOWN)
        self.assertEqual(found["篠田ゆう"], NEW)
        self.assertEqual(found["神ユキ"], MOVED)
        self.assertEqual(found["明日花キララ"], AMBIGUOUS)

    def test_a_rejected_agency_is_not_offered_back_to_her(self):
        """她驳回过这一家，名册再收她也只记一行，`--apply` 不装。"""
        self.con.execute("UPDATE entity SET metadata_json=? WHERE id=12", (json.dumps(
            {"agency_rejected": [{"name": "KRONE(クローネ)"}]}, ensure_ascii=False),))
        found = self.verdicts(plan(self.site, self.con, self.args()))
        self.assertEqual(found["篠田ゆう"], REJECTED)
        self.assertNotIn(REJECTED, INSTALLABLE)

    def test_people_the_ledger_does_not_have_collapse_into_one_counted_row(self):
        """名册的绝大多数是库里没有的人，而这些行没有任何可执行的下一步。"""
        rows = [row for row in plan(self.site, self.con, self.args())
                if row["verdict"] == ABSENT]
        self.assertEqual(len(rows), 1)
        self.assertIn("誰も知らない", str(rows[0]["evidence"]))
        self.assertEqual(rows[0]["roster_name"], "")

    def test_all_names_writes_them_out_one_by_one(self):
        rows = plan(self.site, self.con, self.args(all_names=True))
        self.assertEqual(self.verdicts(rows)["誰も知らない"], ABSENT)

    def test_an_agency_whose_number_cannot_be_obtained_is_reported_as_missing(self):
        """一家取不到编号不能让整轮空转，它自己占一行「未取得」。"""
        rows = plan(Site({}), self.con, self.args(only=["T-POWERS"]))
        self.assertEqual([row["verdict"] for row in rows], [MISSING])

    def test_only_the_named_agencies_are_walked(self):
        rows = plan(self.site, self.con, self.args())
        self.assertEqual({row["agency"] for row in rows}, {"KRONE"})


class ApplyTests(LedgerFixture):
    """写入只补空着的那一位，别的判词一律不动账本。"""

    def test_a_new_membership_is_written_and_a_conflicting_one_is_left_alone(self):
        rows = plan(self.site, self.con, self.args())
        done = apply_rows(self.con, rows, STAMP)
        self.con.commit()
        held = dict(self.con.execute(
            "SELECT member_id, agency_id FROM entity_membership").fetchall())
        self.assertEqual(done["归属"], 1)
        self.assertEqual(held[12], 30)
        # 神雪 在账本里属于 T-POWERS，名册说她属于 KRONE：留给人判，不改。
        self.assertEqual(held[13], 31)
        self.assertEqual(self.con.execute(
            "SELECT source FROM entity_membership WHERE member_id=12").fetchone()[0],
            "minnano-av:事務所名簿")

    def test_an_ambiguous_name_is_never_installed(self):
        rows = plan(self.site, self.con, self.args())
        apply_rows(self.con, rows, STAMP)
        self.con.commit()
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM entity_membership WHERE member_id IN (14,15)"
        ).fetchone()[0], 0)

    def test_the_number_is_kept_so_the_next_run_need_not_ask_again(self):
        rows = plan(self.site, self.con, self.args())
        apply_rows(self.con, rows, STAMP)
        self.con.commit()
        self.assertEqual(self.con.execute(
            "SELECT external_id FROM entity_external_ref WHERE entity_id=30"
            " AND provider=? AND external_kind=?", (PROVIDER, PRODUCTION)).fetchone()[0],
            "573")


if __name__ == "__main__":
    unittest.main()
