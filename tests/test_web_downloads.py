"""云下载的 Web 一侧：任务列表与提交取消的路由、设置块的形状与凭据去向。

应用用临时账本与临时凭据目录建，远端换成假 provider，不起 `lifespan`。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from peach import download_postprocess
from peach import downloads as dl
from peach import downloads_clouddrive, web_downloads
from peach.api import create_app
from peach.config import PeachSettings
from peach.push_discovery import CloudPrefix, PushDiscoveryConfig
from support.ledger import fresh_ledger

HASH = "c9e15763f722f23e98a29decdfae341b98d53056"


class FakeProvider:
    key = "115"

    def __init__(self):
        self.submitted = []
        self.cancelled = []

    def submit(self, magnet, target):
        self.submitted.append((magnet.info_hash, target))
        return dl.RemoteStatus(dl.RUNNING, "r1", magnet.name)

    def status(self, task):
        return dl.RemoteStatus(dl.RUNNING, "r1")

    def landed(self, task):
        return False

    def cancel(self, task):
        self.cancelled.append(task.id)


class FakeBrowserLogin:
    """`downloads_pikpak_browser.BrowserLogin` 的替身：不拉起浏览器，只记被调了几次。"""

    def __init__(self, available=True):
        self.is_available = available
        self.state = {"state": "idle", "message": ""}
        self.started = self.cancelled = 0

    def available(self):
        return self.is_available

    def status(self):
        return dict(self.state)

    def start(self):
        if not self.is_available:
            raise ValueError("这台电脑上没有找到 Chrome 或 Edge，用不了浏览器登录")
        self.started += 1
        self.state = {"state": "waiting", "message": "浏览器窗口已打开，在里面登录 PikPak"}
        return self.status()

    def cancel(self):
        self.cancelled += 1
        self.state = {"state": "cancelled", "message": "已取消浏览器登录"}
        return self.status()


class _App(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.app = create_app(PeachSettings(
            db_path=fresh_ledger(self.root), configured=True, token="",
            follow_state_root=self.root / "state", secrets_root=self.root / "secrets"))
        self.addCleanup(self.app.state.http_transport.close)
        self.service = self.app.state.downloads
        self.provider = FakeProvider()
        self.service.providers = lambda key: self.provider
        self.client = TestClient(self.app)

    def secrets(self) -> dict:
        folder = self.root / "secrets" / "follow"
        return {path.name: path.read_text(encoding="utf-8") for path in folder.glob("*.json")}


class RouteTests(_App):
    def test_the_contract_and_the_app_share_one_service(self):
        self.assertIs(self.app.state.web_contract.downloads, self.service)

    def test_a_pasted_magnet_is_submitted_listed_and_cancelled(self):
        response = self.client.post("/api/downloads", json={
            "magnet": f"magnet:?xt=urn:btih:{HASH}&dn=ABC-123", "provider": "115",
            "target": "/115/云下载", "code": "ABC-123"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["outcome"], body["task"]["origin"]), ("submitted", "paste"))
        listed = self.client.get("/api/downloads").json()
        self.assertEqual([task["info_hash"] for task in listed["tasks"]], [HASH])
        self.assertEqual([row["key"] for row in listed["providers"]], ["115", "pikpak"])
        cancelled = self.client.post("/api/downloads/cancel", json={"id": body["task"]["id"]})
        self.assertEqual(cancelled.json()["task"]["state"], dl.CANCELLED)
        self.assertEqual(self.provider.cancelled, [body["task"]["id"]])

    def test_bad_input_is_a_400_with_the_reason(self):
        response = self.client.post("/api/downloads", json={"magnet": "nope", "provider": "115",
                                                            "target": "/115/a"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("infohash", response.text)
        self.assertEqual(self.client.post("/api/downloads/cancel", json={}).status_code, 400)

    def test_failed_postprocess_can_be_requeued_without_resubmitting(self):
        result = self.service.submit(
            source=f"magnet:?xt=urn:btih:{HASH}&dn=ABC-123", provider="115",
            target="/115/云下载", code="ABC-123")
        result.task.state = dl.INGESTED
        result.task.postprocess_state = dl.POSTPROCESS_FAILED
        self.service.store.save(result.task)
        queued = []
        self.service.after_ingest = lambda task: queued.append(task.id)
        response = self.client.post("/api/downloads/postprocess", json={"id": result.task.id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["task"]["postprocess_state"], dl.POSTPROCESS_PENDING)
        self.assertEqual(queued, [result.task.id])

    def test_postprocess_enqueue_is_persisted_and_adopts_an_existing_queue_row(self):
        result = self.service.submit(
            source=f"magnet:?xt=urn:btih:{HASH}&dn=ABC-123", provider="115",
            target="/115/云下载", code="ABC-123")
        result.task.state = dl.INGESTED
        result.task.postprocess_state = dl.POSTPROCESS_PENDING
        self.service.store.save(result.task)
        contract = self.app.state.web_contract
        wake = mock.patch.object(contract.followups, "wake")
        wake.start()
        self.addCleanup(wake.stop)
        first = download_postprocess.enqueue(contract, result.task)
        self.assertIsNotNone(first)
        self.assertEqual(self.service.store.get(result.task.id).postprocess_state,
                         dl.POSTPROCESS_QUEUED)
        self.service.store.postprocess(result.task.id, dl.POSTPROCESS_PENDING)
        second = download_postprocess.enqueue(contract, self.service.store.get(result.task.id))
        self.assertEqual(second, first)
        self.assertEqual(self.service.store.get(result.task.id).postprocess_run_id, first)


class SettingsBlockTests(_App):
    """CloudDrive2 那一侧换成 `self.answer`；没有令牌时走真的 `check`，它在连网之前就停。"""

    def setUp(self):
        super().setUp()
        real = downloads_clouddrive.check
        self.checked = []
        self.answer = lambda address, target: downloads_clouddrive.empty_report(address)

        def check(address, token, target, *, hints=downloads_clouddrive.Hints()):
            if not token:
                return real(address, token, target, hints=hints)
            self.checked.append({"address": address, "token": token, "target": target, "hints": hints})
            return self.answer(address, target)

        patcher = mock.patch.object(downloads_clouddrive, "check", check)
        patcher.start()
        self.addCleanup(patcher.stop)

    def roots(self):
        return {"pikpak": ("P:\\",)}

    def suggest(self, path: str, exists: bool):
        def answer(address, target):
            report = downloads_clouddrive.empty_report(address)
            report["suggested_target"] = {"path": path, "exists": exists}
            if exists:
                report["folder"] = {"path": path, "can_offline": True, "cloud": "115open"}
            return report
        self.answer = answer

    def test_saving_with_a_blank_target_fills_in_the_suggested_folder_when_it_exists(self):
        self.suggest("/115open/云下载", True)
        payload = web_downloads.save_settings(self.service, {
            "clouddrive_address": "127.0.0.1:19798", "token": "cd2-secret", "targets": {"115": ""},
            "wait_hours": 24}, self.roots())
        self.assertEqual(self.checked[0]["target"], "")
        self.assertEqual(self.checked[0]["token"], "cd2-secret")
        self.assertEqual(payload["config"]["targets"], {"115": "/115open/云下载"})
        self.assertEqual(payload["config"]["wait_hours"], 24)
        self.assertEqual(payload["report"]["folder"]["path"], "/115open/云下载")
        self.assertNotIn("cd2-secret", json.dumps(payload, ensure_ascii=False))
        saved = json.loads((self.root / "state" / dl.SETTINGS_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(saved["targets"], {"115": "/115open/云下载"})
        self.assertEqual(self.service.config.targets, {"115": "/115open/云下载"})

    def test_saving_with_a_blank_target_keeps_it_blank_when_the_suggested_folder_is_missing(self):
        self.suggest("/115open/云下载", False)
        payload = web_downloads.save_settings(self.service, {
            "clouddrive_address": "127.0.0.1:19798", "token": "cd2-secret", "wait_hours": 24},
            self.roots())
        self.assertEqual(payload["config"]["targets"], {})
        self.assertEqual(payload["config"]["wait_hours"], 24)
        self.assertEqual(payload["report"]["suggested_target"], {"path": "/115open/云下载", "exists": False})

    def test_saving_checks_the_folder_the_user_filled_in_and_keeps_it(self):
        self.suggest("/115open/云下载", True)
        payload = web_downloads.save_settings(self.service, {
            "clouddrive_address": "127.0.0.1:19798", "token": "cd2-secret",
            "targets": {"115": "/115open/自己的"}, "pikpak_account": True}, self.roots())
        self.assertEqual(self.checked[0]["target"], "/115open/自己的")
        self.assertTrue(self.checked[0]["hints"].pikpak_account)
        self.assertEqual(payload["config"]["targets"], {"115": "/115open/自己的"})

    def test_saving_uses_the_stored_token_and_skips_the_check_without_one(self):
        payload = web_downloads.save_settings(self.service, {"clouddrive_address": "127.0.0.1:19798"},
                                              self.roots())
        self.assertIsNone(payload["report"])
        self.assertEqual(self.checked, [])
        self.service.credentials.save(dl.CLOUDDRIVE_CREDENTIAL, {"token": "stored"})
        payload = web_downloads.save_settings(self.service, {"clouddrive_address": "127.0.0.1:19798"},
                                              self.roots())
        self.assertEqual(self.checked[0]["token"], "stored")
        self.assertIsNotNone(payload["report"])
        cleared = web_downloads.save_settings(self.service, {"clear_token": True}, self.roots())
        self.assertIsNone(cleared["report"])

    def test_opening_the_page_checks_the_saved_configuration(self):
        web_downloads.save_settings(self.service, {
            "clouddrive_address": "127.0.0.1:19798", "token": "cd2-secret",
            "targets": {"115": "/115open/已保存"}}, self.roots())
        self.checked.clear()
        client = TestClient(self.app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1")
        response = client.get("/api/configuration/downloads/check")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()), set(downloads_clouddrive.empty_report("")))
        self.assertEqual((self.checked[0]["address"], self.checked[0]["token"], self.checked[0]["target"]),
                         ("http://127.0.0.1:19798", "cd2-secret", "/115open/已保存"))
        self.assertFalse(self.checked[0]["hints"].pikpak_account)

    def test_the_token_goes_to_the_local_credential_file_only(self):
        payload = web_downloads.save_settings(self.service, {
            "clouddrive_address": "127.0.0.1:19798", "token": "cd2-secret",
            "targets": {"115": "/115/云下载", "pikpak": "/云下载"}, "pikpak_root": "p:\\"},
            self.roots())
        self.assertTrue(payload["token_set"])
        self.assertNotIn("cd2-secret", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(payload["config"]["pikpak_root"], "P:\\")
        self.assertEqual(payload["pikpak_roots"], ["P:\\"])
        self.assertEqual(json.loads(self.secrets()["download-clouddrive2.json"]), {"token": "cd2-secret"})
        state = (self.root / "state" / dl.SETTINGS_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn("cd2-secret", state)

    def test_a_blank_token_keeps_the_saved_one_and_clear_removes_it(self):
        web_downloads.save_settings(self.service, {"token": "cd2-secret"}, self.roots())
        self.assertTrue(web_downloads.save_settings(self.service, {"token": ""}, self.roots())["token_set"])
        cleared = web_downloads.save_settings(self.service, {"clear_token": True}, self.roots())
        self.assertFalse(cleared["token_set"])

    def test_checking_without_a_token_reports_the_problem_without_calling_out(self):
        report = web_downloads.check_clouddrive(self.service, {"clouddrive_address": "127.0.0.1:1"})
        self.assertFalse(report["ok"])
        self.assertIn("API 令牌", report["problems"][0])
        self.assertEqual((report["suggested_target"], report["suggested_pikpak_root"]), (None, ""))

    def test_the_check_takes_the_push_prefixes_and_the_blanks_on_the_form(self):
        web_downloads.save_settings(self.service, {"targets": {"115": "/115open/已保存"}}, self.roots())
        prefixes = (CloudPrefix("/115open", "B:\\"),)
        self.service.landing.push_discovery.config = PushDiscoveryConfig(prefixes=prefixes)
        seen = {}

        def check(address, token, target, *, hints):
            seen.update(target=target, hints=hints)
            return downloads_clouddrive.empty_report(address)

        with mock.patch.object(downloads_clouddrive, "check", check):
            web_downloads.check_clouddrive(self.service, {
                "clouddrive_address": "127.0.0.1:19798", "token": "t", "target": "", "pikpak_root": ""})
            self.assertEqual((seen["target"], seen["hints"].prefixes, seen["hints"].pikpak_root),
                             ("", prefixes, ""))
            self.assertFalse(seen["hints"].pikpak_account)
            web_downloads.check_clouddrive(self.service, {"clouddrive_address": "127.0.0.1:19798"})
            self.assertEqual(seen["target"], "/115open/已保存")
            web_downloads.check_clouddrive(self.service, {"clouddrive_address": "127.0.0.1:19798",
                                                          "pikpak_account": True})
            self.assertTrue(seen["hints"].pikpak_account)
            self.service.credentials.save(dl.PIKPAK_CREDENTIAL, {"refresh_token": "rt", "username": "u"})
            web_downloads.check_clouddrive(self.service, {"clouddrive_address": "127.0.0.1:19798"})
            self.assertTrue(seen["hints"].pikpak_account)

    def test_creating_a_folder_needs_a_token_and_a_real_folder(self):
        with self.assertRaises(ValueError) as caught:
            web_downloads.create_clouddrive_folder(self.service, {
                "clouddrive_address": "127.0.0.1:1", "path": "/115open/云下载"})
        self.assertIn("API 令牌", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            web_downloads.create_clouddrive_folder(self.service, {
                "clouddrive_address": "127.0.0.1:1", "token": "t", "path": "/"})
        self.assertIn("根目录", str(caught.exception))

    def test_a_pikpak_captcha_comes_back_as_a_page_to_open(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"url": "https://user.mypikpak.com/captcha"})
        result = web_downloads.pikpak_login(
            self.service, {"username": "me@example.com", "password": "pw"}, self.roots(),
            transport=httpx.MockTransport(handler))
        self.assertEqual((result["ok"], result["captcha_url"]),
                         (False, "https://user.mypikpak.com/captcha"))
        self.assertFalse(result["settings"]["pikpak"]["logged_in"])
        self.assertNotIn("pw", self.secrets().get("download-pikpak.json", ""))

    def test_the_service_carries_its_own_browser_login_profile(self):
        from peach.downloads_pikpak_browser import BrowserLogin
        login = self.service.pikpak_browser
        self.assertIsInstance(login, BrowserLogin)
        self.assertEqual(login.profile, self.root / "secrets" / "browser-pikpak")

    def test_logging_out_removes_the_pikpak_credentials(self):
        self.service.credentials.save(dl.PIKPAK_CREDENTIAL, {"refresh_token": "rt", "username": "u"})
        self.assertTrue(web_downloads.settings_payload(self.service, {})["pikpak"]["logged_in"])
        self.assertFalse(web_downloads.pikpak_logout(self.service, {})["pikpak"]["logged_in"])
        self.assertNotIn("download-pikpak.json", self.secrets())


class BrowserLoginRouteTests(_App):
    """浏览器登录三条接口只回设置块：状态与有没有浏览器，不回 token。登录在后台等，页面轮询读接口。"""

    URL = "/api/configuration/downloads/pikpak/browser-login"

    def setUp(self):
        super().setUp()
        self.login = FakeBrowserLogin()
        self.service.pikpak_browser = self.login
        self.local = TestClient(self.app, base_url="http://127.0.0.1", client=("127.0.0.1", 123))

    def test_starting_polling_and_cancelling_go_through_the_settings_block(self):
        idle = self.local.get(self.URL)
        self.assertEqual(idle.status_code, 200, idle.text)
        self.assertEqual(idle.json()["pikpak_browser"], {"available": True, "state": "idle", "message": ""})
        started = self.local.post(self.URL).json()
        self.assertEqual((started["pikpak_browser"]["state"], self.login.started), ("waiting", 1))
        self.service.credentials.save(dl.PIKPAK_CREDENTIAL, {
            "refresh_token": "page-refresh", "access_token": "page-access", "client_id": "YUMx5nI8ZU8Ap8pm"})
        self.login.state = {"state": "done", "message": "已用浏览器登录 PikPak"}
        done = self.local.get(self.URL)
        self.assertEqual(done.json()["pikpak"], {"logged_in": True, "username": "", "remember": False,
                                                  "method": "browser"})
        self.assertNotIn("page-refresh", done.text)
        self.assertNotIn("page-access", done.text)
        cancelled = self.local.post(f"{self.URL}/cancel").json()
        self.assertEqual((cancelled["pikpak_browser"]["state"], self.login.cancelled), ("cancelled", 1))

    def test_a_machine_without_a_browser_gets_the_reason(self):
        self.login.is_available = False
        self.assertFalse(self.local.get(self.URL).json()["pikpak_browser"]["available"])
        response = self.local.post(self.URL)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Chrome 或 Edge", response.text)

    def test_a_read_only_side_does_not_open_a_login_window(self):
        self.service.available = False
        self.assertEqual(self.local.post(self.URL).status_code, 400)
        self.assertEqual(self.login.started, 0)

    def test_another_origin_cannot_open_the_window(self):
        response = self.local.post(self.URL, headers={"origin": "https://other.invalid"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.login.started, 0)


class ResourceIndexerRoutes(_App):
    URL = "/api/configuration/indexers"

    def setUp(self):
        super().setUp()
        self.local = TestClient(self.app, base_url="http://127.0.0.1", client=("127.0.0.1", 123))

    def test_credentials_are_local_and_origin_guarded(self):
        body = {"indexers": [{"key": "one", "name": "测试源", "url": "http://indexer.test/api",
                              "api_key": "private-test-key", "enabled": True}]}
        self.assertEqual(self.client.post(self.URL, json=body).status_code, 403)
        self.assertEqual(self.local.post(self.URL, json=body,
                                        headers={"origin": "https://other.invalid"}).status_code, 403)
        response = self.local.post(self.URL, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("private-test-key", response.text)
        self.assertTrue(self.local.get(self.URL).json()["indexers"][0]["api_key_set"])
        self.assertEqual(self.client.get(self.URL).status_code, 403)

    def test_search_endpoint_passes_saved_config_and_returns_candidates(self):
        from peach.resource_search import Indexers
        Indexers(self.service.credentials).save({"indexers": [{
            "key": "one", "name": "测试源", "url": "http://indexer.test/api", "enabled": True}]})
        with mock.patch("peach.web_resource_search.direct_search", return_value={
                "items": [], "warnings": [], "trace": [],
                "next_sources": [], "reference_only": []}) as direct, \
             mock.patch("peach.web_resource_search._SEARCH.run", return_value={
                "ok": True, "state": "ready", "items": [], "warnings": [], "error": ""}) as search:
            response = self.client.get("/api/resources/search?code=ABC-123&goal=chinese")
        self.assertEqual(response.status_code, 200, response.text)
        args = search.call_args.args
        self.assertEqual((args[0][0]["key"], args[1], args[2].goal), ("one", "ABC-123", "chinese"))
        self.assertEqual(direct.call_args.kwargs["goal"], "chinese")
        self.assertEqual(self.client.get("/api/resources/search?code=ABC-123&min_size=-1").status_code, 400)


if __name__ == "__main__":
    unittest.main()
