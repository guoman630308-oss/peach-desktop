/* 独立页面包的 @peach/legacy/ui 别名落点（vite.pages.config.ts）。
 * 主界面的同名别名指向 ui-kit/index.ts；独立页面只直接打包实际使用的折叠与来源站标。
 * 导出清单缺名时由构建检查拒绝。 */
export { setCollapseOpen } from '../../ui-kit/collapse';
export { MEDIA_SOURCE_ICONS } from '../../ui-kit/media-source-icons';
