/* 播放列表页与关注页由路由树画进 `#stats`（`BROWSE_ROUTES`）：骨架留到首屏取齐，换成整页落在同一批变化里
 * （关注页的骨架经 `revealSkeleton` 抬成淡出层）；停在这一页时经 `updateManagedRoute` 推刷新代次或筛选，
 * 页面只重取、不重挂，代次不变；页面里的去处与回执接到壳的那一组上；管理区跳到这两条路径时交壳写地址。
 *
 * 页内取数、写操作与卡面由 `playlists.test.tsx`、`follow-feed.test.tsx` 管，什么时候打开由
 * `pages/follow.test.tsx` 管，这里看的是路由树这一层。 */
import { act, type ReactElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

import type { PlaylistsProps } from '../../src/react/bundle';
import type {
  FollowFeedActions, FollowFeedHelpers, FollowFeedProps, FollowView,
} from '../../src/react/follow-feed/follow-feed';
import type { ShellActions } from '../../src/react/router/shell-actions';
import { revealSkeleton } from '../../src/ui-kit';

// 首次导入会编译路由表带进来的整棵页面子树，编译等待使用独立的有限窗口；之后每条用例重新装载只重跑模块。
const REACT_IMPORT_TIMEOUT_MS = 30_000;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => { await import('../../src/react/router/router') }, REACT_IMPORT_TIMEOUT_MS);

async function load(path = '/') {
  vi.resetModules();
  window.history.replaceState(null, '', path);
  const [history, router, routes] = await Promise.all([
    import('../../src/history'), import('../../src/react/router/router'), import('../../src/react/router/managed-routes'),
  ]);
  return { ...history, ...router, ...routes };
}
type Loaded = Awaited<ReturnType<typeof load>>;

const unmounts: Array<() => void> = [];
afterEach(async () => {
  for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

function shellActions(): ShellActions {
  return {
    openItem: vi.fn(), openEntity: vi.fn(), openTag: vi.fn(), openTasteSignal: vi.fn(), navigate: vi.fn(),
    openManage: vi.fn(), managePath: vi.fn(() => ''), openFollow: vi.fn(), receipt: vi.fn(), toast: vi.fn(),
    failure: vi.fn(), revealSource: vi.fn(async () => ''), reopenTutorial: vi.fn(async () => {}),
    requestConfigurationSection: vi.fn(), routeReview: vi.fn(),
    routeFollowManage: vi.fn(), saveFollowPreference: vi.fn(), srcBadge: () => '', routeIndex: vi.fn(),
    savePeopleLayout: vi.fn(), exitSelectMode: vi.fn(), personAvatar: vi.fn(() => ({ html: '', face: '' })),
    authorAvatar: vi.fn(() => ''), showIndexTags: vi.fn(), openFollowAuthor: vi.fn(), openFollowTag: vi.fn(),
    openPlaylist: vi.fn(), canFlip: vi.fn(() => true),
    surfaceChanged: vi.fn(), clearSearch: vi.fn(), openImmerse: vi.fn(), openOverlay: vi.fn(), closeStage: vi.fn(), grid: {} as ShellActions['grid'],
  };
}

async function mount(r: Loaded) {
  const root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<r.RouterRoot actions={shellActions()} />) });
  unmounts.push(() => root.unmount());
  r.connectManagedRoutes(Promise.resolve(r.prefetchManagedRoute));
}

type Reply = { ok: boolean; status: number; json(): Promise<unknown> };
const reply = (body: unknown): Reply => ({ ok: true, status: 200, json: async () => body });

/** 一行播放列表；名字用来认出画的是哪一次取回来的。 */
const playlist = (name: string) => ({
  items: [{
    id: 1, name, source_kind: 'manual', source_seed_asset_id: null, current_asset_id: null,
    created_at: '2026-09-01 10:00:00', updated_at: '2026-09-01 10:00:00', item_count: 2,
    preview_asset_id: 11, preview_ids: [11, 12], faces: [],
  }],
});

/** 播放列表页的取数：`/api/playlists` 可以手动兑现（首屏等的就是它），回的名字可以中途换掉。 */
function playlistsFetch({ deferred = false } = {}) {
  const pending: Array<() => void> = [];
  let name = '周末连看';
  const fetched = vi.fn((input: string, init?: RequestInit) => {
    const body = playlist(name);
    if (!deferred) return Promise.resolve(reply(body));
    return new Promise<Reply>((resolve, reject) => {
      pending.push(() => resolve(reply(body)));
      init?.signal?.addEventListener('abort', () => { reject(new DOMException('已中止', 'AbortError')) });
    });
  });
  vi.stubGlobal('fetch', fetched);
  const calls = (path: string) => fetched.mock.calls
    .map(([input]) => new URL(String(input), 'http://peach.test')).filter((url) => url.pathname === path);
  return {
    fetched, calls, rename: (next: string) => { name = next },
    resolve: () => { for (const settle of pending.splice(0)) settle() },
  };
}

