"""异常取证与分享报告只使用临时目录；进程级钩子在子进程里验证。"""
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from peach import error_reporting, support_bundle

ROOT = Path(__file__).resolve().parents[1]


class ErrorReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.logs = self.root / "logs"
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(os.environ, {"PEACH_DATA_ROOT": str(self.root)}).start()
        mock.patch.object(error_reporting, "_log_dir", self.logs).start()

    def probe(self, code):
        return subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                              env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_processing_export_keeps_recent_bounded_evidence_and_hides_media_names(self):
        from peach.settings_file import PeachConfig
        config = PeachConfig(self.root, self.root / 'config.toml', present=True)
        state = config.directory('state')
        state.mkdir(parents=True)
        row = {'asset_id': 23, 'title': 'private-movie.mp4', 'path': r'D:\private\movie.mp4',
               'diagnostics': {'reason': 'timeout', 'stderr': 'token=private-token',
                               'timestamp_seconds': 12, 'timeout_seconds': 45}}
        for index in range(10):
            path = state / f'library-processing-{index}.issues.jsonl'
            path.write_text(json.dumps(row) + '\n', encoding='utf-8')
            os.utime(path, (100 + index, 100 + index))
        (state / 'private-credentials.json').write_text('private-credentials', encoding='utf-8')
        (state / 'library-processing.json').write_text(json.dumps({
            'status': 'failed', 'issue_preview': [row], 'current_asset_name': 'private-movie.mp4'}),
            encoding='utf-8')
        with mock.patch.object(error_reporting, 'log_directories', return_value=[]), \
                zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes(config=config))) as archive:
            names = [name for name in archive.namelist() if name.startswith('processing/')]
            self.assertEqual(len(names), 9)
            self.assertNotIn('processing/library-processing-0.issues.jsonl', names)
            exported = json.loads(archive.read('processing/library-processing-9.issues.jsonl'))
            self.assertEqual(exported['asset_id'], 23)
            self.assertEqual(exported['diagnostics']['timestamp_seconds'], 12)
            contents = '\n'.join(archive.read(name).decode() for name in archive.namelist())
        for private in ('private-movie', 'private-token', 'private-credentials', r'D:\private'):
            self.assertNotIn(private, contents)

    def test_processing_export_omits_partial_rows_and_limits_large_files(self):
        from peach.settings_file import PeachConfig
        config = PeachConfig(self.root, self.root / 'config.toml', present=True)
        state = config.directory('state')
        state.mkdir(parents=True)
        path = state / 'library-processing-large.issues.jsonl'
        row = json.dumps({'asset_id': 23, 'message': 'error' * 100}) + '\n'
        path.write_text(row * 400 + '{"partial":', encoding='utf-8')
        with mock.patch.object(error_reporting, 'log_directories', return_value=[]), \
                zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes(config=config))) as archive:
            text = archive.read('processing/' + path.name).decode()
            report = json.loads(archive.read('report.json'))
        self.assertLessEqual(len(text.encode()), support_bundle.TAIL_BYTES)
        self.assertTrue(text.splitlines())
        self.assertTrue(all(json.loads(line)['asset_id'] == 23 for line in text.splitlines()))
        self.assertTrue(next(row for row in report['logs'] if row['name'] == path.name)['truncated'])

    def test_bundle_contains_update_tunnel_rotated_and_startup_evidence_with_invalid_config(self):
        from peach import settings_file
        self.logs.mkdir()
        for name in ('tray-restart.out.log', 'tray-scan.out.log', 'windows-source-sync.log',
                     'cloudflare-tunnel.log', 'tray.until-20261001.log'):
            (self.logs / name).write_text('failure --token private-cli-token\n', encoding='utf-8')
        state = self.root / 'state'
        state.mkdir()
        (state / 'runtime-prepare.json').write_text(json.dumps({
            'ok': False, 'steps': [{'name': 'dependencies', 'message': 'failed --password private-password'}]},
            indent=2), encoding='utf-8')
        migration = state / 'update-backups' / 'fixture' / 'migration.log'
        migration.parent.mkdir(parents=True)
        migration.write_text('migration failed\n', encoding='utf-8')
        with mock.patch.object(settings_file, 'load_config', side_effect=ValueError('invalid config')), \
                zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes())) as archive:
            names = archive.namelist()
            self.assertTrue(any('tray.until-' in name for name in names))
            for name in ('tray-restart.out.log', 'tray-scan.out.log', 'windows-source-sync.log', 'cloudflare-tunnel.log'):
                self.assertTrue(any(item.endswith('/' + name) for item in names), name)
            startup = json.loads(archive.read('processing/runtime-prepare.json'))
            self.assertFalse(startup['ok'])
            self.assertIn('updates/update-backups/fixture/migration.log', names)
            contents = '\n'.join(archive.read(name).decode() for name in names)
        for private in ('private-cli-token', 'private-password'):
            self.assertNotIn(private, contents)

    def test_one_unreadable_error_log_does_not_hide_other_logs(self):
        self.logs.mkdir()
        (self.logs / 'tray.log').write_text('service failed\n', encoding='utf-8')
        (self.logs / 'errors-unreadable.log').write_text('error', encoding='utf-8')
        original = Path.stat
        def stat(path, *args, **kwargs):
            if path.name == 'errors-unreadable.log':
                raise PermissionError('not readable')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'stat', stat), \
                zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes())) as archive:
            self.assertTrue(any(name.endswith('/tray.log') for name in archive.namelist()))
            report = json.loads(archive.read('report.json'))
        self.assertTrue(any(row['name'] == 'errors-unreadable.log' and row['status'] == 'PermissionError'
                            for row in report['logs']))

    def records(self):
        return [json.loads(line) for path in self.logs.glob("errors-*.log")
                for line in path.read_text(encoding="utf-8").splitlines()]

    def test_uncaught_import_and_thread_failures_are_saved_without_a_console(self):
        result = self.probe("import sys, threading; from peach import error_reporting as e; "
                            "e.install(); sys.stderr=None; "
                            "t=threading.Thread(target=lambda: 1/0, name='worker'); t.start(); t.join(); "
                            "import peach_missing_startup_dependency")
        self.assertNotEqual(result.returncode, 0)
        events = self.records()
        stages = {row["stage"] for row in events}
        self.assertTrue({"entry", "unhandled-main", "unhandled-thread"} <= stages)
        failure = next(row for row in events if row["stage"] == "unhandled-main")
        self.assertEqual(failure["exception"][0]["type"], "ModuleNotFoundError")
        self.assertTrue(failure["frames"])

    def test_native_traceback_handler_is_enabled_and_writes_to_a_file(self):
        result = self.probe("import faulthandler; from peach import error_reporting as e; "
                            "e.install(); assert faulthandler.is_enabled(); "
                            "faulthandler.dump_traceback(file=e._fault_stream)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any('line' in path.read_text(encoding="utf-8") for path in self.logs.glob("crash-*.log")))

    def test_exception_records_keep_codes_and_frames_without_source_lines_or_credentials(self):
        try:
            raise OSError(5, 'password=private-password https://example.com/?token=private-token')
        except OSError as error:
            error.peach_operation = "OpenProcess(desktop)"
            path = error_reporting.event("tray-handoff", error)
        row = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(row["exception"][0]["errno"], 5)
        self.assertEqual(row["exception"][0]["operation"], "OpenProcess(desktop)")
        self.assertTrue(row["frames"])
        self.assertNotIn("private-password", path.read_text(encoding="utf-8"))
        self.assertNotIn("private-token", path.read_text(encoding="utf-8"))

    def test_unwritable_log_directory_uses_the_temporary_fallback(self):
        blocked = self.root / "blocked"
        blocked.write_text("file", encoding="utf-8")
        fallback = self.root / "fallback"
        with mock.patch.object(error_reporting, "log_directories", return_value=[blocked / "logs", fallback]):
            path = error_reporting.event("entry")
        self.assertEqual(path.parent, fallback)
        self.assertTrue(path.is_file())

    def test_shared_job_failure_context_is_preserved_in_the_redacted_report(self):
        error = OSError("desktop process creation denied")
        error.peach_operation = "CreateProcessW(breakaway)"
        error.peach_context = {
            "caller": {"pid": 123, "in_job": True, "immediate_limit_flags": "0x2000",
                       "immediate_limit_names": ["KILL_ON_JOB_CLOSE"]},
            "desktop": {"pid": 456, "in_job": True},
            "job_chain": {"status": "unavailable"}, "token": "private-job-token"}
        error_reporting.event("tray-handoff", error)
        with zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes())) as archive:
            name = next(name for name in archive.namelist() if 'errors-' in name)
            payload = archive.read(name).decode()
        context = json.loads(payload)["exception"][0]["context"]
        self.assertEqual(context["caller"], error.peach_context["caller"])
        self.assertEqual(context["desktop"], {"pid": 456, "in_job": True})
        self.assertEqual(context["job_chain"]["status"], "unavailable")
        self.assertNotIn("private-job-token", payload)

    def test_bundle_is_available_with_corrupt_configuration_and_excludes_private_files(self):
        (self.root / "config.toml").write_text('secret = "unclosed-private-config', encoding="utf-8")
        (self.root / "ledger.db").write_text("private-database", encoding="utf-8")
        self.logs.mkdir()
        (self.logs / "tray.log").write_text(
            'Cookie: session=private-session; other=private-cookie\n'
            'Authorization: Bearer private-auth\n'
            'password=private-password\n'
            'https://private.example/download?key=private-key\n'
            'C:\\Users\\private-user\\private.mp4\n'
            '/example/private-user/movie.mp4\n203.0.113.20\n'
            'user@private.example\n', encoding="utf-8")
        (self.logs / "unapproved.log").write_text("private-unapproved", encoding="utf-8")
        error_reporting.event("entry")
        with zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes())) as archive:
            report = json.loads(archive.read("report.json"))
            contents = "\n".join(archive.read(name).decode() for name in archive.namelist())
            self.assertEqual(report["configuration"]["status"], "unavailable")
            self.assertTrue(all(name == "report.json" or name.startswith("logs/") for name in archive.namelist()))
            self.assertNotIn("unapproved.log", " ".join(archive.namelist()))
        for secret in ("private-session", "private-cookie", "private-auth", "private-password", "private-key",
                       "private.example", "private-user", "203.0.113.20", "private-database", "unclosed-private-config"):
            self.assertNotIn(secret, contents)

    def test_report_tail_is_bounded_and_records_the_truncation(self):
        self.logs.mkdir()
        (self.logs / "tray.log").write_bytes(b"discard-this\n" * 20_000 + b"final-error\n")
        with zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes())) as archive:
            name = next(name for name in archive.namelist() if name.endswith("tray.log"))
            self.assertLessEqual(len(archive.read(name)), support_bundle.TAIL_BYTES)
            self.assertIn(b"final-error", archive.read(name))
            report = json.loads(archive.read("report.json"))
            self.assertTrue(next(row for row in report["logs"] if row["name"] == "tray.log")["truncated"])

    def test_prefixed_cookie_headers_and_arbitrary_absolute_paths_are_redacted(self):
        self.logs.mkdir()
        (self.logs / "tray.log").write_text(
            '2026-10-15 ERROR Cookie: SEID=private-a; CID=private-b; UID=private-c\n'
            '/data/private-user/movie.mp4\n/srv/private-user/movie.mp4\n', encoding="utf-8")
        with zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes())) as archive:
            content = "\n".join(archive.read(name).decode() for name in archive.namelist())
        for secret in ("private-a", "private-b", "private-c", "private-user"):
            self.assertNotIn(secret, content)

    def test_warning_logs_keep_their_console_output_and_persistent_record(self):
        result = self.probe("import logging; from peach import error_reporting as e; "
                            "e.install(); logging.warning('warning example')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning example", result.stderr)
        self.assertTrue(any(row.get("level") == "WARNING" for row in self.records()))

    def test_an_unusable_temporary_directory_does_not_disable_the_data_log_directory(self):
        with mock.patch.object(error_reporting.tempfile, "gettempdir", side_effect=FileNotFoundError("No usable temporary directory")):
            path = error_reporting.event("entry")
            report = support_bundle.save_bundle()
        self.assertEqual(path.parent, self.logs)
        self.assertTrue(zipfile.is_zipfile(report))

    def test_structured_log_export_preserves_frames_and_redacts_secret_fields(self):
        self.logs.mkdir()
        row = {"stage": "logging", "message": "Cookie: SEID=private-cookie; CID=private-cid",
               "exception": [{"type": "OSError", "winerror": 5}],
               "frames": [{"file": "tray.py", "line": 5}], "token": "private-token"}
        (self.logs / "errors-123.log").write_text(json.dumps(row) + "\n", encoding="utf-8")
        with zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes())) as archive:
            name = next(name for name in archive.namelist() if name.endswith("errors-123.log"))
            content = archive.read(name).decode()
            exported = json.loads(content)
        self.assertEqual(exported["frames"], row["frames"])
        self.assertEqual(exported["exception"][0]["winerror"], 5)
        for secret in ("private-cookie", "private-cid", "private-token"):
            self.assertNotIn(secret, content)

    def test_failure_dialog_points_to_an_existing_report_and_cli_needs_no_service(self):
        message = error_reporting.failure_details("tray-handoff", OSError("desktop unavailable"))
        self.assertIn("诊断报告", message)
        self.assertEqual(len(list(self.logs.glob("peach-support-*.zip"))), 1)
        destination = self.root / "share.zip"
        result = self.probe(f"from peach.support_bundle import main; raise SystemExit(main([{str(destination)!r}]))")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(zipfile.is_zipfile(destination))
        again = self.probe(f"from peach.support_bundle import main; raise SystemExit(main([{str(destination)!r}]))")
        self.assertNotEqual(again.returncode, 0)

    def test_browser_errors_require_same_origin_and_remote_login_while_export_is_local(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from types import SimpleNamespace
        from peach import routes_configuration
        from peach.routes_auth import require_auth
        app = FastAPI()
        app.include_router(routes_configuration.router)
        app.dependency_overrides[require_auth] = lambda: {}
        app.state.settings = SimpleNamespace(mdns_name="peach", token='', access_path=self.root / 'access.json')
        app.state.mount_reachability = SimpleNamespace(summary=lambda: {})
        client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234))
        path = "/api/diagnostics/client-error"
        self.assertEqual(client.post(path, json={"message": "secret=private-browser"},
                                     headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(client.post(path, content=b"x" * 20_000).status_code, 413)
        self.assertEqual(client.post(path, json={"message": 5}).status_code, 400)
        self.assertEqual(client.post(path, json={"message": "secret=private-browser", "stack": "at render",
                                               'source_file': 'app-abcd.js', 'line': 21, 'column': 8}).status_code, 200)
        browser = next(row for row in self.records() if row["stage"] == "browser")
        self.assertNotIn("private-browser", str(browser))
        self.assertEqual(browser["stack"], "at render")
        self.assertEqual(browser['line'], 21)
        self.assertEqual(browser['source_file'], 'app-abcd.js')
        from peach import remote_reporting
        with mock.patch.object(remote_reporting, 'send') as send:
            response = client.post(path, json={'message': 'request cancelled', 'error_name': 'AbortError',
                                               'stack': 'at peach-app.js:12:34'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.records()[-1]['error_name'], 'AbortError')
        send.assert_not_called()
        with mock.patch("peach.diagnostics.report", side_effect=RuntimeError("diagnostics unavailable")):
            response = client.get("/api/diagnostics/bundle")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.content)))
        remote = TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.55", 1234))
        self.assertEqual(remote.post(path, json={"message": "example"}).status_code, 401)
        from peach import access
        empty_policy = access.load(app.state.settings.access_path)
        empty_policy['key'] = ''
        remote.cookies.set(access.COOKIE, access.issue(empty_policy, 1)[0])
        self.assertEqual(remote.post(path, json={'message': 'anonymous'}).status_code, 401)
        policy = access.save(app.state.settings.access_path, 'valid-private-password')
        remote.cookies.set(access.COOKIE, access.issue(policy, 1)[0])
        self.assertEqual(remote.post(path, json={'message': 'phone failed'}).status_code, 200)
        self.assertEqual(remote.post(path, json={'message': 'phone failed'},
                                     headers={'Origin': 'https://other.example'}).status_code, 403)
        remote.cookies.clear()
        self.assertEqual(remote.post(path, json={'message': 'anonymous'}).status_code, 401)
        self.assertEqual(remote.get("/api/diagnostics/bundle").status_code, 403)
        for _ in range(20):
            response = client.post(path, json={"message": "example"})
        self.assertEqual(response.status_code, 429)

    def test_background_failure_has_a_persistent_traceback_and_job_identifier(self):
        from peach.jobs import BackgroundJob
        job = BackgroundJob('test-diagnostic-job')
        def fail(_job_id):
            raise ValueError('failed --token private-cli-token')
        state = job.start(fail)
        job.thread.join(3)
        self.assertFalse(job.thread.is_alive())
        event = next(row for row in self.records() if row['stage'] == 'background-job')
        self.assertEqual(event['job_id'], state['job_id'])
        self.assertEqual(event['exception'][0]['type'], 'ValueError')
        self.assertTrue(event['frames'])
        self.assertNotIn('private-cli-token', str(event))
        job.start_result(lambda: {'ok': False, 'error': 'expired --token private-result-token'})
        job.thread.join(3)
        result = next(row for row in self.records() if row['stage'] == 'background-result')
        self.assertIn('expired', result['message'])
        self.assertNotIn('private-result-token', str(result))

    def test_export_keeps_safe_source_health_without_source_response_content(self):
        source = {'source': 'javdb', 'status': 'warning', 'reason': '来源正在冷却',
                  'cooldown_until': '2026-10-18T01:00:00Z', 'raw': 'private-source-response',
                  'url': 'https://private.example/?token=private-token'}
        with zipfile.ZipFile(io.BytesIO(support_bundle.bundle_bytes(report={'sources': [source]}))) as archive:
            report = json.loads(archive.read('report.json'))
        self.assertEqual(report['sources'][0]['status'], 'warning')
        self.assertEqual(report['sources'][0]['cooldown_until'], source['cooldown_until'])
        self.assertNotIn('private-source-response', str(report))

    def test_probe_error_preserves_exit_code_and_stderr(self):
        from peach import media_probe
        result = subprocess.CompletedProcess(['ffprobe'], 69, b'{}', b'AV1 decoder unavailable')
        with mock.patch.object(media_probe.subprocess, 'run', return_value=result), \
                mock.patch.object(media_probe, 'resolve_case_insensitive', str):
            measured = media_probe.measure('ffprobe', 23, 'private.mp4')
        self.assertEqual(measured[0], -1)
        event = next(row for row in self.records() if row['stage'] == 'media-probe')
        self.assertEqual(event['asset_id'], 23)
        self.assertEqual(event['returncode'], 69)
        self.assertIn('AV1 decoder unavailable', event['stderr'])

    def test_command_line_credentials_are_redacted(self):
        for flag in ('--token', '--tunnel-token', '--api-key', '--access-key', '--password'):
            self.assertNotIn('private-value', error_reporting.redact(f'{flag} "private-value"'))

    def test_http_server_errors_are_correlated_without_request_contents(self):
        from fastapi import HTTPException
        from fastapi.testclient import TestClient
        from peach.api import create_app
        from peach.config import PeachSettings
        app = create_app(PeachSettings(db_path=self.root / 'database/ledger.db'))
        @app.get('/diagnostic-test/{identifier}')
        def fail(identifier: str):
            raise RuntimeError('request failed')
        @app.get('/diagnostic-handled-test')
        def handled():
            raise HTTPException(503, 'service unavailable')
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get('/diagnostic-test/private-identifier?token=private-query',
                              headers={'Authorization': 'Bearer private-auth'})
        self.assertEqual(response.status_code, 500)
        record = next(row for row in self.records() if row['stage'] == 'http-request')
        self.assertEqual(record['route'], 'diagnostic-test/{identifier}')
        self.assertEqual(record['error_id'], response.json()['error_id'])
        self.assertEqual(response.headers['x-peach-error-id'], record['error_id'])
        self.assertTrue(record['frames'])
        for value in ('private-identifier', 'private-query', 'private-auth'):
            self.assertNotIn(value, str(record))
        self.assertEqual(client.get('/diagnostic-handled-test').status_code, 503)
        self.assertEqual(len([row for row in self.records() if row['stage'] == 'http-request']), 2)
        client.get('/diagnostic-missing')
        self.assertEqual(len([row for row in self.records() if row['stage'] == 'http-request']), 2)

    def test_asyncio_exceptions_are_recorded_and_keep_the_default_handler(self):
        loop = mock.Mock()
        error_reporting.asyncio_handler(loop, {"exception": ValueError("async failure"), "message": "task"})
        self.assertEqual(self.records()[0]["stage"], "unhandled-asyncio")
        loop.default_exception_handler.assert_called_once()

    def asyncio_callback(self, error, *, module='asyncio.proactor_events',
                         name='_call_connection_lost', business=False, cleared=False,
                         recoverable=False, close_error=None, socket_override=None, server_override=None):
        namespace = {'__name__': module, 'failure': error}
        from asyncio.proactor_events import _ProactorBasePipeTransport
        namespace['Base'] = _ProactorBasePipeTransport if recoverable else object
        if business:
            exec(compile('def fail():\n    raise failure\n', 'peach/network_fixture.py', 'exec'), namespace)
        operation = 'fail()' if business else 'raise failure'
        if recoverable:
            operation = ('if self._called_connection_lost:\n            return\n'
                         '        self._protocol.connection_lost(None)\n        raise failure')
        exec(compile(f'class _ProactorBasePipeTransport(Base):\n    def {name}(self):\n        {operation}\n',
                     'asyncio/proactor_events.py', 'exec'), namespace)
        transport = object.__new__(namespace['_ProactorBasePipeTransport'])
        protocol_type = type('H11Protocol', (), {'__module__': 'uvicorn.protocols.http.h11_impl'})
        ssl_type = type('SSLProtocol', (), {'__module__': 'asyncio.sslproto'})
        transport._protocol = ssl_type()
        transport._protocol._app_protocol = None if cleared else protocol_type()
        transport._protocol.connection_lost = mock.Mock()
        if recoverable:
            transport._sock = socket_override if socket_override is not None else mock.Mock()
            if socket_override is None:
                transport._sock.close.side_effect = close_error
            transport._server = server_override if server_override is not None else mock.Mock()
            if server_override is not None:
                transport._server._attach(transport)
            transport._closing = True
            transport._called_connection_lost = False
            self.addCleanup(lambda: setattr(transport, '_sock', None))
        transport.private_url = 'https://private.example/?token=private-token'
        transport.private_path = 'D:/private/movie.mp4'
        loop = mock.Mock()
        loop.get_debug.return_value = False
        loop.diagnostic_transport = transport
        if recoverable:
            loop.diagnostic_socket = transport._sock
            loop.diagnostic_server = transport._server
        loop.call_exception_handler.side_effect = lambda context: error_reporting.asyncio_handler(loop, context)
        asyncio.Handle(getattr(transport, name), (), loop)._run()
        return loop

    def test_connection_cleanup_completes_socket_close_and_server_detach_once(self):
        from peach import remote_reporting
        error = ConnectionResetError(10054, 'connection reset')
        error.winerror = 10054
        with mock.patch.object(remote_reporting, 'send') as send:
            loop = self.asyncio_callback(error, recoverable=True)
        transport = loop.diagnostic_transport
        self.assertIsNone(transport._sock)
        self.assertIsNone(transport._server)
        self.assertTrue(transport._called_connection_lost)
        self.assertTrue(self.records()[-1]['cleanup_recovered'])
        loop.diagnostic_socket.close.assert_called_once()
        loop.diagnostic_server._detach.assert_called_once_with(transport)
        self.assertEqual(transport._protocol.connection_lost.call_count, 1)
        asyncio.Handle(transport._call_connection_lost, (), loop)._run()
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(transport._protocol.connection_lost.call_count, 1)
        loop.default_exception_handler.assert_not_called()
        send.assert_not_called()

    def test_socket_cleanup_failure_keeps_the_original_exception_and_reports(self):
        from peach import remote_reporting
        error = ConnectionResetError(10054, 'connection reset')
        error.winerror = 10054
        close_error = OSError(5, 'close failed')
        with mock.patch.object(remote_reporting, 'send') as send:
            loop = self.asyncio_callback(error, recoverable=True, close_error=close_error)
        send.assert_called_once()
        self.assertFalse(loop.diagnostic_transport._called_connection_lost)
        record = self.records()[-1]
        self.assertEqual(record['classification'], 'connection-cleanup-failed')
        self.assertFalse(record['cleanup_recovered'])
        self.assertIs(close_error.__cause__, error)
        loop.default_exception_handler.assert_called_once()

    def test_windows_asyncio_connection_cleanup_stays_local_with_safe_context(self):
        from peach import remote_reporting
        error = ConnectionResetError(10054, 'connection reset')
        error.winerror = 10054
        with mock.patch.object(remote_reporting, 'send') as send:
            loop = self.asyncio_callback(error)
        send.assert_not_called()
        record = self.records()[0]
        self.assertEqual(record['classification'], 'connection-cleanup')
        self.assertEqual(record['asyncio_callback'],
                         'asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost')
        self.assertEqual(record['asyncio_protocol'], 'uvicorn.protocols.http.h11_impl.H11Protocol')
        self.assertEqual(record['asyncio_connection_role'], 'http-server')
        self.assertTrue(record['asyncio_tls'])
        self.assertEqual(record['exception'][0]['winerror'], 10054)
        self.assertEqual(record['frames'][-1]['function'], '_call_connection_lost')
        for value in ('private.example', 'private-token', 'movie.mp4', 'private_url', 'private_path'):
            self.assertNotIn(value, json.dumps(record))
        loop.default_exception_handler.assert_called_once()

    def test_connection_cleanup_closes_real_socket_and_releases_asyncio_server(self):
        import socket
        from peach import remote_reporting

        async def run():
            server = await asyncio.get_running_loop().create_server(asyncio.Protocol, '127.0.0.1', 0)
            accepted, peer = socket.socketpair()
            try:
                error = ConnectionResetError(10054, 'connection reset')
                error.winerror = 10054
                with mock.patch.object(remote_reporting, 'send') as send:
                    loop = self.asyncio_callback(error, recoverable=True,
                        socket_override=accepted, server_override=server)
                self.assertEqual(accepted.fileno(), -1)
                self.assertEqual(len(server._clients), 0)
                loop.default_exception_handler.assert_not_called()
                send.assert_not_called()
            finally:
                accepted.close()
                peer.close()
                server.close()
                await asyncio.wait_for(server.wait_closed(), timeout=2)

        asyncio.run(run())

    def test_asyncio_tls_cleanup_keeps_protocol_class_after_application_detaches(self):
        from peach import remote_reporting
        error = ConnectionResetError(10054, 'connection reset')
        error.winerror = 10054
        with mock.patch.object(remote_reporting, 'send') as send:
            loop = self.asyncio_callback(error, cleared=True)
        send.assert_not_called()
        record = self.records()[-1]
        self.assertEqual(record['asyncio_protocol'], 'asyncio.sslproto.SSLProtocol')
        self.assertTrue(record['asyncio_tls'])
        self.assertNotIn('asyncio_connection_role', record)
        loop.default_exception_handler.assert_called_once()

    def test_asyncio_connection_faults_require_the_exact_cleanup_signature(self):
        from peach import remote_reporting
        cases = [
            ({}, 10053, ConnectionResetError),
            ({}, 10054, RuntimeError),
            ({'name': '_read_ready'}, 10054, ConnectionResetError),
            ({'module': 'peach.network_fixture'}, 10054, ConnectionResetError),
            ({'business': True}, 10054, ConnectionResetError),
        ]
        for options, code, error_type in cases:
            with self.subTest(options=options, code=code, error_type=error_type):
                error = error_type(code, 'network failure')
                error.winerror = code
                with mock.patch.object(remote_reporting, 'send') as send:
                    loop = self.asyncio_callback(error, **options)
                send.assert_called_once()
                self.assertNotIn('classification', self.records()[-1])
                loop.default_exception_handler.assert_called_once()

    def test_asyncio_cleanup_with_linked_failure_keeps_remote_evidence(self):
        from peach import remote_reporting
        for attribute in ('__cause__', '__context__'):
            with self.subTest(attribute=attribute):
                error = ConnectionResetError(10054, 'connection reset')
                error.winerror = 10054
                setattr(error, attribute, ValueError('linked failure'))
                with mock.patch.object(remote_reporting, 'send') as send:
                    self.asyncio_callback(error)
                send.assert_called_once()
                self.assertNotIn('classification', self.records()[-1])
                self.assertNotIn('asyncio_protocol', json.dumps(send.call_args.args[1]))

    def test_connection_reset_in_business_stage_is_reported_without_asyncio_metadata(self):
        from peach import remote_reporting
        error = ConnectionResetError(10054, 'business network failure')
        error.winerror = 10054
        with mock.patch.object(remote_reporting, 'send') as send:
            error_reporting.event('background-job', error)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1]['tags']['winerror'], '10054')

    def test_unraisable_errors_and_logging_exceptions_have_persistent_records(self):
        code = """
import gc, logging, sys
from peach import error_reporting as e
e.install()
sys.stderr = None
class Broken:
    def __del__(self):
        raise RuntimeError('cleanup failed')
delayed = Broken()
del delayed
gc.collect()
try:
    raise ValueError('service failed')
except ValueError:
    logging.getLogger('peach.service').exception('request failed')
"""
        result = self.probe(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        stages = {row["stage"] for row in self.records()}
        self.assertTrue({"unraisable", "logging"} <= stages)

    def test_packaged_tray_import_failure_saves_a_report_before_optional_dependencies(self):
        code = f"""
import builtins, ctypes, runpy, sys
from pathlib import Path
original = builtins.__import__
def import_module(name, *args, **kwargs):
    if name == 'pystray':
        raise ModuleNotFoundError('tray dependency unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = import_module
if sys.platform == 'win32':
    ctypes.windll.user32.MessageBoxW = lambda _, message, *rest: Path({str(self.root / 'dialog.txt')!r}).write_text(message, encoding='utf-8')
sys.argv = ['Peach.exe']
runpy.run_path({str(ROOT / 'scripts/build_app_entry.py')!r}, run_name='__main__')
"""
        result = self.probe(code)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(list(self.logs.glob("peach-support-*.zip")))
        self.assertTrue(any(row["stage"] == "tray-import" for row in self.records()))
        if sys.platform == "win32":
            self.assertIn("诊断报告", (self.root / "dialog.txt").read_text(encoding="utf-8"))

    def test_offline_export_creates_its_log_directory_on_a_fresh_machine(self):
        result = self.probe("from peach.support_bundle import main; raise SystemExit(main())")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(list(self.logs.glob("peach-support-*.zip")))


if __name__ == "__main__":
    unittest.main()
