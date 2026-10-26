/* 目录网格与垃圾队列由路由树画进 `#grid`（`CATALOG_ROUTES`）：冷启动的骨架经 `revealSkeleton` 抬成淡出层，与整页
 * 落在同一批变化里；壳认领表面只收 `#stats` 与 `#index`，`#grid` 上那一页留着；停在目录时壳经 `updateManagedRoute`
 * 推选择态、版式与筛选，页面不重挂、代次不变，换筛选只按新键重取一次；垃圾队列一次清空骨架，收起时计数行那一格
 * 同步撤掉；壳那一份助手与动作原样交给页面；这些路径不交给 React Router。
 *
 * 一屏卡片怎么排、卡面画什么由 `catalog-grid.test.tsx` 与 `junk-queue.test.tsx` 管，这里看的是路由树这一层。 */
import { act, type ReactElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

import type { CatalogGridProps, MediaCardActions, MediaCardHelpers } from '../../src/react/catalog-grid/types';
import type { JunkQueueProps } from '../../src/react/junk-queue/junk-queue';
import type { ShellActions } from '../../src/react/router/shell-actions';
import { revealSkeleton } from '../../src/ui-kit';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

async function load(path = '/') {
  vi.resetModules();
  window.history.replaceState(null, '', path);
  const [history, router, routes] = await Promise.all([
    import('../../src/history'), import('../../src/react/router/router'), import('../../src/react/router/managed-routes'),
  ]);
  return { ...history, ...router, ...routes };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

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

async function mount(r: Loaded) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot actions={shellActions()} />) });
  unmounts.push(() => root.unmount());
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
}

type Reply = { ok: boolean; status: number; json(): Promise<unknown> };
const reply = (body: unknown): Reply => ({ ok: true, status: 200, json: async () => body });

/** 目录的一页：名字带着排序键，认得出画的是哪一次取回来的。 */
const catalogPage = (sort: string) => ({
  items: [1, 2, 3].map((id) => ({ id, name: `作品 ${id} ${sort}`, has_thumb: true })), total: 3,
});
const playlistPage = { items: [] };
/** 垃圾队列的一页：名字带着分类，认得出画的是哪一次取回来的。 */
const junkPage = (kind: string) => ({
  items: [11, 12, 13].map((id) => ({
    id, name: `垃圾 ${id} ${kind || '全部'}.mp4`, junk_kind: kind || 'video', why: '时长过短', location: 'local', cost: 'free',
  })),
  total: 3, all_total: 3, dismissed_total: 0, counts: { video: 3 },
});

/** 目录、垃圾队列与播放列表的取数：`/api/items` 可以手动兑现（首屏等的就是它）。 */
function catalogFetch({ deferred = false } = {}) {
  const pending: Array<() => void> = [];
  const fetched = vi.fn((input: string, init?: RequestInit) => {
    const url = new URL(String(input), 'http://peach.test');
    const body = url.pathname === '/api/playlists' ? playlistPage
      : url.pathname === '/api/ads' ? junkPage(url.searchParams.get('kind') || '')
        : catalogPage(url.searchParams.get('sort') || '');
    if (!deferred || url.pathname !== '/api/items') return Promise.resolve(reply(body));
    return new Promise<Reply>((resolve, reject) => {
      pending.push(() => resolve(reply(body)));
      init?.signal?.addEventListener('abort', () => { reject(new DOMException('已中止', 'AbortError')) });
    });
  });
  vi.stubGlobal('fetch', fetched);
  const calls = (path: string) => fetched.mock.calls
    .map(([input]) => new URL(String(input), 'http://peach.test')).filter((url) => url.pathname === path);
  return { fetched, calls, resolve: () => { for (const settle of pending.splice(0)) settle() } };
}

