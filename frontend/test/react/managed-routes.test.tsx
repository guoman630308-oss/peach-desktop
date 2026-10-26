/* 由路由画的那几页（`src/history/managed.ts` 与 `src/react/router/`；管理区画进 `#stats`，索引页画进
 * `#index`）：壳的骨架留到首屏取齐，换成整页落在同一个任务里；冷启动不论 Router 先挂还是先打开都只取一次；
 * 每次打开都重取，壳的开关就地合进去不重挂；两个容器互不相收；详情舞台压在上面时页面留着，壳下一次认领
 * 表面才收；管理区几页之间的跳转走 React Router，别的交壳。
 *
 * 历史对象与各容器的登记都是模块级的，和页面上只有一份一致，所以每条用例重新装载
 * `src/history` 与 `src/react/router`。 */
import { act, type ReactElement } from 'react';
import { createRoot } from 'react-dom/client';
import type { NavigateFunction } from 'react-router';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

import type {
  ConfigurationProps, DataCleanupProps, DuplicatesProps, FollowManageProps, QualityGoalsProps, ReviewProps,
  ScrapingProps, StatsProps, TasteProps,
} from '../../src/react/bundle';
import type { ShellActions } from '../../src/react/router/shell-actions';
import type { DiagnosticsProps } from '../../src/react/diagnostics/diagnostics-page';

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
  const sections: Record<string, string> = { review: '/review', quality: '/quality-goals', trash: '/trash' };
  return {
    openItem: vi.fn(), openEntity: vi.fn(), openTag: vi.fn(), openTasteSignal: vi.fn(), navigate: vi.fn(),
    openManage: vi.fn(), managePath: vi.fn((section: string) => sections[section] ?? ''), openFollow: vi.fn(),
    receipt: vi.fn(), toast: vi.fn(), failure: vi.fn(), revealSource: vi.fn(async () => ''),
    reopenTutorial: vi.fn(async () => {}), requestConfigurationSection: vi.fn(),
    routeReview: vi.fn(), routeFollowManage: vi.fn(), saveFollowPreference: vi.fn(), srcBadge: () => '',
    routeIndex: vi.fn(), savePeopleLayout: vi.fn(), exitSelectMode: vi.fn(),
    personAvatar: vi.fn(() => ({ html: '', face: '' })), authorAvatar: vi.fn(() => ''), showIndexTags: vi.fn(),
    openFollowAuthor: vi.fn(), openFollowTag: vi.fn(), openPlaylist: vi.fn(), canFlip: () => true,
    surfaceChanged: vi.fn(), clearSearch: vi.fn(), openImmerse: vi.fn(), openOverlay: vi.fn(), closeStage: vi.fn(), grid: {} as ShellActions['grid'],
  };
}

async function mount(r: Loaded, actions = shellActions()) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot actions={actions} />) });
  unmounts.push(() => root.unmount());
  return actions;
}

/** 管理区正文的容器，里面是壳铺好的骨架。 */
function surface() {
  const stats = document.createElement('section');
  stats.id = 'stats';
  stats.innerHTML = '<div class="geist-skeleton" data-skeleton="activity">正在读取</div>';
  document.body.append(stats);
  return { stats, skeleton: stats.firstElementChild! };
}

/** 活动页首屏那一份 `/api/tasks`：只有一轮刚跑完的，名字用来认出画的是哪一次取回来的。 */
const tasks = (label = '追更检查') => ({
  available: true, running: [], skipped: [],
  finished: [{
    id: 1, task_key: 'follow-check', task_label: label, trigger: 'manual', status: 'succeeded', host: 'desk',
    started_at: '2026-09-11T10:00:00Z', finished_at: '2026-09-11T10:00:05Z', elapsed_seconds: 5,
    progress_current: null, progress_total: null, progress_label: '', result_summary: {}, error: '',
    parent_run_id: null, root_run_id: null, followup_key: '', followup_depth: 0,
  }],
});
const DOWNLOADS = { available: true, providers: [], tasks: [] };
type Reply = { ok: boolean; status: number; json(): Promise<unknown> };
const reply = (body: unknown): Reply => ({ ok: true, status: 200, json: async () => body });

