/* 女优资料页照片档的番号样张（ADR-0068）。
 *
 * 演示库没有人物实体，也没有样张：资料、照片清单、封面和样张图都由这里按接口的形状给，
 * 页面自己的路由、渲染、深链与灯箱照常跑。第 2 张样张故意回 404，验「取不到的那格留着占位、
 * 灯箱照样翻得到」。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { launch, settle, visit, VIEWPORTS, type Visit } from './harness.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const NAME = '七沢みあ';
// 1×1 的 PNG，样张与本地图都用它：这里验的是结构与交互，不是图。
const PIXEL = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64');
const MISSING = /\/sample-(?:thumb|image)\?code=SSIS-057&n=2$/;
const SETS = [
  { id: 'code:SSIS-057', kind: 'code', code: 'SSIS-057', name: '雨の日', title: 'SSIS-057 雨の日', n: 3,
    release_date: '2021-05-18', site: 'dmm', site_label: 'DMM', has_cover: true },
  { id: 'code:SSIS-001', kind: 'code', code: 'SSIS-001', name: '初夏', title: 'SSIS-001 初夏', n: 2,
    release_date: '2021-02-19', site: 'dmm', site_label: 'DMM', has_cover: true },
];

type Local = { first: number; more: number };

/** `local` 给了就同时有本地图片：第一页 `first` 张、`has_more` 为真，翻页请求把 offset 记下来。 */
async function serveProfile(page: Page, local?: Local): Promise<number[]> {
  const offsets: number[] = [];
  const photo = (id: number) => ({ id, name: `${id}.jpg`, size: 1, location: 'media' });
  await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
    id: 90_001, kind: 'performer', canonical_name: NAME, aliases: [], display_aliases: [],
    user_aliases: [], asset_count: 0, tags: [], related_performers: [], links: [],
    metadata: {}, has_image: false, has_avatar: false, avatar_focus: null,
    representative_asset_id: null, entry_links: [], feed: { following: false },
  } }));
  await page.route(/\/api\/photos\?/, (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset') || 0);
    if (offset) offsets.push(offset);
    const items = !local ? [] : offset
      ? Array.from({ length: local.more }, (_, i) => photo(100 + offset + i))
      : Array.from({ length: local.first }, (_, i) => photo(100 + i));
    return route.fulfill({ json: {
      kind: 'performer', name: NAME, entity_id: 90_001, total: local ? local.first + local.more : 0, items,
      seed: '', has_more: Boolean(local) && !offset, sample_total: 5, sets: SETS,
    } });
  });
  await page.route(/\/photo-thumb\?/, (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL }));
  await page.route(/\/sample-(?:thumb|image)\?/, (route) => MISSING.test(route.request().url())
    ? route.fulfill({ status: 404, json: { error: 'unavailable' } })
    : route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL }));
  return offsets;
}

async function openPhotos(browser: Browser, search: string, local?: Local): Promise<Visit & { offsets: number[]; documents: string[] }> {
  let offsets: number[] = [];
  const documents: string[] = [];
  const opened = await visit(browser, `/performers/${encodeURIComponent(NAME)}?${search}`, DESKTOP, async page => {
    page.on('request', request => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame()) documents.push(request.url());
    });
    offsets = await serveProfile(page, local);
  });
  try {
    await opened.page.locator('#index [data-entity-readout]', { hasText: '照片' })
      .waitFor({ state: 'visible', timeout: 15_000 });
    await settle(opened.page);
    return { ...opened, offsets, documents };
  } catch (error) {
    await opened.close();
    throw error;
  }
}

/** 故意回 404 的那一张不算运行期问题，浏览器为它各记一条的控制台 404 也按条数扣掉；
 * 其余照 harness 的口径一条都不许有。 */
function unexpected(problems: string[]): string[] {
  const planted = /^404 \/sample-/;
  let mirrors = problems.filter((problem) => planted.test(problem)).length;
  return problems.filter((problem) => {
    if (planted.test(problem)) return false;
    if (mirrors > 0 && /^console Failed to load resource: .*404/.test(problem)) { mirrors -= 1; return false; }
    return true;
  });
}

/** 每一段的段头标签和它下面那面墙的格数，按页面顺序。 */
const groups = (page: Page) => page.locator('[data-photo-group]').evaluateAll((heads) => heads.map((head) => [
  head.querySelector('b')?.textContent?.trim() || '',
  head.nextElementSibling?.querySelectorAll('[data-photo-cell]').length ?? -1,
]));

