from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from sqlite3 import Connection


def normalize_entity_name(name: str) -> str:
    return strip_zero_width(name).strip().casefold()


def male_performer_ids(connection: Connection) -> set[int]:
    """明确登记为男优的人物只保留资料，不参与展示和偏好。"""
    from .javdb_public import actor_gender
    found = set()
    for row in connection.execute("SELECT * FROM entity WHERE kind='performer'"):
        try:
            metadata = json.loads(dict(row).get('metadata_json') or '{}')
        except (ValueError, TypeError):
            continue
        if isinstance(metadata, dict) and actor_gender(metadata) == 'male':
            found.add(int(row['id']))
    return found


# 官方标题登记的并写名：https://attackers.net/、https://premium-beauty.com/、
# https://kawaiikawaii.jp/。只折叠已核实的品牌，不剥任意括号或标点。
STUDIO_BRAND_NAMES = {
    'attackers(アタッカーズ)': 'attackers',
    'アタッカーズ': 'attackers',
    'premium(プレミアム)': 'premium',
    'プレミアム': 'premium',
    'kawaii*': 'kawaii',
    'カワイイ': 'kawaii',
}
STUDIO_SYMBOL_FOLD = str.maketrans({
    '’': "'", '‘': "'", '“': '"', '”': '"', '☆': '*', '★': '*', '♪': '*',
})


def studio_name_key(name: str) -> str:
    """保留字种的厂牌写法键；官方登记的品牌并写名共用一个键。"""
    folded = unicodedata.normalize('NFKC', strip_zero_width(name)).translate(STUDIO_SYMBOL_FOLD)
    key = re.sub(r'\s+', '', folded).casefold()
    return STUDIO_BRAND_NAMES.get(key, key)


def studio_identity_ids(connection: Connection, name: str) -> set[int]:
    """按规范名与登记别名取厂牌身份；多条命中由调用方保留冲突。"""
    key = studio_name_key(name)
    return {int(entity_id) for entity_id, spelling in connection.execute(
        "SELECT id,canonical_name FROM entity WHERE kind='studio' UNION ALL "
        "SELECT a.entity_id,a.alias FROM entity_alias a JOIN entity e ON e.id=a.entity_id "
        "WHERE e.kind='studio'") if studio_name_key(spelling) == key}


def _ingest_name_id(connection: Connection, kind: str, name: str) -> int | None:
    """入库名称先取精确规范名，再取唯一的登记身份。"""
    normalized = normalize_entity_name(name)
    exact = connection.execute("SELECT id FROM entity WHERE kind=? AND normalized_name=?",
                               (kind, normalized)).fetchone()
    if exact:
        return int(exact[0])
    if kind == 'studio':
        matches = studio_identity_ids(connection, name)
    elif kind in PERSON_ENTITY_KINDS:
        matches = {int(row[0]) for row in connection.execute(
            "SELECT DISTINCT e.id FROM entity e JOIN entity_alias a ON a.entity_id=e.id "
            "WHERE e.kind=? AND a.normalized_alias=? ORDER BY e.id LIMIT 2", (kind, normalized))}
    else:
        return None
    return next(iter(matches)) if len(matches) == 1 else None


KANA = re.compile(r"[぀-ゟ゠-ヿ]")
KANJI = re.compile(r"[一-鿿]")


#: 旧称写法的前缀。`旧・GRANZPRO` 与 `元・VERGER` 都是这一类，名字本体在前缀后面。
FORMER_PREFIX = re.compile(r"^\s*[旧元]\s*[・·]?\s*")


def split_name(raw: str) -> tuple[str, list[str]]:
    """(现用名, 别名列表)。括号外是现用名，括号里和括号后的都是别名。

    实测的三种形态都由这一条覆盖：`ACT(アクト)` 是读音，`Wish(元・GIRFY)` 是旧名，
    `SO MODEL AGENT(ソウ モデルエージェント)旧・Eightman Production` 两样都有，
    而旧名跟在右括号后面。
    """
    raw = raw.strip()
    parts = [part for part in re.split(r"[（(]([^）)]*)[）)]", raw) if part is not None]
    if len(parts) == 1:
        return raw, []
    canonical = parts[0].strip()
    aliases = []
    for part in parts[1:]:
        name = FORMER_PREFIX.sub("", part).strip()
        if name and name != canonical:
            aliases.append(name)
    return (canonical or raw), aliases


#: 人工驳回过的事务所，记在女优自己的元数据上。minnano-av 的「所属事務所」只记她现在
#: 签在谁名下，退役后转进个人经纪公司的人（三上悠亜 → 株式会社Miss），那一格写的就不是
#: AV 事务所。驳回记在人身上，事务所实体删掉以后这条判断还在；写元数据、建实体、补名册
#: 这几条路径都照它跳过，重跑哪一个都不会把它写回来。
AGENCY_REJECTED = "agency_rejected"