/** 活动页的取数：`/api/tasks` 手动兑现（首屏等的就是它），云下载段另取的 `/api/downloads` 当场回空表。 */
function tasksFetch(body: unknown = tasks(), { deferred = true } = {}) {
  const pending: Array<() => void> = [];
  let signal: AbortSignal | undefined;
  const fetched = vi.fn((input: string, init?: RequestInit) => {
    if (input.startsWith('/api/downloads')) return Promise.resolve(reply(DOWNLOADS));
    signal = init?.signal ?? undefined;
    if (!deferred) return Promise.resolve(reply(body));
    return new Promise<Reply>((resolve, reject) => {
      pending.push(() => resolve(reply(body)));
      signal?.addEventListener('abort', () => { reject(new DOMException('已中止', 'AbortError')) });
    });
  });
  const taskCalls = () => fetched.mock.calls.filter(([input]) => input.startsWith('/api/tasks')).length;
  return {
    fetched, taskCalls,
    resolve: () => { for (const settle of pending.splice(0)) settle() },
    signal: () => signal,
    install: () => vi.stubGlobal('fetch', fetched),
  };
}

/** 每一批 DOM 变化交付时容器里的样子：骨架还在不在、整页画出来没有。 */
function watch(stats: Element, skeleton: Element, label = '追更检查') {
  const batches: Array<{ skeleton: boolean; painted: boolean }> = [];
  const observer = new MutationObserver(() => {
    batches.push({ skeleton: stats.contains(skeleton), painted: Boolean(stats.textContent?.includes(label)) });
  });
  observer.observe(stats, { childList: true, subtree: true });
  unmounts.push(() => observer.disconnect());
  return batches;
}

