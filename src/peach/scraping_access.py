"""采集来源的本机设置、域内 Cookie 和按来源连接策略。"""
from __future__ import annotations

import http.cookiejar
import hashlib
from http.cookies import SimpleCookie, CookieError
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.parse import urlsplit, urljoin

import httpx

from .follow_secrets import CredentialStore
from . import browser_transport, peach_proxy
from .http import HttpRequest, HttpxTransport
from .scripting import USER_AGENT, host_under, hostname_of
from .fsutil import atomic_write_text

LOGGER = logging.getLogger(__name__)


SOURCES = {
    "r18dev": {"label": "R18.dev", "domains": ("r18.dev",), "login": "https://r18.dev/"},
    "dmm": {"label": "DMM / FANZA", "domains": ("dmm.co.jp", "dmm.com"), "login": "https://www.dmm.co.jp/"},
    "prestige": {"label": "Prestige", "domains": ("prestige-av.com",), "login": "https://www.prestige-av.com/"},
    "mgstage": {"label": "MGStage", "domains": ("mgstage.com",), "login": "https://www.mgstage.com/"},
    "faleno": {"label": "FALENO", "domains": ("faleno.jp",), "login": "https://faleno.jp/"},
    "dahlia": {"label": "DAHLIA", "domains": ("dahlia-av.jp",), "login": "https://dahlia-av.jp/"},
    "makers": {"label": "片商官网", "domains": (), "login": ""},
    "fc2club": {"label": "FC2Club", "domains": ("fc2club.top",), "login": "https://fc2club.top/"},
    "freejavbt": {"label": "FreeJavBT", "domains": ("freejavbt.com",), "login": "https://freejavbt.com/"},
    "airav": {"label": "AIRAV", "domains": ("airav.io",), "login": "https://airav.io/"},
    "avsox": {"label": "AVSOX", "domains": ("avsox.click",), "login": "https://avsox.click/"},
    # FC2 的商品页、卖家页和图片存储（`storage*`、`contents-thumbnail*`）都在 fc2.com
    # 底下，一条域名就够。免登录可读，不收 Cookie。
    "fc2": {"label": "FC2", "domains": ("fc2.com",), "login": "https://adult.contents.fc2.com/"},
    # 女優那一栏在浏览器里只对登录用户显示，公开采集也带上用户贴的 Cookie（`session`），
    # 按登录用户看到的那一页取；游客补问这一栏眼下也回，见 `sources/fc2cmadb.py`。
    "fc2cmadb": {"label": "FC2CMADB", "domains": ("fc2cmadb.com",), "login": "https://fc2cmadb.com/",
                 "cookie": True, "session": True},
    # FC2 商品的两个存档站都在 Cloudflare 的 JS 验证后面：httpx 与模拟 Chrome 指纹的直连都回 403，
    # 只有浏览器过完验证发下的 `cf_clearance` 能进，而它绑着解题那台浏览器的 User-Agent 与出口。
    # `browser`：请求在本机 Edge/Chrome 的页面里发，验证由它自己过（ADR-0065）；这台机器没有可驱动的
    # 浏览器时才退回下面这条路——整站 UA 与用户的 Chrome 一致、Cookie 由用户贴、连接方式选成与浏览器
    # 同一个出口。验证没过或 Cookie 过期都按 `blocked_pause` 停下，不反复撞。
    # `browser_gate`：新 profile 第一次进站会被送到年龄确认页（`/ja/age-verify?returnTo=…`），点那颗
    # 「18 岁以上」的按钮才回到要看的页；浏览器传输落到这个路径就替用户点。
    "fc2ppvdb": {"label": "FC2PPV-DB", "domains": ("fc2ppv-db.com",), "login": "https://fc2ppv-db.com/",
                 "cookie": True, "session": True, "browser": True, "blocked_pause": 6 * 3600,
                 "browser_gate": {"path": "/age-verify", "button": r"18|はい|入場|同意|以上|Enter|Yes"}},
    "javten": {"label": "JAVten", "domains": ("javten.com",), "login": "https://javten.com/",
               "cookie": True, "session": True, "browser": True, "blocked_pause": 6 * 3600},
    # 下架 FC2 的最后一档。作品页在 javarchive.com、封面转存在 javstore.net 上，两边算同一个
    # 来源。免登录可读，不收 Cookie，`robots.txt` 是全站放行。
    "javarchive": {"label": "JavArchive", "domains": ("javarchive.com", "javstore.net"),
                   "login": "https://javarchive.com/"},
    # 作品 JSON、剧照和样片都在 1pondo.tv 底下（含 `smovie.`）。免登录可读，不收 Cookie。
    "1pondo": {"label": "一本道", "domains": ("1pondo.tv",), "login": "https://www.1pondo.tv/"},
    "instagram": {"label": "Instagram", "domains": ("instagram.com", "cdninstagram.com"), "login": "https://www.instagram.com/accounts/login/", "cookie": True},
    # 三家社区来源拒绝访问时回 403，不发 Retry-After：javdb 是出口 IP 超了配额（一封 3～7 日，
    # docs/SOURCING.md），AVBase 是 Cloudflare 验证，JavBus 的 403 原因未取得。封期里接着问只会每条都
    # 再撞一次，所以整个来源停下——停多久按 `FIRST_BLOCKED_PAUSE` 翻倍，`blocked_pause` 是这个来源的上限。
    # 回 200 的验证页由解析器认成 `cloudflare_challenge`，经 `SourceTransport.hold` 进同一份记录。
    # `session`：公开采集也带上用户在采集设置里贴的 Cookie。javdb 有登录墙，JavBus 有年龄门，
    # 不带只回确认页；Cookie 由用户在浏览器里过门或登录后贴进来，Peach 不读浏览器的 Cookie 库。
    "javdb": {"label": "JavDB", "domains": ("javdb.com", "jdbstatic.com", "jdbimgs.com"), "login": "https://javdb.com/",
              "cookie": True, "session": True, "blocked_pause": 24 * 3600},
    "javbus": {"label": "JavBus", "domains": ("javbus.com",), "login": "https://www.javbus.com/", "cookie": True,
               "session": True, "blocked_pause": 6 * 3600},
    "javlibrary": {"label": "JavLibrary", "domains": ("javlibrary.com",), "login": "https://www.javlibrary.com/cn/",
                   "cookie": True, "session": True, "browser": True, "blocked_pause": 6 * 3600},
    # 磁力候选链。manko.fun 的页面数据来自同一产品使用的 healertanker API；Sukebei
    # 只读搜索结果。两者仍走统一的请求预算、来源冷却与域名白名单。
    "manko": {"label": "manko.fun", "domains": ("manko.fun", "healertanker.com"),
              "login": "https://manko.fun/", "blocked_pause": 6 * 3600},
    "sukebei": {"label": "sukebei", "domains": ("sukebei.nyaa.si",),
                "login": "https://sukebei.nyaa.si/", "blocked_pause": 6 * 3600},
    "avbase": {"label": "AVBase", "domains": ("avbase.net",), "login": "https://www.avbase.net/", "blocked_pause": 6 * 3600},
    # 女优编号与资料交叉核对（ADR-0067）。httpx 直连就回整页，不收 Cookie、不走浏览器；
    # 拒绝访问时和 AVBase 一样停一段，不反复撞。
    "avwikidb": {"label": "AVWikiDB", "domains": ("avwikidb.com",), "login": "https://avwikidb.com/",
                 "blocked_pause": 6 * 3600},
    # 女优资料表、别名与事务所名册（补别名、补女优资料、链接与名册采集）。Cloudflare 对 HTTP 客户端
    # 间歇拦截：同一天里有的请求 403、有的照常 200。`browser_fallback`：HTTP 客户端撞上 403、429 或
    # 验证页时，改由本机浏览器取同一页验证；取到正常页就把这个来源固定到浏览器（`fixed_to_browser`），
    # 浏览器也没取到才照常冷却。不收 Cookie。
    "minnano-av": {"label": "みんなのAV", "domains": ("minnano-av.com",), "login": "https://www.minnano-av.com/",
                   "blocked_pause": 6 * 3600, "browser_fallback": True},
}
_LOCK = threading.RLock()

