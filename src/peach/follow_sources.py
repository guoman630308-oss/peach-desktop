"""站点专用追更连接器。

每个连接器只做发现：把一次显式的、有界的抓取翻译成 `FollowCandidate` 序列。
它们不写 ledger、不下载媒体、不在服务启动或普通浏览时联网，也不绕过任何机器人验证。
凭据由 `follow_secrets.CredentialStore` 提供，只进请求头或 POST 体，绝不进 URL。

站点结构证据登记在 `docs/reference-sources.json`。
"""
from __future__ import annotations

import base64
import html
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping

import httpx
from bs4 import BeautifulSoup

from .fanbox import FanboxContentError, normalize_fanbox_post
from . import follow_providers
from .follow import (
    DEFAULT_MAX_BYTES, FollowHistoryEnd, FollowSourceError, FollowSourceRateLimited,
    plain_text, stable_id,
)
from .follow_secrets import Credential, CredentialError
from .follow_gofile import GofileExpander, folder_labels
from .follow_image_dims import positive_dims
from .http import CurlCffiTransport, HttpRequest, HttpResponse, HttpTransport, HttpxTransport
from .mp4index import movie_seconds


#: 默认连接器共用的 UA。只有 ADR-0019 明确登记的 FANBOX 详情传输例外。
from .user_agent import USER_AGENT

#: 单次抓取的条目上限。追更只关心增量，不做全站归档。
DEFAULT_MAX_ITEMS = 100

#: 网盘与文件分发站。**判「这一帖有没有交付资源」只认这张表，不认「有外链」**——
#: 投票链接（`forms.gle`）、社交主页、打赏页都是外链，把它们算成资源会让每一帖都算
#: release，过滤就等于没做。
#:
#: 这张表注定不全，作者换网盘就得往里加。加的时候只加**分发文件**的域名：
#: 判据是点进去拿到的是文件，不是一个页面。
_FILE_HOST_DOMAINS = frozenset({
    "downloads.fanbox.cc",
    "gofile.io", "mega.nz", "mega.io", "mediafire.com", "pixeldrain.com",
    "workupload.com", "catbox.moe", "1fichier.com", "dropbox.com",
    "drive.google.com", "docs.google.com", "1drv.ms", "onedrive.live.com",
    "sendspace.com", "bunkr.site", "bunkrr.su", "cyberdrop.me",
    "krakenfiles.com", "buzzheavier.com", "saint2.su", "swisstransfer.com",
    "wetransfer.com", "we.tl", "ufile.io", "zippyshare.com", "anonfiles.com",
    "qiwi.gg", "mixdrop.co", "gigafile.nu", "xgf.nu", "firestorage.jp",
    "terabox.com", "1024terabox.com", "pan.baidu.com", "123pan.com",
    "aliyundrive.com", "alipan.com", "pan.quark.cn", "lanzou.com",
    "lanzoui.com", "lanzoux.com", "yadi.sk", "disk.yandex.ru",
    "disk.yandex.com", "fileditch.com",
})

_LINK_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
#: URL 看起来指向一张图片。不只 f95 在用：归档站推导旧行缩略图时也要这个判据。
_IMAGE_URL_RE = re.compile(
    r"\.(?:avif|bmp|gif|jpe?g|png|webp)(?:$|[?#])", re.IGNORECASE)


def resource_links(text: str | None) -> list[str]:
    """文本里指向网盘 / 文件站的链接。

    只按域名判，不按「看起来像下载」判。匹配到注册域边界：`gofile.io` 命中
    `https://gofile.io/d/x` 和 `https://www.gofile.io/d/x`，但**不**命中
    `https://gofile.io.evil.example/`——那个的注册域根本不是 gofile。
    """
    found = []
    for link in _LINK_RE.findall(text or ""):
        if _is_resource_url(link) and link not in found:
            found.append(link)
    return found


# 用户逐张确认的讨论表情附件；按附件身份判断，不按 GIF 格式排除作品。
_F95_DISCUSSION_ATTACHMENTS = frozenset({
    "6453006_IMG_2124.jpeg", "6456143_attachment-3.gif",
})


def f95_discussion_image(value: object) -> bool:
    """已确认的 F95 讨论插图不参与封面和媒体投影。"""
    try:
        parsed = urllib.parse.urlsplit(str(value or ""))
    except ValueError:
        return False
    return (parsed.hostname == "attachments.f95zone.to"
            and urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
            in _F95_DISCUSSION_ATTACHMENTS)


def f95_attachment_media_items(metadata: Mapping[str, object]) -> list[dict[str, object]]:
    """Project F95 image attachments, including rows saved before galleries existed."""
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list):
        return []
    result: list[dict[str, object]] = []
    for index, value in enumerate(attachments):
        url = str(value or "")
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            continue
        if (f95_discussion_image(url) or parsed.scheme != "https" or parsed.hostname != "attachments.f95zone.to"
                or not _IMAGE_URL_RE.search(url)):
            continue
        name = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1]) or f"image-{index + 1}"
        result.append({
            "id": f"f95-attachment-{index + 1}",
            "name": name,
            "media_kind": "image",
            "url": url,
            "thumb_url": url,
            "resource_provider": "f95zone",
        })
    return result


def _is_resource_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return parsed.scheme == "https" and any(
        host == domain or host.endswith("." + domain)
        for domain in _FILE_HOST_DOMAINS
    )


@dataclass(frozen=True)
class FollowCandidate:
    """一个未经复核的追更候选。"""

    provider: str
    external_id: str
    title: str
    url: str | None = None
    media_url: str | None = None
    thumb_url: str | None = None
    published_at: str | None = None
    version: str | None = None
    duration: float | None = None
    author: str | None = None
    summary: str | None = None
    #: 来源自带的分组标识。可能是站内的父帖，也可能是归一化后的跨站出处键
    #: （`fanbox:12304831` 这种）。有它就不必靠标题猜同一作品的变体。
    group_hint: str | None = None
    #: 标题是不是一个真正的名字。booru 没有标题，只能拿标签凑一个可读标签——
    #: 那种「标题」不能参与按标题分组，否则同一作者的两个作品会因标签相似被并掉。
    title_is_name: bool = True
    extra: Mapping[str, object] = field(default_factory=dict)
    #: 这一条只有列表阶段的视图，详情没有取（额度用完、库里已经补齐过，或者详情页
    #: 这次被挡回来）。落库时它**不得覆盖**已有的细节：详情给出的上传时间、时长、
    #: 媒体地址和 metadata 要原样留着，只更新列表本来就权威的那几列。
    partial: bool = False


@dataclass(frozen=True)
class SourceFetch:
    provider: str
    ref: str
    request_url: str
    semantics: str
    candidates: tuple[FollowCandidate, ...] = ()
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    #: 这次抓取里被判为「不是 release」而丢掉的条数。必须报出来：用户看到的条目数
    #: 比站点上少的时候，他得能分清少的是被过滤掉的，还是根本没抓到。
    skipped: int = 0
    #: `skipped` 里有多少条是来源详情确认的跨作者合集。单列出来让界面能说清
    #: 「主动不收」而不是把它误报成「没有资源」。
    skipped_compilations: int = 0
    #: 列表判不出来、额外抓了详情页的条数。这是唯一会放大请求数的路径，
    #: 报出来才能看出某个作者是不是每一帖都要多打一次站点。
    probed: int = 0
    raw_body: bytes | None = field(default=None, repr=False, compare=False)


def published_stamp(candidate: FollowCandidate) -> datetime | None:
    """候选的发布时间；没有或解析不了就是 `None`。"""
    if not candidate.published_at:
        return None
    try:
        stamp = datetime.fromisoformat(str(candidate.published_at).replace('Z', '+00:00'))
    except (TypeError, ValueError, OverflowError):
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def within_history(candidate: FollowCandidate, after: datetime | None) -> bool:
    """有限历史范围只排除日期明确且早于边界的条目。"""
    stamp = published_stamp(candidate) if after is not None else None
    return stamp is None or stamp >= after


def history_window(candidates, after: datetime | None, floor: int = 0) -> tuple:
    """有限历史范围内的条目；不足 `floor` 条时按列表顺序补入更早的，补满为止。

    列表是新的在前，所以补进来的是边界之外最近的那几条。
    """
    within = sum(1 for candidate in candidates if within_history(candidate, after))
    spare = max(0, floor - within)
    kept = []
    for candidate in candidates:
        if within_history(candidate, after):
            kept.append(candidate)
        elif spare:
            kept.append(candidate)
            spare -= 1
    return tuple(kept)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_from_epoch(value) -> str | None:
    try:
        return _iso_utc(datetime.fromtimestamp(int(value), tz=timezone.utc))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _dims_extra(width, height) -> dict[str, int]:
    """来源接口报的宽高进 extra 前先归一：不是一对正整数就一个键也不写。"""
    dims = positive_dims(width, height)
    return {"width": dims[0], "height": dims[1]} if dims else {}


