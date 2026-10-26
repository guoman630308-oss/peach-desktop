# ADR-0076：统计页与口味页的图表改用 Recharts + EvilCharts，热力图与流向图保留自绘

- 状态：Accepted
- 日期：2026-09-26
- 关系：ADR-0031 的 React + BoardUI 前端；`frontend/src/react/evilcharts/ORIGIN.md` 记来源与差异

## 背景

统计页和口味页的径向图、雷达、排行条、热力图、流向图都是手写 SVG，各自一套几何函数，悬停时只换卡片
页头的读数，没有浮层提示，也没有进场动画。用户 2026-09-26 要求参照 EvilCharts（`https://evilcharts.com/docs`）
重写这两页的图表，并用同一套样式补上还没展示的数据，先改生产页面。

EvilCharts 是 shadcn 注册表形态的图表组件，MIT 许可，同时有 Recharts 与 ECharts 两个分支，组件以源码形式
复制进项目，和 BoardUI 的接法一样（ADR-0031）。

## 决策

**一、用 Recharts 分支，源码逐字复制进 `frontend/src/react/evilcharts/`。** 依赖钉 `recharts@3.8.0`、
`motion@12.43.0`、`clsx@2.1.1`。这一支画 SVG，系列颜色是 CSS 变量：`ChartConfig` 里写
`var(--color-chart-N)`，深浅两档跟着 BoardUI token 走，不用在 JS 里读色值；提示浮层、轴刻度写的是 Tailwind
类，桥到 BoardUI 已有 token 就能用。上游文件不改，差异（路径别名、`cn`、语义色名、reduced motion、浮层内容、
lint 排除）全在 Peach 自己的文件里，由 `UPSTREAM.sha256` 与 `tests/test_frontend_build.py` 守住。浮层内容要重组，
是因为上游容器写的 `grid` 类与旧样式表卡片网格的 `.grid` 同名。

**二、不用 ECharts 分支。** ECharts 默认画在 canvas 上，颜色是 option 里的字面值：换深浅色要在 JS 里重读
token 再重设 option，Tailwind 类与 `@theme` 管不到图里任何一处，BoardUI 的 token 体系在图上断开。
它还是整套图表引擎，React 岛是单包产物（`codeSplitting: false`），多出来的体积每个页面都要付。
Recharts 是 React 组件树，和现有的 React Aria、Query 在同一棵根里，状态直接用 React 的。

**三、热力图保留自绘，视觉对齐 EvilCharts。** Recharts 没有热力图类型，EvilCharts 的 Recharts 分支也没有；
一格一个 `rect` 本身就是最小实现，换库没有东西可以复用。热力卡搬到 `frontend/src/react/charts/`，统计页的
播放时间与口味页的浏览时间共用：格子颜色取 `chart-*`，悬停时其余格子淡下去，浮层与 EvilCharts 图共用
`frontend/src/react/charts/chart-tip.tsx` 那一块面，页头读数照旧跟着指到的那一格走。

**四、流向图保留 `d3-sankey`。** EvilCharts 的桑基图按节点名生成 CSS 变量和渐变 id，创作者名不能直接当
标识符；节点标签只能附节点总量，Peach 右侧要显示的是线索占比；节点和流都不能用键盘聚焦。换过去这三样
都要回头补，改动不可控，所以只共用卡片外壳与页头。

## 后果

- 径向图、雷达、柱状图由 EvilCharts 组件画，带悬停浮层与进场动画；系统开了减弱动态效果时，Recharts 的
  动画（`isAnimationActive="auto"`）与 Motion 驱动的柱子生长都直接画终态。
- 统计页补上时长分档、画质分档、文件类型、最近播放时间与播放次数分布；口味页的图表外观与统计页一致。
- 升级 EvilCharts 时重新复制文件、重算哈希；上游改了组件接口，`../charts/` 里的组合跟着改。
