/* 详情覆盖的路由元素（`src/react/router/pages/overlay.tsx`）：覆盖组按真实地址匹配六条覆盖路径，没人认领的历史变化
 * 落上来就接上条目记的背景、交壳打开那一条（`ShellActions.openOverlay`），认领写地址的打开请求也交元素；去处不是覆盖地址时收起舞台。
 * 页面组那一侧按背景匹配，背景页不重挂；启动那一条不读背景，作品与队列补画目录网格，关注详情只让出 `#stats`。
 *
 * 壳怎么取数、怎么画详情由舞台岛与 e2e 管；这里看的是什么时候开、开哪一条、背景接没接上。历史对象与壳状态都是
 * 模块级的，每条用例重新装载。 */
import { act, useEffect } from 'react';
import { flushSync } from 'react-dom';
import { createRoot } from 'react-dom/client';
import { useLocation } from 'react-router';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

import type { OverlayTarget } from '../../../src/history';
import type { ShellActions } from '../../../src/react/router/shell-actions';

const REACT_IMPORT_TIMEOUT_MS = 30_000;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => { await import('../../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

/* `entry` 是装载时那一条历史条目（`{ usr, key, idx }`，刷新后条目照旧带着）。 */
async function load(path: string, entry: unknown = null) {
  vi.resetModules();
  window.history.replaceState(entry, '', path);
  const [history, router, shell] = await Promise.all([
    import('../../../src/history'), import('../../../src/react/router/router'), import('../../../src/shell'),
  ]);
  return { ...history, ...router, shell };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

/** 壳交进来的那一组：打开记下目标与那一刻取到的来处，其余是空桩。 */
function shellActions(r: Loaded) {
  const opened: Array<{ target: OverlayTarget; from: string | null }> = [];
  let bootOpened = false;
  const actions = {
    openOverlay: vi.fn((target: OverlayTarget) => {
      if ((!r.bootEntry() || bootOpened) && r.peachHistory.navigation.claimed) return;
      bootOpened ||= r.bootEntry();
      opened.push({ target, from: r.takeOverlayReturn() });
    }),
    closeStage: vi.fn(), openImmerse: vi.fn(), surfaceChanged: vi.fn(),
    catalog: { defaultLoc: () => 'local', open: vi.fn(), load: vi.fn(), release: vi.fn(), fill: vi.fn() },
    follow: { refresh: vi.fn(() => false), skeleton: vi.fn(() => ''), props: vi.fn(), ground: vi.fn() },
  } as unknown as ShellActions;
  return { actions, opened };
}

/** 页面组认不出的地址那一格的探针：记挂载、卸载与它看到的地址。 */
function probe() {
  const seen = { mounted: 0, unmounted: 0, paths: [] as string[] };
  function Probe() {
    seen.paths.push(useLocation().pathname);
    useEffect(() => { seen.mounted += 1; return () => { seen.unmounted += 1 } }, []);
    return null;
  }
  return { seen, Probe };
}

async function settle() {
  for (let step = 0; step < 3; step += 1) {
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
}

async function mount(r: Loaded, actions: ShellActions, children?: React.ReactNode) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot actions={actions}>{children}</r.RouterRoot>) });
  unmounts.push(() => root.unmount());
  await settle();
}

/** 像壳那样开始路由：`pageOpens` 写成 1。 */
async function start(r: Loaded) {
  await act(async () => { r.shell.writeShell({ pageOpens: 1 }) });
  await settle();
}

/** 浏览器的后退前进：换好地址与条目再报 `popstate`；不给地址就是同一条目重放。 */
async function pop(path?: string, entry?: unknown) {
  await act(async () => {
    if (path) window.history.replaceState(entry === undefined ? window.history.state : entry, '', path);
    window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }));
  });
  await settle();
}

const over = (pathname: string, overlay: 'item' | 'follow' = 'item', search = '') =>
  ({ backgroundLocation: { pathname, search }, overlay });
const entry = (state: unknown, key = 'x') => ({ usr: state, key, idx: 1 });

