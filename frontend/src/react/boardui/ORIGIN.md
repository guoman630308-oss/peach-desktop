# BoardUI 源码

普通分段控件、短菜单悬停面、共用弹窗与选择条动效由 `../components/` 组合实现，参数与差异见 `docs/reference-snapshots/fluid-functionalism.md`；本目录副本不承担这些扩展。

来源是 BoardUI 的 shadcn 注册表条目 `https://www.boardui.com/r/<条目>.json`，2026-09-15 取得。
许可证为 MIT，原文见 `web/vendor/boardui-LICENSE.txt`。

本目录逐字复制条目里 `files[].content`，相对路径与上游 `path` 相同，上游源码里的
`@/utils/cx` 因此不用改写。这里的文件不做修改：Peach 需要不同的组合或外观时，
在 `../settings/` 这类 Peach 自己的目录里组合，差异写进下表。

`UPSTREAM.sha256` 记着复制时每个文件的 SHA-256，`tests/test_frontend_build.py` 逐文件比对：
改了副本、多出没登记的文件都会红。升级上游时重新复制、重算对应行，并更新下表的条目哈希。

| 条目 | 注册表 JSON 的 SHA-256 | 复制的文件 |
| --- | --- | --- |
| `tree-view`（2026-10-17） | `72b06e3187ae7738bd1eabd46bc15ebe1d2d49be768e44306b266d91206eeb9c` | `components/base/tree-view/tree-view.tsx` |
| `breadcrumb`（2026-10-17） | `15ec77574bb50fd5e37bf6d157905ffe7eb03226585cc77983c0beea92abd811` | `components/base/breadcrumb/breadcrumb.tsx` |
| `direction`（2026-10-17） | `94ff665ff5060ffbbbda89f395882c0236646db74f6b080ab987518fb5fa4592` | `components/foundations/direction/direction.tsx` |
| `avatar`（2026-10-14） | `614f2a384e2d44c0a15df8e3fc42c1648ccd7101f1c8be98911087641ab28aec` | `components/base/avatar/avatar.tsx` |
| `theme` | `436353b1f8466dd56adf3909cb7a871288e0d6f5b067cf03a2f860375d2a013e` | `styles/theme.css` |
| `typography` | `5b7eca25350829755eb15cb474ab009fd1f8b929e62d54182f648b4f2e97bf8e` | `styles/typography.css` |
| `cx` | `d118c2ace1454a92fbfb69ad0d03b2916fece06ef5102ae26dcedaa6da2e390c` | `utils/cx.ts` |
| `button` | `89cc2c176d1d94d481bbfc6e34f233ccf109c10d319533cc7c1e98923b6e7d69` | `components/base/buttons/button.tsx` |
| `input` | `ac1e66c9ed15f9db2750dd528bc894c856ac84e79f72ff4acb96f14b1ab9c249` | `components/base/input/{input,label,hint-text}.tsx` |
| `checkbox` | `6210fdb4c54aab6dd89db3ae1bc387596a2dbacde62896aecaa2a7e15ece859c` | `components/base/checkbox/{checkbox,checkbox-glyph}.tsx` |
| `settings-modal` | `eb01742ca56daa473f042244176cd697fc20010ab18f680e3a3a8012d69f6c5b` | `components/application/settings/settings-rows.tsx` |
| `switch` | `3412c1910b2fa7f5d17404bf50bd2503e9ca097cf9d741366379cf18e09ebeb6` | `components/base/switch/switch.tsx` |
| `select` | `36a3a2b91508bdb278ecf84d24b35a7324e8be0781677e5bc12d8333eb9ff5ef` | `components/base/select/select.tsx`、`components/base/dropdown/menu-styles.ts` |
| `chevrons` | `b08900e01f7a82dcd38a66f46041a576fcefae667ed7afaba5d177c480ae85b0` | `components/foundations/icons/chevrons.tsx` |
| `use-dismiss-on-outside-press` | `854569e5d2188146c1ebb41ceccc9eaaddb3d4a31b623946e7dd72be650636b1` | `utils/use-dismiss-on-outside-press.ts` |
| `link-button` | `05eb37b3cf1334c153e0702de05fe4989e4359c9c74d5ba55cc552a58e4629bd` | `components/base/buttons/link-button.tsx` |
| `icon-button` | `1441e8301efc6e16e0693194f876ce285ca5fe8d440156a060a9266e012d2c90` | `components/base/buttons/icon-button.tsx` |
| `chip` | `d2b0dd38146325acada58fbc241d13fcd633bb5407ce1ef457e752023fe282de` | `components/base/badges/chip.tsx` |
| `status-dot`（2026-10-17） | `f39a5df3e93d6e58884fa823c787bf9b94d048de9e2c5884d1e4a6346391fa54` | `components/base/badges/status-dot.tsx` |
| `table` | `fc12a8f2f4012d9e983e0b9bbb10f9fe3288d74046566d2623d60e7056c50d88` | `components/base/table/table.tsx` |

