# PikPak 网页端会话与用户脚本取证

- 取证日期：2026-10-02
- 用途：ADR-0093「用浏览器登录」取网页端会话的依据，包括存储键、设备号、刷新请求与签名常量。
- 凭据保护：只下载公开的网页脚本和用户脚本，没有登录 PikPak，也没有读任何真实 token。本文只记录键名和常量，不记录值。
- 不登记进 `docs/reference-sources.json`：网页脚本是带内容哈希文件名的打包产物，PikPak 每次发布都会换文件名和字节，
  没有可以重新抓到同一份字节的固定地址。登记表只收能 `accept` 重抓的上游原文，所以这里只记 SHA-256 作为当次证据。

## 网页端（`https://mypikpak.com/drive/`）

| 文件 | SHA-256 | 取到的事实 |
| --- | --- | --- |
| `drive.html` | `2a6d14e1d409eb3cc476e247bfe1b7efdb8bbc6b2dbffeec443182f416aedfe1` | 入口页，引用下面几个块 |
| `main.27112fa0.js` | `cc673003a3edde6e5fe18f5a1a8f8dd1a678eef68d88d202cfb99fbb13fde928` | `captcha_sign` 为 `"1."` 加上对 clientId、版本、包名、设备号、时间戳拼串逐条加盐 md5；meta 带 `captcha_sign`、`client_version`、`package_name`、`user_id`、`timestamp`；请求头带 `x-client-id` 与归一化后的 `x-device-id` |
| `5990.326e2dc8.js` | `b44d6b16eb3c6105838c6997948f5f8e94a5ae27fb2a0d5b7a62d27cbda90bcc` | 模块 65558：clientId `YUMx5nI8ZU8Ap8pm`、clientVersion `2.0.0`、packageName `mypikpak.com`、固定 timestamp `1790733736477`、15 条盐；模块 22550：设备号取 `deviceid` 或 `_deviceid`，按 `.` 切开取最后一段的前 32 位；模块 87827：用 `credentials_<clientId>` 找当前用户 |
| `3765.04250f78.js` | `8c092b516bdb0bf61b733358ac602dd415cf4ca1c26f67495a2fc608a328e8b8` | 登录 SDK 的初始化参数：clientId 同上，接口源 `https://user.mypikpak.com`，没有 clientSecret（三个应用块里 `clientSecret` 命中 0 次） |
| `7991.17876548.js` | `14fc80d7da72d4552ec45175b3e6b03f34f0a7e1da1715d028299f31f8e879d0` | 363 个懒加载块里唯一含 `/v1/auth` 的一块，登录 SDK 8.1.4：刷新是 `POST /v1/auth/token`，请求体 `client_id`、`client_secret`（取配置，网页端为空）、`grant_type=refresh_token`、`refresh_token`；请求头 `x-client-id`、`x-sdk-version: 8.1.4`、`x-protocol-version: 301`，有设备号时带 `x-device-id`；凭据存在 `credentials_<clientId>`，多账号时是 `credentials_<clientId>@<sub>`；`expires_at` 存成 ISO 字符串，写入前已扣掉 120 秒或 30 秒余量 |
| `2572.b2bb8d5f.js` | `38e5a864881f61b1b5c9750a6e02f00d29fef39990f6bf7fe4db685084f026b4` | 同次抓取的页面块，没有从中取用事实，只留哈希备查 |

签名实现用网页脚本里同一段 `reduce` 写法在 Node 里算一遍，再和 Peach 的 Python 实现对照：输入设备号 `abc`、
网页常量时，两边都得到 `1.e07351bd60bb92ce14b49dcf7273008f`。盐表和时间戳由程序从脚本原文抽出比对，一致。

## 用户脚本 PikPak Assistant v2.4.3

- 地址：`https://update.greasyfork.org/scripts/587487.user.js`，MIT，SHA-256
  `96d605413a3f666c19aff8fbc3374e3facff81b30ca327e9c52943463127fcc8`。
- `@match` 为 `mypikpak.com/drive/*`、`app.mypikpak.com/*`、`drive.mypikpak.com/*`。
- `getHeaders()` 遍历 localStorage：`credentials*` 取 `token_type` 加 `access_token` 作 `Authorization`，
  `captcha*` 取 `captcha_token`，`deviceid` 原样读，组成 `Authorization`、`x-device-id`、`x-captcha-token` 三个请求头。
- 脚本从不自己刷新 token，刷新完全交给网页。

## Peach 的取舍

- 和用户脚本一样从 localStorage 的 `credentials_*` 取会话；不同在于用户脚本借网页续期，Peach 取走后由自己续期，
  所以取完删掉网页里的 `credentials_*` 并关窗，避免两边轮流刷新、互相作废 refresh token。
- 设备号按网页 app 自己的取法归一化（取最后一段前 32 位），不照用户脚本原样读。
- client_id 从键名后缀读，不写死；网页端没有 secret，Peach 也不带。

## 未取得

- 网页端 `/v1/shield/captcha/init` 的请求体形状：没有在打包块里定位到，Peach 沿用安卓端的形状，只换常量。
- 网页登录 SDK 里 statInfo 的 `deviceId` 与 `deviceSign` 来源。
- 服务端是否接受不带 `client_secret` 的网页 client 刷新：要用真实账号验证，列为上线验收项。
