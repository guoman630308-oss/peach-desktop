/* 侧栏配色卡（常驻组件 `glow-picker`，`glow-picker-island.tsx`）与壳之间的接缝。
 *
 * 卡的外壳（`#boardGlowMenu`：popover、玻璃材质、锚定与开合）由壳建、由壳的 `wireAnchoredMenu` 管；
 * 路由树只往里画内容。读写的是壳那一份界面偏好 store，钮上那两枚小圆与设置面板都订阅同一份。 */
import type { GlowSettings } from '@peach/appearance';

import type { SettingsStore } from '../../settings-store';

export interface GlowPickerHost {
  /** 卡本身，也就是常驻组件的宿主：路由树把内容直接画成它的子节点。 */
  root: HTMLElement;
  store: SettingsStore<GlowSettings>;
  /** 「详细设置」：收起这张卡，开设置面板「界面」那一页并滚到光晕参数。 */
  openDetails(): void;
}
