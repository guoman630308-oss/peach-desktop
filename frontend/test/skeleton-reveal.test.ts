/* 骨架的延迟显形：低于门槛的请求不该闪一下占位。
 *
 * 这里守的是「同一批占位都要拿到标记」。`fitSkeleton` 会被反复调用——网格补行、
 * 容器改宽都会再进来一次，第二次里总有已经武装过的那几枚。 */
import { describe, expect, it, vi } from 'vitest';

import { fitSkeleton } from '../src/ui-kit';

const placeholder = (root: HTMLElement): HTMLElement => {
  const node = document.createElement('div');
  node.dataset.skeleton = '';
  root.append(node);
  return node;
};

describe('骨架延迟显形', () => {
  it('补进来的占位与先到的一样拿到等待标记', () => {
    const root = document.createElement('div');
    document.body.append(root);
    const first = placeholder(root);
    fitSkeleton(root);
    const second = placeholder(root);
    fitSkeleton(root);
    for (const node of [first, second]) {
      expect(node.dataset.skeletonReveal).toBe('pending');
      expect(node.classList.contains('skeleton-awaiting')).toBe(true);
    }
    root.remove();
  });

  it('等待超过门槛后整批一起显形', () => {
    vi.useFakeTimers();
    try {
      const root = document.createElement('div');
      document.body.append(root);
      const first = placeholder(root);
      fitSkeleton(root);
      const second = placeholder(root);
      fitSkeleton(root);
      vi.advanceTimersByTime(200);
      for (const node of [first, second]) {
        expect(node.dataset.skeletonReveal).toBe('shown');
        expect(node.classList.contains('skeleton-awaiting')).toBe(false);
      }
      root.remove();
    } finally {
      vi.useRealTimers();
    }
  });
});
