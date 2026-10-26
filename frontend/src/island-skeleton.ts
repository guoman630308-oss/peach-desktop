/* React 页面与卡片的首屏骨架：画在 `.peach-react` 里，按键照 Board UI `Button` 渲染出的静止态写。
 *
 * 骨架跑在 React 包加载之前，只能是一段 HTML。挂在 `.peach-react` 外面的话，卡面、字色与按键
 * 取的是遗留层那套 token，接管时底色、字号和按键的尺寸各跳一档。类名只取决定几何与颜色的
 * 那几项（`frontend/src/react/boardui/components/base/buttons/button.tsx` 的 size / variant /
 * icon / label 四张表），悬停、按下、焦点环留给接管后的真按键。 */

import { esc } from '@peach/legacy/core';

type Variant = 'primary' | 'secondary' | 'ghost';
type Size = 'medium' | 'small';

const BASE = 'inline-flex items-center justify-center gap-0.5 whitespace-nowrap overflow-hidden font-sans';
const SIZE: Record<Size, string> = {
  medium: 'h-9 rounded-2lg p-2 text-body-medium',
  small: 'h-8 rounded-lg px-2 py-1.5 text-body-medium',
};
/* 小号纯图标键在 Board UI 里是强制 32×32、内边距归零（`size-8 p-0`）；中号纯图标沿用基础尺寸。 */
const ICON_ONLY: Record<Size, string> = { medium: SIZE.medium, small: 'size-8 rounded-lg p-0 text-body-medium' };
const ICON: Record<Size, string> = { medium: 'size-5 shrink-0', small: 'size-[18px] shrink-0' };
const LABEL: Record<Size, string> = {
  medium: 'inline-flex items-center justify-center px-1 shrink-0',
  small: 'inline-flex items-center justify-center px-0.5 shrink-0',
};
const VARIANT: Record<Variant, string> = {
  primary: 'bg-button-primary text-text-white shadow-xs',
  secondary: 'bg-background-primary-default text-text-primary border border-border-button-default shadow-xs',
  ghost: 'bg-button-ghost-background text-button-ghost-foreground',
};

/** 岛里没有遗留层那条给 Lucide 字形描边的全局规则，描边跟 React 那侧一样写在 `svg` 自己身上。 */
export const islandGlyph = (name: string, cls: string): string =>
  `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="#i-${name}"/></svg>`;

/** 收藏入口在页面等待阶段保留图标与尺寸，交互由已就绪的页面接管。 */
export function islandShortcutsEntry(): string {
  return `<span data-shortcuts-entry class="inline-flex flex-none"><button type="button" data-shortcuts-trigger data-filter-action data-skeleton-action disabled tabindex="-1" aria-label="我的收藏" title="我的收藏" class="inline-grid size-7.5 flex-none place-items-center rounded-(--control-radius) border border-(--border-10) bg-(--surface) text-(--muted)">${islandGlyph('star', 'size-4')}</button></span>`;
}

/** 与 Board Input 的中号外框、前缀和正文使用相同几何。 */
export function islandSearchInput(placeholder: string, value = '', label = placeholder): string {
  return `<div data-input-size="medium" class="group flex h-max w-full flex-col items-start gap-1"><div data-rac class="relative flex w-full items-center rounded-2lg bg-background-tertiary-default text-foreground-icon-tertiary ring-2 ring-inset ring-transparent p-2"><div class="flex w-full items-center gap-2 min-w-0"><div class="flex flex-1 items-center gap-0.5 min-w-0">${islandGlyph('search', 'size-5 shrink-0')}<input type="search" enterkeyhint="search" readonly tabindex="-1" aria-label="${esc(label)}" value="${esc(value)}" placeholder="${esc(placeholder)}" class="min-w-0 flex-1 bg-transparent border-0 outline-none p-0 m-0 font-sans text-body-regular text-text-primary pl-1 placeholder:text-text-tertiary"></div></div></div></div>`;
}

