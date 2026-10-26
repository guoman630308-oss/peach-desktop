import { resolveFollowSort, SORT_OPTIONS, type SortDir, type SortKey } from './follow-sort';
import { islandButton, islandSelect } from './island-skeleton';
import { configurationSkeleton } from './configuration-skeleton';
import { javPageSkeleton } from './jav-skeleton';
import { duplicatesSkeleton, qualityGoalsSkeleton, statsSkeleton, tasteSkeleton } from './management-skeletons';

/** 等待态复用页面容器，只有异步内容使用占位。 */
const line = (width = '60%') => `<span class="skeleton" style="width:${width}"></span>`;
const lines = () => `${line('80%')}${line('48%')}`;
const repeat = (html: string, count: number) => html.repeat(count);
/* 关注管理整页归 React，骨架整块画在 `.peach-react` 里，容器与按键都取 React 那侧渲染出的同一串
   类名（`react/follow-manage/`、`react/components/` 与 `island-skeleton.ts`）：页面那一列、读数带、
   分段控件、关注列表那张填充卡、创作者卡、来源行和表格外框。只有等数据的读数画占位条。
   骨架整块 `inert`，焦点和点击都进不来；要等数据才能执行的键（检查、移除、全选、收起）
   另标原生 `disabled` 与 `data-skeleton-action`，外观是全站骨架共用的那副禁用面，
   视图切换、排序这类偏好控件本地就有答案，保持接管后的长相。 */
const WAITING = 'disabled data-skeleton-action';
const text = (width: string) => `<span class="skeleton skeleton-text" style="width:${width}"></span>`;
const block = (width: number, height: number, round = false) =>
  `<span class="skeleton" style="width:${width}px;height:${height}px;flex:none${round ? ';border-radius:50%' : ''}"></span>`;
const STAT = 'min-w-0 bg-background-secondary-default rounded-2xl shadow-card flex flex-col gap-3 px-6 py-5 max-sm:gap-2 max-sm:p-4';
const followReadings = () => `<div class="inline-grid w-full grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 xl:grid-cols-6">${['关注创作者', '启用来源', '检查失败', '未看更新', 'JAV 订阅', '未看新作'].map(term => `<div class="${STAT}"><span class="text-body-medium text-text-secondary">${term}</span><b class="text-title-1-medium tabular-nums text-text-primary">${text('3em')}</b></div>`).join('')}</div>`;
/** 页签与 React `follow-manage-page.tsx` 的 `TABS` 同名同序；选中的是地址栏 `?tab=` 那一枚，认不出时是第一枚。 */
const FOLLOW_TABS = [['list', '关注列表'], ['add', '添加关注'], ['feeds', 'JAV 订阅源']] as const;
const followTabKey = (tab = '') => (FOLLOW_TABS.some(([key]) => key === tab) ? tab : 'list');
const followTabs = (tab: string) => `<div data-section-nav data-section-items class="flex max-w-full flex-nowrap overflow-x-auto">${FOLLOW_TABS.map(([key, name]) => `<span class="whitespace-nowrap" aria-selected="${key === tab}">${name}</span>`).join('')}</div>`;
/** Board UI `Checkbox` 的未勾静止态；给了字就是「全选本页」那种带标签的一枚。 */
const followCheck = (label = '') => `<span class="group inline-flex items-center select-none gap-2"><span class="flex shrink-0 items-center justify-center rounded-sm size-4 border bg-background-primary-default shadow-xs border-border-checkbox-default"></span>${label ? `<span class="text-body-medium text-text-primary">${label}</span>` : ''}</span>`;

/* 工具行：检查全部是主按钮，视图切换是一组两颗 ghost 纯图标键、选中那颗由 `aria-pressed` 标出，
   排序框写着地址栏那一档的名字，方向键朝着同一头。工具行窄于 740px 时 React 把带字的键收成
   纯图标，骨架由 `09-skeleton.css` 的容器查询按同一条线收。 */
