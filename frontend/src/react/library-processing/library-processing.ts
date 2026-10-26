/* 扫描与采集的数据契约、轮询节律与纯折算。
 *
 * 数据管理页卡片、常驻任务面与缺预览提示读同一个 `queryKey`，使用同一份任务快照。
 * 轮询也由 Query 合成一份，进度、失败与完成状态由服务端统一提供。
 * 端点在 `frontend/src` 里只在这里声明一次（`tests/test_frontend_build.py` 盯着）。 */
import { apiGet } from '../../api';
import { elapsedText, progressText } from '../activity/tasks';
import { queryClient } from '../query';

export const LIBRARY_PROCESSING_URL = '/api/library-processing';
export const LIBRARY_PROCESSING_IMPORT_URL = '/api/library-processing/import-file';
export const PICK_MEDIA_FILE_URL = '/api/pick-media-file';

/** 卡片与常驻任务面共用这一个键。 */
export const LIBRARY_PROCESSING_KEY = ['library-processing'] as const;

/** 处理过程中记下的一条问题。`asset_id` 为空时说的是媒体来源本身。 */
export interface LibraryProcessingIssue {
  asset_id: number | null;
  title?: string;
  path?: string;
  message: string;
  /** `paused` 是来源限流没轮到这一项，等来源放开再跑就有；`error` 是它自己出的事。 */
  severity?: 'info' | 'error' | 'paused';
}

/** 一趟扫描与采集的快照。字段以 `q_library_processing`（`src/peach/web_library_processing.py`）为准。 */
export interface LibraryProcessingData {
  metadata_only?: boolean;
  /** `idle` | `running` | `pausing` | `paused` | `complete` | `failed`。 */
  status: string;
  speed_mode?: ProcessingSpeed;
  eta_seconds_min?: number | null;
  eta_seconds_max?: number | null;
  eta_kind?: 'historical' | 'measured' | 'estimating';
  eta_scope?: string;
  eta_initial?: boolean;
  job_id?: string;
  stage?: string;
  requested_stage?: string;
  steps?: { id: string; label: string; status: 'pending' | 'current' | 'complete' }[];
  checked?: number;
  total?: number;
  scanned?: number;
  scanned_bytes?: number;
  identified?: number;
  candidates?: number;
  auto_applied?: number;
  covers?: number;
  previews?: number;
  /* 落库之后补齐女优资料那一步的四个读数：别名、头像、交回人工的冲突、没取到的头像。 */
  performer_aliases?: number;
  performer_avatars?: number;
  performer_profile_conflicts?: number;
  performer_profile_failed?: number;
  error?: string;
  issue_count?: number;
  /** `issue_count` 里卡在来源限流上的那些，等来源放开再跑一次就有。 */
  paused_count?: number;
  issue_preview?: LibraryProcessingIssue[];
  issues_log?: string;
  issues_truncated?: boolean;
  retryable_asset_ids?: number[];
  notes?: Record<string, number>;
  current_asset_id?: number | null;
  current_asset_name?: string;
  current_action?: string;
  current_started_at?: number;
  last_progress_at?: number;
  stalled?: boolean;
  waited_seconds?: number;
  completed_at?: number;
}

/** 一次写操作的请求体：指定处理阶段与影片，或者按任务号重试失败项。 */
export type ProcessingSpeed = 'low' | 'standard' | 'high';
export interface LibraryProcessingCommand {
  stage?: 'scan' | 'collect' | 'previews' | 'metadata';
  asset_ids?: number[];
  job_id?: string;
  retry?: number[];
  speed_mode?: ProcessingSpeed;
}
export type ProcessingControlCommand =
  | { action: 'pause' | 'resume'; job_id: string }
  | { action: 'set_speed'; speed_mode: ProcessingSpeed; job_id?: string };

