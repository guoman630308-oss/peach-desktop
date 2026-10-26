"""驳回一家事务所：记在人身上，撤归属、删空了的实体，重跑安装与标签修复都不写回。"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.entities import normalize_entity_name, rejected_agencies
from peach.migrations import upgrade
from scripts.install_agencies import collect as agency_plan
from scripts.reject_agency import apply_rows, plan
from scripts.repair_link_labels import apply_rows as repair_labels
from scripts.repair_link_labels import collect as collect_labels

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
STAMP = "2026-09-24T00:00:00Z"


class RejectFixture(unittest.TestCase):
    """一家只有她一个人的个人公司，一家有两个人的事务所。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        upgrade(self.db, MIGRATIONS)
        self.con = sqlite3.connect(self.db)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        held = {"agency": {"name": "株式会社Miss", "checked_at": STAMP}}
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,metadata_json,"
            "created_at,updated_at) VALUES(?,?,?,?,?,'t','t')",
            [(11, "performer", "三上悠亚", normalize_entity_name("三上悠亚"),
              json.dumps(held, ensure_ascii=False)),
             (12, "performer", "松本一香", normalize_entity_name("松本一香"), "{}"),
             (13, "performer", "宫西光", normalize_entity_name("宫西光"), "{}"),
             (30, "agency", "株式会社Miss", normalize_entity_name("株式会社Miss"), "{}"),
             (31, "agency", "LIGHT", normalize_entity_name("LIGHT"), "{}")])
        self.con.executemany(
            "INSERT INTO entity_membership(member_id,agency_id,source,confidence,checked_at)"
            " VALUES(?,?,'minnano-av:所属事務所',1.0,?)",
            [(11, 30, STAMP), (12, 31, STAMP), (13, 31, STAMP)])
        self.con.execute(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,created_at,updated_at)"
            " VALUES(30,'official','官方网站','https://miss.co.jp/','miss.co.jp','t','t')")
        self.con.execute(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id,"
            "metadata_json,last_synced_at) VALUES(30,'minnano-av','production','1215','{}',?)",
            (STAMP,))
        self.con.commit()

    def reject(self, performer, agency, reason="个人经纪公司", replacement=""):
        rows = plan(self.con, [{"performer": performer, "agency": agency, "reason": reason,
                                "replacement": replacement}])
        done = apply_rows(self.con, rows, STAMP)
        self.con.commit()
        return rows, done

    def metadata(self, entity_id):
        row = self.con.execute("SELECT metadata_json FROM entity WHERE id=?", (entity_id,))
        return json.loads(row.fetchone()[0] or "{}")

    def count(self, sql, *params):
        return self.con.execute(sql, params).fetchone()[0]


