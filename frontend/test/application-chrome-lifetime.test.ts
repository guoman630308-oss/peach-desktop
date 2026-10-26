import { afterEach, expect, it, vi } from 'vitest';

const probes = vi.hoisted(() => ({
  api: vi.fn(), menus: vi.fn(() => ({ setOpen: vi.fn(), isOpen: () => false, dispose: vi.fn() })),
  glowOff: vi.fn(), theme: vi.fn(), openDrawer: vi.fn(),
  preferences: {
    appSettings: { theme: 'dark' }, saveSettings: vi.fn(), settingsStore: {},
    placeSidebarHead: undefined as (() => void) | undefined,
    syncGlassOptics: undefined as (() => void) | undefined,
  },
}));
vi.mock('../src/core', () => ({ api: probes.api, esc: String, icon: () => '' }));
vi.mock('../src/ui-kit', () => ({ MEDIA_SOURCE_ICONS: {}, moveGlidePane: vi.fn(), wireAnchoredMenu: probes.menus }));
vi.mock('../src/application/layout.js', () => ({ narrowShell: () => false, openDrawer: probes.openDrawer, openSettings: vi.fn() }));
vi.mock('../src/application/playlists.js', () => ({ openConfigurationSection: vi.fn() }));
vi.mock('../src/application/preferences.js', () => ({ preferencesState: probes.preferences }));
vi.mock('../src/theme-transition', () => ({ transitionTheme: vi.fn() }));
vi.mock('../src/appearance/theme', () => ({ applyTheme: probes.theme }));
vi.mock('../src/appearance/glow', () => ({ wireGlowButton: () => probes.glowOff }));
vi.mock('../src/react/application-residents', () => ({ loadGlowPicker: () => Promise.resolve() }));

afterEach(async () => {
  const effects = await import('../src/application/effects');
  effects.applicationEffects.dispose();
  document.body.innerHTML = ''; vi.restoreAllMocks(); vi.unstubAllGlobals();
});

it('侧栏原生节点复位、自建节点清退，卸载后媒体库响应不写新根', async () => {
  probes.api.mockReset(); probes.menus.mockClear(); probes.glowOff.mockClear();
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Firefox');
  document.body.innerHTML = '<header><a id="brandHome"><span class="mark"></span><h1>原品牌</h1></a><button id="filterBtn"></button><button id="settingsBtn"></button><i id="tail"></i></header><aside id="drawer"><div id="drawerScroll"><div data-sidebar-head></div></div></aside><main id="main"></main>';
  const nativeNodes = ['brandHome', 'filterBtn', 'settingsBtn'].map(id => document.getElementById(id)!);
  const original = nativeNodes.map(node => ({ parent: node.parentNode, next: node.nextSibling }));
  let finish!: (value: unknown) => void;
  probes.api.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const effects = await import('../src/application/effects');
  effects.renewApplicationEffects();
  const chrome = await import('../src/application/chrome.js'); chrome.installChrome25();
  const signal = probes.api.mock.calls[0]?.[1]?.signal as AbortSignal;
  const scope = effects.applicationEffects;
  document.querySelector('[data-sidebar-head]')!.remove();
  scope.dispose();
  for (const [index, node] of nativeNodes.entries()) {
    expect(node.parentNode).toBe(original[index]?.parent);
    expect(node.nextSibling).toBe(original[index]?.next);
  }
  expect(signal.aborted).toBe(true);
  expect(document.querySelectorAll('#boardLibraryMenu,#boardGlowMenu,.board-sidebar-foot,.board-library-chevron')).toHaveLength(0);
  expect(probes.glowOff).toHaveBeenCalledTimes(1);
  expect(probes.menus.mock.results.every(result => result.type === 'return' && result.value.dispose.mock.calls.length === 1)).toBe(true);
  effects.renewApplicationEffects();
  finish({ libraries: [] }); await Promise.resolve(); await Promise.resolve();
  expect(nativeNodes[0]?.querySelector('h1')?.textContent).toBe('原品牌');
  probes.api.mockReturnValueOnce(new Promise(() => {})); chrome.installChrome25();
  expect(document.querySelectorAll('#boardLibraryMenu')).toHaveLength(1);
  effects.applicationEffects.dispose();
});
