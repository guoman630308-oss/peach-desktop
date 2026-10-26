import { act } from 'react';
import { QueryClientProvider, notifyManager } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { queryClient } from '../../src/react/query';
import { WantMagnets, type MagnetCandidate } from '../../src/react/wants/want-magnets';
import type { Want } from '../../src/react/wants/wants';
import { WANTS_KEY, type WantsData } from '../../src/react/wants/wants';
import { buttonNamed, click, mount, mountRoot, pending, settle } from './render';
import { cachedResources, rememberResources, resourceLifetime } from '../../src/react/wants/resource-cache';
import { resourceNotice, resourceSize } from '../../src/react/wants/resource-display';

notifyManager.setScheduler((notify) => notify());
afterEach(() => { queryClient.clear(); localStorage.clear() });
const candidate: MagnetCandidate = {
  id: 'magnet:abc', protocol: 'magnet', info_hash: 'abc', uri: 'magnet:?xt=urn:btih:abc',
  name: 'DEMO-001 HD', size: '4 GB', files: '2 個文件', date: '2026-10-01', attributes: ['字幕', '高清'],
  source: 'JavDB', source_url: 'https://javdb.com/v/demo', origins: ['https://javdb.com/v/demo'],
};
async function open(items = [candidate], readOnly = false, ok = true) {
  vi.stubGlobal('IntersectionObserver', undefined);
  const toast = vi.fn();
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => ({ ok: true, status: 200,
    json: async () => init?.method === 'POST'
      ? { ok, outcome: ok ? 'submitted' : 'failed', task: { failure_detail: '远端拒绝' } }
      : { state: 'ready', items, error: '', checked_at: null },
  }));
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<QueryClientProvider client={queryClient}>
    <WantMagnets want={{ id: 1, code: 'DEMO-001', title: '演示作品' } as Want} readOnly={readOnly}
      downloads={{ available: true, providers: [{ key: '115', label: '115', configured: true, target: '/下载' }], tasks: [] }}
      provider="115" toast={toast} />
  </QueryClientProvider>);
  await settle();
  return { host, fetcher, toast };
}
it('自动查询，展示来源、大小、日期与属性，直接添加到选定的云下载目录', async () => {
  const { host, fetcher, toast } = await open();
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/wants/magnets?id=1');
  for (const text of ['4 GB', '2 个文件', '2026-10-01', '字幕', 'JavDB']) expect(host.textContent).toContain(text);
  expect(host.querySelector('[data-resource-facts]')?.textContent).not.toContain('高清');
  expect([...host.querySelectorAll('[data-resource-actions] button, [data-resource-actions] a')].map(node => node.textContent)).toEqual(['添加下载', '复制链接', '链接来源']);
  await click(buttonNamed('添加下载', host));
  await settle();
  const writes = fetcher.mock.calls.filter(([, init]) => init?.method === 'POST');
  expect(JSON.parse(String(writes[0]?.[1]?.body))).toEqual({ magnet: candidate.uri, provider: '115', target: '/下载',
    code: 'DEMO-001', title: '演示作品', origin: 'wishlist:1' });
  expect(buttonNamed('已添加', host)?.disabled).toBe(true);
  expect(toast).toHaveBeenCalledWith('已添加下载：DEMO-001 HD');
});
it('ed2k 与网页链接可复制，不能作为磁链提交', async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const ed2k = { ...candidate, id: 'ed2k:abc:12', protocol: 'ed2k' as const, uri: 'ed2k://|file|demo|12|abc|/' };
  const { host } = await open([ed2k]);
  expect(buttonNamed('添加下载', host)).toBeNull();
  await click(buttonNamed('复制链接', host));
  expect(writeText).toHaveBeenCalledWith(ed2k.uri);
});
it('提交等待期保持按钮可聚焦并拦截重复点击', async () => {
  const { host, fetcher, toast } = await open();
  const held = pending<{ ok: boolean; outcome: string; task: { failure_detail: string } }>();
  fetcher.mockImplementationOnce(async () => ({ ok: true, status: 200, json: () => held.answer }));
  const add = buttonNamed('添加下载', host)!;
  await click(add);
  await click(add);
  expect(add.getAttribute('aria-busy')).toBe('true');
  expect(add.disabled).toBe(false);
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  expect(toast).not.toHaveBeenCalled();
  await held.release({ ok: true, outcome: 'submitted', task: { failure_detail: '' } });
  await settle();
  expect(buttonNamed('已添加', host)?.disabled).toBe(true);
});
it('只读时禁止提交，远端失败留在卡片并保留重试', async () => {
  const reader = await open([candidate], true);
  expect(buttonNamed('添加下载', reader.host)?.disabled).toBe(true);
  const writer = await open([candidate], false, false);
  await click(buttonNamed('添加下载', writer.host));
  await settle();
  expect(writer.host.textContent).toContain('远端拒绝');
  expect(buttonNamed('已添加', writer.host)).toBeNull();
  expect(writer.toast).not.toHaveBeenCalled();
});

