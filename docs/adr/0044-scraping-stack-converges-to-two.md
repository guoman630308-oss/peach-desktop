# ADR-0044：刮削栈收敛：官方站自写、社区站 amane、Javinizer-Go 退场

- 状态：Accepted
- 日期：2026-09-23

## 背景

到 2026-09-22 为止，番号资料一共有三套取法并存：Peach 自写的解析器（r18.dev、一本道、FC2 与
两处存档站、AVBase、JavBus、javdb、Seesaa）；amane 桥子进程（fc2club、freejavbt、airav、avsox，
ADR-0043）；Javinizer-Go v1.5.x 单来源 JSON CLI（`scripts/scrape_codes.py` 的 `--profile` 组合，
问 dmm、libredmm、mgstage、javlibrary、jav321、tokyohot、aventertainment、caribbeancom、dlgetchu、
javstash）。三套各有自己的凭据、限流、冷却与错误分档，同一个番号在采集任务和批量脚本里走的不是
同一条链：采集任务按 `metadata_routes` 分档短路，批量脚本按 Javinizer 的 scraper 组合逐家问到底。
Javinizer-Go 那一路还要求本机装一份特定大版本的二进制并单独维护 YAML 配置，账本里最后一批经它
写入的 provenance 停在 2026-09 上旬。

用户 2026-09-23 决定收敛到两套：官方与半官方站由 Peach 自己解析；社区站经 amane 桥；Javinizer-Go 退场。

## 决策

- **归属判据**：来源是发行方或发行方目录的镜像（r18.dev、一本道、FC2 商品页、fc2cmadb、JavArchive
  这类只对一个发行面负责的站）由 Peach 自写解析器持有，因为它们的字段就是账本真相字段的直接证据，
  形状变了要当天知道。转载、索引与聚合站（fc2club、freejavbt、airav、avsox 及其后继）经 amane 桥，
  Peach 只持有站名到 `SOURCE_SPECS` 的映射与字段翻译，不再为社区站写第二份 HTML 解析器。
- **一条链**：一个番号问谁、什么顺序、何时停只由 `metadata_routes` 决定；采集任务与
  `scripts/scrape_codes.py` 用同一份 `LibraryMetadataProvider`、同一套凭据、限流与冷却，
  `scrape_codes` 只保留批量、快照落盘、健康统计与字段候选排序。`--sources` 点名的来源限于链上能问的
  成员，点名即每家都问、不短路。
- **Javinizer-Go 退场**：删除子进程适配器、版本判定、二进制查找、`--binary`／`--config` 与所有以其
  scraper 组合命名的 profile。它曾问过的十个来源名留在 `metadata_policy.HISTORICAL_SOURCES` 与
  `SOURCE_SPECS`，只为结算（`chain_rank`）与复核页仍认得账本里 `javinizer:<站>:<字段>` 那一万多行
  provenance 的级别；当前链不向它们发请求。`javinizer:` 前缀是整条刮削链的 provenance 写法，不改写。
- **自写社区解析器逐站退场的验收条件**：AVBase、JavBus、javdb 三家的自写解析器暂留，任何一家改经
  amane 桥前必须在同一批番号上取到三条证据：桥给出的字段集合覆盖自写解析器当前给出的字段；命中率
  不低于自写（按 `metadata-source-health-*.csv` 的 `succeeded`／`attempted`）；被限流或封禁时的
  表现不差于自写（冷却写回同一份 `scraping_access` 记录、鉴权墙仍分到 `auth` 一档）。三条齐了写进
  [docs/SOURCING.md](../SOURCING.md) 再删代码；缺一条写「未取得」，解析器留着。
- **封面层独立于资料来源**：`jav_cover_fetch`、`best_cover`、`verified_cover` 与它们的图源汇总不随
  资料链变化；`sources/metadata/javinizer-go/**` 下的旧快照只作离线证据继续被读取（`cover_url`、
  `content_id`、厂牌），不再有任何一条路往里写 Javinizer 形状的新快照——`scrape_codes` 写进同一目录
  的新快照 `provider` 记解析器名、`provider_version` 记 Peach 版本，`result` 形状不变。
- **不删磁盘**：旧快照与 `peach-data/tools/javinizer/**` 下的二进制不删，历史由 Git 与磁盘各自保存。

## 被否决的方案

