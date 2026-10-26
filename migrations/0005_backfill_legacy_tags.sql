-- 0002 只迁移了当时已有的 asset_tag；其后旧脚本继续写入的标签需要补入规范实体层。
-- 先暂停逐行 FTS 刷新，批量回填后按受影响资源重建一次，避免 O(关系数 x 聚合成本)。
DROP TRIGGER IF EXISTS asset_search_relation_insert;

INSERT OR IGNORE INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)
SELECT CASE WHEN tag LIKE '演员:%' THEN 'performer' ELSE 'tag' END,
       CASE WHEN tag LIKE '演员:%' THEN trim(substr(tag,4)) ELSE trim(tag) END,
       lower(CASE WHEN tag LIKE '演员:%' THEN trim(substr(tag,4)) ELSE trim(tag) END),
       strftime('%Y-%m-%dT%H:%M:%fZ','now'),
       strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM asset_tag
WHERE trim(CASE WHEN tag LIKE '演员:%' THEN substr(tag,4) ELSE tag END)<>'';

INSERT OR IGNORE INTO asset_entity(asset_id,entity_id,role,source,confidence,
                                   metadata_json,first_seen_at,last_seen_at)
SELECT t.asset_id,e.id,
       CASE WHEN t.tag LIKE '演员:%' THEN 'performer' ELSE 'tag' END,
       COALESCE(t.source,'legacy:asset_tag'),COALESCE(t.confidence,1.0),
       '{"migration":"0005_backfill_legacy_tags"}',
       strftime('%Y-%m-%dT%H:%M:%fZ','now'),
       strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM asset_tag t
JOIN entity e ON e.kind=CASE WHEN t.tag LIKE '演员:%' THEN 'performer' ELSE 'tag' END
 AND e.normalized_name=lower(CASE WHEN t.tag LIKE '演员:%'
                                  THEN trim(substr(t.tag,4)) ELSE trim(t.tag) END)
WHERE trim(CASE WHEN t.tag LIKE '演员:%' THEN substr(t.tag,4) ELSE t.tag END)<>'';

DELETE FROM asset_search
WHERE asset_id IN (SELECT DISTINCT asset_id FROM asset_tag);

INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
SELECT asset_id,name,code,entities,search_terms
FROM asset_search_source
WHERE asset_id IN (SELECT DISTINCT asset_id FROM asset_tag);

CREATE TRIGGER asset_search_relation_insert AFTER INSERT ON asset_entity BEGIN
  DELETE FROM asset_search WHERE asset_id=NEW.asset_id;
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms
  FROM asset_search_source WHERE asset_id=NEW.asset_id;
END;
