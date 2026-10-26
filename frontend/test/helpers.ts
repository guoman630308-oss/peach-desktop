import { vi } from 'vitest';

/** 一个可手动兑现的 fetch：island 的等待窗口本身就是被测行为，不能靠 setTimeout 猜。 */
export function deferredFetch(body: unknown) {
  let settle = (): void => {};
  let signal: AbortSignal | undefined;
  const fetched = vi.fn((_input: string, init?: RequestInit) => {
    signal = init?.signal ?? undefined;
    return new Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>((resolve, reject) => {
      settle = () => resolve({ ok: true, status: 200, json: async () => body });
      signal?.addEventListener('abort', () => {
        reject(new DOMException('已中止', 'AbortError'));
      });
    });
  });
  return {
    fetched,
    resolve: () => settle(),
    signal: () => signal,
    install: () => vi.stubGlobal('fetch', fetched),
  };
}
