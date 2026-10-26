import { describe, expect, it } from 'vitest';
import { DOTS, clampPage, pageCount, paginationHtml, paginationRange } from '../src/pagination';

describe('复核分页', () => {
  it('页数少时全列，多时两侧折成省略号并留一个邻页', () => {
    expect(paginationRange(1, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(paginationRange(1, 10)).toEqual([1, 2, 3, 4, 5, DOTS, 10]);
    expect(paginationRange(10, 10)).toEqual([1, DOTS, 6, 7, 8, 9, 10]);
    expect(paginationRange(5, 10)).toEqual([1, DOTS, 4, 5, 6, DOTS, 10]);
  });

  it('页数与页码都夹在合法范围内', () => {
    expect(pageCount(0, 20)).toBe(1);
    expect(pageCount(41, 20)).toBe(3);
    expect(clampPage(0, 3)).toBe(1);
    expect(clampPage(9, 3)).toBe(3);
    expect(clampPage(NaN, 3)).toBe(1);
  });

  it('只有一页时不画；当前页标 aria-current，首末页禁掉对应的方向键', () => {
    expect(paginationHtml(1, 1, '复核分页')).toBe('');
    const root = document.createElement('div');
    root.innerHTML = paginationHtml(1, 3, '复核分页');
    const nav = root.querySelector('nav.board-pagination')!;
    expect(nav.getAttribute('aria-label')).toBe('复核分页');
    const [prev, next] = [...nav.querySelectorAll<HTMLButtonElement>(':scope > button')];
    expect(prev!.disabled).toBe(true);
    expect(next!.disabled).toBe(false);
    expect(next!.dataset.page).toBe('2');
    expect(nav.querySelector('[aria-current="page"]')?.textContent).toBe('1');
    expect([...nav.querySelectorAll('li button')].map(button => button.textContent)).toEqual(['1', '2', '3']);
    expect(nav.querySelector('use')?.getAttribute('href')).toBe('#i-chevron-left');
    root.innerHTML = paginationHtml(3, 3, '复核分页');
    expect(root.querySelector<HTMLButtonElement>('nav > button:last-of-type')!.disabled).toBe(true);
    expect(root.querySelector('[aria-current="page"]')?.textContent).toBe('3');
  });
});
