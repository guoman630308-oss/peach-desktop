"""浏览与详情：列表、版本组、分卷组、单条详情、筛面和标签编辑。

从 2372 行的 `web_contract` 拆出的最大一块。这一域的共同点不是「都在查 asset 表」，
而是**共用同一套可见性与展示口径**：`tag_not_hidden` 决定哪些标签算数、
`state_predicate` 决定「未看／稍后看／已标记」怎么翻成 SQL、`attach_jav_display_fields`
决定番号作品在卡片上叫什么。这几条一旦在不同页面漂开，同一部片在首页、女优页和
详情页会显示成三个样子，而每一页单独看都「对」。所以它们必须留在同一个模块里，
由 `web_entity`、`web_stats`、`web_batch` 反过来 import，而不是各自再写一遍。
"""
from __future__ import annotations

import hashlib
import random
import time
from . import entities, media_libraries, sample_images, settings_file

from .catalog_rules import (
    LENGTH_TAGS,
    current_tags,
    fc2_collection_label,
    is_jav_asset,
    jav_edition_badges,
    jav_display_metadata,
    names_without_shared_part_tail,
    normalise_code_key,
    release_code_from_filename,
    part_marker,
    solo_performer_clause,
    tag_cat,
)
from .entities import normalize_entity_name, resolve_entity, upsert_asset_entity
from .catalog_versions import foldable_versions, partition_versions, version_labels
from .field_owners import parse_owners
from .metadata_policy import SOURCE_SPECS
from .media_sources import cost_for
from .regions import infer_region, normalize_region, region_label
from .web_activity import DEFAULT_PROFILE_ID
from .web_state import WebContract
from .western_artwork import artwork_key


GROUP_TABLES = ('asset', 'asset_entity', 'entity')

#: 卡片随每条记录返回的女优上限。共演作品必须带上全部出镜者，而不是只留第一位；
#: 但 BEST 合集实测有 41 位，全量下发会把列表响应撑大，所以截断并同时给出总数，
#: 由界面显示「等 N 人」。详情页走 q_item，不受这个上限影响。
CARD_PERFORMERS = 6


def attach_artwork_fields(contract: WebContract, item: dict) -> None:
    """列表、详情与推荐共用本地官方封面和版本投影。"""
    from .strm import is_pointer
    if is_pointer(item.get('path') or item.get('name') or ''):
        item['size'] = None
        item['is_strm'] = True
    key = artwork_key(item['id'], item.get('code'))
    item['has_cover'] = contract.has_cover(key)
    item['cover_key'] = key if item['has_cover'] else ''
    if item['has_cover']:
        item['cover_frame'] = contract.cover_frame(key)
        item['cover_version'] = contract.cover_version(key)
        item['poster_box'] = contract.poster_box(key)

#: 排序拆成「列 + 方向」两段：`{d}` 由方向填入。方向不进列键，`时长` 才能在同一枚
#: 控件上翻转，而不是分裂成两个互斥选项，也不会有一个方向在界面上永远点不到。
#: 每个方向都显式 `NULLS LAST`——升序时 SQLite 默认把 NULL 排在最前，
#: 「时长最短」会先给出一整屏没有时长的条目。
SORT_COLUMNS = {
    "name": "a.name COLLATE NOCASE {d} NULLS LAST, a.id {d}",
    "new": "a.first_seen {d} NULLS LAST, a.id {d}",
    "release": "a.release_date {d} NULLS LAST, a.first_seen {d} NULLS LAST, a.id {d}",
    "size": "a.size {d} NULLS LAST, a.id {d}",
    "dur": "a.duration {d} NULLS LAST, a.id {d}",
    "played": "a.last_played {d} NULLS LAST, a.id {d}",
    "rating": "a.rating {d} NULLS LAST, a.o_count {d} NULLS LAST, a.id {d}",
    "plays": "a.play_count {d} NULLS LAST, a.last_played {d} NULLS LAST, a.id {d}",
    "o": "a.o_count {d} NULLS LAST, a.id {d}",
}

#: 旧键沿用：地址栏和书签里存着把方向写进键名的值，认不出来它们会落到
#: `a.id DESC`——那不报错，只是给出一屏顺序看起来合理、其实没按要求排的结果。


def attach_jav_display_fields(row: dict, tags=(), entity_kinds=()) -> None:
    """Add one canonical display projection while retaining raw file identity fields."""
    # 产地取查询算好的那一列。取不到时退到番号推断——比直接读 `a.region` 强：那一列
    # 绝大多数行是空的，读它等于说「全都没判过」，韩国片又会原样回到 JAV 版式上。
    # 退化路径拿不到实体那一层，所以列表与详情的查询都把 `effective_region` 选出来。
    code = row.get('code') or _filename_fc2_code(row.get('name'))
    stored = normalize_region(row.get("region"))
    region = normalize_region(row.get("effective_region"))
    if not region:
        region = infer_region(code, row.get("name"))
    row["region"] = region
    row["region_label"] = region_label(region)
    # 详情页要能分开「这条是你定的」和「这是按番号猜的」，后者才需要请人确认。
    row["region_settled"] = bool(stored)
    row["is_jav"] = is_jav_asset(
        code, row.get("studio"), row.get("release_date"), entity_kinds,
        region,
    )
    if row["is_jav"]:
        row.update(jav_display_metadata(row.get("name"), code, tags))


def _filename_fc2_code(name) -> str:
    code = release_code_from_filename(name) or ''
    return code if code.startswith('FC2-PPV-') else ''


def tag_is_not_a_performer_name(tag: str) -> str:
    """「这个标签不是某个女优的名字」的 SQL 判据。

    同名的标签和 performer 身份会互相冒充，所以标签榜要把它们排掉。这条同样写了
    四份，其中一份拿 `lower(trim(t.tag))` 去比 `performer.normalized_name`——后者
    是 Python casefold 写进去的，而 SQLite 的 lower() 只认 ASCII，于是西里尔或
    带罗马数字的名字永远排不掉。列一侧一律走 `peach_normalize()`。
    """
    return ("NOT EXISTS(SELECT 1 FROM entity performer WHERE performer.kind='performer' "
            f"AND performer.normalized_name={tag})")


def tag_not_hidden(asset: str, tag: str) -> str:
    """「这个标签没有被用户隐藏」的 SQL 判据。

    同一条规则此前在五处各写了一份裸 SQL。join 列和比较列因查询语境不同是正常的，
    但规则本体（默认档案、hidden=1、按归一化名比对）只该有一份：漏抄一处，被隐藏
    的标签就会从那个表面漏回来，而这属于语义契约。

    `tag` 传比较用的表达式。列一侧要用 `peach_normalize()`——SQLite 的 lower() 只认
    ASCII，拿它去比 Python casefold 写进去的值，非 ASCII 标签永远匹配不上。
    """
    return ("NOT EXISTS(SELECT 1 FROM asset_tag_preference p "
            f"WHERE p.asset_id={asset} AND p.profile_id='{DEFAULT_PROFILE_ID}' "
            f"AND p.hidden=1 AND p.normalized_tag={tag})")


#: 「未归属」是一个真实的集合，不是详情页上的一句说明文字：22509 部视频里 4566 部
#: 没有任何署名人。判据和卡片署名同源（`cardIdentity` 取 creator 或 performer），
#: 所以卡片上写着「未归属」的每一条，点进这个集合都必须在里面。厂牌和系列不算归属：
#: 「ラグジュTV 出品」不回答「这是谁」，把有厂牌的排除掉会让集合小掉 631 条，而那批
#: 恰恰是最该被找出来补人的。
UNOWNED_ASSET_CLAUSE = (
    "NOT EXISTS(SELECT 1 FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
    "WHERE ae.asset_id=a.id AND e.kind IN ('performer','creator')) "
    "AND COALESCE(trim(a.creator),'')=''"
)

#: 搜索里的同一条关系，但按名字模糊比，并且认别名——事务所改名比女优改艺名还常见，
#: 「GRANZPRO」和「LiStarPRO」是同一批人。
AGENCY_SEARCH_CLAUSE = (
    "EXISTS(SELECT 1 FROM asset_entity ae "
    "JOIN entity_membership m ON m.member_id=ae.entity_id "
    "JOIN entity agency ON agency.id=m.agency_id "
    "WHERE ae.asset_id=a.id AND agency.kind='agency' "
    "AND (agency.canonical_name LIKE ? OR EXISTS(SELECT 1 FROM entity_alias al "
    "WHERE al.entity_id=agency.id AND al.alias LIKE ?)))"
)


#: 「这条资源在普通馆藏里看得见」的判据：是视频，且在库——不在回收站，也没有标「已消失」
#: （`disposal` 为空，ADR-0087）。列表、搜索和补全
#: 共用这一份。各写各的后果是补全会补出回收站里的作品——那一条点开是已经删掉的片，
#: 而列表本身从来不显示它，所以只会在补全这一个表面上露出来。
VISIBLE_CATALOG_ASSET = "a.medium='video' AND a.disposal IS NULL"


