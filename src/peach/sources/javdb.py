"""javdb 的作品页。

搜索页 `/search?q=<番号>&f=all` 的结果卡片带番号，番号一致的那张进详情页；详情页
`?locale=zh` 下面板字段是 `番號`、`日期`、`片商`、`發行`、`系列`、`導演`、`演員`，
演员里女优带 `actor-female`，类别保留链接里的原词。它按出口 IP 计配额，限速与封禁的处理在 `scraping_access`
和调用方的主机间隔里，这里只管取页与解析。几家的值常有出入：ABW-358 在 javdb 上发行日期是
MGS 的 5/23、标题带 MGS 附注、演员里有男优，各字段按 Peach 的来源策略结算。

一部分页面要登录才给，回的是登入页而不是 401/403（`peach.javdb.LOGIN`），搜索页与详情页都要认。
"""
from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

from ..catalog_rules import same_release_code
from ..javdb import LOGIN, clean
from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure

#: 主机间隔由用户定（2026-09-22 定为 3 秒，每分钟 20 页）。参照面：每分钟 40～50 页会招来 3～7 天
#: 的封 IP。页面与图分别落在 `javdb.com` 与 `jdbstatic.com` 两个主机上，间隔要一起管。
JAVDB = SiteConfig(name="javdb", label="javdb", provider="javdb-page",
                   base_url="https://javdb.com", domains=("javdb.com", "jdbstatic.com", "jdbimgs.com"),
                   stage="community", interval=3.0, cookie=True)

_BOX = re.compile(r'<a href="(/v/[A-Za-z0-9]+)" class="box" title="[^"]*">.*?<strong>([^<]+)</strong>', re.S)
_PANEL = re.compile(r'<div class="panel-block[^"]*">\s*<strong>([^<:：]+)[:：]</strong>\s*(?:&nbsp;)?\s*'
                    r'<span class="value">(.*?)</span>', re.S)
_TITLE = re.compile(r'<strong class="current-title">([^<]*)</strong>')
#: 详情页那张封面。认标签、再从标签里取 `src`，不把属性挨着写死：站上 `class` 与 `src` 的先后
#: 两种写法都出现过，中间还可能夹着 `width`、`height` 与 `fetchpriority`。
_COVER = re.compile(r'<img\b[^>]*\bclass="video-cover"[^>]*>')
_IMG_SRC = re.compile(r'\bsrc="(https://[^"]+)"')
_ACTRESS = re.compile(r'<a\s([^>]*)>([^<]+)</a>')
#: 演員一栏里每个人名都挂着自己的资料页。那串 id 正是人物页 JavDB 入口要的东西，
#: 取名字时顺手带出来——另走一趟演员页只是把同一页再取一遍，而 javdb 的配额最紧。
_ACTOR_HREF = re.compile(r'href="/actors/([A-Za-z0-9]+)"')
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def actresses(value: str) -> list[dict]:
    """演員一栏里的女优：名字，以及她在 javdb 的演员 id。

    男优挂的是同样的 `/actors/` 链接，靠 `actor-female` 分开。href 与 class 在标签里的
    先后不固定，所以先取整段属性再判，不假设它们的次序。
    """
    found = []
    for attributes, name in _ACTRESS.findall(value):
        if "actor-female" not in attributes:
            continue
        actor = _ACTOR_HREF.search(attributes)
        found.append({"japanese_name": clean(name), "profile_source": "javdb",
                      "external_id": actor.group(1) if actor else ""})
    return found


def maker_writing(value: str) -> str:
    """片商一栏并列英文与日文（`PRESTIGE,プレステージ`），取日文那一种。"""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return next((part for part in parts if _JAPANESE.search(part)), parts[0] if parts else "")


