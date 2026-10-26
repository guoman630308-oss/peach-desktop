# Docker 部署

Peach 的 Docker 镜像用于单人 VPS 与 NAS，通过浏览器管理，镜像仓库为 [longmeidao/peach](https://hub.docker.com/r/longmeidao/peach)。`edge` 随 `master` 分支构建更新，版本号标签对应发布提交。当前镜像目标为 `linux/amd64`；ARM NAS 尚未验收。2026-10-15 在 WSL 的独立 Docker Engine 中，用正式锁定依赖通过空数据卷初始化、非 root、鉴权、配置保存、合成视频扫描、HTTP Range、配置重载与重启持久化验收。

Docker 不使用桌面托盘。在网页配置中添加媒体目录时，只需填写容器内实际目录，例如 `/media`；Peach 自动保存所需的路径配置。Windows 安装包、macOS 源码运行和 Docker 的入口分别见 [README](../README.md#下载与使用)。

## 数据与媒体

`/data` 保存配置、SQLite 数据库、凭据和图片缓存，必须使用数据卷保存，重建容器时保留该卷。媒体目录单独挂到 `/media`；默认只读，扫描和播放不需要媒体写权限。整理、移动与删除需要使用者明确提供可写挂载。

CloudDrive2 与 Peach 分别运行。NAS 已有 CloudDrive2 时直接复用；全新机器按 [CloudDrive2 官方 Docker 指南](https://www.clouddrive2.com/docker.html) 安装并绑定网盘。让 Peach 容器也能读取 CloudDrive2 后续挂上的网盘目录，需要 Linux 宿主机支持 FUSE，并将 CloudDrive2 的挂载目录配置为可共享挂载变化；Compose 中的 `rslave` 用于让这些变化进入 Peach 容器。此机制不适用于 Docker Desktop，详见 [Docker 挂载传播说明](https://docs.docker.com/engine/storage/bind-mounts/#configure-bind-propagation)。Peach 容器不需要 FUSE 或特权模式。目录权限必须允许容器用户 UID 1000 读取。

需要云下载或推送发现时，Peach 配置中的 CloudDrive2 API 地址使用同一容器网络的服务名或可达的宿主机地址；容器里的 `127.0.0.1` 只指向 Peach 自己。

仓库根的 `compose.yaml` 使用 `PEACH_MEDIA_ROOT` 指定宿主机已有媒体根；容器中固定为 `/media`。先完成 CloudDrive2 挂载，再启动 Peach。命名卷 `peach-data` 保存 `/data`，不要执行删除该卷的操作。需要绑定现有数据目录时，先做完整备份并确保 UID 1000 可写；不要让另一台 Peach 同时写这份 Peach 数据库。

## 首次运行与访问

使用 Compose 启动后，Peach 会为全新数据卷创建配置、数据库和随机登录口令。已有配置保持原值；如果已有数据库但缺少配置文件，服务拒绝启动，需要恢复配置文件。初始化日志只显示口令文件位置。

在容器终端运行 `peach token` 取得登录口令，在浏览器登录后设置访问密码。配置页要求登录，并且连接必须来自容器本机或私有网络；携带代理转发头的管理请求会被拒绝。通过配置页保存后，服务自动读取配置；勾选扫描时会启动资料处理。

容器内 HTTP 端口固定为 8900，访问端口在 Compose 设置。默认只发布到宿主机 `127.0.0.1`，远程访问使用私有 VPN 或 SSH 隧道。需要局域网访问时，通过 `PEACH_BIND` 绑定宿主机的局域网地址，并设置访问密码。公网托管边界见 [安全说明](../SECURITY.md)。Docker 镜像不提供多人账号或租户隔离。

## 配置文件与路径

配置文件位于数据卷内的 `/data/config.toml`。在浏览器配置页的「媒体目录」中添加目录：填写 `/media` 或 `/media/115` 等容器内目录，选择媒体来源和读取策略，再保存。已有目录改了位置时，修改这一行的媒体目录；数据库路径的对应关系由 Peach 管理。

手动编辑配置文件前先备份，编辑后重启容器。更新镜像保留这个文件，因此已有文件的注释也保持原样。查看配置说明不需要重新初始化数据卷。

容器已更新并重新启动后，在 `compose.yaml` 所在目录运行以下命令，可查看新版注释与当前配置值；命令只显示内容，不改写 `/data/config.toml`：

```sh
docker compose exec peach python -c "from peach import settings_file as s; print(s.render(s.active()), end='')"
```

默认模板只包含初始值，不包含已有部署的媒体目录和自定义设置。用默认模板整份替换会同时替换这些值，可能使已有影片无法读取。只更新说明时可以替换注释，保留配置值；需要替换整个文件时，先备份并逐项核对差异。

以下说明供手动编辑配置或在多台 Peach 之间对应已有媒体目录时参考。网页保存时会处理 `[media.locations]` 和 `[media.mounts]`：前者保存数据库中的路径前缀，后者保存实际读取文件的目录。Peach 数据库在所有系统上都使用 Windows 格式保存影片路径；Linux 无需创建相同的盘符或目录。

以下三个路径各有用途。假设宿主机媒体目录为 `/srv/videos`，Compose 将它挂到容器 `/media`：

| 位置 | 示例 | 用途 |
| --- | --- | --- |
| 宿主机目录 | `/srv/videos` | 填在 Compose 的 `PEACH_MEDIA_ROOT`，目录必须已存在 |
| 容器内目录 | `/media` | 填在配置页的媒体目录或 `[media.mounts]`，Peach 实际读取这里 |
| 数据库中的目录前缀 | `R:\media` | `[media.locations]` 的示例值，对应数据库影片路径的开头；通常由 Peach 管理 |

手动配置全新本地馆藏时，可使用以下对应关系。网页自动生成的前缀可能不同，使用时无需改成示例值。将两个表中的条目合入已有配置，不要重复添加同名表：

```toml
[media.locations]
local = 'R:\media'

[media.mounts]
local = '/media'
```

扫描 `/media/a.mp4` 后，Peach 在数据库的影片 `path` 字段中保存 `R:\media\a.mp4`；播放时再把 `R:\media` 替换为 `/media`，读取容器中的 `/media/a.mp4`。数据库中的这段 Windows 路径是字符串，不是 Linux 上实际存在的文件名。

已有影片时，`[media.locations]` 必须继续匹配数据库中的路径前缀。把它改成 `/media` 不会同步修改已有影片记录，反而会使路径无法对应。只搬动实际媒体目录时，保留 `[media.locations]`，调整 `[media.mounts]` 和 Compose 的目录挂载。一个来源有多个目录时，两张表均使用数组，数量和顺序一一对应。

需要复用另一台 Peach 的数据库时，在配置文件的 `[media.locations]` 中填写与该数据库影片路径匹配的目录前缀，在 `[media.mounts]` 中填写这台容器能读取的目录，例如 `/media/115`；已有部署保留自身的对应关系。Compose 必须已挂入这些目录。CloudDrive2 是媒体来源之一，本地目录也可直接挂入 Peach。

容器中的服务固定监听 `0.0.0.0:8900`，宿主机访问地址和端口使用 Compose 的 `PEACH_BIND`、`PEACH_PORT`；`[server]` 的 `host`、`port` 用于直接运行 `peach serve`。独立 Docker 部署保持 `[replication] enabled = false`，无需配置桌面托盘或 SMB 复制。

## 硬件转码（AMD / Intel VAAPI）

Linux 首次重编码时会探测实际可用的硬件编码器，整片 MP4 与 HLS 分片共享探测结果。AMD / Intel 使用 VAAPI；存在 NVIDIA 设备时探测 NVENC。探测失败的后端在当前服务进程内不再尝试，重启服务后重新探测。封装复制不需要硬件探测。

镜像包含 Debian FFmpeg、Mesa VAAPI 驱动与 `vainfo`。AMD 宿主机需加载 `amdgpu` 和对应固件，并提供 `/dev/dri/renderD*`。Intel 需在自定义镜像内安装适配设备的 VAAPI 驱动；Mesa 驱动不代表已覆盖全部 Intel GPU。将下面的条目合入 Compose 的 `services.peach`，设备路径按宿主机调整：

```yaml
    devices:
      - /dev/dri/renderD128:/dev/dri/renderD128
    group_add:
      - "${PEACH_RENDER_GID}"
    environment:
      PEACH_TRANSCODE_BACKEND: auto
      PEACH_VAAPI_DEVICE: /dev/dri/renderD128
```

`PEACH_RENDER_GID` 填宿主机渲染节点所属组的数字 GID。容器以 UID 1000 运行，需要该节点的读写权限；只透传设备但没有组权限仍会初始化失败。Unraid 在容器设置中添加同样的设备映射、环境变量和额外组，无需开启特权模式。默认 Compose 不挂设备，无 GPU 的机器可直接运行。

| 环境变量 | 值与用途 |
| --- | --- |
| `PEACH_TRANSCODE_BACKEND` | 默认 `auto`；可设为 `vaapi`、`nvenc` 或 `software`。指定硬件后端仍会验证能否编码，失败回退软件。 |
| `PEACH_VAAPI_DEVICE` | 指定容器内渲染节点；留空时按名称依次探测 `/dev/dri/renderD*`，使用第一个可用节点。 |

VAAPI 尝试顺序为硬件解码加 `scale_vaapi` / `h264_vaapi`、软件解码加 `hwupload` / `h264_vaapi`、`libx264`。解码能力还受 GPU 型号、片源编码与位深限制；硬件编码可用不能证明每个片源都能硬件解码。NVIDIA 透传还需宿主机驱动及 NVIDIA Container Toolkit，Mesa 不提供 CUDA / NVENC。

出现 CPU 高占用时，先查看日志中的「转码硬件后端」和「转码硬件探测失败」。完成日志「整片转码完成」或「HLS 分片完成」后的 `vaapi` 表示使用硬件解码和编码，`vaapi-encode` 表示软件解码加硬件编码，`libx264` 表示软件转码。再在同一容器、同一用户下检查设备权限与 `vainfo --display drm --device /dev/dri/renderD128` 的结果。`No VA display found` 需结合驱动和设备权限排查，不能仅凭节点存在排除权限问题。

`ffmpeg -hwaccels` 列出构建支持的硬件加速方式，`ffmpeg -h decoder=h264` 等列出对应解码器支持的硬件设备。VAAPI 解码通过普通解码器的硬件加速接口启用，`ffmpeg -decoders` 中没有名为 `vaapi` 的解码器不代表缺少 VAAPI 解码支持。参数含义见 [FFmpeg 官方文档](https://ffmpeg.org/ffmpeg.html#Advanced-Video-options)。目标 GPU 的实际解码、GPU 占用与播放效果需要在部署机器上验证。

## 更新与范围

更新镜像并重建容器时保留 `/data`。数据库有待执行迁移时，先备份并按 [运行与配置](OPERATIONS.md) 的迁移流程处理；Docker 启动入口不会自动迁移已有数据库。健康检查使用 `/healthz?ready=1`。

Linux 容器不读取用户电脑上的浏览历史，也不包含交互式浏览器；需要本机浏览器完成验证的来源在此环境不可用。CloudDrive2 的真实账号登录、网盘流量与 NAS 实机挂载需在目标机器验证。

## 镜像发布

构建使用官方 Python `3.14.8-slim-trixie` 与 uv `0.13.1`，依赖由 `uv.lock` 锁定，应用以非 editable 包安装，包含 Web 与迁移资源。镜像以 UID/GID 1000 运行，内置 FFmpeg、FFprobe 和 OpenSSL。构建上下文只允许代码与资源，不包含工作树、媒体、真实 Peach 数据库或凭据。

GitHub Actions 的 `Docker` 工作流先构建并运行临时卷验收，通过后发布。仓库变量 `DOCKERHUB_USERNAME` 和机密 `DOCKERHUB_TOKEN` 用于 Docker Hub 发布；机密只保存在 GitHub，不进入项目文件。未配置发布身份时仍执行构建验收。ARM 镜像需通过同等运行验收后再加入发布。