- 保留 Javinizer-Go 作「官方站的第三份兜底」：它问的十个来源里，dmm 与 libredmm 已由 r18.dev 覆盖，
  mgstage 与 caribbeancom 尚无 Peach 解析器——但「尚无」的代价是再维护一份二进制、一份 YAML 与一套
  错误分档，而它最近一年在这些站上的命中都是靠登录态；需要时按本 ADR 的归属判据自写，不靠它续命。
- 把自写的 AVBase、JavBus、javdb 解析器与 Javinizer-Go 一起删掉：三家现在是有码与素人链的社区那一档，
  amane 桥对这三站的字段与命中率尚未按上面的验收条件量过，删了就是拿猜测顶替证据。
- 让 `scrape_codes` 保留自己的一套来源组合（profile）与采集任务并行：同一个番号两条链两种答案，
  复核页上无法解释「为什么批量给的和任务给的不同」。

## 后果

- 影响面：`src/peach/metadata.py`、`metadata_policy.py`、`metadata_routes.py`、`metadata_seesaa.py`、
  `scripts/scrape_codes.py`、对应测试与 [docs/REUSE.md](../REUSE.md)、[docs/SOURCING.md](../SOURCING.md)。设置页没有 Javinizer-Go
  的卡片或 API，前端无改动。
- ADR-0006 把 Javinizer-Go 定为默认查询适配器、把 `REGISTERED_SOURCES` 定为登记位，ADR-0024 的复用表
  把它列为番号元数据的正式基础；两处由本 ADR 取代，登记位改为 `SOURCE_SPECS`，链的成员由
  `metadata_routes.ROUTES` 决定。
- `scrape_codes` 默认按番号内容类型走链并逐档短路，与采集任务同一判据；官方一档取齐必填标量后
  不再向社区站发请求，批量的社区站请求数因此下降，代价是官方答全的番号不再有社区候选并排比对——
  需要比对时用 `--sources` 点名。

## 实施：解析器契约

用户 2026-09-23 决定自写解析器改用统一形状，amane 桥的站套同一形状：一个站一个类，取页与解析分开，
按站配置进数据，一种返回模型，一张失败原因表。契约在 `src/peach/sources/base.py`：

- `SiteSource`：`fetch(code, *, session) -> Page` 与 `parse(page, code) -> SiteRecord` 分开，`query()` 串起来并把
  `_fetch` 的 HTTP 分档翻成 `SourceFailure`；配置经构造函数注入，测试只喂 `parse` 一张页面就能覆盖解析。
- `SiteConfig`：站名、界面名、解析器名（provenance）、主域与图床、请求间隔、是否带 Cookie、页面上限、档位。
  它与 `SOURCE_SPECS`、`SOURCE_LABELS`、`PROVIDER_NAMES`、`scraping_access.SOURCES`、`SOURCE_INTERVALS` 里同一站
  的那几行逐项一致，由测试守住；这几张表仍是各自消费者的真相，契约不替代它们。
- `SiteRecord`：`source`、`provenance`、`code`、`source_url`、`title`、`performers`、`studio`、`label`、`series`、
  `director`、`release_date`、`runtime`、`tags`、`cover_urls`、`confidence`、`extra`；`payload()` 投影成来源快照那份
  dict（`id`、`maker`、`actresses[].japanese_name`、`genres`……），候选、来源链结算、封面层与账本读到的东西不变。
- `FailureReason`：从 amane 的十六档里取 Peach 用得上的十二档（`cloudflare_challenge`、`ip_banned`、`geo_restricted`、
  `auth_required`、`not_found`、`gone`、`parse_error`、`no_usable_metadata`、`rate_limited`、`timeout`、`server_error`、
  `network`）。`REASON_KINDS` 映到 `MetadataProviderError` 现有的三档，`COOLDOWN_ACTIONS` 定哪几档把整站写进
  `scraping_access` 的冷却记录，`PERMANENT_REASONS` 定哪几档不重试。传输层自己的信号（`SourcePaused`、
  `DeadlineExceeded`、`httpx.TransportError`）不经这张表，`query()` 原样放过，冷却行为由传输层一处决定。

