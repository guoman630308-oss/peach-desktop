# ADR-0043：amane 刮削站点以薄桥子进程接入

- 状态：Accepted
- 日期：2026-09-22

## 背景

`sqzw-x/amane`（GPL-3.0）接了二十多个 JAV 影片站的解析器，其中 fc2club、freejavbt、airav、
avsox 是 Peach 自己没有且站点可达的。2026-09-22 的 POC（[docs/reference-snapshots/amane-crawlers-poc.md](../reference-snapshots/amane-crawlers-poc.md)）
证实它的爬虫层能脱离 FastAPI 与 AppRuntime 手工构造并跑通真实番号，但也量出了三道墙：它要求
Python 3.14、依赖全用 `>=` 下限，并进 Peach 主 venv 会撞上「精确固定版本」的门槛并把 Peach 的
下限从 3.12 抬上去；它的聚合层会连带 SQLAlchemy 与配置层；它的 `observability.invoke_source`
把失败原因吞进 Recorder，返回值里只剩站点名。

## 决策

Python 支持范围以主清单为准，最低与默认版本为 3.14。独立桥继续按自己的锁复现传递依赖，
隔离上游数据库和聚合层；Python 版本一致不改变进程边界和 Peach 的来源结算归属。

**一、桥是一个子进程，形状同 Javinizer-Go。** `tools/amane-bridge/bridge.py` 一次跑完：
`--number <番号> --sites a,b --language jp --output json`，stdout 只有一行 JSON，退出码分
「至少一站取到」「问的站都说没有」「没取到且有站出错」「参数错」四档。无端口、无状态、无常驻。
Peach 一侧 `peach.metadata_amane.AmaneBridge` 起它、读那一行、按站拆成 payload 与失败，和
`JavinizerGoProvider` 是同一种东西。

**二、只用爬虫层与网络层，自己 catch `SourceError`。** 桥只 import `amane.crawlers.sites.<站>`、
`amane.crawlers.http` 与 `amane.net.*`，不碰 `amane.aggregate`，也不经 `invoke_source`。
上游 `FailureReason` 那 16 档原样写进 JSON；Peach 一侧把它们映到 `MetadataProviderError` 已有的
`auth` / `unavailable` / `not_found` 三档，细档留在新加的 `detail` 里给冷却用：`rate_limited`
走 429 那一档，`cloudflare_*` 与 `ip_banned` 走 403 那一档翻倍，写的是 `scraping_access` 同一份
冷却记录。不加新的冷却档。

**三、运行环境是数据目录里一个独立 venv，按锁重建。** `tools/amane-bridge/pyproject.toml` 唯一
的直接依赖是 amane 的一个 40 位 sha，`uv.lock` 钉住整棵传递依赖；venv 建在
`<数据根>/tools/amane-bridge/.venv`，由设置页「来源和凭证」里那张卡一键 `uv sync --locked`
重建（首次约 98 MB）。它不并进主 `pyproject.toml`，依赖策略测试为它登记了替代门槛：只此一条
依赖、必须是完整 sha、锁与清单一致、桥脚本只 import 标准库、amane 与 structlog。

**四、升级是人做的事。** 设置页只显示钉住的 sha 与上游最新 release 的 tag（只读 GitHub API，
取不到写「未取得」），不自动跟上游：改 sha 前要读上游 diff 确认站点解析器的字段语义没变，再
同批更新锁、[docs/REUSE.md](../REUSE.md) 与映射表。Dependabot 推不动 git sha 钉，所以桥不进 Dependabot。

**五、聚合留在 Peach。** 一站一份 payload 按 Javinizer-Go 快照的键名交出去（`maker`、`label`、
`actresses[].japanese_name`、`genres`），身份由 `identifies_code` 核，分歧由 `metadata_policy` /
ADR-0038 结算。amane 的字段合并逻辑不复制。

**六、默认链只在 Peach 没有对应解析器的位置接。** FC2 链在 JavArchive 之后、javdb 之前插
fc2club；无码链末尾接 avsox；有码与素人链不变，freejavbt 与 airav 只能由用户整条
覆盖时点名。javdb / javbus / dmm 这些 Peach 已有的不经桥换：两条路径答同一站，分歧没人会去看。
经桥的几站合成一档 `amane`，一次子进程并发问完，不占综合索引那一档的互证名额。

## 被否决的方案

- **进程内 import amane。** Python 版本、依赖钉法与 SQLAlchemy 三道墙都在；一次上游升级就是
  一次 Peach 依赖树重解。
- **跑 amane 自己的 FastAPI。** 多一个端口、一份配置、一个常驻进程和一套它的数据库；Peach 要的
  只是「给番号、回字段」。
- **经 `aggregate` 拿合并后的结果。** 失败原因不进返回值，一站被封只会表现成「没有」；聚合
  规则也和 ADR-0038 冲突。
- **自动跟随上游最新 tag。** 站点改版的修复和字段语义的变化混在同一次升级里，没人读过 diff 就
  换掉，候选里多出来的错值要等复核时才发现。
- **在 Peach 里重写这四个站的解析器。** 复用优先（`peach-reuse-first`）：上游有人在维护，Peach
  只需要一道进程边界。

## 后果

- 新目录 `tools/amane-bridge/`（清单、锁、脚本）；新模块 `peach.metadata_amane`；`MetadataProviderError`
  多一个 `detail`；`scraping_access` 多三个冷却读写入口；来源链多一档 `amane`。
- 设置页多一张卡、三条端点（`/api/scraping/amane-bridge` 与它的 `check`、`rebuild`）、一个后台任务。
- amane 为 GPL-3.0，Peach 为 AGPL-3.0-or-later，两者以进程边界相接；随 Peach 分发的只有清单、锁
  与桥脚本，不含 amane 源码，amane 由用户机器上的 uv 按锁下载。登记在 [docs/REUSE.md](../REUSE.md)。
- 每次子进程有约 0.6～1 秒的 import 开销（`amane.crawlers` 的包 `__init__` 连带 SQLAlchemy），
  接受它换来不改上游一行。
- amane 的 `WebClient` 以 `verify=False` 发请求；这一路取回的是公开页面的文字与图片地址，不带
  凭据，Peach 自己的 httpx 那一路不受影响。

## 修订：移除 fc2ppvdb（2026-09-22）

fc2ppvdb 从开放站表、FC2 链与桥的站表中移除：当日实测三个商品号都回 HTTP 526（站方 Cloudflare 源站证书故障），用户判定该站已不可访问。 <!-- copy-lint-disable-line -->
经桥的站只剩 fc2club、freejavbt、airav、avsox 四站；站点恢复后要接回来，按本 ADR「四」的升级流程同批改站表、链与 [docs/REUSE.md](../REUSE.md)。
