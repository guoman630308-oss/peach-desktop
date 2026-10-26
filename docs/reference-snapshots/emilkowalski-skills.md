# emilkowalski/skills 动效与移动端规则摘录

- 来源：<https://github.com/emilkowalski/skills>，提交 `e8a175de22ae1e49370fc144c1f3bb9aeedf988d`，2026-10-09 读取。
  摘自 `review-animations/STANDARDS.md`、`mobile-native`、`break-ui` 与仓库根的 `performance-cheatsheet.md`。
- 许可：MIT，Copyright (c) 2026 Emil Kowalski。本文是中文摘录与改写，不复制原文。
- **不在 `docs/reference-sources.json` 里登记**：规则取自多份文件的一次性挑选，没有单一可哈希的上游文件。
  复核时比对上面这个提交与上游最新提交的 diff，只看本文摘过的文件。
- 用法：复核已有动效、移动端交互和组件自测时当清单。Peach 只在有直接证据时新增动效，本文不构成新增动效的证据；
  与 `peach-web-ui`、`transitions-dev-measured.md` 冲突时以 Peach 的为准。与 `jakubkrehel-interface-skills.md`
  重复的条目（不写 `transition: all`、交互态不用 keyframes、悬停门控、浮层 `overscroll-behavior:contain`）只记在那一份。

## 动效复核

| 检测 | 改法 |
| --- | --- |
| 键盘触发、每天上百次的动作（快捷键、切换标签）带动画 | 去掉；频次越高越该即时 |
| 进场、退场或悬停用 `ease-in` | 进退场 `ease-out`，屏内移动 `ease-in-out`，匀速进度 `linear` |
| 按钮、菜单、弹层类动效超过 300ms | 收到 300ms 以内；骨架扫光、Spinner 这类持续动画不算 |
| 弹层从中心缩放 | `transform-origin` 指向触发点；居中的模态框例外 |
| 交叉淡入时两态重影 | 过渡期间加 `filter:blur(2px)` 糊成一次变化；动画中的模糊保持在 20px 以下 |
| 错峰入场阻塞了点击 | 错峰只作装饰，播放期间照常可交互 |
| 高频写入的 CSS 变量挂在父级或 `html` 上，驱动子元素 `transform` | 直接写目标元素的 `transform`，或把变量挂在专用的空元素上；挂在父级会让整棵子树重算样式 |
| 动画属性里有 `width`、`height`、`top`、`margin` | 换成 `transform`／`opacity`；Collapse 一类有证据的高度动画除外 |

## 手势与拖动

- 拖动开始后 `setPointerCapture`，指针移出边界也继续跟手；拖动中忽略新增的触点，防止跳位。
- 甩动关闭按速度判定（位移除以耗时，超过约 `0.11` px/ms 即关闭），不只看距离阈值。
- 拖过自然边界时位移逐渐衰减，不撞一堵看不见的墙。

## 移动端

Peach 已有 `-webkit-tap-highlight-color`、`viewport-fit=cover`、按主题更新的 `theme-color`、`text-size-adjust`
与输入框防放大，这里只记仍需逐处检查的：

| 检测 | 改法 |
| --- | --- |
| 悬停样式只写 `(hover:hover)` | 写成 `(hover:hover) and (pointer:fine)`，排除触控笔和自称能悬停的安卓设备 |
| 可点元素缺 `touch-action` | 按钮、链接、`[role="button"]` 加 `touch-action:manipulation`，点按不等双击判定 |
| 按压反馈只挂在 `click` 上 | 用 `:active` 或 `pointerdown`，手指落下就有反馈 |
| 长按按钮、标签、拖动把手会选中文字或弹出系统菜单 | 这些控件加 `user-select:none` 与 `-webkit-touch-callout:none`；正文和可复制的番号、路径保持可选 |
| 横向滑动区与页面纵向滚动打架 | 滑动区 `touch-action:pan-y`；完全自管手势的面才用 `none` |
| 搜索框、筛选框回车键显示「换行」或「前往」 | `enterkeyhint="search"`；数字输入加 `inputmode="numeric"` |

粘滞悬停、点按延迟、软键盘、安全区和橡皮筋回弹只在真机上出现。浏览器设备模拟里看不出来，
没有真机结果的这几项在验收里写 `未验证`。

## 极端值要真实

组件自测与 E2E 夹具的极端值取生产里真会出现的，或后端接受的最长值，不用 `aaaa…` 这类假数据：
设计者会说「不会有这种数据」，结论就没人看。也不要只测长文本，常漏的是短的和缺的：

- 一个字的名字、只有番号没有片名、缺头像、缺封面、可选字段全空。
- 恰好 0、1、2 项，以及带千位分隔的大数（`1,284`）。
- 不可断的长串：长番号、长 URL、相机原始文件名（`IMG_20250914_183022_HDR.HEIC`）、扩展名在末尾的长文件名。
  末尾截断会吃掉区分它们的那一段，这类值考虑中间截断（见 `vercel-geist-middle-truncate.md`）。
- 一项挂十几个标签、emoji 开头的名字（`charAt(0)` 会切出半个代理对）。

## 不采纳

| 上游规则 | 原因 |
| --- | --- |
| 永远不从 `scale(0)` 起 | 计数徽标的弹出形态取自 transitions.dev 实测，见 `frontend/src/ui-kit/motion.css` |
| 按下缩放到 `0.97`、按压过渡 100–160ms | Geist Button 按下不缩放 |
| 减弱动态效果时保留淡入淡出 | Peach 在全局 `prefers-reduced-motion` 规则里关掉全部动画与过渡 |
| 自定义强缓动曲线（`cubic-bezier(0.23,1,0.32,1)` 等） | 时长与缓动只读 board.css 的 motion token |
| 根元素 `overscroll-behavior:none` | Peach 的主滚动在页面上，不是自管滚动容器 |
| `100svh` 给首屏 | Peach 没有营销首屏，满高面板用 `100dvh` |
