"""社媒链接的共用判据：哪些主机算社交平台、链接文字怎么写、两条链接是不是同一个账号、
X 登出页能不能看出账号还活着、哪些 performer 值得去外站查。

`harvest_performer_links`、`normalize_link_hosts` 与目录型采集（laoshi.ink、事务所官网）
都从这里取，平台名单只有这一份，改一处处处生效。这里只放判据，不放任何来源的解析——
来源怎么翻页、怎么过年龄门，留在各自的脚本里。
"""
from __future__ import annotations

import html
import re
import sqlite3
import unicodedata
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

from .catalog_rules import is_jav_code
from .entities import name_chain

#: Facebook 和 Linktree 也在这里：它们此前不在表里，于是走了 official 分支，被贴上
#: 事务所名——古川伊织的个人 Facebook 页在资料页上写着「T-POWERS」。本人账号就该按
#: 本人账号显示，事务所名是另一件事。
SOCIAL_HOSTS = ("x.com", "twitter.com", "instagram.com", "tiktok.com", "youtube.com",
                "facebook.com", "linktr.ee")
#: 博客也是本人的社交存在，但它没有 handle 概念，标签另算。
#: `livedoor.biz` 与 `livedoor.jp` 是同一家的两个域，`plaza.rakuten.co.jp` 和
#: `blog.dmm.co.jp` 同样是博客托管——它们不在表里时都走了 official 分支，被贴上事务所名。
BLOG_HOSTS = ("ameblo.jp", "lineblog.me", "note.com", "livedoor.jp", "livedoor.biz",
              "hatenablog.com", "plaza.rakuten.co.jp", "blog.dmm.co.jp")
#: 网页存档。关门的公司（ONE'S DOUBLE、AINA）官网只剩快照，那一条照样记成 official，
#: 可它的主机是存档站自己：拿它认域名归属，存档站就成了最后写入那家公司的站；拿它
#: 取标识，每一家都会顶着 Internet Archive 的图标。认归属、取标识的地方都先问这里。
ARCHIVE_HOSTS = ("web.archive.org",)
PLATFORM_NAMES = {("x.com", "twitter.com"): "X", ("instagram.com",): "Instagram",
                  ("tiktok.com",): "TikTok", ("youtube.com",): "YouTube",
                  ("facebook.com",): "Facebook", ("linktr.ee",): "Linktree"}
#: 这些首段不是 handle，是平台的路径前缀：`youtube.com/channel/UC…`、`youtube.com/c/名字`。
PATH_PREFIXES = frozenset({"channel", "c", "user"})
#: 这些首段是平台功能页，不是任何人的账号：`x.com/intent/follow?…`、`instagram.com/p/…`。
NON_HANDLES = frozenset({
    "intent", "search", "hashtag", "i", "home", "share", "explore", "p", "reel", "reels",
    "stories", "watch", "shorts", "playlist", "results", "embed", "tag", "tags", "login",
    "signup", "accounts", "privacy", "tos", "about", "settings",
})
#: 旧主机 → 现主机。只放**同一家站点改名**的情况，不是通用的域名清洗表。
#:
#: twitter.com 与 x.com 是同一个站在 2023 年的改名，路径原样可用（实测 status 永久链接
#: 也照样解析）。别把「看起来相似的站」放进来：那是两个站，不是一个站的两种写法。
HOST_ALIASES: dict[str, str] = {
    "twitter.com": "x.com",
    "www.twitter.com": "x.com",
    "mobile.twitter.com": "x.com",
}
#: X 登出页在账号封停或不存在时正文里会出现的字样（英文、日文两种界面）。
X_GONE_MARKERS = ("Account suspended", "アカウントは凍結", "This account doesn’t exist",
                  "This account doesn't exist", "このアカウントは存在しません")
META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')


def under(host: str, domains: tuple[str, ...]) -> bool:
    """host 是否就是这些域名之一或它们的子域。

    直接用 `endswith` 会把 `notx.com` 判成 x.com——后缀匹配必须落在点边界上，
    否则任何人注册一个以平台名结尾的域名就能让链接被标成官方社交账号。
    """
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def is_archive(url: str) -> bool:
    """这条链接是不是指向网页存档里的快照。"""
    return under(host_of(url), ARCHIVE_HOSTS)


