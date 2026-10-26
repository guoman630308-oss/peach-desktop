"""数据管理页的孤儿记录：列出已消失的作品、接到某个文件、彻底删除（ADR-0087 决策三）。

账本路径都落在临时目录里：彻底删除那一支会顺手清空文件夹，路径指向真实的 `R:\\media`
就是在动这台机器上的目录。
"""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from peach import record_rehome, web_batch
from peach import web_contract as rm_web
from peach.web_orphan_records import q_orphan_records, w_orphan_attach

from support.ledger import fresh_ledger


class OrphanRecordsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = fresh_ledger(self.root)
        gone, here = self.root / "gone", self.root / "here"
        rows = [
            # id, path, code, duration, disposal, feedback_at
            (7, gone / "ABC-123.mp4", "ABC-123", 3600, "vanished", 200),
            (9, here / "ABC-123.mkv", None, 3590, None, None),
            (10, here / "abc-123.mp4", None, 1800, None, None),
            (11, here / "XYZ-001.mp4", "XYZ-001", 3600, None, None),
            (12, gone / "海边.mp4", None, 600, "vanished", 100),
            (13, here / "海边.mkv", None, 601, None, None),
            # 回收站里的不当候选。
            (14, here / "ABC-123 old.mp4", None, 3600, "trash", 50),
        ]
        with closing(sqlite3.connect(self.db)) as connection:
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,size,code,duration,disposal,feedback_at) "
                "VALUES(?,'local',?,?,'video',10,?,?,?,?)",
                [(row[0], str(row[1]), row[1].name, *row[2:]) for row in rows])
            connection.execute("INSERT INTO asset_preference(profile_id,asset_id,liked,reason,updated_at) "
                               "VALUES('default',7,1,'','2026-01-01')")
            connection.execute("INSERT INTO watch_queue(profile_id,asset_id,added_at) VALUES('default',12,'2026-01-01')")
            connection.executemany(
                "INSERT INTO activity_event(asset_id,profile_id,kind,occurred_at,source) "
                "VALUES(7,'default','play',?,'web')", [("2026-01-01",), ("2026-01-02",)])
            connection.commit()
        self.contract = rm_web.WebContract(Path(self.db))

    def owners(self, table):
        with closing(sqlite3.connect(self.db)) as connection:
            return sorted({row[0] for row in connection.execute(f"SELECT asset_id FROM {table}")})

    def test_the_list_shows_each_vanished_row_with_its_records_and_candidates(self):
        listed = q_orphan_records(self.contract)
        self.assertEqual(listed["total"], 2)
        first, second = listed["items"]
        self.assertEqual((first["id"], second["id"]), (7, 12), "新标的在前")
        self.assertEqual(first["records"], {"asset_preference": 1, "activity_event": 2})
        self.assertEqual([item["id"] for item in first["candidates"]], [9, 10],
                         "番号对得上的在库文件，时长接近的在前；回收站里的不算")
        self.assertEqual(second["records"], {"watch_queue": 1})
        self.assertEqual([item["id"] for item in second["candidates"]], [13], "无番号按文件名主干对")

    def test_attaching_moves_the_records_and_records_a_revertible_batch(self):
        result = w_orphan_attach(self.contract, {"id": 7, "target": 10})
        self.assertEqual(result["batch"], f"{record_rehome.MANUAL_SOURCE}@1")
        self.assertEqual(self.owners("asset_preference"), [10])
        self.assertEqual(self.owners("activity_event"), [10])
        self.assertEqual([item["id"] for item in q_orphan_records(self.contract)["items"]], [12])
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM asset WHERE id=7").fetchone()[0], 0)
            self.assertEqual(len(record_rehome.planned_revert(connection, record_rehome.MANUAL_SOURCE)), 1)

    def test_attaching_refuses_a_target_outside_the_library(self):
        with self.assertRaisesRegex(ValueError, "在库"):
            w_orphan_attach(self.contract, {"id": 7, "target": 14})
        self.assertEqual(self.owners("asset_preference"), [7])

    def test_deleting_a_vanished_row_drops_it_and_its_records_but_keeps_a_file_that_came_back(self):
        back = self.root / "gone" / "海边.mp4"
        back.parent.mkdir()
        back.write_bytes(b"x")
        result = web_batch.w_batch(self.contract, {"ids": [7, 12], "operation": "delete"})
        self.assertEqual(result["purged"], 1)
        self.assertEqual([item["id"] for item in result["blocked"]], [12], "文件又回到盘上，只删数据库行的口径不删它")
        self.assertTrue(back.exists())
        self.assertEqual(self.owners("asset_preference"), [])
        self.assertEqual(self.owners("activity_event"), [])
        self.assertEqual(self.owners("watch_queue"), [12])


if __name__ == "__main__":
    unittest.main()
