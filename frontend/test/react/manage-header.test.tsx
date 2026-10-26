/* 管理区页头：骨架与路由树的换手、管理条的按下态与点击、面包屑左键走路由而带修饰键照链接走、回收站说明行
 * 先占位后落读数，同一页重画时读数那一格原地换字，以及它作为常驻组件（`RESIDENT_ROUTES['manage-header']`）在
 * 路由树里的行为：宿主就是 `[data-manage-header]`、骨架在首帧的同一个任务里换掉、句柄同步画完、抛错只卸组件。
 *
 * 指示线的滑动、标题居中与窄屏收档要量布局，由 e2e 在浏览器里走（`e2e/management-layout.test.ts`）。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { manageHeaderSkeletonHtml, pressedManageKey, type ManageEntry, type ManageHeaderProps } from '../../src/manage-header';
import type { BatchDockApi, BatchDockHost } from '../../src/react/batch-dock/batch-dock-api';
import type { ManageHeaderApi, ManageHeaderHost } from '../../src/react/manage-header/manage-header-api';
import type { ShellActions } from '../../src/react/router/shell-actions';
import { click } from './render';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

type ActFlag = { IS_REACT_ACT_ENVIRONMENT?: boolean };

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

/* 页签与面包屑的字形换成一层能当场决定抛不抛的包装：用例中途改 `glyph.broken`，就是「下一次绘制抛错」。 */
const glyph = vi.hoisted(() => ({ broken: false }));
vi.mock(import('../../src/react/settings-panel/icon'), async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    Icon: (props: Parameters<typeof actual.Icon>[0]) => {
      if (glyph.broken) throw new Error('页头画不出来');
      return actual.Icon(props);
    },
  };
});

