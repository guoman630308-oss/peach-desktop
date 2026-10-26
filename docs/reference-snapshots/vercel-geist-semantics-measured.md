# Geist 语义控件实测记录（按钮/徽章/标题/布局）

- 取证日期：2026-08-29
- URL：<https://vercel.com/geist/button>、<https://vercel.com/geist/banner>、<https://vercel.com/geist/badge>
- 取证方式：浏览器加载页面后读 `getComputedStyle`（与 `vercel-geist-controls-measured.md` 同法）
- **不在 `docs/reference-sources.json` 里登记**：理由同上，渲染结果没有可哈希上游快照。

## Button（dark 主题实测）

| 变体 | 背景 | 文字 | 几何 |
| --- | --- | --- | --- |
| primary | `rgb(237,237,237)` | `rgb(10,10,10)` | 500 字重 |
| danger | `rgb(217,48,54)` | `#fff` | 同上 |
| warning | `rgb(255,153,10)` | `rgb(10,10,10)` | 同上 |
| secondary | `rgb(10,10,10)` 或透明 | `rgb(237,237,237)` | 同上 |

- 小号（default）：32 px 高、`border-radius:6px`、`padding:0 6px`、14 px 字。
- 大号（large）：40 px 高、`border-radius:8px`、`padding:0 14px`、16 px 字。

语义对应：销毁类操作用 danger 实底，暂停/降级类用 warning，主推进动作用 primary
（dark 主题下是白），其余一律 secondary。Dashboard 的危险设置卡（Delete Project）
是红发丝边框 + 底部一条红 tint 区放危险按钮。

## Badge

- 彩色变体：24 px 高、12 px 字、正圆胶囊（radius 999px）、`padding:2px 12px`、实底。
  blue `rgb(0,98,209)`、red `rgb(217,48,54)`、amber `rgb(255,178,36)`（黑字）、
  green `rgb(26,147,56)`、teal `rgb(12,151,132)`、purple `rgb(95,46,133)`、pink `rgb(179,26,87)`。
- 灰色默认（代码示例里量到的）：bg `rgb(26,26,26)`、边 `rgb(41,41,41)`、字
  `rgb(161,161,161)`、radius 6px、mono 字体。Dashboard 的状态徽章走低饱和
  tint（如 subtle 绿/红），不是每行都上实底彩色。

## 标题

- H1：40 px / 600 / `letter-spacing:-2.4px` / `line-height:48px` / `margin:0 0 12px`。
- H2：24 px / 600 / `letter-spacing:-0.96px` / `line-height:32px`。

## 布局与字体

- 文档正文列：body `max-width:1265px`，main 实测 1220px；Dashboard 的设置类
  页面内容列实测约 943px（Team Settings 手工截图）——低密度页面不全宽。
- 正文字体栈：`Geist, Inter, -apple-system, …, sans-serif`，全程无衬线；站方
  栈里没有 CJK 名字，中文站必须自行补 CJK sans，否则 Chrome 的中文默认字体
  （宋体）会接管标题。

## Dashboard 后台实测（2026-08-29，登录态）

- 设置页内容列：卡片实测 **914px**（1265px 视口），居中；低密度管理页不全宽。
- 设置卡：bg `rgb(10,10,10)`，radius **6px**，发丝边（文档示例卡为
  `1px rgba(255,255,255,0.14)`、radius 8px）；卡内标题 **20px/600/26px 行高**，
  描述为 muted 灰；卡底与主体之间发丝分隔，底部一行「左侧 helper 灰字 +
  右侧动作按钮」。
- 危险设置卡（Delete Project，手工截图）：整卡红发丝边框，底部一条红 tint
  区，右侧 danger 实底按钮；Pause Project 用 warning（amber）实底按钮。
- 文档页排版：H1 40px/48 行高/600/`-2.4px`/`mb 12px`；导语 20px/30 行高 muted；
  正文段落 16px/24 行高，`mt 16px`。


## Toggle 与卡片面（2026-08-29 补测）

- **Geist 的「Switch」是分段选择器**（radio 语义，2–3 个互斥视图切换），
  布尔开/关的正确控件叫 **Toggle**。
- Toggle 实测：小号 28×14、中号 36×20、大号 40×24，全胶囊；圆点 = 高-3 px
  （11/17/21），左右各留 2 px。关 = 轨道 `rgb(46,46,46)`、圆点
  `rgba(237,237,237,.84)` 靠左；开 = 轨道 `rgb(0,112,243)`、圆点靠右。
  禁用：轨道 `rgb(31,31,31)`、圆点 `rgb(69,69,69)`。
- **卡片面用贴背景的实底升起面**，不是白色透明叠加：Vercel 后台黑主题
  实测卡片 `rgb(10,10,10)` 置于 `#000` 上，配低透明发丝边。Peach 对应
  `--surface` 置于 `--ground` 上（统计卡即此方案），设置面板此前用的
  `--overlay-5`（白色 5% 叠加）与全站卡片色系不一致，已统一到 `--surface`。

## Tooltip、Collapse 与 Projects 工具栏（2026-08-30 补测）

- Tooltip：<https://vercel.com/geist/tooltip> 当前加载的组件 chunk 为
  `/vc-ap-b3331f/_next/static/immutable/chunks/118l4-ay8nz2o.js`，SHA-256
  `FA7C49EDEB237132B09619409AAF97B09EF265BA8FED71CEBD09D21216F7C8DE`。
  默认最大宽度 250px，8px 圆角，水平／垂直内边距 8／6px，13px 字、1.3 行高；
  内容经 Portal 渲染，打开时才给触发器 `aria-describedby`，触发器默认可聚焦，支持
  hover、focus、触摸和 Escape。上下方向的自动对齐在离左右视口不足 100px 时改为
  left／right boxAlign。
