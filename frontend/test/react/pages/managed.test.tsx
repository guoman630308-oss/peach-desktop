/* 管理区的页面元素（`src/react/router/pages/managed.tsx`）：页面组按匹配挂上它就打开那一页。先报换页，
 * 再铺骨架（与壳冷启动铺的同键就不重画）、取首屏；没人认领的历史变化一次就是打开一次，认领的写地址与详情压上来都不重开；
 * 卸载时这一页还露在 `#stats` 上才收；`/resource-sync` 与配置页的 `#libraryProcessing` 改写地址、不加历史条目。
 *
 * 历史对象与壳的内存状态都是模块级的，每条用例重新装载。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

import type { ShellActions } from '../../../src/react/router/shell-actions';

const REACT_IMPORT_TIMEOUT_MS = 30_000;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => { await import('../../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

async function load(path = '/') {
  vi.resetModules();
  window.history.replaceState(null, '', path);
  const [history, router, routes, shell, placeholder] = await Promise.all([
    import('../../../src/history'), import('../../../src/react/router/router'),
    import('../../../src/react/router/managed-routes'), import('../../../src/shell'),
    import('../../../src/management-placeholder'),
  ]);
  // 壳的内存状态是活绑定：整份模块留着读，展开出来的只是装载那一刻的值。
  return { ...history, ...router, ...routes, ...placeholder, shell };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function shellActions(): ShellActions {
  return {
    openItem: vi.fn(), openEntity: vi.fn(), openTag: vi.fn(), openTasteSignal: vi.fn(), navigate: vi.fn(),
    openManage: vi.fn(), managePath: vi.fn(() => ''), openFollow: vi.fn(),
    receipt: vi.fn(), toast: vi.fn(), failure: vi.fn(), revealSource: vi.fn(async () => ''),
    reopenTutorial: vi.fn(async () => {}), requestConfigurationSection: vi.fn(),
    routeReview: vi.fn(), routeFollowManage: vi.fn(), saveFollowPreference: vi.fn(), srcBadge: () => '',
    routeIndex: vi.fn(), savePeopleLayout: vi.fn(), exitSelectMode: vi.fn(),
    personAvatar: vi.fn(() => ({ html: '', face: '' })), authorAvatar: vi.fn(() => ''), showIndexTags: vi.fn(),
    openFollowAuthor: vi.fn(), openFollowTag: vi.fn(), openPlaylist: vi.fn(), canFlip: () => true,
    surfaceChanged: vi.fn(), clearSearch: vi.fn(), openImmerse: vi.fn(), openOverlay: vi.fn(), closeStage: vi.fn(), grid: {} as ShellActions['grid'],
  };
}

/** 挂上路由树、接上页面包，再像壳那样开始路由：`pageOpens` 写成 1。 */
async function boot(r: Loaded, actions = shellActions()) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot actions={actions} />) });
  unmounts.push(() => root.unmount());
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  await act(async () => { r.shell.writeShell({ pageOpens: 1 }) });
  return { actions };
}

/** 管理区正文的容器，铺着壳冷启动时那一张骨架。 */
function surface(r: Loaded, path: string) {
  const stats = document.createElement('section');
  stats.id = 'stats';
  stats.innerHTML = r.managementSkeletonHtml(path, { followLayout: 'default', followSort: '', followDir: '' });
  document.body.append(stats);
  return { stats, skeleton: stats.firstElementChild! };
}

const tasks = (label = '追更检查') => ({
  available: true, running: [], skipped: [],
  finished: [{
    id: 1, task_key: 'follow-check', task_label: label, trigger: 'manual', status: 'succeeded', host: 'desk',
    started_at: '2026-09-11T10:00:00Z', finished_at: '2026-09-11T10:00:05Z', elapsed_seconds: 5,
    progress_current: null, progress_total: null, progress_label: '', result_summary: {}, error: '',
    parent_run_id: null, root_run_id: null, followup_key: '', followup_depth: 0,
  }],
});
const configuration = {
  editable: true, notice: '', revision: 'rev-1', media_dirs: ['D:\\Media'], port: 9123, facts: [],
  startup: { available: true, enabled: false, silent: true, message: '', desktop: false, desktop_message: '' },
  peach_proxy: { mode: 'environment', proxy_saved: false, needs_selection: false },
};
type Reply = { ok: boolean; status: number; json(): Promise<unknown> };
const reply = (body: unknown): Reply => ({ ok: true, status: 200, json: async () => body });

