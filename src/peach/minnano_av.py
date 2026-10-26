r"""minnano-av 的页面解析：检索、女优资料表、事务所名册。

这个站是目前唯一能从女优名走到社媒、事务所和「这家事务所有哪些人」的入口
（`docs/reference-snapshots/minnano-av-official-portraits.md`）。解析规则本来长在
`scripts/harvest_performer_links.py` 里，名册采集要用同一套，所以搬到这里；
两个脚本共用一份，改站点结构时只有一处要改。

**只采信重定向。** `search_result.php?search_scope=actress&search_word=<名字>` 有三种
结果：唯一命中会跳到 `actressNNN.html`；多命中或无命中都停在检索页。检索页正文里同样
有一堆 `actressNNN.html`，那是「相关女优」，按正文解析会把别人的社媒安到这个人头上——
信号只在最终地址里。

**资料表的站内链接不是这个人的链接，却正是名册的入口。** 表里的值形如
`<td><span>标签</span><p>值</p></td>`；「所属事務所」那一格的 `<a>` 指向
`actress_list.php?production=134`，那个数字就是这家事务所在站内的编号。
`profile_fields` 按「绝对 URL 才算外链」把它挡在外面是对的——但编号得单独取出来
（`production_ref`），否则名册页根本无从进入。

**名册页读 JSON-LD，不读正文。** 页面自己带一份 `CollectionPage`：`numberOfItems`
是这家的总人数，`itemListElement` 每项给 `name` 和 `actressNNN.html`。正文里那几个
`<a>` 的文字是 `鈴北梨乃女優情報`——把「女優情報」当名字的一部分拿去和账本对名字，
一个都对不上。翻页也不猜 `&page=N`：`<link rel="next">` 就在头里，没有它就是最后一页。

**资料表整张读成结构化资料**（`profile`，ADR-0067）：出生日期、身高三围、血型、出身地、
出道年与出道作品这些格子逐格规整，读不出的列留空；每一格的原文另存一份，规整规则改了
不必重新取页。
"""
from __future__ import annotations

import html as html_entities
import json
import re
from urllib.parse import quote, urljoin, urlsplit

SEARCH = "https://www.minnano-av.com/search_result.php?search_scope=actress&search_word="
SITE = "https://www.minnano-av.com/"
ACTRESS_PAGE = re.compile(r"/actress(\d+)\.html")
FIELD = re.compile(r"<td[^>]*>\s*<span[^>]*>(.*?)</span>(.*?)</td>", re.S)
HREF = re.compile(r'href=["\']([^"\']+)["\']')
_PRODUCTION = re.compile(r'href=["\'][^"\']*production=(\d+)[^"\']*["\'][^>]*>(.*?)</a>', re.S)
_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_NEXT = re.compile(r'<link[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']', re.I)
_CANONICAL = re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_SPAN = re.compile(r"<span[^>]*>.*?</span>", re.S)
_ACT_PROFILE = re.compile(r'<div[^>]+class=["\']act-profile["\'][^>]*>(.*?)</table>', re.S)
_RESULT_TABLE = re.compile(r'<table[^>]+class=["\']tbllist actress["\'][^>]*>(.*?)</table>', re.S)
_RESULT_NAME = re.compile(
    r'<h2[^>]+class=["\']ttl["\'][^>]*>\s*<a[^>]+href=["\'][^"\']*actress(\d+)\.html["\'][^>]*>(.*?)</a>',
    re.S)


def search_url(name: str) -> str:
    return SEARCH + quote(name, encoding="utf-8")


def roster_url(production: str | int, page: int = 1) -> str:
    """这家事务所的名册页。第一页不带 `page=`，和站点自己的 canonical 保持一致。"""
    tail = f"&page={page}" if page > 1 else ""
    return f"{SITE}actress_list.php?production={production}{tail}"


def actress_id(final_url: str) -> str:
    """唯一命中时最终地址里的女优编号；停在检索页就返回空。"""
    match = ACTRESS_PAGE.search(urlsplit(final_url).path)
    return match.group(1) if match else ""


