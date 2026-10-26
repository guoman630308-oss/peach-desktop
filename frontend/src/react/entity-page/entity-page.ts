/* 实体资料页（路由树画进 `#index`，见 `router/managed-routes.tsx` 的 `ENTITY_ROUTES`）：数据形状、
 * 查询键与首屏取数。
 *
 * 资料卡、筛选浮层、新作那一行与正文是同一页的四块，读的是同一份页内状态（这一页的筛选、当前
 * 视图、照片墙翻到哪儿），所以是同一个组件；四块各自的宿主由壳在 `#index` 里排好，页面画进资料卡
 * 那一格，再用 portal 画进另外三块。取数全在这里：`/api/entity`、新作、作品与照片由页面按查询键取，
 * 地址栏是筛选与视图的唯一真相源、归壳写——页面改筛选只调 `actions.route`，壳写好地址再经
 * `updateManagedRoute` 把新的 `filters`／`media` 推回来，页面按新键重取。
 *
 * 首屏仍由壳铺骨架（深链冷启动时 React 包还没到，骨架只能由壳画），`prefetch` 把四样取齐再画，
 * 骨架与整页一次换掉；页内换筛选时作品区的骨架归正文那一格自己（`useSkeletonReveal`）。 */
import { api } from '@peach/legacy/core';

import type { CatalogGridProps, MediaPage } from '../catalog-grid/types';
import type { EntityBodyActions, EntityBodyHelpers, EntityCodeSet, EntityLocalPhoto, PhotoLayout, PhotoSize } from '../entity-body/entity-body';
import type { EntityComboItem, EntitySortKey, EntityView, SegmentOption } from '../entity-filter/entity-filter';
import type { FollowFeedActions, FollowFeedHelpers } from '../follow-feed/follow-feed';
import type { EntityHeroData, HeroCostar } from '../entity-hero/entity-hero';
import { feedNewKey, feedNewOptions, type FeedRowHtml } from '../feed-new/feed-new';
import type { IndexPerson, PeopleLayout } from '../index/index-data';
import { queryClient } from '../query';

/** 地址栏上这一页的筛选，键同壳的 `ENTITY_FILTER_KEYS`。 */
export const ENTITY_FILTER_KEYS = ['loc', 'creator', 'tag', 'state', 'dur_min', 'dur_max', 'orient', 'sort', 'dir'] as const;
export type EntityFilters = Readonly<Record<string, string>>;

/** 地址栏上的媒体视图：`media=photos`、`media=online` 与目录图集 `set=<id>`。名册不进地址栏，见 `rosterView`。 */
export interface EntityMedia { media: 'videos' | 'photos' | 'online'; set: number }
export const EMPTY_MEDIA: EntityMedia = { media: 'videos', set: 0 };

/** `/api/entity` 里这一页读到的字段：资料卡那份，外加标签计数与名册。名字对不上时回 `{error}`。 */
export interface EntityPageData extends EntityHeroData {
  error?: string;
  redirect?: { kind: string; name: string; id?: number };
  tags?: { k: string; n: number }[];
  related_performers?: (HeroCostar & IndexPerson)[];
  labels?: IndexPerson[];
  has_image?: boolean;
  image_version?: string;
  has_avatar?: boolean;
  avatar_stand_in?: boolean;
  has_logo?: boolean;
  representative_asset_id?: number | null;
  avatar_focus?: unknown;
  mark_link_id?: number | null;
}

/** `/api/photos`、`/api/photo-set` 的一页。`sets` 里 `kind` 为 `code` 的是名下作品的官方样张。 */
export interface PhotoPage {
  error?: string;
  id?: number | string;
  title?: string;
  total?: number;
  sample_total?: number;
  sets?: (EntityCodeSet & { kind?: string })[];
  items?: EntityLocalPhoto[];
  has_more?: boolean;
  seed?: string;
}

