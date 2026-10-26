/* 播放列表页：首屏重取、三个写操作与撤销、壳要求的重读、卡片去处与悬停翻页。
 *
 * 写操作走假 fetch，按地址与方法记下每一问；删除的确认弹层归遗留层，这里替人点确认。
 * 去处（队列、资料页）与回执都经 props 交还给壳，这里只看页面交出去了什么。 */
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { act, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as legacyUi from '@peach/legacy/ui';

import type { PlaylistsProps } from '../../src/react/bundle';
import {
  PLAYLISTS_KEY, prefetchPlaylists, type PlaylistDetail, type PlaylistRow,
} from '../../src/react/playlists/playlists';
import { PlaylistsPage } from '../../src/react/playlists/playlists-page';
import { queryClient } from '../../src/react/query';
import { buttonNamed, click, mount, settle, submit, type } from './render';

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const row = (id: number, patch: Partial<PlaylistRow> = {}): PlaylistRow => ({
  id, name: `列表${id}`, source_kind: 'manual', source_seed_asset_id: null, current_asset_id: null,
  created_at: '2026-09-01 10:00:00', updated_at: '2026-09-01 10:00:00', item_count: 2,
  preview_asset_id: 11, preview_ids: [11, 12], faces: [], ...patch,
});

type Call = { url: string; method: string; body: unknown };
type Answer = { status?: number; body: unknown };

/** 按「方法 地址」回话的假 fetch；没登记的地址回空列表。 */
function serve(routes: Record<string, Answer | ((body: unknown) => Answer)> = {}) {
  const calls: Call[] = [];
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method, body });
    const route = routes[`${method} ${url}`];
    const answer = typeof route === 'function' ? route(body) : route ?? { body: { items: [] } };
    const status = answer.status ?? 200;
    return { ok: status < 400, status, json: async () => answer.body };
  });
  vi.stubGlobal('fetch', fetcher);
  return {
    calls,
    posts: () => calls.filter((call) => call.method === 'POST').map((call) => call.body),
    reads: () => calls.filter((call) => call.method === 'GET' && call.url === '/api/playlists').length,
  };
}

const written = (id: number, name = `列表${id}`) => ({ body: { ok: true, playlist: { id, name, items: [] } } });

function props(patch: Partial<PlaylistsProps> = {}): PlaylistsProps {
  return {
    openPlaylist: vi.fn(), openEntity: vi.fn(),
    canFlip: () => true, toast: vi.fn(), revision: 0, ...patch,
  };
}

let bump: () => void = () => {};
/** 壳经 `updateManagedRoute` 推进来的刷新代次，这里用一层状态代替。 */
function Shell(given: PlaylistsProps) {
  const [revision, setRevision] = useState(given.revision ?? 0);
  bump = () => setRevision((now) => now + 1);
  return <PlaylistsPage {...given} revision={revision} />;
}

function open(items: PlaylistRow[], given = props()) {
  queryClient.setQueryData(PLAYLISTS_KEY, { items });
  return mount(<QueryClientProvider client={queryClient}><Shell {...given} /></QueryClientProvider>);
}

const card = (host: ParentNode, id: number) => host.querySelector(`[data-playlist-card="${id}"]`)!;
const createInput = (host: ParentNode) => host.querySelector<HTMLInputElement>('[data-playlist-create] input')!;
const undoOf = (toast: PlaylistsProps['toast'], index = 0) =>
  vi.mocked(toast).mock.calls[index]?.[1]?.undo as () => Promise<void>;

async function menuAction(host: ParentNode, id: number, label: string) {
  await click(card(host, id).querySelector('[data-playlist-menu]'));
  await click(buttonNamed(label));
}

type Confirmation = Parameters<typeof legacyUi.confirmModal>[0];
function confirmYes() {
  const seen: Confirmation[] = [];
  vi.spyOn(legacyUi, 'confirmModal').mockImplementation((async (options: Confirmation) => {
    seen.push(options);
    await options.onConfirm?.();
    return { confirmed: true };
  }) as typeof legacyUi.confirmModal);
  return seen;
}

