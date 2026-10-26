"""挂载共享盘。

从 `platform` 拆出：那个模块的职责是盘符↔挂载点的路径翻译（ARCHITECTURE 里说的
「唯一翻译层」），而这里做的是 NetFS 调用和钥匙串查询，两件事只是都碰 macOS。

钥匙串检查在挂载之前先做：没有记录时直接快速失败，不要让 NetFS 弹出阻塞式认证框——
服务是 launchd 起的，那个框没人能点。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from typing import Callable
from urllib.parse import quote

LOGGER = logging.getLogger(__name__)


#: 挂载超时。这是兜底，不是常规耗时——本机实测一次成功挂载 0.6 秒。
MOUNT_TIMEOUT_SECONDS = 15.0

#: 钥匙串里 SMB 记录的协议标识。四字符定长，末尾那个空格是格式的一部分。
_SMB_PROTOCOL = "smb "


def share_url(host: str, share: str, user: str = "") -> str:
    """SMB URL。每一段都做百分号编码，引号和反斜杠因此不可能落进 AppleScript 字面量。"""
    credentials = f"{quote(user, safe='')}@" if user else ""
    return f"smb://{credentials}{quote(host, safe='')}/{quote(share, safe='')}"


def mount_share_command(url: str) -> list[str] | None:
    """挂上一个 SMB 共享的 argv；本平台没有这条路径时返回 None。

    走 `osascript` 的 `mount volume` 而不是 `open smb://`：两者都经 NetFS，会自己在
    `/Volumes` 下建挂载点并取钥匙串里的凭据（`/Volumes` 归 root，脚本 mkdir 不了，
    而 `mount_smbfs` 又要求挂载点已经存在），区别是 `open` 会顺带弹一个 Finder 窗口。
    2026-08-27 本机实测：卸载后跑这条命令 0.6 秒挂回来，无窗口，返回 0。
    """
    if sys.platform != "darwin":
        return None
    return ["osascript", "-e", f'mount volume "{url}"']


def share_credentials_command(host: str, user: str) -> list[str]:
    """查钥匙串里有没有这台主机的 SMB 记录的 argv。

    刻意不带 `-w`：只判断记录在不在，密码不会被读出来，也就不会经过本进程。
    """
    return [
        "security", "find-internet-password",
        "-s", host, "-a", user, "-r", _SMB_PROTOCOL,
    ]


def share_credentials_present(
    host: str,
    user: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """钥匙串里有没有这台主机、这个账号的 SMB 记录。

    2026-08-27 实测：没有记录时 NetFS 不会失败，而是拉起 NetAuthAgent 弹一个认证框
    然后一直等——托盘那次点击既卡到超时，又在用户没要求的时候把密码框推到屏幕上。
    先查一次就把这条路径变成一次快速失败。记录按**主机名**存：同一台机器的 IP 和
    mDNS 名是两条不同的记录，换一个主机名就要重新记一次密码。
    """
    try:
        result = run(share_credentials_command(host, user),
                     capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.warning("查钥匙串失败：%s", exc)
        return False
    return result.returncode == 0


def mount_share(
    host: str,
    share: str,
    user: str = "",
    *,
    timeout: float = MOUNT_TIMEOUT_SECONDS,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """挂一次共享，成败只用返回值表示。

    任何失败都不抛：调用方是「挂不上就照旧降级」的那条路径，异常在那里只会把一条
    清楚的消息换成一次崩溃。失败原因写日志，否则事后完全无从查起。
    """
    command = mount_share_command(share_url(host, share, user))
    if command is None:
        return False
    if user and not share_credentials_present(host, user, run=run):
        LOGGER.warning("钥匙串里没有 %s@%s 的 SMB 记录，不去弹认证框", user, host)
        return False
    try:
        result = run(command, capture_output=True, text=True,
                     timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.warning("挂载 //%s/%s 失败：%s", host, share, exc)
        return False
    if result.returncode:
        LOGGER.warning("挂载 //%s/%s 失败：%s", host, share,
                       (result.stderr or result.stdout or "").strip())
        return False
    return True