class JavDBSource(SiteSource):
    DEFAULT = JAVDB

    def resources(self, code: str, *, session: Session) -> dict:
        """核对作品身份后合并资源区与评论链接；最多读取三页评论。"""
        with self.holding(session):
            page = self.fetch(code, session=session)
            self.parse(page, code)
            warnings = []
            try:
                items = parse_magnets(page)
            except SourceFailure as error:
                items = []
                warnings.append(str(error))
            soup = BeautifulSoup(page.text, "html.parser")
            items.extend(parse_comment_links(page))
            entry = soup.select_one('.review-tab[data-url]')
            next_url = str(entry.get("data-url", "")) if entry else ""
            seen = set()
            for _ in range(3):
                if not next_url:
                    break
                url = urllib.parse.urljoin(page.url, next_url)
                parts, original = urllib.parse.urlsplit(url), urllib.parse.urlsplit(page.url)
                if (parts.netloc != original.netloc or parts.scheme != "https"
                        or not parts.path.startswith(original.path + "/reviews/") or url in seen):
                    warnings.append("部分评论未取得，请打开来源页查看。")
                    break
                seen.add(url)
                try:
                    with self.holding(session):
                        comments = self._page(url, session=session)
                    body = BeautifulSoup(comments.text, "html.parser")
                    if not body.select_one('.review-items'):
                        raise SourceFailure(FailureReason.PARSE_ERROR, "评论区域未取得")
                    items.extend(parse_comment_links(comments))
                except Exception:
                    warnings.append("评论链接未取得，请打开来源页查看。")
                    break
                if body.select_one('.more a[href="/plans"]'):
                    warnings.append("来源只返回了部分评论，资源链接可能不完整。")
                following = body.select_one('a[rel="next"], .pagination-next:not([disabled])')
                next_url = str(following.get('href', '')) if following else ""
            else:
                if next_url:
                    warnings.append("已读取前三页评论，其余请打开来源页查看。")
            if not entry and not soup.select_one('.review-items'):
                warnings.append("评论入口未取得。")
            return {"items": merge_resources(items), "warnings": list(dict.fromkeys(warnings)),
                    "source_url": page.url}

    def search_url(self, code: str) -> str:
        return f"{self.config.base_url}/search?q={urllib.parse.quote(code)}&f=all"

    def _page(self, url: str, *, session: Session) -> Page:
        page = session.get(url, config=self.config)
        if LOGIN.search(page.text):
            raise SourceFailure(FailureReason.AUTH_REQUIRED, "javdb 要求登录")
        return page

    def fetch(self, code: str, *, session: Session) -> Page:
        search = self._page(self.search_url(code), session=session)
        path = next((path for path, shown in _BOX.findall(search.text)
                     if same_release_code(code, clean(shown))), None)
        if path is None:
            raise SourceFailure(FailureReason.NOT_FOUND, "javdb 没有这个番号")
        detail = self._page(self.config.base_url + path + "?locale=zh", session=session)
        # `source_url` 记不带界面语言参数的详情页地址。
        return Page(self.config.base_url + path, detail.body)

    def parse(self, page: Page, code: str) -> SiteRecord:
        text = page.text
        panel = {clean(label): value for label, value in _PANEL.findall(text)}
        shown = clean(panel.get("番號", "")).replace(" ", "")
        if not same_release_code(code, shown):
            raise SourceFailure(FailureReason.PARSE_ERROR, "javdb 详情页的番号与搜索结果不一致")
        runtime = re.search(r"\d+", clean(panel.get("時長", "")))
        title = _TITLE.search(text)
        tag = _COVER.search(text)
        cover = _IMG_SRC.search(tag.group(0)) if tag else None
        genres = tuple(dict.fromkeys(
            clean(link.get_text(" ", strip=True))
            for link in BeautifulSoup(panel.get("類別", ""), "html.parser").find_all("a")
            if clean(link.get_text(" ", strip=True))))
        return SiteRecord(
            source=self.config.name, provenance=self.config.provider, code=shown, source_url=page.url,
            title=clean(title.group(1)) if title else "",
            performers=tuple(actresses(panel.get("演員", ""))),
            studio=maker_writing(clean(panel.get("片商", ""))), label=clean(panel.get("發行", "")),
            series=clean(panel.get("系列", "")), director=clean(panel.get("導演", "")),
            release_date=clean(panel.get("日期", "")), runtime=int(runtime.group()) if runtime else None,
            tags=genres,
            cover_urls=(cover.group(1),) if cover else ())


