/* 目录的路由元素（`src/react/router/pages/catalog.tsx`）：页面组匹配到目录六条路径时挂上，按页面打开序号、壳的重开
 * 次数（`@peach/shell` 的 `pageOpens`）与覆盖层决定整页打开还是只重取；整页打开时照地址重建目录筛选，交壳收尾。
 *
 * 网格怎么取数、怎么画由 `catalog-routes.test.tsx` 与各页的用例管；这里看的是元素什么时候开、开几次、写进
 * `state` 的是哪一套筛选、交给壳什么。历史对象与壳状态都是模块级的，每条用例重新装载。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

import type { ShellActions } from '../../../src/react/router/shell-actions';

// 完整路由树的冷加载单独计时，交互用例使用 Vitest 默认超时。
const REACT_IMPORT_TIMEOUT_MS = 60_000;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => { await import('../../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

async function load(path = '/') {
  vi.resetModules();
  window.history.replaceState(null, '', path);
  const [history, router, routes, shell, core, sorting] = await Promise.all([
    import('../../../src/history'), import('../../../src/react/router/router'),
    import('../../../src/react/router/managed-routes'), import('../../../src/shell'),
    import('../../../src/core'), import('../../../src/sort-preferences'),
  ]);
  return { ...history, ...router, ...routes, shell, core, sortFromAddress: sorting.sortFromAddress };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

/** 壳交进来的那一组，外加一份按先后记下的打开过程。 */
function shellActions() {
  const log: string[] = [];
  const actions: ShellActions = {
    openItem: vi.fn(), openEntity: vi.fn(), openTag: vi.fn(), openTasteSignal: vi.fn(), navigate: vi.fn(),
    openManage: vi.fn(), managePath: vi.fn(() => ''), openFollow: vi.fn(), receipt: vi.fn(), toast: vi.fn(),
    failure: vi.fn(), revealSource: vi.fn(async () => ''), reopenTutorial: vi.fn(async () => {}),
    requestConfigurationSection: vi.fn(), routeReview: vi.fn(),
    routeFollowManage: vi.fn(), saveFollowPreference: vi.fn(), srcBadge: () => '', routeIndex: vi.fn(),
    savePeopleLayout: vi.fn(), exitSelectMode: vi.fn(), personAvatar: vi.fn(() => ({ html: '', face: '' })),
    authorAvatar: vi.fn(() => ''), showIndexTags: vi.fn(), openFollowAuthor: vi.fn(), openFollowTag: vi.fn(),
    openPlaylist: vi.fn(), canFlip: () => true,
    surfaceChanged: vi.fn(), clearSearch: vi.fn(), openImmerse: vi.fn(), openOverlay: vi.fn(), closeStage: vi.fn(),
    grid: { helpers: {}, actions: {} } as ShellActions['grid'],
    configurable: () => true,
    surfaceShown: vi.fn(),
    catalog: {
      defaultLoc: () => 'local,115',
      open: vi.fn((path: string, options?: { entering?: boolean; retitle?: boolean }) => {
        log.push(`open ${path}${options?.entering ? ' entering' : ''}${options?.retitle ? ' retitle' : ''}`);
      }),
      load: vi.fn(() => { log.push('load') }),
      release: vi.fn(() => { log.push('release') }),
      fill: vi.fn(() => { log.push('fill') }),
    },
  };
  return { actions, log };
}

async function settle() {
  for (let step = 0; step < 3; step += 1) {
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
}

/** 挂上路由根，再像壳那样开始路由：启动那一次把 `pageOpens` 写成 1。 */
async function boot(r: Loaded, actions: ShellActions) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot actions={actions} />) });
  unmounts.push(() => root.unmount());
  await settle();
  await act(async () => { r.shell.writeShell({ pageOpens: 1 }) });
  await settle();
}

/** 后退前进：浏览器换好地址再报 `popstate`，不经壳。 */
async function pop(path: string, state: unknown = null) {
  await act(async () => {
    window.history.replaceState(state, '', path);
    window.dispatchEvent(new PopStateEvent('popstate', { state }));
  });
  await settle();
}

const filters = (r: Loaded) => r.shell.state ?? {};

