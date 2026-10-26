-- 关注页直接在线播放同样属于观看行为；不要求先把候选保存成 asset。
-- follow_item 的 new/seen/saved/ignored 是当前收件箱状态，不能承担不可回退的播放历史。
CREATE TABLE IF NOT EXISTS follow_playback(
  follow_item_id INTEGER NOT NULL REFERENCES follow_item(id) ON DELETE CASCADE,
  profile_id TEXT NOT NULL DEFAULT 'local-default' REFERENCES profile(id),
  play_count INTEGER NOT NULL DEFAULT 0 CHECK(play_count >= 0),
  play_seconds REAL NOT NULL DEFAULT 0 CHECK(play_seconds >= 0),
  max_reached REAL NOT NULL DEFAULT 0 CHECK(max_reached >= 0 AND max_reached <= 1),
  last_played REAL,
  PRIMARY KEY(follow_item_id, profile_id)
);

CREATE INDEX IF NOT EXISTS idx_follow_playback_profile_time
ON follow_playback(profile_id, last_played DESC);
