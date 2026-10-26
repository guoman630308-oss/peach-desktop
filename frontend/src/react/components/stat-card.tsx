/* 读数卡：一枚图标配一个名字、一个大数、一条脚注。
 *
 * 旧 `.metricstrip>button` 与 `.tastesummary` 是同一张卡，统计页让它兼做页签、口味页只读。
 * 三段各有各的内边距，脚注贴着卡底、比卡面暗一点点，所以卡片自己不留内边距，
 * 由三段分别排（`padding:16px 0 0`、`0 16px`、`12px 16px 16px`、`10px 16px`）。
 *
 * 图标底色按卡片在一排里的位次换，四张卡四个颜色；旧值是四个字面色，这里取图表色 token
 * 里最接近的四档（蓝、紫、绿、黄），差异登记在 `../boardui/ORIGIN.md`。 */
import type { ReactNode } from 'react';

import { cardClass, type CardOptions } from './card';
import type { Glyph } from './sprite-glyph';

/* 图标那个小方块居中用 flex 不用 grid：`grid` 与旧样式表同名，按 `../styles.css` 顶上的
 * `@source not inline(...)` 不生成这个工具类。 */

/** 一排四张卡的图标色。第五张起循环，旧样式表也只定到第四张。 */
const ACCENT = ['text-chart-6', 'text-chart-5', 'text-chart-7', 'text-chart-8'];
const ACCENT_TINT = ['bg-chart-6/10', 'bg-chart-5/10', 'bg-chart-7/10', 'bg-chart-8/10'];

/** 读数卡的卡面。宿主可能是 `<div>` 也可能是 React Aria 的 `Tab`，所以只给类名。 */
export function statCardClass(options: CardOptions = {}) {
  return `${cardClass({ ...options, padding: 'none' })} flex flex-col overflow-hidden pt-4 text-left`;
}

/** 一排读数卡：四张一排，窄屏折成两张一排。宿主可能是 `TabList`，所以类名也单独给一份。 */
export const STAT_STRIP = 'inline-grid w-full grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4';

/** 一排六张读数卡：宽屏一排，中等宽度三张一排，窄屏两张一排。关注管理顶上那一排是四张关注
 *  读数加两张 JAV 订阅读数；放进四列那一档会折成四加二，后两张孤零零挂在第二行。 */
export const STAT_STRIP_SIX = 'inline-grid w-full grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 xl:grid-cols-6';

/** 净面读数卡的卡面：数据管理顶上那一排入口与资源同步的结果。和上面那一档不是同一张卡——
 *  旧 `.board-plain-stat` 没有脚注带、没有阴影，内边距落在卡片自己身上（16px，三段之间
 *  8px），图标块是一格白底，读数下面那行脚注是三级灰小字。 */
export function plainStatClass({ interactive = false }: { interactive?: boolean } = {}) {
  return cardClass({ padding: 'none', radius: 'plain', interactive,
    className: 'flex min-h-33 flex-col gap-2 p-4 text-left' });
}

/** 一排五张净面读数卡：窄一档两张一排，手机上一张一排（遗留骨架 `.ui-cleanupstats` 的 1119／559 两档）。 */
export const PLAIN_STAT_STRIP = 'inline-grid w-full grid-cols-5 gap-4 max-plain-stat-pair:grid-cols-2'
  + ' max-plain-stat-single:grid-cols-1';

export function PlainStat(
  { label, icon: Icon, mark, aside, figure, meta, wrapMeta = false }:
  {
    label: string; icon?: Glyph; mark?: ReactNode; aside?: ReactNode;
    figure: string; meta?: string; wrapMeta?: boolean;
  },
) {
  return (
    <>
      <span className="flex min-w-0 items-center gap-2 text-body-medium text-text-secondary">
        {Icon || mark ? (
          <i aria-hidden className="flex size-8 shrink-0 items-center justify-center rounded-md bg-background-primary-default text-text-primary">
            {Icon ? <Icon aria-hidden className="size-4" /> : mark}
          </i>
        ) : null}
        <span className="min-w-0 truncate">{label}</span>
        {aside ? <span className="ml-auto shrink-0 text-caption-1-regular">{aside}</span> : null}
      </span>
      <strong className="block text-title-1-medium tabular-nums text-text-primary">{figure}</strong>
      <span className={`block min-h-4 text-caption-1-regular text-text-tertiary ${wrapMeta ? '' : 'truncate'}`}>
        {meta}
      </span>
    </>
  );
}

export function StatCard(
  { label, icon: Icon, accent = 0, figure, footer }:
  { label: string; icon?: Glyph; accent?: number; figure: string; footer?: ReactNode },
) {
  return (
    <>
      <span className="flex min-w-0 items-center gap-2 px-4 text-body-regular text-text-secondary max-sm:gap-1.5 max-sm:px-3">
        {Icon ? (
          <i aria-hidden className={`flex size-7 shrink-0 items-center justify-center rounded-lg max-sm:size-6 ${ACCENT[accent % 4]} ${ACCENT_TINT[accent % 4]}`}>
            <Icon aria-hidden className="size-4" />
          </i>
        ) : null}
        <span className="min-w-0 truncate">{label}</span>
      </span>
      {/* 窄屏两张一排时卡只有一百四十来像素，读数降一档字号；九位数再放不下就折行，不截掉开头几位。 */}
      <b className="px-4 pt-3 pb-4 text-title-1-medium tabular-nums text-text-primary wrap-anywhere max-sm:px-3 max-sm:pb-3 max-sm:text-title-3-medium">
        {figure}
      </b>
      {/* 脚注压在卡底：卡片被邻居撑高时空出来的那块留给读数上方，脚注不跟着飘。 */}
      <small className="mt-auto block min-h-9.5 bg-card-footer px-4 py-2.5 text-caption-1-regular text-text-secondary max-sm:px-3 max-sm:py-2">
        {footer}
      </small>
    </>
  );
}
