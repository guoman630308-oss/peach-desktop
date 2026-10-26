# BoardUI 实测：Pro access 卡片与 Button 组件

前半是换头像弹层照着看的那张卡片，后半是全站按钮悬停照着改的那份组件源码。两段同一天取，
取法不同——卡片是付费墙、只量得到运行时样式，按钮有可取的 `/r/button.json`。

- 取证日期：2026-09-12
- 来源：<https://www.boardui.com/components/composer> 页面里的 Pro access 卡片，读运行时 DOM 与
  `getComputedStyle`；两档主题由 `html.dark` 这个 class 切，不跟随 `prefers-color-scheme`，
  所以浏览器的配色模拟对它无效，要手动加减这个 class 才取得到暗色一档。
- **不登记进 `docs/reference-sources.json`**：那张表要求每个来源有可重新抓取、可哈希的上游
  快照。BoardUI 的组件源码走 `https://www.boardui.com/r/<name>.json`，而这张卡片是站点自己的
  付费墙，实测 `pro-access`、`pro-access-card`、`upgrade-card`、`composer` 四个名字全部 404，
  给不出可哈希的字节。要复核只能照本文的取法重新量一次运行时样式。

## 可复用证据

结构：`卡片(rounded-[28px]，1px 边)` > [`头部(p-5 sm:p-6，自己不画底)`,
`中间容器(border-t + p-1，自己画底)`]；中间容器里是 `步骤卡(rounded-3xl，p-4 sm:p-5)` ×2
加一条 `底部条(px-5 py-3，自己不画底)`。

三层面色，两档主题实测：

| 位置 | 浅色 | 暗色 | 类名 |
| --- | --- | --- | --- |
| 卡片 / 头部 / 步骤卡 | `rgb(255,255,255)` | `rgb(23,23,23)` | `bg-background-primary-default dark:bg-background-secondary-default` |
| 中间容器 | `rgb(247,247,247)` | `rgb(38,38,38)` | `bg-background-secondary-default dark:bg-background-primary-default` |
| 分隔线与卡片边 | `rgb(235,235,235)` | `rgb(38,38,38)` | `border-separator-border` / `border-border-button-default` |

两档里 primary 与 secondary 这两个 token 的角色是**对调**的，类名自己写着 `dark:` 那一半。
读成「primary 永远是卡片面」会在暗色里把卡片画得比中间块还亮一档。

头部：左侧图标槽 56×56、圆角 `12.432px`；文本块 `min-w-0 space-y-2 pt-7 sm:pr-24 sm:pt-0`；
标题 18/26/500，说明 14/20/400；徽章绝对定位在卡片右上角 `top-5 right-5`，整圆、
`px-2 py-0.5 text-[10px]`，实测 84×22。步骤序号槽 32×32、圆角 12px。

`Get Pro` 按钮：高 36px、圆角 10px、`shadow-xs`（`0 1px 2px rgba(0,0,0,.05)`）、
1px 边。静止 `bg #fff` + `border #ebebeb`，悬停 `bg #f7f7f7` + `border lab(84.92%)`——
**填充和描边一起变**。过渡 `background-color/border-color/box-shadow/color .15s` 加
`transform .42s cubic-bezier(.4,0,.2,1)`。

BoardUI 的 76 个组件里没有图片网格或 gallery 控件，只有 carousel、file-upload 和 avatar：
竖图候选网格没有可照抄的上游件，按 Peach 自己的规范自查。

## Peach 主动保留的差异

- **换头像弹层不做中间那层沉下去的容器**。BoardUI 那块里放的是两行文字步骤，靠沉下去的底
  才分得出段；换头像这一屏中间放的是一格一格的候选图，图本身就是边界，再沉一层加一圈描边
  等于给同一件事画两层框，还把格子挤窄。弹层因此一张面到底，头部与网格之间只留一条
  `--line-soft` 发丝线。