def agency_key(raw: str) -> str:
    """比对事务所用的键：拆掉括号里的读音和旧称，只比现用名。"""
    return normalize_entity_name(split_name(raw)[0])


def rejected_agencies(metadata: object) -> set[str]:
    """这个人驳回过的事务所，按 `agency_key` 给。"""
    items = metadata.get(AGENCY_REJECTED) if isinstance(metadata, dict) else None
    keys = {agency_key(str(item.get("name") or "")) for item in items or []
            if isinstance(item, dict)}
    keys.discard("")
    return keys


def agency_rejections(connection: Connection) -> dict[int, set[str]]:
    """performer id → 她驳回过的事务所键；只列有驳回记录的人。"""
    found: dict[int, set[str]] = {}
    for entity_id, raw in connection.execute(
            "SELECT id, metadata_json FROM entity WHERE kind='performer'"
            f" AND json_extract(metadata_json,'$.{AGENCY_REJECTED}') IS NOT NULL"):
        keys = rejected_agencies(json.loads(raw or "{}"))
        if keys:
            found[int(entity_id)] = keys
    return found


def name_rank(name: str) -> int:
    """这个写法对日文站有多可用，越小越先试。

    账本里 performer 的规范名是简体中文（`凉森玲梦`、`释爱丽丝`），日文站按它一个都
    搜不到；真正能用的日文写法在 `entity_alias` 里（`涼森れむ`、`釈アリス`）。实测拿
    规范名直接搜，12 位只命中 1 位；改用日文写法后同样 12 位全中。

    汉字加假名混排的是艺名本身，最可靠；纯假名是读音，能搜到但更容易撞名；纯汉字既可能
    是日文也可能是简体中文，放在后面；罗马字对日文站基本无效，排最后。
    """
    kana, kanji = bool(KANA.search(name)), bool(KANJI.search(name))
    if kana and kanji:
        return 0
    if kana:
        return 1
    if kanji:
        return 2
    return 3


#: 纯假名写法的短单名上限：`そら`、`みく`、`あかり` 这种只是名，不带姓。
SHORT_KANA_NAME = 3
#: 任何字种的短单名上限：`舞香`、`茜`、`栞` 同样只是名。
SHORT_NAME = 2


def is_short_single_name(name: str) -> bool:
    """只有名、没有姓的短写法：拿它去图库或站上找，命中的多半是另一个人。

    `伊吹彩` 的别名 `月島舞香` 在图库里两张都是她本人，短名 `舞香` 名下那两张跟她比
    只有 0.360、0.286（2026-09-25 实测），是另外的人。
    """
    text = re.sub(r"\s+", "", str(name or ""))
    if not text:
        return False
    if len(text) <= SHORT_NAME:
        return True
    return len(text) <= SHORT_KANA_NAME and all(KANA.match(char) for char in text)


def name_chain(canonical: str, aliases: list[str]) -> list[str]:
    """去重后按可用程度排序的候选名字，罗马字不进链。

    罗马字留着只会白跑一次往返，并且它落空后混进未取得，看起来像是「这个人查不到」，
    而实际上是「我们从没用她的日文名查过」。

    放在实体层而不是某个脚本里：`harvest_performer_links` 和 `rediscover_entity_links`
    都要用它。此前它住在前者、后者靠改 `sys.path` 反向 import——依赖门槛把它算成外部
    模块是对的，那种导入既依赖 path 顺序，工具也读不懂。
    """
    seen: list[str] = []
    for name in [canonical, *aliases]:
        name = (name or "").strip()
        if name and name not in seen and name_rank(name) < 3:
            seen.append(name)
    return sorted(seen, key=name_rank)


PERSON_ENTITY_KINDS = frozenset({"creator", "performer"})
INVALID_PERSON_ENTITY_NAMES = frozenset({"画像を拡大する"})


def collapse_repeated_entity_name(name: str) -> str:
    """把 ``姓名 姓名`` 这类完整重复串收敛为一次。

    这里只处理以空白分隔、前后两半完全相同的高置信错误；不会碰
    ``M M Produce``、无空白的叠字或带分隔符的内容标签。
    """
    original = str(name or "").strip()
    canonical = " ".join(original.split())
    parts = canonical.split(" ") if canonical else []
    half = len(parts) // 2
    if (len(parts) >= 2 and len(parts) % 2 == 0
            and [part.casefold() for part in parts[:half]]
            == [part.casefold() for part in parts[half:]]):
        return " ".join(parts[:half])
    return original


#: `Ako Momona (Kou Akemi, Mari Koizumi)` 这种一格装了三个艺名的写法。签名收得很紧：
#: 括号前有一个空格、括号内逗号分隔、整串只有拉丁字母与 `. ' -` 这几个名字里出现的标点。
#: 放宽任何一条都会误伤——`AV DEBUT（本物人妻）` 的括号里是厂牌消歧，`アスナ(SAO)` 是
#: 角色的出处，`快慢扳机（接稿中）` 是接稿状态，`kitty(1)` 是去重后缀。它们和艺名共用
#: 「名字后面跟一对括号」这个形状，只有「两侧都是罗马字人名」能把它们分开。
COMPOSITE_PERSON_NAME = re.compile(
    r"^([A-Za-z][A-Za-z .'-]*) \(([A-Za-z][A-Za-z .',-]*)\)$")


