/* 卡片版式、照片墙尺寸、卡片密度与主题写到页面上（`src/appearance/layout.ts`、`density.ts`、`theme.ts`）。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyDensity, currentDensity, paintPhotoSizeButton, toggleDensity } from '../../src/appearance/density';
import {
  cardLayoutFor, cardRatio, COVER_FRONT_RATIO, gridLayout, photoLayout, photoSize, storePhotoLayout, storePhotoSize,
  storeVideoLayout,
} from '../../src/appearance/layout';
import { normalizeAppSettings, SETTINGS_KEY } from '../../src/appearance/settings';
import { applyTheme, THEME_OPTIONS } from '../../src/appearance/theme';
import { createSettingsStore } from '../../src/settings-store';

const settingsWith = (patch: Record<string, unknown> = {}) => ({ ...normalizeAppSettings({}).settings, ...patch });

describe('卡片版式', () => {
  it('首页读首页那一份，别处读 JAV 那一份；旧值并进两档', () => {
    const settings = settingsWith({ homeLayout: 'big', javLayout: 'sleeve' });
    expect([cardLayoutFor(true, settings), cardLayoutFor(false, settings)]).toEqual(['big', 'small']);
  });

  it('版式对象只在真变了的时候换新：卡片按引用比较', () => {
    const settings = settingsWith();
    const context = { active: true, home: true, portrait: false };
    const first = gridLayout(context, settings);
    expect(gridLayout({ ...context }, settings)).toBe(first);
    settings.homeLayout = 'big';
    const changed = gridLayout(context, settings);
    expect(changed).not.toBe(first);
    expect(changed).toEqual({ active: true, size: 'big', portrait: false, javImage: 'cover' });
  });

  it('骨架与真卡同一个比例：竖屏 9:16，分大小图的路径上大图取正封比例，其余 16:9', () => {
    const at = (active: boolean, size: 'big' | 'small', portrait: boolean) => cardRatio({ active, size, portrait, javImage: 'cover' });
    expect([at(true, 'big', true), at(true, 'big', false), at(false, 'big', false), at(true, 'small', false)])
      .toEqual([9 / 16, COVER_FRONT_RATIO, 16 / 9, 16 / 9]);
    /* 比本机最宽的正封（0.749）还宽一点：大图只留正封、一张都不从左边切。 */
    expect(COVER_FRONT_RATIO).toBe(0.75);
  });

  it('设置面板那一组一次改首页和 JAV 两份并落盘', () => {
    const store = createSettingsStore(SETTINGS_KEY, settingsWith());
    storeVideoLayout('preview', store);
    expect([store.value.homeLayout, store.value.javLayout, JSON.parse(localStorage.getItem(SETTINGS_KEY)!).homeLayout])
      .toEqual(['small', 'small', 'small']);
  });
});

describe('照片墙', () => {
  it('尺寸默认小图、排法默认瀑布流，写入按白名单', () => {
    expect([photoSize(settingsWith()), photoLayout(settingsWith())]).toEqual(['small', 'masonry']);
    const store = createSettingsStore(SETTINGS_KEY, settingsWith());
    storePhotoSize('huge', store);
    storePhotoLayout('fixed', store);
    expect([store.value.photoSize, store.value.photoLayout]).toEqual(['small', 'fixed']);
    storePhotoSize('big', store);
    expect(photoSize(store.value)).toBe('big');
  });
});

describe('卡片密度与顶栏大小图键', () => {
  beforeEach(() => {
    localStorage.clear();
    document.body.innerHTML = '<button id="density" type="button"></button>';
  });

  it('写 --tile、body 上的 data-density 与那颗键的按下态、提示和字形', () => {
    applyDensity();
    const button = document.querySelector('#density')!;
    expect([document.documentElement.style.getPropertyValue('--tile'), document.body.dataset.density,
      button.getAttribute('aria-pressed'), button.getAttribute('title'), button.getAttribute('aria-label'),
      button.querySelector('[data-icon-swap]')?.getAttribute('data-icon-state')])
      .toEqual(['336px', 'big', 'false', '当前：大图', '切换为小图', 'a']);
    toggleDensity();
    expect([currentDensity(), localStorage.getItem('density'), document.documentElement.style.getPropertyValue('--tile'),
      document.body.dataset.density, button.getAttribute('aria-pressed'), button.getAttribute('title')])
      .toEqual(['dense', 'dense', '168px', 'dense', 'true', '当前：密集']);
  });

  it('停在照片墙上时同一颗键报照片大小', () => {
    paintPhotoSizeButton('small');
    const button = document.querySelector('#density')!;
    expect([button.getAttribute('aria-pressed'), button.getAttribute('title'), button.getAttribute('aria-label'),
      button.querySelector('[data-icon-swap]')?.getAttribute('data-icon-state')])
      .toEqual(['true', '当前：小图', '切换为大图', 'b']);
  });
});

describe('主题', () => {
  let systemDark = false;
  beforeEach(() => {
    /* 模块只建一次媒体查询、之后每次读 `matches`：桩也得是活的。 */
    vi.stubGlobal('matchMedia', (query: string) => ({
      get matches() { return query.includes('dark') && systemDark }, media: query,
      addEventListener: () => {}, removeEventListener: () => {},
    }));
    document.head.innerHTML = '<meta name="theme-color" data-theme-color="light"><meta name="theme-color" data-theme-color="dark">';
    document.body.innerHTML = `<div class="board-theme-toggle"><button data-board-theme="light"></button>
      <button data-board-theme="dark"></button></div>`;
  });
  afterEach(() => vi.unstubAllGlobals());

  const snapshot = () => {
    const root = document.documentElement;
    return {
      theme: root.dataset.theme ?? null, dark: root.classList.contains('dark'),
      pressed: [...document.querySelectorAll('[data-board-theme]')].map((button) => button.getAttribute('aria-pressed')),
      thumb: document.querySelector('.board-theme-toggle')!.classList.contains('is-dark'),
      meta: [...document.querySelectorAll<HTMLMetaElement>('meta[data-theme-color]')].map((meta) => meta.media),
    };
  };

  it('选一档写 data-theme、dark 类、明暗键与地址栏色块', () => {
    applyTheme('dark');
    expect(snapshot()).toEqual({ theme: 'dark', dark: true, pressed: ['false', 'true'], thumb: true, meta: ['not all', 'all'] });
    applyTheme('light');
    expect(snapshot()).toEqual({ theme: 'light', dark: false, pressed: ['true', 'false'], thumb: false, meta: ['all', 'not all'] });
  });

  it('三档各一枚字形：跟随系统说的是设备，所以是显示器', () => {
    expect(THEME_OPTIONS.map(([value, , icon]) => [value, icon])).toEqual([['system', 'monitor'], ['light', 'sun'], ['dark', 'moon']]);
  });

  it('跟随系统摘掉属性，深浅按系统那一档算', () => {
    systemDark = true;
    applyTheme('system');
    expect(snapshot()).toMatchObject({ theme: null, dark: true, meta: ['not all', 'all'] });
  });
});
