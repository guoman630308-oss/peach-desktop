import { act } from 'react';
import { expect, it, vi } from 'vitest';
import { SourceImage } from '../../src/react/review/review-evidence';
import { click, mountRoot } from './render';

it('来源图片只在点击后请求，失败可以重试，换来源要重新确认', async () => {
  const { host, rerender } = await mountRoot(<SourceImage url="https://example.org/one.jpg" />);
  expect(host.querySelector('img')).toBeNull();
  await click(host.querySelector('button'));
  expect(host.querySelector('img')?.getAttribute('src')).toBe('https://example.org/one.jpg');
  expect(host.querySelector('[role=status]')?.textContent).toContain('正在加载图片');
  await act(async () => { host.querySelector('img')!.dispatchEvent(new Event('error')) });
  expect(host.querySelector('img')).toBeNull();
  expect(host.textContent).toContain('未取得来源图片');
  await click(host.querySelector('button'));
  await act(async () => { host.querySelector('img')!.dispatchEvent(new Event('load')) });
  expect(host.querySelector('[role=status]')).toBeNull();
  expect(host.querySelector('img')?.getAttribute('referrerpolicy')).toBe('no-referrer');
  await rerender(<SourceImage url="https://example.org/two.jpg" />);
  expect(host.querySelector('img')).toBeNull();
  await rerender(<SourceImage url="javascript:alert(1)" />);
  expect(host.querySelector('button')).toBeNull();
});

it('来源图片超时结束等待并提供重试', async () => {
  vi.useFakeTimers();
  const { host } = await mountRoot(<SourceImage url="https://example.org/slow.jpg" />);
  await click(host.querySelector('button'));
  await act(async () => { vi.advanceTimersByTime(30_000) });
  expect(host.querySelector('img')).toBeNull();
  expect(host.querySelector('[role=status]')).toBeNull();
  expect(host.textContent).toContain('重新加载图片');
});