def split_composite_person_name(name: str) -> list[str]:
    """把 `现用名 (曾用名, 曾用名)` 拆成一个人的若干个名字，顺序保持原样、去重。

    r18.dev 的罗马字字段本身就是这个渲染格式，导入时一个字段写一行，于是整串成了一条
    别名。它做别名是死的：没有人叫「Ako Momona (Kou Akemi, Mari Koizumi)」，按任何一段
    都搜不到，选成统称更是不成立。同一条实体的假名和汉字写法本来就各自成行，缺的只是
    这几个罗马字。

    不匹配签名的原样返回一个元素，调用方不必先判断。
    """
    matched = COMPOSITE_PERSON_NAME.match(strip_zero_width(name).strip())
    if not matched:
        stripped = strip_zero_width(name).strip()
        return [stripped] if stripped else []
    parts = [matched.group(1).strip()]
    parts.extend(part.strip() for part in matched.group(2).split(","))
    unique: list[str] = []
    for part in parts:
        if part and part not in unique:
            unique.append(part)
    return unique


#: 规范名里要剥掉的零宽字符。上游译名夹带过 `‌斋藤亚美里`（performer）和
#: `比特ビット‌`（creator）：页面上和普通名字一模一样，但 `strip()` 不认它们不是
#: 空白，`normalized_name` 也就带着它，于是同一个人在账本里能存成两个实体、按名字搜
#: 一个都搜不到。U+200D（ZWJ）不在名单里——emoji 的家庭、职业序列靠它连字，剥掉会把
#: 创作者名字里的一个 emoji 拆成两三个。
ZERO_WIDTH = str.maketrans({"\u200b": None, "\u200c": None,
                            "\u2060": None, "\ufeff": None})


def strip_zero_width(name: str) -> str:
    return str(name or "").translate(ZERO_WIDTH)


def is_identity_alias(kind: str, name: str | None) -> bool:
    """人物别名只承载身份名称；画质、月份、合集注记、题材词与番号形态留在文件信息里。"""
    text = strip_zero_width(name).strip()
    if not text:
        return False
    if kind not in PERSON_ENTITY_KINDS:
        return True
    from .catalog_rules import normalise_code_key, release_code_from_text
    from .classification import creator_collection_base, is_structural_creator
    if creator_collection_base(text) != text or is_structural_creator(text):
        return False
    code = release_code_from_text(text)
    return not (code and normalise_code_key(text) == normalise_code_key(code))


def canonicalize_entity_name(kind: str, name: str | None) -> str:
    canonical = strip_zero_width(name).strip()
    if kind == 'creator':
        from .classification import is_structural_creator, is_repost_creator
        from .studio_sites import is_platform
        if is_platform(canonical) or is_structural_creator(canonical) or is_repost_creator(canonical):
            return ''
    if kind in PERSON_ENTITY_KINDS:
        canonical = collapse_repeated_entity_name(canonical)
        if canonical in INVALID_PERSON_ENTITY_NAMES:
            return ""
    return canonical


#: 规范名有一份扁平投影（ADR-0005）：女优落在 `asset_tag` 的 `演员:` 标签里，其余三种
#: 落在 `asset` 的同名列里。只改实体名不改这一份，卡片上还写着旧名、按旧名也照样查得到，
#: 资料页和卡片就各说各话了。
FLAT_COLUMN = {"studio": "studio", "creator": "creator", "series": "series"}


def rewrite_flat_projection(connection: Connection, kind: str, entity_id: int,
                            old_name: str, new_name: str) -> int:
    """规范名换写法之后，把扁平投影一并改过来，返回改动的资产数。

    资料页的统称选择器和账本清理脚本改的是同一件事，共用这一份：投影跟不上实体名的
    后果不是报错，是卡片和资料页各说各话，而且按旧名还照样搜得到。
    """
    column = FLAT_COLUMN.get(kind)
    if column:
        connection.execute(f"UPDATE asset SET {column}=? WHERE {column}=?",
                           (new_name, old_name))
        return connection.execute("SELECT changes()").fetchone()[0]
    rewritten = 0
    old_tag, new_tag = f"演员:{old_name}", f"演员:{new_name}"
    for item in connection.execute(
        "SELECT DISTINCT asset_id FROM asset_entity WHERE entity_id=?", (entity_id,)
    ):
        asset_id = int(item[0])
        # 置信度与来源跟着旧标签走：换的是写法，不是这条标注的可信程度。
        connection.execute(
            "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) "
            "SELECT asset_id,?,confidence,source FROM asset_tag WHERE asset_id=? AND tag=?",
            (new_tag, asset_id, old_tag))
        connection.execute("DELETE FROM asset_tag WHERE asset_id=? AND tag=?",
                           (asset_id, old_tag))
        if connection.execute("SELECT changes()").fetchone()[0]:
            rewritten += 1
    return rewritten


