/* 作品详情岛（`item-detail`）的桩数据：`item-detail.test.ts` 与设计决定用例（`design-*.test.ts`）共用。
 *
 * 演示库里的作品没有共演、分卷、版本和播放列表，这里按 `q_item`、`q_items`（`src/peach/web_catalog.py`）
 * 与 `w_playlist`（`src/peach/web_playlists.py`）的形状造几种形态：普通、十二位共演、在脱盘来源上、标题很长、
 * 三卷分卷、两个版次，外加一份四条的播放列表。详情读写的接口全部由桩接住，写进来的请求记在
 * `writes` 里；片源不给正片，播放器报的那一条 VIDEOJS 错误由各条自己滤掉。 */
import type { Browser, Page, Request } from 'playwright-core';

import { settle, visit, type Viewport, type Visit } from './harness.ts';

export const NAMES = ['七海ひな', '桜井まい', '白石りん', '水瀬ゆい', '藤原あや', '早川なな', '森下えみ', '高橋さき',
  '小野みお', '石原かな', '青木ゆず', '中村はる'];
const TAGS = ['剧情', '单体作品', '高画质', '中出', '巨乳', '美少女'];

type Row = Record<string, unknown> & { id: number; tags: unknown[] };
const ref = (name: string, id: number, extra: Record<string, unknown> = {}) =>
  ({ id, name, has_image: false, avatar_focus: null, ...extra });

const detail = (id: number, extra: Record<string, unknown> = {}): Row => {
  const performers = (extra.performers as string[] | undefined) || [NAMES[0]!];
  return {
    id, name: `sample_${id}.mp4`, title: `演示作品 ${id}`, is_jav: false, medium: 'video', location: 'local',
    cost: 'free', size: 1_234_567_890, duration: 3600, play_seconds: 900, play_count: 2, leave_ratio: 0.4,
    has_cover: false, has_thumb: true, has_local_poster: false, performers, performer_total: performers.length,
    performer_entities: performers.map((_, at) => ({ id: at + 1, has_image: false })), creator: '', studio: 'Peach Studio',
    feedback: null, disposal: null, watch_later: false, width: 1920, height: 1080, release_date: '2026-08-01',
    region: 'jp', region_label: '日本', region_settled: true, rating: 60, liked: false, like_reason: '', o_count: 1,
    better_version: false, better_version_reason: '', has_studio_logo: false, poster_box: null,
    tags: TAGS.map((k) => ({ k, cat: 'general', official: false })),
    entity_refs: {
      performer: performers.map((name, at) => ref(name, at + 1)),
      studio: [ref('Peach Studio', 50, { has_logo: false })], creator: [], series: [ref('夏日系列', 70)],
    },
    ...extra,
  };
};

const jav = (id: number, code: string, extra: Record<string, unknown> = {}) =>
  detail(id, { is_jav: true, code, has_cover: true, ...extra });

/** 几种形态的条目 id。 */
export const ITEM = { plain: 11, cast: 12, offline: 13, long: 17, seller: 18, part: 21, edition: 31 } as const;
const PART_GROUP = { key: 'p', seed_id: 21, count: 3 };
const EDITION_GROUP = { key: 'e', seed_id: 31, count: 2, editions: ['中字', '无码'] };
const DETAILS = new Map<number, Row>([
  [11, detail(11, { performers: NAMES.slice(0, 2) })],
  [12, detail(12, { performers: NAMES, title: '演示作品 12 共演十二位' })],
  [13, detail(13, { location: '115', title: '演示作品 13 在脱盘的来源上' })],
  [14, detail(14)], [15, detail(15)], [16, detail(16)],
  // 非番号作品的标题栏印的是文件名。
  [17, detail(17, { name: `演示作品 17 ${'标题很长，写满了这一条的剧情梗概、出演与拍摄地点。'.repeat(8)}.mp4` })],
  [18, jav(18, 'FC2-PPV-4075681', { performers: [], creator: '演示卖家', studio: 'FC2-PPV',
    creator_entity: ref('演示卖家', 80, { rep: 18, has_avatar: true, avatar_stand_in: true, identity_categories: ['seller'] }),
    entity_refs: { performer: [], series: [], studio: [ref('FC2-PPV', 50, { has_logo: true })],
      creator: [ref('演示卖家', 80, { rep: 18, has_avatar: true, avatar_stand_in: true, identity_categories: ['seller'] })] },
  })],
  [21, jav(21, 'PCH-021', { part_group: PART_GROUP })],
  [22, jav(22, 'PCH-021', { part_group: PART_GROUP })],
  [23, jav(23, 'PCH-021', { part_group: PART_GROUP })],
  [31, jav(31, 'PCH-031', { edition_group: EDITION_GROUP })],
  [32, jav(32, 'PCH-031', { edition_group: EDITION_GROUP })],
]);

