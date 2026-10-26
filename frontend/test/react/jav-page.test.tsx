import { afterEach, expect, it, vi } from 'vitest';
import { QueryClientProvider, notifyManager } from '@tanstack/react-query';
import { queryClient } from '../../src/react/query';
import { JavPage } from '../../src/react/jav/jav-page';
import { JavHeader } from '../../src/react/jav/jav-header';
import { discoveryKey, prefetchJav } from '../../src/react/jav/jav';
import { WANTS_KEY } from '../../src/react/wants/wants';
import { buttonNamed, click, mount, settle, type } from './render';

notifyManager.setScheduler(notify => notify());
afterEach(() => queryClient.clear());

it('发现已有结果即时呈现，陈旧推荐在后台更新', async () => {
  const cached = { items: [{ id: 1, code: 'ABC-001' }], candidate_total: 1, limited: false };
  queryClient.setQueryData(discoveryKey('external'), cached, { updatedAt: Date.now() - 60_000 });
  let finish!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { finish = resolve })));
  const loaded = prefetchJav('discover', new AbortController().signal);
  const result = await Promise.race([loaded.then(() => 'ready'), new Promise(resolve => setTimeout(() => resolve('waiting'), 40))]);
  expect(result).toBe('ready');
  expect(queryClient.getQueryData(discoveryKey('external'))).toEqual(cached);
  finish({ ok: true, status: 200, json: async () => cached } as Response);
  await loaded;
});

it('想要已有清单立即返回，陈旧数据在后台更新', async () => {
  const cached = { items: [], counts: {}, scraping: false };
  queryClient.setQueryData(WANTS_KEY, cached, { updatedAt: Date.now() - 60_000 });
  let finish!: (response: Response) => void;
  const request = new Promise<Response>(resolve => { finish = resolve });
  const fetcher = vi.fn(() => request);
  vi.stubGlobal('fetch', fetcher);
  await prefetchJav('wants', new AbortController().signal);
  expect(queryClient.getQueryData(WANTS_KEY)).toEqual(cached);
  expect(fetcher).toHaveBeenCalledOnce();
  finish({ ok: true, status: 200, json: async () => cached } as Response);
  await settle();
  await prefetchJav('wants', new AbortController().signal);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('JAV 分区导航保留三个入口，馆藏使用目录搜索', async () => {
  const navigate = vi.fn(), find = vi.fn();
  const host = await mount(<JavHeader tab="library" navigate={navigate} find={find} />);
  const tabs = [...host.querySelectorAll('[role="tab"]')];
  expect(tabs.map(tab => tab.textContent?.trim())).toEqual(['馆藏', '发现', '想要']);
  expect(tabs[0]?.getAttribute('aria-selected')).toBe('true');
  expect(host.querySelector('input')).toBeNull();
  await click(tabs[1]);
  expect(navigate).toHaveBeenLastCalledWith('/jav?tab=discover');
  await click(tabs[2]);
  expect(navigate).toHaveBeenLastCalledWith('/jav?tab=wants');
  await click(buttonNamed('查找资源', host));
  expect(find).toHaveBeenCalledOnce();
});

it('从想要分区返回馆藏时打开 JAV 目录', async () => {
  const navigate = vi.fn();
  const host = await mount(<JavHeader tab="wants" navigate={navigate} find={vi.fn()} />);
  expect(host.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim()).toBe('想要');
  await click(host.querySelector('[role="tab"]'));
  expect(navigate).toHaveBeenCalledWith('/?jav=1');
});

it('发现按范围读取，清除文字和标签恢复列表，卡片打开对应详情', async () => {
  const navigate = vi.fn();
  const common = { title: '示例作品', performers: '女优', studio: '厂牌', has_cover: false,
    release_date: '2010-01-01', wanted: false, reasons: ['常看的厂牌：厂牌'] };
  const fetcher = vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => ({
    items: url.includes('scope=library') ? [{ ...common, id: 22, asset_id: 22, code: 'ABC-022', tags: ['恋足'] }]
      : [{ ...common, id: 1, code: 'ABC-001', score: 1, tags: ['恋足'], link: 'https://example.test/work' },
        { ...common, id: 2, code: 'ABC-002', score: 2, tags: ['剧情'] }],
  }) }));
  vi.stubGlobal('fetch', fetcher);
  const host = await mount(<QueryClientProvider client={queryClient}><JavPage tab="discover" readOnly navigate={navigate} receipt={vi.fn()} /></QueryClientProvider>);
  await settle();
  expect(host.querySelectorAll('[data-jav-card]')).toHaveLength(2);
  expect(host.querySelector('[data-jav-card] h3')?.textContent).toContain('ABC-001');
  await click(host.querySelector('[data-jav-card] [data-media-tag]'));
  expect(host.querySelectorAll('[data-jav-card]')).toHaveLength(1);
  await click(buttonNamed('清除筛选', host));
  expect(host.querySelectorAll('[data-jav-card]')).toHaveLength(2);
  await type(host.querySelector('input[type="search"]'), 'ABC-001');
  expect(host.querySelectorAll('[data-jav-card]')).toHaveLength(1);
  await type(host.querySelector('input[type="search"]'), '');
  expect(host.querySelectorAll('[data-jav-card]')).toHaveLength(2);
  await click(host.querySelector('[data-jav-card] h3'));
  expect(navigate).toHaveBeenLastCalledWith('/works/ABC-001');
  await click(buttonNamed('馆藏探索', host));
  await settle();
  expect(fetcher.mock.calls.some(([url]) => url.includes('scope=library'))).toBe(true);
  await click(host.querySelector('[data-jav-card] h3'));
  expect(navigate).toHaveBeenLastCalledWith('/assets/22');
  expect(buttonNamed('已入库', host)?.disabled).toBe(true);
});
