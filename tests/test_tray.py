import contextlib
import importlib.util
import inspect
import io
import json
import logging
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    """按路径加载 `scripts/` 下的脚本；它们不是包的一部分。"""
    spec = importlib.util.spec_from_file_location(f"test_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from peach import appid, onboarding, runtime_prepare, settings_file
from peach import tray as tray_module
from peach.config import SECRETS_DIR
from peach.tray import (
    AlreadyRunning, PeachTray, ServiceManager, ServiceSpec, SetupGate,
    SingleInstance, apply_macos_template, build_service_specs,
    build_setup_service_specs, create_icon, enable_hidpi,
    launchd_owns_this_process, ledger_menu_items, needs_setup,
    restart_tray_process, tray_restart_required,
)
from peach.tray import main as tray_main
from unittest import mock
from peach.sync import SyncPlan
from peach.versioning import UpdateResult, VersionSnapshot
from peach.windows_update import PendingWindowsUpdate, WindowsUpdatePreparation


class Response:
    status_code = 200

    @staticmethod
    def json():
        return {"ok": True}


class TrayTests(unittest.TestCase):
    def test_create_icon_has_expected_size_and_alpha(self):
        icon = create_icon(64)
        self.assertEqual(icon.size, (64, 64))
        self.assertEqual(icon.mode, "RGBA")

    def test_start_missing_does_not_duplicate_healthy_service(self):
        spec = ServiceSpec("http", "http://local/healthz", ("peach", "serve"), True)
        popen = Mock()
        manager = ServiceManager((spec,), popen=popen, health_get=lambda *args, **kwargs: Response())
        manager.start_missing()
        popen.assert_not_called()

    def test_services_never_own_ledger_sync(self):
        """浏览服务只观察写入角色；跨机复制只能由托盘显式执行。"""
        with tempfile.TemporaryDirectory() as directory:
            tls_dir = Path(directory)
            for name in ("peach-local-ca.crt", "peach.crt", "peach.key"):
                (tls_dir / name).write_text("test-only", encoding="utf-8")
            specs = build_service_specs(lan_address="192.0.2.10", tls_dir=tls_dir)
        owners = [spec for spec in specs if "--no-ledger-sync" not in spec.command
                  and "--redirect-origin" not in spec.command]
        self.assertEqual(owners, [])
        for spec in specs:
            if spec.name == "https":
                self.assertIn("--no-ledger-sync", spec.command)
                self.assertNotIn("--no-mdns", spec.command)
            else:
                self.assertIn("--redirect-origin", spec.command)

    @unittest.skipUnless(
        os.name == "nt", "macOS 走 build_macos_service_specs，不发布 LAN 地址"
    )
    def test_windows_service_address_uses_the_current_route_when_not_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            tls_dir = Path(directory)
            for name in ("peach-local-ca.crt", "peach.crt", "peach.key"):
                (tls_dir / name).write_text("test-only", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False), patch(
                "peach.tray.lan_ipv4", return_value="192.0.2.55",
            ):
                os.environ.pop("PEACH_LAN_ADDRESS", None)
                specs = build_service_specs(tls_dir=tls_dir)
        commands = [item for spec in specs for item in spec.command]
        self.assertIn("192.0.2.55", commands)

    @patch("peach.tray.LOG_DIR")
    def test_start_and_stop_only_owned_service(self, log_dir):
        with tempfile.TemporaryDirectory() as directory:
            log_dir.__fspath__ = lambda: directory
            log_dir.mkdir.side_effect = lambda **_kwargs: None
            log_dir.__truediv__.side_effect = lambda name: Path(directory) / name
            spec = ServiceSpec("http", "http://local/healthz", ("peach", "serve"), True)
            process = Mock()
            process.poll.return_value = None
            popen = Mock(return_value=process)
            manager = ServiceManager(
                (spec,), popen=popen,
                health_get=Mock(side_effect=OSError("down")),
            )
            manager.start_missing()
            popen.assert_called_once()
            manager.stop_owned()
            process.terminate.assert_called_once()

    @unittest.skipUnless(
        os.name == "nt", "DPI 声明与单实例锁是 Windows 托盘专属能力"
    )
    @patch("peach.tray.ctypes.windll.user32.SetProcessDpiAwarenessContext", create=True)
    def test_hidpi_prefers_per_monitor_v2(self, set_context):
        set_context.return_value = True
        self.assertEqual(enable_hidpi(), "per-monitor-v2")

    def test_update_check_is_background_notification_not_modal_dialog(self):
        snapshot = VersionSnapshot("0.2.1", "master", "abc12345", False, False, None)
        completed = threading.Event()

        class Versions:
            def inspect(self):
                return snapshot

            def check(self):
                completed.set()
                return UpdateResult("unconfigured", "未配置更新源", snapshot)

        icon = Mock()
        # 这里只验证后台更新行为，不创建真实系统托盘。macOS 的 pystray Icon 构造会向
        # WindowServer 注册应用；在 Codex seatbelt 沙箱内系统会直接 SIGABRT，连测试
        # 报告都来不及生成，并弹出「Python 意外退出」。
        with patch("peach.tray.pystray.Icon", return_value=Mock()):
            tray = PeachTray(ServiceManager(tuple()), Versions())
        tray.check_updates(icon)
        self.assertTrue(completed.wait(2))
        for _ in range(20):
            if icon.notify.call_count == 2:
                break
            time.sleep(0.01)
        self.assertEqual(icon.notify.call_count, 2)
        icon.update_menu.assert_called_once()

    def test_single_instance_rejects_a_second_holder(self):
        """两个菜单栏项会各自再拉起一份服务去抢同一个端口，必须挡住。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tray.lock"
            first = SingleInstance(path)
            second = SingleInstance(path)
            first.acquire()
            try:
                with self.assertRaises(AlreadyRunning):
                    second.acquire()
            finally:
                first.close()


class ServiceStatusTests(unittest.TestCase):
    """状态文案逐个点名：正常的和异常的都写明白，异常的带上失败原因。

    只说「未运行」没法行动：HTTP 和 HTTPS 会因为端口占用、证书过期、pf 转发写错
    等完全不同的原因挂掉，而且服务活着时探测本身也可能被骗（见代理劫持的回归测试）。

    本类里的账本同步用例都以「这台机器开了复制」为前提，所以显式钉住开关；
    关掉之后的行为由 `ReplicationSwitchTests` 单独验。
    """

    def setUp(self):
        patcher = patch("peach.tray.REPLICATION_ENABLED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def manager(self, health: dict) -> ServiceManager:
        specs = tuple(
            ServiceSpec(name, f"http://127.0.0.1/{name}", ("noop",), True)
            for name in health
        )
        manager = ServiceManager(specs)
        manager._last_health.update(health)
        return manager

    def test_all_healthy(self):
        self.assertEqual(
            self.manager({"http": (True, ""), "https": (True, "")}).status(),
            "HTTP 正常 · HTTPS 正常",
        )

    def test_none_healthy(self):
        self.assertEqual(
            self.manager({"http": (False, "无响应"), "https": (False, "无响应")}).status(),
            "HTTP 异常（无响应） · HTTPS 异常（无响应）",
        )

    def test_mount_failure_names_the_source_without_restarting_the_healthy_service(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "media_mounts": {
            "warnings": ["CloudDrive · 115：没有权限读取"]}}
        spec = ServiceSpec("https", "https://local/healthz", ("noop",), True)
        popen = Mock()
        manager = ServiceManager((spec,), popen=popen, health_get=lambda *a, **k: response)
        manager.start_missing()
        self.assertEqual(manager.status(), "HTTPS 正常（CloudDrive · 115：没有权限读取）")
        popen.assert_not_called()

    def test_short_summary_deduplicates_mount_warnings_and_recovers(self):
        warnings = ["本地磁盘：目录不存在", "CloudDrive · 115：" + "很长的目录/" * 40]
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "media_mounts": {"warnings": warnings}}
        manager = self.manager({"http": (False, "未检测"), "https": (False, "未检测")})
        manager._health_get = lambda *a, **k: response
        for spec in manager.specs:
            self.assertTrue(manager.healthy(spec))
        self.assertEqual(manager.status_summary(), "运行中 · 2 项提醒")
        self.assertEqual(manager.status_details()[:3], ("HTTP 正常", "HTTPS 正常", warnings[0]))
        self.assertTrue(manager.status_details()[-1].endswith("…"))
        self.assertTrue(all(len(line) <= 48 for line in manager.status_details()))
        response.json.return_value = {"ok": True, "media_mounts": {"warnings": []}}
        for spec in manager.specs:
            manager.healthy(spec)
        self.assertEqual(manager.status_summary(), "运行中")
        self.assertEqual(manager.status_details(), ("HTTP 正常", "HTTPS 正常"))

    def test_short_summary_preserves_service_failures(self):
        manager = self.manager({"http": (True, ""), "https": (False, "状态码 503")})
        self.assertEqual(manager.status_summary(), "部分服务异常")
        self.assertEqual(manager.status_details(), ("HTTP 正常", "HTTPS 异常（状态码 503）"))
        self.assertEqual(self.manager({"http": (False, "无响应")}).status_summary(), "服务未运行")

    def test_windows_style_menu_keeps_details_out_of_the_root_and_refreshes(self):
        manager = self.manager({"http": (True, "")})
        with patch("peach.tray.pystray.Icon") as icon:
            tray = PeachTray(manager)
        root = icon.call_args.args[3]
        status = next(item for item in root.items if item.text.startswith("状态："))
        self.assertEqual(status.text, "状态：运行中")
        self.assertIn("HTTP 正常", [item.text for item in status.submenu.items])
        manager._last_health["http"] = (False, "无响应")
        self.assertEqual(status.text, "状态：服务未运行")
        self.assertIn("HTTP 异常（无响应）", [item.text for item in status.submenu.items])
        self.assertIs(tray.manager, manager)

    def test_partial_names_the_broken_one_with_reason(self):
        self.assertEqual(
            self.manager({"http": (True, ""), "https": (False, "状态码 503")}).status(),
            "HTTP 正常 · HTTPS 异常（状态码 503）",
        )
        self.assertEqual(
            self.manager({"http": (False, "状态码 503"), "https": (True, "")}).status(),
            "HTTP 异常（状态码 503） · HTTPS 正常",
        )

    def test_health_check_never_goes_through_a_proxy(self):
        """健康检查必须绕过代理：Stash 等客户端设置系统级 HTTP 代理后，httpx 默认
        把 127.0.0.1 的探测送进代理、由代理回 503，服务活着却被判成「未运行」。"""
        seen: dict = {}

        def probe(url, **kwargs):
            seen.update(kwargs)
            return Response()

        spec = ServiceSpec("http", "http://127.0.0.1/healthz", ("noop",), True)
        manager = ServiceManager((spec,), popen=Mock(), health_get=probe)
        self.assertTrue(manager.healthy(spec))
        self.assertIs(seen.get("trust_env"), False)

    @patch("peach.tray.DATABASE_PATH", Path("/local/ledger.db"))
    @patch("peach.tray.SHARED_DATABASE_PATH", Path("/shared/ledger.db"))
    def test_manual_sync_stops_owned_service_and_restarts_it(self):
        spec = ServiceSpec("http", "http://127.0.0.1/healthz", ("peach", "serve"), True)
        process = Mock()
        process.poll.return_value = None
        completed = Mock(returncode=0, stdout="数据库同步：in-sync · 两侧已经一致\n", stderr="")
        manager = ServiceManager(
            (spec,), popen=Mock(return_value=process), run=Mock(return_value=completed),
            ledger_plan=lambda: SyncPlan("pull", "共享副本更新"),
        )
        manager._owned["http"] = process
        with patch.object(manager, "healthy", return_value=True), patch.object(
            manager, "start_missing"
        ) as start, patch.object(manager, "wait_until_ready", return_value=True):
            ok, message = manager.sync_ledger(Path("/venv/peach"))
        self.assertTrue(ok)
        self.assertIn("两侧已经一致", message)
        process.terminate.assert_called_once()
        start.assert_called_once()
        self.assertEqual(
            manager._run.call_args.args[0][:2],
            [str(Path("/venv/peach")), "ledger-sync"],
        )
        self.assertEqual(manager._run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(
            manager._run.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8",
        )

    def test_take_ownership_is_an_explicit_ledger_command(self):
        manager = ServiceManager(
            tuple(),
            run=Mock(return_value=Mock(
                returncode=0, stdout="数据库同步：take-ownership\n", stderr="")),
            ledger_plan=lambda: SyncPlan("in-sync", "两侧一致"),
        )
        with patch.object(manager, "start_missing"), patch.object(
            manager, "wait_until_ready", return_value=True,
        ):
            ok, _message = manager.sync_ledger(Path("/venv/peach"), take_ownership=True)
        self.assertTrue(ok)
        self.assertIn("--take-ownership", manager._run.call_args.args[0])

    def test_manual_sync_refuses_a_healthy_unowned_service(self):
        spec = ServiceSpec("http", "http://127.0.0.1/healthz", ("peach", "serve"), True)
        runner = Mock()
        manager = ServiceManager(
            (spec,), health_get=lambda *args, **kwargs: Response(), run=runner,
            ledger_plan=lambda: SyncPlan("pull", "共享副本更新"),
        )
        ok, message = manager.sync_ledger(Path("/venv/peach"))
        self.assertFalse(ok)
        self.assertIn("不归本托盘管理", message)
        runner.assert_not_called()

    def test_an_unreachable_share_never_stops_the_services(self):
        """共享盘没挂时，停一遍服务再启回来换不到任何东西，只换来一次断网页。"""
        spec = ServiceSpec("http", "http://127.0.0.1/healthz", ("peach", "serve"), True)
        process = Mock()
        process.poll.return_value = None
        runner = Mock()
        manager = ServiceManager(
            (spec,), run=runner,
            ledger_plan=lambda: SyncPlan("offline", "共享副本所在的盘不可达，本地照常读写"),
            mount_share=Mock(return_value=False),
        )
        manager._owned["http"] = process

        ok, message = manager.sync_ledger(Path("/venv/peach"))

        self.assertFalse(ok)
        self.assertIn("盘不可达", message)
        runner.assert_not_called()
        process.terminate.assert_not_called()

    def test_a_reader_that_cannot_push_is_reported_without_an_outage(self):
        """本机不是写入端时结论已经定了，跑一遍 CLI 也只会得到同一个 conflict。"""
        spec = ServiceSpec("http", "http://127.0.0.1/healthz", ("peach", "serve"), True)
        process = Mock()
        process.poll.return_value = None
        runner = Mock()
        manager = ServiceManager(
            (spec,), run=runner,
            ledger_plan=lambda: SyncPlan("conflict", "当前写入端是 host-88053062，本机不能推送"),
        )
        manager._owned["http"] = process

        ok, message = manager.sync_ledger(Path("/venv/peach"))

        self.assertFalse(ok)
        self.assertIn("不能推送", message)
        runner.assert_not_called()
        process.terminate.assert_not_called()

    def test_take_ownership_still_runs_when_the_two_sides_are_in_sync(self):
        """`in-sync` 对同步是无事可做，对接管却正是要做的那一次；短路不能一视同仁。"""
        manager = ServiceManager(
            tuple(),
            run=Mock(return_value=Mock(
                returncode=0, stdout="数据库同步：take-ownership\n", stderr="")),
            ledger_plan=lambda: SyncPlan("in-sync", "两侧一致"),
        )
        with patch.object(manager, "start_missing"), patch.object(
            manager, "wait_until_ready", return_value=True,
        ):
            ok, _message = manager.sync_ledger(Path("/venv/peach"), take_ownership=True)
        self.assertTrue(ok)
        manager._run.assert_called_once()

    def test_take_ownership_refuses_early_when_the_share_is_unreachable(self):
        runner = Mock()
        manager = ServiceManager(
            tuple(), run=runner,
            ledger_plan=lambda: SyncPlan("offline", "共享副本所在的盘不可达，本地照常读写"),
            mount_share=Mock(return_value=False),
        )
        ok, message = manager.sync_ledger(Path("/venv/peach"), take_ownership=True)
        self.assertFalse(ok)
        self.assertIn("先挂上共享副本所在的盘", message)
        runner.assert_not_called()

    def test_an_unmounted_share_is_mounted_once_and_then_synced(self):
        """macOS 重启后 SMB 共享不会自己回来。`offline` 是本机的日常状态，不是结论。"""
        spec = ServiceSpec("http", "http://127.0.0.1/healthz", ("peach", "serve"), True)
        process = Mock()
        process.poll.return_value = None
        plans = iter([
            SyncPlan("offline", "共享副本所在的盘不可达，本地照常读写"),
            SyncPlan("pull", "共享副本更新"),
        ])
        mount = Mock(return_value=True)
        completed = Mock(returncode=0, stdout="数据库同步：pull · 共享副本更新\n", stderr="")
        manager = ServiceManager(
            (spec,), popen=Mock(return_value=process), run=Mock(return_value=completed),
            ledger_plan=lambda: next(plans), mount_share=mount,
        )
        manager._owned["http"] = process
        with patch.object(manager, "healthy", return_value=True), patch.object(
            manager, "start_missing"
        ), patch.object(manager, "wait_until_ready", return_value=True):
            ok, message = manager.sync_ledger(Path("/venv/peach"))

        self.assertTrue(ok)
        self.assertIn("共享副本更新", message)
        mount.assert_called_once_with()
        manager._run.assert_called_once()

    def test_a_share_that_will_not_mount_falls_back_to_the_clear_message(self):
        """挂不上就回到那条固定消息：菜单栏项不能卡住，服务也不该白停一次。"""
        spec = ServiceSpec("http", "http://127.0.0.1/healthz", ("peach", "serve"), True)
        process = Mock()
        process.poll.return_value = None
        runner = Mock()
        mount = Mock(return_value=False)
        manager = ServiceManager(
            (spec,), run=runner,
            ledger_plan=lambda: SyncPlan("offline", "共享副本所在的盘不可达，本地照常读写"),
            mount_share=mount,
        )
        manager._owned["http"] = process

        ok, message = manager.sync_ledger(Path("/venv/peach"))

        self.assertFalse(ok)
        self.assertIn("盘不可达", message)
        mount.assert_called_once_with()
        runner.assert_not_called()
        process.terminate.assert_not_called()

    def test_take_ownership_mounts_the_share_before_refusing(self):
        """接管同样先补挂：盘只是没挂时，`offline` 之后往往正好是可以接管的 `in-sync`。"""
        plans = iter([
            SyncPlan("offline", "共享副本所在的盘不可达，本地照常读写"),
            SyncPlan("in-sync", "两侧一致"),
        ])
        manager = ServiceManager(
            tuple(),
            run=Mock(return_value=Mock(
                returncode=0, stdout="数据库同步：take-ownership\n", stderr="")),
            ledger_plan=lambda: next(plans), mount_share=Mock(return_value=True),
        )
        with patch.object(manager, "start_missing"), patch.object(
            manager, "wait_until_ready", return_value=True,
        ):
            ok, _message = manager.sync_ledger(Path("/venv/peach"), take_ownership=True)
        self.assertTrue(ok)
        self.assertIn("--take-ownership", manager._run.call_args.args[0])

    def test_a_reachable_share_is_never_remounted(self):
        """判定已经通了还去碰挂载，只是一次白跑的网络往返。"""
        mount = Mock()
        manager = ServiceManager(
            tuple(),
            run=Mock(return_value=Mock(returncode=0, stdout="ok", stderr="")),
            ledger_plan=lambda: SyncPlan("pull", "共享副本更新"), mount_share=mount,
        )
        with patch.object(manager, "start_missing"), patch.object(
            manager, "wait_until_ready", return_value=True,
        ):
            manager.sync_ledger(Path("/venv/peach"))
        mount.assert_not_called()


class ReplicationSwitchTests(unittest.TestCase):
    """ADR-0023 第 3 阶段：`replication.enabled = false` 时托盘不装配复制那一层。"""

    def items(self, enabled):
        with patch("peach.tray.REPLICATION_ENABLED", enabled):
            return ledger_menu_items(
                lambda label, action: (label, action), Mock(), Mock())

    def test_menu_items_disappear_when_replication_is_off(self):
        self.assertEqual(self.items(False), [])

    def test_menu_items_are_assembled_when_replication_is_on(self):
        labels = [label for label, _ in self.items(True)]
        self.assertEqual(labels, ["同步数据库", "由本机接管数据库写入"])

    def test_sync_ledger_refuses_instead_of_probing_a_share(self):
        """菜单项没了，但托盘还有别的入口；这条路径会去探一个不存在的共享。"""
        plan = Mock()
        mount = Mock()
        manager = ServiceManager(tuple(), ledger_plan=plan, mount_share=mount)
        with patch("peach.tray.REPLICATION_ENABLED", False):
            ok, message = manager.sync_ledger(Path("/venv/peach"))
        self.assertFalse(ok)
        self.assertIn("replication.enabled", message)
        plan.assert_not_called()
        mount.assert_not_called()


class SetupGateTests(unittest.TestCase):
    """首次设置期间的服务切换（ADR-0023 的 GUI 引导）。

    托盘不重启自己就要完成切换，所以这里断言的是「规格换了、日志目录和 PEACH_DATA_ROOT
    跟着换了、首扫标记只被消费一次」，而不是某个平台的菜单长什么样。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.data_root = self.root / "peach-data"
        patcher = patch("peach.tray._peach_executable", return_value=Path("/venv/peach"))
        self.addCleanup(patcher.stop)
        patcher.start()

    def _config(self):
        return settings_file.load_config(
            project_root=self.root / "app",
            environ={"PEACH_DATA_ROOT": str(self.data_root)})

    def _tls(self, config):
        tls_dir = config.directory("secrets") / "tls"
        tls_dir.mkdir(parents=True, exist_ok=True)
        for name in ("peach-local-ca.crt", "peach.crt", "peach.key"):
            (tls_dir / name).write_text("test-only", encoding="utf-8")

    def test_a_fresh_machine_needs_setup_even_after_a_state_directory_appeared(self):
        self.assertTrue(needs_setup(self._config()))
        # 托盘的单实例锁会建出 `state/`，Peach 数据目录于是存在、`configured` 变成 True。
        (self.data_root / "state").mkdir(parents=True)
        self.assertTrue(self._config().configured)
        self.assertTrue(needs_setup(self._config()), "空 Peach 数据目录不算配置过")

    def test_an_existing_deployment_without_a_settings_file_is_left_alone(self):
        (self.data_root / "database").mkdir(parents=True)
        (self.data_root / "database" / "ledger.db").write_bytes(b"")
        self.assertFalse(needs_setup(self._config()))

    def test_the_setup_service_is_loopback_only_without_tls_or_a_token(self):
        specs = build_setup_service_specs(self._config())
        self.assertEqual([spec.name for spec in specs], ["setup"])
        command = specs[0].command
        self.assertIn("--setup", command)
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(command[command.index("--port") + 1], "8900")
        self.assertNotIn("--ssl-certfile", command)
        self.assertNotIn("0.0.0.0", command)
        self.assertEqual(specs[0].health_url, "http://127.0.0.1:8900/healthz")

    def test_an_unconfigured_machine_starts_the_setup_service_not_the_normal_one(self):
        config = self._config()
        manager = ServiceManager(build_setup_service_specs(config),
                                 log_dir=config.directory("logs"))
        gate = SetupGate(manager, config, waiting=True, load=self._config)
        self.assertTrue(gate.waiting)
        self.assertEqual(gate.open_url(), "http://127.0.0.1:8900/")
        self.assertEqual(gate.open_label(), "重新打开设置页")
        self.assertIn("等待完成首次设置", gate.status_line())
        self.assertFalse(gate.poll(), "设置还没做完，轮询不动任何东西")
        self.assertEqual([spec.name for spec in manager.specs], ["setup"])

    def test_configured_source_tray_consumes_reload_once_and_marks_its_services(self):
        config = self._config()
        settings_file.write(config)
        config = self._config()
        self._tls(config)
        config.directory("state").mkdir(parents=True, exist_ok=True)
        reload_path = config.directory("state") / onboarding.RELOAD_NAME
        reload_path.write_text("reload", encoding="utf-8")
        manager = ServiceManager((), log_dir=config.directory("logs"),
                                 popen=Mock(), health_get=lambda *a, **k: Response())
        gate = SetupGate(manager, config, waiting=False, load=self._config)
        with patch("peach.tray.lan_ipv4", return_value="192.0.2.10"), patch(
                "peach.distribution.standalone", return_value=False):
            self.assertTrue(gate.poll())
            self.assertFalse(gate.poll())
        self.assertFalse(reload_path.exists())
        self.assertEqual(manager.child_environment()["PEACH_TRAY_MANAGED"], "1")
        self.assertTrue(manager.specs)

    def test_a_finished_setup_switches_to_the_normal_services_and_runs_the_first_scan(self):
        config = self._config()
        manager = ServiceManager(build_setup_service_specs(config),
                                 log_dir=config.directory("logs"),
                                 popen=Mock(), health_get=lambda *a, **k: Response())
        scan_popen = Mock()
        gate = SetupGate(manager, config, waiting=True, load=self._config,
                         popen=scan_popen, open_browser=Mock())
        self._tls(config)
        (self.data_root / "config.toml").write_text(
            "[server]\nport = 9100\nmdns_name = 'peach-writer'\n", encoding="utf-8")
        onboarding.request_first_scan(self._config())

        with patch("peach.tray.lan_ipv4", return_value="192.0.2.10"):
            self.assertTrue(gate.poll())
        self.assertFalse(gate.waiting)
        self.assertNotIn("setup", [spec.name for spec in manager.specs])
        self.assertTrue(manager.specs, "切换之后必须有正常服务规格")
        self.assertEqual(manager.log_dir, self.data_root / "logs")
        self.assertEqual(manager.child_environment()["PEACH_DATA_ROOT"], str(self.data_root))
        self.assertEqual(gate.open_label(), "打开 Peach")
        self.assertEqual(gate.open_url(), "https://peach-writer.local/")

        scan = scan_popen.call_args
        self.assertEqual(list(scan.args[0][1:]), ["process", "local"])
        self.assertEqual(scan.kwargs["env"]["PEACH_DATA_ROOT"], str(self.data_root))
        # 标记只消费一次：下一轮轮询已经不在等待状态，也不会再拉起一次扫描。
        self.assertFalse(gate.poll())
        self.assertEqual(scan_popen.call_count, 1)
        self.assertIsNone(gate.start_first_scan(self._config()))

    def test_the_plain_port_redirects_to_the_name_the_setup_form_just_wrote(self):
        """明文口的跳转目标取新鲜读到的 mDNS 名，不用模块常量。

        `MDNS_HOSTNAME` 在 import 期就按当时的设置文件定型，而首次设置里那一题正是它；
        托盘不重启自己就完成切换，用常量会把人跳到一个不存在的 `.local` 名下。
        """
        config = self._config()
        manager = ServiceManager(build_setup_service_specs(config),
                                 log_dir=config.directory("logs"),
                                 popen=Mock(), health_get=lambda *a, **k: Response())
        gate = SetupGate(manager, config, waiting=True, load=self._config,
                         popen=Mock(), open_browser=Mock())
        self._tls(config)
        (self.data_root / "config.toml").write_text(
            "[server]\nport = 9100\nmdns_name = 'peach-writer'\n", encoding="utf-8")

        # 打进去的是 import 期那份旧名字；跳转目标必须是表单刚写下的那个。
        with patch("peach.tray.MDNS_HOSTNAME", "peach-reader.local"), patch(
                "peach.tray.lan_ipv4", return_value="192.0.2.10"):
            self.assertTrue(gate.poll())
        redirecting = [spec for spec in manager.specs
                       if "--redirect-origin" in spec.command]
        self.assertTrue(redirecting, "正常规格里必须有一条只做跳转的明文口")
        for spec in redirecting:
            command = spec.command
            self.assertEqual(command[command.index("--redirect-origin") + 1],
                             "https://peach-writer.local")

    def test_missing_tls_material_keeps_the_gate_waiting(self):
        """没有 openssl 的机器上 CA 生成会失败；那时不能拿一组缺文件的规格去启动。

        macOS 的规格是另一种约定：证书缺失只是不起 HTTPS，闸门照样打开，只带明文口。
        两种行为都在这里钉住，`popen` 必须是替身——闸门一开就会真的去拉起服务进程。
        """
        config = self._config()
        popen = Mock()
        manager = ServiceManager(build_setup_service_specs(config),
                                 log_dir=config.directory("logs"),
                                 popen=popen, health_get=lambda *a, **k: Response())
        gate = SetupGate(manager, config, waiting=True, load=self._config,
                         popen=Mock(), open_browser=Mock())
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "config.toml").write_text("[server]\nport = 8900\n", encoding="utf-8")
        with patch("peach.tray.lan_ipv4", return_value="192.0.2.10"):
            opened = gate.poll()
        if sys.platform == "darwin":
            self.assertTrue(opened)
            self.assertFalse(gate.waiting)
            self.assertEqual([spec.name for spec in manager.specs], ["http"])
            return
        self.assertFalse(opened)
        self.assertTrue(gate.waiting)
        self.assertEqual([spec.name for spec in manager.specs], ["setup"])
        popen.assert_not_called()


