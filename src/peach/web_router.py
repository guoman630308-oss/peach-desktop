"""稳定 JSON 契约的路由表：路径 → 处理器，以及只读端的放行清单。

前端只认这三张表。它们单独成一个模块，是为了让「加一个端点」变成一处改动：
处理器写在自己的域模块里，在这里登记一行。反过来说，这个文件是唯一需要 import
全部域模块的地方，所以域模块之间不必互相认识。

`READ_ONLY_POST_ROUTES` 是这里最容易出事的一张表：写入端闸门管的是账本分叉，
而有些 POST 只是因为要带请求体才用 POST，根本不碰账本。漏登记的表现是用户在
只读端点某个按钮直接吃 409。
"""
from __future__ import annotations

from .follow_discovery import MAX_SUGGESTIONS, MAX_TERM_LENGTH
from .review_csv import candidate_root_version
from .web_activity import (
    w_activity,
    w_feedback,
    w_play,
    w_preference,
    w_quality_goal,
    w_watch_later,
)
from .web_batch import q_ads, q_duplicates, w_batch, w_empty_trash
from .taste_history import history_source_count
from .web_catalog import (
    catalog_filter,
    q_editions,
    q_facets,
    q_item,
    q_items,
    q_parts,
    q_related,
    q_tops,
    w_item_tag,
)
from .web_entity import (
    SUGGEST_GROUP_LIMIT,
    SUGGEST_KIND_LIMIT,
    q_entity,
    q_entity_photos,
    q_entity_shapes,
    q_index,
    q_photo_set,
    q_suggest,
    suggest_kinds,
    w_entity_alias,
    w_entity_name,
)
from .web_downloads import (q_downloads, w_download_cancel, w_download_postprocess,
                            w_download_submit)
from .web_resource_search import q_resource_search
from .web_jav_detail import q_jav_detail, q_jav_image, q_jav_resources, q_jav_reviews
from .web_jav_discovery import q_jav_discoveries
from .web_feeds import (
    q_feed_check,
    q_feed_discoveries,
    q_feed_lookup,
    q_feeds,
    w_feed_check,
    w_feed_discovery,
    w_feed_source,
)
from .web_follow import (
    q_follow,
    q_follow_authors,
    q_follow_check,
    q_follow_credentials,
    q_follow_schedule,
    q_follow_suggest,
    q_follow_tags,
    w_follow_activity,
    w_follow_author_alias,
    w_follow_check,
    w_follow_creator,
    w_follow_credential,
    w_follow_image_dims,
    w_follow_media_hide,
    w_follow_play,
    w_follow_resolve,
    w_follow_save,
    w_follow_schedule,
    w_follow_source,
    w_follow_status,
)
from .web_links import w_links, w_links_check, w_links_prune
from .web_library_processing import (q_library_processing, q_library_processing_issues,
                                     w_library_processing, w_library_import_file)
from .web_media_repair import q_media_repair, w_media_repair
from .web_organize import (
    q_organize, w_organize_apply, w_organize_preview, w_organize_rollback,
)
from .web_orphan_records import q_orphan_records, w_orphan_attach
from .web_playlists import q_playlist, q_playlists, w_playlist
from .web_custom_metadata import q_custom_metadata, w_custom_metadata
from .web_resource_sync import w_purge_missing, w_resource_sync_apply, w_resource_sync_scan
from .web_review import q_review, w_review_decision, w_review_genre
from .web_settings import q_settings, w_settings
from .web_library_shortcuts import q_library_shortcuts, w_library_shortcuts
from .web_folders import q_folders, w_folder_preview, w_folder_rule
from .web_scraping import (
    q_scraping, q_scraping_amane_bridge, w_scraping_amane_check, w_scraping_amane_rebuild,
    w_scraping_check, w_scraping_cover, w_scraping_settings,
)
from .web_state import WebContract, path_version
from .web_tasks import q_tasks
from .web_timeline_thumbnails import (
    q_media_processing, q_thumbnail_jobs, q_timeline, w_media_processing, w_thumbnail_jobs,
)
from .web_wants import q_wants, q_want_magnets, w_wants
from .web_stats import (
    q_quality_goals,
    q_search_history,
    q_stats,
    q_taste,
    taste_inputs,
    w_search_history,
    w_taste_refresh,
    w_taste_source,
)


