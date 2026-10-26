"""字幕 sidecar：识别、配对、登记，以及播放器要的那份 WebVTT。

三段职责，边界刻意分开：

- **识别与配对**（`directory_sidecars`）只看一个目录里的文件名，不碰数据库，也不读
  文件内容。sidecar 按定义就和正片同目录，跨目录的相似文件名一律不算——番号相同的
  两个目录在这个库里很常见，跨目录配对会把「另一版的字幕」挂到这一版上。
- **登记**（`record`）把配对结论 upsert 进 `asset_subtitle`，幂等，重扫只更新
  `last_seen` 与可变字段；消失的行不删，沿用 `asset` 的约定。
- **出口**（`decode`、`to_webvtt`）把磁盘上的 srt/ass/ssa/vtt 换成浏览器唯一认识的
  WebVTT。这一段在 Python 里做而不是叫 FFmpeg：每请求一次字幕就起一个子进程，代价
  落在挂载网盘的目录遍历上；而且 FFmpeg 解码失败只给一个退出码，界面要说的是
  「这份字幕的编码认不出来」，两者不是同一件事。

本模块不 import `scan`：方向是 `scan` → `subtitles`。哪些扩展名算视频是扫描层的
判断，`directory_sidecars` 因此要求调用方把视频文件名传进来。
"""
from __future__ import annotations

import codecs
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PureWindowsPath

from .catalog_rules import VERSION_TAIL_TOKENS, release_code_from_filename

#: 文本字幕：能转成 WebVTT 交给播放器。
TEXT_FORMATS = {".srt": "srt", ".ass": "ass", ".ssa": "ssa", ".vtt": "vtt"}
#: 图形字幕。`.sub` 与同名 `.idx` 成对时是 VobSub 位图，浏览器没有对应的 text track；
#: 单独出现的 `.sub` 多半是 MicroDVD，它按帧计时，没有帧率就换不出时间轴。两种都只登记。
GRAPHIC_FORMATS = {".sub": "sub"}
SUBTITLE_FORMATS = {**TEXT_FORMATS, **GRAPHIC_FORMATS}

#: 文件名末尾的语言 token → (BCP 47 语言码, 界面标签)。
#: `ch` 在这个库里既是「中文字幕版」的版次标记也是语言标记，两种含义指向同一个结论。
LANGUAGE_TOKENS: dict[str, tuple[str, str]] = {
    token: ("zh-Hans", "简体中文")
    for token in ("chs", "gb", "gbk", "sc", "zhcn", "zh-cn", "chi", "chn", "简体", "简中")
}
LANGUAGE_TOKENS.update({
    token: ("zh-Hant", "繁體中文")
    for token in ("cht", "big5", "tc", "zhtw", "zh-tw", "zhhk", "zh-hk", "繁体", "繁體", "繁中")
})
LANGUAGE_TOKENS.update({token: ("zh", "中文") for token in ("zh", "ch", "cn", "chinese", "中文", "中字")})
LANGUAGE_TOKENS.update({token: ("en", "英语") for token in ("en", "eng", "english", "英文", "英语")})
LANGUAGE_TOKENS.update({token: ("ja", "日语") for token in ("ja", "jp", "jpn", "japanese", "日文", "日语", "日本語")})
LANGUAGE_TOKENS.update({token: ("ko", "韩语") for token in ("ko", "kor", "korean", "韩文", "韩语")})

#: 剥去之后仍应与正片同名的 token：语言之外，还有 `catalog_rules` 已经认定的版次标记
#: （`-C` 中文字幕版、`-UC` 无码流出、画质词）。版次表只此一份，不在这里重抄。
_STRIPPABLE = (frozenset(LANGUAGE_TOKENS) | frozenset(VERSION_TAIL_TOKENS)
               | {"forced", "sdh", "default"})
_SEPARATORS = ".-_ "
#: 一个 stem 最多剥几层：`ABP-758.chs.forced` 是两层，再多就不像是 sidecar 命名了。
_MAX_STRIPS = 3

#: 配对判据。存判据名而不是置信度，理由见迁移 0027 的注释。
EXACT, SUFFIX, CODE, ORPHAN = "exact", "suffix", "code", "orphan"


