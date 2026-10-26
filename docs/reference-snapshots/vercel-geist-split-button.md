# Geist Split Button 实测记录

- 取证日期：2026-09-07；「悬停」一节 2026-09-25 复测
- URL：<https://vercel.com/geist/split-button>
- 取证方式：在浏览器里打开 Default 示例，对包裹层与两颗按钮读 `getComputedStyle` 和
  `getBoundingClientRect`，再点开触发档读菜单项文字；规范正文取页面 Best Practices 一节
- **不在 `docs/reference-sources.json` 里登记**：那张表的契约是「每个来源都有可哈希的上游
  快照」，这一页由 React 渲染，记下的是渲染结果，随上游发布变化且没有可锁定的文件哈希。
  要复核就按上面的方式重测一次。

## 实测几何

包裹层是一个 `div`，里面两颗 `button` 紧挨着，中间没有间隙：

| 位置 | 宽 × 高 | 圆角 | 说明 |
| --- | --- | --- | --- |
| 包裹层 | 95.4 × 40 | 0 | 只负责并排，没有自己的边框和底色 |
| 主动作（左） | 54.4 × 32 | `6px 0 0 6px` | 文字标签，`padding:0 6px`，14px/500 |
| 触发档（右） | 41 × 32 | `0 6px 6px 0` | 只有一枚箭头，`aria-label="Select save method"` |

两颗共用同一个底色（`rgb(23,23,23)`），交界处的竖线由触发档的 `::before` 画出来：
`left:-1px`、`width:1px`、`height:100%`，浅色主题 `#404040`、深色主题 `#cdcdcd`。
主动作 `border-r-0`、触发档 `border-l-0`，所以整体看着是一个盒子而不是两颗按钮。

## 悬停

2026-09-25 复测（agent-browser 打开同一页 Default 示例，逐颗读 `getComputedStyle`，
悬停用真实指针移入）：

- 底色画在两颗按钮各自身上，包裹层 `div.flex.relative` 没有底色、边框和圆角。两颗都是
  `relative z-[1]`、`focus:z-[2]`，边框 `1px` 取 gray-400（浅色 `rgb(235,235,235)`、深色
  `rgb(46,46,46)`），过渡 `150ms cubic-bezier(0.4,0,0.2,1)`。
- 悬停只改指针下那一颗：浅色主题静止两颗都是 `rgb(23,23,23)`，指针在哪半哪半变成
  `rgb(56,56,56)`（`hsl(0,0%,22%)`），另一半不动；深色主题静止 `rgb(237,237,237)`、悬停
  `rgb(204,204,204)`。字色两态都不变。
- 分隔线不随悬停变色，高度实测 30px（32px 减去上下两条边框），也就是上下顶满内容盒。它在
  触发档里、`left:-1px` 压在主动作最后一列上；触发档排在后面，同为 `z-[1]` 时它整颗画在
  主动作之上，所以不管悬停哪半，线都在最上面。
- 线色与悬停填充几乎同一档（浅色线 `#404040`、悬停 `#383838`），悬停时那半与线融成一片，
  静止时线清楚可见。禁用时线 `disabled:before:opacity-10`。

## 菜单

默认 `menuAlignment="bottom-start"`，菜单贴主动作左边缘往下开。Default 示例的菜单项：

1. `Save` —— 和左半那颗的可见标签一字不差
2. `Save + Redeploy`

每项可以带一行说明（示例里是 `Save changes` 与 `Save changes and create a new production
deployment`），说明不参与和主动作的比对，第一行标签才是。

## 规范正文要点

- 一个动作是明确的默认值、旁边还有 1–4 个近亲变体时才用 Split Button；不相干的动作用普通
  Menu。
- 主动作必须原样作为菜单第一项，可见标签和菜单项标签必须完全一致——键盘和读屏用户只走
  菜单这一条路。
- 主动作只能是 default 或 secondary。API 明确挡掉破坏性变体：把删除藏进下拉是个尖角。
- 菜单项用 Title Case 的「动词 + 名词」；破坏性项排在最下面，上面加分隔线。
- `menuButtonLabel` 是触发档的 `aria-label`，写成描述这组动作的句子（如 `More deploy
  options`），它是读屏用户听到的唯一名称。
- 默认 `bottom-start`；只有按钮贴着容器右边缘时才换 `bottom-end`。

## Peach 的落点

口味页顶栏原本并排摆着「读取浏览器历史」和「导入历史文件」——同一件事（把浏览记录喂给口味
分析）的两种取得方式，权重却看着一样。现在合成一个 Split Button：左半是读取本机浏览记录，
右半的箭头开出菜单，第一项与左半同名同事，第二项是导入历史文件。

数据管理页「扫描与采集」卡用同一颗。全站只有一份样式（`web/board.css` 的
`[data-split-button]` 那几条），两半都是 BoardUI 主按钮：

- 高度 36px 与同一行的下拉框齐平，外圆角由按钮组那一层裁出。
- 底色画在两半各自身上（`--board-blue` 渐变），悬停只淡入指针下那一半自己的
  `bg-button-primary::before` 悬停层，另一半不动，与 Geist 的分层一致。
- 中缝是触发档的 `::after`：`left:-1px`、上下顶满、1px，颜色取悬停那一档的起始色
  `--color-accent-400`，悬停时不变。
- 骨架里两半是原生 `disabled`，外圈 `--border-15` 画在整组上，中缝退成同一色。

两半的悬停、中缝几何与骨架禁用面由 `frontend/e2e/design-controls.test.ts` 量 computed style 守住，
结构由 `tests/test_web_ui.py` 的 `test_history_actions_are_one_split_button_with_the_primary_mirrored` 守住。