class ContractRouteNotFound(KeyError):
    """The stable JSON contract has no handler for this path."""


def _get_item(contract, args):
    return q_item(contract, int(args["id"]))


def _get_index(contract, args):
    return q_index(
        contract,
        args.get("kind", "tags"),
        args.get("q", ""),
        min(max(int(args.get("limit", "180")), 1), 600),
        max(int(args.get("offset", "0")), 0),
        args.get("category", ""),
    )


def _get_stats(contract, _args):
    return contract.cached("stats", lambda: q_stats(contract))


def _get_tops(contract, args):
    n = min(int(args.get("n", "28")), 60)
    jav = args.get("jav") == "1"
    seed = str(args.get("seed", ""))[:32]
    state = str(args.get("state", ""))
    # 横滚续接一页一页地要，页号必须进缓存键：不进的话第二页拿回的是第一页的答案。
    page = max(int(args.get("page", "0")), 0)
    folders = {key: args[key] for key in ('folder', 'folder_recursive') if args.get(key)}
    return contract.cached(
        f"tops{n}p{page}{'-jav' if jav else ''}:{seed}:{state}:{sorted(folders.items())}",
        lambda: q_tops(contract, n, jav=jav, seed=seed, state=state, page=page, filters=folders),
    )


def _get_ads(contract, args):
    return q_ads(
        contract,
        min(int(args.get("limit", "60")), 200),
        max(int(args.get("offset", "0")), 0),
        args.get("kind", ""),
        args.get("status", "pending"),
    )


def _get_related(contract, args):
    """相关推荐走 LRU 缓存：打分是纯 Python 对几千个候选做 MMR，一次几百毫秒。

    每打开一次资料页都现算太贵，而结果只读不写；写路径（标签、反馈、播放、复核）
    本来就调 `cache_bust()`，缓存跟着一起作废。上面几个聚合用的是 `cached()`，
    这里必须用 `cached_lru()`：键跟着浏览过的资产走，键空间不封闭。
    键里带上 limit——同一个资产要不同条数就是不同结果。
    """
    asset_id = int(args["id"])
    limit = min(int(args.get("limit", "24")), 60)
    return contract.cached_lru(
        f"related:{asset_id}:{limit}", lambda: q_related(contract, asset_id, limit))


def _get_facets(contract, args):
    jav = args.get("jav") == "1"
    scope_kind = str(args.get("scope_kind", ""))
    scope_name = str(args.get("scope_name", ""))
    asset_id = int(args["id"]) if args.get("id") else None
    state = str(args.get("state", ""))
    scope_key = f"{scope_kind}:{scope_name}:{asset_id or ''}:{state}"
    filters = {key: args[key] for key in (
        'loc', 'creator', 'performer', 'studio', 'series', 'agency', 'tag', 'tag_match',
        'len', 'dur_min', 'dur_max', 'orient', 'region', 'exclude_vertical', 'q', 'thumb',
        'library', 'folder', 'folder_recursive',
    ) if args.get(key)}
    scope_key += repr(sorted(filters.items()))
    return contract.cached(
        f"facets{'-jav' if jav else ''}:{scope_key}",
        lambda: q_facets(
            contract,
            jav=jav, scope_kind=scope_kind, scope_name=scope_name, asset_id=asset_id,
            state=state, filters=filters or None,
        ),
    )


def _get_suggest(contract, args):
    """搜索栏每敲一下就来一次，所以走 LRU：键是用户输入，键空间不封闭。

    键里带上 limit 与 kind——同一段输入要不同条数、不同种类就是不同结果。写路径调
    `cache_bust()` 时它跟着一起作废，补全不会在新片入库后还给出旧的一批。
    """
    query = str(args.get("q") or "").strip()[:64]
    limit = min(max(int(args.get("limit", str(SUGGEST_GROUP_LIMIT))), 1), SUGGEST_KIND_LIMIT)
    kinds = suggest_kinds(args.get("kind", ""))
    if not query:
        return q_suggest(contract, "")
    return contract.cached_lru(
        f"suggest:{limit}:{','.join(kinds)}:{query}",
        lambda: q_suggest(contract, query, limit, kinds))


