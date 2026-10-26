# Vercel Geist Note／Progress／Switch 与 Analytics 证据（2026-08-30）

## 取得方式与锁定值

- 官方页：<https://vercel.com/geist/note>、<https://vercel.com/geist/progress>、<https://vercel.com/geist/switch>。
- **不登记进 `docs/reference-sources.json`**：下面锁的是当次 HTML 的 SHA-256 和登录态截图，
  仓库里没有可比对的上游快照文件；登记表要求的是「快照文件 + 可重抓 URL」这一对。
- Windows Schannel 路径反复返回凭据错误，抓取改用 OpenSSL 构建的 curl，请求头为 `Accept: text/html,application/xhtml+xml` 与 `User-Agent: Mozilla/5.0`。
- 页面 HTML SHA-256：Note `18FF709BAE3813D8153DE2631793280614B1290941E4BD11D67026EC17E2B5FC`；Progress `2DD846E2EC4A8ADF7D3FD1F5C6CE4ACCEA95E29D71881DF8AC6F6874290AD744`；Switch `075696BCEEB320D2267179AA86E1B037D7607C977DA7B1E6B93366A9990EF6A5`。
- 登录态 Analytics：`https://vercel.com/<account>/<project>/analytics`，在登录态浏览器里读取
  DOM 与 1178×900 当前视图截图；导航、全局筛选、三个摘要指标、图表、维度 tablist、数据行与
  空状态均已取得。
- 登录态 Speed Insights：`https://vercel.com/<account>/<project>/speed-insights`，2026-08-30
  重新取证成功。浏览器首次直接导航等待 30 秒后报 `js execution timed out; kernel reset`，但标签页
  实际已经到达目标；重连后用当前标签取得 1192×911 截图、完整可访问 DOM 与计算样式。当前页面
  资源清单含 31 个 CSS；清单 SHA-256 为
  `57409CE91609EEEA61F42C250676BABCADBDEEF386414E9C3CA2BCA9785C03A6`。其中承载
  `w-[220px]`、`min-w-[220px]` 与 `overflow-x-auto` 等当前 utility 的
  `047a0cxj5ow-g.css` SHA-256 为
  `E2179DA34B52A05D0A8782AEFB4E9C1B730C06A7D90B08CE7E12A63A95B77B61`。

## 可复用语义

### Note

- Note 是紧邻字段、卡片或分区的持久上下文；底层状态改变前持续存在。
- 2026-08-30 重新打开官方页取得当前桌面截图、可访问 DOM 与锁定 HTML；默认 Note 左侧是
  14×14、16 viewBox 的圆圈 `i`。Peach 不复制未开放许可的 Geist 私有 SVG，继续复用本地固定版
  Lucide `info`；图片详情、只读提示与中性 Note 因而使用同一枚圆圈 `i`。
- `error` 表示用户必须修复的问题，`warning` 表示需要理解后果，`success` 表示已通过检查，普通中性说明使用默认或 `secondary`。
- 页面／系统级且带动作的问题用 Banner，瞬时确认用 Toast，销毁确认用 Modal；不要把这些表面都画成 Note。
- 一条 Note 只讲一个概念；标签 1–2 个词，正文用一句主动句说明影响。Note 不加临时关闭按钮。

### Progress

- Progress 只用于已知总量的确定性进度；必须提供真实 `value`、`max`、文本单位与 `progressbar` 无障碍值。
- 1–3 秒未知进度使用 Spinner，行内保存使用 Loading Dots，比例／配额才考虑 Gauge；不能拿装饰性蓝条冒充进度。
- 阈值颜色必须来自同一业务阈值，阶段 stop 必须代表真实、有名称的阶段。

### Switch

- Switch 是 2–3 个互斥视图的分段选择器，使用 radio 语义、共享 `name`，初始恰好一个选中。
- 布尔开／关使用 Toggle；超过 3 项或长标签使用 Tabs／Select。
- 控件宽度必须容得下最长标签，图标项也必须有可访问名称；图标解释可复用 Tooltip。

### Analytics 数据面板

- 页面先提供环境、日期范围等全局筛选，再给 Visitors／Page Views／Bounce Rate 等并列摘要指标。
- 数据区以有边界的面板承载；面板标题有独立 header 分隔线，正文承载指标、tablist、表格或空状态。
- Pages／Routes／Hostnames、Referrers／UTM Parameters、Countries、Devices／Browsers、Operating Systems、Events、Flags 使用 tablist 在同层信息间切换，而不是把所有维度堆成装饰性 Bento。

### Speed Insights 数据面板

- 顶部先用 `Desktop`／`Mobile` radio 分段选择器和环境／日期筛选收窄口径；指标带保持同一横向
  滚动层，不因窄视口把每个指标拆成独立卡片。
- 实测指标单元宽 220px、高 97px、内边距 16px、圆角 0；选中项背景为 `rgb(10,10,10)`，
  其余为 `rgb(0,0,0)`。页面正文字体为 GeistSans，14px／24px；页面底为黑色，正文为
  `rgb(237,237,237)`。
