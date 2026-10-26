/* 侧栏配色卡与它底下那枚配色钮：两组球画什么、点下去写什么，光晕关掉之后卡上剩什么，设置面板那一格改了
 * 配色这里当场跟上，以及卡作为常驻组件（`RESIDENT_ROUTES['glow-picker']`）由路由树直接画成 `#boardGlowMenu`
 * 的子节点。卡的锚定、开合、玻璃材质与球的像素在真浏览器里量（`e2e/design-*.test.ts`）。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { ACCENTS, DEFAULT_ACCENT, glowAccent, glowPalette, normalizeHomeGlow } from '@peach/appearance';

import { chooseGlowPreset, wireGlowButton, type GlowSettings } from '../../src/appearance/glow';
import { SETTINGS_KEY } from '../../src/appearance/settings';
import type { ShellActions } from '../../src/react/router/shell-actions';
import { createSettingsStore } from '../../src/settings-store';
import { click } from './render';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

async function load() {
  const [history, router, routes, island] = await Promise.all([
    import('../../src/history'), import('../../src/react/router/router'), import('../../src/react/router/managed-routes'),
    import('../../src/react/glow-picker/glow-picker-island'),
  ]);
  return { ...history, ...router, ...routes, ...island };
}
let r: Awaited<ReturnType<typeof load>>;

/* 壳启动时的顺序：先画路由树，再接上取数。整个文件共用这一棵，每条用例开自己的那张卡、结束时收掉。 */
beforeAll(async () => {
  r = await load();
  const root = createRoot(document.createElement('div'), r.ROUTER_ROOT_OPTIONS);
  await act(async () => { root.render(<r.RouterRoot actions={{} as ShellActions} />) });
  await act(async () => { r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute)) });
}, REACT_IMPORT_TIMEOUT_MS);

const opened: HTMLElement[] = [];
afterEach(() => {
  for (const menu of opened.splice(0)) act(() => { r.releaseManagedRoute(menu) });
});

/** 照壳的 `loadGlowPicker` 接上：壳建的那张卡交进来，路由树把内容画成它的子节点。 */
async function setup(patch: Partial<GlowSettings['homeGlow']> = {}, accent = 'blue') {
  document.body.innerHTML = '<div class="glowlayer"></div><button id="boardGlowBtn"></button>';
  const root = document.createElement('div');
  root.id = 'boardGlowMenu';
  document.body.append(root);
  const store = createSettingsStore<GlowSettings>(SETTINGS_KEY, { homeGlow: { ...normalizeHomeGlow(null), ...patch }, accent });
  const openDetails = vi.fn();
  r.configureGlowPicker({ root, store, openDetails });
  await act(async () => { expect(await r.openResidentSurface('glow-picker', root)).toBe(true) });
  opened.push(root);
  return { root, store, openDetails };
}

const pressed = (root: ParentNode, selector: string) =>
  [...root.querySelectorAll(`${selector}[aria-pressed="true"]`)].map((chip) => chip.getAttribute('aria-label'));

beforeEach(() => {
  localStorage.clear();
  /* 当场跑完那一帧；回 0，模块里那枚「已排过一帧」的记号也就跟着清零。 */
  vi.stubGlobal('requestAnimationFrame', (run: FrameRequestCallback) => { run(0); return 0 });
});

describe('常驻组件', () => {
  it('内容直接是 #boardGlowMenu 的子节点，不包 .peach-react；收起只卸内容，卡留在文档里', async () => {
    const { root } = await setup();
    expect([...root.children].map((child) => child.tagName.toLowerCase())).toEqual(['header', 'div', 'p', 'div', 'footer']);
    expect(root.querySelector('.peach-react')).toBeNull();
    expect(r.managedEntry(root)?.path).toBe('glow-picker');
    act(() => { r.releaseManagedRoute(root) });
    expect([root.isConnected, root.childNodes.length, r.managedTaken(root)]).toEqual([true, 0, false]);
  });
});

