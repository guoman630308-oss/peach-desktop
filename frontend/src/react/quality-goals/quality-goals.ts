/* 高清版目标的数据契约与折算：`/api/quality-goals` 一次请求供整页使用。
 *
 * 端点在 `frontend/src` 里只在这里声明一次（`tests/test_frontend_build.py` 盯着）。
 * 数据管理页那张「高清版」卡片要的是同一个真相的 `total`，它随那一页迁到 React 时读
 * 下面这个 `QUALITY_GOALS_KEY`，而不是另发一次请求——同一个真相取两次，两处显示的数
 * 就可能对不上。 */
import { coverUrl } from '@peach/card-art';

import { apiGet } from '../../api';
import { queryClient } from '../query';

/** 上限沿用服务端的 200：`limit` 也钉在 200，再大只会被截。 */
export const QUALITY_GOALS_URL = '/api/quality-goals?limit=200';
/** 整页共用这一个键。 */
export const QUALITY_GOALS_KEY = ['quality-goals'] as const;

/** `/api/quality-goals` 的单条目。字段与 `web_contract.q_quality_goals` 对齐。 */
export interface QualityGoal {
  id: number;
  name: string;
  code: string | null;
  location: string;
  size: number | null;
  duration: number | null;
  reason: string | null;
  cost: string;
  has_thumb: boolean;
  has_cover: boolean;
  cover_version?: string;
}

export interface QualityGoalsData {
  total: number;
  items: QualityGoal[];
  offset: number;
  has_more: boolean;
}

export const fetchQualityGoals = (signal?: AbortSignal) =>
  apiGet<QualityGoalsData>(QUALITY_GOALS_URL, signal);

/** 首屏：取完数才画。
 *
 * 不给 `staleTime`，所以每次进这一页都重新取。路由表里 `/quality-goals` 是
 * `refresh:'reopen'`——刷新就是重新进来一次，那时要的是新数据，不是几分钟前的缓存。
 * 中止时 `fetchQuery` 把 `AbortError` 抛回给挂载方，它据此放弃这一次。 */
export async function prefetchQualityGoals(signal: AbortSignal): Promise<void> {
  await queryClient.fetchQuery({
    queryKey: QUALITY_GOALS_KEY,
    queryFn: () => fetchQualityGoals(signal),
  });
}

/** 封面优先用番号封面，没有就退回第 4 张海报。两者都取不到时由卡片把 img 摘掉。 */
export const previewUrl = (item: QualityGoal): string => item.has_cover
  ? coverUrl(item)
  : `/poster?id=${item.id}&c=4`;