def profile_fields(html: str) -> dict[str, list[str]]:
    """资料表 → {标签: [绝对 URL, ...]}，只留站外链接。

    站内链接（`actress_list.php?blood_type=A` 这类）是检索入口不是这个人的链接，
    混进来会让每位女优都挂上一串「A 型」「東京都」的站内跳转。
    """
    found: dict[str, list[str]] = {}
    for match in FIELD.finditer(html):
        label = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        external = [href for href in HREF.findall(match.group(2))
                    if urlsplit(href).scheme in {"http", "https"}]
        if external:
            found.setdefault(label, []).extend(external)
    return found


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_entities.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def page_actress_id(html: str) -> str:
    """这一页是某位女优的资料页时，它自报的编号；不是就返回空。

    检索唯一命中时站点直接跳到资料页，缓存里存的是跳过去之后的正文，发出去的检索地址
    说明不了这是谁的页。页面头里的 canonical 才说得清。
    """
    found = _CANONICAL.search(html or "")
    return actress_id(found.group(1)) if found else ""


def search_hits(html: str) -> list[tuple[str, str]]:
    """检索结果表 → [(女优编号, 这一行显示的写法)]。

    只读结果表那一张：页面其余位置的 `actressNNN.html` 是推荐与相关女优。同一位女优的
    几个别名各占一行（「神山ももか」「神山ももか(天然むすめ)」都指 699633），
    所以判唯一要按编号去重，不能数行。
    """
    table = _RESULT_TABLE.search(html or "")
    if not table:
        return []
    return [(found, _text(name)) for found, name in _RESULT_NAME.findall(table.group(1))]


def profile_names(html: str) -> tuple[str, list[str]]:
    """资料页 → (主名, 资料表里每一行「別名」的原文)。不是资料页返回 ("", [])。

    主名取 `<h1>` 里 `<span>` 之前那段（`<span>` 里是读音与罗马字）。别名只读资料表
    `act-profile` 那一块：作品列表与评论里也有人名，那些不是她的名字。原文带着站上的注记
    （`雫つむぎ(FC2) （しずくつむぎ / SizukuTsumugi）`），注记怎么剥由调用方决定。
    """
    if not page_actress_id(html):
        return "", []
    heading = _H1.search(html)
    main = _text(_SPAN.sub("", heading.group(1))) if heading else ""
    block = _ACT_PROFILE.search(html)
    aliases = []
    for match in FIELD.finditer(block.group(1) if block else ""):
        if _text(match.group(1)) == "別名":
            aliases.append(_text(match.group(2)))
    return main, [alias for alias in aliases if alias]


_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_PERIOD = re.compile(r"(\d{4})\s*年?\s*[-－~〜～]\s*(?:(\d{4})\s*年?)?")
_SIZE = re.compile(r"([TBWH])\s*(\d{2,3})(?:\s*[(（]\s*([A-Z]{1,2})\s*カップ\s*[)）])?")
_BLOOD = re.compile(r"^(AB|A|B|O)\s*型$")
_TRAILING_NOTE = re.compile(r"[（(][^（()）]*[）)]\s*$")
_ANCHOR_TEXT = re.compile(r"<a[^>]*>(.*?)</a>", re.S)
#: `profile` 交回的固定列；解析不到的一律是 None，不猜。
PROFILE_COLUMNS = (
    "kana", "romaji", "birth_date", "height_cm", "bust_cm", "cup", "waist_cm", "hip_cm",
    "blood_type", "birthplace", "hobbies", "debut_year", "active_until", "debut_title",
    "debut_date", "blog_url", "site_url")


def _iso_date(text: str) -> str | None:
    """`2001年12月08日` → `2001-12-08`；年月日不齐或不是合法日期都返回 None。"""
    from datetime import date

    found = _DATE.search(text or "")
    if not found:
        return None
    try:
        return date(*(int(part) for part in found.groups())).isoformat()
    except ValueError:
        return None


def _sizes(text: str) -> dict:
    """`T156 / B86( Eカップ ) / W58 / H85 / S` → 身高与三围（厘米）、罩杯。

    站上的数字就是厘米，不带单位；写成 `B-`、`W--` 的是没填，对应列留空。最后那个 `S`
    站上没有说明是什么，只留在原文里。
    """
    found: dict = {}
    names = {"T": "height_cm", "B": "bust_cm", "W": "waist_cm", "H": "hip_cm"}
    for letter, number, cup in _SIZE.findall(text or ""):
        found.setdefault(names[letter], int(number))
        if letter == "B" and cup:
            found.setdefault("cup", cup)
    return found


def _external(fragment: str) -> str | None:
    """这一格里第一个站外链接；站上显示的文字常是 `http://`，`href` 才是真地址。"""
    return next((href for href in HREF.findall(fragment)
                 if urlsplit(href).scheme in {"http", "https"}), None)


