import { RiArrowDownSLine } from '@remixicon/react';
import {
  Children, cloneElement, isValidElement, useId, useLayoutEffect, useRef, useState,
  type ReactElement, type ReactNode,
} from 'react';

interface ExpandableRankingProps {
  children: ReactNode;
  className?: string;
  previewCount?: number;
}

/** 排名只露前几项；点箭头后保留同一棵列表，用测得的高度完成展开与折叠。 */
export function ExpandableRanking(
  { children, className = '', previewCount = 10 }: ExpandableRankingProps,
) {
  const listId = useId();
  const list = useRef<HTMLOListElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [animating, setAnimating] = useState(false);
  const rows = Children.toArray(children);
  const expandable = rows.length > previewCount;

  useLayoutEffect(() => {
    const element = list.current;
    if (!element || !expandable) return undefined;
    const measure = () => {
      const row = element.children[Math.min(previewCount, element.children.length) - 1] as HTMLElement | undefined;
      if (!row || !element.offsetWidth) return;
      element.style.setProperty('--ranking-collapsed-height', `${row.offsetTop - element.offsetTop + row.offsetHeight}px`);
      element.style.setProperty('--ranking-expanded-height', `${element.scrollHeight}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [expandable, previewCount, rows.length]);

  const visibleRows = rows.map((row, index) => {
    if (!isValidElement(row) || index < previewCount) return row;
    return cloneElement(row as ReactElement<{ inert?: boolean }>, { inert: expanded ? undefined : true });
  });

  return (
    <div data-expandable-ranking data-expandable={expandable || undefined}
      data-expanded={expanded || undefined}
      data-animating={animating || undefined}>
      <ol id={listId} ref={list} data-expandable-ranking-list className={className}
        onTransitionEnd={(event) => {
          if (event.target === list.current && event.propertyName === 'max-height') setAnimating(false);
        }}>
        {visibleRows}
      </ol>
      {expandable ? (
        <button type="button" data-ranking-toggle aria-controls={listId} aria-expanded={expanded}
          aria-label={expanded ? '收起排名' : '展开更多排名'}
          onClick={() => { setAnimating(true); setExpanded((current) => !current) }}>
          <RiArrowDownSLine aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