@dataclass(frozen=True)
class Sidecar:
    """一条字幕在账本里的样子。`video` 为 None 就是孤立字幕。"""

    path: str
    name: str
    format: str
    language: str
    pairing: str
    video: str | None
    size: int | None = None
    mtime: str | None = None


def subtitle_format(name: str) -> str:
    """文件名对应的字幕格式；不是字幕返回空串。"""
    suffix = PureWindowsPath(str(name or "")).suffix.lower()
    return SUBTITLE_FORMATS.get(suffix, "")


def is_playable_format(fmt: str) -> bool:
    return fmt in set(TEXT_FORMATS.values())


def _stem(name: str) -> str:
    text = str(name or "")
    head, dot, tail = text.rpartition(".")
    return head if dot and head else text


def _split_tail(stem: str) -> tuple[str, str]:
    """切下 stem 末尾的一个分隔 token；切不动时后半是空串。"""
    index = max(stem.rfind(separator) for separator in _SEPARATORS)
    if index <= 0:
        return stem, ""
    return stem[:index], stem[index + 1:]


def language_of(name: str) -> str:
    """从文件名末尾的 token 推断语言码；推不出返回空串。

    只看末尾几个 token，不扫整个文件名：`日本人妻.chs.srt` 里的「日本」是标题的一部分，
    按整串搜关键词会把它读成日语字幕。
    """
    stem = _stem(name)
    for _ in range(_MAX_STRIPS):
        head, tail = _split_tail(stem)
        if not tail:
            break
        known = LANGUAGE_TOKENS.get(tail.casefold())
        if known:
            return known[0]
        if tail.casefold() not in _STRIPPABLE:
            break
        stem = head
    return ""


def language_label(language: str) -> str:
    """语言码对应的界面标签；空语言码返回空串。"""
    for code, label in LANGUAGE_TOKENS.values():
        if code == language:
            return label
    return language


def track_label(name: str, language: str, video_name: str | None) -> str:
    """播放器菜单里那一行。

    有语言就用语言名。没有的话用文件名比正片多出来的那一段——那一段正是这条字幕区别于
    同一部片其它字幕的全部信息。完全同名时连那一段都没有，退到格式名：菜单里一行
    `SRT` 比一行四十个字的文件名好读，而这种情况下文件名一个字也没多说。
    """
    label = language_label(language)
    if label:
        return label
    stem, video_stem = _stem(name), _stem(video_name or "")
    if video_stem and stem.casefold().startswith(video_stem.casefold()):
        extra = stem[len(video_stem):].strip(_SEPARATORS)
        return extra or subtitle_format(name).upper()
    return stem


def _strip_known_tail(stem: str) -> Iterable[str]:
    """依次给出剥去 0…N 个已知 token 之后的 stem。"""
    yield stem
    for _ in range(_MAX_STRIPS):
        head, tail = _split_tail(stem)
        if not tail or tail.casefold() not in _STRIPPABLE:
            return
        stem = head
        yield stem


def pair(name: str, videos: Sequence[str]) -> tuple[str | None, str]:
    """这条字幕配哪个视频，以及判据名。配不上返回 `(None, ORPHAN)`。

    判据按先后取第一个成立的，同一判据下有多个视频命中时取文件名排序最前的那个：
    分卷片的每一卷都能配上同一条字幕，挂给第一卷至少是可复现的选择。
    """
    ordered = sorted(videos, key=str.casefold)
    stem = _stem(name).casefold()
    for video in ordered:
        if _stem(video).casefold() == stem:
            return video, EXACT
    trimmed = [candidate.casefold() for candidate in _strip_known_tail(_stem(name))][1:]
    for candidate in trimmed:
        for video in ordered:
            if _stem(video).casefold() == candidate:
                return video, SUFFIX
    # 番号解析按主名做：`.srt`／`.ass` 不在它认得的媒体扩展名里，带着后缀送进去
    # 会被当成文件名噪声的一部分，`ABP-758.srt` 于是解析不出 `ABP-758`。
    code = release_code_from_filename(_stem(name))
    if code:
        for video in ordered:
            if release_code_from_filename(video) == code:
                return video, CODE
    return None, ORPHAN


