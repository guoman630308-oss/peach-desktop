/* 关注页岛：请求地址怎么拼、两排怎么取样、一叠说什么、写完缓存里换什么，以及点下去交给壳的是什么。
 *
 * 骨架只铺在列表区、Shift 连选、照片墙尺寸档与进详情再回来这些要真浏览器的，在
 * `frontend/e2e/follow-feed.test.ts` 里量。 */
import { notifyManager, QueryClientProvider, type InfiniteData } from '@tanstack/react-query';
import { act, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  FOLLOW_AUTO_LOAD_LIMIT, backfillState, dropCondition, followConditions, followPageUrl, followStack, groupMediaKinds, itemForMedia,
  nextSort, prefetchFollowFeed, randomOrder, sortAriaLabel, withStatus,
  type FollowFeedActions, type FollowFeedHelpers, type FollowFeedProps, type FollowGroup, type FollowItem,
  type FollowPage, type FollowSource, type FollowStackInfo, type FollowView,
} from '../../src/react/follow-feed/follow-feed';
import { FollowFeedPage } from '../../src/react/follow-feed/follow-feed-page';
import { FollowCard } from '../../src/react/follow-feed/follow-card';
import { learnFollowDims } from '../../src/react/follow-feed/follow-marks';
import { queryClient } from '../../src/react/query';
import { click, mount, mountRoot, settle } from './render';

/* 回写尺寸那一批由 `follow-marks.test.ts` 量；这里只看卡片在什么时候报。 */
vi.mock(import('../../src/react/follow-feed/follow-marks'), async (importOriginal) => ({
  ...(await importOriginal()), learnFollowDims: vi.fn(),
}));

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const VIEW: FollowView = {
  status: '', media: 'videos', author: '', provider: '', work: '', tags: [], durMin: 0, durMax: 0, sort: 'new', dir: 'desc', seed: 7,
};
const view = (patch: Partial<FollowView> = {}): FollowView => ({ ...VIEW, ...patch });

const item = (id: number, extra: Partial<FollowItem> = {}): FollowItem => ({
  id, title: `更新 ${id}`, status: 'new', provider: 'kemono', provider_label: 'Kemono', source_id: 1,
  published_at: `2026-09-${String(10 + (id % 10)).padStart(2, '0')}T08:00:00Z`, media_kind: 'video', playable: true,
  thumb_url: `/thumb/${id}`, tags: [], ...extra,
});
const group = (primary: FollowItem, extra: Partial<FollowGroup> = {}): FollowGroup =>
  ({ primary, variants: [], duplicates: [], stack: null, ...extra });
const source = (id: number, author: string, extra: Partial<FollowSource> = {}): FollowSource => ({
  id, provider: 'kemono', provider_label: 'Kemono', author_key: `name:${author}`, last_status: 'ok', ...extra,
});

it('失败封面随数据刷新重试，可用图片保持同一节点', async () => {
  const common = { group: group(item(1)), authorSources: [], media: 'videos' as const,
    context: { sources: [], aliases: [], credentials: new Set<string>() }, selected: false, selectMode: false,
    busy: '', failure: '', actions: { openDetail: vi.fn(), openAuthor: vi.fn(), canFlip: () => false } };
  const root = await mountRoot(<FollowCard {...common} retryKey={1} />);
  const first = root.host.querySelector('[data-media-pic] > img')!;
  await act(async () => first.dispatchEvent(new Event('error')));
  expect(root.host.querySelector('[data-follow-nothumb]')).not.toBeNull();
  await root.rerender(<FollowCard {...common} retryKey={1} />);
  expect(root.host.querySelector('[data-media-pic] > img')).toBeNull();
  await root.rerender(<FollowCard {...common} retryKey={2} />);
  const recovered = root.host.querySelector('[data-media-pic] > img');
  expect(recovered).not.toBeNull();
  await root.rerender(<FollowCard {...common} retryKey={3} />);
  expect(root.host.querySelector('[data-media-pic] > img')).toBe(recovered);
});

