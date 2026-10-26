/* 口味页的数据契约、轮询节律与纯折算。
 *
 * 四个端点在 `frontend/src` 里都只在这里声明一次（`tests/test_frontend_build.py` 盯着）。
 *
 * 键带着分析范围：`['taste', window]`。同一台机器的七天和全部时间是两份真相，合成一个键
 * 的话切范围就是把上一份直接覆盖掉，切回来还得再问一遍。范围切换时用
 * `keepPreviousData` 留住上一份，不退回骨架——这一屏的结构不变，只有数在变。
 * 不给 `staleTime`：服务端已经按 `taste:{window}` 缓存过一层，前端再压一层就会出现
 * 「刚导入完，页面还是导入前那一份」。
 *
 * 刷新任务是另一份真相，节律由后台推进，所以单独一个键（`['taste','refresh']`）。 */
import { sankey, sankeyLinkHorizontal } from 'd3-sankey';
import { clipGraphemes } from '@peach/legacy/core';

import { apiGet, apiSend, ApiError } from '../../api';
import type { BarRow } from '../charts/bar-card';
import type { ActivityCounts } from '../charts/heat';
import { queryClient } from '../query';
import { playedFor } from '../stats/stats';

export const TASTE_URL = '/api/taste';
export const TASTE_REFRESH_URL = '/api/taste/refresh';
export const TASTE_IMPORT_URL = '/api/taste/import';
export const TASTE_SOURCE_URL = '/api/taste/source';

/** 一份 dashboard 的键，带分析范围。 */
export const tasteKey = (window: string) => ['taste', window] as const;
/** 读取浏览记录那一趟后台任务。 */
export const TASTE_REFRESH_KEY = ['taste', 'refresh'] as const;

/** 分析范围。值就是 `/api/taste?window=` 收的那几个。 */
export const TASTE_WINDOWS = [
  ['7d', '最近 7 天'], ['30d', '最近 30 天'], ['90d', '最近 90 天'],
  ['365d', '最近一年'], ['all', '全部时间'],
] as const;

export const DEFAULT_WINDOW = 'all';

/** 排行里的一行。`entity_id` 与 `has_avatar` 决定画哪一级图，`peach_items` 决定点不点得动。 */
export interface RankRow {
  name: string;
  score?: number;
  visits?: number;
  web_visits?: number;
  web_bookmarks?: number;
  peach_score?: number;
  peach_items?: number;
  entity_id?: number | null;
  has_image?: boolean;
  image_version?: string;
  avatar_focus?: unknown;
  has_avatar?: boolean;
  avatar_stand_in?: boolean;
  representative_asset_id?: number | null;
  source_domain?: string | null;
}

/** 按维度聚合的名次。名字与 `taste_history.build_taste_dashboard` 对齐。 */
export interface TasteRankings {
  browser_tags?: RankRow[];
  browser_creators?: RankRow[];
  browser_performers?: RankRow[];
  browser_categories?: RankRow[];
  domains?: RankRow[];
  peach_tags?: RankRow[];
  peach_creators?: RankRow[];
  peach_performers?: RankRow[];
  peach_studios?: RankRow[];
  peach_series?: RankRow[];
}

export interface TasteSummary {
  history_visits?: number;
  bookmarks?: number;
  history_sources?: number;
  peach_items?: number;
  peach_seconds?: number;
  liked?: number;
  disliked?: number;
  range_start?: string | null;
  range_end?: string | null;
}

export interface TasteCoverage {
  tagged?: number; identified?: number; untagged?: number; unidentified?: number;
}

/** 一台设备的一份浏览记录。`source_key` 是 64 位十六进制，移除时交回去的就是它。 */
export interface TasteSource {
  source_key: string; browser: string; profile: string; host: string; visits?: number; bookmarks?: number;
}

export interface TasteAnalysis {
  headline?: string;
  confidence?: { level?: string; label?: string };
  points?: { label: string; text: string }[];
  explore?: { tag: string; title: string; detail: string }[];
  next_steps?: { route: string; title: string; detail: string }[];
}

export interface CreatorFlow { source: string; target: string; value: number }