- Collapse：<https://vercel.com/geist/collapse> 当前组件位于
  `/vc-ap-b3331f/_next/static/immutable/chunks/1k513hf7se3-l.js`，SHA-256
  `0BD8AA9351E4CAA33F01CCEC48C68EDAD491BD1D25234964A4482B7E7E273982`。
  触发器使用 `aria-controls`／`aria-expanded`；内容区关闭时 `inert`，以测量高度写入
  inline `height`，`overflow-y:hidden`，高度和 chevron 都使用 200ms `ease-in-out`。
- 登录态 Projects 页 `https://vercel.com/<account>`：2026-08-30 的可见 DOM
  顺序为 Search、`Filter and Sort Projects`、Add New；实测同一行 36px 高、8px gap，
  搜索占剩余宽度，筛选按钮 36×36px，Add New 115×36px。筛选菜单 250px 宽、6px
  内边距、12px 圆角，内部纵向滚动且 `overscroll-behavior:contain`；展开没有动画。
  手工截图的 SHA-256 为
  `037CBD12E6254072817F402864A0B11C641AA920CA4BD9EDEC08D591DAF2EA1E`（1673×189）。

Peach 保留的差异：添加关注沿用现有 38px 输入基线，因此筛选钮是 38×38px；Tooltip
使用 Peach 的暗色 surface 且不复制箭头或淡入动画。复用的是可聚焦语义、严格视口夹取、
菜单无展开动画，以及 Collapse 的高度／chevron 过渡，不把三种组件的 motion 混用。

## Context Card／Toast／Material（2026-08-30 补查）

- <https://vercel.com/geist/context-card>：短元数据卡最多保留一个主动作；键盘可进入卡片，
  Escape 关闭并把焦点交还触发器。触发器的无障碍名称独立存在，卡片只补充上下文。
- <https://vercel.com/geist/toast>：用户主动动作的非阻塞成功回执用自动消失 Toast；失败若
  需要处理，原因与恢复动作仍留在原位置。Toast 区域使用 `aria-live="polite"`。
- <https://vercel.com/geist/material>：浮层的阴影、边框只负责视觉层级，语义必须落在外层
  `role` 和标题关联上；暗色主题不能只靠阴影分层。

Peach 图片灯箱的信息卡据此保持一个“在资源管理器中显示”动作，改为具名非模态 dialog；
触发器通过 `aria-controls`／`aria-expanded` 关联，打开后把焦点移到动作，Escape 只关闭信息卡
并交还焦点。成功回执进入现有 Toast，失败原因保留在卡内。视觉上沿用 Peach 的 12px
浮层圆角、发丝边与双层阴影，不复制 Vercel 品牌样式。

## 选中态与开关色（2026-09-03 实测，深色主题）

用浏览器打开 `vercel.com/geist/*` 的示例块，读 `getComputedStyle` 得到的值。
取证目的只有一个：Geist 的「被选中」到底用不用蓝。

| 控件 | 页面 | 选中／开态 | 未选中 |
| --- | --- | --- | --- |
| Toggle | `/geist/toggle` | 轨道 `rgb(0,112,243)`（蓝），28×14 与 36×20 两档，`background .15s cubic-bezier(0,0,.2,1)` | 轨道 `rgb(46,46,46)` |
| Tabs | `/geist/tabs` | 文字 `rgb(237,237,237)` + `border-bottom:2px solid rgb(237,237,237)`，字重 400 | 文字 `rgb(161,161,161)` |
| Checkbox | `/geist/checkbox` | 框底仍是 `rgb(10,10,10)`，勾 `rgb(237,237,237)`；16px，圆角 4px | 框底 `rgb(10,10,10)`，边 `rgb(143,143,143)`；禁用且选中时底 `rgb(135,135,135)` |
| Switch（分段） | `/geist/switch` | 选中项底 `rgb(26,26,26)`、字 `rgb(237,237,237)`，高 28px | 透明底；容器底 `rgb(10,10,10)`、圆角 6px、内边距 4px、`box-shadow:0 0 0 1px rgba(255,255,255,.14)`、高 36px |
| Button primary | `/geist/button` | 底 `rgb(237,237,237)`、字 `rgb(10,10,10)`、字重 500 | — |
| Badge gray | `/geist/badge` | 底 `rgb(26,26,26)`、边 `rgb(41,41,41)`、字 `rgb(161,161,161)`、圆角 6px | — |
| Collapse | `/geist/collapse` | 触发器是带 `aria-expanded`／`aria-controls` 的 `<button>`；内容关闭时仍在 DOM；文档明写「Animate the open/close transition; jump-cuts make the page feel like it teleported」 | — |

结论：整套 Geist 里只有 Toggle 开态用蓝，Tabs／Switch／Checkbox／主按钮的选中与强调全是墨色
反相或抬一档的灰面。蓝色的另外两个去处是焦点环与链接。

## Button 全变体与状态（2026-09-03 复测，深色主题）

- URL：<https://vercel.com/geist/button>
- 取证方式：`getComputedStyle` 遍历页面全部 `<button>` 去重；hover 与 disabled 的规则原文
  直接 `fetch` 站点样式表 `0p9r363b8n-x2.css` 后按 `}`…`}` 切片取出——手工写
  `data-hover` 属性并不触发 Geist 的悬停样式，只能读源规则。

