#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""社媒存在感扩张：从已核实的 X 账号走到集链页，扩张全部社媒链接并取最高清头像。

规范化流程（2026-09-02 与用户约定）：社媒链接与头像是同一条链路的两个产出——

    entity_link 里的 X/Twitter（minnano-av 管线已装、人工复核过的）
      → X 登出页：og:image 给本人头像、og:title 给显示名（名字闸门的证据）、
        简介文本里的明文外链给集链页
      → 集链页（lit.link / linktr.ee / allmylinks / twpf.jp）：服务端直出的
        全部外链——Instagram、TikTok、Threads、Fantia、DMM/MGStage 检索……
      → 各社媒头像同图去重，取最大的最清晰的一张。

产出三份东西：
  1) 链接安装队列 CSV（喂 install_entity_links.py；只含名字闸门通过的行）；
  2) 闸门没过的行进复核 CSV，只记录不安装；
  3) 头像候选 CSV（performer-avatar-candidate- 前缀，复核页照常可读），其中质量、
     身份都过关的赢家在 `--apply` 下装进 generated/avatars——用户已明确授权
     「不同社媒头像一般是一样的，取最大的最清晰的；不行就加入复核」。
     达不到自动安装线的照样进复核队列，由人在复核页批准。

缺省是空跑：只产上面三份 CSV，一个字节都不落盘。装错的是人脸，而人脸正是复核
队列存在的理由（KRONE 那次「人对、页面错」就是这一类），所以本脚本和仓库里其余
写入脚本一样，写盘必须显式 `--apply`。

`--apply` 只往好里换：落盘前先按同一把尺（`rank_key`）量一次盘上那张，脸没有更大就
不覆盖。头像目录是几条管线共用的，本脚本的候选池未必比别人装的强。

头像源（登出可取；取不到的只记链接，不产头像）：
  X          profile_images 原图（无尺寸后缀），失败退 _400x400 / _200x200
  lit.link   creators/<uuid>/icons/<uuid>.jpe
  babepedia  /pics/<Babe>.jpg —— creator 实体走 babepedia-candidates.csv 的命中行
  jae        Japan Adult Expo 名录里片商自己交的人像 —— 走
             harvest_directory_links.py 产的 jae-performer-links-portraits.csv 命中行
Instagram 登出页拿不到头像图；TikTok 的 oembed 头像只有百来像素——都只记链接。

身份闸门是链式核验：minnano-av 重定向装进账本 → X 页 og:title 命中名字链 →
集链页标题/正文命中名字链。每一跳都有名字证据，断在哪一跳，那一跳之后的产出
就只进复核不自动写入。
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import re
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.avatar_face import (   # noqa: E402
    FaceProbe, face_px_width, read_sidecar,
)
from peach.avatar_provider import (   # noqa: E402
    POLICY_VERSION,
    AvatarCandidateCache,
    inspect_avatar,
    install_entity_avatar,
    provenance_now,
)
from peach.config import (   # noqa: E402
    DATABASE_PATH, GENERATED_DIR, REVIEW_DIR, STATE_DIR, COVER_DIR,
)
from peach.entities import resolve_entity_id  # noqa: E402
from peach.http import HttpRequest, HttpTransport, HttpxTransport   # noqa: E402
from peach.review_csv import read_rows, write_rows   # noqa: E402
from peach.scripting import (   # noqa: E402
    USER_AGENT, HostLimiter, host_under, hostname_of, open_readonly,
)
from peach.social_links import twimg_tiers   # noqa: E402


X_HOSTS = ("x.com", "twitter.com", "mobile.twitter.com")
AGGREGATOR_HOSTS = ("lit.link", "linktr.ee", "allmylinks.com", "twpf.jp")
BLOG_HOSTS = ("ameblo.jp", "lineblog.me", "note.com", "livedoor.jp", "hatenablog.com")
#: 候选的 provider 归到哪个内容寻址缓存。
PROVIDER_CACHES = {"social-web": "social", "babepedia": "babepedia", "jae": "jae", "cover-fallback": "cover"}
SOCIAL_LABELS = {
    ("instagram.com",): "Instagram",
    ("threads.net", "threads.com"): "Threads",
    ("tiktok.com",): "TikTok",
    ("youtube.com",): "YouTube",
    ("fantia.jp",): "Fantia",
    ("fansly.com",): "Fansly",
    ("dmfan.jp",): "DMFan",
    ("booth.pm",): "BOOTH",
    ("pixiv.net",): "pixiv",
    ("twitch.tv",): "Twitch",
    ("onlyfans.com",): "OnlyFans",
    ("line.me",): "LINE",
}
CATALOG_LABELS = {
    ("dmm.co.jp", "dmm.com"): "DMM 检索",
    ("mgstage.com",): "MGStage 检索",
    ("av-event.jp",): "AV活动检索",
    ("javlibrary.com",): "JavLibrary",
}
#: 简介扫描认得的主机。必须精确到主机本身：`x.com` 是 `netflix.com` 的子串、
#: `twitter.com` 是 `ads-twitter.com` 的后缀，松一档就会把广告域当成这个人的主页。
BIO_HOSTS = frozenset(X_HOSTS + AGGREGATOR_HOSTS + BLOG_HOSTS
                      + tuple(h for hosts in SOCIAL_LABELS for h in hosts))
BIO_URL = re.compile(
    r"(?:https?://)?(?:www\.)?("
    + "|".join(re.escape(h) for h in sorted(BIO_HOSTS, key=len, reverse=True))
    + r")(/[^\s\"'<>\\,，。]+)", re.I)

X_OG_TITLE = re.compile(r'property="og:title"\s+content="([^"]+)"')
X_OG_IMAGE = re.compile(r'property="og:image"\s+content="([^"]+)"')
#: profile_images 的尺寸档位；基名是档位前缀，原图（无后缀）最大。
PBS_SUFFIX = re.compile(r"_(?:normal|bigger|mini|x96|[0-9]+x[0-9]+)$")
PBS_URL = re.compile(
    r"https://pbs\.twimg\.com/profile_images/(\d+)/([A-Za-z0-9_-]+)\.(?:jpg|jpeg|png|webp)",
    re.I)
