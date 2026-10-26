"""`local_dirty` 的三层判据。

每个用例对应 2026-08-25 在临时库上实测到的一种情形（见 ADR-0020）：只改 mtime、
写入后帧留在 WAL 里、checkpoint 回写、只读打开关闭。全部使用临时数据库。
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.sync import (
    UNKNOWN_WAL, Marker, copy_database, digest_database, local_dirty, plan, read_marker,
    wal_payload_size, write_marker,
)


class _DirtyCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Path(self.temporary.name) / "ledger.db"
        connection = sqlite3.connect(self.db)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE t(x)")
        connection.executemany("INSERT INTO t VALUES(?)", [(i,) for i in range(2000)])
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
        self.marker = write_marker(self.db, Marker(1, "dev", "2026-08-25T00:00:00Z", 0, 0))

    def _touch(self, delta_ns=10**9):
        stat = self.db.stat()
        os.utime(self.db, ns=(stat.st_mtime_ns + delta_ns, stat.st_mtime_ns + delta_ns))

    def _write(self, *, keep_open=False, checkpoint=False):
        """写一条并提交。

        `keep_open=True` 保留连接：帧只在还有连接开着（或进程崩溃）时才留在 WAL 里，
        最后一个连接关闭会 checkpoint 并删掉 `-wal`。真正会丢数据的正是前一种状态——
        源库还有人开着、改动还在 WAL 里，这时去拷主库文件就会漏掉它们。
        """
        connection = sqlite3.connect(self.db)
        connection.execute("INSERT INTO t VALUES(999999)")
        connection.commit()
        if checkpoint:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if keep_open:
            self.addCleanup(connection.close)
            return connection
        connection.close()
        return None


class MarkerTests(_DirtyCase):
    def test_marker_records_digest_and_wal_payload(self):
        self.assertEqual(len(self.marker.digest), 64)
        self.assertEqual(self.marker.wal_size, 0)
        self.assertEqual(self.marker.digest, digest_database(self.db))

    def test_marker_round_trips_through_json(self):
        restored = read_marker(self.db)
        self.assertEqual(restored, self.marker)

    def test_old_markers_without_the_new_fields_still_load(self):
        path = self.db.with_name(self.db.name + ".sync.json")
        path.write_text(
            '{"generation": 1, "device": "dev", "written_at": "x",'
            ' "size": 1, "mtime_ns": 2}', encoding="utf-8")
        restored = read_marker(self.db)
        self.assertEqual(restored.digest, "")
        self.assertEqual(restored.wal_size, UNKNOWN_WAL)


class LocalDirtyTests(_DirtyCase):
    def test_a_freshly_marked_copy_is_clean(self):
        self.assertFalse(local_dirty(self.db, self.marker))

    def test_touching_only_the_mtime_is_not_dirty(self):
        # 内容中性的 checkpoint 或备份工具碰时间戳都会走到这里。旧判据在这一步误报，
        # 于是只读端每次同步都退化成要人工选边。
        self._touch()
        self.assertNotEqual(self.db.stat().st_mtime_ns, self.marker.mtime_ns)
        self.assertFalse(local_dirty(self.db, self.marker))

    def test_a_write_still_sitting_in_the_wal_is_dirty(self):
        # 旧判据在这一步漏报：主库文件一个字节都没动，于是 plan() 判成可以 pull，
        # 而 copy_database 会 unlink 掉目标的 -wal，已提交的事务就此永久消失。
        self._write(keep_open=True)
        self.assertEqual(self.db.stat().st_size, self.marker.size)
        self.assertEqual(self.db.stat().st_mtime_ns, self.marker.mtime_ns)
        self.assertGreater(wal_payload_size(self.db), 0)
        self.assertTrue(local_dirty(self.db, self.marker))

    def test_a_checkpointed_write_is_dirty(self):
        self._write(checkpoint=True)
        self.assertTrue(local_dirty(self.db, self.marker))

    def test_a_write_that_grows_the_file_is_dirty_without_hashing(self):
        connection = sqlite3.connect(self.db)
        connection.executemany("INSERT INTO t VALUES(?)", [(i,) for i in range(50000)])
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
        self.assertNotEqual(self.db.stat().st_size, self.marker.size)
        self.assertTrue(local_dirty(self.db, self.marker))

    def test_read_only_traffic_leaves_the_copy_clean(self):
        for _ in range(3):
            connection = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
            connection.execute("SELECT count(*) FROM t").fetchone()
            connection.close()
        self.assertFalse(local_dirty(self.db, self.marker))

    def test_a_missing_marker_is_dirty(self):
        self.assertTrue(local_dirty(self.db, None))

    def test_a_missing_database_is_not_dirty(self):
        self.assertFalse(local_dirty(self.db.with_name("absent.db"), self.marker))

    def test_an_old_marker_falls_back_to_the_previous_behaviour(self):
        legacy = Marker(1, "dev", "x", self.marker.size, self.marker.mtime_ns)
        self.assertFalse(local_dirty(self.db, legacy))
        self._touch()
        # 没有摘要就没法把「碰过」和「写过」分开，旧标记只能保持旧结论。
        self.assertTrue(local_dirty(self.db, legacy))


class PlanTests(_DirtyCase):
    """判据修好之后，`plan()` 在只读端不再退化成人工选边。"""

    def setUp(self):
        super().setUp()
        self.shared = Path(self.temporary.name) / "shared" / "ledger.db"
        self.shared.parent.mkdir()
        copy_database(self.db, self.shared)
        write_marker(self.shared, Marker(2, "writer", "2026-08-25T01:00:00Z", 0, 0),
                     with_digest=False)

    def test_a_touched_read_only_copy_can_still_pull(self):
        # 修复前：共享更新 + 本地被判 dirty → conflict，只读端每次都要人工选边。
        self._touch()
        decision = plan(self.db, self.shared)
        self.assertEqual(decision.action, "pull")

    def test_a_copy_with_pending_wal_writes_refuses_to_be_overwritten(self):
        # 反过来也要成立：真有未回写的事务时必须挡住，否则 copy_database 会连同
        # 目标的 -wal 一起删掉，已提交的改动就此消失。
        self._write(keep_open=True)
        decision = plan(self.db, self.shared)
        self.assertEqual(decision.action, "conflict")

    def test_shared_markers_skip_the_digest(self):
        self.assertEqual(read_marker(self.shared).digest, "")


class WalPayloadTests(_DirtyCase):
    def test_absent_and_empty_wal_are_equivalent(self):
        wal = Path(f"{self.db}-wal")
        wal.unlink(missing_ok=True)
        self.assertEqual(wal_payload_size(self.db), 0)
        wal.write_bytes(b"")
        self.assertEqual(wal_payload_size(self.db), 0)

    def test_a_stub_shorter_than_a_wal_header_holds_no_frames(self):
        Path(f"{self.db}-wal").write_bytes(b"\x00" * 8)
        self.assertEqual(wal_payload_size(self.db), 0)

    def test_a_real_wal_reports_its_size(self):
        self._write(keep_open=True)
        self.assertGreaterEqual(wal_payload_size(self.db), 32)


class DigestTests(_DirtyCase):
    def test_digest_is_stable_across_reads(self):
        first = digest_database(self.db)
        sqlite3.connect(f"file:{self.db}?mode=ro", uri=True).close()
        self.assertEqual(first, digest_database(self.db))

    def test_digest_of_a_missing_file_is_empty(self):
        self.assertEqual(digest_database(self.db.with_name("absent.db")), "")


if __name__ == "__main__":
    unittest.main()