interface FollowToolbar { table: boolean; sort: SortKey; dir: SortDir }
const followToolbar = ({ table, sort, dir }: FollowToolbar) => {
  const name = SORT_OPTIONS.find(([key]) => key === sort)![1];
  return `<div class="flex flex-wrap items-center gap-2 ui-follow-skeleton-toolbar"><span class="contents max-sm:flex max-sm:min-w-0 max-sm:flex-1 max-sm:flex-col"><h3 class="mr-auto text-title-2-medium text-text-primary">关注列表</h3><span class="text-body-2-regular text-text-secondary">${text('132px')}</span></span>${islandButton({ glyph: 'refresh-cw', label: '检查全部', compact: true, attrs: WAITING })}<span data-follow-toolbar-controls class="contents max-sm:flex max-sm:w-full max-sm:items-center max-sm:gap-2"><span data-button-group role="group">${islandButton({ variant: 'ghost', glyph: 'layout-grid', attrs: `aria-pressed="${!table}"` })}${islandButton({ variant: 'ghost', glyph: 'table', attrs: `aria-pressed="${table}"` })}</span>${islandSelect(name)}${islandButton({ variant: 'secondary', glyph: dir === 'asc' ? 'arrow-up' : 'arrow-down' })}${table ? '' : islandButton({ variant: 'secondary', glyph: 'chevron-up', label: '全部收起', compact: true, attrs: WAITING })}</span></div>`;
};
/** 行尾那对动作键：检查这一条的更新、移除这一条，与 React `SourceRow` 同为小号次级纯图标键。 */
const followRowActions = () => `<span class="flex shrink-0 items-center gap-1">${islandButton({ variant: 'secondary', size: 'small', glyph: 'refresh-cw', attrs: WAITING })}${islandButton({ variant: 'secondary', size: 'small', glyph: 'trash', attrs: WAITING })}</span>`;
/** 一条来源：勾选、名字、站点图标、状态徽章、上次检查、动作，与 React `SourceRow` 同一串类名。 */
const followRow = () => `<div class="flex min-h-16 flex-wrap items-center gap-3 px-2 py-3 max-sm:gap-x-2 follow-skeleton-source">${followCheck()}<span class="flex min-w-0 grow basis-0 flex-col gap-0.5"><span class="text-body-medium">${text('8em')}</span></span><span class="flex shrink-0 items-center gap-1.5">${block(14, 14)}</span>${block(48, 24)}<span data-follow-source-controls class="contents max-sm:flex max-sm:w-full max-sm:flex-wrap max-sm:items-center max-sm:gap-1.5"><span class="shrink-0 text-body-2-regular whitespace-nowrap text-text-secondary max-sm:mr-auto max-sm:text-caption-1-regular">${text('113px')}</span>${followRowActions()}</span></div>`;
/** 一位创作者一张 raised 卡：头行是头像、名字、检查、站点图标、全选、收起，底下是来源行。 */
const followAuthor = (rows: number) => `<section class="min-w-0 bg-background-primary-default rounded-2xl shadow-card flex flex-col overflow-hidden p-2 follow-skeleton-author"><div class="flex flex-wrap items-center gap-3 px-2 py-2.5" data-follow-author-header data-open>${block(32, 32, true)}<b class="min-w-0 grow text-body-medium wrap-anywhere text-text-primary max-sm:basis-0">${text('92px')}</b>${islandButton({ variant: 'secondary', size: 'small', glyph: 'refresh-cw', attrs: WAITING })}<span data-follow-author-controls class="contents max-sm:flex max-sm:w-full max-sm:items-center max-sm:gap-2"><span class="flex shrink-0 items-center gap-1 max-sm:mr-auto">${block(14, 14)}${block(14, 14)}</span>${islandButton({ variant: 'secondary', size: 'small', glyph: 'check-check', label: '全选', attrs: WAITING })}${islandButton({ variant: 'secondary', size: 'small', glyph: 'chevron-up', label: '收起', attrs: WAITING })}</span></div><div data-source-divider>${repeat(followRow(), rows)}</div></section>`;
/** 表格骨架画多少行由每页条数决定，写死一个数会在别的页大小上多留或少留一屏。
 *  偏好存在旧层那份 `peach.settings.v1` 里，直接读它：骨架跑在 island 挂载之前，
 *  这时候页面自己的状态还不存在。 */
