# ADR-0065：Cloudflare 后面的来源由本机浏览器打开，验证由浏览器自己过

- 状态：Accepted
- 日期：2026-09-25
- 修订：ADR-0060 第五条（UA 走整站、Cookie 由用户贴）与「后果」第二条（自动过验证不做）；[开发约定](../../AGENTS.md) 「被 Cloudflare 拦的站一律放弃」那一条
- 依据：ADR-0060 的实测（两站只认浏览器过完验证发下的 `cf_clearance`）、`peach-reuse-first`（OpenAver 的做法作参照）

## 背景

ADR-0060 让 FC2PPV-DB 与 JAVten 收用户从浏览器里贴过来的 Cookie。上线一天的账：`cf_clearance` 在这两站
只活 30 分钟（2026-09-24 实测，站方设置，不是 Peach 的问题），一轮处理里两站各在第一条请求 403 后整站
冷却，那一轮 FC2 番号的女优栏与日文标题几乎全写「来源正在冷却」。要它们真的出资料，用户得每半小时
去浏览器里复制一次 Cookie，这不是「无感落库」（ADR-0052 的目标）。

OpenAver 的做法是让一个隐藏的 WebView2 打开站点、由它过 Turnstile，Peach 只拿它的会话。评估过两档：
嵌一个 WebView2（要 pythonnet 或 pywebview，加 15 MB 依赖，只在 Windows 上有）；驱动用户机器上已装的
Chrome 或 Edge（Chrome DevTools Protocol，只需要一个 WebSocket 客户端）。选后者。

## 实测（2026-09-25，Edge 154 与 Chrome 153，Windows）

| 项 | 结果 |
| --- | --- |
| 只开远程调试口 | `navigator.webdriver` 为真，Turnstile 连人工点击都拒绝（fc2ppv-db 点了照样 403） |
| 加 `--disable-blink-features=AutomationControlled` | `navigator.webdriver` 为假；fc2ppv-db 3.4～11 秒、javten 3～25 秒自动过，干净 profile 没人点 |
| 窗口放在屏幕外（`--window-position=-32000,-32000`） | 照样自动过；系统把坐标钳到 -16384，仍在屏幕外 |
| 页面内 `fetch(url, {credentials: 'include'})` | 同源 200；JAVten 搜索一跳的 Location 是 `http://`，`fetch()` 跟过去被按混合内容拦下（`Failed to fetch`），导航则照常走 |
| `Browser.setWindowBounds` | 一条命令同时带 `windowState` 与坐标只改状态不动位置，要分两步 |
| Edge 新 profile | 顶上挂「不受支持的命令行标志」警告条（`--test-type` 压掉）；拿 Windows 账号隐式登录并弹「正在同步你的浏览数据」 |
| fc2ppv-db 第一次进站 | 200 但落 `/ja/age-verify?returnTo=…` 年龄确认页（Next.js 客户端页）；到过这一页之后 profile 里有 `age-verified` Cookie，同一 profile 再取作品页直接到 |
| 同一出口一小时内几十次撞验证 | Cloudflare 把这个出口升为要人点的 Turnstile 框（「请验证您是真人」，勾选框在闭合 shadow DOM 里，页面脚本查不到 iframe），全新 profile 也一样；正常使用一站半小时一次不会撞到这档 |
| 发下的 Cookie | `cf_clearance` 30 分钟；javten 另有 `laravel_session`、`XSRF-TOKEN` 2 小时 |
| 会话级 `Network.setExtraHTTPHeaders` 带 Referer | Turnstile 勾选框那个子框架（`challenges.cloudflare.com/cdn-cgi/challenge-platform/…/turnstile/…`）的导航被 `ERR_BLOCKED_BY_CLIENT`（`blockedReason=other`）拦下，页上只剩转圈、`cf_chl_rc_ni` 每 50 秒涨一，人点不到；同一进程新标签页只挂 Accept-Language 就放行；Referer 经 `Page.navigate` 的 `referrer` 传，主文档照样带 Referer 且 5 秒过（Chrome 153.0.8010.54，生产进程内对照） |
| 放弃后留在最小化窗口里的验证页 | 每 50 秒自己重载一次，`cf_chl_rc_ni` 涨到 12（2026-09-25 生产进程内观察十分钟）；这些重试对出口评分的影响未测 |
| 依赖 | venv 里没有 WebSocket 库（只有 h11、anyio）；自写 RFC 6455 客户端 80 行够用 |

## 决策

