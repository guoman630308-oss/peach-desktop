---
name: peach-web-ui
description: 在新增、修改或复核 Peach 页面、控件、提示、错误、数据面板、响应式布局或视觉样式时使用。
---

# Peach Web UI 复用门槛

最后复核：2026-10-18
证据来源：现有 UI 契约、`docs/TESTING.md`、2026-09-19 本机会话工具记录、jakubkrehel/skills 与 emilkowalski/skills 摘录、`docs/reference-snapshots/open-claude-ui.md`。

## 开工顺序

1. 读取相关页面、`frontend/src/ui-kit/`、`web/css/` 与 `tests/test_web_ui.py`，先找现成控件、token 和行为；按 `docs/DESIGN_GUIDELINES.md` 对应章节核对层级、容器、输入、滚动、手势与反馈，验收参数仍以本技能和现有契约为准。
2. 外部产品被称为参考时同时执行 `peach-reference-evidence`；没有当前可复现证据就写 `未取得`，不补动画、间距或交互猜测。
3. 视觉与交互先过 `docs/reference-snapshots/vercel-web-interface-guidelines.md` 的 Focus States、Forms、Animation、Content 四节，以及 `vercel-report-design.md`（即 `vercel.com/design.md`）的「Reject generated-design reflexes」；第三方逆向测量的 DESIGN.md（如 design-bites）不作证据。
4. 新控件先检查 `docs/reference-snapshots/vercel-geist-controls-measured.md`、`vercel-geist-semantics-measured.md`、`vercel-geist-note-progress-switch-analytics.md`、`vercel-geist-command-search-loading.md`、`vercel-geist-button-icons.md` 与 `vercel-geist-split-button.md`。
5. 收尾前用 `docs/reference-snapshots/jakubkrehel-interface-skills.md` 与 `emilkowalski-skills.md` 的检测表自查，组件按「极端内容场景」挑轴、按「极端值要真实」取值写夹具；与本技能冲突时以本技能为准。
   用 `break-ui`、`/state-machine` 时，临时页和演示开关只建在本任务工作树里，交付前删掉。

## 组件选择

| 需求 | 使用 | 不要使用 |
| --- | --- | --- |
| 字段、卡片、分区旁的持久反馈 | Note | Toast、空状态 |
| 页面／系统级问题与恢复动作 | Banner | Note |
| 短暂操作回执 | Toast | 持久 Note |
| 写操作前的确认 | `confirmModal()` | 原生 `confirm()`、Toast |
| 一个主动作加 1–4 个近亲做法 | Split Button（主动作在左，第一项菜单项与它同名同事） | 把变体摊平成并排按钮、把破坏性动作当主动作 |
| 一行内的重排 | `wireDragReorder()` 拖动 | 上下移动按钮 |
| 已知总量的进行状态 | Progress | 装饰性蓝条 |
| 用户触发动作等待结果 | Spinner | 旋转原操作图标、Loading Dots |
| 后台任务仍在推进 | Loading Dots | Spinner、假百分比 |
| 整页或大区块首次等待内容结构 | Skeleton | Spinner、Loading Dots |
| 2–3 个互斥视图 | Switch（radio） | Toggle |
| 布尔开关 | Toggle | Switch |
| 无布局高度变化的菜单 | Menu／Listbox；开合走 `presentMenu`／`dismissMenu`，动效只在 Board 层那一份 | Collapse 动画、自己再写一份 150ms |
| 带搜索语义的输入 | Search Input（搜索图标前缀；搜索中原位换 Spinner） | 无图标裸输入 |
| 全屏命令／设置覆盖层 | 有锁定证据的 Dialog motion | 把同一动画套给普通菜单 |
| 展开正文或分组 | Collapse | 自造 easing |
| 一组输入／候选及其底部动作 | Fieldset | 自造卡片 Footer |
| 固定高度区域中的溢出内容 | Scroller | 页面级滚动或嵌套滚动区 |
| 没有数据或结果 | Empty State（图标、标题、说明同组） | 裸灰字或把说明拆到组件外 |
| 图标含义与补充说明 | Tooltip | 让浮层撑宽页面 |

