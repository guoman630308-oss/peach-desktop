/* 管理区页头：管理条、面包屑、页面标题与回收站说明行的视图模型与启动骨架。
 *
 * 页头归常驻组件 `manage-header`（`react/manage-header/`）。壳启动时同步把这里的骨架写进宿主
 * `[data-manage-header]`，React 包装载回来、路由树画上这一面的那一刻整块换掉；骨架与组件画的是同一份结构，
 * 判据（标题取哪个名字、哪几页画面包屑、标题在窄屏上收到哪一档）也只在这里写一次，两边共用。
 *
 * 当前在哪个管理区、菜单里列哪几项、回收站读数由壳算好推进来：路由表、`/healthz` 的
 * `configurable` 与目录计数都还在壳里。 */

/** `[键, 名称, 字形]`，同壳的 `MANAGE_SECTIONS`。 */
export type ManageEntry = readonly [key: string, label: string, glyph: string];

/** 回收站说明行的读数：目录网格每接一页报一次。 */
export interface TrashCount {
  total: number;
  shown: number;
}

export interface ManageHeaderProps {
  /** 当前管理区；空串时整块页头不画（首页、目录、关注页这些）。 */
  section: string;
  /** 解码过的 `location.pathname`。 */
  path: string;
  /** 全部管理区，标题按 `section` 从这里取名。 */
  sections: readonly ManageEntry[];
  /** 管理条上列的那几项（壳的 `manageMenuSections()`，「配置」只在本机列出）。 */
  menu: readonly ManageEntry[];
  /** 回收站这一次进页以来最近一次读数；还没读到时是 null，说明行铺同形占位。 */
  trash: TrashCount | null;
}

/* 数据管理那几张卡通往的子页（vercel.com/geist/breadcrumbs：有上一级页面的子页才画面包屑）。
   人工复核、回收站、高清版虽也保留侧栏直达入口，层级上仍从数据管理进；资源同步是 hub 上的
   就地操作，没有独立页面。数据管理之下的页面标题也取这里的名字，「数据管理」让给面包屑的上一级。 */
export const MANAGE_CRUMB_PAGES: Readonly<Record<string, string>> = {
  '/junk-files': '垃圾文件',
  '/duplicates': '重复文件',
  '/review': '人工复核',
  '/trash': '回收站',
  '/quality-goals': '高清版',
  '/sources': '来源和凭证',
};

/* 窄屏（≤760px）上标题收一档到 22px 的页面：正文是 812px 窄列的数据管理 hub、来源和凭证与配置页。
   其余管理页跟全站页面标题走 24px。 */
const COMPACT_TITLE_PATHS = new Set(['/data-cleanup', '/sources']);

/** 管理条上按下哪一项。数据管理那几张卡通往的子页从数据管理进，按下的是「数据管理」；
 *  别的页按自己的管理区，管理条上没有这一项时就没有按下项。 */
export function pressedManageKey(props: Pick<ManageHeaderProps, 'section' | 'path' | 'menu'>): string {
  const key = MANAGE_CRUMB_PAGES[props.path] ? 'cleanup' : props.section;
  return props.menu.some(([entry]) => entry === key) ? key : '';
}

export interface ManageHeaderView {
  title: string;
  /** 管理条上按下的那一项；空串时没有按下项。 */
  pressed: string;
  /** 面包屑当前那一项的名字；空串时不画面包屑。 */
  crumb: string;
  compact: boolean;
  /** 只有回收站有说明行。 */
  lede: { kind: 'none' } | { kind: 'skeleton' } | { kind: 'count'; text: string; total: number };
}

export const trashLedeText = ({ total, shown }: TrashCount) =>
  `${total.toLocaleString()} 个符合 · 显示 ${shown.toLocaleString()}`;

/** 页头这一刻画什么；不在管理区时是 null。 */
export function manageHeaderView(props: ManageHeaderProps): ManageHeaderView | null {
  const entry = props.sections.find(([key]) => key === props.section);
  if (!entry) return null;
  const crumb = MANAGE_CRUMB_PAGES[props.path] ?? '';
  return {
    title: (props.section === 'cleanup' && crumb) || entry[1],
    pressed: pressedManageKey(props),
    crumb,
    compact: COMPACT_TITLE_PATHS.has(props.path) || props.section === 'configuration',
    lede: props.section !== 'trash' ? { kind: 'none' }
      : props.trash ? { kind: 'count', text: trashLedeText(props.trash), total: props.trash.total }
        : { kind: 'skeleton' },
  };
}

const escape = (value: string) => value.replace(/[&<>"']/g, (char) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);
const glyph = (name: string) => `<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-${escape(name)}"/></svg>`;

/** 回收站说明行读数到之前的同形占位：左边一格按读数的宽，右端是「清空回收站」那颗键的尺寸。
 *  骨架与岛共用这一段，扫光动画跟全站 `.skeleton` 走。 */
export const TRASH_LEDE_SKELETON_HTML = '<span class="skeleton" data-trash-lede-skeleton="text" aria-hidden="true"></span>'
  + '<span class="skeleton" data-trash-lede-skeleton="action" aria-hidden="true"></span>';

/** 启动骨架：与岛同一份结构，岛接手时整块换掉。 */
export function manageHeaderSkeletonHtml(props: ManageHeaderProps): string {
  const view = manageHeaderView(props);
  if (!view) return '';
  const menu = props.menu.map(([key, label, mark]) => {
    const on = key === view.pressed;
    return `<button type="button" data-manage="${escape(key)}" aria-pressed="${on}"${on ? ' aria-current="page"' : ''}>`
      + `${glyph(mark)}<span>${escape(label)}</span></button>`;
  }).join('');
  const crumb = view.crumb
    ? `<nav data-manage-crumb="" aria-label="Breadcrumb"><ol><li><a href="/data-cleanup">数据管理</a>${glyph('chevron-right')}</li>`
      + `<li aria-current="true"><span>${escape(view.crumb)}</span></li></ol></nav>`
    : '';
  const lede = view.lede.kind === 'none' ? ''
    : `<p class="mono" data-manage-lede="">${view.lede.kind === 'skeleton' ? TRASH_LEDE_SKELETON_HTML
      : `<span data-lede-text="">${escape(view.lede.text)}</span>${view.lede.total ? EMPTY_TRASH_HTML : ''}`}</p>`;
  return `<nav data-manage-bar="" aria-label="管理"><div data-manage-menu="">${menu}</div></nav>`
    + crumb
    + `<h2 data-manage-title=""${view.compact ? ' data-compact=""' : ''}>${escape(view.title)}</h2>`
    + lede;
}

const EMPTY_TRASH_HTML = '<button type="button" data-empty-trash="" title="永久删除回收站内容">清空回收站</button>';
