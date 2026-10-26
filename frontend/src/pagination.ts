/** Board 的 Pagination（boardui.com `r/pagination.json`）：上一页／下一页是 32px 的次级小键，页码是 32×32、圆角 8 的格，
 * 当前页借次级键的面（primary 底、border-button 描边、xs 阴影），其它页码只有文字，悬停才有底；页数多时两侧折成「…」，
 * 当前页左右各留 `sibling` 个邻页。页码格不做过渡：动了背景和阴影，换页时上一枚当前页会肉眼可见地淡出。 */
export const DOTS = '…';

export function paginationRange(current: number, total: number, sibling = 1): (number | typeof DOTS)[] {
  const range = (start: number, end: number) => Array.from({ length: end - start + 1 }, (_, i) => start + i);
  // 首页 + 末页 + 当前页 + 两侧邻页 + 两个「…」都摆得下时就不折。
  if (sibling * 2 + 5 >= total) return range(1, total);
  const left = Math.max(current - sibling, 1), right = Math.min(current + sibling, total);
  const leftDots = left > 2, rightDots = right < total - 2;
  if (!leftDots && rightDots) return [...range(1, 3 + 2 * sibling), DOTS, total];
  if (leftDots && !rightDots) return [1, DOTS, ...range(total - (2 + 2 * sibling), total)];
  return [1, DOTS, ...range(left, right), DOTS, total];
}

export function pageCount(total: number, perPage: number): number {
  return Math.max(1, Math.ceil(total / perPage));
}

export function clampPage(page: number, pages: number): number {
  return Math.min(Math.max(1, Math.floor(page) || 1), pages);
}

/** 只有一页就什么都不画（上游 `totalPages <= 1` 返回 null）。点击由宿主接 `[data-page]`。 */
export function paginationHtml(page: number, pages: number, label: string): string {
  if (pages <= 1) return '';
  const cells = paginationRange(page, pages).map(item => item === DOTS
    ? '<li class="ui-board-page-dots" aria-hidden="true">…</li>'
    : `<li><button type="button" class="ui-board-page" data-page="${item}" aria-label="第 ${item} 页"${item === page ? ' aria-current="page"' : ''}>${item}</button></li>`).join('');
  return `<nav class="board-pagination" aria-label="${label}">
    <button type="button" class="geist-button" data-page="${page - 1}"${page <= 1 ? ' disabled' : ''}><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-chevron-left"></use></svg>上一页</button>
    <ul>${cells}</ul>
    <button type="button" class="geist-button" data-page="${page + 1}"${page >= pages ? ' disabled' : ''}>下一页<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-chevron-right"></use></svg></button>
  </nav>`;
}
