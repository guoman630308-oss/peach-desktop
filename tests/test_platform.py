import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from peach import settings_file
from peach.media import FilesystemBackend, MediaOffline, MediaUnavailable
from peach.platform import (
    MOUNTS_ENV,
    UNMAPPED_ROOT,
    is_unmapped,
    is_windows_path,
    location_mounts,
    location_roots,
    resolve_location,
    resolve_root,
    root_online,
    root_status,
    system_volume,
    translate_ledger_path,
    translate_roots,
    within_root,
)
from peach.repository import MediaAsset


POSIX_ONLY = unittest.skipIf(os.name == "nt", "路径翻译只在非 Windows 上发生")

#: 测试固定使用程序默认的数据库路径前缀，不依赖本机 config.toml。
LOCATIONS = {"local": (r"R:\media",), "115": ("B:/",), "pikpak": ("A:/",)}


def _roots(table):
    """测试可传单个字符串；设置层使用与数据库路径前缀按序对应的元组。"""
    return {key: ((value,) if value else ()) if isinstance(value, str) else tuple(value)
            for key, value in table.items()}


def with_media(mounts, locations=None):
    """把设置层的 `[media.locations]` / `[media.mounts]` 钉死成给定值。"""
    config = settings_file.load_config(environ={}, strict=False)
    fixed = replace(
        config, mounts=_roots(mounts),
        locations=_roots(LOCATIONS if locations is None else locations),
    )
    return patch.object(settings_file, "active", lambda: fixed)


class LocationResolutionTests(unittest.TestCase):
    """按数据库路径前缀判定来源，两个平台的行为一致。"""

    def test_windows_path_shape_is_recognised(self):
        self.assertTrue(is_windows_path(r"R:\Media\one.mp4"))
        self.assertTrue(is_windows_path("B:/one.mp4"))
        self.assertFalse(is_windows_path("/Volumes/Media/media/one.mp4"))
        self.assertFalse(is_windows_path("relative/one.mp4"))

    def test_path_under_a_declared_root_resolves_to_that_location(self):
        with with_media({}):
            self.assertEqual(
                resolve_location(r"R:\media\创作者\one.mp4"),
                ("local", ("创作者", "one.mp4")),
            )
            self.assertEqual(resolve_location("B:/x/one.mp4"), ("115", ("x", "one.mp4")))

    def test_declared_root_matching_ignores_case(self):
        """数据库中 `R:\\Media` 与配置中的 `R:\\media` 对应同一目录，匹配时忽略大小写。"""
        with with_media({}):
            self.assertEqual(resolve_location(r"R:\MEDIA\one.mp4")[0], "local")

    def test_the_longest_declared_root_wins(self):
        with with_media({}, {"whole": "R:/", "local": r"R:\media"}):
            self.assertEqual(resolve_location(r"R:\media\a.mp4"), ("local", ("a.mp4",)))
            self.assertEqual(
                resolve_location(r"R:\Resources\a.mp4"), ("whole", ("Resources", "a.mp4")))

    def test_path_outside_every_declared_root_has_no_location(self):
        with with_media({}):
            self.assertEqual(resolve_location(r"Z:\secret\one.mp4"), (None, ()))
            # 同一盘符下仍按目录区分：`R:\Resources` 不在 `R:\media` 中。
            self.assertIsNone(resolve_location(r"R:\Resources\Intake\one.mp4")[0])

    def test_declared_roots_are_read_from_the_settings_layer(self):
        with with_media({}):
            self.assertEqual(location_roots().get("local"), (r"R:\media",))
            self.assertNotIn("online", location_roots())

    def test_a_source_with_two_roots_resolves_each_to_its_own_index(self):
        roots = {"local": (r"R:\media", r"S:\more"), "115": ("B:/",)}
        self.assertEqual(resolve_root(r"R:\media\a.mp4", roots), ("local", 0, ("a.mp4",)))
        self.assertEqual(resolve_root(r"S:\more\x\b.mp4", roots), ("local", 1, ("x", "b.mp4")))
        self.assertEqual(resolve_root("B:/c.mp4", roots), ("115", 0, ("c.mp4",)))
        self.assertEqual(resolve_root(r"Z:\nope", roots), (None, -1, ()))

    @POSIX_ONLY
    def test_each_root_translates_to_the_mount_at_the_same_position(self):
        with with_media({"local": ["/Volumes/A", "/Volumes/B"]},
                        {"local": [r"R:\media", r"R:\media2"]}):
            self.assertEqual(translate_ledger_path(r"R:\media\one.mp4"), Path("/Volumes/A/one.mp4"))
            self.assertEqual(translate_ledger_path(r"R:\media2\two.mp4"), Path("/Volumes/B/two.mp4"))
        # 部分前缀没有对应的本机目录时按无法访问处理，不转换到其他目录。
        with with_media({"local": ["/Volumes/A"]}, {"local": [r"R:\media", r"R:\media2"]}):
            self.assertTrue(is_unmapped(translate_ledger_path(r"R:\media2\two.mp4")))


