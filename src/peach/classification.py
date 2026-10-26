from __future__ import annotations

import re
import unicodedata
from functools import cache

from .settings_file import PROJECT_ROOT

STRUCTURAL_WORDS_FILE = PROJECT_ROOT / "resources" / "naming" / "structural_directory_words.txt"

_EPISODE = re.compile(r"(?<![A-Za-z0-9])S\d{1,2}E\d{1,3}(?!\d)", re.IGNORECASE)
_MAINSTREAM_RELEASE = re.compile(
    r"WEB[ ._-]?(?:DL|Rip)|HDTV|BluRay|AppleTor|\[rartv\]",
    re.IGNORECASE,
)
_MONTH = r"(?:\d{4}[年._-]?)?(?:0?[1-9]|1[0-2])月"
_QUALITY = r"(?:[2468]k|\d{3,4}[pi])"
_SEPARATORS = re.compile(r"[\s_\-.,，、&+·|/\\()（）\[\]【】「」『』《》<>@!！?？~～:：;；'\"]+")
_FILLER = re.compile(rf"\d+(?:mm|cm|gb|g|tb|v|p|部|集|个)?|{_MONTH}|{_QUALITY}|[a-z]", re.IGNORECASE)
_SCRIPT_RUNS = re.compile(r"[a-z0-9]+|[^a-z0-9]+")