| 尺寸 | 高 | 内边距 | 圆角 | 字号 |
| --- | --- | --- | --- | --- |
| small | 32px | `0 6px` | 6px | 14px |
| medium | 36px | `0 10px` | 6px | 14px |
| large | 40px | `0 14px` | 8px | 16px |
| rounded（胶囊） | 同上 | `0 12px` | 999px | 同上 |

变体（32px 档实测）：primary 底 `rgb(237,237,237)` 字 `rgb(10,10,10)`；secondary 底
`rgb(10,10,10)` 字 `rgb(237,237,237)`；tertiary／ghost 透明底 `rgb(237,237,237)` 字；
error 底 `rgb(217,48,54)` 字 `#fff`；warning 底 `rgb(255,153,10)` 字 `rgb(10,10,10)`。
全部 500 字重、`border-width:0`——边框是 `box-shadow:0 0 0 1px rgb(46,46,46)` 画的，
只有 secondary 有，tertiary 的 `box-shadow` 是 `none`。过渡统一
`.15s cubic-bezier(.4,0,.2,1)`。

三条关键状态规则：

- **悬停只动填充，边框不动**。源规则里没有任何按钮 hover 改 border/ring：
  primary 走 `--themed-hover-bg` 默认 `#ccc`（`hsl(0,0%,80%)`，即 #EDEDED 掉一档），
  secondary 走 `--ds-gray-200`（深色下 `#1F1F1F`，即 #0A0A0A 抬一档），
  ghost 走 `--ds-gray-alpha-200`。
- **禁用是实底灰，不是半透明**：底 `rgb(26,26,26)`、字 `rgb(143,143,143)`、
  ring `rgb(46,46,46)`、`opacity:1`、`cursor:not-allowed`。
- **按下没有缩放**：页面上全部按钮的 `transform` 都是 `none`，Geist 不做 `scale`。

### 选中态与它的悬停：源类名取证（2026-09-03）

`getComputedStyle` 读不到悬停态（要么得手工触发，要么被冻住的 transition 骗），但 Geist
是 Tailwind 写的，`class` 属性里的 `hover:`／`aria-selected:`／`peer-checked:` 前缀就是
规则原文，比计算值更硬。三个组件的类名清单一致：

| 组件 | 未选中悬停 | 选中 |
| --- | --- | --- |
| Switch 分段项 `/geist/switch` | `hover:text-[var(--ds-gray-1000)]` | `peer-checked:text-[--ds-gray-1000]` + `peer-checked:bg-[--switch-checked-color]` + `peer-checked:rounded-[4px]` |
| Tabs primary `/geist/tabs` | `not-disabled:hover:text-gray-1000` | `aria-selected:text-gray-1000` + `primary:aria-selected:border-gray-1000`（底边 2px） |
| Tabs secondary `/geist/tabs` | `not-disabled:hover:text-gray-1000` | `aria-selected:text-gray-1000` + `aria-selected:bg-gray-200`，`rounded-md`、`h-8`、`px-3` |

三条共同点，都与我们之前的写法相反：

- **悬停只改文字色，不上填充**。填充是「选中」的专属信号，所以一行选项里鼠标划过邻居时，
  哪个是当前项始终看得见。这与 Button 的「悬停只抬填充」不冲突：Button 没有选中态，
  没有需要让位的信号。
- **选中不加边、不加内嵌一圈线**。Tabs primary 的底边 2px 是这个组件自己的形状语言
  （未选中也占着 `border-b-2 border-transparent`），不是通用的选中环。
- **字重两态相同**。Switch 分段项 `font-medium` 常驻，Tabs 全程不改字重。

唯一的例外是主题选择器（system／light／dark 三个圆形按钮）：选中项是
`bg-[--ds-background-100]` + `shadow-[0_0_0_1px_var(--ds-gray-400)]`。它加环是因为选中项
的底色和容器同色，填充本身分不出来；不是通用做法。

### 主题选择器：整件几何（2026-09-04 实测，深色主题）

- URL：<https://vercel.com/geist/switch>，1440×900 视口（它挂在 `hidden xl:block` 的
  文档栏里，窄视口下三档的盒子都是 0，必须先把视口撑到 xl 再读）。
- 取证方式：按 `aria-label` 取 `system`／`light`／`dark` 三个 `type=radio`，读它们的
  `label` 与外层 `fieldset` 的 `getBoundingClientRect` 和 `getComputedStyle`；悬停与
  选中的规则原文取自 Tailwind 类名（`peer-checked:`／`hover:` 前缀就是规则本身）。

| 部位 | 值 |
| --- | --- |
| 外框 `fieldset` | 96×32，`rounded-full`，无填充，`--ds-shadow-border`（`0 0 0 1px`）一圈环，`p-0`、`border-0` |
| 每一档 `label` | 32×32 正圆（`size-8 rounded-full`），图标 16×16 |
| 未选中 | 透明底，图标 `--ds-gray-700`（`rgb(143,143,143)`）；`hover:text-[var(--ds-gray-1000)]`，不上填充 |
| 选中 | 底 `--ds-background-100`（`rgb(10,10,10)`，与页面同色），`shadow-[0_0_0_1px_var(--ds-gray-400),0_1px_2px_0_var(--ds-gray-alpha-100)]`，图标 `--ds-gray-1000` |
| 语义 | `fieldset` + `legend.sr-only`「Select a display theme:」+ 三个共享 name 的 radio，每档一个 `span.sr-only` 写档位名 |
| 焦点 | `peer-focus-visible:shadow-[var(--ds-focus-ring)]`，环由 radio 的焦点态传给同级 label |

