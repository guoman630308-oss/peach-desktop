/* 详情类历史条目的背景（ADR-0031「覆盖式路由」）：作品详情、四种队列与关注详情压在一页上面，
 * 它们的历史条目在 `usr` 里记下压在哪一页上（`backgroundLocation`），条目自己说得清背景。
 *
 * 写：壳在决定打开详情的那一刻调 `holdOverlayBackground` 记下背景，push 详情地址时用 `overlayState` 带上；
 * 队列地址要等取完数才 push，背景仍是决定那一刻的，不是 push 那一刻的地址。详情之间换条沿用上一条的
 * 背景、不嵌套；只存路径与查询串，不存 DOM、对象或条目 `key`（刷新后对不上任何活条目）。
 *
 * 读：后退前进落到详情条目上时，路由树的覆盖元素（`react/router/pages/overlay.tsx`）调 `adoptOverlayState`
 * 接上这一条记的背景，壳打开详情的那一处再用 `takeOverlayReturn` 取来处，关掉回到那一页。冷启动（刷新、
 * 新标签页、深链）不读：条目跨刷新仍带着 `usr`，下面那页却从没画过，页面组照没有背景补画（`/assets/:id` 补画
 * 目录网格），关掉回各自的缺省来处。 */
import { matchPath } from 'react-router';
import { isPreferencePath } from './settings-route';

/** 覆盖在页面上的那几种地址。不进 `isRoutedPath`：页面宿主不画它们。 */
export const OVERLAY_PATHS = [
  '/assets/:id',
  '/posts/:id',
  '/works/:code',
] as const;

export type OverlayPath = typeof OVERLAY_PATHS[number];

export type OverlayKind = 'item' | 'follow' | 'jav';

/** 一条覆盖地址要打开的那一条：作品详情、关注详情，或四种队列里的一条（`key` 是种子或播放列表）。 */
export type OverlayTarget =
  | { kind: 'jav'; code: string }
  | { kind: 'item' | 'follow'; id: number }
  | { kind: 'queue'; queue: 'mix' | 'parts' | 'editions' | 'playlist'; key: number; item: number };

const QUEUES = ['mix', 'parts', 'editions', 'playlist'] as const;

/** 地址匹配哪一条覆盖路径；本地条目使用数字，库外作品使用有效番号。 */
export function overlayMatch(pathname: string, search = ''): { path: OverlayPath; target: OverlayTarget } | null {
  const address = new URL(pathname, 'https://peach.invalid');
  pathname = address.pathname;
  search ||= address.search;
  for (const path of OVERLAY_PATHS) {
    const match = matchPath(path, pathname);
    if (!match) continue;
    if (path === '/works/:code') {
      const code = match.params.code || '';
      return /^[A-Za-z0-9_-]{2,64}$/.test(code) ? { path, target: { kind: 'jav', code } } : null;
    }
    const values = Object.values(match.params);
    if (!values.every((value) => /^\d+$/.test(value ?? ''))) return null;
    const first = Number(values[0]);
    if (!Number.isSafeInteger(first) || first <= 0) return null;
    if (path === '/assets/:id') {
      const params = new URLSearchParams(search);
      const contexts = QUEUES.filter(queue => params.has(queue));
      if (contexts.length === 1) {
        const queue = contexts[0]!, value = params.get(queue)!;
        const key = Number(value);
        if (/^[1-9]\d*$/.test(value) && Number.isSafeInteger(key)) {
          return { path, target: { kind: 'queue', queue, key, item: first } };
        }
      }
    }
    return { path, target: { kind: path === '/assets/:id' ? 'item' : 'follow', id: first } };
  }
  return null;
}

/** 地址要打开的那一条覆盖；路径或身份参数无效时是 null。 */
export const overlayTarget = (pathname: string, search = ''): OverlayTarget | null => overlayMatch(pathname, search)?.target ?? null;

export interface BackgroundLocation {
  pathname: string;
  search: string;
}

/** 详情条目的 `usr`：压在哪一页上，压着的是哪一种详情。 */
export interface OverlayState {
  backgroundLocation: BackgroundLocation;
  overlay: OverlayKind;
}

export const isOverlayPath = (pathname: string): boolean => (
  isPreferencePath(pathname.split(/[?#]/)[0]!) || OVERLAY_PATHS.some((pattern) => matchPath(pattern, pathname.split(/[?#]/)[0]!) !== null)
);

/** 条目里记的背景：详情与队列条目的 `usr` 里有 `{ backgroundLocation, overlay }`，别的条目没有。 */
export function backgroundOf(state: unknown): BackgroundLocation | null {
  const background = (state as { backgroundLocation?: Partial<BackgroundLocation> } | null)?.backgroundLocation;
  if (typeof background?.pathname !== 'string') return null;
  return { pathname: background.pathname, search: typeof background.search === 'string' ? background.search : '' };
}

let held: BackgroundLocation | null = null;
/* 后退前进接上的那一条的来处，打开详情时取一次就清：只给条目自己打开的那一下用，换条、展开小窗、
 * 盘回来重开这些在打开之后才发生的动作照旧按壳记的来处走。 */
let pendingReturn: string | null = null;

/** 决定打开详情的那一刻：当前在页面上就记下这一页；已经在详情地址上（同队列换条、关注组内换条、
 * 从详情点开另一条）沿用已记的背景，没有就不记，不把详情地址当背景。 */
export function holdOverlayBackground(): void {
  const { pathname, search } = window.location;
  if (!isOverlayPath(pathname)) held = { pathname, search };
}

/** 真正离开详情时清掉：关掉详情、删掉当前条目、后退前进与启动。 */
export function clearOverlayBackground(): void {
  held = null;
  pendingReturn = null;
}

/** 后退前进落到一条历史条目上：条目记了背景就接上，之后从这条详情换条、展开小窗照样带同一个背景；
 * 打开详情的那一处由 `takeOverlayReturn` 取它作来处。条目没记背景时什么都不做。 */
export function adoptOverlayState(state: unknown): void {
  const background = backgroundOf(state);
  if (!background) return;
  held = background;
  pendingReturn = background.pathname + background.search;
}

/** 打开详情时取后退前进接上的来处（路径加查询串），取一次就清；没有就是 null，壳用自己的缺省。 */
export function takeOverlayReturn(): string | null {
  const path = pendingReturn;
  pendingReturn = null;
  return path;
}

/** push 详情地址时带的 `usr`；没有背景就不带，和页面条目一样。 */
export function overlayState(overlay: OverlayKind): OverlayState | undefined {
  return held ? { backgroundLocation: { ...held }, overlay } : undefined;
}