export interface IslandButton {
  variant?: Variant;
  size?: Size;
  glyph?: string;
  label?: string;
  /** 追加在 `<button>` 上的原样属性，例如 `aria-disabled`、`aria-pressed`、`aria-label`。 */
  attrs?: string;
  /** 这颗键的字在窄工具行里收起、只留字形，对应 React 那侧按工具行宽度切换的 `iconOnly`。 */
  compact?: boolean;
}

/** 一颗与 Board UI `Button` 同尺寸、同变体的静止态按键。有字没图标、有图标没字、两者都有三种都按原组件排。 */
export function islandButton(
  { variant = 'primary', size = 'medium', glyph, label, attrs = '', compact = false }: IslandButton,
): string {
  const shape = label ? SIZE[size] : ICON_ONLY[size];
  const icon = glyph ? islandGlyph(glyph, ICON[size]) : '';
  const text = label ? `<span class="${LABEL[size]}"${compact ? ' data-compact-label' : ''}>${label}</span>` : '';
  return `<button type="button" class="${BASE} ${shape} ${VARIANT[variant]}"${attrs ? ` ${attrs}` : ''}>${icon}${text}</button>`;
}

type SelectSize = 'md' | 'sm';
const SELECT: Record<SelectSize, { box: string; value: string; chevron: string }> = {
  md: { box: 'gap-1.5 px-2.5 py-2 text-body-medium', value: 'gap-[5px]', chevron: 'size-4' },
  sm: { box: 'gap-1 px-[7px] py-1 text-body-2-medium', value: 'gap-1', chevron: 'size-3.5' },
};

/** Board UI `Select` 的静止态：外层 `group flex flex-col`，里面一颗带 `aria-haspopup="listbox"`
 *  的触发键。高度由 `react/styles.css` 按这个标记统一补齐，骨架用同一个元素和标记才吃得到那条规则。
 *  `label` 可以是一截等数据的占位条；`className` 落在外层，与组件的 `className` 同位。 */
export function islandSelect(
  label: string, { size = 'md', className = '', attrs = '' }: { size?: SelectSize; className?: string; attrs?: string } = {},
): string {
  const shape = SELECT[size];
  return `<div class="group flex flex-col${className ? ` ${className}` : ''}"><button type="button" aria-haspopup="listbox"${attrs ? ` ${attrs}` : ''} class="flex w-full items-center justify-between rounded-2lg border border-border-button-default bg-background-primary-default shadow-xs text-text-primary ${shape.box}"><span class="flex min-w-0 items-center truncate ${shape.value}">${label}</span>${islandGlyph('chevron-down', `shrink-0 text-text-secondary ${shape.chevron}`)}</button></div>`;
}

/* 以下类名抄自 `react/components/collection-summary.tsx` 的等待态，由用例逐字对照。 */
const SUMMARY_CLASS = 'flex items-end justify-between gap-4 rounded-surface bg-background-secondary-default px-6 py-5 '
  + 'max-compact:flex-col max-compact:items-start max-compact:gap-3 max-compact:p-4 dark:bg-background-primary-default';
const SUMMARY_TERMS = 'flex min-w-0 flex-col gap-2';
const SUMMARY_LABEL = 'text-body-regular text-text-secondary';
const SUMMARY_FIGURE = 'text-display-4-medium tabular-nums text-text-primary';
const SUMMARY_PENDING = 'relative inline-block h-8 w-24 rounded-2lg align-middle skeleton-sheen';

/** `CollectionSummary` 带 `flush`、等数据时的那一版：名目此刻就有，大数那一格是微光占位。 */
export function islandSummary(label: string): string {
  return `<div data-collection-summary="" class="${SUMMARY_CLASS}"><div class="${SUMMARY_TERMS}">`
    + `<span class="${SUMMARY_LABEL}">${label}</span>`
    + `<strong class="${SUMMARY_FIGURE}"><span data-skeleton="count" aria-hidden="true" class="${SUMMARY_PENDING}"></span></strong>`
    + `</div></div>`;
}
