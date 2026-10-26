import { afterEach, expect, it, vi } from 'vitest';
import { initMiddleTruncate } from '../src/ui-kit/middle-truncate';

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('截断观察者与复制监听可销毁，重挂同一元素重新观察', () => {
  vi.useFakeTimers();
  const observers: { observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }[] = [];
  vi.stubGlobal('ResizeObserver', class {
    observe = vi.fn(); disconnect = vi.fn(); unobserve = vi.fn();
    constructor() { observers.push(this); }
  });
  const root = document.createElement('div'); root.innerHTML = '<span data-middle-truncate>完整文件名</span>';
  document.body.append(root);
  const remove = vi.spyOn(root, 'removeEventListener');
  const first = initMiddleTruncate(root); first(); first();
  expect(observers[0]?.disconnect).toHaveBeenCalledTimes(1);
  expect(remove.mock.calls.filter(([name]) => name === 'copy')).toHaveLength(1);
  const second = initMiddleTruncate(root);
  expect(observers[1]?.observe).toHaveBeenCalledWith(root.firstElementChild);
  second(); expect(observers[1]?.disconnect).toHaveBeenCalledTimes(1);
  root.remove();
});
