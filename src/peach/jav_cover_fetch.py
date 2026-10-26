#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""按番号抓官方封套：把所有候选量一遍，留像素最多的那张。

**不用固定优先级链**，因为实测证明没有哪个源恒定最好：

    GYAN-017   awsimgsrc 2184x1464   有数字版，DMM 自己的高清路径最好
    ABW-232    duga      1000x674    没有数字版，awsimgsrc 四种写法全 404
    PPT-018    pics.dmm   800x539    只有低清

同一个 DMM 有两条路径，差 7.4 倍像素——之前一直用的是低清那条：

    低清  pics.dmm.co.jp/mono/movie/adult/<cid>/<cid>pl.jpg            800x539
    高清  awsimgsrc.dmm.co.jp/pics_dig/digital/video/<cid>/<cid>pl.jpg 2184x1464

候选不是固定优先级，而是汇总后量像素：

- 已保存的 Javinizer-Go 原始证据，离线复用官方与官方镜像快照的 cover URL 与 content_id
  （JavBus、javdb 这类社区站的快照只借厂牌选渠道，它们的图要走社区来源的图源印证）；
- r18.dev 官方 DMM jacket（本地没有成功快照时才联网补）；
- DMM 新旧 awsimgsrc CDN 的 digital/video、digital/amateur、mono/movie 路径；
- 有 Prestige 厂牌证据时，直连 Prestige API 与 MGS EnlargeImage；
- 上轮成功日志里的原 URL，保住已经发现但当前无法重新检索的 DUGA 等官方图。

批量流程不请求社区来源；采集任务在官方渠道落空时经 `peach.community_catalog` 去问
AVBase、JavBus 与 JavDB，封面先求两个图源比对一致；JavDB 只作比对证据（ADR-0107）。DUGA 批量搜索 API
需要代理店应用 ID，未配置前只复用成功日志中已经取得的精确图片 URL。

两条番号改写规则，都由实测得出：

- cid 数字段必须补到 5 位。`waaa415` 404，`waaa00415` 命中 2184x1468。
- 素人系要去掉三位厂牌前缀。`278GYAN-017` 查不到，`GYAN-017` 能查到。

为省流量，先用 Range 只取前 64 KiB 量尺寸，只有胜出的那张才整张下载。

存原图不裁：4:3 与 16:9 两种版式在界面上靠 `object-fit` / `object-position` 取景，
切版式零成本也不重新下载。官方那张独立正封 `ps.jpg` 只有 147x200，比裁出来的还小。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from peach.review_csv import ENCODING, read_rows, write_rows
from peach.scripting import USER_AGENT, open_readonly
from peach.config import COVER_DIR, DATABASE_PATH, GENERATED_DIR, SOURCES_DIR
from peach.http import HttpRequest, HttpTransport, HttpxTransport
from peach.scripting import HostLimiter
from peach.scraping_access import SOURCES, SourcePaused, SourceTransport, source_for
from peach.config import SECRETS_DIR
from peach.jobs import DiskGuard, JobPolicyError
from peach.task_runs import TaskRunHandle, cli_run, inert_handle
from peach.platform import system_volume
from peach.catalog_rules import (
    code_letter_stem,
    is_amateur_code,
    is_jav_code,
    is_korean_mib_code,
    is_uncensored_code,
    normalise_code_key,
)
from peach.metadata_policy import SOURCE_SPECS

AWS_LEGACY_DIGITAL = (
    "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/{cid}/{cid}pl.jpg"
)
AWS_MODERN = "https://awsimgsrc.dmm.com/dig/{kind}/{cid}/{cid}pl.jpg"
AWS_LEGACY = "https://awsimgsrc.dmm.co.jp/pics_dig/{kind}/{cid}/{cid}pl.jpg"
R18_DETAIL = "https://r18.dev/videos/vod/movies/detail/-/dvd_id={code}/json"
R18_COMBINED = "https://r18.dev/videos/vod/movies/detail/-/combined={content_id}/json"
MGS_DETAIL = "https://www.mgstage.com/product/product_detail/{code}/"
PRESTIGE_SEARCH = "https://www.prestige-av.com/api/search"
PRESTIGE_PRODUCT = "https://www.prestige-av.com/api/product/{uuid}"
PRESTIGE_MEDIA = "https://www.prestige-av.com/api/media/{path}"
DEFAULT_METADATA_ROOT = SOURCES_DIR / "metadata" / "javinizer-go"
DEFAULT_FC2_METADATA_LOG = GENERATED_DIR / "fc2-candidate-log.csv"

#: 低于这个宽度的是缩略图或占位图，不当封套。实测最低的正片封套是 800 宽。
MIN_WIDTH = 700
#: 采集任务找不到大图时退而求其次的下限。素人系官方图只有 300×300，有图比没图好；
#: 官方那张 147×200 的独立正封仍然太小，不当封面。
SMALL_MIN_WIDTH = 240
#: DMM 缺图时 302 到「NOW PRINTING」占位图（590×800），尺寸像一张正常封面。
PLACEHOLDER = re.compile(r"now_?printing", re.I)
#: 官方候选一张都量不出可用尺寸时的两种说法，下标是「有没有量到过偏小的图」。
NO_USABLE_OFFICIAL = ("官方封面地址都没有取到图片", "官方封面只有缩略图或占位图")
#: 占位图是来源自己说「没有图」：DMM 下架的作品两个版本都只剩这一张。
PLACEHOLDER_REASON = "来源给的是「准备中」占位图"
#: 官方候选里取到的只有占位图、没有偏小的真图时的说法。
OFFICIAL_PLACEHOLDER_ONLY = "官方封面地址只回「准备中」占位图，作品多半已从官方下架"
#: 韩国 MIB 的编号不在 JAV 目录站上，封面来源一律不问；记进日志算确认落空，不重探。
MIB_NOT_JAV = "韩国 MIB 不适用 JAV 封面来源"
#: 量尺寸只需要 JPEG 头部，别把整张 1 MB 的图拉下来。
PROBE_BYTES = 64 * 1024
# 瞬时 TLS EOF / 连接重置不能落成“官方没有封面”。按项目外网退避规则重试，
# 只有 2/4/6/8 秒四次重试全部失败后，才把网络异常交给逐条日志记录。
NETWORK_RETRY_DELAYS = (2, 4, 6, 8)
#: 页面与上游证据里可能混着剧照与缩略图，按文件名排除。
THUMBNAIL = re.compile(r"(thumb|small|icon|/ts/|-s\d|_s\.)", re.I)
FC2_HIRES = re.compile(r"https://contents-thumbnail\d*\.fc2\.com/w(?:7\d\d|[89]\d\d|\d{4,})/", re.I)
IMAGE_URL = re.compile(r"https?://[^\"'\\ )]+?\.(?:jpg|jpeg|png|webp)")


def is_javdb_image(url: str = '', source: str = '') -> bool:
    """JavDB 转存图片仅参与来源印证，不作为正式封面。"""
    from .sources.javdb import JAVDB
    domains = (*JAVDB.domains, 'tp.spfcas.com')
    for value in (url, source):
        value = str(value or '').strip().lower()
        if value in {'javdb', 'javdb-public', 'javdb_public'}:
            return True
        host = urlparse(value).hostname if '://' in value else value
        if host and any(host == domain or host.endswith('.' + domain)
                        for domain in domains):
            return True
    return False


@dataclass(frozen=True)
class Candidate:
    source: str
    url: str
    referer: str = "https://www.dmm.co.jp/"
    kind: str = "cover"


PRODUCT_IMAGE = "product"
PREVIEW_IMAGE = "preview"
COVER_IMAGE = "cover"
_CANDIDATE_QUALITY = {PREVIEW_IMAGE: 0, COVER_IMAGE: 1, PRODUCT_IMAGE: 2}


