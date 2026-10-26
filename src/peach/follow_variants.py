"""追更条目的变体判定：WIP、alt 版本与跨站重复项。

判据只看标题文本与来源提供的版本字段，不联网、不读数据库，也不写任何文件。
调用方拿到 `VariantVerdict` 后自行决定是否入库。

两条刻意保守的边界：

- **括号只在命中已知标记、创作者别名、版本模式或日期模式时才剥离。** 例如
  `Episode 5 [Anilingus, Light BDSM]` 的括号是标签列表，不是变体标记，保留在
  `release_key` 里。误合两个不同作品比多出一张卡片糟糕得多。日期括号
  （`[2026-09-01]`）剥掉但不当版本：它是合集的更新批次标签，放进版本位会被
  读成卡片上的第二个发布日期。
- **标题末尾的裸数字算作品序号，不算版本。** `Sayuri - Cowgirl 2` 与
  `Sayuri - Cowgirl` 是两个作品；只有显式的 `v2`/`version 2` 才判为同一作品的另一版。
"""
from __future__ import annotations

from . import follow_providers
import difflib
import re
import unicodedata
from dataclasses import dataclass


# 括号内命中即剥离；裸词只认 `_BARE_MARKERS` 里的子集，避免把
# `Sound of Music` 这类正常标题词误判成变体标记。
_WIP_MARKERS: tuple[tuple[str, str], ...] = (
    (r"w\.?i\.?p\.?\d*", "WIP"),
    (r"work[\s\-]?in[\s\-]?progress", "WIP"),
    (r"preview", "preview"),
    (r"teaser", "teaser"),
    (r"sneak[\s\-]?peek", "sneak peek"),
    (r"unfinished", "unfinished"),
    (r"animatic", "animatic"),
    (r"rough[\s\-]?(?:cut|draft)?", "rough"),
    (r"draft", "draft"),
    (r"test[\s\-]?render", "test render"),
    (r"progress[\s\-]?(?:update|report)", "progress"),
)

_ALT_MARKERS: tuple[tuple[str, str], ...] = (
    (r"(?:no|un)[\s\-]?water[\s\-]?mark(?:ed)?|no[\s\-]?wm", "no watermark"),
    (r"water[\s\-]?mark(?:ed)?", "watermarked"),
    (r"nude|naked|topless", "nude"),
    (r"clothed|dressed|clothes[\s\-]?on", "clothed"),
    (r"uncensored|decensored", "uncensored"),
    (r"censored", "censored"),
    (r"no[\s\-]?sound|muted?|silent", "no sound"),
    (r"with[\s\-]?sound|sound[\s\-]?version|w/[\s\-]?sound|audio", "sound"),
    (r"(\d{3,4})p", "{0}p"),
    # 分辨率常写成不带 `p` 的裸数字：`2B Camp [4K]` 与 `2B Camp [1080]` 是同一部片的
    # 两个版本，而 `[1080]` 此前留在 release_key 里，两张卡永远合不到一起。只认已知
    # 分辨率档位，不认任意三四位数——那可能是年份或作品序号。
    (r"(480|720|1080|1440|2160|4320)", "{0}p"),
    (r"([248])k(?![a-z])", "{0}K"),
    (r"(\d{2,3})[\s\-]?fps", "{0}fps"),
    (r"alt(?:ernat(?:e|ive))?(?:[\s\-]?(?:version|ver|angle|ending))?", "alt"),
    (r"v(?:er(?:sion)?[\s\-]?)?([2-9]\d*)", "v{0}"),
    (r"re[\s\-]?(?:make|master(?:ed)?|render(?:ed)?|upload)", "remake"),
    (r"loop(?:ing)?", "loop"),
    (r"x[\s\-]?ray", "x-ray"),
    (r"futa(?:nari)?", "futa"),
    (r"p\.?o\.?v\.?", "pov"),
    (r"vertical|portrait", "vertical"),
    (r"extended(?:[\s\-]?cut)?|full[\s\-]?(?:version|length)", "extended"),
)

_BARE_MARKERS = frozenset({
    "WIP", "preview", "teaser", "alt", "nude", "uncensored", "censored",
    "no watermark", "remake", "loop",
})

# 裸词还额外放行这几类形态无歧义的标记：`v2`、`1080p`、`60fps`、`4K`。
# 它们不可能是固定词表，只能按形态判。
_BARE_SHAPES = re.compile(r"v\d+|\d{3,4}p|\d{2,3}fps|[248]K")
_VIDEO_SPEC_RE = re.compile(r"([248]k|\d{3,4}p)[\s\-]*(\d{2,3})[\s\-]*fps", re.IGNORECASE)


def video_spec_markers(text: str) -> tuple[str, ...]:
    """清晰度与帧率连写的一组版本标记。"""
    matched = _VIDEO_SPEC_RE.fullmatch(text.strip())
    if not matched:
        return ()
    resolution, rate = matched.groups()
    return (resolution.upper() if resolution.lower().endswith("k") else resolution.lower(), f"{rate}fps")


def _bare_marker_allowed(label: str) -> bool:
    return label in _BARE_MARKERS or _BARE_SHAPES.fullmatch(label) is not None