def region_filter(value, trash: bool) -> tuple[list[str], list[str]]:
    """产地筛选的条件与参数；回收站和空参数都返回空。

    产地和时长、画幅一样是内容条件，回收站不带。`none` 是「还没判过产地」那一批，
    它得能筛出来：判产地是件要人一条条过的活，没有这个入口就看不到还剩多少。
    """
    if trash or not value:
        return [], []
    picked = sorted({
        "" if item.strip().lower() == "none" else normalize_region(item)
        for item in str(value).split(",") if item.strip()
    })
    if not picked:
        return [], []
    return [f"{EFFECTIVE_REGION_SQL} IN ({','.join('?' * len(picked))})"], picked


def region_facet_rows(rows) -> list[dict]:
    """产地筛选项，连未判定那一档一起给。

    判产地是人一条条过的活，看不见还剩多少就没人会去过。未判定在筛选参数里写作
    `none`，空串到不了查询串里。
    """
    return [{**dict(row), "k": row["k"] or "none",
             "label": region_label(row["k"]) or "未判定"} for row in rows]


def _entity_asset_filter(connection, kind: str, name: str) -> tuple[str, list[int]]:
    """名称按资料页身份解析，作品范围包含旗下厂牌或事务所成员。"""
    from .web_entity import _tag_scope_visibility, scope_predicate

    entity = resolve_entity(connection, kind, name)
    if entity is None:
        return "0", []
    predicate = scope_predicate(kind, "scoped.entity_id") + _tag_scope_visibility(kind, "scoped.entity_id", "scoped.asset_id")
    return f"a.id IN (SELECT scoped.asset_id FROM asset_entity scoped WHERE {predicate})", [entity["id"]]


def catalog_filter(contract: WebContract, args):
    """视频列表与侧栏使用同一组筛选条件。"""
    trash = args.get("state") == "trash"
    # 普通馆藏仍是视频表面；回收站必须展示所有文件类型，否则从垃圾复核移入的
    # 图片、网址快捷方式等会变成不可见、不可恢复，只能被「清空回收站」直接删掉。
    where, par = ([] if trash else [VISIBLE_CATALOG_ASSET]), []
    from .web_folders import folder_filter
    folder_where, folder_params = folder_filter(contract, args)
    where.extend(folder_where)
    par.extend(folder_params)
    if args.get("library"):
        clause, values = media_libraries.predicate(settings_file.active(), args["library"])
        where.append(clause)
        par.extend(values)
    if trash:
        where.append("a.disposal='trash'")
    if args.get("loc"):
        locs = [x for x in args["loc"].split(",") if x]
        where.append("a.location IN (%s)" % ",".join("?" * len(locs))); par += locs
    subjects = [(kind, args[kind]) for kind in ("creator", "performer", "studio", "series", "agency")
                if args.get(kind)]
    if subjects:
        with contract.read_connection() as connection:
            for kind, name in subjects:
                clause, values = _entity_asset_filter(connection, kind, name)
                where.append(clause)
                par.extend(values)
    # 没有署名人的那批也是一类，和「某某的作品」并列，不是一句写在详情页上的说明。
    if args.get("owner") == "none":
        where.append(UNOWNED_ASSET_CLAUSE)
    if args.get("tag"):
        tags = [x for x in args["tag"].split(",") if x]
        tag_clause = (
            "(a.id IN (SELECT ae.asset_id FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "WHERE e.kind='tag' AND e.canonical_name=? UNION "
            "SELECT t.asset_id FROM asset_tag t WHERE t.tag=?) AND "
            + tag_not_hidden("a.id", "?") + ")"
        )
        if args.get("tag_match") == "any" and len(tags) > 1:
            where.append("(" + " OR ".join(tag_clause for _ in tags) + ")")
            for tag in tags:
                par.extend((tag, tag, normalize_entity_name(tag)))
        else:
            # 默认保持原有组合语义：逗号分隔的标签必须全部满足。
            for tag in tags:
                where.append(tag_clause)
                par.extend((tag, tag, normalize_entity_name(tag)))
    # 回收站是跨类型恢复入口。首页遗留的「只看有缩略图」、时长、画幅和 JAV
    # 条件只对视频成立，带进这里会再次把图片、网址快捷方式等资源藏起来。
    if not trash and args.get("len"):
        where.append("a.ctx_length = ?"); par.append(args["len"])
    if not trash and args.get("dur_min"):
        where.append("a.duration >= ?"); par.append(max(0, float(args["dur_min"])))
    if not trash and args.get("dur_max"):
        where.append("a.duration <= ?"); par.append(max(0, float(args["dur_max"])))
    if not trash and args.get("orient"):
        where.append("a.ctx_orient = ?"); par.append(args["orient"])
    elif not trash and args.get("exclude_vertical") == "1":
        where.append("(a.ctx_orient IS NULL OR a.ctx_orient <> '竖屏')")
    region_where, region_par = region_filter(args.get("region"), trash)
    where += region_where
    par += region_par
    if not trash and args.get("jav") == "1":
        # 只有番号形态还不够：JI-103 这类 creator clip 没有任何发行证据。
        where.append(JAV_ASSET_PREDICATE)
    if args.get("q"):
        query = args["q"].strip()
        if len(query) >= 3 and contract.has_fts():
            # 事务所名不在 FTS 索引里，也不该进去：`asset_search` 索引的是作品自己的
            # 文字和它名下的实体名，而事务所隔着一层成员关系。搜「Capsule Agency」
            # 要能出这家人的片，所以在这里并联一条成员关系的判据。
            where.append(
                "(a.id IN (SELECT asset_id FROM asset_search WHERE asset_search MATCH ?)"
                " OR " + AGENCY_SEARCH_CLAUSE + ")"
            )
            par.append('"' + query.replace('"', '""') + '"')
            par += [f"%{query}%"] * 2
        else:
            # 短查询走 LIKE，必须和 FTS 覆盖同样的身份写法：规范名、别名和检索词。
            # 只比 canonical_name 会让「凉森」搜不到 `涼森れむ`——trigram 要求三字
            # 起步，两字查询永远落在这条分支上，补检索词也救不了。
            # kind 也必须两条分支一致：FTS 的 entities 字段聚合的是这条作品名下的
            # 全部实体、不挑 kind，这里少认一种，同一个标签就会写三个字搜得到、
            # 写两个字搜不到，看着像搜索时灵时不灵。
            where.append(
                "((a.name LIKE ? OR a.catalog_title LIKE ? OR a.original_title LIKE ? "
                "OR a.code LIKE ? OR EXISTS("
                "SELECT 1 FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
                "WHERE ae.asset_id=a.id AND e.kind IN "
                "('creator','performer','studio','series','tag') "
                "AND (e.canonical_name LIKE ? "
                "OR EXISTS(SELECT 1 FROM entity_alias al WHERE al.entity_id=e.id "
                "AND al.alias LIKE ?) "
                "OR EXISTS(SELECT 1 FROM entity_search_term st WHERE st.entity_id=e.id "
                "AND st.term LIKE ?))))"
                " OR " + AGENCY_SEARCH_CLAUSE + ")"
            )
            pattern = f"%{query}%"
            par += [pattern] * 9
    state = state_predicate(str(args.get("state") or ""))
    if state:
        where.append(state)
    if not trash and args.get("thumb") == "1":
        # 已保存的关注条目只登记在线资产，不下载媒体或生成本地缩略图。
        # 它仍然是可筛选的真实资产；若沿用首页的缩略图门槛，来源 facet 会显示
        # 「在线 1」，点进去却永远是 0 条。
        where.append("(a.snapshot_path IS NOT NULL OR a.location = 'online')")

    return where, par


def seeded_order(seed) -> str:
    """按种子打散的 ORDER BY 片段，列名固定是 `a.id`。

    同一粒种子顺序固定，翻页不重不漏；换一粒就是「换一批」。拼进 SQL 的是算出来的
    整数，不是请求里的原文。"""
    sd = int(seed or 1) % 99991 or 7
    return f"((a.id * {sd}) % 99991), a.id"


