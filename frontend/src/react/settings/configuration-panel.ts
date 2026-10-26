/* 配置页一组的根节点：一条竖排，组内块间距 24px。配置页按页签切分组时，根节点同时是那一格页签面板
 * （`data-board-group`、`role="tabpanel"`、`aria-labelledby`），选中的那一组多一个 `ui-board-group-active`：
 * 同目录 `configuration-page.css` 只显示带它的那一组。 */
import type { ConfigurationPanel } from '../bundle';

const GROUP = 'flex flex-col gap-6';

export function groupRoot(panel?: ConfigurationPanel) {
  if (!panel) return { className: GROUP };
  return {
    className: panel.active ? `${GROUP} ui-board-group-active` : GROUP,
    'data-board-group': String(panel.index),
    id: panel.id,
    role: 'tabpanel',
    'aria-labelledby': panel.labelledBy,
  };
}
