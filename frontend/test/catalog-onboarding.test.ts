import { describe, expect, it, vi } from 'vitest';
import { catalogEmptyHtml, catalogSuggestions } from '../src/catalog-onboarding';

describe('当前馆藏推荐', () => {
  it('空实例不显示示例词', async () => {
    expect(await catalogSuggestions({}, async () => ({ items: [], tags: [] }))).toEqual([]);
  });
  it('少量内容也能推荐，且每条在当前筛选内有搜索命中', async () => {
    const request = vi.fn(async (url: string) => {
      const parsed = new URL(url, 'http://localhost');
      expect(parsed.searchParams.get('jav')).toBe('1');
      if (parsed.pathname === '/api/facets') return { tags: [{ k: '本机标签' }, { k: '失效词' }] };
      if (!parsed.searchParams.has('q')) return { items: [{ name: '本机视频.mp4' }] };
      return { total: parsed.searchParams.get('q') === '失效词' ? 0 : 1 };
    });
    expect(await catalogSuggestions({ jav: '1', q: '不会带入' }, request)).toEqual(['本机标签', '本机视频.mp4']);
  });
});

describe('馆藏与资料空状态', () => {
  it('空首页提供添加内容与来源的真实入口', () => {
    const html = catalogEmptyHtml({ configurable: true });
    expect(html).toContain('data-geist-empty-state');
    expect(html).toContain('data-empty-settings');
    expect(html).toContain('href="/subscriptions?tab=add"');
    expect(catalogEmptyHtml()).not.toContain('data-empty-settings');
    expect(catalogEmptyHtml()).toContain('class="geist-button primary" href="/subscriptions?tab=add"');
    expect(catalogEmptyHtml({ configurable: true }).match(/class="geist-button primary"/g)).toHaveLength(1);
  });
  it('JAV 与筛选无结果可查看未筛选内容', () => {
    expect(catalogEmptyHtml({ jav: true })).toContain('尚未补充发行资料');
    expect(catalogEmptyHtml({ filtered: true })).toContain('href="/?loc=&thumb=0"');
  });
  it('标签和各类实体没有资料时显示说明', () => {
    for (const kind of ['tags', 'performers', 'creators', 'studios', 'agencies', 'series']) {
      expect(catalogEmptyHtml({ kind })).toContain('data-geist-empty-state');
    }
    expect(catalogEmptyHtml({ kind: 'tags', online: true })).toContain('关注来源');
    expect(catalogEmptyHtml({ kind: 'creators' })).toContain('还没有卖家');
  });
});
