/* 关注页 `/follow`（ADR-0031 第 10g 步）：数据形状、取数键与这一页自己的判定。
 *
 * 整页一个岛，自己拥有取数：`/api/follow` 与 `/api/follow/credentials` 两趟都在 TanStack Query
 * 里。地址栏是筛选的唯一真相源，归壳写：壳从 URL 读出 `view`（状态、媒体、创作者、来源、题材、
 * 标签、排序、种子）当 props 递进来，岛改筛选只调 `actions.route(view)`，壳写好地址再经
 * `updateManagedRoute` 推回新的 `view`，岛按新键取数。
 *
 * 两粒种子分开：`view.seed` 是服务端随机排序那一粒，写在地址里；`seed` 是这一次进入的取样种子，
 * 上面创作者、题材、标签三排露出哪些由它定。重新进入（壳的 `enterFollow`）与「换一批」
 * 才换它，从别的页面回来沿用离开时那一粒，岛重画从不重新洗牌。
 *
 * 详情（`/posts/:id`）是另一座岛（`../follow-detail/`），先扫这里缓存的几页找条目
 * （`findFollowItem`），扫不到才单独取。侧栏仍归壳画：岛每取到一版列表就经 `actions.loaded`
 * 交回这一视图可见条目的标签计数、全库口径的来源与时长有无。 */
import { keepPreviousData, type InfiniteData, type QueryKey } from '@tanstack/react-query';
import { seededRank } from '@peach/legacy/core';

import { apiGet } from '../../api';
import { FOLLOW_CREDENTIALS_URL, type CheckResult } from '../follow-manage/follow-manage';
import { queryClient } from '../query';

/* ── 地址栏那一组 ── */

/** 状态那一排。已看那一档不摆：看过就归档，要再翻出来是「全部」的事。 */
export const FOLLOW_FILTERS = [['', '全部'], ['new', '未看'], ['saved', '已保存'], ['ignored', '已忽略']] as const;
export type FollowStatus = (typeof FOLLOW_FILTERS)[number][0];

/** 排序键与各自方向的说法；「换一批」按下去是 `rand`，没有自己的键。 */
export const FOLLOW_FEED_SORTS = [['new', '更新时间'], ['hot', '热度'], ['dur', '时长']] as const;
export const FOLLOW_FEED_DIR_WORDS: Record<string, readonly [string, string]> = {
  new: ['从新到旧', '从旧到新'], hot: ['从高到低', '从低到高'], dur: ['从长到短', '从短到长'],
};
export const FOLLOW_RANDOM_SORT = 'rand';
export type FollowSort = (typeof FOLLOW_FEED_SORTS)[number][0] | typeof FOLLOW_RANDOM_SORT;
export type FollowMedia = 'videos' | 'images';

/** 地址栏上的关注页筛选。创作者、来源、题材各按着一个（空串是不筛），标签是交集。 */
export interface FollowView {
  status: FollowStatus;
  media: FollowMedia;
  author: string;
  provider: string;
  work: string;
  tags: readonly string[];
  /** 时长两端（秒），同首页的 `dur_min`／`dur_max`；0 是这一端不限。 */
  durMin: number;
  durMax: number;
  sort: FollowSort;
  dir: 'asc' | 'desc';
  /** 服务端随机排序的种子，只在 `sort` 为 `rand` 时进请求。 */
  seed: number;
}

/** 关注页一次取一屏。`counts` 是全库口径，`groups` 只是这一页，所以列表底部要能继续加载。
 *  一页 100 组：一张卡三四十个节点，一次挂几百张就是首屏一秒多的长任务。 */
export const FOLLOW_PAGE = 100;
/** 已经摆出这么多组以后，滚到底不再自己接下一页，要点「加载更多」：一直滚下去页面就一直变重，
 *  滚过头的那一下不该悄悄再挂几百张卡。 */
export const FOLLOW_AUTO_LOAD_LIMIT = 500;