class MountSourceTests(unittest.TestCase):
    """挂载表按来源 ID 取自设置文件；`PEACH_MEDIA_MOUNTS` 仍然压过它。"""

    def test_empty_source_uses_the_settings_parser_shape(self):
        with with_media({"local": "/mnt/res", "115": "", "pikpak": ""}):
            with patch.dict("os.environ", {MOUNTS_ENV: ""}):
                self.assertEqual(settings_file.active().mounts["115"], ())
                self.assertEqual(location_mounts(), {"local": (Path("/mnt/res"),)})

    def test_settings_file_supplies_the_base_mapping(self):
        with with_media({"local": "/mnt/res"}):
            with patch.dict("os.environ", {MOUNTS_ENV: ""}):
                self.assertEqual(location_mounts(), {"local": (Path("/mnt/res"),)})

    def test_environment_still_wins_over_the_settings_file(self):
        with with_media({"local": "/mnt/res", "115": "/mnt/115"}):
            with patch.dict("os.environ", {MOUNTS_ENV: "local=/mnt/other"}):
                self.assertEqual(
                    location_mounts(),
                    {"local": (Path("/mnt/other"),), "115": (Path("/mnt/115"),)})

    def test_a_fresh_machine_has_no_mounts_at_all(self):
        """内建默认为空：没挂的来源按脱盘处理，绝不猜一个本机路径。"""
        with with_media({}):
            with patch.dict("os.environ", {MOUNTS_ENV: ""}):
                self.assertEqual(location_mounts(), {})


class TranslationTests(unittest.TestCase):
    @POSIX_ONLY
    def test_declared_root_is_rebased_onto_the_local_mount(self):
        with with_media({"local": "/mnt/res/media", "115": "/mnt/115"}):
            with patch.dict("os.environ", {MOUNTS_ENV: ""}):
                self.assertEqual(
                    translate_ledger_path(r"R:\media\创作者\one.mp4"),
                    Path("/mnt/res/media/创作者/one.mp4"),
                )
                self.assertEqual(
                    translate_ledger_path("B:/x/one.mp4"), Path("/mnt/115/x/one.mp4"))

    @POSIX_ONLY
    def test_unmapped_source_never_becomes_a_relative_path(self):
        """没有本机挂载目录的来源转换到 UNMAPPED_ROOT，避免读取当前目录下的同名文件。"""
        with with_media({"local": "/mnt/res/media"}):
            with patch.dict("os.environ", {MOUNTS_ENV: ""}):
                path = translate_ledger_path(r"Z:\secret\one.mp4")
        self.assertTrue(path.is_absolute())
        self.assertTrue(is_unmapped(path))
        self.assertTrue(str(path).startswith(str(UNMAPPED_ROOT)))

    @POSIX_ONLY
    def test_declared_but_unmounted_source_is_unmapped_too(self):
        """已配置路径前缀但没有本机挂载目录时，仍标为无法访问，不返回相对路径。"""
        with with_media({}):
            with patch.dict("os.environ", {MOUNTS_ENV: ""}):
                self.assertTrue(is_unmapped(translate_ledger_path(r"R:\media\one.mp4")))

    @POSIX_ONLY
    def test_unmapped_roots_are_dropped_from_authorisation(self):
        with with_media({"local": "/mnt/res/media", "pikpak": "", "115": ""}):
            with patch.dict("os.environ", {MOUNTS_ENV: ""}):
                roots = translate_roots((r"R:\media", "B:/", "A:/"))
        self.assertEqual(roots, (Path("/mnt/res/media"),))

    def test_posix_path_passes_through_untouched(self):
        self.assertEqual(translate_ledger_path("/tmp/one.mp4"), Path("/tmp/one.mp4"))

    def test_system_volume_is_the_disk_gate_target(self):
        self.assertEqual(system_volume(), Path("C:/") if os.name == "nt" else Path("/"))


