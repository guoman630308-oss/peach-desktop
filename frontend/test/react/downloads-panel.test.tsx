/* 活动页云下载段的行为：任务卡给哪些键、没有任务时整段不画、只读端不给写操作。 */
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { DownloadsPanel, downloadPollInterval } from '../../src/react/activity/downloads-panel';
import type { DownloadsSnapshot, DownloadTask } from '../../src/react/bundle';
import { queryClient } from '../../src/react/query';
import { buttonNamed, click, mount, settle } from './render';

afterEach(() => queryClient.clear());

const task = (patch: Partial<DownloadTask> = {}): DownloadTask => ({
  id: 1, info_hash: 'a'.repeat(40), provider: '115', provider_label: '115', source_uri: 'magnet:?xt=urn:btih:' + 'a'.repeat(40),
  display_name: 'ABC-123.mp4', target: '/115/云下载', code: 'ABC-123', title: null, origin: 'paste',
  state: 'remote_running', state_label: '远端下载中', failure: null, failure_label: null, failure_detail: null,
  progress: 0.42, remote_name: null, ledger_path: null, asset_id: null, submitted_at: '2026-10-01T08:00:00Z',
  updated_at: '2026-10-01T08:01:00Z', finished_at: null, blocked: false, cancellable: true, resubmittable: false,
  postprocess_state: 'not_requested', postprocess_label: '未开始', postprocess_detail: null,
  postprocess_run_id: null, postprocessed_at: null, postprocess_active: false, postprocess_retryable: false,
  ...patch,
});

const snapshot = (patch: Partial<DownloadsSnapshot> = {}): DownloadsSnapshot => ({
  available: true,
  providers: [
    { key: '115', label: '115', configured: true, target: '/115/云下载' },
    { key: 'pikpak', label: 'PikPak', configured: true, target: '/云下载' },
  ],
  tasks: [],
  ...patch,
});

type Call = [string, RequestInit | undefined];

/** 读接口回 `shown`；写接口按地址回 `answers` 里那一份。 */
function serve(shown: DownloadsSnapshot, answers: Record<string, unknown> = {}) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    calls.push([input, init]);
    const body = init?.method === 'POST' ? answers[input] : shown;
    return { ok: true, status: 200, json: async () => body };
  }));
  const posts = () => calls.filter(([, init]) => init?.method === 'POST')
    .map(([url, init]) => [url, JSON.parse(String(init!.body))]);
  return { calls, posts };
}

const show = async () => {
  const host = await mount(<QueryClientProvider client={queryClient}><DownloadsPanel /></QueryClientProvider>);
  // 首屏取数要走完 fetch、json 与 Query 派发三段，等到这一段画出内容为止。
  await vi.waitFor(async () => {
    await settle();
    if (!host.childElementCount) throw new Error('云下载段还没画出来');
  });
  return host;
};

it('远端在跑的任务给进度与取消键，取消发到取消接口', async () => {
  const { posts } = serve(snapshot({ tasks: [task()] }), { '/api/downloads/cancel': { ok: true, task: task() } });
  const host = await show();
  const card = host.querySelector('[data-download-state=remote_running]')!;
  expect(card.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')).toBe('42');
  expect(card.textContent).toContain('远端下载中');
  expect(buttonNamed('重新提交', card)).toBeNull();
  await click(buttonNamed('取消', card));
  await settle();
  expect(posts()).toEqual([['/api/downloads/cancel', { id: 1 }]]);
});

it('失败的任务写中文原因，可以重新提交；被判违规的只剩复制磁力', async () => {
  const failed = task({ id: 2, state: 'failed', state_label: '失败', failure: 'no_source', cancellable: false,
    resubmittable: true, failure_label: '没有可用的资源', failure_detail: '远端任务列表里找不到它' });
  const blocked = task({ id: 3, state: 'failed', state_label: '失败', failure: 'rejected', cancellable: false,
    blocked: true, failure_label: '被判违规', failure_detail: '50038' });
  const { posts } = serve(snapshot({ tasks: [failed, blocked] }),
    { '/api/downloads': { ok: true, outcome: 'submitted', task: failed } });
  const host = await show();
  const [first, second] = [...host.querySelectorAll('[data-download-state=failed]')];
  expect(first?.textContent).toContain('没有可用的资源：远端任务列表里找不到它');
  expect(second?.textContent).toContain('被判违规');
  expect([buttonNamed('取消', second!), buttonNamed('重新提交', second!)]).toEqual([null, null]);
  expect(buttonNamed('复制磁力', second!)).not.toBeNull();
  await click(buttonNamed('重新提交', first!));
  await settle();
  expect(posts()).toEqual([['/api/downloads', {
    magnet: failed.source_uri, provider: '115', target: '/115/云下载', code: 'ABC-123', title: null, origin: 'paste',
  }]]);
});

it('只读端任务照常列出，但没有取消键', async () => {
  serve(snapshot({ available: false, tasks: [task()] }));
  const host = await show();
  expect(host.querySelector('[data-download-state=remote_running]')).not.toBeNull();
  expect(buttonNamed('取消', host)).toBeNull();
});

it('后处理失败可以单独重试，不会重新提交磁力', async () => {
  const failed = task({
    state: 'ingested', state_label: '已入库', cancellable: false,
    postprocess_state: 'failed', postprocess_label: '后处理失败',
    postprocess_detail: '资料采集失败', postprocess_retryable: true,
  });
  const { posts } = serve(snapshot({ tasks: [failed] }), {
    '/api/downloads/postprocess': { ok: true, task: failed },
  });
  const host = await show();
  expect(host.textContent).toContain('资料采集失败');
  await click(buttonNamed('重试后处理', host));
  await settle();
  expect(posts()).toEqual([['/api/downloads/postprocess', { id: 1 }]]);
});

it('一条任务都没有时整段不画，也没有提交表单', async () => {
  const { calls } = serve(snapshot());
  const host = await mount(<QueryClientProvider client={queryClient}><DownloadsPanel /></QueryClientProvider>);
  await vi.waitFor(async () => {
    await settle();
    if (!calls.length) throw new Error('还没取数');
  });
  await settle();
  expect(host.childElementCount).toBe(0);
});

it('有任务还在跟进时五秒查一次，全是终态三十秒一次', () => {
  expect(downloadPollInterval(snapshot({ tasks: [task()] }))).toBe(5_000);
  expect(downloadPollInterval(snapshot({ tasks: [task({ cancellable: false, state: 'ingested' })] }))).toBe(30_000);
  expect(downloadPollInterval(snapshot({ tasks: [task({
    cancellable: false, state: 'ingested', postprocess_active: true, postprocess_state: 'running',
  })] }))).toBe(5_000);
  expect(downloadPollInterval(undefined)).toBe(30_000);
});
