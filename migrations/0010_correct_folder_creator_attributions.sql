-- 仅应用两组已有文件级证据的纠正；其余旧目录投影进入复核 CSV。
INSERT OR IGNORE INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)
SELECT 'creator','suzuq','suzuq',strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now')
WHERE EXISTS (
  SELECT 1 FROM asset WHERE replace(path,'/','\') LIKE 'B:\云下载\足交仙人\%'
);

INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
SELECT id,'Suzyq','suzyq','filename',0.95
FROM entity WHERE kind='creator' AND normalized_name='suzuq';

DELETE FROM asset_entity
WHERE role='creator' AND source='legacy:asset'
  AND entity_id IN (
    SELECT id FROM entity WHERE kind='creator' AND canonical_name='足交仙人'
  )
  AND asset_id IN (
    SELECT id FROM asset
    WHERE replace(path,'/','\') LIKE 'B:\云下载\足交仙人\%'
  );

INSERT OR IGNORE INTO asset_entity(
  asset_id,entity_id,role,source,confidence,metadata_json,first_seen_at,last_seen_at
)
SELECT a.id,e.id,'creator','user:watermark',1.0,
       '{"evidence":"user-confirmed watermark; Suzyq filenames"}',
       strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM asset a JOIN entity e ON e.kind='creator' AND e.normalized_name='suzuq'
WHERE replace(a.path,'/','\') LIKE 'B:\云下载\足交仙人\%';

UPDATE asset SET creator='suzuq'
WHERE replace(path,'/','\') LIKE 'B:\云下载\足交仙人\%';

DELETE FROM asset_entity
WHERE role='creator' AND source='legacy:asset'
  AND entity_id IN (
    SELECT id FROM entity WHERE kind='creator' AND canonical_name='捅主任'
  )
  AND asset_id IN (
    SELECT id FROM asset
    WHERE replace(path,'/','\') LIKE 'B:\MVP\捅主任\TokyoDolls\%'
  );

UPDATE asset
SET creator=(
  SELECT e.canonical_name
  FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id
  WHERE ae.asset_id=asset.id AND e.kind='creator'
  ORDER BY ae.confidence DESC,e.canonical_name
  LIMIT 1
)
WHERE replace(path,'/','\') LIKE 'B:\MVP\捅主任\TokyoDolls\%';

DELETE FROM entity
WHERE kind='creator' AND canonical_name='足交仙人'
  AND NOT EXISTS (SELECT 1 FROM asset_entity ae WHERE ae.entity_id=entity.id);
