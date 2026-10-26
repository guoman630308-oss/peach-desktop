#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""同一个人被记成两条实体的去重（跨 kind 与同 kind 两类）。

`entity` 的唯一约束是 `(kind, normalized_name)`，所以同名但 kind 不同的两条记录
可以并存。实际发生了 35 组：卡片上的头像和名字会一个跳 `/performers/x`、另一个跳
`/creators/x`，统计、筛选和资料页也各算各的。

第三类是名录页重复：两条实体各按自己的名字检索，链接安装时落到同一个 minnano-av
名录页，那就是站方认为的同一个人。判据见 `_directory_identity_plan`。

第四类是艺名重复：一个名字已经作为别名登记在另一条实体上，它却还自己占着一条实体。
判据与闸门见 `_stage_name_plan`。

两条来路很清楚：

    creator   ← source='legacy:asset'，从目录名投影出来（ADR-0013：目录名只是候选证据）
    performer ← source='r18:performer' / 'javbus:performer'（真实发行元数据）
                或 'javinizer:<站点>:performer'（刮削链新建，同样是发行元数据）
                或 'performer'（Stash 扁平模型）

除了完全同名，还会处理一类高置信变体：两边关联的非空作品集合完全相同，且 performer
别名等于 creator 名，或 creator 名由 performer 本名和账号别名拼成。名字相似但作品集合
不同的不会自动合并。

**保留哪一边只看 provenance，不看数量**：

- performer 侧带 `r18:performer` / `javbus:performer` / `javinizer:<站点>:performer` 的保留
  performer。那是发行元数据，creator 侧只是
  网盘目录名恰好等于艺名，正是 ADR-0013 要防的那种误投影。
- 否则保留 creator。剩下的 performer 断言全部来自 Stash 的扁平 performer 模型——
  `docs/STASH.md` 已把它判为已知缺陷（把上传者、合集和演员塞进同一个平面）——而
  creator 侧往往对应一个真实的本地目录。

按数量投票会翻车：`小桃` 的 performer 侧有 151 条、creator 侧只有 15 条，但它是
本地 `media/创作者/小桃` 的上传者，不是女优。

## 同 kind 的目录名投影

跨 kind 那一轮合并完成后，`R:\Media\<本名 + 账号名>` 这类目录还会各自留下一条**同为
creator** 的实体，`(kind, normalized_name)` 唯一约束拦不住它：`小桃` / `小桃 shixiaotaone`
和 `猫猫碎冰冰` / `猫猫碎冰冰 & 趣趣` 就是这样在详情页和 `/creators` 索引页并排出现的。

判据要求四项同时成立，缺一项就留给人工，不自动合并：

1. 两条同 kind，被丢弃方的作品集合非空且**完全落在**保留方的作品集合里；
2. 被丢弃方的名字词集**真包含**保留方的名字词集，多出来的词（去掉 `&` 之类的连接符后）
   全部是保留方的已登记别名；
3. 被丢弃方只有 `legacy:asset` 断言，且没有别名、没有外部引用——只是目录名投影；
4. 该投影只匹配到唯一一个保留方。

保留方是带别名和外部引用的规范实体，被丢弃的名字降为别名，扁平 `asset.creator` 一并
改写成保留名（ADR-0005：兼容投影跟着规范关系走）。

合并不可逆。默认只产出复核 CSV，`--apply` 才写 ledger 且必须给 `--backup`。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import GENERATED_DIR
from peach.entities import (
    canonicalize_entity_name,
    collapse_repeated_entity_name,
    merge_entity,
    normalize_entity_name,
)
from peach.review_csv import write_rows
from peach.scripting import add_ledger_write_args, counts_of, open_for_write, verify_after_write


#: 真实发行元数据的来源标记。只有这些能把一条断言判成「这是女优，不是上传者」。
RELEASE_SOURCES = frozenset({"r18:performer", "javbus:performer"})

#: 刮削链新建 performer 时写的来源整族：`javinizer:javdb:performer`、
#: `javinizer:r18dev:performer`、`javinizer:dmm:performer` …… 站点还会增加，所以按形状认
#: 而不是逐个登记。它们和 `r18:performer` 是同一类东西——发行方元数据，不是 Stash 那套
#: 把上传者和演员塞进一个平面的扁平模型；漏掉这一族会让刚刮出来的 performer 被当成扁平
#: 断言，合并时保留目录名投影出来的 creator 侧。
RELEASE_SOURCE_PREFIX = "javinizer:"
RELEASE_SOURCE_SUFFIX = ":performer"
MERGE_ALIAS_SOURCE = "merge:duplicate-identity"

#: 目录名里把两个称呼拼在一起的连接符。它们本身不是名字的一部分。
JOINER_TOKENS = frozenset({"&", "＆", "+", "＋", "/", "／", "、", "，", ","})

#: 目录名投影的唯一来源标记：没有别名、没有外部引用，只有从路径投影出来的断言。
PROJECTION_SOURCES = frozenset({"legacy:asset"})

#: 链接安装把名录页号写进了自由文本证据里（`entity_link.metadata_json` 的 `evidence`）。
#: minnano-av 每位女优一页，页号就是身份——比名字强，也比别名强：两条实体各按自己的
#: 名字检索、落到同一页，说明站方认为这是同一个人。页号本身该升成 `entity_external_ref`，
#: 那样唯一约束会先一步拦住重复；在那之前只能从这段文本里读回来。
MINNANO_ACTRESS = re.compile(r"minnano-av\s+actress(\d+)")