async function until(ok: () => boolean, what: string): Promise<void> {
  for (let step = 0; step < 200; step += 1) {
    if (ok()) return;
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
  throw new Error(`一直没等到：${what}`);
}

/* 这里直接调打开管线：用例把地址落在首页或不交壳的动作，管理区的页面元素不打开（元素怎么打开见
   `pages/managed.test.tsx`）。 */
const open = (r: Loaded, stats: Element, isCurrent = () => true) =>
  r.openManagedRoute('/tasks', {}, { container: stats, isCurrent });
/** 页面画出来的那一块（宿主里的第一个元素）：重挂之后就换成另一个。 */
const painted = (stats: Element) => stats.querySelector(':scope > .peach-react > *');

it('冷启动深链，Router 先挂上：壳画的骨架留到首屏取齐，换成整页在同一批变化里', async () => {
  const r = await load('/');
  const { stats, skeleton } = surface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  const fetch = tasksFetch();
  fetch.install();
  let opened: Promise<boolean> | undefined;
  await act(async () => { opened = open(r, stats) });
  await until(() => fetch.taskCalls() > 0, '首屏取数发出去');
  expect(stats.firstElementChild, '取齐之前骨架原样留着').toBe(skeleton);
  const batches = watch(stats, skeleton);
  fetch.resolve();
  await act(async () => { expect(await opened).toBe(true) });
  expect(fetch.taskCalls()).toBe(1);
  expect(batches[0], '骨架撤下的那一批变化里整页已经画好，中间没有空白的一帧').toEqual({ skeleton: false, painted: true });
  expect(stats.querySelector(':scope > .peach-react')?.textContent).toContain('追更检查');
});

it('冷启动深链，壳先打开、Router 后挂上：包到了才取数，骨架同样留到换成整页', async () => {
  const r = await load('/');
  const { stats, skeleton } = surface();
  let arrive: (prefetch: typeof r.prefetchManagedRoute) => void = () => {};
  r.connectManagedRoutes(new Promise((resolve) => { arrive = resolve }));
  const fetch = tasksFetch();
  fetch.install();
  let opened: Promise<boolean> | undefined;
  await act(async () => { opened = open(r, stats) });
  await mount(r);
  expect(fetch.fetched).not.toHaveBeenCalled();
  expect(stats.firstElementChild).toBe(skeleton);
  await act(async () => { arrive(r.prefetchManagedRoute) });
  await until(() => fetch.taskCalls() > 0, '首屏取数发出去');
  expect(stats.firstElementChild).toBe(skeleton);
  const batches = watch(stats, skeleton);
  fetch.resolve();
  await act(async () => { expect(await opened).toBe(true) });
  expect(fetch.taskCalls()).toBe(1);
  expect(batches[0]).toEqual({ skeleton: false, painted: true });
});

it('应用内切页：在途那一次被收起时不动容器，壳留着的同一张骨架等到下一次打开才换掉', async () => {
  const r = await load('/tasks');
  const { stats, skeleton } = surface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  const first = tasksFetch(tasks('第一次'));
  first.install();
  const firstOpen = open(r, stats);
  await until(() => first.taskCalls() > 0, '第一次取数发出去');
  // 壳认领表面（`claimSurface`）：在途的首屏取数中止，这一次不画。
  act(() => { r.releaseManagedRoute(stats) });
  expect(first.signal()?.aborted).toBe(true);
  await act(async () => { expect(await firstOpen).toBe(false) });
  expect(stats.firstElementChild, '收起在途那一次不碰壳的骨架').toBe(skeleton);
  const second = tasksFetch(tasks('第二次'));
  second.install();
  const secondOpen = open(r, stats);
  await until(() => second.taskCalls() > 0, '第二次取数发出去');
  expect(stats.firstElementChild).toBe(skeleton);
  const batches = watch(stats, skeleton, '第二次');
  second.resolve();
  await act(async () => { expect(await secondOpen).toBe(true) });
  expect(batches[0]).toEqual({ skeleton: false, painted: true });
  expect(stats.textContent).not.toContain('第一次');
});

it('取数期间壳换了页就不画，骨架留给下一页', async () => {
  const r = await load('/tasks');
  const { stats, skeleton } = surface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  const fetch = tasksFetch();
  fetch.install();
  let current = true;
  const opening = open(r, stats, () => current);
  await until(() => fetch.taskCalls() > 0, '取数发出去');
  current = false;
  fetch.resolve();
  await act(async () => { expect(await opening).toBe(false) });
  expect(stats.firstElementChild).toBe(skeleton);
  expect(r.managedEntry(stats)).toBeNull();
});

it('同一路径再打开一次就重取：每次打开领一个新代次，页面重挂', async () => {
  const r = await load('/tasks');
  const { stats } = surface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  const fetch = tasksFetch(tasks(), { deferred: false });
  fetch.install();
  await act(async () => { await open(r, stats) });
  const page = painted(stats);
  const revision = r.managedEntry(stats)?.revision;
  act(() => { r.releaseManagedRoute(stats) });
  expect(stats.querySelector('.peach-react'), '收起时宿主跟着撤掉').toBeNull();
  await act(async () => { await open(r, stats) });
  expect(fetch.taskCalls()).toBe(2);
  expect(r.managedEntry(stats)?.revision).not.toBe(revision);
  expect(painted(stats)).not.toBeNull();
  expect(painted(stats)).not.toBe(page);
});

it('详情舞台压在上面时页面留着，地址回来也不重挂；壳下一次认领表面才收', async () => {
  const r = await load('/');
  const { stats } = surface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  tasksFetch(tasks(), { deferred: false }).install();
  await act(async () => { await open(r, stats) });
  const page = painted(stats);
  expect(page).not.toBeNull();
  // 详情由壳推 `/assets/:id`，不认领表面。
  await act(async () => { r.shellNavigate('/assets/7') });
  expect(painted(stats), '舞台下面那一页不许跟着地址卸掉').toBe(page);
  // 关掉详情：壳把地址写回列表那一页。
  await act(async () => { r.shellNavigate('/') });
  expect(painted(stats)).toBe(page);
  act(() => { r.releaseManagedRoute(stats) });
  expect(stats.children).toHaveLength(0);
});

it('壳直接改写了容器再收起也不报错：页面画在自己的宿主里', async () => {
  const r = await load('/tasks');
  const { stats } = surface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  tasksFetch(tasks(), { deferred: false }).install();
  const errors = vi.spyOn(console, 'error');
  await act(async () => { await open(r, stats) });
  stats.innerHTML = '<p>别的页面</p>';
  act(() => { r.releaseManagedRoute(stats) });
  expect(errors).not.toHaveBeenCalled();
  expect(stats.innerHTML).toBe('<p>别的页面</p>');
  expect(r.managedEntry(stats)).toBeNull();
});

it('路由树没装载就打开：容器先归路由树、不取数，装载之后才取首屏、才画', async () => {
  const r = await load('/tasks');
  const { stats, skeleton } = surface();
  const fetch = tasksFetch(tasks(), { deferred: false });
  fetch.install();
  const opening = open(r, stats);
  await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  expect(fetch.taskCalls(), '没装载时不取数').toBe(0);
  expect(stats.firstElementChild).toBe(skeleton);
  expect(r.managedTaken(stats), '等着的那一次已经占了容器，壳不再重开').toBe(true);

  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  await act(async () => { expect(await opening).toBe(true) });
  expect(fetch.taskCalls()).toBe(1);
  expect(painted(stats)).not.toBeNull();
});

it('包取不回来时，等着的那一页跟着失败', async () => {
  const r = await load('/tasks');
  const { stats, skeleton } = surface();
  r.connectManagedRoutes(Promise.reject(new Error('取不回 peach-react.js')));
  await expect(open(r, stats)).rejects.toThrow('取不回 peach-react.js');
  expect(stats.firstElementChild).toBe(skeleton);
});

const configuration = {
  editable: true, notice: '', revision: 'rev-1', media_dirs: ['D:\\Media'], port: 9123, facts: [],
  startup: { available: true, enabled: false, silent: true, message: '', desktop: false, desktop_message: '' },
  peach_proxy: { mode: 'environment', proxy_saved: false, needs_selection: false },
};

/* 壳打开返回的那一刻就按 `#peachProxy` 滚过去，所以页签条与交进来的那一组的选中态要在同一次提交里画好。
   分区自己怎么排、页签怎么走在 `configuration.test.tsx`。 */
it('配置页打开返回的那一刻，页签条已经在容器里，选中的是交进来的那一组', async () => {
  const r = await load('/settings/library');
  const { stats } = surface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => {
    if (url === '/api/configuration') return configuration;
    if (url === '/api/configuration/indexers') return { indexers: [], max_indexers: 4 };
    if (url === '/api/follow/credentials') return { root: '', providers: [] };
    if (url === '/api/follow/schedule') return { enabled: false, available: true, interval_minutes: 60 };
    return { mode: 'off', status: 'idle' };
  } })));
  await act(async () => {
    const open = { section: '网络与访问' };
    expect(await r.openManagedRoute('/settings/library', open, { container: stats, isCurrent: () => true })).toBe(true);
    const tabs = [...stats.querySelectorAll('.ui-configpage > .ui-board-local-nav[role="tablist"] > [role="tab"]')];
    expect(tabs.map((tab) => tab.textContent)).toEqual(['媒体库与入库', '来源与订阅', '网络与访问', '应用与维护', '系统诊断']);
    expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toEqual(['false', 'false', 'true', 'false', 'false']);
    const active = stats.querySelector('.ui-board-group-active');
    expect(active?.id).toBe(tabs[2].getAttribute('aria-controls'));
    expect(stats.querySelector('.ui-configpage')?.parentElement?.classList.contains('peach-react')).toBe(true);
  });
});

