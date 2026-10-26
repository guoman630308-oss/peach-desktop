--
-- 真相字段的字段级归属与乐观并发。
--
-- 为什么要这么改：`asset` 的真相字段（`catalog_title`、`original_title`、
-- `release_date`、`studio`、`series`、`creator`、`code`）由六条不同的路径写入——
-- `/review` 批准、ADR-0018／0025 的免复核落库、库处理的文件名推导、维护脚本、
-- 实体改名的扁平投影、迁移本身。账本里存的只有取值，「这一个是谁写的」得靠
-- `review_decision` 恰好有一条对得上的记录去猜；猜不出来时就答不出 ADR-0018 要求
-- 必须答得出的那句话。`field_owners` 把这句答案直接存进账本。
--
-- 取值是 JSON 对象，键是列名，值是归属串（`user:manual`、`review:<来源>`、
-- `auto:<来源>`、`scan:filename`、`script:<脚本名>`）。写法与覆盖规则在
-- `src/peach/field_owners.py`，它是这几列唯一的写入入口。
--
-- 为什么不建一张 `asset_field_owner` 子表：归属是字段的属性，和取值同生同死，
-- 每次写真相字段都要跟着改。分成两张表后，「写值」与「写归属」就成了两条语句，
-- 而这一改动的全部意义就在于两者必须原子地一起发生。JSON 列让它们留在同一条
-- UPDATE 里，SQLite 的 `json_extract` / `json_patch` 够用。
--
-- 已有行的归属留空（`field_owners IS NULL`），不回填、不猜：无主字段任何写入者都能
-- 写，猜错的归属反而会把用户真正的判断锁在一个错误的写入者名下。
--
-- `mutation_revision` 是乐观并发的凭据，只在真相字段的取值真的变了时加一。它不是
-- 行的通用版本号：播放次数、反馈、回收站状态各有自己的列，改它们不动这个数。

ALTER TABLE asset ADD COLUMN field_owners TEXT;
ALTER TABLE asset ADD COLUMN mutation_revision INTEGER NOT NULL DEFAULT 0;
