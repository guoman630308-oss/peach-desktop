/* 光晕那一层的写入时机（`src/appearance/glow.ts`）。点选写什么由 `test/react/glow-picker.test.tsx` 走。 */
import { beforeEach, expect, it, vi } from 'vitest';

import { applyHomeGlow, paintHomeGlowNow, type GlowSettings } from '../../src/appearance/glow';
import { normalizeHomeGlow } from '../../src/appearance/home-glow';
import { SETTINGS_KEY } from '../../src/appearance/settings';
import { createSettingsStore } from '../../src/settings-store';

const store = () => createSettingsStore<GlowSettings>(SETTINGS_KEY, { homeGlow: { ...normalizeHomeGlow(null), strength: 100 }, accent: 'blue' });
const strength = () => document.querySelector<HTMLElement>('.glowlayer')!.style.getPropertyValue('--glow-strength');

beforeEach(() => { document.body.innerHTML = '<div class="glowlayer"></div>' });

it('启动那一次当场写进侧栏那一层，不等下一帧', () => {
  paintHomeGlowNow(store());
  expect(strength()).toBe('1');
});

it('拖动时一帧只写一次，写的是那一帧时的值', () => {
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal('requestAnimationFrame', (run: FrameRequestCallback) => frames.push(run));
  const current = store();
  for (const value of [80, 60, 40]) { current.value.homeGlow.strength = value; applyHomeGlow(current) }
  expect([frames.length, strength()]).toEqual([1, '']);
  frames.shift()!(0);
  expect(strength()).toBe('0.4');
  current.value.homeGlow.strength = 20; applyHomeGlow(current);
  expect(frames.length).toBe(1);
  vi.unstubAllGlobals();
});
