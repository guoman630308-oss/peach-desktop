import { describe, expect, it, vi } from 'vitest';
import { searchRecommendations } from '../../src/react/search/recommendations';

describe('搜索图片推荐', () => {
  it('不同作品来源保留各自详情地址，关注头像和署名取实际发布者', async () => {
    const source = { id: 8, author_key: 'name:作者', label: '作者', ref: '作者', provider: 'pixiv',
      entity_id: 20, entity_name: '作者', official_avatar_url: '/avatar-fixture' };
    const request = vi.fn(async (url: string) => {
      if (url.startsWith('/api/items')) return { items: [{ id: 11, name: '本地作品' }] };
      if (url.startsWith('/api/jav/')) return { items: [{ id: 12, asset_id: 12, code: 'ABW-101',
        performer_refs: [{ id: 15, name: '女优', has_image: true }] }] };
      if (url.startsWith('/api/feeds/')) return { items: [{ id: 12, code: 'ABW-102', cover_url: '/release-cover' }] };
      if (url.startsWith('/api/follow/authors')) return { items: [{ k: '作者', key: source.author_key, entity_id: 20 }] };
      return { sources: [source], groups: [{
        primary: { id: 12, source_id: 8, title: '关注作品', thumb_url: '/post-cover', media_kind: 'video', status: 'saved' },
        variants: [{ id: 13, source_id: 8, title: '图片作品', status: 'seen', media_kind: 'image',
          media_items: [{ index: 2, media_kind: 'image', thumb_url: '/photo-cover' }] }], duplicates: [],
      }] };
    });
    const groups = await searchRecommendations(request);
    const entry = (kind: string) => groups.find(group => group.kind === kind)!.items[0]!;
    expect(entry('asset').id).toBe(11);
    expect(entry('jav').href).toBe('/assets/12');
    expect(entry('release').href).toBe('/works/ABW-102');
    expect(entry('release').card?.sourceWork).toBe(true);
    expect(entry('post').href).toBe('/posts/12');
    expect(entry('post').who).toBe('作者');
    expect(entry('post').card?.thumb_url).toBe('/post-cover');
    expect(entry('photo').href).toBe('/posts/13');
    expect(entry('photo').mediaIndex).toBe(2);
    expect(entry('photo').followMedia).toBe('images');
    expect(entry('photo').card?.thumb_url).toBe('/photo-cover');
    expect(entry('author').href).toBe('/creators/20');
    expect(entry('author').avatarHtml).toContain('/avatar-fixture');
    expect(entry('performer').entity_id).toBe(15);
    expect(request.mock.calls.every(([url]) => !url.includes('scope=external'))).toBe(true);
    expect(request.mock.calls.filter(([url]) => url.startsWith('/api/follow?')).every(([url]) => !url.includes('status='))).toBe(true);
  });

  it('一类读取失败不会丢失其他推荐，无实体的作者进入自己的关注筛选', async () => {
    const groups = await searchRecommendations(async url => {
      if (url.startsWith('/api/jav/')) throw new Error('离线');
      if (url.startsWith('/api/follow/authors')) return { items: [{ k: '作者', key: 'name:作者' }] };
      return { items: [{ id: 11, name: '本地作品' }] };
    });
    expect(groups.find(group => group.kind === 'asset')!.items).toHaveLength(1);
    expect(groups.find(group => group.kind === 'jav')!.items).toEqual([]);
    expect(groups.find(group => group.kind === 'author')!.items[0]!.href).toBe('/follow?author=name%3A%E4%BD%9C%E8%80%85');
    expect(groups.find(group => group.kind === 'release')!.items).toEqual([]);
    expect(groups.find(group => group.kind === 'performer')!.action).toBe('订阅新作');
  });
});