it('启动：壳开始路由之前只挂着；启动那一下照地址写筛选、整页打开一次', async () => {
  const r = await load('/unseen?tag=a,%E7%9F%AD%E7%89%87-2%E5%88%86%E5%86%85&owner=none&loc=local');
  const { actions, log } = shellActions();
  await boot(r, actions);
  expect(log).toEqual(['open /unseen']);
  expect(filters(r)).toMatchObject({ state: 'fresh', tag: 'a', owner: 'none', loc: 'local' });
  expect(filters(r).seed, '地址上没写种子就掷一粒').toMatch(/^\d+$/);
});

it('从回收站后退到首页重掷取样种子，并让壳作废顶部三层的缓存', async () => {
  const r = await load('/trash');
  r.shell.writeShell({ state: { seed: '111', sort: 'seed', dir: '' } });
  const { actions, log } = shellActions();
  await boot(r, actions);
  expect(log).toEqual(['open /trash']);
  expect(filters(r)).toMatchObject({ state: 'trash', seed: '111' });
  await pop('/');
  expect(log.slice(1)).toEqual(['open / entering']);
  expect(filters(r).state).toBe('');
  expect(filters(r).seed).toMatch(/^\d+$/);
  expect(filters(r).seed).not.toBe('111');
});

it('首页换一枚标签的后退前进沿用这一粒种子', async () => {
  const r = await load('/?tag=a');
  r.shell.writeShell({ state: { seed: '222' } });
  const { actions, log } = shellActions();
  await boot(r, actions);
  expect(filters(r)).toMatchObject({ tag: 'a', seed: '222' });
  await pop('/?tag=b');
  expect(log.slice(1)).toEqual(['open /']);
  expect(filters(r)).toMatchObject({ tag: 'b', seed: '222' });
});

it('地址上写了种子就用它', async () => {
  const r = await load('/trash');
  const { actions } = shellActions();
  await boot(r, actions);
  await pop('/?seed=4321');
  expect(filters(r).seed).toBe('4321');
});

it('壳认领写地址只重取，不重建筛选；同一个地址再写一次也重取', async () => {
  const r = await load('/');
  const { actions, log } = shellActions();
  await boot(r, actions);
  r.shell.writeShell({ state: { ...filters(r), tag: 'x' } });
  await act(async () => { r.shellNavigate('/?tag=x') });
  await settle();
  await act(async () => { r.shellNavigate('/?tag=x', { replace: true }) });
  await settle();
  expect(log.slice(1)).toEqual(['load', 'load']);
  expect(filters(r).tag).toBe('x');
});

it('首页、筛选态与回收站之间换页不重挂；壳认领进回收站、紧接着要求重开，两件事合成一次整页打开', async () => {
  const r = await load('/');
  const { actions, log } = shellActions();
  await boot(r, actions);
  await act(async () => {
    r.shellNavigate('/trash');
    r.shell.writeShell({ pageOpens: r.shell.pageOpens + 1 });
  });
  await settle();
  expect(log.slice(1)).toEqual(['open /trash']);
  expect(actions.catalog?.release).not.toHaveBeenCalled();
});

it('详情压着时什么都不做；关掉回到压着的那一份地址、筛选没变时不重取，筛选变了才重取', async () => {
  const r = await load('/?tag=a');
  const { actions, log } = shellActions();
  await boot(r, actions);
  const overlay = { backgroundLocation: { pathname: '/', search: '?tag=a' }, overlay: 'item' };
  await act(async () => { r.shellNavigate('/assets/7', { state: overlay }) });
  await settle();
  await act(async () => { r.peachHistory.push('/assets/8', overlay) });
  await settle();
  await act(async () => { r.shellNavigate('/?tag=a') });
  await settle();
  expect(log.slice(1), '关掉回到原处').toEqual([]);
  await act(async () => { r.shellNavigate('/assets/7', { state: overlay }) });
  await settle();
  r.shell.writeShell({ state: { ...filters(r), seed: '9' } });
  await act(async () => { r.shellNavigate('/?tag=a') });
  await settle();
  expect(log.slice(1), '压着的时候筛选换了').toEqual(['load']);
  expect(actions.catalog?.release).not.toHaveBeenCalled();
});

it('没有背景的作品地址（深链）：补画一次目录网格，不整页打开；关掉时壳要求重开才整页打开', async () => {
  const r = await load('/assets/7');
  const { actions, log } = shellActions();
  await boot(r, actions);
  expect(log).toEqual(['fill']);
  await act(async () => {
    r.shellNavigate('/');
    r.shell.writeShell({ pageOpens: r.shell.pageOpens + 1 });
  });
  await settle();
  expect(log).toEqual(['fill', 'open /']);
});