- **面色用 `--page` / `--ground`，不用 `--surface`**。board.css 把 BoardUI 的 token 映射过来时，
  `--surface` 接的是 `background-primary-default`，于是它在浅色是 `#fff`、暗色是 `#262626`，
  跟着上游一起对调了角色。`--page` 在两档里都是「最靠那一端的那个面」，设置弹层的卡片用的
  也是它。
- **圆角走 Peach 的 token**：卡片 `--floating-radius`（board.css 一档是 20px），不取 28px；
  候选格 `--surface-radius`。28px、24px 不在 Peach 的圆角清单里。
- **徽章不占右上角**：那儿归关闭键——弹层跟页面里的一张卡片差着一件事，它要能关掉，而右上角
  是全站弹层放关闭键的位置。徽章跟到标题后面，用 `.geist-badge`（圆角 `--badge-radius`，
  不是整圆）。
- **按钮悬停只换填充，不动那圈线**。判据取的是 BoardUI 的强调档而不是 secondary：用户要
  「不应该有线，应该有高亮」，而 primary 与 ghost 两档正是这么做的（见下节）。Peach 的
  次级按钮因此在 `web/board.css` 的 Board secondary 那一条上走
  `background:var(--control-hover)`——浅色 #ebebeb、暗色 #404040，都是同一套面色里的下一档
  实色。不照抄 BoardUI secondary 的 `hover:border-border-button-hover`：那一档要同时点亮边，
  是因为它的悬停填充在暗色里只是 60% 的 #404040 压在 #262626 上，差几个色阶不够看。
  `web/css/01-base.css` 里的 Geist 静止态不动：那一层记的是 Geist 的实测值，BoardUI 带来的
  偏差落在 board.css 这一层。

## Button 组件实测（2026-09-12）

来源：<https://www.boardui.com/components/button>，组件源码走 `https://www.boardui.com/r/button.json`
（HTTP 200，可取）。Figma 源在组件页上写着 Board UI → Buttons，node `3656:13819`。

| 档 | 静止 | 悬停 | 类名要点 |
| --- | --- | --- | --- |
| primary | 渐变 `rgb(43,127,255)`→`rgb(21,93,252)`，白字，`shadow-xs`，无边 | 渐变提一档到 `rgb(51,146,255)`→`rgb(43,127,255)`，铺在 `::before` 上淡入 | `bg-button-primary text-text-white shadow-xs`，三档全长在 `.bg-button-primary` 这个类里 |
| ghost | 浅色底 `rgb(219,234,254)` 字 `rgb(20,71,230)`；暗色底 `rgb(28,57,142)` | 浅色 `rgb(190,219,255)`、暗色 `rgb(25,60,184)`，**全程没有边** | `hover:bg-button-ghost-hover active:bg-button-ghost-active` |
| secondary | 白底 + `#ebebeb` 边 + `shadow-xs` | 底 `#f7f7f7`、边 `#d4d4d4` | `hover:bg-background-primary-hover hover:border-border-button-hover` |

尺寸：medium `h-9 rounded-2lg p-2 text-body-medium`（实测 36px / 10px 圆角 / 8px 内边距 /
14px·20px·500）、small `h-8 rounded-lg px-2 py-1.5`、xs `h-6 rounded-sm px-2`；图标 20/18/14。
基础类含 `button-press-motion`（按下时的形变，`transform .42s cubic-bezier(.4,0,.2,1)`）与
`focus-visible:ring-2 ring-offset-2 ring-border-focus-ring`。

暗色档的 `--color-background-primary-hover` 上游写的是
`color-mix(in srgb, lab(27.036%) 60%, transparent)`——即 60% 的 #404040，Peach 映射成的
`#40404099` 是忠实的，两边算出来都只比 #262626 亮几个色阶。

### primary 的三档：`.bg-button-primary` 规则原文

