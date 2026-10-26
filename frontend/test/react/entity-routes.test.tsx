/* 资料页由路由树画进 `#index`（`ENTITY_ROUTES`，按 `/performers/*` 这样的模式登记）：框架由资料页元素排、经
 * `place` 在首屏取齐那一刻换进容器，骨架与整页落在同一批变化里；壳之后的开关经 `updateManagedRoute`
 * 合进去，页面按新键重取、不重挂；名字带斜杠、百分号、空格或日文时，地址两种写法读出来的名字、标题、
 * 链接与首屏请求都对得上；页内跳到资料页交壳写地址，不经 React Router 的 `navigate`。
 *
 * 元素什么时候打开、怎么从地址解出这一位由 `pages/index-entity.test.tsx` 管。
 *
 * 页内取数、写操作与各块怎么画由 `entity-page.test.tsx` 与各块自己的用例管，这里看的是路由树这一层。 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';

// 用正式模块：链接怎么拼、地址段对哪一类实体，与浏览器里那一份是同一份。
import { entityPath } from '../../src/core';
import { routeMetaOf } from '../../src/history/route-meta';
import type { EntityPageProps } from '../../src/react/entity-page/entity-page';
import { entityTarget } from '../../src/react/router/pages/index-entity';
import type { ShellActions } from '../../src/react/router/shell-actions';

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
    openPlaylist: vi.fn(), canFlip: () => true,
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

/** 资料页的取数：`/api/entity` 可以手动兑现（首屏等的就是它），其余当场回。 */
function entityFetch({ deferred = false } = {}) {
  const pending: Array<() => void> = [];
  const fetched = vi.fn((input: string, init?: RequestInit) => {
    const url = new URL(String(input), 'http://peach.test');
    if (url.pathname === '/api/entity') {
      const body = { id: 7001, kind: 'performer', canonical_name: url.searchParams.get('name'), aliases: [],
        user_aliases: [], asset_count: 2, tags: [], related_performers: [], feed: { following: false } };
      if (!deferred) return Promise.resolve(reply(body));
      return new Promise<Reply>((resolve, reject) => {
        pending.push(() => resolve(reply(body)));
        init?.signal?.addEventListener('abort', () => { reject(new DOMException('已中止', 'AbortError')) });
      });
    }
    if (url.pathname === '/api/items') {
      return Promise.resolve(reply({ items: [1, 2].map((id) => ({ id, name: `作品 ${id}`, has_thumb: true })),
        total: 2, has_more: false }));
    }
    if (url.pathname === '/api/photos') {
      return Promise.resolve(reply({ total: 0, sample_total: 0, sets: [], has_more: false, seed: '', items: [] }));
    }
    if (url.pathname === '/api/feeds/discoveries') return Promise.resolve(reply({ items: [] }));
    return Promise.resolve(reply({ status: 'idle' }));
  });
  vi.stubGlobal('fetch', fetched);
  const calls = (path: string) => fetched.mock.calls
    .map(([input]) => new URL(String(input), 'http://peach.test'))
    .filter((url) => url.pathname === path);
  return { fetched, calls, resolve: () => { for (const settle of pending.splice(0)) settle() } };
}

/** 壳在 `#index` 里铺好的骨架。 */
function indexSurface() {
  const index = document.createElement('section');
  index.id = 'index';
  index.innerHTML = '<div class="geist-skeleton" data-skeleton="entity">正在读取</div>';
  document.body.append(index);
  return { index, skeleton: index.firstElementChild! };
}

/** 资料页元素排的四块框架（`pages/index-entity.tsx` 的 `openEntity`），连同交给 `openManagedRoute` 的 `place`。 */
function entityFrame() {
  const frame = document.createElement('template');
  frame.innerHTML = `<div data-entity-hero><div class="peach-react"></div></div>
    <div data-entity-filter><div class="peach-react"></div></div>
    <section class="feednew" data-feed-new aria-label="未入库的新作" hidden></section>
    <div data-entity-body><div class="peach-react"></div></div>`;
  const parts = [...frame.content.childNodes];
  const hero = frame.content.querySelector<HTMLElement>('[data-entity-hero]')!;
  const hosts = {
    filter: frame.content.querySelector<HTMLElement>('[data-entity-filter]>.peach-react')!,
    feed: frame.content.querySelector<HTMLElement>('[data-feed-new]')!,
    body: frame.content.querySelector<HTMLElement>('[data-entity-body]>.peach-react')!,
  };
  const place = (container: Element) => { container.replaceChildren(...parts); return hero.firstElementChild as HTMLElement };
  return { hero, hosts, place };
}

