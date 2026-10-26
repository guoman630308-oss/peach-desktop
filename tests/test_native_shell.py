from pathlib import Path
import json
import os
import socket
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from peach.native_shell import NativeShell, defer_download, local_origin, prepare_proxy_environment, request_existing


class NativeProxyEnvironmentTests(unittest.TestCase):
    def test_system_proxy_is_available_with_local_bypass_environment(self):
        from urllib.request import getproxies_environment
        with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1,localhost'}, clear=True), \
                patch('peach.native_shell.sys.platform', 'win32'), \
                patch('urllib.request.getproxies_registry', create=True, return_value={
                    'http': 'http://127.0.0.1:18080', 'https': 'http://127.0.0.1:18080', 'no': '*.local'}):
            prepare_proxy_environment()
            effective = getproxies_environment()
            self.assertEqual(effective['https'], 'http://127.0.0.1:18080')
            self.assertEqual(set(effective['no'].split(',')), {'127.0.0.1', 'localhost', '*.local'})

    def test_explicit_environment_proxy_retains_priority(self):
        with patch.dict(os.environ, {'NO_PROXY': 'localhost', 'HTTPS_PROXY': 'http://127.0.0.1:18081'}, clear=True), \
                patch('peach.native_shell.sys.platform', 'win32'), \
                patch('urllib.request.getproxies_registry', create=True) as registry:
            prepare_proxy_environment()
            self.assertEqual(os.environ['HTTPS_PROXY'], 'http://127.0.0.1:18081')
            self.assertNotIn('HTTP_PROXY', os.environ)
            registry.assert_not_called()

    def test_wildcard_bypass_still_disables_proxy_use(self):
        from httpx._utils import get_environment_proxies
        with patch.dict(os.environ, {'NO_PROXY': '*'}, clear=True), \
                patch('peach.native_shell.sys.platform', 'win32'), \
                patch('urllib.request.getproxies_registry', create=True, return_value={'https': 'http://127.0.0.1:18080'}):
            prepare_proxy_environment()
            self.assertEqual(get_environment_proxies(), {})

    def test_plain_system_proxy_lookup_is_left_to_the_http_client(self):
        with patch.dict(os.environ, {}, clear=True), patch('peach.native_shell.sys.platform', 'win32'), \
                patch('urllib.request.getproxies_registry', create=True) as registry:
            prepare_proxy_environment()
            self.assertNotIn('HTTP_PROXY', os.environ)
            registry.assert_not_called()


class NativeDownloadTests(unittest.TestCase):
    def setUp(self):
        self.form, self.sender, self.args = Mock(), Mock(), Mock()
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {'System': SimpleNamespace(Action=lambda callback: callback)}).start()

    def test_download_waits_for_save_selection_before_continuing(self):
        defer_download(self.form, self.sender, self.args)
        self.form.browser.on_download_starting.assert_not_called()
        self.args.GetDeferral.return_value.Complete.assert_not_called()
        self.assertIs(self.args.Handled, True)
        self.form.BeginInvoke.call_args.args[0]()
        self.form.browser.on_download_starting.assert_called_once_with(self.sender, self.args)
        self.args.GetDeferral.return_value.Complete.assert_called_once()

    def test_save_dialog_failure_cancels_download_and_releases_deferral(self):
        self.form.browser.on_download_starting.side_effect = RuntimeError('窗口已关闭')
        defer_download(self.form, self.sender, self.args)
        with self.assertLogs('peach.native_shell', level='ERROR'):
            self.form.BeginInvoke.call_args.args[0]()
        self.assertIs(self.args.Cancel, True)
        self.args.GetDeferral.return_value.Complete.assert_called_once()

    def test_window_shutdown_cancels_unscheduled_download(self):
        self.form.BeginInvoke.side_effect = RuntimeError('窗口已关闭')
        with self.assertLogs('peach.native_shell', level='ERROR'):
            defer_download(self.form, self.sender, self.args)
        self.assertIs(self.args.Cancel, True)
        self.args.GetDeferral.return_value.Complete.assert_called_once()


class NativeShellTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'state').mkdir()
        self.window = Mock()
        self.window.get_current_url.return_value = 'http://127.0.0.1:8000/'
        self.shell = NativeShell(self.window, self.root)

    def test_only_loopback_http_service_addresses_are_accepted(self):
        self.assertEqual(local_origin('http://127.0.0.1:8000/configuration'), ('http', '127.0.0.1', 8000))
        for url in ('https://example.com/', 'http://localhost:8000/', 'http://127.0.0.1/',
                    'file:///C:/data', 'http://user@127.0.0.1:8000/', 'http://127.0.0.1:80/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                local_origin(url)

    def test_show_preserves_page_when_it_is_already_current(self):
        self.shell.open('http://127.0.0.1:8000/')
        self.window.load_url.assert_not_called()
        self.window.restore.assert_called_once()
        self.window.show.assert_called_once()

    def test_open_uses_actual_navigation_state_for_configuration(self):
        self.window.get_current_url.return_value = 'http://127.0.0.1:8000/configuration'
        self.shell.open('http://127.0.0.1:8000/configuration')
        self.window.get_current_url.return_value = 'http://127.0.0.1:8000/'
        self.shell.open('http://127.0.0.1:8000/configuration')
        self.window.load_url.assert_called_once_with('http://127.0.0.1:8000/configuration')

    def test_external_address_is_rejected_before_window_is_changed(self):
        with self.assertRaises(ValueError):
            self.shell.open('https://example.com/')
        self.window.load_url.assert_not_called()
        self.window.show.assert_not_called()

    def test_navigation_follows_only_saved_service_port_changes(self):
        self.shell.origin = ('http', '127.0.0.1', 8000)
        self.shell.tray = Mock()
        config = Mock(present=True)
        config.server.port = 8123
        with patch('peach.settings_file.load_config', return_value=config):
            self.assertTrue(self.shell.allows_url('http://127.0.0.1:8123/?onboarding=1'))
            self.assertFalse(self.shell.allows_url('http://127.0.0.1:8124/'))
            self.assertFalse(self.shell.allows_url('https://example.com/'))
        self.assertEqual(self.shell.origin, ('http', '127.0.0.1', 8123))

    def test_hiding_pauses_media_and_keeps_tray_running(self):
        self.shell.tray = Mock()
        self.shell.hide()
        self.window.evaluate_js.assert_called_once()
        self.window.hide.assert_called_once()
        self.shell.tray.exit.assert_not_called()

    def test_hide_still_hides_window_if_page_script_has_failed(self):
        self.window.evaluate_js.side_effect = RuntimeError('页面正在切换')
        with self.assertRaises(RuntimeError):
            self.shell.hide()
        self.window.hide.assert_called_once()

    def test_close_hides_until_application_is_stopping(self):
        with patch('peach.native_shell.threading.Thread') as thread:
            self.assertIs(self.shell.close_window(), False)
            thread.return_value.start.assert_called_once()
        self.shell.stopping.set()
        self.assertIs(self.shell.close_window(), True)

    def test_configuration_command_uses_original_tray_gate(self):
        self.shell.tray_ready(Mock())
        self.shell.command('configuration')
        self.shell.tray.gate.open.assert_called_once_with('configuration')

    def test_duplicate_launch_waits_for_service_before_navigation(self):
        self.shell.tray_ready(Mock())
        self.shell.tray.manager.wait_until_ready.return_value = False
        self.shell.command('open')
        self.shell.tray.gate.open.assert_not_called()
        self.shell.tray.manager.wait_until_ready.return_value = True
        self.shell.command('open')
        self.shell.tray.gate.open.assert_called_once_with('')

    def test_quit_requests_original_tray_shutdown(self):
        self.shell.tray_ready(Mock())
        self.shell.command('quit')
        self.assertTrue(self.shell.stopping.is_set())
        self.shell.tray.exit.assert_called_once()

    def test_quit_during_startup_disarms_original_tray_watchdog(self):
        self.shell.stopping.set()
        tray = Mock()
        with self.assertRaises(SystemExit):
            self.shell.tray_ready(tray)
        tray.exit.assert_called_once_with(reason='window-exit')

    def test_control_requires_secret_and_accepts_only_known_actions(self):
        self.shell.command = Mock()
        self.shell.start_control()
        self.addCleanup(self.shell.close)
        record = json.loads((self.root / 'state/webview-window.json').read_text())
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(.15)
            client.sendto(json.dumps({'token': 'wrong', 'action': 'quit'}).encode(), ('127.0.0.1', record['port']))
            with self.assertRaises(socket.timeout):
                client.recv(32)
        self.assertTrue(request_existing(self.root, 'open'))
        self.shell.command.assert_called_once_with('open')
        with self.assertRaises(ValueError):
            request_existing(self.root, 'http://example.com')

    def test_tray_failure_closes_window_and_preserves_failure_result(self):
        with patch('peach.tray.main', return_value=1):
            self.shell.run_tray([])
        self.assertEqual(self.shell.exit_code, 1)
        self.assertTrue(self.shell.stopping.is_set())
        self.window.destroy.assert_called_once()

    def test_closed_shell_removes_control_record(self):
        self.shell.start_control()
        self.shell.close()
        self.assertFalse((self.root / 'state/webview-window.json').exists())

    def test_page_script_error_keeps_control_channel_available(self):
        self.shell.tray_ready(Mock())
        self.window.evaluate_js.side_effect = RuntimeError('页面正在切换')
        self.shell.start_control()
        self.addCleanup(self.shell.close)
        self.assertTrue(request_existing(self.root, 'hide'))
        self.assertTrue(request_existing(self.root, 'open'))


if __name__ == '__main__':
    unittest.main()