## 实现门槛

- 优先扩展 `frontend/src/ui-kit/`，不要为同一语义复制一次性 class 和模板。
- 数据表统一用 TanStack Table 驱动 BoardUI `Table`，具备全集排序、分页、跨页选择与批量操作；工具栏、分页和批量栏按关注列表复用 `data-table.tsx`、`table-controls.tsx`、`DataTableFrame` 与 `SelectionDock`，默认每页 20 条，同页只保留当前表格的选择。
- 管理页单面板分区的标题放在面板内，不在框外重复；多卡片分组可用框外标题。同级分区标题用 `text-title-2-medium`，加载态与内容态共用 500 字重、20px 字号和 26px 行高。
- 颜色、字号、圆角、浮层层级只用 `:root` 已有 token；新 token 必须证明现有词汇无法表达。
- 字重只有 400／500／600 三档，标题按既定层级选择 medium 或 semibold；圆角只用 `--badge-radius`／`--control-radius`／`--surface-radius`／`--floating-radius`／`--pill-radius` 加 `50%` 与 `0`，带边框容器里的头尾条用 `calc(… - 1px)` 保持同心。两者的字面值由 `tests/test_web_ui.py` 拒绝，归属判据见 `:root` 注释。
- 单色优先：`--tungsten` 只给焦点环、链接、进度／数据与 Toggle 开态。主动作用 `--ink` 底 `--ground` 字且每屏最多一个。标题悬停下划线不变蓝，计数徽章中性灰。其它选择器引用 `--tungsten` 由 `tests/test_web_ui.py` 拒绝；实测见 `vercel-geist-semantics-measured.md`「选中态与开关色」「Button 全变体与状态」。
- 选中态只有填充：一律 `--hover` 底 `--ink` 字，不加边框、不加 `inset` 一圈线、不加字重（Geist Switch／Tabs 的类名里三样都没有）。填充既然专属选中，**同一排横向**互斥选项的未选中项悬停就只提文字色到 `--ink`；没有选中态的按钮和没有并排邻居的孤立开关悬停照旧抬填充。侧栏导航（`.edge`／`.dnav`）分工相反：实测 Geist 左栏是悬停抬填充、当前项握着文字与图标色，别把横排那条推广过去。清单见同一份快照的「选中态与它的悬停」和「侧栏导航是例外」。
- 按钮悬停只抬填充：次级到 `color-mix(in srgb,var(--ink) 8%,var(--ground))`（Geist gray-200 那一档，全站同一个值），主动作到 `color-mix(in srgb,var(--ink) 88%,var(--ground))`，边框与文字色都不动。主动作那条悬停规则要自己写上 `color:var(--ground)`，否则同组更宽的通用 hover 里那句 `color:var(--ink)` 无人竞争，浅色实底上落成白字白底。禁用走 `--surface` 底、`--border-15` 边、`--muted` 字，不用 `opacity`；按下不加 `scale`。三条都有 Geist Button 源规则佐证。
- `outline:0`／`outline:none` 只允许出现在同一规则给出替代焦点样式的地方（`box-shadow` 或子元素 outline），或输入框由带 `:focus-within` 的容器接管焦点时；reduced motion 由全局 `@media (prefers-reduced-motion:reduce)` 统一关闭，不逐处补。
- Progress 必须有真实 `value/max`、可见单位与 `aria-valuemin/max/now`；分隔线放在完整指标（含进度条）之后。
- Switch 必须共享 radio `name`、初始一个 `checked`、键盘可用；布尔状态继续使用 Toggle。
- 菜单每项包含与入口相同的图标和文字，菜单内部滚动、`overscroll-behavior:contain`，不得把浏览器页面撑出滚动条。锚定菜单一律走 `wireAnchoredMenu`：高度压到触发钮那一侧真正剩下的空间（上沿是 `--topH` 顶栏下缘），装不下就在菜单内滚，不横跨触发钮；页面滚动关掉菜单，菜单自身的滚动不关。
- 只读查询（搜索、筛选）不配提交按钮：回车即执行，忙态落在表单自己身上（`form[aria-busy]` 加前缀原位换 Spinner）。有副作用的提交必须有按钮，且回车同样要能提交。表单里除这个输入框外还有别的字段时浏览器不做隐式提交，回车要自己接管 `requestSubmit()` 并跳过 `isComposing`。判据与实测见 `docs/reference-snapshots/vercel-forms-submit-affordance.md`。
- Spinner 只反馈用户直接触发的动作，触发器统一调用 `setActionBusy()`：写入 `aria-busy=true` 与 `aria-disabled=true`、视觉变灰、拦截重复触发，同时保持可聚焦；请求等待期不得再用原生 `disabled`，它只留给缺输入、无权限等动作确实不可执行的状态。未知时长的后台抓取使用 Loading Dots。整页或大区块首次取数使用 Skeleton 预留最终结构。Spinner／Loading Dots 保留可见状态文字；Skeleton 只保留给辅助技术的状态名，不另画「正在读取」文案。三者都尊重 reduced motion。
- 确认弹层一律走 `confirmModal()`：原生 `<dialog>` 承载，标题是陈述句，正文先说后果并点名
  涉及的两个值，主按钮是与标题同一动词的「动词+名词」，取消键就写「取消」，成功 Toast 与主
  按钮的动词一一对应。写入交给 `onConfirm`，忙态落在主按钮上，失败时弹层不关、原因留在正文
  下方等重试。形状与文案判据见 `docs/reference-snapshots/vercel-geist-modal-measured.md`。