const helpers: MediaCardHelpers = {
  badgeHtml: () => '', titleHtml: (_item, raw) => raw, displayName: (_item, raw) => raw, tagLabel: (tag) => tag,
};
function cardActions(): MediaCardActions {
  return {
    open: vi.fn(), openResource: vi.fn(), openShort: vi.fn(), openShorts: vi.fn(), openMix: vi.fn(),
    openEntity: vi.fn(), openUnowned: vi.fn(), toggleTag: vi.fn(), toggleSelection: vi.fn(),
    watchLater: vi.fn(async () => {}), resourceOperation: vi.fn(async () => {}), mixRelated: vi.fn(async () => []),
    canFlip: () => true,
  };
}
const catalogOpen = (patch: Partial<CatalogGridProps> = {}): CatalogGridProps => ({
  mode: 'catalog', helpers, actions: cardActions(), layout: { active: false, size: 'small', portrait: false, javImage: 'cover' },
  selectMode: false, selected: new Set(), seekSeconds: 10, revision: 0, skeletonHtml: () => '', wireDrag: vi.fn(),
  settled: vi.fn(), filters: { sort: 'seed' }, batchSize: 60, groupCollapse: true, excludeVertical: false,
  mix: false, shorts: false, onCount: vi.fn(), emptyHtml: () => '', canLoadMore: () => true, ...patch,
});

/** 垃圾队列那一页的打开：计数行是壳的 `#count`，壳往里铺着骨架那一版。 */
function junkOpen(patch: Partial<JunkQueueProps> = {}): JunkQueueProps {
  const countRow = document.createElement('div');
  countRow.id = 'count';
  countRow.innerHTML = '<div class="peach-react" data-junk-count-skeleton=""></div>';
  document.body.append(countRow);
  return {
    kind: '', view: 'pending', helpers: { badgeHtml: () => '' },
    actions: { navigate: vi.fn(), toggleSelection: vi.fn(), open: vi.fn(), reveal: vi.fn(async () => ''), operate: vi.fn(async () => {}) },
    batchSize: 60, revision: 0, selectMode: false, selected: new Set(), countRow,
    skeletonHtml: () => '<div class="grid"><div class="skeletoncard"></div></div>', settled: vi.fn(), ...patch,
  };
}
const junkCards = (grid: Element) => grid.querySelectorAll('.peach-react [data-junk-card]');

const GRID_SKELETON = '<div class="grid"><div class="catalog-skeleton" data-skeleton="catalog">'
  + '<div class="skeletoncard"></div></div></div>';

/** 目录的容器，里面是壳铺好的骨架。 */
function gridSurface() {
  const grid = document.createElement('div');
  grid.id = 'grid';
  grid.innerHTML = GRID_SKELETON;
  document.body.append(grid);
  return { grid, skeleton: grid.firstElementChild! };
}
function statsSurface() {
  const stats = document.createElement('section');
  stats.id = 'stats';
  stats.innerHTML = '<div data-skeleton="playlists">正在读取播放列表</div>';
  const index = document.createElement('section');
  index.id = 'index';
  document.body.append(stats, index);
  return { stats, index };
}

/** 同壳里的 `revealRoutedPage`：骨架交给 `revealSkeleton` 抬成淡出层，`write` 只放进空宿主。 */
function revealRoutedPage(container: Element): HTMLElement {
  const host = document.createElement('div');
  host.className = 'peach-react';
  revealSkeleton(container, () => { container.textContent = ''; container.append(host) });
  return host;
}

