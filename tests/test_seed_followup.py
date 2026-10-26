"""导入实体种子后继：扫描结算时按版本与新实体决定派不派，跑一次把包补进账本并留下复核产物（ADR-0075）。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from peach import seed_followup, seed_pack, seed_validation
from peach.entities import normalize_entity_name
from peach.repository import LedgerDatabase
from peach.review_csv import read_rows
from peach.task_runs import TaskRunStore
from support.ledger import fresh_ledger

VERSION = "2026-09-25"
STAMP = "2026-09-25T00:00:00Z"
PACK = {"format": 1, "version": VERSION, "source": "auto:seed", "counts": {}, "entities": [
    {"kind": "performer", "name": "天川そら", "aliases": [{"alias": "春山心愛", "source": "r18:performer"}],
     "refs": [], "links": [{"kind": "social", "label": "X", "url": "https://x.com/amakawa_sora_"}],
     "agency": {"name": "STARTUP", "source": "minnano-av:所属事務所"}},
    {"kind": "studio", "name": "PRESTIGE PREMIUM", "aliases": [], "refs": [], "links": [],
     "maker": {"name": "Prestige", "source": "review:label-maker-20260923.csv"}},
]}
KEY = seed_followup.followup_key(VERSION, seed_validation.digest(PACK))


class Case(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.database = LedgerDatabase(fresh_ledger(self.root))
        self.store = TaskRunStore(self.database, host="test-host")
        self.pack = self.root / "entities.json"
        self.pack.write_text(seed_pack.dump(PACK), encoding="utf-8")
        patcher = mock.patch.object(seed_pack, "DEFAULT_PACK", self.pack)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.candidates = self.root / "candidates"
        self.candidates.mkdir()
        self.busts = 0
        self.contract = SimpleNamespace(database=self.database, candidate_root=self.candidates,
                                        cache_bust=self._bust)

    def _bust(self) -> None:
        self.busts += 1

    def add(self, kind: str, name: str) -> int:
        with self.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,metadata_json,created_at,updated_at)"
                " VALUES(?,?,?,'{}',?,?)", (kind, name, normalize_entity_name(name), STAMP, STAMP))
            return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])

    def plan(self, since_entity_id: int):
        with self.database.read_connection() as connection:
            return seed_followup.plan(connection, since_entity_id=since_entity_id)

    def finished_run(self, key: str) -> None:
        """任务记录里留一条这个 key 的成功后继：父任务派它，通道认领，跑完。"""
        parent = self.store.start("library-processing", trigger="manual")
        self.store.enqueue_followups(parent.id, [(key, seed_followup.TASK_KEY, "x")])
        child = self.store.claim_followup([seed_followup.TASK_KEY])
        self.store.finish(child.id, "succeeded", summary={})
        self.store.finish(parent.id, "succeeded", summary={})


class PlanTests(Case):
    def test_content_hash_changes_the_followup_key(self):
        first = self.plan(since_entity_id=10 ** 9)[0].key
        self.finished_run(first)
        modified = seed_pack.load(self.pack)
        modified["entities"][0]["links"][0]["url"] = "https://x.com/verified_handle"
        self.pack.write_text(seed_pack.dump(modified), encoding="utf-8")
        second = self.plan(since_entity_id=10 ** 9)[0].key
        self.assertNotEqual(first, second)

    def test_without_a_pack_nothing_is_planned(self):
        with self.database.read_connection() as connection:
            self.assertEqual(seed_followup.plan(connection, since_entity_id=0,
                                                pack_path=self.root / "missing.json"), [])

    def test_a_version_never_landed_is_planned_even_without_new_entities(self):
        found = self.plan(since_entity_id=10 ** 9)
        self.assertEqual([(item.key, item.task_key, item.label) for item in found],
                         [(KEY, "seed-import", f"导入初始资料：{VERSION}")])

    def test_a_landed_version_is_planned_again_only_when_new_entities_appear(self):
        self.finished_run(KEY)
        self.assertEqual(self.plan(since_entity_id=10 ** 9), [], "导过、又没有新实体，不再派")
        her = self.add("performer", "天川そら")
        self.assertEqual(len(self.plan(since_entity_id=her - 1)), 1, "这一轮登记了新女优，再派一次给她补")
        self.add("tag", "标签不算")
        self.assertEqual(self.plan(since_entity_id=her), [], "只有标签这类包里没有的种类，不派")


class RunTests(Case):
    def test_queued_content_change_is_rejected_without_writing(self):
        self.add("performer", "天川そら")
        modified = seed_pack.load(self.pack)
        modified["entities"][0]["links"][0]["url"] = "https://x.com/verified_handle"
        self.pack.write_text(seed_pack.dump(modified), encoding="utf-8")
        with self.assertRaises(ValueError):
            seed_followup.run(self.contract, KEY, None)
        with self.database.read_connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM entity_alias").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM seed_pack_history").fetchone()[0], 0)

    def test_run_lands_the_pack_and_writes_the_review_file(self):
        her, kmp = self.add("performer", "天川そら"), self.add("studio", "K M Produce")
        self.add("agency", "STARTUP")
        label = self.add("studio", "PRESTIGE PREMIUM")
        self.add("studio", "Prestige")
        with self.database.write_transaction() as connection:
            connection.execute("INSERT INTO label_maker(label_id,maker_id,source,confidence,checked_at)"
                               " VALUES(?,?,?,1.0,?)", (label, kmp, "review:user", STAMP))
        summary = seed_followup.run(self.contract, KEY, None)
        self.assertEqual((summary["matched"], summary["aliases"], summary["links"], summary["memberships"],
                          summary["makers"], summary["conflicts"], summary["duplicates"]), (2, 1, 1, 1, 0, 1, 0))
        self.assertEqual(summary["outcome"], "别名 1、链接 1、归属 1、不一致 1")
        self.assertEqual(self.busts, 1, "动了数据库就清一次聚合缓存")
        with self.database.read_connection() as connection:
            self.assertEqual(connection.execute("SELECT maker_id FROM label_maker WHERE label_id=?",
                                                (label,)).fetchone()[0], kmp, "人复核过的片商不被种子改")
            self.assertEqual(connection.execute("SELECT count(*) FROM entity_alias WHERE entity_id=?",
                                                (her,)).fetchone()[0], 1)
        rows = read_rows(self.candidates / seed_followup.REVIEW_FILE)
        self.assertEqual([(row["entity"], row["field"], row["ours"], row["theirs"]) for row in rows],
                         [("PRESTIGE PREMIUM", "maker", "K M Produce", "Prestige")])
        again = seed_followup.run(self.contract, KEY, None)
        self.assertEqual((again["aliases"], again["links"], again["memberships"], again["refreshed"]), (0, 0, 0, 0),
                         "同一包再跑一遍没有东西可补")
        self.assertEqual(self.busts, 1, "没动数据库就不清缓存")
        self.assertTrue(again["outcome"].startswith("不一致 1"), again["outcome"])

    def test_two_local_entities_for_one_pack_item_become_a_duplicate_row(self):
        self.add("performer", "天川そら")
        self.add("performer", "春山心愛")
        summary = seed_followup.run(self.contract, KEY, None)
        self.assertEqual((summary["matched"], summary["duplicates"]), (0, 1))
        rows = read_rows(self.candidates / seed_followup.REVIEW_FILE)
        self.assertEqual([(row["field"], row["entity"], row["theirs"]) for row in rows],
                         [("identity", "天川そら、春山心愛", "天川そら")])


if __name__ == "__main__":
    unittest.main()
