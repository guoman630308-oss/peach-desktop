# 安装后教程取证

最后复核：2026-09-20。

本文件是三份源码的人工组合取证，不是单一上游原文镜像，因此不登记进
`docs/reference-sources.json`；每份源码的固定 revision 与 SHA-256 在下表分别记录。

## 来源

| 参考 | 固定版本与源码 | SHA-256 | 采用行为 |
| --- | --- | --- | --- |
| [RareUI Task List](https://www.rareui.com/components/tasklist) | `swamimalode07/rare-ui@368b93244b875a760e48ca4ecec7774b95f94263`，`components/ui/task-list.tsx` | `9038a6110902f0d944740f91e19e0bfc2cbee8a9397ba7f1f780f72ec6802657` | 24px 圆形状态位、完成文字划线并淡出、完成项移到清单末尾、减少动态效果时立即换态 |
| [BoardUI Announcement](https://www.boardui.com/components/announcement) | `BoardUI/boardui@3e76e282614b147a34b9b2a510e31b97d58a3909`，`components/base/announcement/announcement.tsx` | `ec0015c2c341cfdb281a9a319970ec7e5364de1934cb1a43f814250b2cdcdaa7` | 图标、标题、说明和整行次级动作组成紧凑提示卡；使用边框与主表面，不加阴影 |
| [BoardUI Notification](https://www.boardui.com/components/notification) | `BoardUI/boardui@3e76e282614b147a34b9b2a510e31b97d58a3909`，`components/base/notification/notification.tsx` | `10dc6985e2ab4e287266f756912bdeaf7610bf8b8a6941c8ea14ffd6b6c201fa` | `NotificationViewport` 默认固定在右下角；移动端距边 12px、桌面 24px，宽度为 `min(400px, 100vw - 24px)`，卡片用 16px 圆角、边框和下拉阴影 |

## Peach 采用方式

- 教程不放在首次设置表单；`POST /api/setup` 成功后用 `?onboarding=1` 标记一次安装后流程，进入 Peach 后显示。
- 任务不是可手动勾选的待办。教程读取馆藏、采集来源凭证、浏览器历史、关注来源、对应关注凭证和复核队列的实际状态，达到条件后自动打勾并移到清单末尾。
- 下一步按钮始终指向第一项未完成任务；每项可跳过并短时撤销，全部完成或跳过后教程自动消失。
- 教程挂在 `main` 之外的右下角固定浮窗，不参与当前页面正文排版；未处理完时跨页面持续显示。历史导入可先进入 `/taste?onboarding=1`，教程仍会保留。
- 切换页面时保留现有浮窗并静默刷新；任务状态没有变化时不重建卡片，避免浮窗闪烁。
- 浮窗按内容增长并受视口高度限制，任务区单独滚动；可折叠为标题与完成进度，全部处理后自动消失。任务链接与「跳过」各有独立悬停区；链接悬停时背景即时切换、上下两条内收直线分隔同时隐去，不做跨行淡变；暗色主题仍保留可辨识的线条，右箭头保持次级文字色。

## 明确差异

- RareUI 示例允许直接切换任务；Peach 禁止手动改状态，避免“点过页面”被误报成已完成。
- BoardUI Notification 是短消息；Peach 保留它的固定 viewport 与卡片外观，在卡片内增加可滚动任务清单，并且不自动消失。
- 未复刻站点视觉资产、字体或颜色，继续使用 Peach 的 BoardUI token、按钮与图标体系。
