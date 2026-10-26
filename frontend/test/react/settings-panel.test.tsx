/* 设置面板（常驻组件 `settings-panel`）与壳之间的接缝：每一项改完只落一次盘、只报自己那一个效果名，
 * 拉条拖动中只排重画、松手才落盘，面板 DOM 只建一次；以及它作为常驻组件（`RESIDENT_ROUTES['settings-panel']`）
 * 在路由树里的行为：宿主 `[data-settings-host]` 第一次打开时放进 body 末尾、不包 `.peach-react`，画上之后句柄
 * 才交出，`open` 返回时面板已经画好，抛错只卸组件。开合动效、焦点圈、写接口与像素在真浏览器里验，见
 * `e2e/settings-panel.test.ts` 与 `e2e/design-*.test.ts`。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { notifyManager } from '@tanstack/react-query';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import { normalizeHomeGlow } from '@peach/appearance';

import type { BatchDockApi, BatchDockHost } from '../../src/react/batch-dock/batch-dock-api';
import type { ShellActions } from '../../src/react/router/shell-actions';
import type {
  PanelSettings, SettingsEffect, SettingsPanelApi, SettingsPanelHost,
} from '../../src/react/settings-panel/settings-panel-api';
import { createSettingsStore } from '../../src/settings-store';
import { FOLLOW_INITIAL_RANGE_OPTIONS } from '../../src/application/initial-follow-ranges';

import { click, settle } from './render';

notifyManager.setScheduler((notify) => notify());

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

type ActFlag = { IS_REACT_ACT_ENVIRONMENT?: boolean };

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

/* 左栏分区的字形换成一层能当场决定抛不抛的包装：用例中途改 `glyph.broken`，就是「下一次绘制抛错」。 */
const glyph = vi.hoisted(() => ({ broken: false }));
vi.mock(import('../../src/react/settings-panel/icon'), async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    RemixIcon: (props: Parameters<typeof actual.RemixIcon>[0]) => {
      if (glyph.broken) throw new Error('设置面板画不出来');
      return actual.RemixIcon(props);
    },
  };
});

/* 常驻组件适配层直接读取同一包内的配置函数；测试只接本场景的句柄契约。 */
type ShellIslands = { connectApplication(next: (actions: ShellActions) => void): () => void;
  loadSettingsPanel(host: SettingsPanelHost): Promise<SettingsPanelApi>;
  settingsPanelApi(): SettingsPanelApi | null;
  loadBatchDock(host: BatchDockHost): Promise<BatchDockApi>;
};
const ISLANDS_MODULE = '../../src/react/application-residents';

async function load() {
  vi.resetModules();
  window.history.replaceState(null, '', '/');
  const [history, router, routes, query, islands] = await Promise.all([
    import('../../src/history'), import('../../src/react/router/router'), import('../../src/react/router/managed-routes'),
    import('../../src/react/query'), import(/* @vite-ignore */ ISLANDS_MODULE) as Promise<ShellIslands>,
  ]);
  return { ...history, ...router, ...routes, queryClient: query.queryClient, islands };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
beforeEach(() => {
  /* 不放动画：关上当场收起。 */
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('reduce'), media: query, addEventListener: () => {}, removeEventListener: () => {},
  }));
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })));
});
afterEach(async () => {
  (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = true;
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.innerHTML = '';
  document.body.className = '';
  document.documentElement.style.overflow = '';
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
  unmounts.push(() => r.queryClient.clear());
}
async function connect(r: Loaded) {
  await act(async () => { r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute)) });
}

const settings = (): PanelSettings => ({
  theme: 'system', uiSounds: true, homeGlow: { ...normalizeHomeGlow(null), on: true, preset: 'ash' }, accent: 'blue',
  sidebarOrder: [''], batchSize: 60, defaultSort: 'seed', defaultSortDirection: '', groupCollapse: true,
  javImage: 'cover', feedAutoScroll: true, feedHideGroupCompilations: true, feedHideSoloCompilations: false,
  feedHideExcerpts: true, hoverDelaySeconds: 5, detailAutoplay: false, miniplayer: true, seekSeconds: 10,
  relatedLimit: 24, searchHistoryLimit: 10, followInitialDays: 30, avatarRefreshDays: 90, faviconRefreshDays: 365,
});

