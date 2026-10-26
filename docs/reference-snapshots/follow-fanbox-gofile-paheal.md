# FANBOX 多媒体、Gofile API 与 Paheal 订阅证据

- 取证日期：2026-08-28
- 用途：FANBOX 多图切换、Gofile 文件页展开、Paheal 标签订阅与跨站去重。
- 凭据保护：只记录公开响应结构、状态、字节数和 SHA-256；API token 只用于现场状态验证，未记录。

## FANBOX 官方帖子详情

请求：`GET https://api.fanbox.cc/post.info?postId=12228983`，带 FANBOX 要求的公开
`Origin` 与 `Referer`。

### 2026-08-28 纠正记录

本快照最初把 HTTP 200 误写成普通 HTTP 客户端可稳定复现。当天重新核对后，HTTPX 和浏览器
地址栏分别得到 HTTP 403 或 `{"error":"general_error"}`；保存的 FANBOX Cookie 本身没有
填错。使用 `curl_cffi==0.16.2` 的 `firefox147` 传输特征、同一 Cookie、`Origin` 和帖子
`Referer` 后，才重新得到下述完全相同的 6,961 字节响应与 SHA-256。

这项纠正只改变传输假设，不改变响应结构和产品边界：Peach 不执行网页脚本、不求解质询，
只回查公开、免费且不受限的官方 `post.info`；Cookie 仍只发给 `api.fanbox.cc`。

- HTTP 200，6,961 字节，SHA-256
  `039DCE0418EBCA7703CC2277C8B9B803717D84C6DAF7989F6D55722DDA577653`。
- 顶层结构为 `body.post`；帖子免费且不受限。
- 正文 `body.blocks` 内含 `https://gofile.io/d/OS2Qz9`。
- `body.imageMap` 有 6 张图片，原图和缩略图均由官方响应直接给出。

Peach 因此在列表请求后有界回查公开详情：按正文块顺序保留全部图片，详情右侧使用
Mix 同款队列切换；正文中的文件站链接继续作为可点击资源页显示。

## Gofile 官方 API

官方文档：`https://gofile.io/api`。内容列表请求为
`GET https://api.gofile.io/contents/{contentId}`，账号 API token 仅放在
`Authorization: Bearer` 请求头。

官方文档在 2026-08-28 复核时明确说明：所有 API 请求都要 Bearer token，大多数 API
端点只向 Premium 账户开放；超出端点限流时返回 HTTP 429。

- 未带 token 请求 `OS2Qz9` 返回 34 字节
  `{"status":"error-token","data":{}}`，SHA-256
  `C745FF20204557E4775521417A9351E949E9BBB0B3078582649BF961FC513C61`。
- 用户所说的 21 个视频在没有 token 时**未取得**，不能把分享页文字当成视频数。
- 用本机已保存的 32 字符 token 请求 `GET /accounts/getid` 返回 HTTP 200、`status=ok`，
  证明 token 有效；同一 token 请求 `GET /contents/OS2Qz9` 返回 HTTP 401、
  `status=error-notPremium`。拒绝原因是账户套餐，不是 token 错误。

Peach 新增独立的 Gofile API token 输入；配置后递归读取 API 返回的 `children`，只保留
MIME 为图片或视频的文件。文件直链留在服务端 metadata，浏览器只收到媒体序号、名称、
类型与缩略图；播放由 `/follow-stream` 代理，token 仍只进上游请求头。

FANBOX Cookie 是另一份本机可选凭据，只发给 `api.fanbox.cc`，用于公开 `post.info`
被验证页拦截时复用用户自己的浏览器会话；Gofile 跨站请求不会继承这份 Cookie。

## Rule34 Paheal

标签页：`https://rule34.paheal.net/post/list/InitialA/1`

- HTTP 200，92,657 字节，SHA-256
  `E2752C3C07258BF7A1C5DAD62369C6BC2306F438E6733F287341DB0CB932DC67`。
- `.shm-thumb[data-post-id]` 直接给帖子 ID、扩展名、标签、缩略图和文件地址。

详情：`https://rule34.paheal.net/post/view/7428820`

- HTTP 200，34,035 字节，SHA-256
  `5E59D3D1509FCE478633D2CEE6DFE22DD60C205A03BAAA7A7FCE297339779091`。
- `video#main_image source` 给 MP4，`poster` 给封面；信息表给精确 UTC 时间、28.4 秒、
  标签、上传者和原始出处 `https://subscribestar.adult/posts/2639932`。

Peach 以标签页为有界列表、逐条详情补全出处。该帖子归一为
`subscribestar:2639932`，与其他来源声明的同一 SubscribeStar 帖精确合并；标签拼出的
展示名不参与标题模糊去重。
