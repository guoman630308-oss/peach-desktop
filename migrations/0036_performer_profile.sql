--
-- 女优资料一位一行（ADR-0067）。
--
-- 为什么单开一张表：出生日期、身高三围、出道年这些是要按列筛、按列排的量（「2000 年以后
-- 出生」「身高 150 以下」），塞进 `entity.metadata_json` 就只能整段取出来在 Python 里拆。
-- 别名继续走 `entity_alias`，事务所继续走 `entity.metadata_json.agency`，这张表不重复存。
--
-- 列的约定：解析不到就是 NULL，不猜；日期是 ISO 形态（`2001-12-08`），尺寸是厘米整数。
-- `active_until` 为 NULL 表示站上没写截止年，即仍在活动。`tags_json` 是站上的标签数组，
-- `raw_json` 是资料表每一格的原文，规整规则改了可以就地重算，不必重新取页。
--
-- `source` 是写入这一行的批次号（`auto:performer-profile@<任务行 id>`），
-- `scripts/revert_auto_landing.py` 按它整批撤回；`fetched_at` 决定多久后重取。
CREATE TABLE performer_profile(
  entity_id INTEGER PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  kana TEXT,
  romaji TEXT,
  birth_date TEXT,
  height_cm INTEGER,
  bust_cm INTEGER,
  cup TEXT,
  waist_cm INTEGER,
  hip_cm INTEGER,
  blood_type TEXT,
  birthplace TEXT,
  hobbies TEXT,
  debut_year INTEGER,
  active_until INTEGER,
  debut_title TEXT,
  debut_date TEXT,
  blog_url TEXT,
  site_url TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  raw_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL,
  source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

-- 撤回按批次找行，重取按取回时间挑最旧的。
CREATE INDEX idx_performer_profile_source ON performer_profile(source);
CREATE INDEX idx_performer_profile_fetched ON performer_profile(fetched_at);
