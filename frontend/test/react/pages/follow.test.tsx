/* 关注页与播放列表页的路由元素（`src/react/router/pages/follow.tsx`）：页面组匹配到这两条时挂上，按页面打开序号、
 * 壳的重开次数（`@peach/shell` 的 `pageOpens`）与覆盖层决定什么时候按地址打开那一页，画进 `#stats`。
 *
 * 关注页不按代次挂 key：每次打开先交壳按地址读筛选（`follow.refresh`），列表还画着就由壳就地推，
 * 没画着才整页打开。这里的壳替身照真壳的判据推：`#stats` 里画着关注页就把地址上的种子与一个新代次推过去。
 * 页面自己怎么取数、怎么画由 `browse-routes.test.tsx` 与各页的用例管。历史对象与壳状态都是模块级的，
 * 每条用例重新装载。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

import type { FollowFeedActions, FollowFeedHelpers, FollowFeedProps } from '../../../src/react/follow-feed/follow-feed';
import type { ShellActions } from '../../../src/react/router/shell-actions';

const REACT_IMPORT_TIMEOUT_MS = 30_000;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => { await import('../../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

async function load(path = '/') {
  vi.resetModules();
  window.history.replaceState(null, '', path);
  const [history, router, routes, shell] = await Promise.all([
    import('../../../src/history'), import('../../../src/react/router/router'),
    import('../../../src/react/router/managed-routes'), import('../../../src/shell'),
  ]);
  // 壳的内存状态是活绑定：整份模块留着读。
  return { ...history, ...router, ...routes, shell };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const FOLLOW_SKELETON = '<div class="follow" data-skeleton="follow"><h2 class="pagetitle">关注</h2></div>';

const helpers: FollowFeedHelpers = {
  workMark: () => '', tagLabel: (tag) => tag, wireDrag: () => {}, wireScroller: () => {},
  listSkeletonHtml: () => '<div data-test-skeleton>骨架</div>', jobProgress: () => {},
};
const feedActions: FollowFeedActions = {
  route: () => {}, shuffle: () => {}, loaded: () => {}, openDetail: () => {}, openAuthor: () => {}, openManage: () => {}, toggleSelection: () => {},
  setImagesOnly: () => {}, setPhotoLayout: () => {}, canFlip: () => false, toast: () => {}, failure: () => {}, checkReport: () => {},
};

/** 壳交进来的那一组，外加一份按先后记下的打开过程。关注页那几步照真壳的判据做。 */
function shellActions(r: Loaded) {
  const log: string[] = [];
  const stats = () => document.getElementById('stats')!;
  const followShown = () => r.managedEntry(stats())?.path === '/follow';
  const props = (): FollowFeedProps => ({
    view: { status: '', media: 'videos', author: '', provider: '', work: '', tags: [], durMin: 0, durMax: 0, sort: 'new', dir: 'desc', seed: 7 },
    seed: r.shell.followDiscoverySeed, revision: r.shell.followRevision, selectMode: false, selected: new Set(),
    photoSize: 'small', photoLayout: 'masonry', imagesOnly: false, helpers, actions: feedActions,
  });
  const actions: ShellActions = {
    openItem: vi.fn(), openEntity: vi.fn(), openTag: vi.fn(), openTasteSignal: vi.fn(), navigate: vi.fn(),
    openManage: vi.fn(), managePath: vi.fn(() => ''), openFollow: vi.fn(), receipt: vi.fn(), toast: vi.fn(),
    failure: vi.fn(), revealSource: vi.fn(async () => ''), reopenTutorial: vi.fn(async () => {}),
    requestConfigurationSection: vi.fn(), routeReview: vi.fn(),
    routeFollowManage: vi.fn(), saveFollowPreference: vi.fn(), srcBadge: () => '', routeIndex: vi.fn(),
    savePeopleLayout: vi.fn(), exitSelectMode: vi.fn(), personAvatar: vi.fn(() => ({ html: '', face: '' })),
    authorAvatar: vi.fn(() => ''), showIndexTags: vi.fn(), openFollowAuthor: vi.fn(), openFollowTag: vi.fn(),
    openPlaylist: vi.fn(), canFlip: () => true,
    surfaceChanged: vi.fn((kind: string, path: string) => { log.push(`surface ${kind} ${path}`) }),
    clearSearch: vi.fn(), openImmerse: vi.fn(), openOverlay: vi.fn(), closeStage: vi.fn(),
    grid: { helpers: {}, actions: {} } as ShellActions['grid'],
    follow: {
      refresh: vi.fn(() => {
        log.push('refresh');
        if (!followShown()) return false;
        r.shell.writeShell({ followRevision: r.shell.followRevision + 1 });
        r.updateManagedRoute(stats(), { seed: r.shell.followDiscoverySeed, revision: r.shell.followRevision });
        return true;
      }),
      skeleton: vi.fn(() => FOLLOW_SKELETON),
      props: vi.fn(props),
      ground: vi.fn(() => { log.push('ground') }),
    },
  };
  return { actions, log };
}

