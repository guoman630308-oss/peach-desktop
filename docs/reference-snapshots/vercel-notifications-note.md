# Vercel Notifications 中性说明 Note 证据（2026-08-30）

## 取得方式

- 页面：`https://vercel.com/<account>/~/settings/notifications`
- 登录态手工截图，尺寸 `1990×139`，SHA-256 为
  `ae1d66ee4147aeeeb4c39b48e21cf655fd8c7b73f0b48034fbbc214bfec097c78`。
- 复核 Peach 时截下的 `59×61` 错误图标，SHA-256 为
  `a218e6ed234f1b77c5a276455e53818d27b86460ff569f7ec4fea063164e4a97`：圆环过细，
  `i` 的圆点不可见，不能作为已对齐的结果。
- 内置浏览器已打开该页面；继续读取实时 DOM／CSS 时在 30 秒后返回
  `js execution timed out; kernel reset, rerun your request`。实时结构与计算样式未取得，
  不用猜测补齐。

## 截图锁定的结构

- 补充说明使用一整行中性深色表面，四周圆角，不作为正文末尾的悬空小字。
- 左侧是圆圈信息图标，正文在同一行；存在真实后续动作时，链接紧接正文并加强字重。
- 组件没有关闭按钮，属于持续可见的上下文说明；文字过长时应由正文列自然换行。

## Peach 的复用与差异

- `/taste` 的“不合口味”归因说明和底部隐私说明复用已有 `noteHtml` secondary Note，
  沿用本地 Lucide 圆圈信息图标、语义 `role="note"` 和现有颜色变量。
- Lucide `info` 的圆点是长度 `.01` 的短线；Note SVG 必须显式使用 2px 描边与圆形端点，
  否则圆点会因默认平端点消失。该规则与图片详情已有的正确渲染保持一致。
- 两条说明都没有可执行的后续动作，因此不伪造链接或箭头；错误与页面级问题继续分别使用
  error Note 与 Banner，不扩大中性 Note 的语义。
- Note 与相邻内容保持口味页统一的 16px 内缩；窄屏只允许正文自然换行，不产生横向滚动。
