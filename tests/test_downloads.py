"""云下载的任务模型：磁力解析、状态机、幂等接管、拉黑、退避、停滞与落地登记。

远端一律是假 provider，账本是临时库。落地那几条用真实的推送发现服务与临时目录：
远端完成之后账本里有没有那个文件，走的是和推送通知同一段登记。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path, PureWindowsPath

from peach import downloads as dl
from peach import push_discovery as push
from peach import wants
from peach.follow_secrets import CredentialStore
from peach.repository import LedgerDatabase
from support.ledger import fresh_ledger

HASH = "c9e15763f722f23e98a29decdfae341b98d53056"
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=ABC-123"
CLOUD_LEDGER_ROOT = "B:\\"


class FakeProvider:
    """记下每一次调用；`statuses` 依次吐出，吐完就重复最后一个。"""

    def __init__(self, key="115"):
        self.key = key
        self.submitted: list[tuple[str, str]] = []
        self.cancelled: list[int] = []
        self.submit_result: dl.RemoteStatus | Exception = dl.RemoteStatus(dl.RUNNING, "r1", "ABC-123")
        self.statuses: list[dl.RemoteStatus | Exception] = [dl.RemoteStatus(dl.RUNNING, "r1", "ABC-123", 0.2)]
        self.is_landed = False

    def submit(self, magnet, target):
        self.submitted.append((magnet.info_hash, target))
        if isinstance(self.submit_result, Exception):
            raise self.submit_result
        return self.submit_result

    def status(self, task):
        item = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def landed(self, task):
        return self.is_landed

    def cancel(self, task):
        self.cancelled.append(task.id)


class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class MagnetTests(unittest.TestCase):
    def test_hex_and_base32_spellings_are_one_key(self):
        base32 = "ZHQVOY7XELZD5GFCTXWN7LRUDOMNKMCW"
        self.assertEqual(dl.normalise_info_hash(base32), HASH)
        self.assertEqual(dl.parse_magnet(f"magnet:?xt=urn:btih:{base32}").info_hash, HASH)
        self.assertEqual(dl.parse_magnet(HASH.upper()).info_hash, HASH)

    def test_the_display_name_comes_from_dn(self):
        magnet = dl.parse_magnet(MAGNET)
        self.assertEqual((magnet.info_hash, magnet.name, magnet.uri), (HASH, "ABC-123", MAGNET))

    def test_links_without_a_btih_are_refused(self):
        for bad in ("", "magnet:?dn=x", "magnet:?xt=urn:btmh:1220abcd", "https://example.com/a",
                    "abc"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dl.parse_magnet(bad)

    def test_remote_messages_are_sorted_into_the_nine_kinds(self):
        cases = {
            "errno 50038 涉嫌违规": ("rejected", True),
            "本月离线配额已用完": ("quota", False),
            "任务已存在": ("duplicate", False),
            "链接无效": ("invalid_link", False),
            "something else": ("rejected", False),
        }
        for message, (failure, block) in cases.items():
            with self.subTest(message=message):
                error = dl.classify_remote_message(message)
                self.assertEqual((error.failure, error.block), (failure, block))
        self.assertEqual(set(dl.FAILURE_KINDS), {
            "config", "quota", "rejected", "invalid_link", "no_source", "duplicate",
            "not_landed", "mismatch", "network"})
        self.assertEqual([key for key, kind in dl.FAILURE_KINDS.items() if kind.retryable],
                         ["network"])


class _Deployment(unittest.TestCase):
    """临时账本、一个 115 网盘来源和真实的推送发现服务。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = fresh_ledger(self.root)
        self.cloud = self.root / "cloud"
        (self.cloud / "云下载").mkdir(parents=True)
        if os.name == "nt":
            self.declared, self.mounts = {"115": (str(self.cloud),)}, {}
        else:
            self.declared, self.mounts = {"115": (CLOUD_LEDGER_ROOT,)}, {"115": (str(self.cloud),)}
        self.push = push.PushDiscoveryService(
            state_root=self.root / "state", secrets_root=self.root / "secrets", db_path=self.db,
            declared_roots=self.declared, mounts=self.mounts)
        self.addCleanup(self.push.stop)
        self.push.save({"enabled": False, "cloud": False, "watch_local": False, "prefixes": [
            {"prefix": "/115", "root": self.declared["115"][0]}]})
        self.provider = FakeProvider()
        self.clock = Clock()
        self.credentials = CredentialStore(self.root / "secrets")
        self.service = dl.DownloadService(
            database=LedgerDatabase(self.db), state_root=self.root / "state",
            credentials=self.credentials, providers=lambda key: self.provider,
            landing=dl.Landing(self.push), clock=self.clock)
        self.service.save_settings({"targets": {"115": "/115/云下载"}}, self.declared)

    def ledger(self, *parts) -> str:
        return str(PureWindowsPath(self.declared["115"][0]).joinpath(*parts))

    def submissions(self) -> list[tuple]:
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute(
                "SELECT outcome,failure,provider,origin FROM download_submission ORDER BY id").fetchall()
        finally:
            connection.close()

    def submit(self, **overrides):
        body = {"magnet": MAGNET, "provider": "115", "origin": "paste", **overrides}
        return dl.submit_offline_download(self.service, **body)

    def tick(self, seconds):
        self.clock.advance(seconds)
        return self.service.poll_once()


