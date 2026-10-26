# Geist 设置区块取证

2026-09-06。实时浏览器访问 Fieldset 返回 `js execution timed out`；改用 HTTPX 读取官方 HTML/CSS。浏览器实时动画未取得，动画参数复用已登记的 `vercel-geist-semantics-measured.md`。本文件是人工测量笔记，不在 `docs/reference-sources.json` 中登记为上游快照。

| 来源 | HTML SHA-256 |
| --- | --- |
| https://vercel.com/geist/fieldset | 6a5bde91b9d131014d4837763227457711eee2575e68994a1d9c51a44948aa0c |
| https://vercel.com/geist/select | 921c1730ceea0220ab6507e93c636d5821bcc56bfbc97633cc4b40d8c842d52a |
| https://vercel.com/geist/collapse | ac9f84000a4a71bfabe70ac493e8f67bfc86e2b3e13cebf2daf401a59bf5b638 |

共同 CSS：`https://vercel.com/vc-ap-b3331f/_next/static/immutable/chunks/0ggp-66pwlt2m.css`，SHA-256 `ec205bee3ef6e6f0920703d79055cd9251990ecb23e12515a9ed041068245a28`。

- Error Fieldset 根带 `data-fieldset-type="error"`、红色 400 边框；正文使用背景 100；footer 使用红色 100 底、红色 400 顶边、红色 900 文字。危险按钮为实底，整卡并非全铺深红。
- Select 标签在上方，`mb-2` 对应 8px；原生选择器 `w-full` 跟随容器，文字截断、右侧留箭头位置。规范没有要求统一的固定像素宽度。
- Collapse 文档明确要求开合过渡。Peach 复用 `wireCollapse` 的高度与箭头 200ms `ease-in-out`、`aria-expanded`、`aria-controls` 和关闭时 `inert`。
- [Vercel General settings](https://vercel.com/docs/project-configuration/general-settings) 将通用设置作为独立类别。Peach 使用通用、媒体、网络与访问、更新与维护四组标题，组内使用 Fieldset，卸载区放在末尾。

## Peach 采用与差异

配置顺序为自启、媒体与挂载、网络代理、访问控制、检查更新、运行信息、卸载。代理容器选用 `min(320px,100%)`，这是 Peach 布局决定；菜单固定与触发器等宽并复用已有键盘及视口定位。代理地址标签使用既有 8px 间距。卸载区与确认按钮使用项目危险色；目录明细复用已有 Collapse，不使用原生瞬间展开。

## 设置控件与风险审查

2026-09-06 通过 HTTPX 读取官方页面。HTML SHA-256：

- [Checkbox](https://vercel.com/geist/checkbox)：`f72fd80f1e22766a824d1c42b5accbd42da73b144584b670074c700ee6ddee55`。
- [Toggle](https://vercel.com/geist/toggle)：`87efc6d9ccc986f5c3e4d3140e8cd9105d274a3a7c04f9bb870da694cbf737f4`。
- [Note](https://vercel.com/geist/note)：`f3471155164c8e0085419ce04e15928463cb526b4a52b51483c214fd84b645dc`。

自启与静默启动复用共用 Toggle（36×20），保留显式「保存配置」。确认删除数据、关闭密码和附带扫描使用 Checkbox；框体 16px，几何依据 `vercel-geist-semantics-measured.md`。Peach 选项间距 16px、说明间距 8px，文字随窄屏换行。

| 操作 | 风险呈现 | 依据 |
| --- | --- | --- |
| 卸载、删除空文件夹、删除已失效链接 | 红色区域及按钮 | 删除程序、目录或记录，应用内无完整恢复入口 |
| 永久删除、清空回收站 | 保持已有红色按钮 | 永久删除媒体与账本记录 |
| 网盘差异同步、清理孤立缓存 | 黄色区域、后果 Note 与警告按钮 | 缺失条目进入回收站；缓存可重建 |
| 关闭访问密码 | 选中后黄色区域与访问范围 Note | 改变访问条件，保存前说明后果 |
| 扫描、检查、挂载、浏览、恢复、选择媒体路径 | 中性 | 读取、导航、恢复或可撤销配置 |
| 垃圾与重复文件移入回收站 | 保持可恢复操作及确认 | 不等同永久删除 |
| 删除播放列表、取消关注、清除来源凭据 | 保持既有危险按钮和作用范围确认 | 删除所选集合、订阅或凭据，不按存储来源着色 |

危险区域共用 `data-fieldset-type` 选择器，置于基础 Fieldset 规则之后；浏览器验收须检查实际底栏颜色，源代码包含红色声明不能证明层叠结果。

警告按钮复用 Geist Warning 的琥珀底、深色字（`vercel-geist-semantics-measured.md`）；底栏说明左对齐、动作靠右。风险预览须加载正式 SVG 图标集，Note 图标引用必须能解析。失效链接重验的结果在链接管理原位置显示，保留计数包含恢复和暂时无法确认失效的链接。

2026-09-06 复核 Button 当前源码：HTML SHA-256 `3a4f9729c54e104507b032eaa14ef94cfb757b05c7420749f66811c6bc41d409`。CSS `328y7_b581oob.css` SHA-256 `3034e6739ae0e19814df6e53ba7febefe56cf986ed0f1c3534df9aee1f751b87`，`.geist-new-warning-fill` 明确为 `--themed-fg:#0a0a0a`，黄色按钮保留深色字。Peach 分组标题采用已有 24px 档、Fieldset 标题 20px 档；危险底栏说明使用危险色，正文仍保持正常阅读色。

## 管理页面间距审查

更新可用提示复用中性 Note、信息图标与“有可用更新”标签；普通更新不是错误或有风险的后果。2026-09-06 复核 `https://vercel.com/geist/note` 的 Best Practices：Note 用于局部持续信息，secondary 用于中性信息；Status Dot 文档限定部署状态。Note HTML 版本沿用本页登记的 `f3471155…`。检查为最新后恢复普通状态文字，更新按钮仍放在原操作栏，不重复增加 CTA。

站内文字链接参考本页 Fieldset 的 `View documentation` 示例：`text-[var(--geist-link-color)]`、`hover:underline`、`focus-visible:outline-2` 与 `outline-offset-4`。Peach 使用主题链接色、悬停下划线及 2px 焦点轮廓，访问后颜色保持一致；“配置 Peach 代理”跳转 `/configuration#peachProxy`，不附外链箭头。独立 `/geist/link` 页面未取得，证据来自官方 Fieldset 示例及本页已登记 HTML。

Input 的 HTML SHA-256 为 `df3a8b7ba933798842915d06056ef079878faa091af6b8d67e61d69cc0a08962`，Description 为 `7a16d502b3fdba316ef740057b275d2acdeb85f79d4944201331af906d9b497f`；Fieldset 与共同 CSS 的 SHA-256 与首表一致。浏览器参考页返回 `js execution timed out`，实时参考布局未取得。

Fieldset 正文 `p-5` 为 20px；副标题 `pt-2` 为 8px，`pb-5` 为 20px；副标题是最后一个孩子时正文取消底内边距，避免两份留白相加。Footer 使用 `pt-3 pr-3 pb-3 pl-5`，即上、右、下 12px，左 20px。Label 使用 8px 的 `mb-2`。这些是具体组件值，不是所有元素一律相隔 8px 的规定。

按重复原因计为 8 类，动态列表每条记录不重复计数：

| 类别 | 适用位置 | Peach 使用 |
| --- | --- | --- |
| 标题与说明 | 配置代理、访问密码、卸载；数据管理卡片 | 同组 8px；标题和说明共用容器 |
| 说明与错误 | 配置表单、更新状态 | 字段下方 8px；作为网格独立项时不另加外边距 |
| 字段标签 | 媒体目录、来源选择、关注凭据、播放列表创建 | 标签到输入 8px；凭据字段组间 16px |
| 页标题与说明 | 管理页页头、播放列表 | 有说明时 8px；没有说明的页头保留 20px |
| 独立面板之间 | 配置、采集来源、数据管理、关注管理 | 20px；嵌套紧凑列表不使用这个值 |
| 底部操作区 | 配置、采集、清理、资源同步、链接管理 | 上右下 12px、左 20px；动作之间 8px |
| 行内反馈 | 关注添加、凭据操作 | 反馈距所属控件 8px；提交操作由字段组的 16px 间距承接 |
| 来源标题与网址 | 采集来源 | 使用 8px 标题组，不使用负外边距补偿 |

Peach 的表单组、卡片和页头使用上述一致性约定；不将它们宣称为 Vercel 对所有管理页面的统一强制值。复核候选、重复文件、高清版、统计和回收站的表格、媒体网格、数值及紧凑元数据保留各自密度；这些与表单帮助文字不是同一场景。此轮没有数据层、API、凭据或生产入口变更。

## 设置面板分组与布尔开关（2026-09-06）

当前官方 HTML：Toggle https://vercel.com/geist/toggle ，SHA-256 `87efc6d9ccc986f5c3e4d3140e8cd9105d274a3a7c04f9bb870da694cbf737f4`；Separator https://vercel.com/geist/separator ，SHA-256 `45ee368f0b857483782ce983dc0f9eebd521b850ea18d220956d5d0dbf8205b0`。通过 HTTPX 严格 TLS 获取。Toggle 表示立即生效的布尔设置；Separator 区分独立内容，官方没有要求所有行都加线。

Peach 按界面、浏览、播放、搜索、关注、安全排列。主题和导航属于界面；JAV 封面和合并分卷属于浏览；悬停放大属于播放。排序与方向共用一行，随机排序禁用方向。独立设置之间加线，同一设置的关联控件之间不加线；JAV 封面类型与大小作为关联项，中间不加线。卡片之间使用间距。行位置按相邻关系判断，不按 div / label 类型判断。

排序选项在入口和菜单使用纯文字，方向箭头仅显示在入口，方向菜单保留文字与选中标记。预览复用正式导航排序、隐藏与添加函数，持久化使用独立预览数据。

Toggle 复用已测量的中号 36×20、17px 圆点；圆点用白色，开态保留 Peach 主题蓝，是与上游明确的色彩差异。合并分卷、详情自动播放、安全模式使用 switch 语义。悬停放大的 0 值表示关闭，保存在浏览器设置，普通预览保持原行为。

影响面：页面及本地设置契约、共享卡片悬停、源测试与计时行为测试适用；数据层和 API 无变更。首页及实体列表复用 wireHover，其他未使用该函数的页面不受影响。没有真实账本写入、生产替换、发布或智能体规则变更。
