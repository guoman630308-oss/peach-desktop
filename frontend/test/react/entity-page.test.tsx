/* 实体资料页：取数、页内状态与写操作。资料卡、浮层与正文各自怎么画由它们自己的用例管
 * （`entity-hero`／`entity-filter`／`entity-body.test.tsx`），这里看的是把它们拼成一页的那一层：
 * 首屏取哪几样、换筛选重取几次、哪些动作回壳写地址、写操作成功后哪几块跟着更新。
 *
 * 骨架换成整页时的几何、吸顶与窄屏横滚由 e2e（`entity-filter.test.ts`、`design-*.test.ts`）量。 */
import { act, useState } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as legacyUi from '@peach/legacy/ui';

import { fetchItems, itemsParams, prefetchEntityPage, type EntityPageProps } from '../../src/react/entity-page/entity-page';
import { EntityPage } from '../../src/react/entity-page/entity-page-view';
import { queryClient } from '../../src/react/query';
import { buttonNamed, click, mountRoot, settle } from './render';

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const NAME = '篠田ゆう';
const entity = (patch: Record<string, unknown> = {}) => ({
  id: 7001, kind: 'performer', canonical_name: NAME, aliases: [], user_aliases: [], asset_count: 3,
  tags: [{ k: '巨乳', n: 12 }, { k: '美乳', n: 4 }], related_performers: [], feed: { following: false },
  ...patch,
});
const items = (ids: number[], jav = false) => ({
  items: ids.map((id) => ({ id, name: `作品 ${id}`, has_thumb: true, is_jav: jav })), total: ids.length, has_more: false,
});
const photos = (count: number, patch: Record<string, unknown> = {}) => ({
  total: count, sample_total: 0, sets: [], has_more: false, seed: '',
  items: Array.from({ length: count }, (_, at) => ({ id: 500 + at, name: `${String(at + 1).padStart(3, '0')}.jpg` })),
  ...patch,
});
const followGroup = (id: number, kind: 'video' | 'image') => ({
  primary: { id, title: `更新 ${id}`, status: 'new', provider: 'rule34video', source_id: 1, media_kind: kind,
    thumb_url: `/thumb/${id}`, tags: [] },
  variants: [], duplicates: [], stack: null,
});
const followPage = (groups: unknown[]) => ({
  groups, counts: { new: groups.length }, offset: 0, has_more: false, facets: {},
  sources: [{ id: 1, provider: 'rule34video', provider_label: 'Rule34Video', author_key: 'name:jul3d' }],
  author_facets: { providers: ['rule34video'], tags: [['cum', groups.length]] },
});
const heroFollow = (n: number) => ({ key: 'name:jul3d', n, providers: ['rule34video'], avatar: '', avatar_fallback: '', held: [] });
const creator = (patch: Record<string, unknown> = {}) =>
  entity({ kind: 'creator', canonical_name: 'Jul3D', follow: heroFollow(3), ...patch });

interface Plan {
  entity?: Record<string, unknown>;
  items?: (query: URLSearchParams) => unknown;
  photos?: (query: URLSearchParams) => unknown;
  set?: (query: URLSearchParams) => unknown;
  feed?: unknown;
  /** `/api/follow` 的回话：资料页在线视图那一页。 */
  follow?: (query: URLSearchParams) => unknown;
  /** 每一次取资料现算一份，盖过 `entity`：写回前后服务端给的不一样时用。 */
  entityFor?: () => Record<string, unknown>;
  /** 换头像写回落地时调一下。 */
  picked?: () => void;
  /** `/api/feeds/check` 的回话，按次序一问一个；问完了就一直是最后那个。 */
  checks?: string[];
}

