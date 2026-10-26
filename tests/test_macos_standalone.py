"""独立 macOS 制品的分发边界。"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest import TestCase, mock, skipUnless
from dataclasses import dataclass


@dataclass
class FakeSubmenu:
    items: object

class MacStandaloneDistributionTests(TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "build_macos_standalone.py"
        spec = importlib.util.spec_from_file_location("macos_standalone_builder", path)
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def bundle(self, *, links=False):
        app = self.root / "Peach.app"
        executable = app / "Contents" / "MacOS" / "Peach"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"native executable")
        resource = app / "Contents" / "Resources"
        resource.mkdir()
        if links:
            (resource / "runtime").symlink_to("../MacOS/Peach")
        else:
            (resource / "runtime").write_bytes(b"native executable")
        return app, resource

    def test_disk_image_provides_the_bundle_and_install_destination(self):
        app, resource = self.bundle()
        destination = self.root / "release" / "Peach.dmg"

        def create_image(filename, volume_name, *, settings):
            from PIL import Image

            self.assertEqual(filename, str(destination))
            self.assertEqual(volume_name, "Peach")
            self.assertEqual(settings["files"], [str(app)])
            self.assertEqual(settings["symlinks"], {"Applications": "/Applications"})
            self.assertEqual((resource / "runtime").read_bytes(), b"native executable")
            self.assertEqual(settings["icon_locations"],
                             {"Peach.app": (174, 220), "Applications": (466, 220)})
            background = Path(settings["background"])
            for path, dimensions in ((background, (640, 420)),
                    (background.with_name("background@2x.png"), (1280, 840))):
                with Image.open(path) as image:
                    self.assertEqual(image.size, dimensions)
            self.assertFalse(settings["show_toolbar"])
            self.assertFalse(settings["show_sidebar"])
            destination.write_bytes(b"disk image")

        with mock.patch.dict(sys.modules, {"dmgbuild": SimpleNamespace(build_dmg=create_image)}):
            result = self.builder.package_dmg(app, destination)
        self.assertEqual(result.read_bytes(), b"disk image")

    @skipUnless(sys.platform != "win32", "应用内符号链接在 POSIX 文件系统验证")
    def test_disk_image_preserves_posix_bundle_links(self):
        app, resource = self.bundle(links=True)
        destination = self.root / "release" / "Peach.dmg"

        def create_image(filename, volume_name, *, settings):
            self.assertEqual(settings["files"], [str(app)])
            self.assertTrue((resource / "runtime").is_symlink())
            self.assertEqual((resource / "runtime").read_bytes(), b"native executable")
            Path(filename).write_bytes(b"disk image")

        with mock.patch.dict(sys.modules, {"dmgbuild": SimpleNamespace(build_dmg=create_image)}):
            self.builder.package_dmg(app, destination)
        self.assertTrue((resource / "runtime").is_symlink())
        self.assertEqual((resource / "runtime").read_bytes(), b"native executable")

    def test_incomplete_bundle_is_rejected_before_creating_an_image(self):
        destination = self.root / "release" / "Peach.dmg"
        with self.assertRaises(FileNotFoundError):
            self.builder.package_dmg(self.root / "Peach.app", destination)
        self.assertFalse(destination.parent.exists())

    def test_non_macos_build_is_rejected_before_running_tools(self):
        with mock.patch.object(self.builder.sys, "platform", "win32"), \
                mock.patch.object(self.builder.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                self.builder.build(self.root)
        run.assert_not_called()

    def test_mismatched_python_architecture_is_rejected_before_building(self):
        with mock.patch.object(self.builder.sys, "platform", "darwin"), \
                mock.patch.object(self.builder.platform, "machine", return_value="arm64"), \
                mock.patch.object(self.builder.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                self.builder.build(self.root, target_arch="x86_64")
        run.assert_not_called()

    def test_minimum_system_uses_dependency_requirements_and_ignores_the_sdk_version(self):
        app = self.root / "Peach.app"
        app.mkdir()
        (app / "native").write_bytes(b"\xcf\xfa\xed\xfe")
        commands = "cmd LC_BUILD_VERSION\n cmdsize 32\n platform 1\n minos 11.0\n sdk 26.5\n"
        commands += "cmd LC_VERSION_MIN_MACOSX\n cmdsize 16\n version 14.0\n sdk 26.5\n"
        with mock.patch.object(self.builder.subprocess, "run", return_value=mock.Mock(stdout=commands)):
            self.assertEqual(self.builder.minimum_system_version(app), "14.0")

    def test_menu_actions_and_certificate_lifecycle_follow_the_distribution(self):
        from peach import tray

        for independent in (True, False):
            with self.subTest(standalone=independent):
                gate = mock.Mock()
                gate.config.directory.return_value = self.root
                gate.waiting = False
                menu = mock.Mock()
                menu_type = mock.Mock(return_value=menu)
                with mock.patch.dict(sys.modules, {"peach.menubar": SimpleNamespace(
                        MenuBarApp=menu_type, Submenu=FakeSubmenu)}), \
                        mock.patch.object(tray, "standalone", return_value=independent), \
                        mock.patch.object(tray, "VersionManager"), \
                        mock.patch.object(tray, "create_icon"), \
                        mock.patch.object(tray, "lan_ipv4", return_value="192.0.2.1"), \
                        mock.patch.object(tray, "ensure_certificate", return_value=None) as certificate, \
                        mock.patch.object(tray, "NetworkChangeWatcher") as watcher:
                    tray.run_macos_menu_bar(mock.Mock(specs=()), gate, silent=True)
                labels = [title for title, _ in menu_type.call_args.args[2]]
                self.assertEqual("同步开发进度" in labels, not independent)
                if independent:
                    certificate.assert_not_called()
                    watcher.assert_not_called()
                else:
                    certificate.assert_called_once()
                    watcher.return_value.start.assert_called_once()
                    watcher.return_value.stop.assert_called_once()
                    self.assertLess(labels.index("同步开发进度"), labels.index("重启服务"))
                menu.run.assert_called_once()

    @skipUnless(sys.platform == "darwin", "AppKit 是 macOS 专属")
    def test_native_submenu_updates_rows_and_routes_nested_actions(self):
        from peach import menubar

        class Menu:
            def __init__(self):
                self.rows = []

            def setAutoenablesItems_(self, value):
                pass

            def addItem_(self, item):
                self.rows.append(item)

        menu_type = mock.Mock()
        menu_type.alloc.return_value.init.side_effect = Menu
        item_type = mock.Mock()
        item_type.alloc.return_value.initWithTitle_action_keyEquivalent_.side_effect = (
            lambda title, *_: mock.Mock(title=title))
        app = object.__new__(menubar.MenuBarApp)
        app.item, app._target = mock.Mock(), mock.Mock()
        action = mock.Mock()
        rows = [("目录不可用", None), ("打开配置", action)]
        app._items = [("状态", menubar.Submenu(lambda: rows)), ("退出", action)]
        app._handlers, app._rendered = {}, None
        with mock.patch.object(menubar.AppKit, "NSMenu", menu_type), \
                mock.patch.object(menubar.AppKit, "NSMenuItem", item_type):
            app.refresh_titles()
            parent = app.menu.rows[0]
            nested = parent.setSubmenu_.call_args.args[0]
            self.assertEqual([row.title for row in nested.rows], ["目录不可用", "打开配置"])
            nested.rows[0].setEnabled_.assert_called_once_with(False)
            self.assertEqual(len(app._handlers), 2)
            app._handlers[0]()
            action.assert_called_once()
            original = app.menu
            app.refresh_titles()
            self.assertIs(app.menu, original)
            rows[:] = [("HTTP 正常", None)]
            app.refresh_titles()
            nested = app.menu.rows[0].setSubmenu_.call_args.args[0]
            self.assertEqual([row.title for row in nested.rows], ["HTTP 正常"])
            self.assertEqual(len(app._handlers), 1)
