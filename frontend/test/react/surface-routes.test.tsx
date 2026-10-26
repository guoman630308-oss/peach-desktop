/* 页面里的页面内组件由路由树画（`SURFACE_ROUTES`）：首页筛选条、首页新作行、目录页处理横幅与顶栏搜索下拉按名字
 * 登记，壳经 `openManagedRoute` 开、`updateManagedRoute` 推、`releaseManagedRoute` 收，各画进自己的容器。
 * 路由树接上之前打开的那一面等它接上再画；首屏取齐才换掉壳铺的骨架；首屏在途时容器已归路由树。
 *
 * 各面自己的内容与时序由 `library-processing.test.tsx`、`search.test.tsx` 等管，这里看的是路由树这一层。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

import type { SearchApi, SearchProps } from '../../src/react/search/search';
import type { ShellActions } from '../../src/react/router/shell-actions';

import { deferredFetch } from '../helpers';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

async function load() {
  vi.resetModules();
  window.history.replaceState(null, '', '/');
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

/** 壳启动时的顺序：先画路由树，再接上取数（`loadRouter`）。 */
async function mount(r: Loaded) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot actions={shellActions()} />) });
  unmounts.push(() => root.unmount());
  await act(async () => { r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute)) });
}

/** 壳铺好骨架的容器。 */
function container(id: string): HTMLElement {
  const el = document.createElement('div');
  el.id = id;
  el.innerHTML = '<div class="geist-skeleton" data-skeleton="cards">正在读取</div>';
  document.body.append(el);
  return el;
}

/** 动态装载与取数中间隔几步不固定；等到条件成立为止。 */
async function until(ok: () => boolean, what: string): Promise<void> {
  for (let step = 0; step < 100; step += 1) {
    if (ok()) return;
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
  throw new Error(`一直没等到：${what}`);
}

/** 顶栏搜索那一份 props：静态输入框、能交出命令的 `expose`，取数全部回空。 */
function searchProps(exposed: { api: SearchApi | null }): SearchProps {
  const input = document.createElement('input');
  document.body.append(input);
  return {
    input, historyLimit: 10,
    actions: { search: vi.fn(), openItem: vi.fn(), openEntity: vi.fn() },
    helpers: {
      pool: async () => [], coverHtml: () => '',
      present: (el) => { el.hidden = false }, dismiss: (el) => { el.hidden = true },
      wireScroller: () => {}, typed: () => {}, composing: () => {}, clearField: (el) => { el.value = '' },
    },
    expose: (api) => { exposed.api = api },
  };
}

it('路由树接上之前打开的搜索下拉等着，不动容器；接上那一刻画进去并交出命令', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ items: [] }) })));
  const r = await load();
  const preload = vi.fn();
  r.preloadManagedRoutes(preload);
  const menu = container('searchMenu');
  const exposed: { api: SearchApi | null } = { api: null };
  const opening = r.openManagedRoute('search', searchProps(exposed), { container: menu, isCurrent: () => true });
  expect(preload, '第一次打开当场发出 React 包的请求，不等 loadRouter').toHaveBeenCalledTimes(1);
  void r.openManagedRoute('library-processing', { toast: vi.fn() }, { container: container('y'), isCurrent: () => true });
  r.releaseManagedRoute(document.getElementById('y')!);
  expect(preload, '装载入口只调一次').toHaveBeenCalledTimes(1);
  await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  expect(menu.querySelector('[data-skeleton]'), '路由树没接上时容器归壳').not.toBeNull();
  expect(r.managedTaken(menu), '等着的那一次已经占了容器，壳不再重开').toBe(true);
  expect(r.managedEntry(menu)).toBeNull();

  await mount(r);
  await act(async () => { expect(await opening).toBe(true) });
  expect(menu.querySelector('[data-skeleton]')).toBeNull();
  expect(menu.querySelector(':scope > .peach-react')).not.toBeNull();
  expect(exposed.api, '下拉画上就把命令交给壳').not.toBeNull();
  expect(r.managedEntry(menu)?.path).toBe('search');
});

