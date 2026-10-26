# ADR-0031：前端改用 React + Tailwind v4 + BoardUI 原版源码，继续按页面绞杀式迁移

- 状态：Accepted
- 日期：2026-09-15
- 修订：2026-09-15 补齐迁移桥接契约、弹层样式作用域、上游升级方式、lint 边界与验收定义；同日写下前端基础库的取舍与引入时机。2026-09-16 活动页迁入后 Query 的引入时机与共用方式按实际落地改写；同日配置页外壳与换头像迁入、Preact 移除后，挂载方式只剩 React 档一种，JSX 运行时不再分档；同日复核页迁入、已迁八页外观对回迁移前基线后，补页面外观以基线 CSS 为准的判据与玻璃面高对比回退的归属。2026-09-27 数据管理、重复文件与资源同步迁入：资源同步照 ADR-0080 的契约改写（空文件夹并入资源同步，失效记录永久删除、走危险确认），这一块的外观基线取合入 ADR-0080 后的旧代码，不取迁移开工时的；回收站只是目录网格的一种状态，随馆藏网格一起迁。同日艺人、创作者、厂牌、事务所与标签五个索引页迁入：页内状态（过滤词、词表、视图、类型）全由地址栏承载，从导航进入回到默认档；壳保留骨架、取图链与 `setSelectMode`，选择开关经新增的 `updateIsland` 推进页面；基线里被通用 `.meta` 规则误伤的标签间距不照抄。同日整页空态对回旧 `.emptystate`（沉一档底色、20px 浮层圆角、54px 描边图标方框），播放列表 `/playlists` 列表页迁入：点开一份之后的 `/playlists/:playlist/:item` 仍是遗留层队列播放器；停在这一页时壳经 `updateIsland` 推 `revision`，页面只重取、不重挂；列表键 `staleTime: 0`，因为壳里的「存为播放列表」「加入播放列表」仍在 React 外写
- 关系：替代 ADR-0022 的框架与样式选择；沿用它的绞杀式迁移、构建产物入库、`/dist/` 路由与单一测试入口；ADR-0014 的 Video.js 保留。

## 背景

ADR-0022 选了 Preact 加手写 CSS，BoardUI 的外观靠把样式规则抄进 `web/css/` 与 `web/board.css`。抄写没有单一真相：同一个 token 在 BoardUI 与 `board.css` 各有一份定值，浅色中性灰、焦点蓝和跟随系统深色的三档文字色都对不上，界面反复「没对齐」。

BoardUI 通过 shadcn 注册表发布 React + Tailwind v4 源码，表单与弹层交互建在 React Aria 上。留在 Preact 里，要么继续抄样式，要么经 `preact/compat` 运行 React Aria 并自行验证每个组件的焦点、弹层与键盘行为。

2026-09-14 在配置页「访问密码」分区做了试点：BoardUI 源码逐字复制，React 子树由 Preact 岛经 `ReactSlot` 挂载，与旧样式表同页共存。量到的代价与约束：

- `peach-react.js` gzip 110.6 kB，只在挂 React 子树的页面动态加载；`peach-react.css` gzip 11.8 kB，每页加载；
- 旧样式表不分层，其中 `button,input{color:inherit}` 这类标签规则会压过层叠层里的工具类；
- Tailwind Preflight 铺到整页会改掉未迁移页面的标题、表单与图片基线；
- `board.css` 的同名 token 排在后面，会盖过 `theme.css` 的上游值；
- 旧的全局 `:focus-visible` 会给 React 输入框多画一圈。

测试也要换写法。`tests/test_web_ui.py` 639 个用例里有 597 个只读旧前端源码的字符串；页面迁走后，这批断言如果改绑新源码的字符串，等于把「没对齐」从 CSS 挪进测试。

曾评估但不采用的方案：

- 继续 Preact + 手写 CSS：正是反复没对齐的来源。
- Preact + `preact/compat` 跑 BoardUI：兼容层下 React Aria 的焦点管理、Portal 与键盘行为需要逐组件验证，升级时还要重验。Peach 不承担这份适配维护。
- 只引入 Tailwind、组件自己写：样式仍是抄的，只是换成类名。
- 整站 React 重写：没有可验收的中间态，违反「替代实现测试通过后才删旧代码」。
- Next.js：本次迁移用不到它的路由与服务端能力；静态导出同样不需要 Node 运行时，但既有 Vite 构建加 Python 静态服务已经满足需要，换框架只增加迁移面。
- 用 ESLint 承载 `@shadcn/lint`：`@typescript-eslint/parser` 的 peer 只到 TypeScript 6.0，项目用的是 7.0.2。

## 决策

### 技术栈与上游源码

- 新页面与迁移页面用 **React 19 + TypeScript（strict）+ Tailwind v4**，源码在 `frontend/src/react/`，单测用 vitest。
- 前端只有 React 一种渲染层，`frontend/src` 里 JSX 只出现在 `src/react/`；`frontend/src/react/tsconfig.json` 只为逐字复制的 BoardUI 源码放宽 `exactOptionalPropertyTypes` 与 `noUncheckedIndexedAccess` 两条，其余继承全局配置。Vite 与 tsc 都取离文件最近的 tsconfig。
- **BoardUI 源码日常开发只读**：从注册表逐字复制到 `frontend/src/react/boardui/`，来源与条目哈希记在 `ORIGIN.md`，逐文件 SHA-256 记在 `UPSTREAM.sha256`。Peach 需要不同组合或外观时，在 `src/react/` 下 Peach 自己的目录里组合，差异写进 `ORIGIN.md`。
- **页面外观以该页迁移前的基线 CSS 为准**，不以「BoardUI 里长什么样」为准：卡面、读数卡、分段控件、空态这类跨页共用的面收在 `src/react/components/` 各自一处（`card.tsx` 按基线选 `filled` / `outlined` / `raised`），页面不自己拼类名串。测试只钉数据流与结构，钉不住外观，验收要对照迁移前提交的样式表与骨架逐块核卡面、网格列数、控件形态。
- **上游升级走独立提交**：整文件重新复制，同一提交更新 `ORIGIN.md` 条目哈希、`UPSTREAM.sha256`、`theme.css` 与相关依赖，跑 `web` 域回归。发现上游缺陷时同样以组合件绕开或等上游修复后整文件替换，不在副本上打补丁。没有引用者的上游文件连同哈希行一起删除。哈希只证明副本没有偏离登记版本，组件行为仍由 vitest 与浏览器断言验证。

### 应用入口与共享源码

主界面入口为 `frontend/src/react/bootstrap.tsx`，由 `Application` 管理启动与清理；应用适配按职责放在 `frontend/src/application/`，页面模型与控制器放在对应 React 域。`vite.config.ts` 直接构建 `peach-app.js` 与 `peach-app.css`，主界面模块不外置、不分割。`@peach/*` 直接解析到共享源码，React、Query、历史、shell、头像助手与偏好 store 各只有一份。独立页面包与本机开发取证工具按各自边界另行构建。

