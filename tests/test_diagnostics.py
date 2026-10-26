"""诊断使用临时账本和数据目录，不执行自动修复。"""
import io
import json
import sqlite3
import socket
import errno
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from peach import cli, diagnostics, health
from peach.config import PeachSettings
from peach.ffmpeg import BinaryChoice
from peach.migrations import upgrade
from peach.settings_file import PeachConfig, ServerSettings


ROOT = Path(__file__).resolve().parents[1]
EMPTY_MOUNTS = {"state": "ok", "sources": [], "warnings": []}


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db = self.root / "ledger.db"
        self.page = self.root / "index.html"
        self.page.write_text("<!doctype html><title>Peach</title>", encoding="utf-8")
        self.config = PeachConfig(self.root, self.root / "config.toml", data_root_found=True,
                                  server=ServerSettings(host="127.0.0.1", port=0))
        self.settings = PeachSettings(db_path=self.db, page_path=self.page,
                                      host="127.0.0.1", port=0, configured=True,
                                      access_path=self.root / "access.json", token="private-token",
                                      ffmpeg_root=self.root / "tools")

    def inspect(self):
        return diagnostics.report(self.settings, EMPTY_MOUNTS, config=self.config)

    def test_missing_empty_and_corrupt_databases_are_distinct_and_never_created(self):
        self.assertEqual(health.inspect_database(self.db)["database"], "missing")
        self.assertFalse(self.db.exists())
        sqlite3.connect(self.db).close()
        self.assertEqual(health.inspect_database(self.db)["database"], "empty")
        self.db.write_bytes(b"invalid database")
        self.assertEqual(health.inspect_database(self.db)["database"], "unavailable")

    def test_schema_checks_pending_checksum_and_unknown_versions_without_migration(self):
        upgrade(self.db, ROOT / "migrations")
        self.assertEqual(health.inspect_database(self.db)["schema"], "current")
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("DELETE FROM schema_migration WHERE version='0043'")
        self.assertEqual(health.inspect_database(self.db)["pending"], ["0043"])
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("UPDATE schema_migration SET checksum='incorrect' WHERE version='0001'")
        self.assertEqual(health.inspect_database(self.db)["mismatched"], ["0001"])
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO schema_migration(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                               ("private-token", "secret", "secret", "2026-10-03"))
        inspected = health.inspect_database(self.db)
        self.assertEqual(inspected["unknown_versions"], 1)
        self.assertNotIn("private-token", json.dumps(inspected))
        before = self.db.read_bytes()
        result = self.inspect()
        self.assertEqual(result["checks"]["schema"]["status"], "failed")
        self.assertEqual(self.db.read_bytes(), before)

    def test_a_malformed_configuration_is_reported_without_its_sensitive_text(self):
        self.config.path.write_text('secret = "private-token\n', encoding="utf-8")
        result = self.inspect()
        self.assertEqual(result["checks"]["configured"]["status"], "failed")
        self.assertNotIn("private-token", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_writable_probe_cleans_up_and_permission_failure_has_a_specific_reason(self):
        before = set(self.root.iterdir())
        self.assertEqual(diagnostics.writable_root(self.root)["status"], "ok")
        self.assertEqual(set(self.root.iterdir()), before)
        with patch.object(diagnostics.tempfile, "TemporaryFile", side_effect=PermissionError):
            self.assertEqual(diagnostics.writable_root(self.root)["reason"], "没有权限写入数据目录")

    def test_security_reports_anonymous_lan_and_password_without_secrets(self):
        from peach.access import save
        save(self.settings.access_path, "")
        settings = replace(self.settings, host="0.0.0.0")
        self.assertEqual(diagnostics.security(settings)["status"], "warning")
        save(settings.access_path, "my-private-password")
        result = diagnostics.security(settings)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("my-private-password", json.dumps(result))
        self.assertNotIn("hash", result["details"])

    def test_corrupt_access_policy_and_missing_tools_are_explained(self):
        self.settings.access_path.write_text("broken", encoding="utf-8")
        self.assertEqual(diagnostics.security(self.settings)["status"], "failed")
        with patch.object(diagnostics.FFmpegResolver, "ffmpeg", return_value=None), \
             patch.object(diagnostics.FFmpegResolver, "ffprobe", return_value=None), \
             patch.object(diagnostics.shutil, "which", return_value=None):
            result = diagnostics.tools(self.settings)
        self.assertEqual(result["status"], "warning")
        self.assertTrue(all(not binary["available"] for binary in result["details"].values()))

    def test_port_reports_occupied_without_guessing_the_owner(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            result = diagnostics.port_status("127.0.0.1", listener.getsockname()[1])
        self.assertEqual(result["details"]["state"], "in_use")
        with patch.object(diagnostics.socket, "socket", side_effect=PermissionError(errno.EACCES, "secret")):
            result = diagnostics.port_status("127.0.0.1", 443)
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("secret", json.dumps(result))

    def test_doctor_uses_source_tray_tls_and_standalone_configuration_endpoints(self):
        self.assertEqual(diagnostics.doctor_endpoint(self.config), ("127.0.0.1", 0))
        tls = self.config.directory("secrets") / "tls"
        tls.mkdir(parents=True)
        for name in ("peach-local-ca.crt", "peach.crt", "peach.key"):
            (tls / name).write_text("fixture", encoding="utf-8")
        with patch.object(diagnostics.distribution, "standalone", return_value=False), \
             patch.object(diagnostics.sys, "platform", "win32"), \
             patch.dict(diagnostics.os.environ, {"PEACH_LAN_ADDRESS": "192.0.2.10"}):
            self.assertEqual(diagnostics.doctor_endpoint(self.config), ("192.0.2.10", 443))
        with patch.object(diagnostics.distribution, "standalone", return_value=False), \
             patch.object(diagnostics.sys, "platform", "darwin"):
            self.assertEqual(diagnostics.doctor_endpoint(self.config), ("0.0.0.0", 8443))
        with patch.object(diagnostics.distribution, "standalone", return_value=True):
            self.assertEqual(diagnostics.doctor_endpoint(self.config), ("127.0.0.1", 0))

    def test_mount_unknown_and_failure_do_not_claim_online_or_break_service_readiness(self):
        upgrade(self.db, ROOT / "migrations")
        for state, expected in (("checking", "unknown"), ("degraded", "warning")):
            snapshot = {"state": state, "sources": [{"state": "timeout", "online": None}],
                        "warnings": ["本地磁盘：目录不存在"] if state == "degraded" else []}
            self.assertEqual(diagnostics.media_mounts(snapshot)["status"], expected)
        self.assertTrue(health.readiness(self.settings)["ready"])

    def test_doctor_mount_probe_returns_unknown_at_the_deadline_without_waiting_for_io(self):
        from peach.mount_reachability import MountReachability, MountRoot
        release = threading.Event()
        entered = threading.Event()

        def stalled(_path):
            entered.set()
            release.wait(5)
            return "ok"

        monitor = MountReachability([MountRoot("local", "本地磁盘", "R:/private", self.root)], probe=stalled)
        try:
            with patch.object(diagnostics, "MountReachability", return_value=monitor), \
                 patch.object(diagnostics.time, "monotonic", side_effect=[0, 100]):
                snapshot = diagnostics.probe_mounts([])
            self.assertTrue(entered.wait(1))
            self.assertEqual(diagnostics.media_mounts(snapshot)["status"], "unknown")
            self.assertIsNone(snapshot["sources"][0]["online"])
        finally:
            release.set()
            monitor.stop()

    def test_recent_failure_excludes_media_paths_tokens_and_raw_errors(self):
        upgrade(self.db, ROOT / "migrations")
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO task_run(task_key,trigger,status,mutex_key,pid,host,error,finished_at) "
                               "VALUES('private-task','manual','failed','x',1,'private-host',?,?)",
                               ("R:/secret/video.mp4?cookie=private-token", "2026-10-03T03:00:00Z"))
        result = diagnostics.recent_failure(self.db)
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["target"], "/tasks")
        text = json.dumps(result)
        for private in ("R:/secret", "cookie", "private-token", "private-host", "private-task"):
            self.assertNotIn(private, text)

    def test_public_components_only_include_status_and_reason(self):
        binary = BinaryChoice(self.root / "tools/ffmpeg", "peach-managed")
        with patch.object(diagnostics.FFmpegResolver, "ffmpeg", return_value=binary), \
             patch.object(diagnostics.FFmpegResolver, "ffprobe", return_value=binary), \
             patch.object(diagnostics.settings_file, "active", return_value=self.config), \
             patch.object(diagnostics, 'binary_diagnostics') as inspect:
            result = diagnostics.health_components(self.settings, EMPTY_MOUNTS)
        inspect.assert_not_called()
        self.assertEqual(set(result), {"database", "schema", "configured", "ffmpeg", "media_mounts", "security"})
        self.assertTrue(all(set(value) == {"status", "reason"} for value in result.values()))
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_doctor_json_uses_the_shared_report_and_packaged_dispatch_finds_it(self):
        result = {"version": "test", "status": "warning", "checks": {}}
        output = io.StringIO()
        with patch.object(diagnostics, "report", return_value=result), \
             patch.object(diagnostics, "probe_mounts", return_value=EMPTY_MOUNTS), \
             patch.object(diagnostics.settings_file, "active", return_value=self.config), redirect_stdout(output):
            code = cli.main(["doctor", "--json", "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), result)
        self.assertIn("doctor", cli.subcommands())

    def test_doctor_text_reports_failure_and_exits_nonzero(self):
        result = {"version": "test", "status": "failed", "checks": {
            "database": diagnostics.item("数据库", "failed", "数据库不存在", action="检查本机数据库与备份")}}
        output = io.StringIO()
        with patch.object(diagnostics, "report", return_value=result), \
             patch.object(diagnostics, "probe_mounts", return_value=EMPTY_MOUNTS), \
             patch.object(diagnostics.settings_file, "active", return_value=self.config), redirect_stdout(output):
            code = cli.main(["doctor", "--db", str(self.db)])
        self.assertEqual(code, 1)
        self.assertIn("数据库 [failed] 数据库不存在", output.getvalue())
        self.assertIn("检查本机数据库与备份", output.getvalue())
