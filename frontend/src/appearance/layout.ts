/* 卡片版式与照片墙尺寸：存的是哪一档、读回来按哪一档算、写进去之后落盘。
 *
 * 首页与 JAV 各存一份卡片版式（`homeLayout` / `javLayout`）。「现在在首页还是在 JAV」是路由与目录状态的
 * 判据，归壳（`application/layout.js` 的 `javActive` / `homeLayoutActive`），这里只收它算好的布尔值。 */
import { normalizeJavLayout, type JavLayout } from '../jav-artwork';
import type { SettingsStore } from '../settings-store';
import { allowedSetting, appSettingsStore, type AppSettings } from './settings';

/** `[值, 显示名, 字形]`：版式开关在设置面板与筛选条下排各有一组，读的都是这一张表。 */
export const JAV_LAYOUTS: readonly (readonly [value: string, label: string, icon: string])[] = [
  ['big', '大图', 'maximize'], ['small', '小图', 'layout-grid'],
];
/* 图片墙是等宽网格，改的是列数。默认小图——一套图几十上百张，先看得见全貌，挑中哪一张再点开看大的。 */
type Choice = readonly [value: string, label: string, icon: string];
export const PHOTO_SIZES: readonly [big: Choice, small: Choice] = [
  ['big', '大图', 'maximize'], ['small', '小图', 'layout-grid'],
];
export const PHOTO_LAYOUTS: readonly (readonly [value: string, label: string, icon: string])[] = [
  ['fixed', '固定比例', 'layout-grid'], ['masonry', '瀑布流', 'columns-2'],
];
/* 大图卡片固定为 3:4；传统正封完整居中，比例留白使用同图模糊补底，整幅大封按主体取景。 */
export const COVER_FRONT_RATIO = 0.75;

type LayoutSettings = Pick<AppSettings, 'javLayout' | 'homeLayout' | 'photoSize' | 'photoLayout' | 'javImage'>;
type LayoutStore = SettingsStore<LayoutSettings>;
const current = (): LayoutSettings => appSettingsStore().value;

export const javLayout = (settings: LayoutSettings = current()): JavLayout => normalizeJavLayout(settings.javLayout);
export const homeLayout = (settings: LayoutSettings = current()): JavLayout => normalizeJavLayout(settings.homeLayout);
/** 当前这一页的卡片版式：首页读首页那一份，其余读 JAV 那一份。 */
export const cardLayoutFor = (home: boolean, settings: LayoutSettings = current()): JavLayout =>
  home ? homeLayout(settings) : javLayout(settings);

const PHOTO_SIZE_KEYS = PHOTO_SIZES.map(([key]) => key);
const PHOTO_LAYOUT_KEYS = PHOTO_LAYOUTS.map(([key]) => key);
export const photoSize = (settings: LayoutSettings = current()): string => allowedSetting(settings.photoSize, PHOTO_SIZE_KEYS, 'small');
export const photoLayout = (settings: LayoutSettings = current()): string =>
  allowedSetting(settings.photoLayout, PHOTO_LAYOUT_KEYS, 'masonry');

/** 一屏卡片的版式。`active` 为假的路径（标签页、搜索结果…）卡片不分大小图。 */
export interface GridLayout { active: boolean; size: JavLayout; portrait: boolean; javImage: string }

/* 只在真变了的时候换新对象：卡片按引用比较，版式对象每次都新建的话，选一张卡也会让整屏每一张都重画一遍。 */
let gridLayoutValue: GridLayout | null = null;
export function gridLayout(
  context: { active: boolean; home: boolean; portrait: boolean }, settings: LayoutSettings = current(),
): GridLayout {
  const next: GridLayout = {
    active: context.active, size: cardLayoutFor(context.home, settings), portrait: context.portrait, javImage: settings.javImage,
  };
  const previous = gridLayoutValue;
  if (!previous || (Object.keys(next) as (keyof GridLayout)[]).some((key) => next[key] !== previous[key])) gridLayoutValue = next;
  return gridLayoutValue!;
}

/** 作品网格的骨架与真卡共享比例：显式竖屏为 9:16，大图为正封比例，其余为 16:9。 */
export function cardRatio(layout: GridLayout): number {
  if (layout.portrait) return 9 / 16;
  return layout.active && layout.size === 'big' ? COVER_FRONT_RATIO : 16 / 9;
}

/* 写入都是「归一化、落盘」两步。落盘之后要重画哪一块（网格、筛选条、照片墙）是壳的事：哪些岛正挂着、
   停在哪一页，只有壳知道。 */
export function storeHomeLayout(value: unknown, store: LayoutStore = appSettingsStore()): void {
  store.value.homeLayout = normalizeJavLayout(value); store.save();
}
export function storeJavLayout(value: unknown, store: LayoutStore = appSettingsStore()): void {
  store.value.javLayout = normalizeJavLayout(value); store.save();
}
/** 设置面板那一组「视频封面默认大小」：首页与 JAV 一起改。 */
export function storeVideoLayout(value: unknown, store: LayoutStore = appSettingsStore()): void {
  store.value.homeLayout = store.value.javLayout = normalizeJavLayout(value); store.save();
}
export function storePhotoSize(value: unknown, store: LayoutStore = appSettingsStore()): void {
  store.value.photoSize = allowedSetting(value, PHOTO_SIZE_KEYS, 'small'); store.save();
}
export function storePhotoLayout(value: unknown, store: LayoutStore = appSettingsStore()): void {
  store.value.photoLayout = allowedSetting(value, PHOTO_LAYOUT_KEYS, 'masonry'); store.save();
}
