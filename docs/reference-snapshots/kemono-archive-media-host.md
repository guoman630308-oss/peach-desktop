# 归档站静态资源主机实测

- 取证日期：2026-08-30
- 取证方式：`curl` 带常规浏览器 UA，无 Referer，记录状态码、`content-type` 与字节数；
  不跟随重定向以外的处理，不带任何凭据
- **不登记进 `docs/reference-sources.json`**：那张表要求每个来源有可哈希的上游快照，
  这里记的是「哪个主机能取到静态资源」，站点改版就会变。复核就按下面的方式重测。

## 起因

关注页上 pawchive 的卡片一律没有封面，详情里的媒体也是空白。前端对封面用
`onerror="this.remove()"`，所以取不到时表现为「没有预览图」，看不出是 404 还是防盗链。

## 实测

同一份路径分别请求主域与 `img.` 子域：

| URL | 状态 | content-type | 字节 |
| --- | --- | --- | --- |
| `kemono.cr/thumbnail/data/7e/6b/<hash>.jpg` | 302 | text/html | 138 |
| `img.kemono.cr/thumbnail/data/7e/6b/<hash>.jpg` | **200** | image/jpeg | 24,050 |
| `pawchive.pw/thumbnail/data/3f/9c/<hash>.gif` | 404 | text/html | 207 |
| `img.pawchive.pw/thumbnail/data/3f/9c/<hash>.gif` | **200** | image/gif | 12,796 |

结论：**两站的静态资源都应走 `img.` 子域。**kemono 主域只是重定向，浏览器跟随之后
仍能显示，所以这个问题在 kemono 上看不出来；pawchive 主域直接 404，卡片因此永远是空的。

`follow_sources.py` 里原来那条注释记的是 2026-08-27 主域回 200 `image/jpeg`——
站点行为在这三天里变了。**这类"当前哪个主机能取到"的结论必须带日期，并且会过期。**

## 原始文件（非缩略图）

缩略图和原始文件走**不同的主机与路径**，而且三站规则并不一致。真实 URL 不是猜出来的：
pawchive 的帖子页是服务端渲染的，`<source src>` 里就写着；kemono 是 SPA 外壳，
但它的主域会用 302 告诉你当前节点。**别再逐个试子域了，问站点自己。**

| 请求 | 结果 |
| --- | --- |
| `pawchive.pw/<path>`（旧代码拼的） | 404 |
| `pawchive.pw/data/<path>` | 404，主域对 /data 也不重定向 |
| `file.pawchive.pw/data/<path>` | **206** video/mp4，支持 Range |
| `kemono.cr/data/<path>` | 302 → `n1.kemono.cr/data/<path>` |
| `coomer.st/data/<path>` | 302 → `n4.coomer.st/data/<path>` |

两处结论：

1. **路径要带 `/data` 前缀。**旧代码拼的是 `https://<主域><path>`，少了这一段，
   三站的原始文件都取不到——不只是 pawchive。
2. **主机分两种处理。**kemono/coomer 走主域让站点自己 302（`nX` 的编号会变，
   写死会过期）；pawchive 主域对 `/data` 直接 404，必须点名 `file.` 子域。

`follow_stream._allowed` 按后缀匹配原站域名，`file.` / `n1.` 这些子域天然在白名单内，
安全边界没有放宽。

## 连通性：kemono 的文件节点未取得

`n1.kemono.cr` 在本机网络下连不上（curl 返回 000），而同站的 `img.kemono.cr` 正常
200。所以这不是 URL 规则问题，是那个节点在本网络不可达。

`HttpxTransport` 用的是默认 `httpx.Client()`：`trust_env=True` 会读 `HTTPS_PROXY`，
`follow_redirects=True` 会跟随 302。**要走代理不必改代码**，给服务进程设环境变量即可。
是否要为归档站配代理是网络决策，留给用户。

## 复核方式## 复核方式

```bash
curl -s -o /dev/null -w "%{http_code} %{content_type} %{size_download}\n" \
  -A "Mozilla/5.0" "https://img.pawchive.pw/thumbnail/data/<path>"
```

`<path>` 取自 `follow_item.thumb_url` 里 `/thumbnail/data` 之后的部分。
