"""单资产、限时的外部播放器访问票据。签名密钥只存在于服务进程内。"""
from __future__ import annotations

import hashlib
import hmac
import time

TTL_SECONDS = 6 * 60 * 60


def issue(key: bytes, asset_id: int | str, *, origin: str = '', now: float | None = None) -> str:
    expires = int(time.time() if now is None else now) + TTL_SECONDS
    message = f"{asset_id}.{expires}.{origin}"
    signature = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def valid(key: bytes, asset_id: int | str, ticket: str, *, origin: str = '', now: float | None = None) -> bool:
    try:
        expires, signature = ticket.split(".")
        current = time.time() if now is None else now
        if not current < int(expires) <= current + TTL_SECONDS:
            return False
        expected = hmac.new(key, f"{asset_id}.{expires}.{origin}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except (ValueError, TypeError):
        return False
