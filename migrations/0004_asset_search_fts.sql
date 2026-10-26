CREATE VIRTUAL TABLE IF NOT EXISTS asset_search USING fts5(
  asset_id UNINDEXED,
  name,
  code,
  entities,
  search_terms,
  tokenize='trigram'
);

CREATE VIEW IF NOT EXISTS asset_search_source AS
SELECT
  a.id AS asset_id,
  COALESCE(a.name,'') AS name,
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

INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
SELECT asset_id,name,code,entities,search_terms FROM asset_search_source;

CREATE TRIGGER asset_search_asset_insert AFTER INSERT ON asset BEGIN
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source WHERE asset_id=NEW.id;
END;

CREATE TRIGGER asset_search_asset_update AFTER UPDATE OF name,code ON asset BEGIN
  DELETE FROM asset_search WHERE asset_id=OLD.id;
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source WHERE asset_id=NEW.id;
END;

CREATE TRIGGER asset_search_asset_delete AFTER DELETE ON asset BEGIN
  DELETE FROM asset_search WHERE asset_id=OLD.id;
END;

CREATE TRIGGER asset_search_relation_insert AFTER INSERT ON asset_entity BEGIN
  DELETE FROM asset_search WHERE asset_id=NEW.asset_id;
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source WHERE asset_id=NEW.asset_id;
END;

CREATE TRIGGER asset_search_relation_delete AFTER DELETE ON asset_entity BEGIN
  DELETE FROM asset_search WHERE asset_id=OLD.asset_id;
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source WHERE asset_id=OLD.asset_id;
END;

CREATE TRIGGER asset_search_relation_update AFTER UPDATE OF asset_id,entity_id ON asset_entity BEGIN
  DELETE FROM asset_search WHERE asset_id IN (OLD.asset_id,NEW.asset_id);
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source
  WHERE asset_id IN (OLD.asset_id,NEW.asset_id);
END;

CREATE TRIGGER asset_search_entity_update AFTER UPDATE OF canonical_name ON entity BEGIN
  DELETE FROM asset_search WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id=NEW.id
  );
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source
  WHERE asset_id IN (SELECT asset_id FROM asset_entity WHERE entity_id=NEW.id);
END;

CREATE TRIGGER asset_search_alias_insert AFTER INSERT ON entity_alias BEGIN
  DELETE FROM asset_search WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id=NEW.entity_id
  );
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source
  WHERE asset_id IN (SELECT asset_id FROM asset_entity WHERE entity_id=NEW.entity_id);
END;

CREATE TRIGGER asset_search_alias_delete AFTER DELETE ON entity_alias BEGIN
  DELETE FROM asset_search WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id=OLD.entity_id
  );
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source
  WHERE asset_id IN (SELECT asset_id FROM asset_entity WHERE entity_id=OLD.entity_id);
END;

CREATE TRIGGER asset_search_alias_update AFTER UPDATE OF alias,entity_id ON entity_alias BEGIN
  DELETE FROM asset_search WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id IN (OLD.entity_id,NEW.entity_id)
  );
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source
  WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id IN (OLD.entity_id,NEW.entity_id)
  );
END;

CREATE TRIGGER asset_search_term_insert AFTER INSERT ON entity_search_term BEGIN
  DELETE FROM asset_search WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id=NEW.entity_id
  );
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source
  WHERE asset_id IN (SELECT asset_id FROM asset_entity WHERE entity_id=NEW.entity_id);
END;

CREATE TRIGGER asset_search_term_delete AFTER DELETE ON entity_search_term BEGIN
  DELETE FROM asset_search WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id=OLD.entity_id
  );
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source
  WHERE asset_id IN (SELECT asset_id FROM asset_entity WHERE entity_id=OLD.entity_id);
END;

CREATE TRIGGER asset_search_term_update AFTER UPDATE OF term,entity_id ON entity_search_term BEGIN
  DELETE FROM asset_search WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id IN (OLD.entity_id,NEW.entity_id)
  );
  INSERT INTO asset_search(asset_id,name,code,entities,search_terms)
  SELECT asset_id,name,code,entities,search_terms FROM asset_search_source
  WHERE asset_id IN (
    SELECT asset_id FROM asset_entity WHERE entity_id IN (OLD.entity_id,NEW.entity_id)
  );
END;
