import { expect, it } from 'vitest';
import { canonicalPath } from '../../src/history/canonical-path';
import { overlayTarget } from '../../src/history/overlay';
import { entityLinkId, entityLinkName, registerResponseEntities } from '../../src/core/entity-links';

it('三类对象与队列兼容地址保留筛选和播放位置', () => {
  expect(canonicalPath('/item/12?t=93')).toBe('/assets/12?t=93');
  expect(canonicalPath('/follow/item/7?media=images')).toBe('/posts/7?media=images');
  expect(canonicalPath('/jav/movie/abp-968?q=x')).toBe('/works/ABP-968?q=x');
  for (const [source, key] of [['mix', 'mix'], ['parts', 'parts'], ['editions', 'editions'], ['playlists', 'playlist']]) {
    expect(canonicalPath(`/${source}/21/22?t=42`)).toBe(`/assets/22?t=42&${key}=21`);
    expect(overlayTarget('/assets/22', `?${key}=21&t=42`)).toEqual({ kind: 'queue', queue: key, key: 21, item: 22 });
  }
  expect(canonicalPath('/api/item?id=12')).toBe('/api/item?id=12');
  for (const [source, target] of [['activity', 'tasks'], ['scraping', 'sources'],
    ['follow-manage', 'subscriptions'], ['configuration', 'settings/library'], ['diagnostics', 'settings/diagnostics']]) {
    expect(canonicalPath(`/${source}?tab=x`)).toBe(`/${target}?tab=x`);
  }
});

it('规范名优先于同名别名，歧义别名交给服务器解析', () => {
  registerResponseEntities('/api/entity/shapes', { entities: [
    { kind: 'performer', id: 801, names: ['Canonical Alice', 'Shared Alias'] },
    { kind: 'performer', id: 802, names: ['Canonical Bob', 'Canonical Alice', 'Shared Alias'] },
  ] });
  expect(entityLinkId('performer', 'Canonical Alice')).toBe(801);
  expect(entityLinkId('performer', 'Shared Alias')).toBeUndefined();
  expect(entityLinkName('performer', 802)).toBe('Canonical Bob');
});

it('实体别名与改名沿用同一个 ID，非实体响应不参与地址登记', () => {
  registerResponseEntities('/api/entity?kind=studio&id=91', { id: 91, kind: 'studio', canonical_name: '新厂牌', aliases: ['旧厂牌'] });
  expect(entityLinkId('studio', '旧厂牌')).toBe(91);
  expect(entityLinkName('studio', 91)).toBe('新厂牌');
  expect(() => registerResponseEntities('/api/links', { entities: 210, total: 719 })).not.toThrow();
  expect(entityLinkId('studio', '不存在')).toBeUndefined();
});

it('无效与冲突队列参数使用普通资产详情', () => {
  for (const query of ['?parts=bad', '?parts=0', '?parts=2&playlist=3']) {
    expect(overlayTarget('/assets/22', query)).toEqual({ kind: 'item', id: 22 });
  }
  expect(overlayTarget('/assets/0')).toBeNull();
});

it('头像条、筛选与搜索补全沿用已取得的实体 ID', () => {
  registerResponseEntities('/api/tops', { performers: [{ id: 991, k: '头像条人物' }] });
  registerResponseEntities('/api/facets', { studios: [{ entity_id: 992, k: '筛选厂牌' }] });
  registerResponseEntities('/api/suggest?q=test', { groups: [
    { kind: 'creator', items: [{ entity_id: 993, value: '补全作者' }] },
    { kind: 'asset', items: [{ id: 1, value: '示例视频' }] },
  ] });
  expect(entityLinkId('performer', '头像条人物')).toBe(991);
  expect(entityLinkId('studio', '筛选厂牌')).toBe(992);
  expect(entityLinkId('creator', '补全作者')).toBe(993);
  expect(entityLinkId('asset', '示例视频')).toBeUndefined();
});
