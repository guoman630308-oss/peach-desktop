/* 界面偏好那一份真相：壳原地改字段，落盘时通知订阅者；面板靠版本号知道要重画。 */
import { beforeEach, expect, it, vi } from 'vitest';

import { createSettingsStore } from '../src/settings-store';

beforeEach(() => localStorage.clear());

it('存的是壳手里那个对象本身，原地改完落盘就是改后的值', () => {
  const value = { theme: 'system', batchSize: 60 };
  const store = createSettingsStore('peach.settings.v1', value);
  value.theme = 'dark';
  store.save();
  expect(store.value).toBe(value);
  expect(JSON.parse(localStorage.getItem('peach.settings.v1')!)).toEqual({ theme: 'dark', batchSize: 60 });
});

it('每次落盘换一个版本号并通知订阅者；退订之后不再收到', () => {
  const store = createSettingsStore('k', { on: true });
  const listener = vi.fn();
  const stop = store.subscribe(listener);
  const before = store.version();
  store.save();
  expect(store.version()).toBe(before + 1);
  expect(listener).toHaveBeenCalledTimes(1);
  stop();
  store.save();
  expect(listener).toHaveBeenCalledTimes(1);
});

it('值在别的键上时只通知不落盘', () => {
  const store = createSettingsStore('k', { on: true });
  const listener = vi.fn();
  store.subscribe(listener);
  store.notify();
  expect(listener).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem('k')).toBeNull();
});
