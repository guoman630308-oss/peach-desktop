/* 作品详情岛：停在队列哪一条、身份与标签怎么分组、首屏要转去哪、交给壳的是什么、写完之后缓存与回执怎么变。
 *
 * 从目录进出不重取、舞台在媒体框里挂 Video.js、脱盘说明、四种队列、拖动排序、保存 Mix、接着看与手机
 * 布局要真浏览器，在 `frontend/e2e/item-detail.test.ts` 里量；尺寸与色板在 `e2e/design-*.test.ts`。 */
import { notifyManager, QueryClientProvider, type InfiniteData } from '@tanstack/react-query';
import { act } from 'react';
import { afterEach, describe, expect, it, vi, type Mock } from 'vitest';

import type { GridPage } from '../../src/react/catalog-grid/catalog-grid';
import type { MediaItem } from '../../src/react/catalog-grid/types';
import {
  chooseItem, detailTags, identityGroups, itemKey, mediaGate, movedOrder, nextRating, pickerSections, prefetchItemDetail,
  nextPart, queueCopy, queueKey, ratingText, withPartLabel,
  type DetailItem, type DetailQueue, type ItemDetailActions, type ItemDetailHelpers, type ItemDetailProps, type QueueItem,
} from '../../src/react/item-detail/item-detail';
import { ItemDetailPage } from '../../src/react/item-detail/item-detail-page';
import { tagLabel } from '../../src/core/tags';
import { queryClient } from '../../src/react/query';
import { click, mount, pending, settle, type } from './render';

afterEach(() => { queryClient.clear(); localStorage.removeItem('peach.recentTags') });
notifyManager.setScheduler((notify) => notify());

const item = (id: number, extra: Partial<DetailItem> = {}): DetailItem => ({
  id, name: `sample_${id}.mp4`, location: 'local', cost: 'free', size: 1024, duration: 3600, rating: 60,
  performers: ['七海ひな'], tags: [{ k: '剧情' }, { k: '高画质' }], ...extra,
} as DetailItem);
const row = (id: number, extra: Partial<QueueItem> = {}) => ({ id, name: `sample_${id}.mp4`, ...extra } as QueueItem);
const queue = (kind: DetailQueue['kind'], ids: number[], extra: Partial<DetailQueue> = {}): DetailQueue =>
  ({ kind, seedId: ids[0], title: '标题', items: ids.map((id) => row(id)), ...extra });

function helpers(patch: Partial<ItemDetailHelpers> = {}): ItemDetailHelpers {
  return {
    badgeHtml: () => '', titleHtml: (shown) => String(shown.name), displayName: (shown) => String(shown.name),
    javImage: () => 'cover',
    tagLabel: (tag) => tag, isDurationTag: (tag) => tag.startsWith('长片'), tagCandidates: () => [], sourceOffline: () => false,
    offlineReason: () => '盘没挂上', relatedSkeletonHtml: () => '', mixRelated: async () => [], wireDrag: vi.fn(),
    wireDragReorder: vi.fn(), ...patch,
  };
}

type ActionMocks = { [K in keyof ItemDetailActions]: Mock<ItemDetailActions[K]> };
function actions(): ActionMocks {
  return {
    close: vi.fn(), present: vi.fn(), redirect: vi.fn(), openQueueItem: vi.fn(), mountPlayer: vi.fn(() => vi.fn()), reopen: vi.fn(),
    checkSource: vi.fn(async () => false), openSavedFollow: vi.fn(), openEntity: vi.fn(), openUnowned: vi.fn(), openRegion: vi.fn(),
    openTag: vi.fn(), addToPlaylist: vi.fn(), saveMix: vi.fn(), editPlaylist: vi.fn(), openPlaylists: vi.fn(),
    reveal: vi.fn(async () => ''), sync: vi.fn(async () => ({ text: '', removed: [] })), trashChanged: vi.fn(async () => {}),
    toast: vi.fn(), failure: vi.fn(),
  };
}

function props(patch: Partial<ItemDetailProps> = {}): ItemDetailProps {
  return {
    id: 1, queue: null, relatedLimit: 0, helpers: helpers(), actions: actions(),
    grid: { helpers: {} as ItemDetailProps['grid']['helpers'], actions: {} as ItemDetailProps['grid']['actions'] },
    layout: {} as ItemDetailProps['layout'], selectMode: false, selected: new Set(), seekSeconds: 10, ...patch,
  };
}

