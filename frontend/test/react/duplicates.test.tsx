/* 重复文件页：每组留谁、批量保留、回收与撤销。
 *
 * 首屏和真实路径一样先落进共用缓存（遗留层 `prefetch` 那一步），页面挂上去直接读。 */
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as legacyUi from '@peach/legacy/ui';

import { DuplicatesPage } from '../../src/react/duplicates/duplicates-page';
import {
  cloudPreferenceLocations, distinctName, DUPLICATES_KEY, duplicateVictims, mixedCloudPreferences, prefetchDuplicates,
  sharedNamePrefix,
  type DuplicateFile, type DuplicateGroup, type DuplicatesData,
} from '../../src/react/duplicates/duplicates';
import { cloudLocations, MEDIA_SOURCES_KEY, type MediaSourcesData } from '../../src/react/media-sources';
import { queryClient } from '../../src/react/query';
import { buttonNamed, click, fetchMock, mount, sentBody, settle } from './render';

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const GB = 1073741824;

const file = (id: number, patch: Partial<DuplicateFile> = {}): DuplicateFile => ({
  id, name: `ABC-${id}.mp4`, path: `R:\\media\\ABC-${id}.mp4`, location: 'local', drive: 'R:',
  size: GB, duration: 3600, is_largest: false, is_longest: false, ...patch,
});

const group = (code: string, files: DuplicateFile[], patch: Partial<DuplicateGroup> = {}): DuplicateGroup => ({
  code, files, count: files.length, identical: false, drives: ['R:'], cross_drive: false,
  reclaimable: GB, ...patch,
});

/** 一组全在本地，一组本地与 115 混着。 */
const groups = [
  group('ABC-001', [file(1, { size: 2 * GB, is_largest: true }), file(2, { is_longest: true })], { identical: true }),
  group('ABC-002', [file(3, { size: 3 * GB, is_largest: true, is_longest: true }), file(4, { location: '115', drive: 'B:' })],
    { cross_drive: true, drives: ['R:', 'B:'] }),
];
const data: DuplicatesData = { total: 2, files: 4, reclaimable: 2 * GB, groups };
const sources: MediaSourcesData = {
  sources: [{ location: 'local', online: true, roots: ['R:\\'] }, { location: '115', online: false, roots: ['B:\\'] },
    { location: 'pikpak', online: true, roots: [] }],
};

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

async function open(payload: DuplicatesData = data) {
  queryClient.setQueryData(DUPLICATES_KEY, payload);
  queryClient.setQueryData(MEDIA_SOURCES_KEY, sources);
  const props = { openItem: vi.fn(), toast: vi.fn(), failure: vi.fn() };
  const host = await mount(<QueryClientProvider client={queryClient}><DuplicatesPage {...props} /></QueryClientProvider>);
  return { host, ...props };
}

const card = (host: ParentNode, code: string) => host.querySelector(`section[aria-label="${code}"]`)!;

describe('网盘保留选项', () => {
  it('本地与在线来源不算网盘；已配置但离线的网盘仍然算，没配根目录的不算', () => {
    expect(cloudLocations({ sources: [{ location: 'local', online: true }, { location: 'online', online: true }] })).toEqual([]);
    expect(cloudLocations(sources)).toEqual(['115']);
  });

  it('只给组内真的出现过、且已配置的网盘', () => {
    expect(cloudPreferenceLocations([{ location: 'local' }], ['115', 'pikpak'])).toEqual([]);
    expect(cloudPreferenceLocations([{ location: 'local' }, { location: '115' }], ['115', 'pikpak'])).toEqual(['115']);
    expect(cloudPreferenceLocations([{ location: '115' }], [])).toEqual([]);
  });

  it('整组都在同一个网盘时不给「留 115」：留它和留最大是同一个结果', () => {
    expect(mixedCloudPreferences([{ location: '115' }, { location: '115' }], ['115'])).toEqual([]);
    expect(mixedCloudPreferences([{ location: '115' }, { location: 'local' }], ['115'])).toEqual(['115']);
  });
});

describe('每组留谁', () => {
  it('留最大、留最长按服务端标的那一个；指名网盘时那里没有就退回全组最大；整组回收一个不留', () => {
    expect(duplicateVictims(groups, 'largest')).toEqual([2, 4]);
    expect(duplicateVictims(groups, 'longest')).toEqual([1, 4]);
    expect(duplicateVictims(groups, '115')).toEqual([2, 3]);
    expect(duplicateVictims([groups[1]!], 'all')).toEqual([3, 4]);
  });
});

it('首屏一次取回分组和来源可达性，页面挂上去不再补取', async () => {
  const fetcher = vi.fn(async (path: string) => ({
    ok: true, status: 200, json: async () => (path === '/api/sources' ? sources : data),
  }));
  vi.stubGlobal('fetch', fetcher);
  await prefetchDuplicates(new AbortController().signal);
  const host = await mount(
    <QueryClientProvider client={queryClient}>
      <DuplicatesPage openItem={vi.fn()} toast={vi.fn()} failure={vi.fn()} />
    </QueryClientProvider>);
  expect(fetcher.mock.calls.map((call) => call[0]).sort()).toEqual(['/api/duplicates?limit=120', '/api/sources']);
  expect(host.textContent).toContain('2 组');
  expect(host.textContent).toContain('4 个文件 · 可回收 2.0 GB');
});

it('组头说清判据：sha1 齐全才写「一致」，否则只是时长推断；跨盘的组写出盘符', async () => {
  vi.stubGlobal('fetch', fetchMock(200, {}));
  const { host } = await open();
  expect(card(host, 'ABC-001').textContent).toContain('sha1 一致');
  expect(card(host, 'ABC-002').textContent).toContain('时长推断');
  expect(card(host, 'ABC-002').textContent).toContain('跨盘 R: B:');
  expect(card(host, 'ABC-001').textContent).not.toContain('跨盘');
});