def parse_magnets(page: Page) -> list[dict]:
    """JavDB 磁链区：名称、大小、文件数、标签与收录日期；按 infohash 去重。"""
    from ..downloads import parse_magnet

    soup = BeautifulSoup(page.text, "html.parser")
    container = soup.select_one("#magnets-content")
    if container is None:
        raise SourceFailure(FailureReason.PARSE_ERROR, "JavDB 磁链区域未取得")
    found: dict[str, dict] = {}
    invalid = False
    for row in container.select(".item"):
        link = row.select_one('a[href^="magnet:"]')
        if link is None:
            continue
        try:
            magnet = parse_magnet(str(link.get("href", "")))
        except ValueError:
            invalid = True
            continue
        def text(selector, row=row):
            node = row.select_one(selector)
            return node.get_text(" ", strip=True) if node else ""
        meta = re.split(r"[,，]", text(".meta"), maxsplit=1)
        found.setdefault(magnet.info_hash, {
            "id": "magnet:" + magnet.info_hash, "protocol": "magnet",
            "info_hash": magnet.info_hash, "uri": magnet.uri,
            "name": text(".name") or magnet.name or magnet.info_hash,
            "size": meta[0].strip(), "files": meta[1].strip() if len(meta) > 1 else "",
            "date": text(".time") or text(".date"),
            "attributes": list(dict.fromkeys(tag.get_text(" ", strip=True)
                                             for tag in row.select(".tag") if tag.get_text(strip=True))),
            "source": "JavDB", "source_url": page.url, "origins": [page.url],
        })
    if invalid and not found:
        raise SourceFailure(FailureReason.PARSE_ERROR, "JavDB 磁链格式无法识别")
    return list(found.values())


_COMMENT_LINK = re.compile(r"(?:magnet:\?[^\s<>\"']+|ed2k://\|file\|[^\r\n<>]+?\|/|https?://[^\s<>\"']+)", re.I)
_ED2K = re.compile(r"ed2k://\|file\|([^|]+)\|(\d+)\|([a-f0-9]{32})\|(?:[^\r\n]*?\|)?/", re.I)


def parse_comment_links(page: Page) -> list[dict]:
    """只取评论正文的链接，跳过作者主页、点赞与站点推广入口。"""
    found = []
    soup = BeautifulSoup(page.text, "html.parser")
    for row in soup.select('.review-item'):
        content = row.select_one('.content')
        if content is None:
            continue
        links = [str(a.get('href', '')) for a in content.select('a[href]')]
        links.extend(_COMMENT_LINK.findall(content.get_text(' ', strip=True)))
        date = row.select_one('.time')
        # 片段引用保留评论 id；正文中的任意 URL 都不会被服务器追踪请求。
        origin = page.url + ("#" + str(row['id']) if row.get('id') else "")
        found.extend(comment_resources(links, origin, date.get_text(' ', strip=True) if date else ""))
    return merge_resources(found)


def parse_comment_text(text: str, origin: str, date: str = "") -> list[dict]:
    """匿名评论正文与网页评论使用同一资源解析。"""
    return comment_resources(_COMMENT_LINK.findall(text), origin, date)


def comment_resources(links: list[str], origin: str, date: str) -> list[dict]:
    from ..downloads import parse_magnet

    found = []
    for uri in links:
        uri = uri.strip()
        item = {"id": "", "protocol": "url", "info_hash": "", "uri": uri,
                "name": uri, "size": "", "files": "", "date": date,
                "attributes": [], "source": "JavDB 评论", "source_url": origin, "origins": [origin]}
        if uri.lower().startswith('magnet:'):
            try:
                magnet = parse_magnet(uri)
            except ValueError:
                continue
            item.update(id="magnet:" + magnet.info_hash, protocol="magnet", info_hash=magnet.info_hash,
                        name=magnet.name or magnet.info_hash, uri=magnet.uri)
        elif uri.lower().startswith('ed2k:'):
            match = _ED2K.fullmatch(uri)
            if not match:
                continue
            name, size, digest = match.groups()
            item.update(id=f"ed2k:{digest.lower()}:{int(size)}", protocol="ed2k",
                        name=urllib.parse.unquote(name), size=f"{int(size):,} B")
        else:
            try:
                url = urllib.parse.urlsplit(uri)
                if url.scheme.lower() not in {'http', 'https'} or not url.hostname or url.username or url.password:
                    continue
                canonical = urllib.parse.urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path or '/', url.query, url.fragment))
            except ValueError:
                continue
            item.update(id="url:" + canonical, uri=canonical)
        found.append(item)
    return merge_resources(found)


def merge_resources(items: list[dict]) -> list[dict]:
    """同一资源保留完整属性，并收集它在资源区和评论中的全部出处。"""
    unique: dict[str, dict] = {}
    for item in items:
        if item['id'] not in unique:
            unique[item['id']] = {**item, "origins": list(item['origins'])}
            continue
        current = unique[item['id']]
        current['origins'] = list(dict.fromkeys([*current['origins'], *item['origins']]))
        current['attributes'] = list(dict.fromkeys([*current['attributes'], *item['attributes']]))
        for key in ('size', 'files'):
            if not current[key]:
                current[key] = item[key]
    return list(unique.values())