describe('首屏', () => {
  it('缓存里有上一次的列表也照样重取：首页刚存的 Mix 要看得到', async () => {
    queryClient.setQueryData(PLAYLISTS_KEY, { items: [row(1)] });
    const server = serve({ 'GET /api/playlists': { body: { items: [row(1), row(2)] } } });
    await prefetchPlaylists(new AbortController().signal);
    expect(server.reads()).toBe(1);
    expect(queryClient.getQueryData<{ items: PlaylistRow[] }>(PLAYLISTS_KEY)?.items).toHaveLength(2);
  });

  it('读取失败时页头与新建框照常在，列表的位置上是原因和重试键；重试成功后画出列表', async () => {
    let fail = true;
    serve({ 'GET /api/playlists': () => (fail ? { status: 500, body: { error: '数据库被锁住' } } : { body: { items: [row(1)] } }) });
    const host = await mount(<QueryClientProvider client={queryClient}><Shell {...props()} /></QueryClientProvider>);
    await settle();
    expect(host.querySelector('h2')?.textContent).toBe('播放列表');
    expect(host.querySelector('[data-playlist-create]')).not.toBeNull();
    const note = host.querySelector('[data-media-error]')!;
    expect(note.textContent).toContain('数据库被锁住');
    fail = false;
    await click(note.querySelector('[data-note-action]'));
    await settle();
    expect(card(host, 1)).not.toBeNull();
    expect(host.querySelector('[data-media-error]')).toBeNull();
  });

  it('没有列表时是整页空态，图标取播放列表字形', async () => {
    const host = await open([]);
    const empty = host.querySelector('[data-empty-state]')!;
    expect(empty.querySelector('h3')?.textContent).toBe('还没有播放列表');
    expect(empty.querySelector('use')?.getAttribute('href')).toBe('#i-playlist');
    expect(host.querySelector('[data-playlist-grid]')).toBeNull();
  });
});

describe('卡片', () => {
  it('名称只有空白时标题与读屏名写「未命名播放列表」', async () => {
    const host = await open([row(1, { name: '   ' })]);
    expect(card(host, 1).textContent).toContain('未命名播放列表');
    expect(card(host, 1).querySelector('[data-mix-open]')?.getAttribute('aria-label')).toBe('打开播放列表 未命名播放列表');
  });

  it('上千条的徽标带千分位', async () => {
    const host = await open([row(1, { item_count: 98765 })]);
    expect(card(host, 1).querySelector('[data-mix-badge]')?.textContent).toBe('98,765 个视频');
  });

  it('来源、条数与封面按行画；点封面从续播点接着播', async () => {
    const given = props();
    const host = await open([row(1, { source_kind: 'mix', current_asset_id: 12, item_count: 3 }), row(2)], given);
    const mix = card(host, 1);
    expect(mix.textContent).toContain('由 Mix 保存');
    expect(card(host, 2).textContent).toContain('手动播放列表');
    expect(mix.querySelector('[data-mix-badge]')?.textContent).toBe('3 个视频');
    expect(mix.querySelector('[data-mix-poster]')?.getAttribute('src')).toBe('/poster?id=11&c=4');
    const opener = mix.querySelector<HTMLButtonElement>('[data-mix-open]')!;
    expect(opener.getAttribute('aria-label')).toBe('打开播放列表 列表1');
    await click(opener);
    expect(given.openPlaylist).toHaveBeenCalledWith(1, 12);
    await click(card(host, 2).querySelector('[data-mix-open]'));
    expect(given.openPlaylist).toHaveBeenLastCalledWith(2, 11);
  });

  it('空列表没有去处：点击区不可点，封面写「无预览」', async () => {
    const given = props();
    const host = await open([row(3, { item_count: 0, preview_asset_id: null, preview_ids: [] })], given);
    const opener = card(host, 3).querySelector<HTMLButtonElement>('[data-mix-open]')!;
    expect(opener.disabled).toBe(true);
    expect(card(host, 3).querySelector('[data-mix-cover]')?.textContent).toContain('无预览');
    await click(opener);
    expect(given.openPlaylist).not.toHaveBeenCalled();
  });

  it('头像各自通往资料页；没有人时画标题首字', async () => {
    const given = props();
    const faces = [{ kind: 'performer', id: 7, name: '甲', has_image: false }];
    const host = await open([row(1, { faces }), row(2, { name: '周末慢看' })], given);
    const avatar = card(host, 1).querySelector<HTMLButtonElement>('[data-mix-avatars] button')!;
    expect(avatar.getAttribute('aria-label')).toBe('打开资料页：甲');
    expect(avatar.innerHTML).toBe('<span class="ini">甲</span>');
    await click(avatar);
    expect(given.openEntity).toHaveBeenCalledWith('performer', '甲');
    expect(card(host, 2).querySelector('[data-mix-initial]')?.textContent).toBe('周');
  });
});