**一、取页就是让浏览器导航过去，再把文档读回来。** `peach.browser_transport.BrowserTransport` 实现
`HttpTransport`，只发 GET：拉起本机 Chromium 系浏览器（`--remote-debugging-port=0`，端口从 profile 里的
`DevToolsActivePort` 读），只开一页，每条请求 `Page.navigate` 到地址，每秒看一次标题、`readyState` 与页头，
DOM 解析完（`interactive`）且不是验证页就用 `Runtime.evaluate` 读回最终地址、Navigation Timing 的
`responseStatus` 与序列化后的 `documentElement`，正文按 utf-8 编码、`content-type` 取 `document.contentType`。
Cookie、User-Agent 与出口三者天然一致，不再要用户贴 Cookie；请求头里只有 Accept-Language 经
`Network.setExtraHTTPHeaders` 挂在会话上（JAVten 按语言决定回日文原页还是译文页），Referer 经 `Page.navigate`
的 `referrer` 只落在主文档上——挂在会话上它会跟进 Turnstile 子框架的导航并被浏览器拦下。请求串行，一个进程一个
浏览器；连接方式不同（直连／带地址的 Peach 代理）各起一个。`Network.setBlockedURLs` 挡掉图片、字体与媒体，
只下载 HTML。不用页面内 `fetch()`：它只能同源，且跟到 `http://` 跳转会被浏览器按混合内容拦下。

**二、验证页三态。** 导航落到验证页（标题 `Just a moment...`／`请稍候…`，页头含 `cf-chl-`、`challenge-platform`）
时接着等：转了 `RESET_SECONDS`（15 秒）还没过，先只清掉该站的 cookie 重新导航一次——过期或被判无效的
`cf_clearance` 会让 Cloudflare 把这台浏览器扔进不放行也不给勾选框的重试循环（页上只有转圈，`cf_chl_rc_ni`
计数涨），等多久、点哪里都没用；2026-09-25 用生产 profile 实测，全新 profile 5 到 7 秒直接过，生产 profile
30 秒只转圈，删掉 `cf_clearance` 与 `cf_chl_rc_ni` 重载 5 秒就过，启动参数、语言头、资源屏蔽、出口节点都排除。
重载后 `AUTO_SECONDS`（40 秒）内过了就读文档，窗口不动；没过就把窗口放回屏幕内并顶到前面（先
`windowState: normal` 再给坐标，两步），同时登记到 `browser_transport.attention()`，`/healthz` 带出去供排查。
窗口顶到前面本身就是提醒，托盘不再弹系统通知：验证转圈时页上常常没有可点的框，通知只会打扰；再等
`CLICK_SECONDS`（120 秒）还没过就报 `ChallengeUnsolved`，`SourceTransport` 按拒绝访问那一档冷却
（`FIRST_BLOCKED_PAUSE` 起翻倍到 `blocked_pause`），措辞写「浏览器窗口再弹出时点一下验证即可」。同一站弹过
窗口没点过去就记进 `_unsolved`：之后再撞验证到自动时限就报，不再弹窗，直到哪次页面正常打开才
重新弹（实测 Cloudflare 升级判定后一次冷却到期撞一次，每次弹 120 秒窗口而页上无框可点）。过了或放弃
都把窗口最小化收起（挪回负坐标会被系统钳住，最小化确定不占屏幕）；放弃时还把页面导航到 `about:blank`，
因为留在窗口里的验证页每 50 秒自己重载一次、`cf_chl_rc_ni` 跟着涨，在没人看的窗口里一直重试。不用无头模式，
不伪造指纹，不接解题服务：过验证的是一台真的浏览器，人点不点由人决定。

时限与调用方的 `timeout`（这一条请求的预算，`SourceTransport` 已按本趟截止时间裁过）这样配合，都从导航那一刻
起算：一直没见到验证页时，`timeout` 内要解析完，否则报 `PageTimeout`（`BrowserUnavailable` 的子类，按连接失败
重试，浏览器进程留着）；见到验证页时，清 cookie 那一步在 `min(RESET_SECONDS, 自动时限)` 触发，重载后的
自动阶段最多等 `min(AUTO_SECONDS, timeout)`；弹窗那一段固定
`CLICK_SECONDS`，从弹出起算、不受 `timeout` 约束，因为人点验证的时间不归一部片的预算管。`_unsolved` 保证同一站
在页面正常打开之前只弹一次，所以一个站最多让整趟多等一次 `CLICK_SECONDS`，不会每部片拖一遍。