def _iso_from_text(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return _iso_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


_RELATIVE_RE = re.compile(
    r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*(?:ago)?", re.IGNORECASE)

#: 相对时间换算成秒。月按 30 天、年按 365 天——只用来排序，不当精确发布时间用。
_RELATIVE_SECONDS = {
    "second": 1, "minute": 60, "hour": 3600, "day": 86400,
    "week": 604800, "month": 2592000, "year": 31536000,
}


def _iso_from_relative(text: str | None, *, now: datetime | None = None) -> str | None:
    """把 `1 week ago` 这类相对时间换算成近似 UTC 时间戳。

    rule34video 的列表页只给相对时间，没有绝对时间。换算结果**是近似值**，
    调用方必须在 `extra` 里标出 `published_precision='approximate'`，
    界面上也要照实显示，不能当成站点给出的精确发布时间。
    """
    if not text:
        return None
    matched = _RELATIVE_RE.search(text)
    if not matched:
        return None
    amount = int(matched.group(1))
    unit = _RELATIVE_SECONDS[matched.group(2).lower()]
    reference = now or datetime.now(timezone.utc)
    return _iso_utc(reference - timedelta(seconds=amount * unit))


_DURATION_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$")
_ISO_DURATION_RE = re.compile(
    r"^PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$",
    re.IGNORECASE,
)


def _duration_seconds(text: str | None) -> float | None:
    if not text:
        return None
    matched = _DURATION_RE.match(text)
    if not matched:
        return None
    hours, minutes, seconds = matched.groups()
    return float(int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds))


def _iso_duration_seconds(text) -> float | None:
    """Schema.org `PT0H27M45S` 时长转秒。"""
    if not isinstance(text, str):
        return None
    matched = _ISO_DURATION_RE.match(text.strip())
    if not matched or not any(matched.groups()):
        return None
    hours, minutes, seconds = (float(value or 0) for value in matched.groups())
    return hours * 3600 + minutes * 60 + seconds


#: 站点自报出处时能认出来的原始平台。键是主机名后缀，值是分组键的前缀。
#: 这些前缀是**跨站**的：rule34.xxx 的 `source` 指向某个 fanbox 帖时，产生的键和
#: kemono 上同一个帖子的键完全相同，跨站重复因此不必靠标题去猜。
_ORIGIN_HOSTS: tuple[tuple[str, str], ...] = (
    ("fanbox.cc", "fanbox"),
    ("patreon.com", "patreon"),
    ("pixiv.net", "pixiv"),
    ("x.com", "x"),
    ("twitter.com", "x"),
    ("subscribestar.adult", "subscribestar"),
    ("gumroad.com", "gumroad"),
)

_ORIGIN_ID_RE = re.compile(r"/(?:posts?|status(?:es)?|artworks)/(\d{4,})")


def origin_group_key(source_url: str | None) -> str | None:
    """把站点自报的出处 URL 归一成跨站可比的分组键。

    实测（2026-08-25）同一个 fanbox 帖在 rule34.xxx 上有两种写法——
    `lazyprocrast.fanbox.cc/posts/12304831` 和
    `www.fanbox.cc/@lazyprocrast/posts/12304831`——必须归一到同一个键，
    而那串数字正是 kemono 上同一帖子的 post id。出处常常省掉协议头
    （`x.com/vileclipse/status/…`），缺了就按 https 补上再解析。
    """
    if not source_url or not isinstance(source_url, str):
        return None
    text = source_url.strip()
    if "://" not in text and not text.startswith("//"):
        text = "https://" + text.lstrip("/")
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    platform = next(
        (name for suffix, name in _ORIGIN_HOSTS
         if host == suffix or host.endswith("." + suffix)),
        None,
    )
    if platform is None:
        return None
    matched = _ORIGIN_ID_RE.search(parsed.path)
    return f"{platform}:{matched.group(1)}" if matched else None


def _is_opaque_filename(stem: str) -> bool:
    """文件名是不是哈希。

    rule34.xxx 实测 15/15 的 `image` 都是 32 位十六进制哈希——拿它当标题既不可读，
    又会让每条帖子各自成组，跨站去重永远不可能命中。
    """
    return bool(re.fullmatch(r"[0-9a-f]{16,}", stem.strip().lower()))


def _exc_summary(exc: Exception, limit: int = 140) -> str:
    """把底层网络异常压成一行能看懂的原因，跟着「请求失败」一起报给用户。"""
    text = f"{type(exc).__name__}: {exc}".strip()
    return re.sub(r"\s+", " ", text)[:limit]


@dataclass(frozen=True)
class ParsedSource:
    """从一条粘进来的链接认出来的订阅。"""

    provider: str
    ref: str
    url: str
    label: str

    @property
    def semantics(self) -> str:
        """`work` 是每条一个独立作品，`release` 是同一作品的历次发布；由登记表决定。"""
        return follow_providers.PROVIDERS[self.provider].semantics

    @property
    def evidence(self) -> str:
        return "链接直接指明"


#: 站点主机 → 来源键。粘进来的链接靠它认出属于哪个站，不再是一串 if/elif。
_URL_HOSTS = follow_providers.url_hosts()

_KEMONO_PATH_RE = re.compile(r"^/([a-z0-9_\-]{1,32})/user/([A-Za-z0-9_\-.]{1,64})")
_R34V_PATH_RE = re.compile(r"^/models/([a-z0-9][a-z0-9_\-]{0,80})")
_THREAD_PATH_RE = re.compile(r"^/threads/(?:[^/]*?\.)?(\d{1,12})")
_DIRECT_CREATOR_RE = re.compile(r"^/([A-Za-z0-9_-]{1,80})(?:/|$)")
_FANBOX_AT_RE = re.compile(r"^/@([A-Za-z0-9_-]{1,64})(?:/|$)")
_PAHEAL_LIST_RE = re.compile(r"^/post/list/([^/]+)/\d+$")


def canonical_source_ref(provider: str, ref: str) -> str:
    """Return the provider's stable source identity.

    Most provider ids are case-sensitive opaque values.  rule34.xxx tags are not:
    the API returns the same feed for ``LazyProcrastinator`` and
    ``lazyprocrastinator``.  Keeping the pasted spelling in the unique key therefore
    creates duplicate subscriptions when the same author is added in two batches.
    """
    value = str(ref or "").strip()
    return value.casefold() if provider in {"rule34xxx", "rule34paheal"} else value


#: 线程 slug 里从这个 token 起就不是作品名了：f95 的惯例是
#: `<作品名>-<发布日期>-<作者手柄>`，日期之后全是元数据。
_SLUG_TAIL_RE = re.compile(r"^(?:19|20)\d{2}$|^v?\d+(?:\.\d+)+[a-z]?$")


def _slug_label(slug: str) -> str:
    words = [word for word in re.split(r"[-_]+", slug) if word]
    kept: list[str] = []
    for word in words:
        if kept and _SLUG_TAIL_RE.match(word):
            break
        kept.append(word)
    return " ".join(kept).strip() or re.sub(r"[-_]+", " ", slug).strip() or slug


class _BaseConnector:
    provider = ""

    @property
    def semantics(self) -> str:
        """条目语义来自登记表；没登记的（只有测试里的探针）按 work。"""
        spec = follow_providers.PROVIDERS.get(self.provider)
        return spec.semantics if spec else "work"
    #: 站点被机器人验证挡住时的说明；非空表示该连接器不可用。
    blocked_reason = ""
    #: 往回翻页时代表「没有更早的了」的上游状态码。声明在类上而不是每次调用
    #: `_request` 时传参，因为 `is_history_end_error` 要用同一份声明去认旧行。
    HISTORY_END_STATUSES: tuple[int, ...] = ()

    #: 一次抓取最多为补全细节额外打几次详情页。0 表示这个站没有第二阶段。
    #: 列表页一次请求给一整页，详情页是每条一次——它是唯一会让请求数随条目数增长
    #: 的路径，所以必须有额度，不能由页面长度决定。
    DEFAULT_ENRICH_BUDGET = 0
    #: 详情页问过之后，ledger 行上哪一处会有值。空串 = 不打详情页。第二阶段补细节
    #: （`DEFAULT_ENRICH_BUDGET`）与列表阶段的逐条探测（`DEFAULT_MAX_PROBES`）都按它跳过。
    #: 调用方拿它算「这条不必再问详情」，判据本身登记在 `follow_store`。
    #: **不能一律用 `published_at`**：rule34xxx 的上传时间来自列表的 `change`，
    #: 第一次落库就有值，拿它当判据会让详情失败的行永远补不回来。
    ENRICHED_MARK = ""

    def __init__(self, *, timeout: float = 15.0, max_bytes: int = DEFAULT_MAX_BYTES,
                 max_items: int = DEFAULT_MAX_ITEMS,
                 transport: HttpTransport | None = None,
                 credential: Credential | None = None,
                 gofile_credential: Credential | None = None,
                 enrich_skip: Iterable[str] = (),
                 enrich_budget: int | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_items = max_items
        self.transport = transport or HttpxTransport()
        self.credential = credential
        self.gofile_credential = gofile_credential
        #: 细节已经补齐过的站内 id，第二阶段直接跳过。由调用方按 ledger 现状算出。
        self.enrich_skip = frozenset(str(value) for value in enrich_skip)
        self.enrich_budget = (self.DEFAULT_ENRICH_BUDGET if enrich_budget is None
                              else max(0, int(enrich_budget)))
        #: 退避时怎么等。默认真的睡；测试注入一个记账用的假实现，好让退避节奏
        #: 可断言又不真的把测试拖成几十秒。
        self.sleeper = sleeper
        self.progress = None
        self.candidate_ready = None
        self.history_after = None
        self.history_skipped = 0
        #: 首次采集的条数下限。边界之外最多放行这么多条，精确裁剪由 `run_check`
        #: 按 `history_window` 做：连接器逐条判断时还不知道后面有几条落在边界内。
        self.history_floor = 0
        self._history_spares: set[str] = set()

    def within_history(self, candidate: FollowCandidate) -> bool:
        if within_history(candidate, self.history_after):
            return True
        key = str(candidate.external_id)
        if key in self._history_spares or len(self._history_spares) < self.history_floor:
            # 同一条可能先在列表阶段、再在补全阶段各问一次，按 id 记住才不重复占名额。
            self._history_spares.add(key)
            return True
        self.history_skipped += 1
        return False

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT}

    def _send(self, method: str, url: str, body: bytes | None, *,
              headers: Mapping[str, str], base: Mapping[str, str]) -> HttpResponse:
        """发一次请求。`_get`/`_post` 的共同那半都在这里。

        拦下被拦的站、合并头、把网络异常压成一句话、卡住响应大小——这四件事
        与方法无关，所以只有这一份。GET 与 POST 各写一份必然分岔，而
        `connector_headers=False` 承载的“跳站不带来源站 Cookie”是安全语义，不应该
        取决于用的是哪个动词。
        """
        if self.blocked_reason:
            raise FollowSourceError(self.blocked_reason)
        merged = dict(base)
        merged.update(headers)
        delays = (0, 1, 2, 4, 8) if method == "GET" else (0,)
        for attempt, delay in enumerate(delays, 1):
            if self.progress:
                self.progress(attempt=attempt, max_attempts=len(delays), retry_in=delay)
            if delay:
                self.sleeper(delay)
                if self.progress:
                    self.progress(attempt=attempt, max_attempts=len(delays), retry_in=0)
            try:
                response = self.transport(HttpRequest(method, url, merged, body),
                                          self.timeout, self.max_bytes)
            except (OSError, httpx.TransportError) as exc:
                if attempt < len(delays):
                    continue
                raise FollowSourceError(
                    f"{self.provider} 请求失败（已尝试 {attempt} 次）：{_exc_summary(exc)}") from exc
            except httpx.HTTPError as exc:
                raise FollowSourceError(
                    f"{self.provider} 请求失败：{_exc_summary(exc)}") from exc
            if response.status not in {408, 500, 502, 503, 504} or attempt == len(delays):
                break
        if len(response.body) > self.max_bytes:
            raise FollowSourceError(f"{self.provider} 响应超出大小上限")
        return response

    def _get(self, url: str, *, headers: Mapping[str, str] | None = None,
             etag: str | None = None, last_modified: str | None = None,
             connector_headers: bool = True) -> HttpResponse:
        conditional = dict(headers or {})
        if etag:
            conditional["If-None-Match"] = etag
        if last_modified:
            conditional["If-Modified-Since"] = last_modified
        # 跨站资源 API 不能继承来源站的 Cookie。Gofile 只拿自己的 Bearer token；
        # FANBOX/F95 的会话不得跟着资源链接发到第三方主机。
        base = self._headers() if connector_headers else {"User-Agent": USER_AGENT}
        return self._send("GET", url, None, headers=conditional, base=base)

    def _post(self, url: str, body: bytes, *,
              headers: Mapping[str, str] | None = None,
              connector_headers: bool = True) -> HttpResponse:
        base = self._headers() if connector_headers else {"User-Agent": USER_AGENT}
        return self._send("POST", url, body, headers=headers or {}, base=base)

    @staticmethod
    def _conditional(response: HttpResponse) -> dict[str, str | None]:
        headers = {key.lower(): value for key, value in response.headers.items()}
        return {"etag": headers.get("etag"),
                "last_modified": headers.get("last-modified")}

    def _check_status(self, response: HttpResponse) -> None:
        if response.status in (401, 403):
            raise FollowSourceError(
                f"{self.provider} 拒绝访问（HTTP {response.status}）：需要有效凭据，"
                "或站点已加机器人验证")
        self._raise_if_rate_limited(response)
        if response.status != 200:
            raise FollowSourceError(f"{self.provider} 返回 HTTP {response.status}")

    def _raise_if_rate_limited(self, response: HttpResponse) -> None:
        """HTTP 429 抛 `FollowSourceRateLimited`。

        逐条探测详情页时也要过这一关：探测吞掉失败是为了不因一条取不到就丢更新，
        被限流却不同，接着探剩下的只会让整站被封得更久，要让检查停下、整站冷却。
        """
        if response.status != 429:
            return
        headers = {key.lower(): value for key, value in response.headers.items()}
        try:
            retry_after = float(headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        raise FollowSourceRateLimited(
            f"{self.provider} 返回 HTTP 429：请求过于频繁，稍后再试", retry_after)

    #: 上游限流页的固定句式（2026-08-29 实测 rule34.xxx）：HTTP 200 + 文本正文
    #: "You currently have a limit of 60 requests every 60 second(s)"。不识别的话
    #  会被报成「请求失败/不是合法 JSON」，用户看不出是被限流了。
    _RATE_LIMIT_RE = re.compile(
        r"limit of (\d+) requests? every (\d+) seconds?", re.IGNORECASE)

    def _upstream_reason(self, response: HttpResponse) -> FollowSourceError | None:
        """能从响应正文里读出的明确失败原因；读不出就返回 None。"""
        text = response.body.decode("utf-8", errors="replace")
        matched = self._RATE_LIMIT_RE.search(text)
        if matched:
            count, seconds = matched.group(1), matched.group(2)
            return FollowSourceRateLimited(
                f"{self.provider} 触发频率限制：每 {seconds} 秒最多 {count} 次请求，请稍后再试",
                float(seconds))
        return None

    @staticmethod
    def _body_snippet(response: HttpResponse, limit: int = 120) -> str:
        text = re.sub(r"<[^>]+>", " ", response.body.decode("utf-8", errors="replace"))
        return re.sub(r"\s+", " ", text).strip()[:limit]

    def parse_json(self, response: HttpResponse):
        """解析一份已经取回的响应；不是合法 JSON 就抛错。"""
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            reason = self._upstream_reason(response)
            if reason:
                raise reason from exc
            snippet = self._body_snippet(response)
            detail = f"：{snippet}" if snippet else ""
            raise FollowSourceError(
                f"{self.provider} 返回的不是合法 JSON{detail}") from exc

    def _request(self, url: str, *, ref: str, etag: str | None = None,
                 last_modified: str | None = None, page: int = 0,
                 headers: Mapping[str, str] | None = None,
                 request_url: str | None = None,
                 ) -> tuple[dict[str, object], HttpResponse | None]:
        """每个连接器 fetch 开头都一样的那段：条件请求 → 304 短路 → 状态检查。

        返回 `(common, response)`。`response` 为 None 表示站点回了 304，调用方直接
        `return SourceFetch(not_modified=True, **common)`，不用再自己拼 common。

        往回翻页时不带条件请求头：`If-None-Match` 存的是第一页的 etag，拿它去问第二页
        很可能换回 304，表现就是「点了没反应」。这条规则过去在每个连接器里各写一遍，
        现在只有这里一处。

        `request_url` 是落进证据的那个 URL。带凭据的真实请求 URL 绝不能落盘——
        rule34xxx 的 `api_key` 在查询串里，必须传一个脱敏版本进来。
        """
        response = (self._get(url, headers=headers) if page
                    else self._get(url, headers=headers,
                                   etag=etag, last_modified=last_modified))
        common: dict[str, object] = {
            "provider": self.provider, "ref": ref,
            "request_url": request_url or url,
            "semantics": self.semantics, **self._conditional(response),
        }
        if response.status == 304:
            return common, None
        if page and response.status in self.HISTORY_END_STATUSES:
            raise FollowHistoryEnd("没有更多历史内容")
        self._check_status(response)
        return common, response

    @classmethod
    def display_thumb_url(cls, item) -> str | None:
        """一条已入库的条目现在该用哪个缩略图 URL。

        站点的 CDN 规则会变，而错的 URL 已经写进上千行。所以改写发生在读取时而不是
        改 ledger：这是可推导的投影，不是真相字段。默认照原样用，只有实测证明当前
        规则取不到的站点才在子类里覆盖——那份实测属于连接器，不属于 Web 层。
        """
        return str(item.thumb_url or "") or None

    @classmethod
    def content_hash(cls, url: str | None) -> str | None:
        """这个站的一条媒体地址里有没有文件内容的哈希，有就返回 `算法:十六进制`。

        只有确认按内容哈希命名文件的站点才覆盖；其余站点的文件名是随机 id、帖子号
        或签名令牌，同名不代表同一个文件，一律返回 None。
        """
        return None

    def enrich(self, candidates: Iterable[FollowCandidate]) -> tuple[
            tuple[FollowCandidate, ...], int]:
        """第二阶段：为列表页给不出的细节逐条打详情页。返回补全后的候选和打了几次。

        只打真正需要的条：库里已经补齐过的跳过（`enrich_skip`），额度用完的也跳过。跳过的
        条目保持 `partial=True`，落库时不会把上一轮取到的细节覆盖成空。

        细节缺失的旧行怎么补回来：需要重取媒体时调用方给出空 `enrich_skip`，
        于是整页重新走第二阶段——这是有界的一次性修复，不是每次检查都付的成本。
        """
        limit = self.enrich_budget
        skipped = self.enrich_skip
        candidates = tuple(candidate for candidate in candidates if self.within_history(candidate))
        for candidate in candidates:
            self.publish_candidate(candidate)
        spent = 0
        result: list[FollowCandidate] = []
        for candidate in candidates:
            if spent >= limit or str(candidate.external_id) in skipped:
                result.append(candidate)
            else:
                spent += 1
                result.append(self._enrich_one(candidate))
                self.publish_candidate(result[-1])
        return tuple(result), spent

    def publish_candidate(self, candidate: FollowCandidate) -> None:
        """把已通过当前条目判据的结果交回调用方，分页完成由 fetch 回执声明。"""
        if self.candidate_ready is not None:
            self.candidate_ready(candidate)

    def _enrich_one(self, candidate: FollowCandidate) -> FollowCandidate:
        """补全一条候选。取不到就原样返回（仍是 `partial`），不猜、不写死。"""
        return candidate

    @classmethod
    def provider_keys(cls) -> tuple[str, ...]:
        """这个连接器负责哪些来源键。默认就是它自己声明的那一个。

        kemono 系三站共用一套代码，所以由类自己说清楚它服务三个键，而不是在
        `CONNECTORS` 里把同一个类写三遍——那份重复要和 `HOSTS` 各改一处。
        """
        return (cls.provider,) if cls.provider else ()

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        """把一条已经认出属于本站的链接解析成可登记的订阅。

        主机到来源的分派由 `follow_providers.provider_for_host` 做，`host` 已经
        小写且去掉了 `www.`；这里只管「这个站的 URL 里哪一截是 ref」。纯解析，
        不联网；认不出就抛 `FollowSourceError` 并说清楚该长什么样。
        """
        raise FollowSourceError(f"{cls.provider} 暂不支持从链接登记")

    @classmethod
    def profile_handle(cls, ref: str) -> str:
        """这条 ref 里可以当作者别名用的平台手柄，没有就返回空串。

        只有官方渠道才有这种手柄：`fanbox/ffxivinitiala` 里的 `ffxivinitiala`
        就是作者本人在那个平台的名字。归档站与标签站的 ref 是数字 id 或标签，
        它们不是名字，学成别名只会造出一个假作者。
        """
        return ""

    @classmethod
    def is_history_end_error(cls, message: str) -> bool:
        """一句已经落盘的错误文本，其实是「历史到底」吗？

        `record_history_end` 之前的版本把往回翻到尽头记成了 `error`，正文就是
        `_check_status` 那句 `<provider> 返回 HTTP <status>`。判据只能是本连接器
        声明的 `HISTORY_END_STATUSES`——放在 Web 层照站点名硬编码中文串比较的话，
        新增来源时没人会想到还要改那一处。
        """
        matched = re.fullmatch(r".+ 返回 HTTP (\d{3})",
                               str(message or "").strip(), re.IGNORECASE)
        return bool(matched) and int(matched.group(1)) in cls.HISTORY_END_STATUSES

    def probe(self, url: str, *, headers: Mapping[str, str] | None = None) -> HttpResponse:
        """探测一个 URL 是否存在，不检查状态码。

        发现流程按状态码判断「这个作者页在不在」，404 是有意义的答案而不是故障，
        所以这里刻意不做 `_check_status`。有了它，`follow_discovery` 不必再去摸
        连接器的私有方法。
        """
        return self._get(url, headers=headers)

    def fetch_json(self, url: str, *, headers: Mapping[str, str] | None = None):
        """取回并解析一份 JSON；状态码不是 200 就抛错。"""
        response = self._get(url, headers=headers)
        self._check_status(response)
        return self.parse_json(response)

    def _gofile_media(self, links: list[str], *, labels=None) -> tuple[dict[str, object], ...]:
        """把帖子里的 Gofile 链接展开成媒体条目；实现见 `follow_gofile`。

        留一个薄委托而不是让连接器直接构造展开器：调用点（f95zone / fanbox）不必知道
        展开器怎么造，而 Gofile 的 HTTP 细节也不再摊在站点基类里。
        """
        return GofileExpander(
            self.transport, credential=self.gofile_credential,
            timeout=self.timeout, max_bytes=self.max_bytes, max_items=self.max_items,
        ).expand(links, labels=labels)


class KemonoConnector(_BaseConnector):
    """kemono.cr / coomer.st / pawchive.pw 共用同一套公开 JSON API。

    这三个站点对默认 `Accept` 头回 403，响应体里直接写着抓取应当带
    `Accept: text/css`。那是站点自己给出的抓取路径，不是绕过防护。

    `ref` 形如 `fanbox/30917150`：服务名 + 站内创作者 id。
    """

    HOSTS = {"kemono": "kemono.cr", "coomer": "coomer.st", "pawchive": "pawchive.pw"}
    #: 往回翻页翻到尽头时，kemono 系回 400（越界偏移）或 404（创作者没有更多帖子）。
    HISTORY_END_STATUSES = (400, 404)
    #: 原始文件的主机。2026-08-30 实测（取证见
    #: `docs/reference-snapshots/kemono-archive-media-host.md`）三站行为并不一致：
    #: kemono/coomer 的主域对 `/data/<path>` 回 302，分别指向 `n1.` 和 `n4.` 节点——
    #: 编号会变，所以走主域让站点自己路由，不写死；pawchive 主域对 `/data` 直接 404，
    #: 必须点名 `file.` 子域。
    #: 路径也要带 `/data` 前缀：拼成 `https://<host><path>` 少了这一段，
    #: 三站都取不到原始文件。
    FILE_HOSTS = {"pawchive": "file.pawchive.pw"}
    _SERVICE_RE = re.compile(r"^[a-z0-9_\-]{1,32}$")
    _USER_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")

    #: 一次抓取最多为「判不出来」的帖子额外打几次详情页。这是唯一会让请求数随条目数
    #: 增长的路径：一个从不贴附件、只发网盘链接的作者会让每一帖都触发一次。上限用完
    #: 之后剩下的判不出来的帖子一律保留——宁可多留卡片，不能因为额度用完就删更新。
    DEFAULT_MAX_PROBES = 12
    ENRICHED_MARK = "kept"

    #: 列表接口一页的条数，2026-08-27 实测为 50（`?o=50` 拿到的是第 51 条起）。
    PAGE_SIZE = 50

    #: 原始文件与缩略图的路径：`/data/<h0h1>/<h2h3>/<sha256>.<ext>`，缩略图前面多一段
    #: `/thumbnail`，pawchive 旧行还有不带 `/data` 的写法。
    _FILE_HASH_RE = re.compile(
        r"(?:/thumbnail)?(?:/data)?/([0-9a-f]{2})/([0-9a-f]{2})/([0-9a-f]{64})(?:\.[a-z0-9]+)?$")

    @classmethod
    def provider_keys(cls) -> tuple[str, ...]:
        return tuple(cls.HOSTS)

    @classmethod
    def content_hash(cls, url: str | None) -> str | None:
        """kemono 系三站按文件内容的 SHA-256 命名，两级目录是哈希的前四位。

        JSON 里没有单独的哈希字段，判据来自 2026-09-24 本机 ledger 只读实测：三站
        约 3 万个文件地址的两级目录全部等于文件名的前四位；kemono 与 pawchive 是两个
        各自抓取的归档站，同一组里 5034 对文件名相同，其中取得签名的 798 对缩略图
        dHash 至多差 3、色块至多差 2.0（两站各自重压缩缩略图的量级），没有一对画面
        不同。目录与文件名对不上的地址不认。
        """
        try:
            parsed = urllib.parse.urlsplit(str(url or ""))
        except ValueError:
            return None
        host = (parsed.hostname or "").casefold()
        if not any(host == base or host.endswith("." + base) for base in cls.HOSTS.values()):
            return None
        matched = cls._FILE_HASH_RE.search(parsed.path.casefold())
        if not matched or matched.group(3)[:4] != matched.group(1) + matched.group(2):
            return None
        return f"sha256:{matched.group(3)}"

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        matched = _KEMONO_PATH_RE.match(parsed.path or "/")
        if not matched:
            raise FollowSourceError(
                f"{host} 的链接要指向某个创作者，形如 "
                f"https://{host}/fanbox/user/30917150")
        service, user = matched.group(1), matched.group(2)
        return ParsedSource(provider, f"{service}/{user}",
                            f"https://{host}/{service}/user/{user}",
                            f"{user} · {service}")

    def __init__(self, provider: str = "kemono", *,
                 max_probes: int = DEFAULT_MAX_PROBES, **kwargs):
        if provider not in self.HOSTS:
            raise FollowSourceError(f"未知的 kemono 系来源：{provider}")
        super().__init__(**kwargs)
        self.provider = provider
        self.host = self.HOSTS[provider]
        self.max_probes = max_probes

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        headers["Accept"] = "text/css"
        return headers

    def _split_ref(self, ref: str) -> tuple[str, str]:
        service, _, user = (ref or "").strip().strip("/").partition("/")
        if not self._SERVICE_RE.match(service) or not self._USER_RE.match(user):
            raise FollowSourceError(
                f"{self.provider} 的 ref 必须形如 `service/user_id`，收到：{ref!r}")
        return service, user

    def fetch(self, ref: str, *, etag: str | None = None,
              last_modified: str | None = None, page: int = 0) -> SourceFetch:
        service, user = self._split_ref(ref)
        url = f"https://{self.host}/api/v1/{service}/user/{user}/posts"
        if page:
            # 实测这个接口一页固定 50 条，往回翻用 `?o=` 偏移。
            url = f"{url}?o={page * self.PAGE_SIZE}"
        common, response = self._request(url, ref=ref, etag=etag,
                                         last_modified=last_modified, page=page)
        if response is None:
            return SourceFetch(not_modified=True, **common)
        payload = self.parse_json(response)
        # kemono 回 {"posts": [...]}，pawchive 直接回列表；两种都要吃。
        posts = payload.get("posts", []) if isinstance(payload, dict) else payload
        if not isinstance(posts, list):
            raise FollowSourceError(f"{self.provider} 的帖子列表格式不符")
        if page and not posts:
            raise FollowHistoryEnd("没有更多历史内容")
        kept, skipped, probed = [], 0, 0
        for post in posts[: self.max_items]:
            if not isinstance(post, dict):
                continue
            if not self.within_history(self._candidate(post, service, user)):
                continue
            verdict = self._delivers_resource(post)
            if verdict is None and str(post.get("id") or "") in self.enrich_skip:
                # 库里已有的帖子当初判过保留，再探一次只会得到同一个答案。
                verdict = True
            if verdict is None and probed < self.max_probes:
                # 列表接口判不出来才去抓详情。这是唯一一处「一帖一请求」，
                # 所以只在拿不准时用，并且有上限。
                probed += 1
                verdict = self._probe_post(post, service, user)
            # 只有**明确判定不是** release 才丢。额度用完后仍然「不知道」的一律保留：
            # 因为探测预算耗尽就删掉用户的更新，是拿一个内部限额换一次不可见的数据丢失。
            if verdict is False:
                skipped += 1
                continue
            kept.append(self._candidate(post, service, user))
            self.publish_candidate(kept[-1])
        return SourceFetch(candidates=tuple(kept), skipped=skipped, probed=probed,
                           raw_body=response.body, **common)

    @staticmethod
    def _delivers_resource(post: dict) -> bool | None:
        """这一帖交付了资源没有。`None` 表示**列表接口判不出来**，要去抓详情。

        用户定的判据是「真正的资源贴要么贴附件，要么附上网盘链接」，而且
        **有些作者只做图**——所以一张图片附件就足够算数，不要求是压缩包或视频。

        三态而不是布尔，是因为列表接口只给 `substring`（正文摘要），网盘链接常常在
        摘要之外。判不出来时直接当成「不是 release」会删掉真东西，当成「是」又等于
        没过滤；只有第三种答案「不知道」才允许下一步去抓详情。

        **按标题关键词判投票贴是错的，不要再加回来。** 2026-08-27 拿
        LazyProcrastinator 的真实 50 条跑过一版 `poll|vote|survey|…` 正则，丢掉 18 条，
        其中有 `Public Poll Release + Littlest Ramble`、`October Poll Animations
        Released`——这位作者的正片就是按投票结果命名的。同一份数据里，
        那条「February Poll + Animations」的详情页 `poll` 字段是 `null`、正文里挂着
        gofile 链接，按资源判就是 release，按标题判就被删了。
        """
        has_file = bool((post.get("file") or {}).get("path")
                        if isinstance(post.get("file"), dict) else None)
        has_attachment = any(
            isinstance(item, dict) and item.get("path")
            for item in (post.get("attachments") or []))
        if has_file or has_attachment:
            return True
        if resource_links(post.get("substring")):
            return True
        return None

    def _probe_post(self, post: dict, service: str, user: str) -> bool:
        """抓详情页再判一次。**抓不到就当它是 release。**

        这一步是兜底，不是判据来源：网络抖一下就删掉用户的一份更新，是拿一次失败的
        请求换一次不可见的数据丢失。宁可多留一张卡片。被限流例外：照样抛出，让这次
        检查停下、整站冷却。
        """
        post_id = str(post.get("id") or "")
        if not post_id:
            return True
        url = f"https://{self.host}/api/v1/{service}/user/{user}/post/{post_id}"
        try:
            response = self._get(url)
            self._raise_if_rate_limited(response)
            if response.status >= 400:
                return True
            payload = self.parse_json(response)
        except FollowSourceRateLimited:
            raise
        except (FollowSourceError, OSError, httpx.HTTPError):
            return True
        if not isinstance(payload, dict):
            return True
        detail = payload.get("post") if isinstance(payload.get("post"), dict) else payload
        for key in ("attachments", "videos", "previews"):
            if any(isinstance(item, dict) and item.get("path")
                   for item in (payload.get(key) or [])):
                return True
        return bool(resource_links(str(detail.get("content") or "")))

    #: 缩略图能直接当封面用的扩展名。视频和压缩包没有缩略图，给了也是 404，
    #: 那会让卡片显示一个碎图而不是干净的占位。
    _THUMBABLE = (".jpg", ".jpeg", ".png", ".webp", ".gif")

    def _thumb_url(self, media: str | None) -> str | None:
        """归档站的封面。

        `thumbnail/` 前缀是必需的，不是可选的美化：换成 `/data<path>` 是 404。

        **主机用 `img.` 子域。**2026-08-30 实测（取证见
        `docs/reference-snapshots/kemono-archive-media-host.md`）：主域
        `kemono.cr` 只回 302，`pawchive.pw` 直接 404，两者的 `img.` 子域都回 200。
        2026-08-27 那次记的是主域回 200——站点行为后来变了，pawchive 的卡片因此
        一直是空的。

        这里必须设 `thumb_url`：不设的话归档站的卡片一律没有封面——
        不是取不到，是压根没去取。
        """
        if not media or not str(media).lower().endswith(self._THUMBABLE):
            return None
        return f"https://img.{self.host}/thumbnail/data{media}"

    @classmethod
    def archive_media_host(cls, provider: str, url: str) -> str:
        """把归档站的静态资源指到 `img.` 子域。

        2026-08-30 实测（取证见 `docs/reference-snapshots/kemono-archive-media-host.md`）：

            kemono.cr/thumbnail/data/<path>        302
            img.kemono.cr/thumbnail/data/<path>    200 image/jpeg 24,050 B
            pawchive.pw/thumbnail/data/<path>      404
            img.pawchive.pw/thumbnail/data/<path>  200 image/gif   12,796 B

        kemono 主域只是重定向、浏览器跟随后仍能显示，所以一直没人发现；pawchive
        主域直接 404，卡片因此永远是空的（`onerror` 把 img 摘掉，看起来像「没有
        预览图」）。2026-08-27 那次记的是主域回 200——站点行为后来变了。
        """
        host = cls.HOSTS.get(provider)
        if not host or not url.startswith(f"https://{host}/"):
            return url
        return url.replace(f"https://{host}/", f"https://img.{host}/", 1)

    @classmethod
    def display_thumb_url(cls, item) -> str | None:
        if item.thumb_url:
            return cls.archive_media_host(item.provider, str(item.thumb_url))
        # 封面修复前入库的旧行：`media_url` 是图片，但 `thumb_url` 是空的。按
        # `_thumb_url` 同一条已验证规则即时推导，不改 ledger 就能补齐旧卡片。
        media = str(item.media_url or "")
        host = cls.HOSTS.get(str(item.provider or ""))
        if not host or not _IMAGE_URL_RE.search(media):
            return None
        # `/data` 前缀是 `archive_file_url` 加的，而 `/thumbnail/data` 里已经有一个。
        # 库里两种形状都有（那次修复之前拼的没有前缀），拼之前先剥掉，否则新形状的
        # 行会得到 `/thumbnail/data/data/...` 这种必然 404 的地址。
        path = urllib.parse.urlsplit(media).path
        path = path[len("/data"):] if path.startswith("/data/") else path
        return cls.archive_media_host(
            item.provider, f"https://{host}/thumbnail/data{path}")

    def _candidate(self, post: dict, service: str, user: str) -> FollowCandidate:
        post_id = str(post.get("id") or "")
        title = plain_text(post.get("title")) or "(untitled)"
        page = f"https://{self.host}/{service}/user/{user}/post/{post_id}" if post_id else None
        primary = post.get("file") if isinstance(post.get("file"), dict) else {}
        attachments = [a for a in (post.get("attachments") or []) if isinstance(a, dict)]
        paths = [primary.get("path"), *(item.get("path") for item in attachments)]
        # 交付文件优先取**非图片**的那个。作者常把 gif 预览放在 `file` 位、真正的
        # mp4 放进附件——2026-08-30 实测 pawchive `patreon/user/80149692/post/166107691`：
        # file 是 TFCLASSIC01.gif，附件里才是两个 1080p mp4。按 `file.path` 优先会把
        # 整条判成图片，两个正片直接不见，卡片也进不了「视频」那个页签。
        media = next((path for path in paths
                      if path and not str(path).lower().endswith(self._THUMBABLE)), None)
        if media is None:
            media = primary.get("path") or (attachments[0].get("path") if attachments else None)
        # 正片/压缩包仍是主要资源；封面则要从所有附件里另找第一张图片。
        # 过去把两件事绑在同一个 `media` 上，主文件只要是 mp4/zip，后面明明附了
        # jpg 也会显示「没有预览图」。
        # 封面仍从所有路径里找第一张图片：正片是 mp4 时，那张 gif/jpg 预览就是封面。
        preview = next((path for path in paths
                        if path and str(path).lower().endswith(self._THUMBABLE)), None)
        return FollowCandidate(
            provider=self.provider,
            external_id=post_id or stable_id(title, str(post.get("published"))),
            title=title,
            url=page,
            media_url=(f"https://{self.FILE_HOSTS.get(self.provider, self.host)}"
                       f"/data{media}") if media else None,
            thumb_url=self._thumb_url(preview),
            published_at=_iso_from_text(post.get("published")),
            author=None,
            summary=plain_text(post.get("substring")),
            # kemono 系的 post id 就是原平台的 post id，所以这个键和别的站点从
            # `source` 归一出来的键是同一个命名空间——跨站重复因此能精确命中。
            group_hint=f"{service}:{post_id}" if post_id else None,
            extra={"service": service, "user": user,
                   "edited": post.get("edited"),
                   "attachment_count": len(attachments),
                   **({"media_items": items}
                      if (items := self._media_items([primary, *attachments], media)) else {})},
        )

    #: 媒体清单里认作视频的扩展名；图片沿用 `_THUMBABLE`。压缩包、文本不进清单，
    #: 仍由 `media_url` 作资源交付。
    _PLAYABLE = (".mp4", ".webm", ".mov", ".m4v")

    def _media_items(self, entries: list[dict], media: str | None) -> list[dict]:
        """一帖里能看的图和视频，给详情的多图轮播与媒体队列。

        交付文件排第一：条目级的媒体类型由清单首项决定，要与 `media_url` 是同一个；
        其余按帖子里的顺序。`file` 常在附件里再列一遍，按路径去重。只有一张的帖子
        不建清单，条目级字段已经说全了。视频没有缩略图（给了也是 404），`thumb_url`
        留空。
        """
        host = self.FILE_HOSTS.get(self.provider, self.host)
        items: list[dict] = []
        seen: set[str] = set()
        for entry in sorted(entries, key=lambda entry: entry.get("path") != media):
            path = str(entry.get("path") or "")
            lowered = path.lower()
            kind = ("image" if lowered.endswith(self._THUMBABLE)
                    else "video" if lowered.endswith(self._PLAYABLE) else None)
            if kind is None or path in seen:
                continue
            seen.add(path)
            items.append({"id": path,
                          "name": str(entry.get("name") or path.rsplit("/", 1)[-1]),
                          "url": f"https://{host}/data{path}",
                          "thumb_url": self._thumb_url(path),
                          "media_kind": kind,
                          "resource_provider": self.provider})
        return items if len(items) > 1 else []



def archive_file_url(provider: str, url: str) -> str:
    """把归档站的**原始文件** URL 修成能取到的形式。

    存量行是按旧规则拼的 `https://<主域><path>`——既少了 `/data` 前缀，主机也不对，
    所以详情里的视频一直取不到。2026-08-30 实测：

        pawchive.pw/<path>              404
        pawchive.pw/data/<path>         404   ← 主域对 /data 也不重定向
        file.pawchive.pw/data/<path>    206 video/mp4，支持 Range
        kemono.cr/data/<path>           302 → n1.kemono.cr
        coomer.st/data/<path>           302 → n4.coomer.st

    kemono/coomer 走主域让站点自己路由（nX 的编号会变，写死会过期）；
    pawchive 必须点名 `file.` 子域。缩略图仍走 `img.`，见 `archive_media_host`。
    """
    host = KemonoConnector.HOSTS.get(provider)
    if not host or not url.startswith(f"https://{host}/"):
        return url
    path = url[len(f"https://{host}") :]
    if not path.startswith("/data/"):
        path = "/data" + path
    return f"https://{KemonoConnector.FILE_HOSTS.get(provider, host)}{path}"


class Rule34VideoConnector(_BaseConnector):
    """rule34video.com 的创作者页（KVS 引擎，无公开 API，只能读 HTML）。

    `ref` 是模特/作者 slug，例如 `lazyprocrastinator`。
    """

    provider = "rule34video"
    #: 往回翻到尽头时作者页回 404。
    HISTORY_END_STATUSES = (404,)
    _SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,80}$")
    _VIDEO_RE = re.compile(r"^https://rule34video\.com/video/(\d+)/")
    #: 站点把配音和音效也记在同一份 Artist 名单里，角色写在名字末尾的括号中
    #: （`Oolay-Tiger (VA)`、`HentAudio (Audio)`、`Huntress___ (Audio/SFX)`）。
    #: 判跨作者打包只数画面作者：2026-09-08 复核库内 623 条，普通作品剔掉配音后
    #: 剩 1 到 2 位，PMV 与合辑剩 7 到 17 位，超过 3 位的一共 12 条。
    _CREDIT_ROLE_RE = re.compile(
        r"\((?:va|audio|audio/sfx|sfx|sound|voice|music)\)\s*$", re.IGNORECASE)
    MAX_COLLECTION_MODELS = 3
    DEFAULT_MAX_PROBES = 24
    ENRICHED_MARK = "tag_types"
    #: 列表卡片给的、详情页会改写的 metadata 键。库里已补齐的行跳过探测时去掉它们，
    #: 免得列表的「预览片」「相对时间」并进去盖掉详情页取到的值。
    _LIST_ONLY_EXTRA = ("media_kind", "published_precision")

    @classmethod
    def visual_model_count(cls, models) -> int:
        """这份署名里有几位画面作者。配音与音效署名不计。

        采集时按它挡合辑，读取关注列表时按它补挡历史条目——两处必须是同一次
        计数，否则「抓的时候留下了、看的时候又没了」，或者反过来。
        """
        return sum(1 for value in models or []
                   if value and not cls._CREDIT_ROLE_RE.search(str(value)))

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        matched = _R34V_PATH_RE.match(parsed.path or "/")
        if not matched:
            raise FollowSourceError(
                "rule34video 的链接要指向作者页，形如 "
                "https://rule34video.com/models/lazyprocrastinator/")
        slug = matched.group(1)
        return ParsedSource("rule34video", slug,
                            f"https://rule34video.com/models/{slug}/",
                            _slug_label(slug))

    def __init__(self, *, max_probes: int = DEFAULT_MAX_PROBES,
                 max_collection_models: int = MAX_COLLECTION_MODELS, **kwargs):
        super().__init__(**kwargs)
        self.max_probes = max(0, int(max_probes))
        self.max_collection_models = max(1, int(max_collection_models))

    def fetch(self, ref: str, *, etag: str | None = None,
              last_modified: str | None = None, page: int = 0) -> SourceFetch:
        slug = (ref or "").strip().strip("/").lower()
        if not self._SLUG_RE.match(slug):
            raise FollowSourceError(f"rule34video 的 ref 必须是作者 slug，收到：{ref!r}")
        url = f"https://rule34video.com/models/{slug}/"
        if page:
            # KVS 的分页是异步块请求，`from` 是 1 起的两位页码（`from:02`）。
            # 2026-08-27 实测这个端点回的是同样结构的 24 条卡片。
            url = (f"{url}?mode=async&function=get_block"
                   f"&block_id=custom_list_videos_common_videos"
                   f"&sort_by=post_date&from={page + 1:02d}")
        common, response = self._request(url, ref=slug, etag=etag,
                                         last_modified=last_modified, page=page,
                                         headers={"Accept": "text/html"})
        if response is None:
            return SourceFetch(not_modified=True, **common)
        soup = BeautifulSoup(response.body, "html.parser")
        seen: set[str] = set()
        listed: list[FollowCandidate] = []
        for anchor in soup.select('a[href*="/video/"]'):
            href = (anchor.get("href") or "").strip()
            matched = self._VIDEO_RE.match(href)
            if not matched or matched.group(1) in seen:
                continue
            seen.add(matched.group(1))
            listed.append(self._candidate(anchor, href, matched.group(1)))
            if len(listed) >= self.max_items:
                break
        if not listed:
            if page:
                raise FollowHistoryEnd("没有更多历史内容")
            raise FollowSourceError(
                "rule34video 创作者页没有解析出任何作品：页面结构可能已变，"
                "或该 slug 不存在")
        candidates: list[FollowCandidate] = []
        skipped_compilations = probed = 0
        for candidate in listed:
            if not self.within_history(candidate):
                continue
            enriched = candidate
            if str(candidate.external_id) in self.enrich_skip:
                # 库里这条的详情已经取到过：不再打详情页，只让列表权威的几列照常更新。
                # 合辑在落库前就挡掉了，库里有的都不是合辑。
                enriched = replace(candidate, partial=True, extra={
                    key: value for key, value in candidate.extra.items()
                    if key not in self._LIST_ONLY_EXTRA})
            elif probed < self.max_probes and candidate.url:
                probed += 1
                enriched = self._probe_detail(candidate)
            visual = int(enriched.extra.get("visual_model_count") or 0)
            if visual > self.max_collection_models:
                skipped_compilations += 1
                continue
            candidates.append(enriched)
            self.publish_candidate(enriched)
        return SourceFetch(
            candidates=tuple(candidates), skipped=skipped_compilations,
            skipped_compilations=skipped_compilations, probed=probed,
            raw_body=response.body, **common,
        )

    def _candidate(self, anchor, href: str, video_id: str) -> FollowCandidate:
        title = plain_text(anchor.get("title"))
        if not title:
            node = anchor.select_one(".thumb_title") or anchor.select_one(".title")
            title = plain_text(node.get_text(" ")) if node else None
        thumb = anchor.select_one("img")
        thumb_url = None
        if thumb is not None:
            thumb_url = thumb.get("data-original") or thumb.get("data-src") or thumb.get("src")
            if isinstance(thumb_url, str) and thumb_url.startswith("data:"):
                thumb_url = None
        # `.time` 是时长，`.added` 是相对提交时间——列表页没有绝对时间。
        duration_node = anchor.select_one(".time")
        added_node = anchor.select_one(".added")
        added_text = plain_text(added_node.get_text(" ")) if added_node else None
        preview = anchor.select_one("[data-preview]")
        return FollowCandidate(
            provider=self.provider,
            external_id=video_id,
            title=title or f"video {video_id}",
            url=href,
            media_url=str(preview.get("data-preview")) if preview is not None else None,
            thumb_url=thumb_url if isinstance(thumb_url, str) else None,
            published_at=_iso_from_relative(added_text),
            duration=_duration_seconds(
                plain_text(duration_node.get_text(" ")) if duration_node else None),
            extra={"added_text": added_text,
                   "published_precision": "approximate" if added_text else "unknown",
                   "media_kind": "preview_clip"},
        )

    def _probe_detail(self, candidate: FollowCandidate) -> FollowCandidate:
        """详情页补全封面、正片、绝对日期和标签；失败时保留列表候选。

        Rule34Video 的列表页只有预览片和相对时间，详情页才同时给 JSON-LD 正片、
        内容标签、分类与署名作者。探测有每页 24 条上限，不会无界放大请求。
        失败时的列表候选标成 `partial`：库里已有的正片、封面与日期不被列表值盖掉，
        判据仍不成立，下次检查再补。被限流照样抛出，让检查停下、整站冷却。
        """
        listed = replace(candidate, partial=True)
        try:
            response = self._get(candidate.url or "", headers={"Accept": "text/html"})
            self._raise_if_rate_limited(response)
            if response.status != 200:
                return listed
            detail = self._detail(response.body)
        except FollowSourceRateLimited:
            raise
        except (FollowSourceError, OSError, httpx.HTTPError, ValueError):
            return listed
        if not detail:
            return listed
        extra = {**dict(candidate.extra), **detail["extra"]}
        return replace(
            candidate,
            title=detail.get("title") or candidate.title,
            media_url=detail.get("media_url") or candidate.media_url,
            thumb_url=detail.get("thumb_url") or candidate.thumb_url,
            published_at=detail.get("published_at") or candidate.published_at,
            duration=detail.get("duration") or candidate.duration,
            extra=extra,
        )

    @classmethod
    def _detail(cls, body: bytes) -> dict[str, object]:
        soup = BeautifulSoup(body, "html.parser")
        video = {}
        for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                payload = json.loads(node.get_text() or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("@type") == "VideoObject":
                video = payload
                break
        tags = [plain_text(node.get_text(" ")) for node in soup.select(
            'a.tag_item[href*="/tags/"]')]
        categories = [plain_text(node.get_text(" ")) for node in soup.select(
            'a.video_meta_pill[href*="/categories/"]')]
        models = [plain_text(node.get_text(" ")) for node in soup.select(
            'a.video_meta_pill[href*="/models/"]')]
        tags = list(dict.fromkeys(value for value in tags if value))
        categories = list(dict.fromkeys(value for value in categories if value))
        models = list(dict.fromkeys(value for value in models if value))
        if not video and not tags and not categories and not models:
            return {}
        tag_types = {tag: "general" for tag in tags}
        for category in categories:
            tag_types[category] = (
                "metadata" if category.casefold() in {"2d", "3d"} else "copyright")
        for model in models:
            tag_types[model] = "artist"
        published = _iso_from_text(video.get("uploadDate"))
        return {
            "title": plain_text(video.get("name")),
            "media_url": (str(video.get("contentUrl"))
                          if video.get("contentUrl") else None),
            "thumb_url": (str(video.get("thumbnailUrl"))
                          if video.get("thumbnailUrl") else None),
            "published_at": published,
            "duration": _iso_duration_seconds(video.get("duration")),
            "extra": {
                **({"published_precision": "exact"} if published else {}),
                **({"media_kind": "video"} if video.get("contentUrl") else {}),
                "tags": tags,
                "categories": categories,
                "models": models,
                "model_count": len(models),
                "visual_model_count": cls.visual_model_count(models),
                "tag_types": tag_types,
            },
        }


class Rule34XxxConnector(_BaseConnector):
    """rule34.xxx 的官方 dapi。

    网页版已挂 Cloudflare Turnstile，Peach 不绕验证码，因此这里只走官方 API，
    并且必须带账号自己的 `user_id` + `api_key`（在
    `https://rule34.xxx/index.php?page=account&s=options` 生成）。凭据只进查询参数
    以外的位置不可行——dapi 只接受查询参数——所以请求 URL 不进日志也不进 ledger，
    只有 `request_url` 的脱敏形式会被记录。

    `ref` 是标签，例如 `lazyprocrastinator`。
    """

    provider = "rule34xxx"
    #: 一页 24 条时整页都能补上；额度存在是为了页面变长时请求数不跟着长。
    DEFAULT_ENRICH_BUDGET = 24
    #: 第二阶段补两样：帖子页的标签分类（metadata 的 `tag_types`）和原文件头的时长。
    #: mp4 缺时长的行下次检查还会进第二阶段，判据在 `follow_store._ENRICHED_PREDICATES`。
    ENRICHED_MARK = "tag_types_duration"
    _TAG_RE = re.compile(r"^[^\s&?#]{1,100}$")
    #: 历史行存的是 250px 的 preview。官方 dapi 的 `sample_url` 与它用同一
    #: bucket/hash；2026-08-28 对生产历史行实测推导，结果是 1920x1080。
    _PREVIEW_RE = re.compile(
        r"^https://api-cdn\.rule34\.xxx/thumbnails/(\d+)/thumbnail_"
        r"([0-9a-f]{32})\.jpg(?:[?#].*)?$", re.IGNORECASE)

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        tags = canonical_source_ref(
            "rule34xxx", urllib.parse.parse_qs(parsed.query).get("tags", [""])[0])
        if not tags:
            raise FollowSourceError(
                "rule34.xxx 的链接要带标签，形如 "
                "https://rule34.xxx/index.php?page=post&s=list&tags=lazyprocrastinator")
        return ParsedSource("rule34xxx", tags,
                            "https://rule34.xxx/index.php?page=post&s=list"
                            f"&tags={urllib.parse.quote(tags)}",
                            _slug_label(tags))

    #: 原图与视频：`/images/<目录>/<md5>.<ext>`。早年帖子有 40 位十六进制的文件名，
    #: 站点的 `hash` 字段与它对不上号时无从核对，不认。
    _FILE_HASH_RE = re.compile(r"^/images/\d+/([0-9a-f]{32})\.[a-z0-9]+$")

    @classmethod
    def content_hash(cls, url: str | None) -> str | None:
        """原图文件名是文件内容的 MD5：dapi 每条帖子自报的 `hash` 就是这个文件名。
        """
        try:
            parsed = urllib.parse.urlsplit(str(url or ""))
        except ValueError:
            return None
        host = (parsed.hostname or "").casefold()
        if host != "rule34.xxx" and not host.endswith(".rule34.xxx"):
            return None
        matched = cls._FILE_HASH_RE.match(parsed.path.casefold())
        return f"md5:{matched.group(1)}" if matched else None

    @classmethod
    def display_thumb_url(cls, item) -> str | None:
        matched = cls._PREVIEW_RE.match(str(item.thumb_url or ""))
        if matched and _IMAGE_URL_RE.search(str(item.media_url or "")):
            return str(item.media_url or item.thumb_url or "") or None
        return str(item.thumb_url or "") or None

    def _dapi(self, tags: str, *, limit: int, page: int = 0,
              etag: str | None = None, last_modified: str | None = None,
              ) -> tuple[dict[str, object], HttpResponse | None]:
        """问一次 dapi。凭据、地址形状和脱敏都只有这一处。

        `tags` 原样交给站点：dapi 的标签之间用空格分隔，`sort:score` 这类元标签也
        走同一个参数，所以这里不解析它，只负责把它编码进查询串。
        """
        if self.credential is None:
            raise CredentialError(
                "rule34xxx 需要 user_id 与 api_key；请把它们写进 "
                "peach-data/secrets/follow/rule34xxx.json")
        user_id, api_key = self.credential.require("user_id", "api_key")
        # `pid` 是 0 起的页号，页大小就是 `limit`。
        parameters = {
            "page": "dapi", "s": "post", "q": "index", "json": "1",
            "limit": str(min(limit, 1000)), "tags": tags,
            "user_id": user_id, "api_key": api_key,
        }
        if page:
            parameters["pid"] = str(page)
        query = urllib.parse.urlencode(parameters)
        url = f"https://api.rule34.xxx/index.php?{query}"
        safe_url = (f"https://api.rule34.xxx/index.php?page=dapi&s=post&q=index"
                    f"&tags={tags}" + (f"&pid={page}" if page else ""))
        # request_url 传脱敏版：真实 url 的查询串里带 api_key，绝不能落进证据。
        return self._request(url, ref=tags, etag=etag,
                             last_modified=last_modified, page=page,
                             headers={"Accept": "application/json"},
                             request_url=safe_url)

    def _dapi_posts(self, response: HttpResponse) -> list[dict]:
        """一次 dapi 响应里的帖子。"""
        body = response.body.decode("utf-8", errors="replace").strip()
        if body.startswith('"') and "authentication" in body.lower():
            raise CredentialError("rule34xxx 拒绝了 user_id/api_key")
        # rule34.xxx 在标签没有任何帖子时返回 HTTP 200 + 空正文，不是 JSON `[]`。
        # 这只代表零命中；非空但无法解析的响应仍按结构异常报告，避免吞掉站点改版。
        payload = [] if not body else self.parse_json(response)
        posts = payload.get("post", []) if isinstance(payload, dict) else payload
        if not isinstance(posts, list):
            raise FollowSourceError("rule34xxx 的帖子列表格式不符")
        return [post for post in posts if isinstance(post, dict)]

    def search(self, tags: str, *, limit: int = 8) -> tuple[FollowCandidate, ...]:
        """按一段标签表达式问站方要帖子，不落库、不进第二阶段。

        `fetch()` 抓的是一条订阅：单个标签、要条件请求、要补标签分类。这里问的是
        一次性的「这个题材最热的几张图」，两件事共用的只是 dapi 那一半，所以分两个
        入口而不是给 `fetch()` 加开关——订阅那条路上的条件请求和第二阶段额度，在这
        里全是白花的请求。
        """
        expression = " ".join(str(tags or "").split())
        if not expression:
            return ()
        _, response = self._dapi(expression, limit=limit)
        if response is None:
            return ()
        subject = expression.split(" ", 1)[0]
        return tuple(self._candidate(post, subject)
                     for post in self._dapi_posts(response)[:limit])

    def fetch(self, ref: str, *, etag: str | None = None,
              last_modified: str | None = None, page: int = 0) -> SourceFetch:
        tag = (ref or "").strip()
        if not self._TAG_RE.match(tag):
            raise FollowSourceError(f"rule34xxx 的 ref 必须是单个标签，收到：{ref!r}")
        common, response = self._dapi(tag, limit=self.max_items, page=page,
                                      etag=etag, last_modified=last_modified)
        if response is None:
            return SourceFetch(not_modified=True, **common)
        listed = [self._candidate(post, tag)
                  for post in self._dapi_posts(response)[: self.max_items]]
        # 第二阶段：标签分类只有帖子详情页给，列表的 dapi 只给一串扁平标签。
        candidates, probed = self.enrich(listed)
        if page and not candidates:
            raise FollowHistoryEnd("没有更多历史内容")
        return SourceFetch(candidates=candidates, probed=probed,
                           raw_body=response.body, **common)

    def _enrich_one(self, candidate: FollowCandidate) -> FollowCandidate:
        detail = self._detail(str(candidate.external_id))
        # 文件头在另一个主机上，帖子页被限流挡回来不影响它，所以不论分类取没取到都问。
        duration = self._video_seconds(candidate.media_url)
        tag_types = detail.get("tag_types")
        if not tag_types:
            # 详情页这次没给出分类。保持 partial：上一轮取到的分类还在库里，
            # 覆盖成空会让「这条没有分类」和「这次没问到」在数据里长得一样。
            # partial 行落库时时长只补空，所以读到的时长照样带上。
            return replace(candidate, duration=duration) if duration else candidate
        return replace(candidate, partial=False,
                       published_at=detail.get("published_at") or candidate.published_at,
                       duration=duration,
                       extra={**candidate.extra, "tag_types": tag_types})

    #: 读时长只取文件开头这么多字节。站方转码出的 mp4 都是 faststart，`mvhd` 在开头
    #: 几百字节里；实测随机 12 条全部读出，64 KiB 留足 `ftyp` 之后夹杂别的盒的余量。
    _VIDEO_HEAD_BYTES = 65536

    def _video_seconds(self, media_url: str | None) -> float | None:
        """原文件头里 `mvhd` 声明的时长。接口和详情页都不给时长，只有文件本身有。

        只问 mp4：rule34.xxx 的视频实测全是 mp4。取不到（状态码不对、`moov` 在文件
        结尾）就是 None，落库时 `COALESCE` 保留上一轮的值，不写 0 冒充测过。
        """
        url = str(media_url or "")
        if not urllib.parse.urlsplit(url).path.lower().endswith(".mp4"):
            return None
        headers = {"User-Agent": USER_AGENT, "Range": f"bytes=0-{self._VIDEO_HEAD_BYTES - 1}"}
        try:
            response = self.transport(HttpRequest("GET", url, headers),
                                      self.timeout, self._VIDEO_HEAD_BYTES)
        except (OSError, httpx.HTTPError):
            return None
        if response.status not in (200, 206):
            return None
        return movie_seconds(response.body[:self._VIDEO_HEAD_BYTES])

    #: 自动补全项的形状：`ria-neearts (248)`，括号里是该标签下的帖子数。
    _AUTOCOMPLETE_COUNT_RE = re.compile(r"\((\d[\d,]*)\)\s*$")

    def autocomplete(self, prefix: str) -> tuple[tuple[str, int], ...]:
        """按前缀问站方真实存在的标签，返回 `(标签, 帖子数)`。

        标签的写法是站里的既成事实，和手边的手柄常常差一个分隔符：手柄写作
        `Ria_neearts`，站上是 `ria-neearts`（2026-09-01 实测 248 帖）。逐字拿手柄
        当标签查，结果永远是零命中。

        这个接口是官方 tag 补全，公开、不需要凭据，返回值自带帖子数，正好当
        「标签下有作品」的证据。实测匹配的是**字面前缀**且大小写不敏感：`ria`
        命中 `ria-neearts`，`neea` 和 `rianeearts` 都是空——所以要试的是分隔符的
        几种写法，不是子串。
        """
        term = (prefix or "").strip()
        if not term:
            return ()
        url = ("https://api.rule34.xxx/autocomplete.php?"
               + urllib.parse.urlencode({"q": term}))
        response = self._get(url, headers={"Accept": "application/json"})
        self._check_status(response)
        payload = self.parse_json(response)
        if not isinstance(payload, list):
            raise FollowSourceError("rule34xxx 的标签补全格式不符")
        rows = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            tag = str(row.get("value") or "").strip()
            if not tag:
                continue
            matched = self._AUTOCOMPLETE_COUNT_RE.search(str(row.get("label") or ""))
            rows.append((tag, int(matched.group(1).replace(",", "")) if matched else 0))
        return tuple(rows)

    #: dapi 的 tag 接口用数字表示分类。名字取站方自己在详情页 `#tag-sidebar` 上用的
    #: 那套（`_detail` 读的就是它），两条路认出来的类型才是同一个词。
    _TAG_TYPE_NAMES = {0: "general", 1: "artist", 3: "copyright",
                       4: "character", 5: "metadata"}
    #: dapi 的 tag 接口只回 XML，`json=1` 实测被忽略。
    _TAG_ROW_RE = re.compile(
        r'<tag\s+type="(\d+)"\s+count="(\d+)"\s+name="([^"]*)"', re.IGNORECASE)

    def tag_type(self, name: str) -> str:
        """这个标签在站上是什么分类，问不出来就是空串。

        补全接口只回名字和帖子数，认不出哪个是作者：`lewd`（8548 帖）是普通标签、
        `lewd_dorky` 是角色、`lewdrex` 才是作者，名字本身看不出区别。分类只有
        dapi 的 tag 接口给，而它要 `user_id` + `api_key`——没凭据时实测回的是
        `"Missing authentication"`，那时认不出就是认不出，不按词形猜。

        只接受精确名：`name_pattern` 实测是两边通配的子串匹配，而且按 id 截断——
        `lewd%` 取满 1000 条里一条真前缀都没有，`orderby` 也不生效。
        """
        tag = (name or "").strip()
        if not tag or self.credential is None:
            return ""
        user_id, api_key = self.credential.require("user_id", "api_key")
        query = urllib.parse.urlencode({
            "page": "dapi", "s": "tag", "q": "index", "name": tag,
            "user_id": user_id, "api_key": api_key,
        })
        # 查询串里带 api_key，和 `fetch` 一样绝不落进日志或证据。
        response = self._get(f"https://api.rule34.xxx/index.php?{query}",
                             headers={"Accept": "application/xml"})
        self._check_status(response)
        body = response.body.decode("utf-8", errors="replace")
        if body.lstrip().startswith('"') and "authentication" in body.lower():
            raise CredentialError("rule34xxx 拒绝了 user_id/api_key")
        for code, _count, found in self._TAG_ROW_RE.findall(body):
            if html.unescape(found).strip().casefold() == tag.casefold():
                return self._TAG_TYPE_NAMES.get(int(code), "")
        return ""

    #: 拼可读标签时跳过的词：作者手柄、媒体类型和评级，留下的才是内容。
    _TITLE_TAG_STOPWORDS = frozenset({
        "video", "sound", "animated", "mp4", "webm", "3d", "hd", "60fps",
        "tagme", "highres", "absurdres",
    })
    #: 详情页取不到时的退避节奏，与外网退避规则同一套。
    _DETAIL_RETRY_DELAYS = (1.5, 4.0, 9.0)
    #: 详情页 `#stats` 里的上传时间：`Posted: 2026-09-11 00:48:20 by mhzw666`。
    _POSTED_RE = re.compile(r"Posted:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

    def _detail(self, post_id: str) -> dict[str, object]:
        """帖子详情页上才有的两样：标签分类和上传时间。取不到就是空字典。

        dapi 只给一串扁平标签，分类要读详情页 `#tag-sidebar`，取不到就不给，不按词形
        猜。dapi 也不给上传时间：它的 `change` 是最后一次修改，实测 17361475 在
        2026-05-01 上传、09-14 被改过标签，`change` 就是 09-14。详情页 `#stats` 的
        Posted 才是上传时间；没被改过的帖子两者逐秒相同，所以它和 `change` 一样是 UTC。
        """
        url = ("https://rule34.xxx/index.php?page=post&s=view&id="
               f"{urllib.parse.quote(post_id)}")
        # 站方公布的口径是每 60 秒 60 次，而列表页一页 24 条、每条都要单独打一次
        # 详情页，被挡回来是常态。一次挡回来就返回 {}，得到的是「这条没有类型」——
        # 和「这条确实没有类型」在日志里长得一模一样。实测：回填首轮 400 条里
        # 372 条判成「没有类型」，事后逐条直取，全部 200 且 `#tag-sidebar` 正常。
        response = None
        for delay in (0.0, *self._DETAIL_RETRY_DELAYS):
            if delay:
                self.sleeper(delay)
            try:
                response = self._get(url, headers={"Accept": "text/html"})
            except FollowSourceError:
                response = None
                break
            if response.status not in {425, 429}:
                break
        if response is None or response.status != 200:
            return {}
        soup = BeautifulSoup(response.body, "html.parser")
        allowed = {"general", "artist", "copyright", "character", "metadata"}
        result: dict[str, str] = {}
        for row in soup.select("#tag-sidebar li[class*='tag-type-']"):
            tag_type = next((
                value.removeprefix("tag-type-") for value in row.get("class", [])
                if value.startswith("tag-type-")
            ), "")
            if tag_type not in allowed:
                continue
            link = row.select_one("a[href*='page=post'][href*='tags=']")
            if link is None:
                continue
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(str(link.get("href") or "")).query)
            name = html.unescape(str((query.get("tags") or [""])[0])).strip()
            if name:
                result[name] = tag_type
        stats = soup.select_one("#stats")
        posted = self._POSTED_RE.search(stats.get_text(" ") if stats is not None else "")
        return {"tag_types": result,
                "published_at": _iso_from_text(posted.group(1)) if posted else None}

    def _candidate(self, post: dict, tag: str) -> FollowCandidate:
        post_id = str(post.get("id") or "")
        # dapi 返回的标签是 HTML 转义形态（实测 `miqo&#039;te`）。实体不反转义
        # 就进 metadata，读取层再转一次就成了双重转义，用户看到的就是 `&#039;`
        # 字面量，同一个标签还会和反转义后的写法分裂成两个身份。
        tags = html.unescape(str(post.get("tags") or ""))
        image = str(post.get("image") or "")
        stem = re.sub(r"\.[a-z0-9]{2,5}$", "", image, flags=re.IGNORECASE)
        # booru 帖子没有标题。实测 rule34.xxx 的 `image` 全是 32 位十六进制哈希，
        # 拿它当标题既不可读，又会让每条帖子各自成组。哈希就退回标签拼一个可读标签，
        # 并声明这不是名字——标签相似的两个作品不能因此被并成一个。
        opaque = _is_opaque_filename(stem)
        if stem and not opaque:
            title, title_from, title_is_name = (
                plain_text(stem.replace("_", " ")) or f"post {post_id}", "image", True)
        else:
            title = self._tag_label(tags, tag) or f"post {post_id}"
            title_from, title_is_name = "tags", False
        # 出处比站内父帖强得多：实测 15 条 parent_id 全是 0，而 13 条有 source，
        # 其中 4 条指向同一个 fanbox 帖——那才是真正的同组信号，而且跨站可比。
        # source 是站点转义过的 URL（`&amp;` 代替 `&`），反转义后才是真实地址；
        # 归组键只取 path，query 里的实体不影响既有分组的稳定性。
        source = post.get("source")
        source = html.unescape(str(source)) if source else None
        parent = post.get("parent_id")
        hint = origin_group_key(str(source) if source else None)
        if hint is None:
            anchor = parent if parent not in (None, 0, "0", "") else post_id
            hint = f"{self.provider}:post:{anchor}" if anchor else None
        return FollowCandidate(
            provider=self.provider,
            external_id=post_id or stable_id(image, tags),
            title=title,
            url=f"https://rule34.xxx/index.php?page=post&s=view&id={post_id}"
                if post_id else None,
            media_url=str(post.get("file_url")) if post.get("file_url") else None,
            # dapi 的 preview_url 只有约 250px；sample_url 对视频是同帧 JPEG，
            # 对图片则是站点选定的样图。gallery-dl 的 booru 抽取器也把这三层
            # 作为可配置回退链，不能把最小 preview 固定成 Peach 封面。
            thumb_url=str(post.get("sample_url") or post.get("preview_url") or "") or None,
            # `change` 是最后修改时间，只在详情页取不到时占位；上传时间由 `_detail` 给。
            published_at=_iso_from_epoch(post.get("change")),
            group_hint=hint,
            title_is_name=title_is_name,
            # 列表视图：标签分类要等第二阶段的详情页。
            partial=True,
            extra={"tag": tag, "tags": tags, "score": post.get("score"),
                   "source": source, "title_from": title_from,
                   "preview_url": post.get("preview_url"),
                   # dapi 随帖给出原文件的宽高；图片墙靠它在图落地前占好比例。
                   # sample 与 preview 都是等比缩放，比例与原文件一致。
                   **_dims_extra(post.get("width"), post.get("height"))},
        )

    @classmethod
    def _tag_label(cls, tags: str, subject: str) -> str:
        """用标签拼一个可读标签。只作展示，不参与按标题分组。"""
        skip = cls._TITLE_TAG_STOPWORDS | {subject.strip().lower()}
        words = [word.replace("_", " ") for word in tags.split()
                 if word.lower() not in skip and not word.startswith("rating:")]
        return " · ".join(words[:5])


class Rule34PahealConnector(_BaseConnector):
    """rule34.paheal.net 标签页；详情页补齐原始出处用于精确跨站去重。"""

    provider = "rule34paheal"
    #: 往回翻到尽头时标签页回 404。
    HISTORY_END_STATUSES = (404,)
    #: 一页 24 条，整页都能补上；额度存在是为了页面变长时请求数不跟着长。
    DEFAULT_ENRICH_BUDGET = 24
    #: 上传时间只有详情页给（列表的缩略图属性里没有），所以它就是补齐的判据。
    ENRICHED_MARK = "published_at"
    _TAG_RE = re.compile(r"^[^/?#]{1,100}$")
    _DURATION_RE = re.compile(r"\b(\d+(?:\.\d+)?)s\b", re.IGNORECASE)
    _TITLE_STOPWORDS = frozenset({"animated", "blender", "video", "sound", "mp4", "webm"})
    #: 原始文件：`r34i.paheal-cdn.net/<h0h1>/<h2h3>/<md5>`，没有扩展名。
    _FILE_HASH_RE = re.compile(r"^/([0-9a-f]{2})/([0-9a-f]{2})/([0-9a-f]{32})$")

    @classmethod
    def content_hash(cls, url: str | None) -> str | None:
        """原始文件按内容 MD5 命名，两级目录是哈希的前四位。

        地址形状与站点自报的 MD5 一致时，作为跨来源内容身份使用。
        """
        try:
            parsed = urllib.parse.urlsplit(str(url or ""))
        except ValueError:
            return None
        host = (parsed.hostname or "").casefold()
        if host != "paheal-cdn.net" and not host.endswith(".paheal-cdn.net"):
            return None
        matched = cls._FILE_HASH_RE.match(parsed.path.casefold())
        if not matched or matched.group(3)[:4] != matched.group(1) + matched.group(2):
            return None
        return f"md5:{matched.group(3)}"

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        # 搜索标签既可能在 fragment 里（帖子详情页），也可能在列表页路径里。
        tag = urllib.parse.parse_qs(parsed.fragment).get("search", [""])[0]
        if not tag:
            matched = _PAHEAL_LIST_RE.match(parsed.path or "/")
            tag = urllib.parse.unquote(matched.group(1)) if matched else ""
        tag = canonical_source_ref("rule34paheal", tag)
        if not tag:
            raise FollowSourceError(
                "rule34.paheal 的链接要带搜索标签，形如 "
                "https://rule34.paheal.net/post/view/7428820#search=InitialA")
        encoded = urllib.parse.quote(tag, safe="()_")
        return ParsedSource("rule34paheal", tag,
                            f"https://rule34.paheal.net/post/list/{encoded}/1",
                            _slug_label(tag))

    def __init__(self, *, max_items: int = 24, **kwargs):
        super().__init__(max_items=max_items, **kwargs)

    def fetch(self, ref: str, *, etag: str | None = None,
              last_modified: str | None = None, page: int = 0) -> SourceFetch:
        tag = str(ref or "").strip()
        if not self._TAG_RE.fullmatch(tag):
            raise FollowSourceError(f"rule34.paheal 的 ref 必须是单个标签，收到：{ref!r}")
        page_number = page + 1
        encoded = urllib.parse.quote(tag, safe="()_")
        url = f"https://rule34.paheal.net/post/list/{encoded}/{page_number}"
        common, response = self._request(url, ref=tag, etag=etag,
                                         last_modified=last_modified, page=page,
                                         headers={"Accept": "text/html"})
        if response is None:
            return SourceFetch(not_modified=True, **common)
        soup = BeautifulSoup(response.body, "html.parser")
        listed = []
        for thumb in soup.select(".shm-image-list .shm-thumb[data-post-id]")[:self.max_items]:
            post_id = str(thumb.get("data-post-id") or "")
            if not post_id.isdigit():
                continue
            listed.append(self._listed(thumb, post_id, tag))
        # 第二阶段：上传时间、时长、上传者和原始出处都只有帖子详情页给。
        candidates, probed = self.enrich(listed)
        if not candidates:
            if page:
                raise FollowHistoryEnd("没有更多历史内容")
            raise FollowSourceError("rule34.paheal 标签页没有解析出任何作品")
        return SourceFetch(candidates=candidates, probed=probed,
                           raw_body=response.body, **common)

    def _listed(self, thumb, post_id: str, tag: str) -> FollowCandidate:
        """只用列表页缩略图上的属性造一条候选。不联网。

        `extra` 里刻意**不放**详情才知道的键（`source`）：`partial` 行落库走
        `json_patch`，而补丁里的 null 是「删掉这个键」，写进去就会把上一轮取到的
        出处抹掉。
        """
        tags = str(thumb.get("data-tags") or "").split()
        # 列表缩略图有两种写法：归档里早期的页面给扩展名 `data-ext='mp4'`，
        # 现在的页面给 MIME `data-mime='video/mp4'`，两种都认。
        extension = str(thumb.get("data-ext") or "").casefold()
        mime = str(thumb.get("data-mime") or "").casefold()
        video = extension in {"mp4", "webm", "mov"} or mime.startswith("video/")
        link = thumb.select_one("a.shm-thumb-link[href]")
        file_link = thumb.select_one("a[href*='paheal-cdn.net'], a[href*='r34i.paheal']")
        image = thumb.select_one("img[src]")
        return FollowCandidate(
            provider=self.provider,
            external_id=post_id,
            title=self._tag_label(tags, tag) or f"Paheal 帖子 {post_id}",
            url=urllib.parse.urljoin(
                "https://rule34.paheal.net",
                str(link.get("href") if link else f"/post/view/{post_id}")),
            media_url=str(file_link.get("href")) if file_link else None,
            thumb_url=str(image.get("src")) if image else None,
            group_hint=f"{self.provider}:post:{post_id}",
            title_is_name=False,
            partial=True,
            extra={"tag": tag, "tags": " ".join(tags), "title_from": "tags",
                   "media_kind": "video" if video else "image",
                   "tag_types": {value: "general" for value in tags}},
        )

    def _enrich_one(self, candidate: FollowCandidate) -> FollowCandidate:
        detail = self._detail(str(candidate.external_id))
        if not detail:
            # 详情页这次没拿到。保持 partial：上一轮取到的上传时间和时长留在库里，
            # 覆盖成空会让「站上就没写」和「这次没问到」在数据里长得一样。
            return candidate
        subject = str(candidate.extra.get("tag") or "")
        tag_values = (list(detail.get("tags") or ())
                      or str(candidate.extra.get("tags") or "").split())
        return replace(
            candidate,
            partial=False,
            title=self._tag_label(tag_values, subject) or candidate.title,
            media_url=str(detail.get("media_url") or "") or candidate.media_url,
            thumb_url=str(detail.get("thumb_url") or "") or candidate.thumb_url,
            published_at=detail.get("published_at"),
            duration=detail.get("duration"),
            author=detail.get("author"),
            group_hint=(origin_group_key(detail.get("source"))
                        or candidate.group_hint),
            extra={**candidate.extra, "tags": " ".join(tag_values),
                   "source": detail.get("source"),
                   "tag_types": {value: "general" for value in tag_values}},
        )

    #: 详情页取不到就重试的状态码。列表页一页 24 条，每条都要单独打一次详情页，
    #: 上游按频率挡回来是常态；一次挡回来就当「这条没有上传时间」，得到的是一条
    #: 看似完整、时间却是抓取时刻的记录——比报错更难发现。
    _DETAIL_RETRY_STATUSES = frozenset({425, 429})
    #: 与外网退避规则同一套节奏。
    _DETAIL_RETRY_DELAYS = (1.0, 2.0, 4.0)

    def _detail(self, post_id: str) -> dict[str, object]:
        url = f"https://rule34.paheal.net/post/view/{post_id}"
        response = self._get(url, headers={"Accept": "text/html"})
        for delay in self._DETAIL_RETRY_DELAYS:
            if response.status not in self._DETAIL_RETRY_STATUSES:
                break
            self.sleeper(delay)
            response = self._get(url, headers={"Accept": "text/html"})
        if response.status != 200:
            return {}
        soup = BeautifulSoup(response.body, "html.parser")
        video = soup.select_one("video#main_image")
        source_node = soup.select_one("tr[data-row='Source Link'] td a[href]")
        time_node = soup.select_one("tr[data-row='Uploader'] time[datetime]")
        author_node = soup.select_one("tr[data-row='Uploader'] a.username")
        info_node = soup.select_one("tr[data-row='Info'] td")
        tags = [plain_text(node.get_text(" ")) for node in
                soup.select("tr[data-row='Tags'] a.tag")]
        media = (video.select_one("source[src]") if video is not None else
                 soup.select_one("img#main_image[src]"))
        duration_match = self._DURATION_RE.search(info_node.get_text(" ") if info_node else "")
        return {
            "media_url": str(media.get("src")) if media is not None and media.get("src") else None,
            "thumb_url": (str(video.get("poster")) if video is not None and video.get("poster")
                          else None),
            "published_at": _iso_from_text(time_node.get("datetime") if time_node else None),
            "duration": float(duration_match.group(1)) if duration_match else None,
            "author": plain_text(author_node.get_text(" ") if author_node else ""),
            "source": str(source_node.get("href")) if source_node is not None else None,
            "tags": [tag for tag in tags if tag],
        }

    @classmethod
    def _tag_label(cls, tags: list[str], subject: str) -> str:
        skip = cls._TITLE_STOPWORDS | {subject.casefold()}
        values = [tag.replace("_", " ") for tag in tags if tag.casefold() not in skip]
        return " · ".join(values[:5])


def _xenforo_thread_title(soup) -> str | None:
    """取 XenForo 线程页的 `h1.p-title-value` 并去掉前缀标签。

    `<title>` 会被站点拼上栏目名和站名，og:title 同样带前缀，只有 h1 里的
    `.label` 是可以精确摘掉的结构。f95zone 与 simpcity 都是 XenForo，这段只写一份。
    """
    heading = soup.select_one("h1.p-title-value")
    if heading is None:
        return None
    for label in heading.select(".label, .labelLink"):
        label.extract()
    return plain_text(heading.get_text(" "))


def _xenforo_posts(soup, limit: int):
    """XenForo 线程页里最后 `limit` 个楼层，逐个给出 `(article, post_id, time, body)`。

    引用块在这里就剥掉。XenForo 把被引用的楼层原样嵌在正文里，不剥的话摘要会变成
    「某某 said: … Click to expand…」，而引用里的下载链接还会被算成这条回复自己发的——
    追更判断因此指向错误的楼层。
    """
    posts = soup.select('article[data-content^="post-"]')
    for article in posts[-limit:]:
        content = str(article.get("data-content") or "")
        post_id = content.removeprefix("post-")
        if not post_id.isdigit():
            continue
        time_node = article.select_one("time")
        body = article.select_one(".bbWrapper")
        if body is not None:
            # 链接预览卡（unfurl）是楼主贴的一条裸链接被站点渲染成的卡片：卡片上的标题
            # 和摘要不是楼主写的，链接却是。先把它换回一个指向 `data-url` 的普通链接，
            # 再剥块级引用，否则网盘链接会连同预览一起被当成引用剥掉（2026-09-08 实测
            # simpcity 一楼的 Gofile 链接就只存在于预览卡里）。
            for unfurl in body.select(".bbCodeBlock--unfurl[data-url]"):
                target = str(unfurl.get("data-url") or "")
                link = soup.new_tag("a", href=target)
                link.string = target
                unfurl.replace_with(link)
            for quote in body.select("blockquote, .bbCodeBlock, .js-expandWatch"):
                quote.extract()
        yield article, post_id, time_node, body


#: 搜索结果里线程链接的形状：`/threads/<slug>.<id>/`，后面可能还跟着帖子锚点。
_XENFORO_SEARCH_THREAD_RE = re.compile(r"^/threads/(?:[^/]*\.)?(\d+)/")
_XENFORO_TOKEN_RE = re.compile(rb'name="_xfToken" value="([^"]+)"')


def _xenforo_search_threads(connector: "_BaseConnector", host: str, query: str, *,
                            cookie: str, title_only: bool = True) -> tuple[dict, ...]:
    """XenForo 站内搜索按标题找线程；f95zone 与 simpcity 共用这一份。

    **站内搜索必须登录**，无 cookie 时 `/search/` 直接回 403。搜索表单里的
    `_xfToken` 和会话绑定，所以每次都要先取一遍，不能缓存成常量。提交后站点
    303 到 `/search/<id>/?q=…` 结果页，传输层同源跟随并保留 Cookie。

    每行给出 `thread_id`、去掉前缀标签的 `title`，以及那些前缀标签本身 `labels`
    （f95 的 `Collection`、`Pinup`，simpcity 的 `OnlyFans`、`Simp Chat`）：同一个
    名字的资源线程和讨论帖都会命中，标签是让人分辨的依据。
    """
    headers = {"Accept": "text/html", "Cookie": cookie}
    form = connector._get(f"https://{host}/search/", headers=headers)
    connector._check_status(form)
    matched = _XENFORO_TOKEN_RE.search(form.body)
    if matched is None:
        raise FollowSourceError(
            f"{connector.provider} 搜索表单里没有 _xfToken：cookie 可能已失效")
    body = urllib.parse.urlencode({
        "keywords": query, "c[title_only]": "1" if title_only else "0",
        "order": "relevance", "search_type": "post",
        "_xfToken": matched.group(1).decode("utf-8", errors="replace"),
    }).encode()
    response = connector._post(
        f"https://{host}/search/search", body,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded",
                 "Referer": f"https://{host}/search/"})
    connector._check_status(response)
    soup = BeautifulSoup(response.body, "html.parser")
    rows, seen = [], set()
    for link in soup.select("h3.contentRow-title a[href]"):
        found = _XENFORO_SEARCH_THREAD_RE.match(link.get("href") or "")
        if found is None or found.group(1) in seen:
            continue
        seen.add(found.group(1))
        labels = [plain_text(node.get_text(" ")) for node in link.select(".label")]
        for label in link.select(".label, .labelLink, .label-append"):
            label.extract()
        # 命中的词被 `<em class="textHighlight">` 包着，按分隔符取文本会把
        # `[Ria_neearts]` 拆成 `[ Ria_neearts ]`——标题要的是原样。
        rows.append({"thread_id": found.group(1),
                     "title": plain_text(link.get_text("")),
                     "labels": [label for label in labels if label]})
    return tuple(rows)


#: 帖子正文里那些确实说明「作者本人在哪」的站点，以及从地址里取手柄的方式。
#: 主机写死在这里：论坛正文是任何人都能编辑的地方，放开主机等于把别人随手贴的
#: 地址当成作者身份，头像和别名都会跟着错。
_PROFILE_LINK_HOSTS = {
    "twitter.com": "twitter", "x.com": "twitter",
    "patreon.com": "patreon",
    "subscribestar.adult": "subscribestar", "subscribestar.com": "subscribestar",
    "deviantart.com": "deviantart",
    "instagram.com": "instagram",
    "gumroad.com": "gumroad",
    "ko-fi.com": "kofi",
}
#: 这些站点的第一段路径是功能页而不是名字，取到了也不是手柄。
_PROFILE_PATH_STOPWORDS = frozenset({
    "about", "c", "checkout", "cw", "discover", "explore", "help", "home", "join",
    "login", "posts", "search", "settings", "signup", "user", "users", "watch",
})
_PROFILE_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{2,64}$")
_PIXIV_USER_RE = re.compile(r"(?:^|/)users/(\d{1,20})(?:$|/)")
_XENFORO_MEMBER_RE = re.compile(r"^members/([^/.]+)\.(\d{1,20})/?$")
_BSKY_PROFILE_RE = re.compile(r"^profile/([^/]+)$")
_FANTIA_FANCLUB_RE = re.compile(r"^fanclubs/(\d{1,20})(?:$|/)")


def profile_link_identity(url: str, *, forum_host: str = "") -> tuple[str, str] | None:
    """一条链接指向哪个服务上的哪个手柄，认不出就返回 None。

    `forum_host` 传进来时，论坛自己的 `/members/<name>.<id>/` 也算一个身份：
    作者本人在站上有账号时，正文里贴的就是这个地址。
    """
    value = str(url or "").strip()
    # booru 的 source 偶尔把几条出处用空格拼在一个字段里。urlsplit 会把后一条
    # 吞进前一条的 path，让第一段手柄看起来仍然合法；这种字段不是一条身份链接。
    if not value or any(character.isspace() for character in value):
        return None
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = (parsed.path or "").strip("/")
    first = path.split("/")[0] if path else ""
    if host.endswith(".fanbox.cc"):
        handle = host.removesuffix(".fanbox.cc")
        return ("fanbox", handle) if _PROFILE_HANDLE_RE.match(handle) else None
    if host == "fanbox.cc" and first.startswith("@"):
        handle = first[1:]
        return ("fanbox", handle) if _PROFILE_HANDLE_RE.match(handle) else None
    if host == "pixiv.net":
        matched = _PIXIV_USER_RE.search(path)
        return ("pixiv", matched.group(1)) if matched else None
    if host.endswith(".itch.io"):
        handle = host.removesuffix(".itch.io")
        return ("itchio", handle) if _PROFILE_HANDLE_RE.match(handle) else None
    if host == "bsky.app":
        matched = _BSKY_PROFILE_RE.match(path)
        handle = matched.group(1) if matched else ""
        return ("bsky", handle) if _PROFILE_HANDLE_RE.match(handle) else None
    if host == "fantia.jp":
        matched = _FANTIA_FANCLUB_RE.match(path)
        return ("fantia", matched.group(1)) if matched else None
    if forum_host and host == forum_host.lower().removeprefix("www."):
        matched = _XENFORO_MEMBER_RE.match(path)
        return (forum_host.split(".")[0], matched.group(1)) if matched else None
    service = _PROFILE_LINK_HOSTS.get(host)
    if service is None or first.casefold() in _PROFILE_PATH_STOPWORDS:
        return None
    return (service, first) if _PROFILE_HANDLE_RE.match(first) else None


#: 一段正文里最多认多少个身份。作者的链接区就那么几行，比这还多的多半是
#: 别人在回复里贴的一串，不是这个人的名片。
MAX_PROFILE_LINKS = 8


def official_profile_links(body, *, forum_host: str = "") -> tuple[dict, ...]:
    """一段帖子正文里指向作者官方主页的链接，逐个给出服务与手柄。

    同一个服务下的**不同手柄**都要留：2026-09-12 实测 `63802` 的首楼同时挂着
    `twitter.com/strauzek` 和 `twitter.com/Mr_Strauz`，这正是「同一个人两个写法」
    的可复现证据，也是别名候选的依据。重复的同一个地址只留一条——作者常把同一个
    Patreon 贴三遍（文字、按钮图、签名各一次）。
    """
    found: dict[tuple[str, str], dict] = {}
    for node in (body.select("a[href]") if body is not None else ()):
        identity = profile_link_identity(node.get("href") or "", forum_host=forum_host)
        if identity is None:
            continue
        service, handle = identity
        found.setdefault(identity, {"service": service, "handle": handle,
                                    "url": str(node.get("href"))})
        if len(found) >= MAX_PROFILE_LINKS:
            break
    return tuple(found.values())


class F95ZoneConnector(_BaseConnector):
    """f95zone.to 的线程追更。

    主贴的版本号更新常常滞后于回复——真正的新链接先出现在楼下——所以这里读的是
    线程的 `/latest` 页而不是主贴；`latest_data.php` 另给线程当前的 `version` 与时间戳。

    **发现不需要登录，解析 masked 链接需要。** 2026-08-28 实测 `/latest` 页在无
    cookie 下完整返回回复正文与 `/masked/...`；配置 cookie 后向同一路径 POST
    `xhr=1&download=1` 才返回真实网盘 URL。cookie 只发回 f95zone.to，绝不跟着
    真实链接送到 Gofile / Pixeldrain。

    `ref` 是线程 id，例如 `50685`。
    """

    provider = "f95zone"
    _THREAD_RE = re.compile(r"^\d{1,12}$")
    #: latest_data.php 按分类分库，线程不在哪个分类里事先不知道，只能逐个试。
    CATEGORIES = ("games", "animations", "comics", "assets", "mods")
    _MASKED_PATH_RE = re.compile(r"^/masked/", re.IGNORECASE)
    _ATTACHMENT_PATH_RE = re.compile(r"^/attachments/\d+/?$", re.IGNORECASE)
    _INLINE_IMAGE_RE = re.compile(
        r"\.(?:avif|bmp|gif|jpe?g|png|webp)(?:$|[?#])", re.IGNORECASE)

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        path = parsed.path or "/"
        matched = _THREAD_PATH_RE.match(path)
        if not matched:
            raise FollowSourceError(
                "f95zone 的链接要指向一个线程，形如 "
                "https://f95zone.to/threads/xxx.50685/")
        thread = matched.group(1)
        slug = path.split("/threads/", 1)[1].rsplit(".", 1)[0] if "." in path else ""
        return ParsedSource("f95zone", thread,
                            f"https://f95zone.to/threads/{thread}/",
                            _slug_label(slug) or f"线程 {thread}")

    def fetch(self, ref: str, *, etag: str | None = None,
              last_modified: str | None = None, page: int = 0) -> SourceFetch:
        thread = (ref or "").strip()
        if not self._THREAD_RE.match(thread):
            raise FollowSourceError(f"f95zone 的 ref 必须是线程 id，收到：{ref!r}")
        if page:
            # `/latest` 是站点自己渲染的「最近回复」聚合视图，没有 page-N 变体，
            # 这个连接器因此只有一页可读。`page` 不能静默丢掉：丢了往回翻页会
            # 重新抓同一页、报成一次成功的检查，表现就是「点了没反应」。报到底
            # 比假装成功好：调用方据此显示历史已尽。
            raise FollowHistoryEnd("没有更多历史内容")
        url = f"https://f95zone.to/threads/{thread}/latest"
        headers = {"Accept": "text/html"}
        if self.credential and self.credential.values.get("cookie"):
            headers["Cookie"] = self.credential.values["cookie"]
        common, response = self._request(url, ref=thread, etag=etag,
                                         last_modified=last_modified, headers=headers)
        if response is None:
            return SourceFetch(not_modified=True, **common)
        soup = BeautifulSoup(response.body, "html.parser")
        title = _xenforo_thread_title(soup)
        candidates, parsed, skipped = self._replies(
            soup, thread, title or f"thread {thread}")
        if not parsed:
            raise FollowSourceError(
                "f95zone 线程页没有解析出任何回复：可能需要登录，或页面结构已变")
        enriched = []
        for candidate in candidates:
            if not self.within_history(candidate):
                continue
            links = [str(value) for value in candidate.extra.get("links", [])]
            image_items = f95_attachment_media_items(candidate.extra)
            media_items = tuple(image_items) + self._gofile_media(links)
            enriched.append(replace(
                candidate,
                extra={**dict(candidate.extra), "media_items": media_items,
                       "gofile_video_count": sum(
                           item.get("media_kind") == "video" for item in media_items)},
            ))
            self.publish_candidate(enriched[-1])
        return SourceFetch(candidates=tuple(enriched), skipped=skipped,
                           raw_body=response.body, **common)

    def _replies(self, soup, thread: str, thread_title: str):
        candidates: list[FollowCandidate] = []
        parsed = 0
        skipped = 0
        for article, post_id, time_node, body in _xenforo_posts(soup, self.max_items):
            parsed += 1
            links = [
                str(node.get("href")) for node in (body.select("a[href]") if body else [])
                if str(node.get("href", "")).startswith("http")
            ]
            media_links, needs_credential = self._media_links(links)
            attachment_urls = [url for url in self._attachment_urls(body)
                               if not f95_discussion_image(url)]
            # F95 会把正文里粘贴的 GIF / meme 也存到 attachments.f95zone.to。
            # 它们只是讨论插图，不是作者交付的资源；单凭一张内嵌图片不能让楼层
            # 进入追更。非图片附件仍保留，正文图片按已确认的讨论附件身份过滤。
            downloadable_attachments = [
                url for url in attachment_urls if not self._INLINE_IMAGE_RE.search(url)
            ]
            if not media_links and not downloadable_attachments:
                skipped += 1
                continue
            direct_attachment = next((url for url in attachment_urls
                                      if urllib.parse.urlsplit(url).hostname
                                      == "attachments.f95zone.to"), None)
            candidates.append(FollowCandidate(
                provider=self.provider,
                external_id=post_id,
                title=thread_title,
                url=f"https://f95zone.to/threads/{thread}/post-{post_id}",
                media_url=media_links[0] if media_links else None,
                thumb_url=direct_attachment,
                published_at=_iso_from_text(time_node.get("datetime"))
                if time_node is not None else None,
                author=plain_text(str(article.get("data-author") or "")) or None,
                summary=plain_text(body.get_text(" ")) if body else None,
                extra={"thread_id": thread, "link_count": len(media_links),
                       "links": media_links[:8],
                       "attachment_count": len(attachment_urls),
                       "attachments": attachment_urls[:8],
                       "media_needs_credential": needs_credential},
            ))
        return tuple(candidates), parsed, skipped

    @classmethod
    def _attachment_urls(cls, body) -> list[str]:
        """只认回复正文内的 F95 附件，避免把头像或签名图算成发布内容。"""
        if body is None:
            return []
        direct: list[str] = []
        pages: list[str] = []
        for node in body.select("[data-src], a[href]"):
            for attribute in ("data-src", "href"):
                value = str(node.get(attribute) or "").strip()
                if not value.startswith("https://"):
                    continue
                try:
                    parsed = urllib.parse.urlsplit(value)
                except ValueError:
                    continue
                host = (parsed.hostname or "").casefold()
                if host == "attachments.f95zone.to":
                    if value not in direct:
                        direct.append(value)
                elif (host == "f95zone.to" or host.endswith(".f95zone.to")) \
                        and cls._ATTACHMENT_PATH_RE.match(parsed.path):
                    if value not in pages:
                        pages.append(value)
        # 同一附件通常同时有直链和详情页；优先保留可直接预览的直链，避免重复计数。
        return direct or pages

    def _media_links(self, links: list[str]) -> tuple[list[str], bool]:
        """只保留文件分发链接，并用本机 F95 会话解开 masked URL。"""
        cookie = str(self.credential.values.get("cookie") or "") \
            if self.credential else ""
        media: list[str] = []
        needs_credential = False
        for link in links:
            if self._is_masked(link):
                target = self._resolve_masked(link, cookie) if cookie else None
                if target is None:
                    needs_credential = True
                    media.append(link)
                elif target not in media:
                    media.append(target)
                continue
            if _is_resource_url(link) and link not in media:
                media.append(link)
        return media, needs_credential

    @classmethod
    def _is_masked(cls, url: str) -> bool:
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            return False
        host = (parsed.hostname or "").casefold()
        return (parsed.scheme == "https"
                and (host == "f95zone.to" or host.endswith(".f95zone.to"))
                and bool(cls._MASKED_PATH_RE.match(parsed.path)))

    def _resolve_masked(self, url: str, cookie: str) -> str | None:
        # masked.js 使用同路径 XHR POST。失败只让这一条维持「需会话」，不能让整个
        # 线程的公开发现一起失败。
        try:
            response = self._post(
                url,
                b"xhr=1&download=1",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Cookie": cookie,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            if response.status != 200:
                return None
            payload = self.parse_json(response)
        except FollowSourceError:
            return None
        if not isinstance(payload, dict):
            return None
        target = str(payload.get("msg") or "")
        return target if payload.get("status") == "ok" and _is_resource_url(target) else None

    def thread_index(self, category: str, query: str) -> tuple[dict, ...]:
        """在 `latest_data.php` 里按名字查线程；用于登记订阅时确认 id 与版本。"""
        if category not in self.CATEGORIES:
            raise FollowSourceError(f"未知的 f95zone 分类：{category}")
        params = urllib.parse.urlencode({
            "cmd": "list", "cat": category, "page": "1",
            "search": query, "rows": "30",
        })
        url = f"https://f95zone.to/sam/latest_alpha/latest_data.php?{params}"
        response = self._get(url, headers={"Accept": "application/json"})
        self._check_status(response)
        payload = self.parse_json(response)
        rows = ((payload or {}).get("msg") or {}).get("data") or []
        return tuple(row for row in rows if isinstance(row, dict))

    def search_threads(self, query: str, *, title_only: bool = True) -> tuple[dict, ...]:
        """用站内搜索按标题找线程。

        `latest_data.php` 只索引 Latest Updates（游戏、动画、漫画、资产、mod）。
        艺术家的 Collection 帖发在普通版块，那份索引里根本没有：2026-09-01 实测
        `Ria_neearts` 在五个分类全为空，站内搜索一次就命中
        `/threads/ria-collection-2026-08-03-ria_neearts.146348/`。
        """
        cookie = self.credential.values.get("cookie") if self.credential else None
        if not cookie:
            raise CredentialError(
                "f95zone 站内搜索需要登录 cookie；请把它写进 "
                "peach-data/secrets/follow/f95zone.json")
        return _xenforo_search_threads(self, "f95zone.to", query,
                                       cookie=cookie, title_only=title_only)

    def thread_profile(self, thread: str) -> dict:
        """线程首楼里的标题与作者自己留下的官方主页链接。

        **需要登录。** 2026-09-12 实测游客态打得开首楼，但站外链接全被站点换成了
        `/login/`：`63802` 无 cookie 只剩三个登录跳转，带 cookie 才看得到
        `patreon.com/strauzek`、`twitter.com/strauzek` 与 `twitter.com/Mr_Strauz`。
        有的版块对游客整个关闭（`189698` 游客态回登录页）。所以缺 cookie 时直接说
        缺凭据，不要拿一个空清单冒充「这个作者没留主页」。

        读的是线程根页的第一楼，不是 `fetch` 那个 `/latest`——名片在首楼，
        最近回复里没有。
        """
        thread = (thread or "").strip()
        if not self._THREAD_RE.match(thread):
            raise FollowSourceError(f"f95zone 的 ref 必须是线程 id，收到：{thread!r}")
        cookie = self.credential.values.get("cookie") if self.credential else None
        if not cookie:
            raise CredentialError(
                "读 f95zone 首楼的作者主页链接需要登录 cookie；请把它写进 "
                "peach-data/secrets/follow/f95zone.json")
        response = self._get(f"https://f95zone.to/threads/{thread}/",
                             headers={"Accept": "text/html", "Cookie": cookie})
        self._check_status(response)
        soup = BeautifulSoup(response.body, "html.parser")
        opening = soup.select_one("article.message")
        body = opening.select_one(".message-userContent") if opening else None
        return {"title": _xenforo_thread_title(soup) or "",
                "links": official_profile_links(body, forum_host="f95zone.to")}


class FanboxConnector(_BaseConnector):
    """pixivFANBOX 官方公开帖子列表。

    只保留 `feeRequired=0` 且没有受限的帖子；付费标题可以被公开接口看见，
    但这条来源的用途是跟踪作者直接公开分发的内容，不能把付费预告混进来。
    """

    provider = "fanbox"
    #: 2026-08-27 实测公开接口单页 10 条，整页都能补上。
    DEFAULT_ENRICH_BUDGET = 10
    #: 详情补齐一轮的标记：补全阶段在 extra 里写 `media_dims`。拿新键当判据，
    #: 缺键的行下一轮检查会各重探一次详情。媒体清单现在是带封面和固有宽高的
    #: 完整形状，按旧键判成已补齐的行只有重探才能拿到这个形状。
    ENRICHED_MARK = "media_dims"
    _CREATOR_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        # 作者主页有两种形状：`creator.fanbox.cc` 子域，和 `fanbox.cc/@creator`。
        if host.endswith(".fanbox.cc"):
            creator = host.removesuffix(".fanbox.cc")
        else:
            matched = _FANBOX_AT_RE.match(parsed.path or "/")
            creator = matched.group(1) if matched else ""
        if not creator:
            raise FollowSourceError(
                "FANBOX 的链接要指向创作者主页，形如 https://creator.fanbox.cc/")
        return ParsedSource("fanbox", creator, f"https://{creator}.fanbox.cc/",
                            creator)

    @classmethod
    def profile_handle(cls, ref: str) -> str:
        return str(ref or "").strip()

    _IMPERSONATION = "chrome150"

    def __init__(self, *, detail_transport: HttpTransport | None = None, **kwargs):
        injected_transport = kwargs.get("transport")
        super().__init__(**kwargs)
        # 测试或调用方显式注入 transport 时保持单一边界；正式运行只把容易被
        # TLS/HTTP2 指纹拦截的 post.info 切到浏览器传输，公开列表仍复用 HTTPX。
        self.detail_transport = (
            detail_transport or injected_transport
            or CurlCffiTransport(impersonate=self._IMPERSONATION)
        )

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        headers.update({
            "Accept": "application/json",
            "Origin": "https://www.fanbox.cc",
            "Referer": "https://www.fanbox.cc/",
            "User-Agent": USER_AGENT,
        })
        if self.credential and self.credential.values.get("cookie"):
            headers["Cookie"] = self.credential.values["cookie"]
        return headers

    def fetch(self, ref: str, *, etag: str | None = None,
              last_modified: str | None = None, page: int = 0) -> SourceFetch:
        creator = str(ref or "").strip()
        if not self._CREATOR_RE.fullmatch(creator):
            raise FollowSourceError(f"fanbox 的 ref 必须是创作者 id，收到：{ref!r}")
        # 2026-08-27 实测公开接口单页为 10 条；不要把本地通用上限 100 原样塞给站点。
        query = urllib.parse.urlencode({"creatorId": creator,
                                        "limit": min(self.max_items, 10)})
        url = f"https://api.fanbox.cc/post.listCreator?{query}"
        if page:
            # 历史页是另一份资源，没有配过条件的缓存凭据，不带 If-None-Match。
            common, response = self._request(self._history_page_url(creator, page),
                                             ref=creator)
        else:
            common, response = self._request(url, ref=creator, etag=etag,
                                             last_modified=last_modified)
        if response is None:
            return SourceFetch(not_modified=True, **common)
        payload = self.parse_json(response)
        posts = ((payload or {}).get("body") or {}).get("posts")
        if not isinstance(posts, list):
            raise FollowSourceError("fanbox 返回的帖子列表格式不符")
        if page and not posts:
            raise FollowHistoryEnd("没有更多历史内容")
        listed, skipped = [], 0
        for post in posts[:self.max_items]:
            if not isinstance(post, dict) or not str(post.get("id") or "").isdigit():
                continue
            if post.get("isRestricted") or int(post.get("feeRequired") or 0) > 0:
                skipped += 1
                continue
            listed.append(self._listed(post, creator))
        # 第二阶段：正文外链、按正文顺序排列的多图和正文类型都只有 post.info 给。
        candidates, probed = self.enrich(listed)
        return SourceFetch(candidates=candidates, skipped=skipped,
                           probed=probed, raw_body=response.body, **common)

    def _history_page_url(self, creator: str, page: int) -> str:
        """第 page 页历史（page 从 1 起）对应的站点游标地址。

        站点自己的分页是 `post.paginateCreator`：先给一份按新到旧排序的游标页
        清单，每张页 URL 用 `firstPublishedDatetime`+`firstId` 定位、含端点——
        第 1 张就是最新一页，往后逐页更旧。公开接口不认 `offset` 参数，数字
        偏移翻不动页。游标清单走完（page 超出张数）就是没有更多历史。
        """
        common, response = self._request(
            "https://api.fanbox.cc/post.paginateCreator?"
            + urllib.parse.urlencode({"creatorId": creator}), ref=creator)
        if response is None:
            raise FollowSourceError("fanbox 历史页清单意外返回 304")
        payload = self.parse_json(response)
        pages = ((payload or {}).get("body") or {}).get("pageUrls")
        if not isinstance(pages, list) or not pages:
            raise FollowSourceError("fanbox 的历史页清单格式不符")
        if not 0 < page <= len(pages):
            raise FollowHistoryEnd("没有更多历史内容")
        return str(pages[page - 1])

    def _listed(self, post: dict, creator: str) -> FollowCandidate:
        """只用 post.listCreator 给的字段造一条候选。不联网。

        `extra` 里刻意**不放**详情才知道的键：`partial` 行落库走 `json_patch`，
        而补丁里的 null 是「删掉这个键」，写进去就会把上一轮取到的媒体清单抹掉。
        """
        post_id = str(post["id"])
        cover = post.get("cover") if isinstance(post.get("cover"), dict) else {}
        user = post.get("user") if isinstance(post.get("user"), dict) else {}
        return FollowCandidate(
            provider=self.provider,
            external_id=post_id,
            title=plain_text(str(post.get("title") or "")) or f"FANBOX 帖子 {post_id}",
            url=f"https://{creator}.fanbox.cc/posts/{post_id}",
            thumb_url=str(cover.get("url")) if cover.get("url") else None,
            published_at=_iso_from_text(post.get("publishedDatetime")),
            author=plain_text(str(user.get("name") or "")),
            summary=plain_text(str(post.get("excerpt") or "")),
            group_hint=f"fanbox:{post_id}",
            partial=True,
            extra={"fee_required": 0, "official": True},
        )

    def _enrich_one(self, candidate: FollowCandidate) -> FollowCandidate:
        # 创作者 id 从候选自己的地址推回来，不额外存进 metadata：那是同一个事实
        # 的第二份副本，而 `url` 本来就是 `https://<creator>.fanbox.cc/posts/<id>`。
        host = str(urllib.parse.urlsplit(str(candidate.url or "")).hostname or "")
        try:
            detail = self._post_detail(str(candidate.external_id), host.split(".")[0])
        except FollowSourceError as error:
            # FANBOX 会把单篇 post.info 临时换成 Cloudflare 验证页。列表本身仍是
            # 可信的公开更新：保留卡片、标明媒体未取得，并留着 `partial` 让下一轮
            # 再试——不能让一篇详情失败拖垮整个作者来源，也不能就此当作已补齐。
            return replace(candidate, extra={**candidate.extra,
                                             "media_error": str(error)})
        links = detail["links"]
        direct_media = detail["media_items"]
        gofile_media = self._gofile_media(
            links, labels=folder_labels(str(detail.get("summary") or "")))
        media_items = tuple(direct_media) + gofile_media
        return replace(
            candidate,
            partial=False,
            # 正文里的首图比列表封面准：封面是作者选的展示图，不一定是这篇的内容。
            # FANBOX 视频通常没有 `thumbnailUrl`。此时不能把视频地址当
            # 图片 poster；保留列表接口提供的 cover，关注页才能显示缩略图。
            thumb_url=(str(direct_media[0].get("thumb_url") or "")
                       or candidate.thumb_url
                       if direct_media else candidate.thumb_url),
            summary=detail["summary"] or candidate.summary,
            extra={**candidate.extra, "links": links, "media_items": media_items,
                   "media_error": None,
                   "media_dims": True,
                   "post_type": detail.get("post_type"),
                   "image_count": detail.get("image_count", 0),
                   "video_count": detail.get("video_count", 0),
                   "file_count": detail.get("file_count", 0),
                   "gofile_video_count": sum(
                       item.get("media_kind") == "video" for item in gofile_media)},
        )

    def _post_detail(self, post_id: str, creator: str) -> dict[str, object]:
        """公开详情补全正文外链和按正文顺序排列的多图。"""
        url = "https://api.fanbox.cc/post.info?" + urllib.parse.urlencode({"postId": post_id})
        headers = self._headers()
        headers["Referer"] = f"https://www.fanbox.cc/@{creator}/posts/{post_id}"
        try:
            response = self.detail_transport(
                HttpRequest("GET", url, headers), self.timeout, self.max_bytes)
        except (OSError, httpx.HTTPError) as exc:
            raise FollowSourceError("fanbox 请求失败") from exc
        if len(response.body) > self.max_bytes:
            raise FollowSourceError("fanbox 响应超出大小上限")
        self._check_status(response)
        payload = self.parse_json(response)
        if isinstance(payload, dict) and payload.get("error"):
            raise FollowSourceError(f"fanbox 帖子详情返回错误：{payload['error']}")
        body = (payload or {}).get("body") if isinstance(payload, dict) else None
        post = body.get("post") if isinstance(body, dict) else None
        if not isinstance(post, dict):
            raise FollowSourceError("fanbox 帖子详情格式不符")
        if post.get("isRestricted") or int(post.get("feeRequired") or 0) > 0:
            raise FollowSourceError("fanbox 帖子详情不是公开免费正文")
        try:
            content = normalize_fanbox_post(post)
        except FanboxContentError as exc:
            raise FollowSourceError(str(exc)) from exc
        links = resource_links("\n".join((content.summary, *content.links)))
        return {
            "summary": plain_text(content.summary),
            "links": links,
            "media_items": content.media_items,
            "post_type": content.post_type,
            "image_count": content.image_count,
            "video_count": content.video_count,
            "file_count": content.file_count,
        }


def _visible_post_image(node) -> str | None:
    image = node.select_one("img[src]") if node is not None else None
    return str(image.get("src")) if image is not None and image.get("src") else None


class SubscribeStarConnector(_BaseConnector):
    """SubscribeStar 的公开创作者页；不登录，也不穿过付费墙。"""

    provider = "subscribestar"
    HOSTS = frozenset({"subscribestar.adult", "subscribestar.com"})
    _SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

    #: 这些首段路径是站点自己的功能页，不是创作者。
    _RESERVED = frozenset({"posts", "search", "login", "signup"})

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        matched = _DIRECT_CREATOR_RE.match(parsed.path or "/")
        if not matched or matched.group(1) in cls._RESERVED:
            raise FollowSourceError(
                "SubscribeStar 的链接要指向创作者主页，形如 "
                "https://subscribestar.adult/creator")
        slug = matched.group(1)
        # ref 带上主机名：`.adult` 与 `.com` 是两个站，同名创作者不一定是一个人。
        return ParsedSource("subscribestar", f"{host}/{slug}",
                            f"https://{host}/{slug}", slug)

    @classmethod
    def profile_handle(cls, ref: str) -> str:
        # ref 带着站点主机名（`subscribestar.adult/initiala`），手柄只是最后那截。
        return str(ref or "").strip().rsplit("/", 1)[-1]

    @classmethod
    def _split_ref(cls, ref: str) -> tuple[str, str]:
        host, _, slug = str(ref or "").strip().partition("/")
        if host not in cls.HOSTS or not cls._SLUG_RE.fullmatch(slug):
            raise FollowSourceError(
                "subscribestar 的 ref 必须形如 subscribestar.adult/creator")
        return host, slug

    def fetch(self, ref: str, *, etag: str | None = None,
              last_modified: str | None = None, page: int = 0) -> SourceFetch:
        host, slug = self._split_ref(ref)
        if page:
            raise FollowSourceError("SubscribeStar 官方来源暂不支持向前翻页")
        url = f"https://{host}/{slug}"
        common, response = self._request(url, ref=ref, etag=etag,
                                         last_modified=last_modified,
                                         headers={"Accept": "text/html"})
        if response is None:
            return SourceFetch(not_modified=True, **common)
        try:
            soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
        except UnicodeDecodeError as exc:
            raise FollowSourceError("subscribestar 返回的页面不是 UTF-8") from exc
        candidates = []
        for post in soup.select("div.post[data-id]")[:self.max_items]:
            post_id = str(post.get("data-id") or "")
            if not post_id.isdigit():
                continue
            title_node = post.select_one(".post-title h2")
            date_node = post.select_one(".post-date a[href]")
            author_node = post.select_one(".post-user")
            title = plain_text(title_node.get_text(" ") if title_node else "")
            published = _iso_from_text(date_node.get_text(" ") if date_node else "")
            if published is None and date_node is not None:
                try:
                    parsed = datetime.strptime(
                        date_node.get_text(" ").strip(), "%b %d, %Y %I:%M %p")
                    published = _iso_utc(parsed)
                except ValueError:
                    pass
            candidates.append(FollowCandidate(
                provider=self.provider,
                external_id=post_id,
                title=title or f"SubscribeStar 帖子 {post_id}",
                url=f"https://{host}/posts/{post_id}",
                thumb_url=_visible_post_image(post.select_one(".post-uploads")),
                published_at=published,
                author=plain_text(author_node.get_text(" ") if author_node else ""),
                summary=plain_text(
                    post.select_one(".post-content").get_text(" ")
                    if post.select_one(".post-content") else ""),
                group_hint=f"subscribestar:{post_id}",
                extra={"official": True, "published_precision": "approximate"},
            ))
        return SourceFetch(candidates=tuple(candidates), raw_body=response.body, **common)


class PatreonConnector(_BaseConnector):
    """Patreon 公开创作者页。

    Patreon 的正式 posts API 要创作者 OAuth scope，不能用于任意作者；这里仅读取官网
    已经服务端渲染给公开访客的帖子卡片，不碰登录态或私有 API。
    """

    provider = "patreon"
    _VANITY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
    _POST_RE = re.compile(r"/posts/(?:[^/?#]*-)?(\d{4,})(?:[/?#]|$)")

    #: 这些首段路径是站点自己的功能页，不是创作者短名。
    _RESERVED = frozenset({"posts", "join", "login", "signup", "home", "explore"})

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        path = parsed.path or "/"
        user_id = urllib.parse.parse_qs(parsed.query).get("u", [""])[0]
        if path.rstrip("/") == "/user" and user_id.isdigit():
            return ParsedSource("patreon", f"user/{user_id}",
                                f"https://www.patreon.com/user?u={user_id}",
                                f"Patreon {user_id}")
        parts = [part for part in path.split("/") if part]
        if parts and parts[0] == "cw":
            parts = parts[1:]
        if not parts or parts[0].lower() in cls._RESERVED:
            raise FollowSourceError(
                "Patreon 的链接要指向创作者主页，形如 https://patreon.com/cw/creator")
        vanity = parts[0]
        return ParsedSource("patreon", vanity,
                            f"https://www.patreon.com/cw/{vanity}", vanity)

    @classmethod
    def profile_handle(cls, ref: str) -> str:
        value = str(ref or "").strip().strip("/")
        if value.startswith("user/") or value.isdigit():
            # 数字用户 id 页没有短名，`user/12345` 不是作者的名字。
            return ""
        return value.rsplit("/", 1)[-1]

    def _url(self, ref: str) -> str:
        value = str(ref or "").strip()
        if value.startswith("user/") and value[5:].isdigit():
            return f"https://www.patreon.com/user?u={value[5:]}"
        if not self._VANITY_RE.fullmatch(value):
            raise FollowSourceError(f"patreon 的 ref 必须是创作者短名，收到：{ref!r}")
        return f"https://www.patreon.com/cw/{value}"

    def fetch(self, ref: str, *, etag: str | None = None,
              last_modified: str | None = None, page: int = 0) -> SourceFetch:
        if page:
            raise FollowSourceError("Patreon 官方来源暂不支持向前翻页")
        url = self._url(ref)
        common, response = self._request(url, ref=ref, etag=etag,
                                         last_modified=last_modified,
                                         headers={"Accept": "text/html"})
        if response is None:
            return SourceFetch(not_modified=True, **common)
        try:
            soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
        except UnicodeDecodeError as exc:
            raise FollowSourceError("patreon 返回的页面不是 UTF-8") from exc
        candidates, seen = [], set()
        for anchor in soup.select("a[href*='/posts/']"):
            href = str(anchor.get("href") or "")
            matched = self._POST_RE.search(urllib.parse.urlsplit(href).path)
            if not matched or matched.group(1) in seen:
                continue
            post_id = matched.group(1)
            seen.add(post_id)
            node = anchor
            for _ in range(8):
                if node is None or node.select_one("h3") is not None:
                    break
                node = node.parent
            title_node = node.select_one("h3") if node is not None else None
            title = plain_text(title_node.get_text(" ") if title_node else "")
            if not title:
                slug = urllib.parse.urlsplit(href).path.rsplit("/", 1)[-1]
                title = _slug_label(re.sub(rf"-{post_id}$", "", slug))
            text = plain_text(node.get_text(" ") if node is not None else "") or ""
            relative = _RELATIVE_RE.search(text)
            candidates.append(FollowCandidate(
                provider=self.provider,
                external_id=post_id,
                title=title or f"Patreon 帖子 {post_id}",
                url=urllib.parse.urljoin("https://www.patreon.com", href),
                thumb_url=_visible_post_image(node),
                published_at=_iso_from_relative(relative.group(0)) if relative else None,
                group_hint=f"patreon:{post_id}",
                extra={"official": True, "public_page": True,
                       "published_precision": "approximate"},
            ))
            if len(candidates) >= self.max_items:
                break
        return SourceFetch(candidates=tuple(candidates), raw_body=response.body, **common)


class SimpCityConnector(_BaseConnector):
    """simpcity.cr 的线程追更。

    站点前面是 DDoS-Guard。2026-09-08 实测它对 Peach 的标准桌面 UA 不出质询：首页、
    版块列表和登录页在无 cookie 下直接 200。帖子页 403 不是机器人拦截，而是站点
    「游客不可读帖」的访问规则——所以这里**必须**带用户自己的登录 cookie，而 Peach
    仍然不解任何质询：cookie 不对就把 403 原样报出来，不重试、不换指纹。

    **只请求不重定向的规范地址。** XenForo 的 `/latest` 会 303 到末页，而 HTTPX 跟随
    重定向时会丢掉显式的 `Cookie` 头：同一份 cookie 直接请求目标页是 200，经重定向
    到达就是 403（2026-09-08 实测）。所以末页由自己算——先读第一页的分页导航取末页号，
    末页不是第一页时再读 `page-N`；一次检查最多两个请求。

    cookie 只发回 simpcity.cr。帖子里的图站与网盘链接只记录，不在这里去取。
    `ref` 是线程 id，例如 `21229`。
    """

    provider = "simpcity"
    HOST = "simpcity.cr"
    _THREAD_RE = re.compile(r"^\d{1,12}$")
    _PAGE_HREF_RE = re.compile(r"/page-(\d+)(?:[?#]|$)")
    _ATTACHMENT_PATH_RE = re.compile(r"^/attachments/[^/?#]+/?$", re.IGNORECASE)

    @classmethod
    def parse_url(cls, provider: str, parsed: urllib.parse.SplitResult,
                  host: str) -> "ParsedSource":
        path = parsed.path or "/"
        matched = _THREAD_PATH_RE.match(path)
        if not matched:
            raise FollowSourceError(
                "simpcity 的链接要指向一个线程，形如 "
                "https://simpcity.cr/threads/xxx.21229/")
        thread = matched.group(1)
        segment = path.split("/threads/", 1)[1].split("/", 1)[0]
        slug = segment.rsplit(".", 1)[0] if "." in segment else ""
        return ParsedSource("simpcity", thread,
                            f"https://{cls.HOST}/threads/{thread}/",
                            _slug_label(slug) or f"线程 {thread}")

    def _cookie(self) -> str:
        if self.credential is None:
            raise CredentialError(
                "simpcity 需要登录 cookie：站点不让游客读帖。登录后把浏览器里 "
                "simpcity.cr 的整条 Cookie 请求头写进凭据文件的 cookie 字段。")
        return self.credential.require("cookie")[0]

    def _check_status(self, response: HttpResponse) -> None:
        if response.status == 403:
            raise FollowSourceError(
                "simpcity 拒绝访问（HTTP 403）：站点不让游客读帖，cookie 可能已过期或"
                "不完整，请重新登录后更新凭据文件")
        super()._check_status(response)

    def search_threads(self, query: str, *, title_only: bool = True) -> tuple[dict, ...]:
        """用站内搜索按标题找线程；simpcity 没有别的索引，这是唯一一条路。

        2026-09-08 实测 `solazola` 按标题命中两条：`OnlyFans` 版块的
        `solazola-baby_sue.17401` 和 `Simp Chat` 版块的 `solazola-discussion.392510`。
        正文全文搜会把「Who is this?」这类提到名字的帖子一起带回来，所以默认只搜标题，
        版块标签跟着每行回去让人自己分。
        """
        return _xenforo_search_threads(self, self.HOST, query,
                                       cookie=self._cookie(), title_only=title_only)

    def fetch(self, ref: str, *, etag: str | None = None,
              last_modified: str | None = None, page: int = 0) -> SourceFetch:
        thread = (ref or "").strip()
        if not self._THREAD_RE.match(thread):
            raise FollowSourceError(f"simpcity 的 ref 必须是线程 id，收到：{ref!r}")
        headers = {"Accept": "text/html", "Cookie": self._cookie()}
        base = f"https://{self.HOST}/threads/{thread}/"
        # 第一页不带条件请求头：新回复长在末页，第一页没变不代表线程没更新。
        common, response = self._request(base, ref=thread, headers=headers)
        soup = self._page(response)
        target = self._last_page(soup) - int(page or 0)
        if target < 1:
            raise FollowHistoryEnd("没有更多历史内容")
        if target != 1:
            common, response = self._request(
                f"{base}page-{target}", ref=thread, etag=etag,
                last_modified=last_modified, page=page, headers=headers)
            if response is None:
                return SourceFetch(not_modified=True, **common)
            soup = self._page(response)
        title = _xenforo_thread_title(soup) or f"thread {thread}"
        candidates, parsed, skipped = self._posts(soup, thread, title, target)
        if not parsed:
            raise FollowSourceError("simpcity 线程页没有解析出任何楼层：页面结构可能已变")
        return SourceFetch(candidates=tuple(candidates), skipped=skipped,
                           raw_body=response.body, **common)

    @staticmethod
    def _page(response: HttpResponse):
        """解析一页并确认站点认出了登录态。

        游客态的帖子页是 403；能拿到 200 却写着 `data-logged-in="false"` 只会发生在
        cookie 被部分接受时，这时候正文是残缺的，与其解析出一堆「什么都没有」不如
        直接说 cookie 没被认出。
        """
        soup = BeautifulSoup(response.body, "html.parser")
        root = soup.find("html")
        if root is not None and str(root.get("data-logged-in") or "").lower() == "false":
            raise FollowSourceError(
                "simpcity 没有认出这份 cookie（页面仍是游客态）：请重新登录后复制整条 "
                "Cookie 请求头")
        return soup

    @classmethod
    def _last_page(cls, soup) -> int:
        """分页导航里最大的页号；单页线程没有导航，就是第 1 页。"""
        last = 1
        for node in soup.select(".pageNav-page a[href]"):
            matched = cls._PAGE_HREF_RE.search(str(node.get("href") or ""))
            if matched:
                last = max(last, int(matched.group(1)))
        return last

    def _posts(self, soup, thread: str, title: str, page_number: int):
        candidates: list[FollowCandidate] = []
        parsed = 0
        skipped = 0
        for article, post_id, time_node, body in _xenforo_posts(soup, self.max_items):
            parsed += 1
            images = self._images(body)
            file_links: list[str] = []
            for node in (body.select("a[href]") if body else []):
                link = self._external_href(str(node.get("href") or ""))
                if _is_resource_url(link) and link not in file_links:
                    file_links.append(link)
            attachments = self._attachments(body)
            embeds = len(body.select("[data-s9e-mediaembed], .bbMediaWrapper iframe")) \
                if body else 0
            # 图、网盘、附件、嵌入播放器四样都没有的楼层是纯讨论，不进追更。
            if not (images or file_links or attachments or embeds):
                skipped += 1
                continue
            # 图片才是能在详情页看的媒体；网盘链接只做资源按钮，没有图时才顶上来
            # 当外链媒体，免得一条网盘链接把整层的图挤成缩略图。
            media_url = images[0][0] if images else (file_links[0] if file_links else None)
            candidates.append(FollowCandidate(
                provider=self.provider,
                external_id=post_id,
                title=title,
                url=f"https://{self.HOST}/threads/{thread}/post-{post_id}",
                media_url=media_url,
                thumb_url=images[0][1] if images else None,
                published_at=_iso_from_text(time_node.get("datetime"))
                if time_node is not None else None,
                author=plain_text(str(article.get("data-author") or "")) or None,
                summary=(plain_text(body.get_text(" ")) or None) if body else None,
                extra={"thread_id": thread, "page": page_number,
                       "images": [full for full, _ in images[:40]],
                       "image_count": len(images),
                       "links": file_links[:8], "link_count": len(file_links),
                       "attachments": attachments[:8],
                       "attachment_count": len(attachments),
                       "embed_count": embeds},
            ))
        return candidates, parsed, skipped

    @classmethod
    def _external_href(cls, href: str) -> str:
        """把站点的跳转链接还原成真实外链。

        simpcity 给每个外链套一层 `/redirect/?to=<base64url>&e=1&m=b64`（2026-09-08 实测
        `aHR0cHM6Ly9waXhlbGRyYWluLmNvbS91L3pGM1BxVEpG` 即 `https://pixeldrain.com/u/zF3PqTJF`）。
        不还原的话网盘链接一个都认不出来。只接受解出来是 http(s) 地址的；解不开就原样
        返回，让后面的域名判定自己拒掉。
        """
        try:
            parsed = urllib.parse.urlsplit(href)
        except ValueError:
            return href
        if parsed.path.rstrip("/") != "/redirect" or (parsed.netloc and parsed.netloc != cls.HOST):
            return href
        query = urllib.parse.parse_qs(parsed.query)
        encoded = (query.get("to") or [""])[0]
        if not encoded:
            return href
        try:
            decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)) \
                .decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return href
        return decoded if decoded.startswith(("https://", "http://")) else href

    @classmethod
    def _images(cls, body) -> list[tuple[str, str]]:
        """正文里的图片：`(原图, 缩略图)`。

        2026-09-08 实测：图片是 `img.bbImage`，`data-url` 指原图、`src` 指缩略图，
        外面套着图站页面的链接。表情是 `img.smilie`，不带 bbImage，选择器天然排除。
        """
        if body is None:
            return []
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for node in body.select("img.bbImage"):
            full = str(node.get("data-url") or node.get("data-src") or node.get("src") or "")
            thumb = str(node.get("src") or full)
            if not full.startswith("https://") or full in seen:
                continue
            host = (urllib.parse.urlsplit(full).hostname or "").casefold()
            if host == cls.HOST or host.endswith("." + cls.HOST):
                continue
            seen.add(full)
            result.append((full, thumb if thumb.startswith("https://") else full))
        return result

    @classmethod
    def _attachments(cls, body) -> list[str]:
        """站内附件链接，统一写成绝对地址；取附件同样需要登录，这里只登记。"""
        if body is None:
            return []
        found: list[str] = []
        for node in body.select("a[href]"):
            value = str(node.get("href") or "").strip()
            if value.startswith("/"):
                value = f"https://{cls.HOST}{value}"
            try:
                parsed = urllib.parse.urlsplit(value)
            except ValueError:
                continue
            if (parsed.scheme == "https" and parsed.hostname == cls.HOST
                    and cls._ATTACHMENT_PATH_RE.match(parsed.path) and value not in found):
                found.append(value)
        return found


