"""可选密码、会话期限与本机管理边界。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Depends

from peach import access, routes_auth, routes_configuration


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve() / "access.json"
        self.app = FastAPI()
        self.app.state.settings = SimpleNamespace(token="internal-test-token", access_path=self.path, configured=True, mdns_name="peach")
        self.app.include_router(routes_auth.router)
        self.app.include_router(routes_configuration.router)
        @self.app.get("/private")
        def private(_=Depends(routes_auth.require_auth)):
            return {"ok": True}
        @self.app.post("/private")
        def write_private(_=Depends(routes_auth.require_auth)):
            return {"ok": True}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 1234)), base_url="http://localhost")
        self.addAsyncCleanup(self.client.aclose)
        routes_auth._ATTEMPTS.clear()

    async def login(self, password="correct-password", days=7):
        return await self.client.post("/login", data={"token": password, "days": str(days)})

    async def test_open_access_accepts_first_visit_without_exposing_internal_token(self):
        access.save(self.path, "")
        self.assertEqual((await self.client.get("/private")).status_code, 200)
        page = await self.client.get("/login")
        self.assertEqual(page.status_code, 303)
        self.assertNotIn("internal-test-token", page.text)
        self.assertFalse(self.client.cookies)
        self.assertEqual((await self.client.post("/private", headers={"Origin": "https://evil.example"})).status_code, 403)

    async def test_password_sessions_expire_on_server_and_survive_independent_app_reads(self):
        access.save(self.path, "correct-password")
        self.assertEqual((await self.client.get("/private")).status_code, 401)
        self.assertEqual((await self.login("incorrect")).status_code, 401)
        response = await self.login()
        self.assertEqual(response.status_code, 303)
        self.assertIn("Max-Age=604800", response.headers["set-cookie"])
        self.assertNotIn("internal-test-token", response.headers["set-cookie"])
        self.assertEqual((await self.client.get("/private")).status_code, 200)
        with patch.object(access.time, "time", return_value=access.time.time() + 604801):
            self.assertEqual((await self.client.get("/private")).status_code, 401)

    async def test_browser_session_has_no_persistent_cookie_and_twelve_hour_limit(self):
        policy = access.save(self.path, "correct-password")
        response = await self.login(days=0)
        session_header = next(header for header in response.headers.get_list("set-cookie") if header.startswith(access.COOKIE))
        self.assertNotIn("Max-Age", session_header)
        self.assertIn("HttpOnly", session_header)
        session = self.client.cookies.get(access.COOKIE)
        with patch.object(access.time, "time", return_value=access.time.time() + 43201):
            self.assertFalse(access.valid_session(policy, session))

    async def test_custom_duration_and_unchecked_remember_control(self):
        access.save(self.path, "correct-password")
        response = await self.login(days=17)
        self.assertIn("Max-Age=1468800", response.headers["set-cookie"])
        self.client.cookies.clear()
        response = await self.client.post("/login", data={"token": "correct-password"})
        session_header = next(header for header in response.headers.get_list("set-cookie") if header.startswith(access.COOKIE))
        self.assertNotIn("Max-Age", session_header)
        self.assertEqual((await self.login(days=366)).status_code, 400)

    async def test_the_login_page_is_a_shell_around_the_page_bundle(self):
        """登录页由页面包画：壳里只有 `next`、口令错误的标记与页面包，表单字段由页面包按原名提交。

        `next` 经净化与 HTML 转义写进挂载点；主题预读排在任何样式之前，手动选的深浅色才能在首绘前定下来。
        控件的外观、`aria-invalid`、`role="alert"` 与焦点归 vitest 与截图验收。
        """
        access.save(self.path, "correct-password")
        page = await self.client.get("/login", params={"next": '/stats?x=1&y="<b>'})
        self.assertEqual(page.status_code, 200)
        self.assertIn('<div id="peach-page" data-page="login" data-next="/stats?x=1&amp;y=&quot;&lt;b&gt;">',
                      page.text)
        self.assertIn('<link rel="stylesheet" href="/dist/peach-pages.css">', page.text)
        self.assertIn('<script type="module" src="/dist/peach-pages.js"></script>', page.text)
        self.assertNotIn("<style", page.text)
        self.assertNotIn("<form", page.text)
        self.assertLess(page.text.index("dataset.theme=c"), page.text.index('<link rel="stylesheet"'),
                        "主题预读要排在任何样式之前")
        for unsafe in ("//evil.example/", "https://evil.example/"):
            with self.subTest(unsafe):
                page = await self.client.get("/login", params={"next": unsafe})
                self.assertIn('data-page="login" data-next="/">', page.text)
        wrong = await self.client.post("/login", data={"token": "incorrect", "next": "/stats", "days": "30"})
        self.assertEqual(wrong.status_code, 401)
        self.assertIn('data-page="login" data-next="/stats" data-invalid="true">', wrong.text)
        self.assertFalse(self.client.cookies)
        accepted = await self.client.post("/login", data={"token": "correct-password", "next": "/stats", "days": "30"})
        self.assertEqual(accepted.status_code, 303)
        self.assertEqual(accepted.headers["location"], "/stats")

    async def test_password_change_revokes_sessions_and_disable_is_explicit(self):
        policy = access.save(self.path, "correct-password")
        await self.login()
        old_cookie = self.client.cookies.get(access.COOKIE)
        body = {"revision": policy["revision"], "action": "set", "password": "new-password", "confirmation": "new-password", "current_password": "wrong"}
        self.assertEqual((await self.client.post("/api/configuration/access", json=body)).status_code, 400)
        body["current_password"] = "correct-password"
        response = await self.client.post("/api/configuration/access", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(access.valid_session(access.load(self.path), old_cookie))
        self.assertEqual((await self.client.get("/private")).status_code, 200)
        body = {"revision": response.json()["revision"], "action": "disable", "current_password": "new-password"}
        self.assertEqual((await self.client.post("/api/configuration/access", json=body)).status_code, 400)
        body["confirm_disable"] = True
        self.assertEqual((await self.client.post("/api/configuration/access", json=body)).status_code, 200)
        self.client.cookies.clear()
        self.assertEqual((await self.client.get("/private")).status_code, 200)

    async def test_management_rejects_foreign_origin_remote_peer_and_stale_revision(self):
        policy = access.save(self.path, "")
        body = {"revision": policy["revision"], "action": "set", "password": "correct-password", "confirmation": "correct-password"}
        self.assertEqual((await self.client.post("/api/configuration/access", json=body, headers={"Origin": "https://evil.example"})).status_code, 403)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("192.0.2.2", 1)), base_url="http://localhost") as remote:
            self.assertEqual((await remote.post("/api/configuration/access", json=body)).status_code, 403)
        body["revision"] = "stale"
        self.assertEqual((await self.client.post("/api/configuration/access", json=body)).status_code, 409)

    async def test_existing_token_cookie_can_configure_without_copying_token_again(self):
        self.assertEqual((await self.client.get("/private")).status_code, 401)
        self.client.cookies.set("tok", "internal-test-token")
        response = await self.client.post("/api/configuration/access", json={"revision": "legacy", "action": "set", "password": "correct-password", "confirmation": "correct-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.client.cookies.clear()
        self.client.cookies.set("tok", "internal-test-token")
        self.assertEqual((await self.client.get("/private")).status_code, 401)

    async def test_corrupt_policy_fails_closed_and_password_is_not_stored_or_returned(self):
        policy = access.save(self.path, "correct-password")
        self.assertNotIn("correct-password", self.path.read_text())
        self.assertEqual(set(access.public(policy)), {"mode", "revision"})
        self.path.write_text("invalid", encoding="utf-8")
        self.assertEqual((await self.client.get("/private")).status_code, 401)

    async def test_login_duration_tampering_and_repeated_guesses_are_rejected(self):
        access.save(self.path, "correct-password")
        self.assertEqual((await self.login(days=9999)).status_code, 400)
        for _ in range(9):
            await self.login("wrong")
        self.assertEqual((await self.login("wrong")).status_code, 429)

    def test_first_setup_initializes_optional_password_but_keeps_existing_policy(self):
        from peach import onboarding, settings_file
        config = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.path.parent)})
        path = config.directory("secrets") / "access.json"
        tree = SimpleNamespace(token_created=True)
        with patch.object(onboarding, "configure", return_value=config), patch.object(onboarding, "create_data_tree", return_value=tree), patch.object(settings_file, "write", return_value=config.path):
            onboarding.apply(config, None, windows=True, access_password="correct-password")
            self.assertTrue(access.verify(access.load(path), "correct-password"))
            onboarding.apply(config, None, windows=True)
            self.assertTrue(access.verify(access.load(path), "correct-password"))
            path.unlink()
            onboarding.apply(config, None, windows=True)
            self.assertEqual(access.load(path)["mode"], "open")
