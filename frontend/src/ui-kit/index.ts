/* 共享控件导出清单：Application 与 React 页面按 @peach/legacy/ui 或源码路径引用。
 * 别名直接指向本文件，控件随 peach-app.js 发出一份，类型检查也读同一清单。
 * 独立页面包仅将用到的控件直接打进自己的产物。 */
export { attachOverlayScrollbar } from './overlay-scrollbar';
export { growCollapse, setCollapseOpen, wireCollapse } from './collapse';
export { closeAnchoredMenu, dismissMenu, presentMenu, scrollMovesAnchor, wireAnchoredMenu } from './anchored-menu';
export { selectFieldHtml, selectOptionIconHtml, wireSelectField } from './select-field';
export { MEDIA_SOURCE_ICONS } from './media-source-icons';
export {
  badgeHtml, boardTabsHtml, checkboxHtml, collectionHeaderHtml, emptyStateHtml, gaugeHtml, loadingDotsHtml, noteHtml,
  progressHtml, projectBannerHtml, searchInputHtml, spinnerHtml,
} from './markup';
export { dissolveValue, iconSwapHtml, popBadges, popCount, revealTexts, setIconSwap, swapText } from './motion';
export {
  configurationSkeletonHtml, fillSkeletonTier, fitSkeleton, indexSkeletonHtml, revealSkeleton, SKELETON_REVEAL_DELAY,
  skeletonHtml,
} from './skeleton';
export {
  glideEase, moveGlidePane, rubberBand, scrollerHtml, stopAutoScroll, wireAutoScroll, wireHorizontalScroller,
  wireOverlayScrollbars, wireScrollers,
} from './scroll';
export {
  dialSliderHtml, iconSwitchHtml, setActionBusy, wireBusyActions, wireContextCard, wireDialSlider, wireDragReorder,
  wireIconSwitch,
} from './controls';
export { confirmModal, formModal } from './modal';
export {
  isCurrentPostSetupTutorialRequest, nextPostSetupTutorialRequest, POST_SETUP_TUTORIAL_COLLAPSED_KEY,
  POST_SETUP_TUTORIAL_KEY, POST_SETUP_TUTORIAL_SKIPPED_KEY, postSetupTutorialCollapsed, postSetupTutorialMarker,
  postSetupTutorialSignature, postSetupTutorialSkipped, resetPostSetupTutorialState, setPostSetupTutorialCollapsed,
  setPostSetupTutorialMarker, setPostSetupTutorialSkipped,
} from '../onboarding/post-setup-tutorial';