const followPageSize = (): number => {
  try {
    return Number(JSON.parse(localStorage.getItem('peach.settings.v1')||'{}').followPageSize)||20;
  } catch { return 20 }
};
/* 表格的列、列名与排序指示照 React 那张 Board UI `Table`：「选择」「操作」两列名只给读屏，
   当前排序落在哪一列，那一列的指示就朝哪头。 */
const TABLE_COLUMNS = [['select', ''], ['author', '创作者'], ['source', '来源'], ['provider', '站点'], ['status', '状态'], ['checked', '上次检查'], ['actions', '']] as const;
const COLUMN_OF: Partial<Record<SortKey, string>> = { name: 'author', source: 'source', provider: 'provider', status: 'status', checked: 'checked' };
const SORT_MARK = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12.7071 15.2929C12.3166 15.6834 11.6834 15.6834 11.2929 15.2929L7.70711 11.7071C7.07714 11.0771 7.52331 10 8.41421 10H15.5858C16.4767 10 16.9229 11.0771 16.2929 11.7071L12.7071 15.2929Z" fill="currentColor"/></svg>';
const followTable = (rows: number, { sort, dir }: { sort: SortKey; dir: SortDir }) => {
  const head = TABLE_COLUMNS.map(([id, name]) => {
    if (!name) return '<th></th>';
    const direction = COLUMN_OF[sort] === id ? ` data-direction="${dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    return `<th><span class="flex items-center gap-0.5">${name}<span data-sort-indicator${direction}>${SORT_MARK}</span></span></th>`;
  }).join('');
  const row = `<tr>${[followCheck(), `<span class="flex min-w-0 items-center gap-2">${block(32, 32, true)}${text('5em')}</span>`, text('10em'), `<span class="flex items-center gap-1.5">${block(14, 14)}${text('4em')}</span>`, block(48, 24), text('113px'), followRowActions()].map(cell => `<td>${cell}</td>`).join('')}</tr>`;
  return `<div data-board-data-table data-follow-table class="follow-skeleton-table"><div class="w-full overflow-x-auto"><table class="bui-table bui-table-sm"><thead><tr>${head}</tr></thead><tbody>${repeat(row, rows)}</tbody></table></div></div>`;
};
/** 分页行：条数与页码要等数据，每页几条是这台浏览器的偏好，照最终的字样写出来。 */
const followPager = (table: boolean, size: number) => `<div class="flex flex-wrap items-center justify-between gap-3"><span class="text-body-2-regular text-text-secondary">${text('111px')}</span>${islandSelect(`每页 ${size} ${table ? '条' : '位'}`, { size: 'sm' })}${block(204, 32)}</div>`;
/** 关注列表以外的页签：内容各不相同、要等数据才知道长什么样，只画那张填充卡和几行占位。 */
const followPanel = () => `<div class="min-w-0 bg-background-secondary-default rounded-2xl shadow-card flex flex-col gap-4 px-6 py-5 max-sm:px-4 ui-follow-skeleton-surface"><h3 class="text-title-2-medium text-text-primary">${text('120px')}</h3>${['72%', '56%', '64%', '40%'].map((width) => `<span class="text-body-medium">${text(width)}</span>`).join('')}</div>`;
type FollowSkeletonOptions = { followLayout?: string; followPageSize?: number; followSort?: string; followDir?: string; followTab?: string };
const followList = (options: FollowSkeletonOptions) => {
  const tab = followTabKey(options.followTab);
  if (tab !== 'list') return `<div class="peach-react"><div class="mx-auto flex w-full max-w-board flex-col gap-8">${followReadings()}<div class="flex flex-col gap-6">${followTabs(tab)}<div class="flex flex-col gap-4">${followPanel()}</div></div></div></div>`;
  const table = options.followLayout === 'table';
  const order = resolveFollowSort(options.followSort, options.followDir);
  const size = options.followPageSize || followPageSize();
  const content = table
    ? followTable(size, order)
    : `${followCheck('全选本页')}<div class="flex flex-col gap-3">${followAuthor(3)}${followAuthor(4)}${followAuthor(3)}</div>`;
  return `<div class="peach-react"><div class="mx-auto flex w-full max-w-board flex-col gap-8">${followReadings()}<div class="flex flex-col gap-6">${followTabs(tab)}<div class="flex flex-col gap-4"><div class="min-w-0 bg-background-secondary-default rounded-2xl shadow-card flex flex-col gap-4 px-6 py-5 max-sm:px-4 ui-follow-skeleton-surface" data-layout="${table ? 'table' : 'default'}">${followToolbar({ table, ...order })}${content}${followPager(table, size)}</div></div></div></div></div>`;
};

/** 详情骨架外层 `[data-skeleton="detail"]` 里面那一格：舞台岛自己画外层，里面照这一份写。格子与详情栏
 *  的几何是舞台的（`react/stage/stage.css`），骨架的底色与行距是壳的 `skeleton` 一族。 */
export function detailSkeletonBody(): string {
  return `<div data-stage-grid="" aria-hidden="true"><div data-stage-media="" class="ui-skeleton-detail-media skeleton"></div><aside data-stage-side=""><div data-stage-side-content="" class="ui-skeleton-lines">${line('85%')}${line('65%')}${repeat(lines(), 4)}</div></aside></div>`;
}

export function boardPageSkeleton(
  path: string, options: FollowSkeletonOptions = {},
): string {
  let body = '';
  if (path === '/stats') {
    body = statsSkeleton();
  } else if (path === '/jav') {
    body = javPageSkeleton(new URLSearchParams(location.search).get('tab') === 'wants' ? 'wants' : 'discover');
  } else if (path === '/taste') {
    body = tasteSkeleton();
  } else if (path === '/subscriptions') {
    body = followList(options);
  } else if (path === '/settings/library' || path === '/settings/diagnostics') {
    body = configurationSkeleton(path === '/settings/diagnostics' ? '系统诊断' : '媒体库与入库');
  } else if (path === '/tasks') {
    body = `<div class="ui-activitypage">${['正在进行', '被挡下的', '最近完成'].map(title => `<section class="activitysection"><h3 class="geist-fieldset-title">${title}</h3><div class="ui-activity-runs"><article class="cleanupfieldset activity-run"><div class="geist-fieldset-content ui-skeleton-lines">${line('35%')}${lines()}</div></article></div></section>`).join('')}</div>`;
  } else if (path === '/duplicates') {
    body = duplicatesSkeleton();
  } else if (path === '/quality-goals') {
    body = qualityGoalsSkeleton();
  } else if (path === '/playlists') {
    body = `<section class="ui-playlistpage"><header><div><h2>播放列表</h2><p>保存 Mix，按自己的顺序继续播放。</p></div><div class="playlistcreate ui-skeleton-lines"><span>新播放列表</span>${line('200px')}</div></header><div class="ui-playlistcards">${repeat(`<article class="card playlistcard"><div class="mixstack"><div class="pic skeleton"></div></div><div class="mixmeta"><span class="mav skeleton"></span><div class="mixcopy ui-skeleton-lines">${lines()}</div></div></article>`, 6)}</div></section>`;
  } else return '';
  return `<div class="board-page-skeleton" data-skeleton="board${path}" role="status" aria-label="正在读取页面"><div aria-hidden="true" inert>${body}</div></div>`;
}