describe('番号样张', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  it('只有样张时照片档照样出现，各部样张按发行日一部一段，段头写番号、标题与来源', async () => {
    const opened = await openPhotos(browser, 'media=photos');
    const { page } = opened;
    try {
      assert.equal(opened.documents.length, 1, '夹具直接打开目标资料页，页面只导航一次');
      assert.equal(new URL(opened.documents[0]).pathname, `/performers/${encodeURIComponent(NAME)}`);
      assert.equal(await page.locator('[data-entity-media] [data-media-view="photos"]').getAttribute('aria-pressed'),
        'true');
      assert.deepEqual(await groups(page), [['SSIS-057', 3], ['SSIS-001', 2]]);
      // 第一段的 28px 段距与筛选浮层 22px 的下边距合并成一段，不叠加。
      const gap = await page.evaluate(() => document.querySelector('[data-photo-group]')!.getBoundingClientRect().top
        - document.querySelector('[data-entity-filter-glass]')!.getBoundingClientRect().bottom);
      assert.ok(Math.abs(gap - 28) <= 1, `第一段离浮层 ${gap}px`);
      assert.match(await page.locator('[data-photo-group]').first().innerText(), /雨の日[\s\S]*DMM 样张 · 2021-05-18 · 3 张/);
      assert.match(await page.locator('[data-entity-readout]').innerText(), /样张 5 张 · 2 部作品/);
      assert.equal(await page.locator('[data-entity-batch]').count(), 0, '没有本地图可换一批');
      assert.equal(await page.locator('[data-photo-back]').count(), 0, '样张不另开一层');
      assert.deepEqual(unexpected(opened.problems), [], JSON.stringify(opened.problems));
    } finally {
      await opened.close();
    }
  });

  it('取不到的那格留着占位，灯箱跨段连续翻', async () => {
    const opened = await openPhotos(browser, 'media=photos');
    const { page } = opened;
    try {
      await page.waitForFunction(() => !document.querySelector('[data-photo-wall]')?.children[1]?.querySelector('img'));
      const hole = await page.locator('[data-photo-wall] [data-photo-cell]').nth(1).boundingBox();
      assert.ok(hole && hole.height > 0, '占位格不塌');
      await page.locator('[data-photo-wall] [data-photo-cell]').nth(3).click();
      const count = page.locator('dialog[data-photo-lightbox] [data-photo-count]');
      await count.waitFor({ state: 'visible' });
      assert.equal((await count.innerText()).trim(), '4 / 5');
      await page.locator('dialog[data-photo-lightbox] [data-photo-close]').click();
      assert.deepEqual(unexpected(opened.problems), [], JSON.stringify(opened.problems));
    } finally {
      await opened.close();
    }
  });

  it('样张各段排在本地图片前面，翻页只数本地图片', async () => {
    const opened = await openPhotos(browser, 'media=photos', { first: 2, more: 1 });
    const { page } = opened;
    try {
      // 「载入更多」一进视口就自己翻下一页：把它滚进来，等两页都落定再读。
      await page.evaluate(() => document.querySelector('#index [data-entity-more]')?.scrollIntoView());
      await page.waitForFunction(() => document.querySelectorAll('[data-photo-wall] [data-photo-cell]').length === 8);
      assert.deepEqual(await groups(page), [['SSIS-057', 3], ['SSIS-001', 2], ['本地图片', 3]]);
      assert.equal(await page.locator('[data-local-wall] [data-photo-cell] img[src^="/photo-thumb"]').count(), 3);
      assert.deepEqual(opened.offsets, [2]);
      assert.deepEqual(unexpected(opened.problems), [], JSON.stringify(opened.problems));
    } finally {
      await opened.close();
    }
  });

  it('带番号集 id 的旧地址退回照片档全部内容', async () => {
    const opened = await openPhotos(browser, `media=photos&set=${encodeURIComponent('code:SSIS-057')}`);
    const { page } = opened;
    try {
      assert.equal(await page.locator('[data-photo-wall] [data-photo-cell]').count(), 5);
      assert.equal(await page.locator('[data-photo-back]').count(), 0);
      assert.deepEqual(unexpected(opened.problems), [], JSON.stringify(opened.problems));
    } finally {
      await opened.close();
    }
  });
});
