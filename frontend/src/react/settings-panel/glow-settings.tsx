/* 侧栏光晕的详细设置，照 feralui.dev/gradients 的工作台面板来：参数行是「84px 标签 + 自绘拉条 +
 * 右侧等宽读数」，颜色行点开在自己下方弹一张色板。
 * 取证见 docs/reference-snapshots/feralui-studio-boardui-accent-measured.md。
 *
 * 预设色块在这一屏和侧栏底部那张配色卡上是同一组色块、同一份写入（`GlowPresetGrid`），两处读的都是
 * 壳那一份 store，点哪一边另一边当场对齐。当前档名、预设色块和五条参数同属「配色」这一组。
 *
 * 「玻璃原色」那一档没有三枚光晕：那一层整个算作 0，面上漂的是每块玻璃自带的两团反光。强度、
 * 颗粒、柔化、大小和三枚颜色于是全都管不着任何东西，这一档把它们收起来，换一句说明颜色由明暗
 * 主题给。漂移速度不收——自带那两团也在漂。「恢复默认」同样不收，任何一档下都要能一步回到出厂那一套。 */
import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react';

import { closeAnchoredMenu, wireAnchoredMenu } from '@peach/legacy/ui';
import {
  GLOW_SPOT_LABELS, GLOW_SWATCH_FAMILIES, GLOW_SWATCHES, HOME_GLOW_SPOTS, glowColor, glowPresetName, isNativeGlass,
  normalizeHomeGlow, type HomeGlow,
} from '@peach/appearance';

import { GlowPresetGrid } from '../components/glow-preset-grid';
import type { SettingsPanelHost } from './settings-panel-api';
import { Icon } from './icon';
import { DialRow } from './legacy-controls';

type Field = 'strength' | 'noise' | 'speed' | 'soften' | 'size';
type Spot = 'spot1' | 'spot2' | 'spot3';

const FIELDS: readonly [Field, string, number][] = [
  ['strength', '强度', 100], ['noise', '颗粒', 60], ['speed', '漂移速度', 300], ['soften', '柔化', 100], ['size', '大小', 100],
];


function StopRow({ host, spot, index, glow }: { host: SettingsPanelHost; spot: Spot; index: number; glow: HomeGlow }) {
  const label = GLOW_SPOT_LABELS[index];
  const row = useRef<HTMLDivElement | null>(null);
  const toggle = useRef<HTMLButtonElement | null>(null);
  const pop = useRef<HTMLDivElement | null>(null);
  const [family, setFamily] = useState('all');
  /* 弹层左右贴齐这一行：宽度不跟着行走的话，一张比行窄的色板看不出是从哪一行开出来的。
     这条要排在 wireAnchoredMenu 之前，它按当前宽度算左缘。 */
  useLayoutEffect(() => {
    const [mount, button, menu] = [row.current, toggle.current, pop.current];
    if (!mount || !button || !menu) return;
    button.addEventListener('click', () => { menu.style.width = `${mount.getBoundingClientRect().width}px` });
    wireAnchoredMenu(mount, button, menu);
  }, []);
  const color = glow[spot].color;
  const pick = (hex: string) => {
    const current = host.store.value.homeGlow;
    current[spot].color = glowColor(hex, current[spot].color);
    current.preset = 'custom';
    host.store.save();
    host.changed('glow');
    closeAnchoredMenu();
    toggle.current?.focus();
  };
  return (
    <div data-glow-stop={spot} ref={row}>
      <button type="button" data-glow-stop-toggle="" aria-haspopup="dialog" aria-expanded="false" ref={toggle}>
        <span aria-hidden="true" data-glow-stop-dot="" style={{ '--glow-swatch': color } as CSSProperties} />
        <span data-glow-stop-text=""><b>{label}</b><small className="mono" data-glow-stop-hex="">{color}</small></span>
        <Icon name="chevron-down" />
      </button>
      <div className="popmenu" data-glow-stop-pop="" popover="manual" hidden ref={pop}>
        <div data-glow-pills="" role="group" aria-label="色系">
          {GLOW_SWATCH_FAMILIES.map(([key, name]) => (
            <button type="button" key={key} data-glow-pill={key} aria-pressed={key === family}
              onClick={() => setFamily(key)}>{name}</button>
          ))}
        </div>
        <div data-glow-palette="" role="radiogroup" aria-label={`${label}颜色`}>
          {GLOW_SWATCHES.map(([swatchFamily, name, hex]) => (
            <button type="button" key={hex} role="radio" aria-checked={hex === color} data-glow-swatch={hex}
              data-glow-family={swatchFamily} style={{ '--glow-swatch': hex } as CSSProperties}
              title={name} aria-label={name} hidden={family !== 'all' && swatchFamily !== family}
              onClick={() => pick(hex)} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function GlowSettings({ host }: { host: SettingsPanelHost }) {
  const glow = host.store.value.homeGlow;
  const native = isNativeGlass(glow.preset);
  return (
    <section data-glow-setting="" id="homeGlowControls" aria-label="侧栏光晕参数" hidden={!glow.on}>
      <section data-glow-group=""><h4>配色</h4>
        <p data-glow-current="">当前配色<b data-glow-preset-name="">{glowPresetName(glow.preset)}</b></p>
        <GlowPresetGrid store={host.store} label="光晕配色" />
        <p data-glow-native-note="" hidden={!native}>这一档用每块玻璃自带的反光，颜色跟着明暗主题走。</p>
        <div data-glow-fields="">
          {/* 拖动中只改参数、只排一帧重画；落盘留给松手那一下。其余玻璃面跟着走的只有速度，
              而它那两枚变量只能写在根上，所以拖动期间只有侧栏那一层在变，松手才铺到整页。 */}
          {FIELDS.map(([field, label, max]) => (
            <DialRow key={field} field={field} label={label} max={max} value={glow[field]} hidden={native && field !== 'speed'}
              onStep={(value) => { host.store.value.homeGlow[field] = value; host.changed('glowFrame') }}
              onSettle={() => { host.store.save(); if (field === 'speed') host.changed('glow') }} />
          ))}
        </div>
      </section>
      <section data-glow-group="" data-glow-colours="" hidden={native}><h4>颜色</h4>
        <div data-glow-stops="">
          {HOME_GLOW_SPOTS.map((spot, index) => <StopRow key={spot} host={host} spot={spot as Spot} index={index} glow={glow} />)}
        </div>
      </section>
      <div data-glow-actions="">
        <button type="button" className="geist-button" data-glow-reset=""
          onClick={() => { host.store.value.homeGlow = normalizeHomeGlow(null); host.store.save(); host.changed('glow') }}>恢复默认</button>
      </div>
    </section>
  );
}