# `release` 语义下从标题里摘出的版本；`work` 语义下不识别，交给 alt 标记处理。
# 日期不算版本：`[2026-09-01]` 是合集的更新批次标签，不是 `v2` 那种版本号，
# 显示在版本位会被读成卡片上的第二个发布日期。日期括号由 `_DATE_RE` 单独剥离。
_VERSION_PATTERNS: tuple[str, ...] = (
    r"v(?:er(?:sion)?[\s\-.]?)?\d+(?:\.\d+)*[a-z]?",
    r"r\d+(?:\.\d+)*",
    r"(?:build|rev)[\s\-.]?\d+(?:\.\d+)*",
    r"ch(?:apter)?[\s\-.]?\d+(?:\.\d+)*",
    r"(?:final|complete|full)[\s\-]?(?:version|release)",
)
_DATE_RE = re.compile(r"\d{4}[-/.]\d{2}[-/.]\d{2}")

_BRACKETS: tuple[tuple[str, str], ...] = (("[", "]"), ("(", ")"), ("{", "}"), ("【", "】"))
_BRACKET_RE = re.compile(r"[\[\(\{【]([^\[\]\(\)\{\}【】]*)[\]\)\}】]")
# 括号本身也是分隔符：保留下来的括号组要参与分组键，但 `(zenless zone zero)` 与
# `zenless zone zero` 必须归一，否则同一作品在不同站点的写法分不到一组。
_SEPARATOR_RE = re.compile(r"[\s　_\-–—~～:：;；,，.。!！?？'\"“”‘’/\\|+*#&\[\]\(\)\{\}【】]+")

#: 创作者别名匹配阈值。低于这个相似度的 token 不当成同一个人的手柄。
ALIAS_SIMILARITY = 0.88

#: 别名模糊匹配的最短长度。短 token 相似度不可靠（`riona`/`rinoa` 只差换位）。
ALIAS_MIN_LENGTH = 8


@dataclass(frozen=True)
class VariantVerdict:
    """一个追更条目的分组与变体判定。"""

    release_key: str
    variant_kind: str            # main / wip / alt
    variant_label: str | None
    version: str | None
    markers: tuple[str, ...]


def normalize_handle(value: str) -> str:
    """把创作者手柄压成只含小写字母数字的比较形式。"""
    folded = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z一-鿿぀-ヿ]+", "", folded)


def _looks_like_alias(token: str, aliases: frozenset[str]) -> bool:
    if not token:
        return False
    if token in aliases:
        return True
    if len(token) < ALIAS_MIN_LENGTH:
        return False
    return any(
        abs(len(token) - len(alias)) <= 3
        and difflib.SequenceMatcher(None, token, alias).ratio() >= ALIAS_SIMILARITY
        for alias in aliases
        if len(alias) >= ALIAS_MIN_LENGTH
    )


def _is_alias_group(inner: str, aliases: frozenset[str]) -> bool:
    """整个括号组是否只由创作者手柄组成。

    f95 的标题惯例是 `[LazyProcrastinator/LazyProcrast]` —— 一个括号里并列多个手柄，
    整体拼起来谁都不像，逐段看才认得出来。
    """
    pieces = [piece for piece in re.split(r"[/,、|]", inner) if piece.strip()]
    if not pieces:
        return False
    return all(_looks_like_alias(normalize_handle(piece), aliases) for piece in pieces)


def _match_markers(text: str) -> tuple[list[str], list[str]]:
    """返回 (WIP 标记, alt 标记)；text 必须已是单个括号组或单个 token。"""
    probe = text.strip().casefold()
    if not probe:
        return [], []
    if specs := video_spec_markers(probe):
        return [], list(specs)
    wip: list[str] = []
    alt: list[str] = []
    for pattern, label in _WIP_MARKERS:
        if re.fullmatch(pattern, probe):
            wip.append(label)
            return wip, alt
    for pattern, label in _ALT_MARKERS:
        matched = re.fullmatch(pattern, probe)
        if matched:
            alt.append(label.format(*matched.groups()) if matched.groups() else label)
            return wip, alt
    return wip, alt


def _match_version(text: str) -> str | None:
    probe = text.strip()
    for pattern in _VERSION_PATTERNS:
        if re.fullmatch(pattern, probe, flags=re.IGNORECASE):
            return probe
    return None


