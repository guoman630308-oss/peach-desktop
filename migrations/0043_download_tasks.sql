-- 下载任务：云下载（115 经 CloudDrive2、PikPak 直连）与本地下载共用的运行状态。
--
-- 两张表都是运行状态，不是真相字段：哪部作品在不在馆藏仍只由 `asset` 回答，这里只记
-- 「交给了谁、走到哪一步、为什么停下」。
--
-- `download_task` 一个 infohash 一行，`info_hash` 是小写 40 位十六进制，唯一索引就是幂等键：
-- 同一个磁力再提交一次，接管已有那一行，不在远端再建一个任务、不再扣一条配额。直链下载
-- 没有 infohash，那一列留空，部分唯一索引只管有值的行。
--
-- `state` 与 `failure` 的取值由 `peach.downloads` 定义（状态机与九类失败），这里不写
-- CHECK：第 42 条本地下载接进来时只加代码里的常量，不必为一个枚举值重建表。
-- `blocked_at` 是拉黑：115 对违规内容的离线拦截（`50038`）不重试，同一 infohash 以后
-- 提交直接拒收。
--
-- `download_submission` 每次提交追加一行，永不更新也不随任务删除：任务行会被「重新下载」
-- 覆盖成新的状态，而每一次提交都扣过一条配额，这笔账要留着。`task_id` 不挂外键，理由
-- 同 `record_rehome`。
--
-- 时间列一律 ISO-8601 UTC 文本。
CREATE TABLE download_task(
  id INTEGER PRIMARY KEY,
  info_hash TEXT,
  provider TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  target TEXT NOT NULL,
  code TEXT,
  title TEXT,
  origin TEXT,
  state TEXT NOT NULL,
  failure TEXT,
  failure_detail TEXT,
  remote_id TEXT,
  remote_name TEXT,
  progress REAL,
  ledger_path TEXT,
  asset_id INTEGER,
  checks INTEGER NOT NULL DEFAULT 0,
  submitted_at TEXT,
  remote_done_at TEXT,
  updated_at TEXT NOT NULL,
  finished_at TEXT,
  next_check_at TEXT,
  blocked_at TEXT
);

CREATE UNIQUE INDEX idx_download_task_info_hash ON download_task(info_hash)
  WHERE info_hash IS NOT NULL;
CREATE INDEX idx_download_task_due ON download_task(next_check_at) WHERE next_check_at IS NOT NULL;

CREATE TABLE download_submission(
  id INTEGER PRIMARY KEY,
  task_id INTEGER,
  info_hash TEXT,
  provider TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  target TEXT NOT NULL,
  code TEXT,
  origin TEXT,
  outcome TEXT NOT NULL,
  failure TEXT,
  detail TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_download_submission_task ON download_submission(task_id, id);

INSERT INTO ledger_revision(tbl) VALUES ('download_task');
INSERT INTO ledger_revision(tbl) VALUES ('download_submission');

CREATE TRIGGER rev_download_task_insert AFTER INSERT ON download_task BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='download_task';
END;

CREATE TRIGGER rev_download_task_update AFTER UPDATE ON download_task BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='download_task';
END;

CREATE TRIGGER rev_download_task_delete AFTER DELETE ON download_task BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='download_task';
END;

CREATE TRIGGER rev_download_submission_insert AFTER INSERT ON download_submission BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='download_submission';
END;

CREATE TRIGGER rev_download_submission_update AFTER UPDATE ON download_submission BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='download_submission';
END;

CREATE TRIGGER rev_download_submission_delete AFTER DELETE ON download_submission BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='download_submission';
END;