/** 按路径回的取数替身：`deferred` 里的路径等手动兑现，表里没有的一直不回（中止时拒绝）。 */
function serve(bodies: Record<string, unknown>, deferred: string[] = []) {
  const pending: Array<() => void> = [];
  const signals = new Map<string, AbortSignal>();
  const fetched = vi.fn((input: string, init?: RequestInit) => {
    const path = input.split('?')[0]!;
    if (init?.signal) signals.set(path, init.signal);
    return new Promise<Reply>((resolve, reject) => {
      init?.signal?.addEventListener('abort', () => { reject(new DOMException('已中止', 'AbortError')) });
      if (!Object.hasOwn(bodies, path)) return;
      if (deferred.includes(path)) pending.push(() => resolve(reply(bodies[path])));
      else resolve(reply(bodies[path]));
    });
  });
  vi.stubGlobal('fetch', fetched);
  const calls = (path: string) => fetched.mock.calls.filter(([input]) => input.split('?')[0] === path).length;
  return { fetched, calls, signal: (path: string) => signals.get(path), resolve: () => { for (const settle of pending.splice(0)) settle() } };
}

async function until(ok: () => boolean, what: string): Promise<void> {
  for (let step = 0; step < 200; step += 1) {
    if (ok()) return;
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
  throw new Error(`一直没等到：${what}`);
}
const settle = () => act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });

/** 页面画出来的那一块（宿主里的第一个元素）：重挂之后就换成另一个。 */
const painted = (stats: Element) => stats.querySelector(':scope > .peach-react > *');

it('挂上就打开：先报换页，骨架与冷启动那张同键不重画，首屏取齐才换成整页', async () => {
  const r = await load('/tasks');
  const { stats, skeleton } = surface(r, '/tasks');
  const order: string[] = [];
  const actions = shellActions();
  vi.mocked(actions.surfaceChanged).mockImplementation((kind, path) => { order.push(`${kind} ${path}`) });
  const api = serve({ '/api/tasks': tasks(), '/api/downloads': { available: true, providers: [], tasks: [] } }, ['/api/tasks']);
  api.fetched.mockImplementationOnce(((original) => (input: string, init?: RequestInit) => {
    order.push(`fetch ${input.split('?')[0]}`);
    return original(input, init);
  })(api.fetched.getMockImplementation()!));
  await boot(r, actions);
  await until(() => api.calls('/api/tasks') > 0, '首屏取数发出去');
  expect(order).toEqual(['management /tasks', 'fetch /api/tasks']);
  expect(stats.firstElementChild, '同一张骨架原样留着').toBe(skeleton);
  api.resolve();
  await until(() => painted(stats) !== null, '整页画上');
  expect(stats.firstElementChild).not.toBe(skeleton);
  expect(stats.textContent).toContain('追更检查');
});

it('没人认领的写地址一次就是打开一次：认领的写地址不重开，同一路径不认领再写就重取、重挂', async () => {
  const r = await load('/tasks');
  const { stats } = surface(r, '/tasks');
  const api = serve({ '/api/tasks': tasks(), '/api/downloads': { available: true, providers: [], tasks: [] } });
  await boot(r);
  await until(() => painted(stats) !== null, '第一次画上');
  const page = painted(stats);
  const revision = r.managedEntry(stats)?.revision;
  await act(async () => { r.shellNavigate('/tasks?from=menu', { replace: true }) });
  await settle();
  expect([api.calls('/api/tasks'), painted(stats)]).toEqual([1, page]);
  const length = window.history.length;
  await act(async () => { r.peachHistory.push('/tasks') });
  await until(() => painted(stats) !== null && painted(stats) !== page, '重挂之后再画上');
  expect(api.calls('/api/tasks')).toBe(2);
  expect(r.managedEntry(stats)?.revision).not.toBe(revision);
  expect(window.history.length - length).toBe(1);
});

it('详情压在上面时页面不拆也不重开，关掉详情回到这一页照旧', async () => {
  const r = await load('/tasks');
  const { stats } = surface(r, '/tasks');
  const api = serve({ '/api/tasks': tasks(), '/api/downloads': { available: true, providers: [], tasks: [] } });
  await boot(r);
  await until(() => painted(stats) !== null, '画上');
  const page = painted(stats);
  const overlay = { backgroundLocation: { pathname: '/tasks', search: '' }, overlay: 'item' };
  await act(async () => { r.shellNavigate('/assets/7', { state: overlay }) });
  await settle();
  expect(painted(stats), '舞台下面那一页留着').toBe(page);
  await act(async () => { r.shellNavigate('/tasks') });
  await settle();
  expect([painted(stats), api.calls('/api/tasks')]).toEqual([page, 1]);
});

it('后退落到压在这一页上的详情才挂上（中间去过别的页）时不开；关掉详情回来按地址打开一次', async () => {
  const r = await load('/tasks');
  const { stats } = surface(r, '/tasks');
  const api = serve({ '/api/tasks': tasks(), '/api/downloads': { available: true, providers: [], tasks: [] } });
  await boot(r);
  await until(() => painted(stats) !== null, '画上');
  const overlay = { backgroundLocation: { pathname: '/tasks', search: '' }, overlay: 'item' };
  await act(async () => { r.shellNavigate('/assets/7', { state: overlay }) });
  await settle();
  await act(async () => { r.shellNavigate('/'); stats.replaceChildren() });
  await settle();
  await act(async () => {
    window.history.replaceState({ usr: overlay, key: 'x', idx: 1 }, '', '/assets/7');
    window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }));
  });
  await settle();
  expect([painted(stats), api.calls('/api/tasks')], '被详情压着挂上不开').toEqual([null, 1]);
  await act(async () => { r.shellNavigate('/tasks') });
  await until(() => painted(stats) !== null, '关掉详情后画上');
  expect(api.calls('/api/tasks')).toBe(2);
});