#: 存档快照地址里的时间戳与原地址：`/web/20220512171503/https://ones-double.com/…`。
ARCHIVE_PATH = re.compile(r"^/web/(\d{4,14})[a-z_]*/(https?://.+)$", re.I)


def archive_view(url: str) -> tuple[str, Callable[[str], str]]:
    """按哪个地址找站点图标，以及每个要取的地址怎么改写。

    存档快照的主机是存档站自己，照它找图标，每一家关了门的公司都会顶着 Internet Archive
    的图标。所以按快照里的原站去找，要取的每个地址都换成同一时刻的存档原件（`id_`
    返回的是原始字节，不带存档站插的工具栏）。不是存档快照的原样取。
    """
    parts = urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    found = ARCHIVE_PATH.match(path) if is_archive(url) else None
    if found is None:
        return url, lambda target: target
    stamp = found.group(1)
    return found.group(2), lambda target: (
        target if is_archive(target) else f"https://web.archive.org/web/{stamp}id_/{target}")


def canonical_url(url: str) -> str:
    """改过名的站点换成现主机，主机名一律小写；其余原样返回。

    主机名本来就大小写不敏感，而 jae 的资料页上写着 `https://Instagram.com/…`：照抄进去
    就是同一个账号的第二条记录，`entity_link` 的 UNIQUE 只认字面。

    只换 netloc 并把 scheme 提到 https，路径、查询和片段原样保留——handle 大小写在 X 上
    不敏感，但它是用户当初复核过的写法，没有理由在这里替他改掉。
    """
    parts = urlsplit(url.strip())
    new_host = HOST_ALIASES.get((parts.hostname or "").lower())
    if new_host:
        return urlunsplit(("https", new_host, parts.path, parts.query, parts.fragment))
    if parts.netloc == parts.netloc.lower():
        return url
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query,
                       parts.fragment))


def platform(url: str) -> str:
    """平台名（`X`、`Instagram`…）；不是社交平台就返回空串。"""
    host = host_of(url)
    if not under(host, SOCIAL_HOSTS):
        return ""
    return next((name for hosts, name in PLATFORM_NAMES.items() if under(host, hosts)), host)


def account_segment(url: str) -> str:
    """路径里代表账号的那一段，保留原大小写；功能页返回空串。

    多数平台是第一段；YouTube 的 `/channel/UC…` 和 `/c/名字` 第一段只是前缀，取第二段。
    `@setokan.channel` 的 `@` 是 YouTube 的写法不是账号名的一部分，去掉。
    """
    parts = [part for part in urlsplit(url).path.split("/") if part]
    if not parts:
        return ""
    first = parts[0].casefold()
    if first in NON_HANDLES:
        return ""
    if first in PATH_PREFIXES:
        return parts[1].lstrip("@") if len(parts) > 1 else ""
    return parts[0].lstrip("@")


def handle(url: str) -> str:
    """比较两条链接是不是同一个账号用的键。

    统一 casefold——X 的 handle 不分大小写，`@Yua_Mikami` 和 `@yua_mikami` 是同一个人，
    不能算成冲突。
    """
    return account_segment(url).casefold()


def classify(url: str, agency: str = "",
             owner_of: Callable[[str], str] | None = None) -> tuple[str, str]:
    """(link_kind, label)。label 就是资料页上那行链接文字，所以要说清点过去是什么。

    **标签跟着域名走，不跟着事务所字段走。** minnano-av 的资料表里「所属事務所」和
    「公式サイト」是两行不同的事实：白石亚子的事务所是 T-POWERS，公式サイト填的却是
    Prestige 的専属宣传页。把事务所名贴到这条链接上，一个控件就同时替两家公司说话——
    图标按域名取，于是页面上写着 T-POWERS、图标和落点都是 Prestige。

    `owner_of(hostname)` 给出账本已知的域名归属（厂牌官网、事务所自己的站）。知道归谁
    就用谁的名字；不知道才退回事务所名，那时它至少没有和已知事实矛盾。两样都没有就写
    「官方网站」——这个词不承诺点过去会见到谁，是唯一诚实的兜底。
    """
    name = platform(url)
    if name:
        shown = account_segment(url)
        # `youtube.com/channel/UC…` 的那串是频道 ID 不是名字，写进标签只是一行乱码。
        if name == "YouTube" and re.fullmatch(r"UC[\w-]{22}", shown):
            shown = ""
        return "social", (f"{name} @{shown}" if shown else name)
    host = host_of(url)
    if under(host, BLOG_HOSTS):
        return "social", "博客"
    # `owner_of` 常常就是某个 dict 的 `.get`，未命中给的是 None 不是空串。
    owner = str(owner_of(host) or "" if owner_of else "").strip()
    return "official", (owner or agency.strip() or "官方网站")


