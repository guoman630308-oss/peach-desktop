/* 作品详情 `/assets/:id` 与四种队列（Mix、分卷、版本、播放列表）的数据与判定（ADR-0031）。
 *
 * 这一块是舞台岛（`../stage/`）树里的子组件，舞台本身、进出场、小窗与 Video.js 归舞台岛，
 * 关注详情也在用同一套。条目归这里：`/api/items` 的卡片没有 `entity_refs`、标签也只是
 * 字符串，拿来当详情只会让身份区与标签区先画一版再跳，所以一律单条取 `GET /api/item?id=`。
 *
 * 队列也归岛，按 `{kind, seedId|playlistId}` 取、键里带上它们：同一个队列里换一条走缓存，
 * 从外面重新进来（`fresh`）才重取——壳只记着此刻开的是哪一个队列，用来判这一下。 */
import type { QueryKey } from '@tanstack/react-query';
import { esc, foldName } from '@peach/legacy/core';
import { mixLabel, type FaceRef } from '@peach/card-art';

import { apiGet } from '../../api';
import type { MediaCardActions, MediaCardHelpers, MediaCardLayout, MediaItem } from '../catalog-grid/types';
import { queryClient } from '../query';

/* ── 接口形状（`src/peach/web_catalog.py` 的 `q_item`） ── */

/** 详情里一枚标签：`cat` 是分类，`official` 是来源站点给的。 */
export interface DetailTag { k: string; cat?: string; official?: boolean }

/** 身份区里的一个实体。扁平字段兜底来的那一格没有 `id`，只画、不能点。 */
export interface DetailEntityRef extends FaceRef {
  id: number | null;
  name: string;
  has_logo?: boolean;
  /** 标识文件的内容版本，拼进 `/logo` 地址的 `&v=`。 */
  logo_version?: string;
  /** 厂牌是某家片商旗下的 label 时（ADR-0049），从近到远的上级片商链。 */
  makers?: DetailEntityRef[];
}

/** 卡片条目里声明过的那些字段。`MediaItem` 带着 `[field: string]: unknown`，直接 `Omit` 会把
 *  声明过的键一起抹掉（`keyof` 成了 `string`），先摘掉索引签名再挑。 */
type CardFields = { [K in keyof MediaItem as string extends K ? never : K]: MediaItem[K] };

/** `/api/item` 的一条。只列详情读到的字段，其余原样带着，壳的缓存要的是整条。 */
export interface DetailItem extends Omit<CardFields, 'tags'> {
  [field: string]: unknown;
  error?: string;
  title?: string;
  width?: number | null;
  height?: number | null;
  release_date?: string | null;
  region?: string | null;
  region_label?: string | null;
  region_settled?: boolean;
  rating?: number | null;
  liked?: boolean | number;
  like_reason?: string;
  o_count?: number;
  better_version?: boolean | number;
  better_version_reason?: string;
  has_studio_logo?: boolean;
  studio_logo_version?: string;
  poster_box?: { x0: number; y0: number; x1: number; y1: number; px: number[] } | null;
  part_label?: string;
  tags?: DetailTag[];
  entity_refs?: Partial<Record<'performer' | 'studio' | 'creator' | 'series', DetailEntityRef[]>>;
  sample_images?: { n: number }[];
}

/** 队列里的一条：卡片那一份，外加版次与卷标。 */
export interface QueueItem extends MediaItem { edition_label?: string; part_label?: string; version_id?: number }

export type QueueKind = 'mix' | 'parts' | 'editions' | 'playlist';

/** 壳递进来的队列引用。`fresh` 为真表示这一下是从外面进这个队列，要重取；同一个队列里
 *  换一条时为假，读缓存。播放列表每次都重取：它的顺序和内容别处也在改。 */
export interface QueueRef { kind: QueueKind; seedId?: number; playlistId?: number; fresh?: boolean; autoplay?: boolean }

export interface DetailQueue {
  kind: QueueKind;
  seedId?: number;
  playlistId?: number;
  title: string;
  items: QueueItem[];
  /** 播放列表记下的续播位置。 */
  currentAssetId?: number | null;
  versionCount?: number;
}

