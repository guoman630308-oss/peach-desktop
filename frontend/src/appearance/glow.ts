/* 光晕、玻璃面与强调色写到页面上，以及配色那几下点选。
 *
 * 光晕怎么算、怎么写在 `./home-glow.ts`：那一份不认识偏好对象，这里把当前设置和目标元素递进去。
 * 侧栏那一层写的是 `.glowlayer` 那枚空 div 而不是 <html>：自定义属性是继承的，写在根上整棵树都要
 * 重算样式，实测每帧 15ms 上下，拖动时帧预算当场就超；量法和数字记在 web/css/01-base.css 那条规则
 * 上面。一帧只写一次：指针拖动一秒能发上百个 pointermove，排进 requestAnimationFrame 之后写的就是
 * 这一帧真正要画的那一份。
 *
 * 其余玻璃面（搜索框、顶栏图标钮、筛选浮层、设置卡的分区导航、媒体库与配色弹层、窄栏、选择工具条）
 * 分散在整棵树上，没有共同的宿主，它们那两团反光的色相只能写在根上。写一次根就是整棵树重算样式，所以
 * 那一条只接换档、点颜色和开关那几下。强调色同理：一次点选写一个属性，换来的是整页的按钮、焦点环和
 * 链接跟着走。 */
import {
  ACCENTS, DEFAULT_ACCENT, DEFAULT_HOME_GLOW, HOME_GLOW_PRESETS, HOME_GLOW_SPOTS, glowAccent, glowChipFill, glowPalette,
  isNativeGlass, normalizeAccent, paintGlassFaces, paintHomeGlow, type HomeGlow,
} from './home-glow';

import type { SettingsStore } from '../settings-store';
import { appSettingsStore } from './settings';

/** 这一层读写的那两项；store 上还有别的字段。 */
export interface GlowSettings { homeGlow: HomeGlow; accent: string }
type GlowStore = SettingsStore<GlowSettings>;

/** 侧栏光晕那一层当场写一次。启动时第一帧之前走这一条。 */
export function paintHomeGlowNow(store: GlowStore = appSettingsStore()): void {
  paintHomeGlow(document.querySelector<HTMLElement>('.glowlayer'), store.value.homeGlow);
}

let glowFrame = 0;
/** 排一帧重画侧栏光晕那一层；同一帧里再叫不另排。读的是那一帧时的设置。 */
export function applyHomeGlow(store: GlowStore = appSettingsStore()): void {
  if (glowFrame) return;
  glowFrame = requestAnimationFrame(() => { glowFrame = 0; paintHomeGlowNow(store) });
}

export function applyGlassFaces(store: GlowStore = appSettingsStore()): void {
  paintGlassFaces(document.documentElement, store.value.homeGlow);
}

export function applyAccent(store: GlowStore = appSettingsStore()): void {
  document.documentElement.dataset.accent = store.value.accent;
}

/** 预设色块里的一格。`native` 那一格不带颜色，球色由样式表按当前主题取。 */
export interface GlowChip { key: string; label: string; native: boolean; fill: string }

const chip = (key: string, label: string, colors: readonly string[]): GlowChip =>
  ({ key, label, native: isNativeGlass(key), fill: isNativeGlass(key) ? '' : glowChipFill(colors) });

/* 预设色块照 feralui 的预设 chip 做：三枚光晕色等分一圈 conic-gradient，再叠 BoardUI 那三层白色高光。
   「自定义」只有在用户真手调过颜色之后才占一格：没调过时那一格里是一份和默认档一模一样的球，点它
   什么也不会变，读起来却像还有一档没试过。 */
export function glowChips(glow: HomeGlow): GlowChip[] {
  const chips = HOME_GLOW_PRESETS.map(([key, label, palette]) => chip(key, label, HOME_GLOW_SPOTS.map((spot) => palette[spot].color)));
  if (glow.preset === 'custom') chips.push(chip('custom', '自定义', HOME_GLOW_SPOTS.map((spot) => glow[spot].color)));
  return chips;
}

/** 强调色那一排：`[键, 名称]`。球色由样式表按 `data-accent-ball` 取这一档的 400 与 600 两级。 */
export const ACCENT_CHOICES = ACCENTS;

/* 换一档光晕连强调色一起换：一档配色就是一副面，光晕暖着、按钮还是蓝的，读起来是两套皮叠在一起。
   强调色那一排可以单独点，点完只改强调色、不动光晕——先给一套搭配好的，要拆开也拆得开。 */
export function chooseGlowPreset(key: string, store: GlowStore = appSettingsStore()): void {
  if (key === 'custom') return;
  const glow = store.value.homeGlow;
  glow.preset = key;
  Object.assign(glow, glowPalette(key));
  store.value.accent = glowAccent(key);
  store.save();
  applyHomeGlow(store); applyGlassFaces(store); applyAccent(store);
}

export function chooseAccent(key: string, store: GlowStore = appSettingsStore()): void {
  store.value.accent = normalizeAccent(key);
  store.save();
  applyAccent(store);
}

/* 重置的是配色，不是整份光晕：标题就写着「光晕」和「强调色」，把强度和颗粒一起清掉会让人以为按错了键。
   整份恢复默认在详细设置那一屏。 */
export function resetGlowColors(store: GlowStore = appSettingsStore()): void {
  const glow = store.value.homeGlow;
  glow.preset = DEFAULT_HOME_GLOW.preset;
  Object.assign(glow, glowPalette(glow.preset));
  store.value.accent = DEFAULT_ACCENT;
  store.save();
  applyHomeGlow(store); applyGlassFaces(store); applyAccent(store);
}

/* 侧栏底部那枚配色钮右下角的两枚小圆：上面那枚报当前预设的第一枚光晕色，下面那枚报强调色。光晕关掉
   之后上面那枚收起、只剩强调色那一枚——BoardUI 原版那颗点本来就是强调色。「玻璃原色」那一档没有光晕色
   可报，换成玻璃自带那两团反光色各一半，样式表按 `data-glow-native` 取。 */
export function paintGlowButton(button: HTMLElement, glow: HomeGlow): void {
  button.style.setProperty('--glow-swatch', glow.on ? glow.spot1.color : 'var(--color-accent-500)');
  button.toggleAttribute('data-glow-native', glow.on && isNativeGlass(glow.preset));
  button.toggleAttribute('data-glow-off', !glow.on);
}

/** 当场画一次那枚钮，之后每次落盘都跟上：钮常驻在壳里，点哪一处改的配色它都要对齐。 */
export function wireGlowButton(button: HTMLElement, store: GlowStore = appSettingsStore()): () => void {
  paintGlowButton(button, store.value.homeGlow);
  return store.subscribe(() => paintGlowButton(button, store.value.homeGlow));
}
