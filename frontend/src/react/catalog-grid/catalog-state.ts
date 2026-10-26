import { cleanTagFilter, isCatalogPath, ROUTE_STATES, STATE_ROUTES } from '@peach/legacy/core';
import type { BarsContext, CatalogFilters } from '@peach/shell';

import { defaultSortDir, preferredDirection, sortFromAddress } from '../../sort-preferences';

export interface CatalogPreferences {
  defaultSort: string;
  defaultSortDirection?: string;
}

export interface CatalogAddress { pathname: string; search: string }

/** 目录请求的完整口径；随机种子留在内存，地址只表达可分享的筛选。 */
export interface CatalogState extends CatalogFilters {
  loc: string; creator: string; studio: string; owner: string;
  tag: string; tag_match: string; len: string; dur_min: string; dur_max: string;
  orient: string; region: string; state: string; sort: string; dir: string;
  seed: string; q: string; jav: string; thumb: string;
  browse: string; folder: string; folder_recursive: string;
}

const HOME_QUERY_KEYS = [
  'loc', 'creator', 'studio', 'owner', 'tag', 'tag_match', 'len', 'dur_min', 'dur_max',
  'orient', 'region', 'sort', 'dir', 'q', 'jav',
  'browse', 'folder', 'folder_recursive',
] as const;

export function resolveSort(rawSort: string | null, rawDir: string | null,
  preferences: CatalogPreferences, fallback = preferences.defaultSort): { sort: string; dir: string } {
  return sortFromAddress(rawSort, rawDir, preferences.defaultSort, preferences.defaultSortDirection, fallback);
}

/** 启动时只在目录地址读目录参数；来源范围同时适用于其它页面。 */
export function initialCatalogFilters(address: CatalogAddress, preferences: CatalogPreferences,
  seed: () => string): CatalogState {
  const pathname = decodeURIComponent(address.pathname);
  const params = new URLSearchParams(address.search);
  const catalog = isCatalogPath(pathname) || pathname === '/trash';
  const read = (key: string) => catalog ? params.get(key) : null;
  return {
    loc: params.get('loc') ?? 'local,115', creator: read('creator') || '', studio: read('studio') || '',
    owner: read('owner') === 'none' ? 'none' : '', tag: cleanTagFilter(read('tag')),
    tag_match: read('tag_match') === 'any' ? 'any' : 'all', len: read('len') || '',
    dur_min: read('dur_min') || '', dur_max: read('dur_max') || '', orient: read('orient') || '',
    region: read('region') || '', state: ROUTE_STATES[pathname] || read('state') || '',
    ...resolveSort(read('sort'), read('dir'), preferences), seed: read('seed') || seed(),
    q: read('q') || '', jav: read('jav') || '', thumb: read('thumb') || '0',
    browse: read('browse') === 'folders' || read('folder') ? 'folders' : '',
    folder: read('folder') || '', folder_recursive: read('folder_recursive') === '0' ? '0' : '',
  };
}

/** 全部来源离线时保留范围，空范围在 API 中表示整个馆藏。 */
export function onlineDefaultLoc(loc: string, online: Readonly<Record<string, boolean | undefined>>): string {
  return loc.split(',').filter(Boolean).filter(key => online[key] !== false).join(',') || loc;
}

/** 显式来源参数（包括空串）拥有最终决定权。 */
export function defaultCatalogLoc(loc: string, search: string,
  online: Readonly<Record<string, boolean | undefined>>): string {
  return new URLSearchParams(search).has('loc') ? loc : onlineDefaultLoc(loc, online);
}

export function homePath(filters: Readonly<CatalogFilters>): string {
  const path = STATE_ROUTES[filters.state ?? ''] || '/';
  const params = new URLSearchParams();
  for (const key of HOME_QUERY_KEYS) {
    const value = filters[key];
    if (value && !(key === 'tag_match' && value === 'all')
      && !(key === 'dir' && value === defaultSortDir(filters.sort ?? ''))) params.set(key, value);
  }
  if (!STATE_ROUTES[filters.state ?? ''] && filters.state) params.set('state', filters.state);
  return path + (params.size ? `?${params}` : '');
}

/** 回首页保留来源范围，其余口径使用浏览偏好与新种子。 */
export function resetCatalogFilters(previous: Readonly<CatalogFilters>, preferences: CatalogPreferences,
  seed: () => string): CatalogState {
  return {
    loc: previous.loc ?? '', creator: '', studio: '', owner: '', tag: '', tag_match: 'all',
    len: '', dur_min: '', dur_max: '', orient: '', region: '', state: '',
    sort: preferences.defaultSort,
    dir: preferredDirection(preferences.defaultSort, preferences.defaultSort, preferences.defaultSortDirection),
    seed: seed(), q: '', jav: '', thumb: '0',
    browse: '', folder: '', folder_recursive: '',
  };
}

/** 一次写入让首页筛选条与请求读取同一个对象。 */
export function homeCatalogState<T extends CatalogFilters>(state: T): {
  state: T; barsContext: BarsContext; detailReturnBarsContext: null;
} {
  return { state, barsContext: { type: 'home', filters: state }, detailReturnBarsContext: null };
}
