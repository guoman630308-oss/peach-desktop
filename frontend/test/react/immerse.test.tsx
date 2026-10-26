/* 沉浸模式：片单怎么抽、起播与深链落在哪一条、每换一条取几次详情、动作键写回哪几份缓存、关掉之后
 * 读取有没有按会话取消，以及单击、双击、横划、竖划与进度条这几种手势；还有它作为常驻组件
 * （`RESIDENT_ROUTES.immerse`）在路由树里的行为：宿主是 body 末尾的 `[data-immerse-host]`、画上之后才交出
 * 句柄、`open` 里的几次绘制同步画完、抛错只卸组件而模块上的监听还在。
 *
 * 每条用例照应用启动顺序走一遍：重新装载模块、画路由树、接上取数，再经 `application-residents` 的 `loadImmerse` 拿句柄。
 * 播放器换成一个记账的替身：这里看的是这一面交给它什么、什么时候拆它，真 Video.js 与真流会话由
 * e2e 在浏览器里走一遍（`e2e/immerse.test.ts`）。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import { seekVideoBy, toggleVideoPlayback } from '../../src/player/playback';
import type { BatchDockApi, BatchDockHost } from '../../src/react/batch-dock/batch-dock-api';
import { catalogKey, type GridPage } from '../../src/react/catalog-grid/catalog-grid';
import {
  FIT_TOLERANCE, extendList, fitMode, immerseQuery, isWide, ownerOf, playable, type ImmerseItem,
} from '../../src/react/immerse/immerse';
import type { ImmerseApi, ImmerseHost } from '../../src/react/immerse/immerse-api';
import { itemKey, type DetailItem } from '../../src/react/item-detail/item-detail';
import type { ShellActions } from '../../src/react/router/shell-actions';
import { click, pending } from './render';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
// 完整路由树的冷加载单独计时，交互用例使用 Vitest 默认超时。
const REACT_IMPORT_TIMEOUT_MS = 60_000;

type ActFlag = { IS_REACT_ACT_ENVIRONMENT?: boolean };

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

interface Mounted { video: HTMLVideoElement; item: ImmerseItem; session: string; dispose: ReturnType<typeof vi.fn> }

const player = vi.hoisted(() => ({
  mounted: [] as Mounted[],
  cancelled: [] as string[],
  sessions: 0,
  /** 每条片子的画面尺寸；没登记的按竖屏。 */
  dims: new Map<number, [number, number]>(),
}));

/** 一条假媒体：可读的时长、可写的播放位置，play／pause 改 `paused`。 */
function fakeMedia(video: HTMLVideoElement, [width, height]: [number, number]) {
  let time = 0, paused = true;
  Object.defineProperties(video, {
    readyState: { configurable: true, get: () => 4 },
    duration: { configurable: true, get: () => 100 },
    currentTime: { configurable: true, get: () => time, set: (value: number) => { time = value } },
    paused: { configurable: true, get: () => paused },
    videoWidth: { configurable: true, get: () => width },
    videoHeight: { configurable: true, get: () => height },
  });
  video.play = vi.fn(async () => { paused = false });
  video.pause = vi.fn(() => { paused = true });
}

vi.mock('../../src/player', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/player')>();
  return {
    ...actual,
    newStreamSession: () => `s${++player.sessions}`,
    cancelStreamSession: (session: string) => { player.cancelled.push(session) },
    streamSpeedBits: () => 0,
    mountPlayer: (video: HTMLVideoElement, options: { item: ImmerseItem; session: string; onLoaded?: () => void }) => {
      fakeMedia(video, player.dims.get(options.item.id) ?? [1080, 1920]);
      const dispose = vi.fn();
      player.mounted.push({ video, item: options.item, session: options.session, dispose });
      queueMicrotask(() => options.onLoaded?.());
      return dispose;
    },
  };
});

/* ── 假服务端 ── */

const row = (id: number, patch: Partial<ImmerseItem> = {}): ImmerseItem => ({
  id, name: `clip-${id}.mp4`, duration: 60, cost: 'free', location: 'local', width: 1080, height: 1920, ctx_orient: '竖屏', ...patch,
});

const detail = (id: number, patch: Partial<DetailItem> = {}): DetailItem => ({
  ...row(id), performers: ['甲'], creator: '', entity_refs: { performer: [{ id: 90 + id, has_image: true }] },
  feedback: undefined, o_count: 2, ...patch,
} as DetailItem);

interface Server {
  /** 每一批 `/api/items` 回的条目，按请求顺序取，取完重复最后一批。 */
  draws: ImmerseItem[][];
  details?: Record<number, Partial<DetailItem>>;
  /** 某条详情由用例放行。 */
  hold?: Map<number, Promise<unknown>>;
}