/** 这一页第 `offset` 组起的请求地址，参数拼法同壳里 `followPageUrl`：默认那一档不写进去。 */
export function followPageUrl(view: FollowView, offset: number): string {
  const csv = (values: readonly string[]) => encodeURIComponent(values.join(','));
  return `/api/follow?limit=${FOLLOW_PAGE}&offset=${offset}`
    + (view.status ? `&status=${view.status}` : '')
    + (view.author ? `&author=${csv([view.author])}` : '')
    + (view.provider ? `&provider=${csv([view.provider])}` : '')
    + (view.tags.length ? `&tag=${csv(view.tags)}` : '')
    + (view.work ? `&work=${csv([view.work])}` : '')
    + (view.durMin ? `&dur_min=${view.durMin}` : '')
    + (view.durMax ? `&dur_max=${view.durMax}` : '')
    + (view.sort !== 'new' ? `&sort=${view.sort}` : '')
    + (view.sort === FOLLOW_RANDOM_SORT ? `&seed=${view.seed}` : '')
    + (view.dir !== 'desc' ? `&dir=${view.dir}` : '');
}

/* ── 接口形状（`src/peach/web_follow.py` 的 `q_follow`） ── */

/** 条目里嵌着的一份媒体：多附件帖的每张图、每段视频。 */
export interface FollowMediaItem {
  index: number;
  media_kind?: string;
  thumb_url?: string;
  width?: number;
  height?: number;
  face?: unknown;
  resource_group?: unknown;
}

/** 一条抓回来的更新。只列这一页读到的字段，其余原样留给详情。 */
export interface FollowItem {
  id: number;
  title: string;
  status: string;
  provider: string;
  provider_label?: string;
  resource_provider?: string;
  source_id?: number;
  published_at?: string;
  duration?: number;
  thumb_url?: string;
  media_kind?: string;
  media_items?: FollowMediaItem[];
  width?: number;
  height?: number;
  face?: unknown;
  tags?: string[];
  tag_types?: Record<string, string>;
  playable?: boolean;
  [field: string]: unknown;
}

/** 服务端对一组的叠卡判定（`peach.follow_faces`）：去重后的媒体数、合并了几份与翻卡画面。 */
export interface FollowStackInfo {
  media?: number;
  copies?: number;
  kind?: string;
  faces?: { thumb_url?: string; face?: unknown; media_kind?: string }[];
}

/** 一张卡：同一作品的主条目、本站的 alt 与 WIP、跨站的另见。 */
export interface FollowGroup {
  primary: FollowItem;
  variants: FollowItem[];
  duplicates: FollowItem[];
  has_wip?: boolean;
  /** 论坛发布帖：组里每条是一条回复，正文是回复内容而不是线程标题。 */
  is_release?: boolean;
  stack?: FollowStackInfo | null;
}

/** 一个关注来源。 */
export interface FollowSource {
  id: number;
  provider: string;
  provider_label?: string;
  author_key?: string;
  last_status?: string;
  can_backfill?: boolean;
  backfill_page?: number;
  [field: string]: unknown;
}

/** 题材那一排的一行：键、给人看的写法、组数、挑不挑得出代表图、那张图的取景。 */
export type FollowWorkRow = readonly [key: string, label: string, n: number, icon?: number, focus?: unknown];

export interface FollowFacets {
  authors?: string[];
  providers?: string[];
  tags?: [string, number][];
  works?: FollowWorkRow[];
  /** 库里有没有时长读数：侧栏据它出不出时长拉条。 */
  duration?: boolean;
}

/** 交给壳画侧栏的那一份：可见条目的标签计数、全库口径的来源（键、显示名、站标地址）与时长有无。 */
export interface FollowDrawer {
  tags: readonly (readonly [string, number])[];
  providers: readonly (readonly [key: string, label: string, icon: string])[];
  duration: boolean;
}

export interface FollowPage {
  groups: FollowGroup[];
  counts: Record<string, number>;
  sources: FollowSource[];
  author_aliases?: unknown[];
  facets: FollowFacets;
  /** 正好按一位作者筛时，只数这一位名下的那一份筛选项（资料页在线视图读它）；否则是空的。 */
  author_facets?: FollowFacets;
  offset: number;
  limit?: number;
  has_more: boolean;
  [field: string]: unknown;
}

export interface FollowCredentials { providers?: { provider: string; present?: boolean }[] }

/* ── 取数键 ── */