#: 撞上拒绝访问后第一次停多久。`blocked_pause` 是**上限**，不是首停时长：那几家按出口
#: IP 计的封，实际封期常常远短于上限。2026-09-22 实测——javdb 记下的 24 小时只走了
#: 6.6 小时，用同一套 client 问首页和两条搜索全回 200，剩下的时间整个来源在盲等，那一轮
#: 778 部片的 1432 条失败全部写着「来源正在冷却」。所以先停这一档，连着再撞才翻倍到上限；
#: 通了一次就把记录清掉，下次从最短的一档重新起算。
FIRST_BLOCKED_PAUSE = 900


class SourcePaused(RuntimeError):
    """来源冷却期内停止请求，保留已有图像。"""


def cooldown_path(root: Path, source: str) -> Path:
    """这个来源的冷却记录。键是来源名；没有来源名的请求按主机名散列，见 `SourceTransport`。"""
    return Path(root) / ("scraping-" + source + ".cooldown.json")


#: 冷却记录里的 `via`：写下这笔账时请求走的是哪条路。HTTP 客户端（httpx 直连或经 Peach 代理、amane 桥）
#: 与本机浏览器（ADR-0065）在站方眼里是两个客户端，一条路撞出的封不说明另一条路进不去。
#: 没有 `via` 的记录按 HTTP 客户端算。
VIA_HTTP = "http"
VIA_BROWSER = "browser"
#: 来源设置里记「这个来源已固定走浏览器」的键，值是 `VIA_BROWSER`。只对登记了 `browser_fallback` 的来源有效。
FIXED_TRANSPORT = "transport"


