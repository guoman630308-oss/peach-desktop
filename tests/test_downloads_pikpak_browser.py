"""PikPak 用浏览器登录（ADR-0093）：假浏览器回 CDP `Runtime.evaluate` 的结果，验证读取、存储、清理与收尾。

真实浏览器与真实 PikPak 账号都不碰：浏览器换成记录调用的假对象，凭据存进测试自己的字典。
"""
from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path

from peach import downloads_pikpak_browser as pb
from peach.browser_transport import BrowserUnavailable

KEY = "credentials_YUMx5nI8ZU8Ap8pm"
CAPTURE = {"key": KEY, "access_token": "page-access", "refresh_token": "page-refresh",
           "expires_at": "2026-10-02T10:00:00.000Z", "sub": "user-9", "device": "0123456789abcdef0123456789abcdef"}


class FakeSocket:
    def __init__(self, owner):
        self.owner = owner

    def call(self, method, timeout=30.0, **params):
        self.owner.calls.append((method, params))
        return {}


class FakeBrowser:
    """`_Browser` 的替身：`replies` 依次是每次读凭据时的回包，可以是字符串、None 或要抛出的异常。"""

    def __init__(self, replies, *, alive=True, fail_start=None):
        self.replies = list(replies)
        self.calls: list[tuple[str, dict]] = []
        self.evaluated: list[str] = []
        self.closed = False
        self._alive = alive
        self.fail_start = fail_start
        self.socket = FakeSocket(self)
        self.read = threading.Event()

    def start(self):
        if self.fail_start:
            raise self.fail_start
        self.calls.append(("start", {}))

    def navigate(self, url, *, referrer=None):
        self.calls.append(("navigate", {"url": url}))

    def place(self, *, visible):
        self.calls.append(("place", {"visible": visible}))

    def alive(self):
        return self._alive and not self.closed

    def evaluate(self, expression, *, timeout=30.0):
        self.evaluated.append(expression)
        if expression is pb._FORGET_SCRIPT:
            return True
        self.read.set()
        reply = self.replies.pop(0) if self.replies else None
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class BrowserLoginTests(unittest.TestCase):
    def setUp(self):
        self.saved: list[dict] = []
        self.clock = Clock()
        self.woken = 0

    def make(self, browser: FakeBrowser, *, executable="C:/fake/chrome.exe", sleep=None, wait=600.0):
        made: list[tuple[str, Path]] = []

        def factory(path, profile):
            made.append((path, profile))
            return browser

        def wake():
            self.woken += 1
        login = pb.BrowserLogin(Path("C:/fake/secrets/browser-pikpak"), self.saved.append,
                                find=lambda: executable, browser=factory, clock=self.clock,
                                sleep=sleep or self.clock.sleep, wait_seconds=wait, on_done=wake)
        self.addCleanup(login.close)
        return login, made

    def finish(self, login: pb.BrowserLogin) -> dict:
        thread = login._thread
        assert thread is not None
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        return login.status()

    def test_the_page_credentials_are_stored_with_the_client_from_the_key_and_then_forgotten(self):
        browser = FakeBrowser([None, RuntimeError("Execution context was destroyed"), json.dumps(CAPTURE)])
        login, made = self.make(browser)
        with self.assertNoLogs(pb.LOGGER, level="WARNING"):
            self.assertEqual(login.start()["state"], pb.WAITING)
            status = self.finish(login)
        self.assertEqual(status, {"state": pb.DONE, "message": "已用浏览器登录 PikPak"})
        self.assertEqual(made[0][1], Path("C:/fake/secrets/browser-pikpak"))
        self.assertEqual(self.saved, [{
            "refresh_token": "page-refresh", "access_token": "page-access", "expires_at": "1790935200",
            "user_id": "user-9", "device_id": "0123456789abcdef0123456789abcdef",
            "client_id": "YUMx5nI8ZU8Ap8pm"}])
        methods = [method for method, _params in browser.calls]
        self.assertLess(methods.index("Page.addScriptToEvaluateOnNewDocument"), methods.index("navigate"))
        self.assertEqual(browser.calls[methods.index("navigate")][1]["url"], pb.LOGIN_URL)
        self.assertIn(("place", {"visible": True}), browser.calls)
        self.assertIs(browser.evaluated[-1], pb._FORGET_SCRIPT)
        self.assertTrue(browser.closed)
        self.assertEqual(self.woken, 1)
        self.assertNotIn("page-refresh", json.dumps(login.status()))

    def test_the_first_document_forgets_old_credentials_before_the_page_runs(self):
        """下次拉起时网页从未登录开始：不会拿旧 token 去刷新，Peach 也不会把旧 token 当新登录读回来。"""
        browser = FakeBrowser([json.dumps(CAPTURE)])
        login, _made = self.make(browser)
        login.start()
        self.finish(login)
        script = next(params["source"] for method, params in browser.calls
                      if method == "Page.addScriptToEvaluateOnNewDocument")
        self.assertIs(script, pb._FORGET_ON_FIRST_DOCUMENT)

    def test_closing_the_window_cancels_the_login(self):
        browser = FakeBrowser([None, ConnectionError("浏览器关闭了调试连接")])
        login, _made = self.make(browser)
        login.start()
        self.assertEqual(self.finish(login)["state"], pb.CANCELLED)
        self.assertEqual(self.saved, [])
        self.assertTrue(browser.closed)
        self.assertEqual(self.woken, 0)

    def test_an_exited_browser_process_cancels_the_login(self):
        browser = FakeBrowser([], alive=False)
        login, _made = self.make(browser)
        login.start()
        self.assertEqual(self.finish(login)["state"], pb.CANCELLED)

    def test_waiting_too_long_times_out_and_closes_the_window(self):
        browser = FakeBrowser([])
        login, _made = self.make(browser, wait=5.0)
        login.start()
        status = self.finish(login)
        self.assertEqual(status["state"], pb.TIMEOUT)
        self.assertIn("用浏览器登录", status["message"])
        self.assertTrue(browser.closed)

    def test_cancel_returns_at_once_and_the_thread_closes_the_window(self):
        browser = FakeBrowser([])
        login, _made = self.make(browser, sleep=lambda _seconds: time.sleep(0.01))
        login.start()
        self.assertTrue(browser.read.wait(5))
        self.assertEqual(login.cancel()["state"], pb.CANCELLED)
        self.assertEqual(self.finish(login)["state"], pb.CANCELLED)
        self.assertTrue(browser.closed)
        self.assertEqual(self.saved, [])

    def test_a_second_start_while_waiting_does_not_open_another_window(self):
        browser = FakeBrowser([])
        login, made = self.make(browser, sleep=lambda _seconds: time.sleep(0.01))
        login.start()
        self.assertTrue(browser.read.wait(5))
        self.assertEqual(login.start()["state"], pb.WAITING)
        self.assertEqual(len(made), 1)
        login.cancel()
        self.finish(login)

    def test_without_a_browser_the_start_is_refused(self):
        login, made = self.make(FakeBrowser([]), executable=None)
        self.assertFalse(login.available())
        with self.assertRaises(ValueError) as caught:
            login.start()
        self.assertIn("账号密码", str(caught.exception))
        self.assertEqual(made, [])

    def test_a_browser_that_does_not_start_is_a_failure_to_poll(self):
        browser = FakeBrowser([], fail_start=BrowserUnavailable("浏览器没有写出调试口"))
        login, _made = self.make(browser)
        login.start()
        status = self.finish(login)
        self.assertEqual(status["state"], pb.FAILED)
        self.assertIn("调试口", status["message"])