/** 壳 `settingsHost()` 交进来的那一份。 */
function panelHost() {
  const value = settings();
  const store = createSettingsStore('peach.settings.v1', value);
  const save = vi.spyOn(store, 'save') as unknown as Mock<() => void>;
  const changed = vi.fn<(effect: SettingsEffect) => void>();
  const setHighContrast = vi.fn<(on: boolean) => void>();
  const attached = vi.fn<(panel: HTMLElement) => void>();
  const host: SettingsPanelHost = {
    store, changed, sound: () => {},
    navCatalog: [['', '首页', 'house']],
    themeOptions: [['system', '跟随系统', 'monitor'], ['light', '浅色', 'sun'], ['dark', '深色', 'moon']],
    videoLayouts: [['small', '小'], ['large', '大']],
    followInitialRanges: FOLLOW_INITIAL_RANGE_OPTIONS,
    videoLayout: () => 'small', setVideoLayout: () => {},
    censored: () => false, setCensored: () => {}, highContrast: () => false, setHighContrast,
    receipt: () => {}, failure: () => {}, syncRemote: () => {}, openConfiguration: () => {}, attached,
    configurable: async () => false,
  };
  return { host, value, save, changed, setHighContrast, attached };
}

/** 照壳的 `openSettings` 接上：路由树在启动时就画着，第一次按齿轮才装载。 */
async function setup() {
  const r = await load();
  await mountRouter(r);
  await connect(r);
  const shell = panelHost();
  let api!: SettingsPanelApi;
  await act(async () => { api = await r.islands.loadSettingsPanel(shell.host) });
  return { r, api, ...shell };
}
type Stage = Awaited<ReturnType<typeof setup>>;

const effects = (changed: Stage['changed']) => changed.mock.calls.map(([effect]) => effect);
const settingsRoot = () => document.querySelector<HTMLElement>('[data-settings-host]');

async function open(current: Stage, section?: string): Promise<HTMLElement> {
  await act(async () => current.api.open(section));
  await settle();
  return document.querySelector<HTMLElement>('#settingsPanel')!;
}

async function close(current: Stage): Promise<void> {
  await act(async () => current.api.close());
  await settle();
}

/** 收下每一次上报；`console.error` 只静音，不数条数。 */
function watchReports() {
  const reported = vi.fn();
  vi.stubGlobal('reportError', reported);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  return reported;
}

