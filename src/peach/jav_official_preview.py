"""官方商品已声明的预告；媒体地址与作品身份同时核对。"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .catalog_rules import normalise_code_key
from .jav_cover_fetch import HostLimitedTransport, PRESTIGE_MEDIA, PRESTIGE_PRODUCT, _fetch
from .javdb_public import PublicSourceUnavailable
from .scraping_access import SourceTransport
from .sources.base import FailureReason, Session, SourceFailure
from .sources.dmm import DmmSource, code_parts, matching_cids
from .sources.javdb import JAVDB, JavDBSource

PRESTIGE_SKU = 'https://www.prestige-av.com/api/sku/item/{code}'
_UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
_MOVIE_PATH = re.compile(r'[a-f0-9]/[a-f0-9]/[a-f0-9-]{36}\.mp4')
DMM_PREVIEW_QUERY = ('query PeachPreview($id: ID!) { ppvContent(id: $id) '
                     '{ id makerContentId sample2DMovie { highestMovieUrl } } }')
DMM_PREVIEW_HOST = 'cc3001.dmm.co.jp'


def dmm_preview_url(value):
    """只接受来源明确声明的公开 DMM MP4。"""
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return ''
    try:
        parts = urlsplit(value)
        allowed = (parts.scheme == 'https' and parts.hostname == DMM_PREVIEW_HOST
                   and not parts.username and not parts.password and parts.port in (None, 443)
                   and not parts.fragment and '..' not in parts.path.split('/')
                   and re.fullmatch(r'/(?:pv|litevideo)/[A-Za-z0-9/_-]+\.mp4', parts.path))
    except ValueError:
        return ''
    return value if allowed else ''


class _DmmPreviewSource(DmmSource):
    """复用 DMM 的 CID 搜索、精确匹配和传输，只读取预告字段。"""

    def content(self, cid, *, session):
        data = self.graphql(DMM_PREVIEW_QUERY, {'id': cid}, session=session)
        if 'ppvContent' not in data:
            raise ValueError('DMM 商品字段未取得')
        item = data['ppvContent']
        if item is None:
            return None
        if (not isinstance(item, dict) or item.get('id') != cid
                or not matching_cids(item.get('makerContentId'), [cid])):
            raise ValueError('DMM 作品身份不匹配')
        return item


def dmm_preview(code, secrets_root, *, transport=None):
    """按已核对的官方商品身份读取 sample2DMovie。"""
    if not code_parts(code):
        return ''
    owned = transport is None
    transport = transport or HostLimitedTransport(SourceTransport(
        secrets_root, max_requests=4, max_bytes=2 * 1024 * 1024, max_seconds=30), 2)
    try:
        source = _DmmPreviewSource()
        session = Session(transport, deadline=time.monotonic() + 30)
        with source.holding(session):
            item = json.loads(source.fetch(code, session=session).body)
        if normalise_code_key(item.get('makerContentId')) != normalise_code_key(code):
            raise ValueError('DMM 番号不匹配')
        if 'sample2DMovie' not in item:
            raise ValueError('DMM 公开预告字段未取得')
        movie = item['sample2DMovie']
        if movie is None:
            return ''
        url = dmm_preview_url(movie.get('highestMovieUrl')) if isinstance(movie, dict) else ''
        if not url:
            raise ValueError('DMM 公开预告地址未识别')
        return url
    except SourceFailure as error:
        if error.reason == FailureReason.NOT_FOUND:
            return ''
        raise PublicSourceUnavailable('DMM 视频预览未取得，请稍后重试') from error
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError, httpx.HTTPError) as error:
        raise PublicSourceUnavailable('DMM 视频预览未取得，请稍后重试') from error
    finally:
        if owned:
            transport.close()


def javdb_preview(code, movie_id, secrets_root, *, transport=None):
    """从同一番号的 JavDB 详情读取页面声明的 DMM 预告。"""
    if movie_id is not None and (not isinstance(movie_id, str) or not re.fullmatch(r'[A-Za-z0-9]+', movie_id)):
        return ''
    owned = transport is None
    transport = transport or HostLimitedTransport(SourceTransport(
        secrets_root, max_requests=3, max_bytes=2 * 1024 * 1024, max_seconds=30), JAVDB.interval)
    try:
        source = JavDBSource()
        session = Session(transport, deadline=time.monotonic() + 30)
        with source.holding(session):
            page = (source._page(f'https://javdb.com/v/{movie_id}?locale=zh', session=session) if movie_id
                    else source.fetch(code, session=session))
            source.parse(page, code)
        soup = BeautifulSoup(page.text, 'html.parser')
        for node in soup.select('video[src], video source[src], [data-video-url], a[data-fancybox][href]'):
            value = node.get('src') or node.get('data-video-url') or node.get('href') or ''
            url = dmm_preview_url(urljoin(page.url, value))
            if url:
                return url
        return ''
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError, httpx.HTTPError, SourceFailure) as error:
        raise PublicSourceUnavailable('JavDB 视频预览未取得，请稍后重试') from error
    finally:
        if owned:
            transport.close()


def prestige_preview(code, secrets_root, *, transport=None):
    """复用 amane 固定版本的 SKU→UUID 协议，只读取两份 JSON。"""
    owned = transport is None
    transport = transport or HostLimitedTransport(SourceTransport(
        secrets_root, max_requests=3, max_bytes=2 * 1024 * 1024, max_seconds=30), 2)
    deadline = time.monotonic() + 30

    def read(url):
        body = _fetch(transport, url, referer='https://www.prestige-av.com/',
                      limit=1024 * 1024, deadline=deadline)
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError('商品资料格式未识别')
        return payload

    try:
        sku = read(PRESTIGE_SKU.format(code=quote(normalise_code_key(code))))
        uuid = (sku.get('parentProduct') or {}).get('uuid')
        if not isinstance(uuid, str) or not _UUID.fullmatch(uuid):
            raise ValueError('商品身份未取得')
        product = read(PRESTIGE_PRODUCT.format(uuid=uuid))
        numbers = [row.get('deliveryItemId') for row in product.get('sku') or [] if isinstance(row, dict)]
        if product.get('uuid') != uuid or not any(normalise_code_key(number) == normalise_code_key(code) for number in numbers):
            raise ValueError('来源作品身份不匹配')
        if 'movie' not in product:
            raise ValueError('公开预告字段未取得')
        movie = product['movie']
        if movie is None:
            return ''
        if not isinstance(movie, dict) or movie.get('mimeType') != 'video/mp4' or movie.get('isLimited') != 0:
            raise ValueError('公开预告格式未识别')
        path = movie.get('path')
        if not isinstance(path, str) or not _MOVIE_PATH.fullmatch(path):
            raise ValueError('公开预告地址未识别')
        return PRESTIGE_MEDIA.format(path=path)
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError, httpx.HTTPError) as error:
        raise PublicSourceUnavailable('官方视频预览未取得，请稍后重试') from error
    finally:
        if owned:
            transport.close()
