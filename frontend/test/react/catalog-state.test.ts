import { describe, expect, it } from 'vitest';

import {
  defaultCatalogLoc, homeCatalogState, homePath, initialCatalogFilters, onlineDefaultLoc,
  resetCatalogFilters, resolveSort,
} from '../../src/react/catalog-grid/catalog-state';

const preferences = { defaultSort: 'new', defaultSortDirection: 'asc' };
const seed = () => 'fresh-seed';
const initial = (pathname = '/', search = '') => initialCatalogFilters({ pathname, search }, preferences, seed);

describe('目录地址与默认范围', () => {
  it('只在目录地址读取筛选，非目录地址仍可指定来源范围', () => {
    expect(initial('/follow', '?loc=115&tag=blender&owner=none&q=other&sort=rating&seed=foreign')).toMatchObject({
      loc: '115', tag: '', owner: '', q: '', sort: 'new', dir: 'asc', seed: 'fresh-seed',
    });
    expect(initial('/unseen', '?state=later&owner=none&tag=blender&tag_match=any')).toMatchObject({
      state: 'fresh', owner: 'none', tag: 'blender', tag_match: 'any',
    });
    expect(initial('/', '?owner=someone&tag_match=bad')).toMatchObject({ owner: '', tag_match: 'all' });
  });

  it('目录地址覆盖排序偏好，旧排序键与随机方向使用现有解析', () => {
    expect(resolveSort(null, null, preferences)).toEqual({ sort: 'new', dir: 'asc' });
    expect(resolveSort('short', null, preferences)).toEqual({ sort: 'dur', dir: 'asc' });
    expect(resolveSort('short', 'desc', preferences)).toEqual({ sort: 'dur', dir: 'desc' });
    expect(resolveSort('seed', 'asc', preferences)).toEqual({ sort: 'seed', dir: '' });
    expect(resolveSort('unknown', null, preferences, 'rating')).toEqual({ sort: 'rating', dir: 'desc' });
  });

  it('分享地址包含 owner=none 与非默认方向，省略种子和缩略图开关', () => {
    const filters = initial('/watch-later', '?loc=115&owner=none&sort=new&dir=asc&seed=old&thumb=1&tag_match=all');
    const path = homePath(filters);
    const address = new URL(path, 'https://peach.test');
    expect(address.pathname).toBe('/watch-later');
    expect(Object.fromEntries(address.searchParams)).toEqual({ loc: '115', owner: 'none', sort: 'new', dir: 'asc' });
    expect(initial(address.pathname, address.search)).toMatchObject({ ...filters, seed: 'fresh-seed', thumb: '0' });
  });

  it('没有独立路径的状态用查询参数，垃圾文件使用独立路径', () => {
    expect(homePath({ state: 'trash', sort: 'seed', dir: '', seed: 'private' })).toBe('/?sort=seed&state=trash');
    expect(homePath({ state: 'ads', tag_match: 'all' })).toBe('/junk-files');
    expect(homePath({ state: 'fresh', sort: 'new', dir: 'desc', tag_match: 'any' }))
      .toBe('/unseen?tag_match=any&sort=new');
  });

  it('默认范围剔除明确离线来源，全部离线时仍保留原范围', () => {
    expect(onlineDefaultLoc('local,115,pikpak', { local: true, '115': false })).toBe('local,pikpak');
    expect(onlineDefaultLoc('local,115', { local: false, '115': false })).toBe('local,115');
    expect(onlineDefaultLoc('', { local: true })).toBe('');
    expect(defaultCatalogLoc('local,115', '?loc=', { '115': false })).toBe('local,115');
    expect(defaultCatalogLoc('', '?loc=', { '115': false })).toBe('');
    expect(defaultCatalogLoc('local,115', '?q=test', { '115': false })).toBe('local');
  });

  it('重置保留来源并更新种子，首页筛选条与目录状态共用对象', () => {
    const previous = initial('/flagged', '?loc=pikpak&owner=none&creator=x&region=CN&jav=1&thumb=1');
    const state = resetCatalogFilters(previous, preferences, seed);
    expect(state).toEqual({
      loc: 'pikpak', creator: '', studio: '', owner: '', tag: '', tag_match: 'all', len: '',
      dur_min: '', dur_max: '', orient: '', region: '', state: '', sort: 'new', dir: 'asc',
      seed: 'fresh-seed', q: '', jav: '', thumb: '0',
      browse: '', folder: '', folder_recursive: '',
    });
    const patch = homeCatalogState(state);
    expect(patch.state).toBe(state);
    expect(patch.barsContext.filters).toBe(state);
    expect(patch.detailReturnBarsContext).toBeNull();
    state.q = 'same-object';
    expect(patch.barsContext.filters.q).toBe('same-object');
    expect(previous.owner).toBe('none');
  });
});