def candidate_quality(candidate: Candidate | None) -> int:
    """来源里的图片角色，供同尺寸候选择优；不按主机名整体提权。"""
    if candidate is None:
        return _CANDIDATE_QUALITY[PRODUCT_IMAGE]
    return _CANDIDATE_QUALITY.get(candidate.kind, _CANDIDATE_QUALITY[COVER_IMAGE])


def candidate_improves(candidate: Candidate, pixels: int,
                       baseline_quality: int, baseline_pixels: int) -> bool:
    """图片角色或像素数至少有一项严格改善，候选才算升级。"""
    return candidate_quality(candidate) > baseline_quality or pixels > baseline_pixels


@dataclass(frozen=True)
class MetadataEvidence:
    candidates: tuple[Candidate, ...] = ()
    makers: frozenset[str] = frozenset()
    sources: frozenset[str] = frozenset()


class Unavailable(RuntimeError):
    pass


class NotFound(Unavailable):
    """来源明确答复没有这个番号。

    与网络故障、来源限流、下载失败分开：后者下次再问可能就好了，前者短期内问多少次
    答案都一样，采集任务据此把它记住一阵子。
    """


class DeadlineExceeded(RuntimeError):
    """动作预算已用尽：调用方记录当前项目后继续下一项。"""


#: 候选一张都没连上时的说法。
HOSTS_UNREACHABLE = "官方图片主机都连不上"


class CoverConnectError(Unavailable):
    """量尺寸时每一张候选都断在连接上，没有一家回过话：不是来源说没有，是这条线路到不了。

    中国移动宽带直连 DMM 图片主机，九成监测点在握手后被断开（2026-09 实测），配了代理
    就好。所以这一类单独报出来，界面据此提示去配来源的连接方式；类名里的 Connect 也让
    续跑的 `TRANSIENT` 把它算作可重试，不当成「官方没有封面」。
    """


class HostLimitedTransport:
    """把请求间隔按主机分别计算；不同官方站点互不阻塞。"""

    def __init__(self, inner: HttpxTransport, interval: float, *,
                 intervals: dict[str, float] | None = None,
                 clock=time.monotonic, sleeper=time.sleep) -> None:
        self.inner = inner
        self.interval = max(0.0, interval)
        self.clock = clock
        self.sleeper = sleeper
        self.limiter = HostLimiter(dict(intervals or {}), default_interval=self.interval,
                                   clock=clock, sleeper=sleeper)

    def __call__(self, request: HttpRequest, timeout: float, limit: int):
        self.limiter.wait(request.url)
        return self.inner(request, timeout, limit)

    def renew(self) -> None:
        if hasattr(self.inner, "renew"):
            self.inner.renew()
            return
        self.inner.close()
        self.inner = HttpxTransport()

    def close(self) -> None:
        self.inner.close()