/** `/api/taste` 的响应。字段与 `web_stats.q_taste` 对齐。 */
export interface TasteData {
  browsers?: BrowserSource[];
  summary?: TasteSummary;
  coverage?: TasteCoverage;
  rankings?: TasteRankings;
  gaps?: RankRow[];
  sources?: TasteSource[];
  analysis?: TasteAnalysis;
  activity?: ActivityCounts;
  creator_flows?: CreatorFlow[];
  storage?: { exports?: number; bytes?: number };
  window?: string;
  updated_at?: string | null;
}

export interface BrowserSource {
  id: string;
  name: string;
  path: string;
  enabled: boolean;
  available: boolean;
  profiles: number;
  source_keys?: string[];
}

export const configureTasteBrowser = (body: { operation: 'add' | 'disable' | 'enable' | 'delete_browser'; name?: string; path?: string; id?: string }, window: string) =>
  apiSend<{ dashboard: TasteData }>(TASTE_SOURCE_URL, { ...body, window });

/** 读取浏览记录那一趟的快照。`status` 一次都没跑过是 `idle`。 */
export interface TasteJob {
  status: string;
  job_id?: string;
  stage?: string;
  message?: string;
  checked?: number;
  total?: number | null;
  error?: string;
  warnings?: string[];
}

export const fetchTaste = (window: string, signal?: AbortSignal) =>
  apiGet<TasteData>(`${TASTE_URL}?window=${window}`, signal);

export const fetchTasteJob = (signal?: AbortSignal) =>
  apiGet<TasteJob>(TASTE_REFRESH_URL, signal);

/** 首屏：取完数才画。中止时把 `AbortError` 抛回挂载方，它据此放弃这一次。 */
export async function prefetchTaste(window: string, signal: AbortSignal): Promise<void> {
  await queryClient.fetchQuery({
    queryKey: tasteKey(window), queryFn: () => fetchTaste(window, signal),
  });
}

/** 起一趟读取浏览记录。回的是任务快照，节律仍由 `TASTE_REFRESH_KEY` 说了算。 */
export const startTasteRefresh = (window: string) =>
  apiSend<TasteJob>(TASTE_REFRESH_URL, { window, background: true });

/** 移除一台设备的浏览记录。服务端连这一范围的新 dashboard 一起回，换进缓存即可。 */
export const removeTasteSource = (sourceKey: string, window: string) =>
  apiSend<{ removed: number; dashboard: TasteData }>(
    TASTE_SOURCE_URL, { operation: 'remove', source_key: sourceKey, window });

/** 导入一份私有导出。
 *
 * 文件原样当请求体发，文件名走 `X-Peach-Filename`：走 multipart 就要在服务端多接一个
 * 解析器，而这里只有一个文件、一个名字。服务端回的是「全部时间」那一份 dashboard。 */
export async function importTasteExport(file: File): Promise<{ dashboard: TasteData }> {
  const response = await fetch(TASTE_IMPORT_URL, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/octet-stream',
      'X-Peach-Filename': encodeURIComponent(file.name),
    },
    body: file,
  });
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch { /* 失败响应不一定是 JSON，下面按状态码兜底 */ }
  if (!response.ok) {
    const reason = (payload as { error?: string } | null)?.error;
    throw new ApiError(reason || `导入失败（${response.status}）`, response.status, payload);
  }
  return payload as { dashboard: TasteData };
}

/** 读取闲着时十秒问一次——这一趟可以从别的页面或上一次会话里起来。 */
export const IDLE_POLL_MS = 10_000;

export const tasteDate = (value: string | null | undefined): string =>
  (value ? new Date(value).toLocaleDateString('zh-CN') : '—');

/** 浏览记录那张读数卡的脚注：几个数据源，加上记录覆盖的日期范围；两头都没有日期时不写范围。 */
export function historySpan(summary: TasteSummary): string {
  const sources = `${Number(summary.history_sources || 0).toLocaleString()} 个数据源`;
  if (!summary.range_start && !summary.range_end) return sources;
  const sameYear = summary.range_start?.slice(0, 4) === summary.range_end?.slice(0, 4);
  const date = (value: string | null | undefined) => value && sameYear
    ? new Date(value).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) : tasteDate(value);
  return `${sources} · ${date(summary.range_start)}–${date(summary.range_end)}`;
}

