"""r18.dev 的作品 JSON：DMM 数字版目录的镜像，有码与素人来源链的第一档。

`dvd_id=<番号>` 那一页只给英文，标题和系列多是机翻（ABW-358 的 `title_en_is_machine_translation`
为真），演员只有罗马字。日文在 `combined=<content_id>` 那一页：`title_ja`、`series_name_ja`、
演员 `name_kanji`，取到后写进 `translations`（形状与 Javinizer-Go 快照一致），标题、系列、演员与
genre 都换成日文那份。厂牌不取日文——账本的厂牌实体用品牌名（`Prestige`、`MOODYZ`），换成
`プレステージ` 会另起一个实体。

标题、系列和演员只认日文那一侧：日文那一页取不到，或那一侧空着，这几样就留空，不拿英文页的
写法顶上。英文页的这三样一旦落库，系列与演员就成了实体规范名——ABW-358 的系列实体因此叫了
`HOW TO SEX! The Infirmary Teacher…`，详情页一直显示英文。留空的字段由来源链接着问下一家；
发行日、厂牌、封面和 genre 两边一样可用，照旧交出，一次失败不吞掉整条资料。

genre 取 `categories[].name_ja`，也就是 DMM 自己那套词。英文是 r18 在它上面再译一层，词根在那
一层会丢：`その他フェチ` 一眼看得出是「フェチ」那一格的兜底，从 `Other Fetishes` 反推不回去。
取日文原词，一个词只登记一次就覆盖整个来源；取英文则每个写法都得另外逐条登记才追得平。

封面层另有一条到同一页的路（`jav_cover_fetch.r18_evidence`），只取图不取资料，不经这里。
"""
from __future__ import annotations

import json
from dataclasses import replace
from urllib.parse import quote

import httpx

from ..jav_cover_fetch import Unavailable
from ..metadata import identifies_code
from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure

#: 主机间隔用默认的 2 秒，不带 Cookie。两页实测都在几十 KB，2 MiB 是改版余量。
R18DEV = SiteConfig(name="r18dev", label="r18.dev", provider="r18-json", base_url="https://r18.dev",
                    domains=("r18.dev",), stage="official_mirror", page_limit=2 * 1024 * 1024)
DETAIL_PATH = "/videos/vod/movies/detail/-/dvd_id={code}/json"
COMBINED_PATH = "/videos/vod/movies/detail/-/combined={content_id}/json"
#: 女优头像：combined 页的 `actresses[].image_url` 只是文件名，图在 DMM 的头像目录下。
ACTRESS_IMAGE = "https://pics.dmm.co.jp/mono/actjpgs/{filename}"


def actresses(rows) -> list[dict]:
    """combined 页的演员：日文名、假名、罗马字、DMM id 与官方头像地址。

    `japanese_name` 只取 `name_kanji`，那是 DMM 登记的艺名（`RARA`、`JULIA` 这类本来就写拉丁
    字母的也在这一栏）。没有它的只剩 r18 拼的罗马字，名字留空，由 `with_japanese` 剔掉。
    """
    found = []
    for row in rows or []:
        image = str(row.get('image_url') or '').strip()
        thumb_url = (ACTRESS_IMAGE.format(filename=quote(image, safe=''))
                     if image and '/' not in image and '\\' not in image else '')
        found.append({
            'japanese_name': row.get('name_kanji') or '',
            'name_kana': row.get('name_kana') or '',
            'name_romaji': row.get('name_romaji') or '',
            'dmm_id': row.get('id') or '',
            'thumb_url': thumb_url,
            'profile_source': 'r18dev',
        })
    return found


def english_only(record: SiteRecord) -> SiteRecord:
    """日文那一页没拿到时交出的记录：标题、系列与演员留空，其余照旧。"""
    return replace(record, title='', series='', performers=())


def with_japanese(record: SiteRecord, combined: object) -> SiteRecord:
    """把 combined 页的日文写法并进记录；那一页不是这部片的，按没拿到处理。"""
    content_id = str(record.extra.get('content_id') or '')
    if not isinstance(combined, dict) or combined.get('content_id') != content_id:
        return english_only(record)
    directors = [row.get('name_kanji') for row in combined.get('directors') or [] if row.get('name_kanji')]
    title = ' '.join(str(combined.get('title_ja') or '').split())
    series = ' '.join(str(combined.get('series_name_ja') or '').split())
    translations = [dict(language='ja', title=title, series=series,
                         label=combined.get('label_name_ja') or '',
                         director=directors[0] if directors else '')]
    performers = tuple(row for row in actresses(combined.get('actresses')) if row['japanese_name'])
    japanese = [row.get('name_ja') or row.get('name_en') or '' for row in combined.get('categories') or []]
    return replace(
        record, title=title, series=series, performers=performers,
        tags=tuple(name for name in japanese if name) if any(japanese) else record.tags,
        extra={**record.extra, 'translations': translations, 'combined': combined})


class R18DevSource(SiteSource):
    DEFAULT = R18DEV

    def detail_url(self, code: str) -> str:
        return self.config.base_url + DETAIL_PATH.format(code=quote(code))

    def combined_url(self, content_id: str) -> str:
        return self.config.base_url + COMBINED_PATH.format(content_id=quote(content_id))

    def fetch(self, code: str, *, session: Session) -> Page:
        return session.get(self.detail_url(code), config=self.config)

    def parse(self, page: Page, code: str) -> SiteRecord:
        try:
            raw = json.loads(page.body)
        except ValueError:
            raise SourceFailure(FailureReason.PARSE_ERROR, "r18.dev 返回的不是作品 JSON") from None
        if not isinstance(raw, dict):
            raise SourceFailure(FailureReason.PARSE_ERROR, "r18.dev 返回的不是作品 JSON")
        if not identifies_code(code, {'content_id': raw.get('content_id')}):
            raise SourceFailure(FailureReason.PARSE_ERROR, '来源返回的番号不匹配')
        name = lambda key: (raw.get(key) or {}).get('name', '')
        jacket = (raw.get('images') or {}).get('jacket_image')
        return SiteRecord(
            source=self.config.name, provenance=self.config.provider, code=code, source_url=page.url,
            title=raw.get('title') or '',
            performers=tuple({'japanese_name': row.get('name', '')} for row in raw.get('actresses', [])),
            studio=name('maker'), label=name('label'), series=name('series'),
            director=raw.get('director') or '', release_date=raw.get('release_date') or '',
            runtime=raw.get('runtime_minutes'),
            tags=tuple(row.get('name', '') for row in raw.get('categories', [])),
            cover_urls=(jacket,) if jacket else (),
            extra={'content_id': raw.get('content_id'), 'raw': raw})

    def japanese(self, record: SiteRecord, *, session: Session) -> SiteRecord:
        """补上 combined 页的日文写法。那一页取不到、不是 JSON 或连接失败时交 `english_only`。"""
        content_id = str(record.extra.get('content_id') or '')
        if not content_id:
            return english_only(record)
        try:
            combined = json.loads(session.get(self.combined_url(content_id), config=self.config).body)
        except (Unavailable, ValueError, httpx.TransportError):
            return english_only(record)
        return with_japanese(record, combined)

    def query(self, code: str, *, session: Session) -> SiteRecord:
        return self.japanese(super().query(code, session=session), session=session)