function entityProps(name: string, hosts: EntityPageProps['hosts']): EntityPageProps {
  return {
    kind: 'performer', name, filters: {}, media: { media: 'videos', set: 0 }, jav: false, seed: '42',
    revision: 0, feedRevision: 0, photoSize: 'small', photoLayout: 'masonry', followImagesOnly: false,
    javLayout: 'small', javLayouts: [['small', '小图', 'layout-grid']], photoLayouts: [['masonry', '瀑布流', 'columns-2']],
    states: [{ k: '', label: '全部' }], peopleLayout: 'big', hosts,
    layout: { active: false, size: 'small', portrait: false, javImage: 'cover' },
    selectMode: false, selected: new Set(), seekSeconds: 10, wireDrag: vi.fn(),
    skeletonHtml: () => '<div data-test-skeleton></div>', groupCollapse: true, canLoadMore: () => true,
    card: {
      helpers: {
        badgeHtml: () => '', titleHtml: (_it, raw) => raw, displayName: (_it, raw) => raw, tagLabel: (tag) => tag,
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
      feedRowHtml: () => '<div class="feednewrow"></div>', receipt: vi.fn(), failure: vi.fn(),
      aliasForm: vi.fn(async () => {}), sourceToolsHtml: () => '', wireSourceTools: vi.fn(), comboItems: () => [],
      sortKeys: vi.fn(() => [{ key: 'new', label: '入库时间', pressed: true, dir: '' as const, ariaLabel: '' }]),
    },
    actions: {
      route: vi.fn(), toggleTag: vi.fn(), clearFilter: vi.fn(), clearAll: vi.fn(), setSort: vi.fn(),
      reshuffleVideos: vi.fn(() => '77'), setJavLayout: vi.fn(), setPhotoLayout: vi.fn(), openEntity: vi.fn(),
      javContext: vi.fn(), painted: vi.fn(), missing: vi.fn(), avatarChanged: vi.fn(), openFollowAuthor: vi.fn(),
    },
  };
}

async function until(ok: () => boolean, what: string): Promise<void> {
  for (let step = 0; step < 200; step += 1) {
    if (ok()) return;
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0) }) });
  }
  throw new Error(`一直没等到：${what}`);
}

const NAME = '篠田ゆう';
const heroPainted = (index: Element) => index.querySelector('[data-entity-hero] > .peach-react > *');

it('冷启动深链资料页：骨架留到首屏取齐，元素排的框架与整页在同一批变化里换上', async () => {
  const r = await load(entityPath('performer', NAME));
  const { index, skeleton } = indexSurface();
  await mount(r);
  const fetch = entityFetch({ deferred: true });
  const { hosts, place } = entityFrame();
  const opened = r.openManagedRoute('/performers/*', entityProps(NAME, hosts), { container: index, isCurrent: () => true, place });
  await until(() => fetch.calls('/api/entity').length > 0, '首屏取数发出去');
  expect(index.firstElementChild, '取齐之前骨架原样留着').toBe(skeleton);
  const batches: Array<{ skeleton: boolean; painted: boolean }> = [];
  const observer = new MutationObserver(() => {
    batches.push({ skeleton: index.contains(skeleton), painted: Boolean(heroPainted(index)) });
  });
  observer.observe(index, { childList: true, subtree: true });
  unmounts.push(() => observer.disconnect());
  fetch.resolve();
  await act(async () => { expect(await opened).toBe(true) });
  expect(batches[0], '骨架撤下的那一批变化里资料卡已经画好').toEqual({ skeleton: false, painted: true });
  expect([...index.children].map((el) => el.getAttribute('data-entity-hero') ?? el.getAttribute('data-entity-filter')
    ?? el.getAttribute('data-feed-new') ?? el.getAttribute('data-entity-body'))).toEqual(['', '', '', '']);
  expect(hosts.filter.childElementCount, '浮层经 portal 画进元素排的那一块').toBeGreaterThan(0);
  expect(hosts.body.querySelectorAll('[data-media-card]')).toHaveLength(2);
  expect(r.managedEntry(index)).toMatchObject({ path: '/performers/*', host: index.querySelector('[data-entity-hero] > .peach-react') });
  act(() => { r.releaseManagedRoute(index) });
  expect(heroPainted(index), '收起时页面连同资料卡那一层宿主一起撤掉').toBeNull();
  expect(index.querySelector('[data-entity-hero] > .peach-react')).toBeNull();
});