/** 离开关注页以后，列表在缓存里留多久。回到关注页时照这一份原样画，已经加载的几页和滚动位置
 *  才接得上；默认的 5 分钟太短，点开别处看一部片子回来就没了。 */
export const FOLLOW_FEED_GC_MS = 30 * 60_000;

/** 列表的键。媒体切换是纯前端分组，不进键：换视图不重取。`revision` 是壳要求重读的代次。 */
export function followFeedQuery(view: FollowView, revision: number) {
  const url = followPageUrl(view, 0);
  return {
    queryKey: ['follow-feed', url, revision] as QueryKey,
    queryFn: ({ pageParam, signal }: { pageParam: number; signal?: AbortSignal }) =>
      apiGet<FollowPage>(followPageUrl(view, pageParam), signal),
    initialPageParam: 0,
    /* 服务端先整批分组再按组分页，同一作品不会跨页，下一页的组直接接在后面。 */
    getNextPageParam: (last: FollowPage) => (last.has_more ? (last.offset || 0) + FOLLOW_PAGE : undefined),
    placeholderData: keepPreviousData,
    gcTime: FOLLOW_FEED_GC_MS,
  };
}

export const FOLLOW_CREDENTIALS_KEY: QueryKey = ['follow-feed', 'credentials'];

/** 凭据取不到时当作一个都没有：它只决定「需要登录会话」那句提示出不出，不该拦住整页。 */
export const fetchFollowCredentials = (signal?: AbortSignal) =>
  apiGet<FollowCredentials>(FOLLOW_CREDENTIALS_URL, signal).catch((): FollowCredentials => ({ providers: [] }));

/** 首屏：两趟并行，挂上就是最终样子。同一个键（同一份筛选、同一个代次）缓存里还有，就是离开前
 *  看着的那一份，原样拿来接着看，连同已经加载的几页；要重读由壳推一个新代次换键。凭据每次都重取：
 *  它在管理页上改，回到这里就该是新的。 */
export async function prefetchFollowFeed(props: { view: FollowView; revision: number }, signal: AbortSignal) {
  const query = followFeedQuery(props.view, props.revision);
  queryClient.removeQueries({ queryKey: FOLLOW_CREDENTIALS_KEY, exact: true });
  const kept = queryClient.getQueryData(query.queryKey) !== undefined;
  await Promise.all([
    kept ? null : queryClient.fetchInfiniteQuery({
      queryKey: query.queryKey,
      queryFn: ({ pageParam }) => query.queryFn({ pageParam: pageParam as number, signal }),
      initialPageParam: 0,
      getNextPageParam: query.getNextPageParam,
      gcTime: FOLLOW_FEED_GC_MS,
    }),
    queryClient.fetchQuery({ queryKey: FOLLOW_CREDENTIALS_KEY, queryFn: () => fetchFollowCredentials(signal) }),
  ]);
}

/** 几页合成一份，形状同壳里 `followData`：组接在后面，其余取最后一页（来源随每页刷新）。 */
export function mergedPage(data: InfiniteData<FollowPage> | undefined): FollowPage | null {
  if (!data?.pages.length) return null;
  const pages = data.pages;
  const last = pages[pages.length - 1]!;
  return { ...pages[0]!, ...last, groups: pages.flatMap((page) => page.groups || []) };
}

/* ── 取样 ── */

/** 按种子打散，同秩再按键名排，结果只随种子变。秩取壳那一份 `seededRank`，同一粒种子两边取到同一批。 */
export function randomOrder<T>(rows: readonly T[], key: (row: T) => string, seed: number): T[] {
  return [...rows].sort((a, b) =>
    seededRank(seed, key(a)) - seededRank(seed, key(b)) || String(key(a)).localeCompare(String(key(b))));
}

/** 题材那一排露出几个，同首页那排厂牌（壳的 `ROW_FIRST`）。 */
export const FOLLOW_WORKS_FIRST = 24;
/** 标签露出几个。 */
export const FOLLOW_TAGS_FIRST = 20;

/* ── 媒体判定 ── */

