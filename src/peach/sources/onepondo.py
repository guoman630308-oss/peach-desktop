"""一本道（1pondo.tv）官网的作品 JSON。

无码番号在 JAV 目录站上要么没有、要么给的是转售商的上架日：`092415_001` javdb 报
2016-06-16，而番号自己写着 2015-09-24。发行方自己那一页才是正主，而且不用解析 HTML——
站点前端读的就是 `dyn/phpauto/movie_details/movie_id/<id>.json`，一次请求给标题、说明、
女优（日文与罗马字）、系列、发行日、时长和站内标签，不要凭据。下架的作品直接回 404。

番号的分隔符是片商标识（`catalog_rules.normalise_code_key`）：一本道写 `112312_478`，
カリビアンコム 写 `040221-001`。两家都按 `MMDDYY_nnn` 编号，光看番号分不出是谁家的，
所以问哪一家由调用方按这一行的证据决定（`names_this_studio`、`library_processing._sources_for`），
这一站只负责把番号写成官网认的形状并核对返回的 `MovieID`。

封面只有站点自己那张 16:9 剧照（`str.jpg`，实测 960×540）。无码片商不出封套，`thum_b.jpg`
是 120×197 的列表缩略图，两者都不是 JAV 那种竖版封面；取大的那张。
"""
from __future__ import annotations

import json
import re

from ..catalog_rules import is_uncensored_code
from ..jav_cover_fetch import NotFound
from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure

#: 作品 JSON、剧照和样片都在 1pondo.tv 底下（含 `smovie.`）。免登录可读，不带 Cookie，主机间隔
#: 用默认的 2 秒。作品 JSON 实测 6～8 KB，带样片清单也只有十几 KB，上限 1 MiB。
ONEPONDO = SiteConfig(name="1pondo", label="一本道", provider="1pondo-json", base_url="https://www.1pondo.tv",
                      domains=("1pondo.tv",), stage="official", page_limit=1024 * 1024)
DETAIL_PATH = "/dyn/phpauto/movie_details/movie_id/{movie_id}.json"
PAGE_PATH = "/movies/{movie_id}/"

#: 账本里这批作品的厂牌就叫这个（`一本道`），新抓的跟着走，免得同一家分裂成两个实体。
STUDIO = "一本道"

#: 站上的作品名写作 `112312_478`；账本里同一部片可能写成 `112312-478`，那是压制组的
#: 文件名带进来的。
_DATED = re.compile(r"^(\d{6})[-_](\d{2,4})$")
#: 一本道自己在页面上标着「1pon」，作品目录、文件名与账本的 studio 都用得上这几个写法。
_OWN_MARKS = ("1pon", "一本道", "1pondo")


def movie_id(code: str) -> str:
    """账本番号 → 官网作品号。不是日期式番号的回空串。"""
    found = _DATED.match(str(code or "").strip())
    if not found or not is_uncensored_code(f"{found.group(1)}_{found.group(2)}"):
        return ""
    return f"{found.group(1)}_{found.group(2)}"


def names_this_studio(*evidence: str) -> bool:
    """这几段文字（路径、文件名、账本厂牌）里有没有说这部片是一本道的。

    日期式番号本身不带片商，拿另一家的番号去问官网，答回来的是同一天发行的另一部片。
    所以只在本机已有证据指着一本道时才问它，问不着的照旧走社区来源。
    """
    text = " ".join(str(item or "") for item in evidence).lower()
    return any(mark.lower() in text for mark in _OWN_MARKS)


def _actresses(raw: dict) -> list[dict]:
    """女优。日文名是账本的身份写法，罗马字作别名跟着走。"""
    japanese = [str(name).strip() for name in raw.get("ActressesJa") or [] if str(name).strip()]
    english = [str(name).strip() for name in raw.get("ActressesEn") or []]
    if not japanese:
        japanese = [name for name in [str(raw.get("Actor") or "").strip()] if name]
    found = []
    for index, name in enumerate(japanese):
        romaji = english[index] if index < len(english) else ""
        found.append({"japanese_name": name, "name_romaji": romaji})
    return found


def _cover(raw: dict) -> str:
    """站上那张剧照。四个 Thumb 字段实测同一个地址，按大到小取第一个给出来的。"""
    for field in ("ThumbUltra", "ThumbHigh", "ThumbMed", "ThumbLow", "MovieThumb"):
        url = str(raw.get(field) or "").strip()
        if url:
            return url
    return ""


class OnePondoSource(SiteSource):
    DEFAULT = ONEPONDO

    def detail_url(self, code: str) -> str:
        """这个番号的作品 JSON 地址；认不出作品号时回空串。"""
        found = movie_id(code)
        return self.config.base_url + DETAIL_PATH.format(movie_id=found) if found else ""

    def page_url(self, movie: str) -> str:
        return self.config.base_url + PAGE_PATH.format(movie_id=movie)

    def _unrecognised(self) -> SourceFailure:
        return SourceFailure(FailureReason.NOT_FOUND, f"这个番号不是{self.config.label}的写法")

    def fetch(self, code: str, *, session: Session) -> Page:
        url = self.detail_url(code)
        if not url:
            raise self._unrecognised()
        try:
            return session.get(url, config=self.config)
        except NotFound:
            raise SourceFailure(FailureReason.NOT_FOUND, f"{self.config.label}站上没有这部片") from None

    def parse(self, page: Page, code: str) -> SiteRecord:
        """作品 JSON → 记录。回的作品号对不上这个番号归 `not_found`：番号同形的另一家片子问到这里时，
        官网答的是同一天发行的另一部片，那份资料一个字都不能用。"""
        wanted = movie_id(code)
        if not wanted:
            raise self._unrecognised()
        try:
            raw = json.loads(page.body or b"null")
        except ValueError:
            raise SourceFailure(FailureReason.PARSE_ERROR, f"{self.config.label}返回的不是作品 JSON") from None
        if not isinstance(raw, dict) or str(raw.get("MovieID") or "").strip() != wanted:
            raise SourceFailure(FailureReason.NOT_FOUND, f"{self.config.label}回的作品号对不上这个番号")
        duration = raw.get("Duration")
        cover = _cover(raw)
        return SiteRecord(
            source=self.config.name, provenance=self.config.provider, code=wanted,
            source_url=self.page_url(wanted), title=str(raw.get("Title") or "").strip(),
            performers=tuple(_actresses(raw)), studio=STUDIO, series=str(raw.get("Series") or "").strip(),
            release_date=str(raw.get("Release") or "").strip(),
            # 官网按秒记时长，账本按分钟。
            runtime=round(duration / 60, 2) if isinstance(duration, (int, float)) and duration > 0 else None,
            tags=tuple(str(name).strip() for name in raw.get("UCNAME") or [] if str(name).strip()),
            cover_urls=(cover,) if cover else (),
            extra={"content_id": wanted, "description": str(raw.get("Desc") or "").strip()})