- `Real Experience Score` 的顶部紧凑 progressbar 为 32×32px，主详情 progressbar 为
  64×64px。主详情左侧同时给数值、阈值、解释和文档链接，右侧给时间图；下方 Routes／Paths
  继续使用 tablist，并按 Poor／Needs Improvement／Great 同层分组，不再套一层 Bento。
- Peach 不伪造体验分或时间图。统计页把横向指标带用于库存、观看、覆盖和系统盘的真实快照；
  口味页把 Desktop／Mobile 的位置换成真实的「浏览器记录／Peach 内部」证据来源，主详情只展示
  可解释的访问／观看计数与确定性覆盖进度。

## Peach 有意差异

### 关注任务容器复核（2026-09-05）

HTTPX 取得 `https://vercel.com/geist/fieldset` 当前 HTML，SHA-256 为
`6a5bde91b9d131014d4837763227457711eee2575e68994a1d9c51a44948aa0c`。
正文提供 Without Footer 示例，DOM 使用 `group/fieldset material-base`、正文 `p-5`；
关注任务复用现有 Fieldset 的 20px 正文内边距、项目表面 token 与真实来源计数 Progress，
不加无动作的页脚。官方浏览器读取两次报 `js execution timed out; kernel reset`，
当前交互与计算样式未取得；本地桌面与手机另行验收。
此段是人工笔记，不登记到可变上游快照注册表。

- Peach 是无构建的原生 HTML／CSS／ES module，不引入 Geist React 包；复用语义、状态与可测量层级，统一实现位于 `web/js/ui-components.js`。
- `/stats` 当前没有环境与日期参数，不复制无消费者的筛选器；使用有 ARIA 的指标 tab 切换库存、
  观看、覆盖和系统盘主详情，再用同层 tablist 切换标签、最近观看和标签来源。
- `/taste` 保留现有真实时间窗；以 radio 切换浏览器／Peach 证据，摘要指标、主详情和排行面板随
  证据来源切换。宽屏使用一个 1100px 阅读列，窄屏指标带内部横向滚动，页面本身不得横向溢出。
- 关注页的可关闭历史失败是页面级 Banner，不是 Note；检查失败明细、资源同步失败和口味加载失败使用持久 error Note。

## 2026-09-06 提示与任务进度复核

官方页面：<https://vercel.com/geist/note>、<https://vercel.com/geist/progress>、<https://vercel.com/geist/gauge>、<https://vercel.com/geist/project-banner>、<https://vercel.com/geist/context-card>、<https://vercel.com/geist/separator>、<https://vercel.com/geist/loading-dots>。通过 HTTPX 严格 TLS 获取页面 HTML/CSS，并用浏览器核验本地组件。

HTML SHA-256：Note `f3471155164c8e0085419ce04e15928463cb526b4a52b51483c214fd84b645dc`；Project Banner `9bcc6478e468ecb48d146b774c36ceed62439ee78cc51e5ecf691c2c728204c8`；Context Card `ab1891a9600b3f019ffc3540ad2ca340b569b4261ced74babe0996f08c5e3210`；Gauge `a7303d848ea4dca4f865d5f7bbff467080015e6a771f4c900eaa7b91b0c3b4ab`。

- NoteAction 在提示内部放置一个操作按钮；需要处理的失败使用 filled 浅色背景。进度仅在数值变化时平滑过渡 200ms，减少动态效果偏好下关闭过渡。
- Note 使用语义色、12px 图文间距、14px 正文，成功为蓝色。默认无填充，filled 用低饱和色底；small 用于紧凑说明。Note 持续到状态解决，不额外加关闭键；短成功回执仍用 Toast。
- 页面后台任务使用 Project Banner：正文 14px，最小高度 40px，上下 8px、横向 24px；处理链接持续带下划线。横幅内用 32px Gauge 表示任务完成比例，这是 Peach 的用途差异；详情仍用 Progress。
- Gauge 用于实际已用／总容量，无有效总量时显示未取得。容量统一采用 80% 警告、95% 危险阈值，颜色与文字同档；任务完成率不使用容量阈值。
- Progress 按状态使用蓝、黄、红，而非把接近完成误判为危险；stops 仅用于真实阶段并带标签。更新准备使用后端的 65、67、90 阶段边界；浏览记录分阶段计数，不把不同单位拼成虚假的总体百分比。
- 局部未知总量任务使用带边界的 Fieldset 内容区与 Loading Dots，已知计数时添加文字与 Progress；不重复嵌套已有任务容器。
- Separator 用于同一容器内不同内容组时留缩进；卡片正文与页脚之间保持完整边界。相关设置共享一组时不强加分隔线。
- Context Card 用于凭据位置说明与图片元信息，复用原生 Popover 和锚定浮层；150ms 悬停，键盘 Escape 关闭，优先左对齐，空间不足时翻转和限制内部高度。短标签继续使用 Tooltip。

本节为人工取证笔记，不登记为可覆盖的上游快照。
