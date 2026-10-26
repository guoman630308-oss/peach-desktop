import { peachHistory } from '@peach/history';
import { isCatalogPath, ROUTE_STATES } from '@peach/legacy/core';
import { entityLinkName } from '../../core/entity-links';
import { WALL_FILTER_KEYS } from './wall-layout';

export function wallAddress(from = `${location.pathname}${location.search}`, filters: Readonly<Record<string, string>> = {}): string {
  const source = new URL(from, location.origin);
  const [section, ...parts] = source.pathname.split('/').filter(Boolean);
  const kind = ({ performers: 'performer', creators: 'creator', studios: 'studio', series: 'series', tags: 'tag' } as Record<string, string>)[section || ''];
  const scoped = (isCatalogPath(source.pathname) && source.pathname !== '/junk-files') || source.pathname === '/jav' || !!(kind && parts.length);
  const params = new URLSearchParams(scoped ? source.search : '');
  for (const [key, value] of Object.entries(filters)) if (value && (scoped || ['jav','loc'].includes(key))) params.set(key, value);
  if (scoped && ROUTE_STATES[source.pathname]) params.set('state', ROUTE_STATES[source.pathname]!);
  if (kind && parts.length) {
    const identity = decodeURIComponent(parts.join('/'));
    const name = /^[0-9]+$/.test(identity) ? entityLinkName(kind, Number(identity)) : identity;
    if (name && kind === 'tag') {
      params.set('tag', [...new Set([name, ...(params.get('tag') || '').split(',').filter(Boolean)])].join(','));
      params.set('tag_match', 'all');
    } else if (name && !params.has(kind)) params.set(kind, name);
  }
  params.set('returnTo', `${source.pathname}${source.search}`);
  return `/wall?${params}`;
}

export function unfilteredWallAddress(from = `${location.pathname}${location.search}`): string {
  const params = new URL(from, location.origin).searchParams;
  for (const key of WALL_FILTER_KEYS) params.delete(key);
  params.delete('tag_match');
  return `/wall${params.size ? `?${params}` : ''}`;
}

export function exitWall(): void {
  const target = new URLSearchParams(peachHistory.navigation.location.search).get('returnTo') || '/';
  peachHistory.replace(target.startsWith('/') && !target.startsWith('//') && !target.startsWith('/wall') ? target : '/');
}
