"""fc2cmadb 的作品页：下架 FC2 的镜像，以及它评论区里用户长期维护的演员与等价标记。

下架的商品在 fc2cmadb 上还留着：它是个 Laravel + Inertia 的镜像站，整棵 props 树放在
`<script type="application/json">` 里，字段与商品页一一对得上——本地那批没封面的 FC2
多半只能从这里取（实测 `FC2-PPV-3189161` 官方页已空，镜像给出 3456×1942 的原图）。请求带用户
在采集设置里贴的 Cookie，按登录用户看到的那一页取（见下一段）。封面指向 `storage*.contents.fc2.com`
上与商品页同一个文件，镜像有时给的是 `contents-thumbnail*.fc2.com/w276/` 包装过的缩略图地址，
`storage_original` 把包装拆掉；站上没有商品图时挂的是它自己那张占位件，判成没有图。

这一站另有一栏对得上人的女優，那是 Inertia 的延迟 prop——首屏那份 HTML 里没有，要带上
这一页自报的握手版本号把这一栏单独再问一次才给（`partial_headers`），一次一千来字节，还附
一串曾用名。`query()` 在解析出作品之后补这一跳；问不出来就按没有女优落。浏览器里这一栏只对
登录用户显示；2026-09-23 实测游客补问也照样回（`2851534`、`3518061` 两边一致），带 Cookie
是为站方收紧游客访问时不断档。

评论区那条线（`scripts/fetch_fc2_metadata.py`）读同一页的 props：演员标记 `2724256　未歩なな`、
「一行名字 + 若干作品链接」，以及把同一段内容在不同 video_id 下的发布对应起来的等价标记
`3312576-4 = 2471432`。解析函数在这里，汇总、收获表与复核候选在脚本里。
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from collections.abc import Mapping

from bs4 import BeautifulSoup

from ..jav_cover_fetch import Unavailable
from ..scraping_access import SourcePaused
from .base import FailureReason, Page, Session, SiteConfig, SiteRecord, SiteSource, SourceFailure
from .fc2 import PAGE_LIMIT, fc2_record, runtime_minutes, seller_page, storage_original, unrecognised, video_id

#: 按社区来源登记：它转载的是发行方那一页，但标题和标签由站方用户维护。主机间隔用默认的 2 秒。
FC2CMADB = SiteConfig(name="fc2cmadb", label="FC2CMADB", provider="fc2cmadb-article",
                      base_url="https://fc2cmadb.com", domains=("fc2cmadb.com",), stage="community",
                      cookie=True, page_limit=PAGE_LIMIT)
ARTICLE_PATH = "/articles/{video_id}"
#: 作品页的 Inertia 组件名；站上没有的商品回的是 `Error`。
COMPONENT = "Articles/Show"

#: FC2 的 video_id 是 6-8 位纯数字，短于这个长度的多半是楼层号或年份。
VIDEO_ID = re.compile(r"\d{6,8}")
#: `2724256　未歩なな　皐月`：ID 开头，其后全是名字，中间只允许空白。
PERFORMER_LINE = re.compile(r"^(\d{6,8})[\s　]+(\S.*)$")
#: 等价项形如 `3312576-4`、`3312576_4`、`2471432`。
EQUIV_TOKEN = re.compile(r"(\d{6,8})(?:[-_](\d{1,2}))?")
#: 评论里用 `bad:` 起一段列对不上的分片，那之后的 `=` 不是等价断言。
BAD_HEADER = re.compile(r"^\s*bad\s*[:：]", re.I)
#: 另一种演员标记：一行名字，后面跟若干作品链接（多指向姊妹站 fc2ppvdb）。
ARTICLE_LINK = re.compile(r"https?://[\w.]*fc2(?:ppvdb|cmadb)\.com/articles/(\d{6,8})")
#: 日文输入法打出的是全角等号，占实际写法的一部分，不认就整条读不到。
FULLWIDTH = str.maketrans("＝－＿０１２３４５６７８９", "=-_0123456789")


def inertia_page(html: str | bytes) -> dict:
    """页面里那份 Inertia 数据（`component`、`version`、`props`）；没有就回空。站上是 UTF-8，按它解码，
    不让 BeautifulSoup 去猜：短页面上它会猜成别的编码，日文就全成了乱码。"""
    text = html.decode("utf-8", "replace") if isinstance(html, bytes) else str(html)
    for node in BeautifulSoup(text, "html.parser").find_all("script", type="application/json"):
        try:
            data = json.loads(node.string or "")
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("props"), dict):
            return data
    return {}


def inertia_props(html: str | bytes) -> dict:
    return inertia_page(html).get("props") or {}


def partial_headers(html: str | bytes) -> dict[str, str]:
    """把女优那一栏单独再问一次要用的请求头；手里这一页不是作品页就回空。

    版本号是站上那份前端资源的指纹，对不上就不给这一栏只给整页，所以每次都取自手里
    这一页而不是记下来重用。只点名 `actresses`：连 `article` 一起要，回来的是同一份刚
    读完的资料，白花三倍字节。站上没有的商品回的是错误页，那一页问也问不出人来。
    """
    page = inertia_page(html)
    version = str(page.get("version") or "").strip()
    if not version or str(page.get("component") or "").strip() != COMPONENT:
        return {}
    return {
        "X-Inertia": "true",
        "X-Inertia-Version": version,
        "X-Inertia-Partial-Component": COMPONENT,
        "X-Inertia-Partial-Data": "actresses",
        "Accept": "text/html, application/xhtml+xml",
    }


def parse_actresses(payload: str | bytes) -> list[dict]:
    """那一跳回来的 JSON → 每位女优一份 `{japanese_name, external_id, alias_names}`。

    同名一位只留一个。`external_id` 是站上人物页的编号：同一个人在站上只有一页，落库时
    登记成外部编号，她以后的作品按编号挂回同一条实体，不看站上这次给她起了什么称呼。

    `alias_name` 那一串按空白拆开放进 `alias_names`，不放 `aliases`：一位女优能挂十几个，
    摊进演员栏就成了十几个人；而这一串里混着卖家起的商品名（`お隣のスレンダー美女`），
    放进 `aliases` 会跟着资料页证据被直接登记成别名。它只进来源快照，由补别名后继按
    自己的判据读（ADR-0061）。
    """
    try:
        data = json.loads(payload if isinstance(payload, str) else bytes(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    props = data.get("props") if isinstance(data, dict) else None
    listed = props.get("actresses") if isinstance(props, dict) else None
    if not isinstance(listed, list):
        return []
    found: dict[str, dict] = {}
    for one in listed:
        if not isinstance(one, dict):
            continue
        name = str(one.get("name") or "").strip()
        if not name or name in found:
            continue
        person = {"japanese_name": name}
        if str(one.get("id") or "").strip().isdigit():
            person["external_id"] = str(one["id"]).strip()
        aliases = alias_names(one.get("alias_name"))
        if aliases:
            person["alias_names"] = aliases
        found[name] = person
    return list(found.values())


def alias_names(raw) -> list[str]:
    """人物页 `alias_name` 那一格 → 写法列表。站上用空白分隔，全角半角都有。"""
    return list(dict.fromkeys(part for part in re.split(r"[\s　]+", str(raw or "")) if part))


def page_comments(props: Mapping) -> list[dict]:
    """按 id 去重。`comments.data` 与 `article.comments` 两份是重叠的，直接相加
    会让同一条评论投两票，把「两条独立评论都这么说」这个置信度信号做废。"""
    seen: dict[object, dict] = {}
    both = list((props.get("comments") or {}).get("data") or [])
    both += list((props.get("article") or {}).get("comments") or [])
    for index, comment in enumerate(both):
        seen.setdefault(comment.get("id", f"#{index}"), comment)
    return list(seen.values())


def parse_performers(body: str) -> dict[str, list[str]]:
    """取演员标记，返回 {video_id: [名字]}。两种写法都算数。

    行内式 `2724256　未歩なな`，以及「一行名字 + 若干作品链接」式——后者多指向
    姊妹站 fc2ppvdb，语义是同一个人的作品集，同样是人工攒出来的标记。
    """
    body = body.translate(FULLWIDTH)
    found: dict[str, list[str]] = {}
    linked = ARTICLE_LINK.findall(body)
    if linked:
        heads = [line.strip() for line in body.splitlines()
                 if line.strip() and not line.strip().startswith("http")
                 and not VIDEO_ID.search(line) and "=" not in line]
        if len(heads) == 1:
            for video in linked:
                found.setdefault(video, []).append(heads[0])
    for line in body.splitlines():
        line = line.strip()
        # 带 `=` 的是等价标记，不是演员标记；`*` 是分隔用的装饰行。
        if not line or "=" in line or line == "*":
            continue
        match = PERFORMER_LINE.match(line)
        if not match:
            continue
        names = [name for name in re.split(r"[\s　]+", match.group(2).strip())
                 if name and not VIDEO_ID.fullmatch(name)]
        if names:
            found.setdefault(match.group(1), []).extend(names)
    return found


def parse_equivalences(body: str, subject: str = "") -> list[list[tuple[str, str]]]:
    """取等价组，每组是 [(video_id, part)]，part 缺省为空串。

    `3312576-1` 与紧跟其后的 `= 3090722-3` 是同一个断言写成了两行，所以以
    `=` 开头的行要并回上一行，否则整张合集映射表会一条都读不出来。

    整条评论只有 `＝2407240` 时主语被省略了，就是当前这一页；没有 `subject`
    就只能把这种断言丢掉。
    """
    body = body.translate(FULLWIDTH)
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if BAD_HEADER.match(line):
            break                      # `bad:` 之后全是否定标记，停止解析
        if not line:
            continue
        if line.startswith("=") and lines:
            lines[-1] = f"{lines[-1]} {line}"
        elif line.startswith("=") and subject:
            lines.append(f"{subject} {line}")
        else:
            lines.append(line)
    groups = []
    for line in lines:
        if "=" not in line or line.startswith("#"):
            continue
        tokens = [(m.group(1), m.group(2) or "") for m in EQUIV_TOKEN.finditer(line)]
        if len(tokens) >= 2:
            groups.append(tokens)
    return groups


def collection_parts(video: str, groups: list[list[tuple[str, str]]]) -> dict[str, str]:
    """本 ID 的分片 -> 对应的独立 video_id。够多才算合集。"""
    parts: dict[str, str] = {}
    for group in groups:
        mine = [tok for tok in group if tok[0] == video and tok[1]]
        others = [tok for tok in group if tok[0] != video]
        if len(mine) == 1 and others:
            parts.setdefault(mine[0][1], others[0][0])
    return parts


class Fc2cmadbSource(SiteSource):
    DEFAULT = FC2CMADB

    def article_url(self, code: str) -> str:
        """这个番号在 fc2cmadb 上的地址；认不出商品号时回空串。"""
        found = video_id(code)
        return self.config.base_url + ARTICLE_PATH.format(video_id=found) if found else ""

    def fetch(self, code: str, *, session: Session) -> Page:
        url = self.article_url(code)
        if not url:
            raise unrecognised()
        return session.get(url, config=self.config)

    def parse(self, page: Page, code: str) -> SiteRecord:
        """作品页 → 记录；对不上番号归 `not_found`。女优不在这一页里，由 `query()` 多问一跳补上。"""
        wanted = video_id(code)
        if not wanted:
            raise unrecognised()
        article = inertia_props(page.body).get("article")
        if not isinstance(article, dict) or str(article.get("video_id") or "").strip() != wanted:
            raise SourceFailure(FailureReason.NOT_FOUND, f"{self.config.label} 上没有这个商品")
        writer = article.get("writer") if isinstance(article.get("writer"), dict) else {}
        slug = str(writer.get("slug") or "").strip()
        return fc2_record(
            self.config, wanted, self.config.base_url + ARTICLE_PATH.format(video_id=wanted),
            title=str(article.get("title") or "").strip(),
            release_date=str(article.get("release_date") or "").strip(),
            runtime=runtime_minutes(article.get("duration")),
            label=str(writer.get("name") or "").strip(),
            # 镜像的 slug 与官方用户页的 slug 是同一个（实测 `otonakamenz` 两处一致），
            # 所以这一栏指回发行方自己那一页，而不是镜像的作者页。
            seller_url=seller_page(slug) if slug else "",
            tags=[str((tag or {}).get("name") or "").strip() for tag in article.get("tags") or []],
            cover_urls=[storage_original(article.get("image_url"))])

    def with_actresses(self, record: SiteRecord, page: Page, *, session: Session) -> SiteRecord:
        """点名 `actresses` 把女优那一栏再问一次。那一跳撞上冷却或限流是常事，其余字段是站上最全的
        一份，不跟着丢：问不出来就按没有女优交回。"""
        headers = partial_headers(page.body)
        if not headers:
            return record
        try:
            partial = session.get(page.url, config=self.config, referer=page.url, headers=headers).body
        except (SourcePaused, Unavailable):
            return record
        return replace(record, performers=tuple(parse_actresses(partial)))

    def query(self, code: str, *, session: Session) -> SiteRecord:
        with self.holding(session):
            page = self.fetch(code, session=session)
            return self.with_actresses(self.parse(page, code), page, session=session)