- **迁移节奏不变**：逐页替换，每次一到两个页面、独立分支集成；旧渲染函数、旧 CSS 与旧断言随页面删除，不保留双实现。
- **页面与页面里的附属面只由路由树画**：每一页（每条路径都由路由树按匹配打开），以及页面里的附属面（首页筛选条、首页新作行、目录页处理横幅、顶栏搜索下拉），都由 `RouterRoot` 这一棵 React 根经 portal 画进壳的容器，不为某一页或某一块另建 React 根，也不另设第二套挂载入口；不跟某一页走的常驻层按名字登记在常驻表里，同样由这一棵根画：它们没有首屏取数，打开后一直算当前页，收起只卸组件、不撤宿主；壳与它们说话的命令式句柄保留，壳仍经 `application-residents.ts` 的 `loadXxx(host)` 取句柄，各层组件订阅自己的 store，句柄里的绘制照旧同步完成。批量条、配色卡、管理页头、沉浸、设置面板、侧栏与舞台都在常驻表里；图片灯箱与全站 Toast 压在所有页面之上，同样各有一棵常驻根（见下文）。页面元素决定什么时候开、什么时候收，经 `frontend/src/history/managed.ts` 下令：打开时先取齐首屏写进 Query 缓存（取完数才画，中止或壳已离开这一页就放弃这一次），再在同一个任务里换掉骨架、放进宿主，让路由树同步画出首帧（壳紧接着就读页面结构）；每次打开领一个新代次，页面按代次重挂、重取；打开之后壳的开关经就地更新合进同一份 props，不重挂、不重取，还没画完或已收起的容器上是空操作；收起时中止在途请求、撤宿主。壳在 `claimSurface` 换页时收 `#stats` 与 `#index`，所以离开页面后组件不再活着，轮询随组件一起停；目录页处理横幅与首页新作行不在这两个容器里，由它们自己的路由判据收，顶栏搜索下拉常驻、从不收。一棵根里的一处渲染错误不能带走别的面：路由树给每一面各套一层错误边界，一面抛错只空出那一面并撤掉它的登记，壳下次打开时能重开（常驻面的宿主是壳的节点，只卸组件、不撤宿主），错误照旧经 `reportError` 上报一次；根上不套边界，后退前进的派发点与 `<Routes>` 不随某一面出错而卸掉。馆藏媒体卡与网格（`catalog-grid`）挂在 `#grid` 与 `#nrow` 上，`claimSurface` 离开目录与回收站时一并卸掉；卡片的选中、待删、稍后看等状态一律写在 `data-*` 上，壳不往 React 管的类名上加东西，壳里的 Shift 连选与选中态经就地更新推进去。垃圾文件队列（`junk-queue`）与它共用 `#grid` 这一个容器：壳经 `paintGridIsland` 记住当前挂的是哪个岛，换岛先卸旧岛再挂新岛；它的计数行经 portal 画进壳的 `#count`，处置与撤销仍由壳发写操作。实体资料页是一座 `entity-page` 岛、一棵 React 根：资料卡、筛选浮层（交集条、两排玻璃与内容区头）、新作那一行与正文读同一份页内状态，所以不拆成几座岛；四块的宿主仍由壳在 `#index` 里排好（`[data-entity-hero]`、`[data-entity-filter]`、`[data-entity-body]`，根挂在第一格上），岛用 portal 画进去，深链冷启动时的骨架也仍由壳按这几格铺。取数全在岛里：`/api/entity`、新作、作品列表与照片各有 Query 键 `['entity', kind, name]`、`['entity-items', kind, name, query, revision]`、`['entity-photos', kind, name, set, seed]`、`['feed-new', 实体 id]`，`prefetch` 把四样取齐再画，骨架与整页一次换掉。地址栏仍是筛选与视图的唯一真相源、归壳写：壳从 URL 读出 `filters` 与 `media` 当 props 递进来，岛改筛选、换视图只调 `actions.route`，壳写好地址再经就地更新推回，岛按新键重取；前进后退落在同一实体时也只推 props、不重挂。换统称、添别名、订阅与发现源开关这些写操作在岛里发，站内跳转仍走壳的 `openEntity` / `openItem`，壳只剩 `openEntity` / `routeEntityPage` 这一个入口。名册直接用索引页的 `PeopleGrid`，作品网格直接渲染 `catalog-grid` 的组件而不嵌套挂岛，点开一张图直接调 React 的灯箱，只把「在资源管理器中显示」经 `actions.revealSource(id)` 回壳（路径不进浏览器）。未入库的新作那一行（ADR-0042）首页与资料页共用 `feed-new` 组件与同族查询键 `['feed-new', 实体 id | 'home']`：首页那一行由路由树画进 `#feedNew`，换筛选只按代次重取、不重挂，离开目录页才卸；资料页那一行是 `entity-page` 里的同一组件。卡片 HTML 仍由壳的 `feedRowHtml` 拼。图片灯箱（`photo-lightbox`）和全站 Toast（`toaster.tsx`）一样不跟某一页走：它压在所有页面之上、哪一页都能打开，不该跟某个页面容器同生共死，所以 `@peach/react` 导出命令式的 `openPhotoLightbox(index, slides, {revealSource})` / `closePhotoLightbox()`，在 `document.body` 末尾一个常驻宿主 `[data-photo-lightbox-host]` 上只建一棵根，每次打开换新的 `key` 让 Swiper 从头建、关闭把根渲染成空；实体页的照片墙与关注详情的大图都调它，Swiper 仍从 `/vendor/swiper/` 按需加载、只用核心 API。关注页 `/follow` 归 `follow-feed` 整页岛（和关注管理页一样挂在 `#stats` 上），是第一个自己拥有取数的页面岛：`/api/follow` 与凭据两趟都在岛的 TanStack Query 里；地址栏仍是筛选的唯一真相源、归壳写，壳从 URL 读出 `view` 连同这一次进页的取样种子 `seed` 当 props 递进来，岛改筛选只调 `actions.route(view)`，壳写好地址再经就地更新推回，岛按新键取数、只让列表区铺骨架，页头与三排取样不重洗；标记状态、稍后看、检查更新与往回抓一页都由岛的 `useMutation` 发，壳的 `followWrite` 随之删掉。关注详情 `/follow/item/:id` 是舞台 `#stage` 里的 `follow-detail` 组件：条目 Query 先扫 `follow-feed` 的列表缓存，没有才取 `/api/follow?item=`，`followData` 已删；状态、稍后看与隐藏媒体的写操作在岛里发；`<video>` 由岛画、经 `actions.mountPlayer` 交壳挂 Video.js 并拿回清理函数。舞台开关、小窗与回执仍在壳；侧栏只收计数：列表经 `actions.loaded` 交标签计数，深链直接进详情时列表不挂岛，由详情的 `actions.present` 按这一条的标签算好，壳再推给常驻面 `sidebar`。作品详情 `/item/:id` 与 `/mix`、`/parts`、`/editions`、`/playlists/:playlist/:item` 四种队列是同一舞台里的 `item-detail` 岛：两座详情都画在舞台里，Video.js 与氛围光由舞台的播放区挂；条目、队列与相关作品各有 Query 键 `['item', id]`、`['item-queue', kind, id]`、`['item-related', id, limit]`，队列由岛按种类自己取，同一队列里换条不重取；评分、标签、反馈、稍后看、喜爱理由与播放列表排序移出都在岛里发，保存 Mix 的命名表单仍由壳弹进舞台同一层。队列行两座岛共用 `components/mix-queue.tsx`。舞台本身是常驻面 `stage`（`frontend/src/react/stage/`）：`<dialog id="stage">`、进出场、骨架、两座详情与小窗是同一座面，宿主是 `body` 直接子元素 `[data-stage-host]`、换哪一条详情都不换；舞台在第一次打开详情时经 `application-residents.ts` 的 `loadStage(host)` 登记进常驻表，覆盖组的元素按匹配经它返回的 `StageApi`（`open`、`update`、`exit`、`dispose`、`requestClose` 与小窗几项）开关详情，壳不再自己开舞台，`detailReturnPath`、`followDetailReturnPath`、`detailOriginAnchor` 这些「关掉回哪里」的状态仍在壳。Video.js 与它挂的控件、统计角标、右键菜单、流会话与遥测是 `frontend/src/player/` 下的命令式 TypeScript 模块，不进 React 渲染，仍用 vendored 的 `videojs`；舞台里的播放区在 effect 里调 `mountPlayer(video, options)` 并拿回清理函数，小窗与舞台共用同一个播放器实例。沉浸模式 `/immerse` 归常驻面 `immerse`（`frontend/src/react/immerse/`）：全屏连播层、每一格的 `<video>`、手势、动作键、作者与标题、进度条都在岛里，选择器一律 `data-*`；它和舞台一样常驻、哪一页都能打开，所以登记在常驻表里并保留命令式句柄：沉浸面在第一次打开时经 `application-residents.ts` 的 `loadImmerse(host)` 登记进常驻表，壳只经 `ImmerseApi`（`open(startId)`、`close`、`isOpen`、`activeVideo`）下命令。片单与每一条的详情由岛取，Query 键 `['immerse-list', 查询串, seed, draw]` 与作品详情共用的 `['item', id]`；看过、高潮、不喜欢由岛的 `useMutation` 发，回执写回同一条 `['item', id]`、目录卡与壳的条目缓存。每一格经 `mountPlayer(video, {kind: 'immerse', …})` 挂一层不带控件的 Video.js，每格一个流会话，切走、关闭时按会话取消。地址栏归壳写：岛换条只调 `host.route(id)`，壳用 replace 写 `/immerse?id=`，关闭调 `host.closed`；首页筛选口径经 `host.filters()` 读壳的同一份状态。`/immerse` 在页面组里有元素，按匹配经 `ImmerseApi.open` 打开。地址离开 `/immerse` 时保留已打开的沉浸面，关闭键与 Escape 经既有关闭流程回首页；壳保留关闭流程及快捷键守卫里对 `immerseOpen()` 的判断。首页的两排头像、标签条、交集条、排序与版式键归 `catalog-filter` 岛：壳经 `paintCatalogFilter(patch)` 就地推当前口径，动作经 `CatalogFilterActions` 回壳改地址栏与取数；`barsContext` 这份口径仍在壳。顶栏搜索的下拉、补全与键盘归 `search` 岛，经 `expose` 交出 `SearchApi`（`close()`）；搜索框的窄屏展开、失焦兜底与全局 Escape 仍在壳。顶栏齿轮打开的设置面板与侧栏排序归常驻面 `settings-panel`：宿主由常驻面的 `place` 在第一次打开时建在 `document.body` 末尾，壳经 `loadSettingsPanel(host)` 把它登记进常驻表后拿到 `SettingsPanelApi`（`open(section)`、`close`、`isOpen`、`reveal`）。界面偏好仍是壳启动时归一化的那一个 `appSettings` 对象，岛经 `createSettingsStore` 读写同一个对象并落盘，改完经 `changed(effect)` 回调壳去重画受影响的面；跟账本走的几项经 `/api/settings` 写，成功后落进同一个对象。左侧抽屉 `#drawer` 里的导航列、导航上那块滑动玻璃与按语境出现的筛选分组归常驻面 `sidebar`（`frontend/src/react/sidebar/`）：收起态就是抽屉本身那条图标列，导航顺序、按下态与玻璃只有一份，所以不拆成边栏与抽屉两座；壳启动时先按同一份顺序同步写 `sidebarSkeletonHtml` 骨架，Application 启动时经 `application-residents.ts` 的 `loadSidebar(host)` 登记进常驻表，由路由树画进 `#drawerScroll`、在首帧的同一个任务里换掉骨架，壳只经 `SidebarApi`（`render(props)`、`navChanged()`）推内容。导航顺序读写设置面板那同一份 `appSettings` store，设置面板或这一列自己拖动改了顺序，岛按 store 的通知当场重排；筛选口径、`barsContext`、聚合与计数仍由壳算好当 props 推（目录与资料页一份 `catalog`、关注页与关注详情一份 `follow`，作品详情、管理区与索引页只画导航），点下去经 `host.toggleChip` / `navTo` 回壳，所以沉浸与首页筛选条读的仍是同一份 `state`。玻璃 portal 挂在 `#drawer` 上而不住在岛画进去的 `#drawerScroll` 里，切页重画时动画不断。抽屉开合、遮罩、底栏三枚键与品牌仍在壳，岛画好后壳把品牌与开合键挪进标题行；新增 React Query 键为零。管理区的页头（管理条页签、面包屑、标题与说明行）归常驻面 `manage-header`，底部批量选择条归常驻面 `batch-dock`，两面都由路由树画进壳的常驻节点（`[data-manage-header]`、`[data-batch-dock]`，本身 `display: contents`）：壳同步先写骨架，Application 启动时经 `application-residents.ts` 的 `loadManageHeader(host)` / `loadBatchDock(host)` 登记进常驻表、换掉骨架，此后只经 `render(props)` 推当前管理区、菜单项与读数，或选中数、语境（目录、回收站、垃圾、关注）与垃圾页的已排除态。当前在哪个管理区仍由壳从路由表推（`manageSection`），回收站读数由目录计数写进壳的 `trashCount`，同页重画沿用旧读数、离开回收站才清；面包屑左键回壳的 `openDataCleanup`，清空回收站回壳的 `emptyTrash`。批量条的外壳复用 `components/selection-dock.tsx`，键按迁移前基线量；选中集、`setSelectMode` 与 Shift 连选仍在壳，按键经 `host.run(group, operation)` 分派回壳的批量函数，写操作与撤销不进岛。这两面与回收站的危险键读 `styles.css` 里同一条 `[data-danger]` 规则，不各写一份红。
- **卡片图片助手只有一份**：封面与头像的 HTML、人脸取景、图片回落链、按原尺寸摆小图与悬停预览收在 `frontend/src/card-art/`，Application 与组件按源码路径或 `@peach/card-art` 引用同一个模块实例，随主包发出：代表作表、悬停配置和 document 上那组取景监听全站只能有一份，打两份就是壳写一张表、岛读另一张。岛不再经宿主回调向壳借这些函数。它们仍吐 HTML 字符串，岛用 `dangerouslySetInnerHTML` 接：回落链会原地换 `src` 或摘掉元素，取景往 `<img>` 上写内联样式，微光在框上加类名，这些 DOM 侧改动交给 React 管会在下一次渲染被冲掉。代表作表（`representatives.ts`）仍由壳取 `/api/tops` 时写，随目录数据流一起进 Query；悬停预览要的选择态、打码开关与延时由壳经 `configureHoverPreview` 注入取值函数，每次现读。
- **外观应用层同理只有一份**：界面偏好的归一化与 `appSettingsStore` 单例、主题、密度、卡片与照片墙版式、光晕与强调色的写入收在 `frontend/src/appearance/`，Application 与组件按 `@peach/appearance` 直接引用共享源码。壳在模块体里同步调用这些写入函数，`<html>`、`<body>` 属性与 `--tile` 等变量在启动阶段同步写好。版式函数只收壳算好的判据（首页还是 JAV、是否竖屏），自己不读 `state` 与路由；账本下发设置后要重画哪一块，由壳的 effect 表决定。侧栏底部的配色卡是常驻面 `glow-picker`，宿主仍是壳建的 `popover` 元素，开合、锚定与和媒体库菜单互斥沿用 `wireAnchoredMenu`；不换 BoardUI Popover，是因为玻璃材质与开合动效都在那一份里，换掉就要复刻第二份。配色卡和设置面板的预设格是同一个 `glow-preset-grid` 组件，订阅同一份 store，任一边改了，另一边当场对齐。
- **业务状态只有一份**：取数与缓存归 TanStack Query，所有 React root 共用一个 `QueryClient`，同一份真相只用一个 `queryKey`，第二个读者读同一个键；节律不同的两份真相分键（来源列表由用户改、由写操作换单条，封面任务由后台推进、按状态轮询），合成一键会让轮询重画用户正在填的表单。写操作用 `useMutation`，成功后用 `setQueryData` 换局部，不为一次写入重取整页；页面之间不另起一套订阅传数据，也不在遗留层与 React 两侧各存一份同一数据。页面经 props 拿遗留能力、经回调（如 `receipt`、`onPicked`）交回结果。需要的遗留能力（`confirmModal`、来源图标表、番号标题）只经 `@peach/legacy/*` 的声明模块或 props 传入的遗留函数调用，不抄一份。
- **壳与岛共用同一个 `QueryClient`**：实例建在 `frontend/src/query/`，Application 与组件按 `@peach/query` 直接引用共享源码；`@tanstack/query-core` 只打进主包一份，否则 `notifyManager`、`focusManager`、`onlineManager` 这些单例会分成两套，观察者与缓存不在同一套调度里。Application 在启动路径上直接 `fetchQuery`。壳原有的条目缓存 `CACHE` 删除，`['item', id]` 只放详情投影，卡片投影不写进去（卡片的 `tags` 是字符串数组，详情的是对象数组，混进同一个键会被当成详情读）；唯一读卡片字段的回收站点击改为直接收那张卡。筛选栏数据归 `catalog-bars.ts`：`['facets', 口径, 代次]` 与 `['tops', 参数, 口径, 代次]`，`staleTime` 30 秒；两排共用同一页续页请求，30 秒内回到同一口径时复用，所以请求比迁移前少，界面不变。写操作与手动刷新调 `dropBars()` 让代次加一，保证一定重取；不用整族 `removeQueries`，因为它会取消在途请求，等它的调用方会拿到 `CancelledError`。
- **壳自己的内存状态也只有一份**：目录口径（`state`、`barsContext` 与详情打开前的那份快照）、选择集与选择模式、详情与关注详情「关掉回哪里」的来处、跳到配置页某一节与活动页预填这类一次性请求，存放在 `frontend/src/shell/` 的一个 TS 单例里，随主包发出，Application 与组件直接引用它读写；路由树里的 React 组件接手这些页面时读写同一个实例，不在组件里另存一份。单例保住壳今天的对象语义：整体重建的写点换引用，其余写点原地改同一个对象的字段，`barsContext.filters` 与 `state` 仍是同一个对象，选择集仍是同一个 `Set`；每次写入后通知订阅者，读者按单调递增的版本号接 `useSyncExternalStore`。改成不可变更新会改变两份引用之间的别名关系，那是可见行为的变化，不在这一层做。服务端事实（来源在线状态、目录总数、聚合、实体形态、回收站计数）不进这个单例，按上一条归 TanStack Query；壳路由派发用来判过期的代次计数器（`surfaceEpoch`）也不进，派发搬进路由树后由路由树的开次代次（`openEpoch`）接替。
- **浏览器历史也只有一份**：`frontend/src/history/` 用 React Router 自带的 `UNSAFE_createBrowserHistory({ v5Compat: true })` 建全站唯一的历史对象，Application 与路由树按 `@peach/history` 直接引用同一源码实例。启动路径通过它写地址；`<BrowserRouter>` 自建的历史只听 `popstate`，看不见壳写进去的条目。不引独立的 `history` 包，因为它已停更，条目格式也和 React Router 内置那份不同。`UNSAFE_` 是非公开导出，升级 React Router 时可能静默失效，`frontend/test/history/` 与 `frontend/test/react/router.test.tsx` 是它的报警线。路由树用底层 `<Router location navigationType navigator>`，仍属 Declarative 模式；不用 `unstable_HistoryRouter`，因为它把更新包进 `startTransition`，连续两次变化会并成一次渲染。派发规则：每次历史变化领一个序号，壳经 `shellNavigate` 写地址时当场认领（页面换档经 `ShellActions` 交壳写回地址的也走它），页面元素不重开；覆盖元素接手该次详情请求，详情内认领的地址改写不重复打开；后退前进、React 子树发起的导航与壳的 `actions.navigate` 没人认领，派发一次并领一个开次代次（`openEpoch`），按匹配画的页面元素按代次重挂重取或就地推。每条路径都有元素，壳不再接派发，也没有路由表。判据用序号不用地址，因为地址不变的 `popstate` 照样要重开那一屏；覆盖组的元素同样按派发序号挂 key，`history.state` 为空的后退也重开同一条。路由根在历史变化的同一次调用里同步提交（`flushSync`），壳接着写宿主时上一页的 portal 已经卸掉。元素打开那一屏的动作放在提交阶段之外，因为侧栏等常驻面的句柄内部用 `flushSync` 同步绘制，同一棵根在提交阶段里不会同步刷新。
- **管理区页面由路由树按匹配打开**：统计、来源与凭证、活动、诊断等十一条管理路径在页面组里各有元素（`frontend/src/react/router/pages/managed.tsx`），`/resource-sync` 与 `/configuration#libraryProcessing` 是改写到数据管理的重定向。元素按路径加开次代次（`useOpenEpoch()`）挂 key：派发一次就是打开一次，同一路径再打开重挂重取；经 `shellNavigate` 认领的写地址（复核页换分类、关注管理换页签）代次不变，不重开；带上路径是因为认领式改写到另一条管理路径时代次也不变，同一个元素类型会被原地复用。元素挂上经 `ShellActions.surfaceChanged` 让壳收起别的面；去处不是覆盖地址时，覆盖元素在卸载后的微任务收舞台，再在 `#stats` 里铺这一页的骨架（与壳冷启动铺的是同一张，键相同就不重画），然后走 `openManagedRoute`：释放上一页，领一个 revision，按 `MANAGED_ROUTES` 里该路径的 `prefetch` 取齐首屏数据，再在同一个任务里清空 `#stats`、挂上宿主并通知 `ManagedSurface` 用 `flushSync` 画，所以不会先出空骨架。首帧 props（只读态、配置权限、引导标记、配置页页签）由元素自算。`#stats` 里的整页只由 `openManagedRoute` 换上；此外只有元素铺骨架，以及关注页打开前先释放画着的管理页。路径集合由 `ManagedPath` 类型定死，`MANAGED_ROUTES` 必须逐条覆盖。页面要用壳的能力（开详情、按标签回目录等）经 `configureRouter(actions)` 传入的 `ShellActions` 拿，不 import `app.js`。元素从 effect 里写地址一律排进微任务并带存活守卫：路由根在历史变化的同一次调用里同步提交，提交阶段里同步写地址会再要一次同步提交。页面组按 `background ?? location` 匹配，详情舞台压在 `/item/:id` 上时下面那页不拆。
- **索引页与资料页同样由路由树按匹配打开，画进 `#index`**：`managed.ts` 按容器分槽，`#stats` 与 `#index` 各记一页；资料页只收 `#index`，所以从管理页进资料页时管理页只是被藏起来、照旧活着。索引五页（`INDEX_ROUTES`）与资料页五个模式（`ENTITY_ROUTES`，`/performers/*` 等）的元素在 `frontend/src/react/router/pages/index-entity.tsx`，`kind` 与 `name` 由元素从地址解（先整段解码再按段切，名字里的斜杠吃掉剩下全部段）。资料页的框架（资料卡、筛选浮层、新作行、正文四块）由元素排好，经 `openManagedRoute` 的 `place` 在首屏取齐那一刻换进 `#index`；浮层吸顶要求父盒就是 `#index`，新作行是遗留层卡片，所以框架不进 React。打开之后的开关（换筛选、换视图、换版式、选择模式）合进画着的那一页，不重挂也不重取。页内改地址参数（索引页过滤词与页签、资料页筛选、排序、换一批、照片视图）经 `ShellActions` 交壳，由 `shellNavigate` 认领写入：不认领就会派发成一次重开，每敲一个字就重挂一次。后退前进各走各的：索引页按开次代次挂 key、按地址重开，资料页按 `kind` 与 `name` 挂 key、同一实体只换参数时就地推。壳要重开画着的那一页（批量操作后重取、筛选回退、点开的正是画着的那一位）不写地址，把 `@peach/shell` 的 `pageOpens` 计数加一，元素整页重开，同一微任务里的换页与重开合成一次。筛选条的资料页语境不再存进 `barsContext`，由 `currentBarsContext()` 推导：变量是作品详情就用变量，否则 `#index` 里画着资料页就按那一页的 props 推，再否则用变量。推导源用画着的那一页而不用地址，因为后退前进进详情时地址已经是 `/item/:id`，资料页还压在下面。
- **播放列表页与关注页画进 `#stats`，但不算管理区**：两页登记在 `BROWSE_ROUTES`，由路由树按匹配打开（`frontend/src/react/router/pages/follow.tsx`），元素不按开次代次挂 key，卸载时也不释放 `#stats`；壳要整页打开时把 `pageOpens` 加一，写地址一律经 `shellNavigate` 认领。关注页分两种进法：已经停在 `/follow` 时再点侧栏或点「去看更新」算重新进入，重掷取样种子、滚动记忆清零；从别页点侧栏、后退前进与关掉详情算回到，地址带上一次的 `view`。列表还画着时两种都就地推新代次、重取、回顶，不重挂；列表已被收掉时整页打开，列表读缓存不重取，滚回记下的位置。取样种子、代次与滚动位置记在 `@peach/shell` 的 store 里。播放列表页由侧栏、后退前进、关掉详情进来都整页打开；停在这一页时的「换一批」推 store 的 `playlistsRevision`，只重读、不重挂。`/follow/item/:id` 按背景匹配时列表压在详情下面；深链进详情没有背景，不挂列表，这与 `/item/:id` 深链补画目录是两条规则。关注页首屏的骨架淡出经 `place` 交给元素的 `reveal`（内部是 `revealSkeleton`）：写入那一步只放空宿主，页面紧接着在同一个任务里由 `flushSync` 画满，骨架与整页之间没有空帧。详情深链 `/follow/item/:id` 由壳让出 `#stats`，关掉详情回到列表时才画。关注页的助手与动作由 `ShellActions.follow` 在打开时交进来：卡片按引用比较，它们拼的 HTML、碰的 DOM 与写的地址都还在壳里。
- **目录网格与垃圾队列画进 `#grid`，表按页面分键**：`CATALOG_ROUTES` 只有两个键，`/` 是目录网格（首页、未看、稍后看、标记与回收站画的都是这一张），`/junk-files` 是垃圾队列；槽里记的是画着哪一页，不是地址。`<Routes>` 另按 `CATALOG_PATHS` 声明六条地址路径。按地址分键会在三处出错：网格在 `/trash` 打开之后去 `/` 是就地推，槽里的路径不跟着变；`?state=ads` 落在 `/` 上，画的是垃圾队列；冷启动落在详情地址上时，网格在 `/item/:id` 底下补画。六条地址路径由路由树按匹配打开（`frontend/src/react/router/pages/catalog.tsx`），元素按这两个页面键挂 key、不按开次代次：代次变化只让元素按地址重建筛选并重取，从详情关掉回来、后退前进经过详情都不重挂。目录筛选 `state` 由目录元素按地址与 `appSettings` 重建：排序与方向进地址，换排序用 replace 写入、不加后退步；取样种子不写进地址（地址上手写的 `?seed=` 照读），进首页重掷、目录内沿用；`loc` 的缺省值看来源是否在线；`owner` 按地址重读，`region` 沿用旧值；缩略图版式只在启动时读；`barsContext.filters` 与 `state` 是同一个对象。页内换筛选、排序与 JAV 切换先写 store，再经 `shellNavigate` 认领写地址（JAV 切换写 `homePath()`），元素订阅 store 重取，不重挂。壳的判据改读槽：`gridPainted()` 看 `managedEntry($('#grid'))`，`gridTaken()` 再算上首屏取数中的那一次。`#grid` 只在 `clearCatalogGrid` 里收；`claimSurface` 只收 `#stats` 与 `#index`；`releaseManagedRoute` 必须点名容器，没有无参的「全部收起」。目录首屏的骨架淡出经 `revealRoutedPage`；垃圾队列不在打开前取数，画上就铺自己的骨架。
- **详情条目记下压在哪一页上，路由树分页面组与覆盖组**：作品详情、四种队列与关注详情（`OVERLAY_PATHS`）的历史条目在 `usr` 里带 `{ backgroundLocation: { pathname, search }, overlay }`。背景在壳决定打开的那一刻经 `holdOverlayBackground` 快照，不在 push 那一刻读地址，因为队列打开意图由路由树的 `QueueRequests` 接手，先铺舞台骨架并取数，实际数字条目和首屏确定后才 push 队列地址；当前已在详情地址上（同队列换条、关注组内换条、从详情点开另一条）就沿用上一条的背景，不嵌套。背景另记在 `overlay.ts`，和壳给关闭用的 `detailReturnPath`、`followDetailReturnPath` 分开，两者的清空时机不同。关掉详情、删掉当前条目、后退前进与启动时先清空。只存路径与查询串，不存 DOM、对象或条目 `key`，因为条目跨刷新存活而内存不在。`RouterRoot` 里的 `<Routes>` 分成两组：页面组 `location={background ?? location}`，详情压在哪一页上就还匹配那一页，`location` 一直给，不给与给之间会多包一层 `LocationContext`，`path="*"` 的元素会被重挂；覆盖组按真实地址匹配 `OVERLAY_PATHS`，元素按导航序号接走普通点卡的一次性请求或打开历史条目；冷启动条目虽然已认领，`initialOverlayOpened` 仍允许首次打开，后续只改写条目的认领 replace 不重开，两组都不报没有路由；页面组的 location 不变时后退进详情，覆盖组照样按序号重开。后退前进时读条目里的背景，三处行为由用户定：后退前进进到详情时，覆盖组元素经 `adoptOverlayState` 接上这一条的背景，之后换条、展开小窗照样带它；打开详情的那一处用 `takeOverlayReturn` 取来处，取一次就清，关掉回到条目记的那一页；关注详情的来处带着筛选查询串。同一个队列里换条不改来处，播放列表关掉回打开队列那一刻的列表页并重读。启动不读（用户定，保持现状）：条目跨刷新仍带着 `usr`，页面组在启动那一条上不用它，刷新、新标签页与深链落在详情上时都当作没有背景：`/item/:id` 下面画目录网格（非首页口径），`/follow/item/:id` 下面不画列表，关掉回各自的缺省来处（作品详情 `/`、关注详情 `/follow`）。之后的后退前进照常按条目里的背景匹配：从详情进统计页再后退回详情、关掉，回到并画出来处那一页。`/immerse` 在页面组里，不算覆盖层，不带背景；页面元素打开沉浸，换条由壳认领 replace 写地址，不重开；POP 离开地址时保留沉浸打开，Escape 或关闭键收起并回首页。
- **入口页由独立页面包画，不进主界面的包**：首次运行页与完成页由 `peach-pages.js` / `peach-pages.css` 画（`frontend/vite.pages.config.ts`），登录页与错误页也由它画，这个包因此免登录可取（ADR-0094）。服务端在未配置时只吐一张薄壳：`color-scheme`、主题预读、Inter 字体与挂载点；题目、默认值、显隐规则、校验文案与完成后的入口都来自 `GET /api/setup/questions` 与 `POST /api/setup`，页面骨架上的固定文案归前端。这个包自成一份，不加载主包 `peach-app.js`：首启时没有主界面的壳和登录态，主界面出错也拖不垮首启。独立包将用到的共享控件源码直接打入，不借主界面的模块或会话。用户已定的四条：控件全用 BoardUI，像素差随之而来；浏览器禁用 JS 时首启页打不开，填错时就地标出、已填内容不丢；首启页认用户手动选的深浅色，与登录页同一套预读，并加载 Inter；独立包完成后的自动跳转用脚本定时，和设置页重启后跳转共用 `RESTART_REDIRECT_MS`。从局域网打开未配置的服务时，题目接口回 403，页面只画一句「请在运行 Peach 的这台电脑上打开设置」，不画一张提交必被拒的表单。字段错误按读屏可播报的写法挂在各自输入框下，焦点落到第一个出错的字段。
- Peach 自己以 HTML 字符串拼出的 Board 风格组件随使用它们的页面改写成 `src/react/` 下的组合件，复用其中的数据与布局计算：`board-metrics.ts`、`board-sankey.ts`、`board-analytics.ts` 已随统计页与口味页改写并删除；`board-controls.ts` 剩下的是给遗留页共用的 DOM 行为（范围输入读数、全站 tooltip、下划线 Tabs 与分段控件的滑块），读者只剩各页骨架（复核页迁走后页面里没有遗留读者，`.follow-workspace-switch` 只剩骨架里那一份），随最后一个遗留读者一起删。跨页共用的卡面与外壳先于第二个读者进 `src/react/components/`：`mix-card.tsx` 与 `use-stack-flip.ts` 是一叠视频的卡（翻页时序照抄遗留 `wireStackFlip`），首页 Mix 卡迁入时直接换用；`modal-frame.tsx` 是模态弹层唯一的外壳（`form` 档 540px 对应 `ui-kit/modal.css` 的 `.ui-geist-modal`），页面不再各自拼 `ModalOverlay`。
- **模块边界**：主界面由 Application、React 路由树与域控制器组成，发布 `peach-app.js` 与 `peach-app.css`。共享助手以源码别名引用，不发布运行时桥接产物或 `/js/` 垫片。`board.css` 中与 BoardUI 同名的 token 按实际 `var()` 读者归零清理；骨架、全局外壳和第三方样式仍需的隔离保留。

