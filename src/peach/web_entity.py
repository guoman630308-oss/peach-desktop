"""实体资料页：女优／厂牌／创作者／系列的聚合，以及它们的图集与索引页。

实体是 ledger 的规范身份（见 AGENTS.md 术语表），扁平的 `asset_tag`、creator/studio
字段只是兼容投影。这一域负责把两者合成一页：别名归并、代表作、图集分组、索引分类。

它 import `web_catalog` 的可见性谓词，而不是自己写一份——女优页上该不该出现某个标签，
必须和首页是同一个判据。
"""
from __future__ import annotations

import json
import re
import time

from urllib.parse import urlsplit

from . import (company_profiles, entry_links, feeds, link_status, performer_header, web_feeds,
               web_follow)
from .catalog_rules import (LENGTH_TAGS, dir_expr, photo_set_title, solo_performer_clause, tag_cat)
from .entities import normalize_entity_name, resolve_entity, resolve_entity_id, rewrite_flat_projection
from .social_links import ARCHIVE_HOSTS, is_archive
from .media_sources import cost_for
from .web_catalog import (
    JAV_ASSET_PREDICATE,
    VISIBLE_CATALOG_ASSET,
    attach_avatar_availability,
    seeded_order,
    tag_is_not_a_performer_name,
    tag_not_hidden,
    video_work_key,
)
from .web_state import WebContract
from . import entity_classification


#: 有资料页的实体种类。事务所（agency）和厂牌是两件事：厂牌出片、事务所出人，
#: 一位女优可以在同一年里给多个厂牌拍片而只属于一家事务所。
PROFILE_KINDS = {"performer", "studio", "creator", "series", "agency"}
READ_ENTITY_KINDS = PROFILE_KINDS | {"tag"}


def _query_entity_id(args) -> int | None:
    """显式 ID 使用正整数，名称查询不占用数字名称的契约。"""
    if "id" not in args:
        return None
    value = str(args["id"])
    if not value.isascii() or not value.isdecimal() or len(value) > 19 or not 0 < int(value) < 2**63:
        raise ValueError("invalid entity id")
    return int(value)


def _read_entity_query(args):
    """资料与图集共用实体种类、名称和显式 ID 的查询契约。"""
    kind, name = args.get("kind", ""), args.get("name", "")
    try:
        entity_id = _query_entity_id(args)
    except ValueError:
        return {"error": "invalid entity"}
    if kind not in READ_ENTITY_KINDS or (entity_id is None and not name):
        return {"error": "invalid entity"}
    return kind, name, entity_id


def _query_entity(connection, kind: str, name: str, entity_id: int | None):
    if entity_id is None:
        return resolve_entity(connection, kind, name)
    target = resolve_entity_id(connection, entity_id)
    return connection.execute("SELECT * FROM entity WHERE id=? AND kind=?",
                              (target, kind)).fetchone() if target else None


def entity_id_redirect(connection, kind: str, entity_id: int) -> dict | None:
    """跨种类身份合并由墓碑来源证明原种类，现存 ID 始终使用自身身份。"""
    row = connection.execute(
        "SELECT e.id,e.kind,e.canonical_name FROM entity_redirect r JOIN entity e ON e.id=r.target_id "
        "WHERE r.old_id=? AND r.source=? AND e.kind<>? "
        "AND NOT EXISTS(SELECT 1 FROM entity live WHERE live.id=r.old_id)",
        (entity_id, f'user:identity-merge:{kind}', kind),
    ).fetchone()
    if row is None or row['kind'] not in READ_ENTITY_KINDS:
        return None
    return {'id': row['id'], 'kind': row['kind'], 'name': row['canonical_name']}


def _missing_entity_result(connection, kind: str, name: str, entity_id: int | None):
    """缺失身份仅按合并来源证明返回最终资料，其余保持不存在。"""
    if entity_id is not None:
        redirect = entity_id_redirect(connection, kind, entity_id)
        return {'redirect': redirect} if redirect else {"error": "not found"}
    merged = connection.execute(
        "SELECT DISTINCT e.id,e.kind,e.canonical_name FROM entity e "
        "JOIN entity_alias al ON al.entity_id=e.id "
        "JOIN entity_redirect r ON r.target_id=e.id AND r.source=al.source "
        "WHERE al.alias=? AND al.source=? ORDER BY e.id LIMIT 2",
        (name, f'user:identity-merge:{kind}'),
    ).fetchall()
    if len(merged) == 1:
        return {'redirect': {'id': merged[0]['id'], 'kind': merged[0]['kind'],
                             'name': merged[0]['canonical_name']}}
    return {"error": "not found"}


#: 资料页与索引页数作品的口径：视频，且没有标「已消失」（ADR-0087）。回收站里的照旧算；
#: 代表作的取图不套这一条。
COUNTED_VIDEO = "a.medium='video' AND COALESCE(a.disposal,'')<>'vanished'"


def scope_predicate(kind: str, column: str, subject: str = "?") -> str:
    """这一页的作品挂在谁名下的 SQL 判据，占位符恒为一个。

    事务所自己不挂作品——作品是它的成员拍的，`asset_entity` 里没有它的行。把范围
    从「这个 id」换成「这组 id」之后，事务所页和女优页共用同一批统计、标签和图集
    查询，而不是各写一份再慢慢漂移。

    `subject` 默认是占位符，索引页那种「一句 SQL 里每行一个实体」的写法传列名
    （`e.id`）。判据只有这一份，索引页和资料页数出来的作品数才不会各算各的。

    片商的范围是它自己加上 `label_maker` 往下的每一级（ADR-0051 修订）：旗下 label
    出的片都算在它名下，和事务所算成员的片是同一个道理。`UNION` 去重，账本里万一
    有环也会停下来。

    这两种范围是 `IN` 子查询，规划器估不出它有几行。和 `asset` 上的 `medium` 过滤
    同句时，真实账本的统计会让它从 `asset` 那侧按 `medium` 起步，再逐行回查
    `asset_entity`，一家事务所的图集要扫五万张图。所以这类查询用 `CROSS JOIN` 把
    `asset_entity` 钉在外层，从范围出发走 `entity_id` 索引；`=` 那一种本来就这样走。
    """
    if kind == "agency":
        return (f"{column} IN (SELECT member_id FROM entity_membership"
                f" WHERE agency_id={subject})")
    if kind == "studio":
        return (f"{column} IN (WITH RECURSIVE down(id) AS (SELECT {subject}"
                " UNION SELECT lm.label_id FROM label_maker lm JOIN down ON lm.maker_id=down.id)"
                " SELECT id FROM down)")
    return f"{column}={subject}"


def _tag_scope_visibility(kind: str, entity_column: str, asset_column: str) -> str:
    """标签资料的资产范围遵守当前用户的隐藏选择。"""
    if kind != "tag":
        return ""
    normalized = f"(SELECT normalized_name FROM entity WHERE id={entity_column})"
    return " AND " + tag_not_hidden(asset_column, normalized)


#: 索引页地址 → `entity.kind`。有资料页就该有索引页：只靠女优页上那个名字进事务所页的话，
#: 名下没有关系的几十家等于只能靠猜地址。标签不在这里，它走另一条分支。
INDEX_ENTITY_KINDS = {"performers": "performer", "creators": "creator",
                      "studios": "studio", "agencies": "agency"}


def _performer_entries(contract: WebContract, c, d: dict, alias_rows) -> dict:
    """女优资料卡上的外部入口，和「订阅新作」开关。

    入口下发的是拼好的地址，不是模板：拼它要的站点 id、规范名和别名都在服务端，
    前端再拼一遍就会有两份规则。缺 id 的站点不出现在这个列表里。开关只在她有
    JavDB 演员页时出现，订阅地址同样由服务端拼，页面只管开和关。
    """
    metadata = d.get("metadata") or {}
    localization = metadata.get("name_localization") if isinstance(metadata, dict) else None
    out = {"entry_links": entry_links.entry_links(
        contract.entry_links_root, d["canonical_name"], d["external_refs"], alias_rows,
        localization)}
    if entry_links.javdb_actor_pages(d["canonical_name"], d["external_refs"]):
        out["feed"] = {"following": feeds.entity_following(c, d["id"])}
    return out


def label_layer(contract: WebContract, c, kind: str, entity_id: int) -> tuple[dict | None, list[dict]]:
    """厂牌资料页的 label 一层（ADR-0051）：它归哪家片商，和它旗下有哪些 label。

    旗下那批是片商页的名册，和事务所页的艺人同一个形状（`id / k / n`），格子是同一个
    组件；`n` 是这个 label 连同它自己的下级一共多少视频，和它资料页上那个数一致。
    """
    if kind != "studio":
        return None, []
    maker = c.execute(
        "SELECT e.id,e.canonical_name name FROM label_maker lm "
        "JOIN entity e ON e.id=lm.maker_id WHERE lm.label_id=?", (entity_id,)).fetchone()
    maker = dict(maker) if maker else None
    labels = [dict(row) for row in c.execute(
        "SELECT e.id,e.canonical_name name,e.canonical_name k,"
        "(SELECT count(DISTINCT " + video_work_key(contract, c) + ") FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id"
        " WHERE " + COUNTED_VIDEO + " AND " + scope_predicate("studio", "ae.entity_id", "e.id") + ") n "
        "FROM label_maker lm JOIN entity e ON e.id=lm.label_id WHERE lm.maker_id=? "
        "ORDER BY n DESC,e.canonical_name", (entity_id,))]
    for ref in ([maker] if maker else []) + labels:
        ref["has_logo"] = contract.has_logo(ref["name"])
        ref["logo_version"] = contract.logo_version(ref["name"])
    for label in labels:
        label["has_image"] = contract.has_entity_image("studio", label["id"])
        label["image_version"] = contract.entity_image_version("studio", label["id"])
    return maker, labels