/** `/api/playlist` 的写操作都回整份列表（`web_playlists.w_playlist`）。 */
export interface PlaylistPayload {
  id: number;
  name: string;
  items: QueueItem[];
  current_asset_id: number | null;
}

/* ── 取数 ── */

export const itemKey = (id: number): QueryKey => ['item', id];
export const queueKey = (ref: QueueRef): QueryKey => ['item-queue', ref.kind, ref.kind === 'playlist' ? ref.playlistId : ref.seedId];
export const relatedKey = (id: number, limit: number): QueryKey => ['item-related', id, limit];

export const ITEM_URL = (id: number) => `/api/item?id=${id}`;
export const RELATED_URL = (id: number, limit: number) => `/api/related?id=${id}&limit=${limit}`;
export const PLAYLIST_URL = '/api/playlist';
export const FEEDBACK_URL = '/api/feedback';
export const ITEM_TAG_URL = '/api/item-tag';
export const WATCH_LATER_URL = '/api/watch-later';
export const QUALITY_GOAL_URL = '/api/quality-goal';
export const PREFERENCE_URL = '/api/preference';

/** 条目已不在数据库里：服务端回 `{error}`，不是一次失败的请求。 */
export class ItemGone extends Error {}

export async function fetchItem(id: number, signal?: AbortSignal): Promise<DetailItem> {
  const item = await apiGet<DetailItem>(ITEM_URL(id), signal);
  if (item.error) throw new ItemGone(item.error);
  return item;
}

interface GroupPayload { error?: string; title?: string; items?: QueueItem[]; count?: number }

/** 一个队列的全部条目。Mix 是种子加上它的相关作品（相关那一份每个种子只取一次，和目录里
 *  Mix 卡的悬停翻页共用）；分卷与版本是同一个番号下的几个可播条目；播放列表按它自己的顺序。 */
export async function fetchQueue(ref: QueueRef, helpers: Pick<ItemDetailHelpers, 'mixRelated' | 'tagLabel'>, signal?: AbortSignal): Promise<DetailQueue> {
  if (ref.kind === 'mix') {
    const seedId = ref.seedId!;
    const [seed, related] = await Promise.all([fetchItem(seedId, signal), helpers.mixRelated(seedId)]);
    queryClient.setQueryData(itemKey(seed.id), seed);
    return {
      kind: 'mix', seedId, title: `Mix · ${mixLabel(seed as unknown as QueueItem, helpers.tagLabel)}`,
      items: [seed as unknown as QueueItem, ...related.filter((item) => item.id !== seed.id)],
    };
  }
  if (ref.kind === 'playlist') {
    const playlist = await apiGet<PlaylistPayload>(`${PLAYLIST_URL}?id=${ref.playlistId}`, signal);
    return playlistQueue(playlist);
  }
  const group = await apiGet<GroupPayload>(`/api/${ref.kind}?id=${ref.seedId}`, signal);
  if (group.error || !group.items?.length) throw new Error(group.error || '这一组已不存在');
  return {
    kind: ref.kind, seedId: ref.seedId!, title: `${ref.kind === 'parts' ? '分卷' : '版本'} · ${group.title || ''}`,
    items: group.items,
    versionCount: ref.kind === 'editions' ? group.count : undefined,
  };
}

export const playlistQueue = (playlist: PlaylistPayload): DetailQueue => ({
  kind: 'playlist', playlistId: playlist.id, title: playlist.name, items: playlist.items || [],
  currentAssetId: playlist.current_asset_id,
});

/** 播放列表里标「已消失」的条目：文件不在盘上，列出来、标出来，但不打开去播放（ADR-0087）。 */
export const isVanished = (item: QueueItem) => item.disposal === 'vanished';

/** 队列里停在哪一条。Mix 就是点的那一条；分卷与版本点的那一条不在组里（深链写错了、组变了）
 *  就退到第一条；播放列表退到它记下的续播位置，已消失的条目不停，退到第一条能播的。
 *  一条都停不了是 null。 */
