/* island 层唯一的取数入口。
 *
 * 和遗留层用的 `api()`（`src/core/index.ts`）保持同一套失败语义（先读 JSON，再按
 * `message`／`detail`／`error` 取人能看的原因），但多两件事：返回类型由调用方声明，
 * 请求带 `AbortSignal`。第二件是 island 必需的——页面在取数途中被换掉时，
 * 迟到的响应不能再往新页面上写东西。 */

/** 服务端明确回了非 2xx。`status` 留给调用方区分 401／409／404 这类需要不同处置的情况。 */
import { requestErrorMessage } from './core/request-error';
import { registerResponseEntities } from './core/entity-links';

export class ApiError extends Error {
  readonly status: number;
  /** 响应体原样。表单校验那种 400 会在 `errors` 里按字段给原因，页面要把它们写回原位。 */
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown = null) {
    super(requestErrorMessage(message,status));
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

const reasonOf = (payload: unknown): string => {
  if (!payload || typeof payload !== 'object') return '';
  const body = payload as Record<string, unknown>;
  for (const key of ['message', 'detail', 'error']) {
    const value = body[key];
    if (typeof value === 'string' && value) return value;
  }
  return '';
};

/** 把抛出来的东西收敛成一句能显示给人看的原因。
 *
 * 一次取数失败会同时落到两处——路由树打开时的首屏状态和共享 store 的错误态——
 * 两边显示的必须是同一句话，否则同一个失败会因为落在哪儿而说法不同。 */
export const errorMessage = (cause: unknown): string => requestErrorMessage(cause);

/** GET 一个 `/api/...` 契约端点。`signal` 中止时抛出 `AbortError`，调用方据此放弃写 DOM。 */
export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: 'application/json' },
    // 页面口令是 httponly cookie，取数必须带上；跨源请求不在此列。
    credentials: 'same-origin',
    ...(signal ? { signal } : {}),
  });
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    // 失败响应不一定是 JSON（例如 401 的登录页），下面用状态码兜底。
  }
  if (!response.ok) {
    throw new ApiError(reasonOf(payload) || `请求失败（${response.status}）`, response.status);
  }
  registerResponseEntities(path, payload);
  return payload as T;
}

/** 带 JSON 请求体的写操作。失败时原响应体挂在 `ApiError.body` 上，字段级原因由调用方取。 */
export async function apiSend<T>(path: string, body: unknown, method = 'POST', signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method, ...(signal ? { signal } : {}),
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    // 同上：失败响应不一定是 JSON。
  }
  if (!response.ok) {
    throw new ApiError(reasonOf(payload) || `请求失败（${response.status}）`, response.status, payload);
  }
  return payload as T;
}
