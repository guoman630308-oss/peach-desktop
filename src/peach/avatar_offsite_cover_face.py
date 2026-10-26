"""馆里没有她的单人作品时，从馆外单人作品的封面上截脸（ADR-0074）。

图库不认得、馆里又只有合集的女优，封面截脸那一档（`avatar_cover_face`）一张都截不出来：
合集封面上最大的那张脸多半是领衔的另一位。`杉野綾子` 的两部馆藏都是 15 人合集，封面主图
比出来是 `葵つかさ`（余弦 0.529，过线 0.363），同一张图上她自己一张脸都没有。

她在 avwikidb 上有编号时（补女优资料后继绑的，ADR-0067），站上的女优页与单人作品筛选页列着
她只身出演的那几部，封面在 DMM 上。一张封面上的那张脸仍只是「多半是她」，所以要**两部不同作品**
的封面脸彼此过线才装：站上认错一部，两张脸就对不上。近重复（余弦到 `NEAR_DUPLICATE`）的
两张算一份证据，那是同一张照片的重制版，不是第二部作品。

装的是脸最宽的那一张，来源记 `cover-face`、`identity_verified: false`：它和馆藏封面截的
那一档同级，之后有了图库认定的人像、或更宽的脸，照样换掉。对不上的那几张截好留进候选
缓存，挑图弹层里一点就能换。封面原图留在候选缓存（`offsite-cover`），不进馆藏封面目录：
那个目录是馆藏的封面集，盘点、书脊框与取景边车都按它数。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import avatar_cover_face, avatar_picker, avwikidb, face_match
from .avatar_followup import MAX_KEPT_FACES, NEAR_DUPLICATE
from .avatar_provider import AvatarCandidateCache, inspect_avatar
from .catalog_rules import normalise_code_key

#: 馆外封面原图的缓存目录（`provider-cache/performer-avatars/` 下）。
COVER_CACHE = "offsite-cover"
#: 一条后继最多取几部作品的封面。
MAX_COVERS = 8
#: 两张封面脸要有这么多部作品互相作证。
AGREE_WORKS = 2
REQUEST_TIMEOUT = 30.0
MAX_IMAGE_BYTES = 8 << 20
#: DMM 图片主机按来源页放行。
REFERER = "https://www.dmm.co.jp/"

UNFETCHED = "未取得"


@dataclass
class Outcome:
    """这一档的结论。`winner` 是 None 时 `reason` 说为什么没装。"""

    reason: str
    winner: avatar_cover_face.CoverFace | None = None
    faces: list = field(default_factory=list)
    urls: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    unavailable: bool = False


def cover_urls(work: dict) -> list[str]:
    """一部作品在 DMM／MGS 上的封面地址，先试的在前。素人（`videoc`）的封套叫 `jp`。"""
    cid, floor = work.get("content_id") or "", work.get("floor") or ""
    if floor == "mgs":
        return [work["mgs_image"]] if work.get("mgs_image") else []
    if not cid:
        return []
    if floor == "videoc":
        return [f"https://awsimgsrc.dmm.co.jp/pics_dig/digital/amateur/{cid}/{cid}jp.jpg"]
    return [f"https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/{cid}/{cid}pl.jpg",
            f"https://awsimgsrc.dmm.com/dig/digital/video/{cid}/{cid}pl.jpg"]


def fetch_cover(transport, cache: AvatarCandidateCache, url: str) -> bytes | None:
    """一张封面的字节，先看缓存；取不到、是「准备中」占位图、不是图都是 None。"""
    from .http import HttpRequest
    from .jav_cover_fetch import PLACEHOLDER

    cached = cache.lookup(url)
    if cached is not None:
        return cached
    try:
        response = transport(HttpRequest("GET", url, {"Referer": REFERER}),
                             REQUEST_TIMEOUT, MAX_IMAGE_BYTES)
    except Exception:  # noqa: BLE001 - 网络层失败都是「这一张没取到」
        return None
    if response.status != 200 or PLACEHOLDER.search(response.url or url):
        return None
    inspected = inspect_avatar(response.body)
    if inspected is None:
        return None
    cache.store(url, response.body, inspected)
    return response.body


def listed_faces(works: list[dict], skip: set[str], probe, fetch) -> tuple[list, dict]:
    """馆外单人作品封面上检得出、截得出的脸，脸最宽的在前；连同每部用的封面地址。

    `skip` 是馆里已有的番号：那几张封面归馆藏封面截脸那一档，这里不再取一次。
    """
    found, urls, seen = [], {}, set(skip)
    for work in works:
        code = normalise_code_key(work["code"])
        if not code or code in seen or len(urls) >= MAX_COVERS:
            continue
        seen.add(code)
        for url in cover_urls(work):
            body = fetch(url)
            if body is None:
                continue
            urls[code] = url
            record = probe.on_bytes(body) or {}
            px = record.get("px") or [0, 0]
            face = avatar_cover_face.CoverFace(0, code, body, int(px[0]), int(px[1]), record)
            if face.face_px > 0 and avatar_cover_face.readable_cut(face, probe):
                found.append(face)
            break
    found.sort(key=lambda face: (face.face_px, face.width * face.height), reverse=True)
    return found, urls


def agreeing(faces: list, matcher) -> tuple[list, dict]:
    """彼此过线、又不是同一张照片的那几张脸，连同每一对的余弦。

    比的是截出来的那块方图：装上去的就是它，比整张封面会比到封面上另一张脸。
    """
    features = {}
    for face in faces:
        cut = avatar_cover_face.cut(face)
        feature = matcher.embedding(cut[0]) if cut is not None else None
        if feature is not None:
            features[face.code] = feature
    scores: dict[str, float] = {}
    agreed: set[str] = set()
    codes = list(features)
    for index, one in enumerate(codes):
        for other in codes[index + 1:]:
            score = face_match.cosine(features[one], features[other])
            scores[f"{one}~{other}"] = round(score, 3)
            if face_match.COSINE_THRESHOLD <= score < NEAR_DUPLICATE:
                agreed.update((one, other))
    return [face for face in faces if face.code in agreed], scores


def find(pages, actor: str, skip: set[str], probe, matcher, transport,
         providers_root: Path) -> Outcome:
    """读她的单人作品，取封面、截脸、互证。只读：装与留交给调用方。

    先读女优页：补资料后继取过、多半在缓存里，作品不多的女优那一页就列全了。女优页上数到的
    单人作品少于站方数的 `singleCount` 时，才去取单人作品筛选页。
    """
    from .performer_alias_followup import Blocked, Unavailable

    try:
        _final, html = pages.get(avwikidb.ACTOR_PAGE.format(id=actor))
        works = avwikidb.single_works(html, actor)
        if len(works) < avwikidb.single_count(html):
            _final, html = pages.get(avwikidb.SINGLE_WORKS_PAGE.format(id=actor))
            listed = {work["code"] for work in works}
            works += [work for work in avwikidb.single_works(html, actor)
                      if work["code"] not in listed]
    except (Blocked, Unavailable) as error:
        return Outcome(f"avwikidb 单人作品{UNFETCHED}：{error}", unavailable=True)
    if not works:
        return Outcome("avwikidb 上没有她的单人作品")
    cache = AvatarCandidateCache(Path(providers_root) / COVER_CACHE)
    faces, urls = listed_faces(works, skip, probe,
                               lambda url: fetch_cover(transport, cache, url))
    if not faces:
        return Outcome(f"馆外 {len(urls)} 部单人作品封面上没有能截的脸", urls=urls)
    agreed, scores = agreeing(faces, matcher)
    if matcher.unavailable:
        return Outcome(f"比对模型不可用：{matcher.unavailable}", faces=faces, urls=urls,
                       unavailable=True)
    evidence = {"avwikidb": actor, "codes": [face.code for face in agreed], "scores": scores}
    if len({face.code for face in agreed}) < AGREE_WORKS:
        return Outcome(f"馆外单人作品封面只截到 {len(faces)} 张脸，凑不齐两部互证",
                       faces=faces, urls=urls, evidence=evidence)
    return Outcome("", winner=agreed[0], faces=faces, urls=urls, evidence=evidence)


def origin(face, url: str, evidence: dict) -> tuple[bytes, dict] | None:
    """截出方图与来源记录。形状沿用馆藏封面截脸，番号与地址指向馆外那部。"""
    cut = avatar_cover_face.cut(face)
    if cut is None:
        return None
    body, record = cut
    return body, {**record, "asset_id": None, "cover_url": url, "offsite": True,
                  **({"face_match": evidence} if evidence else {})}


def keep_all(providers_root: Path, entity_id: int, outcome: Outcome) -> int:
    """没装上的那几张也截好留进候选缓存，返回留了几张。"""
    kept = 0
    for face in outcome.faces[:MAX_KEPT_FACES]:
        if face is outcome.winner:
            continue
        made = origin(face, outcome.urls.get(face.code, ""), {})
        if made is not None:
            avatar_picker.keep(providers_root, entity_id, *made)
            kept += 1
    return kept
