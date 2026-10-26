/* 整行可选的列表：点在一行的空白处就是选这一行，点在行里的控件、链接或输入框上不算。
 *
 * 勾选框本来就在行首，但它只有 16px，一整行的其余部分点了没反应的话，选几条就要瞄几次。
 * 判「空白」看的是点到的元素往上找有没有可交互的祖先，找到行为止：行外面的祖先不算，
 * 否则整张表包在一个 `label` 里时每一下都成了「点在控件上」。 */
import type { MouseEvent, SyntheticEvent } from 'react';

const INTERACTIVE = [
  'a', 'button', 'input', 'select', 'textarea', 'label',
  '[role="checkbox"]', '[role="switch"]', '[role="button"]', '[role="link"]', '[role="menu"]',
].join(',');

/** 这一下是不是点在 `row` 的空白处。`row` 是整行的元素，事件可以从它的任何后代冒上来。 */
export function clickedBlank(event: SyntheticEvent | Event, row: Element): boolean {
  const target = event.target;
  if (!(target instanceof Element) || !row.contains(target)) return false;
  const control = target.closest(INTERACTIVE);
  return !control || !row.contains(control) || control === row;
}

/** 卡片网格的 `onMouseDown`：按着 Shift 点卡是范围多选，这一下不让浏览器把两次点击之间的
 * 文字一并刷成选区。只拦按下，`click` 照常冒上来交给选择逻辑。 */
export function suppressShiftTextSelection(event: MouseEvent): void {
  if (event.shiftKey) event.preventDefault();
}