### 新旧样式并存

以下做法只服务于新旧样式表同页的阶段，由 `tests/test_frontend_build.py` 的 `BoardTokenTests`、`ReactBundleTests` 钉住：

- 工具类不进层叠层，旧样式表的标签规则因此压不过类名；
- Preflight 逐字包进 `@scope (.peach-react)`；
- React 容器上按 `theme.css` 的 `:root` 与 `.dark` 块重新声明同名 token。这份声明不是手工副本，`BoardTokenTests` 逐条比对它与 `theme.css`，不一致即失败；
- 全局 `:focus-visible` 排除 `.peach-react` 子树；
- React 子树里复刻的旧玻璃面，其高对比回退仍写在 `web/board.css`，按 `data-*` 属性（`data-glass-pane`）认人：`frontend/test/legacy-class-names.test.ts` 禁止 React 产物与遗留样式表出现同名类。

**作用域覆盖弹层**：React Aria 的 Popover、Select 列表与 Dialog 经 Portal 渲染到容器外。Application 的 Providers 用 `UNSAFE_PortalProvider` 把它们统一挂到 `body` 末尾一个同样带 `.peach-react` 的容器，读到的 token、Preflight 与焦点规则和页面内一致。弹层不塞进可能裁切它的局部容器。

旧样式表全部退出后重新评估上述五条：仍被第三方样式（如 Video.js）需要的隔离保留并写明原因，其余删除，Tailwind 回到上游默认的层叠层写法。

