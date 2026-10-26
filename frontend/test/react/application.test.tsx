import { act, StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ShellActions } from '../../src/react/router/shell-actions';

const boot = vi.hoisted(() => ({ initialize: vi.fn(), truncate: vi.fn(() => () => {}) }));
vi.mock('../../src/application/initialize.js', () => ({ initializeApplication: boot.initialize }));
vi.mock('../../src/ui-kit/middle-truncate', () => ({ initMiddleTruncate: boot.truncate }));
const roots = new Set<Root>();
let runtime: Awaited<ReturnType<typeof load>>;

beforeEach(async () => {
  vi.resetModules(); boot.initialize.mockReset(); boot.truncate.mockClear();
  window.history.replaceState(null, '', '/nowhere');
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  runtime = await load();
}, 30_000);
afterEach(async () => {
  for (const root of roots) await act(async () => { root.unmount(); });
  roots.clear(); document.body.innerHTML = ''; vi.useRealTimers(); vi.restoreAllMocks();
});

async function load() {
  const [app, residents, effects, shell, menus] = await Promise.all([
    import('../../src/react/application'), import('../../src/react/application-residents'),
    import('../../src/application/effects'), import('../../src/shell'), import('../../src/ui-kit/anchored-menu'),
  ]);
  return { ...app, residents, effects, shell, menus };
}
function host() { const node = document.createElement('div'); document.body.append(node); return node; }

function openNativeMenu(menus: Awaited<ReturnType<typeof load>>['menus']) {
  const mount = document.createElement('div'), toggle = document.createElement('button'), menu = document.createElement('div');
  menu.hidden = true; mount.append(toggle, menu); document.body.append(mount);
  const add = vi.spyOn(window, 'addEventListener'), start = add.mock.calls.length;
  const measure = vi.spyOn(toggle, 'getBoundingClientRect');
  const control = menus.wireAnchoredMenu(mount, toggle, menu); control.setOpen(true);
  const installed = add.mock.calls.slice(start);
  const resize = installed.find(([type]) => type === 'resize')?.[1];
  const scroll = installed.find(([type]) => type === 'scroll')?.[1];
  expect(resize).toBeDefined(); expect(scroll).toBeDefined();
  return { control, toggle, measure, resize, scroll };
}

function actions(): ShellActions {
  return {
    openItem: vi.fn(), openEntity: vi.fn(), openTag: vi.fn(), openTasteSignal: vi.fn(), navigate: vi.fn(),
    openManage: vi.fn(), managePath: vi.fn(() => ''), openFollow: vi.fn(), receipt: vi.fn(), toast: vi.fn(),
    failure: vi.fn(), revealSource: vi.fn(async () => ''), reopenTutorial: vi.fn(async () => {}),
    requestConfigurationSection: vi.fn(), routeReview: vi.fn(), routeFollowManage: vi.fn(),
    saveFollowPreference: vi.fn(), srcBadge: () => '', routeIndex: vi.fn(), savePeopleLayout: vi.fn(),
    exitSelectMode: vi.fn(), personAvatar: vi.fn(() => ({ html: '', face: '' })), authorAvatar: vi.fn(() => ''),
    showIndexTags: vi.fn(), openFollowAuthor: vi.fn(), openFollowTag: vi.fn(), openPlaylist: vi.fn(),
    canFlip: vi.fn(() => true), surfaceChanged: vi.fn(), clearSearch: vi.fn(), openImmerse: vi.fn(),
    openOverlay: vi.fn(), closeStage: vi.fn(), grid: {} as ShellActions['grid'],
  };
}

it('同宿主重复挂载沿用实际 Application，一次后退只打开一次覆盖', async () => {
  const r = runtime, node = host(), capabilities = actions();
  boot.initialize.mockImplementation(() => { void r.residents.loadRouter(capabilities); });
  let first!: Root, second!: Root;
  await act(async () => { first = r.mountApplication(node); second = r.mountApplication(node); });
  roots.add(first); expect(second).toBe(first); expect(boot.initialize).toHaveBeenCalledTimes(1);
  r.shell.writeShell({ pageOpens: 1 });
  await act(async () => {
    window.history.replaceState({ usr: { backgroundLocation: { pathname: '/nowhere', search: '' }, overlay: 'item' }, key: 'x', idx: 1 }, '', '/assets/7');
    window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }));
  });
  await act(async () => { await new Promise(resolve => { setTimeout(resolve, 0); }); });
  expect(vi.mocked(capabilities.openOverlay).mock.calls).toEqual([[{ kind: 'item', id: 7 }]]);
});

