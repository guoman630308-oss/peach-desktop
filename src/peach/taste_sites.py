"""口味网址规则：专门来源、混合平台和实体账号各有明确边界。"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from urllib.parse import parse_qs, unquote, urlparse

ADULT_DOMAINS = {
    "dmm.co.jp", "fanza.co.jp", "fanza.com", "fanza.jp", "mgstage.com", "sod.co.jp", "sodprime.com",
    "prestige-av.com", "moodyz.com", "ideapocket.com", "s1s1s1.com", "maxing.jp",
    "madonna-av.com", "premium-beauty.com", "kawaiikawaii.jp", "av-wiki.net", "javlibrary.com",
    "javtrailers.com", "jav321.com", "jav.land", "r18.com", "tokyo-hot.com",
    "caribbeancom.com", "1pondo.tv", "10musume.com", "pacopacomama.com", "heyzo.com",
    "fc2ppv.com", "hsex.men", "manko.fun", "missav.ws", "missav.ai", "jav.guru",
    "javmost.com", "javhd.com", "thisav.com", "pornhub.org", "xvideos.red", "xnxx.com",
    "youporn.com", "tube8.com", "youjizz.com", "eporner.com", "hqporner.com",
    "beeg.com", "tnaflix.com", "pornone.com", "thumbzilla.com", "motherless.com",
    "erome.com", "avgle.com", "iwara.tv", "rule34.paheal.net", "sankakucomplex.com",
    "e621.net", "e-hentai.org", "exhentai.org", "hitomi.la", "nhentai.to",
    "hentaihaven.xxx", "hanime.tv", "hentainexus.com", "hentai2read.com",
    "myreadingmanga.info", "wnacg.com", "nhentai.xxx", "asmr.one", "hcomic.in",
    "coomer.su", "coomer.party", "kemono.party", "simpcity.su", "simpcity.cr",
    "pornpics.com", "sex.com", "literotica.com", "chaturbate.com", "stripchat.com",
    "bongacams.com", "myfreecams.com", "camsoda.com", "manyvids.com", "clips4sale.com",
    "loyalfans.com", "justfor.fans", "myfans.jp", "unfiltrd.com",
    "javtiful.com", "javmost.ws", "njav.tv", "njavtv.com", "porn-comic.com", "rule34.world",
    "rule34vault.com", "91porny.com", "91porna.com", "indexxx.com", "pornbox.com",
}
MIXED_DOMAINS = {"x.com", "twitter.com", "instagram.com", "youtube.com", "youtu.be",
                 "reddit.com", "tumblr.com", "bsky.app", "threads.net", "threads.com",
                 "pixiv.net", "patreon.com", "fanbox.cc", "fantia.jp", "dlsite.com", "dmm.com",
                 "deviantart.com", "subscribestar.com", "subscribestar.adult", "gumroad.com", "ko-fi.com"}
ACCOUNT_DOMAINS = MIXED_DOMAINS - {"dmm.com", "dlsite.com"} | {"onlyfans.com", "fansly.com", "manyvids.com", "loyalfans.com"}
ADULT_TITLE = re.compile(r"\b(?:nsfw|r[- ]?18|onlyfans|fansly|pornstar|porn|hentai|jav|javdb|javbus|fanza|rule34|erotica|adult creator)\b|18\+|成人向|成人創作者|成人创作者|AV女優", re.I)
ACCOUNT_RESERVED = {"home", "i", "intent", "search", "explore", "settings", "login", "accounts",
                    "share", "watch", "feed", "results", "shorts", "reel", "reels", "p", "notifications",
                    "artworks", "novel", "posts", "post", "about", "help", "signup", "stories", "hashtag", "messages"}


def _profile_url(parsed, domain, parts):
    """个人页的历史和现代网址统一到已有名片识别入口。"""
    path = parsed.path
    if domain == "pixiv.net" and parts[:1] == ["member.php"]:
        identity = parse_qs(parsed.query).get("id", [""])[0]
        path = f"/users/{identity}" if identity.isdigit() else "/"
    elif domain == "patreon.com" and parts[:1] == ["c"] and len(parts) > 1:
        path = "/" + parts[1]
    elif domain == "bsky.app" and parts[:1] == ["profile"] and len(parts) > 1:
        path = "/profile/" + parts[1]
    return parsed._replace(netloc=domain, path=path, query="", fragment="").geturl()


def host(url: str) -> str:
    try:
        domain = (urlparse(url).hostname or "").casefold().removeprefix("www.").removeprefix("mobile.")
    except ValueError:
        return ""
    return "x.com" if domain == "twitter.com" else domain


def account_key(url: str) -> tuple[str, str] | None:
    from .follow_sources import profile_link_identity
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    domain = host(url)
    if domain.endswith((".fanbox.cc", ".tumblr.com")):
        account, _, base = domain.partition(".")
        return base, account
    parts = [unquote(part).casefold() for part in parsed.path.split("/") if part]
    if not parts or parts[0] in ACCOUNT_RESERVED:
        return None
    recognised = profile_link_identity(_profile_url(parsed, domain, parts))
    if recognised:
        service, account = recognised
        canonical = {"twitter": "x.com", "fanbox": "fanbox.cc", "pixiv": "pixiv.net",
                     "fantia": "fantia.jp", "bsky": "bsky.app", "itchio": "itch.io"}.get(service, domain)
        return canonical, account.casefold()
    return _extra_account(domain, parts)


def _extra_account(domain, parts):
    """现有名片入口未覆盖的个人频道，不识别作品和平台功能页。"""
    if domain == "youtube.com":
        account = parts[1] if parts[0] in {"channel", "user", "c"} and len(parts) > 1 else parts[0] if parts[0].startswith("@") else ""
    elif domain == "reddit.com":
        account = parts[1] if parts[0] in {"user", "u"} and len(parts) > 1 else ""
    elif domain == "manyvids.com":
        account = parts[1] if parts[0] == "profile" and len(parts) > 1 else ""
    elif domain in {"onlyfans.com", "fansly.com", "loyalfans.com", "instagram.com", "threads.net", "threads.com"}:
        account = parts[0]
    else:
        return None
    if not re.fullmatch(r"@?[\w.\-]{2,100}", account):
        return None
    return domain, account.removeprefix("@")


def mixed_adult(url: str, title: str = "") -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    domain = host(url)
    path = unquote(parsed.path).casefold()
    if domain == "dmm.com":
        return "fanza" in path
    if domain == "dlsite.com":
        return path.startswith(("/maniax/", "/pro/", "/girls-pro/", "/bl-pro/"))
    return bool(ADULT_TITLE.search(title) or re.search(r"(?:^|[/=])r-?18(?:$|[/&])", unquote(url).casefold()))


def identity_key(url: str):
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    account = account_key(url)
    if account or host(url) in ACCOUNT_DOMAINS:
        return account
    return host(url), unquote(parsed.path).rstrip("/").casefold()


def linked_identities(url: str, accounts):
    matched = set(accounts.get(identity_key(url), ()))
    if host(url) not in MIXED_DOMAINS:
        matched.update(accounts.get((host(url), ""), ()))
    return matched


def identity_context(connection):
    """仅复用馆藏登记的身份链接与启用的关注，浏览证据不写回实体。"""
    accounts: dict[tuple[str, str], set[tuple[int, str, str]]] = defaultdict(set)
    followed: dict[str, Counter] = defaultdict(Counter)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "entity_link" in tables:
        for eid, kind, name, url in connection.execute(
            "SELECT e.id,e.kind,e.canonical_name,l.url FROM entity_link l JOIN entity e ON e.id=l.entity_id "
            "WHERE e.kind IN ('performer','creator','studio','series')"):
            key = identity_key(url)
            if key:
                accounts[key].add((eid, kind, name))
    if "follow_source" in tables:
        for eid, kind, name, url in connection.execute(
            "SELECT e.id,e.kind,e.canonical_name,s.url FROM follow_source s JOIN entity e ON e.id=s.entity_id "
            "WHERE s.enabled=1 AND e.kind IN ('performer','creator','studio','series')"):
            followed[kind][(eid, name)] = 1
            key = identity_key(url)
            if key:
                accounts[key].add((eid, kind, name))
    return accounts, followed
