import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, settle, visit, VIEWPORTS } from './harness.ts';
import { JAV_DETAIL, mockJavDetail } from './jav-detail-fixture.ts';

describe('图片导航固定位置与悬停', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) it(`${viewport.name}：横竖图箭头固定在媒体区边缘`, { timeout: 60000 }, async () => {
    const opened = await visit(browser, '/', viewport);
    const { page } = opened;
    try {
      await settle(page);
      await mockJavDetail(page);
      await page.route('**/api/jav/detail?*', route => route.fulfill({ json: { ...JAV_DETAIL,
        performer_refs: [], studio_refs: [], series_refs: [], preview_video_url: '', preview_count: 1 } }));
      await page.route('**/api/jav/image?*', route => {
        const vertical = new URL(route.request().url()).searchParams.get('image') === '0';
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${vertical ? 400 : 1200}" height="800"><rect width="100%" height="100%" fill="#456"/></svg>`;
        return route.fulfill({ json: { ok: true, image: 'data:image/svg+xml,' + encodeURIComponent(svg) } });
      });
      await page.goto(new URL('/works/EXM-001', page.url()).href);
      const frame = page.locator('[data-jav-art]');
      const prev = frame.locator('[data-follow-image-arrow="prev"]');
      const next = frame.locator('[data-follow-image-arrow="next"]');
      await frame.locator('[data-jav-picture]').waitFor();
      await settle(page, '#stage');
      const first = await prev.boundingBox(), last = await next.boundingBox(), area = await frame.boundingBox();
      assert.ok(first && last && area);
      assert.ok(Math.abs(first.x - area.x - 11) < 1);
      assert.ok(Math.abs(area.x + area.width - last.x - last.width - 11) < 1);
      await next.click();
      await page.waitForFunction(() => document.querySelector<HTMLImageElement>('[data-jav-picture]')?.naturalWidth === 400);
      assert.equal((await prev.boundingBox())!.x, first.x);
      assert.equal((await next.boundingBox())!.x, last.x);
      if (!viewport.mobile) {
        const style = (node: Element) => ({ base: getComputedStyle(node).backgroundColor,
          hover: getComputedStyle(node, '::after').backgroundColor, inset: getComputedStyle(node, '::after').left });
        await next.hover();
        await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-follow-image-arrow="next"]')!, '::after').backgroundColor === 'rgba(255, 255, 255, 0.2)');
        const arrow = await next.evaluate(style);
        const close = frame.locator('[data-stage-close]');
        await close.hover();
        await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-stage-close]')!, '::after').backgroundColor === 'rgba(255, 255, 255, 0.2)');
        assert.deepEqual(arrow, await close.evaluate(style));
      }
      await page.screenshot({ path: resolve('../build', `navigation-${viewport.name}.png`) });
      assert.deepEqual(opened.problems, []);
    } finally { await opened.close() }
  });
});
