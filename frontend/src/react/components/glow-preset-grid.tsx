/* 光晕预设那一格：设置面板「配色」那一组和侧栏底部配色卡上半张是同一组色块、同一份写入。两处都读
 * 同一份 store（`@peach/appearance` 的 `appSettingsStore`），点哪一边，另一边经 store 通知当场对齐。
 *
 * 选中一档写什么（光晕、玻璃面色相、强调色一起换）在 `appearance/glow.ts` 的 `chooseGlowPreset`；
 * 这里只画球。「自定义」那一格会出现或消失，按档名做 key，正落着焦点的那一枚是同一个节点，
 * 键盘选完一档焦点不会掉回页面开头。
 *
 * 样式在 `./glow-preset-grid.css`。 */
import type { CSSProperties } from 'react';

import { chooseGlowPreset, glowChips, type GlowSettings } from '@peach/appearance';

import type { SettingsStore } from '../../settings-store';
import './glow-preset-grid.css';

export function GlowPresetGrid({ store, label, hidden }: {
  store: SettingsStore<GlowSettings>; label: string; hidden?: boolean;
}) {
  const glow = store.value.homeGlow;
  return (
    <div data-glow-grid="" role="group" aria-label={label} hidden={hidden}>
      {glowChips(glow).map((chip) => (
        <button type="button" key={chip.key} data-glow-chip="" data-glow-preset={chip.key}
          aria-pressed={chip.key === glow.preset} title={chip.label} aria-label={chip.label}
          onClick={() => chooseGlowPreset(chip.key, store)}>
          <span data-glow-ball="" aria-hidden="true" data-glow-native={chip.native ? '' : undefined}
            style={chip.native ? undefined : { '--glow-chip': chip.fill } as CSSProperties} />
        </button>
      ))}
    </div>
  );
}
