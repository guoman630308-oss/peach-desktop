/* 云下载弹层的行为：提交带什么、成功后回执并关上、失败留在原处、没配置与只读端说什么。 */
import { act } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { CloudDownloadDialog, type DownloadPrefill } from '../../src/react/activity/cloud-download-dialog';
import type { DownloadsSnapshot, DownloadSubmitResult, DownloadTask } from '../../src/react/bundle';
import { queryClient } from '../../src/react/query';
import { buttonNamed, choose, mount, section, settle, submit, type } from './render';

afterEach(() => queryClient.clear());

const task = (patch: Partial<DownloadTask> = {}): DownloadTask => ({
  id: 1, info_hash: 'a'.repeat(40), provider: '115', provider_label: '115', source_uri: 'magnet:?xt=urn:btih:' + 'a'.repeat(40),
  display_name: 'ABC-123.mp4', target: '/115/云下载', code: 'ABC-123', title: null, origin: 'paste',
  state: 'submitted', state_label: '已提交', failure: null, failure_label: null, failure_detail: null,
  progress: null, remote_name: null, ledger_path: null, asset_id: null, submitted_at: '2026-10-01T08:00:00Z',
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

/** 读接口回 `shown`；写接口回 `answer`。 */
function serve(shown: DownloadsSnapshot, answer?: DownloadSubmitResult) {
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_input: string, init?: RequestInit) => {
    if (init?.method === 'POST') posts.push(JSON.parse(String(init.body)));
    return { ok: true, status: 200, json: async () => (init?.method === 'POST' ? answer : shown) };
  }));
  return posts;
}

/** 弹层画在 `body` 上的浮层容器里，不在挂载点里，所以从整页找。 */
const open = async (prefill: DownloadPrefill, provider?: string) => {
  const close = vi.fn();
  const receipt = vi.fn();
  await mount(
    <QueryClientProvider client={queryClient}>
      <CloudDownloadDialog prefill={prefill} close={close} receipt={receipt} provider={provider} />
    </QueryClientProvider>,
  );
  // 取到下载配置之后才画表单或那句说明；Query 的通知走定时器，只等微任务不够。
  await vi.waitFor(async () => {
    await settle();
    if (!document.querySelector('[role=dialog] form, [role=dialog] [role=note], [role=dialog] .p-5 > *')) {
      throw new Error('弹层还没取到下载配置');
    }
  });
  return { close, receipt, page: document.body };
};

const field = (root: HTMLElement, label: string) =>
  [...root.querySelectorAll('label')].find((node) => node.textContent?.trim() === label)
    ?.closest('[data-input-size]')?.querySelector('input') ?? null;

const MAGNET = 'magnet:?xt=urn:btih:' + 'b'.repeat(40);

it('带着番号与标题打开：标题行写番号与片名，搜索框与番号框已填，提交带上渠道、目录与来处', async () => {
  const posts = serve(snapshot(), { ok: true, outcome: 'submitted', task: task() });
  const { close, receipt, page } = await open({ code: 'ABC-123', title: '一部作品', origin: 'asset:12', searchReason: '中字' });
  expect(page.querySelector('[role=dialog]')?.textContent).toContain('ABC-123 · 一部作品');
  expect(page.textContent).toContain('版本目标：中字');
  expect(field(page, '按番号查找资源')?.value).toBe('ABC-123');
  expect(field(page, '番号')).toBeNull();
  expect(field(page, '目标目录')?.value).toBe('/115/云下载');
  await type(field(page, '磁力链接'), MAGNET);
  await submit(section(page, '提交下载'));
  await settle();
  expect(posts).toEqual([{
    magnet: MAGNET, provider: '115', target: '/115/云下载', code: 'ABC-123', title: '一部作品', origin: 'asset:12',
  }]);
  expect(receipt).toHaveBeenCalledWith('已提交离线下载，进度在任务中心');
  expect(close).toHaveBeenCalled();
});

it('提交没成功时弹层不关，原因留在表单里', async () => {
  serve(snapshot(), { ok: false, outcome: 'failed',
    task: task({ state: 'failed', failure_label: '没有可用的资源', failure_detail: '远端拒收' }) });
  const { close, receipt, page } = await open({});
  await type(field(page, '磁力链接'), MAGNET);
  await submit(section(page, '提交下载'));
  await settle();
  expect(page.textContent).toContain('提交没有成功：没有可用的资源：远端拒收');
  expect([close.mock.calls.length, receipt.mock.calls.length]).toEqual([0, 0]);
});