export function chooseItem(queue: DetailQueue, requested: number | null): number | null {
  if (queue.kind === 'mix') return requested;
  const items = queue.kind === 'playlist' ? queue.items.filter((item) => !isVanished(item)) : queue.items;
  if (!items.length) return null;
  if (requested != null && items.some((item) => item.id === requested)) return requested;
  if (queue.kind === 'playlist' && items.some((item) => item.id === queue.currentAssetId)) return queue.currentAssetId!;
  return items[0]!.id;
}

/** 卷标只有分卷队列知道：`/api/item` 是单条口径，它答不出「这是第几卷」。不补的话标题栏里的
 *  卷号在深链进来和点开队列另一条时都不出现。 */
export function withPartLabel(item: DetailItem, queue: DetailQueue | null): DetailItem {
  if (queue?.kind !== 'parts' && queue?.kind !== 'editions') return item;
  const part_label = queue.items.find((part) => part.id === item.id)?.part_label || '';
  return item.part_label === part_label ? item : { ...item, part_label };
}

/** 播完一卷只接同一版本的下一卷；选另一个版本由用户发起。 */
export function nextPart(queue: DetailQueue | null, id: number): QueueItem | null {
  if (!queue || (queue.kind !== 'parts' && queue.kind !== 'editions')) return null;
  const index = queue.items.findIndex((row) => row.id === id);
  const current = queue.items[index];
  const next = queue.items[index + 1];
  if (!current?.part_label || !next?.part_label) return null;
  return queue.kind === 'parts' || (current.version_id != null && current.version_id === next.version_id) ? next : null;
}

/** 首屏：先定队列（这一下要不要重取由壳说），再定停在哪一条，最后取那一条。要转走的几种情形
 *  （在线作品转关注详情、队列取不到退回普通详情、播放列表空了回列表页、条目已不在）交给壳，
 *  壳一换舞台，这一次挂载就作废，不会画出来。 */
export async function prefetchItemDetail(props: ItemDetailProps, signal: AbortSignal): Promise<void> {
  const { queue: ref, actions } = props;
  let queue: DetailQueue | null = null;
  let seeded = false;
  if (ref) {
    const key = queueKey(ref);
    try {
      const cached = ref.fresh ? undefined : queryClient.getQueryData<DetailQueue>(key);
      seeded = !cached && ref.kind === 'mix';
      queue = cached || await queryClient.fetchQuery({ queryKey: key, queryFn: () => fetchQueue(ref, props.helpers, signal), staleTime: 0 });
    } catch (error) {
      if (signal.aborted) throw error;
      if (props.id != null) actions.redirect({ kind: 'item', id: props.id });
      else actions.redirect({ kind: 'gone' });
      return;
    }
  }
  const id = queue ? chooseItem(queue, props.id) : props.id;
  if (id == null) {
    actions.redirect(queue?.kind === 'playlist' ? { kind: 'playlists' } : { kind: 'gone' });
    return;
  }
  let item: DetailItem;
  /* 重开同一条也重取：缓存里那份是上一次打开时的，这期间封面、评分、标签都可能在别处改过，
     而挂载不重取（`query.ts`）。只有 Mix 的种子不重取，取队列那一步刚把它写进缓存。 */
  const cachedItem = seeded && id === queue?.seedId ? queryClient.getQueryData<DetailItem>(itemKey(id)) : undefined;
  try {
    item = cachedItem
      || await queryClient.fetchQuery({ queryKey: itemKey(id), queryFn: () => fetchItem(id, signal), staleTime: 0 });
  } catch (error) {
    if (signal.aborted) throw error;
    if (error instanceof ItemGone) { actions.redirect({ kind: 'gone' }); return }
    throw error;
  }
  if (!signal.aborted && item.location === 'online' && item.follow_item_id) actions.redirect({ kind: 'follow', id: Number(item.follow_item_id) });
}

/* ── 壳递进来的那几样 ── */

/** 标签选择器的一个候选：馆藏里带这枚标签的作品数。 */
export interface TagCount { k: string; n?: number }

/** 详情里仍由遗留层拼的几段 HTML 与读数。都是全站语义契约的唯一实现，这里不另写一份。
 *  身份格与队列行的图、头像和署名直接取 `@peach/card-art`。 */