def _entity_links(c, entity_id: int, kind: str) -> list[dict]:
    """资料页那一排外链。敏感来源与失效的链接列出来但不给地址：页面只写它是哪家。"""
    links = []
    retired = link_status.retired_year(c, entity_id) if kind == "performer" else None
    for link in c.execute(
        "SELECT id AS link_id,link_kind,label,url,hostname,is_sensitive,metadata_json "
        "FROM entity_link WHERE entity_id=? ORDER BY link_kind,label", (entity_id,),
    ):
        item = dict(link)
        host = item["hostname"] or urlsplit(item["url"]).hostname or ""
        sensitive = bool(item.pop("is_sensitive")) or item["link_kind"] == "source_reference"
        item["hostname"] = host
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, ValueError):
            item["metadata"] = {}
        # 失效的那一枚悬停说明为什么点不了，隐退年份随它一起下发。
        gone = link_status.gone_mark(item["metadata"]) is not None
        item["gone"] = gone
        if gone:
            item["retired_year"] = retired
        item["clickable"] = not (sensitive or gone)
        if not item["clickable"]:
            item["url"] = None
        links.append(item)
    return links


def q_entity(contract: WebContract, args):
    """女优、厂牌、事务所等实体的资料页。

    `source_reference` 是私人馆藏来源证据：API 只返回站点名和备注，不把敏感下载
    地址变成可点击链接。官方、社交和资料库链接可直接访问。
    """
    query = _read_entity_query(args)
    if isinstance(query, dict):
        return query
    kind, name, entity_id = query
    with contract.read_connection() as c:
        row = _query_entity(c, kind, name, entity_id)
        if not row:
            return _missing_entity_result(c, kind, name, entity_id)
        d = dict(row)
        from .web_library_shortcuts import favorite_ids
        d["favorite"] = d["id"] in favorite_ids(c)
        try:
            metadata = json.loads(d.pop("metadata_json") or "{}")
        except (TypeError, ValueError):
            metadata = {}
        d["metadata"] = metadata
        d.update(entity_classification.summaries(c, [d['id']])[d['id']])
        d['related_identities'] = entity_classification.related_identities(c, d['id'])
        alias_rows = _entity_alias_rows(c, d["id"])
        d["aliases"] = [row["alias"] for row in alias_rows]
        # 罗马字仍是检索和旧链接的重要身份键，但中文/日文规范名下面再把英文全列一遍
        # 只会像名称没有本地化。展示契约单独收窄，身份契约 `aliases` 保持完整。
        d["display_aliases"] = _display_entity_aliases(
            d["canonical_name"], d["aliases"])
        if kind in {'studio', 'agency'}:
            d['display_aliases'] = [alias for alias in d['aliases'] if alias != d['canonical_name']]
        # 自己敲进来的那几个单独报一遍：界面只在这些名字上给撤销，刮削和合并留下的
        # 是来源记录，不给一次点击删掉。
        d["user_aliases"] = [r[0] for r in c.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=? AND source=? ORDER BY alias",
            (d["id"], USER_ALIAS_SOURCE),
        )]
        d["links"] = _entity_links(c, d["id"], kind)
        d["search_terms"] = [dict(r) for r in c.execute(
            "SELECT term,purpose,source FROM entity_search_term WHERE entity_id=? "
            "ORDER BY purpose,term", (d["id"],),
        )]
        d["external_refs"] = [dict(r) for r in c.execute(
            "SELECT provider,external_kind,external_id,last_synced_at "
            "FROM entity_external_ref WHERE entity_id=? "
            "ORDER BY provider,external_kind,external_id",
            (d["id"],),
        )]
        d.update(_performer_entries(contract, c, d, alias_rows) if kind == "performer" or 'japanese_av' in d['identity_categories']
                 else {"entry_links": []})
        # 人物资料与按名义分组的别名。
        if kind in {"performer", "creator"}:
            d.update(performer_header.header(c, d["id"], d["canonical_name"]))
        elif kind in {'studio', 'agency'}:
            d['company_profile'] = company_profiles.public_profile(metadata)
        # 创作者在关注里的那一面（ADR-0096）：绑在名下的来源和同名等确认的那几组。
        d["follow"] = web_follow.entity_follow(contract, c, d["id"]) if kind == "creator" else None
        scope = scope_predicate(kind, "ae.entity_id") + _tag_scope_visibility(kind, "ae.entity_id", "a.id")
        count, rep = c.execute(
            "SELECT count(DISTINCT " + video_work_key(contract, c) + "),"
            "(SELECT a2.id FROM asset_entity ae2 CROSS JOIN asset a2 ON a2.id=ae2.asset_id "
            " WHERE " + scope_predicate(kind, "ae2.entity_id") +
            _tag_scope_visibility(kind, "ae2.entity_id", "a2.id") +
            " AND a2.medium='video' AND a2.snapshot_path IS NOT NULL " +
            (" AND " + solo_performer_clause("a2.id", "ae2.entity_id") if kind == "performer" else "") +
            " ORDER BY a2.size DESC LIMIT 1) "
            "FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id "
            "WHERE " + scope + " AND " + COUNTED_VIDEO, (d["id"], d["id"]),
        ).fetchone()
        d["asset_count"] = count
        d["representative_asset_id"] = rep
        d["avatar_focus"] = contract.avatar_focus(kind, d["id"])
        d["tags"] = [dict(r) for r in c.execute(
            "SELECT tag.id,tag.canonical_name k,count(DISTINCT " + video_work_key(contract, c) + ") n "
            "FROM asset_entity scope "
            "JOIN asset_entity tagged ON tagged.asset_id=scope.asset_id "
            "JOIN entity tag ON tag.id=tagged.entity_id "
            "JOIN asset a ON a.id=scope.asset_id "
            "WHERE " + scope_predicate(kind, "scope.entity_id") +
            _tag_scope_visibility(kind, "scope.entity_id", "a.id") +
            " AND " + COUNTED_VIDEO + " AND tag.kind='tag' "
            "AND " + tag_is_not_a_performer_name("tag.normalized_name") + " "
            f"AND tag.canonical_name NOT IN ({','.join('?' for _ in LENGTH_TAGS)}) "
            "AND " + tag_not_hidden("scope.asset_id", "tag.normalized_name") + " "
            "GROUP BY tag.id,tag.canonical_name ORDER BY n DESC,tag.canonical_name LIMIT 36",
            (d["id"], *sorted(LENGTH_TAGS)),
        )]
        # 那排圆头像，事务所页问的是另一个问题。别的资料页问「谁和这条实体同台」，
        # 事务所页问「这家有哪些人」——共演者对它没有意义，它自己一部片都没拍。
        # 契约形状保持一致（id / k / n / rep），前端仍是同一个组件。
        if kind == "agency":
            roster = c.execute(
                "SELECT person.id,person.canonical_name k,"
                "(SELECT count(DISTINCT " + video_work_key(contract, c) + ") FROM asset_entity ae "
                " JOIN asset a ON a.id=ae.asset_id "
                " WHERE ae.entity_id=person.id AND " + COUNTED_VIDEO + ") n,"
                "(SELECT a2.id FROM asset_entity ae2 JOIN asset a2 ON a2.id=ae2.asset_id "
                " WHERE ae2.entity_id=person.id AND a2.medium='video' "
                " AND a2.snapshot_path IS NOT NULL AND " + solo_performer_clause("a2.id", "person.id") +
                " ORDER BY COALESCE(a2.play_count,0) DESC,COALESCE(a2.play_seconds,0) DESC,"
                " COALESCE(a2.width,0)*COALESCE(a2.height,0) DESC,a2.size DESC LIMIT 1) rep "
                "FROM entity_membership m JOIN entity person ON person.id=m.member_id "
                "WHERE m.agency_id=? ORDER BY n DESC,person.canonical_name",
                (d["id"],))
        else:
            roster = c.execute(
                "SELECT person.id,person.canonical_name k,count(DISTINCT " + video_work_key(contract, c) + ") n,"
                "(SELECT a2.id FROM asset_entity ae2 JOIN asset a2 ON a2.id=ae2.asset_id "
                " WHERE ae2.entity_id=person.id AND a2.medium='video' "
                + _tag_scope_visibility(kind, "scope.entity_id", "a2.id") +
                " AND a2.snapshot_path IS NOT NULL AND " + solo_performer_clause("a2.id", "person.id") +
                " ORDER BY COALESCE(a2.play_count,0) DESC,COALESCE(a2.play_seconds,0) DESC,"
                " COALESCE(a2.width,0)*COALESCE(a2.height,0) DESC,a2.size DESC LIMIT 1) rep "
                "FROM asset_entity scope "
                "JOIN asset_entity co ON co.asset_id=scope.asset_id "
                "JOIN entity person ON person.id=co.entity_id "
                "JOIN asset a ON a.id=scope.asset_id "
                "WHERE " + scope_predicate(kind, "scope.entity_id") +
                _tag_scope_visibility(kind, "scope.entity_id", "a.id") +
                " AND " + COUNTED_VIDEO + " AND person.kind='performer' "
                "AND person.id<>? "
                "GROUP BY person.id,person.canonical_name "
                "ORDER BY n DESC,person.canonical_name LIMIT 18",
                (d["id"], d["id"]))
        d["related_performers"] = [dict(person) for person in roster]
        d["member_count"] = c.execute(
            "SELECT count(*) FROM entity_membership WHERE agency_id=?", (d["id"],)).fetchone()[0]
        # 这条实体现在归哪家事务所。`metadata.agency` 记的是采到的原文和采集时间，
        # 这里给的是账本里那条实体——前者是证据，后者才是能点进去的身份。
        d["agency"] = None
        home = c.execute(
            "SELECT agency.id,agency.canonical_name,m.source,m.checked_at "
            "FROM entity_membership m JOIN entity agency ON agency.id=m.agency_id "
            "WHERE m.member_id=?", (d["id"],)).fetchone()
        if home:
            d["agency"] = dict(home)
        d["maker"], d["labels"] = label_layer(contract, c, kind, d["id"])
    # 公司那个大位先取 `/logo`、取不到才退到别的图。没装标识时直接跳过这一环，省掉
    # 必然 404 的那一跳。标识按名字落盘，厂牌和事务所是同一个仓、同一条取图链。
    if kind in ("studio", "agency"):
        d["has_logo"] = contract.has_logo(d["canonical_name"])
        d["logo_version"] = contract.logo_version(d["canonical_name"])
    # 大位那条链的后两环同样要随资料下发：实体图取不到就直接从代表作头像起步，两样
    # 都取不到就一个 `<img>` 都不出。判定在库连接之外做，它读的是目录索引。
    d["has_image"] = contract.has_entity_image(kind, d["id"])
    d["image_version"] = contract.entity_image_version(kind, d["id"])
    attach_avatar_availability(contract, [d], key="representative_asset_id")
    # 页脚那排共演者是同一个圆头像，用的也是同一条两级链，取景也是同一份 sidecar。
    # 圆框越小越需要取景：一张 3762×2535 的封面塞进 44 px 的圆里，几何居中给出的是
    # 封面正中那块版式，脸在不在里面全看运气。事务所页的艺人大图与这排小圆头像用
    # 同一条链，差别只在框多大，所以判据不该按页面分岔。
    for person in d["related_performers"]:
        person["has_image"] = contract.has_entity_image("performer", person["id"])
        person["image_version"] = contract.entity_image_version("performer", person["id"])
        person["avatar_focus"] = contract.avatar_focus("performer", person["id"])
    attach_avatar_availability(contract, d["related_performers"])
    if kind == "agency":
        d["mark_link_id"] = agency_mark_link(d["links"])
    return d


