# amane 的海报裁剪与演员头像取图

- 取证日期：2026-09-22
- 上游：<https://github.com/sqzw-x/amane>，GPL-3.0
- 固定 revision：`79ecfa763cc786318e1964a3d7f4e244a7d5c96d`
- 取证方式：仓库外独立克隆并读取源码，
  逐文件核对前端组件、API 路由、图像函数与派生资源落盘；未运行其服务，未截图。

**本文不登记进 `docs/reference-sources.json`**：这是对上游源码的一次性只读实证，不是需要跟踪漂移的
可变 Markdown。同一 revision 已由 `amane-content-routes` 条目登记，那条跟踪的是上游的
`docs/dev/content-routes.md` 原文，与本文无关。

只借设计，不引代码。GPL-3.0 的实现一行也不进 Peach。

## 一、作品海报裁剪（poster 从 thumb 裁）

上游把「封面 thumb」和「海报 poster」当两张图：thumb 是横版整幅 DVD 封套，poster 是它右侧的竖版人像部分。
默认比例常量写在 `src/amane/media/images.py:16`，`_DEFAULT_POSTER_RATIO = 0.7`，注释给了出处：
标准 DVD 封套约 800×538，右侧海报约 379×538，`379/538 ≈ 0.704`。运行期取 `config.scraping.poster_ratio`。

### 交互形态

`web/src/components/media/poster-crop-dialog.tsx`：

- 对话框是 Mantine `Modal size="xl" centered`，标题「裁剪海报」，底部右对齐两枚按钮（取消 / 确认）。
- 底图区 `maxHeight: 55vh` 且 `overflow: auto`，图片 `maxWidth: 100%`。
- 框选用第三方组件 `react-image-crop`（`ReactCrop`，`keepSelection`），不是自研 canvas。
- **默认选区**（`initialFullHeightCrop`，第 62 行）：打满整图高度，宽度按比例算，**靠右对齐**。
  这是把「自动裁剪取右侧」的规则直接做成了手工裁剪的初值。
- **比例锁**：一个 `Switch`「锁定宽高比」+ 一个 `NumberInput` 比例数值（step 0.01，范围 0.1–5，
  `clampAspect` 夹紧）。锁上时高度输入框 `disabled`，宽度由高度推出。
- **数值输入**：left / top / width / height 四个整数 `NumberInput`，与鼠标框选双向同步——
  拖拽改数值（`syncBoxFromCrop`），改数值回写选区（`commitBox`）。单位一律是**原图像素**，不是显示像素。
- 比例改变时按新比例重塑选区，且优先右对齐腾宽度（`reshapeWithAspect` 第 281 行、
  `fitLockedBox` 第 100 行都有这条「preferRight」逻辑）。
- 图右下角浮一行 `naturalWidth×naturalHeight` 的尺寸提示，`pointerEvents: none`。
- 底图**不直接用 `<img src>`**：先 `fetch(..., {cache: "no-store"})` 拿 blob 再 `createObjectURL`。
  源码注释写明原因：裁切必须与后端 `acquire` 的同一份字节对齐，绕过 HTTP 磁盘缓存。
- 坐标换算集中在两个纯函数：`toNaturalBox`（显示像素 → 原图像素，`scale = naturalWidth / width`，
  四边各自 `Math.round` 后再夹到 `[0, natural]`）与 `fromNaturalBox`（反向）。右/下边界取开区间。

### 入口按钮

`web/src/routes/meta.$metadataId.tsx:474`：按钮在**封面图下方**、左栏底部，不在标题旁。
形态 `Button size="xs" variant="light" leftSection={<IconCrop size={14}/>}`，
外面套 `Tooltip`，无 thumb 时按钮 `disabled` 且 tooltip 说明原因（有图时 tooltip `disabled`）。

### 后端

`src/amane/api/routes/metadata.py:265`，`POST /metadata/{metadata_id}/crop-poster`，
请求体就是四个整数 `{left, top, right, bottom}`（原图像素，右/下不含），响应是更新后的 metadata。
400 的两种情形：无封面图可裁、裁切区域无效；404 是 metadata 不存在。