def parse_source_url(raw_url: str) -> ParsedSource:
    """把一条来源链接认成可登记的订阅。

    只做纯解析，不联网。这里只管链接本身是否合法、以及这个主机属于哪个站；每个站
    的 URL 形状归它自己的连接器（`parse_url`）。认不出来就抛 `FollowSourceError`
    并说清楚支持哪些形状——与其静默登记一个永远抓不到东西的来源，不如当场说不认识。
    """
    text = (raw_url or "").strip()
    if not text:
        raise FollowSourceError("请先粘贴一条来源链接")
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError as exc:
        raise FollowSourceError("这不是一条合法链接") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FollowSourceError("只接受 http(s) 链接")
    if parsed.username is not None or parsed.password is not None:
        raise FollowSourceError("链接里不能带账号密码")
    host = parsed.hostname.lower().removeprefix("www.")

    provider = follow_providers.provider_for_host(host)
    factory = CONNECTORS.get(provider)
    if factory is None:
        raise FollowSourceError(
            f"不认识 {host}。当前支持 " + "、".join(sorted(_URL_HOSTS)) + "。")
    return factory.parse_url(provider, parsed, host)


#: 全部站点连接器。来源键由每个类自己声明，所以 kemono 系三站不再在映射里把
#: 同一个类写三遍——那份重复和 `KemonoConnector.HOSTS` 是同一件事的两份手写。
_CONNECTOR_CLASSES: tuple[type, ...] = (
    FanboxConnector, PatreonConnector, SubscribeStarConnector, KemonoConnector,
    Rule34VideoConnector, Rule34XxxConnector, Rule34PahealConnector,
    F95ZoneConnector, SimpCityConnector,
)

