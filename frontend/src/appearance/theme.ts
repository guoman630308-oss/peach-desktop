/* 明暗主题写到页面上。
 *
 * 主题只写属性，不写颜色：两套色板都在 web/css/01-base.css，选跟随系统就把属性摘掉，交还给
 * `prefers-color-scheme`。React 子树里的 BoardUI 源码把深色 token 挂在 `.dark` 上，所以同一次调用按
 * 实际深浅给 <html> 加减 `dark` 类，跟随系统时也算上系统那一档。地址栏色块跟着同一次调用走——两枚
 * meta 各代表一档，选中的那枚开到 `all`、另一枚关成 `not all`，否则手机上的地址栏还留在系统那一档。
 * `index.html` 的首屏内联脚本做的是同三件事，它只负责第一帧，之后都从这里出。 */
import { appSettingsStore } from './settings';

/** `[值, 显示名, 字形]`：设置面板那一组三档。显示器那枚字形说的是「跟着这台显示器」。 */
export const THEME_OPTIONS: readonly (readonly [value: string, label: string, icon: string])[] = [
  ['system', '跟随系统', 'monitor'], ['light', '浅色', 'sun'], ['dark', '深色', 'moon'],
];

let systemDark: MediaQueryList | null = null;
const prefersDark = (): MediaQueryList => (systemDark ??= matchMedia('(prefers-color-scheme: dark)'));

/* 换主题时整页的颜色过渡一起播，满屏拖影。写属性前挂一张样式把过渡全关掉，写完强制算一次样式，
 * 新颜色就直接落定，之后再摘掉也不会补播。明暗键的滑块不在其内：它那段位移本来就该跟着这次切换走。
 * 摘除排在下一帧，同时挂一枚定时器兜底：页面不可见时 rAF 不跑，样式不能一直挂着。 */
let transitionBlock: HTMLStyleElement | null = null;
function suppressTransitions(): () => void {
  if (!transitionBlock) {
    transitionBlock = document.createElement('style');
    transitionBlock.textContent = '*:not(.board-theme-thumb),*::before,*::after{transition:none!important}';
  }
  if (!transitionBlock.isConnected) document.head.append(transitionBlock);
  return () => {
    void getComputedStyle(document.documentElement).opacity;
    const release = () => { cancelAnimationFrame(frame); clearTimeout(timer); transitionBlock?.remove() };
    const frame = requestAnimationFrame(release);
    const timer = setTimeout(release, 100);
  };
}

export function applyTheme(choice: string = appSettingsStore().value.theme): void {
  const restore = suppressTransitions();
  const root = document.documentElement;
  if (choice === 'system') delete root.dataset.theme; else root.dataset.theme = choice;
  const dark = choice === 'dark' || (choice === 'system' && prefersDark().matches);
  root.classList.toggle('dark', dark);
  document.querySelectorAll<HTMLElement>('[data-board-theme]').forEach((button) =>
    button.setAttribute('aria-pressed', String((button.dataset.boardTheme === 'dark') === dark)));
  document.querySelector('.board-theme-toggle')?.classList.toggle('is-dark', dark);
  document.querySelectorAll<HTMLMetaElement>('meta[data-theme-color]').forEach((meta) => {
    meta.media = (meta.dataset.themeColor === 'dark') === dark ? 'all' : 'not all';
  });
  restore();
}

/** 跟随系统那一档：系统换了明暗，页面当场跟上。只接一次。 */
let watching = false;
export function watchSystemTheme(): void {
  if (watching) return;
  watching = true;
  prefersDark().addEventListener('change', () => { if (appSettingsStore().value.theme === 'system') applyTheme() });
}