def q_items(contract: WebContract, args):
    where, par = catalog_filter(contract, args)
    sort_key = str(args.get("sort") or "")
    direction = "ASC" if str(args.get("dir") or "").lower() == "asc" else "DESC"
    column = SORT_COLUMNS.get(sort_key)
    order = column.format(d=direction) if column else ("RANDOM()" if sort_key == "rand" else None)
    if order is None:
        if args.get("sort") == "seed":
            order = seeded_order(args.get("seed"))
        elif args.get("sort") == "daily" or not args.get("sort"):
            # 每日轮换：用当天日期做种子打散，同一天顺序固定，隔天自动换一批。
            # 不用 RANDOM() —— 那样每次刷新都不同，翻页还会重复/漏掉。
            seed = int(time.strftime("%Y%m%d")) % 9973 or 7
            order = f"((a.id * {seed}) % 99991), a.id"
        else:
            order = "a.id DESC"
    lim = max(1, min(int(args.get("limit", 60)), 200))
    off = max(0, int(args.get("offset", 0)))
    include_total = args.get("count", "1") != "0"
    fetch_limit = lim if include_total else lim + 1
    sql = ("SELECT a.id,a.location,a.path,a.name,a.catalog_title,a.original_title,a.medium,"
           "a.creator,a.studio,a.code,a.release_date,a.size,"
           "a.duration,a.width,a.height,a.ctx_length,a.ctx_orient,a.snapshot_path,"
           "a.play_count,a.leave_ratio,a.feedback,a.disposal,a.disposal_reason,a.rating,a.o_count,"
           "a.play_seconds,a.max_reached,a.seek_count,a.region,"
           f"{EFFECTIVE_REGION_SQL} AS effective_region,"
           "EXISTS(SELECT 1 FROM watch_queue w WHERE w.asset_id=a.id "
           f"AND w.profile_id='{DEFAULT_PROFILE_ID}') AS watch_later "
           "FROM asset a WHERE " + " AND ".join(where) + f" ORDER BY {order} LIMIT ? OFFSET ?")
    with contract.read_connection() as c:
        rows = [dict(r) for r in c.execute(sql, par + [fetch_limit, off])]
        has_more = len(rows) > lim if not include_total else None
        rows = rows[:lim]
        # 条数和体积同一次聚合出来：回收站卡片要说的是「清空能腾出多少」，
        # 为它单独再发一次请求只是把同一条 WHERE 又跑一遍。
        cnt, total_bytes, work_total = (
            c.execute("SELECT count(*),COALESCE(sum(a.size),0),count(DISTINCT "
                      + video_work_key(contract, c) + ") FROM asset a WHERE "
                      + " AND ".join(where), par).fetchone()
            if include_total else (None, None, None))
    # 卡片要显示出镜者和高权重标签，不能只有番号 —— 一次批量取，别 N+1
    if rows:
        ids = [r["id"] for r in rows]
        qm = ",".join("?" * len(ids))
        tmap: dict[int, list[str]] = {}
        for aid, tag in con_tags(contract, ids, qm):
            tmap.setdefault(aid, []).append(tag)
        emap: dict[int, dict[str, list[str]]] = {}
        performer_refs: dict[int, list[dict[str, object]]] = {}
        creator_refs: dict[int, dict[str, object]] = {}
        for aid, entity_id, kind, name in con_entities(contract, ids, qm):
            emap.setdefault(aid, {}).setdefault(kind, []).append(name)
            if kind == "performer":
                performer_refs.setdefault(aid, []).append(
                    entity_ref(contract, "performer", entity_id, name))
            elif kind == "creator" and aid not in creator_refs:
                creator_refs[aid] = entity_ref(contract, kind, entity_id, name)
        attach_creator_reference_details(contract, creator_refs.values())
        for r in rows:
            ts = tmap.get(r["id"], [])
            canonical = emap.get(r["id"], {})
            canonical_tags = canonical.get("tag", [])
            canonical_performers = canonical.get("performer", [])
            all_performers = canonical_performers or [
                tag[3:] for tag in ts if tag.startswith("演员:")
            ]
            performers = all_performers[:CARD_PERFORMERS]
            performer_names = {normalize_entity_name(name) for name in all_performers}
            visible_tags = canonical_tags or [t for t in ts if not t.startswith("演员:")]
            r["tags"] = [
                tag for tag in visible_tags
                if tag_cat(tag) in (
                    "general", "relationship", "role", "appearance", "scene", "story", "position",
                )
                and normalize_entity_name(tag) not in performer_names
            ][:4]
            r["performers"] = performers
            refs = performer_refs.get(r["id"], [])
            r["performer_entities"] = refs[:CARD_PERFORMERS]
            r["creator_entity"] = creator_refs.get(r["id"])
            if r["creator_entity"]:
                r["creator"] = r["creator_entity"]["name"]
            r["performer_total"] = len(refs) or len(all_performers)
            r["_entity_kinds"] = tuple(canonical)
    for r in rows:
        r["cost"] = cost_for(r['location'], r.get('path'))
        r["has_thumb"] = contract.has_snapshot(r["snapshot_path"])
        r['has_local_poster'] = (contract.poster_root / f"{r['id']}_4.jpg").is_file()
        attach_artwork_fields(contract, r)
        # 卡片上的出镜者称谓、规范番号、版本徽章与详情页使用同一份投影。
        attach_jav_display_fields(r, r.get("tags", ()), r.pop("_entity_kinds", ()))
        r.pop("snapshot_path", None)
        r.pop("path", None)                     # 路径不外发，串流走 id
    attach_saved_follow_cards(contract, rows)
    attach_multipart_groups(contract, rows)
    attach_edition_groups(contract, rows)
    return {"total": cnt, "work_total": work_total, "bytes": total_bytes, "items": rows, "has_more": has_more}

def con_tags(contract: WebContract, ids, qm):
    with contract.read_connection() as c:
        rows = c.execute(
            f"SELECT asset_id, tag FROM asset_tag WHERE asset_id IN ({qm})", ids).fetchall()
        return visible_identity_data(c, [], rows, tag_column=1)[1]


def visible_identity_data(connection, canonical, legacy, *, tag_column=0):
    """身份与兼容演员标签共用展示范围，来源记录保持完整。"""
    excluded = entities.male_performer_ids(connection)
    if not excluded:
        return canonical, legacy
    marks = ','.join('?' for _ in excluded)
    names = connection.execute(
        f'SELECT canonical_name FROM entity WHERE id IN ({marks}) UNION ALL '
        f'SELECT alias FROM entity_alias WHERE entity_id IN ({marks})', (*excluded, *excluded))
    hidden = {normalize_entity_name(row[0]) for row in names}
    canonical = [row for row in canonical if row[0] not in excluded]
    legacy = [row for row in legacy if not row[tag_column].startswith('演员:')
              or normalize_entity_name(row[tag_column][3:]) not in hidden]
    return canonical, legacy


def con_entities(contract: WebContract, ids, qm):
    with contract.read_connection() as connection:
        excluded = entities.male_performer_ids(connection)
        rows = connection.execute(
            f"SELECT DISTINCT ae.asset_id,e.id,e.kind,e.canonical_name "
            f"FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            f"WHERE ae.asset_id IN ({qm}) "
            f"AND e.kind IN ('tag','performer','creator','studio','series') "
            f"ORDER BY ae.asset_id,e.kind,e.canonical_name", ids,
        ).fetchall()
        return [row for row in rows if row[1] not in excluded]


def entity_ref(contract: WebContract, kind: str, entity_id, name: str) -> dict:
    """身份引用：id、名字，加上「实体图取不取得到」和这张图的取景。

    卡片头像、详情页身份格和沉浸模式的署名圈都读这一份，所以标志跟着引用走，不由
    各处自己再问一遍。缺了它，这些位置只能无条件出 `<img>`、等 `/entity-image` 回
    404 再把图摘掉——一个作品详情页实测就是 5 个这样的 404，而那条响应不带缓存头。

    取景和标志是同一个道理，而且更急：读这份引用的位置全都拿不到第二份数据，没有
    它就只能几何居中。人脸落在画面顶上的那批（`focus.pct` 为 0）在这些圆框里正好
    被裁掉脑袋——出镜者那一排实测五个人有四个是这样。没装图就没有 sidecar，那时
    不去问盘。
    """
    has_image = contract.has_entity_image(kind, entity_id)
    ref = {"id": entity_id, "name": name, "has_image": has_image}
    if has_image:
        ref["image_version"] = contract.entity_image_version(kind, entity_id)
        ref["avatar_focus"] = contract.avatar_focus(kind, entity_id)
    if kind == 'creator':
        from .follow_identity import creator_avatar_refs
        urls = creator_avatar_refs(contract).get(entity_id, [])
        if urls:
            ref['source_avatar_url'] = urls[0]
            ref['source_avatar_fallbacks'] = urls[1:]
    return ref


def attach_avatar_availability(contract: WebContract, rows, key="rep",
                               flag="has_avatar"):
    """给带代表作 id 的行标上「`/avatar` 取不取得到」。

    判据要看接触印相在不在盘上，一次批量取路径——逐行查库就是 N+1，顶栏一次 30 行。
    「还没裁过但印相还在」算取得到：`/avatar` 按需生成，把这种也判成没有等于把点一下
    就有的头像永远关掉。

    发布账号缺少本人或出演职业依据（不过 `work_portrait_predicate`）时，代表作画面照样
    当头像，同时标 `avatar_stand_in`：那是代表作画面，不是账号本人。资料页、索引、搜索、
    口味榜与统计都从这里拿这个标志（ADR-0099）。
    """
    ids = sorted({int(row[key]) for row in rows if row.get(key)})
    paths: dict[int, str | None] = {}
    entity_ids = sorted({int(row.get('entity_id') or row.get('id')) for row in rows
                         if row.get('entity_id') or row.get('id')})
    accounts: set[int] = set()
    if ids:
        marks = ",".join("?" * len(ids))
        with contract.read_connection() as connection:
            paths = {row[0]: row[1] for row in connection.execute(
                f"SELECT id,snapshot_path FROM asset WHERE id IN ({marks})", ids)}
            if entity_ids:
                entity_marks = ','.join('?' for _ in entity_ids)
                from .entity_classification import work_portrait_predicate
                accounts = {row[0] for row in connection.execute(
                    f"SELECT e.id FROM entity e WHERE e.id IN ({entity_marks}) AND NOT (" +
                    work_portrait_predicate(connection) + ")", entity_ids)}
    for row in rows:
        rep = row.get(key)
        entity_id = row.get('entity_id') or row.get('id')
        row[flag] = bool(rep) and contract.has_avatar(rep, paths.get(int(rep)))
        row.pop('avatar_stand_in', None)
        if row[flag] and entity_id in accounts:
            row['avatar_stand_in'] = True


