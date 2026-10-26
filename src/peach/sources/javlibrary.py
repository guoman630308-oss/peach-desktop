"""JavLibrary 的搜索与作品页；传输和人工验证复用当前浏览器会话。"""
from __future__ import annotations

from dataclasses import replace
import re
from time import monotonic
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..catalog_rules import same_release_code
from ..http import public_https_url
from ..jav_cover_fetch import BROWSER_REQUEST_TIMEOUT
from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure, challenge_page

JAVLIBRARY = SiteConfig(name='javlibrary', label='JavLibrary', provider='javlibrary-page',
    base_url='https://www.javlibrary.com', domains=('javlibrary.com',),
    stage='community', cookie=True, interval=3.0)
_CODE = re.compile(r'\b[A-Z][A-Z0-9]{1,14}-\d{2,7}\b', re.I)


def _text(node):
    return node.get_text(' ', strip=True) if node is not None else ''


def _soup(page):
    if challenge_page(page.body):
        raise SourceFailure(FailureReason.CLOUDFLARE_CHALLENGE,
                            'JavLibrary 需要在浏览器窗口中手动完成人机验证')
    soup = BeautifulSoup(page.text, 'html.parser')
    if soup.select_one('input[type="password"]'):
        raise SourceFailure(FailureReason.AUTH_REQUIRED, 'JavLibrary 需要在浏览器中登录')
    return soup


class JavLibrarySource(SiteSource):
    DEFAULT = JAVLIBRARY

    def _detail_url(self, href):
        address = urljoin(self.config.base_url + '/cn/', str(href or ''))
        parts = urlsplit(address)
        if (parts.scheme != 'https' or parts.netloc != urlsplit(self.config.base_url).netloc
                or not re.fullmatch(r'[a-zA-Z0-9]{6,32}', (parse_qs(parts.query).get('v') or [''])[0])):
            return None
        return address

    def fetch(self, code: str, *, session: Session) -> Page:
        started = monotonic()
        page = session.get(self.config.base_url + '/cn/vl_searchbyid.php?' + urlencode({'keyword': code}),
                           config=self.config)
        spent = monotonic() - started
        soup = _soup(page)
        if soup.select_one('#video_id .text'):
            canonical = soup.select_one('link[rel="canonical"][href], h3.post-title a[href]')
            address = self._detail_url(canonical.get('href')) if canonical else None
            return Page(address or page.url, page.body)
        for link in soup.select('div.video a[href]'):
            shown = _text(link.select_one('.id')) or str(link.get('title') or '')
            found = _CODE.search(shown)
            if not found or not same_release_code(code, found.group()):
                continue
            address = self._detail_url(link.get('href'))
            if address:
                # 等人验证的额外时长不占第二次取页的预算。
                if session.deadline is not None and spent > BROWSER_REQUEST_TIMEOUT:
                    session = replace(session, deadline=session.deadline + (spent - BROWSER_REQUEST_TIMEOUT))
                return session.get(address, config=self.config)
        if soup.select_one('div.videothumblist, div.videos, div.video'):
            raise SourceFailure(FailureReason.NOT_FOUND, 'JavLibrary 没有这个番号')
        raise SourceFailure(FailureReason.PARSE_ERROR, 'JavLibrary 搜索页结构未识别，请检查来源页面')

    def parse(self, page: Page, code: str) -> SiteRecord:
        soup = _soup(page)
        def field(name):
            return _text(soup.select_one(f'#{name} .text'))
        shown = field('video_id')
        if not shown:
            raise SourceFailure(FailureReason.PARSE_ERROR, 'JavLibrary 作品页缺少番号')
        if not same_release_code(code, shown):
            raise SourceFailure(FailureReason.NOT_FOUND, 'JavLibrary 返回的番号与查询番号不一致')
        title = _text(soup.select_one('h3.post-title a')).removeprefix(shown).strip()
        if not title:
            raise SourceFailure(FailureReason.PARSE_ERROR, 'JavLibrary 作品页缺少标题')
        runtime = re.search(r'\d+', field('video_length'))
        image = soup.select_one('#video_jacket_img[src]')
        raw_image = str(image.get('src') or '') if image else ''
        cover = urljoin(self.config.base_url, raw_image) if raw_image else ''
        if cover.startswith('http://'):
            cover = 'https://' + cover[len('http://'):]
        cover = cover if cover and public_https_url(cover) else ''
        performers = tuple(dict.fromkeys(_text(link) for link in soup.select('#video_cast .star a') if _text(link)))
        return SiteRecord(source=self.config.name, provenance=self.config.provider, code=shown,
            source_url=page.url, title=title, performers=tuple({'japanese_name': name} for name in performers),
            studio=field('video_maker'), label=field('video_label'), director=field('video_director'),
            release_date=field('video_date'), runtime=int(runtime.group()) if runtime else None,
            tags=tuple(dict.fromkeys(_text(link) for link in soup.select('#video_genres .genre a') if _text(link)))
                 if soup.select_one('#video_genres') else None,
            cover_urls=(cover,) if cover else ())
