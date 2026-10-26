--
-- 按番号存官方样张（ADR-0068）。
--
-- 样张属于番号，不属于某个文件：同一部片在 115 与本地各有一份时，两份看到的是同一组
-- 样张，所以键是归一番号（`catalog_rules.normalise_code_key`），和封面缓存同一把钥匙。
-- 这里只存地址，图片本身在第一次被人看到时才下载进 `generated/sample-cache/`。
--
-- 地址来自官方站（DMM／FANZA、MGS）或来源快照里官方站给的那一列，判据由代码给出，按
-- ADR-0052 直接落库。`source` 记 `auto:sample-images@<任务行 id>`，判错了按它整批撤回；
-- 只填空，番号已有样张就整组跳过，所以撤回就是删除。
--
-- `site` 是图从哪一站来（`dmm`、`mgstage`），和 `source` 分开：前者回答「这张图是谁的」，
-- 后者回答「是哪一趟写进来的」。宽高在下载进缓存后才知道，可空。
CREATE TABLE code_sample_image(
  code TEXT NOT NULL,
  position INTEGER NOT NULL CHECK(position>=1),
  url TEXT NOT NULL,
  site TEXT NOT NULL,
  width INTEGER,
  height INTEGER,
  source TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY(code,position)
);

-- 撤回按来源认一批。
CREATE INDEX idx_code_sample_image_source ON code_sample_image(source);
