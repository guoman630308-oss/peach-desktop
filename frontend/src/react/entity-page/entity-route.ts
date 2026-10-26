import { entityPath } from '@peach/legacy/core';
import { defaultSortDir, sortFromAddress } from '../../sort-preferences';
import { ENTITY_FILTER_KEYS, type EntityFilters, type EntityMedia } from './entity-page';

export const emptyEntityFilters = (): Record<string, string> => Object.fromEntries(
  ENTITY_FILTER_KEYS.map(key => [key, key === 'sort' ? 'new' : key === 'dir' ? 'desc' : '']));

/** 排序偏好只决定未显式给方向的地址；资料页缺省列仍是入库时间。 */
export function parseEntityFilters(search: string, preference: { sort: string; dir: string }): EntityFilters {
  const params = new URLSearchParams(search), filters = emptyEntityFilters();
  for (const key of ENTITY_FILTER_KEYS) if (key !== 'sort' && key !== 'dir') filters[key] = params.get(key) || '';
  return { ...filters, ...sortFromAddress(params.get('sort'), params.get('dir'), preference.sort, preference.dir, 'new') };
}

export function entityFilterSearch(filters: EntityFilters): string {
  const params = new URLSearchParams();
  for (const key of ENTITY_FILTER_KEYS) if (filters[key] && !(key === 'sort' && filters[key] === 'new')
    && !(key === 'dir' && filters[key] === defaultSortDir(filters.sort || ''))) params.set(key, filters[key]);
  return params.toString();
}

export function parseMediaView(search: string): EntityMedia {
  const params = new URLSearchParams(search), set = params.get('set') || '', media = params.get('media');
  return { media: media === 'photos' || media === 'online' ? media : 'videos',
    set: media === 'photos' && /^\d+$/.test(set) ? Number(set) : 0 };
}

export function entityViewSearch(filters: EntityFilters, media?: EntityMedia): string {
  const params = new URLSearchParams(entityFilterSearch(filters));
  if (media?.media === 'photos') {
    params.set('media', 'photos');
    if (media.set) params.set('set', String(media.set));
  } else if (media?.media === 'online') params.set('media', 'online');
  return params.toString();
}

export function entityViewPath(kind: string, name: string, filters: EntityFilters, media?: EntityMedia): string {
  const search = entityViewSearch(filters, media);
  return entityPath(kind, name) + (search ? `?${search}` : '');
}

/** 认领打开同一位时消费一次空筛选；深链与 POP 始终读地址。 */
export function createEntityEntryState() {
  let freshPath = '';
  return {
    fresh(kind: string, name: string): string { freshPath = entityPath(kind, name); return freshPath },
    read(kind: string, name: string, search: string, preference: { sort: string; dir: string }) {
      const fresh = freshPath === entityPath(kind, name) && !search;
      freshPath = '';
      const filters = fresh ? emptyEntityFilters() : { ...parseEntityFilters(search, preference) };
      if (kind === 'creator') filters.creator = '';
      return { filters, media: fresh ? { media: 'videos' as const, set: 0 } : parseMediaView(search) };
    },
  };
}
