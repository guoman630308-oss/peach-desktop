"""按入口查磁力：直接添加优先命中，待找清单汇集多个来源。

这一层只返回候选，不提交下载。站点解析与排序分开：来源顺序回答「先问谁」，
``rank_candidates`` 回答同一来源的多个版本选哪个。文件多不构成拒绝；只有明显预告、
安装包或推广附件会降低排序。manko.fun 候选带 ``watermark_check='bypassed'``，其余
候选在磁力元数据阶段只能标记为 ``pending``，留给到盘后的媒体检查。
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from .catalog_rules import normalise_code_key, release_code_from_text, same_release_code
from .downloads import parse_magnet
from .jav_cover_fetch import HostLimitedTransport
from .resource_search import qualities
from .scraping_access import SourceTransport
from .sources import JavDBSource, Session, SourceFailure

HIGH_QUALITY = 1080
MAX_RESULTS = 8
BASE91_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "!#$%&()*+,./:;<=>?@[]^_`{|}~\""
)
_BASE91 = {character: index for index, character in enumerate(BASE91_ALPHABET)}
_SIZE = re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*(TiB|GiB|MiB|KiB|TB|GB|MB|KB)\b")
_MAGNET = re.compile(r'href=["\'](magnet:\?[^"\']+)', re.I)
_PROMO_FILE = re.compile(r"(?i)(?:\.url$|\.apk$|\.exe$|manko\.fun|广告|廣告|扫码|掃碼|最新地址)")


@dataclass(frozen=True)
class Source:
    key: str
    label: str


CHAIN = (
    Source("manko", "manko.fun"),
    Source("javdb", "JavDB"),
    Source("sukebei", "sukebei"),
    Source("javbus", "JavBus"),
    Source("16mag", "16mag"),
    Source("onejav", "OneJAV"),
    Source("freejavtorrent", "Free JAV Torrent"),
)
REFERENCE_ONLY = ("iJAVTorrent", "Akiba Online", "Reddit")


def decode_base91(value: str) -> bytes:
    output = bytearray()
    first = -1
    buffer = bits = 0
    for character in str(value or ""):
        decoded = _BASE91.get(character)
        if decoded is None:
            continue
        if first < 0:
            first = decoded
            continue
        combined = first + decoded * 91
        buffer |= combined << bits
        bits += 13 if combined & 8191 > 88 else 14
        while bits >= 8:
            output.append(buffer & 255)
            buffer >>= 8
            bits -= 8
        first = -1
    if first >= 0:
        output.append((buffer | first << bits) & 255)
    return bytes(output)


def resolution_of(value: object) -> int:
    text = str(value or "")
    for pattern, resolution in ((r"(?i)\b(?:2160p|4k|uhd)\b", 2160),
                                (r"(?i)\b(?:1080[pi]|fhd)\b", 1080),
                                (r"(?i)(?:\b720p\b|(?<![a-z])hd(?![a-z]))", 720)):
        if re.search(pattern, text):
            return resolution
    return 0


def size_bytes(value: object) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    match = _SIZE.search(str(value or ""))
    if not match:
        return 0
    unit = match.group(2).casefold()
    exponent = {"kb": 1, "kib": 1, "mb": 2, "mib": 2,
                "gb": 3, "gib": 3, "tb": 4, "tib": 4}[unit]
    return int(float(match.group(1)) * 1024 ** exponent)


def _normalise(item: dict, source: Source, code: str) -> dict | None:
    uri = str(item.get("uri") or item.get("magnet") or "")
    try:
        magnet = parse_magnet(uri)
    except ValueError:
        return None
    name = str(item.get("name") or magnet.name or magnet.info_hash)[:500]
    # 站点搜索可以模糊命中，只有名字能确认番号时才收；manko 的搜索结果另在标题层核过。
    shown = release_code_from_text(name)
    if shown and not same_release_code(shown, code):
        return None
    raw_files = item.get("file_list") or ()
    raw_files = (raw_files,) if isinstance(raw_files, str) else raw_files
    files = [str(value)[:500] for value in raw_files if str(value).strip()][:200]
    promo_files = sum(bool(_PROMO_FILE.search(value)) for value in files)
    raw_attributes = item.get("attributes") or ()
    raw_attributes = (raw_attributes,) if isinstance(raw_attributes, str) else raw_attributes
    attributes = [str(value) for value in raw_attributes]
    quality = qualities(name, attributes, code)
    resolution = int(item.get("resolution") or resolution_of(
        " ".join((name, str(item.get("quality") or ""), " ".join(attributes)))))
    return {
        "id": magnet.info_hash, "info_hash": magnet.info_hash,
        'protocol': 'magnet', 'attributes': attributes,
        "uri": magnet.uri, "name": name, "size": size_bytes(item.get("size")),
        "size_text": str(item.get("size") or ""), "files": str(item.get("files") or ""),
        "file_count": int(item.get("file_count") or len(files) or 0),
        "file_list": files, "promo_files": promo_files,
        "resolution": resolution, "codec": str(item.get("codec") or quality["codec"]),
        "chinese": bool(item.get("chinese", quality["chinese"])),
        "uncensored": bool(item.get("uncensored", quality["uncensored"])),
        "seeders": int(item.get("seeders") or item.get("seeds") or 0),
        "peers": int(item.get("peers") or 0), "date": str(item.get("date") or "")[:100],
        "source": source.label, "origins": [source.label],
        "source_url": str(item.get("source_url") or ""),
        "watermark_check": "bypassed" if source.key == "manko" else "pending",
    }


def rank_candidates(items: list[dict], goal: str = "quality") -> list[dict]:
    """先画质、再做种、最后正片体积；附属文件只轻微降序，不作硬门槛。"""
    unique: dict[str, dict] = {}
    for item in items:
        prior = unique.get(item["info_hash"])
        if prior is None:
            unique[item["info_hash"]] = item
            continue
        prior["origins"] = list(dict.fromkeys(prior["origins"] + item["origins"]))
        prior["seeders"] = max(prior["seeders"], item["seeders"])
        if not prior["file_list"] and item["file_list"]:
            prior["file_list"] = item["file_list"]
            prior["file_count"] = item["file_count"]
            prior["promo_files"] = item["promo_files"]
    def rank(row):
        trailer = bool(re.search(r"(?i)\b(?:sample|trailer|preview)\b|予告|预告|預告", row["name"]))
        preferred = bool(row.get(goal)) if goal in {"chinese", "uncensored"} else False
        return (trailer, -int(preferred), -row["resolution"], -row["seeders"], row["promo_files"],
                -row["size"], row["info_hash"])
    return sorted(unique.values(), key=rank)[:MAX_RESULTS]


class Fetcher:
    def __init__(self, root: Path, *, transport=None, deadline: float | None = None):
        self.root = Path(root)
        self.resource_result = {}
        self.deadline = deadline or time.monotonic() + 45
        self._owned = transport is None
        self.transport = transport or HostLimitedTransport(
            SourceTransport(self.root, max_requests=18, max_bytes=24 * 1024 * 1024,
                            max_seconds=45), 3)
        self.session = Session(self.transport, self.deadline)

    def close(self):
        if self._owned:
            self.transport.close()

    def json(self, url: str, source: str) -> dict:
        from .sources.base import SiteConfig
        config = SiteConfig(source, source, source + "-resource", "https://" + urllib.parse.urlsplit(url).netloc,
                            (urllib.parse.urlsplit(url).hostname or "",), "community", page_limit=4 * 1024 * 1024)
        page = self.session.get(url, config=config, referer="https://manko.fun/",
                                headers={"Accept": "application/json,*/*"})
        body = json.loads(page.text)
        if isinstance(body, dict) and isinstance(body.get("data"), str):
            body = {**body, "data": json.loads(decode_base91(body["data"]).decode("utf-8"))}
        return body

    def manko(self, code: str) -> list[dict]:
        source = CHAIN[0]
        query = urllib.parse.quote(code)
        body = self.json(f"https://healertanker.com/swx/searchBy?keyword={query}&by=title&page=1&size=20",
                         source.key)
        wanted = normalise_code_key(code)
        rows = [row for row in body.get("data", ()) if isinstance(row, dict)
                and normalise_code_key(str(row.get("title") or "")) == wanted]
        found = []
        for row in rows[:4]:
            detail = self.json("https://healertanker.com/swx/movie/detail/" +
                               urllib.parse.quote(str(row.get("_id") or "")), source.key).get("data")
            if not isinstance(detail, dict):
                continue
            for value, size in ((detail.get("torrent_url_2"), detail.get("file_size_2")),
                                (detail.get("torrent_url_1"), detail.get("file_size_1"))):
                if not value:
                    continue
                item = {"uri": value if isinstance(value, str) else value.get("url"),
                        "name": (value.get("name") if isinstance(value, dict) else "") or row.get("title") or code,
                        "quality": value.get("quality") if isinstance(value, dict) else "",
                        "seeders": value.get("seeds") if isinstance(value, dict) else 0,
                        "peers": value.get("peers") if isinstance(value, dict) else 0,
                        "size": size,
                        "source_url": f"https://manko.fun/movie-info/{row.get('_id')}?series=false"}
                candidate = _normalise(item, source, code)
                if candidate:
                    found.append(candidate)
        return rank_candidates(found)

    def javdb(self, code: str) -> list[dict]:
        source = CHAIN[1]
        result = JavDBSource().resources(code, session=self.session)
        self.resource_result = result
        return rank_candidates([candidate for item in result.get("items", ())
                                if (candidate := _normalise({**item, "source_url": result.get("source_url")},
                                                            source, code))])

    def sukebei(self, code: str) -> list[dict]:
        from .sources.base import SiteConfig
        source = CHAIN[2]
        config = SiteConfig(source.key, source.label, source.key + "-resource",
                            "https://sukebei.nyaa.si", ("sukebei.nyaa.si",), "community",
                            page_limit=4 * 1024 * 1024)
        url = f"https://sukebei.nyaa.si/?f=0&c=0_0&q={urllib.parse.quote(code)}"
        page = self.session.get(url, config=config)
        found = []
        for row in BeautifulSoup(page.text, "html.parser").select("table tbody tr"):
            text = row.get_text(" ", strip=True)
            if normalise_code_key(code) not in normalise_code_key(text):
                continue
            link = row.select_one('a[href^="magnet:"]')
            if link is None:
                continue
            cells = row.select("td")
            item = {"uri": html.unescape(str(link.get("href") or "")),
                    "name": str(link.get("title") or "") or text,
                    "size": next((cell.get_text(" ", strip=True) for cell in cells if _SIZE.search(cell.get_text(" ", strip=True))), ""),
                    "seeders": cells[-3].get_text(strip=True) if len(cells) >= 3 else 0,
                    "peers": cells[-2].get_text(strip=True) if len(cells) >= 2 else 0,
                    "source_url": urllib.parse.urljoin(url, str((row.select_one('a[href^="/view/"]') or {}).get("href", "")))}
            if item["source_url"]:
                try:
                    detail = self.session.get(item["source_url"], config=config, referer=url)
                    soup = BeautifulSoup(detail.text, "html.parser")
                    item["file_list"] = [node.get_text(" ", strip=True) for node in
                                         soup.select(".torrent-file-list li")
                                         if node.get_text(" ", strip=True)][:200]
                    item["file_count"] = len(item["file_list"])
                except Exception:
                    # 文件清单是质量证据，不是磁力本身；详情页失败仍保留搜索命中。
                    pass
            candidate = _normalise(item, source, code)
            if candidate:
                found.append(candidate)
        return rank_candidates(found)


def search(root: Path, code: str, *, transport=None, min_size: int = 0,
           max_size: int = 1024 ** 4, goal: str = "quality", stop_on_preferred: bool = True) -> dict:
    """有界查询三个可直连来源；直接添加允许在 manko 高清命中时结束。"""
    fetcher = Fetcher(root, transport=transport)
    trace, warnings, found = [], [], []
    try:
        for source, collect in ((CHAIN[0], fetcher.manko), (CHAIN[1], fetcher.javdb),
                                (CHAIN[2], fetcher.sukebei)):
            try:
                items = collect(code)
            except SourceFailure as error:
                items = []
                warnings.append(f"{source.label}：{error}")
            except Exception:
                items = []
                warnings.append(f"{source.label}：未取得")
            items = [item for item in items if min_size <= item["size"] <= max_size]
            found.extend(items)
            if stop_on_preferred and source.key == "manko" and any(item["resolution"] >= HIGH_QUALITY for item in items):
                trace.append(f"{source.label}：取得 1080p/2160p，已短路")
                break
            trace.append(f"{source.label}：取得 {len(items)} 条" if items else f"{source.label}：无结果")
    finally:
        fetcher.close()
    incomplete = bool(warnings) and not found
    result = {"items": rank_candidates(found, goal), "warnings": warnings, "trace": trace,
            'state': 'error' if incomplete else 'ready',
            'error': '部分来源未取得，请稍后重试或检查来源连接。' if incomplete else '',
            "next_sources": [source.label for source in CHAIN[3:]],
            "reference_only": list(REFERENCE_ONLY)}
    if not stop_on_preferred:
        linked = getattr(fetcher, 'resource_result', {})
        result.update(linked_resources=linked.get('items', []), source_url=linked.get('source_url', ''))
        result['warnings'].extend(linked.get('warnings') or [])
    return result
