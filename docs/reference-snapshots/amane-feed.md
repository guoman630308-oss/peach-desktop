# amane Feed（远程发现源）设计证据

- 来源：<https://github.com/sqzw-x/amane>，GPL-3.0
- 固定 revision：`79ecfa763cc786318e1964a3d7f4e244a7d5c96d`
- 取得日期：2026-09-22
- 取证方式：仓库外独立克隆，
  只读 `docs/dev/feeds.md`、`src/amane/scheduler/feeds.py`、`scheduler/rss.py`、`db/models.py`、
  `db/repos/feeds.py`、`db/feed_keywords.py`、`api/models/feeds.py`，不引入代码。GPL 与 Peach 的
  许可证不兼容，本文只记录设计判据，Peach 侧按自己的表结构、HTTP 栈与调度模型重写。
- 本文不登记进 `docs/reference-sources.json`：与 `amane-crawlers-poc.md`、`amane-task-followups.md`
  同理，这是对上游源码的一次性只读实证，没有对应的可变 Markdown 原文需要跟踪漂移。

## 已取得

### 一、Feed 的定位是「远程目录事件源」，产物只有一条番号

上游把 `Feed` 与 `Library` + 文件监视器并列：一个是远程目录的事件源，一个是本地目录的事件源。
`docs/dev/feeds.md` 写死了产物边界——只产生 by-number 的 `SCRAPE`（`media_file_id=None`），
**不下载 enclosure 与种子，不写入 Library**；文件日后真出现了，仍由本地监视器把它和番号关联起来。

它也刻意不是 `Schedule`：`Schedule` 是用户 routine，入队任务并占用 Worker；Feed 拉取是生产者，
间隔写在源自己的行上（`interval_seconds` + `next_fetch_at`），不进全局热配置，也不并入 cron 调度器。

产品不内置站点列表爬虫。没有原生 RSS 的站点，上游要求用站外适配器（如 RSSHub）转成 feed 再订阅。

### 二、拉取调度：固定 60 秒扫到期，失败也按同一间隔重试

`scheduler/feeds.py` 的 `FeedService.start` 是一个 `while self._running` 循环，每轮 `_tick` 后
`await asyncio.sleep(60)`。`_tick` 查 `enabled AND (next_fetch_at IS NULL OR next_fetch_at <= now)`，
按 id 顺序逐个 `poll_one`。

几条判据值得抄：

- `enabled` **只**控制是否进入这一轮扫描。关掉之后，创建时的立即拉取和手动 `POST /feeds/{id}/poll`
  照常执行，走的是同一个 `poll_one`。
- 每源一把 `asyncio.Lock`（`self._locks: dict[int, asyncio.Lock]`，外加一把建锁用的 guard 锁），
  定时拉取与手动拉取因此不会同时打同一个源。粒度是源 id，不是全局。
- 失败也按该源的 `interval_seconds` 排下一次，**不做指数退避**；错误写进 `last_error`，
  下一次成功时清成 `None`。
- `next_fetch_at` 在发请求之前就按 `now + interval` 算好，四条返回路径（请求失败、304、空体、
  解析失败、正常）全都写同一个值——也就是说任何一种结局都不会让这个源卡住不再排队。
- `rebuild()` 只替换 HTTP 客户端引用，循环不停。

### 三、去重分两层，各管各的

上游文档原话是「两项相互独立」：

- **整份文档层（HTTP 304）**：源行上存 `etag` 与 `last_modified`，下一次拉取带
  `If-None-Match` / `If-Modified-Since`。304 被显式列进 `ok_statuses`，**不算失败**，
  直接按间隔排下次并返回，连解析都不做。
- **条目身份层**：`FeedItem` 上 `UNIQUE(feed_id, item_key)`，`item_key` 取 `guid` → `link` → `title`
  三级回退，三个都空的条目直接丢弃。拉取时先 `list_feed_item_keys(feed_id)` 取全量已见集合，
  在内存里过滤出新条目。

关键的反直觉一条：**番号本身不参与跨轮去重**。已经有 `Metadata` 的番号仍然入队 `SCRAPE`；
只有「同一 tick、同一源内」的相同番号才合并成一次入队（`enqueued_numbers` 集合）。
解析不出番号的条目也照样插一行（`number` 为空），为的是不要每轮都对同一个垃圾标题重试。

### 四、番号解析：每源可覆盖，覆盖后不回退

`resolve_entry_number`：源上设了 `number_pattern` 就**只**用这条正则，按 title → description → link
依次试，有捕获组取 group 1，没有取整次匹配，取不到就是没有——**不回退**通用提取器。
没设 `number_pattern` 才走通用的 `extract_number(title)`，标题取不到再试正文。

正则在 API 层就 `re.compile` 校验过；运行期 `re.error` 再兜一次，返回 `None` 而不是让整轮拉取炸掉。

### 五、解析只吃字节，禁止把 URL 交给解析器

`scheduler/rss.py` 的模块 docstring 写得很直白：调用方负责 HTTP，**禁止把 URL 交给 feedparser**
（它会自己发 HTTP，绕过项目的客户端、代理、限流与超时）。解析入口是 `parse_feed_bytes(body: bytes)`。

正文取值顺序：Atom/RSS `content[].value` → `summary_detail.value` → `summary` / `description`，原样保留 HTML。
发布时间取 `published_parsed`，没有再取 `updated_parsed`——但必须写成 `dict.get(entry, "updated_parsed")`，
因为上游解析库的字典在缺该键时会映射到 `published_parsed` 并触发弃用警告。

