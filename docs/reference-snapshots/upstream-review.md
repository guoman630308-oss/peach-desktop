# 上游依赖与参考来源复核

复核日期：2026-10-13（Asia/Taipei）。本文件是人工复核笔记，不登记为可覆盖的上游快照；原文字节、哈希与 revision 见 [来源登记](../reference-sources.json)。

## amane 桥

采用 [amane 0.20.0](https://github.com/sqzw-x/amane/releases/tag/v0.20.0)，固定 revision `cc42cee6ecf290cf05af1caa347d2dc2d5aeffcd`，GPL-3.0。Python 下限仍为 3.14，桥以独立 venv 和子进程消费；不引入其数据库、整理、任务中心或界面。

[源码差异](https://github.com/sqzw-x/amane/compare/7a980aff9c503f7a5815864d62a6ef309a27808a...cc42cee6ecf290cf05af1caa347d2dc2d5aeffcd)共 29 个提交。适用于 Peach 的变化是 FreeJavBT、AIRAV 精确检索，AIRAV 详情解析及网络层指纹轮换。演员、kin8、giga、字幕、整理与超分的变化不扩展 Peach 来源链；每站唯一归属、身份核对、候选与结算仍由 Peach 管理。

实测：所有 20 个桥声明的爬虫类可导入，`SearchQuery`、`FetchOptions`、`MediaMetadata` 与 17 档失败原因兼容。真实解析器配合合成页面确认 AIRAV、FreeJavBT 精确命中 `ABW-032`，查 `ABW-03` 时不取相近条目；AIRAV 的标题、演员、厂牌、发行日、标签、剧情与封面可解析，片长为空，不采用固定试看时长。

只读在线查询 `ABW-032`：Prestige 2.48 秒、AIRAV 2.54 秒取得同一番号，FreeJavBT 5.32 秒明确未命中。Prestige 返回片长 190 分钟，AIRAV 不给片长；Prestige 配信日与发行日的分离仍按 Peach 既有规则处理。上游测试数据仓库请求为 HTTP 404，真实 fixture 未取得，合成页面不能作为该站所有作品均可抓取的证据。

`max_retries=1` 限普通失败重试；403/406 另可轮换三次 TLS 指纹，单个地址最多五次请求。Peach 保持整次查询 60 秒子进程总时限，超时终止并报可重试失败，不按尝试次数推导完成时长；桥不启用浏览器或 solver。公开请求的 TLS 行为沿用上游，未用于证明 Peach 生产 HTTPS 验收。

桥锁文件同时覆盖 PR #40、#42 的传递依赖要求：Mako 1.4.3、pydantic-ai-slim / pydantic-graph 2.55.0。没有新增直接依赖；传递更新包括 anthropic、openai、genai-prices，桥不调用它们的模型服务。

## amane 0.21.0 采用范围

2026-10-17 复核稳定版 [v0.21.0](https://github.com/sqzw-x/amane/releases/tag/v0.21.0)，
提交 `a2d45880e83df7047d461452965919692701e8c9`；桥清单仍固定 0.20.0，未执行运行依赖升级。
从固定提交到该版本的源码差异覆盖 DMM 图片路径、CID、数字前缀与 MGStage 搜索；
版本检查与实际接入必须分开判断。

| 能力 | 上游修复与 Peach 消费者 | 判断 |
| --- | --- | --- |
| 高清图片 | amane 生成 legacy 高清候选；Peach `jav_cover_fetch` 同时测 modern、legacy 与原始图的真实像素面积，保留最大官方图 | 保留 Peach 选图策略；上游补充的 mono/movie 分类目录与剧照路径可单独补回归 |
| DMM CID | amane 区分通贩与配信 CID，剥离门店前缀；Peach 正式 DMM 来源独立使用 GraphQL 与 `makerContentId` 核对 | 升级桥不会替换 Peach DMM；不直接套用全局去数字规则 |
| MGStage 搜索 | amane 生成搜索变体，精确命中优先，只有一个候选时才允许单边数字前缀差异；Peach 正式来源通过桥调用 MGStage | 桥升级的有效收益，须在独立升级中验证字段与错误契约 |
| 聚合与产品功能 | Peach 经桥使用 4 个社区站、5 个官方来源入口，不调用 amane 聚合、任务中心或前端 | 沿用固定上游版本加 Peach 领域策略，无需维护完整 fork |

pnpm 管理 amane 的前端依赖，Justfile 编排 uv、前端、测试与打包；它们不是桥运行依赖。
Peach 继续使用现有 npm 清单与项目命令入口。pnpm 的多工作树安装收益需实测，just 暂无独立消费者。

## Dependabot PR

PR #41、#43、#44、#45、#46、#47、#48 在隔离工作树按项目接管入口处理，保持一 PR 一提交。PR #43 的锁冲突保留主线 `pyahocorasick` 并采用 Pydantic 2.14.0，`uv lock --check` 通过。npm vendor、主界面与独立页面包按清单重建。

Motion 14.0.0 的大版本删除内部兼容 API。Peach 只通过 `motion` 公共入口使用，没有直接引用 `framer-motion`、`motion-dom`、`motion-utils` 或被移除的 timeline 内部 API；版本由其内部精确依赖保持一致，类型检查、组件与浏览器回归由项目完整测试入口核验。

轮播箭头几何用例在图片就绪后等待渲染阶段，再读位置；`ResizeObserver` 的异步测量不能由图片 `complete` 代替。位置、尺寸、黑边中心和桌面／手机断言保持完整。

## 参考来源判断

运行项目 `check_reference_updates.py check --diff`，覆盖 weekly、monthly、quarterly；manual 不在本轮范围。47 个来源中 28 个未变化，19 个报告漂移，另有 amane 运行依赖前移。14 份原文按完整 SHA-256 与 Git revision 接受；5 份人工笔记保留。

| 来源 | 实测变化 | Peach 判断 |
| --- | --- | --- |
| Fluid springs、tabs | 名称与 import 从 Framer Motion 改为 Motion；弹簧参数未变 | 接受原文，Peach 已用 `motion` |
| Vercel report-design | 增加 Vercel 官方 Logo 下载说明 | 上游品牌专属，不采用其 Logo |
| Vercel web-interface-guidelines | 增加细指针设备页面 overscroll 建议与混合输入限制 | 保留现有滚动行为，本轮不扩展为界面改动 |
| Bruno README | 增加 Hindi 翻译链接 | 接受原文，无运行影响 |
| amane 路由与 README | kin8 欧美归类、giga 会话与 Referer、欧美日期号年份形态及称呼 | 锁定证据，Peach 仅使用已接入来源 |
| OpenAver README | 女优排行 Top 25 | 原文接受，不改 Peach 排行数量 |
| SakuraMediaBE README | 新增番号解析开源项目致谢 | 仅参考出处，不新增依赖 |
| SakuraMedia README | VR 外部播放与插件市场说明 | 同类产品信息，不导入其播放协议 |
| JavBoss README | v2.1.2 下载与设置路径说明 | 同类产品信息，不改变 Peach 部署 |
| MDCz README | 平台、安装包、便携数据与自动更新说明 | 保留 Peach 既有分发和运行边界 |
| Cuelume README | press/release、unlock、卸载绑定与关闭声音语义 | 候选参考，无直接消费者，不安装 |
| Atlas README | 女优资料来源改用 JavBus，注明 minnano-av 反爬 | Peach 有自己的传输与来源冷却，不以 README 声明推断本机站点故障 |

PixivUtil2 从 `fc0f9adf44e590dde9242b3755b53528f159720a` 前移到 `22b5fdc9fc83a976d8f4f6f6acbb4d2d3d542f21`，三个提交处理 FFmpeg 8+ 动图转换、`x-userid` 登录身份和独立错误日志。`PixivBrowserFactory.py` 只增登录 ID 赋值，Peach 采用的公开 FANBOX 传输边界不变；保留既有取证 revision，不复制登录逻辑。

OpenAver 从 `8cc17e50453d9f69a81f5fee1a072df80f7aab73` 前移到 `502278a3e09c7520838ffb00b2ff080323a4cdcb`，共 318 个提交；相关推荐源码未变化。DMM 采用代理 session、连通探测与失败分类，GraphQL 查询取法不变；Peach 参考声明式来源和公网目标守卫，使用受限 JSON 适配器。桌面路径继续由 `platform` 处理，相关推荐保留固定算法证据。

`fiu758-studio-logo-discovery`、`rule34-follow-tags-and-collections` 的漂移是人工笔记与动态 HTML 的比较；`f95-masked-gofile-media` 返回的脚本哈希与笔记记录相同，协议未变化。这三份和 FANBOX、OpenAver 的人工笔记均不执行 `accept`，避免被网页或脚本字节覆盖。

## 采用的应用行为

| 能力 | Peach 实现与边界 |
| --- | --- |
| 实体收藏与命名筛选 | 资料页、标签页和目录入口；复用 `profile.settings_json`，收藏跟随规范身份与合并事务，筛选可保存、打开和删除 |
| 来源诊断 | 正式 amane 来源逐站选择直连或公共代理；连接结果区分代理、超时、限流、验证页与服务错误，显示耗时和冷却；诊断把连接与解析分开 |
| 比较图按需请求 | 复核卡点击后加载，30 秒超时可重试；换来源重置请求状态 |
| 声明式来源 | [单步 JSON 配置](../CUSTOM_SOURCES.md)，公网地址固定连接、严格 TLS、响应番号核对、低置信度候选；不复制 OpenAver 的 HTML/YAML 规则 |
| 外部播放器与 VR | [限时网络链接与 M3U](../EXTERNAL_PLAYERS.md)，设备可使用 VLC/IINA 协议；实际投影与解码由播放器负责 |
| 分卷与整理 | 复用同一版本分卷连播、模板预览、目标冲突跳过和整批回滚，不引入第二个规划器 |

Python 最低与默认版本为 3.14；CI 保留 Windows/macOS 系统与独立 wheel 消费，前端类型、lint、
组件验证在 `web-bundle` 集中执行，本机正式入口完整验证。amane 仍使用独立锁与子进程，
只消费爬虫和网络层；其任务中心、数据库、整理、超分和 AI 服务没有接入 Peach。

本任务未执行真实数据整理、合并或删除。静态、组件、桌面及手机浏览器和生产运行的验收结果
以项目验证记录及 [运行与配置](../OPERATIONS.md) 为准。