## 没有逐字复制的部分

目录浏览保留首页筛选、排序、卡片和详情结构。树采用 `small` 档和受控展开、选中状态，子目录按需从账本读取。面包屑按钮由 Peach 导航；上游 `next/link` 导入通过构建别名连接 `../components/board-navigation-link.tsx`，只承担原生链接槽，不引入 Next.js。上游目录树需要的 `menu-styles.ts` 继续使用已登记副本。全部依赖已由当前 React、React Aria、Motion 和 Remix Icon 清单覆盖。

| 上游 | 处理 | 原因 |
| --- | --- | --- |
| `globals`（`693885494e6f229637fe118852efbfb3df820dd8d174a2a7e47f65dadfcd96bf`） | 不复制整份；用到的 `check-draw` 动画、根元素的字体平滑和 `.bui-table` 那一组规则逐字搬进 `../styles.css`，作用域是 `.peach-react` | 整份 `@import "tailwindcss"` 会把 Preflight 和 `html`、`body` 的底色铺到整页，未迁移页面仍由旧样式表绘制。`table` 条目的外观全在 `globals.css` 里（React Aria 的集合组件包不进自定义组件），不搬就只剩一张没有边线和字阶的裸表 |
| `data-table`（`7bb73a6cd099b9b16390e5fe00f8ba2d55b5ef96c1e8f90a056168cd2e3235e6`） | 不复制；表格视图用 `table` 条目的 `Table` 配 `@tanstack/react-table`，列定义、排序、行选择与分页写在 `../follow-manage/` 里 | 该条目是 `registry:block`，只有一份 `docs/examples/data-table-example.tsx` 示例，依赖的 avatar、segmented-control、status-dot、tooltip 等条目 Peach 都没有；它演示的正是「`table` 加 TanStack Table」这套接法 |
| Tailwind Preflight | `../preflight-scoped.css` 逐字包进 `@scope (.peach-react)` | 同上 |
| 深色模式 | 上游读 `<html class="dark">`；`appearance/theme.ts` 的 `applyTheme()` 与 `index.html` 首帧脚本按实际深浅加减这个类 | Peach 的主题选择写在 `data-theme`，跟随系统时不写属性 |
| 与 `web/board.css` 同名的 `--color-*` token | `:root` 上由 `board.css` 定值；`../styles.css` 在 `.peach-react` 与 `.dark .peach-react` 上按 `theme.css` 原文重新声明 | 未迁移页面的颜色保持不变，React 子树读到上游值 |
| 深色的 `--color-border-checkbox-default` | 两份定值都抬到 `neutral-600`，不取上游的 `neutral-700`；`tests/test_frontend_build.py` 的 `LOCAL_TOKEN_VALUES` 记着这一条 | 未选中的勾选框底色就是卡面色，形状全靠那 1px 边线说话。`neutral-700` 压在 `neutral-800` 上只差 11 个 CIE 明度，浅色那一对有 15，低亮度那头的细线当场消失，暗色下看着就是没有框 |
| 深色的 `--color-separator-border` | 两份定值都抬到 `neutral-700`，不取上游的 `neutral-800`；`tests/test_frontend_build.py` 的 `LOCAL_TOKEN_VALUES` 记着这一条 | 深色的 `--color-background-primary-default` 也是 `neutral-800`，两者同色。复核卡的外框、候选块与它下面那段证据之间的线、卡片脚注带的上边线全画在那种面上，明度差是 0.0，不是弱而是没有。`neutral-700` 在卡面上差 11.9，浅色那一对是 9.1；再亮一档就成了抢眼的白线 |
| 主题阴影 | `../styles.css` 在 `@theme` 里把 `--shadow-*` 接到 `--elevation-*`，亮暗两档分别取 `theme.css` 原值 | Tailwind 将阴影字面值编入工具类；通过变量间接引用确保切换主题时生效。卡片靠填充区分层级，阴影只收边 |
| 弹出层的挂载位置 | `../entry.tsx` 用 `react-aria` 的 `UNSAFE_PortalProvider` 把 Popover 渲染进 `body` 末尾一个同样带 `.peach-react` 的容器 | 上游 Popover 渲染到 `body`，落在 token 重声明与 Preflight 的作用域外，读到的是 `board.css` 的值 |
| 焦点环 | 输入框只画 BoardUI 外框上的 `ring`；`web/css/01-base.css` 的全局 `:focus-visible` 排除 `.peach-react` 子树 | 旧样式表排在后面，同特指度时会盖过 `outline-none`，内层输入框多出一圈 |
| 与旧样式表同名的类 | 网格容器放在 flex 父元素里写 `inline-grid`，块级化后按 `display:grid` 计算，类名不和卡片网格撞，也不触发任意值 lint；`../styles.css` 用 `@source not inline("ring")` 不生成注释里扫到的 `ring`；`frontend/test/legacy-class-names.test.ts` 核对产物与旧样式表无同名类 | 旧样式表排在后面，卡片网格那条同名规则会把 `grid-cols-*` 压成一列，生成的 `.ring` 也会落到旧页面的 `.ring` 元素上 |
| 提交键忙态 | 写 `aria-busy` 与 `aria-disabled`，不画 Spinner | `button` 条目没有加载态 |
| 行内提示 | `../components/note.tsx` 的 `Note` 按语气取 `status-yellow`、`background-tertiary-error`、`notification-*` token 组合 | 注册表里没有行内 Note 组件；`notification` 条目是带关闭键和动效的浮动通知 |
| 进度条 | `../components/progress.tsx` 的 `Progress` 用 SVG 矩形画 | 注册表里没有进度组件 |
| 空态 | `../components/empty-state.tsx` 用一圈 `separator-border` 框住图标、标题与说明 | 注册表里没有空态组件 |
| 等待点 | `../components/loading-dots.tsx` 三颗点，错相由 `../styles.css` 的 `dot-wave-*` 给 | 注册表里没有等待态组件，没有总量时也不画进度条 |
| 功能启停标识 | `../settings/jav-browser-settings.tsx` 用 Remix Icon 实心圆配 `text-tertiary` 灰色与 `notification-success-foreground` 绿色 | `status-dot` 只有绿、黄、靛三种颜色；功能关闭使用中性灰色，不表示警告 |
| 分区标题与任务卡 | `../activity/activity-page.tsx` 自己用 `title-*` 字阶和 `separator-border` 的圆角框排，失败那张换成 `border-error-default` | 注册表里没有分区标题；卡片条目都带自己的头尾结构与操作区 |
| 汇总行与卡片网格 | `../quality-goals/quality-goals-page.tsx` 的汇总一行用 `title-2-semibold` 配正文字阶排；网格取 `../styles.css` 的 `card-grid`，封面取 `w-card-cover` 与 `aspect-card-cover` | 注册表里没有列表页的汇总行；`auto-fill` 网格与定宽封面在工具类里没有对应档位，写成 `@utility` 与 `@theme` 而不是任意值 |
| 折叠 | `../settings/section.tsx` 的 `Disclosure` 用原生 `details`，开合调共享源码 `ui-kit/collapse.ts` 的 `setCollapseOpen`，高度按共用 Collapse 的 `.ui-fcollapse` 过渡（`.2s ease-in-out`） | 注册表里没有折叠组件；原生 `details` 不过渡高度 |
| 图标选择 | `../settings/library-icon-picker.tsx` 用 React Aria 的 `Popover`、`RadioGroup` 组合，面板取 `menu-styles.ts` 的外观 | 注册表里没有网格单选的弹出面板 |
| 二选一切换 | `../scraping/scraping-page.tsx` 提供 Cookie 的两种方式用 React Aria 的 `RadioGroup`，选中项取 `background-tertiary-default` 配 `text-primary` | 注册表里 `tabs` 是页面级导航、`segmented-control` 没有条目；这里切的是同一个字段的两种填法，不是两块内容 |
| 选文件 | 同上：原生 `input[type=file]` 只留着接文件，点它的是一颗 `secondary` 按钮，选中的文件名跟在旁边 | 注册表里没有文件选择组件；原生控件的按钮长相由浏览器决定，改不动 |
| 分区标题旁的次要内容 | `../settings/section.tsx` 的 `Section` 收一个 `aside`，标题占剩下的宽度、它靠右（来源站点的站标与登录地址） | `SettingsSectionLabel` 只画标题；把外链塞进标题里会进无障碍名称 |
| 站点标识 | `../components/site-mark.tsx` 的 `SiteMark` 读取本站缓存与静态站标，失败时显示通用站点字形；来源凭据与诊断页共用 | `../settings/section.tsx` 的 `SourceMark` 只认雪碧图字形与内嵌 PNG，采集来源的图标是一条服务端地址 |
| 主键带下拉 | `../library-processing/processing-actions.tsx` 的 `ProcessingActions`：扫描与缺图补全共用，触发键是一颗 `iconOnly` 的 `Button`，面板用 React Aria 的 `Popover` + `MenuDialog`，行的外观取 `components/base/dropdown/menu-styles.ts` | `dropdown` 条目的 `DropdownTrigger` 自己就是那颗按钮、外观全由 `className` 给，`@shadcn/lint` 的 `no-restyle` 只放行布局类，套不进 `Button` 的档位 |
| 常驻处理任务 | `../library-processing/library-processing-notice.tsx` 与 `library-processing-dock.tsx` 组合进度、折叠正文、估时和直接控制，Sonner 按目标栈管理位置与持久停留 | 用户要求参考公开 Agent Progress 自行实现，任务由服务端真实阶段推进；取证与领域差异见 `docs/reference-snapshots/processing-task-controls.md` |
| 进度环 | 同上的 `Gauge`：SVG 两圈，`pathLength={100}` 把一圈长度钉成 100，画出来的那一段就是当前阶段百分比 | 任务总量未知时使用静态任务图标，已知总量才绘制读数 |
| 三档处理强度 | `../library-processing/processing-effort.tsx` 基于 React Aria Slider 和已登记的 Button、Popover 组合，最高档 SVG 流光读取强调色色阶 | 用户要求参考 Composer Panel Effort 公开演示重建，六档适配低占用、标准、全速；流光绘制为 Peach 重建方案 |
| 模态弹层 | `../avatar-picker/avatar-picker-page.tsx` 用 React Aria 的 `ModalOverlay` + `Modal` + `Dialog` 组合，遮罩取 `../styles.css` 的 `--color-scrim`（同旧样式表 `.geist-modal::backdrop` 的黑 60%） | 注册表里没有模态弹层，也就没有它的遮罩底色 |
| 候选网格 | 同上：一排四张 3:4 的候选，弹层宽度与格子比例取 `../styles.css` 的 `--container-avatar-picker` 与 `--aspect-avatar-choice` | 注册表里没有图片选择网格；这两档在工具类里没有对应档位，写成 `@theme` 而不是任意值 |
| 空态里的去处 | `../components/empty-state.tsx` 的 `EmptyState` 收一个 `actions`，按钮与链接画在说明下面 | 上游没有空态组件；标题、说明和那个去处必须在同一个组件里，散到调用处就会各排各的 |
| 读数兼页签 | `../stats/stats-page.tsx` 的四张读数卡是 React Aria 的 `Tabs`／`TabList`／`Tab`，选中态是一圈 `border-focus-ring` | `tabs` 条目是页面级导航的下划线页签，这里切的是同一页里的四层细节，而且页签本身要显示读数 |
| 径向图 | `../stats/radial-card.tsx`：圈由 EvilCharts 的 `EvilRadialChart` 画（`../evilcharts/ORIGIN.md`），图下的图例格子取 `background-tertiary-default` 与 `border-focus-ring`，高亮是 React 状态 | 注册表里没有图表条目；`library-processing-notice.tsx` 的 `Gauge` 是单圈进度，排不下多段对比 |
| 排行 | 同上页面的 `TagRanking`：两列的 `ol`，收起时露前十，展开键是一颗 `secondary` 按钮 | 注册表里没有排行榜；`table` 条目带表头与排序，这里一行只有名次、名字和一个数 |
| 两套证据的切换 | `../taste/taste-page.tsx` 顶上用 React Aria 的 `Tabs`／`TabList`／`Tab` 整块切浏览器记录与 Peach 内部，选中态取 `background-tertiary-default` | `segmented-control` 没有条目，`tabs` 条目是页面级导航；这里切的是两块各自成篇的内容，不是同一个字段的两种填法 |
| 雷达图与排行条 | `../taste/charts.tsx` 的 `TasteRadar` 与 `RankedBars`：由 EvilCharts 的 `EvilRadarChart` 与横向 `EvilBarChart` 画（`../evilcharts/ORIGIN.md`），颜色只取 `chart-4` | 注册表里没有图表条目；`../stats/radial-card.tsx` 是环形占比，画不出多维度的形状与并排的长度 |
| 热力图 | `../charts/heat-card.tsx` 的 `ActivityHeat`，统计页与口味页共用：一格一个 `rect`，浓度是 `fill-opacity`，指到哪一格读数换成哪一格，浮层与 EvilCharts 图共用 `../charts/chart-tip.tsx` | 注册表里没有热力图，EvilCharts 的 Recharts 分支也没有；一格单独没有标注，不换读数就只剩一片颜色深浅 |
| 流向图 | `../taste/charts.tsx` 的 `CreatorSankey`：布局由 `d3-sankey` 算，路径与节点是 SVG，淡入淡出用 `stroke-opacity` | 注册表里没有流向图；`table` 条目排得出同样的数，但看不出来源与创作者之间的分流 |
