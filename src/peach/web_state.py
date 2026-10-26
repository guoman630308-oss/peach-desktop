"""一个应用实例的库连接、聚合缓存与后台任务句柄。

`WebContract` 是 web 层唯一的可变状态载体，刻意不用模块级全局：测试和真实服务在
同一个解释器里各建自己的实例，任何一个全局都会让它们串到同一个库上。

它单独成一个模块，是为了把依赖方向钉死成一个走法——**它不认识任何域处理器**。
`web_catalog`、`web_entity`、`web_stats`、`web_batch` 全都 import 它，所以它一旦反
过来 import 其中任何一个，整层立刻循环。判据就是这个文件里不许出现 `from .web_`。

`CACHE_TTL` 与账本版本号归这里，因为它们决定本文件 `cached()`／`cached_lru()` 的寿命。内联 favicon 也放这里：它是
唯一一个没有磁盘文件的静态资源，而 `api` 一直从契约模块取它，这次只搬位置。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time

from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from .catalog_rules import normalise_code_key
from .config import (
    COVER_DIR,
    DATA_ROOT,
    GENERATED_DIR,
    SECRETS_DIR,
    SHARED_CREDENTIAL_ROOT,
    SOURCES_DIR,
    STATE_DIR,
    TOOLS_DIR,
)
from . import avatar_face
from . import brand_marks
from . import jav_poster_crop
from .jobs import BackgroundJob
from .media import normalized_path
# `previews` 是取图那一侧，不是 web 域处理器：依赖方向仍然只有一个走法。落盘名的
# 规则必须和 `/logo` 用的是同一个函数，否则可用性判定迟早和取图对不上。
from .previews import ENTITY_IMAGE_KINDS, LOGO_VARIANTS, entity_image_key, logo_key
from .repository import LedgerDatabase
from .task_runs import TaskRunStore


class AvatarRootIndex(NamedTuple):
    """`avatar_root` 一次扫描的产物。两样东西同处一个目录，分开扫就是白扫两遍。

    `entity_images` 是已装实体图的 casefold 落盘名到它的版本（修改时间的十六进制纳秒），
    `generated` 是已经裁好的头像资产 id。两者的判据不一样，故意不合并成一个集合：实体图
    有就是有、没有就是没有，而头像是按需生成的，「目录里没有」只说明还没裁过。
    """

    entity_images: Mapping[str, str]
    generated: frozenset[int]





FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#0B0B0D"/><defs><linearGradient id="pg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FF9A76"/><stop offset="1" stop-color="#F2557B"/></linearGradient></defs><path d="M16 28c-5.7 0-9.7-3.6-9.7-8.6 0-4.3 2.8-7.6 6.5-7.6 1.4 0 2.4.5 3.2 1.1.8-.6 1.8-1.1 3.2-1.1 3.7 0 6.5 3.3 6.5 7.6C25.7 24.4 21.7 28 16 28z" fill="url(#pg)"/><path d="M16 13.4V27" stroke="#0B0B0D" stroke-width="1.1" opacity=".3" stroke-linecap="round"/><path d="M17.1 11.7c.6-2.8 2.8-4.6 5.6-4.8-.2 2.8-2.2 4.7-5.6 4.8z" fill="#5FB95F"/><path d="M16 11.9c0-1.9.5-3.4 1.5-4.5" stroke="#8A5A3B" stroke-width="1.5" stroke-linecap="round" fill="none"/></svg>')

CACHE_TTL = 90


def path_version(path: Path) -> int | None:
    """文件或目录的修改时间，缓存键拿它认「变了没有」；不存在就是 None。

    目录的时间在里面新增、删除、换名文件时变，原地覆写已有文件时不变，所以会被这样
    读的边车一律先写临时文件再换名（`avatar_face.write_sidecar`）。
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class WebContract:
    """单个应用实例的数据库、写锁和聚合缓存；不共享模块级可变状态。"""

    def __init__(self, db_path: Path, snapshot_root: Path | None = None,
                 candidate_root: Path | None = None,
                 cover_root: Path | None = None,
                 avatar_root: Path | None = None,
                 logo_root: Path | None = None,
                 marks_root: Path | None = None,
                 poster_root: Path | None = None,
                 timeline_root: Path | None = None,
                 photo_root: Path | None = None,
                 transcode_root: Path | None = None,
                 stream_root: Path | None = None,
                 follow_sources_root: Path | None = None,
                 follow_secrets_root: Path | None = None,
                 follow_state_root: Path | None = None,
                 follow_shared_root: Path | None = None,
                 entry_links_root: Path | None = None,
                 tools_root: Path | None = None,
                 taste_history_root: Path | None = None,
                 taste_history_store: Path | None = None,
                 taste_history_import_root: Path | None = None,
                 taste_history_manifest: Path | None = None,
                 database: LedgerDatabase | None = None):
        # 候选 CSV 的目录做成实例属性而不是模块常量，复核层才能在临时目录里被测试。
        self.candidate_root = Path(candidate_root) if candidate_root is not None else GENERATED_DIR
        self.cover_root = Path(cover_root) if cover_root is not None else COVER_DIR
        self.avatar_root = Path(avatar_root) if avatar_root is not None else GENERATED_DIR / "avatars"
        # `/logo` 就是从这里读；批准候选等于把图装进这个目录。
        self.logo_root = Path(logo_root) if logo_root is not None else GENERATED_DIR / "logos"
        # 随仓库分发的标识（ADR-0026）。和上面同一个道理：读模块常量的话，用临时
        # `logo_root` 造「没装图」的测试会撞上仓库里那 198 个，判定于是说反话。
        self.marks_root = (Path(marks_root) if marks_root is not None
                           else brand_marks.STUDIOS_DIR)
        self.poster_root = Path(poster_root) if poster_root is not None else GENERATED_DIR / "posters"
        # 时间轴预览的接触印相。和上面几个不一样，它不是取不到就现做的缓存：一张图要抽
        # 一百帧，只能靠采集任务提前铺好，没铺到的片子悬停时退回九宫格。
        self.timeline_root = (Path(timeline_root) if timeline_root is not None
                              else GENERATED_DIR / "timeline")
        self.photo_root = Path(photo_root) if photo_root is not None else GENERATED_DIR / "photo-thumbs"
        self.transcode_root = (Path(transcode_root) if transcode_root is not None
                               else GENERATED_DIR / "transcodes")
        self.stream_root = Path(stream_root) if stream_root is not None else GENERATED_DIR / "stream-segments"
        # API construction passes every managed cache root. A bare WebContract is also used
        # by isolated unit tests; it must never wander into the machine's real generated tree.
        self.resource_cleanup_enabled = any(
            root is not None for root in (poster_root, photo_root, transcode_root, stream_root))
        # 追更的原始证据与本机凭据目录同样做成实例属性，测试才能落在临时目录里。
        self.follow_sources_root = (Path(follow_sources_root)
                                    if follow_sources_root is not None else SOURCES_DIR)
        self.follow_secrets_root = (Path(follow_secrets_root)
                                    if follow_secrets_root is not None else SECRETS_DIR)
        self.follow_state_root = (Path(follow_state_root)
                                  if follow_state_root is not None else STATE_DIR)
        # 人物页外部入口的开关与镜像域名。和上面几个同一个道理：走实例属性，测试才
        # 落得进临时目录，不会读到这台机器真实的偏好。
        self.entry_links_root = (Path(entry_links_root)
                                 if entry_links_root is not None else STATE_DIR)
        # 外部工具区（FFmpeg、amane 桥的 venv）。测试给临时目录，重建桥就不会碰真实工具区。
        self.tools_root = Path(tools_root) if tools_root is not None else TOOLS_DIR
        # 共享副本只承载**声明为可同步**的凭据字段，见 follow_secrets.SYNCABLE_FIELDS。
        # 复制关掉时 SHARED_CREDENTIAL_ROOT 是 None，凭据只留在本机。
        self.follow_shared_root = (Path(follow_shared_root)
                                   if follow_shared_root is not None
                                   else SHARED_CREDENTIAL_ROOT)
        # 浏览历史口味分析的产出目录，`scripts/taste_history.py --output` 的默认值。
        self.taste_history_root = (Path(taste_history_root)
                                   if taste_history_root is not None
                                   else DATA_ROOT / "review" / "taste-history")
        self.taste_history_store = (Path(taste_history_store)
                                    if taste_history_store is not None
                                    else SOURCES_DIR / "taste-history" / "history.sqlite")
        self.taste_history_import_root = (Path(taste_history_import_root)
                                          if taste_history_import_root is not None
                                          else SOURCES_DIR / "taste-history" / "imports")
        self.taste_history_manifest = (Path(taste_history_manifest)
                                       if taste_history_manifest is not None
                                       else STATE_DIR / "taste-history" / "manifest.json")
        self.database = database or LedgerDatabase(db_path)
        self.db_path = self.database.db_path
        self.snapshot_root = Path(snapshot_root) if snapshot_root is not None else None
        #: 键 → (算出时刻, 输入版本, 结果)，见 `cached()`。
        self.cache: OrderedDict[str, tuple[float, object, object]] = OrderedDict()
        self.cache_lock = threading.Lock()
        #: 按资产取键的读缓存，LRU 限界。键空间不封闭的场景走这里，见 `cached_lru()`。
        self.keyed_cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
        #: 每次 cache_bust 递增。在途计算据此判断自己出发后缓存是否失效过。
        self.cache_generation = 0
        self.revision_cache_keys: set[str] = set()
        self.aggregate_locks: dict[str, threading.RLock] = {}
        self.follow_check_lock = threading.Lock()
        #: 任务中心。每个后台任务的开始、进度与结束都写这一张表，`/tasks` 只读它。
        #: 只读端由 `api` 关掉写入：那边的账本是复制来的，写一行就是一处合不回去的分叉。
        self.task_runs = TaskRunStore(self.database)
        #: 云下载后处理复用 Web 层的垃圾评分与整理设置，但核心后继不反向依赖 Web。
        #: `api` 作为组合根接入真实实现；裸契约用于单元测试时保持无副作用。
        self.download_junk_candidates = lambda: []
        self.download_organize_templates = lambda: {}
        #: 后继调度（ADR-0040）。导入 `avatar_followup` 就是它的登记动作：调度器本身
        #: 不认识任何一种后继，登记表在 `followups.REGISTRY` 里。
        from . import avatar_followup  # noqa: F401  登记补头像后继
        from . import creator_identity_followup  # noqa: F401  登记查创作者身份后继
        from . import download_postprocess  # noqa: F401  登记云下载后处理
        from . import feed_followup  # noqa: F401  登记取新作资料后继
        from . import follow_creators  # noqa: F401  登记关注作者建档后继
        from . import performer_alias_followup  # noqa: F401  登记补女优别名后继
        from . import performer_profile_followup  # noqa: F401  登记补女优资料后继
        from . import sample_followup  # noqa: F401  登记补番号样张后继
        from . import seed_followup  # noqa: F401  登记导入实体种子后继
        from . import studio_followup  # noqa: F401  登记补厂牌官网与标识后继
        from .followups import FollowupRunner
        self.followups = FollowupRunner(self)
        self.follow_job = self._job("PeachFollowCheckJob", "follow-check")
        self.feed_job = self._job("PeachFeedCheckJob", "feed-check")
        self.want_job = self._job("PeachWantScrapeJob", "want-scrape")
        self.follow_resolve_job = self._job("PeachFollowResolveJob", "follow-resolve")
        self.taste_refresh_job = self._job("PeachTasteRefreshJob", "taste-refresh")
        self.link_prune_job = self._job("PeachLinkPruneJob", "link-prune")
        self.scraping_cover_job = self._job("PeachScrapingCoverJob", "scraping-cover")
        self.amane_bridge_job = self._job("PeachAmaneBridgeJob", "amane-bridge")
        self.library_processing_job = self._job(
            "PeachLibraryProcessingJob", "library-processing")
        self.thumbnail_job = self._job(
            "PeachTimelineThumbnailJob", "timeline-thumbnails")
        self.resource_apply_job = self._job("PeachResourceApplyJob", "resource-apply")
        self.media_repair_job = self._job("PeachMediaRepairJob", "media-repair")
        # 整理（ADR-0039）：同一时间只跑一批，互斥由这个任务自己的键提供。
        self.organize_job = self._job("PeachOrganizeJob", "organize")
        self.follow_scheduler = None
        #: 关注卡缩略图的画面签名缓存（`follow_faces.FollowFaceIndex`），由 `api` 接上；
        #: 裸契约不联网，组里只按缩略图地址认同一张。
        self.follow_faces = None
        #: 订阅源的定时拉取（ADR-0042）。与追更共用 `follow_scheduler` 那一个实现，
        #: 只是各占一个 job id 与一份状态文件；由 `api` 在装配时接上。
        self.feed_scheduler = None
        #: 云下载（`downloads.DownloadService`），由 `api` 在装配时接上；没接上时
        #: `/api/downloads` 回「云下载没有启用」。
        self.downloads = None
        #: 批量修 MP4 头要的两件东西，由 `api` 在装配时接上：它们属于播放链路，
        #: 建在 app 那一侧，契约这边只留接口。没接上时修复端点会说清楚。
        self.header_repairs = None
        self.transcode_service = None
        # 两块后台任务的锁、状态和线程都归 BackgroundJob 管，契约上只留这两个字段。
        # 任务 id 的键名沿用各自原有的名字：它随公开投影下发，是前端契约。
        self.resource_scan = self._job(
            "PeachResourceScanJob", "resource-scan", id_key="scan_id")
        self.link_check = self._job("PeachLinkCheckJob", "link-check", id_key="check_id")
        self._fts_available: bool | None = None
        self.database.after_commit = self.cache_bust

    def _job(self, name: str, task_key: str, *, id_key: str = "job_id") -> BackgroundJob:
        """建一个后台任务并接进任务中心。task_key 就是它在活动页上的身份。"""
        return BackgroundJob(name, id_key=id_key, task_key=task_key,
                             runs=self.task_runs, followup_runner=self.followups)

    def cached(self, key, fn, *, ttl: float = CACHE_TTL, version=None):
        """带 TTL 的读缓存。`fn` 刻意在锁外算——它会读 CSV、查库，拿着锁算会把
        并发请求全串起来。

        `version` 是这份结果依赖的输入的版本，与存下的不同就算没命中。一个键只存
        最新算的那一版：封面刮削期间目录版本一直在变，按版本另起键会把整目录索引
        一份份堆在内存里。

        代价是计算期间缓存可能被 `cache_bust()` 清掉，那份还没写回的值就是失效前的
        快照。复核页正是这个场景：`q_review` 在算，用户批准了一条候选，
        `w_review_decision` 调 `cache_bust`；旧实现照样把批准前的快照写回去，
        于是用户批准完刷新，看到的还是批准前的列表，而且持续整整一个 TTL。

        用代次挡住：写回前确认这期间没有失效过，否则丢弃这次结果。
        """
        now = time.monotonic()
        with self.cache_lock:
            hit = self.cache.get(key)
            if hit and hit[1] == version and now - hit[0] < ttl:
                self.cache.move_to_end(key)
                return hit[2]
            generation = self.cache_generation
        value = fn()
        with self.cache_lock:
            if generation == self.cache_generation:
                # 时间戳沿用进入时的 now：算得比 TTL 还久的结果直接算过期，
                # 宁可下次重算，也不要把一份已经旧了的数据当新的用。
                self.cache[key] = (now, version, value)
                self.cache.move_to_end(key)
                while len(self.cache) > 192:
                    self.cache.popitem(last=False)
        return value

    def cached_lru(self, key, fn, *, maxsize: int = 192, ttl: float = CACHE_TTL):
        """带 TTL 的 LRU 读缓存，给键空间不封闭的场景用（`/api/related` 按资产取键）。

        与聚合缓存分开计量，容量和 TTL 可由调用方指定。计算在锁外进行，
        计算期间发生提交或显式失效时，结果不进入缓存。
        """
        now = time.monotonic()
        with self.cache_lock:
            hit = self.keyed_cache.get(key)
            if hit and now - hit[0] < ttl:
                self.keyed_cache.move_to_end(key)
                return hit[1]
            generation = self.cache_generation
        value = fn()
        with self.cache_lock:
            if generation == self.cache_generation:
                self.keyed_cache[key] = (now, value)
                self.keyed_cache.move_to_end(key)
                while len(self.keyed_cache) > maxsize:
                    self.keyed_cache.popitem(last=False)
        return value

    def ledger_revision(self, tables=()) -> int | None:
        """账本版本号：`ledger_revision` 各行之和（migration 0038）。

        每张账本表的增删改都由触发器给自己那一行加一，不论写的是服务、CLI 脚本还是
        账本同步，所以这个数变了就是账本变了。任务中心的心跳不挂触发器。库还没迁移到
        0038（或根本没有库）时返回 None。
        `tables` 非空时只累计这些表的版本。
        """
        try:
            with self.read_connection() as connection:
                clause = " WHERE tbl IN (" + ','.join('?' for _ in tables) + ")" if tables else ''
                row = connection.execute("SELECT total(n) FROM ledger_revision" + clause, tables).fetchone()
        except sqlite3.Error:
            return None
        return int(row[0])

    def cached_until_changed(self, key: str, fn, *inputs, tables=()):
        """只读账本聚合的缓存：账本版本号与 `inputs` 都没变就一直命中，不按时间过期。

        `inputs` 是聚合读到的账本之外的东西的版本（候选目录、头像目录、浏览历史库），
        由调用方给出：漏一样，那一样变了页面就一直是旧的。版本号在计算前读，计算期间
        有进程外的写，结果记在旧版本下，下一次请求读到新版本照样重算。库还没有版本号时
        退回 `CACHE_TTL`。
        `tables` 声明聚合依赖的表；这类缓存经过提交通知后仍按声明的版本核验，
        同键的并发请求共用一次计算。
        """
        if not tables:
            revision = self.ledger_revision()
            return self.cached(key, fn, ttl=CACHE_TTL if revision is None else math.inf,
                               version=(revision, *inputs))
        # 指定依赖表的聚合共用一次计算；提交后仍按这些表的代次核验。
        with self.cache_lock:
            lock = self.aggregate_locks.setdefault(key, threading.RLock())
            self.revision_cache_keys.add(key)
        with lock:
            revision = self.ledger_revision(tables)
            return self.cached(key, fn, ttl=CACHE_TTL if revision is None else math.inf,
                               version=(tuple(tables), revision, *inputs))

    def stop_background_jobs(self) -> None:
        """服务关停时丢掉后台任务状态并等线程收工。

        谁在跑归契约自己知道，`api` 的 lifespan 不该再列一遍任务清单。
        """
        self.resource_scan.stop()
        self.follow_job.stop()
        self.scraping_cover_job.stop()
        self.library_processing_job.stop()
        self.thumbnail_job.stop()
        self.follow_resolve_job.stop()
        self.taste_refresh_job.stop()
        self.link_prune_job.stop()
        self.resource_apply_job.stop()
        self.link_check.stop()
        self.feed_job.stop()
        self.want_job.stop()
        self.organize_job.stop()
        self.media_repair_job.stop()
        self.amane_bridge_job.stop()
        if hasattr(self, "jav_discovery_metadata"):
            self.jav_discovery_metadata.stop()
        self.followups.stop()

    def cache_bust(self):
        with self.cache_lock:
            self.cache = OrderedDict((key, value) for key, value in self.cache.items()
                                     if key in self.revision_cache_keys)
            self.keyed_cache.clear()
            # 在途计算靠这个数认出「我出发之后缓存失效过」，从而放弃写回。
            self.cache_generation += 1

    def read_connection(self):
        return self.database.read_connection()

    def write_transaction(self):
        return self.database.write_transaction()

    def has_snapshot(self, raw_path: str | None) -> bool:
        return self.snapshot_file(raw_path) is not None

    def snapshot_file(self, raw_path: str | None) -> Path | None:
        """账本里那条接触印相路径在本机的文件；不在盘上是 None。"""
        if not raw_path:
            return None
        path = normalized_path(raw_path) if self.snapshot_root is not None else Path(raw_path)
        return path if path.is_file() else None

    def cover_path(self, code: str | None) -> Path | None:
        """封面按归一番号存一份，多个文件共用同一张；没有就返回 None。"""
        key = normalise_code_key(code)
        if not key:
            return None
        path = self.cover_root / f"{key}.jpg"
        from .cover_artwork import has_javdb_source
        return path if path.is_file() and not has_javdb_source(path) else None

    def cover_index(self) -> dict[str, dict]:
        """封面目录扫一遍的索引：casefold(归一番号) → 两份取景提示加封面版本。

        值的形状是 `{"frame": 人脸中心或 None, "poster": 正封框或 None, "version": 修改时间}`。
        两份边车各描述一件事，一次目录扫描一起收齐：`.face.json` 说脸在哪，
        `.poster.json` 说正封那一块在哪。版本见 `cover_version`。

        卡片列表逐行问「有封面吗」「取景是多少」，一页 60 行就是 120+ 次 stat 加
        读文件；封面目录一次 scandir 就覆盖全部番号，结果走 `cached()` 的 TTL。
        `/cover` 端点仍走 `cover_path()` 直读，取图不受索引影响。

        键里带目录的修改时间：新落盘或换名替换的封面立刻进索引，按版本缓存的聚合
        （复核）重算时拿到的也是这一份。原地覆写的边车最多一个 TTL 后生效。
        """
        return self.cached("cover-index", self._scan_cover_root,
                           version=path_version(self.cover_root))

    def _scan_cover_root(self) -> dict[str, dict]:
        """一次目录扫描同时收集封面存在性和两份 sidecar。目录不存在就是空索引。"""
        scanned: dict[str, dict] = {}
        try:
            with os.scandir(self.cover_root) as entries:
                for entry in entries:
                    if not entry.name.endswith(".jpg"):
                        continue
                    from .cover_artwork import has_javdb_source
                    if has_javdb_source(Path(entry.path)):
                        continue
                    stem = entry.path[:-len(".jpg")]
                    scanned[entry.name[:-len(".jpg")].casefold()] = {
                        "frame": self._cover_focus(stem + ".face.json"),
                        "poster": self._poster_box(
                            stem + jav_poster_crop.SIDECAR_SUFFIX),
                        "version": f"{entry.stat().st_mtime_ns:x}",
                    }
        except OSError:
            return {}
        return scanned

    @staticmethod
    def _cover_focus(sidecar: str) -> dict | None:
        """sidecar 里的人脸中心与整幅画面标记。缺少坐标时页面退回固定取景。

        两个轴都给出去：哪个轴生效由容器和图片的宽高比决定，不由这里判断。
        缺哪个键就不带哪个键，前端各自回落，不要拿另一个轴的值顶替。
        """
        try:
            with open(sidecar, encoding="utf-8") as handle:
                record = json.load(handle) or {}
                if not isinstance(record, dict):
                    return None
                face = record.get("face")
        except (OSError, ValueError):
            return None
        focus = {'layout': 'still'} if record.get('layout') == 'still' else {}
        if isinstance(face, dict):
            focus.update({axis: face[axis] for axis in ("cx", "cy") if axis in face})
        return focus or None

    @staticmethod
    def _poster_box(sidecar: str) -> dict | None:
        """sidecar 里那个正封框。算法版本落后、判定为不裁、读不出都是 None。

        校验和形状判据都在 `jav_poster_crop.projection` 一处，这里只负责把文件读
        进来：页面拿到的框必须和算它的那份代码是同一套判据。
        """
        try:
            with open(sidecar, encoding="utf-8") as handle:
                return jav_poster_crop.projection(json.load(handle))
        except (OSError, ValueError):
            return None

    def has_cover(self, code: str | None) -> bool:
        key = normalise_code_key(code)
        # 索引的键是 casefold 过的：`is_file()` 在 Windows 与 macOS 的默认文件系统上
        # 大小写不敏感，改走索引不能顺手把这层容错丢了。
        return bool(key) and key.casefold() in self.cover_index()

    def cover_version(self, code: str | None) -> str:
        """这个番号封面的版本，页面拼进 `/cover` 的 `v=`；没有封面是空串。

        补高清、重探都把封面原地换成新图而地址不变，同一页里浏览器直接复用内存里那张
        旧图。判据与 `has_cover` 是同一份索引。
        """
        return (self._cover_entry(code) or {}).get("version", "")

    def logo_index(self) -> Mapping[str, str]:
        """厂牌标识目录扫一遍的索引：已装标识的 casefold(落盘名) → 版本。

        页面据此决定「输出 `<img>` 还是直接首字母垫底」。没有这份索引就只能每个厂牌
        都先发一次 `/logo`、靠 404 把图换掉：首页顶栏一次渲染 30 个厂牌里 21 个是
        404，而 404 那条响应不可缓存，每次重绘再打一遍。

        判据必须和 `PreviewService.logo` 逐字一致——同一个 `logo_key`、同样大小写不
        敏感、同样把 `.icon` / `.logo` 变体算作这个厂牌有图。松一格就是页面说有图却
        取回 404（碎图），紧一格就是明明装了却永远只显示首字母。

        键里和 `cover_index()` 一样带目录的修改时间，刚装上的标识立刻进索引。
        """
        return self.cached("logo-index", self._scan_logo_root,
                           version=path_version(self.logo_root))

    def _scan_logo_root(self) -> Mapping[str, str]:
        """一次目录扫描收齐已装标识，并入随仓库分发的那批（ADR-0026）。

        内置那批不进这份索引的话，`/logo` 取得回图而页面判「没图」，厂牌位永远停在
        首字母——两边判据不一致的后果不是报错，是安静地少显示一批图。

        本机目录不存在时仍然保留内置那批：干净数据目录正是内置资源要顶上的场景。

        版本是这个厂牌几份变体里最新的修改时间：复核批准是原地替换，地址不带版本时同一页
        里看到的还是旧标识。内置那批随版本分发、不在本机替换，版本是空串。
        """
        keys: dict[str, str] = dict.fromkeys(
            brand_marks.installed_stems(LOGO_VARIANTS, self.marks_root), "")
        try:
            with os.scandir(self.logo_root) as entries:
                for entry in entries:
                    name = entry.name.casefold()
                    # `.ct` 边车和 SVG 原件不是 `/logo` 会取的文件，不算这个厂牌有图。
                    if not name.endswith(".img") or not entry.is_file():
                        continue
                    stem = name[:-len(".img")]
                    for variant in LOGO_VARIANTS:
                        if stem.endswith(f".{variant}"):
                            stem = stem[:-len(variant) - 1]
                            break
                    if stem:
                        mtime = entry.stat().st_mtime_ns
                        if mtime > int(keys.get(stem) or "0", 16):
                            keys[stem] = f"{mtime:x}"
        except OSError:
            return keys
        return keys

    def has_logo(self, studio: str | None) -> bool:
        """这个厂牌是否已装标识。空名字一律为假——`/logo` 也拒绝空 studio。"""
        key = logo_key(studio or "")
        return bool(key) and key.casefold() in self.logo_index()

    def logo_version(self, studio: str | None) -> str:
        """这个厂牌标识的版本，页面拼进 `/logo` 的 `v=`；没装或是内置的那批是空串。"""
        key = logo_key(studio or "")
        return self.logo_index().get(key.casefold(), "") if key else ""

    def avatar_root_index(self) -> AvatarRootIndex:
        """头像目录扫一遍的索引：已装的实体图，加已经裁好的头像。

        页面据此决定「输出 `<img>` 还是直接首字母垫底」。没有这份索引就只能无条件出图、
        靠 404 把图摘掉：一个作品详情页是 9 个这样的 404（1 个厂牌实体图、4 个人物
        实体图、4 个头像），而 `/entity-image` 和 `/avatar` 的 404 都不带缓存头，
        每次重绘再打一整轮。

        实体图和头像同处 `avatar_root`（`{kind}-{id}.img` 与 `{asset_id}.jpg`），
        所以一次 `os.scandir` 一起收；分成两个缓存键就是同一个目录连扫两遍。

        键里和 `cover_index()` 一样带目录的修改时间，刚装上的实体图立刻进索引，
        口味与复核按头像目录版本重算时拿到的也是这一份。
        """
        return self.cached("avatar-root-index", self._scan_avatar_root,
                           version=path_version(self.avatar_root))

    def _scan_avatar_root(self) -> AvatarRootIndex:
        """一次目录扫描同时收齐实体图和已裁头像。目录不存在就是两个空集合。

        实体图顺手记下修改时间：换头像是原地覆盖同一个文件，地址不带版本时，同一页里
        浏览器直接复用内存里那张旧图，不刷新就看不到新的。Windows 上 `entry.stat()`
        取自目录枚举本身，不多一次系统调用。
        """
        entity_images: dict[str, str] = {}
        generated: set[int] = set()
        try:
            with os.scandir(self.avatar_root) as entries:
                for entry in entries:
                    name = entry.name.casefold()
                    if name.endswith(".img"):
                        # `.ct`、`.provenance.json`、`.face.json` 都是边车，不是
                        # `/entity-image` 会取的文件，不算这个实体有图。
                        if entry.is_file():
                            entity_images[name[:-len(".img")]] = f"{entry.stat().st_mtime_ns:x}"
                    elif name.endswith(".jpg"):
                        # 头像按资产 id 落盘。生成中途的 `<id>.<格>.tmp.jpg` 不算数，
                        # 它随时会被删掉或改名。
                        stem = name[:-len(".jpg")]
                        if stem.isdigit() and entry.is_file():
                            generated.add(int(stem))
        except OSError:
            return AvatarRootIndex({}, frozenset())
        return AvatarRootIndex(entity_images, frozenset(generated))

    def entity_image_version(self, kind: str, entity_id) -> str:
        """这个实体那张图的版本，页面拼进 `/entity-image` 的 `v=`；没有图是空串。

        判据与 `has_entity_image` 是同一份索引，两者不会一个说有、一个说没有。
        """
        if not self.has_entity_image(kind, entity_id):
            return ""
        # 两次取索引之间缓存可能刚失效过，第二次那份里未必还有这个键。
        return self.avatar_root_index().entity_images.get(
            entity_image_key(kind, entity_id).casefold(), "")

    def has_entity_image(self, kind: str, entity_id) -> bool:
        """`/entity-image` 能不能取到这个实体的图。

        判据必须和 `PreviewService.entity_image` 逐字一致——同一份 kind 白名单、同一个
        `entity_image_key`。松一格就是页面说有图却取回 404（碎图），紧一格就是明明
        装了却永远只显示首字母。

        索引的键 casefold 过：`is_file()` 在 Windows 与 macOS 的默认文件系统上大小写
        不敏感，改走索引不能顺手把这层容错丢了。落盘名本来就全小写，这层只兜手工
        摆进去的文件。
        """
        if kind not in ENTITY_IMAGE_KINDS or entity_id is None:
            return False
        try:
            key = entity_image_key(kind, entity_id)
        except (TypeError, ValueError):
            return False
        return key.casefold() in self.avatar_root_index().entity_images

    def has_avatar(self, asset_id, snapshot_path: str | None) -> bool:
        """`/avatar` 能不能取到这个代表作的头像。

        和实体图不是一回事，判据也不能照抄：`/avatar` 是**按需生成**的，目录里没有
        那张 jpg 只说明还没人要过，不说明取不到。只要接触印相还在盘上，第一次请求
        就现裁一张出来。把「还没裁过」也判成没有，等于把点一下就有的头像永远关掉——
        真缺和没抓过是两回事。

        所以判据是「已经裁好了」或「印相还在，裁得出来」，和 `has_thumb` 同一个
        `has_snapshot`。剩下的 404 只有生成本身失败那一种（没有 ffmpeg、六格全黑），
        那要真去跑一遍 ffmpeg 才知道，预测不了；那条兜底链照旧留着兜。
        """
        try:
            key = int(asset_id)
        except (TypeError, ValueError):
            return False
        return key in self.avatar_root_index().generated or self.has_snapshot(snapshot_path)

    def cover_frame(self, code: str | None) -> dict | None:
        """封面的取景提示：人脸中心与整幅画面标记。未检出时不含人脸坐标。

        双页封套的横向锚点仍由版式决定（正封贴着右边缘，几何规则已经稳定），人脸
        锚点用于整幅画面被裁掉的那一轴，VR 与数字发行拼图携带 `layout=still` 标记。
        取不到人脸时前端退回固定取景，不影响显示。
        """
        return (self._cover_entry(code) or {}).get("frame")

    def poster_box(self, code: str | None) -> dict | None:
        """封面里正封那一块的取景框，源图像素坐标加源图尺寸。

        没算过、算法版本落后、或这个番号的封面本来就不该裁（`jav_poster_crop`
        的 `none` 一档）都返回 None，竖版位置退回整张封面。
        """
        return (self._cover_entry(code) or {}).get("poster")

    def _cover_entry(self, code: str | None) -> dict | None:
        key = normalise_code_key(code)
        return self.cover_index().get(key.casefold()) if key else None

    def avatar_focus(self, kind: str, entity_id) -> dict | None:
        """实体图的取景提示：人脸中心换算成的 object-position，加上脸框的像素尺寸。

        sidecar 由 `scripts/detect_avatar_faces.py` 离线写入，与封面取景同一
        约定；没算过、读不出或没检出都返回 None，页面维持几何居中。取值与校验在
        `avatar_face.focus_hint`，关注页的题材圆标读的是同一份判据。

        身份 id 和 `has_entity_image` 一样按不可信对待：复核队列和榜单里的行有的
        根本没有实体（扁平标签那一批，`entity_id` 是空串或 None），拿它去算落盘名
        会当场抛出来，整页跟着 500。没有身份就是没有取景。
        """
        if entity_id is None:
            return None
        try:
            key = entity_image_key(kind, entity_id)
        except (TypeError, ValueError):
            return None
        path = self.avatar_root / f"{key}.face.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return avatar_face.focus_hint(data)

    def has_fts(self) -> bool:
        if self._fts_available is None:
            with self.read_connection() as connection:
                self._fts_available = connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='asset_search'"
                ).fetchone() is not None
        return self._fts_available