/** 累计时长。与统计页同一套口径。 */
export const tasteHours = playedFor;

/** 「口味维度」读数：分数为正的维度才算，和雷达、排行条画出来的是同一批。 */
export const tasteDimensions = (rows: RankRow[]): RankRow[] =>
  rows.filter((row) => Number.isFinite(Number(row.score)) && Number(row.score) > 0)
    .sort((a, b) => Number(b.score) - Number(a.score));

/** 一行在这一榜里的强度。浏览侧按访问次数，Peach 侧按分数，都退化到条目数。 */
export const rankStrength = (row: RankRow): number =>
  Number(row.web_visits ?? row.score ?? row.visits ?? row.peach_items ?? 0);

/** 一行名次底下那句补充。两侧都有证据时并排显示，只有一侧就读那一侧的数。 */
export function rankDetail(row: RankRow): string {
  if (row.web_visits == null && !row.web_bookmarks) return Number(row.score || row.visits || 0).toLocaleString();
  const parts: string[] = [];
  const bookmarks = Number(row.web_bookmarks || 0);
  const visits = Number(row.web_visits ?? row.visits ?? 0) - bookmarks;
  if (visits > 0) parts.push(`浏览 ${visits.toLocaleString()}`);
  if (bookmarks) parts.push(`书签 ${bookmarks.toLocaleString()}`);
  if (row.peach_items) parts.push(`Peach ${Number(row.peach_items).toLocaleString()}`);
  return parts.join(' · ');
}

/** 一段条的长度，百分比。最长的那一条占满，空榜不除零。 */
export function rankShares(rows: RankRow[]): number[] {
  const strengths = rows.map(rankStrength);
  const ceiling = Math.max(1, ...strengths);
  return strengths.map((value) => Math.max(0, Math.min(100, value / ceiling * 100)));
}

/** 按分数从高到低的前 `limit` 个维度。分数为 0 或非有限的行先滤掉。 */
export const topScores = (rows: RankRow[], limit: number): BarRow[] =>
  rows.filter((row) => Number.isFinite(Number(row.score)) && Number(row.score) > 0)
    .map((row) => ({ name: row.name, value: Number(row.score) }))
    .sort((a, b) => b.value - a.value).slice(0, limit);

/** 少于三个维度画不成面，三点以下不画。最多取前六个，再多标签互相压住。 */
export const RADAR_MIN = 3;
export const RADAR_MAX = 6;

/** 雷达图的几个顶点。 */
export function radarRows(rows: RankRow[]): BarRow[] {
  const values = topScores(rows, RADAR_MAX);
  return values.length < RADAR_MIN ? [] : values;
}

/** 排行条最多八条。 */
export const RANK_MAX = 8;

/** 桑基图的一个节点。`side` 决定它落在左边还是右边，也决定它的文字往哪边排。 */
export interface FlowNode {
  id: string; name: string; side: 'source' | 'target'; index: number;
  x: number; y: number; height: number; value: number; share: number; color: number;
}
/** 一条流。`d` 是 d3 算出来的贝塞尔路径。 */
export interface FlowLink {
  key: string; d: string; width: number; color: number;
  source: number; target: number; value: number; label: string;
}
export interface FlowGraph { nodes: FlowNode[]; links: FlowLink[]; total: number; height: number }

/** 节点条宽与画布：左右各留一截给文字，视口宽 720，高度跟着节点数走，最矮 435。 */
export const FLOW_NODE_WIDTH = 10;
export const FLOW_VIEWBOX = { width: 720, height: 435 } as const;
export const FLOW_LABEL_X = { source: 145, target: 545 } as const;

/** 一侧最多几个节点。再多就留下线索最多的前几个，其余并成一个「其余 N …」节点。 */
export const FLOW_SIDE_MAX = 12;

/** 相邻两个节点之间的空隙，也就是两个节点中心的最小间距：节点名和它下面那行数从中心往上
 *  约 7、往下约 22，隔 30 时小节点挤在一起，两行字也不压到下一个节点上。 */
const FLOW_ROW = 30;

