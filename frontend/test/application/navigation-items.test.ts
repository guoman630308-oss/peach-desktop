import { describe, expect, it } from 'vitest';
import { getManageMenuSections, NAV_CATALOG, SIDEBAR_ITEMS } from '../../src/application/navigation-items';
import { DEFAULT_SIDEBAR_ORDER, normalizeSidebarOrder } from '../../src/sidebar';

describe('应用导航词表', () => {
  it('关注的下一项是沉浸模式，可选入口不挤进默认可见顺序', () => {
    const keys = SIDEBAR_ITEMS.map(([key]) => key);
    expect(keys.slice(keys.indexOf('follow'), keys.indexOf('follow') + 2)).toEqual(['follow', 'immerse']);
    expect(normalizeSidebarOrder([])).toEqual(DEFAULT_SIDEBAR_ORDER);
    expect(DEFAULT_SIDEBAR_ORDER).not.toContain('immerse');
    expect(NAV_CATALOG.map(([key]) => key)).toContain('immerse');
  });

  it('每个导航身份只有一项，关注更新与关注管理有各自可见名称', () => {
    const keys = NAV_CATALOG.map(([key]) => key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(NAV_CATALOG.find(([key]) => key === 'follow')?.[1]).toBe('关注');
    expect(NAV_CATALOG.find(([key]) => key === 'follow-manage')?.[1]).toBe('关注管理');
  });

  it('配置入口只在本机可改时出现在管理菜单，其余入口顺序不动', () => {
    const enabled = getManageMenuSections(true), locked = getManageMenuSections(false);
    expect(enabled.some(([key]) => key === 'configuration')).toBe(true);
    expect(locked.some(([key]) => key === 'configuration')).toBe(false);
    expect(enabled.filter(([key]) => key !== 'configuration')).toEqual(locked);
  });
});