/** 按端点分流的假 fetch，记下每一次请求的地址与写入的内容。 */
function serve(plan: Plan = {}) {
  const writes: { url: string; body: Record<string, unknown> }[] = [];
  const reply = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(String(input), 'http://peach.test');
    const query = url.searchParams;
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      writes.push({ url: url.pathname, body });
      if (url.pathname === '/api/entity-alias') return reply({ added: !body.remove, alias: body.alias });
      if (url.pathname === '/api/avatar-pick') {
        plan.picked?.();
        return reply({ ok: true });
      }
      if (url.pathname === '/api/avatar-frame') {
        return reply({ ref: body.ref, source: 'gfriends', label: 'a', width: 300, height: 400, detail: '',
          found_by: '', current: false, crop: false, bases: [body.ref], focus: null, cast: 0, version: '' });
      }
      if (url.pathname === '/api/entity-name') {
        return reply({ changed: true, canonical_name: body.canonical, previous_name: body.name });
      }
      return reply({ ok: true });
    }
    if (url.pathname === '/api/avatar-choices') {
      return reply({ kind: 'performer', entity_id: 7001, names: [NAME], matched_names: [NAME], index_age_hours: 1,
        index_stale: false, choices: [{ ref: 'gfriends:a.jpg', source: 'gfriends', label: 'a', width: 0, height: 0,
          detail: '', found_by: '', current: false, crop: false, bases: [], focus: null, cast: 0 }] });
    }
    if (url.pathname === '/api/entity') return reply(plan.entityFor?.() ?? plan.entity ?? entity());
    if (url.pathname === '/api/items') return reply(plan.items ? plan.items(query) : items([1, 2, 3]));
    if (url.pathname === '/api/photos') return reply(plan.photos ? plan.photos(query) : photos(0));
    if (url.pathname === '/api/photo-set') return reply(plan.set ? plan.set(query) : photos(2, { id: 9, title: '图集' }));
    if (url.pathname === '/api/follow/credentials') return reply({ providers: [] });
    if (url.pathname === '/api/follow') return reply(plan.follow ? plan.follow(query) : followPage([]));
    if (url.pathname === '/api/feeds/discoveries') return reply(plan.feed ?? { items: [] });
    if (url.pathname === '/api/feeds/check') {
      const checks = plan.checks ?? ['idle'];
      return reply({ status: checks.length > 1 ? checks.shift() : checks[0] });
    }
    throw new Error(`没有安排这个端点：${url.pathname}`);
  });
  vi.stubGlobal('fetch', fetcher);
  const calls = (path: string) => fetcher.mock.calls
    .map(([input]) => new URL(String(input), 'http://peach.test'))
    .filter((url) => url.pathname === path);
  return { fetcher, writes, calls };
}

function shellProps(patch: Partial<EntityPageProps> = {}): EntityPageProps {
  const host = () => { const el = document.createElement('div'); el.className = 'peach-react'; document.body.append(el); return el };
  const feed = document.createElement('section');
  feed.hidden = true;
  document.body.append(feed);
  return {
    kind: 'performer', name: NAME, filters: {}, media: { media: 'videos', set: 0 }, jav: false, seed: '42',
    revision: 0, feedRevision: 0, photoSize: 'small', photoLayout: 'masonry', followImagesOnly: false,
    javLayout: 'small', javLayouts: [['big', '大图', 'maximize'], ['small', '小图', 'layout-grid']],
    photoLayouts: [['fixed', '固定比例', 'layout-grid'], ['masonry', '瀑布流', 'columns-2']],
    states: [{ k: '', label: '全部' }, { k: 'fresh', label: '没看过' }], peopleLayout: 'big',
    hosts: { filter: host(), feed, body: host() },
    layout: { active: false, size: 'small', portrait: false, javImage: 'cover' },
    selectMode: false, selected: new Set(), seekSeconds: 10, wireDrag: vi.fn(),
    skeletonHtml: () => '<div data-test-skeleton></div>', groupCollapse: true, canLoadMore: () => true,
    card: {
      helpers: {
        badgeHtml: () => '', titleHtml: (_it, raw) => raw,
        displayName: (_it, raw) => raw, tagLabel: (tag) => tag,
        personAvatar: vi.fn(() => ({ html: '', face: '' })),
      },
      actions: {
        open: vi.fn(), openResource: vi.fn(), openShort: vi.fn(), openShorts: vi.fn(), openMix: vi.fn(),
        openEntity: vi.fn(), openUnowned: vi.fn(), toggleTag: vi.fn(), toggleSelection: vi.fn(),
        watchLater: vi.fn(async () => {}), resourceOperation: vi.fn(async () => {}), mixRelated: vi.fn(async () => []),
        canFlip: () => true, revealSource: vi.fn(async () => ''),
      },
    },
    follow: {
      helpers: { workMark: () => '', tagLabel: (tag) => tag, wireDrag: vi.fn(), wireScroller: vi.fn(),
        listSkeletonHtml: () => '<div data-test-follow-skeleton></div>', jobProgress: vi.fn() },
      actions: { route: vi.fn(), shuffle: vi.fn(), loaded: vi.fn(), openDetail: vi.fn(), openAuthor: vi.fn(), openManage: vi.fn(),
        toggleSelection: vi.fn(), setImagesOnly: vi.fn(), setPhotoLayout: vi.fn(), canFlip: () => false, toast: vi.fn(),
        failure: vi.fn(), checkReport: vi.fn() },
    },
    helpers: {
      portraitImg: () => '', wireDrag: vi.fn(), wireScroller: vi.fn(), wireFeedRow: vi.fn(),
      feedRowHtml: (data) => `<div class="feednewrow">${(data?.items || []).map((one) => `<div data-feed-id="${one.id}"></div>`).join('')}</div>`,
      receipt: vi.fn(), failure: vi.fn(), aliasForm: vi.fn(async () => {}),
      sourceToolsHtml: () => '', wireSourceTools: vi.fn(), comboItems: () => [],
      sortKeys: vi.fn((sort: string) => [{ key: 'new', label: '入库时间', pressed: sort === 'new', dir: '' as const, ariaLabel: '' }]),
    },
    actions: {
      route: vi.fn(), toggleTag: vi.fn(), clearFilter: vi.fn(), clearAll: vi.fn(), setSort: vi.fn(),
      reshuffleVideos: vi.fn(() => '77'), setJavLayout: vi.fn(), setPhotoLayout: vi.fn(), openEntity: vi.fn(),
      javContext: vi.fn(), painted: vi.fn(), missing: vi.fn(), avatarChanged: vi.fn(), openFollowAuthor: vi.fn(),
    },
    ...patch,
  };
}

