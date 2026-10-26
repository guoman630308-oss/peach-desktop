import { act } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { queryClient } from '../../src/react/query';
import { CustomSources } from '../../src/react/scraping/custom-sources';
import { click, mount, pending, settle, type } from './render';

notifyManager.setScheduler(notify => notify());
afterEach(() => queryClient.clear());

it('来源停用由服务端结果确认，保留配置以便再次启用', async () => {
  const row = { id: 'custom-1234567890abcdef', name: '示例来源', endpoint: 'https://example.com/{code}',
    fields: { code: 'id' }, content_types: ['censored'], enabled: true };
  const fetcher = vi.fn(async (_url, options) => ({ ok: true, status: 200,
    json: async () => ({ items: [{ ...row, enabled: options?.method === 'POST' ? false : true }] }) }));
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<QueryClientProvider client={queryClient}><CustomSources receipt={receipt} /></QueryClientProvider>);
  await settle();
  const disable = Array.from(host.querySelectorAll('button')).find(button => button.textContent === '停用');
  await click(disable!);
  await settle();
  expect(receipt).toHaveBeenCalledWith('已停用来源');
  expect(host.textContent).toContain('已停用');
  const sent = JSON.parse(fetcher.mock.calls.find(([, options]) => options?.method === 'POST')?.[1].body);
  expect(sent).toMatchObject({ id: row.id, enabled: false, fields: { code: 'id' } });
});

it('试查期间修改配置后不展示旧请求的结果', async () => {
  const row = { id: 'custom-1234567890abcdef', name: '示例来源', endpoint: 'https://example.com/{code}',
    fields: { code: 'id', title: 'title' }, content_types: ['censored'], enabled: false };
  const waiting = pending<{ ok: boolean; status: number; json(): Promise<unknown> }>();
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => options?.method === 'POST' ? waiting.answer
    : { ok: true, status: 200, json: async () => ({ items: [row] }) }));
  const host = await mount(<QueryClientProvider client={queryClient}><CustomSources receipt={() => {}} /></QueryClientProvider>);
  await settle();
  await click(Array.from(host.querySelectorAll('button')).find(button => button.textContent === '编辑'));
  const form = host.querySelector('form')!;
  const code = Array.from(form.querySelectorAll('input')).filter(input => input.type === 'text').at(-1)!;
  await type(code, 'ABW-032');
  await act(async () => { code.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })) });
  await settle();
  await type(form.querySelector<HTMLInputElement>('input[placeholder="https://example.com/api/movies/{code}"]'), 'https://example.org/{code}');
  await waiting.release({ ok: true, status: 200, json: async () => ({ record: { title: '旧请求标题' } }) });
  await settle();
  expect(host.querySelector('pre')).toBeNull();
  expect(host.textContent).not.toContain('旧请求标题');
});
