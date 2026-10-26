# YouTube 小窗播放器（miniplayer）与播放器右键菜单实测

- 取证日期：2026-09-06
- 来源：实时访问 <https://www.youtube.com/watch?v=jNQXAC9IVRw>（`Me at the zoo`），在桌面版
  播放器右键选「Miniplayer」进入小窗后读取 DOM、计算样式与播放器资源；实测视口 1456×822 CSS px
- 桌面站版本：`f82dea74`；播放器版本：`f572e43c`
- 播放器 CSS：<https://www.youtube.com/s/player/f572e43c/www-player.css>，SHA-256
  `6503becd8abddddc2a7cf3e598efe9f7aca089bf56f4a497624c8839845453fd`
- 播放器 JS：<https://www.youtube.com/s/player/f572e43c/player_es6.vflset/zh_CN/base.js>，SHA-256
  `5b4bd0e8f05798738bbdb46b828e2bd8331a7dbc6e6108fb7a787e9902134586`
- 桌面站脚本 `desktop_polymer.js` 的可重抓 URL 未取得，`ytd-miniplayer` 的规则只有计算样式一侧
- 完整实测原文与失败记录留在仓库外、与 `peach-app` 同级的
  `<私有验证记录>`

## 不登记进 docs/reference-sources.json 的原因

这份笔记是人工实测计算样式与 DOM 的汇总，不是上游一份可重抓、可哈希的 Markdown；
`www-player.css` 与 `base.js` 已按 SHA-256 记在上面，桌面站脚本的 URL 未取得，
登记表没有能整体覆盖的 snapshot，所以只以本文件说明用途和差异。

## 可复用证据

### 小窗容器 `ytd-miniplayer`

- `position:fixed`，`z-index:2030`；行内样式 `right:16px;bottom:16px;width:400px;height:376px`
  （本片 4:3，画面区 400×300 加信息栏 76px；样式表默认画面区 400×225）；`max-height:calc(100vh - 32px)`。
- 内容卡 `border-radius:12px;background:rgb(33,33,33);box-shadow:0 2px 5px rgba(0,0,0,.16),0 3px 6px rgba(0,0,0,.2)`。
- 吸附动画 `.ytdMiniplayerComponentAnimatingSnap{transition:transform .5s cubic-bezier(.05,0,0,1)}`；
  进出场 `.ytdMiniplayerComponentAnimatingFade` 时长 `.366s cubic-bezier(.05,0,0,1)`，关键帧位移量未取得。
- 信息栏 76px 高，`padding:12px 16px`，`cursor:grab`；标题 14/20 500 `rgb(241,241,241)`，
  副标题 12/18 400 `rgb(170,170,170)`，上距 4px。四边各有 12px 的 resizer。

### 小窗内播放器层 `.ytp-miniplayer-ui`

- `z-index:67`；悬停幕 `.ytp-miniplayer-scrim` 为 `rgba(0,0,0,.5)`，圆角 `12px 12px 0 0`。
- 关闭键右上 40×40（padding 8）；展开键左上 40×40，`title="Expand (i)"`、`aria-keyshortcuts="i"`；
  中央播放键 42×42；时间读数左 7、底 0、12px。
- 进度条 400×5 常显，scrubber 12px。

### 播放器右键菜单 `.ytp-contextmenu`

- 293×337，`border-radius:12px`，`background:rgba(0,0,0,.6)`，`backdrop-filter:blur(16px)`，无阴影，
  面板 `padding:8px 0`。
- 每项 40px 高；图标格 44px（`padding:0 10px`，svg 24）；标签右侧 15px；字号实测 12.98px、500、
  `rgb(238,238,238)`；悬停 `rgba(255,255,255,.1)`；「Loop」是 `menuitemcheckbox`。
- 项目顺序：Loop、Miniplayer、Copy video URL、Copy video URL at current time、Copy embed code、
  Copy debug info、Troubleshoot playback issue、Stats for nerds。

## 未取得

- 四个角的落点坐标：CDP 合成拖动松手后被 AnimatingSnap 弹回原位，JS 合成 PointerEvent 拖动使
  渲染进程卡死 45 秒，两条通道都没拿到「拖到左上后的 left/top」。Peach 按同一 16px 边距自行推导。
- 进出场关键帧的位移量。Peach 只做 .366s 透明度进场。
- `desktop_polymer.js` 的可重抓 URL。

## Peach 主动保留的差异

- 顶部两角让出吸顶顶栏：`top:calc(var(--topH) + 16px)`，上游没有吸顶顶栏。
- `i` 键与上游同义（详情里进小窗，小窗里展开），因此原来映射到画中画的 `i` 改掉，画中画只留按钮。
- 小窗内换片重新挂一个 Video.js 播放器，不复用旧实例：错误兜底、观看上报与清晰度表都绑在
  旧实例的闭包里。上游只在播放列表内切换，Peach 把目录卡与关注视频卡的点击也收进来，这是主动扩大的范围。
- 分卷／版次组、计费的本地条目、反查不到关注条目的在线资产和脱盘来源不进小窗，照旧走详情。
- 右键菜单去掉嵌入代码、调试信息、排查播放问题三项；「播放统计」只在详情里且统计键可用时出现；
  「画中画」只在浏览器支持时出现。宽度不复制 293px，按内容自适应且不小于 220px。
- 颜色用 Peach 自己的 `--surface`／`--ink`／`--muted` token，进度条用 `--tungsten`；不复制 YouTube 品牌色与字体。
- 窄屏宽度收成 `min(400px, 100vw - 32px)`，竖片画面区比例压到 1:1 以内。