def _transfer_favorites(connection: Connection, source_id: int, target_id: int, stamp: str) -> None:
    """用户收藏随合并身份迁移，避免已删除的整数 ID 被新实体复用。"""
    target = connection.execute("SELECT kind,canonical_name FROM entity WHERE id=?", (target_id,)).fetchone()
    source_ids = {source_id, *(row[0] for row in connection.execute(
        "SELECT r.old_id FROM entity_redirect r WHERE r.target_id=?"
        " AND NOT EXISTS (SELECT 1 FROM entity e WHERE e.id=r.old_id)", (source_id,)))}
    for profile_id, raw in connection.execute("SELECT id,settings_json FROM profile").fetchall():
        try:
            settings = json.loads(raw)
        except (ValueError, TypeError):
            continue
        shortcuts = settings.get("libraryShortcuts") if isinstance(settings, dict) else None
        favorites = shortcuts.get("favorites") if isinstance(shortcuts, dict) else None
        if not isinstance(favorites, list):
            continue
        moved, seen, changed = [], set(), False
        for item in favorites:
            entity_id = item.get("entity_id") if isinstance(item, dict) else None
            if type(entity_id) is int and entity_id in source_ids:
                item = {**item, "entity_id": target_id, "key": f"entity:{target_id}",
                        "kind": target[0], "name": target[1]}
                entity_id, changed = target_id, True
            if type(entity_id) is int:
                if entity_id in seen:
                    changed = True
                    continue
                seen.add(entity_id)
            moved.append(item)
        if changed:
            shortcuts["favorites"] = moved
            connection.execute("UPDATE profile SET settings_json=?,updated_at=? WHERE id=?",
                               (json.dumps(settings, ensure_ascii=False), stamp, profile_id))


