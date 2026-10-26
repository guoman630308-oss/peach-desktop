/* 设置偏好与 Peach 配置的三份服务端数据，各一个 Query 键。
 *
 * - `['settings']`：跟数据库走、所有访问端同一份的那几项（`/api/settings`，`src/peach/web_settings.py`）。
 *   面板每次打开都重取一次，取回来交给壳对账（`host.syncRemote`）：界面偏好的真相仍是壳里那一份
 *   `appSettings`，这个键只是数据库那边的快照。写入同样走这个端点，成功后回来的整份写回这个键。
 * - `['follow-schedule']`：这台机器上关注自动更新的档位与上次运行状态。
 * - `['thumbnail-jobs']`：视频缩略图采集的档位与状态。档位跟着机器走，不存在浏览器里。
 *
 * 面板常驻不卸载，每次打开重取设置；Peach 配置在挂载时重取各自的数据。 */
import { useMutation, type QueryClient } from '@tanstack/react-query';

import { apiGet, apiSend } from '../../api';
import { queryClient } from '../query';

export const SETTINGS_URL = '/api/settings';
export const FOLLOW_SCHEDULE_URL = '/api/follow/schedule';
export const THUMBNAIL_JOBS_URL = '/api/thumbnail-jobs';

export const SETTINGS_KEY = ['settings'] as const;
export const FOLLOW_SCHEDULE_KEY = ['follow-schedule'] as const;
export const THUMBNAIL_JOBS_KEY = ['thumbnail-jobs'] as const;

/** `q_settings` 返回的整份；写入时只送改了的那几个键，回来的仍是整份。 */
export interface SyncedSettings {
  sidebarOrder?: string[];
  followInitialDays?: number;
  postSetupTutorialDone?: boolean;
  feedHideGroupCompilations?: boolean;
  feedHideSoloCompilations?: boolean;
  feedHideExcerpts?: boolean;
  /** 数据库里还没存过时是 null：这台设备本地那个数替所有访问端先定下来。 */
  searchHistoryLimit?: number | null;
}

export interface FollowScheduleStatus {
  available: boolean;
  enabled: boolean;
  interval_minutes: number;
  running?: boolean;
  last_error?: string | null;
  last_finished_at?: string | null;
  last_added?: number | null;
  next_run_at?: string | null;
}

export interface ThumbnailJobStatus {
  mode: string;
  status: 'idle' | 'running' | 'failed' | 'complete' | string;
  stopped?: string;
}

export const fetchSettings = (signal?: AbortSignal) => apiGet<SyncedSettings>(SETTINGS_URL, signal);
export const fetchFollowSchedule = (signal?: AbortSignal) =>
  apiGet<FollowScheduleStatus>(FOLLOW_SCHEDULE_URL, signal);
export const fetchThumbnailJobs = (signal?: AbortSignal) =>
  apiGet<ThumbnailJobStatus>(THUMBNAIL_JOBS_URL, signal);

/** 打开偏好弹窗时重取共享设置；采集状态由 Peach 配置中的控件读取。 */
export function refreshPanelData(client: QueryClient = queryClient): Promise<SyncedSettings | null> {
  return client.fetchQuery({
    queryKey: SETTINGS_KEY, queryFn: ({ signal }) => fetchSettings(signal), staleTime: 0,
  }).catch(() => null);
}

/** 写跟数据库走的那几项。回来的是合并之后的整份，写回 `['settings']`。 */
export function useSaveSettings() {
  return useMutation({
    mutationFn: (patch: SyncedSettings) => apiSend<SyncedSettings>(SETTINGS_URL, patch),
    onSuccess: (saved) => queryClient.setQueryData(SETTINGS_KEY, saved),
  });
}

export function useSaveFollowSchedule() {
  return useMutation({
    mutationFn: (minutes: number) => apiSend<FollowScheduleStatus>(FOLLOW_SCHEDULE_URL,
      { enabled: minutes > 0, interval_minutes: minutes || 60 }),
    onSuccess: (status) => queryClient.setQueryData(FOLLOW_SCHEDULE_KEY, status),
  });
}

export function useSaveThumbnailMode() {
  return useMutation({
    mutationFn: (mode: string) => apiSend<ThumbnailJobStatus>(THUMBNAIL_JOBS_URL, { mode }),
    onSuccess: (status) => queryClient.setQueryData(THUMBNAIL_JOBS_KEY, status),
  });
}
