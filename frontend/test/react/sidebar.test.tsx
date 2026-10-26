/* 侧栏：骨架与路由树的换手、导航顺序跟 store 走、拖动排序写回、筛选键的按下态与计数、分组开合、
 * 「展开全部」、关注标签与时长两端，以及它作为常驻组件（`RESIDENT_ROUTES.sidebar`）在路由树里的行为：
 * 宿主就是 `#drawerScroll`、骨架在首帧的同一个任务里换掉、`attached` 在首帧之后调一次、句柄同步画完、
 * 壳挪进标题行的节点经重画仍在、抛错只卸组件。
 *
 * 玻璃的滑动、真实的拖动手势与抽屉开合要量布局，由 e2e 在浏览器里走（`e2e/sidebar.test.ts`）。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore } from '../../src/settings-store';
import { NAV_CATALOG } from '../../src/application/navigation-items';
import { DEFAULT_SIDEBAR_ORDER } from '../../src/sidebar';
import { sidebarSkeletonHtml } from '../../src/sidebar-skeleton';
import type { BatchDockApi, BatchDockHost } from '../../src/react/batch-dock/batch-dock-api';
import type { ShellActions } from '../../src/react/router/shell-actions';
import type { SidebarApi, SidebarChip, SidebarFacets, SidebarHost, SidebarProps } from '../../src/react/sidebar/sidebar-api';
import { moveSidebarKey } from '../../src/react/sidebar/sidebar-order';
import { click, fetchMock, sentBody, settle } from './render';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

type ActFlag = { IS_REACT_ACT_ENVIRONMENT?: boolean };

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

/* 侧栏的字形换成一层能当场决定抛不抛的包装：用例中途改 `glyph.broken`，就是「下一次绘制抛错」。 */
const glyph = vi.hoisted(() => ({ broken: false }));
vi.mock(import('../../src/react/settings-panel/icon'), async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    Icon: (props: Parameters<typeof actual.Icon>[0]) => {
      if (glyph.broken) throw new Error('侧栏画不出来');
      return actual.Icon(props);
    },
  };
});

/* 常驻组件适配层直接读取同一包内的配置函数；测试只接本场景的句柄契约。 */
type ShellIslands = { connectApplication(next: (actions: ShellActions) => void): () => void;
  loadSidebar(host: SidebarHost): Promise<SidebarApi>;
  sidebarApi(): SidebarApi | null;
  loadBatchDock(host: BatchDockHost): Promise<BatchDockApi>;
};
const ISLANDS_MODULE = '../../src/react/application-residents';

async function load() {
  vi.resetModules();
  window.history.replaceState(null, '', '/');
  const [history, router, routes, islands] = await Promise.all([
    import('../../src/history'), import('../../src/react/router/router'), import('../../src/react/router/managed-routes'),
    import(/* @vite-ignore */ ISLANDS_MODULE) as Promise<ShellIslands>,
  ]);
  return { ...history, ...router, ...routes, islands };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = true;
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.innerHTML = '';
  glyph.broken = false;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
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

/** 壳启动时先画路由树（根的选项与 `mountApplication` 同一份）；接上取数单独一步，用例可以把它往后放。 */
async function mountRouter(r: Loaded) {
  unmounts.push(r.islands.connectApplication(() => {}));
  const root = createRoot(document.createElement('div'), r.ROUTER_ROOT_OPTIONS);
  await act(async () => { root.render(<r.RouterRoot actions={shellActions()} />) });
  unmounts.push(() => root.unmount());
}
async function connect(r: Loaded) {
  await act(async () => { r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute)) });
}

const CATALOG = [['', '首页', 'home'], ['unseen', '未看', 'eye'], ['follow', '关注', 'rss'], ['stats', '统计', 'chart']] as const;

const chip = (value: string, n: number | null = 1, extra: Partial<SidebarChip> = {}): SidebarChip =>
  ({ value, label: value.toUpperCase(), n, ...extra });

const facets = (patch: Partial<SidebarFacets> = {}): SidebarFacets => ({
  locations: [chip('local', 3), chip('115', 2, { offline: '这块盘没挂上' })],
  regions: [], orientations: [], creators: [], tags: [chip('a', 5), chip('b', 4)], tech: [], followTags: [],
  duration: false, ...patch,
});

