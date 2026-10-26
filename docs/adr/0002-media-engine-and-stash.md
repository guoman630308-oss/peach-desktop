# ADR-0002：Media Engine 与 Stash 渐进退出

- 状态：Accepted（Stash adapter 部分已由 ADR-0021 关闭）
- 日期：2026-08-14

## 背景

Stash 只覆盖 2,551 个本地 Scene，ledger 覆盖 24,980 个视频及本地、115、PikPak、online 多来源。Peach Web 已直接使用 ledger 和文件串流，但项目仍有 19 个脚本直连 Stash GraphQL、11 个脚本借用 Stash 私有目录中的 FFmpeg/FFprobe。

## 决策

- ledger 是资产、行为与知识的核心真相源。
- 定义 `MediaBackend`/`MediaEngine`，能力至少包括 probe、preview、stream plan、transcode、search。
- Stash 先成为可关闭的 adapter；只通过公开 GraphQL/HTTP 协议集成，不复制其实现。
- Native backend 直接使用可配置的 FFmpeg/FFprobe、标准 HTTP Range/HLS 与 SQLite FTS5，不再硬编码 Stash 的二进制目录。
- 写入方向逐步改为 ledger → 可选 Stash exporter，结束 Stash → ledger 的双库往返。

## 分类

- Peach 必须适配：跨来源身份、成本与可用性策略、行为事件、在线追更、统一实体与 provenance。
- 应复用：FFmpeg/FFprobe、HTTP Range、HLS/DASH、SQLite FTS5、过渡期 Stash GraphQL。
- 应替换：零散 GraphQL helper、Stash 私有 FFmpeg 路径、`rm-web.py` 手写不完整 Range、本地媒体重复 probe/preview 管线。

## 许可证边界

Stash v0.31.1 是 AGPL-3.0。Peach 可把未修改 Stash 当独立进程经协议调用；不复制或移植其 Go 实现。若分发/修改 Stash 或捆绑 GPL FFmpeg build，必须单独满足对应源码、许可证与通知义务。工程结论不替代法律意见。

## 完全去 Stash 的门槛

2,551 Scene 与全部实体关系、hash、预览和播放能力完成对账；19 个 GraphQL 直连与 11 个私有 FFmpeg 路径归零；桌面和 390×844 下 direct/transcode、Range/seek、搜索与回退均通过；关闭 Stash 的影子期和一键回退验证完成。
