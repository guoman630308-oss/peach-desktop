/* 管理区各页（连同播放列表页）的加载态只有一份定义：壳在深链冷启动时铺它，路由树的页面元素挂上时也铺它。
 * 两处各写各的时，整页刷新会连播两段动画：先一张通用大布局骨架，再各页自己的加载态。取同一份，
 * `data-skeleton` 的键就相同，后铺的那一处认出是同一张后不再重画（`paintManagementPlaceholder`）。
 *
 * 版式照 `boardPageSkeleton` 画的那几页之外，数据管理、复核、诊断与采集来源各有一张，其余路径落到通用面板。 */
import { esc } from '@peach/legacy/core';
import { configurationSkeletonHtml, fitSkeleton, skeletonHtml } from '@peach/legacy/ui';

import { boardPageSkeleton } from './board-skeleton';
import { cleanupSkeletonHtml } from './management';
import { CONFIGURATION_SECTIONS } from './history/settings-route';

export interface PageSkeletonOptions {
  cards?: boolean;
  className?: string;
  variant?: string;
  count?: number;
  fill?: boolean;
  cardRatio?: number;
  gridClass?: string;
  gridSize?: string;
}

/** 整页骨架：`cards` 画卡片网格，否则画面板；只把给了的几项交下去，其余用 `skeletonHtml` 自己的缺省。 */
export const pageSkeletonHtml = (
  label: string,
  { cards = false, className = '', variant = '', count, fill, cardRatio, gridClass = '', gridSize = '' }: PageSkeletonOptions = {},
): string => skeletonHtml(label, {
  variant: variant || (cards ? 'cards' : 'panel'), className, gridClass, gridSize,
  ...(count ? { count } : {}), ...(fill === undefined ? {} : { fill }), ...(cardRatio ? { cardRatio } : {}),
});

/* 分类名是静态文案，骨架直接写出来；次序就是页签次序，与复核页那一份一致。 */
const REVIEW_LABELS = ['元数据字段', '创作者标签', '厂牌 Logo', '女优头像', '西方身份回配', '番号目录存疑', 'FC2 评论标记', 'FC2 跨号相似', '片尾/出处证据'];
/* 复核页是左边一列分类、右边工具条加一格一格 Fieldset，骨架就用最终容器的那几个类名，
   分栏、列宽和卡高全由页面自己那套规则给：读完数据只是把占位换成内容，版面一格不挪。
   分类名和「复核分类」这两样与数据无关，直接写出来；等的是每类多少条，所以只有计数
   那一枚是占位。工具条上那三件——分组方式、分类筛选、全选本页——要等队列回来才知道
   选项和条数，三枚占位一件对一件。 */
const reviewSkeletonHtml = (label = '正在读取复核队列'): string => `<div class="review review-workspace review-skeleton" data-skeleton="review" aria-busy="true" aria-label="${esc(label)}">
  <div class="reviewcontrols" data-section-nav><h2 class="review-category-title">复核分类</h2>
    <div class="reviewtabs" data-section-items>${REVIEW_LABELS.map((text, i) =>
      `<button type="button" disabled aria-selected="${i === 0}">${esc(text)}<span class="skeleton reviewcountskeleton" aria-hidden="true"></span></button>`).join('')}</div></div>
  <div class="reviewbulkbar reviewbulktoolbar" aria-hidden="true">${'<span class="skeleton reviewtoolskeleton"></span>'.repeat(3)}</div>
  <section class="reviewsection"><div class="reviewlist">${
    '<div class="skeletoncard" aria-hidden="true"><i></i><b></b><em></em></div>'.repeat(6)}</div></section></div>`;

const PLACEHOLDERS: Record<string, () => string> = {
  ...Object.fromEntries(Object.keys(CONFIGURATION_SECTIONS).map(key => [`/settings/${key}`, () => configurationSkeletonHtml()])),
  '/data-cleanup': () => cleanupSkeletonHtml(),
  // /resource-sync 只是数据管理页上的一个锚点，占位也该是数据管理那张。
  '/resource-sync': () => cleanupSkeletonHtml(),
  '/review': () => reviewSkeletonHtml(),
  /* 采集来源是 812px 窄列里一叠同宽的 Fieldset：一块高清封面加六个来源。骨架画四块，
     那是首屏装得下的张数；说明那一句是静态文案，与数据无关，立刻显示。 */
  '/sources': () => `<div class="scraping-page"><p>高清图片可能要经代理才能下载，先检查连接。</p>
    ${pageSkeletonHtml('正在读取采集来源', { cards: true, count: 4, fill: false, className: 'cleanup-skeleton' })}</div>`,
};

/** 关注管理的骨架照这台浏览器上次选的视图画，排序框与方向键照地址栏上的那一档画：等数据的这段时间画成
 *  卡片、数据到了换成表格的话，同一次进入里版式会整个翻一遍。 */
export interface FollowSkeletonState {
  followLayout: string;
  followSort: string;
  followDir: string;
}

/** 这一页的加载态 HTML。 */
export function managementSkeletonHtml(path: string, { followLayout, followSort, followDir }: FollowSkeletonState): string {
  return boardPageSkeleton(path, { followLayout, ...(path === '/subscriptions' ? { followSort, followDir } : {}) })
    || (PLACEHOLDERS[path] ?? (() => pageSkeletonHtml('正在读取页面')))();
}

/** 骨架的键：同一个键说明是同一张，不必重画。 */
export const skeletonKeyOf = (html: string): string => String(html).match(/data-skeleton="([^"]*)"/)?.[1] || '';

/** 把加载态铺进容器。容器里已经是同一张骨架就不动：innerHTML 换新节点会把 shimmer 从头放一遍，
 *  整页刷新看到的就是同一段动画闪两次。 */
export function paintManagementPlaceholder(container: HTMLElement, placeholder: string): void {
  const painted = container.querySelector<HTMLElement>('[data-skeleton]')?.dataset.skeleton || '';
  const next = skeletonKeyOf(placeholder);
  if (!next || next !== painted) {
    container.innerHTML = placeholder;
    fitSkeleton(container);
  }
}
