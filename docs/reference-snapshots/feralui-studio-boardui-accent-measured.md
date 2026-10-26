# feralui 工作台面板与 BoardUI Accent color 圆钮实测记录

- 取证日期：2026-09-16
- URL：<https://feralui.dev/gradients>、<https://www.boardui.com/components/button>
- 取证方式：在实时页面上读 DOM 与计算样式；feralui 一侧另取两份样式表的 SHA-256。
- 样式表指纹：
  - `/assets/JapaneseGradients-DGpoXXSy.css` SHA-256 `e48fa12445d1c3834857ca50a913658420556159c238acf38f2cf1727f2597ec`
  - `/assets/index-GXg76-98.css` SHA-256 `f11598620f1c71fba79f28d1061c8e5343ab95d1c96ebfcbbe82e3a97dbbe305`
- 以下数值转录自上述实时页面读数，未在采用这些值的分支上复抓，不声称重测。

## 不登记进 `docs/reference-sources.json` 的理由

这是一份人工取证笔记。feralui 一侧和 `feralui-gradients-measured.md` 同因——闭源 React
SPA，可读的只有构建产物，页面主体由运行时生成；BoardUI 一侧取的是实时 DOM 上的 Tailwind
计算值，上游发布物里没有对应的、可重抓可哈希的声明式字节。按本目录的约定，这类实测留在
快照正文里说明，不进登记表。

## feralui.dev/gradients 工作台

| 部位 | 实测 |
| --- | --- |
| 面板 `aside.jg-panel` | 340px 宽，`background: rgba(33,30,26,.52)`，`backdrop-filter: blur(20px) saturate(1.15)`，1px 玻璃边，圆角 18px，内边距约 18.4px / 底 21.6px，分组之间 `gap: 1.3rem`，超高 `overflow-y:auto` 且隐藏滚动条 |
| 分组标题 `.jg-panel-group h4` | 11.5px、600、字距 .05em、大写、灰字；`.jg-group-head` 是标题与右侧小按钮的 space-between 行，下边距 .5rem |
| 参数行 `.jg-field-row` | `display:flex; align-items:center; gap:10px`；标签 `.jg-field-label` 固定 84px、10.5px、600、字距 .04em、大写、灰字、不换行；拉条 `flex:1; min-width:0`。同组多行 `display:flex; flex-direction:column; gap:12px` |
| 拉条 `.dial-slider`（自绘，不是 `input[type=range]`） | `role="slider"`、`tabindex=0`、`aria-valuemin/max/now/valuetext`；高 30px，`touch-action:none`，`user-select:none`；轨道 `.dial-track` 圆角 10px、`color-mix(in srgb, ink 11%, transparent)` 底加 `inset 0 1px 2px` 内影；填充 `.dial-fill` 是 ink 42%（激活 55%）；刻度 `.dial-ticks span` 每 10% 一根 1px×8px、ink 20%，默认 opacity 0，悬停或拖动时显示；抓手 `.dial-handle` 3px×20px 圆头竖条、ink 色、`0 1px 2px` 阴影、默认 opacity 0，悬停或拖动时显示；读数 `.dial-value` 绝对定位在轨道右侧 12px、13px、600、tabular-nums；聚焦 `outline: 2px solid` ink，offset 3px |
| 颜色行 `.jg-stop-row` | 最小高 54px、内边距 7px 7px 7px 8px、圆角 12px、透明底，悬停 ink 3.2% 底，展开态 ink 4.6% 底加 1px inset 线；主按钮 `.jg-stop-main` 是 34px 圆点 `.jg-stop-dot`（`inset 0 1px 1px #ffffff6b, inset 0 -1px 2px, 0 0 0 1px #ffffff29` 加 145deg 高光叠层）+ 两行文字（名称 13px 600；十六进制 11.5px 灰）+ 14px 折角图标；行尾 24px 图标键。相邻行之间是一条从 54px 起、右缩 6px 的 1px 渐变分隔线 |
| 颜色弹层 `.jg-stop-pop` | 绝对定位在行下方 `top: calc(100% + 6px)`，左右贴齐行，圆角 16px，卡片底 98% 不透明加 `blur(18px) saturate(1.18)`，阴影 `inset 0 1px 0 高光, 0 0 0 .5px, 0 2px 6px, 0 18px 44px -16px`，入场 `.3s cubic-bezier(.32,.72,0,1)` 从顶部中心放大；装不下时 `[data-up]` 翻到行上方 |
| 弹层内容 | 顶部一排胶囊 `.jg-pill`（Custom / All / Grey / Red / Yellow / Green / Blue / Purple / Brown，5px 13px 内边距、999px 圆角、ink 6% 底、12px 500、选中 ink 底反白字），横向可滚、底部 1px 分隔；下面 `.jg-palette` 是 `repeat(auto-fill, minmax(34px, 1fr))` 网格、间距 8px 9px、最大高 218px 可滚，每格 `.jg-palette-swatch` 34px 圆、1px 半透明边、内高光，悬停 `scale(1.055)`，选中 `0 0 0 1.5px 卡片色, 0 0 0 2.5px ink 30%` 双环。**没有任何原生取色器** |
| 预设 `.jg-chiprow` | 4 列网格、间距 8px；每个 `.jg-chip` 是 40px 圆点（`conic-gradient(from -90deg, 色1 0 25%, 色2 25% 50%, …)` 按颜色数等分）+ 11px 两行标签，选中双环 `0 0 0 1.5px 卡片色, 0 0 0 2.5px ink 32%`、标签变 ink 色 600 |
| 分段切换 `.jg-seg` | 2px 内边距、13px 圆角轨道、滑块 `.jg-seg-thumb` 999px 圆角随选中项 `transform/width/height .28s cubic-bezier(.32,.72,0,1)` 位移 |

