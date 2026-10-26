"""javdb 资料页的名字层：搜索命中、现名、旧名。

拆成模块是因为两处要用同一份判据：`harvest_directory_links.py` 拿名字确认「搜到的
是不是账本里这个人」，`harvest_javdb_cn_names.py` 拿名字取中文写法。各写一份正则的
代价是站上改版时只修好一处，另一处继续按旧结构解析，还不报错。

页面顶部的结构（2026-09-04 实测）：

    <span class="actor-section-name">深田詠美, 深田えいみ</span>
    <span class="section-meta">天海こころ</span>
    <span class="section-meta">324 部影片</span>

`actor-section-name` 是**现名**，中文写法与日文写法用逗号并列；两种写法同形时只有
一个（`大橋未久`）。紧随的 `section-meta` 是**旧艺名**，最后一个 `section-meta` 是
影片数。现名与旧名必须分开取：`JULIA` 那页的旧名里有 `京香じゅりあ`，把两者混成
一串就分不出「这是她的中文名」和「这是她用过的旧艺名」。

`?locale=zh-CN` 只切界面语言，女优名是数据不跟着变（实测 `愛音麻里亞` 在简体界面下
仍是繁体）。要简体得自己转，这里不做——转换属于调用方的本地化判断。
"""
from __future__ import annotations

import html as html_lib
import re

from .social_links import name_key

BASE = "https://javdb.com/"
SEARCH = BASE + "search?f=actor&q={}"

#: 从资料页取来的名字写进 `entity_alias` 时记的来源。两个脚本都往这里写
#: （`localize_performer_names.py` 写降级下来的旧规范名，`apply_alias_candidates.py`
#: 写页面上的新写法），来源串必须是同一个：回溯「这个名字是谁写的」时，它是唯一
#: 留着这个区别的地方，两处各写一份字面量就等于埋着一次静默的分叉。
ALIAS_SOURCE = "javdb-actor-page"

#: 搜索结果里的演员卡。`title` 一栏就是这个人在站上的全部写法，不必先点进去。
BOX = re.compile(r'class="box actor-box">\s*<a href="(/actors/[^"]+)" title="([^"]*)"', re.S)
#: 整张卡：站内 id、`title` 与卡片正文。正文里头像上那枚 `info` 标记是记录类型。
CARD = re.compile(r'class="box actor-box">\s*<a href="/actors/([A-Za-z0-9]+)" title="([^"]*)">(.*?)</a>',
                  re.S)

INFO = re.compile(r'class="info">([^<]*)<')
NAME = re.compile(r'class="actor-section-name">([^<]*)<')
META = re.compile(r'class="section-meta">([^<]*)<')
#: 「323 部影片」也在 `section-meta` 里，它不是名字。
COUNT = re.compile(r"^\d+\s*部影片$")
#: 一部分资料页要登录才给，回的是登入页而不是 401/403。同一位女优在站上常有两条
#: 记录（有碼那条公开、無碼那条要登录），搜索结果把两条都给出来，所以这不是抓失败。
LOGIN = re.compile(r"<title>\s*登入\s*\|")
ACTOR_ID = re.compile(r'href="/actors/([A-Za-z0-9]+)/collect"')

#: 同一位女优的无码那条记录，现名后面挂着 `(無碼)`。它是记录类型不是名字的一部分，
#: 留着会让 `木下ひまり(無碼)` 与 `木下ひまり` 变成两个人。
RECORD_KIND = re.compile(r"[(（](?:無碼|无码|有碼|有码|流出|破解|中文字幕)[)）]\s*$")

_TAG = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG.sub(" ", text)).replace("\xa0", " ")).strip()


def split_names(text: str) -> list[str]:
    """一栏里的多个名字。不按空格拆——罗马字名里有空格。"""
    parts = (RECORD_KIND.sub("", part).strip()
             for part in re.split(r"[、,，/／|｜;；]", text))
    return [part for part in parts if part]


def _names(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = clean(value)
        if text and not COUNT.match(text):
            out += split_names(text)
    return list(dict.fromkeys(out))


def current_names(html: str) -> list[str]:
    """现名的各种写法。同形时只有一个。"""
    found = NAME.search(html)
    return _names([found.group(1)]) if found else []


def former_names(html: str) -> list[str]:
    """现名底下那一栏：这个人的其他叫法。影片数那一条不算。

    站上不给这一栏任何标签，旧艺名与昵称混在一起（`RJM8` 是四个真艺名，`KxPb`
    只有一个爱称 `傻梦`）。解析层分不开也不猜，判它是不是艺名归调用方。
    """
    return _names(META.findall(html))


def all_names(html: str) -> list[str]:
    """页面上这个人的全部写法，现名在前。"""
    return list(dict.fromkeys(current_names(html) + former_names(html)))


def actor_id(html: str) -> str:
    """站内 id，例如 `RJM8`。它是这一页的身份，比名字稳。"""
    found = ACTOR_ID.search(html)
    return found.group(1) if found else ""


def search_hits(html: str, wanted: set[str], key=name_key) -> list[str]:
    """搜索结果里名字对得上的资料页路径。`wanted` 必须已按 `key` 归一。

    搜的是账本里的名字，回来的却不一定是同一个人（javdb 的演员搜索会给近似结果）。
    卡片的 `title` 一栏已经列出这个人在站上的全部写法，够判是不是同一个人：对不上的
    不点进去，省一次请求，也不给判定送一页与账本无关的名字。

    对得上的全都返回，不只取第一个。同一个名字在站上真有两位时，两页都进判定——
    取第一个是默默替用户挑了一位。

    `key` 可以换成更宽的归一（比如先折叠繁简与日本字形）。默认不折叠：折叠要
    opencc，那是可选依赖，不能让这一层跟着必装。
    """
    out = []
    for path, title in BOX.findall(html):
        if wanted & {key(name) for name in split_names(clean(title))}:
            out.append(path)
    return list(dict.fromkeys(out))


def search_cards(html: str) -> list[dict]:
    """搜索结果里的演员卡：站内 id、标题一栏的全部写法、记录类型（有碼那条是空串）。

    卡片上的 id 就是资料页的 id，登记入口不必再点进资料页；無碼那条资料页常要登录，
    点进去也只拿到登入页。
    """
    cards = []
    for actor, title, body in CARD.findall(html):
        record = INFO.search(body)
        cards.append({"id": actor, "names": split_names(clean(title)),
                      "record": clean(record.group(1)) if record else ""})
    return cards


def one_person(cards: list[dict]) -> bool:
    """名字对得上的这几张卡是不是同一个人：每种记录类型至多一张。

    站上一位女优常有两条记录，有碼那条不带标记、無碼那条带 `無碼`，两张卡的名字一样，
    两个 id 都是她的。同一种记录出现两张，就是站上真有两位同名的人，不替用户挑。
    """
    kinds = [card["record"] for card in cards]
    return bool(kinds) and len(kinds) == len(set(kinds))