def merge_entity(
    connection: Connection, *, target_id: int, source_id: int,
    source_name: str, alias_source: str, now: str | None = None,
) -> dict:
    """把 source 实体并入 target，然后删除 source。调用方负责事务与备份。

    合并是不可逆的，只应在人工确认两个实体确为同一身份后调用——典型场景是
    同一位女优的旧艺名与现用艺名各自成了一个实体。

    `entity_external_ref` 的引用整批跟着走，同一个站点下的多条也一样（0032）：一位女优
    在 javdb 有两个演员页是常事，两边挂的作品不同，丢掉一条就少一个能点进去的页面。
    `UPDATE OR IGNORE` 与 `dropped_refs` 留作安全网，迁不动的条数在返回值里报告。

    source 的 id 留一条墓碑指向 target（0040 `entity_redirect`），指向 source 的墓碑一并
    改指 target，链条始终只有一跳；`resolve_entity_id` 按它把旧 id 解析到 target。
    """
    if source_id == target_id:
        raise ValueError("不能合并到同一份档案")
    target = connection.execute("SELECT kind FROM entity WHERE id=?", (target_id,)).fetchone()
    if target is None:
        raise ValueError(f"合并目标档案 {target_id} 不存在")
    target_kind = str(target[0])
    stamp = now or datetime.now(timezone.utc).isoformat()
    moved = {"assets": 0, "aliases": 0, "refs": 0, "links": 0, "terms": 0,
             "dropped_refs": 0, "memberships": 0, "members": 0, "labels": 0, "profiles": 0,
             "follows": 0, "feeds": 0, "discoveries": 0, "redirects": 0}
    from .entity_classification import transfer
    transfer(connection, source_id, target_id)

    # 被并入的名字本身留作别名，否则按旧名搜索会落空；人物的画质、合集与题材写法
    # 只留墓碑，旧 id 仍按墓碑解析。
    aliases = [(source_name, normalize_entity_name(source_name), alias_source, 1.0)]
    aliases += connection.execute(
        "SELECT alias,normalized_alias,source,confidence FROM entity_alias WHERE entity_id=?",
        (source_id,)).fetchall()
    for index, (alias, normalized, source, confidence) in enumerate(aliases):
        if not is_identity_alias(target_kind, alias):
            continue
        connection.execute(
            "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(?,?,?,?,?)", (target_id, alias, normalized, source, confidence))
        if index:
            moved["aliases"] += connection.execute("SELECT changes()").fetchone()[0]
    connection.execute("DELETE FROM entity_alias WHERE entity_id=?", (source_id,))

    connection.execute(
        "INSERT OR IGNORE INTO asset_entity"
        " SELECT asset_id,?,role,source,confidence,metadata_json,first_seen_at,last_seen_at"
        " FROM asset_entity WHERE entity_id=?", (target_id, source_id))
    moved["assets"] = connection.execute("SELECT changes()").fetchone()[0]
    connection.execute("DELETE FROM asset_entity WHERE entity_id=?", (source_id,))

    before = connection.execute(
        "SELECT count(*) FROM entity_external_ref WHERE entity_id=?", (source_id,)).fetchone()[0]
    connection.execute(
        "UPDATE OR IGNORE entity_external_ref SET entity_id=? WHERE entity_id=?",
        (target_id, source_id))
    left = connection.execute(
        "SELECT count(*) FROM entity_external_ref WHERE entity_id=?", (source_id,)).fetchone()[0]
    moved["refs"] = before - left
    moved["dropped_refs"] = left
    connection.execute("DELETE FROM entity_external_ref WHERE entity_id=?", (source_id,))

    connection.execute(
        "INSERT OR IGNORE INTO entity_link(entity_id,link_kind,label,url,hostname,"
        "is_sensitive,metadata_json,created_at,updated_at)"
        " SELECT ?,link_kind,label,url,hostname,is_sensitive,metadata_json,created_at,updated_at"
        " FROM entity_link WHERE entity_id=?", (target_id, source_id))
    moved["links"] = connection.execute("SELECT changes()").fetchone()[0]
    connection.execute("DELETE FROM entity_link WHERE entity_id=?", (source_id,))

    connection.execute(
        "INSERT OR IGNORE INTO entity_search_term(entity_id,term,purpose,source,created_at)"
        " SELECT ?,term,purpose,source,created_at FROM entity_search_term WHERE entity_id=?",
        (target_id, source_id))
    moved["terms"] = connection.execute("SELECT changes()").fetchone()[0]
    connection.execute("DELETE FROM entity_search_term WHERE entity_id=?", (source_id,))

    # 归属关系两个方向都要跟着走。做成员那一侧主键在 `member_id` 上，target 已有现役
    # 归属时 source 那条只能丢——一个人一条现役归属是这张表的语义，合并不该破例造出
    # 第二条。做事务所那一侧没有这个限制，成员整批改指向 target。
    connection.execute(
        "INSERT OR IGNORE INTO entity_membership(member_id,agency_id,source,confidence,checked_at)"
        " SELECT ?,agency_id,source,confidence,checked_at FROM entity_membership WHERE member_id=?",
        (target_id, source_id))
    moved["memberships"] = connection.execute("SELECT changes()").fetchone()[0]
    connection.execute("DELETE FROM entity_membership WHERE member_id=?", (source_id,))
    connection.execute(
        "UPDATE entity_membership SET agency_id=? WHERE agency_id=?", (target_id, source_id))
    moved["members"] = connection.execute("SELECT changes()").fetchone()[0]

    # label 的母公司（ADR-0049）同理：label 一侧一条现役，母公司一侧整批改指向。label 并进
    # 自己的母公司时那一条会变成自己指自己，表上的 CHECK 不收，先删掉。
    connection.execute(
        "INSERT OR IGNORE INTO label_maker(label_id,maker_id,source,confidence,checked_at)"
        " SELECT ?,maker_id,source,confidence,checked_at FROM label_maker"
        " WHERE label_id=? AND maker_id<>?", (target_id, source_id, target_id))
    connection.execute("DELETE FROM label_maker WHERE label_id=?", (source_id,))
    connection.execute("DELETE FROM label_maker WHERE label_id=? AND maker_id=?",
                       (target_id, source_id))
    connection.execute(
        "UPDATE label_maker SET maker_id=? WHERE maker_id=?", (target_id, source_id))
    moved["labels"] = connection.execute("SELECT changes()").fetchone()[0]
    # 上级可以一级套一级（ADR-0051）。把链条顶上那家并进底下某一级时，底下那级的上级
    # 会绕回它自己：它吞掉的就是链条的顶层，它自己那一行上级该删。新造的环一定经过 target。
    if connection.execute(
            "WITH RECURSIVE up(id,depth) AS (SELECT maker_id,1 FROM label_maker WHERE label_id=?"
            " UNION ALL SELECT lm.maker_id,up.depth+1 FROM up JOIN label_maker lm"
            " ON lm.label_id=up.id WHERE up.id<>? AND up.depth<64)"
            " SELECT 1 FROM up WHERE id=? LIMIT 1", (target_id, target_id, target_id)).fetchone():
        connection.execute("DELETE FROM label_maker WHERE label_id=?", (target_id,))

    # 女优资料一人一行（ADR-0067）：target 已有就留它的，没有才接 source 那一行。
    connection.execute(
        "UPDATE OR IGNORE performer_profile SET entity_id=? WHERE entity_id=?",
        (target_id, source_id))
    moved["profiles"] = connection.execute("SELECT changes()").fetchone()[0]
    connection.execute("DELETE FROM performer_profile WHERE entity_id=?", (source_id,))

    # 关注与订阅绑在人身上（0018、0034）。表上声明的 SET NULL / CASCADE 在不开外键的连接上
    # 不执行，不搬就悬空，合并后的 `foreign_key_check` 跟着不为 0。
    connection.execute(
        "UPDATE follow_source SET entity_id=? WHERE entity_id=?", (target_id, source_id))
    moved["follows"] = connection.execute("SELECT changes()").fetchone()[0]
    connection.execute(
        "UPDATE feed_source SET entity_id=? WHERE entity_id=?", (target_id, source_id))
    moved["feeds"] = connection.execute("SELECT changes()").fetchone()[0]
    connection.execute(
        "INSERT OR IGNORE INTO feed_discovery_entity(discovery_id,entity_id)"
        " SELECT discovery_id,? FROM feed_discovery_entity WHERE entity_id=?",
        (target_id, source_id))
    moved["discoveries"] = connection.execute("SELECT changes()").fetchone()[0]
    connection.execute("DELETE FROM feed_discovery_entity WHERE entity_id=?", (source_id,))

    _transfer_favorites(connection, source_id, target_id, stamp)
    # 墓碑先写、先压平，再删 source：删 source 时触发器会清掉仍指向它的墓碑。
    connection.execute(
        "UPDATE entity_redirect SET target_id=? WHERE target_id=?", (target_id, source_id))
    moved["redirects"] = connection.execute("SELECT changes()").fetchone()[0]
    connection.execute(
        "INSERT OR REPLACE INTO entity_redirect(old_id,target_id,source,merged_at)"
        " VALUES(?,?,?,?)", (source_id, target_id, alias_source, stamp))

    connection.execute("UPDATE entity SET updated_at=? WHERE id=?", (stamp, target_id))
    connection.execute("DELETE FROM entity WHERE id=?", (source_id,))
    return moved


