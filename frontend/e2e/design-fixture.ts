/* 设计决定用例（`design-*.test.ts`）共用的桩数据、打开页面的入口与浏览器生命周期。
 *
 * 设计决定按浏览器里的计算值断言，不绑定 CSS 源码的写法。页面迁到 React 时，旧的源码字符串
 * 断言按 ADR-0031 分三类：设计决定落在 `design-*.test.ts` 或 lint 规则，行为落在 vitest，
 * 布局与运行期问题归 `smoke.test.ts`。每条用例守一个决定，读的是 `getComputedStyle`，
 * 类名或样式写法换了照样成立。 */
import assert from 'node:assert/strict';
import { after, before, beforeEach } from 'node:test';
import type { Browser, Locator, Page } from 'playwright-core';
import {
  configurationBody, expectBody, launch, settle, visit, VIEWPORTS, type Viewport, type Visit,
} from './harness.ts';

export const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
export const MOBILE = VIEWPORTS.find((viewport) => viewport.mobile)!;

/** 在 `scope` 里解析一个颜色 token：临时挂一个元素读背景色，读完就移除。 */
export async function tokenColor(page: Page, scope: string, token: string): Promise<string> {
  return page.evaluate(([selector, name]) => {
    const probe = document.createElement('div');
    probe.style.backgroundColor = `var(${name})`;
    document.querySelector(selector)!.append(probe);
    const value = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return value;
  }, [scope, token] as const);
}

/** 配置页「网络与访问」下的访问密码分区。演示库没有 access.json，分区处于系统口令状态。
 * 与冒烟同一口径：先等配置页主体，再 settle、再切分区。 */
export async function openAccess(browser: Browser): Promise<Visit & { form: Locator }> {
  const opened = await visit(browser, '/settings/library', DESKTOP);
  await expectBody(opened.page, '/settings/library', configurationBody(opened.page));
  await settle(opened.page);
  await opened.page.getByRole('tab', { name: '网络与访问' }).click({ timeout: 5_000 });
  await settle(opened.page);
  const form = opened.page.locator('form[aria-label="访问密码"]');
  await form.waitFor({ timeout: 10_000 });
  return { ...opened, form };
}

/** 一轮跑完的任务。字段以 `/api/tasks`（`src/peach/routes_tasks.py`）为准。 */
export const settledRun = (id: number, status: string, label: string) => ({
  id, task_key: `task-${id}`, task_label: label, trigger: 'manual', status, host: 'desk',
  started_at: '2026-09-11T10:00:00Z', finished_at: '2026-09-11T10:01:00Z', elapsed_seconds: 60,
  progress_current: null, progress_total: null, progress_label: '',
  result_summary: {}, error: status === 'failed' ? 'RuntimeError: 上游挡回来了' : '',
});

/** 活动页按给定的一份 `/api/tasks` 打开：演示库里凑不齐失败、被挡下和成功三种状态。 */
export async function openActivity(browser: Browser, runs: unknown[]): Promise<Visit> {
  const opened = await visit(browser, '/tasks', DESKTOP);
  await opened.page.route('**/api/tasks', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ available: true, running: [], skipped: [], finished: runs }),
  }));
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('li[data-status]').first().waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 一条待升级的目标。字段以 `/api/quality-goals`（`src/peach/web_contract.py`）为准。 */
export const qualityGoal = (id: number, name: string) => ({
  id, name, code: null, location: 'local', size: 2147483648, duration: 3725,
  reason: '只有 720p', cost: 'free', has_thumb: true, has_cover: false,
});

/** 1×1 的透明 PNG，够让 `<img>` 走完一次加载。 */
export const PIXEL = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

/** 口味页浏览器画像的五个维度：头一个是其余的十倍，和真实馆藏的分布一样。 */
export const TASTE_CATEGORIES = [['剧情', 657], ['写真', 68], ['素人', 56], ['偶像', 4], ['企划', 1]]
  .map(([name, score]) => ({ name, score }));

/** 一张全身站姿照的人脸框：脸落在画面上半截的一小块里，小圆框要放大好几倍才看得清。 */
export const TASTE_FACE = { cx: .439, cy: .224, faceW: 67, imgW: 640, imgH: 960 };

/** 口味页按改写过的 `/api/taste` 打开：演示库没有浏览记录，也没有带人脸框的实体图。
 * 字段以 `src/peach/taste_history.py` 与 `src/peach/web_stats.py` 为准；实体图换成与人脸框同尺寸的纯色图。 */
export async function openTaste(browser: Browser, viewport: typeof DESKTOP): Promise<Visit> {
  const opened = await visit(browser, '/taste', viewport);
  const face = (id: number, name: string) => ({
    name, peach_items: 3, peach_score: 5, entity_id: id, has_image: true, avatar_focus: { box: TASTE_FACE },
  });
  await opened.page.route(/\/api\/taste\?/, async (route) => {
    const json = await (await route.fetch()).json();
    await route.fulfill({ json: { ...json, rankings: { ...json.rankings,
      browser_categories: TASTE_CATEGORIES,
      peach_performers: [face(901, '演示女优')],
      peach_creators: [face(902, '演示创作者')],
    } } });
  });
  await opened.page.route(/\/entity-image\?/, (route) => route.fulfill({
    contentType: 'image/svg+xml',
    body: `<svg xmlns="http://www.w3.org/2000/svg" width="${TASTE_FACE.imgW}" height="${TASTE_FACE.imgH}">`
      + `<rect width="${TASTE_FACE.imgW}" height="${TASTE_FACE.imgH}" fill="#888"/></svg>`,
  }));
  await opened.page.reload({ waitUntil: 'load' });
  await expectBody(opened.page, '/taste', [
    opened.page.locator('#stats').getByRole('tab', { name: '浏览器记录', exact: true }),
  ]);
  return opened;
}