describe('两组球', () => {
  it('上面一组是光晕预设，下面一组是十二档强调色，各自按下当前那一档', async () => {
    const { root } = await setup({ preset: 'ash' }, 'teal');
    const grids = [...root.querySelectorAll('[role="group"]')].map((grid) => grid.getAttribute('aria-label'));
    expect(grids).toEqual(['光晕', '强调色']);
    expect([...root.querySelectorAll('[data-glow-head]')].map((head) => head.textContent)).toEqual(['光晕重置', '强调色']);
    expect(pressed(root, '[data-glow-preset]')).toEqual(['灰雾']);
    expect([...root.querySelectorAll('[data-accent]')].map((chip) => chip.getAttribute('data-accent')))
      .toEqual(ACCENTS.map(([key]) => key));
    expect(root.querySelector('[data-accent="teal"] [data-accent-ball="teal"]')).not.toBeNull();
    expect(pressed(root, '[data-accent]')).toEqual([ACCENTS.find(([key]) => key === 'teal')![1]]);
  });

  it('「玻璃原色」那一格的球不带颜色，交给样式表按主题取；其余每格带一份 conic 填充', async () => {
    const { root } = await setup();
    const native = root.querySelector<HTMLElement>('[data-glow-preset="native"] [data-glow-ball]')!;
    expect([native.hasAttribute('data-glow-native'), native.style.getPropertyValue('--glow-chip')]).toEqual([true, '']);
    const ash = root.querySelector<HTMLElement>('[data-glow-preset="ash"] [data-glow-ball]')!;
    expect(ash.style.getPropertyValue('--glow-chip')).toMatch(/^conic-gradient\(/);
  });

  it('「自定义」只在真手调过颜色之后才占一格', async () => {
    expect((await setup()).root.querySelector('[data-glow-preset="custom"]')).toBeNull();
    expect((await setup({ preset: 'custom' })).root.querySelector('[data-glow-preset="custom"]')).not.toBeNull();
  });
});

describe('点下去写什么', () => {
  it('换一档光晕连强调色一起换，并把光晕、玻璃面与强调色写到页面上', async () => {
    const { root, store } = await setup({ preset: 'ash' });
    await click(root.querySelector('[data-glow-preset="amber"]'));
    expect(store.value.homeGlow).toMatchObject({ preset: 'amber', ...glowPalette('amber') });
    expect(store.value.accent).toBe(glowAccent('amber'));
    expect(JSON.parse(localStorage.getItem(SETTINGS_KEY)!).homeGlow.preset).toBe('amber');
    expect(document.documentElement.dataset.accent).toBe(glowAccent('amber'));
    expect(document.querySelector<HTMLElement>('.glowlayer')!.style.getPropertyValue('--glow-strength')).not.toBe('');
    expect(pressed(root, '[data-glow-preset]')).toEqual(['钨丝暖阁']);
  });

  it('单点强调色只改强调色，不动光晕', async () => {
    const { root, store } = await setup({ preset: 'ash' });
    await click(root.querySelector('[data-accent="rose"]'));
    expect([store.value.accent, store.value.homeGlow.preset, document.documentElement.dataset.accent]).toEqual(['rose', 'ash', 'rose']);
  });

  it('「重置」把配色与强调色收回默认，强度这类参数不动', async () => {
    const { root, store } = await setup({ preset: 'amber', strength: 40 }, 'rose');
    await click(root.querySelector('[data-glow-preset-reset]'));
    expect(store.value.homeGlow).toMatchObject({ preset: 'ash', strength: 40, ...glowPalette('ash') });
    expect(store.value.accent).toBe(DEFAULT_ACCENT);
  });

  it('「详细设置」交回壳去开设置面板', async () => {
    const { root, openDetails } = await setup();
    await click(root.querySelector('[data-glow-detail]'));
    expect(openDetails).toHaveBeenCalledTimes(1);
  });
});

describe('与别处同步', () => {
  it('光晕关掉之后只剩强调色那一组，标题与「重置」一起收起', async () => {
    const { root } = await setup({ on: false });
    expect([root.querySelector<HTMLElement>('[data-glow-presets]')!.hidden, root.querySelector<HTMLElement>('[data-glow-grid]')!.hidden,
      root.querySelector<HTMLElement>('[data-accent-grid]')!.hidden]).toEqual([true, true, false]);
  });

  it('设置面板那一格选一档，卡里同一枚当场按下，焦点所在那一枚还是同一个节点', async () => {
    const { root, store } = await setup({ preset: 'ash' });
    const amber = root.querySelector('[data-glow-preset="amber"]');
    act(() => chooseGlowPreset('amber', store));
    expect(pressed(root, '[data-glow-preset]')).toEqual(['钨丝暖阁']);
    expect(root.querySelector('[data-glow-preset="amber"]')).toBe(amber);
  });

  it('配色钮上那枚点报第一枚光晕色，原色那一档挂标记，光晕关掉之后改报强调色', async () => {
    const { store } = await setup({ preset: 'ash' });
    const button = document.querySelector<HTMLElement>('#boardGlowBtn')!;
    wireGlowButton(button, store);
    const paint = () => [button.style.getPropertyValue('--glow-swatch'), button.hasAttribute('data-glow-native'), button.hasAttribute('data-glow-off')];
    expect(paint()).toEqual([store.value.homeGlow.spot1.color, false, false]);
    act(() => chooseGlowPreset('native', store));
    expect(paint()).toEqual([store.value.homeGlow.spot1.color, true, false]);
    store.value.homeGlow.on = false; store.save();
    expect(paint()).toEqual(['var(--color-accent-500)', false, true]);
  });
});
