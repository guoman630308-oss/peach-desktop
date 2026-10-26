"""从一个裸 id 或名字反查它在哪些站点上存在。

粘链接是精确的，但人手边常常只有一个名字或一串数字。这一层把那个词拿去各个来源
问一遍，把**查到的**结果摆出来让人选——不猜、不自动登记，查不到就说查不到。

联网只在显式调用 `discover()` 时发生。每个来源独立成败：一个站点挂了或缺凭据，
不该让其余来源的结果一起消失。
"""
from __future__ import annotations

import bisect
import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from .follow import FollowSourceError
from .follow_avatar import resolve_official_profile
from .follow_secrets import Credential, CredentialError, credential_store_for
from .follow_sources import (
    F95ZoneConnector, KemonoConnector, Rule34VideoConnector, Rule34XxxConnector,
    SimpCityConnector, canonical_source_ref,
)

#: 创作者索引的缓存有效期。索引是几 MB 的整站清单，不该每次发现都重下一遍。
CREATOR_INDEX_TTL_SECONDS = 24 * 3600

#: 单个来源最多回多少个候选。发现是给人看的，不是导出整站。
MAX_CANDIDATES_PER_SOURCE = 8

_NUMERIC_RE = re.compile(r"^\d{1,12}$")
#: 名字里不能出现的字符：路径分隔、查询串标点和控制字符。除此之外一律放行——
#: kemono 上大量创作者是日文或中文名（`うるしばら`、`冰鲜鱼子酱二代目`），
#: 用「只允许 ASCII」的白名单会把他们整批挡在门外。
_TERM_FORBIDDEN_RE = re.compile(r"[/\\?#&<>\"'`\x00-\x1f\x7f]")
MAX_TERM_LENGTH = 80


def identity_key(text: str) -> str:
    """跨站可比的身份键：只留字母数字并折叠大小写。

    同一个人在各站的写法差的是分隔符——`Ria_neearts`、`ria-neearts`、
    `RiaNeearts` 是同一个手柄。把分隔符抹掉之后才谈得上「这条命中的是不是他」。
    字母数字按 Unicode 算：`鈴太郎_3D` 与 `鈴太郎-3d` 同一个键，中日文名不会被抹成空串。
    """
    return re.sub(r"[\W_]+", "", str(text).casefold())


def spelling_variants(term: str) -> tuple[str, ...]:
    """一个手柄在各站可能的几种分隔符写法，原样排在最前。

    这和 `search_variants` 不是一回事：那个是给全文搜索按词切分用的，这个是给
    「站上的标识符长什么样」用的，连写和连字符都要试。
    """
    text = term.strip()
    parts = [part for part in re.split(r"[\s_.-]+", text) if part]
    variants = [text]
    if len(parts) > 1:
        for joiner in ("_", "-", "", " "):
            spelling = joiner.join(parts)
            if spelling not in variants:
                variants.append(spelling)
    return tuple(variants)


@dataclass(frozen=True)
class Candidate:
    """一个查到的、可以登记的来源。"""

    provider: str
    ref: str
    url: str
    label: str
    semantics: str
    #: 为什么认为它命中了。界面照实显示，不要替用户断言「就是这个」。
    evidence: str
    #: 作者名片上与检索词不同的手柄。人勾选登记这一条时，它们随之记成检索词的别名。
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExternalSearch:
    """站内索引没命中时，交给人继续核对的外部搜索入口。"""

    provider: str
    label: str
    query: str
    url: str
    evidence: str


@dataclass(frozen=True)
class Discovery:
    term: str
    candidates: tuple[Candidate, ...] = ()
    #: 逐来源的失败原因；界面要显示，否则「没查到」和「没查成」分不开。
    failures: dict[str, str] = field(default_factory=dict)
    #: 搜索引擎只提供继续核对的入口，不伪装成可直接登记的来源。
    external_searches: tuple[ExternalSearch, ...] = ()


#: 建议用的名字表，按清单文件缓存在进程里，值带着它的 mtime。清单一天刷一次，
#: 换了就重建；同一份清单只解析一次，之后每次敲字都是内存里的一次二分。
#: 2026-09-12 实测本机三份清单（kemono、pawchive、coomer 共 42 万个名字）：第一次
#: 建表 0.84 秒、驻留 57 MB，之后每次前缀查询 4 毫秒。这笔常驻内存是拿来换手速的，
#: 建表只发生在用户真的在添加框里敲字之后。
_NAME_TABLES: dict[Path, tuple[float, tuple[tuple[str, str], ...]]] = {}