export interface ItemDetailHelpers {
  /** 来源角标（遗留层 `srcBadge`）；`cls` 是 `srcbig` 这类尺寸档。 */
  badgeHtml(location: string, cost: string, cls?: string): string;
  /** 番号 + 版次徽章 + 片名（遗留层 `javTitleHtml`）。 */
  titleHtml(item: DetailItem): string;
  /** 同一标题的纯文本（遗留层 `javDisplayName`）。 */
  displayName(item: QueueItem): string;
  /** 「JAV 默认封面」设置，每次现读：队列一行的小图按它取封套或预览图。 */
  javImage(): string;
  tagLabel(tag: string): string;
  /** 时长分档那几枚标签：由时长推出来的，不在详情里列。 */
  isDurationTag(tag: string): boolean;
  /** 标签选择器里的全部候选（目录的标签读数）。 */
  tagCandidates(): TagCount[];
  /** 来源此刻是不是没挂载。 */
  sourceOffline(location: string): boolean;
  offlineReason(location: string): string;
  /** 「接着看」那一排的骨架（遗留层 `pageSkeletonHtml`），和壳里别处的是同一份。 */
  relatedSkeletonHtml(): string;
  /** Mix 的相关作品，每个种子只取一次，和目录里 Mix 卡的悬停翻页共用。 */
  mixRelated(seedId: number): Promise<QueueItem[]>;
  /** 横排接上拖动滚动（遗留层 `wireDrag`）。 */
  wireDrag(el: HTMLElement): void;
  /** 播放列表那一列拖着排序（遗留层 `wireDragReorder`）：拖动中的行由它挂 `dragging`、
   *  `drop-before`／`drop-after`，落下时回调新位置。 */
  wireDragReorder(root: HTMLElement, options: {
    selector: string; attribute: string; onMove(from: string, target: string, after: boolean): Promise<void> | void;
  }): void;
}

/** 壳要转走的几种情形。 */
export type ItemDetailRedirect =
  | { kind: 'follow'; id: number }
  | { kind: 'item'; id: number }
  | { kind: 'playlists' }
  | { kind: 'gone' };

export interface ItemDetailActions {
  /** 关闭详情回到来处（壳的 `closeItemDetail`：退场动画、拆舞台、推回列表地址与筛选）。 */
  close(): void;
  /** 这一条画出来了：壳据此更新小窗元数据、顶栏的实体上下文、氛围光与剧场模式，队列的地址
   *  也在这时推（停在哪一条要等岛定下来）。 */
  present(item: DetailItem, queue: DetailQueue | null): void;
  redirect(to: ItemDetailRedirect): void;
  /** 队列里点另一条：舞台上的播放器要先拆、地址要换，经壳重挂。`push` 为假时不推地址。 */
  openQueueItem(queue: QueueRef, id: number, push?: boolean): void;
  /** 把媒体框交给舞台挂播放器：`<video>` 由舞台建在框里、交给 Video.js，不进这棵 React 树——
   *  从小窗展开回来时同一个实例要整块搬进这块框。返回的清理在这块媒体区卸下时调。签名与关注
   *  详情同一个：作品详情没有「第几份媒体」，`media` 传 null；`autoplay` 不给就按设置，计费
   *  拦截点开的那一下给 true。 */
  mountPlayer(frame: HTMLElement, item: DetailItem, media: null, options?: { autoplay?: boolean }): () => void;
  /** 盘回来了：按正常路径重开这一条，不在半路挂播放器。 */
  reopen(): void;
  /** 重新检测一个来源挂没挂上。 */
  checkSource(location: string): Promise<boolean>;
  /** 在线作品反查不到关注条目时的出口：打开关注页「已保存」那一档。 */
  openSavedFollow(): void;
  openEntity(kind: string, name: string): void;
  openUnowned(): void;
  openRegion(region: string): void;
  /** 点一枚标签：回列表只看它。 */
  openTag(tag: string): void;
  /** 加入播放列表那一屏（壳的表单弹层：舞台是 `showModal()` 开的原生 dialog，弹层要进同一层）。 */
  addToPlaylist(item: DetailItem): void;
  /** 保存 Mix 那一屏：壳弹表单，确认时调 `save(name)`；存好了壳转去那份播放列表并给撤销。 */
  saveMix(options: { title: string; count: number; save(name: string): Promise<PlaylistPayload> }): void;
  /** 编辑播放列表：去播放列表页。 */
  editPlaylist(): void;
  /** 播放列表被移空了：回播放列表页。 */
  openPlaylists(): void;
  /** 定位源文件；回的是失败原因，成功是空串（成功的回执归全站 Toast）。 */
  reveal(id: number): Promise<string>;
  /** 核对这一条所在的目录；回状态一行，外加被移入回收站的那几条。 */
  sync(id: number): Promise<{ text: string; removed: number[] }>;
  /** 目录的「垃圾文件」那一档按回收站状态列：移入回收站之后（`undo` 为假）、撤销任何一次反馈之后
   *  （`undo` 为真）由壳判要不要重读列表。 */
  trashChanged(disposal: string | null, undo: boolean): Promise<void>;
  /** 操作回执；给了 `undo` 就带一颗撤销键。失败走 `failure`。 */
  toast(message: string, options?: { undo?: () => Promise<void> }): void;
  failure(action: string, error: unknown): void;
}