/** 列表与队列里的那一份：没有 `entity_refs`，标签只是字符串，同 `/api/items`。 */
const card = (id: number) => {
  const { entity_refs: _refs, ...rest } = DETAILS.get(id)!;
  return { ...rest, tags: (rest.tags as { k: string }[]).map((tag) => tag.k) };
};
export const CATALOG = [11, 12, 13, 14, 15, 16];
export const RELATED = [14, 15, 16, 12];
const PARTS = { title: 'PCH-021', items: [21, 22, 23].map((id, at) => ({
  ...DETAILS.get(id), part_label: String(at + 1), performer_entities: undefined,
  performers: NAMES.slice(0, 2),
  entity_refs: { performer: NAMES.slice(0, 2).map((name, index) => ref(name, index + 1, { has_image: true })) },
})) };
export const EDITIONS = { title: 'PCH-031', items: [{ ...card(31), edition_label: '中字' }, { ...card(32), edition_label: '无码' }] };
export const PLAYLIST = { id: 7, name: '周末片单', items: [11, 14, 15, 16] };
export const SAMPLE_ITEM = jav(41, 'PCH-041', { sample_images: [{ n: 1 }, { n: 3 }] });
DETAILS.set(SAMPLE_ITEM.id, SAMPLE_ITEM);
export const COVER_ONLY_ITEM = jav(42, 'PCH-042');
DETAILS.set(COVER_ONLY_ITEM.id, COVER_ONLY_ITEM);

const svg = (key: string, width: number, height: number) => {
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">`
    + `<rect width="${width}" height="${height}" fill="hsl(${hash % 360} 35% 45%)"/></svg>`;
};

export interface ItemStub {
  /** 桩收到的写请求：路径与请求体，按到达顺序。 */
  writes: { url: string; body: Record<string, unknown> }[];
  /** 各读接口被请求了几次，按路径计。 */
  reads: Map<string, number>;
}

const body = (request: Request) => JSON.parse(request.postData() || '{}') as Record<string, unknown>;

