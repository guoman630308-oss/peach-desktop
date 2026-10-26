"""推送发现：去抖、写完判定、云端路径映射、鉴权拒收与单路径登记。

真正会伤到账本的是两件事：把半截文件当成品登记，和把一条云端路径拼成指向别处的账本
路径。这两条各有专门的用例，其余覆盖的是拒收面——密钥不符、来源不是局域网、通道没开。

账本口径的路径在 Windows 上是真实盘符目录，在 macOS 上是 `R:\\media` 加一张挂载表；
`_shape()` 按本机给出当前平台成立的那一种，两边跑的是同一批断言。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest import mock


from peach import push_discovery as push
from peach import routes_configuration
from peach import scan
from support.ledger import fresh_ledger

CLOUD_ROOT = "B:\\"
LOCAL_LEDGER_ROOT = r"R:\media"


def _wait_for(check, timeout=15.0, interval=0.05):
    """等一个后台线程把事情做完；超时就让调用方自己断言失败。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(interval)
    return check()


class CloudPathTests(unittest.TestCase):
    """云端路径只用前缀表加纯字符串切分换算，不进本机 pathlib。"""

    PREFIXES = (push.CloudPrefix("/115", CLOUD_ROOT),
                push.CloudPrefix("/115/影视", r"A:\影视"))

    def test_the_longest_matching_prefix_decides_the_declared_root(self):
        self.assertEqual(push.map_cloud_path("/115/纪录片/a.mp4", self.PREFIXES),
                         str(PureWindowsPath(CLOUD_ROOT, "纪录片", "a.mp4")))
        self.assertEqual(push.map_cloud_path("/115/影视/b.mp4", self.PREFIXES),
                         str(PureWindowsPath(r"A:\影视", "b.mp4")))

    def test_separators_and_repeated_slashes_are_normalised(self):
        for raw in ("115//纪录片//a.mp4", "\\115\\纪录片\\a.mp4"):
            with self.subTest(raw=raw):
                self.assertEqual(push.map_cloud_path(raw, self.PREFIXES),
                                 str(PureWindowsPath(CLOUD_ROOT, "纪录片", "a.mp4")))

    def test_a_path_that_could_escape_the_root_is_refused(self):
        for bad in ("/115/../secret.mp4", "/115/C:x.mp4", "/115/a\nb.mp4"):
            with self.subTest(bad=bad), self.assertRaises(push.CloudPathError):
                push.map_cloud_path(bad, self.PREFIXES)

    def test_an_unconfigured_prefix_and_the_prefix_itself_are_refused(self):
        for bad in ("/pikpak/a.mp4", "/115", "", "/"):
            with self.subTest(bad=bad), self.assertRaises(push.CloudPathError):
                push.map_cloud_path(bad, self.PREFIXES)


class NotificationTests(unittest.TestCase):
    """CloudDrive2 的载荷形状，含它把布尔渲染成字符串的那一档。"""

    def test_new_and_renamed_files_are_taken_and_deletions_are_not(self):
        paths = push.parse_notification({"device_name": "nas", "data": [
            {"action": "CREATE", "is_dir": "false", "source_file": "/115/a.mp4",
             "destination_file": ""},
            {"action": "rename", "is_dir": False, "source_file": "/115/b.mp4",
             "destination_file": "/115/c.mp4"},
            {"action": "delete", "is_dir": "false", "source_file": "/115/d.mp4"},
            {"action": "create", "is_dir": "true", "source_file": "/115/新目录"},
            {"action": "create", "is_dir": "false", "source_file": "  "},
        ]})
        self.assertEqual(paths, ["/115/a.mp4", "/115/c.mp4"])

    def test_a_payload_without_the_expected_shape_yields_nothing(self):
        for body in ({}, {"data": "x"}, {"data": [1, "a"]}, {"data": [{}]}):
            with self.subTest(body=body):
                self.assertEqual(push.parse_notification(body), [])


