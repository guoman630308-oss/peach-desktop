/* 批量条：四种语境下列哪几颗键、计数、选空时收起、点下去回到壳的哪条流程，以及它作为常驻组件
 * （`RESIDENT_ROUTES['batch-dock']`）在路由树里的行为：宿主就是 `[data-batch-dock]`、句柄同步画完、
 * 收起与抛错都只卸组件不撤宿主，抛错之后这一面空到刷新为止。
 *
 * 浮条的玻璃、位置与窄屏三列栅格要量布局，由 e2e 在浏览器里走（`e2e/design-*.test.ts`）。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { batchActions, type BatchDockApi, type BatchDockHost, type BatchDockProps } from '../../src/react/batch-dock/batch-dock-api';
import type { ShellActions } from '../../src/react/router/shell-actions';
import { click } from './render';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

type ActFlag = { IS_REACT_ACT_ENVIRONMENT?: boolean };

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

/* 浮条外壳换成一层能当场决定抛不抛的包装：用例中途改 `dock.broken`，就是「下一次绘制抛错」。 */
const dock = vi.hoisted(() => ({ broken: false }));
vi.mock(import('../../src/react/components/selection-dock'), async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    SelectionDock: (props: Parameters<typeof actual.SelectionDock>[0]) => {
      if (dock.broken) throw new Error('浮条画不出来');
      return actual.SelectionDock(props);
    },
  };
});

/* 常驻组件适配层直接读取同一包内的配置函数；测试只接本场景的句柄契约。 */
type ShellIslands = { connectApplication(next: (actions: ShellActions) => void): () => void; loadBatchDock(host: BatchDockHost): Promise<BatchDockApi>; batchDockApi(): BatchDockApi | null };
const ISLANDS_MODULE = '../../src/react/application-residents';

async function load() {
  vi.resetModules();
  window.history.replaceState(null, '', '/');
  const [history, router, routes, island, islands] = await Promise.all([
    import('../../src/history'), import('../../src/react/router/router'), import('../../src/react/router/managed-routes'),
    import('../../src/react/batch-dock/batch-dock-island'), import(/* @vite-ignore */ ISLANDS_MODULE) as Promise<ShellIslands>,
  ]);
  return { ...history, ...router, ...routes, ...island, islands };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = true;
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.innerHTML = '';
  dock.broken = false;
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

/** 壳启动时的顺序：先画路由树（根的选项与 `mountApplication` 同一份），再接上取数。 */
async function mountRouter(r: Loaded) {
  unmounts.push(r.islands.connectApplication(() => {}));
  const root = createRoot(document.createElement('div'), r.ROUTER_ROOT_OPTIONS);
  await act(async () => { root.render(<r.RouterRoot actions={shellActions()} />) });
  unmounts.push(() => root.unmount());
  await act(async () => { r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute)) });
}

/** 壳 `index.html` 里那个常驻宿主。 */
function dockHost(): BatchDockHost {
  const root = document.createElement('div');
  root.setAttribute('data-batch-dock', '');
  document.body.append(root);
  return { root, run: vi.fn() };
}

/** 照壳的 `loadBatchDock` 接上：交出句柄，再在路由树里打开这一面。 */
async function setup() {
  const r = await load();
  await mountRouter(r);
  const host = dockHost();
  const api: BatchDockApi = r.configureBatchDock(host);
  await act(async () => { expect(await r.openResidentSurface('batch-dock', host.root)).toBe(true) });
  const render = async (props: BatchDockProps) => { await act(async () => { api.render(props) }) };
  return { r, root: host.root, host, api, render };
}

/** 收下每一次上报；`console.error` 只静音，不数条数。 */
function watchReports() {
  const reported = vi.fn();
  vi.stubGlobal('reportError', reported);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  return reported;
}

const labels = (root: ParentNode) => [...root.querySelectorAll('[data-selection-dock] button')].map((node) => node.textContent?.trim());

describe('每种语境的键', () => {
  it('从左到右的顺序，「取消」总在最后，危险操作只有移出与彻底删除', () => {
    const shape = (context: Parameters<typeof batchActions>[0], dismissed = false) =>
      batchActions(context, dismissed).map((action) => `${action.label}${action.danger ? '!' : ''}`);
    expect(shape('catalog')).toEqual(['喜欢', '看过', '稍后看', '判定产地', '移入回收站!', '取消']);
    expect(shape('trash')).toEqual(['还原', '彻底删除!', '取消']);
    expect(shape('junk')).toEqual(['不是垃圾', '移入回收站!', '取消']);
    expect(shape('junk', true)).toEqual(['重新判断', '移入回收站!', '取消']);
    expect(shape('follow')).toEqual(['保存到数据库', '标记已看', '忽略', '取消']);
  });

  it('「忽略」用划掉的眼睛，不借「取消」那枚叉', () => {
    const glyph = (operation: string) => batchActions('follow').find((action) => action.operation === operation)!.glyph;
    expect([glyph('ignored'), glyph('clear')]).toEqual(['eye-off', 'x']);
  });
});

