"""局域网闸门的口令：生成、存放、取用。

口令只存 `<Peach 数据目录>/secrets/auth-token` 这一个地方。不写 `config.toml`——那份文件是可以贴进
issue 的本机坐标；也不靠 `peach serve --token <口令>` 长期传递——同一台机器上任何进程都能从
进程表里读到别人的命令行。`--token` 与 `PEACH_TOKEN` 仍然认，用来做一次性覆盖和测试注入。

两台机器互相取复核结果时发的是**自己**的口令（`peach.api` 里 `ReviewMirror(token=...)`），
所以 writer 与 reader 必须共用同一份口令文件，复制过去即可。

`peach serve` 绑非回环地址却取不到口令时拒绝启动，判定在 `peach.cli`。那里是硬拒绝而不是
告警：托盘在后台起服务，告警没有人会看见。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

#: 口令文件名，固定在设置文件声明的 `secrets` 目录下。
TOKEN_FILENAME = "auth-token"

#: 32 字节即 256 bit。`token_urlsafe` 出 43 个 URL 安全字符，可以直接进地址栏和请求头。
TOKEN_BYTES = 32


def token_path(secrets_dir: Path) -> Path:
    return Path(secrets_dir) / TOKEN_FILENAME


def read_token(secrets_dir: Path) -> str:
    """读已有口令。文件不在、读不出、内容为空都返回空串，由调用方当成「没有口令」。"""
    try:
        return token_path(secrets_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_token(secrets_dir: Path) -> tuple[str, bool]:
    """取口令，没有就生成一份。返回 (口令, 这次是不是新生成的)。"""
    existing = read_token(secrets_dir)
    if existing:
        return existing, False
    return write_token(secrets_dir), True


def write_token(secrets_dir: Path) -> str:
    """生成新口令并覆盖写入，返回新口令。旧口令签发的 cookie 立即失效。"""
    path = token_path(secrets_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    path.write_text(token + "\n", encoding="utf-8")
    if os.name != "nt":
        # Windows 的 chmod 只动只读位，收权限靠 `peach-data` 本身在用户目录下。
        path.chmod(0o600)
    return token


def resolve_token(explicit: str, secrets_dir: Path) -> str:
    """取用顺序：`--token` > `PEACH_TOKEN` > 口令文件。"""
    return (explicit or os.environ.get("PEACH_TOKEN", "") or read_token(secrets_dir)).strip()
