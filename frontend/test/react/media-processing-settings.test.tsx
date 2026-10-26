import { QueryClientProvider, notifyManager } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { queryClient } from '../../src/react/query';
import { MediaProcessingSettings } from '../../src/react/settings/media-processing-settings';
import { click, mount, pending, settle } from './render';

notifyManager.setScheduler((notify) => notify());
afterEach(() => queryClient.clear());

it('读取已保存模式，等待服务端保存完成才更新开关和回执', async () => {
  const saved = pending<{ metadata_only: boolean }>();
  const receipt = vi.fn();
  const requests: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      requests.push(JSON.parse(String(init.body)));
      return { ok: true, json: () => saved.answer };
    }
    return { ok: true, json: async () => ({ metadata_only: false }) };
  }));
  const host = await mount(<QueryClientProvider client={queryClient}><MediaProcessingSettings receipt={receipt} /></QueryClientProvider>);
  await settle();
  const control = host.querySelector<HTMLInputElement>('input[role="switch"]') || host.querySelector<HTMLInputElement>('input[type="checkbox"]');
  expect(control).not.toBeNull();
  await click(control);
  await settle();
  expect(requests).toEqual([{ metadata_only: true }]);
  expect(receipt).not.toHaveBeenCalled();
  expect(host.textContent).toContain('正在保存');
  await saved.release({ metadata_only: true });
  await settle();
  expect(receipt).toHaveBeenCalledWith('已关闭视频预览生成');
  expect(control?.checked).toBe(false);
});

it('保存失败保留当前模式并显示可恢复的错误', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => ({
    ok: init?.method !== 'POST', status: init?.method === 'POST' ? 400 : 200,
    json: async () => init?.method === 'POST' ? { error: '设置未能保存' } : { metadata_only: true },
  })));
  const receipt = vi.fn();
  const host = await mount(<QueryClientProvider client={queryClient}><MediaProcessingSettings receipt={receipt} /></QueryClientProvider>);
  await settle();
  const control = host.querySelector<HTMLInputElement>('input[type="checkbox"]');
  await click(control);
  await settle();
  expect(control?.checked).toBe(false);
  expect(host.textContent).toContain('设置未能保存');
  expect(receipt).not.toHaveBeenCalled();
});