#: 一个域名要被多少条链接指认成同一家，才算「账本知道它归谁」。
#: 1 会让香西咲个人站 `saki-k.com` 上那一条孤证自我确认成 T-POWERS 的站。
OWNER_QUORUM = 3


def owner_key(hostname: str) -> str:
    """归属表的键。

    同一家站在账本里两种写法都有（`bambi.ne.jp` 与 `www.bambi.ne.jp`），落成哪一种是
    采集时抓到什么决定的。键和 `host_of` 用同一条归一规则，写入和查询才对得上——
    两边各归一各的，`www.` 那一半就永远查不到归属、白白退回事务所名。
    """
    return (hostname or "").casefold().removeprefix("www.")


def host_owners(connection: sqlite3.Connection) -> dict[str, str]:
    """域名 → 账本知道的归属方名字。

    两种证据，厂牌优先：厂牌实体自己挂的官网链接直接指认（`www.prestige-av.com` 是
    Prestige 的），这是一手的。其次是女优 official 链接里已经形成共识的那些——
    `www.t-powers.co.jp` 上 37 条都写着 T-POWERS，那它就是 T-POWERS 的站。

    共识要够多条才作数。孤证会自我确认：香西咲的个人站只出现过一次、恰好被贴了
    T-POWERS，采信它等于把错误当成证据再用一遍。
    """
    owners: dict[str, str] = {}
    votes: dict[str, dict[str, int]] = {}
    for host, label in connection.execute(
        "SELECT hostname,label FROM entity_link l JOIN entity e ON e.id=l.entity_id"
        " WHERE e.kind='performer' AND l.link_kind='official' AND l.hostname<>''"
    ):
        host = owner_key(host)
        if under(host, ARCHIVE_HOSTS):
            continue
        votes.setdefault(host, {})[label] = votes.setdefault(host, {}).get(label, 0) + 1
    for host, tally in votes.items():
        label, count = max(tally.items(), key=lambda item: item[1])
        if count >= OWNER_QUORUM and label != "官方网站":
            owners[host] = label
    # 厂牌那一手证据后写，压过女优链接里的共识。事务所的官网链接不算在内：
    # 那批链接是 `install_agencies.py` 反过来照这个函数的结论装的，再收回来当
    # 证据就是拿自己的输出确认自己。
    for host, name in connection.execute(
        "SELECT hostname,canonical_name FROM entity_link l JOIN entity e ON e.id=l.entity_id"
        " WHERE e.kind='studio' AND l.link_kind='official' AND l.hostname<>''"
    ):
        if not under(owner_key(host), ARCHIVE_HOSTS):
            owners[owner_key(host)] = name
    return owners


def name_key(name: str) -> str:
    """名字比对用的键：全角半角归一、去空白、不分大小写。

    目录站写 `三上悠亜`，账本别名也是 `三上悠亜`，但中间可能多一个全角空格或半角空格；
    罗马字则常见 `Yua Mikami` 与 `YUA MIKAMI` 两种写法。键只用来找候选，最终装入的
    名字仍是账本里的那一个。
    """
    folded = unicodedata.normalize("NFKC", str(name or "")).casefold()
    return re.sub(r"\s+", "", folded)


def meta_content(body: str, prop: str) -> str:
    """`<meta property="og:…" content="…">` 的 content，属性顺序不限。"""
    for tag in META_TAG.findall(body):
        attrs = {key.casefold(): value for key, value in ATTR.findall(tag)}
        if attrs.get("property") == prop or attrs.get("name") == prop:
            return html.unescape(attrs.get("content", ""))
    return ""