let push: (patch: Partial<EntityPageProps>) => Promise<void> = async () => {};
/** 壳经 `updateManagedRoute` 推进来的补丁，这里用一层状态代替。 */
function Shell(given: EntityPageProps) {
  const [current, set] = useState(given);
  push = (patch) => act(async () => { set((value) => ({ ...value, ...patch })) });
  return <EntityPage {...current} />;
}

/** 走真实的首屏路径：先 prefetch 落进缓存，再挂载。 */
async function open(plan: Plan = {}, patch: Partial<EntityPageProps> = {}) {
  const served = serve(plan);
  const props = shellProps(patch);
  await prefetchEntityPage(props);
  const requestsBeforeMount = served.fetcher.mock.calls.length;
  const { host } = await mountRoot(<QueryClientProvider client={queryClient}><Shell {...props} /></QueryClientProvider>);
  await settle();
  return { ...served, props, host, requestsBeforeMount };
}

const cards = (props: EntityPageProps) =>
  [...props.hosts.body.querySelectorAll<HTMLElement>('[data-media-grid] > [data-media-card]')].map((card) => card.dataset.id);
const pressedView = (props: EntityPageProps) =>
  props.hosts.filter.querySelector('[data-media-view][aria-pressed="true"]')?.getAttribute('data-media-view');
const readout = (props: EntityPageProps) => props.hosts.filter.querySelector('[data-entity-readout]')?.textContent;