ANCHOR_HREF = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\']', re.I)
PAGE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
OG_IMAGE = re.compile(r'property="og:image"\s+content="([^"]+)"')
LIT_ICON = re.compile(
    r"https://prd\.storage\.lit\.link/images/creators/[0-9a-f-]+/icons/[0-9a-f-]+\.jpe",
    re.I)
TAG = re.compile(r"<[^>]+>")

HANDLE = re.compile(r"^[A-Za-z0-9_]{1,20}$")
#: X 登录页保留路径。页脚（/tos、/privacy、/sitemap.xml、/articles/...）与功能页
#: （/intent、/i）都在 HTML 里以真锚点出现，不拦会把 X 的服务条款装成她的社交链接。
X_RESERVED = frozenset({
    "home", "i", "intent", "hashtag", "search", "messages", "explore",
    "settings", "personalization", "tos", "privacy", "rules", "jobs", "press",
    "legal", "help", "features", "moments", "dev", "developers", "support",
    "safety", "verification", "communities", "compose", "notifications",
    "bookmarks", "login", "signup", "register", "welcome", "sitemap",
})

LINK_FIELDS = ("entity_id", "kind", "name", "link_kind", "label", "url", "evidence",
               "verdict", "reason")
CANDIDATE_FIELDS = (
    "entity_id", "current_name", "matched_name", "name_source", "provider",
    "source_kind", "source_url", "external_id", "gfriends_category",
    "gfriends_file", "width", "height", "face_width", "mime_type", "sha256", "cache_path",
    "provenance_path", "policy_version", "verdict", "avatar_url", "evidence",
)


def fetch(http: HttpTransport, url: str, timeout: float, limiter: HostLimiter,
          accept: str = "text/html,application/xhtml+xml,*/*;q=0.8"):
    """取一次；网络层异常降级为 None，单个来源的 TLS 抖动不拖垮整批。"""
    limiter.wait(url)
    try:
        return http(HttpRequest("GET", url, {"User-Agent": USER_AGENT, "Accept": accept}),
                    timeout, 16 * 1024 * 1024)
    except Exception:
        return None


def visible_text(body: str, limit: int = 20000) -> str:
    return html_mod.unescape(re.sub(r"\s+", " ", TAG.sub(" ", body)))[:limit]


# ---------------------------------------------------------------- 名字链与闸门

def load_entity_chain(connection: sqlite3.Connection, entity_id: int) -> dict:
    row = connection.execute(
        "SELECT kind, canonical_name, metadata_json FROM entity WHERE id=?",
        (entity_id,)).fetchone()
    if row is None:
        return {}
    kind, canonical, raw = row
    jp = ""
    try:
        localization = (json.loads(raw or "{}").get("name_localization") or {})
        jp = localization.get("jp") or ""
    except (TypeError, ValueError):
        jp = ""
    aliases = [r[0] for r in connection.execute(
        "SELECT alias FROM entity_alias WHERE entity_id=? ORDER BY confidence DESC",
        (entity_id,)) if r[0].strip()]
    names: list[str] = []
    for name in [canonical, *aliases, jp]:
        name = (name or "").strip()
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    return {"entity_id": entity_id, "kind": kind, "canonical": canonical or "",
            "names": names}


def identity_match(names: list[str], text: str) -> str:
    """返回名字链里第一个出现在文本中的写法；都不在则空串。"""
    folded = text.casefold()
    for name in names:
        if len(name) >= 2 and name.casefold() in folded:
            return name
    return ""


# ---------------------------------------------------------------- 链接分类

def classify(url: str) -> tuple[str, str]:
    """(link_kind, label)。label 要让人在资料页上不点就知道去的是什么。"""
    host = hostname_of(url)
    if host_under(host, X_HOSTS):
        handle = urllib.parse.urlsplit(url).path.strip("/").split("?")[0]
        return "social", (f"X @{handle}" if handle and HANDLE.match(handle) else "X")
    for hosts, platform in SOCIAL_LABELS.items():
        if host_under(host, hosts):
            handle = urllib.parse.urlsplit(url).path.strip("/").lstrip("@")
            return "social", (f"{platform} @{handle}" if handle else platform)
    if host_under(host, AGGREGATOR_HOSTS):
        return "social", f"链接集（{host}）"
    if host_under(host, BLOG_HOSTS):
        return "social", "博客"
    for hosts, label in CATALOG_LABELS.items():
        if host_under(host, hosts):
            return "catalog", label
    return "official", "官方网站"


def profile_shaped(url: str) -> bool:
    """这个社媒 URL 是不是指向个人主页而不是平台功能页。

    集链页与简介里混着 /intent、/hashtag、/explore、/p/<帖子> 这类地址，它们指向
    平台而不是这个人。X 域内只认「单段 handle 形」路径——X 的页脚与帮助页全是
    单段或两段真锚点，靠枚举保留字加单段约束一起拦；youtu.be 没有个人主页形态。
    """
    host = hostname_of(url)
    path = urllib.parse.urlsplit(url).path
    if host_under(host, X_HOSTS):
        segments = path.strip("/").split("/")
        return (len(segments) == 1 and HANDLE.match(segments[0])
                and segments[0].lower() not in X_RESERVED)
    if host_under(host, ("instagram.com",)):
        return not re.search(r"^/(p|reel|reels|explore|stories)(\b|/)", path)
    if host_under(host, ("youtube.com",)):
        return path.startswith(("/@", "/channel/", "/c/", "/user/"))
    if host_under(host, ("youtu.be",)):
        return False
    if host_under(host, ("tiktok.com",)):
        return path.startswith("/@")
    return True


# ---------------------------------------------------------------- X 登出页

