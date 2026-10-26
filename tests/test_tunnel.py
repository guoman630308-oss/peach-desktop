import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from peach import access, api, routes_auth, routes_configuration, settings_file, tunnel
from peach.config import PeachSettings

#: 假扮 cloudflared 的真实子进程：写 pidfile、按行吐出一条入口，然后等着被收掉。
FAKE_CLOUDFLARED = '''\
import os
import sys
import time

arguments = sys.argv[1:]
with open(arguments[arguments.index("--pidfile") + 1], "w", encoding="ascii") as handle:
    handle.write(str(os.getpid()))
print("INF | Your quick Tunnel has been created! https://real-child.trycloudflare.com", flush=True)
while True:
    time.sleep(0.2)
'''


#: 夹具里的隧道令牌一律是这个假值；真实令牌不进仓库，也不进任何测试输出。
FAKE_TOKEN = "fake-tunnel-token-for-tests"


class _Config:
    def __init__(self, root: Path, *, port: int = 8900, binary: str = "",
                 mode: str = "quick", token: str = "", hostname: str = ""):
        self.data_root = root
        self.server = SimpleNamespace(port=port, mdns_name="peach")
        self.tunnel = SimpleNamespace(
            binary=binary, mode=mode, token=token, hostname=hostname,
        )

    def directory(self, name: str) -> Path:
        return self.data_root / name


class _Stream:
    def __init__(self, *lines: str):
        self._lines = iter(lines)
        self.closed = False

    def readline(self):
        try:
            return next(self._lines)
        except StopIteration:
            return ""

    def close(self):
        self.closed = True


class _Process:
    def __init__(self, stream, pid: int = 4123):
        self.stdout = stream
        self.pid = pid
        self._returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def kill(self):
        self.killed = True
        self._returncode = -9

    def wait(self, timeout=None):
        if self._returncode is None:
            self._returncode = 0
        return self._returncode


class _StubbornProcess(_Process):
    """收到 terminate 之后继续活着，逼出 kill 那一步。"""

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self._returncode is None:
            raise subprocess.TimeoutExpired("cloudflared", timeout)
        return self._returncode


class TunnelPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.binary = self.root / "cloudflared.exe"
        self.binary.write_bytes(b"fake")
        self.config = _Config(self.root, binary=str(self.binary))
        self.access_path = self.root / "secrets" / "access.json"
        access.save(self.access_path, "a-strong-test-password")

    def tearDown(self):
        self.temp.cleanup()

    def test_quick_url_extraction_rejects_non_cloudflare_urls(self):
        self.assertEqual(
            tunnel.extract_quick_url(
                "INF | https://AbC-123.trycloudflare.com. connected"
            ),
            "https://AbC-123.trycloudflare.com",
        )
        self.assertIsNone(tunnel.extract_quick_url("https://example.com/trycloudflare.com"))

    def test_standalone_uses_loopback_http_without_tls_options(self):
        plan = tunnel.plan_for_config(
            self.config, access_path=self.access_path, token="internal-token",
            standalone_mode=True,
        )
        self.assertEqual(plan.origin.url, "http://127.0.0.1:8900")
        self.assertNotIn("--origin-server-name", plan.command)
        self.assertNotIn("--origin-ca-pool", plan.command)

    def test_standalone_uses_the_actual_runtime_port_when_supplied(self):
        plan = tunnel.plan_for_config(
            self.config, access_path=self.access_path, token="internal-token",
            standalone_mode=True, https_port=18901,
        )
        self.assertEqual(plan.origin.url, "http://127.0.0.1:18901")

    def test_source_uses_lan_https_and_project_ca(self):
        ca = self.config.directory("secrets") / "tls" / "peach-local-ca.crt"
        ca.parent.mkdir(parents=True)
        ca.write_bytes(b"ca")
        plan = tunnel.plan_for_config(
            self.config, access_path=self.access_path, token="internal-token",
            standalone_mode=False, lan_address="192.0.2.162",
        )
        self.assertEqual(plan.origin.url, "https://192.0.2.162:443")
        self.assertEqual(plan.origin.origin_server_name, "peach.local")
        self.assertIn(("--origin-ca-pool", str(ca)), tuple(zip(plan.command, plan.command[1:])))

    def test_source_can_use_a_nonstandard_https_port(self):
        ca = self.config.directory("secrets") / "tls" / "peach-local-ca.crt"
        ca.parent.mkdir(parents=True)
        ca.write_bytes(b"ca")
        origin = tunnel.origin_for_config(
            self.config, standalone_mode=False, lan_address="192.0.2.162",
            https_port=8443,
        )
        self.assertEqual(origin.url, "https://192.0.2.162:8443")

    def test_source_ipv6_origin_is_bracketed(self):
        ca = self.config.directory("secrets") / "tls" / "peach-local-ca.crt"
        ca.parent.mkdir(parents=True)
        ca.write_bytes(b"ca")
        origin = tunnel.origin_for_config(
            self.config, standalone_mode=False, lan_address="2001:db8::5",
        )
        self.assertEqual(origin.url, "https://[2001:db8::5]:443")

    def test_source_without_tls_is_rejected_before_starting_a_tunnel(self):
        with self.assertRaisesRegex(tunnel.TunnelError, "需要正在运行的 HTTPS"):
            tunnel.plan_for_config(
                self.config, access_path=self.access_path, token="internal-token",
                standalone_mode=False, lan_address="192.0.2.162",
                tls_enabled=False,
            )

    def test_open_access_and_missing_token_are_rejected_before_binary_lookup(self):
        access.save(self.access_path, "")
        with self.assertRaisesRegex(tunnel.TunnelError, "访问密码"):
            tunnel.plan_for_config(
                self.config, access_path=self.access_path, token="internal-token",
                standalone_mode=True,
            )
        access.save(self.access_path, "a-strong-test-password")
        with self.assertRaisesRegex(tunnel.TunnelError, "内部访问令牌"):
            tunnel.plan_for_config(
                self.config, access_path=self.access_path, token="",
                standalone_mode=True,
            )

    def test_missing_binary_is_rejected(self):
        self.binary.unlink()
        self.config.tunnel.binary = str(self.root / "missing.exe")
        with self.assertRaisesRegex(tunnel.TunnelError, "找不到 cloudflared"):
            tunnel.plan_for_config(
                self.config, access_path=self.access_path, token="internal-token",
                standalone_mode=True, environ={}, which=lambda _name: None,
            )

    def test_a_binary_dropped_into_the_data_root_is_not_picked_up(self):
        self.config.tunnel.binary = ""
        with self.assertRaisesRegex(tunnel.TunnelError, "找不到 cloudflared"):
            tunnel.plan_for_config(
                self.config, access_path=self.access_path, token="internal-token",
                standalone_mode=True, environ={}, which=lambda _name: None,
            )

    def test_a_plan_built_from_injected_settings_never_reads_the_settings_file(self):
        settings = SimpleNamespace(
            access_path=self.access_path, token="internal-token",
            tunnel_standalone=True, tunnel_binary=str(self.binary),
            tunnel_origin_port=18902, tunnel_lan_address=None,
            tunnel_ca_path=self.root / "missing-ca.crt", tls_enabled=False,
            mdns_name="peach",
        )
        with mock.patch.object(
            tunnel, "validate_access", wraps=tunnel.validate_access,
        ) as validated:
            plan = tunnel.plan_for_settings(settings, environ={})
        self.assertEqual(plan.origin.url, "http://127.0.0.1:18902")
        self.assertEqual(plan.binary, self.binary)
        validated.assert_called_once_with(self.access_path, "internal-token")

    def test_source_settings_without_tls_are_rejected(self):
        settings = SimpleNamespace(
            access_path=self.access_path, token="internal-token",
            tunnel_standalone=False, tunnel_binary=str(self.binary),
            tunnel_origin_port=443, tunnel_lan_address="192.0.2.162",
            tunnel_ca_path=self.root / "ca.crt", tls_enabled=False,
            mdns_name="peach",
        )
        with self.assertRaisesRegex(tunnel.TunnelError, "需要正在运行的 HTTPS"):
            tunnel.plan_for_settings(settings, environ={})

    def named_config(self, **overrides):
        values = {
            "binary": str(self.binary), "mode": "named",
            "token": FAKE_TOKEN, "hostname": "peach.example.com",
        }
        return _Config(self.root, **{**values, **overrides})

    def test_named_mode_runs_by_token_and_takes_its_url_from_the_hostname(self):
        plan = tunnel.plan_for_config(
            self.named_config(), access_path=self.access_path, token="internal-token",
            standalone_mode=False, lan_address="192.0.2.162",
        )
        self.assertEqual(plan.mode, "named")
        self.assertEqual(plan.url, "https://peach.example.com")
        self.assertEqual(plan.origin.url, "https://peach.example.com")
        self.assertEqual(plan.command[1], "tunnel")
        self.assertEqual(plan.command[-3:], ("run", "--token", FAKE_TOKEN))
        self.assertNotIn("--url", plan.command)
        run_index = plan.command.index("run")
        for option in ("--no-autoupdate", "--loglevel", "--metrics"):
            self.assertLess(plan.command.index(option), run_index)
        self.assertEqual(plan.command[plan.command.index("--metrics") + 1], "127.0.0.1:0")

    def test_a_standalone_package_refuses_the_named_mode(self):
        with self.assertRaisesRegex(tunnel.TunnelError, tunnel.STANDALONE_NAMED_ERROR):
            tunnel.plan_for_config(
                self.named_config(), access_path=self.access_path, token="internal-token",
                standalone_mode=True,
            )

    def test_named_mode_needs_both_a_token_and_a_hostname(self):
        with self.assertRaisesRegex(tunnel.TunnelError, "隧道令牌"):
            tunnel.plan_for_config(
                self.named_config(token="  "), access_path=self.access_path,
                token="internal-token", standalone_mode=False,
            )
        with self.assertRaisesRegex(tunnel.TunnelError, "公开主机名"):
            tunnel.plan_for_config(
                self.named_config(hostname=""), access_path=self.access_path,
                token="internal-token", standalone_mode=False,
            )

    def test_a_hostname_with_a_path_or_a_bare_label_is_rejected(self):
        for value in ("peach.example.com/admin", "localhost", "peach.example.com:8443", "-bad.example.com"):
            with self.subTest(value=value), self.assertRaises(tunnel.TunnelError):
                tunnel.normalize_hostname(value)

    def test_a_hostname_copied_with_its_scheme_still_resolves(self):
        self.assertEqual(
            tunnel.normalize_hostname(" https://Peach.Example.COM/ "), "peach.example.com")

    def test_an_unknown_mode_is_refused_instead_of_falling_back(self):
        with self.assertRaisesRegex(tunnel.TunnelError, "quick 或 named"):
            tunnel.normalize_mode("tunnel")
        self.assertEqual(tunnel.normalize_mode(""), "quick")

    def test_injected_settings_carry_the_named_mode_without_an_origin_lookup(self):
        settings = SimpleNamespace(
            access_path=self.access_path, token="internal-token",
            tunnel_standalone=False, tunnel_binary=str(self.binary),
            tunnel_mode="named", tunnel_token=FAKE_TOKEN,
            tunnel_hostname="peach.example.com",
            tunnel_origin_port=None, tunnel_lan_address=None,
            tunnel_ca_path=self.root / "missing-ca.crt", tls_enabled=True,
            mdns_name="peach",
        )
        plan = tunnel.plan_for_settings(settings, environ={})
        self.assertEqual(plan.url, "https://peach.example.com")
        self.assertEqual(plan.secret, FAKE_TOKEN)

    def test_a_standalone_package_refuses_named_settings_at_startup(self):
        settings = SimpleNamespace(
            access_path=self.access_path, token="internal-token",
            tunnel_standalone=True, tunnel_binary=str(self.binary),
            tunnel_mode="named", tunnel_token=FAKE_TOKEN,
            tunnel_hostname="peach.example.com",
            tunnel_origin_port=8900, tunnel_lan_address=None,
            tunnel_ca_path=self.root / "missing-ca.crt", tls_enabled=False,
            mdns_name="peach",
        )
        with self.assertRaisesRegex(tunnel.TunnelError, tunnel.STANDALONE_NAMED_ERROR):
            tunnel.plan_for_settings(settings, environ={})


class TunnelManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.processes = []

    def tearDown(self):
        self.temp.cleanup()

    def popen(self, command, **kwargs):
        # `--pidfile` 是 `tunnel` 这一级的选项，得紧跟在子命令后面，落到 `run` 后面会被拒。
        self.assertEqual(command[1:3], ["tunnel", "--pidfile"])
        pid_index = command.index("--pidfile") + 1
        Path(command[pid_index]).write_text("4123\n", encoding="ascii")
        process = _Process(_Stream("INF | Your quick Tunnel has been created! https://unit-7.trycloudflare.com\n"))
        self.processes.append(process)
        return process

    def plan(self):
        return tunnel.TunnelPlan(
            binary=self.root / "cloudflared.exe",
            origin=tunnel.TunnelOrigin("http://127.0.0.1:8900"),
            command=("cloudflared", "tunnel", "--url", "http://127.0.0.1:8900"),
        )

    def named_plan(self):
        return tunnel.TunnelPlan(
            binary=self.root / "cloudflared.exe",
            origin=tunnel.TunnelOrigin("https://peach.example.com"),
            command=("cloudflared", "tunnel", "run", "--token", FAKE_TOKEN),
            mode="named", url="https://peach.example.com", secret=FAKE_TOKEN,
        )

    def test_a_named_tunnel_is_ready_on_its_pidfile_and_keeps_its_own_url(self):
        manager = tunnel.TunnelManager(
            self.root / "state", self.root / "logs", popen=self.popen, wait_timeout=2,
        )
        started = manager.start(self.named_plan())
        self.assertEqual(started.state, "running")
        # 子进程那一行随机地址不能盖掉设置里定下来的入口。
        self.assertEqual(started.url, "https://peach.example.com")
        manager.stop()

    def test_the_tunnel_token_never_reaches_the_log_or_the_snapshot(self):
        def leaky(command, **kwargs):
            pid_index = command.index("--pidfile") + 1
            Path(command[pid_index]).write_text("4123\n", encoding="ascii")
            process = _Process(_Stream(f"INF | registering with token {FAKE_TOKEN}\n"))
            self.processes.append(process)
            return process

        manager = tunnel.TunnelManager(
            self.root / "state", self.root / "logs", popen=leaky, wait_timeout=2,
        )
        started = manager.start(self.named_plan())
        manager.stop()
        log = (self.root / "logs" / tunnel.LOG_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn(FAKE_TOKEN, log)
        self.assertIn("***", log)
        self.assertNotIn(FAKE_TOKEN, repr(started))
        state_file = self.root / "state" / tunnel.STATE_FILENAME
        if state_file.exists():
            self.assertNotIn(FAKE_TOKEN, state_file.read_text(encoding="utf-8"))

    def test_start_writes_url_and_stop_removes_state(self):
        manager = tunnel.TunnelManager(
            self.root / "state", self.root / "logs", popen=self.popen, wait_timeout=2,
        )
        started = manager.start(self.plan())
        self.assertEqual(started.state, "running")
        self.assertEqual(started.url, "https://unit-7.trycloudflare.com")
        state = json.loads((self.root / "state" / tunnel.STATE_FILENAME).read_text())
        self.assertEqual(state["url"], started.url)
        self.assertIn(started.url, (self.root / "logs" / tunnel.LOG_FILENAME).read_text())
        stopped = manager.stop()
        self.assertEqual(stopped.state, "stopped")
        self.assertFalse((self.root / "state" / tunnel.STATE_FILENAME).exists())
        self.assertTrue(self.processes[0].terminated)

    def test_child_exit_after_start_is_reported_as_error(self):
        manager = tunnel.TunnelManager(
            self.root / "state", self.root / "logs", popen=self.popen, wait_timeout=2,
        )
        manager.start(self.plan())
        self.processes[0]._returncode = 1
        snapshot = manager.snapshot()
        self.assertEqual(snapshot.state, "error")
        self.assertEqual(snapshot.url, "https://unit-7.trycloudflare.com")

    def test_saved_running_state_is_not_reused_after_restart(self):
        state_dir = self.root / "state"
        state_dir.mkdir()
        (state_dir / tunnel.STATE_FILENAME).write_text(
            json.dumps({"state": "running", "url": "https://old.trycloudflare.com"}),
            encoding="utf-8",
        )
        snapshot = tunnel.saved_snapshot(state_dir)
        self.assertEqual(snapshot.state, "stopped")
        self.assertIn("上一次", snapshot.error)

    def test_missing_state_file_is_a_clean_stopped_snapshot(self):
        snapshot = tunnel.saved_snapshot(self.root / "state")
        self.assertEqual(snapshot.state, "stopped")
        self.assertEqual(snapshot.error, "")

    def test_corrupt_state_file_remains_an_error(self):
        state_dir = self.root / "state"
        state_dir.mkdir()
        (state_dir / tunnel.STATE_FILENAME).write_text("{", encoding="utf-8")
        snapshot = tunnel.saved_snapshot(state_dir)
        self.assertEqual(snapshot.state, "stopped")
        self.assertEqual(snapshot.error, "Tunnel 状态文件不可读")

    def test_pidfile_for_another_process_does_not_mark_tunnel_ready(self):
        manager = tunnel.TunnelManager(
            self.root / "state", self.root / "logs", popen=self.popen, wait_timeout=0.05,
        )
        original = self.popen

        def mismatched_popen(command, **kwargs):
            process = original(command, **kwargs)
            Path(command[command.index("--pidfile") + 1]).write_text("9999\n", encoding="ascii")
            return process

        manager._popen = mismatched_popen
        with self.assertRaisesRegex(tunnel.TunnelError, "连接到 Cloudflare"):
            manager.start(self.plan())
        self.assertTrue(self.processes[0].terminated)
        self.assertFalse((self.root / "state" / tunnel.PID_FILENAME).exists())

    def test_a_child_that_ignores_terminate_is_killed(self):
        manager = tunnel.TunnelManager(
            self.root / "state", self.root / "logs", popen=self.popen, wait_timeout=2,
        )

        def stubborn_popen(command, **kwargs):
            pid_index = command.index("--pidfile") + 1
            Path(command[pid_index]).write_text("4123\n", encoding="ascii")
            process = _StubbornProcess(_Stream(
                "INF | Your quick Tunnel has been created! https://unit-7.trycloudflare.com\n",
            ))
            self.processes.append(process)
            return process

        manager._popen = stubborn_popen
        manager.start(self.plan())
        manager.stop()
        self.assertTrue(self.processes[0].terminated)
        self.assertTrue(self.processes[0].killed)

    def test_snapshot_accepts_a_process_without_a_pid_attribute(self):
        process = SimpleNamespace(poll=lambda: None, stdout=_Stream())
        manager = tunnel.TunnelManager(self.root / "state", self.root / "logs")
        manager._process = process
        snapshot = manager.snapshot()
        self.assertEqual(snapshot.state, "starting")
        self.assertIsNone(snapshot.pid)


class TunnelOriginGuardTests(unittest.TestCase):
    def request(self, origin: str):
        manager = SimpleNamespace(snapshot=lambda: tunnel.TunnelSnapshot(
            state="running", url="https://unit-7.trycloudflare.com",
        ))
        app = SimpleNamespace(state=SimpleNamespace(tunnel=manager))
        return SimpleNamespace(
            app=app,
            headers={"origin": origin},
            base_url="http://127.0.0.1:8900/",
        )

    def test_current_quick_tunnel_origin_is_allowed_for_write_requests(self):
        routes_auth.same_origin(self.request("https://unit-7.trycloudflare.com"))

    def test_regular_local_origin_is_still_allowed(self):
        routes_auth.same_origin(self.request("http://127.0.0.1:8900/"))

    def test_other_origin_is_rejected_even_when_it_looks_like_a_tunnel(self):
        with self.assertRaises(HTTPException) as raised:
            routes_auth.same_origin(self.request("https://other.trycloudflare.com"))
        self.assertEqual(raised.exception.status_code, 403)

    def named_request(self, origin: str, *, hostname: str = "peach.example.com"):
        """命名隧道的主机名来自运行设置，隧道快照此刻还没有地址。"""
        manager = SimpleNamespace(snapshot=lambda: tunnel.TunnelSnapshot(state="starting"))
        settings = SimpleNamespace(tunnel_mode="named", tunnel_hostname=hostname)
        return SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(tunnel=manager, settings=settings)),
            headers={"origin": origin},
            base_url="http://127.0.0.1:8900/",
        )

    def test_the_configured_named_hostname_is_allowed_before_the_handshake_finishes(self):
        routes_auth.same_origin(self.named_request("https://peach.example.com"))

    def test_another_hostname_is_rejected_in_the_named_mode(self):
        with self.assertRaises(HTTPException) as raised:
            routes_auth.same_origin(self.named_request("https://peach.example.net"))
        self.assertEqual(raised.exception.status_code, 403)

    def test_a_named_hostname_is_not_allowed_while_the_mode_is_quick(self):
        manager = SimpleNamespace(snapshot=lambda: tunnel.TunnelSnapshot(state="stopped"))
        settings = SimpleNamespace(tunnel_mode="quick", tunnel_hostname="peach.example.com")
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(tunnel=manager, settings=settings)),
            headers={"origin": "https://peach.example.com"},
            base_url="http://127.0.0.1:8900/",
        )
        with self.assertRaises(HTTPException):
            routes_auth.same_origin(request)


class TunnelRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config_path = self.root / "config.toml"
        self.config_path.write_text("[tunnel]\nenabled = false\n", encoding="utf-8")
        self.config = SimpleNamespace(
            present=True, path=self.config_path,
            tunnel=SimpleNamespace(enabled=False, binary=""),
            data_root=self.root,
        )
        self.settings = SimpleNamespace(
            access_path=self.root / "access.json", token="internal-token",
            tunnel_standalone=True, tunnel_lan_address=None, tunnel_origin_port=443,
        )
        self.plan = tunnel.TunnelPlan(
            binary=self.root / "cloudflared.exe",
            origin=tunnel.TunnelOrigin("http://127.0.0.1:8900"),
            command=("cloudflared", "tunnel", "--url", "http://127.0.0.1:8900"),
        )

    def tearDown(self):
        self.temp.cleanup()

    def request(self, manager):
        state = SimpleNamespace(tunnel=manager, settings=self.settings)
        return SimpleNamespace(
            app=SimpleNamespace(state=state),
            client=SimpleNamespace(host="127.0.0.1"),
            scope={"server": ("127.0.0.1", 8900)},
            url=SimpleNamespace(hostname="127.0.0.1"),
            headers={"origin": "http://127.0.0.1:8900"},
            base_url="http://127.0.0.1:8900/",
        )

    def manager(self, state="stopped"):
        manager = mock.Mock()
        manager.snapshot.return_value = tunnel.TunnelSnapshot(state=state)
        manager.start.return_value = tunnel.TunnelSnapshot(
            state="running", url="https://unit-7.trycloudflare.com", pid=4123,
        )
        manager.stop.return_value = tunnel.TunnelSnapshot(state="stopped")
        return manager

    def patches(self, current=None):
        current = current or self.config
        return mock.patch.multiple(
            routes_configuration.settings_file,
            load_config=mock.Mock(side_effect=[self.config, current]),
            write=mock.Mock(),
        )

    def test_enable_starts_and_persists_the_tunnel(self):
        manager = self.manager()
        with self.patches(), mock.patch.object(routes_configuration, "revision", return_value="rev"), \
                mock.patch.object(routes_configuration.tunnel, "plan_for_config", return_value=self.plan), \
                mock.patch.object(routes_configuration, "FileLock") as lock, \
                mock.patch.object(routes_configuration, "replace", return_value=self.config):
            lock.return_value.__enter__.return_value = lock.return_value
            result = routes_configuration.save_tunnel(
                self.request(manager), {"revision": "rev", "enabled": True}, None,
            )
        self.assertEqual(result["state"], "running")
        manager.start.assert_called_once_with(self.plan)
        manager.stop.assert_not_called()

    def test_the_write_response_carries_the_same_fields_as_the_read(self):
        manager = self.manager()
        with self.patches(), mock.patch.object(routes_configuration, "revision", return_value="rev"), \
                mock.patch.object(routes_configuration.tunnel, "plan_for_config", return_value=self.plan), \
                mock.patch.object(routes_configuration, "FileLock") as lock, \
                mock.patch.object(routes_configuration, "replace", return_value=self.config):
            lock.return_value.__enter__.return_value = lock.return_value
            result = routes_configuration.save_tunnel(
                self.request(manager), {"revision": "rev", "enabled": True}, None,
            )
            read = routes_configuration.tunnel_payload(
                self.config, tunnel.TunnelSnapshot(state="running"), True,
            )
        self.assertEqual(set(result) - {"revision"}, set(read))
        self.assertIn("available", result)
        self.assertTrue(result["enabled"])

    def test_stale_revision_rolls_back_only_a_tunnel_started_by_this_request(self):
        manager = self.manager()
        with self.patches(current=SimpleNamespace(path=self.config_path)), \
                mock.patch.object(routes_configuration, "revision", side_effect=["rev", "changed", "rev"]), \
                mock.patch.object(routes_configuration.tunnel, "plan_for_config", return_value=self.plan), \
                mock.patch.object(routes_configuration, "FileLock") as lock, \
                mock.patch.object(routes_configuration, "replace", return_value=self.config):
            lock.return_value.__enter__.return_value = lock.return_value
            with self.assertRaises(HTTPException) as raised:
                routes_configuration.save_tunnel(
                    self.request(manager), {"revision": "rev", "enabled": True}, None,
                )
        self.assertEqual(raised.exception.status_code, 409)
        manager.stop.assert_called_once_with()

    def test_stale_revision_does_not_stop_an_existing_tunnel(self):
        manager = self.manager(state="running")
        with self.patches(current=SimpleNamespace(path=self.config_path)), \
                mock.patch.object(routes_configuration, "revision", side_effect=["rev", "changed", "rev"]), \
                mock.patch.object(routes_configuration.tunnel, "plan_for_config", return_value=self.plan), \
                mock.patch.object(routes_configuration, "FileLock") as lock, \
                mock.patch.object(routes_configuration, "replace", return_value=self.config):
            lock.return_value.__enter__.return_value = lock.return_value
            with self.assertRaises(HTTPException):
                routes_configuration.save_tunnel(
                    self.request(manager), {"revision": "rev", "enabled": True}, None,
                )
        manager.stop.assert_not_called()


