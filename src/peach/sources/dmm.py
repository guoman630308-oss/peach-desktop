"""DMM／FANZA 自己的 GraphQL 目录：有码链上 r18.dev 之后的兜底，答 r18.dev 漏收的那部分。

r18.dev 是这份目录的镜像，但不是全份：2026-09-24 实测当月新片 START-640、START-639，以及
FKOS-006、FKOS-002、FNS-061、SUKE-073 在 r18.dev 都是 404，DMM 的接口都有。接口是
`https://api.video.dmm.co.jp/graphql`（新版商品页 video.dmm.co.jp 背后那一个，取法参照 OpenAver
的 `core/scrapers/dmm.py`），只收 POST，从中国电信直连与香港出口都回 200、一次 0.3～1 秒，
没有年龄门，不需要日本出口。它和 amane 那只走 `mono/dvd` 页面的 HTML 爬虫不是一条路：
ADR-0048 因后者答 BOD 版、一次 3～14 秒而不让它进链，本模块由 ADR-0059 另行接入。

一部片的商品 id（cid）由番号推：先猜 `{字母}{五位补零数字}`（`SSIS-057` → `ssis00057`），
猜不中再搜 `legacySearchPPV`，只收字母段与数字都对得上的 cid——搜 `SSIS 057` 排在前面的是
`ssis00570`、`ssis00579`，搜 `ERK 116` 会带出 `gerk116`，不比数字就会把别的片当成它。带厂牌
数字前缀的 cid（`h_1721fkos00006`、`1fns00061`）只有搜索答得出。取到的详情再用
`makerContentId` 核一次身份，AI 重制版（`48midv00012ai`）排在原版之后。

`makerReleasedAt` 是发售日，以 UTC 写日本时间的零点（SSIS-057 答 `2021-05-06T15:00:00Z`，账本与
r18.dev 都是 2021-05-07），换成日本时间再取日期。厂牌是日文名（`エスワン ナンバーワンスタイル`），
r18.dev 给的是品牌英文名；账本的厂牌实体靠 `entity_alias` 归一（ADR-0038），这里不翻。genre 与
r18.dev 的 `name_ja` 是同一套词。

只进有码链：Prestige 已从 FANZA 撤下（搜 `ABW 032` 零结果），MGS 素人号也不在这份目录上
（搜 `MIUM 1239`、`LUXU 1475` 零结果）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from ..metadata import identifies_code
from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure

#: 不带 Cookie，主机间隔用默认的 2 秒。一次响应实测几 KB，1 MiB 是余量。
DMM = SiteConfig(name="dmm", label="DMM / FANZA", provider="dmm-graphql",
                 base_url="https://api.video.dmm.co.jp", domains=("dmm.co.jp", "dmm.com"),
                 stage="official", page_limit=1024 * 1024)
GRAPHQL_PATH = "/graphql"
#: 用户看得到的商品页，写进 `source_url`。
CONTENT_PAGE = "https://video.dmm.co.jp/av/content/?id={cid}"
#: 商品那一份查询。字段名 2026-09-24 实测有效；`sampleImages` 只有 `imageUrl`，没有 `largeUrl`。
DETAIL_QUERY = (
    "query PeachContent($id: ID!) { ppvContent(id: $id) { id title description makerContentId makerReleasedAt"
    " duration maker { id name } label { id name } series { id name } actresses { id name }"
    " directors { id name } genres { id name } packageImage { largeUrl mediumUrl } sampleImages { imageUrl } } }"
)
SEARCH_QUERY = (
    "query PeachSearch($limit: Int!, $sort: ContentSearchPPVSort!, $queryWord: String)"
    " { legacySearchPPV(limit: $limit, sort: $sort, queryWord: $queryWord) { result { contents { id } } } }"
)
#: 搜索取多少条：按发售日倒序，`ssis00057` 排在 `SSIS 057` 结果的第 11 条，30 是余量。
SEARCH_LIMIT = 30
#: 搜索命中里最多核几条详情：`MIDV 012` 同时命中 `midv00012` 与 AI 重制版 `48midv00012ai`。
DETAIL_ATTEMPTS = 2
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
JST = timezone(timedelta(hours=9))
UNRECOGNISED = "认不出可以问 DMM 的番号"
MISSING = "DMM 没有这个番号"
NOT_JSON = "DMM 返回的不是作品 JSON"
REFUSED = "DMM 拒绝了这条查询"

_CODE = re.compile(r"^(\d{3})?([A-Z]{2,8})-?(\d{1,5})$")
_CID = re.compile(r"^(?:h_\d+|\d+)?([a-z]+)(\d+)([a-z]*)$")


def code_parts(code: str) -> tuple[str, str, str] | None:
    """番号拆成（数字前缀，字母段，数字）：`SSIS-057` → `('', 'SSIS', '057')`，`300MIUM-1239` → `('300', 'MIUM', '1239')`。"""
    matched = _CODE.match(str(code or "").strip().upper())
    return (matched.group(1) or "", matched.group(2), matched.group(3)) if matched else None


def guessed_cid(code: str) -> str:
    """按 DMM 最常见的编法猜 cid：字母小写，数字补到五位。猜不出交空串。"""
    parts = code_parts(code)
    if not parts:
        return ""
    digits, letters, number = parts
    return f"{digits}{letters.lower()}{int(number):05d}"


def matching_cids(code: str, cids) -> list[str]:
    """搜索结果里字母段与数字都对得上这个番号的 cid，不带后缀的排前面，顺序其余照搜索结果。"""
    parts = code_parts(code)
    if not parts:
        return []
    digits, letters, number = parts
    found = []
    for cid in cids:
        text = str(cid or "").lower()
        matched = _CID.match(text)
        if not matched or matched.group(1) != letters.lower() or int(matched.group(2)) != int(number):
            continue
        if digits and not text.startswith(digits):
            continue
        found.append((bool(matched.group(3)), str(cid)))
    return [cid for _, cid in sorted(found, key=lambda item: item[0])]


def release_date(value) -> str:
    """`makerReleasedAt` 换成日本时间的日期；解析不了就取前十个字符。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if moment.tzinfo is None:
        return moment.date().isoformat()
    return moment.astimezone(JST).date().isoformat()