/** 高清版目标页按给定的一份 `/api/quality-goals` 打开：演示库里凑不齐很长的标题。 */
export async function openQualityGoals(browser: Browser, items: unknown[]): Promise<Visit> {
  const opened = await visit(browser, '/quality-goals', DESKTOP);
  await opened.page.route('**/api/quality-goals?limit=200', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ total: items.length, items, offset: 0, has_more: false }),
  }));
  /* 这几条目标是造出来的，演示库里没有对应的抽帧，预览图会 404——而失败请求本身是另一
     条判据。这一条量的是封面那块的几何，给它一张能加载完的图就够。 */
  await opened.page.route('**/poster**', (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
  }));
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('li[data-goal-id]').first().waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 一个采集来源。字段以 `/api/scraping`（`src/peach/web_scraping.py`）为准。 */
export const scrapingSource = (source: string, label: string, cookie: boolean) => ({
  source, label, login: `https://${source}.example/login`,
  accepts_cookie: cookie, network: 'peach', cookie_saved: cookie,
});

/** 来源和凭证页按给定的一份 `/api/scraping` 打开：演示库里未必同时有收 Cookie 和不收的来源。
 *
 * 站标走服务端的 `/site-mark`，而这几个来源是造出来的，那一趟必然取不到；那是另一条判据，
 * 这里给它一张能加载完的图，免得运行期问题名单里混进与本条无关的失败。 */
export async function openScraping(browser: Browser): Promise<Visit> {
  const opened = await visit(browser, '/sources', DESKTOP);
  await opened.page.route('**/api/scraping', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      sources: [scrapingSource('demoa', '演示来源甲', true), scrapingSource('demob', '演示来源乙', false)],
    }),
  }));
  await opened.page.route('**/site-mark**', (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
  }));
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('form[aria-label="演示来源甲"]').waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 一行等人判的元数据候选。字段以 `src/peach/web_review.py` 的 `_review_rows` 为准。 */
export const REVIEW_ROW = {
  item_key: 'metadata_fields:ABC-123:studio',
  field: 'studio',
  field_label: '厂牌',
  code: 'ABC-123',
  query: 'ABC-123',
  evidence: '当前值：尚无；1 个匹配资产；2 个资料来源',
  candidates: [{ candidate_key: 'javdb:studio', source: 'javdb', display_value: '示例厂牌' }],
};

/** 人工复核页按一行造出来的队列打开：演示库里这一格未必正好有候选，而队列空了就没有卡。 */
export async function openReview(browser: Browser): Promise<Visit> {
  const opened = await visit(browser, '/review', DESKTOP);
  await opened.page.route('**/api/review', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      sections: { metadata_fields: [REVIEW_ROW] }, counts: { metadata_fields: 1 }, genre_tags: [],
    }),
  }));
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('section[data-review-key]').first().waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** CIE L*。一条 1px 的线看不看得见跟的是明度差，不是对比度比值：同样 1.48:1，浅色底上
 *  是一条灰线，深色底上两头的绝对亮度都贴着 0，什么都看不出来。 */
export function lightness([red, green, blue]: number[]): number {
  const linear = (channel: number) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  const y = 0.2126 * linear(red!) + 0.7152 * linear(green!) + 0.0722 * linear(blue!);
  return y > 0.008856 ? 116 * Math.cbrt(y) - 16 : 903.3 * y;
}

/** 按给定的一份 `/api/library-processing` 打开某一页：演示库里那趟任务早就跑完了，
 *  而运行态和失败态正是这两条要看的东西。字段以 `src/peach/web_library_processing.py` 为准。 */
export async function openProcessing(
  browser: Browser, path: '/' | '/data-cleanup', job: Record<string, unknown>,
): Promise<Visit> {
  const opened = await visit(browser, path, DESKTOP, (page) =>
    page.route('**/api/library-processing', (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(job),
    })));
  try {
    const body = opened.page.locator(path === '/data-cleanup'
      ? '#libraryProcessing section[aria-label="扫描与采集"]'
      : '[data-catalog-filter] [data-count-readout]');
    await expectBody(opened.page, path, [body], opened.problems);
    return opened;
  } catch (error) {
    await opened.close();
    throw error;
  }
}

/** 1280 的桌面视口比 `--board-content` 还窄，量不出「网格铺满、标题居中」这类差别。 */
export const WIDE = { name: 'wide', width: 1600, height: 900, mobile: false };

/** 垃圾文件页。计数行由垃圾队列那一页画，演示库里一条候选都没有时它照样在；壳铺的
 *  骨架（`[data-junk-count-skeleton]`）也有这两块，等的是页面接管之后的那一版。 */
export const JUNK_COUNT = '#count > .peach-react:not([data-junk-count-skeleton])';
export async function openJunk(browser: Browser): Promise<Visit> {
  const opened = await visit(browser, '/junk-files', WIDE);
  await expectBody(opened.page, '/junk-files', [
    opened.page.locator(`${JUNK_COUNT} [data-collection-summary]`),
    opened.page.locator(`${JUNK_COUNT} [data-junk-filters]`),
  ]);
  await settle(opened.page);
  return opened;
}

