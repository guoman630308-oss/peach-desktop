/* 数据管理页的数据契约。
 *
 * 这一页是「库里已经有的东西怎么收拾」的唯一入口。顶上一排读数各是一条内容在库里的经过：
 * 先把它说清楚（人工复核、高清版），再把不该留的挑出去（重复文件、垃圾文件），最后是删掉的
 * 东西还在哪儿（回收站）。
 *
 * 五份读数里有两份不归这一页所有：高清版读 `QUALITY_GOALS_KEY`、重复文件读 `DUPLICATES_KEY`，
 * 同一个数在两页上永远是同一份。这里只声明人工复核的计数、垃圾文件与回收站。
 * 复核计数不读 `REVIEW_KEY`：那一把是整条队列，这里只要 `?counts=1` 那几个数，响应形状不同，
 * 是另一份资源。
 *
 * 各卡各自失败各自算：复核接口出错不该把回收站那张卡也变成「读取失败」，所以是五个
 * `useQuery`，不是一次 `Promise.all`。 */
import type { QueryKey } from '@tanstack/react-query';

import { apiGet } from '../../api';
import { DUPLICATES_KEY, fetchDuplicates } from '../duplicates/duplicates';
import { prefetchLibraryProcessing } from '../library-processing/library-processing';
import { prefetchMediaRepair } from '../media-repair/media-repair';
import { prefetchMediaSources } from '../media-sources';
import { fetchQualityGoals, QUALITY_GOALS_KEY } from '../quality-goals/quality-goals';
import { queryClient } from '../query';
import { REVIEW_LABELS, type ReviewCategory } from '../review/review';
import {
  fetchLinkCheck, fetchLinkPrune, fetchLinkStats, LINK_CHECK_KEY, LINK_PRUNE_KEY, LINKS_KEY,
} from './links';
import { prefetchOrganize } from './organize';
import { prefetchResourceSync } from './resource-sync';

export const REVIEW_COUNTS_URL = '/api/review?counts=1';
export const REVIEW_COUNTS_KEY = ['review', 'counts'] as const;

/** 垃圾文件：只要待判断的总数与分项，一条都不用取回来。 */
export const JUNK_URL = '/api/ads?limit=1';
export const JUNK_KEY = ['junk-summary'] as const;

/** 回收站：目录那张网格的同一条端点，钉死 `state=trash` 只为拿总数与占用。 */
export const TRASH_URL = '/api/items?state=trash&limit=1';
export const TRASH_KEY = ['trash-summary'] as const;

/** 垃圾判断的分类与界面上的名字。次序就是分项那一行的次序（遗留层 `JUNK_KIND_OPTIONS`）。 */
export const JUNK_KINDS: [string, string][] = [
  ['video', '视频'], ['image', '图片'], ['archive', '压缩包'],
  ['audio', '音频'], ['url', '网址'], ['other', '其它'],
];

export interface ReviewCounts { counts: Partial<Record<ReviewCategory, number>> }

export interface JunkSummary {
  pending_total: number;
  dismissed_total: number;
  counts: Record<string, number>;
}

export interface TrashSummary { total: number; bytes: number }

export const fetchReviewCounts = (signal?: AbortSignal) => apiGet<ReviewCounts>(REVIEW_COUNTS_URL, signal);
export const fetchJunkSummary = (signal?: AbortSignal) => apiGet<JunkSummary>(JUNK_URL, signal);
/** 回收站计数跟着侧栏选中的媒体库走，和目录网格同一个口径：遗留层 `api()` 取 `/api/items`
 *  时会补上 `library=`，`apiGet` 不补，这里自己接上。 */
export const fetchTrashSummary = (signal?: AbortSignal) => {
  const library = sessionStorage.getItem('peach.library');
  const url = library ? `${TRASH_URL}&library=${encodeURIComponent(library)}` : TRASH_URL;
  return apiGet<TrashSummary>(url, signal);
};

const count = (value: number | undefined) => Number(value || 0).toLocaleString();

