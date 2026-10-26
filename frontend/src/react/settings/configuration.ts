/* 配置页的首屏数据。
 *
 * 整页与各分区读同一个 `queryKey`：挂载状态那一块按下「刷新」重取回来的也是整份配置，
 * 换进这一个键，屏幕上就只有一份真相。端点字符串在 `frontend/src` 里只声明一次，
 * 在 `../../configuration-endpoints`（`tests/test_frontend_build.py` 盯着）。 */
import { apiGet } from '../../api';
import { CONFIGURATION_URL } from '../../configuration-endpoints';
import type { ConfigurationData } from '../bundle';
import { queryClient } from '../query';

export const CONFIGURATION_KEY = ['configuration'] as const;

export const fetchConfiguration = (signal?: AbortSignal) =>
  apiGet<ConfigurationData>(CONFIGURATION_URL, signal);

/** 首屏：取回来才画。中止时 `fetchQuery` 把 `AbortError` 抛回给挂载方，它据此放弃这一次。 */
export async function prefetchConfiguration(signal: AbortSignal): Promise<void> {
  await queryClient.fetchQuery({
    queryKey: CONFIGURATION_KEY, queryFn: () => fetchConfiguration(signal),
  });
}