async function until(ok: () => boolean, what: string): Promise<void> {
  for (let step = 0; step < 200; step += 1) {
    if (ok()) return;
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
  throw new Error(`一直没等到：${what}`);
}

const cards = (grid: Element) => grid.querySelectorAll('.peach-react [data-media-grid] > [data-media-card][data-id]');
/** 页面画出来的那一块（宿主里的第一个元素）：重挂之后就换成另一个。 */
const painted = (grid: Element) => grid.querySelector(':scope > .peach-react > *');

it('冷启动深链首页：骨架淡出与整页在同一批变化里交接，中间没有空帧', async () => {
  const r = await load('/');
  const { grid, skeleton } = gridSurface();
  await mount(r);
  const fetch = catalogFetch({ deferred: true });
  const open = catalogOpen();
  const opened = r.openManagedRoute('/', open, { container: grid, isCurrent: () => true, place: revealRoutedPage });
  await until(() => fetch.calls('/api/items').length > 0, '首屏取数发出去');
  expect(grid.firstElementChild, '取齐之前骨架原样留着').toBe(skeleton);
  const batches: Array<{ bare: boolean; fading: boolean; painted: boolean; revealing: boolean }> = [];
  const observer = new MutationObserver(() => {
    batches.push({
      bare: Boolean(grid.querySelector(':scope > .grid > [data-skeleton]')),
      fading: Boolean(grid.querySelector(':scope > .skelfade [data-skeleton]')),
      painted: cards(grid).length === 3,
      revealing: grid.classList.contains('skelreveal') && grid.classList.contains('revealing'),
    });
  });
  observer.observe(grid, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  unmounts.push(() => observer.disconnect());
  fetch.resolve();
  await act(async () => { expect(await opened).toBe(true) });
  expect(batches[0], '撤下骨架的那一批里，骨架已在淡出层、整页已经画好').toEqual(
    { bare: false, fading: true, painted: true, revealing: true });
  expect(batches.every((batch) => batch.bare || batch.fading || batch.painted), '任何一批都不是空的').toBe(true);
  expect(grid.querySelector('.skelfade')?.firstElementChild, '淡出层里是原来那张骨架').toBe(skeleton);
  expect(fetch.calls('/api/items'), '首屏只取一次').toHaveLength(1);
  expect(open.settled).toHaveBeenCalledWith(0);
  expect(r.managedEntry(grid)).toMatchObject({ path: '/', props: { filters: { sort: 'seed' } } });
  // jsdom 不发 transitionend，淡出层靠 `revealSkeleton` 的兜底定时器收掉。
  await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 1100) }) });
  expect(grid.querySelector('.skelfade')).toBeNull();
  expect(cards(grid)).toHaveLength(3);
});

/* 壳每次换页都认领表面（`claimSurface`），目录内换筛选也是：那里点名收 `#stats` 与 `#index`，`#grid` 上的
   网格照旧画着，只有点名 `#grid` 才收。 */
it('`#grid` 上画着网格时，收 `#stats` 与 `#index` 不动它；点名 `#grid` 才收', async () => {
  const r = await load('/');
  const { grid } = gridSurface();
  const { stats, index } = statsSurface();
  await mount(r);
  const fetch = catalogFetch();
  await act(async () => { await r.openManagedRoute('/', catalogOpen(), { container: grid, isCurrent: () => true }) });
  await act(async () => { await r.openManagedRoute('/playlists', { revision: 0 }, { container: stats, isCurrent: () => true }) });
  const page = painted(grid);
  const revision = r.managedEntry(grid)?.revision;
  expect(r.managedEntries().map((entry) => entry.path)).toEqual(['/', '/playlists']);
  act(() => { r.releaseManagedRoute(stats, index) });
  expect(stats.querySelector('.peach-react'), '`#stats` 那一页收掉了').toBeNull();
  expect(painted(grid), '收别的容器不许卸网格').toBe(page);
  expect(r.managedEntry(grid)?.revision).toBe(revision);
  expect(cards(grid)).toHaveLength(3);
  expect(fetch.calls('/api/items'), '网格不重取').toHaveLength(1);
  act(() => { r.releaseManagedRoute(grid) });
  expect([grid.querySelector('.peach-react'), r.managedEntries().length]).toEqual([null, 0]);
});

/* 多选、换版式、换快进秒数落在目录上：壳推进来，卡片就地重画，不重挂也不重取。 */
it('就地推选择态与版式：代次不变、不重挂、不重取', async () => {
  const r = await load('/');
  const { grid } = gridSurface();
  await mount(r);
  const fetch = catalogFetch();
  const open = catalogOpen();
  await act(async () => { await r.openManagedRoute('/', open, { container: grid, isCurrent: () => true }) });
  const page = painted(grid);
  const revision = r.managedEntry(grid)?.revision;
  await act(async () => { r.updateManagedRoute(grid, { selected: new Set([2]), selectMode: true }) });
  expect(grid.querySelector('[data-media-sections]')?.hasAttribute('data-select-mode')).toBe(true);
  await act(async () => {
    r.updateManagedRoute(grid, { layout: { active: true, size: 'big', portrait: false, javImage: 'cover' }, seekSeconds: 30 });
  });
  expect(painted(grid), '推选择态与版式不许重挂').toBe(page);
  expect(r.managedEntry(grid)?.revision).toBe(revision);
  expect(fetch.calls('/api/items'), '不重取').toHaveLength(1);
  expect(r.managedEntry(grid)?.props).toMatchObject({ selectMode: true, seekSeconds: 30, layout: { size: 'big' } });
  expect((r.managedEntry(grid)!.props as CatalogGridProps).actions, '壳那一份动作原样留着').toBe(open.actions);
});

