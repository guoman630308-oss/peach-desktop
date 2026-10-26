"""fc2ppv-db.com（FC2PPV Database）的作品页：FC2 商品的元数据库，女优、卖家与「流出」标记由站方用户维护。

旧域名 fc2ppvdb.com 已关（`docs/SOURCING.md`），这一站是新起的 Next.js 应用，作品页 `/ja/videos/<video_id>`
服务端渲染，不需要跑脚本：`<h1>` 是 `FC2-PPV-<id> 标题`，「動画詳細情報」那块给動画ID、販売日、流出あり／なし、
モザイクあり／なし、出演女優（链到 `/ja/actresses/<uuid>`）、販売者（链到 `/ja/sellers/<slug>`）与タグ；
`<meta name="description">` 末尾另有 `販売者: X / 公開日: 2026年9月21日 / 再生時間: 53:52`，时长只在这里有。
搜索 `/ja/search?q=<id>` 按商品号直接命中，也能按女优名搜（2026-09-24 实测 `川北すずね` 62 件）。
站上没有的商品回一张 200 的「404 ページが見つかりません」页，`<h1>` 里没有番号。

站前是 Cloudflare 的 JS 验证：httpx 与模拟 Chrome 指纹的 curl_cffi 直连都回 403，只有浏览器过完验证发下的
`cf_clearance` 能进。有 Chrome 或 Edge 的机器上请求由本机浏览器打开，验证由它自己过（`browser_transport`，
ADR-0065），新会话落到的年齢確認页由 `browser_gate` 替人点；没有浏览器时退回 httpx，请求带用户在采集设置里
贴的 Cookie（`scraping_access.SOURCES['fc2ppvdb']`），它绑着解题那台浏览器的 UA，整站 UA（`peach.user_agent`）
与用户的 Chrome 保持一致。

不交封面：站上那张是 360×360 的 CloudFront 缩略图（`og:image:width` 写 800，实测 360），连封面的最低宽度都
过不了，下回来只是白花一次请求；封面留给链上前后指着 FC2 存储原件的几档。
"""
from __future__ import annotations

import re
from dataclasses import replace

from bs4 import BeautifulSoup

from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure, challenge_page
from .fc2 import PAGE_LIMIT, fc2_record, runtime_minutes, seller_page, strip_code, text_of, unrecognised, video_id

#: 按社区来源登记：它的元数据与女优栏由站方用户维护。主机间隔用默认的 2 秒。
FC2PPVDB = SiteConfig(name="fc2ppvdb", label="FC2PPV-DB", provider="fc2ppvdb-page",
                      base_url="https://fc2ppv-db.com", domains=("fc2ppv-db.com",), stage="community",
                      cookie=True, page_limit=PAGE_LIMIT)
VIDEO_PATH = "/ja/videos/{video_id}"
#: 新会话第一次进站被送到的年齢確認页；`scraping_access.SOURCES["fc2ppvdb"]["browser_gate"]` 让浏览器传输替人点。
AGE_GATE_PATH = "/age-verify"

#: `<meta name="description">` 末尾那三项。
_META_TAIL = re.compile(r"販売者:\s*(?P<seller>.*?)\s*/\s*公開日:\s*(?P<date>[^/]*?)\s*/\s*再生時間:\s*(?P<runtime>[\d:]+)\s*$")
_JAPANESE_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
#: 站上没有女优时那一栏写的话。
_NO_INFORMATION = "情報がありません"
#: 女优页地址末尾那段 uuid。
_ACTRESS_ID = re.compile(r"/actresses/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?(?:[?#]|$)")


def _number(wanted: str) -> re.Pattern:
    """按数字边界认番号：`FC2-PPV-4898837` 不能命中 `FC2-PPV-48988370`。"""
    return re.compile(rf"FC2-PPV-{re.escape(wanted)}(?!\d)")


def japanese_date(text: str) -> str:
    """`2026年9月21日` → `2026-09-21`；认不出回空串。"""
    found = _JAPANESE_DATE.search(str(text or ""))
    if not found:
        return ""
    year, month, day = found.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _labelled(soup: BeautifulSoup, label: str) -> str:
    """`<p>販売日</p><p>2026年9月21日</p>`：标签与值是相邻的两个 `<p>`。"""
    for node in soup.find_all("p"):
        if text_of(node) == label:
            return text_of(node.find_next_sibling("p"))
    return ""


def _block_after(soup: BeautifulSoup, label: str):
    """`<div><div><p>出演女優</p>…</div><div>…链接…</div></div>`：标签所在那一块的上两层才装着值。
    只在这一块里找链接：页面别处（PR 位、関連動画）也链到女优页，不是这部片的人。"""
    for tag in ("p", "span"):
        for node in soup.find_all(tag):
            if text_of(node) == label and node.parent is not None and node.parent.parent is not None:
                return node.parent.parent
    return None


