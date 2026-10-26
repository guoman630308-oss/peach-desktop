r"""avwikidb.com 的页面解析：作品页的出演女优、女优页的基本资料（ADR-0067）。

这个站在 Peach 里做三件事：给女优一个站上的编号（`/actor/<编号>/`），拿站上的出生日期与身高
去核对 minnano-av 的资料，以及列出她的单人作品，让补头像从馆外的作品封面上截脸（ADR-0074）。
站上的女优图是 DMM 的 125×125 小图，不作头像；作品封面是 DMM 自己的图，不是这一张。

**作品列表读 `__NEXT_DATA__`，不读卡片。** 女优页与单人作品筛选页
`/actor/<编号>/works/?filter=single` 的 `pageProps.movies` 每项给番号、`floor`（`videoa` 是 DMM
数字版、`videoc` 是素人、`mgs` 是 MGS）、`fanzaContentId` 与出演表 `actor`（每人带站上编号，
就是 `/actor/<编号>/` 那一段）。卡片上的 `alt` 会把十几个人截成「ほか」，结构化数据是全表。
女优页最多列 8 部，`pageProps.singleCount` 是站方数的单人作品部数。

**从作品页进，不从名字进。** 站内搜索是 Next.js 页面上的一个弹层，地址形态未取得；按名字
猜编号更不行。作品页 `/work/<番号>/` 头里有一份 JSON-LD `Movie`，`actor` 每项给 `name`、
`alternateName`（罗马字）与 `url`：她自己那部作品的出演表里名字对得上的那一位，就是她。

**女优页读 JSON-LD `Person`，不读正文。** `alternateName` 前两项是主名的读音与罗马字，后面
混着站上认为是同一个人的其他名义，其中有男优名，所以只取前两项。`height` 是
`QuantitativeValue`；三围不在结构化数据里，只在正文「身長・スリーサイズ」那一格
（`T148 B83(B) W55 H85`）。
"""
from __future__ import annotations

import html as html_entities
import json
import re
from datetime import date

SITE = "https://avwikidb.com/"
ACTOR_PAGE = SITE + "actor/{id}/"
WORK_PAGE = SITE + "work/{code}/"
SINGLE_WORKS_PAGE = SITE + "actor/{id}/works/?filter=single"
_ACTOR = re.compile(r"/actor/(\d+)/?$")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_SIZES_CELL = re.compile(r"身長・スリーサイズ\s*</p>\s*<p[^>]*>([^<]*)<", re.S)
_SIZE = re.compile(r"([TBWH])\s*(\d{2,3})(?:\s*[(（]\s*([A-Z]{1,2})\s*[)）])?")
_KANA = re.compile(r"^[぀-ゟ゠-ヿ\s・]+$")
_LATIN = re.compile(r"^[A-Za-z][A-Za-z\s'.-]*$")


def actor_id(url: str) -> str:
    """`https://avwikidb.com/actor/1046723/` → `1046723`；不是女优页地址返回空。"""
    found = _ACTOR.search(str(url or "").split("?")[0])
    return found.group(1) if found else ""


def _nodes(html: str):
    """页面里每一份 JSON-LD 的每一个节点；坏掉的那份跳过。"""
    for block in _LD_JSON.findall(html or ""):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict):
                yield node


def work_cast(html: str) -> list[dict]:
    """作品页 → [{"id", "name", "romaji"}]，按页上顺序，同一编号只留一次。"""
    cast: list[dict] = []
    seen: set[str] = set()
    for node in _nodes(html):
        if node.get("@type") != "Movie":
            continue
        actors = node.get("actor") or []
        for person in actors if isinstance(actors, list) else [actors]:
            if not isinstance(person, dict):
                continue
            found = actor_id(str(person.get("url") or ""))
            name = str(person.get("name") or "").strip()
            if not found or not name or found in seen:
                continue
            seen.add(found)
            romaji = person.get("alternateName")
            cast.append({"id": found, "name": name,
                         "romaji": romaji.strip() if isinstance(romaji, str) else ""})
    return cast


