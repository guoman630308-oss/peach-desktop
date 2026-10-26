export const JAV_TABS = [['library', '馆藏'], ['discover', '发现'], ['wants', '想要']] as const;
export const javTab = (value: string | null) => JAV_TABS.some(([key]) => key === value) ? value! : 'library';
export const javPath = (tab: string) => javTab(tab) === 'library' ? '/?jav=1' : `/jav?tab=${tab}`;
export function javLibraryPath(search: string) {
  const params = new URLSearchParams(search);
  params.delete('tab'); params.set('jav', '1');
  return '/?' + params;
}
