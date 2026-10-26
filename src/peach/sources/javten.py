"""JAVten（javten.com，前身 fc2hub.com）的 FC2 作品页：下架商品的另一份标题、标签、卖家与存储原件地址。

作品地址 `/video/<站内号>/id<video_id>/<标题>`，站内号拼不出来，先问 `/search?kw=<video_id>`：只有一条命中时
站方直接跳到作品页，否则回结果页，从里面挑 `id<video_id>` 那一条（同一部片列着 `/tw/`、`/en/`、`/ko/`
几个译文版，只取不带语言前缀的日文原页）。搜索也按标题里的数字命中，所以要按 `id<号>` 那一段认，不按
出现过就算。

作品页上：`<h1 class="fc2-id">` 是番号，`og:title` 是 `[FC2-PPV-<id>]标题`，`<meta name="description">` 里
`| By <卖家> | 55:00 |` 给卖家与时长，`videos:published_time` 是販売日，`a.badge[href*="/tag/"]` 是标签，
`og:image` 与 `a[data-fancybox]` 指向 `storage*.contents.fc2.com` 上卖家自己传的那张图——与发行方商品页是
同一个文件，下架后存储上那张可能已经删了，能不能用由封面层量过才知道。卖家在站上只有站内号，
没有发行方那边的 slug，所以不给 `seller_url`。

站上的中文是机器翻译（用户判定），只收日文原页：`fetch` 只取不带语言前缀的地址，`parse` 见到
`og:url` 带 `/tw/`、`/en/`、`/ko/` 就按结构对不上处置，不把译文写进标题。

站前是 Cloudflare 的 JS 验证，进法与 `fc2ppvdb` 相同：有浏览器的机器上由本机浏览器打开（ADR-0065），
没有时退回 httpx，请求带用户在采集设置里贴的 Cookie（`scraping_access.SOURCES['javten']`），整站 UA 与
用户的 Chrome 保持一致。
"""
from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure, challenge_page
from .fc2 import PAGE_LIMIT, fc2_record, runtime_minutes, storage_original, strip_code, text_of, unrecognised, video_id

#: 转载站，标题与标签由站方整理，按社区来源登记。主机间隔用默认的 2 秒。
JAVTEN = SiteConfig(name="javten", label="JAVten", provider="javten-page", base_url="https://javten.com",
                    domains=("javten.com",), stage="community", cookie=True, page_limit=PAGE_LIMIT)
SEARCH_PATH = "/search?kw={video_id}"

#: 作品地址：可带译文语言前缀；`id` 后面是商品号，再后面是标题。
_WORK = re.compile(r"https?://javten\.com/(?P<lang>(?:tw|en|ko)/)?video/(?P<inner>\d+)/id(?P<video>\d+)/(?P<slug>[^\"'\s#?]*)")
#: `<meta name="description">` 里卖家与时长那两段。
_DESCRIPTION = re.compile(r"\|\s*By\s+(?P<seller>.*?)\s*\|\s*(?P<runtime>\d{1,2}:\d{2}(?::\d{2})?)\s*\|")
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def links(html: str | bytes, code: str) -> list[str]:
    """搜索结果里这个商品号的日文原页地址，按站上先后去重；没有回空表。译文版折回原页。"""
    wanted = video_id(code)
    if not wanted:
        return []
    text = html.decode("utf-8", "replace") if isinstance(html, bytes) else str(html)
    found = []
    for match in _WORK.finditer(text):
        if match.group("video") == wanted:
            found.append(f"https://javten.com/video/{match.group('inner')}/id{wanted}/"
                         f"{urllib.parse.quote(urllib.parse.unquote(match.group('slug')), safe='')}")
    return list(dict.fromkeys(found))


def _meta(soup: BeautifulSoup, name: str) -> str:
    node = soup.select_one(f'meta[property="{name}"]') or soup.select_one(f'meta[name="{name}"]')
    return str(node.get("content") or "").strip() if node is not None else ""


