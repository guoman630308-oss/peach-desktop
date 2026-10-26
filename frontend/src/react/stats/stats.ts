/* 统计页的数据契约与折算：`/api/stats` 一次请求供整页使用。
 *
 * 端点在 `frontend/src` 里只在这里声明一次（`tests/test_frontend_build.py` 盯着）。
 * 这一页是数据库当前的快照，没有后台任务也不轮询：整页只有一个 `queryKey`，一屏里的四个
 * 指标、下面那三个面板读的都是同一份数，分开取就会出现这一格是新的、那一格是旧的。
 *
 * 体积走 `src/core/index.ts` 的 `fmtSize`，与馆藏、重复项、高清版同一套口径；播放时长、百分比
 * 与图表的分档是这一页自己的折算，写成纯函数放在这里，由 vitest 直接验。 */
import { apiGet } from '../../api';
import type { BarRow } from '../charts/bar-card';
import type { ActivityCounts } from '../charts/heat';
import { queryClient } from '../query';

export const STATS_URL = '/api/stats';
/** 整页共用这一个键。 */
export const STATS_KEY = ['stats'] as const;

/** 按来源分的库存。`k` 是 `local`／`115`／`pikpak`／`online` 这些来源代号。 */
export interface LocationCount { k: string; n: number; bytes: number; videos: number }

/** 按媒体库分的库存。`name` 是用户给这个库起的名字。 */
export interface LibraryCount { k: string; name: string; icon: string; videos: number; bytes: number }

/** 一个计数。服务端某一项取不到时回 null，页面读成「未取得」。 */
export type Count = number | null;

/** 视频的资料归属：分母是 `videos`，其余几项是已经有那一项的条数。 */
export interface Attribution {
  videos: number; creator: Count; code: Count; studio: Count; thumb: Count; duration: Count;
}

/** 一个标签来源覆盖了多少条标签、多少个视频。 */
export interface TagSource { k: string; n: number; assets: number }

export interface TopTag { k: string; n: number; cat: string }

export interface Consumption {
  played: Count; library_played: Count; online_played: Count; play_seconds: Count;
  o_total: Count; liked: Count; dislike: Count; seen: Count; trash: Count; skimmed: Count;
}

/** 按文件类型分的条目数。`k` 是 `video`／`image`／`archive` 这些媒介代号。 */
export interface MediumCount { k: string; n: number; bytes: number }

/** 视频的一个时长或画质分档。 */
export interface BandCount { k: string; n: number }

/** 播放过 `k` 次的作品有 `n` 个。 */
export interface ReplayCount { k: number; n: number }

/** 最近一条播放记录。馆藏与在线追更两条来源合并后按时间排，`kind` 决定点进去去哪。 */
export interface RecentPlay {
  id: number; name: string; creator: string | null; play_seconds: number;
  duration: number | null; max_reached: number | null; o_count: number; kind: string;
}

/** 一个存储卷。离线或取不到容量时 `total` 是 null，此时不画使用率。 */
export interface StorageVolume {
  kind: string; label: string; root: string | null; online: boolean;
  free: number | null; used: number | null; total: number | null;
}

export interface StorageSummary {
  volumes: number; online: number; measured: number; free: number; used: number; total: number;
}

/** `/api/stats` 的响应。字段与 `web_stats.q_stats` 对齐。 */
export interface StatsData {
  by_loc: LocationCount[];
  by_medium: MediumCount[];
  by_library: LibraryCount[];
  by_length: BandCount[];
  by_quality: BandCount[];
  play_activity: ActivityCounts;
  replays: ReplayCount[];
  attribution: Attribution;
  tag_source: TagSource[];
  tag_cov: Count;
  top_tags: TopTag[];
  consumption: Consumption;
  recent: RecentPlay[];
  storage_volumes: StorageVolume[];
  storage_summary: StorageSummary;
}

export const fetchStats = (signal?: AbortSignal) => apiGet<StatsData>(STATS_URL, signal);

/** 首屏：取完数才画。
 *
 * 不给 `staleTime`。这一页读的是数据库此刻的样子，每进一次都该重新问一遍；缓存住的话，
 * 扫完一批回来看到的还是进页面之前那个数。 */
export async function prefetchStats(signal: AbortSignal): Promise<void> {
  await queryClient.fetchQuery({ queryKey: STATS_KEY, queryFn: () => fetchStats(signal) });
}

/** 取得了的计数：null、NaN 与无穷都不算。 */
export const known = (value: Count | undefined): value is number =>
  value != null && Number.isFinite(value);

/** 一个计数的读法：千分位；取不到时读「未取得」。 */
export const fmtCount = (value: Count | undefined): string =>
  (known(value) ? value.toLocaleString() : '未取得');

/** 整数百分比，夹在 0 到 100 之间。分母是 0 或取不到时读 0，不是 NaN。 */
export const percentOf = (value: Count | undefined, total: Count | undefined): number =>
  (known(value) && known(total) && total > 0
    ? Math.min(100, Math.max(0, Math.round(value / total * 100))) : 0);

/** 百分比的读法：有一点但不到 0.5% 时读「<1%」，不读成 0%。 */
export const percentText = (value: Count | undefined, total: Count | undefined): string => {
  const share = percentOf(value, total);
  return !share && known(value) && value > 0 && known(total) && total > 0 ? '<1%' : `${share}%`;
};

