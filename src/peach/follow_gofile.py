"""展开 Gofile 文件页。

Gofile 是第三方文件站，不是追更来源。F95 和 FANBOX 的帖子里可能贴 Gofile 链接，
但 Gofile 本身跟这两个站没有关系。

这段逻辑独立成模块，不挂在 `_BaseConnector` 上：挂上去的话每个连接器——包括根本不碰
Gofile 的 rule34、kemono、paheal——都继承着它，而且发请求必须记得写
`connector_headers=False`：漏掉的话，来源站的 Cookie（F95 的会话、FANBOX 的登录态）
会跟着一起发到 gofile.io。那是个只靠「调用方记得传参」维持的安全约束。

搬出来之后，展开器自己持有 transport，只发自己的 User-Agent 和自己的 Bearer token。
把来源站凭据带到第三方主机这件事，从「参数传对了」变成结构上做不到。

token 只进 Authorization 头，永远不进 URL、不进候选、不进日志。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Mapping

import httpx

from .follow import DEFAULT_MAX_BYTES, FollowSourceError, plain_text, stable_id
from .follow_secrets import Credential, CredentialError
from .http import HttpRequest, HttpResponse, HttpTransport

#: 请求 Gofile 用的固定 UA。刻意不复用连接器的 `_headers()`——那里面可能有来源站
#: 的 Cookie，而这里是另一个主机。
from .user_agent import USER_AGENT

_FOLDER_PATH = re.compile(r"/d/([A-Za-z0-9_-]+)")
_LABELED_FOLDER = re.compile(
    r"gofile\s*\(([^)]+)\)\s*[-:：]?\s*https://(?:[^/]+\.)?gofile\.io/d/"
    r"([A-Za-z0-9_-]+)", re.IGNORECASE)


def folder_ids(links: list[str]) -> list[str]:
    """从一堆链接里挑出 Gofile 文件夹 id，保持出现顺序且去重。"""
    found: list[str] = []
    for link in links:
        try:
            parsed = urllib.parse.urlsplit(link)
        except ValueError:
            continue
        host = (parsed.hostname or "").casefold()
        matched = _FOLDER_PATH.fullmatch(parsed.path.rstrip("/"))
        if (host == "gofile.io" or host.endswith(".gofile.io")) and matched:
            found.append(matched.group(1))
    return list(dict.fromkeys(found))


def folder_labels(text: str | None) -> dict[str, str]:
    """Read optional human labels next to FANBOX Gofile links."""
    return {folder: label.strip() for label, folder in _LABELED_FOLDER.findall(text or "")
            if label.strip()}


class GofileExpander:
    """用用户自己的 API token 把 Gofile 文件夹展开成媒体条目。"""

    def __init__(self, transport: HttpTransport, *,
                 credential: Credential | None = None,
                 timeout: float = 15.0,
                 max_bytes: int = DEFAULT_MAX_BYTES,
                 max_items: int = 100):
        self.transport = transport
        self.credential = credential
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_items = max_items

    def expand(self, links: list[str], *,
               labels: Mapping[str, str] | None = None,
               ) -> tuple[dict[str, object], ...]:
        folders = folder_ids(links)
        if not folders or self.credential is None:
            return ()
        token, = self.credential.require("api_token")
        items: list[dict[str, object]] = []
        seen: set[str] = set()
        for folder in folders:
            payload = self._contents(folder, token)
            self._collect(payload, items, seen, folder,
                          str((labels or {}).get(folder) or ""))
        return tuple(items)

    def _contents(self, folder: str, token: str) -> dict:
        url = f"https://api.gofile.io/contents/{urllib.parse.quote(folder)}"
        response = self._get(url, token)
        payload = None
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # 非 JSON 的 401/403 仍按凭据拒绝处理；其他状态交给下面的通用错误。
            pass
        api_status = payload.get("status") if isinstance(payload, dict) else None
        if api_status == "error-notPremium":
            raise FollowSourceError(
                "Gofile 文件列表 API 需要 Premium 账户；token 有效，"
                "但当前账户套餐不支持此接口")
        if response.status in (401, 403) or api_status == "error-token":
            raise CredentialError("Gofile 拒绝了 API token")
        if response.status != 200:
            raise FollowSourceError(f"Gofile 返回 HTTP {response.status}")
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise FollowSourceError("Gofile 文件列表未取得")
        return payload

    def _get(self, url: str, token: str) -> HttpResponse:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        }
        try:
            response = self.transport(
                HttpRequest("GET", url, headers), self.timeout, self.max_bytes)
        except (OSError, httpx.HTTPError) as exc:
            raise FollowSourceError("Gofile 请求失败") from exc
        if len(response.body) > self.max_bytes:
            raise FollowSourceError("Gofile 响应超出大小上限")
        return response

    def _collect(self, payload: dict, items: list, seen: set,
                 folder: str, label: str) -> None:
        stack = [payload.get("data")]
        while stack and len(items) < self.max_items:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            children = node.get("children")
            if isinstance(children, dict):
                stack.extend(reversed(list(children.values())))
            elif isinstance(children, list):
                stack.extend(reversed(children))
            if str(node.get("type") or "").casefold() != "file":
                continue
            media_type = str(node.get("mimetype") or "").casefold()
            kind = ("video" if media_type.startswith("video/") else
                    "image" if media_type.startswith("image/") else "")
            link = str(node.get("link") or node.get("downloadLink") or "")
            if not kind or not link.startswith("https://") or link in seen:
                continue
            seen.add(link)
            items.append({
                "id": str(node.get("id") or stable_id(link)),
                "name": plain_text(str(node.get("name") or "")) or f"{kind} {len(items)+1}",
                "url": link,
                "thumb_url": str(node.get("thumbnail") or "") or None,
                "media_kind": kind,
                "size": node.get("size"),
                "resource_provider": "gofile",
                "resource_group": f"gofile:{folder}",
                "resource_group_label": label or f"Gofile · {folder}",
            })
