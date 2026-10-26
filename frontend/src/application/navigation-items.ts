/** 应用导航的完整词表；默认可见顺序由 sidebar 的持久偏好决定。 */
export type SidebarKey = '' | 'performers' | 'studios' | 'tags' | 'jav' | 'flagged' | 'playlists'
  | 'follow' | 'immerse' | 'manage' | 'stats' | 'taste' | 'review' | 'data-cleanup' | 'trash'
  | 'follow-manage' | 'quality' | 'activity';
export type ManagementKey = 'stats' | 'taste' | 'review' | 'cleanup' | 'trash' | 'follow'
  | 'quality' | 'activity' | 'configuration';
export type NavigationItem<Key extends string = string> = readonly [key: Key, label: string, glyph: string];

export const SIDEBAR_ITEMS = [
  ['', '首页', 'home'],
  ['performers', '艺人', 'user-round'],
  ['studios', '厂牌', 'clapperboard'],
  ['tags', '标签', 'tags'],
  ['jav', 'JAV', 'jav'],
  ['flagged', '已标记', 'bookmark'],
  ['playlists', '播放列表', 'playlist'],
  ['follow', '关注', 'rss'],
  ['immerse', '沉浸模式', 'gallery-vertical-end'],
  ['manage', '管理', 'wrench'],
] as const satisfies readonly NavigationItem<SidebarKey>[];

export const MANAGE_SECTIONS = [
  ['stats', '统计', 'chart'],
  ['taste', '口味', 'heart'],
  ['review', '人工复核', 'square-check-big'],
  ['cleanup', '数据管理', 'hard-drive'],
  ['trash', '回收站', 'trash'],
  ['follow', '关注管理', 'rss'],
  ['quality', '高清版', 'sparkles'],
  ['activity', '任务中心', 'history'],
  ['configuration', 'Peach 配置', 'folder-cog'],
] as const satisfies readonly NavigationItem<ManagementKey>[];

export const MANAGE_MENU_SECTIONS: readonly ManagementKey[] = ['stats', 'taste', 'cleanup', 'follow', 'activity', 'configuration'];
export const getManageMenuSections = (configurable: boolean): readonly NavigationItem<ManagementKey>[] =>
  MANAGE_SECTIONS.filter(([key]) => MANAGE_MENU_SECTIONS.includes(key) && (key !== 'configuration' || configurable));

export const OPTIONAL_SIDEBAR_ITEMS: readonly NavigationItem<SidebarKey>[] = MANAGE_SECTIONS
  .flatMap(([key, label, glyph]): NavigationItem<SidebarKey>[] => key === 'configuration' ? []
    : [[key === 'follow' ? 'follow-manage' : key === 'cleanup' ? 'data-cleanup' : key, label, glyph]]);
export const NAV_CATALOG: readonly NavigationItem<SidebarKey>[] = [...SIDEBAR_ITEMS, ...OPTIONAL_SIDEBAR_ITEMS];
export const DIRECT_MANAGE_NAV: Readonly<Partial<Record<SidebarKey, ManagementKey>>> = {
  stats: 'stats', review: 'review', 'data-cleanup': 'cleanup', trash: 'trash',
  'follow-manage': 'follow', quality: 'quality', activity: 'activity',
};
