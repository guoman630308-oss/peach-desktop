/* 界面偏好的启动归一化、整页那一份 store 与数据库对账（`src/appearance/settings.ts`）。 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_ACCENT, DEFAULT_HOME_GLOW } from '../../src/appearance/home-glow';
import {
  applySyncedSettings, DEFAULT_SETTINGS, normalizeAppSettings, readAppSettings, SETTINGS_KEY, type AppSettings,
} from '../../src/appearance/settings';
import { createSettingsStore } from '../../src/settings-store';
import { DEFAULT_SIDEBAR_ORDER } from '../../src/sidebar';

beforeEach(() => localStorage.clear());

describe('启动归一化', () => {
  it('什么都没存时就是出厂那一套，布尔项各有自己的默认', () => {
    const { settings, migrated } = normalizeAppSettings({});
    expect(migrated).toBe(false);
    expect(settings).toMatchObject({
      ...DEFAULT_SETTINGS, sidebarOrder: [...DEFAULT_SIDEBAR_ORDER], homeGlow: DEFAULT_HOME_GLOW, accent: DEFAULT_ACCENT,
      followInitialDays: 30, detailAutoplay: true, uiSounds: true, feedAutoScroll: true,
      feedHideGroupCompilations: true, feedHideSoloCompilations: false, feedHideExcerpts: true,
      /* JAV 版式与艺人索引默认大图；照片墙一套几十张，先看全貌所以是小图。 */
      homeLayout: 'small', javLayout: 'big', peopleLayout: 'big', photoSize: 'small',
      ambientMode: true, miniplayer: true, theaterMode: false, theme: 'system', groupCollapse: true,
    });
  });

  it('认不出的一项退回出厂那一档，其余照留', () => {
    const { settings } = normalizeAppSettings({
      batchSize: 0, defaultSort: 'nope', hoverDelaySeconds: 61, seekSeconds: 2.5, searchHistoryLimit: 51, relatedLimit: -1,
      theme: 'sepia', followInitialDays: 14, metadataRefreshDays: '7', accent: 'teal', homeLayout: 'big', ambientMode: 0,
      theaterMode: 'yes', miniplayer: false, feedHideSoloCompilations: 'true',
    });
    expect(settings).toMatchObject({
      batchSize: 60, defaultSort: 'seed', hoverDelaySeconds: 5, seekSeconds: 10, searchHistoryLimit: 10, relatedLimit: 20,
      theme: 'system', followInitialDays: 30, accent: 'teal', homeLayout: 'big', ambientMode: true,
      theaterMode: false, miniplayer: false, feedHideSoloCompilations: false,
    });
  });

  it('界面上已经不存在的键不留在对象上', () => {
    const { settings } = normalizeAppSettings({ rotateMinutes: 5, loginDays: 30, metadataRefreshDays: 7, followImagesOnly: true });
    expect('metadataRefreshDays' in settings).toBe(false);
    expect(['rotateMinutes' in settings, 'loginDays' in settings, settings.followImagesOnly]).toEqual([false, false, true]);
  });

  it('旧版本存的默认排序换成现在的键，并报要落一次盘；用户主动选过的不动', () => {
    expect(normalizeAppSettings({ defaultSort: 'new', sortDefaultsVersion: 1 })).toMatchObject(
      { settings: { defaultSort: 'seed', sortDefaultsVersion: 3 }, migrated: true });
    expect(normalizeAppSettings({ defaultSort: 'long', sortDefaultsVersion: 2 })).toMatchObject(
      { settings: { defaultSort: 'dur' }, migrated: true });
    expect(normalizeAppSettings({ defaultSort: 'new', sortDefaultsVersion: 3 })).toMatchObject(
      { settings: { defaultSort: 'new' }, migrated: false });
    /* 版本已经是 3 还存着旧键，说明不是迁移能认的来路，按白名单退回随机，不改写成别的列。 */
    expect(normalizeAppSettings({ defaultSort: 'long', sortDefaultsVersion: 3 })).toMatchObject(
      { settings: { defaultSort: 'seed' }, migrated: false });
  });

  it('JAV 版式的旧值并进现在的两档，「预览」那一档带着把默认封面换成预览图', () => {
    expect(normalizeAppSettings({ javLayout: 'preview' }).settings).toMatchObject({ javLayout: 'small', javImage: 'thumbnail' });
    expect(normalizeAppSettings({ javLayout: 'sleeve', javImage: 'bogus' }).settings).toMatchObject({ javLayout: 'small', javImage: 'cover' });
  });

  it('存的不是合法 JSON 时当作什么都没存', () => {
    const storage = { getItem: (key: string) => (key === SETTINGS_KEY ? '{broken' : null) };
    expect(readAppSettings(storage).settings).toMatchObject({ batchSize: 60, theme: 'system' });
  });
});

