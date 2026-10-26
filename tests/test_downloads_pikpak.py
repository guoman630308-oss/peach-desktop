"""PikPak 云下载：假传输上的登录、刷新轮换、人机验证、离线提交与对账。

断言落在请求形状与凭据去向上：refresh token 每次刷新都写回，密码只在勾选时保存，
要人机验证时把验证页交出来而不是吞掉。
"""
from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx

from peach import downloads as dl
from peach import downloads_pikpak as pk

HASH = "c9e15763f722f23e98a29decdfae341b98d53056"
MAGNET = dl.parse_magnet(f"magnet:?xt=urn:btih:{HASH}&dn=ABC-123")
TARGET = "/云下载"


class FakePikPak:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.captcha_url = ""
        self.refresh_count = 0
        self.expired_once = False
        self.tasks: list[dict] = []
        self.signin_error = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        body = json.loads(request.content) if request.content else {}
        route = (request.method, url.netloc, url.path)
        if route[2] == "/v1/shield/captcha/init":
            if self.captcha_url:
                return httpx.Response(200, json={"url": self.captcha_url})
            return httpx.Response(200, json={"captcha_token": f"ct-{body['action']}"})
        if route[2] == "/v1/auth/signin":
            if self.signin_error:
                return httpx.Response(400, json=self.signin_error)
            return httpx.Response(200, json=self._tokens())
        if route[2] == "/v1/auth/token":
            if body.get("refresh_token") != f"rt{self.refresh_count}":
                return httpx.Response(400, json={"error": "invalid_grant", "error_code": 4126})
            return httpx.Response(200, json=self._tokens())
        if self.expired_once:
            self.expired_once = False
            return httpx.Response(401, json={"error": "unauthenticated", "error_code": 16})
        if route == ("GET", "api-drive.mypikpak.com", "/drive/v1/files"):
            parent = parse_qs(url.query).get("parent_id", [""])[0]
            files = {"": [{"id": "f1", "name": "云下载", "kind": "drive#folder"}],
                     "f1": [{"id": "x", "name": "ABC-123", "kind": "drive#folder"}]}
            return httpx.Response(200, json={"files": files.get(parent, []), "next_page_token": ""})
        if route == ("POST", "api-drive.mypikpak.com", "/drive/v1/files"):
            task = {"id": "t1", "file_name": "ABC-123", "phase": "PHASE_TYPE_RUNNING",
                    "progress": 0, "params": {"url": body["url"]["url"]}}
            self.tasks.append(task)
            return httpx.Response(200, json={"task": task})
        if route == ("GET", "api-drive.mypikpak.com", "/drive/v1/tasks"):
            return httpx.Response(200, json={"tasks": self.tasks, "next_page_token": ""})
        if route == ("DELETE", "api-drive.mypikpak.com", "/drive/v1/tasks"):
            ids = parse_qs(url.query)["task_ids"]
            self.tasks = [task for task in self.tasks if task["id"] not in ids]
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": "not_found"})

    def _tokens(self):
        self.refresh_count += 1
        return {"access_token": f"at{self.refresh_count}", "refresh_token": f"rt{self.refresh_count}",
                "expires_in": 7200, "sub": "user-1"}

    def paths(self) -> list[str]:
        return [f"{request.method} {request.url.path}" for request in self.requests]


class _Case(unittest.TestCase):
    def setUp(self):
        self.fake = FakePikPak()
        self.transport = httpx.MockTransport(self.fake)
        self.stored: dict = {}

    def persist(self, values):
        self.stored = dict(values)

    def login(self, remember=False):
        return pk.login(lambda: self.stored, "me@example.com", "pw", remember=remember,
                        persist=self.persist, transport=self.transport)

    def provider(self):
        return pk.PikPakProvider(lambda: dict(self.stored), persist=self.persist,
                                 transport=self.transport)


class LoginTests(_Case):
    def test_signing_in_keeps_tokens_and_drops_the_password_unless_asked(self):
        self.assertEqual(self.login(), {"username": "me@example.com", "remember": False})
        self.assertEqual(self.stored["refresh_token"], "rt1")
        self.assertNotIn("password", self.stored)
        self.assertEqual(len(self.stored["device_id"]), 32)
        signin = json.loads(self.fake.requests[1].content)
        self.assertEqual((signin["username"], signin["captcha_token"]),
                         ("me@example.com", "ct-POST:https://user.mypikpak.com/v1/auth/signin"))
        self.login(remember=True)
        self.assertEqual(self.stored["password"], "pw")
        self.assertEqual(pk.account(self.stored),
                         {"logged_in": True, "username": "me@example.com", "remember": True,
                          "method": "password"})

    def test_a_captcha_hands_back_the_page_and_keeps_the_device(self):
        self.fake.captcha_url = "https://user.mypikpak.com/captcha?x=1"
        with self.assertRaises(pk.CaptchaRequired) as caught:
            self.login()
        self.assertEqual(caught.exception.url, self.fake.captcha_url)
        device = self.stored["device_id"]
        self.assertNotIn("refresh_token", self.stored)
        self.fake.captcha_url = ""
        self.login()
        self.assertEqual(self.stored["device_id"], device)

    def test_a_wrong_password_is_a_configuration_failure(self):
        self.fake.signin_error = {"error": "invalid_account_or_password", "error_code": 4022,
                                  "error_description": "账号或密码错误"}
        with self.assertRaises(dl.DownloadError) as caught:
            self.login()
        self.assertEqual(caught.exception.failure, "config")
        self.assertIn("登录失败：账号或密码错误", caught.exception.detail)

    def test_the_captcha_signature_matches_the_reference_algorithm(self):
        self.assertEqual(pk.captcha_sign("abc", "123"), "1.3b6c3d5e08b41cc77d291c082ba26e7d")


