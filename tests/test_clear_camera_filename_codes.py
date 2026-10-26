"""`scripts/clear_camera_filename_codes.py`：只清相机文件名派生的伪番号，且默认不写。"""
from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

from peach.field_owners import write_owned_fields
from support.ledger import fresh_ledger

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "test_clear_camera_filename_codes", ROOT / "scripts" / "clear_camera_filename_codes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROWS = [
    # (name, medium, code, owner)：前两行是要清的旧值；后面几行各占一个「不能碰」的理由。
    ("video_2022-06-08_11-40-56.mp4", "video", "VIDEO-2022", None),
    ("IMG_1734 (2).mp4", "video", "IMG-1734", None),
    ("IMG_2001.mp4", "video", "IMG-2001", "user:me"),
    ("IMG_0005.jpg", "image", "IMG-0005", None),
    ("STP-26232.mp4", "video", "STP-26232", None),
]


class ClearCameraFilenameCodesTests(unittest.TestCase):
    def setUp(self):
        self.script = load_script()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = fresh_ledger(self.root)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            for index, (name, medium, code, owner) in enumerate(ROWS, start=1):
                connection.execute(
                    "INSERT INTO asset(id, location, path, name, medium, code) VALUES (?,?,?,?,?,?)",
                    (index, "local", rf"R:\media\{name}", name, medium, code))
                if owner:
                    write_owned_fields(connection, [index], {"code": code}, owner)

    def codes(self):
        with closing(sqlite3.connect(self.db)) as connection:
            return [row[0] for row in connection.execute("SELECT code FROM asset ORDER BY id")]

    def run_script(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.script.main(["--db", str(self.db), "--json", *argv])
        return code, json.loads(out.getvalue())

    def test_dry_run_lists_the_plan_and_writes_nothing(self):
        code, report = self.run_script()
        self.assertEqual(code, 0)
        self.assertFalse(report["applied"])
        self.assertEqual([item["id"] for item in report["clear"]], [1, 2])
        self.assertEqual([(item["id"], item["owner"]) for item in report["protected"]], [(3, "user:me")])
        self.assertEqual(self.codes(), [code for _, _, code, _ in ROWS])

    def test_apply_needs_a_backup_then_clears_only_the_unowned_camera_codes(self):
        with self.assertRaises(SystemExit):
            self.run_script("--apply")
        backup = self.root / "ledger.pre-clear.db"
        code, report = self.run_script("--apply", "--backup", str(backup))
        self.assertEqual(code, 0)
        self.assertTrue(backup.is_file())
        self.assertEqual(report["written_fields"], ["code"])
        self.assertEqual((report["before"]["camera_codes"], report["after"]["camera_codes"]), (3, 1))
        self.assertEqual((report["integrity"], report["foreign_key_violations"]), ("ok", 0))
        # 图片行不在范围内：脚本只管被当成番号去问外部来源的视频。
        self.assertEqual(self.codes(), [None, None, "IMG-2001", "IMG-0005", "STP-26232"])
        with closing(sqlite3.connect(self.db)) as connection:
            owners = [row[0] for row in connection.execute(
                "SELECT json_extract(field_owners,'$.code') FROM asset WHERE id IN (1,2)")]
        self.assertEqual(owners, [self.script.OWNER] * 2)
        code, report = self.run_script()
        self.assertEqual(report["clear"], [], "可重复运行：第二遍没有可清的行")


if __name__ == "__main__":
    unittest.main()
