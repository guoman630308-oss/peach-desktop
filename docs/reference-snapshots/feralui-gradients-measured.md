# feralui.dev/gradients 渐变生成器实测记录

- 取证日期：2026-09-16
- URL：<https://feralui.dev/gradients>
- 取证方式：打开生成器，读预览节点的实际渲染方式、bundle 体积与两种导出（CSS、JSON）的产物。

## 不登记进 `docs/reference-sources.json` 的理由

页面是闭源 React SPA，内容由 `assets/index-BalH5ccj.js`（约 1.06 MB，未见许可）在运行时
生成，取不到可哈希、可重抓的上游字节；渐变本体又是 Canvas 逐帧绘制的像素，没有可比对的
声明式源。按本目录的约定，这类实测留在快照正文说明，不进登记表。

## 实测

实时行为取证——渐变本体由 2D Canvas 逐帧绘制（Flow 预览画布 240×160 拉伸、Glow 型
1024×1280），上叠 PNG 噪点瓦片 `mix-blend-mode: overlay`；bundle `assets/index-BalH5ccj.js`
（约 1.06 MB，闭源，未见许可）；CSS 导出只有一条 `linear-gradient(135deg in oklab, …)`，
自带注释承认 CSS 画不出 mesh 场；JSON 导出的参数模型为 `type` / `stops[]`（hex+oklch+
position）/ `dividers` / `soften` / `noise` / `speed`。

## 预设区那十二枚 chip

2026-09-16 用 agent-browser 打开同一页，读 `.jg-chiprow` 里每个 `.jg-chip` 的文字和它那枚
圆点的 `conic-gradient`——圆点按色标数等分，四段的颜色就是这一档的四枚色标，顺序与它
JSON 导出里的 `stops[]` 一致。共十二档，每档四枚。

| feralui 档名 | 色标 1 | 色标 2 | 色标 3 | 色标 4 |
| --- | --- | --- | --- | --- |
| Iridescent cloud | `#eaf4fc` | `#1e50a2` | `#f09199` | `#895b8a` |
| Opal | `#f6f9ff` | `#9be0e8` | `#c4b5f7` | `#f8b8d9` |
| Lagoon | `#eafbf7` | `#5ce3e6` | `#0f9cc2` | `#274a78` |
| Emerald | `#f0fbef` | `#8fe3b0` | `#22c79a` | `#0b5f51` |
| Solar flare | `#fff6de` | `#ffc24b` | `#f4664d` | `#8a2e5e` |
| Orchid | `#fbeffb` | `#e794c9` | `#9678ce` | `#4a3894` |
| Peach glow | `#fff3ec` | `#ffc9a3` | `#f08a8c` | `#b65e8c` |
| Electric tide | `#dffbff` | `#6fd8f2` | `#4c5be0` | `#2a2450` |
| Sunset | `#ffe9c4` | `#ffae3f` | `#f0574d` | `#5d2a66` |
| Mint ice | `#f2fdf9` | `#a8f0dc` | `#52cbb0` | `#147a5f` |
| Midnight bloom | `#12142e` | `#4c3894` | `#b387e8` | `#f6c6e2` |
| Rose gold | `#fff4e8` | `#fbc9ac` | `#e79ba7` | `#8e5a74` |

色标各自的位置、`soften`、`noise` 与 `speed` 未取得：圆点只按色标数等分，读不出各档自己
的位置值，而这几项在 Peach 里本来就交给用户那几条拉条。

### Peach 的取法

`web/js/home-glow.js` 按这张表加了对应的十二档，每档取色标 2、3、4。第一枚不取：十一档
是铺满画布的近白底色、Midnight bloom 那档是近黑底色，在它的 mesh 模型里那是画布本身，
而 Peach 没有底色层——三枚光晕各自向 `transparent` 收边，身后就是页面自己的面，把那一枚
搬过来在深色玻璃上等于没有、在浅色玻璃上是一团脏。不透明度不跟着走，统一用 Peach 默认
档那一组（62 / 52 / 52），那是这块玻璃自己的浓淡。档名中文自拟，按颜色取。

## Peach 借了什么

只借三点，都不涉及它的代码或位图：

1. **参数模型**：一档配色 = 若干色标（hex 加位置）+ 柔化 + 噪点。Peach 的 `homeGlow` 落成
   三枚光斑（颜色、不透明度、圆心、椭圆半轴、收边位置），开关、强度、噪点单列，
   见 `web/app.js` 的 `HOME_GLOW_PRESETS`。
2. **`in oklab` 插值**：`web/css/01-base.css` 的 `.glowlayer::before` 三层渐变都走它，由
   `--glow-lerp` 一个变量给，`@supports` 判不出时退回 sRGB。
3. **噪点叠层**：`.glowlayer::after` 一层 `mix-blend-mode: overlay` 的瓦片，强度 0 即关。瓦片由
   SVG `feTurbulence` 的 data URI 当场生成，不引入它那张 PNG，也不往仓库里落位图。

渲染保持纯 CSS 径向光晕，不引入 Canvas：首页顶栏与筛选条都挂着 `backdrop-filter`，
一块逐帧重绘的画布压在它们下面会让这两处每一帧都重算。

## 与本快照的差异

Peach 最终只保留光斑层：它那种铺满画布的底色场和压在上面的遮罩，Peach 都不画，三枚光斑
各自向 `transparent` 收边，底下就是页面自己的 `--page`。