describe('请求地址', () => {
  it('默认那一档不写进去，标签按逗号拼，种子只跟着随机排序走', () => {
    expect(followPageUrl(VIEW, 0)).toBe('/api/follow?limit=100&offset=0');
    const url = new URL(followPageUrl(view({
      status: 'saved', author: 'name:kou', provider: 'kemono', tags: ['3d', 'loop'], work: 'zelda', sort: 'hot', dir: 'asc',
    }), 100), 'http://peach.test');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      limit: '100', offset: '100', status: 'saved', author: 'name:kou', provider: 'kemono', tag: '3d,loop', work: 'zelda',
      sort: 'hot', dir: 'asc',
    });
    expect(new URL(followPageUrl(view({ sort: 'rand', seed: 42 }), 0), 'http://peach.test').searchParams.get('seed')).toBe('42');
  });

  it('时长两端按秒交给服务端，0 那一端不写', () => {
    const params = (patch: Partial<FollowView>) =>
      Object.fromEntries(new URL(followPageUrl(view(patch), 0), 'http://peach.test').searchParams);
    expect(params({ durMin: 600, durMax: 1800 })).toMatchObject({ dur_min: '600', dur_max: '1800' });
    expect(params({ durMin: 600 })).not.toHaveProperty('dur_max');
    expect(params({ durMax: 1800 })).not.toHaveProperty('dur_min');
  });
});

describe('回到关注页', () => {
  /* 从别的页面回来，壳交回同一份筛选与代次：列表照离开前那一份接着画，不重取；凭据每次都重取。
     重新进入时壳推新代次换键，才重取列表。 */
  it('同一个键还在缓存里就原样接着用，换代次才重取列表', async () => {
    const fetched = vi.fn(async (input: string) => ({
      ok: true, status: 200,
      json: async () => (String(input).startsWith('/api/follow/credentials')
        ? { providers: [] } : { groups: [], counts: {}, has_more: false, offset: 0 }),
    }));
    vi.stubGlobal('fetch', fetched);
    const paths = () => fetched.mock.calls.map(([input]) => new URL(String(input), 'http://peach.test').pathname);
    const signal = new AbortController().signal;
    await prefetchFollowFeed({ view: VIEW, revision: 3 }, signal);
    await prefetchFollowFeed({ view: VIEW, revision: 3 }, signal);
    expect(paths().sort()).toEqual(['/api/follow', '/api/follow/credentials', '/api/follow/credentials']);
    await prefetchFollowFeed({ view: VIEW, revision: 4 }, signal);
    expect(paths().filter((path) => path === '/api/follow')).toHaveLength(2);
  });
});

describe('取样', () => {
  it('同一粒种子次序不变，换种子换一批', () => {
    const keys = Array.from({ length: 30 }, (_, index) => `k${index}`);
    const once = randomOrder(keys, (key) => key, 11);
    expect(randomOrder(keys, (key) => key, 11)).toEqual(once);
    expect(randomOrder(keys, (key) => key, 12)).not.toEqual(once);
    expect([...once].sort()).toEqual([...keys].sort());
  });

});