function serve(server: Server) {
  let drawn = 0;
  const json = (body: unknown) => ({ ok: true, status: 200, json: async () => structuredClone(body) });
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as { id: number; kind?: string };
      if (url === '/api/feedback') {
        if (body.kind === 'o') return json({ feedback: null, o_count: 3 });
        return json({ feedback: body.kind, o_count: 2 });
      }
      return json({ ok: true });
    }
    if (url.startsWith('/api/items?')) {
      const items = server.draws[Math.min(drawn, server.draws.length - 1)]!;
      drawn += 1;
      return json({ items });
    }
    const id = Number(/^\/api\/item\?id=(\d+)/.exec(url)?.[1]);
    if (id) {
      await server.hold?.get(id);
      return json(detail(id, server.details?.[id]));
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

const gets = (fetcher: ReturnType<typeof serve>, prefix: string) =>
  fetcher.mock.calls.filter(([url, init]) => !init?.method && url.startsWith(prefix)).length;
const posts = (fetcher: ReturnType<typeof serve>) => fetcher.mock.calls
  .filter(([, init]) => init?.method === 'POST').map(([url, init]) => [url, JSON.parse(String(init!.body))]);

/* ── 宿主与页面 ── */

type HostMock = { [K in keyof ImmerseHost]: Mock<ImmerseHost[K]> };

function makeHost(): HostMock {
  return {
    filters: vi.fn<ImmerseHost['filters']>(() => ({ q: '', orient: 'portrait', thumb: '0' })),
    seekSeconds: vi.fn<ImmerseHost['seekSeconds']>(() => 10),
    sourceOffline: vi.fn<ImmerseHost['sourceOffline']>((location) => location === 'gone'),
    displayName: vi.fn<ImmerseHost['displayName']>((item) => `片名 ${String(item.name)}`),
    route: vi.fn<ImmerseHost['route']>(),
    closed: vi.fn<ImmerseHost['closed']>(),
    openItem: vi.fn<ImmerseHost['openItem']>(),
    openEntity: vi.fn<ImmerseHost['openEntity']>(),
    openUnowned: vi.fn<ImmerseHost['openUnowned']>(),
    toast: vi.fn<ImmerseHost['toast']>(),
    warn: vi.fn<ImmerseHost['warn']>(),
    failure: vi.fn<ImmerseHost['failure']>(),
  };
}

/* 常驻组件适配层直接读取同一包内的配置函数；测试只接本场景的句柄契约。 */
type ShellIslands = { connectApplication(next: (actions: ShellActions) => void): () => void;
  loadImmerse(host: ImmerseHost): Promise<ImmerseApi>;
  immerseApi(): ImmerseApi | null;
  loadBatchDock(host: BatchDockHost): Promise<BatchDockApi>;
};
const ISLANDS_MODULE = '../../src/react/application-residents';

async function load() {
  vi.resetModules();
  window.history.replaceState(null, '', '/');
  const [history, router, routes, query, islands] = await Promise.all([
    import('../../src/history'), import('../../src/react/router/router'), import('../../src/react/router/managed-routes'),
    import('../../src/react/query'), import(/* @vite-ignore */ ISLANDS_MODULE) as Promise<ShellIslands>,
  ]);
  return { ...history, ...router, ...routes, queryClient: query.queryClient, islands };
}
type Loaded = Awaited<ReturnType<typeof load>>;

function shellActions(): ShellActions {
  return {
    openItem: vi.fn(), openEntity: vi.fn(), openTag: vi.fn(), openTasteSignal: vi.fn(), navigate: vi.fn(),
    openManage: vi.fn(), managePath: vi.fn(() => ''), openFollow: vi.fn(), receipt: vi.fn(), toast: vi.fn(),
    failure: vi.fn(), revealSource: vi.fn(async () => ''), reopenTutorial: vi.fn(async () => {}),
    requestConfigurationSection: vi.fn(), routeReview: vi.fn(),
    routeFollowManage: vi.fn(), saveFollowPreference: vi.fn(), srcBadge: () => '', routeIndex: vi.fn(),
    savePeopleLayout: vi.fn(), exitSelectMode: vi.fn(), personAvatar: vi.fn(() => ({ html: '', face: '' })),
    authorAvatar: vi.fn(() => ''), showIndexTags: vi.fn(), openFollowAuthor: vi.fn(), openFollowTag: vi.fn(),
    openPlaylist: vi.fn(), canFlip: vi.fn(() => true),
    surfaceChanged: vi.fn(), clearSearch: vi.fn(), openImmerse: vi.fn(), openOverlay: vi.fn(), closeStage: vi.fn(), grid: {} as ShellActions['grid'],
  };
}

const unmounts: Array<() => void> = [];

/** 壳启动时先画路由树（根的选项与 `mountApplication` 同一份）；接上取数单独一步，用例可以把它往后放。 */
async function mountRouter(r: Loaded) {
  unmounts.push(r.islands.connectApplication(() => {}));
  const tree = createRoot(document.createElement('div'), r.ROUTER_ROOT_OPTIONS);
  await act(async () => { tree.render(<r.RouterRoot actions={shellActions()} />) });
  unmounts.push(() => tree.unmount());
}
async function connect(r: Loaded) {
  await act(async () => { r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute)) });
}