def agency_mark_link(links: list[dict]) -> int | None:
    """事务所门面圆标取哪一条链接。

    事务所的门面是它自己的标识。没装实体图时给出官网那条链接的 id，页面拿它去
    `/link-mark` 取站点圆标；两样都没有就只剩首字母。作品截图不参加——那是某位成员
    某部片的画面，和这家公司没有关系。存档快照那条也不参加：它的站点圆标是存档站的。
    """
    return next((link["link_id"] for link in links if link["link_kind"] == "official"
                 and not is_archive(link.get("url") or "")), None)


def _entity_shape_parts(contract, connection, entity_id: int | None = None) -> dict[int, list[str]]:
    """实体页形状的判据；深链只查询地址上的实体。"""
    scope = (entity_id,) if entity_id is not None else ()
    parts = {key: ["feed"] for key in web_feeds.feed_row_entity_ids(contract, connection)
             if entity_id is None or key == entity_id}
    for row in connection.execute(
            "SELECT DISTINCT scope.entity_id FROM asset_entity scope"
            " JOIN entity e ON e.id=scope.entity_id AND e.kind<>'agency'"
            " JOIN asset a ON a.id=scope.asset_id AND " + COUNTED_VIDEO +
            " JOIN asset_entity co ON co.asset_id=scope.asset_id AND co.entity_id<>scope.entity_id"
            " JOIN entity person ON person.id=co.entity_id AND person.kind='performer'"
            + (" WHERE scope.entity_id=?" if scope else ""), scope):
        parts.setdefault(int(row[0]), []).append("costars")
    for key in performer_header.profiled(connection):
        if entity_id is None or key == entity_id:
            parts.setdefault(key, []).append("facts")
    for row in connection.execute(
            "SELECT DISTINCT scope.entity_id FROM asset_entity scope"
            " JOIN entity e ON e.id=scope.entity_id AND e.kind IN ('performer','studio')"
            " JOIN asset a ON a.id=scope.asset_id WHERE " + COUNTED_VIDEO + " AND " + JAV_ASSET_PREDICATE
            + (" AND scope.entity_id=?" if scope else ""), scope):
        parts.setdefault(int(row[0]), []).append("jav")
    return parts


def entity_page_shape(contract, connection, entity) -> dict:
    """深链 HTML 携带的首屏形状，与异步形状名单共用判据。"""
    entity_id = int(entity["id"])
    names = [entity["canonical_name"]]
    names.extend(row[0] for row in connection.execute(
        "SELECT DISTINCT alias FROM entity_alias WHERE entity_id=? ORDER BY alias", (entity_id,))
                 if row[0] not in names)
    return {"id": entity_id, "kind": entity["kind"], "names": names,
            "parts": _entity_shape_parts(contract, connection, entity_id).get(entity_id, [])}


def q_entity_shapes(contract, args) -> dict:
    """资料页上可有可无的那几块，哪些实体有：新作那一行（`feed`）、卡底的同台艺人（`costars`）、
    页头右侧那张资料表（`facts`，ADR-0069），以及作品的 JAV 版式（`jav`）。

    骨架在资料到达之前就要照最终形状画：不留位，那一块画出来时从中间顶进来；每页都
    留，没有的那一页又得在画出来时收掉。两块各有一半上下的页面有，猜哪一边都有一半在
    跳。同台艺人的判据与 `q_entity` 那一份同一套；事务所那份名单走名册，不在卡底。
    名单放在服务端，几台设备看到的是同一份。页面按地址里的名字比对，所以别名一起给。
    """
    with contract.database.read_connection() as connection:
        home = {"feed": web_feeds.home_has_feed(contract, connection)}
        parts = _entity_shape_parts(contract, connection)
        entities = {int(row["id"]): {"id": int(row["id"]), "kind": row["kind"],
                                     "names": [row["canonical_name"]], "parts": parts[int(row["id"])]}
                    for row in connection.execute("SELECT id, kind, canonical_name FROM entity ORDER BY id")
                    if int(row["id"]) in parts}
        for alias in connection.execute("SELECT entity_id, alias FROM entity_alias ORDER BY entity_id, alias"):
            names = entities.get(int(alias["entity_id"]), {}).get("names")
            if names is not None and alias["alias"] not in names:
                names.append(alias["alias"])
    return {"ok": True, "home": home, "entities": list(entities.values())}

# ────────────────────────────── 照片 ──────────────────────────────
# 图集就是目录：账本没有图集实体，一个目录下的图片本来就是一份图集，
# `<作品目录>\P\001.jpg` 这种约定在 A:/B: 上到处都是。图集的 id 用目录里最小的
# 资产 id，既稳定又不用把真实路径发给前端（`q_item` 同样不发 `path`）。

#: 图集查询一律带 `a.` 别名。
PHOTO_DIR = dir_expr()


def _entity_alias_rows(c, entity_id: int) -> list[dict]:
    """这条实体的别名连同来源。

    同一个名字按来源分行（主键含 source），合并会再写一条 `merge:*`，所以同一个写法
    能出现两次。留痕属于账本，展示不该把同一个名字并排列两遍：按归一形取置信度最高
    的那一条。`max()` 让 SQLite 把裸列取自同一行，结果是确定的。来源跟着别名一起
    取：外部入口要从这些写法里挑日文艺名，而挑哪一条要看来源。
    """
    return [dict(r) for r in c.execute(
        "SELECT alias,source,max(confidence) AS top FROM entity_alias WHERE entity_id=?"
        " GROUP BY normalized_alias ORDER BY top DESC,alias", (entity_id,))]