## boardui.com 右下角 Accent color

| 部位 | 实测 |
| --- | --- |
| 浮动容器 | `fixed right-4 bottom-4`（桌面 right-6 bottom-6），纵向 `flex-col gap-2`，先配色钮后明暗钮 |
| 触发钮 | 40×40 圆形，`bg-background-primary-default` 白底、1px `border-button-default`（浅色实测 `rgb(235,235,235)`）、`shadow-dropdown`（`0 1px 1px rgba(0,0,0,.04), 0 4px 4px rgba(0,0,0,.02)`）、`backdrop-blur-sm`（8px）；图标是 Remix `palette` 20px、次级图标色，悬停变主图标色与 hover 底；右下角 `size-2.5`（10px）圆点，`bg-accent-500` 当前强调色，`ring-2` 环色等于按钮底色，位置 `-right-1 -bottom-1`（实测距钮右下各 6px 内）。`aria-label="Accent color"`、`aria-expanded` |
| 弹层卡 | 248px 宽、196px 高（20 色时）、圆角 24px、内边距 10px、白底 1px 边、同 `shadow-dropdown`，入场 `opacity-0 scale-95 blur-[2px]` → 正常，`duration-150 ease-out` |
| 弹层内容 | 头部行 `px-1 pb-2` 左「Accent color」caption 灰字，右「↻ Reset」文字键（12px 图标）；主体 `grid grid-cols-6 gap-1.5 px-1`（间距 6px），每格 28px 圆球 `radial-gradient(circle closest-side, 亮 0%, 暗 100%)` 加三层白色高光叠片（顶部 38.5% 高的椭圆高光、更小的一片、底部反光），悬停 `scale-110 duration-150`；底部一枚全宽次级按钮「Copy theme CSS」。选中态实测未标出（`aria-pressed` 缺失），Peach 自己补选中环 |

## Accent 换色的机制与它的引用来源

弹层换的是什么，仓库里就有原文，不必回到线上取：BoardUI 的源码已经 vendored 在
`frontend/src/react/boardui/`。