def attach_card_performers(contract: WebContract, rows):
    """给各类视频卡片补同一份表演者资料，避免首页/相关/复核卡片各说各话。"""
    if not rows:
        return
    attach_saved_follow_cards(contract, rows)
    ids = [row["id"] for row in rows]
    qm = ",".join("?" * len(ids))
    names: dict[int, list[str]] = {}
    refs: dict[int, list[dict[str, object]]] = {}
    creator_refs: dict[int, dict[str, object]] = {}
    for asset_id, entity_id, kind, name in con_entities(contract, ids, qm):
        if kind == "creator" and asset_id not in creator_refs:
            creator_refs[asset_id] = entity_ref(contract, kind, entity_id, name)
        if kind != "performer":
            continue
        names.setdefault(asset_id, []).append(name)
        refs.setdefault(asset_id, []).append(
            entity_ref(contract, "performer", entity_id, name))
    attach_creator_reference_details(contract, creator_refs.values())
    for row in rows:
        row["performers"] = names.get(row["id"], [])[:CARD_PERFORMERS]
        row["performer_entities"] = refs.get(row["id"], [])[:CARD_PERFORMERS]
        row["creator_entity"] = creator_refs.get(row["id"])
        if row["creator_entity"]:
            row["creator"] = row["creator_entity"]["name"]
        row["performer_total"] = len(names.get(row["id"], []))


def attach_creator_reference_details(contract: WebContract, refs):
    """署名引用携带可信角色与资料页同源的代表作头像，按实体批量取数。"""
    refs = list(refs)
    ids = sorted({ref['id'] for ref in refs})
    if not ids:
        return
    from .entity_classification import summaries
    with contract.read_connection() as connection:
        roles = summaries(connection, ids)
        portraits = [dict(row) for row in connection.execute(
            "SELECT e.id,(SELECT a.id FROM asset_entity ae CROSS JOIN asset a ON a.id=ae.asset_id "
            "WHERE ae.entity_id=e.id AND a.medium='video' AND a.snapshot_path IS NOT NULL "
            "ORDER BY a.size DESC LIMIT 1) rep FROM entity e "
            f"WHERE e.id IN ({','.join('?' for _ in ids)})", ids)]
    attach_avatar_availability(contract, portraits)
    by_id = {row['id']: row for row in portraits}
    for ref in refs:
        categories = roles.get(ref['id'], {}).get('identity_categories', [])
        if categories:
            ref['identity_categories'] = categories
        portrait = by_id.get(ref['id'], {})
        if portrait.get('has_avatar'):
            ref.update(rep=portrait['rep'], has_avatar=True)
            if portrait.get('avatar_stand_in'):
                ref['avatar_stand_in'] = True


def attach_saved_follow_cards(contract: WebContract, rows):
    """已保存在线卡片使用关注来源的封面与内容标签。"""
    from .follow_store import FollowStore
    from .web_follow import _item_tags, _thumb_url

    ids = [row["id"] for row in rows if row.get("location") == "online"]
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    with contract.read_connection() as connection:
        linked = connection.execute(
            FollowStore._SELECT + f" WHERE i.asset_id IN ({marks}) ORDER BY i.id", ids,
        ).fetchall()
    by_asset = {}
    for record in linked:
        item = FollowStore._row(record)
        by_asset.setdefault(item.asset_id, item)
    for row in rows:
        item = by_asset.get(row["id"])
        if item is None or row.get("location") != "online":
            continue
        row["follow_item_id"] = item.id
        row["follow_thumb_url"] = _thumb_url(item)
        row["follow_tags"] = _item_tags(item)


#: 版次的展示次序：正片在前，处理过的在后。这不是偏好排序，只是要一个稳定的
#: 锚点——卡片标题取第一个，次序一变卡片就换名字。
EDITION_ORDER = ("有码", "中字", "无码", "无码破解")


def _edition_label(row: dict, tags: tuple[str, ...] = ()) -> str:
    """这一份是哪个版次。

    判据只有 `jav_edition_badges` 一份。自己按文件名再写一套「像不像无码」的判断，
    会和卡片上已经显示的徽章各说各话——同一个文件，角标写着无码、分组却当它是正片。
    """
    badges = jav_edition_badges(row.get('name'), row.get('code'), tags)
    return str(badges[0]) if badges else "有码"


def _edition_rows(contract: WebContract, codes) -> list[dict]:
    keys = {normalise_code_key(code) for code in codes if str(code or '').strip()}
    return [row for row in _catalog_group_rows(contract)
            if normalise_code_key(row.get('code')) in keys and row.get('disposal') is None]


def _catalog_group_rows(contract: WebContract) -> list[dict]:
    """规范番号、版次、探测规格共用一次全库只读快照。"""
    return contract.cached_until_changed('catalog-group-rows', lambda: _read_group_rows(contract),
                                        tables=GROUP_TABLES)


def _read_group_rows(contract: WebContract) -> list[dict]:
    with contract.read_connection() as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT id,name,path,code,size,duration,width,height,disposal,catalog_title,field_owners FROM asset "
            "WHERE medium='video' "
            "AND COALESCE(disposal,'')<>'vanished' ORDER BY id",
        )]
        if not rows:
            return []
        # 版次判据同时看文件名和标签（`无码` 可能只登记在标签上），所以标签要一起取。
        tags: dict[int, list[str]] = {}
        for row in connection.execute(
                "SELECT ae.asset_id aid, e.canonical_name name FROM asset_entity ae "
                "JOIN entity e ON e.id=ae.entity_id "
                "WHERE e.kind='tag'"):
            tags.setdefault(int(row["aid"]), []).append(str(row["name"]))
    coded = []
    for row in rows:
        row['raw_code'] = row['code']
        row['code'] = row['code'] or _filename_fc2_code(row['name'])
        if not row['code']:
            continue
        row["edition"] = _edition_label(row, tuple(tags.get(int(row["id"]), ())))
        coded.append(row)
    return coded


def _edition_groups(contract: WebContract, codes) -> dict[str, list[dict]]:
    """播放版本按规范番号归组，分卷属于各自的版本。"""
    keys = {normalise_code_key(code) for code in codes}
    return {code: group for code, group in contract.cached_until_changed(
        'catalog-edition-groups', lambda: _derive_edition_groups(contract),
        tables=GROUP_TABLES).items() if code in keys}


def _derive_edition_groups(contract: WebContract) -> dict[str, list[dict]]:
    candidates: dict[str, list[dict]] = {}
    for row in _catalog_group_rows(contract):
        if row.get('disposal') is None:
            candidates.setdefault(normalise_code_key(row.get("code")), []).append(row)
    groups: dict[str, list[dict]] = {}
    for code, items in candidates.items():
        if len(items) < 2:
            continue
        versions = partition_versions(items)
        if not foldable_versions(versions):
            continue
        versions.sort(key=lambda version: (
            EDITION_ORDER.index(version['signature'][0])
            if version['signature'][0] in EDITION_ORDER else len(EDITION_ORDER), version['id']))
        labels = version_labels(versions)
        groups[code] = [dict(row, version_id=version['id'], version_label=label,
                             version_multipart=version['multipart'])
                        for version, label in zip(versions, labels) for row in version['items']]
    return groups


def attach_edition_groups(contract: WebContract, rows) -> None:
    """给列表卡挂上同番号的版次组，不写账本。

    用户实测：`ABF-234` 与 `ABF-234 UN` 是两张并排的卡，`ABF-216` 也是。它们是同一部
    片的两个版次，占两个格子只是让人多滚一屏，还得自己认哪个是无码。
    """
    if not rows:
        return
    groups = _edition_groups(contract, [row.get("code") or _filename_fc2_code(row.get('name')) for row in rows])
    for row in rows:
        code = normalise_code_key(row.get("code") or _filename_fc2_code(row.get('name')))
        group = groups.get(code)
        if not group or not any(item["id"] == row["id"] for item in group):
            continue
        row["edition_group"] = {
            "key": code,
            "title": code or str(row.get("code") or "多版本作品"),
            "count": len({item['version_id'] for item in group}),
            "seed_id": group[0]["id"],
            "item_ids": [item["id"] for item in group],
            "editions": list(dict.fromkeys(item['version_label'] for item in group)),
        }


def q_editions(contract: WebContract, args):
    """按版次次序返回同一番号的全部版本。"""
    asset_id = int(args["id"])
    with contract.read_connection() as connection:
        seed = connection.execute(
            "SELECT id,code,name FROM asset WHERE id=? AND medium='video' "
            "AND disposal IS NULL",
            (asset_id,),
        ).fetchone()
    if not seed:
        return {"error": "edition group not found"}
    code = normalise_code_key(seed['code'] or _filename_fc2_code(seed['name']))
    group = _edition_groups(contract, [code]).get(code, [])
    if not group or not any(item["id"] == asset_id for item in group):
        return {"error": "edition group not found"}
    items = []
    for row in group:
        item = q_item(contract, row["id"])
        item["edition_label"] = row['version_label']
        item['version_id'] = row['version_id']
        parts = [member for member in group if member['version_id'] == row['version_id']]
        if row['version_multipart']:
            item['part_label'] = _part_labels(parts)[row['id']]
        items.append(item)
    return {"title": code or str(seed["code"]),
            "count": len({row['version_id'] for row in group}), "items": items}