三枚图标都是 16×16 的 Geist 私有 SVG：system 是一台显示器，light 是太阳，dark 是月亮带星。

2026-09-04 在登录态的 <https://vercel.com/sandun-bingshi> 复读同一个控件（账户菜单里的
「Theme」行），类名与上表逐条一致，只多一个把 32 收成 24 的 `data-small` 档。这套几何在
文档站与后台是同一份。

**Peach 采用与差异**：形状、尺寸、语义和「选中项加环」照抄，颜色换成本仓库 token
（外框环 `--border-15`，选中底 `--ground`、环 `--line` 加 `0 1px 2px var(--overlay-5)`，
未选中 `--muted`、悬停提到 `--ink`）。跟随系统那一档同样用显示器字形（Lucide `monitor`）：
这一档说的是「照这台设备的设定走」，讲的是设备而不是明暗。详情页的画面尺寸因此换成
`ratio`——那里量的是画幅本身，不是放画幅的机器。一处有意不同：`≤760px` 时三档各撑到
44×44，这是本仓库的手机命中区要求，Geist 没有这一档。

### 下拉：后台没有一个原生控件（2026-09-04 实测，登录态）

- URL：<https://vercel.com/sandun-bingshi>（Projects 后台首页）。
- 取证方式：先枚举页面里的 `select` 与 `[role="combobox"]`——两者都是 0 个；触发器一律是
  `button[aria-haspopup="true"]`（Filter and Sort Projects、Add New、Preview Menu…）。
  然后点开 Filter and Sort，读面板与行的 `getComputedStyle`，规则原文取自类名与 CSSOM。

| 部位 | 值 |
| --- | --- |
| 触发器 | 36×36，6px 圆角，`0 0 0 1px rgb(46,46,46)` 一圈环，无实心底 |
| 面板 | `ul[role="menu"]`，宽 250px（inline style），`--ds-background-100` 底（**浅色下是纯白**），12px 圆角，`padding:6px` |
| 面板阴影 | `--ds-shadow-menu`：`0 0 0 1px 边框色, 0 1px 1px #00000005, 0 4px 8px -4px #0000000a, 0 16px 24px -8px #0000000f` |
| 行 | `--ds-popover-row-height:36px`、`--ds-popover-row-radius:6px`、`--ds-popover-row-padding:0 8px`，14px/400 |
| 行的悬停与选中 | 同一个 token：`data-highlighted:bg-gray-alpha-100`、`data-selected:bg-gray-alpha-100`（浅色 `#0000000d`，深色 `#ffffff0f`），`transition:background .1s` |
| 行内次要文字 | `--ds-gray-900` |

### 浅色一档的中性刻度（2026-09-04 实测）

在同一页把 `<html>` 的 `dark-theme` 换成 `light-theme` 读 `:root` 的计算值（只改 DOM，不动
账号设置），拿到两档并排的刻度：

| token | 浅色 | 深色 |
| --- | --- | --- |
| `--ds-background-100` / `-200` | `#FFFFFF` / `#FAFAFA` | `hsl(0 0% 4%)` / `#000` |
| `--ds-gray-100` / `-200` / `-300` / `-400` | `#F2F2F2` / `#EBEBEB` / `#E5E5E5` / `#EBEBEB` | 10% / 12% / 16% / 18% |
| `--ds-gray-700` / `-900` / `-1000` | `#8F8F8F` / `#4D4D4D` / `#171717` | 56% / 63% / 93% |
| `--ds-gray-alpha-100` / `-200` / `-300` | `#0000000d` / `#00000014` / `#0000001a` | `#ffffff0f` / `#ffffff17` / `#ffffff21` |

要点：浅色这一档**没有色相**，从纸白到近黑全部落在 `hsl(0,0%,·)` 上；中间态（悬停、选中）
是纯黑透明度而不是调过色的实色灰，所以它永远比所在面板重一点点，不会自己变成一块底。

**Peach 采用与差异**：浅色色板照这张表改成无色相，`--hover` 从实色 `#EEF1F5` 换成
`rgba(0,0,0,.05)`；`--surface`/`--sunk`/`--line`/`--line-soft` 取 `#FAFAFA`/`#F2F2F2`/
`#E5E5E5`/`#EBEBEB`，`--ink`/`--ink-2` 取 `#171717`/`#4D4D4D`。两处不同：`--muted` 用
`#666666` 而不是 Geist 的 `#8F8F8F`（本仓库这一档要承担 12–13px 的说明文字，56% 灰在白底
上读不动），`--border-10`/`--border-15` 保留本仓库自己的 10%／15% 两档而不是收到 Geist 的
8%。下拉面板与菜单行按上表：`.popmenu` 底色改成 `--ground`、阴影换成那四层薄影，行高走
本仓库既有的 `--control-h`（38px）而不是 36px，悬停与选中共用 `--hover`。

### 侧栏导航是例外：分工反过来（2026-09-04 实测，深色主题）

上一节那张表只取了横排选项组（Switch、Tabs），当时把结论推广到了侧栏，这是错的。
2026-09-04 在 `https://vercel.com/geist/tabs` 用 1600×900 视口读左栏文档导航
（`aside` 里那 82 条链接，值取自每条链接内层的 `span`，悬停用真实鼠标移入后再读计算值）：

