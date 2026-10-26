import { act } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { appSettingsStore } from '../../src/appearance/settings';
import { queryClient } from '../../src/react/query';
import { FollowPreferenceRows, VideoThumbnailRow } from '../../src/react/settings/service-preferences';
import { choose, click, mount, mountRoot, settle, type } from './render';

notifyManager.setScheduler((notify) => notify());

afterEach(() => { queryClient.clear(); localStorage.removeItem('peach.number.followScheduleSetting') });

function server() {
  const settings = { followInitialDays: 30, avatarRefreshDays: 90, faviconRefreshDays: 365 };
  const schedule = { available: true, enabled: true, interval_minutes: 180 };
  const thumbnails = { mode: 'precise', status: 'idle' };
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const data = url === '/api/settings' ? settings : url === '/api/follow/schedule' ? schedule : thumbnails;
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body));
      posts.push({ url, body });
      Object.assign(data, body);
    }
    return { ok: true, status: 200, json: async () => ({ ...data }) };
  }));
  return { posts, settings, schedule, thumbnails };
}

it('首次采集范围写入服务端，头像和 favicon 使用固定刷新周期', async () => {
  const { posts } = server();
  const receipt = vi.fn();
  const host = await mount(<QueryClientProvider client={queryClient}><FollowPreferenceRows receipt={receipt} /></QueryClientProvider>);
  await settle();
  await choose(host.querySelector('#followInitialDaysSetting [aria-haspopup="listbox"]'), '最近 7 天');
  await settle();
  expect(host.textContent).toContain('每 3 个月');
  expect(host.textContent).toContain('每年');
  expect(posts).toEqual([
    { url: '/api/settings', body: { followInitialDays: 7 } },
  ]);
  expect(appSettingsStore().value.followInitialDays).toBe(7);
  expect(receipt.mock.calls).toEqual([['已保存首次采集范围']]);
});

it('缩略图选择提交原档位，保存后的控件显示采集间隔', async () => {
  const { posts } = server();
  const host = await mount(<QueryClientProvider client={queryClient}><VideoThumbnailRow /></QueryClientProvider>);
  await settle();
  await choose(host.querySelector('[aria-haspopup="listbox"]'), '每 30 秒一张');
  await settle();
  expect(posts).toEqual([{ url: '/api/thumbnail-jobs', body: { mode: 'coarse' } }]);
  expect(host.querySelector('[aria-haspopup="listbox"]')?.textContent).toContain('每 30 秒一张');
});

it('自动检查间隔拒绝范围外值，只在有效提交时写入原接口', async () => {
  const { posts } = server();
  const host = await mount(<QueryClientProvider client={queryClient}><FollowPreferenceRows receipt={vi.fn()} /></QueryClientProvider>);
  await settle();
  const input = host.querySelector<HTMLInputElement>('input[aria-label="自动检查间隔"]')!;
  await type(input, '2');
  await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  expect(posts).toEqual([]);
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('15–10080');
  await type(input, '240');
  await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  await settle();
  expect(posts).toEqual([{ url: '/api/follow/schedule', body: { enabled: true, interval_minutes: 240 } }]);
});

it('服务设置重新进入时读取最新状态，关闭自动检查后保留间隔', async () => {
  const { posts, settings, thumbnails } = server();
  const view = () => <QueryClientProvider client={queryClient}>
    <FollowPreferenceRows receipt={vi.fn()} /><VideoThumbnailRow />
  </QueryClientProvider>;
  const first = await mountRoot(view());
  await settle();
  await click(first.host.querySelector('input[role="switch"]'));
  await settle();
  expect(localStorage.getItem('peach.number.followScheduleSetting')).toBe('180');
  await first.unmount();
  settings.followInitialDays = 7;
  thumbnails.mode = 'coarse';
  const second = await mount(view());
  await settle();
  expect(second.querySelector('#followInitialDaysSetting [aria-haspopup="listbox"]')?.textContent).toContain('最近 7 天');
  expect(second.textContent).toContain('每 3 个月');
  expect(second.querySelector('#videoThumbnailSetting [aria-haspopup="listbox"]')?.textContent).toContain('每 30 秒一张');
  expect(second.querySelector<HTMLInputElement>('input[role="switch"]')?.checked).toBe(false);
  await click(second.querySelector('input[role="switch"]'));
  await settle();
  expect(posts).toEqual([
    { url: '/api/follow/schedule', body: { enabled: false, interval_minutes: 60 } },
    { url: '/api/follow/schedule', body: { enabled: true, interval_minutes: 180 } },
  ]);
  expect(second.querySelector<HTMLInputElement>('input[aria-label="自动检查间隔"]')?.value).toBe('180');
});
