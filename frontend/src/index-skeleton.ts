/* 索引五页（艺人、卖家、厂牌、事务所、标签）的进页骨架与地址解析。
 *
 * 页头此刻就能给出最终样子：标题、读数的占位、版式开关、收藏入口、过滤框、页面级 Tabs 与本地艺人的身份分类，
 * 等的只有下面那块内容。
 * React 页取回首屏后整块换掉它，键和文字同页面那一份一致，换的那一下页头不跳。这里的控件不接线：骨架只在
 * 取数那一段露面。两处铺它：壳在冷启动落在索引页时先铺一遍（`renderInitialSurfaceLoading`），路由树的索引元素
 * 每次打开前再铺；屏幕上已经是同一张骨架就不重画。
 *
 * 版式偏好由调用方读好交进来：这个模块壳与 React 包各带一份，设置 store 只能有一份。 */
import { boardTabsHtml, fitSkeleton, iconSwitchHtml, indexSkeletonHtml } from '@peach/legacy/ui';
import { identityFilterSkeletonHtml } from './identity-filter';
import { islandShortcutsEntry, islandSearchInput } from './island-skeleton';

export type IndexPageKind = 'performers' | 'creators' | 'studios' | 'agencies' | 'tags';

/** 地址栏上的那几项。 */
export interface IndexParams {
  kind: IndexPageKind;
  q: string;
  scope: 'local' | 'online';
  view: 'alphabet' | 'cloud';
  category: string;
}

export const INDEX_TITLES: Readonly<Record<IndexPageKind, string>> = {
  performers: '艺人', creators: '卖家', studios: '厂牌', agencies: '事务所', tags: '标签',
};

/* 艺人索引版式，思路同 JAV 大图：列宽不变、只把图从圆框拉成竖幅，一屏里的人数不变而每张脸更大；紧凑就是
   圆头像那一屏。资料页的名册读的是同一个设置值。 */
const PEOPLE_LAYOUTS =[['big', '大图 · 竖幅头像', 'maximize'], ['compact', '紧凑 · 圆形头像', 'layout-grid']] as const;
/* 公司那一格摆的是方形标识而不是脸，说法跟着换；档位仍是同一个设置值。 */
const COMPANY_LAYOUTS = [['big', '大图 · 完整标识', 'maximize'], ['compact', '紧凑 · 圆形标识', 'layout-grid']] as const;
const MAKER_INDEX_KINDS = [['studios', '厂牌', 'clapperboard'], ['agencies', '事务所', 'briefcase']] as const;
const INDEX_SCOPES = [['local', '本地', 'hard-drive'], ['online', '在线', 'rss']] as const;
const PEOPLE_INDEX_TABS = [['performers', '艺人', 'user-round'], ['creators', '卖家', 'user-round'], ['online', '在线', 'rss']] as const;
const TAG_VIEWS = [['cloud', '标签云', 'tags'], ['alphabet', '字母表', 'text-aa']] as const;

/** 存着的版式偏好，认不出的回到大图。 */
export const peopleLayoutOf = (saved: unknown): 'big' | 'compact' => (saved === 'compact' ? 'compact' : 'big');

/** 地址栏上的那几项。范围与视图只认两个值；类型由页面按这一套词表核对，认不出的回到全部。 */
export function indexParams(kind: IndexPageKind, search: string): IndexParams {
  const params = new URLSearchParams(search);
  return {
    kind, q: params.get('q') || '', scope: params.get('scope') === 'online' ? 'online' : 'local',
    view: params.get('view') === 'cloud' ? 'cloud' : 'alphabet', category: params.get('category') || 'all',
  };
}

type Choices = readonly (readonly [string, string, string])[];
const tabs = (items: Choices, active: string, label: string) => boardTabsHtml(
  items.map(([value, text, symbol]) => ({ value, label: text, symbol })), { active, label, className: 'indextabs' });

/* 标签页 Tabs 下面还有一块两排的筛选玻璃（React 的 `FilterGlassRows`）：上排是类型药丸，下排是读数、按首字
   跳转和视图切换。药丸有哪几枚、读数多少、有哪些首字都要等数据，视图切换此刻就是最终那一档；块高与下边距同
   旧 `.board-filter-frame`，页面落地时它原地换成真的那一块，下面的内容不下跳。 */
const tagFilterSkeletonHtml = (view: string) => `<div class="board-filter-frame" data-filter-frame>
    <div class="tagbar" data-filter-row="top" data-skeleton-tier="pill" aria-label="标签类型"></div>
    <div class="count" data-filter-row="bottom"><span class="mono"><span class="countskeleton"></span></span>
      ${iconSwitchHtml('tag-view', '标签视图', TAG_VIEWS, view)}</div></div>`;

export function indexPlaceholderHtml({ kind, q, scope, view, category }: IndexParams, layout: 'big' | 'compact'): string {
  const title = INDEX_TITLES[kind] || '标签', people = kind !== 'tags', company = kind === 'studios' || kind === 'agencies';
  const switcher = people
    ? iconSwitchHtml('people-layout', title + '索引版式', company ? COMPANY_LAYOUTS : PEOPLE_LAYOUTS, layout) : '';
  return `<div class="ihead peach-react" data-index-head>
      <h2 class="disp indexheading">${title}</h2>
      ${people ? '<span class="mono" id="indexCount"><span class="countskeleton"></span></span>' : ''}${switcher}
      ${islandShortcutsEntry()}
      <div data-index-search class="ml-auto min-w-35 max-w-80 flex-1 max-board-narrow:order-1 max-board-narrow:basis-40">${islandSearchInput('', q, '过滤' + title)}</div>
    </div>
    ${kind === 'tags' ? tabs(INDEX_SCOPES, scope, '词表') : kind === 'performers' || kind === 'creators'
      ? tabs(PEOPLE_INDEX_TABS, kind === 'performers' && scope === 'online' ? 'online' : kind, '人物名册')
      : company ? tabs(MAKER_INDEX_KINDS, kind, '公司类型') : ''}
    ${kind === 'tags' ? tagFilterSkeletonHtml(view) : ''}
    ${kind === 'performers' && scope !== 'online' ? identityFilterSkeletonHtml(category) : ''}
    ${indexSkeletonHtml({ kind, layout, mode: view })}`;
}

const skeletonKeyOf = (html: string) => html.match(/data-skeleton="([^"]*)"/)?.[1] || '';

/** 把这一页的骨架铺进容器。屏幕上已经是同一张骨架就别重画：深链冷启动时首屏骨架先铺过一遍，innerHTML 换
 *  新节点会把 shimmer 从头放一遍。 */
export function paintIndexSkeleton(container: HTMLElement, params: IndexParams, layout: 'big' | 'compact'): void {
  const placeholder = indexPlaceholderHtml(params, layout);
  if (container.querySelector<HTMLElement>('[data-skeleton]')?.dataset.skeleton !== skeletonKeyOf(placeholder)) {
    container.innerHTML = placeholder;
    fitSkeleton(container);
  }
}