/** 收下每一次上报；`console.error` 只静音，不数条数。 */
function watchReports() {
  const reported = vi.fn();
  vi.stubGlobal('reportError', reported);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  return reported;
}

let host: ReturnType<typeof makeHost>;
let api: ImmerseApi;
let loaded: Loaded;
let queryClient: Loaded['queryClient'];

const q = <T extends Element = HTMLElement>(selector: string) => document.querySelector<T>(selector);
const root = () => q('[data-immerse]')!;
const track = () => q('[data-immerse-track]')!;
const meta = () => q('[data-immerse-author]>span')!.textContent;
const routed = () => host.route.mock.calls.map(([id]) => id);
const current = () => player.mounted.at(-1)!;

/** 等一件事成立：换条要走两帧动画加 210ms 落位，都在 act 里等完。 */
async function until(condition: () => boolean, ms = 2_000) {
  await act(async () => {
    const deadline = Date.now() + ms;
    while (!condition()) {
      if (Date.now() > deadline) throw new Error('等不到条件成立');
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  });
}
const wait = (ms: number) => act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)) });

async function open(startId: number | null = null) {
  await act(async () => { await api.open(startId) });
}

const key = (name: string, target: EventTarget = document) => act(async () => {
  target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
});

/** 下一条出画：地址栏写到它、加载提示收起、只剩一格。 */
const shown = (id: number) => until(() => routed().at(-1) === id && !!q('[data-immerse-loader]')?.hidden
  && document.querySelectorAll('[data-immerse-slide]').length === 1 && current().item.id === id);

function touch(type: string, x: number, y: number) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  const point = [{ clientX: x, clientY: y }];
  Object.defineProperties(event, {
    touches: { value: type === 'touchend' ? [] : point },
    changedTouches: { value: point },
  });
  return event;
}
const swipe = (...steps: [string, number, number][]) => act(async () => {
  for (const [type, x, y] of steps) track().dispatchEvent(touch(type, x, y));
});

/* 照壳的 `openTok` 接上：路由树已经画着、接上了取数，第一次打开沉浸模式时装载并拿到句柄。 */
beforeEach(async () => {
  player.mounted = [];
  player.cancelled = [];
  player.dims.clear();
  // 洗牌恒等：j 总取 i，片单顺序就是服务端给的顺序。
  vi.spyOn(Math, 'random').mockReturnValue(0.999);
  loaded = await load();
  queryClient = loaded.queryClient;
  await mountRouter(loaded);
  await connect(loaded);
  host = makeHost();
  await act(async () => { api = await loaded.islands.loadImmerse(host) });
});