describe('媒体与一叠', () => {
  /* 关注卡的 `stack` 取 `/api/follow` 下发的形状（`follow_faces.annotate_group`）：`faces` 里编号相同的
     是同一个画面，服务端已按编号去重。 */
  const stackOf = (media: number, copies: number, kind: string, faces: [string, number, string][]): FollowStackInfo =>
    ({ media, copies, kind, faces: faces.map(([thumb_url, face, media_kind]) => ({ thumb_url, face, media_kind })) });

  it('只在彼此不同的画面之间翻', () => {
    // 2026-09-24 生产样本：paheal 一组 9 帖（1 图 8 视频），画面各不相同，照翻。
    const burst = stackOf(9, 9, 'mixed', [
      ...Array.from({ length: 8 }, (_, index): [string, number, string] => [`/follow-cover?id=${index}`, index, 'video']),
      ['/img/8', 8, 'image'],
    ]);
    // 主条目加两份穿衣版一致的 alt，服务端把三张判成一个画面：只剩一张，不翻。
    const oneFace = stackOf(3, 3, 'video', [['/follow-cover?id=1', 0, 'video']]);
    expect(followStack({ cover: '/follow-cover?id=0', coverFace: 0, stack: burst }).faces)
      .toEqual([...Array.from({ length: 8 }, (_, index) => `/follow-cover?id=${index}`), '/img/8']);
    const still = [
      followStack({ cover: '/follow-cover?id=1', coverFace: 0, stack: oneFace }),
      // 封面落在 alt 上、地址不同却是同一个画面：按编号认，不按地址。
      followStack({ cover: '/follow-cover?id=2', coverFace: 0, stack: oneFace }),
    ];
    expect(still.map((stack) => stack.faces)).toEqual([[], []]);
    // 不翻的照样是一叠：纸边和角标都还在。
    expect(still.map((stack) => stack.isMix)).toEqual([true, true]);
    expect(followStack({ cover: '/follow-cover?id=0', coverFace: 0, stack: burst, limit: 3 }).faces).toHaveLength(3);
    // 图片视图只翻图片。
    expect(followStack({ cover: '/img/8', coverFace: 8, stack: burst, imageView: true }).faces).toEqual([]);
  });

  it('角标只数不同的媒体，量词随媒体类型，字形说点开看什么', () => {
    const video: [string, number, string][] = [['/v', 0, 'video']];
    const said = [
      // 同一个视频在两个站各一份：一个媒体、两个来源。
      followStack({ stack: stackOf(1, 2, 'video', video) }),
      // 三个不同的视频，其中一个另有跨站那份：只报不同视频的数目。
      followStack({ stack: stackOf(3, 4, 'video', video) }),
      followStack({ stack: stackOf(11, 11, 'image', [['/img', 0, 'image']]), imageView: true }),
      // 图和视频混在一组：量词用中性的「个媒体」，字形跟当前视图。
      followStack({ stack: stackOf(9, 9, 'mixed', video) }),
      followStack({ stack: stackOf(9, 9, 'mixed', video), imageView: true }),
      // 只合并了一份，或者没有 stack：不是一叠，没有角标。
      followStack({ stack: null }),
      followStack({ cover: '/img/0' }),
    ].map((stack) => [stack.isMix, stack.label, stack.glyph]);
    expect(said).toEqual([
      [true, '2 个来源', 'play'], [true, '3 个视频', 'play'], [true, '11 张图片', 'pics'], [true, '9 个媒体', 'play'],
      [true, '9 个媒体', 'pics'], [false, '', 'play'], [false, '', 'play'],
    ]);
  });

  it('外部文件页不算视频；卡面取组里最新的、含当前媒体的那条', () => {
    const external = group(item(1, { media_kind: 'external' }));
    expect([...groupMediaKinds(external)]).toEqual([]);
    const mixed = group(item(1, { published_at: '2026-09-01T00:00:00Z' }), {
      variants: [
        item(2, { media_kind: 'image', published_at: '2026-09-05T00:00:00Z' }),
        item(3, { media_kind: 'image', published_at: '2026-09-03T00:00:00Z' }),
      ],
    });
    expect(itemForMedia(mixed, 'images').id).toBe(2);
    expect(itemForMedia(mixed, 'videos').id).toBe(1);
  });
});

describe('底栏与排序', () => {
  it('往回抓到第几页：页码从 0 起，给人读的从 1 起', () => {
    expect(backfillState([source(1, 'a', { can_backfill: false })])).toBe('');
    expect(backfillState([source(1, 'a', { can_backfill: true, backfill_page: 0 })])).toBe('每个来源都只抓了第 1 页');
    expect(backfillState([source(1, 'a', { can_backfill: true, backfill_page: 2 })])).toBe('每个来源都抓到第 3 页');
    expect(backfillState([
      source(1, 'a', { can_backfill: true, backfill_page: 0 }), source(2, 'b', { can_backfill: true, backfill_page: 2 }),
    ])).toBe('已抓到第 1–3 页');
  });

  it('点别的列从默认方向起，点生效那列翻方向；无障碍名称播报点下去会得到什么', () => {
    expect(nextSort('hot', 'new', 'desc')).toEqual({ sort: 'hot', dir: 'desc' });
    expect(nextSort('new', 'new', 'desc')).toEqual({ sort: 'new', dir: 'asc' });
    expect(sortAriaLabel('new', '更新时间', 'new', 'desc')).toBe('按更新时间从旧到新排序');
    expect(sortAriaLabel('dur', '时长', 'new', 'desc')).toBe('按时长从长到短排序');
  });

  it('组合条按维度撤条件，同一个字符串在两个维度里各算各的', () => {
    const current = view({ author: 'loop', tags: ['loop', 'solo'] });
    const rows = followConditions(current, { authors: new Map([['loop', 'Loop 本人']]), providers: new Map(), works: new Map() });
    expect(rows.map((row) => `${row.kind}:${row.label}`)).toEqual(['创作者:Loop 本人', '标签:loop', '标签:solo']);
    expect(dropCondition(current, rows[1]!)).toEqual({ ...current, tags: ['solo'] });
    expect(dropCondition(current, rows[0]!)).toEqual({ ...current, author: '' });
  });
});