/** 一条垃圾候选。字段以 `/api/ads`（`src/peach/web_batch.py` 的 `q_ads`）为准；类型都挑没有预览
 *  的，造出来的 id 取不到缩略图。 */
export const junkItem = (id: number, kind: string, name: string) => ({
  id, name, junk_kind: kind, why: '文件名像推广', size: 1048576 * id, location: 'local', cost: '',
});

/** 垃圾文件页按给定的候选打开：演示库里没有垃圾候选。`/api/ads` 每次都按当前名单回话，
 *  `/api/batch` 记下请求体、把处置掉的那条从名单里拿掉。 */
export async function openJunkWith(browser: Browser, items: ReturnType<typeof junkItem>[], viewport = WIDE) {
  const opened = await visit(browser, '/junk-files', viewport);
  const state = { items: [...items], batches: [] as { ids: number[]; operation: string }[], reads: [] as string[] };
  await opened.page.route('**/api/ads?**', (route) => {
    const url = new URL(route.request().url());
    state.reads.push(url.search);
    const kind = url.searchParams.get('kind');
    const shown = state.items.filter((item) => !kind || item.junk_kind === kind);
    const counts: Record<string, number> = {};
    for (const item of state.items) counts[item.junk_kind] = (counts[item.junk_kind] || 0) + 1;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      items: shown, total: shown.length, all_total: state.items.length, pending_total: state.items.length,
      dismissed_total: 0, counts,
    }) });
  });
  await opened.page.route('**/api/batch', (route) => {
    const body = JSON.parse(route.request().postData() || '{}');
    state.batches.push(body);
    state.items = state.items.filter((item) => !body.ids.includes(item.id));
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, updated: body.ids.length }) });
  });
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('#grid [data-junk-card]').first().waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return { ...opened, state };
}

/** 一个重复文件。字段以 `/api/duplicates`（`src/peach/web_contract.py`）为准。 */
export const duplicateFile = (id: number, location: string, patch: Record<string, unknown> = {}) => ({
  id, name: `DUPE-${id}.mp4`, path: `R:\\media\\DUPE-${id}.mp4`, location, drive: location === '115' ? 'B:' : 'R:',
  size: 1073741824, duration: 3600, is_largest: false, is_longest: false, ...patch,
});

/** 重复文件页按给定的两组打开：演示库里没有重复组。一组字节一致，一组本地与 115 混着。
 *  文件是造出来的，抽帧那一趟必然取不到，给它一张能加载完的图。 */
export async function openDuplicates(browser: Browser): Promise<Visit> {
  const opened = await visit(browser, '/duplicates', DESKTOP);
  const groups = [
    { code: 'DUPE-001', count: 2, identical: true, drives: ['R:'], cross_drive: false, reclaimable: 1073741824,
      files: [duplicateFile(9001, 'local', { is_largest: true }), duplicateFile(9002, 'local', { is_longest: true })] },
    { code: 'DUPE-002', count: 2, identical: false, drives: ['R:', 'B:'], cross_drive: true, reclaimable: 1073741824,
      files: [duplicateFile(9003, 'local', { is_largest: true, is_longest: true }), duplicateFile(9004, '115')] },
  ];
  await opened.page.route('**/api/duplicates?**', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ total: 2, files: 4, reclaimable: 2147483648, groups }),
  }));
  await opened.page.route('**/thumb?**', (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
  }));
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('#stats section[aria-label="DUPE-002"] .duplicate-row').first().waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 打开一张索引页，名册与词表都换成桩：演示库的索引在一轮里会从空变成有，不桩的话
 * 这一条量到的可能是空态。名册第一格带一张桩图，其余只落首字母。 */
