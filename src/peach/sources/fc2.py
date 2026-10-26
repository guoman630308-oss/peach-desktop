"""FC2 内容市场（adult.contents.fc2.com）的商品页，以及 FC2 链上五站共用的番号、时长与原件地址。

FC2 不是 JAV：番号是卖家自己的投稿号，JAV 目录站按它去查要么没有，要么撞上别的片。
r18.dev 对 FC2 实测 85 条问了 85 条全空；AVBase 与 JavBus 对本地这批番号一律回「没有
这个番号」；javdb 有页面但配额紧。发行方自己那一页才是这批番号的正主：不要凭据、
一次请求给全标题、说明、卖家、商品标签、时长、販売日和封面原图。

页面主体在 `<script type="application/ld+json">` 的 Product 里，正文 DOM 只补它没有的
那几项。下架的商品仍回 200，靠正文里没有 Product 判定，归 `not_found`，不当成抓取失败。
下架的由 fc2cmadb、FC2PPV-DB、JAVten 与 JavArchive（`sources/` 下同名模块）依次接着答，
五站先后问、取齐即停的流程在 `LibraryMetadataProvider.fc2`，顺序是 `metadata_routes.FC2_STAGE`。

带分段后缀的番号（`FC2-PPV-3312576-1`）在这里一律认不出商品号，于是一处都不问。那是
对的：合集的封面套给每个分段，屏幕上就是 21 个不同内容顶着同一张图。

官方那一页不取演员：它没有演员栏，标题里那个名字是卖家自己写的宣传语，`みお(19)`
这样的写法既不是艺名也没有第二处可以印证。
"""
from __future__ import annotations

import json
import re
import urllib.parse

from bs4 import BeautifulSoup

from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure

#: FC2 链上五站的页面上限。FC2 商品页实测 300～320 KB，fc2cmadb 那页 90 KB；说明与评论都在同一页里。
PAGE_LIMIT = 2 * 1024 * 1024
#: 商品页、卖家页和图片存储（`storage*`、`contents-thumbnail*`）都在 fc2.com 底下，一条域名就够。
#: 免登录可读，不带 Cookie；主机间隔用默认的 2 秒。
FC2 = SiteConfig(name="fc2", label="FC2", provider="fc2-article", base_url="https://adult.contents.fc2.com",
                 domains=("fc2.com",), stage="official", page_limit=PAGE_LIMIT)
ARTICLE_PATH = "/article/{video_id}/"
USER_PATH = "/users/{slug}/"

#: 账本里 FC2 一律记在这个厂牌下（库内既有的 561 条就是它），新抓的跟着走，免得同一批
#: 内容分裂成两个厂牌实体。卖家是另一回事，它走 `label`：那是只作证据的目录字段，
#: 归不归实体由复核页上的人判断，解析器不替他决定。
STUDIO = "FC2-PPV"
#: 番号认不出商品号时三站共用的那一句。
UNRECOGNISED = "这个番号认不出 FC2 商品号"

_VIDEO_ID = re.compile(r"^FC2(?:[-_. ]?PPV)?[-_. ]?(\d{5,})$", re.I)
#: `41:50` 与 `1:23:45` 两种都有。
_RUNTIME = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")
_SOLD_ON = re.compile(r"販売日\s*[:：]\s*(\d{4})/(\d{2})/(\d{2})")
#: `https://contents-thumbnail2.fc2.com/w276/storage92000.contents.fc2.com/file/…`
#: 前半截是缩放服务，后半截就是原件地址。
_THUMBNAIL_WRAPPER = re.compile(
    r"^https?://contents-thumbnail\d*\.fc2\.com/w\d+/(storage[\w.-]+\.fc2\.com/.+)$", re.I)
#: 镜像站对没有商品图的条目回它自己那张占位件（`/storage/images/article/no-image.jpg`），
#: 而且是站内相对地址。当封面交下去，抓取那侧连主机都拼不出来，报回来的是一句「来源连接
#: 未取得」——看着像网络故障，其实是这部片在站上本来就没有图（2026-09-22 实测 21 个取不到
#: 封面的番号里有 15 个是它）。
_NO_IMAGE = re.compile(r"/no-image\.\w+$", re.I)


def video_id(code: str) -> str:
    """账本番号 → 商品号。认不出来的回空串，由调用方决定怎么处置。"""
    found = _VIDEO_ID.match(str(code or "").strip())
    return found.group(1) if found else ""


def canonical_code(video: str) -> str:
    """商品号 → 账本写法。"""
    digits = str(video or "").strip()
    return f"FC2-PPV-{digits}" if digits.isdigit() else ""