it('启动覆盖元素在应用动作就绪后打开，离开详情用当前动作关闭舞台', async () => {
  const r = await load('/assets/7');
  const { actions, opened } = shellActions(r);
  const root = createRoot(document.createElement('div'));
  unmounts.push(() => root.unmount());
  await act(async () => { root.render(<r.RouterRoot />) });
  await settle();
  expect(actions.openOverlay).not.toHaveBeenCalled();
  await act(async () => { root.render(<r.RouterRoot actions={actions} />) });
  await start(r);
  expect(opened.map(one => one.target)).toEqual([{ kind: 'item', id: 7 }]);
  await act(async () => { r.peachHistory.push('/performers/name') });
  await settle();
  expect(actions.closeStage).toHaveBeenCalledTimes(1);
});

it('后退前进落到覆盖地址上接上背景并打开；没有请求的认领条目只同步地址；关掉时背景页不重挂', async () => {
  const r = await load('/nowhere');
  const { actions, opened } = shellActions(r);
  const { seen, Probe } = probe();
  await mount(r, actions, <Probe />);
  await start(r);
  await act(async () => { r.shellNavigate('/assets/7', { state: over('/nowhere') }) });
  await settle();
  expect(opened, '认领条目没有打开请求时不会重开舞台').toEqual([]);
  await pop('/assets/8', entry(over('/nowhere', 'item', '?q=1'), 'a'));
  await pop('/assets/4?mix=3', entry(over('/nowhere'), 'b'));
  await pop('/posts/5', entry(over('/nowhere', 'follow'), 'c'));
  await pop('/assets/9?playlist=2', entry(over('/nowhere'), 'd'));
  expect(opened).toEqual([
    { target: { kind: 'item', id: 8 }, from: '/nowhere?q=1' },
    { target: { kind: 'queue', queue: 'mix', key: 3, item: 4 }, from: '/nowhere' },
    { target: { kind: 'follow', id: 5 }, from: '/nowhere' },
    { target: { kind: 'queue', queue: 'playlist', key: 2, item: 9 }, from: '/nowhere' },
  ]);
  expect(actions.closeStage, '覆盖之间换来换去不收舞台').not.toHaveBeenCalled();
  await act(async () => { r.shellNavigate('/nowhere') });
  await settle();
  expect(actions.closeStage).toHaveBeenCalledTimes(1);
  expect([seen.mounted, seen.unmounted], '背景页从头到尾是同一个实例').toEqual([1, 0]);
  expect(new Set(seen.paths)).toEqual(new Set(['/nowhere']));
});

it('`history.state` 为空的同一条目重放：领一个新代次，重开同一条，不接背景', async () => {
  const r = await load('/nowhere');
  const { actions, opened } = shellActions(r);
  await mount(r, actions);
  await start(r);
  await act(async () => { r.shellNavigate('/assets/7', { state: over('/nowhere') }) });
  await settle();
  await pop('/assets/7', null);
  await pop();
  expect(opened).toEqual([
    { target: { kind: 'item', id: 7 }, from: null },
    { target: { kind: 'item', id: 7 }, from: null },
  ]);
});

it('点卡的认领导航交覆盖元素打开：同一路径再开一次也重新交付，背景页保留原节点', async () => {
  const r = await load('/nowhere');
  const { actions } = shellActions(r);
  const { seen, Probe } = probe();
  await mount(r, actions, <Probe />);
  await start(r);
  const presented: OverlayTarget[] = [];
  vi.mocked(actions.openOverlay).mockImplementation((target) => { presented.push(target) });
  await act(async () => { r.shellNavigate('/assets/7', { state: over('/nowhere') }) });
  await settle();
  await act(async () => { r.shellNavigate('/assets/7', { state: over('/nowhere') }) });
  await settle();
  expect(presented).toEqual([{ kind: 'item', id: 7 }, { kind: 'item', id: 7 }]);
  expect([seen.mounted, seen.unmounted]).toEqual([1, 0]);
});

it('深链 `/assets/:id`：壳开始路由之前只挂着，那一刻打开那一条、补画目录网格；启动那一条不读背景', async () => {
  const r = await load('/assets/7', entry(over('/stats')));
  const { actions, opened } = shellActions(r);
  await mount(r, actions);
  expect(opened, '壳开始路由之前不开').toEqual([]);
  await start(r);
  expect(opened).toEqual([{ target: { kind: 'item', id: 7 }, from: null }]);
  expect(actions.catalog!.fill).toHaveBeenCalledTimes(1);
  expect(actions.follow!.ground).not.toHaveBeenCalled();
  expect(actions.surfaceChanged, '条目里记的背景页不打开').not.toHaveBeenCalled();
  await act(async () => { r.shellNavigate('/assets/7?t=12', { replace: true }) });
  await settle();
  expect(opened).toHaveLength(1);
});

