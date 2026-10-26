import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.sync import (
    LedgerSync,
    Marker,
    copy_database,
    local_dirty,
    plan,
    read_marker,
    resolve,
    writer_device,
    write_marker,
)


def make_db(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS note(id INTEGER PRIMARY KEY, body TEXT)")
        connection.executemany(
            "INSERT INTO note(body) VALUES(?)", [(f"row-{i}",) for i in range(rows)]
        )
        connection.commit()
    finally:
        connection.close()


def count(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT count(*) FROM note").fetchone()[0]
    finally:
        connection.close()


class MarkerTests(unittest.TestCase):
    def test_marker_round_trips_and_stamps_the_real_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.db"
            make_db(db, 3)
            written = write_marker(db, Marker(4, "mac", "2026-08-20T00:00:00+00:00", 0, 0))
            self.assertEqual((written.size, written.mtime_ns), (db.stat().st_size, db.stat().st_mtime_ns))
            self.assertEqual(read_marker(db), written)

    def test_a_write_after_the_marker_counts_as_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.db"
            make_db(db, 3)
            marker = write_marker(db, Marker(1, "mac", "", 0, 0))
            self.assertFalse(local_dirty(db, marker))
            make_db(db, 1)
            self.assertTrue(local_dirty(db, marker))

    def test_a_copy_with_no_marker_is_treated_as_dirty(self):
        """没有血缘的副本必须当成「动过」，否则会被静默覆盖。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.db"
            make_db(db, 1)
            self.assertTrue(local_dirty(db, None))


class CopyTests(unittest.TestCase):
    def test_backup_api_carries_committed_wal_transactions(self):
        """直接拷 `.db` 会漏掉 `-wal` 里已提交未 checkpoint 的事务。"""
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "a.db", Path(tmp) / "b.db"
            make_db(source, 2)
            live = sqlite3.connect(source)
            try:
                live.execute("PRAGMA journal_mode=WAL")
                live.execute("INSERT INTO note(body) VALUES('in-wal')")
                live.commit()
                copy_database(source, target)
            finally:
                live.close()
            self.assertEqual(count(target), 3)

    def test_copy_overwrites_an_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "a.db", Path(tmp) / "b.db"
            make_db(source, 5)
            make_db(target, 1)
            copy_database(source, target)
            self.assertEqual(count(target), 5)

    def test_copy_removes_stale_target_sidecars(self):
        """原子替换新快照前必须清掉旧 WAL；否则会把两代数据库拼在一起。"""
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "a.db", Path(tmp) / "b.db"
            make_db(source, 5)
            make_db(target, 1)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{target}{suffix}").write_bytes(b"stale")
            copy_database(source, target)
            self.assertEqual(count(target), 5)
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(f"{target}{suffix}").exists())

    def test_copy_accepts_an_immutable_closed_snapshot_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "shared.db", Path(tmp) / "local.db"
            make_db(source, 4)
            copy_database(source, target, source_immutable=True)
            self.assertEqual(count(target), 4)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.local = self.root / "local" / "ledger.db"
        self.shared = self.root / "shared" / "ledger.db"
        self.addCleanup(self._tmp.cleanup)

    def test_same_path_disables_replication(self):
        make_db(self.local, 1)
        self.assertEqual(plan(self.local, self.local).action, "disabled")

    def test_unreachable_share_is_offline_not_an_error(self):
        """硬盘不在时本地照常读写，这是脱盘模式的账本侧。"""
        make_db(self.local, 1)
        self.assertEqual(plan(self.local, self.root / "gone" / "ledger.db").action, "offline")

    def test_missing_share_is_seeded_from_local(self):
        make_db(self.local, 1)
        self.shared.parent.mkdir(parents=True)
        self.assertEqual(plan(self.local, self.shared).action, "push")

    def test_missing_local_pulls(self):
        make_db(self.shared, 1)
        self.local.parent.mkdir(parents=True)
        self.assertEqual(plan(self.local, self.shared).action, "pull")

    def test_newer_share_pulls_when_local_is_clean(self):
        make_db(self.local, 1)
        make_db(self.shared, 2)
        write_marker(self.local, Marker(3, "mac", "", 0, 0))
        write_marker(self.shared, Marker(4, "win", "", 0, 0))
        self.assertEqual(plan(self.local, self.shared).action, "pull")

    def test_both_sides_advanced_is_a_conflict(self):
        """两边都写过就没有安全的合并规则，只能报冲突让人选。"""
        make_db(self.local, 1)
        make_db(self.shared, 2)
        write_marker(self.local, Marker(3, "mac", "", 0, 0))
        write_marker(self.shared, Marker(4, "win", "", 0, 0))
        make_db(self.local, 1)                      # 本地在标记之后又写了
        decision = plan(self.local, self.shared)
        self.assertTrue(decision.conflict)
        self.assertEqual((decision.local_generation, decision.shared_generation), (3, 4))

    def test_missing_lineage_on_either_side_is_a_conflict(self):
        make_db(self.local, 1)
        make_db(self.shared, 2)
        self.assertTrue(plan(self.local, self.shared).conflict)

    def test_local_write_since_the_last_push_is_local_ahead(self):
        make_db(self.local, 1)
        make_db(self.shared, 1)
        write_marker(self.shared, Marker(2, "mac", "", 0, 0))
        write_marker(self.local, Marker(2, "mac", "", 0, 0))
        self.assertEqual(plan(self.local, self.shared).action, "in-sync")
        make_db(self.local, 1)
        self.assertEqual(plan(self.local, self.shared).action, "local-ahead")


class ResolveTests(unittest.TestCase):
    """`resolve` 必须和 `synchronize_now` 得出同一个结论，且一个文件都不碰。

    托盘拿它决定要不要停服务。判错的代价不是报错而是停机：`plan()` 说 `local-ahead`
    但本机不是写入端时，真跑一次只会得到 conflict，那次停机纯属白停。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.local = self.root / "local" / "ledger.db"
        self.shared = self.root / "shared" / "ledger.db"
        self.addCleanup(self._tmp.cleanup)

    def test_a_reader_with_local_writes_resolves_to_conflict_not_a_push(self):
        make_db(self.local, 2)
        make_db(self.shared, 2)
        write_marker(self.local, Marker(7, "win", "", 0, 0))
        write_marker(self.shared, Marker(7, "win", "", 0, 0))
        make_db(self.local, 3)

        self.assertEqual(plan(self.local, self.shared).action, "local-ahead")
        decision = resolve(self.local, self.shared, "mac")
        self.assertEqual(decision.action, "conflict")
        self.assertIn("win", decision.reason)

    def test_the_writer_still_resolves_to_a_push(self):
        make_db(self.local, 2)
        make_db(self.shared, 2)
        write_marker(self.local, Marker(7, "mac", "", 0, 0))
        write_marker(self.shared, Marker(7, "mac", "", 0, 0))
        make_db(self.local, 3)
        self.assertEqual(resolve(self.local, self.shared, "mac").action, "local-ahead")

    def test_resolve_matches_what_synchronize_now_does_and_copies_nothing(self):
        make_db(self.local, 2)
        make_db(self.shared, 5)
        write_marker(self.local, Marker(7, "win", "", 0, 0))
        write_marker(self.shared, Marker(8, "win", "", 0, 0))

        decision = resolve(self.local, self.shared, "mac")
        self.assertEqual(decision.action, "pull")
        self.assertEqual(count(self.local), 2)      # 只读判定，没复制

        applied = LedgerSync(self.local, self.shared, "mac", interval=0).synchronize_now()
        self.assertEqual(applied.action, decision.action)
        self.assertEqual(count(self.local), 5)

    def test_an_unreachable_share_resolves_to_offline(self):
        make_db(self.local, 1)
        self.assertEqual(
            resolve(self.local, self.root / "gone" / "ledger.db", "mac").action, "offline")


class LedgerSyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.local = self.root / "local" / "ledger.db"
        self.shared = self.root / "shared" / "ledger.db"
        self.addCleanup(self._tmp.cleanup)

    def sync(self, device="mac") -> LedgerSync:
        return LedgerSync(self.local, self.shared, device, interval=0)

    def test_first_run_seeds_the_share_and_leaves_both_in_sync(self):
        make_db(self.local, 4)
        self.shared.parent.mkdir(parents=True)
        sync = self.sync()
        self.assertEqual(sync.synchronize_now().action, "push")
        self.assertEqual(sync.status, "writer")
        self.assertEqual(count(self.shared), 4)
        self.assertEqual(plan(self.local, self.shared).action, "in-sync")

    def test_second_machine_pulls_what_the_first_pushed(self):
        make_db(self.local, 4)
        self.shared.parent.mkdir(parents=True)
        self.sync("mac").synchronize_now()

        other_local = self.root / "win" / "ledger.db"
        other_local.parent.mkdir(parents=True)
        other = LedgerSync(other_local, self.shared, "win", interval=0)
        self.assertEqual(other.synchronize_now().action, "pull")
        self.assertEqual(count(other_local), 4)
        self.assertEqual(plan(other_local, self.shared).action, "in-sync")

    def test_generation_advances_on_every_push(self):
        make_db(self.local, 1)
        self.shared.parent.mkdir(parents=True)
        sync = self.sync()
        sync.synchronize_now()
        first = read_marker(self.shared).generation
        make_db(self.local, 1)
        self.assertEqual(sync.push_if_needed(), "pushed")
        self.assertEqual(read_marker(self.shared).generation, first + 1)

    def test_a_push_from_the_other_machine_turns_this_one_read_only(self):
        """回写前必须重新判定，绝不能盲目覆盖别人推上去的那一代。"""
        make_db(self.local, 1)
        self.shared.parent.mkdir(parents=True)
        sync = self.sync()
        sync.synchronize_now()
        self.assertFalse(sync.read_only)

        # 另一台机器推了新的一代，同时本机也有未回写的改动
        write_marker(self.shared, Marker(99, "win", "", 0, 0))
        make_db(self.local, 1)

        self.assertEqual(sync.push_if_needed(), "conflict")
        self.assertTrue(sync.read_only)
        self.assertEqual(count(self.shared), 1)      # 共享副本没有被覆盖

    def test_read_only_message_names_the_writer_and_the_recovery(self):
        """409 文案必须让用户看懂发生了什么、下一步做什么，而不是内部诊断串。"""
        make_db(self.local, 1)
        self.shared.parent.mkdir(parents=True)
        self.sync("win").synchronize_now()
        reader = self.sync("mac")
        reader.observe()

        self.assertTrue(reader.read_only)
        message = reader.read_only_message
        self.assertIn("win", message)                 # 指出写入端是谁
        self.assertIn("由本机接管数据库写入", message)     # 给出本机恢复方式
        self.assertNotIn("两侧一致", message)          # 不得把同步判定原样抛给用户

    def test_conflict_message_points_to_manual_recovery(self):
        make_db(self.local, 1)
        make_db(self.shared, 1)
        write_marker(self.local, Marker(5, "mac", "", 0, 0))
        write_marker(self.shared, Marker(5, "win", "", 0, 0))
        sync = self.sync("mac")
        sync.observe()

        self.assertEqual(sync.status, "conflict")
        self.assertIn("冲突", sync.read_only_message)

    def test_offline_share_keeps_local_writes_and_pushes_on_return(self):
        make_db(self.local, 2)
        self.shared.parent.mkdir(parents=True)
        sync = self.sync()
        sync.synchronize_now()

        detached = LedgerSync(self.local, self.root / "gone" / "ledger.db", "mac", interval=0)
        make_db(self.local, 1)
        self.assertEqual(detached.push_if_needed(), "offline")
        self.assertFalse(detached.read_only)

        self.assertEqual(sync.push_if_needed(), "pushed")
        self.assertEqual(count(self.shared), 3)

    def test_manual_sync_pulls_a_newer_shared_copy(self):
        make_db(self.local, 1)
        make_db(self.shared, 2)
        write_marker(self.local, Marker(3, "mac", "", 0, 0))
        write_marker(self.shared, Marker(4, "win", "", 0, 0))
        decision = self.sync().synchronize_now()
        self.assertEqual(decision.action, "pull")
        self.assertEqual(count(self.local), 2)
        self.assertEqual(plan(self.local, self.shared).action, "in-sync")

    def test_manual_sync_pushes_local_changes(self):
        make_db(self.local, 1)
        self.shared.parent.mkdir(parents=True)
        sync = self.sync()
        sync.synchronize_now()
        make_db(self.local, 1)
        self.assertEqual(sync.synchronize_now().action, "local-ahead")
        self.assertEqual(count(self.shared), 2)

    def test_manual_sync_refuses_a_conflict(self):
        make_db(self.local, 1)
        make_db(self.shared, 2)
        write_marker(self.local, Marker(3, "mac", "", 0, 0))
        write_marker(self.shared, Marker(4, "win", "", 0, 0))
        make_db(self.local, 1)
        sync = self.sync()
        self.assertTrue(sync.synchronize_now().conflict)
        self.assertEqual(count(self.shared), 2)

    def test_observe_never_copies_and_only_owner_can_write(self):
        make_db(self.local, 2)
        make_db(self.shared, 2)
        write_marker(self.local, Marker(7, "mac", "", 0, 0))
        write_marker(self.shared, Marker(7, "mac", "", 0, 0))
        reader = self.sync("win")
        self.assertEqual(reader.observe().action, "in-sync")
        self.assertTrue(reader.read_only)
        self.assertEqual(reader.status, "reader")
        writer = self.sync("mac")
        self.assertEqual(writer.observe().action, "in-sync")
        self.assertFalse(writer.read_only)
        self.assertEqual(writer.status, "writer")
        self.assertEqual(writer_device(self.local, self.shared), "mac")

    def test_reader_cannot_push_a_dirty_local_copy(self):
        make_db(self.local, 2)
        make_db(self.shared, 2)
        write_marker(self.local, Marker(7, "mac", "", 0, 0))
        write_marker(self.shared, Marker(7, "mac", "", 0, 0))
        make_db(self.local, 1)
        decision = self.sync("win").synchronize_now()
        self.assertTrue(decision.conflict)
        self.assertEqual(count(self.shared), 2)

    def test_take_ownership_requires_in_sync_and_advances_the_role_generation(self):
        make_db(self.local, 2)
        make_db(self.shared, 2)
        write_marker(self.local, Marker(7, "mac", "", 0, 0))
        write_marker(self.shared, Marker(7, "mac", "", 0, 0))
        sync = self.sync("win")
        decision = sync.take_ownership()
        self.assertEqual(decision.action, "take-ownership")
        self.assertEqual(read_marker(self.local).generation, 8)
        self.assertEqual(read_marker(self.shared).generation, 8)
        self.assertEqual(writer_device(self.local, self.shared), "win")
        self.assertFalse(sync.read_only)

    def test_the_service_lifecycle_has_no_hook_that_could_push(self):
        """停机不回写靠的是「没有这个钩子」，不是钩子里写了 return None。

        `LedgerSync` 上留一对空的 `start`/`stop` 和一个只转发的 `startup` 的话，
        `api` 的 lifespan 会一进一出地调用它们——读代码的人得跳进 `sync.py`
        才能确认那几行什么都不做，而「服务退出会不会把本地改动推上去」正是这个
        模块最不该靠猜的问题。回写只能由托盘/CLI 的 `ledger-sync` 显式触发。
        """
        for name in ("start", "stop", "startup"):
            self.assertFalse(hasattr(LedgerSync, name),
                             f"LedgerSync.{name} 又回来了：回写必须留在显式命令里")


if __name__ == "__main__":
    unittest.main()