class ParseTests(unittest.TestCase):
    def test_a_multiple_account_key_still_yields_the_client(self):
        values = pb.parse_capture({**CAPTURE, "key": "credentials_YUMx5nI8ZU8Ap8pm@user-9"})
        self.assertEqual(values["client_id"], "YUMx5nI8ZU8Ap8pm")

    def test_missing_tokens_or_client_are_not_a_login(self):
        self.assertIsNone(pb.parse_capture({**CAPTURE, "refresh_token": ""}))
        self.assertIsNone(pb.parse_capture({**CAPTURE, "key": "credentials_"}))
        self.assertIsNone(pb.parse_capture({**CAPTURE, "key": "captcha_x"}))

    def test_a_missing_device_gets_a_fresh_one_and_an_unreadable_expiry_forces_a_refresh(self):
        with self.assertLogs(pb.LOGGER, level="WARNING") as logged:
            values = pb.parse_capture({**CAPTURE, "device": "", "expires_at": "soon"})
        self.assertEqual(len(values["device_id"]), 32)
        self.assertEqual(values["expires_at"], "0")
        self.assertNotIn("page-refresh", "\n".join(logged.output))

    def test_the_profile_is_its_own_directory_under_the_secrets_root(self):
        self.assertEqual(pb.profile_dir(Path("S:/secrets")), Path("S:/secrets/browser-pikpak"))


if __name__ == "__main__":
    unittest.main()