it('走开时还露在 `#stats` 上就收起，收起排在提交之后、不报同步提交的告警；壳藏起了它就留着', async () => {
  const errors = vi.spyOn(console, 'error');
  const r = await load('/tasks');
  const { stats } = surface(r, '/tasks');
  serve({ '/api/tasks': tasks(), '/api/downloads': { available: true, providers: [], tasks: [] } });
  await boot(r);
  await until(() => painted(stats) !== null, '画上');
  await act(async () => { r.shellNavigate('/') });
  await settle();
  expect([stats.querySelector('.peach-react'), r.managedEntry(stats)]).toEqual([null, null]);
  expect(errors).not.toHaveBeenCalled();

  await act(async () => { r.peachHistory.push('/tasks') });
  await until(() => painted(stats) !== null, '再开一次画上');
  const page = painted(stats);
  // 资料页压过来：壳在同一个任务里藏起 `#stats`，页面藏着照常活，等下一次认领表面才收。
  await act(async () => { r.shellNavigate('/performers/someone'); stats.hidden = true });
  await settle();
  expect(painted(stats)).toBe(page);
  expect(errors).not.toHaveBeenCalled();
});

it('等 `/healthz` 期间走开：读请求中止，这一页不再打开', async () => {
  const r = await load('/review?category=creator_tags');
  surface(r, '/review');
  const api = serve({ '/healthz': {} }, ['/healthz']);
  await boot(r);
  await until(() => api.calls('/healthz') > 0, '问只读态');
  await act(async () => { r.shellNavigate('/') });
  await settle();
  expect(api.signal('/healthz')?.aborted).toBe(true);
  api.resolve();
  await settle();
  expect(api.fetched.mock.calls.map(([input]) => input.split('?')[0])).toEqual(['/healthz']);
});

it('口味页打开时清掉目录筛选与搜索框，引导标记交出一次就清', async () => {
  const r = await load('/taste');
  r.shell.writeShell({
    state: { creator: 'a', studio: 'b', tag: 'c', tag_match: 'any', q: 'd', jav: '1', sort: 'seed' } as never,
    cameFromSetup: true,
  });
  surface(r, '/taste');
  serve({});
  const { actions } = await boot(r);
  await until(() => vi.mocked(actions.clearSearch).mock.calls.length > 0, '清搜索框');
  expect(r.shell.state).toMatchObject({
    creator: '', studio: '', tag: '', tag_match: 'all', len: '', dur_min: '', dur_max: '', orient: '', region: '',
    state: '', q: '', jav: '', sort: 'seed',
  });
  expect(r.shell.cameFromSetup).toBe(false);
});

it('旧直达地址 `/resource-sync` 改写成数据管理页的锚点，不加历史条目，由数据管理页的元素打开', async () => {
  const r = await load('/resource-sync');
  surface(r, '/resource-sync');
  serve({});
  const length = window.history.length;
  const { actions } = await boot(r);
  await until(() => location.pathname === '/data-cleanup', '改写到数据管理');
  await until(() => vi.mocked(actions.surfaceChanged).mock.calls.length > 0, '数据管理页打开');
  expect([location.hash, window.history.length - length]).toEqual(['#resource-sync', 0]);
  expect(vi.mocked(actions.surfaceChanged).mock.calls).toEqual([['management', '/data-cleanup']]);
});

it('配置页带 `#libraryProcessing` 进来：画上之后改写到数据管理页的那一块，不加历史条目', async () => {
  const r = await load('/settings/library#libraryProcessing');
  const { stats } = surface(r, '/settings/library');
  serve({ '/api/configuration': configuration });
  const length = window.history.length;
  const { actions } = await boot(r);
  await until(() => vi.mocked(actions.surfaceChanged).mock.calls.length > 1, '数据管理页打开');
  expect([location.pathname, location.hash, window.history.length - length]).toEqual(['/data-cleanup', '#libraryProcessing', 0]);
  expect(vi.mocked(actions.surfaceChanged).mock.calls.map(([, path]) => path)).toEqual(['/settings/library', '/data-cleanup']);
  expect(stats.hidden).toBe(false);
});

it('诊断页的标题、管理区身份与换一批由路由元数据登记', async () => {
  const r = await load();
  expect(r.routeMetaOf('/settings/diagnostics')).toEqual({ section: 'configuration', title: '系统诊断', refresh: 'reopen' });
});