/** 除去空隙之外，留给节点本身按线索数分的高度。 */
const FLOW_NODE_ROOM = 240;

/** 一侧的名字超过 `FLOW_SIDE_MAX` 个时，把线索少的那些名字映射到同一个合并节点上。 */
function foldSide(flows: CreatorFlow[], side: 'source' | 'target', rest: (count: number) => string) {
  const totals = new Map<string, number>();
  for (const row of flows) totals.set(row[side], (totals.get(row[side]) ?? 0) + row.value);
  if (totals.size <= FLOW_SIDE_MAX) return (name: string) => name;
  const ranked = [...totals].sort((a, b) => b[1] - a[1]).map(([name]) => name);
  const kept = new Set(ranked.slice(0, FLOW_SIDE_MAX - 1));
  const merged = rest(ranked.length - kept.size);
  return (name: string) => (kept.has(name) ? name : merged);
}

/** 来源网站 → 创作者的流向布局。值为 0 或缺一端的流先滤掉，全空时返回 `null`。 */
export function flowGraph(rows: CreatorFlow[] = []): FlowGraph | null {
  const valid = rows.filter((row) =>
    row.source && row.target && Number.isFinite(row.value) && row.value > 0);
  if (!valid.length) return null;
  const sourceOf = foldSide(valid, 'source', (count) => `其余 ${count.toLocaleString()} 个网站`);
  const targetOf = foldSide(valid, 'target', (count) => `其余 ${count.toLocaleString()} 位创作者`);
  const folded = new Map<string, CreatorFlow>();
  for (const row of valid) {
    const source = sourceOf(row.source);
    const target = targetOf(row.target);
    const key = JSON.stringify([source, target]);
    const seen = folded.get(key);
    if (seen) seen.value += row.value;
    else folded.set(key, { source, target, value: row.value });
  }
  const flows = [...folded.values()];
  const sources = [...new Set(flows.map((row) => row.source))];
  const targets = [...new Set(flows.map((row) => row.target))];
  const rowsOnSide = Math.max(sources.length, targets.length);
  const bottom = Math.max(404, 16 + (rowsOnSide - 1) * FLOW_ROW + FLOW_NODE_ROOM);
  const graph = sankey<{ id: string; name: string; side: 'source' | 'target' }, object>()
    .nodeId((node) => node.id).nodeWidth(FLOW_NODE_WIDTH).nodePadding(FLOW_ROW)
    .extent([[155, 16], [535, bottom]])({
      nodes: [
        ...sources.map((name) => ({ id: `source:${name}`, name, side: 'source' as const })),
        ...targets.map((name) => ({ id: `target:${name}`, name, side: 'target' as const })),
      ],
      links: flows.map((row) => (
        { source: `source:${row.source}`, target: `target:${row.target}`, value: row.value })),
    });
  const total = flows.reduce((sum, row) => sum + row.value, 0);
  const path = sankeyLinkHorizontal();
  type Node = typeof graph.nodes[number];
  const nodes: FlowNode[] = graph.nodes.map((node) => ({
    id: node.id, name: node.name, side: node.side, index: node.index ?? 0,
    x: node.x0 ?? 0, y: node.y0 ?? 0, height: Math.max(1, (node.y1 ?? 0) - (node.y0 ?? 0)),
    value: node.value ?? 0, share: (node.value ?? 0) / total * 100,
    color: sources.indexOf(node.name) % 6,
  }));
  const links: FlowLink[] = graph.links.map((link, at) => {
    const from = link.source as Node;
    const to = link.target as Node;
    return {
      key: `${from.id}->${to.id}:${at}`, d: path(link) || '',
      width: Math.max(0.5, link.width || 0), color: sources.indexOf(from.name) % 6,
      source: from.index ?? 0, target: to.index ?? 0, value: link.value,
      label: `${from.name} → ${to.name}`,
    };
  });
  return { nodes, links, total, height: bottom + FLOW_VIEWBOX.height - 404 };
}

/** 节点名字太长时截断。图里一格只有这么宽，整名在 `aria-label` 和 `<title>` 里。 */
export const flowLabel = (name: string): string => clipGraphemes(name, 17);
