"""跟人走的界面设置。

大部分界面偏好留在浏览器本地就够了——悬停延迟、每批数量、默认排序，这些更像
「这台设备上顺手」而不是「我的习惯」，换台电脑重设一次也不奇怪。

侧栏不一样。左侧导航放哪些入口、什么顺序，是长出来的使用习惯；在 Windows 上排好，
到 Mac 上又是默认顺序，那不叫两台设备，叫两个应用。所以它跟着账本走。

落点是 `profile.settings_json`——0001 建表时就留好的字段，一直是空的 `{}`。
不新建表也就不需要迁移。

写入受 ledger 只读闸门约束：reader（只读端）改不了顺序，但读到的是 writer 的那份，
这正是「同一份习惯」的意思。
"""
from __future__ import annotations

import json
import time
from typing import Protocol

from .feeds import DEFAULT_HIDDEN_COMPILATIONS, EXCERPT, GROUP_COMPILATION, SOLO_COMPILATION
from .media_sources import SCANNABLE_LOCATIONS
from .remote_media_cache import AVATAR_REFRESH_SECONDS, FAVICON_REFRESH_SECONDS

from .profiles import DEFAULT_PROFILE_ID

#: 默认就在侧栏里的入口，顺序即默认顺序。首页之后是关注（每天有新东西的那一屏），
#: 再是主库最常用的浏览模式 JAV，然后三个索引，回头找的已标记，管理垫底。
DEFAULT_SIDEBAR_ORDER = (
    "", "follow", "jav", "performers", "tags", "studios", "flagged", "manage",
)
#: 可以加进侧栏、但默认不在的入口。播放列表和沉浸模式都是从一条作品或一个索引里
#: 发起的动作，常驻一格换来的是每次都要跳过它，所以归到这里等人自己加。
OPTIONAL_SIDEBAR_KEYS = (
    "playlists", "immerse",
    "stats", "review", "data-cleanup", "trash", "follow-manage", "quality",
)
ALL_SIDEBAR_KEYS = frozenset(DEFAULT_SIDEBAR_ORDER) | frozenset(OPTIONAL_SIDEBAR_KEYS)
SIDEBAR_KEY_ALIASES = {"ads": "data-cleanup", "dupes": "data-cleanup"}

FOLLOW_INITIAL_DAYS = (0, 7, 30, 90)
DEFAULT_FOLLOW_INITIAL_DAYS = 30
#: 搜索框下拉里列几条最近搜索；0 是不记录。记录本身就在账本里（`/api/search-history`），
#: 上限跟着它走，否则同一份记录在两台设备上显示的条数不一样。
SEARCH_HISTORY_LIMIT_MAX = 50

#: 只有这些键跟着账本走。白名单而不是黑名单：将来往设置里加字段的人必须显式表态
#: 它该不该跨机同步，而不是默认就同步过去。
#: `postSetupTutorialDone` 说的是「这套馆藏已经装完了」，不是「这台设备看过教程」——
#: 装完就是装完，换台设备打开不该再被教一遍，所以它也跟着账本走。
#: 整理模板（ADR-0039）同理：它是「我要我的库长什么样」，不是这台设备上的顺手。
#: 新作那一行收不收合集（`feeds.compilation_kind`）同样是服务端行为：列表、未读数和
#: 替哪些壳补封面都按它算，所以跟着账本走。
SYNCED_SETTING_KEYS = frozenset({
    "sidebarOrder", "followInitialDays", "postSetupTutorialDone",
    "organizeTemplates", "feedHideGroupCompilations", "feedHideSoloCompilations",
    "feedHideExcerpts", "searchHistoryLimit",
})
#: 三个收起开关的键与各自对应的那一类，缺省值取 `feeds.DEFAULT_HIDDEN_COMPILATIONS`。
FEED_COMPILATION_SWITCHES = (
    ("feedHideGroupCompilations", GROUP_COMPILATION),
    ("feedHideSoloCompilations", SOLO_COMPILATION),
    ("feedHideExcerpts", EXCERPT),
)