export interface ItemDetailProps {
  /** 点的那一条；播放列表从列表页进来时可以没有，退到续播位置。 */
  id: number | null;
  queue: QueueRef | null;
  /** 「接着看」一排取几条；0 是关掉。有队列时不出这一排。 */
  relatedLimit: number;
  helpers: ItemDetailHelpers;
  actions: ItemDetailActions;
  /** 「接着看」那一排的卡片：和目录网格同一份助手与动作。 */
  grid: { helpers: MediaCardHelpers; actions: MediaCardActions };
  layout: MediaCardLayout;
  selectMode: boolean;
  selected: ReadonlySet<number>;
  seekSeconds: number;
}

/* ── 身份区 ── */

/** 出镜者超过这个数就收起：BEST 合集实测有 41 位，全铺开会把标签和反馈按钮挤出可视区。 */
export const CAST_SHOWN = 8;

export interface IdentityGroups {
  cast: DetailEntityRef[];
  studios: DetailEntityRef[];
  makers: DetailEntityRef[];
  creators: DetailEntityRef[];
  series: DetailEntityRef[];
  /** 判据和 `owner=none` 那条 SQL 一样：没有出镜者、没有创作者实体、扁平 creator 也空。厂牌和
   *  系列不算归属——两边说的必须是同一批作品，否则卡片上写着「未归属」的这条点进集合会不在里面。 */
  unowned: boolean;
}

/** 身份按类别分组，同一个名字只出现一次（先到的那一类留下）。共演作品的女优逐个列出；
 *  非规范厂牌只有扁平 `studio`，它的标识可用性单独下发在 `has_studio_logo`。 */
export function identityGroups(item: DetailItem): IdentityGroups {
  const refs = item.entity_refs || {};
  const seen = new Set<string>();
  const fresh = (ref: DetailEntityRef) => {
    const key = foldName(ref.name);
    if (!ref.name || seen.has(key)) return false;
    seen.add(key);
    return true;
  };
  const performers: DetailEntityRef[] = refs.performer?.length
    ? refs.performer : (item.performers || []).map((name) => ({ id: null, name }));
  const cast = performers.filter(fresh);
  const studioRef = refs.studio?.[0];
  const studioFallback: DetailEntityRef[] = studioRef || !item.studio
    ? [] : [{ id: null, name: item.studio, has_logo: !!item.has_studio_logo,
      ...(item.studio_logo_version ? { logo_version: item.studio_logo_version } : {}) }];
  const studios = [...(refs.studio || []), ...studioFallback].filter(fresh);
  const makers = studios.flatMap((ref) => ref.makers || []).filter(fresh);
  const creators = (refs.creator || []).filter(fresh);
  const series = (refs.series || []).filter(fresh);
  return { cast, studios, makers, creators, series, unowned: !cast.length && !creators.length && !String(item.creator || '').trim() };
}