| 状态 | 背景 | 文字 | 类名 |
| --- | --- | --- | --- |
| 未选中 | `rgba(0,0,0,0)` | `rgb(161,161,161)` | `text-gray-900 hover:bg-gray-100` |
| 未选中 + 悬停 | `rgb(26,26,26)` | `rgb(161,161,161)`（不动） | 同上 |
| 当前项 | `rgba(255,255,255,.06)` | `rgb(237,237,237)` | `text-gray-1000 bg-gray-alpha-100`，**不带任何 hover 类** |

几何两态相同：`h-10`、`rounded-md`（6px）、`px-3 py-1.5`、`text-copy-14`，字重全程 400，无边框无环。

要点：

- **侧栏的悬停是抬填充的**，而且填充强度和当前项几乎一样：`--ds-gray-100` 是 hsl 10% 的实底，
  `--ds-gray-alpha-100`（`#ffffff0f`）压在 hsl 4% 的页底上合成约 hsl 9.4%。Geist 显然不打算
  用填充的强弱区分「鼠标在这儿」和「你在这儿」。
- **区分二者的是文字色**：63% 灰对 93% 白。悬停不碰文字色，当前项不带 hover 类。
  所以横排选项组那条「填充专属选中、悬停只提文字色」在这里是反的。
- 当前项确实不加边、不加环、不加字重——上一节这部分结论仍然成立。

Peach 的两列侧栏导航按上表对齐：`.edge button` 与 `.dnav button` 的未选中字色都是
`--muted`（63% 灰），当前项是 `--hover` 底 `--ink` 字。抽屉里的图标不再自己声明颜色，
跟着按钮的 `currentColor` 走，所以图标与标签始终同色、同时变。

### 纠正记录

`1367a9a`（2026-09-03）把 Switch／Tabs 的取证结论推广成了全站规则，顺手删掉了
`.edge button:hover` 与 `.dnav button:hover` 的填充。2026-09-04 发现窄栏悬停没反馈，
对照 Vercel 后台左栏的截图（Projects 与 Deployments 两行同时带填充）。上表是照此复测的结果：
证据只覆盖横排选项组，推广到侧栏没有依据。已按上表把两处填充改回，并加正向断言
`test_sidebar_nav_keeps_the_hover_fill_and_leaves_state_to_the_color` 锁住。

### Peach 对应（2026-09-03 收敛，2026-09-04 修正侧栏一条）

- 按下／选中（`aria-pressed="true"`、`aria-current`、`.selected`、`.current`、`.picked`、`:checked`）
  只有填充：一律 `--hover` 底 `--ink` 字，不加边、不加内嵌一圈线、不加字重。这与本文件早前记录
  冲突，以本条实测为准——早前两版先是「统一 `--ink-2` 底 `--ground` 字」（Geist 的 Switch 选中项只是 `#0A0A0A`
  面上抬到 `#1A1A1A`，没有一处是反相白块），再是「无边框补内嵌一圈线、带边框提到墨色 28%、
  还要更强就加字重」（那一整套是自造的强调阶梯，上面三个组件的类名里一条都没有）。
- 因此**同一排横向互斥选项的未选中项，悬停只提文字色到 `--ink`，不上填充**。这些组的
  未选中基态一律 `--muted`（对应 Geist 横排选项实测的 `rgb(161,161,161)`）：停在 `--ink-2`
  时悬停只剩一档可提，肉眼读不出鼠标停在哪一枚。适用于 `.pill`、`.chip`、
  `.reviewtabs button`、`.sorts button`、`.junkfilters a`、`.tagmodes button`、
  `.managebar button`、`.mediaviewbutton`、`.javbar button`。没有选中态的按钮
  （`.fbtn`、`.fchip`）和没有并排邻居的孤立开关（`.ib`、`.brandpill`、`.playerstatsbtn`、
  `.fb .like`）悬停照旧抬填充。**侧栏导航 `.edge button` 与 `.dnav button` 不在这条里**，
  按上一节的实测走「悬停抬填充、当前项握着颜色」。彩色标签类控件（`.tagfilters`、`.index-tags .tg`、`.alphatag`、
  `.sec.cat-* .chip`）的悬停本来就是标签色 tint，与白色选中底不同色，不受这条影响。
- 主动作 `--ink` 底 `--ground` 字，悬停 `color-mix(in srgb,var(--ink) 88%,var(--ground))`
  （对应 Geist 的 #EDEDED→#CCC，同为掉一档而非换色）；每屏最多一个。
- 按钮（无选中态）悬停只抬填充到 `--hover`，边框与文字色不动——同样推翻此前的
  「悬停边提亮到墨色 28%」，那一档在 Peach 是全站最亮的边，实际效果比 Vercel 重得多。
  墨色 28% 的边现在只剩输入框 `.fpicksearch:hover` 一处。
- 禁用统一 `--sunk` 底、`--line-soft` 边、`--muted` 字，不用 `opacity`；
  按下不加 `scale`。Peach 保留 `cursor:default` 而不是 Geist 的 `not-allowed`，
  与本仓库其余禁用态写法一致。
- 计数徽章（如作者别名「3 组」）走 gray badge：`--overlay-5` 底、`--border-15` 细边、`--muted` 字、`--control-radius`。
- 保留蓝：`:focus*`、真正的链接（`.entitylink`／`.flink`／`.fsourcelink`／`.fcred a`／`.tokauthor>a`）、
  进度与数据（progress／watchprogress／range／slider／tokbar／trace）、`#censorSetting:checked`（Toggle）。
