-- 把「删掉父行时子行怎么办」写进 schema，并补两条由真实查询计划证明的索引。
--
-- 为什么现在补：运行时 sqlite 连接不开 `PRAGMA foreign_keys`（只有 `migrations.py`
-- 迁移时开），物理删除靠 `web_batch.ASSET_REFERENCE_TABLES` 显式逐表删。所以这里补的
-- CASCADE / SET NULL **不是删除路径，是安全网**：它约束的是那些确实开了 foreign_keys
-- 的入口——迁移本身，以及 `import_stash_entities.py`、`install_entity_links.py`、
-- `localize_*.py`、`normalize_link_hosts.py` 这些运维脚本。声明缺失时，这些入口
-- 要么留下孤儿行，要么在 `PRAGMA foreign_key_check` 里才被发现。
-- `ASSET_REFERENCE_TABLES` 与 `web_playlists`、`entities.merge_entity` 里的显式 DELETE
-- 一律保留不动：运行时 foreign_keys 仍是关的，删掉它们就等于没人删子表。
--
-- 每条的判据（按现有删除代码的真实行为，不按直觉）：
--   asset_tag.asset_id            -> CASCADE   资产的附属事实；已在 ASSET_REFERENCE_TABLES 里
--                                              被 purge_assets 显式删，CASCADE 只是把既有
--                                              行为写进 schema。原来连 REFERENCES 都没有。
--   media_binding.asset_id        -> CASCADE   同上；后端外部 id 绑定，资产不在就没有意义。
--   activity_event.asset_id       -> CASCADE   同上；NOT NULL，事件不能指向不存在的资产。
--   activity_event.profile_id     -> SET NULL  这一列是**可空**的，全库其它 profile_id 都
--                                              NOT NULL。观看行为历史属于 AGENTS.md 的保留
--                                              清单，不能因为删掉一个 profile 就消失；丢掉
--                                              的只是归属。
--   asset_tag_preference.profile_id -> CASCADE profile 私有状态（「我隐藏了这个标签」），
--                                              与 watch_queue / asset_preference /
--                                              asset_quality_goal / playlist 同类，那四张
--                                              表 0003/0006/0011/0017 都已是 CASCADE。
--                                              原来连 REFERENCES 都没有。
--   follow_playback.profile_id    -> CASCADE   NOT NULL 且在主键里，SET NULL 不合法；按上面
--                                              同一条 profile 私有状态的约定走 CASCADE。
--
-- 没有改 `profile.user_id -> app_user(id)`，这是本迁移唯一保留 NO ACTION 的外键，两条理由：
--   1) 语义上它本来就该是 RESTRICT，而 SQLite 的即时（非 deferred）外键下 NO ACTION 已经
--      实现了 RESTRICT：删 app_user 会直接报 FOREIGN KEY constraint failed。Peach 从不用
--      deferred 外键，两者行为没有可观察差别，改写只是换个词。
--   2) 改它必须重建 `profile`，而重建要 DROP TABLE profile；`foreign_keys=ON` 时
--      DROP TABLE 会先做一次隐式 DELETE FROM，于是 watch_queue / asset_preference /
--      asset_quality_goal / playlist / follow_playback / asset_tag_preference 的行会被
--      真的 CASCADE 删掉。这不是风险，是必然的数据丢失。要写出字面的 RESTRICT，得先让
--      `migrations.py` 支持在 foreign_keys=OFF 下跑重建，属于另一次改动。
--
-- PRAGMA foreign_keys 怎么处理：**动不了**。`migrations.py` 在循环外执行
-- `PRAGMA foreign_keys=ON`，每个迁移的 SQL 被包进一条 `BEGIN IMMEDIATE; ... COMMIT;`
-- 的 executescript，而 `PRAGMA foreign_keys` 在事务里是 no-op。所以 SQLite 官方
-- 「Making Other Kinds Of Table Schema Changes」12 步法的第 1 步（关外键）和第 12 步
-- （开回来）在这里都执行不了，只能在 foreign_keys=ON 下重建。这样做安全的前提是下面
-- 五张表**都是纯子表**：没有任何表、视图或触发器引用它们。因此
--   - DROP TABLE 的隐式 DELETE 没有下游可以 CASCADE 到；
--   - ALTER TABLE RENAME 在 foreign_keys=ON 时会改写其它表指向旧名的 REFERENCES 子句，
--     而没人引用 `*_0024` 临时名，改写集合为空；
--   - 0004 的 FTS 触发器全部挂在 asset / asset_entity / entity / entity_alias /
--     entity_search_term 上，触发器体和 `asset_search_source` 视图也都不提这五张表，
--     所以 RENAME 重新解析整个 schema 时不会解析失败，`asset_search` 一行都不用动。
-- 每张表的 create → insert → drop → rename → 重建索引连续写完，不与别的表交错：
-- 中间态里没有被引用而缺失的表名。
--
-- 孤儿行不静默清理。`asset_tag.asset_id` 与 `asset_tag_preference.profile_id` 原先没有
-- 外键，可能已经存在指向不存在父行的数据。下面的 INSERT ... SELECT 在 foreign_keys=ON
-- 下会直接报 FOREIGN KEY constraint failed 并整体回滚，而不是丢掉那些行——发生时先查清
-- 孤儿来源，再由人决定怎么处理。2026-09-03 在本机真实账本副本上核对：
-- integrity_check=ok、foreign_key_check=0、asset_tag 孤儿 0、asset_tag_preference 孤儿 0。
--
-- 索引：DROP TABLE 会连带删掉表上的索引，所以迁移建立的索引在下面按原名原样重建。
-- 唯一的例外是 `asset_tag` 上的 `ix_tag`、`ix_tag_tag`、`ix_tag_asset`——它们只存在于本机
-- 真实账本，任何一版迁移都没创建过（Stash 时期手工留下的），而且三条都是纯冗余：
-- `ix_tag`、`ix_tag_tag` 与 0000 建的 `idx_asset_tag_tag` 完全相同，`ix_tag_asset(asset_id)`
-- 被 `UNIQUE(asset_id, tag)` 的自动索引前缀覆盖。这里不重建它们：把它们写进迁移，会让每个
-- 全新安装凭空多出三条冗余索引，恰好是反方向。结果是真实账本与新库的 schema 收敛一致。
-- `idx_media_binding_backend_external` 同样与 `UNIQUE(backend, external_id)` 的自动索引
-- 重复，但它是 0001 建的、每个库都有，属于另一件事，这次原样保留。