def resolve_entity_id(connection: Connection, entity_id: int) -> int | None:
    """把一个实体 id 解析成现在的实体：活实体原样返回，并入过的按墓碑给目标，都不是给 None。

    先查活实体：`entity.id` 会复用，墓碑的 id 可能已经是另一条新实体。目标被删时墓碑由
    触发器清掉；连接不开外键时的悬空行在这里也按「查不到」处理。
    """
    row = connection.execute(
        "SELECT id,0 AS hop FROM entity WHERE id=?"
        " UNION ALL SELECT r.target_id,1 FROM entity_redirect r"
        " JOIN entity t ON t.id=r.target_id WHERE r.old_id=? ORDER BY hop LIMIT 1",
        (int(entity_id), int(entity_id))).fetchone()
    return int(row[0]) if row else None


def _ref_is_free(connection: Connection, entity_id: int, provider: str,
                 external_kind: str, external_id: str) -> bool:
    """这个实体在这家来源下还没被别的 id 占着。

    表上 `UNIQUE(entity_id, provider, external_kind)` 说的是「一个实体在一家来源只有一个
    id」，而插入语句的 `ON CONFLICT` 只认主键那一组，撞上这个索引就是一个未捕获的
    `IntegrityError`，把整笔事务连同同批次其它字段一起带走。

    撞得上是因为艺名：实体 8004「桥本有菜」名下挂着「橋本ありな」「新ありな」等 8 个
    别名，r18dev 给前者的 id 是 1032668、给后者是 1078619，账本里已经存着后者。人是一个，
    来源那边是两个页面。先到的那条留着——它才是账本里其它引用和历史指向的那一个。
    """
    held = connection.execute(
        "SELECT external_id FROM entity_external_ref "
        "WHERE entity_id=? AND provider=? AND external_kind=?",
        (entity_id, provider, external_kind)).fetchone()
    return held is None or str(held[0]) == external_id


CREATOR_ATTRIBUTION = 'creator-attribution'


def creator_directory_key(name: str, directory: str) -> str:
    """目录级否定的复核键：该目录及其子目录里的文件都不归这个账号。"""
    folded = str(directory).replace('/', '\\').rstrip('\\').casefold()
    return f'dir:{normalize_entity_name(name)}|{folded}'


def _directories_of(path) -> list[str]:
    """文件所在目录及其全部祖先目录，由浅到深。"""
    parts = [part for part in re.split(r'[\\/]', str(path or '')) if part][:-1]
    return ['\\'.join(parts[:depth]) for depth in range(1, len(parts) + 1)]