it('实际卸载后立即重挂同宿主重新初始化，旧资源信号已取消', async () => {
  const r = runtime, node = host();
  const remove = vi.spyOn(window, 'removeEventListener'), menus: ReturnType<typeof openNativeMenu>[] = [];
  boot.initialize.mockImplementation(() => { menus.push(openNativeMenu(r.menus)); });
  let first!: Root, second!: Root, oldSignal!: AbortSignal;
  await act(async () => {
    first = r.mountApplication(node); oldSignal = r.effects.applicationEffects.signal;
    first.unmount(); second = r.mountApplication(node);
  });
  roots.add(second); expect(second).not.toBe(first);
  expect(boot.initialize).toHaveBeenCalledTimes(2); expect(oldSignal.aborted).toBe(true);
  const [oldMenu, newMenu] = menus;
  expect(oldMenu!.control.isOpen()).toBe(false); expect(oldMenu!.toggle.getAttribute('aria-expanded')).toBe('false');
  expect(remove.mock.calls.filter(([type, callback]) => type === 'resize' && callback === oldMenu!.resize)).toHaveLength(1);
  expect(remove.mock.calls.filter(([type, callback, capture]) => type === 'scroll' && callback === oldMenu!.scroll && capture === true)).toHaveLength(1);
  expect(newMenu!.control.isOpen()).toBe(true);
  const oldMeasurements = oldMenu!.measure.mock.calls.length, newMeasurements = newMenu!.measure.mock.calls.length;
  window.dispatchEvent(new Event('resize'));
  expect(oldMenu!.measure.mock.calls.length).toBe(oldMeasurements);
  expect(newMenu!.measure.mock.calls.length).toBe(newMeasurements + 1);
  await act(async () => { second.unmount(); }); roots.delete(second);
  expect(newMenu!.control.isOpen()).toBe(false);
  expect(remove.mock.calls.filter(([type, callback]) => type === 'resize' && callback === newMenu!.resize)).toHaveLength(1);
  expect(remove.mock.calls.filter(([type, callback, capture]) => type === 'scroll' && callback === newMenu!.scroll && capture === true)).toHaveLength(1);
});

it('StrictMode 诊断重取仅初始化一次，最终卸载移除监听和调度', async () => {
  vi.useFakeTimers();
  const r = runtime, node = host(), heard = vi.fn(), timer = vi.fn();
  const remove = vi.spyOn(window, 'removeEventListener');
  let menu!: ReturnType<typeof openNativeMenu>;
  let signal!: AbortSignal;
  boot.initialize.mockImplementation(() => {
    const effects = r.effects.applicationEffects; signal = effects.signal;
    effects.listen(document, 'application-probe', heard); effects.delay(timer, 100);
    menu = openNativeMenu(r.menus);
  });
  const root = createRoot(node); roots.add(root);
  await act(async () => { root.render(<StrictMode><r.Application /></StrictMode>); });
  expect(boot.initialize).toHaveBeenCalledTimes(1);
  expect(menu.control.isOpen()).toBe(true);
  expect(remove.mock.calls.filter(([type, callback]) => type === 'resize' && callback === menu.resize)).toHaveLength(0);
  expect(remove.mock.calls.filter(([type, callback]) => type === 'scroll' && callback === menu.scroll)).toHaveLength(0);
  document.dispatchEvent(new Event('application-probe')); expect(heard).toHaveBeenCalledTimes(1);
  await act(async () => { root.unmount(); }); roots.delete(root);
  document.dispatchEvent(new Event('application-probe')); vi.runAllTimers();
  expect(heard).toHaveBeenCalledTimes(1); expect(timer).not.toHaveBeenCalled(); expect(signal.aborted).toBe(true);
  expect(menu.control.isOpen()).toBe(false); expect(menu.toggle.getAttribute('aria-expanded')).toBe('false');
  expect(remove.mock.calls.filter(([type, callback]) => type === 'resize' && callback === menu.resize)).toHaveLength(1);
  expect(remove.mock.calls.filter(([type, callback, capture]) => type === 'scroll' && callback === menu.scroll && capture === true)).toHaveLength(1);
});
