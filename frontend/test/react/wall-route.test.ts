import { describe, expect, it } from 'vitest';
import { unfilteredWallAddress, wallAddress } from '../../src/react/video-wall/wall-route';
import { registerEntityLink } from '../../src/core/entity-links';

describe('播放墙独立入口', () => {
  it('清除作品条件，保留浏览语境、排序和返回地址', () => {
    const filters = new URLSearchParams({ q:'Alice', performer:'Alice', studio:'Studio', tag:'风景', tag_match:'all',
      state:'fresh', orient:'vertical', dur_min:'600', region:'jp', jav:'1', loc:'local,115', sort:'new', returnTo:'/performers/71?tag=风景' });
    const target = new URL(unfilteredWallAddress(`/wall?${filters}`), location.origin);
    expect(Object.fromEntries(target.searchParams)).toEqual({ jav:'1', loc:'local,115', sort:'new', returnTo:'/performers/71?tag=风景' });
    expect(unfilteredWallAddress('/wall?q=测试')).toBe('/wall');
  });
  it('全局入口保留返回地址，非作品页使用馆藏范围', () => {
    for (const path of ['/stats?tab=creators','/trash?q=清理','/junk-files','/follow?q=动态','/performers?q=姓名']) {
      const target = new URL(wallAddress(path, { state:'trash',q:'清理',jav:'1',loc:'local' }), location.origin);
      expect(target.searchParams.get('state')).toBeNull();
      expect(target.searchParams.get('q')).toBeNull();
      expect(target.searchParams.get('tab')).toBeNull();
      expect(target.searchParams.get('jav')).toBe('1');
      expect(target.searchParams.get('loc')).toBe('local');
      const source = new URL(path, location.origin);
      expect(target.searchParams.get('returnTo')).toBe(source.pathname + source.search);
    }
  });
  it('继承搜索和筛选，并保留返回地址', () => {
    const target = new URL(wallAddress('/?q=测试&tag=风景&loc=local'), location.origin);
    expect(target.pathname).toBe('/wall');
    expect(target.searchParams.get('q')).toBe('测试');
    expect(target.searchParams.get('tag')).toBe('风景');
    expect(target.searchParams.get('returnTo')).toBe('/?q=%E6%B5%8B%E8%AF%95&tag=%E9%A3%8E%E6%99%AF&loc=local');
  });
  it('实体资料页转换为同一实体的作品范围', () => {
    for (const [section, kind] of [['performers','performer'],['studios','studio'],['creators','creator'],['series','series']]) {
      const target = new URL(wallAddress(`/${section}/Alice?jav=1`), location.origin);
      expect(target.searchParams.get(kind!)).toBe('Alice');
      expect(target.searchParams.get('jav')).toBe('1');
    }
  });
  it('保留状态路径和当前作品筛选', () => {
    const target = new URL(wallAddress('/unseen', { loc:'local', tag:'风景', jav:'1' }), location.origin);
    expect(target.searchParams.get('state')).toBe('fresh');
    expect(target.searchParams.get('tag')).toBe('风景');
    expect(target.searchParams.get('jav')).toBe('1');
  });
  it('数字实体地址转换为规范名字，显式上下文保持优先', () => {
    registerEntityLink('performer', 'Alice', 71);
    const target = new URL(wallAddress('/performers/71?jav=1', { tag: '风景' }), location.origin);
    expect(target.searchParams.get('performer')).toBe('Alice');
    expect(target.searchParams.get('returnTo')).toBe('/performers/71?jav=1');
    expect(new URL(wallAddress('/performers/71', { performer: '当前规范名' }), location.origin)
      .searchParams.get('performer')).toBe('当前规范名');
  });
  it('标签资料进入播放墙时沿用标签交集', () => {
    registerEntityLink('tag', '巨乳', 72);
    const target = new URL(wallAddress('/tags/72?tag=口交'), location.origin);
    expect(target.searchParams.get('tag')).toBe('巨乳,口交');
    expect(target.searchParams.get('tag_match')).toBe('all');
  });
});
