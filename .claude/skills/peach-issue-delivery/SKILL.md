---
name: peach-issue-delivery
description: 处理 Peach 的 GitHub issue、关联修复 PR、合并交付或判断 issue 是否可以关闭时使用。
---

# Issue 交付与验证

最后复核：2026-10-19
证据来源：用户确认的 issue 沟通与验收约定、[issue #95](https://github.com/peach-mitao/peach/issues/95)、[PR #96](https://github.com/peach-mitao/peach/pull/96)、[项目看板状态判据](../../../scripts/project_board.py)。

## 沟通与标记

- 默认不自动发表 issue 评论或回复报告者；只有用户明确要求代发时才发送。保留人工已有回复。
- 在用户授权的 issue 维护范围内，优先用现有标签表达平台、影响域与验证状态。先读取标签和原生 Issue Type，避免重复分类。

## 合并责任

- 自己维护的项目可以用 PR 保留审阅和 CI 记录，PR 不是必须由用户手动完成的步骤。
- 用户授权合并后，由你完成必要检查、合并和结果核验；不要把已授权且可执行的合并交回用户。工作树与本地集成按 [peach-worktree](../peach-worktree/SKILL.md) 执行。

## 验证与关闭

- 分别确认代码合并、CI 结果、修复产物发布和 issue 验收；每项结论只使用对应证据。
- 问题依赖特定硬件、部署环境或报告者确认时，通用测试、CI 通过和镜像发布不能代替该验证。必要验证未完成时保持 issue 开放；修复产物可供测试时使用 `status:needs-verification`。
- 尚需验证的 issue，在 PR 正文中用独立的 `Refs #编号` 行关联，跨仓库使用 `Refs owner/repo#编号`。此格式也供项目看板识别实现关联。
- 检查 PR 正文、提交消息以及合并时采用的消息，避免用 `Fixes`、`Closes`、`Resolves` 等关键词触发尚需验证的 issue 自动关闭。
- 关闭前取得覆盖问题验收条件的实际验证证据，或用户明确要求关闭。证据写清测试环境、修复版本、结果与未覆盖范围；不得把未覆盖范围说成已验证。
- 若 issue 被自动关闭且必要验证未完成，重新打开并恢复适用标签；无需为修正状态自动追加评论。

这些是操作约定；现有看板脚本只映射状态，不会阻止 GitHub 自动关闭 issue。