class CloudDriveConfigTests(unittest.TestCase):
    """页面给出的那段配置，是 CloudDrive2「配置内容」框要的整段 TOML。"""

    def test_the_block_parses_and_points_at_this_machine(self):
        config = tomllib.loads(push.clouddrive_config("https://192.0.2.10", "s3cret-token"))
        self.assertEqual(config["global_params"]["base_url"], "https://192.0.2.10")
        # 总开关关着的话，下面两节写什么都不会发出来。
        self.assertIs(config["global_params"]["enabled"], True)
        watcher = config["file_system_watcher"]
        self.assertIs(watcher["enabled"], True)
        self.assertEqual(watcher["method"], "POST")
        self.assertEqual(watcher["url"], "{base_url}" + push.WEBHOOK_PATH)
        self.assertEqual(watcher["headers"][push.SECRET_HEADER], "s3cret-token")
        # 挂载点通知一条路径也给不出，收下来只是空转一次队列。
        self.assertIs(config["mount_point_watcher"]["enabled"], False)
        # 两个 watcher 在 CloudDrive2 那边是同一个 5 字段结构，关着的那个也得写全：
        # 只写 `enabled = false` 时整份配置在它的 Webhooks 列表里标「无效」。
        for name in ("file_system_watcher", "mount_point_watcher"):
            self.assertEqual(set(config[name]), {"url", "method", "enabled", "headers", "body"}, name)
        # 默认模板里那行 `authorization = "basic usernamepassword"` 不能留：它是示例值，
        # 发出来只会在 Peach 的访问日志里留一串假凭据。
        self.assertNotIn("authorization", config["global_params"]["default_headers"])

    def test_a_trailing_slash_on_the_origin_does_not_double_up_the_path(self):
        config = tomllib.loads(push.clouddrive_config("https://peach.local/", "t"))
        self.assertEqual(config["global_params"]["base_url"], "https://peach.local")

    def test_the_body_it_declares_is_what_the_parser_reads(self):
        """配置里那段正文，按 CloudDrive2 代入占位符之后要能被本模块解析回路径。

        两处各写一份的话，改了正文模板而没改解析器（或者反过来）是静默的：推送照发，
        Peach 收下来解析出零条路径，看上去就是「推送没生效」。
        """
        body = tomllib.loads(push.clouddrive_config("https://h", "t"))["file_system_watcher"]["body"]
        for placeholder, value in (("{action}", "create"), ("{is_dir}", "false"),
                                   ("{source_file}", "/115/影视/a.mp4"),
                                   ("{destination_file}", "")):
            body = body.replace(placeholder, value)
        self.assertEqual(push.parse_notification(json.loads(body)), ["/115/影视/a.mp4"])

    def test_nothing_is_handed_out_before_there_is_an_address_and_a_secret(self):
        for origin, secret in (("", "t"), ("https://h", ""), ("", ""), ("  ", " ")):
            with self.subTest(origin=origin, secret=secret):
                self.assertEqual(push.clouddrive_config(origin, secret), "")


class ConfigPagePayloadTests(unittest.TestCase):
    """配置页拿到的那份载荷里，CloudDrive2 要推去的地址是怎么定下来的。"""

    @staticmethod
    def _request(*, tls=True, published=None, declared="192.0.2.10", port=443):
        state = SimpleNamespace(
            mdns=None if published is None else SimpleNamespace(address=published),
            settings=SimpleNamespace(tls_enabled=tls, mdns_address=declared, mdns_port=port))
        return SimpleNamespace(app=SimpleNamespace(state=state))

    def _payload(self, request, secret="tok"):
        return routes_configuration.push_discovery_payload(request, {"secret": secret})

    def test_the_address_is_the_one_this_machine_publishes_not_the_caller_origin(self):
        """配置页多半是从回环地址打开的，而 TLS 那条服务只绑局域网地址。

        照着这次请求的 origin 生成，抄进 CloudDrive2 的就是 `127.0.0.1`——同机也连不上，
        因为 443 根本没绑在回环上。
        """
        payload = self._payload(self._request(published="198.51.100.7"))
        self.assertEqual(payload["origin"], "https://198.51.100.7")
        self.assertIn('base_url = "https://198.51.100.7"', payload["config_toml"])

    def test_a_port_other_than_443_stays_in_the_address(self):
        payload = self._payload(self._request(port=8443))
        self.assertEqual(payload["origin"], "https://192.0.2.10:8443")

    def test_without_tls_or_an_address_the_block_is_withheld_rather_than_guessed(self):
        """HTTP 的地址发出去就是白发：80 口那条服务对写请求回 426，不替它转发。"""
        for request in (self._request(tls=False), self._request(declared="", port=443)):
            with self.subTest(request=request):
                payload = self._payload(request)
                self.assertEqual(payload["origin"], "")
                self.assertEqual(payload["config_toml"], "")

    def test_a_machine_that_never_published_an_address_falls_back_to_the_declared_one(self):
        payload = self._payload(self._request(published=None))
        self.assertEqual(payload["origin"], "https://192.0.2.10")