afterEach(async () => {
  (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = true;
  act(() => api.close());
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
});

describe('片单与起播', () => {
  it('抽样去掉画幅、随机取 60 条且偏移恒为 0；续取按 id 去重接在后面', () => {
    const query = new URLSearchParams(immerseQuery({ q: '', orient: 'portrait', tag: '剧情', jav: null }));
    expect(Object.fromEntries(query)).toEqual({ tag: '剧情', sort: 'rand', limit: '60', offset: '0', thumb: '' });
    expect(extendList([row(1), row(2)], [row(2), row(3)]).map((item) => item.id)).toEqual([1, 2, 3]);
  });

  it('片单不收按流量计费、没有时长与来源脱盘的片子', async () => {
    const offline = (location: string) => location === 'gone';
    expect(playable(row(1), offline)).toBe(true);
    expect(playable(row(2, { cost: 'metered' }), offline)).toBe(false);
    expect(playable(row(3, { duration: 0 }), offline)).toBe(false);
    expect(playable(row(4, { location: 'gone' }), offline)).toBe(false);
    serve({ draws: [[row(1, { location: 'gone' }), row(2)]] });
    await open();
    expect(current().item.id).toBe(2);
    expect(meta()).toBe('· 1:00 · 竖屏 · 1/1');
  });

  it('起播那一条在这一批里就从它放起，详情只取它自己那一份', async () => {
    const fetcher = serve({ draws: [[1, 2, 3, 4, 5, 6].map((id) => row(id))] });
    await open(3);
    expect(player.mounted.map((entry) => entry.item.id)).toEqual([3]);
    expect(routed()).toEqual([3]);
    expect(meta()).toBe('· 1:00 · 竖屏 · 3/6');
    expect(gets(fetcher, '/api/item?id=')).toBe(1);
  });

  it('片名空着时标题写「未命名视频」', async () => {
    serve({ draws: [[1, 2, 3].map((id) => row(id))] });
    host.displayName.mockImplementation(() => '');
    await open(1);
    expect(q('[data-immerse-title]')!.textContent).toBe('未命名视频');
  });

  it('深链的那一条不在这一批里：取它的详情插到最前，地址栏落回同一条', async () => {
    const fetcher = serve({ draws: [[1, 2, 3, 4, 5, 6].map((id) => row(id))] });
    await open(42);
    expect(current().item.id).toBe(42);
    expect(routed()).toEqual([42]);
    expect(q('[data-immerse-title]')!.textContent).toBe('片名 clip-42.mp4');
    expect(meta()).toBe('· 1:00 · 竖屏 · 1/7');
    // 插进来时取的那一份就是出画时读的那一份，不再取第二次。
    expect(gets(fetcher, '/api/item?id=42')).toBe(1);
  });

  it('换条与切回都只取一次详情；每换一条交给壳写一次地址', async () => {
    const fetcher = serve({ draws: [[1, 2, 3, 4, 5, 6, 7, 8].map((id) => row(id))] });
    await open();
    await key('ArrowDown');
    await shown(2);
    await key('ArrowUp');
    await shown(1);
    expect(routed()).toEqual([1, 2, 1]);
    expect(gets(fetcher, '/api/item?id=1')).toBe(1);
    expect(gets(fetcher, '/api/item?id=2')).toBe(1);
    expect(gets(fetcher, '/api/items?')).toBe(1);
    // 起播记一次：每条出画时一次。
    expect(posts(fetcher).filter(([url]) => url === '/api/play').map(([, body]) => body.id)).toEqual([1, 2, 1]);
  });

  it('离尾部不足三条就再抽一批，接上之后计数跟着变', async () => {
    const fetcher = serve({ draws: [[row(1), row(2), row(3), row(4)], [row(3), row(4), row(5)]] });
    await open();
    await key('ArrowDown');
    await shown(2);
    expect(gets(fetcher, '/api/items?')).toBe(2);
    expect(meta()).toBe('· 1:00 · 竖屏 · 2/5');
  });

  it('输入框里的方向键不换条', async () => {
    serve({ draws: [[1, 2, 3, 4, 5].map((id) => row(id))] });
    await open();
    const input = document.createElement('input');
    document.body.append(input);
    await key('ArrowDown', input);
    await wait(50);
    expect(routed()).toEqual([1]);
  });

  it('当前筛选下一条都放不了：关掉并带提示音警告', async () => {
    serve({ draws: [[row(1, { cost: 'metered' })]] });
    await open();
    expect(api.isOpen()).toBe(false);
    expect(host.warn).toHaveBeenCalledWith('当前筛选下没有可直接播放的内容');
    expect(player.mounted).toEqual([]);
  });

  it('第一条出画前只露加载提示：舞台、作者标题与进度条藏着', async () => {
    const hold = pending<void>();
    serve({ draws: [[row(1), row(2)]], hold: new Map([[1, hold.answer]]) });
    let opening!: Promise<void>;
    await act(async () => { opening = api.open(null) });
    await until(() => !!q('[data-immerse-loader]') && !q('[data-immerse-loader]')!.hidden);
    expect(root().hasAttribute('data-idle')).toBe(true);
    expect(q('[data-immerse-loader]')!.textContent).toContain('加载中…');
    expect(document.body.hasAttribute('data-immerse-open')).toBe(true);
    await hold.release();
    await act(async () => { await opening });
    expect(root().hasAttribute('data-idle')).toBe(false);
    expect(q('[data-immerse-loader]')!.hidden).toBe(true);
  });
});

describe('关闭与流会话', () => {
  it('关闭键拆掉每一格的播放器、解开锁滚，交回壳', async () => {
    serve({ draws: [[row(1), row(2)]] });
    await open();
    const first = current();
    await click(q('[data-immerse-close]'));
    expect(first.dispose).toHaveBeenCalledTimes(1);
    expect(api.isOpen()).toBe(false);
    expect(api.activeVideo()).toBeNull();
    expect(root().hidden).toBe(true);
    expect(document.body.hasAttribute('data-immerse-open')).toBe(false);
    expect(document.querySelectorAll('[data-immerse-slide]')).toHaveLength(0);
    expect(host.closed).toHaveBeenCalledTimes(1);
  });

  it('换条时旧的那一格拆掉；离开页面按会话取消还开着的格', async () => {
    serve({ draws: [[1, 2, 3, 4, 5, 6].map((id) => row(id))] });
    await open();
    const first = current();
    await key('ArrowDown');
    await shown(2);
    expect(first.dispose).toHaveBeenCalledTimes(1);
    expect(current().dispose).not.toHaveBeenCalled();
    await act(async () => { window.dispatchEvent(new Event('pagehide')) });
    expect(player.cancelled).toEqual([current().session]);
  });

  it('关掉之后在途的那一条不再挂播放器', async () => {
    const hold = pending<void>();
    serve({ draws: [[row(1), row(2)]], hold: new Map([[1, hold.answer]]) });
    let opening!: Promise<void>;
    await act(async () => { opening = api.open(null) });
    await until(() => !!q('[data-immerse-loader]') && !q('[data-immerse-loader]')!.hidden);
    act(() => api.close());
    await hold.release();
    await act(async () => { await opening });
    expect(player.mounted).toEqual([]);
    expect(api.isOpen()).toBe(false);
  });
});

describe('动作键', () => {
  const grid = (): GridPage => ({ items: [{ id: 1, name: 'clip-1.mp4', feedback: null, o_count: 2 }], offset: 0 } as unknown as GridPage);
  const card = () => queryClient.getQueryData<{ pages: GridPage[] }>(catalogKey('q=', 0))!.pages[0]!.items[0]!;
  const action = (label: string) => q<HTMLButtonElement>(`[data-immerse-actions] button[aria-label="${label}"]`)!;

  it('看过写进同一份详情：详情缓存与目录卡一起换，按下态与字样跟着', async () => {
    const fetcher = serve({ draws: [[row(1), row(2)]] });
    queryClient.setQueryData(catalogKey('q=', 0), { pages: [grid()], pageParams: [0] });
    await open();
    expect(action('标为看过').getAttribute('aria-pressed')).toBe('false');
    expect(action('记一次高潮').hasAttribute('aria-pressed')).toBe(false);
    await click(action('标为看过'));
    await until(() => action('标为看过').getAttribute('aria-pressed') === 'true');
    expect(posts(fetcher).filter(([url]) => url === '/api/feedback')).toEqual([['/api/feedback', { id: 1, kind: 'seen' }]]);
    expect(queryClient.getQueryData<DetailItem>(itemKey(1))!.feedback).toBe('seen');
    expect(card()).toMatchObject({ feedback: 'seen' });
    expect(action('标为看过').closest('[data-immerse-action]')!.textContent).toBe('已看');
    expect(host.toast).toHaveBeenCalledWith('已标记看过', expect.objectContaining({ undo: expect.any(Function) }));
  });

  it('高潮计数随回执换，目录卡同一个数', async () => {
    serve({ draws: [[row(1), row(2)]] });
    queryClient.setQueryData(catalogKey('q=', 0), { pages: [grid()], pageParams: [0] });
    await open();
    expect(action('记一次高潮').closest('[data-immerse-action]')!.textContent).toBe('高潮 2');
    await click(action('记一次高潮'));
    await until(() => action('记一次高潮').closest('[data-immerse-action]')!.textContent === '高潮 3');
    expect(card()).toMatchObject({ o_count: 3 });
    expect(host.toast).toHaveBeenCalledWith('已记录一次高潮', expect.anything());
  });

  it('撤销高潮只发一次 o-undo，不去改看过与不喜欢那一档', async () => {
    const fetcher = serve({ draws: [[row(1), row(2)]], details: { 1: { feedback: 'seen' } } });
    await open();
    await click(action('记一次高潮'));
    await until(() => host.toast.mock.calls.length === 1);
    const { undo } = host.toast.mock.calls[0]![1] as { undo: () => Promise<void> };
    await act(async () => { await undo() });
    expect(posts(fetcher).filter(([url]) => url === '/api/feedback').map(([, body]) => body.kind))
      .toEqual(['o', 'o-undo']);
  });

  it('撤销看过：先撤掉这一次，再写回原来那一档', async () => {
    const fetcher = serve({ draws: [[row(1), row(2)]], details: { 1: { feedback: 'dislike' } } });
    await open();
    await click(action('标为看过'));
    await until(() => host.toast.mock.calls.length === 1);
    const { undo } = host.toast.mock.calls[0]![1] as { undo: () => Promise<void> };
    await act(async () => { await undo() });
    expect(posts(fetcher).filter(([url]) => url === '/api/feedback').map(([, body]) => body.kind))
      .toEqual(['seen', 'seen', 'dislike']);
  });

  it('不喜欢落地之后停一下换下一条', async () => {
    serve({ draws: [[1, 2, 3, 4, 5, 6].map((id) => row(id))] });
    await open();
    await click(action('标为不喜欢'));
    await shown(2);
    expect(host.toast).toHaveBeenCalledWith('已标记不合口味', expect.anything());
  });

  it('写失败交给壳报错，按键不卡在忙态', async () => {
    serve({ draws: [[row(1), row(2)]] });
    await open();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ error: '写不进去' }) })));
    await click(action('标为看过'));
    await until(() => host.failure.mock.calls.length === 1);
    expect(host.failure.mock.calls[0]![0]).toBe('更新反馈');
    expect(action('标为看过').hasAttribute('aria-busy')).toBe(false);
  });
});

