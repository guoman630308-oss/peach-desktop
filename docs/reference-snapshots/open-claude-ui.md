# Open Claude UI 阅读与取舍

本文件是人工阅读笔记，不登记为可被线上字节覆盖的上游快照。原文、版本与哈希登记在 [reference-sources.json](../reference-sources.json) 的 `open-claude-ui-*` 条目；采用的原则见 [Peach 设计准则](../DESIGN_GUIDELINES.md)。

## 证据范围

- 来源：[Caho1/open-claude-ui](https://github.com/Caho1/open-claude-ui)，阅读日期 2026-10-15。[固定源码树](https://github.com/Caho1/open-claude-ui/tree/501c05a587404048c6877b1358b70d4a17f20169)对应登记 revision。
- 阅读七篇设计手册、`code-examples/` 全部 32 个文件（30 个 TS／TSX 模块、CSS 与 Tailwind 配置）、包入口、清单、TypeScript 配置和资产说明；Storybook 核对资源加载与演示入口，未执行组件或安装上游依赖。
- **实测（源码读取）**：公开仓库的代码、配置与本文指出的实现分支可按固定版本复查。上游 Storybook 的浏览器视觉、交互和 Claude 官方原始 bundle 均为 **未取得**。
- **推导**：从代码分支得出的能力边界与失败条件。**推断**：迁移至 Peach 的产品收益，须在实际实现时验收。作者关于「官方精确还原」「生产级」「消除色阶断层」的描述不作为 Peach 实测结论。
- MIT 原文保存在 [许可证](upstream/open-claude-ui-license.txt)。快照保留原文字节与版权声明；生产代码没有消费上游模块。

## 七篇手册的采用范围

| 手册范围 | Peach 的判断 |
| --- | --- |
| 1. 设计哲学与 token | 采用表面层级、减少重复外框、稳定几何、语义排版与等宽数字；颜色、尺寸、字重、阴影与光学补偿沿用 Peach 的证据与 token |
| 2. 基础控件 | 采用独立边界、附件身份和状态、危险动作清楚表达的原则；按钮、互斥选择、Dialog 与等待反馈复用现有组件 |
| 3. 会话与指令 | 采用输入法保护、建议菜单的键盘优先级、对象与参数／结果分区；模型菜单、指令、Token 估算及每次工具调用强制授权不属于 Peach 的默认设计 |
| 4. 动效与渐隐 | 采用按角色选择动效、渐隐随实际溢出变化；多段渐变可选，数值须实测，持续光场与逐字模糊没有当前消费者 |
| 5. 媒体与产物 | 采用锚点缩放、图像实际范围、受限预载、分栏取消与恢复、增量更新；音频管线、专有 MIME 与代码编辑器不引入 |
| 6. 桌面与协作 | 采用真实任务状态、保持阅读位置、容器适配、可退出的键盘导航与可恢复重排；Electron、macOS 标题栏与 Cowork 架构不引入 |
| 7. 工程与资产 | 采用明确的依赖和资产归属；不加载上游全局 CSS、Tailwind 配置、字体或提示音，保留 Peach 已固定的运行依赖 |

## 组件源码逐项判断

下表文件均位于固定源码树的 `code-examples/`。判断是设计参考范围，不是运行验收或代码采用清单。

| 文件 | 源码判断与 Peach 边界 |
| --- | --- |
| `CdsButton.tsx` | 变体与尺寸明确；忙态使用原生 disabled、opacity 和按压缩放，Peach 使用现有 Button／setActionBusy 契约 |
| `SegmentedControl.tsx` | 单一移动底板可参考；没有方向键漫游，固定 layoutId，Peach 使用已有 Switch／Tabs／ViewGlide |
| `StatusIndicators.tsx` | 不同等待形态可参考；不复制颜色、周期或流式装饰光标 |
| `AttachmentCard.tsx` | 身份、大小、移除与上传进度分区可参考；可点击 div 的键盘语义与进度可访问属性不完整 |
| `ChatComposer.tsx` | 附件仅模拟添加，Enter 路径没有合成检查；不作为输入保护实现 |
| `ChatMessageBubbles.tsx` | 正文与产物分层可参考；没有真实聊天传输，复制未等待结果，固定计时不用于成功反馈 |
| `ThinkingAccordion.tsx` | 任务与显示状态可分开；触发器是可点击 div，运行中强制展开不迁移到 Peach 日志 |
| `RichComposerWithMentions.tsx` | 合成状态、isComposing 与 229 守卫存在；只采用输入保护，建议源和 Token 算式是示例数据 |
| `ModelSwitcherWithTokenGauge.tsx` | 指标分解的展示层可参考；默认 Token 数值和模型矩阵不用于真实状态或成本判断 |
| `McpToolDecisionCard.tsx` | 对象、入参与结果分区可参考；1200ms 计时器直接设 success，不代表真实执行；拒绝与失败也合用状态 |
| `ClaudeMotionPresets.ts` | 按交互角色分预设可参考；四套数值、收敛时间及自写弹簧求解器不引入 |
| `ReasoningAmbientGlow.tsx` | 具有持续 Canvas 和多重装饰等待；没有当前消费者，不作为状态反馈模板 |
| `ScrollFadeContainer.tsx` | 纵向多段遮罩与边缘状态可参考；横向是线性遮罩，both 返回空样式；ResizeObserver 只观察容器自身 |
| `ClaudeAudioPlayer.ts` | 代码合成音效确实存在；没有手册描述的 AudioWorklet 流式 PCM 管线，不作为 Peach 播放器替代 |
| `LiveMicActivityBars.tsx` | 六段采样与门限映射存在；语音场景无当前消费者，循环调用 Hook 与 idle 持续帧循环需独立验证 |
| `MediaLightboxViewer.tsx` | 锚点公式和受限图像窗口可参考；没有 Tab 焦点圈定，拖动会累计起点位移，测量读取变换后矩形，不替换 Swiper |
| `useClaudeSharedElementTransition.ts` | 图像内切范围与 reduced motion 分支可参考；目标名称在 ready 后设置，不能仅凭源码声明共享元素视觉通过 |
| `ArtifactsSplitPaneResizer.tsx` | 取消回滚与展开入口可参考；没有 4px 门槛、pointercancel 或方向键调节，窄容器可能使 min／max 冲突 |
| `EpitaxyIframeBridge.tsx` | source／origin 校验存在；祖先页面曾经激活不证明当前 iframe 手势，不能作为可靠授权边界；不引入此桥 |
| `StreamingDiffViewer.tsx` | 统一／分屏、行号与内容的归属可参考；实际是每次全量解析并渲染表格，没有 Shadow DOM、Worker 或增量 feed |
| `CodeBlock.tsx` | 复制动作与局部反馈可参考；没有等待剪贴板 Promise，Peach 成功回执以实际结果为准 |
| `createClaudeMainWindow.ts` | Electron 专属窗口工厂；Peach 的浏览器服务与托盘入口不采用此宿主 |
| `ClaudeDesktopHeader.tsx` | 系统控件避让只属于特定宿主；普通 Web 页面不继承 macOS 交通灯占位 |
| `DraggableSessionList.tsx` | 落点提示与键盘重排可参考；数据只在本组件内存，移出原项后的插入索引需验证，不作为持久化实现 |
| `CoworkAgentCanvas.tsx` | 任务、成员与产物分区可参考；静态示例数据，没有宣称的 BroadcastChannel 同步 |
| `useClaudeScrollPinning.ts` | 解除跟随与跳至最新可参考；需要调用方接线，150ms／40px／120px 不成为 Peach 固定参数 |
| `ContainerResponsiveBar.tsx` | 按容器宽度压缩辅助标签可参考；固定模型名与断点不是通用工具栏组件 |
| `useClaudeRovingFocus.ts` | 单一 Tab 停靠点与 RTL 意图可参考；首项向前时 -1 同时被用作无目标标记，循环回绕分支不执行；没有真实二维网格几何 |
| `useRateLimitBackoff.ts` | 等待、倒计时和手动恢复可参考；重试策略仍归服务端，±15% 抖动不等于 Full Jitter，不能照搬等待时限 |
| `cds-theme.css` | 分层和语义别名可参考；全局选择器与可变字体补偿会覆盖 Peach，保留自身 token |
| `tailwind.config.js` | 展示上游样式词汇；Peach 使用已有 Tailwind 4 与 BoardUI 构建，不加载该配置 |

## 保留的 Peach 差异

采用的是设计准则；运行时保持 React、BoardUI、React Aria、Motion、Swiper、现有菜单、滚动与任务契约。普通按钮按下不缩放，选中态继续使用项目既定填充；「单一外框」允许独立字段与可访问焦点的必要边界。

上游 Storybook 需要 Tailwind CDN、React／ReactDOM UMD、Babel 和 Google Fonts；它不是可直接作为离线验收入口的自包含页面。`package.json` 为 1.0.0，`build` 调用 tsc，而配置 `noEmit: true`；未执行构建，不能据此认定可安装产物可用。

设计准则不新增业务功能、批准范围、依赖或生产设置，不写真实 ledger。页面采用时需验证输入法、焦点、取消、部分失败、快速反向操作、窄容器、缺图与减少动态效果；不存在当前消费者的能力不提前接入。
