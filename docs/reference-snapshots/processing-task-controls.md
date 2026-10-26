# 扫描任务控件取证

2026-10-18 通过隔离 `agent-browser` 会话检查公开演示。本文件为人工取证笔记；渲染界面与用户截图没有可重抓的上游 Markdown 快照，不登记到 `docs/reference-sources.json`。

## 公开参考

- [Agent Progress](https://www.boardui.com/components/agent-progress)：实测外框宽 341px，`rounded-2xl` 对应 16px 圆角，1px `border-button-default` 边框，`background-primary-default` 表面，`shadow-xs` 对应浅色 `0 1px 2px / 5%` 接触阴影。可展开与收起，阶段行高 32px、行间距 6px，标题行高 20px。官方示例按 `stepDuration` 推进。
- [Composer Panel](https://www.boardui.com/components/composer-panel)：实测模型菜单内的 Effort 浮层有六个停点，轨道宽 240px、高 27px；拖柄宽 21px、高 27px，左右预留各 10.5px。最高档在轨道内用两层 canvas 绘制流光，装饰不接收指针，滑块由 React Aria 驱动。
- 用户截图确认最高档保留流光，三档文案上方为最慢／最快，下方为占用少／占用多。用户明确要求参考公开代码和 CSS 自行实现组件。

公开演示截图存于本机 `<私有验证记录>`：

| 文件 | SHA-256 |
| --- | --- |
| `reference-effort.png` | `20551f789bb235a3ec1da5c17e8616d9a189b5b6750003dee29349d789ca0d66` |
| `reference-agent-progress.png` | `179620ca3e3fedb40c2cc5d1899e79a46fd15d10871d45f337cacfc8db2bb288` |

## Peach 适配

保留折叠后可找回进度的交互；常驻任务面位于右下角。普通 Toast 和任务卡共用 Agent Progress 外框：通知栈内的浮层半径引用既有 `radius-2xl`，边框沿用 `border-button-default`，阴影沿用 `elevation-xs`；内容宽度、字号、内边距与控制排版各自保留。未知总量不显示百分比；圆环表达当前阶段完成率，正文来自实际任务阶段和文件名。后端逐资产执行资料、封面和预览，页面不按演示计时器自动完成阶段。

Effort 是重建方案，不是官方 Pro 源码副本。轨道与拖柄采用实测几何，圆角使用站点 token，六档调整为低占用、标准、全速三档；键盘和拖动沿用 React Aria Slider，最高档用 SVG 流光和 CSS 粒子替代 canvas。流光形状与波动是根据用户截图重建，未取得上游绘制算法，不能称为逐帧复刻。所有流光层读取站点强调色色阶，默认蓝色可随配色切换。

任务面复用 Sonner 的持久通知，普通回执在上方独立堆叠；收起保留暂停、速度与预计时间，运行及暂停时不能自动或滑动关闭。完成后撤下任务卡，由既有去重逻辑发送一条回执；失败保留查看入口并允许关闭。

任务栈启用 Sonner `expand`，高度随当前内容计算；详情开合由卡片管理。实测 Sonner 2.0.8 在子组件自行收起后仍保留旧 `initial-height`，悬停进出会导致卡片底边偏移；任务栈使用自动高度避免该偏移。展开详情列出服务端 `steps`，完成步骤使用勾选与删除线；待处理与当前步骤保留原文，阶段状态在实际处理边界发布。

失败任务卡与扫描错误结果内的日志分隔线使用 `border-error-default / 20%`；日志地址保留次级文字色，分隔线不随灰色文字变色。正常任务卡使用中性分隔线，警告任务卡跟随警告色。

任务浮层以不透明 `background-primary-default` 为底，错误／警告 token 通过背景图层叠色。深色状态 token 含 60% 透明度，不能直接当作悬浮卡片唯一背景；普通页面中的状态提示继续使用既有语义背景。

展开／收起使用中性 `secondary` 图标按钮，详情入口使用 `secondary` 链接；深浅色均通过语义 token 适配，不给辅助操作填强调色。

扫描错误结果四周内边距为 12px，正文与分隔线、分隔线与清单标题、清单标题与首条问题、末条问题与日志分隔线均留 12px；条目在内部线两侧各留 8px，文件名与错误说明间留 4px。Note 与 Disclosure 开放样式组合参数，仅该结果面板覆盖默认间距，避免父级 gap 与子级 margin 累加。