def parse_x_profile(body: str) -> dict:
    """从 X 登出页 HTML 里取 (显示名, 本人头像基名, 简介外链)。

    本人的头像以 og:image 为准——页面里混着大量 pbs.twimg.com 地址（嵌入模块、
    引用卡片），谁的出现次数多都说明不了归属；og:image 是 X 自己声明的本人头像。
    og:image 缺席时退回「去档位后唯一基名」：出现两个以上基名就放弃，绝不从
    「推荐关注」里替人挑头像。
    """
    title = X_OG_TITLE.search(body)
    display = html_mod.unescape(title.group(1)).strip() if title else ""
    og = X_OG_IMAGE.search(body)
    base = ""
    if og and "profile_images" in og.group(1):
        path = html_mod.unescape(og.group(1)).split("profile_images/", 1)[1]
        path = path.split("?", 1)[0]
        # og:image 通常带档位后缀（_200x200.jpg）；先去扩展名再剥档位，
        # 否则锚定行尾的尺寸正则永远碰不到后缀。
        stem = PBS_SUFFIX.sub("", path.rsplit(".", 1)[0])
        base = "https://pbs.twimg.com/profile_images/" + stem
    if not base:
        stripped = {image_id + "/" + PBS_SUFFIX.sub("", stem)
                    for image_id, stem in PBS_URL.findall(body)}
        if len(stripped) == 1:
            base = "https://pbs.twimg.com/profile_images/" + stripped.pop()
    # 档位表由 `peach.social_links.twimg_tiers` 给（厂牌 Logo 那条路共用同一份）。
    avatars: list[str] = twimg_tiers(base + ".jpg") if base else []
    bio_urls: list[str] = []
    for match in BIO_URL.finditer(html_mod.unescape(body)):
        url = "https://" + match.group(1).removeprefix("www.").lower() + match.group(2)
        url = url.rstrip("),.;]")
        if profile_shaped(url):
            bio_urls.append(url)
    return {"display_name": display, "avatars": avatars, "bio_urls": bio_urls}


# ---------------------------------------------------------------- 集链页

def parse_link_page(url: str, body: str) -> dict:
    """集链页（lit.link 等）→ (标题, 头像图, 全部外链)。

    lit.link 是服务端渲染，锚点直出；linktr.ee 对爬虫回 403（安装器的可达性
    记录里有同一笔账），取不到就由调用方记「未取得」，不假装没有。
    """
    title_match = PAGE_TITLE.search(body)
    title = html_mod.unescape(title_match.group(1)).strip() if title_match else ""
    icon = ""
    icon_match = LIT_ICON.search(body)
    if icon_match:
        icon = icon_match.group(0)
    else:
        og = OG_IMAGE.search(body)
        if og:
            icon = html_mod.unescape(og.group(1))
    self_host = hostname_of(url)
    anchors: list[str] = []
    for href in ANCHOR_HREF.findall(body):
        href = html_mod.unescape(href).strip()
        if href.startswith("http") and hostname_of(href) != self_host:
            anchors.append(href)
    return {"title": title, "icon": icon, "anchors": anchors}


# ---------------------------------------------------------------- ledger 读取

def x_handle(url: str) -> str:
    """X/Twitter URL → handle；功能页与带查询串的返回空。"""
    path = urllib.parse.urlsplit(url).path.strip("/")
    handle = path.split("/")[0]
    return handle if HANDLE.match(handle) and handle.lower() not in (
        "home", "intent", "hashtag", "search", "i", "explore", "settings") else ""


def load_babepedia_rows(path: Path) -> dict[int, dict]:
    """babepedia-candidates.csv → {entity_id: 行}，只留命中与需人工确认。"""
    if not path.is_file():
        return {}
    out = {}
    for row in read_rows(path):
        if str(row.get("verdict") or "") not in ("命中", "需人工确认"):
            continue
        if str(row.get("entity_id") or "").isdigit() and row.get("portrait_url"):
            out[int(row["entity_id"])] = row
    return out


def load_jae_rows(path: Path) -> dict[int, list[dict]]:
    """jae 人像候选表 → {entity_id: [行]}，只留唯一命中的。

    2014／2015／2017 三届各有一张，同一个人可能占三行；三张一起进竞选，谁大用谁。
    """
    out: dict[int, list[dict]] = {}
    if not path.is_file():
        return out
    for row in read_rows(path):
        if str(row.get("verdict") or "") != "命中" or not row.get("portrait_url"):
            continue
        if str(row.get("entity_id") or "").isdigit():
            out.setdefault(int(row["entity_id"]), []).append(row)
    return out


def load_targets(connection: sqlite3.Connection, avatar_dir: Path,
                 entities: list[int], force: bool, *, include_cover_targets: bool = False) -> list[dict]:
    """本轮要跑的人：缺头像、且至少有一条可扩张的路径。

    performer 走社媒路线（账本里有 X 链接）与 jae 名录路线（jae 人像候选表的命中行）；
    creator 走 babepedia 路线（babepedia-candidates.csv 的命中行）。几条路都有就都走——
    头像竞选不看路线，谁的最大最清晰用谁。
    """
    babe = {}
    for old_id, row in load_babepedia_rows(GENERATED_DIR / "babepedia-candidates.csv").items():
        target = (old_id if connection.execute('SELECT 1 FROM entity WHERE id=?', (old_id,)).fetchone()
                  else resolve_entity_id(connection, old_id))
        if target is not None:
            babe.setdefault(target, row)
    jae = load_jae_rows(REVIEW_DIR / "jae-performer-links-portraits.csv")
    x_links: dict[int, str] = {}
    for entity_id, url in connection.execute(
            "SELECT entity_id, url FROM entity_link WHERE hostname IN "
            "('x.com','twitter.com','mobile.twitter.com') ORDER BY entity_id"):
        handle = x_handle(url)
        if handle:
            x_links.setdefault(int(entity_id), handle)

    if entities:
        ids = entities
    else:
        ids = [int(row[0]) for row in connection.execute(
            "SELECT id FROM entity WHERE kind IN ('performer','creator') ORDER BY id")]
    targets = []
    for entity_id in ids:
        info = load_entity_chain(connection, entity_id)
        if not info:
            continue
        kind = info["kind"]
        if (avatar_dir / f"{kind}-{entity_id}.img").exists() and not force:
            continue
        routes: dict[str, object] = {}
        if entity_id in x_links:
            routes["x"] = x_links[entity_id]
        if entity_id in babe:
            routes["babepedia"] = babe[entity_id]
        if entity_id in jae:
            routes["jae"] = jae[entity_id]
        if not routes and not (include_cover_targets and kind == "performer"):
            continue
        targets.append({**info, "routes": routes})
    return targets


