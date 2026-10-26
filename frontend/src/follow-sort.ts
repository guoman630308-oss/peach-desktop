/* 关注列表的排序维度与各自的默认方向。React 关注管理页与遗留层画的首屏骨架都读这一份：
   骨架跑在 React 包加载之前，排序框里该写哪一档、方向键该朝哪头，得跟接管后是同一个答案。 */

/** 工具栏那个下拉里的排序维度。前四个按创作者比，后三个按单条来源比。 */
export const SORT_OPTIONS = [
  ['checked', '检查时间'], ['added', '添加时间'], ['name', '创作者名称'], ['sources', '来源数量'],
  ['source', '来源名称'], ['provider', '站点'], ['status', '状态'],
] as const;
export type SortKey = (typeof SORT_OPTIONS)[number][0];
export const DEFAULT_SORT: SortKey = 'checked';

export type SortDir = 'asc' | 'desc';

/** 每一列问的那句话的常态：时间问「最近的先看」，名字问「从头排」。 */
export const SORT_DEFAULT_DIR: Record<SortKey, SortDir> = {
  checked: 'desc', added: 'desc', name: 'asc', sources: 'desc',
  source: 'asc', provider: 'asc', status: 'asc',
};

export const isSortKey = (value: unknown): value is SortKey =>
  SORT_OPTIONS.some(([key]) => key === value);

/** 地址栏里的 `sort` / `dir` 落到哪一档：认不出的排序回默认，没写方向就取那一档的常态。 */
export function resolveFollowSort(sort: unknown, dir: unknown): { sort: SortKey; dir: SortDir } {
  const key = isSortKey(sort) ? sort : DEFAULT_SORT;
  return { sort: key, dir: dir === 'asc' || dir === 'desc' ? dir : SORT_DEFAULT_DIR[key] };
}
