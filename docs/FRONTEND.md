# 前端层

本页说明前端构建、挂载契约和页面迁移。按任务进入：

1. 修改现有页面：从 [开发循环](#开发循环) 安装依赖、构建和验证。
2. 新增 React 页面：按 [迁移下一个页面](#迁移下一个页面) 接入路由树。
3. 查询模块职责：看 [目录与产物](#目录与产物) 和 [挂载契约](#挂载契约)。

Peach 按 [ADR-0031](adr/0031-frontend-react-boardui-tailwind.md) 接入 React + Tailwind + BoardUI。主界面由 `frontend/src/react/bootstrap.tsx` 启动 `Application`，应用域控制器负责骨架、容器和页面助手，React 路由树负责客户端导航、页面与详情内容。主界面只有 `peach-app.js` 与 `peach-app.css` 两份产物，控制器与组件引用同一源码图。

只有一条不可变的约束：**运行时没有 Node**。Python 服务、PyInstaller 包和 macOS 上的
检出都直接读 `web/`，所以构建产物提交进 Git，不用任何 CDN。

## 目录与产物

| 路径 | 是什么 |
| --- | --- |
| `frontend/src/react/bootstrap.tsx`、`application.tsx` | 主界面启动入口与 Application 生命周期；配置共享状态、挂载路由树、启动应用域控制器并在卸载时清理 |
| `frontend/src/api.ts` | 带 `AbortController` 的取数封装 |
| `frontend/src/management.ts` | 数据管理首屏 Fieldset 与网盘能力显隐 |
| `frontend/src/appearance/` | 外观与版式设置（`@peach/appearance`）：偏好 store、主题、强调色、密度、卡片版式与光晕配色。别名直接解析到本目录，控制器与组件读写同一实例 |
| `frontend/src/core/` | DOM 取元素、请求、转义、格式化与路由常量（`index.ts`）、标签显示名（`tags.ts`）、番号标题（`jav-title.ts`）。`@peach/legacy/*` 是源码别名，主包直接包含唯一实现 |
| `frontend/src/ui-kit/` | 共享控件与模板：滚动条、Collapse、锚定菜单、Select、来源站标、读数动效、骨架、横滚、开关、拉条、确认框、音效与中段截断。`@peach/legacy/ui` 直接解析到 `index.ts`；独立页面包仅打入用到的控件 |
| `frontend/src/onboarding/` | 安装后教程状态（`post-setup-tutorial.ts`）：本地键、签名与请求代际，控制器直接引用共享源码 |
| `frontend/src/application/` | 按职责拆分的应用适配：目录、关注、实体、索引、布局、导航、媒体与全局动作。`initializeApplication` 按启动次序连接控制器，副作用由生命周期管理 |
| `frontend/src/react/pages/` | 独立页面包：`index.tsx` 是 `vite.pages.config.ts` 的构建入口，按挂载点的 `data-page` 画 SPA 外壳之外的三张页：首启（`setup/`）、登录（`login/`）与错误页（`error/`）。`auth-card.tsx` 是它们共用的外框 |
| `frontend/src/react/` | 路由页面、常驻组件与组件；`entry.tsx` 是内部命令式适配，`application-residents.ts` 连接常驻组件，`bundle.d.ts` 声明内部接口，`boardui/` 逐字复制上游源码 |
| `frontend/src/query/` | 全站唯一 TanStack Query 客户端（`@peach/query`）与共享来源查询。Application 的 `fetchQuery`、页面 `prefetch` 和 Providers 读同一实例；`media-sources.ts` 声明 `/api/sources` 与查询键 |
| `frontend/src/react/query.ts` | React 子树里取那一个客户端的入口，转出 `@peach/query` |
| `frontend/src/history/` | 全站唯一浏览器历史（`@peach/history`），与路由树共用 React Router 的 `createBrowserHistory`。地址经 `shellNavigate` 写入；覆盖条目在 `usr` 中记录背景，启动不读取背景 |
| `frontend/src/shell/` | 目录口径、选择集、详情来处与一次性请求的共享单例。控制器与组件直接引用活绑定；整体换值用 `writeShell`，原地修改后用 `notifyShell` 通知订阅者 |
| `frontend/src/react/router/` | 客户端导航：`<Router>` 接管那一份历史，后退前进由它自己处理；每条路径的页面（管理区、播放列表页、关注页、目录网格、索引页、资料页与沉浸）、覆盖组的详情与队列，以及首页筛选条、新作行、处理横幅与搜索下拉这四个页面内组件由它画（`managed-routes.tsx`）；页面组按条目记的背景匹配，覆盖组按真实地址匹配详情与队列 |
| `frontend/src/catalog-bars.ts` | 首页筛选栏与侧栏的两份聚合：`['facets', 口径]` 与 `['tops', 参数, 口径]`，续页 `['tops', 参数]`，30 秒复用，状态页名单为空时退回全库口径；壳的 `getBarsData` 只算参数串 |
| `frontend/src/react/components/` | Peach 自己的组合件（说明条、进度、空态、等待点），BoardUI 注册表里没有对应条目的那些 |
| `frontend/src/react/taste/` | 口味页：`taste.ts` 是契约与几何算法，`charts.tsx` 是雷达／名次条／热力／桑基，`taste-page.tsx` 是整页 |
| `frontend/test/` | vitest 用例与遗留模块的桩；`test/react/` 直接挂组件，`*-routes.test.tsx` 走路由树的开收契约 |
| `web/dist/peach-app.js`、`peach-app.css` | 主界面构建产物，**进 Git**，固定文件名，由 `web/index.html` 引用；服务端通过 `/dist/{name}` 提供 |
| `web/dist/peach-pages.js`、`peach-pages.css` | 首启、登录与错误页的独立构建产物，**进 Git**，固定文件名。主包构建后追加构建（`emptyOutDir: false`）；只有这两份产物允许免登录读取 |

首次运行页（未配置时的 `GET /`）、登录页（`GET /login`）与浏览器导航撞上的错误页是 SPA
外壳之外的三张独立页面。服务端对三页都只回同一张薄壳（`web_entry.page_shell()`）：主题预读
脚本、Inter、`/dist/peach-pages.css`、挂载点 `#peach-page`，`data-page` 取 `setup`、`login`
或 `error`，服务端才知道的变量写成挂载点上转义过的 `data-*`（登录页的 `next`、`invalid`、
`error`，错误页的 `status`、`detail`），首启页另带表单用到的三枚雪碧图字形。页面整个由页面包
`/dist/peach-pages.js` 用 BoardUI 画出，所以三页都离不开脚本：禁用脚本时只剩空白页。

- 首启页的题目取自 `GET /api/setup/questions`，提交走 `POST /api/setup`，形态见
  `docs/OPERATIONS.md`「首次设置的内部流程」。
  首启与配置页共用媒体目录行，只展示运行 Peach 的设备能读取的目录；Docker 填容器内目录。页面不展示内部路径输入。请求可省略 `root`，服务端补齐数据库路径；已有行仍携带原 `root`，修改媒体目录时保留对应关系。
- 登录页是原生 `<form method="post" action="/login">`，字段 `token`、`next`、勾选时
  `days=30`。`POST /login` 拒收时（口令错、429、400），HTML 请求回登录页并原位报错，状态码
  照旧，其余回 JSON。
- 错误页按 `status` 定标题（403、404、409 各一句，其余一句通用），404 不显示说明，只给一个
  「返回首页」。
- 三页用原生滚动条，不挂覆盖式滚动条。

页面包自成一份，不加载主包 `peach-app.js`，也不建 Query 客户端，
请求用裸 `fetch`。原因有三条：未配置的机器还没有数据库，主界面那一套一上来就打 `/api/items`；
首启只开一次，不为它改动每天加载的主包或拆出共享块；登录页在没有会话时就要画出来，免登录面只多这两份产物，不连带主界面的包。和配置页共用的控件（媒体目录行、密码与确认两格、忙态属性）在
`frontend/src/react/settings/`，两边各自打进自己的包。`@peach/legacy/ui` 在页面包里由别名落到
`pages/legacy-ui.ts`，只把来源站标与折叠的源码打进独立包。

样式层：`pages.css` 与主界面的 `styles.css` 共用 `base.css`（暗色变体、Inter、阴影 token 与
`.peach-react` 容器基线、输入框静止态边线），Preflight 同样限定在 `.peach-react` 里，薄壳把这个类挂在 `<body>` 上，弹出层落进 body 也在范围内。
`styles.css` 以 `@source not "./pages"` 排除页面包，三页的工具类只进 `peach-pages.css`。
深浅色读 `localStorage` 的 `peach.settings.v1`，同时写 `data-theme` 与 `<html>` 的 `.dark`。
完成态在独立包上过 `RESTART_REDIRECT_MS`（`frontend/src/react/restart-redirect.ts`，与配置页
保存后的跳转同一个数）自动跳到入口。

### 配置页

配置好之后改文件夹与端口的那张页整个是 React（`frontend/src/react/settings/`，入口 `configuration-page.tsx`）。`/configuration` 是唯一的编辑页，进管理菜单；媒体库选单、统计页与首次配置引导都指向它。

- 结构：一条窄列里排「通用 / 媒体 / 下载 / 网络与访问 / 维护」五组，每组一个 `h2.ui-configgroup` 小标题，没有内容的组连标题一起省略。页面自己画顶上那排页签（`.ui-board-local-nav`，`role="tablist"`，方向键与 Home／End 在整排里走），一组一格，一次只显示选中的那一组；页签条和整页同一次提交画出，第一帧就在。
- 数据契约是 `/api/configuration`（`src/peach/routes_configuration.py`）。端点字符串只在 `frontend/src/configuration-endpoints.ts` 声明一次，整页和设置弹层的摘要卡读同一个 `queryKey`。
- 第一帧必须同步：壳挂完这一页紧接着按地址里的 `#peachProxy` 滚到那一块，所以路由树用 `flushSync` 画第一帧，往后的更新照常异步。`.ui-configpage` 的第一层依次是页签条、各组的小标题与面板；每组的根节点就是那一格 `role="tabpanel"`，选中的那一组带 `ui-board-group-active`。
- 跳到某一组：别处（统计页「添加媒体目录」、空库提示、媒体库选单「管理媒体库」、诊断页）把组名记进壳单例 `configurationRequestedSection`（路由树里的页面经 `actions.requestConfigurationSection`），壳打开这一页时作为 `open.section` 交进去、页面画上之后清空；页面只拿它定第一帧选中哪一格。
- 设置弹层「这台电脑」一格只挂 `configuration-summary`（`configuration-summary.tsx`）：媒体库数、端口、更新状态和「打开配置页」，不放可编辑的控件（ADR-0050）。媒体库数取 `/api/configuration` 的 `library_count`，由服务端按 `media_libraries.libraries` 分组数好，页面不自己归并。
- 相邻的两处不在这页：媒体修复是数据管理页 React 子树里的一张卡（`frontend/src/react/media-repair/`），订阅源是关注管理页的「订阅源」页签（`follow-manage/feed-sources.tsx`，读 `/api/feeds`）。
- 服务端检查部署方式和请求来源：桌面服务由托盘管理且请求来自本机；Docker 服务由容器入口管理，允许经过登录验证的本机或私有网络请求，拒绝携带代理转发头的管理请求。`/healthz` 按调用方返回 `configurable`，页面据此显示配置入口；入口隐藏不能替代服务端访问检查。
- 表单校验的原因由服务端按字段给（400 的 `errors`），页面写回原位，不在前端复制判定。
- 浏览器直接导航撞上 `HTTPException` 时，`api.py` 的处理器按 `Accept` 回一张 HTML 错误页（`routes_pages.error_page`，薄壳由页面包画），`/api/` 下和非导航请求仍回 JSON。

### 索引页

`/performers`、`/creators`、`/studios`、`/agencies`、`/tags` 五张索引页整个是 React（`frontend/src/react/index/`，入口 `index-page.tsx`），由路由树画进 `#index`。

- 地址栏是唯一真相。索引元素从地址读出 `q`、`scope`、`view`、`category`，经 `openManagedRoute` 作初值交给页面；页面换档只改自己的状态，经 `ShellActions.routeIndex` 交壳，由 `shellNavigate` 写回地址并认领，不重挂。后退前进到另一份 search 时领新的页面打开序号，按地址重开。从侧栏进来一律回到本地、字母表、全部类型。
- 本地名册与词表读 `/api/index`，在线那一档读 `/api/follow/authors` 与 `/api/follow/tags`，键建在 `frontend/src/react/follow/online-vocab.ts`，归关注那一侧。四份都是 `useInfiniteQuery`，「载入更多」取下一页；打字过滤时新结果到手前留着上一份，不铺骨架。
- 圆框里那段 HTML 由 `card-art/markup.ts` 的 `avatarInner` 拼，经壳的 `personAvatar` 递进来；原尺寸摆图、补底与首字母收起的规则在 `web/css/01-base.css` 的 `[data-person-ring]`，量图的是 `installCardArt()` 挂在文档上的 `load` 监听。
- 顶栏选择键归壳，选择模式记在 `@peach/shell` 的 store 里：索引元素订阅它，推给画着的本地标签页；关掉时页面清空所选。所选标签的操作条三颗键都不写账本，「显示结果」回目录按所选标签筛选。
- 数据回来之前的骨架由索引元素写进 `#index`，模板在 `frontend/src/index-skeleton.ts`（正文那段是 `ui-kit/skeleton.ts` 的 `indexSkeletonHtml`），页头骨架与页面同一组文字。本地艺人那一排身份分类在骨架里就是最终长相，词表与骨架都在 `frontend/src/identity-filter.ts`，页面与骨架模板共用。

### 资料页

`/performers/:name*`、`/studios/…`、`/creators/…`、`/series/…`、`/agencies/…` 五类资料页是同一个 React 页面（`frontend/src/react/entity-page/`），在 `ENTITY_ROUTES` 里按 `/performers/*` 这样的模式登记，由路由树按匹配画进 `#index`；种类与名字由元素从地址读出（先 `decodeURIComponent` 再匹配，名字里的斜杠吃掉剩下全部段）。

- 资料卡、筛选浮层、新作那一行与正文是同一页的四块。资料页元素排好框架（加载骨架的形状仍由壳经 `ShellActions.entity.loading` 画，它依赖目录的形状名单与卡片比例），经 `openManagedRoute` 的 `place` 在首屏取齐那一刻换进 `#index`，页面画进资料卡那一格，再 portal 进另外三块：浮层吸顶要它的父盒就是 `#index`，新作那一行是遗留层卡片、不进 `.peach-react`。
- 地址栏是筛选与媒体视图的唯一真相。页面改筛选调 `actions.route`，壳的 `routeEntityPage` 写好地址再经 `updateManagedRoute` 推回新的 `filters`／`media`；版式、选择态与展示设置由 `pushEntityPage` 推。后退前进落在同一位的另一份筛选上时只推新值，不重挂；刷新经 `revision` 加一重取。
- 筛选条的语境由 `currentBarsContext()` 推：作品详情开着时用 `openItem` 记下的那一份，`#index` 里画着资料页时按那一页的 props，其余用首页那份。

### 播放列表页

`/playlists` 列表页整个是 React（`frontend/src/react/playlists/`，入口 `playlists-page.tsx`），登记在 `BROWSE_ROUTES`，由路由树画进 `#stats`；点开一份之后的 `/playlists/:playlist/:item` 仍是遗留层 `openPlaylist`。这一页不进 `isManagedPath`，页面里的跳转都交壳。

- 读 `/api/playlists`，首屏 `prefetch` 写明 `staleTime: 0`：首页刚存的 Mix 进来就要看得到。新建、改名、删除都 POST `/api/playlist`，写完让列表键重取，不拿回话拼缓存。
- 删除先过遗留层 `confirmModal`，删之前 GET `?id=` 取回内容，撤销按原内容与来源重建一份；取不到就不给撤销。回执与撤销失败的说法归 `actionReceipt`。
- 页面画着时壳要求重读（顶栏「换一批」），壳把 `@peach/shell` 的 `playlistsRevision` 加一，页面只重取、不重挂。从播放队列返回、后退前进与侧栏进来都整页打开一次。
- 每份列表是共用的 Mix 卡 `components/mix-card.tsx`：纸边、黑底封面、玻璃徽标、叠放头像，几何写在 `styles.css` 的 `[data-mix-*]`；悬停翻页是 `components/use-stack-flip.ts`（关注页卡叠也用它），时序钉在 `use-stack-flip.test.tsx`，翻页门槛（多选、遮挡、减少动效、滚动中）由壳经 `ShellActions.canFlip` 递进来。
- 改名弹层、换头像与裁剪封面共用 `components/modal-frame.tsx` 的外壳，`form` 档 540px 同 `ui-kit/modal.css` 的 `.ui-geist-modal`。

### 关注页

`/follow` 列表页整个是 React（`frontend/src/react/follow-feed/`，入口 `follow-feed-page.tsx`），登记在 `BROWSE_ROUTES`，由路由树画进 `#stats`。它不进 `isManagedPath`，由 `frontend/src/react/router/pages/follow.tsx` 的元素按匹配打开；重新进入时重掷种子由壳写进 store，元素按 store 的代次重取。

- 地址栏是筛选的唯一真相。页面改筛选、排序、换一批调 `actions.route`，壳的 `routeFollowFeed` 写好地址并认领，再经 `pushFollowFeed` 把新的 `view`／`seed` 用 `updateManagedRoute` 推进画着的那一页：代次不变、不重挂。判据是 `#stats` 的 `managedEntry` 记着 `/follow`。
- 助手与动作是壳里各一份、身份不变的对象（卡片按引用比较），跟着打开交进来，不进 `ShellActions`。标签的界面名称列表页与卡片直接从 `@peach/legacy/tags` import。
- 骨架淡出：关注页元素经 `place` 交自己的 `reveal`，它在 `revealSkeleton` 的 write 里只放空宿主，页面紧接着在同一个任务里画进去，骨架淡出时底下已是整页。
- 深链 `/follow/item/:id` 开在舞台里：壳的 `renderForDetail` 只让出列表区，回到列表时才画；`followDetailActions`、`openFollowDetail`、`closeFollowDetail` 按「列表页还画着」决定就地关还是重开。

### 馆藏网格

目录（`/` 与筛选态、`/trash`）、资料页作品区和详情页的接着看画的都是同一张网格（`frontend/src/react/catalog-grid/`）。作品卡是 `components/media-card.tsx`，Mix 卡用播放列表页那张 `components/mix-card.tsx`。三种取数按 `mode` 分：

- `catalog` 登记在 `CATALOG_ROUTES`，由路由树画进 `#grid`。表按页面分键：目录各路径与回收站都用 `/` 打开，槽里的路径说的是画着哪一页、不是地址；`CATALOG_PATHS` 只进 `<Routes>` 的声明，不进 `isManagedPath`。筛选态是壳的 `state`，打开时连同版式、选择态、助手与动作整份交进来；之后壳的 `paintGridPage` 看 `#grid` 的 `managedEntry`：画着就经 `updateManagedRoute` 推新筛选与 `revision`，查询换键重取、不重挂；首屏还在途就重开一次。冷启动经 `place` 交 `revealRoutedPage`，骨架淡出与整页同批交接。读数行 `#count` 的结构归壳，读数那一格、`#loadSentinel` 的自动续页、Mix 落位、竖屏带与分卷／版次折叠都在页面里。判据钉在 `test/react/catalog-routes.test.tsx`。
- 垃圾文件队列（`/junk-files`，`frontend/src/react/junk-queue/`）不是这张网格，是 `CATALOG_ROUTES` 的另一页，同样画进 `#grid`：打开前不取数，画上就铺自己那份骨架；换分类、换视图与处置后重读经 `updateManagedRoute` 推进来。它与目录网格换页时先由 `clearCatalogGrid` 收，计数行里归它的那一格随之同步撤掉，壳再往 `#count` 铺骨架。
- `entity` 由资料页正文 `entity-body`（`frontend/src/react/entity-body/`）直接渲染在它的作品视图里，不另挂岛：第一页随资料页首屏一起取来，续页由资料页按查询键取。同一块的另两个视图是名册（索引页的 `PeopleGrid`）与照片墙（样张分段在前、本地图片在后；直接打开 React 灯箱（`photo-lightbox/`），定位源文件调 `actions.revealSource`）。
- `items` 挂在 `#nrow` 上：壳手上已有那一批，岛只画卡。
- 版式、选中态与快进秒数经 `updateManagedRoute`（资料页作品区经 `pushEntityPage`）推进来：换版式只重画，已载入的分页原样保留。`selected` 每次推一个新的 `Set`。
- 卡上的悬停预览、封面取景与图片微光都在 `frontend/src/card-art/`：卡片直接调 `wireHover`／`releaseHover`（状态写在卡的 `data-previewing`／`data-longhover` 上）与 `relayoutCovers`，微光由 `installCardArt` 装的监听按 `PENDING_IMAGES` 认 `[data-media-art]>img`；壳只经 `configureHoverPreview` 告诉悬停预览多选态、打码与延迟。卡片的结构钩子全是 `data-media-*`；悬停预览插进封面格的 `video.hv`、`img.ui-hvframes` 与封套 `img.poster` 用自己的类名；`.ui-hvframes` 的样式在同目录的 `card-art.css`，另两样在 `12-cards.css`。
- 离场：去目录、回收站与垃圾文件以外的页面时由 `clearCatalogGrid` 收 `#grid` 那一页（`claimSurface` 只收 `#stats` 与 `#index`）；资料页正文随资料页由 `releaseManagedRoute` 收起；接着看是作品详情里的子组件，随舞台的内容一起卸。
- 屏外卡用 `content-visibility` 跳过封面与元信息区的渲染，不做虚拟列表。
- 单卡写操作都由用户点击触发：稍后看走 `actions.watchLater`，回收站卡的还原走 `actions.resourceOperation`，做完给撤销；彻底删除只在批量条上，先过 `confirmModal` 的危险档。

### 舞台与播放器

作品详情与关注详情都开在常驻组件 `stage` 里（`frontend/src/react/stage/`），由路由树画。宿主 `div[data-stage-host]` 是 body 的直接子元素、整页只有一个、换详情不换，不包 `.peach-react`；这一面拥有 `dialog#stage`、进出场、骨架、关闭键 `#closeStage` 与小窗 `#miniplayer`，两座详情是它的子组件，与页面共用同一份 Query 缓存。

- Application 使用命令式入口：`loadStage(host)` 第一次打开详情时连接常驻组件，`configureStage` 接上播放器，再经 `openResidentSurface('stage', …)` 建宿主、在画出小窗节点的同一个任务里挂到 body 末尾，画上之后才交出句柄；此后 `stageApi()` 同步可取，契约在 `stage/stage-api.ts`。来处（`detailReturnPath`、`followDetailReturnPath`、`detailOriginAnchor`）、地址与顶栏上下文由应用域维护。
- 句柄写本模块的 store 再 `flushSync` 通知：`open`（含原地换条）、`update`、`dispose` 里每一次绘制都在返回之前画完，骨架量尺寸、`showModal`、标题揭示与焦点交给关闭键读到的是刚画好的结构。两座详情画出来时报给壳的 `present` 排到微任务里：壳收到后画侧栏与顶栏，那几座常驻组件的句柄也 `flushSync`，在路由树的提交阶段里画不出来；那一条已经换走或舞台已经收起就不报。
- 舞台抛错时只卸组件：浮窗与小窗节点跟着消失，宿主与 body 上的 `data-detail-open` 留着，之后句柄各成员照调不抛，空到刷新为止。
- 关掉详情 push 来处：点进来的是点卡那一页；同一个队列里换条不变，播放列表关掉回列表页并重读；后退前进进来的取条目记的背景（关注详情连筛选一起保住）；刷新、新标签页与深链落在详情上不读条目，作品详情下面补画目录网格、关掉回 `/`，关注详情关掉回 `/follow`。
- 焦点：骨架期间焦点停在 dialog 本身、不画焦点环，内容到了交给关闭键。Escape 先关最里层（右键菜单、标签搜索等弹层先吃掉），没人拦才关舞台。
- 播放器在 `frontend/src/player/`，用 vendored 的 Video.js。入口 `mountPlayer(video, options)` 把媒体框里的 `<video>` 换成 Video.js 并返回拆除函数；详情只画媒体框，挂载由舞台的 `attachStagePlayer` 做。
- 小窗与舞台共用同一个播放器实例：离开详情时正在放的那一个搬进小窗，展开回同一条时认领回来，不重建。显式关闭、暂停着、设置里关了小窗、换到别的条目时随舞台拆掉。交接判据钉在 `test/stage-player.test.ts`，真 Video.js 的行为在 `e2e/stage.test.ts`。

### 侧栏与标签抽屉

左侧抽屉 `#drawer` 里滚动的那一层（`#drawerScroll`）是常驻组件 `sidebar`（`frontend/src/react/sidebar/`），由路由树画：导航那一列、导航上那块滑动玻璃（`use-view-glide.ts`，经 portal 画在 `#drawer` 上），以及按语境出现的筛选分组。抽屉本身、它的开合与遮罩、底栏三枚键、品牌与开合键归壳。

- 壳在启动时写一份骨架（`sidebar-skeleton.ts`，与组件画的导航同一份顺序与按下态），随后 `loadSidebar(sidebarHost())` 经 `openResidentSurface('sidebar', #drawerScroll, place)` 登记进常驻表，宿主就是 `#drawerScroll` 本身，不包 `.peach-react`。`place` 在画首帧的同一个任务里清掉骨架；画上之后先调 `attached`（壳把品牌与开合键挪进标题行的空槽），再交出句柄，此后 `sidebarApi()` 同步可取，契约在 `sidebar/sidebar-api.ts`。覆盖式滚动条由壳挂在 `#drawerScroll` 上：轨道与边缘渐隐挂在 `#drawer` 里，滚动层本身只多几个属性、样式变量与监听，子节点全归组件。
- 内容由壳推：`paintSidebar(patch)` 合并 `content`、`filters`、`latest` 后调 `render`。目录与资料页的聚合在 `buildBars` 里换成 `{kind:'catalog'}`，关注页与关注详情的内容标签由 `renderFollowDrawer` 推 `{kind:'follow'}`；就地改筛选时 `applyFilterStateInPlace` 只推 `filters`，`refreshFacetCounts` 只推 `latest`。点下去的动作回到壳的 `navTo`、`commitContextFilter` 与关注页的筛选。`render` 与 `navChanged` 写组件模块里的 store 再 `flushSync`，返回时已经画好。
- 导航顺序读 `appSettings` 这一份 store 的 `sidebarOrder`：拖动排序先落 store 再写 `/api/settings`，设置面板改顺序也写同一份 store，侧栏按通知当场重排。按下态换了由壳的 `paintNav` 调 `navChanged`：它跑在 `route()` 的同步段里，玻璃拿到的是旧位置到新位置。
- 标题行 `[data-sidebar-head]` 是组件画的空槽，品牌与开合键是壳挪进去的节点；组件不往这个槽里画子节点，重画不碰它们。
- 样式在 `sidebar/sidebar.css`，只认 `data-sidebar-*`；组件里不写 className。行为在 `test/react/sidebar.test.tsx`，量布局的玻璃滑动、拖动、各页计数与窄屏开合在 `e2e/sidebar.test.ts`；当前项玻璃、标题行间距、时长拉条与窄屏遮罩的外观在 `e2e/design-detail.test.ts` 读计算值。

### 客户端导航

React Router 以 Declarative 模式接管历史（`frontend/src/react/router/`）。管理区十一页（统计、口味、复核、数据管理、重复文件、高清版、来源与凭证、配置、活动、关注管理、诊断）、五张索引页与五类资料页由路由树按匹配打开，`/resource-sync` 与 `/configuration#libraryProcessing` 由元素用 replace 改写到数据管理页；目录六条路径、播放列表页与关注页同样由路由树按匹配打开；作品详情、四种队列与关注详情由覆盖组按匹配送进舞台，沉浸由页面组的 `/immerse` 元素打开。壳没有路由表。

- 历史只有一份：`@peach/history` 直接解析到共享源码，随主包发出，壳的 `route()` 经 `shellNavigate` 写地址，`<Router>` 的 `navigator` 也是它。路由树挂在一个不进文档的容器上，管理区那一页与播放列表页、关注页经 portal 画进 `#stats`，索引页与资料页画进 `#index`，目录网格与垃圾队列画进 `#grid`。
- 元素打开那一屏的动作排在提交阶段之后的微任务里：侧栏等常驻组件的句柄内部用 `flushSync` 当场画完，同一棵根在提交阶段里不会同步刷新。页面组的 `<Routes>` 里每条路径都挂着元素，`/immerse` 也在这一组；它按条目 `usr.backgroundLocation` 匹配，详情压在哪一页上就还匹配那一页，启动那一条不读背景。覆盖组按真实地址匹配 `OVERLAY_PATHS`（详情、四种队列、关注详情），元素把那一条详情送进舞台常驻组件，按派发序号挂 key。两组都不会报没有路由。
- 页面的宿主跟地址：元素挂上经 `surfaceChanged` 让壳收起别的面，再由元素铺这一页的骨架，然后 `openManagedRoute(path, open, {container, isCurrent, place})`。覆盖元素离开时，去处不是覆盖地址才收舞台。它领一个代次、先取首屏，取齐后在同一个任务里清掉骨架、放进 `.peach-react` 宿主（给了 `place` 就由它把壳排的框架换进容器、交出宿主），宿主用 `flushSync` 当场画完，骨架与正文之间没有空白帧；同一路径再打开就是新代次，页面重挂重取。三个容器各记一条、互不相收，`releaseManagedRoute` 逐个点名容器：`claimSurface` 收 `#stats` 与 `#index`，`showHomeSurfaces` 只收 `#index` 那一条，`#grid` 只由 `clearCatalogGrid` 收，资料页压在管理页上时管理页藏着照常活；详情舞台推 `/item/:id` 不经过它们，页面留在舞台下面。打开之后壳的开关（选择键、资料页换筛选与版式）经 `updateManagedRoute(container, patch)` 合进画着的那一页：代次不变，不重挂、不重取，照常排进下一次渲染。
- `open` 只带那一次才算得出的值（地址上的分类与页签、只读状态、引导标记、配置页页签），由元素自算；回执与换到还归壳的那几屏走壳交给 `configureRouter(actions)` 的 `ShellActions`，经 Context 下发。站内跳转交 `navigate`（壳那边是 `actions.navigate`，同样不认领），派发后由对应元素按页面打开序号打开。壳要重开画着的索引页或资料页（批量操作后重取、筛选回退、点开的正是画着的那一位）时把 `@peach/shell` 的 `pageOpens` 计数加一，元素整页重开；同一微任务里的换页与重开合成一次。元素从 effect 里写地址一律排进微任务并带存活守卫：路由根同步提交，提交阶段里同步写地址会出 flushSync 告警。配置页页签先交给壳再换地址，不进地址栏。判据钉在 `test/react/managed-routes.test.tsx`。
- 每次历史变化领一个 `seq`。页面元素经 `useOpenEpoch()` 读页面打开序号、按它挂 key：壳的 `shellNavigate` 写地址时当场认领，页面不重开；后退前进、React 子树里的 `navigate` 与壳的 `actions.navigate` 领新代次，页面重挂重取。覆盖元素按 `seq` 挂 key，接手该次详情请求；详情内改写地址时认领序号，不重复打开。队列打开意图经 `queueOpens` 交给元素，取齐数据后才写入实际队列地址。`actions.navigate` 照 `route()` 写好标题，使用 `shellNavigate(path, {claim: false})`。地址不变的 `popstate` 也领新序号与新代次。
- 路由根同步提交：`PeachRouter` 在历史变化的同一调用里 `flushSync` 换上新地址，`shellNavigate` 与后退前进返回时两组 `<Routes>` 已经换好匹配、上一页的元素已经卸掉。派发不在这次提交里，仍排在其后的微任务。判据钉在 `test/react/router-sync.test.tsx`。
- 管理区十一条路径的标题、侧栏身份（`section`）与顶栏「换一批」的行为（`refresh`）登记在 `@peach/history` 的 `ROUTE_META`：Application 启动与路由元素都直接读这份源码。壳的读者经 `routeMeta(path)` 读它；同一身份按登记顺序取第一条，数据管理排在重复文件与来源和凭证前面。
- 启动：壳完成来源、页面结构与运行态准备后推进 `pageOpens`，路由树按当前地址匹配首屏；启动条目不读取历史背景。Router 挂载前发生的后退前进，在挂载时按当前地址匹配一次。判据钉在 `test/react/router.test.tsx`。

队列首屏取齐前，打开意图由源导航序号持有取消权。新的非覆盖导航（包括同地址 POP）撤回意图、清空待写地址并作废舞台代次；意图微任务、舞台装载和响应回调都检查取消状态。成功首屏在写队列地址前解除取消权，覆盖详情之间换条不会关闭新舞台。

### 产物缓存

产物使用固定文件名，由 `web/index.html` 与独立页面薄壳引用。服务端对 `/dist/` 与 `/app.css` 返回 `Cache-Control: no-cache` 和 mtime＋字节数的 ETag；浏览器每次重新验证，内容未变时返回 304。`index.html` 使用 `no-store`，确保入口能看到当前产物。

## 样式表分区

样式表按界面分区拆在 `web/css/` 下，`/app.css` 把它们按文件名顺序拼成一份交付
（`src/peach/routes_pages.py` 的 `stylesheet_response()`）。拆分只为让两处改动落在不同
文件上：一整份两千七百行的样式表，两个分支各改一处几乎必然撞在一起。页面仍然只取
一份 `/app.css`：不给首屏加二十来个阻塞请求，层叠顺序也不必写进 `index.html`。

两条规则：

- **两位数前缀就是层叠顺序**，`sorted()` 出来的顺序即生效顺序。新增分区要同时改
  `tests/test_web_ui.py` 里 `StylesheetPartitionTests.PARTITIONS`：插在哪一档决定谁覆盖
  谁，那是判断，不该由 glob 顺手发现。
- **切口只许落在花括号深度 0、注释之外**。规则或 `@media` 被切成两半时拼起来仍然完全
  正确，只有单独看每一份才会发现，所以每份分区自己的花括号和注释必须闭合。

| 分区 | 覆盖 |
| --- | --- |
| `01-base.css` | 主题变量、色板、字号、Geist 基元、滚动条 |
| `02-topbar.css` | 顶栏与顶部三层 |
| `03-filterbar.css` | 常驻筛选层、combo、页面提要 |
| `04-manage.css` | 统计页、播单、复核、元数据、数据管理 fieldset |
| `05-insights.css` | Analytics／Speed Insights，以及口味页骨架的板块与指标条 |
| `06-index.css` | 索引页首屏骨架、资料页名册格 |
| `07-entity.css` | 实体资料页头、外链、相关人物 |
| `08-photos.css` | 灯箱打开时的页面锁滚、共用折叠 |
| `09-skeleton.css` | Geist Skeleton 与各页骨架变体 |
| `11-identity.css` | 身份组、演员与系列链接、重复项、质量清单、复核对照 |
| `12-cards.css` | 壳自己画的卡片（垃圾文件、新作）、悬停预览层与密度 |
| `15-detail.css` | 壳画的源文件管理（定位与目录对账） |
| `16-settings.css` | 设置面板打开时的页面锁滚（面板归常驻组件 `settings-panel`） |
| `17-overlay.css` | Toast 与审查遮挡 |
| `18-chips.css` | 产地选择与详情里次要操作的标签按钮（侧栏筛选标签归常驻组件 `sidebar`） |
| `19-immersive.css` | 加载更多、空状态、选择条与批量条、窄屏总表（沉浸模式归常驻组件 `immerse`） |
| `21-online.css` | 关注页骨架与关注详情（列表归路由树画的关注页） |
| `22-followmanage.css` | 关注管理页 |

## 开发循环

改前端代码时怎么看到效果、怎么跑测试，以及提交前必须做什么。

```bash
npm --prefix frontend ci        # 首次或改了依赖之后
npm --prefix frontend run dev   # vite build --watch，改完存盘就重建 web/dist
npm --prefix frontend run build # 出一次正式产物，提交前必须跑
```

没有 dev server：`index.html` 归 Python 服务，页面照常从 Peach 自己的端口打开，
watch 模式只负责把产物写回 `web/dist/`。刷新页面就能看到改动。

测试与类型仍然只有一个入口：

```bash
& .\scripts\test.ps1 -Scope web   # Windows；含 tsc、vitest、产物与契约断言
./scripts/test.sh web             # macOS
```

vitest 转译时只剥掉类型、不做检查，所以 `web` 域另跑一遍 `npm --prefix frontend run typecheck`。
两者在本机没有 npm 或没装 `frontend/node_modules` 时**显式跳过**，不会让测试域变红；
CI（`GITHUB_ACTIONS=true`）里缺这些就判失败，由工作流负责装齐。「产物是否由当前源码构建出来」这一条本机验不了（不装 Node 就无法重建），
它的门槛在 CI 的 `web-bundle` job：`npm run build` 之后 `git diff --exit-code -- web/dist`。
**改了 `frontend/src` 就必须重新构建并把 `web/dist/` 一起提交**，否则 CI 会红。

同一个域里还有真浏览器冒烟 `tests/test_web_e2e.py`：它在临时 Peach 数据目录上生成 12 条合成演示库、
起回环 `peach serve --no-auth`，再按批调起 `node --test` 跑 `frontend/e2e` 下全部用例（调度见 [docs/TESTING.md](TESTING.md)）。冒烟用例在
`frontend/e2e/smoke.test.ts`，每条主路由在桌面与 390×844 下先等到目标页面主体出现（路由自己的标题，
加上内容区、索引条目或明确的空态），再断言：无页面异常与 `console.error`、无同源 4xx/5xx 与失败请求、
`aria-busy` 与 `data-skeleton` 会消失、无横向溢出、无越出视口的元素。主体一项不能省：页面完全没渲染时，
其余几条照样全部成立。新增路由要在 `ROUTES` 里写明它的主体。
浏览器取本机 Google Chrome（`PEACH_E2E_CHROME` 可指定），短片由 ffmpeg 编码；缺 npm、
`playwright-core`、ffmpeg 或 Chrome 时本机显式跳过，CI 里判失败。浏览器 CI 由 `web-e2e` 矩阵分片，
分片与平台范围以 [测试与依赖](TESTING.md) 和工作流为准。界面验收里发现的同类问题，
先在这里补一条用例再修。

设计决定另有 `frontend/e2e/design-*.test.ts`（按页面区域分文件，共用 `design-fixture.ts`），读 `getComputedStyle` 断言用户定过的外观：React 输入框不带旧焦点环、
React 子树读到 BoardUI 的 token 原值、持久警示是状态色块、一张卡底下只有写入那一颗是主按钮。页面迁到 React 时，旧的源码字符串断言按 ADR-0031
分三类再删：设计决定进这里或 lint，行为进 vitest，布局与运行期进冒烟。

导航按用途定位：页面分区在正文上方；配置、复核与设置弹窗的分类目录在桌面左侧，窄屏移到正文上方；图表维度与显示方式用内容附近的分段控件。目录选中态使用填充，未选中项悬停只提亮文字。
配置保存使用主按钮，检查、添加、刷新与取消使用次按钮，移除使用删除按钮。安装教程与系统诊断入口使用单行设置；活动后继明细可展开和收起，进行中及失败的后继默认展开。

`npm --prefix frontend run lint` 检查 `src/`、`test/`、`e2e/` 的 correctness 规则、严格相等比较和累积展开；`== null` 保留同时匹配 null 与 undefined 的语义。设计系统规则只作用于 `src/react/`，排除 BoardUI 与 EvilCharts 的上游副本。`web` 域与 CI 都跑。`no-restyle` 报在
BoardUI 组件上的间距或外观，处理办法是在组件外面套一层普通元素，不给规则加例外。
`src/react/boardui/` 只加不改，`UPSTREAM.sha256` 记着复制时每个文件的哈希，由 `tests/test_frontend_build.py` 比对。

## 界面标注

指认界面上的某一块时，用 [Agentation](https://agentation.com) 在页面上点中元素、写下意见，
复制出 markdown 直接贴给智能体，不必再用话描述「侧栏那块光斑」。复制内容的多少由工具栏
Settings 里的 Output Detail 决定，设一次就存在这台浏览器里：默认 Standard 只有选择器；
Detailed 加上 class 与附近文字；Forensic 再加上带 id 的完整 DOM 路径（如
`main#main > div#libraryProcessingNotice > …`）和计算样式，交给智能体时用这一档。

```powershell
npm --prefix frontend run build:agentation   # 在要用它的那份检出里构建一次
```

然后在页面地址后加 `?agentation=on`（关闭用 `?agentation=off`），开关存在这台设备的
`localStorage` 里，之后每次开页都会带上右下角的工具栏。

- 产物写到 `build/agentation/peach-agentation.js`，由 `/dev/agentation.js` 提供，口令同 `/dist/`。
  它不进 Git、不进独立包：Agentation 是 PolyForm Shield 许可，只许自用，不随 Peach 分发，
  所以不放在会被打包整个带走的 `web/` 下。没构建过的检出里这条路由是 404，`Application` 静默跳过。
- 产物是本机构建的：换检出、升级依赖之后重跑一次，刷新页面即生效。
- 标注里拿不到源文件路径：它靠 React 的 `_debugSource`，React 19 已移除这个字段；组件名也被
  `peach-app.js` 的压缩改掉了。要在 Tailwind 类名之外给智能体更稳的抓手，给区块加
  `data-component`（Agentation 默认采集的属性之一）。
- Agentation 自己的全局快捷键已关闭，避免与 Peach 的 Esc、方向键冲突，只用工具栏按钮操作。
- 标注工具挂在自己的容器里；适配入口为它的 Shadow DOM 表单字段补齐名称。
- 标注同步到本机 `agentation-mcp`（`http://localhost:4747`），智能体用它的 MCP 工具直接读、
  回复和标记已处理，不必复制粘贴。服务由注册了它的 Claude Code 会话拉起：
  `claude mcp add agentation -- npx -y agentation-mcp server`，注册后新开的会话才有这组工具。
  服务没起时标注照存浏览器里，起来后补传；它只接受回环来源的请求，局域网其他设备连不上。
  浏览器若询问是否允许页面访问本机上的应用和服务，选允许。

## 挂载契约

页面、页面里的页面内组件与已收进常驻表的常驻组件只由路由树画（ADR-0031）。壳决定什么时候开、什么时候收，props 由壳算好推进来；
React 路由树（`RouterRoot`）是唯一的一棵根，按 `frontend/src/history/managed.ts` 的登记把各面 portal 进壳的容器。
下面每条都是为了不出现两段等待态或离场后还在轮询的页面。

```js
// 路由元素直接引用共享源码：import { openManagedRoute } from '../../../history/managed'
await openManagedRoute('/playlists', props, {container: $('#stats'), isCurrent: () => surfaceCurrent(surface)});
await openManagedRoute('search', props, {container: $('#searchMenu'), isCurrent: () => true});
```

- 登记键是「面」：页面用路径（`/stats`、`/performers/*`、`/`），页面内组件用名字（`catalog-filter`、`feed-new`、
  `library-processing`、`search`），常驻组件也用名字（`batch-dock`、`glow-picker`、`manage-header`、`immerse`、
  `settings-panel`、`sidebar`、`stage`），三者不重叠。路由树按键查 `managed-routes.tsx` 里的同一组表，每条是
  `{prefetch, page}`；页面内组件与常驻组件不进 `<Routes>`，也不进 `ROUTED_PATHS`。
- 常驻组件是不跟某一页走的那几座，登记在常驻表 `RESIDENT_ROUTES` 里，由 `application-residents.ts` 的 `loadXxx(host)` 经
  `openResidentSurface(name, container, place?)` 打开一次（沉浸模式、设置面板与舞台在第一次打开时，其余几座在壳
  启动时）：没有首屏取数，一直算当前页，宿主就是那个常驻节点本身（`[data-batch-dock]`、`#boardGlowMenu`、
  `[data-manage-header]`、`[data-immerse-host]`、`[data-settings-host]`、`#drawerScroll`、`[data-stage-host]`），组件直接画成它的
  子节点，DOM 和各自建根时一样，不包 `.peach-react`。壳照旧经命令式
  句柄说话：组件订阅自己模块里的 store，句柄写 store 再 `flushSync` 通知，返回时已经画好；`loadXxx` 等这一面
  画上才交出句柄。宿主里先有壳的启动骨架时（管理区页头、侧栏），`openResidentSurface` 的 `place` 在画首帧的
  同一个任务里清掉骨架，句柄交出之前骨架上的点击归壳；宿主不在壳的页面里时（沉浸模式、舞台），`loadImmerse` 与
  `loadStage` 新建 `[data-immerse-host]` 与 `[data-stage-host]`，`place` 在画首帧（沉浸是藏着的外框，舞台是藏着的
  小窗）的同一个任务里把它挂到 body 末尾。沉浸的句柄
  `open(startId)` 里骨架、列表与播放器那几次绘制都在返回前画完；方向键、改窗口大小与离开页面的监听挂在模块上，
  不随组件卸掉。设置面板在第一次按齿轮时才装载：`loadSettingsPanel` 建一枚 `[data-settings-host]`，`place` 把它
  放进 `document.body` 末尾，路由树在同一个任务里画出收着的面板，之后开合只换 `hidden`；`open(section)` 写 store
  当场画完，锁滚、取数与焦点紧跟在后面，`isOpen()` 读的是同一份 store。常驻组件从不收，只有错误边界会卸它的组件，
  宿主始终留在文档里。
- `openManagedRoute(key, props, options)` 是 async 且**取完数才画**。壳已经铺了骨架，页面若先画一个空容器
  再自己转圈，同一次进入就会出现两段等待态。它先收起同一容器里的上一面，`prefetch(props, signal)` 把首屏
  写进共用的 Query 缓存，取齐后在同一个任务里换掉骨架、放进 `.peach-react` 宿主（或 `options.place` 排好的
  框架），路由树用 `flushSync` 当场画完。路由树尚未接上时，打开先等待路由树连接再取数。搜索下拉与首页筛选条在 Application 启动阶段登记，页面与常驻组件的代码均已包含在主包内。
- `options.isCurrent` 是换页判据。壳用「代」而不是 `AbortSignal` 判当前页（`claimSurface`／`surfaceCurrent`），
  取数期间用户走开时靠这个谓词决定不画。一打开就不会走开的页面内组件（搜索下拉、首页筛选条）传 `() => true`；
  常驻组件的判据由 `openResidentSurface` 定成恒真。
- `managedTaken(el)` 回答这个容器归没归路由树：已经画着，或首屏还在取。壳据此决定要不要再开一次，在途时
  再开会把那一趟中止、重取一遍。`managedEntry(el)` 只认已经画上的那一面。
- 壳手里的一项状态变了、页面又不该重挂时，用 `updateManagedRoute(el, patch)`：它把 `patch` 合进打开时的
  props，代次不变，页面就地重渲染。重挂会把页面里打了一半的字和滚动位置一起换掉。还没画上时是空操作。
- `releaseManagedRoute(el, …more)` 中止在途取数，卸掉页面并撤掉宿主；常驻组件的宿主归壳，只卸组件。还没画就
  收起时容器里是壳的骨架，那不属于路由树，原样留着。
- 离场有两道闸。第一道是壳的公共点：`claimSurface` 是所有页面共同经过的换页点，它收 `#stats` 与 `#index`，
  也在离开目录页时收处理横幅（`#libraryProcessingNotice`）；`showHomeSurfaces` 是索引页与资料页重画前的公共点，
  收 `#index` 那一页；`#grid` 那一页离开目录时由 `clearCatalogGrid` 收，首页新作行由 `clearHomeFeed` 收；搜索
  下拉壳从不收，常驻表里的面结构上就不收。多数页面的离场路径是直接 `innerHTML=`，页面被挤出文档却照样活着，所以收起必须由这几个
  公共点负责，而不是逐页判断。第二道是 `isCurrent`：取数落地时用户可能已经走开，这时不画。
- 抛错的那一面：每一面各套一层错误边界（`router.tsx` 的 `SurfaceBoundary`，按代次挂 key）。某一面渲染
  抛错时只空出那一面，`failManagedRoute` 撤掉它的登记与宿主，`managedTaken` 回 false、推补丁是空操作，壳
  下次打开就重开；首帧就抛错时 `openManagedRoute` 照样回 true，回来时登记已经撤了。错误经根的
  `onCaughtError` 交给 `reportError`，每次一条，边界自己不再报。根上不套边界：派发点与两组 `<Routes>`
  不随某一面卸掉。确定性的抛错每重开一次就再报一次；搜索下拉只在壳启动时打开，抛错后空到刷新为止。
  常驻组件抛错时只卸组件、不撤宿主，同样空到刷新为止：`loadXxx` 是缓存的 Promise，不会再开第二次；之后壳调句柄
  只写进没人订阅的 store，不画、不抛、不再上报，所以确定性的抛错只报一次。不在下一次推内容时自动重开，因为
  重开要等一次异步打开，那一次句柄就不再是同步画完。

共享助手 `LOC`、`fmtDur`、`fmtSize`、`emptyStateHtml`、`noteHtml`、`javTitleHtml` 与 `tagLabel` 直接来自 `core/`、`ui-kit/` 的唯一源码。`@peach/legacy/*` 是源码别名，不对应浏览器垫片或外置产物。应用动作（如 `srcBadge`、`openItem`）由各域控制器经有限 props/actions 接口交给组件；路由层的跨域动作由 `ShellActions` 下发。

两条跨层都成立的硬约束：

- 数据库元数据不得插值到 inline JavaScript 事件属性：真实厂牌名里的撇号会直接造成 Firefox 语法错误。
- 前端 API 包装必须先检查 HTTP 状态再返回 JSON：冲突只读时写端点返回 `409` 和错误 JSON，当成普通成功对象会清空选择并重载，用户只看到条目原样回来。批量处置和详情反馈必须保留当前选择并显示失败原因。

## 共享状态怎么写

判据只有一条：**这份数据有没有第二个读者**。

没有就用 hooks。展开、悬停、翻到第几页这些东西只属于一页，提上去只是把本来局部的
东西变成全局的。

有第二个读者就让两个读者读**同一个 `queryKey`**，不另建一份状态。全站只有
`src/query/client.ts` 那一个 `QueryClient`（`tests/test_frontend_build.py` 盯着），壳与
React 岛都从 `@peach/query` 取它；页面级 `prefetch` 或壳写进去的那一份，任何组件的
`useQuery` 都直接读得到，谁先谁后都是同一个数。
现成的例子是扫描与采集那趟后台任务：`/data-cleanup` 上的卡片（容器 `#libraryProcessing`）
要进度、结果和重试，目录页顶上那条横幅（容器 `#libraryProcessingNotice`）只要一句话和一个
去处。两个容器不相邻，各由遗留层自己的时机挂载，读的却是同一个 `LIBRARY_PROCESSING_KEY`：

```tsx
// src/react/library-processing/use-library-processing.ts —— 卡片与横幅都调它
const job = useQuery({
  queryKey: LIBRARY_PROCESSING_KEY,
  queryFn: ({ signal }) => fetchLibraryProcessing(signal),
  refetchInterval: (query) => pollInterval(query.state.data, watching),
});
```

两处同时在场时一个周期只发一趟请求：两个 observer 的定时器在每次查询更新后一起重排，
并发的 `fetch` 由 Query 自己合并。反过来各存一份状态的话，两边的轮询各走各的节律，卡片
说「已完成」、横幅还挂着进度。

数据管理页（`src/react/data-cleanup/`）顶上五张读数卡是同一条判据的例子：「高清版」只要
`total`，读的仍是 `/quality-goals` 整页那一把 `QUALITY_GOALS_KEY`；「重复文件」读
`/duplicates` 整页的 `DUPLICATES_KEY`。同一个数在两页上永远是同一份，谁先进哪一页都一样。
复核计数不读 `REVIEW_KEY`：那一把是整条队列，`?counts=1` 响应形状不同，是另一份资源。
五张卡各自一个 `useQuery`，一张取不到只写那一张「读取失败」。
**遗留层里的读者等它所在的页面迁过来再接**，不为它在产物上另开一个通知入口。

这一页下半截的卡片各是一趟后台任务或一次写入：链接检查与删除、资源同步的扫描与清理走
`useBackgroundJob`，整理的预览与执行、重复文件的批量保留走 `useMutation`。
写真实 ledger 的动作（资源同步清理、`/api/batch`、整理执行与回滚、链接删除）
都由用户点击触发、先过 `confirmModal`；资源同步清理永久删除失效记录与空文件夹，走危险档。
资源同步只在有来源配了根目录时出现，它的两份任务状态也只在那时进首屏预取；
直达 `#resource-sync`（`/resource-sync` 转过来的）时，它上面那几份懒取的读数也一并等齐，
免得滚到位之后又被撑下去。

端点和查询键由页面的数据模块统一提供（如 `src/react/quality-goals/quality-goals.ts`）。组件测试验证请求次数、请求体和缓存更新；跨组件共享由同一个 `QueryClient` 提供。

首屏要不要吃缓存看路由表：`/quality-goals` 是 `refresh:'reopen'`，刷新就是重新进这一页，
所以它的 `prefetch` 不给 `staleTime`，每次进来都重取。要按节律更新的页面写
`refetchInterval`（活动页 2 秒／10 秒），不另起 `setInterval`：轮询跟着组件走，
换页时壳在 `claimSurface` 卸根，它自己就停了。

统计页（`src/react/stats/`）是这条判据的另一端：整页只有 `STATS_KEY` 这一个键，读的是账本
此刻的样子，没有后台任务也不轮询。一屏里的四个读数和下面三个面板分开取的话，就会出现这一格
是新的、那一格是旧的。四张读数卡同时是页签（React Aria 的 `Tabs`），因为这一页没有别的主
动作，读数本身就是入口；环形库存图用 SVG 画，`pathLength={100}` 把一圈钉成 100，颜色只取
BoardUI 的 `chart-*` 档。点一个内容标签是「回目录并按它筛选」，整页换成目录仍归遗留壳，页面
只把标签键交回去（`onTag`）。

口味页（`src/react/taste/`）把「同一份真相换一个范围看」写进键里：`['taste', window]`，换范围
就是换键，上一份靠 `placeholderData: keepPreviousData` 留在屏幕上。范围是组件状态而不是 URL：
它不进路由表，壳只按 `/taste` 一条路由挂岛，刷新回到默认的「全部」。服务端的 `_get_taste` 自己
按 `taste:{window}` 缓存，演示库上一趟往返十几毫秒，所以这一页不设 `staleTime`。后台重算是另
一个键 `['taste','refresh']`，`running` 时两秒问一次、闲时十秒，终态按下面第 2 条的判据认。
雷达、名次条、活动热力和创作者桑基都是 React 组件，几何落在 SVG 属性上，颜色只取 BoardUI 的
`chart-*` 档。

## 迁移下一个页面

整页归 React（ADR-0031）。先挑一个**容器不与别人共用**的页面；写操作和后台任务的
写法已经定型，见下面第 2 条。

1. `frontend/src/react/<page>/<page>.ts`：端点常量、`queryKey`、数据类型和纯折算函数，
   外加一个 `prefetch<Page>(signal)`，用 `queryClient.fetchQuery` 包住 `src/api.ts` 的
   `apiGet`，信号透到真正的 `fetch` 上。同一份真相只用一个 `queryKey`：一屏里的几段要是
   分开取，就会出现这一段是新的、那一段是旧的。节律不同的两份才分键：来源和凭证页的
   来源列表由用户改，抓封面的任务状态由后台推进，合成一个键的话每两秒的一轮轮询都会把
   用户正在填的那张卡重画一遍。
2. `frontend/src/react/<page>/<page>-page.tsx`：组件用 `useQuery` 读同一个 `queryKey`，
   要轮询就写 `refetchInterval`，间隔按上一次拿到的内容算，不另起 `setInterval`。
   写操作是 `useMutation`，不进 Query 的缓存节律：成功后用 `setQueryData` 把服务端回的
   那一条换进列表，而不是把整页重取一遍，因为用户可能正在填同一屏的另一张卡；失败只在卡内
   留一句原因，缓存里的上一份不动，刚填的内容也不清。同一张卡上互斥的动作共用一个
   `isPending`，进另一个动作前 `reset()` 掉上一个的结果，屏幕上不会同时挂着两次的结论。
   跟后台任务一律用 `frontend/src/react/background-job.ts` 的 `useBackgroundJob`，不在页面里
   自己拼 `useQuery` + `useMutation` + effect：按 `running` 开关轮询、首屏读到的旧终态
   不冒充新结果、启动时先换进这一趟的快照再重读，这三条时序在 hook 里定死，由
   `frontend/test/react/background-job.test.tsx` 拖住重读逐条验。页面只交任务键、读取与启动
   函数和 `onFinish`；缓存的是整张卡时再交 `jobOf` / `withJob`。
3. `frontend/src/react/router/managed-routes.tsx`：在对应的表里登记 `{prefetch, page}`，页面用路径、
   页面内组件用名字；打开时交进来的 props 类型写进 `router/shell-actions.ts` 里那张表，壳经
   `openManagedRoute` 打开（见 [挂载契约](#挂载契约)）。
4. 外观按 BoardUI：注册表里有的条目逐字复制进 `src/react/boardui/`，哈希记进
   `ORIGIN.md` 与 `UPSTREAM.sha256`；注册表里没有的（分区标题、空态、进度、说明条）
   用 `src/react/components/` 下 Peach 自己的组合件，第二个页面要用就搬进那里，不复制一份。
   `auto-fill` 网格、固定像素的封面这类工具类里没有的档位，在 `styles.css` 里加
   `@theme` 或 `@utility`，类名照常由 Tailwind 生成；lint 不收任意值。
   卡面取 `components/card.tsx` 的 `cardClass()`，不自己拼描边卡：Board 的卡一律是
   `--ground` 填充面，各页自拼 `rounded-2xl border border-separator-border` 的结果是同一种
   卡片长出十几种空心壳。读数卡与分段控件同理，走 `stat-card.tsx`、`segmented.tsx`。
5. `frontend/test/react/<page>.test.tsx`：假 fetch 加 `test/react/render.tsx` 的挂载助手，
   断言结构、请求次数、轮询节律和失败时留下什么，用例之间 `queryClient.clear()`。
   有写操作就再断言交上去的请求体、成功后页面上不再留着秘密输入、失败后输入原样还在；
   有后台任务就用假时钟推到终态，看回执只发一次、卸载之后不再问。
   外观决定进对应区域的 `frontend/e2e/design-*.test.ts`：`page.route` 造出真实数据里凑不齐的状态，
   断言读 `getComputedStyle`。
6. 页面接入 `react/router/`，所需助手复用 `core/`、`ui-kit/` 或域内 typed controller。只有应用动作通过 props/actions 传入；通用块用 `components/` 的组合件。清理只服务该页正文的 `web/css/` 与 `web/board.css` 规则，保留骨架及其他读者仍需的规则。
7. 路由、菜单入口和骨架由组件与浏览器测试验证。`tests/test_web_ui.py` 只保留通用样式规范、CSS 分区层叠与隐私边界，不按页面逐段比对实现源码；已有行为验证的文本断言直接清退。
8. 跑 `& .\scripts\test.ps1 -Scope web`（含 tsc、lint、vitest 与真浏览器冒烟），
   再 `npm --prefix frontend run build` 并把 `web/dist/` 一起提交。

应用骨架与全局外壳沿用 `web/css/` 下仍被读取的分区。主界面组件与共享控件样式由 `react/styles.css` 汇入 `peach-app.css`。
只由 `frontend/src` 产出标记的规则（含 `ui-kit` 模板与骨架拼的 HTML）住在组件旁的 css，类名带 `ui-` 前缀，经 `react/styles.css` 引入、随 `peach-app.css` 加载；壳也拼的类和尚未搬的类仍在 `web/css/` 与 `web/board.css`。原地换态的动效类在 `ui-kit/motion.css`。播放器皮肤与字幕外观由 `player/player.css`、`player/subtitles.css` 随主界面样式加载；Video.js 10 的控件模块由 `player/videojs.ts` 按需加载。
React 子树的样式是 Tailwind v4 加 BoardUI 主题，产物 `peach-app.css`；它与旧样式表同处一页的
三条约束（工具类不分层、只扫描 `src/react/`、Preflight 限定在 `.peach-react` 里）写在
`frontend/src/react/styles.css` 开头，逐字复制与没有复制的上游文件见 `frontend/src/react/boardui/ORIGIN.md`。
Preflight 给每张 img `max-width:100%`；`card-art` 拼的人脸头像由 `avatarFrame` 在图上内联撤掉这条，
岛里放这种头像的容器不必再各写 `max-width:none`。封面与带脸框的头像取景完才显示（`09-skeleton.css`）。
`.oxlintrc.json` 里的例外也在那儿定：`ui-configpage`、`ui-configgroup`、`ui-board-local-nav` 是配置页自己的类名，
骨架（`configuration-skeleton.ts`）与 React 页输出同一组类名，样式在组件旁的 `configuration-page.css` 与 `board-controls.css`，这条规则不读这些样式表；`swiper`、`swiper-wrapper`、`swiper-slide`、
`swiper-zoom-container` 是 Swiper 核心 API 认的结构类名（图片灯箱），不写它就找不到轮播的
容器与每一张；`mono` 是 `01-base.css` 的等宽数字字体栈，和 Tailwind 的 `font-mono` 不是同一组字体；
`javedition` 与色调（`censored` 等）是目录卡片也用的版次徽章；`chip` 是 `18-chips.css` 的标签按钮，壳的产地选择用它，作品详情里脱盘与在线说明块的按钮沿用它（侧栏的筛选键归侧栏组件，不用这个类）；`geist-button`、`primary` 是
舞台模态里各处按钮共用的遗留按钮，设置面板的「添加」「恢复默认」也沿用；`popmenu` 是遗留浮层菜单的盒子，
`presentMenu`／`dismissMenu` 的开合动效按它起，设置面板的色板弹层与侧栏「添加」菜单都是它；`geist-input`
是 `01-base.css` 的输入框，设置面板的数值框沿用。这几个在它们的主人（卡片、浮层菜单等）归 React 时一起收回。
舞台、两座详情共用的格子与队列、小窗的样式在 `frontend/src/react/stage/stage.css`，沉浸模式在
`frontend/src/react/immerse/immerse.css`，播放器画面框、
统计角标与右键菜单在 `frontend/src/player/player.css`，都由 `styles.css` 引入、只认 `data-*`；
`shadow-dropdown` 是 BoardUI 主题里的 `--shadow-*`，
`no-raw-colors` 只认 `--color-*`，把它当成了未声明的颜色。

## 依赖清单

`frontend/package.json` 和根 `package.json` 是两份，各管一件事：根清单只登记手工
vendor 到 `web/vendor/` 的四个包（video.js、swiper、lucide-static、healthicons），
构建依赖不许混进去（`tests/test_dependency_policy.py` 卡着那份清单）。
两份都精确钉版本，lockfile 进 Git。

| 依赖 | 为什么需要它 |
| --- | --- |
| `vite` | 构建入口。库模式出单个 ES module，`external` + `output.paths` 把遗留模块留在外面 |
| `typescript` | 类型即契约：注册表、props 与端点响应都靠它在编译期拦住漂移 |
| `vitest` | 前端测试运行器。与 Vite 共用同一份配置解析，不必再维护第二套转译 |
| `happy-dom` | vitest 的 DOM 环境。断言的是真实 DOM 结构，比 jsdom 轻且启动快 |
| `playwright-core` | `frontend/e2e/` 的浏览器驱动，只驱动本机 Chrome、不下载浏览器。happy-dom 没有布局，横向溢出、等待态卡住这类事实只有真浏览器测得出；不用 `@playwright/test`，用例跑在 `node:test` 上，与 docu.md（`markdown-viewer/markdown-viewer-extension` 的 `test/helpers/browser-render-harness.ts`）同一做法 |
| `oxlint`、`@shadcn/lint` | `npm run lint`：Oxlint 原生规则覆盖源码与测试，六条 shadcn 设计规则检查自有 React 源码。使用 TypeScript 与 Oxc 插件；规则集见 `.oxlintrc.json`。`eslint` 作为 `@shadcn/lint` 的 peer 安装，不作为检查入口 |
| `react`、`react-dom` | 前端唯一的渲染层。BoardUI 源码是 React 组件，交互建在 React Aria 上；不经兼容层运行它（ADR-0031）。`react-dom` 的 `flushSync` 还负责配置页那一帧：壳挂完紧接着按 `#peachProxy` 滚过去 |
| `react-aria-components` | BoardUI 输入框、勾选框、开关、下拉与弹出面板的交互和无障碍语义：标签关联、键盘操作、焦点进出、`aria-invalid` |
| `react-aria` | 只用 `UNSAFE_PortalProvider`：把 Popover 与下拉列表挂进 `body` 末尾同样带 `.peach-react` 的容器，弹层读到与页面内一致的 token 与 Preflight |
| `@tanstack/react-query` | React 页面的取数与缓存：页面级 `prefetch` 与组件里的 `useQuery` 共用一份缓存，「取完数才画」不必把首屏数据当 props 串一路；轮询写成 `refetchInterval`，卸载时跟着组件一起停 |
| `@tanstack/query-core` | `QueryClient` 本体。Application 与 React 页面直接引用同一客户端源码，运行时只有一份；版本与 `@tanstack/react-query` 同步固定 |
| `react-router` | Declarative 客户端导航：Router、Routes 与共享浏览器历史包含在主包内，地址经唯一历史对象派发。`@remix-run/route-pattern`、`cookie-es` 是它的依赖 |
| `@tanstack/react-table` | v9 的 `useTable`、`tableFeatures` 与列 helper 驱动关注列表、JAV 订阅源和创作者别名表格，只注册各表需要的分页、行选择与排序状态。行身份是业务 ID（`getRowId`）；关注列表跨页跨视图共用勾选集合，排序作用在**全集**上，分页只切最后一步；别名表按平台别名和规范创作者键标识行 |
| `tailwind-merge` | BoardUI 的 `cx()` 合并类名时去掉互相冲突的工具类 |
| `@remixicon/react` | BoardUI 组件内置的图标 |
| `tailwindcss`、`@tailwindcss/vite` | 按 `src/react/` 里实际用到的类名生成 `peach-app.css` |
| `@types/react`、`@types/react-dom` | React 子树的类型检查 |
| `agentation` | 本机开发用的界面标注工具栏，单独构建、不进产物与独立包（见「界面标注」） |

主界面由 `vite.config.ts` 从 `src/react/bootstrap.tsx` 构建为 `peach-app.js` 与 `peach-app.css`。Application、路由树、常驻组件与共享状态同属一份源码图，`@peach/*` 直接解析到源码，不外置 React、Query、历史、shell 或 UI 模块。主界面不做代码分割；开发取证工具 `/dev/agentation.js` 是独立的按需外部模块。

`npm run build` 先构建主界面并清空 `web/dist/`，再用 `vite.pages.config.ts` 追加独立页面包。浏览器基线是 Chrome 111、Firefox 128、Safari 16.4，oklch 原样输出；随主题变化的值用 `.dark` 与自定义属性，不写 `light-dark()`，由 `react-color-scheme.test.ts` 检查产物。

Vitest 的模块别名也指向共享源码；`@peach/legacy/ui` 在组件测试中可落到 `test/stubs/legacy-ui.ts`，需真实控件的测试直接引用 `src/ui-kit/`。`tests/test_web_js.py` 使用 Node 类型剥离验证 `src/core/` 纯函数，不加载需要 document 的主包。

没有引入 `@testing-library/react`：`createRoot` 加 `querySelector` 已经够用
（挂载与输入的助手在 `frontend/test/react/render.tsx`），断言的本来就是真实 DOM。