def multipart_groups(contract: WebContract) -> dict[str, list[dict]]:
    """全库的分卷作品：规范番号 → 按播放次序排好的各卷。

    作品计数（`video_work_key`）、卡片折叠（`attach_multipart_groups`）、分卷队列
    （`q_parts`）与相关推荐去重都读这一份，判据只此一处。范围与作品计数的口径相同：
    进了回收站的卷仍属于这部作品，只有已消失的文件不算；卡片和队列只摆在库的那几卷
    （`_live_parts`）。裸名首卷没有标记，由 `ordered_multipart_items` 定夺。
    """
    def derive():
        candidates: dict[str, list[dict]] = {}
        for row in _catalog_group_rows(contract):
            code = normalise_code_key(row['code'])
            if code:
                candidates.setdefault(code, []).append(row)
        groups = {}
        for code, items in candidates.items():
            if len(items) < 2:
                continue
            versions = partition_versions(items)
            for version in versions:
                if version['multipart']:
                    key = code if len(versions) == 1 else f"{code}:{version['id']}"
                    groups[key] = version['items']
        return groups
    return contract.cached_until_changed("catalog-multipart-groups", derive, tables=GROUP_TABLES)


def _live_parts(group) -> list[dict]:
    """一套分卷里还在库的那几卷；只剩一卷时不再成套，卡片照单个文件摆。"""
    live = [item for item in group if item.get("disposal") is None]
    return live if len(live) > 1 else []


def video_work_key(contract: WebContract, connection) -> str:
    """分卷与版本按一部作品计数，其余文件各自计数。"""
    def derive():
        editions = _edition_groups(contract, [row['code'] for row in _catalog_group_rows(contract)])
        keys = {int(item['id']): f"edition:{int(group[0]['id'])}"
                for group in editions.values() for item in group}
        for group in multipart_groups(contract).values():
            for item in group:
                keys.setdefault(int(item['id']), f"multipart:{int(group[0]['id'])}")
        return keys
    keys = contract.cached_until_changed('catalog-video-work-key', derive, tables=GROUP_TABLES)
    connection.create_function('peach_video_work_key', 1,
                               lambda asset_id: keys.get(asset_id, f'asset:{asset_id}'),
                               deterministic=True)
    return 'peach_video_work_key(a.id)'


def attach_multipart_groups(contract: WebContract, rows) -> None:
    """Annotate list cards with one derived multipart release, without ledger writes."""
    if not rows:
        return
    groups = _parts_by_asset(contract)
    for row in rows:
        key, all_parts = groups.get(int(row['id']), ('', []))
        code = normalise_code_key(all_parts[0]['code']) if all_parts else ''
        group = _live_parts(all_parts)
        if not any(item["id"] == row["id"] for item in group):
            continue
        row["part_group"] = {
            "key": key,
            "title": code or str(row.get("code") or "分卷作品"),
            "count": len(group),
            "seed_id": group[0]["id"],
            "item_ids": [item["id"] for item in group],
            "total_duration": sum(float(item.get("duration") or 0) for item in group),
            "total_size": sum(int(item.get("size") or 0) for item in group),
        }


def q_parts(contract: WebContract, args):
    """Return an explicitly marked multipart release in playback order."""
    asset_id = int(args["id"])
    with contract.read_connection() as connection:
        seed = connection.execute(
            "SELECT id,code FROM asset WHERE id=? AND medium='video' "
            "AND disposal IS NULL",
            (asset_id,),
        ).fetchone()
    if not seed:
        return {"error": "multipart release not found"}
    group = _parts_by_asset(contract).get(asset_id, ('', []))[1]
    code = normalise_code_key(group[0]['code']) if group else ''
    live = {item["id"] for item in _live_parts(group)}
    if asset_id not in live:
        return {"error": "multipart release not found"}
    items = []
    labels = _part_labels(group)
    for row in group:
        if row['id'] not in live:
            continue
        item = q_item(contract, row['id'])
        item['part_label'] = labels[row['id']]
        items.append(item)
    return {"title": code or str(seed["code"]), "count": len(items), "items": items}


def _parts_by_asset(contract: WebContract) -> dict[int, tuple[str, list[dict]]]:
    return contract.cached_until_changed('catalog-parts-by-asset', lambda: {
        int(row['id']): (key, group) for key, group in multipart_groups(contract).items() for row in group},
        tables=GROUP_TABLES)


def _part_labels(group: list[dict]) -> dict[int, str]:
    # 卷号后面还挂着版次或修复标记时（`PPT-018-1-uncensored.mp4`），剥掉组内共有的
    # 那段尾缀才取得到卷标；这一步和分组用的是同一个判据。卷标按整套算，进了回收站
    # 的那一卷只是不进队列，后面几卷的卷标不跟着前移。
    stripped = names_without_shared_part_tail(group) or [""] * len(group)
    has_bonus = any(fc2_collection_label(row).startswith("特典 ") for row in group)
    labels = {}
    for position, (row, bare) in enumerate(zip(group, stripped), 1):
        marker = part_marker(bare) or part_marker(str(row.get("name") or ""))
        # 裸名首卷没有标记，卷标按队列位置给；有标记时沿用文件名里的写法。
        label = fc2_collection_label(row)
        labels[row['id']] = (label if has_bonus and label else
                              (marker.upper() if marker.isalpha() else marker) or str(position))
    return labels

def label_makers(contract: WebContract, c, aid) -> dict[int, dict]:
    """这部作品上哪些厂牌是某家片商旗下的 label（ADR-0049），键是 label 的实体 id。

    值是要并进那条厂牌引用的 `{"makers": [...]}`：详情里厂牌组旁多一组「片商」，只是
    链接，作品仍挂在 label 上。上级可以一级套一级（ADR-0051），列表从近到远排，
    素人ホイホイpower 的作品列出「素人ホイホイ、妄想族」。深度上限只防坏数据成环。
    """
    chains: dict[int, dict] = {}
    for label_id, maker_id, name in c.execute(
            "WITH RECURSIVE up(label_id,maker_id,depth) AS ("
            "SELECT lm.label_id,lm.maker_id,1 FROM label_maker lm WHERE lm.label_id IN "
            "(SELECT entity_id FROM asset_entity WHERE asset_id=?) "
            "UNION ALL SELECT up.label_id,lm.maker_id,up.depth+1 FROM up "
            "JOIN label_maker lm ON lm.label_id=up.maker_id WHERE up.depth<8) "
            "SELECT up.label_id,e.id,e.canonical_name FROM up JOIN entity e ON e.id=up.maker_id "
            "ORDER BY up.label_id,up.depth", (aid,)):
        makers = chains.setdefault(label_id, {"makers": []})["makers"]
        if all(maker["id"] != maker_id for maker in makers):
            makers.append({"id": maker_id, "name": name, "has_logo": contract.has_logo(name),
                           "logo_version": contract.logo_version(name)})
    return chains


