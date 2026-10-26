"""追更的 Web 契约层。

读接口无副作用；`/api/follow/check` 是唯一会联网的端点，可由用户点击或已启用的
APScheduler 任务触发。
`/api/follow/save` 写真相，走和 CLI 同一个 `save_asset(confirm=True)` 边界。
"""
from __future__ import annotations

import contextlib
import html
import hashlib
import json
import os
import re
import sqlite3
import time
import threading
import uuid
import urllib.parse

from pathlib import Path

from . import avatar_face, follow_assets, follow_creators, follow_providers, wants
from .follow import FollowSourceError
from .follow_faces import annotate_group
from .follow_check import plan_check, run_check
from .follow_covers import fanbox_video_indexes
from .web_settings import follow_initial_days
from .follow_discovery import (
    MAX_SUGGESTIONS, PROFILE_ALIAS_SKIP_SERVICES, archive_suggestions, discover,
    discovery_plan, no_backoff, suggest_term, tag_suggestions,
)
from .follow_image_dims import positive_dims
from .follow_identity import (
    author_display_name, author_key, author_name, name_key, official_avatar_url, source_metadata,
)
from .follow_secrets import (
    CREDENTIAL_GUIDE, CredentialError, CredentialStore, credential_store_for,
)
from .follow_stream import proxyable
from .follow_sources import (
    CONNECTORS, KemonoConnector, Rule34VideoConnector, build_connector,
    canonical_source_ref, display_thumb_url, f95_attachment_media_items, f95_discussion_image,
    is_history_end_error, media_content_hash, parse_source_url, resource_links,
)
from .follow_store import (
    FollowStore, ReleaseGroup, normalized_author_name,
)
from .taste_history import read_creator_candidates
from .web_state import path_version


#: 界面上给每个来源的中文短名。没登记的 provider 直接显示原名。
PROVIDER_LABELS = follow_providers.labels()

_STATUSES = ("new", "seen", "saved", "ignored")

#: 关注库整库取回的上限。筛选要在 Python 里做（见 follow 载荷的注释），
#: 所以这里不是分页，只是别让一个失控的库把内存吃干的护栏。
_ALL_ITEMS = 100_000
_BACKFILL_PROVIDERS = follow_providers.backfill_providers()


def _store(contract, connection) -> FollowStore:
    return FollowStore(lambda: connection, sources_root=contract.follow_sources_root)


#: 一条目最多给前端多少个 tag。筛选条是给人扫一眼用的，不是导出全部——
#: rule34.xxx 的热门帖能带上百个标签，整串发下去只会把筛选条撑爆。
MAX_ITEM_TAGS = 24

#: 按来源列出要从浏览面隐藏的既有条目，投影自 follow_providers。
_EXCLUDED_EXTERNAL_IDS = follow_providers.excluded_external_ids()

# 内容筛选不让载体/渲染方式挤掉动作、角色与作品标签。原始 metadata 仍完整保留。
LOW_VALUE_GENERAL_TAGS = frozenset({
    "video", "tagme", "sound",
    "no sound", "audio", "loop", "webm", "mp4",
})

# 关注页的标签是「里面发生了什么」，不是人物计数、身体部位、画幅、年份或场景。
# 来源类型是第一道门槛：只有来源明确标成 general 的标签才会走到这里；形态判据
# 只负责 general 内部的通用词清理，绝不能拿它猜 artist/metadata 等来源类型。
_NON_CONTENT_FOLLOW_TAG_RE = re.compile(
    r"^(?:"
    r"(?:19|20)\d{2}|\d{1,2}:\d{1,2}|"
    r"\d+[_\s-]*(?:boy|girl|futa)s?|female|male|male/female|female_only|"
    r"(?:light[-_\s]?skinned[_\s-]?)?(?:female|male)|human|"
    r"(?:nude|naked)(?:[_\s-](?:female|male))?|"
    r"(?:(?:large|big|medium|small|bouncing)[_\s-])?"
    r"(?:breasts?|ass|pussy|penis|vagina|nipples?|areolae)|"
    r"(?:blonde|black|white|long|short)[_\s-](?:hair|female)|"
    r"(?:blue|green|brown)[_\s-]eyes|light[_\s-]skin|"
    r"(?:shorter|longer)[_\s-]than[_\s-]\d+[_\s-]seconds|short[_\s-]video|"
    r"beach"
    r")$", re.I,
)

_IMAGE_MEDIA_RE = re.compile(r"\.(?:avif|gif|jpe?g|png|webp)(?:$|[?#])", re.I)
_VIDEO_MEDIA_RE = re.compile(r"\.(?:m4v|mov|mp4|og[gv]|webm)(?:/)?(?:$|[?#])", re.I)
#: 音乐剪辑合辑的两种写法。前后不接字母，标题里的 `PMV`、标签里的 `hmv` 都算，
#: 而 `pmvideo` 这种撞进去的词不算。
_MUSIC_EDIT_RE = re.compile(r"(?<![a-z])(?:pmv|hmv)(?![a-z])")


def _item_all_tags(item) -> list[str]:
    """Return every recorded source tag once, preserving source spelling."""
    raw = item.metadata.get("tags")
    if isinstance(raw, str):
        values = raw.split()
    elif isinstance(raw, list):
        values = [str(value).strip() for value in raw if str(value).strip()]
    else:
        values = []
    for field in ("categories", "models"):
        recorded = item.metadata.get(field)
        if isinstance(recorded, list):
            values.extend(str(value).strip() for value in recorded if str(value).strip())
    seen, result = set(), []
    for value in values:
        tag = html.unescape(value)
        key = tag.casefold()
        if not tag or key in seen:
            continue
        seen.add(key)
        result.append(tag)
    return result


def _recorded_tag_type(item, tag: str) -> str:
    raw = item.metadata.get("tag_types")
    if not isinstance(raw, dict):
        return ""
    value = raw.get(tag)
    if value is None:
        key = tag.casefold()
        value = next((candidate for name, candidate in raw.items()
                      if html.unescape(str(name)).casefold() == key), None)
    tag_type = str(value or "").casefold()
    return tag_type if tag_type in {
        "artist", "character", "copyright", "metadata", "general"
    } else ""


def _item_tags(item) -> list[str]:
    """条目的内容标签。

    rule34.xxx 存空格分隔标签；Rule34Video 详情页存保留空格的标签列表和分类列表。
    kemono 系与 f95zone 的列表接口不给标签，所以它们是空列表。

    实体在这里统一反转义：rule34.xxx 的 dapi 曾把 `miqo&#039;te` 这类转义形态
    直接写进 metadata，归一后旧行照常显示与筛选，也和反转义后的新写法并成
    同一个身份。unescape 是幂等的，对已干净的标签不起作用。

    去掉作者手柄本身：按作者筛已经有专门的筛选条，标签里再出现一次没有信息量。
    """
    values = _item_all_tags(item)
    subject = html.unescape(str(item.metadata.get("tag") or "")).casefold()
    seen, tags = set(), []
    for tag in values:
        key = tag.casefold()
        # 来源没给类型时宁可暂不放进卡片，也不把 unknown 猜成 general。
        if (_recorded_tag_type(item, tag) != "general"
                or key == subject or key in seen or key in LOW_VALUE_GENERAL_TAGS
                or _NON_CONTENT_FOLLOW_TAG_RE.fullmatch(tag.strip())):
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) >= MAX_ITEM_TAGS:
            break
    return tags


def _item_tag_types(item, tags: list[str]) -> dict[str, str]:
    return {tag: tag_type for tag in tags
            if (tag_type := _recorded_tag_type(item, tag))}


#: 声音标签按下划线、空格归一后比对。配音版包括后期加声音的 `sound_edit`：同一段动画
#: 常由配音者配上人声或音效后另发一帖。只写呻吟、音效的帖子不算，那是原片自带的声音。
_VOICED_TAGS = frozenset((
    "voice acted", "voice acting", "english voice acting", "japanese voice acting",
    "ai voice acted", "voice actress", "english voice", "voice",
    "dialogue", "english dialogue", "japanese dialogue", "sound edit",
))
_SILENT_TAGS = frozenset(("no sound", "no audio"))


def _item_audio(item) -> str | None:
    """条目的声音版本：`voiced` 配音版、`silent` 无声版，来源没标就是 None。"""
    tags = {re.sub(r"[\s_]+", " ", tag).strip().casefold() for tag in _item_all_tags(item)}
    if tags & _VOICED_TAGS:
        return "voiced"
    return "silent" if tags & _SILENT_TAGS else None


#: 路径首段就是账号名的出处站。bsky 的账号在第二段（`/profile/<handle>`），fanbox
#: 的账号是子域名，各自在 `_source_handle` 里单独取。
_HANDLE_HOSTS = frozenset((
    "x.com", "twitter.com", "patreon.com", "www.patreon.com", "subscribestar.adult",
    "www.subscribestar.adult", "bsky.app", "www.pixiv.net", "ko-fi.com",
))


def _handle_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", html.unescape(str(text or "")).casefold())


def _source_handle(url) -> str:
    first = str(url or "").split()
    try:
        parts = urllib.parse.urlsplit(first[0] if first else "")
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold()
    segments = [segment for segment in parts.path.split("/") if segment]
    if host.endswith(".fanbox.cc"):
        return host.split(".")[0]
    if host not in _HANDLE_HOSTS or not segments:
        return ""
    return segments[1] if host == "bsky.app" and len(segments) > 1 else segments[0]


def _same_handle(handle: str, tag: str) -> bool:
    """账号名与 artist 标签是不是同一个人。

    账号常比标签多个后缀（`2hour2hour` 对 `2hour2`），所以四个字符以上的前缀也算。
    """
    a, b = _handle_key(handle), _handle_key(tag)
    return bool(a and b) and (a == b or (
        min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a))))


def _item_credit(item) -> dict | None:
    """booru 帖子真正的发布者，被关注的这位只是素材署名时才有值。

    rule34 把动画、模型、场景的作者都标成 artist。帖子出处多半指向发布者自己的
    X／Patreon 帖子：账号名对上一个 artist 标签、而那一位不是被关注者时，卡片署名
    改成发布者，被关注者退成「署名含」。认不出账号、对不上标签，或对上的就是被关注者
    本人，照常显示被关注者（ADR-0078）。
    """
    if item.provider not in {"rule34xxx", "rule34paheal"}:
        return None
    followed = html.unescape(str(item.metadata.get("tag") or "")).strip()
    handle = _source_handle(item.metadata.get("source"))
    raw = item.metadata.get("tag_types")
    if not followed or not handle or not isinstance(raw, dict):
        return None
    artists = [html.unescape(str(tag)) for tag, kind in raw.items()
               if str(kind).casefold() == "artist"]
    if not any(_handle_key(tag) == _handle_key(followed) for tag in artists):
        return None
    owners = [tag for tag in artists if _same_handle(handle, tag)]
    if not owners or any(_same_handle(followed, tag) for tag in owners):
        return None
    # 同一人挂了几个写法（`madruga3d`、`madrugasfm`）时取和账号完全一致的，其次取最长的。
    poster = max(owners, key=lambda tag: (_handle_key(tag) == _handle_key(handle),
                                          len(_handle_key(tag))))
    return {"poster": poster, "credited": followed}


#: 作品名里的罗马数字。写成名单而不是通用式：通用式会把 `mix`、`did` 这类英文词
#: 也判成数字，而作品名里实际用到的就这十几个。
_ROMAN_NUMERALS = frozenset((
    "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx",
))


def _work_key(tag: str) -> str:
    """题材的身份。

    同一部作品被不同来源写成 `zenless_zone_zero` 和 `zenless zone zero`，下划线
    和大小写是写法差别不是两部作品。归一到一个键，它们才是筛选条上的同一枚。
    """
    return re.sub(r"[\s_]+", " ", html.unescape(str(tag or ""))).strip().casefold()


#: 作品名里保持小写的虚词。整串套首字母大写会读成「Angels Of Delusion」，
#: 而作品自己写的是 `Angels of Delusion`。首词不在此列，它总要大写。
_WORK_MINOR_WORDS = frozenset((
    "a", "an", "and", "at", "for", "in", "no", "of", "on", "or", "the",
    "to", "vs", "x",
    "de", "del", "di", "du", "la", "le", "van", "von",
))


#: 数字打头的短代号，`2b`、`9s` 这种：字母整个大写。
_CODE_WORD_RE = re.compile(r"\d+[a-z]{1,2}")
#: 词内的分段符：`chun-li`、`d.va`、`k/da` 各段都要提首字母。
_WORD_SEGMENT_RE = re.compile(r"([-./])")
#: 连字符后面的敬称保持小写：`hasshaku-sama` 不是 `Hasshaku-Sama`。
_LOWER_SEGMENTS = frozenset(("chan", "dono", "kun", "sama", "san", "senpai", "sensei"))
#: 末尾的消歧括号，可能叠几层：`mona_(genshin_impact)_(cosplay)`。
_TRAILING_QUALIFIERS_RE = re.compile(r"(?:\s*\([^()]*\))+\s*$")


def _work_label(tag: str) -> str:
    """题材的显示名：下划线换空格，去掉末尾括号里的消歧，全小写的词提首字母。

    `final_fantasy_vii` 直接摆在筛选条上读的是文件名不是作品名。按词判不按整串判：
    来源写成 `megami Tensei` 时只有后一个词是人写的形态，前一个照样要提；已经带
    大小写的词（`NieR`、`Genshin Impact`）原样留着，再套一遍规则只会改坏。
    `raven_(stellar_blade)` 末尾那个括号是站上的消歧，不是名字。
    """
    text = re.sub(r"[\s_]+", " ", html.unescape(str(tag or ""))).strip()
    text = _TRAILING_QUALIFIERS_RE.sub("", text) or text
    words = text.split(" ")
    last = len(words) - 1
    return " ".join(_label_word(word, index == 0, index == last)
                    for index, word in enumerate(words))


def _label_word(word: str, first: bool, last: bool) -> str:
    """一个词在显示名里的写法。

    罗马数字整词大写，否则要读成「Final Fantasy Vii」；只认末词那一个，否则
    `spy x family` 中间那个连接词也要被当成十。
    """
    if word != word.lower():
        return word
    if last and word in _ROMAN_NUMERALS:
        return word.upper()
    if not first and word in _WORK_MINOR_WORDS:
        return word
    if _CODE_WORD_RE.fullmatch(word):
        return word.upper()
    segments = _WORD_SEGMENT_RE.split(word)
    return "".join(segment if index and segment in _LOWER_SEGMENTS
                   else segment[:1].upper() + segment[1:]
                   for index, segment in enumerate(segments))


