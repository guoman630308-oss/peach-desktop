-- 实体合并的墓碑：被并入的实体 id 指向它并进去的那一条。
--
-- `entities.merge_entity` 把 source 的作品、别名、引用整批搬到 target 之后删掉 source 那一行。
-- 账本之外还有按实体 id 记下的东西：头像文件 `<kind>-<id>.img`、复核 CSV 的 `entity_id`
-- 列、页面与书签里的 `/entity-image?id=`。墓碑让这些旧 id 解析得到现在的实体。
--
-- 为什么是一张侧表、不是 `entity` 上的一列：被并入的那一行留在 `entity` 里，就要在每个
-- 列实体的查询上加「不是墓碑」的条件，而 `UNIQUE(kind, normalized_name)` 与按规范名匹配的
-- `upsert_asset_entity` 会把下一次刮削到的同名作品挂回那条死实体。侧表让 `entity` 里只有活
-- 实体这件事保持不变。
--
-- `old_id` 不带外键：它指的那一行已经删了。`target_id` 永远指活实体，链式合并在写入时压平
-- （A 并入 B、B 再并入 C，A 这一行直接改指 C），解析只查一跳。
--
-- `entity.id` 没有 AUTOINCREMENT，删掉的最大 id 会被下一条新实体复用。解析一律先查活实体，
-- 查不到才看墓碑；新实体占用某个墓碑的 id 时，下面的触发器把那条墓碑删掉。
--
-- 服务与脚本的连接不开 `PRAGMA foreign_keys`，`ON DELETE CASCADE` 不会执行。目标实体被删
-- （标签改名、清理脚本、FC2 卖家修复都直接删 `entity`）时由触发器删掉指向它的墓碑，否则悬空
-- 的 `target_id` 会让合并后的 `foreign_key_check` 不为 0（ADR-0064 第三条按它回滚）。
-- 重建 `entity` 表的迁移（像 0025 那样 DROP 再 RENAME）会连带丢掉这两个触发器，要原样补回。
CREATE TABLE entity_redirect(
  old_id INTEGER PRIMARY KEY,
  target_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  merged_at TEXT NOT NULL,
  CHECK(old_id<>target_id)
);

-- 压平链条按目标改写，目标被删时按目标清除。
CREATE INDEX idx_entity_redirect_target ON entity_redirect(target_id);

CREATE TRIGGER entity_redirect_target_delete AFTER DELETE ON entity BEGIN
  DELETE FROM entity_redirect WHERE target_id=OLD.id;
END;

CREATE TRIGGER entity_redirect_id_reuse AFTER INSERT ON entity BEGIN
  DELETE FROM entity_redirect WHERE old_id=NEW.id;
END;

INSERT INTO ledger_revision(tbl) VALUES('entity_redirect');

CREATE TRIGGER rev_entity_redirect_insert AFTER INSERT ON entity_redirect BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_redirect';
END;

CREATE TRIGGER rev_entity_redirect_update AFTER UPDATE ON entity_redirect BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_redirect';
END;

CREATE TRIGGER rev_entity_redirect_delete AFTER DELETE ON entity_redirect BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_redirect';
END;
