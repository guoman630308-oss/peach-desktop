"""按 URL 缓存整页 HTML 的取页器，供各个采集脚本共用。

采集脚本的规则改一行就得重跑，而抓页动辄十几分钟。把取回的 HTML 落在磁盘上，
重跑时判定逻辑走离线数据，只有真的没抓过的页才出网。这样调规则不再是一次完整重抓，
也不会因为反复打同一个站被限流。

缓存与限速上提到这一层：目录链接采集和厂牌名回查 javbus 都要，第二份抄本没有
存在的理由。
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx

from .http import HttpRequest, HttpxTransport
from .scripting import RateLimiter


from .user_agent import USER_AGENT


class HttpStatusError(RuntimeError):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


class Site:
    """一个来源的取页器：本地缓存优先，未命中才走网络并按间隔限速。

    缓存按 URL 的 sha1 落在 `cache_dir` 下。`cookies` 用来带过站点的年龄门——
    javbus 不给 `age=verified` 就只回一张 21 KB 的确认页，那不是内容页。
    """

    def __init__(self, cache_dir: Path, interval: float, timeout: float, *,
                 refresh: bool = False, via_proxy: bool = False, transport=None,
                 cookies: dict[str, str] | None = None, retries: int = 2,
                 backoff: float = 2.0):
        self.cache_dir, self.interval, self.timeout, self.refresh = cache_dir, interval, timeout, refresh
        self.transport = transport or HttpxTransport(
            httpx.Client(trust_env=via_proxy, follow_redirects=True, cookies=cookies or {}),
            owns_client=True)
        self.retries, self.backoff = max(0, retries), backoff
        self._limiter = RateLimiter(interval)
        self.fetched = self.cached = self.retried = 0

    def request(self, method: str, url: str, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> str:
        response = None
        for attempt in range(self.retries + 1):
            self._limiter.wait()
            try:
                response = self.transport(
                    HttpRequest(method, url, {"User-Agent": USER_AGENT, **(headers or {})},
                                body=body),
                    self.timeout, 8 << 20)
                break
            except (httpx.HTTPError, OSError):
                # 经代理取 javdatabase 实测约三次里有一次 TLS `UNEXPECTED_EOF`，重试即成。
                # 一次抖动打死整批采集是这个项目犯过两回的错，所以退让重试放在取页器里，
                # 每个脚本不必各写一遍。HTTP 状态码不重试：404 重试三次仍是 404。
                if attempt == self.retries:
                    raise
                self.retried += 1
                time.sleep(self.backoff * (attempt + 1))
        if response.status != 200:
            raise HttpStatusError(response.status)
        return response.body.decode("utf-8", "replace")

    def cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".html")

    def get(self, url: str, refresh: bool = False) -> str:
        path = self.cache_path(url)
        if not (refresh or self.refresh) and path.exists():
            self.cached += 1
            return path.read_text("utf-8")
        text = self.request("GET", url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
        self.fetched += 1
        return text

    def close(self) -> None:
        self.transport.close()
