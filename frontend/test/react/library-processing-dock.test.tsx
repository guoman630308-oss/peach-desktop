import { act } from 'react';
import { QueryClientProvider, notifyManager } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { queryClient } from '../../src/react/query';
import { LIBRARY_PROCESSING_KEY, type LibraryProcessingData } from '../../src/react/library-processing/library-processing';
import { LibraryProcessingDock } from '../../src/react/library-processing/library-processing-dock';
import { mount } from './render';

const sonner = vi.hoisted(() => ({ custom: vi.fn(), dismiss: vi.fn() }));
vi.mock('sonner', () => ({ toast: sonner }));
notifyManager.setScheduler(notify => notify());
beforeEach(() => { sonner.custom.mockClear(); sonner.dismiss.mockClear() });
afterEach(() => { queryClient.clear(); localStorage.clear() });

async function open(state: LibraryProcessingData) {
  queryClient.setQueryData(LIBRARY_PROCESSING_KEY, state);
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => state })));
  await mount(<QueryClientProvider client={queryClient}><LibraryProcessingDock toast={vi.fn()} /></QueryClientProvider>);
}

it('页面打开时历史失败留在数据管理，不自动弹出任务卡', async () => {
  await open({ status: 'failed', job_id: 'old', issue_count: 7276,
    completed_at: Date.now() / 1000 - 4 * 86400, error: '7276 项需要处理' });
  expect(sonner.custom).not.toHaveBeenCalled();
  expect(sonner.dismiss).toHaveBeenCalled();
});

it('本次观察到的任务失败持续显示，关闭后保持收起', async () => {
  await open({ status: 'running', job_id: 'current' });
  sonner.custom.mockClear();
  const failed = { status: 'failed', job_id: 'current', completed_at: Date.now() / 1000 - 600 };
  await act(async () => { queryClient.setQueryData(LIBRARY_PROCESSING_KEY, failed) });
  expect(sonner.custom).toHaveBeenCalled();
  const options = sonner.custom.mock.calls.at(-1)?.[1] as { onDismiss(): void };
  options.onDismiss();
  sonner.custom.mockClear();
  await act(async () => { queryClient.setQueryData(LIBRARY_PROCESSING_KEY, { ...failed, issue_count: 1 }) });
  expect(sonner.custom).not.toHaveBeenCalled();
});

it('刚刚失败的短任务仍可显示', async () => {
  await open({ status: 'failed', job_id: 'fresh', completed_at: Date.now() / 1000 - 10 });
  expect(sonner.custom).toHaveBeenCalled();
});
