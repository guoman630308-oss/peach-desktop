# Rule34 追更标签与跨作者合集证据

- 取证日期：2026-08-27（选择器与字段）、2026-09-08（署名里的角色标注与阈值）
- 取证通道：Python HTTPX 直接读取公开 HTML/CSS；浏览器加载详情页 45 秒超时，视觉与实时 DOM 为**未取得**。
- 用途：Rule34Video 详情补全、跨作者合集排除、关注页标签类型配色。

## Rule34Video 详情页

URL：`https://rule34video.com/video/4533145/…/`

本次 HTML 响应 200，298,419 字节，SHA-256
`CFBA7498BCC829345117226D8756F13A9535F7EDA57809202A041DDE41F662D1`。页面含动态签名，
整页哈希只标识这一次响应，不作为长期不变版本号。

可重放选择器与字段：

| 内容 | 证据 |
| --- | --- |
| 正片与封面 | `VideoObject.contentUrl`、`thumbnailUrl`、`uploadDate`、`duration` |
| 当前可播地址 | 播放器配置 `video_url`；带短期签名，不能存进公共 JSON |
| 内容标签 | `a.tag_item[href*="/tags/"]`，本页 31 个 |
| 作品分类 | `a.video_meta_pill[href*="/categories/"]`，本页 3 个 |
| 署名作者 | `a.video_meta_pill[href*="/models/"]`，本页 54 位 |

同一作者页抽取的 8 个普通作品均只有 1 位署名作者，内容标签为 3–11 个。

### 署名里的角色标注（2026-09-08）

站点把配音与音效记在同一份 `Artist` 名单里，角色写在名字末尾的括号中：
`Oolay-Tiger (VA)`、`HentAudio (Audio)`、`Huntress___ (Audio/SFX)`、
`Squish The Succubus (sound)`。当日抓取的 16 个详情页与库内 623 条记录中，
后缀出现 `va` 126 次、`audio` 54 次、`sound` 5 次、`audio/sfx` 3 次、`voice` 1 次。

判跨作者打包只数剔掉这些角色之后的画面作者，阈值 `> 3`：

| 条目 | 名单 | 画面作者 |
| --- | --- | --- |
| `Yunara Showing Ahri Some Discipline` | 5 | 1 |
| `The Rite of Loss` | 6 | 1 |
| `Max Feetfuck [Regina3D]` | 4 | 2 |
| `Fuck Track / Futa PMV` | 13 | 13 |
| `ON AND ON \| HMV / PMV` | 17 | 17 |

同一批 623 条里超过阈值的共 12 条。`Uploaded by` 不是判据：抽样 16 条无一由署名
作者本人上传，站内几乎全是搬运号。详情取不到时保留候选，不用一次网络失败删更新。
4533145 没探过详情页、拿不到署名，它单独按 id 在读取时隐藏。

## Rule34.xxx 标签类型

- 标签列表：`https://rule34.xxx/index.php?page=tags&s=list`
- 样式：`https://rule34.xxx/css/desktop.css?46`
- CSS 响应 200，25,126 字节，SHA-256
  `FC34871535E66A1AE9862C4B0EAE772B11D214845DB93F582492680B66FBE107`

站点用父级类型类区分标签，CSS 原色如下：

| 类型 | 类 | 原色 |
| --- | --- | --- |
| Artist | `.tag-type-artist` | `#A00` |
| Character | `.tag-type-character` | `#0A0` |
| Copyright | `.tag-type-copyright` | `#A0A` |
| Metadata | `.tag-type-metadata` | `#F80` |

Peach 保留类型差异，但不复制白底站点的低亮度原色；在深色界面换成可读的同色相描边和浅底。
General 沿用 Peach 蓝色。`2d`、`3d`、`animated`、`video`、`sound`、`tagme` 等载体标签仍留在
原始证据里，但不进入前 24 个内容筛选标签。

## Rule34.xxx 帖子页的标签排列顺序

- 取证日期：2026-09-01
- 取证通道：Python HTTPX 直接读取公开 HTML；`#tag-sidebar` 的 `li.tag-type-*` 按出现顺序读取。

| 帖子 | 响应 | SHA-256 | `#tag-sidebar` 类型出现顺序 | 各类型条数 |
| --- | --- | --- | --- | --- |
| `index.php?page=post&s=view&id=18622796` | 200 · 41,563 字节 | `10B3DF9247D0A146274928A50092512575E05F47FAE78B4CEA80F2B82392184C` | copyright → character → artist → general → metadata | 2 / 1 / 1 / 40 / 3 |
| `index.php?page=post&s=view&id=18622794` | 200 · 40,710 字节 | `91EE6DAF284249823EDF3E13D17082E4C3ABDA7D2EF11F2FF08915BD5BAF36A8` | copyright → character → artist → general → metadata | 2 / 1 / 1 / 38 / 2 |

同一次取证里 `id=18622795` 没有 copyright 标签，其余四类顺序不变；缺的类型直接跳过，
不占位。组内按标签名升序：`18622796` 的 general 组前 12 个是
`1girls, abs, bare_arms, bare_legs, bare_shoulders, bare_thighs, big_breasts, bikini,
bikini_bottom, bikini_top, clothed, clothing`，与排序后完全一致。

页面含动态内容，整页哈希只标识这一次响应。Peach 的关注详情按同一顺序分组排列；
来源没有记录类型的标签排在最后，保持中性色，不按词形猜类型。
