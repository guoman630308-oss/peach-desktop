/* 图片灯箱在真浏览器里走一遍：真 Swiper、真键鼠，从资料页照片墙和关注详情两处打开。
 *
 * 取图口全部由这里回桩图，`/photo` 原图走计费来源，用例绝不碰真的。大图是 4000×3000 的 SVG：
 * 缩放条的百分比相对原图像素，图得真有尺寸，「适应窗口」和 1:1 才分得开。关灯箱走关闭键或
 * 点背景，不按 Escape：Playwright 合成的 Escape 触发不了 dialog 的 CloseWatcher。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Locator, Page } from 'playwright-core';

import { launch, settle, visit, VIEWPORTS, type Visit } from './harness.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const NAME = '七沢みあ';
const ORIGINAL = { width: 4000, height: 3000 };
const svg = (width: number, height: number) => ({
  status: 200, contentType: 'image/svg+xml',
  body: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#3a4a5a"/></svg>`,
});

async function stubImages(page: Page): Promise<void> {
  await page.route(/\/(?:photo|sample-image|follow-stream)\?/, (route) => route.fulfill(svg(ORIGINAL.width, ORIGINAL.height)));
  await page.route(/\/(?:photo-thumb|sample-thumb)\?|\/follow-thumb-stub\//, (route) => route.fulfill(svg(160, 120)));
}

/** 资料页照片档：一部作品两张样张加 `locals` 张本地图（默认三张，墙上五格）。 */
async function openWall(browser: Browser, viewport = DESKTOP, locals = 3): Promise<Visit> {
  const opened = await visit(browser, '/', viewport);
  const { page } = opened;
  await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
    id: 90_001, kind: 'performer', canonical_name: NAME, aliases: [], display_aliases: [], user_aliases: [],
    asset_count: 0, tags: [], related_performers: [], links: [], metadata: {}, has_image: false, has_avatar: false,
    avatar_focus: null, representative_asset_id: null, entry_links: [], feed: { following: false },
  } }));
  await page.route(/\/api\/photos\?/, (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset') || 0);
    const items = offset ? [] : Array.from({ length: locals }, (_, at) => ({ id: 100 + at, name: `${100 + at}.jpg`, size: 2_345_678, location: 'local' }));
    return route.fulfill({ json: {
      kind: 'performer', name: NAME, entity_id: 90_001, total: items.length, items, seed: '', has_more: false,
      sample_total: 2, sets: [{ id: 'code:SSIS-057', kind: 'code', code: 'SSIS-057', name: '雨の日',
        title: 'SSIS-057 雨の日', n: 2, release_date: '2021-05-18', site: 'dmm', site_label: 'DMM', has_cover: true }],
    } });
  });
  await stubImages(page);
  await page.goto(new URL(`/performers/${encodeURIComponent(NAME)}?media=photos`, page.url()).href, { waitUntil: 'load' });
  await page.locator('[data-photo-cell] img').first().waitFor({ timeout: 15_000 });
  await settle(page);
  return opened;
}

