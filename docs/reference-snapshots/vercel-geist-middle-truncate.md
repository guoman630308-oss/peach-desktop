# Vercel Geist MiddleTruncate 证据

- 取证日期：2026-08-30
- URL：<https://vercel.com/geist/middle-truncate>
- 取证状态：官方页面的搜索索引正文已取得；实时浏览器 DOM、CSS、JS 与截图未取得。
- 未取得原因：内置浏览器加载超时（`js execution timed out; kernel reset`）；
  PowerShell 直连返回 `Authentication failed, see inner exception`；`curl.exe` 返回
  `SEC_E_NO_CREDENTIALS (0x8009030e)`。这些错误只说明本机取证通道失败，不表示页面下线。
- **不登记进 `docs/reference-sources.json`**：与 `vercel-geist-controls-measured.md` 同一策略，
  Geist 文档站是 React 渲染，规格拿不到可哈希的上游快照；要复核就按上面的方式重抓一次。

## 官方页面可确认的行为

- 只用于文件路径、URL、部署 ID、提交 SHA、带前缀分支名等首尾都携带信息的值。
- 说明、段落和标题继续使用末尾省略；句子从中间切开会破坏语义。
- 输出一个 `…`，不使用三个句点。
- 监听容器宽度并重算；交互过程中应锁定容器宽度，避免截断点抖动。
- 复制可见截断值时取得完整原文。
- 完整值通过包装元素的无障碍名称提供；焦点控件必须有明确 `aria-label`。
- 不把组件再包进 `text-overflow:ellipsis`，两套省略策略会冲突。
- 可能需要完整值的表面应提供完整值提示或复制入口。

## 外部候选与复用判断

| 候选 | 状态 | 判断 |
| --- | --- | --- |
| Vercel Geist `MiddleTruncate` | 官方行为页当前可查；公开可安装源码未取得 | 复用行为契约，不复制未取得的内部代码 |
| `truncate-middle==2.0.1` | MIT、零依赖、约 1 年前发布 | 只按固定字符数切割，不读取容器宽度，也不处理复制与无障碍；不采用 |
| `@telegraph/truncate` | 支持测量式 middle mode | React 组件；Peach 明确保持无构建原生前端，不为一个文本原语引入 React；不采用 |
| `@web-components/middle-truncate` | Web Component，项目自述为 work in progress | 稳定性不足；不采用 |

## Peach 采用的差异

Peach 使用原生 `ResizeObserver`、`Intl.Segmenter` 与 Canvas 文本测量实现一个平铺 ES module，
不新增运行时依赖。首批消费者是重复文件名、重复文件路径、高清版目标文件名和照片详情文件名。
媒体标题、创作者名、说明文字等语义文本继续使用末尾省略或多行截断。

完整值保存在模块状态中；可见文本只显示首尾与单个 `…`，`aria-label`、原生 `title` 和复制事件
仍提供完整值。容器宽度、字体加载或动态文本变化后重新计算。

## 项目门槛

- 资源标识一律显式加 `data-middle-truncate`；当前页面源测试锁定全部调用点。
- CSS `text-overflow:ellipsis` 与多行 line clamp 只留给标题、说明、人名、标签和状态值。
- `test_every_end_truncation_selector_is_explicitly_reviewed` 登记当前全部末尾省略选择器；新增、删除或改名都会失败，修改者必须先判断其语义，再同步门槛。
- `*[data-middle-truncate]` 在样式表末尾强制 `text-overflow:clip`，即使资源标识落入旧的末尾省略容器，也不会叠加两种省略算法。