/* 换排序、侧栏标签、首页与回收站互切落在目录上：壳推新的筛选与刷新代次，页面只按新键重取一次，宿主不换。 */
it('就地推筛选与刷新代次：宿主与代次不变，只按新键重取一次', async () => {
  const r = await load('/');
  const { grid } = gridSurface();
  await mount(r);
  const fetch = catalogFetch();
  const open = catalogOpen();
  await act(async () => { await r.openManagedRoute('/', open, { container: grid, isCurrent: () => true }) });
  const host = r.managedEntry(grid)?.host;
  const revision = r.managedEntry(grid)?.revision;
  await act(async () => { r.updateManagedRoute(grid, { filters: { sort: 'new' }, revision: 1 }) });
  await until(() => Boolean(grid.textContent?.includes('作品 1 new')), '按新键重取回来的那一份画上');
  expect(fetch.calls('/api/items').map((url) => url.searchParams.get('sort')), '只按新键重取一次').toEqual(['seed', 'new']);
  expect(grid.querySelectorAll(':scope > .peach-react'), '宿主只有一个').toHaveLength(1);
  expect(r.managedEntry(grid)?.host).toBe(host);
  expect(r.managedEntry(grid)?.revision).toBe(revision);
  expect(open.settled).toHaveBeenLastCalledWith(1);
});

/* 进垃圾文件：壳先收掉 `#grid` 上的目录网格，再打开队列。队列打开前不取数，一次清空骨架、放进宿主，
   不交叉淡入；读数由页面自己取，只取一次。 */
it('垃圾队列：换掉 `#grid` 上的网格，一次清空骨架，首屏由页面自己取一次', async () => {
  const r = await load('/junk-files');
  const { grid } = gridSurface();
  await mount(r);
  const fetch = catalogFetch();
  await act(async () => { await r.openManagedRoute('/', catalogOpen(), { container: grid, isCurrent: () => true }) });
  expect(grid.querySelector('[data-media-sections]'), '先画着目录网格').not.toBeNull();
  const open = junkOpen();
  await act(async () => { expect(await r.openManagedRoute('/junk-files', open, { container: grid, isCurrent: () => true })).toBe(true) });
  expect(r.managedEntries().map((entry) => entry.path), '同一容器只留一页').toEqual(['/junk-files']);
  expect([...grid.children].map((el) => el.className), '骨架一次清空，没有淡出层').toEqual(['peach-react']);
  await until(() => junkCards(grid).length === 3, '队列画上');
  expect(grid.querySelector('[data-media-sections]'), '目录网格已经不在').toBeNull();
  expect(fetch.calls('/api/ads'), '读数只取一次').toHaveLength(1);
  expect(open.countRow?.querySelector(':scope > .peach-react:not([data-junk-count-skeleton])'), '计数行那一格接管了骨架').not.toBeNull();
  expect(open.settled).toHaveBeenCalledWith(0);
});

/* 壳从垃圾文件去别处时先收 `#grid`，再往 `#count` 铺骨架。收的那一刻队列在计数行里建的那一格必须已经撤掉：
   晚一拍撤，就是把壳刚铺的骨架冲掉，或者往一个已经被换掉的节点里卸 portal。 */