CONNECTORS: dict[str, type] = {
    key: factory for factory in _CONNECTOR_CLASSES for key in factory.provider_keys()
}


def display_thumb_url(item) -> str | None:
    """一条已入库的追更条目现在该用哪个缩略图 URL。

    分派到该站自己的连接器：「这个站的缩略图 URL 长什么样」是站点知识，属于连接器，
    不属于 Web 层。没登记的 provider 照原样用。
    """
    factory = CONNECTORS.get(str(item.provider or ""))
    if factory is None:
        return str(item.thumb_url or "") or None
    return factory.display_thumb_url(item)


def media_content_hash(provider: str, url: str | None) -> str | None:
    """这个来源的一条媒体地址里带的文件内容哈希（`算法:十六进制`），没有就是 None。

    只解析已经存下的地址，不发请求。判据归各站连接器：哪些站按内容哈希命名文件是
    站点知识。
    """
    factory = CONNECTORS.get(str(provider or ""))
    return factory.content_hash(url) if factory is not None else None


def is_history_end_error(provider: str, message: str) -> bool:
    """一句已落盘的错误文本其实是「往回翻到尽头」吗？

    判据是各连接器声明的 `HISTORY_END_STATUSES`，和翻页时现场判定的用同一份声明。
    """
    factory = CONNECTORS.get(str(provider or ""))
    return factory is not None and factory.is_history_end_error(message)


def enrichment_mark(provider: str) -> str:
    """这个来源第二阶段补齐之后 ledger 上哪一处会有值；没有第二阶段就是空串。

    调用方用它决定要不要去 ledger 里算跳过集合，以及按哪一列算。
    """
    factory = CONNECTORS.get(str(provider or ""))
    return str(getattr(factory, "ENRICHED_MARK", "") or "") if factory else ""


def official_profile_handle(provider: str, ref: str) -> str:
    """这条来源的 ref 里那个可信的作者手柄，没有就返回空串。

    分派到该站自己的连接器：ref 的形状是站点知识。
    """
    factory = CONNECTORS.get(str(provider or ""))
    return factory.profile_handle(ref) if factory is not None else ""


def build_connector(provider: str, **kwargs) -> _BaseConnector:
    factory = CONNECTORS.get(provider)
    if factory is None:
        raise FollowSourceError(f"未知的追更来源：{provider}")
    # 服务多个来源键的连接器（kemono 系）必须知道自己这次代表哪个站。
    if len(factory.provider_keys()) > 1:
        return factory(provider=provider, **kwargs)
    return factory(**kwargs)