### 测试与门槛

- **沿用 ADR-0022**：`npm run build` 的产物提交进 `web/dist/`，运行时不需要 Node；`/dist/{path}` 路由不变；测试入口仍是 `scripts/test.ps1` / `scripts/test.sh` 的 `web` 域；依赖精确锁定，在 [docs/FRONTEND.md](../FRONTEND.md) 登记用途。
- **旧断言先分类再删**。页面迁走时，它在 `tests/test_web_ui.py` 等处的源码字符串断言逐条归入三类，去向写进提交说明：
  - 设计决定（用户定过的颜色、状态色块、焦点样式）写成 `frontend/e2e/design-*.test.ts` 里读 `getComputedStyle` 的断言，或由 lint 规则覆盖；
  - 行为（提交什么、错误写回哪个字段、控件何时可用）写成 vitest；
  - 布局与运行期问题（溢出、等待态、控制台报错）归 `frontend/e2e/smoke.test.ts`。
  - 三类各管一件事：lint 查源码是否守约定，`getComputedStyle` 查浏览器实际应用的样式，vitest 与浏览器交互查点击和键盘行为，互不替代。
- **lint**：`npm run lint` 用 Oxlint 跑 `@shadcn/lint` 的六条规则，只查 `src/react/`、排除 `boardui/`；`web` 域与 CI 都执行。
  - `no-restyle` 按 `settings.shadcn.ui`（`@/components`）识别 BoardUI 组件，所以 Peach 代码一律经 `@/components/...` 别名引用 BoardUI，不写相对路径；
  - 规则约束的是随手改颜色、间距与组件外观。由数据决定的几何（进度、定位、媒体尺寸）走 SVG／元素属性或 React Aria 自带定位；确需内联样式时逐行禁用并在同一行写明原因，不整条关规则。