export async function openIndexPage(browser: Browser, path: string): Promise<Visit> {
  const opened = await visit(browser, path, DESKTOP);
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await opened.page.route('**/api/index?**', (route) => {
    const kind = new URL(route.request().url()).searchParams.get('kind');
    return route.fulfill(json(kind === 'tags'
      ? { items: [{ k: '痴女', n: 4, cat: 'role' }, { k: '秘书OL', n: 2, cat: 'scene' }], has_more: false,
        categories: { role: 1, scene: 1 } }
      : { items: [{ k: '甲', n: 3, members: 2, entity_id: 90_101, has_image: true }, { k: '乙', n: 1, members: 1 }],
        has_more: false }));
  });
  // 桩图要比框大：比框小的图按原尺寸摆，走的是另一条规则。
  await opened.page.route('**/entity-image?**', (route) => route.fulfill({
    status: 200, contentType: 'image/svg+xml',
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="#888"/></svg>',
  }));
  await opened.page.route('**/api/follow/tags?**', (route) => route.fulfill(json({
    items: [{ k: 'some_artist', n: 5, cat: 'artist' }, { k: 'long_hair', n: 9, cat: 'general' }], has_more: false,
    categories: { artist: 1, general: 1 } })));
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('#index [data-index-cell], #index [data-alpha-tag], #index [data-tag-chip]').first()
    .waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 一串 `box-shadow`（计算值或 token 原文）里最大的那个像素数：落影三层里最远那层的模糊半径。 */
export const farthestShadow = (shadow: string) =>
  Math.max(0, ...[...shadow.matchAll(/(\d+(?:\.\d+)?)px/g)].map((match) => Number(match[1])));

/** 播放列表页按一份造出来的 `/api/playlists`（`src/peach/web_playlists.py`）打开：演示库里
 * 没有播放列表。第一份带三位署名，第二份没人（画标题首字），第三份是空列表（写「无预览」）。
 * 封面是造出来的编号，给一张能加载完的图。 */
export async function openPlaylistsPage(browser: Browser, viewport = DESKTOP): Promise<Visit> {
  const opened = await visit(browser, '/playlists', viewport);
  const face = (id: number, name: string, kind = 'performer') => ({ kind, id, name, has_image: false });
  const row = (id: number, patch: Record<string, unknown> = {}) => ({
    id, name: `列表${id}`, source_kind: 'manual', source_seed_asset_id: null, current_asset_id: null,
    created_at: '2026-09-01 10:00:00', updated_at: '2026-09-01 10:00:00', item_count: 2,
    preview_asset_id: 11, preview_ids: [11, 12], faces: [], ...patch,
  });
  await opened.page.route((url) => url.pathname === '/api/playlists', (route) => route.fulfill({ json: { items: [
    row(1, { source_kind: 'mix', faces: [face(901, '甲'), face(902, '乙'), face(903, '丙', 'creator')] }),
    row(2),
    row(3, { item_count: 0, preview_asset_id: null, preview_ids: [] }),
  ] } }));
  await opened.page.route('**/poster**', (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
  }));
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('#stats [data-playlist-card]').first().waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 打开目录并等到读数与卡片一起替下首屏骨架。 */
export async function openCatalog(browser: Browser): Promise<Visit> {
  const opened = await visit(browser, '/', DESKTOP);
  await opened.page.locator('[data-catalog-filter] [data-count-readout]').waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 打开目录，两排头像与标签条用桩数据铺满：演示库那十几部作品凑不出一排标签。 */
export async function openCatalogBars(browser: Browser, viewport: Viewport = DESKTOP): Promise<Visit> {
  const opened = await visit(browser, '/', viewport);
  const performers = Array.from({ length: 6 }, (_, at) => ({
    id: 96_000 + at, k: `演示女优${at + 1}`, n: 30 - at, has_image: false, has_avatar: false, avatar_focus: null, rep: null,
  }));
  const studios = Array.from({ length: 3 }, (_, at) => ({ k: `演示厂牌${at + 1}`, n: 20 - at, has_logo: false }));
  const tags = Array.from({ length: 8 }, (_, at) => ({ k: `演示标签${at + 1}`, n: 90 - at }));
  await opened.page.route((url) => url.pathname === '/api/tops', (route) =>
    route.fulfill({ json: Number(new URL(route.request().url()).searchParams.get('page') || 0)
      ? { performers: [], studios: [] } : { performers, studios } }));
  await opened.page.route((url) => url.pathname === '/api/facets', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), tags } });
  });
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('[data-catalog-filter] [data-catalog-tag]').first().waitFor({ timeout: 15_000 });
  await opened.page.locator('[data-catalog-filter] [data-tier-studio]').first().waitFor({ timeout: 15_000 });
  await settle(opened.page);
  // 关页时还在路上的那一趟桩请求不算失败。
  return { ...opened, close: async () => {
    await opened.page.unrouteAll({ behavior: 'ignoreErrors' });
    await opened.close();
  } };
}

