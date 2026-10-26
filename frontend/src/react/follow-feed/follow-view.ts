import { FOLLOW_FEED_SORTS, FOLLOW_FILTERS, FOLLOW_RANDOM_SORT, type FollowView } from './follow-feed';

/** 关注页 URL 是筛选的真相源；种子由地址、当前会话、掷种子依次提供。 */
export function readFollowView(search: string, previousSeed: number, rollSeed: () => number): FollowView {
  const params = new URLSearchParams(search);
  const csv = (key: string) => [...new Set((params.get(key) || '').split(',').filter(Boolean))];
  const seconds = (key: string) => { const value = Number(params.get(key)); return value > 0 ? value : 0 };
  const status = params.get('status');
  const sort = params.get('sort');
  return {
    status: FOLLOW_FILTERS.find(([key]) => key && key === status)?.[0] || '',
    media: params.get('media') === 'images' ? 'images' : 'videos',
    author: csv('author')[0] || '', provider: csv('provider')[0] || '', work: csv('work')[0] || '',
    tags: csv('tag'), durMin: seconds('dur_min'), durMax: seconds('dur_max'),
    sort: sort === FOLLOW_RANDOM_SORT ? sort : FOLLOW_FEED_SORTS.find(([key]) => key === sort)?.[0] || 'new',
    seed: Number(params.get('seed')) >>> 0 || previousSeed || rollSeed(),
    dir: params.get('dir') === 'asc' ? 'asc' : 'desc',
  };
}

export function followViewPath(view: FollowView): string {
  const params = new URLSearchParams();
  if (view.author) params.set('author', view.author);
  if (view.provider) params.set('provider', view.provider);
  if (view.tags.length) params.set('tag', view.tags.join(','));
  if (view.work) params.set('work', view.work);
  if (view.durMin) params.set('dur_min', String(view.durMin));
  if (view.durMax) params.set('dur_max', String(view.durMax));
  if (view.status) params.set('status', view.status);
  if (view.media === 'images') params.set('media', view.media);
  if (view.sort !== 'new') params.set('sort', view.sort);
  if (view.sort === FOLLOW_RANDOM_SORT) params.set('seed', String(view.seed));
  if (view.dir !== 'desc') params.set('dir', view.dir);
  return '/follow' + (params.size ? `?${params}` : '');
}

/** 索引跳转问的是这一位的全部更新；排序与取样仍属于当前关注会话。 */
export function followAuthorView(view: FollowView, author: string): FollowView {
  return { ...view, author, provider: '', work: '', tags: [], durMin: 0, durMax: 0, media: 'videos', status: '' };
}

/** 在线标签跳转保持当前题材，与关注页的来源筛选采用各自的地址语义。 */
export function followTagView(view: FollowView, tag: string): FollowView {
  return { ...view, author: '', provider: '', tags: [tag], durMin: 0, durMax: 0, media: 'videos', status: '' };
}
