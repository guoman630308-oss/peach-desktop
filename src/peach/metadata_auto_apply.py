"""元数据字段候选的判据与落库：复核页批准与处理任务自动落库共用这一份。

判据（ADR-0018/0025/0029/0033/0034/0035）与写入映射（ADR-0005 的字段归属）是领域规则，
不是 Web 行为：命令行首扫、Web 后台处理任务和复核页的「通过」按钮走的必须是同一套。
它们此前住在 `web_review` 里，于是 `cli` 与处理任务为了自动落库反向 import 了 web 层。

`auto_apply_metadata` 由调用方注入已经在用的 `LedgerDatabase`：进程内写锁是实例级的，
自己再 new 一个就等于在同一个进程里开第二条写入路径，与页面上的写入互相 `database is
locked`。批量落库按 `AUTO_APPLY_BATCH` 条一个事务提交，中途被停止或失败时已经判完的
那些不回滚重来。
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

from .catalog_rules import (
    code_release_date,
    current_tags,
    fill_masked_title,
    is_jav_code,
    is_korean_mib_code,
    normalise_code_key,
    release_code_from_filename,
    same_release_code,
    strip_scrape_mark,
)
from .entities import (
    canonicalize_entity_name,
    collapse_repeated_entity_name,
    is_short_single_name,
    normalize_entity_name,
    resolve_entity,
    upsert_asset_entity,
)
from .field_owners import (
    auto_owner,
    check_revision,
    is_protected,
    owner_of,
    write_owned_fields,
)
from .genre_decisions import load_genre_decisions
from .genre_taxonomy import UNMAPPED, genres_in_warning, resolve_genre
from .metadata import identifies_code
from .metadata_alias_resolve import (
    is_planning_alias as _is_planning_alias,
    register_planning_alias,
    resolve_planning_alias,
    stage_name as _stage_name,
)
from .metadata_policy import (
    CHAIN_OFFICIAL, FALLBACK_SOURCES, LOCAL_NFO_SOURCE, SOURCE_SPECS,
    blacklisted, chain_rank, preferred_tag_source, source_tier,
)
from .review_csv import read_candidates
from .sources import fc2

#: 一个写事务里最多落多少条。整批一个事务在本机实测是几千条候选压着写锁不放，
#: 页面上任何一次写入都得等它跑完；分批之后每段只占一小会儿，被停止时也只丢这一段。
AUTO_APPLY_BATCH = 200

REVIEW_APPLY_LIMIT = 500


#: 多值字段在账本里是实体关系，不是 `asset` 上的列。
_MULTI_VALUE_ROLES = {"performers": "performer", "tags": "tag"}

#: `_codes_matching` 一条查询里最多带几个宽查键；SQLite 表达式树深度上限是 1000。
_LIKE_CHUNK = 200


def _codes_matching(connection, codes: list[str], columns: str) -> list:
    """这些番号名下的资产行。账本里的写法未必和候选件一致。

    账本存的是编目后的规范写法，候选件写的是来源站上的那个号：Tokyo-Hot 的 `n0762`
    在账本里是 `TOKYO-HOT-N0762`，按字符串比一条都对不上（2026-09-22 实测 n0762 的
    标题、演员、系列、发行日期四行全部因此判成「账本里没有这个番号」，停在人工队列，
    而账本里的别名早就把「藤原遼子／森沢かな」和「東熱／東京熱」各自认作一个）。
    `normalise_code_key` 两边归一化之后是同一个键，它也正是封面缓存和复核页在用的那个。

    精确写法与同号异写一次查齐：归一化后的键是账本写法的子串（`n0762` ⊂
    `TOKYO-HOT-N0762`），拿它把范围收小，再逐条按归一化判等。精确命中一条也照样找
    异写：`n0780` 与 `TOKYO-HOT-N0780` 是同一部作品的两个文件，前者叫 `Tokyo-Hot.mp4`
    读不出番号，后者叫 `n0780.mkv`，只拿前者判「文件名认不认得出这个番号」，整组就停在
    人工队列（2026-09-27 实测 10 行）。精确查本来就是全表扫描，单条候选并进同一条查询
    不多花；整队列刷新现值时一次几百个番号，宽查按 `_LIKE_CHUNK` 个键一段，一长串 OR
    会撞上 SQLite 的表达式深度上限。
    """
    wanted = [code for code in dict.fromkeys(code.strip() for code in codes) if code]
    if not wanted:
        return []
    exact = {code.upper() for code in wanted}
    keys = sorted({normalise_code_key(code) for code in wanted} - {""})
    marks = ",".join(["upper(?)"] * len(wanted))
    found = []
    # 每一行只在它的归一化键所在那一段收下，精确写法每段都查，也不会重复收。
    for start in range(0, max(len(keys), 1), _LIKE_CHUNK):
        chunk = set(keys[start:start + _LIKE_CHUNK])
        likes = "".join(" OR upper(code) LIKE '%'||upper(?)||'%'" for _key in sorted(chunk))
        for row in connection.execute(
                f"SELECT {columns} FROM asset WHERE medium='video' "
                f"AND disposal IS NULL "
                f"AND (upper(trim(code)) IN ({marks}){likes}) ORDER BY id",
                [*wanted, *sorted(chunk)]):
            key = normalise_code_key(str(row["code"] or ""))
            if key in chunk or (not start and not key
                                and str(row["code"] or "").strip().upper() in exact):
                found.append(row)
    return found


def refresh_current_values(connection, rows: list[dict]) -> None:
    """把候选行的「账本现值」换成账本此刻的值。

    候选件是抓取那一刻写的，`current_value` 也就停在那一刻。之后落过库、合并过实体、
    改过名的，这一栏都不跟着动：实测 300 行队列里有 85 行写着空而账本早有值（标签 29、
    演员 22、标题 13、厂牌 8、系列 8、发行日期 5）。队列拿它判「补空还是冲突」，自动
    落库拿它判「这里是不是空的」——过期一份，两处一起错，而且错的方向是往库里写。

    资产数 `videos` 同理按账本现算：候选件那一列是生成器顺手填的，fc2-cast 那一份
    根本没填，复核页就把账本里有资产的番号写成「0 个同番号资产」。

    账本里找不到这个番号的资产时保留候选件那一份：那种行本来就轮不到按现值判。
    """
    codes = [code for code in dict.fromkeys(
        str(row.get(key) or "").strip() for row in rows for key in ("code", "query")) if code]
    if not codes:
        return
    columns = sorted(set(METADATA_FIELD_COLUMNS.values()))
    by_asset: dict[int, dict] = {}
    by_code: dict[str, list[int]] = defaultdict(list)
    for asset in _codes_matching(connection, codes, f"id,code,{','.join(columns)}"):
        by_asset[int(asset["id"])] = dict(asset)
        by_code[normalise_code_key(asset["code"])].append(int(asset["id"]))
    if not by_asset:
        return
    ids = list(by_asset)
    id_marks = ",".join("?" * len(ids))
    linked: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for asset_id, role, name in connection.execute(
            f"SELECT ae.asset_id,ae.role,e.canonical_name FROM asset_entity ae"
            f" JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id IN ({id_marks})"
            " AND ae.role IN ('performer','tag') ORDER BY e.canonical_name", ids):
        linked[int(asset_id)][str(role)].append(str(name))

    for row in rows:
        field = str(row.get("field") or "").strip()
        raw = str(row.get("asset_id") or "").strip()
        # 范围不一致就会
        # 出现「按九卷里空着的那一卷判成空位，落库却把第十卷已有的值一起改掉」。
        if str(row.get("asset_path") or "").strip() and raw.isdigit() and int(raw) in by_asset:
            targets = [int(raw)]
        else:
            key = normalise_code_key(str(row.get("code") or row.get("query") or ""))
            targets = by_code.get(key, [])
        if not targets:
            continue
        row["videos"] = str(len(targets))
        column = METADATA_FIELD_COLUMNS.get(field)
        if column:
            # 同番号多卷时任取有值的那一卷：落库本来就是整组一起写。
            row["current_value"] = next(
                (value for target in targets
                 if (value := str(by_asset[target][column] or "").strip())), "")
        elif field in _MULTI_VALUE_ROLES:
            role = _MULTI_VALUE_ROLES[field]
            row["current_value"] = "、".join(dict.fromkeys(
                name for target in targets for name in linked[target][role]))


def _unmapped_genres(candidate: dict) -> list[str]:
    """候选里那批未收录原文。

    `unmapped_genres` 是结构化的那份。2026-09-11 之前写下的候选文件只有 `warnings`
    里那句中文提示，而复核页要能在它们身上就把 genre 收录进来——让用户先重抓一遍全库
    才有按钮可点，等于这个功能对现有队列不存在。反解只认 `genre_taxonomy` 自己拼出
    的那个格式，两边写在同一个文件里。
    """
    structured = [str(item).strip() for item in candidate.get("unmapped_genres") or []]
    if any(structured):
        return [item for item in structured if item]
    for warning in candidate.get("warnings") or []:
        recovered = genres_in_warning(warning)
        if recovered:
            return recovered
    return []


def _fold_genre_decisions(field: str, candidate: dict, decided: dict[str, str | None]) -> dict:
    """把用户已经收录的 genre 折进这条候选，剩下的以结构化形式交给页面。

    候选文件是抓取那一刻的产物，收录一个 genre 不重抓全库；判定要写进账本的也是折过
    之后的这份值，否则页面上标签已经多出一个、批准写下去的还是旧的那几个。
    未决的那些从 `warnings` 里那句话挪到 `unmapped_genres`：页面要拿它们做按钮，
    留一句「来源还有 2 个未收录 genre」只能读，读完还是没有出口。

    静态表也要重查一遍，不只查用户的决定：候选文件停在抓取那一刻，而 `CONTENT_GENRES`
    与 `NON_CONTENT_GENRES` 一直在补。
    """
    if field != "tags":
        return candidate
    unmapped = _unmapped_genres(candidate)
    if not unmapped:
        return candidate
    values = [str(value) for value in candidate.get("value") or []]
    remaining: list[str] = []
    for genre in unmapped:
        tag = resolve_genre(genre, decided)
        if tag == UNMAPPED:
            remaining.append(genre)
            continue
        if tag and tag not in values:
            values.append(tag)
    return {**candidate, "value": values, "display_value": "、".join(values),
            "unmapped_genres": remaining,
            "warnings": [warning for warning in candidate.get("warnings") or []
                         if not genres_in_warning(warning)]}


def _split_multi(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[、,，/|]", value or "") if part.strip()]


def _entity_identity_key(connection, kind: str, name: str) -> frozenset:
    """把一个名字折成身份键：能解析到实体的用实体 id，解析不到的保留规范化原名。

    来源常把两种写法并在一串里——演员是「现名（旧名）」，厂牌是
    `プレステージプレミアム(PRESTIGE PREMIUM)` 这种日英并写。整串匹配不到实体，
    括号两边各自却都是已登记别名，所以拆开再查一次。没有一个变体命中就保留原名，
    真正的冲突不会被这一步吞掉。
    """
    variants = [name.strip()]
    match = re.fullmatch(r"\s*([^（(]+?)\s*[（(]([^）)]+)[）)]\s*", name)
    if match:
        variants.extend(part.strip() for part in match.groups() if part.strip())
    resolved = {
        row["id"] for variant in variants
        if (row := resolve_entity(connection, kind, variant))
    }
    return frozenset(resolved) if resolved else frozenset({normalize_entity_name(name)})


def _performer_identity_keys(connection, names: list[str]) -> frozenset:
    """把演员名折成身份键：能解析到实体的用实体 id，解析不到的保留原名。

    r18dev 给的是日文名，而账本规范名多数已本地化成中文——`桃谷エリカ` 与
    `桃谷绘里香` 实测就是同一条实体（日文名早已登记为别名）。按字符串比会把
    这类候选全判成「有差异」，批准反而把规范名倒退成别名。
    """
    keys = set()
    for name in names:
        keys.update(_entity_identity_key(connection, "performer", name))
    return frozenset(keys)


#: 可以不经人判断直接落库的字段（ADR-0025 扩到 P0 全字段）。
#:
#: 标签是多值集合，「取值一致」对它的含义是整份集合逐字相同，而不是逐个标签比对；
#: `_candidate_value_key` 拿的就是拼好的那一串，所以这条判据照样成立。三张表都不认的
#: genre 不再扣住整条候选：认得出的那些先落库，剩下的词记进 `pending_genres` 单独等
#: 人收录（ADR-0038）。
AUTO_APPLY_FIELDS = frozenset({
    "title", "original_title", "performers", "studio", "series", "release_date", "tags",
})


def _planning_alias_resolver(connection, row: dict, snapshot_root):
    """这一行的企划名义解析器：`艺名写法 -> (主艺名, 证据来源)`，解不出返回 None。

    按行缓存。同一行里多个来源常给同一个称呼，每次都去翻一遍快照目录和 `entity_alias`
    是白做的重复 I/O，而这一层在整库落库时每条候选都要走。
    """
    codes = (row.get("code"), row.get("query"))
    cache: dict[str, tuple[str, str] | None] = {}

    def resolve(name: str):
        if name not in cache:
            cache[name] = resolve_planning_alias(
                connection, name, metadata_root=snapshot_root, codes=codes)
        return cache[name]

    return resolve


def _offers_another_stage_name(candidate: dict) -> bool:
    """这条候选给的是不是另一个主艺名；整条都是企划名义就不是。

    空候选照旧算数：那是「来源说这里没人」，与「来源换了个称呼」是两回事。
    """
    names = _split_multi(str(candidate.get("display_value") or ""))
    return not names or not all(_is_planning_alias(name) for name in names)


def _stage_names(candidate: dict, resolve=None) -> tuple[list[str] | None, dict]:
    """整条出演者候选剪成艺名列表，外加解出来的企划名义对照表。

    返回 `(艺名列表, {艺名: (企划名义, 证据来源)})`。剪不出艺名、`resolve` 又给不出
    主艺名的，整条回人工——`(None, {})`。

    `resolve` 缺省是 None，也就是「不走企划名义那条路」。这是有意的默认：解析要读
    本机快照目录，没人把那个目录交给这一层时就不该自己去猜一个（ADR-0038）。
    """
    people = candidate.get("value")
    if not isinstance(people, list) or not people:
        return None, {}
    names: list[str] = []
    resolved: dict[str, tuple[str, str]] = {}
    for person in people:
        if not isinstance(person, dict):
            return None, {}
        raw = str(person.get("name") or "")
        name = _stage_name(raw)
        if name is None:
            # 剪得出边界的那些照旧按 ADR-0025 落剪好的艺名：`桜井奈々 28歳 某企業広報担当`
            # 的艺名就是 `桜井奈々`，不必去别处求证。这条路只管剪完仍认不出的那些。
            found = resolve(raw) if resolve is not None else None
            if found is None:
                return None, {}
            name, source = found
            resolved[name] = (raw.strip(), source)
        names.append(name)
    return (names or None), (resolved if names else {})


def _names_nobody(candidate: dict, resolve=None) -> bool:
    """这条出演者候选里是不是一个艺名都没有：每一位都剪不出，企划名义也解不出。

    `神山ももか、かえでちゃん` 这种一半是人名的不算：前一位是真名，整条丢掉就把她一起
    丢了，那种交人工。空列表也不算，它说的是「来源说这里没人」。
    """
    people = candidate.get("value")
    if not isinstance(people, list) or not people:
        return False
    for person in people:
        if not isinstance(person, dict):
            return False
        raw = str(person.get("name") or "")
        if _stage_name(raw) is not None:
            return False
        if resolve is not None and resolve(raw) is not None:
            return False
    return True


#: FC2 出演者行的自动否决写在这个规则名下（ADR-0079）。撤回脚本按它整批认出来。
FC2_DESCRIPTIVE_REJECTION_RULE = "adr-0079-fc2-descriptive-performer"

#: FC2 出演者按敬称算作一致的落库，规则名以它开头（ADR-0086）。
FC2_HONORIFIC_AGREEMENT_RULE = "adr-0086-fc2-honorific"
_HONORIFIC = "ちゃん"


def _names_only_itself(connection, name: str) -> bool:
    """这个写法在账本里没有别的主人：解析不到实体，或解析到的就是以它为规范名的那一条。

    `夏希` 登记成了 `夏希南` 的别名，按它落库就是把这部片挂到 `夏希南` 名下。
    """
    known = resolve_entity(connection, "performer", name)
    return known is None or (normalize_entity_name(str(known["canonical_name"]))
                             == normalize_entity_name(name))


def _with_honorific_echoes(connection, code: str, field: str, candidates: list[dict],
                           resolve=None) -> tuple[list[dict], list[dict]]:
    """FC2 出演者里 javdb 的 `みおちゃん` 与镜像站的 `みお` 算两家一致（ADR-0086）。

    返回 `(候选, 按敬称折过的那些 {source, value})`。只折这样的候选：整条只有一位，写法是
    `X` 加 `ちゃん`，同一行另有一家整条就是 `X`，`X` 本身剪得出艺名，账本里 `X` 也没有别的
    主人（`_names_only_itself`）。单独一家的 `みおちゃん` 不折：没有另一家给出同一个名字，
    敬称前面那两个字是不是她的艺名就无从对照，那一行照旧归 ADR-0079 判。

    折过的候选只留名字：站上编号属于那家站自己的演员页，挂到按短名共用的实体上不成立。
    """
    if field != "performers" or not _FC2_CODE.match(code):
        return candidates, []
    plain = set()
    for candidate in candidates:
        names, _resolved = _stage_names(candidate, resolve)
        if names is not None and len(names) == 1:
            plain.add(names[0])
    folded: list[dict] = []
    echoes: list[dict] = []
    for candidate in candidates:
        people = candidate.get("value")
        raw = (str(people[0].get("name") or "").strip()
               if isinstance(people, list) and len(people) == 1 and isinstance(people[0], dict)
               else "")
        name = raw.removesuffix(_HONORIFIC)
        if (name != raw and name in plain and _stage_name(name) == name
                and _names_only_itself(connection, name)):
            folded.append({**candidate, "value": [{"name": name}], "display_value": name,
                           "honorific_value": raw})
            echoes.append({"source": str(candidate.get("source") or "").strip(), "value": raw})
        else:
            folded.append(candidate)
    return folded, echoes


def _honorific_agreement(candidates: list[dict]) -> dict:
    """取值一致的那组里按敬称折过的几家，写成落库记录的 `honorific_agreed` 一项。"""
    folded = [{"source": str(c.get("source") or "").strip(), "value": c["honorific_value"]}
              for c in candidates if c.get("honorific_value")]
    return {"honorific_agreed": folded} if folded else {}


def _without_descriptive_performers(code: str, field: str, candidates: list[dict],
                                    resolve=None) -> tuple[list[dict], list[dict]]:
    """FC2 出演者候选里一个艺名都没有的那些不算证据（ADR-0079），剩下的照常比。

    返回 `(留下的候选, 被剔掉的候选)`。全都剔光时原样返回：那一行交给自动否决判，
    不在这里把它变成「没有候选」。

    剩下的里有短单名（`entities.is_short_single_name`）时不剔：`飛鳥` 与 `145cm色白お嬢様`
    两家并列时，剔掉后者就是只凭一个两字名去认人，ADR-0072 说过这种名字会命中别人。
    两家各执一词的行交人工，不因为少了一家就替人认了。`飛鳥ちゃん` 与 `飛鳥` 到这里之前
    已由 `_with_honorific_echoes` 折成两家一致。
    """
    if field != "performers" or not _FC2_CODE.match(code):
        return candidates, []
    dropped = [c for c in candidates if _names_nobody(c, resolve)]
    kept = [c for c in candidates if not _names_nobody(c, resolve)]
    if not dropped or not kept:
        return candidates, []
    for candidate in kept:
        names, _resolved = _stage_names(candidate, resolve)
        if names and any(is_short_single_name(name) for name in names):
            return candidates, []
    return kept, dropped


def fc2_descriptive_rejection(connection, row: dict, *, snapshot_root=None) -> dict | None:
    """这一行 FC2 出演者该不该自动否决；该否决就返回要写进 note 的那份记录。

    用户 2026-09-27 定下（ADR-0079）：候选里一个艺名都剪不出、企划名义也解不出的，
    人看了也不知道这位叫什么，能做的只有否决。判的是这一行过了番号核对的**全部**候选，
    不只是已登记来源：未登记来源给了真名，人在页面上还能选它，那就不替人否决。

    不建实体，不写真相字段。note 里的 `candidate_keys` 是过期判据用的：之后来源给出
    新的候选（fc2cmadb 补上了真名），这条否决就过期，这一行重新判一次。
    """
    field = str(row.get("field") or "").strip()
    code = str(row.get("code") or "").strip()
    if field != "performers" or not _FC2_CODE.match(code):
        return None
    candidates = row.get("candidates") or []
    if not candidates:
        return None
    resolve = _planning_alias_resolver(connection, row, snapshot_root)
    if not all(_names_nobody(candidate, resolve) for candidate in candidates):
        return None
    return {
        "auto_rejected": True, "rule": FC2_DESCRIPTIVE_REJECTION_RULE,
        "candidate_keys": sorted({str(c.get("candidate_key") or "").strip()
                                  for c in candidates}),
        "rejected_values": [{"source": str(c.get("source") or "").strip(),
                             "value": str(c.get("display_value") or "").strip()}
                            for c in candidates],
    }


def _decision_note(decision: dict) -> dict:
    """决定的 note 解析成字典；早期的自由文本留痕、用户手写的备注都返回空字典。"""
    try:
        note = json.loads(str(decision.get("note") or ""))
    except (TypeError, ValueError):
        return {}
    return note if isinstance(note, dict) else {}


def metadata_decision_is_stale(decision: dict, candidates: list[dict], *, code: str = "") -> bool:
    """旧决定是否已经不对应这一行现存的候选；复核页与自动落库共用这一份。

    `metadata_fields` 的 `item_key` 是 `<番号>:<字段>`，不带候选身份。于是
    2026-09-01 对 r18dev「空日文标题」的一条 approved，会把之后 javbus 抓到的
    真标题一并盖住：队列里看不见这条，页面上还是英文标题。实测 TRE-080 就是
    这样卡住的，同批还有 24 个番号。判据与 `studio_logos` 的「上游内容变了就
    清掉旧判定」是同一条线，只是这里的「变了」体现为候选身份换了一个。

    - 批准：note 记着的 `candidate_key` 已经不在当前候选里，`refreshed_candidate_key`
      也不在。后者是自动落库重判后确认「现存候选与账本现值相同」时追加的一项，原决定
      的其余字段不动（`_reopened_approval`）；
    - 自动否决（ADR-0079）：当前候选里出现了否决时没有的 `candidate_key`——来源给了
      新说法，否决时的判断不再覆盖它。来源少了一条不算，剩下的仍是否决过的那些；
    - 用户手工否决、跳过一律不算过期：那是人的判断，候选变了也不替人翻案。

    只在能读出旧决定指向哪个候选时才判过期。note 不是 JSON（早期的自由文本留痕）就
    保守放过——宁可漏一条，也不要把用户已经判过的东西重新翻出来。
    """
    note = _decision_note(decision)
    keys = {str(candidate.get("candidate_key") or "").strip()
            for candidate in candidates or []} - {""}
    if not keys:
        return False
    status = str(decision.get("status") or "").strip()
    if status == "approved":
        reviewed = set(note.get("reviewed_candidate_keys") or [])
        reviewed.update(str(note.get(key) or "") for key in ("candidate_key", "refreshed_candidate_key"))
        if any(candidate.get("requires_review") and candidate.get("candidate_key") not in reviewed
               for candidate in candidates):
            return True
        if note.get("reviewed_candidate_keys") and keys <= reviewed:
            return False
        if _preferred_tags_changed(decision, candidates, code=code):
            return True
        approved_key = str(note.get("candidate_key") or "").strip()
        refreshed_key = str(note.get("refreshed_candidate_key") or "").strip()
        return bool(approved_key) and not {approved_key, refreshed_key} & keys
    if status == "rejected" and note.get("auto_rejected") is True:
        rejected = {str(key).strip() for key in note.get("candidate_keys") or []}
        return not keys <= rejected
    return False


def _preferred_tags_changed(decision: dict, candidates: list[dict], *, code: str = "") -> bool:
    """有码标签出现了尚未结算的 JavDB 候选，旧来源的批准需要重判。"""
    keyed_code, _, field = str(decision.get("item_key") or "").rpartition(":")
    code = code or keyed_code
    source = preferred_tag_source(code) if field == "tags" else None
    note = _decision_note(decision)
    if not source or str(decision.get("status") or "") != "approved":
        return False
    if any(str(c.get("source") or "").strip() == LOCAL_NFO_SOURCE for c in candidates):
        return False
    keys = {str(note.get(key) or "").strip()
            for key in ("candidate_key", "refreshed_candidate_key")} - {""}
    if not keys and not note.get("source"):
        return False
    preferred = [c for c in candidates if str(c.get("source") or "").strip() == source
                 and _candidate_identifies_code(code, c)]
    return bool(preferred) and not any(
        str(c.get("candidate_key") or "").strip() in keys for c in preferred)


def _same_as_current(connection, field: str, candidate: dict, current: str) -> bool:
    """落库用的这条候选与账本现值说的是不是同一件事。

    口径与复核页剔「没有新信息」的行一致：标签按集合比，出演者与厂牌、系列按实体
    身份比（`横宮七海` 与 `横宫七海` 是同一位，`プレステージ` 与 `Prestige` 是同一家），
    其余按折叠空白后的字符串比。现值为空时总是不同：那是补空。
    """
    current = str(current or "").strip()
    value = str(candidate.get("display_value") or "").strip()
    if not current:
        return False
    if field == "performers":
        return (_performer_identity_keys(connection, _split_multi(value))
                == _performer_identity_keys(connection, _split_multi(current)))
    if field == "tags":
        return frozenset(_split_multi(value)) == frozenset(_split_multi(current))
    if field in {"studio", "series"}:
        return (_entity_identity_key(connection, field, value)
                == _entity_identity_key(connection, field, current))
    return " ".join(value.split()) == " ".join(current.split())


def _is_automatic(decision: dict) -> bool:
    """这条决定是不是自动写下的：自动落库或自动否决。其余都算人的判断。"""
    note = _decision_note(decision)
    return note.get("auto_applied") is True or note.get("auto_rejected") is True


def _candidate_value_key(connection, field: str, candidate: dict, resolve=None):
    """两个来源说的是不是同一件事；这条候选本身不可用时返回 None。

    出演者比的是人，不是写法。同一位在两家站上常挂着不同艺名，而账本早把它们登记在
    同一条实体名下：`n0646` javbus 写 `一ノ瀬アメリ`、javdb 写 `美空あやか`，两个写法
    都指向实体 8074（规范名 `美空彩香`）；`011013_511` 的 `飯岡かなこ` 与 `森沢かな`
    同理。按字符串比，这些行会被判成「来源有分歧」而扣在人工队列里，可分歧问的那个
    问题账本自己已经答过了。

    解析不到实体的写法保留规范化原名，所以真换了人不会被这一步折掉；顺序也不参与
    比较——`FSEI-003` 两家给的是同一组六个人，只是排序不同。
    """
    if field == "performers":
        names, _resolved = _stage_names(candidate, resolve)
        return None if names is None else _performer_identity_keys(connection, names)
    value = str(candidate.get("display_value") or "").strip()
    return value or None


def _normalised_candidate(field: str, candidate: dict, resolve=None) -> dict:
    """落库用的候选。出演者写剪好的艺名，原文留在 `raw_display_value` 里备查。

    企划名义解出来的那几位，把原称呼挂在这个人自己身上（`planning_alias`）：落库
    那一步要按它登记别名，而整条的 `raw_display_value` 分不出是哪一位。
    """
    if field != "performers":
        return candidate
    names, resolved = _stage_names(candidate, resolve)
    names = names or []
    people = [{**person, "name": name,
               **({"planning_alias": resolved[name][0]} if name in resolved else {})}
              for person, name in zip(candidate.get("value") or [], names)]
    return {**candidate, "value": people, "display_value": "、".join(names),
            "raw_display_value": str(candidate.get("display_value") or "").strip(),
            **({"alias_source": sorted({source for _raw, source in resolved.values()})[0]}
               if resolved else {})}


def _preferred_candidate(field: str, candidates: list[dict], *, code: str = "") -> dict:
    """取值一致时由谁署名。字段优先级链已经排好，落库记的出处就该是链上最靠前的那家。"""
    return min(candidates, key=lambda candidate: chain_rank(
        field, str(candidate.get("source") or "").strip(), code=code))


#: 韩国 MIB 番号唯一可信的来源：官网 k-mib.com（`metadata_kmib`）。
MIB_OFFICIAL_SOURCE = "kmib"


def _only_mib_official(row: dict) -> bool:
    """这一行的候选是否全部来自 MIB 官网；没有候选或解析不了时为 False。"""
    candidates = row.get("candidates")
    if candidates is None:
        try:
            candidates = json.loads(str(row.get("candidates_json") or "[]"))
        except (TypeError, ValueError):
            return False
    sources = {str(c.get("source") or "").strip()
               for c in candidates if isinstance(c, dict)}
    return sources == {MIB_OFFICIAL_SOURCE}


def _candidate_identifies_code(code: str, candidate: dict) -> bool:
    """来源返回的是不是这个番号本身。

    落库那一步早就有同一道闸（`_apply_metadata_candidate`），但它只能拒绝，拒绝不掉
    的是这张卡先占了人的注意力：`259LUXU-891` 的队列里摆着 javbus 按 `259LUXU-1891`
    取回的标题和日期——那是另一部片，点了也写不进去。既然认得出来，就别摆出来。

    本地 NFO 不按番号去问谁（证据是它躺在视频旁边），没有番号的行也无从核起。
    """
    source = str(candidate.get("source") or "").strip()
    if not code or source == LOCAL_NFO_SOURCE:
        return True
    return identifies_code(code, {
        "id": candidate.get("provider_id"), "content_id": candidate.get("content_id"),
        "source_url": candidate.get("source_url"),
    })


def _parsed_candidates(row: dict) -> list[dict]:
    """一行 `candidates_json` 里形状成立的候选，不做任何取舍。"""
    try:
        parsed = json.loads(str(row.get("candidates_json") or "[]"))
    except (TypeError, ValueError):
        return []
    return [candidate for candidate in parsed if isinstance(candidate, dict)
            and str(candidate.get("candidate_key") or "").strip()]


#: FC2 番号，带不带分段后缀都算：`FC2-PPV-3312576-1` 也是同一个平台上卖的。
_FC2_CODE = re.compile(r"^FC2(?:[-_. ]?PPV)?[-_. ]?\d{5,}", re.I)
#: 平台自己的几种写法，它们本来就是厂牌，不是卖家。
_FC2_PLATFORM = re.compile(r"^fc2(?:[-_. ]?ppv)?$", re.I)


def _fc2_seller_as_label(field: str, code: str, candidate: dict) -> dict:
    """FC2 番号的厂牌一律是 `FC2-PPV`，来源给的其他名字是卖家，改记进 `label`。

    amane 的 FC2 爬虫把卖家填进 studio，它写的 NFO 又经本地 NFO 那条规则落库，javdb
    的片商栏同样是卖家：2026-09-24 账本里 `プライベートアーカイブ管理人` 就这样成了
    一个只有 11 部作品的厂牌实体。Peach 自己的 FC2 解析器早把厂牌钉成 `FC2-PPV`、卖家
    放 `label`（`sources/fc2.py`）；这里对所有来源做同一件事，入口再多也只有这一处。
    """
    if field != "studio" or not _FC2_CODE.match(code):
        return candidate
    seller = str(candidate.get("value") or "").strip()
    if not seller:
        return candidate
    platform = {**candidate, "value": fc2.STUDIO, "display_value": fc2.STUDIO}
    if _FC2_PLATFORM.fullmatch(seller):
        return platform
    return {**platform, "label": str(candidate.get("label") or "").strip() or seller}


def _row_candidates(row: dict, decided) -> list[dict]:
    """把一行的 `candidates_json` 解析成候选列表：折叠 genre 决定、FC2 卖家改记 label、
    剔掉番号对不上的。"""
    field = str(row.get("field") or "").strip()
    code = str(row.get("code") or "").strip()
    return [_fc2_seller_as_label(field, code, _fold_genre_decisions(field, candidate, decided))
            for candidate in _parsed_candidates(row)
            if _candidate_identifies_code(code, candidate)]


def _auto_apply_rule(candidate: dict, agreed: int) -> str:
    """这条自动落库该记在哪条规则名下。

    企划名义解析（ADR-0038）排最前：这一条改的是写进账本的那个名字本身，回溯时要先
    认出它。按字段优先级链取舍过的排第二，规则名带上胜出那一家；被压下的取值另记在
    note 的 `overruled` 里，两样合起来才答得出「为什么是它」。

    official 与 community 两类补空在 `review_decision` 里必须分得开：出了问题要回溯的
    是「哪些值是 community 源补的」，而 note 是唯一留着这个区别的地方。多来源一致
    （ADR-0025）与单来源（ADR-0018）同样要分得开：前者的证据强度不一样。来源之间有
    分歧、按 ADR-0034 取舍过的，记下是按什么取舍的。覆盖既有取值的那一类（ADR-0035）
    单独记名：它是唯一一条会改掉账本已有值的自动写入，回溯时第一个要捞出来的就是它。
    """
    source = str(candidate.get("source") or "").strip()
    if candidate.get("tag_policy") == "censored-javdb":
        return "metadata-censored-javdb-tags"
    if candidate.get("alias_source"):
        return f"adr-0038-planning-alias-resolved-{candidate['alias_source']}"
    if candidate.get("honorific_agreed"):
        return f"{FC2_HONORIFIC_AGREEMENT_RULE}-{agreed}-agreed-sources"
    if candidate.get("chain_winner"):
        return f"adr-0038-chain-{candidate['chain_winner']}"
    if candidate.get("replaces_current"):
        if agreed > 1:
            return f"adr-0035-official-replaces-{agreed}-agreed-sources"
        return "adr-0035-official-replaces-single-source"
    if candidate.get("settled_by"):
        return f"adr-0034-empty-field-{candidate['settled_by']}"
    if source == LOCAL_NFO_SOURCE:
        return "adr-0029-empty-field-local-nfo"
    spec = SOURCE_SPECS.get(source)
    kind = "official" if spec is not None and spec.official else "community"
    if agreed > 1:
        return f"adr-0025-empty-field-{agreed}-agreed-{kind}-sources"
    return f"adr-0018-empty-field-single-{kind}-source"


def _evidence_candidates(row: dict, field: str = "") -> list[dict]:
    """能当补空证据的候选：已登记来源与本地 NFO，去掉这个字段上被拉黑的来源。

    只剩一家社区来源也算：落库只补空格子（ADR-0033），空着的格子有一个值比没有强；
    补错的值用户改过一次就归 `user:manual`，自动写入不再碰它（ADR-0034）。

    黑名单（ADR-0038）排在最前面，和 amane 的 `field_blacklist` 同一个位置：被拉黑的
    来源在这个字段上连候选都不算，所以也不会因为「只剩它一家」而被采信。
    """
    return [c for c in row.get("candidates") or []
            if (str(c.get("source") or "").strip() in SOURCE_SPECS
                or str(c.get("source") or "").strip() == LOCAL_NFO_SOURCE)
            and not blacklisted(field, str(c.get("source") or "").strip())]


def _unmasked_titles(field: str, candidates: list[dict], current: str = "") -> list[dict]:
    """标题候选里被站方打了码的，用别家原文或账本现值补上（`fill_masked_title`）。

    FC2 官方页与镜像 fc2cmadb 把敏感词换成星号，JavArchive、javdb 给的是原文；链上
    fc2 排在前面，不补的话打码那份就压过原文落进账本。账本现值也算一份原文：存量补好
    之后下一轮只剩打码那一家时，补出来的还是现值，不会被星号覆盖回去。

    javdb 插进去的混淆串（`strip_scrape_mark`）先剥掉再补：社区之间听 javdb，不剥的话
    只有 JavArchive 与 javdb 两家时，带混淆串的那份就压过 JavArchive 的原文。
    """
    if field not in {"title", "original_title"}:
        return candidates
    shown = [str(c.get("display_value") or "") for c in candidates]
    stripped = [strip_scrape_mark(text) for text in shown]
    originals = stripped + [strip_scrape_mark(current)]
    rows = []
    for candidate, text, clean in zip(candidates, shown, stripped):
        value = fill_masked_title(clean, originals) or clean
        rows.append(candidate if value == text else {
            **candidate, "value": value, "display_value": value, "masked_value": text})
    return rows


def _settled_candidates(connection, field: str, code: str, candidates: list[dict],
                        resolve=None) -> tuple[list[dict], str | None, list[dict]]:
    """取值只剩一个的那组候选、据以取舍的规则，以及被压下的那些取值。

    日期式番号的发行日期只认番号自己写的那天：候选里没有这一天就交给人。
    兜底来源（javbus）在这之后才降级：还有别家给了值就不看它（ADR-0035），而番号自带
    的那天是硬事实，谁报出来都算——`092415_001` 只有 javbus 报对，先降级就把它丢了。

    取值仍然不一时按字段优先级链取第一位（ADR-0038）：本地 NFO、官方来源、javdb、
    其余社区、兜底，同层之间用 `FIELD_SOURCE_ORDER` 再排。链上 dmm 本来就排在 mgstage 前面，让它说话就不必再问人。

    被压下的取值原样返回，调用方写进 `review_decision.note`：自动结算的前提是事后
    答得出「当时还有哪些说法、为什么没选它」，那句话只能落在这里。
    """
    settled_by = None
    date = code_release_date(code) if field == "release_date" else None
    if date is not None:
        matching = [c for c in candidates if str(c.get("display_value") or "").strip() == date]
        settled_by = "code-date" if len(matching) < len(candidates) else None
        candidates = matching
    candidates = [c for c in candidates
                  if str(c.get("source") or "").strip() not in FALLBACK_SOURCES] or candidates
    keys = {id(c): _candidate_value_key(connection, field, c, resolve) for c in candidates}
    values = set(keys.values())
    if None in values or not values:
        return [], None, []
    if len(values) == 1:
        return candidates, settled_by, []
    ranks = {id(c): chain_rank(field, str(c.get("source") or "").strip(), code=code)
             for c in candidates}
    winner = min(candidates, key=lambda candidate: ranks[id(candidate)])
    # 同一家给出两个不同的值时，链上没有人能替它取舍：`ranks` 里它们分数相同，`min`
    # 只会按列表顺序挑一个，而那个顺序不表达任何判断。这种行交回人工。
    if any(ranks[id(c)] == ranks[id(winner)] and keys[id(c)] != keys[id(winner)]
           for c in candidates):
        return [], None, []
    chosen = [c for c in candidates if keys[id(c)] == keys[id(winner)]]
    overruled = [{"source": str(c.get("source") or "").strip(),
                  "value": str(c.get("display_value") or "").strip()}
                 for c in candidates if keys[id(c)] != keys[id(winner)]]
    return chosen, settled_by, overruled


#: FC2 番号里的商品号。`FC2-PPV-4927200`、`FC2PPV 4927200`、`fc4592208` 都取那串数字。
_FC2_PRODUCT = re.compile(r"^FC2[-_ ]*(?:PPV)?[-_ ]*(\d{5,})$", re.I)


def _filename_carries_code(code: str, name: str) -> bool:
    """这个文件名认不认得出这个番号。

    逐字出现最直白，但盘里有大量不写连字符的名字（`MEYD911.mp4`）。编目规则本来就
    知道怎么从文件名读番号，读出来同号是比子串更强的身份证据——子串只是碰巧包含。

    FC2 另算一条：它的身份就是商品号那串数字，前缀谁爱怎么写怎么写——账本存
    `FC2-PPV-4927200`，盘里同一部片叫 `FC2-4927200-CD1.mp4`、`fc4592208.mp4`、
    `1879920.mp4`，三种前两条都认不出（2026-09-22 实测队列里 12 行因此停住）。
    数字本身足够长（5 位起），碰巧撞上的余地很小。
    """
    if code.casefold() in name.casefold():
        return True
    product = _FC2_PRODUCT.search(code)
    if product:
        return bool(re.search(rf"(?<!\d){product.group(1)}(?!\d)", name))
    parsed = release_code_from_filename(name)
    return bool(parsed) and same_release_code(code, parsed)


#: 目录名兜底要求文件名带着的那段数字至少几位。`WX-17` 这种两位序号在文件名里太常见，
#: 碰巧撞上的余地大；三位起才算得上文件自己的身份。
_FOLDER_DIGITS_MIN = 3


def _folder_carries_code(code: str, target) -> bool:
    """文件名读不出番号时，这个文件的直接父目录能不能替它认出这个番号（ADR-0082）。

    三项同时成立：番号是明确的番号形态（`is_jav_code`）；父目录名逐字写着完整番号，
    不对目录名跑番号解析——`[mtfdz.club]WX17.3` 这类合集名解析出来的 `WX-017` 正是要
    防的那种；文件名里单独出现番号最长的那段数字，至少三位，并列时取最后一段。
    `1pondo-123118.mp4` 放在 `123118_001` 目录下可过：目录名写着番号，文件名写着日期。

    路径按反斜杠和斜杠一起切：账本存 `R:\\...` 形态，测试夹具里是 POSIX 形态。
    """
    if not is_jav_code(code):
        return False
    parts = re.split(r"[\\/]", str(target["path"] or ""))
    if len(parts) < 2 or code.casefold() not in parts[-2].casefold():
        return False
    runs = re.findall(r"\d+", code)
    digits = max(reversed(runs), key=len, default="")
    if len(digits) < _FOLDER_DIGITS_MIN:
        return False
    return bool(re.search(rf"(?<!\d){digits}(?!\d)", str(target["name"] or "")))


def _group_identifies_code(code: str, targets) -> bool:
    """这一组资产是不是这个番号的片。

    要求这组里**有**文件名认得出这个番号的，而认不出的那些也没有指向别的番号。
    整组文件名一个番号都读不出时，退一步看父目录（`_folder_carries_code`）；组里只要有
    一个文件名读得出番号，不论是不是这一个，都不看目录。

    盗版包会往同一个番号目录里塞推广片：`259LUXU-902` 名下两条正片各 985 MB 和
    2714 MB，旁边躺着 `免费手机看片.avi`（4.4 MB／26 秒）、`線上影片每天火熱更新中.avi`
    和一条手游广告，三条的文件名读不出任何番号（2026-09-22 实测队列里 16 行是这一种）。
    逐条都要认得出的话，这类组的元数据就一直空着。

    身份保证仍然成立：读不出番号的文件证明不了这组是别的片；组里真混进别的番号时
    那一条读得出来，照旧交回人工。
    """
    names = [str(target["name"] or "") for target in targets]
    if not any(_filename_carries_code(code, name) for name in names):
        if any(release_code_from_filename(name) for name in names):
            return False
        return any(_folder_carries_code(code, target) for target in targets)
    return not any(
        (parsed := release_code_from_filename(name)) and not same_release_code(code, parsed)
        for name in names if not _filename_carries_code(code, name))


def pending_genres(candidates: list[dict]) -> list[str]:
    """这批标签候选里三张表都不认的词。

    折叠已经按用户决定和当前静态表跑过一遍（`_fold_genre_decisions`），剩在
    `unmapped_genres` 里的就是没人判过的那些。它们此前扣住整条候选：一行里认得出
    的十几个标签陪着两个生词一起等人，而等来的判断只关乎那两个词（ADR-0038）。

    认得出的标签照常落库，这几个词记进 `review_decision.note` 的 `pending_genres`，
    复核页按它把这一行重新摆出来——要判的只剩「这个词收录成什么」。
    """
    return list(dict.fromkeys(
        str(genre).strip() for candidate in candidates
        for genre in candidate.get("unmapped_genres") or [] if str(genre).strip()))


def overwrite_requires_review(connection, row: dict) -> bool:
    """NFO 核查中的已有值差异必须由用户批准。"""
    current = str(row.get("current_value") or "").strip()
    return bool(current) and any(
        candidate.get("requires_review") and not _same_as_current(
            connection, str(row.get("field") or ""), candidate, current)
        for candidate in row.get("candidates") or _parsed_candidates(row))


def _may_replace_current(candidates: list[dict], *, field: str = "", code: str = "") -> bool:
    """这批候选能不能改掉账本已有的值：只有链上第一档可以。

    第一档是本地 NFO 与官方来源（含官方镜像）——用户自己整理的那份，和发行方自己
    写的那页。ADR-0035 让官方来源替换现值时用的就是这条界线，ADR-0038 只是把本地
    NFO 一并纳进来：它在链上排在官方前面，没有理由反而不能替换。

    有码作品的 JavDB 内容标签可以替换自动元数据标签；人工批准走并集，其他来源归属保留。
    其他社区字段只补空（ADR-0033）。兜底来源连挑战都不算：javbus 搜不到就给首个
    近似命中，`259LUXU-891` 它答的是 `259LUXU-1891`。
    """
    sources = {str(c.get("source") or "").strip() for c in candidates}
    if field == "tags" and preferred_tag_source(code) in sources:
        return True
    return bool(sources) and all(
        source_tier(source) <= CHAIN_OFFICIAL for source in sources)


def _auto_apply_targets(connection, row: dict, code: str, field: str):
    """核对番号组的文件身份与字段归属，返回可供自动补全的资产。"""
    query = str(row.get("query") or code).strip()
    targets = _codes_matching(connection, [code, query], "code,name,path,field_owners")
    if not targets or not _group_identifies_code(code, targets):
        return []
    column = METADATA_FIELD_COLUMNS.get(field)
    if column and any(is_protected(owner_of(target["field_owners"], column)) for target in targets):
        return []
    return targets


def metadata_auto_apply_candidate(connection, row: dict, *,
                                  snapshot_root=None) -> dict | None:
    """这一行能否不经复核直接落库；不能就返回 None。

    三项必须同时成立，缺一项就仍然走人工：

    1. 候选**取值**按字段优先级链结算后只剩一个（`_settled_candidates`）。取值一致
       是最强的证据，数的是取值不是候选条数（ADR-0025）；取值不一时按链取第一位
       （ADR-0038），被压下的说法记进 note。两种取舍走在链之前：日期式番号的发行日期
       只认番号自己写的那天（ADR-0034），兜底来源在别家在场时退开（ADR-0035）；
    2. 目标字段当前为空，或者结算下来的来源不是兜底那一家——补空之外，链首那家的
       取值直接替换现值。发行方自己那页就是这部片的出处，账本里那个来路不明的旧值
       没有理由压住它；用户改过的格子归属受保护，仍然不碰；
    3. 该番号名下有资产的文件名认得出这个番号，认不出的那些也没有指向别的番号
       （`_group_identifies_code`）——逐字出现，或按编目规则解析出来就是它；整组文件名
       都读不出番号时，父目录逐字写着番号、文件名带着番号里三位以上那段数字也算。

    补空那一支不看来源是不是 official（用户 2026-09-04 决定）：补空不覆盖任何东西，
    唯一的风险是「这个值属不属于这部片」，而那由第 3 条管，与来源可信度无关。卡住
    official 这条的代价是实测 76 条 javbus 补空候选全部滞留人工，它们补的都是账本里
    空着的发行日期——没有可判断项，却要人逐条点过。落库时按来源实际级别记规则名，
    回溯得出来。

    第 3 条是这条捷径唯一的身份保证。刮削按番号取值，番号错则值错；文件名认得出
    番号是本机可核验的证据，而复核界面其实给不了这个保证——它只并排显示番号和
    日期，并不告诉你番号跟这个文件对不对得上。

    出演者多一道形态门槛：官方页把年龄职业写在艺名后面，剪不出艺名的先按本机已有的
    证据解一次企划名义（`snapshot_root` 给的快照目录与 `entity_alias`，ADR-0038），
    解不出才交回人工。FC2 番号上一个艺名都没有的候选不算证据（ADR-0079），整行都是这种
    时不交人工，由 `fc2_descriptive_rejection` 自动否决。第 1 条对它比的是人而不是写法：同一位在两家站上挂着不同艺名、
    账本已把这两个写法登记在同一条实体名下时，那不是分歧（`_candidate_value_key`）。

    未登记来源的候选先被剔除再比对取值：没进 `REGISTERED_SOURCES` 的来源不构成证据，
    留着它只会把「一个有效取值」算成分歧。

    来源级别一律按当前 policy 解析，不读候选 CSV 里的同名字段：那是抓取当时的
    快照，实测 r18dev 在 CSV 里写着 False，而现行 policy 认它是 official_mirror。

    本地 NFO 不在登记表里，却同样构成证据（ADR-0029）：采集时它的番号已经和文件名
    对过，第 3 条照样再核一遍。NFO 核查的覆盖候选带 `requires_review`，非空字段必须
    复核后才可改值（ADR-0108）；来源链只决定补空采用哪一份。
    """
    field = str(row.get("field") or "").strip()
    if field not in AUTO_APPLY_FIELDS:
        return None
    if overwrite_requires_review(connection, row):
        return None
    code = str(row.get("code") or "").strip()
    if not code:
        return None
    resolve = _planning_alias_resolver(connection, row, snapshot_root)
    evidence, _echoes = _with_honorific_echoes(
        connection, code, field, _evidence_candidates(row, field), resolve)
    evidence, descriptive = _without_descriptive_performers(code, field, evidence, resolve)
    candidates, settled_by, overruled = _settled_candidates(
        connection, field, code, _unmasked_titles(
            field, evidence, str(row.get("current_value") or "")), resolve)
    if not candidates:
        return None
    replaces_current = bool(str(row.get("current_value") or "").strip())
    if replaces_current and not _may_replace_current(candidates, field=field, code=code):
        return None
    candidate = _preferred_candidate(field, candidates, code=code)
    # 韩国 MIB 的番号问 JAV 目录站必错，这类候选一条都不该走自动批准。第 3 条对它们
    # 全部成立——文件名就叫 `AR-101 Ari....mp4`——但它保证的是「候选属于这个文件」，
    # 保证不了「来源返回的是这个番号」，而 MIB 恰恰错在后者。只有候选全部来自 MIB
    # 官网时才放行：官网按番号列出的就是这部片本身。
    if is_korean_mib_code(code) and not _only_mib_official(row):
        return None
    # 归属是用户判断的字段不走自动落库。ADR-0018 第 1 条只看取值空不空，而用户可以
    # 把一个字段判成空——那也是判断。没有这一道，「清空再等自动补回来」就成了
    # 用户无法表达的意思。
    if not _auto_apply_targets(connection, row, code, field):
        return None
    return {**_normalised_candidate(field, candidate, resolve),
            **({"tag_policy": "censored-javdb"}
               if field == "tags" and candidate.get("source") == preferred_tag_source(code) else {}),
            "agreed_sources": len(candidates),
            **({"settled_by": settled_by} if settled_by else {}),
            **({"chain_winner": str(candidate.get("source") or "").strip(),
                "overruled": overruled} if overruled else {}),
            **({"pending_genres": found} if (found := pending_genres(candidates)) else {}),
            **_honorific_agreement(candidates),
            **({"descriptive_dropped": [
                {"source": str(c.get("source") or "").strip(),
                 "value": str(c.get("display_value") or "").strip()} for c in descriptive]}
               if descriptive else {}),
            **({"replaces_current": True} if replaces_current else {})}


def _approved_entity_name(value: object, kind: str) -> str:
    name = str(value or "").strip()
    cleaned = canonicalize_entity_name(kind, name)
    if kind not in {"creator", "performer"}:
        cleaned = collapse_repeated_entity_name(cleaned)
    if not name or not cleaned or cleaned != name:
        raise ValueError("候选仍含重复或未规范化的实体名，拒绝写入")
    return cleaned


def _registered_entity_name(connection, kind: str, name: str) -> str:
    """来源的写法是账本里某条实体的别名，就换成那条的规范名。

    javdb 写 `Tokyo-Hot`、javbus 写 `東京熱`，账本只该有一个 `东京热`。解析不到（或别名
    撞了两条）时保留来源原文。
    """
    known = resolve_entity(connection, kind, name)
    return name if known is None else str(known["canonical_name"])


#: 复核字段名 → `asset` 的真相字段列。`performers` 与 `tags` 不在这里：它们落在
#: `asset_tag` / `asset_entity` 的多值行上，不是 `asset` 的一列，归属由那两张表
#: 自己的 `source` 列承担。
METADATA_FIELD_COLUMNS = {
    "title": "catalog_title", "original_title": "original_title",
    "release_date": "release_date", "studio": "studio", "series": "series",
}


#: 刮削链之前那两套导入写下的关联来源：r18 与 javbus 的发行元数据，外加从 `asset`
#: 扁平字段投影出来的 `legacy:asset`。它们和 `javinizer:*` 一样是抓来的，官方值换掉
#: 字段时要一起换掉。只删 `javinizer:*` 时旧的那几条留在原处：`ACC-006` 的 r18 条目按
#: 番号撞上了 2009 年 Star Ship 的同号片，DMM 的值落进字段以后，页面上仍挂着那部片的
#: 系列「決断」和出演者「三浦沙绫」。
_SCRAPED_ROLE_SOURCES = {
    "performer": ("r18:performer", "javbus:performer"),
    "studio": ("r18:studio", "javbus:studio", "legacy:asset"),
    "series": ("r18:series", "javbus:series", "legacy:asset"),
}


def _drop_superseded_links(connection, asset_ids: list[int], field: str, entity_id: int,
                           name: str) -> None:
    """字段换成官方值 `name` 之后，这个角色上指向别的实体的抓取关联一并删掉。

    只删字段确实已是新值的资产：`write_owned_fields` 不覆盖人写的字段，那些资产上的
    关联还对得上人写的值。人手挂的关联（`user:*`、`review:*`、`script:*`）不在
    `_SCRAPED_ROLE_SOURCES` 里，不删。
    """
    marks = ",".join("?" * len(asset_ids))
    scraped = _SCRAPED_ROLE_SOURCES[field]
    connection.execute(
        f"DELETE FROM asset_entity WHERE role=? AND entity_id<>? "
        f"AND (source LIKE 'javinizer:%' OR source IN ({','.join('?' * len(scraped))})) "
        f"AND asset_id IN (SELECT id FROM asset WHERE id IN ({marks}) "
        f"AND {METADATA_FIELD_COLUMNS[field]}=?)",
        (field, entity_id, *scraped, *asset_ids, name),
    )


def _apply_performer_candidate(connection, asset_ids: list[int], candidate: dict, *,
                               source: str, confidence: float, metadata: dict,
                               now: str) -> None:
    """把演员候选写成这些资产的 performer 实体与 `演员:` 标签。

    演员是 performer 真相，不回写 `asset.creator`；两种身份混写正是重复名称事故的
    来源之一。
    """
    raw_performers = candidate.get("value")
    if not isinstance(raw_performers, list):
        raise ValueError("演员候选必须是数组")
    performers: list[dict] = []
    seen: set[str] = set()
    for raw in raw_performers:
        if not isinstance(raw, dict):
            raise ValueError("演员候选条目无效")
        name = _approved_entity_name(raw.get("name"), "performer")
        normalized = normalize_entity_name(name)
        if normalized in seen:
            continue
        seen.add(normalized)
        performers.append({**raw, "name": name})
    if not performers:
        raise ValueError("演员候选为空")
    marks = ",".join("?" * len(asset_ids))
    scraped = ",".join("?" * len(_SCRAPED_ROLE_SOURCES["performer"]))
    connection.execute(
        f"DELETE FROM asset_entity WHERE asset_id IN ({marks}) AND role='performer' "
        f"AND (source LIKE 'javinizer:%' OR source IN ({scraped}))",
        (*asset_ids, *_SCRAPED_ROLE_SOURCES["performer"]),
    )
    connection.execute(
        f"DELETE FROM asset_tag WHERE asset_id IN ({marks}) "
        f"AND (source LIKE 'javinizer:%:performer' OR source IN ({scraped}))",
        (*asset_ids, *_SCRAPED_ROLE_SOURCES["performer"]),
    )
    for asset_id in asset_ids:
        for performer in performers:
            owner = _former_name_owner(connection, performer)
            name = performer["name"] if owner is None else str(owner["canonical_name"])
            external_id = str(performer.get("external_id") or "").strip()
            connection.execute(
                "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,?,?,?)",
                (asset_id, "演员:" + name, confidence, f"javinizer:{source}:performer"),
            )
            entity_id = upsert_asset_entity(
                connection, kind="performer", name=name, asset_id=asset_id,
                role="performer", source=f"javinizer:{source}:performer",
                confidence=confidence,
                external_provider=_performer_external_provider(
                    performer, source, external_id),
                external_id=(external_id or None), metadata=metadata, now=now,
            )
            # 企划名义解出主艺名时，把封面上印的那个称呼留成别名（ADR-0038）：
            # 账本里写的是 `目黒めぐみ`，用户搜的多半是 `めぐみ 28歳 パパ活女子`
            # 里那个 `めぐみ`，不登记就等于这次解析只落在一行 note 里。
            if entity_id is not None and performer.get("planning_alias"):
                register_planning_alias(
                    connection, entity_id, str(performer["planning_alias"]))
            if owner is not None and entity_id == int(owner["id"]):
                _register_current_name(connection, entity_id, performer["name"])


#: 「现名（旧名）」挂到旧名那一位身上时，现名记成她的别名用的来源。
CURRENT_NAME_ALIAS_SOURCE = "javinizer:current-name"


def _former_name_owner(connection, performer: dict):
    """现名在账本里查不到、旧名查得到：返回旧名那一位；否则 None。

    `姫川ゆうな（月城らん）` 拆开以后，账本里登记的若只有 `月城らん`，按现名落库就会另建
    一条 `姫川ゆうな`，和按整串落库一样重复。
    """
    if resolve_entity(connection, "performer", performer["name"]) is not None:
        return None
    for former in performer.get("former_names") or ():
        known = resolve_entity(connection, "performer", str(former))
        if known is not None:
            return known
    return None


def _register_current_name(connection, entity_id: int, name: str) -> None:
    """把现名登记成旧名那一位的别名；这个写法已经归了别人时不登记。"""
    key = normalize_entity_name(name)
    if connection.execute(
            "SELECT 1 FROM entity WHERE kind='performer' AND normalized_name=? AND id<>? "
            "UNION SELECT 1 FROM entity_alias WHERE normalized_alias=? AND entity_id<>? LIMIT 1",
            (key, entity_id, key, entity_id)).fetchone():
        return
    connection.execute(
        "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
        " VALUES(?,?,?,?,0.9)", (int(entity_id), name, key, CURRENT_NAME_ALIAS_SOURCE))


def _landing_scope(connection, group: dict, candidate: dict,
                   expected_revision: int | None = None) -> tuple[list[int], str, float, dict]:
    """一条候选落库之前必过的几道闸，过了就给出写入范围、来源、置信度与实体留痕。

    整套落库（`_apply_metadata_candidate`）与只补新增标签（`_extend_approved_tags`）
    共用这一份：范围、并发凭据和番号身份不能只在其中一条路上核。
    """
    code = str(group.get("code") or "").strip()
    query = str(group.get("query") or code).strip()
    if group.get('asset_path'):
        assets = connection.execute(
            "SELECT id FROM asset WHERE id=? AND path=? AND medium='video' "
            "AND disposal IS NULL",
            (group.get('asset_id'), group['asset_path']),
        ).fetchall()
    else:
        # 写入范围必须和判据、现值刷新看的是同一组资产，三处共用一份番号匹配。
        assets = _codes_matching(connection, [code, query], "id,code")
    asset_ids = sorted({int(row["id"]) for row in assets})
    if not asset_ids:
        raise ValueError("当前 ledger 已没有匹配的可用资产")
    if len(asset_ids) > REVIEW_APPLY_LIMIT:
        raise ValueError(f"同番号资产 {len(asset_ids)} 条，超过单次批准上限 {REVIEW_APPLY_LIMIT}")
    # 乐观并发的凭据在写任何一张表之前验，多值字段那两条分支才同样受它保护。
    check_revision(connection, asset_ids, expected_revision)
    source = str(candidate.get("source") or "").strip()
    candidate_key = str(candidate.get("candidate_key") or "").strip()
    if not re.fullmatch(r"[a-z0-9_-]+", source) or not candidate_key:
        raise ValueError("字段候选来源无效")
    try:
        confidence = float(candidate.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("字段候选置信度无效") from exc
    if not 0 <= confidence <= 1:
        raise ValueError("字段候选置信度越界")
    # 来源返回的必须就是这个番号。抓取时也核验，但那道闸只管「快照落盘之前」，
    # 而候选一旦进了 CSV 就再没人问过身份：2026-09-08 的自动批准读的正是 09-02
    # 落盘的那批，把 356 条错配写进真相字段，`AR-101` 拿的是 `STAR-101`、
    # `259LUXU-764` 拿的是 `259LUXU-1764`。落库是唯一必经之处，闸放在这里才
    # 同时管住人工批准和自动批准——那 356 条里有 142 条是人点的。
    #
    # 两种情形没有番号可核：没有番号的资产（靠 `asset_path` 钉住那一个文件），
    # 以及本地 NFO——它不按番号去问谁，证据是这份 sidecar 就躺在视频旁边。
    if code and source != "local_nfo" and not identifies_code(code, {
        "id": candidate.get("provider_id"), "content_id": candidate.get("content_id"),
        "source_url": candidate.get("source_url"),
    }):
        raise ValueError(
            f"来源返回的不是 {code}：id={candidate.get('provider_id')!r} "
            f"content_id={candidate.get('content_id')!r}")
    metadata = {
        "provider": candidate.get("provider") or "javinizer-go", "source": source,
        "source_url": candidate.get("source_url"),
        "provider_id": candidate.get("provider_id"), "content_id": candidate.get("content_id"),
        "raw_snapshot": candidate.get("raw_snapshot"), "review_item": group["item_key"],
        "candidate_key": candidate_key,
    }
    return asset_ids, source, confidence, metadata


def _apply_metadata_candidate(
    connection, group: dict, candidate: dict, now: str, owner: str, *,
    expected_revision: int | None = None,
) -> int:
    """把一个候选的取值写进真相字段，`owner` 是本次写入者的归属串。

    `owner` 没有默认值：写入者是谁属于调用点的事实，给个默认就等于让下一个
    调用点默默继承别人的身份，而这一层留痕正是 ADR-0005 要保住的东西。
    """
    field = str(group.get("field") or "").strip()
    if field not in {
        "title", "original_title", "performers", "studio", "series", "release_date", "tags",
    }:
        raise ValueError("该元数据字段没有 Peach 写入映射")
    code = str(group.get("code") or "").strip()
    asset_ids, source, confidence, metadata = _landing_scope(
        connection, group, candidate, expected_revision)
    marks = ",".join("?" * len(asset_ids))

    if field in {"title", "original_title"}:
        raw_value = str(candidate.get("value") or "")
        value = " ".join(raw_value.split())
        if (not value or len(value) > 1000
                or any(ord(char) < 32 for char in raw_value)):
            raise ValueError("标题候选为空、过长或含控制字符")
        write_owned_fields(
            connection, asset_ids, {METADATA_FIELD_COLUMNS[field]: value}, owner)
        return len(asset_ids)

    if field == "release_date":
        value = str(candidate.get("value") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("发行日期候选必须是 YYYY-MM-DD")
        try:
            time.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("发行日期候选无效") from exc
        write_owned_fields(
            connection, asset_ids, {"release_date": value}, owner)
        return len(asset_ids)

    if field in {"studio", "series"}:
        candidate = _fc2_seller_as_label(field, code, candidate)
        name = _registered_entity_name(
            connection, field, _approved_entity_name(candidate.get("value"), field))
        write_owned_fields(
            connection, asset_ids, {field: name}, owner)
        connection.execute(
            f"DELETE FROM asset_entity WHERE asset_id IN ({marks}) AND role=? "
            "AND source LIKE 'javinizer:%'",
            (*asset_ids, field),
        )
        for asset_id in asset_ids:
            entity_id = upsert_asset_entity(
                connection, kind=field, name=name, asset_id=asset_id, role=field,
                source=f"javinizer:{source}:{field}", confidence=confidence,
                metadata=metadata, now=now,
            )
        _drop_superseded_links(connection, asset_ids, field, entity_id, name)
        return len(asset_ids)

    if field == "performers":
        _apply_performer_candidate(
            connection, asset_ids, candidate, source=source, confidence=confidence,
            metadata=metadata, now=now)
        return len(asset_ids)

    raw_tags = candidate.get("value")
    if not isinstance(raw_tags, list):
        raise ValueError("标签候选必须是数组")
    # 候选文件停在抓取那一刻：那时的映射可能还给过退役名或已撤掉的粗桶，
    # 落库这一步按现在的词表换名或丢掉。
    tags = current_tags(_approved_entity_name(tag, "tag") for tag in raw_tags)
    if not tags:
        raise ValueError("标签候选为空")
    # 整套换掉的时候，并集补进来的那几个（`_extend_approved_tags`）一起清：它们是
    # 上一套候选的一部分，留下来就成了哪一套都不属于的标签。
    connection.execute(
        f"DELETE FROM asset_entity WHERE asset_id IN ({marks}) AND role='tag' "
        "AND (source LIKE 'javinizer:%' OR substr(source,1,?)=?)",
        (*asset_ids, len(UNION_TAGS_SOURCE) + 1, UNION_TAGS_SOURCE + "@"),
    )
    connection.execute(
        f"DELETE FROM asset_tag WHERE asset_id IN ({marks}) "
        "AND (source LIKE 'javinizer:%:tag' OR substr(source,1,?)=?)",
        (*asset_ids, len(UNION_TAGS_SOURCE) + 1, UNION_TAGS_SOURCE + "@"),
    )
    for asset_id in asset_ids:
        for tag in tags:
            connection.execute(
                "INSERT INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,?,?,?) "
                "ON CONFLICT(asset_id,tag) DO UPDATE SET "
                "confidence=excluded.confidence,source=excluded.source "
                "WHERE asset_tag.source='filename' AND asset_tag.confidence<excluded.confidence",
                (asset_id, tag, confidence, f"javinizer:{source}:tag"),
            )
            upsert_asset_entity(
                connection, kind="tag", name=tag, asset_id=asset_id, role="tag",
                source=f"javinizer:{source}:tag", confidence=confidence,
                metadata=metadata, now=now, update_entity_metadata=False,
            )
    return len(asset_ids)


#: 并集补标签的归属串。`asset_tag.source` 与 `asset_entity.source` 写的是批次号
#: `<归属串>@<时间>`，`revert_auto_landing.py --source auto:metadata-tags` 按它整批撤回。
UNION_TAGS_SOURCE = "auto:metadata-tags"


def _extend_approved_tags(connection, row: dict, candidate: dict, batch: str,
                          now: str, only: set[str] | None = None) -> list[str] | None:
    """把这条候选里账本还没有的标签补进去，已有的一个不删（ADR-0082）。

    返回补进去的标签名；候选全在账本里时返回空表，什么都不写。账本里还一个标签都没有，
    或落库的闸不过，返回 None：那不是「没有要补的」，是这条路走不通。`only` 给出时只补
    其中的名字（按规范化名比）：生词收录那一条只该补那个词收录出的标签，账本少的其余几个
    可能是用户手删的。

    比较用落库那一刻的名字（按现在的词表换过名，停用的 `乳系` 这时已经丢掉），账本那一侧
    取的是这组资产此刻挂着的全部标签，不分来源：人手加的也算在人的判断里。候选比账本
    少的那几个照旧挂着——人批准过它们，来源这一次没给不等于人判错了。已有的行一行不动，
    只插新增的，归属是 `batch`。
    """
    raw_tags = candidate.get("value")
    if not isinstance(raw_tags, list):
        return None
    try:
        written = current_tags(_approved_entity_name(tag, "tag") for tag in raw_tags)
        asset_ids, _source, confidence, metadata = _landing_scope(connection, row, candidate)
    except ValueError:
        return None
    marks = ",".join("?" * len(asset_ids))
    have = {normalize_entity_name(str(name)) for (name,) in connection.execute(
        "SELECT e.canonical_name FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id"
        f" WHERE ae.asset_id IN ({marks}) AND ae.role='tag'", asset_ids)}
    if not have:
        return None
    added = [tag for tag in written if normalize_entity_name(tag) not in have
             and (only is None or normalize_entity_name(tag) in only)]
    if not added:
        return []
    metadata = {**metadata, "batch": batch}
    for asset_id in asset_ids:
        for tag in added:
            connection.execute(
                "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,?,?,?)",
                (asset_id, tag, confidence, batch))
            upsert_asset_entity(
                connection, kind="tag", name=tag, asset_id=asset_id, role="tag",
                source=batch, confidence=confidence, metadata=metadata, now=now)
    return added


def _performer_external_provider(performer: dict, source: str,
                                 external_id: str) -> str | None:
    """人物外部编号跟随资料来源，不被承载候选的 NFO 来源冒领。"""
    if not external_id:
        return None
    # NFO 的名字是这次落库的真值。在线 profile 只在随后精确对到这个实体后才登记编号，
    # 不能让一个错误或被改写的 external_id 抢先把作品连到另一条既有实体。
    if source == LOCAL_NFO_SOURCE and performer.get("profile_source"):
        return None
    return str(performer.get("profile_source") or source)


def _refresh_auto_batch(connection, batch):
    """在同一事务中取得决定、标签规则和候选的账本现值。"""
    keys = list(dict.fromkeys(key for item in batch
                             if (key := str(item.get("item_key") or "").strip())))
    decided = {}
    for start in range(0, len(keys), AUTO_APPLY_BATCH):
        part = keys[start:start + AUTO_APPLY_BATCH]
        marks = ",".join("?" for _ in part)
        decided.update((row["item_key"], dict(row)) for row in connection.execute(
            "SELECT item_key,status,note FROM review_decision "
            f"WHERE category='metadata_fields' AND item_key IN ({marks})", part))
    genres = load_genre_decisions(connection)
    refresh_current_values(connection, batch)
    return decided, genres


def _collection_candidates(candidate_root, asset_ids, candidate_rows):
    """读取持久候选或当前采集快照，严格限制本轮可结算的资产。"""
    if candidate_rows is None:
        rows, _source, _skipped = read_candidates("metadata_fields", Path(candidate_root))
    else:
        rows = list(candidate_rows)
    if asset_ids is not None:
        chosen = {str(value) for value in asset_ids}
        rows = [row for row in rows if str(row.get('asset_id') or '') in chosen]
    return rows


def auto_apply_metadata(database, candidate_root, *, batch_size=AUTO_APPLY_BATCH,
                        active=lambda: True, snapshot_root=None, asset_ids=None, candidate_rows=None):
    """把确定的那部分直接落库，不占人工队列。

    ADR-0018：这是「刮削结果只作候选、不直接改写真相字段」的一个**窄例外**，
    不是废除该规则。判据见 `metadata_auto_apply_candidate`，四项缺一即回到人工。
    每条仍写 review_decision 留痕（note 里记来源与判据），所以事后可以追问
    「这个值是谁写的、凭什么」——留痕才是那条规则真正要保住的东西。

    已有决定的行不重判，过期的除外（`_decision_reopens`）；过期批准重判之后值不变的只
    追加一项留痕，人批准过的标签按并集补上新增、一个不删，其余要改值而原决定是人批准的
    交回人（`_reopened_approval`）。自动落库时留下生词的那一行，词收录了就把收录出的
    标签按并集补上（`_land_collected_genres`，ADR-0082）。落不下去的
    FC2 出演者行里一个艺名都没有的，写一条 `rejected`（`fc2_descriptive_rejection`，
    ADR-0079），其余照旧留给人。

    `database` 是调用方已经在用的那一个 `LedgerDatabase`：写锁与提交后的缓存失效都挂在
    实例上，自己再 new 一个就绕开了两者。`active` 返回假时停在批与批之间，已经提交的
    那些保留。

    `snapshot_root` 是本机落盘的来源快照目录，企划名义解析要读它（ADR-0038）。缺省
    是 None，也就是只走 `entity_alias` 那一条路：这一层不去猜真实 Peach 数据目录在哪儿，
    猜错的表现是测试悄悄读起了真实库。
    `asset_ids` 非空时只结算给定资产的候选；空集合不落库，None 表示全部候选。
    `candidate_rows` 是当前采集的字段快照；提供时不重新扫描全库 CSV。
    """
    rows = _collection_candidates(candidate_root, asset_ids, candidate_rows)
    applied, skipped, rejected, refreshed = [], 0, 0, 0
    # 一次运行一个批次号，并集补进来的标签按它整批撤回。
    union_batch = f"{UNION_TAGS_SOURCE}@{time.strftime('%Y%m%dT%H%M%S')}"
    step = max(1, int(batch_size))
    for start in range(0, len(rows), step):
        if not active():
            break
        # 一批一个事务。整批一个事务在本机是几千条候选一路压着写锁，页面上任何一次
        # 写入都要等它跑完；被停止或中途失败时，回滚的也只是当前这一批。
        batch = [dict(row) for row in rows[start:start + step]]
        with database.write_transaction() as connection:
            # 第 1 条判据是「这个字段现在是空的」，问的必须是账本此刻，不是候选件那一刻。
            # 候选落盘之后落过库、合并过实体的，快照还写着空，照它落库就是拿来源覆盖已有值。
            decided, genres = _refresh_auto_batch(connection, batch)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for row in batch:
                item_key = str(row.get("item_key") or "").strip()
                if not item_key:
                    continue
                if str(row.get("status") or "").strip() != "candidate":
                    continue
                row["candidates"] = _row_candidates(row, genres)
                prior = decided.get(item_key)
                if overwrite_requires_review(connection, row):
                    skipped += 1
                    continue
                landed = _land_union_tags(
                    connection, item_key, prior, row, genres, batch=union_batch, now=now)
                if landed:
                    applied.append({"item_key": item_key, "field": row.get("field"),
                                    "value": "、".join(landed), "assets": None})
                    continue
                if landed is not None:
                    refreshed += 1
                    continue
                if prior is not None and not _decision_reopens(prior, row["candidates"], code=row.get("code") or ""):
                    continue
                candidate = metadata_auto_apply_candidate(
                    connection, row, snapshot_root=snapshot_root)
                if candidate is None:
                    if _write_fc2_rejection(connection, item_key, prior, row,
                                            snapshot_root=snapshot_root, now=now):
                        rejected += 1
                    else:
                        skipped += 1
                    continue
                outcome, added = ((None, []) if prior is None else _reopened_approval(
                    connection, item_key, prior, row, candidate, batch=union_batch, now=now))
                if outcome == "extended":
                    applied.append({"item_key": item_key, "field": row.get("field"),
                                    "value": "、".join(added), "assets": None})
                    continue
                if outcome == "refreshed":
                    refreshed += 1
                    continue
                if outcome == "left":
                    skipped += 1
                    continue
                try:
                    count = _apply_metadata_candidate(
                        connection, row, candidate, now,
                        auto_owner(str(candidate.get("source") or "")))
                except ValueError:
                    # 落库条件在这一刻不成立（例如资产已删）：回到人工，不记决定。
                    skipped += 1
                    continue
                connection.execute(
                    "INSERT INTO review_decision(category,item_key,status,note,updated_at) "
                    "VALUES('metadata_fields',?,'approved',?,?) "
                    "ON CONFLICT(category,item_key) DO UPDATE SET status=excluded.status,"
                    "note=excluded.note,updated_at=excluded.updated_at",
                    (item_key, json.dumps(_auto_landing_note(candidate),
                                          ensure_ascii=False, separators=(",", ":")), now),
                )
                applied.append({"item_key": item_key, "field": row.get("field"),
                                "value": candidate.get("display_value"),
                                "assets": count})
    return {"ok": True, "applied": len(applied), "auto_rejected": rejected,
            "refreshed": refreshed, "left_to_review": skipped, "items": applied}


#: 落库候选里有值才原样抄进 note 的几项。
_LANDING_NOTE_EXTRAS = (
    "tag_policy",
    # 链上被压下的说法。自动结算的前提是事后答得出「当时还有哪些说法、为什么没选它」，
    # 而候选 CSV 会被下一批盖掉，这里是唯一跟着账本一起留下来的那一份（ADR-0038）。
    "overruled",
    # 三张表都不认的 genre。标签已经落了认得出的那些，这几个词单独等人收录，复核页按它
    # 把这一行重新摆出来。
    "pending_genres",
    # 按敬称算作一致的那几家与它们的原写法（ADR-0086）。
    "honorific_agreed",
    # FC2 出演者里一个艺名都没有、没算作证据的那几家（ADR-0079）。
    "descriptive_dropped",
)


def _auto_landing_note(candidate: dict) -> dict:
    """自动落库写进 `review_decision.note` 的那份记录。"""
    note = {
        "auto_applied": True,
        # 规则名记来源的实际级别，不写死 official：补空对 community 源同样成立，但两者
        # 日后要分开回溯时，note 是唯一还留着这个区别的地方。
        "rule": _auto_apply_rule(candidate, candidate.get("agreed_sources") or 1),
        "candidate_key": candidate.get("candidate_key"),
        "source": candidate.get("source"),
        "value": candidate.get("display_value"),
        **{key: candidate[key] for key in _LANDING_NOTE_EXTRAS if candidate.get(key)},
    }
    # 出演者写的是剪过的艺名，原文得留着：事后要答得出账本里这个名字是从哪一句剪出来的。
    raw = candidate.get("raw_display_value")
    if raw and raw != candidate.get("display_value"):
        note["raw_value"] = raw
    return note


def _reopened_approval(connection, item_key: str, decision: dict, row: dict,
                       candidate: dict, *, batch: str, now: str) -> tuple[str | None, list[str]]:
    """过期的批准重判出一条可落的候选之后怎么办，返回（去向，补进去的标签）。

    - `refreshed`：候选与账本现值是同一件事。真相字段不写，原决定的 status、
      updated_at 与 note 原有各项都不动，只在 note 里追加 `refreshed_candidate_key`，
      让 `metadata_decision_is_stale` 认得这一行已经对过。人批准过的标签，候选全在
      账本里（只是少了几个）也算这一种；
    - `extended`：人批准过的标签，重判出的这一套有账本没有的。按并集补上新增的那几个
      （`_extend_approved_tags`，ADR-0082），算补空，人批准过的一个不删；note 同样只追加
      `refreshed_candidate_key`，再加 `added_tags`；
    - `left`：会改变现值，而原决定是人批准的。不覆盖人的判断（ADR-0052），留在
      复核页交人；
    - None：会改变现值，原决定是自动落库写下的，照常重落并覆盖那条决定。

    原决定不是批准（过期的自动否决）时也返回 None：否决没有写过值，谈不上覆盖。
    """
    if str(decision.get("status") or "").strip() != "approved":
        return None, []
    field = str(row.get("field") or "").strip()
    added: list[str] = []
    if _same_as_current(connection, field, candidate, str(row.get("current_value") or "")):
        outcome = "refreshed"
    elif _is_automatic(decision):
        return None, []
    elif field == "tags" and (grown := _extend_approved_tags(
            connection, row, candidate, batch, now)) is not None:
        added = grown
        outcome = "extended" if added else "refreshed"
    else:
        return "left", []
    note = {**_decision_note(decision),
            "refreshed_candidate_key": str(candidate.get("candidate_key") or "").strip(),
            **({"added_tags": added} if added else {})}
    connection.execute(
        "UPDATE review_decision SET note=? WHERE category='metadata_fields' AND item_key=?",
        (json.dumps(note, ensure_ascii=False, separators=(",", ":")), item_key))
    return outcome, added


def genres_still_pending(genre_decisions: dict, decision: dict) -> bool:
    """这条自动落库是不是还留着没人收录的 genre（ADR-0038）。

    标签候选里认得出的那些已经落库了，`pending_genres` 记的是三张表都不认的词。
    这一行重新摆回队列不是为了再判一次标签，是为了判那几个词——所以判据只看词，
    收录一个就少一个，全收录完这一行就自己消失。

    静态表也要重查：`genre_taxonomy` 随代码一直在补，落库那一刻不认的词，今天可能
    已经在表里了（`_fold_genre_decisions` 的同一条理由）。
    """
    return any(resolve_genre(str(genre), genre_decisions) == UNMAPPED
               for genre in _decision_note(decision).get("pending_genres") or [])


def _write_fc2_rejection(connection, item_key: str, prior: dict | None, row: dict, *,
                         snapshot_root, now: str) -> bool:
    """落不下去的这一行按 `fc2_descriptive_rejection` 该否决时写下否决，返回写没写。

    自动否决只替换自动写下的决定：人批准过的那一行，候选变了也交回人。
    """
    if prior is not None and not _is_automatic(prior):
        return False
    rejection = fc2_descriptive_rejection(connection, row, snapshot_root=snapshot_root)
    if rejection is None:
        return False
    connection.execute(
        "INSERT INTO review_decision(category,item_key,status,note,updated_at) "
        "VALUES('metadata_fields',?,'rejected',?,?) "
        "ON CONFLICT(category,item_key) DO UPDATE SET status=excluded.status,"
        "note=excluded.note,updated_at=excluded.updated_at",
        (item_key, json.dumps(rejection, ensure_ascii=False, separators=(",", ":")), now))
    return True


def _newly_collected_genres(decision: dict | None, note: dict,
                            genre_decisions: dict) -> list[str]:
    """自动落库留下的生词里，已经有了去向、还没补过标签的那几个。

    只看自动落库的批准：`pending_genres` 只有它会写，人批准的行不归这里补。
    """
    if (str((decision or {}).get("status") or "").strip() != "approved"
            or note.get("auto_applied") is not True):
        return []
    collected = {str(word) for word in note.get("collected_genres") or []}
    return [str(word) for word in note.get("pending_genres") or []
            if str(word) not in collected
            and resolve_genre(str(word), genre_decisions) != UNMAPPED]


def _approved_candidate(note: dict, row: dict) -> dict | None:
    """这一行现存候选里，决定当时落的那一条（按 `candidate_key` 或重判追加的那一项认）。"""
    keys = {str(note.get(key) or "").strip()
            for key in ("candidate_key", "refreshed_candidate_key")} - {""}
    return next((c for c in row.get("candidates") or []
                 if str(c.get("candidate_key") or "").strip() in keys), None)


def _land_collected_genres(connection, item_key: str, decision: dict | None, row: dict,
                           genre_decisions: dict, *, batch: str, now: str) -> list[str] | None:
    """自动落库时留下生词的那一行，词收录了之后把收录出的标签补进账本（ADR-0082）。

    返回补进去的标签名（词收成非内容、或标签账本里早有时是空表）；这一行不归这里管时
    返回 None。只管 note 带 `pending_genres`、其中有词已经有了去向却还没补过的自动落库。

    不走整套重落：标签落过一次，现值就不空了，而非官方来源（javbus 这类）不能改非空
    的现值——整套重落的路对它们一条都走不通，收录出的标签就只在页面上消失、不进账本。
    这里用落库时那条候选（按 `candidate_key` 认，genre 已按现在的决定折过）走并集那一条，
    只插账本没有的，归属是 `batch`，与人批准标签的并集补齐同一套撤回口径。

    `pending_genres` 原样留着，复核页按现在的决定逐个重查（`genres_still_pending`）；
    补过的词记进 `collected_genres`，补进去的标签累加进 `added_tags`。撤回脚本去掉这两项
    之后，下一轮按那时的收录结果重补一次。
    """
    note = _decision_note(decision or {})
    if decision and _preferred_tags_changed(decision, row.get("candidates") or [], code=row.get("code") or ""):
        return None
    newly = _newly_collected_genres(decision, note, genre_decisions)
    candidate = _approved_candidate(note, row) if newly else None
    if candidate is None:
        return None
    resolved = [resolve_genre(word, genre_decisions) for word in newly]
    wanted = {normalize_entity_name(tag) for tag in current_tags(tag for tag in resolved if tag)}
    added = _extend_approved_tags(connection, row, candidate, batch, now, only=wanted)
    if added is None:
        return None
    done = {*map(str, note.get("collected_genres") or []), *newly}
    note = {**note, "collected_genres": [str(word) for word in note["pending_genres"]
                                         if str(word) in done]}
    if added:
        note["added_tags"] = list(dict.fromkeys([*note.get("added_tags", []), *added]))
    connection.execute(
        "UPDATE review_decision SET note=? WHERE category='metadata_fields' AND item_key=?",
        (json.dumps(note, ensure_ascii=False, separators=(",", ":")), item_key))
    return added


def _land_union_tags(connection, item_key: str, decision: dict | None, row: dict,
                     genre_decisions: dict, *, batch: str, now: str) -> list[str] | None:
    """两条只补不删的标签路：生词收录之后补（ADR-0082），与没记候选身份的人批准。"""
    landed = _land_collected_genres(connection, item_key, decision, row, genre_decisions,
                                    batch=batch, now=now)
    if landed is not None:
        return landed
    return _extend_unkeyed_tag_approval(connection, item_key, decision, row, batch=batch, now=now)


def _extend_unkeyed_tag_approval(connection, item_key: str, decision: dict | None, row: dict,
                                 *, batch: str, now: str) -> list[str] | None:
    """人批准的标签留痕里没有候选身份时，按留痕记的来源认回那条候选，按并集补新增。

    2026-09-01 那次官方标签回填写下的批准只记了 `source` 与 `value`，没有 `candidate_key`。
    `metadata_decision_is_stale` 认不出它们指向哪条候选，只能保守放过，之后收录的生词在
    这些片上就一直补不进去：CWPBD-126 的 `Lunch Box Fuck` 收录成火车便当以后，账本里仍然
    没有这个标签。复核页照旧放过这些行，这里只补标签、一个不删。

    同一来源的候选恰好一条、它的身份还没对过（不等于 `refreshed_candidate_key`）才补；补过
    （含没东西可补）把那条的身份记进 `refreshed_candidate_key`，候选再变时才会重来。返回
    补进去的标签名；这一行不归这里管时返回 None。
    """
    if (decision is None or str(row.get("field") or "").strip() != "tags"
            or str(decision.get("status") or "").strip() != "approved"
            or _is_automatic(decision)):
        return None
    note = _decision_note(decision)
    source = str(note.get("source") or "").strip()
    if note.get("candidate_key") or not source:
        return None
    if _preferred_tags_changed(decision, row.get("candidates") or [], code=row.get("code") or ""):
        source = preferred_tag_source(str(row.get("code") or "")) or source
    matches = [candidate for candidate in row.get("candidates") or []
               if str(candidate.get("source") or "").strip() == source]
    key = str(matches[0].get("candidate_key") or "").strip() if len(matches) == 1 else ""
    if not key or key == str(note.get("refreshed_candidate_key") or "").strip():
        return None
    added = _extend_approved_tags(connection, row, matches[0], batch, now)
    if added is None:
        return None
    note = {**note, "refreshed_candidate_key": key}
    if added:
        note["added_tags"] = list(dict.fromkeys([*note.get("added_tags", []), *added]))
    connection.execute(
        "UPDATE review_decision SET note=? WHERE category='metadata_fields' AND item_key=?",
        (json.dumps(note, ensure_ascii=False, separators=(",", ":")), item_key))
    return added


def _decision_reopens(decision: dict, candidates: list[dict], *, code: str = "") -> bool:
    """已有决定的这一行要不要重新走一遍判据。

    过期的决定（`metadata_decision_is_stale`）等于没有决定：页面已经把它摆回人工队列，
    自动落库不重判的话，按现行判据落得下去的那些就一直停在人面前。

    带 `pending_genres` 的自动落库不重判：标签已经落了，那一行等的是生词收录，
    由 genre 那一侧管。
    """
    if not metadata_decision_is_stale(decision, candidates, code=code):
        return False
    return (not _decision_note(decision).get("pending_genres")
            or _preferred_tags_changed(decision, candidates, code=code))