it('深链 `/posts/:id`：打开那一条，只让出 `#stats`、不挂列表也不补画目录', async () => {
  const r = await load('/posts/5');
  const { actions, opened } = shellActions(r);
  await mount(r, actions);
  await start(r);
  expect(opened).toEqual([{ target: { kind: 'follow', id: 5 }, from: null }]);
  expect(actions.follow!.ground).toHaveBeenCalledTimes(1);
  expect(actions.follow!.refresh).not.toHaveBeenCalled();
  expect(actions.catalog!.fill).not.toHaveBeenCalled();
});

it('路由根晚于壳开始路由才挂上：挂上就打开启动那一条，只开一次', async () => {
  const r = await load('/assets/4?parts=3');
  const { actions, opened } = shellActions(r);
  r.shell.writeShell({ pageOpens: 1 });
  await mount(r, actions);
  await settle();
  expect(opened).toEqual([{ target: { kind: 'queue', queue: 'parts', key: 3, item: 4 }, from: null }]);
});

it('参数只认数字：`/assets/abc` 什么都不开，下面也不补画', async () => {
  const r = await load('/assets/abc');
  const { actions, opened } = shellActions(r);
  await mount(r, actions);
  await start(r);
  expect(opened).toEqual([]);
  expect(actions.catalog!.fill).not.toHaveBeenCalled();
  expect(r.overlayTarget('/assets/2?editions=1')).toEqual({ kind: 'queue', queue: 'editions', key: 1, item: 2 });
  expect(r.overlayTarget('/assets/7/x')).toBeNull();
  expect(r.overlayTarget('/assets/b?playlist=1')).toBeNull();
  expect(r.overlayTarget('/assets/')).toBeNull();
});

it('打开跑在提交阶段之外：壳在里面用 flushSync 画别的岛，当场画上、不报告警', async () => {
  const errors = vi.spyOn(console, 'error');
  const r = await load('/nowhere');
  const { actions } = shellActions(r);
  const host = document.createElement('div');
  const other = createRoot(host);
  unmounts.push(() => other.unmount());
  const painted: string[] = [];
  vi.mocked(actions.openOverlay).mockImplementation(() => {
    flushSync(() => other.render(<i>{location.pathname}</i>));
    painted.push(host.textContent ?? '');
  });
  await mount(r, actions);
  await start(r);
  await pop('/assets/9', entry(over('/nowhere')));
  expect(painted).toEqual(['/assets/9']);
  expect(errors).not.toHaveBeenCalled();
});

it('队列意图由路由树接手：壳未就绪时等待，取齐前地址与历史深度保持', async () => {
  const r = await load('/nowhere');
  const { actions } = shellActions(r);
  actions.openQueueRequest = vi.fn();
  await mount(r, actions);
  const depth = history.length;
  await act(async () => { r.shell.writeShell({ queueOpens: 1 }) });
  await settle();
  expect(actions.openQueueRequest).not.toHaveBeenCalled();
  await start(r);
  expect(actions.openQueueRequest).toHaveBeenCalledTimes(1);
  expect([location.pathname, history.length]).toEqual(['/nowhere', depth]);
  await act(async () => { r.shell.writeShell({ queueOpens: 2 }) });
  await settle();
  expect(actions.openQueueRequest).toHaveBeenCalledTimes(2);
});

it('沉浸从顶栏的认领导航打开，换条replace不重开，后退离开地址时保留关闭键的行为', async () => {
  const r = await load('/nowhere');
  const { actions } = shellActions(r);
  await mount(r, actions);
  await start(r);
  await act(async () => { r.shellNavigate('/immerse') });
  await settle();
  expect(actions.openImmerse).toHaveBeenCalledTimes(1);
  const depth = history.length;
  await act(async () => { r.shellNavigate('/immerse?id=8', { replace: true }) });
  await settle();
  expect(actions.openImmerse).toHaveBeenCalledTimes(1);
  expect(history.length).toBe(depth);
  await pop('/nowhere', null);
  expect(actions.closeStage).not.toHaveBeenCalled();
});