class CreatorIndex:
    """kemono 系整站创作者清单的本机缓存。

    这三个站点没有按名字查创作者的接口，只有一份整站清单（实测 2–6 MB）。
    与其对着几十种服务名盲探 `/{service}/user/{id}/profile`，不如下一次清单，
    id 和名字都能精确命中，而且缓存一天之后后续发现是零网络开销。
    """

    def __init__(self, state_root: Path, *, transport=None, ttl: float = CREATOR_INDEX_TTL_SECONDS):
        self.root = Path(state_root) / "follow"
        self.transport = transport
        self.ttl = ttl

    def _path(self, provider: str) -> Path:
        return self.root / f"creators-{provider}.json"

    def load(self, provider: str, *, now: float | None = None) -> list[dict]:
        path = self._path(provider)
        now = now if now is not None else time.time()
        try:
            if path.is_file() and (now - path.stat().st_mtime) < self.ttl:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(cached, list):
                    return cached
        except (OSError, ValueError):
            pass
        return self._refresh(provider, path)

    def names(self, provider: str) -> tuple[tuple[str, str], ...]:
        """已经下过的那份清单里的创作者名，`(casefold, 原样)` 按 casefold 排好序。

        **这一条不联网。** 建议是敲一个字就要出来的东西，为它现下一份几 MB 的整站
        清单会让输入框卡住十几秒；清单不在就没有这一组，等用户真的查一次名字，
        `discover` 会把它下下来，下一次敲字就有了。

        排序是为了 `bisect` 取前缀区间：十万条的清单每敲一下全扫一遍，和建议要的
        「跟着手速出」不是一回事。
        """
        path = self._path(provider)
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return ()
        cached = _NAME_TABLES.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        table = []
        for row in rows if isinstance(rows, list) else ():
            name = str(row.get("name") or "") if isinstance(row, dict) else ""
            if not name:
                continue
            folded = name.casefold()
            # 名字本身就是小写时两个位置指同一个字符串对象：清单里这样的占大多数，
            # 省下的是整整半份表。
            table.append((folded, name if name != folded else folded))
        table.sort()
        _NAME_TABLES[path] = (stamp, tuple(table))
        return _NAME_TABLES[path][1]

    def _refresh(self, provider: str, path: Path) -> list[dict]:
        connector = KemonoConnector(provider=provider, transport=self.transport,
                                    max_bytes=48 * 1024 * 1024)
        payload = connector.fetch_json(f"https://{connector.host}/api/v1/creators")
        if not isinstance(payload, list):
            raise FollowSourceError(f"{provider} 的创作者清单格式不符")
        rows = [
            {"id": str(row.get("id") or ""), "name": str(row.get("name") or ""),
             "service": str(row.get("service") or "")}
            for row in payload if isinstance(row, dict) and row.get("id")
        ]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            # 缓存写不进去不影响这一次发现，只是下次要重下。
            pass
        return rows


