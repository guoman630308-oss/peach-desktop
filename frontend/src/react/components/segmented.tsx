/* 分段控件的选中面独立移动，文字、焦点与业务状态即时更新。 */
import { RadioGroup, TabList, type RadioGroupProps, type TabListProps } from 'react-aria-components';
import { useMovingSurface } from './use-moving-surface';
import { useHorizontalScroller } from './use-horizontal-scroller';

export function SegmentedTabList<T extends object>(props: TabListProps<T>) {
  const ref = useHorizontalScroller(useMovingSurface('selection'));
  return <TabList {...props} ref={ref} />;
}

export function SegmentedRadioGroup(props: RadioGroupProps) {
  const ref = useHorizontalScroller(useMovingSurface('selection'));
  return <RadioGroup {...props} ref={ref} />;
}

/** 轨道。宽度按内容收，窄屏装不下时自己横向滚，不把页面撑出滚动条。 */
export const SEGMENTED_TRACK = 'inline-flex w-max max-w-full items-center gap-0.5 overflow-x-auto'
  + ' overscroll-x-contain rounded-2lg bg-background-tertiary-default p-1';

/* 深色下选中面用 `background-primary-hover`：深色的 `background-primary-default` 与轨道
 * （`background-tertiary-default`）是同一档 neutral-800，画上去看不见。 */
/* 手机宽度每格左右各收 2px：四个中文页签在 320 宽的轨道里刚好排下，不被轨道裁掉末字。 */
export const SEGMENT = 'flex min-h-7 flex-none cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 max-sm:px-2'
  + ' text-body-medium whitespace-nowrap text-text-secondary outline-none transition-colors'
  + ' hover:text-text-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring'
  + ' data-focus-visible:ring-2 data-focus-visible:ring-border-focus-ring'
  + ' data-selected:bg-background-primary-default data-selected:text-text-primary data-selected:shadow-card'
  + ' dark:data-selected:bg-background-primary-hover';

/** `aria-selected` 而不是 `data-selected` 的宿主（自己写的 `<button role="tab">`）用这一串。 */
export const SEGMENT_ARIA = 'flex min-h-7 flex-none cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 max-sm:px-2'
  + ' text-body-medium whitespace-nowrap text-text-secondary outline-none transition-colors'
  + ' hover:text-text-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring'
  + ' aria-selected:bg-background-primary-default aria-selected:text-text-primary aria-selected:shadow-card'
  + ' dark:aria-selected:bg-background-primary-hover';

/** 只摆字形的那一档（版式、大图／紧凑）：每格正方，字形 16px，配上面的 `SEGMENTED_TRACK`。 */
export const SEGMENT_ICON = 'flex size-7 flex-none cursor-pointer items-center justify-center rounded-md'
  + ' text-text-secondary outline-none transition-colors'
  + ' hover:text-text-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring'
  + ' data-focus-visible:ring-2 data-focus-visible:ring-border-focus-ring'
  + ' data-selected:bg-background-primary-default data-selected:text-text-primary data-selected:shadow-card'
  + ' dark:data-selected:bg-background-primary-hover';

/* 玻璃浮层上的那一档。flat 面那一副（灰轨道衬一枚不透明的白滑块）摆到浮层上就是整条里
 * 唯一一块实心的面，读起来像贴上去的另一个控件：轨道撤掉，字色按这块玻璃自己的文字色降到
 * 六成，选中那一格换成浮层选中态那块玻璃（`../styles.css` 的 `[data-glass-segment]`）。 */
export const SEGMENTED_GLASS_TRACK = 'inline-flex w-max max-w-full flex-none items-center gap-0.5 rounded-lg';

export const SEGMENT_GLASS = 'relative flex size-7.5 flex-none cursor-pointer items-center justify-center rounded-lg'
  + ' text-(--glass-text)/62 outline-none transition-colors hover:text-(--glass-text)'
  + ' focus-visible:ring-2 focus-visible:ring-border-focus-ring data-selected:text-(--glass-text)';