class SettingsContract(Protocol):
    def read_connection(self): ...
    def write_transaction(self): ...


def normalise_sidebar_order(raw) -> list[str]:
    """把任意输入收敛成一份合法的侧栏顺序。

    数组同时表达顺序和显隐：在里面就显示，不在就不显示。所以「取消显示」和
    「调整顺序」是同一个写入，不需要两个字段。

    不认识的键直接丢掉——它可能来自旧版本或者手改的载荷，留着会让前端渲染出
    一个点不开的入口。全部丢光时回落到默认顺序，空侧栏没有可用性可言。
    """
    if not isinstance(raw, list):
        return list(DEFAULT_SIDEBAR_ORDER)
    seen: list[str] = []
    for raw_key in raw:
        if not isinstance(raw_key, str):
            continue
        key = SIDEBAR_KEY_ALIASES.get(raw_key, raw_key)
        if key not in ALL_SIDEBAR_KEYS or key in seen:
            continue
        seen.append(key)
    return seen or list(DEFAULT_SIDEBAR_ORDER)


def normalise_follow_initial_days(raw) -> int:
    """首次采集的历史天数；0 表示不限时间。"""
    if isinstance(raw, bool):
        return DEFAULT_FOLLOW_INITIAL_DAYS
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_FOLLOW_INITIAL_DAYS
    return days if days in FOLLOW_INITIAL_DAYS else DEFAULT_FOLLOW_INITIAL_DAYS


def follow_initial_days(contract: SettingsContract) -> int:
    return normalise_follow_initial_days(_stored(contract).get("followInitialDays"))


