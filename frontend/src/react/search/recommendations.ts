import { authorAvatarHtml, followIdentity, followTitle } from '../follow-feed/follow-marks';
import { groupMediaKinds, itemForMedia, type FollowPage } from '../follow-feed/follow-feed';
import type { Discovery } from '../jav/jav';
import type { SuggestCard, SuggestGroup, SuggestItem } from './search';

type Request = (url: string) => Promise<any>;

/** 搜索入口复用馆藏、口味推荐、关注和新作的本机读接口。 */
export async function searchRecommendations(request: Request): Promise<SuggestGroup[]> {
  const results = await Promise.allSettled([
    request('/api/items?sort=rand&limit=5'),
    request('/api/jav/discoveries?scope=library&limit=5'),
    request(`/api/follow?limit=20&sort=rand&seed=${Date.now() >>> 0}`),
    request('/api/feeds/discoveries?limit=5'),
    request('/api/follow/authors?limit=3'),
  ]);
  const data = results.map(result => result.status === 'fulfilled' ? result.value : null);
  const library: SuggestCard[] = (data[0]?.items || []).slice(0, 5);
  const jav: Discovery[] = (data[1]?.items || []).slice(0, 5);
  const follow: FollowPage | null = data[2];
  const releases: Discovery[] = data[3]?.items || [];
  const work = (card: SuggestCard, href?: string): SuggestItem => ({
    value: String(card.code || card.display_title || card.title || card.name || '未命名作品'),
    id: card.id, code: card.code, title: String(card.display_title || card.title || ''),
    who: String(card.performers || card.creator || ''), card, href,
  });
  const posts: SuggestItem[] = [];
  const photos: SuggestItem[] = [];
  const context = { sources: follow?.sources || [], aliases: follow?.author_aliases || [], credentials: new Set<string>() };
  for (const group of follow?.groups || []) {
    const kinds = groupMediaKinds(group);
    for (const media of ['videos', 'images'] as const) {
      if (media === 'images' ? !kinds.has('image') : kinds.size && !kinds.has('video')) continue;
      const item = itemForMedia(group, media);
      const source = context.sources.find(source => source.id === item.source_id);
      const sources = source?.author_key ? context.sources.filter(row => row.author_key === source.author_key) : source ? [source] : [];
      const identity = followIdentity(item, sources, context);
      const image = media === 'images' ? item.media_items?.find(entry => entry.media_kind === 'image') : undefined;
      (media === 'images' ? photos : posts).push({ value: followTitle(item), id: item.id, who: identity.author,
        href: `/posts/${item.id}`, followMedia: media, mediaIndex: image?.index ?? null,
        card: { id: item.id, thumb_url: image?.thumb_url || item.thumb_url },
      });
    }
  }
  const authors: SuggestItem[] = (data[4]?.items || []).map((row: { k: string; key: string; entity_id?: number }) => ({
    value: row.k, href: row.entity_id ? `/creators/${row.entity_id}` : `/follow?author=${encodeURIComponent(row.key)}`,
    avatarHtml: authorAvatarHtml(context.sources.filter(source => source.author_key === row.key), row.k),
    subtitle: '关注中的作者',
  }));
  const people = new Map<string, SuggestItem>();
  for (const card of jav) {
    for (const ref of card.performer_refs || []) {
      if (!ref.id || people.has(ref.name)) continue;
      people.set(ref.name, { value: ref.name, entity_id: ref.id, has_image: ref.has_image,
        href: `/performers/${ref.id}`,
        image_version: ref.image_version, avatar_focus: ref.avatar_focus, rep: ref.has_avatar ? ref.rep : null,
        works: [card], subtitle: '查看作品与订阅新作',
      });
    }
  }
  return [
    { kind: 'performer', label: '艺人与新作', items: [...people.values()].slice(0, 3), href: '/subscriptions?tab=feeds', action: '订阅新作' },
    { kind: 'author', label: '关注作者', items: authors, href: '/subscriptions?tab=add', action: '添加关注' },
    { kind: 'asset', label: '作品推荐', items: library.map(card => work(card)), href: '/', action: '全部作品' },
    { kind: 'jav', label: 'JAV 推荐', items: jav.map(card => work(card, `/assets/${card.asset_id || card.id}`)), href: '/jav?tab=discover', action: '发现 JAV' },
    { kind: 'post', label: '关注作品', items: posts.slice(0, 5), href: '/follow', action: '全部关注作品' },
    { kind: 'photo', label: '关注图片', items: photos.slice(0, 5), href: '/follow?media=images', action: '全部关注图片' },
    { kind: 'release', label: '订阅新作', items: releases.filter(card => card.code).map(card => work({ ...card, sourceWork: true }, `/works/${encodeURIComponent(card.code)}`)), href: '/subscriptions?tab=feeds', action: '管理订阅' },
  ];
}
