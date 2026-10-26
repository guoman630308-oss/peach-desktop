"""K-MIB 官网（k-mib.com）的单片页、演员页与列表页解析。

韩国 MIB 不是 JAV，JAV 目录站按它的番号返回的一定是别的作品（见
`catalog_rules.KOREAN_MIB_PREFIXES`）。它自己的官网是这批番号唯一可信的来源：
单片页给标题、发行日期、片长、演员、番号、分类和封面，演员页给艺名、头像和资料。

站上还发行合作厂牌的片（JS MEDIA、Studio REAL 等）。这些片的 Actor 栏填的是
厂牌名而不是人名，照抄会把厂牌建成演员实体，所以按厂牌名单改记为 maker。

只解析本机快照，不联网；抓取由 `scripts/harvest_kmib.py` 负责。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .catalog_rules import normalise_code_key

ROOT = "https://www.k-mib.com"
SOURCE = "kmib"
PROVIDER = "k-mib"
STUDIO = "MIB"
VIDEO_URL = ROOT + "/video/video-view.php?idx={idx}"
STAR_URL = ROOT + "/star/star-view.php?idx={idx}"
VIDEO_LIST_URL = ROOT + "/video/video-list.php?keyword=&sort=N&filter=T&filterIdx=&page={page}"
STAR_LIST_URL = ROOT + "/star/star-list.php?keyword=&sort=R&filter=A&filterIdx=&page={page}"

#: Actor 栏里出现的合作厂牌名 → 规范写法。键按 `_label_key` 压过（去空白、大写），
#: 站上 `JS MEDIA` 与 `JSMEDIA` 两种写法都有。
PARTNER_LABELS = {
    "JSMEDIA": "JS MEDIA",
    "STUDIOREAL": "Studio REAL",
    "SETFLIX": "SETFLIX",
    "PEEKO": "PEEKO",
    "MMP": "MMP",
    "JOGLOBAL": "JO GLOBAL",
}
#: 合作厂牌的番号前缀。Actor 栏并不总写厂牌名：`MMP-001` 列的是三位真人演员。
PARTNER_PREFIXES = {
    "JS": "JS MEDIA", "JES": "JS MEDIA",
    "STRC": "Studio REAL", "STRJ": "Studio REAL", "STRN": "Studio REAL",
    "STRS": "Studio REAL",
    "SFL": "SETFLIX", "PEO": "PEEKO", "MMP": "MMP", "SOOAH": "JO GLOBAL",
}

#: 没上架的占位页：各栏都空着，发行日期是 Unix 纪元。
_PLACEHOLDER_DATE = "1970-01-01"
_RUNTIME = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")
_DETAIL_LINK = re.compile(r"view\('(\d+)'\s*,\s*'([^']*)'\)")


def _label_key(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def _split_list(node) -> list[str]:
    """逗号分隔的栏目。分类栏每项包在 `<p>` 里，演员栏是纯文本，两种都认。"""
    if node is None:
        return []
    items = [_text(p) for p in node.find_all("p")] or _text(node).split(",")
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))


def _pairs(soup, row_selector: str) -> dict[str, object]:
    """`.head` / `.conts` 成对的资料栏，键取 head 文本。"""
    out: dict[str, object] = {}
    for row in soup.select(row_selector):
        head = row.select_one(".head")
        conts = row.select_one(".conts")
        if head is not None and conts is not None:
            out.setdefault(_text(head), conts)
    return out


def canonical_code(raw: str) -> str:
    """站上写法 → 账本写法。`SOY_101` 用的是下划线，其余都是 `PREFIX-NNN`。"""
    return normalise_code_key(str(raw or "").strip().replace("_", "-"))


def runtime_minutes(raw: str) -> float | None:
    match = _RUNTIME.match(str(raw or "").strip())
    if not match:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    total = hours * 60 + minutes + seconds / 60
    return round(total, 2) if total > 0 else None


def parse_video(html: str | bytes, idx: int | str) -> dict | None:
    """单片页 → 与 `extract_peach_fields` 兼容的 payload；占位页返回 None。"""
    soup = BeautifulSoup(html, "html.parser")
    fields = _pairs(soup, "div.info-box")
    code = canonical_code(_text(fields.get("Code")))
    release_date = _text(fields.get("Release date"))
    if not code or release_date in {"", _PLACEHOLDER_DATE}:
        return None
    names = _split_list(fields.get("Actor"))
    partners = [PARTNER_LABELS[_label_key(name)] for name in names
                if _label_key(name) in PARTNER_LABELS]
    partner = PARTNER_PREFIXES.get(code.split("-", 1)[0]) or (partners[0] if partners else "")
    actors = [name for name in names if _label_key(name) not in PARTNER_LABELS]
    cover = soup.select_one("div.video-wrap .control img")
    cover_src = str(cover.get("src") or "").strip() if cover is not None else ""
    return {
        "id": code,
        "provider_id": code,
        "content_id": "",
        "idx": str(idx),
        "source_url": VIDEO_URL.format(idx=idx),
        "title": _text(fields.get("Title")),
        "description": _text(fields.get("Introduction")),
        "release_date": release_date,
        "runtime": runtime_minutes(_text(fields.get("Playing time"))),
        "actresses": [{"japanese_name": name} for name in actors],
        "maker": partner or STUDIO,
        "partner": bool(partner),
        "genres": _split_list(fields.get("Category")),
        "cover_url": urljoin(ROOT + "/", cover_src) if cover_src else "",
    }


def parse_star(html: str | bytes, idx: int | str) -> dict | None:
    """演员页 → 艺名、头像地址与资料；没有名字的空页返回 None。"""
    soup = BeautifulSoup(html, "html.parser")
    fields = _pairs(soup, ".profile-detail li")
    name = _text(fields.get("Name")) or _text(soup.select_one(".main-conts em"))
    if not name:
        return None
    image = soup.select_one(".profile-img img")
    src = str(image.get("src") or "").strip() if image is not None else ""
    profile = {key: _text(fields.get(label)) for key, label in (
        ("introduction", "Introduction"), ("age", "Age"), ("height", "Height"),
        ("weight", "Weight"), ("bwh", "B-W-H"),
    )}
    return {
        "idx": str(idx),
        "name": name,
        "source_url": STAR_URL.format(idx=idx),
        "image_url": urljoin(ROOT + "/", src) if src else "",
        "tags": _split_list(fields.get("Tag")),
        **{key: value for key, value in profile.items() if value},
    }


def parse_list(html: str | bytes) -> dict[str, list[str]]:
    """列表页里的详情页 idx，按 video / star 分开，保序去重。"""
    out: dict[str, list[str]] = {"video": [], "star": []}
    for idx, target in _DETAIL_LINK.findall(str(html if isinstance(html, str)
                                                  else html.decode("utf-8", "replace"))):
        kind = "video" if "video-view" in target else "star" if "star-view" in target else ""
        if kind and idx not in out[kind]:
            out[kind].append(idx)
    return out
