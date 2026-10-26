import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';
import { launch, settle, visit, VIEWPORTS } from './harness.ts';
import { ITEM, stubItemPage } from './item-fixture.ts';

const LIST = '/performers?category=japanese_av&q=%E8%BF%94%E5%9B%9E';
const people = Array.from({ length: 240 }, (_, at) => ({
  k: `返回测试人物${String(at + 1).padStart(3, '0')}`, n: 1, entity_id: 90_000 + at,
}));

describe('退出播放与返回历史', () => {
  let browser: Browser;
  before(async () => { browser = await launch() });
  after(async () => { await browser?.close() });
  for (const viewport of VIEWPORTS) {
    it(`关闭视频并返回名册保持位置，历史重开由按钮播放（${viewport.name}）`, { timeout: 100_000 }, async () => {
      const plays: unknown[] = [];
      const opened = await visit(browser, LIST, viewport, async page => {
        await stubItemPage(page, [ITEM.plain, ITEM.part]);
        await page.route('**/api/index?**', route => {
          const query = new URL(route.request().url()).searchParams;
          const offset = Number(query.get('offset')), limit = Number(query.get('limit'));
          return route.fulfill({ json: { items: people.slice(offset, offset + limit),
            categories: { japanese_av: people.length }, has_more: offset + limit < people.length } });
        });
        await page.route('**/api/entity?**', route => {
          const id = Number(new URL(route.request().url()).searchParams.get('id'));
          const person = people.find(row => row.entity_id === id) || people[0]!;
          return route.fulfill({ json: { id: person.entity_id, canonical_name: person.k, kind: 'performer',
            aliases: [], display_aliases: [], user_aliases: [], asset_count: 1, tags: [], related_performers: [],
            links: [], metadata: {}, has_image: false, has_avatar: false, entry_links: [], feed: null } });
        });
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
        await page.locator('[data-index-cell]').first().waitFor();
        await page.getByRole('button', { name: '载入更多', exact: true }).click();
        await page.waitForFunction(() => document.querySelectorAll('[data-index-cell]').length === 240);
        const listPath = new URL(page.url()).pathname + new URL(page.url()).search;
        const cell = page.locator('[data-index-cell]').nth(130);
        await cell.scrollIntoViewIfNeeded();
        await settle(page);
        const listY = await page.evaluate(() => window.scrollY);
        assert.ok(listY > viewport.height);
        await cell.click();
        await page.locator('[data-entity-hero] h2').waitFor();
        await settle(page);
        const personPath = new URL(page.url()).pathname + new URL(page.url()).search;
        const card = `[data-entity-body] [data-media-card][data-id="${ITEM.plain}"] [data-media-title]`;
        await page.locator(card).first().click();
        await page.waitForFunction(() => document.querySelector('[data-local-player-controls] [role="status"]')?.textContent?.includes('已打开'));
        assert.equal(plays.length, 1);
        await page.locator('#closeStage').click();
        await page.waitForURL(url => url.pathname + url.search === personPath);
        await settle(page);
        if (viewport.mobile || !await page.locator('#drawer').evaluate(node => node.classList.contains('open')))
          await page.locator('#filterBtn').click();
        await page.getByRole('button', { name: '返回上一页', exact: true }).click();
        await page.waitForURL(url => url.pathname + url.search === listPath);
        await page.waitForFunction(() => document.querySelectorAll('[data-index-cell]').length === 240);
        await page.waitForFunction(y => Math.abs(window.scrollY - y) < 2, listY);
        assert.equal(plays.length, 1);

        await page.goForward();
        await page.waitForURL(url => url.pathname + url.search === personPath);
        await settle(page);
        await page.goForward();
        await page.locator('[data-local-player-controls]').waitFor();
        await settle(page);
        await page.evaluate(async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) });
        assert.equal(plays.length, 1, '历史恢复详情不自动唤起外部播放器');
        await page.getByRole('button', { name: 'PotPlayer 播放', exact: true }).click();
        await page.waitForFunction(() => document.querySelector('[data-local-player-controls] [role="status"]')?.textContent?.includes('已打开'));
        assert.equal(plays.length, 2);
        await page.locator('#closeStage').click();
        await page.waitForURL(url => url.pathname + url.search === personPath);
        await page.locator(card).first().click();
        await page.waitForFunction(() => document.querySelector('[data-local-player-controls] [role="status"]')?.textContent?.includes('已打开'));
        assert.equal(plays.length, 3, '主动点击视频仍按默认方式播放');
        await page.locator('#closeStage').click();
        await page.waitForURL(url => url.pathname + url.search === personPath);
        await page.locator(`[data-entity-body] [data-media-card][data-id="${ITEM.part}"] [data-media-title]`).first().click();
        await page.waitForFunction(() => document.querySelector('[data-local-player-controls] [role="status"]')?.textContent?.includes('已打开'));
        assert.equal(plays.length, 4);
        await page.locator('#stage [data-queue-item="22"]').click();
        await page.waitForURL(url => url.pathname === '/assets/22');
        await page.waitForFunction(() => document.querySelector('[data-local-player-controls] [role="status"]')?.textContent?.includes('已打开'));
        assert.equal(plays.length, 5, '主动换卷仍按默认方式播放');
        await page.locator('#closeStage').click();
        await page.waitForURL(url => url.pathname + url.search === personPath);
        await page.goForward();
        await page.waitForURL(url => url.pathname === `/assets/${ITEM.part}`);
        await page.locator('[data-local-player-controls]').waitFor();
        await settle(page);
        assert.equal(plays.length, 5, '历史恢复分卷不自动唤起外部播放器');
        await page.locator('#stage [data-queue-item="22"]').click();
        await page.waitForURL(url => url.pathname === '/assets/22');
        await page.waitForFunction(() => document.querySelector('[data-local-player-controls] [role="status"]')?.textContent?.includes('已打开'));
        assert.equal(plays.length, 6, '历史恢复后主动换卷可正常播放');
        await page.locator('#closeStage').click();
        await page.waitForURL(url => url.pathname + url.search === personPath);
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close() }
    });
  }
});
