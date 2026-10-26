import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, visit, VIEWPORTS } from './harness.ts';
import { ITEM, stubItemPage } from './item-fixture.ts';

describe('本机外部播放器', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });

  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 默认外部播放与手动内置播放`, { timeout: 90_000 }, async () => {
      const card = `#grid [data-media-card][data-id="${ITEM.plain}"] [data-media-title]`;
      const plays: Record<string, unknown>[] = [], requests: string[] = [];
      const opened = await visit(browser, '/', viewport, async page => {
        await stubItemPage(page);
        await page.route('**/api/tops?*', route => route.fulfill({ json: { performers: [], studios: [] } }));
        page.on('request', request => { requests.push(new URL(request.url()).pathname) });
        await page.route('**/api/configuration/local-player', route => route.fulfill({ json: {
          available: true, mode: 'external', executable: 'C:\\Players\\PotPlayerMini64.exe', label: 'PotPlayer',
        } }));
        await page.route('**/api/local-player/play', route => {
          plays.push(route.request().postDataJSON());
          return route.fulfill({ json: { opened: true, player: 'PotPlayer' } });
        });
      });
      const { page } = opened;
      try {
        await page.locator(card).first().waitFor();
        requests.length = 0;
        await page.locator(card).first().hover();
        await page.waitForTimeout(700);
        assert.equal(await page.locator('video.hv').count(), 0);
        assert.equal(requests.some(path => ['/stream', '/api/stream-plan'].includes(path)), false);
        await page.locator(card).first().click();
        await page.locator('#stage [data-local-player-controls]').waitFor();
        await page.waitForFunction(() => document.querySelector('[data-local-player-controls] [role="status"]')?.textContent?.includes('已打开'));
        assert.deepEqual(plays, [{ kind: 'item', id: ITEM.plain, media: null }]);
        assert.equal(await page.locator('#stage video').count(), 0);
        assert.equal(requests.some(path => ['/stream', '/api/stream-plan', '/api/play', '/api/activity'].includes(path)), false);
        const geometry = await page.locator('[data-local-player-controls]').evaluate(node => {
          const box = node.getBoundingClientRect();
          return { left: box.left, right: box.right, viewport: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth };
        });
        assert.ok(geometry.left >= 0 && geometry.right <= geometry.viewport);
        assert.equal(geometry.overflow, false);
        await page.getByRole('button', { name: '内置播放', exact: true }).click();
        await page.locator('#stage video').first().waitFor({ state: 'attached' });
        assert.equal(plays.length, 1);
        assert.deepEqual(opened.problems.filter(line => !line.includes('VIDEOJS')), []);
      } finally { await opened.close() }
    });
  }
});
