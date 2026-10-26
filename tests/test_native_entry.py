from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import build_app_entry


class NativeEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_window_marker_routes_default_launch_into_embedded_host(self):
        with patch.dict('os.environ', {'PEACH_DATA_ROOT': str(self.root)}), \
                patch('peach.distribution.embedded_window', return_value=True), \
                patch('peach.error_reporting.install'), patch.object(build_app_entry, 'wants_cli', return_value=False), \
                patch('peach.native_shell.main', return_value=0) as native, patch.object(build_app_entry, 'tray_main') as tray:
            self.assertEqual(build_app_entry.main(['Peach.exe', '--show']), 0)
        native.assert_called_once_with(['--show'])
        tray.assert_not_called()

    def test_cli_remains_available_in_window_package(self):
        with patch.dict('os.environ', {'PEACH_DATA_ROOT': str(self.root)}), \
                patch('peach.distribution.embedded_window', return_value=True), \
                patch('peach.error_reporting.install'), patch.object(build_app_entry, 'wants_cli', return_value=True), \
                patch.object(build_app_entry, '_prepare_console'), patch.object(build_app_entry, 'cli_main', return_value=0) as cli, \
                patch('peach.native_shell.main') as native:
            self.assertEqual(build_app_entry.main(['Peach.exe', 'serve', '--help']), 0)
        cli.assert_called_once_with(['serve', '--help'])
        native.assert_not_called()

    def test_plain_package_preserves_original_tray_entry(self):
        with patch('peach.distribution.embedded_window', return_value=False), patch('peach.error_reporting.install'), \
                patch.object(build_app_entry, 'wants_cli', return_value=False), \
                patch.object(build_app_entry, 'tray_main', return_value=0) as tray, patch('peach.native_shell.main') as native:
            self.assertEqual(build_app_entry.main(['Peach.exe']), 0)
        tray.assert_called_once()
        native.assert_not_called()

    def test_bootstrap_port_does_not_override_saved_settings(self):
        from peach.settings_file import load_config
        (self.root / 'config.toml').write_text('[server]\nport = 8123\n', encoding='utf-8')
        config = load_config(environ={'PEACH_DATA_ROOT': str(self.root), 'PEACH_WINDOW_BOOTSTRAP_PORT': '8124'})
        self.assertEqual(config.server.port, 8123)

    def test_fresh_settings_use_isolated_bootstrap_port(self):
        from peach.settings_file import load_config
        config = load_config(environ={'PEACH_DATA_ROOT': str(self.root), 'PEACH_WINDOW_BOOTSTRAP_PORT': '8124'})
        self.assertEqual(config.server.port, 8124)
        self.assertFalse(config.present)

    def test_invalid_bootstrap_port_is_rejected(self):
        from peach.settings_file import load_config, SettingsFileError
        with self.assertRaises(SettingsFileError):
            load_config(environ={'PEACH_DATA_ROOT': str(self.root), 'PEACH_WINDOW_BOOTSTRAP_PORT': '70000'})

    def test_occupied_default_port_selects_an_os_assigned_port(self):
        import os
        from unittest.mock import Mock
        from peach.native_shell import prepare_environment
        reservation = Mock()
        reservation.bind.side_effect = [OSError('端口已占用'), None]
        reservation.getsockname.return_value = ('127.0.0.1', 12345)
        with patch.dict('os.environ', {'PEACH_DATA_ROOT': str(self.root), 'PEACH_WINDOW_BOOTSTRAP_PORT': ''}), \
                patch('peach.native_shell.socket.socket') as factory:
            factory.return_value.__enter__.return_value = reservation
            prepare_environment()
            self.assertEqual(os.environ['PEACH_WINDOW_BOOTSTRAP_PORT'], '12345')
        self.assertEqual(reservation.bind.call_count, 2)
