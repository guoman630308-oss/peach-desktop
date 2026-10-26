# YouTube 播放器控制栏截图测量

## 字幕列表与选项（2026-10-18）

用户截图 `codex-clipboard-82665b6b-2d5c-4ff7-83e5-ede378470f5b.png`：611×617 px，SHA-256 `aa28a6ab16e9508e6ec3c35c08f4ae3f2b40333a0b554fdb3dba4da97e75281e`。截图是人工取证笔记的来源，不作为可被线上接受操作覆盖的上游原文快照。

- **实测（截图）**：字幕子层顶部左侧有返回键和标题，右侧是带下划线的「Options」；标题下有分隔线；「Off」和字幕轨道是整行选项，当前轨道左侧有勾选标记；CC 按钮与设置、影院、全屏共用底部胶囊。
- **实测（锁定源码）**：`<私有验证记录>`（YouTube `f572e43c`，SHA-256 `5b4bd0e8f05798738bbdb46b828e2bd8331a7dbc6e6108fb7a787e9902134586`）的 `b$f` 提供 36 单位视框、实底圆角矩形及镂空 CC 路径。Peach 的字幕按钮与设置行共用这条 SVG；CC 开启下划线和 HD／4K 徽标按用户要求使用主题强调色，悬停沿用共享胶囊高亮。
- **实测（官方文档）**：[YouTube 字幕设置帮助](https://support.google.com/youtube/answer/100078?hl=en)，2026-10-18 读取：设置入口依次为 Subtitles/CC、Options，可调字体、文字颜色／不透明度／大小、背景颜色／不透明度、窗口颜色／不透明度和字符边缘，并有重置行为。
- **推导（Peach）**：复用现有 274 px 播放器设置面板及 48 px 行高。字幕时间轴复用浏览器 TextTrack，外观由播放器显示层承接；选项保存在当前浏览器。本地文件加载与 ASSRT 搜索属于 Peach 自己的功能；自动翻译不在采用范围。当前 YouTube 字幕运行时 DOM 和逐属性计算样式未取得，不声明这些外观参数逐像素一致。

- 取证日期：2026-08-30
- 来源：YouTube 播放器手工截图，以及实时访问
  <https://www.youtube.com/watch?v=jNQXAC9IVRw>（`Me at the zoo`）取得的 DOM、计算样式和播放器资源
- 实测视口：1280×720 CSS px，DPR 2；播放器版本：`e937390a`
- 播放器 CSS：<https://www.youtube.com/s/player/e937390a/www-player.css>，SHA-256
  `24cb353f7db6c8025eaf8648aa1f941cddfac84342d06ed4fb9def4523328bb3`
- 播放器 JS：<https://www.youtube.com/s/player/e937390a/player_es6.vflset/zh_CN/base.js>，SHA-256
  `81279f22cbecf0e8ef33abad68b31947b7d22855f73c3d556396e7e410c78ef5`
- 原图：2733×195 px
- SHA-256：`781c6e032251f5d0a3f3c1a16095afa7b6385be86983b8443d2ca70933508f1a`
- 对照图：同轮的 Peach 生产截图，2819×101 px，SHA-256
  `af03bedef18c752c03dec65f7f83f1b6eca7ce87861f2d0b8dbdab82fd3fc632`
- 第二轮对照图：整块毛玻璃背景去掉，1429×155 px，SHA-256
  `e6587d90e0d163844561e36cc6246288b2c7bfa1ab13df61582fd8f9758707cc`
- 第三轮对照图：中央播放键和左上角工具键统一为圆形，637×445 px，SHA-256
  `66096283446a85e8070fa0f6b9f338a7f133d5c9185f30ce00365a0c1865ef5b`
- 第四轮参考图：精确拖动时刻、缩略图和波形层级，625×631 px，SHA-256
  `4890e1c267a7ae3f06fb924bfe40e67ceec83cb524b48694e3a917644b6aa3ef`
- 第四轮 Peach 对照图：2829×131 px，SHA-256
  `65e7a4fe5f6d335642a8a78a154375593a32979c1f67c4c65dddaa043439cd07`
- 第四轮 YouTube 控件分组图：2709×113 px，SHA-256
  `ca84427227cf75ffab5ecad77a8c3dcc69c25e68f2f4dfec7adf47be957f2d42`
- 第五轮进度条参考图：2811×77 px，SHA-256
  `faa0f88a1895bc718758596e9d8d769ada818c994c9713846bb80ac794b6e8ab`
- 第五轮卡片观看进度参考图：1129×889 px，SHA-256
  `41246447eef380c9f6435f7bb699694fea38ae2ce25edc13d86be9710678017f`
- 第六轮 Peach 高度对照图：459×103 px，SHA-256
  `2cd687877698b0be198c031e7786255322dfaa7bc0fc509d91f32bb3c06a1828`
- 第六轮 YouTube 设置面板参考图：749×681 px，SHA-256
  `046d6bb9b8414f1ad2f2347eb2330e44941ff1096f7b34f3293060fa2f19111b`
- 第六轮 YouTube 影院模式参考图：629×275 px，SHA-256
  `a0d51dc86414efffba746faaaa8ab7a12b49bfde5c2b1ba09fa9d53b0a67fec1`
- 第七轮 Peach 控制栏／音量／设置偏差图共 3 张，SHA-256
  `89003281cd0f6736da8217b19c6409955a98284680f675b21d6f281cfa072493`、
  `f0fe864e03ceb005d9d6b30ddd865e72e5ee719122eac6199cbc19f700a42cd3`、
  `b8b4a65ccce1b80067ae2ac81540b357589ea5ef882e42f66be844eaf84d4dfa`
- 第七轮 YouTube 控制栏／音量／设置参考图共 3 张，SHA-256
  `5518710e5755794cb9043d5ce4d7ebbb453f7383e8b5249f666a5beffb64911a`、
  `f49aae4bce641ba3c5b206768860c5f82f788d5319ee60a24ec60583443f9f28`、
  `a594b7296659e9c995a2edf968ac35cd46ebe5229b1b462733beb82a257be44b`
- 第八轮标注的音量裁切、底部播放、控件底色、中央按钮和影院两态偏差图共 8 张，SHA-256
  `dc94e534b1092671f6f46d501169f9a75c52e4462c7782dc2ddfaa97c9db0d3b`、
  `fef85b91cebbba9ae0a72d05e56c694e298281c0201e5a27662f82a546fd6b80`、
  `f93473a5e802bd64a6141305c847dbc3e700af2053689437d5737dd2bc618417`、
  `1d332f48a2a1a70ebfae043623f86d5192b297095bbe40e10a1b477d53703ba0`、
  `1adff33f7da0ef88d02923cb302e212a620ef83fc7701d42e83873bc3709d53e`、
  `093e41fbbeef96d7bfcf9bef1b2ef2e1ebea66c35fb2338c5fe6abcedb216049`、
  `9f14dbd6a4b4d62d45766b245e45bf9bcf764b83451b8461557097886f99e891`、
  `27381b857bd80eb76c15314ca47b12144e451c8520ffdeb4cd2911e50c5c71bd`
- 第九轮标注的时间背景、影院音量、设置主层／子层和整栏图标偏差图共 5 张，SHA-256
  `3199eda86cf160b92fd0f9a27ce27857ab42db6c91438716f1ee9dc96742e97e`、
  `78bec4f733ab87cbf8eadb1ce10e322a8af30c29b5605b2a109eec32fff0aa27`、
  `24c91a1804bca776e3244ebd0be6d582cd8bcd425d0a6681005efe6dcd06ebd2`、
  `732f1d356299e9c2ec0523d55399cb2154eb50c259b87eac2c11c31b5cf6cf44`、
  `bfaeaef7e620367c4421a00528d83c27fbd052ae7a95bd5ae65cffe1ef421950`
- 第十轮标注的音量重叠、设置对齐、实际 YouTube 设置面板和全屏未铺满偏差图共 4 张，SHA-256
  `c72284752f1bb819da90530b180e5119d619fd22e1220eb887b6d645a86860b3`、
  `06c71239b969d3e15cb2fd36ca453dfc52d4f4518869b5459eb4b2018a03fcab`、
  `41c8b2dda5d79044361960f249fa4dd702784da690cc1ca113e299464502c49e`、
  `d2fe79b33c72950951310a7224262c9b0d4a5ede5779cfe890cdc2ffcdba8914`
- 第十一轮标注的 loading／中央反馈重合、音量重叠、三枚设置图标、行尾箭头、清晰度子层和
  YouTube 子层缩进参考共 7 张，SHA-256
  `e38d78d48e90f23cc772c5a5bbf3612626f8d5e115f1fcbee493613e2e344d8e`、
  `be33b4623f3f22a19bce255e4ebb8d2674be672605041c19f4259bc7d3a2aead`、
  `00904ac28cff16350135143d3ec4b7c913fbb7ac21efd33a32617bb62af83260`、
  `00ed60c1bfed5fc84c363e6f0e3743280bcdaa88804f7ddad6c263a1b064552d`、
  `55ff69e21df05c36b6c09647170d5039a9e9cc71b0d13c21102d5136aa82e776`、
  `12bd9bbb9775b027c44302adb1872c33f0c8f6e5d3365125df10fdb56b18dc4e`、
  `e9b009dc847f90e917b859d9e7ee458e42cce408680ba971982d1488c9ba0830`。
- 第十二轮标注的选中勾、错误三角形、当前时刻浮签和音量悬停／垂直对齐偏差图共 4 张，
  SHA-256 `7be69dd2e1e9dad56f99a4c7b51b3a5eaabd0cae9bea1152d6cda17731d2159f`、
  `d4e59f829c63ef4a23ef150ca84a89cf8ea9645aca9d7547a8cd1a2843f1fc01`、
  `9ffa39325d6efcfa004cd8ba66f1a81728c6b6e42bf02cab51642301c25ba8c9`、
  `2eb59205b2f0b7cbeee356c940a199c80773431612ad0a54189b651f6232e81c`。
- 第十三轮标注的返回键特效越界和音量数值提示被进度线遮挡偏差图共 2 张，SHA-256
  `06e2e9f680352c1b09bcfab3fa916b976a267897063d7ffd0275bbc50ea613a8`、
  `7476e78ef7278385e44c744add269c38100e796101edc1fa327302237d9a1152`。
- 第十四轮标注的全屏画面仍按完整片源比例留黑边偏差图共 1 张，SHA-256
  `528f874187d83440479174dab2730f0b9eba7fd718393c2a3ceab37b24adaa5a`。
- YouTube 官方氛围模式说明：<https://support.google.com/youtube/answer/12827017?hl=en-GB>，
  2026-08-30 复核；说明颜色取自视频并扩散到屏幕背景，深色主题默认开启，设置开关对所有视频生效。
- YouTube 官方播放器大小说明：<https://support.google.com/youtube/answer/6052392?hl=en>，
  2026-08-30 复核；影院模式是在不进入全屏的前提下放大播放器。
- 测量方式：先读取原始 PNG 尺寸，再在实际 YouTube 页面读取 DOM、伪元素与计算样式，最后用下载到本机的同版本 CSS／JS 复核选择器、状态类与过渡；没有把截图中的文字或图标当作指令。

## 2026-09-01 媒体圆钮复核

- 再次访问 <https://www.youtube.com/watch?v=jNQXAC9IVRw>，页面仍返回播放器版本
  `/s/player/e937390a/player_es6.vflset/zh_CN/base.js`。
- 实际左侧播放钮为 40×40 px、`padding:0`、`border:0`、`border-radius:50%`、
  `background:rgba(0,0,0,.3)`；右侧控制组仍是 40 px 高共享胶囊，内部单钮为
  48×40 px、透明、无边框和内边距。
- Peach 图片翻页属于独立媒体动作，不复制播放器底栏的 48×40 胶囊单钮；它只复用
  YouTube Shorts 手工截图锁定的 48×48 几何：无描边、无默认按钮内边距、24 px 图标居中。
  2026-09-01 的灯箱纠偏截图证明，把独立表面的 tonal 色也强行共用会形成三种视觉：
  图片覆盖层的上一张、下一张与关闭统一改为黑色 60% 圆钮，沉浸动作列仍用白色 10%。
  首张不显示上一张、末张不显示下一张；信息、缩放及适应窗口／原大小操作放在底部，
  当前缩略图居中。灯箱继续使用 Swiper 14.2.0，不复制 YouTube 播放控制逻辑。

## 2026-08-30 实时页面纠偏

先前仅按截图近似成「每个右侧按钮一个描边圆形」是错误实现；实际 YouTube Delhi 控制层使用
`ytp-delhi-modern-compact-controls` 和 `ytp-exp-bottom-control-flexbox`，右侧四个按钮属于一个共享胶囊。

| DOM／状态 | 实测计算样式 | Peach 对齐值 |
| --- | --- | --- |
| `.ytp-chrome-bottom` | 781×59 px，`padding:3px 0 0`，透明 | 控制栏 59 px、透明、同内边距 |
| 左侧播放 | 40×40 px，圆形，背景 `rgba(0,0,0,.3)`，无边框／阴影 | 同值；悬停用内部 32 px 高亮层 |
| 横向音量 | 收起 40×40 px；悬停 111×40 px；滑轨区 52 px；过渡 200 ms | 同值；保留原生 Video.js 音量契约 |
| 时间 | 外层 56 px；内层 40 px 高、左右 16 px、圆角 28 px、背景 `rgba(0,0,0,.3)`；点击从 `0:01 / 0:19` 切为 `-0:18 / 0:19` | 独立可点击胶囊；按亮色片源截图提高为黑色 60% |
| 右侧控制组 | 高 40 px、左右 padding 4 px、圆角 28 px、背景 `rgba(0,0,0,.3)`、模糊 16 px | 画中画／设置／影院／全屏放进同一共享容器；按亮色片源截图提高为黑色 60% |
| 右侧单钮 | 48×40 px、透明；悬停伪元素 48×32 px、圆角 40 px、白色 10% | 同值；按下白色 20%，SVG 仅保留 1 px drop-shadow |
| 进度 | 命中区 6 px；6 px 轨道静止缩放为约 4 px，悬停恢复 6 px；12 px 圆点从 0 放大到约 20 px；过渡 200 ms | 几何和过渡同值，已播段和圆点改为 Peach 蓝 |
| 设置浮层 | 宽 274 px、圆角 12 px、背景黑 60%、模糊 16 px、无边框／阴影 | 只保留 Peach 已实现的三项能力 |
| 设置行 | 约 48 px；图标 24 px；图标列 56 px；正文 14/18.2 px；悬停白色 10% | 同值，不再使用旧 58 px 行高和大字号 |
| 设置开关 | 40×24 px、圆角 12 px；圆点 20 px、边距 2 px、开启位移 16 px | 同值，状态仍由 Peach 全局设置持久化 |
| 设置子层 | 标题栏 57 px；返回钮 32×32 px、白色箭头；标题距返回钮 8 px | 速度与清晰度共用同一标题栏，不再把返回钮画成整行 |
| 控制图标 | 音量与全屏使用 24×24 viewBox 的实心轮廓；按钮仍分别为 40×40 与 48×40 px | 音量、画中画、全屏改用统一 24 px 本地 SVG，静音／全屏退出各有反向状态 |

### 第八轮 DevTools 状态取证

- 实际底部播放键为 40×40 px、圆形、黑色 30% 底；播放与暂停使用不同的居中 SVG，不是
  Video.js 字体图标。Peach 保留尺寸，亮色片源下底色提高为黑色 60%。
- 实际横向音量容器 `overflow:visible`；收起 40×40 px、悬停展开 111×40 px。Peach 改为只隐藏
  滑轨本身，提示层不再被胶囊裁切。
- 实际影院按钮为 48×40 px；普通态图标表示横向展开，影院态图标改为横向收回，提示分别为
  「影院模式」和「默认视图」。Peach 使用两枚本地图标表达同一状态语义。
- 中央交互定为单个深色圆形播放／暂停键；左右 10 秒键删除，键盘跳转能力保留。

### 第九轮 DevTools 状态取证

- 实际 `.ytp-time-wrapper` 为 40 px 高、左右 16 px、28 px 圆角、黑色 30% 胶囊；点击
  `.ytp-time-contents` 会在已播时间与负号开头的剩余时间间切换，总时长保持不变。
- 影院模式下实际 `.ytp-volume-area` 收起仍为 40×40 px、28 px 圆角和黑色 30% 背景；模式切换
  不移除音量胶囊底色。Peach 为亮色片源保留黑色 60%，并显式覆盖影院与全屏状态。
- 实际设置主层宽 274 px、黑色 60%、12 px 圆角；每行约 48 px，24 px 图标位于 56 px 图标列
  左侧 8 px，正文与状态都是 14/18.2 px。子层标题栏 57 px，返回钮 32 px，白色图标，标题紧随其后。
- 实际底栏音量图标使用 24×24 实心轮廓，全屏按钮 48×40 px 且展开／退出使用反向图形；Peach
  用同尺寸本地 SVG 取代 Video.js 字体图标，避免与设置、影院图标产生光学大小漂移。

### 第十轮 DevTools／官方 CSS 纠偏

- `.ytp-volume-area` 与 `.ytp-time-wrapper` 都使用 `inset:4px` 的内部伪元素；悬停为白色 10%，
  按下为白色 20%，过渡 200 ms。音量从 40 px 展开时，40 px 静音按钮仍固定在容器内，不能沿用
  Video.js 的偏移量。
- `.ytp-progress-list` 本体高 6 px，静止 `scaleY(.667)`、悬停恢复 1；12 px scrubber 静止缩为 0，
  悬停放大到 1.67。两者使用 200 ms `cubic-bezier(.05,0,0,1)`。
- 设置菜单内边距 8 px、行高 48 px；每一整行（包括末端值和展开箭头）统一覆盖白色 10% 悬停层。
  开关为 40×24 px，圆点 20 px、四周 2 px；关闭态轨道黑色 30%、圆点白色 70%，开启态轨道
  白色 30%、圆点纯白并平移 16 px。
- 现代中央反馈层为 78×78 px、黑色 60%、16 px 模糊；播放／暂停触发 1 秒反馈动画：0% 透明，
  25%–75% 不透明且放大到 1.33，100% 恢复尺寸并淡出。
- 全屏不是继承父容器的 `100%`；播放器要固定到视口四边并使用 `100vw × 100vh`，视频 tech 同样
  覆盖视口后以 `contain` 保留完整画面。
- 改用 Chrome DevTools 通道；扩展发现一次返回不可用，重试后可用，同一 Chrome
  扩展成功连接并同时加载实际 YouTube 与 Peach 页面，证明“未安装”判断错误。随后 Playwright
  evaluate、`DOM.getDocument` 和全新标签三条读取路径均在 20–60 秒超时并重置控制会话，故新增
  Chrome computed style 仍标为「未取得」。精确值来自已锁定同版本官方 `www-player.css` 和此前实际
  YouTube 页面 DOM／计算样式，不把仅加载成功写成 DevTools 样式验收成功。

### 第十一轮当前源码纠偏

- 2026-08-31 重新访问实际视频 <https://www.youtube.com/watch?v=mPV-oMZwSW4>；页面当前仍引用
  `e937390a` 的 `www-player.css`，SHA-256 仍为
  `24cb353f7db6c8025eaf8648aa1f941cddfac84342d06ed4fb9def4523328bb3`，同时引用
  <https://www.youtube.com/s/player/e937390a/player_ias.vflset/zh_HK/base.js>，SHA-256
  `88d946f3db89b2d71e52554318b6520c2c518e07cc9827c7c942b042ac65cdc1`。
- `base.js` 的 `FsY` 直接给出 loading 的左右半圆 DOM；CSS 锁定 64 px、6 px 圆环，线性旋转
  `1.5682352941176s`、缓动旋转 `5332ms`、左右半圆各 `1333ms`。Peach 复用 Video.js 的
  `vjs-waiting`／`vjs-seeking` 状态，仅把内部结构和动画换成该来源值；中央播放／暂停改为用户手势后的
  1 秒 bezel 反馈，初始不渲染可见按钮，waiting／seeking 时强制隐藏，所以两层不能重叠。
- 同一 `base.js` 的 modern 分支直接给出环境模式、播放速度、画质三枚 24×24 SVG path，以及
  36×36 播放／暂停 bezel path；Peach 原样登记为本地 symbol，不再使用近似 Lucide 画法。
- 官方设置行仍是 48 px；图标格左 8／右 24 px，内容右 8 px，行尾箭头使用 18 px、白色 70% 的
  modern path。radio 子层标签左 padding 35 px，勾选图位于左 10 px；标题栏为 57 px，48 px
  返回容器与 32 px 箭头。Peach 因此把选中勾移到左列、删掉清晰度右侧伪尾标，并给主层箭头独立
  32 px 网格列，避免越出圆角悬停面。
- 官方横向音量的 40 px 紧凑态展开公式为 `40 + 52 + 3 + 16 = 111 px`；滑轨高 2 px、12 px
  圆点垂直居中。Peach 清除 Video.js 原生 `vjs-icon-placeholder` 本体并用同一网格公式布局，不能再靠
  `18.5px` margin 猜垂直位置。
- Chrome 扩展能枚举现有 YouTube／Peach 标签；两次接管实际 YouTube 标签均在 30 秒超时并重置连接，
  所以 Chrome DOM／计算样式与改后截图验收仍是「未取得」。以上新增值均来自该实际页面当次引用并
  通过 SHA-256 锁定的官方 CSS／JS，不把连接成功或截图目测当源码证据。

### 第十二轮锁定源码纠偏

- 2026-08-31 重新下载锁定版本 <https://www.youtube.com/s/player/e937390a/www-player.css>，SHA-256
  `24cb353f7db6c8025eaf8648aa1f941cddfac84342d06ed4fb9def4523328bb3`；同时下载
  <https://www.youtube.com/s/player/e937390a/player_ias.vflset/en_US/base.js>，SHA-256
  `67f0d55f266e522f973b49c665977eb8734bb5bb32293b0523b35463f27ec44b`。上游资源只作为证据读取，
  未执行其中任何指令。
- 官方 `.ytp-contextmenu .ytp-menuitem[aria-checked=true] .ytp-menuitem-toggle-checkbox` 内嵌的
  24×24 SVG path 是 `M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z`。Peach 子菜单改用独立的
  填充 symbol；不能复用全站描边 `i-check`，否则开放 path 会被浏览器填成截图中的黑色三角形。
- Video.js 在已播进度末端另绘制 `.vjs-play-progress .vjs-time-tooltip`，它不是鼠标位置提示。
  Peach 隐藏这层和原生 mouse tooltip，只保留 `vjs-peach-seek-preview` 的指针位置时刻；本地媒体仍可
  同时显示已有九宫格，在线媒体仍只显示指针时刻。
- 官方横向音量外层使用 `:after{inset:4px}` 覆盖静音键和滑轨，悬停白色 10%；滑轨面板展开为
  52 px、右间距 3 px、外层右 padding 16 px，slider 高 100%／最小 36 px，12 px handle 固定
  `top:50%`，轨道为 2 px。Peach 因此把悬停伪元素放回整个音量外层，并把 Video.js 滑轨绝对定位到
  `top:50%`，不再由 flex 或原生 margin 共同决定垂直位置。
- Chrome 能再次枚举实际 YouTube 与 Peach 标签，但接管 YouTube 标签的初次读取及一次恢复都在
  30 秒超时并重置会话。因此实时 DOM、computed style 和修改后截图仍是「未取得」，不能由源码测试替代。

### 第十三轮层级与命中区纠偏

- 返回键继续保留 48×48 px 点击区域和 32 px 官方箭头，但生产截图证明沿用菜单通用 `inset:0`
  会让按下圆形越过 57 px 标题栏的可视内容区。Peach 仅把伪元素改为 `inset:4px`，得到
  40 px 内部特效；命中区、焦点和键盘行为不缩小。
- 本地固定的 Video.js 8.24.0 CSS 规定 `.vjs-volume-tooltip` 为 `top:-3.4em;z-index:1`，而 Peach
  进度条位于同一控制栏的绝对定位上排。生产截图证明自动绘制顺序会让蓝色进度线压住 tooltip 底边。
  Peach 为进度层、音量外层和 tooltip 分别锁定 `z-index:2/3/4`，并让 2 px volume bar
  `overflow:visible`；只改变绘制和裁切，不改变百分比、拖动或音量状态。
- 没有再次取得可控浏览器 DOM／修改后截图；视觉验收仍待现有生产标签刷新后真机复核。

### 第十四轮全屏画面填充纠偏

- 当时把问题归因成全屏继续使用 `object-fit:contain`；CSS 已部署且静态资源哈希一致，但修改后画面
  没有取得，随后真机复测确认没有生效。这条根因判断因此撤回，不能再把 CSS 命中当作事实。
- 普通详情页继续使用 `contain`，确保浏览时不裁内容；只有 Video.js 的全屏状态改用 `cover`，让画面覆盖
  `100vw × 100vh`。这是 Peach 的主动差异：非等比例片源会裁掉少量上下或左右边缘，以换取
  全屏无外部黑边，不把它写成 YouTube 默认行为。
- 修改后浏览器画面仍需在真实非 16:9 片源上复核。

### 第十五轮全屏运行态兜底

- 复测用的本地片源 `1275` 经只读 `ffprobe` 确认为 1920×1080、SAR 1:1；连续 8 秒
  `cropdetect` 都返回完整 1920×1080，排除了画面黑边已经编码进这个片源。
- Chrome 能打开并截图真实 `/item/1275`，但自动化点击、DOM 和 CDP 读取连续超时；直接调用按钮的
  Playwright 点击没有 Fullscreen API 所需的真实用户激活，因此不能把页面滚动后的截图冒充全屏验收。
- 全屏填充改为双重状态源：继续接受 Video.js 的 `.vjs-fullscreen`／原生 `:fullscreen`，同时监听播放器
  `fullscreenchange`、`enterFullWindow`、`exitFullWindow`，把 `isFullscreen()` 同步到
  `data-peach-fullscreen`；CSS 还覆盖 `body.vjs-full-window` 回退。这样不再把类名同步时序当成唯一入口。

## 可复用证据

| 项 | 截图证据 | Peach 实现 |
| --- | --- | --- |
| 控制层 | 控件悬浮在画面上；不使用整块背景 | 控制栏透明，不再绘制圆角渐变或毛玻璃面板 |
| 进度 | 独占上排，横跨面板可用宽度 | `vjs-progress-control` 脱离底排 flex，绝对定位到上排 |
| 左下 | 播放、音量、当前时间／总时长共用一条基线 | 播放为 40 px 圆钮；音量胶囊从 40 px 展开；时间胶囊点击切换已播／剩余；前后 10 秒移出控制栏，键盘快进仍保留 |
| 右下 | 实时 DOM 证实四个操作共用一个 40 px 高胶囊，单钮 48×40 px | 使用 Video.js spacer，把四个真实操作推到右侧共享胶囊；按钮只在悬停／按下时显示内部高亮 |
| 进度状态 | 细轨道、品牌色已播段和圆点；卡片缩略图底边显示已观看段 | Peach 把红色替换为 `--tungsten` 蓝，卡片按 `play_seconds / duration` 绘制同色进度 |
| 拖动提示 | 指针时刻上方显示时间；精确拖动时可带缩略图 | 本地媒体复用九宫格近似对应时刻，在线媒体只显示时间，不伪造逐帧预览 |
| 清晰度 | 齿轮图标带 `HD` 状态标记，实际选项在菜单内 | 齿轮显示 `HD`／`4K`，菜单继续只列媒体真实分辨率 |
| 中央播放 | 首次 loading 不与播放键重合；点击后才出现播放／暂停反馈 | 初始只由 Video.js waiting 状态显示 64 px 官方结构 spinner；用户手势触发状态变化后才显示 78 px bezel |
| 中央反馈 | 中央不只播放，还要暂停；第八轮起删除左右快退／快进 | 按状态切换官方 36×36 SVG path 并播放 1 秒反馈动画；键盘跳转保留 |
| 左上工具 | 与播放器按钮统一 | 播放统计键使用相同的 40 px 无描边圆钮、底色和悬停反馈 |
| 左上状态 | Peach 对照图中统计圆钮约 80px 高，加载速度约 75px 高 | CSS 固定两者为相同 40px 逻辑高度、相同 `top:11px` 和 `box-sizing:border-box` |
| 设置面板 | 圆角半透明浮层，主层展示氛围模式、播放速度、清晰度；子层选择实际值 | Peach 复用现有清晰度等级和 Video.js `playbackRate`，加入同层氛围开关与返回式子菜单；不画没有实现的睡眠定时 |
| 影院模式 | 独立图标按钮，展开／收回使用不同图标，悬停提示包含 `T` 快捷键 | 两态图标和「影院模式／默认视图」文案随状态同步；按钮、`T` 键和持久设置共用同一状态 |
| 氛围模式 | 官方说明为视频颜色扩散到背景，深色主题默认开，可在设置中为所有视频关闭 | Peach 原有 32×18 当前帧取色、模糊画布方向一致；补全全局持久开关，并让本地和在线视频共用 |
| 窄屏 | 播放器源码给出门槛与折叠行为（见下节） | 播放器宽度 < 528 时右侧只留设置键和一个 180° 旋转的展开箭头，点开才铺开其余按钮；判据是播放器自身宽度，不是视口 |
| 悬停提示 | `www-player.css` 的毛玻璃提示与快捷键徽标 | 控制条上每个按钮共用 `.vjs-peach-tooltip`，播放 `K`、静音 `M`、画中画 `I`、全屏 `F`、影院 `T`；音量百分比抬到控制条上方并同用这套外观 |

## Peach 主动保留的差异

- 不复制 YouTube 品牌、字幕、睡眠定时和自动播放开关；没有真实能力就不画假按钮。
- 保留 Video.js、键盘前后跳转、画中画、影院模式、全屏和真实清晰度菜单；控制栏与中央覆盖层都不重复画前后 10 秒按钮。
- 单一路源只显示实测分辨率或原画；`HD`／`4K` 只是当前媒体等级标记，不伪造可切换线路。

## 窄屏折叠与悬停提示取证（2026-09-03）

第九轮纠偏：窄屏下按钮显隐仍与 YouTube 不一致、控制条会超框；提示统一成
YouTube 那套（含快捷键）。浏览器面板打不开 youtube.com——文档本身回 200，但每个
`www.youtube.com/s/*` 资源和 `/favicon.ico` 都是 `net::ERR_BLOCKED_BY_CLIENT`，
同一面板里 Wikipedia 完整加载，同一台机器上 Python `urllib` 又能取到观看页和播放器资源。
所以那是浏览器工具自己的客户端拦截，不是网络、代理或 Peach 的问题，也不影响下列取证结果：
证据全部走 Python 通道取自播放器资源本体。

| 项 | 值 |
| --- | --- |
| 播放器版本 | `9470c977`（delhi-modern skin） |
| `www-player.css` | 542,776 字节，SHA-256 `96e3e223db36f20082cbc5b5393a6c783c91cd0d52b4d338857e1c2abbc0deeb` |
| `base.js` | 2,951,684 字节，SHA-256 `f75ee405ab74138520caa120ba03e1e0ba5c98e416ac1bd6bf37087dcb351be8` |
| 窄屏门槛 | `base.js` 里 `v.width<528` 打开 `ytp-xsmall-width-mode` |
| 提示外观 | `rgba(0,0,0,.3)` 底、`blur(16px)` 毛玻璃、8px 圆角、5px 9px 内距、13px/15px 500 字重、`text-shadow:0 0 2px #000` |
| 快捷键徽标 | 1px `rgba(255,255,255,.3)` 边、4px 圆角、最小宽 11px、左外边距 4px |

### Peach 主动保留的差异

- 展开后 YouTube 用 `visibility:hidden` 把时间显示藏起来、位置照占；Peach 用 `display:none`
  让出那段宽度。Peach 的控制条比 YouTube 窄，留着空位在 528 以下会重新超框。
- 门槛用 `ResizeObserver` 观察 `player.el()` 而不是媒体查询：同一个视口下影院模式和
  普通视图的播放器宽度差一大截，用视口判据会在影院模式下白折叠、在普通视图下继续超框。
- 提示在组件内固定 `--surface-radius`（8px）、`--badge-radius`（4px），字号使用 `--fs-sm`（13px），
  数值与 YouTube 一致；按钮和音量提示独立于 Board 通用 tooltip，明暗主题均使用深色底、白字和单行排版。

### 报错文案与统计面板重叠（同轮）

报告的现象是「报错又不是居正中导致重叠」。实测报错文字**本来就是精确居中的**：文字中心
x=476 对播放器中心 x=476，y=437.5 对 437.5，2026-08-28 那次居中修复（`937f360`）完好。
压住它的是 `.playerstats`（`left:11px;top:58px;z-index:8`，实测 l=100 r=488 t=245 b=443），
它盖住了居中那行左侧约 180px。所以改的不是居中：撤掉 Video.js 铺满全画面的渐变，
把报错做成一张自带底色、`z-index:9` 的紧凑卡片。加载失败时统计面板里的编码、分辨率、
体积和请求方式正是要看的东西，不能用报错把它整块糊掉。

## 设置面板动画与展开键取证（2026-09-03，第十五轮之后同日）

第十六轮纠偏：设置面板要有渐入渐退、次级菜单要有进入退出动画；窄屏那个展开键排到左侧、
图标别那么细、也要有和其余按钮一样的 hover。证据仍走 Python 通道取自上表那份
`www-player.css`（播放器 `9470c977`，542,776 字节，SHA-256
`96e3e223db36f20082cbc5b5393a6c783c91cd0d52b4d338857e1c2abbc0deeb`），不是同一版本就不能引用下表。

| 项 | 上游原文 | Peach 实现 |
| --- | --- | --- |
| 浮层淡入 | `.ytp-popup{transition:opacity .1s cubic-bezier(0,0,.2,1)}` | `.vjs-peach-settings-menu` 同曲线同时长 |
| 浮层淡出 | `.ytp-popup[aria-hidden=true]{opacity:0;transition:opacity .1s cubic-bezier(.4,0,1,1)}` | 关闭态由 `aria-hidden="true"` 驱动，同曲线 |
| 面板换层 | `.ytp-popup-animating` 与 `.ytp-popup-animating .ytp-panel` 都是 `all .25s cubic-bezier(.4,0,.2,1)` | 同时长同曲线 |
| 进出方向 | `.ytp-panel-animate-back{opacity:0;transform:translateX(-100%)}`、`.ytp-panel-animate-forward{opacity:0;transform:translateX(100%)}` | 类名与数值一一对应 |
| 展开键图标 | `.ytp-xsmall-width-mode.ytp-delhi-modern-icons .ytp-right-controls .ytp-button:not(.ytp-expand-right-bottom-section-button) svg{width:18px;height:18px;padding:7px}`，紧接着 `.ytp-expand-right-bottom-section-button.ytp-button svg{padding:0}` | 窄屏其余键的 svg 缩到 18px，展开键排除在外并铺满 32px |

### Peach 主动保留的差异

- 上游过渡的是 `all`；Peach 只过渡 `height`（容器）与 `transform`/`opacity`（面板）。`all` 会把
  面板里的 hover 底色、边框和 `visibility` 一并接进这条 .25s 曲线，返回时整块面板要慢半拍才亮。
- 关闭态不用 `display:none`：那样没有可过渡的中间态。`visibility` 用 `transition:visibility 0s .1s`
  延后到淡出结束，面板既退出无障碍树也不再接命中测试。
- 展开键靠左是 Peach 自己定的版式。`www-player.css` 只给出显隐、旋转和图标内边距，**没有**任何
  能推出这一簇按钮排列顺序的规则，`base.js` 里的插入顺序**未取得**；这一条为 Peach 自定顺序，
  不声称复刻。
- hover 高亮沿用 Peach 自己的 `.vjs-peach-hover`（`.vjs-control>.vjs-peach-hover`），因此展开键的
  高亮层必须是按钮的兄弟节点，不能塞进 `<button>` 里。
- 悬停提示底色偏离上游：`www-player.css` 的 `.ytp-tooltip` 是 `rgba(0,0,0,.3)` 配 `blur(16px)`，
  落在 Peach 的亮画面上只剩一块低对比灰板，看着像悬停时凭空多出来一块阴影。Peach 一律
  `rgba(0,0,0,.6)`，和播放键、音量条、统计钮这些悬浮件同一档黑。

### 同轮的两个非参考项

- 加载速度徽标改成仪表盘图标加一行速率，与 YouTube 无关：Peach 自己的浮层，图标取本地
  sprite 的 `i-gauge`（lucide 描边图形），容器必须声明 `stroke:currentColor;fill:none`，
  否则按 SVG 默认的 fill 画成黑色实心块。
- 窄屏两个浮层超框由 Peach 自己的版式决定：390 宽视口上 16:9 的播放器只有 200 出头的高，
  设置面板要 212、统计面板要 256。播放器给 320px 最低高度改成上下留黑边，两个浮层各自按
  播放器高度收顶。窄屏设置面板的高度上限走 `--peach-player-h`——面板的定位祖先只有 36px 高，
  百分比取不到播放器。收顶取播放器高度减 74px：面板底边离播放器底边 66px（离设置键 52px
  加控制条 14px），顶上再留 8px，320px 的播放器给出 246px。选项多到八档的列表单列要
  57+16+8×48=457px，窄屏改成选项多于四条时排两列、行高压到 44px，57+8+4×44=241px。

## 播放速度面板与中心提示取证（2026-09-03，同日第十七轮）

第十七轮纠偏：播放速度的样式照 YouTube 对齐；开关静音要在画面中心显示图标；播放键与
静音键点击要有和 YouTube 一样的渐变动画；标题行那两个动作紧贴文字末尾、上下别挤在一起。
证据是同日重取的同一版播放器 `9470c977`：`www-player.css` 542,776 字节，SHA-256
`96e3e223db36f20082cbc5b5393a6c783c91cd0d52b4d338857e1c2abbc0deeb`（与上一节一致）；
`player_ias.vflset/en_US/base.js` 2,951,684 字节，SHA-256
`f75ee405ab74138520caa120ba03e1e0ba5c98e416ac1bd6bf37087dcb351be8`。

| 项 | 上游原文 | Peach 实现 |
| --- | --- | --- |
| 面板内容区 | `.ytp-variable-speed-panel-content{display:flex;flex-direction:column;padding:24px 16px 16px}` | `.vjs-peach-speed-panel` 同值 |
| 倍速读数 | `.ytp-speed-display-container{display:flex;justify-content:center;margin-bottom:24px}`、`.ytp-variable-speed-panel-display{font-size:18px;font-weight:900;line-height:22px}` | `.vjs-peach-speed-display` 居中、下留 24px、行高 22px，字号字重见下方差异 |
| 滑条一行 | `.ytp-variable-speed-panel-slider-container{display:flex;gap:16px;margin-bottom:24px}`，内含 `.ytp-input-slider{width:100%}` | `.vjs-peach-speed-slider` 同值，滑条 `flex:1 1 auto;width:100%` |
| 加减键 | `.ytp-variable-speed-panel-slider-container .ytp-variable-speed-panel-button{font-size:24px;width:32px}`；`base.js` 的 `aria-label` 是 `Decrease playback speed 0.05` / `Increase playback speed 0.05`，动作是 `setPlaybackRate(Number((getPlaybackRate()∓.05).toFixed(2)))` | 32px 圆键、24px 字，每次 ±0.05 并按两位小数收敛 |
| 预设胶囊 | `.ytp-variable-speed-panel-chips{display:flex;gap:8px;align-items:flex-start}`、`.ytp-variable-speed-panel-chips .ytp-variable-speed-panel-button{font-size:12px;width:53px;display:flex;align-items:center;justify-content:center;gap:4px}` | `.vjs-peach-speed-chips` 与 `.vjs-peach-speed-chips .vjs-peach-speed-button` 同值 |
| 胶囊外观 | `.ytp-variable-speed-panel-button{height:32px;border-radius:16px;background-color:rgba(255,255,255,.1);font-weight:500;line-height:16px;transition:background-color .2s cubic-bezier(.05,0,0,1)}`，`:hover` `.2`、`:active` `.3` | 同高、同底色、同悬停与按下、同曲线 |
| 1.0 的说明 | `.ytp-variable-speed-panel-preset-button-wrapper{display:flex;flex-direction:column;align-items:center}`、`.ytp-variable-speed-panel-preset-button-label-text{font-size:10px;line-height:14px;margin-top:4px;font-weight:400;color:rgba(255,255,255,.7)}` | `.vjs-peach-speed-preset` 与 `.vjs-peach-speed-preset-label`，写「正常」，行高 14px、字重 400、同色 |
| 滑条轨道 | `.ytp-input-slider::-webkit-slider-runnable-track{height:4px;border-radius:12px;background:linear-gradient(to right,#fff 0,#fff var(--yt-slider-shape-gradient-percent),#666 …)}`，`.ytp-varispeed-input-slider` 那条把 `#666` 换成 `#909090` | 轨道 4px、同渐变结构，余下部分取倍速专用的 `#909090`，比例走 `--peach-speed-percent` |
| 滑条把手 | `.ytp-input-slider::-webkit-slider-thumb{background:#fff;width:16px;height:16px;border-radius:8px;margin-top:-6px}` | 同值 |
| 滑条区间 | `base.js` 里滑条构造成 `(可用倍速[0], 可用倍速[末], .05, 当前倍速)` | 两端取播放器支持的最低与最高倍速（0.25–2），步进 0.05 |
| 中心提示圆 | `.ytp-delhi-modern .ytp-bezel{left:50%;top:50%;width:78px;height:78px;margin-left:-39px;margin-top:-39px;border-radius:39px;backdrop-filter:blur(16px);background:rgba(0,0,0,.6)}`、`.ytp-bezel{position:absolute;z-index:19;pointer-events:none}` | `.vjs-peach-bezel` 同值，圆角写 50% |
| 提示图标 | `.ytp-delhi-modern .ytp-bezel-icon{width:54px;height:54px;margin:12px}`；窄屏 `.ytp-delhi-modern.ytp-xsmall-width-mode .ytp-bezel{width:64px;height:64px;…}` 配 `48px` 图标 | `.vjs-peach-bezel-icon` 54px，窄屏 64px 圆配 48px 图标 |
| 提示动画 | `animation:ytp-delhi-modern-bezel-fadeout 1s cubic-bezier(.05,0,0,1) 1 normal forwards`，关键帧 `0%{opacity:0}25%,75%{opacity:1;transform:scale(1.33)}to{opacity:0;transform:scale(1)}` | `peach-bezel-fadeout` 同曲线同关键帧 |
| 提示时长 | `base.js` 里 delhi-modern 的隐藏定时是 1000ms（其余皮肤 500ms） | 同为 1000ms |
| 提示语义 | `<div class="ytp-bezel" role="status" aria-label="{{label}}">` | 同为 `role="status"`，`aria-label` 写这一次的动作名 |
| 展开键箭头 | 该按钮自带图标：`viewBox="0 0 32 32"`、`width/height:100%`、路径 `m 12.59,20.34 4.58,-4.59 -4.58,-4.59 1.41,-1.41 6,6 -6,6 z`（两个单位粗），配 `svg{padding:0}` | 新增 sprite `i-player-expand`，同视框同路径，窄屏铺满 32px |
| 窄屏悬停底 | `.ytp-delhi-modern.ytp-xsmall-width-mode .ytp-chrome-bottom .ytp-button:not(.ytp-live-badge):before{border-radius:50%;height:32px;width:32px}`，按钮本体 `32px` 配 `margin:4px 2px` | 窄屏这一排的 `.vjs-peach-hover` 收成 32×32 正圆，落在 40px 行高里 36px 的一格中间 |

### Peach 主动保留的差异

- 五格预设 1.0／1.25／1.5／2.0／3.0 里，3.0 那格不画 Premium 角标（`.ytp-variable-speed-panel-chips`
  里那 `gap:4px` 就是给角标留的）：本机装的 Peach 没有会员分级，角标画出来是个永远点得动的
  假标记。倍速本身照上游留着，滑条上限跟着抬到 3。
- 读数、说明、胶囊圆角走 Peach 自己的 token：上游读数那档 `18px/900` 和说明那档 `10px` 都不在
  Peach 的字号刻度上，`900` 也不在 Geist 的三档字重里，`tests/test_web_ui.py` 的三条词汇表测试
  直接拒收。胶囊 32px 高、轨道 4px 高，圆角本来就大于自身高度的一半，`--pill-radius`（999px）
  的渲染结果与上游的 `16px`／`12px` 逐像素相同。
- 不做 `.ytp-bezel-text`（上游调音量时在画面上方写百分比）。Peach 的音量条上方常驻百分比，
  再叠一层就是同一个数字出现两处。
- 中心提示由控制条上的捕获阶段点击触发，认 `.vjs-play-control` 与 `.vjs-mute-control`。上游的
  bezel 由它自己的动作分发层驱动，「点击控制栏按钮是否也闪」的判据**未取得**；Peach 让点击也
  触发该动画，不声称此处触发时机复刻 YouTube。
- 标题行的定位文件与刷新紧跟标题文字末尾，是 Peach 自己定的版式，与 YouTube 无关。行内块加
  上下各 3px 外边距，把它所在那一行的行盒撑到 32px。代价写清楚：最后一行剩余宽度放不下这
  66px 时，浏览器会把这两个按钮移到下一行开头——「紧贴文字末尾」和「永不单独占一行」在
  行内流里不可能同时成立。

## 图标形变取证（2026-09-04，第十八轮）

第十八轮纠偏：播放键和音量键要有图标自己扭曲形变的动画，照 YouTube；3.0 那一格留下来。
证据是同一版播放器 `9470c977` 的 `player_ias.vflset/en_US/base.js`，2,951,684 字节，SHA-256
`f75ee405ab74138520caa120ba03e1e0ba5c98e416ac1bd6bf37087dcb351be8`，与前两节同一份文件。

| 项 | 上游原文 | Peach 实现 |
| --- | --- | --- |
| 路径拆记号 | `t76=/[0-9.-]+\|[^0-9.-]+/g`，`p5O(d)` 把 `d` 拆成数字与分隔符两类记号 | 交给浏览器插值 CSS `d`，分隔符逐位不变、数字逐位插值，与之同构 |
| 逐位插值 | `c$x(v,q,x)` 遍历记号，数字写成 `Z+(q[y]-Z)*x`，非数字原样拼回 | 同 |
| 时长与曲线 | `eST(...,200)`，缓动 `qn3=new bh(0,0,.4,0,.2,1,1,1)` | `.vjs-peach-morph-icon path{transition:d .2s cubic-bezier(.4,0,.2,1)}` |
| 播放／暂停路径 | `M2e(v,q)` 里 case 1 是 `dD`（播放）、case 2 是 `JBx`（暂停，两根竖杠）、case 4 是 `ABg`（停止，圆角方块）；`showPlaybackIcon` 里 `H6(this,q,"Pause")` 取 `JBx`、`"Stop playback"` 取 `ABg`。三条都是 `viewBox="0 0 36 36"`、各 233 个记号其中 116 个数字，命令序列同为 `MLCCLCCLCCLCCZMLCCVCCLCCVLCCZ` | sprite `i-player-play`／`i-player-pause` 用的是 `dD` 与 `JBx` |
| 音量两道弧 | `jjc(q)`：内弧 `translate(18, 12) scale(1-q) translate(-18,-12)`，外弧 `translate(22, 12) scale(B-q) translate(-22, -12)`，由 `YFs(...,250)` 驱动 | `.vjs-peach-volume-arc-inner`／`-outer` 同中心同缩放，`transition:transform .25s` 同曲线 |
| 外弧的音量档 | `setVolume` 里 `v=X===0?1:v>50?1:0`，静音或零音量按 1 处理再被 `jjc` 收掉 | `data-loud` 认音量过半，`data-silent` 为真时两道弧一起收成 0 |
| 叉号出现时机 | 缩放跑完才把图标换成带叉号的那张（`q===1` 那一支） | 叉号是同一个 svg 里的第四条路径，`transition:opacity 0s linear .25s` |
| 取消静音 | 先换回不带叉号的图标，再把两道弧放大回去 | 无延迟那条规则让叉号立刻消失，弧同时开始长回来 |
| 播放键图标框 | `.ytp-delhi-modern .ytp-chrome-controls .ytp-play-button{width:56px;height:56px}` 配 `svg{padding:10px}`，图标框 36px | 键 40px，同比例给 26px 的图标框，画出来 18.6px |
| 静音键图标框 | delhi-modern 这一排的按钮 svg 18px | 同为 18px，视框仍是 24 |

### Peach 主动保留的差异

- 形变由浏览器的 CSS `d` 与 `transform` 过渡完成，不自带逐帧补间。Chromium 上实测中间值就是
  逐位线性插值的结果（`M 17 8.6` 与 `M 18 6` 之间 0.4 处读到 `M 17.4063 7.54362`），和上游
  `c$x` 同构。不支持插值 `d` 的浏览器退回瞬间切换，也就是没有这段动画时的样子。
- 五格胶囊按 53px 起算，`min(274px,…)` 的面板放不下五格时一起收窄到 42px。上游那块面板是内容
  宽度，五格 53px 加四个 8px 间隔再加 32px 内距会撑到 329px；Peach 的设置面板宽度是整块共用的，
  只为一个视图改宽会把菜单之间的切换动画一起牵动。

## 暂停键、常驻圆点与静音图标取证（2026-09-04，第十九轮）

第十九轮纠偏三处：左下角暂停键该是两根竖杠、进度条上的圆点该常驻、静音切换的动画和
图标都不对。证据仍是 player `9470c977` 的 `player_ias.vflset/en_US/base.js`，2,951,684 字节，
SHA-256 `f75ee405ab74138520caa120ba03e1e0ba5c98e416ac1bd6bf37087dcb351be8`，以及同版
`www-player.css`，与前几节同一份文件。

| 项 | 上游原文 | Peach 实现 |
| --- | --- | --- |
| 暂停键 | `JBx`：`M 12.75 4.5 … Z M 26.25 4.5 … Z`，两根 7.5 宽、24.75 高、圆角 2.25 的竖杠 | sprite `i-player-pause` 用的就是这条 |
| 停止键 | `ABg`：同结构的圆角方块，`M 18 6 L 9 6 …`，`M2e` 里归 case 4 | 不做：Peach 的控制条没有停止键 |
| 圆点静止态 | `.ytp-delhi-modern .ytp-scrubber-button{height:12px;width:12px;border-radius:6px}`，静止就是 12px 的圆，只有 `.ytp-hide-scrubber-button` 才 `scale(0)` | 圆点常驻 12px |
| 圆点悬停态 | `.ytp-progress-bar-container:hover .ytp-scrubber-button{transform:scale(1.67);transition:transform .2s cubic-bezier(.05,0,0,1)}` | 同值同曲线 |
| 圆点与轨道的关系 | 压扁的是 `.ytp-progress-list`（`scaleY(.667)`，悬停回 `none`），滑块在 `.ytp-scrubber-container` 里，是它的兄弟，不被压 | 圆点是 `.vjs-play-progress:before`，在被压那层里面，所以静止态自带 `scale(1,1.5)` 抵掉纵向压缩 |
| 静音图标 | `jjc` 走到 `q===1` 时 `v.updateValue("icon",x)` 换掉整个 `<svg>`，换上的那张是单条路径：喇叭外框 + `M4.94 8.4…` 的挖空 + `M21.29 8.29…` 的叉号 | 三条子路径拆成三个 `<path>` 放同一个 svg，靠 `opacity` 在 250ms 那一刻整块换 |
| 喇叭外框 | 有声那张的 `ytp-svg-volume-animation-speaker` 与静音那张的第一段子路径是同一串数字 | 实心与挖空两条共用这串数字，换的那一刻只有洞出现，轮廓不动 |
| 叉号 | `M21.29 8.29…`，横跨 x15..23、绕 (19,12)，圆头 | 同一条路径原文 |
| 切换时机 | 先缩弧再换图标；取消静音先换回图标再放大弧 | `transition:opacity 0s linear .25s` 与无延迟那条各管一个方向 |

### Peach 主动保留的差异

- 上游换的是整个 `<svg>`，Peach 换的是同一个 svg 里的 `opacity`：这两个键的 `d` 要挂 CSS 过渡，
  换掉 svg 就等于换掉正在过渡的元素，形变会断。挖空与实心共用同一串外框数字，所以视觉结果
  与换图标一致。
- 挖空靠上游原文的子路径顺序与绕向（nonzero），没有改成 `fill-rule:evenodd`。
- 静音那张图标的两道弧不参与：上游那张图标里没有弧，Peach 是把弧缩成 0 再让图标顶上。

### 纠正记录

第十八轮那一行「播放／暂停路径……sprite 用的就是这两条路径」把 `ABg` 当成了暂停：`ABg` 是
`M2e` 里 case 4 的停止键，画出来是圆角方块，也就是纠偏截图里那个白方块。暂停是 case 2 的
`JBx`。三条路径记号结构相同，看数字长度分辨不出来，得读 `M2e` 的 case 与 `showPlaybackIcon`
传的标签。

## 中心提示圆、音量读数与 ± 键取证（2026-09-04，第二十轮）

第二十轮纠偏三处：点左下角播放键时中心圆里两个图标叠着、音量提示的背景不对、± 键
没居中。前两项的判据是 player `9470c977` 的 `player_ias.vflset/en_US/base.js` 与同版
`www-player.css`，与前几节同一份文件；第三项是本机测量。

| 项 | 上游原文 | Peach 实现 |
| --- | --- | --- |
| 中心提示圆 | `.ytp-bezel` 整个播放器只有一个，`showBezel` 每次改写同一个节点的图标 | 只有 `.vjs-peach-bezel` 一个 78px 圆，触发挂在 `player.el()` 上，控制条、画面与静音键都喂它 |
| 音量读数定位 | 数字块由 `.ytp-volume-slider` 的 `left` 定位，`right` 不参与 | `.vjs-volume-tooltip{right:auto!important}` 盖掉 Video.js 每帧写的行内 `style.right`，宽度只由内容决定 |
| 音量条底衬 | 与右侧控件同一层玻璃 | `background:rgba(0,0,0,.6)` 配 `blur(16px)`，与右侧控件药丸同值 |
| 速度加减键 | 24px 文字字形 `−` / `+` | sprite `#i-minus`／`#i-plus`，24px（见下） |

### Peach 主动保留的差异

- 加减键不用文字字形：24px Roboto 的 `−`／`+` 在 32px 按钮里按行盒居中，墨迹相对按钮中心
  实测偏移 dx −1.84px、dy +3.00px，看上去就是没居中。sprite 的路径本身对称，几何居中即墨迹
  居中，也不必为一个符号引入字体度量。
- 中心圆只允许一个：播放态提示、静音提示和缓冲态共用它，缓冲与报错时由 CSS 让它退场，
  由 `.vjs-loading-spinner` 顶上。同时存在第二个提示圆，两处各自读播放态，就会在同一格里
  叠出方向相反的两个图标。
