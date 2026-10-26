CREATE TABLE IF NOT EXISTS search_history(
  query TEXT PRIMARY KEY,
  used_count INTEGER NOT NULL DEFAULT 1,
  last_used_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_search_history_recent
ON search_history(last_used_at DESC, query);