class SubmitTests(_Deployment):
    def test_a_submission_is_recorded_and_checked_after_ten_seconds(self):
        result = self.submit(code="ABC-123")
        self.assertEqual(result["outcome"], "submitted")
        task = result["task"]
        self.assertEqual((task["state"], task["info_hash"], task["target"], task["code"]),
                         (dl.SUBMITTED, HASH, "/115/云下载", "ABC-123"))
        self.assertEqual(self.provider.submitted, [(HASH, "/115/云下载")])
        self.assertEqual(self.tick(9), 0)
        self.assertEqual(self.tick(1), 1)
        self.assertEqual(self.service.store.get(task["id"]).state, dl.REMOTE_RUNNING)
        self.assertEqual(self.submissions(), [("submitted", None, "115", "paste")])

    def test_the_same_infohash_is_taken_over_instead_of_submitted_again(self):
        first = self.submit()
        second = self.submit(magnet=HASH, origin="wishlist:7")
        self.assertEqual(second["outcome"], "adopted")
        self.assertEqual(second["task"]["id"], first["task"]["id"])
        self.assertEqual(len(self.provider.submitted), 1)
        self.assertEqual([row[0] for row in self.submissions()], ["submitted", "adopted"])

    def test_a_task_already_in_the_remote_folder_is_adopted_without_spending_quota(self):
        self.provider.submit_result = dl.RemoteStatus(dl.RUNNING, "r9", "ABC-123", adopted=True)
        self.assertEqual(self.submit()["outcome"], "adopted_remote")

    def test_a_failed_task_can_be_submitted_again_and_keeps_one_row(self):
        self.provider.submit_result = dl.DownloadError("quota", "离线配额不够")
        failed = self.submit()
        self.assertEqual((failed["outcome"], failed["task"]["state"], failed["task"]["failure"]),
                         ("failed", dl.FAILED, "quota"))
        self.assertTrue(failed["task"]["resubmittable"])
        self.provider.submit_result = dl.RemoteStatus(dl.RUNNING, "r1", "ABC-123")
        again = self.submit()
        self.assertEqual((again["outcome"], again["task"]["id"]), ("submitted", failed["task"]["id"]))
        self.assertIsNone(again["task"]["failure"])
        self.assertEqual([row[:2] for row in self.submissions()],
                         [("failed", "quota"), ("submitted", None)])

    def test_a_blocked_infohash_is_refused_forever(self):
        self.provider.submit_result = dl.classify_remote_message("50038 违规")
        blocked = self.submit()
        self.assertTrue(blocked["task"]["blocked"])
        self.assertFalse(blocked["task"]["resubmittable"])
        self.assertEqual(self.submit()["outcome"], "refused")
        self.assertEqual(len(self.provider.submitted), 1)

    def test_bad_input_is_refused_before_anything_is_recorded(self):
        for overrides in ({"magnet": "not a magnet"}, {"provider": "thunder"},
                          {"target": "/"}, {"target": "/a/../b"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.submit(**overrides)
        self.assertEqual(self.submissions(), [])

    def test_the_reader_never_submits(self):
        self.service.available = False
        with self.assertRaisesRegex(ValueError, "只在允许修改数据库的设备上可用"):
            self.submit()


class PollingTests(_Deployment):
    def test_the_poll_interval_backs_off_and_caps(self):
        self.assertEqual([dl.poll_delay(n) for n in (0, 1, 2, 6, 40)], [10, 30, 60, 900, 900])
        task_id = self.submit()["task"]["id"]
        for _ in range(4):
            self.tick(dl.POLL_DELAYS[-1])
        task = self.service.store.get(task_id)
        self.assertEqual(task.checks, 4)
        self.assertEqual(dl.parse_iso(task.next_check_at) - self.clock(), dl.poll_delay(4))

    def test_a_network_blip_is_retried_and_other_failures_stop(self):
        task_id = self.submit()["task"]["id"]
        self.provider.statuses = [dl.DownloadError("network", "连不上"),
                                  dl.RemoteStatus(dl.ERROR, "r1", "ABC-123", 0.1, "种子无人做种")]
        self.tick(10)
        self.assertEqual(self.service.store.get(task_id).state, dl.SUBMITTED)
        self.tick(30)
        task = self.service.store.get(task_id)
        self.assertEqual((task.state, task.failure), (dl.FAILED, "rejected"))

    def test_a_block_seen_while_polling_also_blacklists(self):
        task_id = self.submit()["task"]["id"]
        self.provider.statuses = [dl.RemoteStatus(dl.ERROR, "r1", "", None, "50038")]
        self.tick(10)
        self.assertTrue(self.service.store.get(task_id).blocked_at)

    def test_a_task_that_never_finishes_stalls_after_the_wait_limit(self):
        self.service.save_settings({"targets": {"115": "/115/云下载"}, "wait_hours": 1},
                                   self.declared)
        task_id = self.submit()["task"]["id"]
        self.tick(10)
        self.tick(3600)
        task = self.service.store.get(task_id)
        self.assertEqual((task.state, task.failure), (dl.STALLED, "no_source"))

    def test_a_task_missing_from_the_list_waits_then_fails(self):
        task_id = self.submit()["task"]["id"]
        self.provider.statuses = [dl.RemoteStatus(dl.MISSING)]
        self.tick(10)
        self.assertEqual(self.service.store.get(task_id).state, dl.SUBMITTED)
        self.tick(dl.MISSING_GRACE)
        self.assertEqual(self.service.store.get(task_id).failure, "no_source")

    def test_a_task_missing_from_the_list_with_files_in_the_folder_counts_as_done(self):
        task_id = self.submit()["task"]["id"]
        self.provider.statuses = [dl.RemoteStatus(dl.MISSING)]
        self.provider.is_landed = True
        self.tick(10)
        self.assertEqual(self.service.store.get(task_id).state, dl.REMOTE_DONE)

    def test_cancelling_asks_the_remote_and_ends_tracking(self):
        task_id = self.submit()["task"]["id"]
        task = self.service.cancel(task_id)
        self.assertEqual((task.state, self.provider.cancelled), (dl.CANCELLED, [task_id]))
        self.assertEqual(self.tick(900), 0)
        with self.assertRaisesRegex(ValueError, "已经结束"):
            self.service.cancel(task_id)


class LandingTests(_Deployment):
    def finish(self):
        task_id = self.submit()["task"]["id"]
        self.provider.statuses = [dl.RemoteStatus(dl.DONE, "r1", "ABC-123", 1.0)]
        self.tick(10)
        self.assertEqual(self.service.store.get(task_id).state, dl.REMOTE_DONE)
        return task_id

    def test_a_folder_the_push_channel_missed_is_registered_after_the_grace(self):
        task_id = self.finish()
        folder = self.cloud / "云下载" / "ABC-123"
        folder.mkdir()
        (folder / "ABC-123.mp4").write_bytes(b"0" * 8)
        (folder / "ABC-123.nfo").write_bytes(b"x")
        self.tick(dl.LANDING_GRACE)
        task = self.service.store.get(task_id)
        self.assertEqual((task.state, task.ledger_path), (dl.INGESTED, self.ledger("云下载", "ABC-123")))
        connection = sqlite3.connect(self.db)
        try:
            paths = [row[0] for row in connection.execute("SELECT path FROM asset")]
        finally:
            connection.close()
        self.assertEqual(paths, [self.ledger("云下载", "ABC-123", "ABC-123.mp4")])
        self.assertEqual(task.asset_id is not None, True)

    def test_the_main_video_is_bound_instead_of_the_first_promo_file(self):
        task_id = self.submit(code="ABC-123")["task"]["id"]
        self.provider.statuses = [dl.RemoteStatus(dl.DONE, "r1", "ABC-123", 1.0)]
        self.tick(10)
        folder = self.cloud / "云下载" / "ABC-123"
        folder.mkdir()
        (folder / "manko.fun.mp4").write_bytes(b"ad")
        (folder / "ABC-123.mp4").write_bytes(b"main")
        self.tick(dl.LANDING_GRACE)
        task = self.service.store.get(task_id)
        with closing(sqlite3.connect(self.db)) as connection:
            name = connection.execute("SELECT name FROM asset WHERE id=?", (task.asset_id,)).fetchone()[0]
        self.assertEqual(name, "ABC-123.mp4")
        self.assertEqual(task.postprocess_state, dl.POSTPROCESS_PENDING)

    def test_folder_assets_include_attachments_without_treating_them_as_the_main_video(self):
        task_id = self.submit(code="ABC-123")["task"]["id"]
        self.provider.statuses = [dl.RemoteStatus(dl.DONE, "r1", "ABC-123", 1.0)]
        self.tick(10)
        folder = self.cloud / "云下载" / "ABC-123"
        folder.mkdir()
        (folder / "ABC-123.mp4").write_bytes(b"main")
        self.tick(dl.LANDING_GRACE)
        task = self.service.store.get(task_id)
        attachment = self.ledger("云下载", "ABC-123", "推广页.url")
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                "INSERT INTO asset(location,path,name,medium,size) VALUES(?,?,?,?,?)",
                ("115", attachment, "推广页.url", "other", 128))
            connection.commit()
        rows = self.service.store.assets_under("115", task.ledger_path)
        self.assertEqual({row["medium"] for row in rows}, {"video", "other"})
        self.assertEqual(self.service.store.asset_under("115", task.ledger_path, "ABC-123"),
                         task.asset_id)

    def test_a_wishlist_download_that_lands_marks_the_want_acquired(self):
        with closing(sqlite3.connect(self.db)) as connection:
            want_id = wants.add_code(connection, "ABC-123")["id"]
            connection.commit()
        task_id = self.submit(code="ABC-123", origin=f"wishlist:{want_id}")["task"]["id"]
        self.provider.statuses = [dl.RemoteStatus(dl.DONE, "r1", "ABC-123", 1.0)]
        self.tick(10)
        folder = self.cloud / "云下载" / "ABC-123"
        folder.mkdir()
        (folder / "ABC-123.mp4").write_bytes(b"0" * 8)
        self.tick(dl.LANDING_GRACE)
        task = self.service.store.get(task_id)
        self.assertEqual((task.state, task.origin), (dl.INGESTED, f"wishlist:{want_id}"))
        with closing(sqlite3.connect(self.db)) as connection:
            connection.row_factory = sqlite3.Row
            want = wants.get(connection, want_id)
        self.assertEqual((want["state"], want["acquired_asset_id"], want["acquired_source"]),
                         (wants.ACQUIRED, task.asset_id, wants.AUTO_SOURCE))

    def test_a_file_already_registered_by_the_push_channel_is_not_registered_twice(self):
        task_id = self.finish()
        folder = self.cloud / "云下载" / "ABC-123"
        folder.mkdir()
        (folder / "a.mp4").write_bytes(b"0" * 8)
        self.assertTrue(self.push.ingest_now("115", self.ledger("云下载", "ABC-123", "a.mp4")))
        calls = []
        original = self.push.ingest_now
        self.push.ingest_now = lambda *args: calls.append(args) or original(*args)
        self.tick(dl.LANDING_GRACE)
        self.assertEqual(self.service.store.get(task_id).state, dl.INGESTED)
        self.assertEqual(calls, [])

    def test_a_file_that_never_appears_ends_as_not_landed(self):
        task_id = self.finish()
        self.tick(dl.LANDING_GRACE)
        self.assertEqual(self.service.store.get(task_id).state, dl.REMOTE_DONE)
        self.tick(dl.LANDING_TIMEOUT)
        self.assertEqual(self.service.store.get(task_id).failure, "not_landed")

    def test_a_target_outside_the_cloud_prefixes_is_reported_not_guessed(self):
        self.push.save({"enabled": False, "prefixes": []})
        task_id = self.finish()
        self.tick(dl.LANDING_GRACE)
        task = self.service.store.get(task_id)
        self.assertEqual((task.state, task.failure), (dl.FAILED, "not_landed"))
        self.assertIn("云端路径前缀", task.failure_detail)


class SettingsTests(_Deployment):
    def test_settings_hold_no_credentials_and_check_the_pikpak_root(self):
        with self.assertRaisesRegex(ValueError, "不是已声明的 PikPak 媒体根"):
            self.service.save_settings({"pikpak_root": "Z:\\"}, self.declared)
        config = self.service.save_settings({
            "clouddrive_address": "127.0.0.1:19798", "targets": {"115": "115/云下载/"},
            "wait_hours": 48, "token": "secret-token"}, self.declared)
        self.assertEqual((config.clouddrive_address, config.targets, config.wait_hours),
                         ("http://127.0.0.1:19798", {"115": "/115/云下载"}, 48))
        stored = (self.root / "state" / dl.SETTINGS_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn("secret-token", stored)
        self.assertEqual(json.loads(stored)["targets"], {"115": "/115/云下载"})

    def test_an_address_with_a_path_or_credentials_is_refused(self):
        for bad in ("http://u:p@127.0.0.1:19798", "http://127.0.0.1:19798/api", "ftp://x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.service.save_settings({"clouddrive_address": bad}, self.declared)

    def test_the_snapshot_reports_which_providers_are_ready(self):
        self.service.save_settings({"clouddrive_address": "127.0.0.1"}, self.declared)
        self.credentials.save(dl.CLOUDDRIVE_CREDENTIAL, {"token": "t"})
        providers = {row["key"]: row["configured"] for row in self.service.snapshot()["providers"]}
        self.assertEqual(providers, {"115": True, "pikpak": False})


class CredentialSaveTests(unittest.TestCase):
    def test_saving_overwrites_the_local_copy_and_drops_empty_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CredentialStore(Path(tmp))
            store.save("download-pikpak", {"refresh_token": "a", "password": "p"})
            store.save("download-pikpak", {"refresh_token": "b", "password": ""})
            self.assertEqual(store.load("download-pikpak").values, {"refresh_token": "b"})


if __name__ == "__main__":
    unittest.main()
