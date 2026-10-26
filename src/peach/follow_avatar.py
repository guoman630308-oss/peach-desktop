"""Resolve creator avatars from verified official profile endpoints."""
from __future__ import annotations
from .user_agent import USER_AGENT

import json
import re
import urllib.parse
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .follow import FollowSourceError
from .http import HttpRequest, HttpTransport, HttpxTransport
from .social_links import meta_content, twimg_tiers


MAX_PROFILE_BYTES = 1024 * 1024
_USER_ID_RE = re.compile(r"^\d{1,20}$")
_CREATOR_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
#: 名片链接里换得到头像的社交资料：服务名 → 手柄的合法形状。
_PROFILE_HANDLE_RES = {
    "twitter": re.compile(r"[A-Za-z0-9_]{1,15}"),
    "patreon": re.compile(r"[A-Za-z0-9_.-]{1,64}"),
    "pixiv": _USER_ID_RE,
}
_PATREON_AVATAR_HOST_RE = re.compile(r"c\d+\.patreonusercontent\.com")
#: 一个头像请求最多带几份社交资料。名片上同一个人通常一两个账号。
MAX_PROFILE_IDENTITIES = 4


def profile_identities(value: str) -> tuple[tuple[str, str], ...]:
    """`twitter:Rekin3D,patreon:sharkarts` → 校验过的 `(服务, 手柄)`。

    这串从页面递回来，有一段不合法就整串作废，不挑着用其中合法的几段。
    """
    pairs: list[tuple[str, str]] = []
    for part in str(value or "").split(","):
        service, _, handle = part.partition(":")
        pattern = _PROFILE_HANDLE_RES.get(service)
        if pattern is None or not pattern.fullmatch(handle):
            return ()
        pairs.append((service, handle))
    unique = tuple(dict.fromkeys(pairs))
    return unique if len(unique) <= MAX_PROFILE_IDENTITIES else ()


def profile_avatar_tiers(service: str, handle: str, *,
                         transport: HttpTransport | None = None) -> list[str]:
    """一份社交资料的头像地址，从原图到小图排好。取不到抛 `FollowSourceError`。

    几家都不带凭据：X 的登出页用 og:image 声明本人头像（`_200x200` 那档），
    `social_links.twimg_tiers` 把它展开成原图、400、200 三档；Patreon 的公开
    campaigns 接口按 vanity 查，`avatar_photo_image_urls` 里 `original` 是上传原图；
    pixiv 数字 id 走 `resolve_official_profile` 换 FANBOX 头像。有 pixiv 不等于开了
    FANBOX（flim13 实测换不到），所以它和别家并排试，不单独占住这一格。
    主机写死在这里，返回的地址不会指向别处。
    """
    pattern = _PROFILE_HANDLE_RES.get(service)
    if pattern is None or not pattern.fullmatch(str(handle or "")):
        raise FollowSourceError("不支持这个社交资料")
    request = transport or HttpxTransport()
    if service == "twitter":
        return _x_avatar_tiers(handle, request)
    if service == "pixiv":
        return [resolve_official_avatar("fanbox", handle, transport=request)]
    return _patreon_avatar_tiers(handle, request)


def _x_avatar_tiers(handle: str, request: HttpTransport) -> list[str]:
    page = request(
        HttpRequest("GET", f"https://x.com/{handle}", {
            "Accept": "text/html", "User-Agent": USER_AGENT,
        }),
        15.0,
        MAX_PROFILE_BYTES,
    )
    if page.status != 200:
        raise FollowSourceError(f"X 资料页返回 HTTP {page.status}")
    image = meta_content(page.body.decode("utf-8", "replace"), "og:image")
    if not image.startswith("https://pbs.twimg.com/profile_images/"):
        raise FollowSourceError("X 资料页没有本人头像")
    return twimg_tiers(image)


def _patreon_avatar_tiers(handle: str, request: HttpTransport) -> list[str]:
    url = "https://www.patreon.com/api/campaigns?" + urllib.parse.urlencode({
        "filter[vanity]": handle, "fields[campaign]": "avatar_photo_image_urls,vanity",
    })
    response = request(
        HttpRequest("GET", url, {"Accept": "application/json", "User-Agent": USER_AGENT}),
        15.0,
        MAX_PROFILE_BYTES,
    )
    if response.status != 200:
        raise FollowSourceError(f"Patreon 资料返回 HTTP {response.status}")
    try:
        campaigns = json.loads(response.body.decode("utf-8")).get("data") or []
        urls = next(
            campaign["attributes"].get("avatar_photo_image_urls") or {}
            for campaign in campaigns
            if str(campaign["attributes"].get("vanity") or "").casefold() == handle.casefold()
        )
        tiers = [str(urls.get(size) or "") for size in ("original", "default_large", "default")]
    except (StopIteration, AttributeError, KeyError, TypeError, ValueError) as error:
        raise FollowSourceError("Patreon 资料里没有这个创作者") from error
    trusted = [tier for tier in tiers
               if (parsed := urllib.parse.urlsplit(tier)).scheme == "https"
               and _PATREON_AVATAR_HOST_RE.fullmatch(parsed.hostname or "")]
    if not trusted:
        raise FollowSourceError("Patreon 资料没有可信的头像地址")
    return trusted