# ---------------------------------------------------------------- 头像竞选

def passes_auto_bar(width: int, height: int) -> bool:
    """自动安装线：方图 400×400 起（X/lit.link 原图的原生档位），竖构图人像沿用
    Gfriends 的 500/300 门槛。低于线的赢家照常进复核队列，由人批准。"""
    return ((width == height and min(width, height) >= 400)
            or (max(width, height) >= 500 and min(width, height) >= 300))


def shows_a_real_face(candidate: dict) -> bool:
    """这张图里有没有一张真人的脸。检不出就当没有。

    官方社媒头像也可能是一张画：本人挂动漫头像的不在少数。而头像这一格要
    回答的是「这是谁」，画出来的那张脸回答不了，来源档再高也一样。

    判据借 YuNet，它只认真人脸。这把尺会把真人的侧脸、低头一并判成 False——
    那不是误伤，检不出脸的图放进 112px 的圆框里本来也认不出人，退回复核
    队列由人看一眼正是它存在的理由。全批检不出（模型没下到）时整批退回复核，
    比按画布尺寸盲装安全。
    """
    return bool(candidate.get("face_width"))


def fetch_image(http: HttpTransport, url: str, timeout: float, limiter: HostLimiter):
    """取一张并完整解码校验；失败返回 None。"""
    response = fetch(http, url, timeout, limiter, accept="image/*")
    if response is None or response.status != 200:
        return None
    inspected = inspect_avatar(response.body)
    if inspected is None:
        return None
    return response.body, inspected


def harvest_entity(record: dict, http: HttpTransport, limiter: HostLimiter,
                   timeout: float,
                   caches: dict[str, AvatarCandidateCache]) -> dict:
    """跑一个人的全部路线，返回 {links, candidates, notes}。

    links：发现的全部链接（已过 profile_shaped），每条带 gated 标记——
    False 的行只进复核 CSV。candidates：全部有效头像（入内容寻址缓存），
    调用方做同图去重、竞选与安装。
    """
    names = record["names"]
    links: list[dict] = []
    candidates: list[dict] = []
    notes: list[str] = []

    x_identity = ""
    x_handle_value = record["routes"].get("x")
    if x_handle_value:
        response = fetch(http, f"https://x.com/{x_handle_value}", timeout, limiter)
        if response is None or response.status != 200:
            notes.append(f"X @{x_handle_value} 未取得"
                         f"（HTTP {response.status if response else 'transport'}）")
        else:
            body = response.body.decode("utf-8", "replace")
            parsed = parse_x_profile(body)
            page_text = parsed["display_name"] + " " + visible_text(body, 8000)
            x_identity = identity_match(names, page_text)
            if not x_identity:
                notes.append(f"X @{x_handle_value} 页面没有命中名字链，此跳不采信")
            for url in parsed["bio_urls"]:
                kind, label = classify(url)
                links.append({"link_kind": kind, "label": label, "url": url,
                              "evidence": f"X @{x_handle_value} 简介外链",
                              "gated": bool(x_identity)})
            for url in parsed["avatars"]:
                got = fetch_image(http, url, timeout, limiter)
                if got is None:
                    continue
                _, inspected = got
                object_path = caches["social"].store(url, got[0], inspected)
                candidates.append({
                    "provider": "social-web", "source_kind": "official_profile",
                    "source_url": url, "external_id": f"x:{x_handle_value}",
                    "width": inspected.width, "height": inspected.height,
                    "mime_type": inspected.mime_type, "sha256": inspected.sha256,
                    "object_path": object_path,
                    "matched": x_identity, "name_source": "social_identity_gate",
                    "evidence": f"X @{x_handle_value} 头像 "
                                f"{inspected.width}×{inspected.height}",
                })
                break

    # 集链页：X 简介里给的入口。闸门用集链页自己的标题/正文再验一遍——
    # 就算 X 一跳过了，聚合页也可能被别人注册同 handle 抢走。
    aggregator_urls = [link["url"] for link in links
                       if host_under(hostname_of(link["url"]), AGGREGATOR_HOSTS)]
    for agg_url in list(dict.fromkeys(u.rstrip("/") for u in aggregator_urls))[:3]:
        response = fetch(http, agg_url, timeout, limiter)
        if response is None or response.status != 200:
            notes.append(f"集链页未取得：{agg_url}"
                         f"（HTTP {response.status if response else 'transport'}）")
            continue
        body = response.body.decode("utf-8", "replace")
        page = parse_link_page(agg_url, body)
        agg_identity = identity_match(names, page["title"] + " " + visible_text(body))
        if not agg_identity:
            notes.append(f"集链页标题与正文没有命中名字链，只记复核：{agg_url}")
        for href in page["anchors"]:
            if not profile_shaped(href):
                continue
            kind, label = classify(href)
            if kind == "official":
                # 集链页上的不明外链不进 official——那是给事务所这类已核实来源的；
                # 用主机名当标签让人一眼看清它其实是什么。
                kind, label = "social", hostname_of(href)
            links.append({"link_kind": kind, "label": label, "url": href,
                          "evidence": (f"{hostname_of(agg_url)} 页面锚点"
                                       + (f"，标题「{page['title'][:40]}」"
                                          if page["title"] else "")),
                          "gated": bool(agg_identity)})
        if page["icon"] and agg_identity:
            got = fetch_image(http, page["icon"], timeout, limiter)
            if got is not None:
                _, inspected = got
                object_path = caches["social"].store(page["icon"], got[0], inspected)
                candidates.append({
                    "provider": "social-web", "source_kind": "official_profile",
                    "source_url": page["icon"],
                    "external_id": hostname_of(agg_url),
                    "width": inspected.width, "height": inspected.height,
                    "mime_type": inspected.mime_type, "sha256": inspected.sha256,
                    "object_path": object_path,
                    "matched": agg_identity, "name_source": "social_identity_gate",
                    "evidence": f"{hostname_of(agg_url)} 头像 "
                                f"{inspected.width}×{inspected.height}",
                })

    # babepedia 路线（creator）。身份结论沿用回配时的判定，这里不重新猜人。
    babe_row = record["routes"].get("babepedia")
    if babe_row:
        portrait = str(babe_row.get("portrait_url") or "")
        got = fetch_image(http, portrait, timeout, limiter)
        if got is None:
            notes.append("babepedia 档案像未取得或不是可用图片")
        else:
            _, inspected = got
            object_path = caches["babepedia"].store(portrait, got[0], inspected)
            candidates.append({
                "provider": "babepedia", "source_kind": "external_media_library",
                "source_url": portrait,
                "external_id": f"babepedia:{babe_row.get('babepedia_name', '')}",
                "width": inspected.width, "height": inspected.height,
                "mime_type": inspected.mime_type, "sha256": inspected.sha256,
                "object_path": object_path,
                "matched": str(babe_row.get("babepedia_name") or ""),
                "name_source": "babepedia_match",
                "identity_verified": str(babe_row.get("verdict") or "") == "命中",
                "evidence": (f"babepedia「{babe_row.get('babepedia_name', '')}」档案像 "
                             f"{inspected.width}×{inspected.height}，"
                             f"回配判定「{babe_row.get('verdict', '')}」"),
            })

    # jae 路线（performer）。名字判定在 harvest_directory_links.py 那一步做完，只收 `命中`
    # 的行，这里不重新猜人——头像装错人和链接装错人是同一个错误。
    for jae_row in record["routes"].get("jae") or ():
        portrait = str(jae_row.get("portrait_url") or "")
        got = fetch_image(http, portrait, timeout, limiter)
        if got is None:
            notes.append(f"jae 名录人像未取得或不是可用图片：{portrait}")
            continue
        _, inspected = got
        object_path = caches["jae"].store(portrait, got[0], inspected)
        page = str(jae_row.get("page") or "")
        matched = str(jae_row.get("matched_name") or "")
        candidates.append({
            "provider": "jae", "source_kind": "official_directory",
            "source_url": portrait,
            "external_id": f"jae:{page.rsplit('/', 1)[-1] or page}",
            "width": inspected.width, "height": inspected.height,
            "mime_type": inspected.mime_type, "sha256": inspected.sha256,
            "object_path": object_path,
            "matched": matched, "name_source": "jae_directory",
            "identity_verified": str(jae_row.get("verdict") or "") == "命中",
            "evidence": (f"jae 名录「{matched}」人像 "
                         f"{inspected.width}×{inspected.height}，页面 {page}"),
        })

    return {"links": links, "candidates": candidates, "notes": notes}


