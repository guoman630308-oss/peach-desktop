# FANBOX 浏览器传输证据

- 取证日期：2026-08-28
- 用途：解释 FANBOX `post.info` 为什么不能继续使用普通 HTTPX，并锁定最小复用边界。
- 凭据保护：现场复用了管理页已保存的 FANBOX Cookie，但本文不记录值、长度或字段。

## 上游实现

PixivUtil2 固定 revision `fc0f9adf44e590dde9242b3755b53528f159720a` 的
`common/PixivBrowserFactory.py` 在 FANBOX 请求路径使用 `curl_cffi`，设置 Firefox
impersonation，并只把 Cookie 发送给 FANBOX 域名。该文件 SHA-256 为
`1FEA36D5C0C284E500B260A65B89229B3E231EE8419DE261E535E8AB053E702B`；仓库采用
BSD-2-Clause，LICENSE SHA-256 为
`CD938A4736B48A9D3AADF772AE2DBFA204097F278FD327551ACFA4A8D6417B8D`。

Peach 不复用 PixivUtil2 的下载器、登录、数据库或 Cookie 管理，只采用这条已验证的传输选择。

## 固定依赖与本机可用性

- `curl_cffi==0.16.2`，tag revision
  `b2f76eaa1f39851f8fb10171272d60300aec17ea`，MIT。
- Python 3.14.7 / Windows x64 的依赖解析命中
  `curl_cffi-0.16.2-cp310-abi3-win_amd64.whl` 与
  `cffi-2.1.1-cp314-cp314-win_amd64.whl`。
- Peach 固定 `firefox147`；`curl_cffi 0.16.2` 的已支持 Firefox 档位止于 147，不指定未实现的档位。

## 现场复现

目标：`GET https://api.fanbox.cc/post.info?postId=12228983`。

使用 Peach 凭据存储中的 FANBOX Cookie、官方 `Origin`、目标帖 `Referer` 与
`firefox147` 传输后：

- HTTP 200，`application/json`，6,961 字节；
- SHA-256 `039DCE0418EBCA7703CC2277C8B9B803717D84C6DAF7989F6D55722DDA577653`；
- `body.post` 存在，`imageMap` 为 6 图，正文为 20 个 blocks。

同一 Cookie 走普通 HTTPX 会得到 403 或 `general_error`，所以失败点是传输指纹而不是管理页
存错 Cookie。

## Peach 边界

- 公开列表仍走共用 HTTPX；只有 `api.fanbox.cc/post.info` 走浏览器传输。
- 仍受统一超时和最大响应大小约束，不自动重试。
- Cookie 只发给 FANBOX API；Gofile 和其他站点不会继承。
- 不执行 JavaScript、不解 Turnstile/DDoS 质询、不登录、不读取付费或受限帖子。