| 事实 | 出处 |
| --- | --- |
| 强调色是十一级色阶 `--color-accent-50…950`，默认整组指向 blue 基元 | `frontend/src/react/boardui/styles/theme.css` 第 25–45 行 |
| 主按钮渐变、ghost 与 link 按钮、复选框、单选、开关、滑块、Tabs、侧栏选中、焦点环、日期区间选择都引用 `accent-*`，不写死色相；换色就是运行时改写这十一个变量 | 同上，第 25–34 行的注释原文 |
| 图表、状态胶囊、日历事件、信息提示那几处的蓝有意继续引用 `blue-*`，不跟强调色走 | 同上，第 32–33 行 |
| 运行时改写这十一个变量的模块是 `components/application/theme/accent.ts` | 同上第 31 行的指路；**该文件未取得**——vendored 进来的只有 `styles/theme.css`，那个模块不在仓库里，它到底把变量写到哪个元素上没有直接证据 |
| React 子树在 `.peach-react` 上重新声明的同名语义 token 里，`--color-border-focus-ring` 本身就写成 `var(--color-accent-500)` | `frontend/src/react/styles.css` 第 99 行 |
| 十一级里每一级都真的有人引用（50 与 200 各 1–2 处，500 最多 11 处） | 构建产物 `web/dist/peach-react.css` |
| 弹层里到底是哪二十个色相 | **未取得**：没有回到 boardui.com 复抓，上面那张表只记了「20 色时」的卡高 |

Peach 因此不照抄它那二十档，也不猜 `accent.ts` 的写法：十一级色阶按 `:root[data-accent=…]`
一档一条写在 `web/board.css` 里，排在层叠层之外，无论 `/dist/peach-react.css` 那份
`@layer theme` 怎么写都压得住；`.peach-react` 上的语义 token 引用的是同名变量，值顺着根
继承进去，两边因此不会各说各话，`tests/test_frontend_build.py::BoardTokenTests` 那条逐字
比对也照旧成立。色相取 Tailwind v4 色板原文十二个，blue 的 400 用 BoardUI 自己那枚
`#3392ff` 覆盖值。

## Peach 主动保留的差异

| 差异 | 为什么 |
| --- | --- |
| 分组标题与参数标签用中文、不大写、不加字距 | 全站文案是中文，中文没有大小写；照抄 `text-transform:uppercase` 只会让英文参数名和中文参数名在同一列里长成两种东西 |
| 颜色一律走 token（`--ink` / `--line` / `--ground` / `--muted`）经 `color-mix` 得到，不写 `rgba(33,30,26,.52)` 这类字面值 | feralui 只有一套深色皮；Peach 两档主题都要成立，写死的玻璃色在浅色一档上会变成一块脏灰。`tests/test_web_ui.py` 也会拒绝样式分区里的字面色 |
| 圆角只用 `--badge/control/surface/floating/pill/tag-radius` 或 50%，不抄 18px / 16px / 12px / 24px | 圆角在这套界面里是词汇不是数值，见 `web/css/01-base.css` 顶部那段说明；侧栏弹层的 24px 例外落在 `web/board.css` 里，那一层本来就带 Board 的字面圆角 |
| 焦点环用 `--tungsten`，不用 ink | 蓝在这套色板里只给焦点、链接与数据；拉条自绘之后焦点环得自己画，规矩照旧 |
| 不做色相环 / 原生取色器，只给 7 色系 × 6 明度的命名色板 | 用户点名原生取色器太丑；命名色板还顺带让「挑颜色」这件事留在这套界面的颜色词汇里 |
| 侧栏圆钮的弹层向上开、贴着侧栏底，不是 `fixed right-4 bottom-4` 的浮动件 | BoardUI 把它当示例站的角落挂件；Peach 的侧栏底部已经是设置与主题的落脚处，配色钮排在同一行里才说得出它管的是这套界面 |
| 选中态自己补 `aria-pressed` 与双环 | BoardUI 实测没有标出选中，那不是可以照抄的部分——一排圆球里哪一枚是当前档，读屏和眼睛都得能读出来 |
| 光晕变量写在 `.glowlayer` 这枚空 div 上，不写 `<html>` | 与参考产品无关的自有约束：自定义属性是继承的，写在根上整棵树都要重算样式，实测每帧 12.7–16.6ms，拖动必掉帧 |
| 强调色只给十二档，不给二十档 | 上游那二十档是哪些色相未取得；十二个色相绕色轮一圈已经够挑，六列两行也正好铺满这张 248px 的卡 |
| 弹层里在光晕之外多一组强调色，卡穿的是站内那身液态玻璃 | 上游那枚是示例站角落里的浮动挂件，只管强调色；Peach 这一枚是侧栏底部「换一套外观」的唯一入口，两组挨着才说得出它们是一件事 |
