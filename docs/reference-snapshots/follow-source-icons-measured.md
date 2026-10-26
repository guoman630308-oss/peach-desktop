# 关注来源图标实测记录

- 取证日期：2026-08-27
- 取证方式：`httpx` 带常规浏览器 UA 直接请求，记录状态码、`content-type`、字节数与
  `sha256` 前 12 位；跟随重定向
- **不在 `docs/reference-sources.json` 里登记**：那张表要求每个来源都有可哈希的上游快照，
  而这里记的是「当前哪个 URL 能取到图标」，站点改版就会变。要复核就按上面的方式重测。

## 为什么不用 `/favicon.ico` 一把梭

三个归档站的 `/favicon.ico` 是 404，图标在页面 `<link rel="icon">` 指向的路径下。

| 站点 | `/favicon.ico` | 实际图标 URL | 结果 |
| --- | --- | --- | --- |
| kemono.cr | 404 | `/assets/favicon-CPB6l7kH.ico` | 200 `image/x-icon` 4,154 B `sha256:31d87d68121b` |
| coomer.st | 404 | `/assets/favicon-CPB6l7kH.ico` | 200 `image/x-icon` 4,154 B `sha256:31d87d68121b` |
| pawchive.pw | 404 | `/static/favicon.png` | 200 `image/png` 20,041 B `sha256:b16152e9602b` |
| rule34video.com | 200 | `/favicon-32x32.png` | 200 `image/png` 1,764 B `sha256:5282045bdc85` |
| rule34.xxx | 200 | `/favicon.ico` | 200 `image/x-icon` 1,150 B `sha256:68bd5752e7b0` |
| f95zone.to | 200 | `/assets/favicon-32x32.png` | 200 `image/png` 1,679 B `sha256:f658dc0b364c` |
| mypikpak.com | 未测 | `/apple-touch-icon.png` | 200 `image/png` 3,832 B（2026-08-29 实测，HTTP 头核验；目录来源图标的直连地址，`SRCICON.pikpak` 用它替代 `/logo?studio=pikpak` 的生成 logo） |

## 两条容易判错的地方

**kemono 和 coomer 的图标是同一个文件**（`sha256` 完全相同，连路径里的内容哈希都一样）。
它们是同一套代码的姊妹站，所以图标区分不了这两个来源，靠旁边的站名区分。

**rule34video 与 f95zone 的 `/favicon.ico` 都是 15,086 字节，但 `sha256` 不同**
（`2fe52ae2ed3c4201` / `196f7bb69a3f788e`），是两个不同的文件。字节数相同是巧合——
多分辨率 `.ico` 常见的大小。只按字节数相同就断定「不是各自的图标」会判错，
这一条是 2026-08-27 实际发生过的误判。

## 已知脆弱点

kemono / coomer 的路径里带构建产物的内容哈希（`favicon-CPB6l7kH`），
它们下次发布就会失效。界面对取不到的图标用 `onerror` 摘掉，退回纯文字站名，
不留破图；发现图标消失时按本文件的方式重新取一次路径即可。

rule34.xxx 的首页对我们回 403（Cloudflare），但 `/favicon.ico` 可直接取，
所以这一条不受影响。
