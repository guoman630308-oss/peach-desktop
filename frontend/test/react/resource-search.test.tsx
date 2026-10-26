import { act } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { ResourceSearch } from '../../src/react/activity/resource-search';
import { IndexerSettings } from '../../src/react/settings/indexer-settings';
import { SourceLink } from '../../src/react/follow-manage/source-view';
import { queryClient } from '../../src/react/query';
import { buttonNamed, click, mount, section, settle, submit, type } from './render';

afterEach(() => queryClient.clear());

it('来源名称链接到该来源', async () => {
  const host = await mount(<SourceLink source={{ id: 1, provider: 'kemono', provider_label: 'Kemono',
    ref: 'fanbox/1', label: '演示作者', url: 'https://example.com/author', enabled: true }} />);
  expect(host.textContent).toContain('演示作者');
  expect(host.querySelector('a')?.href).toBe('https://example.com/author');
});
const field = (host: HTMLElement, label: string) => [...host.querySelectorAll('label')]
  .find((node) => node.textContent?.trim() === label)?.closest('[data-input-size]')?.querySelector('input');

it('回车按番号查询，选择候选只填表单', async () => {
  const choose = vi.fn();
  const fetch = vi.fn(async (_url: string) => ({ ok: true, json: async () => ({ state: 'ready', warnings: ['一处来源未响应'],
    error: '', items: [{ id: 'a', uri: 'magnet:?xt=urn:btih:' + 'a'.repeat(40), name: 'ABC-123 4K',
      size: 1024 ** 3, seeders: 2, origins: ['测试源'], resolution: 2160, codec: 'HEVC' }] }) }));
  vi.stubGlobal('fetch', fetch);
  const host = await mount(<ResourceSearch initialCode="ABC-123" choose={choose} />);
  await act(async () => field(host, '按番号查找资源')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  await settle();
  expect(fetch.mock.calls[0]?.[0]).toContain('/api/resources/search?code=ABC-123&goal=quality');
  expect(host.textContent).toContain('一处来源未响应');
  expect(host.textContent).toContain('测试源');
  await click(buttonNamed('选用此资源', host));
  expect(choose).toHaveBeenCalledWith('magnet:?xt=urn:btih:' + 'a'.repeat(40), 'ABC-123');
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('已填入磁力');
});

it('资料查询只接收相同番号，其他作品的字段不会进入资源弹层', async () => {
  const onWork = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.startsWith('/api/items')
    ? { items: [{ id: 2, code: 'ABC-1234', name: '另一部作品', duration: 600 }] }
    : { state: 'ready', warnings: [], error: '', items: [] } })));
  const host = await mount(<ResourceSearch initialCode="ABC-123" choose={vi.fn()} onWork={onWork} />);
  await act(async () => field(host, '按番号查找资源')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  await settle();
  expect(onWork).toHaveBeenCalledWith(null);
});

it('相同番号的资料保留真实出演者、发行日和时长', async () => {
  const onWork = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.startsWith('/api/items')
    ? { items: [{ id: 3, code: 'ABC-123', catalog_title: '作品标题', performers: ['甲'], release_date: '2026-09-01', duration: 600, has_cover: true }] }
    : { state: 'ready', warnings: [], error: '', items: [] } })));
  const host = await mount(<ResourceSearch initialCode="ABC-123" choose={vi.fn()} onWork={onWork} />);
  await act(async () => field(host, '按番号查找资源')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  await settle();
  expect(onWork).toHaveBeenCalledWith(expect.objectContaining({ code: 'ABC-123', title: '作品标题', performers: '甲', release_date: '2026-09-01', duration: 600, cover: '/cover?code=ABC-123&thumb=1' }));
});

it('查询等待期间保持输入焦点并拦截重复回车，完成后恢复编辑', async () => {
  let finish!: (value: unknown) => void;
  const fetch = vi.fn(() => new Promise((resolve) => { finish = resolve }));
  vi.stubGlobal('fetch', fetch);
  const host = await mount(<ResourceSearch initialCode="ABC-123" choose={vi.fn()} />);
  const input = field(host, '按番号查找资源')!;
  input.focus();
  const enter = () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await act(async () => { enter() });
  expect(input.disabled).toBe(false);
  expect(input.readOnly).toBe(true);
  expect(document.activeElement).toBe(input);
  expect(section(host, '查找资源')?.getAttribute('aria-busy')).toBe('true');
  expect(host.textContent).toContain('正在搜索资源');
  await act(async () => { enter() });
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => finish({ ok: true, json: async () => ({ state: 'ready', items: [], warnings: [], error: '' }) }));
  await settle();
  expect(input.readOnly).toBe(false);
  expect(section(host, '查找资源')?.getAttribute('aria-busy')).toBe('false');
  expect(host.textContent).not.toContain('正在搜索资源');
});

it.each([['中字', 'chinese'], ['无码', 'uncensored'], ['不需要无码，只找完整版', 'quality']])(
  '目标 %s 原文可见，使用 %s 排序且等待回车才查询', async (reason, goal) => {
    const fetch = vi.fn(async (_url: string) => ({ ok: true, json: async () => ({ state: 'ready',
      warnings: [], error: '', items: [] }) }));
    vi.stubGlobal('fetch', fetch);
    const host = await mount(<ResourceSearch initialCode="ABC-123" reason={reason} choose={vi.fn()} />);
    expect(host.textContent).toContain(`版本目标：${reason}`);
    expect(fetch).not.toHaveBeenCalled();
    await act(async () => field(host, '按番号查找资源')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    await settle();
    expect(fetch.mock.calls[0]?.[0]).toContain(`goal=${goal}`);
  });

it('设置保存后清空 API key，移除需再保存才生效', async () => {
  const initial = { max_indexers: 4, indexers: [{ key: 'one', name: '测试源', url: 'http://indexer.test/api',
    enabled: true, api_key_set: true }] };
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') posts.push(JSON.parse(String(init.body)));
    return { ok: true, json: async () => initial };
  }));
  const host = await mount(<QueryClientProvider client={queryClient}><IndexerSettings receipt={vi.fn()} /></QueryClientProvider>);
  await vi.waitFor(async () => { await settle(); expect(section(host, '资源搜索')).not.toBeNull() });
  expect(host.textContent).toContain('清除已保存的 API key');
  await type(field(host, '索引器 1 API key'), 'private-test-key');
  await submit(section(host, '资源搜索'));
  await settle();
  expect(posts).toHaveLength(1);
  expect(field(host, '索引器 1 API key')?.value).toBe('');
  await click(buttonNamed('移除索引器 1', host));
  expect(posts).toHaveLength(1);
  await submit(section(host, '资源搜索'));
  expect(posts[1]).toEqual({ indexers: [] });
  // 新添的一行默认启用：填完地址就能搜，不必再记得去勾一下。
  await click(buttonNamed('添加索引器', host));
  await submit(section(host, '资源搜索'));
  expect((posts[2] as { indexers: { enabled: boolean }[] }).indexers[0]?.enabled).toBe(true);
});