/* 壳换筛选（`routeEntityPage`）与切版式、选择键（`pushEntityPage`）都只合进画着的这一页：代次不变、
   资料卡不重挂；筛选换了键，页面只按新键重取作品那一份。 */
it('资料页就地更新：换筛选只重取作品、换版式一次都不取，都不重挂', async () => {
  const r = await load(entityPath('performer', NAME));
  const { index } = indexSurface();
  await mount(r);
  const fetch = entityFetch();
  const { hosts, place } = entityFrame();
  await act(async () => {
    await r.openManagedRoute('/performers/*', entityProps(NAME, hosts), { container: index, isCurrent: () => true, place });
  });
  const card = heroPainted(index);
  const revision = r.managedEntry(index)?.revision;
  const before = { entity: fetch.calls('/api/entity').length, items: fetch.calls('/api/items').length };
  await act(async () => { r.updateManagedRoute(index, { layout: { active: true, size: 'big', portrait: false, javImage: 'cover' } }) });
  expect(fetch.fetched.mock.calls.length - before.entity - before.items - fetch.calls('/api/photos').length
    - fetch.calls('/api/feeds/discoveries').length, '换版式不发请求').toBe(0);
  await act(async () => { r.updateManagedRoute(index, { filters: { tag: '巨乳' } }) });
  await until(() => fetch.calls('/api/items').length > before.items, '按新筛选重取作品');
  expect(fetch.calls('/api/items').at(-1)?.searchParams.get('tag')).toBe('巨乳');
  expect(fetch.calls('/api/entity'), '资料不跟着筛选重取').toHaveLength(before.entity);
  expect(heroPainted(index), '推新值不许重挂').toBe(card);
  expect(r.managedEntry(index)?.revision).toBe(revision);
  expect(r.managedEntry(index)?.props).toMatchObject({ kind: 'performer', name: NAME, filters: { tag: '巨乳' } });
});

/* 女优名字里有斜杠，所以地址吃掉剩下全部段；元素先 `decodeURIComponent` 再按段切。链接按
   `encodeURIComponent` 拼（斜杠写成 `%2F`），手敲或别处来的地址可能把斜杠原样留着（`encodeURI`），
   两种都要读出同一个名字。标题按解码后的路径查路由元数据。 */
const NAMES = ['A/B', '100%', 'Mia Nix', '三上悠亜', 'ラ/ブ 50%'];

it('名字带斜杠、百分号、空格与日文：两种写法读出同一个名字，标题、链接与首屏请求都是它', async () => {
  for (const name of NAMES) {
    const href = entityPath('performer', name);
    expect(href).toBe(`/performers/${encodeURIComponent(name)}`);
    for (const written of [href, `/performers/${encodeURI(name)}`]) {
      const hit = entityTarget(written);
      expect(hit?.name, `${written} 读出的名字`).toBe(name);
      expect(hit?.kind).toBe('performer');
      expect(routeMetaOf(decodeURIComponent(written))?.title, `${written} 的标题`).toBe(name);
      const r = await load(written);
      const { index } = indexSurface();
      await mount(r);
      const fetch = entityFetch();
      const { hosts, place } = entityFrame();
      await act(async () => {
        await r.openManagedRoute('/performers/*', entityProps(hit!.name, hosts), { container: index, isCurrent: () => true, place });
      });
      expect(fetch.calls('/api/entity')[0]?.searchParams.get('name'), `${written} 的首屏请求`).toBe(name);
      expect(r.managedEntry(index)?.props).toMatchObject({ kind: 'performer', name });
      for (const unmount of unmounts.splice(0)) await act(async () => { unmount() });
      document.body.innerHTML = '';
    }
  }
});

it('资料页的路径不经 React Router 的 navigate：同页换 search、换一位与两种写法都交壳写地址', async () => {
  const r = await load('/');
  const actions = shellActions();
  const navigate = vi.fn();
  const paths = [entityPath('performer', 'A/B'), '/performers/A/B?tag=x', entityPath('studio', 'S1'),
    '/agencies/某事务所', '/series/系列?media=photos&set=9', '/creators/某人'];
  for (const path of paths) r.managedGo(path, actions, navigate);
  expect(navigate).not.toHaveBeenCalled();
  expect(vi.mocked(actions.navigate).mock.calls).toEqual(paths.map((path) => [path]));
  expect(r.ROUTED_PATHS).toEqual(expect.arrayContaining(
    ['/performers/*', '/studios/*', '/creators/*', '/series/*', '/agencies/*']));
  expect(r.isRoutedPath('/performers/A')).toBe(false);
});
