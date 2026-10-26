# amane 爬虫能否脱离其服务单独引用（POC）

- 取证日期：2026-09-22
- 上游：<https://github.com/sqzw-x/amane>，GPL-3.0，`requires-python >=3.14`，v0.16.1
- 固定 revision：`79ecfa763cc786318e1964a3d7f4e244a7d5c96d`（2026-09-22 08:34 +0800，`fix(scrape): 长文本存纯文本并移除简介 CDATA (#229)`）
- 取证方式：仓库外独立克隆与独立 venv，手工构造对象跑真实番号；不改 Peach 生产代码，不写 ledger
- 环境与脚本保存在仓库外的私有证据目录

**本文不登记进 `docs/reference-sources.json`**：这是对上游源码的一次性只读实证，不是需要跟踪漂移的
可变 Markdown，也没有对应的 `docs/reference-snapshots/upstream/` 原文。上游要跟踪时按固定 revision
重新克隆即可。

结论一句话：**有条件可以**。爬虫层可以脱离 `AppRuntime` / `start_app` / FastAPI 单独构造并跑通，
零 monkeypatch；但聚合层会把配置与数据库层一起拖进来，且把失败原因留在 Recorder 一侧而不放进返回值，
Peach 的 `auth` / 封禁分档拿不到。

## 环境

`uv venv --python 3.14` + `uv sync --no-dev`：CPython 3.14.7，**72 个包，98 MB**（含 amane 自身）。
可选 extra `browser`（patchright 及其浏览器）未装，不装也能跑；PG 只有 r18dev 一个来源需要。

amane 要求 Python 3.14；Peach 的解释器范围以 `pyproject.toml` 为准，桥使用独立 venv。

重依赖与冲突（Peach 侧取 `pyproject.toml` 的精确钉）：

| 包 | Peach | amane 解析结果 | 判断 |
| --- | --- | --- | --- |
| `pydantic` | `==2.13.5` | `2.13.4` | 同一大版本，钉值不同，合并要重钉 |
| `curl_cffi` | `==0.16.2` | `>=0.15.0` → `0.16.3` | 同上；两边都在用它做 TLS 指纹 |
| `pillow` / `fastapi` / `starlette` / `uvicorn` / `socksio` | 精确钉 | 同版本或兼容 | 无冲突 |
| `httpx` | `==0.28.1` | 用 `httpx2 2.12.0`（另一个发行包） | 可共存，但进程里会同时有两套 HTTP 栈 |
| 新增 | — | `sqlmodel` `sqlalchemy` `alembic` `aiosqlite` `asyncpg` `pydantic-ai-slim`（拖 `openai` `anthropic` `tiktoken` `opentelemetry-api` `logfire-api`）`parsel`+`lxml` `structlog` `aiolimiter` `aiofiles` `watchdog` `feedparser` `croniter` `zhconv` `truststore` `requests` 等约 50 个 | 体积大头是 `PIL` 15 MB、`openai` 11 MB、`sqlalchemy` 10 MB、`lxml` 9 MB、`anthropic` 8 MB |

amane 全部用 `>=` 下限，与 Peach「Python 依赖精确固定版本」的门槛（`tests/test_dependency_policy.py`）
直接冲突：要么在 Peach 侧重新钉死整棵传递依赖树，要么放弃 in-process。

## 耦合点清单

1. **爬虫层零耦合**。`Crawler.__init__(client: HttpClient, config: SiteConfig | None = None)`，
   `HttpClient(web: WebClient)`，`WebClient(*, limiters: RateLimiters, proxy=..., timeout=...)`。
   三行就能起来，不需要 `AppRuntime`、`start_app`、EventBus、日志配置、DB、配置文件、任何 patch：

   ```python
   web = WebClient(limiters=RateLimiters(default_rate=0.5), proxy=os.environ["HTTPS_PROXY"])
   http = HttpClient(web)
   crawler = JavDBCrawler(client=http, config=None)
   meta = await crawler.fetch(SearchQuery(number="SSIS-950"))   # -> MediaMetadata | None
   ```

   `config=None` 时用 `profile()` 的内置 `base_url` / cookies；要贴 Cookie 或换镜像域才需要 `SiteConfig`。