- **哈希**：`tests/test_frontend_build.py` 按 `UPSTREAM.sha256` 逐文件比对 `boardui/`。
- **页面稳定判据**：冒烟先断言目标页面主体已出现（成功内容、空态或错误态之一），再等 `aria-busy` 与 `data-skeleton` 消失。只看等待标记消失不算稳定：页面完全没渲染时也没有这两个标记。React 子树在请求期间写 `aria-busy`，首屏骨架写 `data-skeleton`；BoardUI 组件不带加载态，由 Peach 的组合件补上。

## 后果

- 迁移期同一页会有两种外观：未迁移的分区仍是旧写法。
- 主界面加载 `peach-app.css`，体积随组件与共享控件变化，当前值记在 [docs/FRONTEND.md](../FRONTEND.md)。
- Oxlint 的 JS 插件 API 仍是 alpha，`@shadcn/lint` 只有 0.1.0：两者精确钉版本，升级前先跑 `web` 域。`eslint` 作为 `@shadcn/lint` 的 peer 会装进 `node_modules`，不调用。
- 冒烟与设计决定断言依赖 npm、ffmpeg 与 Chrome。本机缺任一项时整组显式跳过，这只适用于非验收运行。CI 的 `web-bundle` 任务跑 typecheck、lint、vitest 与 build，不跑 e2e；CI 接入带浏览器的 e2e 任务、并在 CI 里把缺依赖判为失败之前，迁移分支须在本机 `web` 域输出里确认 e2e 实际执行。
- `ReactBundleTests` 与 `BoardTokenTests` 里只在新旧并存期成立的断言，随「新旧样式并存」一节的做法一起删除。

