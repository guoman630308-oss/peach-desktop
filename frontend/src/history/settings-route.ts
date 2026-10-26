export const PREFERENCE_SECTIONS = {
  appearance: '外观与导航', playback: '浏览与播放', jav: 'JAV 与新作', search: '搜索与推荐', content: '内容显示',
} as const;
export const CONFIGURATION_SECTIONS = {
  library: '媒体库与入库', subscriptions: '来源与订阅', downloads: '下载渠道', network: '网络与访问', maintenance: '应用与维护',
  diagnostics: '系统诊断',
} as const;
export type ConfigurationPath = `/settings/${keyof typeof CONFIGURATION_SECTIONS}`;
export function configurationSection(path: string): string {
  return CONFIGURATION_SECTIONS[path.split('/')[2] as keyof typeof CONFIGURATION_SECTIONS] || CONFIGURATION_SECTIONS.library;
}
export const PREFERENCE_PATHS = ['/settings', ...Object.keys(PREFERENCE_SECTIONS).map(key => `/settings/${key}`)];
export const isPreferencePath = (path: string): boolean => PREFERENCE_PATHS.includes(path);
export function preferenceSection(path: string): string {
  return PREFERENCE_SECTIONS[path.split('/')[2] as keyof typeof PREFERENCE_SECTIONS] || PREFERENCE_SECTIONS.appearance;
}
export function settingsPath(title: string): string {
  const key = Object.entries({ ...PREFERENCE_SECTIONS, ...CONFIGURATION_SECTIONS }).find(([, value]) => value === title)?.[0];
  return `/settings/${key || 'appearance'}`;
}
export interface SettingsBackground {
  pathname: string; search: string; state: unknown; seq: number;
}
export const settingsBackgroundOf = (state: unknown): SettingsBackground | null => {
  const background = (state as { settingsBackground?: SettingsBackground } | null)?.settingsBackground;
  return background && typeof background.pathname === 'string' ? background : null;
};
