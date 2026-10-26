# 总体架构

这份文档讲 Peach 由哪几块组成、各块管什么、数据怎么流动，以及哪些方向明确不做。每项取舍的理由在 `docs/adr/`。

## 产品形态

Peach 是一个 FastAPI 模块化单体：逻辑上前后端分离，部署时仍是一个进程。默认单用户、本地自托管；在线追更、外部元数据和可插拔 AI 都是正式能力。

应用端口不直接暴露到公网。远程访问与认证边界见 [安全说明](../SECURITY.md)，VPN 和 Cloudflare Tunnel 的配置见 [运行与配置](OPERATIONS.md)。

## 核心边界

每一块只管一件事，跨块只走下面写明的接口。

1. **Peach 数据库（ledger）**：以 SQLite 保存媒体记录、个人操作、资料来源和人物等档案。应用以这里保存的数据为准。两台机器各持一份数据库，同一时间只允许一台修改；`peach.sync` 负责复制，双方都修改时转为只读并要求选择保留哪份数据库，**不做多主合并**。
2. **API / 应用层**：FastAPI 承载页面、JSON、媒体响应和写入边界。`api.py` 只负责组装，路由分 `routes_auth`、`routes_pages`、`routes_media`、`routes_configuration`、`routes_api` 五个 APIRouter；`/api/{route}` 由 `web_router` 的 handler 表分派到 `web_catalog`、`web_entity`、`web_stats`、`web_batch`、`web_follow` 等域模块。
3. **平台层**：`peach.platform` 把数据库中的媒体路径转换为当前系统实际读取的文件路径。影片记录的 `asset.path` 在所有系统上都使用 Windows 格式；`asset.location` 表示媒体来源，例如 `local`、`115`。`[media.locations]` 保存数据库中的目录前缀，`[media.mounts]` 保存本机实际目录。例如数据库中的 `R:\media\a.mp4` 可对应容器中的 `/media/a.mp4`，Linux 无需存在 `R:` 盘。Docker、Linux、macOS 都使用这两项配置；Windows 直接使用数据库路径。缺少对应实际目录时，平台层拒绝读取，该来源显示离线，数据库中的媒体记录保留。`PEACH_MEDIA_MOUNTS` 可临时覆盖实际目录。CloudDrive 在 Windows 使用盘符，在 macOS 使用 macFUSE 挂载目录，系统差异由这一层处理。
4. **Media Engine**：FastAPI 只持有一个 `MediaEngine`，本地文件与挂载网盘都是原生后端。远端 MP4 默认走标准 Range；`stream-plan` 只在显式开启时给出按时间生成的 HLS 片段。HLS 是例外而不是默认，因为 HEVC-in-TS 会静默黑屏（ADR-0016）。
5. **Web**：单页、移动端优先。主界面由 React 路由树渲染，应用域控制器、共享状态和控件位于 `frontend/src/`；使用 Tailwind v4 与 BoardUI 源码，构建产物提交进 `web/dist/`。适配器清退遵循 ADR-0031，模块职责见 [前端层](FRONTEND.md)。
6. **AI Provider**：`InferenceProvider` 与 `AgentProvider` 分开。AI 只产出带来源和置信度的候选。
7. **Profile**：默认单用户，数据模型预留 user/profile，不引入完整账号体系。
8. **追更来源**：RSS/Atom 等协议返回的条目先转换为统一资料格式；原始资料、人工确认和数据库写入分别处理。「想要」清单 `want_item` 按番号登记库外作品，扫描登记影片时按番号自动匹配清单（ADR-0090）。
9. **任务系统**：来源成本、磁盘闸门、进程归属、进度和来源证据由 Peach 自己定义；HTTP、调度、媒体探测、图片与协议解析用成熟组件。
10. **下载**：Peach 只把磁力交给网盘离线（115 经 CloudDrive2 gRPC，PikPak 直连），文件落地后仍由推送发现与扫描登记入库（ADR-0089）。
11. **HTTP / 搜索**：网络适配器默认共用长生命周期的 HTTPX transport。例外有三个，都有证据登记：FANBOX 公开 `post.info` 用固定版本的 `curl_cffi` 保留 Firefox TLS/HTTP2 传输特征，但不求解质询、不登录、不读付费内容；Cloudflare 后面的来源经 `peach.browser_transport` 驱动本机浏览器取页（ADR-0065）；115 云下载经 CloudDrive2 的 gRPC 接口提交，`grpcio` 精确固定版本（ADR-0089）。作品全文搜索用 SQLite FTS5 trigram，短查询回退 LIKE。

## 数据流

```text
本地/网盘/在线来源 -> 索引/探测 -> ledger.db
                                  -> FastAPI -> Web/播放器
AI/外部元数据 -> 经复核的候选 -> ledger
```

## 明确不做

- 微服务、消息总线、PostgreSQL
- 一次性整站重写前端（只按 ADR-0031 逐页迁移）
- 抓取或保存 ChatGPT/Claude OAuth token
- 把 Stash 私有目录或 GPL 构建当作 Peach 的稳定打包依赖

Stash 遗留的数据缺陷与许可证边界见 [docs/STASH.md](STASH.md)；复用与自研的边界、旧脚本的继任关系见 [docs/REUSE.md](REUSE.md)。

## 运行数据目录

Windows 与 macOS 各自在内置盘持有代码、`peach-data`、`.venv` 和 worktree；外置盘只提供
配置声明的媒体挂载点。跨机只有三条通道，各走各的，一条出故障不由另一条代替：

- 代码与任务分支走 GitHub，worktree 目录各机本地重建；
- 数据库由 Peach 在两台设备间复制，同一时间只有一台可修改；
- 图片等生成产物走 Syncthing 单向同步。

其余运行数据按 durable artifact 与本机状态拆开，禁止整体同步，边界见 ADR-0017。

`peach-data` 与代码仓库分开放：默认取仓库同级的 `peach-data/`，`peach init --data-root` 可改，
环境变量 `PEACH_DATA_ROOT` 覆盖它；本机坐标写在 `<Peach 数据目录>/config.toml`。目录分层如下：

- `database/`：SQLite 真相库。本地是工作副本，血缘记在同目录的 `ledger.db.sync.json`
- `generated/`：快照、海报、头像和厂牌 Logo；`follow-assets/` 与 `link-marks/` 是关注头像、来源图标与外链圆标的可重建缓存
- `sources/`：浏览器、追更、盘点和导出等不可变原始输入
- `state/`：任务检查点、本机运行状态和锁
- `secrets/`：仅本机保存的凭据材料
- `logs/`：当前运行日志
- `archive/`：历史迁移证据和备份
- `inbox/`：等待处理的临时导入
- `tools/`：FFmpeg 等本机托管运行时；二进制和许可证不进入 Git

这是约定的分层。各机实际路径、符号链接和挂载状态在操作前核对，运行记录见 [运行与配置](OPERATIONS.md)。
