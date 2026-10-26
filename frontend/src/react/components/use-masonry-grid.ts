import { useLayoutEffect, useRef } from 'react';

/** Grid 按列表顺序固定列归属，卡片高度只影响所在列的后续卡片。 */
export function useMasonryGrid(enabled: boolean, revision: string) {
  const host = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const wall = host.current;
    if (!wall) return;
    const cards = Array.from(wall.children).filter((node): node is HTMLElement =>
      node instanceof HTMLElement && node.hasAttribute('data-media-card'));
    if (!enabled) return;
    let stopped = false;
    let frame = 0;
    const arrange = () => {
      if (stopped) return;
      const columns = Math.max(1, Number(getComputedStyle(wall).getPropertyValue('--photo-columns')) || 1);
      cards.forEach((card, index) => {
        card.style.gridColumn = String(index % columns + 1);
      });
      // 列宽确定后批量读取卡片盒高；轨道只取卡片边界，不按每个像素创建一条轨道。
      const heights = cards.map((card) => Math.ceil(card.getBoundingClientRect().height));
      const ends = Array<number>(columns).fill(0);
      const boxes = heights.map((height, index) => {
        const column = index % columns;
        const top = ends[column]!;
        const bottom = top + height + 14;
        ends[column] = bottom;
        return { top, bottom };
      });
      const lines = [...new Set([0, ...boxes.flatMap(({ top, bottom }) => [top, bottom])])].sort((a, b) => a - b);
      const rows = new Map(lines.map((line, index) => [line, index + 1]));
      wall.style.gridTemplateRows = lines.slice(1).map((line, index) => `${line - lines[index]!}px`).join(' ');
      cards.forEach((card, index) => {
        const box = boxes[index]!;
        card.style.gridRow = `${rows.get(box.top)} / ${rows.get(box.bottom)}`;
      });
    };
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(arrange);
    });
    observer.observe(wall);
    cards.forEach((card) => observer.observe(card));
    arrange();
    return () => {
      stopped = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      wall.style.removeProperty('grid-template-rows');
      cards.forEach((card) => {
        card.style.removeProperty('grid-column');
        card.style.removeProperty('grid-row');
      });
    };
  }, [enabled, revision]);
  return host;
}