describe('首屏', () => {
  it('资料页表格按页取数、全集排序并保留人物与标签筛选', async () => {
    const served = serve();
    await fetchItems('performer', NAME, { tag: '演示标签', state: 'fresh' }, { jav: true, seed: '42' },
      20, new AbortController().signal, { limit: 20, sort: 'name', dir: 'asc' });
    const params = served.calls('/api/items')[0]!.searchParams;
    expect(Object.fromEntries(params)).toMatchObject({ performer: NAME, tag: '演示标签', state: 'fresh',
      jav: '1', offset: '20', limit: '20', sort: 'name', dir: 'asc' });
    expect(params.has('count')).toBe(false);
  });
  it('FC2 合集读数按作品展示，文件总数留给分页', async () => {
    const page=await open({ entity:entity({asset_count:1}),items:()=>({...items([1,2,3]),total:19,work_total:1}) });
    expect(readout(page.props)).toBe('视频 · 1');
    expect(cards(page.props)).toEqual(['1','2','3']);
  });

  it('合并身份的旧地址交给站内路由打开规范资料页', async () => {
    const page=await open({entity:{redirect:{kind:'performer',name:'Christy White'}}}, {kind:'creator',name:'Christy White'});
    expect(page.props.actions.openEntity).toHaveBeenCalledWith('performer','Christy White',true);
    expect(page.props.actions.missing).not.toHaveBeenCalled();
    expect(page.host.querySelector('[data-entity-hero]')).toBeNull();
  });

  it.each(['performer', 'creator', 'studio', 'series', 'agency'])('别名入口替换为 %s 的规范资料页', async kind => {
    const page = await open({ entity: entity({ kind, canonical_name: 'Canonical' }) }, { kind, name: 'Alias' });
    expect(page.props.actions.openEntity).toHaveBeenCalledExactlyOnceWith(kind, 'Canonical', true);
    expect(page.props.actions.missing).not.toHaveBeenCalled();
    expect(cards(page.props)).toEqual([]);
  });

  it('规范名入口直接显示资料页', async () => {
    const page = await open();
    expect(page.props.actions.openEntity).not.toHaveBeenCalled();
    expect(cards(page.props)).toEqual(['1', '2', '3']);
    expect(readout(page.props)).toBe('视频 · 3');
  });

  it('资料、作品第一页、整组照片与新作在挂载前取齐，挂上之后不再补请求', async () => {
    const page = await open({ feed: { items: [{ id: 31, has_cover: true }] } });
    expect(page.calls('/api/entity')).toHaveLength(1);
    expect(page.calls('/api/items')).toHaveLength(1);
    expect(page.calls('/api/photos')).toHaveLength(1);
    expect(page.calls('/api/feeds/discoveries')[0]?.searchParams.get('entity')).toBe('7001');
    expect(page.fetcher.mock.calls).toHaveLength(page.requestsBeforeMount);
    expect(cards(page.props)).toEqual(['1', '2', '3']);
    expect(readout(page.props)).toBe('视频 · 3');
    expect(page.props.hosts.feed.querySelector('[data-feed-id="31"]')).not.toBeNull();
  });

  it('路由已经取得的实体资料直接用于首屏，作品和照片仍并行取齐', async () => {
    const page = await open({}, { resolvedEntity: entity() });
    expect(page.calls('/api/entity')).toHaveLength(0);
    expect(page.calls('/api/items')).toHaveLength(1);
    expect(page.calls('/api/photos')).toHaveLength(1);
    expect(cards(page.props)).toEqual(['1', '2', '3']);
  });

  it('作品请求的口径：每页 48，按地址上的排序与方向，随机带全站种子，这一页自己那一键不重复带', () => {
    const query = itemsParams('creator', '某创作者', { creator: '某创作者', tag: '巨乳', sort: 'seed', dir: '' },
      { jav: true, seed: '42' });
    expect(Object.fromEntries(query)).toEqual({
      creator: '某创作者', limit: '48', offset: '0', sort: 'seed', seed: '42', tag: '巨乳', jav: '1' });
    const next = itemsParams('performer', NAME, { sort: 'size', dir: 'asc' }, { jav: false, seed: '42' }, 48);
    expect(Object.fromEntries(next)).toMatchObject({ sort: 'size', dir: 'asc', offset: '48', count: '0' });
    expect(next.has('seed')).toBe(false);
  });

  it('标签资料页以本标签与追加筛选的交集聚合资产', () => {
    const params = itemsParams('tag', '巨乳', { tag: '口交,巨乳', sort: 'new' }, { jav: true, seed: '42' });
    expect(params.get('tag')).toBe('巨乳,口交');
    expect(params.get('tag_match')).toBe('all');
  });

  it('深链落在一个图集上：整组与那一组一起取，墙画的是那一组，读数写图集名', async () => {
    const page = await open({ photos: () => photos(6) }, { media: { media: 'photos', set: 9 } });
    expect(page.calls('/api/photo-set')[0]?.search).toBe('?id=9&limit=120');
    expect(page.fetcher.mock.calls).toHaveLength(page.requestsBeforeMount);
    expect(pressedView(page.props)).toBe('photos');
    expect(readout(page.props)).toBe('图集 · 2 张');
    expect(page.props.hosts.filter.querySelector('[data-photo-back]')).not.toBeNull();
  });

  it('名字对不上任何一位：交壳换成空态，不画半页', async () => {
    const page = await open({ entity: { error: 'not found' } });
    expect(page.props.actions.missing).toHaveBeenCalledTimes(1);
    expect(page.host.textContent).toBe('');
    expect(page.props.hosts.filter.childElementCount).toBe(0);
  });

  it('JAV 语境按第一页作品推出来，报给壳，排序键与卡片版式都按它', async () => {
    const page = await open({ items: () => items([1, 2], true) });
    expect(page.props.actions.javContext).toHaveBeenCalledWith(true);
    expect(page.props.helpers.sortKeys).toHaveBeenCalledWith('new', '', true);
  });
});