type Reply = { ok: boolean; status: number; json(): Promise<unknown> };
const reply = (body: unknown): Reply => ({ ok: true, status: 200, json: async () => body });

const followPage = {
  groups: [1, 2].map((id) => ({
    primary: { id, title: `更新 ${id}`, status: 'new', provider: 'kemono', provider_label: 'Kemono', source_id: 1,
      published_at: '2026-09-10T08:00:00Z', media_kind: 'video', playable: true, thumb_url: `/thumb/${id}`, tags: [] },
    variants: [], duplicates: [], stack: null,
  })),
  counts: { new: 2, saved: 0, ignored: 0 }, sources: [{ id: 1, provider: 'kemono', provider_label: 'Kemono',
    author_key: 'name:kou', last_status: 'ok' }], author_aliases: [], offset: 0, has_more: false,
  facets: { authors: ['name:kou'], providers: ['kemono'], tags: [], works: [] },
};
const playlists = {
  items: [{
    id: 1, name: '周末连看', source_kind: 'manual', source_seed_asset_id: null, current_asset_id: null,
    created_at: '2026-09-01 10:00:00', updated_at: '2026-09-01 10:00:00', item_count: 2,
    preview_asset_id: 11, preview_ids: [11, 12], faces: [],
  }],
};

/** 本地取数替身：关注列表、凭据与播放列表各回一份，记下每一次请求。 */
function serve() {
  const fetched = vi.fn((input: string) => {
    const path = new URL(String(input), 'http://peach.test').pathname;
    const body = path === '/api/follow/credentials' ? { providers: [] } : path === '/api/playlists' ? playlists : followPage;
    return Promise.resolve(reply(body));
  });
  vi.stubGlobal('fetch', fetched);
  return (path: string) => fetched.mock.calls.filter(([input]) => new URL(String(input), 'http://peach.test').pathname === path).length;
}

function statsSurface(skeleton = FOLLOW_SKELETON) {
  const stats = document.createElement('section');
  stats.id = 'stats';
  stats.innerHTML = skeleton;
  document.body.append(stats);
  return stats;
}

async function until(ok: () => boolean, what: string): Promise<void> {
  for (let step = 0; step < 200; step += 1) {
    if (ok()) return;
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
  throw new Error(`一直没等到：${what}`);
}
async function settle() {
  for (let step = 0; step < 3; step += 1) {
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
}

/** 挂上路由根、接上页面包，再像壳那样开始路由：启动那一次把 `pageOpens` 写成 1。 */
async function boot(r: Loaded, actions: ShellActions) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot actions={actions} />) });
  unmounts.push(() => root.unmount());
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
  await act(async () => { r.shell.writeShell({ pageOpens: 1 }) });
  await settle();
}

/** 壳认领写地址、再要求按地址重开（`openRoutedPage`）。 */
async function openRouted(r: Loaded, path: string) {
  await act(async () => {
    r.shellNavigate(path);
    r.shell.writeShell({ pageOpens: r.shell.pageOpens + 1 });
  });
  await settle();
}

async function pop(r: Loaded, path: string, state: unknown = null) {
  await act(async () => {
    window.history.replaceState(state, '', path);
    window.dispatchEvent(new PopStateEvent('popstate', { state }));
  });
  await settle();
}

const page = (stats: Element) => stats.querySelector(':scope > .peach-react > *');
const opens = (actions: ShellActions) => vi.mocked(actions.surfaceChanged).mock.calls.length;
const FOLLOW_ITEM_STATE = { backgroundLocation: { pathname: '/follow', search: '' }, overlay: 'follow' };

it('关注元素：壳开始路由那一下按地址整页打开，先交壳读筛选，再报换面、铺骨架、取齐首屏', async () => {
  const r = await load('/follow');
  const stats = statsSurface();
  const calls = serve();
  const { actions, log } = shellActions(r);
  await boot(r, actions);
  await until(() => page(stats) !== null, '整页画上');
  expect(log).toEqual(['refresh', 'surface follow /follow']);
  expect(actions.follow!.skeleton).toHaveBeenCalledTimes(1);
  expect([calls('/api/follow'), calls('/api/follow/credentials')]).toEqual([1, 1]);
  expect(r.managedEntry(stats)).toMatchObject({ path: '/follow', props: { revision: 0 } });
});

it('关注元素：侧栏再进（壳重掷种子、要求重开）时列表还画着就就地推新种子与新代次，按新代次重取、不重挂', async () => {
  const r = await load('/follow');
  const stats = statsSurface();
  const calls = serve();
  const { actions } = shellActions(r);
  await boot(r, actions);
  await until(() => page(stats) !== null, '整页画上');
  const painted = page(stats);
  r.shell.writeShell({ followDiscoverySeed: 99, followScrollY: 0 });
  await openRouted(r, '/follow');
  await until(() => calls('/api/follow') === 2, '按新代次重取列表');
  expect(r.managedEntry(stats)?.props).toMatchObject({ seed: 99, revision: 1 });
  expect(page(stats), '就地推，不重挂').toBe(painted);
  expect(opens(actions), '不重新报换面').toBe(1);
  expect(calls('/api/follow/credentials'), '凭据不重取').toBe(1);
});

