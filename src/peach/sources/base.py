"""站点解析器的统一契约：一个站一个类、取页与解析分开、按站配置进数据、一种返回模型、一张失败原因表。

ADR-0044 收敛刮削栈之后，自写解析器与 amane 桥的站都套这一个形状，`LibraryMetadataProvider`
链上拿到的永远是 `[(来源, SiteRecord.payload())]`，候选、来源链结算、封面层与账本一行不动。

四个部件各管一件事：

- `SiteConfig`：站名、主域与图床、请求间隔、是否带 Cookie、页面大小上限、所属档位。它是数据，
  `scraping_access.SOURCES`、`library_processing.SOURCE_INTERVALS` 这几张表与它逐项一致，由测试守住。
- `SiteSource`：`fetch(code, session=)` 取到作品页，`parse(page, code)` 从页面读出 `SiteRecord`，
  `query()` 把两步串起来，`records()` 交出这一站的全部记录（多数站就是 `query()` 那一条）。两者都包在
  `holding()` 里：`_fetch` 抛出的 HTTP 分档翻成 `SourceFailure`，要整站停下的细档写回来源冷却。
  测试只喂 `parse` 一张页面就能覆盖解析，不必起假传输。
- `SiteRecord`：一种返回模型。`payload()` 投影成来源快照那份 dict（`id`、`maker`、`actresses[].japanese_name`、
  `cover_urls`……），`extract_peach_fields`、`verified_cover` 与复核那一路认的就是它。
- `FailureReason`：一张失败原因表，取 amane 那十六档里 Peach 用得上的十二档。`REASON_KINDS` 把它映到
  `MetadataProviderError` 现有的 `auth` / `unavailable` / `not_found` 三档，`COOLDOWN_ACTIONS` 说哪几档要把
  整站停下。自写站与 amane 桥读同一张表、写同一份冷却记录：自写站经 `holding()` 交给会话传输链上的
  `SourceTransport.hold`，amane 桥经 `metadata_amane.cooldown_action` 交给 `scraping_access.pause_source`。

传输层自己的信号不经这张表：`SourcePaused`（冷却期）、`DeadlineExceeded`（动作预算）与
`httpx.TransportError`（连接未取得）由 `scraping_access` 与 `jav_cover_fetch._fetch` 抛出，`holding()`
原样放过，调用方的冷却写回、预算结束与重试语义因此保持不变。
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from .. import jav_cover_fetch
from ..jav_cover_fetch import NotFound, Unavailable
from ..metadata import MetadataProviderError, auth_error


class FailureReason(StrEnum):
    """站点没给出结果的细档。语义在成员名，措辞由抛错的那一处写。"""

    #: 撞上 Cloudflare 的挑战页或拦截页：同一个出口再发结果不变。
    CLOUDFLARE_CHALLENGE = "cloudflare_challenge"
    #: 出口 IP 已被站方封禁（javdb 按出口 IP 计配额、超了封 3～7 天）。
    IP_BANNED = "ip_banned"
    #: 站方按地区拒绝这个出口。
    GEO_RESTRICTED = "geo_restricted"
    #: 登录墙、年龄门或 401/403：换一份 Cookie 才能继续。
    AUTH_REQUIRED = "auth_required"
    #: 站上没有这个番号（HTTP 404 或搜索无命中）。
    NOT_FOUND = "not_found"
    #: 站上有过、现已下架（HTTP 410 或下架页）。
    GONE = "gone"
    #: 页面在，结构却对不上解析器：站点改版，重试无用，也不是这部片没有。
    PARSE_ERROR = "parse_error"
    #: 请求成功却没解析出可用资料。
    NO_USABLE_METADATA = "no_usable_metadata"
    #: HTTP 429 或站方明说的限流。
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    #: 5xx。
    SERVER_ERROR = "server_error"
    #: 连接层失败：DNS、TLS、连接被重置。
    NETWORK = "network"


#: 细档 → `MetadataProviderError.kind`。`auth` 一档收「站方把我们挡在门外」的几种：同一份请求再发
#: 一次结果不变，正是 `auth` 的语义（`retryable=False`、`temporary=True`）。`parse_error` 归 `unavailable`
#: 但不可重试：不是这部片没有，也不是等一会儿就好。
REASON_KINDS: dict[FailureReason, str] = {
    FailureReason.CLOUDFLARE_CHALLENGE: "auth",
    FailureReason.IP_BANNED: "auth",
    FailureReason.GEO_RESTRICTED: "auth",
    FailureReason.AUTH_REQUIRED: "auth",
    FailureReason.NOT_FOUND: "not_found",
    FailureReason.GONE: "not_found",
    FailureReason.NO_USABLE_METADATA: "not_found",
    FailureReason.PARSE_ERROR: "unavailable",
    FailureReason.RATE_LIMITED: "unavailable",
    FailureReason.TIMEOUT: "unavailable",
    FailureReason.SERVER_ERROR: "unavailable",
    FailureReason.NETWORK: "unavailable",
}
#: 这几档说明的是「这个出口对这一站发得太多、已被封或不在站方的服务地区」，整站要进冷却，不只是这一部片。
#: 用的是 `scraping_access.pause_source` 现成的两档：`blocked` 按 403 那一档翻倍，`rate_limited` 按 429 那一档。
#: 地区限制与封禁同档：只收日本出口的站（Prestige、DMM）换一部片问结果不变，换出口才会变。
COOLDOWN_ACTIONS: dict[FailureReason, str] = {
    FailureReason.CLOUDFLARE_CHALLENGE: "blocked",
    FailureReason.IP_BANNED: "blocked",
    FailureReason.GEO_RESTRICTED: "blocked",
    FailureReason.RATE_LIMITED: "rate_limited",
}
#: 重试没有意义的细档。
PERMANENT_REASONS = frozenset({FailureReason.PARSE_ERROR})

#: Cloudflare 的验证页与拦截页的标题：JS 挑战与托管挑战顶着 `Just a moment...`（中文界面 `请稍候…`），
#: 按规则拦下的是 `Attention Required! | Cloudflare`。正文是给浏览器跑的脚本，站方回的状态码可能是
#: 403，也可能是 200（AVBase）。只认标题：Cloudflare 往正常页里也注入 `challenge-platform` 脚本，按它认会误判。
CHALLENGE_TITLE = re.compile(
    r"<title[^>]*>\s*(?:Just a moment\.\.\.|请稍候…|Attention Required! \| Cloudflare)\s*</title>")


def challenge_page(html: str | bytes) -> bool:
    """这一页是不是 Cloudflare 的验证页。站上有没有这部片，这一页说不了。"""
    text = html.decode("utf-8", "replace") if isinstance(html, bytes) else str(html)
    return bool(CHALLENGE_TITLE.search(text[:4096]))

#: 作品页的默认大小上限。javdb 详情页实测 90 KB、JavBus 作品页百余 KB，4 MiB 留的是改版余量。
DEFAULT_PAGE_LIMIT = 4 * 1024 * 1024
#: 主机间隔的默认值，与 `LibraryMetadataProvider` 给 `HostLimitedTransport` 的默认一致。
DEFAULT_INTERVAL = 2.0


class SourceFailure(RuntimeError):
    """一站没给出结果：`reason` 是细档，`message` 是给人看的那一句。

    `detail` 默认就是细档名；amane 桥把上游原样的 reason 放在这里，冷却与快照据此回溯。
    """

    def __init__(self, reason: FailureReason | str, message: str, *,
                 status_code: int = 0, detail: str = "") -> None:
        super().__init__(message)
        self.reason = FailureReason(reason)
        self.message = message
        self.status_code = status_code
        self.detail = detail or self.reason.value
        #: 冷却已经记过：嵌套的 `holding()`（`r18dev.query` 包着基类的 `query`）不再记第二次。
        self.held = False

    @property
    def kind(self) -> str:
        return REASON_KINDS[self.reason]

    @property
    def cooldown_action(self) -> str:
        return COOLDOWN_ACTIONS.get(self.reason, "")

    def provider_error(self, label: str) -> MetadataProviderError:
        """翻成 `MetadataProviderError`。`auth` 的措辞只有 `auth_error` 一处，其余两档措辞由抛错处给。"""
        if self.kind == "auth":
            return auth_error(label, self.message, status_code=self.status_code, detail=self.detail)
        if self.kind == "not_found":
            return MetadataProviderError(self.message, kind="not_found", status_code=self.status_code,
                                         detail=self.detail)
        transient = self.reason not in PERMANENT_REASONS
        return MetadataProviderError(self.message, kind="unavailable", status_code=self.status_code,
                                     detail=self.detail, retryable=transient, temporary=transient)


def http_failure(error: Unavailable) -> SourceFailure:
    """`_fetch` 抛出的 HTTP 分档（`HTTP 403`、`HTTP 429`、`HTTP 503`）→ 细档。措辞保留原文。"""
    text = str(error)
    if isinstance(error, NotFound):
        return SourceFailure(FailureReason.NOT_FOUND, text, status_code=404 if text == "HTTP 404" else 0)
    status = int(text[5:]) if text.startswith("HTTP ") and text[5:].isdigit() else 0
    if status in (401, 403):
        reason = FailureReason.AUTH_REQUIRED
    elif status == 429:
        reason = FailureReason.RATE_LIMITED
    elif status == 410:
        reason = FailureReason.GONE
    elif status >= 500:
        reason = FailureReason.SERVER_ERROR
    else:
        reason = FailureReason.NETWORK
    return SourceFailure(reason, text, status_code=status)


@dataclass(frozen=True)
class SiteConfig:
    """一站的配置。全是数据，站的类不带常量。"""

    #: 来源名，与 `metadata_policy.SOURCE_SPECS`、`scraping_access.SOURCES` 同键。
    name: str
    #: 措辞里的站名（`library_processing.SOURCE_LABELS`）。
    label: str
    #: 解析器名，写进快照与候选的 `provider`（`library_processing.PROVIDER_NAMES`）。
    provider: str
    #: 作品页所在的主域，请求的 Referer 也取它。
    base_url: str
    #: 主域、镜像与图床的域名后缀，`scraping_access.SOURCES[name]['domains']` 与它一致。
    domains: tuple[str, ...]
    #: 链上的档位，与 `metadata_policy.SOURCE_SPECS[name].kind` 同值：`official` / `official_mirror` /
    #: `community` / `amane`。
    stage: str
    #: 同主机两次请求的最短间隔（秒），`library_processing.SOURCE_INTERVALS` 里这一站的主机与它一致。
    interval: float = DEFAULT_INTERVAL
    #: 请求要不要带用户在采集设置里贴的 Cookie。
    cookie: bool = False
    #: 作品页大小上限。
    page_limit: int = DEFAULT_PAGE_LIMIT

    @property
    def referer(self) -> str:
        return self.base_url + "/"


@dataclass(frozen=True)
class Page:
    """取回来的一张页面。`url` 是最终地址，解析器把它写进 `source_url`。"""

    url: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


@dataclass(frozen=True)
class Session:
    """一次查询的传输与预算：`transport` 是按来源带 Cookie、算冷却的那一个，`deadline` 是动作预算。"""

    transport: Any
    deadline: float | None = None

    def get(self, url: str, *, config: SiteConfig, referer: str = "",
            headers: Mapping[str, str] | None = None) -> Page:
        """取一页。`referer` 默认是站的主域；`headers` 是这一次要多带的请求头（fc2cmadb 点名要女优那一栏）。"""
        options = {"extra_headers": dict(headers)} if headers else {}
        return Page(url, jav_cover_fetch._fetch(self.transport, url, referer=referer or config.referer,
                                                limit=config.page_limit, deadline=self.deadline, **options))

    def post(self, url: str, *, config: SiteConfig, body: bytes, referer: str = "",
             headers: Mapping[str, str] | None = None) -> Page:
        """POST 一份请求体，交回响应页。只收 POST 的接口（DMM 的 GraphQL）走这里；重试、预算与失败分档同 `get`。"""
        options = {"extra_headers": dict(headers)} if headers else {}
        return Page(url, jav_cover_fetch._fetch(self.transport, url, referer=referer or config.referer,
                                                limit=config.page_limit, deadline=self.deadline,
                                                method="POST", body=body, **options))


@dataclass(frozen=True)
class SiteRecord:
    """一站给出的一部作品。字段是账本真相字段的直接投影，站上没有的留空。"""

    #: 来源名与解析器名，与 `SiteConfig.name` / `.provider` 相同。
    source: str
    provenance: str
    #: 站上读回的番号写法，`identifies_code` 拿它核身份；不是问的那个。
    code: str
    source_url: str
    title: str = ""
    #: 出演女优，每人一份 `{japanese_name, profile_source?, external_id?}`。
    performers: tuple[Mapping[str, str], ...] = ()
    studio: str = ""
    label: str = ""
    series: str = ""
    director: str = ""
    release_date: str = ""
    #: 分钟。站上按秒或 `41:50` 记的，换算后可能带两位小数。
    runtime: float | None = None
    #: `None` 表示这一站不给标签；空元组表示给了但为空。
    tags: tuple[str, ...] | None = None
    cover_urls: tuple[str, ...] = ()
    confidence: float = 1.0
    #: 只有这一站才有的键（amane 的 `poster_url`、`raw`……），原样并进 `payload()`。
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def cover_url(self) -> str:
        return self.cover_urls[0] if self.cover_urls else ""

    def payload(self) -> dict:
        """来源快照那份 dict：键名沿用 `maker`、`actresses`、`genres` 的写法，候选与复核那一路认的就是它。"""
        payload: dict[str, Any] = dict(
            id=self.code, source_url=self.source_url, title=self.title,
            actresses=[dict(row) for row in self.performers],
            maker=self.studio, label=self.label, series=self.series, director=self.director,
            release_date=self.release_date, runtime=self.runtime,
            cover_urls=list(self.cover_urls), cover_url=self.cover_url)
        if self.tags is not None:
            payload["genres"] = list(self.tags)
        payload.update(self.extra)
        return payload


class SiteSource:
    """一站一个子类：`fetch` 取到作品页，`parse` 读出记录，`query` 串起来。

    配置经构造函数注入，默认取子类的 `DEFAULT`；测试要换域名或上限时传一份别的进来。
    """

    DEFAULT: SiteConfig

    def __init__(self, config: SiteConfig | None = None) -> None:
        self.config = config or type(self).DEFAULT

    def fetch(self, code: str, *, session: Session) -> Page:
        """取到这个番号的作品页。要先搜索的站在这里搜；找不到抛 `SourceFailure(NOT_FOUND)`。"""
        raise NotImplementedError

    def parse(self, page: Page, code: str) -> SiteRecord:
        """从作品页读出记录。页面结构对不上抛 `SourceFailure(PARSE_ERROR)`，门页抛 `AUTH_REQUIRED`。"""
        raise NotImplementedError

    @contextmanager
    def holding(self, session: Session):
        """包住一次查询：`_fetch` 的 HTTP 分档翻成 `SourceFailure`，要整站停下的细档写回来源冷却。

        `COOLDOWN_ACTIONS` 里的细档（验证页、封禁、地区限制、限流）说的是「这个出口对这一站进不去」，
        同一轮里换一部片问结果不变，所以整站进冷却，之后的请求在 `SourceTransport` 那一关就停下，不再
        一部片撞一次。冷却记录由会话传输链上第一个有 `hold` 的那一层写（`HostLimitedTransport.inner`
        一路往里找，找到的是 `scraping_access.SourceTransport`）；链上没有它（Seesaa 的 `WikiPages`、
        测试里的假传输）就不记。`query()` 与 `records()` 覆写时也包在这里面。
        """
        try:
            yield
        except SourceFailure as failure:
            _hold(session, self.config.name, failure)
            raise
        except Unavailable as error:
            failure = http_failure(error)
            _hold(session, self.config.name, failure)
            raise failure from None

    def query(self, code: str, *, session: Session) -> SiteRecord:
        with self.holding(session):
            return self.parse(self.fetch(code, session=session), code)

    def records(self, code: str, *, session: Session) -> list[SiteRecord]:
        """这一站对这个番号给出的全部记录。多数站只有一条；同一部作品在站上有几条各自独立的页面时
        （JavArchive 上几位转存者各发一次，图各存各的）子类逐条交出，封面层要的是每一条的图源。"""
        return [self.query(code, session=session)]


def _hold(session: Session, source: str, failure: SourceFailure) -> None:
    """把 `failure` 的冷却档交给会话传输链上的 `SourceTransport.hold`；一个失败只记一次。"""
    if not failure.cooldown_action or failure.held:
        return
    failure.held = True
    transport = session.transport
    while transport is not None and not callable(getattr(transport, "hold", None)):
        transport = getattr(transport, "inner", None)
    if transport is not None:
        transport.hold(source, failure.cooldown_action)