it('更多资源默认折叠且协议有独立标记', async () => {
  const { host } = await open(Array.from({ length: 8 }, (_, index) => ({ ...candidate, id: String(index) })));
  const more = host.querySelector('details')!;
  expect(more.open).toBe(false);
  expect(host.querySelector('summary')?.textContent).toContain('5 条');
  expect(host.querySelector('[data-resource-protocol="magnet"] svg')).not.toBeNull();
  await click(more.querySelector('summary'));
  expect(more.open).toBe(true);
});

it('待找清单查询多个来源，不采用快速添加的单来源持久缓存', async () => {
  rememberResources('DEMO-001', { state: 'ready', items: [{ ...candidate, source: 'manko.fun' }], error: '', checked_at: null });
  const { host, fetcher } = await open([candidate, { ...candidate, id: 'alternate', source: 'sukebei' }]);
  expect(fetcher).toHaveBeenCalled();
  expect(host.textContent).toContain('JavDB');
  expect(host.textContent).toContain('sukebei');
});

it('ED2K 类型使用文字，字节容量按馆藏口径展示', async () => {
  const { host } = await open([{ ...candidate, protocol: 'ed2k', size: '27,311,519,717 B' }]);
  expect(host.textContent).toContain('ED2K');
  expect(host.textContent).toContain('25.4 GB');
  expect(buttonNamed('添加下载', host)).toBeNull();
  expect(resourceSize('1.61 GB')).toBe('1.61 GB');
  expect(resourceSize('')).toBe('大小未知');
  expect(resourceSize('123')).toBe('大小未知');
  expect(resourceNotice('更多评论需要 JavDB VIP 或官方 App。')).toBe('来源只返回了部分评论，资源链接可能不完整。');
});

it('作品详情按自身来源查询资源，想要缓存不能代替公开来源结果', async () => {
  vi.stubGlobal('IntersectionObserver', undefined);
  rememberResources('DEMO-001', { state: 'ready', items: [candidate], error: '', checked_at: null });
  const fetcher = vi.fn(async (_url: string) => ({ ok: true, status: 200,
    json: async () => ({ state: 'ready', items: [], error: '', checked_at: null }) }));
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<QueryClientProvider client={queryClient}><WantMagnets
    want={{ id: 0, code: 'DEMO-001', title: '示例作品', release_date: '2010-01-01' }}
    queryUrl="/api/jav/resources?code=DEMO-001" readOnly provider="" toast={vi.fn()} /></QueryClientProvider>);
  await settle();
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/jav/resources?code=DEMO-001');
  expect(host.textContent).toContain('暂未找到资源链接');
  await click(buttonNamed('重新查询', host));
  await settle();
  expect(fetcher.mock.calls[1]?.[0]).toBe('/api/jav/resources?code=DEMO-001&refresh=1');
});

it('发行一个月边界、月末与缓存有效期一致', () => {
  const now = Date.parse('2026-10-03T12:00:00Z');
  expect(resourceLifetime('2026-09-03', now)).toBe(86_400_000);
  expect(resourceLifetime('2026-09-02', now)).toBe(Infinity);
  expect(resourceLifetime(null, now)).toBe(86_400_000);
  expect(resourceLifetime('2026-01-31', Date.parse('2026-02-28T12:00:00Z'))).toBe(86_400_000);
});

