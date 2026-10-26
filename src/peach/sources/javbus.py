"""JavBus 的作品页。

作品页就是 `/<番号>`（厂牌回查留下的 126 页缓存为证），字段在
`<p><span class="header">識別碼:</span> …</p>` 这样的行里，封面是 `bigImage` 链接
（`/pics/cover/<id>_b.jpg`），女优在 `star-name`。它有年龄门，要带用户在采集设置里贴的
Cookie；门页上没有「識別碼」，按这一点报错。

结算上它是备选来源（ADR-0035）：搜不到原番号会给首个近似命中，所以页面上读回的番号必须
与问的对得上，对不上就是「没有」。
"""
from __future__ import annotations

import re
import urllib.parse

from ..catalog_rules import same_release_code
from ..jav_cover_fetch import NotFound
from ..javdb import clean
from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure

#: 主机间隔用默认的 2 秒：JavBus 不按出口 IP 计配额，年龄门靠 Cookie 过。
JAVBUS = SiteConfig(name="javbus", label="JavBus", provider="javbus-page",
                    base_url="https://www.javbus.com", domains=("javbus.com",),
                    stage="community", cookie=True)

_FIELD = re.compile(r'<p><span class="header">([^<:：]+)[:：]</span>(.*?)</p>', re.S)
_TITLE = re.compile(r"<h3>([^<]*)</h3>")
_COVER = re.compile(r'<a class="bigImage" href="([^"]+)"')
_ACTRESS = re.compile(r'<div class="star-name"><a[^>]*>([^<]+)</a>')


class JavBusSource(SiteSource):
    DEFAULT = JAVBUS

    def work_url(self, code: str) -> str:
        return f"{self.config.base_url}/{urllib.parse.quote(code)}"

    def fetch(self, code: str, *, session: Session) -> Page:
        try:
            return session.get(self.work_url(code), config=self.config)
        except NotFound:
            raise SourceFailure(FailureReason.NOT_FOUND, "JavBus 没有这个番号", status_code=404) from None

    def parse(self, page: Page, code: str) -> SiteRecord:
        text = page.text
        fields = {clean(label): clean(value) for label, value in _FIELD.findall(text)}
        shown = fields.get("識別碼", "").replace(" ", "")
        if not shown:
            raise SourceFailure(FailureReason.AUTH_REQUIRED,
                                "JavBus 回的不是作品页，多半是年龄确认页：到采集设置给 JavBus 贴上浏览器里的 Cookie")
        if not same_release_code(code, shown):
            raise SourceFailure(FailureReason.NOT_FOUND, "JavBus 没有这个番号")
        heading = _TITLE.search(text)
        cover = _COVER.search(text)
        runtime = re.search(r"\d+", fields.get("長度", ""))
        return SiteRecord(
            source=self.config.name, provenance=self.config.provider, code=shown, source_url=page.url,
            title=clean(heading.group(1)).removeprefix(shown).strip() if heading else "",
            performers=tuple({"japanese_name": clean(name)} for name in _ACTRESS.findall(text)),
            studio=fields.get("製作商", ""), label=fields.get("發行商", ""),
            series=fields.get("系列", ""), director=fields.get("導演", ""),
            release_date=fields.get("發行日期", ""), runtime=int(runtime.group()) if runtime else None,
            cover_urls=(urllib.parse.urljoin(self.config.base_url, cover.group(1)),) if cover else ())