/** 暂停中的任务仍占用同一份处理范围，可继续并持续读取状态。 */
export const processingActive = (state?: LibraryProcessingData) =>
  !!state && ['running', 'pausing', 'paused'].includes(state.status);

/** 时间向分钟取整；区间两端始终使用同一单位。 */
export function remainingTime(state: LibraryProcessingData): string {
  if (!processingActive(state)) return '';
  if (state.status === 'paused') return '已暂停，继续后更新预估';
  if (state.status === 'pausing') return '正在暂停，当前步骤结束后暂停';
  if (state.stalled) return '等待当前文件响应，剩余时间待更新';
  const low = state.eta_seconds_min, high = state.eta_seconds_max;
  if (low == null || high == null || !Number.isFinite(low) || !Number.isFinite(high) || low < 0 || high < low)
    return '正在估算剩余时间…';
  if (high < 60) return '预计剩余不到 1 分钟';
  const unit = high >= 86400 ? 86400 : high >= 3600 ? 3600 : 60;
  const label = unit === 86400 ? '天' : unit === 3600 ? '小时' : '分钟';
  const precision = unit === 60 ? 1 : 10;
  const start = Math.max(1 / precision, Math.floor(low / unit * precision) / precision);
  const end = Math.max(start, Math.ceil(high / unit * precision) / precision);
  const span = start === end ? `${end}` : `${start}–${end}`;
  const rough = state.eta_initial || state.eta_kind !== 'measured' ? '（粗估）' : '';
  return `预计剩余约 ${span} ${label}${rough}${state.eta_scope ? ` · ${state.eta_scope}` : ''}`;
}

export const fetchLibraryProcessing = (signal?: AbortSignal) =>
  apiGet<LibraryProcessingData>(LIBRARY_PROCESSING_URL, signal);

/** 首屏。中止时 `fetchQuery` 把 `AbortError` 抛回挂载方，它据此放弃这一次。 */
export async function prefetchLibraryProcessing(signal: AbortSignal): Promise<void> {
  await queryClient.fetchQuery({
    queryKey: LIBRARY_PROCESSING_KEY,
    queryFn: () => fetchLibraryProcessing(signal),
  });
}

/** 在跑的时候两秒问一次，和别的后台任务同一个节律。 */
export const RUNNING_POLL_MS = 2000;
/** 闲着时也接着问：任务由后台自己起，两个读者都要认出「现在跑起来了」。 */
export const IDLE_POLL_MS = 10_000;

/** 读失败时页面上留的那句话。上一份数据不撤，下一轮轮询自己会接上。 */
export const DISCONNECTED_TEXT = '连接中断，正在重新读取处理进度';

export function pollInterval(
  data: LibraryProcessingData | undefined, watching: boolean,
): number | false {
  if (processingActive(data)) return RUNNING_POLL_MS;
  return watching ? IDLE_POLL_MS : false;
}

const ACTION_LABELS: Record<string, string> = {
  scanning_files: '扫描文件',
  reading_local: '检查配套文件',
  reading_local_cover: '读取配套图片',
  querying_metadata: '查询外部资料',
  fetching_cover: '采集缺失封面',
  generating_preview: '生成视频预览',
  writing_candidates: '保存待确认资料',
};

/* 来源说「没有」按类型报一个数，不进问题清单：馆藏里大量独立资源和创作者作品
   任何目录站都收不到，它们不是待办。键与 `library_processing.MISS_MESSAGES` 同源。 */
const NOTE_LABELS: Record<string, string> = {
  querying_metadata: '没有资料',
  fetching_cover: '没有封面',
};

export function notesLine(notes: Record<string, number>): string {
  const parts = Object.entries(notes)
    .filter(([key, count]) => NOTE_LABELS[key] && count > 0)
    .map(([key, count]) => `${NOTE_LABELS[key]} ${count.toLocaleString()} 部`);
  return parts.length ? `外部来源${parts.join('、')}，7 天内不再问。` : '';
}

