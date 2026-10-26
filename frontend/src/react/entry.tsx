/* 主界面内部的常驻组件配置接口，类型契约见 `bundle.d.ts`。
 * 页面与常驻组件由 Application 的同一棵路由树画；Toaster 的独立根由应用生命周期清理。
 * 共享缓存、减弱动效与弹出层容器见 `providers.tsx`。 */
import './styles.css';

export { configureBatchDock } from './batch-dock/batch-dock-island';
export { configureGlowPicker } from './glow-picker/glow-picker-island';
export { configureImmerse } from './immerse/immerse-island';
export { configureManageHeader } from './manage-header/manage-header-island';
export { prefetchManagedRoute } from './router/managed-routes';
export { configureSettingsPanel } from './settings-panel/settings-panel';
export { configureSidebar } from './sidebar/sidebar-island';
export { configureStage } from './stage/stage';
export { mountToaster, showToast } from './toaster';
