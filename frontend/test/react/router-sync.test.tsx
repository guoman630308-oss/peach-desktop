/* 路由基座（`src/react/router/router.tsx` 与 `src/history/`）：路由根在历史变化的同一调用里同步提交；没人认领的
 * 历史变化各领一个页面打开序号，按代次挂 key 的元素随之重挂，认领的写地址不重挂；路由元数据覆盖管理区、索引与资料那几页。
 *
 * 历史对象是模块级的，每条用例重新装载 `src/history` 与 `src/react/router`（同 `router.test.tsx`）。 */
import { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { useLocation } from 'react-router';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

const REACT_IMPORT_TIMEOUT_MS = 30_000;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

async function load(path = '/') {
  vi.resetModules();
  window.history.replaceState(null, '', path);
  const [history, router, routes] = await Promise.all([
    import('../../src/history'), import('../../src/react/router/router'), import('../../src/react/router/managed-routes')]);
  return { ...history, ...router, ...routes };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
});

async function mount(r: Loaded, children: React.ReactNode) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot>{children}</r.RouterRoot>) });
  unmounts.push(() => root.unmount());
}

function pop(path?: string) {
  if (path) window.history.replaceState(window.history.state, '', path);
  window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }));
}

/** 页面组 `path="*"` 那一格的探针：记它看到的地址、页面打开序号，和按代次挂 key 的那一格挂了几次。 */
function probe(r: Loaded) {
  const seen = { paths: [] as string[], epochs: [] as number[], mounted: 0, unmounted: 0, keyed: 0 };
  function Keyed() {
    useEffect(() => { seen.keyed += 1 }, []);
    return null;
  }
  function Probe() {
    seen.paths.push(useLocation().pathname);
    const epoch = r.useOpenEpoch();
    seen.epochs.push(epoch);
    useEffect(() => { seen.mounted += 1; return () => { seen.unmounted += 1 } }, []);
    return <Keyed key={epoch} />;
  }
  return { seen, Probe };
}

it('shellNavigate 返回时页面组的匹配已经提交：离开的那一页当场卸掉，回来的那一页当场挂上', async () => {
  const r = await load('/nowhere');
  const { seen, Probe } = probe(r);
  await mount(r, <Probe />);
  const after: number[][] = [];
  await act(async () => {
    r.shellNavigate('/stats');
    after.push([seen.mounted, seen.unmounted]);
    pop('/nowhere');
    after.push([seen.mounted, seen.unmounted]);
    r.shellNavigate('/no/such');
    after.push([seen.mounted, seen.unmounted]);
  });
  expect(after).toEqual([[1, 1], [2, 1], [2, 1]]);
  expect(seen.paths.at(-1)).toBe('/no/such');
});

it('没人认领的历史变化各领一个页面打开序号、按代次挂的元素当场重挂；认领的写地址不领', async () => {
  const r = await load('/nowhere');
  const { seen, Probe } = probe(r);
  await mount(r, <Probe />);
  const keyed: number[] = [];
  const claimed: boolean[] = [];
  await act(async () => {
    r.shellNavigate('/nowhere?id=2', { replace: true });
    keyed.push(seen.keyed);
    claimed.push(r.peachHistory.navigation.claimed);
    r.shellNavigate('/nowhere?id=3');
    keyed.push(seen.keyed);
    claimed.push(r.peachHistory.navigation.claimed);
    r.peachHistory.push('/nowhere?id=4');
    keyed.push(seen.keyed);
    claimed.push(r.peachHistory.navigation.claimed);
    pop();
    keyed.push(seen.keyed);
    claimed.push(r.peachHistory.navigation.claimed);
    r.peachHistory.replace('/nowhere?id=5');
    keyed.push(seen.keyed);
    claimed.push(r.peachHistory.navigation.claimed);
  });
  expect(keyed).toEqual([1, 1, 2, 3, 4]);
  expect(claimed).toEqual([true, true, false, false, false]);
  expect(seen.mounted).toBe(1);
  expect(new Set(seen.epochs).size).toBe(4);
});

it('路由元数据登记的就是管理区、播放列表与关注、索引、资料与目录那几页，加上改写过去的旧直达地址、沉浸与覆盖的六条', async () => {
  const r = await load();
  expect(new Set(Object.keys(r.ROUTE_META))).toEqual(new Set([
    ...Object.keys(r.MANAGED_ROUTES), ...Object.keys(r.REDIRECT_ROUTES), ...Object.keys(r.BROWSE_ROUTES),
    ...Object.keys(r.INDEX_ROUTES), ...Object.keys(r.ENTITY_ROUTES), ...r.CATALOG_PATHS,
    ...Object.keys(r.IMMERSE_ROUTES), ...r.OVERLAY_PATHS, ...r.PREFERENCE_PATHS]));
});
