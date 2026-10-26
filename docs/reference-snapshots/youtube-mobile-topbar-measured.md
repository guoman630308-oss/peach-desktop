# YouTube 手机版顶栏定位与 iOS 聚焦显露取证

- 取证日期：2026-09-14
- 来源：以 iPhone Safari UA 抓取 <https://m.youtube.com/>，读取页面引用的样式与主脚本；浏览器面板
  直接打开 m.youtube.com 时静态资源被 `net::ERR_BLOCKED_BY_CLIENT` 拦下，运行时 DOM 与计算样式未取得
- 页面 HTML：SHA-256 `39ff5e1a9943f67941144b154433cc66882e3ab031585ec8a972b9bc4f36a8e9`
- 样式：<https://m.youtube.com/s/_/ytmweb/_/ss/k=ytmweb.c3_base.XeCffbT4X_0.L.W.O/am=AAAAAMCAIAA2Gw/d=1/rs=ABnK5FJvcTupHzma809lM5L8XX7s02z7Fg/m=root,c3_base,PzUdZb,DlvF1d,Tu3MB,SFvGjc,HDmU1e,cDTHu,JoU6vf,OM7xob,UAM5m,Di4hBc,dpUCFd,oPp5Le,A4oAVe,Nr0kfe>，
  SHA-256 `3845f625f33dd1c13589b1cb615002814f2c775b27b3faeaaebd59d9c2c69ce3`
- 主脚本：`ytmweb.c3_base.en_US.sBXzZErQDoA.2021.O`，SHA-256
  `7fd8ff3934588640fe2f026af8f42b2a8fd92828cb5a03cc1eae2fe3be1087cb`

## 实测规则

- 顶栏：`ytm-header,ytm-header-bar,ytm-mobile-topbar-renderer{position:fixed;top:0;left:0;right:0;z-index:4}`；
  新顶栏 `.ytTopBarViewModelHost` 同样 `position:fixed;top:0;left:0;right:0;z-index:4`，左右让出安全区。
- 正文让位：`ytm-app` 在 `.sticky-player` 下与宽屏竖屏断点下都是 `padding-top:48px`。
- 搜索态：`.ytTopBarViewModelHost[data-mode=searching]` 在 fixed 顶栏里就地换成搜索框，隐藏尾部按钮；
  建议列表 `.ytSearchboxComponentSuggestionsContainerScrollable{overflow-y:auto;max-height:80vh}` 自己滚。遮罩 `search-scrim` 只在 AI 搜索开关打开时由脚本挂上。
- 视口 meta：`width=device-width, initial-scale=1.0, minimum-scale=1.0, maximum-scale=1.0, user-scalable=no,viewport-fit=cover`。
- 模态层锁滚动：脚本给 body 加 `modal-open-body` 并写负的 `top`，样式
  `[modal-open-body]{position:fixed;left:0;right:0}`；关闭时 `window.scrollTo` 回原位。搜索态是否走
  这把锁未取得。
- 主脚本里没有 `visualViewport`，也没有 `preventScroll`。

## Peach 录屏测量

用户录屏 `ScreenRecording_09-14-2026 16-16-23_1.mp4`（220×480，7.1 秒）按每秒 5 帧抽帧：按下搜索键
后 0.4–0.8 秒内整页平滑上滚，卡片标题在帧内下移约 115px，折合 CSS 约 200px；顶栏始终贴在状态栏
下方，筛选框随上滚翻出。Chrome 手机模拟里同样操作 `scrollY` 不变、没有 scroll 事件，上滚来自
Safari 本身。

## WebKit 源码对照

取自 WebKit 主干（抓取后查询到的 revision `e4d6f095cfbba338af740f00311e48c8e3c5dd4e`）：

| 文件 | SHA-256 | 相关位置 |
| --- | --- | --- |
| `Source/WebKit/UIProcess/ios/WKContentViewInteraction.mm` | `652313858143462cec3aed61cf5f1cdabdffc488149173091360474157fb3180` | `_zoomToRevealFocusedElement` 先看 `preventScroll`；`_elementDidBlur` 把它清回 false |
| `Source/WebKit/UIProcess/API/ios/WKWebViewIOS.mm` | `d3bfab63fdf1802e0283045fd67f4c6d5922565e084ab3583a526441e6faae08` | `_zoomToFocusRect` 在带表单辅助栏时强制把输入框居中到键盘上方；`_scrollToAndRevealSelectionIfNeeded` 在键盘尺寸变化时触发，不看 `preventScroll` |
| `Source/WebKit/WebProcess/WebPage/ios/WebPageIOS.mm` | `fcd637987289046b66443fd1bc4d93bf5ba2ddd222649654791ff623839bfc3c` | `emitDeferredFocusedElementUpdate` 是唯一写入 `preventScroll` 的地方 |
| `Source/WebKit/WebProcess/WebPage/WebPage.cpp` | `40da5feb3201444d263352d63922d93a0512da5add4d517b7877729a18a896f2` | `elementDidFocus` 把 FocusOptions 放进挂起更新 |
| `Source/WebKit/UIProcess/RemoteLayerTree/ios/ScrollingTreeScrollingNodeDelegateIOS.mm` | `076031a4b537130f63be46d8c6dbce83fc3097143f2f4231d3a5ac289f110c90` | 子滚动节点按 `canHaveScrollbars` 设 `scrollEnabled`；主滚动视图恒为可滚，根上 `overflow:hidden` 挡不住上面那条显露 |

居中公式按录屏几何估算的上滚量与实测约 200px 同一量级，但哪一条路径在用户机型上生效、fixed 顶栏为何
不被带动，未取得真机日志，只能以上线后真机复测为准。

## Peach 采用与差异

- 采用：窄屏 `.top` 改 `position:fixed;top:0;left:0;right:0`，`body` 让出 `var(--topH)`（Peach 为 64px）。
- 保留：搜索键与返回键的 `focus({preventScroll:true})`、搜索框就地展开与返回箭头。
- 不采用：`user-scalable=no`（保留缩放）；模态层锁滚动（没有证据表明 YouTube 搜索态使用）。

## 不登记进 docs/reference-sources.json 的原因

样式与脚本是按构建号轮换的打包产物，不是可整体覆盖的上游 Markdown；这里是人工抽取的规则、
录屏测量与源码对照笔记，哈希已记在上面，登记表没有可对应的 snapshot。
