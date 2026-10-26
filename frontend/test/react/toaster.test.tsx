import { act } from 'react';
import { afterAll, beforeAll, expect, it, vi } from 'vitest';

import { mountToaster, showToast, unmountToaster } from '../../src/react/toaster';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Toaster 全站只挂一次，这份文件里的用例共用同一个栈，各自用不同的 id。
const host = document.createElement('div');
beforeAll(async () => {
  document.body.append(host);
  await act(async () => mountToaster(host, { success: '<svg data-glyph="check"></svg>', error: '<svg data-glyph="alert"></svg>' }));
});
afterAll(async () => { await act(async () => unmountToaster()); host.remove(); });

const toastsWith = (text: string) => [...host.querySelectorAll<HTMLElement>('[data-sonner-toast]')]
  .filter((node) => node.textContent?.includes(text));

it('撤销的结果写回同一条，栈里不另起一条', async () => {
  const run = vi.fn();
  await act(async () => showToast('undo', {
    html: '已删除标签「<b>剧情</b>」', alert: false, timeout: 8000, action: { label: '撤销', run },
  }));
  await vi.waitFor(() => expect(toastsWith('已删除标签')).toHaveLength(1));
  const [receipt] = toastsWith('已删除标签');
  const button = receipt.querySelector<HTMLButtonElement>('[data-action]')!;
  expect(button.textContent).toBe('撤销');
  await act(async () => button.click());
  // 点了不关：请求还在路上，结果要写回这一条。
  expect(run).toHaveBeenCalledWith(button);
  expect(receipt.dataset.removed).toBe('false');

  await act(async () => showToast('undo', { html: '已撤销', alert: false, timeout: 4000, action: null }));
  await vi.waitFor(() => expect(toastsWith('已撤销')).toHaveLength(1));
  expect(toastsWith('已删除标签')).toHaveLength(0);
  // 同一个节点换了内容，撤销键跟着走掉。
  expect(toastsWith('已撤销')[0]).toBe(receipt);
  expect(receipt.querySelector('[data-action]')).toBeNull();
});

it('成功的勾画出来，失败的圈不画，正文按调用点给的 HTML 插入', async () => {
  await act(async () => showToast('ok', { html: '检查了 <b>3</b> 个来源', alert: false, timeout: Infinity, action: null }));
  await act(async () => showToast('bad', { html: '保存失败：磁盘已满', alert: true, timeout: Infinity, action: null }));
  await vi.waitFor(() => expect(toastsWith('保存失败')).toHaveLength(1));
  const [ok] = toastsWith('检查了');
  const [bad] = toastsWith('保存失败');
  expect(ok.dataset.type).toBe('success');
  expect(ok.querySelector('[data-toast-glyph="draw"] [data-glyph="check"]')).not.toBeNull();
  expect(ok.querySelector('b')?.textContent).toBe('3');
  expect(bad.dataset.type).toBe('error');
  expect(bad.querySelector('[data-toast-glyph="still"] [data-glyph="alert"]')).not.toBeNull();
});

it('同宿主重复挂载共用栈，卸载后可重挂，换宿主撤下原栈', async () => {
  const next = document.createElement('div'); document.body.append(next);
  const icons = { success: '', error: '' };
  await act(async () => mountToaster(next, icons));
  const original = next.firstElementChild;
  expect(original).not.toBeNull(); expect(host.children).toHaveLength(0);
  await act(async () => mountToaster(next, icons)); expect(next.firstElementChild).toBe(original);
  await act(async () => { unmountToaster(); unmountToaster(); }); expect(next.children).toHaveLength(0);
  await act(async () => mountToaster(next, icons)); expect(next.firstElementChild).not.toBe(original);
  await act(async () => unmountToaster()); next.remove();
});