第一步迁了 JavBus 与 javdb（`sources/javbus.py`、`sources/javdb.py`），`community_catalog.javbus_work` /
`javdb_work` 只剩投影与异常翻译，`COMMUNITY_SOURCES` 的形状不变，AVBase 暂留原处与之共存；amane 桥的站
经 `metadata_amane.to_record` 套进 `SiteRecord`，十六档 reason 经 `AMANE_REASONS` 一对一翻成契约细档，
上游原样的 reason 仍留在 `detail` 里。对外零变化：provenance 串、候选形状、来源链与结算、封面层、账本一律不动；
唯一多出的键是桥 payload 的 `cover_urls`（amane 的整列 `thumb_urls`），`cover_url` 仍是第一张。
第二步迁了 AVBase 与 r18.dev（`sources/avbase.py`、`sources/r18dev.py`）：`SITE_SOURCES` 成为四站唯一的取站入口，
`LibraryMetadataProvider.site` 与 `scrape_codes` 都经它问站，`COMMUNITY_SOURCES` 只剩来源名的顺序，
`community_catalog` 里的取数函数与 `SourceFailure.legacy()` 这层翻译随之删除；`not_found` 一档由
`library_processing.is_missing` 与传输层的 `NotFound` 一并认，别档措辞经 `describe_failure` 原样交出。
payload 的差别只有契约统一带的键：AVBase 多一个留空的 `runtime`，r18.dev 多一列 `cover_urls`，站上没给的
标量留空串而不是 `None`——候选那一路对两种写法同样处理。
第三步迁了一本道与 FC2 三站（`sources/onepondo.py`、`sources/fc2.py`、`sources/fc2cmadb.py`、`sources/javarchive.py`），
`metadata_1pondo` 与 `metadata_fc2` 两个模块删除，`ONE_PONDO_LIMIT`、`FC2_PAGE_LIMIT` 进了各站的 `SiteConfig.page_limit`。
多站接力那一档要的东西补进契约：`SiteSource.records()` 交出一站对一个番号的全部记录，默认就是 `query()` 那一条，
JavArchive 把搜索命中的每一条转存各交一份；`Session.get` 可以为一次请求指定 `referer` 与额外请求头，fc2cmadb 点名
`actresses` 的那一跳靠它；`SiteRecord.runtime` 按分钟记、允许两位小数。先后问、资料取齐即停、封面问到底的流程仍在
`LibraryMetadataProvider.fc2`，按 `metadata_routes.FC2_STAGE` 逐站调 `records()`。评论区的演员、等价与合集解析在
`sources/fc2cmadb.py`，`scripts/fetch_fc2_metadata.py` 从那里取。payload 的差别：FC2 三站多出契约统一带的
`series`、`director` 空串键，一本道多出 `label`、`director`；fc2cmadb 的女优写成 `{japanese_name}`，与别站同形，候选那一路
据此读出演员；一本道回的不是 JSON 归 `parse_error`。
第四步迁了 Seesaa 作品表（`sources/seesaa.py`），`metadata_seesaa` 模块删除，没有调用方的 `RoutedMetadataProvider` 随之删除，
契约迁移至此完成：自写的九站全部登记在 `SITE_SOURCES`，经桥的四站在 `metadata_amane`。这一站的形状与别站不同：一页作品表
几十上百行，`rows()` 把每一行各读成一份记录，实例记着读过的表，后面的番号先在那里找，找不到才走站内搜索、人物页与厂牌页；
`fetch` 交出载有这一行的那一页，`parse` 从页上取这一行，几张表对同一个番号说法不一报 `ambiguous`。取页层 `WikiPages` 是它的
`Session.transport`：页缓存键、本批请求限额与撞墙停网都在这一层，不经 `SourceTransport`，所以它不在 `scraping_access.SOURCES`，
采集设置页也没有它的卡片。失败沿用原有的 `budget`、`blocked`、`size_limit`、`redirect`、`ambiguous`、`incomplete_search` 等分档，
不折成 `FailureReason` 的三档：这些分档落进 `scrape_codes` 的错误表与健康表，`budget` 与 403/429 还决定本批停网；搜索范围内
没找到是可重试的 `incomplete_search`，按 `not_found` 冻成定论就违背「未取得不冻结」。provenance 仍是 `sougouwiki`，
`SOURCE_LABELS`、`PROVIDER_NAMES` 补上这一行。payload 的差别只有契约统一带的 `maker`、`label`、`series`、`director` 空串键、
`runtime` 空值与 `cover_urls`，没给标题或日期列的表按空串交出；冲突判定比较标题、出演与日期时，表上没有那一列与那一格为空同样按空串比。
迁移状态表在 [docs/SOURCING.md](../SOURCING.md)「站点解析器契约」。

归属判据由 ADR-0048 修订。 <!-- copy-lint-disable-line -->