`parse_feed_bytes` 在「一条条目都没有且被标记为损坏」时返回 `None`；条目为空但文档合法则返回空列表，
这两种在调用侧的处置不同（前者写 `last_error`，后者算一次正常空拉取）。

### 六、已读与忽略是两列，彼此正交，也不影响去重

`FeedItem.ignored_at` 与 `FeedItem.read_at` 是两个独立的可空时间列，都带索引。
上游文档明确：二者正交，**都不改变 `(feed_id, item_key)` 的去重关系**。

- `ignore`：保留历史行与去重记录，不取消已入队/在跑/已完成的 `SCRAPE`；后续拉取不会重新创建。
- `unignore`：只恢复可见性，**不自动提交刮削**；也不会在下次拉取时按关键词再忽略同一条。
- `read` / `unread`：写/清 `read_at`，幂等，不碰忽略状态，不入队。
- `delete`：永久删历史行，不影响已有任务与元数据；源再次返回同一 `item_key` 时重新算新条目。

列表默认 `state=active`（只看未忽略）但 `read=all`（已读未读都看）——默认值不对称是有意的，
调用方不传 `read` 时结果集不变。

`ignore_keywords` 是每源一张字面量列表：大小写不敏感子串，只匹配**标题与番号**，不匹配正文；
规范化时去空白、丢空串、按 casefold 去重，单条最长 64 字符、最多 50 条。保存这张列表时，
当前未忽略的匹配条目会被一并写上忽略。空列表等于关闭过滤。

### 七、只发现，不下载；入队优先级压低

`auto_enqueue` 只决定「发现新条目且解析出番号时是否入队」。关掉之后 `FeedItem` 照写，
刮削改由用户在历史表里手选。已见过的 `item_key` 不会因为后来打开这个开关而补入队。

自动入队的优先级是 `-1`，手动批量刮削是 `0`。上游注释写明理由：一次追赶可能吐出几十条，
不能让它抢占用户手动发起的刮削。

### 八、排序真值是「发布时间，回退创建时间」

`published_at` 只在首次写入或当时为空时回填，不随源更新；`description` 同理。
列表按 `coalesce(published_at, created_at), id` 排，并且建了同样表达式的索引——
上游注释强调表达式必须与 `ORDER BY` 完全一致才命中。

`created_at` 不能当主排序：同一次拉取里多条会落在同一秒。也正因为这样，
写入新条目时上游是 `for entry, number in reversed(new_entries)`——**倒序写库**，
让无日期的条目按旧→新拿到递增的 id。入队则仍按源给出的顺序。

## 未取得

- **来源清单**：上游不内置站点列表，`docs/dev/feeds.md` 只说「没有原生 RSS 的站点用站外适配器」，
  没有任何可直接抄的 JAV feed 地址。可用来源必须 Peach 自己取证，见 `docs/SOURCING.md`。
- **空壳资产**：上游根本没有这个概念——它的产物是一条刮削任务，元数据落在 `Metadata` 表，
  `MediaFile` 那边什么都不建。所以「没有媒体文件的资产怎么表示、算不算馆藏」在上游无对应物，
  Peach 侧要自己定，见 ADR。
- **配额与限流**：`FeedService` 除了每源一把锁之外没有任何速率控制，源之间是紧接着串行拉的。
  按出口 IP 计配额的站点（JavDB）在上游没有对应处置。

## Peach 采用

- Feed 只发现番号，不下载、不碰媒体文件；产物是一条刮削任务加一条「未入库」空壳。
- 两层去重：整份文档走 `ETag` / `Last-Modified`，条目走 `(源, item_key)` 唯一键，
  `item_key` 按 `guid` → `link` → `title` 回退。
- 解析只吃已下载的字节，HTTP 一律走 `peach.http`。
- 已读与忽略是两个独立的时间列，都不参与去重。
- 每源可覆盖番号正则，覆盖后不回退通用提取器。
- 失败也按间隔重排下次，错误写在源行上给页面看；`next_fetch_at` 在所有返回路径上都被写。
- 拉取时段内同一番号只派一次刮削。

## 有意差异

- **解析器用标准库**：上游用 `feedparser`。Peach 用 `xml.etree.ElementTree`，不为这件事引依赖——
  代价是要自己处理 RSS 2.0 与 Atom 的命名空间差异和日期格式，收益是依赖清单不变。
- **调度不另起循环**：上游为 Feed 专门起了一个 60 秒的 asyncio 循环。Peach 复用现有的
  `follow_scheduler`，Feed 只是它的又一个到期任务来源。
- **番号做全局去重**：上游只在「同一 tick、同一源」内合并相同番号，跨源和跨轮都不去重。
  Peach 的空壳是要落进资产表的，所以番号是全局唯一键——已存在的资产（不论真实还是空壳）
  不再建第二个壳。
- **限流是硬约束不是可选项**：JavDB 一类来源按出口 IP 计配额，Peach 侧的抓取按
  `peach-batch-jobs` 的预算与间隔走，不能像上游那样源接源紧着拉。
- **没有阅读器**：上游 `/feeds` 是一个完整的 RSS 阅读器（正文渲染、OPML 导入、分组伪路径、
  键盘导航）。Peach 不做阅读器——条目只在人物页与首页以「新作 · 未入库」的形态出现，
  源的增删在设置页。分组、OPML、正文渲染都不采用。