describe('新建', () => {
  it('只带名称新建一份空列表，成功后清空输入框、重读列表，撤销删掉刚建的那份', async () => {
    const server = serve({ 'POST /api/playlist': (body) => ((body as { action: string }).action === 'create'
      ? written(9, '新列表') : { body: { ok: true, deleted: 9 } }) });
    const given = props();
    const host = await open([], given);
    await type(createInput(host), '新列表');
    await submit(host.querySelector('[data-playlist-create]'));
    await settle();
    expect(server.posts()).toEqual([{ action: 'create', name: '新列表', asset_ids: [] }]);
    expect(createInput(host).value).toBe('');
    expect(server.reads()).toBe(1);
    expect(given.toast).toHaveBeenCalledWith('已新建播放列表', { undo: expect.any(Function) });
    await act(async () => { await undoOf(given.toast)() });
    await settle();
    expect(server.posts()[1]).toEqual({ action: 'delete', id: 9 });
    expect(server.reads()).toBe(2);
  });

  it('名称为空不发请求，原因写在输入框下面', async () => {
    const server = serve();
    const host = await open([]);
    await type(createInput(host), '   ');
    await submit(host.querySelector('[data-playlist-create]'));
    expect(server.calls).toHaveLength(0);
    expect(createInput(host).getAttribute('aria-invalid')).toBe('true');
    expect(host.querySelector('[data-playlist-create]')?.textContent).toContain('播放列表名称不能为空');
  });

  it('服务端拒绝时原因留在原位，输入框里的名字不丢', async () => {
    serve({ 'POST /api/playlist': { status: 409, body: { error: '已有同名播放列表' } } });
    const given = props();
    const host = await open([], given);
    await type(createInput(host), '重名');
    await submit(host.querySelector('[data-playlist-create]'));
    await settle();
    expect(host.querySelector('[data-playlist-create]')?.textContent).toContain('已有同名播放列表');
    expect(createInput(host).value).toBe('重名');
    expect(given.toast).not.toHaveBeenCalled();
  });
});

describe('改名', () => {
  it('菜单里「编辑名称」打开弹层，保存后重读，撤销改回原名', async () => {
    const server = serve({ 'POST /api/playlist': written(1) });
    const given = props();
    const host = await open([row(1)], given);
    await menuAction(host, 1, '编辑名称');
    const form = document.querySelector('[data-playlist-rename]')!;
    const input = form.querySelector('input')!;
    expect(input.value).toBe('列表1');
    await type(input, '改过的名字');
    await submit(form);
    await settle();
    expect(server.posts()).toEqual([{ action: 'rename', id: 1, name: '改过的名字' }]);
    expect(document.querySelector('[data-playlist-rename]')).toBeNull();
    expect(given.toast).toHaveBeenCalledWith('已重命名播放列表', { undo: expect.any(Function) });
    await act(async () => { await undoOf(given.toast)() });
    await settle();
    expect(server.posts()[1]).toEqual({ action: 'rename', id: 1, name: '列表1' });
    expect(server.reads()).toBe(2);
  });

  it('失败原因写在弹层里，弹层不关', async () => {
    serve({ 'POST /api/playlist': { status: 409, body: { error: '已有同名播放列表' } } });
    const host = await open([row(1)]);
    await menuAction(host, 1, '编辑名称');
    const form = document.querySelector('[data-playlist-rename]')!;
    await type(form.querySelector('input'), '重名');
    await submit(form);
    await settle();
    expect(document.querySelector('[data-playlist-rename]')?.textContent).toContain('已有同名播放列表');
  });
});

