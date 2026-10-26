# 复用清单

## JavLibrary 浏览器来源

`sources.javlibrary` 复用现有 SiteSource／SiteRecord、Beautiful Soup 4.15.0（MIT）、SourceTransport 与 BrowserTransport，不增加运行依赖、浏览器运行时或 JSON 代理。字段边界参考已固定的 amane 0.20.0（revision `cc42cee6ecf290cf05af1caa347d2dc2d5aeffcd`，GPL-3.0）JavLibrary 采集器；该桥自身的 HTTP 会话无法复用 Peach 的人工验证窗口，因此保留原生站点适配器，传输与验证交给现有公共层。适配器解析搜索与详情，核对完整番号并拒绝站外详情链接；搜索直接返回详情页时不重复请求。

普通 HTTP 请求的只读实测返回 403；用户手动通过浏览器人机验证后，单作品只读查询取得了番号一致的标题、演员与封面地址。该样本不证明全站覆盖或会话长期有效。人工等待按现有浏览器预算结算，验证超时与页面变形不会记成无作品。隔离用例覆盖单结果跳转、错号、站外链接、验证与登录页面、未知结构、私网图片地址及人工等待预算。资料沿用社区来源复核、冷却与备用来源；封面沿用现有来源和图像检查。

## 本机外部播放器