class NamedTunnelRouteTests(unittest.TestCase):
    """写回接口同时收下形态与开关；令牌只进设置文件，不回给页面。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config_path = self.root / "config.toml"
        self.config_path.write_text("[tunnel]\nenabled = false\n", encoding="utf-8")
        self.config = settings_file.PeachConfig(
            data_root=self.root, path=self.config_path, present=True,
        )
        self.settings = SimpleNamespace(
            access_path=self.root / "access.json", token="internal-token",
            tunnel_standalone=False, tunnel_lan_address="192.0.2.162",
            tunnel_origin_port=443, tls_enabled=True,
        )
        self.plan = tunnel.TunnelPlan(
            binary=self.root / "cloudflared.exe",
            origin=tunnel.TunnelOrigin("https://peach.example.com"),
            command=("cloudflared", "tunnel", "run", "--token", FAKE_TOKEN),
            mode="named", url="https://peach.example.com", secret=FAKE_TOKEN,
        )

    def tearDown(self):
        self.temp.cleanup()

    def request(self, manager):
        state = SimpleNamespace(tunnel=manager, settings=self.settings)
        return SimpleNamespace(
            app=SimpleNamespace(state=state),
            client=SimpleNamespace(host="127.0.0.1"),
            scope={"server": ("127.0.0.1", 8900)},
            url=SimpleNamespace(hostname="127.0.0.1"),
            headers={"origin": "http://127.0.0.1:8900"},
            base_url="http://127.0.0.1:8900/",
        )

    def manager(self):
        manager = mock.Mock()
        manager.snapshot.return_value = tunnel.TunnelSnapshot(state="stopped")
        manager.start.return_value = tunnel.TunnelSnapshot(
            state="running", url="https://peach.example.com", pid=4123,
        )
        manager.stop.return_value = tunnel.TunnelSnapshot(state="stopped")
        return manager

    def save(self, body, *, plan_for_config=None):
        written = mock.Mock()
        manager = self.manager()
        planner = plan_for_config or mock.Mock(return_value=self.plan)
        with mock.patch.multiple(
            routes_configuration.settings_file,
            load_config=mock.Mock(return_value=self.config), write=written,
        ), mock.patch.object(routes_configuration, "revision", return_value="rev"), \
                mock.patch.object(routes_configuration.tunnel, "plan_for_config", planner), \
                mock.patch.object(routes_configuration, "FileLock") as lock:
            lock.return_value.__enter__.return_value = lock.return_value
            result = routes_configuration.save_tunnel(
                self.request(manager), {"revision": "rev", **body}, None,
            )
        return result, written, manager, planner

    def test_the_named_form_is_persisted_and_the_plan_uses_this_submission(self):
        result, written, _manager, planner = self.save({
            "enabled": True, "mode": "named",
            "hostname": "https://Peach.Example.com/", "token": FAKE_TOKEN,
        })
        saved = written.call_args.args[0]
        self.assertEqual(saved.tunnel.mode, "named")
        self.assertEqual(saved.tunnel.hostname, "peach.example.com")
        self.assertEqual(saved.tunnel.token, FAKE_TOKEN)
        self.assertEqual(planner.call_args.args[0].tunnel.hostname, "peach.example.com")
        self.assertEqual(result["mode"], "named")
        self.assertEqual(result["hostname"], "peach.example.com")
        self.assertTrue(result["token_set"])
        self.assertTrue(result["named_available"])

    def test_the_response_never_carries_the_token_itself(self):
        result, _written, _manager, _planner = self.save({
            "enabled": True, "mode": "named",
            "hostname": "peach.example.com", "token": FAKE_TOKEN,
        })
        self.assertNotIn(FAKE_TOKEN, json.dumps(result, ensure_ascii=False))
        self.assertNotIn("token", set(result) - {"token_set"})

    def test_an_empty_token_keeps_the_saved_one(self):
        self.config = settings_file.PeachConfig(
            data_root=self.root, path=self.config_path, present=True,
            tunnel=settings_file.TunnelSettings(
                mode="named", token=FAKE_TOKEN, hostname="peach.example.com"),
        )
        _result, written, _manager, _planner = self.save({
            "enabled": False, "mode": "named", "hostname": "peach.example.com", "token": "",
        })
        self.assertEqual(written.call_args.args[0].tunnel.token, FAKE_TOKEN)

    def test_an_invalid_hostname_comes_back_as_a_field_error(self):
        with self.assertRaises(HTTPException) as raised:
            self.save({"enabled": False, "mode": "named", "hostname": "peach.example.com/admin"})
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("hostname", raised.exception.detail["errors"])

    def test_a_standalone_package_refuses_to_store_the_named_mode(self):
        with mock.patch.object(routes_configuration.distribution, "standalone", return_value=True):
            with self.assertRaises(HTTPException) as raised:
                routes_configuration.tunnel_changes(self.config, {"mode": "named"})
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, tunnel.STANDALONE_NAMED_ERROR)

    def test_a_standalone_package_reports_that_the_named_mode_is_unavailable(self):
        with mock.patch.object(routes_configuration.distribution, "standalone", return_value=True):
            payload = routes_configuration.tunnel_payload(
                self.config, tunnel.TunnelSnapshot(), False)
        self.assertFalse(payload["named_available"])


def direct_interpreter() -> str:
    """找一个自己就是最终进程的解释器。

    venv 的 `python.exe` 在 Windows 上是启动器，真解释器是它的子进程，pid 与
    `Popen.pid` 对不上；假 cloudflared 写出来的 pidfile 于是永远匹配不了。
    """
    for candidate in (getattr(sys, "_base_executable", "") or "", sys.executable):
        if not candidate:
            continue
        try:
            child = subprocess.Popen(
                [candidate, "-c", "import os; print(os.getpid())"],
                stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            )
            printed, _ = child.communicate(timeout=60)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if printed.strip() == str(child.pid):
            return candidate
    return ""


class TunnelChildProcessTests(unittest.TestCase):
    """用真实子进程覆盖行缓冲读取、收尾与遗留进程回收。"""

    @classmethod
    def setUpClass(cls):
        cls.python = direct_interpreter()

    def setUp(self):
        if not self.python:
            self.skipTest("本机解释器都带启动器跳板，pid 与子进程对不上")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.script = self.root / "fake_cloudflared.py"
        self.script.write_text(FAKE_CLOUDFLARED, encoding="utf-8")
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=30)
        self.temp.cleanup()

    def spawn(self, command, **kwargs):
        child = subprocess.Popen(
            [self.python, "-X", "utf8", str(self.script), *command[1:]], **kwargs,
        )
        self.children.append(child)
        return child

    def background_child(self, pid_file: Path):
        child = subprocess.Popen(
            [self.python, "-X", "utf8", str(self.script), "--pidfile", str(pid_file)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.children.append(child)
        return child

    def plan(self):
        return tunnel.TunnelPlan(
            binary=self.root / "cloudflared.exe",
            origin=tunnel.TunnelOrigin("http://127.0.0.1:8900"),
            command=("cloudflared", "tunnel", "--url", "http://127.0.0.1:8900"),
        )

    def test_a_real_child_publishes_its_url_and_leaves_nothing_behind(self):
        manager = tunnel.TunnelManager(
            self.root / "state", self.root / "logs", popen=self.spawn, wait_timeout=60,
        )
        started = manager.start(self.plan())
        self.assertEqual(started.state, "running")
        self.assertEqual(started.url, "https://real-child.trycloudflare.com")
        child = self.children[0]
        self.assertIsNone(child.poll())
        manager.stop()
        child.wait(timeout=30)
        self.assertIsNotNone(child.returncode)
        self.assertFalse((self.root / "state" / tunnel.PID_FILENAME).exists())
        self.assertFalse((self.root / "state" / tunnel.STATE_FILENAME).exists())

    def test_a_live_cloudflared_from_the_last_run_is_reclaimed(self):
        state_dir = self.root / "state"
        state_dir.mkdir()
        child = self.background_child(state_dir / "child.pid")
        (state_dir / tunnel.PID_FILENAME).write_text(str(child.pid), encoding="ascii")
        manager = tunnel.TunnelManager(
            state_dir, self.root / "logs", image_name=lambda _pid: "cloudflared.exe",
        )
        self.assertEqual(manager.reclaim_orphan(), child.pid)
        child.wait(timeout=30)
        self.assertIsNotNone(child.returncode)

    def test_a_pid_belonging_to_another_program_is_left_alone(self):
        state_dir = self.root / "state"
        state_dir.mkdir()
        child = self.background_child(state_dir / "child.pid")
        (state_dir / tunnel.PID_FILENAME).write_text(str(child.pid), encoding="ascii")
        manager = tunnel.TunnelManager(
            state_dir, self.root / "logs", image_name=lambda _pid: "postgres.exe",
        )
        self.assertIsNone(manager.reclaim_orphan())
        self.assertIsNone(child.poll())

    def test_the_image_name_of_a_live_process_is_readable(self):
        self.assertIn("python", tunnel.process_image_name(os.getpid()).lower())


class TunnelStartupTests(unittest.TestCase):
    """服务自己不会因为设置文件里写着 enabled 就打开公网入口。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_default_settings_keep_the_tunnel_closed(self):
        settings = PeachSettings()
        self.assertFalse(settings.tunnel_enabled)
        self.assertEqual(settings.tunnel_binary, "")

    def test_start_tunnel_does_nothing_when_disabled(self):
        manager = mock.Mock()
        api._start_tunnel(PeachSettings(configured=True), manager)
        manager.start.assert_not_called()

    def test_create_app_does_not_start_a_tunnel_by_default(self):
        manager = mock.Mock()
        manager.snapshot.return_value = tunnel.TunnelSnapshot()
        settings = PeachSettings(
            db_path=self.root / "peach.db", configured=True, token="",
            tunnel_state_root=self.root / "state", tunnel_log_root=self.root / "logs",
        )
        with self.assertNoLogs("peach", level="ERROR"), \
                TestClient(api.create_app(settings, tunnel_manager=manager)) as client:
            client.get("/healthz")
        manager.start.assert_not_called()