describe('作者与标题', () => {
  it('共演念前三位、多出来的写人数；点作者先关掉再进第一位的资料页', async () => {
    serve({ draws: [[row(1), row(2)]], details: { 1: { performers: ['甲', '乙', '丙', '丁'] } } });
    await open();
    const author = q<HTMLAnchorElement>('[data-immerse-author]>a')!;
    expect(author.textContent).toBe('甲、乙、丙 等 4 人');
    expect(q('[data-immerse-avatar] .ini')!.textContent).toBe('甲');
    expect(q('[data-immerse-avatar] img')!.getAttribute('src')).toBe('/entity-image?kind=performer&id=91');
    await click(author);
    expect(host.closed).toHaveBeenCalledTimes(1);
    expect(host.openEntity).toHaveBeenCalledWith('performer', '甲');
  });

  it('没有署名人时点作者先关掉再进「未归属」', async () => {
    serve({ draws: [[row(1), row(2)]], details: { 1: { performers: [], creator: '', entity_refs: {} } } });
    await open();
    const author = q<HTMLAnchorElement>('[data-immerse-author]>a')!;
    expect(author.textContent).toBe('未归属');
    await click(author);
    expect(host.closed).toHaveBeenCalledTimes(1);
    expect(host.openEntity).not.toHaveBeenCalled();
    expect(host.openUnowned).toHaveBeenCalledTimes(1);
  });

  it('没有演员就念创作者，再没有就是未归属', () => {
    expect(ownerOf({ performers: [], creator: '某人', entity_refs: {} })).toEqual({ who: '某人', kind: 'creator', name: '某人', ref: null });
    expect(ownerOf({ performers: [], creator: '', entity_refs: {} })).toEqual({ who: '未归属', kind: '', name: '未归属', ref: null });
  });

  it('点标题先关掉再进详情页', async () => {
    serve({ draws: [[row(1), row(2)]] });
    await open();
    await click(q('[data-immerse-title]'));
    expect(api.isOpen()).toBe(false);
    expect(host.closed).toHaveBeenCalledTimes(1);
    expect(host.openItem).toHaveBeenCalledWith(1);
  });
});