class IgnoreAndSourceTests(unittest.TestCase):
    def test_half_written_and_bookkeeping_files_never_enter_the_queue(self):
        for name in ("a.mp4.part", "b.mkv.!qb", "c.crdownload", "desktop.ini", ".DS_Store"):
            with self.subTest(name=name):
                self.assertTrue(push.ignored(name))
        self.assertFalse(push.ignored("正片.mp4"))

    def test_only_loopback_and_private_addresses_are_accepted(self):
        # 局域网那一档用 RFC 5737 的文档网段举例：`ipaddress` 把它判成 private，
        # 正是 `allowed_source` 看的那一位，写谁家的真实网段都不必要（`test_repo_hygiene`）。
        for good in ("127.0.0.1", "::1", "192.0.2.9", "198.51.100.4", "169.254.1.1"):
            with self.subTest(good=good):
                self.assertTrue(push.allowed_source(good))
        for bad in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "", "not-an-address"):
            with self.subTest(bad=bad):
                self.assertFalse(push.allowed_source(bad))


class QueueTests(unittest.TestCase):
    """去抖与写完判定。时钟由用例给，`tick` 一格一格走完，不真的等几秒。"""

    def setUp(self):
        self.sizes: dict[str, int | None] = {}
        self.ingested: list[str] = []
        self.queue = push.DiscoveryQueue(
            self._ingest, lambda _location, path: self.sizes.get(path),
            debounce=5.0, settle=4.0, timeout=100.0, max_pending=3)

    def _ingest(self, _location, path):
        self.ingested.append(path)
        return True

    def test_a_file_is_registered_only_after_its_size_stops_changing(self):
        self.sizes["p"] = 10
        self.queue.submit("local", "p", 0.0)
        self.queue.tick(4.0)
        self.assertEqual(self.ingested, [], "静置窗口没过，一次探测都不该发")
        self.queue.tick(5.0)
        self.assertEqual(self.ingested, [], "第一次探测只记下大小")
        self.sizes["p"] = 40
        self.queue.tick(9.0)
        self.assertEqual(self.ingested, [], "还在长大，接着等")
        self.queue.tick(13.0)
        self.assertEqual(self.ingested, ["p"])
        self.assertEqual(self.queue.snapshot()["pending"], 0)

    def test_a_repeated_event_restarts_the_quiet_window(self):
        self.sizes["p"] = 10
        self.queue.submit("local", "p", 0.0)
        self.queue.submit("local", "p", 3.0)
        self.queue.tick(5.0)
        self.assertEqual(self.queue.snapshot()["pending"], 1)
        self.queue.tick(8.0)
        self.queue.tick(12.0)
        self.assertEqual(self.ingested, ["p"])
        self.assertEqual(self.queue.snapshot()["submitted"], 2)

    def test_a_file_that_vanished_before_the_window_closed_is_dropped(self):
        self.queue.submit("local", "gone", 0.0)
        self.queue.tick(5.0)
        self.assertEqual(self.ingested, [])
        self.assertEqual(self.queue.snapshot()["missing"], 1)
        self.assertEqual(self.queue.snapshot()["pending"], 0)

    def test_an_event_storm_beyond_the_ceiling_is_counted_not_queued(self):
        for index in range(5):
            self.queue.submit("local", f"p{index}", 0.0)
        snapshot = self.queue.snapshot()
        self.assertEqual(snapshot["pending"], 3)
        self.assertEqual(snapshot["dropped"], 2, "溢出的那些交回给全量扫描，不是静默丢弃")

    def test_a_file_that_never_stops_growing_is_handed_back_to_the_full_scan(self):
        self.sizes["p"] = 1
        self.queue.submit("local", "p", 0.0)
        for moment in (5.0, 9.0, 13.0):
            self.sizes["p"] += 1
            self.queue.tick(moment)
        self.queue.tick(200.0)
        self.assertEqual(self.ingested, [])
        self.assertEqual(self.queue.snapshot()["timed_out"], 1)

    def test_one_failing_path_does_not_take_the_queue_down(self):
        def boom(_location, _path):
            raise OSError("数据库忙")

        queue = push.DiscoveryQueue(boom, lambda _location, _path: 1,
                                    debounce=0.0, settle=0.0)
        queue.submit("local", "p", 0.0)
        queue.tick(0.0)
        with self.assertLogs("peach.push_discovery", level="ERROR") as captured:
            queue.tick(0.0)
        self.assertIn("数据库忙", "\n".join(captured.output))
        self.assertEqual(queue.snapshot()["failed"], 1)
        self.assertIn("数据库忙", queue.snapshot()["last_error"])


class SecretTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.secret = push.WebhookSecret(Path(self.tmp.name) / "secrets")

    def test_an_absent_or_empty_secret_never_matches(self):
        self.assertFalse(self.secret.matches("anything"))
        value = self.secret.ensure()
        self.assertTrue(value)
        self.assertFalse(self.secret.matches(""))
        self.assertFalse(self.secret.matches(value + "x"))
        self.assertTrue(self.secret.matches(value))

    def test_rotating_invalidates_the_previous_value(self):
        first = self.secret.ensure()
        self.assertEqual(self.secret.ensure(), first, "已有一份就沿用，不每次换新")
        second = self.secret.rotate()
        self.assertNotEqual(first, second)
        self.assertFalse(self.secret.matches(first))
        self.assertTrue(self.secret.matches(second))


class _ServiceCase(unittest.TestCase):
    """两条通道共用的临时部署：一个本地来源、一个网盘来源，各一份真实目录。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = fresh_ledger(self.root)
        self.media = self.root / "media"
        self.cloud = self.root / "cloud"
        self.media.mkdir()
        self.cloud.mkdir()
        self.declared, self.mounts = self._shape()

    def _shape(self):
        if os.name == "nt":
            return {"local": (str(self.media),), "115": (str(self.cloud),)}, {}
        return ({"local": (LOCAL_LEDGER_ROOT,), "115": (CLOUD_ROOT,)},
                {"local": (str(self.media),), "115": (str(self.cloud),)})

    def ledger(self, location, *parts) -> str:
        return str(PureWindowsPath(self.declared[location][0]).joinpath(*parts))

    def rows(self) -> dict[str, tuple[str, int]]:
        connection = sqlite3.connect(self.db)
        try:
            return {row[0]: (row[1], row[2]) for row in connection.execute(
                "SELECT path,medium,size FROM asset")}
        finally:
            connection.close()

    def service(self, **overrides):
        made = push.PushDiscoveryService(
            state_root=self.root / "state", secrets_root=self.root / "secrets",
            db_path=self.db, declared_roots=self.declared, mounts=self.mounts,
            debounce=0.05, settle=0.05, **overrides)
        self.addCleanup(made.stop)
        return made


class IngestPathTests(_ServiceCase):
    """单条路径走的是扫描那段登记，不是第二套写法。"""

    def test_one_file_lands_as_one_row_with_the_ledger_shape(self):
        (self.media / "创作者").mkdir()
        (self.media / "创作者" / "a.mp4").write_bytes(b"0" * 12)
        result = scan.ingest_path(
            self.db, "local", self.ledger("local", "创作者", "a.mp4"),
            declared_roots=self.declared, mounts=self.mounts)
        self.assertTrue(result.found)
        self.assertEqual(self.rows(), {self.ledger("local", "创作者", "a.mp4"): ("video", 12)})

    def test_registering_the_same_path_twice_refreshes_instead_of_duplicating(self):
        (self.media / "a.mp4").write_bytes(b"0" * 4)
        path = self.ledger("local", "a.mp4")
        kwargs = {"declared_roots": self.declared, "mounts": self.mounts}
        scan.ingest_path(self.db, "local", path, **kwargs)
        (self.media / "a.mp4").write_bytes(b"0" * 9)
        scan.ingest_path(self.db, "local", path, **kwargs)
        self.assertEqual(self.rows(), {path: ("video", 9)})

    def test_a_subtitle_next_to_its_video_is_paired_in_the_same_pass(self):
        (self.media / "a.mp4").write_bytes(b"0" * 4)
        (self.media / "a.zh.srt").write_bytes(b"1" * 4)
        kwargs = {"declared_roots": self.declared, "mounts": self.mounts}
        scan.ingest_path(self.db, "local", self.ledger("local", "a.mp4"), **kwargs)
        result = scan.ingest_path(self.db, "local", self.ledger("local", "a.zh.srt"), **kwargs)
        self.assertEqual(result.subtitles, 1)
        connection = sqlite3.connect(self.db)
        try:
            track = connection.execute(
                "SELECT asset_subtitle.language FROM asset_subtitle "
                "JOIN asset ON asset.id=asset_subtitle.asset_id").fetchone()
        finally:
            connection.close()
        self.assertEqual(track[0], "zh")

    def test_a_scraper_sidecar_is_found_but_not_registered(self):
        folder = self.media / "ABC-123"
        (folder / "extrafanart").mkdir(parents=True)
        for name in ("ABC-123.mp4", "ABC-123.nfo", "poster.jpg", "extrafanart/1.jpg"):
            (folder / name).write_bytes(b"0" * 4)
        kwargs = {"declared_roots": self.declared, "mounts": self.mounts}
        for parts in (("ABC-123.nfo",), ("poster.jpg",), ("extrafanart", "1.jpg")):
            result = scan.ingest_path(self.db, "local", self.ledger("local", "ABC-123", *parts),
                                      **kwargs)
            self.assertEqual((result.found, result.sidecar), (True, True), parts)
        self.assertEqual(self.rows(), {})

    def test_a_slow_directory_listing_does_not_hold_the_ledger_write_lock(self):
        """挂载卡在列目录上时，同一账本的另一个写入者照样能在短超时内拿到写锁。"""
        (self.media / "a.mp4").write_bytes(b"0" * 4)
        entered, release = threading.Event(), threading.Event()
        listing = scan._directory_stats

        def slow_listing(directory):
            entered.set()
            release.wait(10)
            return listing(directory)

        outcome = {}

        def ingest():
            outcome["result"] = scan.ingest_path(
                self.db, "local", self.ledger("local", "a.mp4"),
                declared_roots=self.declared, mounts=self.mounts)

        with mock.patch.object(scan, "_directory_stats", side_effect=slow_listing):
            worker = threading.Thread(target=ingest)
            worker.start()
            try:
                self.assertTrue(entered.wait(10))
                other = sqlite3.connect(self.db, timeout=0.5)
                try:
                    other.execute("BEGIN IMMEDIATE")
                    other.execute("COMMIT")
                finally:
                    other.close()
            finally:
                release.set()
                worker.join(10)
        self.assertTrue(outcome["result"].found)
        self.assertEqual(self.rows(), {self.ledger("local", "a.mp4"): ("video", 4)})

    def test_the_ingest_connection_waits_as_long_as_the_shared_ledger_connection(self):
        (self.media / "a.mp4").write_bytes(b"0" * 4)
        seen = []
        reconcile = scan.wants.reconcile

        def spy(connection, ids):
            seen.append(connection.execute("PRAGMA busy_timeout").fetchone()[0])
            return reconcile(connection, ids)

        with mock.patch.object(scan.wants, "reconcile", side_effect=spy):
            scan.ingest_path(self.db, "local", self.ledger("local", "a.mp4"),
                             declared_roots=self.declared, mounts=self.mounts)
        self.assertEqual(seen, [30_000])

    def test_a_path_that_is_already_gone_writes_nothing(self):
        result = scan.ingest_path(
            self.db, "local", self.ledger("local", "没有这个文件.mp4"),
            declared_roots=self.declared, mounts=self.mounts)
        self.assertFalse(result.found)
        self.assertEqual(self.rows(), {})

    def test_a_path_outside_the_declared_root_is_refused_before_the_ledger(self):
        with self.assertRaises(scan.ScanTargetError):
            scan.ingest_path(self.db, "local", self.ledger("115", "a.mp4"),
                             declared_roots=self.declared, mounts=self.mounts)
        self.assertEqual(self.rows(), {})


class IngestNotifiesTests(_ServiceCase):
    def test_a_registered_file_tells_the_service_its_caches_are_stale(self):
        """入库走扫描自己的连接；不通知的话，统计与复核要等缓存过期才看见新文件。"""
        (self.media / "a.mp4").write_bytes(b"0" * 4)
        notified = []
        service = self.service(after_ingest=lambda: notified.append(True))
        self.assertTrue(service._ingest("local", self.ledger("local", "a.mp4")))
        self.assertFalse(service._ingest("local", self.ledger("local", "gone.mp4")))
        self.assertEqual(notified, [True])


class SettingsTests(_ServiceCase):
    def test_a_prefix_pointing_at_an_unknown_root_is_refused_on_save(self):
        service = self.service()
        with self.assertRaisesRegex(ValueError, "不是已声明的媒体根"):
            service.save({"enabled": True, "prefixes": [{"prefix": "/115", "root": "Z:\\"}]})
        self.assertFalse(service.config.enabled)

    def test_the_same_prefix_cannot_be_registered_twice(self):
        service = self.service()
        with self.assertRaisesRegex(ValueError, "不止一次"):
            service.save({"enabled": True, "prefixes": [
                {"prefix": "/115", "root": self.declared["115"][0]},
                {"prefix": "115/", "root": self.declared["115"][0]}]})

    def test_saving_turns_the_channel_on_and_hands_back_a_secret(self):
        service = self.service()
        state = service.save({"enabled": True, "watch_local": False, "cloud": True, "prefixes": [
            {"prefix": "/115", "root": self.declared["115"][0]}]})
        self.assertTrue(state["enabled"])
        self.assertTrue(state["secret"])
        self.assertEqual(state["endpoint"], push.WEBHOOK_PATH)
        stored = json.loads((self.root / "state" / push.SETTINGS_FILENAME).read_text(
            encoding="utf-8"))
        self.assertEqual(stored["prefixes"], [{"prefix": "/115", "root": self.declared["115"][0]}])

    def test_a_service_that_boots_with_the_cloud_channel_on_mints_the_missing_secret(self):
        """设置文件可能是拷来的，或者密钥文件被删过：开机就得补，否则 webhook 永远收不下。"""
        service = self.service()
        service.save({"enabled": True, "watch_local": False, "cloud": True, "prefixes": [
            {"prefix": "/115", "root": self.declared["115"][0]}]})
        service.stop()
        (self.root / "secrets" / push.SECRET_FILENAME).unlink()

        booted = self.service()
        self.assertFalse(booted.snapshot()["secret_set"])
        booted.start()
        self.addCleanup(booted.stop)
        self.assertTrue(booted.snapshot(reveal=True)["secret"])

    def test_the_reader_never_starts_a_channel(self):
        service = self.service(available=False)
        with self.assertRaisesRegex(ValueError, "只在允许修改数据库的设备上可用"):
            service.save({"enabled": True})
        service.start()
        self.assertFalse(service.local_channel.running)


class CloudChannelTests(_ServiceCase):
    def test_a_notified_cloud_path_becomes_a_ledger_row(self):
        service = self.service()
        service.save({"enabled": True, "watch_local": False, "cloud": True, "prefixes": [
            {"prefix": "/115", "root": self.declared["115"][0]}]})
        (self.cloud / "影视").mkdir()
        (self.cloud / "影视" / "a.mp4").write_bytes(b"0" * 6)
        self.assertEqual(service.submit_cloud("/115/影视/a.mp4"),
                         self.ledger("115", "影视", "a.mp4"))
        _wait_for(lambda: self.rows())
        self.assertEqual(self.rows(), {self.ledger("115", "影视", "a.mp4"): ("video", 6)})

    def test_a_cloud_path_outside_the_prefix_table_never_reaches_the_ledger(self):
        service = self.service()
        service.save({"enabled": True, "watch_local": False, "cloud": True, "prefixes": [
            {"prefix": "/115", "root": self.declared["115"][0]}]})
        with self.assertRaises(push.CloudPathError):
            service.submit_cloud("/pikpak/a.mp4")
        self.assertEqual(self.rows(), {})


class LocalChannelTests(_ServiceCase):
    def test_a_file_landing_in_a_watched_root_reaches_the_ledger(self):
        service = self.service()
        service.save({"enabled": True, "watch_local": True, "cloud": False, "prefixes": []})
        if not service.local_channel.running:
            self.skipTest(f"本机起不了文件监视：{service.local_channel.message}")
        (self.media / "落地.mp4").write_bytes(b"0" * 8)
        path = self.ledger("local", "落地.mp4")
        _wait_for(lambda: path in self.rows() and service.snapshot()["queue"]["ingested"] == 1)
        self.assertEqual(self.rows().get(path), ("video", 8))
        self.assertEqual(service.snapshot()["queue"]["ingested"], 1)

    def test_the_cloud_mounts_are_never_watched(self):
        # 只看选根这一步：监视器起不起得来是另一件事（缺 watchdog 时它报「不可用」），
        # 网盘来源不进本地通道的判据不该跟着那一步一起变红。
        service = self.service()
        roots = [(location, root) for location, root, _ in service._local_roots()]
        self.assertEqual(roots, [("local", self.declared["local"][0])])


class WebhookRouteTests(_ServiceCase):
    """端点的三道门：通道要开着、来源要是局域网、密钥要对上。"""

    def setUp(self):
        super().setUp()
        from peach.api import create_app
        from peach.config import PeachSettings
        self.app = create_app(PeachSettings(
            db_path=self.db, configured=True, token="",
            follow_state_root=self.root / "state", secrets_root=self.root / "secrets"))
        self.addCleanup(self.app.state.http_transport.close)
        self.service = self.service()
        self.app.state.push_discovery = self.service
        self.secret = self.service.save({
            "enabled": True, "watch_local": False, "cloud": True,
            "prefixes": [{"prefix": "/115", "root": self.declared["115"][0]}]})["secret"]
        (self.cloud / "a.mp4").write_bytes(b"0" * 5)

    def post(self, *, secret=None, client=("127.0.0.1", 9000), path="/115/a.mp4"):
        from fastapi.testclient import TestClient
        headers = {} if secret is None else {push.SECRET_HEADER: secret}
        # 不进 `with`：`lifespan` 会把这个进程里的后台通道全起一遍，而这一组用例验的是
        # 端点本身。连接来源由 `client` 给，三道门里的第二道就是按它判的。
        return TestClient(self.app, client=client).post(
            push.WEBHOOK_PATH, headers=headers, json={"data": [
                {"action": "create", "is_dir": "false", "source_file": path,
                 "destination_file": ""}]})

    def test_a_request_with_the_shared_secret_is_accepted(self):
        self.assertEqual(self.post(secret=self.secret).status_code, 204)
        path = self.ledger("115", "a.mp4")
        _wait_for(lambda: path in self.rows())
        self.assertEqual(self.rows().get(path), ("video", 5))

    def test_a_wrong_or_missing_secret_is_refused_without_admitting_the_endpoint(self):
        for supplied in (None, "", self.secret + "x"):
            with self.subTest(supplied=supplied):
                self.assertEqual(self.post(secret=supplied).status_code, 404)
        self.assertEqual(self.rows(), {})

    def test_a_request_from_outside_the_local_network_is_refused(self):
        self.assertEqual(self.post(secret=self.secret, client=("8.8.8.8", 40000)).status_code,
                         404)
        self.assertEqual(self.rows(), {})

    def test_the_endpoint_disappears_when_the_channel_is_off(self):
        self.service.save({"enabled": False})
        self.assertEqual(self.post(secret=self.secret).status_code, 404)
        self.assertEqual(self.rows(), {})

    def test_an_unmapped_cloud_path_is_taken_but_never_written(self):
        self.assertEqual(self.post(secret=self.secret, path="/pikpak/a.mp4").status_code, 204)
        self.assertEqual(self.rows(), {})
