/* 详情舞台作为常驻组件（`RESIDENT_ROUTES.stage`）在路由树里的行为：宿主是 body 末尾的 `[data-stage-host]`、
 * 画上之后才交出句柄、小窗节点在句柄交出时已经画好、`open` 里的几次绘制同步画完、换详情不换宿主、抛错只卸
 * 组件而句柄照调不抛。
 *
 * 每条用例照应用启动顺序走一遍：重新装载模块、画路由树、接上取数，再经 `application-residents` 的 `loadStage` 拿句柄。
 * 播放器换成一个记账的替身：这里看的是舞台这一面怎么画、什么时候交出句柄，真 Video.js、进出场与小窗的交接
 * 由 e2e 在浏览器里走一遍（`e2e/stage.test.ts`），播放器在两个位置之间的交接在 `stage-player.test.ts`。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import type { BatchDockApi, BatchDockHost } from '../../src/react/batch-dock/batch-dock-api';
import type { DetailItem, ItemDetailHelpers, ItemDetailProps } from '../../src/react/item-detail/item-detail';
import type { ShellActions } from '../../src/react/router/shell-actions';
import type { StageApi, StageHost, StageItemRequest } from '../../src/react/stage/stage-api';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

type ActFlag = { IS_REACT_ACT_ENVIRONMENT?: boolean };

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

const player = vi.hoisted(() => ({ mounted: [] as HTMLVideoElement[] }));

vi.mock('../../src/player', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/player')>();
  return {
    ...actual,
    cancelDetailStream: vi.fn(),
    mountPlayer: (video: HTMLVideoElement) => {
      player.mounted.push(video);
      return () => {};
    },
  };
});

/* ── 假服务端 ── */

const detail = (id: number): DetailItem => ({
  id, name: `sample_${id}.mp4`, title: `作品 ${id}`, location: 'local', cost: 'free', size: 1024, duration: 3600,
  rating: 60, performers: ['七海ひな'], tags: [{ k: '剧情' }],
} as DetailItem);

