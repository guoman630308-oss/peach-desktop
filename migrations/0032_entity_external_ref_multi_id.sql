-- 同一个人在同一个站点可以有多个页面。
--
-- 为什么要这么改：0002 给 `entity_external_ref` 写的 `UNIQUE(entity_id, provider,
-- external_kind)` 断言的是「一个实体在一个站点只有一个号」。javdb 上不成立——同一位
-- 女优会因为改艺名、录入重复或跨厂牌收录而有两个演员页（实测 `释爱丽丝` 的 `d45k9`
-- 与 `ZX5z7` 都在线），两个页面下挂的作品各不相同。旧约束下第二条只能被丢掉，于是
-- 资料页的外部入口永远少一半，回填脚本也只能把这种情况记成「冲突」搁置。
--
-- 放宽成 `UNIQUE(entity_id, provider, external_kind, external_id)`：同一实体在同一站点
-- 可以有多条引用，同一条引用仍然不能重复登记。`PRIMARY KEY(provider, external_kind,
-- external_id)` 原样保留，它断言的是另一件事——站点上的一个 id 只能属于一位实体，
-- 这条仍然要成立，两个人认领同一个演员页是真的错。
--
-- 外键不用关：这张表只是 `entity` 的子表，没有任何表引用它，所以 DROP 时那次隐式
-- `DELETE FROM` 不会级联到别处；新表的 `entity_id` 外键在 INSERT SELECT 时照常校验，
-- 留着更好。
--
-- `entities.merge_entity` 跟着松：它那句 `UPDATE OR IGNORE` 原先会把 source 侧同站点的
-- 引用整条丢掉，现在只有连 `external_id` 都一样的重复行才丢，合并两个实体时两边的
-- 站点页都留得住。

CREATE TABLE entity_external_ref_new(
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  external_kind TEXT NOT NULL,
  external_id TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  last_synced_at TEXT,
  PRIMARY KEY(provider, external_kind, external_id),
  UNIQUE(entity_id, provider, external_kind, external_id)
);

INSERT INTO entity_external_ref_new(
  entity_id, provider, external_kind, external_id, metadata_json, last_synced_at)
SELECT entity_id, provider, external_kind, external_id, metadata_json, last_synced_at
FROM entity_external_ref;

DROP TABLE entity_external_ref;
ALTER TABLE entity_external_ref_new RENAME TO entity_external_ref;