#: 能证明「这个名字是那个人的另一个艺名」的别名来源。本地化、简繁转换和系列译名
#: 写的别名说的是同一个名字的另一种写法，不是另一个艺名，不能拿来判同人。
STAGE_NAME_ALIAS_SOURCES = ("r18:performer", "javbus:performer", "avdb-actor-mapping")


def _fold(value: str) -> str:
    """与 entity 规范化一致，并忽略账号名前缀和多余空白。"""
    return " ".join(part.lstrip("@").casefold() for part in str(value).split())


def _fuzzy_evidence(
    creator_name: str, performer_name: str, performer_aliases: list[str],
) -> str | None:
    """返回可自动合并的名字证据；仅由作品集合相同的调用方使用。"""
    creator = _fold(creator_name)
    performer = _fold(performer_name)
    aliases = {_fold(alias) for alias in performer_aliases if _fold(alias)}
    if creator in aliases:
        return "performer 别名等于 creator 名"
    if creator != performer and performer in creator:
        account_aliases = {
            alias for alias in aliases
            if alias != performer and len(re.sub(r"\s+", "", alias)) >= 3
        }
        if any(alias in creator for alias in account_aliases):
            return "creator 名由 performer 本名与账号别名组成"
    return None


def _asset_relation(
    creator_assets: frozenset[int], performer_assets: frozenset[int],
) -> str | None:
    """两边作品集合的包含关系；没有包含关系就不是同一批文件，返回 None。

    真子集也算包含关系。只认「完全相同」的话，`哆米`(7 部) 与 `哆米 Dolmi24`(6 部)
    这种一侧多出一两部的目录投影永远落在判据外——多出的那几部恰恰是只被 Stash 认成
    performer、没有对应本地目录的作品，属于常态而不是反证。放宽的证据强度由名字
    那一重（别名等于 creator 名，或 creator 名由本名与账号别名拼成）兜底，
    并把包含方向和数量写进复核 CSV，交给人看。
    """
    if not creator_assets or not performer_assets:
        return None
    if creator_assets == performer_assets:
        return f"作品集合完全相同（各 {len(creator_assets)} 部）"
    if creator_assets < performer_assets:
        return (f"creator 作品集合真包含于 performer"
                f"（{len(creator_assets)}/{len(performer_assets)} 部）")
    if performer_assets < creator_assets:
        return (f"performer 作品集合真包含于 creator"
                f"（{len(performer_assets)}/{len(creator_assets)} 部）")
    return None


def _release_backed(sources: str) -> bool:
    present = {source for source in str(sources or "").split(",") if source}
    if RELEASE_SOURCES.intersection(present):
        return True
    return any(source.startswith(RELEASE_SOURCE_PREFIX)
               and source.endswith(RELEASE_SOURCE_SUFFIX) for source in present)


def _name_tokens(value: str) -> frozenset[str]:
    """名字按空白切成词集；空词和纯连接符不算名字的一部分。"""
    return frozenset(
        token for token in (_fold(part) for part in str(value).split())
        if token and token not in JOINER_TOKENS
    )


def _projection_only(sources: str, aliases: int, refs: int) -> bool:
    """只有路径投影出来的断言，没有别名也没有外部引用。"""
    present = {source for source in str(sources or "").split(",") if source}
    return bool(present) and present <= PROJECTION_SOURCES and not aliases and not refs


def _variant_evidence(
    keep_name: str, drop_name: str, keep_aliases: list[str],
) -> str | None:
    """返回同 kind 目录名投影可自动合并的名字证据。

    仅由「作品集合被完全包含」的调用方使用：只有名字证据不足以判定同人，
    `猫猫碎冰冰 & 趣趣` 这种拼名目录必须靠「同一批文件」加「多出的词是已登记别名」
    两重证据才成立。
    """
    keep_tokens = _name_tokens(keep_name)
    drop_tokens = _name_tokens(drop_name)
    if not keep_tokens or not keep_tokens < drop_tokens:
        return None
    extra = drop_tokens - keep_tokens
    aliases = {_fold(alias) for alias in keep_aliases if _fold(alias)}
    if not extra or not extra <= aliases:
        return None
    return "同 kind 目录名投影：多出的词是保留方别名"


