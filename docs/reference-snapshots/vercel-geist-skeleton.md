# Vercel Geist Skeleton 取证（2026-09-04）

> 本文是 Peach 的人工取证笔记，不在 `reference-sources.json` 里登记。

## 请求与取得通道

- 目标：`https://vercel.com/geist/skeleton`。
- 用内置浏览器取得，正文、六组示例的元素属性与 `getComputedStyle`（含 `::after`）都已取得。
  下面的数值全部出自计算样式和元素类名，没有截图测量，也没有推断。
- 上游 Markdown 原文未取得：该入口声明 `text/markdown`，直接读取通道返回 HTTP 400，
  因此没有 HTML SHA-256 可锁。

## 微光的实现方式

骨架本体不改自己的不透明度，微光整个画在 `::after` 上：

- 宿主：`position:relative; overflow:hidden`，自身 `background:transparent`。
- `::after`：`content:''; position:absolute; inset:0; right:-200%`，即宽度是宿主的三倍；
  `background-size:50% 100%`、`background-position:0 0`。
- 渐变实测 `linear-gradient(to right in oklab, rgb(26,26,26) 0%, rgb(31,31,31) 50%, rgb(26,26,26) 100%)`，
  也就是 `--ds-gray-100`（明度 10%）到 `--ds-gray-200`（明度 12%）再回来，只差两个百分点。
- 动画 `1.5s ease-in-out infinite reverse`，关键帧 `@keyframes loading-skeleton{100%{transform:translate(-50%)}}`。
- 判据：微光靠移动被看见，不靠明暗跳变。

## 三个形状

| 变体 | 类名 | 实测 border-radius | 正文指定用途 |
| --- | --- | --- | --- |
| Rounded（默认） | `rounded-[5px]` | `5px` | 按钮、胶囊 chips |
| Pill | `rounded-full` | 计算值溢出成全圆 | 头像 |
| Squared | `rounded-none` | `0px` | 图块 image tiles |

正文原则：形状要照最终元素的形状选。

## 包住已有内容（wrapping children）

- 骨架是一个包住真实 children 的 `<span data-geist-skeleton>`。不给固定尺寸时，尺寸由
  children 自动算出；给了固定尺寸则 children 到位后骨架隐藏、尺寸保留。
- 加载态实测：宿主拿 `visibility:hidden`（连同真实 children 一起藏起来），
  `::after` 单独写 `visibility:visible`，所以被藏的是内容、留下的是同样大小的一块微光。
- 因此这种骨架天生零位移——那个框就是真实元素自己的框；`visibility:hidden` 的元素同时
  不可聚焦，正好落实下面无障碍那条。

## 正文给出的判据

- 用 Skeleton 的条件：异步数据要填进一个已知版式（表格行、卡片网格、资料块、侧栏）。
- 单次进行中的动作用 Spinner；不定长的行内等待用 Loading Dots；进度已知用 Progress。
- 不拿 Skeleton 当常驻装饰，也不拿它当空态占位——没有数据可加载时渲染 Empty State。
- 宽高要等于最终内容，否则版式会跳：「200×20 的块变成 80×16 的字读起来像故障」。
- 骨架包住 children 时保持尺寸稳定，露出那一下不能让周围回流。
- 无障碍：`aria-busy="true"` 包住加载区，完成由目标容器上的 `aria-live="polite"` 播报，
  不写在骨架自己身上。
- 低功耗表面用 no animation 变体关掉微光，并尊重 `prefers-reduced-motion`。
- 骨架是装饰性的，加载期间不要把可聚焦控件放进去。

## Peach 采用与差异

- 微光照抄：`@keyframes skeleton-sweep{to{transform:translateX(-50%)}}`，`::after` 三倍宽、
  `background-size:50% 100%`、`1.5s ease-in-out infinite reverse`。高光色新增
  `--skeleton-sheen`，暗色 `rgba(255,255,255,.12)` 配 `--hover` 的 `.07`，亮色 `#E1E6EC`
  配 `#EEF1F5`，两档都对齐 Geist 两个百分点的幅度。
- 形状照最终元素选：顶部头像骨架走圆（`.av .ring` 本身就是 `border-radius:50%`），
  标签与厂牌胶囊走 `--pill-radius`，卡片图位保持卡片自己的 `--control-radius`——
  Geist 的 squared 是给它自己的方图块的，Peach 的卡片是圆角，照抄反而不匹配最终内容。
- 首屏骨架不另算几何，直接套真实类名（`.av`/`.ring`/`.nm`、`.brandpill`、`.pill`），
  这是「宽高等于最终内容」最省事也最不会算错的落法。实测顶部三层空态 18px、
  骨架态 160px、内容态 160px。
- 换一批时标签胶囊用 wrapping children 那一套：真实胶囊留在 DOM 里 `visibility:hidden`，
  微光画在 `::after` 上单独 `visible`。实测切换前后胶囊的宽高与横坐标完全相同。
- 由 state 决定、这次请求不会改的控件不进骨架：标签条的四枚视图胶囊、目录页的排序条
  都在加载期间就画成最终样子并接上事件。Geist 没有这条，它来自 Peach 自己的判据——
  骨架只盖真会变的部分。
- 换一批整体仍按正文归 Spinner：转圈的是 ⟳ 键，覆盖网格与顶部两块的全部等待时间。
- 框体不参与微光（数据管理的操作条、关注管理的头部条），排除口径是 `::after{content:none}`。

## 复核入口

- <https://vercel.com/geist/skeleton>