class LocalClientTests(unittest.TestCase):
    """隧道转发进来的请求不算本机，配置页对它一律关门。"""

    def request(self, state: str, headers: dict[str, str]):
        manager = SimpleNamespace(snapshot=lambda: tunnel.TunnelSnapshot(
            state=state, url="https://unit-7.trycloudflare.com",
        ))
        return SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(
                tunnel=manager, settings=SimpleNamespace(mdns_name="peach", configured=True),
            )),
            client=SimpleNamespace(host="127.0.0.1"),
            scope={"server": ("127.0.0.1", 8900)},
            url=SimpleNamespace(hostname="127.0.0.1"),
            headers=headers,
        )

    def test_a_loopback_request_without_edge_headers_is_local(self):
        self.assertTrue(routes_configuration.local_client(self.request("running", {})))

    def test_cf_ray_during_a_running_tunnel_is_not_local(self):
        request = self.request("running", {"cf-ray": "7a0-LAX"})
        self.assertFalse(routes_configuration.local_client(request))

    def test_cf_connecting_ip_during_a_running_tunnel_is_not_local(self):
        request = self.request("running", {"cf-connecting-ip": "198.51.100.7"})
        self.assertFalse(routes_configuration.local_client(request))

    def test_edge_headers_alone_do_not_lock_out_a_stopped_tunnel(self):
        request = self.request("stopped", {"cf-connecting-ip": "198.51.100.7"})
        self.assertTrue(routes_configuration.local_client(request))

    def test_the_debug_serve_branch_also_refuses_forwarded_requests(self):
        request = self.request("running", {"cf-ray": "7a0-LAX"})
        with mock.patch.dict("os.environ", {"PEACH_DEV": "1"}):
            self.assertFalse(routes_configuration.configurable(request))

    def test_the_login_rate_limit_separates_edge_clients(self):
        request = self.request("running", {"cf-connecting-ip": "198.51.100.7"})
        self.assertEqual(routes_auth._attempt_key(request), "cf:198.51.100.7")

    def test_the_login_rate_limit_uses_the_peer_without_a_tunnel(self):
        request = self.request("stopped", {"cf-connecting-ip": "198.51.100.7"})
        self.assertEqual(routes_auth._attempt_key(request), "127.0.0.1")


