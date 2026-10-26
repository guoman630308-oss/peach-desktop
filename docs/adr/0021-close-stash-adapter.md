# ADR-0021：关掉 Stash adapter，媒体解析只留文件系统

- 状态：Accepted
- 日期：2026-09-01
- 关系：收束 ADR-0002 的 Stash 部分；[docs/STASH.md](../STASH.md) 的第 5 步；修订完成第 6 步

## 背景

ADR-0002 把 Stash 定为「可关闭的 adapter」，路线图（[docs/STASH.md](../STASH.md)）的第 5 步是
「同批资产影子对比并关闭 adapter」。这一步一直没做，于是 `StashAdapter` 留在
`MediaEngine` 的 backend 列表里，看上去仍是一条在用的备选来源。

复核时它不只是「用得少」，而是**从来没有生效过**，两条互相独立的判据都成立：

| 判据 | 实测 |
| --- | --- |
| adapter 自己的入口 | `stream_candidates` 依赖 `media_binding` 中的 Stash 外部编号，扫描流程不维护该绑定 |
| 唯一调用方 | `MediaEngine.stream_candidates` 全仓库只被 `tests/` 调用，`api.py` 一次都没有调过 |

播放路径仅使用 `FilesystemBackend`，backend 列表却明示存在另一套来源。

## 决策

- 删除 `StashAdapter`，以及只为承载它而存在的整层契约：`MediaBackend`、
  `MediaCapabilities`、`StreamCandidate`、`FilesystemBackend.capabilities/stream_candidates`、
  `MediaEngine.capabilities/stream_candidates` 与 `adapters` 构造参数。
- 删除 `MediaAsset.bindings` 与 `external_id()`，以及 `media_asset()` 里那条按资产查
  `media_binding` 的 SELECT，它只服务这个 adapter，且必然返回 0 行。
- `MediaEngine` 保留 `repository + filesystem`：解析媒体只有一条路径，谁决定了这个
  路径可以直接读出来。
- **不动数据**：`media_binding` 表、`asset.stash_scene_id` 列和 `source='stash:*'` 的
  12858 条 `asset_tag` 都是历史断言的溯源，全部保留。这次改的是代码，不是账本。
- `src/peach/stash.py` 保留：`scripts/ledger.py stash` 和 `scripts/import_stash_entities.py`
  两个离线导入脚本仍然用它。它们不在服务进程里，Stash 不跑时也只是这两个命令用不了。

## 后果

- FastAPI 服务运行期不再向 `127.0.0.1:9999` 发任何请求。是否卸载 Stash 变成纯粹的
  本机运维决定，与 Peach 无关。
- 每次 `media_asset()` 少一次 SQLite 查询。
- 转码、预览和搜索都不受影响：它们本来就是 Peach 自己的实现：`TranscodeService`、
  `PreviewService` 加 `scripts/sheets.py` 的 FFmpeg 九宫格、SQLite 查询，从不经过
  Stash。`MediaCapabilities` 里的 `transcode`/`search` 只是声明字段，没有任何调用方
  读过。
- 想恢复 Stash 作为播放来源，要连 `media_binding` 的回填一起重做。Git 是归档，
  这段代码留在历史里。

## 未做的部分

`scripts/ledger.py stash`（场景/标签/Studio/Performer 回灌）和
`scripts/import_stash_entities.py`（身份导入）仍在。它们已经跑完并把结果写进账本，
现在只在需要重新导入时才有用，而重新导入需要 Stash 进程还活着。真正卸载 Stash 时
一并退役，对应 [docs/STASH.md](../STASH.md) 的第 6 步。

## 修订：两个离线导入脚本已退役（2026-09-08）

不再保留 Stash 作备胎，[docs/STASH.md](../STASH.md) 的第 6 步随之完成：`scripts/ledger.py`、
`scripts/import_stash_entities.py` 与只为它们存在的 `src/peach/stash.py` 一并删除，
仓库任何一处都不再连 Stash。「不动数据」那条不变：`media_binding`、`asset.stash_scene_id`
与 `source='stash:*'` 的断言仍是溯源，不清理。想重新导入只能从 Git 历史取回脚本。