export interface CatalogFixture {
  total: number;
  items: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

/** 从演示库取一份能完整渲染的真实目录响应，只替换当前判据需要的字段。 */
export async function openCatalogFixture(
  browser: Browser,
  change: (payload: CatalogFixture, url: URL) => void,
): Promise<Visit> {
  const opened = await visit(browser, '/', DESKTOP);
  const baselines = new Map<string, CatalogFixture>();
  await opened.page.route(/\/api\/items\?/, async (route) => {
    const url = new URL(route.request().url());
    /* `visit()` 返回时，首屏还可能在后台续取下一页。路由接管之后若先撞上 offset>0，
       那份响应本来就可能没有卡片，不能拿它当首屏基线；让旧请求原样完成，reload 后
       再以 offset=0 的响应建立夹具。 */
    if (url.searchParams.get('offset') !== '0') {
      await route.continue();
      return;
    }
    const key = url.searchParams.get('orient') || '';
    let baseline = baselines.get(key);
    if (!baseline) {
      const response = await route.fetch();
      baseline = await response.json() as CatalogFixture;
      baselines.set(key, baseline);
    }
    const payload = structuredClone(baseline);
    change(payload, url);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('[data-catalog-filter] [data-count-readout]').waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 一趟跑到一半的扫描与采集。 */
export const RUNNING_JOB = { status: 'running', stage: '采集缺失资料', checked: 38, total: 100 };

/** 一趟断在半路、攒下一份长问题清单的扫描与采集：演示库里这两样都凑不出来。 */
export const FAILED_JOB = {
  status: 'failed', job_id: 'one', error: '处理被中断，请检查媒体目录后重试。',
  issue_count: 347, issues_truncated: true, retryable_asset_ids: [1, 2],
  issues_log: 'C:\\peach-data\\state\\library-processing-demo.issues.jsonl',
  issue_preview: Array.from({ length: 20 }, (_, at) => ({
    asset_id: at + 1, title: `示例-${at + 1}.mp4`, path: `B:\\番号\\示例-${at + 1}.mp4`,
    message: '封面未取得：javdb：本趟采集次数已用完，再跑一次接着采；已有图片保留',
  })),
};

/** 一条关注来源。字段以 `_source_payload`（`src/peach/web_follow.py`）为准。 */
export const followSource = (id: number, author: string, provider: string, label: string, status = 'ok') => ({
  id, provider: provider.toLowerCase(), provider_label: provider, ref: `ref/${id}`, label,
  url: `https://example.com/${id}`, enabled: true, last_status: status,
  last_checked_at: '2026-09-01T00:00:00Z', created_at: '2026-08-01T00:00:00Z',
  author_key: `name:${author}`, author_name: author,
});

/** 一个站的凭据状态。字段以 `/api/follow/credentials` 为准。 */
export const followCredential = (provider: string, requirement: string, present: boolean, missing: string[]) => ({
  provider: provider.toLowerCase(), provider_label: provider, followable: true, requirement,
  needs: missing, fields: present ? ['cookie'] : [], missing, present,
});

/** 关注管理页按一份造好的来源与凭据打开：演示库没有关注来源，也凑不齐四种凭据处境。
 *  站标同 `openScraping`：造出来的来源取不到图标，给一张能加载完的图。 */
export async function openFollowManage(browser: Browser, viewport = DESKTOP, data: FollowManageData = {}): Promise<Visit> {
  const opened = await visit(browser, '/subscriptions', viewport);
  await stubFollowManage(opened.page, data);
  await opened.page.reload({ waitUntil: 'load' });
  await opened.page.locator('section[aria-label="kou 的关注来源"]').waitFor({ timeout: 15_000 });
  await settle(opened.page);
  return opened;
}

/** 换掉计数、在三条来源之后再加几条：量极端内容时用。 */
interface FollowManageData {
  counts?: Record<string, number>;
  extra?: Record<string, unknown>[];
}

export async function stubFollowManage(page: Page, { counts, extra = [] }: FollowManageData = {}): Promise<void> {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/follow?summary=1', (route) => route.fulfill(json({
    sources: [
      followSource(1, 'kou', 'Kemono', 'kou · Kemono'),
      followSource(2, 'kou', 'Pawchive', 'kou · Pawchive'),
      followSource(3, 'mira', 'Kemono', 'mira · Kemono', 'error'),
      ...extra,
    ],
    counts: counts || { new: 0, seen: 0, saved: 0, ignored: 0 },
    author_aliases: [{ canonical_key: 'kou', canonical_name: 'kou', aliases: [{ key: 'kou_art', name: 'kou_art' }] }],
    alias_suggestions: [],
    suggestions: [],
  })));
  await page.route('**/api/follow/credentials', (route) => route.fulfill(json({
    root: 'C:\\peach\\creds',
    providers: [
      followCredential('Fanbox', 'required', false, ['FANBOXSESSID']),
      followCredential('Patreon', 'optional', true, []),
      followCredential('OnlyFans', 'blocked', false, []),
      followCredential('Kemono', 'none', false, []),
    ],
  })));
  await page.route('**/source-icon**', (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
  }));
  /* 服务端的定时检查可能正在跑，那一趟会让每颗检查键挂着 aria-busy，settle 等不到头。 */
  await page.route('**/api/follow/check', (route) => (route.request().method() === 'GET'
    ? route.fulfill(json({ status: 'idle' })) : route.fallback()));
}

/** 把 `/api/` 请求挂住，直到调用返回的 `release()`：首屏骨架停在屏幕上，量完再放行。
 *  先注册的桩照常生效：放行走 `fallback()`，交给它们或真实服务端。 */
export async function holdApi(page: Page): Promise<() => void> {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route((url) => url.pathname.startsWith('/api/') && url.pathname !== '/api/settings', async (route) => {
    await gate;
    await route.fallback().catch(() => {});
  });
  return release;
}

/** 一组控件此刻的长相：面色、字色、边线、圆角与外框尺寸，以及按钮组的选中标记。 */
export async function controlFaces(page: Page, selectors: Record<string, string>) {
  return page.evaluate((map) => Object.fromEntries(Object.entries(map).map(([name, selector]) => {
    const node = document.querySelector(selector);
    if (!node) return [name, null];
    const style = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    return [name, {
      face: `${style.backgroundColor} ${style.backgroundImage}`, ink: style.color,
      edge: `${style.borderTopWidth} ${style.borderTopColor}`, radius: style.borderTopLeftRadius,
      size: `${Math.round(box.width)}x${Math.round(box.height)}`, pressed: node.getAttribute('aria-pressed'),
    }];
  })), selectors);
}

/** 打开一位订了新作、有一排同台艺人的人物页。演示库里没有人物实体，资料照服务端下发的形状写。 */
export async function openPerformer(browser: Browser, viewport = DESKTOP): Promise<Visit> {
  const name = '七沢みあ';
  const opened = await visit(browser, '/', viewport);
  const costar = (id: number, k: string) => ({ id, k, n: 1, rep: null, has_image: false, has_avatar: false, avatar_focus: null });
  await opened.page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
    id: 90_001, kind: 'performer', canonical_name: name, aliases: [], display_aliases: [],
    user_aliases: [], asset_count: 0, tags: [],
    related_performers: ['本田愛华', '枢木葵', '美谷朱音', '高杉麻里', '高桥圣子', '今井夏帆'].map((k, at) => costar(90_002 + at, k)),
    links: [], metadata: {}, has_image: false, has_avatar: false, avatar_focus: null, representative_asset_id: null,
    entry_links: [
      { site: 'javdb', label: 'JavDB', ordinal: '', slot: 'mark', mark: 'mark-javdb', url: 'https://javdb.com/actors/NPD3' },
      { site: 'missav', label: 'MISSAV', ordinal: '', slot: 'mark', mark: '', url: 'https://missav.ai/actresses/x' },
    ],
    feed: { following: true },
  } }));
  await opened.page.goto(new URL(`/performers/${encodeURIComponent(name)}`, opened.page.url()).href, { waitUntil: 'load' });
  await opened.page.locator('[data-entry-feed]').waitFor({ timeout: 15_000 });
  await opened.page.locator('[data-entity-foot] [data-related-performer]').first().waitFor({ timeout: 15_000 });
  await settle(opened.page);
  await opened.page.evaluate(() => {
    document.documentElement.dataset.theme = 'light';
    document.documentElement.classList.remove('dark');
  });
  return opened;
}