describe('画面与舞台', () => {
  it('比例差超过 1.05 才完整显示：横片进竖框、竖片进更长的手机都不裁', () => {
    expect(FIT_TOLERANCE).toBe(1.05);
    expect(fitMode(1920, 1080, 9 / 19.5)).toBe('contain');
    expect(fitMode(1080, 1920, 9 / 19.5)).toBe('contain');
    expect(fitMode(1080, 1920, 9 / 16 * 1.02)).toBe('cover');
    expect(fitMode(0, 0, 1)).toBeNull();
    expect(isWide({ videoWidth: 0, videoHeight: 0 }, { width: 1920, height: 1080 })).toBe(true);
    expect(isWide({ videoWidth: 1080, videoHeight: 1920 }, { width: 1920, height: 1080 })).toBe(false);
  });

  it('横片出画时舞台换成横的；转屏或改窗口之后铺满判定重算', async () => {
    const size = (width: number, height: number) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
    };
    size(1024, 768);
    try {
      player.dims.set(1, [1920, 1080]);
      serve({ draws: [[row(1, { width: 1920, height: 1080 }), row(2)]] });
      await open();
      expect(q('[data-immerse-stage]')!.hasAttribute('data-wide')).toBe(true);
      expect(root().hasAttribute('data-wide')).toBe(true);
      expect(current().video.dataset.fit).toBe('contain');
      size(1600, 900);
      await act(async () => { window.dispatchEvent(new Event('resize')) });
      expect(current().video.dataset.fit).toBeUndefined();
    } finally {
      size(1024, 768);
    }
  });
});