/** 假服务端：写接口按 `answer` 回话，读接口回 `reads` 里给的那一份。 */
function serve(answer: (url: string, body: Record<string, unknown>) => unknown = () => ({ ok: true }),
  reads: Record<string, unknown> = {}) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body));
      return { ok: true, status: 200, json: async () => answer(url, body) };
    }
    const found = Object.entries(reads).find(([prefix]) => url.startsWith(prefix));
    return found ? { ok: true, status: 200, json: async () => structuredClone(found[1]) } : { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
const posts = (fetcher: ReturnType<typeof serve>) => fetcher.mock.calls
  .filter(([, init]) => init?.method === 'POST').map(([url, init]) => [url, JSON.parse(String(init!.body))]);

/** 条目已在缓存里（从卡片点进来、首屏已取过），直接画。 */
async function show(shown: DetailItem, patch: Partial<ItemDetailProps> = {}) {
  queryClient.setQueryData(itemKey(shown.id), shown);
  const given = props({ id: shown.id, ...patch });
  const host = await mount(<QueryClientProvider client={queryClient}><ItemDetailPage {...given} /></QueryClientProvider>);
  await settle();
  return { host, props: given, actions: given.actions as ActionMocks };
}

describe('标签身份', () => {
  it('详情显示完整制服名与角色名', async () => {
    serve();
    const { host } = await show(item(1, { tags: [{ k: 'OL制服' }, { k: '秘书OL' }] }),
      { helpers: helpers({ tagLabel }) });
    expect([...host.querySelectorAll('[data-detail-tag] [data-tag]')].map((node) => node.textContent))
      .toEqual(['OL制服', '秘书OL']);
  });

  it('已有规范标签时添加同义词保持一枚标签且不撤销原标签', async () => {
    const fetcher = serve((_url, body) => ({ ok: true, tag: body.operation === 'add' ? '秘书OL' : body.tag }));
    const { host, actions: given } = await show(item(1, { tags: [{ k: '秘书OL' }] }));
    await click(host.querySelector('#tagPlus'));
    await type(host.querySelector('#tagPickSearch') as HTMLInputElement, 'OL');
    await click(host.querySelector('[data-pick="OL"]'));
    await settle();
    expect([...host.querySelectorAll('[data-detail-tag] [data-tag]')].map((node) => node.textContent))
      .toEqual(['秘书OL']);
    expect(given.toast.mock.calls.at(-1)?.[1]?.undo).toBeUndefined();
    expect(posts(fetcher).map(([, body]) => [body.operation, body.tag])).toEqual([['add', 'OL']]);
  });

  it('添加同义标签使用服务端规范名并以相同身份撤销', async () => {
    const fetcher = serve((_url, body) => ({ ok: true, tag: body.operation === 'add' ? '秘书OL' : body.tag }));
    const { host, actions: given } = await show(item(1, { tags: [] }));
    await click(host.querySelector('#tagPlus'));
    await type(host.querySelector('#tagPickSearch') as HTMLInputElement, 'OL');
    await click(host.querySelector('[data-pick="OL"]'));
    await settle();
    expect([...host.querySelectorAll('[data-detail-tag] [data-tag]')].map((node) => node.textContent))
      .toEqual(['秘书OL']);
    const undo = given.toast.mock.calls.at(-1)?.[1]?.undo;
    expect(undo).toBeTypeOf('function');
    await act(async () => { await undo?.() });
    expect(posts(fetcher).map(([, body]) => [body.operation, body.tag]))
      .toEqual([['add', 'OL'], ['remove', '秘书OL']]);
  });
});

describe('停在队列哪一条', () => {
  it('分卷接同一版本的下一卷，版本边界保持停止', async () => {
    const versions = queue('editions', [1, 2, 3], {
      versionCount: 2,
      items: [row(1, { version_id: 1 }), row(2, { version_id: 2, part_label: '1' }),
        row(3, { version_id: 2, part_label: '2' })],
    });
    expect(nextPart(versions, 1)).toBeNull();
    expect(nextPart(versions, 2)?.id).toBe(3);
    expect(nextPart(versions, 3)).toBeNull();
    queryClient.setQueryData(queueKey({ kind: 'editions', seedId: 1 }), versions);
    const { host, actions: acts } = await show(item(2), { queue: { kind: 'editions', seedId: 1 } });
    const media = host.querySelector('[data-item-media]')!;
    media.dispatchEvent(new Event('ended', { bubbles: false }));
    expect(acts.openQueueItem).toHaveBeenCalledWith({ kind: 'editions', seedId: 1, autoplay: true }, 3);
  });
  it('Mix 就是点的那一条；分卷与版本点的不在组里退到第一条；播放列表退到续播位置', () => {
    expect(chooseItem(queue('mix', [1, 2]), 9)).toBe(9);
    expect(chooseItem(queue('parts', [1, 2]), 2)).toBe(2);
    expect(chooseItem(queue('editions', [1, 2]), 9)).toBe(1);
    expect(chooseItem(queue('playlist', [1, 2], { currentAssetId: 2 }), null)).toBe(2);
    expect(chooseItem(queue('playlist', [1, 2]), null)).toBe(1);
    expect(chooseItem(queue('playlist', []), null)).toBeNull();
  });

  it('播放列表跳过已消失的条目：续播位置或点的是它，退到第一条能播的', () => {
    const items = [row(1, { disposal: 'vanished' }), row(2), row(3)];
    expect(chooseItem(queue('playlist', [], { items, currentAssetId: 1 }), null)).toBe(2);
    expect(chooseItem(queue('playlist', [], { items }), 1)).toBe(2);
    expect(chooseItem(queue('playlist', [], { items, currentAssetId: 3 }), null)).toBe(3);
    expect(chooseItem(queue('playlist', [], { items: [row(1, { disposal: 'vanished' })] }), null)).toBeNull();
  });

  it('卷标只从分卷队列补进标题，别的队列不动这一条', () => {
    const parts = queue('parts', [1, 2], { items: [row(1, { part_label: '1' }), row(2, { part_label: '2' })] });
    expect(withPartLabel(item(2), parts).part_label).toBe('2');
    const plain = item(2);
    expect(withPartLabel(plain, queue('mix', [2]))).toBe(plain);
  });

  it('本篇与特典在标题和队列中使用完整标签，数字卷保留序号', async () => {
    serve();
    const ref = { kind: 'parts', seedId: 1 } as const;
    queryClient.setQueryData(queueKey(ref), queue('parts', [1, 2, 3], {
      items: [row(1, { part_label: '本篇' }), row(2, { part_label: '特典 1' }), row(3, { part_label: '0' })],
    }));
    const { host } = await show(item(2), { queue: ref });
    expect(host.querySelector('.partlabel')?.textContent).toBe('特典 1');
    expect(host.querySelector('[data-queue-item="1"]')?.textContent).toContain('本篇');
    expect(host.querySelector('[data-queue-item="2"]')?.textContent).toContain('特典 1');
    expect(host.querySelector('[data-queue-item="3"]')?.textContent).toContain('第 0 卷');
    expect(host.textContent).not.toContain('第 特典');
  });

  it('字母分卷照番号后缀大写成「A 卷」', async () => {
    serve();
    const ref = { kind: 'parts', seedId: 1 } as const;
    queryClient.setQueryData(queueKey(ref), queue('parts', [1, 2], {
      items: [row(1, { part_label: 'a' }), row(2, { part_label: 'B' })],
    }));
    const { host } = await show(item(1), { queue: ref });
    expect(host.querySelector('[data-queue-item="1"]')?.textContent).toContain('A 卷');
    expect(host.querySelector('[data-queue-item="2"]')?.textContent).toContain('B 卷');
    expect(host.textContent).not.toContain('第 a 卷');
  });

  it('队列头：版次队列只写数量，其余带上标题', () => {
    expect(queueCopy(queue('editions', [1, 2]))).toEqual({ title: '版本', summary: '2 个版本' });
    const versions = queue('editions', [1, 2, 3], {
      versionCount: 2,
      items: [row(1, { version_id: 1 }), row(2, { version_id: 2, part_label: '1' }),
        row(3, { version_id: 2, part_label: '2' })],
    });
    expect(queueCopy(versions).summary).toBe('2 个版本');
    expect(withPartLabel(item(3), versions).part_label).toBe('2');
    expect(queueCopy(queue('parts', [1, 2, 3], { title: '分卷 · PCH-021' }))).toEqual({ title: '分卷', summary: '分卷 · PCH-021 · 3 卷' });
    expect(queueCopy(queue('playlist', [1], { title: '周末片单' }))).toEqual({ title: '播放列表', summary: '周末片单 · 1 个视频' });
    const many = Array.from({ length: 1284 }, (_, i) => i + 1);
    expect(queueCopy(queue('parts', many, { title: '分卷' })).summary).toBe('分卷 · 1,284 卷');
  });

  it('拖动之后的新顺序：挪到目标前面或后面', () => {
    expect(movedOrder([1, 2, 3, 4], 4, 2, false)).toEqual([1, 4, 2, 3]);
    expect(movedOrder([1, 2, 3, 4], 1, 3, true)).toEqual([2, 3, 1, 4]);
  });
});

describe('身份与标签', () => {
  it('同名只出一次；只有扁平厂牌时带上它的标识可用性；厂牌与系列不算归属', () => {
    const groups = identityGroups(item(1, {
      performers: ['A'], studio: 'S', has_studio_logo: true, studio_logo_version: '9a',
      entity_refs: { performer: [{ id: 1, name: 'A' }, { id: 2, name: 'a' }], series: [{ id: 3, name: 'A' }] },
    }));
    expect(groups.cast.map((ref) => ref.name)).toEqual(['A']);
    expect(groups.studios).toEqual([{ id: null, name: 'S', has_logo: true, logo_version: '9a' }]);
    expect(groups.series).toEqual([]);
    expect(identityGroups(item(2, { performers: [], studio: 'S', creator: '' })).unowned).toBe(true);
    expect(identityGroups(item(3, { performers: [], creator: '某人' })).unowned).toBe(false);
  });

  it('标签去掉时长分档，按显示名去重时留本身就是规范名的那条', () => {
    const tags = detailTags(item(1, { tags: [{ k: '长片-30分上' }, { k: '旧名' }, { k: '规范名' }, { k: '剧情' }] }),
      { tagLabel: (tag) => (tag === '旧名' ? '规范名' : tag), isDurationTag: (tag) => tag.startsWith('长片') });
    expect(tags.map((tag) => tag.k)).toEqual(['规范名', '剧情']);
  });

  it('标签选择器：没输入列最近使用加全部；输入的词没有完全命中才给「新建」', () => {
    const candidates = [{ k: '剧情', n: 3 }, { k: '剧情向', n: 1 }];
    expect(pickerSections('', candidates, ['剧情', '旧标签'])).toEqual({
      recent: [{ k: '剧情', n: 3 }, { k: '旧标签', n: 0 }], results: candidates, create: '',
    });
    expect(pickerSections('剧情', candidates, []).create).toBe('');
    expect(pickerSections('剧', candidates, []).create).toBe('剧');
  });

  it('评分：第 n 颗送 n×20，再点当前那一颗送 0', () => {
    expect([nextRating(60, 3), nextRating(60, 4), nextRating(null, 1)]).toEqual([0, 80, 20]);
    expect([ratingText(60), ratingText(0), ratingText(null)]).toEqual(['3 星', '未评分', '未评分']);
  });

  it('播放区挡在前面的说明：没挂载、在线作品反查不到、计费来源', () => {
    expect(mediaGate(item(1), true)).toBe('offline');
    expect(mediaGate(item(1, { location: 'online' }), false)).toBe('online');
    expect(mediaGate(item(1, { location: 'online', follow_item_id: 5 } as Partial<DetailItem>), false)).toBe('');
    expect(mediaGate(item(1, { cost: 'metered' }), false)).toBe('metered');
    expect(mediaGate(item(1), false)).toBe('');
    expect(mediaGate(item(1, { name: 'ABC-123.ISO' }), false)).toBe('disc');
  });
});

describe('首屏取数', () => {
  const signal = new AbortController().signal;

  it('保存过的在线作品转关注详情；条目已不在收起舞台', async () => {
    serve(undefined, { '/api/item?id=1': item(1, { location: 'online', follow_item_id: 9 } as Partial<DetailItem>),
      '/api/item?id=2': { error: 'gone' } });
    const first = props({ id: 1 });
    await prefetchItemDetail(first, signal);
    expect(first.actions.redirect).toHaveBeenCalledWith({ kind: 'follow', id: 9 });
    const second = props({ id: 2 });
    await prefetchItemDetail(second, signal);
    expect(second.actions.redirect).toHaveBeenCalledWith({ kind: 'gone' });
  });

  it('队列取不到退回普通详情；播放列表空了回列表页', async () => {
    serve(undefined, { '/api/parts?id=1': { error: '这一组已不存在' },
      '/api/playlist?id=7': { id: 7, name: '空', items: [], current_asset_id: null } });
    const parts = props({ id: 1, queue: { kind: 'parts', seedId: 1, fresh: true } });
    await prefetchItemDetail(parts, signal);
    expect(parts.actions.redirect).toHaveBeenCalledWith({ kind: 'item', id: 1 });
    const playlist = props({ id: null, queue: { kind: 'playlist', playlistId: 7, fresh: true } });
    await prefetchItemDetail(playlist, signal);
    expect(playlist.actions.redirect).toHaveBeenCalledWith({ kind: 'playlists' });
  });

  it('同一个队列里换一条读缓存，不重取；从外面进来才重取', async () => {
    const fetcher = serve(undefined, { '/api/parts?id=1': { title: 'PCH', items: [row(1), row(2)] }, '/api/item?id=2': item(2) });
    queryClient.setQueryData(queueKey({ kind: 'parts', seedId: 1 }), queue('parts', [1, 2]));
    await prefetchItemDetail(props({ id: 2, queue: { kind: 'parts', seedId: 1, fresh: false } }), signal);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/item?id=2']);
    await prefetchItemDetail(props({ id: 2, queue: { kind: 'parts', seedId: 1, fresh: true } }), signal);
    expect(fetcher.mock.calls.map(([url]) => url)).toContain('/api/parts?id=1');
  });

  it('重开同一条也重取：缓存里那份是上次打开时的；Mix 的种子刚随队列取过，不取第二遍', async () => {
    const fetcher = serve(undefined, { '/api/item?id=1': item(1, { rating: 100 }) });
    queryClient.setQueryData(itemKey(1), item(1, { rating: 20 }));
    await prefetchItemDetail(props({ id: 1 }), signal);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/item?id=1']);
    expect(queryClient.getQueryData<DetailItem>(itemKey(1))?.rating).toBe(100);
    fetcher.mockClear();
    await prefetchItemDetail(props({ id: 1, queue: { kind: 'mix', seedId: 1, fresh: true } }), signal);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/item?id=1']);
  });
});

describe('交给壳的', () => {
  it('画出来报一次 present；播放区的媒体框交给 mountPlayer，不自动播就不带 autoplay', async () => {
    serve();
    const { host, actions: done } = await show(item(1));
    expect(done.present).toHaveBeenCalledTimes(1);
    const frame = host.querySelector('[data-item-media="video"]');
    expect(done.mountPlayer).toHaveBeenCalledWith(frame, expect.objectContaining({ id: 1 }), null, undefined);
    expect(host.querySelector('video')).toBeNull();
  });

  it('没挂载与在线拦截不挂播放器；计费来源点了说明才挂，带 autoplay', async () => {
    serve();
    const offline = await show(item(1), { helpers: helpers({ sourceOffline: () => true }) });
    expect(offline.host.querySelector('[data-item-gate="offline"]')?.textContent).toContain('盘没挂上');
    expect(offline.actions.mountPlayer).not.toHaveBeenCalled();
    queryClient.clear();
    const metered = await show(item(2, { cost: 'metered' }));
    expect(metered.actions.mountPlayer).not.toHaveBeenCalled();
    await click(metered.host.querySelector('[data-item-gate="metered"]'));
    expect(metered.actions.mountPlayer).toHaveBeenCalledWith(metered.host.querySelector('[data-item-media="metered"]'), expect.objectContaining({ id: 2 }), null,
      { autoplay: true });
  });

  it('队列里点另一条交给壳换，带着队列引用', async () => {
    serve();
    queryClient.setQueryData(queueKey({ kind: 'mix', seedId: 1 }), queue('mix', [1, 2, 3]));
    const { host, actions: done } = await show(item(1), { queue: { kind: 'mix', seedId: 1 } });
    expect(host.querySelector('[data-queue-item="1"]')?.getAttribute('aria-current')).toBe('true');
    await click(host.querySelector('[data-queue-item="3"]'));
    expect(done.openQueueItem).toHaveBeenCalledWith({ kind: 'mix', seedId: 1, playlistId: undefined }, 3);
  });

  it('播放列表里已消失的那一行标出来、点不开', async () => {
    serve();
    queryClient.setQueryData(queueKey({ kind: 'playlist', playlistId: 7 }), queue('playlist', [], {
      playlistId: 7, items: [row(2), row(3, { disposal: 'vanished' })],
    }));
    const { host, actions: done } = await show(item(2), { queue: { kind: 'playlist', playlistId: 7 } });
    const gone = host.querySelector<HTMLButtonElement>('[data-queue-item="3"]');
    expect(gone?.disabled).toBe(true);
    expect(gone?.hasAttribute('data-queue-vanished')).toBe(true);
    expect(gone?.textContent).toContain('已消失 · 文件已不在盘上');
    expect(host.querySelector<HTMLButtonElement>('[data-queue-item="2"]')?.disabled).toBe(false);
    await click(gone);
    expect(done.openQueueItem).not.toHaveBeenCalled();
  });
});

describe('写完之后', () => {
  it('评分写进详情缓存与目录里同一张卡；回执的撤销送回原分', async () => {
    const fetcher = serve((_url, body) => ({ rating: body.value || null }));
    const page: InfiniteData<GridPage> = { pages: [{ items: [{ id: 1, rating: 60 } as MediaItem], offset: 0 } as GridPage], pageParams: [0] };
    queryClient.setQueryData(['catalog-grid', 'catalog', '', 0], page);
    const { host, actions: done } = await show(item(1));
    await click(host.querySelector('[data-rate="3"]'));
    await settle();
    expect(host.querySelector('[data-rating-value]')?.textContent).toBe('未评分');
    expect(queryClient.getQueryData<DetailItem>(itemKey(1))?.rating).toBeNull();
    expect(queryClient.getQueryData<InfiniteData<GridPage>>(['catalog-grid', 'catalog', '', 0])?.pages[0]?.items[0]?.rating).toBeNull();
    const [message, options] = done.toast.mock.calls[0]!;
    expect(message).toBe('已取消评分');
    await options!.undo!();
    await settle();
    expect(host.querySelector('[data-rating-value]')?.textContent).toBe('3 星');
    expect(posts(fetcher).map(([, body]) => body.value)).toEqual([0, 60]);
  });

  it('反馈的撤销再切一次回去，之后交给壳判垃圾文件那一档要不要重读', async () => {
    const fetcher = serve((_url, body) => ({ feedback: body.kind === 'dislike' ? 'dislike' : null, disposal: null }));
    const { host, actions: done } = await show(item(1));
    await click(host.querySelector('[data-kind="dislike"]'));
    await settle();
    expect(host.querySelector('[data-kind="dislike"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(done.toast.mock.calls[0]![0]).toBe('已标记不合口味');
    await done.toast.mock.calls[0]![1]!.undo!();
    expect(posts(fetcher).map(([, body]) => body.kind)).toEqual(['dislike', 'dislike']);
    expect(done.trashChanged).toHaveBeenCalledWith(null, true);
  });

  it('移入回收站之后交给壳：这一档可能要把它从列表里拿掉', async () => {
    serve(() => ({ feedback: null, disposal: 'trash' }));
    const { host, actions: done } = await show(item(1));
    await click(host.querySelector('[data-kind="dispose"]'));
    await settle();
    expect(done.trashChanged).toHaveBeenCalledWith('trash', false);
    expect(done.toast.mock.calls[0]![0]).toBe('已移入回收站');
  });

  it('删标签写进缓存；撤销加回来', async () => {
    const fetcher = serve(() => ({ ok: true }));
    const { host, actions: done } = await show(item(1));
    await click(host.querySelector('[data-remove-tag="剧情"]'));
    await settle();
    expect(host.querySelector('[data-tag="剧情"]')).toBeNull();
    expect(done.toast.mock.calls[0]![0]).toBe('已删除标签「剧情」');
    await done.toast.mock.calls[0]![1]!.undo!();
    await settle();
    expect(host.querySelector('[data-tag="剧情"]')).not.toBeNull();
    expect(posts(fetcher).map(([, body]) => [body.operation, body.tag])).toEqual([['remove', '剧情'], ['add', '剧情']]);
  });

  it('写失败交给壳报出来，键态不变', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ detail: '写入失败' }) })));
    const { host, actions: done } = await show(item(1));
    await click(host.querySelector('#stageLater'));
    await settle();
    expect(done.failure).toHaveBeenCalledWith('更新稍后看', expect.anything());
    expect(host.querySelector('#stageLater')?.getAttribute('aria-pressed')).toBe('false');
  });

  it('寻找更好版本与记一次高潮：回执的撤销各自写回', async () => {
    const fetcher = serve((url, body) => (url === '/api/quality-goal'
      ? { better_version: !!body.wanted, better_version_reason: '' }
      : { feedback: null, disposal: null, o_count: body.kind === 'o' ? 2 : 1 }));
    const { host, actions: done } = await show(item(1, { o_count: 1 }));
    await click(host.querySelector('#betterVersion'));
    await settle();
    expect(host.querySelector('#betterVersion')?.getAttribute('aria-pressed')).toBe('true');
    expect(done.toast.mock.calls[0]![0]).toBe('已标记寻找更好版本');
    await done.toast.mock.calls[0]![1]!.undo!();
    await settle();
    expect(host.querySelector('#betterVersion')?.getAttribute('aria-pressed')).toBe('false');
    await click(host.querySelector('[data-kind="o"]'));
    await settle();
    expect(host.querySelector('#oCount')?.textContent).toBe('2');
    await done.toast.mock.calls[1]![1]!.undo!();
    await settle();
    expect(host.querySelector('#oCount')?.textContent).toBe('1');
    expect(posts(fetcher).map(([url, body]) => [url, body.wanted ?? body.kind])).toEqual([
      ['/api/quality-goal', true], ['/api/quality-goal', false], ['/api/feedback', 'o'], ['/api/feedback', 'o-undo'],
    ]);
  });

  it('喜爱理由：图标键开合理由框；提交时键上转圈，写完回执', async () => {
    const reply = pending<unknown>();
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, status: 200, json: async () => reply.answer }));
    vi.stubGlobal('fetch', fetcher);
    const { host, actions: done } = await show(item(1));
    const panel = host.querySelector<HTMLElement>('#preferencePanel')!;
    const toggle = host.querySelector('#preferenceToggle')!;
    expect(panel.hidden).toBe(true);
    await click(toggle);
    expect(panel.hidden).toBe(false);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    await typeText(host.querySelector('#likeReason'), '镜头好');
    await click(host.querySelector('#savePreference'));
    const save = host.querySelector('#savePreference')!;
    expect(save.getAttribute('aria-busy')).toBe('true');
    expect(save.querySelector('[role="status"]')?.getAttribute('aria-label')).toBe('正在提交喜爱理由');
    await reply.release({ liked: true, like_reason: '镜头好' });
    await settle();
    expect(save.getAttribute('aria-busy')).toBeNull();
    expect(save.textContent).toBe('提交');
    expect(done.toast.mock.calls[0]![0]).toBe('已保存喜欢偏好');
    expect(posts(fetcher)).toEqual([['/api/preference', { id: 1, liked: true, reason: '镜头好' }]]);
    await click(toggle);
    expect(panel.hidden).toBe(true);
  });
});

