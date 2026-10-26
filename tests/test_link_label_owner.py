"""链接标签跟着域名归属走，事务所名搬进实体元数据。"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.migrations import upgrade
from peach.social_links import OWNER_QUORUM, host_owners, owner_key
from scripts.repair_link_labels import agency_from_label, apply_rows, collect

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-04T00:00:00Z"


class LedgerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'t','t')",
            [(1, "studio", "Prestige", "prestige"),
             (10, "performer", "白石亚子", "白石亚子"),
             (11, "performer", "大槻响", "大槻响"),
             (12, "performer", "凉森玲梦", "凉森玲梦"),
             (13, "performer", "羽咲美晴", "羽咲美晴"),
             (14, "performer", "香西咲", "香西咲"),
             (15, "performer", "古川伊织", "古川伊织")])
        self.link_id = 0
        self.link(1, "official", "官方网站", "https://www.prestige-av.com/")
        # 事务所自家站上的共识：三条都写着 T-POWERS，够 quorum。
        for entity_id in (11, 12, 13):
            self.link(entity_id, "official", "T-POWERS",
                      f"http://www.t-powers.co.jp/official/talent/{entity_id}")
        self.link(10, "official", "T-POWERS",
                  "http://www.prestige-av.com/special/shiraishi_ako.php")
        self.link(14, "official", "T-POWERS", "http://saki-k.com")
        self.link(15, "official", "T-POWERS",
                  "https://www.facebook.com/miho.tanaka.334491")
        self.con.commit()

    def link(self, entity_id, kind, label, url):
        self.link_id += 1
        host = url.split("//", 1)[1].split("/", 1)[0]
        self.con.execute(
            "INSERT INTO entity_link(id,entity_id,link_kind,label,url,hostname,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,'t','t')",
            (self.link_id, entity_id, kind, label, url, host))

    def _rows(self):
        return {int(row["entity_id"]): row for row in collect(self.con)}


class HostOwnerTests(LedgerFixture):
    def test_a_studio_owns_the_domain_its_own_link_points_at(self):
        self.assertEqual(host_owners(self.con)["prestige-av.com"], "Prestige")

    def test_enough_performer_links_agreeing_establish_an_agency_domain(self):
        self.assertEqual(host_owners(self.con)["t-powers.co.jp"], "T-POWERS")

    def test_a_lone_link_does_not_get_to_certify_its_own_label(self):
        """孤证会自我确认：香西咲的个人站只出现过一次、恰好被贴了 T-POWERS。"""
        self.assertNotIn("saki-k.com", host_owners(self.con))
        self.assertGreater(OWNER_QUORUM, 1)

    def test_both_spellings_of_one_host_land_on_one_key(self):
        # 证据落在带不带 www 的哪一种，是采集时抓到什么决定的；键和 `host_of` 同一条规则。
        owners = host_owners(self.con)
        self.assertEqual(owner_key("www.prestige-av.com"), "prestige-av.com")
        self.assertNotIn("www.prestige-av.com", owners)

    def test_an_archived_official_site_does_not_hand_the_archive_to_a_studio(self):
        """关门厂牌的官网只剩存档快照，存档站本身不归任何一家。"""
        self.link(1, "official", "官网存档（2015-01）",
                  "https://web.archive.org/web/20150101000000/http://www.prestige-av.com/")
        self.assertNotIn("web.archive.org", host_owners(self.con))


class LabelRepairTests(LedgerFixture):
    def test_an_agency_label_on_a_studio_domain_is_rewritten(self):
        row = self._rows()[10]
        self.assertEqual((row["verdict"], row["new_label"]), ("auto", "Prestige"))
        self.assertEqual(row["agency"], "T-POWERS")

    def test_an_agency_label_on_its_own_domain_is_left_alone(self):
        self.assertEqual(self._rows()[11]["verdict"], "keep")

    def test_a_platform_account_stops_being_an_official_link(self):
        row = self._rows()[15]
        self.assertEqual((row["verdict"], row["new_link_kind"]), ("auto", "social"))
        self.assertEqual(row["new_label"], "Facebook @miho.tanaka.334491")

    def test_an_unverifiable_label_is_kept_but_reported(self):
        # 个人站的归属账本说不出来，改不改都是猜——留原样，写进复核表交人工。
        row = self._rows()[14]
        self.assertEqual((row["verdict"], row["new_label"]), ("review", "T-POWERS"))

    def test_a_generic_label_carries_no_agency(self):
        self.assertEqual(agency_from_label("官方网站", "www.prestige-av.com", set()), "")
        self.assertEqual(agency_from_label("Prestige", "x.jp", {"Prestige"}), "")
        # 标签就是域名本身时它是采集兜底，不是谁的名字。
        self.assertEqual(agency_from_label("tma.co.jp", "tma.co.jp", set()), "")

    def test_applying_moves_the_agency_into_entity_metadata(self):
        done = apply_rows(self.con, collect(self.con), STAMP)
        self.assertEqual(done["改了标签"], 2)
        self.assertEqual(done["其中归位成 social"], 1)
        agency = json.loads(self.con.execute(
            "SELECT metadata_json FROM entity WHERE id=10").fetchone()[0])["agency"]
        self.assertEqual(agency["name"], "T-POWERS")
        # 女优会移籍，没有时间戳就分不清哪一份更新。
        self.assertEqual(agency["checked_at"], STAMP)
        self.assertIn("minnano-av", agency["source"])

    def test_applying_rewrites_the_label_and_the_kind_in_place(self):
        apply_rows(self.con, collect(self.con), STAMP)
        self.assertEqual(
            self.con.execute("SELECT label FROM entity_link WHERE entity_id=10")
            .fetchone()[0], "Prestige")
        self.assertEqual(
            self.con.execute("SELECT link_kind FROM entity_link WHERE entity_id=15")
            .fetchone()[0], "social")

    def test_applying_leaves_the_rows_it_could_not_verify_untouched(self):
        apply_rows(self.con, collect(self.con), STAMP)
        for entity_id in (11, 14):
            self.assertEqual(
                self.con.execute("SELECT label,link_kind FROM entity_link"
                                 " WHERE entity_id=?", (entity_id,)).fetchone(),
                ("T-POWERS", "official"))


if __name__ == "__main__":
    unittest.main()
