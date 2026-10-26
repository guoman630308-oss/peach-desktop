-- peach:foreign_keys=off
--
-- 事务所成为实体，并记下「这个人现在归谁」。
--
-- 为什么要这么改：事务所此前只是 `entity.metadata_json.agency` 里的一个字符串。
-- 字符串搜不到、点不开、也没有反向的「这家有哪些人」，于是「Capsule Agency」在库里
-- 存在却找不着。它和厂牌、系列一样是一个有名字、有官网、有成员的规范身份，缺的只是
-- 一种 `kind`。
--
-- 为什么要重建 `entity`：`kind` 的 CHECK 是表定义的一部分，SQLite 没有改 CHECK 的
-- ALTER。重建就要 DROP，而外键开着时 DROP TABLE 会先做一次隐式 DELETE FROM，
-- `entity_alias`、`entity_link`、`entity_external_ref`、`entity_search_term`、
-- `asset_entity` 的 CASCADE 会真的执行——0024 已经把这个陷阱写下来并推迟。文件头那行
-- `-- peach:foreign_keys=off` 是 `migrations.py` 认得的申明，跑完它会强制一次
-- `PRAGMA foreign_key_check`，有孤儿行就整批失败。
--
-- 事务所不进 `asset_entity`：作品是女优拍的，事务所是女优的归属，把它挂到作品上会
-- 让同一份事实存两遍，而两遍会漂移。资料页要的「这家的作品」由成员关系推出来。

CREATE TABLE entity_new(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('performer','studio','tag','creator','series','agency')),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(kind, normalized_name)
);

INSERT INTO entity_new(id,kind,canonical_name,normalized_name,metadata_json,created_at,updated_at)
SELECT id,kind,canonical_name,normalized_name,metadata_json,created_at,updated_at FROM entity;

DROP TABLE entity;

-- 改名要走 legacy 模式。默认模式下 RENAME 会重新解析整个 schema 并把别处对旧名的
-- 引用改写过来，而此刻 `entity` 还不存在：`asset_search_source` 视图和几个 asset_search
-- 触发器都引用它，重新解析当场失败（`error in trigger asset_search_asset_insert:
-- no such table: main.entity`）。这里没有任何东西引用 `entity_new`，不需要那次改写。
PRAGMA legacy_alter_table=ON;

ALTER TABLE entity_new RENAME TO entity;

PRAGMA legacy_alter_table=OFF;

-- DROP TABLE 连挂在这张表上的触发器一起带走了，原样补回来。它属于 0009 的检索索引
-- 维护链：规范名一改，这条实体名下所有作品的 FTS 行要重建。
CREATE TRIGGER asset_search_entity_update AFTER UPDATE OF canonical_name ON entity BEGIN
  DELETE FROM asset_search WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id=NEW.id
  );
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source
  WHERE asset_id IN (SELECT asset_id FROM asset_entity WHERE entity_id=NEW.id);
END;

-- 「这个人现在归哪家」。主键在成员一侧，所以一个人只有一条现役归属：移籍是覆盖，
-- 不是再加一行。用户定的口径就是保留最新的那一家，历史归属由 Git 之外的备份和
-- `checked_at` 交代，不在这张表里累积。
CREATE TABLE entity_membership(
  member_id INTEGER PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  agency_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  checked_at TEXT NOT NULL
);

-- 反向那一问（「这家有哪些人」）是事务所资料页的主查询，没有索引就是全表扫。
CREATE INDEX idx_entity_membership_agency ON entity_membership(agency_id);
