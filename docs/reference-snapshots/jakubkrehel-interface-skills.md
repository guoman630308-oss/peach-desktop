# jakubkrehel/skills 界面规则摘录

- 来源：<https://github.com/jakubkrehel/skills>，提交 `d574cc8a576dc24256ad38268b8d03d86724a1b3`（2026-10-06），2026-10-09 读取。
- 许可：MIT，Copyright (c) 2026 Jakub Krehel。本文是中文摘录与改写，不复制原文。
- **不在 `docs/reference-sources.json` 里登记**：规则散在 7 个领域技能的多份文件里，本文是一次性挑选，
  没有单一可哈希的上游文件。复核时比对上面这个提交与上游最新提交的 diff，只看本文摘过的技能。
- 用法：新增或复核页面时把下列「检测」当 grep 或审查清单。与 `peach-web-ui`、Geist 实测快照冲突时，
  以 Peach 的规则为准；每条都已对照过 Peach 现有做法，冲突的列在末尾「不采纳」。

## 动效与状态

| 检测 | 改法 |
| --- | --- |
| 切换类（`.open`、`.on`、`:hover`）上挂 `animation:` | 换成同属性的 `transition`；keyframes 中途不能反向，只留给只跑一次的序列 |
| `transition: all` | 写出实际变化的属性 |
| `will-change` 写在从不动画的元素上，或写成 `all` | 出现首帧卡顿后才加，写被动画的那个属性 |
| 主题切换时颜色过渡一起播放，整屏拖影 | 切换前临时关掉全部 `transition`，强制一次样式计算，下一帧恢复 |
| 高频交互（按键、行悬停、标签切换）带长动画 | 即时反馈，或只动 `opacity`／`background-color` 且不超过 150ms |
| 状态变化只靠动画表达 | 同时留颜色、图标或文字这类静态线索 |
| 纯 CSS 的悬停样式没放进 `@media (hover:hover)` | 包进去；触屏点一下后 `:hover` 会一直粘着，看起来像选中。Tailwind 4 的 `hover:` 已自带此条件 |
| 可滚动的对话框、抽屉、侧面板缺 `overscroll-behavior` | `overscroll-behavior:contain`，滚到底不带动背后页面 |
| 图标 SVG 内写死 `fill="#…"`／`stroke="#…"` | `currentColor`，悬停、选中、禁用都由 CSS 颜色给 |

## 布局与尺寸

| 检测 | 改法 |
| --- | --- |
| `1fr` 轨道或 flex 子项装长文本、宽表格后撑破 | 轨道写 `minmax(0,1fr)`，flex 子项加 `min-width:0` |
| 移动端满高面板用 `100vh` | `100dvh`，浏览器工具栏和键盘不会盖住底部 |
| 装文字的盒子写死 `height` | `min-height`，或 `max-height` 加内部滚动 |
| 可复用组件里用视口媒体查询判断自己的宽度 | 父级 `@container`，或观察组件自身宽度 |
| `container-type:inline-size` 写在无确定宽度的 flex 项、行内块或绝对定位元素上 | 先给确定宽度；尺寸约束会把收缩适应的盒子压成 0 宽 |

## 文字渲染

| 检测 | 改法 |
| --- | --- |
| 计时、计数、数值列没有等宽数字 | `font-variant-numeric:tabular-nums` |
| `-webkit-line-clamp` 缺 `display:-webkit-box` | 补上它和 `-webkit-box-orient:vertical` |
| 截断后用户没有办法看到全文 | 留一条回路：Tooltip、展开或详情页 |
| `font-weight` 用了字体文件里没有的字重 | 加载该字重，或换成已加载的；否则浏览器合成假粗体 |
| 布局根或文字容器上 `user-select:none` | 删掉，只留在拖动和手势面上 |

## 可访问性

| 检测 | 改法 |
| --- | --- |
| 原生 `disabled` 控件上挂 Tooltip | 原生禁用控件拿不到焦点，键盘用户看不到提示；改用 `aria-disabled` 或旁边写字 |
| 播报区域和文字一起挂载（`{msg && <div role="status">}`） | 先渲染空的区域，再更新文字，读屏才会播报 |
| 成功 Toast 用 `aria-live="assertive"` | `role="status"` |
| `aria-label` 不包含按钮上的可见文字 | 名称以可见文字开头（WCAG 2.5.3） |
| 按钮、链接、输入框的祖先带 `aria-hidden` | 去掉，或整块设 `inert` |
| 正数 `tabindex` | 理顺 DOM 顺序，用 `0` |
| `<div>`／`<span>` 绑点击 | `<button>`；导航用 `<a href>`，要支持 Ctrl／中键新开 |

## 文案

中文措辞与语气按 `tech-doc-style-chinese` 和 `peach-web-ui` 的确认、Toast 规则；这里只补三条：

- 开关的标签描述**开启**时的状态。坏例「不显示已看过」，好例「显示已看过」。
- 链接文字说明去向，不写「点这里」「了解更多」；同屏两个同名链接要分开命名。
- 图标键的 `aria-label` 说动作和对象（「删除关注」），不说图标形状（「垃圾桶」「X」）。

## 极端内容场景

做极端内容自测（`break-ui`）或写 E2E 夹具时，按组件实际能收到的输入挑轴，收不到的轴不跑：

| 轴 | 何时跑 | Peach 场景 |
| --- | --- | --- |
| 内容长度 | 渲染非自写文本 | 空串、一个词、多句、不可断长串（长番号、URL、无空格日文片名） |
| 内容形态 | 文本来自用户或外站 | emoji、假名与汉字混排、数字列对齐 |
| 数量 | 组件按项重复 | 0 项、1 项、常见数量、十倍数量 |
| 容器 | 总是 | 320px 容器、被兄弟挤压、很宽的容器 |
| 状态 | 组件确有该状态 | 加载、错误、禁用、缺封面、竖图封面 |
| 环境 | 项目支持该模式 | 浅色和深色主题、浏览器缩放、减弱动态效果；由用户切换，不在页面里模拟 |

## 不采纳

| 上游规则 | 原因 |
| --- | --- |
| 按下缩放到 `0.96` | Geist Button 源规则按下不缩放，见 `peach-web-ui` |
| 入场错峰、退场模糊、图标交叉淡入的固定曲线和时长 | 没有直接证据不新增动效；菜单开合只用 BoardUI 那一份 |
| OKLCH 色阶、语义 token 命名体系 | Peach 用 `:root` 既有 token 加 `color-mix`，新 token 要先证明现有词汇不够 |
| 主题只用一种切换机制 | Peach 同时写 `data-theme` 与 BoardUI 的 `.dark`，见 `frontend/src/appearance/theme.ts` |
| 行高无单位、字距用 `em` | Geist 字体规格按像素给行高和字距 |
| 每行 60–75 字符、英文大小写与 ICU 复数 | 针对拉丁文字；中文排版按 `tech-doc-style-chinese` |
| 提交按钮保持可点、提交时校验 | `peach-web-ui` 把原生 `disabled` 留给缺输入的状态 |
| 拖动必须有单指针替代 | `peach-web-ui` 规定行内重排用拖动，不加上下移动按钮 |
| 图片加 1px 半透明描边 | 会改动全站封面观感，要先过用户确认 |
| `100vw` 换成 `100%` | 全站隐藏滚动条，`100vw` 不会多出横向滚动 |