def cooldown_state(root: Path, source: str) -> tuple[float, int]:
    """`(冷却到几点, 连着撞了几次)`；没有记录或记录坏了都按 `(0, 0)`。"""
    until, blocks, _via = _record(root, source)
    return until, blocks


def _record(root: Path, source: str) -> tuple[float, int, str]:
    """`(冷却到几点, 连着撞了几次, 走的哪条路)`；没有记录或记录坏了都按 `(0, 0, VIA_HTTP)`。"""
    try:
        record = json.loads(cooldown_path(root, source).read_text(encoding="utf-8"))
        return float(record["until"]), int(record.get("blocks") or 0), str(record.get("via") or VIA_HTTP)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0.0, 0, VIA_HTTP


def _state_for(root: Path, source: str, via: str) -> tuple[float, int]:
    """走 `via` 这条路时该认的冷却：记录是另一条路写下的就作废（删掉），按 `(0, 0)` 从第一档重新数。"""
    until, blocks, recorded = _record(root, source)
    if recorded != via and (until or blocks):
        with _LOCK:
            cooldown_path(root, source).unlink(missing_ok=True)
        return 0.0, 0
    return until, blocks


def paused_until(root: Path, source: str) -> float:
    """来源还在冷却就返回到几点，否则 0。amane 桥那几站在起子进程前按它筛。"""
    until, _ = cooldown_state(root, source)
    return until if until > time.time() else 0.0


def pause_source(root: Path, source: str, *, refused: bool = False,
                 retry_after: float | None = None, via: str = VIA_HTTP) -> float:
    """按现成的两档把来源停下，返回冷却到几点。

    `refused` 走 403 那一档：先停 `FIRST_BLOCKED_PAUSE`，连着再撞才翻倍，上限是
    `SOURCES[source]['blocked_pause']`，没登记上限的来源按 24 小时。否则走 429 那一档：
    停站方说的 `retry_after` 秒，没说就 15 分钟。amane 桥报的限流与封禁、自写站契约报的
    冷却细档（`SourceTransport.hold`）都从这里进，与 `SourceTransport` 读写同一份记录。
    `via` 是这次请求走的路；记录是另一条路写下的，就从第一档重新数。
    """
    until, blocks = _state_for(root, source, via)
    if refused:
        blocks += 1
        limit = SOURCES.get(source, {}).get("blocked_pause") or 24 * 3600
        until = time.time() + min(FIRST_BLOCKED_PAUSE * 2 ** (blocks - 1), limit)
    else:
        until = time.time() + max(0.0, float(retry_after if retry_after is not None else 900))
    _pause(cooldown_path(root, source), until, blocks, via)
    return until


def _pause(cooldown: Path, until: float, blocks: int = 0, via: str = VIA_HTTP) -> None:
    with _LOCK:
        cooldown.parent.mkdir(parents=True, exist_ok=True)
        cooldown.write_text(json.dumps({"until": max(until, time.time() + 1), "blocks": blocks, "via": via}),
                            encoding="utf-8")


def source_for(url: str) -> str | None:
    return next((name for name, spec in SOURCES.items()
                 if host_under(hostname_of(url), spec["domains"])), None)


def _store(root: Path) -> CredentialStore:
    return CredentialStore(root)


def values_for(root: Path, source: str) -> dict[str, str]:
    if source not in SOURCES:
        raise ValueError("未知采集来源")
    credential = _store(root).load("scraping-" + source)
    return dict(credential.values) if credential else {}


