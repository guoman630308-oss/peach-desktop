-- 清掉指向已删来源的子表行。
--
-- 关注源与订阅源的删除入口此前只删来源那一行，指望表上的 `ON DELETE CASCADE` /
-- `SET NULL` 连带处理子表；可服务的连接从不打开 `PRAGMA foreign_keys`，这些声明一次也
-- 没执行过。删过来源的库因此留着孤儿条目，全库 `foreign_key_check` 不为 0，补别名后继
-- 合并实体时按这个结果回滚。删除入口现在显式处理子表，这里只补存量。
--
-- 迁移在外键打开时运行：删 `follow_item` 会连带删它的播放记录，下面那句只兜住此前就
-- 已经没有主人的播放记录。
DELETE FROM follow_item WHERE source_id NOT IN (SELECT id FROM follow_source);
DELETE FROM follow_playback WHERE follow_item_id NOT IN (SELECT id FROM follow_item);

DELETE FROM feed_item WHERE source_id NOT IN (SELECT id FROM feed_source);
UPDATE feed_discovery SET source_id=NULL
  WHERE source_id IS NOT NULL AND source_id NOT IN (SELECT id FROM feed_source);
