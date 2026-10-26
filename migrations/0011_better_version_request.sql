CREATE TABLE IF NOT EXISTS asset_quality_goal(
  profile_id TEXT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  wanted INTEGER NOT NULL DEFAULT 1 CHECK(wanted IN (0,1)),
  reason TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id,asset_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_quality_goal_profile_wanted
ON asset_quality_goal(profile_id,wanted,updated_at DESC);
