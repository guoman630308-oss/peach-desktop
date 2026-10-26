# ADR-0048：每个站只有一个归属：amane 片商级解析器进官方档

- 状态：Accepted
- 日期：2026-09-23

## 背景

ADR-0044 按站的性质分归属：官方与半官方站由 Peach 自写，社区站经 amane 桥。照这条判据，amane 里现成的
片商官网解析器（按系列前缀路由到二十九家片商的 `official` 模块，以及 Prestige、FALENO、DAHLIA、MGStage
各自的解析器）只能等 Peach 逐家重写；在那之前，有码与素人链的官方档只有 r18.dev 一家，它是 DMM 数字版
目录的镜像，给的发行日是配信开始日口径，演员与标签也只是 DMM 那一份。

用户 2026-09-23 决定把 amane 的片商级解析器开进官方档，并把归属判据改为「每个站只有一个归属」。

## 决策

### 归属判据

一个站只由一条路径问：Peach 已有自写解析器的站只问自写那一份，没有的站可以经 amane 桥，不论它是官方站还是
社区站。自写与 amane 重叠的四站（r18dev、fc2、javbus、javdb）继续只问自写解析器，不经桥重复问；两条路径答
同一站，分歧没人会去看。来源分级仍按站的性质登记在 `metadata_policy.SOURCE_SPECS`：经桥的片商官网是
`official`，经桥的转载站是 `community`，分级决定结算层级，与取数路径无关。

### 进链的站

| 站名（Peach 来源名） | amane 模块 | 分级 | 作用 |
| --- | --- | --- | --- |
| `makers` | `official` | official | 按系列前缀路由到二十九家片商官网的作品页；前缀不在它的表里不发请求 |
| `prestige` | `prestige` | official | Prestige 的作品 API |
| `faleno` | `faleno` | official | FALENO 的站方 REST 接口 |
| `dahlia` | `dahlia` | official | DAHLIA 的站方 REST 接口 |
| `mgstage` | `mgstage` | official | MGS 素人系的发行渠道 |

amane 的 `official` 模块在 Peach 里叫 `makers`：`official` 已是来源分级的名字，一个词不同时当站名。
选站理由是「片商官网 > 官方镜像」：片商自己那一页是发行方口径，r18.dev 转的是 DMM 数字版目录。
2026-09-23 实测（`build/agent-verification/amane-official-stage.md`）五站都经代理取到了自家番号，
S1、MOODYZ、IDEA POCKET 官网与 FALENO、DAHLIA 给的发行日和 r18.dev 当前快照一致。

### 各链官方档顺序

- 有码：`prestige`、`faleno`、`dahlia`、`makers`、`r18dev`，再到 AVBase、JavBus、javdb。
- 素人：`mgstage`、`r18dev`，再到三家综合索引。mgstage 不进有码链：它对有码号是转售店，标题缀着店铺特典。
- 无码与 FC2 不变。

经桥的官方站在采集任务里合成一档 `amane_official`，一次子进程并发问完。Prestige、FALENO、DAHLIA 对任何番号
都发请求，由 `metadata_routes.route_for_code` 按本机证据裁掉：番号字母前缀在 `MAKER_EVIDENCE` 的表里（取自
账本 2026-09-23 的只读统计），或账本厂牌、路径、文件名里写着这家，才问它；一个番号只属于一家片商，三家有一家认了
就不再问 `makers`。都不认的交给 `makers`，前缀不在 amane 片商表里时桥内零 HTTP，只花一次子进程（实测 0.8 秒）。
所以有码番号在官方档里至多问一家片商，素人番号只问 mgstage。

### 短路与字段口径

- 官方档取齐这一行还缺的必填标量（标题、演员、厂牌、发行日）就不问下一档，与 r18.dev 同一判据。
- FALENO、DAHLIA 的作品资料没有类别。缺标签的行在官方档之间多问一家：下一档仍是官方档（有码链上的 r18.dev）
  时，片商站没给标签就接着问它；下一档是综合索引或转载站就照样停，不为标签去问按出口 IP 计配额的 javdb。
- Prestige 回的 `release` 是 MGS 配信开始日，比发行日早一个月（ABW-032、ABF-246、ABF-335 三例），只记在
  资料的 `extra['delivery_date']`，不当发行日候选，也不进 `FIELD_SOURCE_ORDER` 的 `release_date` 一行。