/** 此刻在做什么：哪个文件、哪一步、等了多久。 */
export function currentLine(state: LibraryProcessingData): string {
  if (state.status === 'paused') return '已暂停';
  if (state.status === 'pausing') return '正在暂停';
  const action = ACTION_LABELS[state.current_action || ''] || state.stage || '正在处理';
  const waited = state.waited_seconds ? ` · 已等待 ${elapsedText(state.waited_seconds)}` : '';
  return state.current_asset_name
    ? `当前：${state.current_asset_name} · ${action}${waited}`
    : action + waited;
}

/** 已知总量显示实际完成数；扫描目录期间显示持续发现的文件数。 */
export function processingLine(state: LibraryProcessingData): string {
  const count = state.total ? `${progressText(state.checked, state.total)} 项`
    : state.current_action === 'scanning_files' || state.stage === '扫描文件'
      ? `已发现 ${(state.scanned || 0).toLocaleString()} 个文件` : '';
  return currentLine(state) + (count ? ` · ${count}` : '');
}

/** 折叠起来的问题清单：一句结论 + 逐条明细 + 完整日志的地址。没有问题时是 `null`。 */
export interface IssueDetails {
  label: string;
  items: { key: string; label: string; href: string; note: string; hint: string }[];
  /** 完整记录的本机路径，原样一串：前缀文字和打开它的那颗键归页面，那里才知道摆在哪。 */
  log: string;
}

export function issueDetails(state: LibraryProcessingData): IssueDetails | null {
  const issues = state.issue_preview || [];
  if (!issues.length) return null;
  return {
    label: state.issues_truncated
      ? `问题清单：共 ${state.issue_count || 0} 项，展开查看前 ${issues.length} 项`
      : `问题清单：共 ${state.issue_count || 0} 项`,
    items: issues.map((issue, at) => ({
      key: `${issue.asset_id ?? 'source'}-${at}`,
      label: issue.title || (issue.asset_id ? `视频 ${issue.asset_id}` : '媒体来源'),
      href: issue.asset_id ? `/assets/${issue.asset_id}` : '',
      note: issue.message,
      hint: issue.path || '',
    })),
    log: state.issues_log || '',
  };
}

/* 完成用通知报一次。常驻任务面和数据管理页的卡片看的是同一个任务，谁先看到结束谁报，
   另一处按任务号认出来就不再报。首次引导添加文件夹后那一趟常在跳到目录页之前就跑完
   （一个文件一秒），任务面从没见过「运行中」，所以刚结束的任务第一次读到时也报。 */
const ANNOUNCED_KEY = 'peach.library-processing.announced';
const FRESH_COMPLETION_SECONDS = 120;

export function freshProcessingResult(state: LibraryProcessingData): boolean {
  const age = state.completed_at == null ? Infinity : Date.now() / 1000 - state.completed_at;
  return !!state.job_id && age >= 0 && age < FRESH_COMPLETION_SECONDS;
}

export function announceCompletion(
  state: LibraryProcessingData, toast: (message: string) => void, witnessed: boolean,
): void {
  if (state.status !== 'complete') return;
  if (!witnessed && !freshProcessingResult(state)) return;
  try {
    if (state.job_id && localStorage.getItem(ANNOUNCED_KEY) === state.job_id) return;
    if (state.job_id) localStorage.setItem(ANNOUNCED_KEY, state.job_id);
  } catch { /* 存储不可用时照常提示，最多重复一次 */ }
  if (state.requested_stage === 'previews') {
    toast(`视频预览已生成：${state.previews || 0} 部`);
    return;
  }
  toast(`扫描与资料采集已完成：识别 ${state.identified || 0} 个番号，`
    + `整理 ${state.candidates || 0} 组资料结果，自动保存 ${state.auto_applied || 0} 条`
    + (state.previews ? `，生成 ${state.previews} 部视频预览` : ''));
}
