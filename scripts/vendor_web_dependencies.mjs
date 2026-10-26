import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const checkOnly = process.argv.includes("--check");
const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const lock = JSON.parse(readFileSync(join(root, "package-lock.json"), "utf8"));
const versions = manifest.devDependencies;
const expectedFiles = new Map();

const read = (...parts) => readFileSync(join(root, ...parts));
const text = (...parts) => read(...parts).toString("utf8");
// .gitattributes 让整仓签出 LF，而 npm 包里的许可证原文有 CRLF 的。照抄字节的话，
// 提交后签出来的那份和这里算出来的期望值差在换行上，--check 每次都报未同步。
const lfText = (...parts) => text(...parts).replace(/\r\n/g, "\n");
const sha256 = value => createHash("sha256").update(value).digest("hex").toUpperCase();
const integrity = name => lock.packages[`node_modules/${name}`]?.integrity || "未取得";
const stage = (path, value) => expectedFiles.set(path, Buffer.isBuffer(value) ? value : Buffer.from(value));

const copyPackageFiles = ({ packageName, vendorName, files, note }) => {
  const version = versions[packageName];
  const hashes = [];
  for (const [source, destination] of files) {
    const payload = read("node_modules", packageName, ...source.split("/"));
    const target = `web/vendor/${vendorName}/${version}/${destination}`;
    stage(target, payload);
    hashes.push(`- \`${destination}\` SHA-256：\`${sha256(payload)}\``);
  }
  const license = lfText("node_modules", packageName, "LICENSE");
  stage(`web/vendor/${vendorName}/${version}/LICENSE`, license);
  stage(`web/vendor/${vendorName}/${version}/ORIGIN.md`,
    `# ${packageName} ${version}\n\n` +
    `- npm 包：\`${packageName}@${version}\`\n` +
    `- npm lock integrity：\`${integrity(packageName)}\`\n` +
    `- 许可证：见同目录 \`LICENSE\`\n` +
    `${hashes.join("\n")}\n\n${note}\n`);
};

copyPackageFiles({
  packageName: "@fontsource-variable/inter",
  vendorName: "inter",
  files: [["index.css", "index.css"], ...readdirSync(join(root, "node_modules/@fontsource-variable/inter/files"))
    .filter(name => name.endsWith("-wght-normal.woff2")).map(name => [`files/${name}`, `files/${name}`])],
  note: "Board 界面使用 Inter 可变字体；中文由系统中文字体补齐。",
});

