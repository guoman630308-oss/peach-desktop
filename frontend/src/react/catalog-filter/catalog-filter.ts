/* 首页筛选条（`catalog-filter` 页面内组件，ADR-0031 第 11b 步）：数据形状。
 *
 * 壳（`Application` 的 `buildBars` 一族）拥有目录的筛选、路由与取数：两排头像、标签条的成员与
 * 按下态、四枚视图、读数、排序键和版式都由壳算好当 props 递进来，岛只画。筛选一变壳就用
 * `updateManagedRoute` 推一份新的，不重挂；按下去的反馈（`aria-pressed` 与滑动玻璃）靠壳在发请求之前
 * 先推一次。动作全部回壳，岛不自己拼请求：排序、换一批与版式回壳后走 `loadCatalog`，也就是
 * `catalog-grid` 已有的那条重取入口。
 *
 * 交集条与排序键、换一批、版式开关和资料页那一条（`entity-filter`）是同一份清单、同一组组件。 */
import type { EntityComboItem, EntitySortKey, SegmentOption } from '../entity-filter/entity-filter';

export type { EntityComboItem, EntitySortKey, SegmentOption };

/** 头像条上的一位女优。圆框里那两层（首字母与头像图）由遗留层 `entityFaceImg` 拼：取景、
 *  回落图与异步解码都在那里，React 不拥有里面的节点。 */
export interface TierPerformer {
  name: string;
  ringHtml: string;
}

/** 厂牌那一排的一枚。`logo` 是方形图标的地址，没装标识时为空，只画首两个字。 */
export interface TierStudio {
  name: string;
  logo: string;
  fallback: string;
}

export type TierKind = 'performers' | 'studios';

/** 两排头像。每排手上这份是壳这一趟取回的整页；岛先摆一屏，横滚到右端再往下续，手上的用完
 *  再经 `actions.moreTops` 要下一页。`key` 变了（换了口径或换了一批）两排从头摆。 */
export interface CatalogTiers {
  key: string;
  performers: TierPerformer[];
  studios: TierStudio[];
}

/** 标签条上的一枚。`n` 为空说明这枚生效的标签不在这一批里，不印数字：印 0 会说成
 *  「这个标签下什么都没有」，而它此刻正筛着一屏内容。 */
export interface CatalogTag {
  k: string;
  label: string;
  n: number | null;
  selected: boolean;
}

/** 左端四枚视图：全部、没看过、稍后看、已标记。`href` 是它自己的地址，React 包没到时照常换页。 */
export interface CatalogView {
  k: string;
  label: string;
  href: string;
}

/** 读数右边那组分段开关：首页与 JAV 各存一份卡片版式。 */
export interface CatalogLayout {
  name: 'home-layout' | 'jav-layout';
  label: string;
  value: string;
  options: readonly SegmentOption[];
}

export interface CatalogFilterActions {
  currentHref?(): string;
  /** 头像条上的一格：打开那一位的资料页。 */
  openEntity(kind: 'performer' | 'studio', name: string): void;
  /** 四枚视图里的一枚。 */
  setView(view: string): void;
  toggleTag(tag: string): void;
  /** 撤掉交集条上的一颗创作者／厂牌／归属。 */
  clearFilter(key: string): void;
  /** 交集条末尾的「全部清除」。 */
  clearAll(): void;
  setSort(key: string): void;
  /** 换一批：网格与三层成员一起换，换批键转圈到这个 Promise 落定。 */
  reshuffle(): Promise<void>;
  setLayout(value: string): void;
  /** 某一排手上这份摆完了，要下一页。返回空数组就是到底了。 */
  moreTops(kind: TierKind): Promise<TierPerformer[] | TierStudio[]>;
}

/** 仍由遗留层给的接线。 */
export interface CatalogFilterHelpers {
  /** 横滚行接上拖动与滚轮（`wireDrag`）。 */
  wireDrag(row: Element | null): void;
  /** 只接滚轮与两端渐隐、不接拖动（`wireHorizontalScroller`）：窄屏下才溢出的外层用它。 */
  wireScroller(row: Element | null): void;
}

export interface CatalogFilterProps {
  /** 两排头像。首屏还在等两个聚合查询时为空：两排与标签条摆占位加一道微光，四枚视图与下排照常。 */
  tiers: CatalogTiers | null;
  /** 空馆藏：两排与标签条摆一行占位，说的是「这里将来会有东西」。 */
  empty: boolean;
  /** 标签条成员，已选的排在最前；前 `tagFirst` 枚先摆，其余横滚到右端再续。 */
  tags: CatalogTag[];
  tagFirst: number;
  views: readonly CatalogView[];
  /** 当前视图：''、fresh、later、flagged、trash。 */
  state: string;
  /** 读数。取数期间为空，换成一条微光；`false` 是这一趟取数失败，读数收起，失败与重试由网格那条提示说。 */
  count: { total: number; shown: number } | null | false;
  /** 回收站是待清理队列：读数挂在说明行上，这里没有下排。 */
  trash: boolean;
  sorts: EntitySortKey[];
  layout: CatalogLayout | null;
  /** 换一批这一趟：三层与标签铺一层微光，换批键转圈。 */
  refreshing: boolean;
  /** 筛选条被收起（资料页、索引页与管理页）：浮层两排不画，只留两排头像。 */
  offscreen: boolean;
  combo: EntityComboItem[];
  /** 交集条只在目录铺在屏幕上时有所指；索引页、资料页与管理页盖住目录时收起。 */
  comboHidden: boolean;
  /** 交集条的宿主（`#combo`）：它住在正文里、网格上面，不和筛选条挨着。 */
  comboHost: Element | null;
  actions: CatalogFilterActions;
  helpers: CatalogFilterHelpers;
}

/** 首屏先摆多少、往后一次续多少。一排里每个头像都是一张要解码的图，把手上这份全画出来
 *  等于让首屏替一个多半不会滚到那么远的人买单。 */
export const ROW_FIRST = 24;
export const ROW_BATCH = 12;
/** 离右端还剩这么多就续：滚到那一刻才开始画，手底下已经是一段空白了。 */
export const ROW_AHEAD = 320;
/** 空馆藏占位每排摆多少格，由容器裁到可用宽度。 */
export const EMPTY_SLOTS = 64;
