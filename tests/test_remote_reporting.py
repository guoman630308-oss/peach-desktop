"""远程诊断只用临时同意文件与内存接收端，不访问 Sentry。"""
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from peach import remote_reporting as reporting
from peach.settings_file import PeachConfig

DSN = "https://public@o123.ingest.us.sentry.io/456"


class RemoteReportingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.config = PeachConfig(self.root, self.root / "config.toml")
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(os.environ, {"PEACH_DATA_ROOT": str(self.root), "PEACH_SENTRY_DSN": DSN}).start()
        mock.patch("peach.settings_file.active", return_value=self.config).start()
        mock.patch.object(reporting, "_disabled", False).start()
        self.addCleanup(reporting.close)
        reporting.close()
        reporting.save({"usage": False, "errors": False})

    def test_invalid_and_disabled_choices_never_construct_client(self):
        with mock.patch.object(reporting, "_new_client") as client:
            reporting.usage()
            reporting.capture("unhandled-main", ValueError("secret"))
            self.assertFalse(reporting.snapshot()["errors"])
            path = self.config.directory("state") / "remote-reporting.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            for content in ('{"usage": "true", "errors": 1}', '{broken', '[]'):
                path.write_text(content, encoding="utf-8")
                reporting.usage()
                reporting.capture("unhandled-main", ValueError("secret"))
            client.assert_not_called()
        self.assertFalse((path.parent / "remote-reporting-budget.json").exists())

    def test_default_enables_both_channels_and_saved_disable_is_persistent(self):
        path = self.config.directory("state") / "remote-reporting.json"
        path.unlink()
        client = mock.Mock()
        with mock.patch.object(reporting, "_new_client", return_value=client):
            self.assertTrue(reporting.snapshot()["usage"])
            self.assertTrue(reporting.snapshot()["errors"])
            reporting.usage()
            reporting.capture("unhandled-main", ValueError("private"))
            self.assertEqual(client.capture_event.call_count, 2)
            reporting.save({"usage": False, "errors": False})
            self.assertFalse(reporting.snapshot()["usage"])
            self.assertFalse(reporting.snapshot()["errors"])
            reporting.usage()
            reporting.capture("unhandled-main", ValueError("private"))
            self.assertEqual(client.capture_event.call_count, 2)

    def test_consent_requires_joint_choice_and_missing_endpoint_is_visible(self):
        for invalid in ({"usage": "false", "errors": False}, {"usage": True}, [],
                        {"usage": True, "errors": False}, {"usage": False, "errors": True}):
            with self.assertRaises(ValueError):
                reporting.save(invalid)
        reporting.save({"usage": True, "errors": True})
        self.assertTrue(reporting.snapshot()["usage"])
        self.assertTrue(reporting.snapshot()["errors"])
        with mock.patch.dict(os.environ, {"PEACH_SENTRY_DSN": ""}):
            self.assertFalse(reporting.snapshot()["available"])
            with mock.patch.object(reporting, "_new_client") as client:
                reporting.usage()
                client.assert_not_called()

    def test_partial_consent_keeps_both_channels_disabled(self):
        path = self.config.directory("state") / "remote-reporting.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        for usage, errors in ((True, False), (False, True)):
            path.write_text(json.dumps({"usage": usage, "errors": errors, "revision": "partial"}))
            with mock.patch.object(reporting, "_new_client") as client:
                self.assertFalse(reporting.snapshot()["usage"])
                self.assertFalse(reporting.snapshot()["errors"])
                reporting.usage()
                reporting.capture("unhandled-main", ValueError("private"))
                client.assert_not_called()

    def test_optional_setup_choices_preserve_saved_opt_out(self):
        self.assertEqual(reporting.parse_choices({}, partial=True), {"usage": False, "errors": False})
        (self.config.directory("state") / "remote-reporting.json").unlink()
        self.assertEqual(reporting.parse_choices({}, partial=True), {"usage": True, "errors": True})

    def test_dsn_only_accepts_hosted_public_https_endpoint(self):
        for value in (DSN, DSN.replace('.us.', '.'), DSN.replace('.us.', '.de.')):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"PEACH_SENTRY_DSN": value}):
                self.assertEqual(reporting.dsn(), value)
        for value in ('http://public@o123.ingest.sentry.io/1', 'https://public:secret@o123.ingest.sentry.io/1',
                      'https://public@localhost/1', DSN + '?token=secret', DSN + '#secret',
                      'https://public@o123.ingest.sentry.io.evil/1'):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"PEACH_SENTRY_DSN": value}):
                self.assertEqual(reporting.dsn(), "")

    def test_daily_budget_is_shared_deduplicated_and_bounded(self):
        reporting.save({"usage": True, "errors": True})
        client = mock.Mock()
        with mock.patch.object(reporting, "_new_client", return_value=client):
            reporting.usage()
            reporting.usage()
            for index in range(25):
                event = {"message": str(index)}
                reporting.send("errors", event)
                reporting.send("errors", event)
        self.assertEqual(client.capture_event.call_count, 21)
        payload = json.loads((self.config.directory("state") / "remote-reporting-frequency.json").read_text())
        self.assertEqual(len(payload["records"]), 20)
        self.assertTrue(all(row['total'] == 2 for row in payload['records'].values()))

    def test_frequency_batches_send_disjoint_counts_and_keep_group_identity(self):
        from peach import diagnostic_frequency as frequency
        reporting.save({"usage": True, "errors": True})
        client = mock.Mock()
        now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
        with mock.patch.object(reporting, '_new_client', return_value=client), \
                mock.patch.object(frequency, '_now', return_value=now) as clock:
            for index in range(5):
                reporting.capture('unhandled-main', ValueError('private title'), error_id=str(index))
            self.assertEqual(client.capture_event.call_count, 1)
            first = client.capture_event.call_args.args[0]
            self.assertEqual(first['tags']['occurrence_count'], '1')
            clock.return_value = now + timedelta(hours=1)
            reporting.flush_counts()
            second = client.capture_event.call_args.args[0]
            self.assertEqual(second['tags']['occurrence_count'], '4')
            self.assertEqual(second['tags']['diagnostic_key'], first['tags']['diagnostic_key'])
            self.assertEqual(second['tags']['occurrence_day'], '2026-10-18')
            reporting.flush_counts(force=True)
            self.assertEqual(client.capture_event.call_count, 2)
        payload = (self.config.directory('state') / 'remote-reporting-frequency.json').read_text()
        self.assertNotIn('private title', payload)
        self.assertNotIn('installation', payload)

    def test_frequency_survives_client_restart_and_http_ids_do_not_split_groups(self):
        reporting.save({"usage": True, "errors": True})
        client = mock.Mock()
        with mock.patch.object(reporting, '_new_client', return_value=client):
            for error_id in ('a' * 32, 'b' * 32, 'c' * 32):
                reporting.capture('http-request', RuntimeError('private'), status_code=500, error_id=error_id)
            first = client.capture_event.call_args.args[0]
            self.assertEqual(first['event_id'], 'a' * 32)
            reporting.close()
            reporting.flush_counts(force=True)
            second = client.capture_event.call_args.args[0]
            self.assertNotIn('event_id', second)
            self.assertEqual(second['tags']['occurrence_count'], '2')
            self.assertEqual(first['tags']['diagnostic_key'], second['tags']['diagnostic_key'])
            self.assertEqual(client.capture_event.call_count, 2)

    def test_frequency_pending_counts_expire_with_consent_and_expected_states_are_excluded(self):
        import asyncio
        reporting.save({"usage": True, "errors": True})
        client = mock.Mock()
        with mock.patch.object(reporting, '_new_client', return_value=client):
            reporting.capture('background-job', asyncio.CancelledError())
            reporting.capture('unhandled-main', ValueError('private'))
            reporting.capture('unhandled-main', ValueError('private'))
            reporting.save({"usage": False, "errors": False})
            self.assertFalse((self.config.directory('state') / 'remote-reporting-frequency.json').exists())
            reporting.flush_counts(force=True)
            reporting.save({"usage": True, "errors": True})
            reporting.flush_counts(force=True)
            self.assertEqual(client.capture_event.call_count, 1)
            reporting.capture('unhandled-main', ValueError('private'))
            self.assertEqual(client.capture_event.call_args.args[0]['tags']['occurrence_count'], '1')

    def test_frequency_daily_limit_defers_counts_and_preserves_occurrence_day(self):
        from peach import diagnostic_frequency as frequency
        reporting.save({"usage": True, "errors": True})
        client = mock.Mock()
        now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
        with mock.patch.object(reporting, '_new_client', return_value=client), \
                mock.patch.object(frequency, '_now', return_value=now) as clock:
            for index in range(20):
                reporting.send('errors', {'message': str(index)})
            reporting.send('errors', {'message': '0'})
            reporting.flush_counts(force=True)
            self.assertEqual(client.capture_event.call_count, 20)
            clock.return_value = now + timedelta(days=1)
            reporting.flush_counts(force=True)
            self.assertEqual(client.capture_event.call_count, 21)
            self.assertEqual(client.capture_event.call_args.args[0]['tags']['occurrence_count'], '1')
            self.assertEqual(client.capture_event.call_args.args[0]['tags']['occurrence_day'], '2026-10-18')

    def test_frequency_sdk_rejection_keeps_pending_counts_for_later_batch(self):
        reporting.save({"usage": True, "errors": True})
        client = mock.Mock()
        client.capture_event.return_value = None
        with mock.patch.object(reporting, '_new_client', return_value=client):
            reporting.capture('unhandled-main', ValueError('private'))
            client.capture_event.return_value = 'accepted'
            reporting.flush_counts(force=True)
            self.assertEqual(client.capture_event.call_args.args[0]['tags']['occurrence_count'], '1')
            reporting.flush_counts(force=True)
            self.assertEqual(client.capture_event.call_count, 2)

    def test_frequency_shared_file_serializes_independent_processes(self):
        from peach import diagnostic_frequency as frequency
        root = Path(__file__).resolve().parents[1]
        reporting.save({"usage": True, "errors": True})
        program = ('from peach import diagnostic_frequency as f; '
                   'from peach.settings_file import active; '
                   'from peach.remote_reporting import consent; '
                   'f.record(active(), consent()["revision"], {"release":"test", "tags":{"stage":"cli"}}, lambda e: True)')
        env = {**os.environ, 'PYTHONPATH': str(root / 'src'), 'PEACH_SENTRY_DSN': ''}
        processes = [subprocess.Popen([sys.executable, '-X', 'utf8', '-c', program], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for process in processes:
            try:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, (stdout, stderr))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        sent = []
        frequency.flush(self.config, reporting.consent()['revision'], lambda e: sent.append(e) or True, force=True)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['tags']['occurrence_count'], '1')

    def test_frequency_shutdown_submits_pending_counts_and_stops_timer(self):
        reporting.save({"usage": True, "errors": True})
        client = mock.Mock()
        with mock.patch.object(reporting, '_new_client', return_value=client):
            for _ in range(3):
                reporting.capture('unhandled-main', ValueError('private'))
            reporting._shutdown()
        self.assertEqual(client.capture_event.call_count, 2)
        self.assertEqual(client.capture_event.call_args.args[0]['tags']['occurrence_count'], '2')
        self.assertIsNone(reporting._frequency_timer)
        self.assertFalse(reporting._frequency_configs)

    def test_frequency_background_failure_does_not_break_startup(self):
        reporting.save({"usage": True, "errors": True})
        with mock.patch.object(reporting, '_schedule_frequency', side_effect=OSError('unavailable')), \
                mock.patch.object(reporting, '_new_client'):
            reporting.usage()

    def test_frequency_timer_flushes_without_another_occurrence(self):
        from peach import diagnostic_frequency as frequency
        reporting.save({"usage": True, "errors": True})
        now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
        client = mock.Mock()
        with mock.patch.object(reporting, '_new_client', return_value=client), \
                mock.patch.object(reporting.threading, 'Timer') as timer, \
                mock.patch.object(frequency, '_now', return_value=now) as clock:
            for _ in range(4):
                reporting.capture('unhandled-main', ValueError('private'))
            callback = timer.call_args.args[1]
            clock.return_value = now + timedelta(hours=1)
            callback()
            self.assertEqual(client.capture_event.call_count, 2)
            self.assertEqual(client.capture_event.call_args.args[0]['tags']['occurrence_count'], '3')

    def test_actionable_faults_keep_codes_and_omit_private_text(self):
        with mock.patch.object(reporting, "send") as send:
            cause = OSError(13, 'private movie token=secret D:/media/private.mp4')
            error = RuntimeError('private movie')
            error.__cause__ = cause
            reporting.capture("background-job", error, job_id='private-job')
            event = send.call_args.args[1]
            self.assertEqual(event["tags"]["errno"], '13')
            self.assertIn('PermissionError', event['exception']['values'][0]['value'])
            self.assertNotIn('private', json.dumps(event))
            reporting.capture('frame-capture', returncode=69,
                              stderr="private.mp4 Your platform doesn't support hardware accelerated AV1 decoding")
            self.assertEqual(send.call_args.args[1]['tags']['media_failure'], 'av1_decoder_unavailable')

    def test_expected_and_opaque_errors_stay_local(self):
        with mock.patch.object(reporting, "send") as send:
            reporting.capture('logging', message='generic error', level='ERROR')
            reporting.capture('logging', ValueError('source temporarily unavailable'), logger='sentry_sdk.errors')
            reporting.capture('http-request', ValueError('invalid input'), status_code=400)
            reporting.capture('background-result', message='cancelled')
            reporting.capture('browser', message='Script error.', stack='chrome-extension://private/file.js:1:2')
            send.assert_not_called()
            reporting.capture('browser', message='private title', stack='at https://private/dist/peach-app.js:12:34')
            self.assertEqual(send.call_args.args[1]['exception']['values'][0]['stacktrace']['frames'],
                             [{'filename': 'peach-app.js', 'lineno': 12, 'colno': 34}])

    def test_control_and_source_states_keep_local_records_without_remote_events(self):
        import asyncio
        from concurrent.futures import CancelledError
        from peach import error_reporting
        from peach.browser_transport import BrowserUnavailable, ChallengeUnsolved
        from peach.jav_cover_fetch import CoverConnectError, DeadlineExceeded
        from peach.jobs import JobPaused, MeteredSourceDenied, DiskSpaceDenied, TaskRunConflict
        from peach.metadata import MetadataProviderError
        from peach.previews import PreviewUnavailable
        from peach.scraping_access import SourcePaused
        from peach.segments import SegmentCancelled
        from peach.sources.base import SourceFailure, FailureReason
        from peach.transcodes import TranscodeCancelled
        from starlette.requests import ClientDisconnect
        errors = [asyncio.CancelledError(), CancelledError(), InterruptedError(), JobPaused(),
                  MeteredSourceDenied(), DiskSpaceDenied(), TaskRunConflict('processing', 1),
                  SourcePaused(), DeadlineExceeded(), CoverConnectError(), PreviewUnavailable(),
                  SegmentCancelled(), TranscodeCancelled(), BrowserUnavailable(), ChallengeUnsolved(),
                  ClientDisconnect(),
                  MetadataProviderError('auth', kind='auth'),
                  MetadataProviderError('absent', kind='not_found'),
                  MetadataProviderError('retry', kind='unavailable', retryable=True)]
        errors += [SourceFailure(reason, 'source state') for reason in FailureReason
                   if reason != FailureReason.PARSE_ERROR]
        with mock.patch.object(reporting, 'send') as send:
            for error in errors:
                with self.subTest(error=type(error).__name__):
                    path = error_reporting.event('background-job', error)
                    self.assertTrue(path.is_file())
            send.assert_not_called()

    def test_source_parser_and_unknown_provider_failures_are_actionable(self):
        from peach.metadata import MetadataProviderError
        from peach.sources.base import SourceFailure, FailureReason
        errors = [SourceFailure(FailureReason.PARSE_ERROR, 'parser failure'),
                  MetadataProviderError('parser failure', kind='unavailable', detail='parse_error'),
                  MetadataProviderError('unknown failure')]
        for error in errors:
            with self.subTest(error=type(error).__name__), mock.patch.object(reporting, 'send') as send:
                reporting.capture('background-job', error)
                send.assert_called_once()

    def test_unrelated_exception_names_do_not_acquire_domain_state_semantics(self):
        for name in ('CancelledError', 'NotFound', 'Unavailable'):
            with self.subTest(name=name), mock.patch.object(reporting, 'send') as send:
                error = type(name, (RuntimeError,), {'__module__': 'peach.fixture'})('unexpected failure')
                reporting.capture('background-job', error)
                send.assert_called_once()

    def test_source_client_http_statuses_are_local_and_server_failures_are_reported(self):
        import httpx
        from urllib.error import HTTPError
        for code in (400, 401, 403, 404, 410, 429, 500, 503):
            response = httpx.Response(code, request=httpx.Request('GET', 'https://example.test/'))
            errors = [httpx.HTTPStatusError('http state', request=response.request, response=response),
                      HTTPError('https://example.test/', code, 'http state', {}, None)]
            for error in errors:
                with self.subTest(code=code, error=type(error).__name__), \
                        mock.patch.object(reporting, 'send') as send:
                    reporting.capture('background-job', error)
                    if code < 500:
                        send.assert_not_called()
                    else:
                        send.assert_called_once()
                        self.assertEqual(send.call_args.args[1]['tags']['status_code'], str(code))

    def test_warning_exceptions_do_not_consume_remote_error_budget(self):
        namespace = {}
        exec(compile('def fail():\n    raise ValueError("retrying")\n', 'peach/fixture.py', 'exec'), namespace)
        try:
            namespace['fail']()
        except ValueError as error:
            with mock.patch.object(reporting, 'send') as send:
                reporting.capture('logging', error, level='WARNING', logger='peach.fixture')
                send.assert_not_called()
                reporting.capture('logging', error, level='ERROR', logger='peach.fixture')
                send.assert_called_once()

    def test_expected_media_input_conditions_are_local_and_decoder_faults_are_reported(self):
        for marker in ('Invalid data found', 'Permission denied', 'No space left', 'No such file or directory'):
            with self.subTest(marker=marker), mock.patch.object(reporting, 'send') as send:
                reporting.capture('frame-capture', returncode=1, stderr=marker)
                send.assert_not_called()
        with mock.patch.object(reporting, 'send') as send:
            import errno
            reporting.capture('media-probe', OSError(errno.ENOSPC, 'disk full'), tool='ffprobe')
            send.assert_not_called()
            reporting.capture('media-probe', subprocess.TimeoutExpired('ffprobe', 20), tool='ffprobe')
            send.assert_not_called()
            reporting.capture('media-probe', FileNotFoundError(2, 'missing input', 'movie.mp4'), tool='ffprobe')
            send.assert_not_called()
            reporting.capture('media-probe', FileNotFoundError(2, 'missing tool', 'ffprobe'), tool='ffprobe')
            send.assert_called_once()
            send.reset_mock()
            reporting.capture('frame-capture', returncode=69,
                              stderr="Your platform doesn't support hardware accelerated AV1 decoding")
            send.assert_called_once()
            send.reset_mock()
            reporting.capture('frame-capture', returncode=1,
                              stderr='Unknown decoder; Invalid data found')
            send.assert_called_once()

    def test_browser_abort_and_extension_frames_stay_local_and_bundle_versions_keep_positions(self):
        with mock.patch.object(reporting, 'send') as send:
            reporting.capture('browser', error_name='AbortError', stack='peach-app.js:12:34')
            reporting.capture('browser', stack='at chrome-extension://extension/peach-app.js:12:34')
            reporting.capture('browser', stack='at moz-extension://extension/peach-pages.js:12:34')
            send.assert_not_called()
            reporting.capture('browser', stack='at https://example.test/dist/peach-app.js?v=build:12:34')
            send.assert_called_once()
            frames = send.call_args.args[1]['exception']['values'][0]['stacktrace']['frames']
            self.assertEqual(frames, [{'filename': 'peach-app.js', 'lineno': 12, 'colno': 34}])

    def test_preview_environment_conditions_preserve_guidance_and_local_issue(self):
        from peach import library_processing
        from peach.previews import PreviewUnavailable
        resolver = mock.Mock()
        resolver.ffmpeg.return_value = None
        row = {'id': 1, 'path': str(self.root / 'missing.mp4'), 'location': 'local', 'name': 'missing.mp4'}
        with self.assertRaises(PreviewUnavailable) as raised:
            library_processing._generate_preview(self.config, row, mock.Mock(), resolver, progress=lambda: None)
        issue = mock.Mock()
        with mock.patch.object(reporting, 'send') as send, \
                mock.patch.object(library_processing, '_generate_preview', side_effect=raised.exception):
            made = library_processing._generate_previews(self.config, [row], mock.Mock(),
                update=mock.Mock(), issue=issue, guard=mock.Mock())
        self.assertEqual(made, 0)
        self.assertIn('FFmpeg', issue.call_args.args[1])
        self.assertTrue(issue.call_args.kwargs['retryable'])
        send.assert_not_called()

    def test_sdk_wire_payload_has_no_local_context_or_consent_path(self):
        from sentry_sdk.transport import HttpTransport
        sent = []
        (self.config.directory("state") / "remote-reporting.json").unlink()
        with mock.patch.object(HttpTransport, "_send_envelope", side_effect=lambda envelope: sent.append(envelope.serialize())):
            reporting.capture("unhandled-main", ValueError("private title token=secret"))
            reporting._client.flush(timeout=2)
        self.assertEqual(len(sent), 1)
        wire = sent[0].decode()
        for value in ('private title', 'secret', str(self.root), 'server_name', 'contexts', 'extra', 'consent'):
            self.assertNotIn(value, wire)
        self.assertIn('ValueError', wire)
        self.assertIn('occurrence_count', wire)
        self.assertIn('diagnostic_key', wire)

    def test_queued_event_requires_current_joint_consent_revision(self):
        from sentry_sdk.transport import HttpTransport
        from sentry_sdk.envelope import Envelope
        reporting.save({"usage": True, "errors": True})
        previous = reporting.consent()
        client = reporting._new_client(DSN)
        self.addCleanup(lambda: client.close(timeout=0))
        envelope = Envelope()
        envelope.add_event({"message": "Peach started", "extra": {
            "consent": str(self.config.directory('state') / 'remote-reporting.json'),
            "kind": "usage", "revision": previous['revision']}})
        reporting.save({"usage": False, "errors": False})
        reporting.save({"usage": True, "errors": True})
        with mock.patch.object(HttpTransport, "_send_envelope") as network:
            client.transport._send_envelope(envelope)
            network.assert_not_called()
            path = self.config.directory('state') / 'remote-reporting.json'
            for usage, errors in ((True, False), (False, True)):
                path.write_text(json.dumps({"usage": usage, "errors": errors, "revision": previous['revision']}))
                client.transport._send_envelope(envelope)
            network.assert_not_called()

    def test_sdk_failure_does_not_break_local_error_recording(self):
        from peach import error_reporting
        reporting.save({"usage": True, "errors": True})
        with mock.patch.object(reporting, "_new_client", side_effect=OSError('offline')), \
                mock.patch.object(error_reporting, 'log_directories', return_value=[self.root / 'logs']):
            path = error_reporting.event('unhandled-main', ValueError('local detail'))
        self.assertTrue(path.is_file())

    def test_http_failure_keeps_template_and_error_id_without_request_details(self):
        with mock.patch.object(reporting, "send") as send:
            reporting.capture('http-request', ValueError('private query'), method='POST',
                              route='api/assets/{asset_id}', status_code=500, error_id='a' * 32,
                              query='token=secret', headers={'Authorization': 'secret'})
            event = send.call_args.args[1]
        self.assertEqual(event['event_id'], 'a' * 32)
        self.assertEqual(event['tags']['route_template'], 'api/assets/{asset_id}')
        self.assertEqual(event['tags']['status_code'], '500')
        self.assertNotIn('secret', json.dumps(event))

    def test_installer_choice_is_saved_without_startup_reporting(self):
        reporting.save({"usage": True, "errors": True})
        entry = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_app_entry.py"))
        with mock.patch("peach.error_reporting.install", side_effect=reporting.usage), \
                mock.patch.object(reporting, "send") as send:
            self.assertEqual(entry["main"](["Peach.exe", "--installer-reporting", "0", "0"]), 0)
            send.assert_not_called()
        self.assertFalse(reporting.consent()["usage"])
        self.assertFalse(reporting.consent()["errors"])

    def test_installer_helpers_keep_startup_and_faults_local(self):
        reporting.save({"usage": True, "errors": True})
        entry = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_app_entry.py"))

        def helper(*_args):
            reporting.capture("unhandled-main", RuntimeError("installer fault"))
            return 0

        cases = (
            ("--installer-stop", "main", []),
            ("--installer-uninstall", "main", []),
            ("--installer-prepare", "prepare", [str(self.root), "123", str(self.root / "error.txt")]),
            ("--installer-tools", "prepare_install_tools", [str(self.root)] * 3),
            ("--installer-finish", "finish_install", [str(self.root), "123"]),
            ("--installer-migrate", "migrate_install", [str(self.root)]),
        )
        for mode, handler, args in cases:
            with self.subTest(mode=mode), mock.patch.object(reporting, "_disabled", False), \
                    mock.patch("peach.error_reporting.install", side_effect=reporting.usage), \
                    mock.patch(f"peach.desktop_installer.{handler}", side_effect=helper), \
                    mock.patch.object(reporting, "_new_client") as client:
                self.assertEqual(entry["main"](["Peach.exe", mode, *args]), 0)
                client.assert_not_called()
                self.assertTrue(reporting.consent()["usage"])
                self.assertTrue(reporting.consent()["errors"])

    def test_installer_helper_persists_explicit_choices_in_fresh_process(self):
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, 'PYTHONPATH': str(root / 'src'), 'PEACH_SENTRY_DSN': ''}
        for usage, errors in (('1', '1'), ('0', '0')):
            result = subprocess.run([sys.executable, '-X', 'utf8', str(root / 'scripts/build_app_entry.py'),
                                     '--installer-reporting', usage, errors], env=env,
                                    capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(reporting.consent()['usage'], usage == '1')
            self.assertEqual(reporting.consent()['errors'], errors == '1')
        self.assertFalse(self.config.path.exists())

    def test_invalid_installer_arguments_do_not_start_tray_or_network(self):
        reporting.save({"usage": True, "errors": True})
        entry = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_app_entry.py"))
        tray = mock.Mock()
        with mock.patch.dict(entry["main"].__globals__, {"tray_main": tray}), \
                mock.patch("peach.error_reporting.install", side_effect=reporting.usage), \
                mock.patch.object(reporting, "_new_client") as client:
            for mode in ("--installer-prepare", "--installer-reporting", "--installer-unknown"):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    entry["main"](["Peach.exe", mode])
            tray.assert_not_called()
            client.assert_not_called()


class RemoteReportingApiTests(unittest.TestCase):
    setUp = RemoteReportingTests.setUp

    def request(self, method, path, *, peer='127.0.0.1', origin='http://test', body=None):
        import asyncio
        import httpx
        from fastapi import FastAPI
        from peach.routes_configuration import router, require_auth
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_auth] = lambda: {}

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
                                         base_url='http://test') as client:
                return await client.request(method, path, json=body, headers={'Origin': origin})
        return asyncio.run(run())

    def test_local_save_and_remote_and_cross_origin_denial(self):
        endpoint = '/api/configuration/remote-reporting'
        body = {'usage': True, 'errors': True}
        response = self.request('POST', endpoint, body=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['usage'])
        self.assertTrue(response.json()['errors'])
        response = self.request('POST', endpoint, body={'usage': False, 'errors': True})
        self.assertEqual(response.status_code, 400, response.text)
        for peer, origin in (('198.51.100.7', 'http://test'), ('127.0.0.1', 'https://external.invalid')):
            response = self.request('POST', endpoint, body={'usage': False, 'errors': False}, peer=peer, origin=origin)
            self.assertEqual(response.status_code, 403, response.text)
        self.assertTrue(reporting.consent()['errors'])