def _display_entity_aliases(canonical_name: str, aliases: list[str]) -> list[str]:
    """本地化规范名不重复展示纯拉丁转写；原始别名仍完整保留在 API。"""
    east_asian = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
    canonical_key = normalize_entity_name(canonical_name)
    unique_aliases = [alias for alias in aliases
                      if normalize_entity_name(alias) != canonical_key]
    if not east_asian.search(canonical_name or ""):
        return unique_aliases
    return [alias for alias in unique_aliases
            if east_asian.search(alias or "") or not re.search(r"[A-Za-z]", alias or "")]


def entity_code_sets(contract: WebContract, c, kind: str, entity_id: int) -> list[dict]:
    """实体名下每部有样张的作品一组（ADR-0068）。

    集的 id 是 `code:<番号>`，和目录图集的整数 id 分得开，地址栏的 `set=` 两种都认。集封面就是
    这部片的封面（`/cover`），所以只要账本里有样张，页面不必先下载任何一张样张就画得出这一排。
    新发行的在前，没有发行日的排在最后。
    """
    from . import sample_images

    if not sample_images.table_ready(c):
        return []
    works: dict[str, dict] = {}
    for row in c.execute(
            "SELECT a.code,a.catalog_title,a.original_title,a.release_date "
            "FROM asset_entity ae CROSS JOIN asset a ON a.id=ae.asset_id "
            "WHERE " + scope_predicate(kind, "ae.entity_id") + " AND " + VISIBLE_CATALOG_ASSET +
            _tag_scope_visibility(kind, "ae.entity_id", "a.id") +
            " AND a.code IS NOT NULL AND a.code<>'' ORDER BY a.id", (entity_id,)):
        key = sample_images.code_key(row["code"])
        if not key:
            continue
        work = works.setdefault(key, {"title": "", "release": ""})
        work["title"] = work["title"] or str(row["catalog_title"] or row["original_title"] or "")
        work["release"] = max(work["release"], str(row["release_date"] or ""))
    sets = [{
        "id": f"code:{key}", "kind": "code", "code": key, "name": works[key]["title"],
        "title": f"{key} {works[key]['title']}".strip(), "n": count,
        "release_date": works[key]["release"], "site": site,
        "site_label": sample_images.site_label(site), "has_cover": contract.has_cover(key),
        "cover_version": contract.cover_version(key),
    } for key, (count, site) in sample_images.counts(c, works).items()]
    sets.sort(key=lambda item: item["code"])
    sets.sort(key=lambda item: item["release_date"], reverse=True)
    sets.sort(key=lambda item: not item["release_date"])
    return sets


def q_entity_photos(contract: WebContract, args):
    """实体名下的照片：本地图片分页放在 `items`；`sets` 是分组，`kind` 为 `dir` 的是本地目录图集，
    为 `code` 的是番号样张集（`entity_code_sets`）。`total` 只数本地图片，`sample_total` 数样张。"""
    query = _read_entity_query(args)
    if isinstance(query, dict):
        return query
    kind, name, entity_id = query
    try:
        limit = max(1, min(int(args.get("limit") or 120), 600))
        offset = max(0, int(args.get("offset") or 0))
        # 带种子是「换一批」；不带就按目录、文件名排，一套图的编号顺序不被打乱。
        seed = str(args.get("seed") or "")
        order = seeded_order(seed) if seed else "dir,a.name,a.id"
    except (TypeError, ValueError):
        return {"error": "invalid pagination"}
    with contract.read_connection() as c:
        row = _query_entity(c, kind, name, entity_id)
        if not row:
            return {"error": "not found"}
        # 图集和分页数的是同一批图，`total` 由图集相加，才对得上翻到底的张数。
        scoped = ("FROM asset_entity ae CROSS JOIN asset a ON a.id=ae.asset_id "
                  "WHERE " + scope_predicate(kind, "ae.entity_id") +
                  _tag_scope_visibility(kind, "ae.entity_id", "a.id") +
                  " AND a.medium='image' AND a.name IS NOT NULL "
                  "AND a.disposal IS NULL ")
        sets = [{
            "id": item["id"],
            "kind": "dir",
            "title": photo_set_title(item["dir"]),
            "n": item["n"],
            "bytes": item["bytes"] or 0,
            "location": item["location"],
            "cost": cost_for(item['location'], item['dir']),
        } for item in c.execute(
            # 一张图在范围里可能挂好几行（两位成员、片商连同旗下 label、同一位的不同 role 或
            # source）。先只按 id 去重，再回 `asset` 取路径分组，`n` 和 `bytes` 数的才是图；
            # 每张图恰好落在一个图集里，各组 `n` 相加就是 `total`。
            f"SELECT {PHOTO_DIR} dir,min(a.id) id,count(*) n,sum(a.size) bytes,a.location "
            "FROM (SELECT DISTINCT a.id " + scoped + ") u CROSS JOIN asset a ON a.id=u.id "
            f"GROUP BY {PHOTO_DIR},a.location ORDER BY n DESC,dir",
            (row["id"],),
        )]
        total = sum(item["n"] for item in sets)
        items = [{"id": item["id"], "name": item["name"], "size": item["size"] or 0,
                  "location": item["location"]}
                 for item in c.execute(
                     f"SELECT a.id,a.name,a.size,a.location,{PHOTO_DIR} dir " + scoped +
                     f"GROUP BY a.id,a.name,a.size,a.location,{PHOTO_DIR} "
                     f"ORDER BY {order} LIMIT ? OFFSET ?",
                     (row["id"], limit, offset),
                 )]
        code_sets = entity_code_sets(contract, c, kind, row["id"])
        return {
            "kind": kind, "name": row["canonical_name"], "entity_id": row["id"],
            "sets": code_sets + sets, "total": total, "items": items, "seed": seed,
            "sample_total": sum(item["n"] for item in code_sets),
            "has_more": offset + len(items) < total,
        }


def q_photo_set(contract: WebContract, args):
    """一个图集里的图片。默认按文件名排，`001.jpg` 这类编号才不会乱序；带种子时按种子
    打散，那是「换一批」，翻页沿用同一粒。"""
    try:
        set_id = int(args.get("id", ""))
    except (TypeError, ValueError):
        return {"error": "invalid id"}
    limit = max(1, min(int(args.get("limit") or 120), 600))
    offset = max(0, int(args.get("offset") or 0))
    seed = str(args.get("seed") or "")
    order = seeded_order(seed) if seed else "a.name,a.id"
    with contract.read_connection() as c:
        anchor = c.execute(
            "SELECT id,location,path,name FROM asset "
            "WHERE id=? AND medium='image' AND name IS NOT NULL", (set_id,),
        ).fetchone()
        if not anchor:
            return {"error": "not found"}
        directory = anchor["path"][: len(anchor["path"]) - len(anchor["name"]) - 1]
        par = (directory, anchor["location"])
        total = c.execute(
            f"SELECT count(*) FROM asset a WHERE a.medium='image' AND a.name IS NOT NULL "
            f"AND {PHOTO_DIR}=? AND a.location=? "
            "AND a.disposal IS NULL", par,
        ).fetchone()[0]
        items = [{"id": item["id"], "name": item["name"], "size": item["size"] or 0,
                  "location": item["location"]}
                 for item in c.execute(
                     f"SELECT a.id,a.name,a.size,a.location FROM asset a WHERE a.medium='image' "
                     f"AND a.name IS NOT NULL AND {PHOTO_DIR}=? AND a.location=? "
                     "AND a.disposal IS NULL "
                     f"ORDER BY {order} LIMIT ? OFFSET ?",
                     (*par, limit, offset),
                 )]
        return {
            "id": anchor["id"], "title": photo_set_title(directory),
            "location": anchor["location"], "cost": cost_for(anchor['location'], anchor['path']),
            "total": total, "items": items, "seed": seed, "has_more": offset + len(items) < total,
        }


def _attach_index_following(connection, rows: list[dict]) -> None:
    """名册人物的订阅状态按已启用、绑定实体的来源汇总。"""
    following = {source[0] for source in connection.execute(
        "SELECT entity_id FROM feed_source WHERE kind=? AND enabled=1 "
        "UNION SELECT entity_id FROM follow_source WHERE enabled=1",
        (feeds.KIND_JAVDB_ACTOR,))}
    for row in rows:
        row['following'] = row['entity_id'] in following


def _attach_index_favorites(connection, rows: list[dict]) -> None:
    """按规范实体身份附加名册收藏状态。"""
    from .web_library_shortcuts import favorite_ids

    favorites = favorite_ids(connection)
    for row in rows:
        row["favorite"] = row.get("entity_id") in favorites