/** 一组里的全部条目，主条目在前、按 id 去重。 */
export function collectionItems(group: FollowGroup): FollowItem[] {
  const seen = new Set<number>();
  return [group.primary, ...group.variants, ...group.duplicates].filter((item) => {
    if (!item || seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

/** 列表缓存里的一条：条目本身、所在的组与那一版的来源和别名。 */
export interface FollowItemHit {
  item: FollowItem;
  group: FollowGroup;
  sources: FollowSource[];
  aliases: unknown[];
}

/** 在这座岛已缓存的每一版列表里找一条（详情先扫这里）。来源与别名取那一版最后一页的，同 `mergedPage`。
 *  新取的那一版先找：上一代次的列表还留在缓存里，同一条在那里是标记、保存之前的样子。 */
export function findFollowItem(id: number): FollowItemHit | null {
  const versions = queryClient.getQueryCache().findAll({ queryKey: ['follow-feed'] })
    .sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt);
  for (const version of versions) {
    const data = version.state.data as InfiniteData<FollowPage> | undefined;
    if (!Array.isArray(data?.pages) || !data.pages.length) continue;
    const last = data.pages[data.pages.length - 1]!;
    for (const page of data.pages) {
      for (const group of page.groups || []) {
        const item = collectionItems(group).find((member) => member.id === id);
        if (item) return { item, group, sources: last.sources || [], aliases: last.author_aliases || [] };
      }
    }
  }
  return null;
}

/** 每一版列表缓存里的同一条换成 `next`（详情隐藏或恢复一张图之后，服务端投影给出新的媒体清单）。 */
export function replaceFollowItem(next: FollowItem): void {
  queryClient.setQueriesData<InfiniteData<FollowPage>>({
    queryKey: ['follow-feed'], predicate: (query) => Array.isArray((query.state.data as InfiniteData<FollowPage>)?.pages),
  }, (data) => data && {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      groups: (page.groups || []).map((group) => {
        const swap = (item: FollowItem) => (item.id === next.id ? next : item);
        if (!collectionItems(group).some((item) => item.id === next.id)) return group;
        return { ...group, primary: swap(group.primary), variants: group.variants.map(swap), duplicates: group.duplicates.map(swap) };
      }),
    })),
  });
}

/** 每一版列表缓存里的同一条换状态、挪计数（详情里的保存与标记）。不移出整组：详情不知道每一版筛着哪一档。 */
export function setFollowItemStatus(id: number, to: string): void {
  queryClient.setQueriesData<InfiniteData<FollowPage>>({
    queryKey: ['follow-feed'], predicate: (query) => Array.isArray((query.state.data as InfiniteData<FollowPage>)?.pages),
  }, (data) => data && withStatus(data, null, id, to));
}

/** 同上，按发布时间从新到旧。 */
export function collectionItemsNewest(group: FollowGroup): FollowItem[] {
  return collectionItems(group).sort((a, b) => {
    const byTime = (Date.parse(b.published_at || '') || 0) - (Date.parse(a.published_at || '') || 0);
    return byTime || (+b.id || 0) - (+a.id || 0);
  });
}

/* 关注条目和资料页的作品不是同一种 DTO，但媒体切换的语义相同：一张卡只要含对应媒体就进入对应
   视图。external 只说明有外部文件页，不能冒充视频；没有可验证媒体类型的旧行不进任一视图。 */
export function itemMediaKinds(item: FollowItem): Set<string> {
  const kinds = new Set<string>();
  const embedded = item.media_items || [];
  if (embedded.length) {
    embedded.forEach((media) => {
      if (media.media_kind === 'image' || media.media_kind === 'video') kinds.add(media.media_kind);
    });
  } else if (item.media_kind === 'image' || item.media_kind === 'video') kinds.add(item.media_kind);
  return kinds;
}

export function groupMediaKinds(group: FollowGroup): Set<string> {
  const kinds = new Set<string>();
  collectionItems(group).forEach((item) => itemMediaKinds(item).forEach((kind) => kinds.add(kind)));
  return kinds;
}

/** 卡面摆哪一条：组里最新的、含当前视图那种媒体的那条，都没有就是主条目。 */
export function itemForMedia(group: FollowGroup, view: FollowMedia): FollowItem {
  const wanted = view === 'images' ? 'image' : 'video';
  return collectionItemsNewest(group).find((item) => itemMediaKinds(item).has(wanted)) || group.primary;
}