`src/amane/media/pipeline.py:195` `manual_crop_poster`：

1. `store.acquire(thumb_url)` 取到本地原图（远端图会先落盘）；
2. `probe_size` 读尺寸，`validate_crop_box` 校验（`images.py:27`：四边非负、不越界、`left < right`、`top < bottom`）；
3. `format_crop_box_args` 把框序列化成 `box:L,T,R,B` 字符串（`images.py:20` 的 `CROP_BOX_ARGS_PREFIX`）；
4. `store.acquire_derived(thumb_url, "crop", args, producer)` 落盘；producer 是 `crop_box`，
   `PIL.Image.open(...).crop(box).save(dest, quality=jpeg_quality)`，默认 JPEG 质量 95；
5. 可选超分，返回内部 URL，路由把它写回 `metadata.poster_urls`。

### 裁剪结果存哪、原图是否保留

`ResourceStore.acquire_derived`（`src/amane/media/resource_store.py:176`）：

- 派生物按 `derived_locator(src_url, op, args)` 生成一个合成 URL 作主键，和原图是**两条独立 Resource 记录**；
- 同一 `(src_url, op, args)` 命中已有记录且文件还在就直出，等于内容寻址的缓存——
  **同一个框重复裁不会产生第二份文件**，换了框才生成新的；
- 记录上带 `meta = {"op": "crop", "src": 原图 URL, "args": "box:L,T,R,B"}`，
  所以「这张海报是谁裁的、从哪张裁的、框是多少」是可回溯的；
- 原 thumb 一行不动。自动裁剪走 `crop_poster`（`images.py:78`），也是读 thumb 写另一个文件。

`apply_cover_watermarks`（`images.py:227`）的 docstring 明确写了「不修改 Resource 原图」：
角标叠在库路径的封面/海报副本上，资源库里那份始终是干净原图。

### 不叠加、不并存

上游没有第二套焦点或热区机制；海报只有「自动按比例取右侧」和「手工框」两条路，共用同一个
`op=crop` 的派生位，`args` 一个是 `0.7000` 一个是 `box:...`。

## 二、演员头像从作品封面裁取

**未取得**。上游在 `79ecfa76` 上没有这个功能。核对过的位置：

- `src/amane/api/routes/actors.py`：只有 `image_urls` 列表字段的读写，没有裁剪、上传或取帧端点；
- `web/src/components/media/actor-edit-dialog.tsx`：图片编辑区是一个 `SortableImageList`
  （拖拽排序 + 设为首图 + 删除）加一个「填 https 地址」的 `TextInput`，正则 `^https?:\/\//i` 校验后追加；
  没有「从作品封面里选」的来源，也没有裁剪；
- 全仓 `crop` 命中的文件里没有任何一处与 actor 相关。

也就是说，用户说的「跟 amane 一样」在**海报裁剪**上成立，在**头像从封面裁取**上没有上游先例；
那部分是 Peach 自己的设计，本文不为它编造上游依据。

## 三、Peach 可迁移的点与主动差异

可迁移：

- 手工框的坐标契约用**原图像素的 `left/top/right/bottom` 四整数**，右下开区间，后端二次校验，不传比例或显示坐标；
- 默认选区打满一边、按目标比例贴一侧，而不是居中小框；
- 裁剪产物是**独立派生文件**，原图不动，且记录里带 `op / src / args` 三元组以便回溯与幂等复用；
- 底图取字节时绕开 HTTP 缓存，保证前端量的像素和后端裁的像素是同一份；
- 无底图时按钮禁用并用 tooltip 说明原因，而不是点开后才报错。

Peach 主动保留的差异：

- 不引 `react-image-crop`（GPL 仓库的选型不构成 Peach 的依赖理由，且 Peach 有依赖清单门槛），
  框选用原生实现；
- 入口放在标题旁的按钮行，不放封面下方——Peach 的详情页左栏没有那个位置；
- 裁剪来源写成 `user:crop`，进 Peach 自己的封面证据链；上游没有来源分级这一说；
- 头像那条按 Peach 自己的实体与 artifact 模型做。
