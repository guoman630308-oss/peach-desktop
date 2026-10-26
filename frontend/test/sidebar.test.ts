import { describe, expect, it } from 'vitest';
import { DEFAULT_SIDEBAR_ORDER, normalizeSidebarOrder, sidebarTagCounts, sidebarHasCatalogContent } from '../src/sidebar';

describe('侧栏顺序', () => {
  it('认旧键、去重、丢掉这一版没有的入口', () => {
    expect(normalizeSidebarOrder(['', 'ads', 'dupes', 'tags', 'tags', 'nope', 'trash'])).toEqual(['', 'data-cleanup', 'tags', 'trash']);
  });
  it('读不出顺序或一项不剩时回到默认，且不与默认那份共用数组', () => {
    for (const value of [null, 'tags', [], ['nope'], [1, 2]]) expect(normalizeSidebarOrder(value)).toEqual(DEFAULT_SIDEBAR_ORDER);
    expect(normalizeSidebarOrder(null)).not.toBe(DEFAULT_SIDEBAR_ORDER);
  });
});

describe('侧栏内容范围', () => {
  it('视频集合提供馆藏筛选，管理与索引页只提供导航', () => {
    for (const path of ['/', '/unseen', '/trash', '/assets/1', '/creators/Demo', '/assets/2?parts=1'])
      expect(sidebarHasCatalogContent(path)).toBe(true);
    for (const path of ['/subscriptions', '/stats', '/follow', '/tags', '/data-cleanup', '/creators'])
      expect(sidebarHasCatalogContent(path)).toBe(false);
  });

  it('只计当前内容的标签，空内容没有标签', () => {
    expect(sidebarTagCounts([{tags: ['a', 'a', 'b']}, {tags: ['b']}, {}]))
      .toEqual([['b', 2], ['a', 1]]);
    expect(sidebarTagCounts([])).toEqual([]);
  });
});
