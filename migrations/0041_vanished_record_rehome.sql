-- 盘上消失的文件带着个人记录时标「已消失」，新文件登记时把记录接过去（ADR-0087）。
--
-- `asset_quality_goal.replaced_at`：寻找更好版本的目标随记录搬到新行就算达成，`wanted` 置 0
-- 关闭，这一列记下被替换的时刻，和用户自己取消的目标分得开。
--
-- `record_rehome`：一次搬运一行，就是 ADR-0052 的一个批次（`<source>@<id>`）。`snapshot_json`
-- 存旧行整行、它在各引用表里的全部行和指向它的播放列表指针，`moved_json` 存改指新行的那几条的
-- 键；整批撤回时按快照重建旧行，把搬走的改回旧行。两个 asset id 都不挂外键：旧行搬完就删了，
-- 新行以后也可能被删，批次记录要留着。
ALTER TABLE asset_quality_goal ADD COLUMN replaced_at TEXT;

CREATE TABLE record_rehome(
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  rule TEXT NOT NULL,
  old_asset_id INTEGER NOT NULL,
  new_asset_id INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL,
  moved_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  reverted_at TEXT
);

CREATE INDEX idx_record_rehome_source ON record_rehome(source,reverted_at,id);

INSERT INTO ledger_revision(tbl) VALUES ('record_rehome');

CREATE TRIGGER rev_record_rehome_insert AFTER INSERT ON record_rehome BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='record_rehome';
END;

CREATE TRIGGER rev_record_rehome_update AFTER UPDATE ON record_rehome BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='record_rehome';
END;

CREATE TRIGGER rev_record_rehome_delete AFTER DELETE ON record_rehome BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='record_rehome';
END;
