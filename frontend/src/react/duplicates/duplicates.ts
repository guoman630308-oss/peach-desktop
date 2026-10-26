/* 重复文件的数据契约与折算。
 *
 * 判据是「同番号 + 时长相近 + 分卷标记一致」，不是同番号即重复——合集、分卷和混入的广告
 * 都会共用一个 code，只按番号做「保留最大」会删掉内容。服务端已经按这条判完，前端只挑
 * 每组留谁。批量一律走 `dispose` 进回收站，可逆；永久删除仍只能从回收站单独执行。
 *
 * 数据管理页那张「重复文件」卡片读的是同一份真相的 `total`／`files`／`reclaimable`，
 * 所以它接下面这个 `DUPLICATES_KEY`，不另发一次 `?limit=1`：服务端两种 limit 都要把整张
 * 表扫一遍分完组，省下的只是回程字节，换来的却是两处读数可能对不上。 */
import { apiGet, apiSend } from '../../api';
import { prefetchMediaSources } from '../media-sources';
import { queryClient } from '../query';

/** 120 组够铺满这一页，再多的由「可回收」排序挤到后面。 */
export const DUPLICATES_URL = '/api/duplicates?limit=120';
/** 整页与数据管理页那张卡片共用这一个键。 */
export const DUPLICATES_KEY = ['duplicates'] as const;
/** 批量改判。单次上限 200，超过的分批发。 */
export const BATCH_URL = '/api/batch';
export const BATCH_LIMIT = 200;

/** `/api/duplicates` 里的一个文件。字段与 `web_batch.q_duplicates` 对齐。 */
export interface DuplicateFile {
  id: number;
  name: string;
  path: string | null;
  location: string;
  drive: string;
  size: number | null;
  duration: number | null;
  is_largest: boolean;
  is_longest: boolean;
}

export interface DuplicateGroup {
  code: string;
  files: DuplicateFile[];
  count: number;
  /** 每个文件都有 sha1 且完全相同才算确证字节一致。 */
  identical: boolean;
  /** `same_code_short_copy` 是同番号下一份明显短一截的副本。 */
  evidence?: string;
  drives: string[];
  cross_drive: boolean;
  reclaimable: number;
}

export interface DuplicatesData {
  total: number;
  files: number;
  reclaimable: number;
  groups: DuplicateGroup[];
  has_more?: boolean;
}

export const fetchDuplicates = (signal?: AbortSignal) =>
  apiGet<DuplicatesData>(DUPLICATES_URL, signal);

/* 名字之间在哪里断开算一个词：空格、连字符、下划线、点、括号与路径分隔符。 */
const NAME_BREAK = /[\s\-_.()[\]【】/\\]/;

/** 一组副本共有的开头，退到最后一个断词处：同一部片子的几份常只在中段不同（分辨率、
 *  转载站、序号），整名中段截断正好把唯一的差别切掉。共有段不足 8 个字或只有一份时为空。 */
export function sharedNamePrefix(names: readonly string[]): string {
  if (names.length < 2) return '';
  let end = 0;
  const first = names[0]!;
  while (end < first.length && names.every((name) => name[end] === first[end])) end += 1;
  // 共有段一直到某一个名字的结尾时，那个名字整个都是共有段，退一格留出能显示的部分。
  if (names.some((name) => name.length <= end)) end = Math.max(0, end - 1);
  let cut = end;
  while (cut > 0 && !NAME_BREAK.test(first[cut - 1]!)) cut -= 1;
  return cut >= 8 ? first.slice(0, cut) : '';
}

/** 组内显示的名字：去掉组里共有的开头，用「…」顶替；组头已经写着番号。 */
export const distinctName = (name: string, prefix: string) =>
  prefix && name.startsWith(prefix) && name.length > prefix.length ? `…${name.slice(prefix.length)}` : name;

/** 首屏：重复分组与配了根目录的网盘（「留 115」那几颗键要它），取完才画。不给 `staleTime`，
 *  `/duplicates` 是 `refresh:'reopen'`，进来就重取。 */