def cover_fallback(connection, record: dict, cache: AvatarCandidateCache,
                   cover_root: Path = COVER_DIR, probe=None) -> dict | None:
    """仅使用已关联的单人作品封面，保留完整大图和来源。

    封面是这条链路上脸最容易被画布口径坑到的一档：JAV 封面普遍是 800×538 的双联版式，
    右半幅整幅剧照里那张脸常常只有几十像素，而左半幅的文字块一个像素也不给脸。挑封面
    和挑社媒头像用同一把尺——先看脸有多少像素。
    """
    if record["kind"] != "performer":
        return None
    candidates = []
    for (code,) in connection.execute(
            "SELECT DISTINCT a.code FROM asset a JOIN asset_entity ae ON ae.asset_id=a.id "
            "WHERE ae.entity_id=? AND ae.role='performer' AND coalesce(a.code,'')<>'' "
            "AND (a.disposal IS NULL OR a.disposal<>'trash') "
            "AND NOT EXISTS(SELECT 1 FROM asset_entity other WHERE other.asset_id=a.id "
            "AND other.role='performer' AND other.entity_id<>ae.entity_id)", (record["entity_id"],)):
        if not re.fullmatch(r'[A-Za-z0-9-]+', code):
            continue
        path = cover_root / f'{code}.jpg'
        if not path.is_file():
            continue
        data = path.read_bytes()
        inspected = inspect_avatar(data)
        if inspected is None or not passes_auto_bar(inspected.width, inspected.height):
            continue
        source = path.resolve().as_uri()
        stored = cache.store(source, data, inspected)
        candidates.append(dict(provider='cover-fallback', source_kind='single_performer_cover',
                               source_url=source, external_id=code, width=inspected.width,
                               height=inspected.height, mime_type=inspected.mime_type,
                               sha256=inspected.sha256, object_path=stored,
                               matched=record['canonical'], name_source='ledger-performer',
                               identity_verified=False,
                               evidence=f'单人作品 {code} 完整封面'))
    if not candidates:
        return None
    if probe is not None:
        measure_faces(candidates, probe)
    return max(candidates, key=rank_key)


def measure_faces(candidates: list[dict], probe) -> None:
    """就地给每枚候选检一次脸。同一张图（SHA 相同）只检一次。

    落在候选上的是两样东西：`face_width` 是选图判据——量的是像素不是比例，因为
    `rank_key` 要回答的是「装进圆框后看不看得清这张脸」，而那取决于脸有多少像素可用，
    与画布多大无关；`face_record` 是赢家落盘时一并写出的取景 sidecar，同一次检出直接
    复用，不让页面读的脸框和挑图用的脸框来自两次不同的检测。
    """
    seen: dict[str, dict | None] = {}
    for candidate in candidates:
        digest = candidate.get("sha256", "")
        if digest not in seen:
            path = candidate.get("object_path")
            seen[digest] = probe(path) if path else None
        record = seen[digest]
        candidate["face_record"] = record
        candidate["face_width"] = face_px_width(record)