describe('换筛选与视图', () => {
  it('加一个标签只重取一次作品；换回看过的那一份筛选读缓存，不再请求', async () => {
    const page = await open({ items: (query) => items(query.get('tag') ? [7] : [1, 2, 3]) });
    await push({ filters: { tag: '巨乳' } });
    await settle();
    expect(page.calls('/api/items').map((url) => url.searchParams.get('tag'))).toEqual([null, '巨乳']);
    expect(cards(page.props)).toEqual(['7']);
    // 选了哪几枚由交集条列着，读数只报个数：标签名再列一遍，几枚长标签就能把这一行撑出页面。
    expect(readout(page.props)).toBe('视频 · 1 · 已选 1 个标签');
    expect(page.props.hosts.filter.querySelector('[data-entity-tag="巨乳"]')?.getAttribute('aria-pressed')).toBe('true');
    await push({ filters: {} });
    await settle();
    expect(page.calls('/api/items')).toHaveLength(2);
    expect(cards(page.props)).toEqual(['1', '2', '3']);
  });

  it('名下一部视频也没有：正文是空态，下排不出排序键', async () => {
    const page = await open({ entity: entity({ asset_count: 0 }), items: () => items([]) });
    expect(page.props.hosts.body.textContent).toContain('还没有视频');
    expect(page.props.hosts.body.querySelector('[data-media-grid]')).toBeNull();
    expect(page.props.hosts.filter.querySelector('[data-entity-sort]')).toBeNull();
    expect(buttonNamed('查看全部视频', page.props.hosts.body)).toBeNull();
  });

  it('筛完一部不剩：空态给一条回到全部视频的路，撤掉观看状态与交集条上的筛选', async () => {
    const page = await open({ items: (query) => items(query.get('tag') ? [] : [1, 2, 3]) },
      { filters: { tag: '巨乳', state: 'fresh', sort: 'new' } });
    expect(page.props.hosts.body.textContent).toContain('没有符合条件的视频');
    await click(buttonNamed('查看全部视频', page.props.hosts.body));
    expect(page.props.actions.route).toHaveBeenCalledWith(
      expect.objectContaining({ sort: 'new', tag: '', state: '', creator: '', studio: '', owner: '' }),
      expect.objectContaining({ media: 'videos' }));
  });

  it('视图键与观看状态不自己改地址：交给壳写好再推回来；照片已在首屏取过，切过去不请求', async () => {
    const page = await open({ photos: () => photos(4) });
    await click(page.props.hosts.filter.querySelector('[data-media-view="photos"]'));
    expect(page.props.actions.route).toHaveBeenLastCalledWith({}, { media: 'photos', set: 0 });
    const before = page.fetcher.mock.calls.length;
    await push({ media: { media: 'photos', set: 0 } });
    await settle();
    expect(page.fetcher.mock.calls).toHaveLength(before);
    expect(pressedView(page.props)).toBe('photos');
    expect(page.props.hosts.body.querySelectorAll('[data-photo-cell]')).toHaveLength(4);
    expect(page.props.actions.painted).toHaveBeenLastCalledWith('photos', true);
    await click(page.props.hosts.filter.querySelector('[data-media-view="videos"]'));
    expect(page.props.actions.route).toHaveBeenLastCalledWith({}, { media: 'videos', set: 0 });
  });

  it('事务所进页落在名册；换了筛选回到作品视图', async () => {
    const roster = [{ id: 1, k: '河北彩花', n: 12 }, { id: 2, k: '小湊よつ葉', n: 3 }];
    const page = await open({ entity: entity({ kind: 'agency', canonical_name: 'NAX', related_performers: roster }) },
      { kind: 'agency', name: 'NAX' });
    expect(pressedView(page.props)).toBe('people');
    expect(readout(page.props)).toBe('艺人 · 2');
    await push({ filters: { state: 'fresh' } });
    await settle();
    expect(pressedView(page.props)).toBe('videos');
  });

  it('片商页的名册是旗下 label，同台艺人照旧留在卡底；没有照片的实体不出照片键', async () => {
    const page = await open({
      entity: entity({ kind: 'studio', canonical_name: 'S1', labels: [{ k: 'S1 NO.1 STYLE', n: 40 }],
        related_performers: [{ id: 3, k: '河北彩花', n: 2 }] }),
    }, { kind: 'studio', name: 'S1' });
    expect(readout(page.props)).toBe('厂牌 · 1');
    expect(page.props.hosts.filter.querySelector('[data-media-view="photos"]')).toBeNull();
    expect(page.host.querySelector('[data-related-performer="河北彩花"]')).not.toBeNull();
  });

  it('只有一类东西时不出视图键', async () => {
    const page = await open();
    expect(page.props.hosts.filter.querySelector('[data-media-view]')).toBeNull();
  });

  it('批量操作后换代次：按同一份筛选重取作品', async () => {
    const page = await open();
    await push({ revision: 1 });
    await settle();
    expect(page.calls('/api/items')).toHaveLength(2);
  });

  it('作品的换一批：种子由壳换，岛先把新的一页取到手，键上的转圈等的就是这一趟', async () => {
    const page = await open();
    await act(async () => { page.props.hosts.filter.querySelector<HTMLElement>('[data-entity-batch]')?.click() });
    await settle();
    expect(page.props.actions.reshuffleVideos).toHaveBeenCalledTimes(1);
    expect(page.calls('/api/items').at(-1)?.searchParams.get('seed')).toBe('77');
    await push({ filters: { sort: 'seed' }, seed: '77' });
    await settle();
    expect(page.calls('/api/items')).toHaveLength(2);
  });
});