/** 写操作与站内跳转里仍归壳的那几样：地址、确认弹层的表单、全站回执与筛选的唯一落点。 */
export interface EntityPageActions {
  /** 换视图、换观看状态：壳写好地址再把新的 `filters`／`media` 推回来。 */
  route(filters: EntityFilters, media: EntityMedia): void;
  /** 标签、交集条上的一颗与「全部清除」：走壳里筛选的唯一落点（`commitContextFilter`）。 */
  toggleTag(tag: string): void;
  clearFilter(key: string): void;
  clearAll(): void;
  /** 排序键：点未选中项换列，点选中项翻方向，判据同首页那一排。 */
  setSort(key: string): void;
  /** 作品视图的换一批：种子是全站那一粒（`state.seed`），壳换好、写好地址再推回来；回的是新种子，
   *  页面据此先把那一页取到手，键上的转圈等的就是这一趟。 */
  reshuffleVideos(): string;
  setJavLayout(layout: string): void;
  setPhotoLayout(layout: string): void;
  /** 站内跳转，走遗留路由的同一个入口。 */
  openEntity(kind: string, name: string, replace?: boolean): void;
  /** 这一页是不是 JAV 语境（按第一页作品的 `is_jav` 推）：壳的排序项、侧栏取数与卡片版式都读它。 */
  javContext(on: boolean): void;
  /** 换了视图或换了一批内容：壳重算吸顶，顶栏的大小图键按是不是照片墙（`wall`：照片视图，或在线
   *  视图摆着图片）换成对应的那一组。 */
  painted(view: EntityView, wall: boolean): void;
  /** 名字对不上任何一位（`/api/entity` 回 `{error}`）：壳把整块换成空态。 */
  missing(): void;
  /** 换过头像：壳丢掉自己缓存着的那几排头像（顶部三条），下次画时按新版本号重取。 */
  avatarChanged(): void;
  /** 去关注页，只看这一位名下来源的更新（名册在线档那一格点开的同一个入口）。 */
  openFollowAuthor(key: string): void;
}

/** 仍由遗留层拼的 HTML 与接线。 */
export interface EntityPageHelpers {
  /** 大位那张图（`entityFaceImg`）：取不到就是空串，首字母垫底。 */
  portraitImg(kind: string, entity: EntityPageData): string;
  /** 横滚行接上拖动与滚轮（`wireDrag`）。 */
  wireDrag(row: Element | null): void;
  /** 只接滚轮与两端渐隐（`wireHorizontalScroller`）。 */
  wireScroller(row: Element | null): void;
  /** 新作那一行：拖动、滚轮，按设置接自动滚动。 */
  wireFeedRow(row: Element | null): void;
  /** 新作那一行的整段 HTML：连不上图片主机时的 Note 与一排卡（`feedNewCardHtml`）。 */
  feedRowHtml: FeedRowHtml;
  /** 操作回执（`actionReceipt`），给了 `undo` 就带一颗撤销键。 */
  receipt(message: string, options?: { undo?: () => Promise<void> }): void;
  /** 失败回执（`actionFailure`）。 */
  failure(label: string, error: unknown): void;
  /** 「添加别名」弹层：表单与回执在壳里，写回交给 `write`（页面的 mutation，成功后重取资料与作品）。 */
  aliasForm(mine: string[], write: (payload: { alias: string; remove?: boolean }) => Promise<{ added?: boolean; alias?: string }>): Promise<void>;
  /** 图集那两枚源文件键；`done` 在对账改动了这一组之后重取它。 */
  sourceToolsHtml(setId: number): string;
  wireSourceTools(root: Element, done: () => void): void;
  /** 交集条上的那几颗，清单同首页那条（`comboItems`）。 */
  comboItems(filters: EntityFilters): EntityComboItem[];
  /** 排序键：JAV 语境下多一枚发行时间。 */
  sortKeys(sort: string, dir: string, jav: boolean): EntitySortKey[];
}

/** 卡片网格原样要的那几样，壳里同一份：版式、选择状态与展示设置由 `updateManagedRoute` 推最新值。 */
type SharedGridProps = Pick<CatalogGridProps,
  'layout' | 'selectMode' | 'selected' | 'seekSeconds' | 'wireDrag' | 'skeletonHtml'
  | 'groupCollapse' | 'canLoadMore'>;

/** 壳在 `#index` 里排好的四块宿主（页面画进资料卡那一格）。筛选浮层与正文各带一层
 *  `.peach-react`，新作那一行是遗留层的卡片，宿主不在 React 子树的样式范围里。 */
export interface EntityPageHosts { filter: Element; feed: HTMLElement; body: Element }