class DmmSource(SiteSource):
    DEFAULT = DMM

    def endpoint(self) -> str:
        return self.config.base_url + GRAPHQL_PATH

    def content_url(self, cid: str) -> str:
        return CONTENT_PAGE.format(cid=quote(cid, safe=""))

    def graphql(self, query: str, variables: dict, *, session: Session) -> dict:
        """发一条查询，交回 `data`。回的不是 JSON、或接口拒绝这条查询（只有 `errors`）归 `parse_error`。"""
        body = json.dumps({"query": query, "variables": variables}).encode()
        page = session.post(self.endpoint(), config=self.config, body=body, headers=HEADERS)
        try:
            raw = json.loads(page.body)
        except ValueError:
            raise SourceFailure(FailureReason.PARSE_ERROR, NOT_JSON) from None
        if not isinstance(raw, dict):
            raise SourceFailure(FailureReason.PARSE_ERROR, NOT_JSON)
        data = raw.get("data")
        if not isinstance(data, dict):
            errors = raw.get("errors")
            first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
            message = str(first.get("message") or "")
            raise SourceFailure(FailureReason.PARSE_ERROR, REFUSED + (f"：{message}" if message else ""))
        return data

    def content(self, cid: str, *, session: Session) -> dict | None:
        """一个 cid 的商品资料；DMM 没有这个 cid 时 `ppvContent` 是 null，交 None。"""
        item = self.graphql(DETAIL_QUERY, {"id": cid}, session=session).get("ppvContent")
        return item if isinstance(item, dict) and item.get("id") else None

    def identified(self, code: str, cid: str, *, session: Session) -> dict | None:
        """取这个 cid 的资料，并核它就是这个番号；不是的按没有交 None，让调用方接着试下一个。"""
        item = self.content(cid, session=session)
        if item is None or not identifies_code(code, {"content_id": item.get("id"), "id": item.get("makerContentId")}):
            return None
        return item

    def search(self, code: str, *, session: Session) -> list[str]:
        """按「字母段 数字」搜，交回对得上的 cid。地区限制下接口回 200 加空列表，和「没收」长得一样。"""
        parts = code_parts(code)
        if not parts:
            return []
        _, letters, number = parts
        data = self.graphql(SEARCH_QUERY, {"limit": SEARCH_LIMIT, "sort": "RELEASE_DATE",
                                           "queryWord": f"{letters} {number}"}, session=session)
        result = (data.get("legacySearchPPV") or {}).get("result") or {}
        contents = result.get("contents") or []
        return matching_cids(code, (row.get("id") for row in contents if isinstance(row, dict)))

    def fetch(self, code: str, *, session: Session) -> Page:
        if not code_parts(code):
            raise SourceFailure(FailureReason.NOT_FOUND, UNRECOGNISED)
        guess = guessed_cid(code)
        item = self.identified(code, guess, session=session)
        if item is None:
            for cid in [cid for cid in self.search(code, session=session) if cid != guess][:DETAIL_ATTEMPTS]:
                item = self.identified(code, cid, session=session)
                if item is not None:
                    break
        if item is None:
            raise SourceFailure(FailureReason.NOT_FOUND, MISSING)
        return Page(self.content_url(str(item["id"])), json.dumps(item, ensure_ascii=False).encode())

    def parse(self, page: Page, code: str) -> SiteRecord:
        try:
            item = json.loads(page.body)
        except ValueError:
            raise SourceFailure(FailureReason.PARSE_ERROR, NOT_JSON) from None
        if not isinstance(item, dict) or not item.get("id"):
            raise SourceFailure(FailureReason.PARSE_ERROR, NOT_JSON)
        cid = str(item["id"])
        if not identifies_code(code, {"content_id": cid, "id": item.get("makerContentId")}):
            raise SourceFailure(FailureReason.PARSE_ERROR, "来源返回的番号不匹配")
        name = lambda key: str((item.get(key) or {}).get("name") or "")
        rows = lambda key: [row for row in item.get(key) or [] if isinstance(row, dict) and row.get("name")]
        directors = rows("directors")
        duration = item.get("duration")
        cover = str((item.get("packageImage") or {}).get("largeUrl") or "")
        samples = [str(row["imageUrl"]) for row in item.get("sampleImages") or []
                   if isinstance(row, dict) and row.get("imageUrl")]
        return SiteRecord(
            source=self.config.name, provenance=self.config.provider, code=code, source_url=page.url,
            title=str(item.get("title") or ""),
            performers=tuple({"japanese_name": str(row["name"]), "dmm_id": str(row.get("id") or "")}
                             for row in rows("actresses")),
            studio=name("maker"), label=name("label"), series=name("series"),
            director=str(directors[0]["name"]) if directors else "",
            release_date=release_date(item.get("makerReleasedAt")),
            runtime=round(duration / 60, 2) if isinstance(duration, (int, float)) and duration > 0 else None,
            tags=tuple(str(row["name"]) for row in rows("genres")),
            cover_urls=(cover,) if cover else (),
            extra={"content_id": cid, "description": str(item.get("description") or ""),
                   "sample_images": samples, "raw": item})
