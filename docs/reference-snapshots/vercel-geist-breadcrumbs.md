# Geist Breadcrumbs 取证记录

- 取证日期：2026-09-02
- URL：<https://vercel.com/geist/breadcrumbs>
- 取证方式：实时 DOM 读取 + `getComputedStyle`（文档页内置真实组件示例；
  Geist 文档站是 React 渲染，视觉规格拿不到可哈希的快照，与
  `vercel-geist-controls-measured.md` 同一策略，不在 `docs/reference-sources.json` 登记）
- 上游版本：未取得（页面无锁定资源版本记录；复核需重新抓取）
- 附带核对：登录态后台 `vercel.com/<account>/~/settings` 的 route header
  里有同语义 `nav[aria-label="Breadcrumb"]`，但当时处于水合前的 inert 隐藏态，
  只能确认 `text-heading-14 font-medium`、`gap-0.5` 的头部形态，组件规格以
  文档页实测为准。

## 实测要点（dark 主题计算值）

- 结构：`nav[aria-label="Breadcrumb"]`（display:block）→ `ol.flex.list-none.gap-1.5`
  （项间距 6px）→ `li` 每项自带一个尾部分隔符，最后一项的分隔符
  `display:none`（`last-of-type:[&_svg]:hidden`）。
- `li`：`flex items-center gap-1.5`——文字与分隔符之间也是 6px；字号 14px
  （`text-sm`）、行高 20px、字重 400。
- 颜色：普通项 `--ds-gray-900`（dark `#a1a1a1`）；当前项加
  `aria-current="true"` 且升到 `--ds-gray-1000`（dark `#ededed`）；
  hover 也升到 gray-1000（`hover:text-[var(--ds-gray-1000)]`）。
- 分隔符：16×16 chevron SVG（`viewBox="0 0 16 16"`，`aria-hidden`），
  颜色被 `!text-[var(--ds-gray-900)]` 钉在 gray-900——当前项亮起来时
  分隔符不跟亮；`transition-colors duration-200`。
- 链接项内层 `<a>` 继承颜色、去下划线（`[&_a]:text-inherit [&_a]:no-underline`）。
- Disabled 变体：`--ds-gray-800`（dark `#8f8f8f`）+ `cursor:not-allowed`。
- 文档页未提供溢出截断/折叠行为；示例最多三级。

## Peach 采用与差异

- 语义照搬：`nav[aria-label="Breadcrumb"] > ol > li`，当前项
  `aria-current="true"`（Geist 用 `"true"` 不是 `"page"`）、最后一项分隔符隐藏、
  分隔符不随当前项提亮。
- 数值映射到 Peach token：14px/20px = `--fs-md`/20px；gray-900 → `--muted`、
  gray-1000 → `--ink`（Peach 只用 `:root` 已有 token，不复制 Geist 色值）；
  分隔符复用本地 Lucide `chevron-right` 16px 描边图标，不引 Geist 私有 SVG。
- Peach 只在「有上一级页面的子页」渲染 breadcrumb：数据管理 hub 的五张卡
  对应的子页（垃圾文件、重复文件、人工复核、回收站、高清版）以
  「数据管理」为父级；hub 本身与统计/口味/关注不再画面包屑。
- 垃圾文件/重复文件此前页面 h2 顶着「数据管理」，引入 breadcrumb 后
  h2 改用页面自己的名字（与 `pageTitle` 的 `document.title` 对齐），
  「数据管理」只出现在面包屑上一级。
