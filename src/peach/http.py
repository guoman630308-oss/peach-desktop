"""共享 HTTP transport：连接池、超时、有界读取、字符集解码与可注入测试边界。

还有一份「这个地址能不能让 Peach 替人去取」的判据。它属于这里而不是某一条具体链路：
Peach 跑在用户自己的机器上，能访问路由器后台、NAS、局域网里别的服务和本机的各个
端口——任何一处「你给地址我去下」的入口都是一个替人发请求的跳板。判据只写一份，
新入口直接用，不要各自再写一遍宽严不一的版本。
"""
from __future__ import annotations
from .user_agent import USER_AGENT

import ipaddress
import re
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Mapping, Protocol

import httpx
from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import CurlError

#: 只在本机或局域网里有意义的名字后缀。外面递进来的地址落到它们上，只可能是想让
#: Peach 替人去探内网。
LOCAL_NAME_SUFFIXES = ("localhost", "local", "internal", "intranet", "lan", "home.arpa")


def public_https_url(url: str) -> bool:
    """字面判据：https、公网域名、不带用户信息。

    IP 字面量一律不算——内网地址正是用字面量写的，而公网服务都有域名。域名解析到
    哪里由 `resolves_publicly` 在真正连接前再查一次，这里只看字面。
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    if "." not in host:
        return False
    return not any(host == suffix or host.endswith("." + suffix)
                   for suffix in LOCAL_NAME_SUFFIXES)


def host_addresses(host: str) -> tuple[str, ...]:
    """主机名解析到的全部地址；解析不了就是空。测试只替换这一个函数。"""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return ()
    return tuple(str(info[4][0]) for info in infos)


def resolves_publicly(host: str) -> bool:
    """最后一道：字面上像公网的域名，解析出来也必须**全部**是公网地址。

    全部而不是任意一个：一个名字可以同时解析到公网和内网地址，只要放行一条，
    连接落到哪一条就不由我们说了算。
    """
    addresses = host_addresses(host)
    if not addresses:
        return False
    for address in addresses:
        try:
            if not ipaddress.ip_address(address).is_global:
                return False
        except ValueError:
            return False
    return True


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None = None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    #: 跟完重定向后的最终地址。两个 transport 都跟重定向，请求 URL 因此不等于实际
    #: 取到的页面；判断「这一页属于谁」必须看最终地址，而不是我们发出去的那个。
    #: 默认空字符串是为了让既有的三参数构造（多在测试里）保持可用。
    url: str = ""


class HttpTransport(Protocol):
    def __call__(
        self,
        request: HttpRequest,
        timeout: float,
        max_bytes: int,
    ) -> HttpResponse: ...


#: 从响应开头扫多少字节找 `<meta>` 里的字符集声明。
#: 参考实现（NeoAVDC `src/main/net/httpClient.ts` 的 `bodyToText`）只扫 1 KB；这里取
#: 4 KiB，因为 seesaawiki 的声明落在 1 KB 之后，要扫到前 3000 字节才认得出 UTF-8 页，
#: 扫 1 KB 会把那些页全部按回落编码解。
META_SNIFF_BYTES = 4096

_CHARSET_IN_HEADER = re.compile(r"charset\s*=\s*[\"']?\s*([\w.:-]+)", re.IGNORECASE)
#: `<meta charset="euc-jp">` 与 `<meta http-equiv="Content-Type" content="...; charset=euc-jp">`
#: 两种写法都认。在字节上匹配，免得为了找编码先猜一次编码。
_CHARSET_IN_META = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.IGNORECASE)


def _declared_encodings(body: bytes, headers: Mapping[str, str] | None) -> list[str]:
    """站点自己声明的字符集，按可信度排序：响应头在前，页面内的 `<meta>` 在后。"""
    found: list[str] = []
    for key, value in (headers or {}).items():
        if key.lower() != "content-type":
            continue
        matched = _CHARSET_IN_HEADER.search(str(value))
        if matched:
            found.append(matched.group(1))
    matched = _CHARSET_IN_META.search(body[:META_SNIFF_BYTES])
    if matched:
        found.append(matched.group(1).decode("ascii", "ignore"))
    return [name for name in dict.fromkeys(found) if name]


def body_text(body: bytes, headers: Mapping[str, str] | None = None, *,
              default: str = "utf-8") -> str:
    """按站点自己声明的字符集把响应体解成文本。

    顺序和浏览器一致：先 `Content-Type` 的 charset，再响应开头的 `<meta>` 声明，
    都没有才用 `default`。硬写 utf-8 的代价不是报错而是静默损坏——日站里 EUC-JP 与
    Shift_JIS 仍占相当比例，`errors="replace"` 会把整行标题变成 U+FFFD，而「标题里
    没有这个厂牌名」这条判定会据此误杀掉真官网。

    `default` 留给已知编码的单站：seesaawiki 的页面不声明时就是 EUC-JP。声明的编码
    Python 不认（`x-sjis` 这类站内写法）或者用它解不动，就退到下一个候选；全都不成
    才用 `default` 加 `errors="replace"` 兜底，宁可留几个替换字符也不抛异常。
    """
    for encoding in (*_declared_encodings(body, headers), default):
        try:
            return body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return body.decode(default, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def response_text(response: "HttpResponse", *, default: str = "utf-8") -> str:
    """`body_text` 的响应版：响应头已经在手里，不必让每个调用方自己拆出来。"""
    return body_text(response.body, response.headers, default=default)


#: 跟这些状态的 Location。与 httpx 自己的判据一致。
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
#: 跨源跳转时丢掉的请求头：凭据只属于发出它的那个站。
_CREDENTIAL_HEADERS = ("cookie", "authorization")


def _same_origin(left: httpx.URL, right: httpx.URL) -> bool:
    return (left.scheme, left.host, left.port) == (right.scheme, right.host, right.port)


def _without(headers: dict[str, str], names: tuple[str, ...]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in names}


class HttpxTransport:
    """可跨请求复用的同步 HTTPX client；不会自动重试或持久化响应。

    重定向自己跟，不交给 httpx 的 `follow_redirects`：httpx 跟跳时会无条件丢掉显式的
    `Cookie` 头，只保留 client 自己 cookie jar 里的那些。连接器的登录会话正是作为请求头
    传进来的，于是「直接请求目标页 200、经一次同源 30x 到达就 403」（2026-09-08 实测
    simpcity.cr 的 `/latest` 与纯数字线程地址）。这里同源跳转保留全部请求头，跨源跳转
    丢掉 Cookie 与 Authorization——凭据只属于发出它的那个站。
    """

    #: 最多跟几跳。站点正常只跳一两次，再多就是环。
    MAX_REDIRECTS = 5

    def __init__(self, client: httpx.Client | None = None, *, owns_client: bool = False):
        self.client = client or httpx.Client(
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            headers={"User-Agent": USER_AGENT},
        )
        self._owns_client = client is None or owns_client

    def __call__(
        self,
        request: HttpRequest,
        timeout: float,
        max_bytes: int,
    ) -> HttpResponse:
        method = request.method
        url = httpx.URL(request.url)
        headers = dict(request.headers)
        body = request.body
        for _ in range(self.MAX_REDIRECTS + 1):
            with self.client.stream(
                method, url, headers=headers, content=body, timeout=timeout,
                follow_redirects=False,
            ) as response:
                location = response.headers.get("location")
                if response.status_code not in _REDIRECT_STATUSES or not location:
                    return HttpResponse(
                        response.status_code,
                        dict(response.headers),
                        _bounded_body(response.iter_bytes(), max_bytes),
                        str(response.url),
                    )
                target = response.url.join(location)
            if url.scheme == "https" and target.scheme == "http" and target.host == url.host:
                # 反向代理后面的站点常把 Location 写成 `http://`：源站看到的是明文连接（2026-09-24 实测
                # javten.com 搜索一跳）。浏览器靠 HSTS 升回 https，这里也升：明文地址不带 Secure Cookie，
                # 站前的 Cloudflare 会把这一跳当成陌生人。只升不降，https 落到别的主机照旧按跨源处理。
                target = target.copy_with(scheme="https", port=None)
            if not _same_origin(url, target):
                headers = _without(headers, _CREDENTIAL_HEADERS)
            # 303 一律改成 GET；301/302 只把非 GET 改成 GET，和浏览器与 httpx 的做法一致。
            if response.status_code == 303 or (
                    response.status_code in (301, 302) and method not in ("GET", "HEAD")):
                if method != "HEAD":
                    method = "GET"
                body = None
                headers = _without(headers, ("content-length", "content-type"))
            url = target
        raise httpx.TooManyRedirects(
            f"超过 {self.MAX_REDIRECTS} 次重定向", request=response.request)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


def _bounded_body(chunks, max_bytes: int) -> bytes:
    """最多读 `max_bytes + 1` 字节：多出的那一个字节让调用方能判「超限」而不是「刚好」。"""
    kept: list[bytes] = []
    total = 0
    for chunk in chunks:
        remaining = max_bytes + 1 - total
        if remaining <= 0:
            break
        kept.append(chunk[:remaining])
        total += min(len(chunk), remaining)
        if total > max_bytes:
            break
    return b"".join(kept)


class CurlCffiTransport:
    """保持浏览器 TLS/HTTP2 指纹的有界同步 transport。

    只给明确需要浏览器传输特征的公开接口使用；它不求解验证码，也不重试。
    """

    def __init__(self, *, impersonate: str,
                 session: curl_requests.Session | None = None):
        self.session = session or curl_requests.Session(impersonate=impersonate)
        self._owns_session = session is None

    def __call__(
        self,
        request: HttpRequest,
        timeout: float,
        max_bytes: int,
    ) -> HttpResponse:
        chunks: list[bytes] = []
        total = 0
        try:
            with self.session.stream(
                request.method,
                request.url,
                headers=dict(request.headers),
                content=request.body,
                timeout=timeout,
                allow_redirects=True,
            ) as response:
                for chunk in response.iter_content():
                    remaining = max_bytes + 1 - total
                    if remaining <= 0:
                        break
                    chunks.append(chunk[:remaining])
                    total += min(len(chunk), remaining)
                    if total > max_bytes:
                        break
                return HttpResponse(
                    response.status_code,
                    dict(response.headers),
                    b"".join(chunks),
                    # curl_cffi 的响应带 url，但注入的测试替身不一定；取不到就退回请求
                    # 地址，语义仍然成立（没跟到重定向即最终地址就是请求地址），
                    # 也不必逼每个替身为一个它不关心的字段长出属性。
                    str(getattr(response, "url", "") or request.url),
                )
        except CurlError as exc:
            raise OSError("browser transport request failed") from exc

    def close(self) -> None:
        if self._owns_session:
            self.session.close()
