/* 壳自己的内存状态（ADR-0031「壳自己的内存状态也只有一份」）。
 *
 * 目录口径、选择集与选择模式、详情与关注详情「关掉回哪里」的来处、跳到配置页某一节与活动页预填这类
 * 一次性请求，整页只有这一份：Application 与 React 页面直接引用共享源码，随 `peach-app.js` 发出。读法就是
 * ES 模块的活绑定，导入方读到的永远是这里的当前值；导入方不能给绑定赋值，整体换掉一个值走
 * `writeShell`。
 *
 * 对象语义照壳原样保留：整体重建的写点经 `writeShell` 换引用，其余写点原地改同一个对象的字段，
 * 改完调 `notifyShell`。`barsContext.filters` 与 `state` 的别名关系由写点自己决定，这里不替谁修：
 * `writeShell({state})` 只换 `state`，`barsContext.filters` 仍指旧对象，直到写点再把 `barsContext`
 * 指回来。选择集是两个常驻的 `Set`，原地增删。
 *
 * 每次写入之后通知订阅者，版本号单调递增，React 读者按 `useSyncExternalStore(subscribeShell, shellVersion)`
 * 接。服务端事实（来源在线状态、目录总数、聚合、实体形态、回收站计数）不在这里，归 TanStack Query；
 * 壳判过期的代次计数器也不在这里。 */

/** 目录筛选口径：键与地址栏上的查询参数同名（`loc`、`creator`、`tag`、`state`、`sort`、`seed`…）。 */
export type CatalogFilters = Record<string, string>;

/** 顶部三层与筛选条此刻画的是哪一套筛选：首页那份 `state`、某个资料页，或作品详情。 */
export interface BarsContext {
  type: 'home' | 'entity' | 'item';
  filters: CatalogFilters;
  kind?: string;
  name?: string;
  id?: number;
}

/** 此刻开着的队列，只用来判「是不是同一个队列里换一条」；队列的条目归详情岛。 */
export interface ActiveQueue {
  kind: string;
  seedId?: number;
  playlistId?: number;
}

/** 详情岛交回来的那一条作品；壳只认 `id`，其余字段原样留着。 */
export interface PresentedItem {
  id: number;
  [key: string]: unknown;
}

/** 目录筛选状态。启动时由壳按启动地址与保存过的设置写入，尚未写入时为 undefined。 */
export let state: CatalogFilters | undefined;
/** 顶部三层与筛选条的语境；启动时由壳指向首页那份 `state`。 */
export let barsContext: BarsContext | undefined;
/** 作品详情打开前的那份语境，关掉详情时还原。 */
export let detailReturnBarsContext: BarsContext | null = null;

/** 目录（含资料页、回收站、垃圾文件）与关注页各一份选择集，整页常驻、原地增删。 */
export const selected = new Set<number>();
export const followSelected = new Set<number>();
export let selectMode = false;
/** Shift 连选的起点：目录与关注页各一个。 */
export let lastSelectedId: number | null = null;
export let followLastSelectedId: number | null = null;
/** 进选择模式时所在的那一类页面（`catalog`、`follow`、`junk`）；换到别的一类就退出选择。 */
export let selectSurface = '';

/** 关掉作品详情回到的地址。 */
export let detailReturnPath = '/';
/** 点开详情的那张卡，以及它当时在视口下半还是上半；之后打开详情没带卡片时沿用这一份。 */
export let detailOriginAnchor: Element | null = null;
export let detailOriginAbove = false;
/** 关掉详情时要不要照地址重建来处：深链直接进的详情下面没画过列表。 */
export let detailReturnNeedsRestore = false;
export let activeQueue: ActiveQueue | null = null;
/** 队列地址的前缀：停在哪一条要等详情岛定下来，画出来那一刻才推。 */
export let pendingQueueRoute: string | null = null;
/** 详情岛最近一次画出来的那一条。 */
export let presentedItem: PresentedItem | null = null;
/** 关掉关注详情回到的地址。 */
export let followDetailReturnPath = '/follow';

/** 配置页下一次打开要选中的那一组页签名，选中之后清空。 */
export let configurationRequestedSection = '';
/** 本机能不能改配置（`/healthz` 的 `configurable`）：null 是还没问过，问到之前按不能算。 */
export let runtimeConfigurable: boolean | null = null;
/** 这一次是不是从设置完成页（或教程卡）进来的：口味页打开时取走，取一次就清。 */
export let cameFromSetup = false;

/** 壳要路由树按地址打开页面的次数。壳开始路由那一次记 1：在那之前来源、设置与形状名单还没读完，路由树里按匹配
 *  挂上的页面元素只挂着、不打开。之后壳每要求一次「按当前地址把这一页从头重开」（批量写回之后、设置变了要重取、
 *  点开的正是画着的那一位）就加一。后退前进与跨页由页面打开序号（`@peach/history` 的 `openEpoch`）管，不经这里。 */