- `--tungsten-soft` 退役。允许清单由 `tests/test_web_ui.py` 的
  `test_tungsten_is_reserved_for_focus_links_progress_and_toggle` 强制。

## Peach 对应

- 语义按钮：`.fbtn.danger`（清空回收站等销毁类）、`.fbtn.primary` 保持主推进、
  批量已看/忽略保持 secondary；回收站清空区用红发丝边框卡。
- 行内状态：`ok`/`error` 用低饱和 tint 徽章（`.sbadge`），不再是裸文字。
- 检查失败报告条：对齐 danger 卡语义——红发丝边 + 微红底，去掉左侧粗条。
- 低密度管理页（统计/口味/复核等）内容列限宽约 1000px 居中；浏览页（首页/
  关注）保持全宽。
- 正文字体栈补 CJK sans：`"Microsoft YaHei UI","Microsoft YaHei","PingFang SC",
  "Hiragino Sans GB","Noto Sans CJK SC"` 置于 sans-serif 前。

## 2026-09-05 实测 vercel.com 后台（浅色档，另加暗色 token 对照）

- 取证方式：内置浏览器登录态下打开 `vercel.com/<team>/~/settings` 与 `vercel.com/<team>`，
  读 `getComputedStyle`；暗色一档把 `<html>` 的 `light-theme` 换成 `dark-theme` 后重读 token。
- 本节测四项：输入框聚焦是什么色、fieldset 两块面各是什么色、
  按钮是不是「无脑全黑」、滚动条有没有被改写。

### 中性刻度（同一组 token 的明暗两档）

| token | 浅色 | 暗色 |
| --- | --- | --- |
| `--ds-background-100`（正文面） | `#FFFFFF` | `hsl(0,0%,4%)` |
| `--ds-background-200`（操作条） | `#FAFAFA` | `#000000` |
| `--ds-gray-200`（分隔线） | `#EBEBEB` | `hsl(0,0%,12%)` |
| `--ds-gray-alpha-400`（静止边） | `#00000014` | `#ffffff24` |
| `--ds-gray-alpha-500`（悬停边） | `#00000036` | `#ffffff3d` |
| `--ds-gray-alpha-600`（聚焦边） | `#00000057` | `#ffffff82` |
| `--geist-radius` | 6px | 6px |

两档的方向是一致的：操作条永远比正文面深一档，边永远是当前主题的中性透明色，
没有一处焦点或选中用到蓝。

### Fieldset

- 外框：`background:#FFFFFF`、`border-radius:6px`、`overflow:hidden`，边不是 border 而是
  `box-shadow:rgba(0,0,0,.08) 0 0 0 1px, #FAFAFA 0 0 0 1px`。
- 正文区：`#FFFFFF`、`padding:20px`；标题 20px/600，说明 14px、`padding:8px 0 20px`。
- 操作条：`#FAFAFA`、`border-top:1px #EBEBEB`、`min-height:56px`、
  `padding:12px 12px 12px 20px`，说明推到最左、按钮靠右。

### Button（同一屏上同时出现的两档）

| 档 | 背景 | 文字 | 边 | 几何 |
| --- | --- | --- | --- | --- |
| primary（Save／Add New／Import） | `#171717` | `#FFFFFF` | 无 | 32–36px 高、6px 圆角、14px/500 |
| secondary（Filter and Sort Projects） | `#FFFFFF` | `#171717` | `0 0 0 1px #EBEBEB` | 同上 |
| disabled（Delete Team／Add more） | `#F2F2F2` | `#8F8F8F` | `0 0 0 1px #EBEBEB` | 同上 |

次级档不是透明的：它自己是一块比所在容器亮的面，所以压在 `#FAFAFA` 的操作条上仍读得出边界。
仪表盘工具行里两档并排出现，「主动作实底、其余白面」就是这套的全部规则。

### 滚动条

整站 `scrollbar-width` 与 `scrollbar-color` 都是 `auto`，没有一条 `::-webkit-scrollbar` 规则；
只在个别内部滚动区把滚动条整个藏掉。明暗两档的滚动条外观由 `color-scheme` 交给浏览器。

## Peach 对应（2026-09-05）

- 输入框一组边与环收进 `--field-ring`／`--field-ring-hover`／`--field-ring-focus`／`--field-glow`，
  聚焦是 1px 中性边加 4px 辉光，不再是蓝。顶部搜索、`.geist-search`、`.geist-input`、
  `.gselectfield`、`.sidebaraddfield`、偏好文本域共用这一份。
- 全站收成两张面。`--page` 是退到后面那张：`body`、框体里的操作条。`--ground` 是浮在它
  上面那张：顶栏、窄栏、卡片、面板、按钮和输入框，外框 1px `--field-ring`、内部分隔线
  `--line-soft`，不靠投影。暗色一档 Peach 的底带蓝，所以 `--page` 取 `#04060A` 而不是纯黑，
  方向与 Vercel 一致（面比底亮）。`--surface` 不再当面用，只剩内嵌与控件禁用那一档。
- 顶栏与窄栏是有意与 Vercel 分开的一处：Vercel 的页头和页面底同色、不浮起，Peach 有一条
  从上贯到底的窄栏，两条一起留在 `--ground` 才不会在左上角撞出两种白。
