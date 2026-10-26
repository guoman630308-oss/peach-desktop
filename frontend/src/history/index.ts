/* 全站唯一的浏览器历史（`@peach/history`）。
 *
 * Application 与 React 路由树直接引用本模块，随 peach-app.js 发出同一个历史实例。
 * 启动地址清理与重定向使用 shellNavigate，Router 的 navigator 也读这一份。
 *
 * 实现是 React Router 自己的 `createBrowserHistory`（`v5Compat` 档：push / replace 也通知监听者），
 * 和它的 `<Router>` 读写的是同一种 `{usr, key, idx}` 历史条目，不另引一份 history 库。它只收一个
 * 监听者，而且要有监听者才挂 `popstate`：这里一建好就由本模块接上，再分发给各个订阅者，所以页面
 * 一加载就在数后退与前进，不等谁来订阅。
 *
 * 每一次历史变化（壳写的、React 子树写的、后退前进）都领一个递增的序号 `seq`。壳写地址走 `shellNavigate`，
 * 关闭详情返回已知原页面走 `returnToHistoryPage`，两种变化都认领（`claimed`）：页面元素保持代次，覆盖元素接走打开请求。其余后退前进、React
 * 子树写的与壳经 `peachHistory` 直接写的那几次没人认领，另领一个页面打开序号（`openEpoch`）：每一次都要把那一页
 * 按地址重开一遍。代次在通知订阅者之前就定了，路由根同步提交那一次渲染里读到的已经是新代次，页面按它挂 key，
 * 一次打开只挂一次。判据用序号与代次不用地址：同一条目重放的 `popstate` 地址不变，那一页照样要重开。 */
import { UNSAFE_createBrowserHistory, type Location, type NavigationType, type Navigator, type To } from 'react-router';

import { backgroundOf, isOverlayPath, type BackgroundLocation } from './overlay';
import { canonicalPath } from './canonical-path';
import { settingsBackgroundOf, isPreferencePath } from './settings-route';
export * from './settings-route';
export { canonicalPath } from './canonical-path';

/** 一次历史变化：动作、变化之后的地址、它领到的序号、到这一次为止的页面打开序号，和这一次是不是壳认领写的。 */
export interface Navigation {
  action: NavigationType;
  location: Location;
  seq: number;
  openEpoch: number;
  claimed: boolean;
}

export type NavigationListener = (navigation: Navigation) => void;

/** 给 `<Router navigator>` 用的那一份，外加当前这一次变化与订阅。 */
export interface PeachHistory extends Navigator {
  readonly navigation: Navigation;
  listen(listener: NavigationListener): () => void;
}

const browser = UNSAFE_createBrowserHistory({ v5Compat: true });
let seq = 0;
let openEpoch = 0;
let current: Navigation = { action: browser.action, location: browser.location, seq, openEpoch, claimed: true };
const listeners = new Set<NavigationListener>();
/* `claiming` 只在 `shellNavigate` 写地址那一下为真。 */
let claiming = false;
let returningTo: number | null = null;
/* 还停在页面加载时的那一条上：之后只经 replace 改写过（清理启动地址、页内写参数），没 push、没后退前进。 */
let onBootEntry = true;
interface PagePosition { left: number; top: number; pages?: number }
const pagePositions = new Map<string, PagePosition>();
/* 只追踪本次加载见过的条目；刷新后不能据旧 idx 猜测上一页。 */
const knownEntries = new Map<number, Pick<Location, 'pathname' | 'search'>>([
  [window.history.state.idx, { pathname: browser.location.pathname, search: browser.location.search }],
]);

browser.listen(({ action, location }) => {
  const position = { ...pagePositions.get(current.location.key), left: window.scrollX, top: window.scrollY };
  pagePositions.set(current.location.key, position);
  if (action === 'REPLACE') pagePositions.set(location.key, position);
  const path = location.pathname + location.search + location.hash;
  const canonical = canonicalPath(path);
  if (canonical !== path) {
    window.history.replaceState(window.history.state, '', canonical);
    location = browser.location;
  }
  const index = window.history.state?.idx;
  if (Number.isInteger(index)) {
    if (action === 'PUSH') for (const key of knownEntries.keys()) if (key >= index) knownEntries.delete(key);
    knownEntries.set(index, { pathname: location.pathname, search: location.search });
  }
  const claimed = claiming || (action === 'POP' && returningTo !== null && index === returningTo);
  returningTo = null;
  seq += 1;
  if (!claimed) openEpoch += 1;
  if (action !== 'REPLACE') onBootEntry = false;
  current = { action, location, seq, openEpoch, claimed };
  for (const listener of [...listeners]) listener(current);
});

function canonicalTo(to: To): To {
  if (typeof to === 'string') return canonicalPath(new URL(to, window.location.href).href);
  const source = new URL(`${to.pathname ?? window.location.pathname}${to.search || ''}${to.hash || ''}`, window.location.href);
  const canonical = canonicalPath(source.href);
  if (canonical === source.pathname + source.search + source.hash) return to;
  const target = new URL(canonical, window.location.origin);
  return { ...to, pathname: target.pathname, search: target.search, hash: target.hash };
}

