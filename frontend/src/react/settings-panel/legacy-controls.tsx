/* 面板里沿用的三样共用控件：Geist Switch（`iconSwitchHtml`）、Geist Select（`selectFieldHtml`）与
 * 自绘拉条（`dialSliderHtml`）。
 *
 * 它们的模板、键盘与动效在 `src/ui-kit/` 一份，首页筛选条、卡片版式和配置页用的是
 * 同一套；这里只给每一枚一个容器，由助手把 HTML 写进去再接线。分段滑块由 `board-controls.ts`
 * 在 body 上的观察器看到新节点时自动接上。
 *
 * React 不进这几枚容器的子树：容器里只有 `ref`，没有 JSX 子节点，协调时不会去动助手写下的 DOM。 */
import { useLayoutEffect, useRef } from 'react';

import {
  dialSliderHtml, iconSwitchHtml, selectFieldHtml, setActionBusy, wireDialSlider, wireIconSwitch, wireSelectField,
} from '@peach/legacy/ui';

import type { Choice } from './settings-panel-api';

const rows = (options: readonly Choice[]): Choice[] => options.map(([value, label, mark]) => mark ? [value, label, mark] : [value, label]);

/** 一组互斥视图。`value` 与上一次画出来的不同、或者面板重新打开（`epoch` 换了）时整块重画：
 *  滑块按新的选中项重新量位置。点选本身不重画，DOM 已经是点完的样子。`variant` 是遗留模板
 *  `iconSwitchHtml` 认的那枚修饰类（主题那组的圆钮、封面那两组的文字档），样式在遗留表里。 */
export function IconSwitch({ id, name, legend, options, value, attr, variant, text = false, epoch, onChoose }: {
  id: string; name: string; legend: string; options: readonly Choice[]; value: string;
  attr: string; variant: string; text?: boolean; epoch: number; onChoose(value: string): void;
}) {
  const mount = useRef<HTMLDivElement | null>(null);
  const painted = useRef<{ value: string; epoch: number } | null>(null);
  const choose = useRef(onChoose);
  choose.current = onChoose;
  useLayoutEffect(() => {
    const node = mount.current;
    if (!node) return;
    if (painted.current && painted.current.value === value && painted.current.epoch === epoch) return;
    node.innerHTML = iconSwitchHtml(name, legend, rows(options), value, { attr, className: variant, text });
    wireIconSwitch(node, attr, (next) => {
      painted.current = { value: next, epoch };
      choose.current(next);
    });
    painted.current = { value, epoch };
  });
  return <div id={id} ref={mount} />;
}

/** 一个下拉。触发器上那一格的内容可由 `decorate` 改写（排序方向那一格叠着两枚箭头）。 */
export function SelectField({ id, label, options, value, disabled = false, busy = false, hidden, describedBy, epoch, onPick, decorate }: {
  id: string; label: string; options: readonly Choice[]; value: string; disabled?: boolean; busy?: boolean;
  hidden?: boolean; describedBy?: string; epoch: number; onPick(value: string): void;
  decorate?(field: HTMLElement): void;
}) {
  const mount = useRef<HTMLDivElement | null>(null);
  const field = useRef<(HTMLElement & { value: string; disabled: boolean }) | null>(null);
  const built = useRef(-1);
  const pick = useRef(onPick);
  pick.current = onPick;
  useLayoutEffect(() => {
    const node = mount.current;
    if (!node) return;
    if (built.current !== epoch || !field.current) {
      node.innerHTML = selectFieldHtml(rows(options), value, { label });
      const root = wireSelectField(node.firstElementChild as HTMLElement);
      root.addEventListener('change', () => pick.current(root.value));
      field.current = root;
      built.current = epoch;
    }
    const root = field.current;
    if (root.value !== value) root.value = value;
    if (root.disabled !== disabled) root.disabled = disabled;
    if ((root.getAttribute('aria-busy') === 'true') !== busy) setActionBusy(root, busy);
    decorate?.(root);
  });
  return <div id={id} ref={mount} hidden={hidden} aria-describedby={describedBy} />;
}

/** 一行「标签 + 0–max 拉条」。拉条由助手接在标签后面，和标签同为这一行的直接子节点；React 只管
 *  标签那一格。拖动中每一步走 `onStep`，松手、键盘落键才走 `onSettle`。 */
export function DialRow({ field, label, max, value, hidden, onStep, onSettle }: {
  field: string; label: string; max: number; value: number; hidden: boolean;
  onStep(value: number): void; onSettle(value: number): void;
}) {
  const row = useRef<HTMLDivElement | null>(null);
  const dial = useRef<{ value: number; set(next: number): void } | null>(null);
  const handlers = useRef({ onStep, onSettle });
  handlers.current = { onStep, onSettle };
  useLayoutEffect(() => {
    const node = row.current;
    if (!node || dial.current) return;
    node.insertAdjacentHTML('beforeend', dialSliderHtml({ value, min: 0, max, step: 1, label, attr: `data-glow-dial="${field}"` }));
    dial.current = wireDialSlider(node.lastElementChild as HTMLElement, {
      onInput: (next) => handlers.current.onStep(next),
      onChange: (next) => handlers.current.onSettle(next),
    });
  });
  useLayoutEffect(() => {
    if (dial.current && dial.current.value !== value) dial.current.set(value);
  }, [value]);
  return (
    <div data-glow-field={field} hidden={hidden} ref={row}>
      <span data-glow-field-label="">{label}</span>
    </div>
  );
}
