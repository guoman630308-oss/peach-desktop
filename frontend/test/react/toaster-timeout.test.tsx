import { beforeEach, expect, it, vi } from 'vitest';

const sonner = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('sonner', () => ({ toast: sonner, Toaster: () => null }));

import { showToast } from '../../src/react/toaster';

beforeEach(() => { sonner.success.mockClear(); sonner.error.mockClear() });

const durationOf = (call: unknown[] | undefined) => (call?.[1] as { duration?: number } | undefined)?.duration;

it('时长给 0 或负数时按默认 6 秒消失，显式的 Infinity 才常驻', () => {
  showToast('zero', { html: '已保存', alert: false, timeout: 0, action: null });
  showToast('negative', { html: '已保存', alert: false, timeout: -1, action: null });
  showToast('pinned', { html: '已保存', alert: false, timeout: Infinity, action: null });
  showToast('short', { html: '已保存', alert: true, timeout: 4000, action: null });
  expect(sonner.success.mock.calls.map(durationOf)).toEqual([6000, 6000, Infinity]);
  expect(durationOf(sonner.error.mock.calls[0])).toBe(4000);
});

it('没有字的回执不弹空白条', () => {
  showToast('empty', { html: '', alert: false, timeout: 6000, action: null });
  showToast('blank', { html: '  \n ', alert: true, timeout: 6000, action: null });
  expect(sonner.success).not.toHaveBeenCalled();
  expect(sonner.error).not.toHaveBeenCalled();
});
