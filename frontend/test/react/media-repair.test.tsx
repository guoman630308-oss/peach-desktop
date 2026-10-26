/* 数据管理页的「媒体修复」卡片：选库、确认、起停和这一趟的下场。
 *
 * 首屏和真实路径一样先落进共用缓存（遗留层 `prefetch` 那一步），卡片挂上去直接读。 */
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import * as legacyUi from '@peach/legacy/ui';

import { MediaRepairCard } from '../../src/react/media-repair/media-repair-card';
import {
  IDLE_REPAIR, MEDIA_REPAIR_KEY, prefetchMediaRepair, REPAIR_LIBRARIES_KEY, statusLine,
  type MediaRepairState, type RepairLibrary,
} from '../../src/react/media-repair/media-repair';
import { queryClient } from '../../src/react/query';
import { buttonNamed, choose, click, fetchMock, mount, sentBody, settle } from './render';

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const idle = IDLE_REPAIR;
const libraries: RepairLibrary[] = [
  { id: '网盘', name: '网盘', icon: '115', metered: false },
  { id: 'PikPak', name: 'PikPak', icon: 'heart', metered: true },
  { id: '本机', name: '本机', icon: 'local', metered: false },
];

const card = () => mount(<QueryClientProvider client={queryClient}><MediaRepairCard /></QueryClientProvider>);

async function open(state: MediaRepairState = idle) {
  queryClient.setQueryData(MEDIA_REPAIR_KEY, state);
  queryClient.setQueryData(REPAIR_LIBRARIES_KEY, libraries);
  return card();
}

it('首屏一次取回任务快照和媒体库，卡片挂上去不再补取', async () => {
  const fetcher = vi.fn(async (path: string) => ({
    ok: true, status: 200,
    json: async () => (path === '/api/libraries' ? { libraries } : idle),
  }));
  vi.stubGlobal('fetch', fetcher);
  await prefetchMediaRepair(new AbortController().signal);
  const host = await card();
  expect(fetcher.mock.calls.map((call) => call[0]).sort()).toEqual(['/api/libraries', '/api/media-repair']);
  expect(host.querySelector('section[aria-label="媒体修复"] h3')?.textContent).toBe('媒体修复');
  expect(host.querySelector('[aria-haspopup="listbox"]')?.textContent).toBe('网盘');
});

it('下拉框里每个库都带着它的媒体库图标，选中的那个连图标一起显示在框里', async () => {
  vi.stubGlobal('fetch', fetchMock(200, idle));
  const host = await open();
  const trigger = host.querySelector('[aria-haspopup="listbox"]')!;
  expect(trigger.querySelector('use')?.getAttribute('href')).toBe('#i-fixture-115');
  await click(trigger);
  const options = [...document.querySelectorAll('[role="option"]')];
  expect(options.map((option) => option.querySelector('use')?.getAttribute('href')))
    .toEqual(['#i-fixture-115', '#i-heart', '#i-hard-drive']);
  await click(options[1]);
  expect(trigger.querySelector('use')?.getAttribute('href')).toBe('#i-heart');
  expect(trigger.textContent).toBe('PikPak');
});

type Confirmation = Parameters<typeof legacyUi.confirmModal>[0];

/** 确认弹层直接点确认，并记下弹层上写的是什么。 */
function confirmEverything() {
  const seen: Confirmation[] = [];
  vi.spyOn(legacyUi, 'confirmModal').mockImplementation((async (options: Confirmation) => {
    seen.push(options);
    await options.onConfirm?.();
    return { confirmed: true };
  }) as typeof legacyUi.confirmModal);
  return seen;
}

it('开始修复先确认，确认后把选中的库发过去，随后按服务端的进度报扫描到哪一部', async () => {
  vi.useFakeTimers();
  const seen = confirmEverything();
  const fetcher = fetchMock(200, { ...idle, status: 'running', library: 'PikPak', stage: '扫描', checked: 3, total: 12 });
  vi.stubGlobal('fetch', fetcher);
  const host = await open();
  await choose(host.querySelector('[aria-haspopup="listbox"]'), 'PikPak');
  await click(buttonNamed('开始修复', host));
  await settle();
  expect(seen[0]?.title).toBe('修复媒体库「PikPak」');
  expect(seen[0]?.confirmLabel).toBe('修复媒体库');
  expect(seen[0]?.body).toContain('每修一部都要把片子完整拉一遍');
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/media-repair');
  expect(sentBody(fetcher)).toEqual({ library: 'PikPak', restart: true });
  const bar = host.querySelector('[role="progressbar"]');
  expect([bar?.getAttribute('aria-valuenow'), bar?.getAttribute('aria-valuemax')]).toEqual(['3', '12']);
  expect(host.querySelector('p[role="status"]')?.textContent).toBe('扫描 3 / 12');
  expect(buttonNamed('停止', host)).not.toBeNull();
});

it('不在计费来源上的库，确认正文不提流量', async () => {
  const seen = confirmEverything();
  vi.stubGlobal('fetch', fetchMock(200, idle));
  const host = await open();
  await click(buttonNamed('开始修复', host));
  await settle();
  expect(seen[0]?.title).toBe('修复媒体库「网盘」');
  expect(seen[0]?.body).toContain('坏原件改名成同目录的隐藏文件');
  expect(seen[0]?.body).not.toContain('拉一遍');
});

it('修复阶段点名当前这部片，停止之后回到可以再开始的样子', async () => {
  vi.useFakeTimers();
  const fetcher = fetchMock(200, { ...idle, status: 'complete', library: '网盘', stage: '完成', repaired: 2 });
  vi.stubGlobal('fetch', fetcher);
  const host = await open({
    ...idle, status: 'running', library: '网盘', stage: '修复', checked: 1, total: 2, found: 2, message: 'FC2-PPV-1.mp4',
  });
  expect(host.querySelector('p[role="status"]')?.textContent).toBe('修复 1 / 2 · FC2-PPV-1.mp4');
  await click(buttonNamed('停止', host));
  await settle();
  expect(sentBody(fetcher)).toEqual({ stop: true });
  expect(host.querySelector('p[role="status"]')?.textContent).toBe('修好 2 部');
  expect(buttonNamed('开始修复', host)).not.toBeNull();
});

it('一部都没找到时说清楚这个库里的片子都能直接播', async () => {
  vi.useFakeTimers();
  const host = await open({ ...idle, status: 'complete', library: '网盘', stage: '完成' });
  expect(host.textContent).toContain('这个库里的片子都能直接播');
});

it('缺索引的片子卡在没装 untrunc 上时，指明去配置页的运行信息里下载', async () => {
  vi.useFakeTimers();
  const host = await open({
    ...idle, status: 'complete', library: '网盘', stage: '完成', found: 2, failed: 2, missing_tool: 2,
  });
  expect(host.querySelector('p[role="status"]')?.textContent).toBe('修好 0 部，2 部修不了');
  expect(host.textContent).toContain('2 部缺索引的片子要装上 untrunc 才修得了，下载入口在配置页的「运行信息」里');
});

it('进度读数按总数封顶：服务端报出的已查数超过总数时不写成 1,500 / 1,000', () => {
  expect(statusLine({ ...IDLE_REPAIR, status: 'running', stage: '修复', checked: 1500, total: 1000 }))
    .toBe('修复 1,000 / 1,000');
});