describe('照片墙', () => {
  it('续页数的是已取回的本地图片，带上回话里的种子；换一批换一粒种子，新墙取到才换上', async () => {
    const page = await open({
      photos: (query) => (query.get('offset') ? photos(2, { has_more: false, seed: 's1' })
        : photos(3, { has_more: true, seed: query.get('seed') || 's1' })),
    }, { media: { media: 'photos', set: 0 } });
    await click(page.props.hosts.body.querySelector('[data-entity-more]'));
    await settle();
    const more = page.calls('/api/photos').at(-1)!;
    expect(Object.fromEntries(more.searchParams)).toMatchObject({ offset: '3', limit: '120', seed: 's1' });
    expect(page.props.hosts.body.querySelectorAll('[data-photo-cell]')).toHaveLength(5);
    await act(async () => { page.props.hosts.filter.querySelector<HTMLElement>('[data-entity-batch]')?.click() });
    await settle();
    const shuffled = page.calls('/api/photos').at(-1)!;
    expect(shuffled.searchParams.get('limit')).toBe('120');
    expect(shuffled.searchParams.get('seed')).toMatch(/^\d+$/);
    expect(page.props.hosts.body.querySelectorAll('[data-photo-cell]')).toHaveLength(3);
  });

  it('图集里点返回：交壳回到整组照片', async () => {
    const page = await open({ photos: () => photos(6) }, { media: { media: 'photos', set: 9 } });
    await click(page.props.hosts.filter.querySelector('[data-photo-back]'));
    expect(page.props.actions.route).toHaveBeenLastCalledWith({}, { media: 'photos', set: 0 });
    await push({ media: { media: 'photos', set: 0 } });
    await settle();
    expect(readout(page.props)).toBe('照片 · 6 张');
  });
});