#: 题材那一排要的是作品、IP 或人物。发行商、工作室、平台、节庆和「Original」这类占位
#: 同样被来源记成 copyright。带法人后缀或以 Entertainment、Studios 这类词收尾的按
#: `_COMPANY_TAG_RE` 认；剩下的裸名字形态上跟作品名没有任何区别，只能一条条列。
_NON_WORK_TAGS = frozenset({
    "activision", "arc system works", "atlus", "bandai namco", "bioware", "capcom",
    "cd projekt red", "crunchyroll", "cygames", "disney", "electronic arts", "epic games",
    "fromsoftware", "gust", "hoyoverse", "hypergryph", "kadokawa", "kakao games", "koei",
    "koei tecmo", "konami", "krafton", "kuro games", "level-5", "microsoft", "mihoyo",
    "namco", "naughty dog", "ncsoft", "netease games", "netflix", "netmarble", "nexon",
    "nintendo", "papergames", "pearl abyss", "platinum games", "playstation", "riot games",
    "rockstar games", "sega", "shift up", "smilegate", "snk", "sonnori", "sony",
    "spike chunsoft", "square enix", "team cherry", "tecmo", "tencent", "tencent games",
    "type-moon", "ubisoft", "valve", "wizards of the coast", "xbox", "yostar",
    "brave group", "cover corp", "nanashi inc.", "774 inc.",
    "discord", "fanbox", "fantia", "gumroad", "instagram", "iwara", "mmd", "onlyfans",
    "patreon", "pixiv", "steam", "subscribestar", "tenga", "tiktok", "twitter", "vr chat",
    "youtube",
    "christmas", "halloween", "holidays", "new year", "new year 2026",
    "the game awards", "valentines day",
    "1", "asian mythology", "hentai", "indie virtual youtuber",
    "japanese mythology", "joi", "mythology", "original", "religion", "tmp",
})

#: 公司名按形态认的那一半：法人后缀，或以 Entertainment、Interactive、Studios、
#: Software、Pictures 这类词收尾。`Games` 不在此列——《The Hunger Games》是作品。
_COMPANY_TAG_RE = re.compile(
    r".*\b(?:inc|co|corp|corporation|ltd|llc|entertainment|interactive|studios?|"
    r"software|softworks|pictures|holdings)\.?")

#: 人物那一类里不是人物的：占位词，以及 FF14、LoL、Mass Effect 被来源记成 character 的种族。
_NON_CHARACTER_TAGS = frozenset({
    "adventurer", "anon", "anonymous", "anonymous character", "anonymous female",
    "anonymous male", "avatar", "background character", "background characters",
    "faceless female", "faceless male", "hunter", "npc", "oc", "original character",
    "original characters", "player character", "tig ol bitties", "y n", "you",
    "asari", "au ra", "demon", "drow", "elezen", "elvaan", "hrothgar", "hyur", "krogan",
    "lalafell", "miqote", "mutant", "orc", "padjal", "raen", "roegadyn", "viera",
    "vastaya", "xaela", "yordle",
})

#: 系列名到它在筛选条上的写法。以其中一条开头的题材全部并进这一条：用户要的是
#: 「Final Fantasy」，不是 VII、XIV、XV、VII Remake 各占一格把整排挤满。值必须
#: 显式写，撇号、内部大小写和官方写法没法从归一后的键还原。
_WORK_SERIES = {
    "atelier": "Atelier",
    "baldurs gate": "Baldur's Gate",
    "brown dust": "Brown Dust",
    "cyberpunk": "Cyberpunk",
    "darkstalkers": "Darkstalkers",
    "dc": "DC",
    "dead by daylight": "Dead by Daylight",
    "dead or alive": "Dead or Alive",
    "devil may cry": "Devil May Cry",
    "drag-on dragoon": "Drag-On Dragoon",
    "dragon age": "Dragon Age",
    "drakengard": "Drakengard",
    "fatal frame": "Fatal Frame",
    "fatal fury": "Fatal Fury",
    "fate": "Fate",
    "final fantasy": "Final Fantasy",
    "fire emblem": "Fire Emblem",
    "five nights at freddys": "Five Nights at Freddy's",
    "granblue fantasy": "Granblue Fantasy",
    "half-life": "Half-Life",
    "hollow knight": "Hollow Knight",
    "hololive": "hololive",
    "honkai": "Honkai",
    "king of fighters": "The King of Fighters",
    "kingdom hearts": "Kingdom Hearts",
    "league of legends": "League of Legends",
    "marvel": "Marvel",
    "mass effect": "Mass Effect",
    "metro": "Metro",
    "monster hunter": "Monster Hunter",
    "mortal kombat": "Mortal Kombat",
    "nier": "NieR",
    "ninja gaiden": "Ninja Gaiden",
    "nioh": "Nioh",
    "overwatch": "Overwatch",
    "persona": "Persona",
    "pretty cure": "Pretty Cure",
    "resident evil": "Resident Evil",
    "soul calibur": "Soul Calibur",
    "street fighter": "Street Fighter",
    "tekken": "Tekken",
    "the elder scrolls": "The Elder Scrolls",
    "the last of us": "The Last of Us",
    "the legend of heroes": "The Legend of Heroes",
    "the witcher": "The Witcher",
    "tomb raider": "Tomb Raider",
    "valkyria chronicles": "Valkyria Chronicles",
    "warcraft": "Warcraft",
    "xenoblade": "Xenoblade",
}

#: 长的先试：将来添了互为前缀的两个系列时，`final fantasy vii` 不能被
#: `final fantasy` 先吃掉。
_WORK_SERIES_KEYS = tuple(sorted(_WORK_SERIES, key=len, reverse=True))

#: 同一个系列的另一种叫法：日文原名、缩写，以及副标题排在系列名前面的外传。
#: 这些形态上认不出来，只能一条条认。
_WORK_ALIASES = {
    "ao no kiseki": "the legend of heroes",
    "biohazard": "resident evil",
    "crisis core final fantasy vii": "final fantasy",
    "dark stalkers": "darkstalkers",
    "dbd": "dead by daylight",
    "dc comics": "dc",
    "drag-on dragoon": "drakengard",
    "dragonflight": "warcraft",
    "eiyuu densetsu": "the legend of heroes",
    "ffxiv": "final fantasy",
    "futari wa precure": "pretty cure",
    "garou mark of the wolves": "fatal fury",
    "hajimari no kiseki": "the legend of heroes",
    "holoforce": "hololive",
    "holox": "hololive",
    "k da all out series": "league of legends",
    "k da series": "league of legends",
    "kiseki": "the legend of heroes",
    "marvel comics": "marvel",
    "precure": "pretty cure",
    "senjou no valkyria": "valkyria chronicles",
    "skyrim": "the elder scrolls",
    "stranger of paradise final fantasy origin": "final fantasy",
    "world of warcraft": "warcraft",
    "zero no kiseki": "the legend of heroes",
}


def _work_root(tag: str) -> str:
    """题材的规范身份：同一系列的各代、各写法归到同一枚。

    `Final Fantasy VII Remake` 和 `FFXIV` 各占一格时，那一排读起来是版本号列表
    而不是题材。先把竖线并列的别名、`(series)` 后缀、冒号副标题和撇号这些纯写法
    差别抹平，查一次别名，再看它是不是某个系列名开头；系列名本身也可能是别名
    （`Drag-On Dragoon` 就是 `Drakengard`），所以最后再查一次。
    """
    text = _work_key(tag).split("|", 1)[0]
    text = _TRAILING_QUALIFIERS_RE.sub("", text)
    text = re.sub(r"\s+", " ", text.replace(":", " ").replace("/", " ")
                  .replace("'", "").replace("’", "")).strip()
    if not text:
        return ""
    text = _WORK_ALIASES.get(text, text)
    for series in _WORK_SERIES_KEYS:
        if text == series or text.startswith(series + " "):
            text = series
            break
    return _WORK_ALIASES.get(text, text)


def _work_display(root: str, spellings: dict[str, int]) -> str:
    """题材在筛选条上的写法。

    系列照表写，`NieR` 和 `hololive` 的大小写是作品自己的，推不出来。其余用来源
    里出现最多的那种拼法：它比归一后的键更接近人写的形态，冒号和撇号都还在。
    """
    if root in _WORK_SERIES:
        return _WORK_SERIES[root]
    spelling = max(spellings.items(), key=lambda pair: (pair[1], pair[0]))[0]
    return _work_label(spelling)


#: 题材那一排收两类：来源记成 copyright 的作品，和记成 character 的人物。
_WORK_TAG_TYPES = frozenset({"copyright", "character"})


def _not_a_subject(root: str, tag_type: str) -> bool:
    """这枚身份不该上题材那一排：公司、平台、节庆、占位词，人物那类里还有种族。"""
    return (root in _NON_WORK_TAGS or _COMPANY_TAG_RE.fullmatch(root) is not None
            or (tag_type == "character" and root in _NON_CHARACTER_TAGS))


def _item_works(item) -> list[str]:
    """条目所属的题材：来源记成 `copyright` 的作品和记成 `character` 的人物。

    按词形猜会把画师手柄摆进题材那一排：`lazyprocrastinator` 在字面上跟作品名没有
    区别，区别只写在来源的类型里。人物跟作品同排：用户追的常常是 2B、D.Va 这一个人，
    不是整部作品。

    同一系列的各代在这里已经并成一枚，所以同时带 `final fantasy` 和
    `final fantasy vii` 的一条更新只留一个写法。
    """
    seen, works = set(), []
    for tag in _item_all_tags(item):
        tag_type = _recorded_tag_type(item, tag)
        if tag_type not in _WORK_TAG_TYPES:
            continue
        root = _work_root(tag)
        if (not root or root in seen or _not_a_subject(root, tag_type)
                or _NON_CONTENT_FOLLOW_TAG_RE.fullmatch(tag.strip())):
            continue
        seen.add(root)
        works.append(tag)
    return works


#: 题材头像只认这一个图床。地址来自来源记录而不是固定表，这道白名单就是那个闸：
#: 记录里存的是站点回的 JSON，不能让它把任意主机带进出网路径。
_WORK_ICON_HOSTS = frozenset({"api-cdn.rule34.xxx"})


def work_root(tag: str) -> str:
    """题材的规范身份。`/work-icon` 按它认题材，所以要能从模块外调用。"""
    return _work_root(tag)


#: 一次扫库算出的全部题材代表图能用多久。整排头像同时过期时浏览器会并排发来
#: 二十几个 `/work-icon`，每个都从头扫一遍全库，而结果是同一份表。
_WORK_ICON_MEMO_SECONDS = 60
#: 每个题材备几个候选。取图那一端顺着热度往下找第一张看得见脸的，五张缩略图合起来
#: 也就几十 KB，而多备一张挡住的是「圆标里是一截身子」。
_WORK_ICON_CANDIDATES = 5
_work_icon_memo: tuple[float, dict[str, list[str]]] = (0.0, {})
_work_icon_lock = threading.Lock()


def _work_icon_candidate(item) -> str:
    """这一条能给题材当代表图吗：能就是那张封面的地址，不能是空串。

    取的是卡片上那张高清封面（`thumb_url`，实测 1280×720 到 4096×2304、30–370 KB），
    不是 250px 的 `preview_url`。圆标只有 28px，光看显示尺寸两层都够用，差别在检脸：
    低分辨率缩略图可能缺少足够的人脸细节。没有封面的旧行退回缩略图。

    地址来自来源记录而不是固定表，白名单就是那道闸：记录里存的是站点回的 JSON，
    不能让它把任意主机带进出网路径。
    """
    if item.provider != "rule34xxx" or _excluded_item(item):
        return ""
    url = str(item.thumb_url or item.metadata.get("preview_url") or "")
    return url if urllib.parse.urlsplit(url).netloc in _WORK_ICON_HOSTS else ""