## 验收门槛

- 每个迁移分支：
  - 目标页面在桌面与 390×844 下功能等价；
  - `web` 域与 `full` 全绿，含 tsc、vitest、lint、冒烟与设计决定断言，e2e 显示为跳过的运行不算通过；
  - 反复进入、离开、返回页面，以及请求未完成时切页，都不重复请求、不留旧数据覆盖新页面，控制台无报错；
  - 页面含弹层时，在浏览器里核对明暗主题、焦点进出与恢复、层叠顺序与滚动锁定；
  - `web/dist/` 与源码一致；旧断言的去向写进提交说明。
- 迁移完成的定义：
  - 应用入口为 `Application`，不发布 `web/app.js`、运行时桥包或 `/js/` 垫片；`@peach/legacy/*` 仅作为共享源码别名；
  - 并存期断言按上一节删除；
  - `tests/test_web_ui.py` 里依赖旧实现的断言迁移或删除完毕，仍然成立的静态资源、页面服务与构建契约测试保留或迁往对应测试文件；
  - AGENTS.md 与 README 的前端章节只描述 React。

## 修订：浏览器用例在 CI 执行，冒烟逐路由写明主体（2026-09-15）

「后果」里约定：CI 接入带浏览器的 e2e 任务、并把缺依赖判为失败之前，迁移分支须在本机确认 e2e 实际执行。
当时 `python` 矩阵没有 Node、`frontend/node_modules`、ffmpeg 与 Chrome，`tests/test_web_e2e.py` 在 CI 上每次都跳过；
冒烟也只等 `aria-busy` 与 `data-skeleton` 消失，「页面稳定判据」要求的主体断言还没有落到用例里。两件事现已接入：