/** 关注详情：一条三张图的帖子，详情大图点开就是这一组。 */
async function openFollowItem(browser: Browser): Promise<Visit> {
  const opened = await visit(browser, '/', DESKTOP);
  const { page } = opened;
  await stubImages(page);
  await page.route('**/follow-qualities**', (route) => route.fulfill({ json: {} }));
  await page.route('**/source-icon**', (route) => route.fulfill(svg(16, 16)));
  await page.route('**/api/follow/credentials', (route) => route.fulfill({ json: { providers: [] } }));
  await page.route('**/api/follow/check', (route) => (route.request().method() === 'GET'
    ? route.fulfill({ json: { status: 'idle' } }) : route.fallback()));
  const media = [0, 1, 2].map((index) => ({ index, media_kind: 'image', name: `帖子图 ${index + 1}.png`,
    size: 123_456, thumb_url: `/follow-thumb-stub/${index}`, width: ORIGINAL.width, height: ORIGINAL.height }));
  const item = { id: 501, provider: 'fanbox', provider_label: 'pixivFANBOX', resource_provider: 'fanbox', source_id: 1,
    source_label: '演示 · pixivFANBOX', external_id: '501', title: '演示图集', author: null, summary: null,
    url: 'https://example.com/501', thumb_url: '/follow-thumb-stub/0', published_at: '2026-09-16T00:20:00Z',
    published_precision: 'exact', version: null, duration: null, variant_kind: null, variant_label: null, status: 'new',
    asset_id: null, media_needs_credential: false, media_error: null, has_media: true, media_kind: 'image',
    width: ORIGINAL.width, height: ORIGINAL.height, playable: true, media_type: 'image/svg+xml', media_items: media,
    hidden_media: [], resource_urls: [], tags: [], detail_tags: [], tag_types: {} };
  await page.route((url) => url.pathname === '/api/follow', (route) => route.fulfill({ json: {
    groups: [{ release_key: 'r501', primary: item, variants: [], duplicates: [], providers: ['fanbox'], has_wip: false,
      is_release: false, newest_at: item.published_at, stack: null }],
    sources: [], counts: { new: 1, seen: 0, saved: 0, ignored: 0 }, facets: { authors: [], providers: ['fanbox'] },
    has_more: false } }));
  await page.goto(new URL('/posts/501', page.url()).href, { waitUntil: 'load' });
  await page.locator('[data-follow-detail-poster]').waitFor({ timeout: 15_000 });
  await settle(page);
  return opened;
}

const lightbox = (page: Page) => page.locator('dialog[data-photo-lightbox][open]');
const labelled = (box: Locator, label: string) => box.locator(`[aria-label="${label}"]`).first();

/** 缩略图条里当前那一格的序号，以及它的中线离条的中线多远。 */
const stripFocus = (page: Page) => page.evaluate(() => {
  const strip = document.querySelector('[data-photo-strip]')!;
  const slides = [...strip.querySelectorAll('.swiper-slide')];
  const active = strip.querySelector('.swiper-slide-thumb-active');
  const center = (el: Element) => { const box = el.getBoundingClientRect(); return box.left + box.width / 2 };
  return { at: active ? slides.indexOf(active) : -1, offset: active ? Math.abs(center(active) - center(strip)) : Infinity };
});

async function expectStripOn(page: Page, at: number): Promise<void> {
  await page.waitForFunction((want) => {
    const strip = document.querySelector('[data-photo-strip]')!;
    const active = strip.querySelector('.swiper-slide-thumb-active');
    if (!active || [...strip.querySelectorAll('.swiper-slide')].indexOf(active) !== want) return false;
    const a = active.getBoundingClientRect(), s = strip.getBoundingClientRect();
    return Math.abs(a.left + a.width / 2 - (s.left + s.width / 2)) <= 1;
  }, at, { timeout: 5_000 }).catch(async () => assert.fail(`缩略图条没有停到第 ${at + 1} 张的中线：${JSON.stringify(await stripFocus(page))}`));
}

/** 当前这张大图的显示宽度，与「适应窗口」时它对原图的百分比。 */
const shown = (box: Locator) => box.locator('[data-photo-main] .swiper-slide-active img').evaluate((img: HTMLImageElement) => ({
  width: img.getBoundingClientRect().width,
  fit: Math.round(Math.min(100, img.offsetWidth / img.naturalWidth * 100, img.offsetHeight / img.naturalHeight * 100)),
}));

