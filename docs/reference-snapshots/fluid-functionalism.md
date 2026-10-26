# Fluid Functionalism 交互参考

2026-09-29 读取 <https://www.fluidfunctionalism.com/docs> 与公开 Registry。本文是人工取证笔记，不登记为可变上游快照；原文在 `upstream/fluid-springs.json`、`upstream/fluid-tabs.json`，URL 与 SHA-256 由 `docs/reference-sources.json` 锁定。浏览器控制返回 `nodeRepl.fetch request failed`，参考站实时手感未取得，不作为视觉复刻证据。

## 采用的行为

- 点击后立即更新选中值，只让独立底板移动；不移动文字，不等待动画再取数据。
- 短程参数来自 `springs`：fast 为 `type: spring, duration: 0.08, bounce: 0`；moderate 为 `duration: 0.16, bounce: 0`。退出参数分别为 0.06、0.12 秒。
- `tabs` 的选中底板常驻，悬停面与选中面分开。Peach 的普通分段控件只采用选中底板，未选中项仍只提亮文字。
- 快速改变目标时读取当前可见位置，从该位置过渡；尺寸只在目标改变时设置，逐帧仅改 transform。

## Peach 的实现与差异

`frontend/src/react/components/use-moving-surface.ts` 为现有 React Aria 集合添加无语义的装饰面，使用已固定的 Motion 12.43.0（MIT），不复制 Registry 组件源码、不增加 Radix 或 framer-motion 依赖。现有玻璃筛选条仍使用 `useViewGlide`，上游 BoardUI 副本不修改。

装饰面使用 [Motion mini](https://motion.dev/docs/animate#type) 的原生动画与 `spring` 生成器；取消动画后由组件直接设置键盘、触屏与尺寸变化对应的位置。

短菜单最多八项，仅精细鼠标指向有效按钮时移动高亮。空隙不触发最近项，禁用项不高亮；含复选框等混合表单的面板不接入。键盘立即反馈。初次定位、滚动、改尺寸、触屏与减少动态效果模式不执行位移动画。

共用弹窗由 React Aria 的 entering／exiting 生命周期维持挂载，opacity 与 scale .98 在 200ms 内进入，120ms 退出。批量选择条沿底部 8px 路径进出，进入 160ms、退出 120ms；Motion AnimatePresence 保持退出阶段，退出内容立即 inert，数量更新不重播。上述数值是 Peach 的设计选择，不宣称是参考站的弹窗与操作条实测值。

无障碍与生产边界：保留 React Aria 焦点管理，键盘与减少动态效果模式即时开合；不改变 API、账本写入、选择集合和提交时机。运行回归在 `frontend/e2e/design-*.test.ts`。

局部滚动区通过共用 `attachOverlayScrollbar` 接入上下边缘提示：仍可滚动的一侧显示 16px 渐隐与 2px 背景模糊，到头即撤去，不影响滚轮与点击。整页不叠加此效果。此处根据复核卡截图设计；参考站“超出框范围的模糊”实时视觉证据未取得，不宣称是上游效果的复刻。
