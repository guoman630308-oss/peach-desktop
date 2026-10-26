/** 常驻媒体、面板与原生宿主适配层；内容由同一棵路由树画。 */
import { configureBatchDock, configureGlowPicker, configureImmerse, configureManageHeader,
  configureSettingsPanel, configureSidebar, configureStage } from '../react/entry';
import { prefetchManagedRoute } from '../react/router/managed-routes';
import { mountToaster, showToast as emitToast, unmountToaster } from '../react/toaster';
export { mountToaster } from '../react/toaster';
import { connectManagedRoutes, openManagedRoute } from '../history';
import type * as Bundle from '../react/bundle';
import type { ShellActions } from '../react/router/shell-actions';
import { loadLocalPlayerPolicy, resetLocalPlayerPolicy } from '../local-player';

let publish: ((actions: ShellActions) => void) | null = null;
let publishedActions: ShellActions | null = null;
let residentEpoch = 0;
const ownedContainers = new Set<HTMLElement>();
export function connectApplication(next: (actions: ShellActions) => void): () => void {
  publish = next;
  if (publishedActions) next(publishedActions);
  return () => { if (publish === next) publish = null; };
}
export function loadRouter(actions: ShellActions): Promise<void> {
  if (!publish) return Promise.resolve();
  publishedActions = actions;
  publish(actions);
  connectManagedRoutes(Promise.resolve(prefetchManagedRoute));
  return Promise.resolve();
}

/** 常驻首屏与当前应用代次绑定；卸载后的响应不能再挂宿主或发布句柄。 */
async function openApplicationResident(name: string, container: HTMLElement,
  place: (node: HTMLElement) => HTMLElement = node => node): Promise<void> {
  const epoch = residentEpoch;
  if (!container.isConnected) ownedContainers.add(container);
  const opened = await openManagedRoute(name, {}, {
    container, resident: true, isCurrent: () => !!publish && epoch === residentEpoch,
    place: () => place(container),
  });
  if (!opened || epoch !== residentEpoch || !publish) throw new DOMException('应用已卸载', 'AbortError');
}

export function disposeApplicationResidents(): void {
  resetLocalPlayerPolicy();
  residentEpoch += 1;
  publish = null; publishedActions = null;
  stage = null; stageReady = null; immerse = null; immerseReady = null;
  settings = null; settingsReady = null; sidebar = null; sidebarReady = null;
  manage = null; manageReady = null; batch = null; batchReady = null; glow = null;
  for (const container of ownedContainers) container.remove();
  ownedContainers.clear();
  unmountToaster();
}

let stage: Promise<Bundle.StageApi> | null = null;
let stageReady: Bundle.StageApi | null = null;
export function loadStage(host: Bundle.StageHost): Promise<Bundle.StageApi> {
  stage ??= (async () => {
    await loadLocalPlayerPolicy();
    const api = configureStage(host), container = document.createElement('div');
    container.dataset.stageHost = '';
    await openApplicationResident('stage', container, node => { document.body.append(node); return node });
    stageReady = api; return api;
  })();
  return stage;
}
export const stageApi = (): Bundle.StageApi | null => stageReady;
let immerse: Promise<Bundle.ImmerseApi> | null = null;
let immerseReady: Bundle.ImmerseApi | null = null;
export function loadImmerse(host: Bundle.ImmerseHost): Promise<Bundle.ImmerseApi> {
  immerse ??= (async () => {
    const api = configureImmerse(host), container = document.createElement('div');
    container.dataset.immerseHost = '';
    await openApplicationResident('immerse', container, node => { document.body.append(node); return node });
    immerseReady = api; return api;
  })();
  return immerse;
}
export const immerseApi = (): Bundle.ImmerseApi | null => immerseReady;
let settings: Promise<Bundle.SettingsPanelApi> | null = null;
let settingsReady: Bundle.SettingsPanelApi | null = null;
export function loadSettingsPanel(host: Bundle.SettingsPanelHost): Promise<Bundle.SettingsPanelApi> {
  settings ??= (async () => {
    const api = configureSettingsPanel(host), container = document.createElement('div');
    container.dataset.settingsHost = '';
    await openApplicationResident('settings-panel', container, node => { document.body.append(node); return node });
    settingsReady = api; return api;
  })();
  return settings;
}
export const settingsPanelApi = (): Bundle.SettingsPanelApi | null => settingsReady;
let sidebar: Promise<Bundle.SidebarApi> | null = null;
let sidebarReady: Bundle.SidebarApi | null = null;
export function loadSidebar(host: Bundle.SidebarHost): Promise<Bundle.SidebarApi> {
  sidebar ??= (async () => {
    const api = configureSidebar(host);
    await openApplicationResident('sidebar', host.scroll, node => { node.replaceChildren(); return node });
    host.attached(); sidebarReady = api; return api;
  })();
  return sidebar;
}
export const sidebarApi = (): Bundle.SidebarApi | null => sidebarReady;
let manage: Promise<Bundle.ManageHeaderApi> | null = null;
let manageReady: Bundle.ManageHeaderApi | null = null;
export function loadManageHeader(host: Bundle.ManageHeaderHost): Promise<Bundle.ManageHeaderApi> {
  manage ??= (async () => {
    const api = configureManageHeader(host);
    await openApplicationResident('manage-header', host.root, node => { node.replaceChildren(); return node });
    manageReady = api; return api;
  })();
  return manage;
}
export const manageHeaderApi = (): Bundle.ManageHeaderApi | null => manageReady;
let batch: Promise<Bundle.BatchDockApi> | null = null;
let batchReady: Bundle.BatchDockApi | null = null;
export function loadBatchDock(host: Bundle.BatchDockHost): Promise<Bundle.BatchDockApi> {
  batch ??= (async () => {
    const api = configureBatchDock(host);
    await openApplicationResident('batch-dock', host.root);
    batchReady = api; return api;
  })();
  return batch;
}
export const batchDockApi = (): Bundle.BatchDockApi | null => batchReady;
let glow: Promise<void> | null = null;
export function loadGlowPicker(host: Bundle.GlowPickerHost): Promise<void> {
  glow ??= (async () => { configureGlowPicker(host); await openApplicationResident('glow-picker',host.root) })();
  return glow;
}
export function showToast(host: Element, icons: Bundle.ToastIcons, id: string, request: Bundle.ToastRequest): void {
  mountToaster(host,icons); emitToast(id,request);
}
