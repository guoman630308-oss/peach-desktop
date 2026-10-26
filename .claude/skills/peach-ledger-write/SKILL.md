---
name: peach-ledger-write
description: 在用户说迁移、migrate、--apply、合并实体、merge_entity、改真相字段、批量删除、清空回收站，或任何要写真实 ledger 的动作之前使用。
---

# 真实 ledger 写入流程

最后复核：2026-10-17
证据来源：[开发约定](../../../AGENTS.md)、ADR-0005、ADR-0015、ADR-0017、ADR-0052、ADR-0091、ADR-0106。

真实库：当前写入者本机 `PEACH_DATA_ROOT/database/ledger.db`（WAL）。绝不能把共享传输副本或
另一台机器的副本当当前真实库；测试只用临时 SQLite 与临时媒体。

## 迁移

1. SQLite 备份到当前本机 `PEACH_DATA_ROOT/database/ledger.pre-<用途>-<时间戳>.db`。
2. 记录迁移前 asset/tag 计数。
3. `PRAGMA integrity_check`。
4. `peach migrate status` 核对版本。
5. 应用后重复计数并做服务 smoke test，前后差异逐条解释。
6. 复核通过后清退旧备份：`scripts/prune_ledger_backups.py --apply`（缺省只列计划）。规则在
   `peach.ledger_backups`：最近 5 份、24 小时内、比 `ledger.db` 更新的都留，其余连同 `-wal`／`-shm`
   删；账本 `integrity_check` 不是 ok 一份都不删。Windows 托盘每次启动按同一规则自动跑。

Windows 写者的托盘在启动与「重启服务」时、子服务停着的间隙自动跑 `migrate upgrade`，
备份、失败通知与 reader 不迁移见 ADR-0091。上面六步用于手工迁移与托盘之外的账本。

已应用的迁移文件不得修改。`0007` 曾在应用后被改写注释导致校验和漂移，必须用备份重放并逐条
对比后才校正 `schema_migration`。任何后续变更一律新增版本号。

## 真相字段写入

- `asset` 的真相字段（`catalog_title`、`original_title`、`release_date`、`studio`、`series`、
  `creator`、`code`）只经 `peach.field_owners.write_owned_fields` 写，写入者用归属串署名：
  `user:manual`、`review:<来源>`、`auto:<来源>`、`scan:filename`、`script:<脚本名>`。自己拼
  `UPDATE asset SET <字段>` 会绕过覆盖规则，让自动写入者悄悄改掉用户的判断，事后也答不出
  是谁写的。`mutation_revision` 是乐观并发的凭据，写入端点收 `expected_revision`。
- 改写 `entity.canonical_name` 与迁移同级：`--apply` 必须同时给 `--backup`。
- 证据充分即落库（ADR-0018/0025 管字段，ADR-0052 管实体）：代码判据给出确定结论、只填空不
  覆盖人的判断、能按 `source`／`batch` 整批撤回（`scripts/revert_auto_landing.py`）的，由处理
  任务或后继直接写，每条记 `source`、判词与批次。LLM 输出、打分、`weak`、多候选与来源冲突
  只产候选，人复核后才 `approved`；撤不回的（合并、改规范名）判据再确定也要授权。
- 运维脚本默认 dry-run：`scrape_codes.py` 默认只写复核 CSV，`clean_names.py` 默认只生成
  改名计划且 `--apply` 前备份 SQLite 并在数据库更新失败时回滚文件名。
- 种子包按 ADR-0073、0075、[0106](../../../docs/adr/0106-seed-correction-history-and-explicit-withdrawals.md) 校验；只匹配已有实体，保留源置信度与非种子归属，冲突记 `seed-landing.csv`。
  只精确撤销 `auto:seed` 拥有的旧断言，不依据缺席撤销。`export` 只读；更新须递增版本与修订号、继承累计撤销，
  经差异复核后独立提交数据包。内容摘要与永久历史校验由导入入口执行；公开推送由用户看字段决定。

## 实体合并

- `entity(kind, normalized_name)` 唯一约束冲突通常不是 bug，而是同一人的新旧艺名信号。
- 合并走 `peach.entities.merge_entity`：保留作品多的一侧，迁移关系、别名、外部引用、链接和
  搜索词，旧称全部留作别名。`entity_external_ref` 每个 provider 只保留一条，同源第二条丢弃
  并报告，不静默覆盖。
- 合并不可逆：先取得用户授权并备份。唯一的自动路径是补别名后继（ADR-0064）：minnano-av 或
  av_neme 名字栏把两条女优实体列成同一个人时，后继自己备份到数据库目录再合；别的判据不得照搬。
- 两条实现陷阱：sqlite 连接默认 `foreign_keys=OFF`，子表行必须在函数内显式 DELETE，否则留下
  孤儿 `entity_alias` / `entity_external_ref`；计数用 `SELECT changes()`，不能用
  `total_changes`（连接累计值，会虚报数百倍）。
- 合并后立即 `PRAGMA foreign_key_check`，应为 0。

## 删除

- 物理删除只有一条实现 `purge_assets()`，`/api/batch` 的 `delete` 与 `/api/trash/empty` 共用。
- 顺序固定：先删媒体文件、再删账本行。删不掉的文件整条跳过并在 `blocked` 里回报，前端必须
  显示 `blocked`。反过来先删行会留下无人认领的媒体文件，那才是不可恢复的丢失。
- `asset_search` 不列入 `ASSET_REFERENCE_TABLES`，FTS 行由 `0004` 的删除触发器负责。
- 不可逆动作先产出带证据和置信度的复核产物，执行步骤单独授权。

## 结论必须写入文件

得出结论的同一步就写入 ledger、CSV 或其他持久产物；结论被修正时所有派生产物必须重建。
只存在于聊天里的结论等于不存在，过期的删除清单比没有清单更危险。