describe('写完换缓存里的局部', () => {
  const data = (): InfiniteData<FollowPage> => ({
    pageParams: [0],
    pages: [{
      groups: [group(item(1)), group(item(2), { variants: [item(3)] })], counts: { new: 3, seen: 0 }, sources: [],
      facets: {}, offset: 0, has_more: false,
    }],
  });

  it('换状态、挪计数；筛着「未看」时标成已看的那组整组移出', () => {
    const all = withStatus(data(), VIEW, 1, 'seen');
    expect(all.pages[0]!.groups.map((row) => [row.primary.id, row.primary.status])).toEqual([[1, 'seen'], [2, 'new']]);
    expect(all.pages[0]!.counts).toEqual({ new: 2, seen: 1 });
    const unseen = withStatus(data(), view({ status: 'new' }), 1, 'seen');
    expect(unseen.pages[0]!.groups.map((row) => row.primary.id)).toEqual([2]);
    // alt 换状态不移走整组：卡面还是主条目那张。
    const variant = withStatus(data(), view({ status: 'new' }), 3, 'ignored');
    expect(variant.pages[0]!.groups.map((row) => row.primary.id)).toEqual([1, 2]);
    expect(variant.pages[0]!.groups[1]!.variants[0]!.status).toBe('ignored');
  });
});

/* ── 页面 ── */

function helpers(): FollowFeedHelpers {
  return {
    workMark: (row) => row[1].slice(0, 2), tagLabel: (tag) => tag, wireDrag: vi.fn(), wireScroller: vi.fn(),
    listSkeletonHtml: () => '<div data-test-skeleton>骨架</div>', jobProgress: vi.fn(),
  };
}

function actions(): FollowFeedActions {
  return {
    route: vi.fn(), shuffle: vi.fn(), loaded: vi.fn(), openDetail: vi.fn(), openAuthor: vi.fn(), openManage: vi.fn(), toggleSelection: vi.fn(),
    setImagesOnly: vi.fn(), setPhotoLayout: vi.fn(), canFlip: () => false, toast: vi.fn(), failure: vi.fn(), checkReport: vi.fn(),
  };
}

const AUTHORS = ['kou', 'mira', 'opal', 'remi', 'nanase', 'lazy'];
const SOURCES = AUTHORS.map((author, index) => source(index + 1, author));

function feed(groups: FollowGroup[], extra: Partial<FollowPage> = {}): FollowPage {
  return {
    groups, counts: { new: groups.length, saved: 0, ignored: 0 }, sources: SOURCES, author_aliases: [], offset: 0, has_more: false,
    facets: {
      authors: AUTHORS.map((author) => `name:${author}`), providers: ['kemono'],
      tags: [['3d', 9], ['loop', 5], ['solo', 2]], works: [['zelda', 'The Legend of Zelda', 3, 1]],
    },
    ...extra,
  };
}