冷却记录带 `via`（`http` 或 `browser`），只认同一条路撞出来的账：直连 403 攒下的 `blocks` 与 `until` 在切到浏览器
传输时作废，浏览器那条路的 `ChallengeUnsolved` 从 `FIRST_BLOCKED_PAUSE` 起数；反过来没有浏览器、退回 httpx 时，
浏览器写下的冷却同样作废。浏览器路径成功取到页就清掉记录，与 httpx 那条路相同。

**三、站点的门由传输替人点。** `SOURCES[<来源>]["browser_gate"] = {"path", "button"}` 登记「点一下就过」的
页：导航落到那个路径就在页面里点文字匹配 `button` 且没被禁用的第一颗按钮，等地址离开那个路径
（`GATE_SECONDS` 15 秒）再读文档；没找到按钮或没跳回去就把门页原样交回，解析器自己认。眼下只有
FC2PPV-DB 的年龄确认页（`/age-verify`）。

**四、进程生命周期与浏览器选择。** 第一条请求拉起，`IDLE_SECONDS`（10 分钟）没有请求就 `Browser.close`；
进程退出、调试连接断开或页面脚本抛错都按断连处理：关掉、报 `BrowserUnavailable`，`SourceTransport` 把它
当连接失败（`httpx.TransportError`），取页层的重试会让下一次请求重新拉起。验证没过不算浏览器坏了，进程留着。
`atexit` 关浏览器，服务退出不留孤儿进程。profile 放在凭据根下 `browser/`：里面是 `cf_clearance` 与站点
会话，和贴进来的 Cookie 一个性质。Windows 上 Chrome 排在 Edge 前面：Edge 会拿 Windows 账号把新 profile
隐式登录并开同步，采集用的浏览记录会跟着进用户的微软账号；只有 Edge 的机器用 `--inprivate` 拉起，不登录、
不同步，代价是 Cookie 只活到进程退出，下次拉起再过一次验证（几秒）。所有浏览器都带 `--disable-sync` 与
`--test-type`（压掉「不受支持的命令行标志」警告条）。

**五、来源开关与退路。** `scraping_access.SOURCES` 里登记 `browser: True` 的来源（眼下 fc2ppvdb、javten）
走这条路；`find_browser` 找不到浏览器（`PEACH_BROWSER` 指定的、Windows 的 Chrome／Edge 安装目录、macOS 的
`.app`、Linux PATH 上的 `google-chrome`／`chromium`／`microsoft-edge`；Linux 无 `DISPLAY` 视为没有）或 Peach
代理地址带凭据（Chromium 不收命令行里的代理凭据）时，退回 ADR-0060 那条路：整站 UA、用户贴的 Cookie。
`describe()` 的 `browser` 字段告诉来源卡这台机器走的是哪条路，走浏览器的卡不再要 Cookie，「撤销 Cookie」
留着清旧的。macOS 是读者不采集，这条路在它上面不会被走到。

**六、不加依赖。** 与浏览器说话的是 `browser_transport._WebSocket`：本机回环、文本帧、客户端加掩码、不处理
分片（CDP 回包不分片）。CDP 只用 `Page.enable`、`Page.navigate`、`Page.bringToFront`、`Runtime.evaluate`、
`Network.enable`、`Network.setBlockedURLs`、`Network.setExtraHTTPHeaders`、`Browser.getWindowForTarget`、
`Browser.setWindowBounds`、`Browser.close` 十条。

## 后果

- 两站在有 Chrome 或 Edge 的写者机器上不再要 Cookie；一轮处理里它们该出的女优栏、日文标题与存储原件地址
  能出来。`cf_clearance` 半小时过期只表现为多一次自动过验证（几秒），不再整站冷却。
- 处理任务跑着时屏幕外多一个浏览器进程（约 150 MB 内存），10 分钟不用自己退出。它有自己的 profile，
  不碰用户日常用的浏览器和它的登录态。
- 验证真的要人点时，窗口出现在屏幕上；不点，两分钟后这一站冷却，链照常往下走，同一站之后不再弹。
- 读回的是浏览器解析后再序列化的 DOM，不是服务器发的原始字节：解析器拿到的 HTML 结构相同、字面略有出入
  （属性引号、自闭合标签、脚本已执行后的插入）。状态码来自 Navigation Timing，读不到时按 200。
- 导航走的是浏览器的网络栈：代理按来源连接方式换成启动参数，`environment` 模式跟系统代理，
  与 httpx 读环境变量的口径略有出入；Peach 代理地址带用户名密码的来源退回 httpx 路径。
- 其他被 Cloudflare 拦的来源（AVBase 等）要走这条路只需登记 `browser: True`，但每个来源的请求都多一层
  浏览器往返，只给确实过不去的站。
