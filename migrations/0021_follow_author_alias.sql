-- 不同平台会给同一位创作者使用不同账号名。这里只保存用户确认的追更别名，
-- 不自动提升为全局 entity 真相；自动检测只产生建议。

CREATE TABLE IF NOT EXISTS follow_author_alias(
  alias_key TEXT PRIMARY KEY,
  alias_name TEXT NOT NULL,
  canonical_key TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK(alias_key<>''),
  CHECK(canonical_key<>'')
);

CREATE INDEX IF NOT EXISTS idx_follow_author_alias_canonical
ON follow_author_alias(canonical_key, alias_name);
