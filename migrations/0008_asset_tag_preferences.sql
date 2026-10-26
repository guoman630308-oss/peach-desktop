-- 保留刮削/识别断言，只记录本地 profile 对某条资源标签的隐藏选择。
CREATE TABLE IF NOT EXISTS asset_tag_preference(
  profile_id TEXT NOT NULL,
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  normalized_tag TEXT NOT NULL,
  hidden INTEGER NOT NULL DEFAULT 1 CHECK(hidden IN (0,1)),
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id,asset_id,normalized_tag)
);

CREATE INDEX IF NOT EXISTS idx_asset_tag_preference_asset
ON asset_tag_preference(asset_id,profile_id,hidden);
