r"""站点自己挂在页面上的标识资产：header 里那张 `<img>`，以及社媒头像。

`site_icons` 找的是站点**声明**给浏览器的那一枚（`<link rel=icon>`、manifest、
`/favicon.ico`）。那是给标签页用的，很多站至今只放一张 16×16 的 `favicon.ico`——
bambi.ne.jp 就是（2026-09-05 实测），按声明走只能得出「只有小图标」。

可同一张首页上明明挂着 `images/_/header_logo.png`，187×57，正是这家公司的字标；
再往下还有它自己的 X 账号，头像 119×119，是一枚方标。两者都比那枚 favicon 强，
只是没有任何 `rel` 属性说「这是我的图标」。这个模块就负责把它们找出来。

**怎么认 header 里那张 logo。** 只认写明了的：`src`、`alt`、`class`、`id` 里出现
`logo` 或 `brand`。不按位置猜「首页第一张图」——那多半是首屏大图或轮播，取回来是
一张模特照，和公司标识没有关系。

**og:image 不进这里。** 那一位通常是分享卡片用的横幅或首屏剧照（bambi.ne.jp 的
`images/1/image.jpg` 连图都解不开），命中率低、错得响。要用得由人指定。

**自动发现的社媒头像只认 X。** Instagram 登出页不给头像：`og:image` 是空的，HTML 里
也没有 `profile_pic_url`（2026-09-05 对 `bambi.hajimero` 实测）；登录态才有，而那要
浏览器里的会话，脚本拿不到，所以 Instagram 的头像地址由人解出来写进
`studio_icons.named_avatars` 读的那份文件。TikTok 的 oembed 头像只有百来像素。
X 登出页的 `og:image` 指向 `pbs.twimg.com/profile_images/...`，按
`social_links.twimg_tiers` 退档就能拿到上传原图，这条路 `harvest_social_avatars`
已经走通并用在女优头像上，这里复用同一条。

**账号从站点自己那一页找。** 账本里事务所只有 official 一种链接，一条社媒都没登记
（2026-09-05 实测 15 家全是 official），按账本找账号一个都找不到。而首页页脚就挂着
它们：bambi.ne.jp 上是 `x.com/BambiPromotion`。所以社媒账号也从同一份首页 HTML 里读，
和 header 那张 `<img>` 共用一次取数。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from .social_links import twimg_tiers

_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z][\w:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
#: 说明这张 `<img>` 是标识的词。`brand` 和 `logotype` 是同一件事的别的写法。
_MARK_WORD = re.compile(r"logo|logotype|brand", re.I)
#: 排除按钮和社媒小图标：`icon/x.png`、`icon/instagram.png` 这些路径里也常带 logo。
_NOT_MARK = re.compile(r"/icon/|sns|share|footer_bnr|banner", re.I)

_OG_IMAGE = re.compile(
    r"""<meta\b[^>]*property=["']og:image["'][^>]*content=["']([^"']+)["']""", re.I)

X_HOSTS = ("x.com", "twitter.com", "mobile.twitter.com")
_A_TAG = re.compile(r"<a\b[^>]*>", re.I)
#: X 自己的功能页，不是谁的账号。
_X_RESERVED = frozenset((
    "home", "explore", "search", "share", "intent", "i", "compose", "messages",
    "notifications", "settings", "login", "logout", "signup", "privacy", "tos",
    "about", "download", "hashtag"))


def _attrs(tag: str) -> dict[str, str]:
    return {name.lower(): (double or single or bare)
            for name, double, single, bare in _ATTR.findall(tag)}


def logo_images(html: str, base_url: str) -> list[str]:
    """首页 HTML → 站点自己那几张标识图的绝对地址，按出现顺序去重。

    出现顺序就是优先级：header 在页面最上面，站点自己把最正式的那张放在那里。
    """
    found: list[str] = []
    seen: set[str] = set()
    for tag in _IMG_TAG.findall(html or ""):
        attributes = _attrs(tag)
        src = (attributes.get("src") or attributes.get("data-src") or "").strip()
        if not src:
            continue
        haystack = " ".join(filter(None, (
            src, attributes.get("alt", ""), attributes.get("class", ""),
            attributes.get("id", ""))))
        if not _MARK_WORD.search(haystack) or _NOT_MARK.search(src):
            continue
        url = urljoin(base_url, src)
        if not url.startswith(("http://", "https://")):
            # 懒加载占位图是内联的 `data:` 1×1 透明 PNG，而它顶着的正是标识那张
            # `<img>` 的 class，词形闸门拦不住。取字节那一步 httpx 打不开 `data:`，
            # 白等两轮重试再记一次「取不回来」——eltra.jp 一页就有两张。
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def is_x_profile(url: str) -> bool:
    """这条链接是不是一个 X 账号主页。

    分享按钮和站内功能页也挂在同一批主机上（`intent/tweet`、`share`、`hashtag/...`），
    它们的 `og:image` 是 X 自己的卡片图，取回来每家都一样。只认单段路径的账号页。
    """
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    if host not in X_HOSTS:
        return False
    segments = [segment for segment in parts.path.split("/") if segment]
    return len(segments) == 1 and segments[0].lower() not in _X_RESERVED


def x_profiles(html: str, base_url: str) -> list[str]:
    """首页 HTML → 页面上挂着的 X 账号主页，按出现顺序去重。

    顺序即优先级：公司自己的账号一般排在艺人账号和转发账号前面。
    """
    found: list[str] = []
    seen: set[str] = set()
    for tag in _A_TAG.findall(html or ""):
        href = (_attrs(tag).get("href") or "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        if not is_x_profile(url):
            continue
        parts = urlsplit(url)
        canonical = f"https://x.com/{parts.path.strip('/')}"
        if canonical not in seen:
            seen.add(canonical)
            found.append(canonical)
    return found


def avatar_tiers(html: str) -> list[str]:
    """X 登出页 HTML → 头像候选，从上传原图退到缩略图。

    取不到就是空列表：调用方据此写「未取得」，不要拿页面上别的图顶替。
    """
    match = _OG_IMAGE.search(html or "")
    if not match:
        return []
    url = match.group(1).strip()
    if "pbs.twimg.com/profile_images/" not in url:
        return []
    return twimg_tiers(url)