/** 资料页在线视图取数用的那一份：只按创作者筛，状态全部、按更新时间从新到旧。 */
export function entityFollowView(author: string): FollowView {
  return { status: '', media: 'videos', author, provider: '', work: '', tags: [], durMin: 0, durMax: 0,
    sort: 'new', dir: 'desc', seed: 0 };
}

/* F95 的「8 条动态」可能只有一个网盘页，也可能一条实际视频都没有。Mix 是播放语义，只能由已解析、
   可在 Peach 内播放的视频触发，不能拿回复数或外链数冒充。 */
export function videoItems(group: FollowGroup): FollowItem[] {
  return collectionItemsNewest(group).filter((item) => item.playable && item.media_kind === 'video');
}

/* ── 叠卡 ──
   哪几张是同一个画面由服务端判（`peach.follow_faces`，文件内容哈希，缩略图 dHash、8×8 色块加时长）：
   每个成员和媒体带 `face`，编号相同就是同一个画面；组上的 `stack` 给出去重后的媒体数 `media`、合并了
   几份 `copies`、媒体类型 `kind` 和彼此不同的翻卡画面 `faces`。这里只决定怎么说、翻哪几张。

   角标只报一个数：这张卡合并了几个不同的媒体。同一个视频在两个站各传一份是一个媒体、两个来源；
   去重后只剩一个媒体时，报的就是来源数。量词随媒体类型走：视频论「个」，图片论「张」，与详情里
   「第 1 张，共 11 张」同一套；图和视频混在一起时说「个媒体」，不说「项」——「项」在关注页数的是
   更新条目，读起来像又在数帖子。 */
const UNITS: Record<string, string> = { video: '个视频', image: '张图片', mixed: '个媒体' };

export interface FollowStack {
  isMix: boolean;
  label: string;
  glyph: 'pics' | 'play';
  faces: string[];
}

export function followStack({ cover = '', coverFace = null, stack = null, imageView = false, limit = 9 }: {
  cover?: string; coverFace?: unknown; stack?: FollowStackInfo | null; imageView?: boolean; limit?: number;
} = {}): FollowStack {
  const media = stack?.media || 0;
  const copies = stack?.copies || 0;
  const kind = stack?.kind || '';
  const label = media > 1 ? `${media} ${UNITS[kind] || UNITS.mixed}`
    : copies > 1 ? `${copies} 个来源` : '';
  /* 字形说的是点开以后看什么：视频起播是 play，图片是 pics。图和视频混在一起时，点开落在哪一种由
     视图决定，字形跟着视图走。 */
  const glyph = kind === 'image' || (kind !== 'video' && imageView) ? 'pics' : 'play';
  /* 图片视图里只翻图片：这一叠说的就是这几张图。第一张是静止封面本身，否则一翻就露出取景差别；
     和封面同一个画面的不再翻。画面彼此相同的服务端已经并成一张。 */
  const seen = new Set<unknown>([coverFace ?? cover]);
  const faces = cover ? [cover] : [];
  for (const face of stack?.faces || []) {
    if (imageView && face.media_kind !== 'image') continue;
    if (!face.thumb_url || face.thumb_url === cover || seen.has(face.face)) continue;
    seen.add(face.face);
    faces.push(face.thumb_url);
  }
  return { isMix: Boolean(label), label, glyph, faces: faces.length > 1 ? faces.slice(0, limit) : [] };
}

/* ── 往回抓 ──
   往回抓到哪儿了。不说的话，用户点一次只看到列表变长一点，不知道自己走到第几页，也不知道还要点
   几次。页码是 0 起的游标（0 = 只抓过第一页），显示成人读的第几页。 */
export function backfillState(sources: readonly FollowSource[]): string {
  const pages = sources.filter((source) => source.can_backfill).map((source) => (source.backfill_page || 0) + 1);
  if (!pages.length) return '';
  const deepest = Math.max(...pages);
  const shallowest = Math.min(...pages);
  if (deepest <= 1) return '每个来源都只抓了第 1 页';
  return shallowest === deepest ? `每个来源都抓到第 ${deepest} 页` : `已抓到第 ${shallowest}–${deepest} 页`;
}