def directory_sidecars(
    ledger_dir: PureWindowsPath | str, stats: Mapping[str, tuple[int, str]],
    videos: Sequence[str],
) -> list[Sidecar]:
    """一个目录里的全部字幕及其配对结论。

    `stats` 是这个目录里能 stat 到的文件名 → `(size, mtime)`；`videos` 是其中的视频
    文件名，由扫描层判定。返回的 `path` 已经是账本口径的绝对路径。
    """
    root = PureWindowsPath(ledger_dir)
    found: list[Sidecar] = []
    for name in sorted(stats, key=str.casefold):
        fmt = subtitle_format(name)
        if not fmt:
            continue
        video, pairing = pair(name, videos)
        size, mtime = stats[name]
        found.append(Sidecar(
            path=str(root / name), name=name, format=fmt, language=language_of(name),
            pairing=pairing, video=str(root / video) if video else None,
            size=size, mtime=mtime,
        ))
    return found


_UPSERT = """INSERT INTO asset_subtitle(
                 asset_id,location,path,name,language,format,size,mtime,pairing,
                 first_seen,last_seen)
             VALUES(?,?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(location,path) DO UPDATE SET
               asset_id=excluded.asset_id, name=excluded.name,
               language=excluded.language, format=excluded.format,
               size=excluded.size, mtime=excluded.mtime,
               pairing=excluded.pairing, last_seen=excluded.last_seen"""


def record(
    connection: sqlite3.Connection, location: str, sidecars: Iterable[Sidecar], now: str,
) -> tuple[int, int]:
    """把配对结论写进 `asset_subtitle`，返回 `(落库行数, 其中孤立的条数)`。

    配到的视频在账本里找不到行时降级成孤立字幕：宁可少一条关联，也不要留一个指向
    不存在资产的 `asset_id`——运行时连接不开外键，那种行不会有人替我们拦住。
    """
    rows = []
    orphans = 0
    for sidecar in sidecars:
        asset_id = None
        if sidecar.video:
            found = connection.execute(
                "SELECT id FROM asset WHERE location=? AND path=?",
                (location, sidecar.video)).fetchone()
            asset_id = found[0] if found else None
        pairing = sidecar.pairing if asset_id is not None else ORPHAN
        orphans += pairing == ORPHAN
        rows.append((asset_id, location, sidecar.path, sidecar.name, sidecar.language,
                     sidecar.format, sidecar.size, sidecar.mtime, pairing, now, now))
    if rows:
        connection.executemany(_UPSERT, rows)
    return len(rows), orphans


def subtitles_for(connection: sqlite3.Connection, asset_id: int) -> list[sqlite3.Row]:
    """一个资产的字幕行，顺序稳定：序号就是这个顺序里的位置。"""
    return list(connection.execute(
        "SELECT id,path,name,language,format,size,pairing FROM asset_subtitle "
        "WHERE asset_id=? ORDER BY name COLLATE NOCASE, id", (asset_id,)))


class SubtitleUndecodable(ValueError):
    """字节按已知编码都读不出来。消息可以直接给人看。"""


#: 按顺序试的编码。严格模式是关键：`gb18030` 几乎什么字节都能吞下去，放进来的话
#: 「认不出编码」这个结论永远不会出现，界面上只会看到一屏乱码。
ENCODINGS = ("utf-8", "gbk", "big5", "shift_jis")
_BOMS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def decode(data: bytes) -> str:
    """字幕字节 → 文本。BOM 优先，其次按 `ENCODINGS` 逐个严格试。"""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data[len(bom):].decode(encoding)
            except UnicodeDecodeError:
                break
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SubtitleUndecodable("编码未识别")


_TIME = re.compile(r"(\d{1,3}):([0-5]\d):([0-5]\d)[,.](\d{1,3})")
#: 时间行两端各取一个时间戳。按 `-->` 劈开再各自解析会被 WebVTT 的 cue 设置
#: （`line:90% align:start`）带偏，那些设置跟在结束时间后面、中间只隔一个空格。
_CUE = re.compile(_TIME.pattern + r"\s*-->\s*" + _TIME.pattern)
#: ASS 的样式覆写块与 srt 里偶尔混进来的同款写法。
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")
#: WebVTT 不认 `<font>`，留下标签会被当成正文字符画出来；内容本身保留。
_FONT_TAG = re.compile(r"</?font[^>]*>", re.I)


