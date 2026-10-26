import unittest
import ast
from pathlib import Path

import httpx

from peach.http import (CurlCffiTransport, HttpRequest, HttpResponse, HttpxTransport,
                        body_text, response_text)
from peach.user_agent import USER_AGENT


class UserAgentTests(unittest.TestCase):
    def test_transport_defaults_to_shared_browser_user_agent(self):
        transport = HttpxTransport()
        try:
            self.assertEqual(transport.client.headers['User-Agent'], USER_AGENT)
        finally:
            transport.close()
        self.assertIn('Chrome/', USER_AGENT)

    def test_browser_user_agent_has_one_definition(self):
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for folder in ('src', 'scripts'):
            for path in (root / folder).rglob('*.py'):
                if path.name == 'user_agent.py':
                    continue
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith('Mozilla/5.0'):
                        offenders.append(str(path.relative_to(root)))
                    if isinstance(node, ast.Dict):
                        for key, value in zip(node.keys, node.values):
                            if isinstance(key, ast.Constant) and str(key.value).lower() == 'user-agent' and isinstance(value, ast.Constant):
                                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])


class BodyTextTests(unittest.TestCase):
    """字节到文本按站点自己的声明解，不按调用方的假设解。"""

    JAPANESE = "架空の作品"

    def test_the_content_type_header_decides_the_encoding(self):
        body = self.JAPANESE.encode("euc_jp")
        self.assertEqual(
            body_text(body, {"Content-Type": "text/html; charset=EUC-JP"}), self.JAPANESE)

    def test_a_meta_declaration_decides_it_when_the_header_is_silent(self):
        for markup in (b'<meta charset="shift_jis">',
                       b'<meta http-equiv="Content-Type" content="text/html; charset=shift_jis">'):
            with self.subTest(markup=markup):
                body = b"<html><head>" + markup + self.JAPANESE.encode("shift_jis")
                self.assertIn(self.JAPANESE, body_text(body, {"Content-Type": "text/html"}))

    def test_nothing_declared_falls_back_to_the_caller_default(self):
        body = self.JAPANESE.encode("euc_jp")
        self.assertEqual(body_text(body, default="euc_jp"), self.JAPANESE)
        # 默认值是 utf-8，所以同一份 EUC-JP 字节在没有声明也没有默认值时只能替换字符。
        self.assertNotIn(self.JAPANESE, body_text(body))

    def test_the_header_wins_over_a_contradicting_meta(self):
        body = b'<meta charset="utf-8">' + self.JAPANESE.encode("euc_jp")
        self.assertTrue(
            body_text(body, {"content-type": "text/html;charset=euc-jp"}).endswith(self.JAPANESE))

    def test_an_unusable_declaration_falls_through_instead_of_raising(self):
        """站内写法 Python 不认、或者声明本身就是错的，都要退到下一个候选。"""
        body = self.JAPANESE.encode("euc_jp")
        self.assertEqual(body_text(body, {"Content-Type": "text/html; charset=x-euc-jp"},
                                   default="euc_jp"), self.JAPANESE)
        self.assertEqual(body_text(body, {"Content-Type": "text/html; charset=utf-8"},
                                   default="euc_jp"), self.JAPANESE)
        self.assertIn("�", body_text(b"\xff\xfe", default="not-a-codec"))

    def test_the_declaration_is_only_looked_for_near_the_start(self):
        body = b"<!--" + b"x" * 5000 + b'--><meta charset="euc-jp">' + self.JAPANESE.encode("euc_jp")
        self.assertNotIn(self.JAPANESE, body_text(body))

    def test_a_response_carries_its_own_headers_into_the_decoder(self):
        response = HttpResponse(200, {"content-type": "text/html; charset=euc-jp"},
                                self.JAPANESE.encode("euc_jp"))
        self.assertEqual(response_text(response), self.JAPANESE)


class _CurlResponse:
    status_code = 206
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    @staticmethod
    def iter_content():
        yield b"abc"
        yield b"def"


class _CurlSession:
    def __init__(self):
        self.calls = []
        self.closed = False

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _CurlResponse()

    def close(self):
        self.closed = True