describe('浮条', () => {
  it('没选中时不画，选中后显示计数与这一语境的键，换语境当场换键', async () => {
    const { root, render } = await setup();
    expect(root.querySelector('[data-selection-dock]')).toBeNull();
    await render({ count: 2, context: 'trash', junkDismissed: false });
    const shown = root.querySelector('[data-selection-dock]')!;
    expect(shown.getAttribute('aria-label')).toBe('所选项目操作');
    expect(shown.querySelector('[role="status"]')!.textContent).toBe('已选 2 项');
    expect(labels(root)).toEqual(['还原', '彻底删除', '取消']);
    await render({ count: 3, context: 'junk', junkDismissed: true });
    expect(root.querySelector('[data-selection-dock] [role="status"]')!.textContent).toBe('已选 3 项');
    expect(labels(root)).toEqual(['重新判断', '移入回收站', '取消']);
    expect(root.querySelector('[data-batch-scope]')!.getAttribute('data-context')).toBe('junk');
  });

  it('上千项的计数带千分位', async () => {
    const { root, render } = await setup();
    await render({ count: 12345, context: 'catalog', junkDismissed: false });
    expect(root.querySelector('[data-selection-dock] [role="status"]')!.textContent).toBe('已选 12,345 项');
  });

  it('危险键挂 data-danger 读全站那份红，每颗键带自己的 sprite 字形', async () => {
    const { root, render } = await setup();
    await render({ count: 2, context: 'catalog', junkDismissed: false });
    const keys = [...root.querySelectorAll<HTMLButtonElement>('[data-selection-dock] button')];
    expect(keys.filter((key) => key.hasAttribute('data-danger')).map((key) => key.dataset.batchAction)).toEqual(['dispose']);
    expect(keys.map((key) => key.querySelector('use')?.getAttribute('href')))
      .toEqual(['#i-thumbs-up', '#i-eye', '#i-bookmark-plus', '#i-globe', '#i-trash', '#i-x']);
  });

  it('点一颗键把分组、操作与这颗键交回壳', async () => {
    const { root, host, render } = await setup();
    await render({ count: 1, context: 'catalog', junkDismissed: false });
    const region = root.querySelector<HTMLButtonElement>('[data-batch-action="region"]')!;
    await click(region);
    expect(host.run).toHaveBeenCalledWith('region', 'region', region);
    await click(root.querySelector('[data-batch-action="dispose"]'));
    expect(host.run).toHaveBeenLastCalledWith('batch', 'dispose', expect.any(HTMLButtonElement));
    await click(root.querySelector('[data-batch-group="clear"]'));
    expect(host.run).toHaveBeenLastCalledWith('clear', 'clear', expect.any(HTMLButtonElement));
  });
});

describe('常驻组件', () => {
  it('宿主就是 [data-batch-dock] 本身：组件那层 .peach-react 是它的直接子节点，中间不多包一层', async () => {
    const { root } = await setup();
    expect([...root.children].map((child) => [child.className, child.getAttribute('data-context')]))
      .toEqual([['peach-react', 'catalog']]);
  });

  it('句柄 render 返回时浮条已经画好，不等下一次渲染', async () => {
    const { root, api } = await setup();
    /* 不包 act：act 会把 flushSync 推到它结束时，看不出句柄自己同步没有。 */
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    api.render({ count: 4, context: 'follow', junkDismissed: false });
    const status = root.querySelector('[data-selection-dock] [role="status"]')?.textContent;
    const context = root.querySelector('[data-batch-scope]')?.getAttribute('data-context');
    expect([status, context]).toEqual(['已选 4 项', 'follow']);
  });

  it('壳的 loadBatchDock 在这一面画上之后才交出句柄', async () => {
    const r = await load();
    await mountRouter(r);
    const host = dockHost();
    expect(r.islands.batchDockApi(), '常驻组件尚未挂载时没有句柄，应用只记着当前那份').toBeNull();
    let api: BatchDockApi | null = null;
    await act(async () => { api = await r.islands.loadBatchDock(host) });
    expect(r.islands.batchDockApi()).toBe(api);
    expect(r.managedEntry(host.root)?.path).toBe('batch-dock');
    expect(host.root.querySelector(':scope > [data-batch-scope]')).not.toBeNull();
    await act(async () => { api!.render({ count: 1, context: 'catalog', junkDismissed: false }) });
    expect(host.root.querySelector('[data-selection-dock] [role="status"]')?.textContent).toBe('已选 1 项');
  });

  it('收起只卸组件，宿主留在文档里', async () => {
    const { r, root, render } = await setup();
    await render({ count: 2, context: 'catalog', junkDismissed: false });
    await act(async () => { r.releaseManagedRoute(root) });
    expect(root.isConnected, '宿主是壳的节点').toBe(true);
    expect(root.childNodes.length).toBe(0);
    expect(r.managedTaken(root)).toBe(false);
  });

  it('抛错只卸组件、宿主还在，别的面照画；之后句柄是空操作、不抛，这一面空到刷新为止，只报一次', async () => {
    const reported = watchReports();
    const { r, root, api, render } = await setup();
    const menu = document.createElement('div');
    menu.id = 'boardGlowMenu';
    document.body.append(menu);
    await act(async () => { expect(await r.openResidentSurface('glow-picker', menu)).toBe(true) });
    await render({ count: 2, context: 'catalog', junkDismissed: false });

    dock.broken = true;
    await render({ count: 3, context: 'catalog', junkDismissed: false });
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenCalledWith(expect.objectContaining({ message: '浮条画不出来' }));
    expect(root.isConnected, '宿主是壳的节点，不撤').toBe(true);
    expect(root.childNodes.length, '组件卸掉了').toBe(0);
    expect(r.managedTaken(root)).toBe(false);
    expect(r.managedEntries().map((entry) => entry.path), '别的面照画').toEqual(['glow-picker']);

    /* 壳照旧每次重画选中态都推一份：不抛、不重开、不再上报。 */
    for (const count of [4, 5, 6]) {
      await act(async () => { expect(() => api.render({ count, context: 'trash', junkDismissed: false })).not.toThrow() });
    }
    expect(reported, '确定性抛错只报那一次').toHaveBeenCalledTimes(1);
    dock.broken = false;
    await render({ count: 7, context: 'catalog', junkDismissed: false });
    expect(root.childNodes.length, '空到刷新为止，不自愈').toBe(0);
    expect(r.managedTaken(root)).toBe(false);
  });
});