#: pbs.twimg.com 头像文件名里的档位后缀。
TWIMG_TIER = re.compile(r"_(?:normal|bigger|mini|x96|\d+x\d+)$")
TWIMG_TIERS = ("", "_400x400", "_200x200")


def twimg_tiers(url: str) -> list[str]:
    """一个 pbs.twimg.com 头像地址 → 从原图到缩略图的候选档位，按优先级排列。

    无后缀的那一份是上传原图，也就是最大档，但 X 不保证它一直在（旧头像有过只剩
    缩略图的），所以按档位往下退。`_400x400` 不能当成「有这么大」的证据：原图比它
    小的时候，这个地址返回的仍是原图——`セレブの友` 就是 242×242。

    非 twimg 地址原样返回单元素列表，调用方不必分情况处理。
    """
    if "pbs.twimg.com/profile_images/" not in url:
        return [url]
    stem, _, extension = url.rpartition(".")
    if not stem or "/" in extension:
        return [url]
    stem = TWIMG_TIER.sub("", stem)
    return [f"{stem}{tier}.{extension}" for tier in TWIMG_TIERS]


def x_profile_state(body: str) -> tuple[str, str]:
    """X 登出页 HTML → (状态, 显示名)。状态取 alive / gone / unknown 三值。

    用户 2026-09-02 指出：目录站抄来的 X 账号很多已经封停，本人早换了新号。登出页
    对活着的账号会给 og:title（显示名）和指向 profile_images 的 og:image；封停或不存在
    的账号两者都没有，正文有时还直接写着「Account suspended」。什么 og 都没有的页面
    是 JS 壳或限流页，看不出死活，只能 unknown，不能把限流当成封停。
    """
    if any(marker in body for marker in X_GONE_MARKERS):
        return "gone", ""
    title = meta_content(body, "og:title")
    image = meta_content(body, "og:image")
    if title and "profile_images" in image:
        return "alive", title.strip()
    if not title and not image:
        return "unknown", ""
    return "gone", title.strip()


def load_performers(connection: sqlite3.Connection, minimum: int) -> list[dict]:
    """有作品的 performer，跳过确定不是 JAV 的。

    minnano-av 和 laoshi.ink 都是 JAV 资料库，拿中文素人创作者去查必然落空，还会把真正的
    缺口盖住——和 `audit_performer_portraits.py` 跳过非 JAV 是同一条理由，判据复用同一个
    函数。看不见作品是未知不是反面证据，照查；看得见但没有一个 JAV 番号才跳过。
    """
    codes: dict[int, list[str]] = {}
    counts: dict[int, int] = {}
    for entity_id, total, joined in connection.execute(
            "SELECT ae.entity_id, count(*), group_concat(DISTINCT a.code) "
            "FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id "
            "WHERE a.medium='video' GROUP BY ae.entity_id"):
        counts[entity_id] = int(total or 0)
        codes[entity_id] = [code for code in str(joined or "").split(",") if code]
    aliases: dict[int, list[str]] = {}
    for entity_id, alias in connection.execute(
            "SELECT entity_id, alias FROM entity_alias ORDER BY confidence DESC, alias"):
        aliases.setdefault(entity_id, []).append(alias)
    out = []
    for entity_id, name in connection.execute(
            "SELECT id, canonical_name FROM entity WHERE kind='performer'"):
        total = counts.get(entity_id, 0)
        if total < minimum:
            continue
        # 「作品可见却一个 JAV 番号都没有」是非 JAV 的反面证据（中文素人创作者正是这个
        # 形态）；「一个作品都看不见」只是未知，照查不误。按非空番号判会把前者也当成未知，
        # 查一遍落空后混进未取得，把真正查不到的 JAV 女优盖住。
        if total and not any(is_jav_code(code) for code in codes.get(entity_id, ())):
            continue
        out.append({"entity_id": entity_id, "name": name, "assets": total,
                    "chain": name_chain(name, aliases.get(entity_id, []))})
    return sorted(out, key=lambda record: -record["assets"])
