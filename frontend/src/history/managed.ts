/* 由路由画的那几页与页面里的页面内组件（ADR-0031「页面与页面里的页面内组件只由路由树画」）：壳决定什么时候开、
 * 什么时候收，React 路由树画。
 *
 * 页面画进壳的三个容器：管理区正文 `#stats`，索引页与资料页共用的 `#index`，目录网格与垃圾队列共用的
 * `#grid`。页面内组件各有自己的容器：首页筛选条 `#catalogFilter`、首页新作行 `#feedNew`、
 * 顶栏搜索下拉 `#searchMenu`。一个容器同时只有一页（或一面），各个容器各记各的：
 * 资料页画进 `#index` 时，管理区那一页只是被壳藏起来、照旧活着（有轮询的页面照着原节律取数），等壳下一次
 * 认领表面（`claimSurface`）才把 `#stats` 与 `#index` 一起收；`#grid` 不在其中，目录内换筛选也认领表面，网格
 * 要一直画着；页面内组件由壳按各自的路由判据收，搜索下拉壳从不收。页面跟着这里登记的那一条走，不跟着
 * 地址匹配走：详情舞台压在页面上时地址是 `/assets/:id`，页面要留着。
 *
 * 常驻组件（批量条、配色卡、管理区页头、沉浸模式、设置面板、侧栏、舞台）不跟某一页走，经 `openResidentSurface` 打开：
 * 没有首屏取数、一直算当前页，宿主就是那个常驻节点。它们从不收；只有错误边界会卸掉它的组件，而且只卸组件、
 * 不撤宿主。
 *
 * 登记键是「面」：页面用路径（`/stats`、`/performers/*`、`/`），页面内组件与常驻组件用名字（`search`、
 * `batch-dock`），两者不重叠，路由树按键查同一组表（`react/router/managed-routes.tsx`）。
 *
 * 一次打开：壳铺好骨架后调 `openManagedRoute`，这里先收起同一容器里的上一页，再取首屏；取齐、壳也还停在
 * 这一页时，在同一个任务里换掉骨架、放进宿主，再让路由树同步画出整页。路由树还没接上时，打开先等它接上
 * 再取数（搜索下拉在壳的模块求值时就打开，早于 `loadRouter`）。每次打开都领一个新代次，页面按代次重挂、
 * 重取。打开之后壳的开关（选择模式、换筛选、换版式）经 `updateManagedRoute` 合进同一页的 props：代次不变，
 * 页面就地重渲染，不重挂也不重取。 */

/** 画在某个容器里的那一页（或一面）。`host` 是页面 portal 进去的那个 `.peach-react` 宿主。 */
export interface ManagedEntry {
  /** 登记键：页面的路径，或页面内组件的名字。 */
  path: string;
  props: object;
  revision: number;
  /** 壳的容器（`#stats`、`#index`、`#grid` 或某个页面内组件的容器）。 */
  container: Element;
  host: HTMLElement;
  /** 常驻组件：宿主是壳的节点，收起与抛错时只卸组件、不撤宿主。 */
  resident: boolean;
}

/** 路由树交给这里的首屏取数：按登记键取，中止后抛 `AbortError`。 */
export type ManagedPrefetch = (path: string, props: object, signal: AbortSignal) => Promise<void>;

export interface ManagedOpenOptions {
  /** 页面画进的容器。 */
  container: Element;
  /** 壳的换页判据：取数期间用户走开了，就不画。 */
  isCurrent: () => boolean;
  /** 壳自己排好的框架：取齐首屏的那一刻由它把框架换进容器，返回页面画进去的宿主。不给就清空容器、
   *  放进一个新的 `.peach-react`。资料页要这个：浮层吸顶要它的父盒就是 `#index`，新作那一行是遗留层
   *  卡片、不进 `.peach-react`，所以框架的四块由壳排，页面只占其中一块，再经 portal 画进另外三块。 */
  place?: (container: Element) => HTMLElement;
  /** 常驻组件（`openResidentSurface` 打开的那几座）：宿主归壳，收起与抛错时只卸组件、不撤宿主。 */
  resident?: boolean;
}