def _normalize_key(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    tokens = [token for token in _SEPARATOR_RE.split(folded) if token]
    return " ".join(tokens)


def _bracket_verdict(inner: str, aliases: frozenset[str],
                     semantics: str) -> tuple[str | None, str | None,
                                              list[str], list[str]]:
    """一个括号组的处置。

    返回（替换文本或 None, 摘出的版本, WIP 标记, alt 标记）。替换文本为 None
    表示整组保留在标题里；摘出的版本至多一个。
    """
    if _is_alias_group(inner, aliases):
        return " ", None, [], []
    if semantics == "release" and _DATE_RE.fullmatch(inner.strip()):
        return " ", None, [], []
    if semantics == "release" and (candidate := _match_version(inner)):
        return " ", candidate, [], []
    wip, alt = _match_markers(inner)
    # 逗号分隔的括号组逐段再试一次，`(nude, 4k)` 这类才拆得开。
    if not wip and not alt and "," in inner:
        for piece in inner.split(","):
            piece_wip, piece_alt = _match_markers(piece)
            wip.extend(piece_wip)
            alt.extend(piece_alt)
        if len(wip) + len(alt) != len([p for p in inner.split(",") if p.strip()]):
            # 只要有一段不是标记，整组就当作品名的一部分保留。
            return None, None, [], []
    if not wip and not alt:
        return None, None, [], []
    return " ", None, wip, alt


def classify(
    title: str,
    *,
    creator_aliases: tuple[str, ...] = (),
    version: str | None = None,
    semantics: str = "work",
) -> VariantVerdict:
    """判定一个追更条目的分组键、变体类型和版本。

    `semantics='work'`：每个条目是独立作品（rule34、kemono 一类）。`v2` 判为 alt。
    `semantics='release'`：每个条目是同一作品的一次发布（f95 线程一类）。版本从标题
    摘出并排除在 `release_key` 外，同一作品的历次更新因此落进同一组。
    """
    if semantics not in ("work", "release"):
        raise ValueError("semantics must be 'work' or 'release'")
    aliases = frozenset(
        normalized for alias in creator_aliases
        if (normalized := normalize_handle(alias))
    )
    wip_markers: list[str] = []
    alt_markers: list[str] = []
    found_version = (version or "").strip() or None

    def _take_bracket(match: re.Match[str]) -> str:
        nonlocal found_version
        replacement, extracted, wip, alt = _bracket_verdict(
            match.group(1), aliases, semantics)
        found_version = found_version or extracted
        wip_markers.extend(wip)
        alt_markers.extend(alt)
        return replacement if replacement is not None else match.group(0)

    stripped = _BRACKET_RE.sub(_take_bracket, title)

    kept: list[str] = []
    for token in _SEPARATOR_RE.split(unicodedata.normalize("NFKC", stripped)):
        if not token:
            continue
        if _looks_like_alias(normalize_handle(token), aliases):
            continue
        if semantics == "release" and (candidate := _match_version(token)):
            found_version = found_version or candidate
            continue
        wip, alt = _match_markers(token)
        if wip and _bare_marker_allowed(wip[0]):
            wip_markers.extend(wip)
            continue
        if alt and all(_bare_marker_allowed(marker) for marker in alt):
            alt_markers.extend(alt)
            continue
        kept.append(token)

    release_key = _normalize_key(" ".join(kept))
    markers = tuple(dict.fromkeys(wip_markers + alt_markers))
    if wip_markers:
        kind, label = "wip", " + ".join(dict.fromkeys(wip_markers))
    elif alt_markers:
        kind, label = "alt", " + ".join(dict.fromkeys(alt_markers))
    else:
        kind, label = "main", None
    return VariantVerdict(release_key, kind, label, found_version, markers)


#: 同一 `release_key` 下选主条目时的来源优先级；越小越优先。未列出的排在最后。
PROVIDER_PRIORITY: dict[str, int] = follow_providers.priorities()


def _rank(item) -> tuple:
    kind_rank = {"main": 0, "alt": 1, "wip": 2}.get(getattr(item, "variant_kind", "main"), 3)
    provider = getattr(item, "provider", "") or ""
    published = getattr(item, "published_at", None) or ""
    # `release` 语义下同一组是一个作品的历次动态，代表应当是最新那条；`work` 语义下
    # 同一组是同一作品的多个副本，代表取最早发布的那个原始版本。
    if getattr(item, "semantics", "work") == "release":
        published = _descending(published)
    return (kind_rank, PROVIDER_PRIORITY.get(provider, 99), published,
            str(getattr(item, "external_id", "")))


def _descending(text: str) -> str:
    """把时间戳翻成可以继续用最小值比较的降序键。"""
    return "".join(chr(0x10FFFD - ord(char)) if ord(char) < 0x10FFFD else char
                   for char in text)


def group_duplicates(items) -> tuple:
    """把同一 `release_key` 的条目归组，返回与输入等长的主条目序列。

    `result[i]` 是 `items[i]` 所属组的主条目；没有 `release_key` 的条目对应 `None`。
    刻意返回位置对齐的序列而不是字典：条目里带 `dict` 字段就不可哈希，用条目本身
    当键会在调用方那里炸掉，而用 `id()` 当键又会在条目被重建时悄悄失配。

    条目需要有 `release_key`、`variant_kind`、`provider`、`published_at`、`external_id`。
    主条目取「main 优先于 alt 优先于 wip，再按来源优先级，再按最早发布」。
    跨站同名作品因此折叠成一张卡片，本站的 alt 与 WIP 挂在主条目下。
    """
    members: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        key = getattr(item, "release_key", "") or ""
        if key:
            members.setdefault(key, []).append(index)
    primaries: list[object] = [None] * len(items)
    for indexes in members.values():
        winner = min(indexes, key=lambda index: _rank(items[index]))
        for index in indexes:
            primaries[index] = items[winner]
    return tuple(primaries)