describe('设置面板与壳的接缝', () => {
  it('五个分区按使用场景组织，Peach 配置入口遵守设备权限', async () => {
    const current = await setup();
    const openConfiguration = vi.fn();
    current.host.openConfiguration = openConfiguration;
    const panel = await open(current, 'JAV 与新作');
    expect([...panel.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent))
      .toEqual(['外观与导航', '浏览与播放', 'JAV 与新作', '搜索与推荐', '内容显示']);
    expect(panel.querySelector('#javImageSetting')?.closest('section')?.hidden).toBe(false);
    expect(panel.querySelector('#detailAutoplaySetting')?.closest('section')?.hidden).toBe(true);
    expect(panel.querySelector('#videoThumbnailSetting')).toBeNull();
    expect(panel.querySelector('#followScheduleSetting')).toBeNull();
    const entry = panel.querySelector<HTMLButtonElement>('[data-settings-service] button')!;
    expect(entry.disabled).toBe(true);
    await click(entry);
    expect(openConfiguration).not.toHaveBeenCalled();
    await close(current);
    current.host.configurable = async () => true;
    await open(current);
    await settle();
    expect(entry.disabled).toBe(false);
    await click(entry);
    await settle();
    expect(openConfiguration).toHaveBeenCalledTimes(1);
    expect(panel.hidden).toBe(true);
  });

  it('每一个开关只落一次盘、只报自己那一个效果名', async () => {
    const current = await setup();
    const panel = await open(current);
    for (const [id, field, effect] of [
      ['uiSoundsSetting', 'uiSounds', 'uiSounds'], ['groupCollapseSetting', 'groupCollapse', 'groupCollapse'],
      ['miniplayerSetting', 'miniplayer', 'miniplayer'], ['feedAutoScrollSetting', 'feedAutoScroll', 'feedAutoScroll'],
    ] as const) {
      current.changed.mockClear();
      current.save.mockClear();
      await click(panel.querySelector(`#${id}`));
      expect([current.value[field], current.save.mock.calls.length, effects(current.changed)], id)
        .toEqual([false, 1, [effect]]);
    }
    /* 详情自动播放没有壳那一侧要跟着做的事：只落盘，不报效果。 */
    current.changed.mockClear();
    await click(panel.querySelector('#detailAutoplaySetting'));
    expect([current.value.detailAutoplay, effects(current.changed)]).toEqual([true, []]);

    current.changed.mockClear();
    await click(panel.querySelector('#themeSetting input[value="dark"]'));
    expect([current.value.theme, effects(current.changed)]).toEqual(['dark', ['theme']]);
    await close(current);
  });

  it('「增加对比度」是一枚开关：按下交给壳切实色背景', async () => {
    const current = await setup();
    const panel = await open(current);
    const contrast = panel.querySelector<HTMLInputElement>('#glassContrastSetting')!;
    expect([contrast.getAttribute('role'), panel.querySelector('label[for="glassContrastSetting"] b')?.textContent])
      .toEqual(['switch', '增加对比度']);
    await click(contrast);
    expect(current.setHighContrast.mock.calls).toEqual([[true]]);
    await close(current);
  });

  it('拖动光晕拉条：每一步只叫壳排一帧重画，不落盘；松手那一下才落盘', async () => {
    const current = await setup();
    const panel = await open(current);
    const root = panel.querySelector<HTMLElement>('[data-glow-dial="strength"]')!;
    const slider = root.querySelector<HTMLElement>('[data-dial-slider]')!;
    root.querySelector<HTMLElement>('.ui-dial-track')!.getBoundingClientRect = () =>
      ({ left: 0, top: 0, right: 100, bottom: 10, width: 100, height: 10, x: 0, y: 0, toJSON: () => ({}) });
    current.changed.mockClear();
    current.save.mockClear();
    const pointer = (type: string, clientX: number) =>
      slider.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX, button: 0 }));
    await act(async () => {
      pointer('pointerdown', 10);
      pointer('pointermove', 20);
      pointer('pointermove', 30);
    });
    expect(current.save).not.toHaveBeenCalled();
    expect(effects(current.changed)).toEqual(['glowFrame', 'glowFrame', 'glowFrame']);
    expect(current.value.homeGlow.strength).toBe(30);
    await act(async () => { pointer('pointerup', 30) });
    expect(current.save).toHaveBeenCalledTimes(1);
    expect(effects(current.changed)).toEqual(['glowFrame', 'glowFrame', 'glowFrame']);
    await close(current);
  });

  it('关上再开是同一棵面板：DOM 只建一次，光晕参数区不重建', async () => {
    const current = await setup();
    const panel = await open(current);
    const glow = panel.querySelector('#homeGlowControls');
    const dial = panel.querySelector('[data-glow-dial="speed"]');
    await close(current);
    expect(panel.hidden).toBe(true);
    const again = await open(current, '浏览与播放');
    expect(document.querySelectorAll('[data-settings-host]')).toHaveLength(1);
    expect(again).toBe(panel);
    expect(again.querySelector('#homeGlowControls')).toBe(glow);
    expect(again.querySelector('[data-glow-dial="speed"]')).toBe(dial);
    await close(current);
  });
});

