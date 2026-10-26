import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchFollowRelated, followRelatedGroups } from '../../src/react/follow-detail/follow-related';
import type { FollowDetailData, FollowDetailItem } from '../../src/react/follow-detail/follow-detail';
import type { FollowGroup, FollowItem, FollowPage } from '../../src/react/follow-feed/follow-feed';

afterEach(() => { vi.unstubAllGlobals() });
const item = (id: number, patch: Partial<FollowDetailItem> = {}): FollowDetailItem => ({
  id, title: String(id), status: 'new', provider: 'kemono', source_id: 1, media_kind: 'video', tags: [], ...patch,
});
const group = (shown: FollowItem, duplicates: FollowItem[] = []): FollowGroup =>
  ({ primary: shown, variants: [], duplicates, has_wip: false });
const sources = [{ id: 1, provider: 'kemono', author_key: 'name:kou' }, { id: 2, provider: 'kemono', author_key: 'name:mira' }];
const current: FollowDetailData = { item: item(1, { tags: ['solo'] }), group: group(item(1), [item(2)]), sources, aliases: [] };
const page = (groups: FollowGroup[], extra: Partial<FollowPage> = {}): FollowPage => ({
  groups, sources, counts: {}, facets: {}, offset: 0, has_more: false, ...extra,
});

describe('关注接着看', () => {
  it('排除整个当前合集、重复组、已看、忽略和不同媒体', () => {
    const rows = [group(item(2)), group(item(3)), group(item(3)), group(item(4, { status: 'seen' })),
      group(item(5, { status: 'ignored' })), group(item(6, { media_kind: 'image' }))];
    expect(followRelatedGroups([page(rows)], current, 'videos', 20).map(row => row.primary.id)).toEqual([3]);
    expect(followRelatedGroups([page(rows)], current, 'images', 20).map(row => row.primary.id)).toEqual([6]);
  });
  it('同创作者优先，再按共同标签排序，遵守数量', () => {
    const rows = [group(item(3, { source_id: 2, tags: ['solo'] })), group(item(4)), group(item(5, { tags: ['solo'] }))];
    expect(followRelatedGroups([page(rows)], current, 'videos', 2).map(row => row.primary.id)).toEqual([5, 4]);
  });
  it('混合媒体按当前视图选择卡面，已看的卡面不进入推荐', () => {
    const mixed = group(item(7, { media_kind: 'image' }), [item(8, { published_at: '2026-10-10', status: 'seen' })]);
    expect(followRelatedGroups([page([mixed])], current, 'videos', 20)).toEqual([]);
    expect(followRelatedGroups([page([mixed])], current, 'images', 20).map(row => row.primary.id)).toEqual([7]);
  });
  it('同创作者已有足够结果只发一趟读取，带原始取消信号', async () => {
    const signal = new AbortController().signal;
    const read = vi.fn(async () => ({ ok: true, json: async () => page([group(item(3))]) }));
    vi.stubGlobal('fetch', read);
    const result = await fetchFollowRelated(current, 'videos', 1, signal);
    expect(result.pages).toHaveLength(1);
    expect(read).toHaveBeenCalledTimes(1);
    const [url, init] = read.mock.calls[0] as unknown as [string, RequestInit];
    expect(new URL(url, 'https://example.invalid').searchParams.get('author')).toBe('name:kou');
    expect(init.signal).toBe(signal);
  });
  it('翻一页同作者仍不足时补全库，读取有上限', async () => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url);
      return { ok: true, json: async () => page([group(item(1))], { has_more: true }) };
    }));
    await fetchFollowRelated(current, 'videos', 20, new AbortController().signal);
    expect(urls.map(url => new URL(url, 'https://example.invalid').searchParams.get('author'))).toEqual(['name:kou', 'name:kou', null]);
    expect(new URL(urls[1]!, 'https://example.invalid').searchParams.get('offset')).toBe('100');
  });
});