it('这几页之间的跳转交给 React Router，别的路径交壳', async () => {
  const r = await load('/');
  const actions = shellActions();
  const navigate = vi.fn();
  r.managedGo('/tasks', actions, navigate);
  r.managedGo('/sources?from=cleanup#top', actions, navigate);
  r.managedGo('/', actions, navigate);
  r.managedGo('/trash', actions, navigate);
  expect(navigate.mock.calls).toEqual([['/tasks'], ['/sources?from=cleanup#top']]);
  expect(vi.mocked(actions.navigate).mock.calls).toEqual([['/'], ['/trash']]);
});

it('页面里的跳转：只领一个页面打开序号、历史只多一条', async () => {
  const r = await load('/sources');
  await mount(r);
  const epoch = r.peachHistory.navigation.openEpoch;
  const before = window.history.length;
  const navigate = ((to: string) => { r.peachHistory.push(to) }) as unknown as NavigateFunction;
  await act(async () => { r.managedGo('/tasks', shellActions(), navigate) });
  expect([r.peachHistory.navigation.openEpoch - epoch, location.pathname, window.history.length - before]).toEqual([1, '/tasks', 1]);
});

/** 一页的元素与它拿到的 props，不画。 */
function element<P>(path: keyof Loaded['MANAGED_ROUTES'], r: Loaded, open: object, actions: ShellActions, go: (path: string) => void) {
  return (r.MANAGED_ROUTES[path].page as (open: object, actions: ShellActions, go: (path: string) => void) => ReactElement<P>)(
    open, actions, go).props;
}

