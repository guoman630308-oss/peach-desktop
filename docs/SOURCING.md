# 身份、来源与标识采集

本文件讲 Peach 怎样从外部站点取作品资料、女优与厂牌身份、头像和标识：每个来源怎么接、要什么前提、
会撞上什么限制、出错看哪里。只写现在怎么做，改动经过见 Git 与 ADR。

- 批处理的限流、续跑与流量预算见 `.claude/skills/peach-batch-jobs/SKILL.md`。
- 参考外部产品时的取证登记见 `.claude/skills/peach-reference-evidence/SKILL.md`。
- 真正写账本之前读 `.claude/skills/peach-ledger-write/SKILL.md`。

开发说明中的**候选（candidate）**指保留来源与置信度、尚待判断的信息；面向使用者时按用途写待确认资料、搜索结果或备选图片。**复核件**在本文指 CSV：送审时是待确认资料表，保存人工决定后是确认结果表。**未取得**表示没有取得相应资料或证据，不用推测补足。

| 要做的事 | 看哪节 |
| --- | --- |
| 判断某个来源说的能不能信 | 采集判据 |
| 库内采集对一个番号问哪几站、按什么顺序、何时停 | 哪些行不该进 JAV 刮削、按内容类型的来源链 |
| 新接或修一个站的解析器 | 站点解析器契约 |
| FC2、一本道、K-MIB、素人 Wiki 的专门来源 | 对应各节 |
| javdb、AVBase、JavBus 回 403、被封、进冷却 | javdb、AVBase 与 JavBus 的限流与封禁 |
| 番号认错、创作者建错、跨作品串号 | 番号目录、创作者与水印 |
| 女优改名、别名、头像、社媒链接、事务所 | 命名与身份合并、女优名字与头像来源、目录型来源与社媒链接 |
| 厂牌日文名、官网、Logo、小圆标 | 厂牌名与厂牌标识、站点圆标与图标合成 |
| 订阅「最近出了哪些番号」 | 番号发现源（Feed） |

## JavLibrary 浏览器来源

在「来源和凭证」的 JavLibrary 卡片选择连接方式并保存，点击「检查连接」。需要人机验证时，Peach 打开浏览器窗口，由用户手动完成；验证成功后复用 `<数据根>/secrets/browser` 的浏览器会话。会话过期时需要再次手动验证，不自动解题或绕过验证。此入口不需要 JSON 接口地址或 API 密钥；自定义 JSON 来源表单不接受 JavLibrary 网页。

JavLibrary 位于有码作品的社区来源链末尾，补充标题、演员、厂牌、发行商、导演、日期、时长、类别与封面地址。搜索结果与详情页都核对完整番号；单结果直接跳到详情页也能解析。未知页面结构、人机验证、登录限制与明确无结果分别处理，验证页不会记成「没有这部作品」。人工等待不占下一站查询预算。来源受限时沿用冷却与备用来源规则；返回的社区资料继续走候选复核，图片继续执行既有身份、来源与尺寸检查。

## 采集连接与网盘读取

「同步媒体库」与 `scripts/scrape_codes.py` 共用正式来源链。来源设为「直连」时使用直连客户端；设为跟随 Peach 代理时，使用配置页保存的直连、自定义代理或跟随环境设置。在线封面使用同一来源传输，amane 桥把代理及逐站直连设置传给子进程。浏览器取页也遵循显式直连和自定义代理；代理地址带用户名时退回 HTTP 客户端。

跟随环境时，HTTP 客户端读取进程代理环境变量，浏览器使用系统代理；两者不保证采用相同出口。应用直连不能排除操作系统 TUN。部分独立维护脚本直接使用 `HttpxTransport` 或 `page_cache.Site`，只读取环境代理，未接入页面保存的 Peach 代理，不能据此声称所有脚本设置一致。

网盘媒体的探测、九宫格、时间轴和播放会读取视频数据；挂载路径的网络连接由 CloudDrive2、OpenList 对应挂载工具负责。Peach 的目录「媒体读取策略」限制后台读取和确认边界，不修改挂载工具的代理。联网采集作品文字和封面使用上述来源设置，与视频所在网盘分别配置。

## 采集判据

采集结果须保留资料来源与判断依据。代码规则能够确定、且可按来源整批撤回的资料按既有规则保存；LLM 输出、来源冲突和多种可能结果保留为待确认资料。不能只凭站名判断资料一定正确。

- 需要人工确认的采集脚本输出待确认资料 CSV，用户的确认结果保存为可再次读取的文件。写 `entity.canonical_name`、`asset.studio`、`entity_link` 或头像文件须遵守 `peach-ledger-write`，不能把待确认资料直接当作用户已确认的结果。
- javdb、laoshi、jae、R18 都只是参考，各有自己的可信度。资料页上的名字可能被转载渠道改过。
- 同一条事实由两个互不相干的来源给出，才是最强的证据。只有一个来源说的，一律当候选。
- 两边冲突时不按站名分高低，看这条事实能不能另找一个来源印证。已经进账本的那一侧也一样：
  它当初也只是某个来源的一次判定。

例：判一个 X 账号是不是本人官方号，粉丝量是第一道筛子，明显偏低的就该疑。实际遇到过方向相反的两例：
一例是账本原有的账号对、javdb 给的错；另一例是 javdb 给的在活、账本原有的已疑似失效。所以冲突不能一刀切成
「以账本为准」或「以新来源为准」。

## Seesaa Wiki 作品证据

这节讲怎样从 Seesaa 上的素人认人 Wiki 取番号的出演、标题和日期候选。

入口是 `scripts/scrape_codes.py --profile seesaa`（素人系総合 Wiki），两个认人 Wiki 用
`--sources av_neme,av_name` 点名。三站同一个模块、同一套取页层，产出既有的
`metadata-field-candidates` CSV。

请求与续跑：

- `--wiki-pages-file` 可以预取目录页，同一页里的全部作品共用一次请求。
- `--wiki-max-requests` 默认 80，每站各一份额度；每页最多 4 MiB；同主机合计每 2 秒至多一次请求。
- 成功页有缓存，中断后可续跑；`--refresh` 重取页面。
- 撞上 403、429 或机器人验证就停止本批联网。这次未取得不冻结成永久无结果。

怎么读页面：

- 番号按作品表的 `NO` 列精确回配，支持既有的数字前缀与补零规范化。两个认人 Wiki 的站内搜索是全文子串
  匹配，`AR-101` 会带回 `GAR-101`、`STAR-101`，靠精确回配挡掉。
- `ACTRESS` 链接的显示名是出演候选，链接目标只当身份核验线索。EUC-JP 页面按来源编码解码。
- 名单里带 `？`、`▲`、`--`、没有人物页的名字或写坏的空链接时，整份只留原文证据；冲突行报错。
- `TITLE`、`RELEASE` 出标题与日期候选；`SUBTITLE` 不冒充完整标题。
- 两个认人 Wiki 只出演员候选。标题、日期、素人名义只进 `wiki_evidence`（av_name 厂牌页上的 MGS 标题是
  截断的）。
- 封面 URL、人物页链接、备注和跨平台编号只留在原始快照与候选证据里。不下载图片，不自动合并人物或作品。
- 页面结构的细节在模块 docstring（`src/peach/sources/seesaa.py`）。

Seesaa 是托管平台，下面这些 Wiki 由各自的维护者编辑，不能因为同在一个平台就当成互相独立的印证：

