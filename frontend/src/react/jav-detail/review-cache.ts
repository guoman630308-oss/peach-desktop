/** 评论分页快照保留来源时间，跨浏览器重启复用，失败不覆盖成功内容。 */
import { publicationFresh } from '../publication-cache';
import type { JavReviews } from './jav-detail';

const KEY = 'peach.jav-reviews.v1';
type Entry = { code: string; saved: number; result: JavReviews };
function entries(): Entry[] {
  try {
    const text = localStorage.getItem(KEY) || '[]';
    if (text.length > 2_000_000) return [];
    const rows = JSON.parse(text);
    return Array.isArray(rows) ? rows.filter(row => typeof row?.code === 'string' && Number.isFinite(row.saved)
      && row.result?.ok === true && !row.result.cache_stale && Number.isInteger(row.result.page)
      && row.result.page >= 1 && row.result.page <= 8 && Array.isArray(row.result.items)
      && row.result.items.length <= 10 && row.result.items.every((item: Record<string, unknown>) =>
        typeof item?.id === 'string' && typeof item.content === 'string' && typeof item.username === 'string'
        && typeof item.created_at === 'string')) : [];
  } catch { return [] }
}
export function rememberReviews(code: string, result: JavReviews) {
  if (!result.ok || result.cache_stale) return;
  try {
    const checked = Date.parse(result.checked_at || '');
    const row = { code, saved: Number.isFinite(checked) ? checked : Date.now(), result };
    const rows = [row, ...entries().filter(entry => entry.code !== code || entry.result.page !== result.page)].slice(0, 128);
    const text = JSON.stringify(rows);
    if (text.length <= 2_000_000) localStorage.setItem(KEY, text);
  } catch { /* 存储不可用时评论仍由查询内存保存。 */ }
}
export function cachedReviews(code: string, released?: string) {
  const rows = entries();
  const pages: JavReviews[] = [];
  let saved = Infinity;
  for (let page = 1; page <= 8; page++) {
    const row = rows.find(entry => entry.code === code && entry.result.page === page);
    if (!row || !publicationFresh(row.saved, released)) break;
    pages.push(row.result);
    saved = Math.min(saved, row.saved);
    if (!row.result.more) break;
  }
  return pages.length ? { pages, pageParams: pages.map(page => page.page), saved } : undefined;
}
export function reviewsFresh(pages: JavReviews[], updated: number, released?: string) {
  return pages.length > 0 && pages.every(page => page.ok && !page.cache_stale &&
    publicationFresh(Date.parse(page.checked_at || '') || updated, released));
}
