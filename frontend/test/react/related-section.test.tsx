import { describe, expect, it, vi } from 'vitest';
import { RelatedSection } from '../../src/react/components/related-section';
import { click, mountRoot } from './render';

describe('接着看状态', () => {
  it('等待读取期间保留骨架，完成后显示空状态', async () => {
    const wireDrag = vi.fn();
    const { host, rerender } = await mountRoot(<RelatedSection wireDrag={wireDrag} busy empty><span data-skeleton="" /></RelatedSection>);
    expect(host.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(host.querySelector('[data-skeleton]')).not.toBeNull();
    expect(host.querySelector('[data-empty-state]')).toBeNull();
    await rerender(<RelatedSection wireDrag={wireDrag} empty />);
    expect(host.querySelector('[data-empty-state] h3')?.textContent).toBe('暂无接着看的内容');
    expect(host.querySelector('[data-related-row]')).toBeNull();
  });

  it('失败显示原因和重试按钮，恢复后显示推荐', async () => {
    const retry = vi.fn();
    const wireDrag = vi.fn();
    const { host, rerender } = await mountRoot(<RelatedSection wireDrag={wireDrag} empty error="读取接着看失败" onRetry={retry} />);
    expect(host.textContent).toContain('读取接着看失败');
    expect(host.querySelector('[data-empty-state]')).toBeNull();
    await click(host.querySelector('[data-note-action]'));
    expect(retry).toHaveBeenCalledOnce();
    await rerender(<RelatedSection wireDrag={wireDrag}><span>下一部作品</span></RelatedSection>);
    expect(host.querySelector('[data-related-row]')?.textContent).toBe('下一部作品');
    expect(host.querySelector('[data-empty-state],[data-media-error]')).toBeNull();
  });
});
