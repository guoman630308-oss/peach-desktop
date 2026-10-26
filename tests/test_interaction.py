"""在文件管理器里定位源文件。

随 `interaction` 从 `platform` 一同拆出。patch 目标必须跟着代码走——打在
`peach.platform` 上不会报错，只是再也 patch 不到真正执行的那份，测试照样「通过」。
"""
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from peach.interaction import _windows_reveal, reveal_path


class RevealPathTests(unittest.TestCase):
    """在文件管理器里定位源文件。"""

    @unittest.skipUnless(os.name == "nt", "Windows Shell API only")
    def test_windows_uses_the_native_shell_selection_api(self):
        ole32, shell32 = Mock(), Mock()
        ole32.CoInitializeEx.return_value = 0

        def parse(_path, _bind, output, _attributes, _read_attributes):
            output._obj.value = 1234
            return 0

        shell32.SHParseDisplayName.side_effect = parse
        shell32.SHOpenFolderAndSelectItems.return_value = 0
        with patch("peach.interaction.ctypes.WinDLL",
                   side_effect=lambda name: ole32 if name == "ole32" else shell32):
            _windows_reveal(Path(r"B:\创作者 a\c,d.mp4"))
        self.assertEqual(shell32.SHParseDisplayName.call_args.args[0],
                         r"B:\创作者 a\c,d.mp4")
        self.assertEqual(shell32.SHOpenFolderAndSelectItems.call_args.args[1:],
                         (0, None, 0))
        ole32.CoTaskMemFree.assert_called_once()
        ole32.CoUninitialize.assert_called_once_with()

    def test_windows_reveal_delegates_to_the_native_shell(self):
        source = Path(r"B:\创作者 a\c,d.mp4")
        with (patch("peach.interaction.os.name", "nt"),
              patch("peach.interaction._windows_reveal") as reveal):
            self.assertTrue(reveal_path(source))
        reveal.assert_called_once_with(source)

    def test_macos_reveals_in_finder(self):
        source = Path("/Volumes/Media/media/a.mp4")
        with (patch("peach.interaction.os.name", "posix"),
              patch("peach.interaction.sys.platform", "darwin"),
              patch("peach.interaction.subprocess.Popen") as launch):
            self.assertTrue(reveal_path(source))
        launch.assert_called_once_with(["open", "-R", str(source)], close_fds=True)

    def test_unsupported_platform_returns_false_instead_of_guessing(self):
        with (patch("peach.interaction.os.name", "posix"),
              patch("peach.interaction.sys.platform", "linux")):
            self.assertFalse(reveal_path(Path("/srv/a.mp4")))
