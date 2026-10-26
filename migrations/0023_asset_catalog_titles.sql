ALTER TABLE asset ADD COLUMN catalog_title TEXT;
ALTER TABLE asset ADD COLUMN original_title TEXT;

-- 复用既有 FTS 结构，把已复核标题并入 name 列，避免再造一套标题搜索。
DROP TRIGGER IF EXISTS asset_search_asset_update;
DROP VIEW IF EXISTS asset_search_source;

CREATE VIEW asset_search_source AS
SELECT
  a.id AS asset_id,
  trim(
    COALESCE(a.name,'') || ' ' ||
    COALESCE(a.catalog_title,'') || ' ' ||
    COALESCE(a.original_title,'')
  ) AS name,
  COALESCE(a.code,'') AS code,
  COALESCE((
    SELECT group_concat(value,' ') FROM (
      SELECT DISTINCT e.canonical_name AS value
      FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id
      WHERE ae.asset_id=a.id
      UNION
      SELECT DISTINCT alias.alias AS value
      FROM asset_entity ae JOIN entity_alias alias ON alias.entity_id=ae.entity_id
      WHERE ae.asset_id=a.id
    )
  ),'') AS entities,
  COALESCE((
    SELECT group_concat(DISTINCT term.term)
    FROM asset_entity ae JOIN entity_search_term term ON term.entity_id=ae.entity_id
    WHERE ae.asset_id=a.id
  ),'') AS search_terms
FROM asset a;

CREATE TRIGGER asset_search_asset_update
AFTER UPDATE OF name,code,catalog_title,original_title ON asset BEGIN
  DELETE FROM asset_search WHERE asset_id=OLD.id;
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms
  FROM asset_search_source WHERE asset_id=NEW.id;
END;

DELETE FROM asset_search;
INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
SELECT asset_id,name,code,entities,search_terms FROM asset_search_source;
