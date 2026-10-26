import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from peach import ledger_backups


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 5, 12, 0, 0)


def load_script():
    spec = importlib.util.spec_from_file_location(
        "test_prune_ledger_backups", ROOT / "scripts" / "prune_ledger_backups.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stamp(path: Path, when: datetime) -> None:
    os.utime(path, (when.timestamp(), when.timestamp()))


class LedgerBackupRetentionTests(unittest.TestCase):
    def make_ledger(self, root: Path, *, when: datetime = NOW - timedelta(hours=3)) -> Path:
        live = root / "ledger.db"
        connection = sqlite3.connect(live)
        connection.execute("CREATE TABLE asset (id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.close()
        stamp(live, when)
        return live

    def make_backup(self, root: Path, name: str, when: datetime, *, sidecars: bool = False) -> Path:
        backup = root / name
        backup.write_bytes(b"backup")
        stamp(backup, when)
        if sidecars:
            for suffix in ("-wal", "-shm"):
                sidecar = root / f"{name}{suffix}"
                sidecar.write_bytes(b"")
                stamp(sidecar, when)
        return backup

    def test_keeps_recent_young_and_newer_than_live_backups_and_removes_the_rest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            live = self.make_ledger(root)
            old = [
                self.make_backup(root, f"ledger.pre-old-{index}.db",
                                 NOW - timedelta(days=10 - index), sidecars=(index == 0))
                for index in range(4)
            ]
            recent = [
                self.make_backup(root, f"ledger.pre-recent-{index}.db",
                                 NOW - timedelta(days=3, hours=index))
                for index in range(3)
            ]
            young = self.make_backup(root, "ledger.pre-young.db", NOW - timedelta(hours=2))
            future = self.make_backup(root, "ledger.pre-future.db", NOW - timedelta(hours=1))
            stamp(live, NOW - timedelta(hours=1, minutes=30))
            unrelated = root / "ledger.clean-legacy-20260828.json"
            unrelated.write_text("{}", encoding="utf-8")

            decided = ledger_backups.plan(live, keep_recent=5, now=NOW)

            self.assertIsNone(decided.refused)
            self.assertEqual(set(decided.remove), set(old),
                             "最近 5 份、24 小时内和比数据库新的都留，其余清退")
            self.assertEqual(set(decided.keep), set(recent) | {young, future})
            for path in old:
                self.assertTrue(path.exists(), "只算计划时一个字节都不动")

            applied = ledger_backups.prune(live, apply=True, keep_recent=5, now=NOW)

            self.assertEqual(set(applied.remove), set(old))
            self.assertEqual(applied.removable_bytes, 4 * len(b"backup"),
                             "字节数在删除前量好，报告里才不会写成 0")
            for path in old:
                self.assertFalse(path.exists())
            self.assertFalse((root / "ledger.pre-old-0.db-wal").exists(), "副文件一起删")
            self.assertFalse((root / "ledger.pre-old-0.db-shm").exists())
            for path in (*recent, young, future, live, unrelated):
                self.assertTrue(path.exists())

    def test_refuses_to_prune_when_the_live_ledger_is_corrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            live = root / "ledger.db"
            live.write_bytes(b"not a database at all, just bytes")
            backup = self.make_backup(root, "ledger.pre-old.db", NOW - timedelta(days=30))

            decided = ledger_backups.prune(live, apply=True, keep_recent=0, now=NOW)

            self.assertIsNotNone(decided.refused)
            self.assertEqual(decided.remove, ())
            self.assertTrue(backup.exists(), "数据库坏了正是要用备份的时候")

    def test_refuses_when_the_live_ledger_is_missing_and_no_ops_without_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            live = root / "ledger.db"
            self.assertEqual(ledger_backups.prune(live, apply=True, now=NOW),
                             ledger_backups.BackupPlan((), ()))
            backup = self.make_backup(root, "ledger.pre-old.db", NOW - timedelta(days=30))
            decided = ledger_backups.prune(live, apply=True, keep_recent=0, now=NOW)
            self.assertIsNotNone(decided.refused)
            self.assertTrue(backup.exists())

    def test_wal_activity_counts_as_the_live_ledger_being_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            live = self.make_ledger(root, when=NOW - timedelta(days=5))
            wal = root / "ledger.db-wal"
            wal.write_bytes(b"")
            stamp(wal, NOW - timedelta(hours=1))
            backup = self.make_backup(root, "ledger.pre-old.db", NOW - timedelta(days=2))

            decided = ledger_backups.plan(live, keep_recent=0, now=NOW)

            self.assertEqual(decided.remove, (backup,),
                             "主库文件时间没动但 -wal 动过，备份并不比数据库新")

    def test_sidecars_whose_backup_is_gone_get_swept_even_with_nothing_else_to_remove(self):
        """无主的 `-wal`／`-shm` 没人会再列到，只能单独扫。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            live = self.make_ledger(root)
            (root / "ledger.db-wal").write_bytes(b"")
            young = self.make_backup(root, "ledger.pre-young.db",
                                     NOW - timedelta(hours=2), sidecars=True)
            orphans = []
            for suffix in ("-wal", "-shm"):
                orphan = root / f"ledger.pre-gone-20260821.db{suffix}"
                orphan.write_bytes(b"leftover")
                orphans.append(orphan)

            decided = ledger_backups.plan(live, now=NOW)

            self.assertEqual(decided.remove, (), "该留的备份一份都没动")
            self.assertEqual(set(decided.orphans), set(orphans))
            self.assertEqual(decided.removable_bytes, 2 * len(b"leftover"))
            for path in orphans:
                self.assertTrue(path.exists(), "只算计划时一个字节都不动")

            ledger_backups.prune(live, apply=True, now=NOW)

            for path in orphans:
                self.assertFalse(path.exists())
            for path in (live, root / "ledger.db-wal", young,
                         root / "ledger.pre-young.db-wal", root / "ledger.pre-young.db-shm"):
                self.assertTrue(path.exists(), "主文件还在的副文件跟着主文件走")

    def test_orphan_sidecars_survive_a_refusal_like_every_other_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            live = root / "ledger.db"
            live.write_bytes(b"not a database at all, just bytes")
            orphan = root / "ledger.pre-gone-20260821.db-shm"
            orphan.write_bytes(b"leftover")

            decided = ledger_backups.prune(live, apply=True, keep_recent=0, now=NOW)

            self.assertIsNotNone(decided.refused)
            self.assertTrue(orphan.exists(), "数据库坏了就什么都别动，先看清楚再说")

    def test_script_reports_the_plan_as_json_and_only_deletes_with_apply(self):
        script = load_script()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            live = self.make_ledger(root)
            old = self.make_backup(root, "ledger.pre-old.db", NOW - timedelta(days=30))
            import contextlib
            import io

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = script.main(["--db", str(live), "--keep", "0", "--json"])
            self.assertEqual(code, 0)
            report = json.loads(buffer.getvalue())
            self.assertFalse(report["applied"])
            self.assertEqual(report["remove"], ["ledger.pre-old.db"])
            self.assertEqual(report["removable_bytes"], len(b"backup"))
            self.assertTrue(old.exists(), "缺省是 dry-run")

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = script.main(["--db", str(live), "--keep", "0", "--apply"])
            self.assertEqual(code, 0)
            self.assertIn("已删除 1 份", buffer.getvalue())
            self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