it('资源缓存只接受可展示的字段和完整查询结果', () => {
  const saved = Date.now();
  const result = { state: 'ready', items: [candidate], error: '', checked_at: null };
  const { protocol: _protocol, attributes: _attributes, ...incomplete } = candidate;
  localStorage.setItem('peach.want-resources.v1', JSON.stringify([
    { code: 'DEMO-001', saved, result: { ...result, items: [{ ...incomplete, size: 1024 }] } },
    { code: 'DEMO-002', saved, result: { ...result, items: [], warnings: ['来源未取得'] } },
    { code: 'DEMO-003', saved, result },
  ]));
  expect(cachedResources('DEMO-001', null)).toBeUndefined();
  expect(cachedResources('DEMO-002', null)).toBeUndefined();
  expect(cachedResources('DEMO-003', null)?.result.items).toEqual([candidate]);
});

it('页面与查询客户端重建后复用持久缓存，手动查询强制更新来源', async () => {
  vi.stubGlobal('IntersectionObserver', undefined);
  const want = { id: 1, code: 'DEMO-001', release_date: '2020-01-01' } as Want;
  rememberResources(`all-sources:${want.code!}`, { state: 'ready', items: [candidate], error: '', checked_at: null });
  const fetcher = vi.fn(async (_url: string) => ({ ok: true, status: 200, json: async () =>
    ({ state: 'ready', items: [candidate], error: '', checked_at: null }) }));
  vi.stubGlobal('fetch', fetcher);
  const render = () => <QueryClientProvider client={queryClient}><WantMagnets want={want} readOnly
    provider="" toast={() => {}} /></QueryClientProvider>;
  const first = await mountRoot(render());
  await settle();
  await first.unmount();
  queryClient.clear();
  const second = await mountRoot(render());
  await settle();
  expect(second.host.textContent).toContain(candidate.name);
  expect(fetcher).not.toHaveBeenCalled();
  await click(buttonNamed('重新查询', second.host));
  await settle();
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/wants/magnets?id=1&refresh=1');
  expect(cachedResources(`all-sources:${want.code!}`, want.release_date)?.result.items).toHaveLength(1);
});

it('过期的内存缓存随卡片进入视口重新查询', async () => {
  let reveal: (() => void) | undefined;
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
      reveal = () => callback([{ isIntersecting: true }]);
    }
    observe() {}
    disconnect() {}
  });
  const want = { id: 1, code: 'DEMO-001', release_date: null } as Want;
  const result = { state: 'ready', items: [candidate], error: '', checked_at: null };
  queryClient.setQueryData(['want-magnets', want.id, want.code], result,
    { updatedAt: Date.now() - 366 * 86_400_000 });
  const fetcher = vi.fn(async () => ({ ok: true, status: 200, json: async () => result }));
  vi.stubGlobal('fetch', fetcher);
  await mount(<QueryClientProvider client={queryClient}><WantMagnets want={want} readOnly
    provider="" toast={() => {}} /></QueryClientProvider>);
  await settle();
  expect(fetcher).not.toHaveBeenCalled();
  await act(async () => reveal!());
  await settle();
  expect(fetcher).toHaveBeenCalledOnce();
});

it('已取得磁链更新清单筛选状态，ED2K 不计作磁链', async () => {
  queryClient.setQueryData<WantsData>(WANTS_KEY, { items: [{ id: 1 } as Want], counts: {} as WantsData['counts'], scraping: false });
  await open();
  expect(queryClient.getQueryData<WantsData>(WANTS_KEY)?.items[0]?.has_magnets).toBe(true);
  queryClient.removeQueries({ queryKey: ['want-magnets'] });
  localStorage.clear();
  await open([{ ...candidate, protocol: 'ed2k', uri: 'ed2k://|file|demo|12|abc|/' }]);
  expect(queryClient.getQueryData<WantsData>(WANTS_KEY)?.items[0]?.has_magnets).toBe(false);
});
