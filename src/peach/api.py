"""应用组装点：把依赖建好挂到 `app.state`，再按顺序把各 router include 进来。

路由按用途拆分，各自 import 需要的东西，运行期依赖一律从
`request.app.state` 取：

| 模块 | 管什么 |
| --- | --- |
| `routes_auth` | 口令闸门与登录页，三个 `require_*` 依赖 |
| `routes_pages` | 单页界面、静态资产与全部前端路由落点 |
| `routes_configuration` | 本机配置与账号设置 |
| `routes_jav_browser` | JAV 用户脚本的专用权限、添加与状态查询 |
| `routes_media` | 播放、分片、缩图、封面、头像、外链圆标 |
| `routes_subtitles` | 字幕转换、SubtitleCat 中文字幕搜索与加载 |
| `routes_api` | JSON 契约出口，含两条 `/api/{route:path}` catch-all |

`include_router` 的顺序是契约的一部分，不是排版：FastAPI 按注册顺序匹配，
`routes_api` 的两条 catch-all 会吃掉一切 `/api/...`，所以它必须最后。
`/api/stream-plan` 和 `/api/stream-cancel` 住在 `routes_media`，那个 router 也因此
要排在 `routes_api` 前面。

留在本文件里的只有全应用一份的东西：依赖装配、`lifespan`、异常处理器、`/vendor`
挂载、响应头与压缩中间件和 `/healthz`。
"""
import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

from . import (__version__, browser_transport, feeds, tunnel, web_batch, web_contract,
               web_feeds, web_follow, web_settings)
from . import routes_api, routes_auth, routes_configuration, routes_jav_browser, routes_media, routes_pages, routes_subtitles
from .buildinfo import frozen_build
from .config import LOCATION_ROOT_DECLARATIONS, PeachSettings
from .ffmpeg import FFmpegResolver
from .follow_scheduler import FEED_JOB_ID, FollowScheduleConfig, FollowUpdateScheduler
from .follow_covers import FollowCoverService
from .follow_faces import FollowFaceIndex
from .follow_stream import FollowMediaResolver
from .http import HttpxTransport
from .health import inspect_database
from .media import (
    FilesystemBackend,
    MediaEngine,
    MediaNotFound,
    MediaOffline,
    MediaUnavailable,
)
from .mdns import create_mdns_publisher
from .mount_reachability import MountReachability
from .mp4repair import HeaderRepairStore
from .platform import location_mounts
from .previews import (
    COVER_THUMB_EDGE,
    COVER_THUMB_QUALITY,
    DerivedImageService,
    PhotoThumbnailService,
    PreviewService,
    cover_thumb_root,
    entity_thumb_root,
)
from .follow_secrets import credential_store_for
from .push_discovery import PushDiscoveryService
from .web_downloads import build_download_service
from .sample_images import SampleCache
from .sample_images import cache_root as sample_cache_root
from .providers import OpenCodeGoClient, default_registry
from .repository import LedgerDatabase, LedgerRepository
from .review_mirror import ReviewMirror
from .routes_auth import AssetLoginRequired, PageLoginRequired
# 两档缓存时长的实现在 `routes_media`，这里再导出：它们是对外的缓存契约，
# tests/test_follow_web.py 按 `api.MEDIA_CACHE_SECONDS` 断言两者的关系。
from .routes_media import AVATAR_CACHE_SECONDS, MEDIA_CACHE_SECONDS  # noqa: F401
from .segments import HlsSegmentService
from .streaming import StreamSessionRegistry
from .sync import LedgerSync
from .transcodes import TranscodeService


LOGGER = logging.getLogger(__name__)

#: 这个进程的构建身份，打包时埋在包根，进程内不变。源码运行时没有第二份代码，是 None。
#: 换生产托盘的脚本靠它确认跑起来的正是这次打出的包：版本号一次发布只推一格，同一个
#: 版本号下会有很多个构建，只比版本号证明不了二进制真的换掉了。
BUILD = frozen_build()