2. **`import amane.crawlers.sites` 会连带 r18dev 的 PG 层**。该包的 `__init__` 导出 `R18DevCrawler`，
   顺着 `crawlers.r18dev.database` 把 SQLAlchemy 异步引擎拉进来（实测导入后 `sqlalchemy` 已在
   `sys.modules`）。只 `from amane.crawlers.sites.javdb import JavDBCrawler` 可避开。
   实测：导入 `crawlers.sites` + `crawlers.http` + `net.http` 共 **62 个 amane 子模块**。

3. **`import amane.aggregate` 把配置层与数据库层一起拖进来**。子模块数从 62 涨到 **122**，新增顶层包
   `pydantic_settings` `sqlmodel` `alembic` `mako` `markupsafe` `dotenv` `tomli_w` `sqlite3`。
   起因只有一行：`aggregate/engine.py` 从 `..config.manager` 取常量 `LANG_METADATA_FIELD_SET`，
   而 `config/manager.py` 又 import `sqlalchemy.engine.make_url`、`..sr`、`..plugins`，并经
   `observability` 摸到 `amane.db`。**这一行是 in-process 引用聚合层的唯一硬耦合，也是唯一好剪的。**

4. **FastAPI / uvicorn / asyncpg / pydantic-ai / patchright 全部没有被 import**（实测均为
   `False`，`amane.app`、`amane.api` 也未加载）。架构文档说 `app/` 不依赖 FastAPI，实测成立。

5. **错误语义留在 Recorder 一侧，不进返回值**。`aggregate` 经 `observability.invoke_source` 调爬虫，
   它 catch `SourceError` 后把 `reason`（`ip_banned` / `cloudflare_challenge` / `age_verification` /
   `rate_limited` / `not_found` / `parse_error` …，共 16 档，语义比 Peach 现有分档更细）写进当前
   Recorder，返回值只剩 `failed_sites: list[str]` 这一串站点名。没有 Recorder 时
   `current()` 退回 `_LogOnly` 空壳——不崩，但原因只剩日志文本。Peach 要拿 `auth` / 封禁分档
   驱动 `scraping_access` 的冷却，就必须绕过 `aggregate` 直接调 `crawler.fetch()` 自己 catch
   `SourceError`，或者塞一个自制 Recorder 进 `observability.recorder` 的 ContextVar。

6. **全局状态只有两个 ContextVar**（`net.recording` 的 HTTP 录制钩子、`observability.recorder`），
   默认都未绑定，不需要初始化。`structlog` 未配置时走 stdlib 默认输出，不影响结果。

7. **限速器必须先于 WebClient 构造**（WebClient 持引用）。`RateLimiters` 是容量 1 的严格平滑漏桶、
   不允许突发，语义与 Peach 现有的按来源节流相容，但它按 host 计，Peach 的冷却按「来源」计。

## 三个番号实测

代理走 Peach 当前设置：`peach_proxy.describe()` 报 `mode=environment`，即环境变量
`HTTPS_PROXY=http://127.0.0.1:7897`。每个番号每个来源各请求一次，未批量。

| 番号 | 问的来源 | 结果 | 耗时 |
| --- | --- | --- | --- |
| SSIS-950（有码） | javdb, dmm, javbus | 三家全成功。标量字段全部落在 javdb；title/studio `S1 NO.1 STYLE`/release `2023-11-28`/runtime 150/directors `五右衛門`/actors 3 人带性别/tags 5/poster 2+thumb 3+trailer 1/两家评分（javdb 4.52、dmm 4.32）/三家 `source_url` 各一条 | 9.84 s |
| HEYZO-3607（无码） | javdb, javbus | javdb `search miss`（该站没有这条，不是被挡），javbus 成功：title/studio `HEYZO`/release `2025-06-10`/runtime 61/actors 1 人/tags 15/poster 1+thumb 1 | 4.02 s |
| FC2-PPV-4610638（FC2） | fc2ppvdb, javdb | 两家皆空。fc2ppvdb 回 **HTTP 526**（站方 Cloudflare 源站证书故障），amane 归为 `server_error` 并继续跑完其余来源，未崩；javdb `search miss` | 4.03 s |

FC2 那一档另做了一次判因：换馆藏里的真实旧番号 `FC2-PPV-1015014` 再问 `fc2`（官网）、`fc2ppvdb`、
`fc2club` 三家，官网与 fc2club 返回 `None`（旧商品已下架），fc2ppvdb 仍是 526。**FC2 这一路本次
未取得可用证据**，失败成因在站点侧与商品下架，不是 amane 的实现问题。

