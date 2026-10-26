import type { ReactNode } from 'react';

/** 详情底部：当前作品的分集队列与后续推荐共享一行。 */
export function DetailContinuation({ queue, children, editionCards }: { queue: ReactNode; children: ReactNode; editionCards?: number }) {
  return <div data-detail-continuation="" data-edition-cards={editionCards == null ? undefined : Math.min(3, editionCards)}>{queue}{children}</div>;
}