def collect(connection: sqlite3.Connection) -> list[dict[str, object]]:
    """找出高置信跨 kind 重复实体，并按 provenance 定出保留哪一边。"""
    connection.row_factory = sqlite3.Row
    entity_rows = connection.execute(
        """
        SELECT e.id, e.kind, e.canonical_name, e.normalized_name,
               (SELECT count(*) FROM asset_entity ae WHERE ae.entity_id=e.id) AS links,
               (SELECT group_concat(DISTINCT ae.source) FROM asset_entity ae
                 WHERE ae.entity_id=e.id) AS sources,
               (SELECT count(*) FROM entity_alias a WHERE a.entity_id=e.id) AS alias_count,
               (SELECT count(*) FROM entity_external_ref r WHERE r.entity_id=e.id) AS ref_count
        FROM entity e
        WHERE e.kind IN ('creator','performer')
        ORDER BY e.id
        """
    ).fetchall()

    aliases: dict[int, list[str]] = {}
    for row in connection.execute(
        "SELECT entity_id,alias FROM entity_alias ORDER BY entity_id,alias"
    ):
        aliases.setdefault(int(row["entity_id"]), []).append(str(row["alias"]))
    asset_members: dict[int, set[int]] = {}
    for row in connection.execute(
        "SELECT entity_id,asset_id FROM asset_entity ORDER BY entity_id,asset_id"
    ):
        asset_members.setdefault(int(row["entity_id"]), set()).add(int(row["asset_id"]))
    assets = {
        entity_id: frozenset(asset_ids)
        for entity_id, asset_ids in asset_members.items()
    }

    creators = [row for row in entity_rows if row["kind"] == "creator"]
    performers = [row for row in entity_rows if row["kind"] == "performer"]
    by_normalized_creator = {row["normalized_name"]: row for row in creators}
    by_normalized_performer = {row["normalized_name"]: row for row in performers}

    candidates: dict[tuple[int, int], tuple[sqlite3.Row, sqlite3.Row, str]] = {}
    for normalized in sorted(set(by_normalized_creator) & set(by_normalized_performer)):
        creator = by_normalized_creator[normalized]
        performer = by_normalized_performer[normalized]
        candidates[(int(creator["id"]), int(performer["id"]))] = (
            creator, performer, "规范名完全相同")

    performer_ids_by_name: dict[str, set[int]] = {}
    performer_by_id = {int(row["id"]): row for row in performers}
    for performer in performers:
        performer_id = int(performer["id"])
        values = [str(performer["canonical_name"]), *aliases.get(performer_id, [])]
        for value in values:
            performer_ids_by_name.setdefault(_fold(value), set()).add(performer_id)
    for creator in creators:
        repeated = collapse_repeated_entity_name(str(creator["canonical_name"]))
        if repeated == str(creator["canonical_name"]).strip():
            continue
        matched_ids = performer_ids_by_name.get(_fold(repeated), set())
        release_ids = {
            entity_id for entity_id in matched_ids
            if _release_backed(str(performer_by_id[entity_id]["sources"] or ""))
        }
        if len(release_ids) != 1:
            continue
        performer = performer_by_id[next(iter(release_ids))]
        candidates[(int(creator["id"]), int(performer["id"]))] = (
            creator, performer, "creator 规范名是完整重复串",
        )

    performers_by_asset: dict[int, set[int]] = {}
    for performer in performers:
        for asset_id in assets.get(int(performer["id"]), frozenset()):
            performers_by_asset.setdefault(asset_id, set()).add(int(performer["id"]))

    fuzzy: list[tuple[sqlite3.Row, sqlite3.Row, str]] = []
    for creator in creators:
        creator_assets = assets.get(int(creator["id"]), frozenset())
        if not creator_assets:
            continue
        nearby = {
            performer_id
            for asset_id in creator_assets
            for performer_id in performers_by_asset.get(asset_id, ())
        }
        for performer_id in sorted(nearby):
            performer = performer_by_id[performer_id]
            key = (int(creator["id"]), performer_id)
            if key in candidates:
                continue
            relation = _asset_relation(creator_assets, assets.get(performer_id, frozenset()))
            if not relation:
                continue
            evidence = _fuzzy_evidence(
                str(creator["canonical_name"]), str(performer["canonical_name"]),
                aliases.get(performer_id, []),
            )
            if evidence:
                fuzzy.append((creator, performer, f"{evidence}；{relation}"))

    # 一方若同时命中多个候选，就留给人工复核，避免同作品集合内的连锁误并。
    fuzzy_counts = Counter(
        (side, entity_id)
        for creator, performer, _ in fuzzy
        for side, entity_id in (("creator", int(creator["id"])),
                                ("performer", int(performer["id"])))
    )
    for creator, performer, evidence in fuzzy:
        if (fuzzy_counts[("creator", int(creator["id"]))] == 1
                and fuzzy_counts[("performer", int(performer["id"]))] == 1):
            candidates[(int(creator["id"]), int(performer["id"]))] = (
                creator, performer, evidence)

    plan: list[dict[str, object]] = []
    for _, (creator, performer, match_evidence) in sorted(candidates.items()):
        release_backed = _release_backed(performer["sources"] or "")
        keep, drop = (performer, creator) if release_backed else (creator, performer)
        plan.append({
            "normalized_name": creator["normalized_name"],
            "match_evidence": match_evidence,
            "keep_kind": keep["kind"],
            "keep_id": keep["id"],
            "keep_name": keep["canonical_name"],
            "keep_links": keep["links"],
            "drop_kind": drop["kind"],
            "drop_id": drop["id"],
            "drop_name": drop["canonical_name"],
            "drop_links": drop["links"],
            "keep_sources": keep["sources"] or "",
            "drop_sources": drop["sources"] or "",
            "evidence": "发行元数据" if release_backed else "目录身份 + Stash 扁平 performer",
        })
    # 跨 kind 已经排进计划的实体不再参与同 kind 那一趟：先合并会把 id 删掉，
    # 第二趟再引用同一个 id 就是合并进一条不存在的实体。
    engaged = {int(row["keep_id"]) for row in plan} | {int(row["drop_id"]) for row in plan}
    plan.extend(_variant_plan(entity_rows, aliases, assets, engaged))
    engaged |= {int(row["keep_id"]) for row in plan} | {int(row["drop_id"]) for row in plan}
    stage_aliases = [
        (int(row["entity_id"]), str(row["alias"]), str(row["normalized_alias"]),
         str(row["source"] or ""))
        for row in connection.execute(
            "SELECT entity_id,alias,normalized_alias,source FROM entity_alias"
            " ORDER BY entity_id,alias")
    ]
    refs: dict[int, dict[str, str]] = {}
    for row in connection.execute(
        "SELECT entity_id,provider,external_id FROM entity_external_ref ORDER BY entity_id"
    ):
        refs.setdefault(int(row["entity_id"]), {})[str(row["provider"])] = str(
            row["external_id"])
    directory = _directory_identity_plan(entity_rows, connection, engaged)
    plan.extend(directory)
    engaged |= {int(row["keep_id"]) for row in directory}
    engaged |= {int(row["drop_id"]) for row in directory}
    plan.extend(_stage_name_plan(entity_rows, aliases, stage_aliases, refs, engaged))
    return plan


