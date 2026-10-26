/* 馆藏卡片网格的对外契约：props、卡片版式、壳递进来的助手与动作。
 * 条目与分页的形状以 `/api/items`（`src/peach/web_router.py`）为准。 */

import type { FaceRef } from '@peach/card-art';
/** 卡片署名行上一个实体的引用：头像取图与取景都从这里来。 */
export interface MediaEntityRef extends FaceRef { id: number }

/** `/api/items` 的一条。只列卡片读到的字段，其余原样带着，壳的缓存要的是整条。 */
export interface MediaItem {
  id: number;
  name?: string;
  code?: string;
  is_jav?: boolean;
  medium?: string;
  location?: string;
  cost?: string;
  size?: number | null;
  duration?: number | null;
  release_date?: string | null;
  play_seconds?: number | null;
  play_count?: number | null;
  leave_ratio?: number | null;
  has_cover?: boolean;
  cover_key?: string;
  /** 封面文件的内容版本，拼进封面地址的 `&v=`。 */
  cover_version?: string;
  has_thumb?: boolean;
  has_local_poster?: boolean;
  follow_thumb_url?: string;
  follow_item_id?: number | null;
  follow_tags?: string[];
  tags?: string[];
  performers?: string[];
  performer_entities?: (MediaEntityRef | null)[];
  performer_total?: number;
  creator?: string;
  creator_entity?: MediaEntityRef | null;
  studio?: string;
  why?: string;
  feedback?: string;
  disposal?: string;
  disposal_reason?: 'manual' | 'junk' | 'duplicate' | 'missing' | null;
  watch_later?: boolean;
  part_group?: {
    key: string; seed_id: number; count: number; title?: string; total_size?: number; total_duration?: number;
  } | null;
  edition_group?: { key: string; seed_id: number; count: number; editions: string[] } | null;
  [field: string]: unknown;
}

/** `/api/items` 的一页。第一页带总数，往后的页只说还有没有。 */
export interface MediaPage { items: MediaItem[]; total?: number; work_total?: number; has_more?: boolean }

export interface MediaPageOptions { limit: number; sort?: string; dir?: string }

/** 这一屏卡片的版式。`active` 是番号版式开关生效（JAV 模式或首页版式），`size` 是大图／小图，
 *  `portrait` 是这一屏整列都是竖屏（显式筛了竖屏），`javImage` 是「JAV 默认封面」设置。 */
export interface MediaCardLayout { active: boolean; size: 'big' | 'small'; portrait: boolean; javImage: string }

/** 卡片上仍由遗留层拼的几段 HTML。封面、头像与悬停预览直接取 `@peach/card-art`。 */
export interface MediaCardHelpers {
  /** 来源角标（遗留层 `srcBadge`）。 */
  badgeHtml(location: string, cost: string): string;
  /** 标题的 HTML：番号 + 版次徽章 + 片名，非番号作品是转义后的名字（遗留层 `javTitleHtml`）。 */
  titleHtml(item: MediaItem, raw: string): string;
  /** 同一标题的纯文本，用于无障碍名称（遗留层 `javDisplayName`）。 */
  displayName(item: MediaItem, raw: string): string;
  /** 标签键到界面上的名称。 */
  tagLabel(tag: string): string;
}