describe('整页那一份 store', () => {
  it('第一次取时读回并归一化，之后取到的是同一个对象；改写过旧键当场落一次盘', async () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ defaultSort: 'short', sortDefaultsVersion: 2, theme: 'dark' }));
    vi.resetModules();
    const { appSettingsStore } = await import('../../src/appearance/settings');
    const store = appSettingsStore();
    expect(appSettingsStore()).toBe(store);
    expect(store.value).toMatchObject({ defaultSort: 'dur', theme: 'dark' });
    expect(JSON.parse(localStorage.getItem(SETTINGS_KEY)!)).toMatchObject({ defaultSort: 'dur', sortDefaultsVersion: 3 });
  });
});

describe('数据库那一份落进本地缓存', () => {
  const stage = (patch: Partial<AppSettings> = {}) => {
    const store = createSettingsStore(SETTINGS_KEY, { ...normalizeAppSettings({}).settings, ...patch });
    const changed = vi.fn();
    return { store, changed, save: vi.spyOn(store, 'save') };
  };

  it('合法的值落盘，认不出的不碰；搜索记录条数变了才叫壳推给搜索岛', () => {
    const { store, changed } = stage();
    applySyncedSettings({
      followInitialDays: 7, feedHideSoloCompilations: true, feedHideExcerpts: 'no',
      searchHistoryLimit: 25, sidebarOrder: ['jav', ''],
    }, changed, store);
    expect(store.value).toMatchObject({
      followInitialDays: 7, feedHideSoloCompilations: true, feedHideExcerpts: true,
      searchHistoryLimit: 25, sidebarOrder: ['jav', ''],
    });
    expect(changed.mock.calls).toEqual([['searchHistoryLimit']]);
    changed.mockClear();
    applySyncedSettings({ searchHistoryLimit: 25, followInitialDays: 3 }, changed, store);
    expect([changed.mock.calls.length, store.value.followInitialDays]).toEqual([0, 7]);
  });

  it('数据库里还没定条数时，这台设备改过的那个数送上去一次；没改过不送', async () => {
    const fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    vi.stubGlobal('fetch', fetch);
    applySyncedSettings({ searchHistoryLimit: null }, vi.fn(), stage().store);
    expect(fetch).not.toHaveBeenCalled();
    applySyncedSettings({ searchHistoryLimit: null }, vi.fn(), stage({ searchHistoryLimit: 3 }).store);
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(1);
    const [path, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect([path, init.method, JSON.parse(String(init.body))]).toEqual(['/api/settings', 'POST', { searchHistoryLimit: 3 }]);
    vi.unstubAllGlobals();
  });

  it('侧栏顺序没变就不落盘', () => {
    const { store, save, changed } = stage();
    applySyncedSettings({ sidebarOrder: [...store.value.sidebarOrder] }, changed, store);
    applySyncedSettings({ sidebarOrder: [] }, changed, store);
    applySyncedSettings(null, changed, store);
    expect(save).not.toHaveBeenCalled();
  });
});
