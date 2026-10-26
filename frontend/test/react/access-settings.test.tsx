import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';

import type { AccessState } from '../../src/react/bundle';
import { AccessSettings } from '../../src/react/settings/access-settings';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount());
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

async function mount(initial: AccessState, receipt = vi.fn()) {
  const host = document.createElement('div');
  host.className = 'peach-react';
  document.body.append(host);
  const root = createRoot(host);
  roots.push(root);
  await act(async () => root.render(<AccessSettings initial={initial} receipt={receipt} />));
  return host;
}

/** React 用自己记下的值判断输入有没有变，只派发事件不经过原生 setter 时它会忽略。 */
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
const checkbox = (host: HTMLElement) => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
const newPasswords = (host: HTMLElement) => [...host.querySelectorAll<HTMLInputElement>('input[autocomplete="new-password"]')];

it('系统口令可直接转为可选密码且关闭需要明确提交', async () => {
  const receipt = vi.fn();
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ mode: 'open', revision: 'next' }) }));
  vi.stubGlobal('fetch', fetcher);
  const host = await mount({ mode: 'legacy', revision: 'legacy' }, receipt);
  expect(host.querySelector('[autocomplete="current-password"]')).toBeNull();
  expect(host.querySelector('button[type="submit"]')?.textContent).toBe('保存配置');
  await click(checkbox(host));
  expect(newPasswords(host)).toHaveLength(2);
  expect(newPasswords(host).every((input) => input.disabled)).toBe(true);
  expect(host.querySelector('[role="note"]')?.textContent).toContain('直接访问馆藏');
  expect(fetcher).not.toHaveBeenCalled();
  await submit(host);
  expect(sentBody(fetcher)).toMatchObject({ action: 'disable', confirm_disable: true });
  expect(receipt).toHaveBeenCalledWith('已关闭访问密码');
  expect(host.querySelector('[role="note"]')?.textContent).toContain('未设置访问密码');
});

it('关闭选项位于输入上方且保留草稿并要求当前密码验证', async () => {
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ mode: 'open', revision: 'next' }) }));
  vi.stubGlobal('fetch', fetcher);
  const host = await mount({ mode: 'password', revision: 'one' });
  const current = host.querySelector<HTMLInputElement>('#access-current')!;
  expect(checkbox(host).compareDocumentPosition(current) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  for (const input of newPasswords(host)) await type(input, 'draft-password');
  await click(checkbox(host));
  expect(newPasswords(host).every((input) => input.disabled && input.value === 'draft-password')).toBe(true);
  expect(current.disabled).toBe(false);
  expect(current.getAttribute('aria-required')).toBe('true');
  await click(checkbox(host));
  expect(newPasswords(host).every((input) => !input.disabled && input.value === 'draft-password')).toBe(true);
  await click(checkbox(host));
  await submit(host);
  expect(current.getAttribute('aria-invalid')).toBe('true');
  expect(fetcher).not.toHaveBeenCalled();
  await type(current, 'current-password');
  await submit(host);
  expect(sentBody(fetcher)).toMatchObject({
    action: 'disable', confirm_disable: true, current_password: 'current-password', password: '', confirmation: '',
  });
});

it('服务端按字段给的原因写回对应字段', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: false, status: 400,
    json: async () => ({ message: '当前访问密码不正确', errors: { current_password: '当前访问密码不正确' } }),
  })));
  const host = await mount({ mode: 'password', revision: 'one' });
  for (const input of host.querySelectorAll<HTMLInputElement>('input[type="password"]')) await type(input, 'example-password');
  await submit(host);
  const current = host.querySelector<HTMLInputElement>('#access-current')!;
  expect(current.getAttribute('aria-invalid')).toBe('true');
  const described = (current.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
  expect(described.map((id) => document.getElementById(id)?.textContent)).toContain('当前访问密码不正确');
});

it('没有密码时画成警示，提交不合法内容变为字段错误且不发请求', async () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  const host = await mount({ mode: 'open', revision: 'one' });
  expect(host.querySelector('[role="note"]')?.textContent).toContain('未设置访问密码');
  expect(host.textContent).toContain('至少 8 个字符');
  const [password, confirm] = newPasswords(host) as [HTMLInputElement, HTMLInputElement];
  await type(password, 'short');
  await type(confirm, 'other');
  await submit(host);
  expect(password.getAttribute('aria-invalid')).toBe('true');
  expect(confirm.getAttribute('aria-invalid')).toBe('true');
  expect(host.textContent).toContain('访问密码需为 8–256 个字符');
  expect(host.textContent).toContain('两次输入的密码不一致');
  expect(fetcher).not.toHaveBeenCalled();
});