it('处理横幅取齐首屏才换掉骨架；首屏在途时容器已归路由树', async () => {
  const fetch = deferredFetch({ status: 'idle' });
  fetch.install();
  const r = await load();
  await mount(r);
  const el = container('libraryProcessing');
  const opening = r.openManagedRoute('library-processing', { toast: vi.fn() }, { container: el, isCurrent: () => true });
  await until(() => fetch.fetched.mock.calls.length > 0, '取数发出去');
  expect(r.managedTaken(el)).toBe(true);
  expect(el.querySelector('[data-skeleton]'), '数据还没回来就撤骨架会出现第二段等待态').not.toBeNull();
  fetch.resolve();
  await act(async () => { await opening });
  expect(el.querySelector('[data-skeleton]')).toBeNull();
  // token、Preflight 与焦点规则都作用在 `.peach-react` 上，页面不画在它里面就没有样式。
  expect(el.querySelector('.peach-react')?.textContent).toContain('同步媒体库');
});

it('取数期间壳已换页就不画，骨架留给下一页', async () => {
  const fetch = deferredFetch({ status: 'idle' });
  fetch.install();
  const r = await load();
  await mount(r);
  const el = container('libraryProcessingNotice');
  let current = true;
  const opening = r.openManagedRoute('library-processing', { toast: vi.fn(), mode: 'notice' },
    { container: el, isCurrent: () => current });
  await until(() => fetch.fetched.mock.calls.length > 0, '取数发出去');
  current = false;
  fetch.resolve();
  await act(async () => { expect(await opening).toBe(false) });
  expect(el.querySelector('.peach-react')).toBeNull();
  expect(el.querySelector('[data-skeleton]')).not.toBeNull();
  expect(r.managedTaken(el)).toBe(false);
});

it('收起时中止在途取数；画过的话连宿主一起撤掉', async () => {
  const fetch = deferredFetch({ status: 'idle' });
  fetch.install();
  const r = await load();
  await mount(r);
  const el = container('libraryProcessingNotice');
  const opening = r.openManagedRoute('library-processing', { toast: vi.fn() }, { container: el, isCurrent: () => true });
  await until(() => fetch.fetched.mock.calls.length > 0, '取数发出去');
  r.releaseManagedRoute(el);
  await act(async () => { expect(await opening).toBe(false) });
  expect(fetch.signal()?.aborted, '离开页面必须真的中止请求').toBe(true);
  expect(el.querySelector('[data-skeleton]'), '还没画过就收起，容器里还是壳的骨架').not.toBeNull();

  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ status: 'idle' }) })));
  await act(async () => {
    await r.openManagedRoute('library-processing', { toast: vi.fn() }, { container: el, isCurrent: () => true });
  });
  expect(r.managedTaken(el)).toBe(true);
  await act(async () => { r.releaseManagedRoute(el) });
  expect(el.querySelector('.peach-react'), '宿主要跟着收起一起走').toBeNull();
  expect(r.managedTaken(el)).toBe(false);
});

it('没画上的容器推补丁是空操作；画上之后就地合进去，不重挂', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ items: [] }) })));
  const r = await load();
  await mount(r);
  const menu = container('searchMenu');
  r.updateManagedRoute(menu, { historyLimit: 3 });
  r.updateManagedRoute(null, { historyLimit: 3 });
  expect(menu.querySelector('[data-skeleton]')).not.toBeNull();

  const exposed: { api: SearchApi | null } = { api: null };
  await act(async () => {
    await r.openManagedRoute('search', searchProps(exposed), { container: menu, isCurrent: () => true });
  });
  const host = menu.querySelector('.peach-react');
  const revision = r.managedEntry(menu)?.revision;
  await act(async () => { r.updateManagedRoute(menu, { historyLimit: 3 }) });
  expect((r.managedEntry(menu)?.props as SearchProps | undefined)?.historyLimit).toBe(3);
  expect(r.managedEntry(menu)?.revision).toBe(revision);
  expect(menu.querySelector('.peach-react'), '就地更新不换宿主').toBe(host);
});
