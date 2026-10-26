CREATE TABLE IF NOT EXISTS app_user(
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile(
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_user(id),
  name TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
  settings_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, name)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_one_default_per_user
ON profile(user_id) WHERE is_default=1;

CREATE TABLE IF NOT EXISTS media_binding(
  asset_id INTEGER NOT NULL REFERENCES asset(id),
  backend TEXT NOT NULL,
  external_id TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 100,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  last_synced_at TEXT,
  PRIMARY KEY(asset_id, backend),
  UNIQUE(backend, external_id)
);

CREATE INDEX IF NOT EXISTS idx_media_binding_backend_external
ON media_binding(backend, external_id);

CREATE TABLE IF NOT EXISTS activity_event(
  id INTEGER PRIMARY KEY,
  asset_id INTEGER NOT NULL REFERENCES asset(id),
  profile_id TEXT REFERENCES profile(id),
  kind TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  position_seconds REAL,
  duration_seconds REAL,
  delta_seconds REAL,
  value_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL,
  session_id TEXT,
  event_key TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_activity_event_asset_time
ON activity_event(asset_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_activity_event_profile_time
ON activity_event(profile_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS provider_profile(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('inference','agent')),
  provider TEXT NOT NULL,
  label TEXT NOT NULL,
  auth_mode TEXT NOT NULL,
  secret_ref TEXT,
  config_json TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