export async function stubItemPage(page: Page, catalogIds: readonly number[] = CATALOG): Promise<ItemStub> {
  const state: ItemStub = { writes: [], reads: new Map() };
  const read = (path: string) => state.reads.set(path, (state.reads.get(path) || 0) + 1);
  const playlists = new Map<number, { id: number; name: string; ids: number[]; current: number | null }>([
    [PLAYLIST.id, { id: PLAYLIST.id, name: PLAYLIST.name, ids: [...PLAYLIST.items], current: 11 }],
  ]);
  const playlistPayload = (id: number) => {
    const row = playlists.get(id)!;
    return { id: row.id, name: row.name, items: row.ids.map(card), current_asset_id: row.current };
  };
  const json = (path: string, respond: (url: URL, request: Request) => unknown) =>
    page.route((url) => url.pathname === path, (route) => {
      const request = route.request();
      if (request.method() === 'GET') read(path);
      else state.writes.push({ url: path, body: body(request) });
      return route.fulfill({ json: respond(new URL(request.url()), request) });
    });
  await json('/api/item', (url) => DETAILS.get(Number(url.searchParams.get('id'))) || { error: '这一条已不在数据库里' });
  await json('/api/items', () => ({ items: catalogIds.map(card), total: catalogIds.length, has_more: false }));
  await json('/api/related', () => ({ items: RELATED.map(card) }));
  await json('/api/parts', () => PARTS);
  await json('/api/editions', () => EDITIONS);
  await json('/api/library-processing', () => ({ status: 'idle' }));
  await json('/api/jav/detail', (url) => ({ code: url.searchParams.get('code'), state: 'ready', preview_count: 0 }));
  await json('/api/sources', () => ({ sources: [{ location: 'local', online: true }, { location: '115', online: false }] }));
  await json('/api/playlists', () => ({
    items: [...playlists.values()].map((row) => ({ id: row.id, name: row.name, item_count: row.ids.length })),
  }));
  await json('/api/playlist', (url, request) => {
    if (request.method() === 'GET') return playlistPayload(Number(url.searchParams.get('id')));
    const sent = body(request) as { action: string; id?: number; name?: string; asset_id?: number; asset_ids?: number[] };
    if (sent.action === 'create') {
      const id = Math.max(...playlists.keys()) + 1;
      playlists.set(id, { id, name: sent.name || '', ids: sent.asset_ids || [], current: null });
      return { ok: true, playlist: playlistPayload(id) };
    }
    const row = playlists.get(sent.id!)!;
    if (sent.action === 'reorder') row.ids = [...sent.asset_ids!];
    if (sent.action === 'remove') row.ids = row.ids.filter((id) => id !== sent.asset_id);
    if (sent.action === 'add') row.ids = [...row.ids, ...sent.asset_ids!.filter((id) => !row.ids.includes(id))];
    if (sent.action === 'progress') row.current = sent.asset_id!;
    if (sent.action === 'delete') { playlists.delete(row.id); return { ok: true } }
    return { ok: true, playlist: playlistPayload(row.id) };
  });
  const feedback = new Map<number, string | null>();
  await json('/api/feedback', (_url, request) => {
    const sent = body(request) as { id: number; kind: string; value?: number };
    if (sent.kind === 'rate') return { rating: sent.value || null };
    if (sent.kind === 'dislike' || sent.kind === 'seen') {
      const next = feedback.get(sent.id) === sent.kind ? null : sent.kind;
      feedback.set(sent.id, next);
      return { feedback: next, disposal: null, o_count: 1 };
    }
    return { feedback: feedback.get(sent.id) || null, disposal: sent.kind === 'dispose' ? 'trash' : null, o_count: 1 };
  });
  await json('/api/item-tag', () => ({ ok: true }));
  const later = new Set<number>();
  await json('/api/watch-later', (_url, request) => {
    const id = Number(body(request).id);
    if (later.has(id)) later.delete(id); else later.add(id);
    return { watch_later: later.has(id) };
  });
  await json('/api/quality-goal', (_url, request) => ({ better_version: !!body(request).wanted, better_version_reason: '' }));
  await json('/api/preference', (_url, request) => ({ liked: !!body(request).liked, like_reason: String(body(request).reason || '') }));
  await page.route((url) => /^\/api\/assets\/\d+\/subtitles$/.test(url.pathname),
    (route) => route.fulfill({ json: { subtitles: [] } }));
  await page.route((url) => ['/api/play', '/api/activity', '/api/stream-cancel'].includes(url.pathname),
    (route) => route.fulfill({ json: { ok: true } }));
  // 设置读真服务，写就地接住：`settings` 里的本地值会被推上去，改掉并发文件读到的同一份设置。
  await page.route((url) => url.pathname === '/api/settings',
    (route) => route.request().method() === 'GET' ? route.fallback() : route.fulfill({ json: { ok: true } }));
  await page.route((url) => url.pathname === '/api/stream-plan',(route) => route.fulfill({ json: { protocol: 'direct' } }));
  await page.route((url) => url.pathname === '/stream', (route) => route.fulfill({ status: 204, body: '' }));
  await page.route((url) => ['/poster', '/cover', '/logo', '/entity-image', '/avatar'].includes(url.pathname), (route) => {
    const url = new URL(route.request().url());
    const cover = url.pathname === '/cover';
    return route.fulfill({ contentType: 'image/svg+xml', body: svg(url.search, cover ? 800 : 320, cover ? 538 : 180) });
  });
  return state;
}

export interface ItemVisit extends Visit {
  stub: ItemStub;
}

/** 打开一页：桩与设置装好之后重新载入，等到 `ready` 出现、等待态结束。 */
export async function openItemPage(browser: Browser, path: string, viewport: Viewport,
  { settings = {}, ready = '#stage[open] [data-item-side]', catalogIds }: {
    settings?: Record<string, unknown>; ready?: string; catalogIds?: readonly number[];
  } = {},
): Promise<ItemVisit> {
  const opened = await visit(browser, '/', viewport);
  const page = opened.page;
  await page.context().addInitScript((value) => {
    localStorage.setItem('peach.settings.v1', JSON.stringify(value));
  }, { detailAutoplay: false, relatedLimit: 12, ...settings });
  const stubbed = await stubItemPage(page, catalogIds);
  await page.goto(new URL(path, page.url()).toString(), { waitUntil: 'load' });
  await page.locator(ready).first().waitFor({ timeout: 15_000 });
  await settle(page);
  return { ...opened, stub: stubbed };
}
