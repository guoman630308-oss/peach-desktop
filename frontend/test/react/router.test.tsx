/* 客户端导航（`src/react/router/`）：两组 `<Routes>` 怎么匹配。页面组按条目记的背景匹配（启动那一条不读），
 * 覆盖组按真实地址匹配；`path="*"` 是认不出的地址，那一格在认不出的地址之间从头到尾是同一个实例。没人认领的
 * 历史变化（后退前进、React 子树里的 `navigate`）各领一个页面打开序号，壳认领写的不领。各页什么时候打开见
 * `pages/` 各自的用例，详情覆盖见 `pages/overlay.test.tsx`，管理区那几页的宿主见 `managed-routes.test.tsx`。
 *
 * 历史对象是模块级的，和页面上只有一份一致，所以每条用例重新装载 `src/history` 与 `src/react/router`。
 * React 与 React Router 在 node_modules 里，不随之重载，组件照常写 JSX。 */
import { act, useEffect, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { useLocation, useNavigate } from 'react-router';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

/* `path` 与 `entry` 是装载时的地址与那一条历史条目（`{ usr, key, idx }`，刷新后条目照旧带着）。 */
async function load(path = '/nowhere', entry: unknown = null) {
  vi.resetModules();
  window.history.replaceState(entry, '', path);
  const [history, router] = await Promise.all([import('../../src/history'), import('../../src/react/router/router')]);
  return { ...history, ...router };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
});

async function mount(r: Loaded, children?: ReactNode) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot>{children}</r.RouterRoot>) });
  unmounts.push(() => root.unmount());
}

/** 浏览器自己的后退前进：地址先变，再派发 `popstate`；不给地址就是同一条目重放。 */
function pop(path?: string) {
  if (path) window.history.replaceState(window.history.state, '', path);
  window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }));
}

/* 详情与队列条目的 `usr`：压在哪一页上，和壳写的形状一致。 */
const over = (pathname: string, overlay: 'item' | 'follow' = 'item', search = '') =>
  ({ backgroundLocation: { pathname, search }, overlay });

/** 页面组 `path="*"` 那一格的探针：记挂载、卸载与它看到的地址。 */
function probe() {
  const seen = { mounted: 0, unmounted: 0, paths: [] as string[] };
  function Probe() {
    seen.paths.push(useLocation().pathname);
    useEffect(() => { seen.mounted += 1; return () => { seen.unmounted += 1 } }, []);
    return null;
  }
  return { seen, Probe };
}

it('认不出的地址之间换来换去，页面组 path="*" 那一格是同一个实例', async () => {
  const r = await load('/nowhere');
  const { seen, Probe } = probe();
  await mount(r, <Probe />);
  await act(async () => { r.shellNavigate('/also/nowhere') });
  await act(async () => { pop('/x/y') });
  await act(async () => { pop() });
  expect([seen.mounted, seen.unmounted]).toEqual([1, 0]);
  expect(seen.paths.at(-1)).toBe('/x/y');
});

it('详情压着下面那一页时，页面组的元素不重挂，看到的是背景那一页', async () => {
  const r = await load('/nowhere');
  const { seen, Probe } = probe();
  await mount(r, <Probe />);
  const steps: Array<[string, ReturnType<typeof over>]> = [
    ['/posts/3', over('/nowhere', 'follow')], ['/assets/7', over('/nowhere')], ['/assets/2?parts=1', over('/nowhere')]];
  for (const [path, state] of steps) await act(async () => { r.shellNavigate(path, { state }) });
  await act(async () => { pop('/posts/3') });
  await act(async () => { r.peachHistory.push('/assets/5?mix=4', over('/nowhere')) });
  expect([seen.mounted, seen.unmounted]).toEqual([1, 0]);
  expect(new Set(seen.paths)).toEqual(new Set(['/nowhere']));
});

it('页面组按背景匹配：背景是管理区那一页就匹配那一条；没有背景的覆盖地址挂自己那一格；两组都不报没有路由', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  try {
    const r = await load('/nowhere');
    const { seen, Probe } = probe();
    await mount(r, <Probe />);
    await act(async () => { r.shellNavigate('/assets/7', { state: over('/stats', 'item', '?range=30') }) });
    expect([seen.mounted, seen.unmounted]).toEqual([1, 1]);
    await act(async () => { r.shellNavigate('/assets/8') });
    expect(seen.mounted, '没有背景的作品地址不落到认不出的那一格').toBe(1);
    await act(async () => { r.shellNavigate('/no/such/page') });
    expect(seen.mounted).toBe(2);
    const unmatched = warn.mock.calls.map((call) => String(call[0])).filter((text) => text.includes('No routes matched'));
    expect(unmatched).toEqual([]);
  } finally {
    warn.mockRestore();
  }
});

it('启动那一条不读背景：刷新落在带背景的详情条目上时当作没有背景；后退前进回到这一条才读', async () => {
  const r = await load('/assets/7', { usr: over('/nowhere'), key: 'boot', idx: 0 });
  const { seen, Probe } = probe();
  await mount(r, <Probe />);
  expect(r.navigationBackground()).toBeNull();
  expect(seen.mounted, '启动那一条照没有背景，作品地址挂自己那一格').toBe(0);
  await act(async () => { pop() });
  expect(r.navigationBackground()).toEqual({ pathname: '/nowhere', search: '' });
  expect(seen.mounted, '后退前进回到这一条，按背景匹配').toBe(1);
});

it('壳认领写的地址不领页面打开序号；后退前进与 React 子树里的 navigate 各领一个，地址没变的 popstate 也算', async () => {
  const r = await load('/nowhere');
  let navigate: ReturnType<typeof useNavigate> | null = null;
  function Probe() { navigate = useNavigate(); return null }
  await mount(r, <Probe />);
  const epoch = () => r.peachHistory.navigation.openEpoch;
  const start = epoch();
  await act(async () => { r.shellNavigate('/stats') });
  await act(async () => { r.shellNavigate('/taste', { replace: true }) });
  expect([epoch() - start, r.peachHistory.navigation.claimed]).toEqual([0, true]);
  await act(async () => { pop('/nowhere') });
  await act(async () => { pop() });
  expect(epoch() - start).toBe(2);
  await act(async () => { void navigate!('/performers') });
  expect([epoch() - start, r.peachHistory.navigation.claimed, location.pathname]).toEqual([3, false, '/performers']);
});