def _directory_identity_plan(
    entity_rows: list[sqlite3.Row],
    connection: sqlite3.Connection,
    engaged: set[int],
) -> list[dict[str, object]]:
    """两条实体的官网链接是按同一个 minnano-av 名录页装上的，那就是同一个人。

    实测 `白石亚子`(#8204) 与 `Ako Shiraishi`(#8435)：前者按「白石あこ」检索、后者按
    「平沢すず」检索，两条都落到 actress37250。艺名换过一次，账本就各存了一条。

    这条证据压过 `_stage_name_plan` 的「别名互不相识」那道闸——那道闸防的是规范名写错人，
    而名录页号恰恰能分清「写错了人」和「同一个人的两个艺名」：写错人时两侧不会落到同一页。

    保留规范名已经本地化的那一侧（用户 2026-09-04 的口径：用更知名的名字，其余留作别名），
    两侧都本地化或都没有时保留作品多的一侧。同一页挂三条以上一律交人工：那要先决定
    并的顺序，不是脚本该替人做的。
    """
    by_id = {int(row["id"]): row for row in entity_rows}
    owners: dict[str, set[int]] = {}
    for entity_id, payload in connection.execute(
        "SELECT entity_id,metadata_json FROM entity_link ORDER BY entity_id"
    ):
        if int(entity_id) not in by_id:
            continue
        try:
            evidence = str(json.loads(payload or "{}").get("evidence") or "")
        except (TypeError, ValueError):
            continue
        found = MINNANO_ACTRESS.search(evidence)
        if found:
            owners.setdefault(found.group(1), set()).add(int(entity_id))

    plan: list[dict[str, object]] = []
    for actress, ids in sorted(owners.items()):
        usable = sorted(ids - engaged)
        if len(usable) != 2:
            continue
        first, second = (by_id[entity_id] for entity_id in usable)
        localized = [row for row in (first, second)
                     if not str(row["canonical_name"]).isascii()]
        if len(localized) == 1:
            keep = localized[0]
            why = "保留已本地化的规范名"
        else:
            keep = max((first, second), key=lambda row: (int(row["links"]), -int(row["id"])))
            why = "两侧规范名同为本地化或同为罗马字，保留作品多的一侧"
        drop = second if keep is first else first
        plan.append({
            "normalized_name": drop["normalized_name"],
            "match_evidence": f"两侧官网链接按同一个 minnano-av 名录页 actress{actress} 装上；{why}",
            "keep_kind": keep["kind"],
            "keep_id": keep["id"],
            "keep_name": keep["canonical_name"],
            "keep_links": keep["links"],
            "drop_kind": drop["kind"],
            "drop_id": drop["id"],
            "drop_name": drop["canonical_name"],
            "drop_links": drop["links"],
            "keep_sources": keep["sources"] or "",
            "drop_sources": drop["sources"] or "",
            "evidence": "名录页号",
        })
    return plan


def _stage_name_plan(
    entity_rows: list[sqlite3.Row],
    aliases: dict[int, list[str]],
    stage_aliases: list[tuple[int, str, str, str]],
    refs: dict[int, dict[str, str]],
    engaged: set[int],
) -> list[dict[str, object]]:
    """艺名已经登记成另一条实体的别名，那个名字却还自己占着一条实体。

    实测 `飯岡かなこ`（1 部）与 `森泽佳奈`（2 部）：r18 早已把前者记成后者的别名，
    `/performers/飯岡かなこ` 却还在。`link_entity` 只在建实体那一刻查别名，两条都是
    Stash 那次批量导入建的，别名是之后才由 r18 补上的——建的时候没有可查的东西。

    三道闸都是为了排掉实测里的假阳性，缺一条就会把两个人并成一个：

    1. **别名要出自发行元数据。** 本地化与简繁转换也往别名表里写（`綾乃梓` 之于
       `绫乃梓`），它们说的是同一个名字的另一种写法，证不了「这是那个人的另一个艺名」。
       系列译名同样落在别名表里（`Night Safari` 写进 `クロロホルムレ●プ`），虽然这一趟
       只扫 person，判据本身不该依赖扫描范围。
    2. **被丢弃方自己的别名必须全在保留方的名字集里。** `Ako Shiraishi` 这条实体的
       每一个别名和 r18 引用都指向 平沢すず，规范名却写着另一个人——它是错标，
       并进 `白石亚子` 等于把两位女优搅在一起。
    3. **同一个 provider 的外部引用不能各指一个 ID。** `NOZOMI`（stash 610）撞上
       `绫乃梓`（stash 538）的别名 `Nozomi`，那是素人企划里常见的通名撞上罗马字读音。

    命中两条以上保留方同样交人工：这条判据唯一的身份保证就是那一条别名。
    """
    by_id = {int(row["id"]): row for row in entity_rows}
    owners: dict[tuple[str, str], set[int]] = {}
    alias_note: dict[tuple[str, int], tuple[str, str]] = {}
    for entity_id, alias, normalized, source in stage_aliases:
        row = by_id.get(entity_id)
        if row is None or not normalized:
            continue
        if not source.startswith(STAGE_NAME_ALIAS_SOURCES):
            continue
        owners.setdefault((str(row["kind"]), normalized), set()).add(entity_id)
        alias_note[(normalized, entity_id)] = (alias, source)

    plan: list[dict[str, object]] = []
    for drop_id, drop in sorted(by_id.items()):
        if drop_id in engaged:
            continue
        normalized = str(drop["normalized_name"])
        found = sorted(owners.get((str(drop["kind"]), normalized), set()) - {drop_id}
                       - engaged)
        if len(found) != 1:
            continue
        keep_id = found[0]
        keep = by_id[keep_id]
        keep_names = {_fold(str(keep["canonical_name"]))}
        keep_names |= {_fold(value) for value in aliases.get(keep_id, [])}
        stray = [value for value in aliases.get(drop_id, [])
                 if _fold(value) and _fold(value) not in keep_names]
        if stray:
            continue
        drop_refs, keep_refs = refs.get(drop_id, {}), refs.get(keep_id, {})
        if any(keep_refs[provider] != value for provider, value in drop_refs.items()
               if provider in keep_refs):
            continue
        alias, source = alias_note[(normalized, keep_id)]
        plan.append({
            "normalized_name": normalized,
            "match_evidence": f"艺名已登记为保留方别名「{alias}」（{source}）",
            "keep_kind": keep["kind"],
            "keep_id": keep["id"],
            "keep_name": keep["canonical_name"],
            "keep_links": keep["links"],
            "drop_kind": drop["kind"],
            "drop_id": drop["id"],
            "drop_name": drop["canonical_name"],
            "drop_links": drop["links"],
            "keep_sources": keep["sources"] or "",
            "drop_sources": drop["sources"] or "",
            "evidence": "发行元数据登记的艺名",
        })
    return plan


