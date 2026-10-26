import os
import sys
import time
import unittest
from unittest.mock import patch

from peach.netwatch import NETWORK_CHANGE, NetworkChangeWatcher


class NetworkChangeWatcherTests(unittest.TestCase):
    def test_key_is_the_system_network_change_notification(self):
        self.assertEqual(NETWORK_CHANGE, b"com.apple.system.config.network_change")

    @unittest.skipUnless(sys.platform == "darwin", "Darwin 通知只在 macOS 上存在")
    def test_start_subscribes_and_stop_returns_promptly(self):
        """stop() 不能等到下一次网络事件才生效。

        select 只盯通知描述符的话，没有网络变化时线程会一直阻塞，退出要等到用户下次
        换网络——所以另开了一根管道专门用来叫醒它。
        """
        watcher = NetworkChangeWatcher(lambda: None)
        self.assertTrue(watcher.start())
        time.sleep(0.3)
        started = time.monotonic()
        watcher.stop()
        self.assertLess(time.monotonic() - started, 3.0)

    @unittest.skipUnless(sys.platform == "darwin", "Darwin 通知只在 macOS 上存在")
    def test_double_start_is_a_no_op(self):
        watcher = NetworkChangeWatcher(lambda: None)
        self.addCleanup(watcher.stop)
        self.assertTrue(watcher.start())
        self.assertFalse(watcher.start())

    @unittest.skipUnless(sys.platform == "darwin", "Darwin 通知只在 macOS 上存在")
    def test_a_failing_callback_does_not_kill_the_watcher(self):
        """回调挂掉不该带走整个托盘。"""
        calls = []
        notify_read, notify_write = os.pipe()
        self.addCleanup(os.close, notify_read)
        self.addCleanup(os.close, notify_write)

        class FakeLibSystem:
            @staticmethod
            def notify_register_file_descriptor(_key, descriptor, _flags, token):
                descriptor._obj.value = notify_read
                token._obj.value = 1
                return 0

            @staticmethod
            def notify_cancel(_token):
                return 0

        def boom():
            calls.append(1)
            raise RuntimeError("boom")

        watcher = NetworkChangeWatcher(boom)
        self.addCleanup(watcher.stop)
        with patch("peach.netwatch._libsystem", return_value=FakeLibSystem()), \
                self.assertLogs("peach.netwatch", level="ERROR") as captured:
            watcher.start()
            os.write(notify_write, b"ping")
            for _ in range(20):
                if calls:
                    break
                time.sleep(0.01)
            self.assertEqual(calls, [1])
            self.assertTrue(watcher._thread.is_alive())
            watcher.stop()
        self.assertIn("RuntimeError: boom", "\n".join(captured.output))

    @unittest.skipIf(sys.platform == "darwin", "非 macOS 才走这条")
    def test_unsupported_platform_reports_itself(self):
        watcher = NetworkChangeWatcher(lambda: None)
        self.assertFalse(watcher.supported)
        self.assertFalse(watcher.start())


if __name__ == "__main__":
    unittest.main()