def q_index(contract: WebContract, kind, q="", limit=600, offset=0, category=""):
    """全部艺人 / 创作者 / 厂牌 / 事务所 / 标签的索引页数据。"""
    with contract.read_connection() as c:
        if kind == "agencies":
            # 事务所自己不挂作品，所以它排的是人：一家有多少艺人是它的规模，作品数是
            # 顺着成员算出来的。代表图也不取作品截图——那是某位成员某部片的画面，
            # 拿它当一家公司的门面，页面上就会是一张与这家公司无关的脸。
            sql = ("SELECT e.id entity_id,e.canonical_name k,"
                   "(SELECT count(*) FROM entity_membership m WHERE m.agency_id=e.id) members,"
                   "(SELECT count(DISTINCT " + video_work_key(contract, c) + ") FROM asset_entity ae "
                   " JOIN asset a ON a.id=ae.asset_id WHERE " + COUNTED_VIDEO + " AND "
                   + scope_predicate("agency", "ae.entity_id", "e.id") + ") n,"
                   "(SELECT l.id FROM entity_link l WHERE l.entity_id=e.id"
                   " AND l.link_kind='official' AND l.hostname NOT IN ("
                   + ",".join(f"'{host}'" for host in ARCHIVE_HOSTS) + ")"
                   " ORDER BY l.id LIMIT 1) mark "
                   "FROM entity e WHERE e.kind='agency' ")
            par: list = []
            if q: sql += "AND e.canonical_name LIKE ? "; par.append(f"%{q}%")
            sql += "ORDER BY members DESC,n DESC,e.canonical_name LIMIT ? OFFSET ?"
            par.extend((limit + 1, offset))
            rows = [dict(r) for r in c.execute(sql, par)]
            has_more = len(rows) > limit
            rows = rows[:limit]
        elif kind == "studios":
            # 片商按合计排，旗下 label 的片都归它（ADR-0051 修订）；有上级的 label 不单列，
            # 从片商页的名册进去。搜索时照常列出所有叫这个名字的，找 label 不必先猜它归谁。
            # 从 entity 出发：自己一部片都没挂、片全在旗下的片商（妄想族）也要出现。
            # 两个子查询都用 `CROSS JOIN` 钉住连接顺序，原因见 `scope_predicate`：交给规划器
            # 的话，代表作那句每家片商都把全部有截图的视频回查一遍，一百多家叠起来要两秒多。
            scope = scope_predicate("studio", "ae.entity_id", "e.id")
            sql = ("SELECT * FROM (SELECT e.id entity_id,e.canonical_name k,"
                   "(SELECT count(DISTINCT " + video_work_key(contract, c) + ") FROM asset_entity ae"
                   " CROSS JOIN asset a ON a.id=ae.asset_id WHERE " + COUNTED_VIDEO + " AND " + scope + ") n,"
                   "(SELECT a2.id FROM asset_entity ae CROSS JOIN asset a2 ON a2.id=ae.asset_id "
                   " WHERE " + scope + " AND a2.medium='video' AND a2.snapshot_path IS NOT NULL "
                   " ORDER BY COALESCE(a2.play_count,0) DESC,COALESCE(a2.play_seconds,0) DESC,"
                   " COALESCE(a2.width,0)*COALESCE(a2.height,0) DESC,a2.size DESC LIMIT 1) rep "
                   "FROM entity e WHERE e.kind='studio' ")
            par = []
            if q:
                sql += "AND e.canonical_name LIKE ? "; par.append(f"%{q}%")
            else:
                sql += "AND NOT EXISTS (SELECT 1 FROM label_maker lm WHERE lm.label_id=e.id) "
            sql += ") WHERE n>0 ORDER BY n DESC,k LIMIT ? OFFSET ?"
            par.extend((limit + 1, offset))
            rows = [dict(r) for r in c.execute(sql, par)]
            has_more = len(rows) > limit
            rows = rows[:limit]
        elif kind in INDEX_ENTITY_KINDS:
            entity_kind = INDEX_ENTITY_KINDS[kind]
            sql = ("SELECT e.id entity_id,e.kind entity_kind,e.canonical_name k,count(DISTINCT " + video_work_key(contract, c) + ") n,"
                   "(SELECT a2.id FROM asset_entity ae2 JOIN asset a2 ON a2.id=ae2.asset_id "
                   " WHERE ae2.entity_id=e.id AND a2.medium='video' AND a2.snapshot_path IS NOT NULL " +
                   (" AND (e.kind='creator' OR (" + solo_performer_clause("a2.id", "e.id") + '))' if entity_kind == "performer" else "") +
                   " ORDER BY COALESCE(a2.play_count,0) DESC,COALESCE(a2.play_seconds,0) DESC,"
                   " COALESCE(a2.width,0)*COALESCE(a2.height,0) DESC,a2.size DESC LIMIT 1) rep "
                   "FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
                   "JOIN asset a ON a.id=ae.asset_id "
                   "WHERE " + COUNTED_VIDEO + " AND " + entity_classification.index_scope(c,entity_kind) + ' ')
            par = []
            if q: sql += "AND e.canonical_name LIKE ? "; par.append(f"%{q}%")
            clause, values = entity_classification.filter_sql(category,connection=c)
            sql += clause
            par.extend(values)
            sql += "GROUP BY e.id,e.canonical_name ORDER BY n DESC LIMIT ? OFFSET ?"
            par.extend((limit + 1, offset))
            rows = [dict(r) for r in c.execute(sql, par)]
            has_more = len(rows) > limit
            rows = rows[:limit]
            summaries = entity_classification.summaries(c, [row['entity_id'] for row in rows])
            for row in rows:
                row.update(summaries[row['entity_id']])
            _attach_index_following(c, rows)
            category_counts = entity_classification.counts(c,entity_kind,q,COUNTED_VIDEO)
        else:
            sql = ("SELECT e.id entity_id,e.canonical_name k, count(DISTINCT " + video_work_key(contract, c) + ") n "
                   "FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
                   "JOIN asset a ON a.id=ae.asset_id WHERE " + COUNTED_VIDEO + " AND e.kind='tag' "
                   f"AND e.canonical_name NOT IN ({','.join('?' for _ in LENGTH_TAGS)}) "
                   "AND " + tag_is_not_a_performer_name("e.normalized_name") + " "
                   "AND " + tag_not_hidden("ae.asset_id", "e.normalized_name") + " ")
            par = sorted(LENGTH_TAGS)
            if q: sql += "AND e.canonical_name LIKE ? "; par.append(f"%{q}%")
            sql += "GROUP BY e.id,e.canonical_name ORDER BY n DESC"
            all_rows = [dict(r, cat=tag_cat(r["k"])) for r in c.execute(sql, par)]
            category_counts: dict[str, int] = {}
            for row in all_rows:
                category_counts[row["cat"]] = category_counts.get(row["cat"], 0) + 1
            if category and category != "all":
                all_rows = [row for row in all_rows if row["cat"] == category]
            rows = all_rows[offset:offset + limit]
            has_more = offset + limit < len(all_rows)
        _attach_index_favorites(c, rows)
    if kind in INDEX_ENTITY_KINDS:
        entity_kind = INDEX_ENTITY_KINDS[kind]
        # 索引页一屏几十个圆头像，走的是和顶栏那排同一条两级链：规范实体图优先，
        # 取不到才回落到代表作头像。没有这两个标志就只能无条件出图、等 404 再把图摘掉，
        # `/performers` 桌面视口滚三屏实测 77 个取图请求里 5 个是这样的 404。
        # 判定在库连接之外做，它读的是目录索引而不是账本。
        for row in rows:
            row_kind = row.get('entity_kind',entity_kind)
            row["has_image"] = contract.has_entity_image(row_kind, row.get("entity_id"))
            row["image_version"] = contract.entity_image_version(row_kind, row.get("entity_id"))
        attach_avatar_availability(contract, rows)
        #: 索引页的大图版式把头像裁成竖幅，几何居中会切掉脸。取景与资料页大图同一份
        #: sidecar、同一个换算，只是这里按行取；读的是文件，所以放在连接之外。
        for row in rows:
            row["avatar_focus"] = contract.avatar_focus(row.get('entity_kind',entity_kind), row["entity_id"])
        #: 公司的门面是它的标识，和资料页大位同一条链：`/logo` 优先，取不到才退回实体图。
        #: 索引页一屏几十格，缺了这个标志就只能格格出 `<img>` 等 404。
        if entity_kind in ("studio", "agency"):
            for row in rows:
                row["has_logo"] = contract.has_logo(row["k"])
                row["logo_version"] = contract.logo_version(row["k"])
    result = {"kind": kind, "items": rows, "has_more": has_more}
    if kind in {"tags", "performers", "creators"}:
        result["categories"] = category_counts
    return result


#: 补全每组的条数上限。下拉栏还要同时装搜索记录，每组给满五条已经会把靠后的组
#: 推到需要滚动的位置。
SUGGEST_GROUP_LIMIT = 5

#: 页签里只看一类时的条数上限。那时下拉栏只装这一组，四倍的量刚好铺满宽屏两栏。
SUGGEST_KIND_LIMIT = SUGGEST_GROUP_LIMIT * 4

#: 一个人名下跟着带出来的近作张数。宽下拉一行摆得下的最多那几张；窄下拉一张不画，
#: 但数据照给——同一段输入在两种宽度下取的是同一个缓存键。
SUGGEST_WORKS = 4

#: 挑近作时每人先取的候选数。有的片还没装封面、也没抽帧，要从后面几部补上。
SUGGEST_WORK_CANDIDATES = SUGGEST_WORKS * 3

