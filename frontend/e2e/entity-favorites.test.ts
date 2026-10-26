import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

describe('资料页收藏与别名布局', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser.close() });
  for (const viewport of VIEWPORTS) {
    it(`${viewport.name} 收藏紧邻别名下拉，同尺寸，回执后显示黄色实心星星`, { timeout: 60_000 }, async () => {
      const opened = await visit(browser, '/', viewport);
      const { page } = opened;
      let selected = false;
      await page.route(/\/api\/entity\?/, route => route.fulfill({ json: {
        id: 90701, kind: 'studio', canonical_name: 'Prestige', aliases: ['プレステージ'],
        display_aliases: ['プレステージ'], asset_count: 330, labels: [],
        tags: [], related_performers: [], links: [], has_image: false, has_logo: false, favorite: false,
      } }));
      await page.route(url => url.pathname === '/api/items', route => route.fulfill({ json: { items: [], total: 0, has_more: false } }));
      await page.route(url => url.pathname === '/api/photos', route => route.fulfill({ json: { items: [], sets: [], total: 0, sample_total: 0, has_more: false } }));
      await page.route(url => url.pathname === '/api/library-shortcuts', async route => {
        if (route.request().method() === 'POST') selected = route.request().postDataJSON().on;
        await route.fulfill({ json: { favorites: selected ? [{ entity_id: 90701, key: 'entity:90701', kind: 'studio', name: 'Prestige' }] : [], filters: [] } });
      });
      try {
        await page.goto(new URL('/studios/Prestige', page.url()).href);
        const favorite = page.getByRole('button', { name: '收藏Prestige', exact: true });
        await favorite.waitFor();
        await settle(page);
        const geometry = await page.evaluate(() => {
          const name = document.querySelector('[data-namepick-toggle]')!;
          const star = document.querySelector('[aria-label="收藏Prestige"]')!;
          const a = name.getBoundingClientRect(), b = star.getBoundingClientRect();
          const alias = document.querySelector('[data-meta-item="names"]')!;
          const count = document.querySelector('[data-meta-item][title="视频"]')!;
          return { title: !!star.closest('[data-entity-title]'), size: [a.width, a.height, b.width, b.height],
            gap: b.left - a.right, row: Math.abs(a.top - b.top), text: star.textContent?.trim(),
            aliasesTogether: Math.abs(alias.getBoundingClientRect().top - count.getBoundingClientRect().top) < 2 };
        });
        assert.equal(geometry.title, true);
        assert.deepEqual(geometry.size, [32, 32, 32, 32]);
        assert.equal(geometry.row, 0);
        assert.equal(geometry.gap, 8);
        assert.equal(geometry.text, '');
        if (!viewport.mobile) assert.equal(geometry.aliasesTogether, true);
        await favorite.click();
        const active = page.getByRole('button', { name: '取消收藏Prestige', exact: true });
        await active.waitFor();
        assert.equal(await active.getAttribute('aria-pressed'), 'true');
        const paint = await active.locator('svg').evaluate(node => {
          const style = getComputedStyle(node);
          const probe = document.createElement('span');
          probe.style.color = 'var(--color-yellow-500)'; node.append(probe);
          const yellow = getComputedStyle(probe).color; probe.remove();
          return { fill: style.fill, color: style.color, yellow };
        });
        assert.equal(paint.fill, paint.color);
        assert.equal(paint.color, paint.yellow);
        await page.screenshot({ path: resolve('../build', `favorites-${viewport.name}.png`) });
        await active.click();
        await favorite.waitFor();
        assert.equal(await favorite.getAttribute('aria-pressed'), 'false');
        const size = await layout(page);
        assert(size.scrollWidth <= size.viewportWidth + 1, JSON.stringify(size));
        assert.deepEqual(size.offenders, []);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