it('关注元素：后退回到关注页时列表已被收掉就整页打开，代次没变、首屏命中缓存不重取列表，照记下的位置滚回去', async () => {
  const r = await load('/follow');
  const stats = statsSurface();
  const calls = serve();
  const scrolled = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  const { actions } = shellActions(r);
  await boot(r, actions);
  await until(() => page(stats) !== null, '整页画上');
  r.shell.writeShell({ followScrollY: 640 });
  // 去别的页面：那一页认领表面时收掉关注页。
  await act(async () => { r.shellNavigate('/immerse') });
  await settle();
  expect(r.managedEntry(stats)?.path, '元素卸下时不收页面').toBe('/follow');
  await act(async () => { r.releaseManagedRoute(stats) });
  scrolled.mockClear();
  await pop(r, '/follow');
  await until(() => page(stats) !== null, '整页重新画上');
  expect(opens(actions), '整页打开一次').toBe(2);
  expect(calls('/api/follow'), '代次没变，列表不重取').toBe(1);
  expect(r.managedEntry(stats)?.props).toMatchObject({ revision: 0 });
  expect(scrolled).toHaveBeenLastCalledWith({ top: 640, behavior: 'instant' });
});

it('关注元素：进详情、关掉回列表都由壳认领写地址，列表不重挂、不重取', async () => {
  const r = await load('/follow');
  const stats = statsSurface();
  const calls = serve();
  const { actions, log } = shellActions(r);
  await boot(r, actions);
  await until(() => page(stats) !== null, '整页画上');
  const painted = page(stats);
  const before = log.length;
  await act(async () => { r.shellNavigate('/posts/2', { state: FOLLOW_ITEM_STATE }) });
  await settle();
  await act(async () => { r.shellNavigate('/follow', { replace: true }) });
  await settle();
  expect(log.slice(before), '元素什么都不做').toEqual([]);
  expect(page(stats)).toBe(painted);
  expect(calls('/api/follow')).toBe(1);
});

it('关注元素：深链直接进关注详情不挂列表，只交壳让出 `#stats`；关掉详情（壳认领写地址、要求重开）时才整页打开', async () => {
  const r = await load('/posts/2');
  const stats = statsSurface('');
  const calls = serve();
  const { actions, log } = shellActions(r);
  await boot(r, actions);
  expect(log, '深链详情下面不挂列表').toEqual(['ground']);
  expect(calls('/api/follow')).toBe(0);
  await openRouted(r, '/follow');
  await until(() => page(stats) !== null, '关掉详情后整页画上');
  expect(log).toEqual(['ground', 'refresh', 'surface follow /follow']);
});

it('关注元素：后退前进落在压着列表的详情上、列表没画着时交壳让出 `#stats`；关掉回列表（认领写地址）整页打开一次', async () => {
  const r = await load('/immerse');
  const stats = statsSurface('');
  serve();
  const { actions, log } = shellActions(r);
  await boot(r, actions);
  await pop(r, '/posts/2', { usr: FOLLOW_ITEM_STATE, key: 'x', idx: 1 });
  expect(log, '被详情压着挂上不打开').toEqual(['ground']);
  await act(async () => {
    r.shellNavigate('/follow');
    r.shell.writeShell({ pageOpens: r.shell.pageOpens + 1 });
  });
  await until(() => page(stats) !== null, '关掉详情后整页画上');
  expect(log, '认领写地址与要求重开合成一次').toEqual(['ground', 'refresh', 'surface follow /follow']);
});

it('播放列表元素：按地址整页打开；停在这一页要求重读时就地推新代次重取，壳要求重开时整页重开', async () => {
  const r = await load('/playlists');
  const stats = statsSurface('');
  const calls = serve();
  const { actions, log } = shellActions(r);
  await boot(r, actions);
  await until(() => page(stats) !== null, '整页画上');
  expect(log).toEqual(['surface playlists /playlists']);
  expect(stats.querySelector('[data-skeleton]'), '骨架取齐之后撤下').toBeNull();
  const painted = page(stats);
  await act(async () => { r.shell.writeShell({ playlistsRevision: r.shell.playlistsRevision + 1 }) });
  await until(() => calls('/api/playlists') === 2, '按新代次重取');
  expect(page(stats), '就地推，不重挂').toBe(painted);
  expect(r.managedEntry(stats)?.props).toEqual({ revision: 1 });
  expect(opens(actions)).toBe(1);
  await openRouted(r, '/playlists');
  await until(() => calls('/api/playlists') === 3 && page(stats) !== null && page(stats) !== painted, '整页重开');
  expect(opens(actions), '再点一次侧栏整页重开').toBe(2);
});