def normalise_search_history_limit(raw) -> int | None:
    """0 到上限之间的整数原样收下，其余一律当没设置过。

    没设置过读出来是 None 而不是默认值：前端据此把各浏览器本地记着的那个数同步上来一次。
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    if isinstance(raw, float) and not raw.is_integer():
        return None
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return None
    return limit if 0 <= limit <= SEARCH_HISTORY_LIMIT_MAX else None


def normalise_organize_templates(raw) -> dict:
    """收敛整理模板：`{来源: {"file": 模板, "dir": 模板}}`。

    模板在这里就要过一遍 `validate_template`：它决定文件会被改成什么名字、放到哪一层，
    一个带 `..` 的模板存进去、等到执行那一刻才拒绝，用户已经点过确认了。
    """
    from .organize_templates import validate_template
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, dict[str, str]] = {}
    for location, entry in raw.items():
        if location not in SCANNABLE_LOCATIONS or not isinstance(entry, dict):
            continue
        # 模板不合法时 `TemplateError` 原样抛给调用方：它是 `ValueError`，API 翻成 400
        # 并把那句中文交给用户，而不是把这一份悄悄丢掉。
        file_template = validate_template(str(entry.get("file") or ""))
        dir_template = validate_template(str(entry.get("dir") or ""), directory=True)
        if file_template or dir_template:
            clean[location] = {"file": file_template, "dir": dir_template}
    return clean


def organize_templates(contract: SettingsContract) -> dict:
    """服务端读到的整理模板。没配过就是空字典，整理端点据此拒绝执行。"""
    return normalise_organize_templates(_stored(contract).get("organizeTemplates"))


def _feed_switches(payload: dict) -> dict[str, bool]:
    return {key: payload[key] if isinstance(payload.get(key), bool)
            else kind in DEFAULT_HIDDEN_COMPILATIONS
            for key, kind in FEED_COMPILATION_SWITCHES}


def hidden_compilations(contract: SettingsContract) -> frozenset[str]:
    """新作那一行收起哪几类合集。"""
    switches = _feed_switches(_stored(contract))
    return frozenset(kind for key, kind in FEED_COMPILATION_SWITCHES if switches[key])


def _stored(contract: SettingsContract) -> dict:
    with contract.read_connection() as connection:
        row = connection.execute(
            "SELECT settings_json FROM profile WHERE id=?", (DEFAULT_PROFILE_ID,),
        ).fetchone()
    if row is None:
        return {}
    try:
        payload = json.loads(row["settings_json"] or "{}")
    except (TypeError, ValueError):
        # 载荷坏了就当没设置过：这是界面偏好，不值得让页面打不开。
        return {}
    return payload if isinstance(payload, dict) else {}


def q_settings(contract: SettingsContract, _args=None) -> dict:
    """读取跟人走的那部分设置。缺省时返回默认值，不返回空。"""
    payload = _stored(contract)
    return {
        "sidebarOrder": normalise_sidebar_order(payload.get("sidebarOrder")),
        "avatarRefreshDays": AVATAR_REFRESH_SECONDS // 86400,
        "faviconRefreshDays": FAVICON_REFRESH_SECONDS // 86400,
        "followInitialDays": normalise_follow_initial_days(payload.get("followInitialDays")),
        "postSetupTutorialDone": payload.get("postSetupTutorialDone") is True,
        "organizeTemplates": normalise_organize_templates(payload.get("organizeTemplates")),
        **_feed_switches(payload),
        "searchHistoryLimit": normalise_search_history_limit(payload.get("searchHistoryLimit")),
    }


def w_settings(contract: SettingsContract, body) -> dict:
    """写入跟人走的设置；只认白名单里的键。

    合并而不是整体替换：请求里没提到的键保持原样，这样将来新增同步字段时，
    旧版本前端提交的载荷不会把它抹掉。
    """
    if not isinstance(body, dict):
        raise TypeError("settings body must be an object")
    unknown = set(body) - SYNCED_SETTING_KEYS
    if unknown:
        raise ValueError(f"这些设置不跟数据库同步：{sorted(unknown)}")
    if not body:
        raise ValueError("没有要写入的设置")

    merged = _stored(contract)
    if "sidebarOrder" in body:
        merged["sidebarOrder"] = normalise_sidebar_order(body["sidebarOrder"])
    if "followInitialDays" in body:
        merged["followInitialDays"] = normalise_follow_initial_days(body["followInitialDays"])
    if "postSetupTutorialDone" in body:
        merged["postSetupTutorialDone"] = body["postSetupTutorialDone"] is True
    if "organizeTemplates" in body:
        merged["organizeTemplates"] = normalise_organize_templates(body["organizeTemplates"])
    if "searchHistoryLimit" in body:
        limit = normalise_search_history_limit(body["searchHistoryLimit"])
        if limit is None:
            raise ValueError(f"searchHistoryLimit 只收 0 到 {SEARCH_HISTORY_LIMIT_MAX} 的整数")
        merged["searchHistoryLimit"] = limit
    for key, _kind in FEED_COMPILATION_SWITCHES:
        if key in body:
            if not isinstance(body[key], bool):
                raise ValueError(f"{key} 只收 true 或 false")
            merged[key] = body[key]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with contract.write_transaction() as connection:
        row = connection.execute("SELECT settings_json FROM profile WHERE id=?", (DEFAULT_PROFILE_ID,)).fetchone()
        try:
            current = json.loads(row[0] or "{}") if row else {}
        except (TypeError, ValueError):
            current = {}
        current = current if isinstance(current, dict) else {}
        current.update({key: merged[key] for key in body})
        updated = connection.execute(
            "UPDATE profile SET settings_json=?, updated_at=? WHERE id=?",
            (json.dumps(current, ensure_ascii=False), stamp, DEFAULT_PROFILE_ID),
        ).rowcount
        if not updated:
            raise ValueError(f"profile {DEFAULT_PROFILE_ID} 不存在")
    return {"ok": True, **q_settings(contract)}