def _variant_plan(
    entity_rows: list[sqlite3.Row],
    aliases: dict[int, list[str]],
    assets: dict[int, frozenset[int]],
    engaged: set[int],
) -> list[dict[str, object]]:
    """同 kind 的目录名投影：`小桃 shixiaotaone` 并回 `小桃`。

    先按别名反查候选保留方——多出来的词必须是别名，所以至少有一个词能命中——再逐项
    核对作品集合、词集和 provenance。命中多个保留方的投影一律不合并。
    """
    by_id = {int(row["id"]): row for row in entity_rows}
    alias_owners: dict[tuple[str, str], set[int]] = {}
    for entity_id, values in aliases.items():
        row = by_id.get(entity_id)
        if row is None:
            continue
        for alias in values:
            folded = _fold(alias)
            if folded:
                alias_owners.setdefault((str(row["kind"]), folded), set()).add(entity_id)

    matches: dict[int, list[tuple[sqlite3.Row, str]]] = {}
    for drop_id, drop in by_id.items():
        drop_assets = assets.get(drop_id, frozenset())
        if drop_id in engaged or not drop_assets or not _projection_only(
            drop["sources"], int(drop["alias_count"]), int(drop["ref_count"])
        ):
            continue
        keep_ids = {
            keep_id
            for token in _name_tokens(str(drop["canonical_name"]))
            for keep_id in alias_owners.get((str(drop["kind"]), token), set())
            if keep_id != drop_id and keep_id not in engaged
        }
        for keep_id in sorted(keep_ids):
            keep = by_id[keep_id]
            if not drop_assets <= assets.get(keep_id, frozenset()):
                continue
            evidence = _variant_evidence(
                str(keep["canonical_name"]), str(drop["canonical_name"]),
                aliases.get(keep_id, []),
            )
            if evidence:
                matches.setdefault(drop_id, []).append((keep, evidence))

    plan: list[dict[str, object]] = []
    for drop_id, found in sorted(matches.items()):
        if len(found) != 1:
            continue
        keep, evidence = found[0]
        drop = by_id[drop_id]
        plan.append({
            "normalized_name": drop["normalized_name"],
            "match_evidence": evidence,
            "keep_kind": keep["kind"],
            "keep_id": keep["id"],
            "keep_name": keep["canonical_name"],
            "keep_links": keep["links"],
            "drop_kind": drop["kind"],
            "drop_id": drop["id"],
            "drop_name": drop["canonical_name"],
            "drop_links": drop["links"],
            "keep_sources": keep["sources"] or "",
            "drop_sources": drop["sources"] or "",
            "evidence": "规范实体 + 目录名投影",
        })
    return plan


