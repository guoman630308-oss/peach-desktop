--
-- 番号发现源：订阅、源内已见条目、未入库的番号壳（ADR-0042）。
--
-- 为什么这么改：「关注的女优出新片了」这件事现在只能靠人自己去外面看。刮削链是被动的，
-- 先有文件才有番号；关注那一套盯的是创作者的图文与视频帖，不认番号。Feed 补上的正是
-- 番号这一侧的「新作」概念，产物只有番号，不下载、不碰媒体文件。
--
-- 为什么不在 `asset` 上加一个「文件缺失」状态列：`asset` 的语义是「盘上有这个文件」，
-- 扫描按 `last_seen` 落后表达文件消失、资源同步现场 stat 并清掉真不在的行、整理按
-- `asset.path` 搬文件、抽帧按路径开 FFmpeg、馆藏统计数的是这张表的行数。塞一行没有文件
-- 的记录进去，等于要在这五处各加一个例外，而每处例外的判据都不一样。分成两张表之后，
-- 「不进整理、不进抽帧、不算馆藏」由表结构保证，不靠纪律。
--
-- `feed_discovery` 不登记进 `web_batch.ASSET_REFERENCE_TABLES`：那张名单是「物理删除资产时
-- 要清的引用」，而发现记录不引用 `asset`，两者只按番号在查询时对上。

-- 一条订阅。`kind` 决定怎么解析：`rss` 是原生 RSS/Atom，`javdb-actor` 是把 JavDB 演员页
-- 当伪 Feed 抓（取证见 `docs/SOURCING.md`「番号发现源（Feed）」）。
-- `entity_id` 只有按人绑定的源才有，它让演员页发现的新作直接落到那个人的页面上。
-- `etag` / `last_modified` 是整份文档那一层去重，**不能当作可依赖的东西**：sukebei 两个
-- 响应头一个都不给，那一层对它完全不生效。
CREATE TABLE feed_source (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL,
  entity_id INTEGER REFERENCES entity(id) ON DELETE SET NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  interval_minutes INTEGER NOT NULL DEFAULT 360,
  etag TEXT,
  last_modified TEXT,
  next_fetch_at TEXT,
  last_fetched_at TEXT,
  last_error TEXT,
  last_seen_count INTEGER NOT NULL DEFAULT 0,
  last_new_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(url)
);

-- 到期扫描只看启用的源，部分索引因此只覆盖它们。
CREATE INDEX idx_feed_source_due ON feed_source(next_fetch_at) WHERE enabled=1;

-- 去重第一层：这个源的这一条见过没有。`item_key` 按 guid → link → title 回退。
-- 解不出番号的条目照样记一行（`code` 为空），否则每轮都会对同一个垃圾标题重试一遍。
CREATE TABLE feed_item (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES feed_source(id) ON DELETE CASCADE,
  item_key TEXT NOT NULL,
  code TEXT,
  title TEXT,
  link TEXT,
  published_at TEXT,
  first_seen TEXT NOT NULL,
  UNIQUE(source_id, item_key)
);

CREATE INDEX idx_feed_item_code ON feed_item(code) WHERE code IS NOT NULL;

-- 去重第二层：这个番号需不需要一个壳。`code` 全表唯一，建壳前还要先查 `asset`——
-- 已入库的番号不建。两个源同时报同一个番号只会有一个壳，但两条 `feed_item` 都留着，
-- 因为它们各自是各自源的去重真值。
--
-- `read_at` / `ignored_at` 彼此正交，也都不参与唯一约束：忽略保留去重关系，后续拉取
-- 不会重建，这正是忽略的意义；取消忽略只恢复可见性，不自动派刮削。
--
-- 标题、封面地址、发行日、厂牌与女优名是刮削后继写回来的展示值，不是真相字段：
-- 壳上的每个值都只是「某个来源这么说」，用户复核的时机是文件真的到手、建成 `asset` 之后。
CREATE TABLE feed_discovery (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  source_id INTEGER REFERENCES feed_source(id) ON DELETE SET NULL,
  title TEXT,
  link TEXT,
  cover_url TEXT,
  release_date TEXT,
  studio TEXT,
  performers TEXT,
  scraped_at TEXT,
  scrape_error TEXT,
  discovered_at TEXT NOT NULL,
  read_at TEXT,
  ignored_at TEXT,
  UNIQUE(code)
);

-- 首页那一块按发现时间倒序取未忽略的行。
CREATE INDEX idx_feed_discovery_recent ON feed_discovery(discovered_at) WHERE ignored_at IS NULL;

-- 壳与实体的关联：按人绑定的源直接带上，其余由刮削取到女优名之后匹配。
-- 人物页那一块按 `entity_id` 取，所以索引把它放在前面。
CREATE TABLE feed_discovery_entity (
  discovery_id INTEGER NOT NULL REFERENCES feed_discovery(id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  PRIMARY KEY(discovery_id, entity_id)
);

CREATE INDEX idx_feed_discovery_entity_entity
  ON feed_discovery_entity(entity_id, discovery_id);
