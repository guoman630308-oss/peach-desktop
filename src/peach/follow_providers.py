"""追更来源的单点登记表。

新增一个站点只改这张表。各模块（CLI 的 URL 模板、连接器的语义、媒体代理的主机白名单、
选主条目的优先级、界面的显示名与能力集合）都从这里派生自己那份投影：少登记一项不会
报错，只会在某个页面上少一行、某个媒体代理拒绝一条链接，所以只在一处登记。

**不包含凭据。**哪些字段可以跨机同步由 `follow_secrets.CREDENTIAL_GUIDE` 逐字段声明，
`SYNCABLE_FIELDS` 已经是它的派生视图。那是安全语义，收进这张通用表只会让它更容易
被顺手改错——新增来源时作者必须在凭据表里单独表态，这个摩擦是故意的。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    """一个追更来源在各层的全部登记信息。

    `source_url` 为 None 表示它不是追更来源，只是会出现在界面上的媒体来源（如 Gofile）。
    `hosts` 为空表示它的媒体不经本地代理取回（官方三家走各自的详情接口）。
    """

    key: str
    label: str
    source_url: str | None = None
    #: `work` 是每条一个独立作品，`release` 是同一作品的历次发布。连接器与解析结果
    #: 都从这里读，不另外声明。
    semantics: str = "work"
    hosts: tuple[str, ...] = ()
    priority: int = 99
    backfill: bool = False
    official_identity: bool = False
    #: 用户明确点名要隐藏的既有条目（站内 id）。只影响浏览面，不删 ledger 行。
    excluded_external_ids: tuple[str, ...] = ()
    #: 粘一条链接时，哪些站点主机认成这个来源。子域也算（`x.fanbox.cc`、
    #: `api.rule34.xxx`），所以这里只写注册域那一截。**不是** `hosts`：那是媒体
    #: 代理的白名单，值可能完全不同（paheal 的站点是 `rule34.paheal.net`，
    #: 媒体在 `paheal-cdn.net`）。
    url_hosts: tuple[str, ...] = ()
    #: 这个来源的每个条目都是一次独立发布，即使标题相同。
    #: F95 的线程标题只是容器名，每个带资源的楼层各自成组。
    release_key_per_post: bool = False
    #: 站内 id 是全站递增的上传序号，同一作者一次连传的几条 id 相距很近。
    #: 关注页据此把同一批导出的短片认成一包（ADR-0045）。
    sequential_upload_ids: bool = False
    #: 直链媒体可以落在任意公网主机。给帖子里贴的第三方图床用：图站有几十家、还在
    #: 换，白名单追不上；而这类媒体本来就不带凭据取，代理到哪个公网主机都泄露不了
    #: 什么。安全边界改由 `follow_stream` 守：明文 http、IP 字面量、本机与局域网专用
    #: 名字、解析到内网地址的主机仍然拒收。与 `hosts` 互斥——两个都写会让读者以为
    #: 白名单还在起作用。
    public_media_hosts: bool = False

    def __post_init__(self) -> None:
        if self.semantics not in ("work", "release"):
            raise ValueError(f"{self.key} 的 semantics 只能是 work 或 release")
        if self.public_media_hosts and self.hosts:
            raise ValueError(f"{self.key} 放行任意公网主机时不能再写 hosts 白名单")
        if self.source_url and not self.url_hosts:
            raise ValueError(f"{self.key} 是追更来源，必须登记至少一个 url_hosts")
        if self.source_url and "{ref}" not in self.source_url:
            raise ValueError(f"{self.key} 的 source_url 必须能填入 {{ref}}")


#: 唯一登记处。priority 越小越优先做主条目；未列出的来源排在最后。
PROVIDERS: dict[str, ProviderSpec] = {
    spec.key: spec
    for spec in (
        # 官方渠道：只读公开免费发布，身份可信，所以优先做主条目；媒体走各自详情接口，
        # 不经本地代理，因此没有 hosts。fanbox 的站点分页是 `post.paginateCreator`
        # 给出的游标页清单，能稳定走完整个创作者，所以同样可回填。
        ProviderSpec("fanbox", "FANBOX", source_url="https://{ref}.fanbox.cc/",
                     url_hosts=("fanbox.cc",), priority=1, official_identity=True,
                     backfill=True),
        ProviderSpec("subscribestar", "SubscribeStar", source_url="https://{ref}",
                     url_hosts=("subscribestar.adult", "subscribestar.com"),
                     priority=2, official_identity=True),
        ProviderSpec("patreon", "Patreon", source_url="https://www.patreon.com/cw/{ref}",
                     url_hosts=("patreon.com",), priority=3, official_identity=True),
        # 归档站：同一套代码的姊妹站，支持真实历史分页所以可回填。
        ProviderSpec("kemono", "Kemono", source_url="https://kemono.cr/{ref}",
                     hosts=("kemono.cr",), url_hosts=("kemono.cr",),
                     priority=10, backfill=True),
        ProviderSpec("pawchive", "Pawchive", source_url="https://pawchive.pw/{ref}",
                     hosts=("pawchive.pw",), url_hosts=("pawchive.pw",),
                     priority=15, backfill=True),
        ProviderSpec("coomer", "Coomer", source_url="https://coomer.st/{ref}",
                     hosts=("coomer.st",), url_hosts=("coomer.st",),
                     priority=20, backfill=True),
        # 标签／模特站。
        # `excluded_external_ids` 是用户明确点名的那条，它没探过详情页、拿不到署名。
        # 同类条目由连接器按详情页的画面作者数拦截；这一条只管让它从浏览面消失。
        ProviderSpec("rule34video", "Rule34Video",
                     source_url="https://rule34video.com/models/{ref}/",
                     # 正片不在站内：详情页给的 `/get_file/…` 会 302 到 `*.boomio-cdn.com`
                     # （2026-09-04 实测 eu-cdn05／06／08／11-prem 四个节点，最终一跳才是
                     # 206 `video/mp4`）。少了这个后缀，媒体代理会在跳出白名单那一步拒收，
                     # 整站的视频一条都放不出来。
                     hosts=("rule34video.com", "boomio-cdn.com"),
                     url_hosts=("rule34video.com",),
                     priority=30, backfill=True, sequential_upload_ids=True,
                     excluded_external_ids=("4533145",)),
        ProviderSpec("rule34xxx", "Rule34.xxx",
                     source_url="https://rule34.xxx/index.php?page=post&s=list&tags={ref}",
                     hosts=("rule34.xxx",), url_hosts=("rule34.xxx",),
                     priority=40, backfill=True),
        ProviderSpec("rule34paheal", "Rule34 Paheal",
                     source_url="https://rule34.paheal.net/post/list/{ref}/1",
                     hosts=("paheal.net", "paheal-cdn.net"),
                     url_hosts=("rule34.paheal.net",), priority=45, backfill=True),
        # 论坛：每个条目是同一作品的一次发布，不是独立作品。
        ProviderSpec("f95zone", "F95zone", source_url="https://f95zone.to/threads/{ref}/",
                     semantics="release", hosts=("f95zone.to",), url_hosts=("f95zone.to",),
                     priority=50, release_key_per_post=True),
        # 帖子里的图挂在站方图床 `simp4/5/6.cuckcapital.cr` 和各家第三方图站上（2026-09-08
        # 实测原图对无 cookie 请求直接 200），图站名单没法穷举，所以不写白名单而是放行
        # 任意公网主机、不带凭据取。论坛附件要 cookie，连接器只登记不当媒体，代理也就
        # 不会替浏览器带着登录态去取。
        ProviderSpec("simpcity", "SimpCity", source_url="https://simpcity.cr/threads/{ref}/",
                     semantics="release", public_media_hosts=True,
                     url_hosts=("simpcity.cr",), priority=60, release_key_per_post=True),
        # 文件站：不是追更来源，只作为媒体来源出现在界面上，所以没有 source_url。
        ProviderSpec("gofile", "Gofile"),
    )
}


def labels() -> dict[str, str]:
    """界面上给每个来源的短名。没登记的 provider 直接显示原名。"""
    return {key: spec.label for key, spec in PROVIDERS.items()}


def hosts() -> dict[str, tuple[str, ...]]:
    """媒体代理允许的主机；不在表里也没登记 `public_media_hosts` 的 provider 一律拒绝。"""
    return {key: spec.hosts for key, spec in PROVIDERS.items() if spec.hosts}


def public_media_hosts() -> frozenset[str]:
    """直链媒体放行任意公网主机（不带凭据取）的来源。"""
    return frozenset(key for key, spec in PROVIDERS.items() if spec.public_media_hosts)


def priorities() -> dict[str, int]:
    """同一 `release_key` 下选主条目时的来源优先级；越小越优先。"""
    return {key: spec.priority for key, spec in PROVIDERS.items() if spec.source_url}


def backfill_providers() -> frozenset[str]:
    """支持真实历史分页、因此可以「抓更早一页」的来源。"""
    return frozenset(key for key, spec in PROVIDERS.items() if spec.backfill)


def official_identity_providers() -> frozenset[str]:
    """作者显示名与头像可信的官方渠道；归档站只作回退。"""
    return frozenset(key for key, spec in PROVIDERS.items() if spec.official_identity)


def url_hosts() -> dict[str, str]:
    """站点主机 → 来源键。解析粘进来的链接时用它替代一串 if/elif。

    键只写注册域那一截；子域由 `provider_for_host` 按后缀匹配，所以
    `creator.fanbox.cc` 和 `api.rule34.xxx` 都不必单独登记。
    """
    return {host: key for key, spec in PROVIDERS.items() for host in spec.url_hosts}


def provider_for_host(host: str) -> str:
    """这个主机属于哪个来源。认不出来返回空串。

    先精确匹配，再按最长后缀匹配——后缀更长的登记更具体，必须赢：
    `rule34.paheal.net` 不能被将来某个 `paheal.net` 登记抢走。
    """
    bare = str(host or "").strip().lower().removeprefix("www.")
    table = url_hosts()
    if bare in table:
        return table[bare]
    matched = [registered for registered in table
               if bare.endswith(f".{registered}")]
    return table[max(matched, key=len)] if matched else ""


def release_key_per_post() -> frozenset[str]:
    """每个条目都是一次独立发布的来源；同名条目不合并。"""
    return frozenset(key for key, spec in PROVIDERS.items()
                     if spec.release_key_per_post)


def sequential_upload_providers() -> frozenset[str]:
    """站内 id 是全站递增上传序号的来源。"""
    return frozenset(key for key, spec in PROVIDERS.items()
                     if spec.sequential_upload_ids)


def excluded_external_ids() -> dict[str, frozenset[str]]:
    """按来源列出要从浏览面隐藏的既有条目。没有要隐藏的来源不出现在结果里。"""
    return {key: frozenset(spec.excluded_external_ids)
            for key, spec in PROVIDERS.items() if spec.excluded_external_ids}
