"""追更的共享错误类型与工具。

取回由站点连接器（`follow_sources.py`）负责：七个实际来源无一提供 feed，RSS/Atom
适配层这条路线不成立（ADR-0019）。这里只留连接器和存储层真正在用的四样东西：
错误类型、正文净化、稳定 id、只写一次的证据落盘。

适配器只负责发现候选，不写 ledger 真相；调用方必须落盘原始证据，并让归一化后的
条目走复核边界。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from bs4 import BeautifulSoup


DEFAULT_MAX_BYTES = 5 * 1024 * 1024


class FollowSourceError(RuntimeError):
    pass


class FollowHistoryEnd(FollowSourceError):
    """A valid backfill response that means there are no older items."""


class FollowSourceRateLimited(FollowSourceError):
    """来源明确说了请求太频繁。被限的是整站，同一站剩下的来源这一轮也不该再请求。

    `retry_after` 是来源给的等待秒数，没给就是 None，由冷却方按默认时长处理。
    """

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def plain_text(value: str | None) -> str | None:
    """把站点返回的 HTML 片段压成一行纯文本。"""
    if not value:
        return None
    cleaned = BeautifulSoup(value, "html.parser").get_text(" ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def stable_id(*parts: str | None) -> str:
    """来源没给 id 时，用内容本身算一个稳定的替代 id。"""
    material = "\n".join(part or "" for part in parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def write_immutable(path: Path, payload: bytes) -> None:
    """原始证据只写一次。同名不同内容说明取证边界被破坏，直接报错。"""
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise FollowSourceError("immutable follow snapshot collision")
