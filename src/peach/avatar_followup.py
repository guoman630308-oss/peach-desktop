"""刮削登记了新实体之后，把认得准的那张头像补上（ADR-0040 第七条）。

刮削链每跑一轮都会往账本里登记新的女优和厂牌。它们刚建出来时没有头像：人物页那张圆图
退成作品抽帧，厂牌页退成首字母。补图的能力早就有，缺的是「刮完顺手补上」这一步——
在这之前它是一条人想起来才跑的命令行。

判据：

* **女优**先走图库。按她整条名字链在图库里只找出一张、且尺寸过 `acceptable_avatar` 那一档，
  就装，规则与 `scripts/fill_portrait_gaps.py` 同一条。
* 找出好几张时按脸认人（ADR-0056）：同一个名字下 S1、Ideapocket 几家各存一张的多半是同一
  个人，`ななみ` 这种单名命中的二十几张却是二十几个人，名字答不了这件事。所以拿每张候选上的
  脸和她单人作品封面上截到的脸比（`face_match`，SFace），和 `MATCH_REQUIRED` 张封面（封面
  只截得出一张时就是那一张）都过官方阈值的才算她本人；过尺寸门槛的候选照图库先后比，第一张
  认定是她的装上。没有单人封面、封面检不出脸时图库自己作证（ADR-0062）：两个来源目录里她的
  两张不同照片彼此过线、且认得的候选占多数才算。模型取不到一张都不比，照下一条退回。
* 图库给不出认得准的那一张时，从她单人作品的封面上截脸（`avatar_cover_face`）：挑脸像素最宽的
  那张封面，最差是缩略图；其余检得出脸的封面也各截一张留作候选。这一档截的图、以及批处理用整张封面装上的头像，之后遇到更清楚的
  脸会自动换掉；图库装的、人挑的一律不碰。
* 馆里一张单人作品封面都截不出脸时（只有合集的那些人），按她的 avwikidb 编号读站上的单人
  作品，从馆外那几部的 DMM 封面上截脸，两部不同作品的脸互相过线才装（ADR-0074，
  `avatar_offsite_cover_face`）。装上的和馆藏封面截的同一档。
* 图库里认得准、短边至少 300 像素的小图优先于封面截脸（ADR-0102）；只有一张就装它，
  多张仍按同一套互证判据挑。更小的图在图库与封面都落空时兜底（ADR-0066）：只有一张
  就装它，好几张照同一套互证判据挑，认定的几张里挑像素最大的。小图同样可以在上面几档里
  作证；清晰小图可升级至标准图库人像，更小的兜底可替换为封面人脸。
* **个人博主**（过 `work_portrait_predicate` 的发布账号）不查图库，只从自己独占作品的
  画面上截脸（`avatar_cover_face.sheet_faces`，接触印相九格加有封面的那张）：两部不同作品
  截出的脸互相过线才装，判据与馆外单人作品封面同一条（ADR-0074、ADR-0099）。情侣号、
  多人号的两部作品对不上同一张脸就不装。装上的和封面截脸同一档。没过判据的账号不派。
* **厂牌**不走这条：官网和标识由补厂牌后继（`studio_followup`）按厂牌那套判据补。

这条后继是幂等的（ADR-0040 第六条要求）：第一件事就是看盘上有没有那张图，有就当场返回。
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import avatar_cover_face, avatar_picker, face_match
from .avatar_provider import (
    MIN_LONG_SIDE, MIN_SHORT_SIDE, AvatarCandidateCache, InspectedAvatar, acceptable_avatar,
)
from .followups import Attempts, Followup, FollowupType, attempts_root, register

#: 这类后继在任务中心的身份，也是活动页上那一行的名字来源。
TASK_KEY = "entity-avatar"
TASK_LABEL = "补全资料图片"

#: 会被派后继的实体种类。厂牌的图是标识，归 `studio_followup`；系列、标签没有头像位。
#: 发布账号只派过 `work_portrait_predicate` 的那些（`_candidates`）。
KINDS = ("performer", "creator")

#: 一次刮削最多为这么多个新实体派后继。上限本身由 `task_runs.MAX_FOLLOWUPS` 判，
#: 这里先按作品数排好序再交出去：截断真的发生时，留下的该是库里出现得最多的那些。
PLAN_ORDER = "作品多的在前"

#: 封面人脸最多截几张：装上的一张，加上留进挑图弹层的几张。
MAX_KEPT_FACES = 8

#: 比对拿几张封面上的脸作参照：脸最宽的那几张。
MATCH_COVERS = 3
#: 候选要和这么多张参照都过线才算她本人；参照只截得出一张时降到一张。
MATCH_REQUIRED = 2
#: 一个人最多拿这么多张图库候选去比，每张都要取一次图。
MAX_MATCH_CANDIDATES = 24
#: 两张图库候选的余弦到这一档就当是同一张照片（缩放、重压、AI 放大），不算两份证据。
#: 2026-09-24 实测：同一张图缩到一半或三分之一再放大重压，SFace 给 0.94～0.97；
#: 石川祐奈在 Digigra、Moodyz、DAS 三家的三张不同照片两两 0.59～0.66。
NEAR_DUPLICATE = 0.9
#: 小图兜底那一档装上的图，头像边车里记的 `source_kind`。
SMALL_SOURCE_KIND = "gallery_small"
#: 可优先于封面裁片的档案小图，沿用标准图库头像的短边下限。
PROFILE_MIN_SIDE = MIN_SHORT_SIDE
#: 派出之后账号分类被改、不再过 `work_portrait_predicate` 时的结论。它取决于分类而不是作品，
#: 不记进 `Attempts`：分类改回来时照常再派。
NO_IDENTITY = "账号没有本人身份依据"


def followup_key(kind: str, entity_id: int) -> str:
    return f"{TASK_KEY}:{kind}:{int(entity_id)}"


def parse_key(key: str) -> tuple[str, int]:
    """把后继 key 拆回实体身份。形状不对就抛——那说明排队的行不是这个版本写的。"""
    prefix, _, rest = str(key).partition(":")
    kind, _, raw = rest.partition(":")
    if prefix != TASK_KEY or kind not in KINDS or not raw.isdigit():
        raise ValueError(f"认不出这条补头像后继：{key}")
    return kind, int(raw)


def plan(connection: sqlite3.Connection, avatar_root, *,
         since_entity_id: int, covered_asset_ids=()) -> list[Followup]:
    """这一轮新登记、又没有头像的实体，一个一条后继。

    「新登记」按实体 id 的水位判：刮削开始前记一次 `max(id)`，比它大的就是这一轮建出来
    的。这比从落库结果里往回追要稳——实体可能由字段落库、别名归并或外部编号登记中的
    任何一条路建出来，而它们最终都表现为这张表上多了一行。

    `covered_asset_ids` 是这一轮换上了新封面的作品。它们的女优也派一条：没头像的现在
    可能截得出脸了，头像是从封面截的那些可能换得到更清楚的一张。
    """
    wanted = _candidates(connection)
    rows = connection.execute(
        "SELECT e.id,e.kind,e.canonical_name,"
        " (SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae"
        "  WHERE ae.entity_id=e.id) AS assets"
        f" FROM entity e WHERE e.id>? AND {wanted} ORDER BY e.id",
        (int(since_entity_id),)).fetchall()
    covered = [int(asset_id) for asset_id in covered_asset_ids]
    if covered:
        marks = ",".join("?" * len(covered))
        known = {int(row["id"]) for row in rows}
        rows += [row for row in connection.execute(
            "SELECT e.id,e.kind,e.canonical_name,"
            " (SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae"
            "  WHERE ae.entity_id=e.id) AS assets"
            f" FROM entity e WHERE {wanted} AND e.id IN"
            f" (SELECT entity_id FROM asset_entity WHERE asset_id IN ({marks}))"
            " ORDER BY e.id", covered).fetchall() if int(row["id"]) not in known]
    found = []
    for row in rows:
        entity_id, kind = int(row["id"]), str(row["kind"])
        if not needs_avatar(avatar_root, kind, entity_id):
            continue
        found.append((int(row["assets"] or 0), entity_id, kind,
                      str(row["canonical_name"] or "")))
    found.sort(key=lambda item: (-item[0], item[1]))
    return [Followup(key=followup_key(kind, entity_id), task_key=TASK_KEY,
                     label=f"{TASK_LABEL}：{name}" if name else TASK_LABEL)
            for _assets, entity_id, kind, name in found]


def _candidates(connection: sqlite3.Connection) -> str:
    """派后继的实体：女优，加作品画面能当本人头像的发布账号。"""
    from .entity_classification import work_portrait_predicate

    return ("(e.kind='performer' OR (e.kind='creator' AND "
            f"{work_portrait_predicate(connection)}))")


def _portrait_account(connection: sqlite3.Connection, entity_id: int) -> bool:
    """这个发布账号此刻过不过 `work_portrait_predicate`。派出与跑之间分类可能被改过。"""
    from .entity_classification import work_portrait_predicate

    return connection.execute(
        f"SELECT 1 FROM entity e WHERE e.id=? AND {work_portrait_predicate(connection)}",
        (int(entity_id),)).fetchone() is not None


def needs_avatar(avatar_root, kind: str, entity_id: int) -> bool:
    """没有头像，或者装着的是封面截的、小图兜底的那一档（还可能换到更清楚的）。"""
    if (avatar_picker.installed_digest(avatar_root, kind, entity_id)
            and _installed_record(avatar_root, kind, entity_id).get("user_selected")):
        return False
    return (not avatar_picker.installed_digest(avatar_root, kind, entity_id)
            or avatar_cover_face.installed_face_px(avatar_root, kind, entity_id) is not None
            or _installed_small(avatar_root, kind, entity_id))


def _installed_record(avatar_root, kind: str, entity_id: int) -> dict:
    """已安装头像的选择来源。"""
    from .previews import entity_image_key

    path = Path(avatar_root) / f"{entity_image_key(kind, int(entity_id))}.img.provenance.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _installed_small(avatar_root, kind: str, entity_id: int) -> bool:
    """自动小图可继续寻找高清人像；人工选择保留。"""
    from .previews import entity_image_key

    record = _installed_record(avatar_root, kind, entity_id)
    if record.get("user_selected"):
        return False
    if record.get("source_kind") == SMALL_SOURCE_KIND:
        return True
    if record.get("source_kind") != "official_profile":
        return False
    try:
        inspected = avatar_picker.accept_image(
            (Path(avatar_root) / f"{entity_image_key(kind, int(entity_id))}.img").read_bytes())
    except (OSError, avatar_picker.PickerError):
        return False
    return not acceptable_avatar(inspected, MIN_LONG_SIDE, MIN_SHORT_SIDE)


#: 指纹里的量：作品数、别名数与 avwikidb 编号数。SQL 与 `stock` 那条共用。发布账号的
#: 作品只数铺过接触印相的：印相是它唯一的画面来源，后补的印相要让它再派一次。
_WORK_COUNT = ("(SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae"
               " JOIN asset a ON a.id=ae.asset_id WHERE ae.entity_id=e.id"
               " AND (e.kind<>'creator' OR coalesce(a.snapshot_path,'')<>''))")
_ALIAS_COUNT = "(SELECT count(*) FROM entity_alias al WHERE al.entity_id=e.id)"
_OFFSITE_COUNT = ("(SELECT count(*) FROM entity_external_ref x WHERE x.entity_id=e.id"
                  " AND x.provider='avwikidb' AND x.external_kind='performer')")

#: 认人判据的版本，进指纹。改了判据就加一：存量里缺图的女优按新判据各重比一次。
#: 2 是 ADR-0057 的图库互证，3 是 ADR-0062 没有封面参照时的图库自证，4 是 ADR-0066 的
#: 小图作证与兜底，5 是 ADR-0070 的小脸裁切放大再比，6 是 ADR-0074 的馆外单人作品封面，
#: 7 是 ADR-0102 的清晰档案小图优先，8 是封面未匹配时的图库独立互证，9 包含自动档案缩略图。
MATCH_RULE = 9


def _fingerprint(works, aliases, offsite) -> str:
    return f"{int(works or 0)}:{int(aliases or 0)}:{int(offsite or 0)}:r{MATCH_RULE}"


def fingerprint(connection: sqlite3.Connection, entity_id: int) -> str:
    """会让这条后继结论变的量，写成 `作品数:别名数:avwikidb 编号数:r版本`。

    多一部作品就多一张封面可截、可比；多一个别名就多一个名字去图库里找（`神山ももか`
    补上别名后才命中 `美雲そら` 与 `朝霧いのり` 两张）；补女优资料后继绑上 avwikidb 编号，
    馆外单人作品的封面才有路可走；判据换了，同一份证据也可能认得出。
    """
    row = connection.execute(
        f"SELECT {_WORK_COUNT}, {_ALIAS_COUNT}, {_OFFSITE_COUNT} FROM entity e WHERE e.id=?",
        (int(entity_id),)).fetchone()
    return _fingerprint(*(row if row else (0, 0, 0)))


def stock(connection: sqlite3.Connection, avatar_root, attempts, *, limit: int,
          skip=()) -> list[Followup]:
    """库里早就登记、至今缺头像的女优与个人博主，作品多的在前，最多 `limit` 条（ADR-0053）。

    跑过一次、指纹也没变的不再派（`attempts`）：作品和别名都没变，封面和图库命中也不会变。
    """
    if limit <= 0:
        return []
    skip = set(skip)
    found = []
    for row in connection.execute(
            f"SELECT e.id,e.kind,e.canonical_name,{_WORK_COUNT} AS assets,"
            f" {_ALIAS_COUNT} AS aliases, {_OFFSITE_COUNT} AS offsite"
            f" FROM entity e WHERE {_candidates(connection)}"
            " AND EXISTS(SELECT 1 FROM asset_entity ae WHERE ae.entity_id=e.id)"
            " ORDER BY assets DESC, e.id"):
        entity_id, kind = int(row["id"]), str(row["kind"])
        key = followup_key(kind, entity_id)
        current = _fingerprint(row["assets"], row["aliases"], row["offsite"])
        if (key in skip or not needs_avatar(avatar_root, kind, entity_id)
                or attempts.settled(key, current)):
            continue
        name = str(row["canonical_name"] or "")
        found.append(Followup(key=key, task_key=TASK_KEY,
                              label=f"{TASK_LABEL}：{name}" if name else TASK_LABEL))
        if len(found) >= limit:
            break
    return found


def run(contract, key: str, handle) -> dict:
    """跑一条补头像后继，再把实体当时的指纹记进 `Attempts`，存量补派按它判。

    该比对却取不到比对模型、或 avwikidb 正在冷却的那一次不记：结论取决于那天有没有网，
    不取决于她的作品和名字，记下来就要等到她多一部作品才会再试。账号此刻没有本人身份
    依据的那一次（`NO_IDENTITY`）同样不记。
    """
    summary = _run(contract, key, handle)
    if (summary.get("face_match_unavailable") or summary.get("source_unavailable")
            or summary.get("outcome") == NO_IDENTITY):
        return summary
    _kind, entity_id = parse_key(key)
    with contract.database.read_connection() as connection:
        current = fingerprint(connection, entity_id)
    Attempts(attempts_root(contract.candidate_root)).record(
        key, current, str(summary.get("outcome", "")))
    return summary


def _run(contract, key: str, handle) -> dict:
    """跑一条补头像后继。返回的摘要就是活动页上那一行。

    实体身份不进摘要——它已经在这条后继的 key 里，写两遍只是让那一行更难读。
    """
    kind, entity_id = parse_key(key)
    avatar_root = contract.avatar_root
    providers_root = contract.candidate_root / "provider-cache" / "performer-avatars"
    # 装着的是封面截的那一档时还要往下走：图库可能有了人像，封面可能换了更清楚的。
    cropped_px = avatar_cover_face.installed_face_px(avatar_root, kind, entity_id)
    small = _installed_small(avatar_root, kind, entity_id)
    if (avatar_picker.installed_digest(avatar_root, kind, entity_id)
            and (_installed_record(avatar_root, kind, entity_id).get("user_selected")
                 or (cropped_px is None and not small))):
        # 重跑、或者这中间人自己换过图。两种都不该再装一次。
        return {"outcome": "已有头像"}
    with contract.database.read_connection() as connection:
        row = connection.execute(
            "SELECT canonical_name FROM entity WHERE id=? AND kind=?",
            (entity_id, kind)).fetchone()
        if row is None:
            # 实体被合并或删掉了。这不是失败：那件事已经不存在了。
            return {"outcome": "档案已不存在"}
        name = str(row[0] or "")
        if kind == "creator":
            handle.progress(label=f"{TASK_LABEL}：{name}", throttle=0)
            return _install_account_face(contract, connection, providers_root, avatar_root,
                                         entity_id, name, cropped_px)
        # 图库索引只读本地缓存，这一步不联网；联网只发生在要量、要比、要装那几张的时候。
        listing = avatar_picker.choices(connection, providers_root, avatar_root,
                                        kind, entity_id, deduplicate=False)
        found = [choice for choice in listing["choices"]
                 if choice["source"] == "gfriends"]
        handle.progress(label=f"{TASK_LABEL}：{name}", throttle=0)
        if len(found) == 1:
            installed = _install(contract, connection, providers_root, avatar_root,
                                 kind, entity_id, name, found[0])
            if installed["outcome"] == "已装上":
                return installed
        from .avatar_face import FaceProbe

        probe = FaceProbe()
        covers = avatar_cover_face.faces(connection, contract.cover_root, entity_id, probe)
        preferred, gallery, extra = _preferred_gallery(
            contract, connection, providers_root, avatar_root, kind, entity_id, name, found, covers)
        if preferred is not None:
            return preferred
        if _installed_record(avatar_root, kind, entity_id).get("source_kind") == "official_profile":
            return {"name": name, "outcome": gallery, **extra}
        fallen = {**_install_cover_face(contract, providers_root, avatar_root, kind,
                                        entity_id, name, gallery, len(found), cropped_px,
                                        covers, probe), **extra}
        if fallen["outcome"] != "已装上" and not covers and not extra:
            fallen = _install_offsite(contract, connection, providers_root, avatar_root, kind,
                                      entity_id, cropped_px, probe, fallen)
        # 封面截的那张装着时不退回小图：那一档自己会判要不要换。
        if (fallen["outcome"] == "已装上" or not found or extra or cropped_px is not None
                or (avatar_picker.installed_digest(avatar_root, kind, entity_id) and not small)):
            return fallen
        return _install_small(contract, connection, providers_root, avatar_root, kind,
                              entity_id, found, covers, fallen)


def _preferred_gallery(contract, connection, providers_root, avatar_root, kind: str,
                       entity_id: int, name: str, found: list[dict], covers: list
                       ) -> tuple[dict | None, str, dict]:
    """多来源身份核验与清晰小图选择；返回安装结果或封面兜底所需的结论。"""
    extra: dict = {}
    if len(found) > 1:
        matched = _install_matched(contract, connection, providers_root, avatar_root,
                                   kind, entity_id, name, found, covers)
        if matched["outcome"] == "已装上":
            return matched, "", extra
        gallery = matched["outcome"]
        extra = {key: value for key, value in matched.items()
                 if key == "face_match_unavailable"}
    else:
        gallery = "图库里没有这个名字" if not found else "图库那张太小"
    if found and not extra:
        profile = _install_small(contract, connection, providers_root, avatar_root, kind,
                                 entity_id, found, covers, {"name": name, "outcome": gallery},
                                 min_side=PROFILE_MIN_SIDE)
        if (profile["outcome"] in ("已装上", "已装着认得准的小图")
                or profile.get("face_match_unavailable")):
            return profile, gallery, extra
    return None, gallery, extra


@dataclass(frozen=True)
class Candidate:
    """一张取来、量过的图库候选。"""

    choice: dict
    body: bytes
    origin: dict
    inspected: InspectedAvatar


@dataclass
class GalleryMatch:
    """图库多张候选按脸比对的结论。`winner` 是 None 时 `reason` 说为什么没选出来。"""

    winner: Candidate | None = None
    reason: str = ""
    #: 该比对却取不到比对模型。这一次的结论取决于有没有网，不作数。
    unavailable: bool = False
    #: 记进头像边车的比对证据（`face_match` 字段）。
    evidence: dict = field(default_factory=dict)
    #: 每张比过的候选各自的分数，`ref → [与每张参照的余弦]`。只读估计与测试看它。
    scores: dict = field(default_factory=dict)


def match_gallery(found: list[dict], covers: list, matcher,
                  fetch: Callable[[dict], Candidate | None], *,
                  allow_small: bool = False, min_small_side: int = 0) -> GalleryMatch:
    """图库里这几张候选，哪一张是她本人（ADR-0056）。只读：取图交给 `fetch`，不写任何东西。

    参照是她单人作品封面上截到的脸，按脸宽取前 `MATCH_COVERS` 张；候选要和其中
    `MATCH_REQUIRED` 张（参照只有一张时就是那一张）的余弦都过 `face_match.COSINE_THRESHOLD`
    才算她。只要一张参照的话，一张认错人的封面就能让她装上别人的脸；要两张，封面和
    候选得在两部不同的作品上都对得上。

    挑哪一张：照图库的先后比，第一张过线、又过了尺寸门槛的就是她，后面的不再取。没过尺寸
    门槛的小图照样比，只当证人不当胜者（ADR-0066）：`桃咲ゆり菜` 名下 832×1249 那张和
    399×393 那张余弦 0.49，小图不作证，大图就只剩自己一张、认不准。`allow_small` 是小图
    兜底那一档：小图也能当胜者，认定的几张里挑像素最大的。不按像素挑：图库靠后的大库（`y-Minnano`、`z-DMM(骑)`）原图只有两三百
    像素、靠 AI 放大到五百以上，按像素排它们反而抢到前面；片商与事务所的原生图排在前头
    （`gfriends` 模块头）。

    没有一张过够参照时再看互证（ADR-0057）：一张候选对上某张参照，名下另一张不是同一
    张照片的候选也对上同一张参照、两张彼此也过线，三方互相认得，就算她。石川祐奈的两张
    参照里有一张截的不是她（和谁都只有 0.0x），另一张和三家图库各 0.55～0.61。

    没有参照或封面未匹配时，图库自己作证
    （ADR-0062，`_gallery_agrees`）：两个不同来源目录里她名下的两张不同照片彼此过线，
    而且和这一张认得的候选在全部候选里占多数，就算她。
    """
    references = []
    for face in covers:
        if len(references) >= MATCH_COVERS:
            break
        cut = avatar_cover_face.cut(face)
        vector = matcher.embedding(cut[0]) if cut is not None else None
        if matcher.unavailable:
            return GalleryMatch(reason=f"图库 {len(found)} 张，比对模型不可用："
                                       f"{matcher.unavailable}", unavailable=True)
        if vector is not None:
            references.append((face, vector))
    required = min(MATCH_REQUIRED, len(references))
    threshold = face_match.COSINE_THRESHOLD
    scores: dict = {}
    compared: list[tuple[Candidate, object, list[float]]] = []

    def evidence(row, **extra):
        return {
            "model": face_match.MODEL_NAME, "threshold": threshold, "required": required,
            "covers": [{"code": face.code, "asset_id": face.asset_id, "score": score}
                       for (face, _vector), score in zip(references, row)],
            "candidates": len(found), "compared": len(scores), **extra,
        }

    def eligible(candidate: Candidate) -> bool:
        return (acceptable_avatar(candidate.inspected, min_small_side, min_small_side)
                if allow_small else acceptable_avatar(candidate.inspected, MIN_LONG_SIDE,
                                                      MIN_SHORT_SIDE))

    for choice in found[:MAX_MATCH_CANDIDATES]:
        candidate = fetch(choice)
        if candidate is None:
            continue
        vector = matcher.embedding(candidate.body)
        if matcher.unavailable:
            return GalleryMatch(reason=f"图库 {len(found)} 张，比对模型不可用："
                                       f"{matcher.unavailable}", unavailable=True)
        if vector is None:
            continue
        row = [round(face_match.cosine(vector, reference), 3)
               for _face, reference in references]
        scores[choice["ref"]] = row
        if (references and not allow_small and eligible(candidate)
                and sum(score >= threshold for score in row) >= required):
            return GalleryMatch(winner=candidate, evidence=evidence(row), scores=scores)
        compared.append((candidate, vector, row))
    # 兜底那一档按像素从大到小挑胜者；证人还是全部候选。
    order = sorted(compared, key=lambda item: -_area(item[0])) if allow_small else compared
    if not references:
        return _gallery_agrees(found, compared, scores, evidence, order, eligible)
    for candidate, _vector, row in order:
        if eligible(candidate) and sum(score >= threshold for score in row) >= required:
            return GalleryMatch(winner=candidate, evidence=evidence(row), scores=scores)
    corroborated = _covers_corroborate(found, compared, scores, evidence, order, eligible)
    if corroborated.winner is not None:
        return corroborated

    def gallery_evidence(_row, **extra):
        return evidence([], required=0, unmatched_covers=[
            {"code": face.code, "asset_id": face.asset_id,
             "scores": {key: values[index] for key, values in scores.items()}}
            for index, (face, _vector) in enumerate(references)
        ], **extra)

    return _gallery_agrees(found, compared, scores, gallery_evidence, order, eligible)


def _covers_corroborate(found: list[dict], compared: list, scores: dict, evidence,
                        order: list, eligible: Callable[[Candidate], bool]) -> GalleryMatch:
    """没有一张候选过够参照时，两张候选对上同一张参照、彼此也过线就算她（ADR-0057）。"""
    threshold = face_match.COSINE_THRESHOLD
    for candidate, vector, row in order:
        if not eligible(candidate):
            continue
        for other, other_vector, other_row in compared:
            pair = face_match.cosine(vector, other_vector)
            if other is candidate or not threshold <= pair < NEAR_DUPLICATE:
                continue
            if any(mine >= threshold and theirs >= threshold
                   for mine, theirs in zip(row, other_row)):
                return GalleryMatch(winner=candidate, scores=scores, evidence=evidence(
                    row, corroborated_by={"ref": other.choice["ref"], "score": round(pair, 3)}))
    return GalleryMatch(reason=f"图库 {len(found)} 张里比不出她", scores=scores)


def _directory(choice: dict) -> str:
    """这张候选出自图库哪个来源目录（`gfriends:1-S1/她.jpg` → `1-S1`）。"""
    ref = str(choice.get("ref", ""))
    return ref.partition(":")[2].partition("/")[0] or str(choice.get("label", ""))


def _area(candidate: Candidate) -> int:
    return candidate.inspected.width * candidate.inspected.height


def _gallery_agrees(found: list[dict], compared: list, scores: dict, evidence,
                    order: list, eligible: Callable[[Candidate], bool]) -> GalleryMatch:
    """没有匹配的封面参照时，图库候选之间独立互证（ADR-0062、0104）。

    `叶芽ゆきな` 四部作品都是多人封面，截不出参照；图库里 Javrave 与 DMM 各存她一张，
    两张余弦 0.56。同名下几家片商各存一张的多半是同一个人，`ななみ` 这种单名命中的
    二十几张却是二十几个人——所以要两件事同时成立：

    * **两个目录、两张照片。** 作证的那张出自另一个来源目录，余弦过线而低于
      `NEAR_DUPLICATE`（同一张照片在几个目录各存一份只是一份证据，ADR-0057 第二条）。
    * **占多数。** 和这一张过线的候选（含它自己，近重复也算）在全部提得出脸的候选里
      过半。二十几个人的名下偶然有某一位的两张，两张对二十几张过不了这一条；
      `星野千紗` 名下 GRAPHIS 那张是另一个人（和谁都不到 0.26），Warashi 与 Javrave 两张
      认得，二比三，装 Warashi 那张。

    按 `order` 的先后，第一张 `eligible` 且满足的装上；证人与多数数的是全部 `compared`。
    证据里 `covers` 为空、`required` 为 0，`corroborated_by` 记作证那张与分数，
    `agreeing`/`faces` 记多数是怎么数出来的。
    """
    threshold = face_match.COSINE_THRESHOLD
    faces = len(compared)
    for candidate, vector, row in order:
        if not eligible(candidate):
            continue
        peers = [(other, face_match.cosine(vector, other_vector))
                 for other, other_vector, _row in compared if other is not candidate]
        agreeing = [(other, pair) for other, pair in peers if pair >= threshold]
        witnesses = [(other, pair) for other, pair in agreeing
                     if pair < NEAR_DUPLICATE and _directory(other.choice) != _directory(candidate.choice)]
        if not witnesses or (len(agreeing) + 1) * 2 <= faces:
            continue
        other, pair = witnesses[0]
        return GalleryMatch(winner=candidate, scores=scores, evidence=evidence(
            row, corroborated_by={"ref": other.choice["ref"], "score": round(pair, 3)},
            agreeing=len(agreeing) + 1, faces=faces))
    if faces < 2:
        return GalleryMatch(reason=f"图库 {len(found)} 张认不准", scores=scores)
    return GalleryMatch(reason=f"图库 {len(found)} 张彼此认不出同一个人", scores=scores)


def gallery_fetcher(connection, providers_root, entity_id: int, transport,
                    store: bool = True) -> Callable[[dict], Candidate | None]:
    """按候选的 `ref` 取图并量一次；取不到、不是图的当没有这一张。

    `store` 时把取来的图按地址留进图库缓存（只存对象，不写这个人的证据）：下一轮不必
    再下一次，挑图弹层也就量得出每一格的尺寸。落选的多半是别人，不该记成她取过的图。
    """
    cache = AvatarCandidateCache(providers_root / avatar_picker.GFRIENDS_CACHE)

    def fetch(choice: dict) -> Candidate | None:
        try:
            body, origin = avatar_picker.resolve(choice["ref"], connection,
                                                 providers_root, entity_id, transport)
            inspected = avatar_picker.accept_image(body)
        except avatar_picker.PickerError:
            return None
        if store and origin.get("upstream_url"):
            cache.store(str(origin["upstream_url"]), body, inspected)
        return Candidate(choice, body, origin, inspected)

    return fetch


def _install_matched(contract, connection, providers_root, avatar_root, kind: str,
                     entity_id: int, name: str, found: list[dict], covers: list) -> dict:
    """图库里找出好几张时，按脸比出是她的那一张装上。"""
    from .http import HttpxTransport

    summary = {"name": name, "matched": len(found)}
    transport = HttpxTransport()
    try:
        result = match_gallery(found, covers, face_match.FaceMatcher(),
                               gallery_fetcher(connection, providers_root, entity_id,
                                               transport))
    finally:
        close = getattr(transport, "close", None)
        if close:
            close()
    if result.winner is None:
        flag = {"face_match_unavailable": True} if result.unavailable else {}
        return {**summary, "outcome": result.reason, **flag}
    winner = result.winner
    origin = {**winner.origin, "source_kind": "gallery_face_matched",
              "name_source": "face-match", "face_match": result.evidence}
    avatar_picker.install(providers_root, avatar_root, kind, entity_id, winner.body, origin)
    contract.cache_bust()
    how = "按封面人脸认定" if result.evidence.get("covers") else "按图库互证认定"
    return {**summary, "outcome": "已装上",
            "size": f"{winner.inspected.width}×{winner.inspected.height}",
            "source": f"{winner.choice['label']}（{how}）"}


def _install(contract, connection, providers_root, avatar_root, kind: str,
             entity_id: int, name: str, choice: dict) -> dict:
    """图库里只找出这一张，取来量一次再装。"""
    from .http import HttpxTransport

    transport = HttpxTransport()
    try:
        body, origin = avatar_picker.resolve(choice["ref"], connection,
                                             providers_root, entity_id, transport)
        inspected = avatar_picker.accept_image(body)
    finally:
        close = getattr(transport, "close", None)
        if close:
            close()
    size = f"{inspected.width}×{inspected.height}"
    if not acceptable_avatar(inspected, MIN_LONG_SIDE, MIN_SHORT_SIDE):
        return {"name": name, "outcome": "图太小", "matched": 1, "size": size}
    avatar_picker.install(providers_root, avatar_root, kind, entity_id, body, origin)
    contract.cache_bust()
    return {"name": name, "outcome": "已装上", "matched": 1, "size": size,
            "source": choice["label"]}


def _install_small(contract, connection, providers_root, avatar_root, kind: str,
                   entity_id: int, found: list[dict], covers: list, fallen: dict, *,
                   min_side: int = 0) -> dict:
    """装认得准且满足本档尺寸下限的小图（ADR-0066、0102）。

    `柊木なな` 两部作品都是多人封面，图库里只有 199×299 与 470×470 两张，余弦 0.42：
    两张认得同一个人，装 470×470 那张，比首字母占位强。`fallen` 是上一档的摘要，这一档
    也落空时原样交回。
    """
    from .http import HttpxTransport

    transport = HttpxTransport()
    try:
        fetch = gallery_fetcher(connection, providers_root, entity_id, transport)
        if len(found) == 1:
            winner, evidence = fetch(found[0]), {}
        else:
            result = match_gallery(found, covers, face_match.FaceMatcher(), fetch,
                                   allow_small=True, min_small_side=min_side)
            if result.unavailable:
                return {**fallen, "face_match_unavailable": True}
            winner, evidence = result.winner, result.evidence
    finally:
        close = getattr(transport, "close", None)
        if close:
            close()
    if winner is None or not acceptable_avatar(winner.inspected, min_side, min_side):
        return fallen
    size = f"{winner.inspected.width}×{winner.inspected.height}"
    if winner.inspected.sha256 == avatar_picker.installed_digest(avatar_root, kind, entity_id):
        return {**fallen, "outcome": "已装着认得准的小图", "size": size}
    origin = {**winner.origin, "source_kind": SMALL_SOURCE_KIND,
              **({"name_source": "face-match", "face_match": evidence} if evidence else {})}
    avatar_picker.install(providers_root, avatar_root, kind, entity_id, winner.body, origin)
    contract.cache_bust()
    return {"name": fallen.get("name", ""), "matched": len(found), "outcome": "已装上",
            "size": size, "source": f"{winner.choice['label']}（小图兜底）"}


def _install_cover_face(contract, providers_root, avatar_root, kind: str,
                        entity_id: int, name: str, gallery: str, matched: int,
                        cropped_px: int | None, found: list, probe) -> dict:
    """图库给不出那一张时，从她单人作品的封面上截一张脸装上。

    `found` 是 `avatar_cover_face.faces` 的结果，按脸宽排好；图库比对用的也是这一份。
    `cropped_px` 是装着的那张封面截图当时的脸宽（没装、或不是这一档装的是 None）：
    新挑出来的脸不比它宽就不换，免得每跑一次都把同一张图重写一遍。装着的那张上检不出
    脸时按 0 算：那是从打了模糊的封面上截的，脸再宽也认不出是谁。
    """
    summary = {"name": name, "matched": matched}
    if not found:
        reason = f"探针不可用：{probe.unavailable}" if probe.unavailable else "封面上没有能截的脸"
        return {**summary, "outcome": f"{gallery}，{reason}"}
    face = found[0]
    # 装的只有脸最宽那一张；其余几张也截好留进候选缓存，挑图弹层里一点就能换，
    # 不必再去整张封面上手框。
    for other in found[1:MAX_KEPT_FACES]:
        if (extra := avatar_cover_face.cut(other)) is not None:
            avatar_picker.keep(providers_root, entity_id, *extra)
    if cropped_px and not avatar_cover_face.installed_face_readable(avatar_root, kind, entity_id, probe):
        cropped_px = 0
    if cropped_px is not None and face.face_px <= cropped_px:
        return {**summary, "outcome": "已是最清楚的封面人脸", "source": face.code}
    cut = avatar_cover_face.cut(face)
    if cut is None:
        return {**summary, "outcome": "封面截不出这一块", "source": face.code}
    body, origin = cut
    inspected = avatar_picker.install(providers_root, avatar_root, kind, entity_id,
                                      body, origin)
    contract.cache_bust()
    return {**summary, "outcome": "已装上",
            "size": f"{inspected['width']}×{inspected['height']}",
            "source": f"作品封面 {face.code}"}


def _install_offsite(contract, connection, providers_root, avatar_root, kind: str,
                     entity_id: int, cropped_px: int | None, probe, fallen: dict) -> dict:
    """馆藏封面上截不出她的脸时，从 avwikidb 列出的馆外单人作品封面上截（ADR-0074）。

    `fallen` 是馆藏封面那一档的摘要，这一档的结论接在它后面。两部互证不成时，截到的脸
    照样留进候选缓存，挑图弹层里能点。
    """
    from . import avatar_offsite_cover_face as offsite
    from .catalog_rules import normalise_code_key
    from .http import HttpxTransport
    from .performer_profile_followup import avwikidb_pages

    ref = connection.execute(
        "SELECT external_id FROM entity_external_ref WHERE entity_id=? AND provider='avwikidb'"
        " AND external_kind='performer' ORDER BY external_id LIMIT 1",
        (int(entity_id),)).fetchone()
    if ref is None:
        # 多数女优没有编号；每一行都接一句「没有编号」只是噪声，上一档的结论已经说清了。
        return fallen
    skip = {normalise_code_key(str(code)) for (code,) in connection.execute(
        "SELECT DISTINCT a.code FROM asset a JOIN asset_entity ae ON ae.asset_id=a.id"
        " WHERE ae.entity_id=? AND coalesce(a.code,'')<>''", (int(entity_id),))}
    matcher = face_match.FaceMatcher()
    pages, transport = avwikidb_pages(contract), HttpxTransport()
    try:
        outcome = offsite.find(pages, str(ref[0]), skip, probe, matcher, transport,
                               providers_root)
    finally:
        pages.close()
        close = getattr(transport, "close", None)
        if close:
            close()
    kept = offsite.keep_all(providers_root, entity_id, outcome)
    summary = {**fallen, "outcome": f"{fallen['outcome']}，{outcome.reason}".rstrip("，"),
               **({"kept": kept} if kept else {})}
    if outcome.unavailable:
        flag = "face_match_unavailable" if matcher.unavailable else "source_unavailable"
        return {**summary, flag: True}
    winner = outcome.winner
    if winner is None:
        return summary
    if cropped_px and not avatar_cover_face.installed_face_readable(avatar_root, kind, entity_id,
                                                                    probe):
        cropped_px = 0
    if cropped_px is not None and winner.face_px <= cropped_px:
        return {**summary, "outcome": "已是最清楚的封面人脸", "source": winner.code}
    made = offsite.origin(winner, outcome.urls.get(winner.code, ""), outcome.evidence)
    if made is None:
        return {**summary, "outcome": "封面截不出这一块", "source": winner.code}
    inspected = avatar_picker.install(providers_root, avatar_root, kind, entity_id, *made)
    contract.cache_bust()
    return {"name": fallen.get("name", ""), "matched": fallen.get("matched", 0),
            "outcome": "已装上", "size": f"{inspected['width']}×{inspected['height']}",
            "source": f"馆外单人作品封面 {winner.code}"
                      f"（{len(outcome.evidence['codes'])} 部互证）"}


def _install_account_face(contract, connection, providers_root, avatar_root, entity_id: int,
                          name: str, cropped_px: int | None) -> dict:
    """个人博主：两部独占作品的画面截出同一张脸才装（ADR-0074 的互证，ADR-0099）。

    一部作品只出一张脸（`sheet_faces`），互证数的是作品。没对上的脸照样截好留进候选缓存，
    挑图弹层里能点。装上的来源记 `cover-face`、`identity_verified: false`，和封面截脸同一档。
    """
    from . import avatar_offsite_cover_face as offsite
    from .avatar_face import FaceProbe

    summary = {"name": name, "matched": 0}
    if not _portrait_account(connection, entity_id):
        return {**summary, "outcome": NO_IDENTITY}
    probe = FaceProbe()
    faces = avatar_cover_face.sheet_faces(connection, contract.cover_root, entity_id, probe,
                                          contract.snapshot_file)
    if probe.unavailable:
        return {**summary, "outcome": f"探针不可用：{probe.unavailable}",
                "face_match_unavailable": True}
    if not faces:
        return {**summary, "outcome": "作品画面上没有能截的脸"}
    matcher = face_match.FaceMatcher()
    agreed, scores = offsite.agreeing(faces, matcher)
    if matcher.unavailable:
        return {**summary, "outcome": f"比对模型不可用：{matcher.unavailable}",
                "face_match_unavailable": True}
    winner = agreed[0] if len({face.code for face in agreed}) >= offsite.AGREE_WORKS else None
    kept = 0
    for face in faces[:MAX_KEPT_FACES]:
        if face is not winner and (extra := avatar_cover_face.cut(face)) is not None:
            avatar_picker.keep(providers_root, entity_id, *extra)
            kept += 1
    summary = {**summary, **({"kept": kept} if kept else {})}
    if winner is None:
        return {**summary, "outcome": f"作品画面截到 {len(faces)} 张脸，凑不齐两部互证"}
    if cropped_px and not avatar_cover_face.installed_face_readable(avatar_root, "creator",
                                                                    entity_id, probe):
        cropped_px = 0
    if cropped_px is not None and winner.face_px <= cropped_px:
        return {**summary, "outcome": "已是最清楚的作品画面人脸", "source": winner.code}
    cut = avatar_cover_face.cut(winner)
    if cut is None:
        return {**summary, "outcome": "画面截不出这一块", "source": winner.code}
    body, origin = cut
    evidence = {"codes": [face.code for face in agreed], "scores": scores}
    inspected = avatar_picker.install(providers_root, avatar_root, "creator", entity_id, body,
                                      {**origin, "face_match": evidence})
    contract.cache_bust()
    return {**summary, "outcome": "已装上",
            "size": f"{inspected['width']}×{inspected['height']}",
            "source": f"作品画面 {winner.code}（{len(evidence['codes'])} 部互证）"}


#: 不写账本：这条后继只往 `avatar_root` 与候选缓存里写文件。它照样一次只跑一条——
#: 通道按 task_key 分，同一种后继本来就共用一条。
TYPE = register(FollowupType(task_key=TASK_KEY, label=TASK_LABEL,
                             writes_ledger=False, run=run))
