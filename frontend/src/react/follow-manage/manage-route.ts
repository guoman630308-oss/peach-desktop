export const FOLLOW_MANAGE_TABS = ['list', 'add', 'feeds'] as const;
export type FollowManageTab = (typeof FOLLOW_MANAGE_TABS)[number];
export interface FollowManageRoute { tab: FollowManageTab; page: number; sort: string; dir: string }

export function followManageParams(search: string): FollowManageRoute {
  const params = new URLSearchParams(search), tab = params.get('tab');
  return { tab: FOLLOW_MANAGE_TABS.find(key => key === tab) || 'list',
    page: Math.max(1, Math.floor(Number(params.get('page'))) || 1),
    sort: params.get('sort') || '', dir: params.get('dir') || '' };
}

export function followManagePath(params: FollowManageRoute): string {
  const search = new URLSearchParams();
  if (params.tab !== 'list') search.set('tab', params.tab);
  if (params.page > 1) search.set('page', String(params.page));
  if (params.sort) search.set('sort', params.sort);
  if (params.dir) search.set('dir', params.dir);
  return '/subscriptions' + (search.size ? `?${search}` : '');
}

export function followManageEntry(search: string, workspace?: FollowManageTab): string {
  if (!workspace && new URLSearchParams(search).get('tab') === 'wants') return '/jav?tab=wants';
  if (!workspace && new URLSearchParams(search).get('tab') === 'source') return '/settings/library?section=' + encodeURIComponent('来源与订阅');
  return followManagePath({ tab: workspace || followManageParams(search).tab, page: 1, sort: '', dir: '' });
}
