"""稳定 JSON 契约的兼容入口：只做再导出，不放实现。

实现已经按域拆开：状态在 `web_state`，浏览与详情在 `web_catalog`，实体资料页在
`web_entity`，数据面板在 `web_stats`，清理与批量在 `web_batch`，路由表在 `web_router`。
新代码请直接 import 那些模块。

这个文件留下来只有一个理由：`scripts/audit_related_ranker.py` 和
`scripts/detect_avatar_faces.py` 从这里取 `WebContract`／`q_related`／`face_focus`，
tests 里也有上千行按 `web_contract.X` 写的断言。为这点历史把它们全改一遍不值得，
所以保留一层薄再导出。

有一个名字**刻意没有**再导出：`platform.system_volume`。它是存储卷一节会 patch 的
对象，而拆分之后 patch 打在这个 shim 上不会有任何效果——`web_stats` 读的是它自己
模块里的名字。让 `patch.object(web_contract, "system_volume")` 当场抛 AttributeError，
比让它静默失效好。这条对所有「搬走之后要 patch 的内部名」都成立：需要 patch 就
import 真的那个模块。
"""
from __future__ import annotations

# 纯规则的再导出。新代码请直接从 catalog_rules／entities 取，这里只为既有断言留路。
from .catalog_rules import (
    LENGTH_TAGS,
    face_focus,
    is_jav_code,
    jav_display_metadata,
    normalise_code_key,
    ordered_multipart_items,
    part_marker,
)
from .entities import normalize_entity_name
#: 真相字段的字段级归属，以及写入端点的乐观并发约定。写入端点用请求体字段
#: `expected_revision`（`EXPECTED_REVISION_FIELD`）带上客户端读到的
#: `mutation_revision`；对不上时 `/api/*` 回 409，body 带 `revisions`。
#: 不用 `If-Match`：契约派发只把 body 交给处理器，请求头到不了那一层。
from .field_owners import (
    EXPECTED_REVISION_FIELD,
    OWNED_FIELDS,
    RevisionConflict,
    parse_owners,
    write_owned_fields,
)
# 卡片上的 `poster_box` 字段由这两个名字定形：框由 `front_panel_box` 算，
# 端点侧由 `WebContract.poster_box` 从边车读出来。字段的形状写在
# `jav_poster_crop.projection` 的文档串里——源图像素坐标加源图尺寸，
# 不该裁、没算过与读不出一律 null。
from .jav_poster_crop import PANEL_ASPECT, front_panel_box
# 这几个处理器本来就住在别的域模块里，旧 `web_contract` 只是把它们又摊了一遍。
# tests 里三十多处按 `web_contract.X` 调用，所以再导出留着；新代码请直接 import 那边。
from .web_activity import (
    w_activity,
    w_feedback,
    w_preference,
    w_quality_goal,
    w_watch_later,
)
from .web_playlists import q_playlists, w_playlist
from .web_review import w_review_decision, w_review_genre
from .web_batch import (
    AD_DIRPACK,
    AD_DOMAIN,
    ASSET_REFERENCE_TABLES,
    BUNDLE_DIR_ASSETS,
    CONTENT_BYTES,
    INTERNET_SHORTCUT_SUFFIXES,
    JUNK_KINDS,
    PART_MARK,
    PROMO_CLUSTER_FILES,
    PROMO_DOMAIN,
    PROMO_FILLER,
    PROMO_PHRASE,
    REAL_CODE,
    has_sibling_original,
    promo_residue,
    purge_assets,
    q_ads,
    q_duplicates,
    w_batch,
    w_empty_trash,
)
from .web_catalog import (
    CARD_PERFORMERS,
    EDITION_ORDER,
    EFFECTIVE_REGION_SQL,
    JAV_ASSET_CLAUSE,
    JAV_ASSET_PREDICATE,
    _edition_groups,
    attach_card_performers,
    attach_edition_groups,
    attach_jav_display_fields,
    attach_multipart_groups,
    con_entities,
    con_tags,
    effective_region_sql,
    q_editions,
    q_facets,
    q_item,
    q_items,
    q_parts,
    q_related,
    q_tops,
    state_clause,
    state_predicate,
    tag_is_not_a_performer_name,
    tag_not_hidden,
    w_item_tag,
)
from .web_entity import (
    q_entity,
    q_entity_photos,
    q_index,
    q_photo_set,
    q_suggest,
    w_entity_alias,
    w_entity_name,
)
from .web_router import (
    GET_HANDLERS,
    POST_HANDLERS,
    READ_ONLY_POST_ROUTES,
    ContractRouteNotFound,
    dispatch_api_get,
    dispatch_api_post,
)
from .web_state import CACHE_TTL, FAVICON, WebContract
from .web_stats import (
    TASTE_WINDOWS,
    q_quality_goals,
    q_search_history,
    q_stats,
    q_taste,
    w_search_history,
    w_taste_refresh,
    w_taste_source,
)

__all__ = [
    "PANEL_ASPECT", "front_panel_box",
    "AD_DIRPACK", "AD_DOMAIN", "ASSET_REFERENCE_TABLES", "BUNDLE_DIR_ASSETS", "CACHE_TTL",
    "EXPECTED_REVISION_FIELD", "OWNED_FIELDS", "RevisionConflict", "parse_owners",
    "write_owned_fields",
    "CARD_PERFORMERS", "CONTENT_BYTES", "ContractRouteNotFound", "EDITION_ORDER",
    "EFFECTIVE_REGION_SQL", "effective_region_sql",
    "FAVICON",
    "GET_HANDLERS", "INTERNET_SHORTCUT_SUFFIXES", "JAV_ASSET_CLAUSE",
    "JAV_ASSET_PREDICATE", "JUNK_KINDS", "LENGTH_TAGS", "PART_MARK", "POST_HANDLERS",
    "PROMO_CLUSTER_FILES", "PROMO_DOMAIN", "PROMO_FILLER", "PROMO_PHRASE",
    "READ_ONLY_POST_ROUTES", "REAL_CODE",
    "TASTE_WINDOWS", "WebContract", "_edition_groups", "attach_card_performers",
    "attach_edition_groups", "attach_jav_display_fields", "attach_multipart_groups",
    "con_entities", "con_tags",
    "dispatch_api_get", "dispatch_api_post", "face_focus", "has_sibling_original",
    "is_jav_code",
    "jav_display_metadata", "normalise_code_key", "normalize_entity_name",
    "ordered_multipart_items", "part_marker", "promo_residue", "purge_assets",
    "q_ads", "q_duplicates", "q_editions", "q_entity", "q_entity_photos", "q_facets",
    "w_entity_alias", "w_entity_name",
    "q_index", "q_item", "q_items", "q_parts", "q_photo_set", "q_playlists",
    "q_quality_goals", "q_related", "q_search_history", "q_stats", "q_suggest", "q_taste",
    "q_tops", "state_clause", "state_predicate", "tag_is_not_a_performer_name",
    "tag_not_hidden", "w_activity", "w_batch",
    "w_empty_trash", "w_feedback", "w_item_tag", "w_playlist", "w_preference",
    "w_quality_goal", "w_review_decision", "w_review_genre", "w_search_history", "w_taste_refresh",
    "w_taste_source", "w_watch_later",
]
