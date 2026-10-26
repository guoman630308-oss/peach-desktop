# ADR-0094：登录页与错误页由页面包画，页面包免登录可取

- 状态：Accepted
- 日期：2026-10-04
- 相关：ADR-0004、ADR-0031、ADR-0050

## 背景

登录页、错误页和首次运行页原来由服务端拼字符串，样式来自 `web_entry.entry_page_style()`：它在运行时从
`web/css/` 与 `web/board-entry.css` 里用正则抽出一套手写色板和控件样式，内联进每一页。主界面早已换成
BoardUI，这三页的输入框、勾选框和配色仍是另一套，两边改一处就要对另一处。

ADR-0031 定下首次运行页由独立页面包 `peach-pages.js` / `peach-pages.css` 画，用户在 2026-10-04 定了入口页
全用 BoardUI 控件、三页都加载 Inter、主题认手动档。剩下登录页和错误页。登录页要在拿到会话之前出图，
而 `/dist/*` 一律经 `require_asset_auth`，设了访问密码的服务器上无会话取不到任何产物。

## 决策

**一、三页共用一张薄壳。** 服务端只吐 `<!doctype html>`、主题预读脚本、Inter、页面包的样式与脚本，以及一个
`#peach-page` 挂载点；哪一页、要画的数据写在挂载点的 `data-*` 上。拼壳的函数住在 `web_entry.py`，
`routes_auth` 与 `routes_pages` 都调它，`routes_*` 之间不互相依赖。骨架文案（标题、按钮、说明）归页面包，
服务端只给变量：登录页给净化过的 `next`、是否口令错误、限流或参数错误的文案；错误页给状态码与经
`_DEFAULT_DETAILS` 换成中文后的说明。

**二、登录仍是原生表单提交。** 页面包用 BoardUI 控件画 `<form method="post" action="/login">`，提交的字段名与
取值（`token`、`next`、`days`）不变，会话 cookie 仍由 `POST /login` 的 303 设下，登录不经脚本发请求。

**三、免登录只放两个文件。** `GET|HEAD /dist/peach-pages.js` 与 `/dist/peach-pages.css` 各注册一条字面量路由，
排在 `/dist/{name}` 之前，不挂 `require_asset_auth`，仍经 `asset_response` 给 ETag 与 `no-cache`；两条登记进
`test_fastapi_api.PUBLIC_ROUTES`。放行它们是安全的：它们是提交进 Git、随仓库分发的静态产物，仓库本身公开，
不读账本、不含配置与凭据，内容不随登录与否变化。页面包不 import 任何别的产物，图标只引用早已公开的
`/peach-logo.png`，Inter 来自无鉴权挂载的 `/vendor/`。其余 `/dist/*`（`peach-ui.js`、`peach-react.js`、
`peach-entry.js` 等）、`/js/*`、`/app.css`、`/board.css` 仍要会话；`peach-entry.js` 外置的 `/js/core.js`、
`/js/ui-sounds.js` 不放行，错误页也不再加载它们。

**四、`web_entry` 的色板退场。** `entry_page_style()`、`board_entry_style()`、`check_html`、`CHECK_SVG` 与
`web/board-entry.css` 删除；`web_entry.py` 保留运行信息（`runtime_fact_entries`，配置页 JSON 在读）和拼壳函数。

## 后果

- 三处可见变化：登录页与错误页换成 BoardUI 控件与 Inter 字形，像素随之变；登录页离开脚本就画不出来，和首次运行
  页一样，用户对首次运行页接受了这一点，登录页是「全用 BoardUI 控件」的推论（主界面本来就离不开脚本）；
  浏览器表单提交撞上限流（429）或保持登录天数不合法（400）时回登录页并在原位报错，不再吐一行 JSON，
  状态码不变，非 HTML 请求仍回 JSON。
- 错误页不再挂主界面的叠加滚动条，三页都用浏览器原生滚动条；它们都是一张卡片，只有窗口很矮时才出现滚动条。
- 登录页首帧多两次资源往返（样式与脚本），`no-cache` 下靠 ETag 复验，未改动时是 304。
- 往页面包里加东西就是往免登录面上加东西：它不得 import 需要会话的产物，不得内嵌账本、配置或凭据派生的数据。
