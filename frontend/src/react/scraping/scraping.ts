/* 来源和凭证页的数据契约与折算。几条端点在 `frontend/src` 里都只在这里声明一次
 * （`tests/test_frontend_build.py` 盯着）。
 *
 * 来源配置、封面任务、amane 桥和自定义来源各自缓存；后台任务按自己的节律读取状态，
 * 不重取正在编辑的来源配置。 */
import { apiGet } from '../../api';
import { queryClient } from '../query';
import { CUSTOM_SOURCES_KEY, fetchCustomSources } from './custom-source-data';

export const SCRAPING_URL = '/api/scraping';
export const SCRAPING_SETTINGS_URL = '/api/scraping/settings';
export const SCRAPING_CHECK_URL = '/api/scraping/check';
export const SCRAPING_COVER_URL = '/api/scraping/cover';
export const AMANE_BRIDGE_URL = '/api/scraping/amane-bridge';
export const AMANE_BRIDGE_CHECK_URL = '/api/scraping/amane-bridge/check';
export const AMANE_BRIDGE_REBUILD_URL = '/api/scraping/amane-bridge/rebuild';

/** 来源列表共用这一个键。 */
export const SCRAPING_KEY = ['scraping'] as const;
/** 抓封面那一趟后台任务的状态。 */
export const COVER_JOB_KEY = ['scraping', 'cover'] as const;
/** amane 桥的事实与重建任务的状态（`q_scraping_amane_bridge`）。 */
export const AMANE_BRIDGE_KEY = ['scraping', 'amane-bridge'] as const;

/** 一个采集来源。字段以 `q_scraping`（`src/peach/web_scraping.py`）为准。 */
export interface Source {
  source: string;
  label: string;
  login: string;
  accepts_cookie: boolean;
  network: string;
  cookie_saved: boolean;
  /** 这台机器上这个来源的请求由本机浏览器发、验证由它自己过（ADR-0065）；为真时不需要 Cookie。 */
  browser: boolean;
  cooldown_until?: number | null;
}

export interface ScrapingData {
  sources: Source[];
}

/** 一次连接检查的一项结果：来源页面，以及部分来源另有的高清图片地址。 */
export interface Check {
  label: string;
  ok: boolean;
  status?: number;
  message?: string;
  width?: number;
  height?: number;
  reason?: string;
  elapsed_s?: number;
  checked_at?: number;
}

/** 抓封面的后台任务快照（`scraping_cover_job`）。`status` 是 `idle|running|complete|failed`。 */
export interface CoverJob {
  status: string;
  job_id?: string;
  result?: string;
  error?: string;
  width?: number;
  height?: number;
}

/** amane 桥那张卡（ADR-0043）。`revision` 是清单里钉的 amane sha，`version` 是锁里那一条的
 *  版本号；`installed` 是 venv 里有没有解释器；`sites` 是经桥开放给来源链的站。`job` 是重建
 *  任务的快照，`status` 同 `CoverJob`。 */
export interface AmaneBridge {
  repository: string;
  license: string;
  revision: string;
  version: string;
  installed: boolean;
  installed_version: string;
  python: string;
  sites: { source: string; label: string }[];
  job: CoverJob;
}

/** Netscape Cookie 文件的上限。再大的多半不是 Cookie 文件，先拦住再说。 */
export const COOKIE_TEXT_LIMIT = 256 * 1024;

export const fetchSources = (signal?: AbortSignal) => apiGet<ScrapingData>(SCRAPING_URL, signal);
export const fetchCoverJob = (signal?: AbortSignal) => apiGet<CoverJob>(SCRAPING_COVER_URL, signal);
export const fetchAmaneBridge = (signal?: AbortSignal) => apiGet<AmaneBridge>(AMANE_BRIDGE_URL, signal);

/** 首屏来源与任务快照并发取回，同一份缓存交给页面各分区。
 *
 * 抓封面的状态也算首屏——页面一上来就得知道有没有一趟正在跑，才接得上它的进度；amane 桥
 * 那张卡同理，重建可能正在后台跑。中止时 `fetchQuery` 把 `AbortError` 抛回给挂载方，它据此
 * 放弃这一次。 */
export async function prefetchScraping(signal: AbortSignal): Promise<void> {
  await Promise.all([
    queryClient.fetchQuery({ queryKey: SCRAPING_KEY, queryFn: () => fetchSources(signal) }),
    queryClient.fetchQuery({ queryKey: COVER_JOB_KEY, queryFn: () => fetchCoverJob(signal) }),
    queryClient.fetchQuery({ queryKey: AMANE_BRIDGE_KEY, queryFn: () => fetchAmaneBridge(signal) }),
    queryClient.fetchQuery({ queryKey: CUSTOM_SOURCES_KEY, queryFn: () => fetchCustomSources(signal) }),
  ]);
}

/** 一条检查结果说成一句话：谁、通不通、图有多大、服务端补充了什么。
 *
 * 结果里的 `label` 是这一跳查的是什么；只有一跳时它就是来源页面本身，句子里不必再说一遍。 */
export function checkText(check: Check, sourceLabel: string): string {
  return `${sourceLabel}${check.label === '来源页面' ? '' : ' 高清图片'}`
    + `：${check.ok ? '可连接' : '不能连接'}`
    + (check.width ? ` · ${check.width} × ${check.height}` : '')
    + (check.elapsed_s !== undefined ? ` · ${check.elapsed_s.toFixed(2)} 秒` : '')
    + (check.message ? `。${check.message}` : '');
}