class DriveTests(_Case):
    def setUp(self):
        super().setUp()
        self.login()

    def task(self, **overrides) -> dl.DownloadTask:
        return dl.DownloadTask(1, HASH, "pikpak", MAGNET.uri, "ABC-123", TARGET, dl.SUBMITTED,
                               **overrides)

    def test_a_submission_resolves_the_folder_and_carries_a_captcha_token(self):
        status = self.provider().submit(MAGNET, TARGET)
        self.assertEqual((status.state, status.remote_id, status.adopted), (dl.RUNNING, "t1", False))
        post = [r for r in self.fake.requests if r.method == "POST" and r.url.path == "/drive/v1/files"][0]
        self.assertEqual(post.headers["X-Captcha-Token"], "ct-POST:/drive/v1/files")
        body = json.loads(post.content)
        self.assertEqual((body["parent_id"], body["url"]["url"], body["upload_type"]),
                         ("f1", MAGNET.uri, "UPLOAD_TYPE_URL"))

    def test_the_same_magnet_is_adopted_from_the_task_list(self):
        self.provider().submit(MAGNET, TARGET)
        status = self.provider().submit(MAGNET, TARGET)
        self.assertTrue(status.adopted)
        self.assertEqual(len(self.fake.tasks), 1)

    def test_a_missing_folder_is_reported(self):
        with self.assertRaises(dl.DownloadError) as caught:
            self.provider().submit(MAGNET, "/没有")
        self.assertEqual(caught.exception.failure, "config")

    def test_an_expired_access_token_is_refreshed_and_the_new_refresh_token_saved(self):
        self.fake.expired_once = True
        self.assertEqual(self.provider().status(self.task()).state, dl.MISSING)
        self.assertEqual(self.stored["refresh_token"], "rt2")
        self.assertIn("POST /v1/auth/token", self.fake.paths())

    def test_a_dead_refresh_token_without_a_saved_password_asks_to_sign_in_again(self):
        self.stored["refresh_token"] = "stale"
        self.stored["expires_at"] = "0"
        with self.assertRaises(dl.DownloadError) as caught:
            self.provider().status(self.task())
        self.assertEqual(caught.exception.failure, "config")
        self.assertIn("重新登录", caught.exception.detail)

    def test_a_dead_refresh_token_with_a_saved_password_signs_in_again(self):
        self.login(remember=True)
        self.stored["refresh_token"] = "stale"
        self.stored["expires_at"] = "0"
        self.provider().status(self.task())
        self.assertTrue(self.stored["refresh_token"].startswith("rt"))
        self.assertEqual(self.stored["password"], "pw")

    def test_phases_map_onto_remote_states(self):
        self.provider().submit(MAGNET, TARGET)
        self.fake.tasks[0].update(phase="PHASE_TYPE_ERROR", message="no seeds")
        status = self.provider().status(self.task(remote_id="t1"))
        self.assertEqual((status.state, status.message), (dl.ERROR, "no seeds"))
        self.fake.tasks[0].update(phase="PHASE_TYPE_COMPLETE")
        self.assertEqual(self.provider().status(self.task()).state, dl.DONE)
        self.fake.tasks[0].update(phase="SOMETHING_NEW")
        with self.assertRaises(dl.DownloadError) as caught:
            self.provider().status(self.task())
        self.assertIn("接口可能已经变了", caught.exception.detail)

    def test_landed_lists_the_target_folder_and_cancel_keeps_files(self):
        self.assertTrue(self.provider().landed(self.task()))
        self.provider().submit(MAGNET, TARGET)
        self.provider().cancel(self.task(remote_id="t1"))
        delete = [r for r in self.fake.requests if r.method == "DELETE"][0]
        self.assertEqual(parse_qs(delete.url.query.decode())["delete_files"], ["false"])
        self.assertEqual(self.fake.tasks, [])

    def test_without_a_login_nothing_is_sent(self):
        self.stored = {}
        sent = len(self.fake.requests)
        with self.assertRaises(dl.DownloadError):
            self.provider().submit(MAGNET, TARGET)
        self.assertEqual(len(self.fake.requests), sent)