function serve() {
  const fetcher = vi.fn(async (url: string) => {
    const id = Number(/^\/api\/item\?id=(\d+)/.exec(url)?.[1]);
    if (id) return { ok: true, status: 200, json: async () => detail(id) };
    return { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

/* ── 宿主与请求 ── */

function makeHost(): StageHost {
  const settings = {
    ambientMode: false, theaterMode: false, seekSeconds: 10, miniplayer: true, detailAutoplay: false, javImage: 'cover',
  };
  return {
    player: {
      settings: () => settings, saveSettings: vi.fn(), toast: vi.fn(), loadSourceStatus: vi.fn(async () => ({})),
      offlineReason: () => '盘没挂上',
    },
    sourceOffline: () => false,
    expand: vi.fn(),
    openItem: vi.fn(),
  };
}

function helpers(patch: Partial<ItemDetailHelpers> = {}): ItemDetailHelpers {
  return {
    badgeHtml: () => '', titleHtml: (shown) => String(shown.name), displayName: (shown) => String(shown.name),
    javImage: () => 'cover',
    tagLabel: (tag) => tag, isDurationTag: () => false, tagCandidates: () => [], sourceOffline: () => false,
    offlineReason: () => '盘没挂上', relatedSkeletonHtml: () => '', mixRelated: async () => [], wireDrag: vi.fn(),
    wireDragReorder: vi.fn(), ...patch,
  };
}

type RequestActions = StageItemRequest['actions'];
function actions(): { [K in keyof RequestActions]: Mock<RequestActions[K]> } {
  return {
    close: vi.fn(), present: vi.fn(), redirect: vi.fn(), openQueueItem: vi.fn(), reopen: vi.fn(),
    checkSource: vi.fn(async () => false), openSavedFollow: vi.fn(), openEntity: vi.fn(), openUnowned: vi.fn(), openRegion: vi.fn(),
    openTag: vi.fn(), addToPlaylist: vi.fn(), saveMix: vi.fn(), editPlaylist: vi.fn(), openPlaylists: vi.fn(),
    reveal: vi.fn(async () => ''), sync: vi.fn(async () => ({ text: '', removed: [] })), trashChanged: vi.fn(async () => {}),
    toast: vi.fn(), failure: vi.fn(),
  };
}

function request(id: number, patch: Partial<StageItemRequest> = {}): StageItemRequest {
  return {
    kind: 'item', id, queue: null, relatedLimit: 0, helpers: helpers(), actions: actions(),
    grid: { helpers: {} as ItemDetailProps['grid']['helpers'], actions: {} as ItemDetailProps['grid']['actions'] },
    layout: {} as ItemDetailProps['layout'], selectMode: false, selected: new Set(), seekSeconds: 10, ...patch,
  };
}

/* 常驻组件适配层直接读取同一包内的配置函数；测试只接本场景的句柄契约。 */
type ShellIslands = { connectApplication(next: (actions: ShellActions) => void): () => void;
  loadStage(host: StageHost): Promise<StageApi>;
  stageApi(): StageApi | null;
  loadBatchDock(host: BatchDockHost): Promise<BatchDockApi>;
};
const ISLANDS_MODULE = '../../src/react/application-residents';

async function load() {
  serve();
  vi.resetModules();
  window.history.replaceState(null, '', '/');
  const [history, router, routes, islands] = await Promise.all([
    import('../../src/history'), import('../../src/react/router/router'), import('../../src/react/router/managed-routes'),
    import(/* @vite-ignore */ ISLANDS_MODULE) as Promise<ShellIslands>,
  ]);
  return { ...history, ...router, ...routes, islands };
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

const stageHostOf = (r: Loaded) => r.managedEntries().find((entry) => entry.path === 'stage')?.container as HTMLElement;
const q = <T extends Element = HTMLElement>(selector: string) => document.querySelector<T>(selector);

let loaded: Loaded;
let api: StageApi;

/* 照壳的 `openItem` 接上：路由树已经画着、接上了取数，第一次打开详情时装载并拿到句柄。 */
beforeEach(async () => {
  player.mounted = [];
  loaded = await load();
  await mountRouter(loaded);
  await connect(loaded);
  await act(async () => { api = await loaded.islands.loadStage(makeHost()) });
});

afterEach(async () => {
  (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = true;
  act(() => api.dispose({ miniplayer: false }));
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.replaceChildren();
});

describe('常驻组件', () => {
  it('画上之后句柄才交出；宿主是 body 末尾的 [data-stage-host]，里面直接是藏着的小窗，不包 .peach-react', async () => {
    const r = await load();
    await mountRouter(r);
    let loading: Promise<StageApi> | null = null;
    await act(async () => { loading = r.islands.loadStage(makeHost()) });
    expect(r.islands.stageApi(), '路由树还没接上取数，句柄不交出').toBeNull();
    expect(stageHostOf(r), '这一面还没画上').toBeUndefined();

    await connect(r);
    let handed: StageApi | null = null;
    await act(async () => { handed = await loading });
    expect(r.islands.stageApi()).toBe(handed);
    const container = stageHostOf(r);
    expect(container.hasAttribute('data-stage-host')).toBe(true);
    expect(container.parentElement).toBe(document.body);
    expect(document.body.lastElementChild).toBe(container);
    expect([...container.children].map((el) => el.id), '句柄交出时小窗节点已经在，舞台还没开').toEqual(['miniplayer']);
    expect((container.firstElementChild as HTMLElement).hidden).toBe(true);
    expect(container.querySelector('.peach-react')).toBeNull();
    expect(container.closest('.peach-react')).toBeNull();
    expect(handed!.isOpen()).toBe(false);
  });

  it('open 里的每一次绘制都在句柄里画完：同步段里骨架与浮窗已经开着，返回时内容已画、焦点在关闭键上；换详情不换宿主', async () => {
    serve();
    const container = stageHostOf(loaded);
    const first = request(1);
    /* 不包 act：act 会把 flushSync 推到它结束时，看不出句柄自己同步没有。 */
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    const opening = api.open(first);
    const dialog = q<HTMLDialogElement>('#stage')!;
    expect(dialog.parentElement, '浮窗直接画在宿主里').toBe(container);
    expect(dialog.open, '同步段里已经 showModal').toBe(true);
    expect(dialog.querySelector(':scope > [data-skeleton="detail"]'), '同步段里骨架已经画上').not.toBeNull();
    expect(document.body.hasAttribute('data-detail-open')).toBe(true);
    expect(api.isOpen()).toBe(true);
    await opening;
    expect(dialog.querySelector('[data-stage-scroll] > .peach-react'), '返回时内容已经画完，不等下一次渲染').not.toBeNull();
    expect(q('#vid'), '媒体框交给舞台的播放器').toBe(player.mounted.at(-1));
    expect(document.activeElement?.id).toBe('closeStage');
    expect(first.actions.present, '画出来的那一条报给壳').toHaveBeenCalledTimes(1);
    expect(first.actions.present).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }), null);

    const second = request(2);
    await api.open(second);
    expect(q('#stage'), '同一种详情开着时原地换条，浮窗不重建').toBe(dialog);
    expect(dialog.open).toBe(true);
    expect(dialog.querySelector('[data-stage-scroll] > .peach-react'), '返回时新的一条已经画完').not.toBeNull();
    expect(second.actions.present, '原地换上的那一条同样报给壳').toHaveBeenCalledTimes(1);
    expect(second.actions.present).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }), null);
    expect(first.actions.present, '换走的那一条不再报').toHaveBeenCalledTimes(1);
    expect(stageHostOf(loaded)).toBe(container);
    expect(document.querySelectorAll('[data-stage-host]')).toHaveLength(1);
    expect([...container.children].map((el) => el.id)).toEqual(['stage', 'miniplayer']);

    api.dispose({ miniplayer: false });
    expect(q('#stage'), 'dispose 返回时浮窗已经拆掉').toBeNull();
    expect(document.body.hasAttribute('data-detail-open')).toBe(false);
    expect([...container.children].map((el) => el.id), '小窗节点留着').toEqual(['miniplayer']);

    await api.open(request(3));
    expect(q('#stage'), '拆掉之后再开按新的一代重建浮窗').not.toBe(dialog);
    expect(stageHostOf(loaded), '重开也是同一个宿主').toBe(container);
    expect([...container.children].map((el) => el.id)).toEqual(['stage', 'miniplayer']);
  });

  it('重进已读取的作品立即显示缓存，后台刷新不遮住内容', async () => {
    serve();
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    await api.open(request(1));
    api.dispose({ miniplayer: true });
    let finish!: (value: unknown) => void;
    vi.stubGlobal('fetch', vi.fn((url: string) => url === '/api/library-processing'
      ? Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'idle' }) })
      : new Promise(resolve => { finish = resolve })));
    const opening = api.open(request(1));
    const dialog = q<HTMLDialogElement>('#stage')!;
    expect(dialog.querySelector('[data-skeleton="detail"]')).toBeNull();
    expect(dialog.querySelector('[data-item-side]')).not.toBeNull();
    finish({ ok: true, status: 200, json: async () => detail(1) });
    await opening;
    expect(dialog.querySelector('[data-skeleton="detail"]')).toBeNull();
  });

  it('缓存里的空队列退出详情时不留舞台和等待态', async () => {
    serve();
    const { queryClient } = await import('../../src/react/query');
    const { queueKey } = await import('../../src/react/item-detail/item-detail');
    const queue = { kind: 'parts' as const, seedId: 1 };
    queryClient.setQueryData(queueKey(queue), { ...queue, title: '示例卷组', items: [] });
    const props = request(1, { id: null, queue });
    vi.mocked(props.actions.redirect).mockImplementation(() => api.dispose({ miniplayer: false }));
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    await api.open(props);
    expect(props.actions.redirect).toHaveBeenCalledWith({ kind: 'gone' });
    expect(api.isOpen()).toBe(false);
    expect(q('#stage')).toBeNull();
  });

  it('抛错只卸组件、宿主还在，body 上的打开标记留着，批量条照画；之后句柄各成员照调不抛', async () => {
    const reported = watchReports();
    const dockRoot = document.createElement('div');
    dockRoot.setAttribute('data-batch-dock', '');
    document.body.append(dockRoot);
    let dock!: BatchDockApi;
    await act(async () => { dock = await loaded.islands.loadBatchDock({ root: dockRoot, run: vi.fn() }) });
    serve();
    const container = stageHostOf(loaded);
    /* 内容画出来、侧栏画标签时抛。 */
    const broken = request(1, { helpers: helpers({ tagLabel: () => { throw new Error('标签画不出来') } }) });

    await act(async () => { await api.open(broken) });
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenCalledWith(expect.objectContaining({ message: '标签画不出来' }));
    expect(container.isConnected, '宿主不撤').toBe(true);
    expect(container.childNodes.length, '组件卸掉了，浮窗跟着消失').toBe(0);
    expect(q('#stage')).toBeNull();
    expect(loaded.managedTaken(container)).toBe(false);
    expect(loaded.managedEntries().map((entry) => entry.path), '别的面照画').toEqual(['batch-dock']);
    expect(document.body.hasAttribute('data-detail-open'), '卸组件不清 body 上的打开标记').toBe(true);
    expect(broken.actions.present, '没画出来的那一条不报').not.toHaveBeenCalled();

    act(() => {
      expect(api.isOpen()).toBe(true);
      expect(api.showing(), '浮窗跟着组件卸掉了，没有能原地换条的那一种').toBeNull();
      expect(api.activeVideo()).toBeNull();
      expect(() => api.update({ selectMode: true })).not.toThrow();
      expect(() => api.toggleTheater()).not.toThrow();
      expect(() => api.toggleMiniplayer()).not.toThrow();
      expect(() => api.closeMiniplayer()).not.toThrow();
      expect(api.miniplayerActive()).toBe(false);
      expect(api.miniplayerTakesCard(detail(2))).toBe(false);
      expect(() => api.repaintPoster()).not.toThrow();
      expect(() => api.requestClose()).not.toThrow();
    });
    expect(broken.actions.close, '关闭请求照旧交给详情自己的 close').toHaveBeenCalledTimes(1);
    await act(async () => { await expect(api.exit()).resolves.toBeUndefined() });
    act(() => { expect(() => api.dispose({ miniplayer: false })).not.toThrow() });
    expect(api.isOpen()).toBe(false);
    expect(document.body.hasAttribute('data-detail-open')).toBe(false);
    await act(async () => { await expect(api.open(request(2))).resolves.toBeUndefined() });
    expect(api.isOpen()).toBe(true);
    act(() => { expect(() => api.miniplayerPlay(3)).not.toThrow() });
    act(() => { expect(() => api.dispose({ miniplayer: false })).not.toThrow() });
    expect(container.childNodes.length, '空到刷新为止，不自愈').toBe(0);
    expect(reported, '确定性抛错只报那一次').toHaveBeenCalledTimes(1);
    /* 批量条放在最后画：它的进场动画跑在 motion 的帧循环上，画完就收尾，卸树时不留进行中的动画。 */
    await act(async () => { dock.render({ count: 2, context: 'catalog', junkDismissed: false }) });
    expect(dockRoot.querySelector('[data-selection-dock] [role="status"]')?.textContent).toBe('已选 2 项');
  });
});
