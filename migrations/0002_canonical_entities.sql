CREATE TABLE IF NOT EXISTS entity(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('performer','studio','tag','creator','series')),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(kind, normalized_name)
);

CREATE TABLE IF NOT EXISTS entity_alias(
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY(entity_id, normalized_alias, source)
);

CREATE INDEX IF NOT EXISTS idx_entity_alias_normalized
ON entity_alias(normalized_alias);

CREATE TABLE IF NOT EXISTS entity_external_ref(
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  external_kind TEXT NOT NULL,
  external_id TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  last_synced_at TEXT,
  PRIMARY KEY(provider, external_kind, external_id),
  UNIQUE(entity_id, provider, external_kind)
);

CREATE TABLE IF NOT EXISTS asset_entity(
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  first_seen_at TEXT,
  last_seen_at TEXT,
  PRIMARY KEY(asset_id, entity_id, role, source)
);

CREATE INDEX IF NOT EXISTS idx_asset_entity_entity_role
ON asset_entity(entity_id, role);

INSERT OR IGNORE INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)
SELECT 'performer', substr(tag,4), lower(trim(substr(tag,4))),
       strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM asset_tag WHERE tag LIKE '演员:%' AND trim(substr(tag,4))<>'';

INSERT OR IGNORE INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)
SELECT 'tag', tag, lower(trim(tag)),
       strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM asset_tag WHERE tag NOT LIKE '演员:%' AND trim(tag)<>'';

INSERT OR IGNORE INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)
SELECT 'studio', studio, lower(trim(studio)),
       strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM asset WHERE studio IS NOT NULL AND trim(studio)<>'';

INSERT OR IGNORE INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)
SELECT 'creator', creator, lower(trim(creator)),
       strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM asset WHERE creator IS NOT NULL AND trim(creator)<>'';

INSERT OR IGNORE INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)
SELECT 'series', series, lower(trim(series)),
       strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM asset WHERE series IS NOT NULL AND trim(series)<>'';

INSERT OR IGNORE INTO asset_entity(asset_id,entity_id,role,source,confidence)
SELECT t.asset_id,e.id,'performer',COALESCE(t.source,'legacy:asset_tag'),COALESCE(t.confidence,1.0)
FROM asset_tag t JOIN entity e
  ON e.kind='performer' AND e.normalized_name=lower(trim(substr(t.tag,4)))
WHERE t.tag LIKE '演员:%';

INSERT OR IGNORE INTO asset_entity(asset_id,entity_id,role,source,confidence)
SELECT t.asset_id,e.id,'tag',COALESCE(t.source,'legacy:asset_tag'),COALESCE(t.confidence,1.0)
FROM asset_tag t JOIN entity e
  ON e.kind='tag' AND e.normalized_name=lower(trim(t.tag))
WHERE t.tag NOT LIKE '演员:%';

INSERT OR IGNORE INTO asset_entity(asset_id,entity_id,role,source,confidence)
SELECT a.id,e.id,'studio','legacy:asset',0.8
FROM asset a JOIN entity e
  ON e.kind='studio' AND e.normalized_name=lower(trim(a.studio))
WHERE a.studio IS NOT NULL AND trim(a.studio)<>'';

INSERT OR IGNORE INTO asset_entity(asset_id,entity_id,role,source,confidence)
SELECT a.id,e.id,'creator','legacy:asset',0.8
FROM asset a JOIN entity e
ON e.kind='creator' AND e.normalized_name=lower(trim(a.creator))
WHERE a.creator IS NOT NULL AND trim(a.creator)<>'';

INSERT OR IGNORE INTO asset_entity(asset_id,entity_id,role,source,confidence)
SELECT a.id,e.id,'series','legacy:asset',0.8
FROM asset a JOIN entity e
  ON e.kind='series' AND e.normalized_name=lower(trim(a.series))
WHERE a.series IS NOT NULL AND trim(a.series)<>'';