export let pageOpens = 0;
/** 队列打开意图的代次：真实条目尚未取齐，地址由详情首屏确定。 */
export let queueOpens = 0;
/** 资料页按第一页作品推出来的 JAV 语境（壳的 `javActive` 读）：页面推上来，资料页每打开一次先清掉。 */
export let entityJavLayout = false;
/** 关注页这一次进入的取样种子：创作者、题材、标签三排露出哪些由它定，列表按它取样。重新进入与「换一批」时重掷。 */
export let followDiscoverySeed = Math.floor(Math.random() * 0xffffffff);
/** 关注页的刷新代次：画着时要求重读（回到这一页、批量标记之后）推一个新代次让列表重取，不重挂。 */
export let followRevision = 0;
/** 关注列表停在哪：停在 `/follow` 上滚动时随手记下，列表要从头画时照它滚回去；重新进入时清零。 */
export let followScrollY = 0;
/** 播放列表页的刷新代次：停在这一页时要求重读（顶栏「换一批」）推一个新代次，页面重取、不重挂。 */
export let playlistsRevision = 0;

/** `writeShell` 能整体换掉的那几项。两个选择集是常驻实例，不在其中。 */
export interface ShellFields {
  state: CatalogFilters | undefined;
  barsContext: BarsContext | undefined;
  detailReturnBarsContext: BarsContext | null;
  selectMode: boolean;
  lastSelectedId: number | null;
  followLastSelectedId: number | null;
  selectSurface: string;
  detailReturnPath: string;
  detailOriginAnchor: Element | null;
  detailOriginAbove: boolean;
  detailReturnNeedsRestore: boolean;
  activeQueue: ActiveQueue | null;
  pendingQueueRoute: string | null;
  presentedItem: PresentedItem | null;
  followDetailReturnPath: string;
  configurationRequestedSection: string;
  pageOpens: number;
  queueOpens: number;
  entityJavLayout: boolean;
  followDiscoverySeed: number;
  followRevision: number;
  followScrollY: number;
  playlistsRevision: number;
  runtimeConfigurable: boolean | null;
  cameFromSetup: boolean;
}

type Listener = () => void;
const listeners = new Set<Listener>();
let version = 0;

/** 整体换掉补丁里出现的那几项（值是 undefined 也照写），然后通知一次。只换绑定，不碰别的项。 */
export function writeShell(patch: Partial<ShellFields>): void {
  const has = (key: keyof ShellFields) => Object.prototype.hasOwnProperty.call(patch, key);
  if (has('state')) state = patch.state;
  if (has('barsContext')) barsContext = patch.barsContext;
  if (has('detailReturnBarsContext')) detailReturnBarsContext = patch.detailReturnBarsContext!;
  if (has('selectMode')) selectMode = patch.selectMode!;
  if (has('lastSelectedId')) lastSelectedId = patch.lastSelectedId!;
  if (has('followLastSelectedId')) followLastSelectedId = patch.followLastSelectedId!;
  if (has('selectSurface')) selectSurface = patch.selectSurface!;
  if (has('detailReturnPath')) detailReturnPath = patch.detailReturnPath!;
  if (has('detailOriginAnchor')) detailOriginAnchor = patch.detailOriginAnchor!;
  if (has('detailOriginAbove')) detailOriginAbove = patch.detailOriginAbove!;
  if (has('detailReturnNeedsRestore')) detailReturnNeedsRestore = patch.detailReturnNeedsRestore!;
  if (has('activeQueue')) activeQueue = patch.activeQueue!;
  if (has('pendingQueueRoute')) pendingQueueRoute = patch.pendingQueueRoute!;
  if (has('presentedItem')) presentedItem = patch.presentedItem!;
  if (has('followDetailReturnPath')) followDetailReturnPath = patch.followDetailReturnPath!;
  if (has('configurationRequestedSection')) configurationRequestedSection = patch.configurationRequestedSection!;
  if (has('pageOpens')) pageOpens = patch.pageOpens!;
  if (has('queueOpens')) queueOpens = patch.queueOpens!;
  if (has('entityJavLayout')) entityJavLayout = patch.entityJavLayout!;
  if (has('followDiscoverySeed')) followDiscoverySeed = patch.followDiscoverySeed!;
  if (has('followRevision')) followRevision = patch.followRevision!;
  if (has('followScrollY')) followScrollY = patch.followScrollY!;
  if (has('playlistsRevision')) playlistsRevision = patch.playlistsRevision!;
  if (has('runtimeConfigurable')) runtimeConfigurable = patch.runtimeConfigurable!;
  if (has('cameFromSetup')) cameFromSetup = patch.cameFromSetup!;
  notifyShell();
}

/** 原地改了 `state`、`barsContext` 的字段或选择集之后调它：版本号加一，通知全部订阅者。 */
export function notifyShell(): void {
  version += 1;
  for (const listener of [...listeners]) listener();
}

/** 订阅每一次写入；返回退订函数。 */
export function subscribeShell(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener) };
}

/** 单调递增的版本号，每次写入加一。 */
export const shellVersion = (): number => version;