class AccessTunnelInterlockTests(unittest.TestCase):
    """关掉访问密码就没有第二道闸门，公网入口必须当场收掉。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.access_path = self.root / "secrets" / "access.json"
        access.save(self.access_path, "a-strong-test-password")
        self.config_path = self.root / "config.toml"
        self.config_path.write_text("[tunnel]\nenabled = true\n", encoding="utf-8")
        self.config = SimpleNamespace(
            present=True, path=self.config_path, data_root=self.root,
            tunnel=SimpleNamespace(enabled=True, binary=""),
        )

    def tearDown(self):
        self.temp.cleanup()

    def request(self, manager):
        settings = SimpleNamespace(
            access_path=self.access_path, configured=True, mdns_name="peach",
            token="internal-token",
        )
        return SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(tunnel=manager, settings=settings)),
            client=SimpleNamespace(host="127.0.0.1"),
            scope={"server": ("127.0.0.1", 8900)},
            url=SimpleNamespace(hostname="127.0.0.1"),
            headers={"origin": "http://127.0.0.1:8900"},
            base_url="http://127.0.0.1:8900/",
        )

    def manager(self, state: str):
        manager = mock.Mock()
        manager.snapshot.return_value = tunnel.TunnelSnapshot(
            state=state, url="https://unit-7.trycloudflare.com",
        )
        manager.stop.return_value = tunnel.TunnelSnapshot(state="stopped")
        return manager

    def disable_body(self):
        policy = access.load(self.access_path)
        return {
            "action": "disable", "confirm_disable": True, "revision": policy["revision"],
            "current_password": "a-strong-test-password", "password": "", "confirmation": "",
        }

    def save(self, manager):
        def fake_replace(target, **changes):
            return SimpleNamespace(**{**vars(target), **changes})

        with mock.patch.object(
            routes_configuration.settings_file, "load_config", return_value=self.config,
        ), mock.patch.object(
            routes_configuration.settings_file, "write",
        ) as write, mock.patch.object(
            routes_configuration, "replace", side_effect=fake_replace,
        ), mock.patch.object(routes_configuration, "FileLock") as lock:
            lock.return_value.__enter__.return_value = lock.return_value
            response = routes_configuration.save_access(
                self.request(manager), self.disable_body(), None,
            )
        return response, write

    def test_disabling_the_password_stops_the_tunnel_and_writes_the_switch_back(self):
        manager = self.manager("running")
        response, write = self.save(manager)
        manager.stop.assert_called_once_with()
        self.assertFalse(write.call_args.args[0].tunnel.enabled)
        self.assertIn("临时远程链接", json.loads(response.body)["tunnel_notice"])

    def test_disabling_the_password_without_a_tunnel_changes_nothing(self):
        manager = self.manager("stopped")
        response, write = self.save(manager)
        manager.stop.assert_not_called()
        write.assert_not_called()
        self.assertNotIn("tunnel_notice", json.loads(response.body))


if __name__ == "__main__":
    unittest.main()
