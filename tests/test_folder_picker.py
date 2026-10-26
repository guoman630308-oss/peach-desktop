"""`peach.folder_picker`：由运行 Peach 的这台电脑弹系统文件夹对话框，替浏览器拿到绝对路径。

对话框本身没法在测试里弹，这里钉住的是命令行的形状、取消与失败的区分，以及一次只开一个。
"""
from __future__ import annotations

import base64
import subprocess
import unittest
from unittest import mock

from peach import folder_picker


class FakeRun:
    def __init__(self, stdout: bytes = b"", returncode: int = 0, stderr: bytes = b""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


class CommandTests(unittest.TestCase):
    def test_windows_uses_the_modern_folder_picker_through_the_built_in_powershell(self):
        """`powershell.exe` 每台 Windows 都有，pwsh 7 是另装的；脚本以 -EncodedCommand 传，不碰引号转义。

        对话框是 Shell 的 `IFileOpenDialog`（带地址栏），不是 Windows Forms 那块树形面板。
        """
        argv, env = folder_picker.command(r"D:\Media", platform="win32")
        self.assertEqual(argv[0], "powershell.exe")
        self.assertIn("-STA", argv, "COM 对话框要求单线程套间")
        self.assertEqual(argv[-2], "-EncodedCommand")
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        self.assertLess(script.index('SetThreadDpiAwarenessContext(new IntPtr(-4))'),
                        script.index('new FileOpenDialogRCW()'))
        self.assertIn('SetThreadDpiAwarenessContext(previousDpi)', script)
        self.assertIn('Guid("42f85136-db7e-439c-85f1-e4075d135fc8")', script, "IFileOpenDialog 的接口 ID")
        self.assertIn("dialog.SetOptions(0x8 | 0x20 | 0x40)", script, "FOS_NOCHANGEDIR、FOS_PICKFOLDERS、FOS_FORCEFILESYSTEM")
        self.assertNotIn("FolderBrowserDialog", script)
        self.assertIn("$env:PEACH_PICK_INITIAL", script)
        self.assertIn("dialog.Show(IntPtr.Zero)", script, "不挂所有者窗口：透明置顶的 Form 会把对话框一起藏起来")
        self.assertIn("SetWindowPos(window, new IntPtr(-1)", script, "后台服务进程没有前台权，对话框要自己置顶")
        self.assertNotIn("System.Windows.Forms", script)
        self.assertIn(f"Pick('{folder_picker.PROMPT}', $env:PEACH_PICK_INITIAL)", script)
        self.assertEqual(env, {"PEACH_PICK_INITIAL": r"D:\Media"})

    def test_macos_uses_applescript_choose_folder_and_escapes_the_initial_path(self):
        with mock.patch("os.path.isdir", return_value=True):
            argv, env = folder_picker.command('/Volumes/My "Disk"', platform="darwin")
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertTrue(argv[2].startswith("POSIX path of (choose folder with prompt"))
        self.assertIn('default location POSIX file "/Volumes/My \\"Disk\\""', argv[2])
        self.assertEqual(env, {})

    def test_a_missing_initial_folder_is_not_passed_to_applescript(self):
        """`choose folder` 收到不存在的 default location 会直接报错，还不如不给。"""
        with mock.patch("os.path.isdir", return_value=False):
            argv, _ = folder_picker.command("/gone", platform="darwin")
        self.assertNotIn("default location", argv[2])

    def test_other_platforms_say_so(self):
        with self.assertRaises(folder_picker.PickerUnavailable):
            folder_picker.command(None, platform="linux")


class PickTests(unittest.TestCase):
    def test_file_picker_returns_a_file_and_shares_the_dialog_lock(self):
        run = FakeRun(b'E:\\Media\\sample.mp4')
        self.assertEqual(folder_picker.pick_file(platform='win32', run=run), r'E:\Media\sample.mp4')
        script = base64.b64decode(run.calls[0][0][-1]).decode('utf-16-le')
        self.assertIn('dialog.SetOptions(0x8 | 0x40 | 0x1000)', script)
        argv, _ = folder_picker.command(None, platform='darwin', file=True)
        self.assertTrue(argv[2].startswith('POSIX path of (choose file with prompt'))
        with folder_picker._LOCK, self.assertRaises(folder_picker.PickerBusy):
            folder_picker.pick_file(platform='win32', run=run)

    def test_the_chosen_path_comes_back_stripped_and_the_initial_path_travels_by_environment(self):
        run = FakeRun(b"E:\\Movies\r\n")
        chosen = folder_picker.pick_folder(r"D:\Media", platform="win32", run=run)
        self.assertEqual(chosen, r"E:\Movies")
        (argv, kwargs), = run.calls
        self.assertEqual(argv[0], "powershell.exe")
        self.assertEqual(kwargs["env"]["PEACH_PICK_INITIAL"], r"D:\Media")
        self.assertTrue(kwargs["capture_output"])

    def test_cancelling_yields_none_on_both_platforms(self):
        self.assertIsNone(folder_picker.pick_folder(platform="win32", run=FakeRun(b"")))
        cancelled = FakeRun(b"", returncode=1, stderr=b"execution error: User canceled. (-128)\n")
        self.assertIsNone(folder_picker.pick_folder(platform="darwin", run=cancelled))

    def test_macos_paths_lose_the_trailing_slash(self):
        self.assertEqual(folder_picker.pick_folder(platform="darwin", run=FakeRun(b"/Volumes/Media/\n")),
                         "/Volumes/Media")
        self.assertEqual(folder_picker.pick_folder(platform="darwin", run=FakeRun(b"/\n")), "/")

    def test_a_dialog_that_fails_to_open_is_reported_not_swallowed(self):
        with self.assertRaises(folder_picker.PickerUnavailable):
            folder_picker.pick_folder(platform="win32", run=FakeRun(b"", returncode=1, stderr=b"boom"))

    def test_a_forgotten_dialog_does_not_hold_the_worker_forever(self):
        def hang(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        self.assertIsNone(folder_picker.pick_folder(platform="win32", run=hang))

    def test_only_one_dialog_opens_at_a_time(self):
        """系统对话框是模态的，第二个只会藏在第一个后面。"""
        with folder_picker._LOCK:
            with self.assertRaises(folder_picker.PickerBusy):
                folder_picker.pick_folder(platform="win32", run=FakeRun(b""))


if __name__ == "__main__":
    unittest.main()
