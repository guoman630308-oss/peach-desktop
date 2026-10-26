/* 链接管理的数据契约。
 *
 * 外链是别人服务器上的东西，会在我们不知情的时候烂掉——实测 719 条里 152 条打不开，
 * 而它们在资料页上和好链接长得一模一样，只有点下去才知道。所以检查要能随时重跑。
 *
 * 三份真相三个键，节律各不相同：
 * - `['links']` 是库里链接的现状，纯读库、随页面一起加载，不轮询。
 * - `['links','check']` 是那趟联网检查的状态，`running` 时两秒问一次。
 * - `['links','prune']` 是删除那趟的状态，同样按 `running` 开关。
 * 合成一个键的话，每两秒的一轮轮询都会把上面那块只读统计重画一遍。 */
import { apiGet, apiSend } from '../../api';

export const LINKS_URL = '/api/links';
export const LINK_CHECK_URL = '/api/links/check';
export const LINK_PRUNE_URL = '/api/links/prune';

export const LINKS_KEY = ['links'] as const;
export const LINK_CHECK_KEY = ['links', 'check'] as const;
export const LINK_PRUNE_KEY = ['links', 'prune'] as const;

/** 链接类型与界面上的名字。
 *
 *  `官网 · 事务所` 里的间隔点会和「按类型」那行的分隔点撞在一起，读出来是
 *  「社媒 373 · 官网 · 事务所 224」——分不清哪个数字属于哪一类，所以用斜杠。 */
export const LINK_KINDS: Record<string, string> = {
  official: '官网/事务所', social: '社交账号',
  catalog: '作品资料站', source_reference: '资料出处',
};

export interface LinkStats {
  total: number;
  entities: number;
  by_kind: Record<string, number>;
  top_hosts: [string, number][];
}

/** 一条被判出来的链接。`note` 是站点回的那句结果。 */
export interface LinkRow {
  id: number;
  entity: string;
  link_kind: string;
  label: string;
  url: string;
  note: string;
}

export interface LinkCheckState {
  status: 'idle' | 'running' | 'complete' | 'failed';
  check_id: string;
  checked: number;
  total: number;
  gone: LinkRow[];
  unclear: LinkRow[];
  /** `retry` 是只重验点名的那几条。 */
  scope: string;
  error?: string;
}

export interface LinkPruneState {
  status: 'idle' | 'running' | 'complete' | 'failed';
  job_id?: string;
  checked?: number;
  total?: number;
  message?: string;
  removed?: number;
  /** 已隐退女优的链接不删，留作不可点的记录。 */
  marked?: number;
  recovered?: number;
  error?: string;
}

/** 这几条写端点拒绝时回的是 200 加 `ok:false`（清单过期、没有可重试的行），不是错误码。 */
async function accepted<T>(request: Promise<T & { ok?: boolean; error?: string }>): Promise<T> {
  const result = await request;
  if (result.ok === false) throw new Error(result.error || '操作未完成，请重试');
  return result;
}

export const fetchLinkStats = (signal?: AbortSignal) => apiGet<LinkStats>(LINKS_URL, signal);

/** 检查状态是 POST 读的：这条端点同时负责启动，`status_only` 才是只问不启动。 */
export const fetchLinkCheck = (signal?: AbortSignal) =>
  apiSend<LinkCheckState>(LINK_CHECK_URL, { status_only: true }, 'POST', signal);

/** 起一趟检查。给了 `retry` 就只重验点名的那几条，别的结论原样留着：重跑整批要好几分钟。 */
export const startLinkCheck = ({ retry, checkId }: { retry?: readonly number[]; checkId?: string }) =>
  accepted(apiSend<LinkCheckState>(LINK_CHECK_URL, retry
    ? { retry, check_id: checkId }
    : { restart: true }));

export const fetchLinkPrune = (signal?: AbortSignal) => apiGet<LinkPruneState>(LINK_PRUNE_URL, signal);

/** 删除判定为失效的链接。服务端删前逐条重验一次，所以这一趟走后台任务；不可撤销，
 *  调用方必须先过确认弹层。 */
export const startLinkPrune = ({ checkId, ids }: { checkId: string; ids?: number[] }) =>
  accepted(apiSend<LinkPruneState>(LINK_PRUNE_URL, { confirm: true, check_id: checkId, background: true, ...(ids ? { ids } : {}) }));

const count = (value: number | undefined) => Number(value || 0).toLocaleString();

/** 删完那一句。 */
export const pruneText = (state: LinkPruneState) =>
  `已删除 ${count(state.removed)} 条；${state.marked ? `已隐退女优的 ${count(state.marked)} 条留作不可点的记录；` : ''}`
  + `保留 ${count(state.recovered)} 条未确认失效的链接。`;

/** 检查推进到哪儿了。总量还没算出来时只说在做什么。 */
export function checkLine(state: LinkCheckState): string {
  const retrying = state.scope === 'retry';
  if (!state.total) return retrying ? '正在重验链接' : '正在检查链接';
  return `${retrying ? '已重验' : '已检查'} ${count(state.checked)} / ${count(state.total)} 条链接`;
}
