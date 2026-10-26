/* 整理（ADR-0039）的数据契约：媒体默认留在原目录，这一块是唯一会动文件名和目录的入口。
 * 两份模板、一次预览、一次执行，外加把上一批原样退回去。
 *
 * `/api/organize` 一次回整块卡片要的东西：任务快照摊在顶层，旁边是可选来源、存着的模板、
 * 预设与占位符说明。所以后台任务那一格要 `jobOf`／`withJob`：新快照只换任务那几个字段，
 * 来源与模板原样留着。执行与回滚是真实 ledger 写入，调用方必须先过确认弹层。 */
import { apiGet, apiSend } from '../../api';
import { queryClient } from '../query';
/* 模板跟着数据库走（`SYNCED_SETTING_KEYS` 里的 `organizeTemplates`），写的是设置面板同一个端点。 */
import { SETTINGS_URL } from '../settings-panel/settings-data';

export const ORGANIZE_URL = '/api/organize';
export const ORGANIZE_PREVIEW_URL = '/api/organize/preview';
export const ORGANIZE_APPLY_URL = '/api/organize/apply';
export const ORGANIZE_ROLLBACK_URL = '/api/organize/rollback';

export const ORGANIZE_KEY = ['organize'] as const;

export interface OrganizeTemplate { file: string; dir: string }

export interface OrganizeJob {
  status: 'idle' | 'running' | 'complete' | 'failed';
  job_id?: string;
  stage?: string;
  message?: string;
  checked?: number;
  total?: number;
  /** 执行那一趟落的读数。回滚那一趟没有 `moved`，有 `restored`。 */
  moved?: number;
  restored?: number;
  failed?: number;
  error?: string;
}

export interface OrganizeData extends OrganizeJob {
  locations: { location: string; roots: string[] }[];
  templates: Record<string, OrganizeTemplate>;
  presets: ({ label: string } & OrganizeTemplate)[];
  placeholders: { key: string; label: string }[];
  last_batch: string;
}

export interface OrganizePlanRow { current_path: string; target_path: string; action: string }

export interface OrganizePlan {
  counts: { change?: number; unchanged?: number; [key: string]: number | undefined };
  reasons: Record<string, number>;
  rows: OrganizePlanRow[];
  truncated: boolean;
  plan_csv?: string;
}

/** 这一次要整理什么：来源与两份模板。 */
export interface OrganizeRequest { location: string; file_template: string; dir_template: string }

/** 执行还是回滚。两条都起同一个后台任务，结果落在同一格里。 */
export type OrganizeCommand = { kind: 'apply'; request: OrganizeRequest } | { kind: 'rollback' };

export const fetchOrganize = (signal?: AbortSignal) => apiGet<OrganizeData>(ORGANIZE_URL, signal);

export async function prefetchOrganize(signal: AbortSignal): Promise<void> {
  await queryClient.fetchQuery({ queryKey: ORGANIZE_KEY, queryFn: () => fetchOrganize(signal) });
}

export const previewOrganize = (request: OrganizeRequest) =>
  apiSend<OrganizePlan>(ORGANIZE_PREVIEW_URL, request);

export const startOrganize = (command: OrganizeCommand) => command.kind === 'apply'
  ? apiSend<OrganizeJob>(ORGANIZE_APPLY_URL, { ...command.request, confirm: true })
  : apiSend<OrganizeJob>(ORGANIZE_ROLLBACK_URL, { confirm: true });

/** 确认执行的那一刻存下模板：下次进这一页看到的是自己上次执行用的那两行，而不是又一次
 *  空框。预览不存，试过又放弃的模板不会顶掉上一次的。存不进去不该挡住已经起跑的整理，所以失败不报。 */
export async function rememberTemplates(templates: Record<string, OrganizeTemplate>): Promise<void> {
  queryClient.setQueryData<OrganizeData>(ORGANIZE_KEY, (data) => (data ? { ...data, templates } : data));
  try {
    await apiSend(SETTINGS_URL, { organizeTemplates: templates });
  } catch {
    /* 只读端：模板只留在这一次的缓存里。 */
  }
}

/** 任务快照换进整块卡片：只换任务那几格，来源、模板、上一批原样留着。 */
export const withOrganizeJob = (data: OrganizeData | undefined, job: OrganizeJob) =>
  (data ? { ...data, ...job } : data);

/** 模板不合法时错误出在两个框里的一个：除了说原因，还要指出是哪一格。 */
export const DIRECTORY_TEMPLATE_ERROR = /目录模板|分隔符|盘符|`\.`/;

/** 路径的最后一段。预览只列文件名，完整路径放在悬停提示里。 */
export function baseName(path: string): string {
  const text = String(path || '');
  return text.slice(Math.max(text.lastIndexOf('\\'), text.lastIndexOf('/')) + 1);
}

const count = (value: number | undefined) => Number(value || 0).toLocaleString();

/** 预览结果那一句。只列会变的行：库里几万行里改不到的那部分是背景，不是结果。 */
export function planSummary(plan: OrganizePlan): string {
  const skipped = Object.entries(plan.reasons || {}).sort((a, b) => b[1] - a[1])
    .map(([reason, value]) => `${reason} ${count(value)}`).join(' · ');
  if (!Number(plan.counts?.change || 0)) return `没有要改的文件。${skipped ? `跳过：${skipped}。` : ''}`;
  return `${count(plan.counts.change)} 个文件会改名或移动，${count(plan.counts.unchanged)} 个已经就是目标名字`
    + `${skipped ? `；跳过 ${skipped}` : ''}。`;
}

/** 跑完的那一趟怎么说。回滚那一趟有 `restored`。 */
export function organizeOutcome(job: OrganizeJob): { text: string; failed: number; rollback: boolean } {
  const rollback = job.restored !== undefined;
  const failed = Number(job.failed || 0);
  const done = Number(rollback ? job.restored : job.moved || 0);
  const text = rollback
    ? `已退回 ${count(done)} 个文件${failed ? `，${count(failed)} 行未能退回` : ''}。`
    : `已整理 ${count(done)} 个文件${failed ? `，${count(failed)} 行未能处理` : ''}。`;
  return { text, failed, rollback };
}
