CREATE TABLE IF NOT EXISTS source(
  id INTEGER PRIMARY KEY,
  batch_id TEXT,
  url TEXT,
  url_norm TEXT UNIQUE,
  title TEXT,
  password TEXT,
  platform TEXT,
  note TEXT,
  registered_at TEXT,
  status TEXT
);

CREATE TABLE IF NOT EXISTS asset(
  id INTEGER PRIMARY KEY,
  location TEXT NOT NULL,
  path TEXT NOT NULL,
  name TEXT,
  medium TEXT,
  size INTEGER,
  mtime TEXT,
  hash_kind TEXT,
  hash TEXT,
  creator TEXT,
  studio TEXT,
  series TEXT,
  code TEXT,
  duration REAL,
  width INTEGER,
  height INTEGER,
  vcodec TEXT,
  fps REAL,
  has_audio INTEGER,
  ctx_length TEXT,
  ctx_orient TEXT,
  ctx_quality TEXT,
  ctx_pace TEXT,
  ctx_people TEXT,
  play_count INTEGER DEFAULT 0,
  last_played TEXT,
  rating INTEGER,
  o_count INTEGER,
  watch_ratio REAL,
  source_id INTEGER,
  stash_scene_id INTEGER,
  snapshot_path TEXT,
  first_seen TEXT,
  last_seen TEXT,
  feedback TEXT,
  disposal TEXT,
  leave_ratio REAL,
  play_seconds REAL,
  feedback_at REAL,
  seek_count INTEGER,
  max_reached REAL,
  UNIQUE(location, path)
);

CREATE TABLE IF NOT EXISTS asset_tag(
  asset_id INTEGER,
  tag TEXT,
  confidence REAL DEFAULT 1.0,
  source TEXT,
  UNIQUE(asset_id, tag)
);

CREATE TABLE IF NOT EXISTS quest(
  id INTEGER PRIMARY KEY,
  keyword TEXT,
  origin TEXT,
  created_at TEXT,
  resolved_at TEXT,
  outcome TEXT
);

CREATE INDEX IF NOT EXISTS idx_asset_hash ON asset(hash_kind, hash);
CREATE INDEX IF NOT EXISTS idx_asset_size ON asset(size);
CREATE INDEX IF NOT EXISTS idx_asset_location ON asset(location);
CREATE INDEX IF NOT EXISTS idx_asset_name ON asset(name);
CREATE INDEX IF NOT EXISTS idx_asset_tag_tag ON asset_tag(tag);