def _iso(value) -> str | None:
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        return None


def actor_profile(html: str) -> dict | None:
    """女优页 → 基本资料；页上没有这位的 `Person` 返回 None。

    交回 `id`、`name`、`kana`、`romaji`、`birth_date`（ISO）、`height_cm`、`bust_cm`、`cup`、
    `waist_cm`、`hip_cm`、`image`；读不出的是 None。
    """
    person = next((node for node in _nodes(html)
                   if node.get("@type") == "Person" and actor_id(str(node.get("url") or ""))), None)
    if person is None:
        return None
    names = [str(item).strip() for item in (person.get("alternateName") or [])
             if isinstance(item, str)]
    kana = names[0] if names and _KANA.match(names[0]) else None
    romaji = names[1] if len(names) > 1 and _LATIN.match(names[1]) else None
    height = person.get("height")
    height = height.get("value") if isinstance(height, dict) else height
    found = {"id": actor_id(str(person["url"])), "name": str(person.get("name") or "").strip(),
             "kana": kana, "romaji": romaji, "birth_date": _iso(person.get("birthDate")),
             "height_cm": int(height) if isinstance(height, (int, float)) and height else None,
             "bust_cm": None, "cup": None, "waist_cm": None, "hip_cm": None,
             "image": str(person.get("image") or "") or None}
    cell = _SIZES_CELL.search(html or "")
    names_by_letter = {"T": "height_cm", "B": "bust_cm", "W": "waist_cm", "H": "hip_cm"}
    for letter, number, cup in _SIZE.findall(html_entities.unescape(cell.group(1)) if cell else ""):
        if found[names_by_letter[letter]] is None:
            found[names_by_letter[letter]] = int(number)
        if letter == "B" and cup and found["cup"] is None:
            found["cup"] = cup
    return found


def romaji_key(text: str) -> str:
    """罗马字比较用的键：小写、按词排序。站上写 `Hikaru Minazuki`，minnano-av 写 `Minazuki Hikaru`。"""
    return " ".join(sorted(re.sub(r"[^a-z\s]", "", str(text or "").lower()).split()))


def _page_props(html: str) -> dict:
    found = _NEXT_DATA.search(html or "")
    try:
        props = json.loads(found.group(1))["props"]["pageProps"] if found else {}
    except (ValueError, KeyError, TypeError):
        return {}
    return props if isinstance(props, dict) else {}


def single_count(html: str) -> int:
    """女优页上站方数的单人作品部数（`pageProps.singleCount`）；读不出是 0。"""
    count = _page_props(html).get("singleCount")
    return count if isinstance(count, int) and count > 0 else 0


def single_works(html: str, actor: str) -> list[dict]:
    """女优页或作品列表页 → 出演表里只有 `actor` 这一位的作品，按页上顺序。

    每项 `{"code", "floor", "content_id", "mgs_image", "identified"}`。`identified` 是站上
    「特定済」：官方没写出演者，是站上认出来的。筛选页本身已经只列单人作品，这里仍按出演表
    再判一次：页面换了版式、或筛选参数失效时，不能把合集当成她的单人作品。
    """
    movies = _page_props(html).get("movies") or []
    works: list[dict] = []
    for movie in movies if isinstance(movies, list) else []:
        if not isinstance(movie, dict):
            continue
        cast = movie.get("actor") or []
        ids = {str(person.get("fanzaAvActressId") or "") for person in cast
               if isinstance(person, dict)}
        code = str(movie.get("adultVideoId") or "").strip()
        if not code or len(cast) != 1 or ids != {str(actor)}:
            continue
        works.append({"code": code, "floor": str(movie.get("floor") or ""),
                      "content_id": str(movie.get("fanzaContentId") or "").strip().lower(),
                      "mgs_image": str(movie.get("mgsImageUrl") or "").strip(),
                      "identified": bool(movie.get("actorUnknown"))})
    return works