it('诊断页先选配置分栏再导航，作品与任务交给共享入口', async () => {
  const r = await load();
  const actions = shellActions();
  const order: string[] = [];
  const go = vi.fn((path: string) => { order.push(path) });
  vi.mocked(actions.requestConfigurationSection).mockImplementation(section => { order.push(section) });
  const page = element<ConfigurationProps & { diagnostics: ReactElement<DiagnosticsProps> }>('/settings/diagnostics', r, {}, actions, go);
  const props = page.diagnostics.props;
  props.configure('网络与访问');
  props.openItem(12);
  props.navigate('/tasks');
  expect(order).toEqual(['网络与访问', '/settings/network', '/tasks']);
  expect(actions.openItem).toHaveBeenCalledWith(12);
});

it('统计页：点标签交壳回目录；「添加媒体目录」先交页签再换到配置页', async () => {
  const r = await load();
  const actions = shellActions();
  const order: string[] = [];
  const go = vi.fn((path: string) => { order.push(`go ${path}`) });
  vi.mocked(actions.requestConfigurationSection).mockImplementation((section) => { order.push(`section ${section}`) });
  const props = element<StatsProps>('/stats', r, { configurable: false }, actions, go);
  props.onTag('痴女');
  props.openMediaSettings();
  expect(vi.mocked(actions.openTag).mock.calls).toEqual([['痴女']]);
  expect(order).toEqual(['section 媒体库与入库', 'go /settings/library']);
  expect([props.configurable, props.tagLabel('JK制服')]).toEqual([false, 'JK制服']);
});

it('口味页：总结里的下一步按路径走 go', async () => {
  const r = await load();
  const go = vi.fn();
  const props = element<TasteProps>('/taste', r, { onboarding: true }, shellActions(), go);
  props.navigate('/subscriptions');
  props.navigate('/');
  expect(go.mock.calls).toEqual([['/subscriptions'], ['/']]);
  expect(props.onboarding).toBe(true);
});

it('数据管理页：复核、高清版与重复文件走 go，垃圾文件与回收站交壳', async () => {
  const r = await load();
  const actions = shellActions();
  const go = vi.fn();
  const props = element<DataCleanupProps>('/data-cleanup', r, {}, actions, go);
  for (const section of ['review', 'quality', 'duplicates', 'ads', 'trash'] as const) props.open(section);
  expect(go.mock.calls).toEqual([['/review'], ['/quality-goals'], ['/duplicates']]);
  expect(vi.mocked(actions.openManage).mock.calls).toEqual([['ads'], ['trash']]);
  props.toast('做完了', { warning: true });
  props.toast('做完了');
  expect(vi.mocked(actions.toast).mock.calls).toEqual([[{ text: '做完了' }, { sound: 'warning' }]]);
  expect(vi.mocked(actions.receipt).mock.calls).toEqual([['做完了']]);
});