@dataclass(frozen=True)
class OfficialProfile:
    user_id: str
    creator_id: str
    name: str
    url: str
    avatar_url: str


def resolve_official_profile(service: str, user_id: str, *,
                             transport: HttpTransport | None = None) -> OfficialProfile:
    """Return the verified official profile behind one archive identity.

    ``user_id`` accepts either identity FANBOX publishes.  Archive sources expose the
    Pixiv numeric user id, and the official creator page maps it to the public creator
    id.  A forum profile link only carries that creator id (``jul3dnsfw.fanbox.cc``),
    which ``creator.get`` already accepts, so that shape skips the lookup page.  Either
    way ``creator.get`` returns the current name and ``user.iconUrl``.  Hosts and the
    returned identity are fixed here so a client cannot turn discovery or the avatar
    endpoint into an SSRF/open redirect.
    """
    identity = str(user_id or "")
    if service != "fanbox":
        raise FollowSourceError("不支持这个官方头像来源")
    request = transport or HttpxTransport()
    if _USER_ID_RE.fullmatch(identity):
        expected_user_id = identity
        creator_id = _creator_id_for_user(identity, request)
    elif _CREATOR_ID_RE.fullmatch(identity):
        # 创作者 id 这条路没有可核对的数字 id，身份就以 `creator.get` 回的为准。
        expected_user_id = ""
        creator_id = identity
    else:
        raise FollowSourceError("不支持这个官方头像来源")

    creator_origin = f"https://{creator_id}.fanbox.cc"
    api_url = "https://api.fanbox.cc/creator.get?" + urllib.parse.urlencode(
        {"creatorId": creator_id}
    )
    response = request(
        HttpRequest("GET", api_url, {
            "Accept": "application/json", "Origin": creator_origin,
            "Referer": creator_origin + "/", "User-Agent": USER_AGENT,
        }),
        15.0,
        MAX_PROFILE_BYTES,
    )
    if response.status != 200:
        raise FollowSourceError(f"FANBOX 官方资料返回 HTTP {response.status}")
    try:
        body = (json.loads(response.body.decode("utf-8")) or {}).get("body") or {}
        user = body.get("user") or {}
        avatar = str(user.get("iconUrl") or "")
        returned_user_id = str(user.get("userId") or "")
        name = str(user.get("name") or creator_id).strip()
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise FollowSourceError("FANBOX 官方资料格式不符") from error
    parsed = urllib.parse.urlsplit(avatar)
    if ((expected_user_id and returned_user_id != expected_user_id)
            or not _USER_ID_RE.fullmatch(returned_user_id)
            or parsed.scheme != "https"
            or parsed.hostname != "pixiv.pximg.net"):
        raise FollowSourceError("FANBOX 官方资料没有可信的头像地址")
    return OfficialProfile(
        user_id=returned_user_id, creator_id=creator_id, name=name,
        url=creator_origin + "/", avatar_url=avatar,
    )


def _creator_id_for_user(user_id: str, request: HttpTransport) -> str:
    """Map a Pixiv numeric user id to the public FANBOX creator id."""
    profile_url = f"https://www.pixiv.net/fanbox/creator/{user_id}"
    page = request(
        HttpRequest("GET", profile_url, {
            "Accept": "text/html", "User-Agent": USER_AGENT,
        }),
        15.0,
        MAX_PROFILE_BYTES,
    )
    if page.status != 200:
        raise FollowSourceError(f"FANBOX 官方页面返回 HTTP {page.status}")
    try:
        soup = BeautifulSoup(page.body, "html.parser")
        metadata_node = soup.find("meta", attrs={"name": "metadata"})
        metadata = json.loads(str(metadata_node.get("content"))) if metadata_node else {}
        creator_id = str(
            (((metadata.get("urlContext") or {}).get("host") or {}).get("creatorId"))
            or ""
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise FollowSourceError("FANBOX 官方页面没有可用的创作者资料") from error
    if not _CREATOR_ID_RE.fullmatch(creator_id):
        raise FollowSourceError("FANBOX 官方页面没有可用的创作者 id")
    return creator_id


def resolve_official_avatar(service: str, user_id: str, *,
                            transport: HttpTransport | None = None) -> str:
    """Return a fixed-host official avatar URL for one supported creator service."""
    return resolve_official_profile(
        service, user_id, transport=transport,
    ).avatar_url