/* ── 评分 ──
   评分落在 `asset.rating`，量纲是 0–100：这一列是 Stash 的 rating100 直接导进来的，taste_history
   也按 rating/20 折算成 0–5 分。所以第 n 颗星送出的是 n*20，不是 n。再点当前那一颗表示撤销，
   送 0，后端写回 NULL——「没评过」和「评了一星」在排序上必须是两件事。 */
export const RATING_STEP = 20;
export const clampRating = (value: number | null | undefined) => Math.min(Math.max(value || 0, 0), 100);
export const ratingStars = (value: number | null | undefined) => Math.round(clampRating(value) / RATING_STEP);
export const ratingText = (value: number | null | undefined) => {
  const stars = ratingStars(value);
  return stars ? `${stars} 星` : '未评分';
};
/** 点第 `star` 颗之后要送的值：点的就是当前那一颗是撤销。 */
export const nextRating = (before: number | null | undefined, star: number) => {
  const picked = star * RATING_STEP;
  return picked === (before || 0) ? 0 : picked;
};

/* ── 标签 ── */

/** 详情里列的标签：去掉时长分档，按显示名去重（改过显示名的可能与另一条同名，优先留本身就是
 *  规范显示名的那条），最多 40 枚。 */
export function detailTags(item: DetailItem, helpers: Pick<ItemDetailHelpers, 'tagLabel' | 'isDurationTag'>): DetailTag[] {
  const byDisplay = new Map<string, DetailTag>();
  (item.tags || []).filter((tag) => !helpers.isDurationTag(tag.k)).forEach((tag) => {
    const key = foldName(helpers.tagLabel(tag.k));
    const previous = byDisplay.get(key);
    if (!previous || (foldName(tag.k) === key && foldName(previous.k) !== key)) byDisplay.set(key, tag);
  });
  return [...byDisplay.values()].slice(0, 40);
}

export const hasTag = (item: DetailItem, tag: string) => (item.tags || []).some((own) => foldName(own.k) === foldName(tag));
export const withoutTag = (item: DetailItem, tag: string): DetailItem =>
  ({ ...item, tags: (item.tags || []).filter((own) => foldName(own.k) !== foldName(tag)) });
export const withTag = (item: DetailItem, tag: DetailTag): DetailItem =>
  (hasTag(item, tag.k) ? item : { ...item, tags: [...(item.tags || []), tag] });

const RECENT_TAGS = 'peach.recentTags';
export function recentTags(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(RECENT_TAGS) || '[]');
    return Array.isArray(value) ? value.map(String) : [];
  } catch {
    return [];
  }
}
export function rememberTag(tag: string): void {
  try {
    const rest = recentTags().filter((name) => foldName(name) !== foldName(tag));
    localStorage.setItem(RECENT_TAGS, JSON.stringify([tag, ...rest].slice(0, 12)));
  } catch {
    // 存储满了或被禁用：最近使用只是便利，不影响添加本身。
  }
}

export interface PickerSections { recent: TagCount[]; results: TagCount[]; create: string }

/** 标签选择器里这一刻列什么：没输入时是最近使用加全部（前 120 枚），输入了是命中的那几枚；
 *  输入的词没有完全命中的，末尾多一格「新建」。 */
export function pickerSections(query: string, candidates: readonly TagCount[], recent: readonly string[]): PickerSections {
  const q = foldName(query);
  const byName = new Map(candidates.map((tag) => [foldName(tag.k), tag]));
  const results = candidates.filter((tag) => !q || foldName(tag.k).includes(q)).slice(0, 120);
  const recentRows = q ? [] : recent.map((name) => byName.get(foldName(name)) || { k: name, n: 0 })
    .filter((tag, index, all) => all.findIndex((other) => foldName(other.k) === foldName(tag.k)) === index).slice(0, 12);
  const exact = results.some((tag) => foldName(tag.k) === q);
  return { recent: recentRows, results, create: q && !exact ? query.trim() : '' };
}

/* ── 队列 ── */

/** 版次徽章的配色跟卡片标题上的那套走。多一个 `有码`：卡片上正片不加角标是对的（没角标就是
 *  正片），但队列里两条并排时「什么都不写」等于让人自己猜哪条是哪条。 */