// 官方 CDN 包只分发播放器消费的 ESM 静态导入闭包。
const videoPackage = "@videojs/cdn";
const videoRoot = join(root, "node_modules", videoPackage);
const videoEntries = [
  "video-player.js", "media/hlsjs-video.js", "media/dash-video.js", "ui/container.js", "ui/play-button.js",
  "ui/mute-button.js", "ui/fullscreen-button.js", "ui/pip-button.js", "ui/time-slider.js",
  "ui/volume-slider.js", "ui/slider-track.js", "ui/slider-fill.js", "ui/slider-buffer.js",
  "ui/slider-thumb.js", "ui/slider-value.js", "i18n.js", "locales/zh-CN.js",
];
const videoFiles = new Set();
const visitVideoModule = (name) => {
  if (videoFiles.has(name)) return;
  if (name.startsWith("../") || !name.endsWith(".js")) throw new Error(`播放器模块越界：${name}`);
  videoFiles.add(name);
  const contents = text("node_modules", videoPackage, name);
  for (const match of contents.matchAll(/(?:from\s*|import\s*\(?)["'`](\.[^"'`]+)["'`]/g)) {
    visitVideoModule(relative(videoRoot, resolve(videoRoot, dirname(name), match[1])).replaceAll("\\", "/"));
  }
};
videoEntries.forEach(visitVideoModule);
copyPackageFiles({
  packageName: videoPackage,
  vendorName: "videojs",
  files: [...videoFiles].sort().map(name => [name, name]),
  note: "Peach 自托管 Video.js 10 官方 ESM 模块及其导入闭包。控制器复用 @videojs/html，媒体复用 @videojs/hlsjs-video 与 @videojs/dash-video；原生 video 经 media 插槽承载 MP4、HLS、DASH 与字幕。\n\n"
    + `- hls.js 1.6.19（Apache-2.0）：\`hls.js.LICENSE.txt\` SHA-256：\`${sha256(lfText("resources", "licenses", "hlsjs-1.6.19-LICENSE.txt"))}\`\n`
    + `- dash.js 5.2.0（BSD-3-Clause）：\`media/dash.all.min.js.LICENSE.txt\` SHA-256：\`${sha256(lfText("resources", "licenses", "dashjs-5.2.0-LICENSE.txt"))}\``,
});
stage(`web/vendor/videojs/${versions[videoPackage]}/media/dash.all.min.js.LICENSE.txt`,
  lfText("resources", "licenses", "dashjs-5.2.0-LICENSE.txt"));
stage(`web/vendor/videojs/${versions[videoPackage]}/hls.js.LICENSE.txt`,
  lfText("resources", "licenses", "hlsjs-1.6.19-LICENSE.txt"));

copyPackageFiles({
  packageName: "swiper",
  vendorName: "swiper",
  files: [
    ["swiper-bundle.min.js", "swiper-bundle.min.js"],
    ["swiper-bundle.min.css", "swiper-bundle.min.css"],
  ],
  note: "Peach 自托管固定版本，不依赖 CDN。只有照片灯箱按需加载 Thumbs、Keyboard 与 Zoom；图片墙不经过 Swiper。",
});

const lucideIcons = new Map([
  // 处理速度的低占用、标准与全速刻度；全速叠放同一枚闪电。
  ["snail", "snail"], ["zap", "zap"],
  ["heart-hand","hand-heart"], ["flame","flame"], ["cherry","cherry"], ["gem","gem"],
  ["crown","crown"], ["shirt","shirt"], ["footprints","footprints"], ["flower","flower"],
  ["venetian-mask","venetian-mask"], ["hand","hand"], ["bed-double","bed-double"],
  // 媒体库图标的候选，只归设置页的图标选择器：题材、身份、场景与媒介各一组。
  ["circle-alert","circle-alert"], ["lollipop","lollipop"], ["candy","candy"], ["banana","banana"], ["droplets","droplets"],
  ["venus","venus"], ["mars","mars"], ["venus-and-mars","venus-and-mars"], ["ribbon","ribbon"],
  ["graduation-cap","graduation-cap"], ["stethoscope","stethoscope"], ["glasses","glasses"],
  ["rabbit","rabbit"], ["paw-print","paw-print"], ["dumbbell","dumbbell"], ["bath","bath"],
  ["key-round","key-round"], ["wine","wine"], ["cigarette","cigarette"], ["film","film"],
  ["image","image"], ["gamepad-2","gamepad-2"],
  ["camera","camera"], ["video","video"], ["scan-search","scan-search"],
  ["clapperboard", "clapperboard"], ["briefcase", "briefcase"],
  // 女优资料页头（ADR-0069）：名字那一行的别名，右侧资料表的生日、身材、出道与出演期间。
  ["id-card", "id-card"], ["cake", "cake"], ["ruler", "ruler"], ["flag", "flag"],
  ["calendar-range", "calendar-range"],
  ["home", "home"], ["panel-left", "panel-left"], ["search", "search"],
  ["layout-grid", "layout-grid"], ["square-check-big", "square-check-big"],
  ["columns-2", "columns-2"],
  ["refresh-cw", "refresh-cw"], ["user-round", "user-round"], ["tags", "tags"],
  ["list-filter", "list-filter"], ["chart", "chart-no-axes-column"],
  ["settings", "settings"], ["gauge", "gauge"],
  ["history", "history"], ["sparkles", "sparkles"], ["star", "star"],
  ["x", "x"], ["folder-open", "folder-open"], ["info", "info"],
  ["hard-drive", "hard-drive"], ["globe", "globe"], ["rss", "rss"],
  ["thumbs-up", "thumbs-up"], ["thumbs-down", "thumbs-down"], ["eye", "eye"],
  ["eye-off", "eye-off"], ["grip-vertical", "grip-vertical"], ["trash", "trash-2"],
  // 卡片右上角那个点点点菜单的触发钮，和菜单里的「编辑名称」。
  ["ellipsis", "ellipsis"], ["pencil", "pencil"],
  // 动作图标：一个动作一枚，不共用「转圈」当万能替身。`shuffle` 不在这里：它的几何
  // 取自 Lucide，但为忙态动效拆成两条带 pathLength 的 strand，归 handDrawnIcons。
  ["unlink", "unlink"], ["git-compare", "git-compare"],
  ["compass", "compass"], ["folder-sync", "folder-sync"], ["expand", "expand"],
  ["zoom-in", "zoom-in"], ["zoom-out", "zoom-out"],
  ["plus", "plus"], ["minus", "minus"], ["check", "check"], ["check-check", "check-check"],
  ["rotate-ccw", "rotate-ccw"], ["rotate-cw", "rotate-cw"], ["maximize", "maximize"],
  ["chevron-left", "chevron-left"], ["chevron-right", "chevron-right"],
  ["chevron-up", "chevron-up"], ["chevron-down", "chevron-down"], ["heart", "heart"],
  ["upload", "upload"], ["database", "database"], ["play", "play"], ["clock", "clock"],
  ["external-link", "external-link"], ["bookmark-plus", "bookmark-plus"],
  ["bookmark", "bookmark"], ["gallery-vertical-end", "gallery-vertical-end"],
  ["gallery-thumbnails", "gallery-thumbnails"],
  ["notebook-pen", "notebook-pen"], ["search-x", "search-x"],
  ["file-archive", "file-archive"], ["file-audio", "file-audio"],
  ["file-stack", "file-stack"],
  // 关注列表的表格视图：行列格子，和「默认视图」那枚网格并排。
  ["table", "table"],
  // 关注图片墙「仅显示图片」：划掉字幕，卡片只留图不留文字；不与视频／图片切换那两枚重复。
  ["captions-off", "captions-off"],
  // 关注详情的「隐藏这张图」：划掉的是当前这一张，不是整个条目（那是 eye-off）。
  ["image-off", "image-off"],
  ["arrow-up", "arrow-up"], ["arrow-down", "arrow-down"],
  ["calendar", "calendar"], ["download", "download"], ["monitor", "monitor"],
  // 侧栏「管理」那一层：收拾库里的东西。`settings` 只归右上角的界面偏好。
  ["wrench", "wrench"],
  // 管理菜单里的「配置」：这台电脑的媒体目录与端口。
  ["folder-cog", "folder-cog"],
  // 配置页每行文件夹的「选择文件夹」：弹系统对话框去挑。`folder-open` 归「打开位置」，不兼任。
  ["folder-search", "folder-search"],
  // 详情标题旁的「裁剪封面」：和同一排的定位、同步删除同为线条字形、同一线宽。
  ["crop", "crop"],
  ["sun", "sun"], ["moon", "moon"],
  // 小窗播放：右键菜单里「迷你播放器」是缩进角落的小屏，小窗上的「展开」是对角撑开；
  // `maximize` 归 JAV 大图版式，不兼任。「循环播放」与「复制视频网址」照 Lucide 本名。
  ["picture-in-picture-2", "picture-in-picture-2"], ["maximize-2", "maximize-2"],
  ["repeat", "repeat"], ["link", "link"],
]);

// 自绘 symbol：没有上游可对，所以在这里逐个点名。下面那道分区检查要求雪碧图里
// 每一枚要么由某一套生成、要么写在这张名单上——漏一枚就会被当成忘了纳管。
// 这张名单只收 `i-` 开头、能被 `<use>` 引用的图标；symbol 内部的遮罩、渐变一类
// 零件不带那个前缀，也就不进这张名单。
const handDrawnIcons = new Set([
  "check-check-outline", // 叠勾的空心状态，由用户指定保持勾形轮廓。
  "shuffle", // 两条带 pathLength 的动画路径由 Peach 维护。
  "alert", "pics", "jav", "theater-enter", "theater-exit",
  // 外部入口那两个站的标记：图标集里没有，抠自站点自己的标识文件，来源与取回日期写在
  // index.html 各自那段注释里。MISSAV 没有图形标识，它那枚是按站点 CSS 排的字，不进雪碧图。
  "brand-minnano", "mark-javdb",
  // 「换一批」：Lucide shuffle 的线条拆成 strand-a／strand-b 两条 path 供忙态逐条画出，
  // 上游一刷新就会把两条并回五条，所以由手工维护。
  "shuffle",
]);

const svgInner = source => {
  const match = source.match(/<svg[^>]*>([\s\S]*?)<\/svg>/);
  if (!match) throw new Error("上游 SVG 结构无法识别");
  return match[1].trim().split(/\r?\n/).map(line => line.trim()).join("");
};

let index = text("web", "index.html");
for (const [symbol, icon] of lucideIcons) {
  let inner = svgInner(text("node_modules", "lucide-static", "icons", `${icon}.svg`));
  if (symbol === "rss") inner = inner.replace('<circle cx="5" cy="19" r="1" />', '<circle cx="5" cy="19" r="1" fill="currentColor" stroke="none"/>');
  if (symbol === "grip-vertical") inner = inner.replaceAll(' r="1" />', ' r="1" fill="currentColor" stroke="none"/>');
  /* 忙态动画的挂点：class 与 pathLength 只服务 dasharray 动画，静止时没有任何效果。
     上游不会带这些标注，换版本一刷新就会被抹掉，所以和 shuffle 的 strand 一样在这里补。 */
  if (symbol === "git-compare") inner = inner
    .replace('<circle cx="18" cy="18" r="3" />', '<circle class="gc-part gc-end-a" pathLength="100" cx="18" cy="18" r="3"/>')
    .replace('<path d="M13 6h3a2 2 0 0 1 2 2v7" />', '<path class="gc-part gc-line-a" pathLength="100" d="M13 6h3a2 2 0 0 1 2 2v7"/>')
    .replace('<path d="M11 18H8a2 2 0 0 1-2-2V9" />', '<path class="gc-part gc-line-b" pathLength="100" d="M11 18H8a2 2 0 0 1-2-2V9"/>')
    .replace('<circle cx="6" cy="6" r="3" />', '<circle class="gc-part gc-end-b" pathLength="100" cx="6" cy="6" r="3"/>');
  const pattern = new RegExp(`<symbol id="i-${symbol}" viewBox="0 0 24 24">[\\s\\S]*?<\\/symbol>`);
  if (!pattern.test(index)) throw new Error(`缺少 Lucide symbol：${symbol}`);
  index = index.replace(pattern, `<symbol id="i-${symbol}" viewBox="0 0 24 24">${inner}</symbol>`);
}

// Phosphor 是填充图标，Peach 全局是描边：填充声明写在 symbol 上，路径不改一个字，
// 换版本时不必再核每条 path 有没有被补过 fill。
// viewBox 也不原样照抄：每套图标在自己画格里留的白不一样，同样 15px 画出来一大一小。
// 这里的框是量出来的——把内容外框补到 Lucide 的 20/24 活区，字形按高、图形按长边。
const phosphorIcons = new Map([
  ["text-aa", { icon: "text-aa", viewBox: "-7.3 32.8 262.5 182.9" }],
  ["playlist", { icon: "playlist", viewBox: "10.4 10.5 259.2 259.2" }],
]);
for (const [symbol, { icon, viewBox }] of phosphorIcons) {
  const inner = svgInner(text("node_modules", "@phosphor-icons/core", "assets", "regular", `${icon}.svg`));
  const attrs = `viewBox="${viewBox}" fill="currentColor" stroke="none"`;
  const pattern = new RegExp(`<symbol id="i-${symbol}" ${attrs.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}>[\\s\\S]*?<\\/symbol>`);
  if (!pattern.test(index)) throw new Error(`缺少 Phosphor symbol：${symbol}`);
  index = index.replace(pattern, `<symbol id="i-${symbol}" ${attrs}>${inner}</symbol>`);
}

// 社媒品牌标记：各家自己的场色圆盘，字形留白。
//
// 一枚牌子的形状不是我们能设计的东西，所以不自己画；同一套图标库出的七枚，笔画粗细和
// 圆角风格本来就是一致的，各站点自己的 favicon 拼不出这种一致。颜色同理：X 是黑底白字、
// Instagram 是那道粉紫，认出是哪一家靠的正是这个，跟着界面主题变反而认不出。场色和白
// 字形都是人家标识的一部分，不是界面 token，所以写死在这里、不吃 currentColor。
// 场色铺满整个 viewBox，圆角交给外面那层 `.entitylinkicon`（`--badge-radius` 加
// `overflow:hidden`）去裁：官网那一格的 favicon 也是这么裁成圆角方块的，社媒标记和它站在
// 同一排，一排圆盘夹着几块圆角方片读起来是两种控件。深色主题下黑块不悬空：它画在
// `.entitylinks a` 那圈框里，框自带 1px 边，块与页面之间始终隔着一条边界。
//
// 字形按 24×.5/256 缩到中心，占边长的一半，所以四条边到字形之间总有一圈呼吸空间，外圈
// 那点圆角裁掉的只是场色的角、碰不到字形：社媒标记是拿来认牌子的，裁掉一角就不是那个
// 牌子了。缩放写在一层 `<g>` 上而不是逐条 path 上——上游哪天把一个 logo 拆成两条路径或
// 补一个 `<circle>`，包在外面这一层照样把它们一起缩进场里。
//
// Instagram 的场是渐变不是单色，写成 symbol 内部一条 `linearGradient`。放在 symbol 里
// 而不是雪碧图顶层：`<use>` 克隆整棵子树，引用在它自己那份影子树里就解析得到。
//
// 覆盖范围是「图标库里有的常见社媒」。库里没有的（lit.link、pub.linx.live、livedoor、
// ameblo 这类）继续走 `/link-mark`，那边按站点自己的图标合成。
const GRADIENT_FIELD = "gradient";
const brandDiscs = new Map([
  ["brand-x", ["x-logo", "#000000"]],
  ["brand-instagram", ["instagram-logo", GRADIENT_FIELD]],
  ["brand-threads", ["threads-logo", "#000000"]],
  ["brand-tiktok", ["tiktok-logo", "#000000"]],
  ["brand-youtube", ["youtube-logo", "#FF0000"]],
  ["brand-facebook", ["facebook-logo", "#0866FF"]],
  ["brand-linktree", ["linktree-logo", "#43E660"]],
]);
const GLYPH_TRANSFORM = "translate(12 12) scale(.046875) translate(-128 -128)";
// Instagram 的场从左下走到右上，三停取自它自己那枚 glyph 的用色。
const GRADIENT_STOPS = [["0", "#FFDD55"], [".45", "#FF543E"], ["1", "#C837AB"]];
for (const [symbol, [icon, field]] of brandDiscs) {
  const inner = svgInner(text("node_modules", "@phosphor-icons/core", "assets", "regular", `${icon}.svg`));
  const graded = field === GRADIENT_FIELD;
  const defs = graded
    ? `<linearGradient id="${symbol}-field" x1="2" y1="22" x2="22" y2="2" gradientUnits="userSpaceOnUse">`
      + GRADIENT_STOPS.map(([at, color]) => `<stop offset="${at}" stop-color="${color}"/>`).join("")
      + "</linearGradient>"
    : "";
  const disc = defs
    + `<rect width="24" height="24" stroke="none" fill="${graded ? `url(#${symbol}-field)` : field}"/>`
    + `<g fill="#fff" stroke="none" transform="${GLYPH_TRANSFORM}">${inner}</g>`;
  const pattern = new RegExp(`<symbol id="i-${symbol}" viewBox="0 0 24 24">[\\s\\S]*?<\\/symbol>`);
  if (!pattern.test(index)) throw new Error(`缺少品牌标记 symbol：${symbol}`);
  index = index.replace(pattern, `<symbol id="i-${symbol}" viewBox="0 0 24 24">${disc}</symbol>`);
}

const healthInner = svgInner(text("node_modules", "healthicons", "public", "icons", "svg", "outline-24px", "contraceptives", "sperm.svg"));
const SPERM_VIEWBOX = "1.5 1.2 21.2 21.2";
index = index.replace(
  new RegExp(`<symbol id="i-sperm" viewBox="${SPERM_VIEWBOX}">[\\s\\S]*?<\\/symbol>`),
  `<symbol id="i-sperm" viewBox="${SPERM_VIEWBOX}">${healthInner}</symbol>`);
index = index.replace(/Lucide static [0-9.]+, ISC/, `Lucide static ${versions["lucide-static"]}, ISC`);
index = index.replace(/Health Icons sperm outline-24px, CC0\/public domain/,
  `Health Icons ${versions.healthicons} sperm outline-24px, CC0/public domain`);
index = index.replace(/Phosphor [0-9.]+ regular, MIT/,
  `Phosphor ${versions["@phosphor-icons/core"]} regular, MIT`);
index = index.replaceAll(/\/vendor\/videojs\/[0-9.]+\//g, `/vendor/videojs/${versions[videoPackage]}/`);
// `palette-line` 有两个使用者，它们说的是同一件事：设置分区的「界面」页签，和侧栏底部
// 那枚配色钮——那枚钮的弹层底部「详细设置」开的正是「界面」页。一枚字形一个意思，同一个
// 意思也只有一枚字形，所以这里不给配色钮另备一枚。
// 其余六枚同样只给设置导航：那一列整列必须同一家，Lucide 与 Remix 的笔画差一档。
// 「这台电脑」取 `macbook-line`：Remix 的笔记本电脑就叫这个名字，没有 `laptop-*`。
const remixIcons = ["palette-line", "layout-grid-line", "play-circle-line", "search-line", "rss-line", "shield-check-line",
  "macbook-line"];
const remixSprite = lfText("node_modules", "remixicon", "fonts", "remixicon.symbol.svg");
for (const name of remixIcons) {
  const pattern = new RegExp(`<symbol[^>]*id="ri-${name}"[^>]*>[\\s\\S]*?<\\/symbol>`);
  const symbol = remixSprite.match(pattern)?.[0];
  if (!symbol || !pattern.test(index)) throw new Error(`缺少 Remix symbol：${name}`);
  index = index.replace(pattern, symbol);
}
stage("web/vendor/remixicon-LICENSE.txt", lfText("node_modules", "remixicon", "License"));
stage("web/vendor/remixicon-ORIGIN.md", `# Remix Icon ${versions.remixicon}\n\n- npm 包：\`remixicon@${versions.remixicon}\`\n- npm lock integrity：\`${integrity("remixicon")}\`\n- 许可证：Remix Icon License v1.0，见 \`remixicon-LICENSE.txt\`。\n- 消费者：设置导航七枚内联 symbol，其中 \`palette-line\` 同时给侧栏底部那枚配色钮。完整候选由本地 HTML 审查。\n`);
stage("web/index.html", index);

let player = text("frontend", "src", "player", "videojs.ts");
// 播放器版本由按需加载器固定，随前端清单重算；依赖检查同时核对 HTML 与加载器。
player = player.replaceAll(/\/vendor\/videojs\/[0-9.]+\//g, `/vendor/videojs/${versions[videoPackage]}/`);
stage("frontend/src/player/videojs.ts", player);

// 灯箱的 Swiper 同理按需加载，版本钉在 React 灯箱的加载器里。改了之后要在 frontend/
// 重新 `npm run build`，`web/dist/peach-app.js` 里的版本才与源码一致。
const swiperPin = value => value.replaceAll(/\/vendor\/swiper\/[0-9.]+\//g, `/vendor/swiper/${versions.swiper}/`);
for (const path of ["frontend/src/react/photo-lightbox/photo-lightbox.ts", "frontend/test/react/photo-lightbox.test.tsx"]) {
  stage(path, swiperPin(text(...path.split("/"))));
}

let webTests = text("tests", "test_web_ui.py");
webTests = webTests.replaceAll(/\/vendor\/videojs\/[0-9.]+\//g, `/vendor/videojs/${versions[videoPackage]}/`);
stage("tests/test_web_ui.py", webTests);

let reuse = text("docs", "REUSE.md");
// 版本号在这份文档里不止出现一次，换第一处会留下一行对不上的旧版本。只认完整
// 三段版本号：正文里还有「Video.js 10 Menu」那样指某个大版本的说法，它不是固定版本。
reuse = reuse.replaceAll(/Video\.js \d+\.\d+\.\d+/g, `Video.js ${versions[videoPackage]}`);
reuse = reuse.replaceAll(/Swiper \d+\.\d+\.\d+/g, `Swiper ${versions.swiper}`);
stage("docs/REUSE.md", reuse);

stage("web/vendor/lucide-LICENSE.txt", lfText("node_modules", "lucide-static", "LICENSE"));
stage("web/vendor/lucide-ORIGIN.md",
  `# Lucide static ${versions["lucide-static"]}\n\n` +
  `- npm 包：\`lucide-static@${versions["lucide-static"]}\`\n` +
  `- npm lock integrity：\`${integrity("lucide-static")}\`\n` +
  `- 许可证：ISC；原文见 \`lucide-LICENSE.txt\`\n` +
  `- 消费者：\`web/index.html\` 内联的 ${lucideIcons.size} 个 symbol\n\n` +
  `雪碧图里另有 ${handDrawnIcons.size} 枚自绘 symbol（${[...handDrawnIcons].join("、")}）与 16 枚 player-* 前缀的播放器图标，都没有上游可对。\n` +
  "RSS 与拖动点保留填充修正，避免小圆点在全局描边样式下消失。\n");
stage("web/vendor/phosphor-LICENSE.txt", lfText("node_modules", "@phosphor-icons/core", "LICENSE"));
stage("web/vendor/phosphor-ORIGIN.md",
  `# Phosphor icons ${versions["@phosphor-icons/core"]}\n\n` +
  `- npm 包：\`@phosphor-icons/core@${versions["@phosphor-icons/core"]}\`\n` +
  `- npm lock integrity：\`${integrity("@phosphor-icons/core")}\`\n` +
  `- 许可证：MIT；原文见 \`phosphor-LICENSE.txt\`\n` +
  `- 消费者：\`web/index.html\` 内联的 ${phosphorIcons.size} 个 regular 权重 symbol，`
  + `外加 ${brandDiscs.size} 枚社媒品牌标记（${[...brandDiscs.values()].map(([icon]) => icon).join("、")}）\n\n` +
  "只在描边画法说不清那件事时才用这一套：`text-aa` 是字母表，`playlist` 是播放列表。\n" +
  "填充声明写在 symbol 上，压住全局的 `stroke:currentColor;fill:none`。\n" +
  "品牌标记另走一条路：字形留白、压在各家自己那块场色的圆盘上，缩放写在外面一层 `<g>` 上。\n");
stage("web/vendor/healthicons-LICENSE.txt", lfText("node_modules", "healthicons", "LICENSE"));
stage("web/vendor/healthicons-ORIGIN.md",
  `# Health Icons ${versions.healthicons}\n\n` +
  `- npm 包：\`healthicons@${versions.healthicons}\`\n` +
  `- npm lock integrity：\`${integrity("healthicons")}\`\n` +
  "- 许可证：npm 包为 MIT，图标由上游声明为 CC0/public domain；原文见 `healthicons-LICENSE.txt`\n" +
  "- 消费者：`web/index.html` 的 `sperm` outline-24px symbol\n");

// 雪碧图的分区检查。少了它，新画一枚 symbol 只会安静地待在 index.html 里，
// 换 Lucide 版本时不跟着刷新，也没人看得出它是自绘的还是忘了纳管。
const spriteSymbols = [...index.matchAll(/id="i-([a-z0-9-]+)"/g)].map(m => m[1]);
const owned = new Set([...lucideIcons.keys(), ...phosphorIcons.keys(),
  ...brandDiscs.keys(), ...handDrawnIcons, "sperm"]);
const orphans = spriteSymbols.filter(
  name => !name.startsWith("player-") && !owned.has(name));
if (orphans.length) {
  throw new Error(`雪碧图里这些 symbol 没有归属，加进对应图标集或 handDrawnIcons：${orphans.join("、")}`);
}

const versionRoots = [
  ["web/vendor/videojs", versions[videoPackage]],
  ["web/vendor/swiper", versions.swiper],
];
const problems = [];
for (const [path, expected] of expectedFiles) {
  const absolute = join(root, path);
  if (!existsSync(absolute) || !readFileSync(absolute).equals(expected)) problems.push(path);
}
for (const [path, version] of versionRoots) {
  const absolute = join(root, path);
  const actual = existsSync(absolute) ? readdirSync(absolute).sort() : [];
  if (actual.length !== 1 || actual[0] !== version) problems.push(`${path} 版本目录`);
}

if (checkOnly) {
  if (problems.length) {
    // 修复命令必须印在消息里。只列不同步的路径时，CI 上看到这一段的人得先翻
    // package.json 才知道重算入口叫什么，Dependabot 的 PR 尤其——那些改动不是人写的，
    // 谁都不知道漏了哪一步。
    console.error(`前端固定依赖未同步：\n${problems.map(path => `- ${path}`).join("\n")}\n\n`
      + "重算：npm ci --ignore-scripts && npm run vendor:web\n"
      + "Dependabot 的清单升级用：scripts/adopt_dependency_bump.py --pr <编号>");
    process.exit(1);
  }
  console.log(`前端固定依赖已同步：Video.js ${versions[videoPackage]}、Swiper ${versions.swiper}、Lucide ${versions["lucide-static"]}、Phosphor ${versions["@phosphor-icons/core"]}、Health Icons ${versions.healthicons}`);
  process.exit(0);
}

for (const [path] of versionRoots) rmSync(join(root, path), { recursive: true, force: true });
for (const [path, payload] of expectedFiles) {
  const absolute = join(root, path);
  mkdirSync(dirname(absolute), { recursive: true });
  writeFileSync(absolute, payload);
}
console.log(`已更新 ${expectedFiles.size} 个固定依赖文件。`);