#: 补全的分组顺序与显示名，顺序就是下拉栏里的先后。它只定在这里，界面照抄——
#: 两侧各排一次的话，改了一侧就会出现「后端认为最该先看的组显示在第三位」。
#: 作品垫底：它的值是番号或整句标题，扫读成本比一个人名高。
SUGGEST_GROUPS = (
    ("performer", "女优"), ("creator", "创作者"), ("studio", "厂牌"),
    ("agency", "事务所"), ("series", "系列"), ("tag", "标签"), ("asset", "视频"),
)

#: 直接挂在作品上的实体种类。事务所不在内：`asset_entity` 里没有它的行，
#: 作品是它的成员拍的，所以它单走一条查询。
SUGGEST_ENTITY_KINDS = ("performer", "creator", "studio", "series", "tag")

#: 拉丁短输入按词首比的字符数门槛。两个字母做子串比，命中的多半是别的词中间那
#: 两个字母：真实账本上「MO」会捞出 `kemonokai`，「Pr」会捞出 `chf3_prob4`，而用户
#: 在打的是 MOODYZ 和 Prestige。
SUGGEST_WORD_HEAD_BELOW = 3


def _suggest_patterns(query: str) -> dict[str, str]:
    """这段输入该按什么形状去比。

    拉丁文字的词有词首，所以短输入比词首——整串开头，或某个空格之后。汉字和假名
    没有分词空格，人名与标签本来就出现在名字中间，那一侧照旧按子串，否则「凉森」
    这种两字输入会连自己都补不出来。

    补全按这两种形状取词，`/api/items` 一律按子串找片，所以补全给出的是搜索命中的
    一个子集——反过来不成立才是问题：那意味着下拉里的词点下去是空的。
    """
    prefix = f"{query}%"
    if len(query) < SUGGEST_WORD_HEAD_BELOW and query.isascii():
        return {"head": prefix, "tail": f"% {query}%", "prefix": prefix}
    contains = f"%{query}%"
    return {"head": contains, "tail": contains, "prefix": prefix}


def _suggest_like(column: str) -> str:
    """一列与这段输入的比较。两个占位符是同一件事的两种形状，见 `_suggest_patterns`。"""
    return f"({column} LIKE :head OR {column} LIKE :tail)"


#: 一个实体被这段输入命中的三条路，与 `/api/items` 的搜索同源：规范名、别名、检索词。
#: 少认一条的后果是两个方向的落空——补出来的词搜不到，或者搜得到的词补不出来。
SUGGEST_ENTITY_MATCH = (
    "(" + _suggest_like("e.canonical_name")
    + " OR EXISTS(SELECT 1 FROM entity_alias al WHERE al.entity_id=e.id"
    " AND " + _suggest_like("al.alias") + ")"
    " OR EXISTS(SELECT 1 FROM entity_search_term st WHERE st.entity_id=e.id"
    " AND " + _suggest_like("st.term") + "))"
)

#: 命中的是哪个写法。规范名自己命中时留空——那一项显示的就是规范名，再标一次
#: 等于把「涼森れむ（涼森れむ）」摆到用户面前。命中别名或检索词才有话要说：
#: 用户输入「凉森」，看到的一行是「涼森れむ」，不说凭什么，他会以为补错了人。
SUGGEST_MATCHED = (
    "CASE WHEN " + _suggest_like("e.canonical_name") + " THEN '' ELSE COALESCE("
    "(SELECT al.alias FROM entity_alias al WHERE al.entity_id=e.id"
    " AND " + _suggest_like("al.alias") + " LIMIT 1),"
    "(SELECT st.term FROM entity_search_term st WHERE st.entity_id=e.id"
    " AND " + _suggest_like("st.term") + " LIMIT 1),'') END matched"
)


def _suggest_entity_rows(connection, params, work_key):
    """直接挂着作品的实体，按名下作品数排，前缀命中的排在前面。"""
    length_keys = {f"lt{index}": tag for index, tag in enumerate(sorted(LENGTH_TAGS))}
    kinds = ",".join(f"'{kind}'" for kind in SUGGEST_ENTITY_KINDS)
    sql = (
        "SELECT e.kind kind, e.id entity_id, e.canonical_name k, count(DISTINCT " + work_key + ") n, "
        + SUGGEST_MATCHED + " FROM entity e "
        "JOIN asset_entity ae ON ae.entity_id=e.id JOIN asset a ON a.id=ae.asset_id "
        "WHERE " + VISIBLE_CATALOG_ASSET + f" AND e.kind IN ({kinds}) "
        "AND " + SUGGEST_ENTITY_MATCH + " "
        # 标签沿用标签榜的可见性：时长标签是筛选控件而不是词，与女优同名的标签是
        # 另一个身份的冒充，被隐藏的标签用户已经说过不想看见。
        "AND (e.kind<>'tag' OR (e.canonical_name NOT IN ("
        + ",".join(f":{key}" for key in length_keys) + ") AND "
        + tag_is_not_a_performer_name("e.normalized_name") + " AND "
        + tag_not_hidden("ae.asset_id", "e.normalized_name") + ")) "
        "GROUP BY e.id ORDER BY "
        "CASE WHEN e.canonical_name LIKE :prefix THEN 0 ELSE 1 END, n DESC, e.canonical_name"
    )
    return connection.execute(sql, {**params, **length_keys}).fetchall()


def _suggest_agency_rows(connection, params, work_key):
    """事务所的规模顺着成员算，判据与它的资料页、索引页同一份。

    名下一部作品都没有的事务所不进补全：账本里有它的身份，但按它搜出来是空的。

    计数用 `CROSS JOIN` 把 `asset_entity` 钉在外层：从成员出发走 `entity_id` 索引，
    每家只碰成员名下那几百行。交给规划器排的话，真实账本的统计会让它从 `asset` 那侧
    按 `medium` 起步，每命中一家事务所就把八万条可见作品逐条回查一遍——单个字母能
    命中十几家，补全要等两三秒。
    """
    sql = (
        "SELECT * FROM (SELECT 'agency' kind, e.id entity_id, e.canonical_name k, "
        "(SELECT count(DISTINCT " + work_key + ") FROM asset_entity ae "
        " CROSS JOIN asset a ON a.id=ae.asset_id WHERE " + VISIBLE_CATALOG_ASSET + " AND "
        + scope_predicate("agency", "ae.entity_id", "e.id") + ") n, "
        # 事务所没有标识文件，门面是官网的站点圆标，与索引页同一条链接。
        "(SELECT l.id FROM entity_link l WHERE l.entity_id=e.id"
        " AND l.link_kind='official' AND l.hostname NOT IN ("
        + ",".join(f"'{host}'" for host in ARCHIVE_HOSTS) + ")"
        " ORDER BY l.id LIMIT 1) mark, "
        + SUGGEST_MATCHED + " FROM entity e WHERE e.kind='agency' AND "
        + SUGGEST_ENTITY_MATCH + ") WHERE n>0 "
        "ORDER BY CASE WHEN k LIKE :prefix THEN 0 ELSE 1 END, n DESC, k"
    )
    return connection.execute(sql, params).fetchall()


#: 作品按它叫什么命中：番号、发行标题、原题。
SUGGEST_ASSET_NAMED = (
    "(" + _suggest_like("a.code") + " OR " + _suggest_like("a.catalog_title")
    + " OR " + _suggest_like("a.original_title") + ")"
)
#: 作品这一组的全部命中：叫什么，加上文件名。
SUGGEST_ASSET_MATCH = "(" + SUGGEST_ASSET_NAMED + " OR " + _suggest_like("a.name") + ")"

#: 下拉栏里「一部作品」的身份。番号作品按封面缓存键归一：同一个番号存着几个文件、同一部
#: 挂着几条来源，画出来都是同一张封面。没有番号的按抽帧，抽帧也没有才是文件自己。
#: `normalise_code_key` 是连接上注册的 SQL 函数，与封面缓存同一份实现。
SUGGEST_WORK_KEY = ("COALESCE(NULLIF(normalise_code_key(a.code),''),"
                    "'thumb:'||a.snapshot_path,'id:'||a.id)")

#: 同一部的几份里由哪一份出面：有抽帧的优先（封面按番号共用，抽帧是逐个文件的，挑到没
#: 抽帧的那份会让整部作品掉出近作），其次番号写法规范的那份，再次最早入库的那份。
SUGGEST_WORK_COPY_ORDER = "a.snapshot_path IS NULL,a.code IS NOT normalise_code_key(a.code),a.id"


