--
-- 「这个来源 genre 归哪个 Peach 标签」由用户当场定下来，存在账本里。
--
-- 为什么要这么改：`genre_taxonomy.py` 那两张表是随代码发版的静态策略，用户在复核页
-- 看到「来源还有 2 个未收录 genre：69、初裏」时手上没有任何按钮——要把它收录进来就得
-- 改 Python 再重启。于是那句话每次都原样出现，`map_genres` 文档里写的「不允许长期停在
-- 『不知道』」在界面上没有出口。这张表就是那个出口。
--
-- 为什么不进 `entity`：这里记的不是库里的一个身份，而是「外部写法 → 本地词表」的投影
-- 规则，和 `catalog_rules` 同级。标签实体照旧由 `asset_entity` 那条路产生，这张表只决定
-- 抓回来的原文要不要变成标签、变成哪个。
--
-- `peach_tag IS NULL` 就是「这不是内容标签」，和 `NON_CONTENT_GENRES` 同一种判定：
-- 来源确实给了值，但它说的是画质、发行或促销。两种结论必须都能记，只能记「是什么」
-- 的话，排除项会在每一批候选里重新冒出来。
--
-- 主键用规范化后的写法（`normalise_genre`：NFKC、折空白、小写），原文另存一列给人看。
-- 同一个词的全角、半角和大小写写法是同一条决定，分成几行只会让它们各走各的。

CREATE TABLE genre_decision(
  source_genre TEXT PRIMARY KEY,
  raw_genre TEXT NOT NULL,
  peach_tag TEXT,
  decided_at TEXT NOT NULL,
  CHECK(peach_tag IS NULL OR length(trim(peach_tag))>0)
);
