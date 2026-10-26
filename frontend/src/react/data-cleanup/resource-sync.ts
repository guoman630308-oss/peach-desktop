/* 资源同步的数据契约：按馆藏记录逐条查找本地磁盘与网盘上的文件（ADR-0080、ADR-0087）。
 *
 * 一次检查、一次执行。检查逐来源报文件已不在盘上的记录、空文件夹与读不了的目录，另报孤儿
 * 缓存；文件已不在盘上的记录分两档：带个人记录的标为已消失、记录留着，其余永久删除（含回收站
 * 里的）。执行只认这次检查的候选，逐条复核后处理两档，再自底向上删空文件夹、清孤儿缓存。
 *
 * 两趟后台任务两个键。扫描那一条端点同时负责启动与读状态（`status_only`），执行那一条
 * GET 读快照、POST 起任务；两边都按 `running` 开关轮询，闲着不问。
 *
 * 这一块只在有来源配了根目录时出现（`hasResourceRoots`），所以两份状态跟着来源可达性走：
 * 有根目录才进首屏预取，没有这一块的机器上，进数据管理页不多问两次。 */
import { apiGet, apiSend } from '../../api';
import { MEDIA_SOURCES_KEY, SCANNABLE_LOCATIONS, type MediaSourcesData } from '../media-sources';
import { queryClient } from '../query';

export const RESOURCE_SCAN_URL = '/api/resource-sync/scan';
export const RESOURCE_APPLY_URL = '/api/resource-sync/apply';

export const RESOURCE_SCAN_KEY = ['resource-sync', 'scan'] as const;
export const RESOURCE_APPLY_KEY = ['resource-sync', 'apply'] as const;

/** 一个来源这一趟的核对结果。离线来源跳过，只报馆藏里有多少项。 */
export interface ResourceSource {
  location: string;
  online: boolean;
  total: number;
  missing: number;
  /** `missing` 里带个人记录、执行时标为已消失的条数。 */
  vanish?: number;
  empty: number;
  unreadable: number;
}

export interface ResourceScanState {
  status: 'idle' | 'running' | 'complete' | 'failed';
  scan_id: string;
  sources?: ResourceSource[];
  completed_sources?: number;
  total_sources?: number;
  missing?: number;
  /** `missing` 拆成两档：永久删除的，和带个人记录、标为已消失的。 */
  purge?: number;
  vanish?: number;
  empty?: number;
  unreadable?: number;
  cache?: { files: number; bytes: number };
  /** 这一轮已经照着清过：读数是清理前的，结果区改画清理回执。 */
  applied?: boolean;
  error?: string;
}

/** 执行时复核发现文件其实还在的那几条：没删，报名字与原因。 */
export interface BlockedRecord { id: number; name: string; reason: string }

export interface ResourceApplyState {
  status: 'idle' | 'running' | 'complete' | 'failed';
  job_id?: string;
  message?: string;
  purged?: number;
  vanished?: number;
  /** 标了已消失、库里唯一对得上另一个版本，记录当场接过去的那几条。 */
  reattached?: number;
  blocked?: BlockedRecord[];
  dirs_removed?: number;
  dir_errors?: number;
  cache_removed?: number;
  bytes_reclaimed?: number;
  cache_blocked?: string[];
  error?: string;
}

export const fetchResourceScan = (signal?: AbortSignal) =>
  apiSend<ResourceScanState>(RESOURCE_SCAN_URL, { background: true, status_only: true }, 'POST', signal);

export const startResourceScan = () =>
  apiSend<ResourceScanState>(RESOURCE_SCAN_URL, { background: true, restart: true });

export const fetchResourceApply = (signal?: AbortSignal) =>
  apiGet<ResourceApplyState>(RESOURCE_APPLY_URL, signal);

/** 处理这次检查找出的失效记录（永久删除或标为已消失）与空文件夹，并清理闲置缓存。真实 ledger
 *  写入，永久删除那一档不可撤销，调用方必须先过危险档的确认弹层。 */
export const startResourceApply = (scanId: string) =>
  apiSend<ResourceApplyState>(RESOURCE_APPLY_URL,
    { confirm: true, clean_cache: true, scan_id: scanId, background: true });

/** 这一块出不出现：有落盘来源配了根目录才有东西可对。 */
export const hasResourceRoots = (data: MediaSourcesData | undefined) =>
  (data?.sources ?? []).some((source) => SCANNABLE_LOCATIONS.includes(source.location)
    && Array.isArray(source.roots) && source.roots.length > 0);

/** 首屏：来源可达性落进缓存之后，有根目录才取两份任务状态，取完才画。直达地址
 *  `/resource-sync` 进来要滚到这一块，结果区在滚动之后才接上的话，落点就停在它上面一屏。 */
export async function prefetchResourceSync(signal: AbortSignal): Promise<void> {
  if (!hasResourceRoots(queryClient.getQueryData<MediaSourcesData>(MEDIA_SOURCES_KEY))) return;
  await Promise.all([
    queryClient.fetchQuery({ queryKey: RESOURCE_SCAN_KEY, queryFn: () => fetchResourceScan(signal) }),
    queryClient.fetchQuery({ queryKey: RESOURCE_APPLY_KEY, queryFn: () => fetchResourceApply(signal) }),
  ]);
}