| 来源 | 已核验的用途 |
| --- | --- |
| [素人系総合 Wiki](https://seesaawiki.jp/w/sougouwiki/) | 厂牌作品表、合集名单、名义与人物页链接、日期、图片及跨平台线索；已接入脚本 |
| [このAV女優の名前教えてwiki](https://seesaawiki.jp/av_neme/) | DMM、MGS、S-Cute、舞ワイフ 的出演名义核验；不用表格，系列页与月份归档页一部一个 `h5` 小节（「品番\| 系列名」加「名前(女優名)」）；300MIUM 可查，FC2-PPV 与 MIB 查不到；已接入脚本（`av_neme`） |
| [AV女優の名前特定wiki](https://seesaawiki.jp/av_name/) | FANZA 素人与 FANZAビデオ 一个品番一页（键值表），MGS 挂在厂牌页 `h5` 小节里且只列最新约 1100 件；300MIUM、FKOS 可查，FC2-PPV、476MLA 与 MIB 查不到；已接入脚本（`av_name`） |

表内入口取页于 2026-09-06，两个已接入的认人 Wiki 于 2026-09-24 复测。未接入的同类 Wiki（AV女優大辞典、AV女優パーフェクト、
シロウトTV・ナンパTV、人妻系まとめ、素人AV女優名鑑，以及 VR、成人映画、NHpedia 等分领域索引）只作人工查证线索；平台上其余的
Wiki 与视频作品资料无关。

## K-MIB 官网作品与演员

韩国 MIB 的番号拿去问 JAV 目录站必错，官网 [k-mib.com](https://www.k-mib.com/) 是唯一可信来源。
脚本是 `scripts/harvest_kmib.py`，解析器在 `peach.metadata_kmib`，读 `peach-data/sources/k-mib/` 下的快照
（列表页、`video/<idx>.html`、`star/<idx>.html`、`images/`）。

- `--fetch` 联网补快照：同主机间隔 1.5 秒，已有的跳过，403、429 立即停止本批。robots.txt 只挡
  `/_adm/` 与登录页。
- 默认只读，写三份 CSV：`kmib-catalog.csv`（全站作品与账本对照，官网没有的账本番号记「未取得」）、
  `kmib-performers.csv` 与 `kmib-metadata-field-candidates.csv`。
- `--apply` 先备份 ledger，再补空番号、按 ADR-0018 自动批准官网候选、装缺失封面，给已关联 MIB 作品的
  演员装官网资料页链接和头像，给 MIB 装官网链接和 logo。
- 候选的 item_key 带 `:kmib` 后缀，因为这批番号的 `<番号>:<字段>` 已被 JAV 错配候选的拒绝决定占用。
- 合作厂牌（JS MEDIA、Studio REAL、SETFLIX、PEEKO、MMP、JO GLOBAL）的 Actor 栏常写厂牌名，按厂牌名与
  番号前缀记为 maker。这些前缀不属于 `KOREAN_MIB_PREFIXES`。
- 分类只投影到已有词表。Fetish、Kiss、Sex Toy、Ahegao、Tiny Girl、Oil、Femdom 作为未收录提示随候选进复核。
- 双词罗马音艺名（`Mao Hamasaki`、`Sarina Momonaga`）过不了艺名形态门槛，由人工复核；后者按别名对到
  `藤木真央`，身份待确认。

## 哪些行不该进 JAV 刮削

库内采集拿一行去问 JAV 来源之前，先过 `catalog_rules.scrapes_as_jav`。它只拦一种情况：**创作者作品的
文件名被读成了番号**。

- 例：`sumwall95 masturbation_1.mp4` 读出 `SUMWALL-095`，`aerith 2412a.mp4`（角色名加年月）读出
  `AERITH-2412`。形态上和厂牌番号毫无区别，`is_jav_code` 一律认。
- 判据：归在某个创作者名下，没有厂牌也没有别的发行证据，写法又不属于任何发行体系。
- 放行：FC2 商品号、素人平台的三位数字前缀、MGStage 的 `STP`／`SIRO`、日期式番号。创作者的文件名撞不出
  这几种形状。

**不要换成 `is_jav_asset`。** 它判的是「算不算一部已坐实的 JAV 发行物」，要求发行证据先落库，用来决定
浏览时归到哪一类。刮削入口用它会死锁：证据本来就要刮回来，没刮过就没有证据；`300MIUM-698` 这类真素人
番号也会被一起挡在门外。

## 按内容类型的来源链

过了上一节那道门，问哪几站、按什么顺序、什么时候停，由 `peach.metadata_routes` 一张表决定。

这张表只管**查询侧**；取回来的值怎么排序、分歧听谁的，在 `peach.metadata_policy` 与落库那一侧。例如 javbus
在结算上是备选来源（ADR-0035），查询上却排在 javdb 前面：javdb 按出口 IP 计配额，先问便宜的两家只影响谁先撞限流。

类型只看番号形状与本机证据（路径、文件名、账本厂牌），不等元数据到齐；否则没有元数据的番号永远轮不到该问的那家。

| 类型 | 判据 | 链（从左到右） | 这条链为什么不含 |
| --- | --- | --- | --- |
| censored | 其余厂牌番号 | （本机证据认得时）prestige／faleno／dahlia，Prestige 系另有 mgstage 备用；都不认时 makers → r18dev → dmm → avbase → javbus → javdb → javlibrary | 1pondo（无码片商）、fc2（另一套商品号） |
| amateur | `300MIUM-1239` 这类三位数字前缀，加 `SIRO`／`STP`／`STN`；本机证据写着带三位前缀的同一个号（`LUXU-688` 的文件是 `259LUXU-688.mp4`） | mgstage → avbase → javbus → javdb → r18dev | makers 与三家片商站（素人号不归它们）；dmm（搜 `MIUM 1239`、`LUXU 1475` 零结果）。r18dev 垫底：本机快照里它一份素人号都没给过 |
| uncensored | 日期式番号、`HEYZO-1380`、Tokyo-Hot 的 `n0780` | （证据指着一本道时）1pondo → avbase → javbus → javdb → avsox | r18dev：无码番号在它上面没有 |
| fc2 | `FC2` 开头的商品号，与没归一的 `FC-43768` 这种短写法 | fc2 → fc2cmadb → fc2ppvdb → javten → javarchive → javdb | r18dev（85 条全空）、avbase 与 javbus（对 FC2 零产出）、fc2club（本机快照零产出，还回 429 进冷却） |
| kmib | `KOREAN_MIB_PREFIXES` 里的前缀 | 一家都不问 | 全部：番号与日本片同形，问回来的是别的作品 |
| other | 有码形状，但路径带国产标记（`OTHER_SYSTEM_MARKS`：国产、网红、兔子先生、麻豆……）或文件名是欧美片站写法（`wankzvr-elena-koshka-GFE-180`） | 一家都不问，也不问封面 | 全部：Peach 没接这些体系的来源 |
| unsure | 有码形状，本机证据里有字母段却没有这个号的完整写法（`UWFr85dczsVeysGg.mp4` 读成 `UWFR-085`） | avbase → javbus → javdb，一遍 | 片商站、r18dev、dmm：号多半读错了，官方档只会查空 |

容易问错的几处：

- **`ABF`／`ABW`／`ABP` 是有码不是素人。** mgstage 首页同时挂有码号与素人号，按站点归类会把整个 Prestige
  判成素人。素人只认三位数字前缀与 MGS 那三个字母前缀。
- **一本道要本机证据。** 日期式番号本身不带片商，一本道与カリビアンコム 同形，问错那家答回来的是同一天发行
  的另一部片。指不着就直接落到综合索引。详见「一本道作品资料与封面」。
- **三家片商站只问自家番号。** Prestige、FALENO、DAHLIA 对任何番号都会发请求，所以只有番号字母前缀在
  `metadata_routes.MAKER_EVIDENCE` 里、或账本厂牌与路径写着这家时才问它；有一家认了就不再问 `makers`。
  `makers` 按 amane 自带的片商表路由，前缀不在表里时桥内零 HTTP，只花一次子进程（约 0.8 秒）。
- **国产、欧美归 `other`，一家都不问。** 认得出的停在这里，认不出的仍按有码问。里番没有判据，不单列。

候选来源（未接入，等实际缺口出现再接）：sokmil、kingdom.vc、km-produce、fantia（要 Cookie）、jav321，
出自 2026-10-01 对 Javinizer-Go、mdcz、AMMDS 等元数据引擎的调研（ADR-0092）。

### 什么时候停

- 缺内容标签的有码作品在标量结算后仍单独请求 JavDB `類別`，本地 NFO 优先（ADR-0100）；已尝试、已缓存、路由排除与明确缺失的来源不重复请求，失败保留已有标量。已有标签的作品通过 `scrape_codes` 按番号重取候选，例行库处理不扩成全库回填。
- 官方与发行方那几家逐个成档。一档把**这一行还缺的必填标量**（标题、演员、厂牌、发行日期）给全了，
  就不问下一档；只给了一半照旧往下问，否则那一行只能等人工去填。
- 综合索引那一档整档一起问，不逐家短路。免复核要两家取值一致（ADR-0030、ADR-0034），封面互证要两个
  不同图源（ADR-0032），问到第一家就停等于把这两条判据的样本降到下限。
- 这一行要的本来就只有标签或封面时，第一家给了就停（ADR-0033）。
- 唯一例外：FALENO、DAHLIA 官网不给类别，缺标签的行在片商站答完标量后再问一次 r18.dev；下一档是综合索引
  就照样停。r18.dev 答了就不再为标签问 dmm，两家是同一份目录。

### DMM / FANZA 目录

有码链上 r18.dev 之后的 dmm 是 DMM／FANZA 自己的 GraphQL 目录（`sources/dmm.py`，ADR-0059），补 r18.dev
漏收的那部分，当月新片常见 r18.dev 回 404 而 DMM 已有。

- 接口只收 POST。从中国电信直连也回 200，一次 0.3～1 秒；不需要日本出口，没有年龄门。
- cid 先按 `{字母}{五位数字}` 猜，猜不中再搜。搜索结果只收字母段与数字都对得上的，再用 `makerContentId`
  核身份。
- `makerReleasedAt` 是发售日，以 UTC 写日本时间零点，换成日本时间再取日期。
- 厂牌是日文名，靠 `entity_alias` 归一到账本实体。
- Prestige 已从 FANZA 撤下，MGS 素人号不在这份目录上，所以它只在有码链。

### 少发请求：快照与「说过没有」

- 上一趟存下的原始快照（`<Peach 数据目录>/sources/library-metadata/<番号>-<来源>.json`）还新鲜就直接用，不发请求。
  有效期与「说过没有」的记忆同为 7 天，两边同时到期，才不会出现「没有」已过期而「有」还压着旧值。
  「重试未完成项」要的就是新答复，强制重问。
- 「说过没有」按站记（`state/library-metadata-misses.json`，`library_processing._MissCache`）：一档里说过没有
  的站摘掉，全档都说过才整档跳过。文件记着每站第一次出现的时刻；「封面没有」那条的链上有一站晚于它接入，
  这条就不作数。有码番号的记忆不会因为 FC2 链上接了新站而作废。
- 只有待批候选的字段照样算缺：免复核要两家一致，只有一家给过的字段正该再问下一家。已经给过这一行候选的
  来源不再问（`_answered_sources`），每家对一行最多问出一次候选；链上每站都给过候选或说过没有之后，这一行
  在读盘之前就跳过。依据：2026-09-25 有 410 条 FC2 演员候选待批，把待批候选当成已有着落的话，它们会把
  FC2PPV-DB 整个挡在门外。
- 封面那一步接着用资料那一步的快照：r18.dev 快照里的作品 JSON 直接给出原图地址，社区那几家的快照直接交给
  图源印证；手上有一张候选宽到 700 就不再问 r18.dev 与 MGS（`best_cover` 的 `sites_when_needed`）。r18.dev
  不在这个番号的链上时，封面那一步也不问它。
- 浏览器来源（FC2PPV-DB、JAVten）一条请求给 45 秒，不按剩余预算往下裁。第一次弹验证窗口时等人点的那段
  时间记进 `LibraryMetadataProvider.excused`，不算进这部片的动作预算（ADR-0065 第二条）。

### 手动替换一条链

用户可以整条替换某个类型的链：`process_library(route_overrides='censored=r18dev,javdb')`。文本写法是
`类型=来源,来源`，多条用分号隔开，也接受同形状的映射。

- 替换是整条替换，不是逐项合并。逐项合并会变成「删不掉一家」：想摘掉 javdb 得先知道内建表里有它。
- 类型名或来源名写错直接报错。静默忽略的表现是「设置改了没生效」，比报错难查得多。

来源链的取舍参考了 amane 的 `docs/dev/content-routes.md`，证据登记在 `docs/reference-sources.json` 的
`amane-content-routes`。

### 经 amane 桥的站

有些站不是 Peach 自己的解析器，由 amane 经 `tools/amane-bridge/` 的子进程回答（ADR-0043）。桥的 venv 在
「来源和凭证」页那张卡上重建，钉住的版本只由人改。

- **社区档 `amane`**：avsox，以及只能由覆盖点名的 fc2club、freejavbt、airav。链上合成一档，一次子进程并发
  问完。上游报的 `rate_limited` 按 429 那一档、`cloudflare_*` 与 `ip_banned` 按 403 那一档写进同一份冷却记录；
  正在冷却的站不带进子进程。
- **官方档 `amane_official`**（ADR-0048，每个站只有一个归属）：`makers`（amane 的 `official`，二十九家片商
  官网）、`prestige`、`faleno`、`dahlia`、`mgstage`，是有码与素人链官方档的第一家。分级是 official，结算上
  片商站排在 dmm 与 r18.dev 之前。
- Prestige 回的日期是 MGS 配信开始日（ABW-032 答 2020-11-11，发行日 2020-12-11），只记在
  `extra['delivery_date']`，发行日留给 r18.dev。
- 地区限制与站方裸回的 401/403 都算「被挡」：归 `auth` 一档，整站按 403 那一档冷却，不冻进「没有」的记忆。
- dmm、giga、kin8 不进官方档，理由与数据见 ADR-0048 与 `build/agent-verification/amane-official-stage.md`。
- fc2ppvdb.com 旧域已关（ADR-0043 修订）；新站 fc2ppv-db.com 由 Peach 自写解析器接回 FC2 链（ADR-0060），不经桥。
- 经代理的实测记录在 `build/agent-verification/amane-chain-realtest.json`；那一轮 airav 的搜索地址回 404。

## 站点解析器契约

自写解析器和 amane 桥上的站套同一个接口形状（`src/peach/sources/`，ADR-0044「实施：解析器契约」）。新接
一个站就照这个形状写。

- 一个站一个类：`fetch(code, session=)` 取作品页，`parse(page, code)` 读出记录，`query()` 把两步串起来。
- 站名、主域与图床、请求间隔、是否带 Cookie、页面上限和档位都写在 `SiteConfig` 里，是数据不是常量。
- 返回只有一种 `SiteRecord`。`payload()` 把它投影成来源快照那份 dict，候选、来源链结算、封面层与账本读到的
  东西不变。
- 失败只有一张 `FailureReason` 表（十二档）。`REASON_KINDS` 把它映射到 `MetadataProviderError` 的 `auth`、
  `unavailable`、`not_found` 三档；`COOLDOWN_ACTIONS` 规定哪几档要把整站写进 `scraping_access` 的冷却记录：
  `cloudflare_challenge`、`ip_banned`、`geo_restricted` 按 403 那一档翻倍，`rate_limited` 按 429 那一档。
- 自写站的 `query()`／`records()` 包在 `SiteSource.holding()` 里，报出上面几档就交给会话传输链上的
  `SourceTransport.hold` 记账，和 amane 桥写同一份记录。AVBase 回 200 的验证页就是这样整站冷却的。
- 冷却期（`SourcePaused`）、动作预算与连接失败由传输层抛出，契约原样放过。所以每个站的限流与封禁表现
  只在传输层一处决定。
- 同一站在配置、`SOURCE_SPECS`、`SOURCE_LABELS`、`PROVIDER_NAMES`、`scraping_access.SOURCES`、
  `SOURCE_INTERVALS` 里的几行必须一致，由 `tests/test_metadata_sources.py` 守住。

下表各站全部已套契约：自写的十二站登记在 `sources.SITE_SOURCES`，经桥的九站在 `metadata_amane`。

| 站 | 状态 | 位置 | 说明 |
| --- | --- | --- | --- |
| JavBus | 已套契约 | `sources/javbus.py` | 年龄门归 `auth_required`；404 与番号对不上归 `not_found` |
| javdb | 已套契约 | `sources/javdb.py` | 搜索页与详情页两跳都在 `fetch` 里；登录页归 `auth_required`，详情页番号与搜索结果不一致归 `parse_error`；主机间隔 3 秒进配置 |
| fc2club、freejavbt、airav、avsox | 已套契约（经桥） | `metadata_amane.py` | amane 的十六档 reason 经 `AMANE_REASONS` 一对一翻成契约细档，先套进 `SiteRecord` 再投影；`SITE_CONFIGS` 只持有站名、界面名与档位，主域与 Cookie 由 amane 管 |
| makers、prestige、faleno、dahlia、mgstage | 已套契约（经桥） | `metadata_amane.py` | 档位 `official`（`OFFICIAL_SITES`）；`http_error` 带 401/403 经 `contract_reason` 归 `auth_required`；prestige 的 `release` 进 `extra['delivery_date']` |
| AVBase | 已套契约 | `sources/avbase.py` | 搜索页一跳，从 `__NEXT_DATA__` 里挑出本作与它自己的商品条目；搜索无命中归 `not_found`，Cloudflare 验证页归 `cloudflare_challenge`，别的结构对不上归 `parse_error`；不给时长，`runtime` 留空 |
| r18.dev | 已套契约 | `sources/r18dev.py` | 作品 JSON 与 combined 页两跳，日文写法、女优头像模板与 genre 取日文原词都在这一站里；`content_id` 对不上归 `parse_error`；档位 `official_mirror`，页面上限 2 MiB |
| DMM / FANZA | 已套契约 | `sources/dmm.py` | GraphQL 接口经 `Session.post` 一到三跳（猜 cid → 搜索 → 详情）；`ppvContent` 为 null 与搜索无命中归 `not_found`，回的不是 JSON 或接口拒绝查询（只有 `errors`）归 `parse_error`；档位 `official`，页面上限 1 MiB |
| 一本道 | 已套契约 | `sources/onepondo.py` | 作品 JSON 一跳；认不出作品号、404 与 `MovieID` 对不上归 `not_found`，回的不是 JSON 归 `parse_error`；档位 `official`，页面上限 1 MiB |
| FC2 | 已套契约 | `sources/fc2.py` | 商品页一跳，下架页与 `sku` 对不上归 `not_found`；番号、时长、原件地址与占位件判定是三站共用的函数，也在这里；页面上限 2 MiB |
| fc2cmadb | 已套契约 | `sources/fc2cmadb.py` | 作品页之后在 `query()` 里带握手头点名 `actresses` 再问一跳，那一跳失败按没有女优交回；评论区的演员、等价与合集解析也在这里，`scripts/fetch_fc2_metadata.py` 从这里取 |
| FC2PPV-DB | 已套契约 | `sources/fc2ppvdb.py` | 作品页一跳 `/ja/videos/<id>`；`<h1>` 里认不出番号（含站方 200 的 404 页）归 `not_found`，Cloudflare 验证页归 `cloudflare_challenge`；女优只在出演女優块里取，「流出」标记进 `extra['leaked']`，不交封面（缩略图 360×360） |
| JAVten | 已套契约 | `sources/javten.py` | `/search?kw=<id>` 一跳，单命中时站方直接跳作品页，落到译文页再取日文原页；`h1.fc2-id` 对不上归 `not_found`，`og:url` 带语言前缀归 `parse_error`（中文是机翻，只收日文原页）；封面是 `og:image` 与 fancybox 那两处的 FC2 存储原件地址 |
| JavArchive | 已套契约 | `sources/javarchive.py` | 搜索页加作品页两跳；`records()` 把搜索命中的每一条转存各交一份记录，某一条 404 或对不上就跳过 |
| Seesaa 的三个 Wiki | 已套契约 | `sources/seesaa.py` | 只由 `scrape_codes --profile seesaa` 或 `--sources sougouwiki,av_neme,av_name` 点名；两个认人 Wiki 共用 `NameWikiSource`；`rows()` 把一页作品表每行各读成一份记录；传输是 `WikiPages`（页缓存、本批限额、撞墙停网），不在 `scraping_access.SOURCES` 里；失败沿用 `budget`、`blocked`、`ambiguous`、`incomplete_search` 等分档，落进批处理的错误表与健康表，`budget` 与 403/429 决定本批停网 |

FC2 五站依次问、资料取齐即停、封面问到底的流程在 `LibraryMetadataProvider.fc2`，按
`metadata_routes.FC2_STAGE` 逐站调 `records()`；各站只管自己的取页与解析。

## FC2 作品资料与封面

FC2 不是 JAV：番号是卖家自己的投稿号，JAV 目录站拿它去查，要么没有，要么撞上别的片（r18.dev、AVBase、
JavBus 对 FC2 都给不出东西，javdb 收了一部分但配额紧）。所以库内采集（`peach.library_processing`）对 FC2 番号
只问下面五站，按顺序：

1. **发行方商品页** `adult.contents.fc2.com/article/<video_id>/`（`peach.sources.fc2`，不要凭据）。资料在
   `ld+json` 的 Product 里，一次请求给标题、说明、卖家、商品标签、时长、販売日和封面原图。
   要核 `sku`：站上的商品号会被复用给别的投稿。下架的商品页仍回 200，只是不再带 Product，这不算抓取失败。
2. **镜像站 fc2cmadb** `fc2cmadb.com/articles/<video_id>`（`peach.sources.fc2cmadb`，Laravel + Inertia，props 树在
   `application/json` 里）。它留着下架作品的同一批字段和原图。标题与标签由站方用户维护，按社区来源登记，取值进复核。
3. **FC2PPV-DB** `fc2ppv-db.com/ja/videos/<video_id>`（`peach.sources.fc2ppvdb`，Next.js 服务端渲染）。它是 FC2
   商品的元数据库：出演女優链到站内女优页，販売者链到卖家页（slug 与发行方用户页同名），另有販売日、
   タグ，以及「流出あり／なし」那枚标记（进 `extra['leaked']`）。时长只在 `<meta name="description">` 末尾。
   站上那张是 360×360 的 CloudFront 缩略图，不交封面。站上没有的商品回 200 的 404 页，`<h1>` 里没有番号，
   归 `not_found`。
4. **JAVten** `javten.com`（`peach.sources.javten`，前身 fc2hub.com）。作品地址
   `/video/<站内号>/id<video_id>/<标题>` 拼不出来，先问 `/search?kw=<video_id>`：单命中时站方直接跳作品页
   （Chrome 下跳到 `/tw/` 译文页，解析器认出后再取日文原页），多命中挑 `id<video_id>` 那一条。给日文标题、
   标签、卖家名与時長（description）、販売日（`videos:published_time`，与 FC2PPV-DB 的一致）。站上的中文是
   机器翻译（用户判定），只收日文原页，`og:url` 带 `/tw/`、`/en/`、`/ko/` 归 `parse_error`。
5. **JavArchive**（`peach.sources.javarchive`，不要凭据），前四站都说没有时才走到这里，细节见下文。

第 3、4 站要过 Cloudflare 验证，见下一小节。

### FC2PPV-DB 与 JAVten 的 Cloudflare 验证

这两站都在 Cloudflare 的 JS 验证后面。httpx 与模拟 Chrome 指纹的 curl_cffi 直连都回 403、标题
`Just a moment...`（`sources.base.challenge_page`）。只有浏览器过完验证发下的 `cf_clearance` 能进，而它绑着
解题那台浏览器的 User-Agent 与出口 IP，且只活 30 分钟。

所以这两站的页由本机浏览器打开（`peach.browser_transport`，ADR-0065）：

1. Peach 拉起用户机器上的 Chrome，没有才用 Edge（InPrivate）。独立 profile 放在 `peach-data/secrets/browser/`，
   窗口在屏幕外。
2. 导航到地址。落到验证页就等它自己过（一般 3～25 秒，不用人点）。自动阶段最多等
   `min(40 秒, 这条请求的 timeout)`。
3. 没过就把窗口顶到前面让人点一下，再等两分钟；这两分钟不受 timeout 约束。同一站弹过没点就不再弹。
4. 还没过，按 `blocked_pause` 整站冷却（15 分钟起翻倍到 6 小时），链照常往下走。
5. 过了就把最终地址、状态码与文档读回来。

其他规则：

- 不是验证页、却在 timeout 内没打开的页，按连接失败重试。
- 冷却记录带 `via`：直连 403 攒下的冷却在切到浏览器时作废，反之亦然，规则见 ADR-0065 第二条。
- FC2PPV-DB 第一次进站落年龄确认页（`/age-verify`），传输按 `SOURCES` 里的 `browser_gate` 替人点那颗按钮。
- 浏览器 10 分钟不用就自己退出。
- 这台机器没有 Chrome／Edge（`find_browser`）时走 HTTP：整站 UA（`peach.user_agent.USER_AGENT`）要与用户的
  Chrome 一致，「来源和凭证」里贴 Cookie，连接方式选与那台浏览器同一个出口。403 同样冷却，保存新 Cookie
  清冷却。JAVten 搜索一跳的 Location 是 `http://`，HTTP 层把同主机的明文跳转升回 https 再带 Cookie；浏览器
  路径里由浏览器自己跟跳。

### minnano-av 的间歇拦截

minnano-av 也在 Cloudflare 后面，但对 HTTP 客户端是间歇拦截：2026-10-01 同一出口上，检索页被拦、一分钟后
资料页照常回 200。所以它不像上面两站那样一开始就走浏览器，在 `scraping_access.SOURCES` 里登记的是
`browser_fallback`：

1. 先走 HTTP 客户端（`SourceTransport`，连接方式按「来源和凭证」里这一站的设置）。
2. 回 403、429 或验证页时，由本机浏览器取同一页验证（`peach.browser_transport`，与上面两站共用 profile 与进程）。
3. 浏览器取到 200 的正常页：这一站固定走浏览器，写进来源设置 `secrets/follow/scraping-minnano-av.json` 的
   `transport: browser`；HTTP 客户端攒下的冷却清掉。之后的请求不再先问 HTTP 客户端，删掉这个键才回到第一步。
4. 浏览器也没取到（没有浏览器、验证没过、同样被拦）：照 HTTP 客户端被拦那样冷却，15 分钟起翻倍到 6 小时。

补别名、补女优资料（`MinnanoPages`）与 `harvest_performer_links.py`、`harvest_agency_rosters.py`、
`resync_performer_agency.py` 都经 `SourceTransport` 取 minnano-av，共用同一份冷却与固定状态。2026-10-01 实测：
临时 profile 的 Chrome 取检索页，跟到资料页 `actress695633.html`，资料表解析正常。

候选备援（未接入）：OpenAver 0.16.13 拔掉 minnano-av 后按 xcity → 维基 → graphis 逐字段合并女优资料；只作 minnano-av
取不到时的补位，不替换它，因为它是目前唯一能从名字走到事务所名册的入口（调研见 ADR-0092）。

### JavArchive

- 作品地址里夹着站内文章号和标题（`/926949-FC2-PPV-4137487-…-pn.html`），拼不出来，所以先问
  `/search?q=<番号>` 再取作品页。商品号按数字边界比，`4137487` 不能命中 `41374870`。
- 搜索结果只有标题和一张缩略图，标签、发行日、时长和封面位只在作品页，所以这一跳省不得。
- 这一档给标题、封面，以及转存者在正文资料里填了的标签、発行日与时长。填不填由转存者决定，抽查多数整块空着。
  卖家、商品说明和演员栏站上没有；正文余下几段是转载来的网盘链接，一概不取。
- 同一个商品站上常有好几条，由不同转存者各发一次，文章号、番号写法、标题和图都不同，先后没有质量含义。
  搜索命中的每一条都取，第一条那张未必还在。地址里的标题有的已编码、有的是原字，拼之前统一编一遍。
- `robots.txt` 全站放行。
- 封面在 `img.javstore.net` 上，**认位置不认文件名**：`div.fisrst_sc` 里那张排前，schema.org 的 `image` 排后。
  转存者的命名不统一，按名字认的话多数一张都取不到。`_s.jpg` 结尾的排除，那是标着 `Preview` 的多帧长条拼图。
- 文件名不用来认，只用来否：名字里五到七位的独立数字段都不是本番号时，那是别的作品的图，
  `is_cross_product_cover` 按这条拦。
- 那里存的 GIF 多是预览动画。官方档的 `best_cover` 与社区档的 `picture()` 下完整张后一律不收，卡片封面只要静态图。
- 它的封面通常比官方原图差一档，所以排在最后。

### 封面

- 资料那一步答上就停，封面那一步把链问到底。给出地址的那一档常常下不来图：站上标着没有商品图，或者地址
  还在、FC2 存储上那张已经删了。这一层判不出来，要等 `best_cover` 量过才知道，所以封面要的是链上全部图源，
  由它择优；资料那步问过的档不再问第二遍，多问那一档顺带多一批标签（ADR-0030）。
- 官方页、镜像与 JAVten 的封面都指向 `storage*.contents.fc2.com` 上卖家自己传的那个文件，所以按官方图对待，
  不走社区来源的两图源印证（ADR-0030）。JAVten 那两处地址与发行方商品页是同一个文件，下架后可能已删，
  由封面层量过才知道。
- 镜像有时给的是 `contents-thumbnail*.fc2.com/w276/` 包装过的地址，解析器拆掉包装取原件。
- 镜像上没有商品图的条目挂的是它自己那张 `no-image.jpg` 占位件，还是站内相对地址，当封面交下去只会换来
  「来源连接未取得」，所以解析时就判成没有图。

### 演员

- 官方商品页和 JavArchive 都没有演员栏，标题里的名字是卖家写的宣传语。所以官方页答上、这一行还缺演员时，
  接着问 fc2cmadb 和 FC2PPV-DB 要这一栏（`FC2_CAST_SITES`）。
- **fc2cmadb 的女優栏**在浏览器里只对登录用户显示，采集带上「来源和凭证」里配的登录 Cookie；游客补问这一栏
  眼下也回。那一栏是 Inertia 的延迟 prop，首屏 HTML 里没有，要带上这一页自报的握手版本号点名 `actresses`
  单独再问一次。响应里另附一串曾用名，只取正名，否则一位女优摊进演员栏就成了十几个人。
- **FC2PPV-DB 的女优栏**按片中人整理、写日文原名，优先级排在 fc2cmadb 之后、javdb 之前。
- 演员栏上镜像排在 javdb 前面（`metadata_policy.FIELD_SOURCE_PRIORITY`）：javdb 那一侧常是转载站起的称呼
  （英文昵称或中文译名），镜像写的是原名。
- 一家写 `Xちゃん`、另一家写 `X` 算两家一致，按 `X` 自动落库，条件与留痕见 ADR-0086。
- 账本里已有的演员社区来源换不动。两边不一的由 `scripts/fc2_cast_review.py` 列进
  `generated/fc2-cast-candidates.csv`，在复核页「资料字段」里逐条批。
- 评论区那条线走 `scripts/fetch_fc2_metadata.py`，另有等价标记与合集判定。
- 带分段后缀的番号（`FC2-PPV-3312576-1`）认不出商品号，一处都不问：合集封面套给每一段，就是内容各不相同的
  每一段都顶着同一张图。
- freejavbt 不进这条链：它改版后 amane 的解析器认不出作品页，作品页上演员栏写的是「暫無女優資料」。

## 一本道作品资料与封面

无码番号在 r18.dev 上没有；目录站给日期式番号的发行日是转售商的上架日，和番号自己写的日期对不上。
一本道的前端读的就是一份公开 JSON，不用解析 HTML，也不要凭据：
`www.1pondo.tv/dyn/phpauto/movie_details/movie_id/<id>.json`。

- 一次请求给标题、说明、女优（日文与罗马字）、系列、发行日、时长和站内标签。解析器在 `peach.sources.onepondo`。
- 下架的作品直接 404，落到社区来源（javdb 收了一部分）。
- **问不问由本机证据决定。** 一本道与カリビアンコム 都按 `MMDDYY_nnn` 编号，番号本身分不出是谁家的；分隔符是
  压制组的文件名带进来的，不是片商标识。所以只有这一行的路径、文件名或账本厂牌里出现 `1pon`／`一本道` 时
  才问官网，否则照旧走社区来源。依据：本机カリビアンコム 番号在一本道全部 404，一本道番号全部命中。
- 封面只有站点自己那张 16:9 剧照（`str.jpg`，960×540）。无码片商不出封套，`thum_b.jpg` 是 120×197 的列表
  缩略图；两张都不是 JAV 那种竖版封面，取大的那张，按官方图对待。

## javdb、AVBase 与 JavBus 的限流与封禁

这节讲三个社区来源什么时候问、多快问、被封了怎么办。库内采集只在官方渠道落空时按番号问它们；资料与封面的
比对规则见 ADR-0030、ADR-0032。

### 库内采集的节奏

- javdb 的主机间隔由用户定：`library_processing.SOURCE_INTERVALS` 里 `javdb.com` 与 `jdbstatic.com` 都是 3 秒，
  两个主机要一起改。
- **FC2 的商品号在三家里只问 javdb**（`community_catalog.community_sources_for`）。本机来源证据里 AVBase 与 JavBus
  对 FC2 一份都没给过；javdb 能补约一半的演员与发行日，厂牌、标签、封面不给，所以它问得慢却砍不掉。
- 一轮采集的长短约等于 javdb 请求数乘这个间隔（实测相差约一成）。`HostLimiter` 等的是「距上次满 N 秒」，别家的
  往返落在这个窗口里被吸收，所以跳过两家省的是配额和撞 Cloudflare 的次数，不是时间；改成并行也省不出时间。

### 撞上 403 或验证页之后

- javdb、AVBase 与 JavBus 回 403 或验证页就整源停下。AVBase 回的是 Cloudflare 验证页；JavBus 那道 403 的原因
  未取得。
- 停多久按次数翻倍：第一次停 `scraping_access.FIRST_BLOCKED_PAUSE`（15 分钟），连着再撞才翻倍，上限是
  `SOURCES` 里的 `blocked_pause`（javdb 24 小时，AVBase 与 JavBus 6 小时）。通了一趟就清掉记录，重新起算。
- **上限不能当首停时长。** 实际封期常常短得多：javdb 记下 24 小时冷却的那次，不到 7 小时就全回 200，而那一轮
  的失败全部写着「来源正在冷却」。
- 只剩状态码可看的 403 不当登录墙报：它可能是凭据失效，也可能是出口 IP 被封（javdb 那次封了 3～7 日，换 Cookie 没用）。
  `metadata.auth_wall_reason` 的措辞因此只说两种可能，不劝人换凭据；带着登录页地址或正文的才直说换凭据。
- 冷却期抛的是 `SourcePaused` 而不是 `NotFound`，所以「7 天内不再问」的记忆（`library-metadata-misses.json`）
  一条都不记，下一轮从头再问同样这批。
- 问题清单把这类项记成「本趟没轮到」（`severity: paused`）。自写来源带 `cooldown_action` 的失败（验证页、
  封禁、地区限制、限流）也归这一档。一档里出错的几家全在冷却才算没轮到；有一家是别的原因，就是「未取得」。

### javdb 的 403 是按出口 IP 计的配额

javdb 判的是「这个出口发得太快」，不是「你是不是机器人」。所以**不给它换 curl_cffi 指纹**。

- 依据：不在冷却期时，HTTPX 与 curl_cffi 的 `chrome136` 指纹、带不带 Cookie 四种组合取搜索页、详情页和资料页
  全部 200，没有挑战页也没有登录页，差别只在界面语言（带 Cookie 回简体，不带回繁体）。指纹识别会在第一次请求就拒；
  而一整轮几百次请求能跑完、打到一定量才被封，是按出口 IP 计配额的形状。
- **未取得**：封期之内两种 transport 的对照。要取得就得先把出口 IP 打进封锁，这一条不做，所以「被封之后换
  指纹能不能立刻通」仍然未知。脚本与原始结果在仓库外的 `<私有验证记录>`；
  对照起因见 [docs/reference-snapshots/amane-crawlers-poc.md](reference-snapshots/amane-crawlers-poc.md)。

**配额和机器人判定是两件事，处理不同。** Cloudflare 与验证墙判的是「你是不是机器人」，绕过它要伪装成另一种
客户端，一律放弃。javdb 这道判的是出口速率，换节点只是换一条线路重新计配额，没有伪装，所以换出口可以。
但配额本身照守：来源下限不许压，撞 403 仍然整个来源收工，不在封禁期里换着节点连打，那等于拿多个出口凑一个
超速批次。

- 脚本允许经环境代理出网：`Site` 的 `via_proxy` 让 HTTPX 读环境代理，不保证读取系统代理或 PAC。应用路由与
  实际出口要分开验证，新用户的配置边界见 ADR-0024。

### 目录采集对 javdb 的限速

`scripts/harvest_directory_links.py` 按名字逐人问 javdb（见「目录型来源与社媒链接」），节奏比库内采集更保守。

- 实际被封过一次：约一秒一页连取一百多页后，整站每条路径都回 403，页面写「基於你的異常行為」并建议换节点，
  出口 IP 被封了 3～7 日。封之前不发 `Retry-After`、不降速、不给验证码，中途停顿几分钟也没换回额度；额度按出口
  IP 累计、不按路径，触发点在每分钟 40～50 页这个量级。
- 所以限速是硬编码的来源下限，不是命令行默认值：`harvest_directory_links.SOURCE_INTERVAL` 给 javdb 定 5.0 秒
  （每分钟 12 页），`--interval` 只能往上加、压不过它。
- **撞上 403／429／503 就整个来源收工。** `Site.request()` 对状态码不重试；封了之后接着翻，只会让每位女优的
  每个名字写法各撞一次 403。`rate_limited()` 命中即 `break`，已取到的页照常进判定，不丢这一轮的成果。
- 已取的页缓存在 `state/directory-links/javdb/`，判定可以离线重放。封禁期过后调大 `--limit` 接着跑，缓存命中
  不花请求，不需要断点参数。

### 封面失败的几种原因与年龄门

- 官方各版本只回「准备中」占位图：作品多半已下架（MIDE-594）。
- 两个图源各有图但 dHash 对不上：「不是同一张图」，不用。
- 社区来源的图只出自一个图源时照样装上，`.scraping.json` 的 `verified_by` 为空即未经印证（IPX-060 只有 javdb）。
- JavBus 有年龄门，番号页不带 Cookie 回答题式年龄验证页；javdb 有登录墙。两家的 Cookie 由用户在浏览器里过门
  或登录后贴进采集设置，公开采集随请求带上。
- `sources/metadata/javinizer-go/` 下的 JavBus、javdb 旧快照只借厂牌选官方渠道，封面不当官方候选：JavBus 搜不到
  原番号时返回的是别的作品。

## 本机采集入口

这节讲 GUI 与命令行取封面的共用入口，以及它的连接、Cookie 和写入规则。

GUI 与 `scripts/fetch_jav_covers.py` 共用 `peach.jav_cover_fetch`。不需要把开发者的映射文件或 Cookie 复制到
新用户电脑。已有成功的元数据快照优先，缺快照时公开来源可以联网查询。

- **连接方式**：R18、DMM、Prestige、MGStage 的封面 HTTP 路径按来源使用系统代理、应用直连或自定义代理。
  DMM 连接检查分别报告页面与高清 CDN。HTTPX 的环境代理不等于系统 PAC；应用直连也不能排除 TUN。
- **Cookie**：收 Cookie 的来源（JavDB、JavBus、FC2CMADB、Instagram）的粘贴与 Netscape 文件导入复用
  CredentialStore，只保存当前来源域内未过期的项目。保存本身不校验登录会话是否有效。
- **单次任务上限**：GUI 封面任务每次只接受馆藏命中的一个番号，复用 BackgroundJob；最多 80 个请求、32 MiB，
  请求发出前检查 180 秒截止时间。
- **替换规则**：只在完整图与探测尺寸相同、能完整解码且面积更大时原子替换。原始字节不降采样。成功边车记录
  原图与安装摘要，24 小时内摘要匹配就不重复下载。
- **失败与重启**：429 的 Retry-After 冷却写进本机文件，新建的 transport 也遵守；失败不删除已有封面。程序重启
  不自动重放写入任务，未完成的由用户重新发起。
- 即时 r18 厂牌证据与本机快照用同一套来源路由，Prestige 与 MGS 都参加候选比较；原始图片没有派生降采样。

这套连接配置覆盖封面 HTTP 与 FC2 CLI。amane 桥子进程、其他采集脚本与 curl_cffi 连接器各用自己的配置。
Instagram 的独立用户登录会话未取得，自动适配器不进正式依赖。

测试步骤见 [Windows 测试版](TESTING_DESKTOP.md)，架构要求见 [ADR-0024](adr/0024-mark-manifest-not-bundled-bytes.md)。

## 无番号视频的联网识别

`scripts/scrape_codes.py` 只认番号。没有番号的视频（推特、Telegram 来源的资源等）走
`scripts/identify_resources.py` 的两段式流程，识别结果同样进 `/review` 的「资料字段」，确认后才写入数据库中的对应资料项：

1. `worklist` 只读账本，按文件名生成搜索写法：原样名、摘掉推广头尾、分隔符换空格、无空格英文按大小写拆词、
   剥掉尾部画质标签。同时标出同目录的配套图片，可以直接当海报。`metadata_hits` 数出创作者、标题、厂牌、
   系列、演员里已有几项，默认把少的排前面；`--sparse-only` 只留一项都没有的条目，识别优先做这些。
2. 智能体或人联网核对后，按 `asset_id,field,value,source_url,confidence,note` 填回 CSV。
3. `ingest` 合并进 `generated/library-metadata-field-candidates.csv`，候选来源记 `websearch`，`asset_path` 钉住
   具体文件。

- 字段限于 title、original_title、performers、studio、series、release_date。
- 有番号的视频仍走 `scrape_codes.py` 的 JAV 来源。
- 网页搜索在智能体一侧执行，脚本不发请求、不写账本。

## 命名与身份合并

这节讲规范名怎么选、别名怎么登记、同一个人存成两条时怎么合并。

### 规范名与别名

- 规范名优先用有出处的简体中文通行名，暂无可靠中译时保留日文。旧艺名、罗马字、假名和繁体名降为别名。
  `no_avatar` 只表示没取得合格图片，不阻止已核实的姓名落库。
- 「这一页只有一位女优」不构成证据：库里大量番号是 BEST 合集，搜索无结果的页面也会渲染推荐文章。精确回配
  命中优先于任何「唯一」推断，「唯一」要两个番号同证才作数。
- r18.dev 的罗马字字段是「现用名 (曾用名, 曾用名)」这种渲染格式，一个字段装着一个人的几个艺名；假名与汉字
  写法各自成行，罗马字只有这一份。落库时按 `peach.entities.split_composite_person_name` 拆开，签名是「括号前
  有空格、括号内逗号分隔、两侧都是罗马字」。同一个字面形状在账本里还用于厂牌消歧（`AV DEBUT（本物人妻）`）、
  角色出处（`アスナ(SAO)`）、接稿状态和去重后缀，只能靠这条签名收窄。
- `XX XX` 是来源节点文字重复，不是合法别名。person 名进入 CSV、兼容字段或 `upsert_asset_entity` 之前先收敛
  完整重复串；清理时同时审计 `asset.creator`、`演员:` 标签和 `entity_alias`。
- 判断「账本已经有这个名字」要连罗马字一起看，不能用 `peach.entities.name_chain`。那条链按设计剔掉罗马字
  （拿罗马字去日文站查是白跑），拿它当「已有」判据会把 `entity_alias` 里明摆着的 `Rin Natsuki` 再报一遍新别名。
  要全量就直接读 `canonical_name` 加 `entity_alias`。
- 上游名字里的零宽字符在 `canonicalize_entity_name` 一处剥掉，不在各脚本里各修一遍。`str.strip()` 不认它们是
  空白，`normalized_name` 就会带着一个看不见的字符：界面上和普通名字一模一样，`upsert_asset_entity` 却按
  `normalized_name` 找不到已有实体，同一个人存成两条，按名字搜也搜不到。剥 U+200B／U+200C／U+2060／U+FEFF；
  **U+200D 不剥**，emoji 的家庭与职业序列靠它连字，剥掉会把创作者名字里的一个字形拆成两三个。

别名按来源分三类，界面上只有用户自己敲的那一类可撤销（`user:alias`，写入端点 `/api/entity-alias`）：

- 刮削（`r18:performer` 等）和合并（`merge:*`、`avdb-actor-mapping@<rev>`）留下的是这条实体为什么长这样的
  记录，不给一次点击删掉。
- 自由文本保存到别名表；`canonical_name` 是页面显示的人物或厂牌名称，只能从该档案已有的名字中选择
  （`/api/entity-name`）。
- 头像图库按整条名字链逐个查、取并集，所以少一行别名就少一批候选。同一个人常按好几种写法各存一批，命中即停
  会让排在后面那几个名下的图整批出不来。

### 合并实体

- `entity(kind, normalized_name)` 的唯一约束冲突通常不是 bug，而是同一人新旧艺名的信号。合并走
  `peach.entities.merge_entity`：保留作品多的一侧，迁移关系、别名、外部引用、链接和搜索词，旧称全留作别名。
- 被并入的 id 在 `entity_redirect` 留一条墓碑指向保留的一侧，链式合并写入时压平成一跳；关注源、Feed 订阅与
  发现关联随合并改指。按 id 取数的入口（`/entity-image`、换头像、Feed 发现、复核候选）经
  `entities.resolve_entity_id` 先认活实体、再认墓碑；目标实体被删时触发器删掉指向它的墓碑。
- `entity_external_ref` 每个 provider 只留一条，同源的第二条被丢弃并报告，不静默覆盖。
- creator 与 performer 跨类重复不用「作品多的一侧」规则。只有两边非空作品集合完全相同，并且 performer 别名
  精确命中 creator 名、或 creator 名由 performer 本名与账号别名组成时，才自动归并。
- `r18:performer`／`javbus:performer` 是正式发行的出演元数据，保留 performer；通用 `performer` 是压平后的兼容
  断言，保留 creator。合并要同步 `asset.creator` 与 `演员:` 投影，否则已删的实体仍会在详情页伪造链接。
- `merge_entity` 的两个陷阱：sqlite 连接默认 `foreign_keys=OFF`，子表行必须在函数内显式 DELETE；计数用
  `SELECT changes()`，不用连接累计的 `total_changes`。合并不可逆，合并后 `PRAGMA foreign_key_check` 应为 0。

### 女优别名与资料的自动登记

- **补别名后继**（`peach.performer_alias_followup`，ADR-0055）自动登记女优的其他艺名。`source` 是批次号
  `auto:performer-alias@<任务行 id>`，`revert_auto_landing.py --source auto:performer-alias` 整批撤回。判词写进
  `generated/performer-alias-landing.csv`，被别的实体占用的写法只记不写。
  - 只读 minnano-av 资料表的「別名」行，与 av_neme 人物页「プロフィール」一节的名字栏。
  - minnano-av 检索页上同一人的几个别名各占一行，判唯一按编号去重；唯一命中时站点直接跳到资料页，编号看页头
    canonical。
  - av_neme 的系列页、月份页也写「名前(女優名)」，页名等于主名才算人物页。改过名的旧页只剩一句
    「女優名が【甲】から【乙】へ変更」，第一节不是「プロフィール」，不读。
- **有 FC2 作品的女优先问 fc2cmadb 女优栏**（ADR-0061）：从她自己的作品页进到站上那位人物，只收站上主名；
  曾用名串里混着卖家商品名，不收。卖家称呼当规范名的实体（`たぬき顔サラサラ黒髪ロング`）靠这一站接上真名。
- GirlsDelta（`girlsdelta.com/model/<id>`）有名录与宣传照，但比脸能确认的太少，只当单人佐证链接，不进流水线。
- **补女优资料后继**（`peach.performer_profile_followup`，ADR-0067）把资料写进 `performer_profile`，批次号
  `auto:performer-profile@<任务行 id>`，`revert_auto_landing.py --source auto:performer-profile` 撤回。判词在
  `generated/performer-profile-landing.csv`。
  - minnano-av 资料页 `actress<编号>.html`：表的每格以 `<span>标签</span>` 起头、值到 `</td>`；只读
    `act-profile` 那一块，评论区的同名格子不算。
  - `生年月日` 那格的 `<p>` 里夹着一个多余的 `</td>`，值截到那里正好不带星座后的杂项。
  - `サイズ` 形如 `T156 / B86( Eカップ ) / W58 / H85 / S`，数字就是厘米；末尾那个 `S` 含义未取得，只留原文。
    资料没填的页没有生年月日、血液型、出身地那几行。
  - `ブログ` 那格显示 `http://`、`href` 是 `https://`，取 `href`。站内头像 `/p_actress_125_125/` 只有 125×125。
  - avwikidb.com 直连 httpx 带 Chrome UA 就回整页（登记在 `scraping_access.SOURCES`，拒绝访问停 6 小时）。作品页
    `/work/<番号>/` 头里的 JSON-LD `Movie.actor` 每项给 `name`、罗马字 `alternateName` 与 `/actor/<编号>/`，编号
    从这里取。女优页 JSON-LD `Person.alternateName` 前两项是读音与罗马字，后面混着站上归并的其他名义（含男优
    名），不收。三围只在正文「身長・スリーサイズ」一格（`T148 B83(B) W55 H85`）。女优图是 DMM `actjpgs` 的
    125×125，不作头像；站内搜索是 `⌘K` 弹层，地址形态未取得。
  - 出生日期与身高和 minnano-av 不一致时，只记进外部编号的 `metadata_json.conflicts`。

## 番号目录、创作者与水印

这节讲怎样从文件名和目录里认出番号、怎样确认来源返回的是同一部片，以及谁算创作者。

### 从文件名提取番号

- 番号目录被投影成创作者时，判据只能是文件级证据，不能看名字形态：唯一可靠的区分是目录内媒体文件名是否
  解析出同一个番号（`scripts/audit_code_creators.py`），存疑一律留复核 CSV。
- 番号只补给目录里的视频。发行目录里还混着论坛文宣、下载器广告和封面图（`Tokyo-Hot n0780-HD` 里有两张）。
  番号写到它们头上有两个后果：库里它们冒充这部片的文件；垃圾复核又因为「自己带真番号」判定目录证据不成立，
  把它们挡在队列外。写入走 `field_owners` 署名 `script:code-creators`，用户改过的格子不再被覆盖。
- 发行目录里的推广短片由 `scripts/find_ads.py` 按五条判据记分、只出 CSV 交人处置；javm 的 `find_ad_videos` 按关键词识别，可作对照（ADR-0092）。
- 画质前缀（`HD`／`FHD`／`4K`／`1080P`）和版本后缀（`-C`／`-CH`／`-UC`／`-SUB`）不是番号的一部分，提取器先剥
  这两层再匹配。界面把版本语义投影成「中字」「无码」「无码破解」，原始 `name`／`code` 留给文件操作。缺连字符
  的紧凑 code 只有同时具备片商、发行日或 performer／studio／series 实体证据才恢复。
- 推广域名会出现在番号的头、尾和方括号里（`www.98t.la@ABW-358-U`、`ABP-762-fuckbe.com`、`[xxx.cc]ABC-123`）。
  番号提取与目录判重共用 `strip_promo_markers` 这一层；各剥一半，叠了两层的 `[98t.tv][98t.tv]ABW-251` 就会在
  其中一处漏网。
- 只说明「这是什么文件」的词（`IMG`、`VID`、`VIDEO`、`NO`、`PART`）与番号主体同形，集中在
  `catalog_rules.CODE_BODY_STOPWORDS`；画质词归 `_QUALITY_HEAD`，转载站标识归 `REPOST_SITE_LABELS`。三份名单
  各管一类，提取时依次过一遍。新增条目先用本机临时工具 `build/parse_shapes_audit.py`（不随仓库分发）的
  `stems` 核对误判证据。创作者昵称也可能撞这个形态，逐个塞进名单只会得到
  一张不收敛的表。

### 日期式、Tokyo-Hot 与西片的编号

- 素人系日期式番号（`MMDDYY_NNN`／`MMDDYY-NNN`）的分隔符是片商标识，属于身份：一本道、パコパコママ、
  カリビアンコムPR 用 `_`，カリビアンコム 用 `-`，同一天同一序号是两部不同影片。JavDB 自己也分开保存（仓库外
  `<私有验证记录>`：搜 `092415-001`，首位返回的是一本道的 `092415_001`）。
- 所以 `catalog_rules` 的归一化、身份比对和查询变体一律原样保留分隔符，也不生成另一种写法的变体：用错分隔符
  搜到的是别的片。只有来源给出不带分隔符的纯数字串时两种才都算命中，缺一个字符不是反证。
- 野生文件名的写法会漂移（同一部一本道既有 `1pon-092415-001-fhd1`，也有 `1pondo-092415_001-FHD`），所以剥番号
  显示标题时两种分隔符都放行，落进 `code` 的值保留文件名给出的那一个。
- 日期式番号的六位是 `MMDDYY`，月日必须成立才算这一形态。只看位数的话，手机录像
  `VID_20220818_125735_816.mp4` 里的 `125735_816`（12 月 57 日）就成了一条一本道番号。
- Tokyo-Hot 的编号没有厂牌字母段，规范写法是小写：本编 `n1234`／`k1234` 补零到四位，Red Hot 支线写成
  `red-123`。javbus 的作品页地址就是 `/n1234`，参考实现 NeoAVDC（MIT）的 `TOKYOHOT_NUM_RE` 同样输出小写
  `n####`。`k` 与 `red` 两支在本机账本里一条没有，它们在 javdb／javbus 上的写法**未取得**实证。编号只有一个字母，
  形态挡不住 `no0037_01` 这类名字，所以只在两个位置认它：名字开头，或名字里已经写着 `tokyo-hot`。
- 西片按「厂牌／系列 + 发行日」命名（`DorcelClub.24.12.02.Christy.White.XXX.1080p`），这是身份不是番号：
  `catalog_rules.western_release_identity` 给出 `DORCELCLUB.2024-12-02`，`release_identity` 认它，番号提取一律
  返回空。两位年份按 70 分界展开，四位年份照原样。只在 token 开头认，不在 token 中段搜：账本里的
  `E078. Redhead.Sucking.Big.Cock.And.Hard.Sex.2019.10.15` 中段搜会得到系列名 `Sex`。

### 确认来源返回的是同一部片

- 来源返回的番号必须和查询的番号比对过才算命中。javbus 一侧是拿番号做关键词搜索取首个结果，搜不到就返回
  近似的别人（`SA-104 → AVSA-104`、`AR-301 → STAR-3016`）。不比对时大部分匹配番号根本不对，整个 MIB 目录
  （韩国内容）曾因此被写上日本厂牌、系列和标题，还靠这些假证据升级成 JAV。
- 判据是 `catalog_rules.same_release_code()`。它只归一已核验的前缀别名、DMM 的 `h_` 标记、补零和重制尾字母
  这些良性差异；来源没给 id 的不拦（缺证据不是反证）。复核队列按同一条判据剔候选（ADR-0035）：候选全被剔掉
  的行整行不进队列，点了也写不进去的东西摆在那儿只是要人再认一遍。
- javbus 是备选来源（`metadata_policy.FALLBACK_SOURCES`）：同一个字段上还有别家可用时，它的取值不当证据；只有
  它一家时才轮到它。这一步排在番号日期判据之后。剔完之后来源全是官方的字段，自动写入可以替换账本已有的值，
  规则名记 `adr-0035-official-replaces-*`；community 来源仍然只补空（ADR-0035）。
- 韩国 MIB 的编号（`catalog_rules.KOREAN_MIB_PREFIXES`）资料与封面都不问 JAV 来源。`HA-101`、`MY-102` 撞上番号
  完全相同的日本作品，番号核验照样通过，只能按编号前缀整体不问。刮削、扫描与采集、页面取封面、封面批次和
  按日志恢复五个入口都按 `is_korean_mib_code()` 拦。封面的跨作品判据同时比字母段和数字段：`YUJ-101 → yuj00011`、
  `435MFC-135 → h_1711mfcc00027` 字母对上、数字不对，都是别的片。
- `catalog_rules.code_query_variants()` 只扩展搜索词，每次返回都以账本原始编号校验，缓存和网络结果同样受检。

前缀等价表与查询表是两张表：

- 已核验的 LUXU、BAZX、HA 与 9 个 MGStage 前缀（`_RELEASE_PREFIX_ALIASES`）可以归一，其他数字前缀保留。
- 登记一行要逐组取证：MGStage 商品页对 r18dev 快照比标题、时长、出演与封面；avbase 把两店条目归为同一作品；
  账本里该字母段只挂一个厂牌。
- MGStage 官方商品详情路径里的完整编号可以佐证它省略前缀的展示 id；封面、标题和搜索 URL 不作身份依据。
- DMM／r18dev 只给 `content_id` 时，其中的厂牌段可以用来核对裸番号，但不能抹掉查询里的 MGStage 前缀。
- `390JAC-040` 是 MGStage 配信，`JAC-040`／DMM `118jac040` 是另一部 DVD 合集，JNT 同样不得按裸编号合并。
- 无法证实的变体返回 `identity_mismatch`，保留原始快照，但不生成字段候选。

### 谁是创作者

- 创作者是频道主，不是出镜者。文件名里可以建创作者的只有 `RT_@X - 正文…`、明确标注的 `女主@X` 和正文里的
  中文名；末尾成串的裸 `@A @B @C` 是互推，`📷：@X` 是摄影师，都不建。
- **发行平台既不是厂牌也不是创作者。** FC2、myfans 这类是卖东西的地方，站上有实际卖主（出品者）的那个账号才是
  creator。平台本身只能当来源／平台实体，链接按 `catalog` 登记，不给它找「厂牌官网」：
  `studio_sites.PLATFORM_ENTITIES` 直接判「不适用（发行平台）」，一个请求都不发，也不静默跳过。
- 账本里有些 FC2 作品标着女优、有些评论里提到人，那是 **performer** 身份，不能顺手把平台记成创作者。一旦记了，
  这个平台下所有卖主的作品都会挂到同一个「创作者」名下，和给聚合目录打统一标签是同一类事故。
- 转载渠道水印不是创作者水印，目录名也可能是伪装。判定优先级：画面水印 > 作品名联网反查 > 文件名文本。
- 打创作者级标签之前，先按 ledger 路径的下级目录分布确认这个 creator 不是聚合目录。给聚合目录打统一风格标签
  就是 `asce` 事故的重演。
- **轮换出演者的发行命名是厂牌或系列。** 同一发行名称可能按编号轮换出演者，不能仅凭目录名认作个人账号。`classification.numbered_cast_shape` 认三种命名
  （前缀+编号+人名、出演者组合-编号-标题、驼峰人名连写加编号），同一账号下至少 3 部、过半文件命中、出演者至少
  3 位才报；前缀形态的人名须有 3 位是账本已有的出演者，同一人出现在八成以上命中文件里的是个人或情侣账号。
  归属审计把它报成 `label_shape`，只进复核，不生成修复计划。
- **身份调研先按页名查日本资料站。** 通用搜索不收 sougouwiki、av_neme 这类站，只搜通用引擎得出的「未取得」
  不算查过。`research_creator_identity_wikis.py` 只读账本，对身份待核验的创作者按规范名、去掉番号前缀的名字和
  文件名前缀，用 EUC-JP 页名直取两站（取页层是补别名后继的 `WikiSitePages`，页缓存、404 记忆与来源冷却与服务共用）。
  同名页是 3 位以上出演者的作品一览就记 `identity=release` 的 observed 并否掉 `unknown`；都不存在时 unknown 候选写明
  问过哪几页；没有页以外的取页失败是未取得，不进清单。清单交 `apply_entity_identity_research.py` 冻结预览、备份写入。
  处理任务结束时「查创作者身份」后继按同一判据自动问新登记和存量的创作者，命中直接落批次来源的 observed（ADR-0098）。
  改实体类型仍是用户复核。裏DVD村（uradvd-mura.com）是带会员登录的 DVD／下载商店，没有按页名寻址的系列页，
  未接入，只作人工查证线索。

## 女优名字与头像来源

这节讲头像从哪里取、怎么挑、怎么换，以及各个女优资料站能给什么。

### 取源方向与尺寸门槛

- 头像去精心整理的图库取；Logo 反过来只认品牌自己，官网与厂牌自有社交账号才是权威来源。
- 候选按实测像素判定（`peach.images.classify`）：短边 < 128 拒绝；头像另按长边 ≥ 500、短边 ≥ 300 判，竖构图
  人像套用方图门槛会拒掉最好的来源。只有 URL、没有实测尺寸的不算候选。
- **「长边 ≥ 500」是源头门槛，不是显示门槛。** 它拦的是缩略图级来源。拿它当显示门槛，会把一张脸宽 208px 的
  382×382 挡在门外，让在位那张脸只有 55px 的封面裁片继续占着位子。显示侧的尺寸约定见 [docs/FRONTEND.md](FRONTEND.md)。
- 可用来源：r18.dev、av-wiki.net、Gfriends。javlibrary、missav、xslist 被 Cloudflare 拦，njav 有验证墙，jav321 没有
  独立的女优字段。被 Cloudflare 拦的站一律放弃，不绕过机器人检测。javdb.com 抓得到，但按出口 IP 限速，见
  「javdb、AVBase 与 JavBus 的限流与封禁」。
- Gfriends 只按 `Filetree.json` 和单张 raw 媒体当外部 Provider 用，不克隆图库，不把图片放进 Git。索引缓存按
  mtime 计龄（一天），取不到新索引就用旧缓存并在输出里告警；那一轮的「未收录」记 error 不记 no_match，否则
  `--resume` 会把一次网络失败固化成永久答案。Gfriends 每人中位 1 张、缓存图中位 500×600，不按作品分组。

### 审计与补缺口

- `audit_performer_portraits.py` 把合格图放进候选专用的内容寻址缓存，每条另存 provider、名字命中档、上游
  ID/URL、尺寸、MIME、SHA-256 与 policy version。与当前头像字节相同的只留审计证据，不进 `/review`。这个脚本
  没有写 ledger 的路径。
- `fill_portrait_gaps.py` 走完从缺口到装上的这一段：没装过头像的人里，名字链在图库只命中一张的直接装上；
  命中好几张的一张都不装，改产一份对照表（`--sheet`），候选图并排摆着，旁边是她在这个库里的作品链接。装图
  复用挑图弹层那三步（`avatar_picker.choices`／`resolve`／`install`），证据、缓存与取景 sidecar 的口径和手工换图
  完全一致。默认 dry-run，`--apply` 才写头像文件；账本按只读打开，这一趟不写 ledger。
- **图库按名字存图，同名的是不同的人。** `ななみ` 这种单名命中的二十多张是二十多个人，自动挑等于随机给她安一张
  别人的脸，所以「只找出一张」是唯一敢自动装的判据。单名即使只命中一张，证据也只有「键完全相同」这一条，
  装上后仍要在资料页上认一眼；对着作品认人这件事机器做不了。
- **缺口的判据是「盘上没有 `performer-<id>.img`」，所以一张差图会把更好的源永久挡在门外。** 例：社媒采集把作品
  封面裁片装进了一批空槽位，这些人从此不算缺口，而 Gfriends 里有其中一部分的高清正脸照。缺口审计答不了
  「在位那张够不够好」，换源要另起一轮普查：按 provenance 的 `provider` 认出封面
  裁片，按 sidecar 认出检不出脸、脸太小、源图太小，再逐个问一次图库。

### 挑图：按脸的宽度，不按画布

头像最终落在 64–160 px 的圆框里，认不认得出是谁取决于那张脸有多少像素，与整张图多大无关。

- 判据在 `harvest_social_avatars.rank_key`，脸宽由 `peach.face_detect` 的 YuNet 量（同 SHA 只量一次），写进候选
  CSV 的 `face_width` 列供复核的人直接看。
- 反例：一张全身站姿照画布更大，脸却只有几十像素；同一个人的半身照画布小一些，脸宽大一倍多。按画布挑，赢的是
  看不清脸的那张。合照里那张脸同样可能远小于单人照。
- 检不出脸记 0，排在任何量得到的候选之后；检不出脸的一律不装，官方图也一样。整批都是 0（模型缺席、清一色
  侧脸）时按画布排，并在运行统计里说明本轮是按画布挑的。单人作品封面这条退路也用同一把尺：JAV 双联封面
  右半幅剧照里的脸常常只有几十像素。
- 换图只在赢家那张脸更宽时才动手。填不满圆框的赢家，只在在位那张检不出脸时当保底装上。
- **头像目录由几条管线共用，`--apply` 只往好里换。** `--force` 的意思是「已有头像也参加竞选」，它覆盖的却是
  **所有**已装头像，包括别的管线装的；而 `harvest_social_avatars.py` 的候选池（X 头像、名录人像、作品封面）比
  `verified-photo-page` 那类整版人像差一大截，不加判断地覆盖会让多数头像的脸变小。所以写盘前先按同一把
  `rank_key` 量一次盘上那张，脸没有更大就不覆盖，运行统计里报「在位的更好未覆盖」多少张。在位那张的脸宽
  优先读现成的取景 sidecar，没有才真检一遍。
- **换图必须换 sidecar。** 取景 sidecar 与选图判据来自同一次 YuNet 检出（`peach.avatar_face`），装头像时一并写出；
  给不出新记录就把旧的删掉。留着上一张图的脸框，页面会拿它给这一张取景、放大到一个空位置上，而这在界面上与
  「这张图本来就该这么显示」看不出区别。`/review` 的批准落地也检一次脸。`scripts/detect_avatar_faces.py` 只是
  补齐入口，检测与 sidecar 形状都不由它定义。

### 去水印

抓回来的头像带别人的水印（底部那条 `PRIVATE.com`、`TEAMSKEET.COM`），要在字节里去掉，不能靠取景遮住：
导出、换取景规则或任何改用整图的地方都会把它带出来。

1. `scripts/scrub_avatar_watermarks.py` 默认只看不写，产出一份候选 CSV 加一叠左右对照的标注图。
2. 检出器抓不到半透明水印，人工补框是正路：往 `--marks` 的 CSV 里补一行 `file,x,y,w,h`。人工框不受分数、尺寸
   和位置先验约束，那些判据是用来质疑检出器的，不该推翻已经看过图的判断。一个整条边框的人工框常能把几处水印
   一次裁掉。
3. 人确认后再 `--apply`：原图连三个边车整套搬进 `avatars-superseded/`，provenance 补一段 `watermark_scrubbed`，
   取景 sidecar 按新图重算。

- **能裁边就不 inpaint。** 裁切一个像素都不伪造，inpaint 会，而且常常只抹掉一半、留下半透明的残字。裁切线受
  两条约束：不许切进人脸框加留白（脸框取自 `peach.face_detect`），不许把图裁得只剩 70% 面积以下。逐边独立判定，
  一条边裁不动不影响别的边。
- **一张图检出超过 4 处文字就整张放过，一个像素都不动。** 那不是带水印的头像，是作品封面被当成头像装了进去，
  要的是换源，见上一小节的封面裁片。
- 检出与移除的判据、模型来源和实测数字见 REUSE.md「头像水印检出」。

### 各站能给什么

- **DMM 女优一览页可达，但头像只有 125×125，只适合身份绑定的首次头像。**
  `https://www.dmm.co.jp/mono/dvd/-/actress/=/keyword=<假名行>/` 经 Peach 的 `dmm` 来源设置回完整列表页（带站内
  actress id 与括号里的旧艺名），没有区域拦截；`pics.dmm.co.jp/mono/actjpgs/<罗马字>.jpg` 不带 Referer 也回 200。
  - 问题在尺寸：`actjpgs/<名>.jpg` 是 125×125、`actjpgs/medium/<名>.jpg` 是 100×100，过不了高清候选门槛和显示门槛。
    这批图 Gfriends 已经整批收着，排在质量档位的最后一档，所以不为它另起高清 provider。取证与重放脚本在仓库外的
    `<私有验证记录>`。
  - 例外：新导入时，r18.dev 同一人物对象直接给 DMM id、名字和文件名，缺头像的实体可以先装这张官方缩略图，
    随后由换头像页升级。页面上的日文名、旧艺名和站内 ID 继续用于名字链与消歧。
- **javdatabase 的入口必须是账本里的番号，不能按名字拼 slug。** 它一个艺名一页，slug 与人不是一对一：
  `/idols/rin-natsuki/` 打开的是 `Rin Oka` 的资料页；站内搜索也不给 idol 页，只回作品列表。
  - 查询顺序固定为 番号 → `/movies/<code>/` → 页面给出的 idol 链接 → 名字，每一步都由上一步的页面给出
    （`scripts/harvest_javdatabase_names.py`）。
  - 一部作品可以挂多位女优，番号对上不等于整页名字都属于这个人：idol 页的名字里至少有一个已在账本这个人的
    名字链上才收，对上账本多个人时记「需人工消歧」。
  - 它能给日文原名加旧艺名的罗马字。厂牌名不能用它，它自己就把 `セレブの友` 写成 `Celeb no Tomo`。

## 目录型来源与社媒链接

补女优社媒走「目录型来源整站抓一遍、离线比名、复核 CSV 装入」，不逐人搜索（`scripts/harvest_directory_links.py`）。
这节讲各目录站怎么抓、名字怎么对、链接怎么判、事务所怎么建。

### 抓取与比名

- laoshi.ink 按 sitemap 抓全部女优页（ld+json `sameAs` 加正文外链）；bstar-pro.com 过一次年龄门，抓 models 列表与
  每一页。HTML 按 URL sha1 缓存在 `peach-data/state/directory-links/<来源>/`，重跑不再打外站。
- 页面上的名字（中文名、日文名、别名）按 `peach.social_links.name_key`（NFKC、casefold、去空白）与账本
  `canonical_name` 及 `name_chain` 匹配。一页命中两个实体记「需人工消歧」，不猜。
- 站点自己的社媒账号（laoshi 首页那几枚）先从每页外链里减掉，否则会给每个女优都装上站方的 X。
- javmodel.com 不是社媒来源，不用再试：走代理能取到 200（直连超时），但唯一的 twitter 链接是分享按钮，本人账号
  一个都没有。Instagram↔X 互补要等目录数据装入后从账本自身做，不在采集脚本里做。

### 链接判定与验活

- 判词四种：`ok` 进装入队列；`已有`（同平台同 handle，不分主机写法与大小写）；`conflict`（账本同平台是另一个
  handle）；`未取得`（页面失败或没有社媒）。
- 来源本身可能是过期数据：目录站抄的 X 账号很多已封停，本人早换了新号。所以 X 的 `ok`／`conflict` 行都用登出
  页 og 标签验活：活号有 `og:title` 和指向 `profile_images` 的 `og:image`；不存在的 handle 只回一个没有任何 og 的
  JS 壳，看不出死活。拿不到 og 时，再不走缓存地取对照账号 `x.com/X`：对照正常才判「疑似失效」，对照也空就是
  限流，写「未取得」。Instagram、TikTok、YouTube 的登出页什么都不给，只能写「未验」。
- **X 显示名写着「応援」的是粉丝号，不是本人。** 名录页会把 `篠田ゆう様💝応援アカウント` 这种账号当本人账号
  挂着，验活也是「活」。证据里标一句不够，`installable()` 只看 verdict 和 alive，所以判据落在 `probe_rows`
  （`FAN_ACCOUNT`），命中就降级成 `应援账号` 这个单独的判定。名录型来源不核实社媒归属，这一道只能自己做。
- 判据（平台名单、handle 归一、`twitter.com→x.com` 别名、标签写法、X 死活）集中在 `peach.social_links`，
  `normalize_link_hosts.py`、`harvest_performer_links.py` 都从它取；`harvest_social_avatars.py` 还留着一份旧抄本。
- **主机名一律小写。** jae 的资料页上写着 `https://Instagram.com/…`，而 `entity_link` 的 UNIQUE 只认字面，照抄
  就是同一个账号的第二条记录（`social_links.canonical_url`）。路径和 handle 不动：X 的 handle 大小写不敏感，但那
  是用户当初复核过的写法。
- **页面写明是博客的就按博客算，不看主机名。** `classify()` 只认 `BLOG_HOSTS`，而 `alicejapan.co.jp` 的子域、
  `plaza.rakuten.co.jp`、`takasyo.blog.jp` 都是本人博客却不在名单里；页面上那行「公式ブログ」比主机名更接近
  事实。标签照账本现有写法记「博客」，页面另外点出博客名时（`公式ブログ「旬の果実」`）才带上那个名字；
  `オフィシャルブログ` 这类泛称照抄进去，会让同一件东西在界面上出现三种写法（`harvest_directory_links.owned_link`）。
- 装入用 `install_entity_links.py` 读 `directory-links-<日期>.csv`；`-review.csv` 是全部判词，供人看。两者都不直接
  写账本。

### official 链接的标签与事务所

- **一个字段的值只描述它自己那一行。** minnano-av 资料表里「所属事務所」和「公式サイト」是两件事：白石亚子的事务所是 T-POWERS，
  公式サイト填的却是 Prestige 的専属宣传页。拿事务所名当 official 链接的标签，就会出现一个文字写 T-POWERS、图标和落点都是
  Prestige 的控件。
- 所以 official 链接的标签按域名归属写（`peach.social_links.host_owners`）。证据按序：厂牌实体自己挂的官网链接直接指认；其次是
  女优 official 链接里的共识（同一域名上 `OWNER_QUORUM` 条以上写同一个名字）。孤证会自我确认，不算共识。归属未取得时才用
  事务所名。
- 事务所名存在 `entity.metadata_json.agency`，带 `source` 与 `checked_at`，女优移籍按最新覆盖。已入库的错标由
  `scripts/repair_link_labels.py` 修，默认只出复核 CSV。
- `scripts/install_agencies.py` 把这些名字装成 `entity.kind='agency'`，归属写进 `entity_membership`（主键在成员一侧，一个人只有一条
  现役归属，移籍是覆盖）。原文留在 `metadata.agency` 当证据，结论错了才有得回溯。事务所不进 `asset_entity`：给作品另存一个事务所
  字段会漂移，而漂移的那份没人会发现。
- 事务所名里的括号按同一条规则拆：括号外是现用名，括号里的读音和括号后的 `旧・`／`元・` 都是别名。旧称撞上另一家的现用名时不写
  别名：`GG(旧・Prime Agency)` 的旧称正是仍在营业的 `Prime Agency`。
- 事务所官网取成员 official 链接里标签等于本家名字的那些，用域名根地址，不用某位女优的个人页。名字拆过之后要按原文再查一次，
  否则 `ACT(アクト)` 这类有站的会查不到。
- 现站打不开的公司可以登记 Wayback 快照：link_kind 仍是 `official`，url 写完整的 `web.archive.org/web/<时间戳>/<原址>`，label 写
  `官网存档（YYYY-MM）`（不带公司名，页头已有），优先所属名单页。`peach.social_links.ARCHIVE_HOSTS` 让域名归属、事务所门面圆标
  和厂牌标识采集都跳过它；标识要从快照取时，把快照里那张图的 `id_` 原件地址登记进 `studio_icons.py` 的指定来源表。复核表改了
  label，重跑 `install_entity_links.py` 即对齐。

### 事务所名册与移籍

- 「这家有哪些人」走 `scripts/harvest_agency_rosters.py`，一次取一家，比按人逐个问少两个数量级的请求。
  - minnano-av 没有事务所索引页，站内编号只出现在女优页「所属事務所」那格的链接里。所以先拿这家已知成员搜出编号，并按上面的
    括号规则核对那格写的确实是这家（`KRONE(クローネ)` 拆开才对得上；`GG(旧・Prime Agency)` 里的旧名不作数）。编号写进
    `entity_external_ref(provider='minnano-av', external_kind='production')`，下一趟不必再问。
  - 名册页 `actress_list.php?production=<编号>` 一页 30 位，人名读 JSON-LD 的 `CollectionPage`；正文 `<a>` 的文字是
    「名字 + 女優情報」，对不上账本。
  - 翻页地址取 `<link rel="next">` 并还原 `&amp;`，但终点不能听它的：最后一页之后站点照旧给出下一页，内容是最后一页那几位的
    重复，所以走到「这一页没有新人」为止。人数和 `numberOfItems` 对不上时，分清是页数封顶还是站点名册有重号。
  - 名字对回账本要连别名一起对：对不上的记「不在库」（默认收成一行计数）；一个写法对上两个人记「重名」；已归属别家的记
    「另有归属」，留给人判。`--apply` 只补空着的那一位。
- 事务所会拆、会改名。移籍走 `scripts/resync_performer_agency.py`：按 `--agency` 选整家或 `--only` 点名，重问女优页的「所属事務所」，
  只覆盖 `entity.metadata_json.agency`；实体和归属仍由 `install_agencies.py` 的 REPLACE 落地，免得两边各拆一次名字得出两种结论。
  - 检索必须走 `name_chain`：账本规范名是简体中文，站点只认日文写法，只拿规范名去问大多会「未取得」。
  - 站上那格是空的记「站上没有事务所」，不清账本：解约和站点当天不显示这格，在页面上分不开。
- 「所属事務所」只记现在签在谁名下。退役后转进个人经纪公司的人，那格写的是她自己的公司（三上悠亜 → 株式会社Miss），不是 AV
  事务所。这种由 `scripts/reject_agency.py` 按复核 CSV 驳回：驳回记进她的 `metadata.agency_rejected` 并撤掉归属，那家没人了就删
  实体；查实了 AV 时期的事务所就在 `replacement` 列写上，同一次改挂过去。驳回记在人身上，写元数据、建实体、补名册三条路径都经 `peach.entities.rejected_agencies` 跳过它，判词记「已驳回」。
- 一家拆成两家时，两家都在括号后写「旧・某某」。名册采集器按括号规则拒绝把任何一家认成被拆的那家，这是对的：旧名
  同时属于两家继承者（例：LIGHT 拆成 `ELTRA(エルトラ)旧・LIGHT` 与 `EST(エスト)旧・LIGHT`）。

### jae.tokyo 女优名录

jae.tokyo（Japan Adult Expo）是人工指定的第三个目录来源，同一站的厂牌名录见「厂牌名与厂牌标识」。

- 三届的资料页结构各不相同：2014 是 `jae2014/actress/NNN.html`，社媒和博客混在正文的 `<a>` 里；2015 是 `jae2015/actress.html` 的
  `offActress` 弹层，`actressLinkBtn` 一个按钮一条链接；2017 是 `jae2017/actress/NNN.html`，人像在 `img_area`、链接在 `link_area`。
- **`jae2014/*` 直连会被重置**（`WinError 10054`），所以 jae 整个进了 `PROXY_SOURCES`，同一来源不分届走两套出口。
- **注释里的链接不算这个人的。** 有的页面 HTML 注释里留着上一届模板里别的女优的博客和 Instagram，按 `<a>` 硬取会把几个人的账号
  装到一个人头上，所以解析前先剥注释。
- **名录人像进的是头像竞赛，不是另一条装入路径。** `-portraits.csv` 由 `harvest_social_avatars.py` 的 `jae` 路线读走，和 X、
  babepedia 的候选在同一套内容寻址缓存里比大小；竖版全身宣传照的取景交给人脸 sidecar，见 REUSE.md「人脸取景」。

### javdb 演员页

- **javdb.com 是按名字进的来源，不是能翻的名录。** 站上没有可枚举的女优列表，入口是账本里的名字：逐个写法搜
  `search?f=actor&q=`，结果卡片的 `title` 一栏就是这个人在站上的全部写法，不点进去就能判身份。名字链要整条搜完再放弃：账本
  规范名多是简体，`name_key()` 不做简繁转换，只搜规范名常常一个都搜不到（`harvest_directory_links.collect_javdb`）。
- **演员 id 在作品详情页就拿得到，不必另走一趟资料页。** 演員一栏每个名字都挂着 `/actors/<id>`，`sources.javdb.actresses` 顺手
  带出；名字精确匹配到这条资产已关联的人物实体，才登记成 `entity_external_ref(provider='javdb', external_kind='performer')`，人物页
  的 JavDB 入口靠它拼（`peach.entry_links`）。历史数据走 `scripts/backfill_performer_entry_ids.py`：javdb 页面缓存与
  `review/agency-rosters.csv` 的 `actress_id` 各补一路，先出 dry-run CSV 再 `--apply`。
- **作品不是从 javdb 刮的女优，由补女优资料后继按名字搜演员卡补 id（ADR-0081）。** 卡片上的 id 就是资料页的 id，
  无碼那条的头像上有 `<span class="info">無碼</span>`；对上的卡每种记录类型至多一张才算同一个人（`javdb.one_person`）。
  回填脚本读搜索页缓存时用同一份判据。
- **同名两条记录不取第一个。** 同一位女优在站上常有「有碼」「無碼」两条，两页都进判定，撞上的账号落成 `conflict` 进复核表。
- **一部分资料页要登录，回的是登入页而不是 401。** 不注册账号。那一页记「未取得」并写明原因：「搜过、站上没有这个人」「搜到了
  但要登录」「没搜」是三件事，不分开写，下一轮还得重搜。要登录的那页都是「無碼」那条孪生记录，有碼那条公开，中文名从它就
  取得到，所以为了取名字去登录没有增量；無碼记录的作品列表仍在墙后。
- **社媒按钮只在 `section-addition` 那一块里。** 整页别处的站外链接是广告、姊妹站和 RTA 标签。Instagram 是这个来源的主要增量。
- **javdb 的圆头像 250×250，进不了头像竞赛**（正方需 ≥400），`harvest_social_avatars.py` 不接这个来源。
- 限速与封禁见「javdb、AVBase 与 JavBus 的限流与封禁」里的「目录采集对 javdb 的限速」。

### 追更来源的名字发现

按名字发现追更来源时，站上的标识符写法以站方接口为准，不由手柄推定。

- **敲半个名字就能出建议的，只有本机清单和 rule34.xxx 的公开补全两处**，都不需要凭据。
  - 本机那份是 `discover` 顺带下载的整站创作者清单（kemono、pawchive、coomer），按 casefold 排好表后二分取前缀区间。
    敲字这条路**不下载清单**，没下过就少一组。
  - 站上那份走 `api.rule34.xxx/autocomplete.php?q=`。同一路径挂在主域名下会被 Cloudflare 拦成 403，只有 `api.` 子域回 200 JSON。
    它回的是标签不是作者名录，界面要写明这一组是标签。一次只回十条，热门前缀会把完整写法挤掉，有凭据时改用直接查标签。
    只接受抹掉分隔符并折叠大小写后与查询词相同的那个，同前缀的别人不算命中。
- **rule34.xxx 上谁是作者只有站方分类说得准，而那要凭据。** 按词形猜会错（名字里带 `artist` 的也可能是巧合）。分类在
  `index.php?page=dapi&s=tag&q=index&name=`，不带 `user_id`＋`api_key` 回 `"Missing authentication"`。三个坑：`json=1` 被忽略、只回
  XML；`names=` 不被识别，会吐一批无关标签；`name_pattern=` 是两边通配的子串匹配且按 id 截断，`orderby` 也不生效。所以只能按
  精确名逐条问（每条约 0.3 秒，可并发）。分类是站上改一次就定的事实，问过就记住。
- **gelbooru 不是 rule34.xxx 的超集，不能拿它替换或前置。** 两站同源但各收各的，同名标签的帖数也不同。gelbooru 的
  `index.php?page=autocomplete2&term=` 公开且自带 `category`，但 `limit` 不生效、恒回 10 条，拿它给 rule34.xxx 的候选标分类只能
  覆盖很少几条。候选名单必须来自 rule34.xxx：Peach 在那一站建订阅，给出它没有的名字就是误导。
- **f95zone** 的 `latest_data.php` 只索引 Latest Updates 的五个分类，艺术家的 Collection 帖只有带登录 cookie 的站内
  搜索看得到（无 cookie 时 `/search/` 回 403）。没有 cookie 就跳过它并保留 Google 外链，不把「查不到」写成
  「站上没有」。
  - 站内搜索按整词匹配：`strauz` 命中 0 条，`strauz*` 与完整写法命中同样的帖。所以各种写法都空手之后补一轮 `词*`；四个字符
    以上才加，三个字母加通配等于把半个站搜回来。
  - 线程标题的作者在末尾方括号里，约定是 `作品名 [版本或日期] [作者]`。从右往左找第一个不像版本号、日期和
    `Completed`／`Unity` 这类站点标签的方括号段；并列两个手柄（`[LazyProcrastinator/LazyProcrast]`）时第一个当显示名、其余是
    别名候选。没有可用方括号时才用主体，剥掉 `[Collection Request]` 这类前缀标签和 `Models Collection` 这类容器措辞
    （`follow_store.f95_author_name`）。
  - 作者的头像与别处身份只在首楼的正文链接区，而且要登录才看得见。首楼发帖人是搬运工不是作者，XenForo 的发帖人头像不能当
    作者头像。游客态下正文的站外链接全被换成 `/login/`，有的版块对游客整个关闭。
  - 名片里有 FANBOX 创作者 id 或 pixiv 数字 id 时头像取 FANBOX；没有时取 X 与 Patreon，两家都不带凭据：X 登出页的 og:image 按
    「厂牌名与厂牌标识」里 `pbs.twimg.com` 尺寸后缀那条退档，Patreon 公开的 `api/campaigns?filter[vanity]=` 给
    `avatar_photo_image_urls.original`。两家各取能用的最大一档后比实际像素，留大的。SubscribeStar **未取得**。
  - 认哪些主机算身份写死在 `follow_sources.profile_link_identity`：论坛正文谁都能贴链接，放开主机等于把别人贴的
    地址当成作者。

### 从 javdb 取中文名（`harvest_javdb_cn_names.py`）

- **页面结构：`actor-section-name` 是现名，紧随的 `section-meta` 是旧艺名，最后一个是影片数。** 两者必须分开取：
  `JULIA` 那页的旧名里有 `京香じゅりあ`，混成一串就分不出「这是她的中文名」和「这是她用过的旧艺名」。解析在
  `peach.javdb`，与 `harvest_directory_links.py` 共用一份。
- **现名栏并列的两个名字不一定是同一个名字的两种写法，也可能是两个艺名。** `美空あやか` 那页的现名栏是
  `一之瀨亞美莉, 美空あやか`，当成「中文写法 + 日文写法」就会给美空あやか安上一之濑亚美莉的中文名。判据是首个
  汉字串必须能在对侧找到（`same_person`），对不上的落 `不同名`，规范名不在现名栏里的落 `改艺名` 单独看。
- **比名字时字形要折两次，opencc 一次不够。** 它只管繁简，日本新字体不归它管：`永瀬` 转不成 `永濑`、`姫川` 转不成
  `姬川`，少一层就把同一个人判成两个人。两层都走 `peach.kanji`＋opencc `t2s`，落库的写法另走 `simplify_kanji`。
- **`?locale=zh-CN` 只切界面语言。** 女优名是数据，不跟着变：`愛音麻里亞` 在简体界面下仍是繁体，简体要自己转。
- 判词分档：`ok`、`同形（站上只有日文名）`、`要登录`、`旧名`、`改艺名`、`未取得`、`不同名`。落库取 `ok`、`改艺名`、
  `旧名` 三档。
- **旧艺名的中译只有资料页有，账本一个字都没有。** 刮削源给日文与罗马字；avdb 映射表每人只给一个 `zh_cn`，它的
  `keyword` 逗号列表只用于匹配、从不写进 `entity_alias`；改统称时降为别名的又是日文原规范名。于是 `橋本ありな`
  在账本里、`桥本有菜` 不在，按后者搜不到人。
  - `--aliases` 在同一趟抓取里把现名底下那一栏的中文写法取成候选。那一栏站上不给标签，旧艺名与昵称混放，
    解析层分不开也不筛，候选只记 `origin=别名栏`，认不认得这个写法是复核的人的事。
  - `--scope all` 把范围放开到已经有中文规范名的人，她们才是缺第二个写法的那一批；`--only` 按名字链上任一写法或
    实体 id 点名一位，不受范围限制，两次请求出结论。
  - **搜到多页时一个别名候选都不产（`多页`）。** 规范名那一份把两页都记下来让人挑，别名不能：别名进的是身份，配错
    人比缺一个写法更难查回来。撞上另一条实体名下的写法记 `占用`，那是两条该不该合并的问题，不由采集决定。页面上
    没有新写法的也留一行 `无新写法`：查过没查出东西，和还没轮到她是两件事。
- 落库是另一个脚本、另一次授权：`apply_alias_candidates.py --candidates <csv> --revision <批次> --apply --backup`。
  - 来源记 `javdb-actor-page@<批次>`（`peach.javdb.ALIAS_SOURCE` 是这个前缀），与 `localize_performer_names.py` 写的
    `javdb-actor-page@javdb-202609` 同一形状，也与界面上可撤销的 `user:alias` 分得开。
  - 批次号必须在命令行里给：解析判错时，认得出批次才能按 `source` 把那一趟整批撤回。
  - 四种不写：这条实体已有这个写法；写法归另一条实体；账本里的统称已经不是 CSV 里那个（快照过期，该重抓）；实体
    不在或不是 performer。

## 厂牌名与厂牌标识

这节讲厂牌的日文原名、官网、Logo 从哪里来，以及怎样判断找到的东西真属于这家厂牌。

### 日文原名

厂牌的日文原名是查出来的，不是转写出来的：罗马音回日文没有唯一解（`Hon Naka` 可以是 `本中` 也可以是 `ほんなか`）。
所以 `scripts/localize_studio_names.py` 不做音译，而是拿该厂牌作品的番号打 `www.javbus.com/<CODE>`，读 `製作商` 字段。

- 一个厂牌尽量取两个不同前缀的番号，两页一致才提改名。同前缀必然同一家，证明不了什么。
- javbus 有年龄门，不带 `age=verified` 只回一张确认页；番号页必须走代理，直连超时。
- 番号页 404 是那一页的事，不是这家厂牌查不到。印证靠跨前缀，顶替 404 靠同前缀：`code_groups` 一个前缀一组、组内最多
  `--depth`（默认 3）个，组内后几个不参与印证，只在前一个取不到时接手。
- 一个厂牌所有前缀的番号都 404 时，先怀疑账本：常是英文文件名或韩国演员的片子被刮削器套上了同前缀的 JAV 厂牌名
  （`Crystal Eizo`／HA），那个厂牌名本来就不属于这些片子。

七种判词各是一件事：

- 来源给汉字或平假名才改名（`Celeb no Tomo→セレブの友`）。
- 纯片假名只是英文品牌的外来语写法，默认保留账本里的英文原名（`ムーディーズ` 不顶 `MOODYZ`）。
- 来源自己也写拉丁的（`V＆R PRODUCE→V＆RPRODUCE`）差的只是空格与符号，不是去罗马音。
- 账本里根本没有 JAV 番号的西方厂牌与 FC2-PPV 记「不适用（非番号体系）」，不能写成「未取得」冒充取证失败。
- 两个番号给出不同製作商记「不一致」交人处理：`K M Produce` 出 ケイ・エム・プロデュース 与 スクープ，是一个账本名
  底下混了两家。

番号站转写的罗马音和意译都不算英文，优先级低于日文原名；只有厂牌自己用的英文名才保留（用户定的例子：`PREMIUM`
用英文，`Celeb no Tomo` 用日文）。判据是厂牌自称：官网或官方 X 显示名写片假名、拉丁名只是它罗马音的，同样改回日文
（`Akinori→アキノリ`、`Milu→ミル`、`Das→ダスッ！`）；官网自己写拉丁的保留（`BALTAN`、`GENEKI`）。所以「纯片假名」
判词只是默认值，改判直接写进复核件的 verdict 列。`merge_studio_name_variants.py` 的 class B 用同一口径：日文侧含汉字
或平假名就保留日文侧，纯片假名保留英文侧。

判「改名」的行由 `scripts/apply_studio_name_localization.py` 落库：规范名、旧写法降别名、扁平 `asset.studio` 与
`--logo-root` 下的标识一起改。复核件过期（现名已变、实体已合并）或日文名撞上别家的行，只报不改。

### 重复实体合并

- 重复实体按写法与书写系统两类合并（`merge_studio_name_variants.py`）。
- 写法变体（`AVS collector's` 对 `AVS collector’s`）的比较键是 NFKC 加符号折叠，**一个假名都不能丢**：只留 ASCII 的
  折法会把 `シロウトTV` 与 `ラグジュTV` 双双折成 `tv`，合出来是两家真实厂牌搅在一起，不可逆。
- 日文名与罗马字名（`ムーディーズ` 对 `MOODYZ`）唯一的身份保证是共用番号前缀：前缀属于厂牌，是本机可核验的证据。
  转写不参与判断。撞上两家以上一律交人工。
- 合并同时改写扁平 `asset.studio`，否则下一次刮削会照着投影把旧实体再建一遍。
- **合并之后标识要跟着改挂。** 标识按 `logo_key(canonical_name)` 命名存盘，被丢弃那一侧的方标在合并那一刻起没人
  认领，保留方的大位空着就回落到补白字标。
  `merge_studio_name_variants.py --logo-root` 按复核件搬运：保留方缺哪个变体补哪个，已有的一个字节都不动；`.ct` 与
  `.provenance.json` 随图走（少了 `.ct`，`/logo` 答不出 Content-Type）。

### 找官网

厂牌官网与社媒链接分别登记。`find_studio_socials.py --db <账本> --output <扫描表>
--review-output <链接复核表>` 只读已有官网，连同已有社媒的厂牌一起查，按账号大小写去重并列出缺项。
复核表保留来源 URL、页面 SHA-256、锚文本与上下文；确认归属后交 `install_entity_links.py` 安装。
不能把官网上全部账号自动归给厂牌：商城客服、演员与旗下其他厂牌各有归属。S-Cute 官网的
`SCute_av` 标为 S-Cute 官方，`_scute` 标为其直营后继服务 nanairo 官方，后者的链接标签保留 nanairo。
采集只认账号主页锚点；帖子、分享按钮、脚本字符串不算。年龄门的文字、图片 alt 和文字间空白均参与判定，
只跟同站肯定入口；HTTP 失败与跨域重定向记为未取得。每站完成即保存进度，`--resume` 复用有账号证据的行，
空结果与失败重新检查；采集本身不写账本。

- 搜官网与 X 账号用日文名，不用罗马音：内置浏览器打开
  `html.duckduckgo.com/html/?kl=jp-jp&q=<日文名> AVメーカー 公式`。Google 弹机器人验证，不绕过；内置 WebSearch
  只回美国过滤结果，拿番号或罗马音搜只会回无关结果。
- AV 厂牌官网普遍先给年龄确认页，不穿过它只能拿到约 10 KB 的空壳。判据必须是锚文本而不是 URL：否定链接指向
  站外（`dasdas.jp`、`muku.tv` 的「いいえ」都指向 dmm.com），肯定链接「はい（入室する）」指向站内，两者的 href
  看不出区别。实现见 `scripts/find_studio_socials.py`，`test_age_gate_is_crossed_by_the_affirmative_link_only` 守这条线。

`scripts/harvest_studio_sites.py` 从厂牌名猜域名找官网。每一道拒绝判据都要说得出反例，也要防它误伤真站：

- 拦停放页（`kawaii.com - domain for sale`：关键词在正文很靠后，却写在标题里）。
- 拦自述不可用的页（回 200、正文成人词齐全，标题只有 `Site Unavailable`）。「域名由厂牌名推出 + 是成人站」那条替代路径会把
  它们确认成官网，所以 `BROKEN_TITLE` 必须拦在停放页判据之后。
- 拦标题只回显域名的通用站（`prestige.com` 的标题就是 `prestige.com`，真站是 `prestige-av.com`）。判据是「标题照原样印着域名，
  且除域名之外什么都没说」；不能拿 normalise 后的标题比 normalise 后的主机，否则 `NATURAL HIGH（ナチュラルハイ）` 这类真站
  必然被判成回显。
- 判「没有官网」之前先分清是站点的回答还是网络抖动。`probe` 只对传输层异常按 `page_cache.Site` 的口径重试
  （`retries=2, backoff=2.0`），HTTP 状态码是站点的回答，不重试。`未取得` 的行把每次尝试的判词按顺序拼成证据链写进 `note`。
- 名字里没有拉丁字母的厂牌（`一本道`、`カリビアンコム`……）靠汉字与假名自己参与比对：`normalise` 留下汉字、平假名、片假名和
  长音符 `ー`；`・`（U+30FB）是分隔符，和全角括号、`【】` 一样剥掉。这类厂牌的域名推不出来，`--seeds` 是唯一入口。
- `entity_alias` 一起参与「页面自述厂牌名」那一道：`东京热` 的规范名是简体，页面写「東京熱」，账本里早有这个别名。拿别名比不
  放松判据。判词写「对上的是别名『X』」，不写「页面写作『X』」，因为 normalise 之后不再是页面原文。
- 成人语境词包括无码站写的「アダルト動画」「無修正」。同名的非成人站（Hunter Engineering、Bazooka、麦当娜）不会出现这两个词。
- **页面上没有的信息只能人工确认。** `SOD Create` 的官网是母公司站 `www.sod.co.jp`，这个串整站不出现；放宽通用判据去接住它，
  等于把 `hunter.com`、`bazooka.com`、`madonna.com` 一起放进来。所以走 `studio_sites.CONFIRMED_SITES`：一行一个厂牌，写清地址、
  人工确认的日期和理由。它只替掉最后那道「页面得自述厂牌名」，状态码、空壳、停放页／自述不可用、域名回显四道照旧要过；确认
  地址排在所有推导候选前面。
- 账本记罗马音、站上只用日文原名的，不进 `CONFIRMED_SITES`：那是账本名字错了，改成日文名页面自述就对得上（`えむっ娘ラボ`）。
  白名单只留「厂牌属于哪家公司」这种页面上没有的信息；有别名就走 `aliases`。
- 作品数少的厂牌也照样出现在厂牌页的大位上。`--min-assets` 只是全量扫描的阈值；定点补走 `--only <canonical_name>...`，指名就不看
  作品数，名字对不上直接失败，不静默跳过。

### 社交账号头像当 Logo

AV 厂牌 Logo 的来源是厂牌自己的社交账号头像：社交头像天然是正方形，且由品牌本人发布。

1. 取证顺序：handle → `unavatar.io` 解析出平台 CDN 真实地址 → 从 CDN 下载 → 实测。unavatar 只用来解析地址，provenance
   两者都记（`scripts/fetch_studio_avatar_candidates.py`）。
2. 候选用内容寻址缓存、SHA-256、同厂牌感知哈希和跨厂牌精确重复门槛。同图缩放或重编码记 unchanged，上游视觉真变化
   才重新进 `/review`。无 handle、无图片、unchanged 和 duplicate 只写健康报告，不占人工队列。

- 能解析不等于是对的品牌：`@bazooka` 确实存在且能取到 400×400 头像，但那是 2007 年注册的通用账号，不是这个 AV 厂牌。
  所以 handle 必须逐个取证确认，脚本默认不猜；`--guess-handles` 的产出一律标 `needs_confirmation`、不自动采纳，查不到
  就留空。
- r18.dev 详情 JSON 只有 `maker.name`／`label.name` 和作品封面，没有 Logo 资源。
- **`pbs.twimg.com` 的尺寸后缀不是「有这么大」的证据。** 无后缀的那一份是上传原图（最大档）；带后缀的地址在原图更小时
  返回的仍是原图：`セレブの友` 的 `_400x400` 和无后缀都是 242×242，而 unavatar 给的是 `_200x200`（8068 B 对 12302 B）。
  所以一律从无后缀原图起，按 `peach.social_links.twimg_tiers` 的档位往下退（旧头像有只剩缩略图的），`resolved_url` 记实际
  取到的那一档，全档缺失才算取图失败。厂牌 Logo（`fetch_studio_avatar_candidates.py`）与演员社媒头像
  （`harvest_social_avatars.py`）共用这一份判据。

### 名录来源

发行平台与发行商自己的厂牌名录是官方字标的广度来源。入口登记在 `harvest_maker_directories.DIRECTORIES`。
**存名录入口，不存图片地址**：图片地址里常带时间戳，厂牌换一次标识地址就变，重跑一次拿到的才是当下那份。

| 名录 | 入口与规格 | 注意 |
| --- | --- | --- |
| MGStage | `/ppv/makers.php` 按 50 音分页，统一 180×54 | 整站有年龄门，不带 `adc=1` 只回确认页；`osusume` 推荐位和音节页整片重合，靠 slug 去重；50 音导航条也是 gif，按文件名排掉；`【独占】` 是销售身份不是厂牌名 |
| Prestige | `/api/maker` 一次回全部厂牌 JSON，`codeName` 就是罗马字 slug，图是 `/api/media/maker/banner-<slug>.jpg` 白底字标 | `/maker` 与 `/maker/<slug>` 都是客户端渲染的空壳、标题完全一样，所以走 API 不抓页面 |
| KMP | `/label` 一页列完，名字在 `alt` 上 | 大半是 SVG，不按后缀筛；厂牌图在 `/img2018/label/<slug>/` 或 `/file/label_<时间戳>.<后缀>`，站头页脚的 KMP 自家标识走 `/wp-content/themes/`，按路径分开 |
| FANZA | `www.dmm.co.jp/mono/dvd/-/maker/=/keyword=<音>/` 按 50 音分页 | 带 `age_check_done=1` 才过年龄门，**且必须走代理**：直连回「お住まいの地域からご利用になれません」，状态码却是 200，只按状态码判会当成正常页。只给日文名加 `article=maker/id=<N>`，**没有厂牌标识图**，是名字来源不是标识来源；`digital/videoa/-/maker/` 连年龄门都过不去 |
| 妄想族 | `mousouzoku-av.com/maker/list/<50音>/`，一律 `contents/maker/id<N>/logo_l.jpg`、200×200 | official 级方标，直接够 `LOGO_SOURCES` 的门槛，不必烤方；`wa` 那一页回 500；名录写 `厂牌/发行集团`（`Asia/妄想族`），按斜杠左半对账本；多是同人／独立厂牌，与账本交集很小，价值在方标质量不在覆盖面 |
| javtiful | `/channels` 与 `/actresses` 分页列出 | 聚合站，图不能当标识：频道卡片是站方生成的字体图，演员卡片露出剧照。切 `/ja/` 前缀后**演员名**给日文（`hatano-yui` → `波多野結衣`），可当罗马字↔日文配对来源；厂牌名不随语言切换 |

- 名录给日文名、账本记罗马字，桥是文件名里的 slug。`harvest_maker_directories.py` 四路匹配：slug 归一相等、日文名相等、罗马字
  对上别名、唯一前缀候选（slug 是缩写时，如 `waap` 对 `Waap Entertainment`）。
- **归一成空串必须当不可比。** 纯日文名折掉非 ASCII 后都是空串，不排掉的话整份名录会全部对成同一家。前缀候选比前三路弱，判据要
  写进复核件：`きらきらワイフ` 撞上的 `kira*kira` 是另一家真实厂牌。
- **名录对不上账本，多数是账本里没有那家，不是缺日文别名。** 按子串加编辑距离放宽重算也只翻出假配对。MGStage 是素人／企划
  平台，没对上的多是 FANZA 系（MOODYZ、S1 等），要另找入口。无标识厂牌缺的是官网链接，待办见
  [公开路线图](https://github.com/orgs/peach-mitao/projects/7)「已有骨架」第 5 条。

**展会名录 jae.tokyo**（人工指定来源，Japan Adult Expo 的参展厂牌名录）：三届各带一套片商自己交的 logo，页面结构每届不同。

- 2014：`exhibitor/` 里 `<li><a><h2>名字</h2>` 加 `images/logo/*.jpg`（270×180，`alt` 不可靠）。
- 2015：`maker.html` 里 `offMaker` 弹层的 `makerLogo`／`makerRightTitle`／`makerLinkBtn`（188×188）。
- 2017：`maker.html` 的 `alt` 加详情页 `makaer/NNN.html` 的 `name_area` 与 `class="pop"` 官网链接（320×320）。
- 2016 那届只有图、HTML 里没有名字，不取。
- 名字对不上却是同一家的，按厂牌自称对（名录里 `SODクリエイト` 写作 `ソフト・オン・デマンド株式会社`）。同一家出现在多届时取像素
  最多的那届，逐张看过认得出是哪家才写进 `LOGO_SOURCES`。
- 详情页的官网链接逐条判 kind：
  - 目录站与配信平台不是官网：`mgstage.com`、`indies-av.co.jp`、`dmm.co.jp`、`fanza.com` 四个主机，以及路径带 `/works/list/` 的
    按片商筛出的作品列表，都进 `catalog`。
  - 母公司站内的厂牌页（`km-produce.com/l_06_bazooka.php`）算 official，它是这个厂牌在网上唯一的门面。
  - 站内搜索串（`?s=OREA`）、配信站筛选列表（`ppv_advanced.php?`）、周边商品列表（`goods_list.php?`）和配信平台首页不装。
  - `entity_link` 的 UNIQUE 按 URL 字面判，`http://www.x.com/` 与 `https://x.com/` 会并排两条，所以要按主机去重。
  - 装入前逐条探活，老届的地址有一部分已经不在了。

### 指定标识来源表

指定标识来源按**形状**分三张表，不按画质。三个取用位（资料卡大位 `[data-entity-portrait]`、`.idface`、筛选片）都是 `object-fit:cover` 的方框，
宽扁字标照原样装进去只剩正中间几个字母。

| 表 | 放什么 | 门槛 |
| --- | --- | --- |
| `LOGO_SOURCES` | 直接装进大位的方标 | `MIN_LOGO_SHORT_EDGE=96`，低于它就是缩略图 |
| `WORDMARK_SOURCES` | 宽扁字标，过 `images.bake_square` 烤成方图后两位共用 | 同 icon 位的 32 |
| `ICON_SOURCES` | 只管小位，大位照旧 | — |

- 宽扁字标的问题不在小，而在它不该走直接装的那条路，所以不拿 96 那道闸门去卡它。
- **两个位置要的可能是同一张图里并排的两块。** 例如一张横图左边是方标、右边是横排字标：`refit_plate` 按内容裁出方标给筛选片，
  另一份横条字标该待在 160 px 大位上。这种分工一张表表达不了，所以有 `ICON_SOURCES`。
- **别按像素数挑。** 更大的彩底轮播图烤方后会补出两大块底色，小一些的白底纯字标反而更对。都烤出来看过再选。
- **换掉已装的图要能被收进目标集。** `harvest_targets` 按「有没有 `<safe>.img`」收，改了指定表的地址单靠这一条收不到。
  `restated_sources` 拿装图时的 provenance 边车（`source_url`）和表里现在写的比，对不上才收，装完就一致、下一轮自己退出。
  写盘时「只认更大的」守卫对指定来源不设（`PINNED_KINDS`）：指定表里的地址是人逐张看过写进去的，换上一张小的也是想要的结果。

### FC2 的标识

- FC2-PPV「只有小图标」是站上确实没有。几个 FC2 主机声明的都是同一份 16×16 的 `static.fc2.com/share/image/favicon.ico`；更大的
  资产全是横向字标，属于 `logo` 位；唯一又方又大的 `id.fc2.com/apple-touch-icon.png`（114×114）是带文字的锁定图，缩到 28px 糊成
  一团，且挂在 FC2 ID 而不是 PPV 市场的主机上。
- 所以两个位置各用一份人工指定的非官网来源：
  - `icon` 位：`storage.googleapis.com/datanyze-data//technologies/8ef39cbce34aece41d279b6e8e7dbb77aea3086e.png`（400×400、
    纯红色独角兽没有文字），写在 `site_icons.HOST_OVERRIDES` 的 `fc2.com`。服务端回的 content-type 是
    `application/octet-stream`，靠 `link_marks.decode` 里 PIL 的嗅探解开；按 content-type 决定解不解会把这一枚整个丢掉，
    `test_an_octet_stream_png_is_still_a_png` 守这条。
  - `logo` 位：`images.seeklogo.com/logo-png/42/1/fc2-logo-png_seeklogo-429409.png`（600×600、独角兽 +「FC2」文字），写在
    `studio_icons.LOGO_SOURCES` 而不是 `HOST_OVERRIDES`：那张表管「按主机发现图标」的例外，这一份管「这个厂牌的大字标在哪」。
- 查过不用的：App Store 的「FC2動画」图标（背景多了胶片图案）、Wikimedia 同名的另一家、失效的图标库、要凭据的品牌库、
  只回 16×16 的 favicon 服务、带吉祥物或角标的商店图标。

## 站点圆标与图标合成

这节讲外链旁的小圆标和厂牌标识怎么发现、怎么筛、怎么烤成方图，以及页面怎样判断有没有图。

### 发现顺序

外链圆标取站点自己声明的那一份，不是根目录猜到的第一份。

1. 首页 `<link rel=icon|apple-touch-icon|mask-icon>` 与 `msapplication-TileImage`。
2. web app manifest 的 `icons[]`。
3. 老规矩位置（`/apple-touch-icon.png`、`/favicon.ico`）。

排序按「主机覆盖表 → 矢量 → 位图按尺寸 → 根路径猜测 → mask-icon」。两条排序规则各有反例：

- 矢量必须压过任何位图且与 `rel` 无关：threads 把 512 viewBox 的成品图标声明成 `rel="icon"`。
- 声明过的必须压过根路径猜测：T-POWERS 根目录的 `/apple-touch-icon.png` 是带文字的横向锁定图，`<link>` 里声明的那个才是
  紧凑标识，两个都是 180，并列时字标会因为路径短而排前。
- `rel="mask-icon"` 按规范是纯黑剪影，当成品图标用会得到一枚全黑方块，所以永远排最后，轮到它时走字形通道。

其他规则：

- 发现流程只读声明，不去正文里翻图。确实需要指定来源的（av-event 的吉祥物只出现在年龄确认页正文、FANZA 的资产托在
  p-smith.com）走 `site_icons.HOST_OVERRIDES`，每加一行都要写清为什么发现流程不够，否则那张表会长成一份没人更新的手工
  favicon 清单。`HOST_OVERRIDES` 的键支持「主机 + 路径前缀」，取最长匹配。
- 一次发现最多真的下载 `MAX_FETCH` 个候选：取回来却不合格才算用掉一次，404 不算。

### 闸门与两条通道

- **判据是内容外接框，不是画布。** 方画布里装一条宽扁字标，塞进 32 px 圆里是一条糊掉的横杠；更小的图里只有一个字母，反而清楚。
  所以排序之后还有一道内容比例闸门（`link_marks.MAX_CONTENT_ASPECT`）：宽扁字标不参加小圆标竞选，它属于厂牌页的大 logo 位（`/logo`）。
- 过闸门后分两条通道：成品方形图标照原样放行，圆由 CSS 的 `[data-link-icon]` 裁；透明单色字形才做「品牌色圆底 + 白色主体」。
- 过闸门也不等于适合，内容比接近上限的字标照样认不出。所以 `studio-icons-<日期>.csv` 带 `content_aspect` 列，接近上限的行要
  人眼看过九宫格再定。
- 厂牌小标借 `link_marks` 的内容比闸门，不借它的尺寸下限。`MIN_DESIGNED_SIZE=96` 是给 `/link-mark` 的 128 px 圆标定的，JAV 厂牌站
  的 favicon 普遍只有 32 或 64，套上去会把 HEYZO、MOODYZ、Prestige 等一批全退掉并误记成「仍是字标」。所以
  `scripts/harvest_studio_icons.py` 尺寸另设 `MIN_SHORT_EDGE=32`（要顶的位置只有 28～32 px），像素不放大，`MIN_DESIGNED_SIZE`
  不为它去动。

### 厂牌标识的 icon 与 logo 两份

- 厂牌标识按位置分 icon／logo 两份，但只在真的有两份时才分岔：`<safe>.icon.img`、`<safe>.logo.img` 都回落到 `<safe>.img`。
  绝大多数厂牌两个位置拿到的是同一张。
- 存盘后缀说明不了清晰度：有的厂牌 `icon` 只有几十像素，裸文件却有几百像素。取图位与 `variant` 参数的页面约定见 [docs/FRONTEND.md](FRONTEND.md)。

### 来源顺序（`studio_icons.icon_row`）

来源顺序按分辨率择优，不按正式程度死排（厂牌页大位是方图，头像够清晰就能当 icon 用）。

1. 官网声明的图标 → 首页 header 的 `<img>` → 人指定的社媒头像 → 页面上挂着的 X 账号头像。
2. 短边到 `GOOD_ENOUGH_SHORT_EDGE=360` 就停：公司格最宽 180 CSS px，2 倍屏 360 实像素之后在页面上没有分别，每多问一个来源
   就多敲一次别人的门。
3. 没到线就把余下的来源问完，按短边取最大的那一枚；但要大出 `BETTER_BY=1.5` 倍才顶掉排在前面的：尺寸差一点时看不出区别，
   而来源的正式程度有差；声明的 16×16 对上 400×400 的 X 头像才是该换的那种差距。

- **大不能压过形，两位各挑各的。** 顶替者还要不比被顶的那枚宽出 `ASPECT_SLACK=1.1` 倍：图标加字样的横图比纯图标大，但装进
  28 px 小位只剩认不出的字母。那张正是大位要的完整标识，由 `hunt_logo_row` 从这一趟的收获里另挑最大一枚，
  判词 `这一趟取到的最大一枚`。
- **一条官网都没有的公司用社媒头像。** `FALLBACK_LINK_KIND="social"` 只在这家没有 official／catalog 时才用，直接取账号头像，不问
  声明的图标和 header。有官网的一概不走：账本里的社媒绝大多数挂在艺人身上，混进来就成了运营的自拍。twimg 的头像地址不带签名。
- **共享主机守卫。** 主机级发现只代表主机，代表不了同一主机路径下的频道：`bangbros.com/websites/` 下三个厂牌的 official 链接会
  坍缩成同一个主机，取到站点模板的通用 favicon。所以链接带非根路径时，
  `studio_icons.py` 给 `site_icons.best_mark(accept=...)` 挂守卫：`site_icons.HOST_SCOPE` 的候选不算数，判词 `平台通用图标`，证据写明
  主机、哪一份和 sha256。`/link-mark` 本来就按主机（`cache_key` 也按主机），不受这条约束。
- **可达性探测要扛住 TLS 抖动。** `install_entity_links.resolves` 对传输层异常重试三次，状态码一次成局。`site_logos.logo_images`
  丢掉 `data:` 懒加载占位图，它顶着标识的 class，取字节又打不开。
- **Instagram 头像**：`--avatars` 读本机 `peach-data/state/agency-avatars.json`，是人工地址输入，不是通用解析器；地址发现与
  字节下载分开验证，取图边界见 [来源采集](SOURCING.md) 与 [ADR-0024](adr/0024-mark-manifest-not-bundled-bytes.md)。
- **字标补白**：方标一个都没做成、却取回过短边 ≥ `MIN_SHORT_EDGE` 的宽扁字标时，用 `peach.images.bake_square` 烤成方图装上，判词
  `字标补白`，`content_aspect` 照记。同一份方图再出一行 `logo`（判词 `ok`）装进 `<safe>.logo.img`，否则大位会回落到 `<safe>.img`
  （BangBus 页顶上就会挂母品牌 BANGBROS）。人工指定的 logo 来源做成时优先；留第一份而不是最大的一份，因为 `best_mark` 的遍历
  顺序已经是「覆盖表 → 声明 → 根路径猜测」。BangBus、BangBros18 的字标取自 `bangbros.com/websites` 服务端渲染进 HTML 的
  `*_LOGO` 资产（注意 `/` 转义）；MonstersOfCock 没有对应资产，记**未取得**，继续用现有的 `MonstersOfCock.img`，不用推测顶替。
- **指定 logo 来源自己就是入场理由，小位从大位那张烤。** `harvest_targets()` 收三类：补白过的、有链接但没图的、有指定 logo 来源
  但没图的；第三类给连一条 official／catalog 链接都没有的厂牌（多是 jae.tokyo 名录那批）。`icon_from_logo()` 在小位没做成、大位
  的指定来源做成时，把同一张过 `bake_square` 装进 `icon` 位。判「补没补白」看源图长宽比与 `images.MAX_ASPECT`，不看内容比：整幅
  不透明的 jpg 的 `content_aspect` 一律是 0。复核件的 `studio` 列从 `LOGO_SOURCE_NAMES` 取。
- **已装的方标太小要再问一趟。** `studio_icons.py` 把小位短边不到 64 实像素的厂牌一并收进目标（`INSTALLED_SHORT_EDGE`、
  `small_installed_marks`），量的是小位真会取到的那一份（`<safe>.icon.img` 优先，没有才用 `<safe>.img`）。写盘另有
  `_shorter_than_installed` 守卫，只可能换上更大的，问不到就在复核件上留判词。

### 判词

每一档判词对应不同的下一步：

| 判词 | 含义与下一步 |
| --- | --- |
| `ok` | 可装 |
| `字标补白` | 可装，见上 |
| `只有小图标` | FC2 全站只有 16×16，该去找更大的资产 |
| `平台通用图标` | 见「共享主机守卫」 |
| `仍是字标` | 没有合格方标 |
| `未取得` | 一份字节都没取回，`Fetcher` 自己数取回几份才判得出来 |
| `无官网链接` | 先补链接 |

只有前两档会被 `--install` 写盘。`best_mark` 只回结果不回理由，退回原因由 `SquareMark` 就地记下，否则复核件上只剩一个空判词。

### 烤成不透明方图

**Logo 文件一律是不透明方图**，边距和底色烤进文件，页面不再各自补救。位图的唯一入口是 `peach.images.bake_square`，
`classify_plate` 给出它据以分流的判定：

- `mark`（有透明像素，如透明底字标）：按 alpha 外接框裁掉透明边，居中放到不透明方底上，内容占边长
  `PLATE_CONTENT_RATIO`（0.76，四周各留约 12%），像素不缩放，出不透明 PNG。
- `tile`（完全不透明，如黑底或红底方块）：底色是设计的一部分。接近方形的返回原字节，长条按边缘主色补方（`pad_to_square`），
  不刷白。

两条路的产物都再过一遍 `refit_plate`。小圆片铺满的是整张画布而不是内容：favicon 自带的大留白会让标识小得认不出，顶到边的
实心方标四角又会落在圆外。`refit_plate` 依次做三件事：

1. 内容占宽低于 `PLATE_MIN_SPAN`（0.6）：裁到内容框，四周留 12%。
2. 内容落在内切圆之外的比例超过 `PLATE_CIRCLE_LOSS`（0.025）：把画布补到内容的外接圆。
3. 产物不到 `PLATE_MIN_SIDE`（64，即 32 CSS px 在 2 倍屏上的实像素）：用自己的底色补到这个数。判的是产物而不是原图，因为
   不少图是裁掉留白之后才不够的。补边上限卡在 `PLATE_MIN_SPAN`，再往外撑就成了第 1 条要裁的大留白，两条规则会来回拉锯。

- 像素一律不缩放：裁出来的更小但更清晰，补出来的更大而清晰度不变。几个断点是按实际图标逐张看过定的。
- 内容框按行列统计，零星几个像素的行列不算内容：有损压缩在纯色区留下的淡斑点会把逐像素外接框撑满整张画布。
- 写入侧只有两条路径，规则同一条：`studio_icons.py` 的 `install()` 写盘前烤；`normalize_studio_logos.py` 回溯已装文件（目录下所有
  `*.img`，含 `.icon.img`／`.logo.img`，写操作要 `--apply`）。两者都幂等。女优头像等照片不走这条路径。
- **矢量**：`install()` 只收位图，矢量按「拒绝安装」处理。目录里的 SVG 由 `normalize_studio_logos.py` 走
  `peach.images.bake_square_vector`：同样 76% 边距，方底由外层 SVG 给，原文档整个塞进嵌套 `<svg>`、一个节点都不改写，内容框直接取
  `viewBox`。外层根元素留 `data-peach-plate="1"` 让重跑跳过，否则会越套越多。`viewBox`、`width`／`height` 都没有的空壳量不出比例，
  仍记 `vector`，保持原文件。
- **底色按内容明暗判，位图和矢量同一条规则**（`images._plate_color`；矢量先栅格化一张 256 px 探针数像素）。内容框里的不透明覆盖
  低于 `PLATE_SOLID_COVER`（0.9）才判底色，白底上看得见的比例低于 `PLATE_VISIBLE_RATIO`（0.7）就配深底 `#111111`：白笔画配白底
  等于抹掉半个标识。自带整块底的（白卡片、迷彩方块）边界是自己画的，不配深底。
- **归一从原图开始。** 已装文件记着备份原图、备份又在本机时，`normalize_studio_logos.py` 拿备份当输入：烤底毁掉的透明通道和配错
  的底色在产物上判不回来。边车继续指向原图，不改指到这一轮的归一产物。
- 边车的 `action` 是认来路的稳定标识：`bake-white-plate`、`pad-to-square`、`refit-plate`、`plate-vector`。`studio_icons.padded_studios`
  按 `pad-to-square` 认「源图是条状字标」，所以三条位图路径分开记。每张已装位图的来路记在 `*.img.normalization.json`。
- **存盘文件名保留假名与汉字。** `previews.logo_key` 按 `\w` 归一，标点变下划线，长度上限 60。只留 `[A-Za-z0-9_-]` 的话，
  `プレステージ` 与 `ムーディーズ` 都成了 `______`，后装的那张静默盖掉先装的。

### 反色圆标

反色圆标的锯齿有三层成因，少修一层都还是毛的：

- 遮罩用 `alpha >= 128` 二值化，会把源图自带的抗锯齿中间值一刀砍光。
- 二值图在源分辨率 48×48 上生成再拉到 64，会把台阶一起放大。
- 字形没有与圆做 `composite`，白像素溢出圆外，圆边被啃出缺口。

所以 alpha 直接当连续遮罩，整套合成在 8 倍超采样画布上做完，再一次性 LANCZOS 缩下来，成品 128 px（容器 32 px CSS，3x 屏要
96 px）。改了取图规则或合成方式必须同时加 `link_marks.RENDER_VERSION`：缓存缓存有效期是 30 天，不换键的话代码换了，用户看到的仍是旧那张。

### 页面取图位与「有图才出 `<img>`」

这一小节是 Web 契约，放在这里是因为它和标识、头像的存盘规则绑在一起。

- **三处取图位统一铺满。** 品牌小圆片 `.brandpill .mk`、身份格 `.idface`、厂牌页 160 px 大位 `[data-entity-portrait]` 的 `img` 一律
  `object-fit: cover`，不加 inset、padding，不改 contain：边距已经烤进文件。占位底色与首字母只在取不到图时露出来。
  「原生尺寸 + 模糊补底」（`data-fit-native`）只装在后两处大位上；小圆片不这样摆，否则标识小一半，自带白卡片的图还会露出方角。
- **服务端说有图，页面才出 `<img>`。** 缺图时无条件出图会换来一串不带缓存头的 404，每次重绘再打一轮。
  - 厂牌标识：`WebContract.has_logo()` 判定，随资料下发为 `has_logo`（`/api/tops`、`/api/entity`）与 `has_studio_logo`（`/api/item`
    里非规范厂牌只有扁平 `studio` 字段，单独一个标志）。索引是一次 `os.scandir` 的 `logo_index()`，TTL 90 秒，复核批准
    `cache_bust()` 后立刻可见。
  - 人物图与头像：`has_entity_image()`／`has_avatar()` 判定，下发为 `has_image` 与 `has_avatar`。端点用 `web_catalog` 的
    `entity_ref()` 和 `attach_avatar_availability()`（批量取，不逐行 N+1）挂标志。页面只有 `frontend/src/card-art/markup.ts` 的 `entityFaceImg()` 一处拼
    这两个地址，各取图位经 `avatarInner()` 共用它；缺席的 `has_image` 按「没图」处理，否则忘挂标志的端点会悄悄退回无条件出图。
  - 人物图随 `has_image` 一起下发 `image_version`（`entity_image_version()`，实体图修改时间的十六进制纳秒），`entityFaceImg()`
    拼成 `&v=`。换头像原地覆盖同一个文件，地址不带版本时同一页里浏览器复用内存里那张旧图，要刷新才看得到新的。
  - 封面与标识同理：`cover_version`／`logo_version`（`cover_index()`、`logo_index()` 扫目录时取的修改时间）随卡片、详情、
    厂牌引用下发，拼 `/cover`、`/logo` 时带成 `&v=`。挑头像的底图是封面时 `/api/avatar-choices` 也给 `version`，取图带上它，
    交框时回递，服务端核对不一致就拒收，免得框按旧封面量、裁在新封面上。
  - 存盘文件名只有一份规则：标识走 `previews.logo_key`，人物图走 `previews.entity_image_key`（kind 是名字的一部分，认得的种类见
    `previews.ENTITY_IMAGE_KINDS`；`.ct`、`.provenance.json`、`.face.json` 是边车，不算图）。
  - 头像按需生成，所以 `has_avatar` = 已裁好的 `<id>.jpg` **或**印相还在盘上（`has_snapshot`）；生成中途的 `<id>.<格>.tmp.jpg`
    不算数。生成本身仍可能失败（没有 ffmpeg、六格全黑），`data-drop="self"` 的撤图处理要保留。
  - 复核卡片那张脸的 kind 在 `web_review.ENTITY_REVIEW_KINDS` 与页面的 `ENTITY_REVIEW_CATEGORIES` 各一份，必须逐字一致。
  - 门槛：`tests/test_studio_icon_variants.py` 的 `LogoAvailabilityTests`、`tests/test_previews.py` 的
    `EntityImageAvailabilityTests`／`AvatarAvailabilityTests`（可用性与取图必须给同一个答案）、`tests/test_rm_web.py` 与
    `tests/test_web_review.py` 的端点标志测试、`tests/test_web_ui.py`。

## 番号发现源（Feed）

Feed 只回答一个问题：**最近出了哪些番号**。它不下载、不碰媒体文件，产物是一条番号加一个可点开的作品页地址，刮削仍走既有的
来源链。取证脚本与原始结果在仓库外的 `<私有验证记录>`。

判据不是「这个源有没有 RSS」，而是「条目里那串东西是不是真的番号」。一个源接进来之前，必须先看一眼它的番号长什么样，
不能只看解析成功率（反例见下文 DUGA）。

### 可用的两类

- **原生 RSS：sukebei.nyaa.si**（`https://sukebei.nyaa.si/?page=rss&c=2_2&f=0`）。
  - RSS 2.0，一页 75 条。`guid` 是条目永久链接（`https://sukebei.nyaa.si/view/<id>`），`pubDate` 是 RFC 822 带时区的真实时间，
    **天然满足两层去重里的条目身份那一层**。
  - **它不给 `ETag` 也不给 `Last-Modified`**，对它来说 304 那一层不生效，只能靠条目身份去重。
  - 标题形如 `HMN-071 新人 …[有碼高清中文字幕]`，番号在最前面。整段丢给 `catalog_rules.release_code_from_text` 几乎认不出；
    先按空白与括号切词元再逐个试，能认出约三分之二。**这是 Feed 必须自己做词元扫描的直接理由**，不能照搬「整段文本 → 番号」
    那条路径。认不出的多半是无码番号、素人片与合集，不是解析缺陷。
  - `c=2_2` 是分类，`f=0` 是不过滤；换分类或加 `q=` 关键词就是另一个订阅，形状不变。
- **伪 Feed：JavDB 演员页**（`https://javdb.com/actors/<javdb_id>`）。
  - 一页 40 部作品。每部是 `<a href="/v/<id>" class="box" title="…">` 加 `<div class="video-title"><strong>番号</strong>…</div>`
    加 `<div class="meta">发行日</div>`：`/v/<id>` 当条目身份，`<strong>` 里是干净的番号。几乎每条都取得番号，取不到的是番号栏
    本身为空。
  - **它按发行日排，所以「还没发行的作品」会先出现**。空壳的发行日可以晚于今天，这不是脏数据。
  - 账本里已经有 `entity_external_ref` 的 `javdb` id（`entry_links.provider_ids`），订阅不必让用户手抄地址。
  - 订阅从人物页开关进，也可在关注管理「订阅源」页签按女优名订阅：服务端到 JavDB 演员搜索取演员卡，用户在卡上点选，
    页面只送演员 id，地址由服务端现拼；账本里没有这位就新建 `performer` 实体（ADR-0083）。
  - 限流照库内采集：主机间隔 3 秒、403 就整源停下，冷却判据在 `scraping_access`，见「javdb、AVBase 与 JavBus 的限流与封禁」。
    不要因为 Feed 是后台任务就另开一套。
  - **加 `?sort_type=4` 拿到的页面一条作品都解不出**，同一轮里不带参数的请求仍是完整页。所以演员页伪 Feed 一律用不带查询串的地址。
  - 标题栏 `<span class="actor-section-name">` 是这一页挂的名字，常把几种写法用逗号连成一串（`涼森玲夢, 涼森れむ`）；
    另一个 `section-meta` 写着「N 部影片」。一位女优常有两页，
    旧艺名那页可能常年是空的（`/actors/MmbDR` 白石アイリ，2026-09-28 实测「0 部影片」「暂无内容」）。页面自报 0 部才算安静源；
    计数不为 0 却一条都解不出，仍按拉取失败报。

### 已核实不可用

FANZA／DMM、MGStage、一本道同族、Tokyo-Hot、AVBase、javtrailers、OneJAV 的 feed 地址回 404 或 500、首页不声明 feed（一本道同族
`movie_details` 仍可用，但没有新作列表路径）；FANZA 302 到年龄确认页，JavBus 302 到人机验证页；RSSHub 公共实例与 javlibrary 回
403（Cloudflare，不绕）；色花堂回的是 HTML。逐站地址与原始结果在上面那份取证目录里。

**DUGA 是反例**：`https://duga.jp/news.xml` 是唯一由首页 `<link rel="alternate">` 正经声明的 RSS，`pubDate` 齐全，看起来完全可用。
但条目标题一个番号都不带，番号只能从链接里取，而链接里那个是 DUGA 的站内商品号（`ppv/doc-2376`）。它长得和厂牌番号一模一样，
`release_code_from_text` 会照单全收，产出 `DOC-2376` 这类**在任何刮削来源上都不存在的假番号**。

## 缓存与重试

这节讲所有采集脚本共用的整页缓存、重试规则，以及解析测试的固定件从哪来。

- 整页 HTML 缓存与限速走 `peach.page_cache.Site`（按 URL sha1 命名存盘，`cookies` 用来带过年龄门）。它放在 `src/peach/` 而不是某个
  采集脚本里，因为目录链接采集和厂牌名回查两个脚本都用它。采集脚本的判据改一行就要重跑，缓存在手，重跑才能走离线数据、不再打外站。
- 退让重试也在这一层：经代理取 javdatabase 约三次里有一次 TLS `UNEXPECTED_EOF`，一次抖动打死整批是这个项目犯过两回的错。所以
  `Site` 自己重试传输错误（默认 2 次、`backoff` 递增），采集脚本不必各写一遍。HTTP 状态码不重试：404 重试三次仍是 404，只是白花
  三倍流量。
- **解析用的固定件必须是抓回来的那份 HTML。** javdatabase 的资料行真身是
  `<b>JP:</b> 涼森れむ  - <b>Alt:</b> Iwatani Shiki, …<br>`，按记忆写成 `JP: 名字` 的固定件会让正则被紧跟的 `</b>` 顶掉：测试全绿，
  线上却一个日文名、一个旧艺名都没采到，只回罗马字。照着记忆重画的固定件只能证明代码和记忆一致。
- DMM 图片主机（`pics.dmm.co.jp`、`awsimgsrc.dmm.co.jp`／`.com`）在国内的直连可达性随运营商走：电信、联通直连正常，中国移动
  大多在 TLS 握手后被断开。量尺寸时每张候选都断在连接上、没有一家回过话，`best_cover` 抛 `CoverConnectError`：它说的
  是线路，续跑照常重试，新作那一行与采集页据此指向 DMM / FANZA 的连接方式，不报成官方没有图。

## 番号样张

女优页照片档里按番号摆的官方样张（ADR-0068）。读写与取页在 `peach.sample_images`，补齐在 `peach.sample_followup`（任务中心
「补番号样张」）。

- 只认发行方自己那几站：
  - 有码是 DMM / FANZA 的 `jp-N`。GraphQL 给的 `awsimgsrc…/pics_dig/…/<cid>-N.jpg` 是 120×90 小图，同目录 `<cid>jp-N.jpg` 才是
    800×534 原图，落库前改写。
  - 素人是 MGS 商品页的 `a.sample_image`（`cap_e_N`，840×472）。
  - amane 桥交的 `screenshot_urls` 只从快照里读。
- javdb、JavBus、avwikidb 的样张都是 FANZA 那一张的转载或直链，不算官方判据；avwikidb 的增量是逐张女优标注 `sampleImageActors`，
  见待办。FC2 商品页没有样张区，R18.dev 详情没有 gallery，都是未取得。
- 先读 `sources/library-metadata/<番号>-<站>.json` 快照，零网络；没有才问站。站点说没有记在
  `generated/provider-cache/sample-images/misses.json`，7 天内不再问；冷却与预算用完不记。
- 图不在采集时下载：第一次有人看时，`/sample-thumb` 才按这一站的连接方式取一张存进 `generated/sample-cache/`。DMM 图片主机在移动
  出口不通时回 404、记一天的失败标记，页面显示占位。
- Gfriends 是头像与资料照，不按作品分组，不当写真集用。
