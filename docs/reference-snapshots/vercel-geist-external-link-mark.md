# Geist 外链标实测

- 取证日期：2026-09-06
- URL：<https://vercel.com/geist/colors>
- 取证方式：内置浏览器打开该页，对页内 `Learn More` 链接读 `getComputedStyle` 与
  `getBoundingClientRect`，并遍历全页 `a > svg` 统计同族图标的尺寸与色值。
- **不在 `docs/reference-sources.json` 里登记**：这是 React 渲染的规格页，正文随发布变化、
  没有可锁定的文件哈希。要复核就按上面的 URL 重抓一次。同 `vercel-geist-button-icons.md`。

## 量到的值

`Learn More` 是页脚那条离站链接（`href="https://vercel.com/"`），标在文字后面。

| 项 | 实测值 |
| --- | --- |
| 图标盒 | 16×16 px |
| 图标 viewBox | `0 0 16 16` |
| 图标绘制方式 | 单条 `path`，`fill="currentColor"`、`fill-rule="evenodd"`，**无描边** |
| 图标色 | `rgb(0, 104, 214)`，等于链接自己的 `color`（即 currentColor） |
| 文字 | 14px / 20px / 400 |
| 文字与图标间距 | 2 px（文字右边缘到图标左边缘实测 2.0） |
| 垂直关系 | 图标中心与文字中心相差 0.5 px，即与文字行居中对齐 |
| 链接容器 | `display:flex; align-items:center; gap:2px; text-decoration:none` |

同页其它同族图标（面包屑箭头、上一页／下一页、章节锚点）一律 `viewBox="0 0 16 16"`
加 `fill="currentColor"`，尺寸随所在位置 12～20 px 变化。也就是 Geist 图标是**实心字形**，
不是描边字形；尺寸不是全站一个常数，而是相对所在文字抬一档（16 / 14 ≈ 1.15）。

## 字形本身

字形是「一个缺了右上角的方框 + 一支从框心指向右上、带角括号箭头头」的构造。Peach 现有的
`#i-external-link` 是同一种构造（Lucide 一族的描边版），所以**没有把 Vercel 的 path 数据
复制进仓库**：那是他们的资产，本仓库的依赖策略也要求登记归属。改的是可测量的呈现——尺寸、
粗细、间距、对齐、取色，字形沿用仓库里已有的那一枚。

描边粗细按等效视觉重量换算：Geist 的实心笔画约 1.5 / 16 单位；Peach 的 symbol 是 24 视框，
`stroke-width:2.25` 在 16 px 显示尺寸下正好落回 1.5 px。

## Peach 对应

改前八处各调一档，并排看就是同一枚标大小不一：

| 位置 | 改前尺寸 | 改前间距 | 改前对齐 |
| --- | --- | --- | --- |
| 死链表地址 | 13 px | 6 px | flex 居中 |
| 来源行站名 | 13 px | 5 px | flex 居中 |
| 凭据「去取」 | 13 px | 4 px | flex 居中 |
| 搜索直达 | 12 px | 5 px | flex 居中 |
| 关注资源链 | 未设 | 7 px | flex 居中 |
| 来源页链（纯图标） | 16 px | — | flex 居中 |
| 刮削登录页 | 14 px | 6 px | flex 居中 |
| 设置页说明、历史导入指南 | 14 px | 4 px | `vertical-align:-2px` |

改后全站两条规则：`.externallink` 负责「链接自己是一条 inline-flex、gap 2px」，
`.externalmark` 负责「1.15em 见方、跟文字居中、吃 currentColor、粗细 2.25」。

Peach 主动保留的差异：字形是描边版而不是实心版（理由见上）；`.followorigin` 那一处是纯图标
链接，没有文字可跟，只吃尺寸和取色这两条。
