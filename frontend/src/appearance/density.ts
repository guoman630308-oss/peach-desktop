/* 卡片密度：大图为主，密集为辅。值存在自己的键（`density`）上，不在偏好对象里。
 *
 * 写的是 <html> 上的 `--tile`（一格的模块宽度）与 <body> 上的 `data-density`，以及顶栏那颗大小图键
 * （`#density`）的按下态、提示与字形。壳在模块体里同步调 `applyDensity()`，第一帧之前就写好。 */
import { iconSwapHtml, setIconSwap } from '@peach/legacy/ui';

import { PHOTO_SIZES } from './layout';

export type Density = 'big' | 'dense';
const DENSITY_KEY = 'density';
/** 168px 是模块单位，大图两格。 */
export const TILES: Readonly<Record<Density, string>> = { big: '336px', dense: '168px' };

/** 存的不是认得的那两档时按大图算。 */
export const currentDensity = (): Density => (localStorage.getItem(DENSITY_KEY) === 'dense' ? 'dense' : 'big');

/* 顶栏这颗键和筛选框里的版式开关问同一件事「现在是哪种排法」，所以字形也取同一份映射
   （PHOTO_SIZES 的第三位），按下去跟着换成当前状态的图标。 */
function paintSizeGlyph(button: HTMLElement, size: string): void {
  const [big, small] = PHOTO_SIZES;
  if (!button.querySelector('[data-icon-swap]')) button.innerHTML = iconSwapHtml(big[2], small[2], size === small[0] ? 'b' : 'a');
  else setIconSwap(button, size === small[0] ? 'b' : 'a');
  button.setAttribute('aria-label', size === 'big' ? '切换为小图' : '切换为大图');
}

const densityButton = (): HTMLElement | null => document.querySelector<HTMLElement>('#density');

export function applyDensity(density: Density = currentDensity()): void {
  document.documentElement.style.setProperty('--tile', TILES[density]);
  document.body.dataset.density = density;
  const button = densityButton();
  if (!button) return;
  button.setAttribute('aria-pressed', String(density === 'dense'));
  button.title = '当前：' + (density === 'big' ? '大图' : '密集');
  paintSizeGlyph(button, density === 'big' ? 'big' : 'small');
}

/** 换一档密度、落盘、当场写上。 */
export function toggleDensity(): void {
  const next: Density = currentDensity() === 'big' ? 'dense' : 'big';
  localStorage.setItem(DENSITY_KEY, next);
  applyDensity(next);
}

/** 停在照片墙上时，同一颗键报的是照片的大小。 */
export function paintPhotoSizeButton(size: string): void {
  const button = densityButton();
  if (!button) return;
  button.setAttribute('aria-pressed', String(size === 'small'));
  button.title = '当前：' + (size === 'big' ? '大图' : '小图');
  paintSizeGlyph(button, size);
}
