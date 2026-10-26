# Vercel Geist Fieldset／Scroller／Empty State 证据

> 本文是 Peach 的人工取证笔记，不在 `reference-sources.json` 里登记；同 id 来源的上游 Markdown 原文锁定在 `upstream/vercel-geist-fieldset.md`，`accept` 只覆盖那个文件。


- 取证日期：2026-08-30
- 官方页面：<https://vercel.com/geist/fieldset>、<https://vercel.com/geist/scroller>、<https://vercel.com/geist/empty-state>
- 官方 HTML SHA-256：Fieldset `F8EEB7F54882944131CF9D65EF0EC2A04121FFC011285E0B6B9CD07A552671AE`；Scroller `1BFF2EB9B2D0FA05B737043458A7536B1E263176784C592E13E1180A09A13826`；Empty State `5DAC2EB1E7CC748994FAE9A25CCC2AFD6066BBF29CC93715FF5CA1AC73880B9C`

## 已取得

- Fieldset 根节点使用 `data-geist-fieldset`，正文与页脚分别使用 `data-geist-fieldset-content`、`data-geist-fieldset-footer`。正文内边距 20px；副说明上 8px、下 20px；Footer 最小高度 56px，内边距为上／右／下 12px、左 20px，并以顶部发丝线与正文分隔。
- Fieldset 的官方说明是“把相关表单控件放入有边框的卡片，并可附带 footer actions”。因此 Peach 人工复核卡使用 fieldset；各卡固定同高，变化内容留在正文 Scroller，判定按钮留在固定 Footer。
- Scroller 根、遮罩、正文分别使用 `data-geist-scroller`、overlay、`data-geist-scroller-container`；纵向示例固定 220px。正文只在指定轴滚动、隐藏原生滚动条并使用约 40px 边缘渐隐提示剩余内容；键盘焦点顺序保持 DOM 顺序。
- Empty State 由图标 tile、标题、说明和可选 actions 组成。官方图标为 32px、tile 内边距 10px；标题与说明间距 8px，说明最大宽度 340px。标题下的解释不能拆到组件外。

## 未取得

登录态浏览器已取得 Fieldset 页面 DOM；随后读取 `getComputedStyle` 时控制器在 20 秒后超时并重置，故浏览器计算样式与页面截图未取得。没有用目测值补齐；以上尺寸来自同日下载并锁定哈希的官方 HTML／CSS 资源。

## Peach 适配

- 不引入 Geist React 运行时；共享原生 ES module 输出 Empty State／Scroller 结构，CSS 复用已锁定的层级、间距和 ARIA。
- 人工复核卡统一为 440px 高，正文 20px 内边距并纵向滚动，Footer 最小 56px；大量预览不再另套第二个滚动区。
- 回收站、普通零结果、重复文件、复核、高清版、播放列表与关注页空态统一包含标题和说明；页面工具条与空态之间统一为 16px。
- 异步页面使用导航代际 token；即使快速离开又返回相同路径，旧请求也不能覆盖新页面。

## 2026-09-02 追加：窄屏下的 Fieldset 操作条（浏览器计算样式实测）

在 <https://vercel.com/geist/fieldset> 上把视口设为 375×812，读页面上 12 颗 footer 按钮的
`getBoundingClientRect` 与 `getComputedStyle`。React 渲染的页面没有可哈希的上游产物，
证据是这次的实测读数本身，口径同 `vercel-geist-controls-measured.md`。

- 操作条在 375px 下仍是 `display:flex`、`flex-direction:row`、`flex-wrap:nowrap`、`align-items:center`；
  没有任何断点把它竖起来。
- 高度是 `min-height:56px` 而不是固定高：说明换行时实测长到 65px（一行）、85px（两行）、105px（三行）。
- 内边距 `12px 12px 12px 20px`——右边比左边窄，按钮那侧收紧。
- 按钮一律保持内容宽度：`Accept` 71px、`Update` 71px、`Save Changes` 118px、
  `Change Password` 142px、`Verify API Connection` 166px、`Update Payment Method` 186px，
  条宽 277px，**没有一颗铺满**。按钮靠 `margin-left:auto` 推到最右，说明占左侧剩余宽度并换行。

## Peach 适配（2026-09-02）

- 数据管理与资源同步的操作条由固定 `height:52px` 改为 `min-height:52px` + 上下 8px 内边距，
  说明行数变多时长高而不是溢出。
- 删掉 640px 以下把操作条改成 `flex-direction:column`、按钮 `width:100%` 的那一段——
  它是我们自己加的，Geist 在 375px 下并没有这个断点。说明加 `min-width:0` 允许被压窄换行，
  按钮加 `flex:none` 不参与压缩。
- 操作条按钮统一填 `--surface`：条子本身是 `--overlay-5`，更深的底才让按钮从条子上分出来。
  此前数据管理那六颗是透明底，和同一页「网盘与账本」的 `.resourceaction` 并排时是两种按钮。