- 结算上片商站排在每个字段的最前（`metadata_policy.MAKER_SOURCES`），同属官方层，先于 dmm 与 r18.dev。

### 失败与冷却

amane 的失败原因经 `metadata_amane.AMANE_REASONS` 进契约。`geo_restricted` 与封禁同归冷却 `blocked`：只收日本出口的
站换一部片问结果不变，换出口才会变。amane 只按正文认地区限制；正文认不出、只回 401 或 403 的站上游归 `http_error`
（Prestige 在非日本出口上回 CloudFront 403），`metadata_amane.contract_reason` 把它归 `auth_required`，冷却按
`blocked`。这两种都是「被挡」，属于 `auth` 一档，不冻进七天的「没有」记忆；`not_found` 才是「没有」。年龄验证归
`auth_required`，不停整站。

## 被排除的站

| 站 | 原因 |
| --- | --- |
| dmm（amane 的 HTML 爬虫） | 与 r18.dev 是同一份目录，发行日给配信开始日（SSIS-057 答 2021-05-01，账本 2021-05-07），ABW-032 答的是蓝光 BOD 版，一次 3～14 秒；再问一遍只多一次请求，不多一层口径。DMM 自己的 GraphQL 接口另由 Peach 自写解析器接入（ADR-0059），不经桥 |
| giga | 实测站内搜索 `/top/search?keyword=` 两次都回 HTTP 404，未取得 |
| kin8 | 爬虫从 `KIN8-3500` 里取到 8，拼出 `/moviepages/8/`；换纯数字 3500 仍是没有，未取得 |
| r18dev、fc2、javbus、javdb | 已有自写解析器，只有一个归属 |
| theporndb | 要 API token，桥刻意不带配置 |
| xcity、getchu、jav321、javlibrary、iqqtv | 不是片商官网，不在本次官方档的范围；要接时按归属判据另议 |

giga 与 kin8 留在桥的站表里（`tools/amane-bridge/bridge.py` 的 `SITES`），是官方档的候选：上游修好后按同样的小样本
实测，通了再进链。

## 被否决的方案

- 按 ADR-0044 的原判据逐家自写片商解析器：二十九家片商官网的路由与页面解析 amane 已经在维护，Peach 再写一份等于
  同一件事两份实现，而官方站的真相字段恰恰最该有人持续盯着上游的改版。
- 让片商站排在 r18.dev 之后作补充：这样片商那一页只在镜像落空时才问，发行方口径永远排在镜像口径后面，
  与「片商官网 > 官方镜像」相反。
- 每个番号把 Prestige、FALENO、DAHLIA 都问一遍再看谁答：这三家对不认识的番号也发请求，一部片白发三次。
- 把前缀表从 amane 的 `MANUFACTURER_SERIES` 抄进 Peach 来决定问不问 `makers`：那是复制上游数据，前缀不认时桥内本来就
  零 HTTP，省下的只是一次子进程。

## 后果

- 影响面：`tools/amane-bridge/bridge.py`、`src/peach/metadata_amane.py`、`metadata_routes.py`、`metadata_policy.py`、
  `library_processing.py`、`sources/base.py`、`scripts/scrape_codes.py`，对应测试与 [docs/SOURCING.md](../SOURCING.md)、[docs/REUSE.md](../REUSE.md)。
  采集设置页从 `metadata_amane.describe` 读站名，前端无改动。
- `POLICY_VERSION` 升为 `metadata-source-policy-v5`；`SOURCE_SPECS` 多了四个来源名，`sources_fingerprint` 随之变化，
  此前记下的「没有」按新指纹作废，下一轮重问一次。
- mgstage 从 `HISTORICAL_SOURCES` 移出：账本里 `javinizer:mgstage:tag` 那近 800 行 provenance 仍按官方级别结算。
- 有码链每部片多一次子进程（约 1～7 秒），换来发行方口径的标题、演员、发行日与标签；官方档取齐就不再问综合索引。
- 片商站属于官方层，按 ADR-0035 与 ADR-0038 的界线，它的候选在自动落库里可以替换账本现值。实测里会动到的是旧
  Javinizer 快照写下的发行日：IPX-895 账本 2022-07-08，官网与当前 r18.dev 快照都是 2022-07-12。