def q_item(contract: WebContract, aid):
    """按 id 直取。
    ⚠️ 没有这个接口时，前端只能用「带筛选条件再查一遍然后 find」的绕法：
       limit 被覆盖成 1 → find 必然失败 → 走兜底 items[0]，
       于是**每次点击都打开同一个默认列表首项**（一个 12.6 GB 的 PikPak 文件），
       既显示错条目，又反复拉计费流量。按 id 取就按 id 取。"""
    with contract.read_connection() as c:
        r = c.execute(
            "SELECT id,location,path,name,medium,catalog_title,original_title,creator,studio,code,"
            "release_date,size,duration,width,height,field_owners,mutation_revision,"
            "ctx_length,ctx_orient,snapshot_path,play_count,leave_ratio,feedback,disposal,disposal_reason,"
            "rating,o_count,play_seconds,max_reached,seek_count,region,"
            f"{effective_region_sql('asset')} AS effective_region,"
            "COALESCE((SELECT p.liked FROM asset_preference p WHERE p.asset_id=asset.id "
            f"AND p.profile_id='{DEFAULT_PROFILE_ID}'),0) AS liked,"
            "COALESCE((SELECT p.reason FROM asset_preference p WHERE p.asset_id=asset.id "
            f"AND p.profile_id='{DEFAULT_PROFILE_ID}'),'') AS like_reason,"
            "COALESCE((SELECT g.wanted FROM asset_quality_goal g WHERE g.asset_id=asset.id "
            f"AND g.profile_id='{DEFAULT_PROFILE_ID}'),0) AS better_version,"
            "COALESCE((SELECT g.reason FROM asset_quality_goal g WHERE g.asset_id=asset.id "
            f"AND g.profile_id='{DEFAULT_PROFILE_ID}'),'') AS better_version_reason,"
            "EXISTS(SELECT 1 FROM watch_queue w WHERE w.asset_id=asset.id "
            f"AND w.profile_id='{DEFAULT_PROFILE_ID}') AS watch_later FROM asset WHERE id=?", (aid,)).fetchone()
        if not r:
            return {"error": "not found"}
        d = dict(r)
        d["sample_images"] = sample_images.detail_refs(c, d.get("code"))
        # 真相字段的字段级归属随详情一起下发：页面要显示某个值是谁写的、还能不能
        # 被自动写入者改，`mutation_revision` 则是编辑时回传的乐观并发凭据。
        d["field_owners"] = parse_owners(d.get("field_owners"))
        d["mutation_revision"] = int(d.get("mutation_revision") or 0)
        # 在线资产的 `path` 是来源作品页，不是可播地址；能播的那条代理在
        # `/follow-stream?id=<follow_item>`。保存时写的是 `follow_item.asset_id`，
        # 反查一次就能让馆藏详情自己播，不必先跳回关注页。
        if d.get("location") == "online":
            row = c.execute(
                "SELECT id FROM follow_item WHERE asset_id=? ORDER BY id LIMIT 1", (aid,),
            ).fetchone()
            d["follow_item_id"] = int(row[0]) if row else None
        legacy_rows = list(c.execute(
            "SELECT t.tag,t.source FROM asset_tag t WHERE t.asset_id=? AND "
            + tag_not_hidden("t.asset_id", "peach_normalize(t.tag)")
            + " ORDER BY t.tag", (aid,),
        ))
        canonical = list(c.execute(
            "SELECT DISTINCT e.id,e.kind,e.canonical_name FROM asset_entity ae "
            "JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id=? "
            "AND e.kind IN ('tag','performer','creator','studio','series') "
            "AND (e.kind<>'tag' OR "
            + tag_not_hidden("ae.asset_id", "e.normalized_name") + ") "
            "ORDER BY e.kind,e.canonical_name", (aid,),
        ))
        canonical, legacy_rows = visible_identity_data(c, canonical, legacy_rows)
        makers = label_makers(contract, c, aid)
    legacy = [row[0] for row in legacy_rows]
    official_tag_names = {
        normalize_entity_name(tag)
        for tag, source in legacy_rows
        if str(source or "").startswith("javinizer:")
        and str(source or "").endswith(":tag")
        and SOURCE_SPECS.get(str(source).split(":", 2)[1], None)
        and SOURCE_SPECS[str(source).split(":", 2)[1]].official
    }
    canonical_tags = [name for _, kind, name in canonical if kind == "tag"]
    canonical_performers = [name for _, kind, name in canonical if kind == "performer"]
    canonical_creators = {
        normalize_entity_name(name)
        for _, kind, name in canonical if kind == "creator"
    }
    performers = canonical_performers or [
        tag[3:] for tag in legacy
        if tag.startswith("演员:")
        and normalize_entity_name(tag[3:]) not in canonical_creators
    ]
    performer_names = {normalize_entity_name(name) for name in performers}
    tags = [tag for tag in (canonical_tags or [
        tag for tag in legacy if not tag.startswith("演员:")
    ]) if normalize_entity_name(tag) not in performer_names]
    d["tags"] = [
        {
            "k": tag,
            "cat": tag_cat(tag),
            "official": normalize_entity_name(tag) in official_tag_names,
        }
        for tag in tags if tag not in LENGTH_TAGS
    ]
    d["performers"] = performers
    d["entities"] = {
        kind: [name for _, item_kind, name in canonical if item_kind == kind]
        for kind in ("creator", "performer", "studio", "series")
    }
    d["entity_refs"] = {
        kind: [entity_ref(contract, kind, entity_id, name)
               for entity_id, item_kind, name in canonical if item_kind == kind]
        for kind in ("creator", "performer", "studio", "series")
    }
    attach_creator_reference_details(contract, d['entity_refs']['creator'])
    if d["entities"]["creator"]:
        d["creator"] = d["entities"]["creator"][0]
    if d["entities"]["studio"]:
        d["studio"] = d["entities"]["studio"][0]
    d["cost"] = cost_for(d['location'], d.get('path'))
    d["has_thumb"] = contract.has_snapshot(d["snapshot_path"])
    d['has_local_poster'] = (contract.poster_root / f"{d['id']}_4.jpg").is_file()
    attach_artwork_fields(contract, d)
    # 身份格的厂牌位和顶栏小圆片同一条判据：没装标识就不输出 `<img>`。规范厂牌走
    # `entity_refs`，非规范的那条只有扁平 `studio` 字段，两边都要有标志，否则
    # 后者会从「本来能取到图」退化成永远首字母。
    for ref in d["entity_refs"]["studio"]:
        ref["has_logo"] = contract.has_logo(ref["name"])
        ref["logo_version"] = contract.logo_version(ref["name"])
        ref.update(makers.get(ref["id"], {}))
    d["has_studio_logo"] = contract.has_logo(d.get("studio"))
    d["studio_logo_version"] = contract.logo_version(d.get("studio"))
    # 「女优」是番号发行物的行业称谓；creator clip 即使长得像番号也仍是普通内容。
    attach_jav_display_fields(
        d, [tag["k"] for tag in d["tags"]],
        [kind for kind, values in d["entities"].items() if values],
    )
    d.pop("snapshot_path", None); d.pop("path", None)
    attach_multipart_groups(contract, [d])
    attach_edition_groups(contract, [d])
    return d

def _one_card_per_release(contract: WebContract, source_code, rows: list[dict]) -> list[dict]:
    """一套分卷只留首卷一张卡；和正在看的这部同属一套的一张都不留。

    接着看按共享实体排序，同一部片的几卷实体完全一样，不收拢就会并排占满前几格。
    """
    groups = _parts_by_asset(contract)
    editions = _edition_groups(contract, [row.get('code') or _filename_fc2_code(row.get('name')) for row in rows])
    seen = set()
    source_key = normalise_code_key(source_code) if source_code else ""
    kept = []
    for row in rows:
        key = normalise_code_key(row.get('code') or _filename_fc2_code(row.get('name')))
        edition = editions.get(key, [])
        if any(item['id'] == row['id'] for item in edition):
            if key == source_key or key in seen:
                continue
            seen.add(key)
            kept.append(row)
            continue
        group = _live_parts(groups.get(int(row['id']), ('', []))[1])
        if any(item["id"] == row["id"] for item in group) and (
                key == source_key or row["id"] != group[0]["id"]):
            continue
        kept.append(row)
    return kept

def q_related(contract: WebContract, aid, limit=24):
    """接着看：IDF 抑制泛标签，MMR 避免近重复连续占满列表。"""
    from peach.related import rank_related

    with contract.read_connection() as c:
        source_row = c.execute(
            "SELECT id,code,name,duration,release_date FROM asset WHERE id=?", (aid,),
        ).fetchone()
        if not source_row:
            return {"items": []}
        source_entities = list(c.execute(
            "SELECT DISTINCT ae.entity_id,e.kind FROM asset_entity ae "
            "JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id=? "
            "AND e.kind IN ('creator','performer','series','studio','tag')", (aid,),
        ))
        if not source_entities:
            return {"items": []}
        COLS = ("id,location,path,name,catalog_title,original_title,creator,studio,code,size,duration,width,height,"
                "ctx_orient,snapshot_path,play_count,leave_ratio,feedback,disposal,o_count")
        source_entity_ids = [row[0] for row in source_entities]
        source_marks = ",".join("?" * len(source_entity_ids))
        candidate_rows = [dict(row) for row in c.execute(
            f"SELECT {COLS},a.release_date,a.region,"
            f"{EFFECTIVE_REGION_SQL} AS effective_region "
            "FROM asset a JOIN asset_entity shared "
            "ON shared.asset_id=a.id WHERE a.medium='video' AND a.id<>? "
            "AND (a.feedback IS NULL OR a.feedback<>'dislike') AND a.disposal IS NULL "
            f"AND shared.entity_id IN ({source_marks}) GROUP BY a.id "
            "ORDER BY count(DISTINCT shared.entity_id) DESC,a.id LIMIT 4000",
            (aid, *source_entity_ids),
        )]
        candidate_rows = _one_card_per_release(
            contract, source_row['code'] or _filename_fc2_code(source_row['name']), candidate_rows)
        ids = [aid, *(row["id"] for row in candidate_rows)]
        entities = {asset_id: {} for asset_id in ids}
        for offset in range(0, len(ids), 800):
            batch = ids[offset:offset + 800]
            qm = ",".join("?" * len(batch))
            for asset_id, entity_id, kind in c.execute(
                "SELECT ae.asset_id,ae.entity_id,e.kind FROM asset_entity ae "
                "JOIN entity e ON e.id=ae.entity_id "
                f"WHERE ae.asset_id IN ({qm}) AND e.kind IN "
                "('creator','performer','series','studio','tag')", tuple(batch),
            ):
                entities[asset_id].setdefault(kind, set()).add(entity_id)

        def rank_row(row):
            release_date = row.get("release_date") or ""
            return {
                **row, "entities": entities[row["id"]],
                "year": int(release_date[:4]) if release_date[:4].isdigit() else None,
            }

        source = rank_row(dict(source_row))
        picked = rank_related(source, (rank_row(row) for row in candidate_rows), limit,
                              seed=f"related:{aid}")
        for row in picked:
            row["_entity_kinds"] = tuple(row["entities"])
            row.pop("entities", None)
            row.pop("year", None)
    attach_card_performers(contract, picked)
    attach_multipart_groups(contract, picked)
    attach_edition_groups(contract, picked)
    related_tags: dict[int, list[str]] = {}
    if picked:
        related_ids = [row["id"] for row in picked]
        marks = ",".join("?" * len(related_ids))
        for asset_id, tag in con_tags(contract, related_ids, marks):
            related_tags.setdefault(asset_id, []).append(tag)
    for d in picked:
        d["cost"] = cost_for(d['location'], d.get('path'))
        d["has_thumb"] = contract.has_snapshot(d["snapshot_path"])
        d['has_local_poster'] = (contract.poster_root / f"{d['id']}_4.jpg").is_file()
        attach_artwork_fields(contract, d)
        attach_jav_display_fields(
            d, related_tags.get(d["id"], ()), d.pop("_entity_kinds", ()),
        )
        d.pop("release_date", None)
        d.pop("snapshot_path", None)
        d.pop("path", None)
    return {"items": picked[:limit]}