/** 假服务端：`/api/follow` 回给定的那一页，写成功的状态记进这一页，重读时读得到。 */
function serve(page: FollowPage, writes: { status?: number; body?: unknown } = {}) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/follow/credentials')) return { ok: true, status: 200, json: async () => ({ providers: [] }) };
    if (url.startsWith('/api/follow?')) return { ok: true, status: 200, json: async () => structuredClone(page) };
    if (init?.method === 'POST') {
      const status = writes.status ?? 200;
      if (status < 400 && (url === '/api/follow/status' || url === '/api/follow/save')) {
        const sent = JSON.parse(String(init.body)) as { item: number; to?: string };
        page.groups.forEach((row) => { if (row.primary.id === sent.item) row.primary.status = sent.to || 'saved' });
      }
      return { ok: status < 400, status, json: async () => writes.body ?? { ok: true } };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

function props(patch: Partial<FollowFeedProps> = {}): FollowFeedProps {
  return {
    view: VIEW, seed: 5, revision: 1, selectMode: false, selected: new Set(), photoSize: 'small', photoLayout: 'masonry',
    imagesOnly: false, helpers: helpers(), actions: actions(), ...patch,
  };
}

let push: (patch: Partial<FollowFeedProps>) => void = () => {};
/** 壳经 `updateManagedRoute` 推进来的补丁，这里用一层状态代替。 */
function Shell(given: FollowFeedProps) {
  const [current, set] = useState(given);
  push = (patch) => set((value) => ({ ...value, ...patch }));
  return <FollowFeedPage {...current} />;
}
async function open(given: FollowFeedProps) {
  const host = await mount(<QueryClientProvider client={queryClient}><Shell {...given} /></QueryClientProvider>);
  await settle();
  return host;
}
const authorOrder = (host: HTMLElement) =>
  [...host.querySelectorAll<HTMLElement>('button[data-follow-author]')].map((button) => button.dataset.followAuthor);
const cardIds = (host: HTMLElement) =>
  [...host.querySelectorAll<HTMLElement>('[data-follow-list] > [data-follow-item]')].map((card) => card.dataset.followItem);

describe('页面', () => {
  it('创作者排按种子取样，换筛选推回来的新 view 不洗牌，换种子才换', async () => {
    serve(feed([group(item(1))]));
    const given = props();
    const host = await open(given);
    const first = authorOrder(host);
    expect(first).toHaveLength(AUTHORS.length);
    expect(first).toEqual(randomOrder(AUTHORS.map((author) => `name:${author}`), (key) => key, 5));
    await act(async () => push({ view: view({ status: 'new' }) }));
    await settle();
    expect(authorOrder(host)).toEqual(first);
    await act(async () => push({ seed: 99 }));
    expect(authorOrder(host)).not.toEqual(first);
  });

  it('点创作者、标签、状态、排序与媒体都只交给壳一份新 view', async () => {
    serve(feed([group(item(1)), group(item(2, { media_kind: 'image' }))]));
    const given = props({ view: view({ tags: ['loop'] }) });
    const host = await open(given);
    const route = given.actions.route as ReturnType<typeof vi.fn>;
    await click(host.querySelector('button[data-follow-author="name:mira"]'));
    expect(route).toHaveBeenLastCalledWith({ ...given.view, author: 'name:mira' });
    await click(host.querySelector('button[data-follow-tag="3d"]'));
    expect(route).toHaveBeenLastCalledWith({ ...given.view, tags: ['loop', '3d'] });
    await click(host.querySelector('button[data-follow-tag="loop"]'));
    expect(route).toHaveBeenLastCalledWith({ ...given.view, tags: [] });
    await click(host.querySelector('[data-follow-filter="saved"]'));
    expect(route).toHaveBeenLastCalledWith({ ...given.view, status: 'saved' });
    await click(host.querySelector('[data-follow-sort="new"]'));
    expect(route).toHaveBeenLastCalledWith({ ...given.view, dir: 'asc' });
    await click(host.querySelector('[data-media-view="images"]'));
    expect(route).toHaveBeenLastCalledWith({ ...given.view, media: 'images' });
    // 组合条列出生效的标签，撤一条交回去的是撤掉之后的 view。
    await click(host.querySelector('[data-follow-drop="loop"]'));
    expect(route).toHaveBeenLastCalledWith({ ...given.view, tags: [] });
    expect(route).toHaveBeenCalledTimes(7);
  });

  it('视频与图片各摆含那种媒体的组，读数数的是全库口径', async () => {
    serve(feed([group(item(1)), group(item(2, { media_kind: 'image' })), group(item(3, { media_kind: 'external' }))],
      { counts: { new: 40, saved: 2, ignored: 1 } }));
    const given = props();
    const host = await open(given);
    expect(cardIds(host)).toEqual(['1']);
    expect(host.querySelector('[data-follow-readout]')?.textContent).toBe('43 项更新 · 显示 1');
    await act(async () => push({ view: view({ media: 'images' }) }));
    await settle();
    expect(cardIds(host)).toEqual(['2']);
    expect(host.querySelector('[data-follow-list]')?.hasAttribute('data-follow-wall')).toBe(true);
    // 取到一版就交给壳：详情读的是它。
    expect(given.actions.loaded).toHaveBeenCalled();
  });

  it('列表取不到时页头照画：标题与「管理关注」都在，失败原因下面给重试', async () => {
    const fetcher = vi.fn(async (url: string) => (url.startsWith('/api/follow?')
      ? { ok: false, status: 500, json: async () => ({ error: '服务端出错' }) }
      : { ok: true, status: 200, json: async () => ({ providers: [] }) }));
    vi.stubGlobal('fetch', fetcher);
    const given = props();
    const host = await open(given);
    expect(host.querySelector('[data-follow-title]')?.textContent).toBe('关注');
    await click(host.querySelector('[data-follow-manage]'));
    expect(given.actions.openManage).toHaveBeenCalled();
    expect(host.querySelector('[data-media-error] [data-note-action]')?.textContent).toBe('重试');
  });

  it('有来源、一条更新都还没有：没设筛选时说还没有更新，设了筛选才叫人换筛选', async () => {
    serve(feed([]));
    const bare = await open(props());
    expect(bare.querySelector('[data-follow-empty]')?.textContent).toContain('还没有更新');
    expect(bare.querySelector('[data-follow-empty]')?.textContent).not.toContain('筛选');
    queryClient.clear();
    serve(feed([]));
    const filtered = await open(props({ view: view({ status: 'saved' }), revision: 2 }));
    expect(filtered.querySelector('[data-follow-empty]')?.textContent).toContain('切换状态或来源筛选后再试');
  });

  it('全部创作者可从同一横滚行访问', async () => {
    const many = Array.from({ length: 30 }, (_, index) => `a${index}`);
    const sources = many.map((author, index) => source(index + 1, author, { author_name: author === 'a29' ? 'Zed Lastone' : author }));
    serve(feed([group(item(1))], { sources, facets: { authors: many.map((author) => `name:${author}`), providers: ['kemono'] } }));
    const host = await open(props());
    expect(authorOrder(host)).toHaveLength(many.length);
    expect(authorOrder(host)).toContain('name:a29');
    expect(host.querySelector('input[type="search"]')).toBeNull();
  });

  it(`摆满 ${FOLLOW_AUTO_LOAD_LIMIT} 组以后「加载更多」要点一下才接下一页`, async () => {
    const groups = Array.from({ length: FOLLOW_AUTO_LOAD_LIMIT }, (_, index) => group(item(index + 1)));
    const page = feed(groups, { has_more: true });
    const fetcher = serve(page);
    const host = await open(props());
    const more = host.querySelector('[data-follow-pagination] button[data-entity-more]');
    expect(more?.textContent).toBe('加载更多');
    const before = fetcher.mock.calls.filter(([url]) => String(url).startsWith('/api/follow?')).length;
    page.groups = [group(item(FOLLOW_AUTO_LOAD_LIMIT + 1))];
    page.has_more = false;
    await click(more);
    await settle();
    const pages = fetcher.mock.calls.filter(([url]) => String(url).startsWith('/api/follow?'));
    expect(pages).toHaveLength(before + 1);
    expect(new URL(String(pages.at(-1)![0]), 'http://peach.test').searchParams.get('offset')).toBe('100');
    expect(host.querySelector(`[data-follow-item="${FOLLOW_AUTO_LOAD_LIMIT + 1}"]`)).not.toBeNull();
  });

  it('没有关注任何来源时指到添加关注', async () => {
    serve(feed([], { sources: [], facets: {} }));
    const host = await open(props());
    expect(host.textContent).toContain('还没有关注任何来源');
    expect(host.querySelector('a[href="/subscriptions?tab=add"]')).not.toBeNull();
  });

  it('标记已看：请求体只带条目与目标状态，卡上立刻换状态，回执带撤销', async () => {
    const fetcher = serve(feed([group(item(1)), group(item(2))]));
    const given = props();
    const host = await open(given);
    await click(host.querySelector('[data-follow-status="1"][data-to="seen"]'));
    await settle();
    const write = fetcher.mock.calls.find(([url]) => url === '/api/follow/status');
    expect(JSON.parse(String(write?.[1]?.body))).toEqual({ item: 1, to: 'seen' });
    expect(host.querySelector<HTMLElement>('[data-follow-item="1"]')?.dataset.status).toBe('seen');
    const toast = given.actions.toast as ReturnType<typeof vi.fn>;
    expect(toast.mock.calls[0]![0]).toBe('已标记已看');
    await act(async () => { await toast.mock.calls[0]![1].undo() });
    const undo = fetcher.mock.calls.filter(([url]) => url === '/api/follow/status').at(-1);
    expect(JSON.parse(String(undo?.[1]?.body))).toEqual({ item: 1, to: 'new' });
  });

  it('保存到数据库不给撤销；只读端写入回 409 时原因留在卡上', async () => {
    serve(feed([group(item(1))]), { status: 409, body: { error: '只读副本不能写入' } });
    const given = props();
    const host = await open(given);
    await click(host.querySelector('[data-follow-save="1"]'));
    await settle();
    expect(host.querySelector('[data-follow-item="1"] [data-follow-state]')?.textContent).toBe('只读副本不能写入');
    expect(given.actions.failure).toHaveBeenCalledWith('保存到数据库', expect.anything());

    serve(feed([group(item(1))]));
    await click(host.querySelector('[data-follow-save="1"]'));
    await settle();
    const toast = given.actions.toast as ReturnType<typeof vi.fn>;
    expect(toast).toHaveBeenLastCalledWith('已保存到数据库');
    expect(host.querySelector('[data-follow-item="1"] [data-follow-state]')?.textContent).toBe('');
  });

  it('多选里点卡交给壳切换选中，Shift 带上连选；选中态由壳推回来', async () => {
    serve(feed([group(item(1)), group(item(2))]));
    const given = props({ selectMode: true });
    const host = await open(given);
    const card = host.querySelector<HTMLElement>('[data-follow-item="2"]')!;
    expect(card.querySelector('[data-follow-actions]')).toBeNull();
    expect(card.querySelector('[data-media-check]')).not.toBeNull();
    await act(async () => { card.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })) });
    expect(given.actions.toggleSelection).toHaveBeenCalledWith(2, true);
    expect(given.actions.openDetail).not.toHaveBeenCalled();
    await act(async () => push({ selected: new Set([2]) }));
    expect(card.hasAttribute('data-selected')).toBe(true);
    expect(host.querySelector('[data-follow-feed]')?.hasAttribute('data-select-mode')).toBe(true);
    await act(async () => push({ selectMode: false }));
    expect(card.querySelector('[data-follow-actions]')).not.toBeNull();
    await click(host.querySelector('[data-follow-item="1"]'));
    expect(given.actions.openDetail).toHaveBeenCalledWith(1);
  });

  it('检查更新由岛发起，作业 id 记进 sessionStorage，跑完交回报告并重读', async () => {
    const fetcher = serve(feed([group(item(1))]), { body: { job_id: 'job-9', status: 'running' } });
    const given = props();
    const host = await open(given);
    const progress = given.helpers.jobProgress as ReturnType<typeof vi.fn>;
    expect(progress).toHaveBeenCalled();
    await click(host.querySelector('[data-follow-recheck]'));
    await settle();
    const start = fetcher.mock.calls.find(([url, init]) => url === '/api/follow/check' && init?.method === 'POST');
    expect(JSON.parse(String(start?.[1]?.body))).toEqual({ background: true });
    expect(sessionStorage.getItem('peach-follow-job')).toBe('job-9');
    const options = progress.mock.calls.at(-1)![0];
    await act(async () => options.busy(true));
    expect(host.querySelector('[data-follow-recheck]')?.getAttribute('aria-busy')).toBe('true');
    const reads = fetcher.mock.calls.filter(([url]) => String(url).startsWith('/api/follow?')).length;
    await act(async () => options.complete({ status: 'done', results: [{ ok: true, added: 2 }] }));
    await settle();
    expect(given.actions.checkReport).toHaveBeenCalledWith({ status: 'done', results: [{ ok: true, added: 2 }] });
    expect(host.textContent).toContain('检查结果');
    expect(host.textContent).toContain('检查了 1 个来源：新增 2 条');
    expect(fetcher.mock.calls.filter(([url]) => String(url).startsWith('/api/follow?')).length).toBeGreaterThan(reads);
  });

  it('往回抓一页带上 older，跑完之前那枚键一直写「抓取中」', async () => {
    const fetcher = serve(feed([group(item(1))], { sources: [source(1, 'kou', { can_backfill: true, backfill_page: 1 })] }),
      { body: { job_id: 'job-3', status: 'running' } });
    const given = props();
    const host = await open(given);
    await click(host.querySelector('[data-follow-older]'));
    await settle();
    const start = fetcher.mock.calls.find(([url, init]) => url === '/api/follow/check' && init?.method === 'POST');
    expect(JSON.parse(String(start?.[1]?.body))).toEqual({ older: true, background: true });
    const options = (given.helpers.jobProgress as ReturnType<typeof vi.fn>).mock.calls.at(-1)![0];
    expect(host.querySelector('[data-follow-older]')?.textContent).toContain('抓取中');
    expect(host.querySelector('[data-follow-older]')?.getAttribute('aria-busy')).toBe('true');
    expect(host.querySelector('[data-follow-recheck]')?.getAttribute('aria-busy')).toBe('true');
    await act(async () => options.busy(true));
    expect(host.querySelector('[data-follow-older]')?.textContent).toContain('抓取中');
    expect(host.querySelector('[data-follow-backfill]')?.textContent).toBe('每个来源都抓到第 2 页');
    // 忙着时两枚键只标 aria-busy，不落 BoardUI 的禁用档；再点哪一枚都不另起一趟。
    for (const selector of ['[data-follow-older]', '[data-follow-recheck]']) {
      const button = host.querySelector(selector)!;
      expect(button.getAttribute('aria-busy')).toBe('true');
      expect(button.hasAttribute('aria-disabled')).toBe(false);
      await click(button);
    }
    await settle();
    expect(fetcher.mock.calls.filter(([url, init]) => url === '/api/follow/check' && init?.method === 'POST')).toHaveLength(1);
  });

  it('管理关注是次级键、检查更新是主键；没有来源时只剩管理关注', async () => {
    serve(feed([group(item(1))]));
    const host = await open(props());
    expect(host.querySelector('[data-follow-manage]')?.className).not.toContain('bg-button-primary');
    expect(host.querySelector('[data-follow-recheck]')?.className).toContain('bg-button-primary');
    queryClient.clear();
    serve(feed([], { sources: [], facets: {} }));
    const empty = await open(props({ revision: 2 }));
    expect(empty.querySelector('[data-follow-manage]')).not.toBeNull();
    expect(empty.querySelector('[data-follow-recheck]')).toBeNull();
  });

  it('再点按下的那位创作者撤掉这一条，按下的那枚有 aria-pressed', async () => {
    serve(feed([group(item(1))]));
    const given = props({ view: view({ author: 'name:mira' }) });
    const host = await open(given);
    const pressed = host.querySelector('button[data-follow-author="name:mira"]');
    expect(pressed?.getAttribute('aria-pressed')).toBe('true');
    await click(pressed);
    expect(given.actions.route).toHaveBeenLastCalledWith({ ...given.view, author: '' });
  });

  it('图片卡有尺寸就先占位；没尺寸的加载完回写固有尺寸；视频卡不回写', async () => {
    serve(feed([group(item(1)), group(item(2, { media_kind: 'image', width: 640, height: 480 })),
      group(item(3, { media_kind: 'image' }))]));
    const given = props({ view: view({ media: 'images' }) });
    const host = await open(given);
    const image = (id: number) => host.querySelector<HTMLImageElement>(`[data-follow-item="${id}"] img`)!;
    expect([image(2).getAttribute('width'), image(2).getAttribute('height')]).toEqual(['640', '480']);
    expect(image(3).hasAttribute('width')).toBe(false);
    const load = async (element: HTMLImageElement) => {
      Object.defineProperty(element, 'naturalWidth', { value: 300 });
      Object.defineProperty(element, 'naturalHeight', { value: 500 });
      await act(async () => { element.dispatchEvent(new Event('load')) });
    };
    const learn = vi.mocked(learnFollowDims);
    learn.mockClear();
    await load(image(3));
    expect(learn).toHaveBeenCalledWith(3, null, 300, 500);
    expect([image(3).getAttribute('width'), image(3).getAttribute('height')]).toEqual(['300', '500']);
    await load(image(2));
    expect(learn).toHaveBeenCalledTimes(1);
    await act(async () => push({ view: view() }));
    await settle();
    await load(image(1));
    expect(learn).toHaveBeenCalledTimes(1);
  });

  it('图片缩略图失败后尝试对应原图代理，代理失败结束尝试', async () => {
    serve(feed([group(item(2, { media_kind: 'image', playable: true })),
      group(item(3, { media_kind: 'image', playable: true, media_items: [
        { index: 4, media_kind: 'image', thumb_url: 'https://example.test/image.jpg' },
      ] }))]));
    const host = await open(props({ view: view({ media: 'images' }) }));
    const image = (id: number) => host.querySelector<HTMLImageElement>(`[data-follow-item="${id}"] [data-media-pic] > img`);
    const fail = async (id: number) => {
      await act(async () => { image(id)!.dispatchEvent(new Event('error')) });
    };
    await fail(2);
    expect(image(2)?.getAttribute('src')).toBe('/follow-stream?id=2');
    await fail(3);
    expect(image(3)?.getAttribute('src')).toBe('/follow-stream?id=3&media=4');
    await fail(2);
    expect(image(2)).toBeNull();
    await fail(3);
    expect(image(3)).toBeNull();
  });

  it('视频缩略图失败不请求正片', async () => {
    serve(feed([group(item(1, { playable: true }))]));
    const host = await open(props());
    await act(async () => {
      host.querySelector('[data-media-pic] > img')!.dispatchEvent(new Event('error'));
    });
    expect(host.querySelector('[data-media-pic] > img')).toBeNull();
  });

  it('「仅显示图片」只在图片视图出现，点下交给壳存偏好', async () => {
    serve(feed([group(item(1)), group(item(2, { media_kind: 'image' }))]));
    const given = props();
    const host = await open(given);
    expect(host.querySelector('[data-follow-images-only]')).toBeNull();
    await act(async () => push({ view: view({ media: 'images' }) }));
    await settle();
    const toggle = host.querySelector('[data-follow-images-only]');
    expect(toggle?.getAttribute('aria-pressed')).toBe('false');
    await click(toggle);
    expect(given.actions.setImagesOnly).toHaveBeenCalledWith(true);
    await act(async () => push({ imagesOnly: true }));
    expect(host.querySelector('[data-follow-images-only]')?.getAttribute('aria-pressed')).toBe('true');
  });
});
