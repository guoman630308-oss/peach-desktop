# ADR-0093：PikPak 用浏览器登录，取网页端会话后只由 Peach 续期

- 状态：Accepted
- 日期：2026-10-02
- 相关：ADR-0089、ADR-0052

## 背景

ADR-0089 的 PikPak 登录是账号密码换安卓客户端的 token，经常要过人机验证，而且得把密码交给 Peach。
用户问 PikPak 能不能直接用浏览器里已经登录的会话，像用户脚本那样。PikPak Assistant 这类用户脚本的做法是
在 mypikpak.com 页面里读 localStorage 的 `credentials_*`，带上其中的 access token 调接口，续期交给网页自己。

Peach 不在网页里运行，不能借网页续期；它也不能去读用户日常浏览器的 profile。另外，PikPak 每次刷新都会作废旧的
refresh token，同一份会话只能有一个刷新者。取证见 `docs/reference-snapshots/pikpak-web-session-measured.md`。

## 决策

设置页 PikPak 卡的主按钮是「用浏览器登录」。Peach 用 `browser_transport` 的 CDP 通道拉起一个有人值守的浏览器窗口，
打开 `https://mypikpak.com/drive/` 并顶到前台。用户在窗口里登录后，Peach 每秒读一次 localStorage，读到带 refresh
token 和 access token 的 `credentials_<client_id>` 就取出 refresh_token、access_token、到期时间、用户编号和设备号，
存进 `download-pikpak` 凭据，删掉网页里的 `credentials_*`，然后关窗。实现在 `src/peach/downloads_pikpak_browser.py`。

**一、续期只归 Peach。** 取到会话后只有 Peach 刷新，窗口取完就关，不留一个会自己续期的网页。网页里的
`credentials_*` 删两次：读到后立刻删，下次拉起窗口时第一个文档在网页脚本之前再删一次，所以网页总是从未登录开始，
不会拿旧 token 去刷新，Peach 也不会把旧 token 当新登录读回来。设备号 `deviceid` 保留，同一 profile 下次登录还是
同一台设备。Peach 刷新被拒时不自动弹窗：对账线程在后台跑，没人看着；错误写明「PikPak 拒绝刷新网页登录的令牌」，
提示回设置页点「用浏览器登录」，由用户拉起。

**二、token 绑定签发它的 client。** client_id 从键名 `credentials_<client_id>` 的后缀读（多账号时去掉 `@<sub>`），
不写死，存进凭据的 `client_id` 字段。`downloads_pikpak.ClientProfile` 并存两套常量：安卓（有 secret、按当前时间签名）
和网页（`YUMx5nI8ZU8Ap8pm`，网页配置里没有 secret，签名用网页 bundle 里的固定时间戳）。刷新按凭据里的 client 走，
网页 client 的刷新请求体不带 secret，请求头补 `X-Client-Id`、`X-Sdk-Version`、`X-Protocol-Version`。不认识的 client
仍可刷新，但拒绝签名，报错提示先复制磁力手动添加。不猜 secret，也不把网页 client 的 token 拿安卓常量去刷新。

**三、Peach 专用的持久 profile。** profile 放在凭据根下的 `browser-pikpak/`，和采集用的 profile 分开，也不用
InPrivate：设备号要留在这里，下次登录才是同一台设备。浏览器沿用 `find_browser`，Chrome 优先。`_Browser` 加
`attended` 档：窗口开在屏幕上，不加 `--inprivate`，不屏蔽图片和字体。

**四、账号密码登录保留为第二选择。** 有浏览器时它收在卡片里「用账号密码登录」的折叠中；这台电脑没有 Chrome 或
Edge 时只剩它，直接摊开。安卓 token 的刷新、保存密码自动重登照旧。

**五、等待不卡接口。** 开始接口立刻返回，登录在后台线程里等，页面每两秒读一次状态。等满 10 分钟记为超时并关窗；
用户关掉窗口（调试连接断开或浏览器进程退出）记为已取消；页面上的「取消」立刻返回，后台线程随后关窗。已经在等时
再点开始不会开第二个窗口。三条接口只放行本机，写接口另验同源。

token 不进 URL、日志、ledger 和响应体，日志与错误只写键名。

## 被否决的方案

- **读用户日常浏览器的 profile 或 Cookie。** 要碰用户其他站点的会话，浏览器运行时文件也被锁着。
- **装一个用户脚本或扩展，把 token 推给 Peach。** 网页和 Peach 会轮流刷新，互相作废 refresh token；
  还得让用户另装扩展。
- **让网页继续续期，Peach 每次从网页借 token。** 要一直开着浏览器窗口。
- **用 InPrivate 窗口。** 每次都是新设备，PikPak 更容易要人机验证，设备数也会涨。
- **拿到网页 token 后改用安卓 client 刷新。** token 绑定签发它的 client，服务端会拒。

## 后果

- PikPak 重新发布网页时，签名盐表和固定时间戳可能更换。错误文案把「登录失效」和「网页端常量可能已更新」分开报，
  后者要重新取证、更新 `ClientProfile`。
- 服务端是否接受不带 secret 的网页 client 刷新，还没有用真实账号验证，列为上线验收项。
- Edge 的持久 profile 第一次打开时可能弹账号同步提示，用户关掉即可。
- 登录窗口读的是 Peach 专用 profile，用户要在里面登录一次，日常浏览器里的登录状态不会自动带过来。