-- ---- asset_tag ------------------------------------------------------------
CREATE TABLE asset_tag_0024(
  asset_id INTEGER REFERENCES asset(id) ON DELETE CASCADE,
  tag TEXT,
  confidence REAL DEFAULT 1.0,
  source TEXT,
  UNIQUE(asset_id, tag)
);

INSERT INTO asset_tag_0024(asset_id,tag,confidence,source)
SELECT asset_id,tag,confidence,source FROM asset_tag;

DROP TABLE asset_tag;
ALTER TABLE asset_tag_0024 RENAME TO asset_tag;

CREATE INDEX idx_asset_tag_tag ON asset_tag(tag);

-- ---- media_binding --------------------------------------------------------
CREATE TABLE media_binding_0024(
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  backend TEXT NOT NULL,
  external_id TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 100,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  last_synced_at TEXT,
  PRIMARY KEY(asset_id, backend),
  UNIQUE(backend, external_id)
);

INSERT INTO media_binding_0024(
  asset_id,backend,external_id,priority,metadata_json,last_synced_at)
SELECT asset_id,backend,external_id,priority,metadata_json,last_synced_at
FROM media_binding;

DROP TABLE media_binding;
ALTER TABLE media_binding_0024 RENAME TO media_binding;

CREATE INDEX idx_media_binding_backend_external
ON media_binding(backend, external_id);

-- ---- activity_event -------------------------------------------------------
CREATE TABLE activity_event_0024(
  id INTEGER PRIMARY KEY,
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  profile_id TEXT REFERENCES profile(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  position_seconds REAL,
  duration_seconds REAL,
  delta_seconds REAL,
  value_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL,
  session_id TEXT,
  event_key TEXT UNIQUE
);

INSERT INTO activity_event_0024(
  id,asset_id,profile_id,kind,occurred_at,position_seconds,duration_seconds,
  delta_seconds,value_json,source,session_id,event_key)
SELECT id,asset_id,profile_id,kind,occurred_at,position_seconds,duration_seconds,
       delta_seconds,value_json,source,session_id,event_key
FROM activity_event;

DROP TABLE activity_event;
ALTER TABLE activity_event_0024 RENAME TO activity_event;

CREATE INDEX idx_activity_event_asset_time
ON activity_event(asset_id, occurred_at DESC);

CREATE INDEX idx_activity_event_profile_time
ON activity_event(profile_id, occurred_at DESC);

-- ---- asset_tag_preference -------------------------------------------------
CREATE TABLE asset_tag_preference_0024(
  profile_id TEXT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  normalized_tag TEXT NOT NULL,
  hidden INTEGER NOT NULL DEFAULT 1 CHECK(hidden IN (0,1)),
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id,asset_id,normalized_tag)
);