def _performers(soup: BeautifulSoup) -> list[dict]:
    """出演女優那一块 → 每位一份 `{japanese_name, external_id?}`，同名只留一个。

    `external_id` 是站内女优页 `/ja/actresses/<uuid>` 的 uuid：同一个人在站上只有一页，落库时登记成外部
    编号，她以后的作品按编号挂回同一条实体（ADR-0061）。女优页自己的别名栏几乎是空的，不去读它。
    """
    block = _block_after(soup, "出演女優")
    if block is None:
        return []
    found: dict[str, dict] = {}
    for link in block.select('a[href*="/actresses/"]'):
        picture = link.select_one("img[alt]")
        name = (str(picture.get("alt") or "").strip() if picture is not None else "") or text_of(link)
        if not name or name == _NO_INFORMATION or name in found:
            continue
        person = {"japanese_name": name}
        uuid = _ACTRESS_ID.search(str(link.get("href") or ""))
        if uuid:
            person["external_id"] = uuid.group(1).lower()
        found[name] = person
    return list(found.values())


def _seller(soup: BeautifulSoup) -> tuple[str, str]:
    """`(卖家名, slug)`。名字在头像的 `alt` 里，也在那串 span 的末尾。"""
    link = soup.select_one('main a[href*="/sellers/"]') or soup.select_one('a[href*="/sellers/"]')
    if link is None:
        return "", ""
    slug = str(link.get("href") or "").rstrip("/").rsplit("/", 1)[-1]
    picture = link.select_one("img[alt]")
    name = str(picture.get("alt") or "").strip() if picture is not None else ""
    if not name:
        name = text_of(link).replace("販売者", "", 1).strip()
    return name, slug


def _tags(soup: BeautifulSoup) -> list[str]:
    block = _block_after(soup, "タグ")
    return [text_of(link) for link in block.select("a")] if block is not None else []


def _leaked(soup: BeautifulSoup) -> bool | None:
    """「流出あり」／「流出なし」那枚标记；页上没有就不知道。"""
    marks = {text_of(node) for node in soup.find_all("span")}
    if "流出あり" in marks:
        return True
    if "流出なし" in marks:
        return False
    return None


class Fc2ppvdbSource(SiteSource):
    DEFAULT = FC2PPVDB

    def video_url(self, code: str) -> str:
        """这个番号的作品页地址；认不出商品号时回空串。"""
        found = video_id(code)
        return self.config.base_url + VIDEO_PATH.format(video_id=found) if found else ""

    def fetch(self, code: str, *, session: Session) -> Page:
        url = self.video_url(code)
        if not url:
            raise unrecognised()
        return session.get(url, config=self.config)

    def parse(self, page: Page, code: str) -> SiteRecord:
        """作品页 → 记录；站上没有归 `not_found`，Cloudflare 验证页归 `cloudflare_challenge`。

        番号以 `<h1>` 为准：站上没有的商品回的也是 200，`<h1>` 写着 `404`；别的结构对不上时也不该把
        别的片安上来。標题剥掉开头那截番号。卖家名进 `label`，主页指回发行方那一站（slug 与官方用户页
        同名，`fc2cmadb` 那一档实测一致）。「流出」标记进 `extra['leaked']`。
        """
        wanted = video_id(code)
        if not wanted:
            raise unrecognised()
        if challenge_page(page.body):
            raise SourceFailure(FailureReason.CLOUDFLARE_CHALLENGE,
                                f"{self.config.label} 要求 Cloudflare 验证，请在采集设置里更新 Cookie",
                                status_code=403)
        if AGE_GATE_PATH in page.url:
            # 新会话第一次进站被送到年齢確認页（200）；浏览器传输按 `browser_gate` 替人点，点不过去时页面原样到这里。
            raise SourceFailure(FailureReason.AUTH_REQUIRED, f"{self.config.label} 送到了年齢確認页，浏览器没有点过去")
        soup = BeautifulSoup(page.text, "html.parser")
        heading = soup.select_one("main h1") or soup.select_one("h1")
        title = text_of(heading)
        if not title or not _number(wanted).search(title):
            raise SourceFailure(FailureReason.NOT_FOUND, f"{self.config.label} 上没有这个商品")
        description = soup.select_one('meta[name="description"]')
        tail = _META_TAIL.search(str(description.get("content") or "")) if description is not None else None
        seller, slug = _seller(soup)
        record = fc2_record(
            self.config, wanted, self.video_url(code),
            title=strip_code(title, wanted),
            release_date=japanese_date(_labelled(soup, "販売日") or (tail.group("date") if tail else "")),
            runtime=runtime_minutes(tail.group("runtime")) if tail else None,
            performers=_performers(soup),
            label=seller or (tail.group("seller").strip() if tail else ""),
            seller_url=seller_page(slug) if slug else "",
            tags=_tags(soup))
        return replace(record, extra={**record.extra, "leaked": _leaked(soup)})