def derived_directory_rejections(connection) -> list[dict]:
    """作品级否定覆盖了整个目录子树时，提议同一账号在该目录上的目录级否定。

    子树内每一件有效作品都已逐个否定才提议，且只留最上层的目录：目录级否定会拦下
    以后进入该目录的新文件，但不能顺带否定任何一件还没被否定的作品。目录路径里必须有
    一层以该账号命名：目录归属正是从这一层来的，再往上的分类目录（如整个卖家目录）
    即使眼下只装着被否定的作品，也不能替以后放进去的别的账号做决定。
    """
    from .classification import creator_collection_base
    rejected: dict[str, set[int]] = {}
    notes: dict[str, set[str]] = {}
    existing = set()
    for key, status, note in connection.execute(
            'SELECT item_key,status,note FROM review_decision WHERE category=?', (CREATOR_ATTRIBUTION,)):
        existing.add(key)
        head, separator, name = key.partition(':')
        if status == 'rejected' and separator and head.isdigit():
            rejected.setdefault(name, set()).add(int(head))
            notes.setdefault(name, set()).add(note)
    members: dict[str, set[int]] = {}
    spelled: dict[str, str] = {}
    if rejected:
        for asset_id, path in connection.execute("SELECT id,path FROM asset WHERE COALESCE(disposal,'')<>'vanished'"):
            for directory in _directories_of(path):
                members.setdefault(directory.casefold(), set()).add(asset_id)
                spelled.setdefault(directory.casefold(), directory)
    def names_account(folded, name):
        return any(name in {normalize_entity_name(part), normalize_entity_name(creator_collection_base(part))}
                   for part in folded.split('\\'))

    proposals = []
    for name, ids in sorted(rejected.items()):
        kept: list[str] = []
        for folded in sorted((folded for folded, held in members.items() if held <= ids),
                             key=lambda folded: (folded.count('\\'), folded)):
            if any(folded.startswith(top + '\\') for top in kept) or not names_account(folded, name):
                continue
            kept.append(folded)
            key = creator_directory_key(name, spelled[folded])
            if key not in existing:
                proposals.append({'item_key': key, 'name': name, 'directory': spelled[folded],
                                  'subtree_assets': len(members[folded]), 'name_rejections': len(ids),
                                  'note': f'由 {len(members[folded])} 条作品级否定派生：' + '；'.join(sorted(notes[name]))})
    return proposals


def apply_directory_rejections(connection, proposals, *, reviewer: str) -> int:
    """写入目录级否定；已有同键复核决定的不覆盖。"""
    stamp = datetime.now(timezone.utc).isoformat()
    return sum(connection.execute(
        "INSERT OR IGNORE INTO review_decision(category,item_key,status,reviewer,note,updated_at) "
        "VALUES(?,?,'rejected',?,?,?)", (CREATOR_ATTRIBUTION, row['item_key'], reviewer, row['note'], stamp)).rowcount
        for row in proposals)


def _creator_attribution_rejected(connection, names, asset_id, path) -> bool:
    """单个作品的否定与祖先目录的否定都生效，同目录新文件不再归给被否定的账号。"""
    names = {normalize_entity_name(name) for name in names if name}
    keys = [f'{asset_id}:{name}' for name in names]
    keys += [creator_directory_key(name, directory) for directory in _directories_of(path) for name in names]
    return bool(keys) and connection.execute(
        "SELECT 1 FROM review_decision WHERE category=? AND status='rejected' AND item_key IN ("
        + ','.join('?' for _ in keys) + ") LIMIT 1", (CREATOR_ATTRIBUTION, *keys)).fetchone() is not None


def _creator_ingest_identity(connection, name, asset_id):
    """已有账号承接集合目录；已确认厂牌不进入发布账号关系。"""
    from .classification import creator_collection_base, creator_release_identifier
    asset = connection.execute('SELECT path,name,code FROM asset WHERE id=?',(asset_id,)).fetchone()
    if asset and creator_release_identifier(name,path=asset[0] or '',filename=asset[1] or '',code=asset[2])[0] == 'release_identifier':
        return ''
    original = name
    base = creator_collection_base(name)
    if base != name:
        held = connection.execute("SELECT canonical_name FROM entity WHERE kind='creator' AND normalized_name=?",
                                  (normalize_entity_name(base),)).fetchone()
        if held:
            name = str(held[0])
    if (connection.execute("SELECT 1 FROM sqlite_schema WHERE name='review_decision'").fetchone()
            and _creator_attribution_rejected(connection, (original, base, name), asset_id, asset[0] if asset else '')):
        return ''
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='entity_classification'").fetchone():
        from .entity_classification import trusted_sql
        normalized = normalize_entity_name(name)
        normalized_base = normalize_entity_name(base)
        if connection.execute("SELECT 1 FROM entity e JOIN entity_classification ec ON ec.entity_id=e.id "
            "WHERE e.kind IN ('creator','studio') AND ec.facet='account_role' "
            "AND ec.value='studio' AND " + trusted_sql('ec') + " "
            "AND (e.normalized_name IN (?,?) OR EXISTS (SELECT 1 FROM entity_alias al "
            "WHERE al.entity_id=e.id AND al.normalized_alias IN (?,?)))",
            (normalized, normalized_base, normalized, normalized_base)).fetchone():
            return ''
    return name


