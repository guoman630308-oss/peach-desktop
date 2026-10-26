"""Peach 与 amane 刮削站点之间的薄桥：一次子进程，stdout 只有一行 JSON。

形状与 Peach 调 Javinizer-Go 的方式一致（`peach.metadata.JavinizerGoProvider`）：无端口、
无状态、无常驻，每次由 Peach 起一个子进程、读一行 JSON、看退出码。amane 那边只用爬虫层
与网络层——`amane.crawlers.sites.<站>`、`amane.crawlers.http`、`amane.net.*`；不碰
`amane.aggregate`（它把配置层与数据库层一起拖进来，聚合逻辑 Peach 自己在
`metadata_policy` 里做），也不经 `observability.invoke_source`（失败原因会被它吞进
Recorder，返回值只剩站点名）。这里自己 catch `SourceError`，把 `FailureReason` 那 17 档
原样写进 JSON，冷却与分档由 Peach 一侧决定。

已知代价：`amane.crawlers` 的包 `__init__` 会连带 SQLAlchemy 等模块（实测导入约 0.6～1 秒），
从子模块进也绕不开；这是每次子进程的固定开销，接受它换来的是不改上游一行。

amane 为 GPL-3.0（<https://github.com/sqzw-x/amane>）；本文件属于 Peach（AGPL-3.0-or-later），
两者以进程边界相接，见 ADR-0043。运行环境由 `pyproject.toml` / `uv.lock` 钉死。

退出码：0 至少一站取到资料；2 问的站都明确说没有；3 没有一站取到且至少一站出错；
4 参数错误（未知站名、番号为空）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

#: 桥认得的站点：站名 → （`amane.crawlers.sites` 下的模块名，类名）。站名是 Peach 的来源名，
#: 多数与 amane 同名；`makers` 是 amane 的 `official`：它按番号的系列前缀查自带的片商表
#: （`MANUFACTURER_SERIES`），路由到二十九家片商官网的作品页，前缀不在表里就不发请求、直接答没有，
#: 所以不带 `SiteConfig` 也跑得起来。Peach 那边 `official` 是来源分级的名字，换个站名免得两义。
#: 不列 `r18dev`（要本地 PostgreSQL 镜像）与 `theporndb`（要 API token）：两者没有 `SiteConfig`
#: 就跑不起来，而桥刻意不带配置。
SITES: dict[str, tuple[str, str]] = {
    "airav": ("airav", "AiravCrawler"),
    "avsox": ("avsox", "AvsoxCrawler"),
    "dahlia": ("dahlia", "DahliaCrawler"),
    "dmm": ("dmm", "DmmCrawler"),
    "faleno": ("faleno", "FalenoCrawler"),
    "fc2": ("fc2", "FC2Crawler"),
    "fc2club": ("fc2club", "FC2ClubCrawler"),
    "freejavbt": ("freejavbt", "FreejavbtCrawler"),
    "getchu": ("getchu", "GetchuCrawler"),
    "giga": ("giga", "GigaCrawler"),
    "iqqtv": ("iqqtv", "IqqtvCrawler"),
    "jav321": ("jav321", "Jav321Crawler"),
    "javbus": ("javbus", "JavBusCrawler"),
    "javdb": ("javdb", "JavDBCrawler"),
    "javlibrary": ("javlibrary", "JavLibraryCrawler"),
    "kin8": ("kin8", "Kin8Crawler"),
    "makers": ("official", "OfficialCrawler"),
    "mgstage": ("mgstage", "MGStageCrawler"),
    "prestige": ("prestige", "PrestigeCrawler"),
    "xcity": ("xcity", "XCityCrawler"),
}

LANGUAGES = ("jp", "zh_cn", "zh_tw", "en")

EXIT_FOUND, EXIT_NOT_FOUND, EXIT_FAILED, EXIT_USAGE = 0, 2, 3, 4

#: 上游 `FailureReason` 的全部取值，照抄 `amane/net/errors.py`。桥不解释它们，只保证写进
#: JSON 的 `reason` 落在这张表里；上游加档时这里要跟着改，Peach 一侧的映射也是。
FAILURE_REASONS = (
    "http_error", "api_error", "not_found", "rate_limited", "server_error", "timeout", "network",
    "cloudflare_challenge", "cloudflare_blocked", "ip_banned", "geo_restricted",
    "age_verification", "empty_response", "no_usable_metadata", "parse_error",
    "crawler_unavailable", "unexpected",
)


@dataclass(frozen=True)
class Runtime:
    """桥用到的 amane 那几样东西。真实实现由 `load_runtime()` 给；单测塞假的进来。

    `crawler(site)` 返回爬虫类；`make_client(proxy, timeout, rate)` 返回传给爬虫构造函数的
    `HttpClient`；`query(number)` 与 `options(language)` 构造 `SearchQuery` / `FetchOptions`；
    `source_error` 是要 catch 的异常类型，实例上有 `reason` / `http_status` / `detail` / `url`。
    """

    crawler: Callable[[str], type]
    make_client: Callable[[str | None, float, float], Any]
    query: Callable[[str], Any]
    options: Callable[[str | None], Any]
    source_error: type[BaseException]
    version: str = ""


def load_runtime() -> Runtime:
    """真实 amane。只在这里 import，单测不必装它。"""
    import importlib

    import structlog

    # amane 的日志走 structlog，默认打到 stdout——那正是 JSON 要独占的那条流。
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    from amane.crawlers.http import HttpClient
    from amane.crawlers.models import FetchOptions, SearchQuery
    from amane.enums import Language
    from amane.net.errors import SourceError
    from amane.net.http import RateLimiters, WebClient

    from importlib.metadata import PackageNotFoundError, version as installed_version

    try:
        version = installed_version("amane")
    except PackageNotFoundError:  # 以源码路径而不是安装件运行时没有 dist-info
        version = ""

    def crawler(site: str) -> type:
        module_name, class_name = SITES[site]
        module = importlib.import_module(f"amane.crawlers.sites.{module_name}")
        return getattr(module, class_name)

    def make_client(proxy: str | None, timeout: float, rate: float):
        # 上游 `max_retries=1` 限普通重试；403/406 另可轮换三次指纹，单个地址最多五次请求。
        # Peach 用 60 秒子进程总时限约束整次查询。不带 BrowserClient：挑战页报
        # `cloudflare_challenge`，不切浏览器渲染。
        limiters = RateLimiters(default_rate=rate)
        return HttpClient(WebClient(limiters=limiters, proxy=proxy, timeout=timeout, max_retries=1))

    return Runtime(
        crawler=crawler, make_client=make_client,
        query=lambda number: SearchQuery(number=number),
        options=lambda language: FetchOptions(language=Language(language)) if language else None,
        source_error=SourceError, version=str(version or ""),
    )


def _dump(metadata: Any) -> dict:
    """`MediaMetadata` → JSON 形状。pydantic 的 `model_dump` 优先，假对象退回 `vars`。"""
    if hasattr(metadata, "model_dump"):
        return metadata.model_dump(mode="json")
    if isinstance(metadata, dict):
        return dict(metadata)
    return dict(vars(metadata))


def failure_record(error: BaseException) -> dict:
    """一次 `SourceError` 写成 JSON 里的一条。`reason` 不在表里就按 `unexpected` 记并把原值留在 `detail`。"""
    reason = str(getattr(error, "reason", "") or "")
    detail = str(getattr(error, "detail", "") or "")
    if reason not in FAILURE_REASONS:
        detail = f"{reason}: {detail}" if reason else detail
        reason = "unexpected"
    record: dict[str, Any] = {"status": "failed", "reason": reason}
    status = getattr(error, "http_status", None)
    if status is not None:
        record["http_status"] = int(status)
    if detail:
        record["detail"] = detail[:500]
    url = getattr(error, "url", None)
    if url:
        record["url"] = str(url)
    return record


async def query_site(runtime: Runtime, site: str, client: Any, number: str,
                     language: str | None) -> dict:
    """问一站，返回这一站的 JSON 记录；异常全部收成 `failed`，不让一站的崩溃带走整批。"""
    started = time.monotonic()
    record: dict[str, Any]
    try:
        crawler = runtime.crawler(site)(client=client, config=None)
        metadata = await crawler.fetch(runtime.query(number), runtime.options(language))
        if metadata is None:
            # 上游把「搜不到」与「页面解析不出资料」都返回 None，不抛异常。
            record = {"status": "not_found", "reason": "not_found"}
        else:
            record = {"status": "found", "metadata": _dump(metadata)}
    except runtime.source_error as error:
        record = failure_record(error)
    except Exception as error:  # noqa: BLE001 - 一站的意外不能拖垮整批
        record = {"status": "failed", "reason": "unexpected",
                  "detail": f"{type(error).__name__}: {error}"[:500]}
    record["elapsed_s"] = round(time.monotonic() - started, 2)
    return record


async def run(runtime: Runtime, number: str, sites: list[str], *, language: str | None,
              proxy: str | None, timeout: float, rate: float, direct_sites: tuple[str, ...] = ()) -> dict:
    """并发问每一站；限速器按主机计，不同站互不排队。"""
    client = runtime.make_client(proxy, timeout, rate)
    direct = runtime.make_client(None, timeout, rate) if direct_sites and proxy else client
    records = await asyncio.gather(
        *(query_site(runtime, site, direct if site in direct_sites else client, number, language) for site in sites))
    return {
        "number": number,
        "language": language or "",
        "sites": dict(zip(sites, records)),
        "amane": {"version": runtime.version},
    }


def exit_code(report: dict) -> int:
    statuses = [record.get("status") for record in report.get("sites", {}).values()]
    if "found" in statuses:
        return EXIT_FOUND
    if statuses and all(status == "not_found" for status in statuses):
        return EXIT_NOT_FOUND
    return EXIT_FAILED


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--number", required=True, help="规范化番号")
    parser.add_argument("--sites", required=True, help="逗号分隔的站名，见 SITES")
    parser.add_argument("--language", default="jp", choices=(*LANGUAGES, ""),
                        help="amane 的 Language；空串表示不指定")
    parser.add_argument("--proxy", default=None,
                        help="代理地址；不给就不走代理（libcurl 仍会读环境变量）")
    parser.add_argument("--direct-sites", default="", help="本次查询中强制直连的站名，逗号分隔")
    parser.add_argument("--timeout", type=float, default=30.0, help="单次请求超时（秒）")
    parser.add_argument("--rate", type=float, default=0.5, help="每主机每秒请求数上限")
    parser.add_argument("--output", default="json", choices=("json",))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, runtime: Runtime | None = None,
         out=None) -> int:
    out = out or sys.stdout
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else 0
    number = str(args.number or "").strip()
    sites = [part.strip() for part in str(args.sites).split(",") if part.strip()]
    unknown = [site for site in sites if site not in SITES]
    direct_sites = tuple(part.strip() for part in args.direct_sites.split(",") if part.strip())
    unknown.extend(site for site in direct_sites if site not in sites)
    if not number or not sites or unknown:
        message = ("未知站名：" + "、".join(unknown)) if unknown else "番号与站名都不能为空"
        print(json.dumps({"error": message}, ensure_ascii=False), file=out, flush=True)
        return EXIT_USAGE
    try:
        active = runtime or load_runtime()
    except Exception as error:  # noqa: BLE001 - venv 坏了也要以 JSON 报出去
        print(json.dumps({"error": f"amane 加载失败：{type(error).__name__}: {error}"},
                         ensure_ascii=False), file=out, flush=True)
        return EXIT_FAILED
    report = asyncio.run(run(active, number, sites, language=args.language or None,
                             proxy=args.proxy or None, timeout=args.timeout, rate=args.rate,
                             direct_sites=direct_sites))
    print(json.dumps(report, ensure_ascii=False, default=str), file=out, flush=True)
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
