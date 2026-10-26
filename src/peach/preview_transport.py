"""公开预览的流式传输；TLS 连接失败可使用已安装的浏览器传输。"""
from __future__ import annotations

import httpx
from curl_cffi import requests
from curl_cffi.requests.exceptions import CurlError

from . import peach_proxy
from .follow_stream import open_upstream


class _CurlStream(httpx.SyncByteStream):
    def __init__(self, response):
        self.response = response
        self.owner = None

    def __iter__(self):
        try:
            yield from self.response.iter_content()
        except CurlError as error:
            raise OSError('预览传输中断') from error

    def close(self):
        try:
            self.response.close()
        finally:
            if self.owner is not None:
                self.owner.close()
                self.owner = None


class _CurlClient:
    def __init__(self, root):
        options = peach_proxy.client_options(root)
        self.session = requests.Session(impersonate='chrome', proxy=options.get('proxy'),
                                        trust_env=options.get('trust_env', True))

    def build_request(self, method, url, *, headers):
        return httpx.Request(method, url, headers=headers)

    def send(self, request, *, stream, follow_redirects):
        try:
            response = self.session.request(request.method, str(request.url), headers=dict(request.headers),
                stream=stream, allow_redirects=follow_redirects, accept_encoding=None, timeout=20)
        except CurlError as error:
            raise OSError('预览连接未取得') from error
        return httpx.Response(response.status_code, headers=dict(response.headers),
                              stream=_CurlStream(response), request=request)

    def close(self):
        self.session.close()


def open_preview_upstream(client, method, target, *, incoming, secrets_root):
    """两种传输共用逐跳目标检查；HTTP 拒绝状态不触发传输切换。"""
    try:
        return open_upstream(client, method, target, incoming=incoming)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        fallback = _CurlClient(secrets_root)
        try:
            response = open_upstream(fallback, method, target, incoming=incoming)
            response.stream.owner = fallback
            return response
        except BaseException:
            fallback.close()
            raise
