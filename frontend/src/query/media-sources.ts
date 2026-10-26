/* 来源可达性（/api/sources）：地址、查询键与取数函数只在本模块声明。
 * Application 的 loadSourceStatus、数据管理页、资源同步与重复文件页共读此键。
 * 不设 staleTime，显式刷新与进入页面按服务端最新快照读取。
 * Application 只读调用返回值、不订阅；页面重取不会改变目录默认来源与脱盘提示。 */
import { isCancelledError } from '@tanstack/query-core';

import { apiGet } from '../api';
import { queryClient } from './client';

export const MEDIA_SOURCES_URL = '/api/sources';
export const MEDIA_SOURCES_KEY = ['media-sources'] as const;

/** `/api/sources` 的一行。字段与 `routes_api.source_health` 对齐。 */
export interface MediaSourceStatus {
  location: string;
  online: boolean | null;
  state?: string;
  message?: string;
  roots?: unknown[];
}

export interface MediaSourcesData {
  sources: MediaSourceStatus[];
}

export const fetchMediaSources = (signal?: AbortSignal) =>
  apiGet<MediaSourcesData>(MEDIA_SOURCES_URL, signal);

const stopped = (error: unknown) => (error as { name?: unknown } | null)?.name === 'AbortError' || isCancelledError(error);

/** 壳的来源检测：启动、详情里的「刷新状态」与播放器报错时各调一次，每次都向服务端问一遍。
 *
 *  查询函数不读 Query 交给它的 `signal`：读了它，页面上最后一个观察者卸载时 Query 会撤回这一趟，
 *  壳拿到的是上一份旧数据。同时在途的同一请求由 Query 合并成一次；合并上的若是页面那一趟，
 *  它带着页面自己的 `signal`，页面中止时这一趟以中止收场，壳就自己再发一次。 */
export async function loadMediaSources(signal?: AbortSignal): Promise<MediaSourcesData> {
  signal?.throwIfAborted();
  const fetchOnce = () => queryClient.fetchQuery({ queryKey: MEDIA_SOURCES_KEY, queryFn: () => fetchMediaSources(signal) });
  try {
    const data = await fetchOnce();
    signal?.throwIfAborted();
    return data;
  } catch (error) {
    signal?.throwIfAborted();
    if (!stopped(error)) throw error;
    return await fetchOnce();
  }
}