def _get_follow_suggest(contract, args):
    """关注添加框的建议，同样每敲一下来一次，同样走 LRU。

    这一路会打一次 rule34.xxx 的公开补全，缓存住的正是它：退格再敲回同一段前缀
    不该让站点多挨一枪。
    """
    query = str(args.get("q") or "").strip()[:MAX_TERM_LENGTH]
    try:
        limit = min(max(int(args.get("limit", str(MAX_SUGGESTIONS))), 1),
                    MAX_SUGGESTIONS * 2)
    except (TypeError, ValueError):
        limit = MAX_SUGGESTIONS
    if not query:
        return {"q": "", "groups": []}
    return contract.cached_lru(
        f"follow-suggest:{limit}:{query}",
        lambda: q_follow_suggest(contract, query, limit))


def _get_search_history(contract, args):
    return q_search_history(contract, int(args.get("limit", "10")))


def _get_taste(contract, args):
    window = str(args.get("window") or "all")
    return contract.cached_until_changed(
        f"taste:{window}", lambda: q_taste(contract, {"window": window}),
        *taste_inputs(contract, window))


def _get_review(contract, args):
    # 账本之外，复核读三处磁盘：抓取脚本写的候选 CSV、卡片封面徽章用的封面目录、
    # 创作者头像与取景用的头像目录。
    payload = contract.cached_until_changed(
        "review", lambda: q_review(contract),
        candidate_root_version(contract.candidate_root),
        path_version(contract.cover_root), path_version(contract.avatar_root))
    # 数据管理页只要一个待复核条数。完整 payload 带着每条候选的全文，实测是
    # 兆级；卡片上的一个数字不值这趟传输，但计数本身仍来自同一份缓存快照，
    # 不另立一套口径。
    if str(args.get("counts") or "") in {"1", "true"}:
        return {key: payload[key] for key in ("counts", "sources", "skipped_rows")
                if key in payload}
    return payload


def _get_entity_shapes(contract, args):
    # 每一页开头都要这一份（骨架照它留位），全部来自账本，账本不变就一直用同一份。
    return contract.cached_until_changed("entity-shapes",
                                         lambda: q_entity_shapes(contract, args))


#: 安装教程每一项只读这几个字段。载荷形状由 `test_post_setup_tutorial_only_carries_progress_counts`
#: 钉住：多带一个字段就是又把明细搬回来了。
POST_SETUP_SCRAPING_FIELDS = ("source", "accepts_cookie", "cookie_saved")
POST_SETUP_CREDENTIAL_FIELDS = ("provider", "requirement", "present", "missing")


def _post_setup_tutorial_progress(contract):
    """六项进度各自只算一个读数，不带明细。

    首页右下角那张卡要的是「导入了几项、哪几个来源存了凭证、有没有历史、关注开了几个、
    凭证齐不齐、还剩几条复核」。整份口味分析、完整关注列表和每条候选的全文都与它无关，
    实测那一套在真实馆藏上要二十多秒、三百多 KB，而这里几条 COUNT 与几列投影不到半秒。
    复核条数仍取自复核页的同一份缓存：那一栏的数字必须和 `/review` 页上的一致，口径只能有一份。
    """
    where, params = catalog_filter(contract, {})
    with contract.read_connection() as connection:
        total = connection.execute(
            "SELECT count(*) FROM asset a WHERE " + " AND ".join(where), params).fetchone()[0]
        follow_sources = [
            {"provider": row[0], "enabled": bool(row[1])}
            for row in connection.execute("SELECT provider,enabled FROM follow_source")]
    return {
        "library": {"total": int(total)},
        "scraping": {"sources": [{key: source[key] for key in POST_SETUP_SCRAPING_FIELDS}
                                 for source in q_scraping(contract, {})["sources"]]},
        "taste": {"history_sources": history_source_count(contract.taste_history_store)},
        "follow": {"sources": follow_sources},
        "credentials": {"providers": [{key: row[key] for key in POST_SETUP_CREDENTIAL_FIELDS}
                                      for row in q_follow_credentials(contract, {})["providers"]]},
        "review": {"counts": _get_review(contract, {"counts": "1"})["counts"]},
    }