describe('手势', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    serve({ draws: [[1, 2, 3, 4, 5, 6].map((id) => row(id))] });
  });

  it('鼠标单击当场切播放；触屏抬手后浏览器合成的那一下 click 不再切', async () => {
    await open();
    const { video } = current();
    (video.pause as ReturnType<typeof vi.fn>).mockClear();
    (video.play as ReturnType<typeof vi.fn>).mockClear();
    await click(track());
    expect(video.pause).toHaveBeenCalledTimes(1);
    await swipe(['touchstart', 700, 400], ['touchend', 700, 400]);
    await click(track());
    expect(video.pause).toHaveBeenCalledTimes(1);
    expect(video.play).not.toHaveBeenCalled();
  });

  it('触屏一下等双击窗口过去才切；同一半区两下快进快退，不切播放', async () => {
    await open();
    const { video } = current();
    (video.pause as ReturnType<typeof vi.fn>).mockClear();
    await swipe(['touchstart', 700, 400], ['touchend', 700, 400]);
    expect(video.pause).not.toHaveBeenCalled();
    await wait(320);
    expect(video.pause).toHaveBeenCalledTimes(1);
    video.currentTime = 50;
    await swipe(['touchstart', 700, 400], ['touchend', 700, 400], ['touchstart', 705, 402], ['touchend', 705, 402]);
    expect(video.currentTime).toBe(60);
    await swipe(['touchstart', 200, 400], ['touchend', 200, 400], ['touchstart', 200, 400], ['touchend', 200, 400]);
    expect(video.currentTime).toBe(50);
    await wait(320);
    expect(video.pause).toHaveBeenCalledTimes(1);
  });

  it('横划归进度：拖动中只画进度并拦住页面滚动，抬手才 seek', async () => {
    await open();
    const { video } = current();
    const bar = q('[data-immerse-bar]')!;
    await swipe(['touchstart', 500, 400]);
    const lock = touch('touchmove', 520, 402);
    await act(async () => { track().dispatchEvent(lock) });
    expect(bar.hasAttribute('data-scrubbing')).toBe(true);
    const drag = touch('touchmove', 600, 405);
    await act(async () => { track().dispatchEvent(drag) });
    expect(drag.defaultPrevented).toBe(true);
    expect(q('[data-immerse-bar] i')!.style.width).toBe('10.00%');
    expect(video.currentTime).toBe(0);
    await swipe(['touchend', 600, 405]);
    expect(video.currentTime).toBe(10);
    expect(bar.hasAttribute('data-scrubbing')).toBe(false);
    expect(routed()).toEqual([1]);
  });

  it('竖划超过 60px 换条：往上是下一条，往下是上一条', async () => {
    await open();
    await swipe(['touchstart', 500, 600], ['touchmove', 502, 500], ['touchend', 502, 400]);
    await shown(2);
    await swipe(['touchstart', 500, 300], ['touchmove', 500, 400], ['touchend', 500, 500]);
    await shown(1);
    // 不够 60px 的竖划什么都不做。
    await swipe(['touchstart', 500, 300], ['touchmove', 500, 330], ['touchend', 500, 340]);
    await wait(50);
    expect(routed()).toEqual([1, 2, 1]);
  });

  it('进度条按下就画、拖着跟手，松手才 seek', async () => {
    await open();
    const { video } = current();
    const bar = q('[data-immerse-bar]')!;
    bar.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 20, right: 1000, bottom: 20, x: 0, y: 0, toJSON() {} });
    bar.setPointerCapture = vi.fn();
    const pointer = (type: string, clientX: number) => act(async () => {
      bar.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, clientX, pointerId: 1 }));
    });
    await pointer('pointerdown', 250);
    expect(bar.hasAttribute('data-scrubbing')).toBe(true);
    expect(q('[data-immerse-bar] i')!.style.width).toBe('25.00%');
    await pointer('pointermove', 400);
    expect(q('[data-immerse-bar] i')!.style.width).toBe('40.00%');
    expect(video.currentTime).toBe(0);
    await pointer('pointerup', 400);
    expect(video.currentTime).toBe(40);
    expect(bar.hasAttribute('data-scrubbing')).toBe(false);
  });
});

describe('播放键', () => {
  const media = (duration: number, currentTime: number, paused = true) => {
    const video = document.createElement('video');
    let time = currentTime;
    Object.defineProperties(video, {
      duration: { value: duration },
      paused: { value: paused },
      currentTime: { get: () => time, set: (value: number) => { time = value } },
    });
    video.play = vi.fn(async () => {});
    video.pause = vi.fn();
    return video;
  };

  it('快进快退夹在 0 与总长之间；元数据没到（总长 NaN）只夹下界', () => {
    const video = media(100, 95);
    seekVideoBy(video, 10);
    expect(video.currentTime).toBe(100);
    seekVideoBy(video, -200);
    expect(video.currentTime).toBe(0);
    const early = media(Number.NaN, 5);
    seekVideoBy(early, 30);
    expect(early.currentTime).toBe(35);
  });

  it('暂停着就放，放着就停；没有 video 时什么都不做', () => {
    const paused = media(100, 0, true), playing = media(100, 0, false);
    toggleVideoPlayback(paused);
    toggleVideoPlayback(playing);
    toggleVideoPlayback(null);
    expect(paused.play).toHaveBeenCalledTimes(1);
    expect(playing.pause).toHaveBeenCalledTimes(1);
  });
});