def _clean_text(line: str) -> str:
    return _FONT_TAG.sub("", _ASS_OVERRIDE.sub("", line)).strip()


def _vtt_time(value: str) -> str | None:
    match = _TIME.search(value)
    return _format_time(*match.groups()) if match else None


def _format_time(hours: str, minutes: str, seconds: str, fraction: str) -> str:
    return f"{int(hours):02d}:{minutes}:{seconds}.{fraction.ljust(3, '0')[:3]}"


def srt_to_vtt(text: str) -> str:
    """SRT → WebVTT：时间戳逗号改点、丢掉序号行、正文按基本标签原样带过。"""
    cues = []
    for block in re.split(r"\n\s*\n", _normalise_newlines(text).strip()):
        lines = [line for line in block.split("\n") if line.strip()]
        if len(lines) > 1 and lines[0].strip().isdigit() and "-->" in lines[1]:
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        head = _CUE.search(lines[0])
        if not head:
            continue
        start = _format_time(*head.groups()[:4])
        end = _format_time(*head.groups()[4:])
        body = [_clean_text(line) for line in lines[1:]]
        cues.append(f"{start} --> {end}\n" + "\n".join(body))
    return _wrap(cues)


def ass_to_vtt(text: str) -> str:
    """ASS/SSA → WebVTT：只取 `[Events]` 里的对白，样式、定位和特效全部丢掉。

    字段顺序读 `Format:` 那一行而不是写死：SSA 与 ASS 的列不一样，同一份 ASS 里
    `Name` 与 `Actor` 两种写法也都出现过。`Text` 一定是最后一列，所以它里面的逗号
    靠 `maxsplit` 保下来。
    """
    fields: list[str] = []
    cues: list[tuple[str, str, str]] = []
    events = False
    for line in _normalise_newlines(text).split("\n"):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            events = stripped.casefold() == "[events]"
            continue
        if not events:
            continue
        key, _, rest = stripped.partition(":")
        if key.casefold() == "format":
            fields = [part.strip().casefold() for part in rest.split(",")]
        elif key.casefold() == "dialogue" and fields:
            parts = rest.split(",", len(fields) - 1)
            if len(parts) < len(fields):
                continue
            row = dict(zip(fields, parts))
            start = _vtt_time(_pad_ass_time(row.get("start", "")))
            end = _vtt_time(_pad_ass_time(row.get("end", "")))
            body = _clean_text(re.sub(r"\\[Nn]", "\n", row.get("text", "")).replace("\\h", " "))
            if start and end and body:
                cues.append((start, end, body))
    return _wrap([f"{start} --> {end}\n{body}" for start, end, body in sorted(cues)])


def _pad_ass_time(value: str) -> str:
    """ASS 写 `0:00:02.50`（百分之一秒），补成时间戳正则认得的形状。"""
    text = value.strip()
    head, dot, fraction = text.rpartition(".")
    return f"{head}.{fraction.ljust(2, '0')[:2]}" if dot else text


def vtt_passthrough(text: str) -> str:
    """已经是 WebVTT 的直出，只补规范要求的文件头。"""
    body = _normalise_newlines(text).lstrip("﻿")
    if body.lstrip().startswith("WEBVTT"):
        return body if body.endswith("\n") else body + "\n"
    return "WEBVTT\n\n" + body.lstrip()


def _normalise_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _wrap(cues: Sequence[str]) -> str:
    return "WEBVTT\n\n" + "\n\n".join(cues) + ("\n" if cues else "")


def to_webvtt(data: bytes, fmt: str) -> str:
    """字幕字节 → WebVTT 文本。格式不可播或编码认不出来都抛异常。"""
    if not is_playable_format(fmt):
        raise SubtitleUndecodable("图形字幕不能作为文本轨播放")
    text = decode(data)
    if fmt == "vtt":
        return vtt_passthrough(text)
    if fmt in {"ass", "ssa"}:
        return ass_to_vtt(text)
    return srt_to_vtt(text)