```css
.bg-button-primary{isolation:isolate;background-image:var(--gradient-button-primary-default);position:relative}
.bg-button-primary::before{content:"";z-index:-1;pointer-events:none;border-radius:inherit;
  background-image:var(--gradient-button-primary-hover);opacity:0;
  transition:opacity var(--button-transition-ms) ease;position:absolute;inset:0}
.bg-button-primary:hover:not(:disabled):not([aria-disabled="true"])::before{opacity:1}
.bg-button-primary:active:not(:disabled):not([aria-disabled="true"]){background-image:var(--gradient-button-primary-active)}
.bg-button-primary:active:not(:disabled):not([aria-disabled="true"])::before{opacity:0}
.bg-button-primary:disabled,.bg-button-primary[aria-disabled="true"]{background-image:var(--gradient-button-primary-disabled)}
.bg-button-primary:disabled::before,.bg-button-primary[aria-disabled="true"]::before{display:none}
```

`--button-transition-ms` 是 .15s。三档渐变都是 180deg 两停点，按色阶排 default
`accent-500→600`、hover `400→500`、active `600→700`；canvas 取像素得
300=`#8ec5ff`、400=`#3392ff`、500=`#2b7fff`、600=`#155dfc`、700=`#1447e6`。
其余 computed：`height:36px`、`padding:8px`、`border:0px`、`border-radius:10px`、
`box-shadow:rgba(0,0,0,.05) 0 1px 2px`、`color:#fff`、`font:500 14px/20px Inter`、
`background-origin:padding-box`、
`transition:background-color .15s, border-color .15s, box-shadow .15s, color .15s, transform .42s`。
按下另有 0.98 缩放（`button-press-motion`），键盘是 `focus-visible:ring-2`。

**纠正（2026-09-12）**：这一节此前记的是「primary 没有悬停态：三条独立证据」，同日取到上面
这段原文后作废。三条证据全落在同一个盲点上——那一层挂在 `::before` 上：① 读 `className`
看不到，悬停不是 Tailwind 的 `hover:` 类；② 真鼠标悬停读 computed 也看不到，读的是元素自己的
`background-image`，变的是伪元素的 `opacity`，`getComputedStyle(el)` 不传第二个参数就取不到；
③ 注册表 `/r/button.json` 的 primary 那一行确实只有 `bg-button-primary text-text-white shadow-xs`，
因为三档全长在那个类的 CSS 里，注册表只列类名。结论涉及「某个状态没有样式」时，
`::before` 和 `::after` 两个都要显式读一遍再下判断。

### Button Group（2026-09-12）

两半各自带边，分隔线就是右半自己的左边界，上下顶满：

- 左半 `rounded-l-2lg border border-r-0 border-border-button-default bg-background-primary-default
  p-2 shadow-xs transition-colors hover:bg-background-primary-hover`
- 右半 `rounded-r-2lg border border-border-button-default …
  hover:bg-background-primary-hover`

悬停填充 `#f7f7f7` 比那条线 `#ebebeb` 浅，所以压哪一半，线都还在，两侧填充各自停在它两边。

## Peach 采用与差异（Button 与 Button Group）

- 强调档全站一副面，三档渐变、`::before` 的交叉淡入和 `border:0` 都照上游；token 是
  `--board-blue`、`--board-blue-hover`、`--board-blue-active`，规则写在 `web/board.css` 的
  `body :is(…).primary:not(:disabled)`；错误页、登录页和首启页由页面包画，主按钮是 BoardUI Button。
- `border:0` 不是省事：补一圈透明边在 `box-sizing:border-box` 下会把内容盒压掉 2px，而
  `background-origin` 是 padding-box，渐变被压到 34px 再延展回 36px，色标跟上游错开一像素。
- **`padding` 是 Peach 的主动差异**：取 `8px 12px` 而不是上游四边 `8px`，中文字比拉丁字宽，
  四边等距时两侧字贴着边。上游 `active` 那一下的 0.98 缩放（`transform .42s`）没有跟，
  按下换渐变已经读得出来。
- 分体按钮的分隔线按上游的做法交给右半的 `border-left`，上下顶满；两半的悬停走 Peach 自己的
  `--control-hover`（浅色 #ebebeb、暗色 #404040）而不是上游的 `background-primary-hover`，
  理由与次级按钮那一条相同，见上一节。