class RootBoundaryTests(unittest.TestCase):
    def test_within_root_accepts_exact_and_nested(self):
        root = Path("/mnt/res/media")
        self.assertTrue(within_root(root, root))
        self.assertTrue(within_root(root / "a" / "b.mp4", root))

    def test_within_root_rejects_sibling_and_shorter_paths(self):
        root = Path("/mnt/res/media")
        self.assertFalse(within_root(Path("/mnt/res/other/b.mp4"), root))
        self.assertFalse(within_root(Path("/mnt"), root))

    def test_within_root_survives_case_only_differences_on_insensitive_mounts(self):
        """账本写 `R:\\Media`，授权根是 `.../media`；大小写不敏感的挂载必须仍然放行。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "media"
            root.mkdir()
            shouting = Path(tmp).resolve() / "MEDIA" / "one.mp4"
            insensitive = (Path(tmp).resolve() / "MEDIA").is_dir()
            self.assertEqual(within_root(shouting, root), insensitive)

    def test_root_online_needs_a_readable_mount_not_just_a_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(root_online(Path(tmp)))
        self.assertFalse(root_online(Path(tmp)))
        self.assertFalse(root_online(UNMAPPED_ROOT))


class OfflineModeTests(unittest.TestCase):
    """脱盘是来源级判定：一个来源不在，不影响其他来源上的资产。"""

    def _backend(self, tmp: Path) -> FilesystemBackend:
        return FilesystemBackend([tmp], tmp / "snapshots")

    @POSIX_ONLY
    def test_asset_on_an_unmapped_source_reports_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._backend(Path(tmp).resolve())
            with with_media({"local": "/mnt/res/media"}):
                with self.assertRaises(MediaOffline) as caught:
                    backend.file_for(MediaAsset(1, r"Z:\video\one.mp4", None))
        self.assertEqual(caught.exception.source, "Z:")
        self.assertEqual(caught.exception.asset_id, 1)

    @POSIX_ONLY
    def test_asset_on_a_detached_but_mapped_source_reports_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            detached = Path(tmp).resolve() / "gone"
            backend = FilesystemBackend([detached], Path(tmp).resolve() / "snapshots")
            with with_media({"local": str(detached)}):
                with self.assertRaises(MediaOffline):
                    backend.file_for(MediaAsset(2, r"R:\media\video\one.mp4", None))

    @POSIX_ONLY
    def test_missing_file_on_a_live_source_is_not_offline(self):
        """单个文件丢失和整个来源不在必须分开报，否则前端会误显示脱盘模式。"""
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp).resolve()
            backend = FilesystemBackend([live], live / "snapshots")
            with with_media({"local": str(live)}):
                with self.assertRaises(MediaUnavailable) as caught:
                    backend.file_for(MediaAsset(3, r"R:\media\nope.mp4", None))
        self.assertNotIsInstance(caught.exception, MediaOffline)

    def test_source_status_reports_each_root_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp).resolve()
            dead = live / "gone"
            status = FilesystemBackend([live, dead], live / "snapshots").source_status()
        self.assertEqual([row["online"] for row in status], [True, False])


class MountReachabilityTests(unittest.TestCase):
    def setUp(self):
        from peach.mount_reachability import MountReachability, MountRoot
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.now = 0.0
        self.calls = []
        self.answer = "ok"

        def probe(path):
            self.calls.append(path)
            return self.answer

        self.monitor = MountReachability([MountRoot("local", "本地磁盘", "R:/media", self.root)],
                                         probe=probe, clock=lambda: self.now, retry_delay=0)
        self.addCleanup(self.monitor.stop)

    def wait_state(self, expected):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.monitor.tick()
            if self.monitor.summary()["sources"][0]["state"] == expected:
                return
            time.sleep(0.005)
        self.fail(f"没有取得状态 {expected}")

    def test_directory_probe_distinguishes_empty_missing_file_and_permission(self):
        self.assertEqual(root_status(self.root), "ok")
        self.assertEqual(root_status(self.root / "missing"), "missing")
        file = self.root / "file"
        file.touch()
        self.assertEqual(root_status(file), "not_directory")
        for error, expected in ((PermissionError(), "permission_denied"), (OSError(), "unavailable")):
            with patch("peach.platform.os.scandir", side_effect=error):
                self.assertEqual(root_status(self.root), expected)

    def test_failure_while_reading_the_first_entry_is_not_online(self):
        class Entries:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self): return self
            def __next__(self): raise PermissionError
        with patch("peach.platform.os.scandir", return_value=Entries()):
            self.assertEqual(root_status(self.root), "permission_denied")
            self.assertFalse(root_online(self.root))

    def test_healthy_and_failed_probes_use_different_intervals_and_recover(self):
        self.wait_state("ok")
        self.assertEqual(len(self.calls), 1)
        self.now = 599
        self.monitor.tick()
        self.assertEqual(len(self.calls), 1)
        self.answer = "permission_denied"
        self.now = 600
        self.wait_state("permission_denied")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.monitor.summary()["warnings"], ["本地磁盘：没有权限读取"])
        self.now = 659
        self.monitor.tick()
        self.assertEqual(len(self.calls), 3)
        self.answer = "ok"
        self.now = 660
        self.wait_state("ok")
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.monitor.summary()["warnings"], [])

    def test_a_successful_retry_does_not_report_a_failure(self):
        answers = iter(["missing", "ok"])
        self.monitor.probe = lambda path: next(answers)
        self.wait_state("ok")
        self.assertEqual(self.monitor.summary()["warnings"], [])

    def test_first_probe_does_not_depend_on_the_clock_origin(self):
        self.now = -10000
        self.wait_state("ok")
        self.assertEqual(len(self.calls), 1)

    def test_pending_probe_is_unknown_and_timeout_keeps_one_worker(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(path):
            self.calls.append(path)
            entered.set()
            release.wait(2)
            return "ok"
        self.monitor.probe = blocked
        self.monitor.tick()
        self.assertTrue(entered.wait(1))
        self.assertIsNone(self.monitor.sources()[0]["online"])
        self.now = 6
        self.monitor.tick()
        self.assertEqual(self.monitor.summary()["sources"][0]["state"], "timeout")
        for self.now in (60, 600, 6000):
            self.monitor.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.monitor.summary()["warnings"], [])
        release.set()
        self.wait_state("ok")

    def test_slow_roots_have_a_global_limit_and_stop_does_not_wait_for_them(self):
        from peach.mount_reachability import MountReachability, MountRoot, MAX_PENDING
        release = threading.Event()
        self.addCleanup(release.set)
        def blocked(path):
            self.calls.append(path)
            release.wait(2)
            return "ok"
        self.monitor = MountReachability([
            MountRoot("local", "本地磁盘", str(at), self.root / str(at)) for at in range(12)
        ], probe=blocked)
        self.addCleanup(self.monitor.stop)
        self.monitor.tick()
        self.monitor.tick()
        deadline = time.monotonic() + 1
        while len(self.calls) < MAX_PENDING and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(len(self.calls), MAX_PENDING)
        self.monitor.stop()
        self.assertFalse(release.is_set())
        self.monitor.tick()
        self.assertEqual(len(self.calls), MAX_PENDING)

    def test_summary_hides_paths_and_all_roots_contribute_to_source_health(self):
        from peach.mount_reachability import MountReachability, MountRoot
        self.monitor = MountReachability([
            MountRoot("local", "本地磁盘", "R:/private", self.root),
            MountRoot("local", "本地磁盘", "S:/private", None),
        ])
        self.assertFalse(self.monitor.sources()[0]["online"])
        row = self.monitor.summary()["sources"][0]
        self.assertEqual(row["state"], "unmapped")
        self.assertNotIn("roots", row)
        self.assertNotIn("declared", row)
        self.assertNotIn("resolved", row)
        self.assertEqual(row["message"], "本地磁盘：未配置挂载点")


if __name__ == "__main__":
    unittest.main()