describe('图片灯箱', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  it('资料页照片墙点第 3 格：从这一张开始，页码 3 / 5，翻页时页码与缩略图条一起跟上', { timeout: 60_000 }, async () => {
    const opened = await openWall(browser);
    const { page } = opened;
    try {
      const cell = page.locator('[data-photo-cell]').nth(2);
      const thumb = (await cell.locator('img').getAttribute('src'))!;
      await cell.click();
      const box = lightbox(page);
      await box.waitFor();
      assert.equal(await page.evaluate(() => document.body.classList.contains('photolight-open')), true);
      const count = box.locator('[data-photo-count]');
      assert.equal((await count.innerText()).trim(), '3 / 5');
      assert.equal(await count.getAttribute('aria-live'), 'polite');
      assert.equal(await box.locator('[data-photo-main] .swiper-slide-active img').getAttribute('src'),
        thumb.replace('/photo-thumb?', '/photo?').replace('/sample-thumb?', '/sample-image?'), '点开的不是那一张');
      await expectStripOn(page, 2);

      await box.locator('[data-photo-nav="fwd"]').click();
      await page.waitForFunction(() => document.querySelector('[data-photo-count]')?.textContent === '4 / 5');
      await expectStripOn(page, 3);
      await page.keyboard.press('ArrowLeft');
      await page.waitForFunction(() => document.querySelector('[data-photo-count]')?.textContent === '3 / 5');
      await expectStripOn(page, 2);
      await box.locator('[data-photo-strip] .swiper-slide').nth(4).click();
      await page.waitForFunction(() => document.querySelector('[data-photo-count]')?.textContent === '5 / 5');
      await expectStripOn(page, 4);

      await labelled(box, '关闭').click();
      await box.waitFor({ state: 'detached' });
      assert.equal(await page.evaluate(() => document.body.classList.contains('photolight-open')), false);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('缩放条：百分比相对原图像素，步进十个点，1:1 是原图大小，适应窗口回到起点', { timeout: 60_000 }, async () => {
    const opened = await openWall(browser);
    const { page } = opened;
    try {
      await page.locator('[data-photo-cell]').nth(2).click();
      const box = lightbox(page);
      await box.waitFor();
      const readout = box.locator('[data-photo-zoom-readout]');
      await page.waitForFunction(() => document.querySelector<HTMLImageElement>('[data-photo-main] .swiper-slide-active img')!
        .naturalWidth > 0, undefined, { timeout: 10_000 });
      const start = await shown(box);
      const { fit } = start;
      assert.ok(fit < 100, `4000×3000 的图在 1280×800 里应当缩着放，读到 ${fit}%`);
      await page.waitForFunction((want) => document.querySelector('[data-photo-zoom-readout]')?.textContent === want, `${fit}%`);
      await labelled(box, '放大').click();
      assert.equal(await readout.innerText(), `${fit + 10}%`);
      await labelled(box, '缩小').click();
      assert.equal(await readout.innerText(), `${fit}%`);
      await labelled(box, '原大小').click();
      assert.equal(await readout.innerText(), '100%');
      await page.waitForFunction((want) => Math.abs(document.querySelector('[data-photo-main] .swiper-slide-active img')!
        .getBoundingClientRect().width - want) <= want * .01, ORIGINAL.width, { timeout: 5_000 });
      await box.locator('[data-photo-zoom] input[type=range]').evaluate((input: HTMLInputElement) => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '200');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      assert.equal(await readout.innerText(), '200%');
      await labelled(box, '适应窗口').click();
      assert.equal(await readout.innerText(), `${fit}%`);
      await page.waitForFunction((want) => Math.abs(document.querySelector('[data-photo-main] .swiper-slide-active img')!
        .getBoundingClientRect().width - want) <= 1, start.width, { timeout: 5_000 })
        .catch(async () => assert.fail(`适应窗口没有收回：${start.width} → ${(await shown(box)).width}`));
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('圆圈 i 开合信息面板，点面板外只收面板；点图四周的黑底关掉灯箱', { timeout: 60_000 }, async () => {
    const opened = await openWall(browser);
    const { page } = opened;
    try {
      await page.locator('[data-photo-cell]').nth(4).click();
      const box = lightbox(page);
      await box.waitFor();
      const toggle = labelled(box, '图片详情');
      const panel = box.locator('[data-photo-detail]');
      await toggle.click();
      await panel.waitFor({ state: 'visible' });
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      assert.equal((await panel.locator('h2').innerText()).trim(), '102.jpg');
      assert.match((await panel.locator('[data-photo-detail-meta]').innerText()).trim(), /^本地 · 2(?:\.\d)? MB$/);
      assert.equal(await panel.locator('[data-photo-reveal]').isVisible(), true, '本地图要有定位键');
      await box.locator('[data-photo-zoom-readout]').click();
      await panel.waitFor({ state: 'hidden' });
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
      assert.equal(await box.isVisible(), true, '收面板那一下不该连灯箱一起关');

      // 主画布铺满视口，图上方那条留白属于 zoom 容器。
      await page.mouse.click(DESKTOP.width / 2, 10);
      await box.waitFor({ state: 'detached' });
      assert.equal(await page.locator('[data-photo-main]').count(), 0);
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('关注详情的大图打开同一个灯箱：整组三张，在线图写来源与分辨率，没有定位键', { timeout: 60_000 }, async () => {
    const opened = await openFollowItem(browser);
    const { page } = opened;
    try {
      await page.locator('[data-follow-detail-poster]').click();
      const box = lightbox(page);
      await box.waitFor();
      assert.equal((await box.locator('[data-photo-count]').innerText()).trim(), '1 / 3');
      assert.equal(await box.locator('[data-photo-main] .swiper-slide-active img').getAttribute('src'),
        '/follow-stream?id=501&media=0');
      await labelled(box, '图片详情').click();
      const panel = box.locator('[data-photo-detail]');
      await panel.waitFor({ state: 'visible' });
      assert.equal((await panel.locator('h2').innerText()).trim(), '帖子图 1.png');
      const meta = await panel.locator('[data-photo-detail-meta]').innerText();
      assert.match(meta, /^pixivFANBOX · 4000 × 3000 · 121\sKB$/u);
      assert.doesNotMatch(meta, /第/, '第几张只看底栏的页码，面板不另编一个号');
      assert.equal(await panel.locator('[data-photo-reveal]').isVisible(), false, '在线图不该有定位键');
      await labelled(box, '关闭').click();
      await box.waitFor({ state: 'detached' });
      assert.deepEqual(opened.problems, []);
    } finally {
      await opened.close();
    }
  });

  it('320 宽翻到上千张的最后一张：页码一行写完，底栏整条留在屏内', { timeout: 90_000 }, async () => {
    const opened = await openWall(browser, { name: '320', width: 320, height: 720, mobile: true }, 1287);
    const { page } = opened;
    try {
      await page.locator('[data-photo-cell]').first().click();
      const box = lightbox(page);
      await box.waitFor();
      await page.evaluate(() => {
        const main = document.querySelector('[data-photo-main]') as HTMLElement & { swiper: { slideTo(at: number, speed: number): void } };
        main.swiper.slideTo(1288, 0);
      });
      const count = box.locator('[data-photo-count]');
      await page.waitForFunction(() => document.querySelector('[data-photo-count]')?.textContent === '1289 / 1289');
      const bar = await box.locator('[data-photo-bar]').evaluate((node) => ({
        line: parseFloat(getComputedStyle(node.querySelector('[data-photo-count]')!).lineHeight) || 16,
        count: node.querySelector('[data-photo-count]')!.getBoundingClientRect().height,
        right: Math.max(...[...node.querySelectorAll('button, [data-photo-count]')].map((el) => el.getBoundingClientRect().right)),
        view: document.documentElement.clientWidth,
      }));
      assert.ok(bar.count < bar.line * 2 + 16, `页码折行了：${JSON.stringify(bar)}`);
      assert.ok(bar.right <= bar.view, `底栏越出视口：${JSON.stringify(bar)}`);
      assert.equal(await count.isVisible(), true);
    } finally {
      await opened.close();
    }
  });
});