/** 订阅者收到的是此刻各个容器里的页面。`sync` 为真时要当场画完：壳在同一个任务里刚换上或撤掉宿主，
 *  晚一拍就是一帧空白。为假是就地更新，照常排进下一次渲染。 */
type ManagedListener = (entries: readonly ManagedEntry[], sync: boolean) => void;

interface Pending { revision: number; controller: AbortController }

/* 路由树接上取数之前，打开都等在这里；接上的那一份装载失败时，等着的打开跟着失败。 */
let connect: (ready: Promise<ManagedPrefetch>) => void = () => {};
const prefetcher = new Promise<ManagedPrefetch>((resolve) => { connect = resolve });
prefetcher.catch(() => {});
/* 首次打开时调用应用登记的就绪入口，让搜索下拉与首屏筛选条等待路由树连接后取数。
 * 页面代码已在主包中，就绪入口只调用一次。 */
let preload: (() => void) | null = null;
export function preloadManagedRoutes(load: () => void): void {
  preload = load;
}
const entries = new Map<Element, ManagedEntry>();
const pending = new Map<Element, Pending>();
let revision = 0;
const listeners = new Set<ManagedListener>();

/** 路由树装载时接上取数。只认第一次。 */
export function connectManagedRoutes(ready: Promise<ManagedPrefetch>): void {
  connect(ready);
}

/** 此刻画在这个容器里的那一页；还在取首屏或已经收起时是 null。 */
export function managedEntry(container: Element): ManagedEntry | null {
  return entries.get(container) ?? null;
}
/** 这个容器归路由树：已经画着，或首屏还在取。壳据此判断要不要再开一次：在途时再开会把那一趟中止、
 *  重取一遍，画着时再开会先收起再重画，内容根本没变时那一下只是一次白白的布局塌陷。 */
export function managedTaken(container: Element | null): boolean {
  return !!container && (entries.has(container) || pending.has(container));
}
/** 此刻画着的全部页面，路由树按它画 portal。 */
export const managedEntries = (): readonly ManagedEntry[] => [...entries.values()];

/** 路由树订阅：每次换页、收起或就地更新都同步通知。 */
export function listenManagedEntry(listener: ManagedListener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener) };
}

function notify(sync: boolean): void {
  const shown = managedEntries();
  for (const listener of [...listeners]) listener(shown, sync);
}

/** 打开一页并等首屏落地。画上了回 true；期间被收起、被重开或壳已换页回 false。 */
export async function openManagedRoute(path: string, props: object, options: ManagedOpenOptions): Promise<boolean> {
  const { container } = options;
  releaseManagedRoute(container);
  if (!options.isCurrent()) return false;
  revision += 1;
  const mine: Pending = { revision, controller: new AbortController() };
  pending.set(container, mine);
  const load = preload;
  preload = null;
  load?.();
  const prefetch = await prefetcher;
  if (pending.get(container) !== mine) return false;
  try {
    await prefetch(path, props, mine.controller.signal);
  } catch {
    // 中止就是用户已经走开。其余失败照画：原因和重试的节律都在页面自己手里。
    if (mine.controller.signal.aborted) return false;
  }
  if (pending.get(container) !== mine) return false;
  pending.delete(container);
  if (!options.isCurrent()) return false;
  const host = options.place ? options.place(container) : defaultHost(container);
  entries.set(container, { path, props, revision: mine.revision, container, host, resident: !!options.resident });
  notify(true);
  return true;
}

