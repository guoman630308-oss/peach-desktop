/* 创作者在线视图：纯图片作者、页内筛选和详情返回，在两个视口下守住同一组行为。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { FIRST, openFollowFeed } from './follow-fixture.ts';
import { launch, layout, settle, VIEWPORTS } from './harness.ts';

describe('创作者在线视图', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 纯图片作者显示在线图片，详情返回保留筛选与滚动`, { timeout: 60_000 }, async () => {
      const opened = await openFollowFeed(browser, '/follow?media=images', viewport);
      const page = opened.page;
      try {
        const groups = FIRST.filter((group) => group.primary.media_kind === 'image');
        await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
          id: 8892, kind: 'creator', canonical_name: 'Online Author', aliases: [], display_aliases: [],
          user_aliases: [], asset_count: 0, tags: [], links: [], metadata: {},
          follow: { key: 'name:kou', n: groups.length, providers: ['Rule34.xxx'], avatar: '', avatar_fallback: '', held: [] },
        } }));
        await page.route(/\/api\/photos\?/, (route) => route.fulfill({ json: { items: [], total: 0, sets: [] } }));
        await page.route(/\/api\/items\?/, (route) => route.fulfill({ json: { items: [], total: 0 } }));
        await page.route((url) => url.pathname === '/api/follow' && !url.searchParams.has('item'), (route) =>
          route.fulfill({ json: { groups, sources: [], author_aliases: [], counts: { new: groups.length },
            author_facets: { providers: ['rule34xxx'], tags: [] }, offset: 0, has_more: false } }));
        await page.goto(new URL('/creators/Online%20Author', page.url()).href, { waitUntil: 'load' });
        await page.locator('[data-entity-online] [data-follow-item]').first().waitFor();
        await settle(page);
        assert.equal(await page.locator('[data-follow-wall]').count(), 1);
        assert.equal(await page.locator('[data-entity-online] [data-follow-item]').count(), groups.length);
        await page.locator('[data-entity-state="new"]').click();
        await settle(page);
        await page.evaluate(() => window.scrollTo(0, 150));
        const scroll = await page.evaluate(() => window.scrollY);
        await page.locator('[data-entity-online] [data-follow-open]').first().click();
        await page.locator('#stage[open] [data-follow-detail-side]').waitFor();
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => location.pathname.startsWith('/creators/'));
        await settle(page);
        assert.equal(await page.locator('[data-entity-state="new"]').getAttribute('aria-pressed'), 'true');
        assert.equal(await page.evaluate(() => window.scrollY), scroll);
        const bounds = await layout(page);
        assert.equal(bounds.scrollWidth, bounds.viewportWidth);
        assert.deepEqual(bounds.offenders, []);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