def runtime_minutes(raw: str) -> float | None:
    match = _RUNTIME.match(str(raw or "").strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    total = int(hours or 0) * 60 + int(minutes) + int(seconds) / 60
    return round(total, 2) if total > 0 else None


def storage_original(url: str) -> str:
    """缩略图地址 → 原件地址。站方的占位件回空串，已经是原件的原样返回。"""
    address = str(url or "").strip()
    if _NO_IMAGE.search(urllib.parse.urlparse(address).path):
        return ""
    found = _THUMBNAIL_WRAPPER.match(address)
    return f"https://{found.group(1)}" if found else address


def seller_page(slug: str) -> str:
    """卖家在发行方那一站的主页。"""
    return FC2.base_url + USER_PATH.format(slug=slug)


def text_of(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def strip_code(title: str, wanted: str) -> str:
    """标题开头那截番号剥掉：各站写法不一，`FC2-PPV-4137487`、`FC2PPV 1863914`、`[FC2-PPV-4898837]` 都有。
    留着就把番号写进了标题。"""
    return re.sub(rf"^\s*\[?\s*FC2[-_. ]?(?:PPV)?[-_. ]?{re.escape(wanted)}\s*\]?\s*[-—:：]?\s*", "",
                  str(title or ""), flags=re.I)


def unrecognised() -> SourceFailure:
    return SourceFailure(FailureReason.NOT_FOUND, UNRECOGNISED)


def fc2_record(config: SiteConfig, wanted: str, source_url: str, *, title: str, description: str = "",
               release_date: str = "", runtime: float | None = None, performers=(), label: str = "",
               seller_url: str = "", tags=(), cover_urls=()) -> SiteRecord:
    """三站同一份形状：`content_id`、`description`、`seller_url` 是 FC2 独有的键，放进 `extra`。"""
    return SiteRecord(
        source=config.name, provenance=config.provider, code=canonical_code(wanted), source_url=source_url,
        title=title, performers=tuple(performers), studio=STUDIO, label=label, release_date=release_date,
        runtime=runtime, tags=tuple(dict.fromkeys(tag for tag in tags if tag)),
        cover_urls=tuple(url for url in cover_urls if url),
        extra={"content_id": wanted, "description": description, "seller_url": seller_url})


def _product(soup: BeautifulSoup) -> dict | None:
    """页面里那份 Product。下架的商品这一段整个不出现。"""
    for node in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(node.string or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return None


def _seller_name(soup: BeautifulSoup) -> str:
    """卖家名。Product 的 `brand.name` 实测常是 null，站上写在头部那个指向用户页的链接里。"""
    link = soup.select_one('.items_article_headerInfo a[href*="/users/"]')
    return text_of(link)


class Fc2Source(SiteSource):
    DEFAULT = FC2

    def article_url(self, code: str) -> str:
        """这个番号的商品页地址；认不出商品号时回空串。"""
        found = video_id(code)
        return self.config.base_url + ARTICLE_PATH.format(video_id=found) if found else ""

    def fetch(self, code: str, *, session: Session) -> Page:
        url = self.article_url(code)
        if not url:
            raise unrecognised()
        return session.get(url, config=self.config)

    def parse(self, page: Page, code: str) -> SiteRecord:
        """商品页 → 记录；已下架或对不上番号归 `not_found`。

        番号要核：`article/<id>/` 取不到商品时 FC2 回的是一个 200 的「見つかりませんでした」页，
        而站上的商品号会被复用给别的投稿，不核就会把另一部片的资料写到这个番号头上。
        """
        wanted = video_id(code)
        if not wanted:
            raise unrecognised()
        soup = BeautifulSoup(page.text, "html.parser")
        product = _product(soup)
        if product is None or str(product.get("sku") or "").strip() != wanted:
            raise SourceFailure(FailureReason.NOT_FOUND, f"{self.config.label} 上没有这个商品")
        image = product.get("image")
        #: 商品图有两处：正文那张是 `w276` 缩略图，Product 里这个是存储原件（实测
        #: 2350×2352）。取原件，缩略图连封面的最低宽度都过不了。
        cover = storage_original((image or {}).get("url")) if isinstance(image, dict) else ""
        brand = product.get("brand")
        seller = str((brand if isinstance(brand, dict) else {}).get("url") or "").strip()
        sold = _SOLD_ON.search(soup.get_text(" ", strip=True))
        return fc2_record(
            self.config, wanted, self.article_url(code),
            title=str(product.get("name") or "").strip(),
            description=str(product.get("description") or "").strip(),
            release_date="-".join(sold.groups()) if sold else "",
            runtime=runtime_minutes(text_of(soup.select_one("p.items_article_info"))),
            # 卖家在站上既有名字也有主页；名字空着时退回主页地址，别把这一栏丢掉。
            label=_seller_name(soup) or seller, seller_url=seller,
            tags=[text_of(node) for node in soup.select(".items_article_TagArea a")],
            cover_urls=[cover])
