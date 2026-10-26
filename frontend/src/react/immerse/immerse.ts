/* 沉浸模式的数据与判据：片单怎么抽、详情按哪个键取、署名怎么念、画面铺满还是完整显示。
 * 不碰 DOM，`immerse-island.tsx` 的控制器与组件都从这里取。 */
import type { QueryKey } from '@tanstack/react-query';

import { apiGet } from '../../api';
import { queryClient } from '../query';
import { fetchItem, itemKey, type DetailEntityRef, type DetailItem } from '../item-detail/item-detail';

/** 片单里的一条（`/api/items` 的卡片那一份）。 */
export interface ImmerseItem {
  id: number;
  name?: string;
  title?: string;
  location?: string;
  cost?: string;
  duration?: number | null;
  width?: number | null;
  height?: number | null;
  ctx_orient?: string;
  [field: string]: unknown;
}

/** 片单按「这一次打开」与「第几次续取」分键：每次打开都是一次全新的随机抽样，续取的那一批
 *  也不能读到上一批的缓存。 */
export const immerseListKey = (filters: string, seed: number, draw: number): QueryKey => ['immerse-list', filters, seed, draw];

/** 抽样用的查询串：首页筛选去掉画幅（横竖都进），随机取 60 条。
 *
 *  没有 offset 可翻：`sort=rand` 在服务端是未加种子的 `RANDOM()`（web_contract.py），每次请求
 *  都是一次全新的随机抽样，翻页偏移在它上面没有意义——带上去只会随机跳过若干行。续取靠的是
 *  调用点按 id 去重，不是偏移量。 */
export function immerseQuery(filters: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, String(value));
  params.delete('orient');
  params.set('sort', 'rand');
  params.set('limit', '60');
  params.set('offset', '0');
  params.set('thumb', '');
  return params.toString();
}

/** 能直接进片单的：不按流量计费、有时长、来源没脱盘。脱盘来源的片子拉不到流，进了片单就是
 *  一条黑屏加载中；详情页有门挡着，这里只能在入口筛掉。 */
export const playable = (item: ImmerseItem, offline: (location: string) => boolean): boolean =>
  item.cost !== 'metered' && !!item.duration && !offline(String(item.location ?? ''));

export function shuffle<T>(list: T[], random: () => number = Math.random): T[] {
  for (let i = list.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [list[i], list[j]] = [list[j]!, list[i]!];
  }
  return list;
}

export async function fetchImmerseList(query: string, offline: (location: string) => boolean): Promise<ImmerseItem[]> {
  const page = await apiGet<{ items?: ImmerseItem[] }>(`/api/items?${query}`);
  return shuffle((page.items || []).filter((item) => playable(item, offline)));
}

/** 这一条的详情：和作品详情同一个键，动作键写回之后详情与目录卡读到的是同一份。缓存里已有就不再
 *  取（切回看过的那一条、起播那一条在打开时已经取过）。 */
export const ensureDetail = (id: number): Promise<DetailItem> =>
  queryClient.ensureQueryData({ queryKey: itemKey(id), queryFn: ({ signal }) => fetchItem(id, signal) });

/** 已有的那一批之后接上新抽的一批，按 id 去重。 */
export function extendList(list: ImmerseItem[], more: ImmerseItem[]): ImmerseItem[] {
  const seen = new Set(list.map((item) => item.id));
  return list.concat(more.filter((item) => !seen.has(item.id)));
}

/** 作者那一行：共演作品念全出镜者（最多三位，多出来的写人数），点击进第一位的资料页；没有演员
 *  就是创作者，再没有就是「未归属」。 */
export interface Owner {
  who: string;
  kind: 'performer' | 'creator' | '';
  name: string;
  ref: DetailEntityRef | null;
}

export function ownerOf(item: Pick<DetailItem, 'performers' | 'creator' | 'entity_refs'>): Owner {
  const cast = item.performers || [];
  const who = cast.length
    ? cast.slice(0, 3).join('、') + (cast.length > 3 ? ` 等 ${cast.length} 人` : '')
    : (item.creator || '未归属');
  const kind = cast.length ? 'performer' : (item.creator ? 'creator' : '');
  const name = cast.length ? cast[0]! : (item.creator || '未归属');
  const ref = kind ? (item.entity_refs?.[kind]?.[0] || null) : null;
  return { who, kind, name, ref };
}

/** 沉浸模式默认 cover 铺满，但那只在片源和视口比例接近时才成立。
 *
 *  判据是两者比例差多少，不是「片源是不是竖屏」：那样 16:9 的横屏进竖屏视口照样 cover，按高度
 *  放大到两边各裁掉一大半。容差取得很紧（1.05）是刻意的：对竖屏片源用 contain 是「不裁掉正在看
 *  的画面」的有意选择。放宽到 1.25 会顺手把 9:16 片源在 9:19.5 手机上改成 cover、裁掉约 18%
 *  高度。现在只有比例几乎一致时才 cover（省掉取整产生的 1px 黑边），其余一律完整显示。
 *  视口比例会随旋转和窗口尺寸改变，所以必须跟着重算，不能只在 loadedmetadata 算一次。 */
export const FIT_TOLERANCE = 1.05;

/** `null` 是还量不出来（元数据没到或框没有尺寸），这时不改现状。 */
export function fitMode(sourceWidth: number, sourceHeight: number, box: number): 'cover' | 'contain' | null {
  if (!sourceWidth || !sourceHeight || !box || !Number.isFinite(box)) return null;
  const source = sourceWidth / sourceHeight;
  const mismatch = source > box ? source / box : box / source;
  return mismatch > FIT_TOLERANCE ? 'contain' : 'cover';
}

/** 舞台形状（竖 9:16／横 16:9）：元数据到了按画面，没到按条目登记的宽高，都没有算竖。 */
export function isWide(video: { videoWidth: number; videoHeight: number }, item: Pick<ImmerseItem, 'width' | 'height'>): boolean {
  if (video.videoWidth && video.videoHeight) return video.videoWidth >= video.videoHeight;
  return !!(item.width && item.height && item.width > 0 && item.height > 0 && item.width >= item.height);
}

/* ── 手势的几个数 ── */
/** 同一半区两下之间不超过这么久算双击。 */
export const DOUBLE_TAP_MS = 280;
/** 触屏抬手之后浏览器合成的那一下 click 不再切播放：那一下已经由单击／双击判定接管。 */
export const SYNTHETIC_CLICK_MS = 700;
/** 位移超过这么多才定方向；方向一旦定下就不再改，否则斜着划会又切片又跳进度。 */
export const AXIS_LOCK_PX = 12;
/** 竖划超过这么多换条。 */
export const SWIPE_PX = 60;
/** 两个方向都不超过这么多算一次点击。 */
export const TAP_SLOP_PX = 14;
/** 滚轮换条的节流。 */
export const WHEEL_GAP_MS = 260;
/** 不喜欢之后停这么久再换下一条：回执先落地。 */
export const DISLIKE_ADVANCE_MS = 260;
/** 切片动画的时长加一帧余量；动画帧没跑到（页面在后台）也按这个时刻落位。 */
export const SLIDE_MS = 210;
/** 等一格可以出画的上限。 */
export const READY_TIMEOUT_MS = 15_000;
