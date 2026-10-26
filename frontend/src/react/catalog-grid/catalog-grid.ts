/* 馆藏卡片网格的数据：目录（`/` 与四个筛选态、回收站）、资料页作品区，以及壳直接给一批
 * 条目的那两处（详情页的接着看）。
 *
 * 目录和作品区都是一页一个 `useInfiniteQuery`，「载入更多」就是取下一页。
 *
 * 一屏卡片的排法全由这里算，渲染只照着画：
 * - 分卷与版次各自折叠。同一组只留第一次出现的那一张，跨页也算（设置关掉就不折）。
 * - 目录每一页可见的卡够 8 张时，第 8 位插一张 Mix；种子从 Mix 位再往下隔一屏开始找，
 *   不会和同屏的卡撞图。
 * - 竖屏带每接一页出现一条，落点在这一页新增的那几行之间的行边界上随机取一个。 */
import type { InfiniteData, QueryKey } from '@tanstack/react-query';

import { apiGet } from '../../api';
import { javImageKind } from '../../jav-artwork';
import type { CatalogGridProps, MediaItem, MediaPage } from './types';
import { queryClient } from '../query';
import { catalogView } from './catalog-view';

/** 竖屏带一条放几张。 */
export const SHORTS_BATCH = 18;
/** Mix 卡插在每一页的这一位，也就是第 8 张。 */
export const MIX_SLOT = 7;

/** 一页的结果，外加第一页为空时「整个馆藏是不是空的」：空态据此说「还没有作品」还是
 *  「没有符合的」。 */
export interface GridPage extends MediaPage {
  offset: number;
  libraryEmpty?: boolean;
}

/** 目录的查询串，不含 `limit`／`offset`。`exclude_vertical` 的判据归壳：首页默认列表另有
 *  竖屏带承接竖屏，搜索和显式筛了竖屏的时候不排除。 */
export function catalogParams(filters: Readonly<Record<string, string>>, excludeVertical: boolean): string {
  const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value));
  if (excludeVertical) params.set('exclude_vertical', '1');
  return params.toString();
}

export const catalogKey = (params: string, revision: number): QueryKey => ['catalog-grid', 'catalog', params, revision];
export const entityKey = (key: string, revision: number): QueryKey => ['catalog-grid', 'entity', key, revision];

/** 目录的一页。第一页为空、又不是回收站时，再问一次整个馆藏有没有东西。 */
export async function fetchCatalogPage(
  params: string, offset: number, batch: number, trash: boolean, signal: AbortSignal,
): Promise<GridPage> {
  const query = new URLSearchParams(params);
  query.set('limit', String(batch));
  query.set('offset', String(offset));
  if (offset) query.set('count', '0');
  const page = await apiGet<MediaPage>(`/api/items?${query}`, signal);
  const result: GridPage = { ...page, items: page.items || [], offset };
  if (!offset && !result.items.length && !trash) {
    const library = await apiGet<MediaPage>('/api/items?limit=1&thumb=0', signal);
    result.libraryEmpty = library.total === 0;
  }
  return result;
}

/** 下一页从哪儿取。第一页按总数判断，往后的页不带总数，看 `has_more`。 */
export const nextOffset = (batch: number) => (last: GridPage): number | undefined => {
  const more = last.offset === 0 ? last.items.length < (last.total || 0) : !!last.has_more;
  return more ? last.offset + batch : undefined;
};

/** 资料页作品区的下一页：壳给的第一页可能带 `has_more`，也可能只有总数。 */
export const nextEntityOffset = (last: GridPage, pages: GridPage[]): number | undefined => {
  const loaded = pages.reduce((sum, page) => sum + page.items.length, 0);
  const more = last.has_more == null ? loaded < (last.total || 0) : !!last.has_more;
  return more ? loaded : undefined;
};

export function catalogQuery(props: CatalogGridProps) {
  const params = catalogParams(props.filters || {}, !!props.excludeVertical);
  const batch = props.batchSize || 60;
  const trash = props.filters?.state === 'trash';
  return {
    queryKey: catalogKey(params, props.revision || 0),
    queryFn: ({ pageParam, signal }: { pageParam: number; signal: AbortSignal }) =>
      fetchCatalogPage(params, pageParam, batch, trash, signal),
    initialPageParam: 0,
    getNextPageParam: nextOffset(batch),
  };
}