/** 复核那张卡：总数与前三类，剩下的合成「其余 N」——卡片是入口，不是复核队列本身。 */
export function reviewSummary(data: ReviewCounts | undefined): { figure: string; meta: string } {
  const labels = REVIEW_LABELS as Record<string, string>;
  const counts = Object.entries(data?.counts ?? {})
    .map(([key, value]) => [labels[key] || key, Number(value) || 0] as const)
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = counts.reduce((sum, [, value]) => sum + value, 0);
  const top = counts.slice(0, 3).map(([label, value]) => `${label} ${value.toLocaleString()}`);
  const rest = counts.slice(3).reduce((sum, [, value]) => sum + value, 0);
  if (rest) top.push(`其余 ${rest.toLocaleString()}`);
  return { figure: `${total.toLocaleString()} 条待复核`, meta: top.join(' · ') };
}

/** 垃圾文件那张卡的分项：只列真的有东西的类别，已忽略的接在后面。 */
export function junkBreakdown(summary: JunkSummary | undefined): string {
  if (!summary) return '';
  const parts = JUNK_KINDS
    .filter(([key]) => Number(summary.counts?.[key]) > 0)
    .map(([key, label]) => `${label} ${count(summary.counts[key])}`);
  if (Number(summary.dismissed_total) > 0) parts.push(`已忽略 ${count(summary.dismissed_total)}`);
  return parts.join(' · ');
}

/** 首屏不等的那几份：各自的 `useQuery` 挂载时取。 */
const LAZY_QUERIES: [QueryKey, (context: { signal: AbortSignal }) => Promise<unknown>][] = [
  [REVIEW_COUNTS_KEY, ({ signal }) => fetchReviewCounts(signal)],
  [QUALITY_GOALS_KEY, ({ signal }) => fetchQualityGoals(signal)],
  [TRASH_KEY, ({ signal }) => fetchTrashSummary(signal)],
  [LINKS_KEY, ({ signal }) => fetchLinkStats(signal)],
  [LINK_CHECK_KEY, ({ signal }) => fetchLinkCheck(signal)],
  [LINK_PRUNE_KEY, ({ signal }) => fetchLinkPrune(signal)],
];

/** 首屏：顶上那排读数、这次会扫哪几个来源，以及两张后台任务卡、整理卡与资源同步。
 *
 *  等的是画出第一屏必需的那几份：遗留壳已经铺了骨架，页面自己再转一次圈就是同一次进入里
 *  两段等待态。复核与高清版那两个数和链接统计各读各的，慢的时候不该拖住整页，所以不等。
 *
 *  不等的那几份在缓存里已经有一版时（又一次进这一页）要重取：共用的 `queryClient` 挂载时
 *  不重取已缓存的键，不在这里推一把，读数就停在上一次进来时的样子。第一次进来时它们还
 *  没有缓存，由各自的 `useQuery` 挂载时取。
 *
 *  直达页底的资源同步（`#resource-sync`）时这几份也要等：链接管理在它上面，读数与检查结果
 *  在滚动之后才接上的话，落点会被撑下去几百像素。 */
export async function prefetchDataCleanup(signal: AbortSignal): Promise<void> {
  const anchored = location.hash === '#resource-sync';
  const lazy = LAZY_QUERIES.map(([queryKey, queryFn]) => {
    if (anchored) return queryClient.fetchQuery({ queryKey, queryFn: () => queryFn({ signal }) }).catch(() => undefined);
    if (queryClient.getQueryState(queryKey)) void queryClient.prefetchQuery({ queryKey, queryFn });
    return undefined;
  });
  await Promise.all([
    ...lazy,
    queryClient.fetchQuery({ queryKey: JUNK_KEY, queryFn: () => fetchJunkSummary(signal) }),
    queryClient.fetchQuery({ queryKey: DUPLICATES_KEY, queryFn: () => fetchDuplicates(signal) }),
    prefetchMediaSources(signal).then(() => prefetchResourceSync(signal)),
    prefetchLibraryProcessing(signal),
    prefetchMediaRepair(signal),
    prefetchOrganize(signal),
  ]);
}
