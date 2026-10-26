# Vercel Geist Command Menu、Search Input、Spinner 与 Loading Dots

> 本文是 Peach 的人工取证笔记，不在 `reference-sources.json` 里登记；同 id 来源的上游 Markdown 原文锁定在 `upstream/vercel-geist-command-menu.md`，`accept` 只覆盖那个文件。


首次取证日期：2026-08-30；Button 补充复核：2026-09-01；Spinner 与 Button 的 Best Practices 复核：2026-09-07。

## 锁定来源

- <https://vercel.com/geist/command-menu> HTML SHA-256：`0AC2CA5DBC1FC07E48700206C8E8EC21C447460C089D65CFC3324CAC9D0EC13E`
- <https://vercel.com/geist/search-input> HTML SHA-256：`55F62A034450EB68A4D2A46726A0A9A8CF806570C2E31162C6C869F4B38C21A2`
- <https://vercel.com/geist/spinner> HTML SHA-256（2026-09-07）：`8DCD5E6FE5A95A8499A1AA1EA38DCD6625F836AFB83F7073C47F6B92E376F84D`
- <https://vercel.com/geist/loading-dots> HTML SHA-256：`CB5548EF02D135DFA26DF1A9881F3E57E79F3F846C4DE3F3A94FB4AC4CEB8983`
- <https://vercel.com/geist/button> HTML SHA-256（2026-09-07）：`8BDE0FC499385735A861F27193C55152F12215CFD2E235700B143205C7920576`
- 官方 CSS `1zsi1fomvr48z.css`：`62B3416EB6D73D86FCF284B5A9FD05A69DCBB091E3D79B6725EC83F0990E6ECE`
- 官方 CSS `328y7_b581oob.css`：`3034E6739AE0E19814DF6E53BA7FEBEFE56CF986ED0F1C3534DF9AEE1F751B87`
- 官方 CSS `3ej-07gndl9ds.css`：`587B35B6741B202EE2A7ACAFB4D2A9C6EA2A2819DA55BCF218634FA91A5617EE`

通过项目既有 OpenSSL curl 路径取得。登录态浏览器已加载 Vercel 后台，但导航到 Command Menu 的自动化调用在 30 秒超时并重置运行时，因此实时动画帧与计算样式记为“未取得”；实现只采用锁定 CSS 中可重放的数值。

## 可迁移事实

- Command Menu 是全屏覆盖层。官方 Dialog 打开时使用 `350ms cubic-bezier(.4,0,.2,1)`，内容从 `translate3d(0,-40px,0); opacity:0` 到原位；关闭反向，背景同步淡入淡出。
- Search Input 前缀是搜索图标。Peach 在请求期间原位替换为 Spinner，输入框几何不变。
- Spinner 用于用户直接触发且等待结果的动作，例如按钮与分页；容器声明 `aria-busy=true`，Spinner 自身为 `role=status`。
- Spinner 的适用面写成三档具名场景：提交按钮、原位图标刷新（`inline icon refresh`）、行级重试，等待时长约一到三秒。图标键在忙时把自己的图标换成 Spinner 属于第二档，是官方做法。
- 等待超过约一秒要配上说明这次在做什么的文案（`Verifying…`、`Deploying…`），让人知道被什么挡住。
- Spinner 的尺寸对齐周围的字号或图标尺寸，不对齐父容器。
- Spinner 只在动作开始之后挂载：预渲染再切换可见性会在静止时露出一段停住的转角，读成卡顿。
- Button 官方用 `loading` 表达请求中状态，明确要求触发器继续可聚焦并向辅助技术播报 busy；原生 `disabled` 只用于动作当前确实不可能执行的情况，而不是请求等待期。
- 官方 Spinner 由 10 根径向条组成，相隔 36°，持续 1000ms，延迟从 -900ms 到 0ms，透明度从 1 降至 .15。
- Loading Dots 用于仍在后台推进、总量未知的工作，不代替用户动作按钮的 Spinner。
- `prefers-reduced-motion: reduce` 下不得强行动画；状态文字与 ARIA 仍要保留。

## Peach 采用与差异

- 设置面板复用 Dialog 轨迹；普通来源菜单仍按既有证据无动画，不能把 Command Menu 动画套给所有展开控件。
- 添加关注使用 Search Input 前缀；搜索时只把前缀替换成 Spinner，来源筛选保留“全部来源”可见文字。
- 检查、加载、保存、应用以及用户点下的“抓更早的一页”使用 Spinner；只有离开该按钮后仍在后台推进的资源扫描说明使用 Loading Dots。
- 原生页面没有 Geist React 的 `loading` prop，因此 `setActionBusy()` 同时写入 `aria-busy=true` 与 `aria-disabled=true`，全局拦截重复点击并降低饱和度／不透明度；不用原生 `disabled`，焦点留在触发器上。
- Peach 是原生 ES module，不引入 Geist React 运行时；共享实现位于 `web/js/ui-components.js`。
- 「换一批」是 36px 的图标键，忙时把 shuffle 图标原位换成 16px Spinner，尺寸与图标一致。不给它另做一套「洗牌」动效：官方 Spinner 就是原位图标刷新这一档的做法，自研一份既没有取证也会让站内出现第二种忙态。
- 图标键放不下进行中的文案，所以那句文案落在 Spinner 的 `aria-label` 上，写成进行中的说法（`正在换一批`）而不是按钮已有的名字；键上的 `title` 与`aria-label` 仍在，鼠标停住照样读得出这是哪一颗。
- “没有更多历史内容”是成功的终止状态，使用中性可关闭 Note，不使用红色 error Note。
