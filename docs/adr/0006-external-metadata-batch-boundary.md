# ADR-0006：外部元数据批处理边界

- 状态：Accepted
- 日期：2026-08-14

## 背景

Peach 需要在线补充番号的 performer、studio、series 和内容分类，但外部站点协议、限流和可用性不稳定，返回结果也不能直接成为 ledger 真相。

## 决策

- 外部元数据作为显式、可恢复的批处理能力，不进入 Web 请求热路径或应用启动流程。
- 只向已声明来源发送标准化番号；不发送本地文件路径、文件名、媒体内容、播放行为或凭据。
- 抓取先写逐来源原始 JSON 和可续跑的字段候选 CSV；只有 `/review` 对具体来源值的显式批准才写 ledger，不保留整批 `--apply` 后门。
- 写入同时包含 source/confidence/provenance，并双写规范 `entity`/`asset_entity` 与当前 UI 兼容投影。
- 默认查询适配器已从「Javinizer-Go v1.5.1 单来源 JSON CLI」改为多来源白名单（2026-08-29
  补记）：来源必须显式登记才能进入抓取计划，登记位在 `src/peach/metadata_policy.py` 的
  `REGISTERED_SOURCES`：r18dev、libredmm、dmm、javlibrary、javdb、javbus、jav321、mgstage、
  tokyohot、aventertainment、caribbeancom、dlgetchu、fc2、javstash，并按 official／
  official_mirror／community 分级；新增来源走同一位点的显式登记。Peach 自己聚合来源、
  规范化字段、保存证据和执行批准，绝不调用其 organizer 或让来源工具持有 Peach 数据库。
- P0 字段覆盖演员、厂牌、系列、发行日期和内容标签；发行时间只落 `YYYY-MM-DD`，官方/官方镜像 tag 来源排在社区来源前，仍须逐项批准。
- 对 403/429/503 做来源级冷却；HTML 来源视为易变 adapter，失败不得阻塞 Peach。
- 导入脚本本身无副作用，网络和真实数据库操作只发生在 `main()` 的显式命令路径。

## 被否决的方案

- 页面打开时同步刮削。
- 未复核即覆盖人工字段。
- 把文件路径、完整文件名或媒体上传给第三方做匹配。
- 把易变 HTML 结构当作核心领域协议。

## 影响

元数据到达存在延迟，并需维护来源解析器；换来的是可追溯、可重跑、可限速和可逐源替换，外部故障不会影响本地播放。

「默认查询适配器」与 `REGISTERED_SOURCES` 登记位那一条被 ADR-0044 取代：来源级别登记在 `metadata_policy.SOURCE_SPECS`，链的成员由 `metadata_routes.ROUTES` 决定，Javinizer-Go 已退场。 <!-- copy-lint-disable-line -->
