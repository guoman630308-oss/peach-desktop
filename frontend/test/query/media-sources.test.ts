/* 来源可达性的那一个查询键：壳的 `loadMediaSources` 与 React 页面的 `prefetchMediaSources` 读写同一份缓存。 */
import { afterEach, expect, it, vi } from 'vitest';

import { loadMediaSources, MEDIA_SOURCES_KEY, QueryObserver, queryClient, type MediaSourcesData } from '@peach/query';

import { prefetchMediaSources } from '../../src/react/media-sources';

const ONLINE: MediaSourcesData = { sources: [{ location: 'local', online: true }, { location: '115', online: false }] };
const BACK: MediaSourcesData = { sources: [{ location: 'local', online: true }, { location: '115', online: true }] };

/** 每次 `fetch` 挂起，由用例按顺序放行；带进来的 `signal` 中止时以 `AbortError` 收场。 */
function pendingFetch() {
  const calls: { path: string; signal: AbortSignal | null; answer: (body: MediaSourcesData) => void }[] = [];
  const fetcher = vi.fn((path: string, init: RequestInit = {}) => new Promise((resolve, reject) => {
    const signal = init.signal ?? null;
    signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    calls.push({ path, signal, answer: (body) => resolve({ ok: true, status: 200, json: async () => body }) });
  }));
  vi.stubGlobal('fetch', fetcher);
  return calls;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => { queryClient.clear(); vi.unstubAllGlobals() });

it('壳每检测一次都向服务端问一遍，结果落在页面读的那个键里', async () => {
  const calls = pendingFetch();
  const first = loadMediaSources();
  await flush();
  calls[0]!.answer(ONLINE);
  expect(await first).toEqual(ONLINE);
  const second = loadMediaSources();
  await flush();
  calls[1]!.answer(BACK);
  expect(await second).toEqual(BACK);
  expect(calls.map((call) => call.path)).toEqual(['/api/sources', '/api/sources']);
  expect(queryClient.getQueryData(MEDIA_SOURCES_KEY)).toEqual(BACK);
});

it('同时在途的壳与页面合成一次请求，两边拿到同一个对象', async () => {
  const calls = pendingFetch();
  const shell = loadMediaSources();
  const page = prefetchMediaSources(new AbortController().signal);
  await flush();
  expect(calls).toHaveLength(1);
  calls[0]!.answer(ONLINE);
  await page;
  expect(await shell).toBe(queryClient.getQueryData(MEDIA_SOURCES_KEY));
});

it('合并上的页面那一趟被页面中止时，壳自己再发一次', async () => {
  const calls = pendingFetch();
  const leaving = new AbortController();
  const page = prefetchMediaSources(leaving.signal);
  await flush();
  const shell = loadMediaSources();
  leaving.abort();
  await expect(page).rejects.toMatchObject({ name: 'AbortError' });
  await flush();
  expect(calls).toHaveLength(2);
  expect(calls[1]!.signal).toBeNull();
  calls[1]!.answer(BACK);
  expect(await shell).toEqual(BACK);
});

it('页面上最后一个观察者卸载不撤回壳那一趟', async () => {
  const calls = pendingFetch();
  const observer = new QueryObserver(queryClient, { queryKey: MEDIA_SOURCES_KEY, enabled: false });
  const stop = observer.subscribe(() => {});
  const shell = loadMediaSources();
  await flush();
  stop();
  calls[0]!.answer(BACK);
  expect(await shell).toEqual(BACK);
  expect(calls).toHaveLength(1);
});

it('取不到时照常抛出，由壳落成空表', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) })));
  await expect(loadMediaSources()).rejects.toThrow();
});

it('应用取消信号中止真实来源请求且不再重试', async () => {
  const calls = pendingFetch(), application = new AbortController();
  const request = loadMediaSources(application.signal);
  const rejection = expect(request).rejects.toMatchObject({ name: 'AbortError' });
  await flush(); expect(calls[0]!.signal).toBe(application.signal);
  application.abort(); await rejection; await flush();
  expect(calls).toHaveLength(1);
});

it('已取消应用不发来源请求，页面合并请求取消也不恢复已卸载应用', async () => {
  const calls = pendingFetch(), application = new AbortController();
  application.abort(); await expect(loadMediaSources(application.signal)).rejects.toMatchObject({ name: 'AbortError' });
  expect(calls).toHaveLength(0);
  const pageScope = new AbortController(), applicationScope = new AbortController();
  const page = prefetchMediaSources(pageScope.signal);
  await flush(); const app = loadMediaSources(applicationScope.signal);
  const pageRejection = expect(page).rejects.toMatchObject({ name: 'AbortError' });
  const appRejection = expect(app).rejects.toMatchObject({ name: 'AbortError' });
  applicationScope.abort(); pageScope.abort(); await Promise.all([pageRejection, appRejection]); await flush();
  expect(calls).toHaveLength(1);
});