/** 打开一座常驻组件（常驻表 `RESIDENT_ROUTES` 里的名字）：一直算当前页，宿主就是壳交来的节点本身，组件直接画成
 *  它的子节点，所以 DOM 与这座面自己建根时一样。没有首屏取数，路由树接上就画；画上了回 true。
 *
 *  `place` 在画首帧的同一个任务里把宿主交给路由树，返回的仍须是这个节点本身；不给就原样交出。宿主里先有
 *  壳的启动骨架时（管理区页头、侧栏），由它清掉骨架：骨架一直留到首帧画上的那一刻，中间没有一帧空白。宿主是
 *  新建、还不在文档里的节点时（沉浸模式、设置面板），由它挂进文档，节点与首帧一起出现。
 *
 *  每座只在壳启动（或第一次用到）时打开一次。之后壳经它的命令式句柄推内容，组件订阅自己的 store；
 *  抛错后错误边界只卸组件，这一面空到刷新为止，句柄照旧可调、不抛。 */
export function openResidentSurface(
  name: string, container: HTMLElement, place: (container: HTMLElement) => HTMLElement = () => container,
): Promise<boolean> {
  return openManagedRoute(name, {}, { container, isCurrent: () => true, place: () => place(container), resident: true });
}

/* token、Preflight 与焦点规则都作用在 `.peach-react` 上；容器归壳，所以另建一个，收起时连它一起撤掉。 */
function defaultHost(container: Element): HTMLElement {
  const host = container.ownerDocument.createElement('div');
  host.className = 'peach-react';
  container.textContent = '';
  container.append(host);
  return host;
}

/** 把壳的开关合进此刻画着的那一页：代次不变，页面就地重渲染，不重挂、不重取。那个容器里还没画上
 *  （还在取首屏或已经收起）时是空操作，打开时交进去的那一份照旧。 */
export function updateManagedRoute(container: Element | null, patch: object): void {
  const shown = container ? entries.get(container) : undefined;
  if (!shown) return;
  entries.set(shown.container, { ...shown, props: { ...shown.props, ...patch } });
  notify(false);
}

/** 收起这几个容器里的页面：中止在途的首屏取数，卸掉页面并撤掉宿主，一次通知。没有页面的容器是空操作。
 *  容器逐个点名，没有「全部收起」：壳换页认领表面时收 `#stats` 与 `#index`，`#grid` 在目录各路径与回收站
 *  之间一直画着，只在离开目录时由壳单独收；页面内组件由壳按各自的路由判据收。常驻组件的宿主是壳的节点，
 *  这里只卸组件、不撤宿主。 */
export function releaseManagedRoute(container: Element, ...more: Element[]): void {
  const removed: ManagedEntry[] = [];
  for (const target of new Set([container, ...more])) {
    const inflight = pending.get(target);
    if (inflight) {
      inflight.controller.abort();
      pending.delete(target);
    }
    const shown = entries.get(target);
    if (shown) {
      entries.delete(target);
      removed.push(shown);
    }
  }
  if (!removed.length) return;
  notify(true);
  for (const shown of removed) if (!shown.resident) shown.host.remove();
}

/** 应用卸载收起全部登记，并中止尚未成为页面的首屏请求。 */
export function releaseAllManagedRoutes(): void {
  const [first, ...rest] = new Set([...entries.keys(), ...pending.keys()]);
  if (first) releaseManagedRoute(first, ...rest);
}

/** 某一面渲染抛错、路由树的错误边界接住之后调：只撤这一面的登记与宿主，别的面照画。之后
 *  `managedTaken` 回 false、`updateManagedRoute` 对它是空操作，壳下一次打开就重开。代次对不上（这一格
 *  已经收起或重开过）是空操作：过期的那一面不能撤掉壳刚开的新一面。常驻组件的宿主是壳的节点，只卸组件、
 *  不撤宿主；壳不会再打开它，这一面空到刷新为止。
 *
 *  删登记与撤宿主当场做，通知延到微任务：这里在 React 的提交阶段里（错误边界的 `componentDidCatch`），
 *  通知会让路由树重渲染。抛错出在打开那一次的首帧时，打开照样回 true，只是回来的那一刻登记已经撤了，
 *  壳紧接着推的补丁是空操作。 */
export function failManagedRoute(container: Element, failed: number): void {
  const shown = entries.get(container);
  if (shown?.revision !== failed) return;
  entries.delete(container);
  if (!shown.resident) shown.host.remove();
  queueMicrotask(() => { notify(false) });
}