def _kemono_candidates(provider: str, term: str, index: CreatorIndex) -> list[Candidate]:
    rows = index.load(provider)
    folded = term.casefold()
    # id 的形状跟服务走：fanbox 的 id 是数字，patreon、switch 这些直接拿创作者
    # 手柄当 id。精确比对把两种都收进来，只认数字会把手柄当 id 的来源整个排除。
    # 手柄的大小写只是显示形态，`SuzuTaro3D` 和 `suzutaro3d` 是同一个创作者，
    # 所以按 casefold 比；数字 id 过一遍 casefold 也还是它自己。
    exact_id = [row for row in rows if row["id"].casefold() == folded]
    by_name = [row for row in rows if row["name"].casefold() == folded]
    # 部分命中的排法与 `archive_suggestions` 同一个键：以该词开头的在前，名字短的在前。
    # 下拉里给过的名字按回车之后必须还在结果里；按清单原序截八条的话，子串命中多的
    # 词会把下拉里那几位整批挤出去。
    partial = sorted((row for row in rows
                      if folded in row["name"].casefold() and row not in by_name),
                     key=lambda row: (not row["name"].casefold().startswith(folded),
                                      len(row["name"]), row["name"].casefold()))
    host = KemonoConnector.HOSTS[provider]
    picked, seen = [], set()
    for row, why in ([(r, "站内 id 精确匹配") for r in exact_id]
                     + [(r, "创作者名精确匹配") for r in by_name]
                     + [(r, "创作者名以该词开头" if r["name"].casefold().startswith(folded)
                         else "创作者名包含该词") for r in partial]):
        key = (row["service"], row["id"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(Candidate(
            provider, f"{row['service']}/{row['id']}",
            f"https://{host}/{row['service']}/user/{row['id']}",
            f"{row['name']} · {row['service']}", "work", why))
        if len(picked) >= MAX_CANDIDATES_PER_SOURCE:
            break
    return picked


def _fanbox_candidates(archive_candidates: list[Candidate], transport) -> list[Candidate]:
    """Resolve verified FANBOX archive identities to their official creator pages."""
    user_ids: dict[str, tuple[str, ...]] = {}
    for candidate in archive_candidates:
        service, separator, user_id = candidate.ref.partition("/")
        if (candidate.provider in KemonoConnector.HOSTS and separator
                and service == "fanbox" and user_id.isdigit()
                and user_id not in user_ids):
            user_ids[user_id] = candidate.aliases
    candidates = []
    for user_id, aliases in list(user_ids.items())[:MAX_CANDIDATES_PER_SOURCE]:
        profile = resolve_official_profile("fanbox", user_id, transport=transport)
        candidates.append(Candidate(
            "fanbox", profile.creator_id, profile.url,
            profile.name, "work", "FANBOX 官方资料与归档身份一致", aliases,
        ))
    return candidates


def rule34video_slugs(term: str) -> tuple[str, ...]:
    """一个名字在 rule34video 上可能的作者页 slug，按先后去重。

    KVS 由显示名生成 slug，连续的非字母数字换成一个 `-`：`Bulging Senpai` 的页是
    `bulging-senpai`，写成 `bulgingsenpai` 回 404（2026-09-26 实测）。名字本身连写
    （`LazyProcrastinator`）时 slug 也连写，所以驼峰拆开的写法排在原样之后，全去掉
    分隔的写法垫底。
    """
    forms = [re.sub(r"[^a-z0-9]+", "-", variant.casefold()).strip("-")
             for variant in search_variants(term)]
    forms.append(re.sub(r"[^a-z0-9]+", "", term.casefold()))
    slugs: list[str] = []
    for slug in forms:
        if slug not in slugs and Rule34VideoConnector._SLUG_RE.match(slug):
            slugs.append(slug)
    return tuple(slugs)


def _rule34video_candidates(term: str, transport) -> list[Candidate]:
    """按顺序探作者页，第一个存在的就是它。

    几种写法都存在时那是几个人，只回先命中的那个：一个名字登记两个作者页，比漏一个更难察觉。
    """
    connector = Rule34VideoConnector(transport=transport)
    for slug in rule34video_slugs(term):
        url = f"https://rule34video.com/models/{slug}/"
        if connector.probe(url, headers={"Accept": "text/html"}).status == 200:
            return [Candidate("rule34video", slug, url, term.strip(), "work", "作者页存在")]
    return []


def _rule34xxx_tag_url(tag: str) -> str:
    return ("https://rule34.xxx/index.php?page=post&s=list"
            f"&tags={urllib.parse.quote(tag)}")


def _rule34xxx_candidates(term: str, transport, credential: Credential | None) -> list[Candidate]:
    """按标签补全反查站上真实的写法，列出的与添加框建议里的是同一批标签。

    写法差一个分隔符就是零命中：`Ria_neearts` 逐字当标签什么都查不到，站上写作
    `ria-neearts`，248 件作品。所以按 `spelling_variants` 逐个写法问补全，抹掉分隔符
    后与输入相同的是精确命中，以它开头的是前缀命中。补全公开、不要凭据；凭据仍然是
    **抓取**这条订阅的前提。

    建议和查找是同一件事的两步：下拉里给过的标签，按回车之后必须还在结果里。所以
    精确命中排最前，前缀命中照列在后、证据写明以该词开头，由人分辨是不是同一个人；
    有凭据时按站方分类把作者排在其余标签前，与下拉里作者一组在标签组之前同一个顺序。

    补全一次只回十条，热门前缀会把完整写法挤出去。补全里没有精确命中而手上有凭据时，
    再按原样问一次 dapi，标签下有帖子就是精确命中。
    """
    wanted = identity_key(term)
    if not wanted:
        return []
    connector = Rule34XxxConnector(transport=transport, credential=credential, max_items=1)
    #: 标签 → 帖子数；`None` 是「有帖子，但补全没报数」。
    exact: dict[str, int | None] = {}
    partial: dict[str, int | None] = {}
    for probe in spelling_variants(term):
        for tag, count in connector.autocomplete(probe):
            key = identity_key(tag)
            if key.startswith(wanted):
                (exact if key == wanted else partial).setdefault(
                    canonical_source_ref("rule34xxx", tag), count)
        # 精确写法有命中就不必再试其余分隔符。
        if exact:
            break
    if not exact and credential is not None:
        tag = canonical_source_ref("rule34xxx", re.sub(r"\s+", "_", term.strip()))
        try:
            if connector.search(tag, limit=1):
                exact[tag] = None
        except (FollowSourceError, CredentialError):
            # 这一问只为找回挤出补全的精确写法；问不成，补全给的前缀命中照列。
            pass
    hits = [(tag, count, True) for tag, count in exact.items()]
    hits += [(tag, count, False) for tag, count in partial.items() if tag not in exact]
    hits = hits[:MAX_CANDIDATES_PER_SOURCE]
    types = _tag_types(connector, [tag for tag, _count, _exact in hits])
    # 排序是稳定的：同一档里保持站方补全给的顺序（按帖子数从多到少）。
    hits.sort(key=lambda hit: (not hit[2], types.get(hit[0]) != "artist"))
    picked: list[Candidate] = []
    for tag, count, is_exact in hits:
        if count is None:
            evidence = f"站内标签 {tag} 下有作品"
        elif is_exact:
            evidence = f"站内标签 {tag} 下有 {count} 件作品" if count else f"站内存在标签 {tag}"
        else:
            evidence = (f"站内标签以该词开头：{tag} 下有 {count} 件作品" if count
                        else f"站内存在以该词开头的标签 {tag}")
        kind = types.get(tag, "")
        if kind and kind != "general":
            evidence += f"，站上归为{TAG_TYPE_LABELS.get(kind, kind)}"
        picked.append(Candidate("rule34xxx", tag, _rule34xxx_tag_url(tag),
                                tag.replace("_", " "), "work", evidence))
    return picked


def search_variants(term: str) -> tuple[str, ...]:
    """一个词的几种写法。

    f95 的全文搜索按词匹配，`lazyprocrastinator` 搜不到而 `lazy procrastinator` 能。
    下划线、连字符和大小写边界都是明确的切分信号；全小写的连写仍然切不开，
    那就只用原词，不去猜词典。
    """
    text = term.strip()
    variants = [text]
    separated = re.sub(r"[_-]+", " ", text).strip()
    if separated != text and separated not in variants:
        variants.append(separated)
    split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text).strip()
    if split != text and split not in variants:
        variants.append(split)
    return tuple(variants)


#: 加尾部通配符的最短词长。三个字母加通配等于把半个站搜回来，
#: 命中一屏反而帮不了人认出是哪个作者。
MIN_WILDCARD_TERM_LENGTH = 4


def forum_queries(term: str) -> tuple[str, ...]:
    """XenForo 站内搜索按顺序要试的几个查询，通配那一轮排在最后。

    站内搜索按**整词**匹配：2026-09-12 实测 `strauz` 命中 0 条，而 `strauzek`、
    `Mr_Strauz` 与 `strauz*` 都命中同样 3 条（含
    `Strauzek Collection [2026-09-04] [Mr_Strauz]`）。人手边常常只有名字的开头，
    所以各种写法都空手之后再补一轮尾部通配；它排在最后，精确写法有命中就用不上。
    """
    queries = list(search_variants(term))
    text = term.strip()
    if len(text) >= MIN_WILDCARD_TERM_LENGTH and "*" not in text:
        wildcard = f"{text}*"
        if wildcard not in queries:
            queries.append(wildcard)
    return tuple(queries)


def _f95_external_search(term: str) -> ExternalSearch:
    """Google 由浏览器打开；Peach 不抓结果页，也不绕验证码。"""
    query = f"{term} f95zone"
    return ExternalSearch(
        provider="f95zone",
        label="用 Google 继续查找 F95zone",
        query=query,
        url="https://www.google.com/search?" + urllib.parse.urlencode({"q": query}),
        evidence="F95zone 站内索引未命中，请核对搜索结果里的真实线程链接",
    )


def _forum_candidates(provider: str, host: str, term: str, connector) -> list[Candidate]:
    """XenForo 站内搜索按标题命中的线程，f95zone 与 simpcity 共用。

    站内搜索必须带登录 cookie；调用方先判断有没有。命中行带回的版块标签
    （`OnlyFans`、`Simp Chat`、`Collection`）写进证据：同一个名字的资源线程和
    讨论帖都会命中，人要靠这个分辨。
    """
    picked: list[Candidate] = []
    seen: set[str] = set()
    for query in forum_queries(term):
        how = "按标题开头命中" if query.endswith("*") else "按标题命中"
        for row in connector.search_threads(query):
            thread = str(row.get("thread_id") or "")
            if not thread or thread in seen:
                continue
            seen.add(thread)
            labels = "、".join(str(label) for label in row.get("labels") or () if label)
            picked.append(Candidate(
                provider, thread, f"https://{host}/threads/{thread}/",
                str(row.get("title") or f"线程 {thread}"), "release",
                f"站内搜索{how}「{query}」" + (f"，版块标签 {labels}" if labels else "")))
            if len(picked) >= MAX_CANDIDATES_PER_SOURCE:
                return picked
        if picked:
            return picked
    return picked


def _simpcity_candidates(term: str, transport,
                         credential: Credential | None = None) -> list[Candidate]:
    """simpcity 只有站内搜索这一条路，而它必须登录。

    没有 cookie 就静默跳过：来源筛选菜单里已经标着「需要配置凭据」，每查一个名字
    都在结果里再报一次只是噪音。裸数字不去试——simpcity 的线程页对游客是 403，
    对登录用户任何数字都可能存在，「存在」不构成「就是他」的证据。
    """
    if credential is None or not credential.values.get("cookie"):
        return []
    connector = SimpCityConnector(transport=transport, credential=credential)
    return _forum_candidates("simpcity", SimpCityConnector.HOST, term, connector)


def _f95_candidates(term: str, transport,
                    credential: Credential | None = None) -> list[Candidate]:
    connector = F95ZoneConnector(transport=transport, credential=credential)
    picked: list[Candidate] = []
    if _NUMERIC_RE.match(term):
        response = connector.probe(f"https://f95zone.to/threads/{term}/",
                                   headers={"Accept": "text/html"})
        if response.status == 200:
            picked.append(Candidate("f95zone", term,
                                    f"https://f95zone.to/threads/{term}/",
                                    f"线程 {term}", "release", "线程存在"))
        return picked
    seen: set[str] = set()
    for query in search_variants(term):
        for category in connector.CATEGORIES:
            for row in connector.thread_index(category, query):
                thread = str(row.get("thread_id") or "")
                if not thread or thread in seen:
                    continue
                seen.add(thread)
                version = str(row.get("version") or "").strip()
                picked.append(Candidate(
                    "f95zone", thread, f"https://f95zone.to/threads/{thread}/",
                    str(row.get("title") or f"线程 {thread}"), "release",
                    f"{category} 分类命中" + (f"，当前版本 {version}" if version else "")))
                if len(picked) >= MAX_CANDIDATES_PER_SOURCE:
                    return picked
        if picked:
            return picked
    if credential is None or not credential.values.get("cookie"):
        # 没有 cookie 就搜不了站内，别把「查不到」说成「站上没有」——外部搜索入口还在。
        return picked
    # `latest_data.php` 只索引 Latest Updates 五个分类，艺术家的 Collection 帖发在
    # 普通版块里，怎么搜都不会出现；站内搜索能看到它们。
    return _forum_candidates("f95zone", "f95zone.to", term, connector)


#: 最多读几个 F95 命中线程的首楼名片。每读一个是一次登录态请求，排在后面的命中
#: 多半是标题碰巧带这个词的别人的线程。
MAX_PROFILE_THREADS = 2
#: 名片手柄最多拿几个去其余来源再查一遍。每个手柄都要把名字类来源重问一轮。
MAX_PROFILE_HANDLES = 4
#: 名片上不当作者别名的服务。论坛账号名常常是搬运工自己的账号，
#: 而 pixiv、Fantia 的身份是一串数字，当别名只会在列表里多出一个数字「作者」；
#: Bluesky 的手柄连着域名，不是名字。
PROFILE_ALIAS_SKIP_SERVICES = frozenset({"pixiv", "fantia", "bsky", "f95zone", "simpcity"})
#: 名片手柄会拿去再查一遍的来源：按名字、id 或标签查的那几家。
_HANDLE_SEARCH_PROVIDERS = frozenset({"kemono", "coomer", "pawchive", "rule34video",
                                      "rule34xxx", "simpcity"})
#: 只按名字或标签查、纯数字的词不去碰运气的来源。
_NAME_ONLY_PROVIDERS = frozenset({"rule34video", "rule34xxx", "simpcity"})

DEFAULT_PROVIDERS = ("f95zone", "kemono", "coomer", "pawchive", "fanbox", "rule34video",
                     "rule34xxx", "simpcity")


def discovery_plan(term: str, providers: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """这一轮实际会去问的来源，按执行顺序。

    F95 排第一：它的线程首楼整理了作者在其他平台的账号，名片上的手柄要赶在其余
    来源开查之前拿到。数字的词不去 rule34video / rule34.xxx / simpcity 碰运气——
    那三个都按名字或标签查——fanbox 只是把归档身份换算成官方页。计划先算出来，
    调用方才能把进度摊到每个来源上，而不是整轮查完只跳一格。
    """
    wanted = providers or DEFAULT_PROVIDERS
    return tuple(provider for provider in DEFAULT_PROVIDERS if provider in wanted
                 and not (provider in _NAME_ONLY_PROVIDERS and _NUMERIC_RE.match(term)))


def _f95_profile_handles(candidates: list[Candidate], term: str, transport,
                         credential: Credential | None) -> tuple[list[Candidate], list[str]]:
    """F95 命中线程首楼名片上、与检索词不同的手柄。

    搜 `cekc` 只能在 rule34.xxx 上碰到以它开头的标签，作者在那里写作 `Ceeeeekc`；
    F95 首楼替人把这些写法整理好了。读名片要登录 cookie，没有就不读，查找照常。
    返回带上各自手柄的 F95 候选，以及去重后要拿去其余来源再查的手柄。
    """
    if credential is None or not credential.values.get("cookie"):
        return candidates, []
    connector = F95ZoneConnector(transport=transport, credential=credential)
    wanted = identity_key(term)
    handles: dict[str, str] = {}
    marked: list[Candidate] = []
    for index, candidate in enumerate(candidates):
        if index >= MAX_PROFILE_THREADS:
            marked.append(candidate)
            continue
        try:
            links = connector.thread_profile(candidate.ref)["links"]
        except (FollowSourceError, CredentialError):
            links = ()
        own: list[str] = []
        for link in links:
            handle = str(link.get("handle") or "").strip()
            key = identity_key(handle)
            if (str(link.get("service") or "") in PROFILE_ALIAS_SKIP_SERVICES
                    or not key or key == wanted):
                continue
            if handle not in own:
                own.append(handle)
            handles.setdefault(key, handle)
        marked.append(replace(candidate, aliases=tuple(own)) if own else candidate)
    return marked, list(handles.values())[:MAX_PROFILE_HANDLES]


def discover(term: str, *, secrets_root: Path, state_root: Path,
             shared_root: Path | None = None, transport=None,
             providers: tuple[str, ...] | None = None,
             on_progress=None) -> Discovery:
    """把一个裸 id 或名字拿去各来源问一遍。

    只回**查到的**结果，每个都带上「为什么认为它命中」。查不到就是查不到，
    不按命名规律拼一个看起来像的链接——那种猜测登记之后永远抓不到东西。
    `on_progress(provider, index, total)` 在每个来源开查前调一次，供界面
    显示当前点名到哪一家；不传就不发。
    """
    text = (term or "").strip()
    if not text:
        raise FollowSourceError("请先输入要查找的名字或 id")
    if len(text) > MAX_TERM_LENGTH:
        raise FollowSourceError(f"名字或 id 最长 {MAX_TERM_LENGTH} 个字符")
    if _TERM_FORBIDDEN_RE.search(text):
        raise FollowSourceError("名字或 id 里不能包含 / \\ ? # & < > 引号或控制字符")

    credentials = credential_store_for(secrets_root, shared_root=shared_root)
    index = CreatorIndex(state_root, transport=transport)
    tasks = discovery_plan(text, providers)
    found: list[Candidate] = []
    failures: dict[str, str] = {}
    external_searches: list[ExternalSearch] = []

    handles: list[str] = []

    def run(name: str, fn) -> None:
        try:
            found.extend(fn(text))
        except (FollowSourceError, CredentialError) as error:
            failures[name] = str(error)
        if name not in _HANDLE_SEARCH_PROVIDERS:
            return
        for handle in handles:
            if name in _NAME_ONLY_PROVIDERS and _NUMERIC_RE.match(handle):
                continue
            try:
                hits = fn(handle)
            except (FollowSourceError, CredentialError):
                # 检索词本身那一问的成败已经报过；手柄是追加的一轮，问不成就少这几条。
                continue
            known = {(row.provider, canonical_source_ref(row.provider, row.ref)) for row in found}
            found.extend(
                replace(hit, evidence=f"F95 首楼名片上的手柄 {handle}：{hit.evidence}",
                        aliases=(handle,))
                for hit in hits
                if (hit.provider, canonical_source_ref(hit.provider, hit.ref)) not in known)

    def f95(term: str) -> list[Candidate]:
        credential = credentials.load("f95zone")
        hits, learned = _f95_profile_handles(
            _f95_candidates(term, transport, credential), term, transport, credential)
        handles.extend(learned)
        return hits

    runners = {
        "f95zone": f95,
        "kemono": lambda term: _kemono_candidates("kemono", term, index),
        "coomer": lambda term: _kemono_candidates("coomer", term, index),
        "pawchive": lambda term: _kemono_candidates("pawchive", term, index),
        "fanbox": lambda _term: _fanbox_candidates(found, transport),
        "rule34video": lambda term: _rule34video_candidates(term, transport),
        "rule34xxx": lambda term: _rule34xxx_candidates(term, transport,
                                                        credentials.load("rule34xxx")),
        "simpcity": lambda term: _simpcity_candidates(term, transport,
                                                      credentials.load("simpcity")),
    }
    for position, name in enumerate(tasks):
        if on_progress:
            on_progress(name, position, len(tasks))
        run(name, runners[name])
    if "f95zone" in tasks and not any(row.provider == "f95zone" for row in found):
        external_searches.append(_f95_external_search(text))
    return Discovery(text, tuple(found), failures, tuple(external_searches))


#: 一组建议最多给多少条。下拉是给人扫一眼的，不是把命中全倒出来。
MAX_SUGGESTIONS = 8
#: 短于这个长度不给建议。一个字母在十万条清单里命中几千个名字，排在最前的那几条
#: 和用户要找的人没有关系，白占一屏还白打一次站点。
MIN_SUGGEST_LENGTH = 2
#: 本机清单按这个顺序问。kemono 收的创作者最全，排在前面的先占名额。
SUGGEST_ARCHIVES = ("kemono", "pawchive", "coomer")
#: 同时问几个标签的分类。每条 0.32 秒，一组八条串行就是 2.6 秒——下拉等不起；
#: 2026-09-12 实测并发八路后十条 0.70 秒。站方额度是每 60 秒 60 次，一次敲字用掉
#: 八次，配上分类缓存后连着敲同一个词的后几下基本不再花钱。
TAG_TYPE_WORKERS = 8
#: 标签分类的进程内缓存。分类是站上改一次就定下来的事实，一天问一次都嫌多；
#: 而敲字时前缀越敲越长，后面几下要问的名字前一下刚问过，缓存省掉的就是这些。
#: 问不出来的也记（空串），免得每敲一下都为同一个名字再赌一次网络。
_TAG_TYPES: dict[str, str] = {}
#: 分类给人看的说法。站方的 `general` 落回「标签」——那一档就是「不是人」。
TAG_TYPE_LABELS = {"artist": "作者", "character": "角色", "copyright": "作品",
                   "metadata": "元数据", "general": "标签"}


def no_backoff(_seconds: float) -> None:
    """交互路径的退避：不等。

    连接器对 GET 失败按 0、1、2、4、8 秒退避，累计 15 秒，那是抓取任务的节奏。输入框
    联想和圆标补全是敲一下字就问一次的路径，站点一挂让人等 15 秒才回「没有」，不如
    立刻回空——建议本来就是锦上添花。
    """


@dataclass(frozen=True)
class Suggestion:
    """一个可以直接拿去查找的名字。"""

    provider: str
    value: str
    #: 站上有多少件作品。清单里没有这个数就是 0，界面那一列留空。
    count: int = 0
    #: 这个写法是在哪儿见到的，界面照实显示，不替用户断言就是他。
    matched: str = ""
    #: 站上把它归成哪一类（`artist`、`character`…）。空串是「没问出来」，
    #: 和「问出来是普通标签」不是一回事：前者不能拿去分组。
    tag_type: str = ""


def suggest_term(term: str) -> str:
    """能拿去要建议的那一段输入，不合格就是空串。

    限制和 `discover` 同一套（长度、禁用字符），但这里不抛错：敲字的中途本来就要
    经过各种不完整的形态，每按一个键弹一次报错不是建议该干的事。粘进来的链接也
    正好落在这一关外——地址里必有 `/`，那时该走的是解析链接，不是猜名字。
    """
    text = str(term or "").strip()
    if (len(text) < MIN_SUGGEST_LENGTH or len(text) > MAX_TERM_LENGTH
            or _TERM_FORBIDDEN_RE.search(text)):
        return ""
    return text


def archive_suggestions(prefix: str, *, state_root: Path,
                        providers: tuple[str, ...] = SUGGEST_ARCHIVES,
                        limit: int = MAX_SUGGESTIONS) -> tuple[Suggestion, ...]:
    """本机已有的整站创作者清单里，以这一段开头的名字。

    只按**前缀**取：清单是按 casefold 排好的，前缀是其中一段连续区间，二分一次就
    到手。子串匹配要全扫，十万条乘三个站每敲一下扫一遍，跟不上手速。

    同一个人常常三个站都有（`lewdgazer` 在 kemono 与 pawchive 各一份），按 casefold
    去重，留先问到的那个站的拼写。
    """
    folded = str(prefix or "").strip().casefold()
    if len(folded) < MIN_SUGGEST_LENGTH:
        return ()
    index = CreatorIndex(state_root)
    picked: dict[str, Suggestion] = {}
    for provider in providers:
        table = index.names(provider)
        start = bisect.bisect_left(table, (folded,))
        for name_key, name in table[start:]:
            if not name_key.startswith(folded):
                break
            if name_key in picked:
                continue
            picked[name_key] = Suggestion(provider, name, 0, provider)
            # 一个站自己就能把名额占满时也要停下：整段区间可能有上千条。
            if len(picked) >= limit * len(providers):
                break
    ordered = sorted(picked.values(),
                     key=lambda row: (row.value.casefold() != folded,
                                      len(row.value), row.value.casefold()))
    return tuple(ordered[:limit])


def tag_suggestions(prefix: str, *, transport=None,
                    credential: Credential | None = None,
                    limit: int = MAX_SUGGESTIONS) -> tuple[Suggestion, ...]:
    """rule34.xxx 站上以这一段开头的标签，带作品数和分类。

    站方的补全是公开的、不要凭据，但它只回名字和帖子数，认不出哪个是作者：实测
    `lewd`（8548 帖）是普通标签、`lewd_dorky` 是角色、`lewdrex` 才是作者。分类只有
    dapi 的 tag 接口给，而那个要凭据，所以这里分两档——有凭据就逐条问出分类，
    没凭据就只报名字。**认不出来时不按词形猜**：`lewdchuu_(artist)` 名字里带
    `artist` 是巧合，`lewdtuber` 实测是 metadata。

    站点挂了、超时、改版都只是没有这一组，别的组照常出——建议本来就是锦上添花。
    分类那一轮更是如此：问不出来就是没有分类，名字照给。
    """
    if len(str(prefix or "").strip()) < MIN_SUGGEST_LENGTH:
        return ()
    try:
        connector = Rule34XxxConnector(transport=transport, credential=credential,
                                       max_items=1, sleeper=no_backoff)
        rows = connector.autocomplete(prefix)
    except (FollowSourceError, CredentialError, OSError):
        return ()
    types = _tag_types(connector, [tag for tag, _count in rows[:limit]])
    return tuple(Suggestion("rule34xxx", tag, count,
                            TAG_TYPE_LABELS.get(types.get(tag, ""), ""),
                            types.get(tag, ""))
                 for tag, count in rows[:limit])


def _tag_types(connector, names: list[str]) -> dict[str, str]:
    """这一批标签各自的分类，缓存里有的不再问站点。

    并发问：串行是每条 0.32 秒累加，八条就把下拉拖到两秒半以上。一条问不出来
    只影响它自己那一条，整批不受牵连。
    """
    if connector.credential is None:
        return {}
    pending = [name for name in names if name not in _TAG_TYPES]

    def ask(name: str) -> None:
        try:
            _TAG_TYPES[name] = connector.tag_type(name)
        except (FollowSourceError, CredentialError, OSError):
            # 只有这一条没有分类，缓存不记——下次还值得再问一次。
            pass

    if pending:
        with ThreadPoolExecutor(max_workers=min(TAG_TYPE_WORKERS,
                                                len(pending))) as pool:
            list(pool.map(ask, pending))
    return {name: _TAG_TYPES[name] for name in names if name in _TAG_TYPES}