export interface EntityPageProps extends SharedGridProps {
  kind: string;
  resolvedEntity?: EntityPageData;
  /** 路由通过实体 ID 取得的规范名。 */
  name: string;
  filters: EntityFilters;
  media: EntityMedia;
  /** 全站的 JAV 开关（`state.jav==='1'`）：资料页继承它。 */
  jav: boolean;
  /** 全站那一粒随机种子（`state.seed`），排序为 `seed` 时随作品请求带上。 */
  seed: string;
  /** 批量操作后要求重读作品的代次：加一就换键重取。 */
  revision: number;
  /** 合集开关改了要求重取新作的代次。 */
  feedRevision: number;
  photoSize: PhotoSize;
  photoLayout: PhotoLayout;
  /** 关注图片墙的「仅显示图片」（`followImagesOnly`），在线视图与关注页共用这一个设置。 */
  followImagesOnly: boolean;
  /** JAV 卡片版式的现值与可选项。 */
  javLayout: string;
  javLayouts: readonly SegmentOption[];
  photoLayouts: readonly SegmentOption[];
  /** 四枚观看状态，清单同首页那一排（`VIEW_PILLS`）。 */
  states: readonly { k: string; label: string }[];
  /** 名册一格的版式，读的是索引页同一个设置值。 */
  peopleLayout: PeopleLayout;
  hosts: EntityPageHosts;
  /** 卡片上的助手与动作就是目录那一份；照片墙多一枚定位源文件。 */
  card: { helpers: EntityBodyHelpers; actions: Omit<EntityBodyActions, 'loadMorePhotos'> };
  /** 在线视图里的关注卡：骨架、拖动与进度同关注页那一份；动作是资料页语境的一份，换筛选、侧栏、
   *  多选与图片偏好都不接（这一页不画关注页的侧栏，多选认的是本地作品）。 */
  follow: { helpers: FollowFeedHelpers; actions: FollowFeedActions };
  helpers: EntityPageHelpers;
  actions: EntityPageActions;
}

export const entityKey = (kind: string, name: string) => ['entity', kind, name] as const;
export const entityItemsKey = (kind: string, name: string, query: string, revision: number) =>
  ['entity-items', kind, name, query, revision] as const;
export const entityPhotosKey = (kind: string, name: string, set: number, seed: string) =>
  ['entity-photos', kind, name, set, seed] as const;

const tagList = (value: string | undefined) => String(value || '').split(',').filter(Boolean);
export const tagPressed = (value: string | undefined, tag: string) => tagList(value).includes(tag);
export { tagList };

/** 作品请求的参数，第一页与续页同一套口径（`offset` 之外）。资料页继承 JAV 开关：女优页和厂牌页
 *  同样是按番号浏览的语境。 */
export function itemsParams(kind: string, name: string, filters: EntityFilters, opts: { jav: boolean; seed: string },
  offset = 0): URLSearchParams {
  const p = new URLSearchParams();
  p.set(kind, name);
  if (kind === 'tag') {
    p.set('tag', [...new Set([name, ...tagList(filters.tag)])].join(','));
    p.set('tag_match', 'all');
  }
  p.set('limit', '48');
  p.set('offset', String(offset));
  p.set('sort', filters.sort || 'new');
  if (filters.dir) p.set('dir', filters.dir);
  if (filters.sort === 'seed') p.set('seed', opts.seed);
  if (offset) p.set('count', '0');
  for (const key of ENTITY_FILTER_KEYS) {
    if (filters[key] && key !== kind && key !== 'sort') p.set(key, filters[key]!);
  }
  if (opts.jav) p.set('jav', '1');
  return p;
}

export async function fetchItems(kind: string, name: string, filters: EntityFilters, opts: { jav: boolean; seed: string },
  offset: number, signal?: AbortSignal, options?: { limit: number; sort?: string; dir?: string }): Promise<MediaPage> {
  const params = itemsParams(kind, name, filters, opts, offset);
  if (options) {
    params.set('limit', String(options.limit));
    params.delete('count');
    if (options.sort) params.set('sort', options.sort);
    if (options.dir) params.set('dir', options.dir);
  }
  return await api(`/api/items?${params}`, signal ? { signal } : {}) as MediaPage;
}

/** 同一页里换回看过的那一份筛选不再请求：每次进页先把这一位名下的缓存清掉（`prefetch`），页内
 *  一份筛选只取一次；批量操作后换代次重取。 */