def _suggest_asset_rows(connection, params, limit):
    """作品这一组给的是「打开这一条」，不是一个搜索词。

    番号是可搜的短词，整句标题不是：把一整行带全角括号和空格的标题填回搜索框，
    下一次搜索会因为其中任何一个字符对不上而落空。所以这一组带上 id，由界面
    直接开详情，而不是绕一趟搜索。

    文件名排在最后一档。它是存储事实而不是这部片叫什么，命中它的多半是扩展名和
    转码标记：真实账本上「MO」会从文件名里捞出 `IMG_2757_682.MOV` 和一条标题里
    带 `MOVIE版` 的转码文件。番号和发行标题够五条时它就不露面。

    一部作品只占一格（`SUGGEST_WORK_KEY`）：真实账本上 IPVR-050 存着三个文件，逐行
    列出来就是三张一样的封面。出面的是播得最多的那份，打开的就是用户一直在看的文件。
    """
    tier = ("CASE WHEN a.code LIKE :prefix THEN 0 WHEN " + SUGGEST_ASSET_NAMED
            + " THEN 1 ELSE 2 END")
    sql = (
        "SELECT id, code, title, name, snapshot_path FROM (SELECT a.id id, "
        "COALESCE(a.code,'') code, COALESCE(a.catalog_title,'') title, "
        "COALESCE(a.name,'') name, a.snapshot_path snapshot_path, " + tier + " tier, "
        "COALESCE(a.play_count,0) plays, ROW_NUMBER() OVER (PARTITION BY " + SUGGEST_WORK_KEY
        + " ORDER BY " + tier + ", COALESCE(a.play_count,0) DESC, " + SUGGEST_WORK_COPY_ORDER
        + ") copy FROM asset a WHERE " + VISIBLE_CATALOG_ASSET + " AND " + SUGGEST_ASSET_MATCH
        + ") WHERE copy=1 ORDER BY tier, plays DESC, id DESC LIMIT :limit"
    )
    return connection.execute(sql, {**params, "limit": limit}).fetchall()


def _suggest_asset_total(connection, params) -> int:
    """作品这一组一共命中多少部。页签上的数要和点进去看到的是同一个判据，所以同样按
    `SUGGEST_WORK_KEY` 数，同一部的几个文件算一部。"""
    sql = ("SELECT count(DISTINCT " + SUGGEST_WORK_KEY + ") FROM asset a WHERE "
           + VISIBLE_CATALOG_ASSET + " AND " + SUGGEST_ASSET_MATCH)
    return int(connection.execute(sql, params).fetchone()[0])


def _asset_display_name(name: str) -> str:
    """文件名去掉扩展名。`.mp4` 是存储事实，不是这部片叫什么。

    去掉之后它仍是 `asset.name` 的子串，所以拿它去搜照样命中这一条。
    """
    head, _, tail = name.rpartition(".")
    return head if head and len(tail) <= 4 else name


#: 下拉栏里画一张脸的种类。系列和标签只是一个词，厂牌和事务所画的是标识。
SUGGEST_PEOPLE = ("performer", "creator")


def _suggest_work_card(contract: WebContract, row) -> dict | None:
    """一部作品在下拉栏里的那张小图：官方封面优先，其次抽帧。两样都没有就不画。

    正封取景要的框和人脸位置跟着封面走，与卡片同一份 `poster_box`／`cover_frame`：
    下拉栏的小格和卡片是同一个比例，取景换算在页面上共用一套。
    """
    code = row["code"] or ""
    has_cover = bool(code) and contract.has_cover(code)
    has_thumb = contract.has_snapshot(row["snapshot_path"])
    if not (has_cover or has_thumb):
        return None
    card = {"id": row["id"], "code": code, "has_cover": has_cover, "has_thumb": has_thumb}
    if has_cover:
        card["cover_frame"] = contract.cover_frame(code)
        card["cover_version"] = contract.cover_version(code)
        card["poster_box"] = contract.poster_box(code)
    return card


def _suggest_faces(contract: WebContract, connection, kind: str, items: list[dict]) -> None:
    """人这一行要画的：一张脸、所属事务所、几部近作。

    脸走资料页同一条两级链：规范实体图优先，取不到退到代表作头像。代表作的挑法与
    索引页一致，女优只取单人作品——多人作品的那一帧不一定是她。近作也先排单人作品：
    合集封面上站着一排人，缩到下拉栏那么小认不出哪一个是她。

    近作按部挑，不按行挑（`SUGGEST_WORK_KEY`）。`asset_entity` 的主键带来源，同一部
    片常有 `r18` 与 `javinizer` 各记一条；同一个番号也常存着几个文件。去重必须在截取
    候选之前：FC2 一个番号存了十九份，先截十二行再去重就只剩一部。
    """
    if not items:
        return
    ids = [item["entity_id"] for item in items]
    marks = ",".join("?" * len(ids))
    solo_rep = " AND " + solo_performer_clause("a2.id", "e.id") if kind == "performer" else ""
    reps = {row[0]: row[1] for row in connection.execute(
        "SELECT e.id,(SELECT a2.id FROM asset_entity ae2 JOIN asset a2 ON a2.id=ae2.asset_id"
        " WHERE ae2.entity_id=e.id AND a2.medium='video' AND a2.snapshot_path IS NOT NULL"
        + solo_rep +
        " ORDER BY COALESCE(a2.play_count,0) DESC,COALESCE(a2.play_seconds,0) DESC,"
        " COALESCE(a2.width,0)*COALESCE(a2.height,0) DESC,a2.size DESC LIMIT 1)"
        f" FROM entity e WHERE e.id IN ({marks})", ids)}
    agencies: dict[int, str] = {}
    if kind == "performer":
        for member, name in connection.execute(
                "SELECT m.member_id,e.canonical_name FROM entity_membership m"
                f" JOIN entity e ON e.id=m.agency_id WHERE m.member_id IN ({marks})"
                " ORDER BY m.member_id,e.canonical_name", ids):
            agencies.setdefault(member, name)
    solo = ("CASE WHEN " + solo_performer_clause("a.id", "ae.entity_id")
            + " THEN 0 ELSE 1 END" if kind == "performer" else "0")
    works: dict[int, list[dict]] = {}
    for row in connection.execute(
            "SELECT entity_id,id,code,snapshot_path FROM (SELECT *,ROW_NUMBER() OVER ("
            "PARTITION BY entity_id ORDER BY solo,release_date DESC NULLS LAST,"
            "first_seen DESC,id DESC) rn FROM (SELECT ae.entity_id entity_id,a.id id,"
            "COALESCE(a.code,'') code,a.snapshot_path snapshot_path," + solo + " solo,"
            "a.release_date release_date,a.first_seen first_seen,"
            "ROW_NUMBER() OVER (PARTITION BY ae.entity_id," + SUGGEST_WORK_KEY +
            " ORDER BY " + SUGGEST_WORK_COPY_ORDER + ") copy"
            " FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id"
            f" WHERE ae.entity_id IN ({marks}) AND " + VISIBLE_CATALOG_ASSET +
            ") WHERE copy=1) WHERE rn<=? ORDER BY entity_id,rn", [*ids, SUGGEST_WORK_CANDIDATES]):
        picked = works.setdefault(row["entity_id"], [])
        if len(picked) < SUGGEST_WORKS and (card := _suggest_work_card(contract, row)):
            picked.append(card)
    for item in items:
        entity_id = item["entity_id"]
        item["has_image"] = contract.has_entity_image(kind, entity_id)
        item["image_version"] = contract.entity_image_version(kind, entity_id)
        if item["has_image"]:
            item["avatar_focus"] = contract.avatar_focus(kind, entity_id)
        item["rep"] = reps.get(entity_id)
        if kind == "performer":
            item["agency"] = agencies.get(entity_id, "")
        item["works"] = works.get(entity_id, [])


def _suggest_works(contract: WebContract, connection, rows: list[dict]) -> list[dict]:
    """作品这一组：打开这一条要的 id，一张小图，加上它是谁拍的。

    番号作品的署名是女优，其余是创作者——与卡片署名同一个取舍。
    """
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    marks = ",".join("?" * len(ids))
    names: dict[int, dict[str, str]] = {}
    for asset_id, kind, name in connection.execute(
            "SELECT ae.asset_id,e.kind,e.canonical_name FROM asset_entity ae"
            " JOIN entity e ON e.id=ae.entity_id"
            f" WHERE ae.asset_id IN ({marks}) AND e.kind IN ('performer','creator')"
            " ORDER BY ae.asset_id,e.canonical_name", ids):
        names.setdefault(asset_id, {}).setdefault(kind, name)
    items = []
    for row in rows:
        credit = names.get(row["id"], {})
        who = (credit.get("performer") or credit.get("creator") if row["code"]
               else credit.get("creator") or credit.get("performer"))
        items.append({
            # 番号优先：它既是这条作品的名字，也是一个搜得到的短词。没有番号的
            # 才退到发行标题，最后才是文件名——文件名是最不像「这部片叫什么」的
            # 那个写法。
            "value": row["code"] or row["title"] or _asset_display_name(row["name"]),
            "n": 0, "matched": "", "id": row["id"],
            "code": row["code"], "title": row["title"], "who": who or "",
            "card": _suggest_work_card(contract, row),
        })
    return items


def suggest_kinds(raw: str) -> tuple[str, ...]:
    """`kind=performer,creator` 这种参数里认得的那几类，按声明顺序。认不出的丢掉。"""
    asked = {part.strip() for part in str(raw or "").split(",")}
    return tuple(kind for kind, _ in SUGGEST_GROUPS if kind in asked)


