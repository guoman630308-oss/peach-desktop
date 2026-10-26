CREATE TABLE IF NOT EXISTS asset_preference(
  profile_id TEXT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  liked INTEGER NOT NULL DEFAULT 0 CHECK(liked IN (0,1)),
  reason TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'web',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id,asset_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_preference_profile_liked
ON asset_preference(profile_id,liked,updated_at DESC);