/* 常驻组件适配层直接读取同一包内的配置函数；测试只接本场景的句柄契约。 */
type ShellIslands = { connectApplication(next: (actions: ShellActions) => void): () => void;
  loadManageHeader(host: ManageHeaderHost): Promise<ManageHeaderApi>;
  manageHeaderApi(): ManageHeaderApi | null;
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
  vi.useRealTimers();
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

const SECTIONS: ManageEntry[] = [['stats', '统计', 'chart'], ['cleanup', '数据管理', 'database'], ['trash', '回收站', 'trash']];
const MENU = SECTIONS.filter(([key]) => key !== 'trash');

const props = (section: string, path: string, patch: Partial<ManageHeaderProps> = {}): ManageHeaderProps =>
  ({ section, path, sections: SECTIONS, menu: MENU, trash: null, ...patch });

/** 壳 `index.html` 里那个常驻宿主，壳启动时同步写进骨架。 */
function headerHost(first: ManageHeaderProps): ManageHeaderHost {
  const root = document.createElement('div');
  root.setAttribute('data-manage-header', '');
  document.body.append(root);
  root.innerHTML = manageHeaderSkeletonHtml(first);
  return { root, openManage: vi.fn(), openDataCleanup: vi.fn(), emptyTrash: vi.fn() };
}

/** 照壳的 `mountManageHeader` 接上：铺骨架、装载，拿到句柄就把手上那份推进去。 */
async function setup(first: ManageHeaderProps) {
  const r = await load();
  await mountRouter(r);
  await connect(r);
  const host = headerHost(first);
  let api!: ManageHeaderApi;
  await act(async () => { api = await r.islands.loadManageHeader(host) });
  const render = (next: ManageHeaderProps) => act(() => { api.render(next) });
  render(first);
  return { r, root: host.root, host, api, render };
}

/** 收下每一次上报；`console.error` 只静音，不数条数。 */
function watchReports() {
  const reported = vi.fn();
  vi.stubGlobal('reportError', reported);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  return reported;
}

const title = (root: ParentNode) => root.querySelector('[data-manage-title]')?.textContent;

describe('换手', () => {
  it('预取未接上时宿主里是骨架、句柄为 null；画上之后句柄才交出，画出来的是同一份结构', async () => {
    const first = props('cleanup', '/junk-files');
    const skeleton = document.createElement('div');
    skeleton.innerHTML = manageHeaderSkeletonHtml(first);
    const r = await load();
    await mountRouter(r);
    const host = headerHost(first);
    const html = host.root.innerHTML;
    let loaded: Promise<ManageHeaderApi> | null = null;
    await act(async () => { loaded = r.islands.loadManageHeader(host) });
    expect(host.root.innerHTML, '路由树还没接上取数，骨架原样').toBe(html);
    expect(r.islands.manageHeaderApi(), '画上之前没有句柄，壳继续接骨架上的点击').toBeNull();

    await connect(r);
    let api: ManageHeaderApi | null = null;
    await act(async () => { api = await loaded });
    expect(r.islands.manageHeaderApi()).toBe(api);
    expect(r.managedEntry(host.root)?.path).toBe('manage-header');
    expect(host.root.childNodes.length, '骨架清掉了，首帧没有 props、画成空').toBe(0);

    act(() => { api!.render(first) });
    const shape = (node: ParentNode) => [...node.querySelectorAll('*')]
      .map((el) => [el.tagName, ...[...el.attributes].map((a) => a.name).filter((name) => name.startsWith('data-') || name.startsWith('aria-')).sort()].join(' '))
      .filter((line) => !line.startsWith('USE'));
    expect(shape(host.root)).toEqual(shape(skeleton));
    expect(title(host.root)).toBe('垃圾文件');
  });

  it('骨架与首帧在同一个任务里换掉：每个任务边界上宿主里要么是骨架、要么是画好的页头，宿主上不多包 .peach-react', async () => {
    const first = props('stats', '/stats');
    const r = await load();
    await mountRouter(r);
    await connect(r);
    const host = headerHost(first);
    const skeletonTitle = host.root.querySelector('[data-manage-title]');
    /* 不包 act：act 会把 flushSync 推到它结束时，看不出换手发生在哪一拍。 */
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    let done = false;
    void r.islands.loadManageHeader(host).then((api) => { api.render(first); done = true });
    const state = () => (host.root.contains(skeletonTitle) ? 'skeleton' : title(host.root) ? 'header' : 'empty');
    const seen: string[] = [];
    for (let turn = 0; turn < 200 && !done; turn += 1) {
      seen.push(state());
      await new Promise((resolve) => { setTimeout(resolve, 0) });
    }
    seen.push(state());
    expect(done).toBe(true);
    expect(seen).not.toContain('empty');
    expect(seen.at(-1)).toBe('header');
    expect(title(host.root)).toBe('统计');
    expect(host.root.querySelector(':scope > .peach-react')).toBeNull();
    expect(host.root.firstElementChild?.hasAttribute('data-manage-bar')).toBe(true);
  });

  it('骨架阶段的点击由壳的委托接，句柄交出之后归组件，一次点击只到一处', async () => {
    const r = await load();
    await mountRouter(r);
    const host = headerHost(props('stats', '/stats'));
    /* 壳 `mountManageHeader` 里那一段委托：句柄还没交出时才认。 */
    const delegated = vi.fn();
    host.root.addEventListener('click', (event) => {
      if (r.islands.manageHeaderApi()) return;
      const tab = (event.target as Element).closest<HTMLElement>('[data-manage]');
      if (tab) delegated(tab.dataset.manage);
    });
    let loaded: Promise<ManageHeaderApi> | null = null;
    await act(async () => { loaded = r.islands.loadManageHeader(host) });
    await click(host.root.querySelector('[data-manage="cleanup"]'));
    expect(delegated.mock.calls).toEqual([['cleanup']]);
    expect(host.openManage).not.toHaveBeenCalled();

    await connect(r);
    let api: ManageHeaderApi | null = null;
    await act(async () => { api = await loaded });
    act(() => { api!.render(props('cleanup', '/data-cleanup')) });
    await click(host.root.querySelector('[data-manage="stats"]'));
    expect(vi.mocked(host.openManage).mock.calls).toEqual([['stats']]);
    expect(delegated, '委托不再认').toHaveBeenCalledTimes(1);
  });

  it('离开管理区时整块清空，回来再画', async () => {
    const { root, render } = await setup(props('stats', '/stats'));
    render(props('', '/'));
    expect(root.childElementCount).toBe(0);
    render(props('cleanup', '/data-cleanup'));
    expect(title(root)).toBe('数据管理');
    expect(root.querySelector('[data-manage-title]')!.hasAttribute('data-compact')).toBe(true);
    expect(root.querySelector('[data-manage-crumb]')).toBeNull();
  });
});

describe('管理条与面包屑', () => {
  it('按下项跟着当前区走，点一项回到壳的 openManage', async () => {
    const { root, host, render } = await setup(props('stats', '/stats'));
    const pressed = () => [...root.querySelectorAll('[data-manage][aria-pressed="true"]')].map((node) => node.getAttribute('data-manage'));
    expect(pressed()).toEqual(['stats']);
    expect(root.querySelector('[data-manage="stats"]')!.getAttribute('aria-current')).toBe('page');
    render(props('cleanup', '/data-cleanup'));
    expect(pressed()).toEqual(['cleanup']);
    expect(root.querySelector('[data-manage="stats"]')!.hasAttribute('aria-current')).toBe(false);
    await click(root.querySelector('[data-manage="stats"]'));
    expect(host.openManage).toHaveBeenCalledWith('stats');
  });

  it('数据管理之下的子页按下「数据管理」，骨架与组件同一个判据；管理条上没有的区不按下任何一项', async () => {
    const first = props('trash', '/trash');
    const { root, render } = await setup(first);
    const pressed = () => [...root.querySelectorAll('[data-manage][aria-pressed="true"]')].map((node) => node.getAttribute('data-manage'));
    expect(pressed()).toEqual(['cleanup']);
    expect(manageHeaderSkeletonHtml(first)).toContain('data-manage="cleanup" aria-pressed="true"');
    render(props('cleanup', '/quality-goals'));
    expect(pressed()).toEqual(['cleanup']);
    render(props('stats', '/stats'));
    expect(pressed()).toEqual(['stats']);
    expect(pressedManageKey({ section: 'trash', path: '/somewhere', menu: MENU })).toBe('');
  });

  it('面包屑普通左键走路由，带修饰键或中键的点击照链接自己走', async () => {
    const { root, host } = await setup(props('cleanup', '/duplicates'));
    const link = root.querySelector<HTMLAnchorElement>('[data-manage-crumb] a')!;
    expect(link.getAttribute('href')).toBe('/data-cleanup');
    expect(root.querySelector('[data-manage-crumb] [aria-current="true"]')!.textContent).toBe('重复文件');
    const fire = (init: MouseEventInit) => {
      const event = new MouseEvent('click', { bubbles: true, cancelable: true, ...init });
      act(() => { link.dispatchEvent(event) });
      return event.defaultPrevented;
    };
    expect(fire({})).toBe(true);
    expect(host.openDataCleanup).toHaveBeenCalledTimes(1);
    expect([fire({ ctrlKey: true }), fire({ metaKey: true }), fire({ shiftKey: true }), fire({ button: 1 })])
      .toEqual([false, false, false, false]);
    expect(host.openDataCleanup).toHaveBeenCalledTimes(1);
  });
});

describe('回收站说明行', () => {
  it('读数到之前铺同形占位，读到以后落读数与清空键；说明行一直是同一个节点', async () => {
    const { root, host, render } = await setup(props('trash', '/trash'));
    const lede = root.querySelector('[data-manage-lede]')!;
    expect(lede.querySelectorAll('[data-trash-lede-skeleton]')).toHaveLength(2);
    expect(lede.querySelector('[data-lede-text]')).toBeNull();
    render(props('trash', '/trash', { trash: { total: 1234, shown: 8 } }));
    expect(root.querySelector('[data-manage-lede]')).toBe(lede);
    expect(lede.querySelector('[data-trash-lede-skeleton]')).toBeNull();
    expect(lede.querySelector('[data-lede-text]')!.textContent).toBe('1,234 个符合 · 显示 8');
    await click(lede.querySelector('[data-empty-trash]'));
    expect(host.emptyTrash).toHaveBeenCalledTimes(1);
    render(props('trash', '/trash', { trash: { total: 0, shown: 0 } }));
    expect(lede.querySelector('[data-empty-trash]')).toBeNull();
  });

  it('同一页重画：读数那一格沿用原节点，旧字淡出后换成新字，不回到占位', async () => {
    const { root, render } = await setup(props('trash', '/trash', { trash: { total: 20, shown: 8 } }));
    vi.useFakeTimers();
    const cell = root.querySelector<HTMLElement>('[data-lede-text]')!;
    render(props('trash', '/trash', { trash: { total: 20, shown: 16 } }));
    expect(root.querySelector('[data-lede-text]')).toBe(cell);
    expect(root.querySelector('[data-trash-lede-skeleton]')).toBeNull();
    expect(cell.classList.contains('leaving')).toBe(true);
    expect(cell.textContent).toBe('20 个符合 · 显示 8');
    act(() => { vi.advanceTimersByTime(400) });
    expect(cell.textContent).toBe('20 个符合 · 显示 16');
    expect(cell.classList.contains('leaving')).toBe(false);
  });
});

describe('常驻组件', () => {
  it('句柄 render 返回时标题已是新内容，不等下一次渲染', async () => {
    const { root, api } = await setup(props('stats', '/stats'));
    /* 不包 act：act 会把 flushSync 推到它结束时，看不出句柄自己同步没有。 */
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    api.render(props('cleanup', '/junk-files'));
    expect(title(root)).toBe('垃圾文件');
    api.render(props('trash', '/trash'));
    expect([title(root), root.querySelectorAll('[data-trash-lede-skeleton]').length]).toEqual(['回收站', 2]);
  });

  it('抛错只卸组件、宿主还在，批量条照画；之后句柄是空操作、不抛，这一面空到刷新为止，只报一次', async () => {
    const reported = watchReports();
    const { r, root, api, render } = await setup(props('stats', '/stats'));
    const dockRoot = document.createElement('div');
    dockRoot.setAttribute('data-batch-dock', '');
    document.body.append(dockRoot);
    let dock!: BatchDockApi;
    await act(async () => { dock = await r.islands.loadBatchDock({ root: dockRoot, run: vi.fn() }) });

    glyph.broken = true;
    render(props('cleanup', '/data-cleanup'));
    await act(async () => {});
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenCalledWith(expect.objectContaining({ message: '页头画不出来' }));
    expect(root.isConnected, '宿主是壳的节点，不撤').toBe(true);
    expect(root.childNodes.length, '组件卸掉了').toBe(0);
    expect(r.managedTaken(root)).toBe(false);
    expect(r.managedEntries().map((entry) => entry.path), '别的面照画').toEqual(['batch-dock']);
    await act(async () => { dock.render({ count: 2, context: 'catalog', junkDismissed: false }) });
    expect(dockRoot.querySelector('[data-selection-dock] [role="status"]')?.textContent).toBe('已选 2 项');

    /* 壳照旧每次换页都推一份：不抛、不重开、不再上报。 */
    for (const next of [props('stats', '/stats'), props('trash', '/trash')]) {
      await act(async () => { expect(() => api.render(next)).not.toThrow() });
    }
    expect(reported, '确定性抛错只报那一次').toHaveBeenCalledTimes(1);
    glyph.broken = false;
    render(props('stats', '/stats'));
    expect(root.childNodes.length, '空到刷新为止，不自愈').toBe(0);
  });
});
