/* 「想要」清单的数据契约（ADR-0090）：`/api/wants` 在 `frontend/src` 里只在这里声明一次。
 *
 * 两个键：清单整页一把（关注管理的「想要」页签），关注详情问「这一条想要过没有」按条目各一把。
 * 写完把两族都作废：在详情里点了想要，回到页签时清单要已经有它；在页签里移除了，详情那颗键也要
 * 跟着弹起。 */
import { apiGet, apiSend } from '../../api';
import { queryClient } from '../query';

export const WANTS_URL = '/api/wants';

export const WANTS_KEY = ['wants'] as const;
export const wantFollowKey = (itemId: number) => ['wants', 'follow', itemId] as const;

/** 清单上的四段，次序就是页签里排的次序。 */
export const PHASES = [
  ['searching', '待找'], ['unreleased', '未发售'], ['given_up', '暂时放弃'], ['acquired', '已入库'],
] as const;
export type Phase = (typeof PHASES)[number][0];

/** 一条想要。字段与 `src/peach/web_wants.py` 的 `_payload` 对齐。 */
export interface Want {
  id: number;
  code: string | null;
  has_magnets?: boolean | null;
  origin: 'code' | 'feed' | 'follow';
  title: string | null;
  link: string | null;
  release_date: string | null;
  studio: string | null;
  performers: string | null;
  phase: Phase;
  fresh: boolean;
  search_count: number;
  last_search_at: string | null;
  last_search_outcome: 'found' | 'none' | 'error' | null;
  given_up_at: string | null;
  acquired_asset_id: number | null;
  acquired_at: string | null;
  follow_item_id: number | null;
  follow_provider: string | null;
  cover: string | null;
  /** 封面还是来源给的远程地址：要带 `no-referrer` 去取。 */
  remote_cover: boolean;
  scraped: boolean;
  scrape_error: string | null;
  created_at: string;
}

export interface WantsData {
  items: Want[];
  counts: Record<Phase, number>;
  /** 取资料那一轮还在跑：刚登记的那几条资料还没回来。 */
  scraping: boolean;
}

export const fetchWants = (signal?: AbortSignal) => apiGet<WantsData>(WANTS_URL, signal);

export function recordWantMagnets(id: number, available: boolean) {
  queryClient.setQueryData<WantsData>(WANTS_KEY, current => {
    if (!current?.items.some(want => want.id === id && want.has_magnets !== available)) return current;
    return { ...current, items: current.items.map(want => want.id === id ? { ...want, has_magnets: available } : want) };
  });
}

export const fetchFollowWant = (itemId: number, signal?: AbortSignal) =>
  apiGet<{ want: Want | null }>(`${WANTS_URL}?follow=${encodeURIComponent(itemId)}`, signal);

type AddTarget = { code: string } | { follow: number };

export const addWant = (target: AddTarget, signal?: AbortSignal) =>
  apiSend<{ created: boolean; want: Want }>(WANTS_URL, { action: 'add', ...target }, 'POST', signal);

export const removeWants = (ids: number[], signal?: AbortSignal) =>
  apiSend(WANTS_URL, { action: 'remove', ids }, 'POST', signal);

export const resetWants = (ids: number[], signal?: AbortSignal) =>
  apiSend(WANTS_URL, { action: 'reset', ids }, 'POST', signal);

/** 写完之后：清单与各条关注详情的想要状态一起重取。 */
export const invalidateWants = () => queryClient.invalidateQueries({ queryKey: WANTS_KEY });

/** 一条想要叫什么：番号打头，没有番号的关注条目用标题。 */
export const wantName = (want: Want) => want.code || want.title || `关注条目 ${want.follow_item_id ?? want.id}`;

/** 「待找」那一段的说明：新片查不到不计数，老片查满三次就暂时放弃。 */
export function searchNote(want: Want): string {
  if (want.phase === 'acquired') return want.acquired_at ? `入库于 ${want.acquired_at.slice(0, 10)}` : '已入库';
  if (want.phase === 'unreleased') return want.release_date ? `${want.release_date} 发售` : '还没发售';
  if (want.phase === 'given_up') return `查过 ${want.search_count} 次都没找到`;
  if (want.fresh) return '新片：查不到也一直留在待找';
  return want.search_count ? `查过 ${want.search_count} 次` : '还没查过';
}