class _MGSDetailParser(HTMLParser):
    """只读取 MGS 的放大图链接；列表缩略图和剧照不进入候选。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str,
                        attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        url = None
        if tag.lower() == "a" and values.get("id") == "EnlargeImage":
            url = values.get("href")
        elif tag.lower() == "a" and "link_magnify" in (values.get("class") or "").split():
            url = values.get("href")
        elif tag.lower() == "img" and any(
                token in (values.get("src") or "").lower()
                for token in ("jacket", "cover")):
            url = values.get("src") or values.get("data-src")
            if url:
                url = url.replace("ps.", "pl.", 1)
        if url and (IMAGE_URL.fullmatch(url)
                    or re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?.*)?$", url, re.I)):
            self.urls.append(url)


def _remaining(deadline: float | None) -> float | None:
    return None if deadline is None else deadline - time.monotonic()


def _sleep_within(delay: float, deadline: float | None) -> None:
    """限速等待也服从截止时间，不让睡眠越过预算。"""
    remaining = _remaining(deadline)
    if remaining is not None and delay >= remaining:
        raise DeadlineExceeded("动作预算已用尽")
    time.sleep(delay)


#: 一条请求给传输的时限上限。
REQUEST_TIMEOUT = 30.0
#: 由本机浏览器过验证的来源（`scraping_access.SOURCES` 里登记了 `browser`）一条请求的时限，
#: 不按剩余预算往下裁。浏览器自动过验证最多等 `browser_transport.AUTO_SECONDS`（40 秒），
#: 2026-09-25 实测 JAVten 最慢 25 秒；时限给到 30 秒以下，自动阶段没等完就被掐断，那一站白问一次。
BROWSER_REQUEST_TIMEOUT = 45.0


def request_timeout(url: str, remaining: float | None) -> float:
    """这一条请求给传输多少秒：普通来源不超过剩余预算与 `REQUEST_TIMEOUT`，浏览器来源固定
    `BROWSER_REQUEST_TIMEOUT`——等浏览器过验证的那一段不归单项预算管（ADR-0065 第二条）。"""
    source = source_for(url)
    if source and SOURCES[source].get("browser"):
        return BROWSER_REQUEST_TIMEOUT
    return REQUEST_TIMEOUT if remaining is None else max(1.0, min(REQUEST_TIMEOUT, remaining))


def _fetch(transport: HttpTransport, url: str, *, referer: str,
           limit: int, ranged: bool = False,
           extra_headers: dict[str, str] | None = None,
           deadline: float | None = None, method: str = "GET",
           body: bytes | None = None) -> bytes:
    """取一页。`method` 与 `body` 给只收 POST 的接口（DMM 的 GraphQL）；重试、预算与分档同 GET。"""
    headers = {"User-Agent": USER_AGENT, "Referer": referer,
               "Accept-Language": "ja,en;q=0.9"}
    if extra_headers:
        headers.update(extra_headers)
    if ranged:
        headers["Range"] = f"bytes=0-{PROBE_BYTES - 1}"
    request = HttpRequest(method, url, headers, body)
    for attempt in range(len(NETWORK_RETRY_DELAYS) + 1):
        remaining = _remaining(deadline)
        if remaining is not None and remaining <= 0:
            raise DeadlineExceeded("动作预算已用尽")
        try:
            response = transport(request, request_timeout(url, remaining), limit)
            break
        except httpx.TransportError:
            if attempt == len(NETWORK_RETRY_DELAYS):
                raise
            _sleep_within(NETWORK_RETRY_DELAYS[attempt], deadline)
    final_url = getattr(response, "url", "")
    if isinstance(final_url, str) and PLACEHOLDER.search(final_url):
        raise NotFound(PLACEHOLDER_REASON)
    if response.status == 404:
        raise NotFound("HTTP 404")
    if response.status not in (200, 206):
        raise Unavailable(f"HTTP {response.status}")
    return response.body


def _probe_failure(error: Exception) -> str:
    """量尺寸失败记进诊断的哪一格：占位图单列，它是来源自己说没有图。"""
    return "placeholder" if isinstance(error, NotFound) and str(error) == PLACEHOLDER_REASON else "probe_failed"


def _no_usable_official(diagnostics: dict) -> str:
    """官方候选一张都量不出可用尺寸时的原因。"""
    if diagnostics.get("placeholder") and not diagnostics.get("too_small"):
        return OFFICIAL_PLACEHOLDER_ONLY
    return NO_USABLE_OFFICIAL[bool(diagnostics.get("too_small"))]


def code_variants(code: str) -> list[str]:
    """素人系番号带三位厂牌前缀，去掉才查得到：`278GYAN-017` -> `GYAN-017`。"""
    value = normalise_code_key(code)
    out = [value]
    stripped = re.sub(r"^\d{3}(?=[A-Z])", "", value)
    if stripped != value:
        out.append(stripped)
    return out


def cid_variants(content_id: str) -> list[str]:
    """数字段补到 5 位是必须的；带厂牌数字前缀的多半没有数字版，但仍试一次。"""
    cid = (content_id or "").strip().lower()
    if not cid:
        return []
    out = [cid]
    shape = re.match(r"^(\d{2,4})?([a-z]+)(\d+)$", cid)
    if shape:
        letters, digits = shape.group(2), shape.group(3)
        out.append(f"{letters}{int(digits):05d}")
        out.append(f"{letters}{digits}")
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _referer_for(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "mgstage.com" in host:
        return "https://www.mgstage.com/"
    if "prestige-av.com" in host:
        return "https://www.prestige-av.com/"
    if "duga.jp" in host:
        return "https://duga.jp/"
    return "https://www.dmm.co.jp/"


def candidate_for(url: str, *, kind: str | None = None) -> Candidate:
    parsed = urlparse(url)
    filename = Path(parsed.path).name.lower()
    if kind is None and "mgstage.com" in parsed.netloc.lower():
        if filename.startswith(("pb_e_", "pake-03_")):
            kind = PRODUCT_IMAGE
        elif "popsample" in filename:
            kind = PREVIEW_IMAGE
    return Candidate(parsed.netloc.lower(), url, _referer_for(url), kind or COVER_IMAGE)


def fc2_cover_candidates(path: Path | None) -> dict[str, Candidate]:
    """Reuse FC2CMADB article evidence, upgrading its listing thumb to measured w1200."""
    if path is None or not path.is_file():
        return {}
    found = {}
    for row in read_rows(path):
        url = re.sub(r"(/w)\d+(/)", r"\g<1>1200\2", str(row.get("cover_url") or ""), count=1)
        code = normalise_code_key(str(row.get("code") or ""))
        if (row.get("result") == "取得" and not row.get("is_collection")
                and code.startswith("FC2-PPV-") and IMAGE_URL.fullmatch(url)):
            found[code] = Candidate(urlparse(url).netloc.lower(), url, "https://fc2cmadb.com/")
    return found


def _unique_candidates(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    return [candidate for candidate in candidates
            if candidate.url and not (candidate.url in seen or seen.add(candidate.url))]


def dmm_cdn_images(url: str) -> list[Candidate]:
    """按 Javinizer-Go 当前已验证映射生成 DMM 新旧 CDN 候选。

    主机名和路径不能当清晰度：同一 CID 的 legacy、modern、原始 URL 都要量尺寸。
    """
    clean = (url or "").split("?", 1)[0]
    parsed = urlparse(clean)
    match = re.search(
        r"/(?:pics_dig/|dig/)?(?P<kind>digital/(?:video|amateur)|mono/movie)"
        r"(?:/adult)?/(?P<cid>[^/]+)/",
        parsed.path,
        re.I,
    )
    candidates = [candidate_for(clean)] if IMAGE_URL.fullmatch(clean) else []
    if not match:
        return candidates
    kind = match.group("kind").lower()
    cid = match.group("cid").lower()
    candidates.extend([
        candidate_for(AWS_MODERN.format(kind=kind, cid=cid)),
        candidate_for(AWS_LEGACY.format(kind=kind, cid=cid)),
    ])
    return _unique_candidates(candidates)


def official_image_candidates(url: str) -> list[Candidate]:
    """别处给的一个封面地址里，能当官方候选的那几条。

    只认 DMM 与 MGS 的官方图路径：DMM 路径带 content_id，`is_cross_product_cover` 能按它
    核对是不是这一部；MGS 按商品目录存图。社区站图床的地址不认——那边搜不到原番号时会给
    别的作品，和 `cached_metadata` 不收社区快照里的图是同一条理由。
    """
    clean = (url or "").split("?", 1)[0].strip()
    if not IMAGE_URL.fullmatch(clean):
        return []
    if _DMM_CID.search(urlparse(clean).path):
        return dmm_cdn_images(clean)
    if urlparse(clean).netloc.lower().endswith("mgstage.com"):
        return [candidate_for(clean)]
    return []


def content_id_images(content_id: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    for cid in cid_variants(content_id):
        candidates.extend([
            candidate_for(AWS_MODERN.format(kind="digital/video", cid=cid)),
            candidate_for(AWS_LEGACY_DIGITAL.format(cid=cid)),
        ])
    return _unique_candidates(candidates)


def cached_metadata(metadata_root: Path | None, code: str) -> MetadataEvidence:
    """复用已落盘的 Javinizer 原始快照，不把网络缓存伪装成实时查询。"""
    if metadata_root is None:
        return MetadataEvidence()
    candidates: list[Candidate] = []
    makers: set[str] = set()
    sources: set[str] = set()
    for variant in code_variants(code):
        folder = metadata_root / variant
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                wrapper = json.loads(path.read_text(encoding=ENCODING))
            except (OSError, ValueError):
                continue
            result = wrapper.get("result")
            if not isinstance(result, dict):
                continue
            source = str(result.get("source") or wrapper.get("source") or path.stem)
            sources.add(source.lower())
            for value in (result.get("maker"), result.get("label")):
                if isinstance(value, str) and value.strip():
                    makers.add(value.strip().lower())
            # 社区站的快照只借厂牌选渠道：JavBus 搜不到原番号时返回别的作品，它的图与
            # content_id 要走 `peach.community_catalog` 的图源印证，不在这里当官方候选。
            spec = SOURCE_SPECS.get(source.lower())
            if spec is not None and not spec.official:
                continue
            cover_url = result.get("cover_url")
            if isinstance(cover_url, str) and IMAGE_URL.fullmatch(cover_url.strip()):
                candidates.extend(dmm_cdn_images(cover_url.strip()))
            candidates.extend(content_id_images(str(result.get("content_id") or "")))
    return MetadataEvidence(
        tuple(_unique_candidates(candidates)), frozenset(makers), frozenset(sources),
    )


def logged_success_evidence(
        rows: list[dict], code: str,
        ) -> tuple[Candidate, tuple[int, int]] | None:
    """复用历史精确 URL 与已量尺寸；DUGA 等无需重复探同一张图。"""
    for row in reversed(rows):
        if (normalise_code_key(str(row.get("code") or "")) == code
                and row.get("result") == "取得"
                and isinstance(row.get("url"), str)
                and IMAGE_URL.fullmatch(str(row["url"]))):
            try:
                size = (int(row.get("width") or 0), int(row.get("height") or 0))
            except (TypeError, ValueError):
                size = (0, 0)
            return candidate_for(str(row["url"])), size
    return None


#: DMM 的 content_id 一定带番号字母段：`ABW-232` -> `118abw232`、
#: `MGT-164` -> `h_1711mgt00164`。
_DMM_CID = re.compile(
    r"/(?:pics_dig/|dig/)?(?:digital/(?:video|amateur)|mono/movie)"
    r"(?:/adult)?/(?P<cid>[^/]+)/",
    re.I,
)
#: 番号与 content_id 各自末尾那段数字。content_id 可能带 DVD 版尾缀（`49ha102r`）。
_CODE_NUMBER = re.compile(r"(\d+)\D*$")
_CID_NUMBER = re.compile(r"(\d+)[a-z]*$")
#: 图床文件名里的作品号：五到七位的独立数字段。`pl1654160016.07k.gif` 那种十位的是
#: 上传时间戳，不算作品号。
_FILE_NUMBER = re.compile(r"(?<!\d)\d{5,7}(?!\d)")
#: 图床给上传起的十六进制串（PHP `uniqid`，13 位）。`5dd29b14380eaPS.jpg` 里夹着的
#: `14380` 是串的一截，不是作品号。
_UPLOAD_HASH = re.compile(r"[0-9a-f]{13,}", re.I)


def _fc2_names_another_work(code: str, url: str) -> bool:
    """FC2 番号的图，文件名写着别的作品号。

    JavArchive 的图床 img.javstore.net 按作品号起文件名（`FC2PPV-2543627.gif`、
    `1083921pl.jpg`）。
    """
    key = normalise_code_key(code)
    wanted = _CODE_NUMBER.search(key) if key.startswith("FC2") else None
    name = _UPLOAD_HASH.sub("", urlparse(url or "").path.rsplit("/", 1)[-1])
    numbers = {int(one) for one in _FILE_NUMBER.findall(name)}
    return bool(wanted and numbers) and int(wanted.group(1)) not in numbers


def is_cross_product_cover(code: str, url: str) -> bool:
    """这个 URL 指向的是另一部片的封套。

    素人系番号在 avbase 上会连到 DVD 合集：`259LUXU-1475` 的页面主图取到的是
    `SNG-021` 的封套（`118sng021`）。内容是同一段，封面却不是这部片的——大图
    模式的人脸定位、番号搜索和「这张图是谁」全都跟着错位。

    avbase 抓取已在 65fd95f 删掉，这批 URL 却靠「复用上一轮成功记录」一路带到
    今天：复用只看 result 是不是「取得」，不看它和番号是否对得上，于是错误封面
    再也没有机会被重探。判据落在 URL 上而不是抓取路径上，正是因为下一个引入
    错图的来源不会用同一个函数。
    """
    if _fc2_names_another_work(code, url):
        return True
    stem = code_letter_stem(code)
    match = _DMM_CID.search(urlparse(url or "").path)
    if not stem or match is None:
        return False
    cid = match.group("cid").lower()
    if stem not in cid:
        return True
    # 字母段对上还不够：`YUJ-101` 取回的是 `yuj00011`，`CHU-201` 是 `chu00021`，
    # `435MFC-135` 是 `h_1711mfcc00027`。数字段按整数比，补零和厂牌前缀都不影响。
    wanted, got = _CODE_NUMBER.search(code or ""), _CID_NUMBER.search(cid)
    return bool(wanted and got) and int(wanted.group(1)) != int(got.group(1))


def _is_prestige(evidence: MetadataEvidence) -> bool:
    return any("prestige" in value or "プレステージ" in value
               for value in evidence.makers)


def r18_evidence(transport: HttpTransport, code: str, *,
                 deadline: float | None = None, record=lambda key: None) -> MetadataEvidence:
    """读取 r18 返回的官方封套，并保留旧数字版高清 URL 探测。

    `content_id` 不是稳定的数字版路径。Prestige 的 ABW 系列会返回
    `118abw232`，对应 `pics.dmm.co.jp/mono/.../118abw232pl.jpg`；把它补零后
    拼到 `awsimgsrc.../digital/video` 只会得到 404。r18 已在
    `images.jacket_image` 给出官方原图 URL，必须优先把这个证据加入候选。
    """
    for variant in code_variants(code):
        try:
            payload = json.loads(_fetch(
                transport, R18_DETAIL.format(code=urllib.parse.quote(variant)),
                referer="https://r18.dev/", limit=2 * 1024 * 1024,
                deadline=deadline,
            ).decode("utf-8", "ignore"))
        except NotFound:
            continue
        except (Unavailable, ValueError, httpx.TransportError):
            record('source_unanswered')
            continue
        if not isinstance(payload, dict):
            record('source_unanswered')
            continue
        return r18_payload_evidence(payload)
    return MetadataEvidence()


def r18_payload_evidence(payload: dict) -> MetadataEvidence:
    """r18.dev 作品 JSON 里的封面证据：`jacket_image` 的原图与 `content_id` 拼出的数字版地址。

    资料那一步取回的 r18.dev 快照原样带着这份 JSON（`sources/r18dev.py` 的 `raw`），
    封面那一步拿它就不必再问一遍 r18.dev。
    """
    found: list[Candidate] = []
    jacket = ((payload.get("images") or {}).get("jacket_image") or {})
    if isinstance(jacket, dict):
        for raw_url in jacket.values():
            url = raw_url.strip() if isinstance(raw_url, str) else ""
            if url and IMAGE_URL.fullmatch(url) and not THUMBNAIL.search(url):
                found.extend(dmm_cdn_images(url))
    cid = str(payload.get("content_id") or "")
    found.extend(content_id_images(cid))
    makers = frozenset(str(payload.get(key, {}).get("name", "")).casefold()
                       for key in ("maker", "label") if isinstance(payload.get(key), dict))
    return MetadataEvidence(tuple(_unique_candidates(found)), makers, frozenset({"r18dev"}))


def dmm_evidence(transport: HttpTransport, code: str, *, deadline=None) -> MetadataEvidence:
    """复用已核对番号的 DMM 官方目录，取得图片与数字版候选。"""
    from peach.sources import Session
    from peach.sources.base import SourceFailure
    from peach.sources.dmm import DmmSource
    try:
        payload = DmmSource().query(code, session=Session(transport, deadline)).payload()
    except SourceFailure as error:
        if error.kind == 'not_found':
            return MetadataEvidence()
        raise Unavailable('DMM：' + str(error)) from error
    found = [candidate for url in payload.get('cover_urls') or () for candidate in dmm_cdn_images(url)]
    found += content_id_images(str(payload.get('content_id') or ''))
    makers = frozenset(str(payload.get(field) or '').strip().casefold() for field in ('maker', 'label'))
    return MetadataEvidence(tuple(_unique_candidates(found)), makers, frozenset({'dmm'}))


def mgstage_images(transport: HttpTransport, code: str, *,
                   deadline: float | None = None) -> list[Candidate]:
    """直取 MGS 商品页 EnlargeImage；年龄确认只用公开 cookie，不绕挑战。"""
    for variant in code_variants(code):
        try:
            page = _fetch(
                transport,
                MGS_DETAIL.format(code=urllib.parse.quote(variant)),
                referer="https://www.mgstage.com/",
                limit=4 * 1024 * 1024,
                extra_headers={"Cookie": "adc=1"},
                deadline=deadline,
            ).decode("utf-8", "ignore")
        except (Unavailable, httpx.TransportError):
            continue
        parser = _MGSDetailParser()
        parser.feed(page)
        found: list[Candidate] = []
        for url in parser.urls:
            absolute = urllib.parse.urljoin("https://www.mgstage.com/", url)
            if THUMBNAIL.search(absolute):
                continue
            found.append(candidate_for(absolute))
        if found:
            return _unique_candidates(found)
    return []


def prestige_images(transport: HttpTransport, code: str, *,
                    deadline: float | None = None) -> list[Candidate]:
    """按 MDCX 固定 revision 的公开 API 模型直取 Prestige packageImage。"""
    query = urllib.parse.urlencode({
        "isEnabledQuery": "true",
        "searchText": code,
        "isEnableAggregation": "false",
        "release": "false",
        "reservation": "false",
        "soldOut": "false",
        "from": 0,
        "aggregationTermsSize": 0,
        "size": 20,
    })
    try:
        payload = json.loads(_fetch(
            transport, f"{PRESTIGE_SEARCH}?{query}",
            referer="https://www.prestige-av.com/", limit=4 * 1024 * 1024,
            deadline=deadline,
        ).decode("utf-8", "ignore"))
    except (Unavailable, ValueError, httpx.TransportError):
        return []
    hits = (((payload.get("hits") or {}).get("hits")) or [])
    exact: list[str] = []
    fallback: list[str] = []
    for hit in hits:
        source = hit.get("_source") if isinstance(hit, dict) else None
        if not isinstance(source, dict):
            continue
        item_id = normalise_code_key(str(source.get("deliveryItemId") or ""))
        uuid = str(source.get("productUuid") or "").strip()
        if not uuid:
            continue
        if item_id == normalise_code_key(code):
            exact.append(uuid)
        elif str(source.get("deliveryItemId") or "").upper().endswith(code.upper()):
            fallback.append(uuid)
    uuids = list(dict.fromkeys(exact or fallback))
    for uuid in uuids:
        try:
            product = json.loads(_fetch(
                transport, PRESTIGE_PRODUCT.format(uuid=urllib.parse.quote(uuid)),
                referer="https://www.prestige-av.com/", limit=4 * 1024 * 1024,
                deadline=deadline,
            ).decode("utf-8", "ignore"))
        except (Unavailable, ValueError, httpx.TransportError):
            continue
        package = product.get("packageImage") or {}
        path = package.get("path") if isinstance(package, dict) else ""
        if isinstance(path, str) and path.strip():
            url = PRESTIGE_MEDIA.format(path=path.strip().lstrip("/"))
            if IMAGE_URL.fullmatch(url):
                return [candidate_for(url)]
    return []


def prestige_group_images(transport: HttpTransport, code: str, *,
                          deadline: float | None = None) -> list[Candidate]:
    """汇总 Prestige 与 MGS 的官方候选，尺寸由统一探测比较。"""
    candidates, paused = [], []
    for fetch in (prestige_images, mgstage_images):
        try:
            candidates.extend(fetch(transport, code, deadline=deadline))
        except SourcePaused as error:
            paused.append(error)
    if not candidates and paused:
        raise paused[0]
    return _unique_candidates(candidates)


def probe_size(transport: HttpTransport, candidate: Candidate, *,
               deadline: float | None = None) -> tuple[int, int]:
    head = _fetch(transport, candidate.url, referer=candidate.referer,
                  limit=PROBE_BYTES * 2, ranged=True, deadline=deadline)
    return Image.open(io.BytesIO(head)).size


def _joined_evidence(known: MetadataEvidence | None, cached: MetadataEvidence) -> MetadataEvidence:
    """调用方交来的快照证据排在缓存那份前面，来源与厂牌取并集。"""
    if known is None:
        return cached
    return MetadataEvidence(tuple(_unique_candidates([*known.candidates, *cached.candidates])),
                            known.makers | cached.makers, known.sources | cached.sources)


def _usable_candidates(code: str, candidates, known_sizes: dict[str, tuple[int, int]],
                       minimum_quality: int, minimum_pixels: int) -> list[Candidate]:
    """去重，去掉缩略图与别的作品的封套，再去掉成功日志里量过、不比本机那张好的地址。

    升级模式已在成功日志中量过的精确 URL，若像素不大于当前本地图，就不再发 Range 请求。
    其他 URL 和尺寸未知的候选仍完整探测，不改变择优语义。
    """
    return [
        candidate for candidate in _unique_candidates([
            candidate for candidate in candidates
            if (not THUMBNAIL.search(candidate.url) or FC2_HIRES.match(candidate.url))
            and not is_cross_product_cover(code, candidate.url)])
        if candidate.url not in known_sizes
        or candidate_improves(candidate, known_sizes[candidate.url][0] * known_sizes[candidate.url][1],
                              minimum_quality, minimum_pixels)
    ]


def _site_candidates(transport: HttpTransport, code: str, evidence: MetadataEvidence, *,
                     delay: float, deadline: float | None, record=lambda key: None) -> list[Candidate]:
    """复用官方目录并按发行面查询图片；FC2 由调用方提供商品图候选。"""
    if code.upper().startswith("FC2-PPV-"):
        return []
    from peach.metadata_routes import maker_sites
    candidates: list[Candidate] = []
    live_evidence = MetadataEvidence()
    prestige = _is_prestige(evidence) or 'prestige' in maker_sites(code)
    amateur = is_amateur_code(code) or 'mgstage' in evidence.sources
    if prestige:
        candidates += prestige_group_images(transport, code, deadline=deadline)
    elif amateur:
        candidates += mgstage_images(transport, code, deadline=deadline)
    if candidates or ((prestige or amateur) and evidence.candidates):
        return candidates
    if ('dmm' not in evidence.sources
            and not prestige and not amateur and not is_uncensored_code(code)):
        try:
            live_evidence = dmm_evidence(transport, code, deadline=deadline)
            candidates += list(live_evidence.candidates)
        except (Unavailable, SourcePaused):
            record('source_unanswered')
    # 有成功快照时不重复打 r18；失败快照不算证据，仍允许联网刷新。
    if ('r18dev' not in evidence.sources and not live_evidence.candidates
            and not ('dmm' in evidence.sources and evidence.candidates)):
        live_evidence = r18_evidence(transport, code, deadline=deadline, record=record)
        candidates += list(live_evidence.candidates)
        _sleep_within(delay, deadline)
    # 目录新识别出 Prestige 时补问集团自营渠道。
    if not prestige and _is_prestige(live_evidence):
        candidates += prestige_group_images(transport, code, deadline=deadline)
        _sleep_within(delay, deadline)
    return candidates


def best_cover(transport: HttpTransport, code: str, delay: float, *,
               metadata_root: Path | None = None,
               prior_candidates: tuple[Candidate, ...] = (),
               known_sizes: dict[str, tuple[int, int]] | None = None,
               minimum_pixels: int = 0,
               minimum_quality: int = _CANDIDATE_QUALITY[PRODUCT_IMAGE],
               minimum_width: int = MIN_WIDTH,
               deadline: float | None = None,
               diagnostics: dict[str, int] | None = None,
               known: MetadataEvidence | None = None,
               ) -> tuple[Candidate, tuple[int, int], bytes]:
    """官方封面择优：手上的候选、缓存快照里的候选，加上 r18.dev、MGS 或 Prestige 现问来的候选。

    `known` 是调用方这一趟已经取到的官方快照证据（例如 `r18_payload_evidence`），与 `metadata_root`
    那份缓存同等对待：`sources` 里有 `r18dev` 就不再问 r18.dev。所有可用官方候选一起量尺寸，
    并按像素面积选择。
    """
    # 韩国 MIB 的编号在 JAV 目录站上要么不存在、要么撞上番号相同的日本作品：
    # `HA-101`、`MY-102` 取回的都是那部日本片的封套，番号核验拦不住，因为番号本来
    # 就一样。所以不是「找不到」，是根本不该去找。
    if is_korean_mib_code(code):
        raise Unavailable(MIB_NOT_JAV)
    # 来源一律记主机名。缓存、构造路径和官方页常指向同一个主机，记成多个名字
    # 会让覆盖率统计凭空多出「渠道」。
    evidence = _joined_evidence(known, cached_metadata(metadata_root, code))
    diagnostics = diagnostics if diagnostics is not None else {}
    def record(key):
        diagnostics[key] = diagnostics.get(key, 0) + 1

    def usable(found):
        return _usable_candidates(code, found, known_sizes or {}, minimum_quality, minimum_pixels)

    def source_transport(request, timeout, limit):
        try:
            response = transport(request, timeout, limit)
        except (httpx.TransportError, SourcePaused):
            record('source_unanswered')
            raise
        if response.status not in (200, 206, 404):
            record('source_unanswered')
            record('source_http_' + str(response.status))
        return response

    source_transport.inner = transport

    first = usable([*prior_candidates, *evidence.candidates])
    try:
        discovered = _site_candidates(source_transport, code, evidence, delay=delay,
                                      deadline=deadline, record=record)
    except SourcePaused:
        if not first:
            raise
        discovered = []
    asked = usable([*first, *discovered])
    if not asked:
        if diagnostics.get('source_unanswered'):
            raise Unavailable('官方目录查询未完成，尚未取得封面候选')
        raise NotFound("所有渠道都没有候选")
    measured = _measure(transport, asked, record, minimum_width=minimum_width,
                        delay=delay, deadline=deadline)
    if not measured:
        # 每张候选都回过话（404、太小、占位图、解不开）就是来源的确定答复，下次再问还是这几张：
        # 交 `NotFound`，采集任务记下一周不问。有一张断在连接、5xx 或拒绝访问上才可能是这趟没问成。
        kind = Unavailable if diagnostics.get("unanswered") else NotFound
        raise kind(_no_usable_official(diagnostics))
    return _download_best(transport, measured, record, minimum_width=minimum_width,
                          minimum_quality=minimum_quality, minimum_pixels=minimum_pixels,
                          deadline=deadline)


def _measure(transport: HttpTransport, candidates, record, *, minimum_width: int,
             delay: float, deadline: float | None) -> list[tuple[int, Candidate, tuple[int, int]]]:
    """逐张量候选的尺寸，留下够宽的那些；量不出的原因记进诊断。

    断在连接上的几张与来源回过话的几张分开数：一张都没回过话时，候选用完也好、
    预算在重试里耗尽也好，说的都是线路不通，抛 `CoverConnectError`。连接断开和
    404 以外的错误回应另记一格 `unanswered`：有它在，量不出尺寸就不算来源的确定答复。
    """
    measured: list[tuple[int, Candidate, tuple[int, int]]] = []
    unreachable = answered = 0
    paused = []
    try:
        for candidate in candidates:
            try:
                width, height = probe_size(transport, candidate, deadline=deadline)
            except httpx.TransportError as error:
                record(_probe_failure(error))
                record("unanswered")
                unreachable += 1
                continue
            except SourcePaused as error:
                paused.append(error)
                record("unanswered")
                continue
            except Unavailable as error:
                record(_probe_failure(error))
                if not isinstance(error, NotFound):
                    record("unanswered")
                answered += 1
                continue
            except (UnidentifiedImageError, OSError):
                record("invalid_image")
                answered += 1
                continue
            finally:
                _sleep_within(delay, deadline)
            answered += 1
            if width >= minimum_width:
                measured.append((width * height, candidate, (width, height)))
            else:
                record("too_small")
    except DeadlineExceeded:
        if unreachable and not answered:
            raise CoverConnectError(HOSTS_UNREACHABLE) from None
        raise
    if unreachable and not answered:
        raise CoverConnectError(HOSTS_UNREACHABLE)
    if paused and len(paused) == len(candidates):
        raise paused[0]
    return measured


def _download_best(transport: HttpTransport, measured, record, *, minimum_width: int,
                   minimum_quality: int, minimum_pixels: int,
                   deadline: float | None) -> tuple[Candidate, tuple[int, int], bytes]:
    """封面按像素面积择优，同尺寸优先商品图；预览图仅作末档候选。

    动图只有下完整张才认得出：FC2 链末档 JavArchive 的图床存的常是 GIF 预览动画，
    只读头部量尺寸时它和一张静态图没有区别，落进 `.jpg` 之后卡片就一直在动。候选全是
    动图时算没有，而不是没下载成——再问一遍还是这几张。
    """
    animated = 0
    paused = []
    for _pixels, winner, size in sorted(
            measured, key=lambda item: (candidate_quality(item[1]) > 0, item[0], candidate_quality(item[1])), reverse=True):
        try:
            data = _fetch(transport, winner.url, referer=winner.referer,
                          limit=16 * 1024 * 1024, deadline=deadline)
        except (Unavailable, httpx.TransportError):
            record("download_failed")
            continue
        except SourcePaused as error:
            paused.append(error)
            record("download_failed")
            continue
        try:
            with Image.open(io.BytesIO(data)) as image:
                moving = getattr(image, "is_animated", False)
                image.load()
                actual_size = image.size
        except (OSError, ValueError, Image.DecompressionBombError):
            record("invalid_image")
            continue
        if moving:
            record("animated")
            animated += 1
            continue
        if actual_size != size or actual_size[0] < minimum_width:
            record("dimension_mismatch")
            continue
        if not candidate_improves(
                winner, actual_size[0] * actual_size[1],
                minimum_quality, minimum_pixels):
            continue
        return winner, actual_size, data
    if animated == len(measured):
        raise NotFound("官方候选都是动图")
    if paused and len(paused) == len(measured):
        raise paused[0]
    raise Unavailable("可用候选完整下载都失败")


FIELDS = ("code", "result", "source", "width", "height", "kb", "url", "note")


#: 判定为「所有渠道都没有」的落空，续跑时不必重来；连接类失败必须重试。
#: 三态口径：一次超时不等于确认没有，不能靠它把番号永久踢出队列。
TRANSIENT = re.compile(r"(Error|Timeout|SSL|Connect|Proxy|Protocol)", re.I)


def settled_misses(log: Path) -> set[str]:
    """上一轮已经把所有源探完、确认没有封套的番号。

    这类落空是最贵的：每条都要把全部候选源挨个试完才能确定。实测 194 条里
    150 条落空，重探一遍就是好几个小时，而结论不会变。
    """
    rows = logged_rows(log)
    return {str(row.get("code") or "").strip() for row in rows
            if row.get("result") == "未取得"
            and not TRANSIENT.search(str(row.get("note") or ""))
            and str(row.get("code") or "").strip()}


def logged_rows(log: Path) -> list[dict]:
    if not log.is_file():
        return []
    return read_rows(log)


def restore_logged_successes(transport: HttpTransport, log: Path, root: Path,
                             delay: float = 0.0, guard: DiskGuard | None = None) -> dict:
    """Re-download missing covers from the exact successful URLs already in the audit log.

    This is intentionally narrower than a fresh scrape: it makes no discovery requests,
    preserves the existing audit log and refuses an upstream image whose dimensions changed.
    """
    root.mkdir(parents=True, exist_ok=True)
    restored = skipped = 0
    failed: list[dict[str, str]] = []
    # 恢复不做发现请求，所以它比抓取更容易把历史错图原样搬回本地：跨片封套与 MIB
    # 在这里也要挡掉，否则删掉错图重跑恢复只会把同一张再下一次。
    successes = [row for row in logged_rows(log)
                 if row.get("result") == "取得" and row.get("code") and row.get("url")
                 and not is_cross_product_cover(str(row["code"]), str(row["url"]))
                 and not is_korean_mib_code(str(row["code"]))]
    for index, row in enumerate(successes, 1):
        if guard is not None:
            guard.check()
        code = normalise_code_key(str(row["code"]))
        target = root / f"{code}.jpg"
        if target.is_file():
            skipped += 1
            continue
        temporary = target.with_suffix(".restore.tmp")
        try:
            data = _fetch(transport, str(row["url"]), referer=_referer_for(str(row["url"])),
                          limit=16 * 1024 * 1024)
            with Image.open(io.BytesIO(data)) as image:
                size = image.size
                image.verify()
            expected = (int(row["width"]), int(row["height"]))
            if size != expected or size[0] < MIN_WIDTH:
                raise Unavailable(f"尺寸变化：日志 {expected[0]}x{expected[1]}，当前 {size[0]}x{size[1]}")
            temporary.write_bytes(data)
            temporary.replace(target)
            restored += 1
            print(f"[{index}/{len(successes)}] 恢复 {code}  {size[0]}x{size[1]}", flush=True)
        # 与完整抓取同一条长跑边界：单张网络异常降级成失败记录，不能让余下恢复归零；
        # KeyboardInterrupt 等 BaseException 仍会正常中断。
        except Exception as exc:
            failed.append({"code": code, "error": f"{type(exc).__name__}: {exc}"[:120]})
            print(f"[{index}/{len(successes)}] 未恢复 {code}：{type(exc).__name__} {exc}",
                  flush=True)
        finally:
            temporary.unlink(missing_ok=True)
            if delay:
                time.sleep(delay)
    return {"logged": len(successes), "restored": restored, "skipped": skipped,
            "failed": failed}


def pending(database: Path, root: Path, only_shaped: bool,
            location: str | None = None, *, existing: bool = False,
            max_width: int = 0, fc2_only: bool = False) -> list[str]:
    connection = open_readonly(database)
    try:
        location_sql = " AND location=?" if location else ""
        parameters: tuple[object, ...] = (location,) if location else ()
        rows = connection.execute(
            "SELECT code, COUNT(*) FROM asset WHERE medium='video' "
            "AND code IS NOT NULL AND code<>''" + location_sql
            + " GROUP BY code ORDER BY 2 DESC",
            parameters,
        ).fetchall()
    finally:
        connection.close()
    result = []
    for code, _count in rows:
        if fc2_only and not str(code).upper().startswith("FC2"):
            continue
        # 判形态必须看原值；`normalise_code_key` 补分隔符可能将创作者账号名
        # 归一成番号形态。判据与 web_contract 共用一份实现。
        if only_shaped and not is_jav_code(str(code)):
            continue
        # FC2 不走本抓取器使用的 r18/avsox/javbus 来源；`--all-codes` 可强制尝试。
        if only_shaped and str(code).upper().startswith("FC2"):
            continue
        # MIB 不走 JAV 封面来源（见 `best_cover`），排进队列只会每轮落空一次。
        if is_korean_mib_code(str(code)):
            continue
        key = normalise_code_key(str(code))
        target = root / f"{key}.jpg"
        if existing:
            if not target.is_file():
                continue
            try:
                with Image.open(target) as image:
                    width = image.size[0]
            except (UnidentifiedImageError, OSError):
                width = 0
            if max_width and width > max_width:
                continue
            result.append(key)
        elif not target.is_file():
            result.append(key)
    return result


def audit_state(database: Path, root: Path, log: Path) -> dict[str, object]:
    """只读盘点当前 JAV 封面；用于批次前后使用同一统计口径。"""
    connection = open_readonly(database)
    try:
        raw_codes = [str(row[0]) for row in connection.execute(
            "SELECT DISTINCT code FROM asset WHERE medium='video' "
            "AND code IS NOT NULL AND code<>''"
        )]
    finally:
        connection.close()
    codes = {
        normalise_code_key(code) for code in raw_codes
        if is_jav_code(code) and not code.upper().startswith("FC2")
    }
    dimensions: dict[str, tuple[int, int]] = {}
    invalid: list[str] = []
    for path in sorted(root.glob("*.jpg")) if root.is_dir() else []:
        try:
            with Image.open(path) as image:
                dimensions[path.stem] = image.size
        except (UnidentifiedImageError, OSError):
            invalid.append(path.name)
    widths = {
        "le_800": sum(width <= 800 for width, _height in dimensions.values()),
        "801_999": sum(801 <= width <= 999 for width, _height in dimensions.values()),
        "1000_1199": sum(1000 <= width <= 1199
                         for width, _height in dimensions.values()),
        "ge_1200": sum(width >= 1200 for width, _height in dimensions.values()),
    }
    rows = logged_rows(log)
    return {
        "jav_codes": len(codes),
        "decoded_covers": len(dimensions),
        "missing": len(codes - set(dimensions)),
        "invalid": invalid,
        "width_buckets": widths,
        "log_successes": sum(row.get("result") == "取得" for row in rows),
        "log_misses": sum(row.get("result") == "未取得" for row in rows),
        "settled_misses": len(settled_misses(log)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按番号抓最高清的官方封套")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--out", type=Path, default=COVER_DIR)
    parser.add_argument("--log", type=Path,
                        default=GENERATED_DIR / "cover-fetch-log.csv")
    parser.add_argument(
        "--metadata-root", type=Path, default=DEFAULT_METADATA_ROOT,
        help="Javinizer-Go 原始快照目录；先离线复用成功证据，再补联网来源",
    )
    parser.add_argument("--fc2-metadata-log", type=Path, default=DEFAULT_FC2_METADATA_LOG,
                        help="fc2cmadb 文章封面证据，只对 --fc2-only 生效")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--code", help="只处理一个已归一化番号，用于定点重试")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--min-free", type=float, default=40.0,
                        help="系统盘最低可用 GiB；运行中每隔一段时间复查")
    parser.add_argument("--disk-check-secs", type=float, default=20.0)
    parser.add_argument("--location",
                        help="只抓指定来源的番号封套，例如 pikpak；封套仍按番号共享")
    parser.add_argument("--retry-misses", action="store_true",
                        help="连上轮确认没有封套的番号也重探一遍")
    parser.add_argument("--restore-successes", action="store_true",
                        help="只按成功日志中的原 URL 恢复缺失封套，不重新探测来源")
    parser.add_argument("--upgrade-existing", action="store_true",
                        help="重探已有封套；只有候选像素更多时才原子替换")
    parser.add_argument("--upgrade-max-width", type=int, default=0,
                        help="升级模式只重探不超过此宽度的已有封套；0 表示全部")
    parser.add_argument("--all-codes", action="store_true",
                        help="连 FC2/日期番号一起试；默认只跑片商与素人形态")
    parser.add_argument("--fc2-only", action="store_true",
                        help="只用 fc2cmadb 已存证据抓 FC2 官方 CDN 封面")
    parser.add_argument("--audit", action="store_true",
                        help="只读输出封面数量、缺失、损坏和尺寸分布，不联网不写文件")
    return parser


def _write_log(path: Path, rows: list[dict]) -> None:
    """每条都落盘：这个任务要跑三四个小时，只在结束时写等于全程看不见进度。"""
    write_rows(path, FIELDS, rows)


def _renew_transport_after_error(transport: HttpTransport,
                                 error: Exception) -> HttpTransport:
    """永久 transport 失败后丢弃连接池，避免后续番号连续 PoolTimeout。"""
    if not isinstance(error, httpx.TransportError):
        return transport
    if isinstance(transport, HostLimitedTransport):
        transport.renew()
        return transport
    transport.close()
    return HttpxTransport()


def _replace_log_row(rows: list[dict], code: str, replacement: dict) -> None:
    """一个番号只保留一条最新成功证据，不扰动其他番号。"""
    rows[:] = [row for row in rows
               if str(row.get("code") or "").strip() != code]
    rows.append({field: replacement.get(field, "") for field in FIELDS})


def run(args: argparse.Namespace, handle: TaskRunHandle | None = None) -> int:
    """跑一趟封面抓取。`handle` 是任务中心的句柄，缺省时这一趟不登记。"""
    handle = handle or inert_handle()
    if args.audit:
        print(json.dumps(audit_state(args.db, args.out, args.log),
                         ensure_ascii=False, indent=2))
        return 0
    if args.upgrade_max_width < 0:
        print("[stop] --upgrade-max-width 不能小于 0")
        return 2
    if args.upgrade_max_width and not args.upgrade_existing:
        print("[stop] --upgrade-max-width 只能与 --upgrade-existing 一起使用")
        return 2
    if args.upgrade_existing and args.retry_misses:
        print("[stop] --upgrade-existing 与 --retry-misses 不能同时使用")
        return 2
    if args.restore_successes and args.upgrade_existing:
        print("[stop] --restore-successes 与 --upgrade-existing 不能同时使用")
        return 2
    if args.fc2_only and args.all_codes:
        print("[stop] --fc2-only 与 --all-codes 不能同时使用")
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    guard = DiskGuard(system_volume(), args.min_free, args.disk_check_secs)
    try:
        free_gb = guard.check(force=True)
    except JobPolicyError as exc:
        print(f"[stop] {exc}")
        return exc.exit_code
    print(f"系统盘可用 {free_gb:.1f} GiB，运行期阈值 {args.min_free:.1f} GiB")
    if args.restore_successes:
        transport = HttpxTransport()
        try:
            result = restore_logged_successes(
                transport, args.log, args.out, args.delay, guard=guard,
            )
        except JobPolicyError as exc:
            print(f"[stop] {exc}")
            return exc.exit_code
        finally:
            transport.close()
        print(f"成功日志 {result['logged']}，恢复 {result['restored']}，"
              f"已存在 {result['skipped']}，失败 {len(result['failed'])} → {args.out}")
        return 2 if result["failed"] else 0

    todo = pending(
        args.db, args.out, not args.all_codes and not args.fc2_only, args.location,
        existing=args.upgrade_existing, max_width=args.upgrade_max_width,
        fc2_only=args.fc2_only,
    )
    if args.code:
        wanted = normalise_code_key(args.code)
        todo = [code for code in todo if code == wanted]
    skipped = set()
    if not args.upgrade_existing and not args.retry_misses:
        skipped = settled_misses(args.log) & set(todo)
        todo = [code for code in todo if code not in skipped]
    if args.limit:
        todo = todo[:args.limit]
    selected = set(todo)
    if args.upgrade_existing:
        width_note = (f"，当前宽度不超过 {args.upgrade_max_width}"
                      if args.upgrade_max_width else "")
        print(f"待重探已有封套 {len(todo)} 个{width_note}；只在像素更多时替换")
    else:
        print(f"待抓番号 {len(todo)} 个（本机已有的跳过，"
              f"上轮确认没有的跳过 {len(skipped)} 个，--retry-misses 可重试）")

    transport = HostLimitedTransport(SourceTransport(SECRETS_DIR), args.delay)
    # 日志是整份重写：这轮只跑 pikpak 或只跑 --limit 时，未选中的旧记录也必须保留；
    # 只删除本轮会重新生成的番号。否则一次来源小批次就会抹掉其他来源的复核证据。
    previous_rows = logged_rows(args.log)
    fc2_candidates = fc2_cover_candidates(args.fc2_metadata_log) if args.fc2_only else {}
    rows: list[dict] = [
        {field: row.get(field, "") for field in FIELDS}
        for row in previous_rows
        if args.upgrade_existing
        or str(row.get("code") or "").strip() not in selected
    ]
    stats = {"ok": 0, "miss": 0, "kept": 0}
    stopped: JobPolicyError | None = None
    try:
        for index, code in enumerate(todo, 1):
            handle.progress(index, len(todo), code)
            try:
                guard.check()
            except JobPolicyError as exc:
                stopped = exc
                print(f"[stop] {exc}", flush=True)
                break
            try:
                target = args.out / f"{code}.jpg"
                current_size = (0, 0)
                if target.exists():
                    try:
                        with Image.open(target) as image:
                            current_size = image.size
                    except (UnidentifiedImageError, OSError):
                        pass
                previous = logged_success_evidence(previous_rows, code)
                last = previous[0] if previous is not None else None
                baseline_quality = candidate_quality(last)
                prior = tuple(candidate for candidate in (fc2_candidates.get(code), last)
                              if candidate is not None)
                known_sizes = ({previous[0].url: previous[1]}
                               if previous is not None else {})
                winner, (width, height), data = best_cover(
                    transport, code, 0,
                    metadata_root=args.metadata_root, prior_candidates=prior,
                    known_sizes=known_sizes,
                    minimum_pixels=current_size[0] * current_size[1],
                    minimum_quality=baseline_quality,
                )
            # 网络异常必须按条吞掉。一次 SSL 抖动
            # （httpx.ConnectError: UNEXPECTED_EOF_WHILE_READING）此前直接打死了
            # 整个三小时的任务，而且死得很安静——日志停在半路，看起来像跑完了。
            # 长跑批处理不能因为一个番号的连接问题就整体退出。
            # `Exception` 已涵盖 Unavailable 与网络异常；Ctrl-C 是 BaseException
            # 的另一支，不会被这里吞掉，仍能正常中断。
            except Exception as exc:
                transport = _renew_transport_after_error(transport, exc)
                stats["miss"] += 1
                if args.upgrade_existing:
                    print(f"[{index}/{len(todo)}] 保留 {code}：重探失败 "
                          f"{type(exc).__name__} {exc}", flush=True)
                else:
                    rows.append({"code": code, "result": "未取得", "source": "",
                                 "width": "", "height": "", "kb": "", "url": "",
                                 "note": f"{type(exc).__name__}: {exc}"[:80]})
                    print(f"[{index}/{len(todo)}] 未取得 {code}："
                          f"{type(exc).__name__} {exc}", flush=True)
            else:
                if args.upgrade_existing and not candidate_improves(
                        winner, width * height,
                        baseline_quality, current_size[0] * current_size[1]):
                    stats["kept"] += 1
                    print(f"[{index}/{len(todo)}] 保留 {code}  "
                          f"{current_size[0]}x{current_size[1]} >= {width}x{height}",
                          flush=True)
                else:
                    temporary = target.with_suffix(".tmp")
                    temporary.write_bytes(data)
                    try:
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
                    stats["ok"] += 1
                    row = {"code": code, "result": "取得", "source": winner.source,
                           "width": width, "height": height, "kb": len(data) // 1024,
                           "url": winner.url, "note": ""}
                    if args.upgrade_existing:
                        _replace_log_row(rows, code, row)
                    else:
                        rows.append(row)
                    verb = "升级" if args.upgrade_existing else "取得"
                    print(f"[{index}/{len(todo)}] {verb} {code}  {width}x{height} "
                          f"{len(data)//1024} KB  <- {winner.source}", flush=True)
            # 落空的行也要落盘。这句只放在取得分支里的话，连续落空时 CSV 整段不动；
            # 被强杀时 finally 也来不及跑，那一串判定就白做了——而「查不到」恰恰
            # 是最贵的一类：每条都要把所有候选源挨个探完才能确定。
            _write_log(args.log, rows)
    finally:
        transport.close()
        _write_log(args.log, rows)

    if args.upgrade_existing:
        print(f"\n升级 {stats['ok']}，保留 {stats['kept']}，"
              f"重探失败但保留原图 {stats['miss']} → {args.out}")
    else:
        print(f"\n取得 {stats['ok']}，未取得 {stats['miss']} → {args.out}")
    print(f"逐条记录 → {args.log}")
    summary = dict(stats, planned=len(todo))
    if stopped is not None:
        # 磁盘闸门把这一趟拦下来了：活儿没干完，但也不是故障。活动页上要看得出
        # 是「被叫停」而不是「跑完了」，否则下次没人知道还有剩下的番号没抓。
        handle.finish("cancelled", summary=summary, error=str(stopped))
        return stopped.exit_code
    handle.finish("succeeded", summary=summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    """入口只负责把这一趟登记进任务中心，正文在 `run` 里。"""
    args = build_parser().parse_args(argv)
    with cli_run("jav-covers", args.db, label="封面批量抓取") as handle:
        code = run(args, handle)
        if code:
            # 参数自检这类提前收工走不到 `run` 结尾的结算，在这里补上。
            handle.finish("cancelled", summary={"exit_code": code},
                          error=f"提前收工，退出码 {code}")
        return code


if __name__ == "__main__":
    raise SystemExit(main())
