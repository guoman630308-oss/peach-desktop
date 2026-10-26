"""把掉了的 SMB 共享挂回来。

随 `mount` 从 `platform` 一同拆出；patch 目标同样跟着代码走。
"""
import subprocess
import unittest
from unittest.mock import Mock, patch

from peach.mount import (
    mount_share,
    mount_share_command,
    share_credentials_command,
    share_credentials_present,
    share_url,
)


def _found(*_args, **_kwargs):
    """`security find-internet-password` 找到了记录。"""
    return Mock(returncode=0, stdout="", stderr="")


class ShareMountTests(unittest.TestCase):
    """把掉了的 SMB 共享挂回来。这条路径失败必须降级，不能抛、不能卡、不能弹框。"""

    def test_url_keeps_the_ordinary_case_readable(self):
        self.assertEqual(
            share_url("peach-writer.local", "peach-sync", "syncuser"),
            "smb://syncuser@peach-writer.local/peach-sync")

    def test_url_without_a_user_has_no_stray_at_sign(self):
        self.assertEqual(
            share_url("peach-writer.local", "peach-sync"),
            "smb://peach-writer.local/peach-sync")

    def test_url_percent_encodes_every_segment(self):
        # URL 在 AppleScript 里落在一对双引号中间：账号或主机带引号、反斜杠就能拼出
        # 第二条语句。编码之后这两个字符不可能出现在字面量里。
        url = share_url('pe"ach\\win', 'sh"are', 'us"er')
        self.assertNotIn('"', url)
        self.assertNotIn("\\", url)

    def test_macos_mounts_through_netfs_without_a_finder_window(self):
        with patch("peach.mount.sys.platform", "darwin"):
            command = mount_share_command("smb://syncuser@peach-writer.local/peach-sync")
        # `open smb://` 也走 NetFS、也能挂上，但会顺带弹一个 Finder 窗口；
        # 菜单栏那条动作不该在用户没看着的时候把窗口翻到前面来。
        self.assertEqual(command, [
            "osascript", "-e",
            'mount volume "smb://syncuser@peach-writer.local/peach-sync"'])

    def test_other_platforms_have_no_mount_path(self):
        runner = Mock()
        with patch("peach.mount.sys.platform", "linux"):
            self.assertIsNone(mount_share_command("smb://host/share"))
            self.assertFalse(mount_share("host", "share", "user", run=runner))
        runner.assert_not_called()

    def test_keychain_lookup_never_asks_for_the_password_itself(self):
        # 带 `-w` 就会把密码打到 stdout，于是密码要经过本进程。只判断记录在不在，
        # 不需要它，也不该碰它。
        command = share_credentials_command("peach-writer.local", "syncuser")
        self.assertNotIn("-w", command)
        self.assertEqual(command[:2], ["security", "find-internet-password"])
        self.assertIn("peach-writer.local", command)
        self.assertIn("syncuser", command)

    def test_a_missing_keychain_entry_fails_fast_instead_of_prompting(self):
        # 2026-08-27 实测：钥匙串里没有这台主机的记录时，NetFS 不报错，而是拉起
        # NetAuthAgent 弹认证框并一直等到超时——用户没点过密码框，却看见一个。
        runner = Mock(return_value=Mock(returncode=44, stdout="", stderr="not found"))
        with patch("peach.mount.sys.platform", "darwin"):
            self.assertFalse(mount_share("peach-writer.local", "peach-sync", "syncuser",
                                         run=runner))
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0][:2],
                         ["security", "find-internet-password"])

    def test_a_present_keychain_entry_goes_on_to_mount(self):
        runner = Mock(side_effect=[_found(), Mock(returncode=0, stdout="", stderr="")])
        with patch("peach.mount.sys.platform", "darwin"):
            self.assertTrue(mount_share("peach-writer.local", "peach-sync", "syncuser",
                                        run=runner))
        self.assertEqual(runner.call_args.args[0][0], "osascript")

    def test_an_anonymous_mount_skips_the_keychain_check(self):
        runner = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        with patch("peach.mount.sys.platform", "darwin"):
            self.assertTrue(mount_share("peach-writer.local", "peach-sync", run=runner))
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0][0], "osascript")

    def test_a_failed_keychain_lookup_is_false_not_an_exception(self):
        self.assertFalse(share_credentials_present(
            "peach-writer.local", "syncuser", run=Mock(side_effect=OSError("没有 security"))))

    def test_mount_is_bounded_by_a_timeout(self):
        # 认证框只是最常见的一种等待；网络半通时 SMB 协商同样会挂着。没有超时，
        # 托盘那个线程就永远停在这里，「同步 Ledger」再也点不动。
        runner = Mock(side_effect=[_found(), Mock(returncode=0, stdout="", stderr="")])
        with patch("peach.mount.sys.platform", "darwin"):
            self.assertTrue(mount_share("peach-writer.local", "peach-sync", "syncuser",
                                        run=runner))
        self.assertGreater(runner.call_args.kwargs["timeout"], 0)

    def test_every_mount_failure_is_false_not_an_exception(self):
        failures = (
            Mock(returncode=1, stdout="", stderr="No route to host"),
            subprocess.TimeoutExpired("osascript", 15),
            OSError("osascript 不存在"),
        )
        with patch("peach.mount.sys.platform", "darwin"):
            for failure in failures:
                with self.subTest(failure=failure):
                    self.assertFalse(mount_share(
                        "peach-writer.local", "peach-sync", "syncuser",
                        run=Mock(side_effect=[_found(), failure])))