it('后退落到压在首页上的详情才挂上（中间去过别的页）：压着时不动，关掉回首页整页打开', async () => {
  const r = await load('/');
  const { actions, log } = shellActions();
  await boot(r, actions);
  const overlay = { backgroundLocation: { pathname: '/', search: '' }, overlay: 'item' };
  await act(async () => { r.shellNavigate('/assets/7', { state: overlay }) });
  await settle();
  await act(async () => { r.peachHistory.push('/stats') });
  await settle();
  expect(log.slice(1)).toEqual(['release']);
  await pop('/assets/7', { usr: overlay, key: 'x', idx: 1 });
  expect(log.slice(2), '压着的时候不开').toEqual([]);
  await act(async () => { r.shellNavigate('/') });
  await settle();
  expect(log.slice(2)).toEqual(['open /']);
});

it('首页上的 `?state=ads` 原地改写成垃圾文件的地址，由垃圾文件那一页整页打开并补标题', async () => {
  const r = await load('/?state=ads&type=video&view=dismissed');
  const { actions, log } = shellActions();
  const length = window.history.length;
  await boot(r, actions);
  expect(window.location.pathname + window.location.search).toBe('/junk-files?type=video&view=dismissed');
  expect(window.history.length, '改写不加条目').toBe(length);
  expect(log).toEqual(['open /junk-files retitle']);
  expect(filters(r).state).toBe('ads');
});

it('地址上的排序：写了列没写方向取那一列的默认方向，两样都没写按浏览偏好，旧键自带方向', async () => {
  const r = await load('/?sort=new');
  const { actions } = shellActions();
  await boot(r, actions);
  expect(filters(r)).toMatchObject({ sort: 'new', dir: 'desc' });
  await pop('/?sort=new&dir=asc');
  expect(filters(r)).toMatchObject({ sort: 'new', dir: 'asc' });
  await pop('/?sort=big');
  expect(filters(r)).toMatchObject({ sort: 'size', dir: 'desc' });
  expect(r.sortFromAddress(null, null, 'new', 'asc')).toEqual({ sort: 'new', dir: 'asc' });
  expect(r.sortFromAddress(null, null, 'seed', 'asc')).toEqual({ sort: 'seed', dir: '' });
  expect(r.sortFromAddress('nope', null, 'rating', 'desc', 'new')).toEqual({ sort: 'new', dir: 'desc' });
});

it('后退前进按地址重读未归属；产地沿用', async () => {
  const r = await load('/?owner=none');
  r.shell.writeShell({ state: { region: 'jp' } });
  const { actions } = shellActions();
  await boot(r, actions);
  expect(filters(r)).toMatchObject({ owner: 'none', region: 'jp' });
  await pop('/');
  expect(filters(r)).toMatchObject({ owner: '', region: 'jp' });
  await pop('/?owner=none');
  expect(filters(r).owner).toBe('none');
});

it('离开目录去管理区收起网格；去资料页、详情与沉浸不收', async () => {
  const r = await load('/');
  const { actions } = shellActions();
  await boot(r, actions);
  await act(async () => { r.shellNavigate('/performers/A') });
  await settle();
  await act(async () => { r.shellNavigate('/immerse') });
  await settle();
  await act(async () => { r.shellNavigate('/assets/3') });
  await settle();
  expect(actions.catalog?.release).not.toHaveBeenCalled();
  await act(async () => { r.shellNavigate('/') });
  await settle();
  await act(async () => { r.shellNavigate('/stats') });
  await settle();
  expect(actions.catalog?.release).toHaveBeenCalledTimes(1);
});

it('三个筛选态与垃圾文件的侧栏键与标签就是 `STATE_ROUTES` 与 `STATE_LABELS` 那一份', async () => {
  const r = await load('/');
  for (const [key, path] of Object.entries(r.core.STATE_ROUTES)) {
    expect(r.routeMetaOf(path)).toEqual({ nav: key, title: r.core.STATE_LABELS[key] });
  }
  expect(r.routeMetaOf('/trash')).toEqual({ section: 'trash' });
  expect(r.routeMetaOf('/')).toEqual({});
});
