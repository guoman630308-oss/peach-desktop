-- 「想要」清单：用户登记「这部我想要」的作品，按番号或关注条目认（待办第 40 条）。
--
-- 为什么另起一张表：三处来源形状不同。Feed 壳按番号唯一、关注条目按 `follow_item.id`、库外番号
-- 只有一个字符串；寻找更好版本（`asset_quality_goal`）的主键含 `asset_id`，只能挂在已入库的作品上。
-- 往其中任何一张加列都要给另外两种来源各开一个例外。
--
-- `code` 是登记时的写法，`code_key` 是 `normalise_code_key` 的结果，入库对账与去重都按它比。
-- 关注条目没有番号，按 `follow_item_id` 认；条目随来源一起删掉时置空，行上的快照还认得出是哪一部。
--
-- 快照列（标题、链接、封面地址、发行日、厂牌、女优）取自 Feed 壳、关注条目或取资料那一步，
-- 都是「某个来源这么说」的展示值，不是真相字段。`scraped_at`／`scrape_error` 记取资料那一步。
--
-- `state`：`wanted` 待找，`given_up` 老片查满次数无果暂时放弃，`acquired` 已入库。未发售不是一种
-- 状态，按 `release_date` 读的时候现算，到了发售日自然回到待找。
-- 查找计数（`search_count`、`last_search_*`、`given_up_at`）由下载模块每查一次记一笔，新片不计数。
--
-- 入库对账是代码判据确定的结果，按 ADR-0052 直接落库：`acquired_source` 是归属串，
-- `acquired_batch` 是批次号 `<source>@<登记时刻>`，整批撤回时按它认。`acquired_asset_id` 不挂外键：
-- 搬运接回与资源同步会删掉 `asset` 行，而扫描那条连接不开外键约束，挂了也拦不住悬空，
-- 对账记录本身要留着（同 `record_rehome` 的两个 asset id）。
CREATE TABLE want_item(
  id INTEGER PRIMARY KEY,
  code TEXT,
  code_key TEXT,
  follow_item_id INTEGER REFERENCES follow_item(id) ON DELETE SET NULL,
  origin TEXT NOT NULL CHECK(origin IN ('code','feed','follow')),
  title TEXT,
  link TEXT,
  cover_url TEXT,
  release_date TEXT,
  studio TEXT,
  performers TEXT,
  scraped_at TEXT,
  scrape_error TEXT,
  state TEXT NOT NULL DEFAULT 'wanted' CHECK(state IN ('wanted','given_up','acquired')),
  search_count INTEGER NOT NULL DEFAULT 0,
  last_search_at TEXT,
  last_search_outcome TEXT CHECK(last_search_outcome IN ('found','none','error')),
  last_search_note TEXT,
  given_up_at TEXT,
  acquired_asset_id INTEGER,
  acquired_at TEXT,
  acquired_source TEXT,
  acquired_batch TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 同一个番号、同一条关注条目只登记一次；登记、对账、Feed 卡上的「想要过没有」都按这两个键查。
CREATE UNIQUE INDEX idx_want_item_code_key ON want_item(code_key) WHERE code_key IS NOT NULL;
CREATE UNIQUE INDEX idx_want_item_follow ON want_item(follow_item_id) WHERE follow_item_id IS NOT NULL;

-- 清单页按状态分段、段内按登记时间倒序；整批撤回按归属串找。
CREATE INDEX idx_want_item_state ON want_item(state, created_at);
CREATE INDEX idx_want_item_acquired_source ON want_item(acquired_source) WHERE acquired_source IS NOT NULL;

INSERT INTO ledger_revision(tbl) VALUES ('want_item');

CREATE TRIGGER rev_want_item_insert AFTER INSERT ON want_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='want_item';
END;

CREATE TRIGGER rev_want_item_update AFTER UPDATE ON want_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='want_item';
END;

CREATE TRIGGER rev_want_item_delete AFTER DELETE ON want_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='want_item';
END;
