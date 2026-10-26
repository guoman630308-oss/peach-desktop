"""本机访问密码与浏览器会话；内部 API 凭据由 auth 管理。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

from itsdangerous import BadData, URLSafeSerializer

COOKIE = "peach_session"


def load(path: Path | None) -> dict:
    """缺少策略的已有部署继续要求原访问口令，损坏的策略拒绝访问。"""
    if path is None:
        return {"mode": "legacy", "revision": "legacy"}
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
        if (policy["mode"] not in {"open", "password"}
                or not isinstance(policy["revision"], str)
                or not isinstance(policy["key"], str)):
            raise ValueError("invalid access policy")
        if policy["mode"] == "password" and not all(isinstance(policy[k], str) for k in ("salt", "hash")):
            raise ValueError("invalid password hash")
        return policy
    except FileNotFoundError:
        return {"mode": "legacy", "revision": "legacy"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"mode": "locked", "revision": "locked"}


def validate_password(password: str, confirmation: str) -> None:
    if password != confirmation:
        raise ValueError("两次输入的密码不一致")
    if password and not 8 <= len(password) <= 256:
        raise ValueError("访问密码需为 8–256 个字符")


def _digest(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                          n=32768, r=8, p=1, maxmem=67108864, dklen=32).hex()


def verify(policy: dict, password: str, legacy_token: str = "") -> bool:
    if len(password) > 256:
        return False
    if policy["mode"] == "legacy":
        return bool(legacy_token) and hmac.compare_digest(password.encode(), legacy_token.encode())
    if policy["mode"] != "password":
        return False
    try:
        return hmac.compare_digest(_digest(password, policy["salt"]), policy["hash"])
    except (ValueError, KeyError):
        return False


def save(path: Path, password: str) -> dict:
    """原子替换策略。每次更改都会撤销已签发的浏览器会话。"""
    validate_password(password, password)
    policy = {"mode": "password" if password else "open",
              "revision": secrets.token_hex(16), "key": secrets.token_urlsafe(32)}
    if password:
        policy["salt"] = secrets.token_hex(16)
        policy["hash"] = _digest(password, policy["salt"])
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".access-", suffix=".json")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(policy, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return policy


def public(policy: dict) -> dict:
    return {"mode": policy["mode"], "revision": policy["revision"]}


def issue(policy: dict, days: int) -> tuple[str, int | None]:
    if type(days) is not int or not 0 <= days <= 365:
        raise ValueError("请选择有效的保持登录时间")
    seconds = days * 86400 if days else 43200
    value = URLSafeSerializer(policy["key"], salt="peach-browser-access").dumps(
        {"revision": policy["revision"], "expires": int(time.time()) + seconds})
    return value, seconds if days else None


def valid_session(policy: dict, value: str) -> bool:
    if policy["mode"] not in {"password", "legacy"} or len(value) > 2048:
        return False
    try:
        payload = URLSafeSerializer(policy["key"], salt="peach-browser-access").loads(value)
        return (payload["revision"] == policy["revision"]
                and isinstance(payload["expires"], int) and payload["expires"] > time.time())
    except (BadData, KeyError, TypeError):
        return False
