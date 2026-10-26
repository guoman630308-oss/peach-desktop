--
-- label 属于哪家厂商（ADR-0049）。
--
-- 为什么这么改：账本里厂牌只有一层。同一个母公司旗下的 label 有三种记法：独立成一条
-- 厂牌（BAZOOKA 与 K M Produce 互不相识）、被别名并进母公司（PRESTIGE PREMIUM 挂在
-- Prestige 名下）、「label/母公司」写成一串（`ABC/妄想族`）。按母公司看全部作品做不到，
-- 合并脚本还会把 label 当成母公司的另一种写法吞掉，而合并不可逆。
--
-- label 仍是 `kind='studio'` 的实体，作品照旧挂在它自己身上；这张表只多记一件事：它归
-- 哪家厂商。label 不当成厂商处理：不改名、不合并、不改写作品上的厂牌。
--
-- 主键在 label 一侧，一个 label 只有一家现役母公司，转手是覆盖，不是再加一行；做法同
-- `entity_membership`。
CREATE TABLE label_maker(
  label_id INTEGER PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  maker_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  checked_at TEXT NOT NULL,
  CHECK(label_id<>maker_id)
);

-- 厂商资料页要列「旗下有哪些 label」，没有索引就是全表扫。
CREATE INDEX idx_label_maker_maker ON label_maker(maker_id);
