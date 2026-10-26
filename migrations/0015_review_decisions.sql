CREATE TABLE IF NOT EXISTS review_decision(
  category TEXT NOT NULL,
  item_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('approved','rejected','skipped')),
  reviewer TEXT NOT NULL DEFAULT 'local-default',
  note TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(category,item_key)
);

CREATE INDEX IF NOT EXISTS idx_review_decision_status
ON review_decision(category,status,updated_at DESC);
