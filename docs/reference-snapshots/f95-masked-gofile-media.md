# F95 掩码链接与 Gofile 文件列表证据

- 取证日期：2026-08-28
- 用途：F95 回复里的媒体链接判定、Cookie 边界、Mix 触发条件。
- 凭据保护：取证只记录请求形状、状态、哈希和脱敏后的目标域名；Cookie 与令牌值未记录。

## F95 masked 链接

脚本：`https://f95zone.to/assets/js/masked.js`

- 响应：HTTP 200，1,255 字节。
- SHA-256：`E4863BA97D84C2B2FC459300F7621BEA5467FB922022FA139551154B6D9351E0`。
- 当前协议：向原 masked 路径 POST `xhr=1&download=1`，JSON 的 `status=ok` 时从
  `msg` 读取真实目标；服务也可能返回验证码状态。
- 已配置的 F95 Cookie 对样本请求有效，分别解析到 Gofile 与 Pixeldrain。Cookie 只应发送到
  `f95zone.to`，不能发送到解析后的文件站。

Peach 因此只把真实文件分发域或未解析的 masked 路径当成媒体候选；普通外链不计入视频数。

## Gofile 公开分享

官方页面与模块：

| 资源 | HTTP | 字节 | SHA-256 |
| --- | ---: | ---: | --- |
| `https://gofile.io/js/app.js` | 200 | 1,295 | `F1D7218BFC02E7260A145E0FA207A09238DF264F8E2A0F5977BD95DA8BC8B01A` |
| `https://gofile.io/js/pages/files.js` | 200 | 3,163 | `2F2675E7291EE61A159867EE03773A7F2382140149A5779DFE1819CFBB59E437` |
| `https://gofile.io/js/services/contents.js` | 200 | 7,226 | `C57C3E499A6C46F6212568EC810671C0C11C6034900466318CE4A39DB5B56DC3` |

当前网页通过 `GET /contents/{contentId}` 取目录，并同时发送 Bearer token 与短期
`X-Website-Token`。官方 API 文档也要求 Authorization token。无令牌返回 `error-token`；官网同款
游客令牌配网页签名访问两个样本都返回 `error-notPremium`。应用内浏览器打开样本页的导航超时，实时
DOM 与视觉结果为**未取得**。

结论：F95 Cookie 只能取得 Gofile 分享页，不能代替 Gofile 的账号令牌。没有取得实际文件列表时，
Peach 不把一个分享页或一条回复称为视频；只有至少两个已解析且可播放的视频才显示 Mix。