/** 累计播放时长：不到一小时读分钟，不到 48 小时读小时，再往上读天。 */
export const playedFor = (seconds: Count | undefined): string => {
  if (!known(seconds)) return '未取得';
  const value = Math.max(0, seconds);
  if (value >= 48 * 3600) {
    return `${(value / 86_400).toLocaleString(undefined, { maximumFractionDigits: 1 })} 天`;
  }
  return value >= 3600 ? `${(value / 3600).toFixed(1)} 小时` : `${Math.round(value / 60)} 分钟`;
};

/** 真实看过的比例：播放秒数占时长，封顶 100%。时长未取得时读 0。 */
export const watchedShare = (row: RecentPlay): number =>
  row.duration ? Math.min(row.play_seconds / row.duration, 1) * 100 : 0;

/** 到达过的最远位置，百分比。 */
export const reachedShare = (row: RecentPlay): number => (row.max_reached || 0) * 100;

/** 这一条读出来是哪一种观看。
 *
 * 真实看过的比例比到达位置低一大截，说明中间大段是拖过去的——那和从头看到尾是两回事，
 * 一行里只显示一个百分比的话读者分不出来。 */
export const watchNote = (row: RecentPlay): string => {
  if (row.kind === 'online') return '在线直接观看';
  if (watchedShare(row) < reachedShare(row) - 25) return '快进扫过';
  return row.o_count ? `高潮 ${row.o_count}` : '正常观看';
};

/** 点进去的去处。在线追更的条目不在馆藏里，走它自己那条详情路由。 */
export const playedItemUrl = (row: RecentPlay): string =>
  `${row.kind === 'online' ? '/posts/' : '/assets/'}${row.id}`;

/** 径向图里的一段。`bytes` 是这一段占的体积，图例格子里读成它的大小。 */
export interface RadialSlice { name: string; value: number; bytes?: number; mark?: string }

/** 一张径向图最多画几圈：图表色只有六档，第七圈起颜色就和前面的重了。 */
export const RADIAL_MAX = 6;

/** 径向图要画的几段。段数超过 `RADIAL_MAX` 时留下最多的前五段，其余并成一段「其余 N 项」。
 *  负数与非有限的值先滤掉。 */
export function radialSlices(rows: RadialSlice[], max = RADIAL_MAX): RadialSlice[] {
  const slices = rows.filter((row) => Number.isFinite(row.value) && row.value >= 0);
  if (slices.length <= max) return slices;
  const ranked = [...slices].sort((a, b) => b.value - a.value);
  const rest = ranked.slice(max - 1);
  return [...ranked.slice(0, max - 1), {
    name: `其余 ${rest.length.toLocaleString()} 项`,
    value: rest.reduce((sum, row) => sum + row.value, 0),
    bytes: rest.reduce((sum, row) => sum + (row.bytes ?? 0), 0),
  }];
}

/** 满圈代表的数：最长那段留一成余量，看得出是「最多」而不是「全部」。 */
export const radialCeiling = (values: number[]): number => Math.max(1, ...values) * 1.1;

/** 一段画多长的弧：不为 0 的段至少画满圈的 2%，和最长那段差几个数量级时也看得见。
 *  0 就是 0，不垫高。 */
export const radialArc = (value: number, ceiling: number): number =>
  (value > 0 ? Math.max(value, ceiling * 0.02) : 0);

/** 每圈的粗细（像素）。段数多时压细，最内圈才不会缩进圆心，也不会和相邻那圈叠在一起。 */
export const radialBarSize = (count: number): number =>
  Math.max(4, Math.min(14, Math.round(90 / Math.max(1, count))));

/** 文件类型的显示名。与馆藏里资源卡片的叫法相同。 */
const MEDIUM_LABEL: Record<string, string> = {
  video: '视频', image: '图片', illustration: '插画', archive: '压缩包',
  audio: '音频', account: '账号', other: '其它文件',
};

/** 按文件类型分的条目数，多的在前。 */
export const mediumRows = (rows: MediumCount[]): BarRow[] =>
  rows.map((row) => ({ name: MEDIUM_LABEL[row.k] ?? row.k, value: row.n }))
    .sort((a, b) => b.value - a.value);

/** 时长分档的显示名。分界同 `media_probe.context_fields`：300、900、2400 秒。 */
const LENGTH_LABEL: Record<string, string> = {
  速食: '<5 分钟', 短: '5–15 分钟', 中: '15–40 分钟', 长: '>40 分钟',
};

/** 时长与画质分档，顺序由服务端给。时长换成分钟区间，画质原样。 */
export const bandRows = (rows: BandCount[], labels: Record<string, string> = {}): BarRow[] =>
  rows.map((row) => ({ name: labels[row.k] ?? row.k, value: row.n }));

export const lengthRows = (rows: BandCount[]): BarRow[] => bandRows(rows, LENGTH_LABEL);

/** 播放次数的分档：五次以内一次一档，往后并成两档，长尾不把柱子拉成一排细线。
 * 名字只写次数，单位在图的标题里：320 宽的手机上七根柱子的类别名不互相挤掉。 */
const REPLAY_BANDS: readonly [number, number, string][] = [
  [1, 1, '1'], [2, 2, '2'], [3, 3, '3'], [4, 4, '4'], [5, 5, '5'],
  [6, 9, '6–9'], [10, Infinity, '≥10'],
];

export const replayRows = (rows: ReplayCount[]): BarRow[] =>
  REPLAY_BANDS.map(([low, high, name]) => ({
    name,
    value: rows.filter((row) => row.k >= low && row.k <= high).reduce((sum, row) => sum + row.n, 0),
  }));