- 用户写操作只在服务端终态成功后调用共享 `actionReceipt()` 发一条过去时 Toast；可由安全逆操作完整恢复的状态提供 8 秒「撤销」，永久删除、凭据、保存到账本等不伪造撤销。仅打开面板／菜单／Dialog 不算操作完成，不发 Toast；失败除短 Toast 外仍在原位置保留原因与重试入口。
- 同一次页面进入只呈现一段等待态；深链启动与页面取数复用同一个 Skeleton，禁止 Spinner 再切换成 Loading Dots 或 Skeleton。
- Skeleton 只覆盖真正等待的内容区；静态标题、导航和能同步得到的筛选控件立即显示。骨架必须复用最终容器的宽度、列数与对齐方式：卡片网格横向铺满，居中面板仍居中，不得用一列通用占位替代不同页面结构。
- 骨架里的占位不接指针、没有任何悬停反馈（`09-skeleton.css` 一处关掉）；要等数据才能执行的键用原生 `disabled` 加 `data-skeleton-action`，呈全站共用的禁用面（`--surface` 底、`--border-15` 环、`--muted` 字、not-allowed），不用 opacity，也不伪装成可点的最终档。
- 关注来源标签先服从来源记录的类型：只有明确标为 `general` 的标签才能进入卡片、顶部筛选和在线标签页，`artist`／`character`／`copyright`／`metadata` 与未知类型不得靠词形猜成 `general`。通用词清理是第二道门槛，只处理已经确认的 `general`；详情可显示全部来源标签，并按真实类型着色。
- 危险动作的悬停态一律 `--drop` 实底加白字。只描红边、红字的话，静止态和悬停态在暗色底上几乎一样亮，按下去之前看不出这是不可逆动作；带文字的销毁按钮全站一个写法，纯图标删除键不适用。Geist 的 error Button 同样是实心红填充，只是它静止态就红。实测见 `vercel-geist-controls-measured.md`。
- 下拉框的用途用框内左侧 16px 前缀图标标明，不在同一行挂一个文字标签：Geist 的文字 Label 是块级、排在控件上方，行内并排那种写法它没有，而工具行没有上方空间。无障碍名称改由 `aria-label` 承担。
- 按钮的前置图标只在图标指向对象（来源站点、当前选中项、平台）或形态方向（触发器右侧的 `chevron-down`）时出现；文字已经把动词说完的不加，`+ 添加`、`↻ 刷新`、`✓ 保存` 这枚多余字形会把同一行主次动作的视觉重量拉平。图标键必须给 `aria-label`，名称点出动作和对象、不描述图标形状。判据与 Geist Button 文档正文见 `docs/reference-snapshots/vercel-geist-button-icons.md`；官方 Geist 图标 SVG 没有可直取的入口，同一份快照记了原因。
- 同一行里的输入框和按钮共用 `--control-h`，不各写一个像素数：两个控件差 3px 就不是一行了，而差值往往来自窄屏那条防放大规则只抬其中一个。
- 一枚字形只代表一个意思，同一个意思也只有一枚字形。取字形先问它指的是哪个名词或哪个方向：文件类型取 `file-*`，本地取 `hard-drive`、订阅源取 `rss`，往下接一页取 `chevron-down`，原地换一批取 `shuffle`，`refresh-cw` 只归「去问一遍来源有没有更新」；筛不出结果和一次比对没有发现是两个空态，不能共用一枚。图标语义结合控件的可访问名称与实际操作复核。没有使用者的 symbol 从 `scripts/vendor_web_dependencies.mjs` 的名单和雪碧图里一起删，用户点名留的备用件在名单旁说明用途。
- 播放器控制条的窄屏折叠按播放器自身宽度判定（`ResizeObserver` 观察 `player.el()`），不用媒体查询：同一个视口下影院模式和普通视图的播放器宽度差一大截，用视口判据会在影院模式下白折叠、在普通视图下继续超框。门槛与提示外观见 `youtube-player-controls-user-screenshot.md`。
- 分页末尾、空页和「没有更多内容」是中性终止状态，用可关闭 Note；只有需要恢复或处理的故障才能进入红色 error Note。
- 弹层标题栏与滚动正文分层：标题分隔线属于卡片全宽，滚动条只属于正文。
- 没有直接证据不得新增动效。菜单开合的证据是 boardui `menu-styles.ts`（`docs/BOARD_UI.md`），已落在 board.css 的共用规则里；旧版 Geist 层仍无动画。
- Fieldset 的正文统一 20px 内边距；标题条与底部操作条同为 `--fieldset-bar-h`（52px）、竖直居中、左 20px 右 16px。标题放在框体里，不用原生 `<legend>`：它会在上边框上开缺口，同组卡片内容高度不同时缺口位置也跟着不齐。同组卡片必须同高，变化内容只放一个纵向 Scroller。
- 界面不解释数字是怎么算出来的。口径、免责、隐私声明、「不会做什么」和「按什么汇总」都不写：这个库只有一个用户，定口径的就是他本人。只留他要据以决定或操作的东西：读数本身、不可逆动作的作用范围、正在发生的事。单位跟着数字走（`497 项`），不另起一行说明。
- 上一次跑完的结果不常态显示。它是那一刻的快照，进页面就铺开会被读成现在的状态，而页面上没有任何东西说它是旧的；只有仍在进行的任务才自动接管页面。
- Empty State 的标题和说明必须同处组件内；全页空态与上方工具条统一留 16px，不得再套一层空卡片。
- 任何先请求再重绘整页的入口都必须绑定导航代际；只比较 `location.pathname` 不能防住「离开后快速返回同一路径」的旧响应。