def _work_icon_table(store) -> dict[str, dict]:
    """题材 → 本库现成的几个候选图，加它在站上的标签写法。

    候选按热度排，带 `3d` 标签的排在前面：用户要的是这个题材最有代表性的一张，
    rule34 的 score 是站点自己的热度排序，本库里现成存着；`3d` 优先是因为这一排要的
    是 3D 作品，不是同人画。

    给几个而不是一个：最热的那张常常是个身体特写，圆标里于是一张脸都没有。取图那
    一端会顺着这个次序找出第一张看得清脸的，实测最热那张只有一半带脸。

    给的是站点那张封面而不是正片：封面实测 30–370 KB，而同一条的正片可能是一张几
    MB 的动图，超过 `follow_assets.MAX_BYTES` 反而一张都存不下。

    标签写法要按本库记下的那个，不能拿归一化后的题材身份去站上查：身份是把
    `the_witcher_(series)`、`dbd`、`clair_obscur:_expedition_33` 抹平之后的结果，
    照着它拼出来的 `dead_by_daylight` 在站上是零命中。只认 rule34xxx 条目记下的写法
    ——别的站把同一部作品写成 `Dead or Alive`，那不是 rule34 的标签。
    """
    ranked: dict[str, list[tuple[tuple[int, int, int], str]]] = {}
    spellings: dict[str, dict[str, int]] = {}
    for item in store.items(limit=_ALL_ITEMS):
        if _excluded_item(item):
            continue
        works = _item_works(item)
        if item.provider == "rule34xxx":
            for tag in works:
                seen = spellings.setdefault(_work_root(tag), {})
                seen[tag] = seen.get(tag, 0) + 1
        url = _work_icon_candidate(item)
        if not url:
            continue
        roots = {_work_root(tag) for tag in works}
        if not roots:
            continue
        try:
            score = int(item.metadata.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        spatial = 1 if "3d" in {tag.casefold() for tag in _item_all_tags(item)} else 0
        rank = (spatial, score, item.id)
        for root in roots:
            row = ranked.setdefault(root, [])
            row.append((rank, url))
            row.sort(key=lambda pair: pair[0], reverse=True)
            del row[_WORK_ICON_CANDIDATES:]
    table: dict[str, dict] = {}
    for root in set(ranked) | set(spellings):
        counted = spellings.get(root) or {}
        # 同一个题材在站上常有几个写法（`nier:_automata` 与 `nier`），用得最多的
        # 那个才是这个库实际在追的那条线；并列时取字典序，好让两次扫库给出同一个答案。
        tag = max(sorted(counted), key=counted.get, default="")
        table[root] = {"urls": [url for _, url in ranked.get(root) or ()],
                       "tag": _site_tag(tag or root)}
    return table


def _site_tag(tag: str) -> str:
    """rule34 的标签形态：空格换成下划线。"""
    return re.sub(r"\s+", "_", str(tag or "").strip())


def _work_icons(store) -> dict[str, dict]:
    """题材 → 候选图与标签写法，整张表一起算再存一分钟。

    判据见 `_WORK_ICON_MEMO_SECONDS`。筛选条和 `/work-icon` 读的是同一份表，所以
    那一排说「这枚有图」和端点真的取得到图不会各说各话。落盘那份图另有自己的保鲜期，
    这里只挡住「同一秒里把全库扫二十几遍」。
    """
    global _work_icon_memo
    with _work_icon_lock:
        stamp, table = _work_icon_memo
        if time.time() - stamp >= _WORK_ICON_MEMO_SECONDS:
            table = _work_icon_table(store)
            _work_icon_memo = (time.time(), table)
    return table


def work_icon_urls(store, root: str) -> list[str]:
    """题材头像的候选图，按热度从高到低；一张都挑不出时是空列表。"""
    return list((_work_icons(store).get(root) or {}).get("urls") or ())


def work_icon_tag(store, root: str) -> str:
    """这个题材在 rule34 上的标签写法。本库没记下写法时按身份拼一个。"""
    return str((_work_icons(store).get(root) or {}).get("tag") or _site_tag(root))


#: 去站点问一次最多要回几张。本库那几张挑不出脸才会走到这里，而这一趟的代价是一次
#: JSON 加最多这么多张封面；给多了是在一枚 28px 的圆标上花几兆流量。
_WORK_ICON_REMOTE = 8
#: 问站点的次序。先只要 3D——这一排要的是 3D 作品；那个标签下一张都没有时再问一次
#: 不限形式的，总好过让这一枚空着。
_WORK_ICON_QUERIES = ("{tag} 3d sort:score", "{tag} sort:score")


def work_icon_search_urls(contract, tag: str, *,
                          transport=None, limit: int = _WORK_ICON_REMOTE) -> list[str]:
    """去 rule34 问这个题材最热的几张封面，返回能当代表图的地址。

    本库现成的那几张全都看不清脸时才走这一趟：库里存的是用户关注的那几位作者发的
    东西，一个题材常常只有一两条，而那一两条未必有正脸。站上同一个标签下有成千上万
    帖，按热度往下找总能找到一张。

    白名单照旧（`_WORK_ICON_HOSTS`）：地址来自站点回的 JSON，不能让它把任意主机带进
    出网路径。取不到凭据、站点报错或网络不通一律返回空——圆标退回首字母，不是 500。
    """
    tag = _site_tag(tag)
    if not tag:
        return []
    credential = _credential_store(contract).load("rule34xxx")
    if credential is None:
        return []
    try:
        connector = build_connector("rule34xxx", transport=transport,
                                    credential=credential, max_items=limit,
                                    enrich_budget=0, sleeper=no_backoff)
        for query in _WORK_ICON_QUERIES:
            urls: list[str] = []
            for candidate in connector.search(query.format(tag=tag), limit=limit):
                url = str(candidate.thumb_url or "")
                if (url not in urls
                        and urllib.parse.urlsplit(url).netloc in _WORK_ICON_HOSTS):
                    urls.append(url)
            if urls:
                return urls
    except (FollowSourceError, CredentialError, OSError):
        return []
    return []


def work_icon_root(contract) -> Path:
    """题材头像落盘的目录。

    `/work-icon` 写图和 sidecar、这里读 sidecar，两端必须算出同一个路径——不然
    取景永远是几何居中，而界面上这和「这张图本来就该这么摆」看不出区别。
    """
    return Path(contract.candidate_root) / follow_assets.ROOT_NAME


def _work_icon_focus(cache_root: Path | None, root: str) -> dict | None:
    """题材头像的取景提示：挪到脸上的 object-position 加脸框像素，没有就是 None。

    记录由 `/work-icon` 取回图之后写在图旁边，和实体图那套 sidecar 同一个约定
    （`avatar_face.focus_hint`），页面因此也走头像那套放大。没检出脸、还没取过图、
    模型不可用都返回 None，那时圆标按样式表里的默认取景摆——不拿一个猜出来的位置
    冒充检出结果。
    """
    if cache_root is None:
        return None
    return avatar_face.focus_hint(
        avatar_face.read_sidecar(follow_assets.cache_path(cache_root, "works", root)))


def _media_kind(item) -> str:
    recorded = str(item.metadata.get("media_kind") or "")
    if recorded in {"video", "image"}:
        return recorded
    if item.provider == "rule34video":
        return "video"
    media = str(item.media_url or "")
    if _IMAGE_MEDIA_RE.search(media):
        return "image"
    if _VIDEO_MEDIA_RE.search(media):
        return "video"
    return "external"


def _video_media_type(value: object) -> str:
    path = urllib.parse.urlsplit(str(value or "")).path.casefold()
    if path.endswith(".webm"):
        return "video/webm"
    if path.endswith(".mov"):
        return "video/quicktime"
    return "video/mp4"


def _media_key(media: dict) -> str:
    """一条媒体跨抓取稳定的标识：`id` 优先、`url` 兜底。隐藏状态按它记。"""
    return str(media.get("id") or media.get("url") or "")


def _raw_media_items(item) -> list:
    raw = item.metadata.get("media_items")
    if not isinstance(raw, list) or not raw:
        return f95_attachment_media_items(item.metadata) if item.provider == "f95zone" else []
    return raw


#: 个别作者把非作品图固定贴在正文首位，按「作者 + 标题」直接不投影：
#: `lazyprocrast` 标题带 Poll 的帖子，第一张正文图必是投票结果图表。
_FANBOX_POLL_CHART_AUTHOR = "lazyprocrast"
_FANBOX_POLL_CHART_TITLE_RE = re.compile(r"\bpoll\b", re.IGNORECASE)


def _skips_poll_chart(item) -> bool:
    return (item.provider == "fanbox"
            and str(item.ref or "") == _FANBOX_POLL_CHART_AUTHOR
            and _FANBOX_POLL_CHART_TITLE_RE.search(str(item.title or "")) is not None)


def _media_projection(item) -> tuple[list[dict], list[dict]]:
    """把 media_items 投影成浏览器字段，按用户隐藏的键拆成（可见，已隐藏）两份。

    `index` 保持原始清单里的序号不重排：`/follow-stream?media=N` 按同一份原始
    清单解析，投影一旦重新编号，点开第 N 张就会拿到另一张。
    """
    hidden = frozenset(item.hidden_media or ())
    visible, concealed = [], []
    skip_poll_chart = _skips_poll_chart(item)
    covered = fanbox_video_indexes(item) if item.provider == "fanbox" else []
    for index, media in enumerate(_raw_media_items(item)):
        if not isinstance(media, dict):
            continue
        if item.provider == "f95zone" and f95_discussion_image(media.get("url") or media.get("thumb_url")):
            continue
        kind = str(media.get("media_kind") or "")
        if kind not in {"video", "image"}:
            continue
        if skip_poll_chart and index == 0 and kind == "image":
            continue
        thumb = str(media.get("thumb_url") or "")
        thumb = thumb if thumb.startswith("https://") else None
        if thumb is None and index in covered:
            # fanbox 站内视频不带缩略图，画面由 /follow-cover 抽帧。第一个视频用卡面那条
            # 地址，两处共用一次抽帧和同一份浏览器缓存。
            thumb = (f"/follow-cover?id={item.id}" if index == covered[0]
                     else f"/follow-cover?id={item.id}&media={index}")
        projected = {
            "index": index,
            "name": str(media.get("name") or f"{kind} {index + 1}"),
            "media_kind": kind,
            "media_type": _video_media_type(media.get("name") or media.get("url"))
                          if kind == "video" else None,
            "thumb_url": thumb,
            "size": media.get("size"),
            # 固有宽高给瀑布流预留比例用：fanbox 的 imageMap 带原尺寸，没有的
            # 来源保持 None，前端按无尺寸那套占位。
            "width": _positive_dim(media.get("width")),
            "height": _positive_dim(media.get("height")),
            "resource_provider": str(media.get("resource_provider") or ""),
            "resource_group": str(media.get("resource_group") or "") or None,
            "resource_group_label": str(media.get("resource_group_label") or "") or None,
        }
        (concealed if _media_key(media) in hidden else visible).append(projected)
    return visible, concealed


def _positive_dim(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _media_items(item) -> list[dict]:
    """给浏览器媒体序号和展示字段；真实上游 URL 仍只留在服务端 metadata。"""
    return _media_projection(item)[0]


def item_thumbs(contract, connection, item_ids) -> dict[int, str | None]:
    """别处列出关注条目时用的缩略图，与关注卡同一份投影（`_thumb_url`）。"""
    store = _store(contract, connection)
    found: dict[int, str | None] = {}
    for item_id in item_ids:
        item = store.item(int(item_id))
        if item is not None:
            found[int(item_id)] = _thumb_url(item)
    return found


def _thumb_url(item) -> str | None:
    thumb = _remote_thumb_url(item)
    return _cached_thumb_url(item, thumb)


def _cached_thumb_url(item, thumb, media=None):
    if not thumb or not thumb.startswith("https://"):
        return thumb
    if media is None:
        media = next((index for index, row in enumerate(_raw_media_items(item))
                      if (row.get("thumb_url") or row.get("url")) == thumb), None)
    version = hashlib.sha256(thumb.encode()).hexdigest()[:16]
    return f"/follow-image?id={item.id}{f'&media={media}' if media is not None else ''}&v={version}"


def _remote_thumb_url(item) -> str | None:
    """卡片上用哪个缩略图。

    Rule34Video 经 /follow-cover 缓存静态 poster，并在作品组内寻找封面回退。
    Paheal 图片经同源代理读原图，视频经 /follow-cover 抽首帧并缓存。
    这些是 Peach 自己的路由决定，所以留在这一层；
    其余全是「这个站的缩略图 URL 长什么样」，那是站点知识，实现在各连接器里。
    """
    if item.provider == "rule34video" and item.thumb_url:
        return f"/follow-cover?id={item.id}"
    if item.provider == "f95zone" and f95_discussion_image(item.thumb_url):
        return next((media["thumb_url"] for media in _media_items(item) if media["thumb_url"]), None)
    if item.provider == "fanbox":
        thumb = _fanbox_card_thumb(item) or _unhide_thumb(item, display_thumb_url(item))
        media_items = item.metadata.get("media_items") or []
        videos = [media for media in media_items if isinstance(media, dict)
                  and media.get("media_kind") == "video"
                  and media.get("resource_provider") == "fanbox"]
        if videos and (not thumb or thumb in {media.get("url") for media in videos}
                       or urllib.parse.urlsplit(thumb).path.lower().endswith((".mp4", ".webm", ".mov", ".m4v"))):
            return f"/follow-cover?id={item.id}"
        if thumb:
            return thumb
    if item.provider == "rule34paheal" and item.media_url:
        kind = _media_kind(item)
        if kind == "image":
            return f"/follow-stream?id={item.id}"
        if kind == "video":
            return f"/follow-cover?id={item.id}"
    return _unhide_thumb(item, display_thumb_url(item))


#: 账本一变，冷缓存下同时到的一屏封面请求只让一个去算整库分组，其余等它算完直接命中。
_COVER_INDEX_LOCK = threading.Lock()


def cover_alternatives(contract, item_id: int) -> tuple[str, ...]:
    """同一作品其他版本的 Rule34Video 封面；来源地址只在服务端使用。"""
    with _COVER_INDEX_LOCK:
        index = contract.cached_until_changed(
            "follow-cover-alternatives", lambda: _cover_alternative_index(contract))
    return index.get(item_id, ())


def _cover_alternative_index(contract) -> dict[int, tuple[str, ...]]:
    """每条 Rule34Video 条目 → 同组其他 Rule34Video 版本的 poster 地址，整库分组一次建好。"""
    with contract.database.read_connection() as connection:
        store = _store(contract, connection)
        sources = store.sources()
        aliases, _ = store.author_aliases()
        enabled = {int(source["id"]) for source in sources if source["enabled"]}
        everything = contract.cached_until_changed(
            "follow-items:None", lambda: tuple(item for item in store.items(limit=_ALL_ITEMS)
                                               if item.source_id in enabled and not _excluded_item(item)))
        index: dict[int, tuple[str, ...]] = {}
        for group in store.group(everything, group_authors(sources, aliases)):
            members = [member for member in (group.primary, *group.variants, *group.duplicates)
                       if member.provider == "rule34video"]
            for member in members:
                others = tuple(dict.fromkeys(other.thumb_url for other in members
                                             if other.id != member.id and other.thumb_url))
                if others:
                    index[member.id] = others
        return index


def _fanbox_card_thumb(item) -> str | None:
    """fanbox 的卡面：作者挑的封面优先，其次正文里第一张没被隐藏的图。

    封面是作者给这篇选的展示图；它收在媒体清单里（`id=cover`），从那里认，
    未补齐正文的行没有清单，退回存的缩略图（列表阶段就是封面）。
    """
    hidden = frozenset(item.hidden_media or ())
    fallback = None
    for index, media in enumerate(_raw_media_items(item)):
        if not isinstance(media, dict) or media.get("media_kind") != "image":
            continue
        if _media_key(media) in hidden:
            continue
        if index == 0 and _skips_poll_chart(item):
            continue
        thumb = str(media.get("thumb_url") or "")
        if not thumb.startswith("https://"):
            continue
        if media.get("id") == "cover":
            return thumb
        fallback = fallback or thumb
    return fallback


def _unhide_thumb(item, thumb: str | None) -> str | None:
    """缩略图落在被隐藏的媒体上时，改用第一张可见媒体的缩略图。

    卡片缩略图存的是抓取时的正文首图；用户把那张图藏起来之后，卡面还挂着它
    就等于隐藏没生效。一张可见的都没有时不给缩略图，卡片走无图占位。
    """
    hidden = frozenset(item.hidden_media or ())
    if not hidden or not thumb:
        return thumb
    hidden_thumbs = {str(media.get("thumb_url") or "") for media in _raw_media_items(item)
                     if isinstance(media, dict) and _media_key(media) in hidden}
    if thumb not in hidden_thumbs:
        return thumb
    return next((media["thumb_url"] for media in _media_items(item) if media["thumb_url"]),
                None)


def _f95_has_resource(media_url: str | None, metadata: dict) -> bool:
    if media_url:
        return True
    links = metadata.get("links")
    if isinstance(links, list) and resource_links(
            "\n".join(str(value) for value in links)):
        return True
    attachments = metadata.get("attachments")
    if isinstance(attachments, list):
        return any(
            str(value).startswith("https://")
            and not _IMAGE_MEDIA_RE.search(str(value))
            for value in attachments
        )
    return False


def _excluded_item(item) -> bool:
    if item.external_id in _EXCLUDED_EXTERNAL_IDS.get(item.provider, frozenset()):
        return True
    # 音乐剪辑合辑（PMV / HMV）是把许多人的片段剪到一首曲子上，按谁的作品都算不上。
    # 标题和标签一起看，独立词才算：`Dope Track PMV` 命中，`pmvideo` 不命中。
    text = " ".join((str(item.title or ""), " ".join(_item_all_tags(item)))).casefold()
    if _MUSIC_EDIT_RE.search(text):
        return True
    # 采集端按画面作者数挡合辑，但门槛读的是 `visual_model_count`：历史条目落库时
    # 还没有这个字段，超出探测上限没取到详情的也没有，门槛在它们身上从未生效。
    # 这里按同一份判据和同一个上限回算一次，不改 ledger。
    if item.provider == Rule34VideoConnector.provider:
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        counted = metadata.get("visual_model_count")
        if not isinstance(counted, int):
            counted = Rule34VideoConnector.visual_model_count(metadata.get("models"))
        if counted > Rule34VideoConnector.MAX_COLLECTION_MODELS:
            return True
    # 旧版曾把纯讨论和图片表情包写进候选。读取时隐藏，不改 ledger；真正的
    # 文件附件与文件站链接仍保留，下一次检查会按当前证据重新归类。
    return (item.provider == "f95zone"
            and not _f95_has_resource(item.media_url, item.metadata))


def _item_payload(item, credential_providers: frozenset[str] = frozenset()) -> dict:
    tags = _item_tags(item)
    detail_tags = _item_all_tags(item)
    media_kind = _media_kind(item)
    media_items, hidden_media = _media_projection(item)
    if media_items:
        media_kind = media_items[0]["media_kind"]
    elif item.provider == "f95zone" and f95_discussion_image(item.thumb_url):
        media_kind = "external"
    resource_provider = item.provider
    try:
        resource_url = urllib.parse.urlsplit(str(item.media_url or ""))
    except ValueError:
        resource_url = urllib.parse.urlsplit("")
    if (resource_url.hostname in {"gofile.io", "www.gofile.io"}
            or (resource_url.hostname == "f95zone.to"
                and resource_url.path.startswith("/masked/gofile.io/"))):
        resource_provider = "gofile"
    recorded_links = item.metadata.get("links")
    safe_resource_urls = resource_links(
        "\n".join(str(value) for value in recorded_links)
        if isinstance(recorded_links, list) else ""
    )
    needs_credential = bool(item.metadata.get("media_needs_credential"))
    credential_ready = not needs_credential or item.provider in credential_providers
    # 直链媒体只有媒体代理肯取的才算可播（白名单主机，或来源登记为放行任意公网图床）；
    # 不放行的由界面直接引用缩略图。
    playable = (media_kind in {"video", "image"}
                and ((bool(media_items) and credential_ready)
                     or (not needs_credential and proxyable(item.provider, item.media_url))))
    return {
        "id": item.id,
        "provider": item.provider,
        "provider_label": PROVIDER_LABELS.get(item.provider, item.provider),
        "resource_provider": resource_provider,
        "source_id": item.source_id,
        "source_label": item.source_label,
        "external_id": item.external_id,
        # 旧 rule34xxx 行的标题由带实体的标签拼成（`barnabas&#039; mother`）；
        # 出口统一反转义，与 _item_tags 同一处理，unescape 幂等。
        "title": html.unescape(item.title) if item.title else item.title,
        "author": item.metadata.get("author") or None,
        "credit": _item_credit(item),
        "audio": _item_audio(item),
        "summary": item.metadata.get("summary") or None,
        "url": item.url,
        "thumb_url": _thumb_url(item),
        "published_at": item.published_at,
        # 界面必须照实显示精度：rule34video 只给「1 周前」，换算值不是发布时间。
        "published_precision": item.published_precision,
        "version": item.version,
        "duration": item.duration,
        "variant_kind": item.variant_kind,
        "variant_label": item.variant_label,
        "status": item.status,
        "asset_id": item.asset_id,
        "media_needs_credential": needs_credential,
        "media_error": str(item.metadata.get("media_error") or "") or None,
        "has_media": bool(item.media_url) or bool(media_items),
        "media_kind": media_kind,
        # 条目级直链图片的固有宽高，与 media_items 里每张自带的那对同义：图片墙
        # 拿它在图落地前占好比例。没有的来源保持 None，界面加载完会回写。
        "width": _positive_dim(item.metadata.get("width")),
        "height": _positive_dim(item.metadata.get("height")),
        "media_type": _video_media_type(item.media_url)
                      if media_kind == "video" else None,
        # 可直接读取的附件与仍需会话解析的外链可以同时存在。后者不能把
        # 前者整体锁死；详情继续复用同一条 /follow-stream 媒体代理路径。
        "playable": playable,
        "media_items": [{**media, "thumb_url": _cached_thumb_url(item, media["thumb_url"], media["index"])}
                        for media in media_items],
        "hidden_media": [{**media, "thumb_url": _cached_thumb_url(item, media["thumb_url"], media["index"])}
                         for media in hidden_media],
        # 只投影连接器已验证过的文件页域名；原始媒体 URL 仍不进入 feed。
        "resource_urls": safe_resource_urls,
        "tags": tags,
        "detail_tags": detail_tags,
        "tag_types": _item_tag_types(item, detail_tags),
    }


def _group_payload(group: ReleaseGroup,
                   credential_providers: frozenset[str] = frozenset()) -> dict:
    return {
        "release_key": group.release_key,
        "primary": _item_payload(group.primary, credential_providers),
        "variants": [_item_payload(item, credential_providers)
                     for item in group.variants],
        "duplicates": [_item_payload(item, credential_providers)
                       for item in group.duplicates],
        "providers": list(group.providers),
        "has_wip": group.has_wip,
        "is_release": group.is_release,
        "newest_at": group.newest_at,
    }


def _content_hashes(group: ReleaseGroup) -> dict[tuple[int, int | None], str]:
    """组里每一份媒体的文件内容哈希，键是（条目 id，媒体序号；条目本身为 None）。

    哈希从已存的原始地址解析，原始地址不进 feed，所以在这里算好交给
    `follow_faces.annotate_group`，不写进载荷。
    """
    hashes: dict[tuple[int, int | None], str] = {}
    for item in (group.primary, *group.variants, *group.duplicates):
        digest = media_content_hash(item.provider, item.media_url)
        if digest:
            hashes[(item.id, None)] = digest
        for index, media in enumerate(_raw_media_items(item)):
            if isinstance(media, dict):
                digest = media_content_hash(item.provider, media.get("url"))
                if digest:
                    hashes[(item.id, index)] = digest
    return hashes


def group_authors(source_rows, aliases: dict[str, str] | None = None,
                  ) -> dict[int, tuple[str, frozenset[str]]]:
    """每条来源的作者键，连同这位作者在各个来源上的全部名字写法。

    分组靠它剥掉标题里夹着的作者名、跨同一作者的来源对版本。名字取归一化后的写法，
    与 `author_key` 同一口径：来源标签、来源记下的作者键、别名表里指向同一人的键。
    """
    keys = {int(row["id"]): author_key(row, aliases) for row in source_rows}
    # 别名表写的是名字键；绑了实体的那组键换成了 `entity:`，别名要跟着找到它。
    grouped_as = {name_key(row, aliases): keys[int(row["id"])] for row in source_rows}
    names: dict[str, set[str]] = {}
    for row in source_rows:
        spellings = names.setdefault(keys[int(row["id"])], set())
        for raw in (normalized_author_name(str(row["label"] or row["ref"] or ""),
                                           provider=str(row["provider"] or "")),
                    str(source_metadata(row).get("author_key") or "").strip()):
            if raw:
                spellings.update((raw, (aliases or {}).get(raw, raw)))
    for alias, canonical in (aliases or {}).items():
        key = grouped_as.get(f"name:{canonical}")
        if key in names:
            names[key].add(alias)
    return {source_id: (key, frozenset(names.get(key, ()))) for source_id, key in keys.items()}


#: 一次最多提议多少条别名。这是给人一条条看的清单，不是批处理。
MAX_ALIAS_SUGGESTIONS = 12


def _profile_link_suggestions(rows, aliases: dict[str, str]) -> list[dict]:
    """首楼名片上的手柄与这条来源的作者名不一致时，提议合并。

    证据比字符串包含硬：`Strauzek Collection [2026-09-04] [Mr_Strauz]` 的首楼上同时
    挂着 `twitter.com/strauzek` 和 `twitter.com/Mr_Strauz`，两个写法是同一个人在
    同一张名片上自己写的。仍然只是**提议**——合不合由人点。
    """
    suggestions: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        # “首楼创作者主页”只来自 F95 的开楼正文。booru 每条作品的 source 是作品
        # 出处，合作作品会指向另一位作者；即使旧账本里已有误收的 official_links，
        # 也不能再把它展示成身份合并建议。
        if row["entity_id"] or row["provider"] != "f95zone":
            continue
        canonical = author_display_name(row)
        canonical_key = normalized_author_name(canonical)
        if not canonical_key:
            continue
        for link in source_metadata(row).get("official_links") or ():
            if not isinstance(link, dict):
                continue
            service = str(link.get("service") or "")
            handle = str(link.get("handle") or "").strip()
            alias_key = normalized_author_name(handle)
            if service in PROFILE_ALIAS_SKIP_SERVICES or not alias_key:
                continue
            if aliases.get(alias_key, alias_key) == aliases.get(
                    canonical_key, canonical_key):
                continue
            pair = (canonical_key, alias_key)
            if pair in seen:
                continue
            seen.add(pair)
            suggestions.append({
                "canonical": canonical,
                "alias": handle,
                "evidence": f"{PROVIDER_LABELS.get(row['provider'], row['provider'])}"
                            f" 首楼的创作者主页链接指向 {service}/{handle}",
            })
            if len(suggestions) >= MAX_ALIAS_SUGGESTIONS:
                return suggestions
    return suggestions


def _follow_alias_suggestions(rows, aliases: dict[str, str]) -> list[dict]:
    """Suggest conservative cross-platform aliases; never merge automatically."""
    suggestions = _profile_link_suggestions(rows, aliases)
    if len(suggestions) >= MAX_ALIAS_SUGGESTIONS:
        return suggestions
    identities: dict[str, dict] = {}
    for row in rows:
        if row["entity_id"]:
            continue
        raw_key = author_key(row).removeprefix("name:")
        if not raw_key or raw_key.startswith("source:"):
            continue
        identity = identities.setdefault(raw_key, {
            "key": raw_key, "name": author_display_name(row), "providers": set(),
        })
        identity["providers"].add(str(row["provider"]))
        candidate_name = author_display_name(row)
        if candidate_name and len(candidate_name) < len(identity["name"]):
            identity["name"] = candidate_name

    known = {(normalized_author_name(row["canonical"]),
              normalized_author_name(row["alias"])) for row in suggestions}
    values = sorted(identities.values(), key=lambda item: item["key"])
    for index, left in enumerate(values):
        for right in values[index + 1:]:
            if aliases.get(left["key"], left["key"]) == aliases.get(
                    right["key"], right["key"]):
                continue
            shorter, longer = sorted((left, right), key=lambda item: len(item["key"]))
            if len(shorter["key"]) < 5 or shorter["key"] not in longer["key"]:
                continue
            if (shorter["key"], longer["key"]) in known:
                continue
            suggestions.append({
                "canonical": shorter["name"],
                "alias": longer["name"],
                "evidence": "规范化名称存在包含关系，仅供人工确认",
            })
            if len(suggestions) >= MAX_ALIAS_SUGGESTIONS:
                return suggestions
    return suggestions


def _avatar_url(provider: str, ref: str) -> str | None:
    """归档站上的作者头像，经 Peach 取回存在本机再交给页面。

    哪些来源实测拿得到由 `follow_assets.mirror_avatar_url` 一处判定；拿不到的来源
    这里也是 `None`，页面退回作者首字母，不猜一个路径。
    """
    if follow_assets.mirror_avatar_url(provider, ref) is None:
        return None
    return "/follow-avatar?" + urllib.parse.urlencode({"provider": provider, "ref": ref})


def _source_payload(row, aliases: dict[str, str] | None = None) -> dict:
    history_exhausted = _legacy_history_end(row)
    return {
        "id": row["id"],
        "provider": row["provider"],
        "provider_label": PROVIDER_LABELS.get(row["provider"], row["provider"]),
        "ref": row["ref"],
        "label": row["label"],
        # 标签是这条来源在站上的名字，作者名是从它推出来的人名。两者在论坛上差得
        # 很远（`Strauzek Collection [2026-09-04] [Mr_Strauz]` vs `Mr_Strauz`），
        # 而怎么推是站点知识，页面自己再推一遍迟早和这里漂移。
        "author_name": author_display_name(row),
        "author_key": author_key(row, aliases),
        "official_avatar_url": official_avatar_url(row),
        "avatar_url": _avatar_url(row["provider"], row["ref"]),
        "url": row["url"],
        "semantics": row["semantics"],
        "enabled": bool(row["enabled"]),
        "entity_id": row["entity_id"],
        "entity_name": row["entity_name"],
        "can_backfill": row["provider"] in _BACKFILL_PROVIDERS,
        # 往回抓到哪一页了（0 起，0 = 只抓过第一页）。界面据此说清进度，
        # 否则用户点一次只看到列表变长一点，不知道自己走到第几页。
        "backfill_page": row["backfill_page"],
        "created_at": row["created_at"],
        "last_checked_at": row["last_checked_at"],
        "last_status": "not_modified" if history_exhausted else row["last_status"],
        "last_error": None if history_exhausted else row["last_error"],
        "history_exhausted": history_exhausted,
    }


def _legacy_history_end(row) -> bool:
    """把旧版记成 error 的「往回翻到尽头」认回来。

    判据在连接器那一层：哪些状态码代表「没有更早的了」由各连接器声明，
    `is_history_end_error` 拿的是同一份声明。这里不照站点名硬编码中文串比较：
    那样新增一个可回填来源时没人会想到还要改这一处。
    """
    if int(row["backfill_page"] or 0) <= 0 or row["last_status"] != "error":
        return False
    return is_history_end_error(str(row["provider"] or ""),
                                str(row["last_error"] or ""))


def _follow_facets(store, items, by_source, alias_map, icon_root=None) -> dict:
    """筛选条上能选什么。

    必须按全库算而不是按筛后结果——否则选中一个作者之后，作者栏里就只剩他自己，
    再也切不回去。标签同理，按分组而不是按条目计数：一条发布的多个变体是同一件
    作品，标签不该被数三遍。
    """
    authors: set[str] = set()
    providers: set[str] = set()
    tags: dict[str, int] = {}
    works: dict[str, dict] = {}
    for group in store.group(items, group_authors(tuple(by_source.values()), alias_map)):
        row = by_source.get(group.primary.source_id)
        if row is not None:
            key = author_key(row, alias_map)
            if key:
                authors.add(key)
            providers.add(str(row["provider"] or ""))
        for tag in _item_tags(group.primary):
            tags[tag] = tags.get(tag, 0) + 1
        # 末位是「这个题材挑得出代表图吗」。页面据它决定出不出 `<img>`：无条件出图、
        # 靠 `/work-icon` 回 404 换回字母的话，那条响应不可缓存，每次重绘再打一轮。
        icon = 1 if _work_icon_candidate(group.primary) else 0
        for tag in _item_works(group.primary):
            row_work = works.setdefault(_work_root(tag),
                                        {"spellings": {}, "n": 0, "icon": 0})
            row_work["n"] += 1
            row_work["icon"] = max(row_work["icon"], icon)
            row_work["spellings"][tag] = row_work["spellings"].get(tag, 0) + 1
    return {
        "authors": sorted(authors),
        "providers": sorted(providers),
        "tags": sorted(tags.items(), key=lambda pair: (-pair[1], pair[0])),
        # 题材那一排：键是归并后的系列身份，标签是给人看的写法，数目按发布组算，
        # 第四位说挑不挑得出代表图，第五位是那张图检出的人脸取景。
        "works": [[root, _work_display(root, row["spellings"]), row["n"], row["icon"],
                   _work_icon_focus(icon_root, root)]
                  for root, row in sorted(works.items(),
                                          key=lambda pair: (-pair[1]["n"], pair[0]))],
        # 侧栏的时长拉条只在库里有时长读数时出现，同首页 facets 的 `duration`。
        "duration": any((item.duration or 0) > 0 for item in items),
    }


def _author_facets(contract, store, everything, by_source, alias_map, args, scope) -> dict:
    """只按一位作者算的筛选项，创作者资料页的在线视图读它（ADR-0096）。

    `facets` 按全库算，摆到一位创作者的资料页上就混进了别人的标签与来源。这一份只看这位
    名下的条目，同样不随状态、来源、标签筛选收窄，选了一枚还切得回去。`scope` 是
    `(source_id, summary)`：管理页那种只要计数的请求、不是正好一位作者的请求都不算。
    """
    source_id, summary = scope
    authors = _csv_values(args.get("author"))
    if summary or len(authors) != 1:
        return {}
    matches = _follow_matcher({"author": authors[0]}, by_source, alias_map)
    return contract.cached_until_changed(
        f"follow-author-facets:{source_id}:{authors[0]}",
        lambda: _follow_facets(store, tuple(item for item in everything if matches(item)),
                               by_source, alias_map))


def _follow_tag_index(store, items) -> list[dict]:
    """按发布组汇总全部已分类来源标签，供在线标签索引使用。"""
    rows: dict[str, dict] = {}
    type_rank = {"general": 0, "metadata": 1, "copyright": 2,
                 "character": 3, "artist": 4}
    for group in store.group(items):
        grouped: dict[str, tuple[str, str]] = {}
        for item in (group.primary, *group.variants, *group.duplicates):
            for tag in _item_all_tags(item):
                tag_type = _recorded_tag_type(item, tag)
                if not tag_type:
                    continue
                key = tag.casefold()
                previous = grouped.get(key)
                if (previous is None
                        or type_rank[tag_type] > type_rank[previous[1]]):
                    grouped[key] = (tag, tag_type)
        for key, (tag, tag_type) in grouped.items():
            row = rows.setdefault(key, {"k": tag, "n": 0, "cat": tag_type})
            row["n"] += 1
            if type_rank[tag_type] > type_rank[row["cat"]]:
                row["cat"] = tag_type
    return sorted(rows.values(), key=lambda row: (-row["n"], row["k"]))


def q_follow_tags(contract, args) -> dict:
    """在线标签词表，供标签页和左侧抽屉列出关注页那一套标签。

    单独一个端点而不是从 `/api/follow` 里捞：那个载荷还带着来源、别名建议和一整页
    条目，抽屉每次重建都拉一遍不合算。计数与关注页筛选条完全同源——共用
    `_follow_facets`，不是另写一份统计，否则两处迟早对不上。

    返回形状刻意与 `/api/index` 一致（`items` 加 `has_more`）：标签页的分页、搜索
    和「载入更多」都是现成的，换个地址就能用，不必为在线标签再写一套。
    """
    query = str(args.get("q") or "").strip().casefold()
    include_types = str(args.get("types") or "") == "all"
    wanted_type = str(args.get("type") or "").casefold()
    if wanted_type not in {"general", "artist", "character", "copyright", "metadata"}:
        wanted_type = ""
    try:
        limit = max(1, min(int(args.get("limit") or 180), 2000))
    except (TypeError, ValueError):
        limit = 180
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    with contract.database.read_connection() as connection:
        store = _store(contract, connection)
        source_rows = store.sources()
        alias_map, _aliases = store.author_aliases()
        enabled = {int(row["id"]) for row in source_rows if row["enabled"]}
        by_source = {int(row["id"]): row for row in source_rows}
        items = tuple(item for item in store.items(limit=_ALL_ITEMS)
                      if item.source_id in enabled and not _excluded_item(item))
        facets = _follow_facets(store, items, by_source, alias_map,
                                work_icon_root(contract))
        rows = (_follow_tag_index(store, items) if include_types else
                [{"k": tag, "n": count, "cat": "general"}
                 for tag, count in facets["tags"]])
    categories: dict[str, int] = {}
    for row in rows:
        categories[row["cat"]] = categories.get(row["cat"], 0) + 1
    rows = [row for row in rows
            if (not wanted_type or row["cat"] == wanted_type)
            and (not query or query in row["k"].casefold())]
    return {"kind": "tags", "scope": "online",
            "items": rows[offset:offset + limit],
            "has_more": offset + limit < len(rows),
            "categories": categories}


def q_follow_authors(contract, args) -> dict:
    """在线作者索引，供艺人页那一档「在线」列出关注来源里的人。

    和 `q_follow_tags` 同一个道理：形状对着 `/api/index`（`items` 加 `has_more`），
    艺人页现成的分页、过滤和「载入更多」换个地址就能用。

    身份口径不在这里另算一份——`author_key` 加别名表，跟关注页筛选条、跟管理页那份
    名册是同一套判定。同一个人在 Kemono 和 Rule34 上的两条来源在这里是一行，不是两行。

    每一格上那个数跟关注页的读数同一个口径——数的是条目，不是折叠后的发布组。点开一位
    作者去关注页，那里写着「1,294 项更新」，名册上就得是同一个 1,294；两处各按各的口径
    数，用户看到的是两个都对、却对不上的数字。
    头像给的是来源那两条地址，官方优先、归档兜底，跟关注页作者行完全一样。
    """
    query = str(args.get("q") or "").strip().casefold()
    try:
        limit = max(1, min(int(args.get("limit") or 120), 2000))
    except (TypeError, ValueError):
        limit = 120
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    with contract.database.read_connection() as connection:
        store = _store(contract, connection)
        source_rows = store.sources()
        alias_map, aliases = store.author_aliases()
        canonical = {f"name:{row['canonical_key']}": str(row["canonical_name"] or "")
                     for row in aliases}
        enabled = {int(row["id"]) for row in source_rows if row["enabled"]}
        by_source = {int(row["id"]): row for row in source_rows}
        items = tuple(item for item in store.items(limit=_ALL_ITEMS)
                      if item.source_id in enabled and not _excluded_item(item))
        counts: dict[str, int] = {}
        for item in items:
            row = by_source.get(item.source_id)
            key = author_key(row, alias_map) if row is not None else ""
            if key:
                counts[key] = counts.get(key, 0) + 1
        grouped: dict[str, dict] = {}
        for row in source_rows:
            key = author_key(row, alias_map)
            if not key or key not in counts:
                continue
            entry = grouped.setdefault(key, {"k": "", "key": key, "n": counts[key],
                                             "entity_id": None, "avatar": "",
                                             "avatar_fallback": "", "providers": [],
                                             "_rows": []})
            entry["_rows"].append(row)
            if row["entity_id"]:
                entry["entity_id"] = int(row["entity_id"])
            official = official_avatar_url(row)
            mirror = _avatar_url(row["provider"], row["ref"])
            if official and not entry["avatar"]:
                entry["avatar"] = official
            if mirror and not entry["avatar_fallback"]:
                entry["avatar_fallback"] = mirror
            provider = str(row["provider"] or "")
            if provider and provider not in entry["providers"]:
                entry["providers"].append(provider)
        # 和本地某位创作者同名、等人确认的那几位：格子上标出来，点开去那位的资料页确认。
        held = {landing.key: landing.entity_id for landing in follow_creators.plan(connection)
                if landing.action == "hold" and landing.entity_id is not None}
        held_names = {int(row[0]): str(row[1]) for row in connection.execute(
            f"SELECT id,canonical_name FROM entity WHERE id IN ({','.join('?' * len(held))})",
            tuple(held.values()))} if held else {}
        rows = []
        for key, entry in grouped.items():
            entry["held_by"] = held_names.get(held.get(key, 0), "")
            entry["k"] = author_name(entry.pop("_rows"), key, canonical)
            if entry["avatar_fallback"] == entry["avatar"]:
                entry["avatar_fallback"] = ""
            if not entry["avatar"]:
                entry["avatar"], entry["avatar_fallback"] = entry["avatar_fallback"], ""
            rows.append(entry)
        rows.sort(key=lambda row: (-row["n"], row["k"].casefold()))
        rows = [row for row in rows if not query or query in row["k"].casefold()]
    return {"kind": "performers", "scope": "online",
            "items": rows[offset:offset + limit],
            "total": len(rows),
            "has_more": offset + limit < len(rows)}


def _csv_values(value) -> tuple[str, ...]:
    """逗号分隔的查询值，去空、去重、保持顺序。"""
    seen: list[str] = []
    for part in str(value or "").split(","):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return tuple(seen)


#: 关注页那一排能按什么排。`new` 是这一页的默认，也就是 store 给的那个次序。
#:
#: 三列加一档随机。只有这三列，因为只有这三样在每条更新上都成立。观看次数、体积、评分
#: 那几列问的是本机文件，而这一页上的东西多数还没下载；拿一列全空的数字去排序，得到
#: 的是原顺序加一次无意义的洗牌。`rand` 是页面上「换一批」按出来的那一档：按种子打散，
#: 同一粒种子翻页不重不漏，换一粒就是换一批，跟首页 `seeded_order` 同一个算法。
FOLLOW_SORTS = ("new", "hot", "dur", "rand")


def _seed_key(seed: int):
    """按种子打散的排序键，跟 `web_catalog.seeded_order` 同一个式子，只是在 Python 里算。"""
    scale = int(seed or 1) % 99991 or 7
    return lambda item_id: ((item_id * scale) % 99991, item_id)


def _seed_arg(raw) -> int:
    """随机那一档的种子。不是数字就按 1：页面每次「换一批」都自己掷一粒带过来，
    手敲的地址少了它也能开，只是开出来的是哪一批不保证。"""
    text = str(raw or "")
    return int(text) if text.isdigit() else 1


def _item_rank(item, key: str) -> float:
    """这一条在某一列上的值。取不到就是 0，排在那一列的末尾。

    热度取来源自己的分数（rule34 的 `score`）：站点已经按它排过一次热门，本库里
    现成存着。没有这个字段的来源（f95zone 等）一律 0——这一列上它们并列垫底，而不是
    被悄悄按别的东西排了一遍。
    """
    if key == "hot":
        value = item.metadata.get("score")
    elif key == "dur":
        value = item.duration
    else:
        return 0.0
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _sorted_items(items: tuple, sort: str, direction: str, seed: int = 1) -> tuple:
    """按选中的那一列重排整批条目。

    排在分页之前，也排在分组之前：先分页再排等于只排了当前这一屏，翻一页顺序就换
    一套；而 `store.group` 按给进去的次序归并，分完组再排会把同一个作品的几个版本
    拆开。

    `new` 那一档不自己排：store 的 `ORDER BY published_at DESC, id DESC` 已经是它，
    再排一遍只会把并列条目的相对位置打乱。翻转就是倒着读同一串。
    """
    if sort not in FOLLOW_SORTS:
        return items
    if sort == "new":
        return items if direction == "desc" else tuple(reversed(items))
    if sort == "rand":
        rank = _seed_key(seed)
        return tuple(sorted(items, key=lambda item: rank(item.id)))
    # id 兜底与 store 同一个道理：并列值在两次请求间不许换位置，否则翻页会重复或漏掉。
    return tuple(sorted(items, key=lambda item: (_item_rank(item, sort), item.id),
                        reverse=direction == "desc"))


def _sorted_groups(groups: tuple, sort: str, direction: str, seed: int = 1) -> tuple:
    """分完组再排一次。

    `store.group()` 结尾无条件按 `newest_at` 倒序——那是它自己的默认次序，不是这一页
    要的次序。少了这一步，条目那一层的排序就只决定「哪些条目进这一页」，页面上摆出来
    的仍是按时间：选「时长」看到的是一页长片，但它们内部照旧按更新时间排，看着像没生效。
    两层共用同一个 `_item_rank`，否则同一批东西在选页和摆页时会有两种顺序。
    """
    if sort not in FOLLOW_SORTS:
        return groups
    if sort == "new":
        return groups if direction == "desc" else tuple(reversed(groups))
    if sort == "rand":
        rank = _seed_key(seed)
        return tuple(sorted(groups, key=lambda group: rank(group.primary.id)))
    return tuple(sorted(groups,
                        key=lambda group: (_item_rank(group.primary, sort), group.primary.id),
                        reverse=direction == "desc"))


#: 建议分组的名字与先后。顺序在这里定，页面照抄——两侧各排一次的话，改了一侧就会
#: 出现「服务端认为最该先看的组显示在第三位」。
FOLLOW_SUGGEST_GROUPS = (
    ("followed", "已关注"),
    ("archive", "归档站的创作者"),
    ("tag_artist", "Rule34.xxx 的创作者"),
    ("tag", "Rule34.xxx 标签"),
)


def _followed_names(rows, alias_groups) -> dict[str, str]:
    """这台机器已经见过的每一种写法：`casefold → 原样`。

    关注来源上的实体名和作者名、已确认的别名，以及首楼名片上的手柄。同一个人
    常有两个写法（`strauzek` 与 `Mr_Strauz`），记得住哪个是随机的，都摆出来就不必猜。
    """
    names: dict[str, str] = {}

    def add(value) -> None:
        text = str(value or "").strip()
        if text and text.casefold() not in names:
            names[text.casefold()] = text

    for row in rows:
        add(row["entity_name"])
        add(author_display_name(row))
        for link in source_metadata(row).get("official_links") or ():
            if isinstance(link, dict):
                add(link.get("handle"))
    for group in alias_groups:
        add(group.get("canonical_name"))
        for alias in group.get("aliases") or ():
            add(alias.get("name"))
    return names


def q_follow_suggest(contract, q: str, limit: int = MAX_SUGGESTIONS,
                     *, transport=None) -> dict:
    """添加框敲字时的建议：本机见过的写法、本机清单里的创作者、站上的作者与标签。

    每组各自独立成败，和 `discover` 一个道理：清单没下过、站点挂了都只是少一组。
    这条路**不下载任何整站清单**——清单是「查找」那一步顺带下的，敲字只读已经在
    硬盘上的那份。

    rule34.xxx 那边作者本来就是一个标签，差别只在站方给它的分类。分得出来就分成
    两组，分不出来（没凭据、接口没答）就全落在「标签」一组里——那时照实说不知道
    谁是作者，不按名字猜。

    选中一条只是把名字填进查找框。真正逐站问、几十秒那一步仍然由人按回车触发，
    建议不替他决定要关注谁。
    """
    query = suggest_term(q)
    if not query:
        return {"q": str(q or "").strip(), "groups": []}
    per_group = max(1, min(int(limit), MAX_SUGGESTIONS * 2))
    folded = query.casefold()
    with contract.database.read_connection() as connection:
        store = _store(contract, connection)
        _alias_map, alias_groups = store.author_aliases()
        known = _followed_names(store.sources(), alias_groups)
    followed = [text for key, text in sorted(
        ((key, text) for key, text in known.items() if folded in key),
        key=lambda pair: (not pair[0].startswith(folded), len(pair[1]), pair[0]),
    )][:per_group]
    buckets = {
        "followed": [{"value": text, "n": 0, "matched": ""} for text in followed],
        "archive": [{"value": row.value, "n": 0, "matched": row.matched}
                    for row in archive_suggestions(
                        query, state_root=contract.follow_state_root, limit=per_group)
                    # 已经关注的人不在这一组里再出现一次。
                    if row.value.casefold() not in known],
    }
    tags = tag_suggestions(query, transport=transport, limit=per_group,
                           credential=_credential_store(contract).load("rule34xxx"))
    # 归到「作者」那一组的条目不再逐条标一次「作者」——组名已经说过了。留在标签组
    # 的才标，`角色`／`作品` 之间的差别正是这一列存在的理由。
    buckets["tag_artist"] = [{"value": row.value, "n": row.count, "matched": ""}
                             for row in tags if row.tag_type == "artist"]
    buckets["tag"] = [{"value": row.value, "n": row.count, "matched": row.matched}
                      for row in tags if row.tag_type != "artist"]
    return {"q": query, "groups": [
        {"kind": kind, "label": label, "items": buckets[kind]}
        for kind, label in FOLLOW_SUGGEST_GROUPS if buckets[kind]
    ]}


def _page_groups(store, everything, counted, by_author, *, item_id, statuses, order, window):
    """这一次要回的作品组，以及后面还有没有。"""
    if item_id is not None:
        # 直达详情取这一条所在的整组。组是读时拼的（剥作者名、相似标题），只按库里
        # 同键去捞会漏掉别的站上那几份，详情就比列表里的卡片少。
        ranked = tuple(group for group in store.group(everything, by_author)
                       if item_id in {member.id for member in
                                      (group.primary, *group.variants, *group.duplicates)})
        return ranked, False   # 直达详情只取这一组，没有下一页
    # 分页在筛选与分组之后：SQL 先分页、前端再筛的话，选个冷门作者会看到一页里
    # 只剩两三条，得反复点「加载更多」才凑出一屏；按条目切页再分组的话，同一作品
    # 的几份上传落在相邻两页，页面上就是两张卡。所以整批分组，`limit` 数的是组。
    offset, limit = window
    page = tuple(item for item in counted if not statuses or item.status in statuses)
    ranked = _sorted_groups(store.group(page, by_author, everything), *order)
    return ranked[offset:offset + limit], len(ranked) > offset + limit


def _duration_arg(raw) -> float:
    """时长一端的秒数。不是非负数就按不限读：地址是人能手改的，改坏了不该报错。"""
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _follow_matcher(args, by_source, alias_map):
    """关注页筛选条件落到一条条目上：给一条，回它在不在这次筛选里。

    作者、来源、标签、题材都接受逗号分隔的多个值。作者、来源在同一维度内是「任一」：
    选两个作者就看两个人的更新；标签是「同时具备」。时长两端是秒，同首页的
    `dur_min`／`dur_max`，缺省或 0 是这一端不限。
    """
    authors = frozenset(_csv_values(args.get("author")))
    providers = frozenset(_csv_values(args.get("provider")))
    wanted_tags = _csv_values(args.get("tag"))
    # 题材跟作者、来源一样是「任一」：两部作品同时成立的条目几乎没有，取交集等于
    # 点第二枚就清空列表。
    wanted_works = frozenset(_work_root(value) for value in _csv_values(args.get("work")))
    dur_min, dur_max = _duration_arg(args.get("dur_min")), _duration_arg(args.get("dur_max"))

    def _in_duration(item) -> bool:
        # 任一端生效时，没有时长读数的条目（图片、外链帖）不算落在区间里。
        seconds = float(item.duration or 0)
        return seconds > 0 and seconds >= dur_min and not (dur_max and seconds > dur_max)

    def _matches(item) -> bool:
        row = by_source.get(item.source_id)
        if authors and (row is None or author_key(row, alias_map) not in authors):
            return False
        if providers and (row is None or str(row["provider"] or "") not in providers):
            return False
        if wanted_tags:
            # 在线标签索引包含 artist/character/copyright/metadata；点进去也必须
            # 能筛到对应更新，而不是只允许卡片上那份 general 投影。
            # 多个标签取交集：并集会把筛选变成越点越多，跟用户的意图正好相反。
            tags = set(_item_all_tags(item))
            if not all(tag in tags for tag in wanted_tags):
                return False
        if wanted_works and not any(_work_root(tag) in wanted_works
                                    for tag in _item_works(item)):
            return False
        return not (dur_min or dur_max) or _in_duration(item)

    return _matches


def q_follow(contract, args) -> dict:
    statuses = tuple(
        value for value in str(args.get("status") or "").split(",") if value in _STATUSES
    )
    try:
        limit = max(1, min(int(args.get("limit") or 200), 1000))
    except (TypeError, ValueError):
        limit = 200
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    source = args.get("source")
    source_id = int(source) if str(source or "").isdigit() else None
    requested_item = args.get("item")
    item_id = int(requested_item) if str(requested_item or "").isdigit() else None
    # 管理页只要来源、别名与计数。分组与筛选项占这条接口八成的时间，它一样都不用。
    summary = str(args.get("summary") or "") == "1" and item_id is None
    sort = str(args.get("sort") or "new")
    if sort not in FOLLOW_SORTS:
        sort = "new"
    seed = _seed_arg(args.get("seed"))
    # 认不出的方向按这一列的常态读：时间、热度、时长问的都是「最靠前的先看」。
    direction = "asc" if str(args.get("dir") or "") == "asc" else "desc"
    credential_store = _credential_store(contract)
    credential_providers = frozenset(
        provider for provider in CREDENTIAL_GUIDE
        if credential_store.load(provider) is not None
    )
    with contract.database.read_connection() as connection:
        store = _store(contract, connection)
        source_rows = store.sources()
        alias_map, author_aliases = store.author_aliases()
        sources = [_source_payload(row, alias_map) for row in source_rows]
        alias_suggestions = _follow_alias_suggestions(source_rows, alias_map)
        enabled_source_ids = {int(row["id"]) for row in source_rows if row["enabled"]}
        # 作者、来源和标签都不是库里的列：author_key 要经实体绑定、来源 metadata 和
        # 别名映射推导，tags 要从条目 metadata JSON 解析再去噪。在 SQL 里重写这两套
        # 推导等于把同一个语义实现两遍，迟早漂移。所以整库取回来在 Python 里筛——
        # 实测 3054 条连 metadata 解析一起 15ms，不值得为它另设一套索引。
        by_source = {int(row["id"]): row for row in source_rows}
        _matches = _follow_matcher(args, by_source, alias_map)

        # 全部条目和筛选项只随账本与来源参数变，与筛选、排序、分页无关，按账本版本号
        # 缓存：一次请求省下解析上万条 metadata 与整库分组的好几秒。
        everything = contract.cached_until_changed(
            f"follow-items:{source_id}",
            lambda: tuple(item for item in store.items(source_id=source_id, limit=_ALL_ITEMS)
                          if item.source_id in enabled_source_ids and not _excluded_item(item)))
        counted = _sorted_items(
            tuple(item for item in everything if _matches(item)), sort, direction, seed)
        by_author = group_authors(source_rows, alias_map)
        ranked, has_more = ((), False) if summary else _page_groups(
            store, everything, counted, by_author, item_id=item_id, statuses=statuses,
            order=(sort, direction, seed), window=(offset, limit))
        # 翻卡与封面计数要知道组里哪几张是同一个画面，判据与缓存见 `follow_faces`。
        faces = getattr(contract, "follow_faces", None)
        groups = [annotate_group(_group_payload(group, credential_providers), faces,
                                 _content_hashes(group))
                  for group in ranked]
        # 题材圆标的有无与取景读题材头像目录，目录版本进键。
        icon_root = work_icon_root(contract)
        facets = {} if summary else contract.cached_until_changed(
            f"follow-facets:{source_id}",
            lambda: _follow_facets(store, everything, by_source, alias_map, icon_root),
            path_version(icon_root))
        author_facets = _author_facets(contract, store, everything, by_source, alias_map, args,
                                       (source_id, summary))
        # counts 与列表同源，两边都从 `counted` 出发：筛选怎么变，数字就怎么变，
        # 扣减逻辑也只写一份。写成一句全库 SQL 再逐条减掉被隐藏的 rule34video 和
        # 无资源的 f95zone 的话，同一套排除规则要维护两份，而且它不看作者、来源和
        # 标签筛选，筛选一换就是列表变了、药丸上的数字纹丝不动。
        counts: dict[str, int] = {}
        for item in counted:
            status = str(item.status)
            counts[status] = counts.get(status, 0) + 1
    suggestions = _suggestions(contract, sources)
    return {
        "ok": True,
        "sources": sources,
        "author_aliases": author_aliases,
        "alias_suggestions": alias_suggestions,
        "suggestions": suggestions,
        "groups": groups,
        "counts": {status: int(counts.get(status, 0)) for status in _STATUSES},
        # 排序回一份：页面是从 URL 读的，两边对不上时以服务端这份为准。
        "sort": sort,
        "dir": direction,
        "seed": seed,
        "facets": facets,
        "author_facets": author_facets,
        # counts 是全库口径，groups 只是这一页——两个数并排显示过，看起来像自相矛盾。
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "providers": sorted(CONNECTORS),
    }


#: 「猜你喜欢」一次给多少个。这是给人挑的，不是导出全部。
MAX_SUGGESTIONS = 12


def _suggestions(contract, sources) -> list[dict]:
    """「猜你喜欢」取**浏览历史口味分析**产出的创作者候选，按访问次数排序。

    之前两次都取错了源，记下来免得再犯：`facets.creators` 是「他有谁的文件」，
    `location='online'` 的资产是「他关注过谁」，两者都不是「他常搜谁」。真正的信号是
    `scripts/taste_history.py` 从 Chrome/Safari/Zen/Google Takeout 的历史里分析出来的
    `taste-creator-candidates-*.csv`——用户举的两个名字在那里分别排第 1 和第 3
    （`lazyprocrastinator` 28 次、`ffxivinitiala` 13 次）。

    分析没跑过就没有建议，不拿别的数据顶替。
    """
    followed = {str(row["label"]).casefold() for row in sources}
    picked = []
    for row in read_creator_candidates(contract.taste_history_root,
                                       limit=MAX_SUGGESTIONS * 4):
        name = row["name"]
        if name.casefold() in followed:
            continue
        picked.append({"name": name, "visits": row["visits"],
                       "origin": row["sources"]})
        if len(picked) >= MAX_SUGGESTIONS:
            break
    return picked


def _write_item_ids(body) -> list[int]:
    item_id = body.get("item")
    item_ids = body.get("items")
    if isinstance(item_id, int):
        return [item_id]
    if isinstance(item_ids, list) and 0 < len(item_ids) <= 1000:
        if not all(isinstance(value, int) for value in item_ids):
            raise ValueError("items must contain only integer follow item ids")
        return list(dict.fromkeys(item_ids))
    raise ValueError("item must be an integer follow item id or items a non-empty list")


def w_follow_status(contract, body) -> dict:
    item_ids = _write_item_ids(body)
    status = str(body.get("to") or "")
    with contract.database.write_transaction() as connection:
        store = _store(contract, connection)
        for item_id in item_ids:
            store.set_status(item_id, status)
        if status == "ignored":
            # 忽略与「想要」互斥：忽略了的条目撤掉还没入库的想要。
            wants.drop_follow_items(connection, item_ids)
    result = {"ok": True, "items": item_ids, "status": status}
    if len(item_ids) == 1:
        result["item"] = item_ids[0]
    return result


def w_follow_media_hide(contract, body) -> dict:
    """隐藏或恢复一张媒体。

    界面只报它在投影里看到的 `media` 序号；这里换算成跨抓取稳定的媒体键再落库，
    作者中途增删图片也不会让隐藏错位到别的图上。
    """
    item_id = int(body["item"])
    index = int(body["media"])
    hidden = bool(body.get("hidden", True))
    with contract.database.write_transaction() as connection:
        store = _store(contract, connection)
        item = store.item(item_id)
        if item is None:
            raise ValueError(f"no such follow item: {item_id}")
        raw = _raw_media_items(item)
        if not 0 <= index < len(raw) or not isinstance(raw[index], dict):
            raise ValueError(f"media index out of range: {index}")
        key = _media_key(raw[index])
        if not key:
            raise ValueError("this media has no stable identity to hide by")
        hidden_keys = store.set_media_hidden(item_id, key, hidden)
    contract.cache_bust()
    return {"ok": True, "item": item_id, "hidden_media": list(hidden_keys)}


#: 一次回写最多带多少张图。一屏图片墙几十张，翻两屏也远不到这个数；再多就是
#: 请求体不对劲，直接拒。
IMAGE_DIMS_BATCH_LIMIT = 200


def w_follow_image_dims(contract, body) -> dict:
    """界面把加载完的图片固有宽高回写给还没有尺寸的条目。

    连接器不给尺寸的来源（归档站、论坛附件）第一次只能按无尺寸占位；浏览器一旦
    把图读出来就知道 naturalWidth/naturalHeight，回写之后下一次渲染就能预留比例。
    只补空缺，已有尺寸的条目一律不动；条目不存在、媒体序号不在清单里也只是
    不计数，不报错——卡片可能是上一轮渲染留下的。
    """
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise ValueError("entries 必须是列表")
    if len(entries) > IMAGE_DIMS_BATCH_LIMIT:
        raise ValueError(f"一次最多回写 {IMAGE_DIMS_BATCH_LIMIT} 张")
    learned = 0
    with contract.database.write_transaction() as connection:
        store = _store(contract, connection)
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("每一项必须是对象")
            dims = positive_dims(entry.get("width"), entry.get("height"))
            if dims is None:
                raise ValueError("width/height 必须是正整数")
            media = entry.get("media")
            index = int(media) if media is not None else None
            if store.set_image_dims(int(entry["item"]), *dims, media_index=index):
                learned += 1
    if learned:
        contract.cache_bust()
    return {"ok": True, "learned": learned}


def w_follow_play(contract, body) -> dict:
    """记录一次关注页直接播放；候选无需先保存成 asset。"""
    item_id = int(body["item"])
    contract.cache_bust()
    with contract.database.write_transaction() as connection:
        status = _store(contract, connection).record_playback(item_id)
    return {"ok": True, "item": item_id, "status": status}


def w_follow_activity(contract, body) -> dict:
    """累计关注页在线播放的真实时长与最远到达位置。"""
    item_id = int(body["item"])
    contract.cache_bust()
    with contract.database.write_transaction() as connection:
        result = _store(contract, connection).record_playback_activity(
            item_id,
            position=body.get("position", 0), duration=body.get("duration", 0),
            delta=body.get("delta", 0), ended=bool(body.get("ended")))
    return {"ok": True, "item": item_id, **result}


def w_follow_save(contract, body) -> dict:
    item_ids = _write_item_ids(body)
    with contract.database.write_transaction() as connection:
        store = _store(contract, connection)
        asset_ids = [store.save_asset(item_id, confirm=True) for item_id in item_ids]
        # 保存进账本就是入库：想要这一条的标「已入库」（`wants.reconcile` 按 asset_id 认）。
        wants.reconcile(connection, asset_ids)
    result = {"ok": True, "items": item_ids, "asset_ids": asset_ids}
    if len(item_ids) == 1:
        result.update({"item": item_ids[0], "asset_id": asset_ids[0]})
    return result


def w_follow_check(contract, body) -> dict:
    """显式检查更新。这是唯一会向站点发请求的端点。

    没有 `source` 就检查全部已启用来源。逐个来源独立成败：一个来源缺凭据或被
    机器人验证挡住，不该让其余来源的更新一起消失。

    翻页只有 `older` 一个开关：常规检查只看第一页，`older` 从游标往前抓一页，界面上
    「抓更早的一页」就是它。`backfill_all` 与 `rewind` 只跟着 `older` 生效，是存量行
    重抓的运维入口、不进界面：前者一页记完接着抓到尽头，后者把这一轮的起点拨回第
    1 页。连接器新学到一个字段（图片宽高、封面）之后，已经入库的旧行要靠这一组参数
    重走一遍才补得上，做法见 `docs/REUSE.md`「关注检查」一条。
    """
    if "sources" in body and (not isinstance(body["sources"], list)
            or not body["sources"] or not all(type(value) is int for value in body["sources"])):
        raise ValueError("sources must be a nonempty list of source ids")
    if (body.get("backfill_all") or body.get("rewind")) and not body.get("older"):
        # 不带 `older` 时这两个参数什么都不做，默默跑成一轮常规检查只会让人以为重抓过了。
        raise ValueError("backfill_all and rewind only apply together with older")
    finished = threading.Event()
    outcome = {}
    request_id = uuid.uuid4().hex
    def work(job_id):
        try:
            result = _execute_follow_check(contract, body, job_id)
            outcome["result"] = result
            contract.follow_job.update(job_id, **result, status="complete",
                                     current=None, completed_at=time.time())
        except Exception as error:
            outcome["error"] = error
            raise
        finally:
            finished.set()
    # 自动轮询和用户点一下是同一件事的两种发起方式，任务中心按 `trigger` 分开记：
    # 轮询撞上在跑的那一轮记成跳过，手动撞上外部占用则由 API 回 409。
    started = contract.follow_job.start(work, restart=True,
        trigger="scheduled" if body.get("automatic") else "manual",
        recovery={**{key: body[key] for key in ('source', 'sources', 'older', 'backfill_all', 'rewind', 'automatic') if key in body},
                  'background': True},
        initial={"ok": True, "checked": 0, "total": 0, "results": [],
                 "request_id": request_id, "older": bool(body.get("older")), "current": None})
    if body.get("background") or started.get('waiting_for_update'):
        return started
    if started["request_id"] != request_id:
        return {"ok": False, "busy": True, "checked": 0, "results": []}
    finished.wait()
    # `finished` 只说正文跑完了；线程回到 `BackgroundJob` 之后还要给这一轮结算。
    # 同步调用等它落完再回话：否则端点已经答复而账本还在写，紧跟着读活动页会看到
    # 一轮停在「进行中」的检查，关停时也会误判成被打断。
    worker = contract.follow_job.thread
    if worker is not None and worker is not threading.current_thread():
        worker.join(5)
    if "error" in outcome:
        from .jobs import JobPaused
        if isinstance(outcome['error'], JobPaused):
            return contract.follow_job.snapshot() or started
        raise outcome["error"]
    return outcome["result"]


def _execute_follow_check(contract, body, job_id):
    if not contract.follow_check_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "checked": 0, "results": []}
    try:
        return _run_follow_check(contract, body, job_id)
    finally:
        contract.follow_check_lock.release()


def q_follow_check(contract, _args) -> dict:
    """读取检查进度，不发起抓取。"""
    return contract.follow_job.snapshot() or {"ok": True, "status": "idle"}


def _check_writer(contract):
    """给 `follow_check` 用的写事务工厂：每次写一条来源各自提交。"""
    @contextlib.contextmanager
    def writer():
        with contract.database.write_transaction() as connection:
            yield _store(contract, connection)
    return writer


def _backfill_profile_links(row, credentials, writer) -> None:
    """在这条来源的一次常规检查里顺带补上它的名片，只补一次。

    名片是登记时解析的，比它早登记的来源身上没有，而用户不会为了一张头像把
    关注重加一遍。判据是 metadata 里有没有 `official_links` 这个**键**：解析过
    但什么都没找到会写下空清单，那是「问过了，他没留主页」，不该每次检查再问一遍。
    """
    provider = str(row["provider"] or "")
    if provider != "f95zone" or "official_links" in source_metadata(row):
        return
    links = _profile_links(provider, str(row["ref"] or ""),
                           credentials.load(provider))
    if links is None:
        return
    with writer() as store:
        store.merge_source_metadata(row["id"], {"official_links": links})


def _check_payload(result) -> dict:
    """把一次检查结果摊成界面用的载荷。

    站名和来源标签按人看得懂的写法给，不让用户去猜 `rule34xxx` 是哪个站。
    """
    payload = {
        "source": result.source_id, "provider": result.provider,
        "provider_label": PROVIDER_LABELS.get(result.provider, result.provider),
        "ref": result.ref, "label": result.label, "ok": result.ok,
        # 这次读的是第几页，以及往回还剩没剩。界面据此说「已经抓到第 N 页」，
        # 而不是让用户点了一次不知道自己走到哪儿了。
        "page": result.page, "older": result.older,
    }
    if not result.ok:
        payload.update({"status": result.status, "error": result.error})
        return payload
    if result.exhausted:
        payload.update({"exhausted": True, "message": result.message})
        return payload
    fetch, outcome = result.fetch, result.outcome
    payload.update({
        "not_modified": outcome.not_modified, "discovered": outcome.discovered,
        "added": outcome.added, "updated": outcome.updated,
        # 抓到了但判为「不是 release」而丢掉的条数。丢了多少必须说出来，
        # 否则用户分不清少的是被过滤掉的还是根本没抓到——这次问「为什么这么少」
        # 就是因为界面从来没说过这类数字。
        "skipped": fetch.skipped,
        "skipped_compilations": fetch.skipped_compilations,
        "history_skipped": result.history_skipped,
        # 列表判不出来、额外抓了详情页的条数。这是唯一会放大请求数的路径。
        "probed": fetch.probed,
        # 证据没存下来不算检查失败，但界面必须说出来，不能悄悄少一份原始响应。
        "evidence_error": outcome.evidence_error,
        "author_alias_learned": result.author_alias_learned,
    })
    return payload


class _FollowCheckSession:
    """固定来源集合，在每页落地后保存游标和来源结果。"""

    def __init__(self, contract, body, job_id, previous, rows, credentials):
        self.contract, self.body, self.job_id = contract, body, job_id
        self.rows, self.credentials = rows, credentials
        self.results = list(previous.get('results') or [])
        self.pages = dict(previous.get('backfill_pages') or {})
        self.writer = _check_writer(contract)
        self.initial_days = follow_initial_days(contract.database)
        self.cooldown = getattr(contract, 'follow_media_resolver', None)

    def checkpoint(self, **fields):
        if self.job_id and not self.contract.follow_job.checkpoint(self.job_id, **fields):
            raise InterruptedError('关注检查已停止')

    def remember(self, row, result, *, finished):
        if result.get('ok') and isinstance(result.get('page'), int):
            self.pages[str(row['id'])] = result['page']
        if finished:
            result['author'] = author_display_name(row)
            self.results[:] = [item for item in self.results if item['source'] != row['id']]
            self.results.append(result)
        self.checkpoint(backfill_pages=dict(self.pages), results=self.results.copy(),
                        checked=len(self.results), total=len(self.rows))

    def check(self, row):
        current = {'source': row['id'], 'label': author_display_name(row),
                   'provider': PROVIDER_LABELS.get(row['provider'], row['provider'])}
        def progress(**fields):
            if self.job_id:
                self.contract.follow_job.update(self.job_id, total=len(self.rows), checked=len(self.results),
                    results=self.results.copy(), current={**current, **fields})
        progress(attempt=1, max_attempts=5, retry_in=0)
        _backfill_profile_links(row, self.credentials, self.writer)
        older = bool(self.body.get('older'))
        for round_index in range(501):
            if self.job_id and self.contract.follow_job.snapshot() is None:
                raise InterruptedError('关注检查已停止')
            result = _check_payload(run_check(row, credentials=self.credentials, writer=self.writer,
                connector_factory=build_connector, older=older, progress=progress,
                initial_days=self.initial_days, cooldown=self.cooldown))
            more = (older and self.body.get('backfill_all') and result.get('ok')
                    and not result.get('exhausted') and isinstance(result.get('page'), int)
                    and round_index < 500)
            self.remember(row, result, finished=not more)
            if not more:
                break
            row['backfill_page'] = result['page']
            row['metadata_json'] = json.dumps({**json.loads(row.get('metadata_json') or '{}'),
                                              'initial_history_first_page_pending': False})
        progress()


def _run_follow_check(contract, body, job_id=None) -> dict:
    previous = (contract.follow_job.snapshot() or {}) if job_id else {}
    requested = body.get("source")
    source_id = requested if isinstance(requested, int) else None
    # 往回抓一页。这是**显式的、一次一页**的动作：常规检查永远只看第一页，
    # 因为追更关心的是增量；但那也意味着每个来源只有第一页那点内容
    # （rule34video 的作者页一页 24 条，实际 61 页）。用户点一次，往前挪一页。
    older = bool(body.get("older"))
    # rewind：从第 1 页重走一遍。回填游标是单向的（record 只进不退），走到尽头
    # 之后再想全量重抓就得把起点拨回页首；ledger 里的游标由 record 的 max() 守着，
    # 不会因为重走而倒退。补齐标记缺键的行在重走时各自重探一次详情。
    rewind = bool(body.get("rewind"))
    credentials = _credential_store(contract)
    with contract.database.read_connection() as connection:
        rows = plan_check(_store(contract, connection), credentials,
                          source_id=source_id, older=older,
                          backfill_providers=_BACKFILL_PROVIDERS)
    if "sources" in body:
        rows = [row for row in rows if row["id"] in body["sources"]]
    if 'source_ids' in previous:
        rows = [row for row in rows if row['id'] in previous['source_ids']]
    session = _FollowCheckSession(contract, body, job_id, previous, rows, credentials)
    completed = {row['source'] for row in session.results if row.get('ok')}
    if rewind and older:
        for row in rows:
            row["backfill_page"] = session.pages.get(str(row['id']), 0)
    session.checkpoint(source_ids=[row['id'] for row in rows])
    for row in rows:
        if row['id'] in completed:
            continue
        session.check(row)
    # 还没建档的关注作者交给建档后继（ADR-0096）。后继由结果声明、调度端统一派
    # （ADR-0040）；这里判不出来只是这一轮不派，检查本身的结论不受影响。
    try:
        with contract.database.read_connection() as connection:
            followups = follow_creators.declare(connection)
    except sqlite3.Error:
        followups = []
    return {"ok": True, "checked": len(session.results), "results": session.results, "followups": followups}


def q_follow_schedule(contract, _args) -> dict:
    scheduler = contract.follow_scheduler
    if scheduler is None:
        return {"ok": True, "available": False, "enabled": False,
                "interval_minutes": 60, "running": False, "next_run_at": None}
    return scheduler.status()


def w_follow_schedule(contract, body) -> dict:
    scheduler = contract.follow_scheduler
    if scheduler is None:
        raise ValueError("automatic follow updates are unavailable")
    return scheduler.update(
        enabled=body.get("enabled", True),
        interval_minutes=body.get("interval_minutes", 60),
    )


def _resolve_label(contract, parsed, credential) -> str:
    """尽量把标签换成人看得懂的名字。

    kemono 系有 profile 端点，一次请求就能把 `30917150 · fanbox` 换成创作者名；
    取不到就保留从链接推出来的标签，不猜。
    """
    if parsed.provider not in KemonoConnector.HOSTS:
        return parsed.label
    # 走 `build_connector` 而不是直接构造：登记这条路上的连接器全从同一个工厂出来，
    # 测试替换掉工厂就替换掉了全部出网点，不会有一处漏网去真的问 kemono。
    connector = build_connector(parsed.provider, credential=credential)
    service, _, user = parsed.ref.partition("/")
    try:
        response = connector.probe(
            f"https://{connector.host}/api/v1/{service}/user/{user}/profile")
        if response.status != 200:
            return parsed.label
        name = (connector.parse_json(response) or {}).get("name")
    except (FollowSourceError, CredentialError):
        return parsed.label
    return f"{name} · {service}" if name else parsed.label


def _profile_links(provider: str, ref: str, credential) -> list[dict] | None:
    """论坛来源首楼上那张名片；**没能问到**时是 `None`，问到了没有才是空清单。

    线程标题只给一个手柄，首楼才写着这个人在哪几个平台上：头像和别名都指着它。
    但名片是锦上添花——缺 cookie、版块对游客关闭、页面改版都不该让登记本身失败，
    所以这里把失败吞掉。两种空必须分开：空清单会被记进 metadata 当成「问过了，
    他没留主页」，此后不再问；缺 cookie 记成空清单的话，用户后来配好 cookie 也
    永远等不到那张头像了。
    """
    if provider != "f95zone":
        return None
    try:
        connector = build_connector(provider, credential=credential)
        # 读首楼是论坛连接器才有的能力，判据是这个方法在不在，不是站名清单。
        reader = getattr(connector, "thread_profile", None)
        if reader is None:
            return None
        return list(reader(ref)["links"])
    except (FollowSourceError, CredentialError, OSError):
        return None


def _discovery_aliases(body) -> list[str]:
    aliases = body.get("aliases") or []
    if not isinstance(aliases, list) or len(aliases) > MAX_DISCOVERY_ALIASES:
        raise ValueError(f"aliases must be a list of at most {MAX_DISCOVERY_ALIASES} names")
    return [str(alias).strip() for alias in aliases]


def _save_discovery_aliases(store, author_name: str, aliases: list[str]) -> list[dict]:
    """查找结果带来的名片手柄，随登记一步记成检索词的别名。

    人勾选登记这一条就是确认，不再落进「待合并」等人第二次点。自动来源不覆盖
    已有映射，人工改过的分组不动。
    """
    author_key = normalized_author_name(author_name) if author_name else ""
    learned = []
    for alias in aliases:
        if not author_key or normalized_author_name(alias) in {"", author_key}:
            continue
        row = store.upsert_author_alias(author_name, alias, source="profile:f95zone")
        if row is not None:
            learned.append(row)
    return learned


def w_follow_source(contract, body) -> dict:
    """粘一条来源链接就登记，并立刻检查一次。

    登记成功但首次检查失败不算失败：来源已经在列表里，错误显示在它那一行上——
    rule34.xxx 缺 key 就是这种情况，把它整个回滚掉反而让人不知道发生了什么。
    """
    action = str(body.get("action") or "add")
    if action == "remove":
        source_id = body.get("id")
        if not isinstance(source_id, int):
            raise ValueError("id must be an integer follow source id")
        with contract.database.write_transaction() as connection:
            _store(contract, connection).remove_source(source_id)
        return {"ok": True, "removed": source_id}
    if action == "enabled":
        source_id = body.get("id")
        enabled = body.get("enabled")
        if not isinstance(source_id, int) or not isinstance(enabled, bool):
            raise ValueError("id must be an integer and enabled must be a boolean")
        with contract.database.write_transaction() as connection:
            store = _store(contract, connection)
            exists = any(row["id"] == source_id for row in store.sources())
            if not exists:
                raise ValueError("这个关注来源不存在")
            store.set_enabled(source_id, enabled)
        return {"ok": True, "source": source_id, "enabled": enabled}
    if action != "add":
        raise ValueError(f"unknown follow source action: {action}")

    parsed = parse_source_url(str(body.get("url") or ""))
    credentials = _credential_store(contract)
    credential = credentials.load(parsed.provider)
    label = str(body.get("label") or "").strip() or _resolve_label(
        contract, parsed, credential)
    author_name = str(body.get("author") or "").strip()
    author_hint = normalized_author_name(author_name) if author_name else ""
    aliases = _discovery_aliases(body)
    metadata = {"author_key": author_hint} if author_hint else {}
    links = _profile_links(parsed.provider, parsed.ref, credential)
    if links is not None:
        metadata["official_links"] = links
    metadata = metadata or None
    with contract.database.write_transaction() as connection:
        store = _store(contract, connection)
        source_id = store.register(
            provider=parsed.provider, ref=parsed.ref, label=label, url=parsed.url,
            semantics=parsed.semantics, metadata=metadata)
        learned = _save_discovery_aliases(store, author_name, aliases)
    checked = ({"results": []} if body.get("defer_check") else
               w_follow_check(contract, {"source": source_id}))
    outcome = next((row for row in checked["results"]
                    if row["source"] == source_id), None)
    if outcome is None:
        # 另一次检查占着锁（多半是自动任务刚好在跑），所以顺带的首次检查没做。
        # 来源本身已经登记好了，回一个 `checked: null` 会让调用方分不清「查过了
        # 什么都没有」和「根本没查」。这里明确说出来：已登记，稍后会检查。
        outcome = {
            "source": source_id, "provider": parsed.provider,
            "provider_label": PROVIDER_LABELS.get(parsed.provider, parsed.provider),
            "ref": parsed.ref, "label": label, "ok": True, "deferred": True,
            "message": ("已登记，等待检查更新" if body.get("defer_check") else
                        "已登记，另一次检查正在进行，这条稍后再查"),
        }
    return {"ok": True, "source": source_id, "provider": parsed.provider,
            "ref": parsed.ref, "label": label, "checked": outcome,
            "author_aliases_learned": learned}


def _author_updates(store, rows) -> int:
    """几条来源一共多少项更新，与关注页作者读数同一个口径：启用来源、去掉排除项的条目数。"""
    return sum(1 for row in rows if row["enabled"]
               for item in store.items(source_id=int(row["id"]), limit=_ALL_ITEMS)
               if not _excluded_item(item))


def entity_follow(contract, connection, entity_id: int) -> dict | None:
    """创作者资料页上的关注那一块（ADR-0096）：绑在他名下的来源，和同名等确认的那几组。

    都没有就是 `None`，页面不画这一块。`key` 是关注页的作者键，页面拿它去关注页筛这一位。
    """
    store = _store(contract, connection)
    sources = store.sources()
    bound = [row for row in sources if row["entity_id"] == int(entity_id)]
    held = follow_creators.held_for(connection, entity_id)
    if not bound and not held:
        return None
    by_id = {int(row["id"]): row for row in sources}
    official = next((url for row in bound if (url := official_avatar_url(row))), "")
    archive = next((url for row in bound if (url := _avatar_url(row["provider"], row["ref"]))), "")
    return {
        "key": f"entity:{int(entity_id)}" if bound else "",
        "n": _author_updates(store, bound),
        "providers": list(dict.fromkeys(
            PROVIDER_LABELS.get(row["provider"], row["provider"]) for row in bound)),
        "avatar": official or archive,
        "avatar_fallback": archive if official else "",
        "held": [{
            "key": landing.key, "name": landing.name,
            "providers": [PROVIDER_LABELS.get(provider, provider)
                          for provider in landing.providers],
            "n": _author_updates(store, [by_id[source_id] for source_id in landing.source_ids]),
            "links": [link["url"] for link in landing.links],
        } for landing in held],
    }


def w_follow_creator(contract, body) -> dict:
    """资料页上「是同一个人」：把同名等确认的那组关注作者绑到这位创作者名下。"""
    entity_id, key = body.get("entity_id"), str(body.get("key") or "")
    if type(entity_id) is not int or not key.startswith("name:"):
        raise ValueError("entity_id 与 key 都要给")
    with contract.database.write_transaction() as connection:
        written = follow_creators.confirm(connection, entity_id, key)
    contract.cache_bust()
    return {"ok": True, **written}


def w_follow_author_alias(contract, body) -> dict:
    """Add or remove a user-confirmed cross-platform follow-author alias."""
    action = str(body.get("action") or "add")
    if action == "remove":
        alias_name = str(body.get("alias") or "").strip()
        with contract.database.write_transaction() as connection:
            store = _store(contract, connection)
            store.remove_author_alias(alias_name)
            _, groups = store.author_aliases()
        return {"ok": True, "removed": alias_name, "author_aliases": groups}
    if action != "add":
        raise ValueError(f"unknown follow author alias action: {action}")

    canonical_name = str(body.get("canonical") or "").strip()
    alias_name = str(body.get("alias") or "").strip()
    with contract.database.write_transaction() as connection:
        store = _store(contract, connection)
        store.upsert_author_alias(canonical_name, alias_name, source="manual")
        _, groups = store.author_aliases()
    return {"ok": True, "canonical": canonical_name, "alias": alias_name,
            "author_aliases": groups}


#: 一次最多解析多少行。粘一屏链接是正常的，粘一整个书签导出不是。
MAX_RESOLVE_LINES = 40
#: 登记一条来源时最多随带写下几个名片别名；名片本身最多认八个身份。
MAX_DISCOVERY_ALIASES = 8


def _candidate_payload(candidate, *, author: str = "") -> dict:
    return {"provider": candidate.provider,
            "provider_label": PROVIDER_LABELS.get(candidate.provider, candidate.provider),
            "ref": canonical_source_ref(candidate.provider, candidate.ref),
            "url": candidate.url, "label": candidate.label, "author": author,
            "aliases": list(getattr(candidate, "aliases", ())),
            "semantics": candidate.semantics, "evidence": candidate.evidence}


def _external_search_payload(search) -> dict:
    return {"provider": search.provider,
            "provider_label": PROVIDER_LABELS.get(search.provider, search.provider),
            "label": search.label, "query": search.query,
            "url": search.url, "evidence": search.evidence}


def _resolve_follow_line(contract, line, known, on_progress):
    """解析一行链接或检索词，并标记已登记来源。"""
    try:
        parsed = parse_source_url(line)
    except FollowSourceError as url_error:
        if '://' in line or '/' in line:
            return {'line': line, 'kind': 'error', 'error': str(url_error)}
        try:
            found = discover(line, secrets_root=contract.follow_secrets_root,
                             shared_root=contract.follow_shared_root,
                             state_root=contract.follow_state_root, on_progress=on_progress)
        except (FollowSourceError, CredentialError) as term_error:
            return {'line': line, 'kind': 'error', 'error': str(term_error)}
        return {'line': line, 'kind': 'term',
                'candidates': [{**_candidate_payload(c, author=line),
                                'known': (c.provider, canonical_source_ref(c.provider, c.ref)) in known}
                               for c in found.candidates],
                'failures': found.failures,
                'external_searches': [_external_search_payload(search) for search in found.external_searches]}
    return {'line': line, 'kind': 'url',
            'candidates': [{**_candidate_payload(parsed),
                            'known': (parsed.provider, canonical_source_ref(parsed.provider, parsed.ref)) in known}]}


def w_follow_resolve(contract, body, *, progress=None, checkpoint=None, resume_state=None) -> dict:
    """把粘进来的每一行解析成「可以添加什么」，但**不添加**。

    一行是链接就直接认；不是链接就当成名字或 id 拿去各来源查一遍。两种都只返回结果，
    由人勾选之后再调 `/api/follow/source` 落地——发现要联网，结果也可能不止一个，
    自动登记等于替用户做决定。
    """
    if body.get("background"):
        job = contract.follow_resolve_job
        def work(job_id):
            result = w_follow_resolve(contract, {**body, "background": False},
                progress=lambda **fields: job.update(job_id, **fields),
                checkpoint=lambda **fields: job.checkpoint(job_id, **fields), resume_state=job.snapshot())
            job.update(job_id, **result, status="complete", completed_at=time.time())
        return job.start(work, restart=True, initial={"message": "正在准备查找关注来源"},
                         recovery={'lines': body.get('lines'), 'background': True})
    raw = body.get("lines")
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        raise ValueError("lines must be a list of strings")
    lines = [str(line).strip() for line in raw if str(line).strip()]
    if not lines:
        raise ValueError("没有可解析的内容")
    if len(lines) > MAX_RESOLVE_LINES:
        raise ValueError(f"一次最多解析 {MAX_RESOLVE_LINES} 行，收到 {len(lines)} 行")

    known = {(row["provider"], canonical_source_ref(row["provider"], row["ref"])) for row in
             (_source_payload(r) for r in _existing_sources(contract))}

    # 进度按来源计，不按行计：一行检索词背后是十几个来源各查各的，
    # 行数当分母只会整轮卡在 0% 然后跳满。计划先算一遍（不联网），
    # 每行链接算 1 个单位，检索词行按 discovery_plan 的来源数算。
    def plan_units(line: str) -> int:
        try:
            parse_source_url(line)
            return 1
        except FollowSourceError:
            if "://" in line or "/" in line:
                return 0
            return len(discovery_plan(line))

    total_units = sum(plan_units(line) for line in lines)
    done_units = 0

    def source_progress(line_index: int):
        def note(provider: str, position: int, plan_total: int) -> None:
            if progress is not None:
                label = PROVIDER_LABELS.get(provider, provider)
                progress(
                    checked=done_units + position, total=total_units,
                    message=f"查找关注来源：来源 {done_units + position + 1}/{total_units} · {label}"
                            + (f"（第 {line_index + 1}/{len(lines)} 行）" if len(lines) > 1 else ""))

        return note

    results = list((resume_state or {}).get('results') or [])
    completed = {row['line'] for row in results if row.get('kind') != 'error' and not row.get('failures')}
    for index, line in enumerate(lines):
        # 分母是 plan_units 算出来的，分子就照同一把尺子加：一行查失败也占掉了它那
        # 几个来源，不加进去的话后面每一行都低这么多，环最后停在半路。
        if line not in completed:
            result = _resolve_follow_line(contract, line, known, source_progress(index))
            results = [row for row in results if row['line'] != line]
            results.append(result)
            if checkpoint and not checkpoint(results=results.copy(), checked=done_units + plan_units(line), total=total_units):
                raise InterruptedError('关注来源查找已停止')
        done_units += plan_units(line)
    if progress is not None and total_units:
        # 每个来源是开查前报一次自己的序号，最后一个报的是 total-1；收尾这一下才走满。
        progress(checked=total_units, total=total_units,
                 message=f"查找关注来源：{total_units} 个来源已查完")
    return {"ok": True, "results": results}


def _existing_sources(contract):
    with contract.database.read_connection() as connection:
        return _store(contract, connection).sources()


def w_follow_credential(contract, body) -> dict:
    """保存一个来源的凭据，写到**运行 Peach 的那台机器**的 secrets 目录。

    值只从请求体流向磁盘，**不回显、不记日志、不进任何返回体**。只接受
    `CREDENTIAL_GUIDE` 里声明过的 provider 和字段名——多余的字段一律拒绝，
    免得把任意内容写进 secrets 目录。

    **权限收紧只在 POSIX 上真的发生。** Windows 的 `os.chmod` 只能拨动只读位，
    NTFS 的权限走 ACL，落盘就是继承来的 0o666——所以那里不假装收紧过，
    `describe()` 的 `world_readable` 也照实报 `None`。要在 Windows 上真正收紧
    得走 ACL（icacls/pywin32 断继承），那需要在那台机器上实测验证才能落地，
    当前**未取得**，不写没验过的安全代码。
    """
    provider = str(body.get("provider") or "")
    guide = CREDENTIAL_GUIDE.get(provider)
    if guide is None or not guide.get("fields"):
        raise ValueError(f"{provider or '(空)'} 不接受凭据")
    values = body.get("values")
    if not isinstance(values, dict):
        raise ValueError("values must be an object")
    allowed = set(guide["fields"])
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"{provider} 不认识这些字段：{', '.join(unknown)}")
    cleaned = {name: str(values[name]).strip() for name in guide["fields"]
               if str(values.get(name) or "").strip()}
    store = _credential_store(contract)
    path = store.path_for(provider)
    if not cleaned:
        # 全部留空 = 撤掉这份凭据。**本机和共享副本一起撤**：只删本机的话，
        # `load()` 会从共享副本把 key 重新拼回来，而共享副本还会跟着 peach-sync
        # 传到另一台，变成哪台都撤不掉。共享盘不在就如实说，不静默跳过。
        outcome = store.clear(provider)
        return {"ok": True, "provider": provider, "cleared": True,
                "shared_cleared": outcome["shared"],
                "note": CLEAR_NOTES.get(str(outcome["shared"]), ""),
                "saved": store.describe(provider)}
    _write_secret(path, cleaned)
    shared_written = _write_shared(store, provider, cleaned)
    # 返回的是 describe()，只有字段名，没有值。
    return {"ok": True, "provider": provider, "cleared": False,
            # 界面据此说明「这台机器上没有收紧文件权限」，而不是默认收紧过。
            "permissions_tightened": os.name != "nt",
            "synced": shared_written,
            "saved": store.describe(provider)}


