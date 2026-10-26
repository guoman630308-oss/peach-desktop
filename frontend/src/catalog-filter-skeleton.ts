/* 首页筛选条（`catalog-filter` 岛）挂上之前的那一份骨架。
 *
 * 冷启动时两排头像与标签条要等 `/api/facets` 与 `/api/tops`，约一秒；React 包也还在路上。这段时间
 * 壳把这一串 HTML 铺进宿主，岛挂上时整块换掉。结构与属性照 `react/catalog-filter/catalog-filter-page.tsx`
 * 渲染出来的静止态写（浮层几何是 `FilterGlassRows` 合并后的类），几何在
 * `react/catalog-filter/catalog-filter.css` 一处定，所以换掉那一下不跳。
 *
 * 四枚视图由 state 决定，这一趟取数不改它们，所以现在就画成最终样子：`href` 是真地址，壳在宿主上
 * 委托接住点击。头像与标签只画占位，读数是一条宽度定死的微光。 */
import type { CatalogView } from './react/catalog-filter/catalog-filter';
import { islandShortcutsEntry } from './island-skeleton';
import { filterControlsSkeletonHtml } from './filter-controls-skeleton';
import type { CatalogFilterProps } from './react/catalog-filter/catalog-filter';

/** 每排摆多少格，同岛里空馆藏占位的 `EMPTY_SLOTS`，由容器裁到可用宽度。 */
const EMPTY_SLOTS = 64;

const esc = (value: string): string => value.replace(/[&<>"]/g, (ch) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch] as string);

const slots = (html: string): string => html.repeat(EMPTY_SLOTS);

export function catalogFilterSkeletonHtml(views: readonly CatalogView[], state: string, head?: Pick<CatalogFilterProps, 'sorts' | 'layout'>): string {
  const viewHtml = views.map((view) => `<a href="${esc(view.href)}" data-catalog-view="${esc(view.k)}"`
    + ` data-entity-press="" aria-pressed="${state === view.k}">${esc(view.label)}</a>`).join('');
  return `<div class="peach-react"><div class="contents" data-catalog-root="" data-loading="">`
    + `<div data-catalog-tiers="" aria-busy="true">`
    + `<div data-catalog-tier="performers" data-skeleton="tiers">`
    + slots('<span data-catalog-placeholder="performer" aria-hidden="true"><span></span><span>&nbsp;</span></span>')
    + `</div><div data-catalog-tier="studios">`
    + slots('<span data-catalog-placeholder="studio" aria-hidden="true"><span></span><span>&nbsp;</span></span>')
    + `</div></div>`
    + `<div role="group" aria-label="筛选与排序" data-filter-glass="" data-glass-pane="" data-filter-frame=""`
    + ` data-catalog-frame="" class="sticky top-topbar z-10 mb-5.5 flex flex-col mx-4">`
    + `<div role="group" aria-label="视图与标签" data-filter-row="top"`
    + ` class="flex h-12 min-w-0 items-center overscroll-x-contain px-3 py-2 gap-1.5 overflow-visible">`
    + `<div data-catalog-scroll=""><div role="group" aria-label="视图" data-catalog-views="">${viewHtml}`
    + `<span data-entity-sep="" aria-hidden="true"></span></div>`
    + `<div data-catalog-tags=""><span data-catalog-placeholder="tag" aria-hidden="true">`
    + '<span></span>'.repeat(EMPTY_SLOTS) + `</span></div></div></div>`
    + `<div data-filter-row="bottom" aria-busy="true" class="flex min-w-0 items-center gap-3 px-3 py-2 min-h-12">`
    + `<span data-catalog-summary=""><span data-catalog-readout=""><span data-skeleton="count" aria-hidden="true"`
    + ` class="relative inline-block h-3.5 w-37.5 rounded-md align-middle skeleton-sheen"></span></span>${islandShortcutsEntry()}</span>`
    + (head ? filterControlsSkeletonHtml(head.sorts, head.layout) : '')
    + `</div></div></div></div>`;
}
