# 来源图标

这些图标用于来源设置与诊断页的站点身份，归各站点所有，不用于 Peach 品牌。favicon 保留原色与画布，ICO 仅解码为 PNG。

| 文件 | 来源 | 获取原因 |
| --- | --- | --- |
| javten.png | JAVten 的公开图标缓存：`https://www.google.com/s2/favicons?domain=javten.com&sz=64` | 官方首页与图标端点返回 Cloudflare 403，favicon.ico 返回空文件 |
| fc2ppvdb.png | FC2PPV-DB 的公开图标缓存：`https://www.google.com/s2/favicons?domain=fc2ppv-db.com&sz=64` | 官方首页与图标端点返回 Cloudflare 403 |
| avwikidb.png | `https://avwikidb.com/apple-touch-icon.png` | 首页有验证页，公开图标可读取 |
| minnano-av.png | みんなのAV 的公开图标缓存：`https://www.google.com/s2/favicons?domain=minnano-av.com&sz=64`（缓存只有 32×32） | 官方 apple-touch-icon 与 favicon.ico 对 HTTP 客户端返回 Cloudflare 403 |
| github.png | `https://github.githubassets.com/favicons/favicon.svg` | 官方声明的矢量图标，经现有 `link_marks` 渲染 |
| avsox.png | `https://avsox.click/res/fonts/favicon.2a4d5d787e44d9be7614.ico` | 页面运行后动态写入的 `shortcut icon`；首页 HTML 没有声明，根 favicon 返回 HTML |
| manko.png | `https://manko.fun/images/favicon.ico` | 首页声明的 favicon；apple-touch-icon 指向正文宣传图 |
| sukebei.png | `https://sukebei.nyaa.si/static/favicon.png` | 用户指定并核实的蓝色原图 |
| dmm.png | `https://www.dmm.co.jp/age_check/images/fanza-favicon.ico` | 官方入口声明的 FANZA favicon |
| mgstage.png | `https://static.mgstage.com/mgs/favicon.ico` | 首页声明的 favicon |
| fc2cmadb.png | `https://fc2cmadb.com/favicon.ico` | 官方站点的 favicon |
| javarchive.png | `https://javarchive.com/favicon.ico` | 首页声明的 favicon |
| javdb.png | `https://javdb.com/favicon-32x32.png` | 首页声明的 favicon |
| javbus.png | `https://www.javbus.com/favicon.ico` | 官方站点的 favicon |
| avbase.png | `https://www.avbase.net/favicon.ico` | 首页需验证，公开 favicon 可读取 |
| r18dev.png | 已有 `https://r18.dev/` 站标缓存 | 复用 dev 字标；官方 favicon 核对为同一标识 |
| fc2.png | 已有 FC2 站标缓存；原图来源见 `site_icons.HOST_OVERRIDES['fc2.com']` | 复用用户确认的纯红色独角兽 |
| 1pondo.png | 已有 `https://www.1pondo.tv/` 站标缓存 | 复用现有一本道标识 |

取证：javten、fc2ppvdb、avwikidb、github 为 2026-09-30，minnano-av 为 2026-10-01，其余为 2026-10-19。五个既有静态入口经 `/site-icon/{name}.png` 提供；`/site-mark?source=…` 优先提供本目录中对应的已核对资源。浏览器不向外部图标服务发请求。