it('复核页与关注管理页把地址写回壳，不重开', async () => {
  const r = await load();
  const actions = shellActions();
  const go = vi.fn();
  const review = element<ReviewProps>('/review', r,
    { category: 'name', readOnly: true, readOnlyMessage: '只读', writerUrl: '' }, actions, go);
  expect([review.route, review.category, review.readOnly]).toEqual([actions.routeReview, 'name', true]);
  const follow = element<FollowManageProps>('/subscriptions', r, {
    tab: 'list', page: 2, sort: '', dir: '', pageSize: 20, layout: 'default', readOnly: false, readOnlyMessage: '', writerUrl: '',
  }, actions, go);
  expect([follow.route, follow.savePreference, follow.openFollow, follow.page])
    .toEqual([actions.routeFollowManage, actions.saveFollowPreference, actions.openFollow, 2]);
});

it('回执、打开作品与资料页都交回壳：都走过去时回执', async () => {
  const r = await load();
  const actions = shellActions();
  const undo = async () => {};
  const go = vi.fn();
  const goals = element<QualityGoalsProps>('/quality-goals', r, {}, actions, go);
  goals.openItem(3);
  expect(goals.srcBadge).toBe(actions.srcBadge);
  expect(goals.javDisplayName({ name: 'ABC-123 片名.mp4' } as never)).toContain('ABC-123');
  expect(goals.javTitleHtml({ name: '<b>.mp4' } as never)).not.toContain('<b>');
  goals.toast('已提交离线下载');
  const duplicates = element<DuplicatesProps>('/duplicates', r, {}, actions, vi.fn());
  duplicates.toast('已删除', { undo });
  duplicates.toast('已保留');
  duplicates.failure('删除', 'boom');
  const configuration = element<ConfigurationProps>('/settings/library', r, { section: '媒体库与入库' }, actions, vi.fn());
  configuration.receipt('已保存配置');
  expect([configuration.reopenTutorial, configuration.section]).toEqual([actions.reopenTutorial, '媒体库与入库']);
  const review = element<ReviewProps>('/review', r,
    { category: '', readOnly: false, readOnlyMessage: '', writerUrl: '' }, actions, vi.fn());
  review.openEntity('performer', '某人');
  review.toast('已判定');
  expect(review.revealSource).toBe(actions.revealSource);
  element<FollowManageProps>('/subscriptions', r, {
    tab: 'list', page: 1, sort: '', dir: '', pageSize: 20, layout: 'default', readOnly: false, readOnlyMessage: '', writerUrl: '',
  }, actions, vi.fn()).toast('已添加 1 个关注来源');
  const taste = element<TasteProps>('/taste', r, { onboarding: false }, actions, vi.fn());
  taste.onSignal('tag', '痴女');
  taste.toast('已导入');
  expect(vi.mocked(actions.openItem).mock.calls).toEqual([[3]]);
  expect(vi.mocked(actions.openEntity).mock.calls).toEqual([['performer', '某人']]);
  expect(vi.mocked(actions.openTasteSignal).mock.calls).toEqual([['tag', '痴女']]);
  expect(vi.mocked(actions.failure).mock.calls).toEqual([['删除', 'boom']]);
  expect(actions.toast).not.toHaveBeenCalled();
  expect(vi.mocked(actions.receipt).mock.calls).toEqual([
    ['已提交离线下载'], ['已删除', { undo }], ['已保留', {}], ['已保存配置'], ['已判定'], ['已添加 1 个关注来源'], ['已导入'],
  ]);
});

it('采集页的提示走全站 Toast 原样', async () => {
  const r = await load();
  const actions = shellActions();
  element<ScrapingProps>('/sources', r, {}, actions, vi.fn()).toast('已保存');
  expect(vi.mocked(actions.toast).mock.calls).toEqual([['已保存']]);
  expect(actions.receipt).not.toHaveBeenCalled();
});

/** 索引页与资料页共用的容器，里面是壳铺好的骨架。 */
function indexSurface() {
  const index = document.createElement('section');
  index.id = 'index';
  index.innerHTML = '<div class="geist-skeleton" data-skeleton="index">正在读取</div>';
  document.body.append(index);
  return { index, skeleton: index.firstElementChild! };
}