def apply_rows(
    connection: sqlite3.Connection, rows: list[dict[str, object]],
) -> dict[str, int]:
    """逐对合并，并同步兼容投影 `asset.creator` / `演员:` 标签。

    扁平字段必须跟着规范关系走（ADR-0005），否则资料页和卡片会各说各话：

    - 保留 creator：被并进来的资产可能没有 creator，补上；已有别的值不覆盖。
    - 保留 creator 且丢弃方也是 creator：扁平字段里写的就是被丢弃的目录名，改写成保留名。
      只填空不改写会留下一个没有实体的名字，详情页的归属兜底和按 creator 的查询都还认它。
    - 保留 performer：creator 实体没了，等值的扁平字段一并清掉。
    """
    counts = Counter()
    for row in rows:
        keep_id, drop_id = int(row["keep_id"]), int(row["drop_id"])
        moving = [
            int(item[0]) for item in connection.execute(
                "SELECT asset_id FROM asset_entity WHERE entity_id=?", (drop_id,))
        ]
        moved = merge_entity(
            connection, target_id=keep_id, source_id=drop_id,
            source_name=str(row["drop_name"]), alias_source=MERGE_ALIAS_SOURCE,
        )
        counts["merged"] += 1
        counts["assets"] += moved["assets"]
        counts["aliases"] += moved["aliases"]
        counts["dropped_refs"] += moved["dropped_refs"]

        if row["keep_kind"] == "creator":
            if row["drop_kind"] == "creator":
                connection.execute(
                    "UPDATE asset SET creator=? WHERE creator=?",
                    (row["keep_name"], row["drop_name"]))
                counts["flat_rewritten"] += connection.execute(
                    "SELECT changes()").fetchone()[0]
            for asset_id in moving:
                connection.execute(
                    "UPDATE asset SET creator=? WHERE id=? AND (creator IS NULL OR creator='')",
                    (row["keep_name"], asset_id))
                counts["flat_filled"] += connection.execute("SELECT changes()").fetchone()[0]
                connection.execute(
                    "DELETE FROM asset_tag WHERE asset_id=? AND tag=?",
                    (asset_id, f"演员:{row['drop_name']}"))
                counts["actor_tags_removed"] += connection.execute(
                    "SELECT changes()").fetchone()[0]
        elif row["keep_kind"] == "series":
            # 扁平 `asset.series` 也得跟着规范关系走（ADR-0005）。只搬关系不改这一列，
            # 卡片上还写着被丢弃的系列名，按它查也还查得到——资料页和卡片各说各话。
            connection.execute("UPDATE asset SET series=? WHERE series=?",
                               (row["keep_name"], row["drop_name"]))
            counts["flat_rewritten"] += connection.execute("SELECT changes()").fetchone()[0]
        else:
            connection.execute(
                "UPDATE asset SET creator=NULL WHERE creator=?", (row["drop_name"],))
            counts["flat_cleared"] += connection.execute("SELECT changes()").fetchone()[0]
            old_tag, new_tag = f"演员:{row['drop_name']}", f"演员:{row['keep_name']}"
            connection.execute(
                "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) "
                "SELECT asset_id,?,confidence,source FROM asset_tag WHERE tag=?",
                (new_tag, old_tag),
            )
            counts["actor_tags_inserted"] += connection.execute(
                "SELECT changes()"
            ).fetchone()[0]
            connection.execute("DELETE FROM asset_tag WHERE tag=?", (old_tag,))
            counts["actor_tags_rewritten"] += connection.execute(
                "SELECT changes()"
            ).fetchone()[0]
            if collapse_repeated_entity_name(str(row["drop_name"])) != str(row["drop_name"]):
                connection.execute(
                    "DELETE FROM entity_alias WHERE entity_id=? AND normalized_alias=?",
                    (keep_id, normalize_entity_name(str(row["drop_name"]))),
                )
                counts["bad_aliases_removed"] += connection.execute(
                    "SELECT changes()"
                ).fetchone()[0]
    return dict(counts)


