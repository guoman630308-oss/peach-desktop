# BoardUI 复核布局取证

取证日期：2026-09-13。参考：https://www.boardui.com/components/breadcrumb 。

本记录不登记进 `docs/reference-sources.json`：它是人工取证笔记，不是可由上游资源覆盖的原文快照。
截图文件名为 codex-clipboard-2f2b0fd0-e7ee-4751-a497-1617d6787e1a.png，SHA-256 为
`154463DB5E13D6A533E61F2E97132B171F6E9C40D9E88AE6A7F28D8E27B8EEB9`。

## Peach 采用与差异

按截图可见边界比较：全局导航在左，主内容在中间，页内导航在右；主内容顶部控件止于右栏之前。
Peach 采用正文中间内容与右侧分类的分区关系，保留自己的管理 tabs、复核操作和玻璃材质。
管理 tabs 横跨整个管理区域；右侧分类仅占正文列。用户于 2026-09-13 纠正了此前照参考页缩窄 tabs 的判断，此处层级不同。
右栏宽 184px、栏间距 24px 属于 Peach 现有尺寸，不声称是上游测量值；900px 以下使用单列分类与内容。

## 次级分类组件

2026-09-29 通过 Chrome 对照 [Tabs](https://www.boardui.com/components/tabs) 和 [Segmented Control](https://www.boardui.com/components/segmented-control)，直接请求取得 [Tabs 注册表](https://www.boardui.com/r/tabs.json) 中的 `pill-tab.tsx`。取证目录为 `<私有验证记录>`，保存文件 `board-tabs.json` 的 SHA-256 为 `DC94F6AE9FCA453BD8420E320A3D6087A9E0ED004B91CD811EB2BC28704A8FDC`。

`gray` PillTab 的列表透明，间隔 4px；当前项使用 `background-tertiary-default` 和主文字色，未选中项用次级文字色，悬停底色为 `background-primary-hover`。条目为 10px 圆角、14px/20px 的 Medium 字体，上下内边距 5px、左右 8px。Segmented Control 使用整组轨道与滑块，官网示例是周／月／年这种短的局部选择。

Peach 的复核页和配置页采用灰色 Pills 的表面和文字层级。复核页使用右侧竖排分类，保留 184px 列宽、24px 栏间距和复核数量；桌面条目保留 40px 命中高度，900px 以下在正文上方单行滚动。配置页与关注管理在正文上方横排，窄屏单行滚动。管理 tabs 横跨页面，配置页 skeleton 与正文共用分类样式。Peach 需要内容面板关联和方向键操作，因此保留 React Aria Tabs／现有 tablist 语义，不直接替换为上游普通按钮组，也不增加滑块动画。

纠正：Breadcrumb 参考只确定右栏布局，不能据此把复核分类改成正文锚点目录。用户要求在 Segmented Control 和 Pills Tabs 之间比较；九个带数量的复核分类与配置分区采用灰色 Pills。亮色容器透明本身不是缺样式，应保证两种主题下当前项的独立底色清晰。

关注管理的「关注列表／添加关注／JAV 订阅源／来源和凭证」也采用同一套灰色 Pills，保持横排与内容面板关联，窄屏单行滚动；等待态复用同一导航表面。统计维度、采集模式、隧道模式属于局部互斥选择，继续使用分段控件。

主题阴影采用仓库内 BoardUI `theme.css` 的亮暗原值：卡片暗色接触影为 `0 1px 1px / 14%`。凭据按「可配置凭据／不需要凭据／暂不支持」分组，各组与查找结果使用独立描边表面，两种主题都通过边框区分层级。JAV 订阅列表复用关注列表的外层卡片、顶部操作栏、底部分页和状态栏；默认每页 20 条，选择跨页保留。2026-09-29 的亮暗截图及调用范围记录在 `<私有验证记录>`。

## 玻璃材质回归证据

`23fb53d6` 为复核横条加入 `background-image:none`，清掉静止态的完整玻璃渐变。
完整玻璃伪元素的启用条件仍是吸顶，造成静止与吸顶两套材质。相接横条各自的外阴影形成叠影。
复核工具条与相接分组条统一由一层材质绘制，几何边界随实际位置更新；独立分组和 skeleton 采用共享玻璃规则。
Skeleton 工具条与卡片之间保留 20px，自行绘制玻璃，不参与固定玻璃的滚动测量。
数据、API 与生产进程不在本次样式修改范围内。