/** 管理区正文的容器，里面是壳铺好的骨架。 */
function statsSurface(skeleton = '<div class="geist-skeleton" data-skeleton="playlists">正在读取播放列表</div>') {
  const stats = document.createElement('section');
  stats.id = 'stats';
  stats.innerHTML = skeleton;
  document.body.append(stats);
  return { stats, skeleton: stats.firstElementChild! };
}

async function until(ok: () => boolean, what: string): Promise<void> {
  for (let step = 0; step < 200; step += 1) {
    if (ok()) return;
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
  throw new Error(`一直没等到：${what}`);
}

/** 页面画出来的那一块（宿主里的第一个元素）：重挂之后就换成另一个。 */
const painted = (stats: Element) => stats.querySelector(':scope > .peach-react > *');

it('冷启动深链播放列表页：骨架留到首屏取齐，换成整页在同一批变化里', async () => {
  const r = await load('/playlists');
  const { stats, skeleton } = statsSurface();
  await mount(r);
  const fetch = playlistsFetch({ deferred: true });
  const opened = r.openManagedRoute('/playlists', { revision: 0 }, { container: stats, isCurrent: () => true });
  await until(() => fetch.calls('/api/playlists').length > 0, '首屏取数发出去');
  expect(stats.firstElementChild, '取齐之前骨架原样留着').toBe(skeleton);
  const batches: Array<{ skeleton: boolean; painted: boolean }> = [];
  const observer = new MutationObserver(() => {
    batches.push({ skeleton: stats.contains(skeleton), painted: Boolean(stats.textContent?.includes('周末连看')) });
  });
  observer.observe(stats, { childList: true, subtree: true });
  unmounts.push(() => observer.disconnect());
  fetch.resolve();
  await act(async () => { expect(await opened).toBe(true) });
  expect(batches[0], '骨架撤下的那一批变化里整页已经画好').toEqual({ skeleton: false, painted: true });
  expect(r.managedEntry(stats)).toMatchObject({ path: '/playlists', props: { revision: 0 } });
});

/* 顶栏「换一批」落在这一页上：壳推一个新的刷新代次，页面只重取那一份列表，卡片不重挂。 */
it('播放列表页就地推刷新代次：代次不变、不重挂，只重取一次', async () => {
  const r = await load('/playlists');
  const { stats } = statsSurface();
  await mount(r);
  const fetch = playlistsFetch();
  await act(async () => {
    await r.openManagedRoute('/playlists', { revision: 0 }, { container: stats, isCurrent: () => true });
  });
  const page = painted(stats);
  const revision = r.managedEntry(stats)?.revision;
  expect(fetch.calls('/api/playlists')).toHaveLength(1);
  fetch.rename('换过名字');
  await act(async () => { r.updateManagedRoute(stats, { revision: 1 }) });
  await until(() => Boolean(stats.textContent?.includes('换过名字')), '按新代次重取回来的那一份画上');
  expect(fetch.calls('/api/playlists'), '只重取一次').toHaveLength(2);
  expect(fetch.fetched).toHaveBeenCalledTimes(2);
  expect(painted(stats), '推新代次不许重挂').toBe(page);
  expect(stats.querySelector('[data-skeleton]'), '就地重取不铺骨架').toBeNull();
  expect(r.managedEntry(stats)?.revision).toBe(revision);
  expect(r.managedEntry(stats)?.props).toEqual({ revision: 1 });
});

it('播放列表页：进舞台、去资料页、翻页门槛与回执都接到壳的那一组上', async () => {
  const r = await load();
  const actions = shellActions();
  const props = (r.BROWSE_ROUTES['/playlists'].page({ revision: 3 }, actions, vi.fn()) as ReactElement<PlaylistsProps>).props;
  expect([props.openPlaylist, props.openEntity, props.canFlip]).toEqual([actions.openPlaylist, actions.openEntity, actions.canFlip]);
  expect(props.revision).toBe(3);
  const undo = async () => {};
  props.toast('已删除播放列表', { undo });
  props.toast('已重命名');
  expect(vi.mocked(actions.receipt).mock.calls).toEqual([['已删除播放列表', { undo }], ['已重命名', {}]]);
  expect(actions.toast).not.toHaveBeenCalled();
});

it('播放列表页与关注页的路径不走 React Router：交壳写地址再打开', async () => {
  const r = await load('/');
  const actions = shellActions();
  const navigate = vi.fn();
  r.managedGo('/playlists', actions, navigate);
  r.managedGo('/follow', actions, navigate);
  expect(navigate).not.toHaveBeenCalled();
  expect(vi.mocked(actions.navigate).mock.calls).toEqual([['/playlists'], ['/follow']]);
  for (const path of ['/playlists', '/follow']) {
    expect(r.ROUTED_PATHS).toContain(path);
    expect(r.isManagedPath(path)).toBe(false);
  }
});

/* ── 关注页 ── */

const VIEW: FollowView = {
  status: '', media: 'videos', author: '', provider: '', work: '', tags: [], durMin: 0, durMax: 0, sort: 'new', dir: 'desc', seed: 7,
};

function followHelpers(): FollowFeedHelpers {
  return {
    workMark: () => '', tagLabel: (tag) => tag, wireDrag: vi.fn(), wireScroller: vi.fn(),
    listSkeletonHtml: () => '<div data-test-skeleton>骨架</div>', jobProgress: vi.fn(),
  };
}
function followActions(): FollowFeedActions {
  return {
    route: vi.fn(), shuffle: vi.fn(), loaded: vi.fn(), openDetail: vi.fn(), openAuthor: vi.fn(), openManage: vi.fn(), toggleSelection: vi.fn(),
    setImagesOnly: vi.fn(), setPhotoLayout: vi.fn(), canFlip: () => false, toast: vi.fn(), failure: vi.fn(), checkReport: vi.fn(),
  };
}
const followOpen = (patch: Partial<FollowFeedProps> = {}): FollowFeedProps => ({
  view: VIEW, seed: 5, revision: 0, selectMode: false, selected: new Set(), photoSize: 'small', photoLayout: 'masonry',
  imagesOnly: false, helpers: followHelpers(), actions: followActions(), ...patch,
});

/** 关注列表的一页：标题带着状态档，认得出画的是哪一次取回来的。 */
const followPage = (status: string) => ({
  groups: [1, 2, 3].map((id) => ({
    primary: { id, title: `更新 ${id} ${status || '全部'}`, status: 'new', provider: 'kemono', provider_label: 'Kemono',
      source_id: 1, published_at: '2026-09-10T08:00:00Z', media_kind: 'video', playable: true, thumb_url: `/thumb/${id}`,
      tags: [] },
    variants: [], duplicates: [], stack: null,
  })),
  counts: { new: 3, saved: 0, ignored: 0 }, sources: [{ id: 1, provider: 'kemono', provider_label: 'Kemono',
    author_key: 'name:kou', last_status: 'ok' }], author_aliases: [], offset: 0, has_more: false,
  facets: { authors: ['name:kou'], providers: ['kemono'], tags: [], works: [] },
});

/** 关注页的取数：列表与凭据两条，可以手动兑现（首屏两趟都等齐才画）。 */
function followFetch({ deferred = false } = {}) {
  const pending: Array<() => void> = [];
  const fetched = vi.fn((input: string, init?: RequestInit) => {
    const url = new URL(String(input), 'http://peach.test');
    const body = url.pathname === '/api/follow/credentials' ? { providers: [] } : followPage(url.searchParams.get('status') || '');
    if (!deferred) return Promise.resolve(reply(body));
    return new Promise<Reply>((resolve, reject) => {
      pending.push(() => resolve(reply(body)));
      init?.signal?.addEventListener('abort', () => { reject(new DOMException('已中止', 'AbortError')) });
    });
  });
  vi.stubGlobal('fetch', fetched);
  const calls = (path: string) => fetched.mock.calls
    .map(([input]) => new URL(String(input), 'http://peach.test')).filter((url) => url.pathname === path);
  return { fetched, calls, resolve: () => { for (const settle of pending.splice(0)) settle() } };
}

/** 同壳里的 `revealRoutedPage`：骨架交给 `revealSkeleton` 抬成淡出层，`write` 只放进空宿主。 */
function revealRoutedPage(container: Element): HTMLElement {
  const host = document.createElement('div');
  host.className = 'peach-react';
  revealSkeleton(container, () => { container.textContent = ''; container.append(host) });
  return host;
}

const FOLLOW_SKELETON = '<div class="follow" data-skeleton="follow"><h2 class="pagetitle">关注</h2>'
  + '<span class="countskeleton"></span></div>';

it('冷启动深链关注页：骨架淡出与整页在同一批变化里交接，中间没有空帧', async () => {
  const r = await load('/follow');
  const { stats, skeleton } = statsSurface(FOLLOW_SKELETON);
  await mount(r);
  const fetch = followFetch({ deferred: true });
  const opened = r.openManagedRoute('/follow', followOpen(), { container: stats, isCurrent: () => true, place: revealRoutedPage });
  await until(() => fetch.calls('/api/follow').length > 0 && fetch.calls('/api/follow/credentials').length > 0, '首屏两趟发出去');
  expect(stats.firstElementChild, '取齐之前骨架原样留着').toBe(skeleton);
  const batches: Array<{ bare: boolean; fading: boolean; painted: boolean; revealing: boolean }> = [];
  const observer = new MutationObserver(() => {
    batches.push({
      bare: Boolean(stats.querySelector(':scope > [data-skeleton]')),
      fading: Boolean(stats.querySelector(':scope > .skelfade > [data-skeleton]')),
      painted: Boolean(stats.querySelector('.peach-react [data-follow-feed]')),
      revealing: stats.classList.contains('skelreveal') && stats.classList.contains('revealing'),
    });
  });
  observer.observe(stats, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  unmounts.push(() => observer.disconnect());
  fetch.resolve();
  await act(async () => { expect(await opened).toBe(true) });
  expect(batches[0], '撤下骨架的那一批里，骨架已在淡出层、整页已经画好').toEqual(
    { bare: false, fading: true, painted: true, revealing: true });
  expect(batches.every((batch) => batch.bare || batch.fading || batch.painted), '任何一批都不是空的').toBe(true);
  expect(stats.querySelector('.skelfade')?.firstElementChild, '淡出层里是原来那张骨架').toBe(skeleton);
  expect(r.managedEntry(stats)?.host.parentElement).toBe(stats);
  // jsdom 不发 transitionend，淡出层靠 `revealSkeleton` 的兜底定时器收掉。
  await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 1100) }) });
  expect(stats.querySelector('.skelfade')).toBeNull();
  expect(stats.classList.contains('skelreveal')).toBe(false);
  expect(stats.querySelector(':scope > .peach-react [data-follow-feed]')).not.toBeNull();
});