describe('创作者的在线视图', () => {
  const followCards = (props: EntityPageProps) =>
    [...props.hosts.body.querySelectorAll<HTMLElement>('[data-follow-item]')].map((card) => card.dataset.followItem);

  it('本地一件都没有：不出视频键，进页就落在在线；图片组多，默认是照片墙', async () => {
    const page = await open({
      entity: creator({ asset_count: 0 }),
      follow: () => followPage([followGroup(1, 'image'), followGroup(2, 'image'), followGroup(3, 'video')]),
    }, { kind: 'creator', name: 'Jul3D' });
    await settle();
    expect(page.calls('/api/follow')[0]?.searchParams.get('author')).toBe('name:jul3d');
    expect(page.props.hosts.filter.querySelector('[data-entity-media]:not([data-online-media])')).toBeNull();
    expect(page.props.hosts.filter.querySelector('[data-online-media] [aria-pressed="true"]')
      ?.getAttribute('data-media-view')).toBe('images');
    expect(page.props.hosts.body.querySelector('[data-follow-wall]')).not.toBeNull();
    expect(followCards(page.props)).toEqual(['1', '2']);
    expect(readout(page.props)).toBe('在线 · 3 项更新');
    expect(page.props.actions.painted).toHaveBeenLastCalledWith('online', true);
  });

  it('本地与在线都有：两个键并排，在线不先取；进在线交壳写地址，筛选只在页内重取', async () => {
    const page = await open({
      entity: creator({ asset_count: 3, follow: heroFollow(2) }),
      follow: (query) => followPage(query.get('provider') ? [followGroup(3, 'video')] : [followGroup(1, 'video'), followGroup(2, 'video')]),
    }, { kind: 'creator', name: 'Jul3D' });
    expect([...page.props.hosts.filter.querySelectorAll<HTMLElement>('[data-media-view]')]
      .map((key) => key.dataset.mediaView)).toEqual(['videos', 'online']);
    expect(page.calls('/api/follow')).toHaveLength(0);
    await click(page.props.hosts.filter.querySelector('[data-media-view="online"]'));
    expect(page.props.actions.route).toHaveBeenLastCalledWith({}, { media: 'online', set: 0 });
    await push({ media: { media: 'online', set: 0 } });
    await settle();
    expect(followCards(page.props)).toEqual(['1', '2']);
    expect(page.props.hosts.body.querySelector('[data-follow-wall]')).toBeNull();
    const routed = vi.mocked(page.props.actions.route).mock.calls.length;
    await click(page.props.hosts.filter.querySelector('[data-follow-provider="rule34video"]'));
    await settle();
    expect(page.calls('/api/follow').at(-1)?.searchParams.get('provider')).toBe('rule34video');
    expect(followCards(page.props)).toEqual(['3']);
    expect(page.props.actions.route).toHaveBeenCalledTimes(routed);
  });
});