Windows 外部播放复用系统注册表的 App Paths 发现 PotPlayer，Python 3.14 标准库
[`subprocess.Popen`](https://docs.python.org/3.14/library/subprocess.html#subprocess.Popen)（PSF）以参数数组启动程序，
不经命令解释器，无新增依赖与分发体积。偏好复用 `fsutil.atomic_write_text`，媒体定位复用 MediaEngine
的授权根目录检查，在线播放复用限时 HMAC 与既有关注代理。Peach 仅负责本机权限检查、播放器选择
和启动反馈，解码及外部播放进度留在播放器内。本地隔离用例覆盖参数、拒绝越界、模式保存及观看记录不变；
浏览器用例覆盖常规视频入口、手动内置播放和桌面／手机控件几何。

## Windows WebView2 独立窗口

窗口采用 pywebview 6.2.1（BSD-3-Clause）与 Python.NET 3.2.1（MIT），传递依赖由 `uv.lock` 固定。PyPI 发布元数据已核对，Python.NET 声明支持 Python 3.14；Windows CPython 3.14.9 与本机 WebView2 的隐藏窗口及 JavaScript 执行已通过实测。实现源码核对覆盖 pywebview 的 WinForms、EdgeChromium、关闭事件、页面下载与原生窗口接口。

运行包增加窗口绑定与互操作程序集，复用系统安装的 WebView2，不打包整套 Chromium。依赖许可证随包内分发元数据保留。依据 [pywebview 窗口生命周期](https://pywebview.flowrl.com/guide/usage.html)和[微软 WebView2 分发要求](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)，运行不依赖完整 Edge 浏览器。当前树和 Git 历史没有可复用的 WebView2 窗口宿主；Qt/Electron 引入另一套体积更大的运行环境，本实现使用现有 Python 服务与系统运行组件。

Peach 只持有窗口策略、认证的本机控制指令、原版托盘回调以及包身份。托盘、服务归属、健康检查、首次设置、CLI、日志和任务生命周期复用原版。WebView2 独立 Cookie 与配置目录、下载、原生全屏和外部网址处理属于窗口适配面。默认采用独立安装与资料标识；不迁移真实账本或共享个人凭据。

Windows 的系统代理补齐复用标准库 `urllib.request.getproxies_registry`。启动环境只有 `NO_PROXY` 时，窗口把系统代理路由补进本进程环境，并保留系统与环境两处的绕过规则；已有显式代理时不补齐。代理优先级依据 [Python 的 getproxies 说明](https://docs.python.org/3.14/library/urllib.request.html#urllib.request.getproxies)，绕过规则交给 HTTPX 处理，不另写代理解析器。amane 桥的脚本、清单和锁文件随独立包分发，运行环境仍由原版安装流程管理。

## 检查输入与源码文案

测试差异复用 Git 文件快照、Python AST、标准库 TOML/JSON 解析器及现有 filelock。Python 注释和清单说明字段通过有效内容比较识别；真实依赖、执行模式和无法解析的输入保留完整验证。完整批次按测试文件保存结果，仅补齐受影响范围，不延长结果有效期。

JS/TS 文案复用 [Oxc parser](https://oxc.rs/docs/guide/usage/parser)，开发依赖固定 `oxc-parser@0.154.0`，MIT 许可，来源为 [Oxc 官方仓库](https://github.com/oxc-project/oxc)。项目 Vite/Oxlint 工具链已有 Oxc；独立 Node 检查器解析注释、字符串、模板与 JSX，不进入应用运行包。前端源码解析错误为零。Tree-sitter 的试用在具名元组成员与 JSX 查询参数处产生错误，未采用；不新增 Python 解析器依赖。中文和英文判据集中于 `scripts/copy-rules.json`，含糊词语只提示人工核对。

## macOS 独立构建

独立应用复用项目已有的 PyInstaller 6.22.3（GPL-2.0-or-later，带发行例外），由 macOS 原生 bootloader 承载 Python 3.14 与 Cocoa 菜单栏，资源与 CLI 入口沿用现有桌面实现。镜像使用系统自带的 `hdiutil`，无需新增运行时依赖。构建与签名依据 [PyInstaller macOS 文档](https://www.pyinstaller.org/en/stable/usage.html)和 [Apple 分发文档](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution)。

Peach 负责独立数据目录、首启、构建身份和资源清单。arm64 与 x86_64 使用各自的 Python 和原生扩展，按 [PyInstaller 架构约束](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support)分别生成；不使用 lipo 合并冻结程序。应用不安装系统组件，采用 DMG 拖放安装；PKG 的系统级安装流程不属于这条路径。源码运行的 LaunchAgent 外壳继续使用自己的构建入口。独立制品的启动与读写验收使用隔离数据目录，不改变真实 ledger 或双机配置。

## 硬件转码后端

整片 MP4 与 HLS 复用现有 FFmpeg 的 CUDA / NVENC、VAAPI、`scale_vaapi` 与 `hwupload`，不引入 Python 命令包装库。Windows 发行工具固定为 FFmpeg 9.0.2（GPL-3.0-or-later，来源清单见 `scripts/ffmpeg-sources.json`）；Linux 镜像使用 Debian trixie 的 FFmpeg、`mesa-va-drivers` 与 `vainfo`，随 Debian 安全更新取得版本，不另行复制驱动代码。Mesa 按上游各组件许可证分发，驱动增加的镜像体积由构建决定，AMD 需要宿主机 amdgpu 与固件。设备参数和硬件解码接口依据 [FFmpeg 官方文档](https://ffmpeg.org/ffmpeg.html#Advanced-Video-options)，驱动范围依据 [Debian VAAPI 说明](https://wiki.debian.org/HardwareVideoAcceleration)。

Peach 只保留设备选择、一次性合成帧探测、命令尝试顺序与失败回退；探测结果由两个服务共享。复用检索覆盖当前 NVENC 链、媒体工具解析器与 Git 历史，没有独立 VAAPI 路径。FFmpeg 已覆盖编解码与滤镜，ffmpeg-python 一类包装层不提供设备驱动或硬件可用性判定，因此沿用项目 subprocess 入口，不新增 Python 依赖。回归用合成输入与进程替身验证 Linux 节点选择、权限和超时、10-bit 转 NV12、整片及分片回退。2026-10-19 的 Windows NVENC 实测使用 640×360 合成帧完成探测，合成 10-bit HEVC 经整片 MP4 与 HLS 两条 CUDA / NVENC 路径输出 H.264 `yuv420p`；128×128 不满足该编码器的最低尺寸，因此探测统一用 640×360。目标 AMD GPU 的驱动初始化、解码与播放验收未取得。无生产入口或真实 ledger 写入。

## 独立页面的请求反馈

请求错误文案复用 `core/request-error.ts`，实体登记复用 `core/entity-links.ts`。API 直接引用这两个模块，首启与登录页不加载主界面的路由和字素分段初始化；主界面仍通过 `core/index.ts` 导出同一份错误文案。冷启动导入回归核对 API 不构造界面分段器，既有反馈测试覆盖状态码、超时与服务端中文原因。无新增依赖。

## GitHub 项目看板

用户反馈归属应用仓库 `peach-mitao/peach`，维护者项目任务归属 `peach-mitao/peach-roadmap`，共同使用一个公开 Project。集中在应用仓库处理事件并每 6 小时补查规划仓库，凭据仅保留在应用仓库。Issue 关联使用仓库名与编号，迁移映射兼容已存在的关联行；交付版本始终按应用仓库的主线提交和发布制品核验。

看板同步复用 GitHub CLI 的已登录认证、GraphQL Projects API 和 Git 的提交祖先校验；不新增 Python 依赖。官方 [Projects Actions 自动化](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/automating-projects-using-actions)支持独立项目凭据，仓库默认令牌不能写组织 Project。项目原生状态工作流没有发布标签与制品归属判定；通用自动加卡 Action 也不覆盖验收和发布的分别记录，因此由 `scripts/project_board.py` 保留 Peach 的状态规则，传输与认证交给系统已安装的 `gh`。运行依赖是官方维护、MIT 许可的 GitHub CLI 和 GPL-2.0 Git，使用 GitHub 托管 runner 内置版本；本机只读 GraphQL 与标签祖先验证后再应用。无生产服务、ledger 或媒体写入。

## JAV 用户脚本添加

用户脚本由 [peach-mitao/peach-userscript](https://github.com/peach-mitao/peach-userscript)（AGPL-3.0-or-later）独立维护，使用 [Tampermonkey 官方 GM API](https://www.tampermonkey.net/documentation.php) 的本机存储、菜单与 `GM_xmlhttpRequest`；SPA 通知依据 `window.onurlchange` 契约。上游使用 Node.js 24 LTS 内置模块确定性构建，运行时零依赖。Peach 的 `web/integrations/` 消费同一发布的完整安装脚本与站点快照，固定版本、上游提交与 SHA256 见 `jav-userscript-release.json`。安装端点直接返回制品，后端独立校验已登记域名与作品参数；上游导出工具在隔离工作树同步这两份制品和来源记录。Sleazy 热门脚本只提供显式域名与镜像发现证据，不复制其实现或执行第三方代码。宿主扩展由用户安装，兼容性以实际浏览器及脚本管理器授权为准；模拟 GM API 的真实 Chromium 页面完成回归，真实扩展安装及源站页面联合验收未取得。

Peach 保留页面粗提取与身份冲突提示，服务端复用 `catalog_rules` 的唯一番号规则、`feeds.scan_code`、`web_resource_search.q_resource_search` 的来源顺序与质量排序、`submit_offline_download` 的 infohash 幂等、现有云下载落地与后处理。浏览器添加在共享提交锁内按番号接管已有任务，覆盖其他入口同时提交不同磁力的情况。专用令牌经 `CredentialStore` 仅保存 SHA-256 摘要，不声明跨机同步字段。队列用已有 `atomic_write_text` 保存本机运行状态，关闭功能会撤销令牌并停止尚未提交的添加。

书签不能执行云下载链路；完整浏览器扩展增加发布与权限管理面；现有外部下载器不承接 Peach 的网盘目标、入库和后处理，因此未引入。临时账本与模拟 115／PikPak 的 API 回归验证搜索、重复点击、重启恢复、失败重试和凭据隔离；用户脚本及配置页通过桌面与 390×844 浏览器回归。无新增 ledger 表或迁移。

## 长跑任务的结果显示

资料采集与关注检查复用已有字段判据、事务和候选复核。全部本地 NFO 先读取并按影片提交；在线资料按来源返回时提交，候选 CSV 仍按时间合并落盘。关注列表的安全 partial 行与逐项详情分别保存，历史边界和整页游标沿用连接器规则。中断保留已提交结果，停止后不再落库。

显示层复用 SQLite `ledger_revision` 触发器、候选与图片目录版本，以及 TanStack Query 5.104.3（MIT）查询失效和任务轮询；不新增推送服务或依赖。应用根按结果版本刷新相关读缓存，任务心跳不使资料失效；慢请求期间的新版本排队，保留当前内容。资源同步扫描同时显示已完成来源，清理仍需完整扫描和确认。隔离账本、暂停的来源调用与手动放行的 Promise 验证中间结果可见及中断保留；无真实抓取或真实 ledger 写入。

## STRM 与挂载来源

单文件入库复用 `scan.ingest_path` 与统一处理任务的 `asset_ids` 范围，文件选择复用现有系统选择窗口及互斥锁；目录、文件类型与本机调用权限由服务端校验。临时账本覆盖同目录多视频、重复入库、无效范围与失效候选，页面使用既有表单、弹层和任务查询，不新增依赖。

STRM 采用文本指针、播放时解析的通用行为，参考 SmartStrm 文档、OpenList 的 `drivers/strm` 与 Jellyfin 的 STRM 探测入口；固定 revision、许可证与实际读取位置见 [取证记录](reference-snapshots/strm-source-evidence.md)。SmartStrm 核心闭源，源码未取得。Peach 独立实现受限 HTTP(S) 指针，复用 HTTPX、现有 Range 传输、`m3u8` 解析与播放器计划；无新增运行依赖，也不复制上游应用代码。

光鸭、OneDrive 与其他网盘复用声明挂载根、路径翻译、扫描、文件整理与回滚、隔离删除和空目录清理。所有挂载来源统一开放读写，底层权限或不支持的操作由挂载工具报错；网盘登录与平台 API 由 CloudDrive2 或 OpenList 等挂载工具提供，不新增 SDK 或依赖。临时文件与账本回归覆盖三个来源的整理、回滚、删除、权限拒绝和空目录清理；真实账号兼容性见待办。挂载目录可单独设置代理流量保护，成本投影与批处理使用同一规则。STRM、ISO 跳过后台媒体读取，普通媒体保留按来源策略执行的探测；九宫格和时间轴共用本机视频预览开关。

Actions 工作流静态检查复用 actionlint 1.7.12（MIT，`rhysd/actionlint:1.7.12` 官方 Docker 镜像），由本地 Docker action 使用，独立 Docker 目录纳入 Dependabot 与接管入口。GitHub Actions 生态不更新 `docker://` 镜像引用。Windows 官方二进制按发布校验和完成本地 POC；CI 在 Linux 容器检查语法、表达式、任务依赖与脚本。Python 与浏览器回归继续使用现有统一入口及 Playwright；六片浏览器权重来自 [已通过的 CI](https://github.com/peach-mitao/peach/actions/runs/38981261189) 的 69 份 TAP，记录在 `scripts/ci-browser-timings.json`，没有新增应用运行依赖。

目录浏览的 `media_folders` 复用 `PureWindowsPath`、媒体库配置的目录前缀和账本路径，不访问媒体挂载。`folder_rules` 复用 profile 偏好、规范实体、字段归属、身份断言、逐件归属否定和扫描登记入口；规则以 `user:directory-<目录标识>` 保存独立来源，预览摘要绑定资产版本与关系，撤回只处理本来源。已有明确归属保持独立。

界面逐字采用 MIT 许可的 BoardUI Tree View、Breadcrumb 与 Direction，固定源码 SHA-256 见 `frontend/src/react/boardui/ORIGIN.md`；复用现有 React 19.3.0、React Aria Components 1.22.1、Motion 14.1.0 与 Remix Icon 4.9.0，无新增依赖。Peach 的原生链接适配上游 `next/link` 槽，领域逻辑仅承担目录归属、冲突保留、范围确认和来源撤回。能力检索覆盖当前媒体库分组、目录否定、字段归属和 `media_libraries.py` 的 Git 历史；物理目录整理继续使用既有入口。验证只使用临时账本与合成馆藏。

Linux Docker 容器复用 Docker 官方 Python `3.14.8-slim-trixie`、uv `0.13.1`（MIT/Apache-2.0），以及 Docker 官方 Buildx、login-action 和 build-push-action 的「先验收再发布」流程。Python 镜像许可证随上游组件保留，FFmpeg 使用 Debian 包及其许可证。Peach 差异仅为配置重载、资料处理请求与持久化路径；不引入新 Python 依赖，不改写真实 ledger。CloudDrive2 使用其官方独立服务，不复制其代码或打包进 Peach。部署与证据范围见 [Docker 部署](DOCKER.md)。

收藏窗口复用 React Aria 外壳、Peach 设置弹层布局及共用关闭控件 `SettingsDialogClose`。入口为中性星形按钮；实体分类和保存的筛选分别显示，已标记与稍后看位于导航底部。收藏图像复用实体引用、厂牌标识、本地代表作与已绑定在线来源的本地头像代理，通过全站图片回退链逐项尝试，不写入偏好。原始 BoardUI 源码哈希见 `docs/BOARD_UI.md`。Rule34 视频封面使用来源缩略图，图片保留原文件扩展名；失败图片在关注数据刷新时重新尝试。无新增运行时依赖或真实 ledger 写入。

关注封面代理复用来源登记的媒体主机白名单；第三方图片地址及重定向继续执行公网 DNS 校验。

收藏弹层的导航复用设置页液态玻璃、`useViewGlide`、`attachOverlayScrollbar`、入场与退场关键帧及落影，桌面和手机检查实际计算样式。官方预告复用 amane `cc42cee6ecf290cf05af1caa347d2dc2d5aeffcd` 的 Prestige SKU→UUID 协议，通过官方返回的作品身份与公开 MP4 字段取得地址；读取复用既有来源传输、限制和媒体代理，无新增依赖。

媒体 indicator 复用关注详情的半透明灰底胶囊：`rgba(0,0,0,.5)` 底色、无描边、`--pill-radius` 圆角及既有圆点间距。滚动内容复用 `wireHorizontalScroller`、`data-overflow-left/right` 和 `board.css` 的左右渐隐，底衬保持完整。预告优先读取 DMM／厂牌官方声明，其次读取同一番号的 JavDB 详情，匿名 WebUI 作为备选。DMM 复用 `DmmSource` 的 CID 搜索、身份核对、GraphQL、`SourceTransport` 和媒体 Range 代理；`sample2DMovie.highestMovieUrl` 字段已按同一 amane 固定 revision（GPL-3.0，仅作协议证据）及真实官方响应核对，不引入上游代码或新依赖。DMM、JavDB 的正负缓存按来源隔离、每天可再检查；预告缺失与连接失败分别返回。`RCTD-764` 已通过 1 KiB Range 请求确认 206、`video/mp4` 和 MP4 文件头，身份不匹配、来源优先级、WebUI 失败、缓存与媒体主机边界由行为测试覆盖。

## 设计准则参考

Open Claude UI（MIT）只用于[设计准则](DESIGN_GUIDELINES.md)与[源码取舍](reference-snapshots/open-claude-ui.md)，原文锁定在 `reference-sources.json`。采用减少重复外框、语义排版、容器适配、输入法保护、保持阅读位置、可恢复手势、锚点缩放与真实反馈等原则；组件复用现有 BoardUI、React Aria、Motion、Swiper 和滚动入口。示例任务终态、全局样式、宿主、语音与专有协议不进入运行时。源码审阅完成，浏览器 POC 未取得；无新增依赖、生产设置或 ledger 写入。

## 上游应用功能

amane `0.20.0 / cc42cee6ecf290cf05af1caa347d2dc2d5aeffcd`（GPL-3.0）提供实体收藏、
元数据比较图按需请求、结构化失败等行为参考；Peach 独立实现交互，复用现有 profile、
TanStack Query、规范实体、来源结算与复核队列，不引入上游应用数据库或任务引擎。
实体收藏随合并身份继承，命名馆藏筛选只保存现有目录查询条件。

来源连接复用 `SourceTransport`、失败原因表和冷却文件，诊断分开记录连接与解析的最近证据。
amane 的所有正式来源可选逐站直连或公共代理，桥保持一次子进程和 60 秒总时限。
比较图片在点击后请求，30 秒内未取得即可重试；不新增任意 URL 图片代理。

[自定义来源](CUSTOM_SOURCES.md)参考 OpenAver `502278a3e09c7520838ffb00b2ff080323a4cdcb`
（MIT）的声明式配置、番号身份核对和公网目标守卫，使用已固定的 curl_cffi、filelock、
SiteRecord 与候选结算；[外部播放](EXTERNAL_PLAYERS.md)复用既有媒体根检查、HTTP Range、
标准 M3U 与客户端协议，无新增依赖。Python 主程序和独立桥均使用 3.14，独立锁仍隔离依赖树。

## 二维播放墙

Folia（`chthollyphile/folia-major`，AGPL-3.0，revision `9a01e788ed52d25ea01b1f0d37934e5e371ae82b`）提供矩形块模板、镜头／世界坐标、视口裁剪、循环／有限墙及视觉样式；原文保存在 [矩形块模板](reference-snapshots/upstream/folia-block-templates.ts)、[让位表](reference-snapshots/upstream/folia-block-reflows.ts)、[动效参数](reference-snapshots/upstream/folia-wall-reflow-motion.ts) 和 [墙面样式](reference-snapshots/upstream/folia-wall.css)。`frontend/src/react/video-wall/` 在独立 `/wall` 页面使用四套模板及镜像、6×6 原位展开和暗色直角拼贴。封面复用 `ArtSlot`、`cardArtwork` 与人脸／正封取景；全封和正封按磁贴比例选择，只接收有封面的视频。Query、BoardUI、Motion、详情入口、舞台播放器和小窗均使用项目已有能力，不增加依赖。

墙内分卷和版本使用 `item-detail` 的 `queueKey`、`fetchQueue`、`chooseItem`、`nextPart`，列表行复用 `MixQueue`；上一部、下一部和进度控制监听同一 video。换批入口复用首页 `Shuffle` 和原有等待动效。展开卡的封面铺满，标题叠在中下部，播放时封面背景模糊、标题移到上方。

上游当前仓库仍在维护，技术栈为 React 19、TypeScript 7 与 Vite 8。模板与坐标算法没有 Python 或平台依赖，正式消费者是浏览器构建。Three.js、Pixi、歌词和音乐账号体系不适用于视频墙，未引入。探索墙复用现有 Query 分页，按进入视口的实例键分配作品，保留已看位置；未展示作品不足时自动续页，无封面页继续跳过，全范围耗尽后才循环。筛选及实体作品墙使用固定三列与手动续页。几何测试覆盖 80,000 项、负坐标、续页位置稳定和耗尽状态，播放器测试覆盖同一实例交接、取消与来源门槛；见 [播放墙取证](reference-snapshots/folia-video-wall.md)。

高亮复用 Folia 当前播放标记的内侧描边与发光，Peach 同时应用于选中和播放。队列滚轮交给现有 `MixQueue` 的滚动区，墙面不接管；手动换批使用独立等待态，Query 就绪后移动到新区域并恢复墙面。全局入口与沉浸模式一样常驻，非作品页面保留来源与 JAV 语境及返回地址，馆藏和实体作品页继承当前筛选。

## 脚本目录与参数

脚本路径读取 `settings_file` 的目录配置。`sync_sha1_115.py` 的 Cookie 默认位于 `secrets/115-cookies.txt`，复核 CSV 默认位于 `review/115-sha1重复.csv`；可通过 `--cookie-file`、`--dupes-out` 或对应的 `PEACH_115_COOKIE_FILE`、`PEACH_115_DUPES_OUT` 指定现有文件。目录排序偏好使用可重复的 `--deprioritize-dir`。TLS 两种 shell 共用 `resolve_tls_directory.py`，显式 `-OutDir`／`PEACH_TLS_DIR` 优先；`rehome_unknown_jav.py` 的目标账本根由 `--ledger-unknown-root` 明确指定。

## Windows 常驻托盘

诊断报告完成窗口复用 Windows `DialogBoxIndirectParamW` 与 `EndDialog` 的系统消息循环，控件和回调声明指针宽度；导出放在独立单实例线程，托盘消息回调可返回。原生隔离进程验证「打开目录」「打开文件」「关闭」、标题栏关闭、Esc 与 Enter，不操作现有托盘。未采用需要 common-controls v6 激活上下文的 `TaskDialogIndirect`，也未为单个窗口引入 GUI 依赖或 PowerShell 子进程；无新增依赖。

独立启动复用 Windows `CreateProcessW`、`STARTUPINFOEX` 与 [`PROC_THREAD_ATTRIBUTE_PARENT_PROCESS`](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)，经标准库 `ctypes` 调用；父身份及 Job 继承来自当前会话的桌面进程。子进程可以保留系统共享 Job，验收判据为关闭启动器的私有 Job 后仍存活；[嵌套 Job](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs) 可能只允许部分脱离。子进程挂起创建，记录归属后以 `ResumeThread` 执行；创建被拒绝时仅重试一次 `CREATE_BREAKAWAY_FROM_JOB`，恢复主线程失败时回收未执行的子进程。官方系统 API 无额外包、固定版本或第三方许可，适用于项目支持的 Windows，2026-10-15 核对。Peach 保留参数、Peach 数据目录、源码与 PyInstaller 环境处理，并消费一次性交接标记。入口先复用 `SingleInstance` 的只读锁探测，已运行时直接打开页面；锁探测不创建文件，不阻塞子进程取得锁。

当前树与 Git 历史已有 `windows_restart`、`tray_lifecycle` 和 `process_job`，继续复用各自的重启、恢复及子服务清理行为。`DETACHED_PROCESS` 只脱离控制台；`CREATE_BREAKAWAY_FROM_JOB` 依赖所选父进程的 Job 允许脱离；WMI、Shell COM 与计划任务额外引入服务、环境交接或注册生命周期，未采用；`pywin32` 不属于运行依赖，监视器又必须只加载标准库，因此采用原生 API，无新增依赖。源码托盘直接调用基础解释器，按 [CPython 3.14.7 venv 启动器](https://github.com/python/cpython/blob/v3.14.7/PC/venvlauncher.c) 的 `__PYVENV_LAUNCHER__` 协议保留虚拟环境身份。仅参照协议，没有复制 PSF 许可源码。临时进程回归覆盖多层共享 Job、内层自动脱离与启动器私有 kill-on-close Job：普通子进程随启动器退出，桌面子进程仍在共享 Job 中存活，并消费交接标记。另核对 Unicode 参数、环境、工作目录、虚拟环境身份与进程清理；真实 Explorer 不加入测试 Job。诊断只记录可取得的进程成员状态和调用进程直接 Job 的原始限制值，位定义复用 [Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)；完整句柄链与所有者未取得时明确标记，不通过全系统提权扫描推断。

## 西方人像与官方封面

Babepedia 主图库和 Vixen 网络公开 GraphQL 的字段核对 [Stash CommunityScrapers](https://github.com/stashapp/CommunityScrapers/tree/d0f75013e09a0469b70365fef3294ca915edcf8c) 的 Babepedia、vixenNetwork 采集器（AGPL-3.0；2026-10-07 获取的 revision）。该项目使用 Python，可经插件在 Windows 上运行；Cloudflare 限流和上游字段变化可能导致未取得。只参考公开协议和身份字段，不复制 AGPL 实现，不恢复已关闭的 Stash 适配器，也不引入依赖。HTTP、HTML、图像检查、内容缓存、头像安装和封面边车分别复用现有 `HttpxTransport`、BeautifulSoup 4.15.0、`avatar_provider`、`avatar_picker`、`cover_artwork`。

真实只读试验取得 Angel Smalls、Christy White、Lena Anderson、Melody Marks、Sweet Sophia 的主图库；Babepedia 的 Blaire Ivory 档案明确列出别名 Lena Anderson。Tushy 2019-08-14《Cam To Me》、Vixen 2019-04-09《I Want It All》的官方封面均已取得。搜索还返回 Lena Paul 等人的作品；Peach 用厂牌、完整名字、发行日和唯一结果筛选，并对详情再核对。没有番号的官方封面按 `ASSET-ID-<id>` 保存，资产字段保持原值。头像保留主图库候选，已有头像不自动覆盖；官方完整出演名单参与合演保护，多人封面需要框选。采集入口 `harvest_western_artwork.py` 每轮至多 16 位，默认只预览，输出来源回执；封面边车记 `auto:western-artwork@<时间>`，由 `revert_auto_landing.py` 按批撤回。

## 实体身份与职业

本地作品的创作者引用由 `web_catalog.attach_creator_reference_details` 批量附上可信浏览分类与资料页同源的代表作头像。`avatarInner`、队列和详情共用实体图优先、可用代表作回退的取图契约；发布账号的代表作保留「非本人」提示。卖家是账号角色，详情按可信 `identity_categories` 展示「卖家」，不推导出演身份。

公司资料复用 `entity.metadata_json`、HTTPX、官网公司概要表和定义列表与标识采集器。`company_profiles` 只有通用判据（概要标签、服务条款订立方原句），按字段保存出处、原文和批次，只填空；一页多张公司表时按法人名对实体名与别名选表，公司成立与品牌启动分列，集团与分工关系不由代码推导，只作候选。`harvest_company_profiles.py` 默认产复核 JSON，`--input --apply --backup --batch auto:company-profile@…` 按缓存页重放判据后落库，清单里的别名、链接与第三方页面不写。`revert_auto_landing.py` 可撤回同批公司字段及标识，无新增依赖。

Babepedia 资料采集复用 `babepedia_page` 的主名与别名交叉核对、`performer_profiles` 的整行来源保护和 `performer_alias_followup.land` 的占用检查。英文艺名只在明确允许的来源中登记，单个罗马字词只在它就是页面主名时登记，JAV 后继保留自己的名字策略。真人账号与出演者共用资料表、别名分组与头像候选；保留账号角色及旧地址，与已登记账号说法不一的链接只进回执。出演者主图经 `avatar_followup.match_gallery` 与单人作品封面比上脸才补空头像，账号与本人档案图库仅作候选，推荐人物排除，单图失败继续其余有限候选；候选表只认判词「命中」的档案地址。`harvest_western_profiles.py` 每轮至多 16 位，计划、备份、回执与批次撤回齐备；既有资料和头像保留。真实只读复核取得 Ria Kurumi 等 16 份匹配档案，无新增依赖。

身份分类复用 `entity`、来源关系、`field_owners`、SQLite 迁移与版本触发器。职业与账号角色使用独立多值断言，跨角色身份使用可撤回关系；[Schema.org Person](https://schema.org/Person) 的 `hasOccupation`、`sameAs` 与 [Role](https://schema.org/Role) 提供语义参照，2026-10-06 已核对官方文档。实现不复制外部解析器或新增依赖。目录推断和搜索命中只存候选，公开来源事实与用户复核分别保留状态。

FC2 卖家名册复用 `sources/fc2.py` 的 `label`、`seller_url` 来源契约、`normalise_code_key`、`upsert_asset_entity`、职业断言和 SQLite 备份。`import_fc2_sellers.py` 读取已有作品快照，不发起采集；账号地址、来源作品号和馆藏作品号必须一致。同名不同账号、作品来源冲突保留在复核产物，既有身份和资料不覆盖；计划按摄取同一判据把集合名归到已有账号，摄取不收的作品记跳过，回收站作品不挂卖家。写入归属为 `script:fc2-seller@<批次>`，统一撤回入口 `revert_auto_landing.py` 同时处理账号链接、别名、外部编号、卖家断言及来源关系；有后续资料或引用的实体保留。导航复用 `BoardTabs`，动画作者保留 `creator` 身份与资料路由，通过艺人分类浏览。不新增依赖或外部解析器。

关注作者建档（`follow_creators`，ADR-0096）复用关注页的作者归组 `follow_identity.name_key`／`author_name`、`canonicalize_entity_name` 的平台名闸、`follow_sources.profile_link_identity` 的名片识别和后继派发（ADR-0040）。链接与别名按 `auto:follow-creator@<批次>` 记归属，`revert_auto_landing.py` 统一撤回；实体有其他引用时保留。资料页的同名确认复用 `Note` 与 Board `Button`，不新增依赖。

在线作者资料页复用关注页的 `FollowCard`、照片墙与 `author_facets`；本地与在线视图共用实体身份。在线头像候选复用 `follow_avatar` 的官方主机解析、`follow_media_resolver` 与 `open_upstream` 的凭据边界、`follow_cover_service` 的有界封面请求和 `avatar_picker_cache` 的裁剪安装。候选限当前实体绑定且可见的内容，社媒自动解析覆盖 X、Patreon、pixiv 与 FANBOX；其他来源由用户提供图片地址，无新增依赖。

关注页的创作者行与卡片头像、署名复用 `FollowAuthorLink` 和来源的唯一实体绑定，进入创作者资料页；卡片按实际发布者取身份。未建档作者沿用关注筛选。创作者行横滚访问全部作者。筛选条件与卡片悬停面留 16px；首页、关注页和资料页共用交集条。`useViewGlide` 复用 `ResizeObserver` 监听显隐与相邻控件尺寸，`moveGlidePane` 在重新落位时取消位移和形变动画，无新增依赖。

JAV 发现卡复用馆藏的 `data-media-card` 网格与悬停面、`coverImage` 封面取景、`cardRatio` 大小图比例和 `feedIdentity` 身份头像；筛选复用 `FilterGlassRows`、`FilterPill`、`Segments` 与 `SortKeys`。出演者引用由 `web_catalog.entity_ref` 提供图片版本与取景；标题保留两行，操作区贴底。发现查询复用口味汇总与 `related.rank_related` 的 IDF/MMR，输出相关度和实际选择分，支持口味、发行日期、来源评分、时长、番号排序。来源资料复用 `PublicAPI`、`BackgroundJob(runs=None)`、`fsutil.atomic_write_text` 与标准库 `ContextVar`；固定至多 100 部，真实 HTTP 预算 202 次、16 MiB、900 秒，3 秒串行间隔。资料边车有效期 6 小时、最多 2,048 条、失败冷却 15 分钟，完成或停止后由显式刷新续跑。`WantMagnets` 以 `queryUrl` 共用作品详情和想要清单的资源布局，按资源区容器宽度排列操作，缓存按来源分开，容量复用 `fmtSize`。JAV 与名册的静态搜索框复用 `islandSearchInput`。资料扩展见 [JAV 发现](JAV_DISCOVERY.md)，无新增依赖。

JAV 作品详情与馆藏视频共用 `IdentityCell` 的头像、标识和首字回退，标题折叠复用 `useFoldedTitle`。厂牌大小写唯一匹配与 `sources.javdb.maker_writing` 的日英组合解析只用于来源身份投影，标识按规范名读取。评论、资源复用 `Disclosure` 和 `setCollapseOpen`；来源与评论评分共用按小数裁切填充的星星。桌面右栏以 `contain:size` 排除正文的固有高度贡献，媒体区决定框体高度，正文在右栏内滚动。写操作取 `/healthz` 的账本只读状态，与运行配置权限分别处理。图片切换由 `MediaCarousel` 同时服务关注、库外 JAV 与馆藏详情，提供环形翻页、横向缩略图和位置圆点。缩略图与圆点复用浏览器原生 `overflow-x:auto`、`wireHorizontalScroller`，选中项在切图、循环和容器缩窄时保持可见。缩略图使用 `IntersectionObserver` 按可见范围挂载，馆藏经 `/sample-thumb`，来源图片共享查询缓存，关注优先取附件小图；Swiper 14.3.0 留给灯箱。馆藏样张复用 `sample_images.detail_refs` 和 `/sample-image`，`/api/item` 只读本地序号、最多 30 项；查看图片暂停正片，返回保留位置。推荐分标签复用 BoardUI `Chip` 与共享 Tooltip。视频预览的 `mountPreviewPlayer` 复用已固定的 Video.js 10.1.0（Apache-2.0）、官方 HLS 媒体组件、`mountPlayerQualityControl`、`mountPlayerSeekPreview` 和 `mountPlayerSpinner`，安装共用控制条、设置、时间、音量与窄宽度折叠。来源给定的 HLS 直接由播放器请求；片源代理、馆藏 ID 和观看上报沿用各自入口。播放器生命周期由当前详情管理，异步加载完成前离开会取消挂载，切换与关闭会停止并销毁实例。

卡片多选由 `MediaCard`、`FollowCard` 的 `selectMode` 统一控制：保留选择入口，单项保存、已看、忽略、稍后看、预览跳转和回收站操作不挂载；退出多选恢复单项操作。首页、实体页、关注与回收站沿用同一组组件。

配置页中的来源凭据复用 `Section`、`Stack` 与 `SettingsSectionLabel`；独立凭据页面保留自己的面板层级。自定义来源表单和卸载开关只使用一层正文间距，列表与表单操作区复用 `Footer`；空来源列表的添加入口放在 `Stack` 正文中。PikPak 直接显示账号表单，两种登录动作同排。在线、离线等连接状态复用 BoardUI `StatusDot`；权限、任务和凭据结果复用 `Chip`。作品资料站点标识经 `/site-mark` 读取本地缓存，资源搜索帮助直达 [详细配置说明](RESOURCE_SEARCH.md)。不新增运行时依赖，不修改凭据内容或 ledger。

账号归属复用 `review_decision`、`creator_collection_base`、`write_owned_fields` 和全库归属审计。明确拒绝的资产与账号组合在自动摄取时跳过；用户写入保留复核入口。网黄博主只命中可信个人、内容创作者或发布账号断言，未分类账号保留全部名册入口。`attach_avatar_availability` 集中处理资料页、索引、搜索与统计的作品头像：发布账号缺少本人或出演职业证据时作品帧照常兜底，另标 `avatar_stand_in`，已安装图片独立保留。个人博主的补头像复用 `avatar_cover_face.face_square`、`readable_cut` 与 ADR-0074 的 `agreeing` 两部互证，画面取自接触印相九格（ADR-0099）。来源核查清单、逐关系回执和备份存于 `peach-data/review/`。

`entity_identity_research` 与 `apply_entity_identity_research.py` 复用规范实体写入、字段归属保护、只读预览、SQLite 备份及冻结计划。逐行撤回回执核对后续修改，新增实体有额外引用时拒绝删除；观看历史和人工判断不被回滚覆盖。资料站调研复用 `performer_alias_followup.WikiSitePages`（包着 `sources/seesaa.py` 的 `WikiPages`，加 404 记忆与按站冷却）、作品表解析 `rows()` 与 `person_profile`，以及 EUC-JP 页名直取写法，不新增依赖；`creator_identity_followup` 的后继壳照抄补厂牌、补别名两条。

## 发现推荐缓存

搜索推荐复用 `/api/items`、馆藏范围的 `/api/jav/discoveries`、`/api/follow`、`/api/follow/authors` 与 `/api/feeds/discoveries`，图片复用 `coverImage`、`feedNewCoverHtml`、`avatarInner` 和 `authorAvatarHtml`。JAV 保留 IDF/MMR 口味排序，关注取全部状态的随机作品，视频与图片复用 `groupMediaKinds`、`itemForMedia` 和附件缩略图；署名沿用 `followIdentity` 的发布者判据。搜索框不启动库外资料补齐；详情与订阅分别沿用现有作品、作者、关注管理路由。无新增依赖、迁移或 ledger 写入。

发现推荐的浏览证据复用 `taste_history` 私有历史库、规范身份和原子 JSON 写入；按日取证与同地址身份解析独立缓存。缓存只保存实体计数，历史、别名、关注、标签决定和解析规则参与失效；观看反馈不重复解析历史，任务心跳不影响偏好版本。页面预取复用 TanStack Query 的 `ensureQueryData`，无新增依赖。

## 播放器清晰度

作品详情的画框几何由 `react/stage/stage.css` 管理，馆藏与库外 JAV 共用视口高度、媒体行及常驻缩略图预留。小数星星的两层 SVG 共用同一图标框。缩略图滚轮复用 `wireHorizontalScroller` 并在卸载时清理。公开图片缓存使用标准库 `Future` 合并同键请求，缓存锁只覆盖内存读写；图片下载使用三个槽位，资料取数使用独立锁与既有来源间隔，不添加依赖。

待找资源复用 `MagnetSearch` 的来源链、发行时间策略与串行限速，服务端结果使用 `atomic_write_text` 原子保存为私有 JSON 边车。读缓存和网络查询分锁；重启时将原始墙钟时间转换为当前进程的单调时钟。边车最多 128 部、16 MiB，不保存错误，主动刷新失败保留成功结果。浏览器沿用 TanStack Query 和 localStorage，不新增缓存服务或依赖。

清晰度控件复用 Video.js 10.1.0 官方 `@videojs/hlsjs-video` 的 hls.js 引擎。多档播放从实际轨道读取分辨率，自动码率和固定轨道通过引擎选择；单档 HLS 缺少尺寸时读取原生视频元素的实际尺寸，并响应 `loadedmetadata`、`resize`。尺寸未取得时显示「当前画质」，轨道 ID 不作界面名称。来源各自独立的 MP4 档位由 Peach 保留播放位置后切换片源。

播放器按 [Video.js 10 官方迁移指南](https://videojs.org/docs/framework/html/guides/migrate-from-video-js-8) 和 [10.1.0 源码](https://github.com/videojs/video.js/tree/919ad955a9baf2433c68aba8151654dde0e58b5d/packages)接入 `video-player`、`media-container`、官方按钮／滑条与 HLS／DASH 媒体组件。`@videojs/cdn` 10.1.0（Apache-2.0）提供固定 ESM 模块，构建脚本按静态与字面量动态导入闭包分发并登记哈希；浏览器支持沿用项目目标。Peach 保留片源授权、时长口径、统计、字幕菜单和舞台生命周期，原生视频经 `media` 插槽携带字幕与观看上报。`video.js` 10.1.0 包的旧入口只有带错误码的 shim，不作为运行依赖；v8 的组件和 VHS 不进入产物。真实短片回归覆盖 MP4、HLS 和 DASH 解码，质量档位选择按 HLS 引擎验证，DASH 不声明手动档位能力。生产切换与 ledger 不属于依赖接管工作树。

Peach 播放器皮肤直接装饰 Video.js 10 的 light DOM：右侧控制组用自动左外边距贴住控制条右缘；官方时间滑条承接完整 18 px 命中区，6 px 轨道与唯一的 12 px 滑块分别绘制，悬停和聚焦放大滑块。浏览器回归 `frontend/e2e/player-skin.test.ts` 使用合成短片验证桌面、390×844 手机、进度点击与全屏进出；不增加播放器依赖。

字幕文件加载复用 `subtitles.to_webvtt`，接受 SRT、ASS、SSA、VTT，兼容已有的编码探测；4 MiB 以内的文件经 `/api/subtitles/convert` 转换后挂到当前 video 的 TextTrack，关闭播放器释放 Blob URL。字幕列表、CC 开关与外观选项按用户截图和 [YouTube 官方说明](https://support.google.com/youtube/answer/100078?hl=en) 组织；字体、字号、颜色、不透明度、背景、窗口与边缘样式保存在当前浏览器，时间轴仍由浏览器驱动。字幕文件不写 ledger，不保存到媒体目录。

字幕面板复用播放器的选项行、描边勾选、设置齿轮和悬停表面；在线入口按当前作品的番号或片名自动检索，结果保留在当前播放会话，失败可重试。详情原生 `dialog` 只处理自身的 `cancel`，文件输入框冒泡的取消事件保留详情与字幕菜单（[浏览器事件说明](https://developer.mozilla.org/en-US/docs/Web/API/HTMLInputElement/cancel_event)）。播放器模块加载期间只显示海报，原生控制条仅用于加载失败后的播放回退。桌面与手机回归覆盖首次加载、文件取消、菜单 Escape、结果加载及字幕外观。

在线中文字幕只采用 [SubtitleCat](https://www.subtitlecat.com)，复用现有 httpx 与 BeautifulSoup 依赖。来源选择参考 [PotPlayer JAV 中文字幕插件](https://github.com/736368217/PotplayerJavChineseSubs_subtitlecat/tree/0b569fdc3bd2cb27fca2140858ad77bf6d7ddcb8)（MIT；仅参考公开协议与语言选择，不复制代码）。番号检索复用 `catalog_rules` 的规范化、查询变体与身份比较，结合 `feeds.scan_code` 读取结果标题；仅保留同一发行身份，再按每页六条读取详情。标题支持紧凑番号、语言和版本后缀；日期分隔符与未经确认的发行前缀保持区分。片名检索保留用户关键词。只列已有 `zh-CN`／`zh-TW` 文件的结果，最多检查前 60 条，翻页由用户触发；不触发生成翻译。下载限固定 HTTPS 主机、字幕路径与 4 MiB，复用现有转码器。2026-10-18 固定 20 个公开番号均取得中文文件；该小样本不代表全站或馆藏覆盖率，也不证明翻译准确。界面说明可能包含机翻，不声称人工翻译。ASSRT 不接入；JavSub.ai 在同批样本中有 8 个中文条目，7 个需购买、1 个标为免费，但下载返回 403，故不作为在线来源。无需凭据，不增加依赖，不写 ledger 或媒体目录。

## 分级诊断

启动取证复用 Python 3.14 标准库的 `sys.excepthook`、`threading.excepthook`、`sys.unraisablehook`、`logging.Handler`、`faulthandler` 和 `zipfile`（PSF 许可，无额外安装体积）。`error_reporting` 在配置与第三方依赖之前加载，`support_bundle` 仅收集选定日志和系统字段；Peach 保留脱敏、备用目录、本机权限与报告内容策略。已有诊断报告用于页面和运行期导出。临时目录与独立子进程覆盖无控制台、导入失败、线程异常、备用目录、配置损坏和脱敏。

可选远程诊断复用官方 [sentry-sdk 2.72.0](https://github.com/getsentry/sentry-python/tree/2.72.0)（MIT，维护中，支持 Python 3.14）；固定版源码通过 `opensrc path pypi:sentry-sdk` 核对。SDK 负责后台 HTTP、TLS、Sentry 信封和服务端限流；Peach 负责本机选择、技术字段投影、同类错误聚合与每机每日上限。版本统计与错误诊断共用默认勾选的开关，关闭时不构造 SDK；SDK 全局自动集成、追踪、回放、日志、会话与指标采集均关闭。`HttpTransport._send_envelope` 的窄覆盖在真正发送前复查本机选择版本并移除本机传输元数据；它是固定版本的内部接口，升级必须通过真实 SDK 信封回归。无真实外发的内存接收端检查最终字节、停用后队列失效与断网不影响本机取证。新增依赖仅 `sentry-sdk`，复用已声明的 urllib3 / certifi；体积以发行构建为准。未采用自写 Sentry 协议或 Workers 收集后台，避免另行维护传输、去重和查看界面。没有真实 ledger 写入。

故障频率复用已固定的 `filelock`、`fsutil.atomic_write_text` 和 SDK 事件标签，保留 Peach 的同意版本、技术投影、跨进程计数与每日批次策略。`diagnostic_frequency` 保存有界技术样本及待提交增量，首次、每小时和正常退出通过同一 SDK 提交；关闭会清除聚合。Sentry 默认事件数统计批次，不会将自定义次数标签自动求和；汇总按事件的 `occurrence_day` 与 `occurrence_count` 计算。未开启 SDK metrics 或新增收集服务：当前需求是随技术诊断附带次数，独立指标通道会增加同意边界、额度与交付口径。计数只覆盖已筛选技术故障，发送及保留限制见 [诊断核验](DIAGNOSTICS.md)。没有新增依赖或真实 ledger 写入。

Windows 连接清理复用 [CPython 3.14.7 的 Proactor 对象协议](https://github.com/python/cpython/blob/v3.14.7/Lib/asyncio/proactor_events.py)（PSF 许可，无新增依赖）。精确识别纯标准库清理回调中的 `WinError 10054`，只补完 socket 关闭、服务端解除登记和关闭状态，不重复通知协议，不全局修改标准库类或替换事件循环。它使用 `_sock`、`_server._detach` 与 `_called_connection_lost` 私有接口，升级 Python 时须核验。真实 socket 和 `asyncio.Server` 配合合成故障验证资源释放；收尾自身失败保留原始异常链并上报。33 次回环实验未复现原始竞争时序，不据此认定客户端来源。协议类别只写本机日志；来源、任务、媒体和浏览器的远程筛选复用现有领域状态，见 [诊断核验](DIAGNOSTICS.md#远程错误筛选核验)。

馆藏与口味群体研究复用 `LedgerDatabase.read_connection`、规范编号、`entities.male_performer_ids`、`taste_evidence.era/length_band`、实体收藏快照、`error_reporting.redact` 与固定版 Sentry 内存附件 API。`library_research` 负责本机去重、用户信号、手动标签和分组评分直方图，这是 Peach 的领域投影；不读取浏览历史或写入偏好缓存。JSON 使用 Python 3.14 标准库 `gzip`（PSF，无新增依赖），固定文件名，gzip 头不含源文件名或生成时间。万条作品投影逐字节还原，真实 SDK 的内存传输验证最终附件、白名单与同意门槛。Sentry 附件接收复用官方 SDK（MIT）；没有引入匿名中继或独立聚合服务，本契约保留逐份投影并披露关联风险。字段、体积上限和验收见[馆藏与口味群体研究](DIAGNOSTICS.md#馆藏与口味群体研究)。

代码展示统一复用 `frontend/src/react/components/code-block.tsx`：BoardUI 已固定版本的 Button、语义颜色、排版与圆角，加现有覆盖式滚动条；包含语言标识、完整原文复制、可聚焦滚动区、剪贴板失败反馈与可选的屏幕截断。用于馆藏上传 JSON、CloudDrive2 TOML 和自定义来源 JSON。2026-10-19 核对 [BoardUI 官方目录](https://www.boardui.com/components) 与 `boardui list`，公开注册表没有独立 Code Block，故以现有原语组合 Peach 包装件；无需引入只为只读文本服务的语法解析依赖，也不复制 Pro 模板。组件测试覆盖原文复制与 HTML 文本安全，布局通过桌面和窄屏浏览器验证。无新增依赖，vendored 上游源文件保持原样。

实体收藏复用 `profiles.read_settings` 与 `library_favorites.favorite_entities`，集中处理规范身份、重定向、标签选择、数量上限与去重，供页面和群体研究共用。命名筛选及 HTTP 展示由网页层负责；数据层不依赖网页模块。

`src/peach/diagnostics.py` 统一生成 CLI 与本机 API 报告；复用 `settings_file`、`health` 的只读迁移校验、`FFmpegResolver`、访问策略和挂载快照。数据库用 SQLite `mode=ro`；写入与端口检查用标准库短暂探针，不新增运行依赖。

应用生命周期复用 `health.inspect_database` 的只读账本与迁移检查；账本可读且结构为 `current` 时才恢复任务、启动追更、订阅、推送发现、下载对账和馆藏聚合预热。账本缺失、空白、损坏或迁移未就绪时保留健康与配置入口，只记录不含路径和 SQL 的状态提示，不创建或迁移账本；账本准备完成后重启服务。`/healthz?ready=1` 的 `checks.background` 反映本次生命周期是否启动了账本后台任务；暂缓启动的实例在重启前保持未就绪。配置与隧道测试复用标准库 `unittest.assertNoLogs` 检查非预期的后台错误，故障注入用 `assertLogs` 核对预期原因，生命周期回归使用 `support.ledger.fresh_ledger` 的真实迁移模板。没有新增依赖或真实 ledger 写入。

聚合预热通过 `asyncio.Event` 接收关停信号，服务退出等待正在执行的 `asyncio.to_thread` 查询释放连接，并跳过剩余聚合。进入关停时 `checks.background` 为 `false`；临时 SQLite 连接与线程事件回归验证服务结束和账本释放的先后顺序。

浏览器网络失败取证复用 Chromium（BSD）的 NetLog 与现有 Playwright 启动入口；`PEACH_E2E_NET_LOG_DIR` 启用 `--log-net-log`，捕获模式固定为 [`HeavilyRedacted`](https://chromium.googlesource.com/chromium/src/net/+/master/log/net_log_capture_mode.h)，保留允许公开的网络事件字段，不采集 URL、主机、IP 或请求头。日志随浏览器正常关闭完成写入，由 CI 与 TAP 一起保存；失败请求仍使测试失败，不重试或过滤 `ERR_NO_BUFFER_SPACE`。番号样张夹具在首次导航前安装响应，直接打开目标页，并在主体等待失败时关闭 context。没有新增依赖或机器资源限额。

已核对 sakuramedia `a10fcef8`（GPL-3.0）的诊断字段与提示组织，仅作产品参考，不复制源码。OpenAver `8cc17e50`（MIT）的 HTTP 健康探针不足以表达分项状态。[fastapi-health 0.4.0](https://pypi.org/project/fastapi-health/)（MIT）在离线试验中将状态字典视为真值；CLI、分级规则和脱敏仍需本项目处理，因此未引入。

诊断页复用同一报告、TanStack Query、BoardUI 设置组合件。库健康参照 javm `dd0e6b62`（MIT）的计数→清单入口，采用 Peach 的资产、封面键与处理日志；作品清单复用现有 TanStack Table、BoardUI Table、排序与分页、跨页选择和复制动作，展示标题、文件名、番号、媒体库、完整路径、时长与大小。单组报告最多加载 500 项，完整计数与已载入数量分别显示。身份字段仅供本机诊断页面使用，分享诊断包继续排除库清单。来源证据复用 `performer_profile_followup` 成功落库来源与 `scraping_access.cooldown_state`，不复用 HTTP 200 作为内容健康。无新增依赖；本机只读试验已核对现有日志与成功记录。

系统诊断作为配置分区，复用配置页签及深链。检查和来源状态复用 BoardUI `Chip`；来源会话各自复用 BoardUI `SettingsCard`，由共用 `SiteMark` 读取已核对的本地 favicon 并提供失败回退，图标保留方形画布；近期解析、历史成功和无记录分别显示。会话列表展示资料平台与连接服务；片商页面及按片商选择站点的路由不单列。默认链之外的可选来源有实际检查或冷却记录时展示。图标来源见 `resources/site-marks/ORIGIN.md`。无新增依赖或 ledger 写入。

## 资源索引器

`resource_search.py` 使用 Torznab 0.2.1（MIT，Python ≥3.10，支持项目 Python 3.14）的 `parse_capabilities` 与 `parse_torznab`。源码固定标签 v0.2.1，项目仍标为 Alpha；离线 XML POC 已覆盖 caps、GUID 磁力、体积、做种、连接数和重复标签。PyPI 包新增一项直接依赖，requests 为已有传递依赖；实际索引器验收需要用户配置端点。

请求复用 HTTPX 0.28.1。SDK 自带 requests 请求层没有响应大小上限，异常可能带完整 URL，故只使用其解析器。Peach 负责每源 caps 和 search 两次请求、至多六份种子、响应上限 2 MiB、每轮最多四源、45 秒预算、禁用重定向、脱敏错误、XML 实体声明拒收，以及体积、做种、黑名单、质量排序和最多五个候选。标题番号复用 `feeds.scan_code`，身份与中字、无码版次复用 `catalog_rules`。凭据复用 `CredentialStore`，不登记跨机同步字段；换端点不会沿用已保存的 API key。

已对照 [Torznab 1.3 规范](https://torznab.github.io/spec-1.3-draft/torznab/Specification-v1.3.html) 与 SakuraMedia `torznab.py`（GPL-3.0，revision `9c6a31915c9a364c9d6717798575daf9445916bd`）的 FC2 查询、磁力字段回退与部分失败边界。SakuraMedia 耦合其 ORM，不作为运行时依赖。候选填入云下载弹层的磁力框，经用户确认后走 `submit_offline_download`。

OneJAV、Free JAV Torrent 只给种子链接。这类条目经索引器同源的下载代理取种子，由 bencode2 0.3.35（MIT，Python ≥3.10，有 cp314 Windows wheel 与纯 Python wheel）解码，再对 info 字典求 SHA-1。实证：真实 sukebei 种子算出的哈希与 Torznab `infohash` 一致；Prowlarr 代理对 OneJAV、Free JAV Torrent 返回 `application/x-bittorrent`。bencode2 和 fastbencode 0.3.11（Apache-2.0）都拒收键乱序的字典，重新编码就是原文。选 bencode2 是因为它有纯 Python 后备。torf（GPL-3.0）功能远超所需，bencode.py（BitTorrent 许可）、bencodepy（2015 年后停更）不采用。

这是实现查找表：每项能力由哪个现成实现承担、Peach 自己只负责哪一段。新增、恢复或重写代码前，按 `.claude/skills/peach-reuse-first/SKILL.md` 先查本文件、当前树、Git 历史和成熟外部实现；旧文件名不存在不等于能力缺失，继任关系见「已删除旧实现与当前继任者」。

安装依赖的精确版本由 [Python 清单](../pyproject.toml)、[Python 锁文件](../uv.lock)、[前端清单](../frontend/package.json) 和 [静态依赖清单](../package.json) 维护。本页记录用途、许可证与取舍；取证版本和提交号只代表对应证据。

## 挂载可达性

`mount_reachability.py` 参考 OpenAver 的 `core/source_reachability.py`（MIT，Copyright 2026 peace；固定 revision `8cc17e50453d9f69a81f5fee1a072df80f7aab73`，2026-10-01）采用正常 600 秒、异常 60 秒的节律、两次失败确认和超时在途去重。Peach 独立实现此算法，未复制上游源码；使用 Python 3.14 标准库线程、事件与 Future，不新增依赖。Peach 的路径映射与首条目录读取归 `platform`，来源接口、健康摘要和托盘共用纯内存快照；最多四个目录探测在途，后台线程不阻塞服务退出。

未采用参考实现的 UNC TCP 445 探测：端口连通不能证明共享目录可读。普通文件也不能作为媒体根，`NotADirectoryError` 单列；权限拒绝、目录不存在与其他读取失败分别报告。APScheduler 已用于定时任务，但其线程执行器不能取消阻塞的文件系统调用；本功能用停止事件驱动调度，保留尚未返回的目录探测。任务中心写入 ledger，不用于此只读探测。

## Board 界面与数值设置

全站 `title` 提示由 `board-tooltip.ts` 共用原生 Popover、Range 和既有滚动锚点判据；完整可见文字不复述，截断原文、图标名称和补充说明可查看。无自身布局盒子的元素按可见子元素定位，文字外链按实际文字定位；弹窗提示留在对应 dialog 内。播放器继续共用带快捷键的 Video.js 提示，无新增依赖。

关注图片瀑布流复用浏览器 [CSS Grid](https://www.w3.org/TR/css-grid-1/#auto-placement-algo)、`ResizeObserver` 和现有 React，不新增依赖。`use-masonry-grid.ts` 按列表顺序固定列归属，按卡片边界生成网格行；轨道数随卡片数增长，图片取得尺寸和续页只影响各自列的后续位置。卡片保留 DOM 顺序、键盘入口和选择范围。图片加载后立即使用固有宽高，固定比例卡面用 `cover`，详情完整显示。

已有实现与历史 `57c42c05`、`91f2c5f0` 提供尺寸占位与学习接口。CSS 多列的 [列平衡](https://www.w3.org/TR/css-multicol-1/#filling-columns) 不满足固定列归属。外部候选 [Masonry 4.2.2](https://github.com/desandro/masonry/blob/v4.2.2/package.json)（MIT、浏览器 DOM 库，依赖 `get-size`、`outlayer`）要求维护另一套定位与图片加载后的 [layout 调用](https://masonry.desandro.com/layout)，不采用。Peach 只维护列归属和测量，定位交给原生 Grid；桌面与 390×844 用例覆盖无尺寸、只读回写拒绝、延迟长图与续页。

页面控件与交互由哪些共用件承担；组件映射、Board 上游证据与许可证见 [BoardUI 适配](BOARD_UI.md)。

- 页面分区复用 `BoardTabs` 与遗留导航的中性填充选中态；显示方式开关使用 `Segmented`。标题位于内容左上角、导航与筛选之前。JAV 馆藏复用目录，推荐和想要的骨架由 `jav-skeleton.ts` 与页面共享容器、列数和封面比例。网盘标识复用 `SourceMark`、`MEDIA_SOURCE_ICONS` 与 `SourceLabel`，不增加图标依赖。
- JAV 推荐通过 `/api/feeds/discoveries` 的 `performer_refs` 匹配口味中的实体身份与规范姓名；引用只来自现有 `entities.resolve_entity`，撞名时不选取身份、不改写来源原文，不在前端维护姓名对照表。

- 外链按钮复用 BoardUI `ButtonLink`，调用处加 `data-button-link`，全站文字外链规则用 `:where` 排除它，保持实体页控件的样式优先级。浏览器验收使用一个渲染进程、进程内音频服务和合成音频输出，给演示服务与媒体进程保留资源预算；入口仍为 `scripts/test.ps1` / `scripts/test.sh`。

- 功能性动效由 `frontend/src/react/components/use-moving-surface.ts`、`modal-frame.tsx`、`selection-dock.tsx` 承担，用现有 Motion 14.0.0（MIT）与 React Aria Components 1.22.0（Apache-2.0）。Fluid Functionalism 只作行为与短程参数参考（[取证](reference-snapshots/fluid-functionalism.md)），不装其 Registry（额外的 Radix、字体与上下文不合 BoardUI 组合）；Peach 保留单一选中底板、键盘即时反馈与原有主题。
- 首页新作与实体资料页共用 `/api/entity/shapes` 和 `feedNewSkeletonHtml`；`home.feed` 按新作列表同一套未入库、未忽略与合集条件判定，有内容才留位；同步设置先于最终横条和网格，同形骨架复用节点。
- 首页、人物页和 JAV「发现」的新作头像共用 `feedIdentity`、`web_catalog.entity_ref` 与 `avatarInner`，携带实体图片版本和人脸取景，多人最多叠三枚，图片缺失时显示首字。已建档人物的头像通往规范资料页，不新增实体、头像采集或运行依赖。
- 作者别名管理复用 `@tanstack/react-table` 9.3.1（MIT）和 BoardUI `Table`、`DataTableFrame`、`DataTableFooter`、`AuthorAvatar` 与既有别名 API。两张表均支持全集排序、分页和跨页选择；待合并表以平台别名为行身份，已保存表以规范创作者键为行身份。批量合并与移除读当前清单的所选行，串行执行并报告部分失败；勾写 `slot={null}`。`frontend/test/react/follow-alias.test.tsx` 验证清单刷新、勾选、合并、保存与移除，不新增依赖。扫描与采集方式收在 BoardUI `Button` 与 React Aria `Popover` 下拉里，行外观复用 `menu-styles.ts`（见 `frontend/src/react/boardui/ORIGIN.md`）。
- 数据管理的孤儿记录与链接结果复用 `react/components/data-table.tsx`，由同版 TanStack Table 处理全集排序、行选择与最后一步分页，外观复用 BoardUI `Table` 与 `DataTableFrame`。七张表共用 `table-controls.tsx` 的分页底栏；关注列表保留卡片与表格共享的排序状态，其余表的排序控件复用 `DataTableSort`。默认每页 20 条，批量动作在选中后使用 `SelectionDock`。`use-table-selection.ts` 在同页表格间互斥选择，保持一份批量栏。孤儿记录批量删除复用 `/api/batch`；链接清理的可选 `ids` 必须属于 `check_id` 对应的失效清单，只重验和处理所选链接，未选行保留。组件回归与临时数据库验证跨页选择、清单刷新、确认和删除范围；不新增依赖。
- 关注列表分页用 `pagination.ts` 的页码范围与边界裁剪（React 与遗留层共用这份纯函数）；默认视图按创作者组，表格按来源。关注列表与 JAV 订阅源表格复用 `@tanstack/react-table` 9.3.1（MIT，https://github.com/TanStack/table ，按 ADR-0031「前端基础库」引入），兼容 React 18+ 与 Node 20+；传递依赖为 `table-core`、`react-store`、`store` 和 `use-sync-external-store`，精确版本由前端锁文件记录。按官方 [React 迁移指南](https://tanstack.com/table/latest/docs/framework/react/guide/migrating) 使用 `useTable`、`tableFeatures` 和带 features 类型的 `createColumnHelper().columns()`，注册分页、行选择与排序状态；固定列用 `getAllCells`，不接列隐藏、全功能集合或 legacy 兼容层。Peach 保留共用排序比较器、来源 ID（`getRowId`）、跨视图勾选集合、页码裁剪和批量写入策略；排序作用在全集，分页只切最后一步，频率、日期与数量按原始值排序。`frontend/test/react/follow-manage.test.tsx` 验证排序、取消选择、跨页跨视图勾选、批量写与清单变化。
- 窄屏筛选框共用 `filterScrollState()`、现有滚动帧调度和原生 sticky；同一方向累计 8px 再切换吸顶，保留文档占位与键盘可达性。
- 搜索玻璃用 `glideEase()` 的采样弹簧，`frontend/src/application/search-morph.js` 只管视口边界与轮廓关键帧。
- 横排滚到头的回弹是 `wireHorizontalScroller` 内的 `edgeBounce`，头像排、厂牌排、新作排、筛选条共用；transitions.dev 的 43 条配方里没有（2026-09-23 核对）。越界位移借 UIScrollView 的橡皮筋公式 `(1 - 1/(x·c/d + 1))·d`，c = 0.55，与 use-gesture（MIT）的 `rubberband` 同一条，回弹走 `--spring-pane`。只抄公式不引依赖：拖动与滚轮归属已由 `wireHorizontalScroller` 判定。新作排自动滚动是同文件的 `wireAutoScroll`。
- React 横向导航通过 `useHorizontalScroller` 绑定上述共享行为，适用于页签、分段控件、筛选栏和首字跳转；`data-horizontal-scroll` 隐藏原生滚动条，左右渐隐由实际溢出方向决定。容器和子项尺寸、文本及增删变化都会重算，竖向分类列关闭绑定；表格和代码块继续使用覆盖式滑块，不新增依赖。
- 管理页头及 `manageHeaderSkeletonHtml` 共用一级导航、面包屑、页面标题的顺序，页内分类放在标题之后；管理条保持跨模块导航，面包屑保留上级链接与当前页语义。
- 浮层筛选首页是页面内组件 `catalog-filter`，资料页是 `entity-filter`，共用 `FilterGlassRows` 两排、交集条与排序键，滑动玻璃走 `use-view-glide.ts`。外框管玻璃与吸顶，壳管查询状态和取数，岛只画、动作回壳，按下态在发请求前由壳经 `updateManagedRoute` 推到；身份与观看状态的组合沿用 `/api/items`。
- `web/board.css` 共用正式页面结构。首启、登录与错误三页是独立页面包 `/dist/peach-pages.js` 里的 React 页，服务端只吐 `web_entry.page_shell()` 那一张薄壳，外框是 `frontend/src/react/pages/auth-card.tsx`，SPA 外壳之外的新页面挂进同一个包。
- `frontend/src/number-setting.ts` 共用带单位输入、可选 Switch、整数边界和锚定错误提示；关闭保留上次合法值，异步读取后切换也恢复实际值，保存归调用方。
- 筛选内层的标签胶囊是 `FilterPill`，换一批、排序键与交集条是 `entity-filter-page.tsx` 导出的同一组组件，页面各自提供查询键与读数；`collectionHeaderHtml` 只剩资料页骨架那一排读数。横向行的拖动、滚轮、渐隐与卸载清理归 `wireHorizontalScroller` 同一个生命周期。
- 选择范围与工具条用 `frontend/src/selection.ts`；馆藏、关注与复核保持各自身份、可见顺序、默认选择与写入权限，批量失败项的保留归业务。
- React 设置分区用 `frontend/src/react/settings/section.tsx`：`Section` 给标题、卡片与表单外壳，`Footer`、`Note`、`ErrorText`、`FactList`、`Progress`、`Disclosure` 补齐 BoardUI 注册表没有的部分。`service-preferences.tsx` 组合缩略图与订阅采集参数，继续使用已有设置、关注计划和缩略图 API。`use-action.ts` 的 `useAction` 管提交互斥、卸载取消与原位错误，`busy-props.ts` 的 `busyProps` 写忙态；媒体目录行（`folder-rows.tsx`）与密码加确认两格（`password-pair.tsx`）由配置页与首启页共用；密码校验与服务端回执归各分区，不自动重试写入。口味读取、封面采集、amane 桥重建、关注检查与查找共用 `frontend/src/react/background-job.ts` 的 `useBackgroundJob`。
- 增量列表用 `catalog-grid/catalog-grid-page.tsx` 的 `LoadMore`：请求互斥、失败留原位重试、卸载即停；目录、资料页照片墙与关注页注入读取和可用条件。首页页码在读取成功后推进，照片沿用随机种子，关注合并分组；显式页码用 `pagination.ts`。
- 在图上框一块由 `frontend/src/crop-geometry.ts`（纯算术）加 `react/crop/crop-frame.tsx`（四块压暗、一个可拖可缩的框）承担，换头像与裁封面共用；坐标一律是源图像素、右下开区间。不引 `react-image-crop`：换到的只有把手样式，代价是一条依赖和它自己的坐标约定。裁封面通过 Application 的媒体域动作连接 React 裁剪组件（ADR-0031）。
- 后台任务用 `watchJob`、`followJobProgress` 默认面板与 `jobActivityHtml` 的真实计数／未知总量显示，关注、来源扫描、链接检查和扫描采集共用；业务保留启动、终态回执和结果面板，不新增轮询循环。任务本体是 `jobs.BackgroundJob`，不加队列或调度依赖；浏览器由 `frontend/src/jobs.ts` 跟进，刷新后经读接口恢复。可恢复的采集任务由本机恢复记录重放，其他任务只保留终态；`BackgroundJob.update(job_id)` 报阶段与计数，查询不重新执行任务。
- 遗留层反馈控件 `noteHtml`、`progressHtml`、`gaugeHtml`、`projectBannerHtml` 与 `wireContextCard` 集中在共享 UI 模块；信息卡片用原生 Popover 与现有锚定菜单定位，不加浮层依赖。证据见 `reference-snapshots/vercel-geist-note-progress-switch-analytics.md`。
- 资源核对复用 `web_resource_sync` 的目录枚举、离线跳过、写前逐条复验与 BackgroundJob，覆盖 local、115、PikPak；失效记录的永久删除复用 `web_batch.purge_assets`，文件仍在盘上的行进 `blocked`。确认弹层复用 Fieldset、Note、Toast 与 confirmModal：失败留在弹层，危险动作初始聚焦取消，忙态阻止重入与关闭；不引入另一套对话框库。
- 新界面走 BoardUI／React（ADR-0031），遗留层共用的 Geist 控件随页面迁走，不引入 Geist React 运行时。覆盖式滚动条 `attachOverlayScrollbar`（滑块不占宽度）、Collapse 的 `wireCollapse`／`setCollapseOpen`、Geist Select 的 `selectFieldHtml`／`wireSelectField`、来源站标 `MEDIA_SOURCE_ICONS`，以及模板、骨架、确认框等其余共用控件的唯一实现在 `frontend/src/ui-kit/`，Application 与 React 页面直接引用共享源码，随 `/dist/peach-app.js` 发出一份；独立页面包只打入用到的控件；覆盖式滚动条与 `.ui-geist-scroller`（只给两端渐隐）可叠加；整页异步重绘复用导航代际隔离。
- 局部等待复用 `skeleton-sheen`：绝对定位的微光由占位元素自己的 `relative` 或 `absolute` 定位，并在该元素内裁切。JAV 评论占位在折叠正文内预留结构，首次展开按占位高度测量；请求仍按展开触发，收起时不标记等待态。
- 图标按钮统一清除浏览器内边距并居中 SVG，不覆盖业务显隐。Remix Icon 由 `vendor_web_dependencies.mjs` 生成设置导航 symbol；随机按钮保留原有双路径动画。
- 宽字形的比例统一取自 `core/icon-geometry.ts`；遗留模板、React 控件、设置和实体页共用。控件按位置决定高度，Aa 等字形按比例定宽，同级图标以实际轮廓核验。
- 统计页与口味页的柱状、径向、雷达图复用 EvilCharts Recharts 分支（MIT，源码逐字复制进 `frontend/src/react/evilcharts/`，依赖 `recharts`、`motion`、`clsx`），单系列柱状图统一走 `frontend/src/react/charts/bar-card.tsx` 的 `BarCard`，悬停浮层统一走 `charts/chart-tip.tsx` 的 `ChartTip`。星期 × 小时与每日热力图是 `charts/heat-card.tsx` 的 `ActivityHeat`，统计页播放时间与口味页浏览活跃共用；EvilCharts 没有这类图（ADR-0076）。
- `frontend/src/react/taste/taste.ts` 使用 d3-sankey（BSD-3-Clause）及其类型包计算来源网站到创作者线索的流向。布局依赖不读取浏览历史；Peach 提供去重聚合值并负责隐私边界。分发许可随 `web/vendor/d3-LICENSE.txt` 保留。

复核用 Checkbox、Button、Badge、Select 与 `/api/review/decision`，默认勾选、沿用馆藏页 Shift 连选；支持跨组通过／拒绝与无歧义的共同来源选择。按候选数量、来源组合或字段分组，每项只出现一次，切组保留选择；成功移出、失败保留，离开页面即停。整页在 `frontend/src/react/review/`，一条队列一个 `queryKey`，判定后改缓存不重取。批准回执在同一事务中读取实际资产版本，缓存只推进仍持有该次写入前版本的候选；逐项与批量提交共用缓存中的并发凭据。身份候选用创作者页、作品详情与 revealSource 核对样本；来源图与缺图占位共用 220px 预览区，样本支持无缩略图作品，回收站不参与。

本地 NFO 配对复用 `library_nfo`、`catalog_rules` 的番号与分卷识别、标准库 ElementTree 及现有资料补空流程。[Kodi NFO 文档](https://kodi.wiki/view/NFO_files/Movies)的同名文件与 `movie.nfo` 约定于 2026-10-17 核对；只参照公开格式，没有复制外部源码。Peach 保留同番号分卷共享、同名文件优先、多份匹配拒绝、内容番号冲突检查和输入安全限制。Kodi 整套媒体中心没有作为依赖引入；标准库随 Python 3.14 运行，无额外体积或许可证。NFO 读取后复用来源缓存与缺失缓存核查在线字段；`requires_review` 跟随候选保留覆盖复核要求，人工决定保存已展示的候选身份，见 ADR-0108。隔离媒体与账本回归覆盖共用 NFO、不同作品混放、错误 NFO 继续在线补全、补空与覆盖分流，以及连续批准的版本回执；真实反馈的 NFO 样本尚未取得，无新增依赖。

## 本机设置与卸载

- Peach 代理复用 HTTPX 的 `trust_env`、`proxy` 与本机 CredentialStore；来源只选择公共策略或直接连接，地址不回传。单一旧代理可继承，多个地址需明确选择。
- 自启用 Windows WScript.Shell 快捷方式（[微软文档](https://learn.microsoft.com/en-us/troubleshoot/windows-client/admin-development/create-desktop-shortcut-with-wsh)，TargetPath、Arguments 与 Save，自带 COM，不引 pylnk3）和 macOS LaunchAgent；临时中文路径的真实快捷方式读、写、移除通过。
- 独立包卸载用正常托盘退出和 Windows PowerShell 助手；计划限于程序标记、数据直属目录、媒体不重叠，助手拒绝目录链接，会退出仍从程序目录运行的进程并重试删除。完全卸载只把 `config.toml.<说明>-<日期>-<时刻>` 当 Peach 写的设置备份，手工的 `config.toml.bak` 保留；混入被占用文件、数据、媒体与无关文件的真实输入验证只删计划内容与整个解压目录。源码树只给手动卸载说明。
- 扫描与采集统一挂在数据管理；常驻任务卡跳转同一入口。默认排序与方向使用浏览偏好，显式 URL 优先。

目录页进度横幅、缺预览提示与数据管理卡片共读 `LIBRARY_PROCESSING_KEY`（[共享状态](FRONTEND.md#共享状态怎么写)）；启动只提交一次，状态查询接续托盘首次处理。首页进度在完成后收起，失败跳转数据管理；数据管理持续读取阶段与真实计数。Geist Banner 取证与 Peach 差异见 [docs/reference-snapshots/vercel-geist-library-banner.md](reference-snapshots/vercel-geist-library-banner.md)。

缺预览提示复用 `cardArtwork` 的可用性判据，目录、实体作品区与详情共用 `LibraryCompletionPrompt` 和 `ProcessingActions`。`/api/library-processing` 的 `collect` 阶段补资料与本地预览，`previews` 仅生成本地九宫格，`metadata` 仅采集资料；可选 `asset_ids` 限定已存在影片，空数组拒绝，全库扫描不用于定向处理。九宫格复用 `frame_capture`、FFmpegResolver 和 `/poster`，本地阶段先于外部查询执行，沿用任务日志和失败重试；不增加依赖。浏览器回归使用临时演示库和拦截响应验证默认动作、下拉、焦点归还及完成后的卡面更新。

## 独立测试包在线更新

- 任务起跑与安装预约复用已固定的 filelock 4.2.0（MIT，Windows/macOS/Linux，无额外依赖）和原子 JSON。`update_tasks` 在同一数据目录内串行登记任务和预约安装，覆盖各服务进程、任务中心、后继领取和 `job_main` 批处理。安装请求触发采集任务在检查点保存并暂停，任务中心保留 `pending` 行；新采集请求进入等待。安装入口等在途步骤保存进度，随后停止自己的服务，不要求用户等待整轮任务结束。源码批处理保留 PID 锁，写完在途批次后等待安装，结束后由原进程继续。
- 安装器在临时目录展开随包运行时，以 `--installer-prepare` 承接馆藏处理状态并暂停任务；预约归安装器持有，完成或取消时释放。在线更新将预约交给更新助手，切换目录和迁移完成后释放，失败回滚也释放。恢复轮询复用 APScheduler，安装取消时现有服务也会接回任务。不采用新任务队列或更新框架：任务语义、PyInstaller 目录切换和 Inno Setup 生命周期继续由已有实现承担。
- `job_recovery` 保存馆藏处理范围、阶段、指定影片、时间轴密度、单部封面、关注、订阅和想要资料的请求；服务启动或安装结束后只领取未完成记录。暂停任务复用任务中心的同一行，已完成、失败和主动停止的任务不循环重试。馆藏预览在产物登记后保存影片检查点，元数据检查点跟随候选 CSV 成功落盘；未取得结果的项目不记完成。时间轴复用已有索引判定。恢复领取和新任务起跑共用安装闸门，数据仍留在本机 state 目录，不引入 ledger 迁移。

- 自动更新设置用 APScheduler（见「必须复用」表的定时轮询）、共享 HTTPX 发行查询、filelock 与原子 JSON 写入，安装走 `standalone_update`。按 [interval trigger](https://apscheduler.readthedocs.io/en/3.x/modules/triggers/interval.html) 每分钟查是否到期，本机持久时间与文件锁协调多个服务；关闭、6/24/168 小时间隔与源码下载限制归 Peach。默认关闭，下载只准备安装，重启仍需确认；设置存 state 目录的 `automatic-updates.json`。
- 版本与资产信息用 [GitHub Releases REST API](https://docs.github.com/en/rest/releases/assets)（测试通道含预发布），查询与流式下载用项目 HTTPX，ZIP 解压用标准库，互斥用 FileLock。只有已上传完整独立包的发布才进更新，优先选择 `*-update.zip`，历史完整 ZIP 保留原名供旧客户端更新。`package_windows_release.py` 从同一构建目录生成完整包、应用更新包与 FFmpeg、untrunc、cloudflared 工具包；更新包内清单固定工具文件哈希、工具包大小与 SHA-256，运行时只复用校验相符的文件，按需下载不符的工具。工具包路径限于对应工具目录，补齐后再次校验并沿用同卷暂存与目录回滚。复用已有 HTTPX、hashlib、zipfile、copy2，无新增依赖；不引入 Velopack 的包格式或二进制差分机制。隔离回归用实际生成的 ZIP 验证工具复用、独立补下载与失败保留现有安装，不使用真实 ledger。
- 安装策略与进度归 Peach：下载校验后在程序同卷暂存，用户确认重启，复制出来的包内助手等原托盘退出再切换完整目录；失败保留或恢复旧目录。配置、数据库与媒体不进更新包。
- 轻量安装包复用应用更新包的工具清单、`standalone_update.prepare_tools` 与既有 HTTPX、hashlib、zipfile。Inno Setup 6.7.3 用 [external 文件](https://jrsoftware.org/ishelp/topic_filessection.htm)安装临时目录中已验证的工具，用[滚动进度页](https://jrsoftware.org/ishelp/topic_isxfunc_createoutputmarqueeprogresspage.htm)显示准备状态；官方 API 已于 2026-10-17 核对。工具先在预检目录准备，成功后才暂停任务并覆盖安装；安装包旁的工具 ZIP 同样按清单校验，不读取馆藏配置或增加依赖。不引入另一套下载器或更新框架，校验及解压路径与在线更新共用。
- 不用 [Velopack](https://docs.velopack.io/packaging/operating-systems/windows)（要求其安装目录与包格式）与 [WinSparkle](https://winsparkle.org/)（要求 appcast 与原生更新界面）：产物是 PyInstaller ZIP、进度在 Web 显示，沿用托盘进程与目录替换协议。
- Web 复用 Fieldset、Progress、confirmModal；状态由 `standalone-update.json` 保存。下载按字节计量，解压按文件数计量，替换使用阶段进度；服务重启期间保留等待状态，恢复连接后核对版本。
- 新版有待应用迁移时，重启安装复用 `migrate upgrade --yes`，先保存 SQLite 备份；迁移或启动失败时恢复数据库与程序。数据库备份位于用户 Peach 数据目录的 `state/update-backups/`。
- 更新事务按当前配置读取 state 与账本路径，助手显式继承所选 Peach 数据目录。已有数据目录恢复复用严格配置解析与位置记录，只接受含配置和账本的目录。`scripts/smoke_desktop.py` 验证恢复及再次启动；`scripts/smoke_online_update.py` 使用真实 Release 包和临时馆藏验证在线更新，可注入无法启动的暂存 EXE 验证回滚。
- 覆盖升级验收复用 `smoke_desktop` 的首启配置、临时 Peach 数据目录、登录和进程退出；`scripts/smoke_overlay_upgrade.py` 使用标准库下载并校验已发布制品，分别走 ZIP 原位解压和 Inno Setup 原位安装。安装包只在没有 Peach 安装登记的 Actions runner 上验收，覆盖安装器停止旧进程、目录记忆、配置与密码保留、账本哨兵、普通启动及卸载保留数据；不新增依赖。

JAV 默认封面（官方封面／预览图）与视频默认大小（大图／小图）独立保存，用 localStorage、共享 Switch 与既有 `/cover`、`/poster`。设置弹窗同步首页与 JAV 的版式，筛选条可单独调当前视图。`frontend/src/jav-artwork.ts` 管作品身份、偏好恢复与缺图回退，首页、接着看、实体作品、详情推荐、Mix 静止与翻图、播放队列共用封面选择。小图展示完整封套，竖版正封完整放入大图卡片；新作封面完成取景才显示，缓存图插入时重算取景。

关注视频与馆藏详情的续播复用 Video.js 10.1.0（Apache-2.0）的元数据事件和 `currentTime`、浏览器 localStorage 与既有活动接口。`player/progress` 按本地资产、关注条目和媒体序号登记秒数，暂停、定位、关闭、小窗销毁、页面退出均保存；原生 video 回退也恢复，明确的时间深链优先，放完从头开始。本地既有记录可按 `leave_ratio × duration` 恢复；最远观看位置与累计观看时长不作为续播秒数。活动请求用 keepalive，销毁同步冲掉末段并清理定时器与监听。此状态属于当前浏览器，未引入账本迁移或跨设备同步，无新增依赖。

详情人物头像复用 `card-art.entityFaceImg` 与 `avatarFrame`，图片直接挂在有尺寸的 `[data-id-face]` 圆框内。取景读取图片的父框几何，`display:contents` 包装层没有可用尺寸。首字与图片由同一段 HTML 承载，图片加载失败时共用回退链，浏览器回归覆盖带人脸框的桌面与手机详情。

新作封面复用 `best_cover` 的官方候选与 `cover_artwork.install_cover`，保存本地封面、正封框、人脸坐标与来源证据。JavDB 图片仅作来源复核证据，不进入安装、封面索引、详情封面投影或缺图兜底；`scripts/quarantine_javdb_covers.py` 将有来源证据的历史图片及边车移入可恢复归档。未取得合格来源时显示未取得状态，详见 ADR-0107。

无快照的有码作品由封面入口复用现有 `DmmSource` 的 GraphQL 查询与精确身份核验；成功快照直接展开 DMM 图片候选，r18.dev 与 DMM 使用同一份目录证据。FC2 来源快照通过 `storage_original` 提取发行方存储原件，排除社区图床。目录查询沿用来源冷却，HTTP、网络与解析失败分别保留未取得状态。

MGS 专属发行先查自营商品目录，Prestige 番号复用 `metadata_routes.maker_sites` 路由后汇总集团官网与 MGS 图片；手上已有候选仍一起比较实际尺寸。专属目录未取得候选时才查综合目录，避免无关目录故障消耗整项预算。

正式封面和商品图以像素面积择优，同尺寸优先商品图，剧照预览保留末档；图片角色不让低清商品图覆盖更大的官方封面。

只有厂牌图片的快照继续补查 DMM 目录；成功的 DMM 来源快照才省略目录查询，按厂牌编号构造的 CDN 地址和 r18.dev 的缺失记忆都不替代 DMM 查询。

人脸缓存使用 `cover-face-v4` 与源图尺寸校验。`detect_cover_faces.py` 自动重算过期记录，逐图核查加 `--refresh-faces` 可在重算整图主脸后再决定版式；无可信书脊的 4:3、√2:1 画布或左侧主脸明显占优的大封按整幅画面取景。安装、批次重算与恢复默认取景读取同一版式标记。

传统正封完整居中，比例留白复用同图派生档的模糊补底，新作与视频流卡片使用同一份取景规则。整幅大封使用卡片纯色底，竖版按主脸填满，横版完整放入容器；图片切换时清理残留背景属性。

取景由 `poster-crop-v13` 管理。Blu-ray 来源标题、明确的版次标签与图片比例共同决定 0.85 正封比例，标题随正封框保留，重算人脸与正封框均读取同一版次；有效宽正封框在没有版次标题的卡片上也完整显示。VR 标记、IENEE、IENFE、SDHS 数字发行拼图，以及可信主脸跨过 DVD 正封先验边界的大封保留整幅画面，由 `cover_frame.layout=still` 传给卡片。两条满高接缝夹出居中正封且两侧纹理均低于正封四分之一时，只显示中间正封。大封在竖版大图按主脸居中填满，横版小图完整显示，均不使用模糊补边；JPEG 软边的书脊识别结合突出度、左忙右静的方向与正封侧基线。手工框保持用户取景。重试队列按最早尝试时间轮转，收起的合集沿用设置跳过；逐番号结果保存在 `feed-cover-attempts/`。复用现有 HTTP、Pillow、OpenCV 与文件锁，无新增依赖。

馆藏与新作取图共用 `cover_artwork.metadata_snapshots` 和 `best_cover`，片商官网、发行方、R18 与 DMM 的官方候选均参与尺寸比较。馆藏采集将长边 1600px 作为高清重探界限，社区转存图可由官方封面替换；同类官方图按像素面积升级，FC2 保留商品图的来源保护。`scripts/audit_cover_artwork.py` 逐张核对尺寸、来源、版次与取景框，输出 CSV；`scripts/upgrade_audited_covers.py` 按已有官方快照逐番号重探，`--live` 经同一官方链在线补查，保存原件备份与结果检查点。两者只处理可重建图片和边车，不写 ledger。

## 复用决策门槛

每次决定「用现成的还是自己写」都按这四条走，本节其余各条是按这四条做出的具体决定：

- 先用真实输入做无写入 POC，再决定「直接依赖、固定来源实现、保留自研」三者之一。
- 采用项要记录固定版本、许可证、首个消费者和 Peach 保留的领域边界；候选依赖不得空转。
- 保留自研要记录被拒绝的候选和不可替代约束，不能只写「特殊需求」。
- 外部项目不适合作为运行时依赖，但其公开数据模型或算法明显更成熟时，固定 revision 后作为参考
  实现；许可证不允许派生或来源不稳定时只作行为证据，不复制代码。

- Python 安装与构建用 [uv](https://github.com/astral-sh/uv)（MIT/Apache-2.0，`pyproject.toml` 只设下限 `>=0.12.13`，不进依赖图）与官方 setup-uv（MIT，版本见 [测试工作流](../.github/workflows/test.yml)）：uv 项目接口、`uv.lock`、`uv sync --locked`，Dependabot 走官方 `uv` 生态。开发与构建在隔离工作树建环境，生产 venv 不参与精确同步；直接依赖精确固定，传递依赖由锁文件复现。
  测试库复用 `tests/support/ledger.py`：迁移生成模板后复制独立临时库（[耗时](SOURCING.md#依赖与工具实测)），真实迁移测试仍执行迁移；重试测试用已有 sleeper 注入点。CI 用 GitHub Actions 独立 runner 分片与现有 unittest 入口，Peach 只维护影响域策略，不引并发测试框架（见 `TESTING.md`）。
- 访问密码复用 Python 3.14 的 [hashlib.scrypt](https://docs.python.org/3.14/library/hashlib.html) 与 OpenSSL，浏览器会话复用 [ItsDangerous](https://itsdangerous.palletsprojects.com/en/stable/)（Pallets、BSD-3-Clause、纯 Python、wheel 16 KB、无传递依赖），本机原子配置写入用 tempfile/os.replace 与 filelock；认证入口的访问口令、三种拒绝响应与本机配置守卫照旧复用。Starlette SessionMiddleware 统一时长、随响应续期，满足不了每台设备自选截止时间，所以直接用同源签名库，Peach 管可选密码、截止时间与撤销，不带账户体系与迁移。
- Cloudflare Quick Tunnel 用官方 `cloudflared`（Apache-2.0），不自写隧道协议。源码环境只管 PATH／环境变量里的进程；Windows 独立包旁路固定 `2026.9.0`，资产、地址与 SHA-256 在 `scripts/cloudflared-windows.json`，`fetch_cloudflared.ps1` 与构建脚本双重校验。就绪判据是官方 `--pidfile`（首次连上边缘才写），未连上不宣称可用。源码 HTTPS origin 用项目 CA，独立包用回环 HTTP，写请求来源校验带当前进程持有的随机 URL。

- README 交付检查用系统 Git 的 `diff --no-renames -z` 与 [`interpret-trailers --parse`](https://git-scm.com/docs/git-interpret-trailers)，挂在 `agent_worktree.py ready/integrate`；Peach 只定义影响文件与双语声明 policy。原生 hook 不随 clone 安装、判断不了文案语义，所以挂在共有的交付入口上（[README 维护](README_MAINTENANCE.md)）。
- 配置访问判定用 ASGI 连接的 `client` / `server` 地址与标准库 `ipaddress`：本机连接匹配回环地址或服务端 IP，Host 只校验托盘配置的域名或绑定地址、不证明调用方在本机。配置保存用修订号校验、原子替换与 `SetupGate` 重载标记，托盘子服务以 `PEACH_TRAY_MANAGED` 声明重载能力。安装检测用 `winreg`、`shutil.which` 与安装目录，媒体工具用 `FFmpegResolver`，脱盘不判为未安装。下载入口 2026-09-06 核验：[CloudDrive](https://www.clouddrive2.com/download.html)、[WinFsp](https://winfsp.dev/rel/)、[macFUSE](https://macfuse.github.io/)、[FFmpeg](https://ffmpeg.org/download.html)、[OpenSSL](https://openssl-library.org/source/)。
- 发行身份用各来源解析器与 Seesaa 的原始 DTO，数字前缀等价是 Peach 的领域 policy。查询回退不能承担身份确认（[`390JAC-040` 实例](SOURCING.md#依赖与工具实测)），响应按原始查询检查，MGStage 官方详情路径可佐证展示编号别名；外部适配器只取值，首条搜索命中不作 ledger 身份断言。
- Seesaa 作品表用 HTTPX、Beautiful Soup 4.15.0（MIT）与现有 `HostLimiter`、番号规范化、字段候选和快照协议。Javinizer-Go `d9724f239d7e127afcb747fa8ce4358685912f50`（MIT）与 MetaTube `6a5e6128c725187aeaf921d48ed7d9cd9f30671b`（Apache-2.0）都没有 Seesaa 适配器（2026-09-06 核对），所以 `peach.sources.seesaa` 承担 EUC-JP、列映射、精确行身份与未知名单保护，消费者是 `scrape_codes --profile seesaa`（[站点细节](SOURCING.md#seesaa-wiki-作品证据)）。

- 补女优别名后继（ADR-0055、0061、0064）复用 `minnano_av` 检索与资料表解析、`sources.seesaa.WikiPages`、
  `sources.fc2cmadb` 女优栏、`HostLimiter`、`scraping_access` 冷却、`metadata_alias_resolve.is_planning_alias` 与
  `apply_alias_candidates.py` 的四种不写口径，撤回用 `revert_auto_landing.py`。minnano-av 不用 `page_cache.Site`
  （丢跳转后的最终地址、限速器按实例各起、会把 200 的验证页当正文缓存），由 `MinnanoPages` 记最终地址、共用一个
  `HostLimiter`、认出验证页不缓存并记冷却；传输是 `SourceTransport`，被拦时按 `browser_fallback` 走
  `browser_transport.shared`（见 [来源采集](SOURCING.md#minnano-av-的间歇拦截)）。
- 补女优资料后继（`performer_profile_followup`，ADR-0067）复用 `minnano_profile_pages`、名字核对与 `MinnanoPages`
  （加 `source` 冷却键与 `max_age`，avwikidb 与 javdb 也用），排单用 ADR-0053 的 `Attempts`（写成的记号保 30 天）。
  解析在 `minnano_av.profile`（资料表）与 `avwikidb`（JSON-LD），读写在 `performer_profiles`。`kanojo-db/scrapers`
  与 `stashapp/CommunityScrapers` 的 Minnano-AV 规则都不是可装的库（后者是 Stash XPath 配置，ADR-0021）。
  女优页头（ADR-0069）由 `performer_header` 出五项与别名分组，別名栏注记由 `minnano_av.name_entries` 拆，
  拆名字用 `sources.seesaa.split_names`。

文档检查用 seiso（MIT，开发依赖），[文案门槛](../tests/test_copy_final_state.py) 调用其稳定规则；文件分类与第三方原文边界见 [seiso 配置](../seiso.toml)，表达与人工复核见 [文档与界面文案](WRITING.md)。不自建 Markdown 文档职责解析器。

测试与集成用 `test_runner.py`、`agent_worktree.py`，进程互斥用开发依赖 `filelock` 的 [`FileLock`](https://py-filelock.readthedocs.io/en/stable/tutorials.html)。全量验证与集成共用一把锁、最多等待 30 分钟；验证持锁至记录写入完成。临时 Git 仓库回归覆盖互斥、等待和释放；代码、环境和范围记录属于 Peach 的集成约束。记录失效返回 `4`，Windows 托盘据此最多重试三轮，用例失败返回 `1`。两端测试入口通过 `test_environment.py` 复用现有 `find_uv` 与主检出 `Dependencies.check`；隔离工作树使用 uv 官方锁文件同步，不新增包管理实现或运行依赖。同步失败停止测试，主检出只读检查；策略与行为回归见 `test_test_environment.py`。

定向调试复用标准库 unittest 的按名字加载与现有浏览器夹具，记录复用沿用文件清单、影响域和 FileLock，不新增测试框架或缓存服务。局部成功记录保留未受影响域的原验证时间，失败域不能从历史基线恢复；浏览器失败即停。回归覆盖过期、环境差异、未知与共享文件、失败后再次验证、路径越界及不签调试证明，详见 [测试](TESTING.md#定向调试与局部记录)。

提交消息复用 Git 的 [`interpret-trailers --parse`](https://git-scm.com/docs/git-interpret-trailers)，署名与 README 声明判据由 `co_author.py`、`check_readme_impact.py` 共用；`check_commit_message.py` 接到仓库 `commit-msg` 钩子。Git 自身处理注释与多行 trailer，Peach 只检查字段形态和分段。Windows Git 2.55.0.windows.3 的临时仓库回归覆盖拒绝提交、保留暂存、人类与智能体共同署名、amend 和合并消息；不新增解析依赖。具体触发范围见 [README 维护](README_MAINTENANCE.md)。

### 播放、采集、发布与打包

各条的实测与取证见 [复用取证记录](SOURCING.md#播放与控件实证)。

- 页面、框体、操作条与选中项共用一套灰阶，操作条用 `--overlay-5`。
- javdb 属有码与素人链的社区档，候选保留 community 来源性质，不自动写真相字段。
- [JavDB Web UI 实证](reference-snapshots/javdb-web-ui-poc.md)：库外作品详情复用舞台、人物身份、想要、`WantMagnets` 与云下载组件；`javdb_public.PublicAPI` 复用 `HttpxTransport`（httpx 0.28.1，BSD-3-Clause）、`HostLimiter`、Pillow 12.3.0 与标准库 MD5/XOR，独立映射公开协议。匿名详情、两页评论、6 条样本资源、封面与预览图像素已取得；全站完整性和不限流未证明。请求串行、3 秒间隔、6 小时有界缓存，评论最多 8 页，失败保留资料并冷却；发现复用持久资料边车和固定集合补齐，传输层预算由 `ContextVar` 隔离。未新增依赖或写入 ledger。
- 库外 JAV 身份引用复用 `entity_ref` 的本地头像与取景、厂牌 `has_logo` / `logo_version`；来源演员头像按作品演员 ID 取回并由同一 Pillow 路径解码，未知演员不绑定其他实体。性别只投影来源明确字段或规范资料，未知值保留 `unknown`；`MIDA-361` 的一位女优和四位男优与原站演员标记一致。标签投影复用 `genre_decision`、`genre_taxonomy.resolve_genre` 与 `tag_cat`，`tag_refs` 保留来源原词。样本 `MIDA-849` 的两张来源演员头像完整解码成功；其 HLS 清单 GET 200，密钥与媒体 HEAD 200，均允许匿名跨域访问。`MIDA-361` 的 HLS 在隔离实例中实际解码为 1024×576，播放按钮推进超过 2 秒，暂停、设置与卸载通过；未保存媒体帧或视频文件，整段播放未验证。预览仅下发已验证 HTTPS 主机与路径，播放复用既有 Video.js；无需新增代理或依赖。外部候选与版本依据沿用上条只读实证，Peach 保留身份、来源和请求预算策略。
- 候选 `ANDonekey/javapi` revision `9a2a245a659a4008ee75bc148b0af9c7126e7a71` 未归档、最后推送为 2026-05-11，使用 Go 和部署服务，GitHub 未标明许可证，不能作为 Python 依赖或复制其源码。仅以部署公开前端与匿名实测确定协议事实；原始取证脚本及脱敏概要保留在工作树 `build/javdb-public-api/`。`phoenixthrush/javdb-python`（MIT、Python ≥3.11、无运行依赖）解析的是 `javdatabase.com` HTML，站点与协议不匹配；现有 `sources.javdb` 同样只适用于 HTML。Peach 保留番号核对、权限失败、字段投影和缓存策略。
- 编码边界依据 [MDN 视频编码说明](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Formats/Video_codecs) 与 [ffprobe 文档](https://ffmpeg.org/ffprobe.html)，用当前 FFmpeg；Peach 只持有兼容格式判定与缓存策略。
- CloudDrive 引导用现有 `settings_file`、`platform.root_online`、`scan_location` 与 React 配置页，来源为 `local`、`115`、`pikpak`。挂载由用户安装的 CloudDrive 负责（[官方帮助](https://www.clouddrive2.com/help.html)：Windows 用盘符、macOS 用目录挂载点），Peach 不捆绑其二进制，挂载引导不依赖管理 API，路径处理用 `pathlib`、`os.scandir`、`tomllib`；表单、来源归属与扫描选择归 Peach。云下载经官方 proto 子集（`downloads_clouddrive.proto`，version 1.1.0）走 gRPC，令牌存 CredentialStore，不用非官方 SDK（ADR-0089）。
- 媒体目录校验沿用 `media.normalized_path` 的平台策略：Windows 用标准库 `os.path.abspath` 保留盘符，macOS 用 `Path.resolve` 解析文件系统链接。实现只依赖项目 Python 标准库（PSF 许可），不新增依赖；校验器独立于账本路径翻译与媒体仓储。映射盘转 UNC 的边界由注入文件系统解析结果的回归覆盖，首次设置的本地来源也验证配置保留盘符。直接输入 UNC 仍按盘符路径契约拒绝；真实 NAS 挂载需在目标机器验收。
- 非 Windows 表单的内部路径由 `media_configuration.prepare_sources` 补齐，复用既有来源校验、配置写入和 `platform` 路径转换；不改数据库路径模型或执行迁移。标准库 [PureWindowsPath / PurePosixPath](https://docs.python.org/3.14/library/pathlib.html#pure-paths) 用于比较两种路径，[hashlib.sha256](https://docs.python.org/3.14/library/hashlib.html) 按来源与实际目录生成稳定候选前缀，已有配置和显式输入优先，冲突时换盘符。媒体库名称使用实际目录名，前端复用现有 `Disclosure` 和 `Input`，无新增依赖。
- JAV 入库资源查询复用 `sources.javdb` 的身份核对、`SourceTransport` 的 Cookie 与冷却、`HostLimitedTransport` 的 3 秒间隔、`beautifulsoup4==4.15.0` 和 `downloads.parse_magnet`。JavPack 的 `JavDB.magnet.user.js` 0.0.2（GPL-3.0，仓库 revision `b546e1881147e15796c697e8e086db9d3ac0c5be`）只作页面字段参考；它依赖浏览器用户脚本 API，不能直接作为 Python 服务端依赖。Peach 负责评论链接、协议分流、去重、60 秒缓存和最多三页评论的预算，不新增依赖。2026-10-02 单作品只读实测取得 4 条磁链和含 ed2k 的评论页；评论入口从 `.review-tab[data-url]` 读取，受限评论保留来源限制提示。磁链可交现有云下载服务，ed2k 与其他网页链接提供复制。
- 磁力优先链复用同一套 `SourceTransport`、`HostLimitedTransport`、`downloads.parse_magnet` 与 Torznab 后备，不引入新的抓取运行时。manko.fun 的页面请求实际落到 `healertanker.com/swx`，响应 `data` 是 basE91 包装的 JSON；实现只移植该公开响应形态与编号精确匹配，不复用会话脚本。Sukebei 直接读搜索表格；JavBus、16mag、OneJAV、Free JAV Torrent 由现有自配 Prowlarr／Jackett 索引器续接。Akiba Online 与 Reddit 只作线索，不自动取磁力。候选按分辨率、做种、推广附件数量与正片体积排序；总文件数不再是一票否决，manko.fun 候选绕过水印门槛，其他来源只能标为待到盘检查。
- 云下载后处理复用 `task_run` 后继串行、`process_library(retry_ids=...)`、垃圾复核的 `AUTO_TRASH_SCORE`、`organize.build_plan(asset_ids=...)`、SQLite backup 与整理批次日志。没有另造第二套刮削、垃圾词库或移动器。
- 抓取入口的复用缺口见 [来源采集](SOURCING.md) ；跨用户安装、来源网络、Cookie GUI、最高可得画质与图像清单按 [ADR-0024](adr/0024-mark-manifest-not-bundled-bytes.md)。私有后缀判断用 tldextract 5.4.0（BSD-3-Clause），随包 PSL、`suffix_list_urls=()`、`cache_dir=None`、`include_psl_private_domains=True`；Instaloader 4.15.3 与 browser_cookie3 不进正式依赖。HTTPX（BSD-3-Clause）、curl_cffi（MIT）、Pillow、amane 桥与现有候选缓存是正式基础，请求节拍用 `scripting.RateLimiter`／`HostLimiter`。
- 采集 GUI 用 React island、CredentialStore、HTTPX、Pillow 与 BackgroundJob，`jav_cover_fetch` 同时服务界面与 CLI；Peach 保留域内凭据、来源路由、预算、冷却、番号身份与高清替换策略。Cookie 文本用标准库 SimpleCookie／MozillaCookieJar 解析，不导入 pickle。
- 删除失效链接与资源同步的执行阶段用 `BackgroundJob.start_result` 存终态回执；刷新只查状态，写入不自动重放，确认与检查结果过期门槛照旧有效。
- 关注进度用 Fieldset 与 Progress；完成时保留内容取数，按作者检查用 `sources` 范围参数，报错作者用现有身份解析。
- GET 重试归现有连接器：[HTTPX 原生重试](https://www.python-httpx.org/advanced/transports/)只覆盖连接失败，统一不了 HTTPX 与 curl_cffi 的读超时、临时 HTTP 状态与页面进度。POST 不重放，403 单次终止。
- 标签发布用系统 Git、GitHub CLI（MIT）与 [Actions runs REST API](https://docs.github.com/en/rest/actions/workflow-runs)；[release_tag.py](../scripts/release_tag.py) 管版本、主线归属、同提交成功 CI 与不可覆盖策略，工作流用 Release 制品验收。
- 独立 Windows 测试包用 PyInstaller（GPL-2.0-or-later，带 bootloader 分发例外）的 [onedir 与自启动子进程](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html)：完整目录共享资源、避免重复解包，onefile 托盘入口只用于源码部署；Peach 保留进程所有权、数据目录、配置和扫描策略。
- 首启数据目录定位复用标准库 `pathlib` 和现有 `fsutil.atomic_write_text`，在默认数据目录记录本机绝对路径，读取限定跳转次数并检查循环。目录选择、单实例锁交接及服务环境属于 Peach 生命周期策略，无新增依赖。临时目录回归覆盖保存配置、真实子服务启动、正常首页、再次发现所选位置和锁冲突。
- Windows 安装包用 Inno Setup 6.7.3（Inno Setup License，允许免费分发）与官方 `Files/Languages/Unofficial/ChineseSimplified.isl` 译文（在 `scripts/installer/`）。目录选择与升级目录记忆复用官方 [`DisableDirPage`](https://jrsoftware.org/ishelp/topic_setup_disabledirpage.htm)、[`UsePreviousAppDir`](https://jrsoftware.org/ishelp/topic_setup_usepreviousappdir.htm)；Peach 在目录页和安装前校验独立目录、数据重叠、目录链接、写入权限及已有安装位置，使用现有 Inno Setup，无新增依赖。馆藏与口味上传说明复用 [`CreateOutputMsgMemoPage`](https://jrsoftware.org/ishelp/topic_isxfunc_createoutputmsgmemopage.htm) 的只读滚动页与 `ShouldSkipPage`；Unicode 正文赋给 `RichEditViewer.Text`，仅主动选择上传时显示。`test_desktop_installer.py` 编译正式脚本并运行共用判据的隔离 EXE，覆盖中文和空格路径、原位升级及目录保护，不安装到真实位置、不写 ledger。`.iss` 只管文件、开始菜单项和卸载登记；停托盘、补迁移、撤快捷方式经 `desktop_installer` 调已有入口，自更新与应用内卸载是唯一实现。
- 运行一致性：用 `LedgerDatabase.write_transaction` 的提交边界与 `OrderedDict`；HTTP 导航用 FastAPI/Starlette，图片复验用 `StaticFiles.is_not_modified` 与 `FileResponse` 的 ETag；列表用 SQLite 的 IN/UNION 保留隐藏标签与多标签组合，不引查询框架。
- 馆藏侧栏用 `catalog_filter` 的列表条件，已保存在线卡片用关注来源的标签与封面投影，详情用 `openFollowDetail`、Video.js 与媒体队列；导航范围与标签计数在 `frontend/src/sidebar.ts`。
- wheel 资源用 setuptools 的 `build_py.copy_tree`（[官方包内数据建议](https://setuptools.pypa.io/en/stable/userguide/datafiles.html)），钩子只复制三个既有资源目录，因为源码、桌面构建和前端产物共用其维护位置。

## Peach 必须自研的领域逻辑

以下属于产品行为，继续由 Peach 实现：

- 女优、厂牌、创作者、标签的规范身份、别名和来源；被并实体的旧 id 经 `entity_redirect` 墓碑与 `entities.resolve_entity_id` 找到新实体（ADR-0088）；
- profile 行为、稍后看、播放列表、口味和推荐排序；
- 本地、115、PikPak、在线来源的绑定和回退策略；
- 计费来源授权、隐私分类和候选复核导入；
- 物理资源垃圾候选的跨类型证据、人工复核和回收站语义；空目录清理复用 Python 标准库自底向上的 `os.walk` 与只删空目录的 `Path.rmdir`，Peach 只负责在线来源、根目录保护和 CloudDrive 并发消失边界；
- 垃圾永久删除复用 `purge_assets` 的隔离、数据库回滚与提交后清退，逐文件复核清单核对路径、体积与来源根，不新增依赖；隔离名带 UUID，移动与恢复使用 `os.rename`，目标须不存在，挂载拒绝移动时保留文件与账本行；
- 私有获取来源、出处引用和发现关键词；
- 创作者级视觉采样语义；
- 推理 Provider 与 Agent Provider 的能力契约；
- 任务归属、进度、取消、成本和证据规则。

下面这些是基础设施而不是产品行为，所以单独记下被拒绝的候选和不可替代约束，避免再从「这看起来该有现成库」开始。

| 自研实现 | 被拒绝的候选 | 不可替代约束 |
|---|---|---|
| `mp4index.py` 有界 MP4 关键帧索引 | PyAV、`pymp4`、Bento4 | PyAV 需要 demux，`pymp4` 依赖旧 Construct，Bento4 是额外二进制；都不能证明在云盘文件上保留「只读 moov/stss/stts、避免整片流量」的约束。 |
| `mp4repair.py` 重建缺失的 `ctts`，新头存成边车 | `ffmpeg -c copy` 重封装、`untrunc`、Bento4 `mp4edit` | ffmpeg 的 h264 解复用器拿不回显示顺序（实测重封装后仍有 986/2015 帧倒着走），`untrunc` 修的是截断不是缺表，`mp4edit` 只会原地重写整个文件，而网盘上的片子改一个字节就是几 GB 重传。约束是「原文件一个字节不动、播放时拼出合规 MP4」。 |
| `certs.py` 固定项目 CA 与短期叶证书（编码继续调用 OpenSSL） | mkcert、cryptography | mkcert 会接管本机 CA 安装/私钥，不能保持跨设备固定项目 CA；cryptography 只替换证书编码且增加原生依赖，不能删除 Peach 的 Apple 398 天与 CA 生命周期策略。 |
| `migrations.py` SQLite 迁移 | Alembic | Alembic 会引入 SQLAlchemy/Mako/greenlet；现有范围只需顺序 SQL、校验和、备份与 PyInstaller 资源定位，没有 ORM 消费者。 |
| Gofile API 直接 HTTP | 社区 wrapper | 官方没有维护中的 Python SDK；社区 wrapper 只是薄封装，不能绕过 Premium `contents` 权限，也不能减少 Peach 的 Bearer 隔离与媒体规范化。 |
| `netwatch.py`、streaming/segments、sync、versioning/Windows update | 通用替代实现 | 分别是无 PyObjC 的系统通知、FFmpeg/Starlette 上的会话策略、单 writer ledger 规则和 Git/PyInstaller 更新契约；通用替代会保留同量 policy 或扩大依赖。 |
| `downloads_pikpak.py` PikPak 云下载 | PikPakAPI；云下载直连 115 用的 `p115client` | PikPakAPI 是 GPL-3.0-only、只有 async 接口且吞掉验证页地址，只借协议常量；`p115client` 要另走一套 115 授权，和 CloudDrive2 挂载争授权名额（ADR-0089）。浏览器登录（`downloads_pikpak_browser.py`）照 PikPak Assistant 用户脚本（MIT）读 localStorage 的 `credentials_*`，拉窗口复用 `browser_transport` 的 `attended` 档；用户脚本借网页续期，Peach 取走后自己续期（ADR-0093）。 |
| `organize.py`／`organize_templates.py` 目录收纳 | amane 的整理模板 | 只借占位符与可选分组语义；amane 把文件当可删的派生物，Peach 的媒体原地不动，收纳要用户发起（ADR-0039）。 |
| `wants.py` 想要清单 | SakuraMedia 的想要与订阅 | SakuraMedia 只作行为证据；入库按番号自动对账，按 ADR-0052 直接落库（ADR-0090）。 |
| `jobs.PidFileLock` 批处理进程锁 | `portalocker==4.3.0` | 候选覆盖 PID 写入、持有者、原子替换与陈旧文件清理，但一直没有消费者落地；按「候选依赖不得空转」不引入。 |
| `record_rehome.py` 个人记录接回新文件 | 按文件指纹接回；六张表改 `ON DELETE SET NULL` | 消失的文件读不到、算不出指纹；记录脱离 `asset` 就丢了番号与标题。番号是唯一跨版本稳定的键，无番号的按创作者、文件名主干与时长接回（ADR-0087）。 |

发行物目录折叠与目录广告清理复用 `scripts/flatten_release_dirs.py`、`catalog_rules` 和 SQLite 备份。
路径计划按目录组件建索引并保留操作顺序，覆盖资产及已登记字幕；物理移动携带未登记封面等旁挂文件。
目录图排除文件末层；同名的多个子目录及带媒体扩展名的目录包装按冗余层判定，扩展名复用扫描器分类。
冗余目录独占父目录时使用两次整目录改名及空暂存目录清理；父目录含附带文件或其他版本时，目标条目互不冲突才合并。暂存位置或文件名冲突时拒绝执行。
包装目录占用内容目标名时，先暂存该目录再移出内容，其他版本保留原位。
每条移动先复验真实目录，部分失败立即恢复本条，账本事务失败恢复本批。
不新增依赖；Python 标准库的路径操作和 SQLite 已覆盖底层能力，Peach 保留冗余名称、广告和账本一致性判据。

完整目录分类归位使用 `scripts/organize_library_dirs.py`，复用 `organize._rename`、目录折叠入口、番号解析器、实体分类与进程锁。默认冻结计划，`--apply --backup` 才执行；`--batch` 分开保存计划与回执，失败可恢复。`--stage flatten` 自行生成折叠复核 CSV。同目录已确认作品与缺厂牌配套图作为整包处理；混合目录只移动已知文件组及配对字幕，物理文件去重，未知视频、冲突身份和覆盖目标均保留；同目录附属文件按 `scan.is_sidecar` 判定，带片名的随所属视频走，裸 `poster`、`movie.nfo` 与 `extrafanart` 只在整目录视频都移走时跟随，已登记文件与隔离文件不动。删不掉的源目录连同剩余名字记进回执 `cleanup_errors`。创作者目标只保留规范名那一层之下的结构，规范名之上的容器目录丢弃，路径里没有规范名时只带源目录名；不整体改名的容器目录名见 `resources/naming/container_directory_words.txt`。`--rollback` 按回执逆序撤回已提交条目，复用 `restore_entry`，账本已被改过的条目单独报出不动；不带 `--apply --backup` 只列计划。目录名的发行站、画质和分片标记复用 `release_code_from_filename`；一本道的日期式分隔符匹配复用其官网作品号解析，不跨片商合并日期编号；目标番号目录先对齐已有的等价目录，几处并存时取官网作品号写法。视频文件名自带编号且与账本番号不是同一部作品时不建目录，记为待确认。FC2 发行目录可使用 `FC-` 简写并附带标题；缺番号的 `@fc编号_分片` 同时核对父目录编号与厂牌，目录编号不补写账本。韩国 MIB 按厂牌归置。已有唯一创作者关系但细分类未确认时归入通用「创作者」目录，不提升身份候选。路径同步覆盖资产与已登记字幕，不补写真相字段。

PikPak WebDAV 同来源移动由 `organize_clouddrive.py` 提供，目录入口显式指定 `--pikpak-webdav --location pikpak --batch` 才启用。复用 CloudDrive 已存令牌与官方 `GetDownloadUrlPath` 的认证头；来源固定为 `https://dav.mypikpak.com`，认证不写入地址、日志或账本。协议依据 [PikPak WebDAV 官方说明](https://mypikpak.com/zh-TW/help-center/connected_apps/webdav/how_to) 与 [RFC 4918](https://www.rfc-editor.org/rfc/rfc4918)，HTTP 客户端复用已声明的 `httpx`，消息编码复用已声明的 `protobuf`，不新增依赖。官方 `GetSubFiles(forceRefresh=True)` 从来源根逐层读取完整目录，校验每层成员来源、父目录、重名与体积。同父改名使用官方 `RenameFile`；同来源、保持原名的跨目录移动使用官方 `MoveFile`，冲突策略为 `Skip`，明确关闭跨云移动与递归合并。WebDAV 只用 HTTPS `MKCOL` 创建所需父目录，改名与移动都走官方接口。官方回 false 或拒绝码（`INVALID_ARGUMENT`、`PERMISSION_DENIED` 等）且目录确认原件仍在、目标未出现时，本条记入回执 `failures` 并回滚，续跑跳过；原件与目标都在或都不在时才中止本批。`organize._rename` 对 A 盘默认拒绝，只有经 `organize.verified_renames` 注入官方核验改名时放行，未注入时各执行入口整批拒绝。单次 HTTP 应答上限 2 MiB，遍历上限 10,000 条。提交移动后只刷新涉及目录的缓存，沿完整祖先链取得路径与体积变化证据，再交回现有账本事务；最多四次只读确认，不重复提交移动。状态不确定时保留意图回执并停止，成功应答也必须取得完整成员变化证据。A 盘折叠目录使用不以点号开头的暂存名，保留同一套恢复回执；R 盘不进入该传输路径。

A 盘同父目录改名复用 CloudDrive 官方 `RenameFile`；跨父且改名的操作拆为同父 `RenameFile` 和保留名称的官方 `MoveFile`，中间路径进入恢复回执并禁止覆盖。每步核对完整成员与体积；成功应答不替代路径核验。路径中的 `@` 保留为 URI 路径字符，其他字符使用 UTF-8 百分号编码。认证样本已移动时，最多另查两条馆藏内图片样本，仅提取既有认证，不读取媒体内容。

推广网页资源目录中的横幅、标志和导航图片由 `web_batch._attachment_junk_reason` 进入复核队列。判据同时要求 `_files`、网址推广套话、域名、导航元素文件名和体积上限；继承的作品番号不豁免网页导航图片，作品图片、字幕、NFO 与普通 `_files` 目录保留。固定命名的推广附件（安装包、宣传视频、二维码卡片、下载站横幅）按扩展名、体积上限与整名收在词库 `resources/junk/promo_attachment_names.json`，由 `web_batch._promo_name_reason` 读取；命中只计复核档 `JUNK_REVIEW_SCORE`，未探测时长视频的推广目录、推广创作者位与体积证据同样只到复核档，这类分数单独凑不到 `trash_junk.py` 默认的自动移入回收站门槛 `AUTO_TRASH_SCORE`，不凭名称移入回收站或删除。永久删除仍走 `trash_junk.py --purge --review-csv` 和 `purge_assets`。

普通作品图片和 `images.rar` 保留。CloudDrive WebDAV 的小文件删除复用原生 `Path.unlink`：同目录隔离改名报 WinError 50 时，最多 1 MiB 的文件先用标准库 `shutil.copyfile` 完整备份到 `generated/purge-staging/`，记录原路径、大小和 SHA256，再删除。数据库失败时恢复备份，提交后清退；大文件继续报告阻塞。该路径没有新增依赖，备份和恢复只由 `purge_assets` 管理。

英文媒体文件名分词复用 `wordninja-enhanced==3.2.0`（MIT，2026-09-04 发布，Python ≥3.9，纯 Python，wheel 11.4 MB，无传递依赖）。源码经 `opensrc path pypi:wordninja-enhanced@3.2.0` 读取；原版 wordninja 的发布较旧，增强版提供标点保留和可补充词典。`space_media_names.py` 先出逐文件复核 CSV，只插入空格，保留序号、字符与扩展名，不推断人物或出处；指定目录内的真实文件核验后复用 `organize.apply_plan`、SQLite 备份及回滚日志。领域词汇在 `english_filename_words.txt`（置顶加权，条目宜少：每多一条都会压低 `a`、`of` 等高频词），常被误当成单词的拼接串在 `english_filename_nonwords.txt` 里移出词频表。撇号缩写、全大写缩写、域名与站点编号、十六进制 viewkey 不拆；西里尔形近字母按拉丁字母算切点、原字符保留。已执行批次用 `--recompute-batch <日志>` 按原名重算出复核 CSV，再走 `--apply --review-csv`。

A 盘垃圾隔离使用短名称 `peach-purge-<32 位 UUID>.peach-quarantine`，扫描入口仅跳过这一精确格式；普通媒体和其他 `.tmp` 文件仍需复核。隔离恢复与本地完整备份沿用 `purge_assets` 的事务边界。

推广附件的 A 盘原文件取证复用 CloudDrive 官方 `GetDownloadUrlPath` 的本机原文件代理，不经过 Peach 转码，也不传递认证信息到其他来源；实际二维码副本和视频取证帧见馆藏复核产物。

保留自研不是永久豁免：约束改变或候选实现更新时重新跑 POC，不因本表结论跳过外部检索。

## 已定型的产品行为

每条是一次验收留下的判据，一条一句。改这些行为是产品决定，照着再实现一遍是
重复劳动，动手前先确认这里没有写过。README、[开发约定](../AGENTS.md)、[docs/OPERATIONS.md](OPERATIONS.md)
和 ADR 已经写下的不在这里重复，出处用 `git log -S` 查。

- 本地浏览器直放 MP4/WebM/Ogg，其余容器由 `TranscodeService` 按六秒片段缓存成 H.264/AAC MP4，不改写原媒体；ffprobe 判定可复制的流不重编码，其余在 Windows 走 CUDA/NVDEC。
- 远端 MP4 默认走标准 Range，显式开启的 HLS 使用关键帧对齐片段并在失败时回退 Range。
- 页面共用 SPA、JSON 与 gzip/ETag；侧栏随当前视频集合，已保存在线作品复用关注详情。
- Logo、侧栏「首页」和沉浸模式关闭统一清除分类、搜索与 JAV 筛选，首页默认稳定随机、换批才换种子，再点当前排序回到随机。
- 高亮、竖屏密度、索引骨架已验桌面/390×844，HTTPS 生效；手机命中区 44 px。
- 主题三选一（跟随系统／浅色／深色），只存本机，首帧前由内联脚本写进 `<html>` 的 `data-theme`。
- 同番号的分卷派生（A/B、1/2、CD/Disc/DVD/Part/Vol、「首卷裸名 + 后续卷 `-2`/`-3`」）折叠成一张卡并按时长排除完整版，首页、搜索、资料页网格、版次队列与角标计数共用这套判定。卷号后挂着尾缀时先剥掉组内每个文件名都带、且从分隔符起头的那一段，不按版次词表拆（`1080p` 会被拆成 `10` 加 `80p`）。
- 分卷卡与版次卡（有码／中字／无码）不翻卡（各卷共用同一封套，翻了像卡住），悬浮走分段视频预览；封面只有一个计数（「N 卷」或「N 个版本」），叠层封面格垫 `--page` 挡住模糊衬底的半透明边。分卷详情标题带卷号，因为各卷标题、女优、厂牌逐字相同。
- 本地播放版本复用 `catalog_rules` 的番号、版次、卷号与重复文件判断。`catalog_versions` 先区分版次、探测规格与修复标记，再识别各版分卷；完整文件与分卷同时存在时核对总时长，重复副本保留文件并共用一个版本身份。计数、首页、资料页、详情队列和相关推荐读取同一查询投影，ledger 不变。`scripts/audit_catalog_versions.py` 只读输出全库复核 CSV；没有时长、规格或明确番号证据的关系保留待复核。
  - 文件身份复用 `metadata.identifies_code` 核对 DMM 数字前缀与补零写法；通用文件名须同时有带归属的刮削标题和匹配的番号目录，显式文件号冲突优先拒绝。同名副本的已知时长相差超过 10% 时保持独立，缺测量项不附到有时长冲突的同名组。来源目录时长保留为证据，不代填媒体实测时长；特典、广告与缺内容的取舍记录在逐文件复核产物里。
- [Jellyfin 的多版本命名](https://jellyfin.org/docs/general/server/media/movies/#multiple-versions)以共同作品名和版本后缀区分文件（2026-10-10 核对）；其整套服务器与严格目录前缀不能直接承接 Peach 的 FC2 短番号、分卷合集和跨盘资产。这里只参照作品、版本与文件的分层，不复制 GPL 源码、不引入依赖。最小实证使用 `FC2-PPV-1110408` 的完整文件和两段 4K 修复文件：两个播放版本、三个文件、同版独立分卷队列。
- 合集翻图复用 `use-stack-flip`（`frontend/src/react/components/`）：逐张解码后才显示，失败帧不入队，悬停代际隔离异步结果，退出清理计时器。馆藏与关注详情共用原生 `dialog#stage`，浮窗不参与列表排版，关闭保留关注列表岛；图片灯箱沿用现有实现。
- 关注卡翻卡去重：`follow_faces` 复用 `community_catalog.fingerprint` 的 dHash，另加 8×8 RGB 色块（dHash 分不开同姿势的穿衣版与 nude 版）。阈值的实测依据见 [复用取证记录](SOURCING.md#媒体与关注实测)。
  - 地址相同即同一张；时长都已知且差 ≤1 秒时 dHash ≤6、最大格差 ≤4；时长未知时 dHash ≤2、格差 ≤3。
  - 翻卡按画面去重、不分站，封面计数把跨站同一画面并成一个媒体，并完只剩一个时写「N 个来源」；同站不按画面合并，4K／8K 两个文件和局部差分会混进来。
  - 文件内容哈希相同即同一个媒体，不论同站跨站、不看 alt／WIP 标签：连接器的 `content_hash` 从已存原始地址解析、不发请求（kemono／coomer／pawchive `/data/<h0h1>/<h2h3>/<sha256>`，rule34.xxx `/images/<目录>/<md5>`，paheal `r34i.paheal-cdn.net/<h0h1>/<h2h3>/<md5>`）；fanbox、rule34video、f95zone 的文件名是随机 id 或签名令牌，不参与。
  - 同站相同 `external_id` 与媒体序号标识一份媒体，归档站同时核对服务与作者范围；多个关注来源重复收录只增加收录份数。已知文件哈希不同、不同帖子或不同媒体序号各自计数。
  - 签名由后台线程补进 `generated/posters/follow-faces/`，不进 ledger。
- 关注视频规格复用 `follow_variants`：`4K60fps`、`1080p60fps` 与带空格的规格拆成清晰度、帧率两个版本标记；存量作品键在读取时投影，无需重抓或改写 ledger。
- Rule34Video 封面复用 `FollowCoverService`、HTTPX 与 Pillow，经 `/follow-cover` 缓存静态 poster。自身图片失效时只在当前作品组内寻找其他版本的封面，沿用来源白名单、请求字节上限、并发与缓存锁。该路径不解析正片、不抽帧；首页、作者筛选、详情队列与已保存在线卡片共用缩略图投影。复核记录见 `<私有验证记录>`（仓库外）。
- F95 讨论图片依据已确认的附件身份在采集与读取投影中排除。网盘资源保留，未取得作品预览时显示资源服务图标；不能仅凭 GIF 扩展名过滤作品。
- 普通多女优卡片叠放前 3 个头像、只显示第一位姓名和真实总人数；JAV 小图是整页版式，混入的非番号作品统一为标题、身份、标签三行固定高度。
- JAV 详情持有 `asset.catalog_title`／`original_title` 与官方 Tag 身份，官方标记用常规字重，身份区收齐到同一内容起点。
- JAV 官方封面重探只在面积更大时替换，失败保留原图。
- 播放器按 YouTube 锁定源码对齐控件、状态、图标形变、设置面板动画与悬停提示，倍速五格到 3.0，窄屏按播放器宽度折叠留黑边，沉浸模式按 Shorts 版式，竖屏用居中 9:16 舞台，播放统计两页共用、按传输方式分口径。参考快照在 `docs/reference-snapshots/youtube-*.md`，索引见 [开发约定](../AGENTS.md)；只复用可测量的层级、尺寸和状态语义。
- 图片灯箱在本地照片、番号样张和关注在线图片间复用同一套 Swiper，样张与在线图片只显示来源、合集序号和浏览器实际解析结果；照片标签进入分页图片墙，点开才按原图比例呈现。番号集卡复用播放列表卡的叠层纸边、`mixbadge` 张数与 `mixcopy` 两行字，封面走视频卡同一份 `coverImage`（ADR-0068）。
- 资料页照片与关注在线图片两处图片墙共用大小（顶部按钮）与固定比例／瀑布流（筛选浮层）设置；关注图片的「仅显示图片」独立保存，隐藏卡片文字与角标。瀑布流用 CSS 多栏、`break-inside:avoid` 与图片 `aspect-ratio:auto 1`，懒加载前留非零高度（[MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/aspect-ratio)）；`alt` 留给读屏，字色透明。
- 缓存型资产路由先问缓存，不先解析源文件：`media_engine.file_for()` 的存在性检查落在 CloudDrive 网盘上一次 137–402 毫秒。`/photo-thumb` 先问 `photo_service.cached()`，`/sample-thumb` 先问 `SampleCache.cached()`，缓存根从 `photo_root` 推出。
- 远程媒体持久缓存复用 HTTPX 0.28.1 的流式响应、Starlette 1.7.0 的文件 Range、filelock 4.0.12 和 `fsutil.atomic_path`；缓存身份、失败保留与按需取得由 Peach 管理。HLS 清单解析用 [m3u8 6.0.0](https://pypi.org/project/m3u8/)（MIT，Python ≥3.7，纯 Python、24.1 KB wheel、无传递依赖），已读取固定版 `model.py` 并验证嵌套清单、密钥、初始化段和字节范围。Hishel 1.4.0（BSD-3-Clause）已读取 `_sync_cache.py` 与 Range 判据，通用 HTTP 缓存按来源 URL、缓存头和重新验证工作；此处需按稳定条目身份避开签名 URL 更新、永久保留成功媒体，因此复用现有持久文件路径。规则及验收见 [媒体缓存](MEDIA_CACHE.md)。
- 账本路径在 Windows 只做 `abspath`，不 `resolve()`：PikPak 的 A: 是 WinFsp 网络驱动器，`resolve()` 得到 UNC 形态，之后每个经 `file_for()` 的播放请求都慢几个数量级；115 的 B: 不受影响。
- 不兼容片源（HEVC、mp3 以外的音轨、非 MP4 容器）按 6 秒片重编码给 HLS，账本没记时长或为负时用 ffprobe 的时长切，探测也拿不到才回 Range。分片与整片共享硬件后端探测及回退链，见[硬件转码后端](#硬件转码后端)，同一分片只起一个 FFmpeg。Range 响应按 1 MiB 读文件，`BufferedFileResponse` 自己盯 `http.disconnect`（uvicorn 断开后 `send()` 静默返回，Starlette `FileResponse` 不监听），否则拖一次进度条就留下一个把整部片拉完的幽灵读者。
- 有 B 帧却没有 `ctts` 的 MP4 是时间戳错乱，浏览器丢掉倒着走的帧。这类片源不重编码：`mp4repair.py` 重建一份 `moov`（游程编码的 `ctts`、编辑列表补整体平移、`stco`/`co64` 按头长差平移）存成 `transcode_root` 里的 `.mp4hdr` 边车，`/stream` 用「边车的头 + 原文件那段 mdat」按 Range 发。显示顺序取自 `ffprobe -ignore_editlist 1 -show_entries frame=pts`，`pkt_dts` 不能用。边车算出来前走 HLS 转码，同一部片后台只算一次，算不出就跳过。
- 「只采集在线资料」对齐全的行（番号已落库、字段有着落或已在候选表、封面在位）不碰磁盘；文件在不在看目录列表、同目录只列一次，扫描用目录列表自带的大小与时间、不逐个 stat；候选 CSV 每 5 秒写盘一次，被打断也在收尾写全。
- 一个控件在两页出现时，选中态也归它：首页、资料页与关注页的「全部／没看过／稍后看／已标记」共用 `use-view-glide.ts` 那块滑动玻璃与 `[data-view-glide]`，填充只由玻璃给，各排自己不铺底。
- 本地详情与关注详情的「接着看」共用 `RelatedSection`、横滚接线与卡片 `next` 版式，数量读同一设置。空结果复用 `EmptyState`，读取失败复用 `RetryNote`，可原位重试；关闭推荐时不取数。关注推荐复用 `/api/follow` 分页及 TanStack Query 缓存，最多读取三页；优先同创作者和共同标签，排除当前合集、已看、忽略与不同媒体类型。创作者头像链接复用卡片的 hover，下载链接复用舞台操作键几何；未新增依赖，推荐读取不写 ledger。
- 详情底部共用 `DetailContinuation`：分集队列在左、接着看在右，手机上下排列，两区独立横滚并共用底色。封面共用宽度与 16:9 比例；宽屏标题区通过 CSS subgrid 共用行高，摘要与操作键参与排版。`RelatedSection` 给卡片的 8px 悬停面留出滚动内距；分卷与版本的推荐按队列种子缓存。队列头像共用 `cardIdentity`、`avatarInner`，同时读取列表投影与详情 `entity_refs`，保留图片版本及人脸取景。
- 艺人、卖家名册的 RSS 标记读取 `/api/index` 的 `following`：按实体 ID 汇总已启用的 JAV 新作订阅与关注来源，停用、移除及未确认绑定的同名来源不计入。标记复用 `PeopleGrid`、BoardUI `Chip` 与现有 RSS 字形，不增加取数请求或依赖。
- 统计与口味两页按登录态 Vercel Analytics／Speed Insights 的当前页面重做，排行与数据源共用父网格的引导线。
- 口味页顶部给出结论与可点入口：浏览与 Peach 两侧的共同信号、可探索标签、待补证据的下一步动作。
- 操作回执复用 Toast（Sonner 的栈，`frontend/src/react/toaster.tsx`；壳里只调 `toast()`／`actionReceipt()`）；按钮以 Spinner 和 `aria-busy` 标明忙态。后台任务显示可恢复进度，断线自动重连。
- 实体的统称由用户在资料页自选：菜单只列这条实体名下已有的写法，选中的提为规范名、换下的留成别名，扁平投影跟着改；先过确认弹层并点名两个写法，成功后发可撤销回执；不收自由文本，撞上另一条实体的规范名只报冲突。
  - 艺人入口审计复用 `entry_links` 的 JAV 目录闸与日文名选择、只读 SQLite 和 `review_csv`；`audit_performer_entry_names.py` 覆盖全部 performer，分别记录入口来源、名称选择和中文来源缺口，远端页面未请求时保留「未取得」。译名复核后的原日文名按 `performer-localization` 来源优先。`localize_performer_names.py --mapping-source` 标明 CSV 的真实复核来源，`--only` 限定实体并拒绝不完整的同人合并组；别名、兼容标签、来源留痕和备份沿用已有实现。未新增依赖。
  - MISSAV 入口优先读取 `state/missav-actresses.json` 的实际名录 href，保留原字形、数字前缀与括号艺名；`directory_name_key` 仅对匹配折叠字形及纯汉字、假名名称的空白，英文名称空白保留。同名对应不同路径时不显示入口；明确 `verified=false` 的来源项阻止该名称入口及模板回退，页面重新核验后可解除。未采全名录的缺项保留模板候选，不能判为站点未收录。七沢みあ、逢沢みゆ的 h1 与 canonical 已在用户 Chrome 验证，证据在 `<私有验证记录>`；名录原始分页和全库匹配 CSV 在 `review/missav-directory/`。API 与全库审计共用 `entry_links.build`，派生入口不写 ledger，403 验证页记「未取得」。艺名选择沿用现名、读音排除与来源冲突复核。无新增依赖。
- FC2 作品计数复用 `ordered_multipart_items`，`web_catalog.video_work_key` 按账本代次缓存已确认的合集键；资料、名册、同台艺人、标签与补全共用。`/api/items.work_total` 供作品读数，`total` 仍用于文件分页；不确定分段不归组。出演名单展开复用 BoardUI `Button`。跨角色人工合并走 `merge_entity`，`user:identity-merge:<kind>` 的别名与墓碑共同证明旧资料页地址，单纯同名不跨角色跳转。
- 馆藏分组缓存声明 `asset`、`asset_entity`、`entity` 依赖；下载进度和复核留痕不使分组重算，同键计算串行共用。作品计数通过 SQLite `create_function` 注册资产到作品的只读字典查找，沿用既有版次与分卷判据。下载后处理复用已绑定资产、整理备份及逐项外键基线比较（`performer_alias_followup`）；单部采集只结算本资产候选，进度在 `state/download-processing/`，共享 CSV 仍受全库处理锁保护。资源查询缓存拒收缺字段和来源失败的旧结果。无新增依赖。
- Application 管理路由与资源，域控制器适配原生宿主。文件检查含本地与网盘，来源等分、共用确认弹层；CloudDrive 分档建议首启与配置页共用。
- 名字里的括号都走 `split_composite_aliases.py`：自动那拨只认罗马字复合人名，`--from-review` 那拨按人工判定清掉不承载名字的尾巴，旧写法留作别名；读音、厂牌消歧和角色出处不拆。`<私有验证记录>` 中的 28 条 creator 注音、575 条 tag 角色出处和 10 条 series 厂牌或载体消歧均保留，不属于待执行批次。
- 全库创作者归属复核走 `scripts/audit_creator_attributions.py` 与 `metadata_creator_attributions`：复用 `code_creators` 的文件番号核对、DMM `matching_cids`、`western_release_identity`、厂牌规范名和别名、`studio_sites.is_platform`、字段归属、SQLite 备份及 `review_csv`。发行站作品号同时核对目录名与媒体文件名；来源目录见 [Legs Japan](https://www.legsjapan.com/en/)、[Fellatio Japan](https://www.fellatiojapan.com/en/)，平台身份见 [myfans 官方说明](https://creators.myfans.jp/)。预览覆盖全部来源关系与无作品实体；执行带冻结计划、备份和回滚记录，在事务内重算。只撤下有文件或发行来源证据的 `legacy:asset` 关系，保留用户判断、外部身份、规范实体和个人记录。月份、画质、份数与合集注记只回归已存在的完整账号；结构目录按整名切分判定，词汇取 `catalog_rules` 标签、`genre_taxonomy` 来源词与 `resources/naming/structural_directory_words.txt`，不登记具体馆藏的目录名；`creator_collection_base` 同时用于审计与摄取。公开厂牌断言可补空厂牌投影及关系，缺少规范厂牌时记录新实体完整快照；`upsert_asset_entity(update_entity_metadata=False)` 保留既有资料。结构目录、已知转载站及可信厂牌不进入自动 creator 摄取。外键检查逐项比较既有悬空；回滚核对字段版本、关系和新实体引用，后续改动拒绝整批恢复。
- 实体链接可安装：`entity_link` 表、`q_entity` 的 `links` 契约、资料页 favicon 与管理页链接管理成套；死链区分「搬走了」和「没了」，`rediscover_entity_links.py` 从站点索引页上溯找新锚，确证没了的由 `link_status.settle_gone` 处置（已隐退女优留成不可点的失效标记，其余删除）。
- 厂牌社媒核查：`find_studio_socials.py` 用 Beautiful Soup 4.15.0 解析锚点，传输与字符集用 `peach.http`，账号键用 `social_links.handle`，不加浏览器运行时；Peach 负责同站年龄门、已有账号差集与证据表。整页正则会混进帖子与脚本里的地址，不用于账号提取。
- 事务所是实体：57 家各有 `/agencies/<名字>` 页，成员、官网、标签与作品都按 `entity_membership` 算，女优页点得进去，搜名字出这家人的片；原文留在 `metadata.agency`。
- 外链圆标与厂牌标识取站点自己声明的资产，宽扁字标不参加小圆标竞选；`/logo` 的 `variant` 分 `icon`、`logo` 与最清晰的 `large`，大图版式和资料页取 `large`，紧凑版式取 `icon`。头像与标识共用 `nativeImageFit`：按像素密度折算的源尺寸不足框四成时等比居中、不放大、同图模糊补底，框短边小于 64 px 不补底；加载、回落和版式切换都重新度量。
- 关注的作者头像与来源图标是元数据：`follow_assets` 取回落在 `generated/follow-assets/`，经 `/follow-avatar`、`/source-icon` 给页面；地址只从固定表或固定主机拼，字节先认成图再写盘，到期重取失败继续用旧的并退避一小时，缓存有效期与 `/link-mark` 共用 `web_settings.metadata_refresh_seconds`。视频与图片不存本机。官方头像先认 FANBOX，没有时 `follow_avatar.profile_avatar_tiers` 取名片上的 X 与 Patreon，`follow_assets.largest_image` 留像素最大的那张。
- 厂牌标识由契约位 `has_logo` 决定出不出图：没装标识的厂牌一个 `<img>` 都不发，改用首字母底板，不靠 404 摘。标识 198 张随仓库分发（ADR-0026）。
- 关注检查分两阶段：列表阶段落 partial 行，详情补全按 provider 额度只补新行和未补齐行。「补齐过」由连接器的 `ENRICHED_MARK` 声明、判据登记在 `follow_store._ENRICHED_PREDICATES`。
- 存量行重抓（连接器新学到图片宽高、封面这类字段时，旧行不会自己补上）分两步：把 `ENRICHED_MARK` 换成新字段落库后才有的键并登记判据；再 `POST /api/follow/check` 发 `{"older":true,"backfill_all":true,"rewind":true,"background":true}`（可加 `"sources":[id,…]`），从第 1 页走到站点说没有更多，进度读 `GET /api/follow/check`。`backfill_all`、`rewind` 离开 `older` 被拒收、不进界面，ledger 的回填游标只进不退；长跑任务，重启前按 `peach-batch-jobs` 先查。
- 图片的固有宽高只问文件头：`follow_image_dims.probe_image_dims` 发 `Range: bytes=0-65535`，`dims_from_header` 认 PNG／GIF／WebP（VP8、VP8L、VP8X）／JPEG（跳过 EXIF 到 SOF）；连接器、`backfill_follow_image_dims.py` 与 `/api/follow/image-dims` 落库前都经 `positive_dims` 归一，`FollowStore.set_image_dims` 只补空缺。宽高只为图片视图的卡面预留比例，回填每个条目只问卡面那张；原文件主机拦脚本（pawchive 的 `file.` 子域挂 ddos-guard）时按缩略图量，详情原图取不到也退回缩略图。
- 归档站（kemono、coomer、pawchive）多图或视频的帖子由 `KemonoConnector._media_items` 列进 `media_items`（交付文件排第一、按路径去重），详情轮播与 `/follow-stream?media=N` 按条目自己站点的主机白名单取。
- `/api/related` 用 Tag IDF 加 MMR 排序并缓存；搜索使用 FTS5 trigram，短查询回退 LIKE 并覆盖规范名、别名和检索词，搜索历史在 reader 写入被拒时降级到页面内存。
- 搜索预览复用 `/api/suggest` 的分类总数、二十条上限与菜单滚动容器；多类各预览五条，单类预览二十条。视频总数超过预览时通过「查看全部」进入现有目录连续滚动，不新增分页接口或滚动依赖。
- 复核页覆盖元数据、创作者标签、Logo、头像、身份、番号目录、FC2 证据和片尾出处；抓取与 AI 结果仍是候选，批准后才写真相字段，元数据候选保留 MetaTube 目录证据且不下载 URL。
- 元数据旧决定是否过期只由 `metadata_auto_apply.metadata_decision_is_stale` 判，复核页与自动落库共用；过期后的重判、人批准标签只增不删、FC2 描述性称呼自动否决与两类撤回见 ADR-0079，`pending_genres` 收录后按并集补标签与 `genres_still_pending` 见 ADR-0082。
- 资料页头像圆框角上的换头像入口有五条路：图库同名的其他图与这个人取过的每一张图、本机文件、https 地址、本人作品画面、输入番号取封面。
  - 取到的图按内容哈希进候选缓存，换回去不重新下载。页面只回递服务端列出的 `ref`，地址由服务端按索引拼；手填地址过 `http.public_https_url`。
  - 人像三路（图库与取过的图、手填地址、本机文件）点开先进框选，可整张使用：`POST /api/avatar-frame` → `avatar_picker.framed` 量转正后的尺寸、按 `face_square` 落默认框；手填地址经 `typed_address` 取一次按地址存进 `url/`，之后的 `url:<地址>` 只读本机；本机文件只量不存，确认时框走查询串 `crop=x0,y0,x1,y1`。框出的一块按 `peach:picker/<sha>` 入缓存，原地址仍指整张；EXIF 方向由 `images.upright` 统一转正。
  - 作品画面一部一格，底图在封面和九宫格九格之间换，框出方形再装（`avatar_picker.asset_artwork` 与 `crop`，走 `/avatar-choice`）；服务端每次先核对作品挂在这个人名下（`asset:<id>:cover` 谁都拼得出），裁出新字节，原图不动。
  - 番号取封面（`POST /api/avatar-code-cover` → `avatar_picker.code_cover`）不要求番号在馆藏：本机封面目录优先，没有才走 `jav_cover_fetch.best_cover`，按番号只存对象、不写证据，之后的 `cover:<番号>` 只读本机。
  - 默认框围着 `avatar_picker.cover_focus` 取景：有脸取 `avatar_cover_face.face_square`（要边车里有脸宽与 `px`，缺的 `detect_cover_faces.py --redo`），没脸取 `jav_poster_crop` 的正封，前端不另抄判据。
- 整张作品封面不装成头像：单人作品关联不证明画面里是谁，`cover_fallback` 标身份未核实，采集脚本的安装闸门拒收。图库给不出唯一人像时，补头像后继（`peach.avatar_followup`）从单人作品封面截脸周围一块方图（`peach.avatar_cover_face`，YuNet 脸框放大 2.4 倍），按脸宽像素挑封面、最差是缩略图，其余检得出脸的封面各截一张进候选缓存（至多 8 张）。面具、眼罩照样算脸，检不出的不降门槛去捞（低分框多落在手和身体上）。来源记 `cover-face` 与 `identity_verified: false`。
  - 图库同名多张时先按脸认人（`peach.face_match`，互证与小图作证见 ADR-0056、0057、0062、0066）；封面未匹配时仍允许不同来源、不同照片的图库多数独立互证（ADR-0104）。短边至少 300 像素的档案小图优先于封面裁片（ADR-0102），更小的图在封面落空时兜底。身份判据复用 `match_gallery`，来源标 `gallery_small`，可升级至标准图库人像。
  - 自动封面头像可换为合格档案人像或更清晰封面，人挑的不碰；候选列表按本机内容及默认裁切哈希去重，保留在用状态、具体来源与作品全部底图。展示去重不参与身份判断，不联网取图，不删除历史缓存。
- 外部来源 genre 只在 `peach.genre_taxonomy` 投影：JavDB `類別` 与日英来源词共用词表，非内容分类排除，未收录原文回传登记；查表只有 `resolve_genre` 一处，抓取与复核折叠共用。r18dev 取 `categories[].name_ja`，日文取不到才用英文。有码标签的来源选择复用 `metadata_policy.preferred_tag_source(code)`，排序、采集与复核一致；替换走 `metadata_auto_apply` 的来源归属与人工判断保护（ADR-0100），不另写直写流程。
- 中文来源词的有限简体别名由 `generate_genre_simplified.py` 使用已有开发依赖 OpenCC 1.4.2（Apache-2.0）生成，冲突拒绝；别名保存在 `genre_taxonomy` 的独立区块，历史词表从同一个 Git 版本读取完整规则，运行时无需 OpenCC。覆盖全部登记词的繁简投影测试保留人工决定优先级和未知词出口，无新增依赖或真实账本写入。
- 一件事只留一个标签名：`catalog_rules.RETIRED_TAGS` 存「不该再用的写法 → 规范名」，唯一写入口是 `scripts/rename_retired_tags.py --apply --backup`，实体按 `entities.merge_entity` 并、旧名留作别名；`pixiv_tag` 是作者原话，不参与。规范名取馆藏通行写法（`合集` 来自文件名，`混合集` 全来自已关停的 Stash 导入，ADR-0021）。
- 手动添加标签复用 `current_tags`，API 返回规范名，详情缓存、最近使用与撤销沿用该名称。`core/tags.ts` 保留 `OL制服`、`JK制服` 完整名称，制服归外观，`秘书OL` 归角色。
- 没有接替者的标签登记在 `catalog_rules.DROPPED_TAGS`（`乳系`、`足系` 这类粗桶）：落库时经 `current_tags` 丢掉，`rename_retired_tags.py` 连实体删掉。映射改了原词去向后，`scripts/reproject_snapshot_tags.py --since <改动前的提交>` 按 `raw_snapshot` 新旧各算一遍、只加减差值；不给 `--since` 是按现映射补缺、一个不删。加的走并集（`auto:metadata-tags@<时间>`，可整批撤回），删的只删那组来源下的行。
- 来源原词经 `genre_taxonomy` 与人工词表投影。`peach.stale_candidates` 识别未收录原词中的整串 ASCII 字母，`scripts/drop_stale_genre_candidates.py --apply` 摘下候选并留备份，下一趟「只采集」按 `_missing_fields` 重取。JavDB 类别使用中文 locale；已有标签切换来源走 `scrape_codes` 番号候选重取。`reproject_snapshot_tags.py --since` 只重投既有快照的映射差异，不代表完成来源切换。
- 未收录 genre 在复核卡上就地收录：`genre_decision`（迁移 0026）存「规范化来源词 → 中文标签」，留空即判非内容词；`peach.genre_decisions` 只管读写，映射仍在 `genre_taxonomy.map_genres`。收录后 `extract_peach_fields`、`scrape_codes.py`、`harvest_kmib.py`、`fetch_fc2_metadata.py` 都当它已知，排队中的候选由 `web_review._fold_genre_decisions` 当场折进值里；收录只改词表，落库仍要按「通过」。候选词表只给静态表已投影到的中文标签。只在 `warnings` 里有中文提示的旧候选由 `genres_in_warning` 反解出原文，不必重抓。
- 补抓按番号发行面分流来源，要求来源认得出所查番号、冷却按连败触发且会过期；无码发行站的片与粘连的版次标记也给得出徽章。
- 「只采集」的「说过没有」记忆与 `_answered_sources` 见 [来源采集](SOURCING.md#按内容类型的来源链)；只有明确答复「没有」（HTTP 404、所有渠道无候选，即 `jav_cover_fetch.NotFound`）才记，超时与网络故障不记。没有番号的视频（创作者作品、裸文件）只登记本地海报，不列为问题项。相机文件名派生的旧伪番号（`VIDEO-2022`、`IMG-1734`）由 `scripts/clear_camera_filename_codes.py` 清掉，用户或复核写下的番号不碰。
- reader 的 `/review` 通过严格 Peach CA HTTPS 读取 writer 的归一化 JSON 并原子缓存；决定按钮和所有关注写操作仍锁定。
- macOS Ledger 同步在共享根判为 `offline` 时先经 NetFS 挂载 `peach-sync` 再重判，挂载失败才保留离线结果，不弹阻塞认证框。
- 浏览历史增量采集使用 SQLite backup API 与 `browserexport`，也接受 Google Takeout ZIP；原始 URL 与标题只留本机私有目录，聚合候选不写 ledger。
- 首次设置的可选历史引导转到 `/taste?onboarding=1`，用现有读取与导入入口；指南链接按 [Google 导出说明](https://support.google.com/accounts/answer/3024190?hl=zh-Hans) 与 [browserexport](https://github.com/purarue/browserexport) 核验（2026-09-06）。
- 实体事实包默认保存在私有 Peach 数据目录：`scripts/seed_pack.py export` 只读账本生成 `state/seed/entities.json`，不随仓库或发行包分发（ADR-0111）。Windows 构建与制品验收均拒绝发行资源中的 `seed` 目录。已有私有包由扫描结算后的种子后继导入，幂等键绑定内容摘要。v2 结构校验复用已固定的 Pydantic，SQLite 历史表记录版本、修订与累计精确撤销，防止旧包回放；别名保留源 confidence，用户资料由归属保护。种子不含图片；包中缺席不视为错误撤销。分发边界、复核与纠错见 ADR-0073、ADR-0075、[ADR-0106](adr/0106-seed-correction-history-and-explicit-withdrawals.md)。
- 数据管理首屏直接复用实际 `cleanupfieldset` 正文和操作条，只有计数等待取数；资源同步与重复文件网盘操作根据 `/api/sources` 已配置来源显示，离线来源保留入口。资源同步的检查与执行覆盖已配置的 local、115、PikPak，一次检查同时报失效记录、空文件夹与孤儿缓存；按目录清理也支持本地磁盘。

## 必须复用的成熟实现

下表每行是一项能力：中列是必须复用的实现（带固定版本与许可证），右列是 Peach 自己负责的部分和不能越过的约束。

| 能力 | 复用实现 | Peach 负责 |
|---|---|---|
| 本机文件夹对话框 | Windows 自带 `powershell.exe` 经 `Add-Type` 调 Shell 的 `IFileOpenDialog`（带地址栏），STA 线程里 `SetThreadDpiAwarenessContext` 设 Per-Monitor V2；macOS `osascript` 的 `choose folder` | `src/peach/folder_picker.py` 只拼命令、区分取消与失败、一次只开一个，用完恢复原 DPI 上下文（托盘的进程 DPI 不传给子进程）；不引 tkinter 或 GUI 框架。证书子进程用 `CREATE_NO_WINDOW`，保留 OpenSSL 退出码与错误输出 |
| HTTP | 全项目共用的 `httpx.Client`/transport；FANBOX 公开 `post.info` 用 `curl_cffi`；对外 UA 统一取 `peach.user_agent.USER_AGENT`（与本机 Chrome 同大版本，ADR-0060），HTTPX、来源连接器、FFmpeg 抽帧与脚本共用 | 来源策略、DTO、脱敏、站点限定、大小上限；不求解机器人质询，标准 UA 不保证放行 |
| 追更来源接口 | FANBOX 公开帖子 API（详情只用用户的可选 Cookie 与 curl_cffi `chrome150`）、kemono 系公开 JSON API（`Accept: text/css`，站点自述的抓取路径）、rule34.xxx 官方 dapi（需 API key）与公开 tag 补全、Paheal 标签/详情页、Gofile contents API（需 Premium token）、f95zone `latest_data.php`、站内搜索（需登录 cookie）、线程页与 masked XHR、simpcity 线程页与站内搜索（需登录 cookie；只请求规范地址，不解 DDoS-Guard 质询） | 连接器边界、凭据隔离、多媒体顺序、文件站目标校验、变体与跨站重复判定、候选复核与批准后的 online asset 投影 |
| XenForo 论坛解析 | gallery-dl `v1.32.11` / `2adf2a8e`（GPL-2.0）的 `extractor/xenforo.py` 只作协议证据（cookie 优先的登录、`article[data-content]` 楼层、`.pageNav` 分页、`data-s9e-mediaembed` 嵌入）；cyberdrop-dl `5.6.21`（GPL-3.0，2024-09 起按站方要求下线 SimpCity）只作历史参照；两者都是完整下载器，不进运行时 | f95zone 与 simpcity 共用 `_xenforo_posts`／`_xenforo_thread_title`／`_xenforo_search_threads`（先取会话绑定的 `_xfToken` 再 POST `/search/search`，带回版块标签）；cookie 只发回来源站，末页由分页导航自算（HTTPX 跟随重定向会丢显式 Cookie），过滤纯讨论楼层。站点 cookie 前缀会轮换（gallery-dl 写死的 `ogaddgmetaprof_user` 已换成 `yMziCv8BrCZz1o7_`），所以不认 cookie 名、只透传整条 Cookie 头 |
| 文件系统事件 | `watchdog`（Apache-2.0）加定期对账，只订阅 `local` 来源的根；网盘挂载不订阅，递归 watcher 在网络挂载上就是轮询。CloudDrive2 的 webhook 请求形态与「云端路径不进 pathlib」取自 `sqzw-x/amane`（GPL，只借设计，见 [docs/reference-snapshots/amane-watcher.md](reference-snapshots/amane-watcher.md)） | `src/peach/push_discovery.py` 负责去抖、大小稳定的写完判定、前缀表映射、共享密钥与来源校验；登记本身调 `scan.ingest_path`，与全量扫描同一条 upsert（ADR-0041） |
| HTML 适配器 | Beautiful Soup 4.15.0（MIT） | 来源专用选择器和来源记录 |
| 位图 | Pillow | 头像/Logo 质量和来源策略 |
| SVG 光栅化 | `resvg-py`（resvg，MPL-2.0 绑定） | 只把站点自己的矢量图标转成位图交给 Pillow（threads 的 app 图标只有 SVG，2026-09-02 核对）。不选 cairosvg：它在 Windows 要另装 cairo 原生库，resvg-py 是 abi3 轮子，win_amd64 / macosx_11_0_arm64 / macosx_10_12_x86_64 都有官方预编译。候选发现、比例判定、缓存与回退在 Peach。`images.bake_square_vector` 用标准库 ElementTree 包一层外层 SVG 做方形归一，产物仍是矢量，栅格化只用来判配白底还是深底 |
| 搜索 | SQLite FTS5 | 索引字段、排序、profile 感知筛选 |
| 相关推荐 | OpenAver `8cc17e50453d9f69a81f5fee1a072df80f7aab73` 的 Tag IDF + 系列／片商／出演者规则只作固定算法参考（MIT） | 独立实现规范实体评分、MMR 多样性、稳定 seed、解释原因与负反馈边界；不复制上游 UI／源码 |
| 女优姓名对照 | `li-peifeng/Jav-Actors-Mapping` 的固定 revision，仅作私有输入（仓库未声明许可证，不随 Peach 分发） | 精确匹配、冲突复核、别名、来源与真实 ledger 写入 |
| 女优头像候选 | Gfriends 的 GitHub raw 索引与单张媒体（只作外部 Provider，不克隆图库）；r18.dev 人物对象给出的 DMM 官方 `actjpgs` 缩略图只作精确身份绑定的首次头像 | 名字链、质量档位、格式/尺寸/SHA-256 门槛、候选缓存、provenance、健康统计和人工复核；首次头像只在人物主名精确一致、实体已连到同一资产且没有在位头像时安装。自动档案缩略图未达标准尺寸时进入图库升级核验；没有可靠图库候选则保留档案照。换头像端点记录人工选择，自动后继保留该选择 |
| Gfriends 索引读法 | `src/peach/gfriends.py` | 页面与批处理共用：`Filetree.json` 解析、名字链匹配、`quality_key` 排序、raw 地址与本地缓存缓存有效期。**目录前缀是来源优先级，不是清晰度**（上游 README 写的「质量升序」不准）：`0-` 网友投稿、`1-`～`8-` 写真机构与片商官方、往后是原图两三百像素的大型数据库，排第一的是最该先试的一张；`AI-Fix-` 是上游放大去水印版，去掉前缀取原件 |
| 「这个地址能不能让 Peach 替人去取」 | `src/peach/http.py` 的 `public_https_url` + `resolves_publicly` | 追更图片代理与手填头像地址共用：必须 https 与公网域名，不收 IP 字面量与用户信息，解析出的每个地址都要 `is_global`；否则 Peach 就是能访问路由器、NAS 与本机端口的跳板 |
| 头像写入 | `src/peach/avatar_provider.install_entity_avatar` | 采集脚本、复核页与换头像共用：`.img` 经临时文件原子替换，`.ct`、`.provenance.json`、`.face.json` 一起换；检不出脸要删掉旧 `.face.json`，否则页面按上一张的脸框取景且看不出错 |
| 厂牌 Logo 候选 | 厂牌官网确认的社交 handle → unavatar URL 解析 → 平台 CDN 单图 | handle 归属、内容缓存、方形归一、精确/感知哈希、provenance、健康统计与变化复核 |
| 厂牌字标名录 | 发行商与平台自己的厂牌名录，入口在 `harvest_maker_directories.DIRECTORIES`：MGStage `/ppv/makers.php`、Prestige `/api/maker`、KMP `/label`（大半是 SVG）与 jae.tokyo 展会名录 | slug↔账本对账（四路判据，空罗马字形不可比）、按形状分三张指定表（方标装大位、字标烤方两位共用、方标只管小位）、改地址后靠 provenance 边车重新收人、复核 CSV 与安装闸门。存入口不存图片地址（KMP 文件名带时间戳），不推导 URL、不猜名字 |
| JAV 元数据查询 | 每个站只有一个归属（ADR-0048）：自写解析器持有 r18.dev、DMM／FANZA、一本道、FC2、fc2cmadb、FC2PPV-DB、JAVten、JavArchive、AVBase、JavBus、javdb，片商官网与转载站经 amane 桥（ADR-0043、ADR-0044）；MetaTube SDK `6a5e6128c725187aeaf921d48ed7d9cd9f30671b`（Apache-2.0）只作来源身份与丰富字段模型参考；DMM 的 GraphQL 接口地址、`ppvContent` 与 `legacySearchPPV` 两条查询的取法参照 OpenAver（MIT）`core/scrapers/dmm.py`，cid 匹配与身份核对是 Peach 自己的（ADR-0059） | 只发规范番号；Peach 管来源链（`metadata_routes`）、`provider_id`／`content_id`、逐字段优先级、原始与目录证据、健康统计、候选复核与 ledger 投影。amane 的 POC 判据与字段缺口见 [取证](reference-snapshots/amane-crawlers-poc.md) |
| Javinizer-Go 历史快照 | `sources/metadata/javinizer-go/<番号>/<来源>.json`，由 Javinizer-Go v1.5.x（MIT，`dd56998328d078c9baf68ff4fde2e6fcaa2a691a`）在 2026-09 之前取回；二进制与快照留在磁盘，没有调用路径（ADR-0044） | 只作离线证据：封面层读它的 `cover_url` 与 `content_id`，别名解析读企划名义，账本里 `javinizer:<站>:<字段>` 的 provenance 按 `metadata_policy.HISTORICAL_SOURCES` 认级别。`scrape_codes` 写进同一目录的新快照 `provider` 记解析器名、`provider_version` 记 Peach 版本 |
| amane 刮削站点（官方档 makers、prestige、faleno、dahlia、mgstage；转载站 fc2club、freejavbt、airav、avsox） | amane `cc42cee6ecf290cf05af1caa347d2dc2d5aeffcd`（v0.20.0，GPL-3.0），经 `tools/amane-bridge/` 薄桥子进程接入（ADR-0043、ADR-0048；`makers` 是 amane 的 `official` 模块）：独立 `pyproject.toml`／`uv.lock` 钉 sha，venv 建在 `<Peach 数据目录>/tools/amane-bridge/.venv`，桥只用 `amane.crawlers.sites.<站>` 与 `amane.net.*`，不碰 `aggregate` | GPL-3.0 与 Peach 的 AGPL-3.0-or-later 以进程边界相接，仓库与分发件只含清单、锁与桥脚本，amane 源码由 uv 按锁下载。字段、失败分档与无写入 POC 见 [上游复核](reference-snapshots/upstream-review.md)。Prestige 封套与正封分别投影封面和海报，配信日不作发行日；AIRAV 不解析试看时长。上游 `WebClient` 以 `verify=False` 发公开请求、不带凭据；403/406 可另轮换三次指纹，Peach 保持 60 秒子进程总时限。Peach 管来源链位置、身份核对、冷却、候选与结算 |
| 已确认厂牌的目录归位 | Javinizer-Go v1.5.2 organizer（MIT）只作冲突预检、模板化目录和回滚边界的协议参考，不调用它，不让它持有 Peach ledger | `rehome_unknown_jav.py` 只消费人工确认映射；先出逐文件 CSV，拒绝扁平化重名与厂牌冲突，SQLite 备份后移动文件并同步 Peach 路径／实体 provenance |
| FC2 目录元数据与跨号证据 | 已缓存的 fc2cmadb Inertia `article`／评论；Javinizer-Go v1.5.2 的 FC2 解析器（MIT）只作商品页字段边界参考 | 旧文章仍有标题、原始标签、日期、时长、卖家、FC2 CDN 封面与 `comments`（2026-08-31 登录态实测）；无歧义标签译成现有词表，标题／标签进 `/review`，`w1200` 封面过尺寸与解码门槛落生成产物；pair、合集/分片保护、hash/时长/尺寸佐证、库外 evidence、健康统计与复核归 Peach，不依赖 FC2-Leak-Detector/JavSP，镜像候选不直写 ledger |
| 缺索引 MP4 重建 | untrunc（anthwlock `9d86ec9`，GPL-2.0-or-later）与兼容的 FFmpeg 8.1.3 静态构建，固定来源及哈希见 `scripts/untrunc-sources.json`。Windows 安装包与免安装包携带 `tools/untrunc`，同一 Release 提供完整对应源码；原生样片实测覆盖尾部 `moov` 缺失后的 H.264/AAC 恢复、时长与全片解码。显式 `PEACH_UNTRUNC` 优先，其后依次查随包目录、数据工具与 PATH | `src/peach/mp4recover.py` 挑参照（同目录文件名最近的先试，最多 4 部）、验收（解码错误 ≤3 行、音视频时长差 ≤5%）、换回原路径并把坏原件改名成同目录的 `.文件名.peach-original`；缺整个 `moov` 的片子哪里都打不开，所以结果替换原文件，不像缺 `ctts` 那样另存头。实测见 [取证](SOURCING.md#媒体与关注实测) |
| 媒体探测/转码 | Windows 独立包从固定源码交叉编译 FFmpeg 9.0.2、x264 与 nv-codec-headers，按 GPL-3.0-or-later 分发；版本、来源和哈希见 `scripts/ffmpeg-sources.json`。工具与完整对应源码一起构建，安装包和免安装 ZIP 共用经校验的 `tools/ffmpeg`。源码与 CI 媒体测试仍可使用 PATH 工具（[取证](SOURCING.md#依赖与工具实测)） | 容器与编码分别检查：MP4/M4V 的 H.264 8-bit 加兼容音轨直出，其余容器里的 H.264 8-bit 只换 MP4 封装；重编码按[硬件转码后端](#硬件转码后端)选择，macOS 默认软件转码 |
| HTML5/HLS/DASH 播放 | Video.js 10.1.0 的 `@videojs/html`、`@videojs/hlsjs-video` 与 `@videojs/dash-video`，通过官方 `@videojs/cdn` 本地固定模块分发（Apache-2.0） | 流方案、授权、稳定时长、回退顺序和统计面板；详情不兼容片源复用 HlsSegmentService 与 FFmpeg 按六秒编码 H.264/AAC，独立缓存、绝对时间轴及会话取消，无整片预转码 |
| 播放器设置与影院布局 | Video.js 10.1.0 的播放器状态、原生媒体事件、播放／静音／全屏／画中画按钮与进度／音量滑条；YouTube `e937390a` 实际 DOM／CSS／JS 给几何、状态、动画和图形证据 | Peach 皮肤组合氛围模式、倍速、真实清晰度和影院模式：59 px 两排控制栏、40→111 px 横向音量、4→6 px 进度、右侧共享胶囊、整行悬停的 274 px 设置菜单、视口级全屏；普通视图 `contain`，全屏 `cover`。播放会话与同一枚原生视频经过舞台和小窗整体搬移，状态、字幕与上报保留；设置、时间、预览、统计、字幕菜单、中央提示和错误反馈属于 Peach。皮肤沿用本地 SVG 与 spinner，不复制上游按钮、滑条或媒体引擎的控制逻辑 |
| 播放器时刻预览 | Video.js 原生进度控件 + Peach 的 `/timeline?id=&s=`（10×10 接触印相），取不到时退到 `/poster?id=&c=0…8` 九宫格切片 | `peach.timeline_sheets` 按每 10 或 30 秒一帧预铺，只覆盖 `location='local'`（网盘每抽一帧都回源，[流量实测](SOURCING.md#媒体与关注实测)）；其余退回只给近似时刻的九宫格。抽帧与拼图共用 `peach.frame_capture`。`videojs-vtt-thumbnails`／`videojs-sprite-thumbnails` 要另建 sprite/VTT 契约与运行时依赖，格子换算在 Peach 是一行整除，不引入 |
| 外挂字幕 sidecar | 浏览器原生 `TextTrack` 与 [WebVTT 规范](https://www.w3.org/TR/webvtt1/)；配对复用 `catalog_rules` 的 `VERSION_TAIL_TOKENS` 与 `release_code_from_filename`。行为证据：sakuramediabe `import_service.py`／`movie_numbers.py` 的 `subtitle_matches_movie_number`（同目录、纯番号、不回退同名），NeoAVDC `organizeMedia.ts`／`parseNumber.ts` 的 `isSubtitleFile`（`.srt .ass .sub .vtt .ssa`，按视频主名前缀）；Peach 的判据是两者的并集加顺序 | `subtitles.py` 只做同目录配对（exact／suffix／code／orphan）、`asset_subtitle` 幂等登记、srt/ass/ssa → WebVTT。不用 `pysubs2`（为几十行加运行时依赖）和 FFmpeg（每次起子进程、报不出「编码认不出来」），编码要认 GBK／Big5／Shift_JIS，不用几乎吞下任何字节的 `gb18030`。内封字幕轨不在此列，`media_probe.py` 只取 `v:0`，流信息不落库 |
| 分卷文件命名 | [Plex 官方命名](https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/)的 `cd/disc/disk/dvd/part/pt + 数字` 与 [Kodi 官方 File Stacking](https://kodi.wiki/view/File_stacking)只作行为证据；运行时复用当前树的 `part_marker`，不新增扫描器依赖 | 兼容裸数字、紧接番号的 A–H 与 cd 序号、fhd 序号及圈号标题，共有版次尾缀先剥离再取卷标。FC2 的明确数字合集允许缺集和零起始；本篇与唯一 SP、gift、特典可合卡，完整版与数字分卷混合、重复标签不自动合并。每个 asset 与播放会话保留，不拼接或改写媒体 |
| 照片灯箱轮播 | Swiper 14.3.0（MIT，本地固定版本，按需加载 CSS／JS）的 Thumbs / Keyboard / Zoom 模块，由 React 灯箱（`frontend/src/react/photo-lightbox/`）只经核心 API 驱动，不用 Swiper 的 React 封装 | 样式与脚本都就绪才构造轮播，保留 scoped 单 slide 结构样式防首载重叠；Swiper 管轮播、键盘、缩放与缩略图，Peach 管图集来源与顺序、缩略图居中、相对原图百分比、适应窗口／原大小、缩略图缓存与计费口径；图片墙是 CSS 网格，不经 Swiper |
| 浏览器历史 | `react-router` 8.4.0（MIT）的 `createBrowserHistory`（`v5Compat`），由主包里的共享源码提供（`frontend/src/history/`）；不用 `history` 5.3.0（2022 年后不再发版、另带 `@babel/runtime`，条目格式与 React Router 内置的那份并存） | 壳写地址的入口 `shellNavigate`：路径按当前地址解析，清理地址时透传当前条目的状态；一建好就接上唯一的监听位再分发，每次变化领一个导航序号 `seq`，认领写地址保持页面打开序号、无人认领时领新 `openEpoch`；页面元素按页面打开序号挂 key，覆盖元素按 `seq` 挂 key。`useRouteOpen` 在 `pageOpens` 就绪后排进微任务，普通点卡交一次性请求，`initialOverlayOpened` 只允冷启动首次打开；队列由 `QueueRequests` 读 `queueOpens` 接手意图，取齐实际条目后才写地址。背景由条目记下，启动不读，后退前进按条目恢复 |
| 客户端导航 | `react-router` 8.4.0 的 `<Router>`（Declarative 模式）与 `<Routes>`，`navigator` 是上面那一份历史（`frontend/src/react/router/`）；不用 `unstable_HistoryRouter`：它把更新包进 `startTransition`，接连两次变化并成一次渲染 | 每条路径都有元素，按匹配打开；每次没人认领的历史变化领一个页面打开序号，元素按代次重挂重取或就地推（`managed-routes.tsx`、`router/pages/`） |
| 页面返回 | 共享 React Router 历史的 `go(-1)`、条目 `key` 和 TanStack Query 的分页缓存 | 侧栏复用既有图标按钮；名册按历史条目保存位置与页数，返回时复用缓存，过期时按原页数取齐，完成显示后恢复滚动；无新增依赖 |
| 导航排序 | 浏览器原生 HTML Drag and Drop，侧栏 `sidebar/sidebar-island.tsx` 与设置面板 `settings-panel/sidebar-order.tsx` 各接一份 | 桌面鼠标直接拖动、落点提示、上下移动按钮作为键盘与触屏回退、`localStorage` 持久化；不为单列排序引入额外运行时依赖 |
| 播放列表队列拖动排序 | `frontend/src/ui-kit/controls.ts` 的 `wireDragReorder()`，详情页岛经 `helpers.wireDragReorder` 调用 | `dragstart` 标记被拖行，`dragover` 按指针在行的上半或下半给落点线，`drop` 把整份新顺序交给调用方落库；落点线、抓手与焦点样式在共用件里 |
| 图标 | 固定版本的本地 Lucide 子集；Health Icons 24 px outline（CC0）用于领域图标；Phosphor regular 填充字形（MIT）只用在描边说不清的地方（字母表 Aa、播放列表） | 标签、状态和交互设计 |
| 资源文本中间省略 | Vercel Geist `MiddleTruncate` 行为契约 + 浏览器原生 `ResizeObserver`、`Intl.Segmenter`、Canvas 测量 | 文件名、路径、URL、ID 等资源标识用 `data-middle-truncate`；标题、说明、人名、标签等语义文本保留末尾省略；页面源测试登记全部末尾省略选择器，新增截断未先分类会失败 |
| 定时轮询 | APScheduler（MIT，固定稳定版；3.x `BackgroundScheduler` / interval trigger） | 只在 ledger writer 启动、持久频率、首次延迟、单实例、手动/自动互斥、运行状态与来源错误汇总 |
| 局域网发现 | Python zeroconf | 服务生命周期和真实客户端验收 |
| 生成产物跨机同步 | Syncthing 2.1.x，Windows send-only → Mac receive-only | 目录划分、忽略规则、方向固定与「Mac 不发布正式产物」的边界 |
| Windows 托盘 | pystray 0.19.5（LGPLv3）、Pillow、Win32 Per-Monitor V2 DPI | Peach 服务归属、后台更新检查、菜单动作、品牌图标 |
| Windows 托盘恢复 | Windows 自带 `OpenProcess`、`GetProcessTimes`、[`WaitForSingleObject`](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)、`GetExitCodeProcess`；Python 3.12+ 标准库 `ctypes` / `subprocess`，无新增依赖或分发体积 | `tray_lifecycle` 保留明确退出意图、创建时间校验、3 次 / 5 分钟预算与重启交接。pystray 0.19.5 的 `_message_handlers` 接收 `WM_STOP` / `WM_ENDSESSION`，已核对安装包源码；Windows 临时进程实验证明强制退出能恢复，明确退出不会恢复。原生 API 随受支持的 Windows 维护；pystray 使用项目固定版本。不采用 Task Scheduler `RestartOnFailure`：它只管理任务启动的实例，桌面快捷方式与现有重启脚本不归其管理；不新增 psutil 运行时依赖，进程句柄已覆盖等待与退出码。已知边界是监视器随整棵进程树被终止时不能恢复。 |
| macOS 菜单栏 | `pyobjc-framework-Cocoa==12.2.2`（MIT）提供 AppKit / PyObjCTools / objc | 附件应用策略、18 pt template 图、服务归属与菜单动作 |
| macOS 拖放安装镜像 | [dmgbuild 1.6.7](https://github.com/dmgbuild/dmgbuild)（MIT，Python ≥3.10，macOS）直接写 Finder 元数据；仅安装于 `build` extra。纯 Python wheel 约 35 KB，依赖 `ds-store`、`mac-alias`；固定传递版本见 `uv.lock` | 已读取固定版的文件复制、背景多分辨率合成与图标布局实现；背景渲染复用 resvg-py 0.5.0，无运行时新增依赖。Peach 提供 640×420 拖放布局、双分辨率背景和应用内许可证；单元验证配置与图片尺寸，发布验收实际挂载镜像。磁盘镜像创建依赖 macOS 的 hdiutil；无需 Finder 自动化权限，挂载或文件系统失败会终止构建 |
| 人脸取景 | `opencv-python-headless==5.0.0.93`（Apache-2.0）的 `cv2.FaceDetectorYN` + opencv_zoo 定版模型 `face_detection_yunet_2023mar.onnx`（sha256 `8f2383e4…52fa4`，232 KB，放 `peach-data/tools/yunet/`，不进 Git）；MetaTube SDK `6a5e6128c725187aeaf921d48ed7d9cd9f30671b` 的主脸聚类只作算法参考 | 头像／封面离线脚本共用 `peach.face_detect`（主脸、归一化焦点与 sidecar）。`detect` 按长边 320/640/1280 各检一次、分数取中位数；`main_face` 先筛掉分数落后最好那张 0.1 以上的框，再取最大。不用 Haar 级联（OpenCV 5 wheel 里没有、检出率低）与 Pigo v1.4.6（有无脸误报）。[实测](SOURCING.md#人脸与水印实测) |
| 人脸比对 | 同一 OpenCV 的 `cv2.FaceRecognizerSF` + opencv_zoo 定版模型 `face_recognition_sface_2021dec.onnx`（sha256 `0ba9fbfa…34e79`，与 LFS 指针的 oid 一致，37 MB，放 `peach-data/tools/sface/`，不进 Git） | `peach.face_match` 提特征、算余弦，阈值用 SFace 官方的 0.363；补头像后继拿图库候选与单人封面截到的脸比（ADR-0056），小脸先放大再检（ADR-0070）。检脸用 `peach.face_detect`，取模型走同一条 `fetch_model`。不引 `face_recognition`／dlib（要编译、体积大）与 InsightFace（模型限非商用、需 onnxruntime）。[实测](SOURCING.md#人脸与水印实测) |
| 头像水印检出 | 同一 OpenCV 的 `cv2.dnn.TextDetectionModel_DB` + opencv_zoo 定版模型 `text_detection_en_ppocrv3_2023may.onnx`（sha256 `03f550c6…66587`，2.4 MB，放 `peach-data/tools/ppocr/`，不进 Git） | `peach.avatar_watermark` 检出，`scripts/scrub_avatar_watermarks.py` 复核与移除。不用 MSER 自写启发式（抓不到半透明水印）。`MIN_SCORE` 0.9、框宽占图宽 ≥ 4%、框整体落在距边 15% 带内；半透明水印给不出框，所以检出只是候选，人看标注图确认、漏的自己补框。移除优先裁边、不 inpaint。[实测](SOURCING.md#人脸与水印实测) |
| 正封取景 | 同一 OpenCV 的 `cv2.Sobel` 求列向梯度；NeoAVDC `c7a430c64013c97a0213cd8a57e2ff5696793a86`（MIT）与 sakuramediabe `9c6a31915c9a364c9d6717798575daf9445916bd`（GPL-3.0，只作算法边界参考）的封套几何实测值 | `peach.jav_poster_crop` 给出正封取景框（折痕列到右下角、满高），`scripts/poster_crop_boxes.py` 批量写边车，产物是坐标不是图片。折痕判据见 `peach-jav-cover-workflow`，外部参照、实测与手工框规则见 [取证](SOURCING.md#正封取景实测) |
| 115 文件清单 | `p115client==0.0.9.6.5.1`（MIT） | 只在显式 SHA-1 对账脚本中安装，Peach 负责 ledger 事务、备份与写入门槛 |
| 视频出处/片尾证据 | 现有 FFmpeg 抽帧 + Windows.Media.Ocr WinRT Provider（Windows PowerShell 5.1 固定适配器） | 有界首尾采样、缓存、来源/Full version 分类、健康统计与人工复核 |
| 参考产品行为 | 当前线上交互 + 有版本的公开 DOM/CSS/JS；取不到源码时用精确截图测量 | 证据登记、无障碍、Peach 差异、回归检查 |
| 浏览器历史解析 | `browserexport==0.4.4`（MIT、Python ≥3.9，约 35 KB wheel；Chrome/Firefox/Zen/Safari 的 SQLite 解析） | 消费者是 `/taste` 的本机读取与导出导入；Peach 保留 SQLite backup、Takeout、私有原始存储、域名分析和 candidate 生成，在 Windows 自己关只读连接。按必需列识别格式，以局部 Schema 补可选元数据列，时间与 Visit 转换继续复用上游。国产浏览器目录参考 HackBrowserData，不引入其运行依赖；目录与合成 SQLite 的最小验证、115 下载记录和旧搜狗限制见[浏览器历史导入](BROWSER_HISTORY.md)。跨主机同步须显式导出、传输并按来源去重合并 |
| Rule34.xxx / Paheal 高清封面 | 固定参考 gallery-dl `86047cf67a12bdb6ff1085774f8ad9fc347e8da9`（GPL-2.0，只作协议行为证据，不引入运行时）；运行时复用现有 FFmpeg | booru 按 `sample_url`/`preview_url`/`file_url` 回退，Paheal 只取原始 `file_url` 再抽帧：FFmpeg `blackframe` 的 `lavfi.blackframe.pblack` 在开头 30 秒选第一张黑色像素低于 98% 的帧，版本化缓存键淘汰旧黑帧（不用 GPL、默认取 10% 位置的 ffmpegthumbnailer）。Peach 管 URL 白名单、同源代理、双并发抽帧、缓存与低清回退 |
| FANBOX 正文解析 | PixivUtil2 `v20251112` / `e537e96` 的公开正文模型（BSD-2-Clause，只复用数据模型，不引入整套下载器） | Peach 的独立规范化 DTO 覆盖 image/text/file/article/video/entry、`fileMap`、`embedMap`、`urlEmbedMap` 和旧 HTML 正文，保留正文顺序、稳定去重、可播放媒体与文件页边界；许可证依据写在实现头部，传输固定 `curl_cffi` |
| 云下载传输 | `grpcio==1.84.0` 与 `protobuf==7.36.2`（消息代码由 grpcio-tools 1.84.0 生成后提交），首个消费者 `downloads_clouddrive` | CloudDrive2 官方 proto 的七个方法子集、令牌存 CredentialStore、任务表与失败分类（`downloads.py`，ADR-0089） |
| 本地 NFO 导入 | Kodi／Jellyfin NFO 协议（2026-09-06 核对 [Kodi](https://kodi.wiki/view/NFO_files/Movies)、[Jellyfin](https://jellyfin.org/docs/general/server/metadata/nfo/)），标准库 ElementTree，图片走项目固定版 Pillow；实现在 `library_nfo.py` | 只适配影片、单集与音乐视频（整剧、专辑、播放记录与远端图片引用不套用），拒绝 DTD、超大输入与越目录图片引用。同名边车优先，`movie.nfo` 与通用海报只用于单影片目录；正片和图片同属一组连号（`(1).mp4` 配 `(1).jpg`…）时同名图是图集的一张。已知内容词投影为中文标签，画质、促销、发行属性与演员编成丢弃，未收录的自定义标签保留；远端补厂牌、导演、发行商、时长与图片出处，人物主名精确一致时补 DMM id、假名、罗马字与首次头像，不替换演员真值。网络复用 R18 JSON 入口、SourceTransport、头像缓存与封面解析器。候选按资产 ID 与路径定位，批准时复核目标，时长证据不覆盖探测时长（ADR-0029） |
| JAV 高清封面 | Javinizer-Go `dd56998328d078c9baf68ff4fde2e6fcaa2a691a`（MIT）的 DMM modern `awsimgsrc.dmm.com/dig/...` 映射与尺寸门槛；Prestige 公开 API 的查询模型参考 MDCX `58e3f930f2e864fceb8a53ceef818716e2a6413d`（GPL-3.0，只作协议证据） | 候选、Range 量尺寸、面积最大者胜出与仅更大才原子升级见 `peach-jav-cover-workflow`。批量流程不请求社区来源，历史快照里的社区站只借厂牌；既有库采集在官方落空时经 `peach.community_catalog` 查 AVBase（取商品号认得出这个番号的店铺条目，名寄せ的标题可能来自合集）、JavBus 与 JavDB，两个图源先按 dHash 求一致，取景不同时用 OpenCV ORB（相似变换内点 ≥60 算同一张）。JavDB 只参与复核，不作为安装候选；其他单一图源留空 `verified_by`，遇验证页不绕过（ADR-0107）。MDC-NG 只证明 Amazon 日本渠道存在，只留 POC 候选。不写 ledger |

依赖的第一个消费者及其隔离测试必须在同一改动落地，否则不引入依赖。
表里没有单列的 Python 依赖及首个消费者：`numpy`（vision 组，`face_detect.decode` 直接 import）、`socksio`（PikPak 的 SOCKS 代理）、
`opencc==1.4.2`（naming 组，`harvest_javdb_cn_names` 繁转简与字形表复核）、`psutil`（开发依赖，`agent_worktree.py processes`）。前端每个包的用途见 [前端岛层](FRONTEND.md#依赖清单)，引入时机按 ADR-0031「前端基础库」。
Python、npm 与 GitHub Actions 的版本由 `.github/dependabot.yml` 每周检查；固定前端文件由
`package-lock.json` 和 `scripts/vendor_web_dependencies.mjs` 重建并核对来源、许可证与 SHA-256。

## 静态检查

Windows 媒体子进程复用 Python 标准库 `subprocess.CREATE_NO_WINDOW`：入库探测、抽帧、拼图、预览、关注封面与媒体修复均显式设置，输出捕获、超时和取消按各入口原有契约处理。该标记由 Windows 原生支持，不引入依赖；见 [Python 文档](https://docs.python.org/3/library/subprocess.html#subprocess.CREATE_NO_WINDOW) 与 [Windows 创建标记](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)。

抽帧与拼图通过现有 Pillow 12.3.0（HPND）完整解码输出图像，不按压缩文件大小推断有效性。复用既有 FFmpeg、色彩元数据重试与 `support_bundle`：失败记录包含采样秒数、时长、退出码、超时、工具来源和各次尝试的错误输出，错误原文截断并脱敏。诊断 ZIP 收集处理状态、最近 8 份问题清单及启动、更新、隧道证据，每文件最多读取尾部 96 KiB，媒体名称留在本机。有效的不足 1 KiB JPEG、中文路径、失败重试和诊断导出均有回归；FFmpeg 参数依据[官方文档](https://ffmpeg.org/ffmpeg.html)，不引入 ffmpeg-python 等命令包装层，不写真实 ledger。核验范围见 [日志与诊断核验](DIAGNOSTICS.md)。

Windows FFmpeg 9.0.2 静态链接 VideoLAN dav1d 1.5.4（BSD-2-Clause，官方源码 1,038,852 字节，Meson + NASM，支持 Windows x64 与 8/10-bit AV1）。版本、HTTPS 来源与 SHA256 固定在 `scripts/ffmpeg-sources.json`，源码和许可证随对应源码归档分发；完整工具的 FFmpeg 二进制增加约 3 MiB，不新增 Python 运行依赖。原生 `av1` 包装器依赖硬件解码，浏览器可播放不能作为 FFmpeg 软件解码证据；CPU 解码直接采用维护中的 dav1d，不为当前仅需解码的链路引入 libaom 编码库。发行验收使用合成 AV1 片段确认 CPU 解码与抽帧。构建参数依据 [FFmpeg libdav1d 文档](https://ffmpeg.org/ffmpeg-codecs.html#libdav1d) 和 [VideoLAN 固定发行源码](https://download.videolan.org/pub/videolan/dav1d/1.5.4/)。

macOS CI 的抽帧与 AV1 软件解码回归使用 [Homebrew FFmpeg formula](https://formulae.brew.sh/formula/ffmpeg) 提供的原生工具，随非 core 测试域安装；实际版本和解码能力由安装日志与媒体回归核对。Windows CI 保留固定版本的 setup-ffmpeg，发布包继续使用自构建工具；不新增 Python 依赖，不改生产入口或真实 ledger。

Python 复用 MIT 许可的 [Ruff](https://docs.astral.sh/ruff/) 0.17.0，版本由开发依赖和 `uv.lock` 固定。规则配置在 `pyproject.toml`；正式入口通过 `tests/test_python_lint.py` 扫描全仓自有 Python 文件，检查 Pyflakes、Ruff 支持的 Pylint 错误规则与四项常见行为隐患。Ruff 的 Pylint 规则覆盖是部分覆盖；需要跨模块推断的问题仍由对应行为回归验证。

复用现有 Oxlint 1.85.0 的 correctness、`eqeqeq` 与 `oxc/no-accumulating-spread`，不新增依赖。源码和测试共用基础规则；shadcn 设计规则仅作用于自有 React 源码。

评估过 MIT 许可的 [anti-slop](https://github.com/dmmulroy/anti-slop/tree/c44ef22ca116d0ba62a3ff663a0bd13a3f3fa40b)。固定提交的试跑中，8,243 条诊断里 7,274 条要求空行；其禁止 `unknown`、运行时 `typeof` 和模块 mock 的规则不适合本项目的输入校验与测试替身，因此不引入整套插件。类型安全由 TypeScript、基础 lint 与对应行为回归共同检查。

## 已删除旧实现与当前继任者

找不到某个旧脚本时先查这张表：它的能力通常已经并进右边的实现，不要照旧名重写一份。

| 已删除/旧名称 | 当前实现 | 规则 |
|---|---|---|
| `rm-web.py` / `rm-web.html` | `src/peach/api.py`、`src/peach/web_contract.py`、`web/index.html` | 不得恢复旧 HTTP server |
| `rm-javlookup.py` | `scripts/scrape_codes.py` | 扩展来源适配器，不分叉刮削器 |
| `rm-probe.py` | `src/peach/media_probe.py`（入库时探本机与 115）、`scripts/probe.py`（历史、重探与计流量来源） | 探测与失败记 -1 只有 `media_probe` 一份，保留续跑语义 |
| `rm-sheets.py` | `scripts/sheets.py` | 共用 FFmpeg/任务原语，不新建抽帧管线 |
| `rm-ledger.py`、`scripts/ledger.py` | `peach init`／`peach scan`（`src/peach/cli.py`、`src/peach/scan.py`）+ repository/migrations | 摄取与建库只有 `peach` 一个入口，不放回旧 CLI；Stash 回灌随 ADR-0021 退役 |
| `rm-status.py`、`scripts/status.py` | `peach status`（`src/peach/cli.py`） | 状态命令只读，并且只有一个入口：打包入口转发全部子命令，不单独发脚本 |
| `rm-suggest.py`、`scripts/suggest.py` + `moods.json` | `scripts/taste_history.py` + 馆藏页筛选 | 排序与心情筛选留在应用端口，不放回旧 CLI |
| 各写库脚本私有的 `--database`／`--backup-dir`、自写 backup 与只读连接 | `src/peach/scripting.py`（`open_readonly`、`add_ledger_write_args`、`open_for_write`、`counts_of`、`verify_after_write`、`USER_AGENT`、`RateLimiter`） | 真实写入的参数只有 `--db`／`--apply`／`--backup` 一套；`--apply` 必须同时给 `--backup`，备份走 `peach.migrations.sqlite_backup`，脚本不各写一份 |
| `rm-trafficwatch.py` | `scripts/traffic_watch.py` | 只停止任务拥有的进程树 |
| `rm-sha1.py` | `scripts/sync_sha1_115.py` | 复用 Provider 哈希，不盲目重算网盘媒体 |
| `import_performer_portraits.py`（原 `agent/claude/performer-portraits`） | `scripts/audit_performer_portraits.py` + `scripts/localize_performer_names.py` | 一次性导入已执行完；后继只产 CSV，不写头像文件 |
| `normalize_code_suffix.py`（原 `agent/claude/code-suffix`） | `catalog_rules.jav_display_metadata` + `scripts/audit_jav_display.py` + `scripts/audit_code_creators.py` | 紧凑番号只随发行证据恢复；版本后缀投影为徽章，原始文件身份不丢失；全库审计只读 |
| `dedupe_performer_creator.py`（原 `agent/claude/dedupe-identity`） | `scripts/merge_duplicate_identities.py` | 后继的判据已扩到跨 kind、同 kind 与真子集三轮，旧脚本判据更窄 |
| RSS 适配层（feedparser） | `src/peach/feeds.py`（JavDB 演员页当伪 Feed，ADR-0042、ADR-0047、ADR-0083） | feedparser 不在依赖里；新增发现源先看番号样本，按 `peach.feeds` 的契约接入 |

详情滚动复用 `attachOverlayScrollbar`：尺寸与内容盒子由 ResizeObserver 跟踪，滚动位置按帧更新；子树中的播放器时间文字不触发几何重算。详情开关复用 `data-detail-open` 暂停与恢复底层玻璃背景动画。播放器时间与悬停预览使用 Peach 控件，Video.js 的隐藏时间组件通过组件选项关闭；上游接口见 [PlayProgressBar](https://docs.videojs.com/control-bar_progress-control_play-progress-bar.js.html)。

详情打开时，共用 `wireAutoScroll` 暂停底层横排，关闭后由属性观察器唤醒；播放器氛围色在任一 RGB 通道累计相差至少 6 时刷新继承色。共享 Tooltip 复用浏览器 `popover="manual"` 顶层，挂在触发器所属的模态 Dialog 内；操作键条允许按钮收缩并保持单行。无新增依赖。原生浮层契约见 [Popover API](https://developer.mozilla.org/en-US/docs/Web/API/Popover_API/Using)。

规范页面地址复用同一份 React Router 历史、`resolve_entity_id`、`resolve_entity`、实体资料查询和 BoardUI 设置面。`history/canonical-path.ts` 处理产品地址，队列属于资产查询参数；`settings-route.ts` 统一设置分区与背景历史。实体 ID 解析取得规范名后复用资料、作品和照片查询。身份缺失、种类校验、实体合并与来源名称的展示属于 Peach 的领域规则；不增加路由或 slug 依赖。页面契约见 [ROUTES.md](ROUTES.md)。

## 当前替换队列

下面是还开着的替换项，以及每一项已经定下的做法。

1. 详情播放由 Video.js 承担；`MediaEngine.stream_plan` 在显式开启时给 115/PikPak 原生 MP4 生成 HLS 临时短片段，默认仍走标准 Range（ADR-0016）。待补自适应码率、多路清单和生产验收。CloudDrive 的虚拟盘固定块预取仍属于来源层成本。
2. `sync_sha1_115.py` 还没有备份闸门（`tests/test_script_policy.py` 的例外表已记账）；其余旧脚本的继任见上表。

## 厂牌名称身份

实体资料页的旧名与大小写变体按账本规范名统一地址：服务端复用 `RedirectResponse` 与 `resolve_entity`，站内复用实体控制器的 replace 导航；查询与片段保留，歧义或未知身份不重定向。规范名沿用账本，不新增独立 slug。

作品列表、分页与筛选统计复用 `entities.resolve_entity` 将名称解析为实体 ID，再用 `web_entity.scope_predicate` 获取关系范围；五类资料实体共用此入口，厂牌覆盖旗下各级 label，事务所覆盖成员作品。未取得唯一身份时返回空集合。

`entities.studio_name_key` 与 `studio_identity_ids` 供入库、元数据落地、名称解析和 `merge_studio_name_variants.py` 共用：NFKC、符号写法及唯一登记别名映射到规范身份。官方标题确认的 [ATTACKERS](https://attackers.net/)、[PREMIUM](https://premium-beauty.com/) 日英并写名和 [kawaii*](https://kawaiikawaii.jp/) 星号写法纳入精确映射。名称冲突保留来源原文；括号两侧须各自唯一指向同一实体；`AVOP` 跨厂牌共用，不作为合并证据。复用项目实体合并、字段归属、SQLite 备份和复核 CSV，无新增依赖。

## 口味来源与偏好

书签复用 `beautifulsoup4==4.15.0`（MIT，现有依赖）解析 Netscape HTML，JSON 和时间戳使用 Python 3.14 标准库；Firefox 复用现有 SQLite 一致快照，私有来源、停止读取、上传及删除入口沿用历史模块。浏览器格式依据 Chromium `BookmarkCodec` 的 `roots/children/type/url/date_added` 与 Mozilla Places 的 `moz_bookmarks/moz_places`，不复制外部实现代码。隔离样本覆盖 HTML 实体、日期缺失、重复 URL、非 HTTP(S) 过滤、Chromium 书签独立资料、Firefox 快照及失败保留；接口与偏好测试使用临时数据库，无新增依赖或 ledger 迁移。

标题题材复用 `genre_taxonomy` 的中英日内容词表、`pyahocorasick==2.3.1` 的最长匹配和现有规范身份别名索引；Peach 保留标题省写过滤、英文词边界、成人上下文及 URL 与标题的同义去重。隔离样本覆盖仅 ID 的详情 URL、标题名称及别名、Rule34 标签交叉证据、全半角、负向条件与普通页面误匹配；不请求原页面，不新增分词或模型依赖。

候选复核（2026-10-17）：[browser-history](https://github.com/browser-history/browser-history) 为 Apache-2.0、纯 Python、跨平台且无依赖，但书签能力明确标为实验性且未受测试覆盖，通用读取先复制文件，无法直接取代 Peach 的一致快照和失败保留策略；[bookmarks-converter](https://github.com/radam9/bookmarks-converter) 为 MIT，声明 Windows/macOS/Linux 与 Python 3.14 支持，HTML 依赖 BeautifulSoup，转换模型还引入 SQLAlchemy。Peach 仅需平面 URL、标题和可缺失日期，采用现有 BeautifulSoup 与标准库承接通用解析，保留最小格式适配和私有证据策略。原始输入只在本机读取，无采集网络流量。

历史快照与导入复用 SQLite backup API 和 `browserexport==0.4.4` 的 Chromium、Firefox、Safari 适配器。`taste_browsers` 管理本机资料目录及启停，配置在私有历史库内，停用保留已导入数据；一份资料未取得不阻塞其他资料。来源规则与账号关联见[口味来源](TASTE_SOURCES.md)，仅复用既有实体链接和启用的关注，不自行确认新身份。

多维偏好复用行为计权、genre 归一和 `related.rank_related` 的 MMR；`taste_preferences` 保存规范身份、证据种类及候选强度。持久缓存复用 `fsutil.atomic_write_text`，按账本、历史库及规则源码版本失效，只读历史连接忽略没有数据帧的 WAL。名称扫描采用 [pyahocorasick 2.3.1](https://github.com/WojciechMula/pyahocorasick)（BSD-3-Clause，Unicode，Python 3.12–3.14，Windows/macOS/Linux）；已读固定版 C 实现，Windows CPython 3.14 wheel 实装通过，新增仅此直接依赖。长标题、多别名、重叠名字及 ASCII 词边界由 Peach 约束。真实只读汇总覆盖七类维度；偏好候选和私有缓存不改写 ledger。

## 卡片与来源头像

回收站复用 `MediaCard`、`/api/batch` 与共享操作回执，视频和资源共用还原按钮及原位失败提示。`asset.disposal_reason` 保存手动、垃圾、重复和文件缺失；还原清空原因，撤销按原原因归组写回，未知原因保持为空。无新增依赖。

卡片 label 复用既有 `javTitleHtml`、`followTitleMarks` 与 `data-media-tags`，分段筛选复用 `SegmentedRadioGroup`。来源头像共用 `PublicAPI` 的已验证图片主机、三个下载槽位、Pillow 解码、单请求合并与六小时缓存；只持久保存没有查询参数的公开静态头像地址。人物使用策略在投影和口味汇总处过滤明确登记的男优，原始来源记录仍完整保留，无新增依赖或 ledger 迁移。

公开作品资料与评论复用 `jav_cache.PublicSnapshots`、既有 `filelock` 和原子写入；`cache_fresh` 与前端 `publicationFresh` 统一日历月边界、每天补齐及满月后的固定快照。`public_cache_scope` 将快照隔离到调用者数据目录，记录实际来源时间；测试显式传临时目录，默认 `PublicAPI()` 只用内存。媒体字节复用媒体缓存，来源 JSON 不承载本地标签决定、想要状态或观看历史。

公开预览复用 HTTPX 0.28.1 的流式响应与已安装的 `curl_cffi` 0.16.3（MIT）浏览器传输。`preview_transport` 只适配响应生命周期与 TLS 连接失败，逐跳主机判据仍复用 `open_upstream`；HTTP 拒绝状态不切换传输。已读取固定版本 SDK 的 `Session.request`、`Response.iter_content` 和 `close`，实际公开 MP4 范围请求取得 206，无新增依赖。HLS 根与嵌套清单使用来源地址作为代际，5 分钟后按需刷新，浏览器核对本机清单；分片、密钥与初始化段继续复用持久缓存。MIKR-122 的来源新清单包含可用的当前签名；作品资料及评论的发行日期缓存独立保存。缓存与协议证据沿用 [媒体缓存](MEDIA_CACHE.md) 中现有 `m3u8` 和 Hishel 评估。

## 原位改字

`scripts/dev/copy-editor.js` 复用官网本地预览的 `edit-client.js` 交互：文字范围提示、原位编辑、按钮文字就近输入、回车和失焦保存、Esc 取消。
改字浮条使用多选操作条的 `data-selection-dock`、`data-glass-pane` 与 `data-batch-scope` 样式，几何、玻璃、字体和按钮共享；窄屏状态提示独占一行。
应用的 React 与动态页面使用现有静态文案定位、摘要校验和备份入口；演示站使用浏览器草稿。两者只在 `?edit` 启用，无新增依赖。
馆藏上传的动态说明与字段复用同一入口，Python 源文件仅纳入 `src/peach/library_sharing.py`，其他 Python 文件不属于改字范围。

## 馆藏处理控制与耗时预估

长时任务续跑复用 `job_recovery` 的本机原子记录、`BackgroundJob.checkpoint` 与 `TaskRunHandle.checkpoint`。主任务保留原 `job_id`、`run_id`、开始时间、处理范围和领域检查点；后继重排保留原进度。扫描复用现有 `os.scandir`、SQLite 批次提交、`filelock` 和 `fsutil.atomic_write_text`，保存遍历栈、文件名游标及字幕配对资料。本地资料按影片保存小份证据边车，候选成功落盘后才提交元数据完成集合；整理逐项原子保存同一批次的日志。`scrape_codes.py` 复用流式 CSV 与原始来源快照，续跑只保留已确认完成的番号，未完成番号可以重放。

当前树、扫描与采集的 Git 历史，以及现有任务队列均已核对；不引入新的调度或工作流依赖。项目已固定的 APScheduler 3.11.3（MIT）负责派发，其持久 job store 记录调度任务，不能保存函数执行到哪一条影片或来源页，见[上游调度说明](https://apscheduler.readthedocs.io/en/3.x/userguide.html#adding-jobs)。结果替换沿用 Python 3.14 标准库 [`os.replace`](https://docs.python.org/3.14/library/os.html#os.replace)（PSF），锁沿用项目固定的 `filelock`，没有新增运行时体积或平台要求。隔离实证覆盖目录与目录内批次中断、关注回抓游标、固定来源集合、原任务标识与计数、候选 CSV 保留及同批整理日志；不读取真实媒体、不访问外部采集来源，也不写真实 ledger。

扫描、探测、资料请求和九宫格复用 `process_library`、`scan_location`、`SourceTransport`、`frame_capture` 与已有影片检查点；任务恢复沿用 `BackgroundJob`、`job_recovery` 和 `task_run`，用户暂停意图单独持久保存。当前树及 `library_processing.py` 的 Git 历史已核对，安装暂停只用于安装事务，不能直接代表用户暂停。Python 标准库 `threading.Event` 与 `contextvars.ContextVar` 提供等待唤醒和当前任务隔离，沿用项目 Python 版本，跨 Windows 与 macOS，无额外依赖体积；许可证为 PSF，官方文档与实现见 [threading](https://docs.python.org/3/library/threading.html) 和 [contextvars](https://docs.python.org/3/library/contextvars.html)。现有 APScheduler 3.11.3 用于定时派发，不能暂停已进入媒体读取的函数，因此不承担安全停点。隔离测试使用临时账本、暂停线程与模拟时钟验证安全停点、跨重启意图、低占用间隔和暂停预算补偿，不读取真实媒体、不发外部请求。

`processing_control` 保留 Peach 的阶段、模式与估时策略：启动仅汇总账本文件数和大小，优先使用本机同类完成耗时；未知新增明确不在初始范围内。整趟剩余时间合计当前阶段和待执行阶段；当前阶段采用最近 20 次完成增量，后续阶段使用保守粗估。无番号、无 NFO 的文件完成同目录检查后不计入外部采集数量；任务停滞时读接口撤下旧估时。处理强度为低占用、标准和高强度：低占用采用单线程 FFmpeg 与较大单项间隔，标准采用两个解码、滤镜和编码线程与短间隔，高强度采用工具自动线程且没有额外间隔。amane 低占用逐站查询，其余沿用现有调度；各档均遵守来源限流，单资产流水线与暂停检查点共用。强度只作用于当前任务，历史耗时按档位系数归一，初始与切换时的范围属于粗估，实际样本继续校准。持久状态不修改 ledger schema，不改变生产入口。

配套图片沿用 Pillow 12.3.0（MIT-CMU）解码与 `install_cover` 登记。网盘及未知来源的文件读取和 JPEG 转换放在独立子进程，20 秒超时后终止并等待退出，再登记可重试问题；声明为本地磁盘的来源直接在线程内解码，不逐张启动进程。隔离测试使用 Unicode 路径图片、无效图片与真实超时子进程验证。进程超时复用项目使用的 Python 3.14.7 标准库 [`subprocess.run`](https://docs.python.org/3.14/library/subprocess.html#subprocess.run)（PSF 许可，无新增安装体积），Windows 隐藏窗口，源码模块和独立包 CLI 共用转换函数。线程等待超时不能终止同步文件读取，因此网盘不采用单纯线程包装；FFmpeg 已用于媒体任务，但单张图片继续复用 Pillow，不增加解码依赖。

目录列表与 `SidecarIndex` 在本趟任务内复用，每个目录建一次文件名索引；同名、共享 NFO 与编号图片的归属沿用 `library_nfo`。500 个视频、1,000 个目录条目的隔离夹具中，逐项匹配耗时约 1.01 秒，复用索引约 0.007 秒，输出一致；该数字仅反映文件名匹配，不代表网盘读取或整趟耗时。配套图片与免费来源的媒体属性探测复用标准库 `ThreadPoolExecutor`，三档各领 1、2、4 项，空闲后补位，主线程保存结果并经过暂停停点；计费来源的媒体读取限制与各资料站限流保持原策略。不同阶段仍按实际依赖推进，界面步骤仅展示这些边界。

`_RemoteSession` 按来源与番号复用本趟新取得的原始证据及明确未收录答复；重试首个同番号资产取得新答复，其余资产共用。来源错误和超时仍可重试，不记为未收录。复用结果逐资产生成候选，字段仍经过既有来源和冲突判据，不直接复制其他资产的真相字段。自定义来源配置实时读取，停用和字段映射变化立即生效。

封面复用 `prestige_group_images`、`best_cover`、尺寸探测与下载候选链。Prestige 与 MGStage 的冷却各自处理，可用目录或已知图片继续参与；单张候选的主机冷却时领取下一张，全部候选冷却仍保留可重试状态。Prestige 系番号的默认资料链通过 `route_for_code` 加入 MGStage 备用，官方档按现有冷却记录筛选可请求站点，字段继续按片商优先级结算；显式来源链覆盖不自动追加。其余类型沿用原来源链，全部流程保留来源间隔、共享动作截止时间、作品身份与封面互证，不新增依赖。

卡片与任务浮层的等待时长以服务端快照为起点，用浏览器单调时钟每秒推进当前文字。新快照重设起点，暂停、结束和卸载清理定时器；每秒重渲染限定在进度子树，不改变两秒一次的状态请求。组件测试核对连续秒数、请求数、换文件复位和卸载清理，桌面与手机浏览器验证实际跳秒。

任务卡复用 Sonner 2.0.8（MIT）的 `toast.custom`、稳定任务 id 与无限停留时间；`processing` 和 `receipts` 两个栈按 `toasterId` 分流，普通回执按任务面板实测高度避让。任务栈启用 `expand` 保持自动高度，详情开合由卡片管理；收起只隐藏正文，暂停、速度与估时仍可见。服务端 `steps` 在实际处理边界更新，完成步骤以勾选与删除线显示。失败浮层只自动展示本次观察到的结束状态或两分钟内的结果，旧失败快照不自动弹出；用户关闭后保持关闭。React Aria 1.22.1 的 Slider、Popover 与现有 BoardUI Button 承担键盘、焦点与浮层行为，三档对应 `low/standard/high`；最高档流光使用 SVG 与 CSS，浮层关闭后卸载，暂停与后台页面停止运动。界面重建依据与差异见 [控件取证](reference-snapshots/processing-task-controls.md)。无新增依赖。问题明细沿用 `state/library-processing-<job_id>.issues.jsonl`，新任务保留其他任务的明细。

站点强调色检查覆盖进度、焦点、选中态、操作按钮与流光：旧 `--feedback-success` 引用既有强调色色阶；RSS 订阅保留橙色，信息、成功、警告、错误、品牌与图表分类保留语义色。

普通 Toast 与扫描任务卡共用 Agent Progress 外框，16px 半径由通知栈内的既有 token 解析，细边框与接触阴影分别复用 `border-button-default`、`elevation-xs`，不改变内容排版。浏览器回归核对桌面、手机和明暗主题下的外框一致性，并以同一任务、同一内容回放外框基线生成对比图。