describe('删除', () => {
  const kept = (patch: Partial<PlaylistDetail> = {}): PlaylistDetail => ({
    id: 1, name: '列表1', source_kind: 'mix', source_seed_asset_id: 12, current_asset_id: 12,
    items: [{ id: 11 }, { id: 12 }], ...patch,
  });

  it('先过确认，删之前取回内容；撤销按原内容与来源重建一份', async () => {
    const seen = confirmYes();
    const server = serve({
      'GET /api/playlist?id=1': { body: kept() },
      'POST /api/playlist': (body) => ((body as { action: string }).action === 'delete'
        ? { body: { ok: true, deleted: 1 } } : written(5)),
    });
    const given = props();
    const host = await open([row(1, { source_kind: 'mix' })], given);
    await menuAction(host, 1, '删除播放列表');
    await settle();
    expect(seen[0]).toMatchObject({ title: '删除播放列表', confirmLabel: '删除播放列表', danger: true });
    const order = server.calls.map((call) => `${call.method} ${call.url}`);
    expect(order.indexOf('GET /api/playlist?id=1')).toBeLessThan(order.indexOf('POST /api/playlist'));
    expect(server.posts()[0]).toEqual({ action: 'delete', id: 1 });
    expect(given.toast).toHaveBeenCalledWith('已删除播放列表', { undo: expect.any(Function) });
    await act(async () => { await undoOf(given.toast)() });
    await settle();
    expect(server.posts()[1]).toEqual({
      action: 'create', name: '列表1', asset_ids: [11, 12], source_kind: 'mix', source_seed_asset_id: 12,
    });
    expect(server.reads()).toBe(2);
  });

  it('Mix 种子已经不在列表里时重建不带种子', async () => {
    confirmYes();
    const server = serve({
      'GET /api/playlist?id=1': { body: kept({ source_seed_asset_id: 99 }) },
      'POST /api/playlist': { body: { ok: true, deleted: 1 } },
    });
    const given = props();
    const host = await open([row(1)], given);
    await menuAction(host, 1, '删除播放列表');
    await settle();
    await act(async () => { await undoOf(given.toast)() });
    expect(server.posts()[1]).toMatchObject({ action: 'create', source_seed_asset_id: null });
  });

  it('内容取不到时照删，但不给撤销', async () => {
    confirmYes();
    serve({
      'GET /api/playlist?id=1': { status: 500, body: { error: '读取失败' } },
      'POST /api/playlist': { body: { ok: true, deleted: 1 } },
    });
    const given = props();
    const host = await open([row(1)], given);
    await menuAction(host, 1, '删除播放列表');
    await settle();
    expect(given.toast).toHaveBeenCalledWith('已删除播放列表', undefined);
  });
});

describe('壳要求重读', () => {
  it('刷新代次每加一次重取一次，首帧那一代不重复取', async () => {
    const server = serve({ 'GET /api/playlists': { body: { items: [row(1), row(2)] } } });
    const host = await open([row(1)]);
    await settle();
    expect(server.reads()).toBe(0);
    await act(async () => bump());
    await settle();
    expect(server.reads()).toBe(1);
    expect(host.querySelectorAll('[data-playlist-card]')).toHaveLength(2);
  });
});

/** jsdom 不解码图片：给 `src` 就当作载好，宽度非零。 */
class LoadedImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  naturalWidth = 16;
  set src(_url: string) { queueMicrotask(() => this.onload?.()) }
  decode() { return Promise.resolve() }
}

describe('悬停翻页', () => {
  const hover = (el: Element) => act(async () => {
    el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, relatedTarget: document.body }));
  });
  const leave = (el: Element) => act(async () => {
    el.dispatchEvent(new MouseEvent('mouseout', { bubbles: true, relatedTarget: document.body }));
  });
  const advance = async (ms: number) => {
    await act(async () => { vi.advanceTimersByTime(ms) });
    await settle();
  };
  const stateOf = (host: ParentNode) =>
    [...host.querySelectorAll('[data-mix-face]')].map((face) => face.getAttribute('data-mix-face'));

  it('停 340ms 才开始，420ms 翻第一下，之后每 1100ms 一张；离开还原静止封面', async () => {
    vi.stubGlobal('Image', LoadedImage);
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const host = await open([row(1, { preview_ids: [11, 12, 13] })]);
    const article = card(host, 1);
    const panel = article.querySelector<HTMLElement>('[data-mix-faces]')!;
    await hover(article);
    await advance(339);
    expect(panel.hidden).toBe(true);
    await advance(1);
    expect(panel.hidden).toBe(false);
    expect(stateOf(article)).toEqual(['on', '', '']);
    await advance(420);
    expect(stateOf(article)).toEqual(['off', 'on', '']);
    await advance(1100);
    expect(stateOf(article)).toEqual(['', 'off', 'on']);
    await leave(article);
    expect(panel.hidden).toBe(true);
    expect(article.querySelectorAll('[data-mix-face]')).toHaveLength(0);
  });

  it('壳说不能翻（多选、遮挡、减少动效、滚动中）就不启动', async () => {
    vi.stubGlobal('Image', LoadedImage);
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const host = await open([row(1, { preview_ids: [11, 12, 13] })], props({ canFlip: () => false }));
    await hover(card(host, 1));
    await advance(2000);
    expect(card(host, 1).querySelector<HTMLElement>('[data-mix-faces]')?.hidden).toBe(true);
  });

  it('只有一张图可翻时不翻', async () => {
    vi.stubGlobal('Image', LoadedImage);
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const host = await open([row(1, { preview_ids: [11] })]);
    await hover(card(host, 1));
    await advance(2000);
    expect(card(host, 1).querySelector<HTMLElement>('[data-mix-faces]')?.hidden).toBe(true);
  });
});
