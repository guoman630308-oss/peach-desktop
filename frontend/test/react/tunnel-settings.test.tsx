import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';

import type { TunnelState } from '../../src/react/bundle';
import { TunnelSettings } from '../../src/react/settings/tunnel-settings';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** 夹具里的令牌是假值；真实令牌不进仓库，页面上也不会出现任何一份。 */
const FAKE_TOKEN = 'fake-tunnel-token-for-tests';

const STOPPED: TunnelState = {
  enabled: false, state: 'stopped', url: '', error: '', available: true,
  mode: 'quick', hostname: '', token_set: false, named_available: true,
};

const roots: Root[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount());
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

async function mount(initial: TunnelState, receipt = vi.fn()) {
  const host = document.createElement('div');
  host.className = 'peach-react';
  document.body.append(host);
  const root = createRoot(host);
  roots.push(root);
  await act(async () => root.render(
    <TunnelSettings revision="rev" initial={initial} receipt={receipt} />,
  ));
  return host;
}

async function type(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const click = (el: HTMLElement) => act(async () => el.click());
const submit = (host: HTMLElement) => act(async () => {
  host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
});
const sentBody = (fetcher: ReturnType<typeof vi.fn>) =>
  JSON.parse((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
const radio = (host: HTMLElement, value: string) =>
  host.querySelector<HTMLInputElement>(`input[type="radio"][value="${value}"]`)!;

it('切到命名隧道才出现主机名与令牌，保存键是主按钮', async () => {
  const saved = {
    ...STOPPED, mode: 'named', hostname: 'peach.example.com',
    token_set: true, revision: 'next',
  };
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => saved }));
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(STOPPED, receipt);
  expect(host.querySelector('#tunnel-hostname')).toBeNull();
  expect(host.querySelector('#tunnel-token')).toBeNull();

  await click(radio(host, 'named'));
  const hostname = host.querySelector<HTMLInputElement>('#tunnel-hostname')!;
  const token = host.querySelector<HTMLInputElement>('#tunnel-token')!;
  expect(token.type).toBe('password');
  await type(hostname, 'peach.example.com');
  await type(token, FAKE_TOKEN);
  expect(host.querySelector('button[type="submit"]')?.textContent).toBe('保存配置');

  await submit(host);
  expect(sentBody(fetcher)).toMatchObject({
    enabled: false, mode: 'named', hostname: 'peach.example.com', token: FAKE_TOKEN,
  });
  expect(receipt).toHaveBeenCalledWith('已保存配置');
});

it('保存后令牌不回显，只说已保存', async () => {
  const saved = {
    ...STOPPED, mode: 'named', hostname: 'peach.example.com',
    token_set: true, revision: 'next',
  };
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => saved })));
  const host = await mount(STOPPED);
  await click(radio(host, 'named'));
  await type(host.querySelector<HTMLInputElement>('#tunnel-token')!, FAKE_TOKEN);
  await submit(host);
  const token = host.querySelector<HTMLInputElement>('#tunnel-token')!;
  expect(token.value).toBe('');
  expect(host.textContent).not.toContain(FAKE_TOKEN);
  expect(host.textContent).toContain('已保存');
});

it('独立包不渲染模式切换，只剩开关', async () => {
  const host = await mount({ ...STOPPED, named_available: false });
  expect(host.querySelector('[role="radiogroup"]')).toBeNull();
  expect(host.querySelector('button[type="submit"]')).toBeNull();
  expect(host.textContent).toContain('启动公网入口');
});

it('主机名的字段错误落在输入框上，不写成一行红字', async () => {
  const fetcher = vi.fn(async () => ({
    ok: false,
    status: 400,
    json: async () => ({ error: '公开主机名不是有效的域名', errors: { hostname: '公开主机名不是有效的域名' } }),
  }));
  vi.stubGlobal('fetch', fetcher);
  const host = await mount({ ...STOPPED, mode: 'named' });
  await type(host.querySelector<HTMLInputElement>('#tunnel-hostname')!, 'peach.example.com/admin');
  await submit(host);
  expect(host.querySelector('#tunnel-hostname')?.getAttribute('aria-invalid')).toBe('true');
  expect(host.querySelector('[role="alert"]')).toBeNull();
});