一个意外但重要的观察：`docs/SOURCING.md` 与 `metadata_policy.py` 记着「javdb / javlibrary 在本机被
Cloudflare 与 403 挡住」，而本次经 amane 的 curl_cffi 指纹轮换 + 同一个代理，**javdb 直接拿到了
SSIS-950 的完整详情页**，且是该番号全部标量字段的来源。这条已单独复验：同日的 HTTPX 与 curl_cffi
对照实验里十六个格子全回 200，两种传输没有差别，javdb 的 403 按出口 IP 计而不是按传输指纹，
结论与实验表记在 `docs/SOURCING.md`。

## 字段缺口

amane `MediaMetadata` / `AggregatedMetadata` → Peach 候选（`metadata.extract_peach_fields`，
真相字段 `PEACH_FIELDS`）：

| Peach 候选字段 | amane 对应 | 缺口 |
| --- | --- | --- |
| `title` | `title` | 有。但 amane 的多语言靠 `FetchOptions.language` + `multi_language` 站点分次抓，同一次聚合拿不到两种语言的同一字段 |
| `original_title` | **无** | amane 不区分原题与译题，只有「这一次抓的是哪个语言」。Peach 的 `title` / `original_title` 双栏要靠两次 `(site, lang)` 节点自己拼 |
| `performers` | `actors: list[FilmActor]`（name + gender） | 有，且比 Javinizer-Go 多带性别。Peach 侧的规范化、别名与实体绑定照旧自己做 |
| `studio` | `studio`（メーカー） | 有，语义对得上 |
| `series` | `series` | 有 |
| `release_date` | `release`（validator 已归一成 `YYYY-MM-DD`） | 有 |
| `tags` | `tags: list[str]` | 有，原文标签；Peach 的 `genre_taxonomy.map_genres` 照旧自己做 |
| 来源身份 `source` | `field_sources: dict[field -> site]` + `source_urls` | **更强**：Javinizer-Go 一次只答一个来源，amane 直接给到字段级归属 |
| `provider_id` / `content_id` | `external_id: str`（实测填的是详情页 URL，不是站内 ID） | **缺**。Peach 要的 `content_id`（`ssis00950` 这种）只能从 `source_url` 反解，dmm 的 `?id=ssis00950` 在 URL 里，javdb 的 `/v/zK3x8E` 是短码 |
| 目录证据 `director` / `label` / `runtime` / `poster_url` / `cover_url` / `screenshot_urls` / `trailer_url` | `directors` / `publisher` / `runtime` / `poster_urls` / `thumb_urls` / `extrafanart_urls` / `trailer_urls` | 全有，且都是列表带来源，比 Javinizer-Go 的单值丰富。注意 amane 的 `publisher` 是レーベル，对应 Peach 的 `label` 而不是 `studio` |
| 原始证据落盘 | `AggregateResult.raw: dict[site -> model_dump()]` | 有，直接可落盘；`log` 字段实测为空串 |
| — | `plot`、`scores`（带来源的评分） | amane 多出来的，Peach 现在没有对应位 |

来源覆盖对比：amane 24 个影片站 + 5 个演员站；Peach 经 Javinizer-Go 登记 14 个（`REGISTERED_SOURCES`）。
amane **没有** `libredmm`、`tokyohot`、`caribbeancom`、`aventertainment`、`javstash`——其中前三个正是
Peach 无码路线（`PROFILE_SOURCES["uncensored"]` / `ROUTED_PROFILE_SOURCES`）的主力，所以 amane
不是 Javinizer-Go 的超集。反过来 amane 独有 `airav` `avsox` `iqqtv` `freejavbt` `xcity` `kin8`
`prestige` `faleno` `dahlia` `giga` `theporndb` `fc2club` `fc2ppvdb`，以及一个覆盖 29 家片商官网的
`official` 爬虫。

## 三条路线

### 一、借设计（建议先做，无前提）

不引入依赖，只照搬三个已验证的边界：

- **字段级抓取图 + 波次调度**（`aggregate/engine.py`）：标量字段当场短路、聚合类字段按链拼接、
  每个字段记 `field_sources`。Peach 现在的 `FIELD_SOURCE_ORDER` 是同一个意图的静态版本，可以对照补上
  「某个字段拿到了就不再问后面的来源」这一层。
- **`FailureReason` 的 16 档**（`net/errors.py`）：`cloudflare_challenge` / `cloudflare_blocked` /
  `ip_banned` / `geo_restricted` / `age_verification` / `parse_error` / `no_usable_metadata` 等，
  比 Peach 现在 `MetadataProviderError` 的 `auth` / `unavailable` / `not_found` 细得多，而 Peach 的
  `scraping_access` 冷却正需要区分「站方挑战」「出口 IP 被封」「这部片本来就没有」。