/** 照片档：一部作品的两张官方样张加六张本地图片，缩略图都是 1×1 的图，量的是墙的几何。 */
export async function openPhotoWall(browser: Browser, viewport = DESKTOP): Promise<Visit> {
  const name = '七沢みあ';
  const opened = await visit(browser, '/', viewport);
  const { page } = opened;
  await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
    id: 90_001, kind: 'performer', canonical_name: name, aliases: [], display_aliases: [], user_aliases: [],
    asset_count: 0, tags: [], related_performers: [], links: [], metadata: {}, has_image: false, has_avatar: false,
    avatar_focus: null, representative_asset_id: null, entry_links: [], feed: { following: false },
  } }));
  // 下一页一直不回：「载入更多」停在忙态，墙不再变，量得到那枚键的几何。
  await page.route(/\/api\/photos\?/, (route) => Number(new URL(route.request().url()).searchParams.get('offset') || 0) ? undefined : route.fulfill({ json: {
    kind: 'performer', name, entity_id: 90_001, total: 12, seed: '', has_more: true, sample_total: 2,
    items: Array.from({ length: 6 }, (_, i) => ({ id: 200 + i, name: `${200 + i}.jpg`, size: 1, location: 'media' })),
    sets: [{ id: 'code:SSIS-057', kind: 'code', code: 'SSIS-057', name: '雨の日', title: 'SSIS-057 雨の日', n: 2,
      release_date: '2021-05-18', site: 'dmm', site_label: 'DMM', has_cover: true }],
  } }));
  await page.route(/\/(?:photo-thumb|sample-thumb)\?/, (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
  }));
  await page.goto(new URL(`/performers/${encodeURIComponent(name)}?media=photos`, page.url()).href, { waitUntil: 'load' });
  await page.locator('[data-local-wall] [data-photo-cell] img').first().waitFor({ timeout: 15_000 });
  await settle(page);
  return opened;
}

/** 本地图片那面墙的排法：网格数列宽，瀑布流数 column-count。 */
export const photoWallFaces = (page: Page) => page.evaluate(() => {
  const wall = document.querySelector<HTMLElement>('[data-local-wall]')!;
  const style = getComputedStyle(wall);
  const cell = wall.querySelector<HTMLElement>('[data-photo-cell]')!;
  const box = cell.getBoundingClientRect();
  return {
    display: style.display,
    columns: style.display === 'grid' ? style.gridTemplateColumns.split(' ').length : Number(style.columnCount),
    gap: style.columnGap,
    square: Math.abs(box.width - box.height) <= 1,
  };
});

/** 照片档里 `count` 张本地图，点开第一张的灯箱。大图是 `width`×`height` 的 SVG；取图口全部回桩图，
 *  `/photo` 原图走计费来源，设计用例也不碰真的。 */
export async function openLightbox(browser: Browser, { count, width, height }: { count: number; width: number; height: number }):
  Promise<Visit & { box: Locator }> {
  const name = '七沢みあ';
  const opened = await visit(browser, '/', DESKTOP);
  const { page } = opened;
  const svg = (w: number, h: number) => ({ status: 200, contentType: 'image/svg+xml',
    body: `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="100%" height="100%" fill="#3a4a5a"/></svg>` });
  await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
    id: 90_001, kind: 'performer', canonical_name: name, aliases: [], display_aliases: [], user_aliases: [],
    asset_count: 0, tags: [], related_performers: [], links: [], metadata: {}, has_image: false, has_avatar: false,
    avatar_focus: null, representative_asset_id: null, entry_links: [], feed: { following: false },
  } }));
  await page.route(/\/api\/photos\?/, (route) => route.fulfill({ json: {
    kind: 'performer', name, entity_id: 90_001, total: count, seed: '', has_more: false, sample_total: 0, sets: [],
    items: Array.from({ length: count }, (_, i) => ({ id: 300 + i, name: `${300 + i}.jpg`, size: 1, location: 'media' })),
  } }));
  await page.route(/\/photo\?/, (route) => route.fulfill(svg(width, height)));
  await page.route(/\/photo-thumb\?/, (route) => route.fulfill(svg(160, 120)));
  await page.goto(new URL(`/performers/${encodeURIComponent(name)}?media=photos`, page.url()).href, { waitUntil: 'load' });
  await page.locator('[data-photo-cell] img').first().waitFor({ timeout: 15_000 });
  await settle(page);
  await page.locator('[data-photo-cell]').first().click();
  const box = page.locator('dialog[data-photo-lightbox][open]');
  await box.waitFor();
  await page.waitForFunction(() => document.querySelector<HTMLImageElement>('[data-photo-main] .swiper-slide-active img')!
    .naturalWidth > 0, undefined, { timeout: 10_000 });
  return { ...opened, box };
}