#: 撤销后要不要多说一句。共享副本删掉了或压根没有都不必打扰用户；
#: 只有「盘不在、这次只撤掉了本机」必须说出来，否则用户以为撤干净了。
CLEAR_NOTES = {
    "offline": "共享盘不在，只撤掉了本机这份。等盘回来要再撤一次，否则会被同步回来。",
}


def _write_secret(path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    if os.name != "nt":
        os.chmod(temporary, 0o600)
    temporary.replace(path)


def _write_shared(store, provider: str, values: dict) -> bool:
    """把**声明为可同步的**字段写一份到共享副本。

    只写声明过的字段：共享副本会跟着 `peach-sync` 走 SMB 并进备份，把不该出去的
    东西写进去就再也收不回来。共享盘不可达时静默跳过——凭据在本机已经存好了，
    同步失败不该让保存失败。
    """
    shared = store.shared_path_for(provider)
    syncable = set(store.syncable(provider))
    if shared is None or not syncable:
        return False
    payload = {name: value for name, value in values.items() if name in syncable}
    if not payload:
        return False
    try:
        if not store.shared_online():
            return False
        _write_secret(shared, payload)
    except OSError:
        return False
    return True


def _credential_store(contract) -> CredentialStore:
    return credential_store_for(contract.follow_secrets_root,
                                shared_root=contract.follow_shared_root)


def q_follow_credentials(contract, _args) -> dict:
    """报告凭据状态和怎么配。只给字段名与文件路径，绝不返回凭据值。"""
    store = _credential_store(contract)
    providers = []
    for provider in sorted(set(CONNECTORS) | set(CREDENTIAL_GUIDE)):
        described = store.describe(provider)
        guide = CREDENTIAL_GUIDE.get(provider, {"requirement": "none"})
        fields = guide.get("fields", [])
        providers.append({
            **described,
            "provider_label": PROVIDER_LABELS.get(provider, provider),
            "followable": provider in CONNECTORS,
            "requirement": guide["requirement"],
            "needs": fields,
            "missing": [name for name in fields if name not in described["fields"]],
            "why": guide.get("why", ""),
            "where": guide.get("where", ""),
            "howto": guide.get("howto", ""),
            "path": str(store.path_for(provider)),
            "example": (json.dumps({name: "…" for name in fields}, ensure_ascii=False)
                        if fields else ""),
        })
    return {"ok": True, "root": str(store.root), "providers": providers}