- **curl_cffi 指纹轮换**：Peach 的采集走 httpx，javdb 一路在这台机器上被挡；amane 这次通了。

### 二、in-process 引用（有前提，不建议现在做）

前提，缺一条就不做：

1. Peach 的 `requires-python` 抬到 `>=3.14`，放弃 3.12 / 3.13 声明与 classifier。
2. 重钉整棵传递依赖树（约 50 个新包、约 98 MB），并给每个被 import 的模块补归属声明，过
   `tests/test_dependency_policy.py`。`pydantic` 与 `curl_cffi` 两处钉值要重新对齐。
3. 只引 `amane.crawlers.*` 与 `amane.net.*`，**不引 `amane.aggregate`**（否则连带 sqlmodel /
   alembic / pydantic-settings）。聚合逻辑用 Peach 自己的 `metadata_policy`，照搬设计不照搬代码。
4. 自己 catch `SourceError` 取 `reason`，不经 `invoke_source`。
5. 接受 GPL-3.0 的传染范围（见下）。
6. PyInstaller 打包体积与启动时间重测——多 98 MB 依赖，其中 `openai` / `anthropic` / `tiktoken`
   是 `pydantic-ai-slim` 拖进来的，对刮削毫无用处，需要确认能不能在打包时排除。

### 三、sidecar（起它的 FastAPI 只用刮削端点）

边界更干净：进程隔离、依赖不进 Peach 的依赖图、Python 版本互不影响、GPL 的链接争议最弱。
代价是要多管一个服务的生命周期、端口、健康检查与升级，而这正是 Peach 现在用 Javinizer-Go 子进程
所**避开**的——Javinizer-Go 是一次性 `scrape --output json` 子进程，无常驻、无端口、无状态。
sidecar 会把「一个二进制 + 一次子进程」换成「一个 Python 服务 + PG 可选 + 自己的配置文件与 DB」。
如果只要刮削，这笔管理成本换来的仅仅是站点数，不值。

**除非**另有一条：amane 的 `r18dev` 离线 PG 镜像。它把 r18.dev 的完整 dump 导进本地 PG，
逐番号查询变成 SQL，没有网络往返、没有限速、没有封禁。Peach 的 `baseline` profile 就是
`("r18dev",)`，这是唯一一个 sidecar 明显赢过现状的场景——但它要用户自备 PostgreSQL。

### 许可与维护

- **GPL-3.0 被 AGPL-3.0-or-later 引用**：兼容。GPLv3 §13 与 AGPLv3 §13 互相开了口子，允许把
  两种作品合并，合并作品整体按 AGPL 分发，amane 那部分仍受 GPL 约束。Peach 已经是
  AGPL-3.0-or-later，**不需要换许可证**。但两条义务是新的：(a) in-process 或打包分发时，
  Peach 的 PyInstaller 产物是包含 GPL 代码的合并作品，必须提供 amane 对应版本的完整源码；
  (b) 必须在 `docs/REUSE.md` 与分发物里登记固定 revision 与许可证。对比之下 Javinizer-Go 是
  MIT，只需保留版权声明，义务弱得多。sidecar 走独立进程 + HTTP，通常按「独立作品」理解，
  但这一点在 FSF 的口径里始终是灰的，不要拿它当规避手段。
- **未发 PyPI，v0.x 每日变更**：仓库 2026-08-23 建，实测最近 10 天里有 8 天推送，单日最多 16 个提交，
  tag 已到 v0.16.1。只能按 git 固定 revision 依赖（`uv` 支持 `git+...@<sha>`），意味着每次升级
  都要人读 diff：站点解析器的改动会静默改变字段语义，而 Peach 这边没有上游回归测试兜底。
  amane 自己的爬虫测试是 TOML 用例 + 真实网络，Peach 的 CI 跑不了。

## 建议

先做路线一（借设计），不引依赖。其中「curl_cffi 指纹能解 javdb 的封」这一条已单独验过并否定，
判据与实验表在 `docs/SOURCING.md`，剩下可借的是字段级抓取图与 `FailureReason` 的 16 档。
路线二与路线三在上游发布 PyPI 包、或 Peach 确实需要 r18.dev 离线镜像之前，都不划算。
