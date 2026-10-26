-- 在线追更的订阅登记与候选条目。
-- follow_item 是候选，不是真相：`status` 停在 'new'/'seen' 时不影响任何 asset。
-- 只有用户在复核界面显式保存，才会写出 asset 并把 asset_id 回填到这里。

CREATE TABLE IF NOT EXISTS follow_source(
  id INTEGER PRIMARY KEY,
  entity_id INTEGER REFERENCES entity(id) ON DELETE SET NULL,
  provider TEXT NOT NULL,
  ref TEXT NOT NULL,
  label TEXT NOT NULL,
  url TEXT NOT NULL,
  semantics TEXT NOT NULL DEFAULT 'work' CHECK(semantics IN ('work','release')),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  etag TEXT,
  last_modified TEXT,
  last_checked_at TEXT,
  last_status TEXT,
  last_error TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(provider, ref)
);

CREATE INDEX IF NOT EXISTS idx_follow_source_entity
ON follow_source(entity_id, provider);

CREATE TABLE IF NOT EXISTS follow_item(
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES follow_source(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT,
  media_url TEXT,
  thumb_url TEXT,
  published_at TEXT,
  -- 站点只给相对时间时写 'approximate'；界面必须照实显示，不得冒充精确发布时间。
  published_precision TEXT NOT NULL DEFAULT 'exact'
    CHECK(published_precision IN ('exact','approximate','unknown')),
  version TEXT,
  duration REAL,
  release_key TEXT NOT NULL,
  variant_kind TEXT NOT NULL DEFAULT 'main' CHECK(variant_kind IN ('main','wip','alt')),
  variant_label TEXT,
  group_hint TEXT,
  status TEXT NOT NULL DEFAULT 'new'
    CHECK(status IN ('new','seen','saved','ignored')),
  asset_id INTEGER REFERENCES asset(id) ON DELETE SET NULL,
  evidence_path TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  UNIQUE(source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_follow_item_status
ON follow_item(status, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_follow_item_release
ON follow_item(release_key, variant_kind);

CREATE INDEX IF NOT EXISTS idx_follow_item_source
ON follow_item(source_id, last_seen_at DESC);