const TAGS = { items: [{ k: '痴女', n: 1, cat: 'role' }], has_more: false };
const tagsOpen = (selectMode = false) => ({
  kind: 'tags', q: '', scope: 'local', view: 'alphabet', category: 'all', layout: 'big', selectMode, configurable: false,
});

/** 索引页的取数：`/api/index` 按需手动兑现，其余当场回空表。 */
function indexFetch(body: unknown = TAGS, { deferred = true } = {}) {
  const pending: Array<() => void> = [];
  const fetched = vi.fn((input: string, init?: RequestInit) => {
    if (!input.startsWith('/api/index')) return Promise.resolve(reply({ items: [], has_more: false }));
    if (!deferred) return Promise.resolve(reply(body));
    return new Promise<Reply>((resolve, reject) => {
      pending.push(() => resolve(reply(body)));
      init?.signal?.addEventListener('abort', () => { reject(new DOMException('已中止', 'AbortError')) });
    });
  });
  return {
    fetched,
    indexCalls: () => fetched.mock.calls.filter(([input]) => input.startsWith('/api/index')).length,
    resolve: () => { for (const settle of pending.splice(0)) settle() },
    install: () => vi.stubGlobal('fetch', fetched),
  };
}

it('冷启动深链索引页：壳画的骨架留到首屏取齐，换成整页在同一批变化里', async () => {
  const r = await load('/tags');
  const { index, skeleton } = indexSurface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  const fetch = indexFetch();
  fetch.install();
  let opened: Promise<boolean> | undefined;
  await act(async () => { opened = r.openManagedRoute('/tags', tagsOpen(), { container: index, isCurrent: () => true }) });
  await until(() => fetch.indexCalls() > 0, '首屏取数发出去');
  expect(index.firstElementChild, '取齐之前骨架原样留着').toBe(skeleton);
  const batches = watch(index, skeleton, '痴女');
  fetch.resolve();
  await act(async () => { expect(await opened).toBe(true) });
  expect(fetch.indexCalls()).toBe(1);
  expect(batches[0], '骨架撤下的那一批变化里整页已经画好').toEqual({ skeleton: false, painted: true });
  expect(index.querySelector(':scope > .peach-react [data-index-search]')).not.toBeNull();
});

/* 壳那枚选择键关掉时经 `updateManagedRoute` 把新值推进画着的那一页：只合并这一项，其余 props 照旧，
   代次不变、不重挂也不重取——重挂会把页头连同过滤框里打了一半的字一起换掉。 */
it('就地更新：合并进画着的那一页，不重挂、不重取，代次不变', async () => {
  const r = await load('/tags');
  const { index } = indexSurface();
  const actions = await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  const fetch = indexFetch(TAGS, { deferred: false });
  fetch.install();
  await act(async () => { await r.openManagedRoute('/tags', tagsOpen(true), { container: index, isCurrent: () => true }) });
  const revision = r.managedEntry(index)?.revision;
  const calls = fetch.fetched.mock.calls.length;
  const input = index.querySelector('[data-index-search] input');
  const tag = () => index.querySelector<HTMLElement>('[data-alpha-tag][data-k="痴女"]')!;
  await act(async () => { tag().click() });
  expect(tag().getAttribute('aria-pressed')).toBe('true');
  await act(async () => { r.updateManagedRoute(index, { selectMode: false }) });
  expect(tag().getAttribute('aria-pressed'), '关掉选择键时所选要跟着清空').toBe('false');
  expect(index.querySelector('[data-index-search] input'), '推新值不许重挂').toBe(input);
  expect(r.managedEntry(index)?.revision).toBe(revision);
  expect(r.managedEntry(index)?.props).toMatchObject({ kind: 'tags', selectMode: false, layout: 'big' });
  expect(fetch.fetched.mock.calls.length, '推新值不许重取').toBe(calls);
  await act(async () => { tag().click() });
  expect(vi.mocked(actions.showIndexTags), '其余 props 照旧：不在选择模式时点一枚直接回目录')
    .toHaveBeenCalledWith(['痴女'], 'all');
});