#: gzip 不碰的内容类型。Starlette 的默认名单已经排掉 `video/*`、`audio/*` 和各种
#: 已压缩的图片，这里再补两个它没排、而 Peach 会真的撞上的：
#: `application/octet-stream` 是通用字节流；`text/plain` 是 `FileResponse` 猜不出
#: 扩展名时的兜底值，`/stream` 与 `/photo` 都不传 media_type，一个没登记过扩展名的
#: 媒体文件会以 `text/plain` 出去——压它纯属烧 CPU。Peach 自己有意发的
#: `text/plain` 只有 "missing" 这类错误提示，本来就在 `minimum_size` 之下。
COMPRESSION_EXCLUDED_TYPES = (
    *DEFAULT_EXCLUDED_CONTENT_TYPES, "application/octet-stream", "text/plain",
)


#: Starlette 自己抛的那几种 HTTPException 带的是英文 detail，给人看之前换成中文。
_DEFAULT_DETAILS = {
    "Not Found": "这个地址下没有页面。",
    "Method Not Allowed": "这个地址不接受这种请求。",
}


def _offline_response(exc: MediaOffline) -> JSONResponse:
    """脱盘：来源盘整体不在，客户端据此显示「脱盘模式」而不是当成文件丢失。"""
    response = JSONResponse(
        {"error": "offline", "source": exc.source, "id": exc.asset_id},
        status_code=503,
    )
    response.headers["X-Peach-Offline"] = "1"
    return response


def _writer(sync: LedgerSync | None) -> bool:
    """这台是不是账本写入端。只读端不起任何会往账本里写的后台通道。"""
    return sync is None or not sync.read_only


def _start_tunnel(settings: PeachSettings, manager: tunnel.TunnelManager) -> None:
    """公网入口只按本次启动注入的设置开；不回头读设置文件里的当前值。"""
    if not (settings.tunnel_enabled and settings.configured):
        return
    try:
        manager.start(tunnel.plan_for_settings(settings))
    except tunnel.TunnelError:
        LOGGER.warning("Cloudflare Tunnel 未能启动", exc_info=True)


def _restore_task_center(contract) -> None:
    """开机时收拾任务中心。

    上一次服务被强杀的话，表里会留下几行停在 `running` 的记录，它们还占着互斥键——不先
    收掉，这一次开机后那几类任务一按就是 409。判据是 pid 还在不在与心跳有没有过期，两样
    都不满足才算被打断。

    后继是例外：它重新排队而不判 `interrupted`，冻在终态就再也没有重试的机会
    （ADR-0040 第六条）。排着的那些也在这一步被叫醒，接着跑完。
    """
    from .job_recovery import reconcile_paused
    if contract.task_runs.enabled:
        reconcile_paused(contract.db_path)
    recovered = contract.task_runs.recover_interrupted()
    if recovered:
        LOGGER.info("task center recovered %s interrupted run(s)", len(recovered))
    requeued = contract.task_runs.enabled and contract.followups.resume()
    if requeued:
        LOGGER.info("task center requeued %s followup(s)", len(requeued))


#: 开机在后台先算一遍的聚合：口味、复核、垃圾复核、关注的全部条目与筛选项各要几秒到
#: 十几秒，都按账本版本号缓存（`WebContract.cached_until_changed`），算好一次就一直用到
#: 账本或文件真的变了。
WARM_AGGREGATES = (("/api/taste", {"window": "all"}), ("/api/review", {}), ("/api/ads", {}),
                   ("/api/follow", {}))


def _start_ledger_services(settings: PeachSettings, contract, services) -> bool:
    """只读核对当前账本后，恢复任务并启动依赖账本的后台服务。"""
    status = inspect_database(settings.db_path)
    if not status["readable"] or status["schema"] != "current":
        LOGGER.warning("数据库未就绪，依赖数据库的后台任务保持停止（数据库：%s；结构：%s）；数据库准备完成后重启服务。",
                       status["database"], status["schema"])
        return False
    _restore_task_center(contract)
    from .web_job_recovery import restore
    if contract.task_runs.enabled:
        restore(contract)
    for service in services:
        service.start()
    return True


