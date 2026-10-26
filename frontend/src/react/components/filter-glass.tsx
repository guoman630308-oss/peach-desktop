/* 粘在顶栏下沿的那条玻璃操作条：一个小标题后面一排胶囊键，或者上下两排的筛选浮层。
 *
 * 料（漂移的两团、斜光、内嵌边与落影）由 `web/board.css` 的 `[data-glass-pane]` 一处给，
 * 几何、饱和度与文字色在 `../styles.css` 的 `[data-filter-glass]`，高对比与减少透明度的回退
 * 也在那一条上。吸到顶栏下沿那一刻由 `use-stuck.ts` 标 `data-stuck`，影换成抬起来那一档。
 * 胶囊键 30px 高、透明底、一圈 `--glass-low`，
 * 悬停换成 `--glass-rim` 边加 `--glass-pick-fill` 底——玻璃上的键不是 BoardUI 那种实心按钮。 */
import { useRef, type ReactNode, type RefObject } from 'react';
import { cx } from '@/utils/cx';
import { IconButton, type IconButtonProps } from '@/components/base/buttons/icon-button';
import { spinnerHtml } from '@peach/legacy/ui';

import { useStuck } from './use-stuck';
import { useHorizontalScroller } from './use-horizontal-scroller';

const BusyIcon = ({ className }: { className?: string }) => <span aria-hidden className={className}
  dangerouslySetInnerHTML={{ __html: spinnerHtml('正在处理') }} />;

/** 筛选栏动作使用 30px 尺寸和蓝色动作表面。 */
export function FilterIconButton({ label, icon, busy = false, onPress, ...props }:
  Omit<IconButtonProps, 'aria-label' | 'size' | 'onClick'> & { label: string; busy?: boolean; onPress(): void }) {
  return <IconButton {...props} data-filter-action="" size="small" icon={busy ? BusyIcon : icon}
    aria-label={label} title={props.title || label} aria-busy={busy || undefined} aria-disabled={busy || undefined}
    onClick={() => { if (!busy) onPress() }} />;
}

export function FilterGlass({ title, children }: { title: string; children: ReactNode }) {
  const glass = useRef<HTMLDivElement>(null);
  useStuck(glass);
  return (
    <div ref={glass} role="group" aria-label={title} data-filter-glass data-glass-pane=""
      className="sticky top-topbar z-10 mb-5.5 flex flex-wrap items-center gap-x-2.5 gap-y-2 px-4 py-2.5">
      <h3 className="mr-1 text-body-medium">{title}</h3>
      {children}
    </div>
  );
}

/** 两排的那一副：首页、标签页与实体页的筛选浮层。上排是一行横滚的筛选药丸，下排是读数和
 *  这一屏的视图控件。几何同壳的 `.board-filter-frame`：上排 48px 高、8px／12px 内边距，下排最矮
 *  46px、格间 12px。整块吸在顶栏下沿，粘住时仍是同一块玻璃。
 *
 *  实体页那一副的几何差一点（上排格间 6px、下排最矮 48px），上排也不是整排一起滚——左端的
 *  媒体键钉住，只有里面那一格滚。这些由调用方在 `className`／`topClassName`／`bottomClassName`
 *  里改写，`cx` 合并时后写的赢。`panes` 是直接挂在浮层根上的那几块滑动玻璃
 *  （`use-view-glide.ts`），`attrs` 给根补壳要认的标记（窄屏让位认 `data-filter-frame`）。
 *  `busy` 标在下排上：读数在等这一趟取数，下排里认它的动效（换批键描线）跟着走。上排没有可放的
 *  东西时 `top` 给 `null`，整行不出，玻璃只剩下排。 */
export function FilterGlassRows(
  { label, topLabel, top, bottom, className, topClassName, bottomClassName, panes, attrs, busy, bottomRef }: {
    label: string;
    topLabel: string;
    top: ReactNode;
    bottom: ReactNode;
    className?: string;
    topClassName?: string;
    bottomClassName?: string;
    panes?: ReactNode;
    attrs?: Record<`data-${string}`, string>;
    busy?: boolean;
    bottomRef?: RefObject<HTMLDivElement | null>;
  },
) {
  const glass = useRef<HTMLDivElement>(null);
  const topScroll = useHorizontalScroller<HTMLDivElement>();
  const bottomScroll = useHorizontalScroller<HTMLDivElement>(bottomRef);
  useStuck(glass);
  return (
    <div ref={glass} role="group" aria-label={label} data-filter-glass data-glass-pane="" {...attrs}
      className={cx('sticky top-topbar z-10 mb-5.5 flex flex-col', className)}>
      {panes}
      {top === null ? null : (
        <div ref={topScroll} role="group" aria-label={topLabel} data-filter-row="top"
          className={cx('flex h-12 min-w-0 items-center gap-1.75 overflow-x-auto overscroll-x-contain px-3 py-2',
            topClassName)}>
          {top}
        </div>
      )}
      <div ref={bottomScroll} data-filter-row="bottom" aria-busy={busy || undefined}
        className={cx('flex min-h-11.5 min-w-0 items-center gap-3 px-3 py-2', bottomClassName)}>
        {bottom}
      </div>
    </div>
  );
}

/* 浮层上的筛选药丸：8px 圆角、13px 字，未选中是一圈虚线 `--glass-low`，悬停转实线；选中的
 * 线与填充归调用方（标签类型药丸取自己的类型色，见 `../styles.css` 的 `[data-tag-pill]`）。 */
const FILTER_PILL = 'flex h-7.5 flex-none cursor-pointer items-center gap-1.75 rounded-lg border border-dashed'
  + ' border-(--glass-low) bg-transparent px-2.5 text-body-2-regular leading-5 whitespace-nowrap text-inherit'
  + ' outline-none hover:border-solid focus-visible:ring-2 focus-visible:ring-border-focus-ring';

export function FilterPill(
  { children, pressed, onPress, className, ...data }:
  { children: ReactNode; pressed: boolean; onPress(): void; className?: string } & Record<`data-${string}`, string>,
) {
  return (
    <button type="button" {...data} aria-pressed={pressed} className={cx(FILTER_PILL, className)}
      onClick={onPress}>
      {children}
    </button>
  );
}

/* 浮层上的动作键：平时一圈 `--glass-low` 细线，悬停浮起一块首页那种提亮的玻璃（料在 `../styles.css`
 * 的 `[data-glass-pill]`）。只换填充的话，浅色下那一档白压在同样发白的浮层上分不出来。 */
const PILL = 'h-7.5 cursor-pointer rounded-full border border-(--glass-low) bg-transparent px-3'
  + ' text-body-2-regular leading-none whitespace-nowrap text-inherit outline-none'
  + ' focus-visible:ring-2 focus-visible:ring-border-focus-ring'
  + ' aria-busy:cursor-wait aria-busy:opacity-55';

export function GlassPill(
  { children, onPress, busy = false }: { children: ReactNode; onPress(): void; busy?: boolean },
) {
  return (
    <button type="button" data-glass-pill="" className={PILL} aria-busy={busy || undefined} aria-disabled={busy || undefined}
      onClick={() => { if (!busy) onPress() }}>
      {children}
    </button>
  );
}