/** 壳 `index.html` 里的抽屉与滚动层，壳启动时同步写进导航骨架；`attached` 由用例按需换成壳的挪动。 */
function sidebarHost(order = ['', 'unseen', 'follow'], current = '', attached: () => void = () => {}) {
  const drawer = document.createElement('aside');
  drawer.setAttribute('data-sidebar-drawer', '');
  const scroll = document.createElement('div');
  scroll.setAttribute('data-sidebar-scroll', '');
  drawer.append(scroll);
  document.body.append(drawer);
  const store = createSettingsStore('peach.settings.v1', { sidebarOrder: order });
  let pressed = current;
  const host: SidebarHost = {
    scroll, store, navCatalog: CATALOG,
    navOn: (key) => key === pressed,
    navTo: vi.fn(), toggleChip: vi.fn(), setDuration: vi.fn(), openFollowTag: vi.fn(), selectFollowTag: vi.fn(),
    selectFollowProvider: vi.fn(), setFollowDuration: vi.fn(),
    attached: vi.fn(attached),
  };
  scroll.innerHTML = sidebarSkeletonHtml(order, CATALOG, host.navOn);
  return { drawer, scroll, store, host, press: (key: string) => { pressed = key } };
}

/** 照壳的启动顺序接上：路由树画好并接上取数，铺骨架，装载侧栏，拿到句柄。 */
async function setup(order?: string[], current?: string, attached?: () => void) {
  const r = await load();
  await mountRouter(r);
  await connect(r);
  const shell = sidebarHost(order, current, attached);
  let api!: SidebarApi;
  await act(async () => { api = await r.islands.loadSidebar(shell.host) });
  const render = (props: Partial<SidebarProps>) => act(() => { api.render({ content: null, filters: {}, latest: null, ...props }) });
  return { r, ...shell, api, render };
}

/** 收下每一次上报；`console.error` 只静音，不数条数。 */
function watchReports() {
  const reported = vi.fn();
  vi.stubGlobal('reportError', reported);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  return reported;
}

const navKeys = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('[data-sidebar-nav] [data-nav]')].map((node) => node.dataset.nav);
const group = (root: ParentNode, title: string) => root.querySelector<HTMLDetailsElement>(`[data-sidebar-group="${title}"]`);
const chipOf = (root: ParentNode, key: string, value: string) =>
  root.querySelector<HTMLButtonElement>(`[data-sidebar-chip][data-key="${key}"][data-val="${value}"]`);

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  document.body.replaceChildren();
});