/** 有 minnano-av 资料的女优。`profile` 与 `name_groups` 照 `peach.entity_profile.header` 的形状写，
 *  出道片名故意很长：资料表那一格要单行截断。 */
export const PROFILED = {
  name: '篠田ゆう',
  profile: {
    birth_date: '1991-07-21', age: 35, height: 155, bust: 86, waist: 60, hip: 87, cup: 'F',
    debut_date: '2010-12-02', debut_title: 'セキララ 〜今どき世代のゆるい性事情〜 03 はじめての撮影でとまどう素人娘の記録',
    active: { from: '2010', to: '2023' },
    tags: ['美乳', '美臀', '百合', '肛交', '巨乳', '高颜值', '苗条', '高个', '剛毛'],
  },
  name_groups: {
    reading: 'しのだゆう', shown: ['篠崎ゆう子', '高木早希', '橋本真紀'], total: 7,
    groups: [
      { label: '旧名义', names: [{ name: '篠崎ゆう子', reading: 'しのざきゆうこ' }] },
      { label: '舞ワイフ', names: [{ name: '橋本真紀' }, { name: '桧山彩音' }, { name: '篠田杏奈' }] },
      { label: 'ラグジュTV', names: [{ name: '高木早希' }] },
      { label: '其它', names: [{ name: '城田優子' }] },
    ],
  },
};

/** 打开一位有资料的女优：资料表、名字行和外链照服务端下发的形状写，主题按参数切好。
 *  `extra` 覆盖资料里的字段：默认没有看片那一行（`entry_links` 只有 minnano-av 那枚 pill、没有 `feed`），
 *  要验两行都在的几何就从这里补。 */
export async function openProfiledPerformer(browser: Browser, viewport = DESKTOP, theme: 'light' | 'dark' = 'light',
  extra: Record<string, unknown> = {}): Promise<Visit> {
  const opened = await visit(browser, '/', viewport);
  await opened.page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
    id: 90_101, kind: 'performer', canonical_name: PROFILED.name, aliases: ['篠崎ゆう子'], display_aliases: ['篠崎ゆう子'],
    user_aliases: [], asset_count: 42, tags: [], related_performers: [], metadata: {},
    has_image: false, has_avatar: false, avatar_focus: null, representative_asset_id: null,
    agency: { id: 90_102, canonical_name: 'New Actor eXperience', source: 'test', checked_at: '' },
    links: [
      { link_id: 90_201, link_kind: 'official', clickable: true, label: 'New Actor eXperience',
        url: 'https://official.nax-pro.com/actress/shinoda' },
      { link_id: 90_202, link_kind: 'social', clickable: true, label: 'X @shinoda_yu', url: 'https://x.com/shinoda_yu' },
    ],
    entry_links: [{ site: 'minnano-av', label: 'みんなのAV', ordinal: '', slot: 'pill', mark: 'brand-minnano',
      url: 'https://www.minnano-av.com/actress12345.html' }],
    profile: PROFILED.profile, name_groups: PROFILED.name_groups,
    ...extra,
  } }));
  // 这几条链接是造出来的，服务端的圆标取不到；那是另一条判据，这里给一张能加载完的图。
  await opened.page.route('**/link-mark**', (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
  }));
  await opened.page.goto(new URL(`/performers/${encodeURIComponent(PROFILED.name)}`, opened.page.url()).href, { waitUntil: 'load' });
  await opened.page.locator('[data-entity-card] [data-entity-links]').waitFor({ timeout: 15_000 });
  await settle(opened.page);
  await opened.page.evaluate((dark) => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.documentElement.classList.toggle('dark', dark);
  }, theme === 'dark');
  return opened;
}

/** 资料卡浮层的落影要和遗留 `.popmenu` 同一副：在同一页造一个空 `.popmenu` 取它的计算值。 */
export async function popmenuShadow(page: Page): Promise<string> {
  return page.evaluate(() => {
    const menu = document.body.appendChild(Object.assign(document.createElement('div'), { className: 'popmenu' }));
    const shadow = getComputedStyle(menu).boxShadow;
    menu.remove();
    return shadow;
  });
}

/** 下拉栏里一部作品的小图。字段以 `_suggest_work_card`（`src/peach/web_entity.py`）为准。 */
export const suggestCard = (id: number) => ({ id, code: `ABW-${id % 1000}`, has_cover: true, has_thumb: false, cover_frame: null, poster_box: null });

/** 一段输入命中女优、厂牌与视频三类的补全，字段以 `q_suggest` 为准。演示库里没有女优实体。 */
export const SUGGEST = {
  q: '涼森',
  groups: [
    { kind: 'performer', label: '女优', total: 2, items: [
      { value: '涼森れむ', n: 128, matched: '', id: null, entity_id: 90_301, has_image: true, avatar_focus: null, rep: null,
        agency: 'Capsule Agency', works: [1, 2, 3, 4].map((at) => suggestCard(90_400 + at)) },
      { value: '涼森ひより', n: 3, matched: 'すずもりひより', id: null, entity_id: 90_302, has_image: false, rep: null,
        agency: '', works: [] },
    ] },
    { kind: 'studio', label: '厂牌', total: 1, items: [
      { value: 'Prestige', n: 900, matched: '', id: null, entity_id: 90_303, has_image: false, has_logo: true },
    ] },
    { kind: 'asset', label: '视频', total: 12, items: [1, 2, 3, 4, 5].map((at) => ({
      value: `ABW-${500 + at}`, n: 0, matched: '', id: 90_500 + at, code: `ABW-${500 + at}`,
      title: '出演作', who: '涼森れむ', card: suggestCard(90_500 + at),
    })) },
  ],
};

