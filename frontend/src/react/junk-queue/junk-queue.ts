/* 垃圾文件队列（`CATALOG_ROUTES['/junk-files']`）的数据与对外契约。
 *
 * 队列一次取 200 条（`/api/ads?limit=200&status=…[&kind=…]`，`src/peach/web_batch.py` 的 `q_ads`
 * 现算评分），客户端按「每批条数」一段段露出来；换分类、换视图就是换查询键。写操作只有三种，
 * 都走 `/api/batch`，由壳执行（回执、撤销与重读都归壳），做完壳推进 `revision`，查询换键重取。 */
import { apiGet } from '../../api';
import type { JunkKind, JunkRoute, JunkView } from '../../junk-queue';

export type { JunkKind, JunkRoute, JunkView };

/** 队列一次取多少条。和遗留层同一个数：评分是现算的，页越大越慢。 */
export const JUNK_LIMIT = 200;

/** `/api/ads` 的一条。只列卡片读到的字段，其余原样带着，壳的缓存要的是整条。 */
export interface JunkItem {
  id: number;
  name?: string;
  junk_kind?: string;
  why?: string;
  size?: number | null;
  location?: string;
  cost?: string;
  [field: string]: unknown;
}

/** `/api/ads` 的回话：`total` 是这一视图、这一类的条数，`all_total` 是这一视图各类合计，
 *  `counts` 按类计数，`dismissed_total` 给分类条末尾那一项的徽标。 */
export interface JunkPage {
  items: JunkItem[];
  total?: number;
  all_total?: number;
  pending_total?: number;
  dismissed_total?: number;
  counts?: Partial<Record<string, number>>;
}

export type JunkOperation = 'dismiss-junk' | 'reconsider-junk' | 'dispose';

/** 卡片上的类型名与字形。网址那一类在卡上写全称。 */
export const JUNK_KIND_META: Record<string, readonly [string, string]> = {
  video: ['视频', 'play'], image: ['图片', 'pics'], archive: ['压缩包', 'file-archive'],
  audio: ['音频', 'file-audio'], url: ['网址快捷方式', 'globe'], other: ['其它文件', 'hard-drive'],
};
export const junkKindMeta = (item: JunkItem) => JUNK_KIND_META[item.junk_kind || 'other'] || JUNK_KIND_META.other!;

/** 中间那颗判断键：待判断视图里是「不是垃圾」，已排除视图里是「重新判断」。 */
export function junkDecision(view: JunkView): { operation: JunkOperation; label: string; glyph: string } {
  return view === 'dismissed'
    ? { operation: 'reconsider-junk', label: '重新判断', glyph: 'rotate-ccw' }
    : { operation: 'dismiss-junk', label: '不是垃圾', glyph: 'check' };
}

/** 分类条上一项的计数：「全部」读这一视图的合计，其余读各类。 */
export const junkCount = (page: JunkPage, kind: JunkKind): number =>
  Number((kind ? page.counts?.[kind] : page.all_total) || 0);

export const junkQueryKey = (route: JunkRoute, revision: number) =>
  ['junk-queue', route.view, route.kind, revision] as const;

export function junkQueryString({ kind, view }: JunkRoute): string {
  const params = new URLSearchParams({ limit: String(JUNK_LIMIT), status: view });
  if (kind) params.set('kind', kind);
  return params.toString();
}

export async function fetchJunkPage(route: JunkRoute, signal: AbortSignal): Promise<JunkPage> {
  const page = await apiGet<JunkPage>(`/api/ads?${junkQueryString(route)}`, signal);
  return { ...page, items: page.items || [] };
}

/** 卡片上仍由遗留层拼的那一段：来源角标（遗留层 `srcBadge`）。 */
export interface JunkQueueHelpers {
  badgeHtml(location: string, cost: string): string;
}

/** 卡片与分类条上的动作。写 ledger、打开详情与换页都归壳。 */
export interface JunkQueueActions {
  /** 分类条上的一项被点了（普通左键）：壳收起多选、改地址、重读。修饰键与中键照浏览器默认。 */
  navigate(path: string): void;
  /** 切换一张的选中；`range` 是按住 Shift 连选。 */
  toggleSelection(id: number, range: boolean): void;
  /** 点标题打开：视频进详情，本地图片在新标签页看原图。 */
  open(item: JunkItem, anchor: HTMLElement): void;
  /** 「打开位置」：在资源管理器里显示。回话是要写在卡片状态行里的一句，成功为空。 */
  reveal(item: JunkItem): Promise<string>;
  /** 三颗处置键。写 ledger，做完重读队列并给撤销；失败由壳报，再抛出。 */
  operate(item: JunkItem, operation: JunkOperation): Promise<void>;
}

export interface JunkQueueProps extends JunkRoute {
  helpers: JunkQueueHelpers;
  actions: JunkQueueActions;
  /** 每一段露多少张（设置里的「每批条数」）。 */
  batchSize: number;
  /** 刷新代次：壳要求重读时加一，查询随之换键重取。换代时旧的一份留在屏上，不重铺骨架。 */
  revision: number;
  selectMode: boolean;
  selected: ReadonlySet<number>;
  /** 计数行（`#count`）。行本身归壳（换页时由壳清空），里面的摘要与分类条归这一页。 */
  countRow: HTMLElement | null;
  /** 网格骨架的 HTML（遗留层 `pageSkeletonHtml`），和壳首屏铺的是同一份。 */
  skeletonHtml(): string;
  /** 一次取数落定（成功、为空或失败）：`revision` 是那一次的代次，壳据此放行等着它的调用方。 */
  settled?(revision: number): void;
  /** 此刻能不能往下露下一段：管理区或索引页盖在上面时不露。 */
  canLoadMore?(): boolean;
}
