"""JAV 用户脚本请求、持久队列与真实云下载服务的隔离回归。"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from peach import downloads as dl
from peach.api import create_app
from peach.config import PROJECT_ROOT, PeachSettings
from peach.jav_browser import CREDENTIAL, JavBrowser, code_key, source_page, source_sites
from support.ledger import fresh_ledger

HASH = "c9e15763f722f23e98a29decdfae341b98d53056"
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=ABC-123"
PAGE = "https://missav.ws/cn/abc-123"


class BrowserTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = create_app(PeachSettings(
            db_path=fresh_ledger(self.root), configured=True, token="admin-secret",
            follow_state_root=self.root / "state", secrets_root=self.root / "secrets"))
        self.addCleanup(self.app.state.http_transport.close)
        self.service = self.app.state.jav_browser
        self.downloads = self.app.state.downloads
        self.remote = Mock()
        self.remote.submit.return_value = dl.RemoteStatus(dl.RUNNING, "remote-1", "ABC-123")
        self.downloads.providers = lambda key: self.remote
        self.downloads.config = dl.DownloadConfig("http://127.0.0.1:19798", {"115": "/115/Movies"})
        self.downloads.credentials.save(dl.CLOUDDRIVE_CREDENTIAL, {"token": "cloud-secret"})
        self.service.search = Mock(return_value={"items": [{"name": "ABC-123 1080p", "uri": MAGNET}]})
        self.client = TestClient(self.app, client=("127.0.0.1", 12345))
        self.headers = {"X-Token": "admin-secret"}
        response = self.configure("enable")
        self.assertEqual(response.status_code, 200, response.text)
        self.token = response.json()["token"]
        self.script_headers = {"Authorization": "Bearer " + self.token, "Origin": "https://missav.ws"}

    def configure(self, action, provider="115"):
        return self.client.post("/api/configuration/jav-browser", headers=self.headers,
                                json={"action": action, "provider": provider})

    def add(self, code="ABC-123", **extra):
        return self.client.post("/api/jav-browser", headers=self.script_headers,
                                json={"code": code, "source_url": PAGE, **extra})

    def status(self):
        return self.client.get("/api/jav-browser", params={"code": "ABC-123"}, headers=self.script_headers)

    def test_queued_request_searches_then_uses_the_cloud_download_contract(self):
        self.assertEqual(self.status().json()["state"], "idle")
        response = self.add()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "queued")
        self.service.search.assert_not_called()
        self.assertTrue(self.service.process_one())
        result = self.status().json()
        self.assertEqual(result["state"], "submitted")
        self.assertTrue(result["task_id"])
        self.remote.submit.assert_called_once()
        task = self.downloads.store.get(result["task_id"])
        self.assertEqual((task.provider, task.target, task.code, task.origin),
                         ("115", "/115/Movies", "ABC-123", "browser:" + PAGE))
        self.assertEqual(self.downloads.store.submissions(task.id)[0]["outcome"], "submitted")

    def test_alias_clicks_and_service_restart_keep_one_job_and_one_cloud_submission(self):
        self.add("abc123")
        self.add("ABC-0123")
        restored = JavBrowser(self.app.state.web_contract, search=self.service.search)
        self.assertEqual(restored.status("ABC-123")["state"], "queued")
        restored.process_one()
        self.add()
        self.assertTrue(self.service.process_one())
        self.assertFalse(self.service.process_one())
        self.remote.submit.assert_called_once()
        self.service.search.assert_called_once()

    def test_searching_job_is_recovered_as_queued(self):
        self.add()
        self.service.rows["ABC-123"]["state"] = "searching"
        self.service._save()
        restored = JavBrowser(self.app.state.web_contract, search=self.service.search)
        self.assertEqual(restored.status("ABC-123")["state"], "queued")

    def test_owned_video_is_not_queued_or_searched(self):
        with self.app.state.web_contract.database.write_transaction() as connection:
            connection.execute("INSERT INTO asset(path,name,location,medium,code) VALUES(?,?,?,?,?)",
                               ("R:\\Media\\ABC-123.mp4", "ABC-123.mp4", "local", "video", "ABC-123"))
        result = self.add().json()
        self.assertEqual(result["state"], "owned")
        self.assertTrue(result["asset_id"])
        self.assertFalse(self.service.process_one())
        self.service.search.assert_not_called()
        self.remote.submit.assert_not_called()

    def test_existing_download_by_code_is_adopted_before_search(self):
        task = dl.submit_offline_download(self.downloads, magnet=MAGNET, provider="115", code="ABC-123")["task"]
        result = self.add().json()
        self.assertEqual((result["state"], result["task_id"]), ("submitted", task["id"]))
        self.service.search.assert_not_called()
        self.remote.submit.assert_called_once()

    def test_candidates_require_matching_code_and_full_video(self):
        self.service.search.return_value = {"items": [
            {"name": "XYZ-999 4K", "uri": MAGNET}, {"name": "ABC-123 sample", "uri": MAGNET},
            {"name": "unknown", "uri": MAGNET}, {"name": "ABC-123", "uri": "https://bad.test/file.torrent"}]}
        self.add()
        self.service.process_one()
        self.assertEqual(self.status().json()["state"], "no_match")
        self.remote.submit.assert_not_called()

    def test_no_match_requires_an_explicit_retry(self):
        self.service.search.return_value = {"items": []}
        self.add()
        self.service.process_one()
        self.assertFalse(self.service.process_one())
        self.status()
        self.assertEqual(self.service.search.call_count, 1)
        self.assertEqual(self.add().json()["state"], "queued")
        self.service.process_one()
        self.assertEqual(self.service.search.call_count, 2)

    def test_cloud_failure_is_visible_and_a_manual_retry_uses_existing_task(self):
        self.remote.submit.side_effect = dl.DownloadError("config", "请更新网盘账号")
        self.add()
        self.service.process_one()
        failed = self.status().json()
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["message"], "请更新网盘账号")
        self.remote.submit.side_effect = None
        self.add()
        self.service.process_one()
        result = self.status().json()
        self.assertEqual((result["state"], result["task_id"]), ("submitted", failed["task_id"]))

    def test_ingested_and_cancelled_states_follow_the_download_task(self):
        self.add()
        self.service.process_one()
        task = self.downloads.store.get(self.status().json()["task_id"])
        task.state, task.asset_id = dl.INGESTED, 42
        self.downloads.store.save(task)
        self.assertEqual(self.status().json()["state"], "ingested")
        self.assertEqual(self.status().json()["asset_id"], 42)
        task.state = dl.CANCELLED
        self.downloads.store.save(task)
        self.assertEqual(self.status().json()["state"], "failed")

    def test_disable_and_rotation_revoke_tokens(self):
        rotated = self.configure("rotate").json()["token"]
        self.assertEqual(self.status().status_code, 401)
        self.assertNotEqual(rotated, self.token)
        self.script_headers["Authorization"] = "Bearer " + rotated
        self.assertEqual(self.status().status_code, 200)
        self.add()
        self.configure("disable")
        self.assertEqual(self.status().status_code, 401)
        self.assertFalse(self.service.process_one())
        self.assertEqual(self.service.rows["ABC-123"]["state"], "failed")
        self.remote.submit.assert_not_called()

    def test_disable_during_search_prevents_cloud_submission(self):
        self.add()
        started, finish = threading.Event(), threading.Event()
        def search(*args):
            started.set()
            if not finish.wait(5):
                raise RuntimeError("test search did not finish")
            return {"items": [{"name": "ABC-123", "uri": MAGNET}]}
        self.service.search = search
        worker = threading.Thread(target=self.service.process_one)
        worker.start()
        try:
            self.assertTrue(started.wait(5))
            self.configure("disable")
        finally:
            finish.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.remote.submit.assert_not_called()

    def test_missing_cloud_configuration_refuses_before_search(self):
        self.downloads.credentials.clear(dl.CLOUDDRIVE_CREDENTIAL)
        self.assertEqual(self.add().status_code, 400)
        self.assertEqual(self.configure("enable").status_code, 400)
        self.service.search.assert_not_called()

    def test_read_only_instance_cannot_accept_jobs(self):
        self.downloads.available = False
        self.assertEqual(self.add().status_code, 403)
        self.assertEqual(self.configure("enable").status_code, 400)
        self.assertFalse(self.service.process_one())

    def test_special_token_grants_no_other_api_or_settings_access(self):
        response = self.client.get("/api/downloads", headers=self.script_headers)
        self.assertEqual(response.status_code, 401)
        response = self.client.post("/api/configuration/jav-browser", headers=self.script_headers,
                                    json={"action": "disable"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/jav-browser", headers={"X-Token": "admin-secret"},
                                    json={"code": "ABC-123", "source_url": PAGE})
        self.assertEqual(response.status_code, 401)

    def test_settings_require_local_and_same_origin_calls(self):
        remote = TestClient(self.app, client=("192.0.2.10", 12345))
        self.assertEqual(remote.get("/api/configuration/jav-browser", headers=self.headers).status_code, 403)
        headers = {**self.headers, "Origin": "https://missav.ws"}
        response = self.client.post("/api/configuration/jav-browser", headers=headers,
                                    json={"action": "enable", "provider": "115"})
        self.assertEqual(response.status_code, 403)

    def test_credentials_are_scoped_and_never_returned_by_reads(self):
        stored = self.downloads.credentials.load(CREDENTIAL).values
        self.assertEqual(stored["token_hash"], hashlib.sha256(self.token.encode()).hexdigest())
        public = self.client.get("/api/configuration/jav-browser", headers=self.headers)
        self.assertEqual(public.status_code, 200)
        for secret in (self.token, stored["token_hash"], "cloud-secret"):
            self.assertNotIn(secret, public.text + self.status().text)
        self.assertEqual(public.headers["cache-control"], "no-store")

    def test_invalid_codes_and_source_pages_cannot_enqueue(self):
        for code in ("", "VIDEO-2025", "ABC-123 some title", "ABC-123 XYZ-999", "x" * 100):
            self.assertEqual(self.add(code).status_code, 400, code)
        for page in ("http://missav.ws/abc-123", "https://missav.ws.evil.test/abc-123",
                     PAGE + "?token=secret", PAGE + "#secret", "https://user:secret@missav.ws/a"):
            self.assertEqual(self.add(source_url=page).status_code, 400, page)
        self.assertFalse(self.service.rows)

    def test_fc2_code_and_supported_site_urls_use_existing_normalization(self):
        self.assertEqual(code_key("fc2ppv1234567"), "FC2-PPV-1234567")
        self.assertEqual(source_page("https://jable.tv/videos/abc-123/"), "https://jable.tv/videos/abc-123/")

    def test_search_errors_stop_without_automatic_resubmission(self):
        self.service.search.side_effect = RuntimeError("upstream unavailable")
        self.add()
        with self.assertLogs("peach.jav_browser", level="ERROR"):
            self.service.process_one()
        self.assertEqual(self.status().json()["state"], "failed")
        self.assertFalse(self.service.process_one())
        self.remote.submit.assert_not_called()

    def test_pikpak_selection_uses_the_saved_account_and_target(self):
        self.downloads.credentials.save(dl.PIKPAK_CREDENTIAL, {"refresh_token": "pikpak-secret"})
        self.downloads.config = dl.DownloadConfig(targets={"pikpak": "/Movies"}, pikpak_root="A:\\")
        response = self.configure("enable", "pikpak")
        self.assertEqual(response.status_code, 200, response.text)
        self.script_headers["Authorization"] = "Bearer " + response.json()["token"]
        self.add()
        self.service.process_one()
        task = self.downloads.store.get(self.status().json()["task_id"])
        self.assertEqual((task.provider, task.target), ("pikpak", "/Movies"))

    def test_rejected_search_reports_a_failure_instead_of_no_match(self):
        self.service.search.return_value = {"items": [], "state": "error", "error": "source unavailable"}
        self.add()
        self.service.process_one()
        self.assertEqual(self.status().json()["state"], "failed")

    def test_installer_is_available_without_an_admin_token(self):
        response = self.client.get("/integrations/peach-jav.user.js")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.text.startswith("// ==UserScript=="))
        self.assertIn("application/javascript", response.headers["content-type"])
        self.assertNotIn(self.token, response.text)
        self.assertNotIn("__PEACH_JAV_SITES__", response.text)
        self.assertNotIn("__PEACH_JAV_MATCHES__", response.text)
        for host in ("javdb.com", "javbus.com", "javlibrary.com", "dmm.co.jp"):
            self.assertIn(f"// @match        https://{host}/*", response.text)
        directory = PROJECT_ROOT / "web" / "integrations"
        release = json.loads((directory / "jav-userscript-release.json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), release["files"]["peach-jav.user.js"])
        self.assertEqual(hashlib.sha256((directory / "jav-sites.json").read_bytes()).hexdigest(),
                         release["files"]["jav-sites.json"])

    def test_shutdown_waits_for_search_and_leaves_a_resumable_job(self):
        self.add()
        started, finish = threading.Event(), threading.Event()
        def search(*args):
            started.set()
            if not finish.wait(5):
                raise RuntimeError("test search did not finish")
            return {"items": [{"name": "ABC-123", "uri": MAGNET}]}
        self.service.search = search
        self.service.start()
        stopper = threading.Thread(target=self.service.stop)
        try:
            self.assertTrue(started.wait(5))
            stopper.start()
            self.assertTrue(self.service._stop.wait(5))
        finally:
            finish.set()
            if stopper.ident is not None:
                stopper.join(5)
            self.service.stop()
        self.assertFalse(stopper.is_alive())
        self.assertEqual(self.service.status("ABC-123")["state"], "queued")
        self.remote.submit.assert_not_called()

    def test_another_entry_submitting_a_different_hash_during_lookup_is_adopted(self):
        self.add()
        candidate_uri = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=ABC-123"
        self.service.search.return_value = {"items": [{"name": "ABC-123", "uri": candidate_uri}]}
        original = self.service._existing
        checked, proceed = threading.Event(), threading.Event()
        lookup_count = 0
        def existing(code):
            nonlocal lookup_count
            result = original(code)
            lookup_count += 1
            if lookup_count == 2:
                checked.set()
                if not proceed.wait(5):
                    raise RuntimeError("test lookup did not resume")
            return result
        self.service._existing = existing
        worker = threading.Thread(target=self.service.process_one)
        worker.start()
        try:
            self.assertTrue(checked.wait(5))
            submitted = dl.submit_offline_download(self.downloads, magnet=MAGNET, provider="115", code="abc123")
        finally:
            proceed.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.status().json()["task_id"], submitted["task"]["id"])
        self.remote.submit.assert_called_once()
        self.assertEqual(self.downloads.store.recent()[0].info_hash, HASH)

    def test_shutdown_does_not_requeue_a_disabled_job(self):
        self.add()
        started, finish = threading.Event(), threading.Event()
        def search(*args):
            started.set()
            if not finish.wait(5):
                raise RuntimeError("test search did not finish")
            return {"items": [{"name": "ABC-123", "uri": MAGNET}]}
        self.service.search = search
        self.service.start()
        stopper = threading.Thread(target=self.service.stop)
        try:
            self.assertTrue(started.wait(5))
            self.configure("disable")
            stopper.start()
            self.assertTrue(self.service._stop.wait(5))
        finally:
            finish.set()
            if stopper.ident is not None:
                stopper.join(5)
            self.service.stop()
        self.assertEqual(self.service.status("ABC-123")["state"], "failed")
        self.remote.submit.assert_not_called()

    def test_database_and_official_source_pages_keep_only_registered_work_identifiers(self):
        pages = ("https://javdb.com/v/AbCd12", "https://www.javbus.com/ABC-123",
                 "https://www.javlibrary.com/cn/?v=javabc123", "https://video.dmm.co.jp/av/content/?id=ssis00057")
        for page in pages:
            with self.subTest(page=page):
                self.assertEqual(source_page(page), page)
        for query in ("v=javabc123&token=secret", "v=javabc123&v=javxyz456", "v=https%3A%2F%2Fevil.test"):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    source_page("https://www.javlibrary.com/cn/?" + query)

    def test_javdb_addition_uses_the_same_cloud_pipeline(self):
        self.assertEqual(self.add(source_url="https://javdb.com/v/AbCd12").json()["state"], "queued")
        self.service.process_one()
        task = self.downloads.store.get(self.status().json()["task_id"])
        self.assertEqual(task.origin, "browser:https://javdb.com/v/AbCd12")
        self.remote.submit.assert_called_once()

    def test_registered_mirror_and_online_source_use_the_cloud_pipeline(self):
        for page in ("https://missav123.com/cn/abc-123", "https://www.busfan.casa/ABC-123",
                     "https://javgo.to/zh/v/abc-123", "https://netflav5.com/video/abc-123",
                     "https://www.av01.media/video/abc-123"):
            with self.subTest(page=page):
                self.assertEqual(source_page(page), page)
        page = "https://netflav5.com/video/abc-123"
        self.assertEqual(self.add(source_url=page).json()["state"], "queued")
        self.service.process_one()
        task = self.downloads.store.get(self.status().json()["task_id"])
        self.assertEqual(task.origin, f"browser:{page}")
        self.remote.submit.assert_called_once()

    def test_site_inventory_exposes_automatic_and_selected_code_modes(self):
        sites = source_sites()
        self.assertGreaterEqual(len(sites), 50)
        by_id = {site["id"]: site for site in sites}
        self.assertEqual(by_id["javdb"]["mode"], "automatic")
        self.assertEqual(by_id["javlibrary"]["mode"], "automatic")
        self.assertTrue(any(site["mode"] == "selection" for site in sites))
        response = self.client.get("/api/configuration/jav-browser", headers=self.headers)
        self.assertEqual(len(response.json()["sites"]), len(sites))