export const itemsOptions = (props: Pick<EntityPageProps, 'kind' | 'name' | 'filters' | 'jav' | 'seed' | 'revision'>) => {
  const opts = { jav: props.jav, seed: props.seed };
  const query = itemsParams(props.kind, props.name, props.filters, opts).toString();
  return {
    queryKey: entityItemsKey(props.kind, props.name, query, props.revision),
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchItems(props.kind, props.name, props.filters, opts, 0, signal),
    staleTime: Infinity,
  };
};

export const entityOptions = (kind: string, name: string) => ({
  queryKey: entityKey(kind, name),
  queryFn: ({ signal }: { signal: AbortSignal }) =>
    api(`/api/entity?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}`, { signal }) as Promise<EntityPageData>,
  staleTime: Infinity,
});

/** 照片一页的地址。整组照片第一页沿用壳里的口径（不带 limit，换一批才带种子）；图集与续页每页 120。 */
function photoUrl(kind: string, name: string, set: number, seed: string, offset: number, pageSeed: string) {
  const who = `kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}`;
  if (offset) {
    const tail = `&limit=120&offset=${offset}${pageSeed ? `&seed=${encodeURIComponent(pageSeed)}` : ''}`;
    return set ? `/api/photo-set?id=${set}${tail}` : `/api/photos?${who}${tail}`;
  }
  const shuffled = seed ? `&seed=${encodeURIComponent(seed)}` : '';
  if (set) return `/api/photo-set?id=${set}&limit=120${shuffled}`;
  return seed ? `/api/photos?${who}&limit=120${shuffled}` : `/api/photos?${who}`;
}

type PhotoParam = { offset: number; seed: string } | null;

/** 照片墙：一屏是一组无限查询，翻页只数本地图片（样张一次铺完）。换一批换一粒种子，就是换一个键。
 *  取不到（`{error}`）时这一页当作没有照片，不让整页停在骨架上。 */
export const photosOptions = (kind: string, name: string, set: number, seed: string) => ({
  queryKey: entityPhotosKey(kind, name, set, seed),
  queryFn: async ({ pageParam }: { pageParam: PhotoParam }): Promise<PhotoPage> => {
    const page = await (api(photoUrl(kind, name, set, seed, pageParam?.offset || 0, pageParam?.seed || '')) as Promise<PhotoPage>)
      .catch((error: unknown) => {
        if (pageParam) throw error;
        return { error: String(error) } as PhotoPage;
      });
    if (pageParam && page.error) throw new Error(page.error);
    return page;
  },
  initialPageParam: null as PhotoParam,
  getNextPageParam: (last: PhotoPage, pages: PhotoPage[]): PhotoParam | undefined => (last.has_more && !last.error
    ? { offset: pages.reduce((sum, page) => sum + (page.items || []).length, 0), seed: pages[0]?.seed || '' }
    : undefined),
  staleTime: Infinity,
});

export const codeSetsOf = (page: PhotoPage | null | undefined): EntityCodeSet[] =>
  page && !page.error ? (page.sets || []).filter((set) => set.kind === 'code') : [];

/** 每次进这一页都是新的一趟：先清掉这一位名下的缓存，再把资料（连同新作）、作品第一页与照片
 *  并行取齐，深链落在一个图集上时连那一组一起取。四样齐了路由树（`openManagedRoute`）一次换掉骨架。 */
export async function prefetchEntityPage(props: EntityPageProps): Promise<void> {
  const { kind, name } = props;
  for (const head of ['entity', 'entity-items', 'entity-photos'] as const) {
    queryClient.removeQueries({ queryKey: [head, kind, name] });
  }
  if (props.resolvedEntity?.id) queryClient.setQueryData(entityKey(kind, name), props.resolvedEntity);
  await Promise.all([
    queryClient.fetchQuery(entityOptions(kind, name)).then((entity) => {
      if (!entity || entity.error || !entity.id) return null;
      queryClient.removeQueries({ queryKey: feedNewKey(Number(entity.id)) });
      return queryClient.fetchQuery(feedNewOptions(Number(entity.id), props.helpers.feedRowHtml, true));
    }),
    queryClient.fetchQuery(itemsOptions(props)),
    queryClient.fetchInfiniteQuery(photosOptions(kind, name, 0, '')),
    props.media.media === 'photos' && props.media.set
      ? queryClient.fetchInfiniteQuery(photosOptions(kind, name, props.media.set, '')) : null,
  ]);
}
