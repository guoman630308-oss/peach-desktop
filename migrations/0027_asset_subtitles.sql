--
-- 同目录的字幕 sidecar：哪个文件是哪部片的字幕，以及这个结论是怎么得出来的。
--
-- 为什么不塞进 `asset`：字幕文件本来就各自有一行 `asset`（扫描把它归成 `other`），
-- 那一行说的是「磁盘上有这个文件」，这张表说的是「它是 asset N 的字幕」。后者是配对
-- 结论，会随重扫改变——同目录多出一部片、文件被改名，同一条字幕就该换宿主。把结论写回
-- `asset` 的真相字段就没有地方记它是怎么来的，也没法在配错时只推翻结论。
--
-- `pairing` 记判据名而不是置信度：配对只有三条规则，每条要么成立要么不成立，给个 0.8
-- 除了看着像机器学习之外不提供任何可操作信息。人要判断「这条配得对不对」时，需要知道
-- 的是「它按什么配上的」——`exact` 出错的概率和 `code` 完全不是一个量级。
--   exact   字幕主名与视频主名逐字相同
--   suffix  剥掉末尾的语言或版本 token 之后相同（`ABP-758.chs.srt` → `ABP-758.mp4`）
--   code    同目录，且两边文件名解析出同一个番号
--   orphan  同目录没有能配上的视频；只登记，不猜
--
-- `asset_id` 可空，就是为了 `orphan`：配不上的字幕仍然要登记，否则每次重扫都要重新
-- 遍历一遍才知道「这个目录里有一条没人认领的字幕」。CASCADE 是安全网而不是删除路径，
-- 理由与 0024 相同——运行时连接不开 `PRAGMA foreign_keys`，物理删除走
-- `web_batch.ASSET_REFERENCE_TABLES` 里的显式 DELETE，这张表已经登记在那里。
--
-- `language` 留空表示「文件名里推不出来」，不是「没有语言」。推不出就留空，不拿
-- 目录名或番号去猜：猜错的字幕轨在播放器菜单里长得和猜对的一模一样。
--
-- 路径与 `asset.path` 同一套规则：一律 Windows 形态，读取时经 `platform.py` 翻译。
-- `UNIQUE(location,path)` 也与 `asset` 一致，重扫靠它做幂等 upsert。
-- 消失的行不删，只让 `last_seen` 落后，同样沿用 `asset` 的约定。

CREATE TABLE asset_subtitle(
  id INTEGER PRIMARY KEY,
  asset_id INTEGER REFERENCES asset(id) ON DELETE CASCADE,
  location TEXT NOT NULL,
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT '',
  format TEXT NOT NULL,
  size INTEGER,
  mtime TEXT,
  pairing TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  UNIQUE(location,path),
  CHECK(pairing IN ('exact','suffix','code','orphan')),
  CHECK(asset_id IS NOT NULL OR pairing='orphan')
);

CREATE INDEX idx_asset_subtitle_asset ON asset_subtitle(asset_id,name);