async def _warm_ledger_aggregates(ledger_ready: bool, contract, stopping: asyncio.Event) -> None:
    """走和真实请求同一条分派，缓存键一致，页面第一次打开就是现成的。"""
    if not ledger_ready:
        return
    for path, args in WARM_AGGREGATES:
        if stopping.is_set():
            return
        try:
            await asyncio.to_thread(web_contract.dispatch_api_get, contract, path, args)
        except Exception:
            # 预热失败不影响服务启动，这一页照旧在第一次打开时自己算。
            LOGGER.debug("aggregate warmup failed: %s", path, exc_info=True)


def _server_error_headers(request: Request, error: Exception, status_code=500):
    """服务错误按路由模板关联；请求参数、正文与鉴权头留在请求中。"""
    if status_code < 500:
        return {}
    from .error_reporting import event
    error_id = uuid.uuid4().hex
    event('http-request', error, error_id=error_id, method=request.method,
          route=getattr(request.scope.get('route'), 'path', '<unmatched>').lstrip('/'), status_code=status_code)
    return {'X-Peach-Error-ID': error_id, 'Cache-Control': 'no-store'}


def create_app(
    settings: PeachSettings | None = None,
    sync: LedgerSync | None = None,
    review_mirror: ReviewMirror | None = None,
    tunnel_manager: tunnel.TunnelManager | None = None,
) -> FastAPI:
    """`sync` 由 CLI 注入。测试直接建 app 时不传，复制与只读闸门整体不参与。"""
    settings = settings or PeachSettings()
    if tunnel_manager is None:
        tunnel_manager = tunnel.TunnelManager(settings.tunnel_state_root, settings.tunnel_log_root)
    database = LedgerDatabase(settings.db_path)
    contract = web_contract.WebContract(
        settings.db_path, settings.snapshot_root,
        candidate_root=settings.candidate_root,
        cover_root=settings.cover_root,
        avatar_root=settings.avatar_root,
        logo_root=settings.logo_root,
        poster_root=settings.poster_root,
        timeline_root=settings.timeline_root,
        photo_root=settings.photo_root,
        transcode_root=settings.transcode_root,
        stream_root=settings.stream_root,
        follow_state_root=settings.follow_state_root,
        entry_links_root=settings.entry_links_root,
        taste_history_root=settings.taste_history_output_root,
        taste_history_store=settings.taste_history_store,
        taste_history_import_root=settings.taste_history_import_root,
        taste_history_manifest=settings.taste_history_manifest,
        database=database,
    )
    contract.download_junk_candidates = lambda: web_batch.automatic_junk_candidates(contract)
    contract.download_organize_templates = lambda: web_settings.organize_templates(contract)
    repository = LedgerRepository(database)
    from .mount_reachability import source_roots
    mount_reachability = MountReachability(source_roots())
    resolver = FFmpegResolver(settings.ffmpeg_root)
    http_transport = HttpxTransport()
    follow_media_resolver = FollowMediaResolver(http_transport).with_credential_loader(
        lambda provider: web_follow._credential_store(contract).load(provider))
    # 检查更新、浏览与画面签名的取图共用这一份按站冷却：同一个站被限流，几处都得停。
    contract.follow_media_resolver = follow_media_resolver
    follow_cover_service = FollowCoverService(
        resolver, follow_media_resolver, settings.poster_root / "follow")
    contract.follow_faces = FollowFaceIndex(
        settings.poster_root / "follow-faces", http_transport,
        cover_root=settings.poster_root / "follow", gate=follow_media_resolver)
    filesystem = FilesystemBackend(
        settings.allowed_media_roots,
        settings.snapshot_root,
    )
    media_engine = MediaEngine(repository, filesystem)
    preview_service = PreviewService(
        repository, resolver, settings.snapshot_root, settings.poster_root,
        settings.avatar_root, settings.logo_root,
    )
    photo_service = PhotoThumbnailService(settings.photo_root)
    sample_cache = SampleCache(sample_cache_root(settings.photo_root))
    entity_thumb_service = DerivedImageService(entity_thumb_root(settings.avatar_root))
    cover_thumb_service = DerivedImageService(cover_thumb_root(settings.cover_root),
                                              edge=COVER_THUMB_EDGE, quality=COVER_THUMB_QUALITY)
    transcode_service = TranscodeService(resolver, settings.transcode_root)
    header_repairs = HeaderRepairStore(settings.transcode_root, resolver)
    hls_plan_executor = ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="PeachHlsPlan",
    )
    hls_service = HlsSegmentService(
        resolver, settings.stream_root, prefer_hardware=transcode_service.prefer_hardware,
        hardware=transcode_service.hardware,
    )
    mdns = create_mdns_publisher(
        settings.mdns_name, settings.mdns_port, secure=settings.tls_enabled,
        address=settings.mdns_address,
    ) if settings.mdns_enabled else None
    providers = default_registry()
    opencode_go = OpenCodeGoClient(transport=http_transport)
    review_mirror = review_mirror or ReviewMirror(
        settings.review_writer_origin,
        settings.review_writer_ca,
        settings.review_mirror_cache,
        token=settings.token,
        proxy=settings.review_writer_proxy,
    )
    follow_scheduler = FollowUpdateScheduler(
        settings.follow_state_root,
        lambda: web_follow.w_follow_check(contract, {"automatic": True}),
        available=sync is None or not sync.read_only,
    )
    push_discovery = PushDiscoveryService(
        state_root=settings.follow_state_root,
        secrets_root=settings.secrets_root,
        db_path=settings.db_path,
        declared_roots=LOCATION_ROOT_DECLARATIONS,
        mounts=location_mounts(),
        available=_writer(sync),
        ffprobe=resolver.ffprobe,
        after_ingest=contract.cache_bust,
    )
    # 云下载：远端完成后的文件由推送发现登记，这里只对账与兜底定向登记。凭据只在本机。
    from .download_postprocess import enqueue as enqueue_download_postprocess
    downloads = build_download_service(
        database=database, state_root=settings.follow_state_root,
        credentials=credential_store_for(settings.secrets_root,
                                         shared_root=contract.follow_shared_root),
        push_discovery=push_discovery, available=_writer(sync),
        after_ingest=lambda task: enqueue_download_postprocess(contract, task))
    contract.downloads = downloads
    from .jav_browser import JavBrowser
    from .web_resource_search import q_resource_search
    jav_browser = JavBrowser(contract, search=q_resource_search)
    contract.follow_scheduler = follow_scheduler
    # 订阅源拉取（ADR-0042）用同一个调度实现，只换 job id、状态文件与默认间隔。
    # 默认 6 小时：一位女优的 JavDB 演员页一天更新几条，比这更密只是把 JavDB 的配额
    # 花在一张没变的页面上。
    feed_scheduler = FollowUpdateScheduler(
        settings.follow_state_root,
        lambda: web_feeds.w_feed_check(contract, {"automatic": True}),
        available=_writer(sync),
        job_id=FEED_JOB_ID,
        filename="feed-schedule.json",
        default=FollowScheduleConfig(enabled=True,
                                     interval_minutes=feeds.DEFAULT_INTERVAL_MINUTES),
        unavailable_message="订阅源拉取只在写入端可用",
    )
    contract.feed_scheduler = feed_scheduler
    from .web_job_recovery import RecoveryService
    recovery_service = RecoveryService(contract, available=_writer(sync))
    contract.header_repairs = header_repairs
    contract.transcode_service = transcode_service
    # 只读端不写任务中心：`task_run` 也在账本里，reader 往里写会造成无法自动合并的
    # 分叉。活动页在只读端照常能看——读不受影响，只是看到的是写入端那台的记录。
    contract.task_runs.enabled = (
        (sync is None or not sync.read_only) and contract.task_runs.available())
    from .library_sharing import Service as LibrarySharing
    from . import settings_file
    sharing = LibrarySharing(settings_file.active(), contract)
    from .automatic_updates import AutomaticUpdates
    from .routes_configuration import managed_configuration
    automatic_updates = AutomaticUpdates(
        settings.follow_state_root,
        available=managed_configuration() and bool(settings.configured),
    )

    async def warm_startup_entries():
        """配置页首屏要的开机启动项状态，先在后台问一遍。

        那一格是 `/api/configuration` 同步路径上唯一一件慢事：读一个 `.lnk` 得起一个
        `powershell.exe`，本机三个快捷方式加起来 2.5 秒，而它正好挡在用户点开设置之后。
        这里先问一次，`desktop_startup` 按 `.lnk` 的指纹留着结论，等用户真点开时是现成的。
        """
        if not (managed_configuration() and settings.configured):
            return
        from . import desktop_startup
        try:
            await asyncio.to_thread(desktop_startup.snapshot)
        except Exception:
            # 预热失败不该影响服务起不起得来，配置页照旧自己现问一遍。
            logging.getLogger(__name__).debug("startup entry warmup failed", exc_info=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from .error_reporting import asyncio_handler
        loop = asyncio.get_running_loop()
        previous_error_handler = loop.get_exception_handler()
        loop.set_exception_handler(asyncio_handler)
        # 启动握手要等 cloudflared 连上边缘，可以占到几十秒；和 mDNS 一样交给线程，
        # 不要在事件循环里卡住整条服务的启动。
        await asyncio.to_thread(_start_tunnel, settings, tunnel_manager)
        ledger_ready = False
        if settings.configured:
            ledger_ready = _start_ledger_services(settings, contract,
                (follow_scheduler, feed_scheduler, push_discovery, downloads, recovery_service, jav_browser))
            automatic_updates.start()
            if ledger_ready:
                sharing.start()
            mount_reachability.start()
        _app.state.ledger_background_ready = ledger_ready
        warmup = asyncio.create_task(warm_startup_entries())
        aggregate_stop = asyncio.Event()
        aggregate_warmup = asyncio.create_task(_warm_ledger_aggregates(ledger_ready, contract, aggregate_stop))
        if mdns is not None:
            try:
                await asyncio.to_thread(mdns.start)
            except Exception:
                mdns.status = "unavailable"
                logging.getLogger(__name__).exception("mDNS publication failed")
        try:
            yield
        finally:
            _app.state.ledger_background_ready = False
            aggregate_stop.set()
            loop.set_exception_handler(previous_error_handler)
            warmup.cancel()
            recovery_service.stop()
            follow_scheduler.stop()
            feed_scheduler.stop()
            automatic_updates.stop()
            await asyncio.to_thread(sharing.stop)
            push_discovery.stop()
            await asyncio.to_thread(jav_browser.stop)
            downloads.stop()
            mount_reachability.stop()
            # to_thread 的查询在线程内释放连接后才算结束。
            await aggregate_warmup
            # 死链检查和资源对账的后台线程是 daemon，本来挡不住进程退出；这里显式收
            # 一下，免得在途的那一轮在解释器拆卸期间还继续查库、往没人读的状态里写。
            contract.stop_background_jobs()
            if mdns is not None:
                await asyncio.to_thread(mdns.stop)
            await asyncio.to_thread(tunnel_manager.stop)
            http_transport.close()
            hls_plan_executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="Peach API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.web_contract = contract
    app.state.library_sharing = sharing
    app.state.repository = repository
    app.state.media_engine = media_engine
    app.state.preview_service = preview_service
    app.state.photo_service = photo_service
    app.state.sample_cache = sample_cache
    app.state.entity_thumb_service = entity_thumb_service
    app.state.cover_thumb_service = cover_thumb_service
    app.state.transcode_service = transcode_service
    app.state.header_repairs = header_repairs
    app.state.hls_plan_executor = hls_plan_executor
    app.state.hls_service = hls_service
    app.state.mdns = mdns
    app.state.providers = providers
    app.state.opencode_go = opencode_go
    app.state.review_mirror = review_mirror
    app.state.http_transport = http_transport
    app.state.follow_media_resolver = follow_media_resolver
    app.state.follow_cover_service = follow_cover_service
    app.state.follow_scheduler = follow_scheduler
    app.state.automatic_updates = automatic_updates
    app.state.push_discovery = push_discovery
    app.state.downloads = downloads
    app.state.jav_browser = jav_browser
    app.state.mount_reachability = mount_reachability
    app.state.stream_sessions = StreamSessionRegistry()
    from .strm_stream import StrmResources
    app.state.strm_resources = StrmResources()
    import secrets
    app.state.external_playback_key = secrets.token_bytes(32)
    app.state.sync = sync
    app.state.tunnel = tunnel_manager

    # 媒体三异常的统一出口，路由里不再手抄同一组 try/except。
    # 404/503/404 是逐个异常的状态码契约，不许并成一种。
    @app.exception_handler(MediaNotFound)
    def _media_not_found_handler(request: Request, exc: MediaNotFound):
        return JSONResponse({"error": "no such id"}, status_code=404)

    @app.exception_handler(MediaOffline)
    def _media_offline_handler(request: Request, exc: MediaOffline):
        return _offline_response(exc)

    @app.exception_handler(MediaUnavailable)
    def _media_unavailable_handler(request: Request, exc: MediaUnavailable):
        return JSONResponse({"error": "unavailable"}, status_code=404)

    # 401 有三种形态，按路由类分组保留（不许统一成一种）：页面路由跳登录页，
    # 页面资产返回 PlainText 提示，API 与媒体路由返回 JSON。
    @app.exception_handler(PageLoginRequired)
    def _page_login_required_handler(request: Request, exc: PageLoginRequired):
        return RedirectResponse(
            "/login?next=" + quote(exc.next_path or "/", safe="/"), status_code=303,
        )

    @app.exception_handler(AssetLoginRequired)
    def _asset_login_required_handler(request: Request, exc: AssetLoginRequired):
        return PlainTextResponse("需要 ?t=口令", status_code=401)

    # 挂在 Starlette 的基类上：路由没匹配到的 404 是它抛的，不是 FastAPI 子类，
    # 只挂子类的话地址栏敲错一个字母仍会看到一行 JSON。
    @app.exception_handler(StarletteHTTPException)
    def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        headers = _server_error_headers(request, exc, exc.status_code)
        detail = exc.detail
        if isinstance(detail, dict):
            # 表单校验那种带字段明细的 4xx：`message` 是总话，其余键原样带给页面。
            body = {"error": str(detail.get("message", "")), **{k: v for k, v in detail.items() if k != "message"}}
        else:
            body = {"error": _DEFAULT_DETAILS.get(str(detail), str(detail))}
        # 浏览器地址栏直接打开一个页面路径撞上 403／404／409 时，给人看的是一页话，
        # 不是一行 JSON；`/api/` 与脚本取数（Accept 里没有 text/html）照旧回 JSON。
        navigation = (request.method in {"GET", "HEAD"} and not request.url.path.startswith("/api/")
                      and "text/html" in request.headers.get("accept", ""))
        if navigation:
            return HTMLResponse(routes_pages.error_page(exc.status_code, body["error"]),
                                status_code=exc.status_code, headers=headers)
        return JSONResponse(body, status_code=exc.status_code, headers=headers)

    @app.exception_handler(Exception)
    def _server_exception_handler(request: Request, exc: Exception):
        headers = _server_error_headers(request, exc)
        return JSONResponse({'error': '服务处理失败，请导出诊断报告',
                             'error_id': headers['X-Peach-Error-ID']}, status_code=500, headers=headers)

    # 第三方前端依赖固定版本并随 Peach 自托管；局域网断网时仍可播放。
    app.mount(
        "/vendor",
        StaticFiles(directory=settings.vendor_path, check_dir=False),
        name="vendor",
    )

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/vendor/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        # 公网入口开着的时候整个站都在互联网上。这一条覆盖每一个响应——页面、静态
        # 资源和 API 都算，页面里的 `<meta name="robots">` 只管得到 HTML 那几页。
        response.headers["X-Robots-Tag"] = routes_pages.ROBOTS_TAG
        return response

    # JSON 契约、页面脚本与样式是仅有的几类大文本响应，压下去省的字节最多：
    # `/api/items`、主界面 bundle 与样式都通过内容类型选择压缩。
    # `add_middleware` 是 `insert(0)`，最后加的在最外层，所以压缩看到的是上面
    # `no_store` 补完 Cache-Control 之后的最终响应头。
    # 自己写内容类型闸门的 ASGI 中间件是重复劳动：Starlette 这个已经按 Content-Type
    # 排除、跳过 206 与已编码响应、逐块流式压缩，还会把大块丢到工作线程去压，
    # 不占事件循环。只需要把它的排除名单补齐（见 COMPRESSION_EXCLUDED_TYPES）。
    app.add_middleware(
        GZipMiddleware, exclude_content_types=COMPRESSION_EXCLUDED_TYPES,
    )

    # 健康检查常被 HEAD 探测（`curl -I`、各种 uptime 工具）。本仓库其他公开端点
    # 都显式声明了 GET+HEAD，只有这个漏了，HEAD 会拿到 405。
    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz(request: Request, ready: bool = False):
        from .health import database_status, readiness
        if ready:
            result = readiness(settings, background_ready=getattr(request.app.state, "ledger_background_ready", None))
            return JSONResponse(result, status_code=200 if result["ready"] else 503,
                                headers={"Cache-Control": "no-store"})
        # 不探测共享目录或迁移数据库；健康检查必须无副作用。
        ffmpeg = resolver.ffmpeg()
        read_only = bool(sync is not None and sync.read_only)
        tunnel_state = tunnel_manager.snapshot()
        from .diagnostics import health_components
        components = health_components(settings, app.state.mount_reachability.summary())
        return {"ok": True, "service": "peach-api", "version": __version__,
                "checks": components,
                # 打包这份代码的提交。源码运行时是 null：跑的就是检出本身。
                "build_commit": BUILD.commit if BUILD else None,
                # 这台机器跑过 `peach init` 没有。未配置时服务照常起，只是没有数据。
                "configured": settings.configured,
                # 这次请求的发起方能不能改这台机器的配置（独立包、回环地址）。它随调用方
                # 变化，只决定管理菜单里「配置」显不显示；拒绝写入的判定在端点自己那里。
                "configurable": routes_configuration.configurable(request),
                "db": database_status(settings.db_path),
                "ffmpeg": ffmpeg.source if ffmpeg else "unavailable",
                "media_mounts": app.state.mount_reachability.summary(),
                "mdns": mdns.status if mdns is not None else "disabled",
                "mdns_backend": mdns.backend if mdns is not None else None,
                "mdns_service": mdns.name if mdns is not None else None,
                "mdns_service_host": mdns.hostname if mdns is not None else None,
                "mdns_address": mdns.address if mdns is not None else None,
                "ledger_sync": sync.status if sync is not None else "disabled",
                "ledger_read_only": read_only,
                "ledger_read_only_message": sync.read_only_message if read_only else None,
                "ledger_writer_origin": settings.review_writer_origin if read_only else None,
                "scheme": "https" if settings.tls_enabled else "http",
                # 采集用的浏览器窗口正等着人点验证的站，供排查时看一眼；窗口本身就是提醒，不另弹通知（ADR-0065）。
                "attention": browser_transport.attention(),
                # 健康检查可能被公网探针访问，不能在这里回传随机 Tunnel URL。
                "tunnel": {"enabled": settings.tunnel_enabled, "state": tunnel_state.state},
        }

    # 顺序即契约：catch-all 最后。`routes_media` 里有 `/api/stream-plan` 与
    # `/api/stream-cancel` 两条具名 API，所以它也要排在 `routes_api` 之前。
    app.include_router(routes_auth.router)
    app.include_router(routes_pages.router)
    app.include_router(routes_configuration.router)
    app.include_router(routes_jav_browser.create_router(routes_configuration.local_only))
    app.include_router(routes_media.router)
    app.include_router(routes_subtitles.router)
    app.include_router(routes_api.router)
    return app
