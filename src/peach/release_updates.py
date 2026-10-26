"""GitHub 测试版查询；传输复用共享 HTTPX，安装入口指向正式发行页。"""
from __future__ import annotations

import json
import re
import httpx

from . import __version__, distribution
from .http import HttpRequest, HttpxTransport
from .update_packages import TOOL_PATHS

RELEASES_URL = "https://github.com/peach-mitao/peach/releases"
API_URL = "https://api.github.com/repos/peach-mitao/peach/releases?per_page=100"


def snapshot() -> dict:
    return {"current_version": __version__, "latest_version": None,
            "channel": "测试版", "installation": "独立测试包" if distribution.standalone() else "源码运行",
            "state": "unchecked", "message": "尚未检查", "release_url": RELEASES_URL}


def _version(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        return None
    return tuple(map(int, value.removeprefix("v").split(".")))


def check(*, transport=None) -> dict:
    result = snapshot()
    client = transport or HttpxTransport()
    try:
        response = client(HttpRequest("GET", API_URL, {"Accept": "application/vnd.github+json",
                          "X-GitHub-Api-Version": "2022-11-28"}), 12.0, 2_000_000)
        if response.status != 200:
            message = "GitHub 请求受限，请稍后重试。" if response.status in (403, 429) else "无法连接 GitHub，请重试。"
            return dict(result, state="error", message=message)
        rows = json.loads(response.body)
        if not isinstance(rows, list):
            raise ValueError("release list required")
        releases = []
        for row in rows:
            if not isinstance(row, dict) or row.get("draft"):
                continue
            version = _version(row.get("tag_name"))
            if version is None:
                continue
            tag = row["tag_name"]
            assets = row.get("assets") or []
            product = 'PeachDesktop' if distribution.embedded_window() else 'Peach'
            prefix = f"{product}-{tag.removeprefix('v')}-windows-x64"
            uploaded = {a.get("name"): a for a in assets if isinstance(a, dict) and a.get("state") == "uploaded"}
            full = uploaded.get(prefix + ".zip")
            if full is None:
                continue
            complete_tools = all(prefix + f"-tools-{tool}.zip" in uploaded for tool in TOOL_PATHS)
            asset = uploaded.get(prefix + "-update.zip", full) if complete_tools else full
            releases.append((version, tag, asset, full))
        if not releases:
            message = '独立窗口测试版请使用配套安装包更新。' if distribution.embedded_window() else '暂未找到可下载的测试版。'
            return dict(result, state="empty", message=message)
        version, tag, asset, full = max(releases, key=lambda item: item[0])
        current = _version(result["current_version"])
        if current is None:
            raise ValueError("current version required")
        state = "available" if version > current else "ahead" if version < current else "current"
        message = {"available": "有新版本可下载。", "ahead": "当前版本高于已发布测试版。", "current": "已是最新测试版。"}[state]
        return dict(result, latest_version=tag.removeprefix("v"), state=state, message=message,
                    asset_url=f"{RELEASES_URL}/download/{tag}/{asset['name']}",
                    asset_size=asset.get("size", 0), asset_digest=asset.get("digest"),
                    package_kind="update" if asset is not full else "full",
                    release_url=f"{RELEASES_URL}/tag/{tag}")
    except (OSError, ValueError, TypeError, TimeoutError, httpx.HTTPError):
        return dict(result, state="error", message="未能取得版本信息，请重试。")
    finally:
        if transport is None:
            client.close()