- 按钮次级档填 `--ground` 而不是透明，悬停抬到 Geist 的 gray-200 那一档，写作
  `color-mix(in srgb,var(--ink) 8%,var(--ground))`：浅色落在 `#EBEBEB`、深色往亮处抬，
  两个主题共用一条规则；禁用落到 `--sunk`。
- 动作按钮一律 `border:0`，两档只靠填充分：主动作 `--ink` 实底、次级 `--ground` 一块亮面。
  描边留给输入类控件和「点开会出一张面板」的触发器：`.gselectfield`、来源筛选的
  `[data-srcfilter-toggle]`、排序方向的 `.fmanagedir` 保留 1px `--field-ring`，
  悬停走 `--field-ring-hover`、展开走 `--field-ring-focus` 加 4px 辉光。
- 危险档静止态就是实底红 `--drop` 加白字，悬停把同一块红压深 18%：与 Geist 的 error
  变体（`rgb(217,48,54)` 实底白字）同构，站内只有 01-base 那一份定义。
- 选中面 `--picked` 取 `--inset`：浅色 `#EBEBEB`（Geist 的 gray-200）、深色 9% 白。
  判据是「比脚下那张面往下压一档」，与实测 Tabs 的 `bg-gray-200` 一致；站在 `--ground`
  上的那一类控件（顶栏、窄栏、抽屉、浮层菜单）例外，选中走 `--hover` 加 `--ink` 字色。
- 滚动条不写任何自定义样式，交给 `html` 上的 `color-scheme`。

## 2026-09-06 实测 vercel.com/<team> 的图标用法

取证方式：内置浏览器登录态下打开团队 dashboard，遍历所有可见 `button` 与 `a`，
统计每个控件里的 `svg` 数量、图标落在文字的哪一侧。

### 按钮

| 控件 | 文字 | 图标 | 位置 |
| --- | --- | --- | --- |
| All Projects | 有 | 1 | 后置 chevron（点开是菜单） |
| Add New | 有 | 1 | 后置 chevron（点开是菜单） |
| Upgrade | 有 | 0 | 纯文字 |
| Upgrade to Pro | 有 | 0 | 纯文字 |
| 其余 12 枚 | 无 | 1 | icon-only，24／32／36px 见方 |

一屏 16 枚可见按钮里，**带文字的没有一枚挂前置图标**。

### 链接

站内链接（PR `#7`、部署 ID、项目名、用量项）不带图标；离站的部署域名
`lmd-gg.vercel.app` 带一枚 14×14 的 Geist external-link（`viewBox="0 0 16 16"`、
`fill:currentColor`）。列表行左端出现的图标是身份标记（用量项的 provider 标、
提交行的 git 标），不是动作图标。

### 判据

图标只出现在三个位置：纯图标控件、菜单触发器的后置 chevron、以及「这一跳会离开当前
应用」的外链标。带文字的动作按钮不加前置图标——文字已经说清动作，再加一个图标是把
同一件事说两遍，一排按钮的左边缘还会被大小不一的图形推得参差。

### Peach 对应

- 保留：`chevron-down` 这类触发器标、`check` 这类选中标、`external-link`、侧栏与抽屉的
  导航标（那一层是 icon-only 加标签的列表，对应 Vercel 的左栏）、状态语义色块。
- 去掉：带文字的动作按钮上的前置图标（`refresh-cw` + 检查死链／扫描差异／重试同步、
  `upload` + 导入历史、`trash` + 删除空文件夹／移入回收站、`plus` + 新建播放列表、
  `play` + 进入沉浸模式）。
- 补上：任何跳出 Peach 的链接都要 external-link 标，站标（favicon）只在来源行这种
  「哪个站」本身是信息的位置用。

## 2026-09-05 实测 vercel.com/<team>/~/deployments 的筛选行

取证方式与上一节相同：内置浏览器登录态下打开该页，用 `getComputedStyle` 读实际值。

### 结构

`Add Filter | Author lmd | Environment Production | Status Error`，右侧另有
`Status 6/7`、`Select Date Range`、`All Environments`。不是一排互斥药丸，是可叠加的维度令牌：
每个令牌自带下拉，可单独摘掉，`Add Filter` 负责再加一个维度。

### 三态

| 态 | 背景 | 描边 |
| --- | --- | --- |
| 未生效（建议中的维度） | `rgba(0,0,0,0)` | `1px dashed rgba(0,0,0,.21)` |
| 已生效（`Add Filter`、`Author lmd`） | `#FFFFFF` | `1px solid rgba(0,0,0,.08)` |
| 悬停／键盘聚焦 | `#FFFFFF` | `1px solid rgba(0,0,0,.08)` |
| 下拉展开 | `gray-200` | 实线 `gray-500` |

四颗令牌的文字全是 `#171717` 14px/400，深浅只差在内部 label 那个 span；
也就是生不生效**只由边的虚实和填不填色**说，不靠文字色。

高 32px、圆角 `10ex`（视觉上等于全圆）。填色画在一个绝对定位的
`div[data-pill-background]` 上，按钮本身不带背景，所以描边虚实可以和填色各自过渡。

### 这套配色成立的前提

该页页面底色是 `#FAFAFA`（`bg-background-200`），`--ds-background-100` 才是纯白。
所以「填成 `#FFFFFF` = 更亮 = 生效」是相对页面底色抬了一档。Peach 首页的 `--ground` 已经是纯白，
同一套写法没有可填的更亮档；要照搬得先把页面底色降到 `--surface` 这一层。

### Peach 对应（2026-09-05）