#: 一条资产实际算哪个产地，按三层依次取第一个有值的：资产自己判定的
#: `asset.region`、它名下厂牌或创作者实体的 `entity.region`、番号推断。
#:
#: 这段表达式是产地的唯一口径。页面上标的产地、筛选器筛出来的那批、JAV 页收不收
#: 一部片，全读它算出来的同一列——`regions.py` 讲了为什么推断不直接写进账本，而
#: 只要推断结果不进账本，它就必须在每次查询时算，那就只能有一处算法。
#:
#: 实体那一层按 `studio` 优先于 `creator`：一部片同时挂着厂牌和创作者时，发行体系
#: 由厂牌定——转载这部片的创作者可能是另一个国家的人。
def effective_region_sql(alias: str = "a") -> str:
    """产地表达式，`alias` 是查询里 `asset` 那张表的写法（详情页没起别名）。"""
    return (
        f"COALESCE(NULLIF(trim(COALESCE({alias}.region,'')),''),"
        "(SELECT NULLIF(trim(COALESCE(region_e.region,'')),'') "
        "FROM asset_entity region_ae JOIN entity region_e ON region_e.id=region_ae.entity_id "
        f"WHERE region_ae.asset_id={alias}.id AND region_e.kind IN ('studio','creator') "
        "AND NULLIF(trim(COALESCE(region_e.region,'')),'') IS NOT NULL "
        "ORDER BY CASE region_e.kind WHEN 'studio' THEN 0 ELSE 1 END LIMIT 1),"
        f"infer_region({alias}.code,{alias}.name),'')"
    )


EFFECTIVE_REGION_SQL = effective_region_sql()

#: JAV 语境下的资产过滤片段。历史缺连字符编号先规范化，但仍必须再有发行证据；
#: 否则 RAIKUN325 这类账号和 JI-103 creator clip 会混入。FC2 单独保留。
#:
#: 产地判过且不是日本的一概排除。韩国 MIB 与国产厂牌的番号和日本番号同形，下面那几条
#: 发行证据它们全都满足——`B:\MVP\MIB\` 那 381 条里已经有 115 条靠 k-mib 官网刮回来的
#: 厂牌和发行日把自己坐实成了 JAV。空串是「还没判过」，照旧按番号与发行证据收。
JAV_ASSET_PREDICATE = (
    "a.code IS NOT NULL AND a.code<>'' AND is_jav_code(normalise_code_key(a.code)) AND "
    f"{EFFECTIVE_REGION_SQL} IN ('','jp') AND ("
    "upper(trim(a.code)) LIKE 'FC2%' OR COALESCE(trim(a.studio),'')<>'' "
    "OR COALESCE(trim(a.release_date),'')<>'' OR EXISTS("
    "SELECT 1 FROM asset_entity jav_ae JOIN entity jav_e ON jav_e.id=jav_ae.entity_id "
    "WHERE jav_ae.asset_id=a.id AND jav_e.kind IN ('performer','studio','series')))"
)
JAV_ASSET_CLAUSE = "AND " + JAV_ASSET_PREDICATE + " "
#: 筛选项、侧栏读数与顶部三层排掉标「已消失」的行；回收站里的照旧算（`q_facets`、`q_tops`）。
PRESENT_ASSET_CLAUSE = "AND COALESCE(a.disposal,'')<>'vanished' "


#: 首页的状态筛选（新鲜 / 看过 / 已标记 / 稍后看）。作品列表、顶部三层和筛选面板
#: 必须用同一份口径：否则「已标记」页会列出全库的人物、厂牌和标签，点进去却是空的。
def state_predicate(state: str) -> str:
    """返回这个状态对应的 SQL 谓词（已加括号，不含 `AND`）；未知状态返回空串。"""
    if state == "fresh":
        return "((a.play_count IS NULL OR a.play_count=0) AND a.feedback IS NULL)"
    if state == "played":
        return "(a.play_count > 0)"
    if state == "flagged":
        return ("(COALESCE(a.o_count,0)>0 OR EXISTS(SELECT 1 FROM asset_preference p "
                f"WHERE p.asset_id=a.id AND p.profile_id='{DEFAULT_PROFILE_ID}' AND p.liked=1))")
    if state == "later":
        return ("(EXISTS(SELECT 1 FROM watch_queue w WHERE w.asset_id=a.id "
                f"AND w.profile_id='{DEFAULT_PROFILE_ID}'))")
    return ""


def state_clause(state: str) -> str:
    """同一个谓词的 `AND ...` 形式，给拼在 WHERE 后面的查询用。"""
    predicate = state_predicate(state)
    return f"AND {predicate} " if predicate else ""


#: 顶部三层的候选池相对展示位的倍数。严格取前 N 会让这一条永远是同一批人——
#: 「换一批」刷新后上面纹丝不动。放大候选池再按种子确定性抽样，既保持是常见身份，
#: 又能真的换一批。倍数太大就会开始出现只有一两部作品的冷门项。
TOPS_POOL_FACTOR = 4


def q_tops(contract: WebContract, n=28, jav=False, seed="", state="", page=0, filters=None):
    """顶部三层用的数据：女优圆头像 / 厂牌 / 内容标签。

    缓存的人物肖像由前端优先使用；缺失时才回退到代表作接触印相裁切。

    `state` 跟作品列表同一份口径：在「已标记」这类页面上，上面这排头像
    只应该出现真的有已标记作品的人，否则点进去是空的。

    `page` 给横滚续接用。第一页那 n 个是从数量前 `n * TOPS_POOL_FACTOR` 里抽的，
    续页要是再回到那一段就会把同一个人给两次，所以那一段归第一页独占，续页从它的
    末尾往后按数量数下去。库里六百多位女优，一排给到头才是「滚到底还能接着滚」。"""
    with contract.read_connection() as c:
        scope = PRESENT_ASSET_CLAUSE + (JAV_ASSET_CLAUSE if jav else "") + state_clause(state)
        folder_params = []
        if filters and filters.get('folder'):
            conditions, folder_params = catalog_filter(contract, filters)
            scope += ' AND ' + ' AND '.join(conditions)
        base = (
            "SELECT e.id,e.canonical_name,count(DISTINCT ae.asset_id) n,"
            "(SELECT a2.id FROM asset_entity ae2 JOIN asset a2 ON a2.id=ae2.asset_id "
            " WHERE ae2.entity_id=e.id AND a2.medium='video' AND a2.snapshot_path IS NOT NULL "
            " AND (e.kind<>'performer' OR " + solo_performer_clause("a2.id", "e.id") + ") "
            " ORDER BY (a2.play_count IS NULL),a2.size DESC LIMIT 1) rep "
            "FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "JOIN asset a ON a.id=ae.asset_id "
            "WHERE a.medium='video' AND e.kind=? " + scope +
            "GROUP BY e.id,e.canonical_name ORDER BY n DESC LIMIT ? OFFSET ?"
        )
        first_page_span = n * TOPS_POOL_FACTOR if seed else n

        def read(kind, limit, skip):
            return [{"id": entity_id, "k": k, "n": cnt, "rep": representative}
                    for entity_id, k, cnt, representative
                    in c.execute(base, (kind, *folder_params, limit, skip))]

        def pick(kind):
            """按数量取候选池，再按种子确定性抽样。种子为空时退回严格前 N。"""
            if page:
                return read(kind, n, first_page_span + (page - 1) * n)
            pool = read(kind, first_page_span, 0)
            if not seed or len(pool) <= n:
                return pool[:n]
            # 同一个种子必须给出同一批人：翻页和重绘之间不能抖动。
            digest = hashlib.blake2b(f"{seed}:{kind}".encode(), digest_size=8).digest()
            rng = random.Random(int.from_bytes(digest, "big"))
            chosen = rng.sample(pool, n)
            # 抽完仍按数量排序，免得常见身份被排到末尾。
            return sorted(chosen, key=lambda row: -row["n"])

        out = {}
        out["performers"] = pick("performer")
        out["studios"] = pick("studio")
    # 顶栏那排厂牌小圆片据此决定要不要输出 `<img>`：没装标识就直接首字母垫底。
    # 判定在库连接之外做，它读的是目录索引而不是账本。
    for studio in out["studios"]:
        studio["has_logo"] = contract.has_logo(studio["k"])
        studio["logo_version"] = contract.logo_version(studio["k"])
    # 女优那排同理，只是它有两级图：规范实体图优先，取不到才回落到代表作头像。
    # 两级都要标志，否则第一级空着的那些人会各打一个必然 404 的请求再回落。
    # 取景与索引页同一份 sidecar、同一个换算。这排圆框只有 64 px，框越小，同一张图
    # 能无损放大的余量越大，全身站姿照在这里才谈得上看清是谁。
    for performer in out["performers"]:
        performer["has_image"] = contract.has_entity_image("performer", performer["id"])
        performer["image_version"] = contract.entity_image_version("performer", performer["id"])
        performer["avatar_focus"] = contract.avatar_focus("performer", performer["id"])
    # 厂牌那排自己不出头像，但两排的 `rep` 都会进前端的 REP 表，卡片头像回落时读的
    # 就是它——所以两排都得判。
    attach_avatar_availability(contract, out["performers"] + out["studios"])
    return out




