-- `asce` is a reviewed mixed download/collection folder, not a creator.
-- Remove only the low-confidence creator-board assertions derived from that
-- false grouping. Preserve all media and independently sourced/name tags.
DELETE FROM asset_tag
WHERE source='vision_creator'
  AND asset_id IN (
    SELECT id FROM asset WHERE lower(trim(creator))='asce'
  );

DELETE FROM asset_entity
WHERE source='vision_creator'
  AND asset_id IN (
    SELECT id FROM asset WHERE lower(trim(creator))='asce'
  );

DELETE FROM asset_entity
WHERE entity_id IN (
  SELECT id FROM entity
  WHERE kind='creator' AND lower(trim(canonical_name))='asce'
);

UPDATE asset
SET creator=(
  SELECT e.canonical_name
  FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id
  WHERE ae.asset_id=asset.id AND e.kind='creator'
  ORDER BY ae.confidence DESC,e.canonical_name
  LIMIT 1
)
WHERE lower(trim(creator))='asce';

DELETE FROM entity
WHERE kind='creator' AND lower(trim(canonical_name))='asce'
  AND NOT EXISTS (SELECT 1 FROM asset_entity ae WHERE ae.entity_id=entity.id);
