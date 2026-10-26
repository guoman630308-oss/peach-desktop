/* 页面分区 Tabs：中性填充选中态，支持方向键与窄屏横向滚动。 */
import { useLayoutEffect, useRef, type KeyboardEvent } from 'react';

import { spriteGlyph } from './sprite-glyph';
import { useHorizontalScroller } from './use-horizontal-scroller';

export interface BoardTab<V extends string> {
  value: V;
  label: string;
  /** 遗留雪碧图里的字形名，只在它指向对象（厂牌、事务所、本地、订阅源）时给。 */
  symbol?: string;
}

/** 页签与分组标题后面挂的那个计数徽标（遗留层 `.ui-board-tab-count` 的同款）：12/16 半档字重、主文字色
 *  10% 的底，整块降到五成，是附注不是读数。口径由调用方给，徽标自己不算数。 */
export function TabCount({ value }: { value: number }) {
  return (
    <span data-tab-count=""
      className="ml-1.5 inline-block rounded-sm bg-text-primary/10 px-1 py-px text-caption-1-medium text-text-primary opacity-50">
      {value.toLocaleString()}
    </span>
  );
}

const TAB = 'relative flex flex-none cursor-pointer items-center whitespace-nowrap rounded-md'
  + ' px-3 py-2 text-body-regular text-text-secondary outline-none'
  + ' focus-visible:outline-2 focus-visible:-outline-offset-3 focus-visible:outline-border-focus-ring'
  + ' hover:text-text-primary aria-selected:bg-background-tertiary-default aria-selected:text-text-primary'
  + ' max-board-narrow:px-2.25 max-board-narrow:text-body-2-regular';

export function BoardTabs<V extends string>(
  { tabs, value, label, onChange, className = 'mb-5.5' }:
  { tabs: readonly BoardTab<V>[]; value: V; label: string; onChange(value: V): void; className?: string },
) {
  const list = useRef<HTMLDivElement>(null);
  const scrollRef = useHorizontalScroller(list);

  useLayoutEffect(() => {
    const host = list.current;
    if (!host) return;
    const measure = () => {
      const selected = host.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
      if (!selected || !selected.offsetWidth) return;
      /* 窄屏这一排横着滚：选中那一枚落在可见区外时把这一排挪到它露全，只动这一排，不动页面。 */
      const left = selected.offsetLeft, right = left + selected.offsetWidth;
      if (left < host.scrollLeft) host.scrollLeft = left;
      else if (right > host.scrollLeft + host.clientWidth) host.scrollLeft = right - host.clientWidth;
    };
    measure();
  }, [value]);

  /* 左右键在同一排里挪，Home／End 到两头，挪到哪一枚就是选了哪一枚：这一排每一枚都是一个
     地址，焦点停在哪儿、页面就摆哪儿，和侧栏导航同一个说法。 */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const at = tabs.findIndex((tab) => tab.value === value);
    const next = event.key === 'ArrowRight' ? at + 1 : event.key === 'ArrowLeft' ? at - 1
      : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -2;
    if (next === -2) return;
    event.preventDefault();
    const target = tabs[(next + tabs.length) % tabs.length];
    list.current?.querySelector<HTMLElement>(`[data-tab="${target.value}"]`)?.focus();
    if (target.value !== value) onChange(target.value);
  };

  return (
    <div ref={scrollRef} role="tablist" aria-label={label} data-board-tab-list="" onKeyDown={onKeyDown}
      className={`relative flex flex-none gap-1 overflow-x-auto max-board-narrow:gap-0 ${className}`}>
      {tabs.map((tab) => {
        const Glyph = tab.symbol ? spriteGlyph(tab.symbol) : null;
        const selected = tab.value === value;
        return (
          <button key={tab.value} type="button" role="tab" data-tab={tab.value} aria-selected={selected}
            tabIndex={selected ? 0 : -1} className={TAB}
            onClick={() => { if (!selected) onChange(tab.value) }}>
            {Glyph ? <Glyph className="mr-1.5 size-4" /> : null}
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