export function entityQuery(props: CatalogGridProps) {
  return {
    queryKey: entityKey(props.entityKey || '', props.revision || 0),
    queryFn: async ({ pageParam, signal }: { pageParam: number; signal: AbortSignal }) => {
      if (!props.fetchPage) return { items: [], total: 0, offset: pageParam } as GridPage;
      const page = await props.fetchPage(pageParam, signal);
      return { ...page, items: page.items || [], offset: pageParam } as GridPage;
    },
    initialPageParam: 0,
    getNextPageParam: nextEntityOffset,
  };
}

/** 首屏取数（目录网格那条路由的 `prefetch`）。只有目录要等：资料页的作品区第一页跟着资料页递进来，
 *  网格挂上时自己落进缓存（`initialData`）。 */
export async function prefetchCatalogGrid(props: CatalogGridProps, signal: AbortSignal): Promise<void> {
  if (props.mode !== 'catalog' || props.wall || catalogView() === 'list') return;
  const query = catalogQuery(props);
  await queryClient.fetchInfiniteQuery({
    ...query, queryFn: ({ pageParam }) => query.queryFn({ pageParam: pageParam as number, signal }),
  });
}

/** 同一集合的分页保存在 Query 中；刷新代次之间认领整份分页，不复制作品到界面状态。 */
export function latestCatalogPages(key: QueryKey): InfiniteData<GridPage, number> | undefined {
  const exact = JSON.stringify(key);
  const cached = queryClient.getQueryCache().findAll({ queryKey: key.slice(0, -1) })
    .filter(query => JSON.stringify(query.queryKey) !== exact && Array.isArray((query.state.data as InfiniteData<GridPage> | undefined)?.pages))
    .sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt)[0];
  return cached?.state.data as InfiniteData<GridPage, number> | undefined;
}

/** 分卷与版次各自折叠：分卷是一部片被切成几段，版次是同一部片的几个来源，两者能同时
 *  出现在一个番号上，所以两套 key 分开记。`seen` 跨页累积，一组只留第一次出现的那一张。 */
export function collapseGroups(
  items: readonly MediaItem[], seen: { parts: Set<string>; editions: Set<string> }, enabled: boolean,
): MediaItem[] {
  if (!enabled) return [...items];
  return items.filter((item) => {
    const part = item.part_group?.key;
    if (part) {
      if (seen.parts.has(part)) return false;
      seen.parts.add(part);
    }
    const edition = item.edition_group?.key;
    if (edition) {
      if (seen.editions.has(edition)) return false;
      seen.editions.add(edition);
    }
    return true;
  });
}

/** 这一条真能画出图吗：和卡片封面的分支一致，只看 `has_cover` 会把只有官方封套、却按
 *  预览图偏好画的条目当成有图。 */
export const mixHasPicture = (item: MediaItem | undefined, javImage: string) =>
  !!item && Boolean(javImageKind(item, javImage));

/** 种子决定 Mix 的封面和署名。从 Mix 位再往下隔一屏开始找有图、有署名的，找不到逐级放宽。 */
export function mixSeed(visible: readonly MediaItem[], javImage: string): MediaItem | undefined {
  const named = (item: MediaItem) => mixHasPicture(item, javImage)
    && Boolean(item.creator || (item.performers || []).length || item.studio);
  return visible.slice(MIX_SLOT + 8).find(named)
    || visible.slice(MIX_SLOT + 1).find(named)
    || visible.slice(MIX_SLOT + 1).find((item) => mixHasPicture(item, javImage))
    || visible[visible.length - 1];
}

/** 网格里的一格：一张作品卡，或一张 Mix。 */
export type Tile = { kind: 'card'; item: MediaItem } | { kind: 'mix'; seed: MediaItem };

/** 把已取回的各页排成一列格子。每一页各自折叠、各自插 Mix，和逐页接上来时一样。
 *  `pageStarts[i]` 是第 i 页在这一列里的起点，竖屏带的落点要落在那一页新增的格子之间。 */