def fixed_to_browser(root: Path, source: str) -> bool:
    """登记了 `browser_fallback` 的来源，是不是已经在这台机器上验证过浏览器能进、固定走浏览器。"""
    return (bool(SOURCES[source].get("browser_fallback"))
            and values_for(root, source).get(FIXED_TRANSPORT) == VIA_BROWSER)


def wants_browser(root: Path, source: str) -> bool:
    """这个来源该不该走本机浏览器：登记了 `browser`，或 `browser_fallback` 已固定到浏览器。"""
    return bool(SOURCES[source].get("browser")) or fixed_to_browser(root, source)


def _write_values(root: Path, source: str, values: dict[str, str]) -> None:
    path = _store(root).path_for("scraping-" + source)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(values, handle, ensure_ascii=False)
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _refused(response) -> bool:
    """HTTP 客户端这一下是不是被站方拦了：429、403，或回 200 的 Cloudflare 验证页。"""
    from .sources.base import challenge_page

    return response.status in {403, 429} or challenge_page(response.body)


def cookie_jar(values: dict[str, str], source: str) -> http.cookiejar.CookieJar:
    """文本导入复用标准库；过期项、异域项和 CDN 会话项不参与请求。"""
    domain = urlsplit(SOURCES[source]["login"]).hostname or ""
    domain = domain.removeprefix("www.")
    jar = http.cookiejar.MozillaCookieJar()
    text = values.get("cookies_text", "")
    if text:
        path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as handle:
                path = handle.name
                handle.write(text)
            jar.load(path, ignore_discard=True, ignore_expires=False)
        except (OSError, http.cookiejar.LoadError) as exc:
            raise ValueError("Cookie 文件须为 Netscape 文本格式") from exc
        finally:
            if path:
                Path(path).unlink(missing_ok=True)
        for cookie in list(jar):
            if not host_under(cookie.domain.lstrip(".").lower(), (domain,)):
                jar.clear(cookie.domain, cookie.path, cookie.name)
    elif values.get("cookie"):
        text = values["cookie"]
        if "\r" in text or "\n" in text:
            raise ValueError("请粘贴一行 Cookie 请求头")
        parsed = SimpleCookie()
        try:
            parsed.load(text)
        except CookieError as exc:
            raise ValueError("Cookie 请求头格式无效") from exc
        for name, item in parsed.items():
            jar.set_cookie(http.cookiejar.Cookie(
                0, name, item.value, None, False, domain, True, False,
                "/", True, True, None, True, None, None, {}, False))
    return jar


def save(root: Path, source: str, body: dict) -> dict:
    with _LOCK:
        values = values_for(root, source)
        previous_network = "direct" if values.get("network") == "direct" else "peach"
        mode = body.get("network", "direct" if values.get("network") == "direct" else "peach")
        if mode not in {"direct", "peach"}:
            raise ValueError("请选择来源连接方式")
        if values.get("network") == "proxy" and mode == "peach":
            inherited = peach_proxy.values(root)
            peach_proxy.client_options(root)
            peach_proxy.save(root, inherited)
        values["network"] = mode
        values.pop("proxy", None)
        if body.get("revoke"):
            values.pop("cookie", None)
            values.pop("cookies_text", None)
        elif body.get("cookie") or body.get("cookies_text"):
            if not SOURCES[source].get("cookie"):
                raise ValueError("此来源不接受登录 Cookie")
            if body.get("cookie") and body.get("cookies_text"):
                raise ValueError("Cookie 粘贴与文件导入只选一种")
            supplied = {name: str(body.get(name) or "") for name in ("cookie", "cookies_text")}
            if sum(map(len, supplied.values())) > 256 * 1024:
                raise ValueError("Cookie 文本超过 256 KiB")
            if not list(cookie_jar(supplied, source)):
                raise ValueError("未找到该来源尚未过期的 Cookie")
            values.update(supplied)
            # 新 Cookie 多半是为了解开 403 才换的；冷却记的是旧 Cookie 撞出来的账，别让它压着新的等到期。
            cooldown_path(root, source).unlink(missing_ok=True)
        _write_values(root, source, values)
        if mode != previous_network:
            cooldown_path(root, source).unlink(missing_ok=True)
    return describe(root, source)