it('收 `#grid` 上的垃圾队列：计数行那一格在收的同一刻撤掉，壳随后铺的骨架留得住', async () => {
  const r = await load('/junk-files');
  const { grid } = gridSurface();
  await mount(r);
  catalogFetch();
  const open = junkOpen();
  const count = open.countRow!;
  await act(async () => { await r.openManagedRoute('/junk-files', open, { container: grid, isCurrent: () => true }) });
  await until(() => junkCards(grid).length === 3, '队列画上');
  const errors = vi.spyOn(console, 'error');
  let atRelease: unknown[] = [];
  act(() => {
    r.releaseManagedRoute(grid);
    atRelease = [count.childElementCount, grid.querySelector('.peach-react'), r.managedEntry(grid)];
    count.innerHTML = '<div class="peach-react" data-junk-count-skeleton=""></div>';
  });
  expect(atRelease, '收的那一刻计数行那一格、宿主与登记都已撤掉').toEqual([0, null, null]);
  await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  expect(count.querySelectorAll('[data-junk-count-skeleton]'), '壳铺的骨架还在').toHaveLength(1);
  expect(errors).not.toHaveBeenCalled();
  errors.mockRestore();
});

/* 换分类、换视图、处置完重读与选择模式落在垃圾队列上：壳推进来，页面按新键重取，宿主与代次不变。 */
it('就地推分类、刷新代次与选择态：垃圾队列不重挂，只按新键重取一次', async () => {
  const r = await load('/junk-files');
  const { grid } = gridSurface();
  await mount(r);
  const fetch = catalogFetch();
  const open = junkOpen();
  await act(async () => { await r.openManagedRoute('/junk-files', open, { container: grid, isCurrent: () => true }) });
  await until(() => junkCards(grid).length === 3, '队列画上');
  const host = r.managedEntry(grid)?.host;
  const revision = r.managedEntry(grid)?.revision;
  const countHost = open.countRow?.firstElementChild;
  await act(async () => { r.updateManagedRoute(grid, { kind: 'image', revision: 1 }) });
  await until(() => Boolean(grid.textContent?.includes('垃圾 11 image')), '按新分类取回来的那一份画上');
  await act(async () => { r.updateManagedRoute(grid, { selectMode: true, selected: new Set([12]) }) });
  expect(fetch.calls('/api/ads').map((url) => url.searchParams.get('kind')), '只按新键重取一次').toEqual([null, 'image']);
  expect([r.managedEntry(grid)?.host, r.managedEntry(grid)?.revision]).toEqual([host, revision]);
  expect(open.countRow?.firstElementChild, '计数行那一格不重建').toBe(countHost);
  expect(grid.querySelector('[data-junk-grid]')?.hasAttribute('data-select-mode')).toBe(true);
  expect(open.settled).toHaveBeenLastCalledWith(1);
});

it('目录网格与垃圾队列：壳那一整份 props 原样交给页面，助手与动作身份不变', async () => {
  const r = await load();
  const open = catalogOpen({ revision: 4 });
  const props = (r.CATALOG_ROUTES['/'].page(open, shellActions(), vi.fn()) as ReactElement<CatalogGridProps>).props;
  expect(props).toEqual(open);
  expect(props.helpers).toBe(open.helpers);
  expect(props.actions).toBe(open.actions);
  const junk = junkOpen({ revision: 2 });
  const junkProps = (r.CATALOG_ROUTES['/junk-files'].page(junk, shellActions(), vi.fn()) as ReactElement<JunkQueueProps>).props;
  expect(junkProps).toEqual(junk);
  expect(junkProps.helpers).toBe(junk.helpers);
  expect(junkProps.actions).toBe(junk.actions);
});

it('目录与垃圾文件的路径不走 React Router：交壳写地址再打开；槽里只认页面键', async () => {
  const r = await load('/stats');
  const actions = shellActions();
  const navigate = vi.fn();
  const paths = ['/', '/unseen', '/watch-later', '/flagged', '/trash', '/junk-files'];
  for (const path of paths) r.managedGo(path, actions, navigate);
  r.managedGo('/?tag=a&sort=new', actions, navigate);
  expect(navigate).not.toHaveBeenCalled();
  expect(vi.mocked(actions.navigate).mock.calls).toEqual([...paths, '/?tag=a&sort=new'].map((path) => [path]));
  for (const path of paths) {
    expect(r.ROUTED_PATHS).toContain(path);
    expect(r.isManagedPath(path)).toBe(false);
  }
  expect([r.isRoutedPath('/'), r.isRoutedPath('/junk-files')]).toEqual([true, true]);
  expect(r.isRoutedPath('/trash'), '回收站画的也是 `/` 那一页').toBe(false);
});