INSERT INTO asset_tag_preference_0024(
  profile_id,asset_id,normalized_tag,hidden,updated_at)
SELECT profile_id,asset_id,normalized_tag,hidden,updated_at
FROM asset_tag_preference;

DROP TABLE asset_tag_preference;
ALTER TABLE asset_tag_preference_0024 RENAME TO asset_tag_preference;

CREATE INDEX idx_asset_tag_preference_asset
ON asset_tag_preference(asset_id,profile_id,hidden);

-- ---- follow_playback ------------------------------------------------------
CREATE TABLE follow_playback_0024(
  follow_item_id INTEGER NOT NULL REFERENCES follow_item(id) ON DELETE CASCADE,
  profile_id TEXT NOT NULL DEFAULT 'local-default'
             REFERENCES profile(id) ON DELETE CASCADE,
  play_count INTEGER NOT NULL DEFAULT 0 CHECK(play_count >= 0),
  play_seconds REAL NOT NULL DEFAULT 0 CHECK(play_seconds >= 0),
  max_reached REAL NOT NULL DEFAULT 0 CHECK(max_reached >= 0 AND max_reached <= 1),
  last_played REAL,
  PRIMARY KEY(follow_item_id, profile_id)
);

INSERT INTO follow_playback_0024(
  follow_item_id,profile_id,play_count,play_seconds,max_reached,last_played)
SELECT follow_item_id,profile_id,play_count,play_seconds,max_reached,last_played
FROM follow_playback;

DROP TABLE follow_playback;
ALTER TABLE follow_playback_0024 RENAME TO follow_playback;

CREATE INDEX idx_follow_playback_profile_time
ON follow_playback(profile_id, last_played DESC);

-- ---- 索引：只补真实查询计划里出现 SCAN、且有明确复合键的两处 -------------------
-- 判据取自 2026-09-03 本机真实账本只读副本（asset 75500 行、asset_tag 82847 行）上的
-- EXPLAIN QUERY PLAN，三次取最快。没有 sqlite_stat1（本库从未 ANALYZE），计划来自
-- SQLite 3.50.4 的默认估算。
--
-- 1) 回收站列表与计数（`web_catalog.q_items` 的 state=trash 分支、`web_stats` 的
--    trash 计数）：`WHERE a.disposal='trash'`
--    前：SCAN a                                     33.0 ms / 30.5 ms
--    后：SEARCH a USING INDEX idx_asset_disposal_id  0.1 ms /  0.0 ms
--    做成部分索引：全库 75500 行里 disposal 非空的只有 34 行（全是 'trash'），
--    整列索引 99.95% 是 NULL 条目，白付写入代价。首页那条
--    `(disposal IS NULL OR disposal<>'trash')` 推不出 `disposal IS NOT NULL`，
--    用不上这条索引也不该用——它要的是补集。
CREATE INDEX idx_asset_disposal_id ON asset(disposal, id DESC)
WHERE disposal IS NOT NULL;

-- 2) 统计页的标签来源分布与标签覆盖率（`web_stats` 的 tag_source / tag_cov）：
--    `SELECT source,count(*),count(DISTINCT asset_id) FROM asset_tag GROUP BY source`
--    前：SCAN asset_tag + TEMP B-TREE FOR GROUP BY + TEMP B-TREE FOR count(DISTINCT)
--        22.1 ms
--    后：SCAN asset_tag USING COVERING INDEX idx_asset_tag_source_asset，GROUP BY
--        直接吃索引序，只剩 ORDER BY 的临时 B 树   4.1 ms
--    复合键的次序是 (source, asset_id) 而不是反过来：GROUP BY 在 source 上，
--    组内 asset_id 有序才能免掉 count(DISTINCT) 的临时 B 树。
CREATE INDEX idx_asset_tag_source_asset ON asset_tag(source, asset_id);