describe('常驻组件', () => {
  const immerseHostOf = (r: Loaded) => r.managedEntries().find((entry) => entry.path === 'immerse')?.container as HTMLElement;

  it('画上之后句柄才交出；宿主是 body 末尾的 [data-immerse-host]，里面直接是藏着的外框，不包 .peach-react', async () => {
    const r = await load();
    await mountRouter(r);
    let loading: Promise<ImmerseApi> | null = null;
    await act(async () => { loading = r.islands.loadImmerse(makeHost()) });
    expect(r.islands.immerseApi(), '路由树还没接上取数，句柄不交出').toBeNull();
    expect(immerseHostOf(r), '这一面还没画上').toBeUndefined();

    await connect(r);
    let handed: ImmerseApi | null = null;
    await act(async () => { handed = await loading });
    expect(r.islands.immerseApi()).toBe(handed);
    const container = immerseHostOf(r);
    expect(container.hasAttribute('data-immerse-host')).toBe(true);
    expect(container.parentElement).toBe(document.body);
    expect(document.body.lastElementChild).toBe(container);
    expect(container.children).toHaveLength(1);
    const frame = container.firstElementChild as HTMLElement;
    expect(frame.hasAttribute('data-immerse')).toBe(true);
    expect(frame.hidden, '第一次打开之前外框就在，藏着').toBe(true);
    expect(container.querySelector('.peach-react')).toBeNull();
    expect(container.closest('.peach-react')).toBeNull();
    expect(handed!.isOpen()).toBe(false);
  });

  it('open 里的每一次绘制都在句柄里画完：开头那一拍就露出加载提示，返回时第一条已经出画', async () => {
    serve({ draws: [[row(1), row(2)]] });
    /* 不包 act：act 会把 flushSync 推到它结束时，看不出句柄自己同步没有。 */
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    const opening = api.open(null);
    expect(root().hidden, '同步段里外框已经显出来').toBe(false);
    expect(root().hasAttribute('data-idle')).toBe(true);
    expect(q('[data-immerse-loader]')!.hidden).toBe(false);
    expect(q('[data-immerse-loader]')!.textContent).toContain('加载内容…');
    expect(document.body.hasAttribute('data-immerse-open')).toBe(true);
    await opening;
    expect(root().hasAttribute('data-idle'), '返回时已经画完，不等下一次渲染').toBe(false);
    expect(q('[data-immerse-loader]')!.hidden).toBe(true);
    expect(document.querySelectorAll('[data-immerse-track] > [data-immerse-slide]')).toHaveLength(1);
    expect(meta()).toBe('· 1:00 · 竖屏 · 1/2');
    expect(q('[data-immerse-title]')!.textContent).toBe('片名 clip-1.mp4');
    expect(api.activeVideo()).toBe(current().video);
  });

  it('抛错只卸组件、宿主还在，body 上的打开标记留着，批量条照画；之后开关不抛，方向键与离开页面的监听还在', async () => {
    const reported = watchReports();
    const dockRoot = document.createElement('div');
    dockRoot.setAttribute('data-batch-dock', '');
    document.body.append(dockRoot);
    let dock!: BatchDockApi;
    await act(async () => { dock = await loaded.islands.loadBatchDock({ root: dockRoot, run: vi.fn() }) });
    serve({ draws: [[1, 2, 3, 4, 5, 6].map((id) => row(id))] });
    const container = immerseHostOf(loaded);
    /* 第一条出画、作者标题那一行画标题时抛。 */
    host.displayName.mockImplementation(() => { throw new Error('标题画不出来') });

    await open();
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenCalledWith(expect.objectContaining({ message: '标题画不出来' }));
    expect(container.isConnected, '宿主不撤').toBe(true);
    expect(container.childNodes.length, '组件卸掉了').toBe(0);
    expect(loaded.managedTaken(container)).toBe(false);
    expect(loaded.managedEntries().map((entry) => entry.path), '别的面照画').toEqual(['batch-dock']);
    expect(document.body.hasAttribute('data-immerse-open'), '卸组件不清 body 上的打开标记').toBe(true);
    expect(api.isOpen()).toBe(true);

    /* 模块上的监听不随组件卸掉：方向键照旧换条（交给壳写地址），离开页面照旧按会话取消还开着的格。 */
    const first = current();
    await key('ArrowDown');
    await until(() => routed().at(-1) === 2);
    await act(async () => { window.dispatchEvent(new Event('resize')) });
    await act(async () => { window.dispatchEvent(new Event('pagehide')) });
    expect(player.cancelled).toContain(first.session);

    act(() => { expect(() => api.close()).not.toThrow() });
    expect(api.isOpen()).toBe(false);
    expect(document.body.hasAttribute('data-immerse-open')).toBe(false);
    expect(host.closed).toHaveBeenCalledTimes(1);
    host.displayName.mockImplementation((item) => `片名 ${String(item.name)}`);
    await open();
    expect(api.isOpen()).toBe(true);
    act(() => { expect(() => api.close()).not.toThrow() });
    expect(container.childNodes.length, '空到刷新为止，不自愈').toBe(0);
    expect(reported, '确定性抛错只报那一次').toHaveBeenCalledTimes(1);
    /* 批量条放在最后画：它的进场动画跑在 motion 的帧循环上，画完就收尾，卸树时不留进行中的动画。 */
    await act(async () => { dock.render({ count: 2, context: 'catalog', junkDismissed: false }) });
    expect(dockRoot.querySelector('[data-selection-dock] [role="status"]')?.textContent).toBe('已选 2 项');
  });
});