it('查找另一番号时清空先前的磁力和作品资料，资料未取得时提交不带其他作品的标题', async () => {
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') posts.push(JSON.parse(String(init.body)));
    return { ok: true, status: 200, json: async () => init?.method === 'POST'
      ? { ok: true, outcome: 'submitted', task: task() }
      : url.startsWith('/api/items') ? { items: [{ id: 2, code: 'ABC-9999', name: '其他作品' }] }
      : url.startsWith('/api/resources') ? { state: 'ready', warnings: [], error: '', items: [] }
      : snapshot() };
  }));
  const { page } = await open({ code: 'ABC-123', title: '原作品', origin: 'asset:12', performers: '甲', duration: 600 });
  await type(field(page, '磁力链接'), MAGNET);
  await type(field(page, '按番号查找资源'), 'ABC-999');
  await act(async () => field(page, '按番号查找资源')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  await settle();
  expect(page.querySelector('[data-resource-work]')?.textContent).toContain('作品资料未取得');
  expect(page.querySelector('[data-resource-work]')?.textContent).not.toContain('原作品');
  expect(field(page, '磁力链接')?.value).toBe('');
  await type(field(page, '磁力链接'), MAGNET);
  await submit(section(page, '提交下载'));
  expect(posts).toEqual([{ magnet: MAGNET, provider: '115', target: '/115/云下载', code: 'ABC-999' }]);
});

it('入库页交进来的下载方式先选中；换渠道时目标目录跟着换，115 才提配额', async () => {
  serve(snapshot());
  const { page } = await open({}, 'pikpak');
  expect(field(page, '目标目录')?.value).toBe('/云下载');
  expect(page.textContent).not.toContain('扣一条离线配额');
  expect(buttonNamed('提交下载', page)?.className).toContain('bg-button-primary');
  await choose(page.querySelector('[role=dialog] [aria-label="下载到"]'), '115');
  expect(field(page, '目标目录')?.value).toBe('/115/云下载');
  expect(page.textContent).toContain('每提交一次扣一条离线配额');
});

it('没有下载渠道时可以查找资源，提交下载禁用', async () => {
  serve(snapshot({ providers: [
    { key: '115', label: '115', configured: false, target: '' },
    { key: 'pikpak', label: 'PikPak', configured: false, target: '' },
  ] }));
  const { page } = await open({});
  expect(section(page, '查找资源')).not.toBeNull();
  expect(buttonNamed('提交下载', page)?.disabled).toBe(true);
  expect(page.textContent).toContain('下载渠道设置');
  expect(page.querySelector('[data-resource-work]')).toBeNull();
});

it.each(['abf-258', 'ABF258', 'ABF-258'])('查询同番号 %s 保留已知作品资料和来处', async requested => {
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') posts.push(JSON.parse(String(init.body)));
    return { ok: true, status: 200, json: async () => init?.method === 'POST'
      ? { ok: true, outcome: 'submitted', task: task() }
      : url.startsWith('/api/items') ? { items: [] }
      : url.startsWith('/api/resources') ? { state: 'ready', warnings: [], error: '', items: [] }
      : snapshot() };
  }));
  const { page } = await open({ code: 'ABF-258', title: '同一作品', origin: 'wishlist:12', cover: '/cover?code=ABF-258', performers: '甲' });
  const details = page.querySelector('[data-resource-work]')!;
  expect(details.compareDocumentPosition(section(page, '查找资源')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await type(field(page, '按番号查找资源'), requested);
  await act(async () => field(page, '按番号查找资源')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  await settle();
  expect(details.textContent).toContain('同一作品');
  expect(details.querySelector('img')?.getAttribute('src')).toBe('/cover?code=ABF-258');
  await type(field(page, '磁力链接'), MAGNET);
  await submit(section(page, '提交下载'));
  expect(posts).toEqual([{ magnet: MAGNET, provider: '115', target: '/115/云下载', code: requested, title: '同一作品', origin: 'wishlist:12' }]);
});

it('只读端可查找资源，提交下载禁用', async () => {
  serve(snapshot({ available: false }));
  const { page } = await open({});
  expect(page.textContent).toContain('云下载只在允许修改数据库的设备上可用');
  expect(section(page, '查找资源')).not.toBeNull();
  expect(buttonNamed('提交下载', page)?.disabled).toBe(true);
});