/** 卡片上的动作。打开与换页都归壳：详情页、沉浸模式和小窗是遗留层的整页视图。 */
export interface MediaCardActions {
  /** 播放墙复用舞台的单一播放器；退出时可把正在播放的实例交给小窗。 */
  wallPlayer?(item: MediaItem, frame: HTMLElement, signal: AbortSignal): Promise<(continuePlaying?: boolean) => void>;
  /** 打开详情，认领同一作品正在播放的实例。 */
  wallDetails?(item: MediaItem, anchor: HTMLElement): void;
  /** 打开一张作品卡：小窗开着时在小窗里换片，分卷与版次组进各自的队列，其余打开详情。 */
  open(item: MediaItem, anchor: HTMLElement): void;
  /** 回收站里的一张：视频打开详情，本地图片在新标签页看原图，其余切换选中。 */
  openResource(item: MediaItem, anchor: HTMLElement): void;
  /** 竖屏带里的一张：从它开始进沉浸模式。 */
  openShort(item: MediaItem): void;
  /** 竖屏带标题上那枚键：进沉浸模式。 */
  openShorts(): void;
  /** 打开以这一条为种子的 Mix。 */
  openMix(seedId: number, anchor: HTMLElement): void;
  openEntity(kind: string, name: string): void;
  /** 打开「未归属」那一批。 */
  openUnowned(): void;
  /** 卡片上的标签：只看这个标签，已经在筛它就取消。在哪一屏点就在哪一屏生效。 */
  toggleTag(tag: string): void;
  /** 切换一张的选中；`range` 是按住 Shift 连选。 */
  toggleSelection(id: number, range: boolean): void;
  /** 切换稍后看。写 ledger；成功与撤销都把新值回给 `onChange`，失败由壳报。 */
  watchLater(item: MediaItem, onChange: (on: boolean) => void): Promise<void>;
  /** 回收站卡上那枚键：还原或移入回收站。写 ledger，做完重读目录并给撤销；失败由壳报，再抛出。 */
  resourceOperation(item: MediaItem, operation: 'restore' | 'dispose'): Promise<void>;
  /** Mix 翻页要的相关作品，每个种子只取一次，和点开后的队列共用。 */
  mixRelated(seedId: number): Promise<MediaItem[]>;
  /** 此刻能不能悬停翻页：多选、遮挡、减少动效与滚动中都回 false。 */
  canFlip(): boolean;
}

/** 馆藏卡片网格。三种取数：
 *  - `catalog`：目录（`/` 与筛选态、回收站），筛选态由壳的 `state` 递进；
 *  - `entity`：资料页作品区，取数由壳给（`fetchPage`），第一页随页头一起取来（`initial`）；
 *  - `items`：壳手上已有的一批（详情页的接着看），不取数。 */
export interface CatalogGridProps {
  mode: 'catalog' | 'entity' | 'items';
  /** 独立全屏播放墙。 */
  wall?: boolean;
  helpers: MediaCardHelpers;
  actions: MediaCardActions;
  layout: MediaCardLayout;
  selectMode: boolean;
  selected: ReadonlySet<number>;
  seekSeconds: number;
  /** 刷新代次：壳要求重读时加一，查询随之换键重取，不重挂。 */
  revision: number;
  /** 骨架的 HTML（遗留层 `pageSkeletonHtml`），和壳首屏铺的是同一份。 */
  skeletonHtml(): string;
  /** 横排接上拖动滚动（遗留层 `wireDrag`）。 */
  wireDrag(el: HTMLElement): void;
  /** 一次取数落定（成功、为空或失败）：`revision` 是那一次的代次，壳据此放行等着它的调用方。 */
  settled?(revision: number): void;

  /* catalog 模式 */
  filters?: Readonly<Record<string, string>>;
  batchSize?: number;
  groupCollapse?: boolean;
  /** 首页默认列表排除竖屏（另有竖屏带承接）；判据归壳。 */
  excludeVertical?: boolean;
  /** 每一页第 8 位插一张 Mix。 */
  mix?: boolean;
  /** 每接一页插一条竖屏带。 */
  shorts?: boolean;
  /** 每接一页报一次：总数与此刻显示的卡数（Mix 与竖屏带不算）。壳据此推筛选条的读数、重画批量条，
   *  回收站把读数写在说明行上。 */
  onCount?(total: number, shown: number): void;
  /** 首屏取数失败：没有读数可报，壳据此把筛选条那条读数微光收起。 */
  onCountFailed?(): void;
  /** 空态 HTML。`libraryEmpty` 是整个馆藏都还没有作品。 */
  emptyHtml?(state: { trash: boolean; libraryEmpty: boolean }): string;
  /** 此刻能不能自动续页：管理区或索引页盖在目录上时不续。 */
  canLoadMore?(): boolean;

  /* entity 模式 */
  entityKey?: string;
  listScope?: string;
  initial?: MediaPage | null;
  fetchPage?(offset: number, signal: AbortSignal, options?: MediaPageOptions): Promise<MediaPage>;

  /* items 模式 */
  items?: MediaItem[];
  variant?: 'grid' | 'next';
}