def fix_to_browser(root: Path, source: str) -> None:
    """把登记了 `browser_fallback` 的来源固定到本机浏览器：写进来源设置，连接方式与 Cookie 原样留着。"""
    with _LOCK:
        _write_values(root, source, {**values_for(root, source), FIXED_TRANSPORT: VIA_BROWSER})


def describe(root: Path, source: str) -> dict:
    values = values_for(root, source)
    until = paused_until(root, source)
    return {"source": source, "label": SOURCES[source]["label"],
            "login": SOURCES[source]["login"], "accepts_cookie": bool(SOURCES[source].get("cookie")),
            "network": "direct" if values.get("network") == "direct" else "peach",
            "cookie_saved": bool(values.get("cookie") or values.get("cookies_text")),
            "cooldown_until": until or None,
            # 这台机器上这个来源是不是由本机浏览器过验证：该走浏览器（`wants_browser`）且找得到浏览器。
            "browser": wants_browser(root, source) and browser_transport.find_browser() is not None}


def source_checks(root: Path, source: str) -> dict:
    """逐站最近一次连接与解析结果；不包含请求地址、正文或凭据。"""
    if source not in SOURCES:
        return {}
    try:
        path = Path(root) / f"source-check-{source}.json"
        if path.stat().st_size > 16384:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        result = {}
        for stage in ("connection", "metadata"):
            record = value.get(stage)
            if not isinstance(record, dict):
                continue
            clean = {"ok": record.get("ok") is True,
                     "kind": record.get("kind") if record.get("kind") in ("ok", "auth", "unavailable", "not_found") else "unavailable",
                     "reason": record.get("reason") if isinstance(record.get("reason"), str) and len(record["reason"]) <= 64 else "unexpected",
                     "status": record.get("status") if type(record.get("status")) is int and 100 <= record["status"] <= 599 else 0}
            for key in ("checked_at", "last_success_at", "elapsed_s"):
                raw = record.get(key)
                if isinstance(raw, bool) or raw is None:
                    continue
                try:
                    if isinstance(raw, str) and key != "elapsed_s":
                        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        number = moment.replace(tzinfo=timezone.utc).timestamp() if moment.tzinfo is None else moment.timestamp()
                    else:
                        number = float(raw)
                    if math.isfinite(number) and number >= 0:
                        clean[key] = number
                except (ValueError, TypeError, OverflowError, OSError):
                    pass
            if "checked_at" in clean:
                result[stage] = clean
        return result
    except (OSError, ValueError):
        return {}


def record_source_check(root: Path, source: str, stage: str, record: dict) -> None:
    """保存脱敏状态，保留连接检查与元数据解析的独立时间戳。"""
    if source not in SOURCES or stage not in {"connection", "metadata"}:
        return
    allowed = {"ok", "kind", "reason", "status", "elapsed_s", "checked_at"}
    with _LOCK:
        state = source_checks(root, source)
        clean = {key: value for key, value in record.items() if key in allowed}
        if stage == "metadata":
            clean["last_success_at"] = record.get("checked_at") if record.get("ok") else state.get(stage, {}).get("last_success_at")
        state[stage] = clean
        try:
            atomic_write_text(Path(root) / f"source-check-{source}.json",
                              json.dumps(state, ensure_ascii=False), mode=0o600)
        except OSError:
            LOGGER.warning("来源 %s 的诊断记录未保存", source)


def browser_profile(root: Path) -> Path:
    """浏览器 profile 放在凭据根下：里面是 `cf_clearance` 与站点会话，和贴进来的 Cookie 一个性质。"""
    return Path(root) / "browser"


def client_for(root: Path, source: str, *, session: bool = False, **kwargs) -> httpx.Client:
    values = values_for(root, source)
    network = {"trust_env": False} if values.get("network") == "direct" else peach_proxy.client_options(root)
    options = {**network, "follow_redirects": True,
               "headers": {"User-Agent": USER_AGENT}}
    if session:
        options["cookies"] = cookie_jar(values, source)
    options.update(kwargs)
    return httpx.Client(**options)


