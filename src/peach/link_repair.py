"""外部入口的形状判据，以及在站点上把正确地址找回来的那套动作。

用户的要求是「外部入口要直达对象页」。一条链接违反它有好几种形态，而它们的处置各不
相同，所以判据要分开写、分开命名：

- **目录页**：`official.nax-pro.com/model/` 是全站模特列表，不是某一个人。它 200、
  有图、有名字，从后台看不出任何异常——只有点进去才发现落在列表上。
- **检索页**：`video.dmm.co.jp/av/list/?key=<名字>` 把「这个人在那边是谁」交给站内
  检索去猜，而同名的人正是最需要点进去核对的那一批。
- **明文 http**：站点早已提供 https 时，账本里留着 http 只是采集那天抄下来的形态。
- **HTML 实体未还原**：`model.php?alias=x&amp;id=176` 是从页面源码里抄地址时把 `&amp;`
  一起抄了进来。服务端把 `amp;id` 当成一个没人认识的参数，于是 `id` 丢失。

判据住在这里而不是某个脚本里：`rediscover_entity_links` 与 `repair_entity_links` 都要
用同一套，而同一条判据有多份实现时，修好一份不等于修好这件事。

**找回地址的判据是「站点自己承认这个人」**，不是我们拼出一个能打开的地址：在死链或
目录页所在站点的索引页里找锚点（href 或锚文本含这个人的名字），候选必须回 200 且
标题里有这个人的名字。少了标题这一关，`/talent/` 这种列表页本身也会 200，然后一批人
全被改写成同一个列表地址——一条指向别人的链接比一条死链更糟，因为它看起来是对的。
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import tldextract

_PSL = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)

ANCHOR = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\']([^>]*)>(.*?)</a>', re.S | re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

#: 路径末段是这些词时，这一页讲的是「有哪些人」而不是「这个人」。
#: 全部取自本机账本里 official 链接的实际末段，不是想象出来的词表。
DIRECTORY_SEGMENTS = frozenset({
    "model", "models", "modellist", "model-list", "model_list", "model-introduction",
    "modelgallery", "modelgallerys", "talent", "talents", "talent-list",
    "actress", "actresses", "actress_list", "actresslist", "actor", "actors",
    "artist", "artists", "girl", "girls", "member", "members", "cast", "casts",
    "people", "profile", "profiles", "gallery", "bloglist", "list",
})
#: 这些末段是「这一层的首页」而不是一个对象，判断前先摘掉。
#: 不先摘掉它，`8man.jp/models/kobatomugi/index.html` 会因为末段是 `index` 被判成目录页，
#: 而它是小鸠むぎ本人的页面——目录词表对它的上一段根本没看。
INDEX_FILENAMES = frozenset({"index", "default", "home", "top"})
#: 这些查询参数装的是检索词。`actress`、`actress_id`、`name`、`id`、`idx` 不在其中——
#: 它们装的是站内编号或别名，那正是直达页的形态。
SEARCH_QUERY_KEYS = frozenset({
    "key", "keyword", "keywords", "q", "query", "word", "words", "search",
    "search_word", "searchword", "searchstr", "s", "actor[]", "actress[]", "term",
})


def registrable(host: str) -> str:
    """固定离线 PSL 含 PRIVATE 区段；不同托管租户保持独立。"""
    return _PSL(host.casefold().rstrip(".")).top_domain_under_public_suffix


def same_site(candidate: str, original: str) -> bool:
    a = registrable(urlsplit(candidate).hostname or "")
    b = registrable(urlsplit(original).hostname or "")
    return bool(a) and a == b


def index_candidates(url: str, *, include_self: bool = False) -> list[str]:
    """从这条链接逐层上溯的索引页，最深的先试。

    艺人页几乎总挂在某个列表下面，而站点改版通常只动其中一层
    （`/official/talent/X` → `/talent/X/`），上一层的列表往往原地还在。

    `include_self` 给目录页用：那条链接本身就是索引，不能像死链那样先砍掉末段——
    砍掉 `/model/` 只剩站点根，而人名全在 `/model/` 那一页上。
    """
    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}"
    out: list[str] = []
    if include_self:
        out.append(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    for cut in range(len(segments) - 1, -1, -1):
        candidate = root + "/" + "/".join(segments[:cut]) + ("/" if cut else "")
        if candidate not in out:
            out.append(candidate)
    if root + "/" not in out:
        out.append(root + "/")
    return out[:4]


def anchors_naming(html: str, base: str, names: list[str]) -> list[tuple[str, str]]:
    """索引页里提到这些名字的链接，返回 (绝对地址, 命中的名字)。

    href 也要看：日文站的艺人页地址常常就是 URL 编码后的名字，而锚文本可能只是一张图。
    """
    found: list[tuple[str, str]] = []
    for match in ANCHOR.finditer(html):
        href = match.group(1)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(3))).strip()
        haystack = text + " " + unquote(href)
        for name in names:
            if name and name in haystack:
                target = urljoin(base, href)
                if urlsplit(target).scheme in {"http", "https"}:
                    found.append((target, name))
                break
    return found


def page_title(html: str) -> str:
    """`<title>` 的纯文本；没有就是空串。"""
    match = TITLE.search(html or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()


def confirms(html: str, names: list[str]) -> str:
    """页面标题里有没有这个人的名字；有就返回命中的写法。

    列表页同样回 200。少了这一条，`/talent/` 本身会被当成每个人的新地址。
    """
    title = page_title(html)
    # 站点常把姓与名之间加空格（`涼森 れむ`），逐字比会漏掉。
    squeezed = title.replace(" ", "").replace("　", "")
    for name in names:
        if name and (name in title or name.replace(" ", "") in squeezed):
            return title[:90]
    return ""


def _stem(segment: str) -> str:
    text = unquote(segment).casefold()
    return text.rsplit(".", 1)[0] if "." in text else text


def _meaningful_segments(url: str) -> list[str]:
    """路径分段，摘掉末尾的「这一层的首页」文件名。"""
    segments = [_stem(segment) for segment in urlsplit(url).path.split("/") if segment]
    while segments and segments[-1] in INDEX_FILENAMES:
        segments.pop()
    return segments


def is_search_page(url: str) -> bool:
    """这条链接是站内检索而不是某个对象的页面。

    两个信号各自成立即可：路径上有 `search` 那一段（`/search/`、`cSearch.php`、
    `search_result.php`），或者查询里带检索词参数。
    """
    parts = urlsplit(url)
    for segment in parts.path.split("/"):
        stem = _stem(segment)
        if stem.startswith("search") or stem.endswith("search"):
            return True
    keys = parse_qs(parts.query, keep_blank_values=True)
    return any(key.casefold() in SEARCH_QUERY_KEYS for key in keys)


def directory_reason(url: str, deeper_paths: Iterable[str] = ()) -> str:
    """这条链接落在目录页上的原因；不是目录页就返回空串。

    `deeper_paths` 是同一站点上别的链接的路径。一条链接是另一条的上级目录，说明它
    讲的是「这一层有哪些人」——这个判据从数据本身长出来，不必逐站写死词表。
    """
    parts = urlsplit(url)
    segments = _meaningful_segments(url)
    base = parts.path.rstrip("/")
    if not segments and not parts.query:
        return "站点根"
    if any(other.rstrip("/") != base and other.rstrip("/").startswith(base + "/")
           for other in deeper_paths):
        return "同站另有更深的链接挂在它下面"
    if not parts.query and segments[-1] in DIRECTORY_SEGMENTS:
        return "路径末段是目录词"
    return ""


def same_document_path(left: str, right: str) -> bool:
    """两个地址指的是不是同一篇文档：路径分段逐段相同，扩展名与末尾斜杠不算数。

    换协议时站点常顺手做点整理——`/special/x.php` 跳到 `/special/x`、补上末尾斜杠。
    那还是同一篇。跳到另一条路径上去就不是了：`t-powers.co.jp` 把一个拼错的
    `/telent/…` 跳到 `/release/`，`l-promotion.com` 把 `/naiyou.html` 跳到 `/blog/`，
    两头的标题当然一致——那证明的是「都落在同一张兜底页上」，不是「这是她的页面」。
    """
    return _meaningful_segments(left) == _meaningful_segments(right)


def bare_root(url: str) -> bool:
    """路径上没有任何一段实质内容，只剩站点根（查询串不算）。

    换地址之后要用它把关：`bambi.ne.jp` 把 `/official/model.php?alias=…` 整段重定向到
    `/?alias=…`，两头的标题因此完全一致——只比标题会把一条指向个人页的链接换成站点首页，
    而且「新旧标题一致」看起来还像是很强的证据。
    """
    return not _meaningful_segments(url)


def html_entity_leak(url: str) -> bool:
    """地址里留着未还原的 HTML 实体。`&amp;id=176` 让服务端收到的参数名是 `amp;id`。"""
    return unescape(url) != url


def without_entities(url: str) -> str:
    return unescape(url)


def upgraded_scheme(url: str) -> str:
    """同一个地址的 https 形态；本来就是 https 就返回空串。"""
    parts = urlsplit(url)
    if parts.scheme != "http":
        return ""
    return urlunsplit(parts._replace(scheme="https"))


def empty_path_segment(url: str) -> bool:
    """路径里有空段（`/talent//`）。多半是拼接时多带了一个斜杠。"""
    return "//" in urlsplit(url).path
