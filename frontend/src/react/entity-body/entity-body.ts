/* 实体资料页的正文（ADR-0031 第 10e 步）：筛选浮层下面那一整块的数据形状。
 *
 * 这一块在三个视图之间切换：名册（事务所旗下艺人、片商旗下厂牌）、作品网格和照片墙。
 * entity-page 管理取数与页内视图，应用域控制器通过唯一历史入口写地址并推进筛选 props。
 * 正文按 props 绘制，换视图、筛选和排序时不重挂；动作经页面接口回到对应控制器。
 *
 * 作品网格就是馆藏卡片网格（`catalog-grid`）的 entity 模式，这里直接渲染那个组件，卡片的
 * 助手、动作、版式与选择状态原样递进去；名册摆的是索引页那一格（`PeopleGrid`）。 */
import type { CatalogGridProps, MediaCardActions, MediaCardHelpers, MediaPage } from '../catalog-grid/types';
import type { IndexPerson, PeopleLayout, PersonAvatar } from '../index/index-data';

/** 这一页此刻摆的是哪一类东西，与筛选浮层那一组视图键同一套词。 */
export type EntityBodyView = 'people' | 'videos' | 'photos';

/** 名册：随资料一起下来的那一批。`kind` 按索引页的词表给：事务所页是 `performers`，
 *  片商页是 `studios`；版式读的是索引页同一个设置值。 */
export interface EntityRoster {
  kind: 'performers' | 'studios';
  people: IndexPerson[];
  layout: PeopleLayout;
}

/** 卡片那几段遗留层 HTML，外加名册一格的取图（遗留层 `personRingHtml` 那条回落链）。 */
export interface EntityBodyHelpers extends MediaCardHelpers {
  personAvatar(item: IndexPerson, entityKind: string, big: boolean): PersonAvatar;
}

/** 名下一部作品的官方样张（`/api/photos` 的 `sets` 里 `kind` 为 `code` 的那几条）。样张地址留在
 *  服务端，墙和灯箱只递番号与序号。 */
export interface EntityCodeSet {
  code: string;
  name?: string;
  n: number;
  release_date?: string;
  site_label?: string;
}

/** 本地图片一张（`/api/photos`、`/api/photo-set` 的 `items`）。整条原样递给灯箱详情。 */
export interface EntityLocalPhoto { id: number; name?: string; [field: string]: unknown }

/** 样张一格。大图与缩略图的地址按番号与序号由服务端查（`sampleQuery`）。 */
export interface EntitySamplePhoto {
  sample: true;
  code: string;
  position: number;
  total: number;
  name: string;
  source: string;
}

export type EntityWallPhoto = EntityLocalPhoto | EntitySamplePhoto;

/** 照片视图这一屏。`codeSets` 只在整组照片里有（目录图集里是空的），各段排在本地图片前面；
 *  `items` 是本地图片里已经取回的那些，翻页往后接；`total` 是本地图片总数，段头读它。
 *  `revision` 是这一屏的代次：换一批、进出图集时加一，墙从头画，续页只追加不换代。 */
export interface EntityPhotos {
  revision: number;
  codeSets: EntityCodeSet[];
  items: EntityLocalPhoto[];
  total: number;
  hasMore: boolean;
}

export type PhotoSize = 'small' | 'big';
export type PhotoLayout = 'fixed' | 'masonry';

/** 卡片上的动作，外加照片墙那两样。名册一格点开走的是其中的 `openEntity`。 */
export interface EntityBodyActions extends MediaCardActions {
  /** 灯箱里本地图片那一枚「在资源管理器中显示」。成功由壳发回执，失败回一句原因。 */
  revealSource(id: number): Promise<string>;
  /** 本地图片的下一页。壳取回后推一份更长的 `photos` 进来；取不到就抛错，键下出重试。 */
  loadMorePhotos(): Promise<void>;
}

/** 从卡片网格原样递进去的那几样：版式、选择状态、缓存与骨架都是壳里同一份。 */
type SharedGridProps = Pick<CatalogGridProps,
  'layout' | 'selectMode' | 'selected' | 'seekSeconds' | 'wireDrag' | 'skeletonHtml'
  | 'groupCollapse' | 'canLoadMore' | 'listScope'>;

export interface EntityBodyProps extends SharedGridProps {
  kind: string;
  name: string;
  view: EntityBodyView;
  /** 只有事务所与片商页有；别的页面、或这一批是空的时候是 `null`。 */
  roster: EntityRoster | null;
  /** 作品视图的第一页。`null` 是壳正在取（换了筛选、排序或观看状态）：这一格铺骨架等它，
   *  不把跟头上筛选对不上的旧卡片留在屏幕上。 */
  items: MediaPage | null;
  /** 作品列表的代次。壳每发起一次新的作品请求就加一，网格按它换键，骨架与续页都从头开始。 */
  revision: number;
  /** 续页。筛选与排序由壳在闭包里带着，和第一页同一套口径。 */
  fetchPage: NonNullable<CatalogGridProps['fetchPage']>;
  /** 照片视图这一屏；还没进过照片视图时是 `null`。 */
  photos: EntityPhotos | null;
  /** 照片墙的大小档与版式。开关画在筛选浮层与顶栏里，这里只读，标在墙的 `data-size`/`data-layout` 上。 */
  photoSize: PhotoSize;
  photoLayout: PhotoLayout;
  helpers: EntityBodyHelpers;
  actions: EntityBodyActions;
}
