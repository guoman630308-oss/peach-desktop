"""本机播放器配置独立于账本，启动参数保持原文件路径。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from peach import local_player


class LocalPlayerTests(unittest.TestCase):
    def test_native_default_discovers_potplayer_and_can_switch_to_internal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            executable = root / "PotPlayerMini64.exe"
            executable.write_bytes(b"MZfixture")
            with patch.object(local_player, "platform", "win32"), \
                    patch.object(local_player.distribution, "embedded_window", return_value=True), \
                    patch.object(local_player, "discover", return_value=str(executable)):
                self.assertEqual(local_player.read(root)["mode"], "external")
                self.assertEqual(local_player.read(root)["label"], "PotPlayer")
                local_player.save(root, "internal", str(executable))
                self.assertEqual(local_player.read(root)["mode"], "internal")
                local_player.save(root, "external", str(executable))
                self.assertEqual(local_player.read(root)["executable"], str(executable))
            self.assertFalse((root / "database").exists())

    def test_launch_passes_the_source_as_one_argument_without_a_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            executable = root / "a player.exe"
            executable.write_bytes(b"MZfixture")
            source = root / "中文 & dollar $ video.mp4"
            with patch.object(local_player, "platform", "win32"), \
                    patch.object(local_player.subprocess, "Popen") as popen:
                local_player.launch({"executable": str(executable)}, source)
            self.assertEqual(popen.call_args.args[0], [str(executable), str(source)])
            self.assertFalse(popen.call_args.kwargs.get("shell", False))
            self.assertFalse(source.exists())

    def test_invalid_executables_and_malformed_modes_do_not_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            with patch.object(local_player, "platform", "win32"):
                for mode, executable in (([], ""), ("external", "relative.exe"),
                                         ("external", str(root / "missing.exe")), ("external", None)):
                    with self.subTest(mode=mode, executable=executable), self.assertRaises(ValueError):
                        local_player.save(root, mode, executable)
            self.assertFalse((root / "local-player.json").exists())

    def test_source_server_and_other_platforms_keep_internal_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for system in ("linux", "darwin", "win32"):
                with patch.object(local_player, "platform", system), \
                        patch.object(local_player.distribution, "embedded_window", return_value=False):
                    self.assertFalse(local_player.read(root)["available"])