/* ── 排序键 ── */

/** 点未选中项换列并用该列的默认方向，点选中项翻方向；同壳里 `nextSortState` 配上这一页的词表。 */
export function nextSort(key: string, current: string, dir: 'asc' | 'desc') {
  if (key !== current) return { sort: key as FollowSort, dir: 'desc' as const };
  if (!FOLLOW_FEED_DIR_WORDS[key]) return null;
  return { sort: key as FollowSort, dir: dir === 'asc' ? 'desc' as const : 'asc' as const };
}

/** 无障碍名称播报点下去会得到什么，不是当前状态。 */
export function sortAriaLabel(key: string, label: string, current: string, dir: 'asc' | 'desc'): string {
  const next = nextSort(key, current, dir);
  if (!next) return '';
  const words = FOLLOW_FEED_DIR_WORDS[next.sort];
  return `按${label}${words ? words[next.dir === 'asc' ? 1 : 0] : ''}排序`;
}

/* ── 壳递进来的那几样 ── */

/** 署名：名字、圆框里那段头像 HTML、「署名含」那一行。 */
export interface FollowIdentity { author: string; avatar: string; credited: string; entityName: string }

/** 这一页上下文：卡片与两排的身份、头像读的都是这一版的来源、别名与凭据，不读壳的全局。 */
export interface FollowContext {
  sources: readonly FollowSource[];
  aliases: readonly unknown[];
  credentials: ReadonlySet<string>;
}

/** 仍由遗留层给的 HTML 与接线：详情用的是同一份，只有一处实现。署名、头像、来源图标与标题前后
 *  那几处字样在岛里（`follow-marks.ts`）。 */
export interface FollowFeedHelpers {
  /** 题材圆标里那段（有代表图出 `<img>`，没有写两个字母）；取景与放大走资料页那两个函数。 */
  workMark(row: FollowWorkRow): string;
  /** 标签的界面名称，关注详情（舞台）读它；列表页与卡片从 `@peach/legacy/tags` 直接 import 同一个函数。 */
  tagLabel(tag: string): string;
  /** 横滚行接上拖动与滚轮（`wireDrag`）；只接滚轮与两端渐隐（`wireHorizontalScroller`）。 */
  wireDrag(row: Element | null): void;
  wireScroller(row: Element | null): void;
  /** 进页整块骨架（`followSkeletonHtml`）之外，换筛选时列表区那一块骨架。 */
  listSkeletonHtml(media: FollowMedia): string;
  /** 检查更新与往回抓那一趟后台任务的进度条（`peach-ui` 的 `followJobProgress`），作业 id 记在
   *  `sessionStorage` 的 `peach-follow-job`，刷新或换页回来还接得上。 */
  jobProgress(options: FollowJobProgressOptions): void;
}

export interface FollowJobState { status?: string; job_id?: string; error?: string; results?: CheckResult[]; [field: string]: unknown }

export interface FollowJobProgressOptions {
  host: HTMLElement;
  active(): boolean;
  read(signal: AbortSignal): Promise<FollowJobState>;
  busy(running: boolean): void;
  complete(state: FollowJobState): void;
}

export interface FollowFeedActions {
  /** 换筛选或排序：壳把它写进地址栏，再推回新的 `view`。 */
  route(view: FollowView): void;
  /** 换一批：壳掷一粒新种子，两排取样与列表次序都换。 */
  shuffle(): void;
  /** 岛取到一版列表（首屏、续页、写操作之后）：侧栏要的标签计数、来源与时长有无，壳拿去画侧栏。 */
  loaded(drawer: FollowDrawer): void;
  openDetail(id: number): void;
  openAuthor(name: string): void;
  openManage(): void;
  /** 多选里的一张：`range` 是 Shift 连选。 */
  toggleSelection(id: number, range: boolean): void;
  /** 照片墙「仅显示图片」与图片布局两样偏好，壳存起来再推回。 */
  setImagesOnly(on: boolean): void;
  setPhotoLayout(layout: FollowPhotoLayout): void;
  /** 此刻能不能悬停翻卡（多选、遮挡、减少动效、滚动中都回 false）。 */
  canFlip(): boolean;
  /** 操作回执；给了 `undo` 就带一颗撤销键。失败走 `failure`。 */
  toast(message: string, options?: { undo?: () => Promise<void> }): void;
  failure(action: string, error: unknown): void;
  /** 检查更新的结果（`followCheckToast`）：新增、没有更新与失败各说清。 */
  checkReport(report: unknown): void;
}

