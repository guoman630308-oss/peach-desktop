import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, requiredEnv, VIEWPORTS } from './harness.ts';

describe('宽封套与来源封面', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}: Blu-ray 正封完整可见，剧照保持横图取景`, async () => {
      const context = await browser.newContext({ viewport });
      try {
        await context.addInitScript(() => localStorage.setItem('peach.settings.v1', JSON.stringify({ detailAutoplay: false, feedAutoScroll: false })));
        const page = await context.newPage();
        const remote = 'https://covers.example.test/wide.svg';
        const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="438"><rect width="428" height="438" fill="#777"/><rect x="428" width="372" height="438" fill="#ccc"/></svg>';
        await page.route(remote, route => route.fulfill({ contentType: 'image/svg+xml', body: svg }));
        await page.route('**/jav-cover?code=MIDA-085', route => route.fulfill({ contentType: 'image/svg+xml', body: svg }));
        await page.route('**/cover?code=EXM-006*', route => route.fulfill({ contentType: 'image/svg+xml', body: svg }));
        await page.route('**/cover?code=MDVR-276*', route => route.fulfill({ contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"><rect width="800" height="600" fill="#ccc"/></svg>' }));
        await page.route('**/api/feeds/discoveries?*', route => route.fulfill({ json: { ok: true, items: [
          ...['MIDA-122', 'MIDA-239', 'MIDA-198'].map((code, id) => ({ id: id + 1, code, title: '作品（ブルーレイディスク）', cover_url: remote })),
          { id: 4, code: 'EXM-004', title: '横版剧照', cover_url: remote },
          { id: 5, code: 'MIDA-085', title: '作品（ブルーレイディスク）', cover_url: '/jav-cover?code=MIDA-085' },
          { id: 6, code: 'EXM-006', title: '作品', has_cover: true,
            poster_box: { x0: 428, y0: 0, x1: 800, y1: 438, px: [800, 438] } },
          { id: 7, code: 'MDVR-276', title: '【VR】作品', has_cover: true, cover_frame: { cx: .22, cy: .2, layout: 'still' } },
        ] } }));
        await page.goto(requiredEnv('PEACH_E2E_ORIGIN') + '/');
        for (const id of [1, 2, 3, 5, 6]) {
          const image = page.locator(`#feedNew [data-feed-id="${id}"] img.cover`);
          await image.scrollIntoViewIfNeeded();
          await image.evaluate((img: HTMLImageElement) => img.decode());
          const frame = await image.evaluate((img: HTMLImageElement) => {
            const css = getComputedStyle(img), pic = img.closest('.pic')!.getBoundingClientRect(), rect = img.getBoundingClientRect();
            const [top, right, bottom, left] = img.style.getPropertyValue('--panel-clip').split(' ').map(parseFloat);
            return { kind: img.dataset.frame, panel: img.classList.contains('panel'),
              x0: rect.x + rect.width * left / 100, x1: rect.right - rect.width * right / 100,
              y0: rect.y + rect.height * top / 100, y1: rect.bottom - rect.height * bottom / 100,
              pic: { x: pic.x, right: pic.right, y: pic.y, bottom: pic.bottom }, fit: css.objectFit };
          });
          assert.equal(frame.kind, id === 6 ? 'still' : 'sleeve');
          assert.equal(frame.panel, true);
          assert.ok(frame.x0 >= frame.pic.x - 1 && frame.x1 <= frame.pic.right + 1, JSON.stringify(frame));
          assert.ok(frame.y0 >= frame.pic.y - 1 && frame.y1 <= frame.pic.bottom + 1, JSON.stringify(frame));
        }
        const still = page.locator('#feedNew [data-feed-id="4"] img.cover');
        await still.scrollIntoViewIfNeeded();
        await still.evaluate((img: HTMLImageElement) => img.decode());
        assert.equal(await still.getAttribute('data-frame'), 'still');
        assert.equal(await still.evaluate(img => img.classList.contains('panel')), false);
        const vr = page.locator('#feedNew [data-feed-id="7"] img.cover');
        await vr.scrollIntoViewIfNeeded();
        await vr.evaluate((img: HTMLImageElement) => img.decode());
        assert.equal(await vr.getAttribute('data-frame'), 'still');
        assert.equal(await vr.evaluate(img => img.classList.contains('panel')), false);
        assert.equal(await vr.evaluate(img => img.style.getPropertyValue('--cover-x')), '0%');
      } finally { await context.close() }
    });
  }
});
