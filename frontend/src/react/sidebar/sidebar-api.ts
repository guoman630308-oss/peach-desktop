/* 侧栏（常驻组件 `sidebar`，`sidebar-island.tsx`）对壳的契约。
 *
 * 它画的是左侧抽屉 `#drawer` 里滚动的那一层：标题行的空槽、导航那一列、导航上那块滑动玻璃，
 * 以及导航下面按语境出现的筛选分组（目录与资料页的来源、时长、产地、画幅、创作者、标签，
 * 关注页的来源、时长与内容标签）。抽屉本身、它的开合、底栏（明暗、配色、设置三枚键）、品牌与开合键归壳：
 * 它们是壳里几张浮层的锚点，壳在 `attached` 时把品牌与开合键挪进标题行的空槽。
 *
 * 筛选、路由与取数仍归壳。成员、按下态与数字由壳算好推进来，点下去的动作回到壳的
 * `commitContextFilter`、`navTo` 与关注页的筛选。导航顺序读的是同一份 `appSettings`
 * （`host.store`）：设置面板或这一列自己拖动改了顺序，侧栏按 store 的通知当场重排。 */
import type { SettingsStore } from '../../settings-store';

/** 来源那一行开头的小记号：本地与在线是雪碧图字形，网盘是官方站标，其余按计费画一个点。 */
export type SidebarDot =
  | { kind: 'glyph'; name: string }
  | { kind: 'image'; src: string }
  | { kind: 'cost'; cost: 'free' | 'metered' };

/** 一枚筛选键。`label` 已由壳按 `LOC`、`tagLabel` 或聚合里的显示名换好。 */
export interface SidebarChip {
  value: string;
  label: string;
  n: number | null;
  dot?: SidebarDot;
  /** 来源脱盘：留在名单里但点不了，`title` 说原因。 */
  offline?: string;
}

/** 目录与资料页那一份聚合，按组排好。 */
export interface SidebarFacets {
  locations: SidebarChip[];
  regions: SidebarChip[];
  orientations: SidebarChip[];
  /** 资料页是某位创作者自己时已经去掉了这个人。 */
  creators: SidebarChip[];
  tags: SidebarChip[];
  tech: SidebarChip[];
  followTags: SidebarChip[];
  /** 库里有时长读数时才出现时长那一组。 */
  duration: boolean;
}

export type SidebarContent =
  /** `key` 换了就是一份新聚合：分组的默认展开重新按按下态定，计数徽标按上一次的值决定弹不弹。 */
  | { kind: 'catalog'; key: string; facets: SidebarFacets }
  /** 关注页与关注详情：内容标签的计数由壳按可见条目或这一条自己的标签算好。来源与时长只在关注页有：
   *  来源按全库列（选中一个之后别的还在），`duration` 说库里有没有时长读数；按下态与时长两端读
   *  `filters` 里的 `provider`、`dur_min`、`dur_max`。详情只给标签。 */
  | { kind: 'follow'; tags: SidebarChip[]; selected: string[]; providers?: SidebarChip[]; duration?: boolean };

export interface SidebarProps {
  /** null 时只画导航（作品详情、管理区与索引页，或换页后聚合还没回来）。 */
  content: SidebarContent | null;
  /** 当前语境的筛选（首页的 `state` 或资料页、详情的那一份）：按下态与时长两端照它画。 */
  filters: Record<string, string | number | null | undefined>;
  /** 就地刷新的数字：成员不动，计数照这一份改，名单里没有的记 0；「展开全部」照它摊开。 */
  latest: SidebarFacets | null;
}

export interface SidebarHost {
  /** 侧栏画进去的那一层（`#drawerScroll`），也是常驻组件的宿主。覆盖式滚动条由壳挂在它上面，轨道住在 `#drawer` 里。 */
  scroll: HTMLElement;
  /** 界面偏好的那一份 store（`appSettings`）；侧栏只读写其中的 `sidebarOrder`。 */
  store: SettingsStore<{ sidebarOrder: string[] }>;
  /** 侧栏能放的全部入口，`[键, 名称, 字形]`；首页那一项的键是空串。 */
  navCatalog: readonly (readonly [string, string, string])[];
  /** 这一项此刻亮不亮（壳的 `navOn`，读地址与目录筛选）。 */
  navOn(key: string): boolean;
  navTo(key: string): void;
  /** 一枚目录筛选键：`multi` 的组按逗号列表增减，其余组单选、再点一次取消。 */
  toggleChip(key: string, value: string, multi: boolean): void;
  /** 时长两端松手（分钟）：0 与 180 表示这一端不限。 */
  setDuration(min: number, max: number): void;
  /** 目录页上的关注标签：去关注页「已保存」那一份，只按这一枚标签筛。 */
  openFollowTag(tag: string): void;
  /** 关注页与关注详情的内容标签：换成只按这一枚标签筛。 */
  selectFollowTag(tag: string): void;
  /** 关注页的来源：单选，再点一次取消。 */
  selectFollowProvider(provider: string): void;
  /** 关注页的时长两端松手（分钟），口径同 `setDuration`。 */
  setFollowDuration(min: number, max: number): void;
  /** 路由树画出首帧之后、句柄交出之前，在同一个任务里调一次：壳把品牌与开合键挪进标题行。 */
  attached(): void;
}

export interface SidebarApi {
  /** 整份 props；壳手上留一份，每次只改其中几项再整份推进来。返回时已经画好。 */
  render(props: SidebarProps): void;
  /** 导航的按下态要重读（路由变了、目录筛选改了 JAV 或竖屏）：玻璃从旧项滑到新项。返回时已经画好。 */
  navChanged(): void;
}
