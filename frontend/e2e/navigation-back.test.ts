import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { after, before, describe, it } from 'node:test';
import type { Browser, Page } from 'playwright-core';
import { launch, layout, settle, visit, VIEWPORTS } from './harness.ts';

const LIST = '/performers?category=japanese_av&q=%E8%BF%94%E5%9B%9E';
const people = Array.from({ length: 240 }, (_, at) => ({
  k: `返回测试人物${String(at + 1).padStart(3, '0')}`, n: 1, entity_id: 90_000 + at,
}));

async function fixtures(page: Page) {
  await page.route('**/api/index?**', route => {
    const params = new URL(route.request().url()).searchParams;
    const offset = Number(params.get('offset'));
    const limit = Number(params.get('limit'));
    return route.fulfill({ json: { items: people.slice(offset, offset + limit),
      categories: { japanese_av: people.length }, has_more: offset + limit < people.length } });
  });
  await page.route('**/api/entity?**', route => {
    const params = new URL(route.request().url()).searchParams;
    const person = people.find(row => String(row.entity_id) === params.get('id') || row.k === params.get('name'))!;
    return route.fulfill({ json: { id: person.entity_id, canonical_name: person.k, kind: 'performer',
      aliases: [], display_aliases: [], user_aliases: [], asset_count: 0, tags: [], related_performers: [],
      links: [], metadata: {}, has_image: false, has_avatar: false, entry_links: [], feed: null } });
  });
  await page.route('**/api/items?**', route => route.fulfill({ json: { items: [], total: 0, has_more: false } }));
  await page.route('**/api/photos?**', route => route.fulfill({ json: { items: [], sets: [], total: 0, has_more: false } }));
  await page.route('**/api/feeds/discoveries?**', route => route.fulfill({ json: { items: [], more: false, ok: true } }));
}

describe('侧栏返回与名册位置', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });
  for (const viewport of VIEWPORTS) {
    it(`返回保留已加载的第二页、筛选与滚动位置（${viewport.name}）`, { timeout: 90_000 }, async () => {
      const opened = await visit(browser, LIST, viewport, fixtures);
      try {
        const { page } = opened;
        await page.locator('[data-index-cell]').first().waitFor();
        assert.equal(await page.locator('#navigationBack').isDisabled(), true);
        if (!viewport.mobile && !await page.locator('#drawer').evaluate(node => node.classList.contains('open')))
          await page.locator('#filterBtn').click();
        await page.getByRole('button', { name: '载入更多', exact: true }).click();
        await page.waitForFunction(() => document.querySelectorAll('[data-index-cell]').length === 240);
        const address = new URL(page.url()).pathname + new URL(page.url()).search;
        for (const at of [130, 150]) {
          const cell = page.locator('[data-index-cell]').nth(at);
          await cell.scrollIntoViewIfNeeded();
          await settle(page);
          const before = await page.evaluate(() => window.scrollY);
          assert.ok(before > viewport.height);
          await cell.click();
          await page.locator('[data-entity-hero] h2').waitFor();
          await settle(page);
          if (viewport.mobile) await page.locator('#filterBtn').click();
          const back = page.getByRole('button', { name: '返回上一页', exact: true });
          await back.waitFor();
          assert.equal(await back.locator('svg').evaluate(node => (node as SVGGraphicsElement).getBBox().width > 0), true);
          assert.equal(await back.locator('svg').evaluate(node => getComputedStyle(node).fill), 'none');
          if (viewport.mobile) await back.click(); else await back.press('Enter');
          await page.waitForURL(url => url.pathname + url.search === address);
          await page.waitForFunction(() => document.querySelectorAll('[data-index-cell]').length === 240);
          await page.waitForFunction(y => Math.abs(window.scrollY - y) < 2, before);
          if (!viewport.mobile) assert.equal(await page.locator('#drawer').evaluate(node => node.classList.contains('open')), true);
          assert.equal(await page.getByRole('button', { name: '女优', exact: true }).getAttribute('aria-pressed'), 'true');
          const geometry = await layout(page);
          assert.ok(geometry.scrollWidth <= geometry.viewportWidth, JSON.stringify(geometry));
          assert.deepEqual(geometry.offenders, []);
        }
        const retainedY = await page.evaluate(() => window.scrollY);
        await page.goForward();
        await page.locator('[data-entity-hero] h2').waitFor();
        if (viewport.mobile) await page.locator('#filterBtn').click();
        await page.getByRole('button', { name: '返回上一页', exact: true }).click();
        await page.waitForURL(url => url.pathname + url.search === address);
        await page.waitForFunction(() => document.querySelectorAll('[data-index-cell]').length === 240);
        await page.waitForFunction(y => Math.abs(window.scrollY - y) < 2, retainedY);
        await mkdir('../build/agent-verification/back-navigation', { recursive: true });
        if (viewport.mobile) await page.locator('#filterBtn').click();
        await page.screenshot({ path: `../build/agent-verification/back-navigation/${viewport.name}.png` });
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