def collect_repeated_projections(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    """审计 person 名称的完整重复串及其所有兼容投影。"""
    connection.row_factory = sqlite3.Row
    names: set[str] = set()
    names.update(str(row[0]) for row in connection.execute(
        "SELECT DISTINCT creator FROM asset WHERE creator IS NOT NULL AND creator<>''"
    ))
    names.update(str(row[0])[3:] for row in connection.execute(
        "SELECT DISTINCT tag FROM asset_tag WHERE tag LIKE '演员:%'"
    ))
    names.update(str(row[0]) for row in connection.execute(
        "SELECT DISTINCT alias FROM entity_alias a JOIN entity e ON e.id=a.entity_id "
        "WHERE e.kind IN ('creator','performer')"
    ))
    names.update(str(row[0]) for row in connection.execute(
        "SELECT canonical_name FROM entity WHERE kind IN ('creator','performer')"
    ))

    plan: list[dict[str, object]] = []
    for bad_name in sorted(names, key=str.casefold):
        collapsed = collapse_repeated_entity_name(bad_name)
        cleaned = canonicalize_entity_name("performer", bad_name)
        if collapsed == bad_name.strip() and cleaned:
            continue
        normalized = {normalize_entity_name(bad_name)}
        if cleaned:
            normalized.add(normalize_entity_name(cleaned))
        marks = ",".join("?" * len(normalized))
        targets = connection.execute(
            "SELECT e.id,e.canonical_name,"
            "(SELECT group_concat(DISTINCT ae.source) FROM asset_entity ae "
            " WHERE ae.entity_id=e.id) sources "
            "FROM entity e WHERE e.kind='performer' AND (e.normalized_name IN (" + marks + ") "
            "OR e.id IN (SELECT a.entity_id FROM entity_alias a "
            "WHERE a.normalized_alias IN (" + marks + "))) GROUP BY e.id ORDER BY e.id",
            tuple(normalized) + tuple(normalized),
        ).fetchall()
        release_targets = [row for row in targets if _release_backed(str(row["sources"] or ""))]
        asset_ids = {
            int(row[0]) for row in connection.execute(
                "SELECT id FROM asset WHERE creator=? UNION "
                "SELECT asset_id FROM asset_tag WHERE tag=?",
                (bad_name, f"演员:{bad_name}"),
            )
        }
        linked_target_ids = {
            int(row["id"]) for row in release_targets
            if asset_ids and connection.execute(
                "SELECT 1 FROM asset_entity WHERE entity_id=? AND asset_id IN ("
                + ",".join("?" * len(asset_ids)) + ") LIMIT 1",
                (int(row["id"]), *sorted(asset_ids)),
            ).fetchone()
        }
        alias_owner_ids = {
            int(row[0]) for row in connection.execute(
                "SELECT DISTINCT e.id FROM entity_alias a JOIN entity e ON e.id=a.entity_id "
                "WHERE e.kind='performer' AND a.alias=?",
                (bad_name,),
            )
            if any(int(target["id"]) == int(row[0]) for target in release_targets)
        }
        chosen = None
        if len(alias_owner_ids) == 1:
            chosen_id = next(iter(alias_owner_ids))
            chosen = next(row for row in release_targets if int(row["id"]) == chosen_id)
        elif len(linked_target_ids) == 1:
            chosen_id = next(iter(linked_target_ids))
            chosen = next(row for row in release_targets if int(row["id"]) == chosen_id)
        elif len(release_targets) == 1:
            chosen = release_targets[0]
        action = "remove-invalid" if not cleaned else "review"
        if chosen is not None:
            action = "use-performer"
        plan.append({
            "bad_name": bad_name,
            "collapsed_name": cleaned,
            "action": action,
            "target_id": int(chosen["id"]) if chosen else "",
            "target_name": str(chosen["canonical_name"]) if chosen else "",
            "flat_assets": connection.execute(
                "SELECT count(*) FROM asset WHERE creator=?", (bad_name,)
            ).fetchone()[0],
            "actor_tags": connection.execute(
                "SELECT count(*) FROM asset_tag WHERE tag=?", (f"演员:{bad_name}",)
            ).fetchone()[0],
            "aliases": connection.execute(
                "SELECT count(*) FROM entity_alias WHERE alias=?", (bad_name,)
            ).fetchone()[0],
            "canonical_entities": connection.execute(
                "SELECT count(*) FROM entity WHERE canonical_name=? AND kind IN ('creator','performer')",
                (bad_name,),
            ).fetchone()[0],
            "evidence": (
                "重复别名所属 performer" if alias_owner_ids
                else "已关联的发行 performer" if linked_target_ids
                else "唯一发行 performer" if chosen
                else "已知页面控件文字" if not cleaned
                else "目标不唯一或未取得"
            ),
        })
    return plan


def apply_repeated_projections(
    connection: sqlite3.Connection, rows: list[dict[str, object]],
) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        if row["action"] == "review":
            counts["review_left"] += 1
            continue
        bad_name = str(row["bad_name"])
        connection.execute("UPDATE asset SET creator=NULL WHERE creator=?", (bad_name,))
        counts["flat_cleared"] += connection.execute("SELECT changes()").fetchone()[0]
        old_tag = f"演员:{bad_name}"
        if row["action"] == "use-performer":
            new_tag = f"演员:{row['target_name']}"
            connection.execute(
                "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) "
                "SELECT asset_id,?,confidence,source FROM asset_tag WHERE tag=?",
                (new_tag, old_tag),
            )
            counts["actor_tags_inserted"] += connection.execute(
                "SELECT changes()"
            ).fetchone()[0]
        connection.execute("DELETE FROM asset_tag WHERE tag=?", (old_tag,))
        counts["actor_tags_removed"] += connection.execute("SELECT changes()").fetchone()[0]

        alias_rows = connection.execute(
            "SELECT a.entity_id,a.source,a.confidence,e.normalized_name "
            "FROM entity_alias a JOIN entity e ON e.id=a.entity_id WHERE a.alias=?",
            (bad_name,),
        ).fetchall()
        for alias in alias_rows:
            cleaned = str(row["collapsed_name"] or "")
            if cleaned and normalize_entity_name(cleaned) != str(alias["normalized_name"]):
                connection.execute(
                    "INSERT OR IGNORE INTO entity_alias("
                    "entity_id,alias,normalized_alias,source,confidence) VALUES(?,?,?,?,?)",
                    (int(alias["entity_id"]), cleaned, normalize_entity_name(cleaned),
                     str(alias["source"]), float(alias["confidence"])),
                )
                counts["aliases_inserted"] += connection.execute(
                    "SELECT changes()"
                ).fetchone()[0]
            connection.execute(
                "DELETE FROM entity_alias WHERE entity_id=? AND normalized_alias=? AND source=?",
                (int(alias["entity_id"]), normalize_entity_name(bad_name), str(alias["source"])),
            )
            counts["aliases_removed"] += connection.execute("SELECT changes()").fetchone()[0]
    return dict(counts)


def write_projection_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = ["bad_name", "collapsed_name", "action", "target_id", "target_name",
              "flat_assets", "actor_tags", "aliases", "canonical_entities", "evidence"]
    write_rows(path, fields, rows)


def named_pairs(connection: sqlite3.Connection, pairs: list[str],
                engaged: set[int] | None = None) -> list[dict[str, object]]:
    """命令行点名的一对。用于证据在站外、本地探测不出来的同一人。

    `五十嵐星蘭` 与 `蘭々` 在账本里没有一处相交：外部 id 不同、别名互不相识、
    作品也不重合。判定它们是同一人靠的是 javdb 资料页把五个艺名列在一起，那份证据
    进不了自动判据，只能由人带进来——所以每一对都必须自带 `证据` 一段话，它会原样
    落进复核 CSV，是这次合并唯一说得清来由的东西。

    写法 `保留id:丢弃id:证据`。方向不自动挑：作品数多的一侧不一定是名字对的一侧。

    保留侧允许在一批里重复出现，丢弃侧不允许：一个人有几个旧艺名就有几条要并进来，
    `香椎美铃` 同时收走 `みすず` 和 `加山優衣` 是常态。被丢弃的那条会被删掉，所以它
    一旦出现过，后面无论摆在哪一侧都是引用一条已经不存在的实体——包括「先丢后留」这种
    顺序写反的情形，一批十几对时肉眼看不出来。
    """
    connection.row_factory = sqlite3.Row
    engaged = engaged or set()
    kept: set[int] = set()
    dropped: set[int] = set()
    plan: list[dict[str, object]] = []
    for pair in pairs:
        parts = pair.split(":", 2)
        if len(parts) != 3 or not parts[2].strip():
            raise SystemExit(f"--pair 要写成 保留id:丢弃id:证据，收到 {pair!r}")
        keep_id, drop_id, why = int(parts[0]), int(parts[1]), parts[2].strip()
        if keep_id == drop_id:
            raise SystemExit(f"--pair {pair}：两侧是同一条实体 {keep_id}，不合并")
        if keep_id in dropped:
            raise SystemExit(
                f"--pair {pair}：保留侧 {keep_id} 在前面的 --pair 里已经被丢弃")
        if drop_id in dropped:
            raise SystemExit(f"--pair {pair}：丢弃侧 {drop_id} 在前面的 --pair 里已经被丢弃")
        if drop_id in kept:
            raise SystemExit(
                f"--pair {pair}：丢弃侧 {drop_id} 在前面的 --pair 里是保留侧")
        sides = {}
        for name, entity_id in (("keep", keep_id), ("drop", drop_id)):
            row = connection.execute(
                "SELECT id,kind,canonical_name,normalized_name,"
                " (SELECT count(*) FROM asset_entity ae WHERE ae.entity_id=e.id) AS links,"
                " (SELECT group_concat(DISTINCT ae.source) FROM asset_entity ae"
                "   WHERE ae.entity_id=e.id) AS sources"
                " FROM entity e WHERE e.id=?", (entity_id,)).fetchone()
            if row is None:
                raise SystemExit(f"--pair {pair}：实体 {entity_id} 不存在")
            if entity_id in engaged:
                raise SystemExit(f"--pair {pair}：实体 {entity_id} 已经在自动判据的计划里")
            sides[name] = row
        if sides["keep"]["kind"] != sides["drop"]["kind"]:
            raise SystemExit(f"--pair {pair}：两侧 kind 不同，不合并")
        kept.add(keep_id)
        dropped.add(drop_id)
        plan.append({
            "normalized_name": sides["drop"]["normalized_name"],
            "match_evidence": why,
            "keep_kind": sides["keep"]["kind"],
            "keep_id": keep_id,
            "keep_name": sides["keep"]["canonical_name"],
            "keep_links": sides["keep"]["links"],
            "drop_kind": sides["drop"]["kind"],
            "drop_id": drop_id,
            "drop_name": sides["drop"]["canonical_name"],
            "drop_links": sides["drop"]["links"],
            "keep_sources": sides["keep"]["sources"] or "",
            "drop_sources": sides["drop"]["sources"] or "",
            "evidence": "人工指定",
        })
    return plan


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = ["normalized_name", "match_evidence", "keep_kind", "keep_name", "keep_id", "keep_links",
              "drop_kind", "drop_name", "drop_id", "drop_links",
              "keep_sources", "drop_sources", "evidence"]
    write_rows(path, fields, rows)


#: 本脚本自己关心的口径；基础计数由 `scripting.counts_of` 给。
EXTRA_COUNTS = {
    "creator_entities": "SELECT count(*) FROM entity WHERE kind='creator'",
    "performer_entities": "SELECT count(*) FROM entity WHERE kind='performer'",
    "asset_tag": "SELECT count(*) FROM asset_tag",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合并 creator / performer 的重复身份（跨 kind 同名与同 kind 目录名投影）")
    add_ledger_write_args(parser)
    parser.add_argument("--review-csv", type=Path,
                        default=GENERATED_DIR / "duplicate-identity-merge.csv")
    parser.add_argument("--projection-review-csv", type=Path,
                        default=GENERATED_DIR / "repeated-identity-name-repair.csv")
    parser.add_argument("--pair", action="append", default=[], metavar="保留id:丢弃id:证据",
                        help="人工指定的一对，用于证据在站外、本地探测不出来的同一人")
    parser.add_argument("--pairs-only", action="store_true",
                        help="只处理 --pair 点名的对子，跳过全部自动判据")
    return parser


def run(args: argparse.Namespace) -> int:
    # 自动判据和人工点名各自成立，但一次只想执行一边：自动那几组的保留方常常另有争议，
    # 人工那批却已经定下来了，捆在一起就只能连着争议一起写，或者一起不写。
    if args.pairs_only and not args.pair:
        raise SystemExit("--pairs-only 必须至少给一个 --pair")
    connection = open_for_write(args)
    try:
        rows = [] if args.pairs_only else collect(connection)
        engaged = {int(row["keep_id"]) for row in rows} | {int(row["drop_id"]) for row in rows}
        rows.extend(named_pairs(connection, args.pair, engaged))
        projection_rows = [] if args.pairs_only else collect_repeated_projections(connection)
        write_csv(args.review_csv, rows)
        write_projection_csv(args.projection_review_csv, projection_rows)
        print(f"高置信重复身份 {len(rows)} 组，复核 CSV：{args.review_csv}")
        print("  保留方分布：", dict(Counter(str(row["keep_kind"]) for row in rows)))
        print("  匹配判据：", dict(Counter(str(row["match_evidence"]) for row in rows)))
        print(f"重复 person 名称/投影 {len(projection_rows)} 组，复核 CSV："
              f"{args.projection_review_csv}")
        print("  处理动作：", dict(Counter(str(row["action"]) for row in projection_rows)))
        if not args.apply:
            print("  未写 ledger（加 --apply --backup 才写）")
            return 0

        print(f"  已备份到 {args.backup}")
        before = counts_of(connection, EXTRA_COUNTS)
        with connection:
            moved = apply_rows(connection, rows)
            projections = apply_repeated_projections(connection, projection_rows)
        after = counts_of(connection, EXTRA_COUNTS)
        _integrity, violations = verify_after_write(connection)

        print("  合并结果：", moved)
        print("  重复名称投影：", projections)
        for key in before:
            mark = "" if before[key] == after[key] else "  <-- 变化"
            print(f"    {key}: {before[key]} -> {after[key]}{mark}")
        print(f"  foreign_key_check 违规 {violations} 条")
        return 1 if violations else 0
    finally:
        connection.close()


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
