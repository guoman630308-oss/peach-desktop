/* 资料页在线视图的取数与筛选（ADR-0096）：状态、来源、标签、排序与视频／图片，判定同关注页那一页。
 *
 * 筛选只活在这一页里、不进地址栏：地址上的 `media=online` 只说「在看在线」。取数在资料页这一层，
 * 浮层（读数、两排控件）与正文那一格读同一份。来源与标签取 `author_facets`：全库口径的 `facets`
 * 摆到一位创作者页上会混进别人的。 */
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { tagLabel } from '@peach/legacy/tags';

import type { EntityFilterActions, EntityOnlineHead, SegmentOption } from '../entity-filter/entity-filter';
import {
  FOLLOW_CREDENTIALS_KEY, FOLLOW_FEED_SORTS, FOLLOW_FILTERS, FOLLOW_RANDOM_SORT, FOLLOW_TAGS_FIRST,
  entityFollowView, fetchFollowCredentials, followFeedQuery, groupMediaKinds, groupTagType, mergedPage, nextSort,
  sortAriaLabel, type FollowContext, type FollowMedia, type FollowView,
} from '../follow-feed/follow-feed';

type OnlineActions = Pick<EntityFilterActions,
  'onlineMedia' | 'onlineShuffle' | 'onlineImagesOnly' | 'onlineStatus' | 'onlineProvider' | 'onlineTag' | 'onlineSort'>;

const newSeed = () => ((Date.now() ^ (Math.random() * 1e9 | 0)) >>> 0) % 99991;

export function useEntityOnline(author: string, active: boolean, wall: {
  photoLayout: string; photoLayouts: readonly SegmentOption[]; imagesOnly: boolean; setImagesOnly(on: boolean): void;
}) {
  const [view, setView] = useState<FollowView>(() => entityFollowView(author));
  /* 换了一位创作者（同一个页面实例换名字）时筛选从头来。 */
  if (view.author !== author) setView(entityFollowView(author));
  /* 视频还是图片：没点过时按第一屏哪一档的组多定下来（只有图片的创作者进来就是照片墙），定了不再随筛选跳。 */
  const [selection, setSelection] = useState<{ author: string; media: FollowMedia | '' }>({ author, media: '' });
  if (selection.author !== author) setSelection({ author, media: '' });
  const picked = selection.author === author ? selection.media : '';
  const setPicked = (media: FollowMedia) => setSelection({ author, media });
  const query = followFeedQuery(view, 0);
  const result = useInfiniteQuery({ ...query, enabled: active && !!author });
  const credentials = useQuery({
    queryKey: FOLLOW_CREDENTIALS_KEY, queryFn: ({ signal }) => fetchFollowCredentials(signal), enabled: active,
  });
  const data = useMemo(() => mergedPage(result.data), [result.data]);
  const pending = result.isPending || result.isPlaceholderData;
  const counts = useMemo(() => {
    const tally = { videos: 0, images: 0 };
    (data?.groups || []).forEach((group) => groupMediaKinds(group)
      .forEach((kind) => { tally[kind === 'image' ? 'images' : 'videos'] += 1 }));
    return tally;
  }, [data]);
  const auto: FollowMedia = counts.images > counts.videos ? 'images' : 'videos';
  if (!picked && data && !pending) setPicked(auto);
  const media = picked || auto;
  const visible = useMemo(() => (data?.groups || [])
    .filter((group) => groupMediaKinds(group).has(media === 'images' ? 'image' : 'video')), [data, media]);
  const context = useMemo<FollowContext>(() => ({
    sources: data?.sources || [], aliases: data?.author_aliases || [],
    credentials: new Set((credentials.data?.providers || [])
      .filter((provider) => provider.present).map((provider) => provider.provider)),
  }), [data, credentials.data]);

  const facets = data?.author_facets || {};
  const labels = new Map((data?.sources || []).map((source) => [source.provider, source.provider_label || source.provider]));
  const tagRows = (facets.tags || []).slice(0, FOLLOW_TAGS_FIRST);
  view.tags.forEach((tag) => { if (!tagRows.some(([key]) => key === tag)) tagRows.push([tag, 0]) });
  const head: EntityOnlineHead = {
    media,
    mediaCounts: counts.images || media === 'images' ? counts : null,
    photoLayout: wall.photoLayout, photoLayouts: wall.photoLayouts, imagesOnly: wall.imagesOnly,
    status: view.status, statuses: FOLLOW_FILTERS,
    provider: view.provider,
    providers: (facets.providers || []).map((key) => [key, labels.get(key) || key] as const),
    tags: tagRows.map(([k, n]) => ({ k, label: tagLabel(k), n, selected: view.tags.includes(k),
      cat: groupTagType(data?.groups || [], k) })),
    sorts: FOLLOW_FEED_SORTS.map(([key, label]) => ({
      key, label, pressed: view.sort === key, dir: view.sort === key ? view.dir : '',
      ariaLabel: sortAriaLabel(key, label, view.sort, view.dir) })),
  };
  const statusCounts = data?.counts || {};
  const total = view.status ? statusCounts[view.status] || 0
    : Object.values(statusCounts).reduce((sum, n) => sum + (Number(n) || 0), 0);

  const patch = (next: Partial<FollowView>) => setView((current) => ({ ...current, ...next }));
  const actions: OnlineActions = {
    onlineMedia: (next) => setPicked(next),
    onlineShuffle: () => patch({ sort: FOLLOW_RANDOM_SORT, seed: newSeed() }),
    onlineImagesOnly: (on) => wall.setImagesOnly(on),
    onlineStatus: (status) => patch({ status: status as FollowView['status'] }),
    onlineProvider: (key) => patch({ provider: view.provider === key ? '' : key }),
    onlineTag: (tag) => patch({ tags: view.tags.includes(tag) ? view.tags.filter((key) => key !== tag) : [...view.tags, tag] }),
    onlineSort: (key) => {
      const next = nextSort(key, view.sort, view.dir);
      if (next) patch(next);
    },
  };
  return {
    view, media, result, data, visible, context, head, actions, busy: active && pending,
    /** 读数：这一档状态下的条目数，同关注页的口径（`counts` 随来源、标签筛选变）。 */
    total: data ? total : null,
    /** 正文那一格的键：换筛选换一份骨架。媒体不进键，换视频／图片不重取。 */
    key: query.queryKey.join('\u0000'),
  };
}
