# Geist Toast 取证记录

- 取证日期：2026-09-01（复核）
- URL：<https://vercel.com/geist/toast>
- 取证方式：web reader 抓取页面正文（Geist 文档站是 React 渲染，视觉规格拿不到
  可哈希的快照；这里记的是规范文字，要复核就重新抓一次）
- **不在 `docs/reference-sources.json` 里登记**：与 `vercel-geist-controls-measured.md`
  同一策略——没有可哈希的上游快照，塞进去会让 `sha256` 字段在别处的含义失真。

## 规范要点（原文摘录，翻译从简）

- **When to use**：toast 用于用户主动动作的非阻塞确认（`Domain added`、
  `Project archived`），不应承载关键信息，也不能替代 dialog。
- **When not to use**：不要用 toast 做系统级的被动通知，不要堆叠超出阅读
  能力的多条 toast，不要在 toast 里放需要用户消费的长内容。
- **Error handling**：失败不能只有 toast。规范原文（大意）：
  > Don't use a toast alone for failures the user has to triage. Pair a ≤6-word
  > toast ("Build failed") with a persistent row that contains the recovery step
  > and a stable identifier, so support tickets and screenshots stay actionable.
  短 toast 只报「失败了」，原因与恢复步骤放在页面里的持久行上。
- **Preservation**：默认自动消失；只有用户必须读到/必须操作时才常驻（preserve）。
- **Accessibility**：异步更新（toast、校验）需要 `aria-live="polite"`
  （与 vercel-web-interface-guidelines 一致）；toast 不自动抢焦点。
- **Undo**：可逆操作使用过去时结果 + 单一 `Undo` 动作，保留 5–10 秒；Peach
  使用 8 秒和本地化的“撤销”。撤销本身必须等待真实写入成功，失败另报短 toast。
- **Async flow**：一次用户动作只在终态发一条 toast，不用“开始／处理中／完成”
  连续三条 toast 复述同一个流程；等待只留在原触发器的 Spinner。
- **Toggle**：持久化的 Toggle 在 `change` 后给成功 toast；只打开面板、菜单或
  Dialog 尚未产生写入，不发伪成功回执。
- **Related**：需要用户立即处理时用 Banner / Note，而不是 toast。

## Peach 复用了什么

检查更新的反馈拆成两条通道（`web/app.js` 的 `followCheckToast` /
`followCheckFailNote`，样式在 `web/css/17-overlay.css`）：

- 「检查了 N 个来源：新增/更新/回查…」是用户主动动作的回执 → toast，
  右下角、自动消失、hover 暂停、右上角关闭，`aria-live="polite"`。
- 失败（含证据未留档）→ 一句短 toast（`N 个失败`），原因明细与
  「去管理关注处理」的恢复入口留在页内持久行（`.fcheckreport`，左侧红条），
  toast 关掉也还在。
- 上次检查的存量失败是持久状态，用页内行（`.fwarn`）+ 恢复入口表达，不走 toast。
- toast 的量纲不取自本次抓取（正文没有视觉规格），沿用
  `vercel-geist-controls-measured.md` 的实测值：8px 卡片圆角、发丝边、
  比页面亮一档的表面、无重底色。
- 详情、批量、关注管理和播放列表的写操作统一调用 `actionReceipt()`；适合逆向
  写回的反馈、标签、稍后看、版本需求、偏好和状态提供“撤销”，永久删除、凭据
  与保存到账本等不安全或不可逆操作只报终态，不伪造撤销。
