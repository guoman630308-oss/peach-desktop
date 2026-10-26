CREATE TABLE playlist(
  id INTEGER PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 80),
  source_kind TEXT NOT NULL DEFAULT 'manual' CHECK(source_kind IN ('manual','mix')),
  source_seed_asset_id INTEGER REFERENCES asset(id) ON DELETE SET NULL,
  current_asset_id INTEGER REFERENCES asset(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_playlist_profile_updated
ON playlist(profile_id,updated_at DESC,id DESC);

CREATE TABLE playlist_item(
  playlist_id INTEGER NOT NULL REFERENCES playlist(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  position INTEGER NOT NULL CHECK(position >= 0),
  added_at TEXT NOT NULL,
  PRIMARY KEY(playlist_id,asset_id),
  UNIQUE(playlist_id,position)
);

CREATE INDEX idx_playlist_item_asset
ON playlist_item(asset_id,playlist_id);
