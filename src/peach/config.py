"""运行时配置：把设置层的合并结果投影成模块常量。

这里只做读取、合并与校验的投影，**不含任何一台具体机器的路径、主机名、账号名或局域网
地址**（ADR-0023 第一阶段）。默认值必须对一个全新用户成立；当前部署的那些值住在
`<Peach 数据目录>/config.toml` 里，由 `peach init --from-existing` 生成。优先级见
`settings_file`：环境变量 > 设置文件 > 内建默认。

下面的模块常量被脚本、web 层和托盘大量 import，名字保持稳定；变的只是它们的来源。
`tests/test_script_policy.py` 的路径闸门也认 `from peach.config import ...` 这个形状。
"""
import os
from dataclasses import dataclass
from pathlib import Path

from . import settings_file
from .platform import translate_roots
from .settings_file import PROJECT_ROOT, SettingsFileError

_SETTINGS = settings_file.active()

#: 设置文件的位置与状态。`CONFIGURED` 为 False 表示这台机器还没跑过 `peach init`：
#: 服务照常启动、`/healthz` 报 `configured=false`、页面提示去初始化，不因为缺目录崩。
#: `SETTINGS_ERROR` 非空表示文件在但读不出来（语法错、编码错、类型错），此时上面这份
#: 配置是退回的内建默认，CLI 必须拒绝提供服务而不是拿它去跑。
CONFIGURED: bool = _SETTINGS.configured
SETTINGS_ERROR: SettingsFileError | None = settings_file.error()

DATA_ROOT: Path = _SETTINGS.data_root
DATABASE_DIR: Path = _SETTINGS.directory("database")
DATABASE_PATH: Path = DATABASE_DIR / "ledger.db"
GENERATED_DIR: Path = _SETTINGS.directory("generated")
SOURCES_DIR: Path = _SETTINGS.directory("sources")
STATE_DIR: Path = _SETTINGS.directory("state")
SECRETS_DIR: Path = _SETTINGS.directory("secrets")
LOG_DIR: Path = _SETTINGS.directory("logs")
TOOLS_DIR: Path = _SETTINGS.directory("tools")
REVIEW_DIR: Path = _SETTINGS.directory("review")
FFMPEG_DIR: Path = TOOLS_DIR / "ffmpeg"
TRANSCODE_DIR: Path = GENERATED_DIR / "transcodes"
COVER_DIR: Path = GENERATED_DIR / "covers"
#: 迁移随代码走，不随数据走：它属于项目而不是某一份账本。
MIGRATIONS_DIR: Path = PROJECT_ROOT / "migrations"

# 共享副本只承担单写者复制传输，不是 Peach 直接运行的数据库。默认取 Peach 数据目录旁的
# `peach-sync`；macOS 上它是挂载来的 SMB 共享，坐标写在设置文件的 [replication] 里。
SHARED_DATA_ROOT: Path = _SETTINGS.shared_root
SHARED_DATABASE_PATH: Path = SHARED_DATA_ROOT / "database" / "ledger.db"
# macOS 重启后不会自动挂回共享，`plan()` 于是把本机日常状态判成 offline；托盘按下面的
# 坐标补挂一次。留空表示这台机器不挂共享——`mount.mount_share` 直接返回 False，不弹框。
SHARED_SMB_HOST: str = _SETTINGS.replication.smb_host
SHARED_SMB_SHARE: str = _SETTINGS.smb_share
SHARED_SMB_USER: str = _SETTINGS.replication.smb_user
#: 单写者复制的总开关（ADR-0023 第 3 阶段）。默认 False：多数部署只有一台机器。
#: 关闭时 `cli._build_sync` 不建观察器、托盘不装配 Ledger 菜单与 SMB 挂载，
#: 服务按独立写者跑；上面那几个 SHARED_* 坐标仍然解析得出，只是没人用。
REPLICATION_ENABLED: bool = _SETTINGS.replication.enabled
#: 追更凭据的共享副本根。复制关掉时是 None——没有第二台机器就没有「共享」，
#: 再往一个不存在的传输点写凭据只会凭空多一份明文。
SHARED_CREDENTIAL_ROOT: Path | None = SHARED_DATA_ROOT if REPLICATION_ENABLED else None

# 媒体来源只用账本口径（Windows 盘符）声明一次，本机挂载点由 platform 层翻译。
# 键是 ledger 的 `asset.location`，脱盘模式按来源逐个判定，不是全局开关。
LOCATION_ROOT_DECLARATIONS: dict[str, tuple[str, ...]] = {
    location: tuple(roots) for location, roots in _SETTINGS.locations.items()}
MEDIA_ROOT_DECLARATIONS: tuple[str, ...] = tuple(
    root for roots in LOCATION_ROOT_DECLARATIONS.values() for root in roots)

# 同时运行的两台机器必须用不同的 mDNS 名，否则互相抢占同一个 `.local`。
MDNS_NAME: str = _SETTINGS.server.mdns_name
MDNS_HOSTNAME: str = f"{MDNS_NAME}.local"
SERVE_HOST: str = _SETTINGS.server.host
SERVE_PORT: int = _SETTINGS.server.port
TUNNEL_ENABLED: bool = _SETTINGS.tunnel.enabled
CLOUDFLARED_BINARY: str = _SETTINGS.tunnel.binary
TUNNEL_MODE: str = _SETTINGS.tunnel.mode
TUNNEL_TOKEN: str = _SETTINGS.tunnel.token
TUNNEL_HOSTNAME: str = _SETTINGS.tunnel.hostname