describe('换手', () => {
  it('骨架与侧栏画的是同一列：顺序、按下态与首页记号一致；预取未接上时滚动层里是骨架、句柄为 null', async () => {
    const skeleton = document.createElement('div');
    skeleton.innerHTML = sidebarSkeletonHtml(['unseen', '', 'missing'], CATALOG, (key) => key === 'unseen');
    expect(navKeys(skeleton)).toEqual(['unseen', '']);
    expect(skeleton.querySelector('[data-nav="unseen"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(skeleton.querySelector('[data-nav=""] [data-sidebar-home-logo]')).not.toBeNull();
    const r = await load();
    await mountRouter(r);
    const { scroll, host } = sidebarHost(['unseen', '', 'missing'], 'unseen');
    const html = scroll.innerHTML;
    let loaded: Promise<SidebarApi> | null = null;
    await act(async () => { loaded = r.islands.loadSidebar(host) });
    expect(scroll.innerHTML, '路由树还没接上取数，骨架原样').toBe(html);
    expect(r.islands.sidebarApi(), '画上之前没有句柄，壳继续接骨架上的点击').toBeNull();
    expect(host.attached).not.toHaveBeenCalled();

    await connect(r);
    let api: SidebarApi | null = null;
    await act(async () => { api = await loaded });
    expect(r.islands.sidebarApi()).toBe(api);
    expect(r.managedEntry(scroll)?.path).toBe('sidebar');
    expect(navKeys(scroll)).toEqual(navKeys(skeleton));
    expect(scroll.querySelector(':scope > [data-sidebar-head]')).not.toBeNull();
    expect(scroll.querySelector('[data-nav="unseen"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(host.attached).toHaveBeenCalledTimes(1);
  });

  it('骨架与首帧在同一个任务里换掉：每个任务边界上滚动层里要么是骨架、要么是画好的导航，不多包 .peach-react', async () => {
    const r = await load();
    await mountRouter(r);
    await connect(r);
    const { scroll, host } = sidebarHost();
    const skeletonNav = scroll.querySelector('[data-nav]');
    /* 不包 act：act 会把 flushSync 推到它结束时，看不出换手发生在哪一拍。 */
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    let done = false;
    void r.islands.loadSidebar(host).then(() => { done = true });
    const state = () => (scroll.contains(skeletonNav) ? 'skeleton' : navKeys(scroll).length ? 'sidebar' : 'empty');
    const seen: string[] = [];
    for (let turn = 0; turn < 200 && !done; turn += 1) {
      seen.push(state());
      await new Promise((resolve) => { setTimeout(resolve, 0) });
    }
    seen.push(state());
    expect(done).toBe(true);
    expect(seen).not.toContain('empty');
    expect(seen.at(-1)).toBe('sidebar');
    expect(navKeys(scroll)).toEqual(['', 'unseen', 'follow']);
    expect(scroll.querySelector(':scope > .peach-react')).toBeNull();
    expect(scroll.firstElementChild?.hasAttribute('data-sidebar-head')).toBe(true);
  });

  it('attached 在首帧画上之后、句柄交出之前调一次，之后的重画不再调', async () => {
    const r = await load();
    await mountRouter(r);
    await connect(r);
    const seen: Array<{ skeleton: boolean; nav: Array<string | undefined>; head: boolean; api: SidebarApi | null }> = [];
    const shell = sidebarHost(undefined, undefined, () => {
      seen.push({
        skeleton: shell.scroll.contains(skeletonNav), nav: navKeys(shell.scroll),
        head: shell.scroll.querySelector(':scope > [data-sidebar-head]') !== null, api: r.islands.sidebarApi(),
      });
    });
    const skeletonNav = shell.scroll.querySelector('[data-nav]');
    let api!: SidebarApi;
    await act(async () => { api = await r.islands.loadSidebar(shell.host) });
    expect(seen).toEqual([{ skeleton: false, nav: ['', 'unseen', 'follow'], head: true, api: null }]);
    act(() => { api.render({ content: { kind: 'catalog', key: '1', facets: facets() }, filters: {}, latest: null }) });
    act(() => { api.navChanged() });
    act(() => { shell.store.value.sidebarOrder = ['follow', '']; shell.store.save() });
    expect(shell.host.attached).toHaveBeenCalledTimes(1);
  });

  it('骨架转义名称', async () => {
    const node = document.createElement('div');
    node.innerHTML = sidebarSkeletonHtml(['x'], [['x', '<img src=x>', 'a"b']], () => false);
    expect(node.querySelector('img')).toBeNull();
    expect(node.querySelector('button')!.getAttribute('aria-label')).toBe('<img src=x>');
  });
});

describe('导航', () => {
  it('完整词表按持久偏好画默认导航，沉浸模式可在关注后启用', async () => {
    const r = await load();
    await mountRouter(r);
    await connect(r);
    const { scroll, host, store } = sidebarHost([...DEFAULT_SIDEBAR_ORDER]);
    host.navCatalog = NAV_CATALOG;
    scroll.innerHTML = sidebarSkeletonHtml(store.value.sidebarOrder, NAV_CATALOG, host.navOn);
    await act(async () => { await r.islands.loadSidebar(host) });
    expect(navKeys(scroll)).toEqual(DEFAULT_SIDEBAR_ORDER);
    expect(scroll.querySelector('[data-nav="immerse"]')).toBeNull();
    await act(async () => {
      store.value.sidebarOrder = ['follow', 'immerse', ...DEFAULT_SIDEBAR_ORDER.filter((key) => key !== 'follow')];
      store.save();
    });
    expect(navKeys(scroll).slice(0, 2)).toEqual(['follow', 'immerse']);
    expect(scroll.querySelector('[data-nav="immerse"]')?.textContent).toContain('沉浸模式');
  });

  it('store 里的顺序一变（设置面板或另一台机器同步回来），这一列当场重排', async () => {
    const { scroll, store } = await setup();
    act(() => { store.value.sidebarOrder = ['follow', '', 'stats']; store.save() });
    expect(navKeys(scroll)).toEqual(['follow', '', 'stats']);
  });

  it('点一项交给壳的 navTo；按下态由壳说重读时才换', async () => {
    const { scroll, host, api, press } = await setup();
    await click(scroll.querySelector('[data-nav="unseen"]'));
    expect(host.navTo).toHaveBeenCalledWith('unseen');
    press('unseen');
    act(() => api.navChanged());
    expect(scroll.querySelector('[data-nav="unseen"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(scroll.querySelector('[data-nav=""]')!.getAttribute('aria-pressed')).toBe('false');
  });

  it('拖到另一项下半截就排到它后面：先落本地 store，再写 /api/settings', async () => {
    const fetcher = fetchMock(200, { sidebarOrder: ['unseen', 'follow', ''] });
    vi.stubGlobal('fetch', fetcher);
    const { scroll, store } = await setup();
    const fire = (node: Element, type: string, clientY = 0) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(event, {
        dataTransfer: { value: { setData: () => {}, effectAllowed: '', dropEffect: '' } },
        clientY: { value: clientY },
      });
      act(() => { node.dispatchEvent(event) });
    };
    const home = scroll.querySelector('[data-nav=""]')!, follow = scroll.querySelector('[data-nav="follow"]')!;
    fire(home, 'dragstart');
    expect(home.hasAttribute('data-dragging')).toBe(true);
    fire(follow, 'dragover', 10);
    expect(follow.getAttribute('data-drop')).toBe('after');
    fire(follow, 'drop', 10);
    await settle();
    expect(store.value.sidebarOrder).toEqual(['unseen', 'follow', '']);
    expect(JSON.parse(localStorage.getItem('peach.settings.v1')!).sidebarOrder).toEqual(['unseen', 'follow', '']);
    expect(navKeys(scroll)).toEqual(['unseen', 'follow', '']);
    expect(scroll.querySelector('[data-dragging],[data-drop]')).toBeNull();
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/settings');
    expect(sentBody(fetcher)).toEqual({ sidebarOrder: ['unseen', 'follow', ''] });
  });

  it('挪不了的情形不产生新顺序', async () => {
    expect(moveSidebarKey(['a', 'b', 'c'], 'a', 'c', false)).toEqual(['b', 'a', 'c']);
    expect(moveSidebarKey(['a', 'b'], 'a', 'a', true)).toBeNull();
    expect(moveSidebarKey(['a', 'b'], 'x', 'a', true)).toBeNull();
  });
});

describe('筛选分组', async () => {
  it('按下态照当前语境的筛选画，点下去交给壳；脱盘的来源点不了', async () => {
    const { scroll, host, render } = await setup();
    render({ content: { kind: 'catalog', key: '1', facets: facets() }, filters: { loc: 'local', tag: 'b' } });
    expect(chipOf(scroll, 'loc', 'local')!.getAttribute('aria-pressed')).toBe('true');
    expect(chipOf(scroll, 'tag', 'b')!.getAttribute('aria-pressed')).toBe('true');
    expect(chipOf(scroll, 'tag', 'a')!.getAttribute('aria-pressed')).toBe('false');
    await click(chipOf(scroll, 'loc', 'local'));
    expect(host.toggleChip).toHaveBeenCalledWith('loc', 'local', true);
    await click(chipOf(scroll, 'tag', 'a'));
    expect(host.toggleChip).toHaveBeenLastCalledWith('tag', 'a', false);
    const offline = chipOf(scroll, 'loc', '115')!;
    expect(offline.disabled).toBe(true);
    expect(offline.hasAttribute('data-offline')).toBe(true);
    expect(offline.title).toBe('这块盘没挂上');
  });

  it('名字放不下被省略时悬停读得到全名：普通条目与关注标签都带 title', async () => {
    const { scroll, render } = await setup();
    render({ content: { kind: 'catalog', key: '1', facets: facets({ followTags: [chip('the_legend_of_zelda:_tears_of_the_kingdom', 2)] }) } });
    expect(chipOf(scroll, 'tag', 'a')!.title).toBe(chipOf(scroll, 'tag', 'a')!.querySelector('[data-sidebar-chip-label]')!.textContent);
    expect(scroll.querySelector<HTMLElement>('[data-follow-drawer-tag="the_legend_of_zelda:_tears_of_the_kingdom"]')!.title)
      .toBe(scroll.querySelector('[data-follow-drawer-tag="the_legend_of_zelda:_tears_of_the_kingdom"] [data-sidebar-chip-label]')!.textContent);
  });

  it('就地刷新的计数只改数字：名单里有、新聚合里没有的记 0', async () => {
    const { scroll, render } = await setup();
    const content = { kind: 'catalog' as const, key: '1', facets: facets() };
    render({ content });
    expect(chipOf(scroll, 'tag', 'a')!.querySelector('[data-sidebar-count]')!.textContent).toBe('5');
    render({ content, latest: facets({ tags: [chip('b', 9)] }) });
    expect(chipOf(scroll, 'tag', 'a')!.querySelector('[data-sidebar-count]')!.textContent).toBe('0');
    expect(chipOf(scroll, 'tag', 'b')!.querySelector('[data-sidebar-count]')!.textContent).toBe('9');
  });

  it('一进来只展开正在生效的组；人自己开合过的组按会话记住，换一份聚合也不改', async () => {
    const { scroll, render } = await setup();
    render({ content: { kind: 'catalog', key: '1', facets: facets() }, filters: { tag: 'a' } });
    expect(group(scroll, '内容标签')!.open).toBe(true);
    expect(group(scroll, '来源')!.open).toBe(false);
    const toggle = group(scroll, '来源')!.querySelector<HTMLElement>('[data-sidebar-toggle]')!;
    expect(document.getElementById(toggle.getAttribute('aria-controls')!)).not.toBeNull();
    await click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    render({ content: { kind: 'catalog', key: '2', facets: facets() }, filters: {} });
    expect(group(scroll, '来源')!.open).toBe(true);
    expect(group(scroll, '内容标签')!.open).toBe(false);
  });

  it('「展开全部」接在名单末尾，摊开照最新那一份聚合', async () => {
    const { scroll, render } = await setup();
    const tags = Array.from({ length: 32 }, (_, index) => chip(`t${index}`, 40 - index));
    const content = { kind: 'catalog' as const, key: '1', facets: facets({ tags }) };
    render({ content, latest: facets({ tags: [...tags, chip('late', 1)] }) });
    await click(group(scroll, '内容标签')!.querySelector('[data-sidebar-toggle]'));
    const body = group(scroll, '内容标签')!.querySelector('[data-sidebar-body]')!;
    expect(body.querySelectorAll('[data-sidebar-chip]')).toHaveLength(30);
    const more = body.lastElementChild as HTMLButtonElement;
    expect(more.getAttribute('data-sidebar-more')).toBe('tag');
    expect(more.getAttribute('aria-label')).toBe('展开全部内容标签');
    await click(more);
    expect(body.querySelectorAll('[data-sidebar-chip]')).toHaveLength(33);
    expect(more.getAttribute('aria-expanded')).toBe('true');
    expect(more.getAttribute('aria-label')).toBe('收起内容标签');
    /* 收起收的是整组：组标题当场报收起、正文不再可聚焦；`details.open` 等高度过渡走完才摘。 */
    await click(more);
    expect(group(scroll, '内容标签')!.querySelector('[data-sidebar-toggle]')!.getAttribute('aria-expanded')).toBe('false');
    expect(group(scroll, '内容标签')!.querySelector<HTMLElement>('[data-sidebar-collapse]')!.inert).toBe(true);
    expect(body.querySelectorAll('[data-sidebar-chip]')).toHaveLength(30);
  });

  it('换一份聚合时只有数字变了的徽标弹一下', async () => {
    const { scroll, render } = await setup();
    render({ content: { kind: 'catalog', key: '1', facets: facets({ tags: [chip('pa', 5), chip('pb', 4)] }) } });
    /* `popped` 加上又当场摘掉，回调到来时类名早已不在；摘掉那一笔的旧值里还留着它。 */
    const popped: string[] = [];
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (/\bpopped\b/.test(record.oldValue ?? '')) popped.push((record.target as HTMLElement).dataset.countBadge!);
      }
    });
    observer.observe(scroll, { subtree: true, attributes: true, attributeFilter: ['class'], attributeOldValue: true });
    render({ content: { kind: 'catalog', key: '2', facets: facets({ tags: [chip('pa', 5), chip('pb', 7)] }) } });
    await settle();
    observer.disconnect();
    expect(popped).toEqual(['tag:pb']);
  });

  it('目录页的关注标签去关注页，关注页的内容标签换成只按这一枚筛', async () => {
    const { scroll, host, render } = await setup();
    render({ content: { kind: 'catalog', key: '1', facets: facets({ followTags: [chip('x', 2)] }) } });
    await click(scroll.querySelector('[data-follow-drawer-tag="x"]'));
    expect(host.openFollowTag).toHaveBeenCalledWith('x');
    render({ content: { kind: 'follow', tags: [chip('y', 3), chip('z', 1)], selected: ['z'] } });
    expect(group(scroll, '来源')).toBeNull();
    const z = scroll.querySelector('[data-follow-drawer-tag="z"]')!;
    expect(z.getAttribute('aria-pressed')).toBe('true');
    expect(z.querySelector('[data-sidebar-count]')!.textContent).toBe('1');
    await click(scroll.querySelector('[data-follow-drawer-tag="y"]'));
    expect(host.selectFollowTag).toHaveBeenCalledWith('y');
  });

  it('关注页的来源与时长回关注筛选，不回目录筛选；详情只给标签时只有内容标签一组', async () => {
    const { scroll, host, render } = await setup();
    const kemono = chip('kemono', null, { dot: { kind: 'image', src: '/source-icon?provider=kemono' } });
    render({
      content: { kind: 'follow', tags: [chip('y', 3)], selected: [], providers: [kemono, chip('fanbox', null)], duration: true },
      filters: { provider: 'fanbox', dur_min: 600, dur_max: '' },
    });
    expect([...scroll.querySelectorAll('[data-sidebar-group]')].map((node) => node.getAttribute('data-sidebar-group')))
      .toEqual(['来源', '时长', '内容标签']);
    expect(chipOf(scroll, 'provider', 'fanbox')!.getAttribute('aria-pressed')).toBe('true');
    expect(chipOf(scroll, 'provider', 'kemono')!.querySelector('[data-sidebar-count]')).toBeNull();
    expect(chipOf(scroll, 'provider', 'kemono')!.querySelector('img')!.getAttribute('src')).toBe('/source-icon?provider=kemono');
    expect(scroll.querySelector(`[data-range-end="min"]`)!.textContent).toBe('10 分钟');
    await click(chipOf(scroll, 'provider', 'kemono'));
    expect(host.selectFollowProvider).toHaveBeenCalledWith('kemono');
    expect(host.toggleChip).not.toHaveBeenCalled();
    const max = scroll.querySelector<HTMLInputElement>('#durMax')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(max, '60');
      max.dispatchEvent(new Event('input', { bubbles: true }));
      max.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(host.setFollowDuration).toHaveBeenCalledWith(10, 60);
    expect(host.setDuration).not.toHaveBeenCalled();

    render({ content: { kind: 'follow', tags: [chip('y', 1)], selected: [] } });
    expect([...scroll.querySelectorAll('[data-sidebar-group]')].map((node) => node.getAttribute('data-sidebar-group')))
      .toEqual(['内容标签']);
  });

  it('时长两端各有一枚读数，右端拉到头是「不限」；松手才提交', async () => {
    const { scroll, host, render } = await setup();
    render({ content: { kind: 'catalog', key: '1', facets: facets({ duration: true }) }, filters: { dur_min: '1200' } });
    const tip = (end: string) => scroll.querySelector(`[data-range-end="${end}"]`)!.textContent;
    expect(tip('min')).toBe('20 分钟');
    expect(tip('max')).toBe('不限');
    const max = scroll.querySelector<HTMLInputElement>('#durMax')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(max, '60');
      max.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(tip('max')).toBe('60 分钟');
    expect(host.setDuration).not.toHaveBeenCalled();
    act(() => { max.dispatchEvent(new Event('change', { bubbles: true })) });
    expect(host.setDuration).toHaveBeenCalledWith(20, 60);
  });

  it('content 为 null 时只画导航', async () => {
    const { scroll, render } = await setup();
    render({ content: { kind: 'catalog', key: '1', facets: facets() } });
    render({ content: null });
    expect(scroll.querySelector('[data-sidebar-group]')).toBeNull();
    expect(navKeys(scroll)).toEqual(['', 'unseen', 'follow']);
  });
});

describe('常驻组件', () => {
  it('句柄 render 与 navChanged 返回时 DOM 已画好，不等下一次渲染', async () => {
    const { scroll, api, press } = await setup();
    /* 不包 act：act 会把 flushSync 推到它结束时，看不出句柄自己同步没有。 */
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    api.render({ content: { kind: 'catalog', key: '1', facets: facets() }, filters: { tag: 'a' }, latest: null });
    expect(chipOf(scroll, 'tag', 'a')!.getAttribute('aria-pressed')).toBe('true');
    press('unseen');
    api.navChanged();
    expect(scroll.querySelector('[data-nav="unseen"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(scroll.querySelector('[data-nav=""]')!.getAttribute('aria-pressed')).toBe('false');
  });

  it('壳挪进标题行的品牌与开合键经重画、换按下态与重排仍是挪进去的那两个节点', async () => {
    const brand = document.createElement('a'), toggle = document.createElement('button');
    const place = () => { document.querySelector('[data-sidebar-scroll] > [data-sidebar-head]')!.append(brand, toggle) };
    const { scroll, store, api, render, press } = await setup(undefined, undefined, place);
    const head = scroll.querySelector(':scope > [data-sidebar-head]')!;
    expect([...head.children]).toEqual([brand, toggle]);
    render({ content: { kind: 'catalog', key: '1', facets: facets() }, filters: { tag: 'a' } });
    render({ content: { kind: 'follow', tags: [chip('y', 3)], selected: [] } });
    press('unseen');
    act(() => { api.navChanged() });
    act(() => { store.value.sidebarOrder = ['follow', 'unseen', '']; store.save() });
    render({ content: null });
    expect(scroll.querySelector(':scope > [data-sidebar-head]')).toBe(head);
    expect([...head.children]).toEqual([brand, toggle]);
  });

  it('抛错只卸组件、滚动层还在，批量条照画；之后句柄是空操作、不抛，这一面空到刷新为止，只报一次', async () => {
    const reported = watchReports();
    const { r, drawer, scroll, api, render } = await setup();
    const dockRoot = document.createElement('div');
    dockRoot.setAttribute('data-batch-dock', '');
    document.body.append(dockRoot);
    let dock!: BatchDockApi;
    await act(async () => { dock = await r.islands.loadBatchDock({ root: dockRoot, run: vi.fn() }) });

    glyph.broken = true;
    render({ content: { kind: 'catalog', key: '1', facets: facets() } });
    await act(async () => {});
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenCalledWith(expect.objectContaining({ message: '侧栏画不出来' }));
    expect(scroll.isConnected && scroll.parentElement === drawer, '滚动层是壳的节点，不撤').toBe(true);
    expect(scroll.childNodes.length, '组件卸掉了').toBe(0);
    expect(drawer.querySelector('[data-sidebar-glide]'), '玻璃随组件一起卸').toBeNull();
    expect(r.managedTaken(scroll)).toBe(false);
    expect(r.managedEntries().map((entry) => entry.path), '别的面照画').toEqual(['batch-dock']);
    await act(async () => { dock.render({ count: 2, context: 'catalog', junkDismissed: false }) });
    expect(dockRoot.querySelector('[data-selection-dock] [role="status"]')?.textContent).toBe('已选 2 项');

    /* 壳照旧每次换页都推一份、换按下态：不抛、不重开、不再上报。 */
    await act(async () => {
      expect(() => api.render({ content: null, filters: {}, latest: null })).not.toThrow();
      expect(() => api.navChanged()).not.toThrow();
    });
    expect(reported, '确定性抛错只报那一次').toHaveBeenCalledTimes(1);
    glyph.broken = false;
    render({ content: null });
    expect(scroll.childNodes.length, '空到刷新为止，不自愈').toBe(0);
  });
});