export function arrangeTiles(
  pages: readonly GridPage[], { collapse, mix, javImage }: { collapse: boolean; mix: boolean; javImage: string },
): { tiles: Tile[]; pageStarts: number[] } {
  const seen = { parts: new Set<string>(), editions: new Set<string>() };
  /* 翻页期间库里新进了作品，偏移量整体后挪，上一页末尾那几条会在下一页再出现一次。
     同一条只画第一次：一屏里两张一样的卡什么也不说明。 */
  const ids = new Set<number>();
  const tiles: Tile[] = [];
  const pageStarts: number[] = [];
  for (const page of pages) {
    pageStarts.push(tiles.length);
    const fresh = page.items.filter((item) => !ids.has(item.id) && Boolean(ids.add(item.id)));
    const visible = collapseGroups(fresh, seen, collapse);
    const cards: Tile[] = visible.map((item) => ({ kind: 'card', item }));
    if (mix && visible.length >= 8) {
      const seed = mixSeed(visible, javImage);
      if (seed) cards.splice(MIX_SLOT, 0, { kind: 'mix', seed });
    }
    tiles.push(...cards);
  }
  return { tiles, pageStarts };
}

/** 竖屏带的落点候选：从这一页新增的那几行开始，每个行边界一个；两端各留至少一行，剪在
 *  头尾就成了「置顶」或「垫底」，不是穿插。`addedFrom` 与 `count` 都按最后一段计。 */
export function shortsBoundaries(count: number, addedFrom: number, columns: number): number[] {
  const step = Math.max(1, columns);
  const boundaries: number[] = [];
  for (let at = Math.max(step, Math.ceil(Math.max(0, addedFrom) / step) * step); at < count; at += step) {
    boundaries.push(at);
  }
  return boundaries;
}

/** 已经插进网格的一条竖屏带：`at` 是它下面那一段在整列格子里的起点。 */
export interface ShortsCut {
  at: number;
  total: number;
  items: MediaItem[];
}

/** 竖屏带的查询串：跟着主列表的筛选与排序走，换一批时它也一起换。 */
export function shortsParams(filters: Readonly<Record<string, string>>, offset: number): string {
  const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value));
  params.set('orient', '竖屏');
  params.set('limit', String(SHORTS_BATCH));
  params.set('offset', String(offset));
  return params.toString();
}

/** 把一列格子按竖屏带切成几段，段与段之间插带子。 */
export function splitSections(tiles: readonly Tile[], cuts: readonly ShortsCut[]) {
  const ordered = [...cuts].sort((a, b) => a.at - b.at);
  const sections: { start: number; tiles: Tile[]; strip: ShortsCut | null }[] = [];
  let start = 0;
  for (const cut of ordered) {
    sections.push({ start, tiles: tiles.slice(start, cut.at), strip: cut });
    start = cut.at;
  }
  sections.push({ start, tiles: tiles.slice(start), strip: null });
  return sections;
}

/** 每一版已缓存的目录与作品区里的同一张卡换上 `patch`（详情里评分、反馈、稍后看之后）。
 *  只改字段不增删卡：详情不知道每一版筛着哪一档，移出与否等下次取数。 */
export function replaceCatalogItem(id: number, patch: Partial<MediaItem>): void {
  patchCatalogItems((item) => item.id === id, patch);
}

/** 同上，换的是 `match` 认出来的每一张：一个番号的几段、几个版本共用同一张封面。 */
export function patchCatalogItems(match: (item: MediaItem) => boolean, patch: Partial<MediaItem>): void {
  queryClient.setQueriesData<MediaPage>({ queryKey: ['catalog-grid', 'table'] }, data => {
    if (!data?.items.some(match)) return data;
    return { ...data, items: data.items.map(item => match(item) ? { ...item, ...patch } : item) };
  });
  queryClient.setQueriesData<InfiniteData<GridPage>>({
    queryKey: ['catalog-grid'], predicate: (query) => Array.isArray((query.state.data as InfiniteData<GridPage>)?.pages),
  }, (data) => {
    if (!data || !data.pages.some((page) => page.items.some(match))) return data;
    return {
      ...data,
      pages: data.pages.map((page) => (page.items.some(match)
        ? { ...page, items: page.items.map((item) => (match(item) ? { ...item, ...patch } : item)) } : page)),
    };
  });
}
