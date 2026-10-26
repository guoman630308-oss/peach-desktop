# Geist Button 文档与前置图标判据

- 取证日期：2026-09-04
- URL：<https://vercel.com/geist/button>、<https://vercel.com/geist/icons>、
  <https://registry.npmjs.org/-/v1/search?text=geist+icons>
- 取证方式：抓取页面正文转 Markdown 后阅读正文，不是渲染量取。像素值不在这份文档里，
  见 `vercel-geist-controls-measured.md` 的 `getComputedStyle` 实测。
- **不在 `docs/reference-sources.json` 里登记**：那张表的契约是「快照文件 + 可哈希的上游
  快照」，而这是 React 渲染的规格页，正文随发布变化、没有可锁定的文件哈希。要复核就按上面
  的 URL 重抓一次。

## 文档写了什么

| 项 | 值 |
| --- | --- |
| `size` | `tiny`／默认(medium)／`small`／`large` |
| `variant` | `default`／`secondary`／`tertiary`／`error`／`warning` |
| `shape` | 默认／`square`／`circle`／`rounded` |
| 图标键 | `svgOnly` 加 `aria-label`；名称要点出动作和对象（例 `Copy deployment URL`），不描述图标形状 |
| 带文字的图标 | 走 `prefix`／`suffix` 插槽 |
| 其他 | `loading`（异步等待时替代 `disabled`）、`disabled`、`shadow`、`typeName="submit"`；导航用 `ButtonLink`，改色用 `CustomButton` |

## 文档没写什么

- **各档的像素高度**。`size` 只给档位名。数值只有实测一条来源：`vercel-geist-controls-measured.md`
  里 `vercel.com/geist/input` 的输入框与按钮同为 32 px。任何「Geist 的控件尺是 24/32/40/48」
  都是二手转述，不作证据。
- **什么时候该给按钮加前置图标**。文档只规定无障碍名称和插槽，不规定该不该加。

## 官方图标的分发

- Vercel 在 npm 上只发布字体包 `geist`（2026-09-04 查 registry 为 v1.7.2）。`geist-icons`
  v1.3.0、`vercel-geist-icons` v1.2.2 都是第三方，包描述里自称 unofficial。
- `vercel.com/geist/icons` 只说图标随站点资源发布，没有下载入口，也没有可抓的原始 SVG URL。
- 结论：官方 Geist 图标 SVG **没有不跳转的取得入口**。要用只能从 Figma 社区文件导出，或取
  第三方镜像并按 `docs/REUSE.md` 的依赖策略登记归属与许可。

## 项目内的前置图标判据

以下是从本仓库现有用法归纳出来的，不是 Geist 规定——Geist 只管无障碍名称和插槽。

**加图标**

- 图标键（只有图标、没有文字）：必须给 `aria-label`，名称点出动作和对象。
- 图标指的是**对象**而不是重复动词：来源站点标、下拉里当前选中的那个页面、平台标。
- 图标表示**形态或方向**：下拉触发器右侧的 `chevron-down`、展开与收起。

**不加图标**

- 文字已经把动词说完，图标只是把它再画一遍：`+ 添加`、`↻ 刷新`、`✓ 保存` 都属于这类。
  同一行里主次动作的视觉重量会被这枚多余字形拉平，主动作就不再是主动作。