- 需要 Node、前端依赖、ffmpeg 或 Chrome 的用例统一经 `tests/support/conditions.py` 的
  `missing_prerequisite` 判定：本机缺失时显式跳过，`GITHUB_ACTIONS=true` 时判失败。
  `test_frontend_build.py` 的 tsc、lint、vitest 同一口径。
- CI 新增 `web-e2e` job，在 `windows-latest` 上装齐四样、经 `PEACH_E2E_CHROME` 指定 Chrome，
  执行 `web` 域并纳入 `verified` 汇总；矩阵扩成全量时 Windows 全量行已含 `web` 域，它按条件跳过，
  同一批用例不跑两遍。`python` 矩阵里 `core` 以外的行也装 Node，Windows 行
  另装 ffmpeg 与 Chrome，否则全量行上的这些用例会判失败。工作流结构由 `test_frontend_build.py` 断言。
  本机确认 e2e 实际执行那一条随之由 CI 兜住；本机跳过仍不算验收通过。
- `frontend/e2e/smoke.test.ts` 为每条路由写明主体：路由自己的标题，加上内容区、索引条目或明确的空态。
  先等主体可见，再 `settle`，再量几何。新增路由要同时写明它的主体。

## 修订：前端基础库随页面引入，不预装（2026-09-15）