/* 页内换筛选、侧栏标签、前进后退落在这一页上：壳推新的 `view`（重进还带新种子与代次），页面只按新键重取
   列表，凭据不重取，页头与两排不重挂。 */
it('关注页就地推筛选与代次：代次不变、不重挂，只按新键重取列表', async () => {
  const r = await load('/follow');
  const { stats } = statsSurface(FOLLOW_SKELETON);
  await mount(r);
  const fetch = followFetch();
  const open = followOpen();
  await act(async () => {
    await r.openManagedRoute('/follow', open, { container: stats, isCurrent: () => true, place: revealRoutedPage });
  });
  await until(() => Boolean(stats.textContent?.includes('更新 1 全部')), '首屏列表画上');
  const page = stats.querySelector('.peach-react [data-follow-feed]');
  const revision = r.managedEntry(stats)?.revision;
  expect([fetch.calls('/api/follow').length, fetch.calls('/api/follow/credentials').length]).toEqual([1, 1]);
  await act(async () => { r.updateManagedRoute(stats, { view: { ...VIEW, status: 'new' }, seed: 9, revision: 1 }) });
  await until(() => Boolean(stats.textContent?.includes('更新 1 new')), '按新键重取回来的那一份画上');
  expect(fetch.calls('/api/follow').map((url) => url.searchParams.get('status')), '只按新键重取一次').toEqual([null, 'new']);
  expect(fetch.calls('/api/follow/credentials'), '凭据不重取').toHaveLength(1);
  expect(stats.querySelector('.peach-react [data-follow-feed]'), '推新筛选不许重挂').toBe(page);
  expect(r.managedEntry(stats)?.revision).toBe(revision);
  expect(r.managedEntry(stats)?.props).toMatchObject({ view: { status: 'new' }, seed: 9, revision: 1 });
  expect((r.managedEntry(stats)!.props as FollowFeedProps).actions, '壳那一份动作原样留着').toBe(open.actions);
});

it('关注页：助手与动作是壳那一份，原样交给页面', async () => {
  const r = await load();
  const open = followOpen({ revision: 4 });
  const props = (r.BROWSE_ROUTES['/follow'].page(open, shellActions(), vi.fn()) as ReactElement<FollowFeedProps>).props;
  expect(props).toEqual(open);
  expect([props.helpers, props.actions]).toEqual([open.helpers, open.actions]);
  expect(props.helpers).toBe(open.helpers);
  expect(props.actions).toBe(open.actions);
});
