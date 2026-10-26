"""Seesaa 上三个认人 Wiki：作品条目里的精确番号出演证据。

三站同一主机、同一套页面骨架（`#page-body .user-area`、站内搜索 `search?keywords=`、EUC-JP），
各有一份 `SiteConfig` 与一个类，取页层 `WikiPages`、缓存、限额与撞墙停网共用：

- 素人系総合 Wiki（`sougouwiki`，`/w/sougouwiki/`）：厂牌作品表，见下文。
- このAV女優の名前教えてwiki（`av_neme`，`/av_neme/`）：不用表格。系列页与月份归档页
  （`d/2021年4月`，一页 1.3 MB）里一部作品一个 `h5` 小节，标题是「品番| 系列名」，正文
  `<b>名前(女優名)</b>：<a>艺名</a>`，后面是 MGS 标题链接与「別名：素人名义 年龄 职业」。人物页
  的作品小节没有这一行。站方说明写着名字旁带「？」或「▲」是不确定。
- AV女優の名前特定wiki（`av_name`，`/av_name/`）：FANZA 素人与 FANZAビデオ 一个品番一页
  （`d/HISYO%2d005`，`-` 按 EUC-JP 页名编成 `%2d`），键值表「品番／配信品番／素人名義／
  出演女優／配信開始日／レーベル」；MGS 作品只挂在厂牌页上，一部一个 `h5` 小节加
  「出演女優: … ／タイトル: … ／配信日: …」列表，厂牌页只挂最新约 1100 件。没特定的写 `--`；
  没有人物页的名字后面跟一个指向 `e/add` 的「?」。人物页的作品小节没有「出演女優」行。

两个认人 Wiki 的站内搜索是全文子串匹配（`AR-101` 会命中 `GAR-101`、`STAR-101`），结果里
单品番页排最前，其余按站给的顺序读；只认带出演行的小节或键值表，人物页的履历不算。
2026-09-24 实测两站都查不到 FC2-PPV 与韩国 MIB（`AR-101` 两站均无该作品）。

素人系総合 Wiki 的厂牌页是一张 `NO` / `PHOTO` / `TITLE` / `ACTRESS` / `RELEASE` / `NOTE` 的作品表，一页几十上百行，
一行一部作品。`rows()` 把一页的每一行各读成一份 `SiteRecord`，读过的页记在 `tables` 里，后面的番号
先在那里找：`--wiki-pages-file` 预取的目录页上全部作品复用同一次请求。找不到才问站内搜索，结果页
逐页读，人物页上「レーベル一覧」链到的厂牌页再读。

身份与出演只认作品表：番号按 `NO` 列经 `same_release_code` 精确回配（`390JAC-040` 与 `JAC-040`
是两件商品、两行），推荐、评论与人物页的履历不算整片出演证据。`ACTRESS` 栏里带人物页链接的
显示名才是出演候选，栏里还剩认不出的名字、`？`、`不明`、`未確認` 时整份名单只留原文证据。

取页层 `WikiPages` 是这一站的 `Session.transport`：不收凭据，成功页落盘缓存可续跑，请求限额与
撞墙停网只在本批内。它抛的 `MetadataProviderError` 与链上站的 `SourcePaused` 一样是传输层信号，
契约原样放过；分档（`budget`、`blocked`、`size_limit`、`redirect`、`ambiguous`、`incomplete_search`
等）落进 `scrape_codes` 的错误表与健康表，`budget` 与 403/429 还决定本批停网，所以不折成
`FailureReason` 的三档。搜索范围内没找到是 `incomplete_search` 且可重试：没问到不等于站上没有，
不能按 `not_found` 冻成定论。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from ..catalog_rules import normalise_code_key, same_release_code
from ..http import HttpRequest, HttpxTransport, body_text
from ..metadata import MetadataProviderError, auth_error, auth_wall_reason, validate_provider_code
from ..scripting import HostLimiter
from .base import Page, Session, SiteConfig, SiteRecord, SiteSource

#: 社区 Wiki，按社区来源登记。主机间隔用默认的 2 秒，单页上限用默认的 4 MiB（真实 Flower 页
#: 253975 字节）。不带 Cookie，也不在 `scraping_access.SOURCES` 里：采集设置页没有这一站的卡片。
SEESAA = SiteConfig(name="sougouwiki", label="素人系総合 Wiki", provider="sougouwiki",
                    base_url="https://seesaawiki.jp", domains=("seesaawiki.jp",), stage="community")
ROOT = SEESAA.base_url + "/w/sougouwiki/"
#: 两个认人 Wiki。与素人系総合 Wiki 同主机，`scrape_codes` 让几站共用一个主机间隔。
AV_NEME = SiteConfig(name="av_neme", label="このAV女優の名前教えてwiki", provider="av_neme",
                     base_url="https://seesaawiki.jp", domains=("seesaawiki.jp",), stage="community")
AV_NAME = SiteConfig(name="av_name", label="AV女優の名前特定wiki", provider="av_name",
                     base_url="https://seesaawiki.jp", domains=("seesaawiki.jp",), stage="community")
#: 本批 HTTP 请求上限的默认值（`scrape_codes --wiki-max-requests`）。
MAX_REQUESTS = 80
#: 单次请求的超时（秒）。
TIMEOUT = 20
#: 搜索结果取前几条，人物页上的厂牌目录至多再读几页。
SEARCH_PAGES = 8
LABEL_PAGES = 4

REVIEW_WARNING = '社区 Wiki 的出演断言需要人工复核；人物页链接不自动合并艺名'
INCOMPLETE_WARNING = '出演栏含未识别或不确定内容，完整出演名单未取得'
NOT_IN_SEARCH = 'Wiki 搜索范围内未取得精确作品表格行'
NOT_IN_ENTRIES = 'Wiki 搜索范围内未取得精确番号的作品条目'
#: 出演栏里这些写法说明名单不确定，整份名单只留原文证据。`▲` 是 av_neme 站方说明里的不确定标记。
_UNCERTAIN_NAME = re.compile(r'[?？�▲]|不明|未確認')
#: 链接名摘掉之后剩下的分隔符与「出演順」不算未识别内容。
_SEPARATORS = re.compile(r'[\s／/、,・]+|出演順')
_PHOTO = re.compile(r'\.(?:jpg|png|webp)(?:\?|$)', re.I)
#: 认人 Wiki 的出演行开头：av_name 写「出演女優:」，av_neme 写「名前(女優名)：」。
_CAST_LABEL = re.compile(r'^\s*(?:出演女優|名前\s*[(（]女優名[)）])\s*[:：]?')
#: av_name 在名字后面标假名读音（`玉木くるみ（たまき くるみ）`），不算未识别内容。
_READING = re.compile(r'[（(][ぁ-ゟー\s]+[）)]')
#: av_name 的人物页名带另一种写法（`羽生アリサ（羽生ありさ）`），艺名取括号前那段。
_ALT_SPELLING = re.compile(r'^(.+?)\s*[（(][^（）()]+[）)]$')


def page_url(url: str, root: str = ROOT) -> str:
    """仅接受 `root` 这个 Wiki 的公开详情页，保留 EUC-JP 百分号编码。"""
    absolute = urljoin(root, url).split('#', 1)[0]
    parsed = urlsplit(absolute)
    if (parsed.scheme != 'https' or parsed.netloc != 'seesaawiki.jp'
            or not parsed.path.startswith(urlsplit(root).path + 'd/') or parsed.query):
        raise ValueError('需要这个 Wiki 的公开详情页')
    return absolute


def _soup(body: bytes) -> BeautifulSoup:
    # 这个 Wiki 两种编码的页面并存，声明写在页面自己的 `<meta>` 里；不声明的按
    # EUC-JP 解（站点的历史编码）。判据走共享的 `body_text`，不在这里另写一份。
    return BeautifulSoup(body_text(body, default='euc_jp'), 'html.parser')


def _signature(row: SiteRecord) -> str:
    return json.dumps({'title': row.title, 'actresses': [dict(p) for p in row.performers],
                       'release_date': row.release_date}, ensure_ascii=False, sort_keys=True)


def _one(code: str, rows) -> SiteRecord | None:
    """`rows` 里这个番号的那一行；没有回 `None`，几行说法不一报 `ambiguous`。"""
    matches = [row for row in rows if same_release_code(code, row.code)]
    if not matches:
        return None
    if len({_signature(row) for row in matches}) != 1:
        raise MetadataProviderError('Wiki 精确番号存在冲突行，需复核原页', kind='ambiguous')
    return matches[0]


class WikiPages:
    """这一站的取页层，一批共用一个：成功页落盘缓存、请求限额、撞墙后整批停网。

    缓存键是地址的 SHA-256，一页一个 `{url, body_hex}` 的 JSON，`--refresh` 才重取。403、429、
    登录墙与机器人验证之后本批不再联网，后面每一页都报 `budget`。
    """

    def __init__(self, cache_dir: Path, *, config: SiteConfig = SEESAA, transport=None, limiter=None,
                 max_requests: int = MAX_REQUESTS, refresh: bool = False):
        self.cache_dir = Path(cache_dir)
        self.config = config
        self.transport = transport or HttpxTransport()
        self.limiter = limiter or HostLimiter({}, default_interval=config.interval)
        self.max_requests = max_requests
        self.refresh = refresh
        self.requests = 0
        self.blocked = False

    def get(self, url: str) -> Page:
        limit = self.config.page_limit
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache_dir / (key + '.json')
        if not self.refresh and path.is_file():
            try:
                cached = json.loads(path.read_text(encoding='utf8'))
                body = bytes.fromhex(cached['body_hex'])
                if cached['url'] == url and len(body) <= limit:
                    return Page(url, body)
            except (ValueError, KeyError, TypeError):
                pass
        if self.blocked or self.requests >= self.max_requests:
            raise MetadataProviderError('Wiki 本批请求已停止或达到限额', kind='budget', retryable=True)
        self.limiter.wait(url)
        self.requests += 1
        try:
            response = self.transport(HttpRequest('GET', url, {}), TIMEOUT, limit)
        except (OSError, RuntimeError, httpx.HTTPError) as error:
            raise MetadataProviderError('Wiki 网络请求未取得', kind='unavailable', retryable=True) from error
        if response.status in {403, 429}:
            self.blocked = True
        # 鉴权失败先判：401/403 与「跳到登录页」都是同一把锁，本批再问多少次都是它。
        # 判成 unavailable 的话它会被当成网络抖动，几百个番号各自再撞一次。
        reason = auth_wall_reason(status_code=response.status, final_url=response.url)
        if reason:
            self.blocked = True
            raise auth_error(self.config.name, reason, status_code=response.status)
        if response.status != 200:
            raise MetadataProviderError('Wiki HTTP 请求未取得', kind='unavailable',
                                        status_code=response.status, retryable=True)
        if len(response.body) > limit:
            raise MetadataProviderError('Wiki 页面超过读取上限', kind='size_limit')
        if response.url and response.url != url:
            raise MetadataProviderError('Wiki 重定向页面未取得身份验证', kind='redirect')
        if b'cf-chl-' in response.body or b'Just a moment' in response.body:
            self.blocked = True
            raise MetadataProviderError('Wiki 机器人验证未取得', kind='blocked', status_code=403)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'url': url, 'body_hex': response.body.hex()}), encoding='utf8')
        temp.replace(path)
        return Page(url, response.body)

    def close(self) -> None:
        close = getattr(self.transport, 'close', None)
        if close:
            close()


class SeesaaSource(SiteSource):
    """一批一个实例：`tables` 记着读过的作品表，`session.transport` 须是 `WikiPages`。

    `pages` 是预取的目录页，每个番号先把它们读进 `tables` 再找。
    """

    DEFAULT = SEESAA
    #: 这个 Wiki 的根地址；详情页都在它下面的 `d/`。
    root = ROOT
    #: 人物页上「レーベル一覧」链到的厂牌页至多再读几页。
    label_pages = LABEL_PAGES
    #: 搜索范围内没找到时的措辞。
    not_found = NOT_IN_SEARCH

    def __init__(self, config: SiteConfig | None = None, *, pages=()) -> None:
        super().__init__(config)
        self.pages = tuple(self.page_url(url) for url in pages)
        #: 读过的页 → 页上作品表的每一行。
        self.tables: dict[str, list[SiteRecord]] = {}
        self._read: dict[str, Page] = {}

    def page_url(self, url: str) -> str:
        return page_url(url, self.root)

    def search_url(self, code: str) -> str:
        return self.root + 'search?' + urlencode({'keywords': code})

    def _rank(self, code: str, links: list[str]) -> list[str]:
        """搜索结果的读取顺序：地址不带百分号编码的是厂牌目录，排在前面。"""
        return sorted(links, key=lambda url: '%' in url)

    def _stage_name(self, name: str) -> str:
        return name

    def _residual(self, text: str) -> str:
        return text

    def _cast(self, cell) -> tuple[list[dict], list[dict], str]:
        """出演栏里带本站人物页链接的名字、它们的链接，和摘掉这些名字之后剩下的未识别内容。"""
        performers, person_links = [], []
        residual = BeautifulSoup(str(cell), 'html.parser')
        for anchor in cell.select('a[href]'):
            name = anchor.get_text(' ', strip=True)
            try:
                target = self.page_url(anchor['href'])
            except ValueError:
                continue
            if not name or _UNCERTAIN_NAME.search(name):
                continue
            performers.append({'japanese_name': self._stage_name(name)})
            person_links.append({'name': name, 'source_url': target})
            for link in residual.select('a'):
                if link.get_text(' ', strip=True) == name:
                    link.decompose()
        return performers, person_links, _SEPARATORS.sub('', self._residual(residual.get_text()))

    def rows(self, page: Page) -> list[SiteRecord]:
        """页上作品表的每一行各一份记录。只认带 `NO` 与 `ACTRESS` 列名的表，合并单元格的行不取。"""
        url = self.page_url(page.url)
        area = _soup(page.body).select_one('#page-body .user-area')
        if area is None:
            return []
        records = []
        for table in area.select('table'):
            header = table.find('tr')
            if header is None:
                continue
            columns = [c.get_text(' ', strip=True).upper() for c in header.find_all(['th', 'td'], recursive=False)]
            if 'NO' not in columns or 'ACTRESS' not in columns:
                continue
            for row_index, row in enumerate(table.find_all('tr')[1:], 1):
                cells = row.find_all(['td', 'th'], recursive=False)
                if len(cells) != len(columns) or any(c.has_attr('rowspan') or c.has_attr('colspan') for c in cells):
                    continue
                record = self._row(url, table, row_index, dict(zip(columns, cells)))
                if record is not None:
                    records.append(record)
        return records

    def _row(self, url: str, table, row_index: int, by_name: dict) -> SiteRecord | None:
        try:
            code = validate_provider_code(normalise_code_key(by_name['NO'].get_text(' ', strip=True)))
        except ValueError:
            return None
        actress = by_name['ACTRESS']
        performers, person_links, remainder = self._cast(actress)
        warnings = [REVIEW_WARNING]
        if remainder:
            warnings.append(INCOMPLETE_WARNING)
        evidence = {'table': str(table.get('id') or ''), 'row': row_index,
                    'performer_links': person_links, 'performers_complete': not bool(remainder),
                    'actress_text': actress.get_text(' ', strip=True)}
        text = lambda column: by_name[column].get_text(' ', strip=True) if column in by_name else ''
        if 'NOTE' in by_name:
            evidence['notes'] = text('NOTE')
        product = by_name['NO'].find('a', href=True)
        if product:
            evidence['product_url'] = product['href']
        covers = ()
        if 'PHOTO' in by_name:
            covers = tuple(anchor['href'] for anchor in by_name['PHOTO'].select('a[href]')
                           if _PHOTO.search(anchor['href']))[:1]
        return SiteRecord(source=self.config.name, provenance=self.config.provider, code=code, source_url=url,
                          title=text('TITLE'), performers=tuple(performers) if not remainder else (),
                          release_date=text('RELEASE').replace('/', '-'), cover_urls=covers,
                          extra={'source_warnings': warnings, 'wiki_evidence': evidence})

    def remember(self, page: Page) -> list[SiteRecord]:
        """把一页的作品表记进 `tables`，后面的番号先在这里找。"""
        url = self.page_url(page.url)
        self._read[url] = page
        self.tables[url] = self.rows(page)
        return self.tables[url]

    def load(self, url: str, *, session: Session) -> list[SiteRecord]:
        """读一页作品表；读过的不再取。"""
        url = self.page_url(url)
        if url not in self.tables:
            self.remember(session.transport.get(url))
        return self.tables[url]

    def match(self, code: str) -> SiteRecord | None:
        """读过的全部作品表里这个番号的那一行；几张表说法不一（标题、出演、日期任一不同）报 `ambiguous`。"""
        return _one(code, [row for rows in self.tables.values() for row in rows])

    def _located(self, code: str) -> Page | None:
        found = self.match(code)
        return self._read[found.source_url] if found else None

    def fetch(self, code: str, *, session: Session) -> Page:
        """载有这个番号那一行的作品表页。

        先读预取的目录页；读过的表里没有就问站内搜索，结果按厂牌页在前（地址不带百分号编码的
        是厂牌目录）取前几条逐页读。厂牌页能同时核验合集的全体出演者；人物页只用来发现它链到
        的「レーベル一覧」，那几页再读，不拿人物页的履历当整片出演名单。
        """
        code = validate_provider_code(code)
        for url in self.pages:
            self.load(url, session=session)
        found = self._located(code)
        if found:
            return found
        links = []
        for anchor in _soup(session.transport.get(self.search_url(code)).body).select('.result-box .body h3 a[href]'):
            try:
                links.append(self.page_url(anchor['href']))
            except ValueError:
                continue
        links = self._rank(code, list(dict.fromkeys(links)))
        label_visits = 0
        for url in links[:SEARCH_PAGES]:
            page = session.transport.get(url)
            self.remember(page)
            found = self._located(code)
            if found:
                return found
            for anchor in _soup(page.body).select('#page-body .user-area a[href]'):
                if 'レーベル一覧' not in anchor.get_text():
                    continue
                try:
                    target = self.page_url(anchor['href'])
                except ValueError:
                    continue
                if target not in self.tables:
                    if label_visits >= self.label_pages:
                        break
                    label_visits += 1
                    self.load(target, session=session)
                found = self._located(code)
                if found:
                    return found
        raise MetadataProviderError(self.not_found, kind='incomplete_search', retryable=True)

    def parse(self, page: Page, code: str) -> SiteRecord:
        """页上这个番号的那一行。"""
        code = validate_provider_code(code)
        url = self.page_url(page.url)
        rows = self.tables[url] if self._read.get(url) is page else self.rows(page)
        found = _one(code, rows)
        if found is None:
            raise MetadataProviderError(self.not_found, kind='incomplete_search', retryable=True)
        return found


def _cast_line(body):
    """一个作品小节里的出演行；没有出演行（人物页的履历小节）回 `None`。

    av_name 是列表里「出演女優:」那一项；av_neme 是 `<b>名前(女優名)</b>` 所在的那一段，
    一直读到下一个 `<br>`，名字后面跟的「？」「▲」也在这一段里。
    """
    for item in body.find_all('li'):
        if _CAST_LABEL.match(item.get_text()):
            return item
    for bold in body.find_all('b'):
        if not _CAST_LABEL.match(bold.get_text()):
            continue
        top = bold
        while top.parent is not None and top.parent is not body:
            top = top.parent
        parts = [str(top)]
        for sibling in top.next_siblings:
            if getattr(sibling, 'name', None) == 'br':
                break
            parts.append(str(sibling))
        return BeautifulSoup(''.join(parts), 'html.parser')
    return None


class NameWikiSource(SeesaaSource):
    """两个认人 Wiki 的共同读法：单品番页的键值表，与厂牌、系列、月份页上的 `h5` 作品小节。

    只产出出演候选：标题、日期、素人名义这些都只进证据（`wiki_evidence`），`TITLE` 在 av_name
    厂牌页上是截断的，拿它当标题候选只会添乱。人物页不跟读——站内搜索是全文检索，载有这个番号的
    系列页与月份页本身就在结果里。
    """

    label_pages = 0
    not_found = NOT_IN_ENTRIES

    def _page_name(self, url: str) -> str:
        return unquote(urlsplit(url).path.rsplit('/d/', 1)[-1], encoding='euc_jp', errors='replace')

    def _rank(self, code: str, links: list[str]) -> list[str]:
        """页名就是这个番号的单品番页排最前，其余照站给的顺序。"""
        return sorted(links, key=lambda url: not same_release_code(code, self._page_name(url)))

    def _stage_name(self, name: str) -> str:
        match = _ALT_SPELLING.match(name)
        return match.group(1) if match else name

    def _residual(self, text: str) -> str:
        return _READING.sub('', _CAST_LABEL.sub('', text))

    def rows(self, page: Page) -> list[SiteRecord]:
        url = self.page_url(page.url)
        area = _soup(page.body).select_one('#page-body .user-area')
        if area is None:
            return []
        records = []
        for table in area.select('table'):
            fields = {}
            for row in table.find_all('tr'):
                head, cell = row.find('th'), row.find('td')
                if head is not None and cell is not None:
                    fields.setdefault(head.get_text(strip=True), cell)
            if '品番' in fields and '出演女優' in fields:
                details = {key: cell.get_text(' ', strip=True) for key, cell in fields.items() if key != '出演女優'}
                record = self._entry(url, fields['品番'].get_text(' ', strip=True), fields['出演女優'],
                                     {'table': str(table.get('id') or ''), 'details': details})
                if record is not None:
                    records.append(record)
        for block in area.select('.wiki-section-3'):
            heading, body = block.select_one('.title-3 h5'), block.select_one('.wiki-section-body-3')
            cast = _cast_line(body) if heading is not None and body is not None else None
            if cast is None:
                continue
            record = self._entry(url, heading.get_text(' ', strip=True).split('|', 1)[0], cast,
                                 {'section': str(heading.get('id') or ''),
                                  'entry_text': body.get_text(' ', strip=True)[:1000]})
            if record is not None:
                records.append(record)
        return records

    def _entry(self, url: str, code_text: str, cast, evidence: dict) -> SiteRecord | None:
        try:
            code = validate_provider_code(normalise_code_key(re.sub(r'\s+', '', code_text)))
        except ValueError:
            return None
        performers, person_links, remainder = self._cast(cast)
        warnings = [REVIEW_WARNING]
        if remainder:
            warnings.append(INCOMPLETE_WARNING)
        evidence = {**evidence, 'performer_links': person_links, 'performers_complete': not bool(remainder),
                    'actress_text': _CAST_LABEL.sub('', cast.get_text(' ', strip=True)).strip()}
        return SiteRecord(source=self.config.name, provenance=self.config.provider, code=code, source_url=url,
                          performers=tuple(performers) if not remainder else (),
                          extra={'source_warnings': warnings, 'wiki_evidence': evidence})


class AvNemeSource(NameWikiSource):
    """このAV女優の名前教えてwiki。"""

    DEFAULT = AV_NEME
    root = AV_NEME.base_url + '/av_neme/'


class AvNameSource(NameWikiSource):
    """AV女優の名前特定wiki。"""

    DEFAULT = AV_NAME
    root = AV_NAME.base_url + '/av_name/'


#: av_neme 人物页「プロフィール」一节里的名字栏。两种排法并存：`<pre>` 里逐行写
#: 「名前(女優名)：主名」「旧名義&別名：甲・乙」，或一张表的「名前(別名)」一格写「主名／甲／乙」。
#: 作品小节里的「別名：そら 18歳 本屋の店員」是那部片给她起的素人名义，标签不同，不在这里。
PERSON_MAIN_LABEL = '名前(女優名)'
PERSON_NAME_LABELS = ('旧名義&別名', '名前(別名)')
_LABEL_FOLD = str.maketrans({'（': '(', '）': ')', '＆': '&', '：': ':'})
_PROFILE_LINE = re.compile(r'^([^:]{2,12}):(.+)$')
#: `&` 连的是两个名字：`橋本真紀&桧山彩音（舞ワイフ名義）` 是她在同一渠道用过的两个艺名。
_NAME_SPLIT = re.compile(r'[／/、,，&＆]')
#: 名字后面的读音与注记：`美雲そら（みくもそら）`、`雫つむぎ(FC2)`、`美雲そら【旧名】`。
_ANNOTATION = re.compile(r'[（(【][^（()）【】]*[）)】]')
_KATAKANA_ONLY = re.compile(r'^[゠-ヿ]+$')


def _label(text: str) -> str:
    return re.sub(r'\s+', '', str(text or '')).translate(_LABEL_FOLD)


def split_names(value: str) -> list[str]:
    """名字栏的一格 → 逐个写法，读音与注记剥掉。

    分隔符有 `・`、`／`、`、`、`&` 几种。`・` 在外国人名里是名字的一部分（`キラ・クィーン`），
    两侧都是纯片假名时不拆。注记本身也是边界：篠田ゆう那页写成
    `篠崎ゆう子（しのざきゆうこ）橋本真紀&桧山彩音（舞ワイフ名義）・高木早希`，读音后面站方
    没写分隔符，只剥掉注记会把前后两个名字粘成一个。
    """
    names = []
    for chunk in _NAME_SPLIT.split(_ANNOTATION.sub('/', str(value or ''))):
        merged: list[str] = []
        for piece in (part.strip() for part in chunk.split('・')):
            if merged and _KATAKANA_ONLY.match(piece) and _KATAKANA_ONLY.match(merged[-1]):
                merged[-1] += '・' + piece
            elif piece:
                merged.append(piece)
        names.extend(merged)
    return names


def search_results(body: bytes) -> list[tuple[str, str, str]]:
    """站内搜索结果页 → [(页面地址, 页名, 摘要)]，按站给的顺序。

    摘要是站给的那几行正文（`p.text`），带 Wiki 标记原文。人物页的摘要开头是资料
    （`旧名義&別名`、`生年月日`），系列页与月份页的是作品小节（`名前(女優名)：[[…]]`），
    只看页名分不出的靠它分。
    """
    found = []
    for block in _soup(body).select('.result-box .body'):
        anchor = block.select_one('h3 a[href]')
        if anchor is None:
            continue
        text = block.select_one('p.text')
        found.append((str(anchor['href']), anchor.get_text(strip=True),
                      text.get_text(' ', strip=True) if text is not None else ''))
    return found


_RENAMED = re.compile(r'から【([^【】]+)】へ変更')


def renamed_to(page: Page) -> str:
    """改名页 → 她现在那一页的页名；不是改名页返回空串。

    她改艺名后，旧名那一页只剩一句「女優名が【旧名】から【新名】へ変更になりました」和一个
    去新页的链接（`叶芽ゆきな` → `桜美ゆきな`，`雪代一鳳` → `雪代美鳳`）。
    """
    area = _soup(page.body).select_one('#page-body .user-area')
    if area is None:
        return ''
    found = _RENAMED.search(area.get_text(' ', strip=True))
    return found.group(1).strip() if found else ''


def person_profile(page: Page) -> tuple[str, list[str]]:
    """人物页 → (主名, 名字栏列出的全部写法，主名在内)。不是人物页返回 ("", [])。

    是不是人物页看两件事：第一节标题是「プロフィール」，且名字栏里的主名就是这一页的页名。
    系列页与月份页的作品小节里也有「名前(女優名)：某某」，但那一页的页名不是她。
    """
    area = _soup(page.body).select_one('#page-body .user-area')
    block = area.select_one('.wiki-section-1') if area is not None else None
    title = block.select_one('.title-1') if block is not None else None
    if title is None or 'プロフィール' not in title.get_text():
        return '', []
    fields: dict[str, str] = {}
    for row in block.select('tr'):
        head, cell = row.find('th'), row.find('td')
        if head is not None and cell is not None:
            fields.setdefault(_label(head.get_text()), cell.get_text(' ', strip=True))
    for br in block.find_all('br'):
        br.replace_with('\n')
    for line in block.get_text('').splitlines():
        match = _PROFILE_LINE.match(line.strip().translate(_LABEL_FOLD))
        if match:
            fields.setdefault(_label(match.group(1)), match.group(2).strip())
    names = split_names(fields.get(PERSON_MAIN_LABEL, ''))[:1]
    for label in PERSON_NAME_LABELS:
        names.extend(split_names(fields.get(label, '')))
    names = list(dict.fromkeys(names))
    page_name = unquote(urlsplit(page.url).path.rsplit('/d/', 1)[-1], encoding='euc_jp',
                        errors='replace')
    if not names or re.sub(r'\s+', '', names[0]) != re.sub(r'\s+', '', page_name):
        return '', []
    return names[0], names


#: 来源名 → 站的类。`scrape_codes` 按它给每个点名的 Wiki 建一个实例与一个 `WikiPages`。
WIKI_SOURCES: dict[str, type[SeesaaSource]] = {
    SEESAA.name: SeesaaSource, AV_NEME.name: AvNemeSource, AV_NAME.name: AvNameSource,
}
