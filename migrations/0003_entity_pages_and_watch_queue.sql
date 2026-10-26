INSERT OR IGNORE INTO app_user(id,display_name,created_at)
VALUES('local','Local user',strftime('%Y-%m-%dT%H:%M:%fZ','now'));

INSERT OR IGNORE INTO profile(id,user_id,name,is_default,settings_json,created_at,updated_at)
VALUES('local-default','local','Default',1,'{}',
       strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'));

CREATE TABLE IF NOT EXISTS watch_queue(
  profile_id TEXT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  added_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'web',
  PRIMARY KEY(profile_id,asset_id)
);

CREATE INDEX IF NOT EXISTS idx_watch_queue_profile_time
ON watch_queue(profile_id,added_at DESC);

CREATE TABLE IF NOT EXISTS entity_link(
  id INTEGER PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  link_kind TEXT NOT NULL CHECK(link_kind IN ('official','social','catalog','source_reference')),
  label TEXT NOT NULL,
  url TEXT NOT NULL,
  hostname TEXT,
  is_sensitive INTEGER NOT NULL DEFAULT 0 CHECK(is_sensitive IN (0,1)),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(entity_id,url)
);

CREATE INDEX IF NOT EXISTS idx_entity_link_entity_kind
ON entity_link(entity_id,link_kind);

CREATE TABLE IF NOT EXISTS entity_search_term(
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  term TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK(purpose IN ('discovery','source_lookup')),
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(entity_id,term,purpose)
);

CREATE INDEX IF NOT EXISTS idx_entity_search_term_entity
ON entity_search_term(entity_id,purpose);

UPDATE entity SET metadata_json='{"summary":"FC2 的用户发布与内容市场厂牌归类；Peach 将 FC2-PPV 作为同一厂牌入口，并保留具体番号和来源证据。"}'
WHERE kind='studio' AND canonical_name='FC2-PPV' AND metadata_json='{}';

UPDATE entity SET metadata_json='{"summary":"日本影像厂牌 Prestige；Prestige Premium 作为其别名归入同一资料页，独立的 PREMIUM 厂牌不合并。"}'
WHERE kind='studio' AND canonical_name='Prestige' AND metadata_json='{}';

INSERT OR IGNORE INTO entity_link(entity_id,link_kind,label,url,hostname,is_sensitive,created_at,updated_at)
SELECT id,'official','FC2','https://fc2.com/en/','fc2.com',0,
       strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM entity WHERE kind='studio' AND canonical_name='FC2-PPV';

INSERT OR IGNORE INTO entity_link(entity_id,link_kind,label,url,hostname,is_sensitive,created_at,updated_at)
SELECT id,'official','FC2 Contents Market','https://contents.fc2.com/','contents.fc2.com',0,
       strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM entity WHERE kind='studio' AND canonical_name='FC2-PPV';

INSERT OR IGNORE INTO entity_link(entity_id,link_kind,label,url,hostname,is_sensitive,created_at,updated_at)
SELECT id,'official','Prestige official','https://www.prestige-av.com/','prestige-av.com',0,
       strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM entity WHERE kind='studio' AND canonical_name='Prestige';