/** 这一趟有没有要清的东西。都没有时底栏换成一句「没有待清理的记录、空文件夹或缓存」。 */
export const hasChanges = (scan: ResourceScanState | undefined) =>
  Boolean(scan?.missing || scan?.empty || scan?.cache?.files);

/** 来源代号在这一块里的名字。认不出的来源统称「媒体来源」。 */
export { SOURCE_LABELS } from '../../core/media-sources';

const count = (value: number | undefined) => Number(value || 0).toLocaleString();

/** 扫描推进到哪儿了。 */
export const scanLine = (state: ResourceScanState) => (state.total_sources
  ? `已扫描 ${state.completed_sources || 0}/${state.total_sources} 个来源`
  : '正在扫描来源');

/** 永久删除那一档的条数：`missing` 减去标为已消失的。 */
export const purgeCount = (scan: ResourceScanState) =>
  scan.purge ?? Number(scan.missing || 0) - Number(scan.vanish || 0);

/** 标为已消失那一档的那一句；没有这一档时是空串。 */
const vanishText = (scan: ResourceScanState) => (scan.vanish
  ? `${count(scan.vanish)} 条带个人记录的标为已消失，记录留着，可在孤儿记录里接到新文件或彻底删除。`
  : '');

/** 不可撤销那一句：有标为已消失那一档时，只说删除的这几样。 */
const irreversible = (scan: ResourceScanState) => (scan.vanish ? '这几样不可撤销。' : '这一步不可撤销。');

/** 结果面板里那一句清理内容。 */
export const applyPlanText = (scan: ResourceScanState) =>
  `将永久删除文件已不在盘上的 ${count(purgeCount(scan))} 条记录和 ${count(scan.empty)} 个空文件夹，`
  + `并清理 ${count(scan.cache?.files)} 个闲置缓存。${irreversible(scan)}${vanishText(scan)}`;

/** 确认弹层的正文：比结果面板多说回收站里的也算、来源根目录保留。 */
export const applyConfirmText = (scan: ResourceScanState) =>
  `将永久删除文件已不在盘上的 ${count(purgeCount(scan))} 条记录（含回收站里的）、${count(scan.empty)} 个空文件夹，`
  + `并清理 ${count(scan.cache?.files)} 个闲置缓存。来源根目录保留。${irreversible(scan)}${vanishText(scan)}`;

/** 一个来源那张读数卡的脚注。 */
export const sourceMeta = (source: ResourceSource) => (source.online
  ? [`找不到文件 · 共 ${count(source.total)} 项`,
    source.vanish ? `${count(source.vanish)} 项带个人记录` : '',
    source.empty ? `空文件夹 ${count(source.empty)} 个` : '',
    source.unreadable ? `${count(source.unreadable)} 个目录读取失败，已跳过` : ''].filter(Boolean).join(' · ')
  : `馆藏中有 ${count(source.total)} 项`);

/** 执行结束后留在原地的那几样：文件其实还在、目录这会儿读不了、缓存正被占用。 */
export function applyLeftovers(out: ResourceApplyState) {
  const blocked = out.blocked ?? [];
  const dirErrors = Number(out.dir_errors || 0);
  const cacheBlocked = out.cache_blocked ?? [];
  const done = Number(out.purged || 0) + Number(out.vanished || 0) + Number(out.reattached || 0)
    + Number(out.dirs_removed || 0) + Number(out.cache_removed || 0);
  const left = blocked.length + dirErrors + cacheBlocked.length;
  const names = blocked.slice(0, 3).map((item) => `「${item.name}」`).join('、') + (blocked.length > 3 ? ' 等' : '');
  const rest = [
    blocked.length ? `${count(blocked.length)} 条记录没有删除（${names}）` : '',
    dirErrors ? `${count(dirErrors)} 个文件夹没有删除` : '',
    cacheBlocked.length ? `${count(cacheBlocked.length)} 个缓存没有清理` : '',
  ].filter(Boolean).join('，');
  return { done, left, rest };
}

/** 执行结果那一句。有没处理的几样时接在后面，说重新检查后可再试。 */
export const applyText = (out: ResourceApplyState, formatSize: (bytes: number) => string) => {
  const { rest } = applyLeftovers(out);
  const summary = `已永久删除 ${count(out.purged)} 条失效记录和 ${count(out.dirs_removed)} 个空文件夹，`
    + `清理 ${count(out.cache_removed)} 个缓存，释放 ${formatSize(out.bytes_reclaimed || 0)}。`
    + (out.vanished ? `${count(out.vanished)} 条带个人记录的已标为已消失，可在孤儿记录里处理。` : '')
    + (out.reattached ? `${count(out.reattached)} 条的记录已接到库里的另一个版本。` : '');
  return rest ? `${summary}${rest}，重新检查后可再试。` : summary;
};
