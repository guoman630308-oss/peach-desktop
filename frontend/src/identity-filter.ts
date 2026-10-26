/* 艺人页本地名册的身份分类：一组按身份筛名册的小号键，React 页（`react/index/index-page.tsx`）与
 * 进页骨架（`index-skeleton.ts`）共用这一张词表。
 *
 * 换分类只改地址，不等数据，所以骨架里就是最终长相：地址栏那一档是 primary、其余 secondary，
 * 只是还不接线。哪几类有人要等计数，计数到之前整排都摆出来，到了再收掉空的那几类。 */
import { islandButton } from './island-skeleton';

export const IDENTITY_CATEGORIES: readonly (readonly [string, string])[] = [
  ['all', '全部'], ['japanese_av', '女优'], ['amateur', '素人'], ['western', '西方'], ['blogger', '网黄博主'], ['animation', '动画作者'],
];

/** 页面那一行的容器类，骨架与页面同一串。 */
export const IDENTITY_ROW_CLASS = 'mb-4 flex flex-wrap gap-2';

export function identityFilterSkeletonHtml(category: string): string {
  const known = IDENTITY_CATEGORIES.some(([key]) => key === category) ? category : 'all';
  const buttons = IDENTITY_CATEGORIES.map(([key, label]) => islandButton({
    variant: key === known ? 'primary' : 'secondary', size: 'small', label, attrs: `aria-pressed="${key === known}"`,
  })).join('');
  return `<div class="peach-react"><div aria-label="身份分类" class="${IDENTITY_ROW_CLASS}">${buttons}</div></div>`;
}
