/* 实体资料页的交集条、玻璃筛选浮层与内容区头（ADR-0031 第 10d 步）：数据形状。
 *
 * entity-page 与应用域控制器提供筛选、计数、读数和排序项，本模块只绘制这些 props。
 * 筛选地址由控制器写入，再经 updateManagedRoute 推进新值，不重挂；按下态先于内容请求更新。
 * 动作通过接口交回页面与控制器，本模块不拼请求。 */

/** 这一页此刻摆的是哪一类东西。 */
export type EntityView = 'people' | 'videos' | 'photos' | 'online';

/** 左端那一组视图键。只有一类东西时壳不给（`null`）：一枚孤零零的键没有可切的对象。 */
export interface EntityViewKeys {
  /** 这一组的无障碍名：有名册时是「页面视图」，否则是「媒体类型」。 */
  label: string;
  /** 名册键：事务所页是艺人，片商页是旗下厂牌。 */
  people: { label: string; count: number; icon: string } | null;
  /** 本地一部视频都没有、只有关注来源时不出：那时作品视图里什么也没有，进页就落在在线。 */
  videos: { count: number } | null;
  photos: { count: number } | null;
  /** 关注里绑在这位创作者名下的更新（ADR-0096），`count` 同资料卡那一句「N 项更新」。 */
  online: { count: number } | null;
}

/** 一枚标签胶囊。`label` 已按壳的 `tagLabel` 译好，`n` 是这一页里带这个标签的视频数。 */
export interface EntityFilterTag {
  k: string;
  label: string;
  n: number;
  selected: boolean;
}

/** 交集条上的一颗：`clear` 撤掉创作者／厂牌／归属那一条，`untag` 撤掉一个标签。 */
export interface EntityComboItem {
  kind: 'clear' | 'untag';
  key: string;
  label: string;
}

/** 一枚排序键。`ariaLabel` 播报的是点下去会得到什么（同首页那一排），`dir` 只在选中那枚上有值。 */
export interface EntitySortKey {
  key: string;
  label: string;
  pressed: boolean;
  dir: 'asc' | 'desc' | '';
  ariaLabel: string;
}

/** 分段开关的一格：值、名字和雪碧图里的字形。 */
export type SegmentOption = readonly [value: string, label: string, symbol: string];

/** 作品视图的表头：排序键，JAV 语境下多一组卡片版式。 */
export interface EntityVideoHead {
  sorts: EntitySortKey[];
  jav: { layout: string; options: readonly SegmentOption[] } | null;
}

/** 照片视图的表头。`back` 为真是在一个图集里：左边一枚「全部照片」，右边多两枚源文件键。 */
export interface EntityPhotoHead {
  back: boolean;
  /** 本地图片有几张；只有样张时是 0，换一批那枚键不出。 */
  shuffle: boolean;
  layout: string;
  layouts: readonly SegmentOption[];
  /** 图集的 id，源文件键按它定位；整页照片墙时为 0。 */
  setId: number;
}

/** 在线视图的那几样，同关注页那条浮层：视频／图片两枚圆键、状态一排、来源站标与标签，下排换一批、
 *  图片墙的布局与「仅显示图片」、排序键。来源与标签只数这一位名下的条目（`author_facets`）。 */
export interface EntityOnlineHead {
  media: 'videos' | 'images';
  /** 两档各有几组；一组图片都没有时不出这两枚键，同关注页。 */
  mediaCounts: { videos: number; images: number } | null;
  photoLayout: string;
  photoLayouts: readonly SegmentOption[];
  imagesOnly: boolean;
  status: string;
  statuses: readonly (readonly [key: string, label: string])[];
  provider: string;
  providers: readonly (readonly [key: string, label: string])[];
  /** `cat` 是来源记的标签类型，药丸按它取类型色（`data-tag-cat`）。 */
  tags: readonly { k: string; label: string; n: number; selected: boolean; cat: string }[];
  sorts: EntitySortKey[];
}

export interface EntityFilterActions {
  /** 换视图（名册／视频／照片）。 */
  setView(view: EntityView): void;
  /** 四枚观看状态里的一枚。 */
  setState(state: string): void;
  toggleTag(tag: string): void;
  /** 撤掉交集条上的一颗创作者／厂牌／归属。 */
  clearFilter(key: string): void;
  /** 交集条末尾的「全部清除」。 */
  clearAll(): void;
  setSort(key: string): void;
  /** 换一批。视频视图由读数骨架说「在等」，照片视图的键自己转圈，等到这个 Promise 落定。 */
  reshuffle(): Promise<void>;
  setJavLayout(layout: string): void;
  setPhotoLayout(layout: string): void;
  /** 图集里的「全部照片」。 */
  photoBack(): void;
  /** 在线视图：换媒体、换状态、按下或抬起一个来源、一枚标签，换排序、换一批，图片墙的两样偏好。 */
  onlineMedia(media: 'videos' | 'images'): void;
  onlineShuffle(): void;
  onlineImagesOnly(on: boolean): void;
  onlineStatus(status: string): void;
  onlineProvider(provider: string): void;
  onlineTag(tag: string): void;
  onlineSort(key: string): void;
}

/** 仍由遗留层给的接线与 HTML。 */
export interface EntityFilterHelpers {
  /** 横滚行接上拖动与滚轮（`wireDrag`）。 */
  wireDrag(row: Element | null): void;
  /** 只接滚轮与两端渐隐、不接拖动（`wireHorizontalScroller`）：窄屏下才溢出的外层用它。 */
  wireScroller(row: Element | null): void;
  /** 图集那两枚源文件键（`sourceTools`）：定位与目录对账，照片详情里复用的是同一对。 */
  sourceToolsHtml(setId: number): string;
  /** 把上面那两枚接到 `revealSource`／`syncMissing`；`done` 在对账改动了这一组之后重开它。 */
  wireSourceTools(root: Element): void;
}

export interface EntityFilterProps {
  kind: string;
  name: string;
  view: EntityView;
  views: EntityViewKeys | null;
  /** 当前观看状态：''、fresh、later、flagged。 */
  state: string;
  states: readonly { k: string; label: string }[];
  tags: EntityFilterTag[];
  combo: EntityComboItem[];
  /** 表头左边那一句；`busy` 时换成骨架。 */
  readout: string;
  busy: boolean;
  video: EntityVideoHead | null;
  photo: EntityPhotoHead | null;
  online: EntityOnlineHead | null;
  actions: EntityFilterActions;
  helpers: EntityFilterHelpers;
}

/** 只在作品视图上成立的那几样（观看状态、标签、交集条）。照片和名册上它们数的都是视频，
 *  点下去还会把视图拨回视频，所以那两档只留视图键。 */
export const videoOnly = (view: EntityView): boolean => view === 'videos';