外部审查按 master `a1ff3cf6` 逐项评估了迁移期常被一并推荐的基础库。判据只有一条：**页面既然要改写，比较的是改写后
的长期维护成本，不是「现在多装一个依赖」**；已有实现不是保留它的理由，但业务语义（身份、分页口径、显式触发）不随库
一起换掉。取舍如下，引入时机都绑在对应页面的迁移分支上，不单独开「装库」的提交。

| 库 | 决定 | 时机与边界 |
| --- | --- | --- |
| TanStack Query | 引入 | 随第一个整页归 React 的页面进入：活动页的三段读 `/api/tasks` 一个 `queryKey`，轮询写成 `refetchInterval`。高清版页随页面迁移接进同一个 client，前端没有 Preact signals store、`frontend/src/state/` 与 `refreshStore`，`@preact/signals` 不在依赖清单里；数据管理卡片的总数在数据管理页迁移时读高清版页同一个 `queryKey`。API 地址、响应类型、错误文案与 `useAction` 的提交互斥保留。所有 React root 共用 `frontend/src/react/query.ts` 里那一个 `QueryClient`：`retry: 0`，不因窗口聚焦或重新挂载自动重取（首屏由页面级 `prefetch` 决定，重进页面就是重取）；本机接口用 `networkMode: 'always'`；对外部来源的采集与追更仍只由用户显式触发，不进 Query 的自动重取；后台任务的**状态**是本机端点，按 `running` 开关 `refetchInterval` 跟进度，跟的是状态不是重新触发；首屏读到的旧终态不画成新结果，只有本次启动过或本次见过 `running` 的那一趟才发回执；`AbortSignal` 必须传到实际 `fetch` |
| TanStack Table | 已引入（8.21.3） | 随关注管理的表格视图进入：BoardUI 的 Data Table 本身由它驱动，Peach 逐字复制的是 `table` 条目，`data-table` 是只有示例的 block、依赖的条目 Peach 没有，只读不抄；别名表、只读信息表用 BoardUI 基础 Table。选择以来源 ID、条目 ID 为身份（`getRowId`），卡片与表格两个视图共用一份选择集合，跨页批量以 ID 集合为准；排序作用于整个结果集：数据能一次取完时由前端对全集排（`manualSorting`，`/api/follow` 一次回全部来源），取不完时由后端全量排，两种都不许把「只排当前页」表现成排了整个结果集 |
| React Router | 外壳阶段接管 | 8.4.0 以 Declarative 模式接管客户端导航：历史写入与后退前进的派发归它，页面与覆盖详情都登记在路由树，壳没有路由表；`tests/test_legacy_shell_routes.py` 冻结页面全集，核对页面组、覆盖组互不相交、壳列为空；不在别的 React 子树里另设路由。数字 ID 校验、实体名称吃掉后续含斜杠的路径、返回列表的状态与播放期间的导航行为归路由树，`web/js/routes.js` 不再发布 |
| TanStack Virtual | 随馆藏网格迁移引入；旧壳先用 `content-visibility` 缓解 | 见下节实测：连续加载到 1500 张卡片时滚动帧间隔到 48 ms，给卡片加一条 `content-visibility: auto` 就降到 18 ms。网格是分段、竖屏带、悬停预览与就地舞台的组合，不是均匀列表，虚拟化要同时解决动态高度、滚动位置恢复与页内查找，所以在网格迁到 React 时作为该页的设计输入一起做，不在旧壳里再写一份。旧壳阶段只加那条 CSS，随 `web/css/12-cards.css` 的改动走 e2e |
| Vidstack、Uppy、Refine | 不引入 | Video.js 保留（ADR-0014），只迁播放器的 React 接入；Peach 的入口是扫描、挂载与来源采集，没有上传中心；已有自己的业务接口、复核与任务处理，不再套资源管理层 |

### 馆藏网格连续加载的实测

2026-09-15，1500 部合成库（`scripts/demo_dataset.py`，占位短片、无缩略图），回环 `peach serve --no-auth`，
无头 Chrome 1280×800，每批 60 张，沿 `#loadSentinel` 连续加载；脚本与原始结果在
`<私有验证记录>`。

| 卡片 | DOM 元素 | JS 堆 MB | 帧间隔均值 ms（原样） | 最长帧 ms（原样） | 帧间隔均值 ms（加 `content-visibility: auto`） | 最长帧 ms（同） |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 61 | 3862 | 3.2 | 6.6 | 12.5 | 6.7 | 14.9 |
| 610 | 28306 | 3.8 | 13.2 | 20.3 | 8.4 | 11.5 |
| 915 | 41886 | 4.8 | 30.3 | 42.6 | 11.0 | 13.1 |
| 1525 | 69037 | 6.6 | 47.7 | 59.7 | 18.2 | 24.6 |

帧间隔取往返滚动 30 帧的均值。每张卡片约 45 个元素；堆与强制布局的开销都小，涨的是滚动时的每帧工作量，
原样十批之后就贴近 16.7 ms 的帧预算。对照组只多一条 `#grid article.card{content-visibility:auto;contain-intrinsic-size:auto 320px}`，
DOM 数不变，浏览器跳过视口外卡片的渲染，1500 张时仍在预算附近。真实库的卡片带缩略图与悬停预览，只会更重；
无头 Chrome 没有 GPU 合成，绝对值偏高，趋势成立。