class SourceTransport:
    """公开采集按来源复用连接池；登录会话由特定来源消费者显式建立。"""

    def __init__(self, secrets_root: Path, *, max_requests: int = 0,
                 max_bytes: int = 0, max_seconds: float = 0):
        self.root = secrets_root
        self.transports: dict[str | None, HttpxTransport] = {}
        self.max_requests, self.max_bytes = max_requests, max_bytes
        from .processing_control import deadline_after
        self.deadline = deadline_after(max_seconds) if max_seconds else 0
        self.requests = self.bytes = 0

    def __call__(self, request: HttpRequest, timeout: float, max_bytes: int):
        for _ in range(6):
            from .processing_control import checkpoint
            checkpoint('network')
            response = self._request(request, timeout, max_bytes)
            if response.status not in {301, 302, 303, 307, 308} or not response.headers.get("location"):
                return response
            target = urljoin(request.url, response.headers["location"])
            if urlsplit(target).scheme != "https":
                raise ValueError("采集重定向必须使用 HTTPS")
            cross_origin = urlsplit(target).netloc != urlsplit(request.url).netloc
            headers = {k: v for k, v in request.headers.items()
                       if not cross_origin or k.lower() not in {"cookie", "authorization", "host"}}
            request = HttpRequest("GET" if response.status == 303 else request.method,
                                  target, headers, None if response.status == 303 else request.body)
        raise ValueError("采集重定向次数超过上限")

    def _request(self, request: HttpRequest, timeout: float, max_bytes: int):
        """闸门到顶的三句话都写「本趟」和「再跑一次接着采」：这三条按任务重新计数，
        读的人要据以决定的是「现在重跑一遍」，不是去查哪里还有配额。"""
        source = source_for(request.url)
        if self.max_requests and self.requests >= self.max_requests:
            raise SourcePaused("本趟采集次数已用完，再跑一次接着采；已有图片保留")
        if self.deadline:
            timeout = min(timeout, self.deadline - time.monotonic())
            if timeout <= 0:
                raise SourcePaused("本趟采集时间已用完，再跑一次接着采；已有图片保留")
        if self.max_bytes:
            remaining = self.max_bytes - self.bytes
            if remaining <= 0:
                raise SourcePaused("本趟下载量已用完，再跑一次接着采；已有图片保留")
            max_bytes = min(max_bytes, remaining - 1)
        key = source or hashlib.sha256(hostname_of(request.url).encode()).hexdigest()
        cooldown = cooldown_path(self.root, key)
        # 冷却只认这条路自己撞出来的账：直连 403 攒下的 `blocks` 与 `until` 不压到浏览器那条路上，反之亦然。
        via = self._via(source)
        until, blocks = _state_for(self.root, key, via)
        if until > time.time():
            raise SourcePaused("来源正在冷却，请稍后重试；已有图片保留")
        if source not in self.transports:
            self.transports[source] = self._transport_for(source)
        self.requests += 1
        try:
            response = self.transports[source](request, timeout, max_bytes)
        except (httpx.HTTPError, browser_transport.BrowserUnavailable):
            raise httpx.TransportError("来源连接未取得，请检查该来源的网络设置") from None
        except browser_transport.ChallengeUnsolved:
            blocks += 1
            _pause(cooldown, time.time() + min(FIRST_BLOCKED_PAUSE * 2 ** (blocks - 1),
                                               SOURCES[source]["blocked_pause"]), blocks, via)
            raise SourcePaused("来源的人机验证没有在限时内通过，暂停向它请求一段时间；"
                               "浏览器窗口再弹出时点一下验证即可；已有图片保留")
        if via == VIA_HTTP and source and SOURCES[source].get("browser_fallback") and _refused(response):
            verified = self._verify_in_browser(source, request, timeout, max_bytes)
            if verified is not None:
                # HTTP 客户端那条路攒下的账不压到浏览器上：清掉，之后按浏览器那条路重新数。
                cooldown.unlink(missing_ok=True)
                response, via, blocks = verified, VIA_BROWSER, 0
        self.bytes += len(response.body)
        if response.status == 429:
            retry = response.headers.get("retry-after", "")
            try:
                until = time.time() + max(0, int(retry))
            except ValueError:
                try:
                    until = parsedate_to_datetime(retry).timestamp()
                except (ValueError, TypeError, OverflowError):
                    until = time.time() + 900
            _pause(cooldown, until, blocks, via)
            raise SourcePaused("来源限流，已记录冷却时间；已有图片保留")
        blocked_pause = SOURCES.get(source or "", {}).get("blocked_pause")
        if response.status == 403 and blocked_pause:
            blocks += 1
            _pause(cooldown, time.time()
                   + min(FIRST_BLOCKED_PAUSE * 2 ** (blocks - 1), blocked_pause), blocks, via)
            if SOURCES[source].get("cookie"):
                raise SourcePaused("来源拒绝访问，暂停向它请求一段时间；"
                                   "请在采集设置里更新它的 Cookie；已有图片保留")
            raise SourcePaused("来源拒绝访问，暂停向它请求一段时间；已有图片保留")
        if blocks and response.status < 400:
            # 这一趟通了，封已经解除：清掉记录，下次撞上从最短的一档重新起算。
            cooldown.unlink(missing_ok=True)
        return response

    def hold(self, source: str, action: str) -> None:
        """契约报的冷却细档（`SourceFailure.cooldown_action`）写回这个来源的冷却记录。

        `blocked` 按 403 那一档翻倍，`rate_limited` 按 429 那一档；别的值与不在 `SOURCES` 里的来源不记。
        回 200 的验证页（AVBase、JAVten、FC2PPV-DB 的 httpx 那条路）在传输层看不出来，由解析器认出后经
        `SiteSource.holding` 走到这里，与 403 进的是同一份记录。
        """
        if action not in {"blocked", "rate_limited"} or source not in SOURCES:
            return
        pause_source(self.root, source, refused=action == "blocked", via=self._via(source))

    def _via(self, source: str | None) -> str:
        """这个来源这次走哪条路。该走浏览器的来源要先选出传输才知道（不发请求、不起进程）。"""
        if source and source not in self.transports and wants_browser(self.root, source):
            self.transports[source] = self._transport_for(source)
        transport = self.transports.get(source)
        return VIA_BROWSER if isinstance(transport, browser_transport.BrowserTransport) else VIA_HTTP

    def _verify_in_browser(self, source: str, request: HttpRequest, timeout: float, max_bytes: int):
        """HTTP 客户端被拦后，由本机浏览器取同一页验证。

        取到 200 且不是验证页：这个来源固定到浏览器（`fix_to_browser`），本趟之后的请求也走浏览器，返回这一页。
        本机没有浏览器、浏览器没取到或同样被拦：返回 None，调用方照 HTTP 客户端被拦那样冷却。
        """
        browser = self._browser_for(source)
        if browser is None:
            return None
        try:
            response = browser(request, timeout, max_bytes)
        except (browser_transport.BrowserUnavailable, browser_transport.ChallengeUnsolved) as error:
            LOGGER.info("%s 的 HTTP 客户端被拦，浏览器验证也没取到：%s", source, error)
            return None
        if response.status != 200 or _refused(response):
            LOGGER.info("%s 的 HTTP 客户端被拦，浏览器验证回 HTTP %s", source, response.status)
            return None
        fix_to_browser(self.root, source)
        replaced = self.transports.get(source)
        if isinstance(replaced, HttpxTransport):
            replaced.close()
        self.transports[source] = browser
        LOGGER.warning("%s 的 HTTP 客户端被拦，浏览器验证取到正常页；这个来源之后固定走本机浏览器", source)
        return response

    def _browser_for(self, source: str):
        """这个来源用的本机浏览器（进程共用）；本机没有可驱动的浏览器或代理带凭据时 None。"""
        values = values_for(self.root, source)
        direct = values.get("network") == "direct"
        options = {"trust_env": False} if direct else peach_proxy.client_options(self.root)
        proxy = options.get("proxy", "")
        direct = not options.get("trust_env", True) and not proxy
        gate = SOURCES[source].get("browser_gate")
        gates = {domain: gate for domain in SOURCES[source]["domains"]} if gate else {}
        return browser_transport.shared(browser_profile(self.root), direct=direct, proxy=proxy, gates=gates)

    def _transport_for(self, source: str | None):
        """这个来源的请求走哪条路：该走浏览器（`wants_browser`）且本机有浏览器就走浏览器页面，否则 httpx。"""
        if source and wants_browser(self.root, source):
            browser = self._browser_for(source)
            if browser is not None:
                return browser
        if source:
            client = client_for(self.root, source, session=bool(SOURCES[source].get("session")),
                                follow_redirects=False)
        else:
            client = httpx.Client(**peach_proxy.client_options(self.root), follow_redirects=False,
                                  headers={"User-Agent": USER_AGENT})
        return HttpxTransport(client, owns_client=True)

    def renew(self):
        self.close()

    def close(self):
        # 浏览器是整个进程共用的，空闲后自己关；这里只关自己开的连接池。
        for transport in self.transports.values():
            if isinstance(transport, HttpxTransport):
                transport.close()
        self.transports.clear()