export const peachHistory: PeachHistory = {
  get navigation() { return current },
  listen(listener) {
    listeners.add(listener);
    return () => { listeners.delete(listener) };
  },
  createHref: (to) => browser.createHref(to),
  createURL: (to) => browser.createURL(to),
  encodeLocation: (to) => browser.encodeLocation(to),
  go: (delta) => browser.go(delta),
  push: (to, state) => browser.push(canonicalTo(to), state),
  replace: (to, state) => browser.replace(canonicalTo(to), state),
};

export interface ShellNavigateOptions {
  replace?: boolean;
  /** 新条目的 `usr` 状态。清理地址时传 `peachHistory.navigation.location.state`，原样保住当前那一份。 */
  state?: unknown;
}

/** 壳写地址：push 或 replace 一条，当场认领。
 *
 * 路径先按当前地址解析成绝对的：壳传进来的有相对路径、有只带查询串的，也有整条 href，
 * `history.pushState` 本来就是这样解析的；React Router 的 `To` 只认路径，`#x` 这种会丢掉查询串。 */
export function shellNavigate(path: string | URL, { replace = false, state }: ShellNavigateOptions = {}): void {
  const url = new URL(path, window.location.href);
  const to: To = canonicalPath(`${url.pathname}${url.search}${url.hash}`);
  claiming = true;
  try {
    if (replace) browser.replace(to, state);
    else browser.push(to, state);
  } finally {
    claiming = false;
  }
}

/** 当前条目是不是页面加载时的那一条（刷新、新标签页、深链）。 */
export const bootEntry = (): boolean => onBootEntry;

/** 当前浏览器条目之前有站内历史时才允许后退。 */
export const canGoBack = (): boolean => Number(window.history.state?.idx) > 0;

/** 关闭详情时跨过连续的详情条目，回到原来那一份页面，保留条目上的列表页数与位置。 */
export function returnToHistoryPage(path: string, { reopen = false }: { reopen?: boolean } = {}): boolean {
  const index = window.history.state?.idx;
  if (!Number.isInteger(index) || !isOverlayPath(current.location.pathname)) return false;
  const source = new URL(path, window.location.href);
  if (source.origin !== window.location.origin) return false;
  const target = new URL(canonicalPath(source.href), window.location.href);
  for (let at = index - 1; at >= 0; at -= 1) {
    const entry = knownEntries.get(at);
    if (!entry) return false;
    if (isOverlayPath(entry.pathname)) continue;
    if (entry.pathname !== target.pathname || entry.search !== target.search) return false;
    returningTo = reopen ? null : at;
    peachHistory.go(at - index);
    return true;
  }
  return false;
}

/** 后退前进返回的这一份页面，包括它已载入的列表页数。 */
export const historyPagePosition = (): PagePosition | undefined =>
  current.action === 'POP' ? pagePositions.get(current.location.key) : undefined;

export function rememberHistoryPages(pages: number): void {
  const key = current.location.key;
  pagePositions.set(key, { left: window.scrollX, top: window.scrollY, ...pagePositions.get(key), pages });
}

/** 页面完成显示后恢复阅读位置，避免骨架高度把位置截到第一页。 */
export function restoreHistoryPage(): boolean {
  const position = historyPagePosition();
  if (!position) return false;
  window.scrollTo({ left: position.left, top: position.top, behavior: 'instant' });
  return true;
}

/** 页面组按它匹配的背景：当前条目在 `usr` 里记的那一页。启动那一条不读（ADR-0031「详情条目记下压在哪一页
 *  上」）：`usr` 跨刷新仍在，下面那一页却从没画过，刷新、新标签页与深链落在详情上一律当作没有背景。 */
export function navigationBackground(): BackgroundLocation | null {
  if (isPreferencePath(current.location.pathname)) {
    const settings = onBootEntry ? null : settingsBackgroundOf(current.location.state);
    return settings ? backgroundOf(settings.state) || settings : null;
  }
  return onBootEntry ? null : backgroundOf(current.location.state);
}

export {
  adoptOverlayState, backgroundOf, clearOverlayBackground, holdOverlayBackground, isOverlayPath, OVERLAY_PATHS,
  overlayState, overlayTarget, takeOverlayReturn,
  type BackgroundLocation, type OverlayKind, type OverlayPath, type OverlayState, type OverlayTarget,
} from './overlay';
export { ROUTE_META, routeMetaOf, type RouteMeta } from './route-meta';
export {
  connectManagedRoutes, failManagedRoute, listenManagedEntry, managedEntries, managedEntry, managedTaken, openManagedRoute,
  openResidentSurface, preloadManagedRoutes, releaseAllManagedRoutes, releaseManagedRoute, updateManagedRoute,
  type ManagedEntry, type ManagedOpenOptions, type ManagedPrefetch,
} from './managed';
