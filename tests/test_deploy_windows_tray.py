from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from peach import __version__


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy_windows_tray.py"
SPEC = importlib.util.spec_from_file_location("deploy_windows_tray", SCRIPT)
deploy_windows_tray = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = deploy_windows_tray
SPEC.loader.exec_module(deploy_windows_tray)


def git_runner(porcelain: str = "", commit: str = "abc12345def", branch: str = "master"):
    answers = {
        "status --porcelain": porcelain,
        "rev-parse HEAD": commit,
        "rev-parse --abbrev-ref HEAD": branch,
    }

    def run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout=answers[" ".join(command[1:])], stderr="",
        )

    return run


class DeployWindowsTrayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # 部署入口先 resolve 检出根和目标，断言也得拿 resolve 过的路径比。
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / "dist" / "Peach" / "Peach.exe"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"old")
        self.staged = self.root / "staging" / "Peach.exe"
        self.staged.parent.mkdir()
        self.staged.write_bytes(b"new")
        self.installer = mock.Mock()
        self.installer.log_path = self.root / "sync.log"
        self.installer.build_staged_tray.return_value = self.staged
        self.installer.packaged_migrations_pass.return_value = True
        self.restarted = mock.Mock(return_value=mock.Mock(
            ok=True, message="托盘已正常重启并重新拥有 HTTP/HTTPS 子服务",
            old_tray_pid=10, new_tray_pid=30, service_pids=(51, 52),
            backup=str(self.target.with_name("Peach.pre-source-sync-20260906-120000.exe")),
        ))

    def tearDown(self):
        self.temp.cleanup()

    def run_deploy(self, **overrides):
        arguments = dict(
            target=self.target, installer=self.installer, restart=self.restarted,
            run=git_runner(),
            identity=lambda **_kwargs: {"version": __version__, "build_commit": "abc12345def"},
        )
        arguments.update(overrides)
        return deploy_windows_tray.deploy(self.root, **arguments)

    def test_a_dirty_checkout_stops_before_anything_is_built_or_swapped(self):
        outcome = self.run_deploy(run=git_runner(porcelain=" M src/peach/tray.py\n"))
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["step"], "checkout")
        self.installer.build_staged_tray.assert_not_called()
        self.restarted.assert_not_called()

    def test_a_package_that_fails_the_migration_check_never_reaches_production(self):
        self.installer.packaged_migrations_pass.return_value = False
        outcome = self.run_deploy()
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["step"], "validate")
        self.restarted.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"old")

    def test_a_given_package_is_reused_instead_of_building_again(self):
        outcome = self.run_deploy(staged=self.staged)
        self.assertTrue(outcome["ok"], outcome["message"])
        self.installer.build_staged_tray.assert_not_called()
        self.assertEqual(self.restarted.call_args.kwargs["swap_from"], self.staged)
        self.assertEqual(outcome["commit"], "abc12345def")
        self.assertEqual(outcome["version"], __version__)

    def test_a_tray_built_from_another_commit_is_reported_as_a_failure(self):
        """认的是构建提交：同一个版本号下有很多个构建，只比版本号证明不了二进制换了。"""
        outcome = self.run_deploy(
            identity=lambda **_kwargs: {"version": __version__, "build_commit": "0ldc0mm1t"})
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["step"], "verify")
        self.assertEqual(outcome["served_commit"], "0ldc0mm1t")
        self.assertEqual(outcome["expected_version"], __version__)
        self.assertEqual(outcome["backup"], self.restarted.return_value.backup)

    def swapping_restart(self):
        """照真实那步的样子换文件：`os.replace` 是搬走，暂存包换完就不在了。

        指纹只能在换之前取，这个替身把「换完再去读暂存包」直接变成 FileNotFoundError。
        """
        def restart(target, *, swap_from, **_kwargs):
            os.replace(swap_from, target)
            return self.restarted.return_value
        return restart

    def test_a_venv_owned_deployment_is_verified_by_the_binary_that_landed(self):
        """回话的是源码进程时，认落地那个文件的指纹。

        托盘按 `_peach_executable()` 的设计把两个子服务交回项目 venv，那个进程没有第二个
        版本，`/healthz` 的 `build_commit` 结构上恒为 None。拿它当判据的话，二进制换得
        再对也必然判失败——这条链路上唯一动了生产入口的那一步，反而永远报不出成功。
        """
        outcome = self.run_deploy(
            staged=self.staged, restart=self.swapping_restart(),
            identity=lambda **_kwargs: {"version": __version__, "build_commit": None})
        self.assertTrue(outcome["ok"], outcome["message"])
        self.assertEqual(outcome["step"], "verify")
        self.assertEqual(outcome["built_digest"], outcome["target_digest"])
        self.assertEqual(self.target.read_bytes(), b"new")

    def test_a_binary_that_never_landed_fails_even_though_the_tray_answers(self):
        """托盘回话不等于跑的是这一份：指纹对不上就是没换上。"""
        outcome = self.run_deploy(
            staged=self.staged,
            identity=lambda **_kwargs: {"version": __version__, "build_commit": None})
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["step"], "verify")
        self.assertNotEqual(outcome["built_digest"], outcome["target_digest"])
        self.assertIn("指纹", outcome["message"])
        self.assertEqual(outcome["backup"], self.restarted.return_value.backup)

    def test_a_silent_health_endpoint_is_reported_as_a_failure(self):
        outcome = self.run_deploy(identity=lambda **_kwargs: None)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["step"], "verify")
        self.assertIn("/healthz", outcome["message"])


class ServingIdentityTests(unittest.TestCase):
    def test_the_verdict_comes_from_the_https_port_checked_against_the_project_ca(self):
        specs = (
            mock.Mock(name="http", health_url="http://127.0.0.1/healthz", verify=True),
            mock.Mock(health_url="https://192.0.2.10/healthz", verify="/ca/peach.crt"),
        )
        specs[0].name = "http"
        specs[1].name = "https"
        response = mock.Mock(status_code=200)
        response.json.return_value = {"ok": True, "version": "9.9.9", "build_commit": "c0ffee"}
        with (
            mock.patch.object(deploy_windows_tray, "build_service_specs",
                              return_value=specs),
            mock.patch.object(deploy_windows_tray.httpx, "get",
                              return_value=response) as get,
        ):
            self.assertEqual(deploy_windows_tray.serving_identity(),
                             {"version": "9.9.9", "build_commit": "c0ffee"})
        self.assertEqual(get.call_args.args[0], "https://192.0.2.10/healthz")
        self.assertEqual(get.call_args.kwargs["verify"], "/ca/peach.crt")
        self.assertFalse(get.call_args.kwargs["trust_env"],
                         "系统代理会替生产口回话，探测必须直连")


if __name__ == "__main__":
    unittest.main()