class HttpTransportTests(unittest.TestCase):
    def test_shared_transport_bounds_response_without_hiding_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["X-Test"], "yes")
            return httpx.Response(304, headers={"ETag": '"v1"'}, content=b"abcdef")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            response = HttpxTransport(client)(
                HttpRequest("GET", "https://example.test/feed", {"X-Test": "yes"}),
                timeout=2,
                max_bytes=4,
            )
        self.assertEqual(response.status, 304)
        self.assertEqual(response.body, b"abcde")
        self.assertEqual(response.headers["etag"], '"v1"')

    def test_cookies_survive_a_same_origin_redirect_and_die_at_the_origin_boundary(self):
        """httpx 自己跟跳会丢掉显式 Cookie 头；连接器的登录会话正是这么传进来的。

        真实用例：simpcity.cr 的纯数字线程地址 301 到带 slug 的地址，带 cookie 直接请求
        目标页 200，经 httpx 跟跳到达就是 403（2026-09-08 实测）。
        """
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((str(request.url), request.headers.get("Cookie"),
                         request.headers.get("Authorization")))
            if request.url.path == "/threads/1/":
                return httpx.Response(301, headers={"Location": "/threads/slug.1/"})
            if request.url.path == "/threads/slug.1/":
                return httpx.Response(302, headers={"Location": "https://cdn.example/x"})
            return httpx.Response(200, content=b"ok")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            response = HttpxTransport(client)(
                HttpRequest("GET", "https://forum.test/threads/1/",
                            {"Cookie": "s=1", "Authorization": "Bearer t"}),
                timeout=2, max_bytes=64)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.url, "https://cdn.example/x")
        self.assertEqual(seen, [
            ("https://forum.test/threads/1/", "s=1", "Bearer t"),
            ("https://forum.test/threads/slug.1/", "s=1", "Bearer t"),
            ("https://cdn.example/x", None, None),
        ])

    def test_a_plain_http_redirect_back_to_the_same_host_is_lifted_to_https_and_keeps_the_cookie(self):
        """反向代理后面的站点把 Location 写成 `http://`，浏览器靠 HSTS 升回去。

        真实用例：javten.com 的搜索 302 到 `http://javten.com/tw/video/...`（2026-09-24 实测）；
        照明文地址请求，Secure 的 `cf_clearance` 不随请求发出，Cloudflare 回验证页。
        换了主机的 `http://` 不升，仍按跨源丢凭据。
        """
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((str(request.url), request.headers.get("Cookie")))
            if request.url.path == "/search":
                return httpx.Response(302, headers={"Location": "http://javten.test/tw/video/1"})
            if request.url.path == "/tw/video/1":
                return httpx.Response(302, headers={"Location": "http://mirror.test/video/1"})
            return httpx.Response(200, content=b"ok")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            response = HttpxTransport(client)(
                HttpRequest("GET", "https://javten.test/search?kw=1", {"Cookie": "cf_clearance=x"}),
                timeout=2, max_bytes=64)
        self.assertEqual(response.url, "http://mirror.test/video/1")
        self.assertEqual(seen, [
            ("https://javten.test/search?kw=1", "cf_clearance=x"),
            ("https://javten.test/tw/video/1", "cf_clearance=x"),
            ("http://mirror.test/video/1", None),
        ])

    def test_a_303_turns_the_post_into_a_get_without_its_body(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.content, request.headers.get("Content-Type")))
            if request.method == "POST":
                return httpx.Response(303, headers={"Location": "/done"})
            return httpx.Response(200, content=b"ok")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            HttpxTransport(client)(
                HttpRequest("POST", "https://forum.test/login", {"Content-Type": "text/plain"},
                            b"x=1"),
                timeout=2, max_bytes=64)
        self.assertEqual(seen, [("POST", b"x=1", "text/plain"), ("GET", b"", None)])

    def test_a_redirect_loop_is_reported_as_an_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": request.url.path})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(httpx.HTTPError):
                HttpxTransport(client)(HttpRequest("GET", "https://forum.test/loop", {}),
                                       timeout=2, max_bytes=64)

    def test_the_final_url_after_a_redirect_is_reported_not_the_requested_one(self):
        """两个 transport 都跟重定向，请求地址因此不等于实际取到的页面。

        真实用例：minnano-av 的女优检索唯一命中时会跳到 `actressNNN.html`，多命中则
        停在检索页。两种情况的请求地址一模一样，只有最终地址能区分——而检索页正文里
        同样有一堆 `actressNNN.html`（那是「相关女优」）。丢掉最终地址，判断「这一页
        属于谁」就只能去解析正文，那会把别人的资料安到这个人头上。
        """
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search":
                return httpx.Response(302, headers={"Location": "/actress494354.html"})
            return httpx.Response(200, content=b"ok")

        with httpx.Client(transport=httpx.MockTransport(handler),
                          follow_redirects=True) as client:
            response = HttpxTransport(client)(
                HttpRequest("GET", "https://example.test/search", {}), timeout=2,
                max_bytes=64)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.url, "https://example.test/actress494354.html")

    def test_a_transport_that_cannot_report_a_final_url_falls_back_to_the_request(self):
        """替身不带 url 时退回请求地址，语义仍成立，不必逼替身长出不关心的属性。"""
        session = _CurlSession()
        response = CurlCffiTransport(impersonate="firefox147", session=session)(
            HttpRequest("GET", "https://example.test/x", {}), timeout=2, max_bytes=64)
        self.assertEqual(response.url, "https://example.test/x")

    def test_browser_transport_keeps_the_same_bounded_contract(self):
        session = _CurlSession()
        transport = CurlCffiTransport(impersonate="firefox147", session=session)
        response = transport(
            HttpRequest("GET", "https://example.test/detail", {"X-Test": "yes"}),
            timeout=3,
            max_bytes=4,
        )
        self.assertEqual(response.status, 206)
        self.assertEqual(response.body, b"abcde")
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("GET", "https://example.test/detail"))
        self.assertEqual(kwargs["headers"]["X-Test"], "yes")
        self.assertEqual(kwargs["timeout"], 3)
        self.assertTrue(kwargs["allow_redirects"])

    def test_injected_browser_session_is_not_closed_by_the_transport(self):
        session = _CurlSession()
        transport = CurlCffiTransport(impersonate="firefox147", session=session)
        transport.close()
        self.assertFalse(session.closed)


if __name__ == "__main__":
    unittest.main()