#: 挑图的第一层判据：这张图是拍来做人像的，还是拍来卖片的。脸有多少像素是第二层。
#:
#: 作品封面是这里唯一被单独压一档的来源。它不是拍来做人像的：JAV 封面普遍是双联版式，
#: 左半幅整块留给标题和宣传文字，右半幅那张脸还常被角标和腰封压住。把它放大成一个
#: 64–160 px 的圆头像，圆框里出现的往往是半行日文标题，而不是这个人。像素再多也换不来
#: 一张能认人的脸——这一档差的是构图，不是清晰度，所以必须在比脸宽之前就分开。
#: 缩略图在它下面：那是同一张图被裁小的一份，除了没有别的可用之外没有任何理由选它。
PORTRAIT_TIER = 2
COVER_TIER = 1
THUMBNAIL_TIER = 0
SOURCE_TIERS = {
    # 候选行用的是 source_kind
    "official_profile": PORTRAIT_TIER,        # 本人社媒头像，脸是自己挑的
    "official_directory": PORTRAIT_TIER,      # 事务所与名录的宣材人像
    "official_mirror": PORTRAIT_TIER,
    "external_media_library": PORTRAIT_TIER,  # 媒体库整版人像
    "single_performer_cover": COVER_TIER,
    "thumbnail": THUMBNAIL_TIER,
    # 盘上那张只留下 provenance 里的 provider，两套词汇在这里并成一张表
    "social-web": PORTRAIT_TIER,
    "jae": PORTRAIT_TIER,
    "babepedia": PORTRAIT_TIER,
    "verified-photo-page": PORTRAIT_TIER,
    "cover-fallback": COVER_TIER,
}


def source_tier(row: dict) -> int:
    """认不出来源就当人像档。

    这个默认方向是有意的：认不出来的多半是别的管线装的整版人像（gfriends 那 434 张、
    Stash 那 14 张的 provenance 里根本没有 provider 字段），把它们当封面会让本脚本那些
    400×400 的社媒头像一路把它们顶掉——正是 2026-09-06 那趟 `--force` 的教训。
    """
    for key in (row.get("source_kind"), row.get("provider")):
        if key in SOURCE_TIERS:
            return SOURCE_TIERS[key]
    return PORTRAIT_TIER


def rank_key(row: dict) -> tuple[int, int, int, int, int]:
    """先看这张图是不是拍来做人像的，再看脸的像素宽，画布只是它们都没得比时的退路。

    来源档在最前面，所以一张 400×400 社媒头像胜过 800×538 的作品封面，哪怕封面上
    那张脸更大：封面上和脸抢地方的是标题文字，那不是清晰度问题。同一档之内才轮到
    脸宽说话。

    检不出脸的候选脸宽是 0，自然排到有脸的后面；全都检不出（侧脸、低头、模型缺席）
    时整批都是 0，排序原样落回画布口径：先比短边再比长边——竖构图人像与方图头像
    用同一把尺会偏袒长边。
    """
    return (source_tier(row),
            row.get("face_width") or 0,
            min(row["width"], row["height"]),
            max(row["width"], row["height"]),
            row["width"] * row["height"])


def select_winner(candidates: list[dict]) -> tuple[dict | None, list[dict]]:
    """同图去重（SHA-256 相同 = 同一张图的不同档位/不同平台），取脸最清楚的那一张。

    **脸的像素数先说话，画布只是它没得比时的退路。** 头像最终显示在一个 64–160 px
    的圆框里，能不能认出是谁，取决于那张脸有多少像素，不取决于这张图有多大。
    实测 `performer-8711`：装着的是 640×960 的全身站姿照，脸只有 67 px 宽，在资料页
    圆框里放到源图 1:1 也只占两成；同一个人另有一张 540×810 的半身照，画布小了 15%，
    脸却有 160 px 上下——按画布挑，赢的是前者，而它是两张里唯一看不清脸的。
    判据本身见 `rank_key`。
    """
    by_sha: dict[str, dict] = {}
    for candidate in candidates:
        kept = by_sha.get(candidate["sha256"])
        if kept is None:
            by_sha[candidate["sha256"]] = candidate
            continue
        larger = max((kept, candidate),
                     key=lambda row: (row["width"], row["height"]))
        larger["evidence"] += "；" + (candidate if larger is kept else kept)["evidence"] \
            + "（同图）"
        by_sha[candidate["sha256"]] = larger
    if not by_sha:
        return None, []
    ranked = sorted(by_sha.values(), key=rank_key)
    return ranked[-1], ranked[:-1]


