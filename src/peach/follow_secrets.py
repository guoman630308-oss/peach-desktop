"""追更来源的本机凭据读取。

凭据只从 `peach-data/secrets/follow/<provider>.json` 读，永不进 Git、URL、日志或 ledger。
本模块不打印凭据值，`describe()` 只报告字段是否存在。
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .fsutil import atomic_write_text
from .platform import root_online


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credential:
    provider: str
    values: dict[str, str]

    def require(self, *names: str) -> tuple[str, ...]:
        missing = [name for name in names if not self.values.get(name)]
        if missing:
            raise CredentialError(
                f"{self.provider} 凭据缺少字段：{', '.join(missing)}"
            )
        return tuple(str(self.values[name]) for name in names)


class CredentialStore:
    """按 provider 读取凭据文件；缺失是正常状态，不是错误。

    凭据默认只在本机。`shared_root` 给出时，**只有被声明为可同步的字段**会额外写一份到
    共享副本，并在本机缺失时从那里回填。哪些字段可同步由 `syncable_fields` 显式给出——
    绝不按字段名猜：今天 `api_key` 能同步、`cookie` 不能，明天新增一个 `session_token`
    就会落到错误的一侧。声明式意味着新增来源时作者必须表态。
    """

    def __init__(self, secrets_root: Path, shared_root: Path | None = None,
                 syncable_fields: dict[str, tuple[str, ...]] | None = None):
        self.root = Path(secrets_root) / "follow"
        self.shared_base = Path(shared_root) if shared_root is not None else None
        self.shared_root = (self.shared_base / "secrets" / "follow"
                            if self.shared_base is not None else None)
        self.syncable_fields = syncable_fields or {}

    def path_for(self, provider: str) -> Path:
        if not provider or "/" in provider or "\\" in provider or provider.startswith("."):
            raise CredentialError("provider 名称非法")
        return self.root / f"{provider}.json"

    def syncable(self, provider: str) -> tuple[str, ...]:
        return tuple(self.syncable_fields.get(provider, ()))

    def shared_path_for(self, provider: str) -> Path | None:
        if self.shared_root is None:
            return None
        self.path_for(provider)          # 复用同一套名称校验
        return self.shared_root / f"{provider}.json"

    def shared_online(self) -> bool | None:
        """共享根现在能不能读。没有配置共享时返回 `None`（不适用），不是 `False`。

        「不适用」和「不可达」必须分开：前者不需要告诉用户任何事，后者意味着这次
        撤销或写入只落在本机，另一台上那份还在。
        """
        if self.shared_base is None:
            return None
        return root_online(self.shared_base)

    def _read(self, path: Path, provider: str) -> dict[str, str]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CredentialError(f"{provider} 凭据文件无法解析") from exc
        if not isinstance(payload, dict):
            raise CredentialError(f"{provider} 凭据文件必须是 JSON 对象")
        return {
            str(key): str(value) for key, value in payload.items()
            if value is not None and not isinstance(value, (dict, list))
        }

    def _shared_values(self, provider: str) -> dict[str, str]:
        """共享副本里的可同步字段。盘不在或读不动都当作没有，不让它挡住本机凭据。"""
        path = self.shared_path_for(provider)
        if path is None or not path.is_file():
            return {}
        allowed = set(self.syncable(provider))
        try:
            return {k: v for k, v in self._read(path, provider).items() if k in allowed}
        except (CredentialError, OSError):
            return {}

    def _local_values(self, provider: str) -> dict[str, str]:
        path = self.path_for(provider)
        return self._read(path, provider) if path.is_file() else {}

    def load(self, provider: str) -> Credential | None:
        # 本机优先：共享副本只补本机没有的可同步字段，不覆盖本机已填的值。
        values = {**self._shared_values(provider), **self._local_values(provider)}
        return Credential(provider, values) if values else None

    def describe(self, provider: str) -> dict[str, object]:
        """只报告存在性与权限，绝不返回凭据值。

        **必须和 `load()` 看同一份事实。** 只看本机文件不行：`load()` 会从共享
        副本回填，于是撤销本机那份之后界面报「未配置」、连接器却还拿着共享里那把 key
        在认证。状态和实际用的凭据不一致，比撤不掉更糟——用户会以为已经撤了。
        所以这里也走 `load()` 的合并口径，并额外分出 `shared_fields`：用户得知道
        哪几个字段是从共享回填的，否则不知道该去哪台机器上撤。

        `world_readable` 只在 POSIX 上有意义：NTFS 的访问控制走 ACL，`st_mode` 里的
        组/其他读位是 Python 合成出来的常量，恒为真——拿它当判据会在 Windows 上
        对每个凭据文件都报「权限过宽」。那里报 `None`（未知），不假装知道。
        """
        path = self.path_for(provider)
        local = self._local_values(provider)
        shared = self._shared_values(provider)
        values = {**shared, **local}
        if not values:
            return {"provider": provider, "present": False, "fields": [],
                    "local_fields": [], "shared_fields": [], "world_readable": False}
        return {
            "provider": provider,
            "present": True,
            "fields": sorted(values),
            "local_fields": sorted(local),
            "shared_fields": sorted(name for name in shared if name not in local),
            "world_readable": self._world_readable(path) if path.is_file() else None,
        }

    def save(self, provider: str, values: dict[str, str]) -> None:
        """整份覆盖写本机这一份。只写本机：共享副本由声明了可同步字段的调用方另写。

        先写同目录的临时文件再替换，写到一半断电不会留下半截 JSON；POSIX 上收紧到 0600。
        """
        payload = {str(key): str(value) for key, value in values.items() if value not in (None, "")}
        atomic_write_text(self.path_for(provider),
                          json.dumps(payload, ensure_ascii=False, indent=2) + "\n", mode=0o600)

    def clear(self, provider: str) -> dict[str, object]:
        """撤销一份凭据：本机和共享副本一起删。

        只删本机等于没撤。`load()` 会从共享副本重建，而共享副本还会跟着 peach-sync
        传到另一台——结果是**在任何一台上都撤不掉**。给得出「配上」就要给得出「撤掉」，
        同步不能把这个保证破坏掉。

        共享盘不可达时如实报 `shared="offline"`，绝不静默跳过：那等于让用户以为撤了
        其实没撤，等盘回来还会把 key 回填回来。
        """
        path = self.path_for(provider)
        local_removed = path.is_file()
        path.unlink(missing_ok=True)
        shared_path = self.shared_path_for(provider)
        if shared_path is None:
            return {"local_removed": local_removed, "shared": "none"}
        if not self.shared_online():
            return {"local_removed": local_removed, "shared": "offline"}
        try:
            if not shared_path.is_file():
                return {"local_removed": local_removed, "shared": "absent"}
            shared_path.unlink()
        except OSError:
            return {"local_removed": local_removed, "shared": "offline"}
        return {"local_removed": local_removed, "shared": "removed"}

    @staticmethod
    def _world_readable(path: Path) -> bool | None:
        if os.name == "nt":
            return None
        mode = stat.S_IMODE(os.stat(path).st_mode)
        return bool(mode & (stat.S_IRGRP | stat.S_IROTH))


#: 每个来源要不要凭据、要哪些字段、去哪里拿。这张表和上面的读取实现放在一起：
#: 「哪些字段可以跨机同步」是凭据自己的安全语义，散在 Web 层就会出现命令行与
#: 网页各建一个仓库、同一份凭据在一边在、在另一边显示「未配置」。
CREDENTIAL_GUIDE: dict[str, dict] = {
    "fanbox": {
        "requirement": "optional",
        "fields": ["cookie"],
        # FANBOX Cookie 绑定浏览器会话与风控环境，不跨机同步。
        "syncable": [],
        "why": "公开列表不需要登录；帖子详情被 FANBOX 验证页拦住时，可用浏览器会话取得正文、多图和外部资源链接。",
        "where": "https://www.fanbox.cc/",
        "howto": "登录 FANBOX 后，从一次成功的 api.fanbox.cc/post.info 请求复制整条 Cookie 请求头。",
    },
    "gofile": {
        "requirement": "optional",
        "fields": ["api_token"],
        "syncable": [],
        "why": "用于展开 Gofile 文件页，取得其中的图片和视频列表；Gofile 当前要求 Premium 才能读取 contents API，不配置仍会保留文件页链接。",
        "where": "https://gofile.io/myprofile",
        "howto": "登录 Premium Gofile 账户后，在个人资料页复制 API token。",
    },
    "kemono": {"requirement": "none"},
    "coomer": {"requirement": "none"},
    "pawchive": {"requirement": "none"},
    "rule34video": {"requirement": "none"},
    "rule34xxx": {
        "requirement": "required",
        "fields": ["user_id", "api_key"],
        # 账号级、与机器无关，用户明确要求跨机同步。
        "syncable": ["user_id", "api_key"],
        "why": "网页版挂了 Cloudflare 验证码，Peach 不绕验证码，只能走官方 API。",
        "where": "https://rule34.xxx/index.php?page=account&s=options",
        "howto": "登录后在账号设置页生成 API key，把 user_id 和 api_key 写进凭据文件。",
    },
    "f95zone": {
        "requirement": "optional",
        "fields": ["cookie"],
        # cookie 绑会话与客户端 IP，同步到另一台大概率直接失效——不同步。
        "syncable": [],
        "why": "发现更新不需要登录；只有取附件和 masked 下载链接才需要会话。",
        "where": "https://f95zone.to/",
        "howto": "登录后从浏览器复制整条 Cookie 请求头，写进凭据文件的 cookie 字段。",
    },
    "simpcity": {
        "requirement": "required",
        "fields": ["cookie"],
        # 登录会话绑浏览器与 DDoS-Guard 的放行记录，同步到另一台大概率直接失效——不同步。
        "syncable": [],
        "why": "站点不让游客读帖，检查更新必须带登录会话；前面的 DDoS-Guard 对标准桌面浏览器"
               "标识不出质询，Peach 也不解任何质询，cookie 失效就原样报错。",
        "where": "https://simpcity.cr/",
        "howto": "登录后从浏览器复制整条 Cookie 请求头，写进凭据文件的 cookie 字段。",
    },
}


#: 哪些字段可以跨机同步，逐 provider 逐字段声明。绝不按字段名猜——今天 `api_key`
#: 能同步、`cookie` 不能，明天新增一个 `session_token` 就会落到错误的一侧。
SYNCABLE_FIELDS: dict[str, tuple[str, ...]] = {
    provider: tuple(guide.get("syncable", ()))
    for provider, guide in CREDENTIAL_GUIDE.items()
}


def credential_store_for(secrets_root: Path, *,
                         shared_root: Path | None = None) -> CredentialStore:
    """构造凭据仓库的唯一入口。

    `shared_root` 与 `syncable_fields` 必须处处一致。Web 与发现各自
    `CredentialStore(...)` 的话，只有其中一份带上共享根和可同步字段声明——表现是
    在另一台机器上配好的 rule34.xxx key 网页里能用、发现那层却报缺凭据。
    哪一层都不该自己决定这件事，所以只留这一个构造函数。
    """
    return CredentialStore(secrets_root, shared_root=shared_root,
                           syncable_fields=SYNCABLE_FIELDS)
