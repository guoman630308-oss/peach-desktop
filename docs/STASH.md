# Stash 遗留：继承的缺陷与许可证边界

Peach 的代码不连 Stash，适配层与离线导入脚本都已删除（ADR-0021）。要不要卸载本机的 Stash
进程和它的 generated 数据，是本机运维的事，与 Peach 无关。

这份文档只回答两件事：从 Stash 导入的数据带着哪些缺陷，以及分发前必须处理的许可证边界。

这些数据一律保留：`media_binding` 表、`asset.stash_scene_id` 列和 `source='stash:*'` 的断言都是
溯源，不清理。

## 从 Stash 继承的已知缺陷

下面三条仍在影响账本，处理相关数据时要记着。

- Stash 的身份模型是平的：上传者、合集和演员塞进同一个自由文本字段（`演员:` 前缀）。导入
  进来的断言因此需要 `scripts/merge_duplicate_identities.py` 这类工具收拾，规范实体才是真相。
- `asset_tag` 的唯一键是 `(asset_id, tag)`，`INSERT OR IGNORE` 存不下同一断言的多个来源，
  provenance 只能靠 `source` 列近似表达。
- `asset.rating` 的量纲是 Stash 的 rating100（0–100），五颗星按 20 的倍数写；撤销写 NULL。

## 许可证边界

Stash v0.31.1 是 AGPL-3.0。Peach 没有复制它的 Go 实现，只通过公开协议调用过它的独立进程。

分发前要先解决的是 FFmpeg：Peach 管理的那份是从本机既有构建复制来的 GPLv3/x264/x265 shared
bundle，完整 `LICENSE.txt` 已保留，只供这个个人实例运行。它不进 Git；在处理完源码、许可证和
通知义务之前，不能随 Peach 安装包分发。这一条归 ADR-0023 的发布准备阶段。

官方依据：[Stash 架构](https://github.com/stashapp/stash/blob/develop/docs/ARCHITECTURE.md)、[Scene schema](https://github.com/stashapp/stash/blob/v0.31.1/graphql/schema/types/scene.graphql)、[AGPL-3.0](https://raw.githubusercontent.com/stashapp/stash/v0.31.1/LICENSE)、[FFmpeg 法律说明](https://www.ffmpeg.org/legal.html)。