@dataclass(frozen=True)
class PeachSettings:
    db_path: Path = DATABASE_PATH
    host: str = SERVE_HOST
    port: int = SERVE_PORT
    page_path: Path = PROJECT_ROOT / "web" / "index.html"
    vendor_path: Path = PROJECT_ROOT / "web" / "vendor"
    #: 界面标注工具的本机构建产物，由 `/dev/agentation.js` 提供。落在 `build/` 而不是 `web/`：
    #: 打包带走整个 `web/`，这份第三方工具不能跟着进独立包（docs/FRONTEND.md「界面标注」）。
    agentation_path: Path = PROJECT_ROOT / "build" / "agentation" / "peach-agentation.js"
    token: str = ""
    access_path: Path | None = None
    docs_enabled: bool = False
    mdns_enabled: bool = False
    mdns_name: str = "peach"
    mdns_port: int = 80
    mdns_address: str | None = None
    tls_enabled: bool = False
    #: Quick Tunnel 由应用进程管理，公网入口不能从默认值打开：这两项只由 `cli._serve`
    #: 从设置文件读出后注入，`create_app()` 自己构造的设置一律是关闭加空路径。
    tunnel_enabled: bool = False
    tunnel_binary: str = ""
    #: 公网入口的形态。`named` 走用户自建的命名隧道，只在源码开发环境成立；
    #: 令牌从设置文件注入，进程内只用于拼启动参数，不写日志也不回给页面。
    tunnel_mode: str = "quick"
    tunnel_token: str = ""
    tunnel_hostname: str = ""
    tunnel_standalone: bool = False
    tunnel_lan_address: str | None = None
    #: Tunnel origin 的实际端口；源码部署留 None 表示标准 HTTPS 端口。
    tunnel_origin_port: int | None = None
    #: 源码部署的 Tunnel origin 走本机 HTTPS，cloudflared 用这份项目 CA 校验它。
    tunnel_ca_path: Path = SECRETS_DIR / "tls" / "peach-local-ca.crt"
    tunnel_state_root: Path = STATE_DIR
    tunnel_log_root: Path = LOG_DIR
    #: 这台机器跑过 `peach init` 了没有。`/healthz` 与首页据此决定是否弹首次运行提示。
    configured: bool = CONFIGURED
    # reader 只从 writer 的严格 CA HTTPS 镜像复核 JSON；不复制候选 CSV，更不放宽写入闸门。
    # 空串表示本机不做镜像：单机用户没有第二台机器，这也是内建默认。
    review_writer_origin: str = os.environ.get(
        "PEACH_REVIEW_WRITER_ORIGIN", _SETTINGS.server.review_writer_origin)
    review_writer_ca: Path = SECRETS_DIR / "tls" / "peach-local-ca.crt"
    review_mirror_cache: Path = REVIEW_DIR / "writer-review.json"
    # 某些 macOS LaunchAgent 的 Python 拿不到 Local Network 权限，只能经本机代理兜底。
    review_writer_proxy: str = os.environ.get(
        "PEACH_REVIEW_WRITER_PROXY", _SETTINGS.server.review_writer_proxy)
    # 本机挂载不到的来源不进授权列表，对应资产按「脱盘」处理而不是报错。
    allowed_media_roots: tuple[Path, ...] = translate_roots(MEDIA_ROOT_DECLARATIONS)
    snapshot_root: Path = GENERATED_DIR / "snapshots"
    poster_root: Path = GENERATED_DIR / "posters"
    # 时间轴预览的接触印相，由采集任务铺，悬停时整张取回来按格子换位置。
    timeline_root: Path = GENERATED_DIR / "timeline"
    avatar_root: Path = GENERATED_DIR / "avatars"
    logo_root: Path = GENERATED_DIR / "logos"
    # 图片资产的缓存缩略图。云盘原图一张就有几 MB，回源一次之后图片墙只读这里。
    photo_root: Path = GENERATED_DIR / "photo-thumbs"
    # 官方封套按番号存一份原图；4:3 与 16:9 两种版式共用同一文件，靠 CSS 取景。
    cover_root: Path = COVER_DIR
    # 推送发现的共享密钥住在这里。它和访问口令是两件事，轮换一份不影响另一份。
    secrets_root: Path = SECRETS_DIR
    ffmpeg_root: Path = FFMPEG_DIR
    transcode_root: Path = TRANSCODE_DIR
    stream_root: Path = GENERATED_DIR / "stream-segments"
    # 复核候选 CSV 的目录。走 settings 而不是模块常量，测试才不会读到真实的 generated 目录。
    candidate_root: Path = GENERATED_DIR
    taste_history_store: Path = SOURCES_DIR / "taste-history" / "history.sqlite"
    taste_history_import_root: Path = SOURCES_DIR / "taste-history" / "imports"
    taste_history_output_root: Path = REVIEW_DIR / "taste-history"
    taste_history_manifest: Path = STATE_DIR / "taste-history" / "manifest.json"
    # 自动追更频率是本机运行偏好，不属于 ledger 真相。
    follow_state_root: Path = STATE_DIR
    # 人物页外部入口的开关与镜像域名，同样是本机偏好。
    entry_links_root: Path = STATE_DIR