def q_suggest(contract: WebContract, q: str, limit: int = SUGGEST_GROUP_LIMIT,
              kinds: tuple[str, ...] = ()):
    """搜索栏下拉的补全：给一段输入，返回馆藏里点得开的身份与作品。

    「点得开」不是靠调用方逐条验一遍达成的，是判据本身与 `/api/items` 同源：
    可见性用的是同一个 `VISIBLE_CATALOG_ASSET`，命中的三条路是搜索 LIKE 分支的
    那三条，实体行来自实际挂着作品的 join。所以这里返回的每一项，按它的 `value`
    去搜都有结果——补全与搜索口径漂开，比没有补全更难查。

    `kinds` 非空时只回这几类，下拉栏的页签用它一次拉满一类。每组带 `total`：这段
    输入在这一类里一共命中多少，页签上的数读的就是它。
    """
    query = (q or "").strip()
    if not query:
        return {"q": "", "groups": []}
    wanted = set(kinds or (kind for kind, _ in SUGGEST_GROUPS))
    per_group = max(1, min(int(limit), SUGGEST_KIND_LIMIT))
    params = _suggest_patterns(query)
    buckets: dict[str, list[dict]] = {}
    totals: dict[str, int] = {}
    with contract.read_connection() as connection:
        work_key = video_work_key(contract, connection)
        entities = [dict(row) for row in _suggest_entity_rows(connection, params, work_key)]
        if "agency" in wanted:
            entities += [dict(row) for row in _suggest_agency_rows(connection, params, work_key)]
        for row in entities:
            if row["kind"] not in wanted:
                continue
            totals[row["kind"]] = totals.get(row["kind"], 0) + 1
            bucket = buckets.setdefault(row["kind"], [])
            if len(bucket) < per_group:
                item = {"value": row["k"], "n": row["n"], "matched": row["matched"],
                        "id": None, "entity_id": row["entity_id"]}
                if row["kind"] == "agency":
                    item["mark"] = row["mark"]
                bucket.append(item)
        for kind in SUGGEST_PEOPLE:
            _suggest_faces(contract, connection, kind, buckets.get(kind, []))
        if "asset" in wanted:
            assets = [dict(row) for row in _suggest_asset_rows(connection, params, per_group)]
            buckets["asset"] = _suggest_works(contract, connection, assets)
            totals["asset"] = _suggest_asset_total(connection, params) if assets else 0
    people = [item for kind in SUGGEST_PEOPLE for item in buckets.get(kind, [])]
    attach_avatar_availability(contract, people)
    for item in people:
        # 代表作头像取不到就不给这一环，页面上直接是首字母，不先出一张等 404 的图。
        if not item.pop("has_avatar"):
            item["rep"] = None
    for kind in ("studio", "agency"):
        for item in buckets.get(kind, []):
            item["has_image"] = contract.has_entity_image(kind, item["entity_id"])
            item["image_version"] = contract.entity_image_version(kind, item["entity_id"])
            if kind == "studio":
                item["has_logo"] = contract.has_logo(item["value"])
                item["logo_version"] = contract.logo_version(item["value"])
    return {"q": query, "groups": [
        {"kind": kind, "label": label, "total": totals.get(kind, 0), "items": buckets[kind]}
        for kind, label in SUGGEST_GROUPS if buckets.get(kind)
    ]}


#: 用户在资料页选定统称时，被换下的旧规范名记这个来源。合并留的是 `merge:*`，
#: 刮削留的是站点名；分得开才答得出「这个名字是谁定的」。
PREFERRED_NAME_SOURCE = "user:preferred-name"
#: 用户自己敲进来的别名记这个来源。它是唯一允许在界面上删掉的一类——刮削和合并留下的
#: 那些是这个人真的用过的名字，删掉就问不出这条实体当初为什么长这样。
USER_ALIAS_SOURCE = "user:alias"


def w_entity_alias(contract: WebContract, body):
    """给这条实体添一个别名，或撤掉一个自己添过的。

    别名是身份的一部分：搜索、头像图库和合并判定读的都是它。刮削给的那几种写法常常
    只有日文或罗马字，而图库、片商和用户自己记得的写法可以是第四种——账本里没有那一
    行，这个人在那几条路上就等于不存在。所以这里收自由文本，`entity.canonical_name`
    那边不收：添别名是补一条「他还被这么叫过」，改统称是改真相字段。

    只拦一种：这个写法已经是另一条实体的统称。那说明要么两条该合并、要么是同名不同
    人，两种都得人来判，而这里静默写下去只会让后面的合并判定多一个假信号。

    撤销只认自己添的那些（`user:alias`）。刮削和合并留下的别名是来源记录，不给界面
    上的一次点击删掉。
    """
    contract.cache_bust()
    kind = str(body.get("kind", "")).strip()
    name = str(body.get("name", "")).strip()
    alias = str(body.get("alias", "")).strip()
    remove = bool(body.get("remove"))
    if kind not in PROFILE_KINDS or not name:
        raise ValueError("kind must be a known entity kind and name is required")
    if not alias:
        raise ValueError("alias is required")
    alias_key = normalize_entity_name(alias)
    if not alias_key:
        raise ValueError("alias is required")
    with contract.write_transaction() as c:
        row = resolve_entity(c, kind, name)
        if not row:
            raise ValueError("entity not found")
        entity_id, canonical = int(row["id"]), str(row["canonical_name"])
        if remove:
            removed = c.execute(
                "DELETE FROM entity_alias WHERE entity_id=? AND normalized_alias=? AND source=?",
                (entity_id, alias_key, USER_ALIAS_SOURCE)).rowcount
            if not removed:
                raise ValueError("only aliases you added here can be removed")
            return {"ok": True, "alias": alias, "removed": True}
        if alias_key == normalize_entity_name(canonical):
            # 统称本来就是这个写法，添进别名表只会让下拉里并排出现两个一样的名字。
            return {"ok": True, "alias": canonical, "added": False}
        taken = c.execute(
            "SELECT canonical_name FROM entity WHERE kind=? AND normalized_name=? AND id<>?",
            (kind, alias_key, entity_id)).fetchone()
        if taken:
            raise ValueError(f"another {kind} is already named {taken[0]}")
        existing = c.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=? AND normalized_alias=?",
            (entity_id, alias_key)).fetchone()
        if existing:
            return {"ok": True, "alias": str(existing[0]), "added": False}
        c.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(?,?,?,?,1.0)",
            (entity_id, alias, alias_key, USER_ALIAS_SOURCE))
        return {"ok": True, "alias": alias, "added": True}


def w_entity_name(contract: WebContract, body):
    """把这个实体已有的某个名字提为统称，旧规范名转成别名。

    统称就是 `entity.canonical_name`，它是真相字段。所以这里只做「换一个已经在
    这条实体名下的名字」：候选必须是现在的规范名或它的别名之一，不收自由文本——
    自由文本是改名，那要有来源和证据，不是一次点击该干的事。要把一个全新的写法提成
    统称，先用 `w_entity_alias` 把它记成这条实体的别名，那一步是身份补充、有自己的
    冲突判定；这一步只在已经属于这条实体的名字里挑。

    规范名唯一（`entity(kind, normalized_name)`），选中的名字若已经是另一条实体的
    规范名，这里只报冲突。那种情况要么是两条该合并，要么是同名不同人，都得人来判。

    这是可逆的：把换下来的那个再选回去就还原了。
    """
    contract.cache_bust()
    kind = str(body.get("kind", "")).strip()
    name = str(body.get("name", "")).strip()
    chosen = str(body.get("canonical", "")).strip()
    if kind not in PROFILE_KINDS or not name:
        raise ValueError("kind must be a known entity kind and name is required")
    if not chosen:
        raise ValueError("canonical is required")
    with contract.write_transaction() as c:
        row = resolve_entity(c, kind, name)
        if not row:
            raise ValueError("entity not found")
        entity_id, current = int(row["id"]), str(row["canonical_name"])
        chosen_key = normalize_entity_name(chosen)
        if chosen_key == normalize_entity_name(current):
            return {"ok": True, "canonical_name": current, "changed": False}
        known = {normalize_entity_name(str(item[0])): str(item[0]) for item in c.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=?", (entity_id,))}
        if chosen_key not in known:
            raise ValueError("canonical must be one of this entity's existing names")
        taken = c.execute(
            "SELECT canonical_name FROM entity WHERE kind=? AND normalized_name=? AND id<>?",
            (kind, chosen_key, entity_id)).fetchone()
        if taken:
            raise ValueError(f"another {kind} is already named {taken[0]}")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        c.execute(
            "UPDATE entity SET canonical_name=?,normalized_name=?,updated_at=? WHERE id=?",
            (known[chosen_key], chosen_key, stamp, entity_id))
        # 旧规范名留成别名：它是这个人真的用过的名字，也是选回去的入口。
        c.execute(
            "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(?,?,?,?,1.0)",
            (entity_id, current, normalize_entity_name(current), PREFERRED_NAME_SOURCE))
        flat = rewrite_flat_projection(c, kind, entity_id, current, known[chosen_key])
        return {"ok": True, "canonical_name": known[chosen_key], "changed": True,
                "previous_name": current, "flat_rewritten": flat}