def _fold(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


@cache
def structural_vocabulary() -> frozenset[str]:
    """标签词表、来源 genre 词表与 `resources/naming` 结构词共同构成目录词汇。

    单字词（`海`、`生`、`足`）在连写切分中会拼出普通人名，不收。
    """
    from .catalog_rules import (APPEARANCE_TAGS, ATTRIBUTE_TAGS, POSITION_TAGS, RELATIONSHIP_TAGS,
                                RETIRED_TAGS, ROLE_TAGS, SCENE_TAGS, STORY_TAGS, TECH_TAGS)
    from .genre_taxonomy import CONTENT_GENRES
    words = set()
    for group in (APPEARANCE_TAGS, ATTRIBUTE_TAGS, POSITION_TAGS, RELATIONSHIP_TAGS,
                  ROLE_TAGS, SCENE_TAGS, STORY_TAGS, TECH_TAGS, RETIRED_TAGS, CONTENT_GENRES):
        words.update(group)
    words.update(CONTENT_GENRES.values())
    for line in STRUCTURAL_WORDS_FILE.read_text(encoding="utf-8").splitlines():
        word = line.split("#", 1)[0].strip()
        if word:
            words.add(word)
    return frozenset(folded for folded in map(_fold, words) if len(folded) > 1)


@cache
def _longest_word() -> int:
    return max(map(len, structural_vocabulary()))


def _segments_into_vocabulary(text: str, vocabulary: frozenset[str]) -> bool:
    """连写的中日韩词组整段切成词表词；任何残余都说明名字里还有身份成分。"""
    longest = min(len(text), _longest_word())
    reachable = [True] + [False] * len(text)
    for end in range(1, len(text) + 1):
        reachable[end] = any(reachable[start] and text[start:end] in vocabulary
                             for start in range(max(0, end - longest), end))
    return reachable[-1]


def is_structural_creator(name: str | None) -> bool:
    """整名只由题材、体位、画质、月份与合集注记构成时，是结构目录而不是账号。

    拉丁字母段必须整段命中词表，不在段内拆词，避免把英文账号名拆成题材词。
    """
    folded = _fold(name)
    if not folded:
        return False
    vocabulary = structural_vocabulary()
    if folded in vocabulary or re.fullmatch(_MONTH + "|" + _QUALITY, folded):
        return True
    hits = 0
    for token in filter(None, _SEPARATORS.split(folded)):
        if token in vocabulary:
            hits += 1
            continue
        if _FILLER.fullmatch(token):
            continue
        for run in _SCRIPT_RUNS.findall(token):
            if run in vocabulary:
                hits += 1
            elif _FILLER.fullmatch(run):
                continue
            elif re.fullmatch(r"[a-z0-9]+", run) or not _segments_into_vocabulary(run, vocabulary):
                return False
            else:
                hits += 1
    return hits > 0


def is_repost_creator(name: str | None) -> bool:
    """已登记转载渠道的域名水印，不按任意域名否定账号。"""
    from .catalog_rules import REPOST_SITE_LABELS
    match = re.match(r'^\[?(?:www\.)?([a-z0-9]+)\.(?:com|me|la|tv|xyz|cc)(?:$|[^a-z])',
                     str(name or '').strip(), re.I)
    return bool(match and match[1].casefold() in REPOST_SITE_LABELS)


_COLLECTION_SUFFIX = re.compile(
    r'(?:\s*(?:4k|6k|8k|2160p|1080p|720p|去重版|整合用|合集|合辑|全集)'
    r'|\s*(?:20\d{2}[.年_-](?:0?[1-9]|1[0-2])(?:月)?'
    r'|(?:0?[1-9]|1[0-2])月|20\d{2}年[一二三四五六七八九十]+月'
    r'|\d{4}\s*[一二三四五六七八九十]+月)'
    r'|(?:\s+|最新)\d+v(?:\s+\d+(?:\.\d+)?\s*(?:gb|g|tb))?'
    r'|\s+v\d+(?:\s+\d+(?:\.\d+)?\s*(?:gb|g|tb))?'
    r'|\s+\d+(?:\.\d+)?\s*(?:gb|tb)'
    r'|\s*[(（]\d+[)）])$', re.I)


def creator_collection_base(name: str) -> str:
    """剥离集合的月份、画质、份数与合集后缀；调用方须核对已有账号身份。"""
    base = str(name)
    while True:
        stripped = _COLLECTION_SUFFIX.sub('', base).strip()
        if stripped == base or not stripped:
            return base.strip()
        base = stripped


SITE_RELEASES = {'legsjapan': 'https://www.legsjapan.com/en/',
                'fellatiojapan': 'https://www.fellatiojapan.com/en/'}


def _site_release(value: str) -> str:
    text = re.sub(r'^\[[^\]]+\]', '', str(value)).strip().lower()
    text = re.sub(r'^\d+[-_ ]+(?=(?:legs|fellatio)japan)', '', text)
    text = re.sub(r'\.(?:mp4|mkv|avi|wmv|mov|jpg|png|webp)$', '', text)
    match = re.search(r'(?<![a-z])(legsjapan|fellatiojapan)[-_](\d{3,6})(?=$|[-_. @])', text)
    return f'{match[1]}-{int(match[2])}' if match else ''


def creator_release_identifier(creator: str, *, path: str, filename: str, code: str | None) -> tuple[str, str]:
    """审计与摄取共用文件级发行证据；账号形似番号并不足以拒绝身份。"""
    from .catalog_rules import release_code_from_text, is_jav_code, normalise_code_key
    from .code_creators import SETTLED_VERDICTS, classify as classify_code
    from .sources.dmm import matching_cids
    verdict, _, reason = classify_code(creator,[dict(path=path,name=filename,code=code)])
    if verdict in SETTLED_VERDICTS:
        return 'release_identifier', f'{reason}；只移除创作者投影，番号不改写'
    site = _site_release(creator)
    site_path = any(_site_release(part) == site for part in re.split(r'[\\/]',path)) if site else False
    numbered_file = re.match(r'^(\d{3,6})[-_]',filename)
    if site and (site == _site_release(filename) or
                 (site_path and numbered_file and int(numbered_file[1]) == int(site.split('-')[1]))):
        return 'release_identifier', f'目录与媒体文件同为站点作品号 {site}；官网目录 {SITE_RELEASES[site.split("-")[0]]}'
    versionless = re.sub(r'[-_](?:[468]k|hd)(?:[-_](?:c|ch))?$', '', creator, flags=re.I)
    identity = release_code_from_text(versionless)
    if (versionless != creator and identity and is_jav_code(identity)
            and normalise_code_key(versionless) == normalise_code_key(identity)
            and creator.casefold() in [part.casefold() for part in re.split(r'[\\/]', path)]):
        return 'release_identifier', f'目录名是发行标识 {identity} 与画质版本后缀；不作为账号，也不推断目录内文件的番号'
    ppv = re.fullmatch(r'gachincoppv[-_](\d{3,6})[-_](?:hd|fhd|[468]k)', creator, re.I)
    if ppv and re.fullmatch(re.escape(creator) + r'(?:[-_]?\d+)?\.[a-z0-9]+', filename, re.I):
        return 'release_identifier', '目录与分段文件使用 Gachinco PPV 作品号及画质后缀；不作为账号'
    if identity and is_jav_code(identity) and re.search(
            r'(?<![a-z0-9])' + re.escape(versionless) + r'(?:[-_](?:[468]k|hd)\d?(?:[-_]c)?)?\.[a-z0-9]+$',filename,re.I):
        return 'release_identifier', f'目录与媒体文件的发行标识 {identity} 一致；画质与版本后缀不作为账号'
    product = re.fullmatch(r'([a-z]{2,8})(\d{5})(?:hhb|mhb|dmb|dm|hq|sd|hd|pl)',creator,re.I)
    if product and re.fullmatch(re.escape(product[1]+product[2])+r'(?:\.part\d+)?\.[a-z0-9]+',filename,re.I):
        identity = normalise_code_key(product[1]+'-'+product[2])
        if is_jav_code(identity):
            return 'release_identifier', f'目录商品标识与媒体文件 {product[1]+product[2]} 一致；番号不改写'
    identifier = release_code_from_text(re.sub(r'_\d{3}$', '', creator))
    cid = re.sub(r'_\d{3}$', '', re.sub(r'\.[^.]+$', '', filename))
    if identifier and re.fullmatch(r'[a-z]+\d{5}(?:hhb|mhb|dmb|dm|hq|sd|hd|pl)',cid,re.I) and matching_cids(identifier,[cid]):
        return 'release_identifier', f'目录番号 {identifier} 与文件 DMM 商品标识一致；番号不改写'
    return ('identifier_candidate',reason) if verdict == '存疑' else ('','')


_JP_NAME = r'[぀-ヿ一-鿿々ー・]{2,10}'
_ROMAN_NAME = r'[A-Z][a-z]+(?: [A-Z][a-z]+){1,2}'
_CAMEL_NAME = r'[A-Z][a-z]+[A-Z][a-z]+'
#: `こすっち238 宇流木さら`：固定前缀紧跟编号，后面只有一个人名。
_PREFIX_NUMBER_CAST = re.compile(r'^(?P<prefix>[^\d\s\[\]【】()（）]{2,}?)[\s_-]*(?P<number>\d{2,4})[\s_-]+'
                                 r'(?P<cast>' + _JP_NAME + '|' + _ROMAN_NAME + r')$')
#: `Akari Asagiri & Eri Hirasawa - 120 - Sensual Kiss`：出演者组合、编号、标题三段。
_CAST_NUMBER_TITLE = re.compile(r'^(?P<cast>.+?)\s+-\s+(?P<number>\d{2,4})\s+-\s+\S.*$')
#: `ChieKobayashi-ShinoAoi-039-1080p`：驼峰人名连写、编号、可选画质。
_CAMEL_CAST_NUMBER = re.compile(r'^(?P<cast>' + _CAMEL_NAME + r'(?:-' + _CAMEL_NAME + r')+)-(?P<number>\d{2,4})(?:-.*)?$')
_CAST_SPLIT = re.compile(r'\s*(?:&|,|、| and )\s*')
#: 至少几部、几位出演者，命中文件占比，以及固定成员出现在多大比例的命中文件里就算个人或情侣账号。
LABEL_SHAPE_FILES, LABEL_SHAPE_CAST, LABEL_SHAPE_SHARE, LABEL_SHAPE_FIXED_MEMBER = 3, 3, 0.5, 0.8


def _cast_key(name: str) -> str:
    return re.sub(r'[\s・]', '', unicodedata.normalize('NFKC', name)).casefold()


def _numbered_cast(filename: str) -> tuple[str, str, int, list[str]] | None:
    """一个文件名 → (形态, 前缀, 编号, 出演者)；不是这几种发行命名返回 None。"""
    text = unicodedata.normalize('NFKC', re.sub(r'\.[A-Za-z0-9]{2,4}$', '', filename)).strip()
    text = re.sub(r'\s*\(\d+\)$', '', text)
    found = _PREFIX_NUMBER_CAST.match(text)
    if found:
        return 'prefix', found['prefix'].casefold(), int(found['number']), [found['cast']]
    found = _CAST_NUMBER_TITLE.match(text)
    if found:
        cast = _CAST_SPLIT.split(found['cast'])
        if all(re.fullmatch(_JP_NAME + '|' + _ROMAN_NAME, name) for name in cast):
            return 'cast', '', int(found['number']), cast
    found = _CAMEL_CAST_NUMBER.match(text)
    if found:
        return 'cast', '', int(found['number']), found['cast'].split('-')
    return None


def numbered_cast_shape(filenames: list[str], people: set[str]) -> dict | None:
    """同一账号名下的发行命名轮换出演者时，交回疑似厂牌或系列的依据；只供复核。

    认三种命名：「前缀+编号+出演者」、「出演者 & 出演者 - 编号 - 标题」、驼峰人名连写加编号。
    前缀形态的尾段可能是标题，所以至少三位出演者要是账本已有的出演者名或别名（`people`
    存 `normalize_entity_name` 后的写法）；另外两种由分隔结构标出人名位置。
    同一位出演者出现在绝大多数命中文件里的是个人或情侣账号，不报。
    """
    from .entities import normalize_entity_name
    groups: dict[tuple[str, str], list[tuple[int, list[str]]]] = {}
    for filename in filenames:
        shaped = _numbered_cast(filename)
        if shaped:
            groups.setdefault(shaped[:2], []).append(shaped[2:])
    if not groups:
        return None
    (form, prefix), matched = max(groups.items(), key=lambda item: len(item[1]))
    appearances: dict[str, int] = {}
    known = set()
    for _number, cast in matched:
        for key, name in {_cast_key(name): name for name in cast}.items():
            appearances[key] = appearances.get(key, 0) + 1
            if normalize_entity_name(name) in people:
                known.add(key)
    numbers = {number for number, _cast in matched}
    if (len(matched) < LABEL_SHAPE_FILES or len(matched) < LABEL_SHAPE_SHARE * len(filenames)
            or len(numbers) < LABEL_SHAPE_FILES or len(appearances) < LABEL_SHAPE_CAST
            or (form == 'prefix' and len(known) < LABEL_SHAPE_CAST)
            or max(appearances.values()) >= LABEL_SHAPE_FIXED_MEMBER * len(matched)):
        return None
    return {'form': form, 'prefix': prefix, 'files': len(matched), 'total': len(filenames),
            'cast': len(appearances), 'known_cast': len(known)}


def is_probable_mainstream_release(name: str | None, path: str | None = None) -> bool:
    """Return only strong TV-release candidates; callers must still review them."""
    text = " ".join(part for part in (name, path) if part)
    return bool(_EPISODE.search(text) and _MAINSTREAM_RELEASE.search(text))