def _get_post_setup_tutorial(contract, _args):
    """一次给安装教程六项进度读数，避免遗留首页重新接管 React 口味页的契约。

    教程卡跟着每次路由切换重新取数；走聚合缓存之后，连着点几页只算一次，账本一提交
    就失效，所以读到的仍然是当下的进度。
    """
    return contract.cached("post-setup-tutorial", lambda: _post_setup_tutorial_progress(contract))


def _post_empty_trash(contract, _body):
    return w_empty_trash(contract)


GET_HANDLERS = {
    "/api/media-processing": q_media_processing,
    "/api/folders": q_folders,
    "/api/tasks": q_tasks,
    "/api/downloads": q_downloads,
    "/api/resources/search": q_resource_search,
    "/api/jav/detail": q_jav_detail,
    "/api/jav/reviews": q_jav_reviews,
    "/api/jav/image": q_jav_image,
    "/api/jav/resources": q_jav_resources,
    "/api/jav/discoveries": q_jav_discoveries,
    "/api/library-processing": q_library_processing,
    "/api/library-processing/issues": q_library_processing_issues,
    "/api/thumbnail-jobs": q_thumbnail_jobs,
    "/api/timeline": q_timeline,
    "/api/scraping": q_scraping,
    "/api/scraping/cover": lambda contract, args: contract.scraping_cover_job.snapshot() or {"status": "idle"},
    "/api/scraping/amane-bridge": q_scraping_amane_bridge,
    "/api/settings": q_settings,
    "/api/library-shortcuts": q_library_shortcuts,
    "/api/feeds": q_feeds,
    "/api/feeds/check": q_feed_check,
    "/api/feeds/discoveries": q_feed_discoveries,
    "/api/feeds/lookup": q_feed_lookup,
    "/api/follow": q_follow,
    "/api/follow/credentials": q_follow_credentials,
    "/api/follow/tags": q_follow_tags,
    "/api/follow/authors": q_follow_authors,
    "/api/follow/schedule": q_follow_schedule,
    "/api/follow/check": q_follow_check,
    "/api/follow/suggest": _get_follow_suggest,
    "/api/follow/resolve": lambda contract, args: contract.follow_resolve_job.snapshot() or {"status": "idle"},
    "/api/taste/refresh": lambda contract, args: contract.taste_refresh_job.snapshot() or {"status": "idle"},
    "/api/links/prune": lambda contract, args: contract.link_prune_job.snapshot() or {"status": "idle"},
    "/api/resource-sync/apply": lambda contract, args: contract.resource_apply_job.snapshot() or {"status": "idle"},
    "/api/media-repair": q_media_repair,
    "/api/orphan-records": q_orphan_records,
    "/api/organize": q_organize,
    "/api/items": q_items,
    "/api/item": _get_item,
    "/api/parts": q_parts,
    "/api/editions": q_editions,
    "/api/entity": q_entity,
    "/api/entity/shapes": _get_entity_shapes,
    "/api/links": w_links,
    "/api/photos": q_entity_photos,
    "/api/photo-set": q_photo_set,
    "/api/index": _get_index,
    "/api/duplicates": q_duplicates,
    "/api/quality-goals": q_quality_goals,
    "/api/wants": q_wants,
    "/api/wants/magnets": q_want_magnets,
    "/api/stats": _get_stats,
    "/api/tops": _get_tops,
    "/api/ads": _get_ads,
    "/api/related": _get_related,
    "/api/custom-metadata": q_custom_metadata,
    "/api/playlists": q_playlists,
    "/api/playlist": q_playlist,
    "/api/facets": _get_facets,
    "/api/suggest": _get_suggest,
    "/api/search-history": _get_search_history,
    "/api/taste": _get_taste,
    "/api/review": _get_review,
    "/api/post-setup-tutorial": _get_post_setup_tutorial,
}