export async function prefetchDuplicates(signal: AbortSignal): Promise<void> {
  await Promise.all([
    queryClient.fetchQuery({ queryKey: DUPLICATES_KEY, queryFn: () => fetchDuplicates(signal) }),
    prefetchMediaSources(signal),
  ]);
}

/** 每组保留哪一个的口径。`all` 是一个不留——整组都是广告时才用得上。其余是网盘代号。 */
export type KeepRule = 'largest' | 'longest' | 'all' | (string & {});

/** 确认弹层里「每组保留……」那半句。 */
export const KEEP_LABELS: Record<string, string> = {
  largest: '最大的一个', longest: '最长的一个',
  '115': '115（没有则留最大）', pikpak: 'PikPak（没有则留最大）', all: '零个文件',
};

/** 这一批文件里真正出现过的网盘来源。 */
export const cloudPreferenceLocations = (
  files: readonly { location: string }[], configured: readonly string[],
) => configured.filter((location) => files.some((file) => file.location === location));

/** 「留 115」只在这一组既有 115 又有别处文件时出现：整组都在同一个网盘，留它和留最大
 *  是同一个结果，多一颗键只是多一个要读的选项。 */
export const mixedCloudPreferences = (
  files: readonly { location: string }[], configured: readonly string[],
) => cloudPreferenceLocations(files, configured)
  .filter((location) => files.some((file) => file.location !== location));

/** 批量条上给哪几颗「全部优先」：至少有一组值得这么挑。 */
export const bulkCloudPreferences = (groups: readonly DuplicateGroup[], configured: readonly string[]) =>
  configured.filter((location) => groups.some((group) => mixedCloudPreferences(group.files, [location]).length));

/** 每组只留一个，其余进回收站；返回要回收的 id。
 *
 *  指名网盘时先在那个来源里挑最大的，那里没有就退回全组里最大的那个：口径写在弹层上
 *  （「115（没有则留最大）」），换个写法就对不上用户刚刚读到的那句话。 */
export function duplicateVictims(groups: readonly DuplicateGroup[], keep: KeepRule): number[] {
  const ids: number[] = [];
  for (const group of groups) {
    if (keep === 'all') {
      for (const file of group.files) ids.push(file.id);
      continue;
    }
    let keeper: DuplicateFile | undefined;
    if (keep === 'largest' || keep === 'longest') {
      const flag = keep === 'longest' ? 'is_longest' : 'is_largest';
      keeper = group.files.find((file) => file[flag]) ?? group.files[0];
    } else {
      const preferred = group.files.filter((file) => file.location === keep);
      const pool = preferred.length ? preferred : group.files;
      keeper = pool.reduce((best, file) => ((file.size || 0) > (best.size || 0) ? file : best), pool[0]!);
    }
    for (const file of group.files) if (file.id !== keeper?.id) ids.push(file.id);
  }
  return ids;
}

/** 这一批被回收的文件一共多少字节。确认弹层要点名这个数。 */
export function victimBytes(groups: readonly DuplicateGroup[], ids: readonly number[]): number {
  const victims = new Set(ids);
  return groups.reduce((total, group) => total + group.files.reduce(
    (sum, file) => sum + (victims.has(file.id) ? file.size || 0 : 0), 0), 0);
}

/** 批量改判。一次最多 200 条，分批串行发：并发发出去只是让服务端自己排队。 */
export async function batchOperate(ids: readonly number[], operation: 'dispose' | 'restore'): Promise<void> {
  for (let offset = 0; offset < ids.length; offset += BATCH_LIMIT) {
    await apiSend(BATCH_URL, { ids: ids.slice(offset, offset + BATCH_LIMIT), operation,
      ...(operation === 'dispose' ? { disposal_reason: 'duplicate' } : {}) });
  }
}

/** 页头说明行与汇总块那一句。 */
export const duplicatesLede = (data: DuplicatesData, formatSize: (bytes: number) => string) =>
  `${data.total} 组 · ${data.files} 个文件 · 可回收 ${formatSize(data.reclaimable)}`;
