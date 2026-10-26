/* 媒体修复的数据层：任务快照与可选的媒体库，两份都进共用的 Query 缓存。
 *
 * 一轮要跑几十分钟到几小时，跑到哪一步由服务自己记，页面关掉再回来照样接得上。
 * 首屏两份一起取，卡片挂上去直接读缓存。 */
import { apiGet } from '../../api';
import { queryClient } from '../query';

export const MEDIA_REPAIR_URL = '/api/media-repair';
export const MEDIA_REPAIR_KEY = ['media-repair'] as const;
export const REPAIR_LIBRARIES_KEY = ['media-repair', 'libraries'] as const;

export interface MediaRepairState {
  status: string;
  library: string;
  stage: string;
  checked: number;
  total: number;
  found: number;
  repaired: number;
  failed: number;
  missing_tool: number;
  skipped: number;
  message: string;
  error?: string;
}

export interface RepairLibrary {
  id: string;
  name: string;
  /** 媒体库图标：来源名（`115`、`pikpak`、`local`）或雪碧图字形名，与侧栏媒体库切换器同一份。 */
  icon: string;
  metered: boolean;
}

export const IDLE_REPAIR: MediaRepairState = {
  status: 'idle', library: '', stage: '', checked: 0, total: 0, found: 0,
  repaired: 0, failed: 0, missing_tool: 0, skipped: 0, message: '',
};

export const fetchMediaRepair = (signal?: AbortSignal) => apiGet<MediaRepairState>(MEDIA_REPAIR_URL, signal);

export const fetchRepairLibraries = async (signal?: AbortSignal) =>
  (await apiGet<{ libraries?: RepairLibrary[] }>('/api/libraries', signal)).libraries || [];

export async function prefetchMediaRepair(signal: AbortSignal): Promise<void> {
  await Promise.all([
    queryClient.fetchQuery({ queryKey: MEDIA_REPAIR_KEY, queryFn: () => fetchMediaRepair(signal) }),
    queryClient.fetchQuery({ queryKey: REPAIR_LIBRARIES_KEY, queryFn: () => fetchRepairLibraries(signal) }),
  ]);
}

const count = (value: number) => Number(value).toLocaleString();

/** 一句话说清此刻在干什么：扫描报进度，修复报进度加当前这部片。 */
export function statusLine(state: MediaRepairState): string {
  if (state.status === 'running') {
    // 服务端偶尔报出超过总数的已查数，按总数封顶。
    const where = state.total ? `${count(Math.min(state.checked || 0, state.total))} / ${count(state.total)}` : '准备中';
    return state.stage === '修复' && state.message
      ? `修复 ${where} · ${state.message}`
      : `${state.stage || '扫描'} ${where}`;
  }
  if (state.status === 'complete' && (state.repaired || state.failed)) {
    return state.failed
      ? `修好 ${count(state.repaired)} 部，${count(state.failed)} 部修不了`
      : `修好 ${count(state.repaired)} 部`;
  }
  return '';
}

export const missingToolText = (state: MediaRepairState) =>
  `${count(state.missing_tool)} 部缺索引的片子要装上 untrunc 才修得了，下载入口在配置页的「运行信息」里。`;