def q_facets(
    contract: WebContract,
    jav: bool = False,
    scope_kind: str = "",
    scope_name: str = "",
    asset_id: int | None = None,
    state: str = "",
    filters: dict | None = None,
):
    """返回当前浏览集合真正存在的筛选项。

    首页不带 scope，维持全库口径；实体资料页按规范实体收窄，详情页按单个作品收窄。
    筛选项必须来自和作品列表相同的规范关系，不能让前端拿全库 facets 猜当前页面。
    回收站里的作品照旧算进筛选项；标「已消失」的不算，它们不在任何列表里（ADR-0087）。
    """
    with contract.read_connection() as c:
        scope = (PRESENT_ASSET_CLAUSE + (JAV_ASSET_CLAUSE if jav else "")
                 + state_clause(state))
        scope_params: list[object] = []
        if filters and filters.get("library") and asset_id is None:
            library_clause, library_params = media_libraries.predicate(settings_file.active(), filters["library"])
            scope += "AND " + library_clause + " "
            scope_params.extend(library_params)
        if asset_id is not None:
            scope += "AND a.id=? "
            scope_params.append(int(asset_id))
        elif scope_kind or scope_name:
            if (scope_kind not in {"creator", "performer", "studio", "series", "agency", "tag"}
                    or not scope_name):
                raise ValueError("invalid facet scope")
            clause, values = _entity_asset_filter(c, scope_kind, scope_name)
            scope += "AND " + clause + " "
            scope_params.extend(values)
        out = {}
        out["locations"] = [dict(r) for r in c.execute(
            "SELECT a.location AS k, count(*) AS n, "
            "SUM(CASE WHEN a.play_count>0 THEN 1 ELSE 0 END) AS played "
            "FROM asset a WHERE a.medium='video' " + scope +
            "GROUP BY a.location ORDER BY n DESC", scope_params)]
        if filters is not None and asset_id is None:
            conditions, parameters = catalog_filter(contract, {**filters, "state": state})
            scope += "AND " + " AND ".join(conditions) + " "
            scope_params.extend(parameters)
        out["orientations"] = [dict(r) for r in c.execute(
            "SELECT a.ctx_orient AS k,count(*) AS n FROM asset a "
            "WHERE a.medium='video' AND a.ctx_orient IS NOT NULL AND a.ctx_orient<>'' " + scope +
            "GROUP BY a.ctx_orient ORDER BY n DESC", scope_params)]
        out["regions"] = region_facet_rows(c.execute(
            f"SELECT {EFFECTIVE_REGION_SQL} AS k,count(*) AS n FROM asset a "
            "WHERE a.medium='video' " + scope + "GROUP BY k ORDER BY n DESC",
            scope_params))
        out["creators"] = [dict(r) for r in c.execute(
            "SELECT e.canonical_name AS k,count(DISTINCT ae.asset_id) AS n "
            "FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "JOIN asset a ON a.id=ae.asset_id WHERE a.medium='video' AND e.kind='creator' " + scope +
        
            "GROUP BY e.id,e.canonical_name ORDER BY n DESC LIMIT 60", scope_params)]
        # 标签要分层 —— 一锅端会让「演员:一个ren」和「1080P」「足交」混在一起。
        # 三类分开：技术规格（画质/时长/画幅，筛选价值低）、内容维度（真正有用的）、演员（另立一栏）。
        rows = [dict(r) for r in c.execute(
            "SELECT e.canonical_name AS k, count(DISTINCT ae.asset_id) AS n "
            "FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "JOIN asset a ON a.id=ae.asset_id WHERE a.medium='video' AND e.kind='tag' " + scope +
            "AND " + tag_is_not_a_performer_name("e.normalized_name") + " "
            "GROUP BY e.id,e.canonical_name ORDER BY n DESC LIMIT 400", scope_params)]
        classified = [
            dict(r, cat=tag_cat(r["k"]))
            for r in rows
            if r["k"] not in LENGTH_TAGS
        ]
        # 标签条横着滚到底还要接着滚，侧栏那组摊开也读的是这一份：截到几十条，
        # 两处都在库里还剩一百多个标签时说「没有了」。
        out["tags"] = [r for r in classified if r["cat"] != "meta"][:200]
        out["tech"] = [r for r in classified if r["cat"] == "meta"][:16]
        out["tagperformers"] = [dict(r) for r in c.execute(
            "SELECT e.canonical_name AS k,count(DISTINCT ae.asset_id) AS n "
            "FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "JOIN asset a ON a.id=ae.asset_id WHERE a.medium='video' AND e.kind='performer' " + scope +
        
            "GROUP BY e.id,e.canonical_name ORDER BY n DESC LIMIT 20", scope_params)]
        st = c.execute(
            "SELECT count(*) total, COALESCE(sum(size),0) bytes, "
            "SUM(CASE WHEN duration IS NOT NULL THEN 1 ELSE 0 END) duration, "
            "SUM(CASE WHEN play_count>0 THEN 1 ELSE 0 END) played, "
            "SUM(CASE WHEN COALESCE(o_count,0)>0 OR EXISTS(SELECT 1 FROM asset_preference p "
            f"WHERE p.asset_id=a.id AND p.profile_id='{DEFAULT_PROFILE_ID}' AND p.liked=1) "
            "THEN 1 ELSE 0 END) flagged, "
            "SUM(EXISTS(SELECT 1 FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "WHERE ae.asset_id=a.id AND e.kind='creator')) attributed "
            "FROM asset a WHERE a.medium='video' AND a.disposal IS NULL "
            + scope, scope_params).fetchone()
        out["stats"] = dict(st)
        saved = [dict(row) for row in c.execute(
            "SELECT a.id,a.location FROM asset a WHERE a.medium='video' "
            "AND a.location='online' " + scope, scope_params)]
    attach_saved_follow_cards(contract, saved)
    follow_counts: dict[str, int] = {}
    for item in saved:
        for tag in set(item.get("follow_tags", [])):
            follow_counts[tag] = follow_counts.get(tag, 0) + 1
    out["follow_tags"] = [{"k": tag, "n": count} for tag, count in
                          sorted(follow_counts.items(), key=lambda row: (-row[1], row[0]))[:30]]
    return out

# ────────────────────────────── 写入 ──────────────────────────────

def w_item_tag(contract: WebContract, body):
    """新增或隐藏单条资源标签；隐藏不销毁刮削/识别来源证据。"""
    contract.cache_bust()
    aid = int(body["id"])
    operation = str(body.get("operation", "")).strip()
    tag = str(body.get("tag", "")).strip()
    if operation not in {"add", "remove"}:
        raise ValueError("operation must be add or remove")
    if not tag or len(tag) > 80 or tag.startswith("演员:"):
        raise ValueError("tag must be 1 to 80 characters and cannot be a performer marker")
    if operation == "add":
        canonical = current_tags([tag])
        if not canonical:
            raise ValueError("该标签不在当前词表中")
        tag = canonical[0]
    normalized = normalize_entity_name(tag)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # 事务边界只有一个入口：write_transaction 自己取 database.write_lock 并在异常时回滚。
    # 这里不手取那把锁再手动 commit/close：任何 execute 抛出都会漏掉回滚和关闭。
    # 那把锁不可重入，所以外层不能再套一个取同一把锁的 with，否则自死锁。
    with contract.write_transaction() as c:
        if not c.execute("SELECT 1 FROM asset WHERE id=?", (aid,)).fetchone():
            raise ValueError("asset not found")
        if operation == "remove":
            c.execute(
                "INSERT INTO asset_tag_preference(profile_id,asset_id,normalized_tag,hidden,updated_at) "
                f"VALUES('{DEFAULT_PROFILE_ID}',?,?,1,?) ON CONFLICT(profile_id,asset_id,normalized_tag) "
                "DO UPDATE SET hidden=1,updated_at=excluded.updated_at",
                (aid, normalized, stamp),
            )
        else:
            c.execute(
                f"DELETE FROM asset_tag_preference WHERE profile_id='{DEFAULT_PROFILE_ID}' "
                "AND asset_id=? AND normalized_tag=?", (aid, normalized),
            )
            c.execute(
                "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) "
                "VALUES(?,?,1.0,'web-user')", (aid, tag),
            )
            upsert_asset_entity(
                c, kind="tag", name=tag, asset_id=aid, role="tag",
                source="web-user", confidence=1.0,
                metadata={"profile_id": DEFAULT_PROFILE_ID}, now=stamp,
            )
    return {"ok": True, "operation": operation, "tag": tag,
            "tags": [item["k"] for item in q_item(contract, aid)["tags"]]}
