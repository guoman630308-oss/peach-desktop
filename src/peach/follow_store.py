"""追更订阅与候选条目的 ledger 边界。

这一层只负责登记与候选：`follow_item` 的 `status` 停在 `new`/`seen` 时，不影响任何
asset、标签或反馈。只有 `save_asset()` 会写出真相，而它要求显式 `confirm=True`，
并且只 INSERT 一条 `location='online'` 的新 asset，绝不改写既有真相字段。

原始响应按来源存进 `peach-data/sources/follow/`，一次写死；条件请求游标存在
`follow_source` 行上，因为它可替换、且要参与界面查询。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import follow_providers
from .follow import FollowSourceError, write_immutable
from .follow_image_dims import positive_dims
from .follow_sources import (
    Rule34VideoConnector, SourceFetch, canonical_source_ref,
    official_profile_handle, origin_group_key, profile_link_identity,
)
from .follow_variants import classify, group_duplicates, video_spec_markers


def _now_text(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise FollowSourceError("追更时间戳必须带时区")
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


#: 标签里跟在中点后面的服务名。`LazyProcrastinator · fanbox` 和 rule34video 上的
#: `lazyprocrastinator` 是同一个人，中点后面那截只说明他在哪个平台连载。
_LABEL_SERVICE_RE = re.compile(r"\s*[·|]\s*[A-Za-z0-9_\-]+\s*$")
_AUTHOR_NOISE_RE = re.compile(r"[^0-9a-z一-鿿]+")
_F95_TITLE_SUFFIX_RE = re.compile(r"\s+collections?\s*$", re.IGNORECASE)

#: F95 线程标题里的每一个方括号段。站点的标题约定是
#: `作品名 [版本或日期] [作者]`，末尾那一个是作者位。
_F95_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")
#: 方括号里长得像版本号或日期的那些：`v1.2`、`0.9.5b`、`2026-09-04`、`Ch.5`。
_F95_VERSION_RE = re.compile(r"^(?:v|ch|ep|part|episode)?\.?\s*\d[\w.\-]*$", re.IGNORECASE)
#: 方括号里的完成状态、引擎、语言和体裁标记。这些占着作者位时那一段不是人名。
#: 词表只收 F95 的固定标签，不收泛词——收多了会把 `[3D Artist]` 这类真名误判掉。
_F95_LABEL_WORDS = frozenset({
    "abandoned", "collection", "collection request", "completed", "complete",
    "eng", "english", "final", "flash", "html", "java", "onhold", "on hold",
    "ongoing", "others", "qsp", "rags", "renpy", "ren'py", "request", "rpgm",
    "unity", "unreal engine", "uncen", "uncensored", "censored", "vn", "wt",
})
#: 作者位里并列几个手柄时的分隔符：`[LazyProcrastinator/LazyProcrast]`。
_F95_HANDLE_SPLIT_RE = re.compile(r"\s*[/|]\s*")
#: 主体末尾那截说明容器而不是作者的措辞：`Memz3D Models Collection` 的作者是
#: `Memz3D`，`Models Collection` 只说明这个线程装的是什么。
_F95_CONTAINER_SUFFIX_RE = re.compile(
    r"\s*[-–—]?\s*(?:complete\s+)?"
    r"(?:models?|arts?|assets?|packs?|mega|renders?|animations?)?"
    r"\s*collections?\s*$", re.IGNORECASE)


def f95_author_handles(title: str) -> tuple[str, ...]:
    """F95 线程标题的作者位上列出的全部手柄，按标题里的顺序。

    站点的标题约定把作者放在末尾的方括号里：真实数据
    `Strauzek Collection [2026-09-04] [Mr_Strauz]` 的作者是 `Mr_Strauz`，中间那个
    方括号是更新日期。所以从右往左找第一个不像版本号、日期和站点标签的方括号段。

    一个人常在那里写上两个手柄——`[LazyProcrastinator/LazyProcrast]`——两个都要，
    第一个当显示名，其余是同一个人在别处的写法，够格做别名候选。
    """
    text = str(title or "").strip()
    for segment in reversed(_F95_BRACKET_RE.findall(text)):
        candidate = segment.strip()
        if not candidate or _F95_VERSION_RE.match(candidate):
            continue
        if candidate.casefold() in _F95_LABEL_WORDS:
            continue
        handles = tuple(part for part in _F95_HANDLE_SPLIT_RE.split(candidate) if part)
        return handles or (candidate,)
    return ()


def f95_author_name(title: str) -> str:
    """从一个 F95 线程标题里取作者名，取不到时回空串。

    作者位在就用它的第一个手柄；没有可用的方括号时才退回主体：剥掉全部方括号再
    剥掉容器措辞，`[Collection Request] Suzutaru 3D - Complete Collection` 得到
    `Suzutaru 3D`。两条路都取不到东西时回空串，由调用方决定拿原标题顶上——
    宁可显示整个标题，也不要把一个猜出来的名字当成作者。
    """
    handles = f95_author_handles(title)
    if handles:
        return handles[0]
    text = str(title or "").strip()
    stem = re.sub(r"\s+", " ", _F95_BRACKET_RE.sub(" ", text)).strip(" -–—·|")
    return _F95_CONTAINER_SUFFIX_RE.sub("", stem).strip(" -–—·|")


#: 作者显示名与头像可信的官方渠道；归档站只作回退。
_OFFICIAL_IDENTITY_PROVIDERS = follow_providers.official_identity_providers()

#: 每个条目都是一次独立发布的来源。论坛线程的标题只是容器名，同一线程里每个带
#: 资源的楼层各自成组；这条口径登记在 `follow_providers`，不在这里点名站点。
_RELEASE_KEY_PER_POST = follow_providers.release_key_per_post()
_SEQUENTIAL_UPLOADS = follow_providers.sequential_upload_providers()

#: 完整候选（列表 + 详情都取到了）更新已有行时用的 SET 子句。
_FULL_UPDATE = (
    "  title=excluded.title, url=excluded.url, media_url=excluded.media_url,"
    # 详情页取不到不等于这条没有发布时间。paheal 每个候选都要单独打一次
    # 详情页，被限流时 `_detail` 返回 {}，这里若直接覆盖，就会把上一轮
    # 已经取到的上传时间和时长抹成 NULL——实测 168 条里 7 条正是这样，
    # 而 `COALESCE(published_at, first_seen_at)` 会让界面改显示抓取时刻。
    "  thumb_url=excluded.thumb_url,"
    "  published_at=COALESCE(excluded.published_at, follow_item.published_at),"
    "  published_precision=CASE WHEN excluded.published_at IS NOT NULL"
    "    THEN excluded.published_precision ELSE follow_item.published_precision END,"
    "  version=excluded.version,"
    "  duration=COALESCE(excluded.duration, follow_item.duration),"
    "  release_key=excluded.release_key, variant_kind=excluded.variant_kind,"
    "  variant_label=excluded.variant_label, group_hint=excluded.group_hint,"
    # 整块替换；上一轮学到的图片宽高由 `_carry_image_dims` 在写入前并进新值。
    "  metadata_json=excluded.metadata_json, last_seen_at=excluded.last_seen_at"
)


def _learned_media_dims(media_items: object) -> dict[str, tuple[int, int]]:
    """清单里已有尺寸的图，按媒体稳定键（`id` 优先、`url` 兜底）索引。"""
    learned: dict[str, tuple[int, int]] = {}
    for media in media_items if isinstance(media_items, list) else ():
        if not isinstance(media, dict):
            continue
        key = str(media.get("id") or media.get("url") or "")
        dims = positive_dims(media.get("width"), media.get("height"))
        if key and dims:
            learned[key] = dims
    return learned


def _carry_image_dims(metadata: dict, previous_json: str | None) -> dict:
    """整行重写时把上一轮学到的图片宽高带进新 metadata，新值自己带尺寸的以新值为准。

    宽高多半不是连接器给的：归档站不报尺寸，是回填脚本问过文件头、或界面加载完
    图片后回写的。整块替换 metadata 会把它们抹掉，下一轮检查更新后卡片又退回无尺寸
    占位。条目级和清单里每张图同一条规则；清单里的图按媒体稳定键对回，作者增删
    图片也不会把尺寸安到别的图上。
    """
    try:
        previous = json.loads(previous_json or "{}")
    except ValueError:
        return metadata
    if not isinstance(previous, dict):
        return metadata
    if positive_dims(metadata.get("width"), metadata.get("height")) is None:
        dims = positive_dims(previous.get("width"), previous.get("height"))
        if dims:
            metadata["width"], metadata["height"] = dims
    learned = _learned_media_dims(previous.get("media_items"))
    # 连接器给的清单可能是 tuple（f95 把附件与 gofile 两段拼起来），写进 JSON 后才是数组。
    if learned and isinstance(metadata.get("media_items"), (list, tuple)):
        carried = []
        for media in metadata["media_items"]:
            dims = learned.get(str(media.get("id") or media.get("url") or "")) \
                if isinstance(media, dict) else None
            if dims and positive_dims(media.get("width"), media.get("height")) is None:
                media = {**media, "width": dims[0], "height": dims[1]}
            carried.append(media)
        metadata["media_items"] = carried
    return metadata

#: `partial=True` 的候选（只有列表视图、详情这次没取）更新已有行时用的 SET 子句。
#: 列表本来就权威的那几列照常更新；详情才能给出的 media_url、thumb_url、
#: published_at、group_hint 一概不动，metadata 用 `json_patch` 并进去而
#: 不是整块替换——否则跳过第二阶段就等于把上一轮取到的细节抹掉，而抹掉之后
#: 「详情本来就没有」和「这次没去问」在数据里长得一模一样。
#: 时长只补空：rule34xxx 的时长读自原文件头，帖子页被限流挡回来时它照样取得到，
#: 这时候选仍是 partial，已有的时长不覆盖，空着的补上。
_PARTIAL_UPDATE = (
    "  title=excluded.title, url=excluded.url, version=excluded.version,"
    "  release_key=excluded.release_key, variant_kind=excluded.variant_kind,"
    "  variant_label=excluded.variant_label,"
    "  duration=COALESCE(follow_item.duration, excluded.duration),"
    "  metadata_json=json_patch(follow_item.metadata_json, excluded.metadata_json),"
    "  last_seen_at=excluded.last_seen_at"
)

#: 「详情页已经问过这一行」在 ledger 上怎么看出来。键就是连接器声明的
#: `ENRICHED_MARK`，值是判据。写死成一张表而不是拼 SQL：判据是封闭词表，
#: 由 `tests/test_follow_sources.py` 反过来核对每个连接器声明的键都在这里。
_ENRICHED_PREDICATES = {
    "published_at": "published_at IS NOT NULL AND published_at<>''",
    # rule34video：标签、分类与署名只有详情页给，有 `tag_types` 就是详情页取到过。
    "tag_types": "json_extract(metadata_json,'$.tag_types') IS NOT NULL",
    # kemono 系：详情页只用来判「这一帖交付了资源没有」，没有要补的列。判过保留的帖子
    # 才会落库，所以行在 ledger 里本身就是判定结果，库里有的帖子不必再探。
    "kept": "1",
    # rule34xxx：分类只有帖子页给，时长只有原文件头给，两样都有才算补齐。只看分类的话，
    # 时长功能上线前补过分类的行、文件头那次没读到的行，常规检查永远不会再问。
    "tag_types_duration": (
        "json_extract(metadata_json,'$.tag_types') IS NOT NULL"
        " AND (duration IS NOT NULL OR (lower(COALESCE(media_url,'')) NOT LIKE '%.mp4'"
        " AND lower(COALESCE(media_url,'')) NOT LIKE '%.mp4?%'))"),
    "post_type": "json_extract(metadata_json,'$.post_type') IS NOT NULL",
    "media_dims": "json_extract(metadata_json,'$.media_dims') IS NOT NULL",
}


def normalized_author_name(value: str, *, provider: str = "") -> str:
    """作者别名表的身份键。

    去掉「· 服务名」后缀，再去掉大小写、空格、连字符这些不影响身份的噪声。
    归一化只做到这一步，不做模糊匹配：把两个碰巧相似的名字并成一个人，比让用户
    自己看到两行严重得多。

    这是**别名表的主键定义**，不是展示逻辑，所以它和别名读写放在同一层。
    """
    stripped = _LABEL_SERVICE_RE.sub("", str(value or "").strip())
    if provider == "f95zone":
        # F95 的线程标题说的是一个容器而不是另一个作者：真实数据是
        # `Lazy Procrastinator Collection`，而每一条作者来源都是
        # `LazyProcrastinator`，留着这个通用后缀会凭空多出一个分组。
        stripped = f95_author_name(stripped) or stripped
    return _AUTHOR_NOISE_RE.sub("", stripped.casefold())


def author_display_text(value: str, *, provider: str = "") -> str:
    """去掉不属于作者名的容器与服务名措辞，保留原始拼写。

    F95 的线程标题另有自己的约定（作者在末尾方括号里），所以那一家单独走
    `f95_author_name`；别的来源的标签就是作者写法本身，只剥掉容器措辞。
    """
    stripped = _LABEL_SERVICE_RE.sub("", str(value or "").strip())
    if provider == "f95zone":
        picked = f95_author_name(stripped)
        if picked:
            return picked
    return _F95_TITLE_SUFFIX_RE.sub("", stripped).strip()


@dataclass(frozen=True)
class FollowItemRow:
    id: int
    source_id: int
    provider: str
    ref: str
    source_label: str
    entity_id: int | None
    external_id: str
    title: str
    url: str | None
    media_url: str | None
    thumb_url: str | None
    published_at: str | None
    published_precision: str
    version: str | None
    duration: float | None
    semantics: str
    release_key: str
    variant_kind: str
    variant_label: str | None
    group_hint: str | None
    status: str
    asset_id: int | None
    first_seen_at: str
    last_seen_at: str
    #: 用户按图隐藏的媒体稳定键（`id` 优先、`url` 兜底）。抓取流程只读不写。
    hidden_media: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ReleaseGroup:
    """一个作品在所有来源上的全部形态。"""

    release_key: str
    primary: FollowItemRow
    variants: tuple[FollowItemRow, ...]
    duplicates: tuple[FollowItemRow, ...]

    @property
    def providers(self) -> tuple[str, ...]:
        members = (self.primary, *self.variants, *self.duplicates)
        return tuple(dict.fromkeys(item.provider for item in members))

    @property
    def is_release(self) -> bool:
        """这一组是同一作品的历次动态（f95 线程），不是同一作品的多个版本。"""
        return self.primary.semantics == "release"

    @property
    def has_wip(self) -> bool:
        return any(item.variant_kind == "wip" for item in self.variants)

    @property
    def newest_at(self) -> str:
        members = (self.primary, *self.variants, *self.duplicates)
        return max((item.published_at or item.first_seen_at) for item in members)


@dataclass(frozen=True)
class RecordOutcome:
    source_id: int
    discovered: int = 0
    added: int = 0
    updated: int = 0
    not_modified: bool = False
    evidence_path: str | None = None
    #: 证据没存下来的原因。发现本身仍然成立，所以这不是失败，但必须说出来。
    evidence_error: str | None = None


class FollowStore:
    def __init__(self, connect, *, sources_root: Path | None = None):
        """`connect` 是返回 sqlite3 连接的可调用对象；由调用方决定读写事务边界。"""
        self._connect = connect
        self.sources_root = Path(sources_root) / "follow" if sources_root else None

    # ---- 订阅登记 -------------------------------------------------------

    def register(self, *, provider: str, ref: str, label: str, url: str,
                 semantics: str = "work", entity_id: int | None = None,
                 metadata: dict | None = None, moment: datetime | None = None) -> int:
        if semantics not in ("work", "release"):
            raise FollowSourceError("semantics 只能是 work 或 release")
        ref = canonical_source_ref(provider, ref)
        stamp = _now_text(moment)
        payload = json.dumps(metadata or {}, ensure_ascii=False)
        connection = self._connect()
        connection.execute(
            "INSERT INTO follow_source"
            "(entity_id,provider,ref,label,url,semantics,metadata_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(provider,ref) DO UPDATE SET"
            "  label=excluded.label, url=excluded.url, semantics=excluded.semantics,"
            "  entity_id=COALESCE(excluded.entity_id, follow_source.entity_id),"
            "  metadata_json=CASE WHEN excluded.metadata_json='{}'"
            "    THEN follow_source.metadata_json ELSE excluded.metadata_json END,"
            "  updated_at=excluded.updated_at",
            (entity_id, provider, ref, label, url, semantics, payload, stamp, stamp),
        )
        row = connection.execute(
            "SELECT id FROM follow_source WHERE provider=? AND ref=?", (provider, ref)
        ).fetchone()
        return int(row[0])

    def merge_source_metadata(self, source_id: int, patch: dict,
                              moment: datetime | None = None) -> None:
        """把几个键并进一条来源的 metadata，其余键原样留着。

        `register` 的 metadata 是整块替换，用它补一个后来才会解析的字段会把登记时
        写下的 `author_key` 一起抹掉。这里走 `json_patch`，所以补齐旧来源和重跑
        都是安全的。
        """
        if not patch:
            return
        self._connect().execute(
            "UPDATE follow_source SET metadata_json=json_patch(metadata_json,?),"
            " updated_at=? WHERE id=?",
            (json.dumps(patch, ensure_ascii=False), _now_text(moment), source_id),
        )

    def set_enabled(self, source_id: int, enabled: bool,
                    moment: datetime | None = None) -> None:
        self._connect().execute(
            "UPDATE follow_source SET enabled=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, _now_text(moment), source_id),
        )

    def sources(self, *, enabled_only: bool = False) -> tuple[sqlite3.Row, ...]:
        clause = " WHERE enabled=1" if enabled_only else ""
        return tuple(self._connect().execute(
            "SELECT s.*, e.canonical_name AS entity_name FROM follow_source s"
            " LEFT JOIN entity e ON e.id=s.entity_id" + clause +
            " ORDER BY s.provider, s.ref"
        ).fetchall())

    def creator_aliases(self, entity_id: int | None) -> tuple[str, ...]:
        """规范名 + 全部别名。变体判定靠它剥掉标题里的创作者手柄。"""
        if entity_id is None:
            return ()
        connection = self._connect()
        names = [row[0] for row in connection.execute(
            "SELECT canonical_name FROM entity WHERE id=?", (entity_id,))]
        names += [row[0] for row in connection.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=?", (entity_id,))]
        return tuple(dict.fromkeys(name for name in names if name))

    def source_needs_media_reparse(self, source_id: int) -> bool:
        """Whether saved credentials could unlock stale media metadata.

        This is deliberately derived from candidate metadata rather than a new
        truth field.  A successful unconditional refresh replaces the metadata
        and naturally clears the condition.

        判定交给 SQLite 的 `json_extract`，不把这个来源**每一条**候选的
        `metadata_json` 整串取回 Python 再逐条 `json.loads`：这里只想知道
        「有没有任意一条」。回填过的来源单源就有几百到上千行，为一个布尔值把它们
        全解一遍。

        `json_valid` 的外壳不能省：`json_extract` 遇到非法 JSON 是**报错**，不是
        返回 NULL，那会把 `except json.JSONDecodeError: continue` 那一级的容忍变成
        整个检查崩掉。写成 `CASE` 而不是并列的 `AND`，因为 SQL 的 `AND` 不保证
        求值顺序。
        """
        row = self._connect().execute(
            "SELECT 1 FROM follow_item WHERE source_id=? AND CASE"
            "   WHEN json_valid(metadata_json)"
            "   THEN json_extract(metadata_json,'$.media_needs_credential')"
            "   END = 1 LIMIT 1",
            (source_id,),
        ).fetchone()
        return row is not None

    # ---- 抓取结果落地 ---------------------------------------------------

    def record(self, source_id: int, fetch: SourceFetch, *,
               creator_aliases: tuple[str, ...] = (),
               moment: datetime | None = None, page: int = 0,
               complete: bool = True) -> RecordOutcome:
        stamp = _now_text(moment)
        connection = self._connect()
        if fetch.not_modified:
            connection.execute(
                "UPDATE follow_source SET last_checked_at=?, last_status='not_modified',"
                " last_error=NULL, updated_at=? WHERE id=?", (stamp, stamp, source_id))
            return RecordOutcome(source_id, not_modified=True)

        evidence, evidence_error = self._persist_evidence(fetch, moment) if complete else (None, None)
        if complete:
            self._replace_candidate_profile_links(source_id, fetch, moment=moment, page=page)
        added = updated = 0
        for candidate in fetch.candidates:
            verdict = classify(candidate.title, creator_aliases=creator_aliases,
                               version=candidate.version, semantics=fetch.semantics)
            release_key = verdict.release_key
            if fetch.provider in _RELEASE_KEY_PER_POST:
                # 线程标题只是容器名；每个带资源的楼层都是一次独立发布。
                # 同一楼层若展开出多个视频，由 media_items 在详情里组成 Mix。
                release_key = f"{release_key}\u0000{candidate.external_id}"
            elif not candidate.title_is_name:
                # booru 的「标题」是标签拼出来的，不是名字。让它各自成组，只靠
                # `group_hint` 合并；否则同一作者标签相似的两个作品会被并掉。
                release_key = f"{release_key}\u0000{candidate.external_id}"
            precision = str(candidate.extra.get("published_precision") or
                            ("exact" if candidate.published_at else "unknown"))
            if precision not in ("exact", "approximate", "unknown"):
                precision = "unknown"
            # author 与 summary 单独并进来：连接器把它们放在 DTO 的具名字段上而不是
            # extra 里，但界面要显示「谁发的、说了什么」——f95 线程里九条回复的标题
            # 全是线程名，摘要才是那条动态的内容。摘要截断，追更不做全文存档。
            metadata = {"markers": list(verdict.markers),
                        **({"author": candidate.author} if candidate.author else {}),
                        **({"summary": candidate.summary[:400]} if candidate.summary else {}),
                        **dict(candidate.extra)}
            previous = connection.execute(
                "SELECT metadata_json FROM follow_item WHERE source_id=? AND external_id=?",
                (source_id, candidate.external_id)).fetchone()
            existed = previous is not None
            if existed and not candidate.partial:
                metadata = _carry_image_dims(metadata, previous[0])
            values = (
                source_id, candidate.external_id, candidate.title, candidate.url,
                candidate.media_url, candidate.thumb_url, candidate.published_at,
                precision, verdict.version, candidate.duration, release_key,
                verdict.variant_kind, verdict.variant_label, candidate.group_hint,
                evidence,
                json.dumps(metadata, ensure_ascii=False),
                stamp, stamp,
            )
            connection.execute(
                "INSERT INTO follow_item(source_id,external_id,title,url,media_url,"
                "thumb_url,published_at,published_precision,version,duration,release_key,"
                "variant_kind,variant_label,group_hint,evidence_path,metadata_json,"
                "first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(source_id,external_id) DO UPDATE SET"
                # status、asset_id 与 first_seen_at 刻意不在两个 SET 里：用户处理过的
                # 条目不因为再次抓到就退回 new，首见时间也不该被重写。
                + (_PARTIAL_UPDATE if candidate.partial else _FULL_UPDATE)
                + ", evidence_path=COALESCE(follow_item.evidence_path, excluded.evidence_path)",
                values,
            )
            if existed:
                updated += 1
            else:
                added += 1
        if not complete:
            return RecordOutcome(source_id, len(fetch.candidates), added, updated)
        if page:
            # 往回抓：**不动 etag/last_modified**。那两个值是第一页的条件请求凭据，
            # 拿第 3 页的 etag 覆盖掉，下次常规检查就会拿它去问第一页，
            # 站点回 304，新的更新从此再也进不来。只推进游标。
            connection.execute(
                "UPDATE follow_source SET backfill_page=max(backfill_page,?),"
                " last_checked_at=?, last_status='ok', last_error=NULL, updated_at=?"
                " WHERE id=?", (page, stamp, stamp, source_id))
        else:
            connection.execute(
                "UPDATE follow_source SET etag=?, last_modified=?, last_checked_at=?,"
                " last_status='ok', last_error=NULL, updated_at=? WHERE id=?",
                (fetch.etag, fetch.last_modified, stamp, stamp, source_id))
        return RecordOutcome(source_id, len(fetch.candidates), added, updated,
                             evidence_path=evidence, evidence_error=evidence_error)

    def _replace_candidate_profile_links(
        self,
        source_id: int,
        fetch: SourceFetch,
        *,
        moment: datetime | None = None,
        page: int = 0,
    ) -> None:
        """只把能证明属于当前作者的 booru 出处记进来源名片。

        Rule34 的 ``source`` 属于作品，不属于订阅作者：合作作品会同时链接另一位作者，
        因此“在白名单站点上”仍不足以证明同一人。只有外链手柄与当前来源作者键相同，
        或已经由用户确认的别名表把两者指向同一规范作者时，才接纳为作者身份。

        第一页替换这项，而不是与旧值累加；这样下一次正常检查就会清掉旧版误收的
        合作作者。往回抓的页只并集追加：那一页看见的是更早的作品，把它当成完整结果
        写回去，第一页刚记下的出处就被一次回填抹掉了。这里只更新抓取中的这条来源，
        不扫描或批量改写真实账本。
        """
        if fetch.provider not in {"rule34xxx", "rule34paheal"}:
            return
        row = self._connect().execute(
            "SELECT ref,metadata_json FROM follow_source WHERE id=?", (source_id,)
        ).fetchone()
        if row is None:
            return
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        mapping, _groups = self.author_aliases()
        source_keys = {
            normalized_author_name(str(row["ref"] or "")),
            normalized_author_name(str(metadata.get("author_key") or ""))
            if isinstance(metadata, dict) else "",
        }
        source_roots = {mapping.get(key, key) for key in source_keys if key}
        links: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        if page and isinstance(metadata, dict):
            for entry in metadata.get("official_links") or ():
                if not isinstance(entry, dict):
                    continue
                service = str(entry.get("service") or "")
                handle = str(entry.get("handle") or "")
                if not service or not handle:
                    continue
                key = (service.casefold(), handle.casefold())
                if key in seen:
                    continue
                seen.add(key)
                links.append({"service": service, "handle": handle,
                              "url": str(entry.get("url") or "")})
        for candidate in fetch.candidates:
            source = str(candidate.extra.get("source") or "").strip()
            identity = profile_link_identity(source)
            if identity is None:
                continue
            service, handle = identity
            handle_key = normalized_author_name(handle)
            if not handle_key or mapping.get(handle_key, handle_key) not in source_roots:
                continue
            key = (service.casefold(), handle.casefold())
            if key in seen:
                continue
            seen.add(key)
            links.append({"service": service, "handle": handle, "url": source})
        self.merge_source_metadata(source_id, {"official_links": links}, moment=moment)

    def enriched_external_ids(self, source_id: int, mark: str) -> frozenset[str]:
        """这条来源里细节已经补齐、不必再打详情页的条目 id。

        `mark` 由连接器声明（`ENRICHED_MARK`），判据在 `_ENRICHED_PREDICATES`：
        SQL 属于这一层，「补齐之后哪一处会有值」属于连接器。补齐过的不再问——
        详情页是唯一会让请求数随条目数增长的路径。判据不成立的行下次会再试一次，
        所以上一轮被限流挡掉的细节能补回来，不必等用户强制重取。
        """
        predicate = _ENRICHED_PREDICATES.get(str(mark or ""))
        if predicate is None:
            raise ValueError(f"未登记的补齐判据：{mark!r}")
        rows = self._connect().execute(
            "SELECT external_id FROM follow_item WHERE source_id=?"
            f" AND {predicate}", (source_id,))
        return frozenset(str(row[0]) for row in rows)

    def remove_source(self, source_id: int) -> None:
        """删掉一条来源登记。

        哪张表、要不要连带清理，属于这一层的知识。写成 Web 处理函数里一句裸
        DELETE 的话，换存储结构时得去处理函数里找 SQL。

        条目与播放记录由这里显式删：连接的 `PRAGMA foreign_keys` 是 OFF，表上的
        `ON DELETE CASCADE` 不会执行。留下的孤儿条目让全库 `foreign_key_check` 不为 0，
        补别名后继合并实体时照这个结果回滚，每一次合并都失败。
        """
        connection = self._connect()
        connection.execute(
            "DELETE FROM follow_playback WHERE follow_item_id IN"
            " (SELECT id FROM follow_item WHERE source_id=?)", (source_id,))
        connection.execute("DELETE FROM follow_item WHERE source_id=?", (source_id,))
        connection.execute("DELETE FROM follow_source WHERE id=?", (source_id,))

    def record_error(self, source_id: int, message: str,
                     moment: datetime | None = None, *, status: str = "error") -> None:
        stamp = _now_text(moment)
        self._connect().execute(
            "UPDATE follow_source SET last_checked_at=?, last_status=?, last_error=?,"
            " updated_at=? WHERE id=?", (stamp, status, message[:500], stamp, source_id))

    def record_history_end(self, source_id: int,
                           moment: datetime | None = None) -> None:
        """Record a successful terminal backfill without advancing its cursor."""
        stamp = _now_text(moment)
        self._connect().execute(
            "UPDATE follow_source SET last_checked_at=?, last_status='not_modified',"
            " last_error=NULL, updated_at=? WHERE id=?", (stamp, stamp, source_id))

    def _persist_evidence(self, fetch: SourceFetch,
                          moment: datetime | None) -> tuple[str | None, str | None]:
        """存原始响应。存不下来不算检查失败。

        Mac 上 `peach-data/sources` 是指向外置盘的符号链接，盘不在时它是一条**断链**——
        `mkdir(exist_ok=True)` 在断链上会抛 `FileExistsError`（链接在、目标不在），
        不接住它就会让整个 `record()` 连同已经抓到的候选一起炸掉。发现本身跟归档盘无关，
        所以这里降级：候选照常入库，证据标成未取得，原因往上报。

        这和「脱盘模式」是同一条边界——脱的是盘不是账本，不该让整个功能挂掉。
        """
        if self.sources_root is None or fetch.raw_body is None:
            return None, None
        reference = moment or datetime.now(timezone.utc)
        digest = hashlib.sha256(fetch.raw_body).hexdigest()
        key = hashlib.sha256(f"{fetch.provider}\n{fetch.ref}".encode()).hexdigest()[:20]
        directory = self.sources_root / fetch.provider / key
        stamp = reference.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = directory / f"{stamp}-{digest[:12]}.raw"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            write_immutable(path, fetch.raw_body)
            write_immutable(path.with_suffix(".json"), (json.dumps({
                "provider": fetch.provider, "ref": fetch.ref,
                # 脱敏后的请求 URL：凭据永不进证据目录。
                "request_url": fetch.request_url, "sha256": digest,
                "checked_at": _now_text(reference), "candidates": len(fetch.candidates),
            }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        except OSError as error:
            return None, f"证据未取得（{self.sources_root} 不可写：{error.strerror or error}）"
        return str(path.relative_to(self.sources_root.parent)), None

    # ---- 读取与分组 -----------------------------------------------------

    _SELECT = (
        "SELECT i.*, s.provider, s.ref, s.label AS source_label, s.entity_id,"
        " s.semantics"
        " FROM follow_item i JOIN follow_source s ON s.id=i.source_id"
    )

    def items(self, *, statuses: tuple[str, ...] = (), source_id: int | None = None,
              limit: int = 500, offset: int = 0) -> tuple[FollowItemRow, ...]:
        """按发布时间倒序取一页条目。

        排序里带 `i.id DESC` 兜底不只是为了稳定：分页靠 OFFSET，而 `published_at`
        在同一批抓取里大量并列，只按它排的话两次查询的相对顺序可以不同，翻页就会
        重复或漏掉条目。
        """
        clauses, params = [], []
        if statuses:
            clauses.append(f"i.status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if source_id is not None:
            clauses.append("i.source_id=?")
            params.append(source_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend((int(limit), max(0, int(offset))))
        rows = self._connect().execute(
            self._SELECT + where +
            " ORDER BY COALESCE(i.published_at, i.first_seen_at) DESC, i.id DESC"
            " LIMIT ? OFFSET ?",
            params).fetchall()
        return tuple(self._row(row) for row in rows)

    def item(self, item_id: int) -> FollowItemRow | None:
        row = self._connect().execute(
            self._SELECT + " WHERE i.id=?", (int(item_id),)
        ).fetchone()
        return self._row(row) if row is not None else None

    @staticmethod
    def _row(row) -> FollowItemRow:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        try:
            hidden = json.loads(row["hidden_media_json"] or "[]")
        except (TypeError, ValueError, IndexError):
            hidden = []
        return FollowItemRow(
            id=row["id"], source_id=row["source_id"], provider=row["provider"],
            ref=row["ref"], source_label=row["source_label"], entity_id=row["entity_id"],
            external_id=row["external_id"], title=row["title"], url=row["url"],
            media_url=row["media_url"], thumb_url=row["thumb_url"],
            published_at=row["published_at"],
            published_precision=row["published_precision"], version=row["version"],
            duration=row["duration"], semantics=row["semantics"],
            release_key=row["release_key"],
            variant_kind=row["variant_kind"], variant_label=row["variant_label"],
            group_hint=row["group_hint"], status=row["status"], asset_id=row["asset_id"],
            first_seen_at=row["first_seen_at"], last_seen_at=row["last_seen_at"],
            hidden_media=tuple(key for key in hidden if isinstance(key, str) and key),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    @staticmethod
    def group(items: tuple[FollowItemRow, ...],
              authors: dict[int, tuple[str, frozenset[str]]] | None = None,
              population: tuple[FollowItemRow, ...] | None = None,
              ) -> tuple[ReleaseGroup, ...]:
        """把条目折叠成作品分组。

        先按来源自带的 `group_hint` 合并（booru 的 `parent_id` 比标题可靠），
        再按标题推出的 `release_key` 合并，最后同一组里选主条目。booru 上没有标题也
        没有出处的帖子，按角色、上传时间与标签重合度认出同一作品的连发（ADR-0046）；
        站内 id 连号上传的同一批短片按角色认成一包（ADR-0045）；角色、毫秒时长与标签
        都对得上的隔天重传也归进同一组（ADR-0077）。

        `authors` 把来源 id 映射到（作者键, 这位作者的全部名字写法）。给了它，标题里
        夹着的作者名在分组前剥掉，相似标题的版本也跨同一作者的各个来源去对。

        `population` 是筛选前的全部条目，只用来认作者的固定导出长度（见
        `_align_reuploads`）；不给就按 `items` 认。按状态或标签筛过的一页里其他角色
        可能正好都不在，同一个时长就会被错认成只属于一部片子。
        """
        # 先按标题判据拆开同站撞车，再按来源自带的关系合并：来源自己声明过的关系
        # 优先，绝不能被标题判据拆散。
        # 兼容已经落库的旧论坛行：旧版用线程标题作为 release_key，读时也要按
        # 楼层拆开，部署后无需改写真实 ledger 就会立即显示成独立条目。
        split_posts = tuple(
            FollowItemRow(**{**item.__dict__,
                             "release_key": f"{item.release_key}\u0000{item.external_id}"})
            if item.provider in _RELEASE_KEY_PER_POST
            and not item.release_key.endswith(f"\u0000{item.external_id}")
            else item
            for item in map(_with_video_specs, map(_with_origin_hint, items))
        )
        stripped = _strip_author_names(split_posts, authors or {})
        linked = _hint_linked(stripped)
        # 连发判据排在出处对齐之后：没有出处的帖子要挂到同批有出处的那组上，得先让
        # 那组共用一个键。
        aligned = _align_reuploads(_align_tag_bursts(_align_by_group_hint(_align_upload_packs(
            _align_title_families(_split_ambiguous_works(stripped, linked), authors))),
            linked), linked, _template_lengths(population if population is not None else items))
        primaries = group_duplicates(aligned)
        buckets: dict[int, tuple[FollowItemRow, list[FollowItemRow]]] = {}
        for item, primary in zip(aligned, primaries):
            primary = primary if primary is not None else item
            buckets.setdefault(id(primary), (primary, []))[1].append(item)
        groups = [
            ReleaseGroup(
                primary.release_key, primary,
                tuple(m for m in members
                      if m is not primary and m.provider == primary.provider),
                tuple(m for m in members
                      if m is not primary and m.provider != primary.provider),
            )
            for primary, members in buckets.values()
        ]
        return tuple(sorted(groups, key=lambda g: g.newest_at, reverse=True))

    # ---- 状态与真相写入 -------------------------------------------------

    def set_status(self, item_id: int, status: str) -> None:
        if status not in ("new", "seen", "saved", "ignored"):
            raise FollowSourceError(f"未知的追更状态：{status}")
        if status == "saved":
            raise FollowSourceError("`saved` 只能由 save_asset() 设置")
        self._connect().execute(
            "UPDATE follow_item SET status=? WHERE id=?", (status, item_id))

    def set_media_hidden(self, item_id: int, key: str, hidden: bool) -> tuple[str, ...]:
        """记录或撤销一张媒体的隐藏，返回这条目当前的隐藏键集。

        键是媒体的稳定标识（`id` 优先、`url` 兜底），由调用方从 media_items 里
        解出来；这里只管集合的增删。集合为空时列写回 NULL，未隐藏过的行保持
        NULL，不制造一列空 JSON。
        """
        row = self._connect().execute(
            "SELECT hidden_media_json FROM follow_item WHERE id=?", (int(item_id),)
        ).fetchone()
        if row is None:
            raise FollowSourceError(f"追更条目不存在：{item_id}")
        try:
            current = [key for key in json.loads(row[0] or "[]")
                       if isinstance(key, str) and key]
        except ValueError:
            current = []
        if hidden and key not in current:
            current.append(key)
        if not hidden:
            current = [item for item in current if item != key]
        value = json.dumps(current, ensure_ascii=False) if current else None
        self._connect().execute(
            "UPDATE follow_item SET hidden_media_json=? WHERE id=?", (value, item_id))
        return tuple(current)

    def set_image_dims(self, item_id: int, width: int, height: int, *,
                       media_index: int | None = None) -> bool:
        """给一条（或它第 N 张媒体）补上图片宽高，只补空缺。

        写了返回 True；条目不存在、那张媒体不在 metadata 的 `media_items` 里，或
        已经有一对正尺寸时不动，返回 False。尺寸只用来定比例，来自来源接口、
        文件头探测或界面加载完的 naturalWidth 都算同一个字段；已有的不覆盖，
        因为三处给的都是同一张图的比例，没有谁更权威。
        """
        dims = positive_dims(width, height)
        if dims is None:
            raise FollowSourceError(f"图片宽高必须是正整数：{width}×{height}")
        if media_index is None:
            prefix = "$"
            guard = ""
        else:
            prefix = f"$.media_items[{int(media_index)}]"
            guard = f" AND json_type(metadata_json,'{prefix}')='object'"
        cursor = self._connect().execute(
            "UPDATE follow_item SET metadata_json=json_set(COALESCE(metadata_json,'{}'),"
            f"'{prefix}.width',?,'{prefix}.height',?)"
            " WHERE id=? AND json_valid(COALESCE(metadata_json,'{}'))" + guard +
            f" AND (COALESCE(json_extract(metadata_json,'{prefix}.width'),0)<=0"
            f" OR COALESCE(json_extract(metadata_json,'{prefix}.height'),0)<=0)",
            (dims[0], dims[1], int(item_id)))
        return cursor.rowcount > 0

    # ---- 作者别名 -------------------------------------------------------

    def author_aliases(self) -> tuple[dict[str, str], list[dict]]:
        """全部跨站作者别名：`别名键 → 规范键` 的映射，以及按规范名分好的组。"""
        rows = self._connect().execute(
            "SELECT alias_key,alias_name,canonical_key,canonical_name,source "
            "FROM follow_author_alias ORDER BY canonical_name,alias_name"
        ).fetchall()
        mapping = {str(row["alias_key"]): str(row["canonical_key"]) for row in rows}
        groups: dict[str, dict] = {}
        for row in rows:
            canonical_key = str(row["canonical_key"])
            group = groups.setdefault(canonical_key, {
                "canonical_key": canonical_key,
                # 旧别名记录可能把 F95 的容器标题存成了规范显示名；只修正读投影，
                # 不在一次读取里偷偷改真实表。
                "canonical_name": author_display_text(row["canonical_name"]),
                "aliases": [],
            })
            if str(row["alias_key"]) != canonical_key:
                group["aliases"].append({
                    "key": str(row["alias_key"]),
                    "name": str(row["alias_name"]),
                    "source": str(row["source"]),
                })
        return mapping, [group for group in groups.values() if group["aliases"]]

    def upsert_author_alias(self, canonical_name: str, alias_name: str, *,
                            source: str, moment: datetime | None = None) -> dict | None:
        """写一条别名，且不让自动证据盖掉人的决定。

        人工确认可以有意重新分组。自动学习窄得多：它只填一个此前未知的平台手柄，
        任何已有映射（尤其是人工的）都不动。
        """
        canonical_key = normalized_author_name(canonical_name)
        alias_key = normalized_author_name(alias_name)
        if not canonical_key or not alias_key:
            raise ValueError("规范创作者名和平台别名都不能为空")
        if canonical_key == alias_key:
            raise ValueError("这两个名字归一化后相同，不需要维护别名")

        connection = self._connect()
        stamp = _now_text(moment)
        mapping, _ = self.author_aliases()
        canonical_root = mapping.get(canonical_key, canonical_key)
        alias_root = mapping.get(alias_key, alias_key)
        if source != "manual":
            existing = connection.execute(
                "SELECT 1 FROM follow_author_alias WHERE alias_key=?", (alias_key,)
            ).fetchone()
            if existing is not None or alias_root != alias_key:
                return None
            canonical_row = connection.execute(
                "SELECT canonical_name FROM follow_author_alias WHERE canonical_key=? "
                "ORDER BY CASE WHEN alias_key=canonical_key THEN 0 ELSE 1 END LIMIT 1",
                (canonical_root,),
            ).fetchone()
            if canonical_row is not None:
                canonical_name = str(canonical_row["canonical_name"])
        elif canonical_root != alias_root:
            connection.execute(
                "UPDATE follow_author_alias SET canonical_key=?,canonical_name=?,"
                "updated_at=? WHERE canonical_key=?",
                (canonical_root, canonical_name, stamp, alias_root),
            )

        conflict = (
            "DO UPDATE SET canonical_key=excluded.canonical_key,"
            "canonical_name=excluded.canonical_name,alias_name=excluded.alias_name,"
            "source=excluded.source,updated_at=excluded.updated_at"
            if source == "manual" else "DO NOTHING"
        )
        sql = (
            "INSERT INTO follow_author_alias(alias_key,alias_name,canonical_key,"
            "canonical_name,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
            f"ON CONFLICT(alias_key) {conflict}"
        )
        connection.execute(
            sql, (canonical_root, canonical_name, canonical_root, canonical_name,
                  source, stamp, stamp),
        )
        inserted = connection.execute(
            sql, (alias_key, alias_name, canonical_root, canonical_name,
                  source, stamp, stamp),
        ).rowcount
        if source != "manual" and not inserted:
            return None
        return {"canonical": canonical_name, "alias": alias_name, "source": source}

    def remove_author_alias(self, alias_name: str) -> None:
        """删掉一条别名。规范名本身不能当别名删掉，那会拆散整个组。"""
        alias_key = normalized_author_name(alias_name)
        if not alias_key:
            raise ValueError("别名不能为空")
        connection = self._connect()
        row = connection.execute(
            "SELECT canonical_key FROM follow_author_alias WHERE alias_key=?",
            (alias_key,),
        ).fetchone()
        if row is None:
            raise ValueError("这个创作者别名不存在")
        if str(row["canonical_key"]) == alias_key:
            raise ValueError("规范名不能作为别名移除")
        connection.execute(
            "DELETE FROM follow_author_alias WHERE alias_key=?", (alias_key,))

    def learn_official_author_alias(self, provider: str, ref: str,
                                    candidates) -> dict | None:
        """只从一个毫无歧义的官方资料页名字学一个平台手柄。

        判据故意窄：这一次抓取里所有候选的署名归一化后必须只有一个，否则宁可不学。
        自动证据永远不覆盖已有映射，见 `upsert_author_alias`。
        """
        if provider not in _OFFICIAL_IDENTITY_PROVIDERS:
            return None
        authors: dict[str, str] = {}
        for candidate in candidates:
            name = str(candidate.author or "").strip()
            key = normalized_author_name(name)
            if key:
                authors.setdefault(key, name)
        if len(authors) != 1:
            return None
        canonical_key, canonical_name = next(iter(authors.items()))
        handle = official_profile_handle(provider, ref)
        if not handle or normalized_author_name(handle) == canonical_key:
            return None
        return self.upsert_author_alias(canonical_name, handle,
                                        source=f"official:{provider}")

    # ---- 播放记录 -------------------------------------------------------

    def _item_status(self, item_id: int) -> str:
        row = self._connect().execute(
            "SELECT status FROM follow_item WHERE id=?", (item_id,)).fetchone()
        if row is None:
            # 「你给的 id 不存在」是请求错误，不是存储故障；调用方映射成 400。
            raise ValueError("follow item not found")
        return str(row["status"])

    def record_playback(self, item_id: int, moment: datetime | None = None) -> str:
        """记一次关注页直接播放，返回这条条目之后的状态。

        候选无需先保存成 asset：关注页可以直接播，播过就不再是 `new`。
        """
        status = self._item_status(item_id)
        connection = self._connect()
        stamp = (moment or datetime.now(timezone.utc)).timestamp()
        connection.execute(
            "INSERT INTO follow_playback"
            "(follow_item_id,profile_id,play_count,last_played) "
            "VALUES(?,'local-default',1,?) ON CONFLICT(follow_item_id,profile_id) "
            "DO UPDATE SET play_count=follow_playback.play_count+1,"
            "last_played=excluded.last_played",
            (item_id, stamp),
        )
        if status != "new":
            return status
        connection.execute(
            "UPDATE follow_item SET status='seen' WHERE id=?", (item_id,))
        return "seen"

    def record_playback_activity(self, item_id: int, *, position: float = 0.0,
                                 duration: float = 0.0, delta: float = 0.0,
                                 ended: bool = False,
                                 moment: datetime | None = None) -> dict:
        """累计在线播放的真实时长与最远到达位置。"""
        self._item_status(item_id)
        position, duration, delta = (max(float(position), 0), max(float(duration), 0),
                                     max(float(delta), 0))
        ratio = 1.0 if ended else (min(position / duration, 1.0) if duration > 0 else 0.0)
        connection = self._connect()
        stamp = (moment or datetime.now(timezone.utc)).timestamp()
        connection.execute(
            "INSERT INTO follow_playback(follow_item_id,profile_id,play_count,"
            "play_seconds,max_reached,last_played) "
            "VALUES(?,'local-default',1,?,?,?) "
            "ON CONFLICT(follow_item_id,profile_id) DO UPDATE SET "
            "play_seconds=follow_playback.play_seconds+excluded.play_seconds,"
            "max_reached=max(follow_playback.max_reached,excluded.max_reached),"
            "last_played=excluded.last_played",
            (item_id, delta, ratio, stamp),
        )
        return dict(connection.execute(
            "SELECT play_seconds,max_reached FROM follow_playback "
            "WHERE follow_item_id=? AND profile_id='local-default'", (item_id,),
        ).fetchone())

    def save_asset(self, item_id: int, *, confirm: bool = False,
                   moment: datetime | None = None) -> int:
        """把一个候选保存成 `location='online'` 的 asset。

        这是真相写入，必须显式 `confirm=True`。它只 INSERT 新行并回填 `asset_id`，
        不改写任何既有 asset 的真相字段，也不下载媒体。
        """
        if not confirm:
            raise FollowSourceError("写 ledger 需要显式 confirm=True")
        connection = self._connect()
        row = connection.execute(
            self._SELECT + " WHERE i.id=?", (item_id,)).fetchone()
        if row is None:
            raise FollowSourceError(f"追更条目 {item_id} 不存在")
        item = self._row(row)
        if item.asset_id:
            return item.asset_id
        if not item.url:
            raise FollowSourceError("候选没有作品页 URL，无法作为在线作品保存")
        stamp = _now_text(moment)
        existing = connection.execute(
            "SELECT id FROM asset WHERE location='online' AND path=?", (item.url,)
        ).fetchone()
        if existing is not None:
            asset_id = int(existing[0])
        else:
            creator = connection.execute(
                "SELECT canonical_name FROM entity WHERE id=?", (item.entity_id,)
            ).fetchone() if item.entity_id else None
            cursor = connection.execute(
                "INSERT INTO asset(location,path,name,medium,creator,duration,"
                "release_date,first_seen,last_seen) VALUES('online',?,?,?,?,?,?,?,?)",
                (item.url, item.title, _medium_for(item), creator[0] if creator else None,
                 item.duration,
                 (item.published_at or "")[:10] or None, stamp, stamp),
            )
            asset_id = int(cursor.lastrowid)
            if item.entity_id:
                connection.execute(
                    "INSERT OR IGNORE INTO asset_entity(asset_id,entity_id,role,source,"
                    "confidence,first_seen_at,last_seen_at)"
                    " VALUES(?,?,'creator',?,1.0,?,?)",
                    (asset_id, item.entity_id, f"follow:{item.provider}", stamp, stamp))
        connection.execute(
            "UPDATE follow_item SET status='saved', asset_id=? WHERE id=?",
            (asset_id, item_id))
        return asset_id

    # ---- 跨作者合集的存量清退 -------------------------------------------

    def collected_compilations(self) -> tuple["CompilationRow", ...]:
        """已入库、按当前判据算跨作者打包的条目。

        抓取那一侧只过滤新拿到的候选，判据收紧之前入库的行还在库里。已经保存成
        asset 的不在结果里：那条 asset 是真相，留不留由它自己那一侧决定。
        """
        connection = self._connect()
        rows = connection.execute(
            "SELECT i.id,i.external_id,i.title,i.status,s.ref,i.metadata_json"
            " FROM follow_item i JOIN follow_source s ON s.id=i.source_id"
            " WHERE s.provider='rule34video' AND i.asset_id IS NULL"
        ).fetchall()
        found = []
        for item_id, external_id, title, status, ref, payload in rows:
            try:
                credits = json.loads(payload or "{}").get("models") or []
            except json.JSONDecodeError:
                continue
            visual = Rule34VideoConnector.visual_model_count(credits)
            if visual <= Rule34VideoConnector.MAX_COLLECTION_MODELS:
                continue
            found.append(CompilationRow(
                item_id=int(item_id), external_id=str(external_id), title=str(title),
                status=str(status), source_ref=str(ref),
                credited=len(credits), visual=visual))
        return tuple(sorted(found, key=lambda row: -row.visual))

    def purge_compilations(self, rows, *, confirm: bool = False) -> int:
        """删掉这些条目，连同它们的播放记录。不可逆，要显式 `confirm=True`。

        播放记录先删：`PRAGMA foreign_keys` 默认是 OFF，指望表上那条
        `ON DELETE CASCADE` 会留下认不出主人的孤儿行。
        """
        if not confirm:
            raise FollowSourceError("删 ledger 行需要显式 confirm=True")
        connection = self._connect()
        removed = 0
        for row in rows:
            connection.execute("DELETE FROM follow_playback WHERE follow_item_id=?",
                               (row.item_id,))
            removed += connection.execute(
                "DELETE FROM follow_item WHERE id=?", (row.item_id,)).rowcount
        return removed


@dataclass(frozen=True)
class CompilationRow:
    """一条按画面作者数判为跨作者打包的存量条目。"""

    item_id: int
    external_id: str
    title: str
    status: str
    source_ref: str
    #: 站点 Artist 名单的长度，配音和音效都算在里面。
    credited: int
    #: 剔掉配音与音效之后的画面作者数，判据看的是这个。
    visual: int


def _medium_for(item: FollowItemRow) -> str:
    if item.duration:
        return "video"
    if item.provider in ("kemono", "coomer", "pawchive"):
        return "illustration"
    return "video"


def _hint_linked(items: tuple[FollowItemRow, ...]) -> frozenset[tuple[str, str]]:
    """来源已经声明为同组的条目。

    `group_hint` 是一个全局字符串（`fanbox:12304831`、`rule34xxx:post:998877`），
    只要两条以上共用它，来源就已经把它们认成同一个作品了。
    """
    counts: dict[str, int] = {}
    for item in items:
        if item.group_hint:
            counts[item.group_hint] = counts.get(item.group_hint, 0) + 1
    shared = {hint for hint, count in counts.items() if count > 1}
    return frozenset(
        (item.provider, item.external_id) for item in items
        if item.group_hint in shared
    )


def _split_ambiguous_works(items: tuple[FollowItemRow, ...],
                           linked: frozenset[tuple[str, str]] = frozenset(),
                           ) -> tuple[FollowItemRow, ...]:
    """`work` 语义下，同站出现两个不同的 main 帖子就按站内编号拆分作品组。

    实测踩到的例子：kemono 上「February Poll Animations」（1 月 31 日）和
    「February Poll + Animations」（2 月 15 日）是两个帖子，归一化后标题完全相同。
    同一个站点里两个都没有变体标记的帖子本来就是两个作品，撞车只说明标题判据到头了。
    这时哪个 alt 该挂到哪个 main 也无从判断，所以整组按 `external_id` 拆开，宁可多出
    几张卡片，也不把两个作品并成一个。

    `release` 语义不适用：那里同一来源的多条本来就是同一个作品的历次动态。
    """
    buckets: dict[tuple[str, str], list[FollowItemRow]] = {}
    for item in items:
        if item.semantics == "release" or not item.release_key:
            continue
        if (item.provider, item.external_id) in linked:
            continue
        buckets.setdefault((item.provider, item.release_key), []).append(item)
    ambiguous = {
        key for key, members in buckets.items()
        if len({member.external_id for member in members if member.variant_kind == "main"}) > 1
    }
    if not ambiguous:
        return items
    return tuple(
        FollowItemRow(**{**item.__dict__,
                         "release_key": f"{item.release_key}\u0000{item.external_id}"})
        if (item.provider, item.release_key) in ambiguous
        and (item.provider, item.external_id) not in linked else item
        for item in items
    )


#: 作者名在标题里至多占几个相邻的词（`Lazy Procrastinator` 占两个），以及多短的名字不剥。
_AUTHOR_NAME_MAX_TOKENS = 4
_AUTHOR_NAME_MIN_LENGTH = 3


def _with_video_specs(item: FollowItemRow) -> FollowItemRow:
    """存量作品键中的连写视频规格按当前版本判据投影，账本行保持原样。"""
    if item.semantics != "work" or not item.release_key or "\u0000" in item.release_key:
        return item
    tokens = item.release_key.split(" ")
    kept = [token for token in tokens if not video_spec_markers(token)]
    if len(kept) == len(tokens) or not kept:
        return item
    verdict = classify(item.title)
    return FollowItemRow(**{**item.__dict__, "release_key": " ".join(kept),
                            "variant_kind": verdict.variant_kind,
                            "variant_label": verdict.variant_label})


def _strip_author_names(items: tuple[FollowItemRow, ...],
                        authors: dict[int, tuple[str, frozenset[str]]],
                        ) -> tuple[FollowItemRow, ...]:
    """把标题里夹着的作者名从 `release_key` 里剥掉。

    同一部作品在 rule34video 上写成 `2B Love at Sunset [pantsushi] 4K`，在 Patreon 上是
    `2B Love at Sunset - 1080p`。来源绑了实体时，入库那一步已按实体别名剥过；没绑的
    来源只有名字，这里按同一作者全部来源的名字写法再剥一次，相邻几个词拼起来等于
    名字也算。剥完不足 `_FAMILY_MIN_BASE` 个词的键保持原样：`Ahri Evil_Rise7` 剥成
    `Ahri`，一个角色名底下是这位作者好几部作品，同键就会并成一张卡。剥完的键还得在
    这位作者别的条目里对得上（相同，或是它们的开头）：剥名只为让同一作者的几份落到
    一起，不借它跟别的作者撞键——lazyprocrastinator 的 `Kyrie Canaan [lazyprocrastinator]`
    剥完正好等于另一位作者的 `Kyrie Canaan`。
    """
    keys_by_author: dict[str, set[str]] = {}
    for item in items:
        known = authors.get(item.source_id)
        if known and item.release_key and chr(0) not in item.release_key:
            keys_by_author.setdefault(known[0], set()).add(item.release_key)
    renamed: dict[int, str] = {}
    for item in items:
        known = authors.get(item.source_id)
        key = item.release_key
        if not known or not key or chr(0) in key:
            continue
        names = {name for name in known[1] if len(name) >= _AUTHOR_NAME_MIN_LENGTH}
        tokens = key.split(" ")
        kept: list[str] = []
        index = 0
        while index < len(tokens):
            for end in range(min(len(tokens), index + _AUTHOR_NAME_MAX_TOKENS), index, -1):
                if "".join(tokens[index:end]) in names:
                    index = end
                    break
            else:
                kept.append(tokens[index])
                index += 1
        stripped = " ".join(kept)
        if _FAMILY_MIN_BASE <= len(kept) < len(tokens) and any(
                other == stripped or other.startswith(stripped + " ")
                for other in keys_by_author[known[0]] if other != key):
            renamed[item.id] = stripped
    if not renamed:
        return items
    return tuple(
        FollowItemRow(**{**item.__dict__, "release_key": renamed[item.id]})
        if item.id in renamed else item
        for item in items
    )


#: 相似标题归组的边界：被接的标题至少几个词、尾巴至多几个词、两条至多隔多久发布、
#: 哪些词说明是续作。
_FAMILY_MIN_BASE = 2
_FAMILY_MAX_TAIL = 4
_FAMILY_WINDOW = timedelta(days=7)
_FAMILY_SEQUEL_RE = re.compile(
    r"\d|^(?:part|pt|episode|ep|chapter|ch|vol|volume|season|act|scene|round)$")


def _align_title_families(items: tuple[FollowItemRow, ...],
                          authors: dict[int, tuple[str, frozenset[str]]] | None = None,
                          ) -> tuple[FollowItemRow, ...]:
    """同一作者的来源里，标题是另一条标题接一小段尾巴的变体归到那一条的键下。

    Pantsushi 把一个作品按清晰度拆成几帖发：`2B Love at Sunset - 720p`、`- 1080p`、
    `- 4K Ultra HD Unwatermarked`。前两条剥完变体标记是同一个键；第三条尾巴上的
    `Ultra HD` 不在词表里，键多出几个词。词表追不上每个创作者的写法，所以这里看
    标题之间的关系：一条的键等于另一条的键再接一段尾巴，就是同一作品的另一版。
    同一作者在别的站上传的那份也这样对：Patreon 的 `Kasumi Secret Ninja Training -
    Alt Black 4K unwatermarked` 与隔天 rule34video 的 `... Alt Black 4K`。没有作者
    映射的来源只在自己里面对。

    判据本身宽，由四个条件收住：接尾巴的那条自己带变体标记（关键词是佐证）；被接的
    标题至少 `_FAMILY_MIN_BASE` 个词（`Tifa` 底下接得出 `Tifa Lifeguard`、
    `Tifa Workout` 好几部）；尾巴至多 `_FAMILY_MAX_TAIL` 个词，不含数字和 `part`、
    `episode` 这类续作词（`Sayuri - Cowgirl 2` 是另一部）；两条发布相隔不超过
    `_FAMILY_WINDOW`——同一作品的几个版本是一起发的。归到能接上的最短那个键，几版
    因此落在同一组。
    """
    keys: dict[str, dict[str, list[FollowItemRow]]] = {}
    for item in items:
        if item.semantics == "release" or not item.release_key or "\u0000" in item.release_key:
            continue
        known = (authors or {}).get(item.source_id)
        bucket = known[0] if known else f"source:{item.source_id}"
        keys.setdefault(bucket, {}).setdefault(item.release_key, []).append(item)
    renamed: dict[int, str] = {}
    for by_key in keys.values():
        for key, members in by_key.items():
            tokens = key.split(" ")
            for cut in range(max(_FAMILY_MIN_BASE, len(tokens) - _FAMILY_MAX_TAIL),
                             len(tokens)):
                base = " ".join(tokens[:cut])
                if base not in by_key or any(
                        _FAMILY_SEQUEL_RE.search(token) for token in tokens[cut:]):
                    continue
                for member in members:
                    if member.variant_kind != "main" and member.id not in renamed and any(
                            _published_near(member, other) for other in by_key[base]):
                        renamed[member.id] = base
    if not renamed:
        return items
    return tuple(
        FollowItemRow(**{**item.__dict__, "release_key": renamed[item.id]})
        if item.id in renamed else item
        for item in items
    )


def _published_near(left: FollowItemRow, right: FollowItemRow) -> bool:
    try:
        gap = (datetime.fromisoformat(str(left.published_at).replace("Z", "+00:00"))
               - datetime.fromisoformat(str(right.published_at).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return False
    return abs(gap) <= _FAMILY_WINDOW


#: 标签连发归组的边界：相邻两条至多隔多久、站内 id 至多差多少、一般标签至少重合多少，
#: 以及哪些角色标签不算身份——rule34.xxx 把 POV 视角里的观众标成角色 `you`，几乎每条都有。
_BURST_GAP = timedelta(hours=3)
_BURST_ID_GAP = 3000
_BURST_MIN_OVERLAP = 0.5
_BURST_PSEUDO_CHARACTERS = frozenset({"you"})


def _align_tag_bursts(items: tuple[FollowItemRow, ...],
                      linked: frozenset[tuple[str, str]] = frozenset(),
                      ) -> tuple[FollowItemRow, ...]:
    """booru 上同一作品连着上传的几帖归到一个键下。

    booru 帖子没有标题，也常常既没有 `source` 也没有父帖：LazyProcrastinator 在
    rule34.xxx 上把 Angel (KOF) 的同一段动画按横屏、竖屏和几个机位拆成 6 帖，几分钟内
    连着发，除了角色和标签之外没有任何字段把它们连起来。

    四个条件同时成立才算连发：同一来源；身份标签的集合完全相同（见 `_burst_identity`）；
    按发布时间排开，相邻两条相隔不超过 `_BURST_GAP`；一般标签与组里某一条的 Jaccard
    重合度不低于 `_BURST_MIN_OVERLAP`。

    只改没有出处、来源也没声明同组的条目的键：出处与父帖是来源自己给的关系，比这里的
    推断可靠，不拿推断去改它。有出处的条目只当锚：InitialA 在 rule34.paheal 上一口气
    传了 9 帖，8 帖写着同一个 subscribestar 出处，剩下一帖没写。没出处的一串帖子前后
    `_BURST_GAP` 内、按同样的身份与重合度能对上的锚全在同一组时，这一串归进那一组；
    对上两组以上说明来源自己把它们分成了几个作品，挂哪一组都是猜，只在这一串里互相归。
    这一步排在 `_align_by_group_hint` 之后，锚所在的那组已经共用一个键。
    """
    buckets: dict[tuple[int, frozenset[str]], list[tuple[datetime, frozenset[str], bool,
                                                         FollowItemRow]]] = {}
    for item in items:
        metadata = item.metadata or {}
        tag_types = metadata.get("tag_types")
        if (metadata.get("title_from") != "tags" or not isinstance(tag_types, dict)
                or not item.release_key):
            continue
        identity = _burst_identity(tag_types)
        try:
            moment = datetime.fromisoformat(str(item.published_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if not identity:
            continue
        general = frozenset(tag for tag, kind in tag_types.items() if kind == "general")
        anchor = _declares_origin(item, linked)
        buckets.setdefault((item.source_id, identity), []).append(
            (moment, general, anchor, item))
    renamed: dict[int, str] = {}
    for members in buckets.values():
        members.sort(key=lambda entry: (entry[0], entry[3].external_id))
        anchors = [entry for entry in members if entry[2]]
        for run in _burst_runs([entry for entry in members if not entry[2]]):
            keys = {anchor[3].release_key for anchor in anchors if any(
                _burst_near(anchor, entry)
                and _overlap(anchor[1], entry[1]) >= _BURST_MIN_OVERLAP for entry in run)}
            if len(keys) == 1:
                key = keys.pop()
            elif len(run) > 1:
                key = min(entry[3].release_key for entry in run)
            else:
                continue
            renamed.update((entry[3].id, key) for entry in run)
    if not renamed:
        return items
    return tuple(
        FollowItemRow(**{**item.__dict__, "release_key": renamed[item.id]})
        if item.id in renamed else item
        for item in items
    )


def _burst_identity(tag_types: dict) -> frozenset[str]:
    """连发判据里的身份：作品与角色标签的集合。

    rule34.paheal 不给标签分类，`tag_types` 里全是 general，身份就退到整组标签（小写）：
    那里一帖只有几个标签（中位数 7 个），作者、角色、作品和 `animated` 这类形态词都在里面，
    整组相同才算同一作品，比按词形猜哪个是角色更窄。
    """
    kinds = set(tag_types.values())
    if kinds == {"general"}:
        return frozenset(str(tag).lower() for tag in tag_types)
    return frozenset(
        tag for tag, kind in tag_types.items()
        if kind in ("copyright", "character") and tag not in _BURST_PSEUDO_CHARACTERS)


def _burst_runs(members: list) -> list[list]:
    """按时间排好的条目切成连发串：相邻两条时间与站内 id 都挨着，标签与串里某一条够重合。"""
    runs: list[list] = []
    for entry in members:
        if runs and _burst_near(entry, runs[-1][-1]) and any(
                _overlap(entry[1], other[1]) >= _BURST_MIN_OVERLAP for other in runs[-1]):
            runs[-1].append(entry)
        else:
            runs.append([entry])
    return runs


def _burst_near(left: tuple, right: tuple) -> bool:
    """两帖的发布时间相隔不超过 `_BURST_GAP`，站内 id 相差不超过 `_BURST_ID_GAP`。

    时间不够：rule34.xxx 早年落库的行，`published_at` 是 dapi 的 `change`（最后修改时间）。
    有人一次改一批旧帖的标签，几个月前各自上传的帖子就「在同一分钟发布」——Rekin3D 的
    17361475（05-01 上传）与 17559518（05-20 上传）都记成 09-14 03:0x。帖子 id 是全站
    递增的上传序号，改标签不动它，所以连发还要 id 挨着。id 不是纯数字的站只看时间。
    """
    if abs(left[0] - right[0]) > _BURST_GAP:
        return False
    try:
        return abs(int(left[3].external_id) - int(right[3].external_id)) <= _BURST_ID_GAP
    except (TypeError, ValueError):
        return True


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _align_reuploads(items: tuple[FollowItemRow, ...],
                     linked: frozenset[tuple[str, str]] = frozenset(),
                     templates: frozenset[tuple[int, float]] | None = None,
                     ) -> tuple[FollowItemRow, ...]:
    """同一段片子隔几天重传的几帖归到一个键下（ADR-0077）。

    Memz 的 Grace Ashcroft 动画 07-18 传过一帖，07-23 又传了两帖：文件各自重新编码，MD5
    不同，站上生成的缩略图也取了不同的帧，只有时长逐毫秒相同（60.054 秒）。连发判据的
    3 小时窗口接不住，作品标签也多挂了一个 `resident_evil_9:_requiem`。

    条件同时成立才算重传：

    1. 同一来源，时长到毫秒相同，而且这个时长在这个来源里只出现在一种角色组合上。作者常用
       固定的导出长度：LazyProcrastinator 的 20.02 秒用在 98 种角色组合上，InitialA 的
       31.232 秒用在 60 种上，这种时长相同说明不了是同一段。整秒的时长也不认，那多半是
       站点报的取整值。
    2. 角色标签的集合相同；作品标签不看，各帖写法不一。
    3. 一般标签的 Jaccard 重合度不低于 `_BURST_MIN_OVERLAP`。
    4. 发布时间相隔 `_BURST_GAP` 以上。同一时段里连着传的几帖归连发判据管，这里不拿
       时长把它按标签切开的几段重新串起来。

    合并的是整组的键，不是单帖：一帖已经在连发组里，整组跟着它走。有出处的组只当锚，
    一次合并里只能有一个锚：两帖各写了不同的出处，是来源自己说的两次发布，不并。

    `templates` 是第 1 条里的固定导出长度（`_template_lengths`），不给就按 `items` 算。
    """
    if templates is None:
        templates = _template_lengths(items)
    buckets: dict[tuple, list[tuple[frozenset[str], datetime, frozenset[str],
                                    FollowItemRow]]] = {}
    for item in items:
        found = _reupload_bucket(item)
        if found is not None and found[0] not in templates and found[1]:
            buckets.setdefault((found[0], found[1]), []).append((*found[1:], item))
    anchored = {item.release_key for item in items
                if item.release_key and _declares_origin(item, linked)}
    parent: dict[str, str] = {}
    anchors: dict[str, frozenset[str]] = {}

    def root(key: str) -> str:
        while parent.get(key, key) != key:
            key = parent[key]
        return key

    for members in buckets.values():
        for item, other in _reupload_pairs(members):
            left, right = root(item.release_key), root(other.release_key)
            if left == right:
                continue
            joined = (anchors.get(left, frozenset({left} & anchored))
                      | anchors.get(right, frozenset({right} & anchored)))
            if len(joined) > 1:
                continue
            target = next(iter(joined)) if joined else min(left, right)
            parent[left] = parent[right] = target
            parent.pop(target, None)
            anchors[target] = joined
    if not parent:
        return items
    return tuple(
        FollowItemRow(**{**item.__dict__, "release_key": root(item.release_key)})
        if item.release_key and root(item.release_key) != item.release_key else item
        for item in items
    )


def _template_lengths(items: tuple[FollowItemRow, ...]) -> frozenset[tuple[int, float]]:
    """作者的固定导出长度：同一来源里出现在两种以上角色组合上的毫秒时长。"""
    seen: dict[tuple, set[frozenset[str]]] = {}
    for item in items:
        found = _reupload_bucket(item)
        if found is not None:
            seen.setdefault(found[0], set()).add(found[1])
    return frozenset(key for key, characters in seen.items() if len(characters) > 1)


def _reupload_pairs(members: list) -> list[tuple[FollowItemRow, FollowItemRow]]:
    """同一来源、同一毫秒时长、同一组角色的一桶里，够得上重传的成对。"""
    return [(item, other)
            for index, (_, moment, general, item) in enumerate(members)
            for _, other_moment, other_general, other in members[index + 1:]
            if abs(moment - other_moment) > _BURST_GAP
            and _overlap(general, other_general) >= _BURST_MIN_OVERLAP]


def _reupload_bucket(item: FollowItemRow) -> tuple[
        tuple, frozenset[str], datetime, frozenset[str]] | None:
    """重传判据的桶键（来源、毫秒时长）、角色集合、发布时间与一般标签；
    不参与判断的条目给 None。"""
    metadata = item.metadata or {}
    tag_types = metadata.get("tag_types")
    if (metadata.get("title_from") != "tags" or not isinstance(tag_types, dict)
            or not item.release_key or not item.duration or item.duration <= 0):
        return None
    seconds = round(float(item.duration), 3)
    try:
        moment = datetime.fromisoformat(str(item.published_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if seconds.is_integer():
        return None
    characters = frozenset(tag for tag, kind in tag_types.items()
                           if kind == "character" and tag not in _BURST_PSEUDO_CHARACTERS)
    general = frozenset(tag for tag, kind in tag_types.items() if kind == "general")
    return (item.source_id, seconds), characters, moment, general


def _declares_origin(item: FollowItemRow, linked: frozenset[tuple[str, str]]) -> bool:
    """来源自己声明了这一帖属于哪次发布：写了出处，或与别的条目共用 `group_hint`。"""
    return (bool((item.metadata or {}).get("source"))
            or (item.provider, item.external_id) in linked)


#: 一包短片的边界：相邻两条站内 id 至多差多少、时长至多差几秒，以及标题开头的
#: 角色名至多几个词。
_PACK_ID_GAP = 100
_PACK_DURATION_SLACK = 1.0
_PACK_NAME_MAX_WORDS = 3
_PACK_NAME_SEPARATOR_RE = re.compile(r"\s+[-–—|:]\s+|[-–—]|[\[(『【|:]")
_PACK_LEADING_ARTICLES = frozenset({"the", "a", "an"})
_PACK_CHARACTER_TAG_RE = re.compile(r"\S.*\s\(.+\)$")


def _pack_name(title: str) -> str:
    """标题开头的角色名：`Angel-handjob` 是 `angel`，`Barney's Mom - Paizuri` 是
    `barney's mom`，`Luna Doggy` 是 `luna`。含数字的词（日期、序号）不算名字。"""
    def words(text: str) -> list[str]:
        cleaned = (word.strip(".,!?&\"'’").lower() for word in text.split()
                   if not any(char.isdigit() for char in word))
        return [word for word in cleaned if word]

    match = _PACK_NAME_SEPARATOR_RE.search(title or "")
    head = words(title[:match.start()]) if match else []
    if 1 <= len(head) <= _PACK_NAME_MAX_WORDS:
        return " ".join(head)
    rest = [word for word in words(title or "") if word not in _PACK_LEADING_ARTICLES]
    return rest[0] if rest else ""


def _align_upload_packs(items: tuple[FollowItemRow, ...]) -> tuple[FollowItemRow, ...]:
    """站内 id 连号上传的同一批短片、同一个角色的几条归到一个键下。

    rule34video 的视频 id 是全站递增的上传序号。LazyProcrastinator 把一段 KOF 沙滩
    动画导出成 7 条 20 秒的短片连着传（`Angel-handjob`、`Mai-blowjob` ……），id 只差
    4 到 20，标题各不相同，站点也不给出处。只看连号太宽：有人一口气补传几十部旧作，
    id 同样连着。所以五个条件同时成立才归组：同一来源；标题开头的角色名相同
    （Angel 与 Mai 是两组）；按 id 排开相邻两条相差不超过 `_PACK_ID_GAP`；时长相差不超过
    `_PACK_DURATION_SLACK` 秒（同一个工程导出的一批长度一样）；作品分类有交集。两条都带
    `名字 (作品)` 形式的角色标签却对不上时也拆开。

    归组按键合并：一条并进来，和它同键的别站副本也一起跟过来。
    """
    buckets: dict[tuple[int, str], list[tuple[int, FollowItemRow, frozenset[str],
                                              frozenset[str]]]] = {}
    for item in items:
        if (item.provider not in _SEQUENTIAL_UPLOADS or not item.release_key
                or not str(item.external_id).isdigit() or not item.duration):
            continue
        name = _pack_name(item.title)
        tag_types = (item.metadata or {}).get("tag_types")
        if not name or not isinstance(tag_types, dict):
            continue
        works = frozenset(tag for tag, kind in tag_types.items() if kind == "copyright")
        # 角色在 rule34video 上归 general；artist 类里的 `Chloeangelva (VA)` 是配音演员。
        characters = frozenset(tag.lower() for tag, kind in tag_types.items()
                               if kind == "general" and _PACK_CHARACTER_TAG_RE.fullmatch(tag))
        buckets.setdefault((item.source_id, name), []).append(
            (int(item.external_id), item, works, characters))
    parent: dict[str, str] = {}

    def root(key: str) -> str:
        while parent.get(key, key) != key:
            key = parent[key]
        return key

    for members in buckets.values():
        members.sort(key=lambda entry: entry[0])
        for previous, current in zip(members, members[1:]):
            if (current[0] - previous[0] <= _PACK_ID_GAP
                    and abs(current[1].duration - previous[1].duration) <= _PACK_DURATION_SLACK
                    and current[2] & previous[2]
                    and not (current[3] and previous[3] and not current[3] & previous[3])):
                left, right = root(previous[1].release_key), root(current[1].release_key)
                if left != right:
                    parent[max(left, right)] = min(left, right)
    if not parent:
        return items
    return tuple(
        FollowItemRow(**{**item.__dict__, "release_key": root(item.release_key)})
        if item.release_key and root(item.release_key) != item.release_key else item
        for item in items
    )


def _with_origin_hint(item: FollowItemRow) -> FollowItemRow:
    """按条目记下的出处重算 `group_hint`。

    落库的 `group_hint` 是抓取那一刻按出处算的。读时按当前的 `origin_group_key` 重算，
    归一规则认出的新写法（例如缺协议头的 `x.com/…`）不必重抓就能生效。
    """
    origin = origin_group_key((item.metadata or {}).get("source"))
    if origin is None or origin == item.group_hint:
        return item
    return FollowItemRow(**{**item.__dict__, "group_hint": origin})


def _align_by_group_hint(items: tuple[FollowItemRow, ...]) -> tuple[FollowItemRow, ...]:
    """让来源声明为同组的条目共用一个 `release_key`。

    判据是「`group_hint` 字符串相同」，**跨站点成立**：rule34.xxx 从 `source` 归一出的
    `fanbox:12304831` 与 kemono 上同一帖子的键完全相同，同一个作品在两个站上因此
    精确合并，不必靠标题去猜。booru 的父子帖也走这条——父帖用自己的 id、子帖用
    `parent_id`，拼出来是同一个键。

    组键取组内最小的 `release_key`，保证结果与条目顺序无关。
    """
    keys: dict[str, str] = {}
    for item in items:
        if not item.group_hint or not item.release_key:
            continue
        current = keys.get(item.group_hint)
        if current is None or item.release_key < current:
            keys[item.group_hint] = item.release_key
    if not keys:
        return items
    return tuple(
        FollowItemRow(**{**item.__dict__, "release_key": keys[item.group_hint]})
        if item.group_hint and item.group_hint in keys
        and keys[item.group_hint] != item.release_key else item
        for item in items
    )