describe('写操作', () => {
  it('添加别名：表单在壳，写回在岛；写完资料卡与作品列表一起重取', async () => {
    const page = await open();
    await click(page.host.querySelector('[data-namepick-toggle]'));
    await click(document.querySelector('[data-namepick-alias]'));
    const [mine, write] = vi.mocked(page.props.helpers.aliasForm).mock.calls[0]!;
    expect(mine).toEqual([]);
    const entityBefore = page.calls('/api/entity').length;
    const itemsBefore = page.calls('/api/items').length;
    await act(async () => { await write({ alias: 'しのだゆう' }) });
    await settle();
    expect(page.writes).toEqual([{ url: '/api/entity-alias', body: { kind: 'performer', name: NAME, alias: 'しのだゆう' } }]);
    expect(page.calls('/api/entity')).toHaveLength(entityBefore + 1);
    expect(page.calls('/api/items')).toHaveLength(itemsBefore + 1);
  });

  it('换完头像：资料卡与作品网格都按重取回来的数据重建，图地址换成新版本', async () => {
    let version = 'old';
    const page = await open({
      entityFor: () => entity({ image_version: version }),
      items: () => (version === 'old' ? items([1, 2, 3]) : items([4, 5, 6])),
      picked: () => { version = 'new' },
    }, {
      helpers: {
        ...shellProps().helpers,
        portraitImg: (_kind, data) => `<img data-test-portrait src="/entity-image?v=${String(data.image_version)}">`,
      },
    });
    const portrait = () => page.props.hosts.filter.ownerDocument.querySelector('[data-test-portrait]')?.getAttribute('src');
    expect(portrait()).toBe('/entity-image?v=old');
    expect(cards(page.props)).toEqual(['1', '2', '3']);
    await click(document.querySelector(`button[aria-label="更换${NAME}的头像"]`));
    await settle();
    await click(document.querySelector('[data-avatar-choice]'));
    await settle();
    await click(buttonNamed('整张使用'));
    await settle();
    expect(page.writes.map((one) => one.url)).toEqual(['/api/avatar-frame', '/api/avatar-pick']);
    expect(portrait()).toBe('/entity-image?v=new');
    // 网格把第一页当初值存进自己的状态：只重取不换键，卡片（连同署名里她的脸）停在旧的那一份。
    expect(cards(page.props)).toEqual(['4', '5', '6']);
    // 首页顶栏那排头像是壳缓存着的，也得告诉它作废。
    expect(page.props.actions.avatarChanged).toHaveBeenCalledOnce();
  });

  it('换统称：先问，写回成功才去新名字那一页；回执的撤销是另一次写回，再回到原名', async () => {
    const page = await open({ entity: entity({ aliases: ['しのだゆう'] }) });
    vi.spyOn(legacyUi, 'confirmModal').mockImplementation((async (options: { onConfirm: () => Promise<unknown> }) =>
      ({ confirmed: true, result: await options.onConfirm() })) as unknown as typeof legacyUi.confirmModal);
    await click(page.host.querySelector('[data-namepick-toggle]'));
    await click(document.querySelector('[data-namepick-name="しのだゆう"]'));
    await settle();
    // 换统称会重写整条实体的扁平投影：写之前让人看见换成什么、旧写法去哪；按钮与回执同一个动词。
    expect(legacyUi.confirmModal).toHaveBeenCalledWith(expect.objectContaining({
      title: '更改统称', confirmLabel: '更改统称',
      body: `「しのだゆう」将成为这份档案显示的名称，「${NAME}」留作别名。作品上的署名、搜索和标签都会跟着改写。`,
    }));
    expect(page.writes[0]).toEqual({ url: '/api/entity-name', body: { kind: 'performer', name: NAME, canonical: 'しのだゆう' } });
    expect(page.props.actions.openEntity).toHaveBeenLastCalledWith('performer', 'しのだゆう');
    const [message, options] = vi.mocked(page.props.helpers.receipt).mock.calls[0]!;
    expect(message).toBe('已把统称更改为 しのだゆう');
    await act(async () => { await options?.undo?.() });
    expect(page.writes[1]).toEqual({ url: '/api/entity-name', body: { kind: 'performer', name: 'しのだゆう', canonical: NAME } });
    expect(page.props.actions.openEntity).toHaveBeenLastCalledWith('performer', NAME);
  });

  it('订阅开关写回成功后记进这一页的资料，失败交壳出失败回执', async () => {
    const page = await open();
    const toggle = () => page.host.querySelector<HTMLInputElement>('input[data-entity-feed]')!;
    await click(toggle());
    await settle();
    expect(page.writes[0]).toEqual({ url: '/api/feeds/source', body: { action: 'follow', entity_id: 7001, enabled: true } });
    expect(toggle().checked).toBe(true);
    expect(queryClient.getQueryData<{ feed: { following: boolean } }>(['entity', 'performer', NAME])?.feed.following).toBe(true);
    page.fetcher.mockImplementationOnce(async () => ({ ok: false, status: 502, json: async () => ({}) }));
    await click(toggle());
    await settle();
    expect(page.props.helpers.failure).toHaveBeenCalledWith('取消订阅新作', expect.any(Error));
    expect(toggle().checked).toBe(true);
  });

  it('刚订上：等服务端这一轮拉取跑完，再把新作那一行重取一遍；跑着的时候不重取', async () => {
    const page = await open({ checks: ['running', 'idle'] });
    vi.useFakeTimers({ toFake: ['setTimeout'] });
    await click(page.host.querySelector<HTMLInputElement>('input[data-entity-feed]'));
    await settle();
    const step = async () => { await act(async () => { vi.advanceTimersByTime(3000) }); await settle() };
    await step();
    expect(page.calls('/api/feeds/check')).toHaveLength(1);
    expect(page.calls('/api/feeds/discoveries')).toHaveLength(1);
    await step();
    expect(page.calls('/api/feeds/check')).toHaveLength(2);
    expect(page.calls('/api/feeds/discoveries')).toHaveLength(2);
  });

  it('合集开关改了换一个代次，新作那一行重取', async () => {
    const page = await open();
    await push({ feedRevision: 1 });
    await settle();
    expect(page.calls('/api/feeds/discoveries')).toHaveLength(2);
  });
});
