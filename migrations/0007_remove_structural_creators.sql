-- Remove three reviewed folder/category labels that the legacy importer flattened
-- into creator truth. Current scanners no longer infer creators from folders.
DELETE FROM asset_entity
WHERE entity_id IN (
  SELECT id FROM entity
  WHERE kind='creator' AND canonical_name IN ('门槛','视频','宣傳文件','宣传文件')
);

UPDATE asset
SET creator=(
  SELECT e.canonical_name
  FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id
  WHERE ae.asset_id=asset.id AND e.kind='creator'
  ORDER BY ae.confidence DESC,e.canonical_name
  LIMIT 1
)
WHERE creator IN ('门槛','视频','宣傳文件','宣传文件');