describe('常驻组件', () => {
  it('第一次装载：画上之前没有宿主、句柄为 null；画上之后宿主在 body 末尾、不包 .peach-react，面板收着，句柄才交出', async () => {
    const r = await load();
    await mountRouter(r);
    const shell = panelHost();
    let loaded: Promise<SettingsPanelApi> | null = null;
    await act(async () => { loaded = r.islands.loadSettingsPanel(shell.host) });
    expect(settingsRoot(), '路由树还没接上取数，宿主还没放进文档').toBeNull();
    expect(r.islands.settingsPanelApi()).toBeNull();

    await connect(r);
    let api: SettingsPanelApi | null = null;
    await act(async () => { api = await loaded });
    expect(r.islands.settingsPanelApi()).toBe(api);
    const host = settingsRoot()!;
    expect(host.parentElement).toBe(document.body);
    expect(document.body.lastElementChild).toBe(host);
    expect(r.managedEntry(host)?.path).toBe('settings-panel');
    expect(host.querySelector(':scope > .peach-react')).toBeNull();
    const panel = host.querySelector<HTMLElement>(':scope > section#settingsPanel')!;
    expect(panel.hidden).toBe(true);
    expect(api!.isOpen()).toBe(false);
    expect(shell.attached.mock.calls, '左栏玻璃在面板第一次进 DOM 时挂一次').toEqual([[panel]]);
  });

  it('open 返回时面板已画好、isOpen 为真、页面锁住；焦点在同一轮微任务里落到关闭钮，关上后还给打开前那一枚', async () => {
    const current = await setup();
    const trigger = document.createElement('button');
    document.body.prepend(trigger);
    trigger.focus();
    /* 不包 act：act 会把 flushSync 推到它结束时，看不出句柄自己同步没有。 */
    (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false;
    current.api.open('浏览与播放');
    const panel = document.querySelector<HTMLElement>('#settingsPanel')!;
    expect([panel.hidden, panel.querySelector('#settingsTitle')?.textContent, current.api.isOpen()])
      .toEqual([false, '浏览与播放', true]);
    expect([document.documentElement.style.overflow, document.body.classList.contains('settings-open')])
      .toEqual(['hidden', true]);
    expect(document.activeElement).toBe(trigger);
    await Promise.resolve();
    expect(document.activeElement?.id).toBe('settingsClose');

    current.api.close();
    expect([panel.hasAttribute('data-closing'), current.api.isOpen()]).toEqual([true, true]);
    await Promise.resolve();
    expect([panel.hidden, current.api.isOpen(), document.documentElement.style.overflow,
      document.body.classList.contains('settings-open')]).toEqual([true, false, '', false]);
    expect(document.activeElement).toBe(trigger);
    await settle();
  });

  it('抛错只卸组件、宿主还在，批量条照画；之后句柄照旧可调、不抛，只报一次', async () => {
    const reported = watchReports();
    const current = await setup();
    const host = settingsRoot()!;
    const dockRoot = document.createElement('div');
    dockRoot.setAttribute('data-batch-dock', '');
    document.body.append(dockRoot);
    let dock!: BatchDockApi;
    await act(async () => { dock = await current.r.islands.loadBatchDock({ root: dockRoot, run: vi.fn() }) });

    glyph.broken = true;
    await act(async () => { current.api.open() });
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenCalledWith(expect.objectContaining({ message: '设置面板画不出来' }));
    expect(host.isConnected, '宿主不撤').toBe(true);
    expect(host.childNodes.length, '组件卸掉了').toBe(0);
    expect(current.r.managedTaken(host)).toBe(false);
    expect(current.r.managedEntries().map((entry) => entry.path), '别的面照画').toEqual(['batch-dock']);
    await act(async () => { dock.render({ count: 2, context: 'catalog', junkDismissed: false }) });
    expect(dockRoot.querySelector('[data-selection-dock] [role="status"]')?.textContent).toBe('已选 2 项');

    /* 壳照旧经 Escape 收、经齿轮开：不抛、不重开、不再上报。 */
    await act(async () => { expect(() => current.api.close()).not.toThrow() });
    await settle();
    glyph.broken = false;
    await act(async () => { expect(() => current.api.open('浏览与播放')).not.toThrow() });
    await settle();
    expect(reported, '确定性抛错只报那一次').toHaveBeenCalledTimes(1);
    expect(host.childNodes.length, '空到刷新为止，不自愈').toBe(0);
    await act(async () => { current.api.close() });
    await settle();
  });
});
