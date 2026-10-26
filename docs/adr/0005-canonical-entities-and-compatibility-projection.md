# ADR-0005：规范实体与兼容投影

- 状态：Accepted；真实迁移已应用
- 日期：2026-08-14

## 背景

现有 ledger 把 performer 压成 `演员:` 标签，studio/creator 仅是 asset 文本列，Stash Scene 也缺少稳定的实体级外部引用。这会让改名、别名、删除同步、多来源合并和未来 profile 隔离都依赖字符串猜测。

## 决策

- `entity` 表示 performer/studio/tag/creator/series 的规范身份。
- `entity_alias` 保存来源明确的别名；`entity_external_ref` 保存 `(source, external_id)` 稳定引用。
- `asset_entity` 表示资产与实体的有类型关系，并保留 source/confidence/provenance。
- `0002` 从现有 `演员:` 标签、普通标签、studio 和 creator 文本回填关系。
- Stash importer 同时写 canonical relation 与现有扁平字段；旧 Web 继续读取兼容投影，直到查询层切换完成。
- 迁移只由 `peach migrate apply` 显式执行，不在服务启动时自动修改真实数据库。

## 被否决的方案

- 继续把 performer 永久编码为带前缀的标签。
- 一次性删除 asset 文本列和 `asset_tag`，迫使 Web 与全部脚本同步重写。
- 用名称充当跨来源稳定 ID。

## 影响

短期存在双写与兼容数据；长期可逐个查询面切换到规范实体，验证无回归后再删除旧投影。真实 ledger 应用 `0002` 前必须创建新备份并单独验收。