/** 状态写接口回的那一份：换的是哪一条、换成了什么。 */
export interface FollowStatusWrite { item: number; to: string }

/** 写完之后在缓存里换局部：条目状态、状态计数，筛着某一档而新状态不在这一档时整组移出。
 *  `view` 为空时只换状态与计数、不移出（详情里的写操作）。 */
export function withStatus(data: InfiniteData<FollowPage>, view: FollowView | null, id: number, to: string):
InfiniteData<FollowPage> {
  let before = '';
  const pages = data.pages.map((page) => ({
    ...page,
    groups: (page.groups || []).flatMap((group) => {
      let hit = false;
      const swap = (item: FollowItem) => {
        if (item.id !== id) return item;
        hit = true;
        before = before || item.status;
        return { ...item, status: to };
      };
      const next = { ...group, primary: swap(group.primary), variants: group.variants.map(swap), duplicates: group.duplicates.map(swap) };
      if (hit && view?.status && next.primary.id === id && to !== view.status) return [];
      return [next];
    }),
  }));
  if (!before || before === to) return { ...data, pages };
  return {
    ...data,
    pages: pages.map((page) => {
      const counts = { ...page.counts };
      if (counts[before] !== undefined) counts[before] = Math.max(0, (counts[before] || 0) - 1);
      counts[to] = (counts[to] || 0) + 1;
      return { ...page, counts };
    }),
  };
}

export type FollowPhotoSize = 'big' | 'small';
export type FollowPhotoLayout = 'fixed' | 'masonry';

export interface FollowFeedProps {
  view: FollowView;
  /** 这一次进入的取样种子。 */
  seed: number;
  /** 壳要求重读的代次（批量标记之后、从别处写完回来），加一就重取、不重挂。 */
  revision: number;
  selectMode: boolean;
  selected: ReadonlySet<number>;
  photoSize: FollowPhotoSize;
  photoLayout: FollowPhotoLayout;
  imagesOnly: boolean;
  helpers: FollowFeedHelpers;
  actions: FollowFeedActions;
}

/** 当前按下的筛选条件，四个维度收成一张清单，顺序照筛选条从粗到细：创作者、来源、题材、标签。 */
export interface FollowCondition { kind: '创作者' | '来源' | '题材' | '标签'; key: string; label: string }

export function followConditions(view: FollowView, names: {
  authors: ReadonlyMap<string, string>; providers: ReadonlyMap<string, string>; works: ReadonlyMap<string, string>;
}): FollowCondition[] {
  const rows: FollowCondition[] = [];
  if (view.author) rows.push({ kind: '创作者', key: view.author, label: names.authors.get(view.author) || view.author });
  if (view.provider) rows.push({ kind: '来源', key: view.provider, label: names.providers.get(view.provider) || view.provider });
  if (view.work) rows.push({ kind: '题材', key: view.work, label: names.works.get(view.work) || view.work });
  view.tags.forEach((tag) => rows.push({ kind: '标签', key: tag, label: tag }));
  return rows;
}

/** 撤掉一条：按维度撤，同一个字符串在两个维度里都可能出现。 */
export function dropCondition(view: FollowView, row: FollowCondition): FollowView {
  if (row.kind === '创作者') return { ...view, author: '' };
  if (row.kind === '来源') return { ...view, provider: '' };
  if (row.kind === '题材') return { ...view, work: '' };
  return { ...view, tags: view.tags.filter((tag) => tag !== row.key) };
}

/** 标签在这一页的类型：取第一个记了类型的组，没有就按 general 着色。 */
export function groupTagType(groups: readonly FollowGroup[], tag: string): string {
  for (const group of groups) {
    const type = group.primary?.tag_types?.[tag];
    if (type) return type;
  }
  return 'general';
}