def incumbent_provider(destination: Path) -> str:
    """盘上那张是谁装的。只有 provenance 记得住这件事，图本身不带来源。

    没有 provenance 的按认不出处理，`source_tier` 会把它当人像档——434 张 gfriends
    和 14 张 Stash 头像的 provenance 里就没有 provider 这个字段。
    """
    try:
        record = json.loads(Path(f"{destination}.provenance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(record.get("provider") or "")


def incumbent_row(destination: Path, probe) -> dict | None:
    """盘上那张的选图口径，用来和赢家比。没有图、读不出都返回 None。

    先用现成的取景 sidecar，那本来就是同一次 YuNet 检出的结果；没有 sidecar 才真去
    检一遍。525 张头像各检一次要好几分钟，而绝大多数的 sidecar 早就算好了。
    """
    if not destination.is_file():
        return None
    record = read_sidecar(destination)
    if not (record and record.get("px")):
        record = probe(destination)
    if not (record and record.get("px")):
        return None
    width, height = record["px"][0], record["px"][1]
    if not (width > 0 and height > 0):
        return None
    return {"width": width, "height": height,
            "face_width": face_px_width(record), "face_record": record,
            "provider": incumbent_provider(destination)}


def outranks_incumbent(winner: dict, incumbent: dict | None) -> bool:
    """赢家值不值得覆盖盘上那张。用的是同一把尺 `rank_key`。

    这道闸门是 `--force` 的安全带。`--force` 的语义是「已有头像也重跑」，覆盖的却是
    **所有**已装头像，包括别的管线装的——而本脚本的候选池（X 的 400×400、jae 的
    320×500、作品封面）比 `verified-photo-page` 那类整版人像差一大截。2026-09-06 实测：
    一趟 `--force --apply` 换掉 350 张，其中 282 张脸变小，脸宽中位数从 262 px 掉到
    139 px。缺头像时这道闸门是空操作，只在真要覆盖时才拦。
    """
    if incumbent is None:
        return True
    return rank_key(winner) > rank_key(incumbent)


def install_avatar(avatar_dir: Path, kind: str, entity_id: int, winner: dict) -> Path:
    """赢家直接落盘到 /entity-image 真正读的目录，带 .ct、provenance 与取景 sidecar。"""
    if source_tier(winner) == COVER_TIER:
        raise ValueError("作品封面只作为候选留档，不安装为人物头像")
    data = winner["object_path"].read_bytes()
    if hashlib.sha256(data).hexdigest() != winner["sha256"]:
        raise ValueError("缓存对象与竞选记录的哈希不一致，拒绝安装")
    # 脸在竞选时就检过了（`measure_faces`），同一张图不必再跑一次模型。
    return install_entity_avatar(
        avatar_dir, kind, entity_id, data, winner["mime_type"], {
            "source": "social avatar harvest",
            "provider": winner["provider"],
            "source_url": winner["source_url"],
            "external_id": winner["external_id"],
            "matched_name": winner.get("matched") or "",
            "name_source": winner.get("name_source") or "",
            "sha256": winner["sha256"],
            "width": winner["width"],
            "height": winner["height"],
            "policy_version": POLICY_VERSION,
        }, winner.get("face_record"), probe_face=False)


def provenance_for(cache: AvatarCandidateCache, entity_id: int, candidate: dict):
    return provenance_now(
        entity_id=int(entity_id), provider=candidate["provider"],
        source_kind=candidate["source_kind"], matched_name=candidate.get("matched") or "",
        name_source=candidate.get("name_source") or "",
        external_id=candidate["external_id"], upstream_url=candidate["source_url"],
        width=candidate["width"], height=candidate["height"],
        mime_type=candidate["mime_type"], sha256=candidate["sha256"],
        cache_path=str(candidate["object_path"].relative_to(cache.root)),
    )


def merge_prior_pending(db_path: Path, new_path: Path, rows: list[dict]) -> None:
    """把上一批候选里还没被判过的行并进本轮，别让换批次把复核队列清空。

    复核页每个类别只读 mtime 最新的一份 CSV；不并的话，2026-08-25 批次里
    还没人判过的 17 条 Gfriends 候选会从界面上凭空消失。已判过的不再并——
    判过即终局，装不装都已经有结论。
    """
    decided: set[str] = set()
    try:
        connection = open_readonly(db_path)
        decided = {str(row[0]) for row in connection.execute(
            "SELECT item_key FROM review_decision WHERE category='performer_avatars'")}
        connection.close()
    except sqlite3.Error:
        pass
    prior_files = sorted(GENERATED_DIR.glob("performer-avatar-candidate-*.csv"),
                         key=lambda p: p.stat().st_mtime_ns)
    prior = [p for p in prior_files if p.resolve() != new_path.resolve()]
    if not prior:
        return
    fresh_keys = {str(row["entity_id"]) for row in rows}
    carried = 0
    for old in read_rows(prior[-1]):
        key = str(old.get("entity_id") or "")
        if not key or key in fresh_keys or key in decided:
            continue
        rows.append(old)
        carried += 1
    if carried:
        print(f"并入上一批未判定候选 {carried} 条（{prior[-1].name}）")


def build_parser() -> argparse.ArgumentParser:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--avatars", type=Path, default=GENERATED_DIR / "avatars")
    parser.add_argument("--cache-root", type=Path,
                        default=GENERATED_DIR / "provider-cache" / "performer-avatars")
    parser.add_argument("--candidates", type=Path,
                        default=GENERATED_DIR / f"performer-avatar-candidate-social-{stamp}.csv")
    parser.add_argument("--out-links", type=Path,
                        default=GENERATED_DIR / f"social-links-{stamp}.csv")
    parser.add_argument("--review-links", type=Path,
                        default=GENERATED_DIR / f"social-links-unverified-{stamp}.csv")
    parser.add_argument("--entities", type=str, default="",
                        help="逗号分隔的 entity id；缺省跑全部缺头像且有路线的人")
    parser.add_argument("--force", action="store_true",
                        help="已有头像也参加竞选（默认跳过）；只有脸更大才覆盖")
    parser.add_argument("--apply", action="store_true",
                        help="把过线的赢家头像落盘到 --avatars；缺省只产 CSV 不写盘")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".social-avatars.lock")
    return parser


def run(args) -> int:
    limiter = HostLimiter({
        "x.com": 2.0, "pbs.twimg.com": 0.3, "lit.link": 1.5,
        "babepedia.com": 3.0, "linktr.ee": 2.0, "jae.tokyo": 1.2,
    })
    connection = open_readonly(args.db)
    http = HttpxTransport()
    caches = {
        "social": AvatarCandidateCache(args.cache_root / "social"),
        "babepedia": AvatarCandidateCache(args.cache_root / "babepedia"),
        "jae": AvatarCandidateCache(args.cache_root / "jae"),
        "cover": AvatarCandidateCache(args.cache_root / "cover"),
    }
    candidate_rows: list[dict] = []
    install_rows: list[dict] = []
    review_rows: list[dict] = []
    installed = 0
    kept = 0
    probe = FaceProbe()
    try:
        entities = [int(v) for v in str(args.entities).split(",") if v.strip().isdigit()]
        targets = load_targets(connection, args.avatars, entities, args.force, include_cover_targets=True)
        if args.limit:
            targets = targets[:args.limit]
        print(f"待跑 {len(targets)} 人："
              + "、".join(f"{r['entity_id']}({r['kind'][0]})" for r in targets[:15])
              + ("…" if len(targets) > 15 else ""), flush=True)

        for record in targets:
            entity_id, kind = record["entity_id"], record["kind"]
            result = harvest_entity(record, http, limiter, args.timeout, caches)
            # 同一地址会同时出现在 X 简介与集链页锚点里，去重保序。键用主机+路径：
            # Instagram/YouTube 的分享链接每次抓都会换 igsh/si 跟踪参数，按完整
            # URL 去重会把同一条链接装两遍。
            seen: set[tuple[str, str]] = set()
            deduped = []
            for link in result["links"]:
                split = urllib.parse.urlsplit(link["url"])
                key = (split.hostname or "", split.path.rstrip("/"))
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(link)
            for link in deduped:
                row = {"entity_id": entity_id, "kind": kind,
                       "name": record["canonical"], "link_kind": link["link_kind"],
                       "label": link["label"], "url": link["url"],
                       "evidence": link["evidence"]}
                if link["gated"]:
                    row.update(verdict="确认", reason="")
                    install_rows.append(row)
                else:
                    row.update(verdict="需人工确认", reason="名字闸门未过")
                    review_rows.append(row)

            # 先量脸再挑：赢家由脸的像素数定，画布只是没得比时的退路。
            measure_faces(result["candidates"], probe)
            winner, runners_up = select_winner(result["candidates"])
            if (winner is None or not winner.get('matched')
                    or not passes_auto_bar(winner['width'], winner['height'])):
                fallback = cover_fallback(connection, record, caches['cover'],
                                          probe=probe)
                if fallback:
                    if winner:
                        runners_up.append(winner)
                    winner = fallback
            # 盘上那张只量一次，赢家拿它当门槛：新的没有更好就不动它。
            incumbent = incumbent_row(args.avatars / f"{kind}-{entity_id}.img", probe)
            for candidate in ([winner] if winner else []) + runners_up:
                cache = caches[PROVIDER_CACHES[candidate["provider"]]]
                provenance_path = cache.store_provenance(
                    provenance_for(cache, entity_id, candidate))
                identity_ok = candidate.get("source_kind") != 'single_performer_cover' and candidate.get("identity_verified", True) and bool(
                    candidate.get("matched"))
                is_winner = candidate is winner
                note = ""
                if is_winner and identity_ok and not shows_a_real_face(candidate):
                    note = "竞选赢家但检不出真人脸（多半是动漫头像），不自动安装，待复核"
                elif is_winner and identity_ok:
                    note = "竞选赢家；质量与身份过关"
                    # 「没过线」和「空跑」是两件事，别都写成待复核：前者要人去看，
                    # 后者只差一个 --apply。
                    if not passes_auto_bar(candidate["width"], candidate["height"]):
                        note += "，未达自动安装线，待复核"
                    elif not args.apply:
                        note += "，已过自动安装线；加 --apply 才落盘"
                    elif not outranks_incumbent(candidate, incumbent):
                        note += (f"，盘上那张的脸更大（{incumbent['face_width']} px "
                                 f"vs {candidate.get('face_width') or 0} px），不覆盖")
                        kept += 1
                    else:
                        destination = install_avatar(args.avatars, kind, entity_id,
                                                     candidate)
                        note += f"，已安装 {destination.name}"
                        installed += 1
                elif is_winner:
                    note = "竞选赢家但身份未核实；先核链接再装"
                else:
                    note = "竞选落选（同图或脸更小）；留档备查"
                candidate_rows.append({
                    "entity_id": entity_id, "current_name": record["canonical"],
                    "matched_name": candidate.get("matched") or "",
                    "name_source": candidate.get("name_source") or "",
                    "provider": candidate["provider"],
                    "source_kind": candidate["source_kind"],
                    "source_url": candidate["source_url"],
                    "external_id": candidate["external_id"],
                    "gfriends_category": "", "gfriends_file": "",
                    "width": candidate["width"], "height": candidate["height"],
                    # 复核的人据此一眼看出「这张脸够不够大」，比画布尺寸有用得多。
                    "face_width": candidate.get("face_width") or 0,
                    "mime_type": candidate["mime_type"],
                    "sha256": candidate["sha256"],
                    "cache_path": candidate["object_path"].name,
                    "provenance_path": provenance_path.name,
                    "policy_version": POLICY_VERSION,
                    "verdict": ("ok" if identity_ok and shows_a_real_face(candidate)
                                else "no_real_face" if identity_ok
                                else "identity_unverified"),
                    "avatar_url": candidate["source_url"],
                    "evidence": candidate["evidence"] + "；" + note,
                })
            for note in result["notes"]:
                print(f"  [{entity_id}] {note}", flush=True)
            print(f"[{entity_id}] {record['canonical']}：链接 {len(deduped)} 条"
                  f"（自动 {sum(1 for l in deduped if l['gated'])}）、"
                  f"候选 {len(result['candidates'])} 张", flush=True)
    finally:
        http.close()
        connection.close()

    merge_prior_pending(args.db, args.candidates, candidate_rows)
    write_rows(args.candidates, CANDIDATE_FIELDS, candidate_rows, atomic=True)
    write_rows(args.out_links, LINK_FIELDS, install_rows, atomic=True)
    write_rows(args.review_links, LINK_FIELDS, review_rows, atomic=True)
    if probe.unavailable:
        # 说清这一轮是按什么挑的。不说的话，一次「今天没网下不到 ONNX」会静悄悄地
        # 把整批退回画布口径，产出看着完全正常，脸却全挑小了。
        print(f"人脸检测未取得（{probe.unavailable}）：本轮按画布尺寸挑图，"
              "face_width 一列全是 0；模型可用后加 --force 重跑该批。")
    if not args.apply:
        print("空跑：以上一张头像都没落盘；确认候选无误后加 --apply 重跑。")
    print({"候选": len(candidate_rows), "自动安装": installed, "在位的更好未覆盖": kept,
           "带脸候选": sum(1 for r in candidate_rows if int(r.get("face_width") or 0) > 0),
           "链接待装": len(install_rows), "链接待复核": len(review_rows),
           "候选 CSV": str(args.candidates), "链接 CSV": str(args.out_links),
           "复核链接 CSV": str(args.review_links)})
    print("链接装账：python scripts/install_entity_links.py --database <db> "
          f"--input {args.out_links.name} --apply --backup <备份路径>")
    return 0


if __name__ == "__main__":
    from peach.jobs import job_main
    raise SystemExit(job_main(build_parser, run))