def profile(html: str) -> dict | None:
    """资料页 → 结构化资料；不是资料页返回 None。

    固定列见 `PROFILE_COLUMNS`，另有 `actress_id`、`name`、`tags`（站上的标签，按页上顺序）
    与 `raw`（资料表每一格的原文，`別名` 那几行是列表，`名前` 是 `<h1>` 里的主名）。只读 `act-profile` 那一块，作品与
    评论里的同名格子不算。逐格的规则：

    - 读音与罗马字取 `<h1>` 里 `<span>` 那段 `かな / Romaji`，缺哪半就留空。
    - `生年月日` 取「年月日」三段拼成 ISO 日期，后面的「現在 24歳」与星座不要。
    - `サイズ` 见 `_sizes`；`血液型` 只收 A、B、O、AB 四种。
    - `AV出演期間` 形如 `2021年 -` 或 `2015年 - 2019年`：后半没写就是还在活动，`active_until` 为 None。
    - `デビュー作品` 形如 `作品名（2021年05月 21日）`：括号里是出道日期，括号前是作品名。
    - `ブログ`、`公式サイト` 取第一个站外链接的 `href`。
    """
    found_id = page_actress_id(html)
    if not found_id:
        return None
    heading = _H1.search(html)
    reading = ""
    if heading:
        span = re.search(r"<span[^>]*>(.*?)</span>", heading.group(1), re.S)
        reading = _text(span.group(1)) if span else ""
    kana, _, romaji = (part.strip() for part in reading.partition("/"))
    block = _ACT_PROFILE.search(html)
    cells: dict[str, str] = {}
    raw: dict = {}
    for match in FIELD.finditer(block.group(1) if block else ""):
        label, value = _text(match.group(1)), match.group(2)
        if not label:
            continue
        if label == "別名":
            raw.setdefault(label, []).append(_text(value))
            continue
        cells.setdefault(label, value)
        raw.setdefault(label, _text(value))
    name = _text(_SPAN.sub("", heading.group(1))) if heading else ""
    if name:
        raw["名前"] = name
    result: dict = {"actress_id": found_id, "name": name,
                    "kana": kana or None, "romaji": romaji or None}
    result["birth_date"] = _iso_date(_text(cells.get("生年月日", "")))
    result.update(dict.fromkeys(("height_cm", "bust_cm", "cup", "waist_cm", "hip_cm")))
    result.update(_sizes(_text(cells.get("サイズ", ""))))
    blood = _BLOOD.match(_text(cells.get("血液型", "")))
    result["blood_type"] = blood.group(1) if blood else None
    result["birthplace"] = _text(cells.get("出身地", "")) or None
    result["hobbies"] = _text(cells.get("趣味・特技", "")) or None
    periods = list(_PERIOD.finditer(_text(cells.get("AV出演期間", ""))))
    result["debut_year"] = min(int(period.group(1)) for period in periods) if periods else None
    latest = max(periods, key=lambda period: int(period.group(1))) if periods else None
    result["active_until"] = int(latest.group(2)) if latest and latest.group(2) else None
    debut = _text(cells.get("デビュー作品", ""))
    result["debut_date"] = _iso_date(debut)
    title = _TRAILING_NOTE.sub("", debut).strip() if result["debut_date"] else debut
    result["debut_title"] = title or None
    result["blog_url"] = _external(cells.get("ブログ", ""))
    result["site_url"] = _external(cells.get("公式サイト", ""))
    result["tags"] = [tag for tag in (_text(text) for text in _ANCHOR_TEXT.findall(cells.get("タグ", "")))
                      if tag]
    result["raw"] = raw
    return result


def profile_text(html: str, label: str) -> str:
    for match in FIELD.finditer(html):
        if re.sub(r"<[^>]+>", "", match.group(1)).strip() == label:
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(2))).strip()[:80]
    return ""


#: 资料表里一格名字后面的括号：读音（`（しのざきゆうこ / Shinozaki Yuuko）`）或注记
#: （`（舞ワイフ）`、`(東京熱)`）。站上全半角括号混着写，`（ラグジュTV)` 这种一半一半的也有。
_PAREN = re.compile(r"[（(【]([^（()）【】]*)[）)】]")
_KANA_READING = re.compile(r"^[぀-ゟ゠-ヿ\s]*$")
#: 这些注记说的是「这是她的旧名字」，不是哪个渠道：归到旧名义，不单列一组。
_FORMER_NOTES = {"旧名", "旧名義", "旧芸名", "旧"}