## 验收门槛

- UI 标签、身份、反馈状态和搜索推荐属于语义契约：按实际影响验证数据层、组件或浏览器行为。
  推荐词上线前对真实 `/api/items` 验证至少一个命中，说明性后缀不得
  混入搜索词（坏例 `ABW 番号`，好例 `ABW`）。

开发中通过标准测试入口显式选择最小影响域；浏览器问题只跑对应 E2E 分片，先取得失败证据，再修到通过。
相关分片通过且代码不再变化后运行一次 `auto`；它选择 `full` 时，不为每次局部修复重复全量测试。
环境或资源失败先修环境并重跑失败域，最后只保留一次覆盖最终代码的有效 `auto`／`full` 记录。

1. 先确认失败模式与现有覆盖，再选择组件、API／数据层或浏览器测试。纯文案、等价重构不补源码文本断言；通用规范由 lint 或集中扫描检查，具体分工见 `docs/TESTING.md`。
2. Windows 从隔离 worktree 根运行 `& .\scripts\test.ps1`，默认 `auto`；按影响域补测，规则见 `peach-worktree`。
3. 桌面与 390×844 的通用不变量（无横向溢出、无越出视口的元素、等待态会结束、控制台无错误、无失败请求）由 `web` 域的 `tests/test_web_e2e.py` 执行，不再逐页手测；新路由加进 `frontend/e2e/smoke.test.ts` 的 `ROUTES`，分区 tab 由用例逐格切过去再测。用例靠 `aria-busy` 与 `data-skeleton` 判断页面稳定，React 子树也要写：BoardUI 组件不带加载态，由 Peach 包装件补上。浏览器里发现的可判定问题（几何、computed style、状态切换），先在 `frontend/e2e/` 写出失败用例再修。页面迁到 React 时，旧源码断言按 ADR-0031 分三类再删：设计决定进 `frontend/e2e/design-*.test.ts` 或 lint，行为进 vitest，布局与运行期进冒烟。
4. 浏览器取证只留给用例表达不了的：新布局首次成形、对齐外部参考、hover／focus 与观感判断。默认用 `agent-browser`（全局 CLI，装法 `npm i -g agent-browser`；
   环境变量 `AGENT_BROWSER_EXECUTABLE_PATH` 指向本机 Chrome，`AGENT_BROWSER_SESSION=<任务名>` 隔离会话）：`open <url>` → `wait <选择器>` → `snapshot -i -c -s <选择器>`／`get styles <选择器>`／`eval <js>`／`screenshot <文件>`，验完 `close`。
   Bash 工具里每条命令加 `</dev/null` 或把输出重定向到文件：接管道时 CLI 会等 stdin 直到工具超时。残留守护进程核对任务归属后按 PID 清理，不按进程名批量结束。
   托盘服务发的是主检出的 `web/`：从 worktree 根另起实例，`python -m peach serve --host 127.0.0.1 --port 8099 --no-mdns --no-ledger-sync`（`PYTHONPATH=<worktree>/src`），验完停掉。点第一下之前先把 `peach.settings.v1` 的 `detailAutoplay` 置 false：用户就在这台机器旁，点进详情会出声。
5. 接口字段、sidecar 取值和锚点算术走 Python 严格 HTTPS（项目 CA 加 `ProxyHandler({})`）核对，不进浏览器。浏览器只留给布局后才成立的事实：`getComputedStyle`、真实裁切几何、hover／focus 态和实际像素；用读源码顶替这几样是降精度。
6. 一轮验收要读的页面状态先列全，再合成一次 `agent-browser eval` 取回一个 JSON。桌面应用的 Browser 面板只在要让用户当场看画面时才开：私有网络主机（`.local` 与 `10.`／`172.16-31.`／`192.168.` 段的局域网 IP 归同一类）在那里只能逐次授权，站点级放行只给只读工具，点击与执行 JS 拿不到常驻许可，每多发一次调用就多一次弹窗。
7. 按 `peach-surfaces` 报告数据层、API、页面、契约、测试与文档各影响面；未部署不得称为生产已生效。
