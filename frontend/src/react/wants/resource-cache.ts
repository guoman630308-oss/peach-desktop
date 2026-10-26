/* 资源查询按作品发行日缓存；浏览器重启后也可读取，最多保留 128 部。 */
import type { MagnetResult } from './want-magnets';
import type { Want } from './wants';
import { publicationFresh, publicationLifetime } from '../publication-cache';

const KEY = 'peach.want-resources.v1';
type Entry = { code: string; saved: number; result: MagnetResult };

export const resourceLifetime = publicationLifetime;
function complete(result: MagnetResult) {
  return !result.cache_stale && (result.complete === true || !result.warnings?.length);
}
export function resourcesFresh(result: MagnetResult | undefined, saved: number, released?: string | null) {
  return !!result && !result.cache_stale && (result.state === 'error' || !complete(result)
    ? Date.now() - saved < 60_000 : publicationFresh(saved, released));
}

function entries(): Entry[] {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(value) ? value.filter((row) => typeof row?.code === 'string' &&
      Number.isFinite(row.saved) && row.result?.state === 'ready' && Array.isArray(row.result.items) &&
      complete(row.result) &&
      row.result.items.every((item: Record<string, unknown>) =>
        item && ['magnet', 'ed2k', 'url'].includes(String(item.protocol)) &&
        typeof item.size === 'string' && Array.isArray(item.attributes))) : [];
  } catch { return [] }
}

export function cachedResources(code: string, released: string | null | undefined) {
  const found = entries().find((row) => row.code === code);
  return found && publicationFresh(found.saved, released) ? found : undefined;
}

export function cachedMagnetStates(wants: readonly Pick<Want, 'code' | 'release_date'>[]) {
  const records = new Map(entries().map(row => [row.code, row]));
  const states = new Map<string, boolean>();
  for (const want of wants) {
    if (!want.code) continue;
    const row = records.get(`all-sources:${want.code}`);
    if (row && publicationFresh(row.saved, want.release_date))
      states.set(want.code, row.result.items.some(item => item.protocol === 'magnet'));
  }
  return states;
}

export function rememberResources(code: string, result: MagnetResult) {
  if (result.state !== 'ready' || !complete(result)) return;
  try {
    const rows = entries().filter((row) => row.code !== code);
    const checked = Date.parse(result.checked_at || '');
    localStorage.setItem(KEY, JSON.stringify([{ code, saved: Number.isFinite(checked) ? checked : Date.now(), result }, ...rows].slice(0, 128)));
  } catch { /* 存储不可用时仍保留 TanStack Query 的内存缓存。 */ }
}