class LogRetentionAtStartupTests(unittest.TestCase):
    def test_the_log_sweep_runs_before_any_child_process_can_open_a_log(self):
        """子进程以追加方式握着 `tray-*.log` 的句柄；先起服务再改名，Windows 上改名会失败，
        POSIX 上则是段文件继续被写。所以整理必须排在 ServiceManager 之前，且失败只记警告。"""
        from peach import tray, settings_file
        for platform in ("win32", "darwin", "linux"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                self.check_log_sweep(Path(directory).resolve(), platform, tray, settings_file)

    def check_log_sweep(self, root, platform, tray, settings_file):
        config = settings_file.PeachConfig(root, root / "config.toml")
        events = []
        def sweep(_):
            events.append("sweep")
            raise OSError("log busy")
        def manager(*args, **kwargs):
            events.append("manager")
            return mock.Mock()
        icon = mock.Mock()
        icon.run.side_effect = lambda: events.append("run")
        with mock.patch.object(tray.sys, "platform", platform), \
                mock.patch.object(tray, "run_macos_menu_bar", side_effect=lambda *args, **kwargs: events.append("run")) as macos_menu, \
                mock.patch.object(tray, "handoff_tray", return_value=False), \
                mock.patch.object(tray.settings_file, "load_config", return_value=config), \
                mock.patch.object(tray.error_reporting, "install"), \
                mock.patch.object(tray, "SingleInstance") as instance, \
                mock.patch.object(tray, "Lifecycle"), \
                mock.patch.object(tray.log_retention, "sweep", side_effect=sweep), \
                mock.patch.object(tray, "log_to_file"), \
                mock.patch.object(tray, "build_setup_service_specs", return_value=()), \
                mock.patch.object(tray, "ServiceManager", side_effect=manager), \
                mock.patch.object(tray, "PeachTray", return_value=icon) as windows_tray, \
                mock.patch.object(tray, "tray_preparation"), \
                mock.patch.object(tray, "show_message"), \
                self.assertLogs(level="WARNING"):
            instance.return_value.is_running.return_value = False
            self.assertEqual(tray_main(["--silent"]), 0)
            instance.return_value.close.assert_called_once_with()
        self.assertEqual(events, ["sweep", "manager", "run"])
        if platform == "darwin":
            macos_menu.assert_called_once()
            windows_tray.assert_not_called()
            icon.run.assert_not_called()
        else:
            macos_menu.assert_not_called()
            windows_tray.assert_called_once()
            icon.run.assert_called_once_with()


class SourceSyncTests(unittest.TestCase):
    """「同步开发进度」这条路径：拉到的代码要真的跑起来，托盘不能自己骗自己。"""

    def test_only_tray_owned_modules_demand_a_tray_restart(self):
        self.assertFalse(tray_restart_required(("src/peach/web.py", "docs/STATUS.md")))
        self.assertTrue(tray_restart_required(("src/peach/web.py", "src/peach/menubar.py")))
        self.assertTrue(tray_restart_required(("pyproject.toml",)))
        self.assertFalse(tray_restart_required(()))

    def test_launchd_ownership_requires_a_matching_pid_not_just_a_loaded_job(self):
        """只看「作业已加载」会在终端启动的托盘上 kickstart 出第二个菜单栏图标。"""
        loaded_elsewhere = Mock(returncode=0, stdout="\tpid = 1\n\tstate = running\n")
        self.assertFalse(launchd_owns_this_process(
            Mock(return_value=loaded_elsewhere), uid=501))

        mine = Mock(returncode=0, stdout=f"\tpid = {os.getpid()}\n\tstate = running\n")
        self.assertTrue(launchd_owns_this_process(Mock(return_value=mine), uid=501))

        self.assertFalse(launchd_owns_this_process(
            Mock(return_value=Mock(returncode=113, stdout="")), uid=501))

    def test_windows_tray_lists_source_sync_next_to_ledger(self):
        source = inspect.getsource(PeachTray.__init__)
        self.assertIn('MenuItem("同步开发进度", self.sync_source,', source)
        self.assertIn('visible=lambda _: not standalone()', source)
        self.assertLess(source.index("同步开发进度"), source.index("ledger_menu_items"))
        self.assertLess(source.index("ledger_menu_items"), source.index("重启服务"))

    def test_windows_source_sync_tests_then_restarts_services(self):
        snapshot = VersionSnapshot("0.6.4", "master", "abc12345", False, True, "origin/master")
        completed = threading.Event()

        class Versions:
            root = ROOT

            def inspect(self):
                return snapshot

            def update(self):
                return UpdateResult(
                    "updated", "updated", snapshot, behind=1,
                    changed_paths=("docs/STATUS.md",),
                )

        class Updates:
            value = None

            def pending(self):
                return self.value

            def mark_pending(self, commit, changed_paths):
                self.value = PendingWindowsUpdate(commit, changed_paths)

            def prepare(self, _commit, _changed_paths):
                return WindowsUpdatePreparation("services", "测试通过。")

            def clear_pending(self):
                self.value = None
                completed.set()

        manager = Mock()
        manager.restart.return_value = True
        icon = Mock()
        with patch("peach.tray.pystray.Icon", return_value=Mock()):
            tray = PeachTray(manager, Versions(), Updates())
        tray.sync_source(icon)
        self.assertTrue(completed.wait(2))
        manager.restart.assert_called_once()
        manager.stop_owned.assert_not_called()

    def test_windows_source_sync_stops_services_only_after_replacer_is_ready(self):
        snapshot = VersionSnapshot("0.6.4", "master", "abc12345", False, True, "origin/master")
        stopped = threading.Event()

        class Versions:
            root = ROOT

            def inspect(self):
                return snapshot

            def update(self):
                return UpdateResult(
                    "updated", "updated", snapshot, behind=1,
                    changed_paths=("src/peach/tray.py",),
                )

        class Updates:
            value = None

            def pending(self):
                return self.value

            def mark_pending(self, commit, changed_paths):
                self.value = PendingWindowsUpdate(commit, changed_paths)

            def prepare(self, _commit, _changed_paths):
                return WindowsUpdatePreparation("replace", "替换已准备。")

        manager = Mock()
        manager.stop_owned.side_effect = lambda: stopped.set()
        icon = Mock()
        with patch("peach.tray.pystray.Icon", return_value=Mock()):
            tray = PeachTray(manager, Versions(), Updates())
        tray.sync_source(icon)
        self.assertTrue(stopped.wait(2))
        icon.stop.assert_called_once()
        manager.restart.assert_not_called()

    def test_tray_restart_asks_launchd_to_kill_and_relaunch_this_label(self):
        runner = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        restart_tray_process(runner, uid=501)
        self.assertEqual(
            runner.call_args.args[0],
            ["launchctl", "kickstart", "-k",
             "gui/501/io.github.longmeidao.peach.tray"],
        )

    def test_sync_changes_restart_the_tray_and_failed_kickstart_restores_services(self):
        self.assertTrue(tray_restart_required(("src/peach/sync.py",)))
        from peach import tray as tray_module
        source = inspect.getsource(tray_module.run_macos_menu_bar)
        self.assertIn("if restarted.returncode != 0:", source)
        self.assertIn("manager.start_missing()", source)


class FakeVersions:
    root = ROOT

    def __init__(self, snapshot, state="ahead", paths=("src/peach/tray.py",),
                 head="a" * 40):
        self.snapshot = snapshot
        self.state = state
        self.paths = paths
        self.head = head
        self.asked: list[str | None] = []

    def inspect(self):
        return self.snapshot

    def update(self):
        return UpdateResult(self.state, "当前已比更新通道领先 3 个提交。",
                            self.snapshot, ahead=3)

    def head_commit(self):
        return self.head

    def stale_build_paths(self, build_commit):
        self.asked.append(build_commit)
        return self.paths


class FakeUpdates:
    def __init__(self, preparation=WindowsUpdatePreparation("replace", "替换已准备。")):
        self.preparation = preparation
        self.value = None
        self.prepared: list[tuple[str, tuple[str, ...]]] = []

    def pending(self):
        return self.value

    def mark_pending(self, commit, changed_paths):
        self.value = PendingWindowsUpdate(commit, tuple(changed_paths))

    def prepare(self, commit, changed_paths):
        self.prepared.append((commit, tuple(changed_paths)))
        return self.preparation

    def clear_pending(self):
        self.value = None


class LocalRebuildTests(unittest.TestCase):
    """这台机器既是开发机又是生产机：提交先落本地再推 GitHub，本地永远领先。

    只在「落后远端」时重建的托盘因此从 2026-08-29 起一次都没重建过，运行中的 EXE 一直是
    旧代码。判据必须改成「EXE 里那份代码与当前检出差了什么」，与远端无关。
    """

    def snapshot(self, *, behind):
        return VersionSnapshot(
            "0.7.14", "master", "abc12345", False, True, "origin/master",
            build_commit="9f8e7d6c5b4a39281706f5e4d3c2b1a098765432", build_behind=behind,
        )

    def tray_for(self, versions, updates, manager=None, waiting=False):
        gate = Mock()
        gate.waiting = waiting
        with patch("peach.tray.pystray.Icon", return_value=Mock()):
            return PeachTray(manager or Mock(), versions, updates, gate)

    def drain(self, tray):
        deadline = time.monotonic() + 10
        while tray._action_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(tray._action_lock.locked(), "后台线程没有在 10 秒内结束")

    def messages(self, icon) -> str:
        return "\n".join(str(call.args[0]) for call in icon.notify.call_args_list)

    def test_a_stale_packaged_tray_rebuilds_itself_while_the_local_checkout_leads(self):
        versions = FakeVersions(self.snapshot(behind=3))
        updates = FakeUpdates()
        manager, icon = Mock(), Mock()
        tray = self.tray_for(versions, updates, manager)
        with patch.object(sys, "frozen", True, create=True), \
                patch("peach.tray.standalone", return_value=False):
            tray.sync_source(icon)
            self.drain(tray)
        self.assertEqual(versions.asked,
                         ["9f8e7d6c5b4a39281706f5e4d3c2b1a098765432"])
        self.assertEqual(updates.prepared, [("abc12345", ("src/peach/tray.py",))])
        manager.stop_owned.assert_called_once()
        icon.stop.assert_called_once()

    def test_changes_that_never_enter_the_package_leave_the_tray_alone(self):
        versions = FakeVersions(self.snapshot(behind=2), paths=("docs/STATUS.md",))
        updates = FakeUpdates()
        icon = Mock()
        tray = self.tray_for(versions, updates)
        with patch.object(sys, "frozen", True, create=True), \
                patch("peach.tray.standalone", return_value=False):
            tray.sync_source(icon)
            self.drain(tray)
        self.assertEqual(updates.prepared, [])
        self.assertIn("托盘无需重建", self.messages(icon))

    def test_a_tray_built_from_the_current_head_reports_that_it_matches(self):
        versions = FakeVersions(self.snapshot(behind=0))
        updates = FakeUpdates()
        icon = Mock()
        tray = self.tray_for(versions, updates)
        with patch.object(sys, "frozen", True, create=True), \
                patch("peach.tray.standalone", return_value=False):
            tray.sync_source(icon)
            self.drain(tray)
        self.assertEqual(updates.prepared, [])
        self.assertIn("托盘构建与检出一致", self.messages(icon))

    def test_a_source_checkout_has_nothing_to_rebuild(self):
        versions = FakeVersions(self.snapshot(behind=5))
        updates = FakeUpdates()
        icon = Mock()
        tray = self.tray_for(versions, updates)
        with patch.object(sys, "frozen", False, create=True), \
                patch("peach.tray.standalone", return_value=False):
            tray.sync_source(icon)
            self.drain(tray)
        self.assertEqual(updates.prepared, [])
        self.assertIn("当前不是打包托盘", self.messages(icon))

    def test_a_standalone_test_package_is_replaced_by_hand_not_rebuilt(self):
        versions = FakeVersions(self.snapshot(behind=5))
        updates = FakeUpdates()
        icon = Mock()
        tray = self.tray_for(versions, updates)
        with patch.object(sys, "frozen", True, create=True), \
                patch("peach.tray.standalone", return_value=True):
            tray.sync_source(icon)
            self.drain(tray)
        self.assertEqual(updates.prepared, [])
        self.assertIn("独立测试包", self.messages(icon))

    def test_a_fast_forward_from_the_channel_still_uses_the_paths_it_pulled(self):
        """`updated` 那条路不变：远端真有新提交时，重建范围就是这次快进带来的改动。"""
        snapshot = self.snapshot(behind=3)

        class Fetched(FakeVersions):
            def update(self):
                return UpdateResult("updated", "已同步 1 个提交。", snapshot, behind=1,
                                    changed_paths=("src/peach/web.py",))

        versions = Fetched(snapshot)
        updates = FakeUpdates()
        icon = Mock()
        tray = self.tray_for(versions, updates)
        with patch.object(sys, "frozen", True, create=True), \
                patch("peach.tray.standalone", return_value=False):
            tray.sync_source(icon)
            self.drain(tray)
        self.assertEqual(versions.asked, [], "快进自己就给出了改动清单")
        self.assertEqual(updates.prepared, [("abc12345", ("src/peach/web.py",))])


class AutomaticRebuildTests(unittest.TestCase):
    """托盘自己发现「我比检出旧」。判据是本地 HEAD，不 fetch。"""

    def setUp(self):
        self.clock = [1000.0]
        self.attempts: list[tuple] = []

    def now(self) -> float:
        return self.clock[0]

    def tray_for(self, versions, *, waiting=False):
        gate = Mock()
        gate.waiting = waiting
        with patch("peach.tray.pystray.Icon", return_value=Mock()):
            tray = PeachTray(Mock(), versions, FakeUpdates(), gate)
        tray._build_checked_at = self.clock[0]
        tray.sync_source = lambda *args, **kwargs: self.attempts.append(args)
        return tray

    def stale(self, head="a" * 40):
        return FakeVersions(
            VersionSnapshot("0.7.14", "master", "abc12345", False, True, "origin/master",
                            build_commit="9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
                            build_behind=3),
            head=head,
        )

    def test_the_same_head_is_attempted_once_and_a_new_one_reopens_the_chance(self):
        """重建要跑完整测试和一次打包。失败也算试过：多跑一遍不会改变失败原因。"""
        versions = self.stale()
        tray = self.tray_for(versions)
        with patch.object(sys, "frozen", True, create=True), \
                patch("peach.tray.standalone", return_value=False):
            self.assertFalse(tray.poll_build_age(self.now), "不到间隔不问 git")
            self.clock[0] += 301
            self.assertTrue(tray.poll_build_age(self.now))
            self.clock[0] += 301
            self.assertFalse(tray.poll_build_age(self.now), "同一个 HEAD 只试一次")
            versions.head = "b" * 40
            self.clock[0] += 301
            self.assertTrue(tray.poll_build_age(self.now))
        self.assertEqual(len(self.attempts), 2)

    def test_a_tray_that_matches_the_checkout_is_left_running(self):
        versions = self.stale()
        versions.snapshot = VersionSnapshot(
            "0.7.14", "master", "abc12345", False, True, "origin/master",
            build_commit="9f8e7d6c5b4a39281706f5e4d3c2b1a098765432", build_behind=0,
        )
        tray = self.tray_for(versions)
        with patch.object(sys, "frozen", True, create=True), \
                patch("peach.tray.standalone", return_value=False):
            self.clock[0] += 301
            self.assertFalse(tray.poll_build_age(self.now))
        self.assertEqual(self.attempts, [])

    def test_first_run_setup_outranks_a_stale_build(self):
        """首次设置没走完时连服务都还没定型，重建换掉的是用户正在用的入口。"""
        tray = self.tray_for(self.stale(), waiting=True)
        with patch.object(sys, "frozen", True, create=True), \
                patch("peach.tray.standalone", return_value=False):
            self.clock[0] += 301
            self.assertFalse(tray.poll_build_age(self.now))
        self.assertEqual(self.attempts, [])

    def test_source_checkouts_and_standalone_packages_never_build_anything(self):
        tray = self.tray_for(self.stale())
        with patch.object(sys, "frozen", False, create=True), \
                patch("peach.tray.standalone", return_value=False):
            self.clock[0] += 301
            self.assertFalse(tray.poll_build_age(self.now))
        with patch.object(sys, "frozen", True, create=True), \
                patch("peach.tray.standalone", return_value=True):
            self.clock[0] += 301
            self.assertFalse(tray.poll_build_age(self.now))
        self.assertEqual(self.attempts, [])


@unittest.skipUnless(sys.platform == "darwin", "菜单栏图标与服务规格是 macOS 专属")
class MacMenuBarTests(unittest.TestCase):
    def test_template_icon_is_a_black_silhouette(self):
        """macOS 菜单栏图标必须是 template image：彩色图不会跟着浅色/深色反色。"""
        colored = create_icon(32)
        template = create_icon(32, template=True)
        self.assertEqual(template.size, colored.size)
        self.assertEqual(template.getchannel("A").tobytes(), colored.getchannel("A").tobytes())
        opaque = [p for p in template.convert("RGBA").getdata() if p[3] > 0]
        self.assertTrue(opaque, "模板图不该整张透明")
        self.assertTrue(all(p[:3] == (0, 0, 0) for p in opaque), "模板图的颜色必须全黑")

    def test_service_specs_avoid_privileged_ports_and_a_pinned_address(self):
        """80/443 在 macOS 上要 root，所以服务跑在高位端口、由 pf 转发过去；
        地址钉死等于换个 Wi-Fi 就打不开。"""
        specs = build_service_specs()
        self.assertIn(len(specs), (1, 2))          # 没有 TLS 材料时只有 http
        self.assertEqual(specs[0].name, "http")
        for spec in specs:
            command = spec.command
            self.assertIn("--port", command)
            self.assertGreater(int(command[command.index("--port") + 1]), 1024)
            self.assertNotIn("--mdns-address", command)

    def test_https_spec_only_appears_with_real_tls_material(self):
        """macOS 这份是开发环境：没有本机 CA 也应该能用，不像 Windows 那样直接报错。"""
        specs = {spec.name: spec for spec in build_service_specs()}
        material = (SECRETS_DIR / "tls" / "peach.crt").is_file()
        self.assertEqual("https" in specs, material)
        if material:
            command = specs["https"].command
            self.assertIn("--ssl-certfile", command)
            # 两份服务只能有一份发布 mDNS，否则同名记录互相打架。
            self.assertNotIn("--no-mdns", command)
            self.assertTrue(str(specs["https"].verify).endswith("peach-local-ca.crt"))


class TemplateHookTests(unittest.TestCase):
    def test_apply_template_is_a_no_op_without_a_backing_nsimage(self):
        """拿不到底层 NSImage 就安静跳过：菜单栏项本身仍然可用。"""
        self.assertFalse(apply_macos_template(Mock(spec=[])))


class MacAppBundleTests(unittest.TestCase):
    """菜单栏项必须打成 bundle 才拿得稳。

    裸控制台进程启动 `peach-tray` 时，AppKit 的运行循环没有应用上下文会立刻返回：
    服务起来了、托盘父进程却安静退出，退出码 0、零输出。实测就是这样。
    """

    def setUp(self):
        self.module = load_script("build_macos_app")
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.tray = self.root / "peach-tray"
        self.tray.write_text("#!/bin/sh\n", encoding="utf-8")

    def test_bundle_declares_itself_as_a_menu_bar_agent(self):
        app = self.module.build(self.root, self.tray)
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        # LSUIElement：只在菜单栏出现，不占 Dock、不进 ⌘Tab。
        self.assertIs(info["LSUIElement"], True)
        self.assertEqual(info["CFBundleExecutable"], "Peach")
        self.assertTrue(info["CFBundleIdentifier"])

    def test_launcher_never_execs_the_interpreter_itself(self):
        """外壳不能自己 exec 到解释器。

        macOS 26 上主可执行文件是 exec 跳板时状态项注册不上：进程活着、NSStatusItem
        也建出来了，但按钮窗口永远是 (0,0,34,0)，菜单栏上什么都不出现（FB21015611）。
        双击只负责踢 LaunchAgent，真正的菜单栏进程是 launchd 的直接子进程。
        """
        app = self.module.build(self.root, self.tray)
        launcher = app / "Contents" / "MacOS" / "Peach"
        self.assertTrue(os.access(launcher, os.X_OK))
        body = launcher.read_text(encoding="utf-8")
        self.assertIn("launchctl kickstart", body)
        self.assertNotIn(str(self.tray), body)

    def test_bundle_carries_a_rounded_icon(self):
        """方形原图直接当图标会比周围大一圈、四角还是直的。"""
        app = self.module.build(self.root, self.tray)
        import plistlib as _plistlib
        info = _plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        icon = ROOT / "resources" / "peach.icns"
        if icon.is_file():
            self.assertEqual(info.get("CFBundleIconFile"), "peach")
            self.assertTrue((app / "Contents" / "Resources" / "peach.icns").is_file())

    def test_rebuild_replaces_a_stale_bundle(self):
        app = self.module.build(self.root, self.tray)
        (app / "Contents" / "MacOS" / "stale").write_text("x", encoding="utf-8")
        self.module.build(self.root, self.tray)
        self.assertFalse((app / "Contents" / "MacOS" / "stale").exists())


class MacIdentityTests(unittest.TestCase):
    """bundle ID、launchd 标签与 pf anchor 名只有 `peach.appid` 一个来源。

    这四处名字对不上时没有任何报错：`launchctl kickstart` 去踢一个不存在的服务、
    pf 加载一个空 anchor，表现只是菜单栏没图标、`peach.local` 不带端口打不开。
    所以每个消费者都在这里对着同一处常量核一遍。
    """

    def setUp(self):
        self.shell = (ROOT / "scripts" / "setup_macos_port80.sh").read_text(encoding="utf-8")

    def test_every_consumer_takes_the_identifier_from_peach_appid(self):
        self.assertEqual(tray_module.LAUNCH_AGENT_LABEL, appid.MACOS_LAUNCH_AGENT_LABEL)
        self.assertEqual(load_script("build_macos_app").BUNDLE_ID, appid.MACOS_BUNDLE_ID)
        self.assertEqual(load_script("build_macos_app").LABEL,
                         appid.MACOS_LAUNCH_AGENT_LABEL)
        self.assertEqual(load_script("install_macos_agent").LABEL,
                         appid.MACOS_LAUNCH_AGENT_LABEL)

    def test_the_identifier_is_the_repository_owner_not_a_private_domain(self):
        """下载者装上的东西不该带着维护者的私有域名（ADR-0023 第 4 阶段）。"""
        for value in (appid.MACOS_BUNDLE_ID, appid.MACOS_LAUNCH_AGENT_LABEL,
                      appid.MACOS_PF_ANCHOR):
            self.assertTrue(value.startswith("io.github."), value)

    def test_the_shell_script_pins_the_same_anchor_names(self):
        """POSIX shell import 不了 Python，那两行字面量只能由这条用例守住。"""
        found = dict(re.findall(r'^(ANCHOR_NAME|LEGACY_ANCHOR_NAMES)="([^"]*)"',
                                self.shell, re.MULTILINE))
        self.assertEqual(found.get("ANCHOR_NAME"), appid.MACOS_PF_ANCHOR)
        self.assertEqual(found.get("LEGACY_ANCHOR_NAMES"),
                         " ".join(appid.LEGACY_MACOS_PF_ANCHORS))

    def test_the_port_forward_script_clears_the_legacy_anchor_on_both_paths(self):
        """遗留 anchor 的那份 LaunchDaemon 每次开机都抢同一个 80/443 转发目标。"""
        self.assertIn("remove_legacy()", self.shell)
        uninstall = self.shell.split('if [ "$ACTION" = "uninstall" ]', 1)[1]
        self.assertIn("remove_legacy", uninstall)
        self.assertIn("strip_conf /etc/pf.conf", uninstall)


class MacLegacyAgentTests(unittest.TestCase):
    """装新标签之前先卸掉遗留标签的 LaunchAgent。

    `launchctl bootout` 只作用于给定的那一个 label，装新标签这一步碰不到遗留的那份：
    它会继续开机自启一个菜单栏进程，也继续占着 80/443 的转发。
    """

    def setUp(self):
        self.module = load_script("install_macos_agent")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.agents = Path(tmp.name).resolve() / "LaunchAgents"
        self.agents.mkdir()
        self.module.launch_agents_dir = lambda: self.agents
        self.calls: list[tuple[str, ...]] = []

    def _launchctl(self, *args: str, loaded: bool):
        self.calls.append(args)
        returncode = 0 if (args[0] == "print" and loaded) else 1
        return Mock(returncode=returncode, stdout="", stderr="")

    def test_installing_boots_out_the_legacy_label_and_deletes_its_plist(self):
        legacy = appid.LEGACY_MACOS_LAUNCH_AGENT_LABELS[0]
        stale = self.agents / f"{legacy}.plist"
        stale.write_bytes(b"stale")
        self.module.launchctl = lambda *args: self._launchctl(*args, loaded=True)

        self.assertEqual(self.module.remove_legacy_agents("gui/501"), [legacy])
        self.assertFalse(stale.exists())
        self.assertIn(("bootout", f"gui/501/{legacy}"), self.calls)

    def test_a_machine_without_the_legacy_label_is_left_alone(self):
        self.module.launchctl = lambda *args: self._launchctl(*args, loaded=False)
        self.assertEqual(self.module.remove_legacy_agents("gui/501"), [])
        self.assertEqual([args[0] for args in self.calls], ["print"])

    def test_install_clears_the_legacy_label_before_writing_the_new_plist(self):
        """顺序反了就会把刚装好的那份又 bootout 掉一次，白等一轮超时。"""
        source = inspect.getsource(self.module.run)
        self.assertLess(source.index("remove_legacy_agents"),
                        source.index("plistlib.dumps"))


class TrayCommandLineTests(unittest.TestCase):
    """`peach-tray` 的参数解析先于一切进程级副作用。

    新用户试探 `peach-tray --help` 时，程序不能先去拿单实例锁：那一步会在 venv 旁边
    凭空建出 `peach-data/state/`，之后的安装探测就把这台机器当成已配置。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name).resolve() / "state"
        for target in ("peach.tray.enable_hidpi", "peach.tray.SingleInstance",
                       "peach.tray.ServiceManager", "peach.tray.webbrowser"):
            patcher = patch(target)
            self.addCleanup(patcher.stop)
            setattr(self, target.rsplit(".", 1)[1], patcher.start())
        patcher = patch("peach.tray.STATE_DIR", self.state_dir)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _run(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                tray_main(argv)
        return raised.exception.code, stdout.getvalue(), stderr.getvalue()

    def _assert_nothing_touched(self):
        self.assertFalse(self.state_dir.exists(), "帮助与参数错误不得创建状态目录")
        self.enable_hidpi.assert_not_called()
        self.SingleInstance.assert_not_called()
        self.ServiceManager.assert_not_called()
        self.webbrowser.open.assert_not_called()

    def test_help_exits_zero_and_leaves_the_state_directory_alone(self):
        code, stdout, _ = self._run(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("peach-tray", stdout)
        self.assertIn("托盘", stdout)
        self._assert_nothing_touched()

    def test_unknown_argument_exits_two_and_leaves_the_state_directory_alone(self):
        code, _, stderr = self._run(["--bogus"])
        self.assertEqual(code, 2)
        self.assertIn("--bogus", stderr)
        self._assert_nothing_touched()


class ServiceRevivalTests(unittest.TestCase):
    """托盘不只在启动时拉一次服务：端口空了就补拉，但不跟正在维护服务的动作抢。

    2026-09-25 一份被强杀的托盘留下两棵孤儿服务，新托盘启动时见端口健康就没拉自己的；
    孤儿被停掉以后端口空了一分半，直到有人去点「重启服务」。
    """

    SPEC = ServiceSpec("https", "https://local/healthz", ("peach", "serve"), True)

    def tray(self, manager):
        gate = Mock()
        gate.waiting = False
        snapshot = VersionSnapshot("0.7.14", "master", "abc12345", False, True, "origin/master")
        with patch("peach.tray.pystray.Icon", return_value=Mock()):
            return PeachTray(manager, FakeVersions(snapshot), FakeUpdates(), gate)

    def one_health_round(self, tray):
        stop = Mock()
        stop.wait.side_effect = [False, True]
        stop.is_set.return_value = False
        tray._stop_event = stop
        with patch("peach.standalone_update.poll"), patch("peach.desktop_uninstall.poll"):
            tray._monitor()

    def manager(self, *, healthy):
        manager = Mock()
        manager.specs = (self.SPEC,)
        manager.healthy.return_value = healthy
        return manager

    def test_a_port_that_went_dark_is_refilled_on_the_next_health_round(self):
        manager = self.manager(healthy=False)
        self.one_health_round(self.tray(manager))
        manager.start_missing.assert_called_once_with()

    def test_healthy_services_are_left_alone(self):
        manager = self.manager(healthy=True)
        self.one_health_round(self.tray(manager))
        manager.start_missing.assert_not_called()

    def test_revival_yields_to_an_action_that_is_stopping_services_on_purpose(self):
        manager = self.manager(healthy=False)
        tray = self.tray(manager)
        with tray._action_lock:
            self.assertFalse(tray.revive_services())
        tray._stop_event.set()
        self.assertFalse(tray.revive_services())
        manager.start_missing.assert_not_called()

    def test_a_healthy_port_it_did_not_start_is_reported_not_adopted_silently(self):
        popen = Mock()
        with tempfile.TemporaryDirectory() as directory:
            manager = ServiceManager((self.SPEC,), popen=popen, log_dir=Path(directory),
                                     health_get=lambda *args, **kwargs: Response())
            with self.assertLogs("peach.tray", "WARNING") as logs:
                manager.start_missing()
        popen.assert_not_called()
        self.assertIn("不是本托盘拉起的", logs.output[0])

    def test_log_handles_go_to_the_child_and_are_closed_in_the_tray(self):
        """补拉会反复发生，父进程每拉一次就多握两个日志句柄的话会越攒越多。"""
        handed = []

        def popen(_command, **kwargs):
            handed.extend((kwargs["stdout"], kwargs["stderr"]))
            process = Mock()
            process.poll.return_value = None
            return process

        with tempfile.TemporaryDirectory() as directory:
            manager = ServiceManager((self.SPEC,), popen=popen, log_dir=Path(directory),
                                     health_get=Mock(side_effect=OSError("down")))
            manager.start_missing()
            self.assertEqual(len(handed), 2)
            self.assertTrue(all(handle.closed for handle in handed))


class TrayLogFileTests(unittest.TestCase):
    def test_the_file_keeps_tray_events_and_drops_per_probe_http_noise(self):
        """健康检查十秒一轮，httpx 的 INFO 进文件的话一天一万多行，真正的启停记录被淹掉。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "tray.log"
            root = logging.getLogger()
            level = root.level
            root.setLevel(logging.INFO)
            handler = tray_module.log_to_file(path)
            self.assertIsNotNone(handler)
            try:
                logging.getLogger("httpx").info("HTTP Request: GET http://127.0.0.1/healthz")
                logging.getLogger("httpx").warning("连接被拒绝")
                logging.getLogger("peach.tray").info("拉起 http 服务，PID 1")
            finally:
                root.removeHandler(handler)
                handler.close()
                root.setLevel(level)
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("HTTP Request", text)
        self.assertIn("连接被拒绝", text)
        self.assertIn("拉起 http 服务", text)


class ReadyResponse:
    def __init__(self, status_code, payload):
        self.status_code, self.payload = status_code, payload

    def json(self):
        return self.payload


class FakeStep:
    """给出固定结果的依赖核对或迁移，顺手记下被调用的时刻。"""

    def __init__(self, events, label, step):
        self.events, self.label, self.step = events, label, step

    def check(self):
        self.events.append(self.label)
        return self.step

    apply = check


class TrayPreparationTests(unittest.TestCase):
    """依赖、迁移与就绪检查在托盘里的落点（ADR-0091）。"""

    CURRENT = runtime_prepare.Step("dependencies", "current", "项目 venv 与 uv.lock 一致")
    STALE = runtime_prepare.Step("dependencies", "stale",
                                 "项目 venv 与 uv.lock 不一致，要装 1 个包：grpcio==1.84.0")
    APPLIED = runtime_prepare.Step("migrations", "applied", "已执行迁移 0042，迁移前备份 x.db")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events: list = []
        self.spawned: list = []

    def tearDown(self):
        self.temp.cleanup()

    def preparation(self, dependencies=CURRENT, migration=APPLIED, process=None):
        def spawn():
            self.spawned.append(True)
            if process is None:
                raise OSError("python.exe missing")
            return process

        return tray_module.TrayPreparation(
            dependencies=FakeStep(self.events, "deps", dependencies),
            migrations=FakeStep(self.events, "migrate", migration),
            state_dir=self.root / "state",
            restart_log=self.root / "logs" / "tray-restart.out.log",
            spawn_restart=spawn,
        )

    def manager(self, *, ready=True, problems=()):
        manager = Mock()
        manager.specs = ()
        manager.start_missing.side_effect = lambda: self.events.append("start")
        manager.wait_until_ready.return_value = ready
        manager.readiness_problems.return_value = list(problems)

        def restart(prepare=None):
            self.events.append("stop")
            if prepare is not None:
                prepare()
            self.events.append("start")
            return ready

        manager.restart.side_effect = restart
        return manager

    def tray(self, manager, preparation):
        gate = Mock()
        gate.waiting = False
        snapshot = VersionSnapshot("0.37.0", "master", "abc12345", False, True, "origin/master")
        updates = FakeUpdates()
        updates.sweep_artifacts = lambda: None
        with patch("peach.tray.pystray.Icon", return_value=Mock()):
            return PeachTray(manager, FakeVersions(snapshot), updates, gate,
                             preparation=preparation)

    def run_tray(self, tray):
        with (
            patch("peach.tray.ledger_backups.prune"),
            patch("peach.tray.sweep_onefile_extractions"),
            patch.object(tray, "_monitor"),
        ):
            tray.run()

    def press_restart(self, tray):
        icon = Mock()
        tray.restart(icon)
        self.assertTrue(tray._action_lock.acquire(timeout=5))
        tray._action_lock.release()
        return icon

    def test_startup_migrates_before_the_first_service_starts(self):
        tray = self.tray(self.manager(), self.preparation())
        self.run_tray(tray)
        self.assertEqual(self.events[:3], ["deps", "migrate", "start"])
        recorded = runtime_prepare.read_record(self.root / "state", pid=os.getpid())
        self.assertTrue(recorded["ok"])
        self.assertEqual([step["name"] for step in recorded["steps"]],
                         ["dependencies", "migrations", "readiness"])
        self.assertIsNone(tray._startup_warning)

    def test_startup_reports_a_failed_migration_and_an_unready_schema(self):
        failed = runtime_prepare.Step("migrations", "failed", "migrate status 失败：database is locked")
        manager = self.manager(problems=["HTTPS 未就绪：数据库结构与迁移不一致"])
        tray = self.tray(manager, self.preparation(migration=failed))
        self.run_tray(tray)
        self.assertIn("database is locked", tray._startup_warning)
        self.assertIn("数据库结构与迁移不一致", tray._startup_warning)
        self.assertFalse(runtime_prepare.read_record(self.root / "state", pid=os.getpid())["ok"])

    def test_restart_migrates_while_the_services_are_stopped(self):
        tray = self.tray(self.manager(), self.preparation())
        icon = self.press_restart(tray)
        self.assertEqual(self.events, ["deps", "stop", "migrate", "start"])
        self.assertEqual(self.spawned, [])
        icon.notify.assert_not_called()

    def test_stale_dependencies_hand_the_restart_to_a_whole_tray_restart(self):
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("restart", 1)
        manager = self.manager()
        tray = self.tray(manager, self.preparation(dependencies=self.STALE, process=process))
        icon = self.press_restart(tray)
        self.assertEqual(self.spawned, [True])
        manager.restart.assert_not_called()
        self.assertIn("整体重启托盘", icon.notify.call_args_list[0].args[0])

    def test_a_refused_whole_tray_restart_falls_back_to_the_services(self):
        log = self.root / "logs" / "tray-restart.out.log"
        log.parent.mkdir(parents=True)
        log.write_text(json.dumps({"ok": False, "message": "拒绝重启：PID 48420 还在用项目 venv"},
                                  ensure_ascii=False) + "\n", encoding="utf-8")
        process = Mock()
        process.wait.return_value = 1
        manager = self.manager()
        tray = self.tray(manager, self.preparation(dependencies=self.STALE, process=process))
        icon = self.press_restart(tray)
        manager.restart.assert_called_once()
        messages = " ".join(call.args[0] for call in icon.notify.call_args_list)
        self.assertIn("48420", messages)
        # 依赖仍不一致，重启子服务之后也要再说一遍。
        self.assertIn("grpcio", icon.notify.call_args_list[-1].args[0])

    def test_without_a_preparation_the_tray_only_restarts_services(self):
        manager = self.manager()
        tray = self.tray(manager, None)
        self.press_restart(tray)
        self.assertEqual(self.events, ["stop", "start"])
        manager.readiness_problems.assert_not_called()

    def test_service_restart_runs_the_preparation_between_stop_and_start(self):
        order = []
        process = Mock()
        process.poll.return_value = None
        process.terminate.side_effect = lambda: order.append("stop")
        process.wait.return_value = 0
        spec = ServiceSpec("https", "https://local/healthz", ("peach", "serve"), True)
        manager = ServiceManager((spec,), popen=lambda *_a, **_k: order.append("start") or process,
                                 log_dir=self.root / "logs",
                                 health_get=Mock(side_effect=OSError("down")))
        with patch("peach.tray.assign_to_job"), patch("peach.tray.create_kill_on_close_job"):
            manager.start_missing()
            order.clear()
            with patch.object(manager, "wait_until_ready", return_value=True):
                manager.restart(prepare=lambda: order.append("prepare"))
        self.assertEqual(order, ["stop", "prepare", "start"])

    def test_readiness_is_read_from_the_api_service_only(self):
        asked = []

        def get(url, **kwargs):
            asked.append((url, kwargs["verify"]))
            if url.startswith("http://"):
                return ReadyResponse(200, {"ok": True, "service": "peach-redirect"})
            return ReadyResponse(503, {"ready": False, "checks": {
                "configured": True, "web": True, "database": True, "schema": False}})

        specs = (ServiceSpec("http", "http://127.0.0.1/healthz", ("peach",), True),
                 ServiceSpec("https", "https://192.0.2.10/healthz", ("peach",), "ca.crt"))
        manager = ServiceManager(specs, health_get=get, log_dir=self.root)
        self.assertEqual(manager.readiness_problems(), ["HTTPS 未就绪：数据库结构与迁移不一致"])
        self.assertEqual(asked, [("http://127.0.0.1/healthz?ready=1", True),
                                 ("https://192.0.2.10/healthz?ready=1", "ca.crt")])

    def test_the_whole_tray_restart_is_detached_and_logged(self):
        python = self.root / ".venv" / tray_module._BIN_DIR / (
            "python.exe" if os.name == "nt" else "python")
        script = self.root / "scripts" / "restart_windows_tray.py"
        log = self.root / "logs" / "tray-restart.out.log"
        with self.assertRaises(FileNotFoundError):
            tray_module.spawn_tray_restart(log, popen=Mock(), root=self.root)
        for path in (python, script):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        seen = {}

        def popen(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return Mock()

        tray_module.spawn_tray_restart(log, popen=popen, root=self.root)
        self.assertEqual(seen["argv"][-3:], [str(script), "--source", "--force"])
        self.assertEqual(seen["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertTrue(seen["stdout"].closed)
        if os.name == "nt":
            self.assertTrue(seen["creationflags"] & subprocess.DETACHED_PROCESS)


if __name__ == "__main__":
    unittest.main()
