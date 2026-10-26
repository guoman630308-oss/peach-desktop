import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() });

it('独立页面的 API 错误反馈不初始化界面分段器', async () => {
  vi.resetModules();
  const segmenter = vi.spyOn(Intl, 'Segmenter');
  const { errorMessage } = await import('../src/api');
  expect(errorMessage(new TypeError('Failed to fetch'))).toContain('无法连接到 Peach 服务');
  expect(segmenter).not.toHaveBeenCalled();
});

for (const method of ['GET', 'POST']) {
  for (const bodyFinishes of [false, true]) {
    it(`${method} 在读取响应体时取消，不提交${bodyFinishes ? '迟到数据' : '空结果'}`, async () => {
      const { apiGet, apiSend } = await import('../src/api');
      const controller = new AbortController();
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => {
        controller.abort();
        if (!bodyFinishes) throw controller.signal.reason;
        return { has_more: false, items: [] };
      } })));
      const request = method === 'GET' ? apiGet('/api/index', controller.signal)
        : apiSend('/api/example', {}, method, controller.signal);
      await expect(request).rejects.toMatchObject({ name: 'AbortError' });
    });
  }
  it(`${method} 的非 JSON 错误响应保留 HTTP 错误状态`, async () => {
    const { apiGet, apiSend } = await import('../src/api');
    vi.stubGlobal('fetch', vi.fn(async () => new Response('login', { status: 401 })));
    const request = method === 'GET' ? apiGet('/api/example') : apiSend('/api/example', {});
    await expect(request).rejects.toMatchObject({ name: 'ApiError', status: 401 });
  });
}
