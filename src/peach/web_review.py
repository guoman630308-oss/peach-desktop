"""复核队列：把带出处的候选摆到人面前，等一次明确的批准或否决。

从 `web_contract` 拆出。这一域读 `peach-data/generated` 下的候选 CSV、把它们和账本
现状比对、渲染成待复核行，并在用户批准后写真相字段——ADR-0006 的闸门落在
`w_review_decision`，判据与写入映射本身归 `metadata_auto_apply`。

浏览域不需要知道候选文件长什么样，复核域也不需要知道首页怎么排序；它们过去只是
恰好住在同一个文件里。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote

from .avatar_provider import install_entity_avatar
from .catalog_rules import is_korean_mib_code, normalise_code_key
from .code_creators import collect
from .entities import normalize_entity_name, resolve_entity, resolve_entity_id
from .field_owners import (
    EXPECTED_REVISION_FIELD,
    owner_label,
    owner_of,
    review_owner,
)
from .fsutil import atomic_write_bytes
from .genre_decisions import load_genre_decisions, record_genre_decision
from .genre_taxonomy import CONTENT_GENRES
# 判据与写入映射属于领域层（`metadata_auto_apply`）：命令行首扫、处理任务的自动落库和
# 这里的「通过」按钮必须是同一套，复核域只负责把它们摆到人面前并记下决定。
from .metadata_auto_apply import (
    METADATA_FIELD_COLUMNS,
    REVIEW_APPLY_LIMIT,
    _apply_metadata_candidate,
    _entity_identity_key,
    _fold_genre_decisions,
    _landing_scope,
    _offers_another_stage_name,
    _only_mib_official,
    _parsed_candidates,
    _performer_identity_keys,
    _row_candidates,
    _split_multi,
    genres_still_pending,
    metadata_decision_is_stale,
    overwrite_requires_review,
    refresh_current_values,
)
from .metadata_policy import FALLBACK_SOURCES, SOURCE_SPECS, preferred_tag_source
from .previews import logo_key
from .review_csv import CANDIDATE_PREFIX, read_candidates


class ReviewContract(Protocol):
    """复核需要契约提供的能力；比整个 WebContract 小得多。"""

    candidate_root: Path
    logo_root: Path
    avatar_root: Path

    def cache_bust(self) -> None: ...
    def read_connection(self): ...
    def has_cover(self, code: str) -> bool: ...
    def has_entity_image(self, kind: str, entity_id) -> bool: ...
    def entity_image_version(self, kind: str, entity_id) -> str: ...
    def write_transaction(self): ...


#: 队列现算、不读候选文件的类别，取行的函数见 `LIVE_CATEGORY_ROWS`。
LIVE_CATEGORIES = ("code_creators",)
#: 复核页的类别全集。页面的 tab 次序由 `app.js` 的 `REVIEW_LABELS` 定，与这里无关。
REVIEW_CATEGORIES = (*CANDIDATE_PREFIX, *LIVE_CATEGORIES)
#: 复核卡片上那张脸属于哪种实体。页面按同一张表决定 `/entity-image` 的 kind
#: （`app.js` 的 `ENTITY_REVIEW_CATEGORIES`），两边必须逐字一致：这边判成 creator、
#: 页面按 performer 取图，就是标志说有图而请求照样 404。不在表里的类别没有这个位置。
ENTITY_REVIEW_KINDS = {"creator_tags": "creator", "western_identity": "creator"}


def _needs_review(category: str, row: dict) -> bool:
    """已经有定论的行不该占复核页。

    babepedia 那批 168 条里有 143 条是「确认无档案」——站上确实没有这个人，
    没有可判断的东西，全列出来只会把真正要看的 25 条淹掉。封面同理：拿到 2184
    宽的高清图不需要人确认，未取得和仍停在 800 低清基线的才需要。
    """
    if category == "western_identity":
        return str(row.get("verdict") or "") in ("命中", "需人工确认")
    if category == "studio_logos":
        # 无 handle、无落盘图片和与现有 Logo 完全相同都没有人工可判断项。
        # 只有新的/变化的确认来源，或明确标 needs_confirmation 的图片才进队列。
        saved = bool(str(row.get("saved") or "").strip())
        state = str(row.get("content_state") or "").strip()
        accepted = str(row.get("accepted") or "").lower() in {"1", "true", "yes"}
        needs_confirmation = row.get("confirmation") == "needs_confirmation"
        return saved and state not in {"unchanged", "duplicate", "rejected"} and (
            accepted or needs_confirmation
        )
    if category == "cover_sources":
        # 封面抓取的成功、尺寸和缺失都是机械状态，不需要人工批准。旧界面把
        # 241 个未取得和 800 px 基线封面全塞进复核页，却没有可执行写入动作。
        return False
    if category == "fc2_markings":
        # FC2 大多数作品页评论区是空的，全列出来会把真正有标记的几十条淹掉。
        # 只有拿到演员名、等价关系或判成合集的才需要人看。
        return bool(row.get("performers") or row.get("equivalents")
                    or row.get("is_collection"))
    return True


REVIEW_PREVIEW_LIMIT = 60


def _creator_entity_ids(connection, creators: list[str]) -> dict[str, int]:
    """创作者名（含别名）-> 规范 creator 实体 id；一次查完，不按候选逐个查。"""
    wanted = [name for name in dict.fromkeys(creators) if name]
    if not wanted:
        return {}
    marks = ",".join("?" * len(wanted))
    found: dict[str, int] = {}
    for row in connection.execute(
        "SELECT e.id,e.canonical_name,alias.alias FROM entity e "
        "LEFT JOIN entity_alias alias ON alias.entity_id=e.id "
        f"WHERE e.kind='creator' AND (e.canonical_name IN ({marks}) "
        f"OR alias.alias IN ({marks}))",
        [*wanted, *wanted],
    ):
        for name in (row["canonical_name"], row["alias"]):
            if name in wanted:
                found.setdefault(name, row["id"])
    return found


def _creator_previews(connection, creators: list[str], *, include_unpictured: bool = False) -> dict[str, list[dict]]:
    """一次查完所有候选创作者的预览作品；按候选逐个查是 N+1。"""
    wanted = [name for name in dict.fromkeys(creators) if name]
    if not wanted:
        return {}
    marks = ",".join("?" * len(wanted))
    rows = connection.execute(
        "SELECT a.id,a.name,a.duration,e.canonical_name,alias.alias,a.creator FROM asset a "
        "LEFT JOIN asset_entity ae ON ae.asset_id=a.id AND ae.role='creator' "
        "LEFT JOIN entity e ON e.id=ae.entity_id AND e.kind='creator' "
        "LEFT JOIN entity_alias alias ON alias.entity_id=e.id "
        "WHERE a.medium='video' AND a.disposal IS NULL "
        + ("" if include_unpictured else "AND a.snapshot_path IS NOT NULL ")
        + f"AND (e.canonical_name IN ({marks}) OR alias.alias IN ({marks}) OR a.creator IN ({marks})) "
        "ORDER BY a.id",
        [*wanted, *wanted, *wanted],
    ).fetchall()
    previews: dict[str, list[dict]] = {name: [] for name in wanted}
    seen: dict[str, set] = {name: set() for name in wanted}
    for row in rows:
        for candidate in (row["canonical_name"], row["alias"], row["creator"]):
            bucket = previews.get(candidate)
            if bucket is None or len(bucket) >= REVIEW_PREVIEW_LIMIT or row["id"] in seen[candidate]:
                continue
            seen[candidate].add(row["id"])
            bucket.append({"id": row["id"], "name": row["name"], "duration": row["duration"]})
    return previews


def _attach_review_asset_context(connection, rows: list[dict]) -> None:
    """Attach one representative original video without per-row SQL queries."""
    codes = [str(row.get("code") or row.get("query") or "").strip()
             for row in rows]
    codes = [code for code in dict.fromkeys(codes) if code]
    assets_by_code: dict[str, dict] = {}
    if codes:
        marks = ",".join("?" * len(codes))
        sql = (
            "SELECT id,name,code,snapshot_path,field_owners,mutation_revision "
            "FROM asset WHERE medium='video' "
            "AND disposal IS NULL AND (code IN (" + marks + ")"
        )
        params: list[object] = list(codes)
        if any(code.upper().startswith("FC2") for code in codes):
            sql += " OR code LIKE 'FC2%'"
        sql += ") ORDER BY (snapshot_path IS NULL),id"
        for asset in connection.execute(sql, params):
            key = normalise_code_key(asset["code"])
            assets_by_code.setdefault(key, dict(asset))

    entity_ids = [int(row["entity_id"]) for row in rows
                  if str(row.get("entity_id") or "").isdigit()]
    assets_by_entity: dict[int, dict] = {}
    if entity_ids:
        marks = ",".join("?" * len(entity_ids))
        for asset in connection.execute(
            "SELECT ae.entity_id,a.id,a.name,a.code,a.snapshot_path,a.field_owners,"
            "a.mutation_revision FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id "
            f"WHERE ae.entity_id IN ({marks}) AND a.medium='video' "
            "AND a.disposal IS NULL "
            "ORDER BY (a.snapshot_path IS NULL),a.id",
            entity_ids,
        ):
            assets_by_entity.setdefault(asset["entity_id"], dict(asset))

    explicit_ids = {
        int(row["asset_id"]) for row in rows
        if str(row.get("asset_id") or "").isdigit()
    }
    comparison_ids = {
        int(value) for row in rows
        for value in (row.get("left_asset_id"), row.get("right_asset_id"))
        if str(value or "").isdigit()
    }
    comparison_assets: dict[int, dict] = {}
    requested_ids = comparison_ids | explicit_ids
    if requested_ids:
        marks = ",".join("?" * len(requested_ids))
        for asset in connection.execute(
            "SELECT id,name,code,snapshot_path,field_owners,mutation_revision "
            f"FROM asset WHERE id IN ({marks})",
            sorted(requested_ids),
        ):
            comparison_assets[asset["id"]] = dict(asset)

    for row in rows:
        code = str(row.get("code") or row.get("query") or "").strip()
        asset = (comparison_assets.get(int(row['asset_id'])) if row.get('asset_path') and str(row.get('asset_id', '')).isdigit()
                 else assets_by_code.get(normalise_code_key(code)) if code else None)
        entity_id = str(row.get("entity_id") or "")
        if asset is None and entity_id.isdigit():
            asset = assets_by_entity.get(int(entity_id))
        explicit_id = str(row.get("asset_id") or "")
        if asset is None and explicit_id.isdigit():
            asset = comparison_assets.get(int(explicit_id))
        if asset is None and row.get("preview_assets"):
            first = row["preview_assets"][0]
            asset = {"id": first["id"], "name": first["name"], "code": code,
                     "snapshot_path": True}
        if asset is None:
            pass
        else:
            row["asset_id"] = asset["id"]
            row["asset_name"] = asset["name"]
            row["asset_code"] = asset.get("code") or code
            row["asset_has_snapshot"] = bool(asset.get("snapshot_path"))
            # 这张卡在问某个字段该填什么，那就得先说清现在这个值是谁填的：批准
            # 一个来源去覆盖用户自己写过的值，和覆盖一个刮削补上的值，是两件事。
            row["current_owner"] = owner_of(
                asset.get("field_owners"),
                METADATA_FIELD_COLUMNS.get(str(row.get("field") or "").strip()))
            row["asset_mutation_revision"] = int(asset.get("mutation_revision") or 0)
        row["comparison_assets"] = [
            comparison_assets[int(value)]
            for value in (row.get("left_asset_id"), row.get("right_asset_id"))
            if str(value or "").isdigit() and int(value) in comparison_assets
        ]


def _drop_fallback_challenges(rows: list[dict]) -> list[dict]:
    """兜底来源不挑战账本已有的值，这种行不该进队列（ADR-0038）。

    javbus 搜不到就返回首个近似命中：`AR-101` 给 `STAR-101`、`259LUXU-891` 给
    `259LUXU-1891`（ADR-0035）。

    空着的格子不走这条：那里只有它一家给得出值，有一个比没有强（ADR-0034）。
    """
    def keep(row: dict) -> bool:
        if not str(row.get("current_value") or "").strip():
            return True
        sources = {str(c.get("source") or "").strip() for c in row.get("candidates") or []}
        return not sources or not sources <= set(FALLBACK_SOURCES)

    return [row for row in rows if keep(row)]


def _metadata_decision_in_force(decision: dict, row: dict, genre_decisions: dict) -> dict:
    """这一行的旧决定还算不算数；不算数就当待定，返回空字典。

    过期判据与自动落库是同一份（`metadata_decision_is_stale`）：页面摆回来的行，
    下一轮处理任务也会重判，不会一边说「待定」一边永远不动。还有生词等收录的
    自动落库（`genres_still_pending`）同样摆回来，那一行等的是 genre 那一侧。
    """
    if not decision:
        return decision
    if metadata_decision_is_stale(decision, row.get("candidates") or [], code=row.get("code") or ""):
        return {}
    if decision.get("status") == "approved" and genres_still_pending(genre_decisions, decision):
        return {}
    return decision


def _code_creator_rows(connection) -> list[dict]:
    """番号目录被投影成创作者的队列，按 `peach.code_creators` 的判据现算。"""
    rows = [dict(row) for row in collect(connection)]
    for row in rows:
        row["item_key"] = str(row["entity_id"])
    return rows


#: 现算类别各自的取行函数，键与 `LIVE_CATEGORIES` 一一对应。加一个现算类别就是在
#: 这里登一行，读队列的 `_queue_rows` 不用跟着改。
LIVE_CATEGORY_ROWS = {"code_creators": _code_creator_rows}


def _queue_rows(contract: ReviewContract, category: str,
                connection) -> tuple[list[dict], str | None, int]:
    """这一类队列的原始行与来源说明：现算类别问账本，其余读候选文件。"""
    live = LIVE_CATEGORY_ROWS.get(category)
    if live:
        return live(connection), "ledger", 0
    rows, source, skipped = read_candidates(category, contract.candidate_root)
    return [row for row in rows if _needs_review(category, row)], source, skipped


def _metadata_queue_rows(connection, rows: list[dict],
                         decisions: dict) -> tuple[list[dict], dict[str, str | None]]:
    """元数据字段队列真正该摆在人面前的那些行，以及用到的 genre 决定。

    每一道都在回答同一个问题：这一行还有没有人能判、值得判的东西。
    """
    genre_decisions = load_genre_decisions(connection)
    for row in rows:
        row["candidates"] = _row_candidates(row, genre_decisions)
    # 候选全是错配时整行消失：剔完一条不剩，就没有可判的东西了。本来就没有候选
    # 的行照旧留着，它讲的是另一件事（这个番号问过、谁都没给值）。
    rows = [row for row in rows if row["candidates"] or not _parsed_candidates(row)]
    refresh_current_values(connection, rows)
    # 和账本已有的值比一遍，只把真差异留在队列里。实测 43 条候选里 24 条
    # 没有任何新信息：17 条与当前值逐字相同、7 条标签只是顺序不同。
    # 例外是标签部分落库（ADR-0038）留下的行：认得出的标签已经写进账本，所以
    # 它对账本「没有新信息」，可留在页面上要判的本来也不是标签，是那几个谁都
    # 不认的词。按新信息剔掉它，那几个词就再没有出现的地方了。
    rows = [row for row in rows
            if _metadata_row_adds_information(connection, row)
            or genres_still_pending(genre_decisions, decisions.get(row["item_key"], {}))]
    # 韩国 MIB 的番号不适用 JAV 规则，`metadata_routes` 给它的链是空的。但候选件是
    # 历史产物，闸门只管以后不再生成，管不了已经落盘的那些：2026-09-04 实测队列里
    # 还有 214 条（title 51、studio 51、release_date 51、performers 39、series 22）。
    # 这些值全是 JAV 目录站按错番号返回的别的作品，没有一条值得占用人的注意力。
    # MIB 官网（kmib）的候选是例外：那是这批番号自己的发行方。
    rows = [row for row in rows
            if not is_korean_mib_code(str(row.get("code") or "")) or _only_mib_official(row)]
    overrides = {row['item_key'] for row in rows if overwrite_requires_review(connection, row)}
    filtered = _drop_community_challenges_to_official(connection, rows)
    filtered = _drop_fallback_challenges(_drop_shop_side_dissent(filtered))
    kept = {row['item_key'] for row in filtered} | overrides
    return [row for row in rows if row['item_key'] in kept], genre_decisions


def _review_rows(contract: ReviewContract, category: str) -> tuple[list[dict], str | None, int]:
    #: 用户对来源 genre 的决定。判「这一行还有没有词等着收录」要用它，而那一步在库
    #: 连接关掉之后才跑，所以先取出来。
    genre_decisions: dict[str, str | None] = {}
    with contract.read_connection() as connection:
        rows, source, skipped = _queue_rows(contract, category, connection)
        decisions = {
            row["item_key"]: dict(row) for row in connection.execute(
                "SELECT item_key,status,note,updated_at FROM review_decision WHERE category=?",
                (category,),
            )
        }
        if category in {"creator_tags", "western_identity"}:
            names = [str(row.get("creator") or "").strip() for row in rows]
            previews = _creator_previews(connection, names, include_unpictured=category == "western_identity")
            # 这批候选判的是「这位创作者的作品该打什么标签」，主体是创作者本人。
            # 页面要给出创作者入口（头像 + 作品数），所以这里得把规范实体解析出来。
            entities = _creator_entity_ids(connection, names)
            for row in rows:
                name = str(row.get("creator") or "").strip()
                row["preview_assets"] = previews.get(name, [])
                row["entity_id"] = entities.get(name, row.get("entity_id", ""))
        elif category == "metadata_fields":
            rows, genre_decisions = _metadata_queue_rows(connection, rows, decisions)
        elif category == "performer_avatars":
            # 候选 CSV 里的 `current_name` 是抓取来源给的罗马音；账本早就有更好的
            # 规范名（`Alice Shaku` 的规范名是 `释爱丽丝`），罗马音本身也已经登记
            # 为别名。复核页该显示账本认的那个名字，来源写法降为副标题。
            _use_canonical_entity_names(connection, rows)
        _attach_review_asset_context(connection, rows)
    for row in rows:
        decision = decisions.get(row["item_key"], {})
        if category == "studio_logos" and row.get("content_state") == "changed":
            # 同一厂牌上游头像变化是新的事实；旧批次 approved 不得把变化静默藏掉。
            decision = {}
        if category == "metadata_fields":
            decision = _metadata_decision_in_force(decision, row, genre_decisions)
        row["decision"] = decision.get("status", "pending")
        row["decision_note"] = decision.get("note", "")
        row["preview_url"] = (row.get("resolved_url") or row.get("source_url")
                              or row.get("avatar_url") or row.get("portrait_url") or "")
        if category == "video_endcards":
            frame = PurePosixPath(str(row.get("frame_key") or ""))
            asset_id = str(row.get("asset_id") or "")
            if (asset_id.isdigit() and len(frame.parts) == 2
                    and frame.parts[0] == asset_id and frame.suffix.lower() == ".png"):
                row["preview_url"] = (
                    f"/endcard-frame?id={asset_id}&name={quote(frame.name)}"
                )
        if category == "cover_sources" and row.get("result") == "取得":
            # 封面已经在本机，直接看落盘的那张，不要回源站再拉一次。
            row["preview_url"] = f"/cover?code={quote(str(row.get('code') or ''))}"
        asset_code = str(row.get("asset_code") or row.get("code") or "")
        if row.get("asset_id"):
            if asset_code and contract.has_cover(asset_code):
                row["asset_preview_url"] = f"/cover?code={quote(asset_code)}"
            elif row.get("asset_has_snapshot"):
                row["asset_preview_url"] = f"/poster?id={row['asset_id']}&c=4"
            else:
                row["asset_preview_url"] = ""
        for comparison in row.get("comparison_assets") or []:
            comparison_code = str(comparison.get("code") or "")
            if comparison_code and contract.has_cover(comparison_code):
                comparison["preview_url"] = f"/cover?code={quote(comparison_code)}"
            elif comparison.get("snapshot_path"):
                comparison["preview_url"] = f"/poster?id={comparison['id']}&c=4"
            else:
                comparison["preview_url"] = ""
            comparison.pop("snapshot_path", None)
        if not row.get("reason"):
            row["reason"] = _review_evidence(category, row)
    # 卡片左边那张脸和别处的圆头像同一条链，只是这里没有代表作可退：装了实体图才
    # 出 `<img>`，否则就是首字母垫底，不再靠 404 把图摘掉。判定读的是目录索引，
    # 所以放在库连接之外。
    face_kind = ENTITY_REVIEW_KINDS.get(category)
    if face_kind:
        for row in rows:
            row["has_image"] = contract.has_entity_image(face_kind, row.get("entity_id"))
            row["image_version"] = contract.entity_image_version(face_kind, row.get("entity_id"))
            #: 取景与索引页、资料页同一份 sidecar、同一个换算。这张脸是复核时认人的
            #: 唯一线索，几何居中把脑袋裁掉就等于没有这一格。
            row["avatar_focus"] = contract.avatar_focus(face_kind, row.get("entity_id"))
    return _pending_first(rows), source, skipped


#: 元数据里的多值字段用顿号分隔；比较时按集合而不是按字符串。
MULTI_VALUE_FIELDS = {"performers", "tags"}


#: 厂牌和系列在账本里也是实体，字段里那串字符只是它的投影。
ENTITY_BACKED_FIELDS = {"studio", "series"}


def _metadata_row_adds_information(connection, row: dict) -> bool:
    """这一行候选相对账本现值有没有新东西；没有就不该占复核队列。

    复核的成本是人的注意力：把「和现在一模一样」的行混在里面，真正要判的那些
    就被淹掉了（`_needs_review` 已经对封面和 babepedia 做过同样的取舍）。
    """
    current = str(row.get("current_value") or "").strip()
    if not current:
        return True                      # 补空值总是有信息，例如发行日期
    field = str(row.get("field") or "").strip()
    candidates = row.get("candidates") or []
    if not candidates:
        return False
    if field in MULTI_VALUE_FIELDS:
        if field == "performers":
            # 企划名义不挑战账本里的主艺名：`259LUXU-1459` 账本存 `結城のの`，
            # libredmm 与 mgstage 给的是 `桜井奈々 28歳 某企業広報担当` 和
            # `佐倉井さん 28歳 某企業広報担当`——那是这部片给她起的称呼，不是她本人的
            # 艺名。两者不在同一层，不构成分歧（用户 2026-09-16 定「取主艺名」）。
            current_key = _performer_identity_keys(connection, _split_multi(current))
            return any(
                _performer_identity_keys(
                    connection, _split_multi(str(c.get("display_value") or ""))
                ) != current_key
                for c in candidates
                if _offers_another_stage_name(c)
            )
        current_set = frozenset(_split_multi(current))
        return any(
            frozenset(_split_multi(str(c.get("display_value") or ""))) != current_set
            for c in candidates
        )
    if field in ENTITY_BACKED_FIELDS:
        # 同一家厂牌在三处各有写法：账本存规范名 `Prestige`，javbus 给日文名
        # `プレステージプレミアム(PRESTIGEPREMIUM)`，libredmm 给日英并写的那一串。
        # 三者都指向同一条实体，按字符串比就成了要人判的「冲突」。
        current_key = _entity_identity_key(connection, field, current)
        return any(
            _entity_identity_key(
                connection, field, str(c.get("display_value") or "").strip()
            ) != current_key
            for c in candidates
        )
    return any(str(c.get("display_value") or "").strip() != current for c in candidates)


def _drop_community_challenges_to_official(connection, rows: list[dict]) -> list[dict]:
    """community 源推不翻 official 源已确认的值，这种行不该进队列。

    用户 2026-09-04 定的口径：按官方来。实测 26 条发行日期「冲突」里，账本现值全部由
    official 源写入（r18dev 10、aventertainment 9、libredmm 1），挑战方无一例外是
    javbus。它们不是「两个日期二选一」，而是 community 源要覆盖 official 源——项目自己的
    `SOURCE_SPECS` 早就排好了序，让人再判一遍等于把已经定好的信任模型丢回给人。

    判据只认账本里留下的落库记录：`review_decision` 的 note 记着当初是哪个来源写的。
    没有记录的行照常进队列——那说明现值来路不明（早期导入、文件名推断），community
    源的异议就有意义，拦掉它才是真的丢信息。
    """
    keyed = {str(row.get("item_key") or ""): row for row in rows
             if str(row.get("current_value") or "").strip()}
    if not keyed:
        return rows
    official_values: dict[str, str] = {}
    marks = ",".join("?" * len(keyed))
    for item_key, note in connection.execute(
            f"SELECT item_key,note FROM review_decision WHERE category='metadata_fields' "
            f"AND status='approved' AND item_key IN ({marks})", list(keyed)):
        try:
            parsed = json.loads(str(note or "{}"))
        except (TypeError, ValueError):
            continue
        spec = SOURCE_SPECS.get(str(parsed.get("source") or "").strip())
        value = str(parsed.get("value") or "").strip()
        # 只认记下了取值的那种记录。人工批准的 note 只记 `candidate_key` 与 `source`，
        # 证明不了账本现在这一个就是它写的；候选后来消失时更是无从判断。拿它当
        # 「official 已确认」会把该重开的字段永久压在队列外面。
        if value and spec is not None and spec.official:
            official_values[str(item_key)] = value
    if not official_values:
        return rows

    def keep(row: dict) -> bool:
        item_key = str(row.get("item_key") or "")
        if item_key not in official_values:
            return True
        preferred = preferred_tag_source(row.get("code"))
        sources = {c.get("source") for c in row.get("candidates") or []}
        sources.discard(None)
        if row.get("field") == "tags" and preferred in sources:
            return True
        # 现值被别的动作改过就不能再算「official 写的那一个」，交回人工。
        if official_values[item_key] != str(row.get("current_value") or "").strip():
            return True
        return any(
            (spec := SOURCE_SPECS.get(str(c.get("source") or "").strip())) is not None
            and spec.official
            for c in row.get("candidates") or []
        )

    return [row for row in rows if keep(row)]


#: MGS 是转售店。同一部片它的商品页跟片商自己那份有三处系统性差异：标题尾巴上缀着
#: 店铺加赠（`【MGSだけのおまけ映像付き+5分】`），发行日期写的是它自己的先行配信日
#: （本机实测早 8 天上下），系列写的是店内货架名（`しろうと女子のAV初体験`，片商那边
#: 是作品系列 `圧倒的ケツ圧ピストン！！`）。三处都不是「哪个对」，是「谁的口径」。
_SHOP_SIDE_SOURCES = frozenset({"mgstage"})
#: 片商自己的店和它的镜像。这两家跟账本一致，就说明账本存的已经是片商口径。
_MAKER_SIDE_SOURCES = frozenset({"dmm", "libredmm"})


def _drop_shop_side_dissent(rows: list[dict]) -> list[dict]:
    """转售店口径的异议不进队列：片商自己那份已经跟账本对上了。

    用户 2026-09-16 定的口径。

    片商方必须真的在场且与账本一致才成立：MGS 独家发行的片子没有片商方候选，它给的
    值是这个番号唯一的说法，照常进队列。多值字段不走这条——那里的差异是集合成员，
    不是同一个值的两种写法。
    """
    def keep(row: dict) -> bool:
        current = str(row.get("current_value") or "").strip()
        if not current or str(row.get("field") or "").strip() in MULTI_VALUE_FIELDS:
            return True
        agreeing, dissenting = set(), set()
        for candidate in row.get("candidates") or []:
            value = str(candidate.get("display_value") or "").strip()
            if not value:
                continue
            source = str(candidate.get("source") or "").strip()
            (agreeing if value == current else dissenting).add(source)
        if not dissenting or not dissenting <= _SHOP_SIDE_SOURCES:
            return True
        return not agreeing & _MAKER_SIDE_SOURCES

    return [row for row in rows if keep(row)]


def _use_canonical_entity_names(connection, rows: list[dict]) -> None:
    """把候选行的显示名换成账本规范名，来源写法留在 `source_name`。

    候选 CSV 里的 `entity_id` 是出候选那一刻的 id；那条实体后来并入了别人，就按合并墓碑
    换成现在那一条，名字、头像与批准落地都跟着它走。
    """
    ids = [int(row["entity_id"]) for row in rows
           if str(row.get("entity_id") or "").strip().isdigit()]
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    current = {row["id"]: (row["id"], row["canonical_name"]) for row in connection.execute(
        f"SELECT id,canonical_name FROM entity WHERE id IN ({marks})", ids)}
    merged = [entity_id for entity_id in ids if entity_id not in current]
    if merged:
        marks = ",".join("?" * len(merged))
        current.update({row["old_id"]: (row["id"], row["canonical_name"]) for row in connection.execute(
            "SELECT r.old_id,t.id,t.canonical_name FROM entity_redirect r"
            f" JOIN entity t ON t.id=r.target_id WHERE r.old_id IN ({marks})", merged)})
    for row in rows:
        raw = str(row.get("entity_id") or "").strip()
        target, name = current.get(int(raw), (None, None)) if raw.isdigit() else (None, None)
        if target is not None:
            row["entity_id"] = str(target)
        shown = str(row.get("current_name") or "").strip()
        if name and name != shown:
            row["source_name"] = shown
            row["current_name"] = name


def _pending_first(rows: list[dict]) -> list[dict]:
    """判过的不再占复核队列。

    队列由服务端定义，前端只负责画。原样返回全部候选、只给每行挂一个 `decision`，
    靠前端在本地把判过的行 splice 掉的话，「点通过」当场消失、一刷新全回来
    （厂牌 logo 上最明显）。

    `approved` / `rejected` 是终局，直接移出；`跳过` 按字面意思是「稍后再看」，
    留在队列里但排到最后，否则一次跳过就等于永久隐藏，而界面上没有任何入口
    能把它找回来。
    """
    return sorted(
        (row for row in rows if row.get("decision") not in ("approved", "rejected")),
        key=lambda row: row.get("decision") == "skipped",
    )


def _review_evidence(category: str, row: dict) -> str:
    """给本身没有 reason 列的候选拼一句可判断的证据，别让复核页只剩一个名字。"""
    if category == "metadata_fields":
        current = str(row.get("current_value") or "").strip() or "尚无"
        target = '匹配资产' if row.get('asset_path') else '同番号资产'
        owner = owner_label(str(row.get("current_owner") or ""))
        return (f"当前值：{current}"
                + (f"（{owner}）" if owner else "")
                + f"；{row.get('videos') or 0} 个{target}；"
                f"{len(row.get('candidates') or [])} 个资料来源")
    if category == "western_identity":
        overlap = row.get("token_overlap") or "0"
        variant = row.get("matched_variant") or ""
        spelling = f"（写法 {variant}）" if variant and variant != row.get("creator") else ""
        return (f"{row.get('verdict', '')} → {row.get('babepedia_name', '')}"
                f"{spelling}；词元重合 {overlap}；{row.get('videos', '')} 部作品")
    if category == "cover_sources":
        if row.get("result") != "取得":
            return f"未取得：{row.get('note') or '所有渠道都没有候选'}"
        return (f"{row.get('source', '')} · {row.get('width', '')}×{row.get('height', '')}"
                f" · {row.get('kb', '')} KB")
    if category == "fc2_markings":
        if row.get("is_collection"):
            return (f"合集，{row.get('collection_parts', '')} 个分片各自独立；"
                    f"封面不下发，分片回落到自己的缩略图")
        bits = []
        if row.get("performer_votes"):
            # 票数就是「几条独立评论这么说」，是这批候选唯一的置信度信号。
            bits.append(f"评论标记 {row.get('performer_votes')}")
        if row.get("equivalents"):
            bits.append(f"等同于 {row.get('equivalents')}")
        if row.get("writer"):
            bits.append(f"卖家 {row.get('writer')}")
        return "；".join(bits)
    if category == "fc2_similarity":
        kinds = str(row.get("evidence_kinds") or "").replace(" ", "、")
        detail = [f"证据 {kinds}" if kinds else "所选资料缺少足够依据"]
        if row.get("duration_delta_seconds") != "":
            detail.append(f"时长差 {row.get('duration_delta_seconds')} 秒")
        if row.get("size_delta_percent") != "":
            detail.append(f"体积差 {row.get('size_delta_percent')}%")
        if row.get("shared_performers"):
            detail.append(f"共同演员 {row.get('shared_performers')}")
        if row.get("warnings"):
            detail.append(str(row.get("warnings")))
        return "；".join(detail)
    return ""


def q_review(contract: ReviewContract):
    """复核页只放冲突项（ADR-0052 第五条）。

    九宫格没抽出来是机械状态，没有可批准或否决的东西，和 `cover_sources` 的未取得同一条
    理由不进队列；重抽归 `scripts/sheets.py`，缺多少由 `peach status` 的加工进度报。
    """
    sections, sources, skipped = {}, {}, {}
    for category in REVIEW_CATEGORIES:
        rows, source, dropped = _review_rows(contract, category)
        sections[category] = rows
        sources[category] = source
        skipped[category] = dropped
    # 候选文件缺失和主键缺失都要说出来。静默的空列表会被读成「没有待复核项」。
    # `genre_tags` 是收录 genre 时的候选词表。给的是静态表已经投影到的那百来个内容标签，
    # 不是账本里全部 5508 个标签实体：后者大半来自文件名，拿它当建议只会把噪声接着抄下去。
    return {"sections": sections, "sources": sources, "skipped_rows": skipped,
            "genre_tags": sorted(set(CONTENT_GENRES.values())),
            "counts": {key: len(value) for key, value in sections.items()}}


def _selected_metadata_candidate(contract: ReviewContract, item_key: str, candidate_key: str) -> tuple[dict, dict]:
    groups = {row["item_key"]: row
              for row in read_candidates("metadata_fields", contract.candidate_root)[0]}
    group = groups.get(item_key)
    if group is None:
        raise ValueError("所选字段资料不在当前批次，无法批准")
    if str(group.get("status") or "").strip() != "candidate":
        raise ValueError("只有待确认的资料字段可以批准")
    try:
        candidates = json.loads(str(group.get("candidates_json") or "[]"))
    except (TypeError, ValueError) as exc:
        raise ValueError("待确认资料的 JSON 格式无效") from exc
    selected = next((candidate for candidate in candidates
                     if isinstance(candidate, dict)
                     and str(candidate.get("candidate_key") or "") == candidate_key), None)
    if selected is None:
        raise ValueError("所选来源的资料不在当前待确认列表中")
    return group, selected


#: 候选图的扩展名 -> content type。`/logo` 靠 `.ct` 边车决定回什么头。
LOGO_CONTENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".ico": "image/x-icon",
}


#: 落盘名的规则归 `previews.logo_key`：取图、可用性判定和这里的批准落地必须同一套，
#: 各留一份正则的代价是「装上了却取不到」。名字沿用，导出的仍是同一个函数。
studio_logo_key = logo_key


#: 只记决定、不需要落地的类别，以及为什么。写在这里而不是靠人记：
#: `w_review_decision` 的每个落地分支都是手写的，把类别加进白名单却忘了写分支，
#: 表现就是「点通过、什么也没发生」——`creator_tags` 和 `studio_logos` 各犯过一次，
#: `performer_avatars` 是第三次。`test_every_approvable_category_can_land` 守住这条。
DECISION_ONLY_CATEGORIES = {
    "western_identity": "身份判断落在 entity 上，由专门的合并流程写，不在复核这一步",
    "code_creators": "番号与创作者的绑定由 metadata_fields 那条路写",
    "cover_sources": "封面已在 /cover 缓存里，复核只是确认取得与否",
    "fc2_markings": "只记录识别依据，不修改影片资料",
    "fc2_similarity": "提供不同番号之间的对应线索，合并需另行确认",
    "video_endcards": "只登记首尾帧证据，不改资产",
}


def _install_performer_avatar(contract: ReviewContract, entity_id: str) -> int:
    """把已批准的人物头像候选装进 `/entity-image` 真正读的目录。

    和 `_install_studio_logo` 同一个毛病、同一种修法：`performer_avatars` 一直只在
    分类白名单里，没有任何写入分支。审计脚本按设计只把外部图放进内容寻址缓存
    （「外部图只进入候选专用内容寻址缓存，不写 generated/avatars」），落地要人批准；
    而批准这一步什么也没做。结果是 18 个已判 ok 的候选——图早就下载好了，最大一张
    2880×1800——从 2026-08-25 起一直躺在缓存里进不去。

    按 sha256 定位缓存对象，并在装载前重算一遍校验：候选 CSV 的 `cache_path` 只是
    哈希名，路径可能过期，而内容寻址的意义就在于不必相信路径。缓存对象在
    provider-cache/performer-avatars/<provider>/objects 下按来源分目录——社媒与
    babepedia 管线（harvest_social_avatars.py）也走同一套缓存，装载按内容找，
    不绑定任何一个来源目录。

    落盘名跟着实体走（`{kind}-{id}.img`）：`/entity-image` 按 kind 分文件，creator
    实体（西方网黄，babepedia 命中的正是这批）写成 performer-<id>.img 是永远读不到的。
    """
    rows = {row["item_key"]: row
            for row in read_candidates("performer_avatars", contract.candidate_root)[0]}
    candidate = rows.get(str(entity_id))
    if candidate is None:
        raise ValueError("所选资料不在当前批次，无法批准")
    if str(candidate.get("verdict") or "").strip() != "ok":
        raise ValueError("只有通过图片质量检查的备选图片可以使用")
    digest = str(candidate.get("sha256") or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("所选图片缺少有效的 SHA-256 校验值")
    objects_root = contract.candidate_root / "provider-cache" / "performer-avatars"
    source = next((item for item in objects_root.glob(f"*/objects/{digest}.*")
                   if item.is_file()), None)
    if source is None:
        raise ValueError(f"所选图片不在本机缓存：{digest[:12]}")
    body = source.read_bytes()
    if hashlib.sha256(body).hexdigest() != digest:
        raise ValueError("图片内容与待确认记录不一致，无法使用")
    content_type = str(candidate.get("mime_type") or "").strip() or "image/jpeg"
    with contract.read_connection() as connection:
        # 候选出来之后实体并入了别人：图装到现在那一条名下，不写成没人读的孤立文件。
        target = resolve_entity_id(connection, int(entity_id)) or int(entity_id)
        kind_row = connection.execute(
            "SELECT kind FROM entity WHERE id=?", (target,)).fetchone()
    kind = (kind_row[0] if kind_row and kind_row[0] in {"performer", "creator"}
            else "performer")
    install_entity_avatar(contract.avatar_root, kind, target, body, content_type, {
        "source": "performer avatar review",
        "provider": candidate.get("provider") or "",
        "source_url": candidate.get("source_url") or "",
        "external_id": candidate.get("external_id") or "",
        "matched_name": candidate.get("matched_name") or "",
        "name_source": candidate.get("name_source") or "",
        "sha256": digest,
        "width": candidate.get("width") or "",
        "height": candidate.get("height") or "",
        "policy_version": candidate.get("policy_version") or "",
    })
    return 1


def _install_studio_logo(contract: ReviewContract, studio: str) -> int:
    r"""把已批准的厂牌 logo 候选装进 `/logo` 真正读的目录。

    `studio_logos` 必须有写入分支：只出现在分类白名单里的话，点「通过」只往
    `review_decision` 记一笔，logo 一张也没装上；配合「队列不过滤已判项」，
    表现就是点完通过、一刷新又回来。

    候选 CSV 的 `saved` 列写的是 `R:\peach-data\...`，那是旧 Peach 数据目录；现在数据在
    `peach-data` 下，按绝对路径找必然落空。所以只取文件名，在当前候选目录里解析。
    """
    rows = {row["item_key"]: row
            for row in read_candidates("studio_logos", contract.candidate_root)[0]}
    candidate = rows.get(studio)
    if candidate is None:
        raise ValueError("所选资料不在当前批次，无法批准")
    saved = str(candidate.get("saved") or "").strip()
    if not saved:
        raise ValueError("所选资料中没有可用的本机图片，无法使用")
    source = contract.candidate_root / "studio-logos" / PurePosixPath(
        saved.replace("\\", "/")).name
    if not source.is_file():
        raise ValueError(f"所选图片未保存在本机：{source.name}")
    key = studio_logo_key(studio)
    if not key:
        raise ValueError("厂牌名无法生成存盘文件名")
    content_type = LOGO_CONTENT_TYPES.get(source.suffix.lower())
    if content_type is None:
        raise ValueError(f"不支持的图片格式：{source.suffix}")
    contract.logo_root.mkdir(parents=True, exist_ok=True)
    destination = contract.logo_root / f"{key}.img"
    # 原子替换：中途失败不会留下半张图被 `/logo` 读到。
    atomic_write_bytes(destination, source.read_bytes())
    Path(f"{destination}.ct").write_text(content_type, encoding="utf-8")
    Path(f"{destination}.provenance.json").write_text(json.dumps({
        "source": "studio logo review",
        "source_file": source.name,
        "resolved_url": candidate.get("resolved_url") or "",
        "handle": candidate.get("handle") or "",
        "platform": candidate.get("platform") or "",
        "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "purpose": "local studio identity cache",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1


def _approve_metadata_with_receipt(connection, group, candidate, now, note, expected_revision):
    """批准一个来源值，保存已展示的候选，并取得同一事务中的实际版本回执。"""
    candidate = _fold_genre_decisions(str(group.get("field") or ""), candidate,
                                      load_genre_decisions(connection))
    asset_ids, *_ = _landing_scope(connection, group, candidate, expected_revision)
    marks = ",".join("?" * len(asset_ids))
    before = {str(row["id"]): int(row["mutation_revision"]) for row in connection.execute(
        f"SELECT id,mutation_revision FROM asset WHERE id IN ({marks})", asset_ids)}
    applied = _apply_metadata_candidate(
        connection, group, candidate, now, review_owner(str(candidate.get("source") or "")),
        expected_revision=expected_revision)
    revisions = {
        str(row["id"]): {"before": before[str(row["id"])], "after": int(row["mutation_revision"])}
        for row in connection.execute(
            f"SELECT id,mutation_revision FROM asset WHERE id IN ({marks})", asset_ids)}
    provenance = json.dumps({
        "candidate_key": candidate['candidate_key'], "source": candidate.get("source"),
        "reviewed_candidate_keys": [item.get("candidate_key") for item in _parsed_candidates(group)],
        "user_note": note,
    }, ensure_ascii=False, separators=(",", ":"))
    connection.execute(
        "UPDATE review_decision SET note=? WHERE category='metadata_fields' AND item_key=?",
        (provenance, group['item_key']))
    return applied, revisions


def w_review_decision(contract: ReviewContract, body):
    category = str(body.get("category", "")).strip()
    item_key = str(body.get("item_key", "")).strip()
    status = str(body.get("status", "")).strip()
    # 复核页展示的每个 tab 都必须能记录决定；漏掉一个，那一页的通过/跳过/拒绝就全部 400，
    # 前端静默时看起来就是「点了没反应」。`cover_sources` 曾这样漏掉。
    if category not in {
        "metadata_fields", "creator_tags", "studio_logos", "performer_avatars",
        "western_identity", "code_creators", "cover_sources", "fc2_markings",
        "fc2_similarity", "video_endcards",
    }:
        raise ValueError("invalid review category")
    if not item_key or status not in {"approved", "rejected", "skipped"}:
        raise ValueError("invalid review decision")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    note = str(body.get("note", "")).strip()[:2000]
    # 乐观并发是可选的：带上就按它验，不带就按「我是唯一写者」处理。前端只在候选钉死
    # 一条资产（`asset_path`）时带，按番号命中多条的那种组没有单一 revision 可报。
    raw_revision = body.get(EXPECTED_REVISION_FIELD)
    try:
        expected_revision = None if raw_revision in (None, "") else int(raw_revision)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{EXPECTED_REVISION_FIELD} 必须是整数") from exc
    asset_revisions = {}
    with contract.write_transaction() as connection:
        connection.execute(
            "INSERT INTO review_decision(category,item_key,status,note,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(category,item_key) DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=excluded.updated_at",
            (category, item_key, status, note, now),
        )
        applied = 0
        if category == "metadata_fields" and status == "approved":
            candidate_key = str(body.get("candidate_key") or "").strip()
            if not candidate_key:
                raise ValueError("批准资料字段时，请先选择要采用的来源")
            group, candidate = _selected_metadata_candidate(contract, item_key, candidate_key)
            applied, asset_revisions = _approve_metadata_with_receipt(
                connection, group, candidate, now, note, expected_revision)
        elif category == "creator_tags" and status == "approved":
            # 权威值只能来自候选文件本身。直接采信请求体的话，「批准候选 X」
            # 可以写入与 X 无关的创作者和标签，而 review_decision 里留痕仍写着 X 通过。
            candidates = {row["item_key"]: row
                          for row in read_candidates(category, contract.candidate_root)[0]}
            candidate = candidates.get(item_key)
            if candidate is None:
                raise ValueError("所选资料不在当前批次，无法批准")
            if str(candidate.get("status") or "").strip() != "candidate":
                raise ValueError("只有 candidate 状态的复核项可以批准")
            creator = str(candidate.get("creator") or "").strip()
            tags = [tag.strip() for tag in str(candidate.get("tags") or "").split("|") if tag.strip()]
            claimed_creator = str(body.get("creator", "")).strip()
            claimed_tags = [tag.strip() for tag in str(body.get("tags", "")).split("|") if tag.strip()]
            if (claimed_creator and claimed_creator != creator) or (claimed_tags and claimed_tags != tags):
                raise ValueError("提交内容与所选资料不一致，未保存")
            if not creator or not tags:
                raise ValueError("approved creator review requires creator and tags")
            entity = resolve_entity(connection, "creator", creator)
            if not entity:
                raise ValueError("creator entity not found")
            assets = connection.execute(
                "SELECT DISTINCT ae.asset_id FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id "
                "WHERE ae.entity_id=? AND ae.role='creator' AND a.medium='video' AND a.disposal IS NULL",
                (entity["id"],),
            ).fetchall()
            selected_ids = {int(value) for value in body.get("selected_ids") or []}
            available_ids = {asset["asset_id"] for asset in assets}
            if selected_ids:
                if not selected_ids <= available_ids:
                    raise ValueError("selected assets are outside the reviewed creator")
                asset_ids = sorted(selected_ids)
            else:
                # 没有勾选就是「整条候选通过」。这里什么都不写却照样把决定记成
                # approved 的话，留痕说通过、实际没写是最糟的组合。
                asset_ids = sorted(available_ids)
                if len(asset_ids) > REVIEW_APPLY_LIMIT:
                    raise ValueError(
                        f"该创作者有 {len(asset_ids)} 条作品，超过单次批准上限 "
                        f"{REVIEW_APPLY_LIMIT}，请在页面上显式勾选后再通过"
                    )
            payload = json.dumps({"review_item": item_key}, ensure_ascii=False)
            connection.executemany(
                "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) "
                "VALUES(?,?,0.6,'vision_creator_review')",
                [(asset_id, tag) for asset_id in asset_ids for tag in tags],
            )
            for tag in tags:
                # 标签实体只解析一次，关系走 executemany。
                # 逐条调用 upsert_asset_entity 会在持写锁期间跑上千次往返，把其它写入全挡住。
                normalized = normalize_entity_name(tag)
                connection.execute(
                    "INSERT INTO entity(kind,canonical_name,normalized_name,metadata_json,"
                    "created_at,updated_at) VALUES('tag',?,?,'{}',?,?) "
                    "ON CONFLICT(kind,normalized_name) DO UPDATE SET "
                    "canonical_name=excluded.canonical_name,updated_at=excluded.updated_at",
                    (tag, normalized, now, now),
                )
                entity_id = connection.execute(
                    "SELECT id FROM entity WHERE kind='tag' AND normalized_name=?", (normalized,),
                ).fetchone()[0]
                connection.executemany(
                    "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence,"
                    "metadata_json,first_seen_at,last_seen_at) "
                    "VALUES(?,?,'tag','vision_creator_review',0.6,?,?,?) "
                    "ON CONFLICT(asset_id,entity_id,role,source) DO UPDATE SET "
                    "confidence=excluded.confidence,metadata_json=excluded.metadata_json,"
                    "last_seen_at=excluded.last_seen_at",
                    [(asset_id, entity_id, payload, now, now) for asset_id in asset_ids],
                )
            applied = len(asset_ids)
        elif category == "studio_logos" and status == "approved":
            applied = _install_studio_logo(contract, item_key)
        elif category == "performer_avatars" and status == "approved":
            applied = _install_performer_avatar(contract, item_key)
    contract.cache_bust()   # 标签写完，聚合缓存必须失效，否则 facets 最多 90 秒还是旧数
    return {"ok": True, "category": category, "item_key": item_key, "status": status,
            "applied_assets": applied, "asset_revisions": asset_revisions}


def w_review_genre(contract: ReviewContract, body):
    """收录一个来源 genre：给它一个中文标签，或者判它不是内容标签。

    `tag` 留空就是后者。这里只写映射规则，不碰任何作品的标签——已经在队列里的候选
    由 `_fold_genre_decisions` 当场折进去，落库仍然要用户按那张卡上的「通过」。
    """
    genre = str(body.get("genre", "")).strip()
    tag = str(body.get("tag", "")).strip()
    if not genre:
        raise ValueError("要收录的 genre 是空的")
    # 标签名进的是和账本同一套词表，判据照 `w_item_tag` 那条：长度上限一致，
    # `演员:` 是出演者标记不是标签。两处各写一套的话，同一个名字这边收得进、那边加不上。
    if tag and (len(tag) > 80 or tag.startswith("演员:")):
        raise ValueError("标签名须为 1 到 80 个字符，且不能是出演者标记")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with contract.write_transaction() as connection:
        key = record_genre_decision(connection, genre, tag or None, now)
    contract.cache_bust()
    return {"ok": True, "genre": genre, "source_genre": key, "tag": tag}
