"""口令的生成、存放与取用，以及绑局域网时的启动闸门。

闸门本身（`require_auth` 三兄弟怎么判、401 有几种形态）在 `test_fastapi_api.py`；
这里只管「这台机器有没有口令、`peach serve` 该不该起得来」。
"""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from peach import auth, cli, settings_file


class TokenFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.secrets = Path(self.tmp.name).resolve() / "secrets"

    def test_a_missing_file_reads_as_no_token(self):
        self.assertEqual(auth.read_token(self.secrets), "")

    def test_a_blank_file_reads_as_no_token(self):
        self.secrets.mkdir(parents=True)
        auth.token_path(self.secrets).write_text("  \n", encoding="utf-8")
        self.assertEqual(auth.read_token(self.secrets), "")

    def test_a_new_token_carries_at_least_256_bit(self):
        token = auth.write_token(self.secrets)
        # token_urlsafe(32) 出 43 个字符；断言字符数而不是熵，是因为长度是这里唯一
        # 能直接观察的量，而它由 TOKEN_BYTES 唯一决定。
        self.assertGreaterEqual(len(token), 43)
        self.assertTrue(all(c.isalnum() or c in "-_" for c in token), token)
        self.assertEqual(auth.read_token(self.secrets), token)

    def test_ensure_keeps_the_token_already_on_disk(self):
        first, created = auth.ensure_token(self.secrets)
        self.assertTrue(created)
        second, created_again = auth.ensure_token(self.secrets)
        self.assertEqual(second, first)
        self.assertFalse(created_again)

    def test_writing_again_replaces_the_token(self):
        first = auth.write_token(self.secrets)
        self.assertNotEqual(auth.write_token(self.secrets), first)

    @unittest.skipIf(os.name == "nt", "Windows 的 chmod 只动只读位，权限靠 Peach 数据目录本身")
    def test_the_token_file_is_owner_only(self):
        auth.write_token(self.secrets)
        self.assertEqual(auth.token_path(self.secrets).stat().st_mode & 0o777, 0o600)

    def test_resolution_order_is_flag_then_environment_then_file(self):
        auth.write_token(self.secrets)
        on_disk = auth.read_token(self.secrets)
        with mock.patch.dict(os.environ, {"PEACH_TOKEN": "from-env"}):
            self.assertEqual(auth.resolve_token("from-flag", self.secrets), "from-flag")
            self.assertEqual(auth.resolve_token("", self.secrets), "from-env")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(auth.resolve_token("", self.secrets), on_disk)


class ServeGateTests(unittest.TestCase):
    """绑局域网地址却没有口令时，`peach serve` 必须拒绝启动而不是告警。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "peach-data"
        self.config = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
        patcher = mock.patch.object(cli.settings_file, "active", return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def _args(self, *extra):
        return cli.build_parser().parse_args(["serve", *extra])

    def _secrets(self) -> Path:
        return self.config.directory("secrets")

    def test_a_lan_bind_without_a_token_refuses_to_start(self):
        with self.assertRaises(SystemExit) as raised:
            cli._serve_token(self._args("--host", "0.0.0.0"))
        message = str(raised.exception)
        self.assertIn("0.0.0.0", message)
        self.assertIn("peach token", message)
        # 拒绝的时候不许顺手把口令生成掉：那等于把硬门槛降成一句提示。
        self.assertFalse(auth.token_path(self._secrets()).exists())

    def test_a_loopback_bind_runs_without_a_token(self):
        self.assertEqual(cli._serve_token(self._args("--host", "127.0.0.1")), "")
        self.assertEqual(cli._serve_token(self._args("--host", "localhost")), "")

    def test_a_lan_bind_takes_the_token_from_the_file(self):
        token = auth.write_token(self._secrets())
        self.assertEqual(cli._serve_token(self._args("--host", "0.0.0.0")), token)

    def test_an_explicit_flag_still_wins(self):
        auth.write_token(self._secrets())
        self.assertEqual(
            cli._serve_token(self._args("--host", "0.0.0.0", "--token", "given")), "given")

    def test_no_auth_skips_the_login_page_on_loopback_and_leaves_the_token_file_alone(self):
        token = auth.write_token(self._secrets())
        self.assertEqual(cli._serve_token(self._args("--host", "127.0.0.1", "--no-auth")), "")
        # 口令文件是托盘那份服务的门槛，开发时跳过登录不该把它擦掉或改写。
        self.assertEqual(auth.read_token(self._secrets()), token)

    def test_no_auth_refuses_a_lan_bind_even_when_a_token_exists(self):
        auth.write_token(self._secrets())
        with self.assertRaises(SystemExit) as raised:
            cli._serve_token(self._args("--host", "0.0.0.0", "--no-auth"))
        self.assertIn("0.0.0.0", str(raised.exception))


class TokenCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "peach-data"
        self.config = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
        patcher = mock.patch.object(cli.settings_file, "active", return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, *extra):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["token", *extra])
        return code, output.getvalue()

    def test_it_creates_and_prints_a_token(self):
        code, output = self._run()
        self.assertEqual(code, 0)
        token = auth.read_token(self.config.directory("secrets"))
        self.assertTrue(token)
        self.assertIn(token, output)

    def test_reading_twice_gives_the_same_token(self):
        self._run()
        token = auth.read_token(self.config.directory("secrets"))
        _code, again = self._run()
        self.assertIn(token, again)
        self.assertEqual(auth.read_token(self.config.directory("secrets")), token)

    def test_rotate_replaces_the_token(self):
        self._run()
        before = auth.read_token(self.config.directory("secrets"))
        _code, output = self._run("--rotate")
        after = auth.read_token(self.config.directory("secrets"))
        self.assertNotEqual(after, before)
        self.assertIn(after, output)


class InitProvisionsTokenTests(unittest.TestCase):
    """两条 init 路径都要留下口令文件——现有部署走的正是 `--from-existing`。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "peach-data"
        patcher = mock.patch.object(cli.onboarding.certs, "bootstrap_certificates")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _init(self, *extra):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["init", "--data-root", str(self.root), *extra])
        return code, output.getvalue()

    def test_a_fresh_init_leaves_a_token_behind(self):
        code, output = self._init()
        self.assertEqual(code, 0)
        token = auth.read_token(self.root / "secrets")
        self.assertTrue(token)
        # 口令本身不进终端输出：那条路通向日志、截图和 issue。
        self.assertNotIn(token, output)
        self.assertIn("peach token", output)

    def test_from_existing_leaves_a_token_behind(self):
        code, _output = self._init("--from-existing")
        self.assertEqual(code, 0)
        self.assertTrue(auth.read_token(self.root / "secrets"))


if __name__ == "__main__":
    unittest.main()
