import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from peach import tray


class DiagnosticExportTests(unittest.TestCase):
    def make_tray(self):
        instance = object.__new__(tray.PeachTray)
        instance._diagnostics_lock = threading.Lock()
        instance.icon = Mock()
        return instance

    def test_export_returns_while_report_window_is_open_and_rejects_duplicate(self):
        instance = self.make_tray()
        entered = threading.Event()
        release = threading.Event()
        returned = threading.Event()
        path = Path('report.zip')

        def show(_path):
            entered.set()
            release.wait(5)

        def invoke():
            instance.export_diagnostics()
            returned.set()

        with patch('peach.support_bundle.save_bundle', return_value=path) as save, patch(
            'peach.tray.show_diagnostic_report', side_effect=show, create=True,
        ), patch('peach.tray.show_message', side_effect=lambda *_a, **_k: show(path)):
            caller = threading.Thread(target=invoke)
            caller.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertTrue(returned.wait(1), '托盘菜单回调必须能返回')
                instance.export_diagnostics()
                self.assertEqual(save.call_count, 1)
            finally:
                release.set()
                caller.join(3)
                with instance._diagnostics_lock:
                    pass

    def test_export_failure_releases_lock_and_reports_error(self):
        instance = self.make_tray()
        completed = threading.Event()
        with patch('peach.support_bundle.save_bundle', side_effect=OSError('disk unavailable')), patch(
            'peach.tray.show_message', side_effect=lambda *_a, **_k: completed.set(),
        ) as message:
            instance.export_diagnostics()
            self.assertTrue(completed.wait(3))
            with instance._diagnostics_lock:
                pass
        self.assertTrue(message.call_args.kwargs['error'])

    def test_open_failure_keeps_the_saved_report_path_available(self):
        instance = self.make_tray()
        completed = threading.Event()
        path = Path('saved-report.zip')
        with patch('peach.support_bundle.save_bundle', return_value=path), patch(
            'peach.tray.show_diagnostic_report', side_effect=OSError('no association'),
        ), patch('peach.tray.show_message', side_effect=lambda *_a, **_k: completed.set()) as message:
            instance.export_diagnostics()
            self.assertTrue(completed.wait(3))
            with instance._diagnostics_lock:
                pass
        self.assertIn(str(path), message.call_args.args[1])
        self.assertIn('诊断报告已保存', message.call_args.args[1])
        self.assertIn('no association', message.call_args.args[1])

    def test_dialog_module_requires_a_tray_restart(self):
        self.assertTrue(tray.tray_restart_required(('src/peach/tray_diagnostics.py',)))


@unittest.skipUnless(os.name == 'nt', '原生 Windows 对话框')
class NativeReportDialogTests(unittest.TestCase):
    def test_native_buttons_and_dismissal(self):
        for action in ('directory', 'file', 'close', 'window-close', 'escape', 'enter'):
            with self.subTest(action=action):
                result = subprocess.run(
                    [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()),
                     '--native-harness', action],
                    capture_output=True, text=True, encoding='utf-8', timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data['opened'], [action] if action in ('file', 'directory') else [])
                self.assertEqual(data['buttons'], ['打开目录', '打开文件', '关闭'])


def native_harness(action):
    """只向当前隔离进程创建的窗口投递消息。"""
    import ctypes
    import time
    from ctypes import wintypes
    from peach import tray_diagnostics as report_dialog

    user = ctypes.WinDLL('user32', use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumThreadWindows.argtypes = [wintypes.DWORD, enum_proc, wintypes.LPARAM]
    user.EnumThreadWindows.restype = wintypes.BOOL
    user.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetDlgItem.restype = wintypes.HWND
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.restype = ctypes.c_int
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    opened = []
    failures = []
    buttons = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / '诊断报告 with spaces.zip'
        path.touch()

        def run():
            try:
                report_dialog.show_report(path)
            except BaseException as error:
                failures.append(repr(error))

        def open_path(target):
            opened.append('file' if Path(target) == path else 'directory' if Path(target) == path.parent else 'unexpected')

        with patch.object(os, 'startfile', side_effect=open_path):
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            deadline = time.monotonic() + 8
            handles = []

            @enum_proc
            def find(hwnd, _data):
                if user.IsWindowVisible(hwnd) and user.GetDlgItem(hwnd, report_dialog.OPEN_FILE):
                    handles.append(hwnd)
                return True

            while worker.is_alive() and time.monotonic() < deadline and not handles:
                user.EnumThreadWindows(worker.native_id, find, 0)
                time.sleep(.02)
            if not handles:
                raise AssertionError(f'未取得原生报告窗口：{failures}')
            hwnd = handles[0]
            for identifier in (report_dialog.OPEN_DIRECTORY, report_dialog.OPEN_FILE, report_dialog.CLOSE):
                text = ctypes.create_unicode_buffer(64)
                user.GetWindowTextW(user.GetDlgItem(hwnd, identifier), text, len(text))
                buttons.append(text.value)
            if action == 'window-close':
                user.PostMessageW(hwnd, 0x10, 0, 0)
            elif action in ('escape', 'enter'):
                key = 0x1B if action == 'escape' else 0x0D
                user.PostMessageW(hwnd, 0x100, key, 0)
                user.PostMessageW(hwnd, 0x101, key, 0)
            else:
                identifier = {'directory': report_dialog.OPEN_DIRECTORY,
                              'file': report_dialog.OPEN_FILE, 'close': report_dialog.CLOSE}[action]
                user.PostMessageW(user.GetDlgItem(hwnd, identifier), 0xF5, 0, 0)  # BM_CLICK
            worker.join(4)
            if worker.is_alive() or failures:
                raise AssertionError(f'原生对话框未完成：{failures}')
    print(json.dumps({'opened': opened, 'buttons': buttons}, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--native-harness':
        native_harness(sys.argv[2])
    else:
        unittest.main()