it('「留 115」只出现在混着 115 与别处文件的那一组，批量条上的「全部优先 115」同理', async () => {
  vi.stubGlobal('fetch', fetchMock(200, {}));
  const { host } = await open();
  expect(buttonNamed('留 115', card(host, 'ABC-001'))).toBeNull();
  expect(buttonNamed('留 115', card(host, 'ABC-002'))).not.toBeNull();
  expect(buttonNamed('全部优先 115', host)).not.toBeNull();
  expect(buttonNamed('留 115', card(host, 'ABC-002'))?.querySelector('svg use')?.getAttribute('href'))
    .toBe(`#i-${legacyUi.MEDIA_SOURCE_ICONS['115']}`);
  expect(buttonNamed('全部优先 115', host)?.querySelector('svg use')?.getAttribute('href'))
    .toBe(`#i-${legacyUi.MEDIA_SOURCE_ICONS['115']}`);
  expect(buttonNamed('全部优先 PikPak', host)).toBeNull();
});

it('批量保留最大先确认，弹层点名条数与字节数；确认后进回收站，回执带撤销', async () => {
  const seen = confirmEverything();
  const fetcher = fetchMock(200, { ok: true });
  vi.stubGlobal('fetch', fetcher);
  const { host, toast } = await open();
  await click(buttonNamed('全部保留最大', host));
  await settle();
  expect(seen).toHaveLength(1);
  expect(seen[0]!.body).toContain('将把 2 个重复文件的馆藏记录移入回收站，每组保留最大的一个。');
  expect(seen[0]!.body).toContain('文件共 2.0 GB，记录可从回收站还原。');
  const batch = fetcher.mock.calls.findIndex((call) => call[0] === '/api/batch');
  expect(sentBody(fetcher, batch)).toEqual({ ids: [2, 4], operation: 'dispose', disposal_reason: 'duplicate' });
  expect(toast).toHaveBeenCalledWith('已把 2 项移入回收站', expect.objectContaining({ undo: expect.any(Function) }));
  const { undo } = toast.mock.calls[0]![1] as { undo(): Promise<void> };
  await undo();
  const batches = fetcher.mock.calls.flatMap((call, index) => (call[0] === '/api/batch' ? [index] : []));
  expect(sentBody(fetcher, batches.at(-1))).toEqual({ ids: [2, 4], operation: 'restore' });
});

it('一次最多 200 条，超过的分批发', async () => {
  confirmEverything();
  const fetcher = fetchMock(200, { ok: true });
  vi.stubGlobal('fetch', fetcher);
  const many = Array.from({ length: 251 }, (_, index) => file(index + 1, { is_largest: index === 0 }));
  const { host } = await open({ total: 1, files: 251, reclaimable: 250 * GB, groups: [group('BIG-001', many)] });
  await click(buttonNamed('留最大', card(host, 'BIG-001')));
  await settle();
  const batches = fetcher.mock.calls.flatMap((call, index) => (call[0] === '/api/batch' ? [sentBody(fetcher, index)] : []));
  expect(batches.map((body: { ids: number[] }) => body.ids.length)).toEqual([200, 50]);
});

it('取消确认就什么都不发', async () => {
  vi.spyOn(legacyUi, 'confirmModal').mockImplementation((async () => ({ confirmed: false })) as typeof legacyUi.confirmModal);
  const fetcher = fetchMock(200, { ok: true });
  vi.stubGlobal('fetch', fetcher);
  const { host } = await open();
  await click(buttonNamed('整组回收', card(host, 'ABC-001')));
  await settle();
  expect(fetcher.mock.calls.some((call) => call[0] === '/api/batch')).toBe(false);
});

it('点封面或文件名打开那一项', async () => {
  vi.stubGlobal('fetch', fetchMock(200, {}));
  const { host, openItem } = await open();
  await click(host.querySelector('button[aria-label="预览 ABC-3.mp4"]'));
  await click(host.querySelector('button[title="ABC-4.mp4"]'));
  expect(openItem.mock.calls).toEqual([[3], [4]]);
});

it('没有重复组时是一张空态，不画批量条', async () => {
  vi.stubGlobal('fetch', fetchMock(200, {}));
  const { host } = await open({ total: 0, files: 0, reclaimable: 0, groups: [] });
  expect(host.textContent).toContain('没有找到重复文件');
  expect(buttonNamed('全部保留最大', host)).toBeNull();
});

it('同组副本只在中段不同时，名字去掉共有开头、退到断词处，差别留在显示的那一段里', () => {
  const names = ['ABC-123 [1080p] 转载站甲.mp4', 'ABC-123 [720p] 转载站甲.mp4', 'ABC-123 [480p] 转载站甲.mp4'];
  const prefix = sharedNamePrefix(names);
  expect(prefix).toBe('ABC-123 [');
  expect(names.map((name) => distinctName(name, prefix)))
    .toEqual(['…1080p] 转载站甲.mp4', '…720p] 转载站甲.mp4', '…480p] 转载站甲.mp4']);
  // 共有段太短、只有一份或名字整个都是共有段时，原样显示。
  expect(sharedNamePrefix(['AB-1.mp4', 'AB-2.mp4'])).toBe('');
  expect(sharedNamePrefix(['ABC-123 一份.mp4'])).toBe('');
  expect(sharedNamePrefix(['ABC-123 part.mp4', 'ABC-123 part.mp4'])).toBe('ABC-123 part.');
  expect(distinctName('ABC-123 part.mp4', 'ABC-123 part.mp4')).toBe('ABC-123 part.mp4');
});
