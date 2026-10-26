import { describe, expect, it } from 'vitest';
import { folderHref } from '../src/react/catalog-folders/catalog-folders';
import { homePath, initialCatalogFilters, resetCatalogFilters } from '../src/react/catalog-grid/catalog-state';

describe('文件夹浏览地址', () => {
  it('目录范围保留首页筛选并可返回全部作品', () => {
    const address = { pathname: '/watch-later', search: '?tag=剧情&sort=name&loc=local' };
    const selected = folderHref(address, { browse: 'folders', folder: 'directory-id' });
    const parsed = new URL(selected, 'https://example.test');
    expect(parsed.searchParams.get('tag')).toBe('剧情');
    expect(parsed.searchParams.get('sort')).toBe('name');
    expect(parsed.pathname).toBe('/watch-later');
    const exited = folderHref(parsed, { browse: null, folder: null });
    expect(new URL(exited, parsed).searchParams.has('folder')).toBe(false);
  });
  it('深链恢复目录和子目录范围，首页重置清除目录', () => {
    const state = initialCatalogFilters({ pathname: '/', search: '?folder=directory-id&folder_recursive=0' }, { defaultSort: 'random' }, () => 'seed');
    expect(state.browse).toBe('folders');
    expect(state.folder_recursive).toBe('0');
    expect(homePath(state)).toContain('folder=directory-id');
    expect(resetCatalogFilters(state, { defaultSort: 'random' }, () => 'next').folder).toBe('');
  });
});
