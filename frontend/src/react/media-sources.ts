/* 来源可达性在 React 页面一侧的用法。数据管理页要知道这次会扫哪几个来源、资源同步那一块出不出现，
 * 重复文件页要知道哪些网盘值得给一颗「优先保留」。
 *
 * 地址、查询键与取数函数和壳共用，声明在 `src/query/media-sources.ts`，按 `@peach/query` 引用，
 * 主包里的实例只有一份。服务端定期探测；页面进入时读取最新快照。 */
import { fetchMediaSources, MEDIA_SOURCES_KEY, type MediaSourcesData, type MediaSourceStatus } from '@peach/query';

import { queryClient } from './query';
import { CLOUD_LOCATIONS, SCANNABLE_LOCATIONS } from '../core/media-sources';

export { fetchMediaSources, MEDIA_SOURCES_KEY } from '@peach/query';
export type { MediaSourcesData, MediaSourceStatus } from '@peach/query';

export async function prefetchMediaSources(signal: AbortSignal): Promise<void> {
  await queryClient.fetchQuery({ queryKey: MEDIA_SOURCES_KEY, queryFn: () => fetchMediaSources(signal) });
}

/** 挂载存储来源。`online` 这类在线来源不落盘，不参与扫描与对账。 */
export { SCANNABLE_LOCATIONS } from '../core/media-sources';

export const scannableSources = (data: MediaSourcesData | undefined): MediaSourceStatus[] =>
  (data?.sources ?? []).filter((source) => SCANNABLE_LOCATIONS.includes(source.location));

/** 配了根目录的网盘。离线的网盘仍然属于已配置来源：它上面的重复副本照样能挑着留。 */
export const cloudLocations = (data: MediaSourcesData | undefined): string[] =>
  (data?.sources ?? []).filter((source) => CLOUD_LOCATIONS.includes(source.location)
    && (source.roots === undefined || source.roots.length > 0)).map((source) => source.location);
