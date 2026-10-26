import { afterEach, expect, it, vi } from 'vitest';
import { publicationFresh, publicationLifetime } from '../../src/react/publication-cache';
import { cachedReviews, rememberReviews } from '../../src/react/jav-detail/review-cache';
import { cachedResources, rememberResources } from '../../src/react/wants/resource-cache';
import type { JavReviews } from '../../src/react/jav-detail/jav-detail';

afterEach(() => { localStorage.clear(); vi.useRealTimers() });
const date = (day: string) => Date.parse(`${day}T12:00:00Z`);
const page: JavReviews = { ok: true, page: 1, more: false, checked_at: '2026-10-13T12:00:00Z',
  items: [{ id: 'one', username: '示例用户', content: '评论正文', created_at: '2026-10-01' }] };

it('月底、未知日期与发行期每日更新都有明确边界', () => {
  expect(publicationLifetime('2024-01-31', date('2024-02-29'))).toBe(86_400_000);
  expect(publicationLifetime('2024-01-31', date('2024-03-01'))).toBe(Infinity);
  expect(publicationLifetime('2026-02-30', date('2026-10-13'))).toBe(86_400_000);
  for (const released of [undefined, '', '未知', '2026-11-01', '2026-10-01']) {
    expect(publicationFresh(date('2026-10-13'), released, date('2026-10-14') - 1)).toBe(true);
    expect(publicationFresh(date('2026-10-13'), released, date('2026-10-14'))).toBe(false);
  }
});

it('成熟作品固定保存满月后取得的快照，发行期快照仍须补齐', () => {
  expect(publicationFresh(date('2026-09-20'), '2026-09-01', date('2026-10-13'))).toBe(false);
  expect(publicationFresh(date('2026-10-02'), '2026-09-01', date('2036-10-13'))).toBe(true);
});

it('浏览器重建分页评论时保留来源时间，失败不覆盖完整评论', () => {
  vi.useFakeTimers(); vi.setSystemTime(date('2026-10-13'));
  rememberReviews('EXM-001', page);
  rememberReviews('EXM-001', { ...page, ok: false, items: [] });
  rememberReviews('EXM-001', { ...page, cache_stale: true, items: [] });
  vi.setSystemTime(date('2036-10-13'));
  expect(cachedReviews('EXM-001', '2020-01-01')).toEqual({ pages: [page], pageParams: [1], saved: date('2026-10-13') });
  expect(cachedReviews('EXM-002', '2020-01-01')).toBeUndefined();
  expect(cachedReviews('EXM-001')).toBeUndefined();
});

it('资源刷新失败时保留成功缓存及原始更新时间', () => {
  vi.useFakeTimers(); vi.setSystemTime(date('2026-10-13'));
  const success = { state: 'ready' as const, items: [], error: '', checked_at: page.checked_at! };
  rememberResources('EXM-001', success);
  rememberResources('EXM-001', { ...success, warnings: ['来源未取得'] });
  rememberResources('EXM-001', { ...success, cache_stale: true });
  vi.setSystemTime(date('2036-10-13'));
  expect(cachedResources('EXM-001', '2020-01-01')?.result).toEqual(success);
});