虚线只借给 `#tagbar` 那一排，即首页顶上的筛选条：未生效 `background:transparent` +
`1px dashed var(--field-ring-hover)`，已生效 `background:var(--ground)` +
`1px solid var(--field-ring)`；浅色一档实测就是 `rgba(0,0,0,.21)` 与 `rgba(0,0,0,.08)`。
`.brandpill`、关注页和实体页的 `.pill` 一律实线，只保留「选中填 `--ground`」这一半。
虚线在 Vercel 上说的是「这条筛选还没加上去」，配得上它的只有恒常在场、可开可关的那一排；
随内容来去的药丸也画成虚线，整页就是一片没生效的框，虚线不再指向任何东西。
中间加了一档 Vercel 没有的悬停：只把虚线拉成实线，不填色。原因是语义不同——
Vercel 的令牌可叠加，悬停预演成生效态没有代价；Peach 首页那一排是单选、恒有一颗生效，
悬停也填就会同屏出现两颗选中。文字色也没照搬：Vercel 一行只有四颗，Peach 那一排有二十来颗，
全部提到 `--ink` 会让整条筛选比页面其它部分都重，所以未生效仍停在 `--muted`。

## 覆盖式滚动条（2026-09-05 实测）

- URL：<https://vercel.com/dashboard>（登录后的团队概览，左侧栏）
- 取证方式：Chrome 148 读 `getComputedStyle` 与 `getBoundingClientRect`；同一页在内置
  浏览器面板里量到的是 `display:none`，因为那个面板报的是粗指针，轨道的 `pointer-fine:block`
  不成立。结论以真实 Chrome 为准。

侧栏容器 `nav.overflow-x-clip.overflow-y-auto`：`scrollbar-width:none`、`padding-right:0px`、
`clientWidth === offsetWidth`（255），即原生滚动条整个关掉，也不给它留槽。

轨道是容器的兄弟，同在一个 `position:relative` 的父级（`div.md:flex-1.relative.md:min-h-0`）里：

| 元素 | 类 | 实测 |
| --- | --- | --- |
| 轨道 | `group/track absolute right-0 top-2 bottom-2 w-3 hidden pointer-fine:block` | `position:absolute`、12px 宽、`background:rgba(0,0,0,0)`、`border-radius:0` |
| 滑块 | `absolute right-0 w-[3px] group-hover/track:w-1.5 rounded-full bg-gray-500 cursor-default transition-[width] duration-150` | 静息 3px、轨道悬停 6px、`border-radius` 9999px、`background:rgb(69,69,69)`（暗色 `--ds-gray-500`；浅色一档是 `rgb(201,201,201)`）、`transition:width .15s cubic-bezier(.4,0,.2,1)` |

几何写在滑块的行内样式上，不是 CSS 规则：`height` = 可视高 / 内容高 × 轨道高
（实测 805 / 851 × 789 = 746.35），`transform:translateY()` = 滚动进度 × (轨道高 − 滑块高)。
根滚动条**没有**被接管：`html` 上 `scrollbar-width:auto`、`scrollbar-color:auto`、
`scrollbar-gutter:auto`，量到 15px 实宽。站内其余滚动区多数只写
`scrollbar-width:thin; scrollbar-color:var(--ds-gray-500) transparent`。

### Peach 对应（2026-09-05）

`.ovtrack` / `.ovthumb` 与 `attachOverlayScrollbar()` 照抄上面的几何与两档宽度，颜色换成
`--field-ring-hover`：它在浅色一档是 `rgba(0,0,0,.21)`，压在白面上正好是 `rgb(201,201,201)`，
和实测值同一个数，还自带暗色一档。有意差异三条：

1. **粗指针仍然画滑块。** Vercel 的轨道 `hidden pointer-fine:block`，触屏上整条不画。Peach 的
   原生滚动条同样已经关掉，跟着收掉就等于触屏上既没有滑块也没有系统指示器，读不出这一列
   有多长、自己停在哪儿。滑块不占宽度，留着不挡任何东西；「能拖」才收给细指针，靠
   `pointer-events` 分档，拖不动的东西不该吃走触摸事件。
2. **整页也换掉。** Vercel 只接管内部滚动区，根滚动条留给浏览器。Peach 的根滚动条本来要靠
   `scrollbar-gutter:stable` 永久扣下 15px 才不跳版，等于为一条装饰付一整列；换成覆盖式之后
   宽度归零，跳版的前提也一起消失。
3. **整页那条的命中区收到滑块本身。** 侧栏轨道只覆盖侧栏，吃掉的点击本来就是它自己的；整页
   那条从窗口顶贯到底，12px 的透明条会把右边缘所有点击一并吃掉，而设置面板正贴在那儿。

站内其余滚动容器（设置面板、详情侧栏、标签选择、混播队列、统计页的指标带与宽表、关注管理的
来源列表与分页条）走同一个 `attachOverlayScrollbar()`，容器名单写在 `web/js/ui-components.js`
的 `OVERLAY_SCROLLERS` 一处。横轴同样自绘：轨道贴底、滑块 3px→6px，几何换成
可视宽/内容宽。原生那条由 `[data-overlay-scrollbar]` 关掉——属性是脚本挂上去的，
所以脚本没跑到的容器留着系统滚动条兜底，不会出现既没滑块又拖不动的死框。

**例外**：`.popmenu` 那类进顶层（`popover`）的菜单仍用原生滚动条。轨道必须是滚动容器的
兄弟，而顶层元素的兄弟画在原来的层里，位置和裁剪都对不上。
