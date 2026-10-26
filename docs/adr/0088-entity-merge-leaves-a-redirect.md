# ADR-0088：实体合并留墓碑，旧 id 按墓碑跳到现在那一条

- 状态：Accepted
- 日期：2026-10-01
- 相关：ADR-0005、ADR-0052、ADR-0064、ADR-0067

## 背景

`entities.merge_entity` 把 source 的作品、别名与引用搬到 target 后删掉 source。账本之外还有按实体
id 记下的东西：头像文件 `<kind>-<id>.img`、复核 CSV 的 `entity_id` 列、页面与书签里的
`/entity-image?id=`。source 一删，这些旧 id 什么都解析不到，孤立头像就是这个症状。账本内部也漏搬了
三处：`follow_source.entity_id`、`feed_source.entity_id` 与 `feed_discovery_entity`。

SakuraMedia 在被并实体上留 `merged_into` 指针，旧 id 照样能找到新实体。

## 决策

**一、墓碑放侧表 `entity_redirect(old_id, target_id, source, merged_at)`（迁移 0040）。** `entity`
里只有活实体：被并的行不留在 `entity`，`UNIQUE(kind, normalized_name)` 与按规范名匹配的
`upsert_asset_entity` 才不会把下一次刮削到的同名作品挂回死实体，几十处 `FROM entity` 的查询也不用加
条件。

**二、`merge_entity` 仍然删行，墓碑在写入时压平。** 删 source 前把指向 source 的墓碑改指 target，
再写 `source → target`，解析只查一跳。合并一并搬走关注、订阅与 Feed 发现的实体引用；并入自己或并入
不存在的目标直接拒绝。

**三、解析先认活实体，再认墓碑**（`entities.resolve_entity_id`）。`entity.id` 没有 AUTOINCREMENT，
被删的最大 id 会发给新实体，新实体占用某个墓碑的 id 时触发器删掉那条墓碑；目标实体被删时触发器删掉
指向它的墓碑。服务与脚本的连接不开外键，这两件事只能交给触发器。重建 `entity` 表的迁移要原样补回
这两个触发器。

**四、按旧 id 进来的入口跳到现在那一条。** `/entity-image` 回 307 到同一地址、只换 id；换头像、
头像候选、Feed 关注与发现筛选、复核候选的规范名都按墓碑落到目标。实体页按名字路由，旧名已是目标的
别名，不需要跳转。

**五、ADR-0064 第四条的留痕多一行墓碑。** 自动合并照旧以 `merge:auto:performer-alias@<任务行 id>`
写别名，同时写一条墓碑，`source` 记同一个来源。

## 理由

- 墓碑不是还原：ADR-0052「未决」里合并不可逆那句仍然成立，但旧 id 的外部引用不再断。
- 侧表让「`entity` 只有活实体」这条不变量保持成立，合并之外的代码不用知道墓碑存在。

## 被否决的方案

- **在 `entity` 上加 `merged_into` 列、保留被并的行。** 名字唯一约束与按名匹配会把作品挂回死实体，
  每个列实体的查询都得过滤墓碑。
- **解析时沿链条逐跳追。** 写入时压平更简单，也不会有环。

## 后果

- 迁移前完成的合并需要按来源与复核产物判断是否回填墓碑；真实写入按 `peach-ledger-write` 单独授权执行。
- 孤立头像 relink 按墓碑反查。
- `review_decision.item_key` 的 `code_creators` 类别与多数复核 CSV 的 `entity_id` 列尚未按墓碑映射。