class RejectTests(RejectFixture):

    def test_her_only_company_goes_away_with_its_links_and_numbers(self):
        _, done = self.reject("三上悠亚", "株式会社Miss")
        self.assertEqual(done, {"驳回": 1, "归属": 1, "事务所": 1, "改挂": 0, "新建事务所": 0,
                                "链接标签": 0})
        self.assertEqual(self.count("SELECT count(*) FROM entity WHERE id=30"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM entity_link WHERE entity_id=30"), 0)
        self.assertEqual(
            self.count("SELECT count(*) FROM entity_external_ref WHERE entity_id=30"), 0)
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_the_rejection_stays_on_her_and_the_stale_evidence_goes(self):
        self.reject("三上悠亚", "株式会社Miss")
        found = self.metadata(11)
        self.assertNotIn("agency", found)
        self.assertEqual(rejected_agencies(found), {normalize_entity_name("株式会社Miss")})
        self.assertEqual(found["agency_rejected"][0]["source"], "user:manual")

    def test_an_agency_with_other_members_keeps_its_entity(self):
        rows, done = self.reject("松本一香", "LIGHT")
        self.assertEqual(done["事务所"], 0)
        self.assertEqual(self.count("SELECT count(*) FROM entity_membership WHERE agency_id=31"), 1)
        self.assertIn("还有 1 人", str(rows[0]["note"]))

    def test_rejecting_twice_records_it_once(self):
        self.reject("三上悠亚", "株式会社Miss")
        _, done = self.reject("三上悠亚", "株式会社Miss")
        self.assertEqual(done["驳回"], 0)
        self.assertEqual(len(self.metadata(11)["agency_rejected"]), 1)

    def test_an_unknown_performer_is_reported_not_written(self):
        rows, done = self.reject("不存在的人", "株式会社Miss")
        self.assertTrue(str(rows[0]["note"]).startswith("未取得"))
        self.assertEqual(set(done.values()), {0})

    def test_the_replacement_becomes_her_agency_and_survives_a_reinstall(self):
        """改挂到 AV 时期的事务所：实体、读音别名、归属、证据四样一致，重跑安装得出同一家。"""
        _, done = self.reject("三上悠亚", "株式会社Miss",
                              replacement="ONE'S DOUBLE(ワンズダブル)")
        self.assertEqual((done["改挂"], done["新建事务所"]), (1, 1))
        agency_id = self.count("SELECT agency_id FROM entity_membership WHERE member_id=11")
        self.assertEqual(self.count("SELECT canonical_name FROM entity WHERE id=?", agency_id),
                         "ONE'S DOUBLE")
        self.assertEqual(self.count("SELECT alias FROM entity_alias WHERE entity_id=?", agency_id),
                         "ワンズダブル")
        self.assertEqual(self.metadata(11)["agency"]["name"], "ONE'S DOUBLE(ワンズダブル)")
        self.assertEqual([item["agency"] for item in agency_plan(self.con)], ["ONE'S DOUBLE"])

    def add_link(self, label, url, hostname):
        self.con.execute(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,created_at,updated_at)"
            " VALUES(11,'official',?,?,?,'t','t')", (label, url, hostname))
        self.con.commit()
        return self.count("SELECT max(id) FROM entity_link")

    def label(self, link_id):
        return self.count("SELECT label FROM entity_link WHERE id=?", link_id)

    def test_her_links_stop_speaking_for_the_rejected_agency(self):
        """官网链接上贴着的那家被驳回：有 AV 时期事务所写它，别的标签不动。"""
        wrong = self.add_link("株式会社Miss", "https://ones-double.com/people/mikami-yua/",
                              "ones-double.com")
        other = self.add_link("FALENO", "https://faleno.jp/top/actress/", "faleno.jp")
        _, done = self.reject("三上悠亚", "株式会社Miss", replacement="ONE'S DOUBLE(ワンズダブル)")
        self.assertEqual(done["链接标签"], 1)
        self.assertEqual((self.label(wrong), self.label(other)), ("ONE'S DOUBLE", "FALENO"))

    def test_without_a_replacement_the_label_falls_back_to_the_generic_one(self):
        wrong = self.add_link("株式会社Miss", "https://ones-double.com/people/mikami-yua/",
                              "ones-double.com")
        self.reject("三上悠亚", "株式会社Miss")
        self.assertEqual(self.label(wrong), "官方网站")


class WriteBackTests(RejectFixture):
    """驳回之后，站上那一格原样再来一遍：两条写回路径都要跳过。"""

    def test_the_installer_does_not_rebuild_a_rejected_agency(self):
        self.reject("三上悠亚", "株式会社Miss")
        found = self.metadata(11)
        found["agency"] = {"name": "株式会社Miss", "checked_at": STAMP}
        self.con.execute("UPDATE entity SET metadata_json=? WHERE id=11",
                         (json.dumps(found, ensure_ascii=False),))
        self.assertEqual([item["agency"] for item in agency_plan(self.con)], [])

    def test_relabelling_links_does_not_write_the_rejected_agency_back(self):
        self.reject("三上悠亚", "株式会社Miss")
        repair_labels(self.con, [{"entity_id": 11, "agency": "株式会社Miss", "verdict": "keep",
                                  "link_id": 0, "link_kind": "official",
                                  "new_label": "", "new_link_kind": "official"}], STAMP)
        self.assertNotIn("agency", self.metadata(11))

    def test_the_label_repair_rewrites_a_label_naming_a_rejected_agency(self):
        """驳回早于链接采集时，标签修复自己认出来：写着已驳回事务所的标签判 auto。"""
        self.reject("三上悠亚", "株式会社Miss")
        self.con.execute(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,created_at,updated_at)"
            " VALUES(11,'official','株式会社Miss','https://ones-double.com/people/mikami-yua/',"
            "'ones-double.com','t','t')")
        found = [row for row in collect_labels(self.con) if row["entity_id"] == 11]
        self.assertEqual([(row["verdict"], row["new_label"], row["agency"]) for row in found],
                         [("auto", "官方网站", "")])


if __name__ == "__main__":
    unittest.main()