def landing(html: str | bytes) -> re.Match | None:
    """这一页是哪部作品的页：搜索只有一条命中时站方直接跳到作品页，从 `og:url` 或 canonical 认。"""
    soup = BeautifulSoup(html.decode("utf-8", "replace") if isinstance(html, bytes) else str(html), "html.parser")
    canonical = soup.select_one('link[rel="canonical"][href]')
    for address in (_meta(soup, "og:url"), str(canonical.get("href") or "") if canonical is not None else ""):
        found = _WORK.match(address.strip())
        if found:
            return found
    return None


def _absolute(url: str) -> str:
    address = str(url or "").strip()
    return "https:" + address if address.startswith("//") else address


class JavtenSource(SiteSource):
    DEFAULT = JAVTEN

    def search_url(self, code: str) -> str:
        """这个番号的搜索地址；认不出商品号时回空串。"""
        found = video_id(code)
        return self.config.base_url + SEARCH_PATH.format(video_id=found) if found else ""

    def _missing(self) -> SourceFailure:
        return SourceFailure(FailureReason.NOT_FOUND, f"{self.config.label} 上没有这个商品")

    def fetch(self, code: str, *, session: Session) -> Page:
        """搜索一跳，必要时再取作品页一跳。站方跳到的若是译文页，再取一次日文原页。"""
        url = self.search_url(code)
        if not url:
            raise unrecognised()
        wanted = video_id(code)
        page = session.get(url, config=self.config)
        if challenge_page(page.body):
            return page
        landed = landing(page.body)
        if landed is not None and landed.group("video") == wanted:
            if not landed.group("lang"):
                return Page(landed.group(0), page.body)
            original = f"https://javten.com/video/{landed.group('inner')}/id{wanted}/{landed.group('slug')}"
            return session.get(original, config=self.config, referer=url)
        found = links(page.body, code)
        if not found:
            raise self._missing()
        return session.get(found[0], config=self.config, referer=url)

    def parse(self, page: Page, code: str) -> SiteRecord:
        """作品页 → 记录；对不上番号归 `not_found`，译文页与验证页各归各的档。"""
        wanted = video_id(code)
        if not wanted:
            raise unrecognised()
        if challenge_page(page.body):
            raise SourceFailure(FailureReason.CLOUDFLARE_CHALLENGE,
                                f"{self.config.label} 要求 Cloudflare 验证，请在采集设置里更新 Cookie",
                                status_code=403)
        soup = BeautifulSoup(page.text, "html.parser")
        heading = text_of(soup.select_one("h1.fc2-id"))
        if heading.upper() != f"FC2-PPV-{wanted}":
            raise self._missing()
        address = landing(page.body)
        if address is not None and address.group("lang"):
            raise SourceFailure(FailureReason.PARSE_ERROR, f"{self.config.label} 回的是译文页，只收日文原页")
        description = _DESCRIPTION.search(_meta(soup, "description"))
        published = _meta(soup, "videos:published_time")
        covers = [storage_original(_absolute(_meta(soup, "og:image")))]
        gallery = soup.select_one("a[data-fancybox][href]")
        if gallery is not None:
            covers.append(storage_original(_absolute(gallery.get("href"))))
        return fc2_record(
            self.config, wanted, address.group(0).replace("http://", "https://", 1) if address else page.url,
            title=strip_code(_meta(soup, "og:title") or text_of(soup.select_one("h1.card-title:not(.fc2-id)")), wanted),
            release_date=published[:10] if _DATE.match(published) else "",
            runtime=runtime_minutes(description.group("runtime")) if description else None,
            label=description.group("seller").strip() if description else text_of(soup.select_one('a[href*="/seller/"]')),
            tags=[text_of(link) for link in soup.select('a.badge[href*="/tag/"]')],
            cover_urls=list(dict.fromkeys(url for url in covers if url)))