POST_HANDLERS = {
    "/api/media-processing": w_media_processing,
    "/api/folders/preview": w_folder_preview,
    "/api/folders/rule": w_folder_rule,
    "/api/downloads": w_download_submit,
    "/api/downloads/cancel": w_download_cancel,
    "/api/downloads/postprocess": w_download_postprocess,
    "/api/library-processing": w_library_processing,
    "/api/library-processing/import-file": w_library_import_file,
    "/api/thumbnail-jobs": w_thumbnail_jobs,
    "/api/scraping/settings": w_scraping_settings,
    "/api/scraping/cover": w_scraping_cover,
    "/api/scraping/check": w_scraping_check,
    "/api/scraping/amane-bridge/check": w_scraping_amane_check,
    "/api/scraping/amane-bridge/rebuild": w_scraping_amane_rebuild,
    "/api/feeds/check": w_feed_check,
    "/api/feeds/source": w_feed_source,
    "/api/feeds/discovery": w_feed_discovery,
    "/api/follow/check": w_follow_check,
    "/api/follow/schedule": w_follow_schedule,
    "/api/follow/source": w_follow_source,
    "/api/follow/author-alias": w_follow_author_alias,
    "/api/follow/creator": w_follow_creator,
    "/api/follow/resolve": w_follow_resolve,
    "/api/follow/credential": w_follow_credential,
    "/api/follow/status": w_follow_status,
    "/api/follow/media/hide": w_follow_media_hide,
    "/api/follow/image-dims": w_follow_image_dims,
    "/api/follow/save": w_follow_save,
    "/api/follow/play": w_follow_play,
    "/api/follow/activity": w_follow_activity,
    "/api/activity": w_activity,
    "/api/play": w_play,
    "/api/feedback": w_feedback,
    "/api/watch-later": w_watch_later,
    "/api/custom-metadata": w_custom_metadata,
    "/api/playlist": w_playlist,
    "/api/preference": w_preference,
    "/api/quality-goal": w_quality_goal,
    "/api/wants": w_wants,
    "/api/item-tag": w_item_tag,
    "/api/batch": w_batch,
    "/api/search-history": w_search_history,
    "/api/taste/refresh": w_taste_refresh,
    "/api/taste/source": w_taste_source,
    "/api/trash/empty": _post_empty_trash,
    "/api/organize/preview": w_organize_preview,
    "/api/organize/apply": w_organize_apply,
    "/api/organize/rollback": w_organize_rollback,
    "/api/purge-missing": w_purge_missing,
    "/api/orphan-records/attach": w_orphan_attach,
    "/api/links/check": w_links_check,
    "/api/links/prune": w_links_prune,
    "/api/resource-sync/scan": w_resource_sync_scan,
    "/api/resource-sync/apply": w_resource_sync_apply,
    "/api/media-repair": w_media_repair,
    "/api/review/decision": w_review_decision,
    "/api/review/genre": w_review_genre,
    "/api/settings": w_settings,
    "/api/library-shortcuts": w_library_shortcuts,
    "/api/entity-name": w_entity_name,
    "/api/entity-alias": w_entity_alias,
}


#: 这些 POST 不写 ledger，只是因为要带请求体才用 POST。写入端闸门管的是账本分叉，
#: 不该拦它们——「查找」只联网、「存凭据」只写本机 secrets 文件，都不碰账本。
#: 追更的「查找」在只读端被拦成 409 是实测踩到的。
READ_ONLY_POST_ROUTES = frozenset({
    "/api/media-processing",
    "/api/folders/preview",
    "/api/custom-metadata",
    "/api/scraping/settings", "/api/scraping/check",
    "/api/follow/resolve", "/api/follow/credential",
    "/api/taste/refresh", "/api/taste/source", "/api/resource-sync/scan",
    "/api/links/check",
    # 修复只写 `transcode_root` 里的边车，那是本机缓存，不是账本。
    "/api/media-repair",
    # 整理的预览只出一份计划 CSV，不动文件也不改账本；执行与回滚不在这里。
    "/api/organize/preview",
})
# `/api/resource-sync/apply` 不在上面：它永久删除文件已不在盘上的账本行，走的是
# `purge_assets`。检查那一步只读盘，候选留在进程内存里；它在任务中心的那一行
# 在只读端本来就不写。


def dispatch_api_get(contract: WebContract, path, args):
    """Dispatch the stable JSON read contract used by the current web client."""
    try:
        handler = GET_HANDLERS[path]
    except KeyError as exc:
        raise ContractRouteNotFound(path) from exc
    return handler(contract, args)


def dispatch_api_post(contract: WebContract, path, body):
    try:
        handler = POST_HANDLERS[path]
    except KeyError as exc:
        raise ContractRouteNotFound(path) from exc
    return handler(contract, body)
