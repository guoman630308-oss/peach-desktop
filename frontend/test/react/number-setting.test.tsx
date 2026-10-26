/* 设置面板里带单位、可选开关与字段错误的数值控件。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { boundedPreference } from '../../src/number-setting';
import { dayReading, NumberSetting, type NumberSettingId } from '../../src/react/settings-panel/number-setting';
import './render';

const unmounts: (() => void)[] = [];
beforeEach(() => { localStorage.clear() });
afterEach(() => { for (const unmount of unmounts.splice(0)) act(unmount) });

/** 挂一枚控件，`rerender` 换一个外部值（数据库对账、请求回来）。 */
async function setup(id: NumberSettingId, value: number | null, label = '搜索记录') {
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  unmounts.push(() => root.unmount());
  const apply = vi.fn();
  const render = (next: number | null) => act(async () => {
    root.render(<NumberSetting id={id} label={label} value={next} onApply={apply} />);
  });
  await render(value);
  return {
    host, apply, rerender: render,
    input: () => host.querySelector<HTMLInputElement>('input[type=number]')!,
    toggle: () => host.querySelector<HTMLInputElement>('[role=switch]')!,
  };
}

/** 原生数字框的 `change`：失焦或回车才提交，打字途中不提交。 */
const commit = (input: HTMLInputElement, value: string) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
});
const flip = (toggle: HTMLInputElement) => act(async () => { toggle.click() });

it('越界的数不提交、标出错误，改对之后错误消失', async () => {
  const { host, apply, input } = await setup('searchHistoryLimitSetting', 10);
  await commit(input(), '51');
  expect(apply).not.toHaveBeenCalled();
  expect(input().getAttribute('aria-invalid')).toBe('true');
  const error = host.querySelector<HTMLElement>('[role=status]')!;
  expect(error.hidden).toBe(false);
  expect(error.textContent).toBe('请输入 1–50 的整数（条）');
  await commit(input(), '17');
  expect(apply).toHaveBeenLastCalledWith(17);
  expect(input().hasAttribute('aria-invalid')).toBe(false);
  expect(error.hidden).toBe(true);
});

it('关掉时收起数字框、提交 0；再打开填回关掉前那个数', async () => {
  const { host, apply, input, toggle } = await setup('searchHistoryLimitSetting', 10);
  await commit(input(), '23');
  await flip(toggle());
  expect(apply).toHaveBeenLastCalledWith(0);
  expect(host.querySelector<HTMLElement>('[data-number-fields]')!.hidden).toBe(true);
  await flip(toggle());
  expect(input().value).toBe('23');
  expect(apply).toHaveBeenLastCalledWith(23);
  expect(localStorage.getItem('peach.number.searchHistoryLimitSetting')).toBe('23');
});

it('外面换来的值关掉再打开也填得回来', async () => {
  const { apply, input, toggle, rerender } = await setup('searchHistoryLimitSetting', 10);
  await rerender(37);
  expect(input().value).toBe('37');
  await flip(toggle());
  await flip(toggle());
  expect(input().value).toBe('37');
  expect(apply).toHaveBeenLastCalledWith(37);
});

it('还没取到值时整块禁用，取回来之后按档位开合', async () => {
  const { input, toggle, rerender, host } = await setup('followScheduleSetting', null, '关注自动更新');
  expect(input().disabled).toBe(true);
  expect(toggle().disabled).toBe(true);
  await rerender(0);
  expect(toggle().disabled).toBe(false);
  expect(toggle().checked).toBe(false);
  expect(host.querySelector<HTMLElement>('[data-number-fields]')!.hidden).toBe(true);
  await rerender(180);
  expect(toggle().checked).toBe(true);
  expect(input().value).toBe('180');
});

it('必填的那几项没有开关', async () => {
  const { host, input } = await setup('batchSizeSetting', 60, '每批作品');
  expect(host.querySelector('[role=switch]')).toBeNull();
  expect(input().value).toBe('60');
  expect(host.querySelector('[data-number-control] span')!.textContent).toBe('个');
});

it('读回的偏好只认区间内的整数', () => {
  expect(boundedPreference(0, 0, 50, 10)).toBe(0);
  expect(boundedPreference(2.5, 1, 50, 10)).toBe(10);
  expect(boundedPreference(99, 1, 50, 10)).toBe(10);
});

it('以分钟计的间隔满一天时在单位旁换算成天，不满一天或不是分钟时不说', () => {
  expect(dayReading(10080, '分钟')).toBe('7 天');
  expect(dayReading(2160, '分钟')).toBe('约 1.5 天');
  expect(dayReading(1439, '分钟')).toBe('');
  expect(dayReading(10080, '秒')).toBe('');
});
