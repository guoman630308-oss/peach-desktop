/* `@peach/legacy/ui`（主界面指向 `src/ui-kit/index.ts`）在测试里的替身（vitest.config.ts 里做 alias）。
 *
 * 只保留断言真正依赖的结构标记（`data-geist-empty-state`、`geist-note-error`）；要用正式实现的那几样从
 * `src/ui-kit` 转出，与主界面共享源码是同一个模块实例。完整页面的结构和样式由 `frontend/e2e` 使用真实模块验证。 */
export const emptyStateHtml = (
  iconName: string,
  title: string,
  description: string,
  options: { actions?: string } = {},
): string => `<div class="ui-emptystate" data-geist-empty-state role="status">`
  + `<div class="ui-es-icon" data-icon="${iconName}"></div>`
  + `<div class="ui-es-copy"><h3>${title}</h3><p>${description}</p></div>${options.actions || ''}</div>`;

// 索引页进页骨架的页头（Tabs 与过滤框）用正式模板。
export {boardTabsHtml, formModal, searchInputHtml} from '../../src/ui-kit';
// 索引骨架复用正式模板，折叠开合、覆盖式滚动条、徽标与读数弹跳、换字、滚动判据与补充信息卡用正式实现。
export {attachOverlayScrollbar, growCollapse, iconSwapHtml, indexSkeletonHtml, popBadges, popCount, revealTexts, scrollMovesAnchor, setCollapseOpen, setIconSwap, swapText, wireContextCard} from '../../src/ui-kit';
// 设置面板沿用的互斥视图、拉条、锚定菜单与横向滚动层用正式实现。
export {closeAnchoredMenu, dialSliderHtml, iconSwitchHtml, wireAnchoredMenu, wireDialSlider, wireHorizontalScroller, wireIconSwitch} from '../../src/ui-kit';
// 骨架露面的等待门槛用正式那一个数；管理区的加载态用正式模板，骨架交给整页的淡出用正式实现。
export {configurationSkeletonHtml, revealSkeleton, SKELETON_REVEAL_DELAY, skeletonHtml} from '../../src/ui-kit';
/* 测试环境没有布局，骨架补齐量不出东西，这里什么都不做。 */
export const fitSkeleton = (_root: Element | null): void => {};
export const loadingDotsHtml = (label: string): string => `<span>${label}</span>`;
export const spinnerHtml = (label: string): string => `<span role="status" aria-label="${label}"></span>`;
export const checkboxHtml = (attrs = ''): string => `<span class="ui-pcheck"><input type="checkbox" ${attrs}></span>`;
export const progressHtml = (label: string, value: number, max = 100): string =>
  `<progress role="progressbar" aria-label="${label}" value="${value}" max="${max}" aria-valuenow="${value}" aria-valuemax="${max}"></progress>`;
export const confirmModal = async (_options: unknown) => ({confirmed:false});
/* 菜单开合：测试环境不放动画，退场当场藏起。 */
export const presentMenu = (menu: HTMLElement): void => { menu.hidden = false };
export const dismissMenu = (menu: HTMLElement, finish?: () => void): void => {
  if (menu.hidden) return;
  menu.hidden = true;
  finish?.();
};

export const MEDIA_SOURCE_ICONS: Record<string,string> = {local:'hard-drive','115':'fixture-115',pikpak:'fixture-pikpak'};
export const selectOptionIconHtml = (mark?: string): string => mark ? `<i data-source-icon="${mark}"></i>` : '';

// 设置面板的选项、change 与持久化回归使用正式下拉控件。
export {selectFieldHtml, wireSelectField} from '../../src/ui-kit';
export const wireCollapse = (_root: ParentNode, _selector: string, _idPrefix: string): void => {};
/* 只落位，不动画：jsdom 没有布局，量出来处处是零，那段弹簧也就没有什么可跑的。
   动作本身由 `frontend/e2e/design-*.test.ts` 在浏览器中验证。 */
export const moveGlidePane = (
  pane: HTMLElement,
  _from: unknown,
  box: { x: number; y: number; w: number; h: number },
): void => {
  pane.style.width = `${box.w}px`;
  pane.style.height = `${box.h}px`;
  pane.style.translate = `${box.x}px ${box.y}px`;
};

export const setActionBusy = (control: Element | null, busy = true): void => {
  if (!control) return;
  if (busy) {
    control.setAttribute('aria-busy', 'true');
    control.setAttribute('aria-disabled', 'true');
  } else {
    control.removeAttribute('aria-busy');
    control.removeAttribute('aria-disabled');
  }
};

// 使用正式 Note 验证内部操作。
export {noteHtml} from '../../src/ui-kit';

export const badgeHtml = (text: string): string => `<span class="ui-geist-badge">${text}</span>`;