export const EDITION_TONE: Record<string, string> = { 中字: 'subtitle', 无码: 'uncensored', 无码破解: 'cracked', 有码: 'censored' };

const QUEUE_LABEL: Record<QueueKind, string> = { mix: 'Mix', parts: '分卷', editions: '版本', playlist: '播放列表' };

/** 队列头那两行：类别与摘要。版次队列的标题是「版本 · 番号」，而番号就印在正上方的详情标题里，
 *  标题栏又已经写着「版本」——三处说同一件事，这里只留数量。别的队列标题带真信息（播放列表名、
 *  Mix 种子），不能一起砍。 */
export function queueCopy(queue: DetailQueue): { title: string; summary: string } {
  const count = (queue.kind === 'editions' ? queue.versionCount ?? queue.items.length : queue.items.length).toLocaleString();
  const countLabel = queue.kind === 'parts' ? `${count} 卷` : queue.kind === 'editions' ? `${count} 个版本` : `${count} 个视频`;
  return { title: QUEUE_LABEL[queue.kind], summary: queue.kind === 'editions' ? countLabel : `${queue.title} · ${countLabel}` };
}

/** 同一部片的几卷共用文件名，标题、女优、厂牌逐字相同：详情标题不写卷号的话，在队列里换一卷，
 *  右侧整栏看上去纹丝不动。卷号说的是「第几份文件」而不是版次，用中性灰。数字卷写「第 2 卷」，
 *  字母卷照番号分卷后缀的写法大写成「A 卷」。 */
export const partLabel = (label = '') => /^\d+$/.test(label) ? `第 ${label} 卷`
  : /^[a-h]$/i.test(label) ? `${label.toUpperCase()} 卷` : label;
export const partLabelHtml = (item: DetailItem, queue: DetailQueue | null) =>
  ((queue?.kind === 'parts' || queue?.kind === 'editions') && item.part_label ? `<small class="javedition partlabel">${esc(partLabel(item.part_label))}</small>` : '');

/** 拖动之后的新顺序：`from` 挪到 `target` 的前面或后面。 */
export function movedOrder(ids: readonly number[], from: number, target: number, after: boolean): number[] {
  const rest = ids.filter((id) => id !== from);
  const at = rest.indexOf(target);
  rest.splice(at + (after ? 1 : 0), 0, from);
  return rest;
}

export const sameOrder = (a: readonly number[], b: readonly number[]) =>
  a.length === b.length && a.every((id, index) => id === b[index]);

/* ── 播放区 ── */

export type MediaGate = 'offline' | 'online' | 'metered' | 'disc' | '';

/** 播放区此刻挡在视频前面的是哪一种说明：来源没挂载、在线作品反查不到关注条目、计费来源要
 *  点一下才拉流。保存过的在线作品照常播（壳在取数时已经把它转去关注详情）。 */
export function mediaGate(item: DetailItem, offline: boolean): MediaGate {
  if (offline) return 'offline';
  if (String(item.name || '').toLowerCase().endsWith('.iso')) return 'disc';
  if (item.location === 'online' && !item.follow_item_id) return 'online';
  if (item.cost === 'metered' && item.location !== 'online') return 'metered';
  return '';
}

/** 真实观看那条的初值：看过的秒数占时长。时长未知（0 或 -1 哨兵）时不画。 */
export function realWatched(item: DetailItem): number | null {
  const duration = Number(item.duration) > 0 ? Number(item.duration) : 0;
  if (!item.play_seconds || !duration) return null;
  return Math.min(Number(item.play_seconds) / duration, 1) * 100;
}

/* ── 写完之后 ── */

/** 反馈键的回执。 */
export function feedbackReceipt(kind: string, result: { feedback?: string | null; disposal?: string | null }): string {
  if (kind === 'dislike') return result.feedback === 'dislike' ? '已标记不合口味' : '已取消不合口味';
  if (kind === 'seen') return result.feedback === 'seen' ? '已标记看过' : '已取消看过';
  if (kind === 'dispose') return result.disposal === 'trash' ? '已移入回收站' : '已移出回收站';
  return '已记录一次高潮';
}