/** 补全、下拉里的图和点进去的资料页都按上面那份造好的数据答。返回每次补全请求问的种类。
 *  图是造出来的实体和番号，服务端取不到；那是另一条判据，这里给一张能加载完的图。 */
export async function stubSuggest(page: Page): Promise<string[]> {
  const kinds: string[] = [];
  await page.route(/\/api\/suggest\?/, (route) => {
    const kind = new URL(route.request().url()).searchParams.get('kind') || '';
    kinds.push(kind);
    return route.fulfill({ json: { q: SUGGEST.q, groups: SUGGEST.groups.filter((group) => !kind || group.kind === kind) } });
  });
  await page.route(/\/(?:cover|entity-image|avatar|logo)\?/, (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from(PIXEL, 'base64'),
  }));
  await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
    id: 90_301, kind: 'performer', canonical_name: '涼森れむ', aliases: [], display_aliases: [], user_aliases: [],
    asset_count: 0, tags: [], related_performers: [], links: [], metadata: {}, entry_links: [],
    has_image: false, has_avatar: false, avatar_focus: null, representative_asset_id: null,
  } }));
  return kinds;
}

/** 资料卡此刻的几何：身份列、资料表与卡本身的外框，以及资料表那道分隔线落在哪一边。 */
export async function heroGeometry(page: Page) {
  return page.evaluate(() => {
    const rect = (selector: string) => {
      const box = document.querySelector(selector)?.getBoundingClientRect();
      return box ? { left: box.left, right: box.right, top: box.top, bottom: box.bottom } : null;
    };
    const hero = document.querySelector('[data-entity-card]')!;
    const facts = document.querySelector('[data-entity-facts]');
    const style = facts ? getComputedStyle(facts) : null;
    return {
      hero: rect('[data-entity-card]'), identity: rect('[data-entity-identity]'), facts: rect('[data-entity-facts]'),
      heroScrolls: hero.scrollWidth > hero.clientWidth + 1,
      rule: style ? { left: style.borderLeftWidth, top: style.borderTopWidth } : null,
      labels: [...document.querySelectorAll('[data-entity-facts] dt')].map((dt) => dt.textContent!.trim()),
    };
  });
}

/** 禁用档的三样颜色（peach-web-ui「按钮悬停只抬填充」那条）：`--surface` 底、`--border-15` 边、`--muted` 字。 */
export async function disabledTokens(page: Page) {
  return {
    face: await tokenColor(page, '#main', '--surface'),
    ink: await tokenColor(page, '#main', '--muted'),
    ring: await tokenColor(page, '#main', '--border-15'),
  };
}

/** 骨架里等数据的操作键此刻的长相与状态，按文档顺序。 */
export async function waitingActionFaces(page: Page, selector: string) {
  return page.locator(selector).evaluateAll((buttons) => buttons.map((button) => {
    const style = getComputedStyle(button);
    return {
      name: (button.getAttribute('aria-label') || button.textContent || '').trim(),
      disabled: (button as HTMLButtonElement).disabled, cursor: style.cursor, opacity: style.opacity,
      face: style.backgroundColor, image: style.backgroundImage, ink: style.color, ring: style.boxShadow,
      split: !!button.closest('[data-split-button]'),
    };
  }));
}

export function assertDisabledFace(face: Awaited<ReturnType<typeof waitingActionFaces>>[number],
  expected: Awaited<ReturnType<typeof disabledTokens>>): void {
  assert.equal(face.disabled, true, `${face.name} 在骨架里没有禁用`);
  assert.equal(face.cursor, 'not-allowed', `${face.name} 在骨架里的光标不是禁用那一种`);
  assert.equal(face.opacity, '1', `${face.name} 的禁用态靠透明度，而不是换颜色`);
  assert.equal(face.face, expected.face, `${face.name} 在骨架里不是禁用底色`);
  assert.equal(face.image, 'none', `${face.name} 在骨架里还铺着渐变`);
  assert.equal(face.ink, expected.ink, `${face.name} 在骨架里不是禁用字色`);
  // 分体键的外圈画在整组上，两半各画一圈的话中缝会出现两道线。
  if (!face.split) assert.ok(face.ring.includes(expected.ring), `${face.name} 在骨架里没有禁用描边：${face.ring}`);
}

/** 切到关注管理页的另一栏。栏名后面可能挂着待配置的数目，按开头认。 */
export async function followTab(opened: Visit, name: string): Promise<void> {
  await opened.page.locator('#stats').getByRole('tab', { name: new RegExp(`^${name}`) }).click({ timeout: 5_000 });
  await settle(opened.page);
}

/** 每个设计决定文件一个浏览器，至多运行 8 项就换一个，页面与图像缓存的累积占用受限。
 * `assign` 在每次换上新浏览器时收到它，文件里的用例照旧读同一个 `browser` 变量。 */
export function installDesignBrowser(assign: (browser: Browser) => void): void {
  let browser: Browser;
  let browserTests = 0;

  before(async () => {
    browser = await launch();
    assign(browser);
  });

  beforeEach(async () => {
    if (browserTests === 8) {
      await browser.close();
      browser = await launch();
      assign(browser);
      browserTests = 0;
    }
    browserTests += 1;
  });

  after(async () => {
    await browser.close();
  });
}