/** 两个节点在文档里的先后：`a` 排在 `b` 前面。 */
const before = (a: Element | null, b: Element | null) =>
  !!a && !!b && !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
const glyph = (el: Element | null | undefined) => el?.querySelector('use')?.getAttribute('href');

async function typeText(field: HTMLTextAreaElement | null, value: string) {
  if (!field) throw new Error('输入框没有画出来');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, value);
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('侧栏怎么读', () => {
  it('评分夹在标题与规格行之间，当前那一颗的读屏名说的是撤销；规格行每项前面是图标', async () => {
    serve();
    const { host } = await show(item(1, { width: 1920, height: 1080, release_date: '2026-08-01' }));
    const rating = host.querySelector('#detailRating');
    expect(before(host.querySelector('[data-detail-title]'), rating)).toBe(true);
    expect(before(rating, host.querySelector('[data-stage-meta]'))).toBe(true);
    expect(host.querySelector('[data-rate="3"]')?.getAttribute('aria-label')).toBe('取消评分（当前 3 星）');
    expect(host.querySelector('[data-rate="4"]')?.getAttribute('aria-label')).toBe('评为 4 星');
    expect([...host.querySelectorAll('[data-stage-meta] [data-spec-item]')].map(glyph)).toEqual(['#i-monitor', '#i-hard-drive', '#i-calendar']);
    expect(host.querySelector('[data-stage-meta]')?.textContent).not.toContain('发行');
    expect(host.querySelector('#ratioTxt')?.textContent).toBe('0%');
  });

  it('没有宽高时规格行不写分辨率，大小没记下写「大小未知」', async () => {
    serve();
    const { host } = await show(item(1, { width: null, height: null, size: 0 } as Partial<DetailItem>));
    expect([...host.querySelectorAll('[data-stage-meta] [data-spec-item]')].map(glyph)).toEqual(['#i-hard-drive']);
    expect(host.querySelector('[data-stage-meta]')?.textContent).toBe('大小未知');
  });

  it('身份按类分组、组标题在上；没有实体 id 的只写名字；厂牌装了标识才要 icon 变体；系列是带图标的链接', async () => {
    serve();
    const { host, actions: done } = await show(item(1, {
      is_jav: true, performers: ['七海ひな', '桜井まい'],
      entity_refs: {
        performer: [{ id: 1, name: '七海ひな' }, { id: null, name: '桜井まい' }],
        studio: [{ id: 5, name: 'Peach Studio', has_logo: true }],
        series: [{ id: 7, name: '夏日系列' }],
      },
    } as Partial<DetailItem>));
    const groups = [...host.querySelectorAll('[data-item-identity] [data-id-group]')];
    expect(groups.map((group) => group.querySelector('[data-id-label]')?.textContent)).toEqual(['女优', '厂牌', '系列']);
    expect(host.querySelector('[data-entity-name="七海ひな"]')?.tagName).toBe('BUTTON');
    const bare = [...host.querySelectorAll('[data-id-cell]')].find((cell) => cell.textContent?.includes('桜井まい'));
    expect(bare?.tagName).toBe('SPAN');
    expect(bare?.hasAttribute('data-entity-kind')).toBe(false);
    expect(host.querySelector('[data-id-cell="studio"] img')?.getAttribute('src')).toBe('/logo?studio=Peach%20Studio&variant=icon');
    const series = host.querySelector('[data-series-link]');
    expect(glyph(series)).toBe('#i-tags');
    await click(series);
    expect(done.openEntity).toHaveBeenCalledWith('series', '夏日系列');
  });

  it('出镜者那一组番号作品写女优、其余写艺人；装了实体图的格按 ref 上的取景与版本出图，没装的只有首字母', async () => {
    serve();
    const { host } = await show(item(1, {
      performers: ['甲', '乙'],
      entity_refs: { performer: [
        { id: 3, name: '甲', has_image: true, image_version: '9', avatar_focus: { axis: 'x', pct: 30 } }, { id: 4, name: '乙' },
      ] },
    } as Partial<DetailItem>));
    expect(host.querySelector('[data-id-group="performer"] [data-id-label]')?.textContent).toBe('艺人');
    const framed = host.querySelector<HTMLImageElement>('[data-entity-name="甲"] img');
    expect([framed?.getAttribute('src'), framed?.style.objectPosition]).toEqual(['/entity-image?kind=performer&id=3&v=9', '30% 50%']);
    expect(framed?.parentElement).toBe(host.querySelector('[data-entity-name="甲"] [data-id-face]'));
    expect(host.querySelector('[data-entity-name="乙"] img')).toBeNull();
    expect(host.querySelector('[data-entity-name="乙"]')?.textContent).toContain('乙');
  });

  it('没有署名人时，归属那一组就是「未归属」入口', async () => {
    serve();
    const { host, actions: done } = await show(item(1, { performers: [], creator: '', entity_refs: {} } as Partial<DetailItem>));
    const group = host.querySelector('[data-id-group="unowned"]');
    expect(group?.querySelector('[data-id-label]')?.textContent).toBe('归属');
    await click(group?.querySelector('[data-open-unowned]'));
    expect(done.openUnowned).toHaveBeenCalled();
  });

  it('作品详情的动作属于当前作品', async () => {
    serve();
    const shown = item(1, { code: 'ABC-123' } as Partial<DetailItem>);
    const { host } = await show(shown);
    expect(host.querySelector('[data-fb="cloud-download"]')).toBeNull();
    expect(host.querySelector('#addPlaylist')?.getAttribute('aria-label')).toBe('加入播放列表');
  });

  it('每一枚键的字形只说它旁边那件事', async () => {
    serve();
    queryClient.setQueryData(queueKey({ kind: 'mix', seedId: 1 }), queue('mix', [1, 2]));
    const mix = await show(item(1, { liked: true }), { queue: { kind: 'mix', seedId: 1 } });
    expect(glyph(mix.host.querySelector('#preferenceToggle'))).toBe('#i-notebook-pen');
    expect(mix.host.querySelector('#likeBtn')?.getAttribute('aria-label')).toBe('取消喜欢');
    expect(glyph(mix.host.querySelector('#addPlaylist'))).toBe('#i-playlist');
    expect(glyph(mix.host.querySelector('[data-save-mix]'))).toBe('#i-playlist');
    expect(glyph(mix.host.querySelector('[data-kind="o"]'))).toBe('#i-sperm');
    expect(glyph(mix.host.querySelector('[data-rate="1"]'))).toBe('#i-star');
    queryClient.clear();
    queryClient.setQueryData(queueKey({ kind: 'playlist', playlistId: 7 }), queue('playlist', [2, 3], { playlistId: 7 }));
    const playlist = await show(item(2), { queue: { kind: 'playlist', playlistId: 7 } });
    expect(glyph(playlist.host.querySelector('[data-edit-playlist]'))).toBe('#i-playlist');
  });

  it('版次队列每一行带版次徽章，按版次上色', async () => {
    serve();
    queryClient.setQueryData(queueKey({ kind: 'editions', seedId: 1 }), queue('editions', [1, 2], {
      items: [row(1, { edition_label: '中字' }), row(2, { edition_label: '无码' })],
    }));
    const { host } = await show(item(1), { queue: { kind: 'editions', seedId: 1 } });
    expect([...host.querySelectorAll('[data-queue-edition]')].map((badge) => [badge.textContent, badge.className])).toEqual([
      ['中字', 'javedition subtitle'], ['无码', 'javedition uncensored'],
    ]);
  });

  it('接着看空结果显示说明，关闭推荐与 Mix 队列不显示推荐区', async () => {
    serve(undefined, { '/api/related': { items: [] } });
    const off = await show(item(1), { relatedLimit: 0 });
    expect(off.host.querySelector('[data-item-related]')).toBeNull();
    queryClient.setQueryData(queueKey({ kind: 'mix', seedId: 2 }), queue('mix', [2, 3]));
    const queued = await show(item(2), { relatedLimit: 12, queue: { kind: 'mix', seedId: 2 } });
    expect(queued.host.querySelector('[data-item-related]')).toBeNull();
    const empty = await show(item(3), { relatedLimit: 12 });
    await settle();
    expect(empty.host.querySelector('[data-item-related] [data-empty-state] h3')?.textContent).toBe('暂无接着看的内容');
    expect(empty.host.querySelector('[data-item-related] [data-empty-state] p')?.textContent).toBe('可以回到列表挑选其他作品。');
  });

  it('标签选择器：组字过程中不筛；上下键挑、回车加；Escape 收起；点外面也收起', async () => {
    Element.prototype.scrollIntoView ??= () => {};
    const fetcher = serve(() => ({ ok: true }));
    const candidates = [{ k: '巨乳', n: 3 }, { k: '中出', n: 2 }];
    const { host } = await show(item(1), { helpers: helpers({ tagCandidates: () => candidates }) });
    const picker = host.querySelector<HTMLElement>('#tagPicker')!;
    const input = host.querySelector<HTMLInputElement>('#tagPickSearch')!;
    const names = () => [...picker.querySelectorAll('[data-pick]')].map((pick) => pick.getAttribute('data-pick'));
    const key = (name: string) => act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true })) });
    await click(host.querySelector('#tagPlus'));
    expect(picker.hidden).toBe(false);
    await act(async () => { input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })) });
    await type(input, 'zhon');
    expect(names()).toEqual(['巨乳', '中出']);
    await type(input, '中');
    await act(async () => { input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })) });
    expect(names()).toEqual(['中出', '中']);
    await key('ArrowDown');
    await key('Enter');
    await settle();
    expect(picker.hidden).toBe(true);
    expect(posts(fetcher).map(([, body]) => [body.operation, body.tag])).toEqual([['add', '中出']]);
    await click(host.querySelector('#tagPlus'));
    await key('Escape');
    expect(picker.hidden).toBe(true);
    expect(document.activeElement).toBe(host.querySelector('#tagPlus'));
    await click(host.querySelector('#tagPlus'));
    await act(async () => { await new Promise((done) => setTimeout(done, 0)) });
    await act(async () => { document.body.dispatchEvent(new Event('pointerdown', { bubbles: true })) });
    expect(picker.hidden).toBe(true);
  });
});