def upsert_asset_entity(
    connection: Connection, *, kind: str, name: str | None, asset_id: int,
    role: str, source: str, confidence: float = 1.0,
    external_provider: str | None = None, external_id: str | int | None = None,
    metadata: dict | None = None, now: str | None = None,
    update_entity_metadata: bool = True,
) -> int | None:
    """写入规范实体关系；调用方负责事务和兼容投影。"""
    canonical = canonicalize_entity_name(kind, name)
    if kind == 'creator' and not source.startswith(('user:','review:')):
        selected = _creator_ingest_identity(connection,canonical,asset_id)
        if selected != canonical:
            metadata = {**(metadata or {}),'directory_name':canonical}
            update_entity_metadata = False
            canonical = selected
    if not canonical:
        return None
    if kind == 'creator' and not source.startswith(('user:', 'review:')):
        merged_people = connection.execute(
            "SELECT DISTINCT e.id,e.canonical_name FROM entity e JOIN entity_alias al ON al.entity_id=e.id "
            "JOIN entity_redirect r ON r.target_id=e.id AND r.source=al.source "
            "WHERE e.kind='performer' AND al.normalized_alias=? AND al.source='user:identity-merge:creator' "
            "ORDER BY e.id LIMIT 2", (normalize_entity_name(canonical),)).fetchall()
        if len(merged_people) == 1:
            metadata = {**(metadata or {}), 'directory_name': canonical}
            canonical = str(merged_people[0][1])
            kind = 'performer'
            role = 'performer' if role == 'creator' else role
            update_entity_metadata = False
    stamp = now or datetime.now(timezone.utc).isoformat()
    normalized = normalize_entity_name(canonical)
    payload = json.dumps(metadata or {}, ensure_ascii=False)
    entity_id = None
    if external_provider and external_id is not None:
        matched = connection.execute(
            "SELECT e.id FROM entity_external_ref x JOIN entity e ON e.id=x.entity_id "
            "WHERE x.provider=? AND x.external_kind=? AND x.external_id=? AND e.kind=?",
            (external_provider, kind, str(external_id), kind),
        ).fetchone()
        entity_id = int(matched[0]) if matched else None
    if entity_id is None:
        entity_id = _ingest_name_id(connection, kind, canonical)
    if entity_id is None:
        connection.execute(
            "INSERT INTO entity(kind,canonical_name,normalized_name,metadata_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (kind, canonical, normalized, payload, stamp, stamp),
        )
        entity_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    elif update_entity_metadata:
        if kind in {'studio', 'agency'}:
            held = connection.execute('SELECT metadata_json FROM entity WHERE id=?', (entity_id,)).fetchone()
            previous = json.loads(held[0] or '{}')
            incoming = json.loads(payload)
            if 'company_profile' in previous:
                incoming['company_profile'] = previous['company_profile']
            payload = json.dumps(incoming, ensure_ascii=False)
        connection.execute(
            "UPDATE entity SET metadata_json=?,updated_at=? WHERE id=?",
            (payload, stamp, entity_id),
        )
    connection.execute(
        """INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence,
                                     metadata_json,first_seen_at,last_seen_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(asset_id,entity_id,role,source) DO UPDATE SET
             confidence=excluded.confidence,
             metadata_json=excluded.metadata_json,
             last_seen_at=excluded.last_seen_at""",
        (asset_id, entity_id, role, source, confidence, payload, stamp, stamp),
    )
    if external_provider and external_id is not None and _ref_is_free(
            connection, entity_id, external_provider, kind, str(external_id)):
        connection.execute(
            """INSERT INTO entity_external_ref(
                 entity_id,provider,external_kind,external_id,metadata_json,last_synced_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(provider,external_kind,external_id) DO UPDATE SET
                 entity_id=excluded.entity_id,
                 metadata_json=excluded.metadata_json,
                 last_synced_at=excluded.last_synced_at""",
            (entity_id, external_provider, kind, str(external_id), payload, stamp),
        )
    return int(entity_id)


def resolve_entity(connection: Connection, kind: str, name: str):
    """先取精确规范名，再取唯一别名；撞名时不任意指向另一位。

    别名撞名返回 None 而不是随便挑一个：指错实体会把作品挂到另一个人名下，
    那是要人工复核才能发现的错误。
    """
    canonical = connection.execute(
        "SELECT e.* FROM entity e WHERE e.kind=? AND e.canonical_name=? LIMIT 1",
        (kind, name),
    ).fetchone()
    if canonical:
        return canonical
    if kind == 'studio':
        ids = studio_identity_ids(connection, name)
        return connection.execute('SELECT * FROM entity WHERE id=?', (next(iter(ids)),)).fetchone() if len(ids) == 1 else None
    aliases = connection.execute(
        "SELECT DISTINCT e.* FROM entity e JOIN entity_alias a ON a.entity_id=e.id "
        "WHERE e.kind=? AND a.alias=? ORDER BY e.id LIMIT 2",
        (kind, name),
    ).fetchall()
    return aliases[0] if len(aliases) == 1 else None