def _note(text: str) -> str:
    note = re.sub(r"\s+", " ", text).strip().removesuffix("名義").strip()
    return "" if note in _FORMER_NOTES else note


def name_entries(cell: str) -> list[dict]:
    """「別名」一格的原文 → [{name, reading, romaji, note}]，一个写法一项。

    格子的形状是 `写法（注记） （读音 / 罗马字）`：带斜杠的那组括号是读音，其余括号是注记，
    说的是这个名字挂在哪个渠道（`舞ワイフ`、`ラグジュTV`）或哪家店。注记本身是边界：
    `橋本真紀&桧山彩音（舞ワイフ名義）` 里两个名字共用一条注记，拆名字的规则与 av_neme
    同一套（`sources.seesaa.split_names`：`&`、`、`、`／`，片假名外国人名里的 `・` 不拆）。

    读音那组括号里左半不是假名时（`あいな （吉原ソープ 薔薇の園 / Aina）`），站上把店名填进了
    读音栏，按注记收。一格里拆出不止一个名字时读音不知道归谁，都不挂。
    """
    from .sources.seesaa import split_names

    text = _text(cell)
    groups = list(_PAREN.finditer(text))
    reading_at = next((index for index in range(len(groups) - 1, -1, -1)
                       if "/" in groups[index].group(1)), -1)
    reading = romaji = reading_note = ""
    if reading_at >= 0:
        left, _, right = groups[reading_at].group(1).partition("/")
        romaji = right.strip()
        if _KANA_READING.match(left):
            reading = re.sub(r"\s+", "", left)
        else:
            reading_note = _note(left)
    entries: list[dict] = []
    pending: list[dict] = []
    cursor = 0
    for index, group in enumerate([*groups, None]):
        end = group.start() if group else len(text)
        for name in split_names(text[cursor:end]):
            entry = {"name": name, "reading": "", "romaji": "", "note": ""}
            entries.append(entry)
            pending.append(entry)
        if group is None:
            break
        cursor = group.end()
        if index == reading_at:
            continue
        note = _note(group.group(1))
        for entry in pending:
            entry["note"] = note
        pending = []
    if reading_note:
        for entry in entries:
            entry["note"] = entry["note"] or reading_note
    if len(entries) == 1:
        entries[0].update(reading=reading, romaji=romaji)
    return entries


def production_ref(html: str) -> tuple[str, str]:
    """女优页的「所属事務所」→ (站内编号, 站上写的事务所名)；这一格没有链接就是 ("", "")。

    编号是名册页唯一的入口。名字一并取回来，用来核对这个编号确实是那家事务所——
    只凭一个数字装上去，装错了在复核件上看不出来。
    """
    for match in FIELD.finditer(html):
        if re.sub(r"<[^>]+>", "", match.group(1)).strip() != "所属事務所":
            continue
        found = _PRODUCTION.search(match.group(2))
        if not found:
            return "", ""
        return found.group(1), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", found.group(2))).strip()
    return "", ""


def roster_page(html: str, url: str = SITE) -> tuple[list[tuple[str, str]], int, str]:
    """名册页 → ([(女优名, 女优编号), ...], 这家的总人数, 下一页地址)。

    总人数是站点自己声明的 `numberOfItems`，用来对账：翻完所有页拿到的条数和它对不上，
    说明翻页断在中间，那是「没采全」而不是「这家就这么多人」。
    """
    people: list[tuple[str, str]] = []
    total = 0
    for block in _LD_JSON.findall(html or ""):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict) or node.get("@type") != "CollectionPage":
                continue
            listing = node.get("mainEntity") or {}
            total = max(total, int(listing.get("numberOfItems") or 0))
            for item in listing.get("itemListElement") or []:
                person = (item or {}).get("item") or {}
                name = str(person.get("name") or "").strip()
                found = actress_id(str(person.get("url") or ""))
                if name:
                    people.append((name, found))
    following = _NEXT.search(html or "")
    if not following:
        return people, total, ""
    # 属性里的 `&amp;` 要还原成 `&`，否则下一页地址成了
    # `...production=134&amp;page=2`：服务端认不出 `amp;page`，照样回第一页，
    # 而每翻一次这段又长一截，看起来像在翻页，实际是同一页抄了七遍。
    return people, total, urljoin(url, html_entities.unescape(following.group(1)))