class WebClientTests(_Case):
    """浏览器登录读到的 token 由网页端 client 签发：刷新、captcha/init 与签名都按网页端常量走。"""

    def setUp(self):
        super().setUp()
        self.fake.refresh_count = 1
        self.stored = {"refresh_token": "rt1", "access_token": "at1", "expires_at": "0", "user_id": "user-1",
                       "device_id": "webdevice", "client_id": pk.WEB.client_id}

    def task(self) -> dl.DownloadTask:
        return dl.DownloadTask(1, HASH, "pikpak", MAGNET.uri, "ABC-123", TARGET, dl.SUBMITTED)

    def bodies(self, path: str) -> list[dict]:
        return [json.loads(r.content) for r in self.fake.requests if r.url.path == path]

    def test_the_web_signature_matches_the_page_script(self):
        """期望值用网页脚本里那段 `reduce` 的写法在 Node 里算出：md5 链、固定时间戳、网页端 15 条盐。"""
        self.assertEqual(pk.captcha_sign("abc", pk.WEB.sign_timestamp, pk.WEB),
                         "1.e07351bd60bb92ce14b49dcf7273008f")

    def test_web_tokens_refresh_with_the_web_client_and_no_secret(self):
        self.assertEqual(self.provider().status(self.task()).state, dl.MISSING)
        refresh = [r for r in self.fake.requests if r.url.path == "/v1/auth/token"][0]
        body = json.loads(refresh.content)
        self.assertEqual(body["client_id"], "YUMx5nI8ZU8Ap8pm")
        self.assertNotIn("client_secret", body)
        self.assertEqual((refresh.headers["x-client-id"], refresh.headers["x-device-id"]),
                         ("YUMx5nI8ZU8Ap8pm", "webdevice"))
        self.assertEqual((self.stored["refresh_token"], self.stored["client_id"]), ("rt2", pk.WEB.client_id))

    def test_a_web_submission_signs_the_captcha_with_web_constants(self):
        self.stored["expires_at"] = "9999999999"
        self.provider().submit(MAGNET, TARGET)
        captcha = self.bodies("/v1/shield/captcha/init")[0]
        self.assertEqual(captcha["client_id"], pk.WEB.client_id)
        self.assertEqual(captcha["meta"]["timestamp"], "1790733736477")
        self.assertEqual((captcha["meta"]["client_version"], captcha["meta"]["package_name"]),
                         ("2.0.0", "mypikpak.com"))
        self.assertEqual(captcha["meta"]["captcha_sign"], pk.captcha_sign("webdevice", "1790733736477", pk.WEB))

    def test_a_rejected_web_refresh_names_the_reason_and_asks_for_the_browser(self):
        self.stored["refresh_token"] = "stale"
        with self.assertRaises(dl.DownloadError) as caught:
            self.provider().status(self.task())
        self.assertEqual(caught.exception.failure, "config")
        self.assertIn("用浏览器登录", caught.exception.detail)
        self.assertIn("invalid_grant", caught.exception.detail)
        self.assertNotIn("stale", caught.exception.detail)

    def test_a_rejected_web_signature_says_the_constants_may_have_changed(self):
        self.stored["expires_at"] = "9999999999"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/shield/captcha/init":
                return httpx.Response(400, json={"error": "invalid_argument", "error_description": "captcha_sign"})
            return self.fake(request)
        provider = pk.PikPakProvider(lambda: dict(self.stored), persist=self.persist,
                                     transport=httpx.MockTransport(handler))
        with self.assertRaises(dl.DownloadError) as caught:
            provider.submit(MAGNET, TARGET)
        self.assertIn("网页端的签名常量可能已经更新", caught.exception.detail)

    def test_an_unknown_client_refreshes_but_refuses_to_sign(self):
        self.stored.update(client_id="SomeNewClient", expires_at="9999999999")
        with self.assertRaises(dl.DownloadError) as caught:
            self.provider().submit(MAGNET, TARGET)
        self.assertIn("SomeNewClient", caught.exception.detail)
        self.assertNotIn("/v1/shield/captcha/init", [r.url.path for r in self.fake.requests])
        self.stored["expires_at"] = "0"
        self.provider().status(self.task())
        self.assertEqual(self.bodies("/v1/auth/token")[0]["client_id"], "SomeNewClient")

    def test_the_account_says_how_it_was_signed_in(self):
        self.assertEqual(pk.account(self.stored)["method"], "browser")
        self.assertEqual(pk.account({"refresh_token": "x"})["method"], "password")
        self.login()
        self.assertEqual(self.stored["client_id"], pk.ANDROID.client_id)
        self.assertEqual(pk.account(self.stored)["method"], "password")


if __name__ == "__main__":
    unittest.main()