it('就地更新在容器里没有画着的页面时是空操作：还在取首屏时交进去的那一份照旧', async () => {
  const r = await load('/tags');
  const { index } = indexSurface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  const fetch = indexFetch();
  fetch.install();
  r.updateManagedRoute(index, { selectMode: true });
  expect(r.managedEntry(index)).toBeNull();
  const opening = r.openManagedRoute('/tags', tagsOpen(), { container: index, isCurrent: () => true });
  await until(() => fetch.indexCalls() > 0, '首屏取数发出去');
  r.updateManagedRoute(index, { selectMode: true });
  fetch.resolve();
  await act(async () => { expect(await opening).toBe(true) });
  expect(r.managedEntry(index)?.props).toMatchObject({ selectMode: false });
});

/* 资料页画进 `#index` 时管理区那一页只是被壳藏起来：两个容器各记各的，收一个不动另一个；
   一次点名两个容器就一起收。 */
it('`#stats` 与 `#index` 各画一页，互不相收；点名两个容器时一起收', async () => {
  const r = await load('/tasks');
  const { stats } = surface();
  const { index } = indexSurface();
  await mount(r);
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  const tasksReply = tasksFetch(tasks(), { deferred: false });
  const tagsReply = indexFetch(TAGS, { deferred: false });
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => (
    input.startsWith('/api/index') ? tagsReply.fetched(input, init) : tasksReply.fetched(input, init))));
  await act(async () => { await open(r, stats) });
  const page = painted(stats);
  await act(async () => { await r.openManagedRoute('/tags', tagsOpen(), { container: index, isCurrent: () => true }) });
  expect(painted(stats), '在 `#index` 里打开一页不收 `#stats` 那一页').toBe(page);
  expect(r.managedEntries().map((entry) => entry.path)).toEqual(['/tasks', '/tags']);
  act(() => { r.releaseManagedRoute(index) });
  expect(index.querySelector('.peach-react')).toBeNull();
  expect(painted(stats), '收 `#index` 不动 `#stats`').toBe(page);
  await act(async () => { await r.openManagedRoute('/tags', tagsOpen(), { container: index, isCurrent: () => true }) });
  act(() => { r.releaseManagedRoute(stats, index) });
  expect([stats.children.length, index.children.length, r.managedEntries().length]).toEqual([0, 0, 0]);
});

it('索引页：页内写地址、存版式、头像与去处都接到壳的那一组上', async () => {
  const r = await load();
  const actions = shellActions();
  const props = (r.INDEX_ROUTES['/performers'].page(
    { ...tagsOpen(), kind: 'performers' } as never, actions, vi.fn()) as ReactElement<Record<string, unknown>>).props;
  expect([props.route, props.savePreference, props.exitSelectMode, props.personAvatar, props.authorAvatar,
    props.openEntity, props.showTags, props.openFollowAuthor, props.openFollowTag]).toEqual([
    actions.routeIndex, actions.savePeopleLayout, actions.exitSelectMode, actions.personAvatar, actions.authorAvatar,
    actions.openEntity, actions.showIndexTags, actions.openFollowAuthor, actions.openFollowTag]);
  expect([props.kind, props.layout, props.selectMode, props.configurable]).toEqual(['performers', 'big', false, false]);
  expect((props.tagLabel as (tag: string) => string)('JK制服')).toBe('JK制服');
});

it('索引页的路径不经 React Router 的 navigate：同页换 search 与跨页进来都交壳写地址', async () => {
  const r = await load('/');
  const actions = shellActions();
  const navigate = vi.fn();
  for (const path of ['/performers', '/tags?view=cloud', '/studios']) r.managedGo(path, actions, navigate);
  expect(navigate).not.toHaveBeenCalled();
  expect(vi.mocked(actions.navigate).mock.calls).toEqual([['/performers'], ['/tags?view=cloud'], ['/studios']]);
  expect(r.ROUTED_PATHS).toEqual(expect.arrayContaining(['/performers', '/creators', '/studios', '/agencies', '/tags']));
});
